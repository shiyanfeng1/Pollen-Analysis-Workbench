"""Single-sheet pollen diagrams with abundance-aware panel widths."""
import math
from dataclasses import replace
from decimal import Decimal

import matplotlib
import numpy as np
from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg, RendererAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, MultipleLocator, FuncFormatter

from .core import GROUPS
from .plotting import (prepare, setup_fonts, nice_scale, readable_step,
                       Diagram, fingerprint)
from .cluster_render import overlay_for, add_panels


def fixed_y_scale(data):
    cfg = data.config
    lo, hi = float(min(data.y)), float(max(data.y))
    if lo == hi:
        padding = max(abs(lo) * .02, .5)
        lo, hi = lo-padding, hi+padding
    raw_low = lo if cfg['y_min'] is None else cfg['y_min']
    raw_high = hi if cfg['y_max'] is None else cfg['y_max']
    if raw_high <= raw_low:
        raise ValueError('纵轴最大值必须大于最小值')
    span = raw_high - raw_low
    if cfg['y_step']:
        step = cfg['y_step']
    else:
        desired = span / 7
        scale = 10 ** math.floor(math.log10(desired))
        step = next(n*scale for n in (1, 2, 2.5, 5, 10) if n*scale >= desired*(1-1e-10))
    low = math.floor(raw_low/step + 1e-9)*step if cfg['y_min'] is None else raw_low
    high = math.ceil(raw_high/step - 1e-9)*step if cfg['y_max'] is None else raw_high
    if (high-low)/step > 150:
        raise ValueError('纵轴刻度超过 150 个，请增大刻度间隔')
    start = math.ceil(low/step - 1e-9)
    stop = math.floor(high/step + 1e-9)
    ticks = np.arange(start, stop+1, dtype=float)*step
    if not np.any((data.y >= low) & (data.y <= high)):
        raise ValueError('指定纵轴范围内没有样品')
    digits = max(0, -Decimal(f'{step:.10g}').normalize().as_tuple().exponent)
    return low, high, step, ticks, min(digits, 10)


def partitions(data):
    return [data.taxa]


def effective_style(config, count):
    style = {key: config[key] for key in ('angle', 'font_size', 'tick_size', 'line_width', 'gap_cm', 'group_gap_cm')}
    if config['auto_dense'] and count > 24:
        style['angle'] = 90
        style['font_size'] = min(config['font_size'], 7)
        style['tick_size'] = min(config['tick_size'], 6)
        style['line_width'] = min(config['line_width'], .4)
        style['gap_cm'] = min(config['gap_cm'], .02 if count >= 45 else .04)
        style['group_gap_cm'] = min(config['group_gap_cm'], .16)
    return style


def panel_widths(data, taxa, available, minimum):
    cfg = data.config
    if available < minimum*len(taxa):
        raise ValueError('单幅子图空间不足，请启用密集排版、减小名称字号/间距或适当增大图幅宽度')
    manual = np.array([cfg['taxa'][t.id]['width'] for t in taxa], dtype=float)
    maxima = np.array([float(np.nanmax(data.values[t.id])) if np.any(np.isfinite(data.values[t.id])) else 0 for t in taxa])
    if cfg['width_mode'] == 'equal':
        score = manual
    else:
        # Peak abundance preserves room for important spikes. Low-abundance taxa
        # receive the readable minimum; the remaining width favors larger curves.
        score = np.sqrt(np.maximum(np.minimum(maxima, 100)-cfg['rare_threshold'], 0))*manual
        score += np.maximum(manual-1, 0)
        if not np.any(score):
            score = manual
    widths = minimum+(available-minimum*len(taxa))*score/score.sum()
    return widths.tolist(), maxima.tolist()


def build_diagrams(project):
    setup_fonts()
    data = prepare(project)
    cfg = data.config
    overlay = overlay_for(project)
    style = effective_style(cfg, len(data.taxa))
    families = list(dict.fromkeys([cfg['font'], 'Microsoft YaHei', 'DejaVu Sans']))
    name_font = font_manager.FontProperties(family=families, size=style['font_size'])
    tick_font = font_manager.FontProperties(family=families, size=style['tick_size'])
    axis_font = font_manager.FontProperties(family=families, size=cfg['axis_size'])
    group_font = font_manager.FontProperties(family=families, size=max(style['font_size'], 6.5))
    renderer = RendererAgg(100, 100, 72)
    extents = {}
    angle = math.radians(style['angle'])
    unrotated_widths = []
    for t in data.taxa:
        w, h, _ = renderer.get_text_width_height_descent(t.name, name_font, ismath=False)
        h = max(h, style['font_size'])
        unrotated_widths.append(w*2.54/72)
        extents[t.id] = ((w*math.cos(angle)+h*math.sin(angle))*2.54/72,
                         (w*math.sin(angle)+h*math.cos(angle))*2.54/72)
    pages = partitions(data)
    width, height = cfg['width_cm'], cfg['height_cm']
    if cfg['auto_dense'] and len(data.taxa) > 24:
        # Bounded expansion leaves useful room for dominant curves while keeping
        # low-abundance labels legible; this never grows with label length.
        width = max(width, min(25.5, 18.3+.2*(len(data.taxa)-24)))
    curve_width = width
    # Only reserve label overhang at the last panel; do not multiply long-name width
    # by every taxon. Parallel 45-degree labels do not require disjoint bounding boxes.
    right = max(cfg['right_cm'], max(extents[page[-1].id][0] for page in pages) + .15)
    if overlay and overlay['extra_width_cm']:
        # Keep the pollen columns at their existing physical widths. The added
        # panels use one compact gap instead of retaining the old right margin.
        width += overlay['extra_width_cm']-right
    dense = len(data.taxa) > 24 and cfg['auto_dense']
    header_space = (1.35 if cfg['title'] else .95) if dense else (.95 if cfg['title'] else .55)
    top = max(cfg['top_cm'], max(h for w, h in extents.values()) + header_space + .30)
    left, bottom = cfg['left_cm'], cfg['bottom_cm']
    plot_height = height-top-bottom
    if plot_height < 3.5:
        raise ValueError('名称较长，当前图高不足；请增加图高、减小名称字号或缩短展示名称')
    low, high, y_step, y_ticks, digits = fixed_y_scale(data)
    signature = fingerprint(project)
    diagrams = []
    for page_index, taxa in enumerate(pages):
        gaps = [style['group_gap_cm'] if taxa[i].group != taxa[i-1].group else style['gap_cm'] for i in range(1, len(taxa))]
        available = curve_width-left-right-sum(gaps)
        # With parallel rotated labels, their perpendicular separation matters;
        # long names should not expand every column by the full label length.
        if style['angle'] < 5:
            minimum = max(unrotated_widths)+.05
        else:
            # Chinese fallback glyphs have a slightly taller line box than Latin.
            pitch = style['font_size']*1.10*2.54/72/max(math.sin(angle), .1)+.025
            minimum = max(.18, pitch-style['gap_cm'])
        widths, maxima = panel_widths(data, taxa, available, minimum)
        with matplotlib.rc_context({'font.family': families, 'font.size': style['tick_size'],
                                    'axes.unicode_minus': False, 'text.parse_math': False,
                                    'svg.fonttype': 'none', 'pdf.fonttype': 42}):
            fig = Figure(figsize=(width/2.54, height/2.54), dpi=100, facecolor='white')
            FigureCanvasAgg(fig)
            axes, limits, x_cm = [], {}, left
            labels = []
            for index, taxon in enumerate(taxa):
                opts = cfg['taxa'][taxon.id]
                panel_cm = widths[index]
                ax = fig.add_axes([x_cm/width, bottom/height, panel_cm/width, plot_height/height],
                                  sharey=axes[0] if axes else None)
                axes.append(ax)
                color = opts['color'] or cfg['group_colors'][taxon.group]
                vals = data.values[taxon.id]
                maximum = float(np.nanmax(vals))*opts['exaggeration'] if np.any(np.isfinite(vals)) else 0
                upper = opts['x_max'] if opts['x_max'] is not None else (
                    nice_scale(maximum)[0] if cfg['x_mode'] == 'auto' else cfg['x_max'])
                if upper+1e-9 < maximum:
                    raise ValueError(f'“{taxon.name}”横轴上限小于曲线值（含放大曲线），请增大上限或使用自动范围')
                step = opts['x_step'] or readable_step(upper, nice_scale(upper)[1], panel_cm, tick_font, renderer)
                if upper/step > 50:
                    raise ValueError(f'“{taxon.name}”横轴刻度过密，请增大间隔')
                compact = panel_cm < .55
                limits[taxon.id] = {'max': upper, 'step': step, 'color': color,
                                    'panel_width_cm': panel_cm, 'observed_max_percent': maxima[index],
                                    'compact_ticks': compact,
                                    'ticks': [upper] if compact else np.arange(step, upper*(1+1e-9), step).tolist()}
                if opts['exaggeration'] > 1:
                    inflated = vals*opts['exaggeration']
                    ax.fill_betweenx(data.y, 0, inflated, where=np.isfinite(inflated), color=color, alpha=.22, linewidth=0)
                    ax.plot(inflated, data.y, color=color, linewidth=style['line_width'], linestyle='--')
                    ax.text(.96, .98, f'×{opts["exaggeration"]:g}', ha='right', va='top', transform=ax.transAxes, fontproperties=tick_font)
                if opts['style'] == 'fill':
                    ax.fill_betweenx(data.y, 0, vals, where=np.isfinite(vals), color=color, linewidth=0)
                if opts['style'] == 'bar':
                    ax.hlines(data.y, 0, vals, colors=color, linewidth=style['line_width'])
                else:
                    ax.plot(vals, data.y, color=color, linewidth=style['line_width'], solid_joinstyle='round')
                    isolated = np.isfinite(vals) & ~np.r_[False, np.isfinite(vals[:-1])] & ~np.r_[np.isfinite(vals[1:]), False]
                    if np.any(isolated):
                        ax.scatter(vals[isolated], data.y[isolated], color=color, s=5, zorder=3)
                if not np.any(np.isfinite(vals)):
                    ax.text(.5, .5, '无有效值', transform=ax.transAxes, rotation=90, ha='center', va='center', fontproperties=tick_font)
                ax.set_xlim(0, upper)
                ax.set_ylim((high, low) if cfg['reverse_y'] else (low, high))
                labels.append(ax.text(0, 1.01, taxon.name, transform=ax.transAxes, rotation=style['angle'],
                    rotation_mode='anchor', ha='left', va='bottom', fontproperties=name_font, clip_on=False, parse_math=False))
                ax.xaxis.set_major_locator(FixedLocator([upper]) if compact else MultipleLocator(step))
                ax.xaxis.set_major_formatter(FuncFormatter(lambda v, pos, upper=upper: f'{v:g}' if 0 < v <= upper*(1+1e-9) else ''))
                ax.tick_params(axis='x', direction='out', length=2, width=cfg['spine_width'], pad=2,
                               labelsize=style['tick_size'], top=False)
                for tick in ax.get_xticklabels():
                    if compact:
                        tick.set_rotation(90)
                        tick.set_ha('center')
                    elif tick.get_text() == f'{upper:g}':
                        tick.set_ha('right')
                ax.tick_params(axis='y', direction='out', length=2.5 if index == 0 else 0,
                               width=cfg['spine_width'], pad=3, labelleft=index == 0, labelsize=style['tick_size'])
                for spine in ax.spines.values():
                    spine.set_linewidth(cfg['spine_width'])
                    spine.set_color('#333333')
                ax.spines['right'].set_visible(False)
                ax.spines['top'].set_visible(False)
                if index == 0:
                    ax.set_ylabel(f'{project.axis_kind}（{project.unit}）', fontproperties=axis_font, labelpad=7)
                    ax.yaxis.set_major_locator(FixedLocator(y_ticks))
                    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, pos: f'{0 if abs(v)<1e-12 else v:.{digits}f}'))
                ax.format_coord = lambda x, y: f'百分比：{x:g}%；纵轴：{y:g}'
                x_cm += panel_cm+(gaps[index] if index < len(gaps) else 0)
            bracket_cm = height-header_space
            group_y = bracket_cm/height
            group_boxes = []
            for group in GROUPS:
                members = [ax for t, ax in zip(taxa, axes) if t.group == group]
                if not members:
                    continue
                x0, x1 = members[0].get_position().x0, members[-1].get_position().x1
                fig.add_artist(Line2D([x0, x0, x1, x1], [group_y-.10/height, group_y, group_y, group_y-.10/height],
                               transform=fig.transFigure, color=cfg['group_colors'][group], linewidth=.6))
                text = fig.text((x0+x1)/2, group_y+.06/height, group, ha='center', va='bottom', fontproperties=group_font)
                text_width = renderer.get_text_width_height_descent(group, group_font, ismath=False)[0]*2.54/72/width
                bounds = ((x0+x1-text_width)/2, (x0+x1+text_width)/2)
                if group_boxes and bounds[0] < group_boxes[-1][1]+.05/width:
                    text.set_y(group_y+.38/height)
                group_boxes.append(bounds)
            if cfg['title']:
                fig.text(.5, 1-.15/height, cfg['title'], ha='center', va='top', fontproperties=axis_font)
            if len(pages)>1:
                fig.text(.985, 1-.15/height, f'{page_index+1}/{len(pages)}', ha='right', va='top', fontproperties=tick_font)
            fig.text((left+x_cm)/2/width, .25/height, '百分比（%）', ha='center', fontproperties=axis_font)
            extras = add_panels(fig,axes,overlay,width,height,bottom,plot_height,
                                low,high,cfg['reverse_y'],families,style['tick_size'],cfg['axis_size'],cfg['spine_width'])
            fig.canvas.draw()
            fig._pollen_axes = axes+list(extras.values())
            fig._pollen_limits = [(ax.get_xlim(), ax.get_ylim()) for ax in fig._pollen_axes]
            fig._pollen_size = tuple(fig.get_size_inches())
            layout = {'width_cm': width, 'height_cm': height, 'panel_height_cm': plot_height,
                      'panel_widths_cm': widths, 'width_mode': cfg['width_mode'],
                      'observed_maxima_percent': maxima, 'rare_threshold_percent': cfg['rare_threshold'],
                      'y_min': low, 'y_max': high,
                      'y_step': y_step, 'y_ticks': y_ticks.tolist(), 'name_size_pt': style['font_size'],
                      'name_angle': style['angle'], 'tick_size_pt': style['tick_size'],
                      'curve_width_pt': style['line_width']}
            layout['cluster_overlay'] = bool(overlay)
            layout['curve_region_width_cm'] = curve_width
            diagrams.append(Diagram(fig, axes, replace(data, taxa=taxa), limits, signature, page_index, len(pages), layout, extras))
    return diagrams
