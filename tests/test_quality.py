# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""core/quality.py 的离线用例。

两类断言缺一不可：
* **不该误报**：五个内置模板必须 0 error、0 越限、0 穿模。限位带本身就是按模板
  极值标定的，模板一开始报警就说明阈值定坏了。
* **必须报得出**：每项检测都给一个人工构造的正控制（反关节、手压过头、脚跟着
  根骨平移、首尾接不上）。只测"不报错"的检测器等于没测——它会永远返回空表。
"""

from __future__ import annotations

from agentcartoon.core import actions, quality
from agentcartoon.core.model import AnimationClip, SkeletonRig

TEMPLATES = ("idle", "walk", "run", "wave", "jump")


def _clip(template: str = "walk", key_step: int = 1):
    rig = SkeletonRig()
    return actions.build(rig=rig, key=template, key_step=key_step), rig


def _override(clip, rig, frame: int, **changes):
    """在指定帧整帧覆写姿态（先取完整姿态再改，缺骨会掉回 T-pose）。"""
    pose = dict(clip.pose_at(rig, frame))
    pose.update(changes)
    clip.set_pose(rig, frame, pose)
    return clip


def _codes(issues):
    return {i.code for i in issues}


# ------------------------------------------------------------------ 模板不该误报
def test_templates_produce_no_errors():
    for key in TEMPLATES:
        clip, rig = _clip(key)
        issues = quality.check(clip, rig)
        errors = [i for i in issues if i.level == "error"]
        assert not errors, (key, errors)


def test_templates_never_exceed_joint_limits():
    """限位带是按模板极值标定的，模板自身越限就说明阈值定坏了。"""
    for key in TEMPLATES:
        clip, rig = _clip(key)
        over = quality.joint_over_limit(clip, rig)
        assert not over, (key, [str(i) for i in over])


def test_templates_have_no_penetration():
    """模板的左右肢体在几何上完全重合，所以只查跨部位配对；这条不该亮。"""
    for key in TEMPLATES:
        clip, rig = _clip(key)
        assert not quality.aabb_penetration(clip, rig), key


def test_loops_are_closed():
    for key in TEMPLATES:
        clip, rig = _clip(key)
        assert clip.closure_error() < 1e-6, (key, clip.closure_error())


# ------------------------------------------------------------------- 正控制：越限
def test_reverse_knee_is_reported():
    """把小腿掰成反关节（+90°，超出 [-160,10] 带）必须报出来。"""
    clip, rig = _clip("idle")
    issues = quality.joint_over_limit(_override(clip, rig, 6, L_calf=90.0), rig)
    assert issues, "反关节没被发现"
    assert all(i.code == "joint_over_limit" for i in issues), issues
    assert any(i.bone == "L_calf" for i in issues), issues
    assert any("90" in i.message for i in issues), issues


def test_over_limit_is_reported_on_every_frame_it_persists():
    """两帧都越限就报两条：合并只并连续帧，不并不同帧的独立问题。"""
    clip, rig = _clip("idle")
    _override(clip, rig, 6, L_calf=90.0)
    _override(clip, rig, 7, R_calf=90.0)
    issues = quality.joint_over_limit(clip, rig)
    assert {i.bone for i in issues} == {"L_calf", "R_calf"}, issues


# ------------------------------------------------------------------ 正控制：穿模
def test_hand_folded_over_head_penetrates():
    """大臂朝上、小臂折回头顶：手骨段压过头，必须报穿模。"""
    clip, rig = _clip("idle")
    issues = quality.aabb_penetration(_override(clip, rig, 4, L_arm=80.0, L_forearm=-140.0), rig)
    assert issues, "手压过头没被发现"
    assert any(i.code == "penetration" and "head" in (i.bone or "") for i in issues), issues
    assert not quality.joint_over_limit(clip, rig), issues  # 姿势合法，只有穿模


def test_inset_suppresses_bare_touch():
    """内缩是主要的压误报手段：刚碰边的重叠必须被 BOX_INSET 吃掉。"""
    clip, rig = _clip("idle")
    _override(clip, rig, 4, L_arm=80.0, L_forearm=-140.0)
    assert quality.aabb_penetration(clip, rig, inset=0.0), "内缩 0 时本就该相交"
    assert quality.aabb_penetration(clip, rig, inset=0.9) == []


# ------------------------------------------------------------------ 正控制：滑步
def test_foot_carried_by_root_slides():
    """脚在 rig 系里不动、根骨却往前走 = 纯粹的溜冰，必须报。"""
    clip, rig = _clip("idle")
    for frame in (0, 4, 8, 12):
        clip.set_pose(rig, frame, dict(clip.pose_at(rig, frame)),
                      positions={"root": (frame * 0.05, 0.0)})
    issues = quality.foot_slide(clip, rig)
    assert issues, "跟着根骨平移的脚没被判定滑步"
    assert all(i.code == "foot_slide" for i in issues), issues
    assert max(i.weight for i in issues) > 0.05


def test_standing_character_does_not_slide():
    """站桩（idle 全程脚不动）不该有滑步：这条以后也是锁脚的验收标准。"""
    clip, rig = _clip("idle")
    assert quality.foot_slide(clip, rig) == []


def test_slide_runs_collapse_into_one_issue():
    """一次滑步常连着十几帧，逐帧报会淹掉结论：连续同骨同代号必须并成一条。"""
    clip, rig = _clip("walk")
    issues = quality.foot_slide(clip, rig)
    assert len(issues) < clip.duration_frames / 2, [str(i) for i in issues]
    assert any(i.last_frame > i.frame for i in issues), issues


def test_loop_boundary_is_not_reported_as_slide():
    """循环末帧是首帧的副本、根位移在那里归零，两帧世界 x 差整个 stride。

    它是周期边界不是滑步，所以任何一条滑步的速度都不许达到一步的位移量，也不许
    落在末帧上。
    """
    clip, rig = _clip("walk")
    stride = clip.root_offset_at(clip.duration_frames - 1)[0]
    assert stride > 0.5, stride
    slides = [i for i in quality.foot_slide(clip, rig) if i.code == "foot_slide"]
    assert slides, "walk 确实滑步，一条都没有说明检测失效"
    assert max(i.weight for i in slides) < stride / 2, [str(i) for i in slides]
    assert max(i.last_frame for i in slides) <= clip.duration_frames - 1, slides


# ------------------------------------------------------------------ 正控制：闭合
def test_broken_loop_is_error():
    clip, rig = _clip("walk")
    _override(clip, rig, clip.duration_frames, L_forearm=60.0)
    issues = quality.loop_not_closed(clip)
    assert len(issues) == 1 and issues[0].level == "error", issues
    assert issues[0].code == "loop_not_closed" and issues[0].frame == -1, issues


def test_non_looping_clip_is_never_flagged_for_closure():
    clip, rig = _clip("jump")
    clip.loop = False
    assert quality.loop_not_closed(clip) == []


# ---------------------------------------------------------------- 正控制：根骨漂移
def _root_clip(points, *, loop=False, duration=12):
    """只有根骨位移、没有姿态的片段：专门喂给 root_drift。"""
    rig = SkeletonRig()
    clip = AnimationClip(name="root-track", fps=24, duration_frames=duration, loop=loop)
    for frame, (x, y) in points:
        clip.set_pose(rig, frame, {}, positions={"root": (x, y)})
    return clip, rig


def test_root_teleport_is_reported():
    """匀速推进里插一次单帧瞬移 = 插值打结，两侧的速度都得跳。"""
    clip, rig = _root_clip(
        [(0, (0.0, 0.0)), (2, (0.02, 0.0)), (4, (0.04, 0.0)), (6, (0.06, 0.0)),
         (7, (0.60, 0.0)), (8, (0.62, 0.0)), (10, (0.64, 0.0)), (12, (0.66, 0.0))]
    )
    issues = quality.root_drift(clip, rig)
    assert issues, "单帧瞬移没被发现"
    assert all(i.bone == "root" and i.code == "root_drift" for i in issues), issues
    # 二阶差分落在速度跳变的那一段两侧，合并后正好把瞬移那一帧圈进区间
    assert [(i.frame, i.last_frame) for i in issues] == [(6, 7)], issues


def test_parabola_and_steady_motion_are_not_drift():
    """抛物线的逐帧加速度恒定，二阶差分接近 0：真跳跃的腾空段不该被算成漂移。"""
    g = 0.01
    arc = [(f, (0.0, 0.4 * f - 0.5 * g * f * f)) for f in range(0, 13)]
    assert quality.root_drift(*_root_clip(arc)) == []
    straight = [(f, (0.03 * f, 0.0)) for f in range(0, 13)]
    assert quality.root_drift(*_root_clip(straight)) == []


def test_looping_root_sawtooth_is_not_drift():
    """循环动画的根位移在收尾帧归零是周期边界，不该报成漂移。"""
    clip, rig = _root_clip(
        [(f, (0.04 * f, 0.0)) for f in range(0, 12)] + [(12, (0.0, 0.0))], loop=True, duration=12
    )
    assert quality.root_drift(clip, rig) == []


# --------------------------------------------------------------------- 汇总与排序
def test_check_sorts_by_frame_then_code():
    clip, rig = _clip("walk")
    issues = quality.check(clip, rig)
    keys = [(i.frame, i.code, i.bone or "") for i in issues]
    assert keys == sorted(keys), keys


def test_summarize_counts_levels():
    issues = [
        quality.Issue("error", "loop_not_closed", -1, None, "x"),
        quality.Issue("warn", "foot_slide", 3, "L_foot", "x"),
        quality.Issue("warn", "root_drift", 5, "root", "x"),
    ]
    assert quality.summarize(issues) == "质检 3 项（error 1 / warn 2）"


def test_issue_str_shows_merged_range():
    issue = quality.Issue("warn", "foot_slide", 3, "L_foot", "在滑", last_frame=9)
    assert "第 3-9 帧" in str(issue) and "[L_foot]" in str(issue), str(issue)
    assert "整段" in str(quality.Issue("error", "loop_not_closed", -1, None, "跳"))
