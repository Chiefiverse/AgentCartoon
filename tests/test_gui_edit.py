# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""Phase 2 编辑接线的回归：末端拖拽走 IK、状态栏说明拖法、质检结论进时间轴。

三条都是"只有真事件 + 真控件"才成立的行为：IK/FK 的分派发生在
``mousePressEvent`` 的命中判断里，色标发生在 ``paintEvent`` 里，都测不到就只能
肉眼在 GUI 里看，所以这里用 offscreen 平台把事件和绘制都跑一遍。

Qt 脚手架与坐标换算在 gui_harness.py。起不来 Qt 就跳过。
"""

from __future__ import annotations

import math

from agentcartoon.core import ik, quality

from gui_harness import (  # noqa: E402
    _midpoint_widget, _press_move_release, _rotated_widget, _tip_widget,
    _widget_of, _window, _bone_world_of, _set_frame)

WARN_RGB = (255, 169, 77)        # timeline_widget.WARN_MARK "#ffa94d"
ERROR_RGB = (255, 77, 77)        # timeline_widget.ERROR_MARK "#ff4d4d"


def _walk_window(frame: int = 6):
    """一个装着走路模板的窗口——四肢都在动，拖哪都有得看。"""
    from agentcartoon.core import actions, model

    window = _window()
    if window is None:
        return None
    rig = model.SkeletonRig()
    window.install(rig, actions.build("walk", rig=rig))
    _set_frame(window, frame)
    return window


def _color_present(widget, rgb) -> bool:
    """控件的绘制结果里是否出现过这个颜色。色标只由 ``_draw_issues`` 画，所以
    "出现/不出现"分别证明色标接通了、以及没问题时不瞎画。"""
    from PyQt6.QtGui import QImage

    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    target = rgb[0] << 16 | rgb[1] << 8 | rgb[2]
    return any(image.pixel(x, y) & 0xFFFFFF == target
               for y in range(0, image.height(), 2) for x in range(0, image.width(), 2))


# --------------------------------------------------------------------------- IK
def test_tip_hit_reports_ik_and_names_the_chain():
    window = _walk_window()
    if window is None:
        return
    seen = []
    window.canvas.drag_mode.connect(lambda mode, bone: seen.append((mode, bone)))
    _press_move_release(window, _tip_widget(window, "L_hand", 6), None)
    assert seen == [("IK", "L_hand")], seen
    assert window.canvas._ik_chain is None, "放开后链状态该清掉，免得下次 FK 拖拽接着用"


def test_dragging_hand_takes_the_whole_arm_to_the_target():
    """拖末端那个点：链要跟着动、手尖要真的落在鼠标处——逐骨 FK 两头都做不到。"""
    window = _walk_window()
    if window is None:
        return
    chain = ik.IK_CHAINS["L_hand"]
    before = {name: window.clip.angle_at(name, 6, 0.0) for name in chain}
    tip = _bone_world_of(window, "L_hand", "tip", 6)
    wanted = (tip[0] + 0.35, tip[1] + 0.30)
    _press_move_release(window, _tip_widget(window, "L_hand", 6), _widget_of(window, wanted))

    reached = _bone_world_of(window, "L_hand", "tip", 6)
    assert math.dist(reached, wanted) < 0.02, (reached, wanted)
    moved = [name for name in chain if abs(window.clip.angle_at(name, 6, 0.0) - before[name]) > 5.0]
    assert len(moved) >= 2, {name: abs(window.clip.angle_at(name, 6, 0.0) - before[name]) for name in chain}
    assert window.undo_stack.count() == 1, window.undo_stack.text(0)

    window.undo_stack.undo()
    back = {name: window.clip.angle_at(name, 6, 0.0) for name in chain}
    assert all(abs(back[name] - before[name]) < 1e-9 for name in chain), back


def test_ik_drag_respects_joint_limits():
    """拖到"手该反着弯"的位置也不能越出限位带：闭式解的两个弯曲分支靠限位挑，
    挑错支就是反关节。质检在这里当裁判——它认得的正是同一份 DEFAULT_LIMITS。"""
    window = _walk_window()
    if window is None:
        return
    tip = _bone_world_of(window, "L_hand", "tip", 6)
    for delta in ((-0.6, -0.9), (0.9, -0.4), (-1.2, 0.2), (0.2, 1.2)):
        _press_move_release(window, _tip_widget(window, "L_hand", 6),
                            _widget_of(window, (tip[0] + delta[0], tip[1] + delta[1])))
        over = [i for i in quality.check(window.clip, window.rig) if i.code == "joint_over_limit"]
        assert not over, (delta, over)


def test_ik_drag_leaves_other_limb_untouched():
    """解算只许动链内的骨：拖左手不该把右臂或腿牵连进去（角度串味是反解写回最常见的坑）。"""
    window = _walk_window()
    if window is None:
        return
    frame = 6
    chain = set(ik.IK_CHAINS["L_hand"])
    before = dict(window.clip.pose_at(window.rig, frame))
    tip = _bone_world_of(window, "L_hand", "tip", frame)
    target = _widget_of(window, (tip[0] + 0.30, tip[1] - 0.25))
    _press_move_release(window, _tip_widget(window, "L_hand", frame), target)
    after = window.clip.pose_at(window.rig, frame)
    stray = {name: (before[name], after[name]) for name in before
             if name not in chain and abs(before[name] - after[name]) > 1e-9}
    assert not stray, stray


def test_bone_body_still_drags_single_bone():
    """按在骨体中段仍是单骨 FK——两种拖法共处一个画布，别把老手段抢掉。"""
    window = _walk_window()
    if window is None:
        return
    seen = []
    window.canvas.drag_mode.connect(lambda mode, bone: seen.append(mode))
    point = _midpoint_widget(window, "L_forearm", 6)
    _press_move_release(window, point, _rotated_widget(window, "L_forearm", 6, -30.0))
    assert seen and seen[0] == "FK", seen


def test_far_target_snaps_the_chain_straight():
    """目标超出可达范围时整链拉直朝过去，不报错也不留半个姿态。"""
    window = _walk_window()
    if window is None:
        return
    chain = ik.IK_CHAINS["R_hand"]
    tip = _bone_world_of(window, "R_hand", "tip", 6)
    _press_move_release(window, _tip_widget(window, "R_hand", 6),
                        _widget_of(window, (tip[0] + 5.0, tip[1] + 5.0)))
    transforms = window.rig.world_transforms(window.clip.pose_at(window.rig, 6))
    reach = sum(window.rig.bones[name].length for name in chain)
    shoulder = transforms[chain[0]][0]
    hand_tip = transforms[chain[-1]][1]
    stretched = (hand_tip[0] - shoulder[0]) ** 2 + (hand_tip[1] - shoulder[1]) ** 2
    assert abs(stretched ** 0.5 - reach) < 1e-3, (stretched ** 0.5, reach)


# -------------------------------------------------------------------------- 质检
def test_generated_clip_marks_issues_on_the_timeline():
    window = _walk_window()
    if window is None:
        return
    expected = quality.check(window.clip, window.rig)
    assert expected and not any(i.level == "error" for i in expected)
    assert window.timeline.canvas.issues == expected, window.timeline.canvas.issues
    assert _color_present(window.timeline.canvas, WARN_RGB), "warn 色标没画出来"


def test_clean_clip_draws_no_marks():
    """反向对照：无问题的模板（idle）必须一个标都不画，否则上一条断言等于什么都没测。"""
    from agentcartoon.core import actions, model

    window = _window()
    if window is None:
        return
    rig = model.SkeletonRig()
    window.install(rig, actions.build("idle", rig=rig))
    assert window.timeline.canvas.issues == []
    assert not _color_present(window.timeline.canvas, WARN_RGB)
    assert not _color_present(window.timeline.canvas, ERROR_RGB)


def test_bad_pose_is_flagged_without_reloading_the_document():
    """改一格角度后色标立刻跟着变：质检挂在编辑路径上，不是只在生成时跑一次。"""
    window = _walk_window()
    if window is None:
        return
    before = len(window.timeline.canvas.issues)
    window._on_angle_edited("L_calf", 90.0)      # 反膝关节，joint_over_limit 必报
    codes = {i.code for i in window.timeline.canvas.issues}
    assert "joint_over_limit" in codes, codes
    assert len(window.timeline.canvas.issues) > before, (before, window.timeline.canvas.issues)


def test_extract_menu_action_reduces_then_undo_restores():
    """菜单里的"抽稀关键帧"必须走撤销栈：抽完不满意要能一键退回去。"""
    from PyQt6.QtWidgets import QInputDialog

    window = _walk_window()
    if window is None:
        return
    original = len(window.clip.sorted_keyframes())
    answer = QInputDialog.getDouble
    QInputDialog.getDouble = staticmethod(lambda *a, **k: (8.0, True))
    try:
        window.extract_keyframes()
    finally:
        QInputDialog.getDouble = answer
    kept = len(window.clip.sorted_keyframes())
    assert kept < original, (original, kept)
    assert window.clip.closure_error() == 0.0
    assert window.undo_stack.count() == 1, window.undo_stack.text(0)

    window.undo_stack.undo()
    assert len(window.clip.sorted_keyframes()) == original


def test_timeline_panel_forwards_to_canvas():
    """MainWindow 用的是 TimelinePanel，标尺画在 TimelineCanvas 上——中间得接通。"""
    window = _walk_window()
    if window is None:
        return
    issue = quality.Issue("error", "test", 3, None, "占位")
    window.timeline.set_issues([issue])
    assert window.timeline.canvas.issues == [issue]
