#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
INSTALL_ROOT="${INSTALL_ROOT:-$PROJECT_ROOT/.venv}"
VENV_PYTHON="$INSTALL_ROOT/bin/python"
[[ -x "$VENV_PYTHON" ]] || { echo "请先运行 install_ubuntu.sh" >&2; exit 1; }
"$VENV_PYTHON" "$SCRIPT_DIR/health_check.py"
"$VENV_PYTHON" -m llm_optimizer.run_agent --dry-run --output "$PROJECT_ROOT/输出/部署验收/ubuntu_dry_run"
echo "Ubuntu演示/干运行验收通过。完整CarSim闭环需Windows CarSim或远程Windows求解服务。"
