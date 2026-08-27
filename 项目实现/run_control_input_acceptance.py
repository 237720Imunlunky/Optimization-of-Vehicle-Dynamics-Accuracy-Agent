"""执行最后一次控制输入闭环验收：零、半、全油门和实车 Trace 对照。"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from pathlib import Path
from runtime_paths import load_runtime_paths
from typing import Any

from run_parameter_sensitivity import (
    DEFAULT_TEMPLATE,
    PROJECT_ROOT,
    REAL_ROOT,
    convert_result,
    run_solver,
)


DEFAULT_OUTPUT = PROJECT_ROOT / "输出" / "控制输入闭环验收" / "iteration_control_acceptance_001"
DEFAULT_RUNTIME = load_runtime_paths()["runtime_root"] / "control_input_acceptance" / "iteration_control_acceptance_001"


def format_value(value: float) -> str:
    """按 CarSim 文件格式写入常数油门值。"""
    return str(int(value)) if value.is_integer() else format(value, ".8f")


def replace_throttle_table(text: str, level: float) -> tuple[str, dict[str, Any]]:
    """保留原 Trace 时间轴，只把油门值统一替换为指定比例。"""
    pattern = re.compile(r"(?ms)(THROTTLE_ENGINE_TABLE\s+LINEAR_FLAT\s*\n)(.*?)(\nENDTABLE)")
    table_info: dict[str, Any] = {}

    def update(match: re.Match[str]) -> str:
        rows = []
        sample_count = 0
        times = []
        for line in match.group(2).splitlines():
            if "," not in line:
                rows.append(line)
                continue
            left, _ = line.split(",", 1)
            try:
                time_s = float(left.strip())
            except ValueError:
                rows.append(line)
                continue
            times.append(time_s)
            rows.append(f"{format_value(time_s)}, {format_value(level)}")
            sample_count += 1
        if sample_count == 0:
            raise ValueError("油门表没有有效时间—数值行")
        table_info.update({"level": level, "sample_count": sample_count, "start_s": min(times), "end_s": max(times)})
        return match.group(1) + "\n".join(rows) + match.group(3)

    updated, count = pattern.subn(update, text, count=1)
    if count != 1:
        raise ValueError("没有找到 THROTTLE_ENGINE_TABLE")
    return updated, table_info


def prepare_case(template: Path, destination: Path, mode: str, level: float | None, tstop: float) -> dict[str, Any]:
    """生成单个控制输入验收工况。"""
    text = template.read_bytes().decode("ascii")
    if level is None:
        throttle = {"mode": mode, "source": "actual_trace_table"}
    else:
        text, throttle_info = replace_throttle_table(text, level)
        throttle = {"mode": mode, "source": "same_trace_time_axis_constant_level", **throttle_info}
    text, count = re.subn(r"(?m)^TSTOP\s+[-+0-9.eE]+", f"TSTOP {tstop:g}", text, count=1)
    if count != 1:
        raise ValueError("模板中未找到 TSTOP")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(text.encode("ascii"))
    return throttle


def summarize_csv(path: Path, window_s: float = 5.63) -> dict[str, float]:
    """提取闭环验收所需的车速和加速度摘要。"""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    early = [row for row in rows if float(row["time_s"]) <= window_s]
    speeds = [float(row["speed_kmh"]) for row in rows]
    early_speeds = [float(row["speed_kmh"]) for row in early]
    accelerations = [abs(float(row["accel_mps2"])) for row in early]
    return {
        "speed_at_window_kmh": early_speeds[-1] if early_speeds else 0.0,
        "max_speed_window_kmh": max(early_speeds, default=0.0),
        "max_speed_90s_kmh": max(speeds, default=0.0),
        "peak_accel_window_mps2": max(accelerations, default=0.0),
    }


def compare_full_case(csv_path: Path) -> dict[str, Any]:
    """用同一评价器比较全油门验收曲线与六条实车曲线。"""
    from evaluate_longitudinal import compare_pair

    rules = {"speed_r2_min": 0.98, "speed_nrmse_max": 0.05, "peak_ax_accuracy_min_pct": 90.0, "target_time_accuracy_min_pct": 90.0}
    truth_paths = sorted(REAL_ROOT.glob("*.csv"))
    comparisons = [compare_pair(path, csv_path, "zero_to_100", rules) for path in truth_paths]
    return {
        "count": len(comparisons),
        "mean_score_pct": sum(item["maneuver_score_pct"] for item in comparisons) / len(comparisons),
        "comparisons": comparisons,
    }


def run_acceptance(template: Path, output_root: Path, runtime_root: Path, tstop: float) -> dict[str, Any]:
    """运行四组控制输入并生成闭环结论。"""
    if output_root.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output_root}")
    output_root.mkdir(parents=True)
    cases = (("zero", 0.0), ("half", 0.5), ("full", 1.0), ("actual_trace", None))
    results = []
    for mode, level in cases:
        case_dir = output_root / mode
        runtime = runtime_root / mode
        case_dir.mkdir(parents=True)
        runtime.mkdir(parents=True, exist_ok=True)
        run_all = runtime / "Run_all.par"
        throttle = prepare_case(template, run_all, mode, level, tstop)
        solver = run_solver(runtime, run_all)
        for name in ("Run_all.par", "run.sim", "solver_stdout.txt", "solver_stderr.txt", "result.vs", "result.vsb"):
            shutil.copy2(runtime / name, case_dir / name)
        simulation_csv = case_dir / "simulation.csv"
        convert_result(runtime, simulation_csv)
        result = {"mode": mode, "throttle": throttle, "solver": solver, "summary": summarize_csv(simulation_csv)}
        if mode in {"full", "actual_trace"}:
            result["real_data_comparison"] = compare_full_case(simulation_csv)
        (case_dir / "case.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (case_dir / "README.md").write_text(
            f"# {mode}\n\n"
            "本目录保存单一控制输入验收工况。`Run_all.par` 是实际送入 Carsim 的输入，"
            "`simulation.csv` 是转换后的纵向输出，`case.json` 是本组控制输入和验收指标。\n",
            encoding="utf-8",
        )
        results.append(result)
    by_mode = {item["mode"]: item for item in results}
    monotonic = (
        by_mode["zero"]["summary"]["max_speed_window_kmh"]
        <= by_mode["half"]["summary"]["max_speed_window_kmh"]
        <= by_mode["full"]["summary"]["max_speed_window_kmh"]
    )
    throttle_saturation = by_mode["full"]["summary"]["max_speed_window_kmh"] <= max(
        by_mode["half"]["summary"]["max_speed_window_kmh"] * 1.02, 0.1
    )
    actual_full_gap = by_mode["actual_trace"]["summary"]["speed_at_window_kmh"] < 0.5 * 104.06
    if monotonic and actual_full_gap and throttle_saturation:
        conclusion = "Trace 已进入模型且零/半油门有响应，但半/全油门饱和；主要差异来自动力系统响应，同时保留功率限幅/油门映射问题待定位"
    elif monotonic and actual_full_gap:
        conclusion = "Trace 输入链路有效，主要差异来自车辆动力学/动力系统响应"
    else:
        conclusion = "控制输入链路仍需进一步排查"
    report = {"type": "control_input_closed_loop_acceptance", "tstop_s": tstop, "cases": results, "checks": {"solver_all_passed": all(item["solver"]["passed"] for item in results), "throttle_response_monotonic": monotonic, "throttle_saturation_detected": throttle_saturation, "actual_trace_low_response_confirmed": actual_full_gap}, "conclusion": conclusion}
    (output_root / "control_input_acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 控制输入闭环验收报告", "", f"仿真总时长：{tstop:g} s", "", "| 输入模式 | 5.63 s车速 | 90 s最高车速 | 峰值加速度 | Carsim |", "|---|---:|---:|---:|---|"]
    for item in results:
        summary = item["summary"]
        lines.append(f"| {item['mode']} | {summary['speed_at_window_kmh']:.2f} km/h | {summary['max_speed_90s_kmh']:.2f} km/h | {summary['peak_accel_window_mps2']:.3f} m/s2 | {'通过' if item['solver']['passed'] else '失败'} |")
    lines += ["", f"控制输入单调性：{'通过' if monotonic else '失败'}", f"半/全油门饱和：{'是' if throttle_saturation else '否'}", f"实车 Trace 全油门低响应复现：{'是' if actual_full_gap else '否'}", f"结论：{conclusion}", "", "本报告用于排除控制输入链路问题，不替代正式的加速+滑行 80% 验收报告。"]
    (output_root / "控制输入闭环验收报告.md").write_text("\n".join(lines), encoding="utf-8")
    (output_root / "README.md").write_text("# 控制输入闭环验收\n\n包含 zero、half、full、actual_trace 四组同模型控制输入试验。每个子目录保存输入文件、Carsim 原始结果、统一 CSV、JSON 和 README。\n", encoding="utf-8")
    return report


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--tstop", type=float, default=90.0)
    args = parser.parse_args()
    report = run_acceptance(args.template.resolve(), args.output.resolve(), args.runtime.resolve(), args.tstop)
    print(json.dumps({"output": str(args.output.resolve()), "conclusion": report["conclusion"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
