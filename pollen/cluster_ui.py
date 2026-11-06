"""Chinese CONISS workspace, selection, zonation and traceable manual editing."""
import copy
import math
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSplitter, QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QComboBox, QSpinBox, QDoubleSpinBox,
    QCheckBox, QFormLayout, QGroupBox, QMessageBox, QInputDialog, QFileDialog, QDialog,
    QScrollArea, QColorDialog)

from .core import GROUPS, default_analysis_taxa
from . import clustering as analysis
from .localization import error_text


class AnalysisWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.snapshot = copy.deepcopy(project)

    def run(self):
        try:
            self.ready.emit(analysis.run_analysis(self.snapshot, cancelled=self.isInterruptionRequested))
        except Exception as exc:
            self.failed.emit(error_text(exc))
        finally:
            self.snapshot = None


class ClusterPanel(QWidget):
    def __init__(self, changed, parent=None):
        super().__init__(parent)
        self.changed = changed
        self.project = None
        self.config = {}
        self.structure = None
        self.loading = False
        self.thread = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0,8,0,0)
        toolbar = QHBoxLayout()
        self.run_button = QPushButton('计算 CONISS')
        self.run_button.clicked.connect(self.start_analysis)
        toolbar.addWidget(self.run_button)
        self.cancel_button = QPushButton('取消计算')
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        toolbar.addWidget(self.cancel_button)
        self.export_button = QPushButton('导出分析结果')
        self.export_button.clicked.connect(self.export)
        toolbar.addWidget(self.export_button)
        reference = QPushButton('带数参考（断棍模型）')
        reference.clicked.connect(self.show_reference)
        toolbar.addWidget(reference)
        toolbar.addStretch()
        root.addLayout(toolbar)
        self.status = QLabel('选择参与聚类的类群和样品，然后计算。')
        self.status.setWordWrap(True)
        self.status.setStyleSheet('padding:8px;background:#e8f1ed;color:#275848;')
        root.addWidget(self.status)
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split,1)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0,0,8,0)
        ll.addWidget(QLabel('参与聚类的类群（独立于绘图勾选）'))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemChanged.connect(self.selection_changed)
        ll.addWidget(self.tree,1)
        row = QHBoxLayout()
        for label, mode in [('全选','all'),('清空','none'),('按基数选择','base')]:
            button = QPushButton(label)
            if mode=='base':
                self.base_button = button
            button.clicked.connect(lambda checked=False,m=mode:self.select_taxa(m))
            row.addWidget(button)
        ll.addLayout(row)
        tip = QLabel('沿用原百分比基数；筛除聚类类群后不重新归一化。空白不补零。')
        self.input_hint = tip
        tip.setWordWrap(True)
        ll.addWidget(tip)
        split.addWidget(left)
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0,0,8,0)
        cl.addWidget(QLabel('样品选择 · 取消勾选可明确排除样品'))
        self.samples = QTableWidget(0,3)
        self.samples.setHorizontalHeaderLabels(['参与','深度/年代','原表数据行'])
        self.samples.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.samples.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.samples.setAlternatingRowColors(True)
        self.samples.itemChanged.connect(self.selection_changed)
        cl.addWidget(self.samples,1)
        row = QHBoxLayout()
        for label,value in [('纳入全部样品',True),('排除选中样品',False)]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False,v=value:self.select_samples(v))
            row.addWidget(button)
        cl.addLayout(row)
        cl.addWidget(QLabel('组合带（从较深／较老端的Ⅰ带开始）· 双击带名修改'))
        self.zones = QTableWidget(0,3)
        self.zones.setHorizontalHeaderLabels(['带号','带名','坐标范围'])
        self.zones.horizontalHeader().setStretchLastSection(True)
        self.zones.setMaximumHeight(190)
        self.zones.itemChanged.connect(self.zone_name_changed)
        cl.addWidget(self.zones)
        split.addWidget(center)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setMinimumWidth(310)
        right_scroll.setMaximumWidth(430)
        right = QWidget()
        right_scroll.setWidget(right)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0,0,0,0)
        settings = QGroupBox('分析与分带设置')
        form = QFormLayout(settings)
        self.transform = QComboBox()
        self.transform.addItem('平方根变换','sqrt')
        self.transform.addItem('不变换（百分比转比例）','none')
        self.distance = QComboBox()
        self.distance.addItem('欧氏距离','euclidean')
        self.distance.addItem('欧氏距离平方','squared_euclidean')
        self.distance.setToolTip('默认距离约定与 rioja 常见调用一致；平方模式衡量变换后矩阵的平方和。')
        self.transform.currentIndexChanged.connect(self.settings_changed)
        self.distance.currentIndexChanged.connect(self.settings_changed)
        form.addRow('数据变换',self.transform)
        form.addRow('距离',self.distance)
        self.mode = QComboBox()
        self.mode.addItem('算法分带','algorithm')
        self.mode.addItem('人工分带','manual')
        self.mode.currentIndexChanged.connect(self.mode_changed)
        form.addRow('分带模式',self.mode)
        self.n_zones = QSpinBox()
        self.n_zones.setRange(1,2000)
        self.n_zones.setKeyboardTracking(False)
        self.n_zones.valueChanged.connect(self.settings_changed)
        form.addRow('算法带数',self.n_zones)
        self.tree_check = QCheckBox('在图谱中显示聚类树')
        self.zone_check = QCheckBox('在图谱中显示组合带栏')
        self.line_check = QCheckBox('显示孢粉图与组合带栏的分带线')
        for checkbox in (self.tree_check,self.zone_check,self.line_check):
            checkbox.toggled.connect(self.settings_changed)
            form.addRow(checkbox)
        self.tree_width = QDoubleSpinBox()
        self.tree_width.setRange(1.5,10)
        self.tree_width.setSingleStep(.2)
        self.tree_width.setKeyboardTracking(False)
        self.zone_width = QDoubleSpinBox()
        self.zone_width.setRange(.5,5)
        self.zone_width.setSingleStep(.1)
        self.zone_width.setKeyboardTracking(False)
        self.tree_width.valueChanged.connect(self.settings_changed)
        self.zone_width.valueChanged.connect(self.settings_changed)
        form.addRow('树宽（厘米）',self.tree_width)
        form.addRow('带栏宽（厘米）',self.zone_width)
        self.line_style = QComboBox()
        for label,value in [('虚线','--'),('实线','-'),('点线',':'),('点划线','-.')]:
            self.line_style.addItem(label,value)
        self.line_style.currentIndexChanged.connect(self.settings_changed)
        form.addRow('分带线型',self.line_style)
        self.line_width = QDoubleSpinBox()
        self.line_width.setRange(.1,3)
        self.line_width.setSingleStep(.05)
        self.line_width.setDecimals(2)
        self.line_width.setKeyboardTracking(False)
        self.line_width.valueChanged.connect(self.settings_changed)
        form.addRow('分带线宽（磅）',self.line_width)
        self.line_color = QPushButton('设置分带线颜色')
        self.line_color.clicked.connect(self.choose_line_color)
        form.addRow(self.line_color)
        rl.addWidget(settings)
        rl.addWidget(QLabel('分带边界 · 原始算法树始终保留'))
        self.boundaries = QTableWidget(0,2)
        self.boundaries.setHorizontalHeaderLabels(['边界坐标','两侧样品区间'])
        self.boundaries.horizontalHeader().setStretchLastSection(True)
        self.boundaries.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.boundaries.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.boundaries.setSelectionMode(QAbstractItemView.SingleSelection)
        self.boundaries.setMinimumHeight(140)
        rl.addWidget(self.boundaries,1)
        row = QHBoxLayout()
        for label,slot in [('新增',self.add_boundary),('修改',self.move_boundary),('删除',self.delete_boundary)]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        rl.addLayout(row)
        reset = QPushButton('恢复算法分带')
        reset.clicked.connect(self.restore_algorithm)
        rl.addWidget(reset)
        split.addWidget(right_scroll)
        split.setSizes([260,470,350])

    def bind(self, project):
        structure = (project.data_type,[ (t.id,t.name,t.group) for t in project.taxa],[(s.id,s.axis) for s in project.samples])
        if project is self.project and structure == self.structure:
            self.refresh_status()
            return
        self.project = project
        self.structure = structure
        self.config = analysis.config_for(project)
        self.base_button.setVisible(project.data_type=='counts')
        self.input_hint.setText('直接采用导入百分比；筛除类群后不重新归一化。空白不补零。' if project.data_type=='percentages' else
            '沿用原百分比基数；筛除聚类类群后不重新归一化。空白不补零。')
        self.loading = True
        self.tree.clear()
        selected = set(self.config['taxa_ids'])
        for group in GROUPS:
            parent = QTreeWidgetItem(self.tree,[group])
            parent.setFlags(parent.flags() | Qt.ItemIsAutoTristate | Qt.ItemIsUserCheckable)
            parent.setCheckState(0,Qt.Unchecked)
            for t in project.ordered_taxa():
                if t.group == group:
                    item = QTreeWidgetItem(parent,[t.name])
                    item.setData(0,Qt.UserRole,t.id)
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(0,Qt.Checked if t.id in selected else Qt.Unchecked)
            parent.setExpanded(True)
        self.samples.setRowCount(len(project.samples))
        excluded = set(self.config['excluded_sample_ids'])
        for i,s in enumerate(project.samples):
            item = QTableWidgetItem('')
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            item.setCheckState(Qt.Unchecked if s.id in excluded else Qt.Checked)
            self.samples.setItem(i,0,item)
            self.samples.setItem(i,1,QTableWidgetItem(s.axis))
            self.samples.setItem(i,2,QTableWidgetItem(str(i+1)))
        self.transform.setCurrentIndex(max(0,self.transform.findData(self.config['transform'])))
        self.distance.setCurrentIndex(max(0,self.distance.findData(self.config['distance'])))
        self.mode.setCurrentIndex(1 if self.config['mode']=='manual' else 0)
        self.n_zones.setMaximum(max(1,len(project.samples)))
        self.n_zones.setValue(self.config['n_zones'])
        self.tree_check.setChecked(self.config['show_tree'])
        self.zone_check.setChecked(self.config['show_zones'])
        self.line_check.setChecked(self.config['show_lines'])
        self.tree_width.setValue(self.config['tree_width_cm'])
        self.zone_width.setValue(self.config['zone_width_cm'])
        self.line_style.setCurrentIndex(max(0,self.line_style.findData(self.config['line_style'])))
        self.line_width.setValue(self.config['line_width'])
        self.loading = False
        self.refresh_status()

    def commit(self):
        self.project.cluster_config = copy.deepcopy(self.config)
        self.changed()
        self.refresh_status()

    def selection_changed(self,*args):
        if self.loading or self.project is None:
            return
        ids = []
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            for j in range(parent.childCount()):
                item = parent.child(j)
                if item.checkState(0) == Qt.Checked:
                    ids.append(item.data(0,Qt.UserRole))
        self.config['taxa_ids'] = ids
        self.config['excluded_sample_ids'] = [s.id for i,s in enumerate(self.project.samples)
                                             if self.samples.item(i,0).checkState() != Qt.Checked]
        self.commit()

    def select_taxa(self, mode):
        self.loading = True
        base = set(default_analysis_taxa(self.project))
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            for j in range(parent.childCount()):
                item = parent.child(j)
                yes = mode == 'all' or (mode=='base' and item.data(0,Qt.UserRole) in base)
                item.setCheckState(0,Qt.Checked if yes else Qt.Unchecked)
        self.loading = False
        self.selection_changed()

    def select_samples(self, include_all):
        rows = range(self.samples.rowCount()) if include_all else {i.row() for i in self.samples.selectedIndexes()}
        self.loading = True
        for i in rows:
            self.samples.item(i,0).setCheckState(Qt.Checked if include_all else Qt.Unchecked)
        self.loading = False
        self.selection_changed()

    def settings_changed(self,*args):
        if self.loading or self.project is None:
            return
        for key, value in {'transform':self.transform.currentData(), 'distance':self.distance.currentData(),
                            'n_zones':self.n_zones.value(), 'show_tree':self.tree_check.isChecked(),
                            'show_zones':self.zone_check.isChecked(), 'show_lines':self.line_check.isChecked(),
                            'tree_width_cm':self.tree_width.value(), 'zone_width_cm':self.zone_width.value(),
                            'line_style':self.line_style.currentData(), 'line_width':self.line_width.value()}.items():
            self.config[key] = value
        self.commit()

    def choose_line_color(self):
        color = QColorDialog.getColor(QColor(self.config['line_color']),self,'分带线颜色')
        if color.isValid():
            self.config['line_color'] = color.name()
            self.commit()

    def is_running(self):
        return self.thread is not None and self.thread.isRunning()

    def cancel(self):
        if self.is_running():
            self.thread.requestInterruption()

    def start_analysis(self):
        if self.is_running():
            return
        if self.thread is not None:
            self.thread.deleteLater()
        self.project.cluster_config = copy.deepcopy(self.config)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText('正在计算；可取消。样品按坐标顺序合并，空白不补零。')
        self.thread = AnalysisWorker(self.project,self)
        self.thread.ready.connect(self.accept_result)
        self.thread.failed.connect(self.analysis_failed)
        self.thread.finished.connect(self.worker_finished)
        self.thread.start()

    def accept_result(self, result):
        if result['input_signature'] != analysis.input_signature(self.project):
            self.status.setText('计算期间数据或分析参数已改变，本次结果未应用，请重新计算。')
            return
        self.project.cluster_result = result
        self.config = analysis.config_for(self.project)
        self.config['n_zones'] = min(self.config['n_zones'],len(result['y']))
        # Manual edits are tied to the old analysis and must be explicitly reapplied.
        self.config['mode'] = 'algorithm'
        self.config['manual_boundaries'] = []
        self.project.cluster_config = copy.deepcopy(self.config)
        self.changed()
        self.loading = True
        self.mode.setCurrentIndex(0)
        self.n_zones.setMaximum(len(result['y']))
        self.n_zones.setValue(self.config['n_zones'])
        self.loading = False
        self.refresh_status()

    def analysis_failed(self, text):
        self.status.setText(text)

    def worker_finished(self):
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if analysis.result_is_current(self.project):
            self.refresh_status()

    def invalidate_status(self):
        if self.project is not None and not analysis.result_is_current(self.project):
            self.refresh_status()

    def refresh_status(self):
        if self.project is None:
            return
        current = analysis.result_is_current(self.project)
        self.export_button.setEnabled(current)
        if self.is_running():
            return
        if not current:
            self.status.setText('尚未计算或结果已失效，请计算 CONISS。修改颜色、线型和带数不需要重算。')
            self.boundaries.setRowCount(0)
            self.zones.setRowCount(0)
            return
        try:
            z = analysis.zoning(self.project)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        r = self.project.cluster_result
        self.status.setText(f'有效结果：{len(r["y"])} 样品、{len(r["taxa_ids"])} 类群、{len(z["zones"])} 带 · {z["source"]}；深部/较老端为Ⅰ带。'
                            + ('\n'+'；'.join(r['warnings']) if r['warnings'] else ''))
        self.loading = True
        self.n_zones.setEnabled(self.config['mode']=='algorithm')
        self.boundaries.setRowCount(len(z['boundaries']))
        import numpy as np
        for i,b in enumerate(z['boundaries']):
            j = min(max(int(np.searchsorted(r['y'],b,side='right')),1),len(r['y'])-1)
            self.boundaries.setItem(i,0,QTableWidgetItem(f'{b:.10g}'))
            self.boundaries.setItem(i,1,QTableWidgetItem(f'{r["y"][j-1]:g} — {r["y"][j]:g}'))
        self.zones.setRowCount(len(z['zones']))
        for i,zone in enumerate(z['zones']):
            for j,text in enumerate((zone['label'],zone['name'],f'{zone["lower"] if zone["lower"] is not None else "浅端"} — {zone["upper"] if zone["upper"] is not None else "深端"}')):
                item = QTableWidgetItem(str(text))
                if j != 1:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.zones.setItem(i,j,item)
        self.zones.resizeColumnsToContents()
        self.loading = False

    def mode_changed(self,index):
        if self.loading or self.project is None:
            return
        if not analysis.result_is_current(self.project):
            self.loading = True
            self.mode.setCurrentIndex(0 if self.config['mode']=='algorithm' else 1)
            self.loading = False
            QMessageBox.information(self,'请先计算','请先取得有效聚类结果，再切换分带模式。')
            return
        if index == 1:
            analysis.edit_boundaries(self.project,analysis.active_boundaries(self.project),'转为人工分带')
            self.config = analysis.config_for(self.project)
        else:
            self.config['mode'] = 'algorithm'
        self.commit()

    def restore_algorithm(self):
        if not analysis.result_is_current(self.project):
            return
        self.config['mode'] = 'algorithm'
        self.loading = True
        self.mode.setCurrentIndex(0)
        self.loading = False
        self.commit()

    def change_boundaries(self, values, action):
        analysis.edit_boundaries(self.project,values,action)
        self.config = analysis.config_for(self.project)
        self.loading = True
        self.mode.setCurrentIndex(1)
        self.loading = False
        self.commit()

    def boundary_value(self, default):
        text,ok = QInputDialog.getText(self,'人工边界','输入深度/年代数值（位于有效样品范围内部）',text=f'{default:.10g}')
        if not ok:
            return None
        try:
            v = float(text)
            if not math.isfinite(v):
                raise ValueError()
            return v
        except ValueError:
            QMessageBox.warning(self,'数值无效','请输入有限数字。')

    def add_boundary(self):
        if not analysis.result_is_current(self.project):
            return
        r = self.project.cluster_result
        value = self.boundary_value((r['y'][0]+r['y'][-1])/2)
        if value is not None:
            try:
                self.change_boundaries(analysis.active_boundaries(self.project)+[value],'新增边界')
            except ValueError as exc:
                QMessageBox.warning(self,'边界无效',str(exc))

    def move_boundary(self):
        index = self.boundaries.currentRow()
        if index < 0 or not analysis.result_is_current(self.project):
            return
        values = analysis.active_boundaries(self.project)
        value = self.boundary_value(values[index])
        if value is not None:
            values[index] = value
            try:
                self.change_boundaries(values,'移动边界')
            except ValueError as exc:
                QMessageBox.warning(self,'边界无效',str(exc))

    def delete_boundary(self):
        index = self.boundaries.currentRow()
        if index < 0 or not analysis.result_is_current(self.project):
            return
        values = analysis.active_boundaries(self.project)
        values.pop(index)
        self.change_boundaries(values,'删除边界')

    def zone_name_changed(self,item):
        if self.loading or item.column() != 1:
            return
        self.config['zone_names'][str(item.row()+1)] = item.text().strip()
        self.project.cluster_config = copy.deepcopy(self.config)
        # Do not rebuild the table during a text edit.
        self.changed()

    def export(self):
        if not analysis.result_is_current(self.project):
            return
        path,_ = QFileDialog.getSaveFileName(self,'导出聚类与分带结果','聚类与分带结果.xlsx','分析工作簿 (*.xlsx)')
        if path:
            try:
                analysis.export_analysis(self.project,path)
                self.status.setText('分析结果已导出：'+path)
            except Exception as exc:
                QMessageBox.critical(self,'导出失败',error_text(exc))

    def show_reference(self):
        if not analysis.result_is_current(self.project):
            QMessageBox.information(self,'请先计算','请先计算 CONISS。')
            return
        rows = analysis.broken_stick(self.project.cluster_result)
        if not rows:
            QMessageBox.information(self,'无带数参考','样品不足或组成无差异，无法提供有效的断棍模型比较。')
            return
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from .plotting import setup_fonts
        setup_fonts()
        dialog = QDialog(self)
        dialog.setWindowTitle('带数参考 · 断棍模型')
        dialog.resize(680,480)
        layout = QVBoxLayout(dialog)
        label = QLabel('仅作为带数参考，结合剖面解释确定；不自动宣称唯一正确带数。')
        label.setWordWrap(True)
        layout.addWidget(label)
        figure = Figure(figsize=(6,4),dpi=100)
        ax = figure.add_subplot(111)
        font = {'fontfamily':['Microsoft YaHei','DejaVu Sans']}
        ax.plot([r['groups'] for r in rows],[r['observed'] for r in rows],'o-',color='#32886e',label='观测离差变化')
        ax.plot([r['groups'] for r in rows],[r['expected'] for r in rows],'o--',color='#cf795d',label='断棍模型参考')
        ax.set_xlabel('组合带数',**font)
        ax.set_ylabel('离差变化',**font)
        ax.legend(prop={'family':['Microsoft YaHei','DejaVu Sans']})
        figure.tight_layout()
        layout.addWidget(FigureCanvasQTAgg(figure))
        dialog.exec()
