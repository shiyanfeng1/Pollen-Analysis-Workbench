import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.deps'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import unittest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidgetItem
from pollen.ui import Window
from pollen.core import example


class Desktop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = Window()
        self.window.replace(example())

    def tearDown(self):
        self.window.dirty = False
        self.window.close()

    def test_edit_calculate_select_group_and_result(self):
        w = self.window
        w.table.setItem(0, 0, QTableWidgetItem('100'))
        w.recalculate()
        self.assertEqual(w.results[0]['base'], 240)
        water = w.tree.topLevelItem(3)
        water.setCheckState(0, Qt.Checked)
        w.recalculate()
        self.assertEqual(w.results[0]['base'], 260)
        w.tabs.setCurrentIndex(1)
        self.assertEqual(w.axis_table.item(0, 1).text(), '260')
        self.assertEqual(w.table.item(0, 0).text(), '38.46')

    def test_paste_adds_rows_and_keeps_missing(self):
        w = self.window
        w.table.setCurrentCell(2, 0)
        QApplication.clipboard().setText('1\t0\n2\t')
        w.paste_cells(w.table)
        self.assertEqual(len(w.project.samples), 4)
        self.assertEqual(w.project.samples[2].counts[w.project.taxa[1].id], '0')
        self.assertEqual(w.project.samples[3].counts[w.project.taxa[1].id], '')

    def test_percentage_unit_preview_scales_plain_numbers_only_once(self):
        from pollen.input_ui import DataTypeDialog
        dialog = DataTypeDialog(data_type='percentages',preview=[
            ('普通数字',.125,False),('Excel百分比',.125,True),('带百分号','12.5%',False)])
        self.assertEqual([dialog.table.item(i,3).text() for i in range(3)],['0.125%','12.500%','12.5%'])
        dialog.unit_combo.setCurrentIndex(dialog.unit_combo.findData('fraction'))
        self.assertTrue(all(float(dialog.table.item(i,3).text().rstrip('%'))==12.5 for i in range(3)))
        dialog.close()

    def test_reinterpret_existing_table_as_percentages_and_disable_unknown_counts(self):
        from PySide6.QtCore import QTimer
        from pollen.input_ui import DataTypeDialog
        from pollen import clustering,pca
        w = self.window
        w.project.samples[0].counts[w.project.taxa[0].id] = '.125'
        def choose():
            dialog = self.app.activeModalWidget()
            self.assertIsInstance(dialog,DataTypeDialog)
            dialog.type_combo.setCurrentIndex(dialog.type_combo.findData('percentages'))
            dialog.unit_combo.setCurrentIndex(dialog.unit_combo.findData('fraction'))
            dialog.accept()
        QTimer.singleShot(30,choose)
        w.edit_data_type()
        self.assertEqual(w.project.data_type,'percentages')
        self.assertEqual(w.results[0]['percentages'][w.project.taxa[0].id],12.5)
        self.assertIsNone(w.results[0]['base'])
        self.assertFalse(w.tree.isEnabled())
        self.assertFalse(w.threshold.isEnabled())
        self.assertFalse(w.schemes.isEnabled())
        self.assertIn('百分比',w.tabs.tabText(0))
        self.assertEqual(w.occurrence_panel.mode.currentData(),'percentages')
        self.assertFalse(w.occurrence_panel.mode.model().item(0).isEnabled())
        w.tabs.setCurrentIndex(1)
        self.assertEqual(w.axis_table.columnCount(),1)
        self.assertTrue(w.plot_panel.draw(report_errors=False))
        self.assertTrue(w.occurrence_panel.draw(report_errors=False))
        self.run_cluster_worker()
        self.run_pca_worker()
        self.assertTrue(clustering.result_is_current(w.project))
        self.assertTrue(pca.result_is_current(w.project))

    def test_import_percentages_through_the_desktop_dialog(self):
        import tempfile
        from unittest.mock import patch
        from openpyxl import Workbook
        from PySide6.QtCore import QTimer
        from pollen.input_ui import DataTypeDialog
        w = self.window
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder)/'百分比.xlsx')
            wb = Workbook()
            ws = wb.active
            ws.append(['深度/年代','木本','草本'])
            ws.append(['','Pinus','Artemisia'])
            ws.append([10,.125,7.5])
            ws['B3'].number_format = '0.00%'
            wb.save(path)
            wb.close()
            def choose():
                dialog = self.app.activeModalWidget()
                self.assertIsInstance(dialog,DataTypeDialog)
                self.assertEqual(dialog.data_type,'percentages')
                self.assertEqual(float(dialog.table.item(0,3).text().rstrip('%')),12.5)
                dialog.accept()
            with patch('pollen.ui.QFileDialog.getOpenFileName',return_value=(path,'')), \
                 patch('pollen.ui.QInputDialog.getItem',return_value=('Sheet',True)), \
                 patch('pollen.ui.QMessageBox.information'):
                QTimer.singleShot(30,choose)
                w.import_file()
        self.assertEqual(w.project.data_type,'percentages')
        self.assertEqual(list(w.results[0]['percentages'].values()),[12.5,7.5])
        self.assertEqual(w.project.source_info['excel_percentage_cells'],1)

    def test_plot_controls_stale_tracking_and_independent_visibility(self):
        w = self.window
        panel = w.plot_panel
        w.workspace.setCurrentIndex(1)
        self.assertTrue(panel.draw(report_errors=False))
        self.assertFalse(panel.stale)
        panel.tree.topLevelItem(0).child(0).setCheckState(0, Qt.Unchecked)
        self.assertTrue(panel.stale)
        self.assertFalse(panel.export_button.isEnabled())
        self.assertTrue(w.project.taxa[0].selected)
        self.assertTrue(panel.draw(report_errors=False))
        self.assertEqual(len(panel.diagram.axes), 7)
        self.assertEqual(panel.diagram.data.bases[0], 200)
        w.workspace.setCurrentIndex(0)
        w.table.setItem(0, 0, QTableWidgetItem('100'))
        self.assertTrue(panel.stale)

    def test_toolbar_and_standard_buttons_are_chinese_and_all_taxa_share_one_sheet(self):
        from pollen.localization import install_chinese
        from pollen.plotting import diagram_example
        from PySide6.QtWidgets import QDialogButtonBox
        install_chinese(self.app)
        w = self.window
        w.replace(diagram_example())
        self.assertTrue(w.plot_panel.draw(report_errors=False))
        self.assertEqual(len(w.plot_panel.diagrams), 1)
        self.assertEqual(len(w.plot_panel.diagram.axes), 16)
        self.assertEqual(w.plot_panel.diagram.page_index, 0)
        self.assertEqual(w.plot_panel.width_mode.currentIndex(), 0)
        labels = [action.text() for action in w.plot_panel.toolbar.actions() if action.text()]
        self.assertEqual(labels, ['恢复全图', '返回', '前进', '平移', '框选缩放'])
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.assertIn('取消', buttons.button(QDialogButtonBox.Cancel).text())

    def run_cluster_worker(self):
        from PySide6.QtCore import QEventLoop, QTimer
        panel = self.window.cluster_panel
        panel.start_analysis()
        loop = QEventLoop()
        panel.thread.finished.connect(loop.quit)
        QTimer.singleShot(10000,loop.quit)
        if panel.thread.isRunning():
            loop.exec()
        self.app.processEvents()
        return panel

    def test_cluster_worker_manual_edits_and_oldest_numbering(self):
        from pollen import clustering as c
        panel = self.run_cluster_worker()
        self.assertTrue(c.result_is_current(self.window.project))
        panel.n_zones.setValue(2)
        self.assertEqual(panel.zones.item(0,0).text(),'Ⅰ')
        panel.change_boundaries([15,25],'手动边界')
        self.assertEqual(panel.mode.currentData(),'manual')
        self.assertEqual(panel.zones.rowCount(),3)
        self.assertEqual(c.zoning(self.window.project)['zone_ids'],[3,2,1])
        panel.restore_algorithm()
        self.assertEqual(c.zoning(self.window.project)['mode'],'algorithm')

    def test_cluster_selection_independent_and_data_edits_invalidate(self):
        from pollen import clustering as c
        panel = self.run_cluster_worker()
        original = self.window.project.cluster_result['input_signature']
        self.window.plot_panel.set_global('angle',90)
        self.assertTrue(c.result_is_current(self.window.project))
        self.assertEqual(self.window.project.cluster_result['input_signature'],original)
        self.window.table.setItem(0,0,QTableWidgetItem('61'))
        self.assertFalse(c.result_is_current(self.window.project))
        self.assertFalse(panel.export_button.isEnabled())

    def run_pca_worker(self):
        from PySide6.QtCore import QEventLoop,QTimer
        panel = self.window.pca_panel
        panel.start_analysis()
        loop = QEventLoop()
        panel.thread.finished.connect(loop.quit)
        QTimer.singleShot(10000,loop.quit)
        if panel.thread.isRunning():
            loop.exec()
        self.app.processEvents()
        return panel

    def test_pca_worker_display_changes_and_raw_data_invalidation(self):
        from pollen import pca
        panel = self.run_pca_worker()
        p = self.window.project
        self.assertTrue(pca.result_is_current(p))
        self.assertFalse(panel.stale)
        self.assertEqual(panel.diagram.layout['ellipses'],0)
        panel.set_option('arrow_top',2)
        self.assertTrue(pca.result_is_current(p))
        self.assertTrue(panel.stale)
        self.assertTrue(panel.draw(report_errors=False))
        self.assertEqual(len(panel.diagram.labels),2)
        self.window.table.setItem(0,0,QTableWidgetItem('61'))
        self.assertFalse(pca.result_is_current(p))
        self.assertFalse(panel.export_button.isEnabled())

    def test_pca_label_dragging_and_manual_display_do_not_change_analysis(self):
        from pollen import pca
        from matplotlib.backend_bases import MouseEvent
        panel = self.run_pca_worker()
        tid = self.window.project.pca_result['taxa_ids'][0]
        signature = self.window.project.pca_result['input_signature']
        panel.config.update(arrow_mode='manual',arrow_ids=[tid])
        panel.commit()
        self.assertTrue(panel.draw(report_errors=False))
        panel.canvas.draw()
        text = panel.diagram.labels[0]
        box = text.get_window_extent(panel.canvas.get_renderer())
        x,y = (box.x0+box.x1)/2,(box.y0+box.y1)/2
        panel.press(MouseEvent('button_press_event',panel.canvas,x,y,button=1))
        self.assertIsNotNone(panel.dragging)
        panel.motion(MouseEvent('motion_notify_event',panel.canvas,x+12,y-8,button=1))
        panel.release(MouseEvent('button_release_event',panel.canvas,x+12,y-8,button=1))
        self.assertIn(tid,panel.config['label_offsets'])
        self.assertTrue(pca.result_is_current(self.window.project))
        self.assertEqual(self.window.project.pca_result['input_signature'],signature)

    def test_pca_sample_dialog_keeps_counts_and_saves_custom_groups(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QTableWidget,QDialogButtonBox
        import copy
        panel = self.window.pca_panel
        before = copy.deepcopy(self.window.project.samples)
        def edit():
            dialog = self.app.activeModalWidget()
            table = dialog.findChild(QTableWidget)
            table.item(0,0).setCheckState(Qt.Unchecked)
            table.item(1,2).setText('自定义组甲')
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
        QTimer.singleShot(30,edit)
        panel.sample_dialog()
        self.assertEqual(self.window.project.samples,before)
        self.assertEqual(panel.config['excluded_sample_ids'],[before[0].id])
        self.assertEqual(panel.config['manual_groups'][before[1].id],'自定义组甲')

    def test_occurrence_counts_mode_without_base_and_percentages_require_base(self):
        w = self.window
        for t in w.project.taxa:
            t.selected = False
        w.refresh()
        panel = w.occurrence_panel
        self.assertTrue(panel.draw(report_errors=False),panel.status.text())
        self.assertEqual(panel.diagram.layout['row_sizes'],[8])
        self.assertFalse(panel.stale)
        panel.mode.setCurrentIndex(panel.mode.findData('percentages'))
        self.assertTrue(panel.stale)
        self.assertFalse(panel.export_button.isEnabled())
        self.assertFalse(panel.draw(report_errors=False))
        self.assertIn('选择计算基数',panel.status.text())
        panel.mode.setCurrentIndex(0)
        self.assertTrue(panel.draw(report_errors=False))

    def test_occurrence_manual_rows_keep_counts_and_cap(self):
        import copy
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QTableWidget,QDialogButtonBox
        panel = self.window.occurrence_panel
        before = copy.deepcopy(self.window.project.samples)
        panel.row_limit.setValue(5)
        self.assertTrue(panel.draw(report_errors=False))
        self.assertEqual(panel.diagram.layout['row_sizes'],[4,4])
        def edit():
            dialog = self.app.activeModalWidget()
            table = dialog.findChild(QTableWidget)
            for i in range(1,table.rowCount()):
                table.item(i,0).setCheckState(Qt.Checked if i==3 else Qt.Unchecked)
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
        QTimer.singleShot(30,edit)
        panel.row_dialog()
        self.assertEqual(panel.config['layout'],'manual')
        self.assertTrue(panel.draw(report_errors=False))
        self.assertEqual(panel.diagram.layout['row_sizes'],[3,5])
        self.assertEqual(self.window.project.samples,before)

    def test_occurrence_group_markers_and_independent_vertical_axis(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QComboBox,QDialogButtonBox
        from pollen.axis_ui import YAxisDialog
        panel = self.window.occurrence_panel
        def symbols():
            dialog = self.app.activeModalWidget()
            for i,combo in enumerate(dialog.findChildren(QComboBox)):
                combo.setCurrentIndex(i)
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
        QTimer.singleShot(30,symbols)
        panel.group_marker_dialog()
        self.assertTrue(panel.config['marker_by_group'])
        self.assertEqual(len(set(panel.config['group_markers'].values())),6)
        self.assertTrue(panel.draw(report_errors=False))
        def axis():
            dialog = self.app.activeModalWidget()
            self.assertIsInstance(dialog,YAxisDialog)
            for key,value in {'y_min':'0','y_max':'40','y_step':'5'}.items():
                dialog.entries[key].setText(value)
            dialog.reverse.setChecked(False)
            dialog.accept()
        QTimer.singleShot(30,axis)
        panel.axis_dialog()
        self.assertEqual(panel.diagram.layout['y_step'],5)
        self.assertFalse(panel.diagram.axes[0].yaxis_inverted())
        self.assertIsNone(self.window.project.plot_config.get('y_min'))


if __name__ == '__main__':
    unittest.main()
