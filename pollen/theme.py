"""An explicit light palette prevents Windows dark mode bleeding into panels."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette


def apply_light_theme(app):
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyle('Fusion')
    palette = QPalette()
    roles = {
        'Window': '#f4f7f6', 'WindowText': '#263c36',
        'Base': '#ffffff', 'AlternateBase': '#f5f9f7', 'Text': '#263c36',
        'Button': '#ffffff', 'ButtonText': '#263c36', 'BrightText': '#ffffff',
        'Highlight': '#d2e9df', 'HighlightedText': '#183e34',
        'ToolTipBase': '#fffced', 'ToolTipText': '#263c36',
        'PlaceholderText': '#6b7f76', 'Link': '#236e59', 'LinkVisited': '#456f64',
        'Light': '#ffffff', 'Midlight': '#eaf1ed', 'Mid': '#d9e3df',
        'Dark': '#b8c9c2', 'Shadow': '#9eaea7', 'Accent': '#287d67',
    }
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        for role, color in roles.items():
            palette.setColor(group, getattr(QPalette.ColorRole, role), QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor('#71817b'))
    palette.setColor(QPalette.Disabled, QPalette.Base, QColor('#eef3f0'))
    app.setPalette(palette)
    app.setStyleSheet('''
        QWidget { font-family: "Microsoft YaHei UI"; font-size: 12px; color: #263c36; }
        QMainWindow, QDialog { background-color: #f4f7f6; }
        QTabWidget::pane { background-color: #f4f7f6; border: 1px solid #d5dfda; }
        QPushButton { background: white; border: 1px solid #c6d6d0; border-radius: 5px; padding: 7px 10px; }
        QPushButton:hover { background: #e0efe9; border-color: #418773; }
        QPushButton:pressed { background: #cee5da; }
        QPushButton:disabled { background: #edf2ef; color: #71817b; border-color: #d5dfda; }
        QTableWidget, QTreeWidget {
            background: white; alternate-background-color: #f5f9f7;
            gridline-color: #e0e8e4; border: 1px solid #d5dfda;
            selection-background-color: #d2e9df; selection-color: #183e34;
        }
        QHeaderView { background-color: #e6efeb; }
        QHeaderView::section { background: #e6efeb; color: #263c36; border: 1px solid #d0ddd6; padding: 5px; }
        QTableCornerButton::section { background: #e6efeb; border: 1px solid #d0ddd6; }
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
            background: white; color: #263c36; border: 1px solid #c8d8d0;
            padding: 5px; border-radius: 3px;
            selection-background-color: #d2e9df; selection-color: #183e34;
        }
        QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
            background: #eef3f0; color: #71817b; border-color: #d5dfda;
        }
        QComboBox QAbstractItemView {
            background: white; color: #263c36;
            selection-background-color: #d2e9df; selection-color: #183e34;
        }
        QGroupBox { background-color: #f4f7f6; border: 1px solid #d0ded6; border-radius: 6px; margin-top: 12px; padding-top: 14px; }
        QGroupBox::title { color: #29483e; subcontrol-origin: margin; left: 10px; }
        QTabBar::tab { padding: 8px 18px; background: #e8efeb; color: #29483e; }
        QTabBar::tab:selected { background: #d2e9df; color: #174e3f; border-bottom: 2px solid #32856c; }
        QToolBar, QToolButton, QStatusBar { background-color: #f4f7f6; color: #263c36; }
        QToolButton:hover { background: #e0efe9; }
        QToolButton:checked { background: #d2e9df; }
        QMenu { background: white; color: #263c36; border: 1px solid #c8d8d0; }
        QMenu::item:selected { background: #d2e9df; color: #183e34; }
        QScrollBar:horizontal { background: #edf3ef; height: 14px; }
        QScrollBar:vertical { background: #edf3ef; width: 14px; }
        QScrollBar::handle { background: #bdcfc5; border: 1px solid #a7beb1; border-radius: 4px; min-width: 22px; min-height: 22px; }
        QScrollBar::handle:hover { background: #9fbfaf; }
        QScrollBar::add-line, QScrollBar::sub-line { background: #e4ede7; border: 1px solid #d0ddd6; }
        QScrollBar::add-page, QScrollBar::sub-page { background: #edf3ef; }
        QSlider::groove:horizontal { background: #dce8e1; height: 5px; border-radius: 2px; }
        QSlider::sub-page:horizontal { background: #8dbca6; border-radius: 2px; }
        QSlider::handle:horizontal { background: #f9fcfa; border: 1px solid #729d87; width: 14px; margin: -5px 0; border-radius: 4px; }
        QToolTip { background: #fffced; color: #263c36; border: 1px solid #d0d8bd; }
    ''')
