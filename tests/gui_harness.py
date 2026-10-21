# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""GUI 测试公共脚手架：offscreen QApplication、真实鼠标事件、世界↔视口坐标换算。

抽出来是因为两个用例卷（撤销回归 test_undo.py、编辑接线 test_gui_edit.py）都要
"用真事件驱动画布"，而合成事件算不到命中了哪根骨——必须按当前视图变换先换算坐标。
本文件不以 ``test_`` 开头，不会被 tests/_run.py 当成测试模块收进去。

起不来 Qt（没装 PyQt6 / 没有平台插件）时 ``_window()`` 返回 None，调用方直接跳过：
这些用例的价值在本机回归，不该反过来把整套测试拖垮。
"""

from __future__ import annotations

import math
import os
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_APP = None
_KEPT = []           # QApplication 与窗口必须由模块留着引用，被 GC 会连带删掉 C++ 对象


def _app():
    global _APP
    if _APP is None:
        from PyQt6.QtWidgets import QApplication

        _APP = QApplication.instance() or QApplication([])
    return _APP


def _window():
    """离屏 MainWindow；环境起不来 Qt 就返回 None（调用方直接跳过）。"""
    try:
        from agentcartoon.gui.main_window import MainWindow

        _app()
        window = MainWindow()
        window.resize(900, 700)
        window.show()
        _APP.processEvents()         # 走完一次布局，mapFromScene 的变换才是真的
        _KEPT.append(window)
        return window
    except Exception:                # 没有 PyQt6 / 起不了平台插件
        print("      跳过：Qt 环境不可用 —— " + traceback.format_exc().strip().splitlines()[-1])
        return None


def _press_move_release(window, widget_point, drag_to):
    """用真实的 Qt 事件走一遍拖骨，覆盖按下命中、移动改写、放开入栈整条路。

    ``drag_to=None`` 表示"按下没动"——Qt 在没有位移时不会发 MouseMove，这里也不发。
    """
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    canvas = window.canvas
    button = Qt.MouseButton.LeftButton

    def make(kind, position):
        local = QPointF(position)
        return QMouseEvent(kind, local, local, local, button, button,
                           Qt.KeyboardModifier.NoModifier)

    canvas.mousePressEvent(make(QEvent.Type.MouseButtonPress, widget_point))
    if drag_to is not None:
        canvas.mouseMoveEvent(make(QEvent.Type.MouseMove, drag_to))
    canvas.mouseReleaseEvent(make(QEvent.Type.MouseButtonRelease, drag_to or widget_point))


def _bone_world_of(window, bone: str, mode: str, frame: int):
    """某骨的锚点(origin)、末端(tip)或中点(mid)，角色世界坐标（已含根位移）。"""
    clip, rig = window.clip, window.rig
    offset = clip.root_offset_at(frame)
    origin, tip, _ = rig.world_transforms(clip.pose_at(rig, frame))[bone]
    if mode == "origin":
        wx, wy = origin
    elif mode == "tip":
        wx, wy = tip
    else:
        wx, wy = (origin[0] + tip[0]) / 2.0, (origin[1] + tip[1]) / 2.0
    return (wx + offset[0], wy + offset[1])


def _widget_of(window, world) -> "QPointF":
    from PyQt6.QtCore import QPointF

    return window.canvas.mapFromScene(QPointF(world[0], -world[1]))


def _set_frame(window, frame: int) -> None:
    """切帧要走时间轴（真正的驱动方），否则画布帧号和时间轴帧号会不一致：
    拖完入栈时 ``_refresh_all`` 会拿时间轴的帧号回灌画布，测试就悄悄跑到别的帧上了。
    """
    window.timeline.set_frame(frame)


def _midpoint_widget(window, bone: str, frame: int):
    """把某骨中点换算成视口坐标（世界 Y 向上，场景里取负）。"""
    return _widget_of(window, _bone_world_of(window, bone, "mid", frame))


def _tip_widget(window, bone: str, frame: int):
    return _widget_of(window, _bone_world_of(window, bone, "tip", frame))


def _rotated_widget(window, bone: str, frame: int, degrees: float):
    """把该骨中点绕骨根转 ``degrees`` 度后的视口坐标，作为拖拽目标。"""
    ox, oy = _bone_world_of(window, bone, "origin", frame)
    mx, my = _bone_world_of(window, bone, "mid", frame)
    radius, angle = math.hypot(mx - ox, my - oy), math.atan2(my - oy, mx - ox) + math.radians(degrees)
    return _widget_of(window, (ox + radius * math.cos(angle), oy + radius * math.sin(angle)))


def _world_of(window, bone: str, frame: int):
    return _bone_world_of(window, bone, "mid", frame)
