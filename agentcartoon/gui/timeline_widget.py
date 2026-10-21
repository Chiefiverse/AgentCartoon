"""底部时间轴：帧标尺 + 每根骨骼一条轨道 + 关键帧标记（§6.2 的蓝菱形/红圆点）+ 播放控制。

自绘而非 item-view：帧数可达数千，绘制成本要可控。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPolygonF, QPainter, QPen
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

from ..core import model

ROW_HEIGHT = 18.0
RULER_HEIGHT = 24.0
DEFAULT_PX_PER_FRAME = 14.0
BG = QColor("#222226")
RULER_BG = QColor("#1b1b1f")
GRID = QColor("#33333a")
PLAYHEAD = QColor("#ffd166")
MANUAL = QColor("#ff6b6b")       # 用户手动 K 的帧：红圆点
TEMPLATE = QColor("#73b8ff")     # 模板/AI 生成的帧：蓝菱形
LABEL = QColor("#8a8f9e")


class TimelineCanvas(QWidget):
    """时间轴绘制区（放在 QScrollArea 里，横向按帧数展开）。"""

    frame_changed = pyqtSignal(int)
    bone_selected = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.rig: Optional[model.SkeletonRig] = None
        self.clip: Optional[model.AnimationClip] = None
        self.frame = 0
        self.px_per_frame = DEFAULT_PX_PER_FRAME
        self.selected_bone: Optional[str] = None
        self._scrubbing = False
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ------------------------------------------------------------------ 数据
    def set_document(self, rig: model.SkeletonRig, clip: model.AnimationClip) -> None:
        self.rig, self.clip = rig, clip
        self._sync_size()
        self.update()

    def set_frame(self, frame: int) -> None:
        if frame != self.frame:
            self.frame = frame
            self.update()

    def select_bone(self, name: Optional[str]) -> None:
        self.selected_bone = name
        self.update()

    def _rows(self):
        return self.rig.depth_first() if self.rig else []

    def _sync_size(self) -> None:
        frames = self.clip.duration_frames if self.clip else 1
        width = int(frames * self.px_per_frame) + 2
        height = int(RULER_HEIGHT + ROW_HEIGHT * max(1, len(self._rows()))) + 2
        self.setFixedWidth(max(width, 400))
        self.setMinimumHeight(max(height, 160))
        self.updateGeometry()

    # ------------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), BG)
        if self.clip is None or self.rig is None:
            return
        rows = self._rows()
        frames = self.clip.duration_frames
        pxf = self.px_per_frame

        painter.fillRect(QRectF(0, 0, self.width(), RULER_HEIGHT), RULER_BG)
        painter.setPen(QPen(GRID))
        for frame in range(frames + 1):
            x = frame * pxf
            if frame % 5 == 0:
                top = RULER_HEIGHT - (10 if frame % 10 == 0 else 5)
                painter.drawLine(int(x), int(top), int(x), int(RULER_HEIGHT))
            painter.drawLine(int(x), int(RULER_HEIGHT), int(x),
                             int(RULER_HEIGHT + ROW_HEIGHT * len(rows)))
        painter.setPen(LABEL)
        for frame in range(0, frames + 1, 10):
            painter.drawText(QRectF(frame * pxf + 3, 2, 10 * pxf, RULER_HEIGHT - 4),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             str(frame))

        keyframes = self.clip.sorted_keyframes()
        for index, name in enumerate(rows):
            y = RULER_HEIGHT + index * ROW_HEIGHT
            if name == self.selected_bone:
                painter.fillRect(QRectF(0, y, self.width(), ROW_HEIGHT), QColor(255, 255, 255, 14))
            painter.setPen(QPen(GRID))
            painter.drawLine(0, int(y + ROW_HEIGHT), self.width(), int(y + ROW_HEIGHT))
            for kf in keyframes:
                if name not in kf.bone_angles and not (name == self.rig.root_bone and kf.bone_positions):
                    continue
                self._draw_marker(painter, kf, name, y + ROW_HEIGHT / 2)

        px = self.frame * pxf
        painter.setPen(QPen(PLAYHEAD, 1.5))
        painter.drawLine(int(px), 0, int(px), int(self.height()))
        painter.setBrush(PLAYHEAD)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(_triangle(px, RULER_HEIGHT))

    def _draw_marker(self, painter: QPainter, kf: model.Keyframe, name: str, cy: float) -> None:
        color = MANUAL if kf.source == "manual" else TEMPLATE
        x = kf.frame * self.px_per_frame
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        if kf.source == "manual":
            painter.drawEllipse(QRectF(x - 3.5, cy - 3.5, 7, 7))
        else:
            painter.drawPolygon(_diamond(x, cy, 4.2))

    # ------------------------------------------------------------------ 交互
    def _frame_at(self, widget_x: float) -> int:
        frames = self.clip.duration_frames if self.clip else 0
        return max(0, min(frames, int(round(widget_x / self.px_per_frame))))

    def mousePressEvent(self, event) -> None:
        if self.clip and event.button() == Qt.MouseButton.LeftButton:
            self._scrubbing = True
            self.frame_changed.emit(self._frame_at(event.position().x()))
            rows = self._rows()
            index = int((event.position().y() - RULER_HEIGHT) // ROW_HEIGHT)
            if 0 <= index < len(rows):
                self.bone_selected.emit(rows[index])
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._scrubbing:
            self.frame_changed.emit(self._frame_at(event.position().x()))
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._scrubbing = False
        event.accept()

    def wheelEvent(self, event) -> None:
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.px_per_frame = max(2.0, min(60.0, self.px_per_frame * factor))
        self._sync_size()
        self.update()
        event.accept()


def _diamond(x: float, y: float, r: float) -> QPolygonF:
    return QPolygonF([QPointF(x, y - r), QPointF(x + r, y), QPointF(x, y + r), QPointF(x - r, y)])


def _triangle(x: float, bottom: float) -> QPolygonF:
    return QPolygonF([QPointF(x, bottom), QPointF(x - 5, 0), QPointF(x + 5, 0)])


class TimelinePanel(QWidget):
    """时间轴 + 播放控制条（§6.1 底部面板）。"""

    frame_changed = pyqtSignal(int)
    bone_selected = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.canvas = TimelineCanvas(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(False)
        scroll.setWidget(self.canvas)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.status = QLabel("—")
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(36)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("时间轴"))
        controls.addWidget(self.status)
        controls.addStretch(1)
        for label, slot in (("⏮", lambda: self.set_frame(0)),
                            ("⏭", lambda: self._to_end())):
            button = QPushButton(label)
            button.setFixedWidth(36)
            button.clicked.connect(slot)
            controls.addWidget(button)
        self.play_btn.clicked.connect(self.toggle_play)
        controls.addWidget(self.play_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(controls)
        layout.addWidget(scroll, 1)
        self.canvas.frame_changed.connect(self._on_canvas_frame)
        self.canvas.bone_selected.connect(self.bone_selected)

    # ------------------------------------------------------------------ 数据
    def set_document(self, rig: model.SkeletonRig, clip: model.AnimationClip) -> None:
        self.canvas.set_document(rig, clip)
        self.set_frame(min(self.canvas.frame, clip.duration_frames))

    def set_frame(self, frame: int) -> None:
        if self.canvas.clip is None:
            return
        frame = max(0, min(self.canvas.clip.duration_frames, frame))
        self.canvas.set_frame(frame)
        seconds = frame / float(self.canvas.clip.fps or 24)
        self.status.setText(f"帧 {frame}   {seconds:.2f}s   {self.canvas.clip.fps}fps")
        self.frame_changed.emit(frame)

    def select_bone(self, name: Optional[str]) -> None:
        self.canvas.select_bone(name)

    def refresh_markers(self) -> None:
        self.canvas.update()

    # ------------------------------------------------------------------ 播放
    def toggle_play(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self.play_btn.setText("▶")
        else:
            clip = self.canvas.clip
            if clip is None:
                return
            self._timer.start(max(10, int(1000 / (clip.fps or 24))))
            self.play_btn.setText("⏸")

    def _tick(self) -> None:
        clip = self.canvas.clip
        if clip is None:
            return
        self.set_frame(0 if self.canvas.frame >= clip.duration_frames else self.canvas.frame + 1)

    def _to_end(self) -> None:
        if self.canvas.clip:
            self.set_frame(self.canvas.clip.duration_frames)

    def _on_canvas_frame(self, frame: int) -> None:
        self.set_frame(frame)
