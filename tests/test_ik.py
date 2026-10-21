# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""core/ik.py 闭式解的离线用例。"""

from __future__ import annotations

import math

from agentcartoon.core import actions, ik
from agentcartoon.core.model import SkeletonRig

TOL = 1e-6


def _pose(template: str = "walk", frame: int = 6):
    rig = SkeletonRig()
    return rig, actions.build(rig=rig, key=template, key_step=1).pose_at(rig, frame)


def _tip(rig, chain, angles):
    return ik.chain_positions(rig, chain, angles)[-1]


# --------------------------------------------------------------------- 基本正确性
def test_self_target_is_identity():
    """目标就是当前末端时不该动：解必须复现当前姿态。"""
    rig, pose = _pose()
    for key, chain in ik.IK_CHAINS.items():
        tip = _tip(rig, chain, pose)
        solved = ik.solve(rig, chain, tip, pose)
        merged = {**pose, **solved}
        assert ik.tip_error(rig, chain, merged, tip) < 1e-9, (key, solved)
        moved = max(abs(solved[n] - pose[n]) for n in chain)
        assert moved < 1e-6, (key, moved, solved, pose)


def test_reachable_targets_hit_exactly_without_limits():
    """不限位时，可达半径内的目标必须精确命中（这是闭式解的核心承诺）。"""
    rig, pose = _pose("run", 3)
    for key, chain in ik.IK_CHAINS.items():
        pts = ik.chain_positions(rig, chain, pose)
        base, tip = pts[0], pts[-1]
        for dx, dy in ((0.3, 0.2), (-0.25, 0.35), (0.5, -0.5), (-0.4, -0.6), (0.0, 0.6)):
            tgt = (tip[0] + dx, tip[1] + dy)
            if ik._dist(base, tgt) > ik.reach(rig, chain):
                continue
            merged = {**pose, **ik.solve(rig, chain, tgt, pose, limits=None)}
            err = ik.tip_error(rig, chain, merged, tgt)
            assert err < 1e-9, (key, tgt, err)


def test_limits_are_never_violated():
    """带限位时返回的每个本地角都必须在限位带内（宁可够不到，也不能反关节）。"""
    rig, pose = _pose()
    for key, chain in ik.IK_CHAINS.items():
        tip = _tip(rig, chain, pose)
        for tgt in [(tip[0] + dx, tip[1] + dy) for dx, dy in
                    ((0.4, 0.4), (-0.7, 0.3), (0.1, -0.9), (1.2, 0.0), (-0.2, -0.2))]:
            solved = ik.solve(rig, chain, tgt, pose)
            for name in chain:
                limit = ik.DEFAULT_LIMITS.get(name)
                if limit is None:
                    continue
                lo, hi = ik._shift_band(limit, pose[name])
                assert lo - 1e-6 <= solved[name] <= hi + 1e-6, (key, name, solved[name], lo, hi)


def test_misses_are_caused_only_by_limits():
    """残差必须能归因于限位：同一无限位解要能精确命中，否则就是求解器自己的错。"""
    rig, pose = _pose()
    for key, chain in ik.IK_CHAINS.items():
        pts = ik.chain_positions(rig, chain, pose)
        base, tip = pts[0], pts[-1]
        for dx, dy in ((0.3, 0.2), (-0.25, 0.35), (0.15, -0.3), (0.05, 0.02), (-0.9, 0.9)):
            tgt = (tip[0] + dx, tip[1] + dy)
            if ik._dist(base, tgt) > ik.reach(rig, chain):
                continue
            err = ik.tip_error(rig, chain, {**pose, **ik.solve(rig, chain, tgt, pose)}, tgt)
            if err <= 1e-6:
                continue
            free = {**pose, **ik.solve(rig, chain, tgt, pose, limits=None)}
            assert ik.tip_error(rig, chain, free, tgt) < 1e-6, (key, tgt, err)


# ----------------------------------------------------------------------- 退化情形
def test_over_reach_straights_chain_toward_target():
    rig, pose = _pose("jump", 10)
    chain = ik.IK_CHAINS["L_hand"]
    pts = ik.chain_positions(rig, chain, pose)
    tgt = (pts[0][0] + 6.0, pts[0][1] + 6.0)
    merged = {**pose, **ik.solve(rig, chain, tgt, pose, limits=None)}
    after = ik.chain_positions(rig, chain, merged)
    for i, name in enumerate(chain):
        assert abs(ik._dist(after[i], after[i + 1]) - rig.bones[name].length) < 1e-9, name
    got = math.degrees(math.atan2(after[-1][1] - after[0][1], after[-1][0] - after[0][0]))
    want = math.degrees(math.atan2(tgt[1] - after[0][1], tgt[0] - after[0][0]))
    assert abs((got - want + 180.0) % 360.0 - 180.0) < 1e-6, (got, want)


def test_target_at_base_returns_current_pose():
    rig, pose = _pose()
    chain = ik.IK_CHAINS["R_foot"]
    base = ik.chain_positions(rig, chain, pose)[0]
    solved = ik.solve(rig, chain, base, pose)
    assert all(abs(solved[n] - pose[n]) < 1e-9 for n in chain), solved


# ------------------------------------------------------------------- 分支与角度约定
def test_knee_never_hyperextends():
    """整条腿扫一圈目标：膝（calf）本地角不得越过限位带上限，即不许反关节。"""
    rig, pose = _pose("walk", 0)
    chain = ik.IK_CHAINS["L_foot"]
    tip = _tip(rig, chain, pose)
    limit = ik.DEFAULT_LIMITS["L_calf"]
    for step in range(-12, 13):
        angle = math.radians(step * 15.0)
        tgt = (tip[0] + 1.2 * math.cos(angle), tip[1] + 1.2 * math.sin(angle))
        solved = ik.solve(rig, chain, tgt, pose)
        lo, hi = ik._shift_band(limit, pose["L_calf"])
        assert solved["L_calf"] <= hi + 1e-6, (tgt, solved["L_calf"], hi)
        assert solved["L_calf"] >= lo - 1e-6, (tgt, solved["L_calf"], lo)


def test_solution_never_jumps_a_full_turn():
    """结果必须落在当前值的同圈等价角上，否则 .sif 里会出现转一大圈的补间。"""
    rig, pose = _pose("wave", 12)
    for key, chain in ik.IK_CHAINS.items():
        tip = _tip(rig, chain, pose)
        solved = ik.solve(rig, chain, (tip[0] + 0.2, tip[1] + 0.2), pose)
        for name in chain:
            assert abs(solved[name] - pose[name]) <= 180.0 + 1e-6, (key, name, solved[name], pose[name])


# ----------------------------------------------------------------------- 链校验
def test_check_chain_rejects_bad_chains():
    rig, _ = _pose()
    for chain, why in (
        ([], "空链"),
        (["nope"], "未知骨"),
        (["head", "L_calf"], "不连续"),
        (["L_arm", "L_arm"], "重复"),
    ):
        try:
            ik.check_chain(rig, chain)
        except ik.IKError:
            continue
        raise AssertionError(f"应当拒绝：{chain}（{why}）")


def test_check_chain_accepts_all_template_chains():
    rig = SkeletonRig()
    for chain in ik.IK_CHAINS.values():
        ik.check_chain(rig, chain)


def test_solver_needs_full_pose_not_just_the_chain():
    """只传链上三根骨会让父骨退回 T-pose，链根位置与基准角都会算错——必须能暴露。"""
    rig, pose = _pose("run", 3)
    chain = ik.IK_CHAINS["L_hand"]
    tip = _tip(rig, chain, pose)
    partial = {n: pose[n] for n in chain}
    merged = {**pose, **ik.solve(rig, chain, (tip[0] + 0.3, tip[1] + 0.3), partial)}
    err = ik.tip_error(rig, chain, merged, (tip[0] + 0.3, tip[1] + 0.3))
    assert err > 1e-3, "偏弱的断言：这条用例存在的意义就是提醒必须传完整姿态"
