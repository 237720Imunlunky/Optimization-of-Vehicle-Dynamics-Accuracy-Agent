"""检查普通功能和完整CarSim闭环分别需要的运行环境。"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime_paths import ensure_f_drive_for_mutable_paths, load_runtime_paths


def module_status(name: str) -> dict[str, object]:
    """检查Python模块是否可导入，不执行网络或外部程序。"""
    return {"name": name, "available": importlib.util.find_spec(name) is not None}


def file_status(name: str, path: Path) -> dict[str, object]:
    """检查关键文件并返回明确路径。"""
    return {"name": name, "available": path.exists(), "path": str(path)}


def llm_config_status(path: Path) -> dict[str, object]:
    """检查API配置完整性但绝不返回密钥内容。"""
    available = False
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            available = bool(
                str(payload.get("api_key", "")).strip()
                and str(payload.get("base_url", "")).startswith(("http://", "https://"))
                and str(payload.get("model", "")).strip()
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            available = False
    return {"name": "LLM API本机配置", "available": available, "path": str(path)}


def model_template_status(path: Path) -> dict[str, object]:
    """验证车辆模板存在且包含参数注册表声明的全部CarSim绑定关键字。"""
    missing: list[str] = []
    if path.is_file():
        try:
            text = path.read_bytes().decode("ascii")
            registry = json.loads((PROJECT_ROOT / "llm_optimizer" / "config" / "parameter_registry.json").read_text(encoding="utf-8"))
            for specification in registry["parameters"].values():
                if specification.get("locked"):
                    continue
                for keyword in specification.get("binding", {}).get("keywords", []):
                    if not re.search(rf"(?m)^{re.escape(str(keyword))}(?:\s|$)", text):
                        missing.append(str(keyword))
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            missing.append("模板不是有效ASCII展开参数文件")
    return {
        "name": "车辆模型模板", "available": path.is_file() and not missing,
        "path": str(path), "missing_bindings": sorted(set(missing)),
    }


def formal_result_status(path: Path, demo: bool) -> dict[str, object]:
    """验证正式/演示基线具备三类工况共18条结构化结果。"""
    result_count = 0
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            result_count = len(payload.get("results", []))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            result_count = 0
    return {
        "name": "正式基线", "available": result_count == 18,
        "path": str(path), "demo": demo, "result_count": result_count,
    }


def runtime_path_status(paths: dict[str, Path]) -> dict[str, object]:
    """CarSim控制文件要求运行目录为F盘纯英文路径。"""
    try:
        ensure_f_drive_for_mutable_paths(paths)
    except ValueError as error:
        return {"name": "CarSim纯英文运行目录", "available": False, "path": str(paths["runtime_root"]), "reason": str(error)}
    return {"name": "CarSim纯英文运行目录", "available": True, "path": str(paths["runtime_root"])}


def build_report() -> dict[str, object]:
    """生成分级体检结论；CarSim授权只能通过实际最小求解进一步确认。"""
    paths = load_runtime_paths()
    # 项目允许把BLF解析依赖随部署包放在F盘，不要求污染系统Python。
    dependency_root = str(paths["blf_dependencies"])
    if dependency_root not in sys.path:
        sys.path.insert(0, dependency_root)
    dbc_files = list(paths["data_root"].glob("*.dbc")) if paths["data_root"].exists() else []
    llm_config = Path(os.environ.get(
        "VEHICLE_AGENT_LLM_CONFIG",
        PROJECT_ROOT / "Agent交互界面" / "config" / "llm_api.local.json",
    ))
    is_linux = platform.system() == "Linux"
    is_windows = platform.system() == "Windows"
    checks = [
        {"name": "支持的操作系统", "available": (is_windows or is_linux) and platform.machine().lower() in {"amd64", "x86_64", "aarch64"}, "system": platform.system(), "arch": platform.machine()},
        {"name": "Python 3.10+", "available": sys.version_info >= (3, 10) and sys.maxsize > 2**32},
        module_status("can"), module_status("cantools"),
        file_status("实车数据目录", paths["data_root"]),
        {"name": "DBC文件", "available": bool(dbc_files), "count": len(dbc_files)},
        runtime_path_status(paths),
        file_status("CarSim CLI求解器", paths["carsim_solver"]),
        file_status("CarSim动态库", paths["carsim_dll"]),
        file_status("VS/VSB转换器", paths["converter_path"]),
        model_template_status(paths["model_template_path"]),
        formal_result_status(paths["formal_result_path"], bool(paths["formal_result_is_demo"])),
        llm_config_status(llm_config),
    ]
    demo_names = {"支持的操作系统", "Python 3.10+", "can", "cantools", "VS/VSB转换器", "正式基线"}
    demo_ready = all(bool(item["available"]) for item in checks if item["name"] in demo_names)
    data_names = demo_names | {"实车数据目录", "DBC文件"}
    data_ready = all(bool(item["available"]) for item in checks if item["name"] in data_names)
    full_ready = data_ready and not bool(paths["formal_result_is_demo"]) and all(
        bool(item["available"]) for item in checks
        if item["name"] in {"CarSim纯英文运行目录", "CarSim CLI求解器", "CarSim动态库", "车辆模型模板", "LLM API本机配置"}
    )
    return {
        "basic_functions_ready": demo_ready,
        "demo_and_dry_run_ready": demo_ready,
        "data_workflow_ready": data_ready,
        "full_optimization_files_ready": full_ready,
        "active_level": "full_optimization" if full_ready else "data_workflow" if data_ready else "demo_and_dry_run" if demo_ready else "not_ready",
        "carsim_license": "需要使用合法授权并通过一次最小求解验证，文件检查无法证明授权有效",
        "checks": checks,
    }


def main() -> None:
    """打印体检结果并保存到部署包独立输出目录。"""
    report = build_report()
    output = PROJECT_ROOT / "输出" / "部署体检" / "当前机器"
    output.mkdir(parents=True, exist_ok=True)
    (output / "health_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "README.md").write_text(
        "# 当前机器部署体检\n\n`health_check.json`区分基础只读功能与完整CarSim闭环是否就绪。"
        "CarSim安装文件存在不代表许可证有效，首次使用必须执行最小求解确认授权。\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
