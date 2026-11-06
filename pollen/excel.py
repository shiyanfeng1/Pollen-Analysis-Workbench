"""Two-level Excel/CSV interchange. Counts are never inferred from metadata."""
import csv
import os
import tempfile
import re
from pathlib import Path
from datetime import datetime
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from .core import Project, Taxon, Sample, GROUPS, calculate, percentage, input_label
from . import __version__


def sheets(path):
    wb = load_workbook(path, read_only=True)
    try:
        return wb.sheetnames
    finally:
        wb.close()


def excel_uses_percent(format_code):
    # A quoted/escaped percent character is literal and does not scale a number.
    return '%' in re.sub(r'"[^\"]*"|\\.|_.|\*.','',format_code or '')


def normalize_percentage(value, unit='percent', formatted=False):
    if value is None or str(value).strip()=='':
        return ''
    explicit = isinstance(value,str) and value.strip().endswith(('%','％'))
    n = percentage(value)
    if not explicit and (formatted or unit=='fraction'):
        n *= 100
    return str(n)


def _read_rows(path,sheet=None):
    meta,percent_cells,actual_sheet = {},set(),sheet
    if Path(path).suffix.lower() == '.csv':
        try:
            with open(path, encoding='utf-8-sig', newline='') as f:
                rows = list(csv.reader(f))
        except UnicodeDecodeError:
            with open(path, encoding='gb18030', newline='') as f:
                rows = list(csv.reader(f))
        settings = []
    else:
        wb = load_workbook(path, data_only=False)
        try:
            ws = wb[sheet] if sheet else wb.worksheets[0]
            actual_sheet = ws.title
            for r,row in enumerate(ws):
                if any(c.data_type == 'f' for c in row):
                    raise ValueError('导入表包含公式，请先在 Excel 中复制并粘贴为数值')
                if r>=2:
                    percent_cells.update((r,c) for c,cell in enumerate(row) if c>0 and cell.value is not None and excel_uses_percent(cell.number_format))
            rows = list(ws.values)
            settings = list(wb['类群设置'].values) if '类群设置' in wb.sheetnames else []
            meta = dict(list(wb['计算说明'].values)[1:]) if '计算说明' in wb.sheetnames else {}
        finally:
            wb.close()
    return rows,settings,meta,percent_cells,actual_sheet


def _input_hint(meta,percent_cells,sheet):
    if sheet in ('百分比结果','导入百分比','原始百分比'):
        return 'percentages'
    if meta.get('数据类型') in ('已有百分比','percentages'):
        return 'percentages'
    if meta.get('数据类型') in ('原始计数','counts'):
        return 'counts'
    return 'percentages' if percent_cells else 'counts'


def inspect_table(path,sheet=None):
    rows,_,meta,formats,actual_sheet = _read_rows(path,sheet)
    preview = []
    if len(rows)>1:
        for r,row in enumerate(rows[2:5],2):
            for c,value in enumerate(row[1:5],1):
                if value is not None and str(value).strip():
                    name = rows[1][c] if c<len(rows[1]) else f'第{c+1}列'
                    preview.append((str(name),value,(r,c) in formats))
    return {'data_type':_input_hint(meta,formats,actual_sheet),
            'unit':'fraction' if str(meta.get('数值存储单位','')).startswith('比例') else 'percent',
            'preview':preview[:8], 'percentage_cells':len(formats)}


def read_table(path, sheet=None, *, data_type=None, percentage_unit=None):
    rows,settings,meta,percent_cells,actual_sheet = _read_rows(path,sheet)
    mode = data_type or _input_hint(meta,percent_cells,actual_sheet)
    unit = percentage_unit or ('fraction' if str(meta.get('数值存储单位','')).startswith('比例') else 'percent')
    if mode not in ('counts','percentages') or unit not in ('percent','fraction'):
        raise ValueError('导入数据类型或百分比数值单位无效')
    if len(rows) < 2 or str(rows[0][0]).strip() != '深度/年代':
        raise ValueError('需要两层表头，左上角为“深度/年代”，请参考导入模板')
    p, group = Project(data_type=mode,source_info={'file':Path(path).name,'sheet':actual_sheet or '',
        'numeric_unit':unit if mode=='percentages' else 'counts',
        'excel_percentage_cells':len(percent_cells),'stored_unit':'percent' if mode=='percentages' else 'counts'}), None
    columns = []
    for col in range(1, max(len(rows[0]), len(rows[1]))):
        top = rows[0][col] if col < len(rows[0]) else None
        name = rows[1][col] if col < len(rows[1]) else None
        if top is not None and str(top).strip():
            group = str(top).strip()
        if name is None or not str(name).strip():
            if any(col < len(r) and r[col] not in (None, '') for r in rows[2:]):
                raise ValueError(f'第 {col + 1} 列有数据但缺少类群名称')
            continue
        if group not in GROUPS:
            raise ValueError(f'第 {col + 1} 列分组必须是：' + '、'.join(GROUPS))
        t = Taxon(str(name).strip(), group)
        if any(existing.name == t.name for existing in p.taxa):
            raise ValueError(f'类群名称重复：{t.name}')
        p.taxa.append(t)
        columns.append((col, t.id))
    if not p.taxa:
        raise ValueError('没有找到类群列')
    for r,row in enumerate(rows[2:],2):
        if not any(v is not None and str(v).strip() for v in row):
            continue
        values = {}
        for col,tid in columns:
            value = '' if col>=len(row) or row[col] is None else str(row[col])
            if mode=='percentages':
                try:
                    value = normalize_percentage(value,unit,(r,col) in percent_cells)
                except (ValueError,OverflowError):
                    pass  # Preserve invalid input for the in-app diagnostic table.
            values[tid] = value
        p.samples.append(Sample('' if row[0] is None else str(row[0]),values))
    # Only our explicit settings sheet restores denominator membership.
    if mode=='counts' and settings and tuple(settings[0][:3]) == ('类群', '分组', '进入基数'):
        membership = {str(r[0]): r[2] in (True, 1, '是') for r in settings[1:] if len(r) >= 3}
        for t in p.taxa:
            t.selected = membership.get(t.name, False)
    if Path(path).suffix.lower() != '.csv':
        if meta.get('坐标类型') in ('深度', '年代'):
            p.axis_kind = meta['坐标类型']
        if meta.get('坐标单位') is not None:
            p.unit = str(meta['坐标单位'])
        if isinstance(meta.get('低计数提示阈值'), int) and 0 <= meta['低计数提示阈值'] <= 100000000:
            p.threshold = meta['低计数提示阈值']
        if isinstance(meta.get('显示小数位数'), int) and 0 <= meta['显示小数位数'] <= 8:
            p.decimals = meta['显示小数位数']
    return p


def style_sheet(ws, header_rows=1):
    ws.freeze_panes = 'B3' if header_rows == 2 else 'A2'
    for row in ws.iter_rows(min_row=1, max_row=header_rows):
        for cell in row:
            cell.fill = PatternFill('solid', fgColor='166B63')
            cell.font = Font(name='Microsoft YaHei', color='FFFFFF', bold=True)
            cell.alignment = Alignment(horizontal='center', vertical='center')
    for row in ws.iter_rows(min_row=header_rows + 1):
        for cell in row:
            cell.font = Font(name='Microsoft YaHei', size=10)
            if cell.row % 2:
                cell.fill = PatternFill('solid', fgColor='F0F6F5')
    for col in range(1, ws.max_column + 1):
        ws.column_dimensions[get_column_letter(col)].width = 20
    ws.row_dimensions[1].height = 28


def put(ws, row, col, value):
    cell = ws.cell(row, col, value)
    # Treat all user text as literal text, including leading '='.
    if isinstance(value, str):
        cell.data_type = 's'
    return cell


def data_sheet(wb, title, p, percentages=None):
    ws = wb.create_sheet(title)
    taxa = p.ordered_taxa()
    put(ws, 1, 1, '深度/年代')
    ws.merge_cells(start_row=1, start_column=1, end_row=2, end_column=1)
    col = 2
    for group in GROUPS:
        members = [t for t in taxa if t.group == group]
        if not members:
            continue
        put(ws, 1, col, group)
        if len(members) > 1:
            ws.merge_cells(start_row=1, start_column=col, end_row=1, end_column=col + len(members) - 1)
        for t in members:
            put(ws, 2, col, t.name)
            col += 1
    for i, sample in enumerate(p.samples, 3):
        try:
            axis = float(sample.axis)
        except (ValueError, TypeError):
            axis = sample.axis
        put(ws, i, 1, axis)
        for j, t in enumerate(taxa, 2):
            value = sample.counts.get(t.id, '') if percentages is None else percentages[i - 3]['percentages'][t.id]
            if percentages is None:
                try:
                    if p.data_type=='percentages':
                        parsed = percentage(value)
                        value = None if parsed is None else float(parsed)
                    else:
                        value = int(value)
                except (ValueError, TypeError):
                    pass
            cell = put(ws, i, j, value)
            if percentages is not None or p.data_type=='percentages':
                cell.number_format = '0' + ('.' + '0' * p.decimals if p.decimals else '')
    style_sheet(ws, 2)
    return ws


def write_excel(p, path, template=False):
    results, issues = calculate(p)
    if not template and any(i[0] == '错误' for i in issues):
        raise ValueError('存在数据错误，请修正后导出计算结果')
    wb = Workbook()
    wb.remove(wb.active)
    direct = p.data_type=='percentages'
    data_sheet(wb, '导入百分比' if direct else '原始计数', p)
    if not template:
        data_sheet(wb, '百分比结果', p, results)
        if not direct:
            ws = wb.create_sheet('基数明细')
            ws.append(['深度/年代', '实际基数', '说明'])
            for s, r in zip(p.samples, results):
                ws.append([s.axis, r['base'], r['reason']])
            style_sheet(ws)
    ws = wb.create_sheet('类群设置')
    ws.append(['类群', '分组', '进入基数'])
    for i, t in enumerate(p.ordered_taxa(), 2):
        for j, value in enumerate((t.name, t.group, '不适用（已有百分比）' if direct else ('是' if t.selected else '否')), 1):
            put(ws, i, j, value)
    style_sheet(ws)
    ws = wb.create_sheet('计算说明')
    for row in [('项目', '内容'), ('软件版本', __version__), ('坐标类型', p.axis_kind),
                ('坐标单位', p.unit), ('数据类型',input_label(p)),('数值存储单位','百分数' if direct else '计数'),
                ('低计数提示阈值', '不适用（原始计数未知）' if direct else p.threshold),
                ('显示小数位数', p.decimals), ('导出时间', datetime.now().isoformat(timespec='seconds')),
                ('公式', '直接采用导入百分比，不重新计算、不归一化；12.5 表示 12.5%' if direct else '类群计数 / 本行勾选类群计数和 × 100；数值单位为 %'),
                ('缺失值', '空白代表缺失，0 是有效零百分比；各类群缺失独立保留' if direct else '空白代表缺失；基数类群缺失时该行百分比为空；零分母不计算'),
                ('精度', '百分比保留计算值，小数位数仅为显示格式'),
                ('模板说明', '前两行为表头；第三行开始填写百分数；空白不同于零' if direct else '前两行为表头；第三行开始填写计数；空白不同于零'),
                ('导入文件',p.source_info.get('file','')),('导入工作表',p.source_info.get('sheet','')),
                ('导入数值解释',{'fraction':'比例：0.125 表示12.5%','percent':'百分数：12.5 表示12.5%','counts':'原始计数'}.get(p.source_info.get('numeric_unit'),'手动输入／旧项目')),
                ('Excel百分比格式单元格数',p.source_info.get('excel_percentage_cells',0))]:
        ws.append(row)
    ws.column_dimensions['B'].width = 80
    style_sheet(ws)
    ws.column_dimensions['B'].width = 80
    if not template:
        ws = wb.create_sheet('检查报告')
        ws.append(['级别', '数据行（不含表头）', '字段', '说明'])
        for issue in issues:
            ws.append(issue)
        style_sheet(ws)
        ws.column_dimensions['D'].width = 60
    # Sanitize text on every sheet, including diagnostic fields.
    for ws in wb:
        for row in ws:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
    target = Path(path)
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.xlsx', delete=False) as f:
        temporary = Path(f.name)
    try:
        wb.save(temporary)
        os.replace(temporary, target)
    finally:
        wb.close()
        if temporary.exists():
            temporary.unlink()
