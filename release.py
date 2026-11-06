"""Include a usable template, sample project, and instructions in the portable folder."""
import sys
import shutil
import argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / '.deps'))
from pollen.core import example, Sample, save_project
from pollen.excel import write_excel
from pollen.plotting import diagram_example, config_for, build_diagrams, export_document, save_style
from pollen.demo import sixty_taxa_example,percentage_example
from pollen import clustering
from pollen import pca
from pollen.pca_render import build_figure
from pollen.plotting import export_diagram
from pollen import occurrence
from pollen.occurrence_render import build_figure as build_occurrence
from pollen import __version__

parser = argparse.ArgumentParser(description='生成正式版模板、模拟示例和离线说明')
parser.add_argument('--output',type=Path,default=Path(__file__).parent/'dist'/f'v{__version__}'/'PollenStudio')
out = parser.parse_args().output.resolve()
out.mkdir(parents=True, exist_ok=True)
p = example()
save_project(p, out / '示例项目.pollen')
write_excel(p, out / '示例计算结果.xlsx')
p.samples = [Sample() for _ in range(5)]
for t in p.taxa:
    t.selected = False
write_excel(p, out / '孢粉计数模板.xlsx', template=True)
p = diagram_example()
p.plot_config = config_for(p)
diagrams = build_diagrams(p)
save_project(p, out / '绘图示例项目（模拟数据）.pollen')
write_excel(p, out / '绘图示例数据（模拟数据）.xlsx')
save_style(p, out / '单幅自动列宽版式.pollenstyle')
for suffix in ('png', 'pdf', 'svg'):
    export_document(p, diagrams, out / f'示例孢粉图谱.{suffix}', dpi=300)
p = sixty_taxa_example()
p.plot_config = config_for(p)
diagrams = build_diagrams(p)
save_project(p, out/'60类群单幅示例（模拟数据）.pollen')
write_excel(p, out/'60类群计数（模拟数据）.xlsx')
for suffix in ('png', 'pdf', 'svg'):
    export_document(p, diagrams, out/f'60类群单幅图谱.{suffix}', dpi=300)
p.cluster_config = clustering.config_for(p)
p.cluster_config['n_zones'] = 3
p.cluster_result = clustering.run_analysis(p)
save_project(p, out/'聚类与分带示例（模拟数据）.pollen')
clustering.export_analysis(p, out/'聚类与分带分析结果（模拟数据）.xlsx')
diagrams = build_diagrams(p)
for suffix in ('png', 'pdf', 'svg'):
    export_document(p, diagrams, out/f'聚类与分带图谱（模拟数据）.{suffix}', dpi=300)
boundaries = clustering.active_boundaries(p)
boundaries[0] = (boundaries[0]+boundaries[1])/2
clustering.edit_boundaries(p, boundaries, '示例：移动较浅端边界')
save_project(p, out/'人工分带示例（模拟数据）.pollen')
clustering.export_analysis(p, out/'人工分带分析结果（模拟数据）.xlsx')
export_document(p, build_diagrams(p), out/'人工分带图谱（模拟数据）.pdf', dpi=300)
p.cluster_config['mode'] = 'algorithm'
p.pca_config = pca.config_for(p)
p.pca_result = pca.run_analysis(p)
save_project(p,out/'PCA双图示例（模拟数据）.pollen')
pca.export_analysis(p,out/'PCA分析结果（模拟数据）.xlsx')
diagram = build_figure(p)
for suffix in ('png','pdf','svg'):
    export_diagram(p,diagram,out/f'PCA双图（模拟数据）.{suffix}',dpi=300)
p.occurrence_config = occurrence.config_for(p)
save_project(p,out/'出现分布图示例（模拟数据）.pollen')
occurrence.export_analysis(p,out/'原始计数检出记录（模拟数据）.xlsx')
diagram = build_occurrence(p)
for suffix in ('png','pdf','svg'):
    export_diagram(p,diagram,out/f'孢粉出现分布图（模拟数据）.{suffix}',dpi=300)
p.occurrence_config.update(mode='percentages',marker='circle')
save_project(p,out/'百分比检出示例（模拟数据）.pollen')
occurrence.export_analysis(p,out/'百分比检出记录（模拟数据）.xlsx')
export_diagram(p,build_occurrence(p),out/'百分比圆点分布图（模拟数据）.pdf',dpi=300)
p = percentage_example()
write_excel(p,out/'已有百分比数据（模拟数据）.xlsx')
from openpyxl import load_workbook
for fractional in (False,True):
    wb = load_workbook(out/'已有百分比数据（模拟数据）.xlsx')
    for row in wb['导入百分比'].iter_rows(min_row=3,min_col=2):
        for cell in row:
            if isinstance(cell.value,(int,float)):
                cell.value /= 100
                cell.number_format = 'General' if fractional else '0.0000%'
    for row in wb['计算说明']:
        if row[0].value=='数值存储单位':
            row[1].value = '比例数' if fractional else '百分数'
    wb.save(out/('比例数导入示例（模拟数据）.xlsx' if fractional else 'Excel百分比格式导入示例（模拟数据）.xlsx'))
    wb.close()
p.cluster_result = clustering.run_analysis(p)
p.pca_result = pca.run_analysis(p)
save_project(p,out/'已有百分比直接分析示例（模拟数据）.pollen')
export_document(p,build_diagrams(p),out/'已有百分比直接绘图（模拟数据）.pdf',dpi=300)
export_diagram(p,build_figure(p),out/'已有百分比PCA（模拟数据）.pdf',dpi=300)
export_diagram(p,build_occurrence(p),out/'已有百分比出现分布（模拟数据）.pdf',dpi=300)
p.samples = [Sample() for _ in range(5)]
write_excel(p,out/'孢粉百分比模板.xlsx',template=True)
for name in ('模板','示例项目','示例数据与结果','示例图件','使用指南'):
    (out/name).mkdir(exist_ok=True)
for path in out.iterdir():
    if not path.is_file():
        continue
    folder = None
    if path.suffix=='.xlsx':
        folder = '模板' if '模板' in path.name else '示例数据与结果'
    elif path.suffix=='.pollen':
        folder = '示例项目'
    elif path.suffix in ('.pdf','.png','.svg','.pollenstyle'):
        folder = '示例图件'
    if folder:
        shutil.move(str(path),str(out/folder/path.name))
guide_source = Path(__file__).parent/'使用指南'
for path in guide_source.glob('*.html'):
    shutil.copyfile(path,out/'使用指南'/path.name)
shutil.copyfile(Path(__file__).parent/'README.md',out/'使用指南/使用说明.md')
(out/'欢迎使用.html').write_text('''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>孢粉分析工作台</title></head>
<body style="font:18px/1.8 Microsoft YaHei,sans-serif;color:#263c36;max-width:800px;margin:60px auto;padding:24px;background:#f4f7f6">
<h1 style="color:#184e43">孢粉分析工作台</h1><p>正式版 '''+__version__+''' · Windows 64 位</p>
<p>请完整解压软件文件夹，然后双击 <b>PollenStudio.exe</b>。无需 Python、R 或联网；保留 _internal 子文件夹。</p>
<p><a href="使用指南/快速入门.html">快速入门</a>　<a href="使用指南/使用说明.html">完整使用说明</a>　<a href="使用指南/版本说明.html">版本说明</a></p>
<p>模板、示例项目、示例数据与结果、示例图件已分类存放。示例均为模拟数据。</p>
<p>软件中按 F1 查看帮助；Ctrl+S 保存项目，Ctrl+Shift+S 另存副本。</p></body></html>''',encoding='utf-8')
print('已生成计数及百分比模板、三种百分比单位示例、聚类、PCA、出现分布图及使用说明。')
