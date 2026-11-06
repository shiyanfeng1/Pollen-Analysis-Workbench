import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.deps'))
import csv
import tempfile
import unittest
from openpyxl import load_workbook
from pollen.core import calculate, example, save_project, load_project, count
from pollen.excel import write_excel, read_table


class Calculations(unittest.TestCase):
    def test_manual_reference_and_denominator_selection(self):
        p = example()
        rows, issues = calculate(p)
        self.assertEqual(rows[0]['base'], 200)
        self.assertEqual(rows[0]['percentages'][p.taxa[0].id], 30)
        self.assertEqual(rows[0]['percentages'][p.taxa[5].id], 10)
        p.taxa[5].selected = True
        rows, _ = calculate(p)
        self.assertEqual(rows[0]['base'], 220)
        self.assertAlmostEqual(rows[0]['percentages'][p.taxa[5].id], 100 / 11)
        self.assertAlmostEqual(sum(rows[0]['percentages'][t.id] for t in p.taxa if t.selected), 100)

    def test_missing_zero_and_invalid_are_distinct(self):
        p = example()
        s = p.samples[0]
        s.counts[p.taxa[5].id] = ''
        rows, _ = calculate(p)
        self.assertEqual(rows[0]['base'], 200)
        self.assertIsNone(rows[0]['percentages'][p.taxa[5].id])
        s.counts[p.taxa[0].id] = ''
        rows, _ = calculate(p)
        self.assertIsNone(rows[0]['base'])
        self.assertTrue(all(v is None for v in rows[0]['percentages'].values()))
        for t in p.taxa:
            s.counts[t.id] = '0'
        rows, _ = calculate(p)
        self.assertEqual(rows[0]['base'], 0)
        self.assertIsNone(rows[0]['percentages'][p.taxa[0].id])
        for invalid in ('-1', '1.5', 'NaN', 'inf', '文字'):
            with self.assertRaises(ValueError):
                count(invalid)

    def test_unsorted_duplicate_and_no_selection(self):
        p = example()
        p.samples[1].axis = '5'
        p.samples[2].axis = '5'
        for t in p.taxa:
            t.selected = False
        _, issues = calculate(p)
        self.assertTrue(any(i[0] == '错误' for i in issues))
        self.assertTrue(any('重复' in i[3] for i in issues))
        self.assertTrue(any('递增' in i[3] for i in issues))


class Persistence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_project_roundtrip(self):
        p = example()
        p.schemes['陆生'] = [t.id for t in p.taxa if t.selected]
        p.samples[0].counts[p.taxa[0].id] = ''
        path = self.root / '中文项目.pollen'
        save_project(p, path)
        restored = load_project(path)
        self.assertEqual(p, restored)
        self.assertEqual(calculate(p), calculate(restored))

    def test_excel_headers_results_and_roundtrip(self):
        p = example()
        path = self.root / '中文.xlsx'
        write_excel(p, path)
        wb = load_workbook(path)
        self.assertEqual(len(wb.sheetnames), 6)
        self.assertEqual(wb['百分比结果']['B3'].value, 30)
        self.assertEqual(wb['基数明细']['B2'].value, 200)
        self.assertIn('B1:C1', str(wb['原始计数'].merged_cells))
        wb.close()
        restored = read_table(path, '原始计数')
        self.assertEqual([t.selected for t in p.taxa], [t.selected for t in restored.taxa])
        original = calculate(p)[0]
        recovered = calculate(restored)[0]
        self.assertEqual([r['base'] for r in original], [r['base'] for r in recovered])
        self.assertEqual(list(original[0]['percentages'].values()), list(recovered[0]['percentages'].values()))

    def test_formula_is_rejected(self):
        path = self.root / 'formula.xlsx'
        write_excel(example(), path)
        wb = load_workbook(path)
        wb['原始计数']['B3'] = '=SUM(1,2)'
        wb.save(path)
        wb.close()
        with self.assertRaisesRegex(ValueError, '公式'):
            read_table(path)

    def test_csv_merged_header_fill_and_missing(self):
        path = self.root / 'input.csv'
        with path.open('w', encoding='utf-8-sig', newline='') as f:
            csv.writer(f).writerows([['深度/年代', '木本', '', '水生'],
                                     ['', '松属', '栎属', '香蒲属'], [10, 60, 40, '']])
        p = read_table(path)
        self.assertEqual([t.group for t in p.taxa], ['木本', '木本', '水生'])
        self.assertFalse(any(t.selected for t in p.taxa))
        self.assertEqual(p.samples[0].counts[p.taxa[-1].id], '')


if __name__ == '__main__':
    unittest.main()
