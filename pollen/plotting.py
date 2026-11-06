"""Scientific diagram preparation/rendering; no Qt and no pyplot dependency."""
import copy
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from contextlib import contextmanager

import numpy as np
import matplotlib
from matplotlib import font_manager, colors
from matplotlib.figure import Figure

from .core import GROUPS, calculate, number

PREVIOUS_GROUP_COLORS = dict(zip(GROUPS, ('#67A583', '#A2C986', '#D0E2C0', '#A1D8E8', '#49C2D9', '#7B95C6')))
GROUP_COLORS = dict(zip(GROUPS, ('#67A583', '#A2C986', '#49C2D9', '#7B95C6', '#FFC1A6', '#F47254')))
DEFAULTS = {
    'layout_version': 3, 'palette_version': 2, 'title': '', 'angle': 45, 'font': 'Arial', 'font_size': 7.5,
    'tick_size': 6.5, 'axis_size': 8, 'spine_width': 0.45,
    'width_cm': 18.3, 'height_cm': 12.5, 'paginate': False, 'panels_per_page': 60,
    'width_mode': 'abundance', 'rare_threshold': 2.0, 'auto_dense': True,
    'left_cm': 1.45, 'right_cm': 0.55, 'top_cm': 2.2, 'bottom_cm': 1.15,
    'gap_cm': 0.10, 'group_gap_cm': 0.25, 'line_width': 0.45,
    'reverse_y': True, 'sort_y': True, 'y_min': None, 'y_max': None, 'y_step': None,
    'x_mode': 'auto', 'x_max': 100, 'hide_zero': True, 'dpi': 300,
    'group_colors': GROUP_COLORS, 'taxa': {},
}
_font_ready = False


def setup_fonts():
    global _font_ready
    if not _font_ready:
        for path in ('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simsun.ttc'):
            if Path(path).exists():
                font_manager.fontManager.addfont(path)
        _font_ready = True


def config_for(project):
    config = copy.deepcopy(DEFAULTS)
    source = project.plot_config
    # Upgrade legacy layout only; retain scientific settings, colors and visibility.
    legacy_layout = source and source.get('layout_version') != 3
    style_keys = {'font_size', 'width_cm', 'height_cm', 'left_cm', 'right_cm',
                  'top_cm', 'bottom_cm', 'gap_cm', 'group_gap_cm', 'line_width',
                  'paginate', 'panels_per_page', 'width_mode', 'rare_threshold', 'auto_dense'}
    for key in DEFAULTS:
        if key in source and key not in ('group_colors', 'taxa'):
            if legacy_layout and key in style_keys:
                continue
            config[key] = copy.deepcopy(source[key])
    config['layout_version'] = 3
    config['paginate'] = False
    config['panels_per_page'] = 60
    config['group_colors'].update(source.get('group_colors', {}))
    old_palette = source.get('palette_version') != 2
    if old_palette:
        for group in GROUPS:
            if str(config['group_colors'][group]).upper() == PREVIOUS_GROUP_COLORS[group]:
                config['group_colors'][group] = GROUP_COLORS[group]
    config['palette_version'] = 2
    for index, taxon in enumerate(project.ordered_taxa()):
        opts = {'visible': True, 'order': index, 'color': '', 'style': 'fill',
                'x_max': None, 'x_step': None, 'exaggeration': 1, 'width': 1}
        opts.update(source.get('taxa', {}).get(taxon.id, {}))
        if old_palette and str(opts['color']).upper() == PREVIOUS_GROUP_COLORS[taxon.group]:
            opts['color'] = ''
        config['taxa'][taxon.id] = opts
    return config


def fingerprint(project):
    return hashlib.sha256(json.dumps(asdict(project), ensure_ascii=False,
                                     sort_keys=True, allow_nan=False).encode('utf-8')).hexdigest()


def nice_scale(value):
    if value <= 0:
        return 1.0, 0.5
    scale = 10 ** math.floor(math.log10(value))
    fraction = value / scale
    upper = next((n * scale for n in (1, 2, 2.5, 4, 5, 6, 8, 10) if n >= fraction - 1e-10), 10 * scale)
    coefficient = round(upper / scale, 6)
    step = {1: .2, 2: .5, 2.5: .5, 4: 1, 5: 1, 6: 2, 8: 2, 10: 2}[coefficient] * scale
    return upper, step


def readable_step(upper, initial, panel_cm, font, renderer):
    """Reduce automatic tick density based on physical label widths, not DPI."""
    step = initial
    for _ in range(16):
        ticks = np.arange(step, upper * (1 + 1e-9), step)
        widths = [renderer.get_text_width_height_descent(f'{value:g}', font, ismath=False)[0]
                  for value in ticks]
        separation = step / upper * panel_cm * 72 / 2.54
        if len(ticks) < 2 or separation >= max(widths, default=0) + 5:
            break
        scale = 10 ** math.floor(math.log10(step))
        step = next(n * scale for n in (1, 2, 2.5, 5, 10, 20) if n * scale > step * (1+1e-9))
    return step


def numeric(value, label, low=None, high=None, optional=False):
    if value is None and optional:
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f'{label}必须是数字')
    if not math.isfinite(result) or (low is not None and result < low) or (high is not None and result > high):
        raise ValueError(f'{label}超出允许范围')
    return result


def validate_config(config):
    for key, label, low, high in (
        ('width_cm', '图幅宽度', 8, 60), ('height_cm', '图幅高度', 6, 40),
        ('font_size', '名称字号', 5, 18), ('tick_size', '刻度字号', 5, 16),
        ('axis_size', '坐标标题字号', 5, 18), ('spine_width', '坐标线宽', 0.2, 2), ('angle', '名称角度', 0, 90),
        ('line_width', '轮廓线宽', 0.1, 3), ('left_cm', '左边距', 0.5, 15),
        ('right_cm', '右边距', 0.3, 15), ('top_cm', '上边距', 1, 25),
        ('bottom_cm', '下边距', .6, 15), ('gap_cm', '列间距', 0, 3),
        ('group_gap_cm', '组间距', 0, 5), ('dpi', '分辨率', 72, 600)):
        numeric(config[key], label, low, high)
    capacity = numeric(config['panels_per_page'], '每幅类群上限', 1, 60)
    if capacity != int(capacity):
        raise ValueError('每幅类群上限必须是整数')
    for key in ('y_min', 'y_max'):
        numeric(config[key], '纵轴范围', optional=True)
    if config['y_step'] is not None:
        numeric(config['y_step'], '纵轴步长', 1e-12)
    if config['x_mode'] not in ('auto', 'uniform'):
        raise ValueError('未知横轴模式')
    if config['width_mode'] not in ('abundance', 'equal'):
        raise ValueError('未知列宽分配方式')
    numeric(config['rare_threshold'], '低占比阈值', 0, 100)
    numeric(config['x_max'], '统一横轴上限', 1e-12)
    for color in config['group_colors'].values():
        if not colors.is_color_like(color):
            raise ValueError('分组颜色无效')
    for opts in config['taxa'].values():
        if opts['style'] not in ('fill', 'line', 'bar'):
            raise ValueError('未知曲线类型')
        numeric(opts['width'], '列宽权重', 0.3, 5)
        numeric(opts['exaggeration'], '放大倍数', 1, 20)
        if opts['x_max'] is not None:
            numeric(opts['x_max'], '类群横轴上限', 1e-12)
        if opts['x_step'] is not None:
            numeric(opts['x_step'], '类群刻度间隔', 1e-12)
        if opts['color'] and not colors.is_color_like(opts['color']):
            raise ValueError('类群颜色无效')


@dataclass
class PlotData:
    config: dict
    taxa: list
    indices: list
    y: np.ndarray
    values: dict
    bases: list
    warnings: list


def prepare(project):
    config = config_for(project)
    validate_config(config)
    if not project.samples or not project.taxa:
        raise ValueError('请先导入或填写样品和类群计数')
    results, issues = calculate(project)
    errors = [i for i in issues if i[0] == '错误']
    if errors:
        issue = errors[0]
        raise ValueError(f'请先修正数据：第 {issue[1]} 行 / {issue[2]} / {issue[3]}')
    original_y = [float(number(s.axis)) for s in project.samples]
    if any(not math.isfinite(v) for v in original_y):
        raise ValueError('坐标超出可绘制的数字范围')
    if len(original_y) != len(set(original_y)):
        raise ValueError('存在重复深度/年代；请先核对数据，不自动合并或平均')
    indices = sorted(range(len(original_y)), key=lambda i: original_y[i])
    warnings = []
    if project.data_type=='percentages':
        warnings.append('直接采用导入百分比，未重新计算或归一化')
    if indices != list(range(len(indices))):
        if not config['sort_y']:
            raise ValueError('坐标未递增，请启用“仅绘图排序”或先整理原始数据')
        warnings.append('仅绘图按坐标递增排序，原始表格顺序保持不变')
    taxa = sorted(project.taxa, key=lambda t: (GROUPS.index(t.group), config['taxa'][t.id]['order']))
    values = {t.id: np.array([np.nan if results[i]['percentages'][t.id] is None else results[i]['percentages'][t.id]
                             for i in indices], dtype=float) for t in taxa}
    taxa = [t for t in taxa if config['taxa'][t.id]['visible'] and not (
        config['hide_zero'] and np.any(np.isfinite(values[t.id])) and np.all(np.nan_to_num(values[t.id]) == 0))]
    if not taxa:
        raise ValueError('没有可展示的类群，请勾选类群或关闭“隐藏全零类群”')
    if len(taxa) > 60:
        raise ValueError(f'当前选择了 {len(taxa)} 个展示类群，单幅最多支持 60 个；请在左侧取消部分展示勾选')
    if not any(np.any(np.isfinite(values[t.id])) for t in taxa):
        raise ValueError('所选类群没有可计算的百分比，请检查基数与缺失值')
    if any(np.any(~np.isfinite(values[t.id])) for t in taxa):
        warnings.append('缺失值处断线、停止填色；未补零或跨点连接')
    return PlotData(config, taxa, indices, np.array([original_y[i] for i in indices]),
                    values, [results[i]['base'] for i in indices], warnings)


@dataclass
class Diagram:
    figure: Figure
    axes: list
    data: PlotData
    limits: dict
    signature: str
    page_index: int = 0
    page_count: int = 1
    layout: dict = field(default_factory=dict)
    extra_axes: dict = field(default_factory=dict)


def build_diagrams(project):
    from .render import build_diagrams as render
    return render(project)


def build_diagram(project, page_index=0):
    return build_diagrams(project)[page_index]


def export_diagram(project, diagram, path, dpi=300):
    if diagram.signature != fingerprint(project):
        raise ValueError('数据或绘图设置已改变，请先更新图谱')
    target = Path(path)
    if target.suffix.lower() not in ('.png', '.svg', '.pdf'):
        raise ValueError('仅支持 PNG、SVG 和 PDF')
    numeric(dpi, '分辨率', 72, 600)
    if target.suffix.lower() == '.png':
        width, height = diagram.figure._pollen_size
        if width * height * dpi * dpi > 80000000 or max(width, height) * dpi > 32000:
            raise ValueError('PNG 像素过大，请降低分辨率、减小图幅，或使用 PDF/SVG 矢量格式')
    # Reset inspection pan/zoom before export, preserving the full configured axes.
    view_axes = getattr(diagram.figure,'_pollen_axes',diagram.axes)
    limits = [(ax.get_xlim(), ax.get_ylim()) for ax in view_axes]
    size = tuple(diagram.figure.get_size_inches())
    diagram.figure.set_size_inches(diagram.figure._pollen_size, forward=False)
    original_limits = getattr(diagram.figure, '_pollen_limits', None)
    if original_limits:
        for ax, (xlim, ylim) in zip(view_axes, original_limits):
            ax.set_xlim(xlim)
            ax.set_ylim(ylim)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=target.suffix, delete=False) as file:
            temporary = Path(file.name)
        with matplotlib.rc_context({'svg.fonttype': 'none', 'pdf.fonttype': 42, 'text.parse_math': False}):
            diagram.figure.savefig(temporary, format=target.suffix[1:].lower(), dpi=dpi,
                                   facecolor=getattr(diagram.figure,'_pollen_facecolor','white'))
        os.replace(temporary, target)
    finally:
        for ax, (xlim, ylim) in zip(view_axes, limits):
            ax.set_xlim(xlim)
            ax.set_ylim(ylim)
        diagram.figure.set_size_inches(size, forward=False)
        if temporary and temporary.exists():
            temporary.unlink()


@contextmanager
def full_figure(diagram):
    size = tuple(diagram.figure.get_size_inches())
    view_axes = getattr(diagram.figure,'_pollen_axes',diagram.axes)
    limits = [(ax.get_xlim(), ax.get_ylim()) for ax in view_axes]
    try:
        diagram.figure.set_size_inches(diagram.figure._pollen_size, forward=False)
        for ax, (xlim, ylim) in zip(view_axes, diagram.figure._pollen_limits):
            ax.set_xlim(xlim)
            ax.set_ylim(ylim)
        yield diagram.figure
    finally:
        for ax, (xlim, ylim) in zip(view_axes, limits):
            ax.set_xlim(xlim)
            ax.set_ylim(ylim)
        diagram.figure.set_size_inches(size, forward=False)


def output_paths(path, count):
    path = Path(path)
    if count == 1 or path.suffix.lower() == '.pdf':
        return [path]
    return [path.with_name(f'{path.stem}_{i+1:02d}{path.suffix}') for i in range(count)]


def export_document(project, diagrams, path, dpi=300):
    if not diagrams or any(d.signature != fingerprint(project) for d in diagrams):
        raise ValueError('数据或绘图设置已改变，请先更新图谱')
    paths = output_paths(path, len(diagrams))
    if len(diagrams) == 1 or Path(path).suffix.lower() != '.pdf':
        for diagram, target in zip(diagrams, paths):
            export_diagram(project, diagram, target, dpi)
        return paths
    from matplotlib.backends.backend_pdf import PdfPages
    target = Path(path)
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.pdf', delete=False) as file:
        temporary = Path(file.name)
    try:
        with matplotlib.rc_context({'pdf.fonttype': 42, 'text.parse_math': False}), PdfPages(temporary) as pdf:
            for d in diagrams:
                with full_figure(d) as figure:
                    pdf.savefig(figure, facecolor='white', dpi=dpi)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return paths


def save_style(project, path):
    config = config_for(project)
    # Taxon names rather than project IDs allow reuse on another imported file.
    config['taxa'] = {t.name: config['taxa'][t.id] for t in project.taxa}
    Path(path).write_text(json.dumps({'format': 'pollen-plot-style', 'version': 1, 'config': config},
                                   ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def load_style(project, path):
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if payload.get('format') != 'pollen-plot-style' or payload.get('version') != 1:
        raise ValueError('不是支持的绘图样式文件')
    config = copy.deepcopy(payload['config'])
    config['taxa'] = {t.id: config.get('taxa', {}).get(t.name, {}) for t in project.taxa}
    old = project.plot_config
    try:
        project.plot_config = config
        resolved = config_for(project)
        validate_config(resolved)
        return resolved
    finally:
        project.plot_config = old


def diagram_example():
    """Synthetic, deterministic dataset for previewing all six groups; not a real core."""
    from .core import Project, Taxon, Sample
    taxa = [Taxon(name, group, selected=group in GROUPS[:3])
            for group, names in zip(GROUPS, [
                ['松属', '栎属', '云杉属', '桦木属'], ['榛属', '蔷薇科'],
                ['蒿属', '禾本科', '藜科', '莎草科'], ['香蒲属', '眼子菜属'],
                ['水龙骨科', '凤尾蕨属'], ['未定类群1', '未定类群2']])
            for name in names]
    rows = []
    for i in range(32):
        counts = [int(max(0, baseline + amplitude * math.sin(i * frequency + phase)))
                  for baseline, amplitude, frequency, phase in [
                      (90, 45, .21, 0), (55, 30, .34, 1), (25, 23, .44, 2), (30, 20, .28, 3),
                      (12, 10, .39, 4), (8, 7, .49, 1), (40, 30, .31, 3), (35, 18, .27, 2),
                      (14, 13, .5, 1), (12, 11, .36, 0), (5, 4, .47, 2), (3, 3, .33, 1),
                      (10, 9, .42, 0), (5, 5, .55, 1), (3, 3, .43, 3), (1, 1, .3, 0)]]
        rows.append(Sample(str(5 + i * 5), {t.id: str(v) for t, v in zip(taxa, counts)}))
    return Project(taxa=taxa, samples=rows, plot_config={'title': '孢粉百分比图谱（模拟数据）'})
