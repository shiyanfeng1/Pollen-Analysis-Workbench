"""Publication PCA panels: correlation arrows and sample scores, without ellipses."""
from dataclasses import dataclass, field
import math

import numpy as np
import matplotlib
from matplotlib import font_manager, colors
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from matplotlib.transforms import Bbox

from . import pca
from .plotting import setup_fonts, fingerprint, GROUP_COLORS, numeric, config_for as pollen_config


@dataclass
class PcaDiagram:
    figure: object
    axes: list
    signature: str
    labels: list = field(default_factory=list)
    score_points: object = None
    layout: dict = field(default_factory=dict)


def validate(cfg, result):
    for key,label,low,high in [('width_cm','图宽',10,40),('height_cm','图高',6,30),
        ('font_size','名称字号',5,18),('tick_size','刻度字号',5,16),('axis_size','坐标标题字号',5,18),
        ('line_width','箭头线宽',.2,3),('spine_width','坐标线宽',.2,2),
        ('point_size','样品点面积',3,150),('point_edge','样品点轮廓线宽',0,2),('dpi','分辨率',72,600)]:
        numeric(cfg[key],label,low,high)
    if cfg['layout'] not in ('both','arrows','scores'):
        raise ValueError('未知 PCA 图形布局')
    axes = [cfg['pc_x'],cfg['pc_y']]
    if any(not isinstance(i,int) or not 0 <= i < result['rank'] for i in axes) or axes[0]==axes[1]:
        raise ValueError('请选择两个不同且有效的主成分')
    if not isinstance(cfg['arrow_top'],int) or not 1 <= cfg['arrow_top'] <= 200:
        raise ValueError('箭头显示数量应为 1 至 200')
    for color in [cfg['arrow_color'],cfg['background']]+list(cfg['group_colors'].values()):
        if not colors.is_color_like(color):
            raise ValueError('PCA 图形颜色无效')
    for prefix in ('arrow','score'):
        for axis in ('x','y'):
            lo,hi = cfg[f'{prefix}_{axis}_min'],cfg[f'{prefix}_{axis}_max']
            numeric(lo,'坐标最小值',optional=True)
            numeric(hi,'坐标最大值',optional=True)
            if lo is not None and hi is not None and hi <= lo:
                raise ValueError('坐标最大值必须大于最小值')


def avoid_labels(fig, ax, records, offsets, signs):
    """Greedy physical text-box placement; user positions override automatic choices."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    bounds = ax.get_window_extent(renderer).padded(-4)
    occupied = []
    ordered = sorted(records,key=lambda item:len(item[0].get_text()),reverse=True)
    for text,tip,tid in ordered:
        best,score_best = None,float('inf')
        if tid in offsets:
            off = np.array(offsets[tid],dtype=float)
            if off.shape != (2,) or not np.all(np.isfinite(off)):
                raise ValueError('类群标签位置无效，请重置标签位置')
            text.set_position(tip+off*signs)
            occupied.append(text.get_window_extent(renderer).padded(2))
            continue
        direction = np.arctan2(tip[1],tip[0])
        for radius in (.045,.09,.15,.23,.33,.44):
            for turn in (0,.45,-.45,.9,-.9,1.5,-1.5,math.pi):
                angle = direction+turn
                delta = np.array([math.cos(angle),math.sin(angle)])*radius
                pos = tip+delta
                text.set_position(pos)
                text.set_ha('left' if delta[0]>=0 else 'right')
                text.set_va('bottom' if delta[1]>=0 else 'top')
                box = text.get_window_extent(renderer).padded(2)
                outside = max(0,bounds.x0-box.x0)+max(0,box.x1-bounds.x1)+max(0,bounds.y0-box.y0)+max(0,box.y1-bounds.y1)
                overlap = 0
                for other in occupied:
                    intersection = Bbox.intersection(box,other)
                    if intersection:
                        overlap += intersection.width*intersection.height
                score = overlap*5+outside*200+radius*10+abs(turn)
                if score < score_best:
                    best = (pos.copy(),text.get_ha(),text.get_va(),box)
                    score_best = score
        pos,ha,va,box = best
        text.set_position(pos)
        text.set_ha(ha)
        text.set_va(va)
        occupied.append(box)
    # Leader lines connect displaced text with its actual correlation-vector tip.
    for text,tip,tid in records:
        position = np.array(text.get_position())
        if np.linalg.norm(position-tip)>.11:
            ax.plot([tip[0],position[0]],[tip[1],position[1]],color='#9DA6AD',linewidth=.3,zorder=1,clip_on=True)
        text._pca_taxon_id = tid
        text._pca_tip = tip.copy()


def build_figure(project):
    setup_fonts()
    r,cfg = pca.require_result(project),pca.config_for(project)
    validate(cfg,r)
    groups = pca.grouping(project)
    signs = np.array([-1 if cfg['flip_x'] else 1,-1 if cfg['flip_y'] else 1])
    components = [cfg['pc_x'],cfg['pc_y']]
    correlations = np.array(r['correlations'])[:,components]*signs
    scores = np.array(r['scores'])[:,components]*signs
    families = list(dict.fromkeys([cfg['font'],'Microsoft YaHei','DejaVu Sans']))
    font = {'fontfamily':families,'fontsize':cfg['font_size']}
    axis_font = {'fontfamily':families,'fontsize':cfg['axis_size']}
    which = ['arrows','scores'] if cfg['layout']=='both' else [cfg['layout']]
    width,height = cfg['width_cm'],cfg['height_cm']
    legend_rows = math.ceil(len(groups['labels'])/4) if 'scores' in which else 0
    bottom = 1.15+.32*legend_rows
    top = .85 if cfg['title'] else .65
    side = min((width-1.85-1.25*(len(which)-1))/len(which),height-bottom-top)
    if side < 3.5:
        raise ValueError('图幅不足以容纳 PCA 图与分组图例，请增加图高或减少自定义分组数量')
    positions = [1.4] if len(which)==1 else [1.4,width-side-.4]
    axes,records = [],[]
    with matplotlib.rc_context({'font.family':families,'font.size':cfg['tick_size'],
         'axes.unicode_minus':False,'text.parse_math':False,'pdf.fonttype':42,'svg.fonttype':'none'}):
        fig = Figure(figsize=(width/2.54,height/2.54),dpi=100,facecolor=cfg['background'])
        FigureCanvasAgg(fig)
        for index,kind in enumerate(which):
            ax = fig.add_axes([positions[index]/width,bottom/height,side/width,side/height],facecolor=cfg['background'])
            axes.append(ax)
            ax._pca_kind = kind
            ax.axhline(0,color='#A3ADB3',linestyle=':',linewidth=.45,zorder=0)
            ax.axvline(0,color='#A3ADB3',linestyle=':',linewidth=.45,zorder=0)
            for spine in ax.spines.values():
                spine.set_linewidth(cfg['spine_width'])
                spine.set_color('#333333')
            ax.tick_params(labelsize=cfg['tick_size'],length=2.5,width=cfg['spine_width'])
            ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
            ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
            ax.set_aspect('equal',adjustable='box')
            ax.set_xlabel(f'PC{components[0]+1}（{r["explained_ratio"][components[0]]*100:.1f}%）',labelpad=4,**axis_font)
            ax.set_ylabel(f'PC{components[1]+1}（{r["explained_ratio"][components[1]]*100:.1f}%）',labelpad=4,**axis_font)
            if cfg['panel_letters']:
                ax.text(.97,.035,'a' if kind=='arrows' else 'b',transform=ax.transAxes,ha='right',fontsize=11,fontfamily=families,zorder=10)
            if kind=='arrows':
                ax.set_title('孢粉类群相关载荷',pad=6,**axis_font)
                ax.set_xlim(-1.28,1.28)
                ax.set_ylim(-1.28,1.28)
                taxa = {t.id:t for t in project.taxa}
                palette = pollen_config(project)['group_colors']
                for i in pca.arrow_indices(project):
                    tid,name,tip = r['taxa_ids'][i],r['taxa_names'][i],correlations[i]
                    color = palette.get(taxa[tid].group,GROUP_COLORS[taxa[tid].group]) if cfg['arrow_by_group'] else cfg['arrow_color']
                    ax.annotate('',xy=tip,xytext=(0,0),arrowprops={'arrowstyle':'-|>','color':color,
                        'lw':cfg['line_width'],'mutation_scale':7,'shrinkA':0,'shrinkB':0},zorder=2)
                    italic = cfg['italic'] and all(ord(c)<128 for c in name)
                    text = ax.text(tip[0],tip[1],name,color=color,fontstyle='italic' if italic else 'normal',clip_on=False,zorder=4,**font)
                    records.append((text,tip,tid))
            else:
                ax.set_title('样品分布',pad=6,**axis_font)
                limit = max(float(np.max(np.abs(scores)))*1.18,.01)
                ax.set_xlim(-limit,limit)
                ax.set_ylim(-limit,limit)
                for key,label in groups['labels'].items():
                    mask = np.array([v==key for v in groups['keys']])
                    ax.scatter(scores[mask,0],scores[mask,1],s=cfg['point_size'],c=groups['colors'][key],
                        edgecolors='#283738',linewidths=cfg['point_edge'],label=label,zorder=3)
                if cfg['sample_labels']:
                    for xy,y in zip(scores,r['y']):
                        ax.annotate(f'{y:g}',xy=xy,xytext=(3,3),textcoords='offset points',fontsize=cfg['font_size'],fontfamily=families)
                legend = ax.legend(loc='lower center',bbox_to_anchor=((positions[index]+side/2)/width,.02),
                    bbox_transform=fig.transFigure,ncol=min(4,len(groups['labels'])),
                    fontsize=cfg['tick_size'],frameon=False,handletextpad=.25,columnspacing=.8,borderaxespad=0)
                for text in legend.get_texts():
                    text.set_fontfamily(families)
            prefix = 'arrow' if kind=='arrows' else 'score'
            ax.set_xlim(left=cfg[prefix+'_x_min'],right=cfg[prefix+'_x_max'])
            ax.set_ylim(bottom=cfg[prefix+'_y_min'],top=cfg[prefix+'_y_max'])
            if ax.get_xlim()[1]<=ax.get_xlim()[0] or ax.get_ylim()[1]<=ax.get_ylim()[0]:
                raise ValueError('自动范围与手动坐标不相容，请填写成对的最小值和最大值，或恢复自动范围')
        if cfg['title']:
            fig.text(.5,1-.15/height,cfg['title'],ha='center',va='top',**axis_font)
        if records:
            avoid_labels(fig,next(ax for ax in axes if ax._pca_kind=='arrows'),records,cfg['label_offsets'],signs)
        fig.canvas.draw()
        fig._pollen_axes = axes
        fig._pollen_limits = [(ax.get_xlim(),ax.get_ylim()) for ax in axes]
        fig._pollen_size = tuple(fig.get_size_inches())
        # Reused scientific exporter reads this face colour without altering other plots.
        fig._pollen_facecolor = cfg['background']
    return PcaDiagram(fig,axes,fingerprint(project),[t for t,_,_ in records],scores,
        {'groups':groups,'components':components,'signs':signs.tolist(),'arrow_count':len(records),'ellipses':0})
