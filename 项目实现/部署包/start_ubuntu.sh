#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
INSTALL_ROOT="${INSTALL_ROOT:-$PROJECT_ROOT/.venv}"
PORT="${PORT:-8765}"
VENV_PYTHON="$INSTALL_ROOT/bin/python"
[[ -x "$VENV_PYTHON" ]] || { echo "未找到虚拟环境，请先运行 install_ubuntu.sh" >&2; exit 1; }
exec "$VENV_PYTHON" "$PROJECT_ROOT/Agent交互界面/server.py" --host 127.0.0.1 --port "$PORT"
