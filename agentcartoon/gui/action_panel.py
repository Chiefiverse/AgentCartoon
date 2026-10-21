# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""右侧面板：AI 对话占位 + 动作模板按钮 + 参数滑块（§6.1 右栏）。

滑块直接读 :mod:`agentcartoon.core.actions` 的 ParamDef 元数据，新增模板无需改这里。
"""

from __future__ import annotations

from typing import Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from ..core import actions


class _ParamControl(QWidget):
    """单个参数：滑块 + 数值框（枚举参数则为下拉框）。"""

    changed = pyqtSignal()

    def __init__(self, definition: actions.ParamDef, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.definition = definition
        self._syncing = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if definition.choices:
            self.combo: Optional[QComboBox] = QComboBox(self)
            self.slider: Optional[QSlider] = None
            self.spin: Optional[QDoubleSpinBox] = None
            best = None
            for label, value in definition.choices.items():
                self.combo.addItem(label, value)
                if value == definition.default:
                    best = self.combo.count() - 1
            self.combo.setCurrentIndex(0 if best is None else best)
            self.combo.currentIndexChanged.connect(self._emit)
            layout.addWidget(self.combo, 1)
            return

        low = int(round((definition.minimum - definition.default) / definition.step))
        high = int(round((definition.maximum - definition.default) / definition.step))
        self.combo = None
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(low, high)
        self.slider.setValue(0)
        self.spin = QDoubleSpinBox(self)
        self.spin.setRange(definition.minimum, definition.maximum)
        self.spin.setSingleStep(definition.step)
        self.spin.setDecimals(2 if definition.step < 0.1 else 1)
        self.spin.setSuffix(definition.unit)
        self.spin.setValue(definition.default)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spin)

    def _from_slider(self, steps: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.spin.setValue(self.definition.default + steps * self.definition.step)
        self._syncing = False
        self.changed.emit()

    def _from_spin(self, value: float) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.slider.setValue(int(round((value - self.definition.default) / self.definition.step)))
        self._syncing = False
        self.changed.emit()

    def _emit(self) -> None:
        self.changed.emit()

    def value(self):
        if self.combo is not None:
            return self.combo.currentText()
        return self.spin.value()


class ActionPanel(QWidget):
    """模板选择 + 参数编辑 + 生成按钮 + AI 对话占位。"""

    generate_requested = pyqtSignal(str, dict, float, int, int)   # key, params, duration, fps, key_step
    nl_requested = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(280)
        self._controls: Dict[str, _ParamControl] = {}
        self._buttons: Dict[str, QPushButton] = {}
        self.current_key = "walk"

        self.chat = QListWidget(self)
        self.chat.setMaximumHeight(150)
        self.chat_entry = QLineEdit(self)
        self.chat_entry.setPlaceholderText("描述你要的动作（Phase 2 接入模型）")
        self.chat_entry.returnPressed.connect(self._on_chat)

        templates = QGroupBox("动作模板", self)
        grid = QVBoxLayout(templates)
        row: Optional[QHBoxLayout] = None
        for index, template in enumerate(actions.TEMPLATES):
            if index % 2 == 0:
                row = QHBoxLayout()
                grid.addLayout(row)
            button = QPushButton(f"{template.label}\n{template.key}", templates)
            button.setCheckable(True)
            button.setToolTip(template.hint)
            button.clicked.connect(lambda _=False, key=template.key: self.select_template(key))
            self._buttons[template.key] = button
            row.addWidget(button)

        self.params_box = QGroupBox("参数", self)
        self.params_form = QFormLayout(self.params_box)
        self.duration = QDoubleSpinBox(self)
        self.duration.setRange(0.2, 20.0)
        self.duration.setSingleStep(0.2)
        self.duration.setValue(1.0)
        self.duration.setSuffix(" s")
        self.fps = QSpinBox(self)
        self.fps.setRange(1, 120)
        self.fps.setValue(24)
        self.key_step = QSpinBox(self)
        self.key_step.setRange(1, 12)
        self.key_step.setValue(2)
        self.generate = QPushButton("生成关键帧", self)
        self.generate.clicked.connect(self._on_generate)
        self.hint = QLabel("", self)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#8a8f9e")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("AI 对话"))
        layout.addWidget(self.chat)
        layout.addWidget(self.chat_entry)
        layout.addWidget(templates)
        layout.addWidget(self.params_box)
        layout.addWidget(self.hint)
        form = QFormLayout()
        form.addRow("时长", self.duration)
        form.addRow("帧率", self.fps)
        form.addRow("K 帧间隔", self.key_step)
        layout.addLayout(form)
        layout.addWidget(self.generate)
        layout.addStretch(1)

        self.select_template(self.current_key)

    # ------------------------------------------------------------------ 模板
    def select_template(self, key: str) -> None:
        template = actions.get_template(key)
        self.current_key = key
        for name, button in self._buttons.items():
            button.setChecked(name == key)
        self.hint.setText(template.hint)
        self.duration.setValue(template.default_duration)
        while self.params_form.rowCount():
            self.params_form.removeRow(0)
        self._controls = {}
        for definition in template.params:
            control = _ParamControl(definition, self.params_box)
            self.params_form.addRow(definition.label, control)
            self._controls[definition.name] = control

    def params(self) -> Dict[str, object]:
        return {name: control.value() for name, control in self._controls.items()}

    # ------------------------------------------------------------------ 动作
    def _on_generate(self) -> None:
        self.generate_requested.emit(self.current_key, self.params(),
                                     self.duration.value(), self.fps.value(),
                                     self.key_step.value())

    def _on_chat(self) -> None:
        text = self.chat_entry.text().strip()
        if not text:
            return
        self.chat_entry.clear()
        self.chat.addItem(f"你：{text}")
        self.nl_requested.emit(text)

    def say(self, text: str) -> None:
        self.chat.addItem(f"系统：{text}")
