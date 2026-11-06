"""Centered SVD PCA, independent of Qt; grouping never enters the decomposition."""
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import tempfile

import numpy as np

from .core import calculate, number, default_analysis_taxa, input_label
from . import clustering

VERSION = 1
PALETTE = ['#166B51','#60A66D','#4478B7','#E28D2B','#D2B62C','#876BB0','#BF5F7D','#548D99']
DEFAULTS = {
    'taxa_ids': None, 'excluded_sample_ids': [], 'transform': 'sqrt', 'standardize': False,
    'group_mode': 'zones', 'manual_groups': {}, 'group_colors': {},
    'pc_x': 0, 'pc_y': 1, 'flip_x': False, 'flip_y': False, 'layout': 'both',
    'arrow_mode': 'top', 'arrow_top': 20, 'arrow_ids': [], 'label_offsets': {},
    'width_cm': 18.3, 'height_cm': 9.5, 'font': 'Arial', 'font_size': 7.5,
    'tick_size': 7, 'axis_size': 8, 'line_width': .6, 'spine_width': .6,
    'point_size': 18, 'point_edge': .45, 'sample_labels': False,
    'italic': True, 'arrow_by_group': False, 'arrow_color': '#273747',
    'background': '#FFFFFF', 'title': '', 'panel_letters': True, 'dpi': 300,
    'arrow_x_min': None, 'arrow_x_max': None, 'arrow_y_min': None, 'arrow_y_max': None,
    'score_x_min': None, 'score_x_max': None, 'score_y_min': None, 'score_y_max': None,
}


def config_for(project):
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(copy.deepcopy(project.pca_config))
    if cfg['taxa_ids'] is None:
        cfg['taxa_ids'] = default_analysis_taxa(project)
    known = {t.id for t in project.taxa}
    cfg['taxa_ids'] = list(dict.fromkeys(tid for tid in cfg['taxa_ids'] if tid in known))
    samples = {s.id for s in project.samples}
    cfg['excluded_sample_ids'] = [sid for sid in cfg['excluded_sample_ids'] if sid in samples]
    return cfg


def input_signature(project, cfg=None):
    cfg = cfg or config_for(project)
    payload = {'version':VERSION,'taxa':[asdict(t) for t in project.taxa],
        'samples':[asdict(s) for s in project.samples], 'axis_kind':project.axis_kind,'unit':project.unit,
        'settings':{key:cfg[key] for key in ('taxa_ids','excluded_sample_ids','transform','standardize')}}
    if project.data_type!='counts':
        payload['data_type'] = project.data_type
    return clustering.digest(payload)


def prepare_matrix(project, cfg=None):
    cfg = cfg or config_for(project)
    if cfg['transform'] not in ('sqrt','none'):
        raise ValueError('未知 PCA 数据变换方式')
    taxa = [t for t in project.ordered_taxa() if t.id in set(cfg['taxa_ids'])]
    if len(taxa) < 2:
        raise ValueError('请至少选择两个参与 PCA 的类群')
    excluded = set(cfg['excluded_sample_ids'])
    indices = [i for i,s in enumerate(project.samples) if s.id not in excluded]
    if not 3 <= len(indices) <= 2000:
        raise ValueError('PCA 需要 3 至 2000 个参与分析的有效样品')
    results,issues = calculate(project)
    rows = {i+1 for i in indices}
    fields = {t.name for t in taxa} | {t.name for t in project.taxa if t.selected} | {'深度/年代'}
    for level,row,field,message in issues:
        if level == '错误' and (not isinstance(row,int) or (row in rows and field in fields)):
            raise ValueError(f'请核对原表第 {row} 行 / {field}：{message}')
    y = [float(number(project.samples[i].axis)) for i in indices]
    if not all(math.isfinite(v) for v in y):
        raise ValueError('样品深度／年代必须是有限数字')
    matrix = []
    for i in indices:
        values = [results[i]['percentages'][t.id] for t in taxa]
        if any(v is None or not math.isfinite(v) for v in values):
            raise ValueError(f'原表第 {i+1} 行百分比缺失或无效，请修正或明确排除样品／类群；空白不补零。')
        matrix.append(values)
    pct = np.array(matrix,dtype=float)
    transformed = np.sqrt(pct/100) if cfg['transform']=='sqrt' else pct/100
    deviations = transformed.std(axis=0,ddof=1)
    # A constant positive taxon and an all-zero taxon both carry no PCA variance.
    tolerance = np.finfo(float).eps*max(transformed.shape)*max(1,float(np.max(np.abs(transformed))))
    keep = deviations > tolerance
    dropped = [{'id':t.id,'name':t.name,'reason':'全零' if np.all(pct[:,j]==0) else '无有效变化'}
               for j,t in enumerate(taxa) if not keep[j]]
    taxa = [t for j,t in enumerate(taxa) if keep[j]]
    if len(taxa) < 2:
        raise ValueError('去除全零或无变化类群后不足两个有效类群，无法绘制二维 PCA')
    pct,transformed,deviations = pct[:,keep],transformed[:,keep],deviations[keep]
    means = transformed.mean(axis=0)
    scales = deviations if cfg['standardize'] else np.ones(len(taxa))
    centered = (transformed-means)/scales
    return {'matrix':centered,'percentages':pct,'transformed':transformed,'means':means,'scales':scales,
        'indices':indices,'y':y,'taxa':taxa,'dropped':dropped,
        'sample_ids':[project.samples[i].id for i in indices], 'bases':[results[i]['base'] for i in indices]}


def decompose(matrix):
    x = np.asarray(matrix,dtype=float)
    if x.ndim != 2 or min(x.shape) < 2 or not np.all(np.isfinite(x)):
        raise ValueError('PCA 输入矩阵无效')
    u,s,vt = np.linalg.svd(x,full_matrices=False)
    tolerance = max(x.shape)*np.finfo(float).eps*s[0]
    rank = int(np.sum(s>tolerance))
    if rank < 2:
        raise ValueError('数据实际只有零个或一个非零主成分，无法绘制二维 PCA；请检查样品与类群变化。')
    eigenvalues = s*s/(len(x)-1)
    ratio = eigenvalues[:rank]/eigenvalues.sum()
    loadings = vt[:rank].T.copy()
    scores = u[:,:rank]*s[:rank]
    # Fix the arbitrary SVD signs for reproducible project reopening/export.
    for j in range(rank):
        if loadings[np.argmax(np.abs(loadings[:,j])),j] < 0:
            scores[:,j] *= -1
            loadings[:,j] *= -1
    correlations = loadings*np.sqrt(eigenvalues[:rank])[None,:]/x.std(axis=0,ddof=1)[:,None]
    correlations = np.clip(correlations,-1,1)
    return {'rank':rank,'scores':scores.tolist(),'loadings':loadings.tolist(),
        'correlations':correlations.tolist(),'eigenvalues':eigenvalues[:rank].tolist(),
        'explained_ratio':ratio.tolist()}


def run_analysis(project, cfg=None, cancelled=None):
    cfg = cfg or config_for(project)
    inputs = prepare_matrix(project,cfg)
    if cancelled and cancelled():
        raise ValueError('PCA 分析已取消')
    result = decompose(inputs['matrix'])
    if cancelled and cancelled():
        raise ValueError('PCA 分析已取消')
    result.update(version=VERSION,input_signature=input_signature(project,cfg),
        sample_ids=inputs['sample_ids'],sample_rows=[i+1 for i in inputs['indices']], y=inputs['y'],bases=inputs['bases'],
        taxa_ids=[t.id for t in inputs['taxa']], taxa_names=[t.name for t in inputs['taxa']],
        means=inputs['means'].tolist(), scales=inputs['scales'].tolist(), dropped=inputs['dropped'],
        computed_at=datetime.now(timezone.utc).isoformat())
    result['result_hash'] = clustering.digest(result)
    return result


def result_is_current(project):
    r = project.pca_result
    if not r or r.get('version') != VERSION:
        return False
    try:
        return (r['input_signature']==input_signature(project) and
            r['result_hash']==clustering.digest({k:v for k,v in r.items() if k!='result_hash'}))
    except (ValueError,TypeError,KeyError):
        return False


def require_result(project):
    if not result_is_current(project):
        raise ValueError('PCA 结果缺失或已失效，请先重新计算 PCA')
    return project.pca_result


def grouping(project):
    r,cfg = require_result(project),config_for(project)
    keys,labels,warnings = [],{},[]
    if cfg['group_mode']=='zones':
        if not project.cluster_result:
            warnings.append('尚无组合带结果，暂按未分组显示；完成分带后更新图形即可。')
            keys = ['ungrouped']*len(r['sample_ids'])
            labels['ungrouped'] = '未分组'
        else:
            if not clustering.result_is_current(project):
                raise ValueError('组合带结果已失效，请重新计算分带，或将 PCA 样品分组改为“不分组／自定义”。PCA 数值结果仍保留。')
            z = clustering.zoning(project)
            lookup = dict(zip(project.cluster_result['sample_ids'],z['zone_ids']))
            for zone in z['zones']:
                labels['zone:'+str(zone['id'])] = zone['label']+(' '+zone['name'] if zone['name'] else '')
            labels['ungrouped'] = '未分带'
            keys = ['zone:'+str(lookup[sid]) if sid in lookup else 'ungrouped' for sid in r['sample_ids']]
            if 'ungrouped' in keys:
                warnings.append('未参与现有聚类的 PCA 样品单独标为“未分带”。')
    elif cfg['group_mode']=='manual':
        for sid in r['sample_ids']:
            name = str(cfg['manual_groups'].get(sid,'')).strip()
            key = 'manual:'+name if name else 'ungrouped'
            keys.append(key)
            labels[key] = name or '未分组'
    elif cfg['group_mode']=='none':
        keys = ['ungrouped']*len(r['sample_ids'])
        labels['ungrouped'] = '全部样品'
    else:
        raise ValueError('未知 PCA 样品分组方式')
    ordered = [key for key in labels if key in set(keys)]
    if cfg['group_mode']=='manual':
        ordered = list(dict.fromkeys(keys))
    colors = {key:cfg['group_colors'].get(key,'#6B7C85' if key=='ungrouped' else PALETTE[i%len(PALETTE)])
              for i,key in enumerate(ordered)}
    return {'keys':keys,'labels':{k:labels[k] for k in ordered},'colors':colors,'warnings':warnings}


def arrow_indices(project):
    r,cfg = require_result(project),config_for(project)
    if cfg['arrow_mode']=='manual':
        return [i for i,tid in enumerate(r['taxa_ids']) if tid in set(cfg['arrow_ids'])]
    if cfg['arrow_mode']=='all':
        return list(range(len(r['taxa_ids'])))
    if cfg['arrow_mode']!='top':
        raise ValueError('未知类群箭头显示方式')
    xy = np.array(r['correlations'])[:,[cfg['pc_x'],cfg['pc_y']]]
    return np.argsort(-np.sum(xy*xy,axis=1),kind='stable')[:int(cfg['arrow_top'])].tolist()


def export_analysis(project,path):
    from openpyxl import Workbook
    from .excel import style_sheet,put
    r,cfg,g = require_result(project),config_for(project),grouping(project)
    inputs = prepare_matrix(project,cfg)
    pcs = [f'PC{i+1}' for i in range(r['rank'])]
    cumulative = np.cumsum(r['explained_ratio'])*100
    tables = {'解释率':[['主成分','特征值','解释率（%）','累计解释率（%）']]+[
        [pc,r['eigenvalues'][j],r['explained_ratio'][j]*100,float(cumulative[j])] for j,pc in enumerate(pcs)]}
    tables['样品得分'] = [['原表数据行','深度/年代','分组']+pcs]
    tables['样品得分'] += [[r['sample_rows'][i],r['y'][i],g['labels'][g['keys'][i]]]+row for i,row in enumerate(r['scores'])]
    tables['类群载荷'] = [['类群']+pcs]+[[name]+row for name,row in zip(r['taxa_names'],r['loadings'])]
    tables['类群相关系数'] = [['类群']+pcs]+[[name]+row for name,row in zip(r['taxa_names'],r['correlations'])]
    ids = set(cfg['taxa_ids'])
    dropped = {t['id']:t['reason'] for t in r['dropped']}
    tables['类群设置'] = [['类群','分组','进入百分比基数','PCA 状态']]+[
        [t.name,t.group,'不适用（已有百分比）' if project.data_type=='percentages' else ('是' if t.selected else '否'),dropped.get(t.id,'参与分析') if t.id in ids else '未选择'] for t in project.taxa]
    tables['分析百分比'] = [['原表数据行','深度/年代']+r['taxa_names']]+[
        [r['sample_rows'][i],r['y'][i]]+row.tolist() for i,row in enumerate(inputs['percentages'])]
    tables['变换后输入'] = [['原表数据行','深度/年代']+r['taxa_names']]+[
        [r['sample_rows'][i],r['y'][i]]+row.tolist() for i,row in enumerate(inputs['matrix'])]
    included = set(r['sample_ids'])
    tables['样品设置'] = [['原表数据行','深度/年代','PCA 状态','自定义分组']]+[
        [i+1,s.axis,'参与分析' if s.id in included else '明确排除',cfg['manual_groups'].get(s.id,'')] for i,s in enumerate(project.samples)]
    from . import __version__
    tables['分析说明'] = [['项目','内容'],['软件版本',__version__],['输入数据类型',input_label(project)],['计算方法','按类群中心化后进行奇异值分解（SVD）'],
        ['数据变换','平方根：√（百分比/100）' if cfg['transform']=='sqrt' else '比例：百分比/100'],
        ['单位方差标准化','是（样本标准差，分母 n−1）' if cfg['standardize'] else '否'],
        ['百分比口径','直接采用导入百分比，不重新计算或归一化；原始计数及基数未知' if project.data_type=='percentages' else '沿用原百分比基数，选择分析类群后不重新归一化'],
        ['图中类群坐标','类群与主成分得分的相关系数；原始载荷另表提供'],
        ['分组方式',{'zones':'现有组合带','manual':'自定义','none':'不分组'}[cfg['group_mode']]],
        ['样品分组','仅用于着色，不参与 PCA 分解'],['椭圆','不绘制'],
        ['主成分方向','最大绝对载荷为正；图中可同步翻转方向，数值表保留原方向'],
        ['展示主成分',f'PC{cfg["pc_x"]+1}、PC{cfg["pc_y"]+1}'],
        ['横向翻转','是' if cfg['flip_x'] else '否'],['纵向翻转','是' if cfg['flip_y'] else '否'],
        ['坐标类型',project.axis_kind],['坐标单位',project.unit],['计算时间',r['computed_at']],
        ['输入指纹',r['input_signature']],['提示','；'.join(g['warnings'])]]
    wb = Workbook()
    wb.remove(wb.active)
    for name,rows in tables.items():
        ws = wb.create_sheet(name)
        for i,row in enumerate(rows,1):
            for j,value in enumerate(row,1):
                put(ws,i,j,value)
        style_sheet(ws)
    target = Path(path)
    with tempfile.NamedTemporaryFile(dir=target.parent,suffix='.xlsx',delete=False) as f:
        temp = Path(f.name)
    try:
        wb.save(temp)
        os.replace(temp,target)
    finally:
        wb.close()
        if temp.exists():
            temp.unlink()
