"""Synfig 命令行渲染封装（设计文档第七章 synfig-cli 集成）。

只依赖本机能找到的 ``synfig`` 可执行文件；找不到时抛出 :class:`RenderUnavailable`
让上层（GUI/CLI）降级为"只导出 .sif"。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

DEFAULT_ANTIALIAS = 4
TIMEOUT_SECONDS = 120.0


class RenderUnavailable(RuntimeError):
    """本机没有 synfig 命令行，或渲染进程失败。"""


def find_synfig(explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        path = shutil.which(explicit) or (explicit if Path(explicit).is_file() else None)
        return path
    return shutil.which("synfig")


@dataclass
class Renderer:
    """单帧 / 序列帧渲染器。时间码使用 Synfig 语法：``0s``、``12f``、``1.5s``。"""

    binary: Optional[str] = field(default_factory=lambda: find_synfig())
    antialias: int = DEFAULT_ANTIALIAS
    threads: int = 0
    timeout: float = TIMEOUT_SECONDS

    def available(self) -> bool:
        return self.binary is not None

    def render(self, sif_path: Path, out_path: Path, time_code: str = "0f",
               target: str = "png", size: Sequence[int] = None) -> Path:
        if not self.available():
            raise RenderUnavailable("未找到 synfig 命令行，请安装 Synfig Studio 或设置 AC_SYNFIG_BIN")
        sif_path, out_path = Path(sif_path), Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        command = [self.binary, "-q", "-i", str(sif_path), "-o", str(out_path),
                   "-t", target, "--time", time_code, "-a", str(self.antialias)]
        if size:
            command += ["-s", f"{int(size[0])}x{int(size[1])}"]
        if self.threads:
            command += ["-j", str(self.threads)]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise RenderUnavailable(f"synfig 渲染超时（{self.timeout}s）") from exc
        if proc.returncode != 0 or not out_path.exists():
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RenderUnavailable(f"synfig 渲染失败（退出码 {proc.returncode}）：{detail[:400]}")
        return out_path

    def render_frame(self, sif_path: Path, frame: int, out_dir: Path,
                     fps: int = 24, target: str = "png") -> Path:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        return self.render(sif_path, out_dir / f"frame_{frame:04d}.{target}",
                           time_code=f"{frame}f", target=target)

    def render_sequence(self, sif_path: Path, out_dir: Path, frames: int,
                        fps: int = 24, start: int = 0, target: str = "png") -> List[Path]:
        return [self.render_frame(sif_path, i, out_dir, fps, target)
                for i in range(start, start + frames)]
