# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""画布视图：用 :meth:`SkeletonRig.world_transforms` 自绘火柴人，支持缩放/平移/洋葱皮/拖骨。

不依赖 Synfig 预览，改角度后立刻能看到结果；渲染预览由主窗口的后台线程负责。
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene

from ..core import model

# 世界坐标：Y 轴向上，单位为"骨长"，与 sif.CanvasSpec 的 view-box 同一量纲
SCENE_BOX = (-2.2, -3.5, 4.4, 7.0)          # 场景矩形（已把 Y 翻下来）
PX_PER_UNIT = 84.0                           # 默认 1 世界单位 ≈ 84 像素
LIMB_COLOR = QColor("#e8e8ef")
JOINT_COLOR = QColor("#73b8ff")
SELECT_COLOR = QColor("#ffd166")
ONION_COLOR = QColor("#9aa0b4")
BG_COLOR = QColor("#29292e")


def _dist_to_segment(point: Tuple[float, float], a, b) -> float:
    px, py = point
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class CanvasView(QGraphicsView):
    """火柴人骨骼画布。"""

    bone_selected = pyqtSignal(str)
    pose_edited = pyqtSignal(int, dict)      # (frame, 全量本地角度)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(*SCENE_BOX, self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(BG_COLOR)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setInteractive(True)

        self.rig: Optional[model.SkeletonRig] = None
        self.clip: Optional[model.AnimationClip] = None
        self.frame: int = 0
        self.onion_skin: bool = True
        self.selected_bone: Optional[str] = None
        self._dragging = False

    # ------------------------------------------------------------------ 数据
    def set_document(self, rig: model.SkeletonRig, clip: model.AnimationClip) -> None:
        self.rig, self.clip = rig, clip
        self.viewport().update()

    def set_frame(self, frame: int) -> None:
        if frame == self.frame:
            return
        self.frame = frame
        self.viewport().update()

    def set_onion_skin(self, enabled: bool) -> None:
        self.onion_skin = enabled
        self.viewport().update()

    def select_bone(self, name: Optional[str]) -> None:
        self.selected_bone = name
        self.viewport().update()

    # ------------------------------------------------------------------ 视图
    def fit(self) -> None:
        self.resetTransform()
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.scale(0.9, 0.9)

    def zoom(self, factor: float) -> None:
        self.scale(factor, factor)

    def wheelEvent(self, event) -> None:
        step = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.zoom(step)
        event.accept()

    # ------------------------------------------------------------------ 绘制
    def drawForeground(self, painter: QPainter, rect) -> None:
        super().drawForeground(painter, rect)
        if self.rig is None or self.clip is None:
            return
        painter.save()
        self._draw_axis(painter)
        if self.onion_skin:
            for offset, alpha in ((-2, 0.10), (-1, 0.22), (1, 0.22), (2, 0.10)):
                frame = self.frame + offset
                if 0 <= frame <= self.clip.duration_frames:
                    self._draw_figure(painter, frame, ONION_COLOR, alpha, joints=False)
        self._draw_figure(painter, self.frame, LIMB_COLOR, 1.0, joints=True)
        painter.restore()

    def _draw_axis(self, painter: QPainter) -> None:
        pen = QPen(QColor("#3c3c44"))
        pen.setWidthF(0.01)
        painter.setPen(pen)
        painter.drawLine(QPointF(-1.2, 0.0), QPointF(1.2, 0.0))

    def _draw_figure(self, painter: QPainter, frame: int, color: QColor,
                     opacity: float, joints: bool) -> None:
        rig, clip = self.rig, self.clip
        angles = clip.pose_at(rig, frame)
        offset = clip.root_offset_at(frame)
        transforms = rig.world_transforms(angles)
        painter.setOpacity(opacity)
        for name, (origin, tip, _) in transforms.items():
            bone = rig.bones[name]
            a = QPointF(origin[0] + offset[0], -(origin[1] + offset[1]))
            b = QPointF(tip[0] + offset[0], -(tip[1] + offset[1]))
            pen = QPen(SELECT_COLOR if joints and name == self.selected_bone else color)
            pen.setWidthF(bone.width if bone.length > 0 else 0.03)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            if bone.length > 0:
                painter.drawLine(a, b)
            if joints:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(JOINT_COLOR)
                painter.drawEllipse(a, 0.045, 0.045)

    # ------------------------------------------------------------------ 交互
    def _world_under(self, widget_pos) -> Tuple[float, float]:
        scene_pos = self.mapToScene(widget_pos)
        return scene_pos.x(), -scene_pos.y()

    def _hit_test(self, world: Tuple[float, float]) -> Optional[str]:
        if self.rig is None or self.clip is None:
            return None
        offset = self.clip.root_offset_at(self.frame)
        transforms = self.rig.world_transforms(self.clip.pose_at(self.rig, self.frame))
        best, best_dist = None, 0.16
        for name, (origin, tip, _) in transforms.items():
            if self.rig.bones[name].length <= 0:
                continue
            dist = _dist_to_segment(world,
                                    (origin[0] + offset[0], origin[1] + offset[1]),
                                    (tip[0] + offset[0], tip[1] + offset[1]))
            if dist < best_dist:
                best, best_dist = name, dist
        return best

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rig is not None:
            hit = self._hit_test(self._world_under(event.position().toPoint()))
            if hit:
                self.selected_bone = hit
                self.bone_selected.emit(hit)
                self._dragging = True
                self.setDragMode(QGraphicsView.DragMode.NoDrag)
                self.viewport().update()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and self.selected_bone:
            self._rotate_selected_to(self._world_under(event.position().toPoint()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self._dragging = False
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _rotate_selected_to(self, world: Tuple[float, float]) -> None:
        """把选中骨骼转向鼠标方向，写入当前帧关键帧（子骨世界角随之保持）。"""
        rig, clip = self.rig, self.clip
        name = self.selected_bone
        bone = rig.bones[name]
        offset = clip.root_offset_at(self.frame)
        transforms = rig.world_transforms(clip.pose_at(rig, self.frame))
        origin, _, _ = transforms[name]
        ox, oy = origin[0] + offset[0], origin[1] + offset[1]
        target_world = math.degrees(math.atan2(world[1] - oy, world[0] - ox))
        if target_world < 0:
            target_world += 360.0
        parent_world = transforms[bone.parent][2] if bone.parent else 0.0
        local = target_world - parent_world
        current = clip.angle_at(name, self.frame, bone.angle)
        # 保持与当前角度同圈，避免拖拽时出现 210° -> -150° 的突跳
        while local - current > 180.0:
            local -= 360.0
        while current - local > 180.0:
            local += 360.0
        angles = clip.pose_at(rig, self.frame)
        angles[name] = local
        clip.set_pose(rig, self.frame, angles, source="manual")
        self.pose_edited.emit(self.frame, angles)
        self.viewport().update()
