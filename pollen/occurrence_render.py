"""Single-page, real-coordinate occurrence diagrams with equal categorical columns."""
from dataclasses import dataclass,field
import math
import numpy as np
import matplotlib
from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg,RendererAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator,FuncFormatter

from . import occurrence,clustering
from .plotting import setup_fonts,fingerprint,config_for as pollen_config
from .render import fixed_y_scale


@dataclass
class OccurrenceDiagram:
    figure: object
    axes: list
    signature: str
    data: object
    rows: list
    layout: dict = field(default_factory=dict)


def scatter_symbols(ax,x,y,marker,color,size):
    code = occurrence.MARKERS[marker][1]
    kwargs = {'s':size*size,'marker':code,'zorder':3,'clip_on':False}
    if marker in ('horizontal','vertical'):
        kwargs.update(color=color,linewidths=.65)
    elif marker=='open_circle':
        kwargs.update(facecolors='none',edgecolors=color,linewidths=.55)
    else:
        kwargs.update(c=color,linewidths=0)
    return ax.scatter(x,y,**kwargs)


def build_figure(project):
    setup_fonts()
    data = occurrence.prepare(project)
    cfg = data.config
    rows = occurrence.partitions(data.taxa,cfg)
    low,high,step,ticks,digits = fixed_y_scale(data)
    families = list(dict.fromkeys([cfg['font'],'Microsoft YaHei','DejaVu Sans']))
    name_font = font_manager.FontProperties(family=families,size=cfg['font_size'])
    tick_font = font_manager.FontProperties(family=families,size=cfg['tick_size'])
    axis_font = font_manager.FontProperties(family=families,size=cfg['axis_size'])
    renderer = RendererAgg(100,100,72)
    angle = math.radians(cfg['angle'])
    extents = {}
    for t in data.taxa:
        w,h,_ = renderer.get_text_width_height_descent(t.name,name_font,ismath=False)
        h = max(h,cfg['font_size']*1.1)
        extents[t.id] = ((w*math.cos(angle)+h*math.sin(angle))*2.54/72,
                        (w*math.sin(angle)+h*math.cos(angle))*2.54/72)
    left = 1.45
    right = max(.55,max(extents[row[-1].id][0] for row in rows)+.12)
    plot_width = cfg['width_cm']-left-right
    minimum_pitch = cfg['font_size']*1.1*2.54/72/max(math.sin(angle),.1)
    if plot_width/max(map(len,rows)) < minimum_pitch:
        raise ValueError('类群名称间距不足，请加宽图幅、减小字号、增大名称角度或降低每排类群上限')
    headers = [max(extents[t.id][1] for t in row)+.8 for row in rows]
    footer = 1.05
    top = .8 if cfg['title'] else .5
    gap = cfg['panel_gap_cm']
    body_height = cfg['panel_height_cm']
    height = top+sum(headers)+len(rows)*body_height+(len(rows)-1)*gap+footer
    if cfg['height_cm'] is not None:
        height = cfg['height_cm']
        body_height = (height-top-sum(headers)-(len(rows)-1)*gap-footer)/len(rows)
        if body_height<3:
            raise ValueError('总图高不足以容纳名称与各排绘图区，请增加总图高或恢复自动图高')
    width = cfg['width_cm']
    palette = pollen_config(project)['group_colors']
    boundaries = clustering.active_boundaries(project) if cfg['show_boundaries'] else []
    indices = {t.id:j for j,t in enumerate(data.taxa)}
    visible_samples = (data.y>=low)&(data.y<=high)
    axes,spans = [],[]
    with matplotlib.rc_context({'font.family':families,'font.size':cfg['tick_size'],
        'axes.unicode_minus':False,'text.parse_math':False,'pdf.fonttype':42,'svg.fonttype':'none'}):
        fig = Figure(figsize=(width/2.54,height/2.54),dpi=100,facecolor=cfg['background'])
        FigureCanvasAgg(fig)
        cursor = height-top
        for row_index,(row,header) in enumerate(zip(rows,headers)):
            body_top = cursor-header
            bottom = body_top-body_height
            ax = fig.add_axes([left/width,bottom/height,plot_width/width,body_height/height],facecolor=cfg['background'])
            axes.append(ax)
            ax.set_xlim(-.5,len(row)-.5)
            ax.set_ylim((high,low) if cfg['reverse_y'] else (low,high))
            ax.xaxis.tick_top()
            ax.set_xticks(np.arange(len(row)))
            texts = ax.set_xticklabels([t.name for t in row],rotation=cfg['angle'],ha='left',va='bottom',
                rotation_mode='anchor',fontfamily=families,fontsize=cfg['font_size'])
            for text,t in zip(texts,row):
                if cfg['italic'] and all(ord(c)<128 for c in t.name):
                    text.set_fontstyle('italic')
            ax.tick_params(axis='x',length=2,pad=3,width=cfg['spine_width'])
            ax.yaxis.set_major_locator(FixedLocator(ticks))
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v,pos:f'{0 if abs(v)<1e-12 else v:.{digits}f}'))
            ax.tick_params(axis='y',labelsize=cfg['tick_size'],length=3,width=cfg['spine_width'])
            ax.set_ylabel(f'{project.axis_kind}（{project.unit}）',fontproperties=axis_font,labelpad=5)
            for spine in ax.spines.values():
                spine.set_color('#333333')
                spine.set_linewidth(cfg['spine_width'])
            for x,t in enumerate(row):
                j = indices[t.id]
                found = (data.states[:,j]==1)&visible_samples
                missing = (data.states[:,j]<0)&visible_samples
                marker = cfg['group_markers'].get(t.group,cfg['marker']) if cfg['marker_by_group'] else cfg['marker']
                color = palette[t.group] if cfg['color_by_group'] else cfg['point_color']
                if np.any(found):
                    scatter_symbols(ax,np.full(np.sum(found),x),data.y[found],marker,color,cfg['marker_size'])
                if np.any(missing):
                    ax.scatter(np.full(np.sum(missing),x),data.y[missing],s=cfg['missing_size']**2,
                        marker='x',color=cfg['missing_color'],linewidths=.55,zorder=3,clip_on=False)
            for boundary in boundaries:
                if low<=boundary<=high:
                    line = ax.axhline(boundary,color=cfg['boundary_color'],linewidth=cfg['boundary_width'],zorder=2)
                    line.set_gid('occurrence-zone-boundary')
            group_y = (cursor-.45)/height
            group_boxes = []
            first = 0
            while first<len(row):
                end = first+1
                group = row[first].group
                while end<len(row) and row[end].group==group:
                    end += 1
                x0 = (left+plot_width*first/len(row)+.04)/width
                x1 = (left+plot_width*end/len(row)-.04)/width
                fig.add_artist(Line2D([x0,x0+.07/width,x1,x1-.07/width],
                    [group_y-.16/height,group_y,group_y,group_y-.16/height],
                    transform=fig.transFigure,color='#333333',linewidth=cfg['group_line_width']))
                continued = row_index>0 and first==0 and rows[row_index-1][-1].group==group
                label = group+('（续）' if continued else '')
                label_width = renderer.get_text_width_height_descent(label,axis_font,ismath=False)[0]*2.54/72/width
                bounds = ((x0+x1-label_width)/2,(x0+x1+label_width)/2)
                offset = .06
                if group_boxes and bounds[0]<group_boxes[-1][1]+.05/width:
                    offset = .34
                fig.text((x0+x1)/2,group_y+offset/height,label,ha='center',va='bottom',fontproperties=axis_font)
                group_boxes.append(bounds)
                first = end
            if cfg['letters']:
                label = chr(97+row_index) if row_index<26 else str(row_index+1)
                fig.text(.25/width,(cursor-.1)/height,label,ha='left',va='top',fontsize=10,fontfamily=families,fontweight='bold')
            spans.append((bottom,body_top))
            cursor = bottom-gap
        if cfg['title']:
            fig.text(.5,1-.12/height,cfg['title'],ha='center',va='top',fontproperties=axis_font)
        handles = []
        if cfg['marker_by_group'] or cfg['color_by_group']:
            groups = list(dict.fromkeys(t.group for t in data.taxa))
            for group in groups:
                marker = cfg['group_markers'].get(group,cfg['marker']) if cfg['marker_by_group'] else cfg['marker']
                color = palette[group] if cfg['color_by_group'] else cfg['point_color']
                handles.append(Line2D([],[],marker=occurrence.MARKERS[marker][1],linestyle='none',
                    markerfacecolor='none' if marker=='open_circle' else color,markeredgecolor=color,
                    markeredgewidth=.55,markersize=cfg['marker_size'],label=group+'检出'))
        else:
            marker = cfg['marker']
            handles.append(Line2D([],[],marker=occurrence.MARKERS[marker][1],linestyle='none',
                markerfacecolor='none' if marker=='open_circle' else cfg['point_color'],markeredgecolor=cfg['point_color'],
                markersize=cfg['marker_size'],markeredgewidth=.55,label='检出'))
        handles.append(Line2D([],[],marker='x',color=cfg['missing_color'],linestyle='none',markersize=cfg['missing_size'],
            markeredgewidth=.55,label='缺失／无法判定'))
        handles.append(Line2D([],[],linestyle='none',label='空白：未检出'))
        legend = fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.5,.1/height),ncol=min(4,len(handles)),
            frameon=False,fontsize=cfg['tick_size'],handletextpad=.3,columnspacing=.8,borderaxespad=0)
        for text in legend.get_texts():
            text.set_fontfamily(families)
        fig.canvas.draw()
        fig._pollen_axes = axes
        fig._pollen_limits = [(ax.get_xlim(),ax.get_ylim()) for ax in axes]
        fig._pollen_size = tuple(fig.get_size_inches())
        fig._pollen_facecolor = cfg['background']
    return OccurrenceDiagram(fig,axes,fingerprint(project),data,rows,
        {'row_sizes':[len(row) for row in rows],'width_cm':width,'height_cm':height,'y_min':low,'y_max':high,
         'y_step':step,'y_ticks':ticks.tolist(),'body_height_cm':body_height,'spans_cm':spans,
         'found':int(np.sum(data.states==1)),'missing':int(np.sum(data.states<0))})
