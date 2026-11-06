import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
import copy
import tempfile
import unittest
import numpy as np
from openpyxl import load_workbook
from matplotlib.patches import Ellipse

from pollen.core import Project,Taxon,Sample,save_project,load_project
from pollen import pca,clustering
from pollen.pca_render import build_figure
from pollen.plotting import export_diagram


def profile():
    taxa = [Taxon('Pinus','木本',selected=True),Taxon('Artemisia','草本',selected=True),
            Taxon('Poaceae','草本',selected=True),Taxon('Aquatic','水生'),Taxon('Zero','未定类群')]
    counts = [[70,20,10,4,0],[60,30,10,8,0],[20,60,20,1,0],[10,70,20,6,0],[40,30,30,9,0],[50,40,10,2,0]]
    return Project(taxa=taxa,samples=[Sample(str((i+1)*10),{t.id:str(v) for t,v in zip(taxa,row)}) for i,row in enumerate(counts)])


class PrincipalComponents(unittest.TestCase):
    def test_hand_calculation_and_scores_correlations(self):
        x = np.array([[-2,-1],[-2,1],[2,-1],[2,1]],dtype=float)
        r = pca.decompose(x)
        np.testing.assert_allclose(r['eigenvalues'],[16/3,4/3])
        np.testing.assert_allclose(r['explained_ratio'],[.8,.2])
        np.testing.assert_allclose(r['scores'],x,atol=1e-14)
        np.testing.assert_allclose(r['loadings'],np.eye(2),atol=1e-14)
        np.testing.assert_allclose(r['correlations'],np.eye(2),atol=1e-14)

    def test_covariance_eigen_reference_reconstruction_and_standardization(self):
        for transform in ('sqrt','none'):
            for scale in (False,True):
                p = profile()
                p.pca_config = pca.config_for(p)
                p.pca_config.update(transform=transform,standardize=scale)
                inputs = pca.prepare_matrix(p)
                r = pca.run_analysis(p)
                x = inputs['matrix']
                eigen,vectors = np.linalg.eigh(np.cov(x,rowvar=False))
                order = np.argsort(eigen)[::-1][:r['rank']]
                np.testing.assert_allclose(r['eigenvalues'],eigen[order],rtol=1e-10,atol=1e-13)
                np.testing.assert_allclose(np.abs(r['loadings']),np.abs(vectors[:,order]),atol=1e-10)
                scores,loadings = np.array(r['scores']),np.array(r['loadings'])
                np.testing.assert_allclose(scores@loadings.T,x,atol=1e-13)
                np.testing.assert_allclose(np.cov(scores,rowvar=False),np.diag(r['eigenvalues']),atol=1e-13)
                direct = np.corrcoef(x.T,scores.T)[:x.shape[1],x.shape[1]:]
                np.testing.assert_allclose(r['correlations'],direct,atol=1e-12)
                self.assertAlmostEqual(sum(r['explained_ratio']),1)
                if scale:
                    np.testing.assert_allclose(x.std(axis=0,ddof=1),1,atol=1e-13)

    def test_selection_missing_zero_drop_and_original_denominator(self):
        p = profile()
        cfg = pca.config_for(p)
        cfg['taxa_ids'] = [t.id for t in p.taxa]
        inputs = pca.prepare_matrix(p,cfg)
        self.assertEqual(inputs['dropped'][0]['name'],'Zero')
        cfg['taxa_ids'] = [p.taxa[0].id,p.taxa[3].id]
        inputs = pca.prepare_matrix(p,cfg)
        np.testing.assert_allclose(inputs['percentages'][0],[70,4])
        p.samples[0].counts[p.taxa[3].id] = ''
        with self.assertRaisesRegex(ValueError,'空白不补零'):
            pca.prepare_matrix(p,cfg)
        cfg['excluded_sample_ids'] = [p.samples[0].id]
        self.assertEqual(len(pca.prepare_matrix(p,cfg)['matrix']),5)
        cfg['excluded_sample_ids'] = [s.id for s in p.samples[:4]]
        with self.assertRaisesRegex(ValueError,'3 至 2000'):
            pca.prepare_matrix(p,cfg)

    def test_insufficient_rank_and_constant_taxa_are_reported(self):
        with self.assertRaisesRegex(ValueError,'非零主成分'):
            pca.decompose(np.array([[-1,-2],[0,0],[1,2]],dtype=float))
        p = profile()
        p.pca_config = pca.config_for(p)
        p.pca_config['taxa_ids'] = [p.taxa[0].id,p.taxa[4].id]
        with self.assertRaisesRegex(ValueError,'不足两个'):
            pca.prepare_matrix(p)


class PcaWorkflow(unittest.TestCase):
    def setUp(self):
        self.p = profile()
        self.p.cluster_config = clustering.config_for(self.p)
        self.p.cluster_result = clustering.run_analysis(self.p)
        self.p.pca_config = pca.config_for(self.p)
        self.p.pca_result = pca.run_analysis(self.p)

    def test_grouping_tracks_manual_boundaries_without_changing_pca(self):
        p = self.p
        result = copy.deepcopy(p.pca_result)
        before = pca.grouping(p)
        self.assertIn('zone:1',before['keys'])
        clustering.edit_boundaries(p,[25],'人工移动')
        groups = pca.grouping(p)
        self.assertEqual(groups['keys'],['zone:2','zone:2','zone:1','zone:1','zone:1','zone:1'])
        self.assertEqual(p.pca_result,result)
        self.assertTrue(pca.result_is_current(p))
        p.pca_config.update(group_mode='manual',manual_groups={p.samples[0].id:'自定义甲'})
        self.assertEqual(pca.grouping(p)['labels']['manual:自定义甲'],'自定义甲')
        self.assertTrue(pca.result_is_current(p))
        p.pca_config['group_mode'] = 'none'
        self.assertEqual(set(pca.grouping(p)['keys']),{'ungrouped'})

    def test_missing_and_stale_zones_and_pca_input_invalidation(self):
        p = self.p
        p.cluster_config['transform'] = 'none'
        self.assertTrue(pca.result_is_current(p))
        with self.assertRaisesRegex(ValueError,'组合带结果已失效'):
            pca.grouping(p)
        p.cluster_result = {}
        self.assertTrue(pca.grouping(p)['warnings'])
        p.samples[0].counts[p.taxa[0].id] = '71'
        self.assertFalse(pca.result_is_current(p))

    def test_roundtrip_exports_preserve_numerical_definitions_and_literal_text(self):
        p = self.p
        p.pca_config.update(group_mode='manual',manual_groups={p.samples[0].id:'=1+1'})
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'项目.pollen'
            save_project(p,path)
            restored = load_project(path)
            self.assertEqual(restored,p)
            self.assertTrue(pca.result_is_current(restored))
            pca.export_analysis(restored,Path(temp)/'PCA.xlsx')
            wb = load_workbook(Path(temp)/'PCA.xlsx',data_only=False)
            self.assertEqual(wb['样品得分']['C2'].value,'=1+1')
            self.assertEqual(wb['样品得分']['C2'].data_type,'s')
            self.assertIn('类群载荷',wb.sheetnames)
            self.assertIn('类群相关系数',wb.sheetnames)
            self.assertAlmostEqual(wb['解释率']['C2'].value,p.pca_result['explained_ratio'][0]*100)
            wb.close()
        p.pca_result['scores'][0][0] += .1
        self.assertFalse(pca.result_is_current(p))

    def test_no_ellipses_full_exports_zoom_and_display_only_changes(self):
        p = self.p
        d = build_figure(p)
        self.assertEqual(len(d.axes),2)
        self.assertEqual(d.layout['ellipses'],0)
        self.assertFalse(any(isinstance(patch,Ellipse) for ax in d.axes for patch in ax.patches))
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            for ext in ('png','pdf','svg'):
                export_diagram(p,d,temp/f'PCA.{ext}',dpi=100)
            original = (temp/'PCA.png').read_bytes()
            d.axes[1].set_xlim(-.01,.01)
            d.axes[1].set_ylim(-.02,.02)
            export_diagram(p,d,temp/'缩放.png',dpi=100)
            self.assertEqual(original,(temp/'缩放.png').read_bytes())
            self.assertEqual(len(list(temp.iterdir())),4)
            p.pca_config['arrow_mode'] = 'manual'
            p.pca_config['arrow_ids'] = [p.pca_result['taxa_ids'][0]]
            p.pca_config['flip_x'] = True
            self.assertTrue(pca.result_is_current(p))
            with self.assertRaisesRegex(ValueError,'更新图谱'):
                export_diagram(p,d,temp/'过期.png')
        flipped = build_figure(p)
        self.assertEqual(len(flipped.labels),1)
        np.testing.assert_allclose(flipped.score_points[:,0],-d.score_points[:,0])
        np.testing.assert_allclose(flipped.labels[0]._pca_tip[0],-p.pca_result['correlations'][0][0])


if __name__=='__main__':
    unittest.main()
