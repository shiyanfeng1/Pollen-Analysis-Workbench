"""Occurrence workspace: count/percentage detection and editable automatic rows."""
import copy
import math
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QSplitter,QTreeWidget,QTreeWidgetItem,QScrollArea,QFormLayout,QGroupBox,
    QDoubleSpinBox,QSpinBox,QCheckBox,QComboBox,QLineEdit,QColorDialog,QFileDialog,
    QMessageBox,QDialog,QDialogButtonBox,QTableWidget,QTableWidgetItem,QHeaderView,
    QAbstractItemView,QTabWidget,QSlider,QSizePolicy)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from .core import GROUPS
from . import occurrence
from .occurrence_render import build_figure
from .plotting import export_diagram,fingerprint
from .plot_ui import DiagramToolbar
from .axis_ui import YAxisDialog
from .localization import error_text


class OccurrencePanel(QWidget):
    def __init__(self,changed,parent=None):
        super().__init__(parent)
        self.changed = changed
        self.project = None
        self.config = {}
        self.structure = None
        self.loading = False
        self.stale = True
        self.diagram = self.canvas = self.toolbar = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0,8,0,0)
        row = QHBoxLayout()
        for label,slot in [('生成图形',self.draw),('恢复全图',self.home),('适应整幅',self.fit),
            ('纵轴坐标设置',self.axis_dialog),('手动分排',self.row_dialog)]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        self.export_button = QPushButton('导出图形')
        self.export_button.clicked.connect(self.export_graph)
        self.export_button.setEnabled(False)
        row.addWidget(self.export_button)
        self.results_button = QPushButton('导出检出表')
        self.results_button.clicked.connect(self.export_results)
        self.results_button.setEnabled(False)
        row.addWidget(self.results_button)
        row.addStretch()
        root.addLayout(row)
        self.status = QLabel('选择展示类群和样品，然后生成出现分布图。')
        self.status.setWordWrap(True)
        self.status.setStyleSheet('padding:7px;background:#e8f1ed;color:#275848;')
        root.addWidget(self.status)
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split,1)
        left = QWidget()
        left.setMinimumWidth(175)
        left.setMaximumWidth(260)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0,0,7,0)
        ll.addWidget(QLabel('展示类群'))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemChanged.connect(self.selection_changed)
        ll.addWidget(self.tree,1)
        row = QHBoxLayout()
        for label,state in [('全选',Qt.Checked),('清空',Qt.Unchecked)]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False,v=state:self.select_all(v))
            row.addWidget(button)
        ll.addLayout(row)
        row = QHBoxLayout()
        for label,delta in [('组内上移',-1),('组内下移',1)]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False,d=delta:self.move_taxon(d))
            row.addWidget(button)
        ll.addLayout(row)
        button = QPushButton('样品选择')
        button.clicked.connect(self.sample_dialog)
        ll.addWidget(button)
        self.zero = QCheckBox('隐藏全零类群')
        self.zero.setToolTip('仅隐藏所有参与样品均可判定且均未检出的类群；含缺失或无法判定的类群保留。')
        self.zero.toggled.connect(lambda v:self.set_option('hide_zero',v))
        ll.addWidget(self.zero)
        tip = QLabel('符号大小统一。有效零值留空，缺失／无法判定以灰色 × 标记。')
        tip.setWordWrap(True)
        ll.addWidget(tip)
        split.addWidget(left)
        self.tabs = QTabWidget()
        preview = QWidget()
        cl = QVBoxLayout(preview)
        cl.setContentsMargins(0,0,0,0)
        self.navigation = QVBoxLayout()
        cl.addLayout(self.navigation)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setStyleSheet('QScrollArea { background:#e7ece9; border:1px solid #ccd9d2; }')
        cl.addWidget(self.scroll,1)
        row = QHBoxLayout()
        row.addWidget(QLabel('预览缩放'))
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(5,200)
        self.zoom.setValue(100)
        self.zoom.valueChanged.connect(self.resize_preview)
        row.addWidget(self.zoom)
        self.zoom_label = QLabel('100%')
        row.addWidget(self.zoom_label)
        cl.addLayout(row)
        self.hover = QLabel('移动鼠标查看原始计数、实际百分比、基数及检出状态。')
        self.hover.setWordWrap(True)
        cl.addWidget(self.hover)
        self.tabs.addTab(preview,'图形预览')
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.tabs.addTab(self.table,'检出矩阵')
        split.addWidget(self.tabs)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setMinimumWidth(270)
        right_scroll.setMaximumWidth(320)
        right = QWidget()
        right_scroll.setWidget(right)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(7,0,0,0)
        box = QGroupBox('检出与分排')
        form = QFormLayout(box)
        self.mode = QComboBox()
        self.mode.addItem('原始计数大于零','counts')
        self.mode.addItem('实际百分比大于零','percentages')
        self.mode.setToolTip('百分比模式使用未四舍五入的数值；需要有效的百分比计算基数。')
        self.mode.currentIndexChanged.connect(lambda:self.set_option('mode',self.mode.currentData()))
        form.addRow('检出依据',self.mode)
        self.layout_combo = QComboBox()
        for label,value in [('均衡分排','balanced'),('完整分组优先','groups'),('手动换排','manual')]:
            self.layout_combo.addItem(label,value)
        self.layout_combo.setToolTip('均衡分排尽量平分；完整分组优先可能增加排数，但每排仍不超过上限。')
        self.layout_combo.currentIndexChanged.connect(lambda:self.set_option('layout',self.layout_combo.currentData()))
        form.addRow('分排方式',self.layout_combo)
        self.row_limit = QSpinBox()
        self.row_limit.setRange(5,40)
        self.row_limit.setKeyboardTracking(False)
        self.row_limit.valueChanged.connect(lambda v:self.set_option('row_limit',v))
        form.addRow('每排上限',self.row_limit)
        rl.addWidget(box)
        box = QGroupBox('符号与样式')
        form = QFormLayout(box)
        self.marker = QComboBox()
        for key,(label,_) in occurrence.MARKERS.items():
            self.marker.addItem(label,key)
        self.marker.currentIndexChanged.connect(lambda:self.set_option('marker',self.marker.currentData()))
        form.addRow('检出符号',self.marker)
        self.checks = {}
        for key,label in [('marker_by_group','按六类分别设置符号'),('color_by_group','使用六类孢粉配色'),
            ('italic','西文类群名称斜体'),('letters','显示子图排号'),('show_boundaries','显示现有组合带边界')]:
            control = QCheckBox(label)
            control.toggled.connect(lambda v,k=key:self.set_option(k,v))
            self.checks[key] = control
            form.addRow(control)
        button = QPushButton('设置各组检出符号')
        button.clicked.connect(self.group_marker_dialog)
        form.addRow(button)
        self.controls = {}
        for key,label,lo,hi,step,decimals in [('marker_size','检出符号（磅）',.5,8,.2,1),
            ('missing_size','缺失符号（磅）',.5,8,.2,1),('width_cm','图宽（厘米）',10,60,.5,1),
            ('panel_height_cm','每排图高（厘米）',3,20,.5,1),('panel_gap_cm','排间距（厘米）',.2,4,.1,1),
            ('font_size','名称字号（磅）',5,16,.5,1),('tick_size','刻度字号（磅）',5,16,.5,1),
            ('axis_size','轴标题字号（磅）',5,18,.5,1),('angle','名称角度（°）',30,90,5,0),
            ('spine_width','坐标线宽（磅）',.2,2,.05,2),('group_line_width','分组线宽（磅）',.2,2,.05,2)]:
            control = QDoubleSpinBox()
            control.setRange(lo,hi)
            control.setSingleStep(step)
            control.setDecimals(decimals)
            control.setKeyboardTracking(False)
            control.valueChanged.connect(lambda v,k=key:self.set_option(k,v))
            self.controls[key] = control
            form.addRow(label,control)
        self.height_control = QDoubleSpinBox()
        self.height_control.setRange(0,150)
        self.height_control.setDecimals(1)
        self.height_control.setSingleStep(1)
        self.height_control.setSpecialValueText('自动适应排数与名称')
        self.height_control.setKeyboardTracking(False)
        self.height_control.valueChanged.connect(lambda v:self.set_option('height_cm',v or None))
        form.addRow('总图高（厘米）',self.height_control)
        self.font_combo = QComboBox()
        for label,value in [('西文无衬线体','Arial'),('西文衬线体','Times New Roman'),('微软雅黑','Microsoft YaHei'),('宋体','SimSun')]:
            self.font_combo.addItem(label,value)
        self.font_combo.currentIndexChanged.connect(lambda:self.set_option('font',self.font_combo.currentData()))
        form.addRow('字体',self.font_combo)
        self.title = QLineEdit()
        self.title.editingFinished.connect(lambda:self.set_option('title',self.title.text()))
        form.addRow('标题',self.title)
        for label,key in [('设置统一符号颜色','point_color'),('设置缺失符号颜色','missing_color'),('设置背景颜色','background')]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False,k=key:self.choose_color(k))
            form.addRow(button)
        self.dpi = QComboBox()
        self.dpi.addItems(['300','600'])
        self.dpi.currentTextChanged.connect(lambda v:self.set_option('dpi',int(v)))
        form.addRow('图片分辨率',self.dpi)
        rl.addWidget(box)
        rl.addStretch()
        for control in right.findChildren(QComboBox):
            control.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            control.setMinimumContentsLength(6)
            control.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        split.addWidget(right_scroll)
        split.setSizes([200,730,290])
        split.setStretchFactor(1,1)
        self.clear_preview()

    def clear_preview(self):
        self.diagram = None
        if self.toolbar:
            self.navigation.removeWidget(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        self.canvas = None
        label = QLabel('选择类群后生成图形：每排最多 40 类群，超过后自动均衡分排。')
        label.setAlignment(Qt.AlignCenter)
        label.resize(520,320)
        self.scroll.setWidget(label)

    def bind(self,project):
        structure = (project.data_type,[(t.id,t.name,t.group) for t in project.taxa],[(s.id,s.axis) for s in project.samples])
        if project is self.project and structure==self.structure:
            self.invalidate_status()
            return
        self.project,self.structure = project,structure
        self.config = occurrence.config_for(project)
        if project.data_type=='percentages':
            self.config['mode'] = 'percentages'
        self.clear_preview()
        self.rebuild_tree()
        self.sync_controls()
        self.invalidate_status()

    def ordered_taxa(self):
        order = {tid:i for i,tid in enumerate(self.config['order'])}
        return sorted(self.project.taxa,key=lambda t:(GROUPS.index(t.group),order[t.id]))

    def rebuild_tree(self,selected=None):
        self.loading = True
        self.tree.clear()
        target = None
        for group in GROUPS:
            parent = QTreeWidgetItem(self.tree,[group])
            parent.setFlags(parent.flags()|Qt.ItemIsAutoTristate|Qt.ItemIsUserCheckable)
            parent.setCheckState(0,Qt.Unchecked)
            for t in self.ordered_taxa():
                if t.group==group:
                    item = QTreeWidgetItem(parent,[t.name])
                    item.setData(0,Qt.UserRole,t.id)
                    item.setFlags(item.flags()|Qt.ItemIsUserCheckable)
                    item.setCheckState(0,Qt.Checked if t.id in self.config['taxa_ids'] else Qt.Unchecked)
                    if t.id==selected:
                        target = item
            parent.setExpanded(True)
        self.tree.setCurrentItem(target)
        self.loading = False

    def sync_controls(self):
        self.loading = True
        self.mode.model().item(0).setEnabled(self.project.data_type=='counts')
        for key,control in [('mode',self.mode),('layout',self.layout_combo),('marker',self.marker),('font',self.font_combo)]:
            i = control.findData(self.config[key])
            if i<0 and key=='font':
                control.addItem('项目自定义字体',self.config[key])
                i = control.count()-1
            control.setCurrentIndex(max(0,i))
        self.zero.setChecked(self.config['hide_zero'])
        self.row_limit.setValue(self.config['row_limit'])
        self.height_control.setValue(self.config['height_cm'] or 0)
        for key,control in self.controls.items():
            control.setValue(self.config[key])
        for key,control in self.checks.items():
            control.setChecked(self.config[key])
        self.title.setText(self.config['title'])
        self.dpi.setCurrentText(str(self.config['dpi']))
        self.loading = False

    def commit(self):
        self.project.occurrence_config = copy.deepcopy(self.config)
        self.changed()
        self.invalidate_status()

    def set_option(self,key,value):
        if self.loading or self.project is None:
            return
        if key=='layout' and value=='manual' and not self.config['manual_starts']:
            taxa = [t for t in self.ordered_taxa() if t.id in self.config['taxa_ids']]
            balanced = dict(self.config,layout='balanced')
            self.config['manual_starts'] = [row[0].id for row in occurrence.partitions(taxa,balanced)[1:]]
        self.config[key] = value
        self.commit()

    def invalidate_status(self):
        if self.project is None:
            return
        if not self.diagram or self.diagram.signature!=fingerprint(self.project):
            self.stale = True
            self.export_button.setEnabled(False)
            self.results_button.setEnabled(False)
            self.status.setText('数据或设置待更新，请点击“生成图形”。检出表与图形使用相同的检出依据。')
            self.table.setRowCount(0)

    def selection_changed(self,*args):
        if self.loading:
            return
        self.config['taxa_ids'] = [item.data(0,Qt.UserRole)
            for i in range(self.tree.topLevelItemCount()) for item in
            [self.tree.topLevelItem(i).child(j) for j in range(self.tree.topLevelItem(i).childCount())]
            if item.checkState(0)==Qt.Checked]
        self.commit()

    def select_all(self,state):
        self.loading = True
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0,state)
        self.loading = False
        self.selection_changed()

    def move_taxon(self,delta):
        item = self.tree.currentItem()
        if not item or not item.parent():
            return
        group = item.parent().text(0)
        tid = item.data(0,Qt.UserRole)
        members = [t.id for t in self.ordered_taxa() if t.group==group]
        i = members.index(tid)
        j = i+delta
        if not 0<=j<len(members):
            return
        a,b = self.config['order'].index(tid),self.config['order'].index(members[j])
        self.config['order'][a],self.config['order'][b] = self.config['order'][b],self.config['order'][a]
        self.commit()
        self.rebuild_tree(tid)

    def draw(self,checked=False,report_errors=True):
        try:
            if self.project.occurrence_config!=self.config:
                self.commit()
            diagram = build_figure(self.project)
            old = self.scroll.takeWidget()
            if self.toolbar:
                self.navigation.removeWidget(self.toolbar)
                self.toolbar.deleteLater()
            if old:
                old.deleteLater()
            self.diagram = diagram
            self.canvas = FigureCanvasQTAgg(diagram.figure)
            self.canvas.mpl_connect('motion_notify_event',self.motion)
            self.toolbar = DiagramToolbar(self.canvas,self)
            self.navigation.addWidget(self.toolbar)
            self.scroll.setWidget(self.canvas)
            self.stale = False
            self.export_button.setEnabled(True)
            self.results_button.setEnabled(True)
            self.fill_table()
            d = diagram.data
            mode = '原始计数 > 0' if self.config['mode']=='counts' else '实际百分比 > 0（未四舍五入）'
            self.status.setText(f'{mode} · {len(d.taxa)} 类群、{len(d.y)} 样品 · {len(diagram.rows)} 排（'
                +'／'.join(map(str,diagram.layout['row_sizes']))+'）'
                +f' · {diagram.layout["width_cm"]:.1f} × {diagram.layout["height_cm"]:.1f} 厘米'
                + ('\n'+'；'.join(d.warnings) if d.warnings else ''))
            self.fit()
            QTimer.singleShot(0,self.fit)
            return True
        except Exception as exc:
            self.stale = True
            self.export_button.setEnabled(False)
            self.results_button.setEnabled(False)
            self.status.setText(error_text(exc))
            if report_errors:
                QMessageBox.warning(self,'出现分布图尚未生成',error_text(exc))
            return False

    def details(self,r,j):
        d = self.diagram.data
        count = d.counts[r,j]
        pct = d.percentages[r,j]
        text = f'{d.taxa[j].name} · {self.project.axis_kind} {d.y[r]:g} {self.project.unit}'
        text += f' · 原表第 {d.indices[r]+1} 行'
        text += ' · 原始计数未知' if self.project.data_type=='percentages' else f' · 计数：{count if count is not None else "缺失"}'
        text += f' · 实际百分比：{pct:.8g}%' if np.isfinite(pct) else ' · 百分比：无法计算'
        text += ' · 原始基数未知' if self.project.data_type=='percentages' else f' · 基数：{d.bases[r] if d.bases[r] is not None else "缺失或未选择"}'
        text += ' · '+occurrence.state_label(d.states[r,j])
        if (r,j) in d.reasons:
            text += '（'+d.reasons[r,j]+'）'
        return text

    def fill_table(self):
        d = self.diagram.data
        self.table.setColumnCount(len(d.taxa)+1)
        self.table.setHorizontalHeaderLabels(['深度/年代']+[t.name for t in d.taxa])
        self.table.setRowCount(len(d.y))
        for r,y in enumerate(d.y):
            self.table.setItem(r,0,QTableWidgetItem(f'{y:g}'))
            for j in range(len(d.taxa)):
                value = d.states[r,j]
                item = QTableWidgetItem(occurrence.state_label(value))
                item.setToolTip(self.details(r,j))
                if value<0:
                    item.setForeground(QColor('#78858d'))
                self.table.setItem(r,j+1,item)
        self.table.resizeColumnsToContents()

    def resize_preview(self,percent):
        self.zoom_label.setText(f'{percent}%')
        if not self.diagram or not self.canvas:
            return
        w,h = self.diagram.figure._pollen_size
        if w*h*percent*percent>24000000:
            self.zoom.setValue(max(5,int(math.sqrt(24000000/(w*h)))))
            return
        self.diagram.figure.set_dpi(percent)
        self.canvas.setFixedSize(round(w*percent),round(h*percent))
        self.canvas.draw_idle()

    def fit(self):
        if self.diagram:
            w,h = self.diagram.figure._pollen_size
            percent = int(min((self.scroll.viewport().width()-12)/(w*100),(self.scroll.viewport().height()-12)/(h*100))*100)
            percent = max(5,min(200,percent))
            self.zoom.setValue(percent)
            self.resize_preview(percent)

    def home(self):
        if self.diagram:
            for ax,(xlim,ylim) in zip(self.diagram.axes,self.diagram.figure._pollen_limits):
                ax.set_xlim(xlim)
                ax.set_ylim(ylim)
            self.canvas.draw_idle()

    def motion(self,event):
        if self.stale or not self.diagram or event.inaxes not in self.diagram.axes or event.xdata is None or event.ydata is None:
            return
        row = self.diagram.axes.index(event.inaxes)
        col = int(round(event.xdata))
        if not 0<=col<len(self.diagram.rows[row]):
            return
        taxon = self.diagram.rows[row][col]
        j = next(i for i,t in enumerate(self.diagram.data.taxa) if t.id==taxon.id)
        r = int(np.abs(self.diagram.data.y-event.ydata).argmin())
        self.hover.setText(self.details(r,j))

    def axis_dialog(self):
        excluded = set(self.config['excluded_sample_ids'])
        coords = [s.axis for s in self.project.samples if s.id not in excluded]
        dialog = YAxisDialog(self.project,self.config,self,validator=occurrence.validate_config,coordinates=coords,
            description='全部分排共用相同的纵轴范围与刻度。样品按真实深度／年代定位；留空的项目自动设置。')
        if dialog.exec()==QDialog.Accepted:
            self.config = dialog.settings
            self.commit()
            if self.diagram:
                self.draw(report_errors=False)

    def sample_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('出现分布图样品选择')
        dialog.resize(430,520)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(len(self.project.samples),2)
        table.setHorizontalHeaderLabels(['参与绘图','深度/年代'])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for i,s in enumerate(self.project.samples):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled|Qt.ItemIsUserCheckable|Qt.ItemIsSelectable)
            check.setCheckState(Qt.Unchecked if s.id in self.config['excluded_sample_ids'] else Qt.Checked)
            table.setItem(i,0,check)
            item = QTableWidgetItem(s.axis)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            table.setItem(i,1,item)
        layout.addWidget(table)
        row = QHBoxLayout()
        def select(all_samples):
            rows = range(table.rowCount()) if all_samples else {index.row() for index in table.selectedIndexes()}
            for i in rows:
                table.item(i,0).setCheckState(Qt.Checked if all_samples else Qt.Unchecked)
        for label,value in [('纳入全部样品',True),('排除选中样品',False)]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False,v=value:select(v))
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec()==QDialog.Accepted:
            self.config['excluded_sample_ids'] = [s.id for i,s in enumerate(self.project.samples) if table.item(i,0).checkState()!=Qt.Checked]
            self.commit()

    def row_dialog(self):
        try:
            taxa = occurrence.prepare(self.project).taxa
        except ValueError:
            taxa = [t for t in self.ordered_taxa() if t.id in self.config['taxa_ids']]
        if not taxa:
            QMessageBox.information(self,'请先选择','请至少选择一个展示类群。')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('手动调整换排位置')
        dialog.resize(610,550)
        layout = QVBoxLayout(dialog)
        label = QLabel('勾选“在此前换排”可从该类群开始新的一排。类群不拆分，各排仍不得超过当前上限。')
        label.setWordWrap(True)
        layout.addWidget(label)
        table = QTableWidget(len(taxa),3)
        table.setHorizontalHeaderLabels(['在此前换排','类群','分组'])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        try:
            current = occurrence.partitions(taxa,self.config)
        except ValueError:
            current = occurrence.partitions(taxa,dict(self.config,layout='balanced'))
        starts = {row[0].id for row in current[1:]}
        for i,t in enumerate(taxa):
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemIsEnabled|Qt.ItemIsUserCheckable|Qt.ItemIsSelectable)
            item.setCheckState(Qt.Checked if t.id in starts else Qt.Unchecked)
            if i==0:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            table.setItem(i,0,item)
            for j,text in [(1,t.name),(2,t.group)]:
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(i,j,item)
        layout.addWidget(table)
        summary = QLabel()
        summary.setWordWrap(True)
        layout.addWidget(summary)
        def candidate():
            return dict(self.config,layout='manual',manual_starts=[t.id for i,t in enumerate(taxa) if table.item(i,0).checkState()==Qt.Checked])
        def update():
            try:
                rows = occurrence.partitions(taxa,candidate())
                summary.setText('各排类群数：'+'／'.join(str(len(row)) for row in rows))
            except ValueError as exc:
                summary.setText(str(exc))
        table.itemChanged.connect(update)
        def balanced():
            starts = {row[0].id for row in occurrence.partitions(taxa,dict(self.config,layout='balanced'))[1:]}
            for i,t in enumerate(taxa):
                table.item(i,0).setCheckState(Qt.Checked if t.id in starts else Qt.Unchecked)
            update()
        button = QPushButton('按均衡分排初始化')
        button.clicked.connect(balanced)
        layout.addWidget(button)
        update()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        def accept():
            cfg = candidate()
            try:
                occurrence.partitions(taxa,cfg)
            except ValueError as exc:
                QMessageBox.warning(dialog,'分排超出上限',str(exc))
                return
            self.config = cfg
            self.commit()
            self.sync_controls()
            dialog.accept()
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def group_marker_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('六类孢粉检出符号')
        form = QFormLayout(dialog)
        controls = {}
        for group in GROUPS:
            combo = QComboBox()
            for key,(label,_) in occurrence.MARKERS.items():
                combo.addItem(label,key)
            combo.setCurrentIndex(combo.findData(self.config['group_markers'].get(group,self.config['marker'])))
            controls[group] = combo
            form.addRow(group,combo)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec()==QDialog.Accepted:
            self.config['group_markers'] = {group:control.currentData() for group,control in controls.items()}
            self.config['marker_by_group'] = True
            self.commit()
            self.sync_controls()

    def choose_color(self,key):
        color = QColorDialog.getColor(QColor(self.config[key]),self,'出现分布图颜色')
        if color.isValid():
            self.set_option(key,color.name())

    def export_graph(self):
        if self.stale or not self.diagram or self.diagram.signature!=fingerprint(self.project):
            QMessageBox.information(self,'请生成图形','数据或设置已改变，请先重新生成图形。')
            return
        path,selected = QFileDialog.getSaveFileName(self,'导出出现分布图','孢粉出现分布图.pdf',
            '论文矢量文档 (*.pdf);;高清图片 (*.png);;可编辑矢量图 (*.svg)')
        if path:
            if not Path(path).suffix:
                path += '.pdf' if '*.pdf' in selected else '.svg' if '*.svg' in selected else '.png'
            try:
                export_diagram(self.project,self.diagram,path,int(self.dpi.currentText()))
                self.status.setText('已导出单幅完整出现分布图：'+path)
            except Exception as exc:
                QMessageBox.critical(self,'导出失败',error_text(exc))

    def export_results(self):
        if self.stale:
            QMessageBox.information(self,'请生成图形','请先生成图形，以核对检出依据和分排。')
            return
        path,_ = QFileDialog.getSaveFileName(self,'导出检出表','孢粉检出记录.xlsx','分析工作簿 (*.xlsx)')
        if path:
            try:
                occurrence.export_analysis(self.project,path)
                self.status.setText('检出记录已导出：'+path)
            except Exception as exc:
                QMessageBox.critical(self,'导出失败',error_text(exc))
