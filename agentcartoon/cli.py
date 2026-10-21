"""AgentCartoon 命令行入口（设计文档 §13 的 Hello World）。

    python -m agentcartoon.cli --template stickman --action walk \\
        --params "stride=60,speed=normal" --duration 3s --out walk_demo.sif
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict

from .core import actions, model, render, sif


def parse_duration(text: str) -> float:
    """``3s`` / ``3`` / ``12f``(需配合 --fps 之外的场景不常见) -> 秒。"""
    text = text.strip().lower()
    if text.endswith("s"):
        return float(text[:-1])
    if text.endswith("f"):
        raise ValueError("--duration 请给秒数（如 3s），帧数用 --key-step/--fps 控制")
    return float(text)


def parse_params(text: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for chunk in filter(None, (part.strip() for part in (text or "").split(","))):
        if "=" not in chunk:
            raise ValueError(f"参数应为 k=v 形式，收到：{chunk}")
        key, _, value = chunk.partition("=")
        result[key.strip()] = value.strip()
    return result


def list_actions() -> int:
    for template in actions.TEMPLATES:
        loop = "循环" if template.loop else "单次"
        print(f"{template.key:<6} {template.label:<6} [{loop}] {template.hint}")
        for p in template.params:
            choices = (" 可选：" + "/".join(p.choices)) if p.choices else (
                " 范围：%.3g..%.3g" % (p.minimum, p.maximum))
            print(f"       {p.name:<10} {p.label:<6} 默认 {p.default:g}{p.unit}{choices}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentcartoon.cli",
        description="生成火柴人动画的 Synfig .sif 文件",
    )
    parser.add_argument("--template", default="stickman", help="角色模板，目前仅 stickman")
    parser.add_argument("--action", help="动作模板 key，见 --list-actions")
    parser.add_argument("--params", default="", help='模板参数，如 "stride=60,speed=normal"')
    parser.add_argument("--duration", default=None, help="片段时长（秒），如 3s；默认用模板时长")
    parser.add_argument("--fps", type=int, default=24, help="帧率，默认 24")
    parser.add_argument("--key-step", type=int, default=2, help="每隔多少帧打一个关键帧，默认 2")
    parser.add_argument("--out", default=None, help="输出 .sif 路径")
    parser.add_argument("--render", nargs="?", const="", default=None, metavar="DIR",
                        help="渲染 PNG 序列到目录（默认 <out 名>_frames/）")
    parser.add_argument("--list-actions", action="store_true", help="列出所有动作模板与参数")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_actions:
        return list_actions()

    if args.template != "stickman":
        print(f"未知模板：{args.template}（目前仅支持 stickman）", file=sys.stderr)
        return 2
    if not args.action:
        print("缺少 --action，或用 --list-actions 查看可选动作", file=sys.stderr)
        return 2

    try:
        template = actions.get_template(args.action)
        raw_params = parse_params(args.params)
        for name in raw_params:
            template.param(name)
        duration = parse_duration(args.duration) if args.duration else None
    except (KeyError, ValueError) as exc:
        print(f"参数有误：{exc}", file=sys.stderr)
        return 2

    clip = actions.build(args.action, fps=args.fps, duration=duration,
                         key_step=args.key_step, raw_params=raw_params,
                         rig=model.SkeletonRig())
    out = Path(args.out or f"{args.action}_demo.sif")
    sif.SifDocument(rig=model.SkeletonRig(), clip=clip).save(out)

    seconds = clip.duration_frames / float(args.fps)
    print(f"已写出 {out}：{args.action}，{len(clip.sorted_keyframes())} 个关键帧，"
          f"{clip.duration_frames} 帧 / {seconds:.2f}s @ {args.fps}fps，"
          f"闭合误差 {clip.closure_error():.2f}°")
    print("可直接用 Synfig Studio 打开该文件继续编辑。")

    if args.render is not None:
        renderer = render.Renderer()
        if not renderer.available():
            print("未找到 synfig 命令行，跳过渲染（可设置 AC_SYNFIG_BIN 指定）", file=sys.stderr)
            return 1
        out_dir = Path(args.render) if args.render else out.with_suffix("").parent / f"{out.stem}_frames"
        print(f"渲染 {clip.duration_frames} 帧到 {out_dir} …")
        paths = renderer.render_sequence(out, out_dir, clip.duration_frames, fps=args.fps)
        print(f"完成，共 {len(paths)} 张 PNG。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
