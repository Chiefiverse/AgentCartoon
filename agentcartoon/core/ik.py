# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""反向运动学求解（设计文档第五章）。

用于"把手放到桌子上""脚踩在地面不滑动"这类末端定位：用户给一个目标点，
求解让末端骨指向该点的各骨**本地角度**。

**为什么不是设计文档里的 FABRIK**：先按 FABRIK 实现并跑通了几何部分，但我们的
链只有 3 根骨、末端还挂着限位，FABRIK 的"限位即投影"在这种短链上会失稳——实测
行走第 0 帧把左脚踩点往前挪 0.3 单位，逐轮误差从 0.21 单调涨到 0.34（发散）；
换 CCD（循环坐标下降）不再发散，但仍会卡在限位带边界上差 0.24~0.34 单位
（80px/unit 下约 20~27px，肉眼可见），因为"需要深蹲才能碰到"的目标在另一支上，
迭代法从伸直构型跨不过去。

所以这里用闭式解：四肢链 = 2 骨前缀（大臂/小臂、大腿/小腿）+ 1 根末端骨（手/脚）。
末端骨只跟着平移、保持原有世界方向（拖的是它尖上那个点，不是它的朝向），剩下的
"两骨把腕/踝送到指定点"用余弦定理直接解，两个弯曲分支按限位带 + 当前姿态取舍。
不迭代、没有局部极小、不会卡边界。代价是只支持 2~3 骨的链。

角度约定沿用 model.py：本地角、逆时针为正、Y 轴向上。与设计文档伪代码的三点差异：

1. 没有 ``bone.world_origin`` / ``world_tip`` 字段，关节位置一律经
   :meth:`model.SkeletonRig.world_transforms` 现算。
2. 关键帧存的是**本地角度**，不是世界角度，所以位置解出来后要反解
   ``local = world_i - world_parent``。
3. 角度有 ±360° 等价类。取与当前值差最小的那个，否则会出现 210°→-150°
   这种"数值跳圈、姿态不变"的关键帧（Synfig 里会转一大圈）。

链的自洽条件：链上除首骨外，必须 ``anchor == 1.0`` 且 ``origin == (0, 0)``，
这样 ``world_transforms`` 里它的起点才正好等于父骨末端，相邻关节点距离才恒等于
骨长。标准 15 骨模板的四条四肢链全满足。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .model import SkeletonRig

Point = Tuple[float, float]

EPS = 1e-9


class IKError(ValueError):
    """骨骼链本身不成立：骨名不存在、不是连续的父子链、或锚点约定不满足。"""


# --------------------------------------------------------------------------- 限位
@dataclass(frozen=True)
class JointLimit:
    """单骨本地角度的允许区间（度）。区间按"与静止姿态同圈"解释。"""

    bone: str
    minimum: float
    maximum: float


# 数值来源：先按解剖学定方向（膝/肘是单向铰链），再用 5 个内置模板在参数极值下
# 的逐骨实测包络向外取整，保证模板自身永不触发超限（见 tests/test_quality.py）。
# 左右符号相反是因为 T-pose 下 L_arm=90 / R_arm=-90，同向弯曲对应异号的本地角。
#
# 肩（L_arm/R_arm）与髋（L_leg/R_leg）**不设限**：它们是球关节，可动范围取决于
# 身体朝向，而 2D 侧视模型里没有这个信息。设死区间只会挡住 IK 的正常解（实测
# 表现为末端够不到目标）并在质检里制造误报；四肢互相穿过去由 AABB 穿模检测负责。
DEFAULT_LIMITS: Dict[str, JointLimit] = {
    name: JointLimit(name, lo, hi)
    for name, lo, hi in [
        ("spine", -30.0, 20.0),
        ("head", -25.0, 30.0),
        ("L_forearm", -150.0, 70.0),
        ("L_hand", -80.0, 80.0),
        ("R_forearm", -70.0, 150.0),
        ("R_hand", -80.0, 80.0),
        ("L_calf", -160.0, 10.0),
        ("L_foot", 15.0, 245.0),
        ("R_calf", -160.0, 10.0),
        ("R_foot", 15.0, 245.0),
    ]
}

# 末端骨 -> 骨骼链（根骨在前，末端骨在最后）。root/spine 不参与：它们是 FK 的事。
IK_CHAINS: Dict[str, List[str]] = {
    "L_hand": ["L_arm", "L_forearm", "L_hand"],
    "R_hand": ["R_arm", "R_forearm", "R_hand"],
    "L_foot": ["L_leg", "L_calf", "L_foot"],
    "R_foot": ["R_leg", "R_calf", "R_foot"],
}


# --------------------------------------------------------------------------- 工具
def _shift_near(value: float, reference: float) -> float:
    """把 value 平移 ±360° 的整数倍，使其与 reference 的差不超过 180°。"""
    while value - reference > 180.0:
        value -= 360.0
    while reference - value > 180.0:
        value += 360.0
    return value


def _shift_band(limit: JointLimit, reference: float) -> Tuple[float, float]:
    """把限位区间平移到与 reference 同一圈（角度有 ±360° 等价类）。"""
    center = (limit.minimum + limit.maximum) / 2.0
    shift = _shift_near(center, reference) - center
    return limit.minimum + shift, limit.maximum + shift


def _clamp_with_limit(value: float, limit: JointLimit, reference: float) -> float:
    lo, hi = _shift_band(limit, reference)
    return min(hi, max(lo, value))


def _dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


# --------------------------------------------------------------------------- 链
def check_chain(rig: SkeletonRig, chain: Sequence[str]) -> None:
    """校验骨骼链可用；不成立时抛 :class:`IKError`。"""
    if not chain:
        raise IKError("骨骼链为空")
    for name in chain:
        if name not in rig.bones:
            raise IKError(f"骨骼链含未知骨骼：{name}")
        if rig.bones[name].length <= 0.0:
            raise IKError(f"骨骼链含零长骨：{name}")
    if len(set(chain)) != len(chain):
        raise IKError(f"骨骼链有重复骨骼：{chain}")
    for child, parent in zip(chain[1:], chain[:-1]):
        if rig.bones[child].parent != parent:
            raise IKError(f"骨骼链不连续：{child} 的父骨是 {rig.bones[child].parent}，不是 {parent}")
    for name in chain[1:]:
        bone = rig.bones[name]
        if abs(bone.anchor - 1.0) > 1e-6 or max(abs(x) for x in bone.origin) > 1e-6:
            raise IKError(
                f"{name} 的 anchor={bone.anchor} origin={bone.origin}，"
                "起点不落在父骨末端，相邻关节点距离不等于骨长"
            )


def parent_world_angle(rig: SkeletonRig, chain: Sequence[str], angles: Mapping[str, float]) -> float:
    """链首骨父骨骼的世界方向角；链首即根骨骼时返回其本地角（根无父）。"""
    parent = rig.bones[chain[0]].parent
    if parent is None:
        return float(angles.get(chain[0], rig.bones[chain[0]].angle))
    return rig.world_transforms(dict(angles))[parent][2]


def chain_positions(rig: SkeletonRig, chain: Sequence[str], angles: Mapping[str, float]) -> List[Point]:
    """链的 n+1 个关节点：首骨起点 + 各骨末端。"""
    tf = rig.world_transforms(dict(angles))
    return [tf[chain[0]][0]] + [tf[name][1] for name in chain]


def _clamp_locals(
    locals_: Mapping[str, float],
    current_angles: Mapping[str, float],
    limits: Optional[Mapping[str, JointLimit]],
) -> Dict[str, float]:
    if not limits:
        return dict(locals_)
    out: Dict[str, float] = {}
    for name, value in locals_.items():
        limit = limits.get(name)
        reference = float(current_angles.get(name, value))
        out[name] = _clamp_with_limit(value, limit, reference) if limit else value
    return out


def _limit_violation(
    value: float,
    limit: Optional[JointLimit],
    reference: float,
) -> float:
    """越出限位带多少度（在带内为 0）。用于在两个弯曲分支之间挑合法的那个。"""
    if limit is None:
        return 0.0
    lo, hi = _shift_band(limit, reference)
    return max(0.0, lo - value, value - hi)


# --------------------------------------------------------------------------- 求解
def _prefix_annulus(rig: SkeletonRig, prefix: Sequence[str]) -> Tuple[float, float]:
    """前缀链末端（腕/踝）可达的半径区间。单骨前缀退化成圆。"""
    if len(prefix) == 1:
        length = rig.bones[prefix[0]].length
        return length, length
    l0, l1 = (rig.bones[name].length for name in prefix[:2])
    return abs(l0 - l1), l0 + l1


def reach(rig: SkeletonRig, chain: Sequence[str]) -> float:
    """链的最大可达半径（骨长之和）。"""
    return sum(rig.bones[name].length for name in chain)


def _two_bone(
    rig: SkeletonRig,
    prefix: Sequence[str],
    base: Point,
    target: Point,
    parent_world: float,
    current: Mapping[str, float],
    limits: Optional[Mapping[str, JointLimit]],
) -> Dict[str, float]:
    """两骨链把末端送到 target 的闭式解，在两个弯曲分支里挑合法的。

    余弦定理解析求，不迭代。分支取舍：先比限位越量（反关节那一支必然越界），
    再比与当前姿态的接近程度——拖拽过程中后者保证姿态连续、不在两支之间跳。
    """
    l0 = rig.bones[prefix[0]].length
    l1 = rig.bones[prefix[1]].length
    dx, dy = target[0] - base[0], target[1] - base[1]
    dist = math.hypot(dx, dy)
    if dist <= EPS:
        return {name: float(current[name]) for name in prefix}
    to_target = math.degrees(math.atan2(dy, dx))
    # 目标落在可达环（|l0-l1| .. l0+l1）之外时贴到环上：够不着就尽量伸/收。
    # 注意不能留 1e-9 的内缩余量：cos_alpha 贴近 ±1 时 acos 的导数发散，
    # 1e-9 的半径误差会被放大成 0.002° 的角度误差（直臂/直腿正好落在这点上）。
    d = min(max(dist, abs(l0 - l1)), l0 + l1)
    cos_alpha = (l0 * l0 + d * d - l1 * l1) / (2.0 * l0 * d)
    alpha = math.degrees(math.acos(max(-1.0, min(1.0, cos_alpha))))

    best: Optional[Tuple[Tuple[float, float], Dict[str, float]]] = None
    for sign in (1.0, -1.0):
        w0 = to_target + sign * alpha
        rad = math.radians(w0)
        joint = (base[0] + l0 * math.cos(rad), base[1] + l0 * math.sin(rad))
        w1 = math.degrees(math.atan2(target[1] - joint[1], target[0] - joint[0]))
        cand = {
            prefix[0]: _shift_near(w0 - parent_world, current[prefix[0]]),
            prefix[1]: _shift_near(w1 - w0, current[prefix[1]]),
        }
        cost = sum(
            _limit_violation(cand[name], (limits or {}).get(name), float(current[name]))
            for name in prefix
        )
        continuity = sum(abs(cand[name] - float(current[name])) for name in prefix)
        key = (round(cost, 9), continuity)
        if best is None or key < best[0]:
            best = (key, cand)
    assert best is not None
    return best[1]


def solve(
    rig: SkeletonRig,
    chain: Sequence[str],
    target: Point,
    current_angles: Mapping[str, float],
    *,
    limits: Optional[Mapping[str, JointLimit]] = DEFAULT_LIMITS,
) -> Dict[str, float]:
    """让链末端骨的 **tip** 到达 ``target``，返回链上各骨的本地角度。

    只含链上的骨，调用方自己合并进完整姿态：
    ``clip.set_pose(rig, frame, {**pose, **ik.solve(...)})``。

    末端骨（手/脚）优先只跟着平移、保持原有世界方向：拖的是它尖上那个点，不是
    它的朝向；要转手腕/脚踝仍然走 FK（拖骨体）。这样剩下的问题就是"两骨链把
    腕/踝送到指定点"，2D 下有闭式解——不迭代、没有局部极小、不会卡在限位带边界。
    保持朝向会把手/脚放进两骨环带之外时（例如手朝向身体），退一步让末端骨转向
    目标，末端仍然精确命中。

    残差只可能来自两处：目标超出可达范围，或限位带禁止了唯一能碰到目标的姿势。
    两种都由 :func:`tip_error` 量给调用方。
    """
    check_chain(rig, chain)
    if len(chain) > 3:
        raise IKError(f"闭式解只支持 2~3 骨的链，当前 {len(chain)} 骨：{chain}")

    current = {name: float(current_angles.get(name, rig.bones[name].angle)) for name in chain}
    # 算几何一律用调用方给的**完整**姿态：只传链上那几根骨的话，父骨（spine/root）
    # 会退回 T-pose 默认值，链根位置和基准世界角就都错了。
    pts = chain_positions(rig, chain, current_angles)
    tx, ty = float(target[0]), float(target[1])
    base = pts[0]
    if _dist(base, (tx, ty)) <= EPS:
        # 目标与链根重合：方向无定义，原样返回
        return current

    last = chain[-1]
    last_len = rig.bones[last].length
    tip, joint = pts[-1], pts[-2]
    world_last = math.degrees(math.atan2(tip[1] - joint[1], tip[0] - joint[0]))
    # 末端骨世界方向不变 -> 它的起点要到的位置
    origin_target = (
        tx - last_len * math.cos(math.radians(world_last)),
        ty - last_len * math.sin(math.radians(world_last)),
    )

    prefix = chain[:-1]
    parent_world = parent_world_angle(rig, chain, current_angles)
    lo, hi = _prefix_annulus(rig, prefix)
    dist_origin = _dist(base, origin_target)

    if lo <= dist_origin <= hi:
        want, want_world_last = origin_target, world_last
    else:
        # 保持朝向就放不进两骨环带（手/脚指向了身体方向）：改成让末端骨转向
        # target，把腕/踝压在 base→target 射线上。末端骨长度固定，这样 tip 仍然
        # 精确落在 target 上，只是姿态转了一下——比"够不到停在半路"更可预测。
        d_tip = _dist(base, (tx, ty))
        ux, uy = (tx - base[0]) / d_tip, (ty - base[1]) / d_tip
        radius = min(max(d_tip - last_len, lo), hi)
        want = (base[0] + ux * radius, base[1] + uy * radius)
        want_world_last = math.degrees(math.atan2(ty - want[1], tx - want[0]))

    if len(prefix) == 1:
        dx, dy = want[0] - base[0], want[1] - base[1]
        world0 = math.degrees(math.atan2(dy, dx)) if math.hypot(dx, dy) > EPS else parent_world
        solved = {prefix[0]: _shift_near(world0 - parent_world, current[prefix[0]])}
    else:
        solved = _two_bone(rig, prefix, base, want, parent_world, current, limits)

    solved[last] = _shift_near(
        want_world_last - (parent_world + sum(solved[name] for name in prefix)), current[last]
    )
    return _clamp_locals(solved, current, limits)




def tip_error(
    rig: SkeletonRig,
    chain: Sequence[str],
    angles: Mapping[str, float],
    target: Point,
) -> float:
    """给定姿态下链末端到 target 的距离，用于判断解是否够用。"""
    return _dist(chain_positions(rig, chain, angles)[-1], (float(target[0]), float(target[1])))
