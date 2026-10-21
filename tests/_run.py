# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""零依赖测试跑法：``.venv/bin/python tests/_run.py``（装了 pytest 也可直接 ``pytest tests/``）。

测试函数一律无参数、用 assert，所以两种跑法都成立。这些用例不碰 GUI、不联网、
不需要 Synfig，秒级跑完；需要 Synfig 的端到端验证在 tests/smoke.sh。
"""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
for path in (HERE.parent, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def run(names=None) -> int:
    files = sorted(HERE.glob("test_*.py"))
    if names:
        files = [f for f in files if f.stem in set(names)]
    failed = 0
    for path in files:
        module = importlib.import_module(path.stem)
        tests = [n for n in dir(module) if n.startswith("test_")]
        print(f"[{path.stem}] {len(tests)} 项")
        for name in sorted(tests):
            try:
                getattr(module, name)()
                print(f"  \033[32mPASS\033[0m {name}")
            except Exception:
                failed += 1
                print(f"  \033[31mFAIL\033[0m {name}")
                print("    " + "\n    ".join(traceback.format_exc().strip().splitlines()[-4:]))
    print(f"\n================  {'全部通过' if not failed else str(failed) + ' 项失败'}  ================")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
