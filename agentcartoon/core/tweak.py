# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""关键帧抽取（设计文档 4.2 方式A 第 5 步：稠密化 → 抽稀）。

模板按 ``key_step`` 每 2 帧铺一个关键帧，动作一多帧数就偏密；用户手工拖骨或 IK
整链联动之后，改动也可能散在几十个关键帧里。抽取做的是**同一个曲线换成更少的
控制点**：

1. 逐帧（或每 ``step`` 帧）稠密采样出完整姿态；
2. 段与段之间**递归二分**：若两关键帧之间有任何一帧偏离"两端插出来的曲线"超过
   阈值（角度按 ``angle_threshold`` 度、根位移按 ``position_threshold`` 单位），
   就在最超标那帧插一个关键帧，左右两半再各自检查，直到每段都合格；
3. 逐骨的**真转折点**（往两侧摆幅超过 :data:`EXTREMA_MIN_SWING` 的极值帧）无条件
   保留——动作的味道全在峰值上，只按误差抽会把它抹平；
4. 落在原始关键帧上的保留帧沿用它的 ``source`` 标记，新插入的帧取原始帧里占多数的
   那个 source（抽取不改动作出处）。原始关键帧默认**不**强制保留，见
   :func:`extract` 的 ``keep_originals``。

第 2 步比的必须是"两端插出来的曲线"而不是"上一稠密帧"：后者在匀速转骨时逐帧差都
低于阈值，于是一个关键帧都不插，两帧之间拉成直线，累积误差可以无限大；前者让阈值
直接成为**任意一帧的误差上限**，抽取结果因此带着一个可验证的承诺。

循环动画抽取完强制首尾姿态一致（沿用 :func:`agentcartoon.core.actions._close_loop`
的语义），所以 :meth:`model.AnimationClip.closure_error` 仍然是 0.00——这是本模块
的硬承诺，循环处一跳整个动作就废了。

**这是 opt-in**：:func:`agentcartoon.core.actions.build` 的默认输出不经过这里，
``tests/smoke.sh`` 断言的关键帧基准数因此不受影响。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .model import CLAMPED, AnimationClip, Keyframe, Point, SkeletonRig, _ease

DEFAULT_ANGLE_THRESHOLD = 8.0   # 度，段内相对插值曲线的偏差上限
DEFAULT_POSITION_THRESHOLD = 0.02   # 单位（= 1.6 像素），根位移同样的偏差上限
DEFAULT_STEP = 1                # 稠密采样的帧距
EXTREMA_MIN_SWING = 1.0         # 度，转折两侧的摆幅下限，滤掉曲线平顶处的数值抖动
_TURN_TINY = 1e-9               # 度，小于它算"没在转"，方向不变


@dataclass(frozen=True)
class Sample:
    """一个稠密采样点：帧号 + 完整本地角 + 根位移。"""

    frame: int
    angles: Dict[str, float]
    root: Point


def dense_poses(clip: AnimationClip, rig: SkeletonRig, step: int = DEFAULT_STEP) -> List[Sample]:
    """按 ``step`` 逐帧取完整姿态，首尾一定各占一个点。"""
    step = max(1, int(step))
    frames = list(range(0, clip.duration_frames + 1, step))
    if frames[-1] != clip.duration_frames:
        frames.append(clip.duration_frames)
    return [
        Sample(frame, clip.pose_at(rig, frame), clip.root_offset_at(frame))
        for frame in frames
    ]


def _turning_points(values: Sequence[float]) -> List[int]:
    """单调段的分界下标（不含首尾）。方向由差分符号决定，平段不改变方向。"""
    if len(values) < 3:
        return []
    pivots: List[int] = []
    direction = 0
    for index in range(1, len(values) - 1):
        delta = values[index + 1] - values[index]
        current = 1 if delta > _TURN_TINY else (-1 if delta < -_TURN_TINY else 0)
        if current and direction and current != direction:
            pivots.append(index)
        if current:
            direction = current
    return pivots


def significant_extrema(samples: Sequence[Sample], floor: float = EXTREMA_MIN_SWING) -> Set[int]:
    """每根骨的真极值帧：与相邻转折点之间的摆幅有一侧超过 ``floor`` 才算。

    只看"差分变号"会把数值抖动也当成峰值（密集采样下平顶附近来回变号），所以要
    拿两侧的摆幅量一把。
    """
    frames: Set[int] = set()
    if len(samples) < 3:
        return frames
    bones = sorted(samples[0].angles)
    for bone in bones:
        values = [s.angles[bone] for s in samples]
        marks = [0] + _turning_points(values) + [len(values) - 1]
        for position in range(1, len(marks) - 1):
            here = values[marks[position]]
            left = abs(here - values[marks[position - 1]])
            right = abs(here - values[marks[position + 1]])
            if max(left, right) >= floor:
                frames.add(samples[marks[position]].frame)
    return frames


def _max_deviation(angles: Dict[str, float], reference: Dict[str, float]) -> Tuple[str, float]:
    """相对参考姿态的最大单骨偏差，返回 (骨骼名, 偏差度数)。"""
    worst, value = "", 0.0
    for name, angle in angles.items():
        delta = abs(angle - reference.get(name, angle))
        if delta > value:
            worst, value = name, delta
    return worst, value


def _modal_source(keyframes: Sequence[Keyframe]) -> str:
    """原始关键帧里出现最多的 source，给新插入的帧用（抽取不改动作出处）。"""
    counts = Counter(kf.source for kf in keyframes if kf.source)
    return counts.most_common(1)[0][0] if counts else "template"


def _segment_worst(
    samples: Sequence[Sample],
    start: int,
    end: int,
    angle_threshold: float,
    position_threshold: float,
) -> Optional[int]:
    """段 ``(start, end)`` 内相对 CLAMPED 插值曲线最超标的采样下标，全段合格给 None。

    比的是"这一段中间那些帧，用两端关键帧插出来的值差多少"，而不是相邻两帧的差：
    匀速转动的骨相邻差都低于阈值，逐帧比会一个关键帧都不插，段内却累积出大误差。
    角度（度）与位移（单位）量纲不同，各自除以阈值化成"超标倍数"再取最大者二分。
    """
    a, b = samples[start], samples[end]
    span = b.frame - a.frame
    if span <= 0 or end - start < 2:
        return None
    worst, at = 1.0, None
    for index in range(start + 1, end):
        sample = samples[index]
        t = _ease(CLAMPED, (sample.frame - a.frame) / span)
        ratio = 0.0
        for name, angle in sample.angles.items():
            expected = a.angles[name] + (b.angles[name] - a.angles[name]) * t
            ratio = max(ratio, abs(angle - expected) / angle_threshold)
        dx = sample.root[0] - (a.root[0] + (b.root[0] - a.root[0]) * t)
        dy = sample.root[1] - (a.root[1] + (b.root[1] - a.root[1]) * t)
        ratio = max(ratio, math.hypot(dx, dy) / position_threshold)
        if ratio > worst:
            worst, at = ratio, index
    return at


def select_frames(
    samples: Sequence[Sample],
    *,
    angle_threshold: float = DEFAULT_ANGLE_THRESHOLD,
    position_threshold: float = DEFAULT_POSITION_THRESHOLD,
    forced: Iterable[int] = (),
) -> Set[int]:
    """递归二分，返回要保留的**采样下标**集合；``forced`` 无条件保留。

    结束条件是每段内的偏差都不超过阈值，所以返回值带着一个承诺:**抽完的曲线在
    任意一帧上离原曲线不超过 ``angle_threshold`` 度 / ``position_threshold`` 单位**
    （前提：输出用同一种插值，这里固定 CLAMPED）。
    """
    keep = set(forced) | {0, len(samples) - 1}
    if min(angle_threshold, position_threshold) <= 0.0:
        return set(range(len(samples)))
    marks = sorted(keep)
    stack = list(zip(marks, marks[1:]))
    while stack:
        start, end = stack.pop()
        worst = _segment_worst(samples, start, end, angle_threshold, position_threshold)
        if worst is None:
            continue
        keep.add(worst)
        stack.append((start, worst))
        stack.append((worst, end))
    return keep


def extract(
    clip: AnimationClip,
    rig: SkeletonRig,
    *,
    angle_threshold: float = DEFAULT_ANGLE_THRESHOLD,
    position_threshold: float = DEFAULT_POSITION_THRESHOLD,
    extrema: bool = True,
    keep_originals: bool = False,
    step: int = DEFAULT_STEP,
    name: Optional[str] = None,
) -> AnimationClip:
    """抽稀关键帧，返回**新片段**（原片段不动，撤销栈里它是 before 快照）。

    ``keep_originals=True`` 时原始关键帧一律保留，抽取只增不减——想在保住模板原有
    节奏点的基础上补细节就用它；默认 False，否则"抽稀"无从谈起（模板每 2 帧一个
    K，全都保留就永远减不下来）。

    阈值设 0 表示每一帧都留；想完全按误差走，传 ``extrema=False``。
    """
    samples = dense_poses(clip, rig, step)
    originals = {kf.frame: kf for kf in clip.keyframes if 0 <= kf.frame <= clip.duration_frames}
    if keep_originals:
        # 原始关键帧可能不落在采样网格上（step=2 却有个奇数帧），补进网格才谈得上保留
        missing = sorted(set(originals) - {sample.frame for sample in samples})
        if missing:
            samples += [
                Sample(frame, clip.pose_at(rig, frame), clip.root_offset_at(frame))
                for frame in missing
            ]
            samples.sort(key=lambda sample: sample.frame)

    forced: Set[int] = set()
    if keep_originals:
        forced |= {index for index, sample in enumerate(samples) if sample.frame in originals}
    if extrema:
        extrema_frames = significant_extrema(samples)
        forced |= {index for index, sample in enumerate(samples) if sample.frame in extrema_frames}
    kept_indices = sorted(select_frames(
        samples, angle_threshold=angle_threshold,
        position_threshold=position_threshold, forced=forced,
    ))

    default_source = _modal_source(clip.keyframes)
    out = clip.copy()
    out.name = name or f"{clip.name}:extracted"
    out.keyframes = [
        Keyframe(
            frame=sample.frame,
            bone_angles=dict(sample.angles),
            bone_positions={"root": sample.root},
            interpolation=CLAMPED,
            source=originals[sample.frame].source if sample.frame in originals else default_source,
        )
        for sample in (samples[index] for index in kept_indices)
    ]
    if clip.loop:
        # 尾帧必须是首帧的副本，不然循环处一跳（closure_error 立刻非 0）
        head = out.keyframes[0]
        tail = out.keyframe_at(clip.duration_frames)
        if tail is None:
            tail = Keyframe(frame=clip.duration_frames, interpolation=head.interpolation,
                            source=head.source)
            out.keyframes.append(tail)
        tail.bone_angles = dict(head.bone_angles)
        tail.bone_positions = dict(head.bone_positions)

    out.metadata["extracted_from"] = {
        "angle_threshold": angle_threshold,
        "position_threshold": position_threshold,
        "step": step,
        "extrema": extrema,
        "keep_originals": keep_originals,
        "dense_samples": len(samples),
        "source_keyframes": len(clip.keyframes),
        "out_of_range_keyframes": len(clip.keyframes) - len(originals),
    }
    return out


def max_error(clip: AnimationClip, reference: AnimationClip, rig: SkeletonRig,
              step: int = DEFAULT_STEP) -> Tuple[float, int, str]:
    """抽取后的姿态对原曲线的最大角度偏差：``(度数, 帧号, 骨骼)``。"""
    worst, at, bone = 0.0, 0, ""
    for sample in dense_poses(clip, rig, step):
        name, delta = _max_deviation(sample.angles, reference.pose_at(rig, sample.frame))
        if delta > worst:
            worst, at, bone = delta, sample.frame, name
    return worst, at, bone


def max_position_error(clip: AnimationClip, reference: AnimationClip, rig: SkeletonRig,
                       step: int = DEFAULT_STEP) -> Tuple[float, int]:
    """抽取后的根位移对原曲线的最大偏差：``(单位, 帧号)``。1 单位 = 80 像素。"""
    worst, at = 0.0, 0
    for sample in dense_poses(clip, rig, step):
        base = reference.root_offset_at(sample.frame)
        delta = math.hypot(sample.root[0] - base[0], sample.root[1] - base[1])
        if delta > worst:
            worst, at = delta, sample.frame
    return worst, at
