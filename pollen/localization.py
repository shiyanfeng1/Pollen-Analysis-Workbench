"""Chinese translations for Qt standard dialogs and application error messages."""
import re
from PySide6.QtCore import QTranslator, QLibraryInfo, QLocale


def install_chinese(app):
    QLocale.setDefault(QLocale('zh_CN'))
    translator = QTranslator(app)
    directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if not translator.load('qtbase_zh_CN', directory):
        raise RuntimeError('无法加载中文界面资源，请保留完整程序文件夹')
    app.installTranslator(translator)
    app._chinese_translator = translator


def error_text(exc):
    if isinstance(exc, PermissionError):
        return '文件无法写入或读取，请检查文件是否正被其他程序占用，以及文件夹是否允许访问。'
    if isinstance(exc, FileNotFoundError):
        return '找不到指定的文件或文件夹，请确认路径后重试。'
    text = str(exc)
    if re.search('[\u4e00-\u9fff]', text):
        return text
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return '文件内容或设置格式不正确，请核对输入数据、绘图设置及所选文件类型。'
    return '操作未完成，请检查数据与文件路径，必要时重新打开项目后重试。'
