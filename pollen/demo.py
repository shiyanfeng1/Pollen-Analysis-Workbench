"""Dense synthetic example with dominant and rare taxa; never a real core."""
import math
from .core import Project, Taxon, Sample, calculate


def sixty_taxa_example():
    groups = [
        ('木本', 'Abies Cedrus Cupressaceae Larix Picea Pinus Podocarpus Tsuga Alnus Tilia Betula Carpinus Carya Castanea Celtis Corylus Elaeagnus Juglans Keteleeria Liquidambar Myrica Oleaceae Platycarya Quercus Robinia Salix Tsuga-type Ulmus Zelkova'),
        ('灌木', 'Ephedra Fabaceae Hippophae Rosaceae Sambucus Tamaricaceae'),
        ('草本', 'Artemisia Anthemis-type Brassicaceae Campanulaceae Caryophyllaceae Amaranthaceae Geraniaceae Lamiaceae Other-herbs Poaceae Polygonum Primulaceae Ranunculaceae Rumex Saussurea-type Saxifragaceae Scrophulariaceae Taraxacum-type Thalictrum'),
        ('水生', 'Typha Potamogeton'), ('蕨类', 'Polypodiaceae Pteris'),
        ('未定类群', '未定类群1 未定类群2')]
    taxa = [Taxon(name, group, selected=group in ('木本', '灌木', '草本')) for group, text in groups for name in text.split()]
    assert len(taxa) == 60
    dominant = {'Pinus': 380, 'Quercus': 220, 'Artemisia': 150, 'Poaceae': 150,
                'Amaranthaceae': 90, 'Rosaceae': 40, 'Polypodiaceae': 80,
                'Typha': 25, 'Brassicaceae': 60, 'Taraxacum-type': 55, 'Betula': 45}
    p = Project(taxa=taxa, axis_kind='年代', unit='ka', plot_config={'title': '60 类群单幅示例（模拟数据）'})
    for i in range(120):
        counts = {}
        for j, t in enumerate(taxa):
            baseline = dominant.get(t.name, .8+(j%4)*.25)
            wave = .65+.28*math.sin(i*.11+j)+.20*math.sin(i*1.7+j*.49)
            if t.name not in dominant and (i+j)%11 == 0:
                wave += .9
            counts[t.id] = str(int(max(0, baseline*wave)))
        p.samples.append(Sample(f'{i*12.5:g}', counts))
    return p


def percentage_example():
    """An explicit percentage-only dataset, with no retained grain counts."""
    p = sixty_taxa_example()
    results,_ = calculate(p)
    for sample,row in zip(p.samples,results):
        sample.counts = {tid:'' if value is None else str(value) for tid,value in row['percentages'].items()}
    for taxon in p.taxa:
        taxon.selected = False
    p.data_type = 'percentages'
    p.source_info = {'file':'模拟百分比数据','numeric_unit':'percent','stored_unit':'percent'}
    p.plot_config['title'] = '已有百分比直接绘图示例（模拟数据）'
    return p
