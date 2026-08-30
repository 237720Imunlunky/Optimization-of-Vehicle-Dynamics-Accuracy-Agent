#!/usr/bin/env bash
set -euo pipefail

# Ubuntu安装入口：只安装Python依赖和本项目，不安装商业CarSim。
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
INSTALL_ROOT="${INSTALL_ROOT:-$PROJECT_ROOT/.venv}"
RUNTIME_ROOT="${RUNTIME_ROOT:-/tmp/VehicleDynamicsAgent/Runtime}"
PYTHON_COMMAND="${PYTHON_COMMAND:-python3}"

usage() {
  echo "用法：$0 [--install-root 路径] [--runtime-root 路径] [--python python3]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-root) INSTALL_ROOT="$2"; shift 2 ;;
    --runtime-root) RUNTIME_ROOT="$2"; shift 2 ;;
    --python) PYTHON_COMMAND="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage; exit 2 ;;
  esac
done

command -v "$PYTHON_COMMAND" >/dev/null || { echo "未找到 $PYTHON_COMMAND，请安装 Python 3.10+ 和 python3-venv。" >&2; exit 1; }
"$PYTHON_COMMAND" - <<'PY'
import platform, sys
if sys.version_info < (3, 10):
    raise SystemExit(f"需要Python 3.10+，当前为{platform.python_version()}")
if sys.maxsize <= 2**32:
    raise SystemExit("需要64位Python")
print(f"Python检查通过：{platform.python_version()} {platform.machine()}")
PY

mkdir -p "$INSTALL_ROOT" "$RUNTIME_ROOT"
if [[ ! -x "$INSTALL_ROOT/bin/python" ]]; then
  "$PYTHON_COMMAND" -m venv "$INSTALL_ROOT"
fi
VENV_PYTHON="$INSTALL_ROOT/bin/python"
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt"

export VEHICLE_AGENT_RUNTIME_CONFIG="$PROJECT_ROOT/config/runtime.local.json"
"$VENV_PYTHON" - <<PY
import json
from pathlib import Path
root = Path(r"$PROJECT_ROOT")
config = {
    "carsim_root": "",
    "runtime_root": r"$RUNTIME_ROOT",
    "data_root": "local_assets/data",
    "output_root": "输出",
    "converter_path": "tools/convert_carsim_vsb.py",
    "blf_dependencies": "tools",
    "model_template_path": "local_assets/vehicle_template/Run_all.par",
    "formal_result_path": "demo_assets/formal_acceptance.demo.json",
    "install_root": r"$INSTALL_ROOT",
}
path = root / "config" / "runtime.local.json"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
PY
"$VENV_PYTHON" "$SCRIPT_DIR/health_check.py"
echo "Ubuntu安装完成。运行部署包/start_ubuntu.sh启动界面。"
