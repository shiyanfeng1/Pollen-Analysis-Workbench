"""Calculation rules independent of the desktop interface."""
from dataclasses import dataclass, field, asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json
import math
import os
import tempfile
import uuid
from . import __version__

GROUPS = ('木本', '灌木', '草本', '水生', '蕨类', '未定类群')


def uid():
    return uuid.uuid4().hex


@dataclass
class Taxon:
    name: str
    group: str
    id: str = field(default_factory=uid)
    selected: bool = False


@dataclass
class Sample:
    axis: str = ''
    counts: dict = field(default_factory=dict)
    id: str = field(default_factory=uid)


@dataclass
class Project:
    taxa: list = field(default_factory=list)
    samples: list = field(default_factory=list)
    axis_kind: str = '深度'
    unit: str = 'cm'
    threshold: int = 200
    decimals: int = 2
    schemes: dict = field(default_factory=dict)
    plot_config: dict = field(default_factory=dict)
    cluster_config: dict = field(default_factory=dict)
    cluster_result: dict = field(default_factory=dict)
    pca_config: dict = field(default_factory=dict)
    pca_result: dict = field(default_factory=dict)
    occurrence_config: dict = field(default_factory=dict)
    data_type: str = 'counts'
    source_info: dict = field(default_factory=dict)

    def ordered_taxa(self):
        return sorted(self.taxa, key=lambda t: GROUPS.index(t.group))


def number(value):
    if value is None or str(value).strip() == '':
        return None
    try:
        n = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError('必须是数字')
    if not n.is_finite():
        raise ValueError('必须是有限数字')
    return n


def count(value):
    n = number(value)
    if n is not None and (n < 0 or n != n.to_integral_value()):
        raise ValueError('计数必须是非负整数')
    return None if n is None else int(n)


def percentage(value):
    """Canonical percentage points: 12.5 and '12.5%' both mean 12.5 percent."""
    if isinstance(value,str) and value.strip().endswith(('%','％')):
        value = value.strip()[:-1].strip()
        if not value:
            raise ValueError('百分比必须是非负有限数字')
    n = number(value)
    if n is not None and (n<0 or not math.isfinite(float(n))):
        raise ValueError('百分比必须是非负有限数字')
    return n


def input_label(project):
    return '已有百分比' if project.data_type=='percentages' else '原始计数'


def default_analysis_taxa(project):
    return [t.id for t in project.taxa if project.data_type=='percentages' or t.selected]


def calculate(project):
    """Missing denominator -> NA; missing numerator only affects that taxon."""
    issues, results = [], []
    direct = project.data_type=='percentages'
    selected = [t for t in project.taxa if t.selected]
    if not direct and not selected:
        issues.append(('错误', '全部', '计算基数', '请至少选择一个类群作为基数'))
    names = [t.name.strip() for t in project.taxa]
    if any(not n for n in names) or len(names) != len(set(names)):
        issues.append(('错误', '全部', '类群名称', '类群名称不能为空或重复'))
    seen, prev = set(), None
    for i, sample in enumerate(project.samples, 1):
        try:
            axis = number(sample.axis)
            if axis is None:
                raise ValueError('深度/年代不能为空')
            if axis in seen:
                issues.append(('提示', i, '深度/年代', '该数值重复，请核对'))
            if prev is not None and axis < prev:
                issues.append(('提示', i, '深度/年代', '未按递增顺序排列，保留原顺序'))
            seen.add(axis)
            prev = axis
        except ValueError as exc:
            issues.append(('错误', i, '深度/年代', str(exc)))
        values = {}
        for taxon in project.taxa:
            try:
                parser = percentage if direct else count
                values[taxon.id] = parser(sample.counts.get(taxon.id, ''))
                if values[taxon.id] is None:
                    issues.append(('提示', i, taxon.name, '百分比缺失，未按零处理' if direct else '计数缺失，未按零处理'))
                elif direct and values[taxon.id]>100:
                    issues.append(('提示',i,taxon.name,'百分比超过 100%，请核对输入单位及原始口径；原值保留'))
            except ValueError as exc:
                values[taxon.id] = None
                issues.append(('错误', i, taxon.name, str(exc)))
        base = None
        reason = ''
        if direct:
            results.append({'base':None,'percentages':{tid:None if v is None else float(v) for tid,v in values.items()},
                            'reason':'直接使用导入百分比；原始计数及实际基数未知，不重新归一化'})
            continue
        if not selected:
            reason = '未选择基数'
        elif any(values[t.id] is None for t in selected):
            reason = '基数类群存在缺失或无效计数'
        else:
            base = sum(values[t.id] for t in selected)
            if base == 0:
                reason = '基数为零'
                issues.append(('提示', i, '计算基数', reason))
            elif base < project.threshold:
                issues.append(('提示', i, '计算基数', f'基数 {base} 低于提示阈值 {project.threshold}'))
        percentages = {t.id: (float(Decimal(values[t.id]) * 100 / Decimal(base))
                             if base and values[t.id] is not None else None)
                       for t in project.taxa}
        results.append({'base': base, 'percentages': percentages, 'reason': reason})
    return results, issues


def save_project(project, path):
    payload = {'format': 'pollen-project', 'version': 1, 'app_version': __version__,
               'data': asdict(project)}
    target = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=target.parent,
                                         suffix='.tmp', delete=False) as f:
            temporary = f.name
            json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(temporary, target)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()


def load_project(path):
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if payload.get('format') != 'pollen-project' or payload.get('version') != 1:
        raise ValueError('不是支持的孢粉项目文件（版本 1）')
    data = payload['data']
    p = Project(taxa=[Taxon(**t) for t in data['taxa']],
                samples=[Sample(**s) for s in data['samples']],
                axis_kind=data['axis_kind'], unit=data['unit'],
                threshold=data['threshold'], decimals=data['decimals'],
                schemes=data.get('schemes', {}), plot_config=data.get('plot_config', {}),
                cluster_config=data.get('cluster_config', {}), cluster_result=data.get('cluster_result', {}),
                pca_config=data.get('pca_config', {}), pca_result=data.get('pca_result', {}),
                occurrence_config=data.get('occurrence_config', {}),
                data_type=data.get('data_type','counts'),source_info=data.get('source_info',{}))
    if p.data_type not in ('counts','percentages') or not isinstance(p.source_info,dict):
        raise ValueError('数据类型或导入来源信息无效')
    ids = [t.id for t in p.taxa]
    if len(ids) != len(set(ids)) or any(t.group not in GROUPS for t in p.taxa):
        raise ValueError('项目类群标识或分组无效')
    if p.axis_kind not in ('深度', '年代') or not isinstance(p.unit, str):
        raise ValueError('坐标设置无效')
    if not isinstance(p.threshold, int) or not 0 <= p.threshold <= 100000000:
        raise ValueError('计数阈值无效')
    if not isinstance(p.decimals, int) or not 0 <= p.decimals <= 8:
        raise ValueError('小数位数无效')
    if any(not isinstance(s.counts, dict) for s in p.samples):
        raise ValueError('项目计数数据无效')
    if not isinstance(p.plot_config, dict):
        raise ValueError('绘图设置无效')
    if not isinstance(p.cluster_config, dict) or not isinstance(p.cluster_result, dict):
        raise ValueError('聚类设置或结果格式无效')
    if not isinstance(p.pca_config, dict) or not isinstance(p.pca_result, dict):
        raise ValueError('PCA 设置或结果格式无效')
    if not isinstance(p.occurrence_config, dict):
        raise ValueError('出现分布图设置格式无效')
    return p


def example():
    taxa = [Taxon('松属', '木本', selected=True), Taxon('栎属', '木本', selected=True),
            Taxon('榛属', '灌木', selected=True), Taxon('蒿属', '草本', selected=True),
            Taxon('禾本科', '草本', selected=True), Taxon('香蒲属', '水生'),
            Taxon('水龙骨科', '蕨类'), Taxon('未定类群1', '未定类群')]
    rows = [(10, [60, 40, 20, 50, 30, 20, 10, 3]),
            (20, [80, 30, 15, 50, 25, 10, 20, 2]),
            (30, [45, 25, 10, 40, 30, 5, 8, 0])]
    return Project(taxa=taxa, samples=[Sample(str(axis), dict(zip([t.id for t in taxa], map(str, counts))))
                                      for axis, counts in rows])
