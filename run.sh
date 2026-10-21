#!/usr/bin/env bash
# AgentCartoon 启动脚本：
#   ./run.sh                 # 启动 GUI
#   ./run.sh cli --action walk --out walk.sif
#   ./run.sh setup           # 只装依赖
set -euo pipefail

cd "$(dirname "$0")"
VENV=".venv"
PY="$VENV/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "==> 创建虚拟环境 $VENV"
  python3 -m venv "$VENV"
fi

if [[ ! -f "$VENV/.deps-ok" ]] || ! "$PY" -c "import PyQt6, lxml" >/dev/null 2>&1; then
  echo "==> 安装依赖"
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -r requirements.txt
  touch "$VENV/.deps-ok"
fi

case "${1:-gui}" in
  setup) echo "==> 依赖就绪" ;;
  cli)   shift; exec "$PY" -m agentcartoon.cli "$@" ;;
  gui)   exec "$PY" -m agentcartoon.gui ;;
  *)     echo "用法: ./run.sh [gui|cli|setup] [参数]" >&2; exit 2 ;;
esac
