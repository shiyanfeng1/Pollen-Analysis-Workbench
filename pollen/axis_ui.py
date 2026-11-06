"""Manual shared vertical-axis settings, with the renderer's own validation."""
import copy
import math
from types import SimpleNamespace

import numpy as np
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel,
    QLineEdit, QCheckBox, QPushButton, QDialogButtonBox)

from .core import number
from .plotting import validate_config
from .render import fixed_y_scale


class YAxisDialog(QDialog):
    def __init__(self, project, config, parent=None, *, validator=None, description=None, coordinates=None):
        super().__init__(parent)
        self.setWindowTitle('纵轴坐标设置')
        self.setMinimumWidth(440)
        self.settings = copy.deepcopy(config)
        self.validator = validator or validate_config
        self.coordinates = []
        values = [s.axis for s in project.samples] if coordinates is None else coordinates
        for raw in values:
            try:
                value = number(raw)
                if value is not None and math.isfinite(float(value)):
                    self.coordinates.append(float(value))
            except (ValueError, OverflowError):
                continue
        root = QVBoxLayout(self)
        root.addWidget(QLabel(f'纵轴：{project.axis_kind}（{project.unit}）'))
        if self.coordinates:
            extent = f'{min(self.coordinates):g} ～ {max(self.coordinates):g}'
            root.addWidget(QLabel(f'当前样品坐标范围：{extent}'))
        else:
            root.addWidget(QLabel('尚无有效样品坐标，可先保存纵轴设置。'))
        form = QFormLayout()
        self.entries = {}
        for key,label in [('y_min','最小坐标值'), ('y_max','最大坐标值'), ('y_step','刻度间隔')]:
            value = config[key]
            entry = QLineEdit('' if value is None else f'{value:g}')
            entry.setObjectName(key)
            entry.setPlaceholderText('自动（留空）')
            entry.setToolTip('支持小数和科学计数法；留空使用自动设置。')
            self.entries[key] = entry
            form.addRow(label,entry)
        root.addLayout(form)
        self.reverse = QCheckBox('纵轴坐标值向下增加')
        self.reverse.setChecked(config['reverse_y'])
        root.addWidget(self.reverse)
        tip = QLabel(description or '全部孢粉子图、组合带和聚类树共用此纵轴。样品按真实深度／年代定位，刻度保持等间隔。留空的项目由软件自动设置。')
        tip.setWordWrap(True)
        root.addWidget(tip)
        self.automatic = QPushButton('恢复自动范围与刻度')
        self.automatic.clicked.connect(self.reset_automatic)
        root.addWidget(self.automatic)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color:#b3392f;')
        root.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText('确定')
        self.buttons.button(QDialogButtonBox.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

    def reset_automatic(self):
        for entry in self.entries.values():
            entry.clear()
        self.error.clear()

    def accept(self):
        config = copy.deepcopy(self.settings)
        labels = {'y_min':'最小坐标值','y_max':'最大坐标值','y_step':'刻度间隔'}
        try:
            for key,entry in self.entries.items():
                text = entry.text().strip()
                try:
                    value = number(text)
                    config[key] = None if value is None else float(value)
                except (ValueError, OverflowError):
                    raise ValueError(f'{labels[key]}必须是有限数字，或留空使用自动设置') from None
            config['reverse_y'] = self.reverse.isChecked()
            self.validator(config)
            if config['y_min'] is not None and config['y_max'] is not None and config['y_max'] <= config['y_min']:
                raise ValueError('最大坐标值必须大于最小坐标值')
            if self.coordinates:
                fixed_y_scale(SimpleNamespace(config=config,y=np.array(self.coordinates)))
            elif all(config[key] is not None for key in self.entries):
                if (config['y_max']-config['y_min'])/config['y_step'] > 150:
                    raise ValueError('纵轴刻度超过 150 个，请增大刻度间隔')
            self.settings = config
            super().accept()
        except (ValueError, OverflowError) as exc:
            self.error.setText(str(exc) if isinstance(exc,ValueError) else '坐标范围过大，请减小数值。')
