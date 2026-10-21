# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""动画自动质检（设计文档 4.4）。

五项检测，全部只依赖 :mod:`agentcartoon.core.model` 的采样接口，不渲染、不出图、
不联网，所以可以在生成动作后立刻跑一遍：

======================  =============================================  ======
检测项                  判据                                            级别
======================  =============================================  ======
关节角度超限            本地角越出 :data:`core.ik.DEFAULT_LIMITS`       warn
跨部位穿模              骨段 AABB（按骨宽外扩、再内缩去误报）相交        warn
脚部滑动                接地期间脚在世界系里仍在平移                    warn
循环不闭合              首尾关键帧姿态差 > 5°                           error
根骨漂移                根骨速度的二阶差分远高于全片常态            warn
======================  =============================================  ======

**为什么穿模只查跨部位、不查左右**：标准 15 骨模板的左右肢体是完全重合的——
``L_leg`` 与 ``R_leg`` 都挂在 root 的同一个点上、静止角都是 180°，两只脚同理，
两条手臂共用肩点。这是正视图纸片人的正常画法（谁压着谁由 ``bone_depth`` 决定），
不是穿模。任何"左↔右"配对都会每帧常亮，只有"手/前臂压过头/躯干/腿"这种跨部位
重叠才是真信号。抬臂动作仍会让手臂经过头部区域，属已知误报，故只报 warn。

**同一问题只报一条**：一次滑步往往持续十几帧，逐帧报会淹掉结论，所以
:func:`_collapse` 把同代号同骨骼的连续帧并成一条，保留最严重那帧的措辞。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

from . import ik
from .model import AnimationClip, Point, SkeletonRig

# ------------------------------------------------------------------ 阈值（可调）
CLOSURE_ERROR_LIMIT = 5.0        # 度，设计文档 4.4 的阈值
FOOT_GROUND_EPS = 0.05           # 单位，低于最低点这么多算接地
FOOT_SLIDE_LIMIT = 0.05          # 单位/帧，接地时每帧平移超过这么多算滑步
BOX_INSET = 0.15                 # 骨段包围盒向内收缩比例，压掉贴边误报
DRIFT_FLOOR = 0.02               # 单位/帧²，绝对下限，防 idle 的微抖被相对阈值放大
# 相对全片 75 分位数的倍数。定在 2.5 是量出来的：跑动的上下弹跳在每个周期谷底
# 有个折角（2.1×），那是模板的节奏风格而不是毛病；起跳蹬地与落地是单帧速度脉冲
# （4.1×），插值打结/瞬移更高。2.0 会把前者一并报出来，太吵。
DRIFT_RATIO = 2.5

# 跨部位穿模配对。(a, b) 无序；不列左右配对，理由见模块 docstring。
PENETRATION_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("L_hand", "spine"), ("R_hand", "spine"),
    ("L_forearm", "spine"), ("R_forearm", "spine"),
    ("L_hand", "L_leg"), ("L_hand", "R_leg"),
    ("R_hand", "L_leg"), ("R_hand", "R_leg"),
    ("L_hand", "head"), ("R_hand", "head"),
)


@dataclass(frozen=True)
class Issue:
    """一条质检结论。``frame`` 为 -1 表示整段动画层面的问题。"""

    level: str          # error / warn
    code: str           # joint_over_limit / penetration / foot_slide / loop_not_closed / root_drift
    frame: int
    bone: Optional[str]
    message: str
    weight: float = 0.0     # 严重程度，合并连续帧时保留最大的那条措辞
    last_frame: int = -1    # 合并后的区间末端；-1 表示只有一帧

    def __str__(self) -> str:
        if self.frame < 0:
            where = "整段"
        elif self.last_frame > self.frame:
            where = f"第 {self.frame}-{self.last_frame} 帧"
        else:
            where = f"第 {self.frame} 帧"
        who = f" [{self.bone}]" if self.bone else ""
        return f"{self.level.upper():5s} {where}{who} {self.code}: {self.message}"


def _percentile(values: Sequence[float], q: float) -> float:
    """线性插值分位数（不为一个统计函数引 numpy）。空表返回 0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    low = math.floor(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _collapse(issues: Sequence[Issue]) -> List[Issue]:
    """同代号、同骨骼、帧号连续的结论并成一条。"""
    merged: List[Issue] = []
    for issue in sorted(issues, key=lambda i: (i.code, i.bone or "", i.frame)):
        if merged and (merged[-1].code, merged[-1].bone, merged[-1].level) == (issue.code, issue.bone, issue.level) \
                and issue.frame == max(merged[-1].last_frame, merged[-1].frame) + 1:
            prev = merged[-1]
            keep = issue if issue.weight >= prev.weight else prev
            merged[-1] = replace(keep, frame=min(prev.frame, issue.frame),
                                 last_frame=max(prev.last_frame, prev.frame, issue.last_frame, issue.frame))
            continue
        merged.append(issue)
    return merged


# --------------------------------------------------------------------- 单项检测
def joint_over_limit(
    clip: AnimationClip,
    rig: SkeletonRig,
    limits: Optional[Dict[str, ik.JointLimit]] = None,
) -> List[Issue]:
    """逐帧检查本地角是否越出关节限位带。"""
    limits = ik.DEFAULT_LIMITS if limits is None else limits
    issues: List[Issue] = []
    for frame in range(clip.duration_frames + 1):
        pose = clip.pose_at(rig, frame)
        for name, value in pose.items():
            limit = limits.get(name)
            if limit is None:
                continue
            lo, hi = ik._shift_band(limit, rig.t_pose_angles.get(name, rig.bones[name].angle))
            if lo - 1e-6 <= value <= hi + 1e-6:
                continue
            over = value - hi if value > hi else lo - value
            issues.append(Issue(
                "warn", "joint_over_limit", frame, name,
                f"本地角 {value:.1f}° 越出 [{lo:.1f}°, {hi:.1f}°]，超出 {over:.1f}°",
                weight=over,
            ))
    return _collapse(issues)


def _bone_box(tf_entry: Tuple[Point, Point, float], bone) -> Tuple[float, float, float, float]:
    """骨段的轴对齐包围盒：线段两端点按最大半宽外扩。"""
    (x0, y0), (x1, y1) = tf_entry[0], tf_entry[1]
    half = max(bone.width, bone.tipwidth) / 2.0
    return (min(x0, x1) - half, min(y0, y1) - half, max(x0, x1) + half, max(y0, y1) + half)


def _inset(box: Tuple[float, float, float, float], ratio: float) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    dx, dy = (x1 - x0) * ratio / 2.0, (y1 - y0) * ratio / 2.0
    return (x0 + dx, y0 + dy, x1 - dx, y1 - dy)


def _overlap(a: Sequence[float], b: Sequence[float]) -> float:
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    return dx * dy if dx > 0.0 and dy > 0.0 else 0.0


def aabb_penetration(
    clip: AnimationClip,
    rig: SkeletonRig,
    pairs: Tuple[Tuple[str, str], ...] = PENETRATION_PAIRS,
    inset: float = BOX_INSET,
) -> List[Issue]:
    """跨部位骨段包围盒相交检测。"""
    issues: List[Issue] = []
    names = {n for pair in pairs for n in pair if n in rig.bones}
    for frame in range(clip.duration_frames + 1):
        tf = rig.world_transforms(clip.pose_at(rig, frame))
        boxes = {n: _inset(_bone_box(tf[n], rig.bones[n]), inset) for n in names}
        for a, b in pairs:
            if a not in boxes or b not in boxes:
                continue
            area = _overlap(boxes[a], boxes[b])
            if area > 1e-6:
                issues.append(Issue(
                    "warn", "penetration", frame, f"{a}/{b}",
                    f"骨段包围盒相交 {area:.4f} 平方单位",
                    weight=area,
                ))
    return _collapse(issues)


def foot_slide(
    clip: AnimationClip,
    rig: SkeletonRig,
    *,
    ground_eps: float = FOOT_GROUND_EPS,
    limit: float = FOOT_SLIDE_LIMIT,
) -> List[Issue]:
    """接地期间脚还在平移 = 滑步。做 IK 锁脚之前，先要知道它有多滑。

    地面取**两只脚的全程最低点**（同一水平面），不是各自脚的最低点：否则腾空中的
    脚也会拿自己的轨迹最低处当地面，把摆动误判成滑步。接触帧按"这一帧的脚在
    地面上"判定，水平速度取左右邻帧里更大的那一侧——落点只有单帧的动作（跑）在
    接触瞬间照样在横扫，只看连续接地帧会漏掉它。

    循环动画的末帧是首帧的副本，而根位移在那里归零（一个周期的位移记在周期内），
    两帧的世界 x 相差整整一步的 stride。它是周期边界不是滑步，末帧不进轨迹。
    """
    feet = [name for name in ("L_foot", "R_foot") if name in rig.bones]
    if not feet:
        return []
    last = clip.duration_frames - 1 if clip.loop else clip.duration_frames
    tracks: Dict[str, List[Tuple[float, float]]] = {}
    for name in feet:
        points = []
        for frame in range(last + 1):
            offset = clip.root_offset_at(frame)
            tip = rig.world_transforms(clip.pose_at(rig, frame))[name][1]
            points.append((tip[0] + offset[0], tip[1] + offset[1]))
        tracks[name] = points
    ground = min(y for points in tracks.values() for _, y in points)
    issues: List[Issue] = []
    for name, points in tracks.items():
        for frame, (x, y) in enumerate(points):
            if y > ground + ground_eps:
                continue
            speed = max(
                (abs(points[near][0] - x) for near in (frame - 1, frame + 1)
                 if 0 <= near <= last),
                default=0.0,
            )
            if speed > limit:
                issues.append(Issue(
                    "warn", "foot_slide", frame, name,
                    f"脚在地面上却仍平移 {speed:.3f} 单位/帧（约 {speed * 80:.0f}px），在滑步",
                    weight=speed,
                ))
    return _collapse(issues)


def loop_not_closed(clip: AnimationClip) -> List[Issue]:
    """循环动画首尾姿态必须接得上。"""
    error = clip.closure_error()
    if clip.loop and error > CLOSURE_ERROR_LIMIT:
        return [Issue("error", "loop_not_closed", -1, None,
                      f"首尾姿态差 {error:.2f}°，循环处会跳", weight=error)]
    return []


def root_drift(clip: AnimationClip, rig: SkeletonRig) -> List[Issue]:
    """根骨速度的二阶差分显著高于全片常态 = 打结/瞬移。

    为什么比"中位数的若干倍"更严一点而仍然可靠：抛物线（跳跃腾空段）的逐帧加速度
    是**恒定**的，二阶差分接近 0，所以平滑弧线不会被判成突变；真正会被判出的只有
    速度在单帧里跳变的场合——蹬地是弹射而不是渐推、循环收尾把根位移直接归零、
    关键帧插值打了个结。基线取 75 分位数而不是中位数：动作里常有一段静止帧，
    中位数会被它们拉到接近 0，于是整条正常加速曲线都"超标"。

    循环动画的收尾帧是周期边界（姿态等于首帧、根位移归零），不参与检测。
    """
    last = clip.duration_frames - 1 if clip.loop else clip.duration_frames
    points = [clip.root_offset_at(f) for f in range(last + 1)]
    if len(points) < 3:
        return []
    velocity = [(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
                for i in range(len(points) - 1)]
    accel = [math.hypot(velocity[i + 1][0] - velocity[i][0],
                        velocity[i + 1][1] - velocity[i][1])
             for i in range(len(velocity) - 1)]
    baseline = max(_percentile(accel, 0.75), DRIFT_FLOOR)
    issues: List[Issue] = []
    for index, value in enumerate(accel):
        if value < DRIFT_FLOOR or value < baseline * DRIFT_RATIO:
            continue
        issues.append(Issue(
            "warn", "root_drift", index + 1, "root",
            f"根骨速度突变 {value:.4f} 单位/帧²（全片常态 {baseline:.4f}）",
            weight=value,
        ))
    return _collapse(issues)


# --------------------------------------------------------------------- 汇总入口
DETECTORS = (joint_over_limit, aabb_penetration, foot_slide, root_drift)


def check(
    clip: AnimationClip,
    rig: SkeletonRig,
    *,
    limits: Optional[Dict[str, ik.JointLimit]] = None,
) -> List[Issue]:
    """跑全部五项，按帧号、代号排序返回问题列表。"""
    issues: List[Issue] = list(loop_not_closed(clip))
    for detector in DETECTORS:
        if detector is joint_over_limit:
            issues.extend(detector(clip, rig, ik.DEFAULT_LIMITS if limits is None else limits))
        else:
            issues.extend(detector(clip, rig))
    return sorted(issues, key=lambda i: (i.frame, i.code, i.bone or ""))


def summarize(issues: Sequence[Issue]) -> str:
    """一行汇总，给状态栏/CLI 用。"""
    errors = sum(1 for i in issues if i.level == "error")
    return f"质检 {len(issues)} 项（error {errors} / warn {len(issues) - errors}）"
