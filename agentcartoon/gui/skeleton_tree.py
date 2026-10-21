# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""左侧骨骼层级面板：显示骨骼树 + 当前帧本地角度，可直接改数值，与画布选中双向联动。"""

from __future__ import annotations

from typing import Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ..core import model

BONE_ROLE = Qt.ItemDataRole.UserRole
REST_COLOR = QColor("#6f7382")
EDITED_COLOR = QColor("#e8e8ef")


class SkeletonTree(QTreeWidget):
    """骨骼树。"""

    bone_selected = pyqtSignal(str)
    angle_edited = pyqtSignal(str, float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setColumnCount(2)
        self.setHeaderLabels(["骨骼", "角度 °"])
        self.setColumnWidth(0, 120)
        self.setUniformRowHeights(True)
        self.rig: Optional[model.SkeletonRig] = None
        self.clip: Optional[model.AnimationClip] = None
        self.frame = 0
        self._updating = False
        self.itemSelectionChanged.connect(self._on_selection_changed)
        self.itemChanged.connect(self._on_item_changed)

    # ------------------------------------------------------------------ 数据
    def set_document(self, rig: model.SkeletonRig, clip: model.AnimationClip) -> None:
        self.rig, self.clip = rig, clip
        self._rebuild()

    def _rebuild(self) -> None:
        self._updating = True
        self.clear()
        items: Dict[str, QTreeWidgetItem] = {}
        for name in self.rig.depth_first():
            bone = self.rig.bones[name]
            item = QTreeWidgetItem([name, ""])
            item.setData(0, BONE_ROLE, name)
            item.setToolTip(0, f"父骨：{bone.parent or '—'}    长度：{bone.length:g}")
            items[name] = item
            parent = items.get(bone.parent) if bone.parent else None
            if parent is None:
                self.addTopLevelItem(item)
            else:
                parent.addChild(item)
        self.expandAll()
        self._updating = False
        self.refresh(self.frame)

    def refresh(self, frame: int) -> None:
        """按给定帧刷新角度显示。"""
        if self.rig is None or self.clip is None:
            return
        self.frame = frame
        angles = self.clip.pose_at(self.rig, frame)
        self._updating = True
        for name, item in self._items().items():
            bone = self.rig.bones[name]
            value = angles.get(name, bone.angle)
            item.setText(1, f"{value:.1f}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            moved = abs(value - bone.angle) > 0.05
            item.setForeground(1, EDITED_COLOR if moved else REST_COLOR)
            if bone.length <= 0:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setText(1, "—")
        self._updating = False

    def _items(self) -> Dict[str, QTreeWidgetItem]:
        found: Dict[str, QTreeWidgetItem] = {}

        def walk(item: QTreeWidgetItem) -> None:
            name = item.data(0, BONE_ROLE)
            if name:
                found[name] = item
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.topLevelItemCount()):
            walk(self.topLevelItem(i))
        return found

    # ------------------------------------------------------------------ 联动
    def select_bone(self, name: Optional[str]) -> None:
        if not name or name not in self._items():
            return
        self._updating = True
        self.setCurrentItem(self._items()[name])
        self._updating = False
        self.bone_selected.emit(name)

    def _on_selection_changed(self) -> None:
        if self._updating:
            return
        items = self.selectedItems()
        if items:
            name = items[0].data(0, BONE_ROLE)
            if name:
                self.bone_selected.emit(name)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 1 or not self.clip:
            return
        name = item.data(0, BONE_ROLE)
        try:
            value = float(item.text(1))
        except ValueError:
            self.refresh(self.frame)
            return
        self.angle_edited.emit(name, value)


class SkeletonPanel(QWidget):
    """骨骼树 + 当前选中骨的父骨/长度信息条。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.tree = SkeletonTree(self)
        self.info = QLabel("未选中骨骼")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.info)
        self.tree.bone_selected.connect(self._show_info)

    def _show_info(self, name: str) -> None:
        rig = self.tree.rig
        if rig is None or name not in rig.bones:
            return
        bone = rig.bones[name]
        self.info.setText(f"{name}  ←  {bone.parent or 'root'}   长度 {bone.length:g}")
