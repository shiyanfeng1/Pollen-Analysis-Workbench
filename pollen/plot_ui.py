"""Qt plotting workspace. All scientific transformations live in plotting.py."""
import copy
import math
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QLabel,
    QPushButton, QTreeWidget, QTreeWidgetItem, QScrollArea, QFormLayout, QGroupBox,
    QDoubleSpinBox, QSpinBox, QCheckBox, QComboBox, QLineEdit, QColorDialog,
    QFileDialog, QMessageBox, QDialog, QDialogButtonBox, QSlider)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT

from .core import GROUPS
from .plotting import (config_for, build_diagrams, export_document,
                       save_style, load_style, fingerprint, DEFAULTS, GROUP_COLORS)
from .localization import error_text
from .axis_ui import YAxisDialog


class DiagramToolbar(NavigationToolbar2QT):
    # Layout/curve editing is provided by the Chinese sidebar; no generic English editors.
    toolitems = (
        ('恢复全图', '恢复初始坐标范围', 'home', 'home'),
        ('返回', '返回上一个视图', 'back', 'back'),
        ('前进', '前进到下一个视图', 'forward', 'forward'),
        (None, None, None, None),
        ('平移', '按住鼠标左键平移；按住右键缩放', 'move', 'pan'),
        ('框选缩放', '拖动鼠标框选需要放大的区域', 'zoom_to_rect', 'zoom'),
    )

    def set_message(self, text):
        text = str(text).replace('pan/zoom', '平移/缩放').replace('zoom rect', '框选缩放')
        super().set_message(text)


class PlotPanel(QWidget):
    def __init__(self, changed, parent=None):
        super().__init__(parent)
        self.changed = changed
        self.project = None
        self.config = {}
        self.diagram = None
        self.diagrams = []
        self.canvas = None
        self.toolbar = None
        self.loading = False
        self.stale = True
        self.structure = None
        self.zoom_factor = 1.0
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        actions = QHBoxLayout()
        for label, slot in [('更新图谱', self.draw), ('恢复全图', self.home),
                            ('适应整幅', self.fit_width), ('纵轴坐标设置', self.axis_dialog),
                            ('保存绘图样式', self.save_template),
                            ('载入绘图样式', self.open_template)]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            actions.addWidget(button)
        self.export_button = QPushButton('导出完整图谱')
        self.export_button.clicked.connect(self.export)
        self.export_button.setEnabled(False)
        actions.addWidget(self.export_button)
        actions.addStretch()
        root.addLayout(actions)
        pages = QHBoxLayout()
        preset = QPushButton('恢复单幅默认版式')
        preset.clicked.connect(self.paper_preset)
        pages.addWidget(preset)
        pages.addWidget(QLabel('单幅总览 · 最多 60 个类群 · 低占比类群自动收窄'))
        pages.addStretch()
        root.addLayout(pages)
        self.banner = QLabel('请先导入数据或打开示例，然后点击“更新图谱”。')
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet('padding:7px;background:#e8f1ed;color:#275848;')
        root.addWidget(self.banner)
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)
        left = QWidget()
        left.setMinimumWidth(175)
        left.setMaximumWidth(260)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 6, 0)
        ll.addWidget(QLabel('展示类群（不改变计算基数）'))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemChanged.connect(self.visibility_changed)
        self.tree.currentItemChanged.connect(self.taxon_changed)
        ll.addWidget(self.tree, 1)
        row = QHBoxLayout()
        for label, value in [('全选', True), ('清空', False)]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, v=value: self.select_all(v))
            row.addWidget(button)
        ll.addLayout(row)
        row = QHBoxLayout()
        for label, delta in [('上移', -1), ('下移', 1)]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, d=delta: self.move_taxon(d))
            row.addWidget(button)
        ll.addLayout(row)
        self.zero = QCheckBox('隐藏全零类群')
        self.zero.toggled.connect(lambda value: self.set_global('hide_zero', value))
        ll.addWidget(self.zero)
        button = QPushButton('设置所选分组颜色')
        button.clicked.connect(self.group_color)
        ll.addWidget(button)
        button = QPushButton('恢复六组默认配色')
        button.setToolTip('应用新版六组配色，并让单类群颜色跟随所属分组。')
        button.clicked.connect(self.reset_colors)
        ll.addWidget(button)
        split.addWidget(left)
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        self.navigation = QVBoxLayout()
        cl.addLayout(self.navigation)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setStyleSheet('QScrollArea { background: #e7ece9; border:1px solid #ccd9d2; }')
        placeholder = QLabel('在这里预览孢粉百分比图谱\n\n共享深度/年代轴 · 六组配色 · 独立百分比横轴')
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.resize(540, 380)
        self.scroll.setWidget(placeholder)
        cl.addWidget(self.scroll, 1)
        zoomrow = QHBoxLayout()
        zoomrow.addWidget(QLabel('预览缩放'))
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(15, 180)
        self.zoom.setValue(100)
        self.zoom.valueChanged.connect(self.resize_preview)
        zoomrow.addWidget(self.zoom)
        self.zoom_label = QLabel('100%')
        zoomrow.addWidget(self.zoom_label)
        cl.addLayout(zoomrow)
        self.hover = QLabel('移动鼠标查看样品数值。工具栏可平移、框选缩放；导出使用完整图谱范围。')
        self.hover.setWordWrap(True)
        cl.addWidget(self.hover)
        split.addWidget(center)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setMinimumWidth(248)
        right_scroll.setMaximumWidth(300)
        right = QWidget()
        right_scroll.setWidget(right)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        self.global_controls = {}
        global_box = QGroupBox('整体样式')
        form = QFormLayout(global_box)
        self.all_curves = QComboBox()
        self.all_curves.addItem('各类群分别设置', None)
        self.all_curves.addItem('填色＋轮廓线', 'fill')
        self.all_curves.addItem('折线', 'line')
        self.all_curves.addItem('水平柱线', 'bar')
        self.all_curves.model().item(0).setEnabled(False)
        self.all_curves.setToolTip('选择后统一替换全部类群的曲线形式，包括未展示的类群；仍可在“当前类群”中单独调整。')
        self.all_curves.currentIndexChanged.connect(self.set_all_curves)
        form.addRow('统一曲线形式', self.all_curves)
        self.title_edit = QLineEdit()
        self.title_edit.editingFinished.connect(lambda: self.set_global('title', self.title_edit.text()))
        form.addRow('标题', self.title_edit)
        button = QPushButton('纵轴坐标设置')
        button.clicked.connect(self.axis_dialog)
        form.addRow(button)
        for key, label, low, high, step, decimals in [
            ('angle', '名称角度（°）', 0, 90, 5, 0),
            ('font_size', '名称字号（磅）', 5, 18, .5, 1),
            ('tick_size', '刻度字号（磅）', 5, 16, .5, 1),
            ('axis_size', '坐标标题字号（磅）', 5, 18, .5, 1),
            ('width_cm', '最小图幅宽（厘米）', 8, 60, 1, 1),
            ('height_cm', '图幅高（厘米）', 6, 40, 1, 1),
            ('line_width', '轮廓线宽（磅）', .1, 3, .05, 2),
            ('spine_width', '坐标线宽（磅）', .2, 2, .05, 2),
            ('gap_cm', '列间距（厘米）', 0, 3, .05, 2),
            ('group_gap_cm', '组间距（厘米）', 0, 5, .05, 2)]:
            control = QDoubleSpinBox()
            control.setRange(low, high)
            control.setDecimals(decimals)
            control.setSingleStep(step)
            control.setKeyboardTracking(False)
            control.valueChanged.connect(lambda value, k=key: self.set_global(k, value))
            form.addRow(label, control)
            self.global_controls[key] = control
        self.font_combo = QComboBox()
        for label, family in [('西文无衬线体', 'Arial'), ('西文衬线体', 'Times New Roman'), ('微软雅黑', 'Microsoft YaHei'), ('宋体', 'SimSun')]:
            self.font_combo.addItem(label, family)
        self.font_combo.currentIndexChanged.connect(lambda index: self.set_global('font', self.font_combo.itemData(index)))
        form.addRow('字体', self.font_combo)
        self.width_mode = QComboBox()
        self.width_mode.addItems(['按占比自动分配', '等宽分配'])
        self.width_mode.currentIndexChanged.connect(lambda index: self.set_global('width_mode', 'abundance' if index == 0 else 'equal'))
        form.addRow('子图列宽', self.width_mode)
        self.rare_threshold = QDoubleSpinBox()
        self.rare_threshold.setRange(0, 100)
        self.rare_threshold.setDecimals(1)
        self.rare_threshold.setKeyboardTracking(False)
        self.rare_threshold.valueChanged.connect(lambda value: self.set_global('rare_threshold', value))
        self.rare_threshold.setToolTip('最大百分比不超过阈值的类群使用可读的窄列，仅改变图形宽度。')
        form.addRow('低占比阈值（%）', self.rare_threshold)
        self.auto_dense = QCheckBox('密集时自动竖排、适度加宽')
        self.auto_dense.setToolTip('超过 24 类群时名称竖排，图宽在设定值基础上适度增加；自动建议宽度最多 25.5 厘米。窄列仅标横轴上限。')
        self.auto_dense.toggled.connect(lambda value: self.set_global('auto_dense', value))
        form.addRow(self.auto_dense)
        self.reverse = QCheckBox('纵轴向下增加')
        self.reverse.toggled.connect(lambda value: self.set_global('reverse_y', value))
        form.addRow(self.reverse)
        self.sort = QCheckBox('仅绘图按坐标递增排序')
        self.sort.toggled.connect(lambda value: self.set_global('sort_y', value))
        form.addRow(self.sort)
        self.xmode = QComboBox()
        self.xmode.addItems(['各类群自动范围', '统一横轴上限'])
        self.xmode.currentIndexChanged.connect(lambda index: self.set_global('x_mode', 'auto' if index == 0 else 'uniform'))
        form.addRow('横轴', self.xmode)
        self.uniform_max = QDoubleSpinBox()
        self.uniform_max.setRange(.001, 1000000)
        self.uniform_max.setDecimals(3)
        self.uniform_max.setKeyboardTracking(False)
        self.uniform_max.valueChanged.connect(lambda value: self.set_global('x_max', value))
        form.addRow('统一上限（%）', self.uniform_max)
        button = QPushButton('页边距设置')
        button.clicked.connect(self.margin_dialog)
        form.addRow(button)
        self.dpi = QComboBox()
        self.dpi.addItems(['300', '600'])
        self.dpi.currentTextChanged.connect(lambda value: self.set_global('dpi', int(value)))
        form.addRow('图片分辨率', self.dpi)
        rl.addWidget(global_box)
        self.taxon_box = QGroupBox('当前类群')
        tf = QFormLayout(self.taxon_box)
        self.taxon_label = QLabel('在左侧选择一个类群')
        self.taxon_label.setWordWrap(True)
        tf.addRow(self.taxon_label)
        self.curve = QComboBox()
        self.curve.addItems(['填色＋轮廓线', '折线', '水平柱线'])
        self.curve.currentIndexChanged.connect(lambda index: self.set_taxon('style', ('fill', 'line', 'bar')[index]))
        tf.addRow('曲线形式', self.curve)
        self.tax_max = QDoubleSpinBox()
        self.tax_max.setDecimals(3)
        self.tax_max.setRange(0, 1000000)
        self.tax_max.setSpecialValueText('自动 / 跟随整体')
        self.tax_max.setKeyboardTracking(False)
        self.tax_max.valueChanged.connect(lambda value: self.set_taxon('x_max', value or None))
        tf.addRow('横轴上限（%）', self.tax_max)
        self.tax_step = QDoubleSpinBox()
        self.tax_step.setDecimals(3)
        self.tax_step.setRange(0, 1000000)
        self.tax_step.setSpecialValueText('自动')
        self.tax_step.setKeyboardTracking(False)
        self.tax_step.valueChanged.connect(lambda value: self.set_taxon('x_step', value or None))
        tf.addRow('刻度间隔（%）', self.tax_step)
        self.exaggeration = QDoubleSpinBox()
        self.exaggeration.setRange(1, 20)
        self.exaggeration.setKeyboardTracking(False)
        self.exaggeration.valueChanged.connect(lambda value: self.set_taxon('exaggeration', value))
        tf.addRow('放大倍数', self.exaggeration)
        self.column_width = QDoubleSpinBox()
        self.column_width.setRange(.3, 5)
        self.column_width.setSingleStep(.1)
        self.column_width.setKeyboardTracking(False)
        self.column_width.valueChanged.connect(lambda value: self.set_taxon('width', value))
        tf.addRow('列宽权重', self.column_width)
        row = QHBoxLayout()
        self.color_button = QPushButton('自定义颜色')
        self.color_button.clicked.connect(self.taxon_color)
        row.addWidget(self.color_button)
        button = QPushButton('跟随分组')
        button.clicked.connect(lambda: self.set_taxon('color', ''))
        row.addWidget(button)
        tf.addRow(row)
        rl.addWidget(self.taxon_box)
        rl.addStretch()
        split.addWidget(right_scroll)
        split.setStretchFactor(1, 1)
        split.setSizes([190, 700, 270])

    def bind(self, project):
        structure = [(t.id, t.name, t.group) for t in project.taxa]
        replaced = project is not self.project
        self.project = project
        if replaced or structure != self.structure:
            self.config = config_for(project)
            self.structure = structure
            self.sync_controls()
            self.rebuild_tree()
            self.mark_stale()
            if replaced:
                self.clear_preview()

    def clear_preview(self):
        self.diagram = None
        self.diagrams = []
        if self.toolbar:
            self.navigation.removeWidget(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        self.canvas = None
        placeholder = QLabel('请点击“更新图谱”生成预览')
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.resize(500, 350)
        self.scroll.setWidget(placeholder)

    def sync_controls(self):
        self.loading = True
        self.sync_curve_selector()
        self.title_edit.setText(self.config['title'])
        for key, control in self.global_controls.items():
            control.setValue(self.config[key])
        self.zero.setChecked(self.config['hide_zero'])
        self.width_mode.setCurrentIndex(0 if self.config['width_mode'] == 'abundance' else 1)
        self.rare_threshold.setValue(self.config['rare_threshold'])
        self.auto_dense.setChecked(self.config['auto_dense'])
        self.reverse.setChecked(self.config['reverse_y'])
        self.sort.setChecked(self.config['sort_y'])
        self.xmode.setCurrentIndex(0 if self.config['x_mode'] == 'auto' else 1)
        self.uniform_max.setValue(self.config['x_max'])
        font_index = self.font_combo.findData(self.config['font'])
        if font_index < 0:
            self.font_combo.addItem('项目自定义字体', self.config['font'])
            font_index = self.font_combo.count()-1
        self.font_combo.setCurrentIndex(font_index)
        self.dpi.setCurrentText(str(self.config['dpi']))
        self.loading = False

    def rebuild_tree(self, selected_id=None):
        self.loading = True
        self.tree.clear()
        target = None
        for group in GROUPS:
            parent = QTreeWidgetItem(self.tree, [group])
            parent.setFlags(parent.flags() | Qt.ItemIsAutoTristate | Qt.ItemIsUserCheckable)
            parent.setCheckState(0, Qt.Unchecked)
            parent.setForeground(0, QColor('#29483e'))
            taxa = sorted([t for t in self.project.taxa if t.group == group], key=lambda t: self.config['taxa'][t.id]['order'])
            for t in taxa:
                child = QTreeWidgetItem(parent, [t.name])
                child.setData(0, Qt.UserRole, t.id)
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Checked if self.config['taxa'][t.id]['visible'] else Qt.Unchecked)
                child.setToolTip(0, '仅控制图谱展示，不改变百分比基数')
                if t.id == selected_id:
                    target = child
            parent.setExpanded(True)
        self.loading = False
        self.tree.setCurrentItem(target)
        self.taxon_changed(target, None)

    def mark_stale(self):
        self.stale = True
        self.export_button.setEnabled(False)
        self.banner.setText('数据或设置待更新。点击“更新图谱”后再导出。')
        self.banner.setStyleSheet('padding:7px;background:#fff0d8;color:#805a1f;')

    def commit(self):
        self.project.plot_config = copy.deepcopy(self.config)
        self.changed()
        self.mark_stale()

    def set_global(self, key, value):
        if not self.loading and self.project is not None:
            self.config[key] = value
            self.commit()

    def current_id(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item and item.parent() else None

    def set_taxon(self, key, value):
        tid = self.current_id()
        if not self.loading and tid:
            self.config['taxa'][tid][key] = value
            self.commit()
            if key == 'style':
                self.sync_curve_selector()
            if key == 'color':
                self.taxon_changed(self.tree.currentItem(), None)

    def sync_curve_selector(self):
        styles = {opts['style'] for opts in self.config.get('taxa', {}).values()}
        index = self.all_curves.findData(next(iter(styles))) if len(styles) == 1 else 0
        previous = self.all_curves.blockSignals(True)
        self.all_curves.setCurrentIndex(index)
        self.all_curves.blockSignals(previous)

    def set_all_curves(self, index):
        if self.loading or self.project is None:
            return
        style = self.all_curves.itemData(index)
        if style not in ('fill', 'line', 'bar'):
            return
        for opts in self.config['taxa'].values():
            opts['style'] = style
        self.commit()
        self.taxon_changed(self.tree.currentItem(), None)

    def visibility_changed(self, item, column):
        if self.loading:
            return
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            for j in range(parent.childCount()):
                child = parent.child(j)
                self.config['taxa'][child.data(0, Qt.UserRole)]['visible'] = child.checkState(0) == Qt.Checked
        self.commit()

    def taxon_changed(self, item, previous):
        if self.loading:
            return
        tid = self.current_id()
        self.taxon_box.setEnabled(bool(tid))
        if not tid:
            self.taxon_label.setText('在左侧选择一个类群')
            return
        self.loading = True
        taxon = next(t for t in self.project.taxa if t.id == tid)
        opts = self.config['taxa'][tid]
        self.taxon_label.setText(f'{taxon.group} / {taxon.name}')
        self.curve.setCurrentIndex(('fill', 'line', 'bar').index(opts['style']))
        self.tax_max.setValue(opts['x_max'] or 0)
        self.tax_step.setValue(opts['x_step'] or 0)
        self.exaggeration.setValue(opts['exaggeration'])
        self.column_width.setValue(opts['width'])
        color = opts['color'] or self.config['group_colors'][taxon.group]
        self.color_button.setStyleSheet(f'border-left: 12px solid {color};')
        self.loading = False

    def select_all(self, value):
        for opts in self.config['taxa'].values():
            opts['visible'] = value
        self.commit()
        self.rebuild_tree()

    def move_taxon(self, delta):
        tid = self.current_id()
        if not tid:
            return
        taxon = next(t for t in self.project.taxa if t.id == tid)
        members = sorted([t for t in self.project.taxa if t.group == taxon.group], key=lambda t: self.config['taxa'][t.id]['order'])
        index = members.index(taxon)
        other = index + delta
        if not 0 <= other < len(members):
            return
        members[index], members[other] = members[other], members[index]
        for i, t in enumerate(members):
            self.config['taxa'][t.id]['order'] = i
        self.commit()
        self.rebuild_tree(tid)

    def group_color(self):
        item = self.tree.currentItem()
        if not item:
            return
        group = item.parent().text(0) if item.parent() else item.text(0)
        color = QColorDialog.getColor(QColor(self.config['group_colors'][group]), self, f'{group}默认颜色')
        if color.isValid():
            tid = self.current_id()
            self.config['group_colors'][group] = color.name().upper()
            self.commit()
            self.rebuild_tree(tid)

    def reset_colors(self):
        self.config['group_colors'] = copy.deepcopy(GROUP_COLORS)
        self.config['palette_version'] = 2
        for opts in self.config['taxa'].values():
            opts['color'] = ''
        tid = self.current_id()
        self.commit()
        self.rebuild_tree(tid)

    def taxon_color(self):
        tid = self.current_id()
        if tid:
            taxon = next(t for t in self.project.taxa if t.id == tid)
            color = self.config['taxa'][tid]['color'] or self.config['group_colors'][taxon.group]
            color = QColorDialog.getColor(QColor(color), self, '类群颜色')
            if color.isValid():
                self.set_taxon('color', color.name().upper())

    def axis_dialog(self):
        dialog = YAxisDialog(self.project,self.config,self)
        if dialog.exec() == QDialog.Accepted:
            self.config = dialog.settings
            self.commit()
            self.sync_controls()
            if self.diagram is not None:
                self.draw(report_errors=False)

    def margin_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('页边距设置')
        form = QFormLayout(dialog)
        entries = {}
        for key, label in [('left_cm', '左边距（厘米）'),
                           ('right_cm', '右边距（厘米）'), ('top_cm', '最小上边距（厘米）'), ('bottom_cm', '下边距（厘米）')]:
            value = self.config[key]
            entry = QLineEdit('' if value is None else str(value))
            form.addRow(label, entry)
            entries[key] = entry
        tip = QLabel('设置论文图的页面留白，单位为厘米。')
        form.addRow(tip)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        form.addRow(buttons)
        def accept():
            from .plotting import validate_config
            try:
                config = copy.deepcopy(self.config)
                for key, entry in entries.items():
                    text = entry.text().strip()
                    try:
                        config[key] = float(text)
                    except ValueError:
                        raise ValueError('页边距必须填写数字，单位为厘米') from None
                validate_config(config)
                self.config = config
                self.commit()
                dialog.accept()
            except ValueError as exc:
                QMessageBox.warning(dialog, '设置无效', str(exc))
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        dialog.exec()

    def paper_preset(self):
        keep = {'title', 'font', 'angle', 'reverse_y', 'sort_y', 'y_min', 'y_max', 'y_step',
                'x_mode', 'x_max', 'hide_zero', 'dpi', 'group_colors', 'taxa'}
        for key, value in DEFAULTS.items():
            if key not in keep:
                self.config[key] = copy.deepcopy(value)
        self.commit()
        self.sync_controls()

    def draw(self, checked=False, report_errors=True):
        if self.project is None:
            return False
        try:
            # Resolve current defaults without altering calculation membership.
            if self.project.plot_config != self.config:
                self.commit()
            self.diagrams = build_diagrams(self.project)
            self.show_page(0)
            self.stale = False
            self.export_button.setEnabled(True)
            diagram = self.diagram
            width, height = diagram.figure._pollen_size
            count = sum(len(d.data.taxa) for d in self.diagrams)
            message = f'{count} 个类群 · 单幅 {width*2.54:.1f} × {height*2.54:.1f} 厘米 · 纵轴刻度间隔 {diagram.layout["y_step"]:g}'
            message += f'\n名称实际角度 {diagram.layout["name_angle"]:g}°、字号 {diagram.layout["name_size_pt"]:g} 磅；窄列横轴仅标上限，列宽不代表相同的百分比比例尺。'
            if diagram.data.warnings:
                message += '\n' + '；'.join(diagram.data.warnings)
            self.banner.setText(message)
            self.banner.setStyleSheet('padding:7px;background:#e8f1ed;color:#275848;')
            self.fit_width()
            return True
        except Exception as exc:
            self.mark_stale()
            self.banner.setText(error_text(exc))
            if report_errors:
                QMessageBox.warning(self, '图谱尚未生成', error_text(exc))
            return False

    def show_page(self, index):
        if not 0 <= index < len(self.diagrams):
            return
        old_canvas = self.scroll.takeWidget()
        if self.toolbar:
            self.navigation.removeWidget(self.toolbar)
            self.toolbar.deleteLater()
        if old_canvas:
            old_canvas.deleteLater()
        self.diagram = self.diagrams[index]
        self.canvas = FigureCanvasQTAgg(self.diagram.figure)
        self.canvas.mpl_connect('motion_notify_event', self.hover_value)
        self.toolbar = DiagramToolbar(self.canvas, self)
        self.navigation.addWidget(self.toolbar)
        self.scroll.setWidget(self.canvas)
        self.fit_width()

    def resize_preview(self, percent):
        self.zoom_label.setText(f'{percent}%')
        if not self.canvas or not self.diagram:
            return
        width, height = self.diagram.figure._pollen_size
        dpi = 100 * percent / 100
        if width * height * dpi * dpi > 24000000:
            self.zoom.setValue(max(15, int(math.sqrt(24000000 / (width * height)))))
            return
        self.diagram.figure.set_dpi(dpi)
        self.canvas.setFixedSize(round(width * dpi), round(height * dpi))
        self.canvas.draw_idle()

    def fit_width(self):
        if not self.diagram:
            return
        width, height = [v*100 for v in self.diagram.figure._pollen_size]
        ratio = min((self.scroll.viewport().width()-12)/width, (self.scroll.viewport().height()-12)/height)
        percent = max(15, min(180, int(ratio*100)))
        self.zoom.setValue(percent)
        self.resize_preview(percent)

    def home(self):
        if self.diagram:
            for ax, (xlim, ylim) in zip(getattr(self.diagram.figure,'_pollen_axes',self.diagram.axes), self.diagram.figure._pollen_limits):
                ax.set_xlim(xlim)
                ax.set_ylim(ylim)
            self.canvas.draw_idle()

    def hover_value(self, event):
        if self.stale:
            self.hover.setText('图谱待更新，请先点击“更新图谱”。')
            return
        if not self.diagram or event.inaxes not in self.diagram.axes or event.ydata is None:
            return
        i = self.diagram.axes.index(event.inaxes)
        t = self.diagram.data.taxa[i]
        r = int(abs(self.diagram.data.y - event.ydata).argmin())
        value = self.diagram.data.values[t.id][r]
        text = '缺失' if not math.isfinite(value) else f'{value:.{self.project.decimals}f}%'
        base = '导入百分比；原始计数与基数未知' if self.project.data_type=='percentages' else f'实际基数 {self.diagram.data.bases[r]}'
        self.hover.setText(f'{t.group} / {t.name}  ·  {self.project.axis_kind} {self.diagram.data.y[r]:g} {self.project.unit}'
                           f'  ·  {text}  ·  {base}  ·  原表第 {self.diagram.data.indices[r]+1} 行')

    def export(self):
        if self.stale or not self.diagram or self.diagram.signature != fingerprint(self.project):
            self.mark_stale()
            QMessageBox.information(self, '请更新图谱', '数据或设置已改变，请先更新图谱。')
            return
        path, selected = QFileDialog.getSaveFileName(self, '导出完整图谱', '孢粉百分比图谱.pdf',
            '论文矢量文档 (*.pdf);;高清图片 (*.png);;可编辑矢量图 (*.svg)')
        if not path:
            return
        if not Path(path).suffix:
            path += '.pdf' if '*.pdf' in selected else '.svg' if '*.svg' in selected else '.png'
        try:
            export_document(self.project, self.diagrams, path, int(self.dpi.currentText()))
            self.banner.setText('已导出单幅完整图谱：' + path)
        except Exception as exc:
            QMessageBox.critical(self, '导出未完成', error_text(exc))

    def save_template(self):
        path, _ = QFileDialog.getSaveFileName(self, '保存绘图样式', '孢粉绘图样式.pollenstyle', '绘图样式 (*.pollenstyle)')
        if path:
            try:
                save_style(self.project, path)
            except Exception as exc:
                QMessageBox.warning(self, '保存失败', error_text(exc))

    def open_template(self):
        path, _ = QFileDialog.getOpenFileName(self, '载入绘图样式', '', '绘图样式 (*.pollenstyle)')
        if path:
            try:
                self.config = load_style(self.project, path)
                self.commit()
                self.sync_controls()
                self.rebuild_tree()
            except Exception as exc:
                QMessageBox.warning(self, '载入失败', error_text(exc))
