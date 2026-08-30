"""集中解析项目路径，支持本机配置覆盖并避免业务代码写死个人路径。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = PROJECT_ROOT.parents[1]
LOCAL_PATH_CONFIG = Path(
    os.environ.get("VEHICLE_AGENT_RUNTIME_CONFIG", PROJECT_ROOT / "config" / "runtime.local.json")
)
BUNDLED_CONVERTER = PROJECT_ROOT / "tools" / "convert_carsim_vsb.py"
LOCAL_ASSETS_ROOT = PROJECT_ROOT / "local_assets"
DEMO_FORMAL_RESULT = PROJECT_ROOT / "demo_assets" / "formal_acceptance.demo.json"


def _read_local_config() -> dict[str, Any]:
    """读取不提交版本库的本机路径配置；文件不存在时使用F盘默认值。"""
    if not LOCAL_PATH_CONFIG.exists():
        return {}
    try:
        # Windows PowerShell 5 的 Set-Content -Encoding utf8 会写入BOM，统一兼容两种UTF-8文件。
        payload = json.loads(LOCAL_PATH_CONFIG.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"本机路径配置JSON格式错误：{LOCAL_PATH_CONFIG}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError("runtime.local.json根节点必须是对象")
    return payload


def _resolve(value: str | Path, base: Path = PROJECT_ROOT) -> Path:
    """把相对路径按项目实现目录解析，绝对路径保持不变。"""
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _registry_carsim_locations() -> list[Path]:
    """从Windows卸载注册表读取CarSim安装位置；其他系统返回空列表。"""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    locations: list[Path] = []
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, key_name in roots:
        try:
            with winreg.OpenKey(hive, key_name) as root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, index)) as item:
                            display = str(winreg.QueryValueEx(item, "DisplayName")[0])
                            location = str(winreg.QueryValueEx(item, "InstallLocation")[0])
                        if "carsim" in display.lower() and location:
                            locations.append(Path(location))
                    except OSError:
                        continue
        except OSError:
            continue
    return locations


def discover_carsim_root(explicit: str | Path | None = None) -> Path:
    """按显式配置、环境变量、注册表和常见盘符顺序发现CarSim安装目录。"""
    if explicit:
        # 用户显式配置必须严格采用，路径错误时由体检报告指出，不能静默换到另一套CarSim。
        return _resolve(explicit)
    candidates: list[Path] = []
    if os.environ.get("CARSIM_ROOT"):
        candidates.append(_resolve(os.environ["CARSIM_ROOT"]))
    candidates.extend(_registry_carsim_locations())
    for drive in "CDEF":
        candidates.extend([
            Path(f"{drive}:/Carsim/Carsim2023/Carsim2023.2/install"),
            Path(f"{drive}:/CarSim/CarSim2023.2/install"),
            Path(f"{drive}:/Program Files/Mechanical Simulation/CarSim 2023.2"),
        ])
    for candidate in candidates:
        normalized = candidate.resolve()
        if (normalized / "Programs" / "VS_SolverWrapper_CLI_64.exe").exists():
            return normalized
    return candidates[0].resolve() if candidates else Path("C:/CarSim/CarSim2023.2/install")


def _default_runtime_root() -> Path:
    """Windows优先使用F盘；Ubuntu使用ASCII临时目录，避免CarSim控制文件编码问题。"""
    if os.name != "nt":
        return Path("/tmp/VehicleDynamicsAgent/Runtime")
    if Path("F:/").exists():
        return Path("F:/VehicleDynamicsAgent/Runtime")
    local_app_data = Path(os.environ.get("LOCALAPPDATA", "C:/VehicleDynamicsAgent"))
    return local_app_data / "VehicleDynamicsAgent" / "Runtime"


def load_runtime_paths() -> dict[str, Path]:
    """返回所有运行路径，业务模块只依赖这些稳定键名。"""
    local = _read_local_config()
    carsim_root = discover_carsim_root(local.get("carsim_root"))
    runtime_root = _resolve(local.get("runtime_root", _default_runtime_root()))
    data_root = _resolve(local.get("data_root", "local_assets/data"))
    output_root = _resolve(local.get("output_root", "输出"))
    converter = _resolve(local.get("converter_path", BUNDLED_CONVERTER))
    blf_dependencies = _resolve(local.get("blf_dependencies", PROJECT_ROOT / "tools"))
    legacy_template = output_root / "动力总成修正" / "当前配置模型" / "closed_loop_acceptance" / "actual_trace" / "Run_all.par"
    local_template = LOCAL_ASSETS_ROOT / "vehicle_template" / "Run_all.par"
    model_template = _resolve(local.get("model_template_path", legacy_template if legacy_template.exists() else local_template))
    legacy_formal = output_root / "正式联合基线" / "当前配置基线" / "formal_acceptance.json"
    local_formal = LOCAL_ASSETS_ROOT / "formal_baseline" / "formal_acceptance.json"
    default_formal = legacy_formal if legacy_formal.exists() else local_formal if local_formal.exists() else DEMO_FORMAL_RESULT
    formal_result = _resolve(local.get("formal_result_path", default_formal))
    return {
        "project_root": PROJECT_ROOT,
        "data_root": data_root,
        "output_root": output_root,
        "carsim_root": carsim_root,
        "carsim_solver": carsim_root / "Programs" / "VS_SolverWrapper_CLI_64.exe",
        "carsim_dll": carsim_root / "Programs" / "solvers" / "carsim_64.dll",
        "runtime_root": runtime_root,
        "converter_path": converter,
        "blf_dependencies": blf_dependencies,
        "model_template_path": model_template,
        "formal_result_path": formal_result,
        "formal_result_is_demo": formal_result.resolve() == DEMO_FORMAL_RESULT.resolve(),
        "install_root": _resolve(local.get("install_root", PROJECT_ROOT / ".venv")),
    }


def ensure_f_drive_for_mutable_paths(paths: dict[str, Path] | None = None) -> None:
    """兼容旧函数名：公开版允许任意盘符，但CarSim运行目录必须是安全ASCII路径。"""
    resolved = paths or load_runtime_paths()
    runtime_root = Path(resolved["runtime_root"])
    if not runtime_root.is_absolute():
        raise ValueError(f"CarSim运行目录必须是绝对路径：{runtime_root}")
    if os.name == "nt" and not str(runtime_root).isascii():
        raise ValueError(f"CarSim运行目录必须使用纯英文ASCII路径：{runtime_root}")
    if runtime_root == Path(runtime_root.anchor):
        raise ValueError("CarSim运行目录不能直接配置为磁盘根目录")
