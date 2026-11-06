"""Chinese PCA workspace with independent selection and editable publication plots."""
import copy
import math
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QSplitter,QTreeWidget,QTreeWidgetItem,QScrollArea,QFormLayout,QGroupBox,
    QDoubleSpinBox,QSpinBox,QCheckBox,QComboBox,QLineEdit,QColorDialog,QFileDialog,
    QMessageBox,QDialog,QDialogButtonBox,QTableWidget,QTableWidgetItem,QHeaderView,
    QAbstractItemView,QTabWidget,QSlider,QSizePolicy)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import is_color_like

from .core import GROUPS, default_analysis_taxa
from . import pca
from .pca_render import build_figure
from .plotting import export_diagram,fingerprint
from .plot_ui import DiagramToolbar
from .localization import error_text


class PcaWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self,project,parent=None):
        super().__init__(parent)
        self.snapshot = copy.deepcopy(project)

    def run(self):
        try:
            self.ready.emit(pca.run_analysis(self.snapshot,cancelled=self.isInterruptionRequested))
        except Exception as exc:
            self.failed.emit(error_text(exc))
        finally:
            self.snapshot = None


class PcaPanel(QWidget):
    def __init__(self,changed,parent=None):
        super().__init__(parent)
        self.changed = changed
        self.project = None
        self.config = {}
        self.structure = None
        self.loading = False
        self.thread = None
        self.diagram = self.canvas = self.toolbar = self.dragging = None
        self.stale = True
        root = QVBoxLayout(self)
        root.setContentsMargins(0,8,0,0)
        row = QHBoxLayout()
        self.run_button = QPushButton('计算 PCA')
        self.run_button.clicked.connect(self.start_analysis)
        row.addWidget(self.run_button)
        self.cancel_button = QPushButton('取消计算')
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        row.addWidget(self.cancel_button)
        for label,slot in [('更新图形',self.draw),('恢复全图',self.home),('适应整幅',self.fit),
                           ('箭头与标签',self.arrow_dialog)]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        self.export_button = QPushButton('导出 PCA 图')
        self.export_button.clicked.connect(self.export_graph)
        self.export_button.setEnabled(False)
        row.addWidget(self.export_button)
        self.results_button = QPushButton('导出分析结果')
        self.results_button.clicked.connect(self.export_results)
        self.results_button.setEnabled(False)
        row.addWidget(self.results_button)
        row.addStretch()
        root.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet('padding:7px;background:#e8f1ed;color:#275848;')
        root.addWidget(self.status)
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split,1)
        left = QWidget()
        left.setMinimumWidth(170)
        left.setMaximumWidth(250)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0,0,7,0)
        ll.addWidget(QLabel('参与 PCA 的类群'))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemChanged.connect(self.selection_changed)
        ll.addWidget(self.tree,1)
        row = QHBoxLayout()
        for label,mode in [('全选','all'),('清空','none'),('按基数','base')]:
            button = QPushButton(label)
            if mode=='base':
                self.base_button = button
            button.clicked.connect(lambda checked=False,m=mode:self.select_taxa(m))
            row.addWidget(button)
        ll.addLayout(row)
        button = QPushButton('样品选择／自定义分组')
        button.clicked.connect(self.sample_dialog)
        ll.addWidget(button)
        tip = QLabel('沿用原百分比基数，筛选类群后不重新归一化。箭头显示选择独立于计算选择。')
        self.input_hint = tip
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
        self.zoom.setRange(20,200)
        self.zoom.setValue(100)
        self.zoom.valueChanged.connect(self.resize_preview)
        row.addWidget(self.zoom)
        self.zoom_label = QLabel('100%')
        row.addWidget(self.zoom_label)
        cl.addLayout(row)
        self.hover = QLabel('移动鼠标查看样品；关闭平移／缩放后，可直接拖动类群标签调整位置。')
        self.hover.setWordWrap(True)
        cl.addWidget(self.hover)
        self.tabs.addTab(preview,'图形预览')
        self.tables = {}
        for key,label in [('variance','解释率'),('scores','样品得分'),('loadings','类群载荷')]:
            table = QTableWidget()
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setAlternatingRowColors(True)
            table.horizontalHeader().setStretchLastSection(True)
            self.tables[key] = table
            self.tabs.addTab(table,label)
        split.addWidget(self.tabs)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setMinimumWidth(270)
        right_scroll.setMaximumWidth(320)
        right = QWidget()
        right_scroll.setWidget(right)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(7,0,0,0)
        box = QGroupBox('分析设置')
        form = QFormLayout(box)
        self.transform = QComboBox()
        self.transform.addItem('平方根变换','sqrt')
        self.transform.addItem('不变换（百分比转比例）','none')
        self.transform.currentIndexChanged.connect(lambda:self.set_option('transform',self.transform.currentData()))
        form.addRow('数据变换',self.transform)
        self.standardize = QCheckBox('按类群标准差标准化')
        self.standardize.setToolTip('先按类群中心化；勾选后再除以各类群样本标准差。')
        self.standardize.toggled.connect(lambda v:self.set_option('standardize',v))
        form.addRow(self.standardize)
        self.group_mode = QComboBox()
        for label,value in [('现有组合带','zones'),('自定义分组','manual'),('不分组','none')]:
            self.group_mode.addItem(label,value)
        self.group_mode.currentIndexChanged.connect(lambda:self.set_option('group_mode',self.group_mode.currentData()))
        form.addRow('样品分组',self.group_mode)
        button = QPushButton('设置样品组颜色')
        button.clicked.connect(self.group_color_dialog)
        form.addRow(button)
        rl.addWidget(box)
        box = QGroupBox('绘图设置')
        form = QFormLayout(box)
        self.layout_combo = QComboBox()
        for label,value in [('左右双图','both'),('仅类群箭头图','arrows'),('仅样品分布图','scores')]:
            self.layout_combo.addItem(label,value)
        self.layout_combo.currentIndexChanged.connect(lambda:self.set_option('layout',self.layout_combo.currentData()))
        form.addRow('布局',self.layout_combo)
        self.pc_x,self.pc_y = QComboBox(),QComboBox()
        for key,control,label in [('pc_x',self.pc_x,'横轴'),('pc_y',self.pc_y,'纵轴')]:
            control.currentIndexChanged.connect(lambda index,k=key,c=control:self.set_option(k,c.currentData()))
            form.addRow(label,control)
        self.checks = {}
        for key,label in [('flip_x','两图同步翻转横轴'),('flip_y','两图同步翻转纵轴'),
            ('italic','西文类群名称斜体'),('arrow_by_group','箭头按六类孢粉配色'),
            ('sample_labels','标注样品深度／年代'),('panel_letters','显示子图 a、b 编号')]:
            control = QCheckBox(label)
            control.toggled.connect(lambda v,k=key:self.set_option(k,v))
            form.addRow(control)
            self.checks[key] = control
        self.arrow_mode = QComboBox()
        for label,value in [('二维表达较好的前若干类群','top'),('全部分析类群','all'),('手动选择箭头','manual')]:
            self.arrow_mode.addItem(label,value)
        self.arrow_mode.currentIndexChanged.connect(lambda:self.set_option('arrow_mode',self.arrow_mode.currentData()))
        form.addRow('箭头展示',self.arrow_mode)
        self.top_count = QSpinBox()
        self.top_count.setRange(1,200)
        self.top_count.setKeyboardTracking(False)
        self.top_count.valueChanged.connect(lambda v:self.set_option('arrow_top',v))
        form.addRow('展示数量',self.top_count)
        self.title_edit = QLineEdit()
        self.title_edit.editingFinished.connect(lambda:self.set_option('title',self.title_edit.text()))
        form.addRow('标题',self.title_edit)
        self.controls = {}
        for key,label,low,high,step,decimals in [('width_cm','图宽（厘米）',10,40,.5,1),
            ('height_cm','图高（厘米）',6,30,.5,1),('font_size','名称字号（磅）',5,18,.5,1),
            ('tick_size','刻度字号（磅）',5,16,.5,1),('axis_size','轴标题字号（磅）',5,18,.5,1),
            ('line_width','箭头线宽（磅）',.2,3,.05,2),('spine_width','坐标线宽（磅）',.2,2,.05,2),
            ('point_size','样品点面积（磅²）',3,150,2,0),('point_edge','点轮廓线宽（磅）',0,2,.05,2)]:
            control = QDoubleSpinBox()
            control.setRange(low,high)
            control.setSingleStep(step)
            control.setDecimals(decimals)
            control.setKeyboardTracking(False)
            control.valueChanged.connect(lambda v,k=key:self.set_option(k,v))
            self.controls[key] = control
            form.addRow(label,control)
        self.font_combo = QComboBox()
        for label,value in [('西文无衬线体','Arial'),('西文衬线体','Times New Roman'),('微软雅黑','Microsoft YaHei'),('宋体','SimSun')]:
            self.font_combo.addItem(label,value)
        self.font_combo.currentIndexChanged.connect(lambda:self.set_option('font',self.font_combo.currentData()))
        form.addRow('字体',self.font_combo)
        for label,slot in [('坐标范围设置',self.axis_dialog),('设置箭头颜色',lambda:self.choose_color('arrow_color','类群箭头颜色')),
            ('设置背景颜色',lambda:self.choose_color('background','图形背景颜色')),('重置标签位置',self.reset_labels)]:
            button = QPushButton(label)
            button.clicked.connect(slot)
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
        split.setSizes([195,740,290])
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
        label = QLabel('选择分析类群与样品，点击“计算 PCA”生成双图。')
        label.setAlignment(Qt.AlignCenter)
        label.resize(500,320)
        self.scroll.setWidget(label)

    def bind(self,project):
        structure = (project.data_type,[(t.id,t.name,t.group) for t in project.taxa],[(s.id,s.axis) for s in project.samples])
        if project is self.project and structure==self.structure:
            self.invalidate_status()
            return
        self.project,self.structure = project,structure
        self.config = pca.config_for(project)
        self.base_button.setVisible(project.data_type=='counts')
        self.input_hint.setText('直接采用导入百分比，筛选类群后不重新归一化。箭头展示与计算选择独立。' if project.data_type=='percentages' else
            '沿用原百分比基数，筛选类群后不重新归一化。箭头显示选择独立于计算选择。')
        self.clear_preview()
        self.loading = True
        self.tree.clear()
        for group in GROUPS:
            parent = QTreeWidgetItem(self.tree,[group])
            parent.setFlags(parent.flags()|Qt.ItemIsAutoTristate|Qt.ItemIsUserCheckable)
            parent.setCheckState(0,Qt.Unchecked)
            for t in project.ordered_taxa():
                if t.group==group:
                    item = QTreeWidgetItem(parent,[t.name])
                    item.setData(0,Qt.UserRole,t.id)
                    item.setFlags(item.flags()|Qt.ItemIsUserCheckable)
                    item.setCheckState(0,Qt.Checked if t.id in self.config['taxa_ids'] else Qt.Unchecked)
            parent.setExpanded(True)
        self.loading = False
        self.sync_controls()
        self.invalidate_status()
        if pca.result_is_current(project):
            self.fill_results()

    def sync_controls(self):
        self.loading = True
        for key,control in [('transform',self.transform),('group_mode',self.group_mode),
            ('layout',self.layout_combo),('arrow_mode',self.arrow_mode),('font',self.font_combo)]:
            index = control.findData(self.config[key])
            if index<0 and key=='font':
                control.addItem('项目自定义字体',self.config[key])
                index = control.count()-1
            control.setCurrentIndex(max(0,index))
        self.standardize.setChecked(self.config['standardize'])
        for key,control in self.checks.items():
            control.setChecked(self.config[key])
        for key,control in self.controls.items():
            control.setValue(self.config[key])
        self.top_count.setValue(self.config['arrow_top'])
        self.title_edit.setText(self.config['title'])
        self.dpi.setCurrentText(str(self.config['dpi']))
        rank = self.project.pca_result['rank'] if pca.result_is_current(self.project) else 2
        for key,control in [('pc_x',self.pc_x),('pc_y',self.pc_y)]:
            control.clear()
            for i in range(rank):
                control.addItem(f'PC{i+1}',i)
            control.setCurrentIndex(min(self.config[key],rank-1))
        self.loading = False

    def set_option(self,key,value):
        if self.loading or self.project is None:
            return
        self.config[key] = value
        self.commit()

    def commit(self):
        self.project.pca_config = copy.deepcopy(self.config)
        self.changed()
        self.invalidate_status()

    def selection_changed(self,*args):
        if self.loading:
            return
        self.config['taxa_ids'] = [item.data(0,Qt.UserRole)
            for i in range(self.tree.topLevelItemCount()) for item in
            [self.tree.topLevelItem(i).child(j) for j in range(self.tree.topLevelItem(i).childCount())]
            if item.checkState(0)==Qt.Checked]
        self.commit()

    def select_taxa(self,mode):
        base = set(default_analysis_taxa(self.project))
        self.loading = True
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            for j in range(parent.childCount()):
                item = parent.child(j)
                yes = mode=='all' or (mode=='base' and item.data(0,Qt.UserRole) in base)
                item.setCheckState(0,Qt.Checked if yes else Qt.Unchecked)
        self.loading = False
        self.selection_changed()

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
        self.project.pca_config = copy.deepcopy(self.config)
        self.status.setText('正在计算 PCA；按类群中心化，样品分组仅用于绘图。')
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.thread = PcaWorker(self.project,self)
        self.thread.ready.connect(self.accept_result)
        self.thread.failed.connect(lambda message:self.status.setText(message))
        self.thread.finished.connect(self.worker_finished)
        self.thread.start()

    def accept_result(self,result):
        if result['input_signature']!=pca.input_signature(self.project):
            self.status.setText('计算期间输入或分析参数已改变，本次结果未应用，请重新计算。')
            return
        self.project.pca_result = result
        if self.config['pc_x']>=result['rank'] or self.config['pc_y']>=result['rank']:
            self.config['pc_x'],self.config['pc_y'] = 0,1
        self.project.pca_config = copy.deepcopy(self.config)
        self.changed()
        self.sync_controls()
        self.fill_results()
        self.draw()

    def worker_finished(self):
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if pca.result_is_current(self.project) and not self.stale:
            self.refresh_status()

    def invalidate_status(self):
        if self.project is None:
            return
        current = pca.result_is_current(self.project)
        self.results_button.setEnabled(current)
        if not self.diagram or self.diagram.signature!=fingerprint(self.project):
            self.stale = True
            self.export_button.setEnabled(False)
        if self.is_running():
            return
        if not current:
            self.status.setText('尚未计算或分析输入已改变，请计算 PCA。颜色、分组和箭头展示调整仅需更新图形。')
            for table in self.tables.values():
                table.setRowCount(0)
        elif self.stale:
            self.status.setText('PCA 数值结果有效；图形设置或分带已改变，请点击“更新图形”。')
        else:
            self.refresh_status()

    def refresh_status(self):
        if not self.diagram:
            return
        r = self.project.pca_result
        g = self.diagram.layout['groups']
        dropped = '；去除无变化类群：'+ '、'.join(t['name'] for t in r['dropped'][:6]) if r['dropped'] else ''
        self.status.setText(f'有效 PCA：{len(r["sample_ids"])} 样品、{len(r["taxa_ids"])} 类群 · 显示 {self.diagram.layout["arrow_count"]} 个箭头'
            f' · {len(g["labels"])} 个样品组'+dropped+ ('\n'+'；'.join(g['warnings']) if g['warnings'] else ''))

    def draw(self,checked=False,report_errors=True):
        try:
            if self.project.pca_config!=self.config:
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
            for event,slot in [('motion_notify_event',self.motion),('button_press_event',self.press),('button_release_event',self.release)]:
                self.canvas.mpl_connect(event,slot)
            self.toolbar = DiagramToolbar(self.canvas,self)
            self.navigation.addWidget(self.toolbar)
            self.scroll.setWidget(self.canvas)
            self.stale = False
            self.export_button.setEnabled(True)
            self.results_button.setEnabled(True)
            self.fill_results()
            self.refresh_status()
            self.fit()
            return True
        except Exception as exc:
            self.stale = True
            self.export_button.setEnabled(False)
            self.status.setText(error_text(exc))
            if report_errors:
                QMessageBox.warning(self,'PCA 图形尚未生成',error_text(exc))
            return False

    def fill_results(self):
        r = pca.require_result(self.project)
        try:
            g = pca.grouping(self.project)
            groups = [g['labels'][key] for key in g['keys']]
        except ValueError:
            groups = ['分组待更新']*len(r['sample_ids'])
        x,y = self.config['pc_x'],self.config['pc_y']
        if max(x,y)>=r['rank']:
            x,y = 0,1
        variance = [[f'PC{i+1}',f'{v:.8g}',f'{r["explained_ratio"][i]*100:.3f}',f'{sum(r["explained_ratio"][:i+1])*100:.3f}'] for i,v in enumerate(r['eigenvalues'])]
        scores = [[r['sample_rows'][i],r['y'][i],groups[i],f'{row[x]:.8g}',f'{row[y]:.8g}'] for i,row in enumerate(r['scores'])]
        loadings = [[name,f'{row[x]:.8g}',f'{row[y]:.8g}',f'{r["correlations"][i][x]:.8g}',f'{r["correlations"][i][y]:.8g}'] for i,(name,row) in enumerate(zip(r['taxa_names'],r['loadings']))]
        for key,headers,rows in [('variance',['主成分','特征值','解释率（%）','累计（%）'],variance),
            ('scores',['原表行','深度/年代','分组',f'PC{x+1}',f'PC{y+1}'],scores),
            ('loadings',['类群',f'PC{x+1}载荷',f'PC{y+1}载荷',f'PC{x+1}相关系数',f'PC{y+1}相关系数'],loadings)]:
            table = self.tables[key]
            table.setColumnCount(len(headers))
            table.setHorizontalHeaderLabels(headers)
            table.setRowCount(len(rows))
            for i,row in enumerate(rows):
                for j,value in enumerate(row):
                    table.setItem(i,j,QTableWidgetItem(str(value)))
            table.resizeColumnsToContents()

    def resize_preview(self,percent):
        self.zoom_label.setText(f'{percent}%')
        if not self.diagram or not self.canvas:
            return
        w,h = self.diagram.figure._pollen_size
        dpi = percent
        if w*h*dpi*dpi>24000000:
            self.zoom.setValue(max(20,int(math.sqrt(24000000/(w*h)))))
            return
        self.diagram.figure.set_dpi(dpi)
        self.canvas.setFixedSize(round(w*dpi),round(h*dpi))
        self.canvas.draw_idle()

    def fit(self):
        if not self.diagram:
            return
        w,h = self.diagram.figure._pollen_size
        value = int(min((self.scroll.viewport().width()-12)/(w*100),(self.scroll.viewport().height()-12)/(h*100))*100)
        value = max(20,min(200,value))
        self.zoom.setValue(value)
        self.resize_preview(value)

    def home(self):
        if self.diagram:
            for ax,(xlim,ylim) in zip(self.diagram.axes,self.diagram.figure._pollen_limits):
                ax.set_xlim(xlim)
                ax.set_ylim(ylim)
            self.canvas.draw_idle()

    def press(self,event):
        if self.stale or not self.diagram or event.button!=1 or self.toolbar.mode:
            return
        for text in self.diagram.labels:
            if text.contains(event)[0]:
                self.dragging = text
                break

    def motion(self,event):
        if not self.diagram or self.stale:
            return
        if self.dragging is not None and event.inaxes is self.dragging.axes and event.xdata is not None and event.ydata is not None:
            self.dragging.set_position((event.xdata,event.ydata))
            self.canvas.draw_idle()
            return
        if event.inaxes and getattr(event.inaxes,'_pca_kind','')=='scores' and event.xdata is not None:
            points = self.diagram.score_points
            index = int(np.sum((points-[event.xdata,event.ydata])**2,axis=1).argmin())
            r,g = self.project.pca_result,self.diagram.layout['groups']
            self.hover.setText(f'原表第 {r["sample_rows"][index]} 行 · {self.project.axis_kind} {r["y"][index]:g} {self.project.unit}'
                f' · {g["labels"][g["keys"][index]]} · 横坐标 {points[index,0]:.5g}，纵坐标 {points[index,1]:.5g}')

    def release(self,event):
        if self.dragging is None:
            return
        text,self.dragging = self.dragging,None
        offset = (np.array(text.get_position())-text._pca_tip)*np.array(self.diagram.layout['signs'])
        self.config['label_offsets'][text._pca_taxon_id] = [round(float(v),6) for v in offset]
        self.commit()
        self.draw(report_errors=False)

    def sample_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('PCA 样品选择与自定义分组')
        dialog.resize(640,540)
        layout = QVBoxLayout(dialog)
        label = QLabel('取消勾选可明确排除样品。自定义分组仅在右侧“样品分组”选择“自定义分组”时生效。')
        label.setWordWrap(True)
        layout.addWidget(label)
        table = QTableWidget(len(self.project.samples),3)
        table.setHorizontalHeaderLabels(['参与 PCA','深度/年代','自定义分组'])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for i,s in enumerate(self.project.samples):
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemIsEnabled|Qt.ItemIsSelectable|Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked if s.id in self.config['excluded_sample_ids'] else Qt.Checked)
            table.setItem(i,0,item)
            item = QTableWidgetItem(s.axis)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            table.setItem(i,1,item)
            table.setItem(i,2,QTableWidgetItem(self.config['manual_groups'].get(s.id,'')))
        layout.addWidget(table)
        row = QHBoxLayout()
        def select(all_samples):
            rows = range(table.rowCount()) if all_samples else {index.row() for index in table.selectedIndexes()}
            for i in rows:
                table.item(i,0).setCheckState(Qt.Checked if all_samples else Qt.Unchecked)
        for text,value in [('纳入全部样品',True),('排除选中样品',False)]:
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False,v=value:select(v))
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec()==QDialog.Accepted:
            self.config['excluded_sample_ids'] = [s.id for i,s in enumerate(self.project.samples) if table.item(i,0).checkState()!=Qt.Checked]
            self.config['manual_groups'] = {s.id:table.item(i,2).text().strip() for i,s in enumerate(self.project.samples) if table.item(i,2).text().strip()}
            self.commit()

    def arrow_dialog(self):
        if not pca.result_is_current(self.project):
            QMessageBox.information(self,'请先计算','请先取得有效的 PCA 结果。')
            return
        r = self.project.pca_result
        try:
            selected = set(pca.arrow_indices(self.project))
        except (ValueError,IndexError):
            selected = set()
        dialog = QDialog(self)
        dialog.setWindowTitle('显示类群箭头与标签位置')
        dialog.resize(720,550)
        layout = QVBoxLayout(dialog)
        label = QLabel('此处仅调整显示，不改变 PCA 计算。偏移留空时自动避让；也可直接在预览中拖动标签。偏移以未翻转的类群图坐标为单位。')
        label.setWordWrap(True)
        layout.addWidget(label)
        table = QTableWidget(len(r['taxa_ids']),4)
        table.setHorizontalHeaderLabels(['显示','类群名称','标签横向偏移','标签纵向偏移'])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for i,(tid,name) in enumerate(zip(r['taxa_ids'],r['taxa_names'])):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled|Qt.ItemIsUserCheckable|Qt.ItemIsSelectable)
            check.setCheckState(Qt.Checked if i in selected else Qt.Unchecked)
            table.setItem(i,0,check)
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            table.setItem(i,1,item)
            offsets = self.config['label_offsets'].get(tid,['',''])
            table.setItem(i,2,QTableWidgetItem(str(offsets[0])))
            table.setItem(i,3,QTableWidgetItem(str(offsets[1])))
        layout.addWidget(table)
        row = QHBoxLayout()
        for label,value in [('显示全部',Qt.Checked),('清空显示',Qt.Unchecked)]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False,v=value:[table.item(i,0).setCheckState(v) for i in range(table.rowCount())])
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        def accept():
            offsets = {}
            try:
                for i,tid in enumerate(r['taxa_ids']):
                    values = [table.item(i,j).text().strip() for j in (2,3)]
                    if any(values):
                        delta = [float(v or '0') for v in values]
                        if not all(math.isfinite(v) for v in delta):
                            raise ValueError()
                        offsets[tid] = delta
            except ValueError:
                QMessageBox.warning(dialog,'位置无效','标签偏移必须是有限数字，或留空自动设置。')
                return
            self.config.update(arrow_mode='manual',arrow_ids=[tid for i,tid in enumerate(r['taxa_ids']) if table.item(i,0).checkState()==Qt.Checked],label_offsets=offsets)
            self.commit()
            self.sync_controls()
            dialog.accept()
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def group_color_dialog(self):
        try:
            groups = pca.grouping(self.project)
        except ValueError as exc:
            QMessageBox.information(self,'分组待设置',str(exc))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('样品组颜色')
        layout = QVBoxLayout(dialog)
        table = QTableWidget(len(groups['labels']),2)
        table.setHorizontalHeaderLabels(['样品组','颜色代码'])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        keys = list(groups['labels'])
        for i,key in enumerate(keys):
            item = QTableWidgetItem(groups['labels'][key])
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            table.setItem(i,0,item)
            table.setItem(i,1,QTableWidgetItem(groups['colors'][key]))
        layout.addWidget(table)
        button = QPushButton('为选中组选择颜色')
        def choose():
            i = table.currentRow()
            if i>=0:
                color = QColorDialog.getColor(QColor(table.item(i,1).text()),dialog,'样品组颜色')
                if color.isValid():
                    table.item(i,1).setText(color.name())
        button.clicked.connect(choose)
        layout.addWidget(button)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        def accept():
            colors = {key:table.item(i,1).text().strip() for i,key in enumerate(keys)}
            if not all(is_color_like(v) for v in colors.values()):
                QMessageBox.warning(dialog,'颜色无效','请输入有效颜色代码，例如 #166B51。')
                return
            self.config['group_colors'].update(colors)
            self.commit()
            dialog.accept()
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def axis_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('PCA 两图坐标范围')
        form = QFormLayout(dialog)
        entries = {}
        for prefix,name in [('arrow','类群图'),('score','样品图')]:
            for axis,label in [('x','横轴'),('y','纵轴')]:
                for end,text in [('min','最小值'),('max','最大值')]:
                    key = f'{prefix}_{axis}_{end}'
                    value = self.config[key]
                    entry = QLineEdit('' if value is None else str(value))
                    entry.setPlaceholderText('自动（留空）')
                    entries[key] = entry
                    form.addRow(name+label+text,entry)
        reset = QPushButton('恢复自动范围')
        reset.clicked.connect(lambda:[e.clear() for e in entries.values()])
        form.addRow(reset)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        def accept():
            candidate = copy.deepcopy(self.config)
            try:
                for key,entry in entries.items():
                    text = entry.text().strip()
                    candidate[key] = None if not text else float(text)
                    if candidate[key] is not None and not math.isfinite(candidate[key]):
                        raise ValueError()
                for prefix in ('arrow','score'):
                    for axis in ('x','y'):
                        lo,hi = candidate[f'{prefix}_{axis}_min'],candidate[f'{prefix}_{axis}_max']
                        if lo is not None and hi is not None and hi<=lo:
                            raise ValueError()
            except ValueError:
                QMessageBox.warning(dialog,'坐标无效','请填写有限数字或留空，最大值必须大于最小值。')
                return
            self.config = candidate
            self.commit()
            dialog.accept()
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        dialog.exec()

    def choose_color(self,key,title):
        color = QColorDialog.getColor(QColor(self.config[key]),self,title)
        if color.isValid():
            self.set_option(key,color.name())

    def reset_labels(self):
        self.config['label_offsets'] = {}
        self.commit()

    def export_graph(self):
        if self.stale or not self.diagram or self.diagram.signature!=fingerprint(self.project):
            QMessageBox.information(self,'请更新图形','请先计算 PCA 或更新图形。')
            return
        path,selected = QFileDialog.getSaveFileName(self,'导出 PCA 图','孢粉PCA分析.pdf',
            '论文矢量文档 (*.pdf);;高清图片 (*.png);;可编辑矢量图 (*.svg)')
        if path:
            if not Path(path).suffix:
                path += '.pdf' if '*.pdf' in selected else '.svg' if '*.svg' in selected else '.png'
            try:
                export_diagram(self.project,self.diagram,path,int(self.dpi.currentText()))
                self.status.setText('PCA 图已导出：'+path)
            except Exception as exc:
                QMessageBox.critical(self,'导出失败',error_text(exc))

    def export_results(self):
        path,_ = QFileDialog.getSaveFileName(self,'导出 PCA 分析结果','PCA分析结果.xlsx','分析工作簿 (*.xlsx)')
        if path:
            try:
                pca.export_analysis(self.project,path)
                self.status.setText('PCA 分析结果已导出：'+path)
            except Exception as exc:
                QMessageBox.critical(self,'导出失败',error_text(exc))
