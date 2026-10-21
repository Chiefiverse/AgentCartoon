# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon — 本文件以 AGPL-3.0-only 发布；闭源商用请获取商业许可证，详见 LICENSE。

"""``python -m agentcartoon`` —— 等价于 ``python -m agentcartoon.cli``。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
