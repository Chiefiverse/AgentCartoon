# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""core/tweak.py 关键帧抽取的离线用例。

核心承诺只有一条：**阈值就是误差上限**。抽取完的曲线在任意一帧上离原曲线不超过
``angle_threshold`` 度 / ``position_threshold`` 单位，且循环动画的闭合误差别坏。
其余用例都在守住这个前提之下检查"少插帧"的各个开关。
"""

from __future__ import annotations

from agentcartoon.core import actions, quality, sif, tweak
from agentcartoon.core.model import AnimationClip, Keyframe, SkeletonRig

TEMPLATES = ("idle", "walk", "run", "wave", "jump")
TOL = 1e-9


def _clip(template: str = "walk", key_step: int = 2):
    rig = SkeletonRig()
    return actions.build(rig=rig, key=template, key_step=key_step), rig


def _one_bone_clip(changed: dict, *, duration: int = 8, loop: bool = False):
    """只动一根骨的合成片段，用来把开关的效果看得干干净净。"""
    rig = SkeletonRig()
    rest = rig.t_pose_angles
    clip = AnimationClip(name="synthetic", fps=24, duration_frames=duration, loop=loop)
    for frame, value in changed.items():
        angles = dict(rest)
        angles["spine"] = float(value)
        clip.set_pose(rig, int(frame), angles)
    return clip, rig


# --------------------------------------------------------------------- 稠密采样
def test_dense_poses_covers_every_frame_and_endpoints():
    clip, rig = _clip("walk")
    samples = tweak.dense_poses(clip, rig, step=1)
    assert [s.frame for s in samples] == list(range(clip.duration_frames + 1))
    assert samples[0].angles == clip.pose_at(rig, 0)
    assert samples[-1].angles == clip.pose_at(rig, clip.duration_frames)


def test_dense_poses_step_aligns_tail():
    """step 不整除时长时，最后一帧也必须出现，否则循环处会缺一个点。"""
    clip, rig = _clip("run")
    frames = [s.frame for s in tweak.dense_poses(clip, rig, step=3)]
    assert frames[0] == 0 and frames[-1] == clip.duration_frames, frames
    assert frames == sorted(set(frames))


# ------------------------------------------------------------------ 误差上限
def test_threshold_is_a_real_error_bound_on_every_template():
    for key in TEMPLATES:
        clip, rig = _clip(key)
        for threshold in (2.0, 4.0, tweak.DEFAULT_ANGLE_THRESHOLD):
            out = tweak.extract(clip, rig, angle_threshold=threshold)
            angle, frame, bone = tweak.max_error(out, clip, rig)
            position, pframe = tweak.max_position_error(out, clip, rig)
            assert angle <= threshold + TOL, (key, threshold, angle, frame, bone)
            assert position <= tweak.DEFAULT_POSITION_THRESHOLD + TOL, (key, threshold, position, pframe)


def test_extrema_are_kept_and_threshold_can_drop_them():
    """峰值帧：阈值再大也得留着（extrema=True），关掉它才真的被抹平。"""
    clip, rig = _one_bone_clip({0: 0.0, 4: 40.0, 8: 0.0})
    kept = tweak.extract(clip, rig, angle_threshold=1e6, position_threshold=1e6,
                         extrema=True, keep_originals=False).frames()
    assert 4 in kept, kept
    assert tweak.extract(clip, rig, angle_threshold=1e6, position_threshold=1e6,
                         extrema=False, keep_originals=False).frames() == [0, 8]


def test_significant_extrema_ignores_flat_noise():
    """平顶上的数值抖动（< EXTREMA_MIN_SWING）不该被当成峰值。"""
    clip, rig = _one_bone_clip({0: 0.0, 2: 0.0, 4: 0.0, 6: 0.0, 8: 0.0})
    samples = tweak.dense_poses(clip, rig, 1)
    assert tweak.significant_extrema(samples) == set()
    clip, rig = _one_bone_clip({0: 0.0, 4: 40.0, 8: 0.0})
    samples = tweak.dense_poses(clip, rig, 1)
    assert 4 in tweak.significant_extrema(samples), samples[4].angles["spine"]


def test_threshold_zero_keeps_every_sampled_frame():
    clip, rig = _clip("walk")
    samples = tweak.dense_poses(clip, rig, 1)
    out = tweak.extract(clip, rig, angle_threshold=0.0)
    assert len(out.keyframes) == len(samples)


# ---------------------------------------------------------------------- 只减不涨
def test_extraction_never_exceeds_dense_sample_count():
    for key in TEMPLATES:
        clip, rig = _clip(key)
        dense = len(tweak.dense_poses(clip, rig, 1))
        out = tweak.extract(clip, rig)
        assert len(out.keyframes) <= dense, (key, len(out.keyframes), dense)


def test_loose_threshold_actually_reduces():
    """抽稀必须真的减得下来：模板里最密的 idle/wave 每 2 帧一个 K。"""
    reductions = {}
    for key in TEMPLATES:
        clip, rig = _clip(key)
        out = tweak.extract(clip, rig, angle_threshold=4.0)
        reductions[key] = (len(clip.keyframes), len(out.keyframes))
    assert reductions["idle"][1] < reductions["idle"][0], reductions
    assert reductions["wave"][1] < reductions["wave"][0], reductions
    # 快动作本来就该密，抽稀后不许比原来还多
    for key, (before, after) in reductions.items():
        assert after <= max(before, 14), (key, before, after)


def test_keep_originals_is_a_superset_and_only_adds():
    clip, rig = _clip("wave")
    lean = tweak.extract(clip, rig, angle_threshold=4.0, keep_originals=False)
    strict = tweak.extract(clip, rig, angle_threshold=4.0, keep_originals=True)
    assert set(clip.frames()) <= set(strict.frames())
    assert len(strict.keyframes) >= len(lean.keyframes)


# --------------------------------------------------------------------- 循环承诺
def test_closure_error_stays_zero():
    for key in TEMPLATES:
        clip, rig = _clip(key)
        if not clip.loop:
            continue
        for threshold in (0.5, 4.0, 60.0):
            out = tweak.extract(clip, rig, angle_threshold=threshold)
            assert out.closure_error() <= 1e-9, (key, threshold, out.closure_error())
            assert out.frames()[-1] == clip.duration_frames, (key, out.frames())


def test_non_looping_clip_gets_no_tail_duplicate():
    clip, rig = _clip("jump")
    assert not clip.loop
    out = tweak.extract(clip, rig, angle_threshold=4.0)
    assert out.frames().count(out.frames()[-1]) == 1
    assert out.closure_error() == 0.0


# ------------------------------------------------------------------------ 元数据
def test_original_clip_is_not_mutated():
    clip, rig = _clip("walk")
    before = [(kf.frame, dict(kf.bone_angles), dict(kf.bone_positions), kf.source)
              for kf in clip.sorted_keyframes()]
    tweak.extract(clip, rig, angle_threshold=2.0)
    after = [(kf.frame, dict(kf.bone_angles), dict(kf.bone_positions), kf.source)
             for kf in clip.sorted_keyframes()]
    assert before == after


def test_metadata_records_the_recipe():
    clip, rig = _clip("walk")
    out = tweak.extract(clip, rig, angle_threshold=3.5, step=1, extrema=False)
    info = out.metadata["extracted_from"]
    assert info["angle_threshold"] == 3.5 and info["extrema"] is False
    assert info["source_keyframes"] == len(clip.keyframes)
    assert info["dense_samples"] == clip.duration_frames + 1
    assert out.metadata["template"] == "walk"  # 模板信息不能被抽取吃掉


def test_out_of_range_keyframes_are_counted_not_silently_dropped():
    clip, rig = _clip("walk")
    clip.keyframes.append(Keyframe(frame=clip.duration_frames + 10,
                                   bone_angles=dict(clip.pose_at(rig, 0)), source="manual"))
    out = tweak.extract(clip, rig, angle_threshold=4.0)
    assert out.metadata["extracted_from"]["out_of_range_keyframes"] == 1
    assert all(kf.frame <= out.duration_frames for kf in out.keyframes)


def test_source_labels_are_preserved_and_backfilled():
    """命中原始帧沿用它的 source，新插的帧取占多数的那个 source。"""
    clip, rig = _one_bone_clip({0: 0.0, 4: 40.0, 8: 0.0})
    clip.keyframes[1].source = "ai"          # frame 4 是人工/AI 改过的峰值
    out = tweak.extract(clip, rig, angle_threshold=1.0, keep_originals=True)
    labels = {kf.frame: kf.source for kf in out.keyframes}
    assert labels[4] == "ai", labels
    assert set(labels.values()) <= {"manual", "ai"}, labels  # 新帧拿多数派 source


# ---------------------------------------------------------------------- 跨模块
def test_extraction_does_not_create_quality_errors():
    """抽稀不许引入新问题：五项质检里 error 必须还是 0，越限与穿模也不许亮。"""
    for key in TEMPLATES:
        clip, rig = _clip(key)
        for threshold in (2.0, tweak.DEFAULT_ANGLE_THRESHOLD):
            out = tweak.extract(clip, rig, angle_threshold=threshold)
            issues = quality.check(out, rig)
            assert not [i for i in issues if i.level == "error"], (key, threshold, issues)
            assert not [i for i in issues if i.code in ("joint_over_limit", "penetration")], \
                (key, threshold, issues)


# ---------------------------------------------------------------------- 往返导出
def test_extracted_clip_roundtrips_through_sif(tmp_path=None):
    """抽取完必须还能导出 .sif 并精确回读——否则 Phase 1 打通的链路就断了。"""
    import tempfile
    from pathlib import Path

    rig = SkeletonRig()
    clip = actions.build(rig=rig, key="walk")
    out = tweak.extract(clip, rig, angle_threshold=4.0)
    directory = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    document = sif.SifDocument(rig=rig, clip=out)
    path = document.save(directory / "extracted.sif")

    back = sif.SifDocument.load(path)
    assert len(back.rig.bones) == 15
    assert back.clip.frames() == out.frames(), (back.clip.frames(), out.frames())
    assert back.clip.closure_error() <= 1e-6
    for frame in (0, out.frames()[len(out.frames()) // 2]):
        mine, theirs = out.pose_at(rig, frame), back.clip.pose_at(back.rig, frame)
        worst = max(abs(mine[name] - theirs.get(name, 0.0)) for name in mine)
        assert worst < 1e-6, (frame, worst)
