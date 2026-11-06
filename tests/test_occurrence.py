import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
import copy
import tempfile
import unittest
import numpy as np
from openpyxl import load_workbook

from pollen.core import Project,Taxon,Sample,GROUPS,save_project,load_project
from pollen import occurrence as o
from pollen.occurrence_render import build_figure
from pollen.plotting import export_diagram


def profile():
    taxa = [Taxon('基数','木本',selected=True),Taxon('稀有类群','草本'),Taxon('缺失类群','蕨类')]
    values = [(10,[50000,1,None]),(20,[0,5,0]),(45,['',0,2])]
    p = Project(taxa=taxa,samples=[Sample(str(y),{t.id:v for t,v in zip(taxa,row)}) for y,row in values])
    p.occurrence_config = o.config_for(p)
    p.occurrence_config['taxa_ids'] = [t.id for t in taxa[1:]]
    return p


class Detection(unittest.TestCase):
    def test_counts_and_percentages_keep_missing_and_invalid_bases_distinct(self):
        p = profile()
        counts = o.prepare(p)
        np.testing.assert_array_equal(counts.states,[[1,-1],[1,0],[0,1]])
        self.assertAlmostEqual(counts.percentages[0,0],.002)
        self.assertEqual(f'{counts.percentages[0,0]:.2f}','0.00')
        p.occurrence_config['mode'] = 'percentages'
        pct = o.prepare(p)
        np.testing.assert_array_equal(pct.states,[[1,-1],[-2,-2],[-2,-2]])
        self.assertEqual(pct.reasons[1,0],'基数为零')
        self.assertIn('缺失',pct.reasons[2,0])

    def test_modes_agree_with_valid_bases_and_counts_need_no_denominator(self):
        p = profile()
        for s in p.samples:
            s.counts[p.taxa[0].id] = 50000
        first = o.prepare(p).states
        p.occurrence_config['mode'] = 'percentages'
        np.testing.assert_equal(o.prepare(p).states,first)
        for t in p.taxa:
            t.selected = False
        with self.assertRaisesRegex(ValueError,'选择计算基数'):
            o.prepare(p)
        p.occurrence_config['mode'] = 'counts'
        np.testing.assert_equal(o.prepare(p).states,first)

    def test_invalid_counts_exclusions_and_real_coordinate_sort(self):
        p = profile()
        before = copy.deepcopy(p.samples)
        p.samples = [p.samples[2],p.samples[0],p.samples[1]]
        order = copy.deepcopy(p.samples)
        data = o.prepare(p)
        np.testing.assert_equal(data.y,[10,20,45])
        self.assertEqual(p.samples,order)
        self.assertEqual(data.indices,[1,2,0])
        p.samples[0].counts[p.taxa[1].id] = -1
        with self.assertRaisesRegex(ValueError,'非负整数'):
            o.prepare(p)
        p.occurrence_config['excluded_sample_ids'] = [p.samples[0].id]
        self.assertEqual(len(o.prepare(p).y),2)
        p.samples[2].axis = '10'
        with self.assertRaisesRegex(ValueError,'重复'):
            o.prepare(p)
        self.assertEqual(before[0].axis,'10')

    def test_hide_zero_does_not_hide_unknown_only_taxa(self):
        p = profile()
        for s in p.samples:
            s.counts[p.taxa[1].id] = 0
            s.counts[p.taxa[2].id] = ''
        p.occurrence_config['hide_zero'] = True
        d = o.prepare(p)
        self.assertEqual([t.id for t in d.taxa],[p.taxa[2].id])
        np.testing.assert_equal(d.states,[[-1],[-1],[-1]])


class RowLayout(unittest.TestCase):
    def test_balanced_boundary_counts_and_no_taxa_lost(self):
        expected = {1:[1],40:[40],41:[21,20],60:[30,30],80:[40,40],81:[27,27,27],
                    100:[34,33,33],120:[40,40,40],121:[31,30,30,30]}
        for n,sizes in expected.items():
            taxa = [Taxon(str(i),'木本') for i in range(n)]
            rows = o.partitions(taxa,o.DEFAULTS)
            self.assertEqual([len(row) for row in rows],sizes)
            self.assertEqual([t.id for row in rows for t in row],[t.id for t in taxa])
            self.assertLessEqual(max(map(len,rows)),40)

    def test_group_priority_and_manual_rows_obey_cap(self):
        taxa = [Taxon(str(i),GROUPS[i//25]) for i in range(75)]
        rows = o.partitions(taxa,dict(o.DEFAULTS,layout='groups'))
        self.assertEqual([len(row) for row in rows],[25,25,25])
        same_group = [Taxon(str(i),'木本') for i in range(81)]
        self.assertEqual([len(row) for row in o.partitions(same_group,dict(o.DEFAULTS,layout='groups'))],[27,27,27])
        cfg = dict(o.DEFAULTS,layout='manual',manual_starts=[taxa[30].id,taxa[60].id])
        self.assertEqual([len(row) for row in o.partitions(taxa,cfg)],[30,30,15])
        cfg['manual_starts'] = [taxa[50].id]
        with self.assertRaisesRegex(ValueError,'超过上限'):
            o.partitions(taxa,cfg)


class PersistenceAndRendering(unittest.TestCase):
    def test_workbook_frequency_and_literal_names(self):
        p = profile()
        p.taxa[1].name = '=1+1'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'项目.pollen'
            save_project(p,path)
            restored = load_project(path)
            self.assertEqual(restored,p)
            output = Path(temp)/'检出.xlsx'
            o.export_analysis(p,output)
            wb = load_workbook(output,data_only=False)
            self.assertEqual(wb['检出矩阵']['C1'].value,'=1+1')
            self.assertEqual(wb['检出矩阵']['C1'].data_type,'s')
            self.assertEqual(wb['检出矩阵']['D2'].value,None)
            self.assertAlmostEqual(wb['类群统计']['G2'].value,200/3)
            self.assertEqual(wb['类群统计']['D3'].value,2)
            self.assertEqual(wb['类群统计']['G3'].value,50)
            wb.close()
            p.occurrence_config['mode'] = 'percentages'
            o.export_analysis(p,output)
            wb = load_workbook(output,data_only=True)
            self.assertEqual(wb['类群统计']['D2'].value,1)
            self.assertEqual(wb['类群统计']['G2'].value,100)
            self.assertIsNone(wb['类群统计']['G3'].value)
            wb.close()

    def test_all_seven_markers_render_and_have_exact_occurrence_positions(self):
        p = profile()
        for marker in o.MARKERS:
            p.occurrence_config['marker'] = marker
            d = build_figure(p)
            self.assertEqual(d.layout['found'],3)
            offsets = d.axes[0].collections[0].get_offsets()
            np.testing.assert_allclose(offsets,[[0,10],[0,20]])
            self.assertEqual(len(d.axes),1)
            self.assertTrue(d.axes[0].yaxis_inverted())

    def test_multirow_shared_y_export_zoom_and_stale_guard(self):
        p = profile()
        p.taxa = [Taxon('Taxon '+str(i),GROUPS[i%6]) for i in range(81)]
        for s in p.samples:
            s.counts = {t.id:(1 if i%2 else 0) for i,t in enumerate(p.taxa)}
        p.occurrence_config = {}
        p.occurrence_config = o.config_for(p)
        p.occurrence_config.update(y_min=0,y_max=50,y_step=10)
        d = build_figure(p)
        self.assertEqual(d.layout['row_sizes'],[27,27,27])
        self.assertEqual(len(d.axes),3)
        for ax in d.axes:
            np.testing.assert_equal(ax.get_ylim(),[50,0])
            np.testing.assert_equal(ax.get_yticks(),[0,10,20,30,40,50])
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            for ext in ('png','pdf','svg'):
                export_diagram(p,d,temp/f'出现分布.{ext}',dpi=100)
            image = (temp/'出现分布.png').read_bytes()
            d.axes[-1].set_ylim(30,15)
            export_diagram(p,d,temp/'缩放后.png',dpi=100)
            self.assertEqual(image,(temp/'缩放后.png').read_bytes())
            self.assertEqual(len(list(temp.iterdir())),4)
            p.samples[0].counts[p.taxa[0].id] = 1
            with self.assertRaisesRegex(ValueError,'更新图谱'):
                export_diagram(p,d,temp/'过期.pdf')


if __name__=='__main__':
    unittest.main()
