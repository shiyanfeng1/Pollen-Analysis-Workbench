"""Render the repository's vector application icon into Windows icon sizes."""
import sys
import os
from pathlib import Path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'.deps'))
from PySide6.QtSvg import QSvgRenderer
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtGui import QImage,QPainter,QGuiApplication,QTextDocument,QFont
from PySide6.QtCore import Qt
from PIL import Image

app = QGuiApplication.instance() or QGuiApplication([])
canvas = QImage(256,256,QImage.Format_ARGB32)
canvas.fill(Qt.transparent)
painter = QPainter(canvas)
renderer = QSvgRenderer(str(root/'assets/pollen.svg'))
if not renderer.isValid():
    raise ValueError('应用图标无效')
renderer.render(painter)
painter.end()
png = root/'assets/pollen.png'
if not canvas.save(str(png)):
    raise OSError('无法生成应用图标')
with Image.open(png) as source:
    source.save(root/'assets/pollen.ico',format='ICO',sizes=[(n,n) for n in (16,24,32,48,64,128,256)])
print('已生成应用图标。')
manual = QTextDocument()
manual.setDefaultFont(QFont('Microsoft YaHei',11))
manual.setMarkdown((root/'README.md').read_text(encoding='utf-8'))
guide_root = root/'使用指南'
guide_root.mkdir(exist_ok=True)
(guide_root/'使用说明.html').write_text(manual.toHtml(),encoding='utf-8')
print('已生成离线完整使用说明。')
