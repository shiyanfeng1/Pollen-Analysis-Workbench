import sys
import copy
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QFont, QFontDatabase
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QSpinBox, QTreeWidget, QTreeWidgetItem, QSplitter, QAbstractItemView,
    QFileDialog, QMessageBox, QInputDialog, QDialog, QDialogButtonBox, QFormLayout,
    QLineEdit, QTabWidget, QGroupBox)
from .core import Project, Taxon, Sample, GROUPS, calculate, save_project, load_project, example, input_label
from .excel import read_table, write_excel, sheets, inspect_table, normalize_percentage
from .input_ui import DataTypeDialog
from .plot_ui import PlotPanel
from .plotting import GROUP_COLORS, diagram_example
from .theme import apply_light_theme
from .localization import install_chinese, error_text
from .cluster_ui import ClusterPanel
from .pca_ui import PcaPanel
from .occurrence_ui import OccurrencePanel
from . import __version__
from .help_ui import GuideDialog,AboutDialog,application_icon

COLORS = list(GROUP_COLORS.values())


class GroupHeader(QHeaderView):
    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.taxa = []
        self.setDefaultSectionSize(112)
        self.setMinimumSectionSize(75)
        self.setFixedHeight(62)

    def paintSection(self, painter, rect, index):
        if index >= len(self.taxa):
            return
        taxon = self.taxa[index]
        painter.save()
        painter.fillRect(rect, QColor('#f7faf9'))
        painter.setPen(QColor('#d5dfdc'))
        painter.drawRect(rect.adjusted(0, 31, -1, -1))
        painter.setPen(QColor('#253c37'))
        painter.drawText(rect.adjusted(4, 31, -4, 0), Qt.AlignCenter, taxon.name)
        painter.restore()

    def paintEvent(self, event):
        super().paintEvent(event)
        from PySide6.QtGui import QPainter
        from PySide6.QtCore import QRect
        painter = QPainter(self.viewport())
        start = 0
        while start < len(self.taxa):
            group = self.taxa[start].group
            end = start + 1
            while end < len(self.taxa) and self.taxa[end].group == group:
                end += 1
            x = self.sectionViewportPosition(start)
            width = sum(self.sectionSize(i) for i in range(start, end))
            rect = QRect(x, 0, width, 31)
            painter.fillRect(rect, QColor(COLORS[GROUPS.index(group)]))
            painter.setPen(QColor('#b6c7c0'))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
            painter.setPen(QColor('#21483e'))
            painter.drawText(rect, Qt.AlignCenter, group)
            start = end


class PasteTable(QTableWidget):
    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Paste) and self.editTriggers() != QAbstractItemView.NoEditTriggers:
            self.window().paste_cells(self)
            return
        if event.key() == Qt.Key_Delete and self.editTriggers() != QAbstractItemView.NoEditTriggers:
            for index in self.selectedIndexes():
                self.setItem(index.row(), index.column(), QTableWidgetItem(''))
            return
        if event.matches(QKeySequence.Copy):
            indices = self.selectedIndexes()
            if indices:
                rows = range(min(i.row() for i in indices), max(i.row() for i in indices) + 1)
                cols = range(min(i.column() for i in indices), max(i.column() for i in indices) + 1)
                QApplication.clipboard().setText('\n'.join('\t'.join(
                    self.item(r, c).text() if self.item(r, c) else '' for c in cols) for r in rows))
            return
        super().keyPressEvent(event)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.project = Project()
        self.path = None
        self.dirty = False
        self.loading = False
        self.results = []
        self.issues = []
        self.resize(1280, 820)
        self.setMinimumSize(960, 600)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.recalculate)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 16, 20, 12)
        heading = QLabel('孢粉分析工作台   /   数据分析与图谱绘制')
        heading.setStyleSheet('font-size:22px;font-weight:600;color:#184e43;padding-bottom:8px')
        layout.addWidget(heading)
        toolbar = QHBoxLayout()
        for label, fn in [('新建', self.new), ('打开项目', self.open_project), ('保存项目', self.save),
                          ('导入 Excel / CSV', self.import_file), ('导出结果', self.export),
                          ('下载模板', self.template), ('示例数据', self.load_example)]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            toolbar.addWidget(b)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.workspace = QTabWidget()
        layout.addWidget(self.workspace, 1)
        split = QSplitter(Qt.Horizontal)
        self.workspace.addTab(split, '数据与百分比')
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 8, 8, 0)
        self.tabs = QTabWidget()
        self.tabs.addTab(QWidget(), '原始计数')
        self.tabs.addTab(QWidget(), '百分比结果（%）')
        self.tabs.setMaximumHeight(36)
        ll.addWidget(self.tabs)
        self.tabs.currentChanged.connect(self.render_grid)
        self.note = QLabel('')
        self.note.setWordWrap(True)
        ll.addWidget(self.note)
        gridrow = QHBoxLayout()
        gridrow.setSpacing(0)
        self.axis_table = PasteTable()
        self.axis_table.setFixedWidth(182)
        self.axis_table.horizontalHeader().setFixedHeight(62)
        self.axis_table.verticalHeader().setDefaultSectionSize(34)
        self.axis_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.table = PasteTable()
        self.group_header = GroupHeader(self.table)
        self.table.setHorizontalHeader(self.group_header)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.axis_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.axis_table.verticalScrollBar().valueChanged.connect(self.table.verticalScrollBar().setValue)
        self.table.verticalScrollBar().valueChanged.connect(self.axis_table.verticalScrollBar().setValue)
        self.axis_table.itemChanged.connect(self.cell_changed)
        self.table.itemChanged.connect(self.cell_changed)
        self.table.cellClicked.connect(lambda row, col: self.tree.setCurrentItem(None))
        for table in (self.axis_table, self.table):
            table.setAlternatingRowColors(True)
            table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        gridrow.addWidget(self.axis_table)
        gridrow.addWidget(self.table, 1)
        ll.addLayout(gridrow, 1)
        buttons = QHBoxLayout()
        for label, fn in [('添加样品行', self.add_rows), ('删除选中行', self.delete_rows),
                          ('添加类群', self.add_taxon), ('编辑选中类群', self.edit_taxon),
                          ('删除选中类群', self.delete_taxon)]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            buttons.addWidget(b)
        ll.addLayout(buttons)
        self.report = QTableWidget(0, 4)
        self.report.setHorizontalHeaderLabels(['级别', '数据行', '字段', '说明'])
        self.report.horizontalHeader().setStretchLastSection(True)
        self.report.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.report.setMaximumHeight(175)
        self.report.cellDoubleClicked.connect(self.locate_issue)
        ll.addWidget(self.report)
        split.addWidget(left)
        side = QWidget()
        side.setMinimumWidth(270)
        side.setMaximumWidth(360)
        sl = QVBoxLayout(side)
        settings = QGroupBox('坐标与计算设置')
        form = QFormLayout(settings)
        self.data_type_label = QLabel('原始计数')
        self.data_type_label.setWordWrap(True)
        form.addRow('数据类型',self.data_type_label)
        self.type_button = QPushButton('数据类型与单位')
        self.type_button.clicked.connect(self.edit_data_type)
        form.addRow(self.type_button)
        self.kind = QComboBox()
        self.kind.addItems(['深度', '年代'])
        self.unit = QLineEdit('cm')
        self.threshold = QSpinBox()
        self.threshold.setRange(0, 100000000)
        self.threshold.setValue(200)
        self.precision = QSpinBox()
        self.precision.setRange(0, 8)
        self.precision.setValue(2)
        form.addRow('深度/年代', self.kind)
        form.addRow('单位', self.unit)
        form.addRow('低计数提示阈值', self.threshold)
        form.addRow('显示小数位', self.precision)
        self.kind.currentTextChanged.connect(self.kind_changed)
        self.unit.textEdited.connect(self.settings_changed)
        self.threshold.valueChanged.connect(self.settings_changed)
        self.precision.valueChanged.connect(self.settings_changed)
        sl.addWidget(settings)
        title = QLabel('计算基数 · 勾选参与分母的类群')
        self.base_title = title
        title.setStyleSheet('font-weight:600;padding-top:10px')
        sl.addWidget(title)
        hint = QLabel('所有类群均计算百分比。未勾选类群仍会显示结果，全部结果之和可能超过 100%。')
        self.base_hint = hint
        hint.setWordWrap(True)
        sl.addWidget(hint)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemChanged.connect(self.membership_changed)
        self.tree.itemClicked.connect(lambda item, col: self.table.setCurrentCell(-1, -1))
        sl.addWidget(self.tree, 1)
        row = QHBoxLayout()
        self.base_buttons = []
        for label, state in [('全选', True), ('清空', False)]:
            b = QPushButton(label)
            b.clicked.connect(lambda checked=False, value=state: self.select_all(value))
            row.addWidget(b)
            self.base_buttons.append(b)
        sl.addLayout(row)
        self.schemes = QComboBox()
        sl.addWidget(self.schemes)
        row = QHBoxLayout()
        for label, fn in [('保存方案', self.save_scheme), ('应用方案', self.apply_scheme)]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            row.addWidget(b)
            self.base_buttons.append(b)
        sl.addLayout(row)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        sl.addWidget(self.summary)
        split.addWidget(side)
        split.setStretchFactor(0, 1)
        self.plot_panel = PlotPanel(self.mark_dirty)
        self.workspace.addTab(self.plot_panel, '孢粉图谱')
        self.cluster_panel = ClusterPanel(self.mark_dirty)
        self.workspace.addTab(self.cluster_panel,'聚类与分带')
        self.pca_panel = PcaPanel(self.mark_dirty)
        self.workspace.addTab(self.pca_panel,'PCA 分析')
        self.occurrence_panel = OccurrencePanel(self.mark_dirty)
        self.workspace.addTab(self.occurrence_panel,'出现分布图')
        self.statusBar().showMessage('准备就绪 · 数据仅保存在本机')
        file_menu = self.menuBar().addMenu('文件')
        for label,slot,shortcut in [('新建项目',self.new,QKeySequence.New),
            ('打开项目',self.open_project,QKeySequence.Open),('保存项目',self.save,QKeySequence.Save),
            ('另存为',self.save_as,QKeySequence.SaveAs)]:
            action = file_menu.addAction(label)
            action.setShortcut(shortcut)
            action.triggered.connect(slot)
        help_menu = self.menuBar().addMenu('帮助')
        action = help_menu.addAction('快速入门')
        action.setShortcut(QKeySequence.HelpContents)
        action.triggered.connect(lambda checked=False: GuideDialog(self).exec())
        help_menu.addAction('完整使用说明').triggered.connect(lambda checked=False: GuideDialog(self,'使用说明.html').exec())
        help_menu.addAction('版本说明与使用范围').triggered.connect(lambda checked=False: GuideDialog(self,'版本说明.html').exec())
        help_menu.addSeparator()
        help_menu.addAction('关于孢粉分析工作台').triggered.connect(lambda checked=False: AboutDialog(self).exec())
        self.refresh()

    def mark_dirty(self):
        self.dirty = True
        if hasattr(self, 'plot_panel'):
            self.plot_panel.mark_stale()
        if hasattr(self, 'cluster_panel'):
            self.cluster_panel.invalidate_status()
        if hasattr(self, 'pca_panel'):
            self.pca_panel.invalidate_status()
        if hasattr(self, 'occurrence_panel'):
            self.occurrence_panel.invalidate_status()
        self.update_title()

    def update_title(self):
        self.setWindowTitle(f'{"* " if self.dirty else ""}{Path(self.path).name if self.path else "未命名项目"} — 孢粉分析工作台 {__version__}')

    def confirm_replace(self):
        if not self.dirty:
            return True
        answer = QMessageBox.question(self, '保存修改', '当前项目有未保存修改，是否保存？',
                                      QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if answer == QMessageBox.Save:
            return self.save()
        return answer == QMessageBox.Discard

    def closeEvent(self, event):
        for panel in (self.cluster_panel,self.pca_panel):
            if panel.is_running():
                panel.cancel()
            if panel.is_running() and not panel.thread.wait(3000):
                event.ignore()
                QMessageBox.information(self,'正在取消分析','分析即将停止，请稍后关闭。')
                return
        if self.confirm_replace():
            event.accept()
        else:
            event.ignore()

    def replace(self, project, path=None, dirty=False):
        self.timer.stop()
        self.project, self.path, self.dirty = project, path, dirty
        self.refresh()

    def refresh(self):
        self.loading = True
        direct = self.project.data_type=='percentages'
        self.data_type_label.setText(input_label(self.project))
        self.tabs.setTabText(0,'原始百分比（%）' if direct else '原始计数')
        self.tabs.setTabText(1,'有效百分比（%）' if direct else '百分比结果（%）')
        self.threshold.setEnabled(not direct)
        self.tree.setEnabled(not direct)
        self.schemes.setEnabled(not direct)
        for button in self.base_buttons:
            button.setEnabled(not direct)
        self.base_title.setText('已有百分比 · 基数设置不适用' if direct else '计算基数 · 勾选参与分母的类群')
        self.base_hint.setText('直接使用导入百分比，不重新归一化。原始粒数和实际基数未知；聚类与 PCA 类群在各自页面选择。' if direct else
            '所有类群均计算百分比。未勾选类群仍会显示结果，全部结果之和可能超过 100%。')
        self.kind.setCurrentText(self.project.axis_kind)
        self.unit.setText(self.project.unit)
        self.threshold.setValue(self.project.threshold)
        self.precision.setValue(self.project.decimals)
        self.tree.clear()
        for group in GROUPS:
            parent = QTreeWidgetItem(self.tree, [group])
            parent.setFlags(parent.flags() | Qt.ItemIsAutoTristate | Qt.ItemIsUserCheckable)
            parent.setCheckState(0, Qt.Unchecked)
            for t in self.project.ordered_taxa():
                if t.group == group:
                    child = QTreeWidgetItem(parent, [t.name])
                    child.setData(0, Qt.UserRole, t.id)
                    child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                    child.setCheckState(0, Qt.Checked if t.selected else Qt.Unchecked)
            parent.setExpanded(True)
        current = self.schemes.currentText()
        self.schemes.clear()
        self.schemes.addItems(self.project.schemes.keys())
        self.schemes.setCurrentText(current)
        self.loading = False
        self.recalculate()
        self.render_grid()
        self.plot_panel.bind(self.project)
        self.cluster_panel.bind(self.project)
        self.pca_panel.bind(self.project)
        self.occurrence_panel.bind(self.project)
        self.update_title()

    def kind_changed(self, value):
        if not self.loading:
            self.unit.setText('cm' if value == '深度' else 'cal yr BP')
            self.settings_changed()

    def settings_changed(self, *args):
        if self.loading:
            return
        self.project.axis_kind = self.kind.currentText()
        self.project.unit = self.unit.text().strip()
        self.project.threshold = self.threshold.value()
        self.project.decimals = self.precision.value()
        self.mark_dirty()
        self.recalculate()
        self.render_grid()

    def recalculate(self):
        self.results, self.issues = calculate(self.project)
        self.report.setRowCount(len(self.issues))
        for r, issue in enumerate(self.issues):
            for c, value in enumerate(issue):
                item = QTableWidgetItem(str(value))
                item.setForeground(QColor('#b83b32' if issue[0] == '错误' else '#8b621b'))
                self.report.setItem(r, c, item)
        self.report.resizeColumnsToContents()
        errors = sum(i[0] == '错误' for i in self.issues)
        selected = sum(t.selected for t in self.project.taxa)
        source = '直接使用百分比，未重新归一化' if self.project.data_type=='percentages' else f'{selected} 个类群进入基数'
        self.summary.setText(f'{len(self.project.samples)} 个样品 · {len(self.project.taxa)} 个类群\n'
                             f'{source}\n{errors} 项错误 · {len(self.issues) - errors} 项提示')
        self.statusBar().showMessage('存在错误，请修正后导出' if errors else
            '百分比原值已就绪 · 空白为缺失，0 为有效零值' if self.project.data_type=='percentages' else '计算已更新 · 空白为缺失，0 为有效计数')
        if self.tabs.currentIndex() == 1:
            self.render_grid()
        else:
            self.highlight_raw()

    def highlight_raw(self):
        self.loading = True
        for table in (self.axis_table, self.table):
            for r in range(table.rowCount()):
                for c in range(table.columnCount()):
                    item = table.item(r, c)
                    if item:
                        item.setData(Qt.BackgroundRole, None)
        taxa = self.project.ordered_taxa()
        for severity, row, name, message in self.issues:
            if not isinstance(row, int) or row > self.table.rowCount():
                continue
            target = self.axis_table.item(row - 1, 0) if name == '深度/年代' else next(
                (self.table.item(row - 1, j) for j, t in enumerate(taxa) if t.name == name), None)
            if target:
                target.setBackground(QColor('#ffe6e2' if severity == '错误' else '#fff4d7'))
                target.setToolTip(message)
        self.loading = False

    def render_grid(self, *args):
        self.loading = True
        result = self.tabs.currentIndex() == 1
        direct = self.project.data_type=='percentages'
        taxa = self.project.ordered_taxa()
        self.group_header.taxa = taxa
        self.table.clearContents()
        self.table.setColumnCount(len(taxa))
        self.table.setRowCount(len(self.project.samples))
        self.axis_table.clearContents()
        self.axis_table.setColumnCount(2 if result and not direct else 1)
        self.axis_table.setRowCount(len(self.project.samples))
        label = f'深度/年代\n{self.project.axis_kind} ({self.project.unit})'
        self.axis_table.setHorizontalHeaderLabels([label, '实际基数'] if result and not direct else [label])
        self.axis_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for table in (self.table, self.axis_table):
            table.setEditTriggers(QAbstractItemView.NoEditTriggers if result else
                                  QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        for i, sample in enumerate(self.project.samples):
            self.axis_table.setItem(i, 0, QTableWidgetItem(sample.axis))
            if result and not direct:
                r = self.results[i]
                item = QTableWidgetItem('—' if r['base'] is None else str(r['base']))
                item.setToolTip(r['reason'])
                self.axis_table.setItem(i, 1, item)
            for j, t in enumerate(taxa):
                if result:
                    value = self.results[i]['percentages'][t.id]
                    text = '—' if value is None else f'{value:.{self.project.decimals}f}'
                else:
                    text = str(sample.counts.get(t.id, ''))
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item.setToolTip(f'{t.group} / {t.name}\n' + ('导入百分比，不重新归一化' if direct else ('进入基数' if t.selected else '不进入基数')))
                self.table.setItem(i, j, item)
        # Highlight invalid cells without changing the imported value.
        if not result:
            for severity, row, name, message in self.issues:
                if not isinstance(row, int):
                    continue
                target = self.axis_table.item(row - 1, 0) if name == '深度/年代' else next(
                    (self.table.item(row - 1, j) for j, t in enumerate(taxa) if t.name == name), None)
                if target:
                    target.setBackground(QColor('#ffe6e2' if severity == '错误' else '#fff4d7'))
                    target.setToolTip(message)
        if direct:
            self.note.setText('百分比原值未重新计算，仅按显示小数位格式化；— 表示缺失。' if result else
                '直接编辑或粘贴已有百分比：12.5 表示 12.5%。不重新归一化，空白和零分别保留；比例数请先设置数值单位。')
        else:
            self.note.setText('结果单位：%；— 表示无法计算。实际基数为本行勾选类群的计数和。' if result else
                              '直接编辑或粘贴 Excel 数值。空白表示缺失，0 表示未计到；左侧坐标列固定。')
        self.group_header.viewport().update()
        self.loading = False

    def cell_changed(self, item):
        if self.loading or self.tabs.currentIndex() != 0:
            return
        sample = self.project.samples[item.row()]
        if self.sender() == self.axis_table:
            sample.axis = item.text()
        else:
            sample.counts[self.project.ordered_taxa()[item.column()].id] = item.text()
        self.mark_dirty()
        self.timer.start(180)

    def paste_cells(self, table):
        start_row, start_col = max(table.currentRow(), 0), max(table.currentColumn(), 0)
        text = QApplication.clipboard().text().replace('\r\n', '\n').replace('\r', '\n').rstrip('\n')
        if not text:
            return
        rows = [row.split('\t') for row in text.split('\n')]
        if max(map(len, rows)) + start_col > table.columnCount():
            QMessageBox.warning(self, '粘贴范围', '粘贴列数超过表格范围，请先添加类群。坐标与计数请分别粘贴。')
            return
        required = start_row + len(rows)
        while len(self.project.samples) < required:
            self.project.samples.append(Sample())
        self.render_grid()
        for r, row in enumerate(rows, start_row):
            for c, value in enumerate(row, start_col):
                table.setItem(r, c, QTableWidgetItem(value.strip()))
        self.mark_dirty()
        self.recalculate()

    def membership_changed(self, item, column):
        if self.loading or self.project.data_type=='percentages':
            return
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            for j in range(parent.childCount()):
                child = parent.child(j)
                taxon = next(t for t in self.project.taxa if t.id == child.data(0, Qt.UserRole))
                taxon.selected = child.checkState(0) == Qt.Checked
        self.mark_dirty()
        self.timer.start(100)

    def select_all(self, value):
        for t in self.project.taxa:
            t.selected = value
        self.mark_dirty()
        self.refresh()

    def add_rows(self):
        n, ok = QInputDialog.getInt(self, '添加样品', '添加行数', 1, 1, 10000)
        if ok:
            self.project.samples.extend(Sample() for _ in range(n))
            self.mark_dirty()
            self.refresh()

    def delete_rows(self):
        rows = {i.row() for table in (self.table, self.axis_table) for i in table.selectedIndexes()}
        if not rows:
            return
        if QMessageBox.question(self, '删除样品', f'删除选中的 {len(rows)} 行计数？') == QMessageBox.Yes:
            self.project.samples = [s for i, s in enumerate(self.project.samples) if i not in rows]
            self.mark_dirty()
            self.refresh()

    def taxon_dialog(self, taxon=None):
        dialog = QDialog(self)
        dialog.setWindowTitle('编辑类群' if taxon else '添加类群')
        form = QFormLayout(dialog)
        name = QLineEdit(taxon.name if taxon else '')
        group = QComboBox()
        group.addItems(GROUPS)
        if taxon:
            group.setCurrentText(taxon.group)
        form.addRow('名称', name)
        form.addRow('分组', group)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        form.addRow(buttons)
        def accept():
            value = name.text().strip()
            if not value or any(t.name == value and t is not taxon for t in self.project.taxa):
                QMessageBox.warning(dialog, '名称无效', '名称不能为空，也不能与已有类群重复。')
                return
            dialog.accept()
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() == QDialog.Accepted:
            return name.text().strip(), group.currentText()

    def current_taxon(self):
        item = self.tree.currentItem()
        if item and item.parent():
            return next((t for t in self.project.taxa if t.id == item.data(0, Qt.UserRole)), None)
        col = self.table.currentColumn()
        taxa = self.project.ordered_taxa()
        return taxa[col] if 0 <= col < len(taxa) else None

    def add_taxon(self):
        value = self.taxon_dialog()
        if value:
            self.project.taxa.append(Taxon(*value))
            self.mark_dirty()
            self.refresh()

    def edit_taxon(self):
        t = self.current_taxon()
        if not t:
            QMessageBox.information(self, '选择类群', '先在右侧类群树或计数表中选中一个类群。')
            return
        value = self.taxon_dialog(t)
        if value:
            t.name, t.group = value
            self.mark_dirty()
            self.refresh()

    def delete_taxon(self):
        t = self.current_taxon()
        if t and QMessageBox.question(self, '删除类群', f'删除“{t.name}”及全部样品中该类群的计数？') == QMessageBox.Yes:
            self.project.taxa.remove(t)
            for s in self.project.samples:
                s.counts.pop(t.id, None)
            for ids in self.project.schemes.values():
                if t.id in ids:
                    ids.remove(t.id)
            self.mark_dirty()
            self.refresh()

    def save_scheme(self):
        name, ok = QInputDialog.getText(self, '保存计算方案', '方案名称')
        name = name.strip()
        if not ok or not name:
            return
        if name in self.project.schemes and QMessageBox.question(self, '覆盖方案', f'覆盖方案“{name}”？') != QMessageBox.Yes:
            return
        self.project.schemes[name] = [t.id for t in self.project.taxa if t.selected]
        self.mark_dirty()
        self.refresh()
        self.schemes.setCurrentText(name)

    def apply_scheme(self):
        ids = self.project.schemes.get(self.schemes.currentText())
        if ids is not None:
            for t in self.project.taxa:
                t.selected = t.id in ids
            self.mark_dirty()
            self.refresh()

    def locate_issue(self, row, col):
        issue = self.issues[row]
        if not isinstance(issue[1], int):
            return
        self.tabs.setCurrentIndex(0)
        self.render_grid()
        i = issue[1] - 1
        self.axis_table.setCurrentCell(i, 0)
        for j, t in enumerate(self.project.ordered_taxa()):
            if t.name == issue[2]:
                self.table.setCurrentCell(i, j)
                self.table.scrollToItem(self.table.item(i, j))
                break

    def new(self):
        if self.confirm_replace():
            self.replace(Project())

    def load_example(self):
        if self.confirm_replace():
            from .demo import sixty_taxa_example
            self.replace(sixty_taxa_example(), dirty=True)

    def error(self, exc):
        QMessageBox.critical(self, '操作未完成', error_text(exc))

    def save(self):
        path = self.path
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, '保存项目', '孢粉项目.pollen', '孢粉项目 (*.pollen)')
        if not path:
            return False
        return self.save_to(path)

    def save_as(self):
        suggested = str(self.path) if self.path else '孢粉项目.pollen'
        path,_ = QFileDialog.getSaveFileName(self,'项目另存为',suggested,'孢粉项目 (*.pollen)')
        return self.save_to(path) if path else False

    def save_to(self,path):
        try:
            save_project(self.project, path)
            self.path, self.dirty = path, False
            self.update_title()
            self.statusBar().showMessage('项目已保存：' + path)
            return True
        except Exception as exc:
            self.error(exc)
            return False

    def open_project(self):
        if not self.confirm_replace():
            return
        path, _ = QFileDialog.getOpenFileName(self, '打开项目', '', '孢粉项目 (*.pollen)')
        if path:
            try:
                self.replace(load_project(path), path)
            except Exception as exc:
                self.error(exc)

    def import_file(self):
        if not self.confirm_replace():
            return
        path, _ = QFileDialog.getOpenFileName(self, '导入数据表', '', '数据表 (*.xlsx *.csv)')
        if not path:
            return
        try:
            sheet = None
            if Path(path).suffix.lower() == '.xlsx':
                names = sheets(path)
                sheet, ok = QInputDialog.getItem(self, '工作表', '选择数据工作表', names, 0, False)
                if not ok:
                    return
            info = inspect_table(path,sheet)
            dialog = DataTypeDialog(self,data_type=info['data_type'],unit=info['unit'],preview=info['preview'])
            if dialog.exec()!=QDialog.Accepted:
                return
            p = read_table(path,sheet,data_type=dialog.data_type,percentage_unit=dialog.percentage_unit)
            self.replace(p, dirty=True)
            QMessageBox.information(self, '导入完成', '百分比已按所选单位导入并保留，不重新归一化。请核对深度／年代与单位，可直接进入绘图、聚类或 PCA。' if p.data_type=='percentages' else
                '请核对深度/年代类型、单位和基数勾选，然后查看百分比结果。')
        except Exception as exc:
            self.error(exc)

    def edit_data_type(self):
        preview = [(t.name,s.counts.get(t.id,''),False) for s in self.project.samples[:2] for t in self.project.ordered_taxa()[:4]]
        populated = any(v is not None and str(v).strip() for s in self.project.samples for v in s.counts.values())
        dialog = DataTypeDialog(self,data_type=self.project.data_type,preview=preview,
            allow_counts=not (self.project.data_type=='percentages' and populated))
        if dialog.exec()!=QDialog.Accepted:
            return
        p = copy.deepcopy(self.project)
        if dialog.data_type=='percentages':
            for s in p.samples:
                for tid,value in s.counts.items():
                    try:
                        s.counts[tid] = normalize_percentage(value,dialog.percentage_unit)
                    except (ValueError,OverflowError):
                        pass
            p.occurrence_config['mode'] = 'percentages'
        p.data_type = dialog.data_type
        p.source_info.update(numeric_unit=dialog.percentage_unit if p.data_type=='percentages' else 'counts',
                             stored_unit='percent' if p.data_type=='percentages' else 'counts',table_reinterpreted=True)
        self.replace(p,self.path,dirty=True)

    def export(self):
        self.recalculate()
        if not self.project.samples or not self.project.taxa:
            QMessageBox.warning(self, '没有数据', '请先添加或导入样品和类群。')
            return
        if any(i[0] == '错误' for i in self.issues):
            QMessageBox.warning(self, '需要修正', '检查报告中有错误，请修正后导出；双击报告可定位。')
            return
        path, _ = QFileDialog.getSaveFileName(self, '导出结果', '孢粉分析结果.xlsx', 'Excel 工作簿 (*.xlsx)')
        if path:
            try:
                write_excel(self.project, path)
                self.statusBar().showMessage('已导出：' + path)
            except Exception as exc:
                self.error(exc)

    def template(self):
        filename = '孢粉百分比模板.xlsx' if self.project.data_type=='percentages' else '孢粉计数模板.xlsx'
        path, _ = QFileDialog.getSaveFileName(self, '保存导入模板', filename, 'Excel 工作簿 (*.xlsx)')
        if path:
            try:
                p = example()
                p.data_type = self.project.data_type
                p.samples = [Sample() for _ in range(5)]
                for t in p.taxa:
                    t.selected = False
                write_excel(p, path, template=True)
                self.statusBar().showMessage('模板已保存：' + path)
            except Exception as exc:
                self.error(exc)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('孢粉分析工作台')
    app.setApplicationVersion(__version__)
    app.setWindowIcon(application_icon())
    install_chinese(app)
    if '--smoke-test' in sys.argv and '--dark-palette' in sys.argv:
        # Reproduce the inherited dark palette seen on the user's Windows desktop.
        from PySide6.QtGui import QPalette
        dark = QPalette()
        for role in (QPalette.Window, QPalette.Button, QPalette.Base):
            dark.setColor(role, QColor('#222222'))
        for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            dark.setColor(role, QColor('#ffffff'))
        app.setPalette(dark)
    # Also supports offscreen QA where Qt cannot enumerate native font families.
    if 'Microsoft YaHei' not in QFontDatabase.families():
        font_path = Path('C:/Windows/Fonts/msyh.ttc')
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont('Microsoft YaHei', 10))
    apply_light_theme(app)
    window = Window()
    window.show()
    if '--smoke-test' in sys.argv:
        import json
        output = Path(sys.argv[sys.argv.index('--smoke-test') + 1]).resolve()
        output.mkdir(parents=True, exist_ok=True)
        export_dpi = int(sys.argv[sys.argv.index('--smoke-dpi')+1]) if '--smoke-dpi' in sys.argv else 150
        app.processEvents()
        window.grab().save(str(output / 'empty-data.png'))
        window.workspace.setCurrentIndex(1)
        app.processEvents()
        window.grab().save(str(output / 'empty-plot.png'))
        window.workspace.setCurrentIndex(0)
        if '--sixty-taxa' in sys.argv:
            from .demo import sixty_taxa_example
            window.replace(sixty_taxa_example())
        else:
            window.replace(example())
        if '--smoke-project' in sys.argv:
            project_path = Path(sys.argv[sys.argv.index('--smoke-project')+1]).resolve()
            window.replace(load_project(project_path),str(project_path))
        if '--help-screenshots' in sys.argv:
            for filename,name in [('快速入门.html','quick-start'),('使用说明.html','manual')]:
                guide = GuideDialog(window,filename)
                guide.show()
                app.processEvents()
                guide.grab().save(str(output/f'{name}.png'))
                guide.close()
            about = AboutDialog(window)
            about.show()
            app.processEvents()
            about.grab().save(str(output/'about.png'))
            about.close()
        if '--percentage-input' in sys.argv:
            from openpyxl import load_workbook
            from .demo import percentage_example
            source = percentage_example() if '--sixty-taxa' in sys.argv else window.project
            if source.data_type!='percentages':
                values,_ = calculate(source)
                for sample,row in zip(source.samples,values):
                    sample.counts = {tid:'' if v is None else str(v) for tid,v in row['percentages'].items()}
                source.data_type = 'percentages'
                for taxon in source.taxa:
                    taxon.selected = False
            input_path = output/'percentage-source.xlsx'
            write_excel(source,input_path)
            unit = 'fraction' if '--percentage-fraction' in sys.argv else 'percent'
            wb = load_workbook(input_path)
            ws = wb['导入百分比']
            for row in ws.iter_rows(min_row=3,min_col=2):
                for cell in row:
                    if isinstance(cell.value,(int,float)):
                        cell.value /= 100
                        cell.number_format = 'General' if unit=='fraction' else '0.0000%'
            for row in wb['计算说明']:
                if row[0].value=='数值存储单位':
                    row[1].value = '比例数' if unit=='fraction' else '百分数'
            wb.save(input_path)
            wb.close()
            info = inspect_table(input_path,'导入百分比')
            dialog = DataTypeDialog(window,data_type=info['data_type'],unit=info['unit'],preview=info['preview'])
            def accept_percentage_preview():
                dialog.grab().save(str(output/'percentage-import-dialog.png'))
                dialog.accept()
            QTimer.singleShot(100,accept_percentage_preview)
            dialog.exec()
            window.replace(read_table(input_path,'导入百分比',data_type=dialog.data_type,percentage_unit=dialog.percentage_unit))
        if '--curve-style' in sys.argv:
            style = sys.argv[sys.argv.index('--curve-style') + 1]
            index = window.plot_panel.all_curves.findData(style)
            if index < 1:
                raise ValueError('曲线形式必须为 fill、line 或 bar')
            window.plot_panel.all_curves.setCurrentIndex(index)
        if '--cluster' in sys.argv:
            from PySide6.QtCore import QEventLoop
            from . import clustering as analysis
            panel = window.cluster_panel
            panel.n_zones.setValue(min(3,len(window.project.samples)))
            panel.start_analysis()
            loop = QEventLoop()
            panel.thread.finished.connect(loop.quit)
            QTimer.singleShot(20000,loop.quit)
            if panel.thread.isRunning():
                loop.exec()
            app.processEvents()
            if not analysis.result_is_current(window.project):
                raise RuntimeError(panel.status.text())
            if '--manual-zones' in sys.argv:
                bounds = analysis.active_boundaries(window.project)
                if bounds:
                    bounds[0] = (bounds[0]+(bounds[1] if len(bounds)>1 else window.project.cluster_result['y'][-1]))/2
                    panel.change_boundaries(bounds,'演示移动边界')
            window.workspace.setCurrentIndex(2)
            app.processEvents()
            window.grab().save(str(output/'cluster-workspace.png'))
            analysis.export_analysis(window.project,output/'cluster-analysis.xlsx')
            window.workspace.setCurrentIndex(0)
        app.processEvents()
        window.grab().save(str(output / 'preview.png'))
        window.tabs.setCurrentIndex(1)
        app.processEvents()
        window.grab().save(str(output / 'percentages.png'))
        save_project(window.project, output / 'example.pollen')
        write_excel(window.project, output / 'example.xlsx')
        # Exercise the same scientific renderer and export path as the desktop UI.
        window.workspace.setCurrentIndex(1)
        if not window.plot_panel.draw(report_errors=False):
            raise RuntimeError(window.plot_panel.banner.text())
        if '--axis-settings' in sys.argv:
            from .axis_ui import YAxisDialog
            def configure_demo_axis():
                dialog = app.activeModalWidget()
                if isinstance(dialog,YAxisDialog):
                    for key,value in {'y_min':'0','y_max':'1500' if '--sixty-taxa' in sys.argv else '40',
                                      'y_step':'100' if '--sixty-taxa' in sys.argv else '5'}.items():
                        dialog.entries[key].setText(value)
                    dialog.grab().save(str(output/'axis-settings.png'))
                    dialog.accept()
            QTimer.singleShot(80,configure_demo_axis)
            window.plot_panel.axis_dialog()
            save_project(window.project,output/'example.pollen')
        from .plotting import export_document
        for extension in ('png', 'pdf', 'svg'):
            export_document(window.project, window.plot_panel.diagrams, output / f'diagram.{extension}', export_dpi)
        app.processEvents()
        window.grab().save(str(output / 'plot-workspace.png'))
        result = {'samples': len(window.project.samples), 'taxa': len(window.project.taxa),
                  'app_version':__version__,'help_available':(Path(sys.executable).parent/'使用指南/快速入门.html').is_file() if getattr(sys,'frozen',False) else (Path(__file__).resolve().parents[1]/'使用指南/快速入门.html').is_file(),
                  'export_dpi':export_dpi,
                  'data_type':window.project.data_type,'source_numeric_unit':window.project.source_info.get('numeric_unit'),
                  'first_base': window.results[0]['base'],
                  'first_percentage': window.results[0]['percentages'][window.project.taxa[0].id],
                  'errors': sum(i[0] == '错误' for i in window.issues),
                  'plot_axes': len(window.plot_panel.diagram.axes),
                  'plot_angle': window.plot_panel.diagram.data.config['angle'],
                  'effective_name_angle': window.plot_panel.diagram.layout['name_angle'],
                  'effective_name_size': window.plot_panel.diagram.layout['name_size_pt'],
                  'panel_widths_cm': window.plot_panel.diagram.layout['panel_widths_cm'],
                  'plot_pages': len(window.plot_panel.diagrams),
                  'all_curve_styles': sorted({opts['style'] for opts in window.plot_panel.config['taxa'].values()}),
                  'curve_selector': window.plot_panel.all_curves.currentText(),
                  'plot_size_cm': [v*2.54 for v in window.plot_panel.diagram.figure._pollen_size],
                  'y_step': window.plot_panel.diagram.layout['y_step'],
                  'y_range': [window.plot_panel.diagram.layout['y_min'],window.plot_panel.diagram.layout['y_max']],
                  'toolbar_labels': [a.text() for a in window.plot_panel.toolbar.actions() if a.text()],
                  'plot_colors': window.plot_panel.diagram.data.config['group_colors'],
                  'theme_window': app.palette().color(app.palette().ColorRole.Window).name(),
                  'theme_base': app.palette().color(app.palette().ColorRole.Base).name(),
                  'frozen': bool(getattr(sys, 'frozen', False))}
        if '--cluster' in sys.argv:
            result.update(cluster_current=analysis.result_is_current(window.project),
                          cluster_samples=len(window.project.cluster_result['y']),
                          cluster_taxa=len(window.project.cluster_result['taxa_ids']),
                          cluster_mode=window.project.cluster_config['mode'],
                          cluster_boundaries=analysis.active_boundaries(window.project),
                          cluster_zone_ids=analysis.zoning(window.project)['zone_ids'],
                          cluster_extra_axes=list(window.plot_panel.diagram.extra_axes))
        if '--pca' in sys.argv:
            from PySide6.QtCore import QEventLoop
            from . import pca
            from .plotting import export_diagram
            panel = window.pca_panel
            window.workspace.setCurrentIndex(3)
            panel.start_analysis()
            loop = QEventLoop()
            panel.thread.finished.connect(loop.quit)
            QTimer.singleShot(30000,loop.quit)
            if panel.thread.isRunning():
                loop.exec()
            app.processEvents()
            if not pca.result_is_current(window.project) or not panel.draw(report_errors=False):
                raise RuntimeError(panel.status.text())
            for extension in ('png','pdf','svg'):
                export_diagram(window.project,panel.diagram,output/f'pca.{extension}',export_dpi)
            pca.export_analysis(window.project,output/'pca-analysis.xlsx')
            app.processEvents()
            window.grab().save(str(output/'pca-workspace.png'))
            panel.tabs.setCurrentIndex(1)
            app.processEvents()
            window.grab().save(str(output/'pca-variance.png'))
            save_project(window.project,output/'example.pollen')
            r = window.project.pca_result
            result.update(pca_current=True,pca_samples=len(r['sample_ids']),pca_taxa=len(r['taxa_ids']),
                          pca_rank=r['rank'],pca_explained_ratio=r['explained_ratio'][:2],
                          pca_groups=panel.diagram.layout['groups']['labels'],pca_ellipses=panel.diagram.layout['ellipses'])
        if '--occurrence' in sys.argv:
            from . import occurrence
            from .plotting import export_diagram
            panel = window.occurrence_panel
            window.workspace.setCurrentIndex(4)
            if '--occurrence-percentages' in sys.argv:
                panel.mode.setCurrentIndex(panel.mode.findData('percentages'))
            if '--occurrence-marker' in sys.argv:
                marker = sys.argv[sys.argv.index('--occurrence-marker')+1]
                index = panel.marker.findData(marker)
                if index<0:
                    raise ValueError('未知检出符号')
                panel.marker.setCurrentIndex(index)
            if '--occurrence-group-markers' in sys.argv:
                panel.config['group_markers'] = dict(zip(GROUPS,occurrence.MARKERS))
                panel.checks['marker_by_group'].setChecked(True)
            if not panel.draw(report_errors=False):
                raise RuntimeError(panel.status.text())
            for extension in ('png','pdf','svg'):
                export_diagram(window.project,panel.diagram,output/f'occurrence.{extension}',export_dpi)
            occurrence.export_analysis(window.project,output/'occurrence-analysis.xlsx')
            app.processEvents()
            window.grab().save(str(output/'occurrence-workspace.png'))
            panel.tabs.setCurrentIndex(1)
            app.processEvents()
            window.grab().save(str(output/'occurrence-matrix.png'))
            save_project(window.project,output/'example.pollen')
            result.update(occurrence_mode=panel.config['mode'],occurrence_marker=panel.config['marker'],
                occurrence_rows=panel.diagram.layout['row_sizes'],occurrence_samples=len(panel.diagram.data.y),
                occurrence_found=panel.diagram.layout['found'],occurrence_missing=panel.diagram.layout['missing'],
                occurrence_size_cm=[panel.diagram.layout['width_cm'],panel.diagram.layout['height_cm']])
        (output / 'smoke.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        window.dirty = False
        QTimer.singleShot(0, window.close)
    exit_code = app.exec()
    window.deleteLater()
    app.processEvents()
    sys.exit(exit_code)
