# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""Synfig ``.sif``（XML）读写。

对应设计文档 2.2 的“文件驱动”方案：读 .sif -> 操作内部模型 -> 写回 .sif -> 交给
Synfig CLI 渲染。

导出的火柴人绑定结构
--------------------
Synfig 中 **同一个 canvas 里，变换层作用于它前面（XML 中较早）的兄弟层**，
因此每根骨骼导出成一个 group，内部先放“子骨骼 group + 本骨骼美术部件”，
再放一个 rotate 层：

.. code-block:: xml

    <layer type="group" desc="AC_bone_L_leg">
      <param name="canvas"><canvas>
        <layer type="group" desc="AC_bone_L_calf">...</layer>
        <layer type="rectangle" desc="AC_art_L_leg">...</layer>
        <layer type="rotate" desc="AC_rot_L_leg">
          <param name="origin">关节静止世界坐标</param>
          <param name="amount"><animated type="angle">…waypoint…</param>
        </layer>
      </canvas></param>
    </layer>

``amount`` = 当前本地角度 - 静止本地角度，第 0 帧为 0，美术部件按静止姿态坐标绘制；
子 group 被父层旋带，天然形成 FK 层级。根骨骼位移用一个 translate 层
（其 ``origin`` 参数即位移量）表达。

本工具生成的文件会在 canvas 上写一份 ``<meta name="agentcartoon" content="json"/>``
保存完整模型，用于精确回读；没有该块时退化为结构化解析（可读 Synfig 原生
``<bones>`` / ``skeleton`` 层的骨骼定义与角度 waypoint）。

已知限制：Synfig 原生 ``<bones>`` 段里 ``<bone>`` 元素的 ``type`` 取值字符串
尚未在 1.4.5 上试通，因此写侧暂不产出 skeleton 层（读侧支持）。
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from lxml import etree

from .model import (AnimationClip, CLAMPED, Keyframe, Point, SkeletonRig,
                    SYNFIG_INTERPOLATION)

META_KEY = "agentcartoon"


# --------------------------------------------------------------- 值节点写入
def _f(value: float) -> str:
    return f"{float(value):.10f}"


def _param(parent: etree._Element, name: str) -> etree._Element:
    return etree.SubElement(parent, "param", {"name": name})


def _real(layer: etree._Element, name: str, value: float) -> None:
    etree.SubElement(_param(layer, name), "real", {"value": _f(value)})


def _integer(layer: etree._Element, name: str, value: int) -> None:
    etree.SubElement(_param(layer, name), "integer", {"value": str(int(value))})


def _bool(layer: etree._Element, name: str, value: bool) -> None:
    etree.SubElement(_param(layer, name), "bool", {"value": "true" if value else "false"})


def _vector(layer: etree._Element, name: str, point: Point) -> None:
    vec = etree.SubElement(_param(layer, name), "vector")
    etree.SubElement(vec, "x").text = _f(point[0])
    etree.SubElement(vec, "y").text = _f(point[1])


def _color(layer: etree._Element, name: str, rgba: Tuple[float, float, float, float]) -> None:
    color = etree.SubElement(_param(layer, name), "color")
    for tag, value in zip(("r", "g", "b", "a"), rgba):
        etree.SubElement(color, tag).text = f"{float(value):.6f}"


def _angle_animated(layer: etree._Element, name: str, samples: List[Tuple[str, float]],
                    interpolation: str) -> None:
    """samples: [(时间码, 角度度数), ...]"""
    anim = etree.SubElement(_param(layer, name), "animated", {"type": "angle"})
    tween = _synfig_tween(interpolation)
    for time_code, degrees in samples:
        wpt = etree.SubElement(anim, "waypoint",
                               {"time": time_code, "before": tween, "after": tween})
        etree.SubElement(wpt, "angle", {"value": _f(degrees)})


def _vector_animated(layer: etree._Element, name: str, samples: List[Tuple[str, Point]],
                     interpolation: str) -> None:
    anim = etree.SubElement(_param(layer, name), "animated", {"type": "vector"})
    tween = _synfig_tween(interpolation)
    for time_code, point in samples:
        wpt = etree.SubElement(anim, "waypoint",
                               {"time": time_code, "before": tween, "after": tween})
        vec = etree.SubElement(wpt, "vector")
        etree.SubElement(vec, "x").text = _f(point[0])
        etree.SubElement(vec, "y").text = _f(point[1])


def _synfig_tween(interpolation: str) -> str:
    return SYNFIG_INTERPOLATION.get(interpolation, "clamped")


# ------------------------------------------------------------------- 规格
@dataclass
class CanvasSpec:
    width: int = 320
    height: int = 560
    view_box: Tuple[float, float, float, float] = (-2.0, 3.35, 2.0, -3.35)
    fps: int = 24
    name: str = "AgentCartoon"
    bgcolor: Tuple[float, float, float, float] = (0.16, 0.16, 0.18, 1.0)


@dataclass
class StickmanArt:
    limb_color: Tuple[float, float, float, float] = (0.92, 0.92, 0.95, 1.0)
    joint_color: Tuple[float, float, float, float] = (0.45, 0.72, 1.0, 1.0)
    thickness: float = 0.10
    head_radius: float = 0.36


@dataclass
class SifDocument:
    """一个 .sif 文件对应的内部文档：画布规格 + 骨骼绑定 + 动画片段。"""

    rig: SkeletonRig = field(default_factory=SkeletonRig)
    clip: AnimationClip = field(default_factory=AnimationClip)
    canvas: CanvasSpec = field(default_factory=CanvasSpec)
    art: StickmanArt = field(default_factory=StickmanArt)
    path: Optional[Path] = None
    warnings: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------ 写
    def build_xml(self) -> etree._ElementTree:
        spec, rig, clip = self.canvas, self.rig, self.clip
        keyframes = clip.sorted_keyframes()
        fps = clip.fps or spec.fps
        last_frame = max([clip.duration_frames] + [kf.frame for kf in keyframes] + [1])

        canvas = etree.Element("canvas", {
            "version": "1.0",
            "width": str(spec.width),
            "height": str(spec.height),
            "xres": "2834.645752",
            "yres": "2834.645752",
            "view-box": " ".join(_f(v) for v in spec.view_box),
            "antialias": "1",
            "fps": str(fps),
            "begin-time": "0f",
            "end-time": f"{last_frame + 1}f",
            "bgcolor": " ".join(f"{v:.6f}" for v in spec.bgcolor),
        })
        etree.SubElement(canvas, "name").text = spec.name
        etree.SubElement(canvas, "desc").text = f"AgentCartoon clip: {clip.name}"
        _meta(canvas, "grid_show", "0")
        _meta(canvas, "guide_show", "1")
        _meta(canvas, "onion_skin", "0")
        _meta(canvas, META_KEY, json.dumps(self.to_json_obj(), ensure_ascii=False,
                                           separators=(",", ":")))

        rest_world = rig.world_transforms(rig.rest_pose())
        self._bone_layer(canvas, rig.root_bone, rest_world, keyframes, fps)
        return etree.ElementTree(canvas)

    def _bone_layer(self, parent: etree._Element, bone_name: str, rest_world, keyframes,
                    fps: int) -> etree._Element:
        rig, clip = self.rig, self.clip
        bone = rig.bones[bone_name]
        group = etree.SubElement(parent, "layer", {
            "type": "group", "active": "true", "version": "0.2",
            "desc": f"AC_bone_{bone_name}"})
        _real(group, "z_depth", 0.0)
        _real(group, "amount", 1.0)
        _integer(group, "blend_method", 0)
        _vector(group, "origin", (0.0, 0.0))
        inner = etree.SubElement(_param(group, "canvas"), "canvas")

        for child in bone.children:
            self._bone_layer(inner, child, rest_world, keyframes, fps)
        self._draw_part(inner, bone_name, rest_world)
        self._draw_transform(inner, bone_name, rest_world, keyframes, fps)

        _bool(group, "children_lock", False)
        _real(group, "outline_grow", 0.0)
        _bool(group, "z_range", False)
        return group

    def _draw_part(self, inner: etree._Element, bone_name: str, rest_world) -> None:
        """按静止姿态坐标绘制本骨骼的美术部件（变换层会带动它们）。"""
        rig, art = self.rig, self.art
        origin, tip, world_angle = rest_world[bone_name]
        if bone_name == rig.root_bone:
            self._circle(inner, "AC_joint_root", origin, art.thickness * 1.1, art.joint_color)
            return
        if bone_name == "head":
            rad = math.radians(world_angle)
            center = (tip[0] - math.cos(rad) * art.head_radius * 0.8,
                      tip[1] - math.sin(rad) * art.head_radius * 0.8)
            self._circle(inner, "AC_art_head", center, art.head_radius, art.limb_color)
            return
        dx, dy = tip[0] - origin[0], tip[1] - origin[1]
        length = math.hypot(dx, dy) or 1e-6
        px, py = -dy / length, dx / length
        half = art.thickness * (0.7 if bone_name.endswith(("_hand", "hand", "_foot", "foot"))
                                else 1.0) / 2.0
        rect = etree.SubElement(inner, "layer", {
            "type": "rectangle", "active": "true", "version": "0.1",
            "desc": f"AC_art_{bone_name}"})
        _real(rect, "z_depth", 0.0)
        _real(rect, "amount", 1.0)
        _integer(rect, "blend_method", 0)
        _color(rect, "color", art.limb_color)
        _vector(rect, "point1", (origin[0] + px * half, origin[1] + py * half))
        _vector(rect, "point2", (tip[0] - px * half, tip[1] - py * half))
        _real(rect, "expand", 0.0)
        _bool(rect, "invert", False)

    def _circle(self, parent: etree._Element, desc: str, center: Point, radius: float,
                color) -> None:
        circle = etree.SubElement(parent, "layer", {
            "type": "circle", "active": "true", "version": "0.1", "desc": desc})
        _real(circle, "z_depth", 0.0)
        _real(circle, "amount", 1.0)
        _integer(circle, "blend_method", 0)
        _color(circle, "color", color)
        _vector(circle, "origin", center)
        _real(circle, "radius", radius)
        _bool(circle, "invert", False)

    def _draw_transform(self, inner: etree._Element, bone_name: str, rest_world,
                        keyframes, fps: int) -> None:
        rig, clip = self.rig, self.clip
        if bone_name == rig.root_bone:
            layer = etree.SubElement(inner, "layer", {
                "type": "translate", "active": "true", "version": "0.1",
                "desc": "AC_root_motion"})
            if not keyframes:
                _vector(layer, "origin", (0.0, 0.0))
                return
            samples = [(f"{kf.frame}f", kf.bone_positions.get("root", (0.0, 0.0)))
                       for kf in keyframes]
            _vector_animated(layer, "origin", samples, keyframes[0].interpolation)
            return

        rest_local = rig.t_pose_angles.get(bone_name, 0.0)
        layer = etree.SubElement(inner, "layer", {
            "type": "rotate", "active": "true", "version": "0.1",
            "desc": f"AC_rot_{bone_name}"})
        _vector(layer, "origin", rest_world[bone_name][0])
        if not keyframes:
            etree.SubElement(_param(layer, "amount"), "angle", {"value": _f(0.0)})
            return
        samples = [(f"{kf.frame}f",
                    kf.bone_angles.get(bone_name, rest_local) - rest_local)
                   for kf in keyframes]
        _angle_animated(layer, "amount", samples, keyframes[0].interpolation)

    def save(self, path: Path = None) -> Path:
        target = Path(path or self.path)
        tree = self.build_xml()
        etree.indent(tree.getroot(), space="  ")
        tree.write(str(target), xml_declaration=True, encoding="UTF-8", pretty_print=True)
        self.path = target
        return target

    # ------------------------------------------------------------------ 读
    @classmethod
    def load(cls, path) -> "SifDocument":
        target = Path(path)
        parser = etree.XMLParser(remove_blank_text=True, resolve_entities=False,
                                 huge_tree=False)
        root = etree.parse(str(target), parser).getroot()
        doc = cls()
        doc.path = target
        doc._read_canvas_spec(root)
        embedded = root.find(f"meta[@name='{META_KEY}']")
        if embedded is not None and (embedded.get("content") or "").startswith("{"):
            doc._read_embedded(json.loads(embedded.get("content")))
        else:
            doc._read_structural(root)
        doc._read_native_skeleton(root)
        return doc

    def _read_canvas_spec(self, root: etree._Element) -> None:
        spec = self.canvas
        spec.width = int(float(root.get("width", spec.width)))
        spec.height = int(float(root.get("height", spec.height)))
        spec.fps = int(float(_fps_text(root.get("fps", "24"))))
        parts = (root.get("view-box") or "").split()
        if len(parts) == 4:
            spec.view_box = tuple(float(p) for p in parts)  # type: ignore
        node = root.find("name")
        if node is not None and (node.text or "").strip():
            spec.name = node.text.strip()

    # ------------------------------------------------------ 内嵌模型回读
    def _read_embedded(self, blob: Dict) -> None:
        rig_obj = SkeletonRig.__new__(SkeletonRig)
        from .model import Bone

        rig_obj.bones = {}
        for name, data in blob["rig"]["bones"].items():
            rig_obj.bones[name] = Bone(
                name=name, parent=data["parent"], length=data["length"],
                origin=tuple(data["origin"]), angle=data["angle"],
                children=list(data["children"]), anchor=data["anchor"],
                width=data["width"], tipwidth=data["tipwidth"])
        rig_obj.name = blob["rig"]["name"]
        rig_obj.root_bone = blob["rig"]["root_bone"]
        rig_obj.root_position = tuple(blob["rig"]["root_position"])
        rig_obj.t_pose_angles = dict(blob["rig"]["t_pose_angles"])
        rig_obj.skin_weights = {k: dict(v) for k, v in blob["rig"]["skin_weights"].items()}
        self.rig = rig_obj

        clip_blob = blob["clip"]
        clip = AnimationClip(
            name=clip_blob["name"], fps=clip_blob["fps"],
            duration_frames=clip_blob["duration_frames"], loop=clip_blob["loop"],
            metadata=clip_blob.get("metadata", {}))
        for kf in clip_blob["keyframes"]:
            clip.keyframes.append(Keyframe(
                frame=kf["frame"],
                bone_angles={k: float(v) for k, v in kf["bone_angles"].items()},
                bone_positions={k: tuple(v) for k, v in kf["bone_positions"].items()},
                interpolation=kf.get("interpolation", CLAMPED),
                tension=kf.get("tension", 0.0), bias=kf.get("bias", 0.0),
                continuity=kf.get("continuity", 0.0), source=kf.get("source", "manual")))
        self.clip = clip
        if "canvas" in blob:
            for key, value in blob["canvas"].items():
                setattr(self.canvas, key, tuple(value) if isinstance(value, list) else value)
        if "art" in blob:
            for key, value in blob["art"].items():
                setattr(self.art, key, tuple(value) if isinstance(value, list) else value)

    # -------------------------------------------------- 结构化解析（无内嵌块）
    def _read_structural(self, root: etree._Element) -> None:
        groups = {}
        for layer in root.iter("layer"):
            desc = layer.get("desc") or ""
            if desc.startswith("AC_bone_"):
                groups[desc[len("AC_bone_"):]] = layer
        if not groups:
            self.warnings.append("文件中没有 AgentCartoon 骨骼层，仅解析原生 skeleton 数据")
            return

        from .model import Bone

        rig = SkeletonRig.__new__(SkeletonRig)
        rig.name = self.rig.name
        rig.bones = {}
        rig.t_pose_angles = {}
        rig.skin_weights = {}
        rig.root_bone = "root" if "root" in groups else sorted(groups)[0]
        rig.root_position = (0.0, 0.0)

        frames: Dict[int, Dict[str, float]] = {}
        positions: Dict[int, Point] = {}
        for bone_name, group in groups.items():
            inner = group.find("param[@name='canvas']/canvas")
            children = sorted(
                {c.get("desc")[len("AC_bone_"):] for c in inner.findall("layer")
                 if (c.get("desc") or "").startswith("AC_bone_")}) if inner is not None else []
            length = self._length_from_art(inner, bone_name)
            rig.bones[bone_name] = Bone(name=bone_name, parent=None, length=length,
                                        children=children, anchor=1.0)
            if inner is None:
                continue
            rot = inner.find("layer[@type='rotate']")
            if rot is not None:
                for time_code, value in _angle_waypoints(rot.find("param[@name='amount']")):
                    frame = int(round(_to_frames(time_code, self.canvas.fps)))
                    frames.setdefault(frame, {})[bone_name] = value
            translate = inner.find("layer[@type='translate']")
            if translate is not None:
                for time_code, point in _vector_waypoints(
                        translate.find("param[@name='origin']")):
                    frame = int(round(_to_frames(time_code, self.canvas.fps)))
                    positions[frame] = point

        for bone_name, data in rig.bones.items():
            for child in data.children:
                if child in rig.bones:
                    rig.bones[child].parent = bone_name
        rig.t_pose_angles = {name: 0.0 for name in rig.bones}
        self.rig = rig

        clip = AnimationClip(name=self.clip.name or "imported", fps=self.canvas.fps,
                             duration_frames=max(frames) if frames else 0)
        for frame in sorted(set(frames) | set(positions)):
            clip.keyframes.append(Keyframe(
                frame=frame, bone_angles=dict(frames.get(frame, {})),
                bone_positions={"root": positions.get(frame, (0.0, 0.0))},
                source="imported"))
        self.clip = clip

    def _length_from_art(self, inner: Optional[etree._Element], bone_name: str) -> float:
        if inner is None:
            return 0.0
        art = inner.find(f"layer[@desc='AC_art_{bone_name}']")
        if art is None:
            return 0.0
        if art.get("type") == "circle":
            radius = art.find("param[@name='radius']/real")
            return float(radius.get("value")) * 2.0 if radius is not None else 0.0
        p1 = _read_vector(art.find("param[@name='point1']"))
        p2 = _read_vector(art.find("param[@name='point2']"))
        if p1 and p2:
            return math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        return 0.0

    # ---------------------------------------------- Synfig 原生 skeleton 只读
    def _read_native_skeleton(self, root: etree._Element) -> None:
        bones: Dict[str, Dict] = {}
        for section in root.findall("bones"):
            for child in section:
                entry = _parse_native_bone(child)
                if entry:
                    bones[entry["name"]] = entry
        for layer in root.iter("layer"):
            if layer.get("type") != "skeleton":
                continue
            param = layer.find("param[@name='bones']")
            for child in param.iter() if param is not None else []:
                entry = _parse_native_bone(child)
                if entry and entry["name"] not in bones:
                    bones[entry["name"]] = entry
        if not bones:
            return
        self.warnings.append(
            f"读到 {len(bones)} 根原生 skeleton 骨骼"
            + ("（含角度关键帧）" if any(b["angle_waypoints"] for b in bones.values()) else ""))
        if not self.clip.keyframes:
            frames: Dict[int, Dict[str, float]] = {}
            for name, data in bones.items():
                for time_code, value in data["angle_waypoints"]:
                    frame = int(round(_to_frames(time_code, self.canvas.fps)))
                    frames.setdefault(frame, {})[name] = value
            if frames:
                clip = AnimationClip(name="skeleton", fps=self.canvas.fps,
                                     duration_frames=max(frames))
                for frame in sorted(frames):
                    clip.keyframes.append(Keyframe(frame=frame, bone_angles=frames[frame],
                                                   source="imported"))
                self.clip = clip

    # -------------------------------------------------------------- 内嵌数据
    def to_json_obj(self) -> Dict:
        return {
            "version": 1,
            "canvas": _asdict_tuples(self.canvas),
            "art": _asdict_tuples(self.art),
            "rig": {
                "name": self.rig.name,
                "root_bone": self.rig.root_bone,
                "root_position": list(self.rig.root_position),
                "t_pose_angles": self.rig.t_pose_angles,
                "skin_weights": self.rig.skin_weights,
                "bones": {
                    name: {
                        "parent": bone.parent, "length": bone.length,
                        "origin": list(bone.origin), "angle": bone.angle,
                        "children": list(bone.children), "anchor": bone.anchor,
                        "width": bone.width, "tipwidth": bone.tipwidth,
                    } for name, bone in self.rig.bones.items()
                },
            },
            "clip": {
                "name": self.clip.name, "fps": self.clip.fps,
                "duration_frames": self.clip.duration_frames, "loop": self.clip.loop,
                "metadata": self.clip.metadata,
                "keyframes": [
                    {
                        "frame": kf.frame, "bone_angles": kf.bone_angles,
                        "bone_positions": {k: list(v) for k, v in kf.bone_positions.items()},
                        "interpolation": kf.interpolation, "tension": kf.tension,
                        "bias": kf.bias, "continuity": kf.continuity, "source": kf.source,
                    } for kf in self.clip.sorted_keyframes()
                ],
            },
        }


def _asdict_tuples(obj) -> Dict:
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(obj).items()}


def _meta(canvas: etree._Element, name: str, content: str) -> None:
    etree.SubElement(canvas, "meta", {"name": name, "content": content})


# --------------------------------------------------------------- 值节点读取
def _fps_text(value) -> str:
    kept = "".join(ch for ch in str(value) if ch.isdigit() or ch == ".")
    return kept or "24"


def _to_frames(code, fps: int) -> float:
    text = str(code).strip()
    if text.endswith("f"):
        return float(text[:-1] or 0)
    if text.endswith("s"):
        return float(text[:-1] or 0) * fps
    return float(text or 0) * fps


def _read_vector(param: Optional[etree._Element]) -> Optional[Point]:
    if param is None:
        return None
    vec = param if param.tag == "vector" else param.find("vector")
    if vec is None:
        return None
    x, y = vec.find("x"), vec.find("y")
    if x is None or y is None:
        return (float(vec.get("x", 0.0)), float(vec.get("y", 0.0)))
    return (float(x.text or 0.0), float(y.text or 0.0))


def _angle_waypoints(param: Optional[etree._Element]) -> List[Tuple[str, float]]:
    if param is None:
        return []
    anim = param.find("animated")
    out: List[Tuple[str, float]] = []
    for wpt in (anim if anim is not None else param).findall("waypoint"):
        angle = wpt.find("angle")
        if angle is not None:
            out.append((wpt.get("time", "0f"), float(angle.get("value", 0.0))))
    if anim is None:
        angle = param.find("angle")
        if angle is not None:
            out.append(("0f", float(angle.get("value", 0.0))))
    return out


def _vector_waypoints(param: Optional[etree._Element]) -> List[Tuple[str, Point]]:
    if param is None:
        return []
    anim = param.find("animated")
    out: List[Tuple[str, Point]] = []
    for wpt in (anim if anim is not None else param).findall("waypoint"):
        point = _read_vector(wpt.find("vector"))
        if point is not None:
            out.append((wpt.get("time", "0f"), point))
    return out


def _parse_native_bone(element: etree._Element) -> Optional[Dict]:
    """解析 Synfig ``<bones>`` 段 / skeleton 层 bones 参数里的骨骼元素。"""
    if element.tag not in ("bone", "bone_valuenode", "value_node"):
        return None
    name_node = element.find("param[@name='name']/string")
    name = name_node.get("value") if name_node is not None else None
    if not name:
        return None
    origin = _read_vector(element.find("param[@name='origin']")) or (0.0, 0.0)
    length_node = element.find("param[@name='length']/real")
    width_node = element.find("param[@name='width']/real")
    tipwidth_node = element.find("param[@name='tipwidth']/real")
    angle_node = element.find("param[@name='angle']/angle")
    parent_node = element.find("param[@name='parent']")
    parent = element.get("parent")
    if parent_node is not None:
        inner = parent_node.find("param[@name='name']/string")
        if inner is not None:
            parent = inner.get("value")
    return {
        "name": name,
        "parent": parent,
        "origin": origin,
        "length": float(length_node.get("value")) if length_node is not None else 0.0,
        "width": float(width_node.get("value")) if width_node is not None else 0.0,
        "tipwidth": float(tipwidth_node.get("value")) if tipwidth_node is not None else 0.0,
        "angle": float(angle_node.get("value")) if angle_node is not None else 0.0,
        "angle_waypoints": _angle_waypoints(element.find("param[@name='angle']")),
    }
