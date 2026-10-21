# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""核心数据模型：骨骼、骨骼绑定、关键帧、动画片段（对应设计文档第三章）。

角度约定
--------
* 世界方向角 = 父骨骼世界方向角 + 本骨骼本地角度（单位：度，逆时针为正，Y 轴向上）。
* 本地角度 0 表示与父骨骼同向；骨骼静止方向（T-pose）由 ``t_pose_angles`` 给出。
* 方向向量 dir(θ) = (cosθ, sinθ)。

这些约定与 Synfig 的 rotate 层一致（rotate amount 以度为单位、逆时针为正、Y 轴向上），
因此导出时可直接写 ``amount = 当前本地角度 - 静止本地角度``。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import ClassVar, Dict, List, Optional, Tuple

Point = Tuple[float, float]

LINEAR = "linear"
CONSTANT = "constant"
CLAMPED = "clamped"
EASE_IN_OUT = "ease_in_out"

INTERPOLATIONS = (LINEAR, CONSTANT, CLAMPED, EASE_IN_OUT)

# 内部插值名 -> Synfig waypoint 的 before/after 取值
SYNFIG_INTERPOLATION = {
    LINEAR: "linear",
    CONSTANT: "constant",
    CLAMPED: "clamped",
    EASE_IN_OUT: "auto",  # TCB 自动插值
}


def direction(degrees: float) -> Point:
    rad = math.radians(degrees)
    return (math.cos(rad), math.sin(rad))


@dataclass
class Bone:
    """单根骨骼。origin 为在父骨骼局部坐标系中的相对偏移。"""

    name: str
    parent: Optional[str] = None
    length: float = 1.0
    origin: Point = (0.0, 0.0)
    angle: float = 0.0
    children: List[str] = field(default_factory=list)
    # 骨骼线段起点在父骨骼上的位置比例（0=父骨骼起点，1=父骨骼末端）
    anchor: float = 1.0
    width: float = 0.07
    tipwidth: float = 0.05
    bone_depth: float = 0.0

    def tip_local(self) -> Point:
        """骨骼在其自身局部坐标系中的末端（指向 +Y）。"""
        return (0.0, self.length)


@dataclass
class SkeletonRig:
    """角色骨骼绑定数据。"""

    name: str = "AgentCartoon_Rig"
    bones: Dict[str, Bone] = field(default_factory=dict)
    root_bone: str = "root"
    skin_weights: Dict[str, Dict[str, float]] = field(default_factory=dict)
    t_pose_angles: Dict[str, float] = field(default_factory=dict)
    # 根骨骼在画布上的静止位置
    root_position: Point = (0.0, 0.0)

    # 标准 15 骨人形模板：(name, parent, length, anchor, t_pose_local_angle)
    STANDARD_HUMAN_BONES: ClassVar[List[Tuple[str, Optional[str], float, float, float]]] = [
        ("root", None, 0.0, 0.0, 90.0),
        ("spine", "root", 1.05, 0.0, 0.0),
        ("head", "spine", 0.78, 1.0, 0.0),
        ("L_arm", "spine", 0.80, 1.0, 90.0),
        ("L_forearm", "L_arm", 0.75, 1.0, 0.0),
        ("L_hand", "L_forearm", 0.28, 1.0, 0.0),
        ("R_arm", "spine", 0.80, 1.0, -90.0),
        ("R_forearm", "R_arm", 0.75, 1.0, 0.0),
        ("R_hand", "R_forearm", 0.28, 1.0, 0.0),
        ("L_leg", "root", 1.05, 0.0, 180.0),
        ("L_calf", "L_leg", 1.00, 1.0, 0.0),
        ("L_foot", "L_calf", 0.32, 1.0, 90.0),
        ("R_leg", "root", 1.05, 0.0, 180.0),
        ("R_calf", "R_leg", 1.00, 1.0, 0.0),
        ("R_foot", "R_calf", 0.32, 1.0, 90.0),
    ]

    def __post_init__(self) -> None:
        if not self.bones:
            self.build_template()

    def build_template(self, scale: float = 1.0) -> None:
        self.bones = {}
        self.t_pose_angles = {}
        for name, parent, length, anchor, angle in self.STANDARD_HUMAN_BONES:
            self.bones[name] = Bone(
                name=name,
                parent=parent,
                length=round(length * scale, 6),
                anchor=anchor,
                angle=angle,
                origin=(0.0, 0.0),
            )
            self.t_pose_angles[name] = angle
            if parent is not None:
                self.bones[parent].children.append(name)

    # ------------------------------------------------------------------ 层级
    def lineage(self, bone_name: str) -> List[str]:
        """从根到该骨骼的名字列表。"""
        chain: List[str] = []
        current: Optional[str] = bone_name
        while current is not None:
            chain.append(current)
            current = self.bones[current].parent
        return list(reversed(chain))

    def depth_first(self) -> List[str]:
        """父先于子的骨骼顺序（用于写 .sif 与矩阵传播）。"""
        order: List[str] = []
        seen = set()

        def visit(name: str) -> None:
            if name in seen:
                return
            seen.add(name)
            order.append(name)
            for child in self.bones[name].children:
                visit(child)

        visit(self.root_bone)
        for name in self.bones:
            visit(name)
        return order

    @property
    def height(self) -> float:
        """静止姿态下的近似身高（单位）。"""
        world = self.world_transforms(self.rest_pose())
        ys = [y for name in world for y in (world[name][0][1], world[name][1][1])]
        return max(ys) - min(ys) if ys else 0.0

    # ------------------------------------------------------------------ 姿态
    def rest_pose(self) -> Dict[str, float]:
        return dict(self.t_pose_angles)

    def world_transforms(self, angles: Dict[str, float]) -> Dict[str, Tuple[Point, Point, float]]:
        """给定本地角度表，返回每根骨骼的 (起点, 末端, 世界方向角)。"""
        result: Dict[str, Tuple[Point, Point, float]] = {}
        for name in self.depth_first():
            bone = self.bones[name]
            local = angles.get(name, bone.angle)
            if bone.parent is None:
                origin = self.root_position
                world_angle = local
            else:
                p_origin, p_tip, p_angle = result[bone.parent]
                parent_bone = self.bones[bone.parent]
                base = (
                    p_origin[0] + (p_tip[0] - p_origin[0]) * bone.anchor,
                    p_origin[1] + (p_tip[1] - p_origin[1]) * bone.anchor,
                )
                # 相对偏移按父骨骼方向旋转
                dx, dy = bone.origin
                cos_a, sin_a = math.cos(math.radians(p_angle)), math.sin(math.radians(p_angle))
                origin = (base[0] + dx * cos_a - dy * sin_a, base[1] + dx * sin_a + dy * cos_a)
                world_angle = p_angle + local
            ux, uy = direction(world_angle)
            result[name] = (origin, (origin[0] + ux * bone.length, origin[1] + uy * bone.length), world_angle)
        return result

    def joint_positions(self, angles: Dict[str, float]) -> Dict[str, Point]:
        return {name: tf[0] for name, tf in self.world_transforms(angles).items()}

    def copy(self) -> "SkeletonRig":
        rig = SkeletonRig.__new__(SkeletonRig)
        rig.name = self.name
        rig.root_bone = self.root_bone
        rig.root_position = self.root_position
        rig.skin_weights = {k: dict(v) for k, v in self.skin_weights.items()}
        rig.t_pose_angles = dict(self.t_pose_angles)
        rig.bones = {}
        for name, bone in self.bones.items():
            clone = field_clone(bone)
            clone.children = list(bone.children)
            rig.bones[name] = clone
        return rig


def field_clone(obj):
    import copy as _copy

    new = obj.__class__.__new__(obj.__class__)
    new.__dict__.update(_copy.deepcopy(obj.__dict__))
    return new


@dataclass
class Keyframe:
    """单个关键帧。"""

    frame: int
    bone_angles: Dict[str, float] = field(default_factory=dict)
    bone_positions: Dict[str, Point] = field(default_factory=dict)
    interpolation: str = CLAMPED
    tension: float = 0.0
    bias: float = 0.0
    continuity: float = 0.0
    source: str = "manual"  # manual / template / ai，用于 GUI 区分标记

    def time_seconds(self, fps: float) -> float:
        return self.frame / float(fps)


@dataclass
class AnimationClip:
    """动画片段。"""

    name: str = "clip"
    fps: int = 24
    duration_frames: int = 48
    keyframes: List[Keyframe] = field(default_factory=list)
    loop: bool = False
    metadata: Dict = field(default_factory=dict)

    def sorted_keyframes(self) -> List[Keyframe]:
        return sorted(self.keyframes, key=lambda k: k.frame)

    def frames(self) -> List[int]:
        return [k.frame for k in self.sorted_keyframes()]

    # -------------------------------------------------------------- 采样
    def _bracket(self, frame: int) -> Tuple[Optional[Keyframe], Optional[Keyframe]]:
        kfs = self.sorted_keyframes()
        if not kfs:
            return None, None
        prev = None
        for kf in kfs:
            if kf.frame <= frame:
                prev = kf
            else:
                return prev, kf
        return prev, None

    def angle_at(self, bone: str, frame: int, rest: float = 0.0) -> float:
        prev, nxt = self._bracket(frame)
        if prev is None and nxt is None:
            return rest
        if prev is None:
            return nxt.bone_angles.get(bone, rest)
        if nxt is None:
            return prev.bone_angles.get(bone, rest)
        if nxt.frame == prev.frame:
            return prev.bone_angles.get(bone, rest)
        t = (frame - prev.frame) / (nxt.frame - prev.frame)
        t = _ease(prev.interpolation, t)
        a = prev.bone_angles.get(bone, rest)
        b = nxt.bone_angles.get(bone, rest)
        return a + (b - a) * t

    def position_at(self, bone: str, frame: int, rest: Point = (0.0, 0.0)) -> Point:
        prev, nxt = self._bracket(frame)
        if prev is None and nxt is None:
            return rest
        if prev is None:
            return nxt.bone_positions.get(bone, rest)
        if nxt is None:
            return prev.bone_positions.get(bone, rest)
        if nxt.frame == prev.frame:
            return prev.bone_positions.get(bone, rest)
        t = (frame - prev.frame) / (nxt.frame - prev.frame)
        t = _ease(prev.interpolation, t)
        a = prev.bone_positions.get(bone, rest)
        b = nxt.bone_positions.get(bone, rest)
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    def pose_at(self, rig: SkeletonRig, frame: int) -> Dict[str, float]:
        """返回该帧每根骨骼的本地角度。"""
        return {
            name: self.angle_at(name, frame, rig.t_pose_angles.get(name, bone.angle))
            for name, bone in rig.bones.items()
        }

    def root_offset_at(self, frame: int) -> Point:
        return self.position_at("root", frame, (0.0, 0.0))

    def duration_seconds(self) -> float:
        return self.duration_frames / float(self.fps)

    def closure_error(self) -> float:
        """循环动画首尾角度差（绝对值之和），非循环返回 0。"""
        if not self.loop or len(self.keyframes) < 2:
            return 0.0
        first = self.sorted_keyframes()[0]
        last = self.sorted_keyframes()[-1]
        return sum(
            abs(first.bone_angles.get(name, 0.0) - last.bone_angles.get(name, 0.0))
            for name in first.bone_angles
        )

    def keyframe_at(self, frame: int) -> Optional[Keyframe]:
        for kf in self.keyframes:
            if kf.frame == frame:
                return kf
        return None

    def set_pose(self, rig: SkeletonRig, frame: int, angles: Dict[str, float],
                 positions: Optional[Dict[str, Point]] = None, source: str = "manual",
                 interpolation: str = CLAMPED) -> Keyframe:
        """写入/覆盖某帧的姿态，返回该关键帧。

        ``positions=None`` 表示**不动**这一帧已有的骨骼位移，不是清空。拖骨、改角度
        只换旋转，若不保留就会把模板写进去的 root 位移抹成 (0,0)——角色在那一帧凭空
        后退一步、走路的推进被捅出一个个洞。要真清空请显式传 ``positions={}``。
        """
        kf = self.keyframe_at(frame)
        if kf is None:
            kf = Keyframe(frame=frame, interpolation=interpolation, source=source)
            self.keyframes.append(kf)
        kf.bone_angles = dict(angles)
        if positions is not None:
            kf.bone_positions = dict(positions)
        kf.source = source
        kf.interpolation = interpolation
        return kf

    def copy(self) -> "AnimationClip":
        import copy as _copy

        clip = AnimationClip(
            name=self.name, fps=self.fps, duration_frames=self.duration_frames,
            loop=self.loop, metadata=_copy.deepcopy(self.metadata),
        )
        clip.keyframes = [field_clone(kf) for kf in self.keyframes]
        for kf, src in zip(clip.keyframes, self.keyframes):
            kf.bone_angles = dict(src.bone_angles)
            kf.bone_positions = dict(src.bone_positions)
        return clip


def _ease(interpolation: str, t: float) -> float:
    """GUI 预览用的插值近似；导出时由 Synfig 按 waypoint 类型计算。"""
    t = max(0.0, min(1.0, t))
    if interpolation == LINEAR:
        return t
    if interpolation == CONSTANT:
        return 0.0
    if interpolation == EASE_IN_OUT:
        return t * t * (3.0 - 2.0 * t)
    # clamped：单调三次，不过冲
    return t * t * (3.0 - 2.0 * t) * 0.5 + t * 0.5
