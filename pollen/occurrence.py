"""Discrete pollen detections, explicit missingness and balanced single-sheet rows."""
import copy
from dataclasses import dataclass
from pathlib import Path
import math
import os
import tempfile
import numpy as np

from .core import GROUPS, count, number, calculate, input_label
from .plotting import numeric

MARKERS = {'square':('实心方块','s'), 'circle':('实心圆','o'), 'open_circle':('空心圆','o'),
           'triangle':('三角形','^'), 'diamond':('菱形','D'), 'horizontal':('短横线','_'), 'vertical':('短竖线','|')}
DEFAULTS = {'taxa_ids':None,'order':[],'excluded_sample_ids':[], 'mode':'counts',
    'layout':'balanced','row_limit':40,'manual_starts':[], 'hide_zero':False,
    'marker':'square','marker_size':2,'marker_by_group':False,'group_markers':{},
    'point_color':'#222222','color_by_group':False,'missing_color':'#969FA5','missing_size':2.3,
    'font':'Arial','font_size':7.5,'tick_size':7,'axis_size':8,'angle':65,'italic':True,
    'width_cm':18.3,'height_cm':None,'panel_height_cm':6.2,'panel_gap_cm':.7,
    'spine_width':.6,'group_line_width':.6,'title':'','background':'#FFFFFF','letters':True,
    'y_min':None,'y_max':None,'y_step':None,'reverse_y':True,
    'show_boundaries':False,'boundary_color':'#606060','boundary_width':.5,'dpi':300}


def config_for(project):
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(copy.deepcopy(project.occurrence_config))
    if project.data_type=='percentages' and 'mode' not in project.occurrence_config:
        cfg['mode'] = 'percentages'
    known = {t.id for t in project.taxa}
    if cfg['taxa_ids'] is None:
        cfg['taxa_ids'] = [t.id for t in project.ordered_taxa()]
    cfg['taxa_ids'] = list(dict.fromkeys(tid for tid in cfg['taxa_ids'] if tid in known))
    cfg['order'] = list(dict.fromkeys(tid for tid in cfg['order'] if tid in known))
    cfg['order'] += [t.id for t in project.ordered_taxa() if t.id not in cfg['order']]
    samples = {s.id for s in project.samples}
    cfg['excluded_sample_ids'] = [sid for sid in cfg['excluded_sample_ids'] if sid in samples]
    cfg['manual_starts'] = list(dict.fromkeys(tid for tid in cfg['manual_starts'] if tid in known))
    return cfg


def validate_config(cfg):
    from matplotlib.colors import is_color_like
    if cfg['mode'] not in ('counts','percentages') or cfg['layout'] not in ('balanced','groups','manual'):
        raise ValueError('检出依据或分排方式无效')
    if not isinstance(cfg['row_limit'],int) or not 5<=cfg['row_limit']<=40:
        raise ValueError('每排类群上限应为 5 至 40')
    for key,label,lo,hi in [('marker_size','检出符号大小',.5,8),('missing_size','缺失符号大小',.5,8),
        ('font_size','名称字号',5,16),('tick_size','刻度字号',5,16),('axis_size','轴标题字号',5,18),
        ('angle','名称角度',30,90),('width_cm','图宽',10,60),('panel_height_cm','每排绘图区高度',3,20),
        ('panel_gap_cm','排间距',.2,4),('spine_width','坐标线宽',.2,2),
        ('group_line_width','分组线宽',.2,2),('boundary_width','分带线宽',.1,3),('dpi','分辨率',72,600)]:
        numeric(cfg[key],label,lo,hi)
    numeric(cfg['height_cm'],'总图高',6,150,optional=True)
    for key in ('y_min','y_max'):
        numeric(cfg[key],'纵轴范围',optional=True)
    if cfg['y_min'] is not None and cfg['y_max'] is not None and cfg['y_max']<=cfg['y_min']:
        raise ValueError('纵轴最大值必须大于最小值')
    numeric(cfg['y_step'],'纵轴刻度间隔',1e-12,optional=True)
    if cfg['marker'] not in MARKERS or any(v not in MARKERS for v in cfg['group_markers'].values()):
        raise ValueError('检出符号无效')
    if any(not is_color_like(cfg[k]) for k in ('point_color','missing_color','background','boundary_color')):
        raise ValueError('出现分布图颜色设置无效')


@dataclass
class OccurrenceData:
    config: dict
    taxa: list
    indices: list
    y: object
    counts: object
    percentages: object
    states: object
    bases: list
    reasons: dict
    warnings: list


def prepare(project):
    cfg = config_for(project)
    validate_config(cfg)
    selected = set(cfg['taxa_ids'])
    order = {tid:i for i,tid in enumerate(cfg['order'])}
    taxa = sorted([t for t in project.taxa if t.id in selected],key=lambda t:(GROUPS.index(t.group),order[t.id]))
    if not taxa:
        raise ValueError('请至少选择一个展示类群')
    if any(not t.name.strip() for t in taxa):
        raise ValueError('展示类群名称不能为空')
    if len({t.name.strip() for t in taxa})!=len(taxa):
        raise ValueError('展示类群名称重复，请先核对或重命名')
    excluded = set(cfg['excluded_sample_ids'])
    indices = [i for i,s in enumerate(project.samples) if s.id not in excluded]
    if not indices:
        raise ValueError('请至少保留一个有效样品')
    coordinates = {}
    for i in indices:
        try:
            value = number(project.samples[i].axis)
            if value is None or not math.isfinite(float(value)):
                raise ValueError()
            coordinates[i] = float(value)
        except (ValueError,OverflowError):
            raise ValueError(f'原表第 {i+1} 行深度／年代必须是有限数字') from None
    if len(set(coordinates.values()))!=len(coordinates):
        raise ValueError('存在重复深度／年代，符号会重叠；请核对数据或明确排除重复样品。')
    indices.sort(key=coordinates.get)
    warnings = []
    if indices!=sorted(indices):
        warnings.append('仅按真实坐标排序绘图，原始表格顺序保留')
    direct = project.data_type=='percentages'
    if direct and cfg['mode']=='counts':
        raise ValueError('已有百分比数据没有原始计数，请选择“实际百分比大于零”检出模式')
    if not direct and cfg['mode']=='percentages' and not any(t.selected for t in project.taxa):
        raise ValueError('百分比模式需要先在“数据与百分比”中选择计算基数')
    results,issues = calculate(project)
    if cfg['mode']=='percentages':
        fields = {t.name for t in project.taxa if t.selected} | {t.name for t in taxa}
        included_rows = {i+1 for i in indices}
        for level,row,field,message in issues:
            if level=='错误' and isinstance(row,int) and row in included_rows and field in fields:
                raise ValueError(f'请核对原表第 {row} 行 / {field}：{message}')
    n,p = len(indices),len(taxa)
    counts = np.empty((n,p),dtype=object)
    percentages = np.full((n,p),np.nan)
    states = np.zeros((n,p),dtype=np.int8)
    reasons = {}
    for row,i in enumerate(indices):
        for col,t in enumerate(taxa):
            value = None
            if not direct:
                try:
                    value = count(project.samples[i].counts.get(t.id,''))
                except ValueError as exc:
                    raise ValueError(f'原表第 {i+1} 行“{t.name}”：{exc}') from None
            counts[row,col] = value
            pct = results[i]['percentages'][t.id]
            if pct is not None and math.isfinite(pct):
                percentages[row,col] = pct
            if direct:
                states[row,col] = -3 if pct is None or not math.isfinite(pct) else int(pct>0)
                if states[row,col]<0:
                    reasons[row,col] = '导入百分比缺失'
            elif value is None:
                states[row,col] = -1
                reasons[row,col] = '原始计数缺失'
            elif cfg['mode']=='counts':
                states[row,col] = int(value>0)
            elif pct is None or not math.isfinite(pct):
                states[row,col] = -2
                reasons[row,col] = results[i]['reason'] or '百分比超出可计算范围'
            else:
                # Full-precision percentages: a displayed 0.00% may still be positive.
                states[row,col] = int(pct>0)
    if cfg['hide_zero']:
        keep = np.any(states!=0,axis=0)
        taxa = [t for j,t in enumerate(taxa) if keep[j]]
        remap = {j:k for k,j in enumerate(np.flatnonzero(keep))}
        reasons = {(i,remap[j]):v for (i,j),v in reasons.items() if j in remap}
        counts,percentages,states = counts[:,keep],percentages[:,keep],states[:,keep]
        if not taxa:
            raise ValueError('所有展示类群均为有效零值，请关闭“隐藏全零类群”或重新选择类群')
    missing = int(np.sum(states<0))
    if missing:
        warnings.append(f'{missing} 个单元格缺失或无法判定，以灰色 × 显示，不计入检出频率分母')
    return OccurrenceData(cfg,taxa,indices,np.array([coordinates[i] for i in indices]),counts,
        percentages,states,[results[i]['base'] for i in indices],reasons,warnings)


def partitions(taxa,cfg):
    n,limit = len(taxa),cfg['row_limit']
    if not n:
        return []
    if cfg['layout']=='balanced':
        rows = math.ceil(n/limit)
        size,extra = divmod(n,rows)
        output,start = [],0
        for i in range(rows):
            end = start+size+(i<extra)
            output.append(taxa[start:end])
            start = end
        return output
    if cfg['layout']=='manual':
        starts = set(cfg['manual_starts'])
        output = []
        for t in taxa:
            if not output or (t.id in starts and output[-1]):
                output.append([])
            output[-1].append(t)
        for i,row in enumerate(output,1):
            if len(row)>limit:
                raise ValueError(f'手动分排第 {i} 排有 {len(row)} 个类群，超过上限 {limit}；请调整换排位置或恢复均衡分排。')
        return output
    # Preserve contiguous groups where possible; oversized groups are split.
    chunks,start = [],0
    while start<n:
        end = start+1
        while end<n and taxa[end].group==taxa[start].group:
            end += 1
        chunks.extend(partitions(taxa[start:end],dict(cfg,layout='balanced')))
        start = end
    output = [[]]
    for chunk in chunks:
        if output[-1] and len(output[-1])+len(chunk)>limit:
            output.append([])
        output[-1].extend(chunk)
    return output


def state_label(value):
    return {1:'检出',0:'未检出',-1:'计数缺失',-2:'百分比无法计算',-3:'百分比缺失'}[int(value)]


def export_analysis(project,path):
    from openpyxl import Workbook
    from .excel import put,style_sheet
    from . import __version__
    data = prepare(project)
    cfg = data.config
    rows = partitions(data.taxa,cfg)
    headers = ['原表数据行','深度/年代']+[t.name for t in data.taxa]
    binary = [headers]+[[i+1,float(data.y[r])]+[int(v) if v>=0 else None for v in data.states[r]] for r,i in enumerate(data.indices)]
    statuses = [headers]+[[i+1,float(data.y[r])]+[state_label(v) for v in data.states[r]] for r,i in enumerate(data.indices)]
    missing = [['原表数据行','深度/年代','类群','判定状态','原因']]+[
        [data.indices[r]+1,float(data.y[r]),data.taxa[c].name,state_label(data.states[r,c]),reason]
        for (r,c),reason in data.reasons.items()]
    stats = [['类群','分组','参与样品数','有效判定样品数','检出样品数','缺失或无法判定数','检出频率（%）']]
    for j,t in enumerate(data.taxa):
        valid,found = int(np.sum(data.states[:,j]>=0)),int(np.sum(data.states[:,j]==1))
        stats.append([t.name,t.group,len(data.indices),valid,found,len(data.indices)-valid,100*found/valid if valid else None])
    actual = [['原表数据行','深度/年代','实际基数']+[t.name for t in data.taxa]]+[
        [i+1,float(data.y[r]),data.bases[r]]+[None if not np.isfinite(v) else float(v) for v in data.percentages[r]]
        for r,i in enumerate(data.indices)]
    counts = [headers]+[[i+1,float(data.y[r])]+data.counts[r].tolist() for r,i in enumerate(data.indices)]
    placement = [['排号','排内顺序','类群','分组','检出符号']]+[
        [i+1,j+1,t.name,t.group,MARKERS[cfg['group_markers'].get(t.group,cfg['marker']) if cfg['marker_by_group'] else cfg['marker']][0]]
        for i,row in enumerate(rows) for j,t in enumerate(row)]
    settings = [['项目','内容'],['软件版本',__version__],['输入数据类型',input_label(project)],['检出依据','原始计数 > 0' if cfg['mode']=='counts' else '未四舍五入的百分比 > 0'],
        ['百分比基数','不适用；直接采用导入百分比，原始基数未知' if project.data_type=='percentages' else ('、'.join(t.name for t in project.taxa if t.selected) or '未选择')],
        ['百分比精度','使用实际计算值，显示为 0.00% 的正值仍判为检出'],
        ['缺失处理','原始计数缺失或百分比无法计算均保留原因，检出频率分母不包含这些单元格'],
        ['分排方式',{'balanced':'均衡分排','groups':'完整分组优先','manual':'手动换排'}[cfg['layout']]],
        ['每排上限',cfg['row_limit']],['实际各排类群数','、'.join(str(len(row)) for row in rows)],
        ['坐标类型',project.axis_kind],['坐标单位',project.unit],['提示','；'.join(data.warnings)]]
    settings.append(['统计范围','检出矩阵与频率基于全部勾选样品；纵轴范围仅裁切图形显示，不改变统计样品集合'])
    included = set(data.indices)
    sample_settings = [['原表数据行','深度/年代','参与绘图']]+[[i+1,s.axis,'是' if i in included else '明确排除'] for i,s in enumerate(project.samples)]
    wb = Workbook()
    wb.remove(wb.active)
    tables = [('检出矩阵',binary),('判定状态',statuses),('缺失与无法判定',missing),('类群统计',stats)]
    if project.data_type=='counts':
        tables += [('原始计数',counts),('实际百分比与基数',actual)]
    else:
        tables += [('导入百分比',[[row[0],row[1]]+row[3:] for row in actual])]
    tables += [('图中分排',placement),('样品选择',sample_settings),('绘图说明',settings)]
    for name,table in tables:
        ws = wb.create_sheet(name)
        for i,row in enumerate(table,1):
            for j,value in enumerate(row,1):
                put(ws,i,j,value)
        style_sheet(ws)
    target = Path(path)
    with tempfile.NamedTemporaryFile(dir=target.parent,suffix='.xlsx',delete=False) as f:
        temporary = Path(f.name)
    try:
        wb.save(temporary)
        os.replace(temporary,target)
    finally:
        wb.close()
        if temporary.exists():
            temporary.unlink()
