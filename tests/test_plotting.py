import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.deps'))
import copy
import json
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
import numpy as np
from PIL import Image

from pollen.core import example, save_project, load_project, Sample, Taxon, Project, calculate
from pollen.plotting import (build_diagram, prepare, config_for, GROUP_COLORS,
                             export_diagram, save_style, load_style, diagram_example,
                             build_diagrams, export_document)


class Plotting(unittest.TestCase):
    def setUp(self):
        self.project = example()
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_user_defaults_and_color_order(self):
        d = build_diagram(self.project)
        self.assertEqual(list(GROUP_COLORS.values()), ['#67A583', '#A2C986', '#49C2D9', '#7B95C6', '#FFC1A6', '#F47254'])
        self.assertEqual(d.data.config['angle'], 45)
        self.assertEqual(d.data.config['x_mode'], 'auto')
        self.assertTrue(d.axes[0].yaxis_inverted())
        self.assertEqual(d.axes[0].lines[0].get_xdata()[0], 30)
        for t in d.data.taxa:
            self.assertEqual(d.limits[t.id]['color'], GROUP_COLORS[t.group])

    def test_visibility_never_changes_denominator_and_scales_can_exceed_100(self):
        p = self.project
        cfg = config_for(p)
        cfg['taxa'][p.taxa[0].id]['visible'] = False
        p.plot_config = cfg
        d = build_diagram(p)
        self.assertNotIn(p.taxa[0], d.data.taxa)
        self.assertEqual(d.data.bases[0], 200)
        self.assertEqual(d.data.values[p.taxa[1].id][0], 20)
        p.samples[0].counts[p.taxa[5].id] = '500'
        d = build_diagram(p)
        self.assertGreaterEqual(d.limits[p.taxa[5].id]['max'], 250)

    def test_real_spacing_sort_without_mutating_rows_and_duplicate_rejected(self):
        p = self.project
        p.samples[0].axis, p.samples[1].axis, p.samples[2].axis = '50', '10', '20'
        original = copy.deepcopy(p.samples)
        d = build_diagram(p)
        np.testing.assert_equal(d.data.y, [10, 20, 50])
        np.testing.assert_equal(d.axes[0].lines[0].get_ydata(), [10, 20, 50])
        self.assertEqual(p.samples, original)
        self.assertEqual(d.data.indices, [1, 2, 0])
        p.samples[2].axis = '10'
        with self.assertRaisesRegex(ValueError, '重复'):
            prepare(p)

    def test_missing_values_split_line_and_fill_geometry(self):
        p = self.project
        t = p.taxa[5]  # aquatic taxon not in denominator
        p.samples[1].counts[t.id] = ''
        for depth, value in [(40, '20'), (50, '10')]:
            values = p.samples[0].counts.copy()
            values[t.id] = value
            p.samples.append(Sample(str(depth), values))
        d = build_diagram(p)
        ax = d.axes[d.data.taxa.index(t)]
        self.assertTrue(np.isnan(ax.lines[0].get_xdata()[1]))
        # No polygon may bridge the missing row at depth 20.
        for path in ax.collections[0].get_paths():
            y = path.vertices[:, 1]
            self.assertFalse(min(y) < 20 < max(y))
        self.assertEqual(d.data.bases[1], 200)
        p.samples[1].counts[p.taxa[0].id] = ''
        prepared = prepare(p)
        self.assertIsNone(prepared.bases[1])
        self.assertTrue(all(np.isnan(v[1]) for v in prepared.values.values()))

    def test_png_svg_pdf_full_export_and_stale_guard(self):
        p = self.project
        d = build_diagram(p)
        full_x = d.axes[0].get_xlim()
        d.axes[0].set_xlim(10, 15)  # inspection zoom must not crop scientific export
        for suffix in ('png', 'svg', 'pdf'):
            path = self.root / f'图谱.{suffix}'
            export_diagram(p, d, path, dpi=100)
            self.assertGreater(path.stat().st_size, 1000)
            self.assertFalse(path.with_suffix(path.suffix + '.plot.json').exists())
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['图谱.pdf', '图谱.png', '图谱.svg'])
        self.assertEqual(d.axes[0].get_xlim(), (10, 15))
        self.assertEqual(d.figure._pollen_limits[0][0], full_x)
        with Image.open(self.root / '图谱.png') as image:
            self.assertAlmostEqual(image.width, 18.3/2.54*100, delta=1)
        svg = ET.parse(self.root / '图谱.svg')
        self.assertFalse(any(e.tag.endswith('}image') for e in svg.iter()))
        self.assertTrue(any(e.tag.endswith('}text') for e in svg.iter()))
        self.assertTrue((self.root / '图谱.pdf').read_bytes().startswith(b'%PDF'))
        p.samples[0].counts[p.taxa[0].id] = '99'
        with self.assertRaisesRegex(ValueError, '更新'):
            export_diagram(p, d, self.root / 'stale.png')

    def test_project_legacy_and_style_reuse_by_name(self):
        p = self.project
        p.plot_config = config_for(p)
        p.plot_config['taxa'][p.taxa[0].id]['exaggeration'] = 5
        p.plot_config['taxa'][p.taxa[1].id]['visible'] = False
        path = self.root / 'settings.pollen'
        save_project(p, path)
        self.assertEqual(load_project(path), p)
        legacy = json.loads(path.read_text(encoding='utf-8'))
        del legacy['data']['plot_config']
        path.write_text(json.dumps(legacy), encoding='utf-8')
        self.assertEqual(config_for(load_project(path))['angle'], 45)
        style = self.root / 'style.pollenstyle'
        save_style(p, style)
        another = example()
        applied = load_style(another, style)
        self.assertEqual(applied['taxa'][another.taxa[0].id]['exaggeration'], 5)
        self.assertFalse(applied['taxa'][another.taxa[1].id]['visible'])
        self.assertEqual(another.plot_config, {})

    def test_manual_upper_bound_cannot_silently_clip_and_exaggeration_preserves_values(self):
        p = self.project
        p.plot_config = config_for(p)
        opts = p.plot_config['taxa'][p.taxa[0].id]
        opts['x_max'] = 10
        with self.assertRaisesRegex(ValueError, '上限'):
            build_diagram(p)
        opts['x_max'], opts['exaggeration'] = None, 5
        d = build_diagram(p)
        self.assertEqual(d.data.values[p.taxa[0].id][0], 30)
        self.assertGreaterEqual(d.limits[p.taxa[0].id]['max'], 200)

    def test_dense_diagram_tick_labels_remain_readable(self):
        d = build_diagram(diagram_example())
        renderer = d.figure.canvas.get_renderer()
        for ax in d.axes:
            boxes = [label.get_window_extent(renderer) for label in ax.get_xticklabels() if label.get_text()]
            boxes.sort(key=lambda box: box.x0)
            self.assertTrue(all(left.x1 < right.x0 for left, right in zip(boxes, boxes[1:])))

    def test_many_taxa_share_one_bounded_sheet_with_regular_y_ticks(self):
        p = example()
        for i in range(45):
            t = Taxon(f'Long Latin taxon {i+1}', '草本')
            p.taxa.append(t)
            for sample in p.samples:
                sample.counts[t.id] = str(1+i%5)
        p.samples[0].axis, p.samples[1].axis, p.samples[2].axis = '0.135', '0.249', '0.433'
        pages = build_diagrams(p)
        self.assertEqual(len(pages), 1)
        self.assertEqual(sum(len(d.axes) for d in pages), 53)
        self.assertEqual(len({t.id for d in pages for t in d.data.taxa}), 53)
        ticks = pages[0].axes[0].get_yticks()
        np.testing.assert_allclose(np.diff(ticks), .05)
        for d in pages:
            self.assertGreaterEqual(d.figure.get_figwidth()*2.54, 18.3)
            self.assertLessEqual(d.figure.get_figwidth()*2.54, 25.5)
            self.assertAlmostEqual(d.figure.get_figheight()*2.54, 12.5)
            np.testing.assert_allclose(d.axes[0].get_yticks(), ticks)
            np.testing.assert_allclose(d.axes[0].lines[0].get_ydata(), [.135, .249, .433])
        paths = export_document(p, pages, self.root/'多幅.pdf', dpi=100)
        self.assertFalse((self.root/'多幅.pdf.plot.json').exists())
        self.assertTrue(paths[0].exists())
        self.assertEqual(len(re.findall(rb'/Type\s*/Page\b', paths[0].read_bytes())), 1)
        paths = export_document(p, pages, self.root/'多幅.png', dpi=100)
        self.assertEqual(len(paths), 1)
        self.assertTrue(all(path.exists() for path in paths))

    def test_old_overwide_layout_upgrades_without_losing_scientific_settings(self):
        p = self.project
        p.plot_config = {'width_cm': 160, 'height_cm': 19, 'auto_width': True,
                         'font_size': 15, 'line_width': 1, 'y_step': .05, 'title': '已有项目',
                         'taxa': {p.taxa[0].id: {'visible': False}}}
        cfg = config_for(p)
        self.assertEqual(cfg['width_cm'], 18.3)
        self.assertEqual(cfg['font_size'], 7.5)
        self.assertEqual(cfg['line_width'], .45)
        self.assertEqual(cfg['title'], '已有项目')
        self.assertEqual(cfg['y_step'], .05)
        self.assertFalse(cfg['taxa'][p.taxa[0].id]['visible'])
        self.assertFalse(cfg['paginate'])
        self.assertEqual(cfg['width_mode'], 'abundance')

    def test_sixty_taxa_low_abundance_columns_are_narrower_without_changing_data(self):
        from pollen.demo import sixty_taxa_example
        p = sixty_taxa_example()
        original = copy.deepcopy(p)
        d = build_diagram(p)
        self.assertEqual(len(d.axes), 60)
        self.assertEqual(d.page_count, 1)
        self.assertEqual(d.layout['name_angle'], 90)
        by_name = {t.name: d.limits[t.id] for t in d.data.taxa}
        self.assertGreater(by_name['Pinus']['panel_width_cm'], 4*by_name['Abies']['panel_width_cm'])
        self.assertTrue(by_name['Abies']['compact_ticks'])
        self.assertFalse(by_name['Pinus']['compact_ticks'])
        expected = calculate(p)[0]
        self.assertEqual(p, original)
        self.assertEqual(d.data.bases, [r['base'] for r in expected])
        for t in p.taxa:
            np.testing.assert_allclose(d.data.values[t.id], [r['percentages'][t.id] for r in expected])
        render = d.figure.canvas.get_renderer()
        # Adjacent vertical names and compact numeric labels must remain distinct.
        names = [next(text for text in ax.texts if text.get_text() == t.name).get_window_extent(render)
                 for ax,t in zip(d.axes,d.data.taxa)]
        self.assertTrue(all(left.x1 < right.x0 for left,right in zip(names,names[1:])))
        for ax in d.axes:
            boxes = [label.get_window_extent(render) for label in ax.get_xticklabels() if label.get_text()]
            self.assertTrue(all(left.x1 < right.x0 for left,right in zip(boxes,boxes[1:])))

    def test_sixty_one_taxa_requires_explicit_selection_instead_of_silent_omission(self):
        from pollen.demo import sixty_taxa_example
        p = sixty_taxa_example()
        extra = Taxon('额外类群', '草本')
        p.taxa.append(extra)
        for s in p.samples:
            s.counts[extra.id] = '1'
        with self.assertRaisesRegex(ValueError, '最多支持 60'):
            build_diagram(p)
        p.plot_config = {'taxa': {extra.id: {'visible': False}}}
        self.assertEqual(len(build_diagram(p).axes), 60)

    def test_equal_width_option_and_legacy_paginated_settings(self):
        p = self.project
        p.plot_config = {'layout_version': 2, 'paginate': True, 'panels_per_page': 2}
        self.assertEqual(len(build_diagrams(p)), 1)
        p.plot_config = config_for(p)
        p.plot_config['width_mode'] = 'equal'
        d = build_diagram(p)
        np.testing.assert_allclose(d.layout['panel_widths_cm'], d.layout['panel_widths_cm'][0])


if __name__ == '__main__':
    unittest.main()
