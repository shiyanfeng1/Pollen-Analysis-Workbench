"""Shared real-coordinate tree/zone panels; the pollen region retains its width."""
from matplotlib.ticker import MaxNLocator, FixedLocator
from . import clustering as analysis


def overlay_for(project):
    cfg = analysis.config_for(project)
    if not project.cluster_result or not any(cfg[k] for k in ('show_tree','show_zones','show_lines')):
        return None
    result = analysis.require_result(project)
    zones = analysis.zoning(project)
    for key,low,high in [('tree_width_cm',1.5,10),('zone_width_cm',.5,5),('line_width',.1,3)]:
        if not low <= float(cfg[key]) <= high:
            raise ValueError('聚类树或组合带的显示尺寸设置无效')
    from matplotlib.colors import is_color_like
    if not is_color_like(cfg['line_color']) or not is_color_like(cfg['tree_color']):
        raise ValueError('分带线或聚类树颜色无效')
    if cfg['line_style'] not in ('-','--',':','-.'):
        raise ValueError('分带线型无效')
    width = (.12 if cfg['show_tree'] or cfg['show_zones'] else 0)
    width += cfg['zone_width_cm'] if cfg['show_zones'] else 0
    width += cfg['tree_width_cm'] if cfg['show_tree'] else 0
    width += .3 if cfg['show_tree'] and cfg['show_zones'] else 0
    width += .45 if cfg['show_tree'] or cfg['show_zones'] else 0
    return {'config':cfg,'result':result,'zones':zones,'extra_width_cm':width}


def add_panels(fig, pollen_axes, overlay, width, height, bottom, plot_height,
               low, high, reverse, families, tick_size, axis_size, spine_width):
    if overlay is None:
        return {}
    cfg,result,z = overlay['config'],overlay['result'],overlay['zones']
    extras = {}
    x = pollen_axes[-1].get_position().x1*width+.12
    kwargs = {'fontfamily':families,'fontsize':axis_size}
    if cfg['show_zones']:
        ax = fig.add_axes([x/width,bottom/height,cfg['zone_width_cm']/width,plot_height/height],sharey=pollen_axes[0])
        extras['zones'] = ax
        ax.set_xlim(0,1)
        ax.set_ylim((high,low) if reverse else (low,high))
        ax.set_xticks([])
        ax.tick_params(axis='y',length=0,labelleft=False)
        ax.set_title('组合带\n（人工）' if z['mode']=='manual' else '组合带',pad=5,**kwargs)
        for zone in z['zones']:
            lo = max(low,zone['lower'] if zone['lower'] is not None else low)
            hi = min(high,zone['upper'] if zone['upper'] is not None else high)
            if hi <= lo:
                continue
            available_points = (hi-lo)/(high-low)*plot_height/2.54*72
            if available_points < tick_size*1.1:
                continue
            text = zone['label']
            if zone['name'] and available_points > axis_size*3:
                name = zone['name']
                wrap = max(2,int(cfg['zone_width_cm']/2.54*72/tick_size))
                name = '\n'.join(name[i:i+wrap] for i in range(0,len(name),wrap))
                text += '\n'+name
            ax.text(.5,(lo+hi)/2,text,ha='center',va='center',clip_on=True,
                    fontfamily=families,fontsize=tick_size)
        x += cfg['zone_width_cm']+.3
    if cfg['show_tree']:
        ax = fig.add_axes([x/width,bottom/height,cfg['tree_width_cm']/width,plot_height/height],sharey=pollen_axes[0])
        extras['tree'] = ax
        ax.set_ylim((high,low) if reverse else (low,high))
        n = len(result['y'])
        positions = {i:(0,float(y)) for i,y in enumerate(result['y'])}
        for step,merge in enumerate(result['merges']):
            xl,yl = positions[merge['left']]
            xr,yr = positions[merge['right']]
            h = merge['height']
            ax.plot([xl,h,h,xr],[yl,yl,yr,yr],color=cfg['tree_color'],linewidth=spine_width)
            positions[n+step] = (h,(yl+yr)/2)
        root_height = result['merges'][-1]['height']
        ax.set_xlim(0,root_height*1.06 if root_height > 0 else .01)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=3) if root_height > 0 else FixedLocator([0]))
        ax.tick_params(axis='x',labelsize=tick_size,length=2,width=spine_width)
        ax.tick_params(axis='y',length=0,labelleft=False)
        ax.set_title('CONISS',pad=5,**kwargs)
        ax.set_xlabel('累计总离差',labelpad=4,fontfamily=families,fontsize=tick_size)
        ax.spines['right'].set_visible(False)
        ax.spines['top'].set_visible(False)
        ax._pollen_tree_leaf_y = list(result['y'])
        ax._pollen_tree_node_positions = positions
    for ax in extras.values():
        for spine in ax.spines.values():
            spine.set_color('#333333')
            spine.set_linewidth(spine_width)
    if cfg['show_lines']:
        boundary_axes = list(pollen_axes)
        if 'zones' in extras:
            boundary_axes.append(extras['zones'])
        for ax in boundary_axes:
            for boundary in z['boundaries']:
                if low <= boundary <= high:
                    line = ax.axhline(boundary,color=cfg['line_color'],linestyle=cfg['line_style'],
                                     linewidth=cfg['line_width'],zorder=5)
                    line.set_gid('pollen-zone-boundary')
    if cfg['show_zones'] and not cfg['show_lines']:
        for boundary in z['boundaries']:
            extras['zones'].axhline(boundary,color=cfg['line_color'],linewidth=cfg['line_width'])
    return extras
