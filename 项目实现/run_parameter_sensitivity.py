"""运行纵向参数敏感性扫描，并把每个候选的证据单独归档。

本脚本只针对已经验证可运行的 0-100 km/h Trace 工况，不修改基准输入文件，
也不把敏感性试验结果直接写入正式验收报告。
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = PROJECT_ROOT / "输出" / "Trace控制输入" / "condition_01_trace_control" / "Run_all.par"
DEFAULT_OUTPUT = PROJECT_ROOT / "输出" / "参数敏感性" / "iteration_trace_001"
DEFAULT_RUNTIME = Path("F:/Carsim/AgentRuntime/parameter_agent/parameter_sensitivity/iteration_trace_001")
REAL_ROOT = PROJECT_ROOT / "输出" / "解码CSV_单位修正" / "纵向动力学_加速试验" / "0-100全油门起步加速"
SOLVER_ROOT = Path("F:/Carsim/Carsim2023/Carsim2023.2/install")

# 只扫描已经确认能在 Trace Run_all.par 中定位的三个纵向参数。
SCAN_PARAMETERS: tuple[dict[str, Any], ...] = (
    {"name": "M_SU", "baseline": 2808.0, "step": 20.0, "unit": "kg"},
    {"name": "H_CG_SU", "baseline": 540.0, "step": 10.0, "unit": "mm"},
    {"name": "IYY_SU", "baseline": 1536.7, "step": 30.0, "unit": "kg-m2"},
    # 下面四个字段来自已展开的电驱/差速器数据集，先以小步长验证影响方向。
    {"name": "PWR_HEV_DRV_MAX", "baseline": 55.0, "step": 10.0, "unit": "kW", "values": (55.0, 100.0, 165.0, 220.0)},
    {"name": "PWR_EV_MODE", "baseline": 25.0, "step": 10.0, "unit": "kW", "values": (25.0, 100.0, 165.0)},
    {"name": "PWR_DRV_THROTTLE_COEFFICIENT", "baseline": 165.0, "step": 30.0, "unit": "kW"},
    {"name": "R_GEAR_FD", "baseline": 3.905, "step": 0.3, "unit": "-"},
    {"name": "R_GEAR_RD", "baseline": 3.905, "step": 0.3, "unit": "-"},
    {"name": "MOTOR_TORQUE_SCALE", "baseline": 1.0, "step": 0.5, "unit": "倍", "values": (0.5, 1.0, 2.0, 4.0, 6.0, 8.0)},
)


def load_converter():
    """加载项目已有的 Carsim VS/VSB 转换器，避免重复实现二进制解析。"""
    path = PROJECT_ROOT.parents[1] / "自动化闭环总控" / "03_数据转换" / "convert_carsim_vsb.py"
    spec = importlib.util.spec_from_file_location("carsim_converter", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载转换器：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def format_value(value: float) -> str:
    """按 CarSim 工程文件习惯写入紧凑数值。"""
    return str(int(value)) if value.is_integer() else format(value, ".12g")


def replace_keyword(text: str, keyword: str, value: float) -> str:
    """替换参数关键字的第一条完整数据行。"""
    pattern = re.compile(rf"(?m)^(?P<prefix>\s*{re.escape(keyword)}\s+)(?P<value>[-+0-9.eE]+)(?P<suffix>\s*(?:[;!].*)?)$")
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"模板中未找到参数关键字：{keyword}")
    return text[: match.start()] + f"{match.group('prefix')}{format_value(value)}{match.group('suffix')}" + text[match.end() :]


def scale_motor_torque_tables(text: str, scale: float) -> str:
    """按比例缩放所有电机最大扭矩表的第二列，保留转速轴不变。"""
    pattern = re.compile(r"(?ms)(MMOTOR_MAX_TABLE\s+LINEAR\s*\n)(.*?)(\nENDTABLE)")

    def update(match: re.Match[str]) -> str:
        rows = []
        changed = 0
        for line in match.group(2).splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("!") or "," not in line:
                rows.append(line)
                continue
            left, right = line.split(",", 1)
            try:
                rpm = float(left.strip())
                torque = float(right.strip())
            except ValueError:
                rows.append(line)
                continue
            rows.append(f"{format_value(rpm)}, {format_value(torque * scale)}")
            changed += 1
        if changed == 0:
            raise ValueError("MMOTOR_MAX_TABLE 中没有可缩放的数值行")
        return match.group(1) + "\n".join(rows) + match.group(3)

    updated, count = pattern.subn(update, text)
    if count == 0:
        raise ValueError("模板中未找到 MMOTOR_MAX_TABLE")
    return updated


def prepare_run_file(template: Path, destination: Path, parameter: str, value: float, tstop: float) -> None:
    """复制 Trace 模板并替换一个参数，同时延长仿真到指定总时长。"""
    text = template.read_bytes().decode("ascii")
    if parameter == "MOTOR_TORQUE_SCALE":
        text = scale_motor_torque_tables(text, value)
    else:
        text = replace_keyword(text, parameter, value)
    text, count = re.subn(r"(?m)^TSTOP\s+[-+0-9.eE]+", f"TSTOP {tstop:g}", text, count=1)
    if count != 1:
        raise ValueError("模板中未找到 TSTOP")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(text.encode("ascii"))


def build_sim_file(runtime: Path, run_all: Path) -> Path:
    """生成 ASCII 路径下的 Carsim 求解器配置。"""
    prefix = runtime / "result"
    sim = runtime / "run.sim"
    carsim_root = SOLVER_ROOT
    lines = [
        "SIMFILE", f"FILEBASE {prefix}", f"INPUT {run_all}", f"INPUTARCHIVE {prefix}_all.par",
        f"ECHO {prefix}_echo.par", f"FINAL {prefix}_end.par", f"LOGFILE {prefix}_log.txt",
        f"ERDFILE {prefix}.vs", f"PROGDIR {carsim_root}", f"DATADIR {runtime}",
        f"RESOURCEDIR {carsim_root / 'Resources'}", "PRODUCT_ID CarSim", "PRODUCT_VER 2023.2",
        "VEHICLE_CODE i_i", "EXT_MODEL_STEP 0.00050000",
        f"DLLFILE {carsim_root / 'Programs' / 'solvers' / 'carsim_64.dll'}", "END", "",
    ]
    sim.write_text("\n".join(lines), encoding="ascii")
    return sim


def run_solver(runtime: Path, run_all: Path) -> dict[str, Any]:
    """调用 Carsim CLI 求解器，并保存标准输出和错误输出。"""
    sim = build_sim_file(runtime, run_all)
    wrapper = SOLVER_ROOT / "Programs" / "VS_SolverWrapper_CLI_64.exe"
    result = subprocess.run(
        [str(wrapper), "-sim", str(sim)], cwd=runtime, capture_output=True,
        text=True, encoding="cp1252", errors="replace", check=False,
    )
    (runtime / "solver_stdout.txt").write_text(result.stdout, encoding="utf-8")
    (runtime / "solver_stderr.txt").write_text(result.stderr, encoding="utf-8")
    status = {"return_code": result.returncode, "passed": result.returncode == 0}
    if result.returncode != 0:
        raise RuntimeError(f"Carsim 求解失败，返回码={result.returncode}，目录：{runtime}")
    return status


def convert_result(runtime: Path, output_csv: Path) -> None:
    """把本轮 VS/VSB 转换为项目统一纵向 CSV。"""
    converter = load_converter()
    group, names = converter.read_vs_metadata(runtime / "result.vs")
    rows = converter.read_vsb(runtime / "result.vsb", len(names))
    standard = converter.build_standard_rows(
        rows, names, group, ["vxdot", "ax", "speed_kmh", "accel_mps2"],
        {"accelerator_pedal_pct": 0.0},
    )
    for row in standard:
        row["speed_kmh"] = row["vxdot"] * 3.6
        row["accel_mps2"] = row["ax"]
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        # 转换器会同时返回横向和位置通道；敏感性评价只需要三列纵向信号。
        writer = csv.DictWriter(stream, fieldnames=["time_s", "speed_kmh", "accel_mps2"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(standard)


def evaluate_trial(sim_csv: Path, rules: dict[str, Any]) -> dict[str, Any]:
    """将本轮仿真与六条真实 0-100 数据分别比较，再取平均分。"""
    from evaluate_longitudinal import compare_pair

    truth_paths = sorted(REAL_ROOT.glob("*.csv"))
    if len(truth_paths) != 6:
        raise ValueError(f"0-100 实车文件数量应为6，实际为{len(truth_paths)}")
    comparisons = [compare_pair(path, sim_csv, "zero_to_100", rules) for path in truth_paths]
    scores = [item["maneuver_score_pct"] for item in comparisons]
    return {
        "comparison_count": len(comparisons),
        "mean_score_pct": sum(scores) / len(scores),
        "mean_peak_ax_score_pct": sum(item["metrics"]["peak_ax"]["score_pct"] for item in comparisons) / len(comparisons),
        "mean_speed_r2": sum(item["metrics"]["speed_r2"]["value"] for item in comparisons) / len(comparisons),
        "mean_speed_nrmse": sum(item["metrics"]["speed_nrmse"]["value"] for item in comparisons) / len(comparisons),
        "comparisons": comparisons,
    }


def scan(template: Path, output_root: Path, runtime_root: Path, tstop: float, limit: int | None, only: set[str] | None = None) -> dict[str, Any]:
    """执行全部候选并写出 JSON、CSV、Markdown 三种汇总证据。"""
    if output_root.exists():
        raise FileExistsError(f"输出目录已存在，为避免覆盖历史证据而停止：{output_root}")
    output_root.mkdir(parents=True)
    rules = {"speed_r2_min": 0.98, "speed_nrmse_max": 0.05, "peak_ax_accuracy_min_pct": 90.0, "target_time_accuracy_min_pct": 90.0}
    trials = []
    candidates = []
    selected_parameters = tuple(item for item in SCAN_PARAMETERS if not only or item["name"] in only)
    for item in selected_parameters:
        if item.get("values"):
            # 表格缩放需要跨越较宽范围，使用显式倍率而不是正负单步。
            for value in item["values"]:
                candidates.append((item, 0, f"value_{value:g}", value))
        else:
            for direction, label in ((-1, "minus"), (1, "plus")):
                candidates.append((item, direction, label, item["baseline"] + direction * item["step"]))
    if limit is not None:
        candidates = candidates[:limit]
    for index, (item, direction, label, value) in enumerate(candidates, start=1):
        trial_name = f"trial_{index:02d}_{item['name']}_{label}"
        trial_dir = output_root / trial_name
        runtime = runtime_root / trial_name
        trial_dir.mkdir(parents=True)
        runtime.mkdir(parents=True, exist_ok=True)
        run_all = runtime / "Run_all.par"
        prepare_run_file(template, run_all, item["name"], value, tstop)
        status = run_solver(runtime, run_all)
        # 将关键输入和求解日志复制回试验目录，保证每个候选都能独立复核。
        for evidence_name in ("Run_all.par", "run.sim", "solver_stdout.txt", "solver_stderr.txt"):
            shutil.copy2(runtime / evidence_name, trial_dir / evidence_name)
        shutil.copy2(runtime / "result.vs", trial_dir / "result.vs")
        shutil.copy2(runtime / "result.vsb", trial_dir / "result.vsb")
        sim_csv = trial_dir / "simulation.csv"
        convert_result(runtime, sim_csv)
        evaluation = evaluate_trial(sim_csv, rules)
        trial = {"trial": trial_name, "parameter": item["name"], "direction": direction, "value": value, "baseline": item["baseline"], "step": item["step"], "unit": item["unit"], "solver": status, "evaluation": evaluation}
        (trial_dir / "trial.json").write_text(json.dumps(trial, ensure_ascii=False, indent=2), encoding="utf-8")
        (trial_dir / "README.md").write_text(
            f"# {trial_name}\n\n"
            f"本目录是 `{item['name']}` 从基线值 {item['baseline']:g} {item['unit']} 调整到 {value:g} {item['unit']} 的独立试验。\n\n"
            "- `Run_all.par`：本轮实际送入 Carsim 的参数和 Trace 输入。\n"
            "- `run.sim`：ASCII 运行配置。\n"
            "- `result.vs`、`result.vsb`：Carsim 原始结果。\n"
            "- `simulation.csv`：转换后的统一纵向结果。\n"
            "- `trial.json`：本轮参数、求解状态和 6 条实车对比指标。\n",
            encoding="utf-8",
        )
        trials.append(trial)
        print(json.dumps({"trial": trial_name, "mean_score_pct": evaluation["mean_score_pct"]}, ensure_ascii=False))
    result = {"type": "longitudinal_parameter_sensitivity", "template": str(template), "tstop_s": tstop, "trial_count": len(trials), "trials": trials}
    (output_root / "sensitivity_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output_root / "sensitivity_results.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["trial", "parameter", "direction", "value", "baseline", "mean_score_pct", "mean_peak_ax_score_pct", "mean_speed_r2", "mean_speed_nrmse"])
        writer.writeheader()
        for trial in trials:
            evaluation = trial["evaluation"]
            writer.writerow({"trial": trial["trial"], "parameter": trial["parameter"], "direction": trial["direction"], "value": trial["value"], "baseline": trial["baseline"], **{key: evaluation[key] for key in ("mean_score_pct", "mean_peak_ax_score_pct", "mean_speed_r2", "mean_speed_nrmse")}})
    lines = ["# 纵向参数敏感性扫描", "", f"试验数量：{len(trials)}", f"仿真总时长：{tstop:g} s", "", "| 参数 | 方向 | 候选值 | 平均精度 | 峰值加速度精度 | 平均R² | 平均NRMSE |", "|---|---:|---:|---:|---:|---:|---:|"]
    for trial in trials:
        evaluation = trial["evaluation"]
        lines.append(f"| {trial['parameter']} | {trial['direction']:+d} | {trial['value']:g} | {evaluation['mean_score_pct']:.2f}% | {evaluation['mean_peak_ax_score_pct']:.2f}% | {evaluation['mean_speed_r2']:.4f} | {evaluation['mean_speed_nrmse']:.4f} |")
    lines += ["", "说明：本扫描只用于判断参数对纵向 Trace 输出是否有可观测影响，不代表正式验收结果；正式验收仍需同时覆盖加速和滑行。"]
    (output_root / "敏感性扫描报告.md").write_text("\n".join(lines), encoding="utf-8")
    (output_root / "README.md").write_text("# 参数敏感性输出\n\n每个 `trial_*` 子目录保存一组参数候选的 Run_all、Carsim 原始结果、统一 CSV 和评价 JSON。运行命令示例：`python run_parameter_sensitivity.py --tstop 90`。\n", encoding="utf-8")
    return result


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--tstop", type=float, default=90.0)
    parser.add_argument("--limit", type=int, help="仅运行前N个试验，用于先做冒烟验证")
    parser.add_argument("--only", nargs="+", help="只扫描指定参数名，例如 PWR_HEV_DRV_MAX PWR_DRV_THROTTLE_COEFFICIENT")
    args = parser.parse_args()
    result = scan(args.template.resolve(), args.output.resolve(), args.runtime.resolve(), args.tstop, args.limit, set(args.only or []))
    print(json.dumps({"output": str(args.output.resolve()), "trial_count": result["trial_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
