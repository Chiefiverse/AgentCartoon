# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""参数化动作模板库（设计文档 4.2 方式B）。

五个内置模板：idle_breathing / walk_cycle / run_cycle / wave_hand / jump。
每个模板返回 :class:`~agentcartoon.core.model.AnimationClip`，关键帧存的是
骨骼“本地角度”（见 model.py 的角度约定），可直接用于预览与导出 .sif。

像素/单位换算：画布缩放为 ``PX_PER_UNIT`` 像素/单位，模板参数沿用设计文档里
以像素为单位的写法（stride=80、bounce=5 等），内部换算成画布单位。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List

from .model import CLAMPED, EASE_IN_OUT, AnimationClip, Keyframe, SkeletonRig

PX_PER_UNIT = 80.0
TWO_PI = 2.0 * math.pi

SPEED_TABLE = {"slow": 0.7, "normal": 1.0, "fast": 1.4}


@dataclass
class ParamDef:
    name: str
    label: str
    minimum: float
    maximum: float
    default: float
    step: float = 1.0
    unit: str = ""
    choices: Dict[str, float] = field(default_factory=dict)

    def resolve(self, value):
        """解析外部传入值：枚举别名 -> 对应值，其余转 float。"""
        if isinstance(value, str) and self.choices:
            key = value.strip().lower()
            if key in self.choices:
                return self.choices[key]
        if isinstance(value, str):
            return float(value)
        return value

    def normalize(self, value) -> float:
        """把 CLI/GUI 传入值转成 0..1 之间的比例，并解析 speed 这类别名。"""
        value = self.resolve(value)
        if isinstance(value, str):
            return 0.5
        value = float(value)
        span = self.maximum - self.minimum
        return value if span <= 0 else (value - self.minimum) / span


@dataclass
class ActionTemplate:
    key: str
    label: str
    loop: bool
    default_duration: float
    params: List[ParamDef]
    generate: Callable[..., AnimationClip]
    hint: str = ""

    def param(self, name: str) -> ParamDef:
        for p in self.params:
            if p.name == name:
                return p
        raise KeyError(name)


def _new_clip(name: str, fps: int, frames: int, loop: bool) -> AnimationClip:
    return AnimationClip(name=name, fps=fps, duration_frames=frames, loop=loop)


def _add(clip: AnimationClip, frame: int, angles: Dict[str, float],
         root_offset=(0.0, 0.0), interpolation=CLAMPED, source="template") -> None:
    clip.keyframes.append(
        Keyframe(
            frame=frame,
            bone_angles=dict(angles),
            bone_positions={"root": (root_offset[0], root_offset[1])},
            interpolation=interpolation,
            source=source,
        )
    )


def _flat_foot(leg_local: float, calf_local: float) -> float:
    """让脚掌保持水平（世界方向 360°）所需的脚踝本地角度。"""
    leg_world = 90.0 + leg_local
    calf_world = leg_world + calf_local
    return 360.0 - leg_world - calf_world


# --------------------------------------------------------------------------- idle
def generate_idle(fps: int = 24, duration: float = 2.0, amplitude: float = 0.4,
                  frequency: float = 0.5, key_step: int = 3, rest: Dict[str, float] = None) -> AnimationClip:
    """呼吸待机：胸腔起伏 + 轻微重心晃动 + 手臂摆动。"""
    rest = rest or SkeletonRig().t_pose_angles
    frames = max(2, int(round(duration * fps)))
    clip = _new_clip("idle", fps, frames, loop=True)
    for frame in range(0, frames, max(1, key_step)):
        t = frame / frames * TWO_PI * frequency * duration
        breathe = math.sin(t)
        angles = dict(rest)
        angles["spine"] = rest["spine"] - 1.8 * amplitude * breathe
        angles["head"] = rest["head"] + 1.8 * amplitude * breathe
        angles["L_arm"] = rest["L_arm"] + 2.5 * amplitude * breathe
        angles["R_arm"] = rest["R_arm"] - 2.5 * amplitude * breathe
        angles["L_forearm"] = rest["L_forearm"] + 1.5 * amplitude * breathe
        angles["R_forearm"] = rest["R_forearm"] - 1.5 * amplitude * breathe
        angles["L_leg"] = rest["L_leg"]
        angles["R_leg"] = rest["R_leg"]
        _add(clip, frame, angles, (0.0, 0.012 * amplitude * breathe), CLAMPED)
    _close_loop(clip)
    return clip


# --------------------------------------------------------------------------- walk
def generate_walk(fps: int = 24, duration: float = 1.0, stride: float = 80.0,
                  speed: float = 1.0, arm_swing: float = 30.0, bounce: float = 5.0,
                  key_step: int = 2, rest: Dict[str, float] = None) -> AnimationClip:
    """行走循环：双腿反相正弦摆动 + 膝/踝联动 + 手臂反向摆动 + 身体弹跳与前移。"""
    rest = rest or SkeletonRig().t_pose_angles
    frames = max(4, int(round(duration * fps)))
    stride_units = stride / PX_PER_UNIT
    clip = _new_clip("walk", fps, frames, loop=True)
    for frame in range(0, frames, max(1, key_step)):
        t = frame / frames * TWO_PI
        lift = bounce / PX_PER_UNIT
        angles = _locomotion(rest, t, leg_amp=30.0 * speed, knee_amp=45.0,
                             arm_amp=arm_swing, lean=3.0)
        offset = (frame / frames * stride_units, lift * math.cos(2.0 * t))
        _add(clip, frame, angles, offset, CLAMPED)
    _close_loop(clip)
    return clip


def generate_run(fps: int = 24, duration: float = 0.8, stride: float = 120.0,
                 speed: float = 1.0, arm_swing: float = 45.0, air_height: float = 18.0,
                 key_step: int = 2, rest: Dict[str, float] = None) -> AnimationClip:
    """奔跑循环：更大的摆幅、腾空相位（双脚同时离地）与身体前倾。"""
    rest = rest or SkeletonRig().t_pose_angles
    frames = max(4, int(round(duration * fps)))
    stride_units = stride / PX_PER_UNIT
    air = air_height / PX_PER_UNIT
    clip = _new_clip("run", fps, frames, loop=True)
    for frame in range(0, frames, max(1, key_step)):
        t = frame / frames * TWO_PI
        angles = _locomotion(rest, t, leg_amp=48.0 * speed, knee_amp=80.0,
                             arm_amp=arm_swing, lean=12.0, knee_bias=25.0)
        offset = (frame / frames * stride_units, air * (0.4 + 0.6 * abs(math.sin(2.0 * t))))
        _add(clip, frame, angles, offset, CLAMPED)
    _close_loop(clip)
    return clip


def _locomotion(rest, t, leg_amp, knee_amp, arm_amp, lean, knee_bias=0.0) -> Dict[str, float]:
    """行走/奔跑共用的相位函数，t 为 0..2π 的循环相位。"""
    angles = dict(rest)
    swing_l = leg_amp * math.sin(t)
    swing_r = leg_amp * math.sin(t + math.pi)
    knee_l = -(knee_amp * max(0.0, math.sin(t)) + knee_bias * max(0.0, -math.sin(t)))
    knee_r = -(knee_amp * max(0.0, -math.sin(t)) + knee_bias * max(0.0, math.sin(t)))
    angles["L_leg"] = rest["L_leg"] + swing_l
    angles["L_calf"] = knee_l
    angles["L_foot"] = _flat_foot(rest["L_leg"] + swing_l, knee_l)
    angles["R_leg"] = rest["R_leg"] + swing_r
    angles["R_calf"] = knee_r
    angles["R_foot"] = _flat_foot(rest["R_leg"] + swing_r, knee_r)
    # 手臂与同侧腿反相
    angles["L_arm"] = rest["L_arm"] - arm_amp * math.sin(t)
    angles["R_arm"] = rest["R_arm"] + arm_amp * math.sin(t)
    angles["L_forearm"] = rest["L_forearm"] - arm_amp * 0.35 * max(0.0, -math.sin(t))
    angles["R_forearm"] = rest["R_forearm"] + arm_amp * 0.35 * max(0.0, -math.sin(t))
    angles["spine"] = rest["spine"] - lean
    angles["head"] = rest["head"] + lean * 0.6
    return angles


# --------------------------------------------------------------------------- wave
def generate_wave(fps: int = 24, duration: float = 2.0, amplitude: float = 45.0,
                  frequency: float = 2.0, hand: str = "right", key_step: int = 2,
                  rest: Dict[str, float] = None) -> AnimationClip:
    """挥手：抬臂 + 前臂/手掌按频率往复摆动。"""
    rest = rest or SkeletonRig().t_pose_angles
    frames = max(4, int(round(duration * fps)))
    right = str(hand).lower().startswith("r")
    arm, forearm, hand_bone = ("R_arm", "R_forearm", "R_hand") if right else ("L_arm", "L_forearm", "L_hand")
    other = ("L_arm", "L_forearm", "L_hand") if right else ("R_arm", "R_forearm", "R_hand")
    clip = _new_clip("wave", fps, frames, loop=False)
    for frame in range(0, frames, max(1, key_step)):
        t = frame / float(fps)
        # 起手与收手用正弦包络，避免首尾突变
        envelope = math.sin(min(1.0, max(0.0, t / duration)) * math.pi) ** 0.35
        raise_amt = 60.0 * envelope
        wave_amt = amplitude * envelope * math.sin(TWO_PI * frequency * t)
        angles = dict(rest)
        sign = 1.0 if right else -1.0
        angles[arm] = rest[arm] + sign * raise_amt
        angles[forearm] = rest[forearm] + sign * (25.0 + wave_amt)
        angles[hand_bone] = rest[hand_bone] + sign * wave_amt * 0.5
        angles[other[0]] = rest[other[0]] - sign * 6.0 * envelope
        angles["spine"] = rest["spine"] - 2.0 * envelope
        angles["head"] = rest["head"] + 2.0 * envelope
        _add(clip, frame, angles, (0.0, 0.015 * envelope * math.sin(t * 3.0)), CLAMPED)
    # 回到静止姿态
    _add(clip, frames, dict(rest), (0.0, 0.0), CLAMPED)
    clip.duration_frames = frames
    return clip


# --------------------------------------------------------------------------- jump
def generate_jump(fps: int = 24, duration: float = 1.4, power: float = 1.0,
                  crouch: float = 1.0, key_step: int = 2,
                  rest: Dict[str, float] = None) -> AnimationClip:
    """跳跃：预备下蹲 → 蹬地起跳 → 腾空收腿 → 落地缓冲 → 恢复。"""
    rest = rest or SkeletonRig().t_pose_angles
    frames = max(8, int(round(duration * fps)))
    clip = _new_clip("jump", fps, frames, loop=False)

    def pose(squat: float, airborne: float, lean: float, arm_up: float) -> Dict[str, float]:
        a = dict(rest)
        knee = 90.0 * squat * crouch + 35.0 * airborne
        a["L_leg"] = rest["L_leg"] - 18.0 * squat + 22.0 * airborne
        a["R_leg"] = rest["R_leg"] - 18.0 * squat + 22.0 * airborne
        a["L_calf"] = -knee
        a["R_calf"] = -knee
        a["L_foot"] = _flat_foot(rest["L_leg"] - 18.0 * squat + 22.0 * airborne, -knee)
        a["R_foot"] = _flat_foot(rest["R_leg"] - 18.0 * squat + 22.0 * airborne, -knee)
        a["L_arm"] = rest["L_arm"] - arm_up
        a["R_arm"] = rest["R_arm"] + arm_up
        a["L_forearm"] = rest["L_forearm"] - arm_up * 0.5
        a["R_forearm"] = rest["R_forearm"] + arm_up * 0.5
        a["spine"] = rest["spine"] - lean
        a["head"] = rest["head"] + lean * 0.5
        return a

    # 相位：0..0.2 下蹲，0.2..0.3 蹬地伸展，0.3..0.7 腾空，0.7..0.85 落地缓冲，0.85..1 恢复
    def phase(frame: int):
        u = frame / frames
        if u < 0.2:
            s = u / 0.2
            return pose(0.75 * s, 0.0, 8.0 * s, 18.0 * s), (0.0, 0.0)
        if u < 0.3:
            s = (u - 0.2) / 0.1
            return pose(0.75 * (1.0 - s), 0.35 * s, 2.0, 55.0 + 25.0 * s), (0.0, 0.02 * power * s)
        if u < 0.7:
            s = (u - 0.3) / 0.4
            height = power * 0.55 * (1.0 - (2.0 * s - 1.0) ** 2)
            return pose(0.0, 0.9 - 0.4 * abs(2.0 * s - 1.0), 0.0, 85.0), (0.0, height)
        if u < 0.85:
            s = (u - 0.7) / 0.15
            return pose(0.6 * s, 0.0, 10.0 * s, 60.0 - 20.0 * s), (0.0, 0.02 * power * (1.0 - s))
        s = (u - 0.85) / 0.15
        return pose(0.6 * (1.0 - s), 0.0, 10.0 * (1.0 - s), 40.0 * (1.0 - s)), (0.0, 0.0)

    for frame in range(0, frames + 1, max(1, key_step)):
        angles, offset = phase(min(frame, frames))
        _add(clip, min(frame, frames), angles, offset, EASE_IN_OUT)
    _add(clip, frames, dict(rest), (0.0, 0.0), EASE_IN_OUT)
    return clip


def _close_loop(clip: AnimationClip) -> None:
    """循环动画：把首帧姿态补到尾部，保证 Synfig 里循环闭合。"""
    if not clip.keyframes:
        return
    first = clip.keyframes[0]
    last_frame = clip.duration_frames
    if clip.keyframes[-1].frame != last_frame:
        clip.keyframes.append(
            Keyframe(frame=last_frame, bone_angles=dict(first.bone_angles),
                     bone_positions=dict(first.bone_positions),
                     interpolation=first.interpolation, source=first.source)
        )


TEMPLATES: List[ActionTemplate] = [
    ActionTemplate(
        key="idle", label="待机呼吸", loop=True, default_duration=2.0,
        hint="角色原地呼吸待机，适合做默认循环。",
        generate=generate_idle,
        params=[
            ParamDef("amplitude", "呼吸幅度", 0.0, 1.0, 0.4, 0.05),
            ParamDef("frequency", "呼吸频率", 0.2, 1.5, 0.5, 0.05, "Hz"),
        ],
    ),
    ActionTemplate(
        key="walk", label="行走循环", loop=True, default_duration=1.0,
        hint="参数化行走循环，一个循环内前移 stride 距离。",
        generate=generate_walk,
        params=[
            ParamDef("stride", "步幅", 30.0, 140.0, 80.0, 1.0, "px"),
            ParamDef("speed", "速度", 0.7, 1.4, 1.0, 0.05,
                     choices={"slow": 0.7, "normal": 1.0, "fast": 1.4}),
            ParamDef("arm_swing", "手臂摆动", 0.0, 60.0, 30.0, 1.0, "°"),
            ParamDef("bounce", "身体弹动", 0.0, 20.0, 5.0, 0.5, "px"),
        ],
    ),
    ActionTemplate(
        key="run", label="奔跑循环", loop=True, default_duration=0.8,
        hint="带腾空相位的奔跑循环。",
        generate=generate_run,
        params=[
            ParamDef("stride", "步幅", 60.0, 200.0, 120.0, 1.0, "px"),
            ParamDef("speed", "速度", 0.7, 1.6, 1.0, 0.05,
                     choices={"slow": 0.7, "normal": 1.0, "fast": 1.4}),
            ParamDef("arm_swing", "手臂摆动", 10.0, 80.0, 45.0, 1.0, "°"),
            ParamDef("air_height", "腾空高度", 0.0, 40.0, 18.0, 1.0, "px"),
        ],
    ),
    ActionTemplate(
        key="wave", label="挥手", loop=False, default_duration=2.0,
        hint="抬起一只手来回挥手。",
        generate=generate_wave,
        params=[
            ParamDef("amplitude", "挥手幅度", 10.0, 90.0, 45.0, 1.0, "°"),
            ParamDef("frequency", "挥手频率", 0.5, 4.0, 2.0, 0.1, "Hz"),
            ParamDef("hand", "挥哪只手", 0.0, 1.0, "right", 1.0,
                     choices={"left": "left", "right": "right"}),
        ],
    ),
    ActionTemplate(
        key="jump", label="跳跃", loop=False, default_duration=1.4,
        hint="下蹲预备 → 起跳 → 腾空 → 落地缓冲。",
        generate=generate_jump,
        params=[
            ParamDef("power", "起跳力度", 0.3, 2.0, 1.0, 0.05),
            ParamDef("crouch", "下蹲深度", 0.3, 1.6, 1.0, 0.05),
        ],
    ),
]

TEMPLATE_INDEX: Dict[str, ActionTemplate] = {t.key: t for t in TEMPLATES}


def get_template(key: str) -> ActionTemplate:
    if key not in TEMPLATE_INDEX:
        raise KeyError(f"未知动作模板：{key}，可选：{', '.join(TEMPLATE_INDEX)}")
    return TEMPLATE_INDEX[key]


def build(key: str, fps: int = 24, duration: float = None, key_step: int = 2,
          raw_params: Dict[str, float] = None, rig: SkeletonRig = None) -> AnimationClip:
    """按模板 key + 参数字典生成动画片段（GUI/CLI 共用入口）。"""
    template = get_template(key)
    rig = rig or SkeletonRig()
    kwargs: Dict[str, float] = {p.name: p.resolve(p.default) for p in template.params}
    for name, value in (raw_params or {}).items():
        kwargs[name] = template.param(name).resolve(value)
    clip = template.generate(
        fps=fps,
        duration=duration if duration is not None else template.default_duration,
        key_step=key_step,
        rest=rig.t_pose_angles,
        **kwargs,
    )
    clip.metadata.setdefault("template", key)
    clip.metadata.setdefault("params", dict(kwargs))
    return clip
