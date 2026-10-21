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

from ..core import ik, model

# 世界坐标：Y 轴向上，单位为"骨长"，与 sif.CanvasSpec 的 view-box 同一量纲
SCENE_BOX = (-2.2, -3.5, 4.4, 7.0)          # 场景矩形（已把 Y 翻下来）
PX_PER_UNIT = 84.0                           # 默认 1 世界单位 ≈ 84 像素
TIP_GRAB_RADIUS = 0.13                       # 末端 tip 的命中半径（骨宽 0.07 的两倍）
LIMB_COLOR = QColor("#e8e8ef")
JOINT_COLOR = QColor("#73b8ff")
SELECT_COLOR = QColor("#ffd166")
IK_COLOR = QColor("#5ce1b0")                # IK 链末端：拖它 = 整链联动
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
    # 一次按下-拖拽-放开 = 一条撤销记录。逐帧的 pose_edited 不能直接当命令用：
    # 一次拖拽会发几十条，而且中间态进栈会把撤销变成"退一步再退一步"。
    drag_started = pyqtSignal()
    drag_finished = pyqtSignal(int)          # 帧号
    drag_mode = pyqtSignal(str, str)         # ("IK"|"FK", 骨骼名)：拖的是整链还是单骨

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
        self._ik_chain: Optional[Tuple[str, ...]] = None

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
                if name in ik.IK_CHAINS:
                    # IK 链末端的空心圈：告诉用户"拖这里 = 整条联动"，拖骨体仍是单骨旋转
                    pen = QPen(IK_COLOR)
                    pen.setWidthF(0.022)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(b, 0.072, 0.072)

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

    def _tip_at(self, world: Tuple[float, float]) -> Optional[str]:
        """鼠标是否落在某条 IK 链的末端尖（手/脚）上。先查 tip，再查骨体。

        末端是"点"，骨体是"线"：手指尖离前臂很近，只按线段距离会永远输给前臂，
        所以 tip 单独一轮、优先于 FK。半径比骨宽大一档，不然手指尖点不准。
        """
        if self.rig is None or self.clip is None:
            return None
        offset = self.clip.root_offset_at(self.frame)
        transforms = self.rig.world_transforms(self.clip.pose_at(self.rig, self.frame))
        best, best_dist = None, TIP_GRAB_RADIUS
        for name in ik.IK_CHAINS:
            bone = self.rig.bones.get(name)
            if bone is None or bone.length <= 0:
                continue
            tip = transforms[name][1]
            dist = math.hypot(tip[0] + offset[0] - world[0], tip[1] + offset[1] - world[1])
            if dist < best_dist:
                best, best_dist = name, dist
        return best

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rig is not None:
            world = self._world_under(event.position().toPoint())
            tip = self._tip_at(world)
            hit = tip or self._hit_test(world)
            if hit:
                self.selected_bone = hit
                self._ik_chain = ik.IK_CHAINS[tip] if tip else None
                self.bone_selected.emit(hit)
                self.drag_mode.emit("IK" if tip else "FK", hit)
                self._dragging = True
                self.setDragMode(QGraphicsView.DragMode.NoDrag)
                self.drag_started.emit()
                self.viewport().update()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and self.selected_bone:
            world = self._world_under(event.position().toPoint())
            if self._ik_chain:
                self._solve_ik_to(world)
            else:
                self._rotate_selected_to(world)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self._dragging = False
            self._ik_chain = None
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.drag_finished.emit(self.frame)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _solve_ik_to(self, world: Tuple[float, float]) -> None:
        """把链末端拖到鼠标处：根、中、末三骨一起动，末端骨只平移不改朝向。

        目标要减掉根位移换回 rig 世界系——鼠标在画布上的位置是"角色走到的地方"，
        而 IK 解算的是骨骼自身坐标系。
        """
        rig, clip, chain = self.rig, self.clip, self._ik_chain
        assert rig is not None and clip is not None and chain
        offset = clip.root_offset_at(self.frame)
        target = (world[0] - offset[0], world[1] - offset[1])
        angles = clip.pose_at(rig, self.frame)
        try:
            solved = ik.solve(rig, chain, target, angles)
        except ik.IKError:
            self._rotate_selected_to(world)   # 自定义骨骼凑不出合法链：退回单骨旋转
            return
        angles.update(solved)
        clip.set_pose(rig, self.frame, angles, source="manual")
        self.pose_edited.emit(self.frame, angles)
        self.viewport().update()

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
