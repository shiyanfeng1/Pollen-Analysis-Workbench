"""Order-constrained incremental dispersion, independent of Qt and plotting.

Default distance convention matches rioja::chclust(dist(sqrt(pct/100))).
Squared-Euclidean mode instead measures SSE in the transformed data space.
"""
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np

from .core import calculate, number, default_analysis_taxa, input_label

ALGORITHM_VERSION = 1
DEFAULTS = {'taxa_ids': None, 'excluded_sample_ids': [], 'transform': 'sqrt',
            'distance': 'euclidean', 'n_zones': 3, 'mode': 'algorithm',
            'manual_boundaries': [], 'manual_history': [], 'zone_names': {},
            'show_tree': True, 'show_zones': True, 'show_lines': True,
            'tree_width_cm': 1.5, 'zone_width_cm': .8, 'line_color': '#555555',
            'line_style': '-', 'line_width': .55, 'tree_color': '#333333'}


def config_for(project):
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(copy.deepcopy(project.cluster_config))
    if cfg['taxa_ids'] is None:
        cfg['taxa_ids'] = default_analysis_taxa(project)
    known = {t.id for t in project.taxa}
    cfg['taxa_ids'] = list(dict.fromkeys(tid for tid in cfg['taxa_ids'] if tid in known))
    samples = {s.id for s in project.samples}
    cfg['excluded_sample_ids'] = [sid for sid in cfg['excluded_sample_ids'] if sid in samples]
    return cfg


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')).hexdigest()


def input_signature(project, cfg=None):
    cfg = cfg or config_for(project)
    payload = {'algorithm_version': ALGORITHM_VERSION,
                   'taxa': [asdict(t) for t in project.taxa], 'samples': [asdict(s) for s in project.samples],
                   'axis_kind': project.axis_kind, 'unit': project.unit,
                   'selection': {key: cfg[key] for key in ('taxa_ids', 'excluded_sample_ids', 'transform', 'distance')}}
    if project.data_type!='counts':
        payload['data_type'] = project.data_type
    return digest(payload)


def prepare_matrix(project, cfg=None):
    cfg = cfg or config_for(project)
    if cfg['transform'] not in ('sqrt', 'none') or cfg['distance'] not in ('euclidean', 'squared_euclidean'):
        raise ValueError('聚类变换或距离设置无效')
    selected = set(cfg['taxa_ids'])
    taxa = [t for t in project.taxa if t.id in selected]
    if not taxa:
        raise ValueError('请至少选择一个参与聚类的类群')
    excluded = set(cfg['excluded_sample_ids'])
    indices = [i for i,s in enumerate(project.samples) if s.id not in excluded]
    if len(indices) < 2:
        raise ValueError('至少需要两个有效样品才能聚类')
    if len(indices) > 2000:
        raise ValueError('本版单次最多分析 2000 个样品，请先选择分析范围')
    values, issues = calculate(project)
    included_rows = {i+1 for i in indices}
    for level, row, field, message in issues:
        if level == '错误' and (not isinstance(row, int) or row in included_rows):
            raise ValueError(f'请核对原表第 {row} 行 / {field}：{message}')
    indices.sort(key=lambda i: number(project.samples[i].axis))
    y = [float(number(project.samples[i].axis)) for i in indices]
    if any(not math.isfinite(v) for v in y) or len(set(y)) != len(y):
        raise ValueError('参与聚类的深度/年代必须有效且不能重复')
    matrix = []
    for i in indices:
        row = []
        for t in taxa:
            v = values[i]['percentages'][t.id]
            if v is None or not math.isfinite(v):
                raise ValueError(f'原表第 {i+1} 行“{t.name}”百分比缺失或无效；请修正数据，或明确排除该样品/类群。不自动补零。')
            row.append(v)
        matrix.append(row)
    pct = np.array(matrix, dtype=float)
    if not np.any(pct):
        raise ValueError('所选类群全部为零，不能形成有意义的聚类')
    transformed = np.sqrt(pct/100) if cfg['transform'] == 'sqrt' else pct/100
    warnings = []
    if np.allclose(transformed, transformed[0], rtol=0, atol=1e-14):
        warnings.append('样品组成无差异；树仅反映并列值的确定性合并次序，不提供分带建议')
    if indices != sorted(indices):
        warnings.append('仅按坐标排序分析，原始计数表顺序保留')
    if excluded:
        warnings.append(f'明确排除 {len(excluded)} 个样品；树叶在原坐标处留空')
    return {'matrix': transformed, 'percentages': pct, 'indices': indices, 'y': y,
            'taxa_ids': [t.id for t in taxa], 'taxa_names': [t.name for t in taxa],
            'sample_ids': [project.samples[i].id for i in indices],
            'bases': [values[i]['base'] for i in indices], 'warnings': warnings}


def coniss(matrix, distance='euclidean', cancelled=None):
    x = np.asarray(matrix, dtype=float)
    if x.ndim != 2 or len(x) < 2 or not np.all(np.isfinite(x)):
        raise ValueError('CONISS 输入必须为至少两行的有限数值矩阵')
    n = len(x)
    distances = np.zeros((n,n), dtype=float)
    for i in range(n-1):
        if cancelled and cancelled():
            raise ValueError('分析已取消')
        delta = x[i+1:]-x[i]
        d = np.einsum('ij,ij->i', delta, delta)
        if distance == 'euclidean':
            d = np.sqrt(d)
        elif distance != 'squared_euclidean':
            raise ValueError('不支持的聚类距离')
        distances[i, i+1:] = d
        distances[i+1:, i] = d
    active = list(range(n))
    sizes = np.ones(n, dtype=int)
    nodes = list(range(n))
    starts, ends = list(range(n)), list(range(n))
    total = 0.0
    merges = []
    boundary_heights = [0.0]*(n-1)
    for step in range(n-1):
        if cancelled and cancelled():
            raise ValueError('分析已取消')
        # Strict argmin resolves exact ties toward the shallower/leftmost pair.
        adjacent = distances[np.array(active[:-1]), np.array(active[1:])]
        p = int(np.argmin(adjacent))
        a,b = active[p:p+2]
        increment = max(0.0, float(distances[a,b]))/2
        total += increment
        boundary = starts[b]
        merges.append({'left': int(nodes[a]), 'right': int(nodes[b]), 'height': float(total),
                       'increment': increment, 'count': int(sizes[a]+sizes[b]),
                       'start': starts[a], 'end': ends[b], 'boundary_index': boundary})
        boundary_heights[boundary-1] = float(total)
        others = np.array([j for j in active if j not in (a,b)], dtype=int)
        if len(others):
            sa,sb = int(sizes[a]),int(sizes[b])
            sj = sizes[others]
            updated = ((sa+sj)*distances[a,others]+(sb+sj)*distances[b,others]-sj*distances[a,b])/(sa+sb+sj)
            distances[a,others] = np.maximum(updated, 0)
            distances[others,a] = distances[a,others]
        sizes[a] += sizes[b]
        nodes[a] = n+step
        ends[a] = ends[b]
        active.pop(p+1)
    return merges, boundary_heights


def run_analysis(project, cfg=None, cancelled=None):
    cfg = cfg or config_for(project)
    inputs = prepare_matrix(project, cfg)
    merges, heights = coniss(inputs['matrix'], cfg['distance'], cancelled)
    result = {'version': ALGORITHM_VERSION, 'input_signature': input_signature(project,cfg),
              'sample_ids': inputs['sample_ids'], 'sample_rows': [i+1 for i in inputs['indices']],
              'y': inputs['y'], 'taxa_ids': inputs['taxa_ids'], 'taxa_names': inputs['taxa_names'],
              'bases': inputs['bases'], 'merges': merges, 'boundary_heights': heights,
              'warnings': inputs['warnings'], 'computed_at': datetime.now(timezone.utc).isoformat()}
    result['result_hash'] = digest(result)
    return result


def result_is_current(project):
    result = project.cluster_result
    if not result or result.get('version') != ALGORITHM_VERSION:
        return False
    try:
        if result['input_signature'] != input_signature(project):
            return False
        payload = {key:value for key,value in result.items() if key != 'result_hash'}
        return digest(payload) == result.get('result_hash')
    except (ValueError, TypeError, KeyError):
        return False


def require_result(project):
    if not result_is_current(project):
        raise ValueError('聚类结果缺失或已失效，请在“聚类与分带”页重新计算，或关闭树与组合带显示')
    return project.cluster_result


def roman(value):
    glyphs = ('Ⅰ','Ⅱ','Ⅲ','Ⅳ','Ⅴ','Ⅵ','Ⅶ','Ⅷ','Ⅸ','Ⅹ','Ⅺ','Ⅻ')
    if 1 <= value <= 12:
        return glyphs[value-1]
    text = ''
    for size, token in ((1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),(100,'C'),(90,'XC'),(50,'L'),(40,'XL'),(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I')):
        count,value = divmod(value,size)
        text += token*count
    return text


def algorithm_boundaries(result, k):
    n = len(result['y'])
    if not isinstance(k,int) or not 1 <= k <= n:
        raise ValueError(f'组合带数必须在 1 至 {n} 之间')
    cuts = [m['boundary_index'] for m in result['merges'][n-k:]]
    return sorted((result['y'][i-1]+result['y'][i])/2 for i in cuts)


def active_boundaries(project):
    r = require_result(project)
    cfg = config_for(project)
    if cfg['mode'] == 'algorithm':
        return algorithm_boundaries(r, cfg['n_zones'])
    if cfg['mode'] != 'manual':
        raise ValueError('无效的分带模式')
    values = [float(v) for v in cfg['manual_boundaries']]
    if any(not math.isfinite(v) or not r['y'][0] < v < r['y'][-1] for v in values):
        raise ValueError('人工边界必须位于有效样品坐标范围内部')
    if len(set(values)) != len(values):
        raise ValueError('人工边界不能重复')
    return sorted(values)


def zoning(project):
    r = require_result(project)
    cfg = config_for(project)
    boundaries = active_boundaries(project)
    n = len(boundaries)+1
    ids = n-np.searchsorted(boundaries, r['y'], side='right')
    names = cfg['zone_names']
    zones = [{'id': i, 'label': roman(i), 'name': names.get(str(i), ''),
              'lower': None if i == n else boundaries[n-i-1],
              'upper': None if i == 1 else boundaries[n-i]}
             for i in range(1,n+1)]
    return {'boundaries': boundaries, 'zone_ids': [int(v) for v in ids], 'zones': zones,
            'mode': cfg['mode'], 'source': '人工调整' if cfg['mode'] == 'manual' else 'CONISS 算法'}


def edit_boundaries(project, boundaries, action):
    require_result(project)
    cfg = config_for(project)
    before = active_boundaries(project)
    cfg['mode'] = 'manual'
    cfg['manual_boundaries'] = sorted(float(v) for v in boundaries)
    old = project.cluster_config
    project.cluster_config = cfg
    try:
        active_boundaries(project)
    except Exception:
        project.cluster_config = old
        raise
    cfg['manual_history'].append({'time': datetime.now(timezone.utc).isoformat(),
                                  'action': action, 'before': before, 'after': cfg['manual_boundaries'][:]})


def broken_stick(result, max_groups=10):
    heights = np.array([m['height'] for m in result['merges']])
    if len(heights) < 2 or heights[-1] <= 1e-14:
        return []
    observed = np.abs(np.diff(heights[::-1]))
    m = len(heights)
    expected = np.cumsum((1/np.arange(m,0,-1, dtype=float)))[::-1]*heights[-1]/m
    return [{'groups': j+2, 'observed': float(observed[j]), 'expected': float(expected[j])}
            for j in range(min(len(observed), max_groups-1))]


def export_analysis(project, path):
    from openpyxl import Workbook
    from .excel import style_sheet, put
    r = require_result(project)
    z = zoning(project)
    cfg = config_for(project)
    inputs = prepare_matrix(project,cfg)
    wb = Workbook()
    wb.remove(wb.active)
    tables = {}
    included = {sid:i for i,sid in enumerate(r['sample_ids'])}
    rows = [['原表数据行', '深度/年代', '分析状态', '带号', '带名', '分带来源', '实际基数']]
    for i,s in enumerate(project.samples):
        j = included.get(s.id)
        zone_id = z['zone_ids'][j] if j is not None else None
        rows.append([i+1,s.axis, '参与' if j is not None else '明确排除',
                     roman(zone_id) if zone_id else '', cfg['zone_names'].get(str(zone_id),'') if zone_id else '',
                     z['source'] if j is not None else '', r['bases'][j] if j is not None else None])
    tables['样品分带'] = rows
    rows = [['边界坐标', '较浅或较年轻样品坐标', '较深或较老样品坐标', '较浅端原表行', '较深端原表行', '来源']]
    for b in z['boundaries']:
        j = min(max(int(np.searchsorted(r['y'],b,side='right')),1),len(r['y'])-1)
        rows.append([b,r['y'][j-1],r['y'][j],r['sample_rows'][j-1],r['sample_rows'][j],z['source']])
    tables['边界区间'] = rows
    tables['合并过程'] = [['步骤','左节点（从零开始）','右节点（从零开始）','累计总离差','本步增量','样品数','较浅端原表行','较深端原表行']] + [
        [i+1,m['left'],m['right'],m['height'],m['increment'],m['count'],r['sample_rows'][m['start']],r['sample_rows'][m['end']]] for i,m in enumerate(r['merges'])]
    tables['参与类群'] = [['类群','分组','进入百分比基数']] + [[t.name,t.group,'不适用（已有百分比）' if project.data_type=='percentages' else ('是' if t.selected else '否')] for t in project.taxa if t.id in r['taxa_ids']]
    tables['分析百分比'] = [['原表数据行','深度/年代']+inputs['taxa_names']] + [
        [r['sample_rows'][i],r['y'][i]]+row.tolist() for i,row in enumerate(inputs['percentages'])]
    tables['分析设置'] = [['项目','内容'], ['方法','CONISS（地层约束增量离差）'],['输入数据类型',input_label(project)],
        ['变换','平方根：√（百分比/100）' if cfg['transform']=='sqrt' else '比例：百分比/100'],
        ['距离','欧氏距离' if cfg['distance']=='euclidean' else '欧氏距离平方'],
        ['距离约定','欧氏距离模式与 rioja::chclust(dist(x)) 相同；平方模式衡量变换矩阵的平方和'],
        ['基数口径','直接采用导入百分比，不重新计算、不重新归一化；原始基数未知' if project.data_type=='percentages' else '采用原百分比基数，筛除聚类类群后不重新归一化'], ['并列值','优先合并坐标较小端的相邻组'],
        ['带号方向','深部/较老端为Ⅰ带，向浅部/较年轻端递增'], ['边界归属','坐标恰在边界上时归入较深/较老一侧'],
        ['分带来源',z['source']], ['坐标类型',project.axis_kind],['坐标单位',project.unit],['计算时间',r['computed_at']],
        ['输入指纹',r['input_signature']],['提示','；'.join(r['warnings'])]]
    tables['人工调整记录'] = [['时间','操作','原边界','新边界']] + [
        [e['time'],e['action'],'、'.join(map(str,e['before'])),'、'.join(map(str,e['after']))] for e in cfg['manual_history']]
    tables['带数参考'] = [['组合带数','观测离差变化','断棍模型参考值']] + [[b['groups'],b['observed'],b['expected']] for b in broken_stick(r)]
    for name, rows in tables.items():
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
