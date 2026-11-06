"""Explicit source type/units with a reviewable preview before any conversion."""
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QFormLayout,QComboBox,QLabel,
    QTableWidget,QTableWidgetItem,QHeaderView,QDialogButtonBox,QAbstractItemView)

from .core import count
from .excel import normalize_percentage


class DataTypeDialog(QDialog):
    def __init__(self,parent=None,*,data_type='counts',unit='percent',preview=None,allow_counts=True):
        super().__init__(parent)
        self.setWindowTitle('数据类型与数值单位')
        self.resize(680,470)
        self.preview = preview or []
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItem('原始计数','counts')
        self.type_combo.addItem('已有百分比（直接绘图与分析）','percentages')
        self.type_combo.model().item(0).setEnabled(allow_counts)
        self.type_combo.setCurrentIndex(self.type_combo.findData(data_type))
        form.addRow('输入数据类型',self.type_combo)
        self.unit_combo = QComboBox()
        self.unit_combo.addItem('百分数：12.5 表示 12.5%','percent')
        self.unit_combo.addItem('比例数：0.125 表示 12.5%','fraction')
        self.unit_combo.setCurrentIndex(self.unit_combo.findData(unit))
        form.addRow('普通数字的单位',self.unit_combo)
        root.addLayout(form)
        self.description = QLabel()
        self.description.setWordWrap(True)
        root.addWidget(self.description)
        self.table = QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels(['类群','文件／表格中的值','原数值格式','解释后数值'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        root.addWidget(self.table,1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        root.addWidget(self.note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText('按预览应用')
        self.buttons.button(QDialogButtonBox.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.type_combo.currentIndexChanged.connect(self.update_preview)
        self.unit_combo.currentIndexChanged.connect(self.update_preview)
        self.update_preview()

    @property
    def data_type(self):
        return self.type_combo.currentData()

    @property
    def percentage_unit(self):
        return self.unit_combo.currentData()

    def update_preview(self,*args):
        direct = self.data_type=='percentages'
        self.unit_combo.setEnabled(direct)
        self.description.setText('百分比数据统一存为百分数：12.5 表示 12.5%。不重新归一化，不要求计数基数。Excel 真正的百分比格式及带 % 的文本单独识别，不会重复乘以 100。' if direct else
            '原始计数要求非负整数，导入后选择参与基数的类群计算百分比。切换数据类型是在解释表格现有数值，不是将计数换算成百分比。')
        self.table.setRowCount(len(self.preview))
        over = False
        for i,(name,value,formatted) in enumerate(self.preview):
            try:
                normalized = normalize_percentage(value,self.percentage_unit,formatted) if direct else count(value)
                display = str(normalized) if normalized is not None and normalized!='' else '缺失'
                if direct and normalized!='':
                    over = over or float(normalized)>100
                    display += '%'
            except (ValueError,OverflowError):
                display = '无效，导入后需修正'
            for j,text in enumerate((name,str(value),'Excel百分比格式' if formatted else '普通数值／文本',display)):
                self.table.setItem(i,j,QTableWidgetItem(str(text)))
        self.note.setText('部分预览值超过100%，请核对单位及原始口径；原值不会自动归一化。' if over else
            '请确认预览符合原数据含义。导入后表格中的百分比均以“12.5=12.5%”编辑和显示，缺失与零分别保留。' if direct else
            '只有百分比时无法恢复原始粒数或实际基数。普通数字不会根据大小自动猜测百分数或比例数。')
