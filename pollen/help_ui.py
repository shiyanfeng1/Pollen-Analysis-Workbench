"""Offline documentation and version information for the Windows distribution."""
import sys
from pathlib import Path
from PySide6.QtCore import QUrl,Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QDialog,QVBoxLayout,QTextBrowser,QDialogButtonBox,QLabel
from . import __version__


def application_root():
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]


def resource_root():
    return Path(sys._MEIPASS) if getattr(sys,'frozen',False) else application_root()


def application_icon():
    return QIcon(str(resource_root()/'assets/pollen.ico'))


class GuideDialog(QDialog):
    def __init__(self,parent=None,filename='快速入门.html'):
        super().__init__(parent)
        self.setWindowTitle('使用帮助 — 孢粉分析工作台')
        self.resize(900,680)
        root = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setSearchPaths([str(application_root()/'使用指南')])
        root.addWidget(self.browser,1)
        path = application_root()/'使用指南'/filename
        if path.is_file():
            self.browser.setSource(QUrl.fromLocalFile(str(path)))
        else:
            self.browser.setPlainText('未找到随附的中文使用说明。请保留完整软件文件夹，或重新解压正式发布包。')
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText('关闭')
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


class AboutDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setWindowTitle('关于孢粉分析工作台')
        self.resize(470,330)
        root = QVBoxLayout(self)
        icon = QLabel()
        icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(application_icon().pixmap(72,72))
        root.addWidget(icon)
        title = QLabel(f'孢粉分析工作台\n正式版 {__version__} · Windows 64 位')
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet('font-size:18px;font-weight:600;padding:8px;color:#184e43')
        root.addWidget(title)
        text = QLabel('计数与已有百分比 · 孢粉图谱 · 聚类与分带\nPCA 主成分分析 · 出现分布图\n\n数据保存在本机，无需联网。\n按 F1 查看中文使用说明。')
        text.setAlignment(Qt.AlignCenter)
        text.setWordWrap(True)
        root.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText('关闭')
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
