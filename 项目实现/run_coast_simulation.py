"""生成并可选运行 Carsim 50->30 km/h 滑行工况。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from runtime_paths import load_runtime_paths

from config_loader import load_project_config
from run_parameter_sensitivity import convert_result


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_PATHS = load_runtime_paths()
DEFAULT_TEMPLATE = RUNTIME_PATHS["model_template_path"]
DEFAULT_ARCHIVE = PROJECT_ROOT / "输出" / "滑行工况" / "当前配置基线"
DEFAULT_RUNTIME = RUNTIME_PATHS["runtime_root"] / "formal_longitudinal" / "current_config" / "coasting"
DEFAULT_CARSIM_ROOT = RUNTIME_PATHS["carsim_root"]


def build_par(
    template: Path,
    output: Path,
    duration_s: float,
    off_throttle_regen: bool = False,
    regen_shape_factor: float | None = None,
    coast_window_kmh: tuple[float, float] | list[float] | None = None,
    condition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从已有纵向模板生成滑行参数文件，并按统一工况配置写入可确认控制量。"""
    condition = condition or {}
    window = coast_window_kmh or condition.get("window_kmh", [50.0, 30.0])
    if len(window) != 2 or float(window[0]) <= float(window[1]):
        raise ValueError("滑行窗口必须是递减的两个车速")
    start_speed, end_speed = float(window[0]), float(window[1])
    # 按字节读取以保留 CarSim 展开参数的 CRLF 格式，避免表格解析器误读。
    text = template.read_bytes().decode("ascii")
    text = re.sub(r"^TSTOP\s+[-+0-9.]+", f"TSTOP {duration_s:.3f}", text, flags=re.MULTILINE)
    # 当前展开模型的SV_VXS单位为km/h；旧脚本误写13.8889会导致实际从13.89 km/h起步。
    text, speed_count = re.subn(r"^SV_VXS\s+[-+0-9.]+", f"SV_VXS {start_speed:g}", text, count=1, flags=re.MULTILINE)
    if speed_count != 1:
        raise ValueError("模板中未找到唯一的SV_VXS初始车速")
    regen_value = 1 if off_throttle_regen else 0
    text, regen_count = re.subn(
        r"^OPT_REGEN_OFF_THRT\s+[-+0-9.]+",
        f"OPT_REGEN_OFF_THRT {regen_value}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if regen_count != 1:
        raise ValueError("模板中未找到唯一的OPT_REGEN_OFF_THRT回收开关")
    if regen_shape_factor is not None:
        text, factor_count = re.subn(
            r"^CF_HEV_PBK\s+[-+0-9.]+",
            f"CF_HEV_PBK {regen_shape_factor:.6g}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if factor_count != 1:
            raise ValueError("模板中未找到唯一的CF_HEV_PBK回收形状系数")
    # 配置中声明的CarSim关键字必须真实写入模板；未声明绑定的条件只记录为未绑定，禁止猜写。
    control_audit = []
    for name, binding in condition.get("carsim_controls", {}).items():
        keyword = str(binding.get("keyword", "")).strip()
        if not keyword:
            control_audit.append({"name": name, "status": "unbound", "reason": "未配置CarSim关键字"})
            continue
        value = binding.get("value")
        replacement = str(value)
        pattern = rf"^(?P<prefix>\s*{re.escape(keyword)}\s+)(?P<value>\S+)(?P<suffix>\s*(?:[;!].*)?)$"
        text, count = re.subn(pattern, rf"\g<prefix>{replacement}\g<suffix>", text, count=1, flags=re.MULTILINE)
        if count != 1:
            raise ValueError(f"CarSim控制绑定{name}的关键字{keyword}未找到唯一字段")
        control_audit.append({
            "name": name, "keyword": keyword, "value": value, "status": "applied",
            "source": binding.get("source"),
        })
    control_audit.extend(
        {"name": name, "status": "unbound", **details}
        for name, details in condition.get("unbound_controls", {}).items()
    )
    text = re.sub(
        r"THROTTLE_ENGINE_TABLE LINEAR_FLAT\r?\n.*?ENDTABLE",
        "THROTTLE_ENGINE_TABLE LINEAR_FLAT\n0, 0\n1, 0\nENDTABLE",
        text,
        count=1,
        flags=re.DOTALL,
    )
    text = text.replace("condition_03_0_to_80_half_pedal", "coast_50_to_30")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(text.encode("ascii"))
    return {"window_kmh": [start_speed, end_speed], "duration_s": float(duration_s), "controls": control_audit}


def build_simfile(run_dir: Path, carsim_root: Path) -> Path:
    """生成 Carsim SolverWrapper 使用的 sim 文件。"""
    prefix = run_dir / "result"
    lines = [
        "SIMFILE", f"FILEBASE {prefix}", f"INPUT {run_dir / 'Run_all.par'}",
        f"INPUTARCHIVE {prefix}_all.par", f"ECHO {prefix}_echo.par",
        f"FINAL {prefix}_end.par", f"LOGFILE {prefix}_log.txt", f"ERDFILE {prefix}.vs",
        f"PROGDIR {carsim_root}", f"DATADIR {run_dir}", f"RESOURCEDIR {carsim_root / 'Resources'}",
        "PRODUCT_ID CarSim", "PRODUCT_VER 2023.2", "VEHICLE_CODE i_i",
        "EXT_MODEL_STEP 0.00050000", f"DLLFILE {carsim_root / 'Programs' / 'solvers' / 'carsim_64.dll'}", "END", "",
    ]
    path = run_dir / "run.sim"
    path.write_text("\n".join(lines), encoding="ascii")
    return path


def run_one(run_dir: Path, carsim_root: Path, execute: bool) -> dict:
    """运行单条滑行仿真，或只完成输入准备。"""
    wrapper = carsim_root / "Programs" / "VS_SolverWrapper_CLI_64.exe"
    if not execute:
        return {"status": "prepared", "run_dir": str(run_dir), "solver": str(wrapper)}
    completed = subprocess.run([str(wrapper), "-sim", str(run_dir / "run.sim")], cwd=run_dir, capture_output=True, text=True, encoding="cp1252", errors="replace", check=False)
    (run_dir / "solver_stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "solver_stderr.txt").write_text(completed.stderr, encoding="utf-8")
    return {"status": "completed" if completed.returncode == 0 else "failed", "return_code": completed.returncode, "run_dir": str(run_dir)}


def main() -> None:
    """准备或执行一条代表性滑行仿真。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="实际调用 Carsim 求解器")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="修正版展开参数模板")
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE, help="分类归档输出目录")
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME, help="CarSim ASCII运行目录")
    project_config = load_project_config()
    condition = dict(project_config["agent"]["coasting_test_condition"])
    parser.add_argument("--duration", type=float, default=float(condition["simulation_duration_s"]), help="仿真时长")
    parser.add_argument("--off-throttle-regen", action="store_true", help="启用D挡松油门能量回收")
    parser.add_argument("--carsim-root", type=Path, default=DEFAULT_CARSIM_ROOT)
    args = parser.parse_args()
    archive = args.archive.resolve()
    output = args.runtime.resolve()
    template = args.template.resolve()
    carsim_root = args.carsim_root.resolve()
    if archive.exists():
        raise FileExistsError(f"归档目录已存在，拒绝覆盖：{archive}")
    archive.mkdir(parents=True)
    output.mkdir(parents=True, exist_ok=True)
    if args.off_throttle_regen:
        condition["carsim_controls"] = dict(condition.get("carsim_controls", {}))
        condition["carsim_controls"]["regeneration"] = {
            **condition["carsim_controls"].get("regeneration", {}), "value": 1,
        }
    inputs = build_par(template, output / "Run_all.par", args.duration, args.off_throttle_regen, condition=condition)
    build_simfile(output, carsim_root)
    result = run_one(output, carsim_root, args.run)
    for item in output.iterdir():
        if item.is_file() and item.name in {"Run_all.par", "run.sim", "result.vs", "result.vsb", "result_echo.par", "result_end.par", "result_log.txt", "solver_stdout.txt", "solver_stderr.txt"}:
            shutil.copy2(item, archive / item.name)
    if result["status"] == "completed":
        convert_result(output, archive / "simulation.csv")
    result["archive_dir"] = str(archive)
    result["template"] = str(template)
    result["duration_s"] = args.duration
    result["inputs"] = inputs
    result["condition"] = condition
    (archive / "run_status.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (archive / "README.md").write_text(
        "# 修正版50→30 km/h滑行基线\n\n"
        "本目录使用与加速闭环相同的修正版动力总成。`Run_all.par` 是实际输入，"
        "`simulation.csv` 是统一纵向输出，`run_status.json` 记录模板、时长和求解状态。\n\n"
        "运行方式：`python run_coast_simulation.py --run`。脚本拒绝覆盖已有归档目录。\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
