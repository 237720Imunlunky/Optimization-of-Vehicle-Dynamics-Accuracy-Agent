"""修正错误的 CarSim 占位动力总成，并执行多档油门闭环验收。

本脚本不修改原始 Run_all.par。目标车厂家电机曲线当前缺失，因此候选电机参数
统一标记为从实车 0-100 km/h Trace 识别，不把识别值表述为厂家标定值。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any

from diagnose_powertrain_chain import convert_all_channels, inject_diagnostic_outputs, summarize_channels
from run_control_input_acceptance import compare_full_case, prepare_case, summarize_csv
from run_parameter_sensitivity import DEFAULT_TEMPLATE, PROJECT_ROOT, convert_result, replace_keyword, run_solver


DEFAULT_OUTPUT = PROJECT_ROOT / "输出" / "动力总成修正" / "当前配置模型"
DEFAULT_RUNTIME = Path("F:/Carsim/AgentRuntime/parameter_agent/powertrain_fix/当前配置模型")

# 主减速比来自项目参数表；电机扭矩和功率是实车 Trace 识别候选，不是厂家参数。
FRONT_FINAL_DRIVE = 11.635
REAR_FINAL_DRIVE = 11.842
PARAMETER_SOURCE = "identified_from_real_vehicle_trace"
TARGET_SPEED_AT_5P63_KMH = 104.06
BATTERY_RESISTANCE_SCALE = 0.05

# 先覆盖合理物理区间，随后以实车 Trace 评价结果选择本轮最佳候选。
MOTOR_CANDIDATES: tuple[dict[str, float | str], ...] = (
    {"id": "candidate_01", "front_peak_torque_nm": 240.0, "rear_peak_torque_nm": 240.0, "front_peak_power_kw": 180.0, "rear_peak_power_kw": 180.0},
    {"id": "candidate_02", "front_peak_torque_nm": 250.0, "rear_peak_torque_nm": 250.0, "front_peak_power_kw": 190.0, "rear_peak_power_kw": 190.0},
    {"id": "candidate_03", "front_peak_torque_nm": 260.0, "rear_peak_torque_nm": 260.0, "front_peak_power_kw": 200.0, "rear_peak_power_kw": 200.0},
    {"id": "candidate_04", "front_peak_torque_nm": 270.0, "rear_peak_torque_nm": 270.0, "front_peak_power_kw": 210.0, "rear_peak_power_kw": 210.0},
)

MOTOR_SPEED_POINTS_RPM = (0, 500, 1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000, 12000, 14000, 16000)


def format_number(value: float) -> str:
    """生成稳定且紧凑的 CarSim 数值文本。"""
    return str(int(value)) if value.is_integer() else format(value, ".8f").rstrip("0").rstrip(".")


def build_motor_table(peak_torque_nm: float, peak_power_kw: float) -> list[tuple[int, float]]:
    """按低速恒扭矩、高速恒功率关系生成电机外特性曲线。"""
    rows: list[tuple[int, float]] = []
    for speed_rpm in MOTOR_SPEED_POINTS_RPM:
        if speed_rpm == 0:
            torque_nm = peak_torque_nm
        else:
            constant_power_torque = peak_power_kw * 9550.0 / speed_rpm
            torque_nm = min(peak_torque_nm, constant_power_torque)
        rows.append((speed_rpm, torque_nm))
    return rows


def replace_motor_tables(text: str, candidate: dict[str, float | str]) -> str:
    """依次替换前、后两个 MMOTOR_MAX_TABLE，保留其他动力总成定义。"""
    pattern = re.compile(r"(?ms)(MMOTOR_MAX_TABLE\s+LINEAR\s*\n)(.*?)(\nENDTABLE)")
    matches = list(pattern.finditer(text))
    if len(matches) != 2:
        raise ValueError(f"预期找到2个电机扭矩表，实际找到{len(matches)}个")

    specifications = (
        (float(candidate["front_peak_torque_nm"]), float(candidate["front_peak_power_kw"])),
        (float(candidate["rear_peak_torque_nm"]), float(candidate["rear_peak_power_kw"])),
    )
    output: list[str] = []
    cursor = 0
    for match, (peak_torque, peak_power) in zip(matches, specifications):
        rows = build_motor_table(peak_torque, peak_power)
        table_text = "\n".join(f"{rpm}, {format_number(torque)}" for rpm, torque in rows)
        output.extend((text[cursor : match.start()], match.group(1), table_text, match.group(3)))
        cursor = match.end()
    output.append(text[cursor:])
    return "".join(output)


def replace_keyword_occurrence(text: str, keyword: str, occurrence: int, value: float) -> str:
    """替换同名关键字的指定出现位置，解决 CarSim 数组参数的展开映射。"""
    pattern = re.compile(rf"(?m)^(?P<prefix>\s*{re.escape(keyword)}\s+)(?P<value>[-+0-9.eE]+)(?P<suffix>\s*(?:[;!].*)?)$")
    matches = list(pattern.finditer(text))
    if occurrence < 1 or occurrence > len(matches):
        raise ValueError(f"{keyword} 第{occurrence}处不存在，实际共{len(matches)}处")
    match = matches[occurrence - 1]
    replacement = f"{match.group('prefix')}{format_number(value)}{match.group('suffix')}"
    return text[: match.start()] + replacement + text[match.end() :]


def scale_battery_resistance_carpet(text: str, keyword: str, scale: float) -> str:
    """缩放电池内阻二维表的数值区，保留SOC和温度坐标轴。"""
    pattern = re.compile(rf"(?ms)({re.escape(keyword)}\s+2D_SPLINE\s*\n)(.*?)(\nENDTABLE)")

    def update(match: re.Match[str]) -> str:
        lines = match.group(2).splitlines()
        output_lines: list[str] = []
        for index, line in enumerate(lines):
            values = [item.strip() for item in line.split(",")]
            # 第一行是温度轴，后续各行第一列是SOC，均不参与缩放。
            if index == 0 or len(values) < 2:
                output_lines.append(line)
                continue
            scaled = [values[0], *(format_number(float(item) * scale) for item in values[1:])]
            output_lines.append(" "+", ".join(scaled))
        return match.group(1) + "\n".join(output_lines) + match.group(3)

    updated, count = pattern.subn(update, text, count=1)
    if count != 1:
        raise ValueError(f"未找到电池内阻表：{keyword}")
    return updated


def apply_powertrain_correction(text: str, candidate: dict[str, float | str]) -> str:
    """写入确定的速比和本轮识别电机曲线，返回修正版模型文本。"""
    corrected = replace_keyword(text, "R_GEAR_FD", FRONT_FINAL_DRIVE)
    corrected = replace_keyword(corrected, "R_GEAR_RD", REAR_FINAL_DRIVE)
    # 展开文件中第1个R_GEAR_DIFF属于分动箱，第2、3个才是前后轴实际求解值。
    corrected = replace_keyword_occurrence(corrected, "R_GEAR_DIFF", 2, FRONT_FINAL_DRIVE)
    corrected = replace_keyword_occurrence(corrected, "R_GEAR_DIFF", 3, REAR_FINAL_DRIVE)
    corrected = replace_motor_tables(corrected, candidate)
    corrected = scale_battery_resistance_carpet(corrected, "R_CHRG_BATTERY_CARPET", BATTERY_RESISTANCE_SCALE)
    corrected = scale_battery_resistance_carpet(corrected, "R_DIS_BATTERY_CARPET", BATTERY_RESISTANCE_SCALE)
    total_power_kw = float(candidate["front_peak_power_kw"]) + float(candidate["rear_peak_power_kw"])
    corrected = replace_keyword(corrected, "PWR_HEV_DRV_MAX", total_power_kw)
    corrected = replace_keyword(corrected, "PWR_EV_MODE", total_power_kw)
    corrected = replace_keyword(corrected, "PWR_DRV_THROTTLE_COEFFICIENT", total_power_kw)
    return corrected


def set_stop_time(text: str, tstop_s: float) -> str:
    """只替换首个仿真停止时间。"""
    updated, count = re.subn(r"(?m)^TSTOP\s+[-+0-9.eE]+", f"TSTOP {tstop_s:g}", text, count=1)
    if count != 1:
        raise ValueError("模板中未找到 TSTOP")
    return updated


def write_ascii_run(path: Path, text: str) -> None:
    """在 CarSim 可可靠处理的 ASCII 运行目录写入模型。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("ascii"))


def copy_solver_evidence(runtime: Path, output: Path) -> None:
    """复制能够独立复核本轮输入和求解状态的证据文件。"""
    output.mkdir(parents=True, exist_ok=True)
    for name in (
        "Run_all.par", "run.sim", "solver_stdout.txt", "solver_stderr.txt",
        "result.vs", "result.vsb", "result_echo.par", "result_log.txt",
    ):
        source = runtime / name
        if source.exists():
            shutil.copy2(source, output / name)


def run_trace_candidate(template_text: str, candidate: dict[str, float | str], output: Path, runtime: Path, tstop_s: float) -> dict[str, Any]:
    """运行一个实车 Trace 候选并计算6条实车曲线的平均评价。"""
    corrected = set_stop_time(apply_powertrain_correction(template_text, candidate), tstop_s)
    run_all = runtime / "Run_all.par"
    write_ascii_run(run_all, corrected)
    solver = run_solver(runtime, run_all)
    copy_solver_evidence(runtime, output)
    simulation_csv = output / "simulation.csv"
    convert_result(runtime, simulation_csv)
    summary = summarize_csv(simulation_csv)
    comparison = compare_full_case(simulation_csv)
    result = {
        "candidate": candidate,
        "parameter_source": PARAMETER_SOURCE,
        "front_final_drive": FRONT_FINAL_DRIVE,
        "rear_final_drive": REAR_FINAL_DRIVE,
        "solver": solver,
        "summary": summary,
        "real_data_comparison": comparison,
        "target_speed_at_5p63_kmh": TARGET_SPEED_AT_5P63_KMH,
        "speed_error_at_5p63_kmh": summary["speed_at_window_kmh"] - TARGET_SPEED_AT_5P63_KMH,
    }
    (output / "candidate_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "README.md").write_text(
        "# 动力总成识别候选\n\n"
        "本目录保存单个实车 Trace 识别候选。`Run_all.par` 为实际求解输入，"
        "`simulation.csv` 为纵向输出，`candidate_result.json` 为参数、摘要和6条实车对比。\n",
        encoding="utf-8",
    )
    return result


def select_best_candidate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """优先选择5.63 s车速最接近实车、其次平均评价分更高的候选。"""
    return min(
        results,
        key=lambda item: (
            abs(float(item["speed_error_at_5p63_kmh"])),
            -float(item["real_data_comparison"]["mean_score_pct"]),
        ),
    )


def run_acceptance_case(base_text: str, mode: str, level: float | None, output: Path, runtime: Path, tstop_s: float) -> dict[str, Any]:
    """对最佳修正版运行一个油门档位，并同步记录动力链内部通道。"""
    source = runtime / "corrected_base.par"
    write_ascii_run(source, set_stop_time(base_text, tstop_s))
    run_all = runtime / "Run_all.par"
    throttle = prepare_case(source, run_all, mode, level, tstop_s)
    inject_diagnostic_outputs(run_all)
    solver = run_solver(runtime, run_all)
    copy_solver_evidence(runtime, output)
    simulation_csv = output / "simulation.csv"
    convert_result(runtime, simulation_csv)
    channels = convert_all_channels(runtime, output / "internal_channels.csv")
    result = {
        "mode": mode,
        "throttle": throttle,
        "solver": solver,
        "summary": summarize_csv(simulation_csv),
        "internal_channel_summary_0_to_5p63s": summarize_channels(output / "internal_channels.csv", channels),
    }
    if mode == "actual_trace":
        result["real_data_comparison"] = compare_full_case(simulation_csv)
    (output / "case_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "README.md").write_text(
        f"# {mode}\n\n本目录保存修正版动力总成的单一闭环工况、内部动力链通道及评价摘要。\n",
        encoding="utf-8",
    )
    return result


def evaluate_acceptance(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """检查油门响应是否严格递增，以及实车 Trace 是否恢复合理加速能力。"""
    fixed = [item for item in cases if item["mode"] != "actual_trace"]
    speeds = [float(item["summary"]["speed_at_window_kmh"]) for item in fixed]
    strictly_increasing = all(right > left + 0.5 for left, right in zip(speeds, speeds[1:]))
    trace = next(item for item in cases if item["mode"] == "actual_trace")
    trace_speed = float(trace["summary"]["speed_at_window_kmh"])
    trace_target_met = abs(trace_speed - TARGET_SPEED_AT_5P63_KMH) <= 10.0
    return {
        "solver_all_passed": all(item["solver"]["passed"] for item in cases),
        "fixed_throttle_speed_at_5p63_kmh": speeds,
        "throttle_response_strictly_increasing": strictly_increasing,
        "actual_trace_speed_at_5p63_kmh": trace_speed,
        "actual_trace_within_10_kmh": trace_target_met,
        "passed": all(item["solver"]["passed"] for item in cases) and strictly_increasing and trace_target_met,
    }


def write_root_report(output_root: Path, candidates: list[dict[str, Any]], best: dict[str, Any], cases: list[dict[str, Any]], checks: dict[str, Any]) -> None:
    """生成面向复核的机器可读清单和中文结论报告。"""
    manifest = {
        "type": "powertrain_model_correction_and_acceptance",
        "parameter_source": PARAMETER_SOURCE,
        "manufacturer_parameters_available": False,
        "fixed_parameters": {
            "R_GEAR_FD": FRONT_FINAL_DRIVE,
            "R_GEAR_RD": REAR_FINAL_DRIVE,
            "R_GEAR_DIFF_front": FRONT_FINAL_DRIVE,
            "R_GEAR_DIFF_rear": REAR_FINAL_DRIVE,
            "battery_resistance_scale": BATTERY_RESISTANCE_SCALE,
        },
        "candidate_results": candidates,
        "selected_candidate": best["candidate"],
        "acceptance_cases": cases,
        "checks": checks,
    }
    (output_root / "powertrain_correction_result.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# CarSim 动力总成修正与闭环验收报告", "",
        "参数来源：`identified_from_real_vehicle_trace`（实车 Trace 识别值，不是厂家参数）", "",
        "## 候选扫描", "",
        "| 候选 | 前/后峰值扭矩 | 前/后峰值功率 | 5.63 s车速 | 6条实车平均精度 |", "|---|---:|---:|---:|---:|",
    ]
    for item in candidates:
        candidate = item["candidate"]
        lines.append(
            f"| {candidate['id']} | {candidate['front_peak_torque_nm']:.0f}/{candidate['rear_peak_torque_nm']:.0f} N·m | "
            f"{candidate['front_peak_power_kw']:.0f}/{candidate['rear_peak_power_kw']:.0f} kW | "
            f"{item['summary']['speed_at_window_kmh']:.2f} km/h | {item['real_data_comparison']['mean_score_pct']:.2f}% |"
        )
    lines += [
        "", "## 六档闭环", "",
        "| 工况 | 5.63 s车速 | 峰值加速度 |", "|---|---:|---:|",
    ]
    for item in cases:
        lines.append(f"| {item['mode']} | {item['summary']['speed_at_window_kmh']:.2f} km/h | {item['summary']['peak_accel_window_mps2']:.3f} m/s² |")
    lines += [
        "", f"油门响应严格递增：{'通过' if checks['throttle_response_strictly_increasing'] else '未通过'}",
        f"实车 Trace 5.63 s 误差≤10 km/h：{'通过' if checks['actual_trace_within_10_kmh'] else '未通过'}",
        f"本轮动力总成闭环：{'通过' if checks['passed'] else '未通过'}", "",
        "说明：本报告用于确认动力总成模型修正是否消除了错误占位模型造成的响应上限，不替代最终加速+滑行80%正式验收。",
    ]
    (output_root / "动力总成修正与闭环验收报告.md").write_text("\n".join(lines), encoding="utf-8")
    (output_root / "README.md").write_text(
        "# 动力总成修正输出\n\n"
        "`candidate_scan` 保存实车 Trace 识别候选；`closed_loop_acceptance` 保存最佳候选的六档闭环。"
        "根目录 JSON 是完整机器可读证据，Markdown 是中文结论。运行方式："
        "`python run_powertrain_model_correction.py`。脚本拒绝覆盖已有输出目录。\n",
        encoding="utf-8",
    )


def run_correction(template: Path, output_root: Path, runtime_root: Path, tstop_s: float) -> dict[str, Any]:
    """执行候选识别、最佳模型选择和六档闭环验收。"""
    if output_root.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output_root}")
    output_root.mkdir(parents=True)
    template_text = template.read_bytes().decode("ascii")

    candidate_results = []
    for candidate in MOTOR_CANDIDATES:
        candidate_id = str(candidate["id"])
        result = run_trace_candidate(
            template_text,
            candidate,
            output_root / "candidate_scan" / candidate_id,
            runtime_root / "candidate_scan" / candidate_id,
            tstop_s,
        )
        candidate_results.append(result)
        print(json.dumps({"candidate": candidate_id, "speed_5p63": result["summary"]["speed_at_window_kmh"]}, ensure_ascii=False))

    best = select_best_candidate(candidate_results)
    corrected_base = apply_powertrain_correction(template_text, best["candidate"])
    case_definitions = (("throttle_000pct", 0.0), ("throttle_010pct", 0.1), ("throttle_025pct", 0.25), ("throttle_050pct", 0.5), ("throttle_100pct", 1.0), ("actual_trace", None))
    cases = []
    for mode, level in case_definitions:
        cases.append(
            run_acceptance_case(
                corrected_base,
                mode,
                level,
                output_root / "closed_loop_acceptance" / mode,
                runtime_root / "closed_loop_acceptance" / mode,
                tstop_s,
            )
        )
    checks = evaluate_acceptance(cases)
    write_root_report(output_root, candidate_results, best, cases, checks)
    return {"selected_candidate": best["candidate"], "checks": checks, "output": str(output_root)}


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--tstop", type=float, default=10.0)
    args = parser.parse_args()
    result = run_correction(args.template.resolve(), args.output.resolve(), args.runtime.resolve(), args.tstop)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
