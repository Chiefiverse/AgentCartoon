# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""拖骨编辑的两条回归（都是 Phase 1 留下的真 bug，只在 GUI 里露头）。

1. **拖完不能撤销**：画布直接改 ``clip``，而 ``_on_pose_edited`` 只刷新不
   入栈。后果比"退不回去"更糟——撤销栈里前一条命令的 after 快照和实际 clip 已经
   不一致，撤销会把刚拖出来的姿态一起吞掉。
2. **拖骨会抹掉该帧的根位移**：``set_pose(positions=None)`` 过去把
   ``bone_positions`` 一起清空，走路模板写进去的推进位移在每被拖过的帧上变成 (0,0)，
   角色凭空后退一步。

Qt 脚手架与坐标换算在 gui_harness.py（撤销/编辑两卷共用）。起不来 Qt 就跳过。
"""

from __future__ import annotations

from agentcartoon.core import actions, model

from gui_harness import (  # noqa: E402  (需在 Qt 引导之前，但不依赖 Qt)
    _midpoint_widget, _press_move_release, _rotated_widget, _set_frame, _window, _world_of)


# ------------------------------------------------------------------------- 模型层
def test_set_pose_keeps_positions_when_not_told_otherwise():
    """positions=None 是"不动位移"，显式给 {} 才是清空。"""
    rig = model.SkeletonRig()
    clip = actions.build(rig=rig, key="walk")
    keep = dict(clip.pose_at(rig, 6))
    keep["L_arm"] = 45.0
    clip.set_pose(rig, 6, keep, source="manual")
    assert clip.root_offset_at(6) == (0.25, -0.0625), clip.root_offset_at(6)
    clip.set_pose(rig, 6, keep, positions={}, source="manual")
    assert clip.root_offset_at(6) == (0.0, 0.0), clip.root_offset_at(6)


def test_dragging_does_not_punch_holes_in_locomotion():
    """拖过的帧仍连着走路推进：整条根位移曲线不许因为改了一次角度就塌掉。"""
    rig = model.SkeletonRig()
    clip = actions.build(rig=rig, key="walk")
    track = [clip.root_offset_at(f) for f in range(clip.duration_frames + 1)]
    for frame in (0, 6, 12):
        angles = clip.pose_at(rig, frame)
        angles["L_arm"] = 40.0
        clip.set_pose(rig, frame, angles, source="manual")
    after = [clip.root_offset_at(f) for f in range(clip.duration_frames + 1)]
    assert all(a == b for a, b in zip(track, after)), list(zip(track, after))[:8]


# -------------------------------------------------------------------------- GUI 层
def test_drag_pushes_exactly_one_undo_command():
    window = _window()
    if window is None:
        return
    frame = 6
    _set_frame(window, frame)
    before = window.clip.pose_at(window.rig, frame)
    root_before = window.clip.root_offset_at(frame)
    hit = window.canvas._hit_test(_world_of(window, "L_arm", frame))
    assert hit == "L_arm", hit

    _press_move_release(window, _midpoint_widget(window, "L_arm", frame),
                        _rotated_widget(window, "L_arm", frame, -35.0))

    assert window.undo_stack.count() == 1, window.undo_stack.text(0)
    assert abs(window.clip.angle_at("L_arm", frame, 0.0) - before["L_arm"]) > 5.0
    assert window.clip.root_offset_at(frame) == root_before, window.clip.root_offset_at(frame)

    window.undo_stack.undo()
    after = window.clip.pose_at(window.rig, frame)
    assert all(abs(after[name] - before[name]) < 1e-9 for name in before), after
    assert window.clip.root_offset_at(frame) == root_before


def test_click_without_move_leaves_undo_stack_empty():
    """纯点选（没拖动）不该在撤销栈里留一条空记录，否则 Ctrl+Z 会先"撤销"一次没发生的操作。"""
    window = _window()
    if window is None:
        return
    frame = 6
    _set_frame(window, frame)
    before = window.clip.pose_at(window.rig, frame)
    point = _midpoint_widget(window, "L_arm", frame)
    _press_move_release(window, point, None)
    assert window.undo_stack.count() == 0, window.undo_stack.text(0)
    after = window.clip.pose_at(window.rig, frame)
    assert all(abs(after[name] - before[name]) < 1e-9 for name in before)


def test_undo_after_drag_then_edit_restores_the_dragged_pose():
    """计划里点名的连锁后果：拖完再撤销上一条命令，不该把刚拖出来的姿态一起吞掉。"""
    window = _window()
    if window is None:
        return
    frame = 6
    _set_frame(window, frame)
    initial = window.clip.angle_at("L_arm", frame, 0.0)
    _press_move_release(window, _midpoint_widget(window, "L_arm", frame),
                        _rotated_widget(window, "L_arm", frame, -35.0))
    dragged = window.clip.angle_at("L_arm", frame, 0.0)
    assert abs(dragged - initial) > 5.0, (initial, dragged)

    window._on_angle_edited("R_arm", 100.0)          # 再改一次右臂（走 _on_angle_edited 的入栈路径）
    assert window.undo_stack.count() == 2
    window.undo_stack.undo()                          # 退掉右臂
    assert abs(window.clip.angle_at("L_arm", frame, 0.0) - dragged) < 1e-9, window.clip.angle_at("L_arm", frame, 0.0)
    window.undo_stack.undo()                          # 再退掉拖骨
    assert abs(window.clip.angle_at("L_arm", frame, 0.0) - initial) < 1e-9, window.clip.angle_at("L_arm", frame, 0.0)

