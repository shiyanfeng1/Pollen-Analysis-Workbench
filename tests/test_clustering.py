import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
import copy
import tempfile
import unittest
import numpy as np
from openpyxl import load_workbook
from pollen.core import Project, Taxon, Sample, example, save_project, load_project
from pollen import clustering as c
from pollen.plotting import build_diagram, config_for as plot_config, export_diagram


def profile():
    taxa = [Taxon('甲','木本',selected=True),Taxon('乙','草本',selected=True),Taxon('水生甲','水生')]
    rows = [(10,[90,10,2]),(20,[85,15,0]),(50,[10,90,5]),(90,[15,85,0])]
    return Project(taxa=taxa,samples=[Sample(str(y),{t.id:str(v) for t,v in zip(taxa,values)}) for y,values in rows])


class Coniss(unittest.TestCase):
    def test_hand_computed_merges_and_distance_conventions(self):
        matrix = np.array([[0],[1],[4],[5]],dtype=float)
        merges, boundaries = c.coniss(matrix)
        self.assertEqual([(m['left'],m['right']) for m in merges],[(0,1),(2,3),(4,5)])
        np.testing.assert_allclose([m['height'] for m in merges],[.5,1,4.5])
        np.testing.assert_allclose(boundaries,[.5,4.5,1])
        squared,_ = c.coniss(matrix,'squared_euclidean')
        self.assertAlmostEqual(squared[-1]['height'],float(((matrix-matrix.mean())**2).sum()))
        self.assertAlmostEqual(squared[-1]['height'],17)

    def test_independent_pairwise_energy_reference_and_adjacency(self):
        x = np.random.default_rng(294).random((12,5))
        distances = np.linalg.norm(x[:,None,:]-x[None,:,:],axis=2)
        groups = [[i] for i in range(len(x))]
        expected = []
        total = 0
        def energy(a,b):
            within_a = distances[np.ix_(a,a)].mean()
            within_b = distances[np.ix_(b,b)].mean()
            between = distances[np.ix_(a,b)].mean()
            return len(a)*len(b)/(len(a)+len(b))*(between-within_a/2-within_b/2)
        while len(groups)>1:
            costs = [energy(a,b) for a,b in zip(groups,groups[1:])]
            i = int(np.argmin(costs))
            total += costs[i]
            expected.append((min(groups[i]),max(groups[i+1]),total))
            groups[i:i+2] = [groups[i]+groups[i+1]]
        merges,_ = c.coniss(x)
        self.assertEqual([(m['start'],m['end']) for m in merges],[(a,b) for a,b,_ in expected])
        np.testing.assert_allclose([m['height'] for m in merges],[h for _,_,h in expected])

    def test_ties_are_leftmost_and_two_samples_supported(self):
        merges,_ = c.coniss(np.ones((4,2)))
        self.assertEqual([(m['left'],m['right']) for m in merges],[(0,1),(4,2),(5,3)])
        self.assertTrue(all(m['height']==0 for m in merges))
        self.assertEqual(len(c.coniss([[0],[1]])[0]),1)

    def test_input_selection_missing_values_sort_and_no_renormalization(self):
        p = profile()
        p.samples = [p.samples[i] for i in (2,0,3,1)]
        cfg = c.config_for(p)
        cfg['taxa_ids'] = [p.taxa[0].id]
        prepared = c.prepare_matrix(p,cfg)
        self.assertEqual(prepared['y'],[10,20,50,90])
        self.assertEqual(prepared['indices'],[1,3,0,2])
        self.assertEqual(prepared['percentages'][:,0].tolist(),[90,85,10,15])
        np.testing.assert_allclose(prepared['matrix'][:,0],np.sqrt([.9,.85,.1,.15]))
        p.samples[1].counts[p.taxa[0].id] = ''
        with self.assertRaisesRegex(ValueError,'不自动补零'):
            c.prepare_matrix(p,cfg)
        cfg['excluded_sample_ids'] = [p.samples[1].id]
        self.assertEqual(len(c.prepare_matrix(p,cfg)['y']),3)


class Zonation(unittest.TestCase):
    def setUp(self):
        self.p = profile()
        self.p.cluster_config = c.config_for(self.p)
        self.p.cluster_result = c.run_analysis(self.p)

    def test_automatic_boundary_and_oldest_first_labels(self):
        self.p.cluster_config['n_zones'] = 2
        z = c.zoning(self.p)
        self.assertEqual(z['boundaries'],[35])
        self.assertEqual(z['zone_ids'],[2,2,1,1])
        self.assertEqual(z['zones'][0]['label'],'Ⅰ')
        self.assertEqual(z['zones'][1]['label'],'Ⅱ')

    def test_manual_edit_history_restoration_and_invalid_boundaries(self):
        p = self.p
        p.cluster_config['n_zones'] = 2
        tree = copy.deepcopy(p.cluster_result)
        c.edit_boundaries(p,[25,70],'新增与移动')
        self.assertEqual(c.zoning(p)['zone_ids'],[3,3,2,1])
        self.assertEqual(p.cluster_result,tree)
        self.assertEqual(len(p.cluster_config['manual_history']),1)
        with self.assertRaisesRegex(ValueError,'范围内部'):
            c.edit_boundaries(p,[100],'错误移动')
        self.assertEqual(c.active_boundaries(p),[25,70])
        p.cluster_config['mode'] = 'algorithm'
        self.assertEqual(c.active_boundaries(p),[35])

    def test_cache_depends_on_data_and_analysis_but_not_plotting(self):
        p = self.p
        p.plot_config = plot_config(p)
        p.plot_config['group_colors']['木本'] = '#ff0000'
        p.plot_config['taxa'][p.taxa[0].id]['visible'] = False
        p.cluster_config['n_zones'] = 3
        self.assertTrue(c.result_is_current(p))
        p.samples[0].counts[p.taxa[0].id] = '91'
        self.assertFalse(c.result_is_current(p))

    def test_corrupt_cache_detected_and_old_project_compatible(self):
        p = self.p
        p.cluster_result['merges'][0]['height'] += 1
        self.assertFalse(c.result_is_current(p))
        self.assertEqual(c.config_for(example())['n_zones'],3)

    def test_roundtrip_and_analysis_excel(self):
        p = self.p
        c.edit_boundaries(p,[30],'移动边界')
        p.cluster_config['zone_names'] = {'1':'深部带','2':'浅部带'}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'项目.pollen'
            save_project(p,path)
            restored = load_project(path)
            self.assertEqual(restored,p)
            self.assertTrue(c.result_is_current(restored))
            out = Path(temp)/'分析.xlsx'
            c.export_analysis(restored,out)
            wb = load_workbook(out,data_only=True)
            self.assertEqual(wb['样品分带']['D2'].value,'Ⅱ')
            self.assertEqual(wb['样品分带']['D5'].value,'Ⅰ')
            self.assertEqual(wb['边界区间']['A2'].value,30)
            self.assertEqual(wb['边界区间']['B2'].value,20)
            self.assertEqual(wb['边界区间']['C2'].value,50)
            self.assertEqual(wb['人工调整记录'].max_row,2)
            wb.close()

    def test_overlay_uses_true_sample_positions_and_preserves_pollen_widths(self):
        p = self.p
        p.cluster_config['n_zones'] = 2
        with_tree = build_diagram(p)
        self.assertEqual(len(with_tree.axes),3)
        self.assertEqual(set(with_tree.extra_axes),{'tree','zones'})
        self.assertEqual(with_tree.extra_axes['tree']._pollen_tree_leaf_y,[10,20,50,90])
        np.testing.assert_allclose(with_tree.extra_axes['tree'].get_ylim(),with_tree.axes[0].get_ylim())
        lines = [l for l in with_tree.axes[0].lines if l.get_gid()=='pollen-zone-boundary']
        self.assertEqual(list(lines[0].get_ydata()),[35,35])
        p.cluster_config.update(show_tree=False,show_zones=False,show_lines=False)
        plain = build_diagram(p)
        np.testing.assert_allclose(with_tree.layout['panel_widths_cm'],plain.layout['panel_widths_cm'])
        self.assertGreater(with_tree.figure.get_figwidth(),plain.figure.get_figwidth())

    def test_broken_stick_reference(self):
        result = {'merges':[{'height':.5},{'height':1},{'height':4.5}]}
        rows = c.broken_stick(result)
        self.assertEqual([r['groups'] for r in rows],[2,3])
        self.assertAlmostEqual(rows[0]['observed'],3.5)
        self.assertAlmostEqual(rows[0]['expected'],2.75)
        self.assertAlmostEqual(rows[1]['expected'],1.25)

    def test_tree_zoom_does_not_crop_exports_and_stale_analysis_is_blocked(self):
        p = self.p
        p.cluster_config['n_zones'] = 2
        diagram = build_diagram(p)
        tree = diagram.extra_axes['tree']
        with tempfile.TemporaryDirectory() as temp:
            before,after = Path(temp)/'原图.png',Path(temp)/'缩放后导出.png'
            export_diagram(p,diagram,before,dpi=100)
            tree.set_xlim(.1,.2)
            tree.set_ylim(70,30)
            diagram.figure.set_size_inches(5,3)
            export_diagram(p,diagram,after,dpi=100)
            self.assertEqual(before.read_bytes(),after.read_bytes())
            np.testing.assert_allclose(tree.get_xlim(),[.1,.2])
            np.testing.assert_allclose(tree.get_ylim(),[70,30])
            np.testing.assert_allclose(diagram.figure.get_size_inches(),[5,3])
            self.assertEqual(len(list(Path(temp).iterdir())),2)
        p.samples[0].counts[p.taxa[0].id] = '89'
        with self.assertRaisesRegex(ValueError,'重新计算'):
            build_diagram(p)


if __name__=='__main__':
    unittest.main()
