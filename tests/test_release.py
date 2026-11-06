import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from pollen.core import example,save_project,load_project
from pollen.ui import Window
from pollen.help_ui import GuideDialog,AboutDialog
from pollen import __version__


class FinalDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_save_as_preserves_original_and_cancel_keeps_unsaved_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            original,copy = Path(folder)/'原项目.pollen',Path(folder)/'项目副本.pollen'
            p = example()
            save_project(p,original)
            before = original.read_bytes()
            window = Window()
            try:
                window.replace(p,str(original))
                window.table.item(0,0).setText('70')
                self.assertTrue(window.dirty)
                with patch('pollen.ui.QFileDialog.getSaveFileName',return_value=(str(copy),'')):
                    self.assertTrue(window.save_as())
                self.assertEqual(original.read_bytes(),before)
                restored = load_project(copy)
                self.assertEqual(restored.samples[0].counts[restored.taxa[0].id],'70')
                self.assertEqual(window.path,str(copy))
                self.assertFalse(window.dirty)
                window.table.item(0,0).setText('71')
                with patch('pollen.ui.QFileDialog.getSaveFileName',return_value=('','')):
                    self.assertFalse(window.save_as())
                self.assertEqual(window.path,str(copy))
                self.assertTrue(window.dirty)
                self.assertEqual(load_project(copy),restored)
            finally:
                window.dirty = False
                window.close()

    def test_failed_save_keeps_original_file_and_current_project(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'保护原项目.pollen'
            p = example()
            save_project(p,path)
            before = path.read_bytes()
            p.samples[0].counts[p.taxa[0].id] = '99'
            with patch('pollen.core.os.replace',side_effect=PermissionError('占用')):
                with self.assertRaises(PermissionError):
                    save_project(p,path)
            self.assertEqual(path.read_bytes(),before)
            self.assertFalse(list(Path(folder).glob('*.tmp')))
            window = Window()
            try:
                window.replace(p,str(path),dirty=True)
                with patch('pollen.ui.save_project',side_effect=PermissionError()),patch.object(window,'error'):
                    self.assertFalse(window.save_to(str(Path(folder)/'失败副本.pollen')))
                self.assertEqual(window.path,str(path))
                self.assertTrue(window.dirty)
                self.assertEqual(window.project.samples[0].counts[p.taxa[0].id],'99')
            finally:
                window.dirty = False
                window.close()

    def test_offline_help_about_and_version_are_available(self):
        guide = GuideDialog()
        self.assertIn('12.5',guide.browser.toPlainText())
        self.assertIn('Ctrl+Shift+S',guide.browser.toPlainText())
        self.assertIn('快速入门',guide.browser.toPlainText())
        guide.close()
        about = AboutDialog()
        from PySide6.QtWidgets import QLabel
        self.assertTrue(any(__version__ in label.text() for label in about.findChildren(QLabel)))
        about.close()
        window = Window()
        try:
            self.assertIn(__version__,window.windowTitle())
            menus = {action.text():action.menu() for action in window.menuBar().actions()}
            self.assertIn('文件',menus)
            self.assertIn('帮助',menus)
            self.assertIn('另存为',[action.text() for action in menus['文件'].actions()])
        finally:
            window.close()


if __name__=='__main__':
    unittest.main()
