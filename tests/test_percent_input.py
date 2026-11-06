import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
import csv
import copy
import json
import tempfile
import unittest
import numpy as np
from openpyxl import Workbook,load_workbook

from pollen.core import Project,Taxon,Sample,calculate,percentage,example,save_project,load_project
from pollen.excel import read_table,write_excel,inspect_table,normalize_percentage,excel_uses_percent
from pollen import pca,clustering,occurrence
from pollen.plotting import build_diagram


def percent_profile():
    taxa = [Taxon('Pinus','木本'),Taxon('Artemisia','草本'),Taxon('Aquatic','水生')]
    rows = [[12.5,7.5,.002],[18.25,0,2.5],[0,28.75,5.5],[15,15,.5]]
    return Project(taxa=taxa,samples=[Sample(str(10*(i+1)),{t.id:str(v) for t,v in zip(taxa,row)}) for i,row in enumerate(rows)],
                   data_type='percentages')


class PercentageValues(unittest.TestCase):
    def test_preserve_non_closed_totals_zero_missing_and_no_fake_base(self):
        p = percent_profile()
        p.samples[0].counts[p.taxa[1].id] = ''
        rows,issues = calculate(p)
        self.assertFalse(any(i[0]=='错误' for i in issues))
        self.assertTrue(all(row['base'] is None for row in rows))
        self.assertEqual(rows[0]['percentages'][p.taxa[0].id],12.5)
        self.assertIsNone(rows[0]['percentages'][p.taxa[1].id])
        self.assertEqual(rows[1]['percentages'][p.taxa[1].id],0)
        self.assertFalse(any('低于' in i[3] for i in issues))
        for t in p.taxa:
            t.selected = True
        self.assertEqual(calculate(p)[0],rows)
        p.samples[0].counts[p.taxa[0].id] = 125
        self.assertEqual(calculate(p)[0][0]['percentages'][p.taxa[0].id],125)
        self.assertTrue(any('超过 100%' in i[3] for i in calculate(p)[1]))

    def test_percentage_parser_and_unit_rules(self):
        self.assertEqual(float(percentage('12.5%')),12.5)
        self.assertEqual(float(percentage(' 12.5％ ')),12.5)
        self.assertEqual(float(percentage('0')),0)
        self.assertIsNone(percentage(''))
        for invalid in ('-1','NaN','inf','文字','%'):
            with self.assertRaises(ValueError):
                percentage(invalid)
        for value,unit,formatted in [(.125,'fraction',False),(.125,'percent',True),(.125,'fraction',True),('12.5%','fraction',True)]:
            self.assertEqual(float(normalize_percentage(value,unit,formatted)),12.5)
        self.assertEqual(float(normalize_percentage(.125,'percent')), .125)
        self.assertTrue(excel_uses_percent('0.00%'))
        self.assertFalse(excel_uses_percent('0.00\\%'))
        self.assertFalse(excel_uses_percent('0.00"%"'))

    def test_all_analysis_consumers_use_the_same_original_values(self):
        p = percent_profile()
        raw = np.array([[12.5,7.5,.002],[18.25,0,2.5],[0,28.75,5.5],[15,15,.5]])
        self.assertEqual(len(pca.config_for(p)['taxa_ids']),3)
        self.assertEqual(len(clustering.config_for(p)['taxa_ids']),3)
        np.testing.assert_allclose(clustering.prepare_matrix(p)['percentages'],raw)
        np.testing.assert_allclose(pca.prepare_matrix(p)['percentages'],raw)
        diagram = build_diagram(p)
        for j,t in enumerate(p.taxa):
            np.testing.assert_allclose(diagram.data.values[t.id],raw[:,j])
        found = occurrence.prepare(p)
        np.testing.assert_equal(found.states,(raw>0).astype(int))
        self.assertTrue(all(v is None for v in found.counts.ravel()))
        self.assertEqual(found.config['mode'],'percentages')
        p.occurrence_config['mode'] = 'counts'
        with self.assertRaisesRegex(ValueError,'没有原始计数'):
            occurrence.prepare(p)

    def test_missing_percentage_is_not_count_missing_and_not_zero(self):
        p = percent_profile()
        p.samples[0].counts[p.taxa[0].id] = ''
        d = occurrence.prepare(p)
        self.assertEqual(d.states[0,0],-3)
        self.assertEqual(occurrence.state_label(-3),'百分比缺失')
        self.assertEqual(d.states[0,2],1)
        with self.assertRaisesRegex(ValueError,'缺失'):
            pca.prepare_matrix(p)


class PercentageIO(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def workbook(self):
        wb = Workbook()
        ws = wb.active
        ws.title = '数据'
        ws.append(['深度/年代','木本','草本','水生'])
        ws.append(['','Pinus','Artemisia','Aquatic'])
        ws.append([10,.125,12.5,'12.5%'])
        ws['B3'].number_format = '0.00%'
        ws.append([20,0,None,.002])
        path = self.root/'含百分比格式.xlsx'
        wb.save(path)
        wb.close()
        return path

    def test_excel_formatted_cells_and_text_percent_are_not_multiplied_twice(self):
        path = self.workbook()
        hint = inspect_table(path)
        self.assertEqual(hint['data_type'],'percentages')
        p = read_table(path,data_type='percentages',percentage_unit='percent')
        np.testing.assert_allclose(list(calculate(p)[0][0]['percentages'].values()),[12.5,12.5,12.5])
        self.assertIsNone(calculate(p)[0][1]['percentages'][p.taxa[1].id])
        self.assertEqual(p.source_info['excel_percentage_cells'],1)

    def test_plain_fraction_csv_and_explicit_percent_literals(self):
        path = self.root/'比例.csv'
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            csv.writer(f).writerows([['深度/年代','木本','草本','水生'],['','Pinus','Artemisia','Aquatic'],[10,.125,'12.5%',0],[20,'',.00002,.2]])
        p = read_table(path,data_type='percentages',percentage_unit='fraction')
        rows,issues = calculate(p)
        np.testing.assert_allclose(list(rows[0]['percentages'].values()),[12.5,12.5,0])
        self.assertEqual(rows[1]['percentages'][p.taxa[1].id],.002)
        self.assertFalse(any(i[0]=='错误' for i in issues))

    def test_percentage_export_reimport_keeps_units_and_does_not_invent_counts(self):
        p = percent_profile()
        p.source_info = {'numeric_unit':'fraction','stored_unit':'percent'}
        path = self.root/'结果.xlsx'
        write_excel(p,path)
        wb = load_workbook(path)
        self.assertIn('导入百分比',wb.sheetnames)
        self.assertNotIn('原始计数',wb.sheetnames)
        self.assertNotIn('基数明细',wb.sheetnames)
        self.assertEqual(wb['导入百分比']['B3'].value,12.5)
        wb.close()
        restored = read_table(path)
        self.assertEqual(restored.data_type,'percentages')
        # Excel assigns new internal IDs; match the actual values by column order.
        np.testing.assert_allclose(
            [list(row['percentages'].values()) for row in calculate(restored)[0]],
            [list(row['percentages'].values()) for row in calculate(p)[0]])
        self.assertTrue(all(row['base'] is None for row in calculate(restored)[0]))
        # A derived percentage sheet from a count workbook is also a percentage input.
        write_excel(example(),path)
        derived = read_table(path,'百分比结果')
        self.assertEqual(derived.data_type,'percentages')
        self.assertEqual(calculate(derived)[0][0]['percentages'][derived.taxa[0].id],30)

    def test_projects_and_analysis_exports_keep_input_type_and_unknown_base(self):
        p = percent_profile()
        p.cluster_result = clustering.run_analysis(p)
        p.pca_result = pca.run_analysis(p)
        path = self.root/'百分比项目.pollen'
        save_project(p,path)
        restored = load_project(path)
        self.assertEqual(restored,p)
        self.assertTrue(pca.result_is_current(restored))
        self.assertTrue(clustering.result_is_current(restored))
        occurrence.export_analysis(restored,self.root/'出现.xlsx')
        wb = load_workbook(self.root/'出现.xlsx',data_only=True)
        self.assertNotIn('原始计数',wb.sheetnames)
        self.assertIn('导入百分比',wb.sheetnames)
        self.assertEqual(wb['导入百分比']['C2'].value,12.5)
        wb.close()
        clustering.export_analysis(restored,self.root/'分带.xlsx')
        pca.export_analysis(restored,self.root/'PCA.xlsx')
        restored.data_type = 'counts'
        self.assertFalse(pca.result_is_current(restored))
        self.assertFalse(clustering.result_is_current(restored))

    def test_legacy_count_projects_keep_existing_cached_analyses(self):
        p = example()
        p.cluster_result = clustering.run_analysis(p)
        p.pca_result = pca.run_analysis(p)
        path = self.root/'旧项目.pollen'
        save_project(p,path)
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['data'].pop('data_type')
        payload['data'].pop('source_info')
        path.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        restored = load_project(path)
        self.assertEqual(restored.data_type,'counts')
        self.assertTrue(pca.result_is_current(restored))
        self.assertTrue(clustering.result_is_current(restored))


if __name__=='__main__':
    unittest.main()
