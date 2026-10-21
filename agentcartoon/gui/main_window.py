"""主窗口：按设计文档 §6.1 拼装左骨骼树 / 中画布 / 右动作面板 / 下时间轴。

含撤销栈（快照式）与后台 Synfig 渲染预览线程。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional, Tuple

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QPixmap, QUndoCommand, QUndoStack
from PyQt6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QLabel,
                             QMainWindow, QMessageBox, QSplitter)

from ..core import actions, model, render, sif
from .action_panel import ActionPanel
from .canvas_view import CanvasView
from .skeleton_tree import SkeletonPanel
from .timeline_widget import TimelinePanel


class SnapshotCommand(QUndoCommand):
    """快照式撤销：一次操作 = 一份 (rig, clip) 前后状态。"""

    def __init__(self, window: "MainWindow", label: str,
                 before: Tuple[model.SkeletonRig, model.AnimationClip],
                 after: Tuple[model.SkeletonRig, model.AnimationClip]) -> None:
        super().__init__(label)
        self.window = window
        self.before = (before[0].copy(), before[1].copy())
        self.after = (after[0].copy(), after[1].copy())

    def redo(self) -> None:
        self.window.install(self.after[0].copy(), self.after[1].copy())

    def undo(self) -> None:
        self.window.install(self.before[0].copy(), self.before[1].copy())


class RenderWorker(QThread):
    """后台渲染当前帧，避免阻塞 UI（§6.2 实时预览）。"""

    frame_ready = pyqtSignal(str, str)     # (png 路径, 错误信息)

    def __init__(self, document: sif.SifDocument, frame: int, parent=None) -> None:
        super().__init__(parent)
        self.document, self.frame = document, frame

    def run(self) -> None:
        try:
            directory = Path(tempfile.mkdtemp(prefix="agentcartoon_"))
            sif_path = self.document.save(directory / "preview.sif")
            png = render.Renderer().render_frame(sif_path, self.frame, directory / "preview")
            self.frame_ready.emit(str(png), "")
        except render.RenderUnavailable as exc:
            self.frame_ready.emit("", str(exc))
        except Exception as exc:                      # 渲染器崩溃不应带走 GUI
            self.frame_ready.emit("", f"{type(exc).__name__}: {exc}")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AgentCartoon — AI 辅助火柴人动画")
        self.resize(1280, 820)
        self.rig = model.SkeletonRig()
        self.clip = actions.build("walk", rig=self.rig)
        self.canvas_spec = sif.CanvasSpec()
        self.path: Optional[Path] = None
        self.worker: Optional[RenderWorker] = None
        self._syncing = False

        self.tree = SkeletonPanel()
        self.canvas = CanvasView()
        self.panel = ActionPanel()
        self.timeline = TimelinePanel()
        self.undo_stack = QUndoStack(self)

        center = QSplitter(Qt.Orientation.Horizontal, self)
        center.addWidget(self.tree)
        center.addWidget(self.canvas)
        center.addWidget(self.panel)
        center.setStretchFactor(0, 0)
        center.setStretchFactor(1, 1)
        center.setStretchFactor(2, 0)
        center.setSizes([220, 700, 300])

        root = QSplitter(Qt.Orientation.Vertical, self)
        root.addWidget(center)
        root.addWidget(self.timeline)
        root.setStretchFactor(0, 1)
        root.setStretchFactor(1, 0)
        root.setSizes([620, 200])
        self.setCentralWidget(root)

        self.preview = QLabel("未渲染")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dock = QDockWidget("Synfig 渲染预览", self)
        dock.setWidget(self.preview)
        dock.setMinimumWidth(240)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

        self._build_menus()
        self._wire()
        self.install(self.rig, self.clip)
        self.canvas.fit()

    # ------------------------------------------------------------------ 菜单
    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("文件")
        self._act(file_menu, "新建", "Ctrl+N", self.new_document)
        self._act(file_menu, "打开 .sif…", "Ctrl+O", self.open_document)
        self._act(file_menu, "保存", "Ctrl+S", self.save_document)
        self._act(file_menu, "另存为…", "Ctrl+Shift+S", self.save_document_as)
        file_menu.addSeparator()
        self._act(file_menu, "退出", "Ctrl+Q", self.close)

        edit_menu = bar.addMenu("编辑")
        undo = self.undo_stack.createUndoAction(self, "撤销")
        undo.setShortcut("Ctrl+Z")
        redo = self.undo_stack.createRedoAction(self, "重做")
        redo.setShortcut("Ctrl+Shift+Z")
        edit_menu.addAction(undo)
        edit_menu.addAction(redo)
        edit_menu.addSeparator()
        self._act(edit_menu, "清空手动关键帧", "", self.clear_manual_keyframes)

        render_menu = bar.addMenu("渲染")
        self._act(render_menu, "渲染当前帧预览", "F5", self.render_preview)
        self._act(render_menu, "渲染 PNG 序列…", "F6", self.render_sequence)

        help_menu = bar.addMenu("帮助")
        self._act(help_menu, "关于", "", self.about)

    def _act(self, menu, text: str, shortcut: str, slot) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    # ------------------------------------------------------------------ 联动
    def _wire(self) -> None:
        self.canvas.bone_selected.connect(self._on_bone_selected)
        self.canvas.pose_edited.connect(self._on_pose_edited)
        self.tree.tree.bone_selected.connect(self._on_bone_selected)
        self.tree.tree.angle_edited.connect(self._on_angle_edited)
        self.timeline.frame_changed.connect(self._on_frame_changed)
        self.timeline.bone_selected.connect(self._on_bone_selected)
        self.panel.generate_requested.connect(self._on_generate)
        self.panel.nl_requested.connect(self._on_nl)

    def _on_bone_selected(self, name: str) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            self.canvas.select_bone(name)
            self.tree.tree.select_bone(name)
            self.timeline.select_bone(name)
        finally:
            self._syncing = False

    def _on_pose_edited(self, frame: int, angles: dict) -> None:
        self.tree.tree.refresh(frame)
        self.timeline.refresh_markers()

    def _on_angle_edited(self, name: str, value: float) -> None:
        before = (self.rig.copy(), self.clip.copy())
        angles = self.clip.pose_at(self.rig, self.timeline.canvas.frame)
        angles[name] = value
        self.clip.set_pose(self.rig, self.timeline.canvas.frame, angles, source="manual")
        self.undo_stack.push(SnapshotCommand(self, f"改 {name} 角度", before,
                                             (self.rig, self.clip)))
        self.tree.tree.refresh(self.timeline.canvas.frame)
        self.canvas.viewport().update()
        self.timeline.refresh_markers()

    def _on_frame_changed(self, frame: int) -> None:
        self.canvas.set_frame(frame)
        self.tree.tree.refresh(frame)

    def _on_generate(self, key: str, params: dict, duration: float,
                     fps: int, key_step: int) -> None:
        before = (self.rig.copy(), self.clip.copy())
        rig = model.SkeletonRig()
        clip = actions.build(key, fps=fps, duration=duration, key_step=key_step,
                             raw_params=params, rig=rig)
        self.undo_stack.push(SnapshotCommand(self, f"生成 {key}", before, (rig, clip)))
        self.panel.say(f"已生成 {key}：{len(clip.sorted_keyframes())} 个关键帧，"
                       f"{clip.duration_frames} 帧，闭合误差 {clip.closure_error():.2f}°")

    def _on_nl(self, text: str) -> None:
        hits = [t.key for t in actions.TEMPLATES if t.key in text.lower() or t.label in text]
        if hits:
            self.panel.select_template(hits[0])
            self.panel.say(f"已选模板 {hits[0]}，点“生成关键帧”即可（自然语言生成在 Phase 2）")
        else:
            self.panel.say("没听出动作名。可用模板：" + "、".join(
                f"{t.label}({t.key})" for t in actions.TEMPLATES))

    def clear_manual_keyframes(self) -> None:
        before = (self.rig.copy(), self.clip.copy())
        self.clip.keyframes = [kf for kf in self.clip.keyframes if kf.source != "manual"]
        self.undo_stack.push(SnapshotCommand(self, "清空手动关键帧", before, (self.rig, self.clip)))
        self._refresh_all()

    # ------------------------------------------------------------------ 文档
    def install(self, rig: model.SkeletonRig, clip: model.AnimationClip) -> None:
        self.rig, self.clip = rig, clip
        self.canvas_spec.fps = clip.fps
        self._refresh_all()

    def _refresh_all(self) -> None:
        self.canvas.set_document(self.rig, self.clip)
        self.tree.tree.set_document(self.rig, self.clip)
        self.timeline.set_document(self.rig, self.clip)
        self._on_frame_changed(self.timeline.canvas.frame)

    def document(self) -> sif.SifDocument:
        return sif.SifDocument(rig=self.rig, clip=self.clip, canvas=self.canvas_spec)

    def new_document(self) -> None:
        before = (self.rig.copy(), self.clip.copy())
        rig = model.SkeletonRig()
        self.undo_stack.push(SnapshotCommand(self, "新建", before, (rig, actions.build("idle", rig=rig))))
        self.path = None
        self.setWindowTitle("AgentCartoon — AI 辅助火柴人动画")

    def open_document(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "打开 .sif", "", "Synfig 文件 (*.sif)")
        if not name:
            return
        try:
            doc = sif.SifDocument.load(name)
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", str(exc))
            return
        before = (self.rig.copy(), self.clip.copy())
        self.undo_stack.push(SnapshotCommand(self, "打开", before, (doc.rig, doc.clip)))
        self.path = Path(name)
        self.canvas_spec = doc.canvas
        self.setWindowTitle(f"AgentCartoon — {name}")

    def save_document_as(self) -> None:
        name, _ = QFileDialog.getSaveFileName(self, "保存 .sif", str(self.path or "out.sif"),
                                              "Synfig 文件 (*.sif)")
        if name:
            self.path = Path(name)
            self.save_document()

    def save_document(self) -> None:
        if self.path is None:
            self.save_document_as()
            return
        try:
            self.document().save(self.path)
            self.panel.say(f"已保存 {self.path}")
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    # ------------------------------------------------------------------ 渲染
    def about(self) -> None:
        from .. import __version__
        QMessageBox.about(
            self, "关于 AgentCartoon",
            f"AgentCartoon {__version__}\n\n"
            "AI 辅助的 Synfig 二维火柴人动画工具：模板/关键帧在这里编辑，导出标准 .sif 后可在 "
            "Synfig Studio 中继续 K 帧。\n\n"
            "已知局限：导出的是 transform-rig（FK 层组），不是 Synfig 原生骨骼变形层；"
            "要用皮肤权重需在 Synfig 里手动添加 skeleton_deformation。")

    def render_preview(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        self.preview.setText("渲染中…")
        self.worker = RenderWorker(self.document(), self.timeline.canvas.frame, self)
        self.worker.frame_ready.connect(self._on_preview)
        self.worker.start()

    def _on_preview(self, png: str, error: str) -> None:
        if not png:
            self.preview.setText(f"渲染失败：{error}")
            return
        pixmap = QPixmap(png)
        self.preview.setPixmap(pixmap.scaled(self.preview.size(),
                                             Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))
        self.preview.setToolTip(png)

    def render_sequence(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if not directory:
            return
        name, _ = QFileDialog.getSaveFileName(self, "先存 .sif", str(self.path or "out.sif"),
                                              "Synfig 文件 (*.sif)")
        if not name:
            return
        self.path = Path(name)
        sif_path = self.document().save(self.path)
        try:
            paths = render.Renderer().render_sequence(sif_path, Path(directory),
                                                      self.clip.duration_frames,
                                                      fps=self.clip.fps)
        except render.RenderUnavailable as exc:
            QMessageBox.warning(self, "渲染失败", str(exc))
            return
        self.panel.say(f"已渲染 {len(paths)} 帧到 {directory}")


def run() -> int:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    return app.exec()
