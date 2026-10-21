"""``python -m agentcartoon`` —— 等价于 ``python -m agentcartoon.cli``。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
