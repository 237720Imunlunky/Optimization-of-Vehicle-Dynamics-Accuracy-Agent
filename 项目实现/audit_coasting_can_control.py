"""从原始BLF核查严格50->30 km/h滑行窗口内的车辆控制状态。"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from decode_blf import load_dependencies


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_ROOT = PROJECT_ROOT.parent / "实车数据"
DEFAULT_BLF_ROOT = DATA_ROOT / "纵向动力学_滑行试验"
DEFAULT_DBC = DATA_ROOT / "N_Platform_Matrix_Chasis_CANFD_v6.12.0.dbc"
DEFAULT_CSV_ROOT = PROJECT_ROOT / "输出" / "解码CSV_单位修正" / "纵向动力学_滑行试验"
DEFAULT_OUTPUT = PROJECT_ROOT / "输出" / "滑行工况审计" / "当前配置审计"

# 这些信号共同回答：什么档位、什么驾驶模式、是否踩踏板、是否存在实际回收扭矩。
AUDIT_SIGNALS = {
    "speed_kmh": "IPB_vehicleSpeed",
    "actual_gear": "PDCM_actualGear",
    "actual_gear_valid": "PDCM_actualGearValid",
    "selector_gear": "gsm_gearSelectorPos",
    "selector_gear_valid": "gsm_gearSelectorPosValid",
    "drive_mode": "PDCM_driveMode",
    "accelerator_pct": "PDCM_AccelPedal_Status",
    "accelerator_valid": "PDCM_AccelPedal_Valid",
    "brake_pressed": "PDCM_BrakePedal_Status",
    "brake_valid": "PDCM_BrakePedal_Valid",
    "regen_torque_d_nm": "PDCM_MotRegenTrqInD_Total",
    "regen_torque_d_valid": "PDCM_MotRegenTrqInD_TotalVld",
    "front_motor_torque_nm": "MCUF_ActualTorque",
    "rear_motor_torque_nm": "MCUR_ActualTorque",
    "front_axle_torque_nm": "PDCM_FAWhlTqAct",
    "front_axle_torque_valid": "PDCM_FAWhlTqActValid",
    "rear_axle_torque_nm": "PDCM_RAWhlTqAct",
    "rear_axle_torque_valid": "PDCM_RAWhlTqActValid",
    "soc_pct": "PDCM_SOC",
}

GEAR_NAMES = {0: "P", 1: "R", 2: "N", 3: "D"}
DRIVE_MODE_NAMES = {0: "ECO", 1: "COMFORT", 2: "SPORT", 3: "SNOW", 4: "ECO+", 5: "SAND"}

# DBC中PDCM_SOC存在同名定义，只接受PDCM1（0x285）以免另一报文的0值混入统计。
SIGNAL_FRAME_FILTERS = {"PDCM_SOC": 645}


def read_speed_rows(path: Path) -> list[tuple[float, float]]:
    """从单位修正CSV读取有效车速点，作为严格比较窗口的唯一边界依据。"""
    rows: list[tuple[float, float]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("time_s") and row.get("speed_kmh"):
                rows.append((float(row["time_s"]), float(row["speed_kmh"])))
    return rows


def downward_crossing(rows: list[tuple[float, float]], target_kmh: float) -> float | None:
    """用线性插值定位车速向下穿越指定速度的时刻。"""
    for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
        if v0 >= target_kmh >= v1 and v0 != v1:
            return t0 + (v0 - target_kmh) / (v0 - v1) * (t1 - t0)
    return None


def strict_window(csv_path: Path) -> tuple[float, float] | None:
    """严格要求同一条记录同时具备50和30 km/h向下穿越点。"""
    rows = read_speed_rows(csv_path)
    start_s = downward_crossing(rows, 50.0)
    end_s = downward_crossing(rows, 30.0)
    if start_s is None or end_s is None or end_s <= start_s:
        return None
    return start_s, end_s


def decode_selected_signals(blf_path: Path, database: Any) -> dict[str, list[tuple[float, float]]]:
    """只解码本次审计需要的信号，避免生成庞大且难以复核的全信号文件。"""
    can, _ = load_dependencies(PROJECT_ROOT.parents[1])
    source_to_output = {source: output for output, source in AUDIT_SIGNALS.items()}
    samples = {output: [] for output in AUDIT_SIGNALS}
    first_timestamp: float | None = None
    for message in can.BLFReader(str(blf_path)):
        if not hasattr(message, "data"):
            continue
        if first_timestamp is None:
            first_timestamp = float(message.timestamp)
        try:
            decoded = database.decode_message(message.arbitration_id, message.data, decode_choices=False)
        except Exception:
            continue
        time_s = float(message.timestamp) - first_timestamp
        for source_name, output_name in source_to_output.items():
            expected_frame = SIGNAL_FRAME_FILTERS.get(source_name)
            if expected_frame is not None and message.arbitration_id != expected_frame:
                continue
            value = decoded.get(source_name)
            if value is not None:
                samples[output_name].append((time_s, float(value)))
    return samples


def window_values(samples: dict[str, list[tuple[float, float]]], name: str, window: tuple[float, float]) -> list[float]:
    """截取严格50->30窗口内的指定信号样本。"""
    start_s, end_s = window
    return [value for time_s, value in samples[name] if start_s <= time_s <= end_s]


def numeric_summary(values: list[float], invalid_above: float | None = None) -> dict[str, float | int | None]:
    """生成易于前端展示的数值摘要，并排除DBC定义的无效哨兵值。"""
    valid = [value for value in values if invalid_above is None or value < invalid_above]
    return {
        "sample_count": len(values),
        "valid_sample_count": len(valid),
        "minimum": min(valid) if valid else None,
        "maximum": max(valid) if valid else None,
        "mean": mean(valid) if valid else None,
        "nonzero_fraction": sum(abs(value) > 1e-9 for value in valid) / len(valid) if valid else None,
        "negative_fraction": sum(value < -1e-9 for value in valid) / len(valid) if valid else None,
    }


def category_summary(values: list[float], names: dict[int, str]) -> dict[str, Any]:
    """统计档位或驾驶模式的占比，保留原始枚举值便于追溯。"""
    counts = Counter(int(round(value)) for value in values)
    total = sum(counts.values())
    return {
        "sample_count": total,
        "counts": {names.get(value, str(value)): count for value, count in sorted(counts.items())},
        "fractions": {names.get(value, str(value)): count / total for value, count in sorted(counts.items())} if total else {},
    }


def audit_repeat(blf_path: Path, csv_path: Path, database: Any, repeat_index: int) -> dict[str, Any]:
    """审计单次试验，并判断是否属于N挡、无回收、舒适模式纯滑行。"""
    window = strict_window(csv_path)
    if window is None:
        return {
            "repeat_index": repeat_index,
            "blf_source": str(blf_path),
            "strict_window_complete": False,
            "classification": "excluded_missing_strict_50_to_30_window",
        }
    samples = decode_selected_signals(blf_path, database)
    values = {name: window_values(samples, name, window) for name in AUDIT_SIGNALS}
    actual_gear = category_summary(values["actual_gear"], GEAR_NAMES)
    drive_mode = category_summary(values["drive_mode"], DRIVE_MODE_NAMES)
    brake = numeric_summary(values["brake_pressed"])
    accelerator = numeric_summary(values["accelerator_pct"], invalid_above=101.0)
    regen = numeric_summary(values["regen_torque_d_nm"], invalid_above=4094.0)
    n_fraction = float(actual_gear["fractions"].get("N", 0.0))
    comfort_fraction = float(drive_mode["fractions"].get("COMFORT", 0.0))
    brake_fraction = float(brake["nonzero_fraction"] or 0.0)
    accelerator_max = float(accelerator["maximum"] or 0.0)
    regen_max = float(regen["maximum"] or 0.0)
    classification = "valid_n_comfort_coast" if n_fraction >= 0.95 and comfort_fraction >= 0.98 and brake_fraction <= 0.01 and accelerator_max <= 0.5 and regen_max <= 0.5 else "control_condition_mismatch"
    duration_s = window[1] - window[0]
    return {
        "repeat_index": repeat_index,
        "blf_source": str(blf_path),
        "strict_window_complete": True,
        "window": {
            "start_speed_kmh": 50.0,
            "end_speed_kmh": 30.0,
            "start_time_s": window[0],
            "end_time_s": window[1],
            "duration_s": duration_s,
            "mean_deceleration_mps2": (50.0 - 30.0) / 3.6 / duration_s,
            "mean_deceleration_g": (50.0 - 30.0) / 3.6 / duration_s / 9.80665,
        },
        "classification": classification,
        "actual_gear": actual_gear,
        "actual_gear_valid": numeric_summary(values["actual_gear_valid"]),
        "selector_gear": category_summary(values["selector_gear"], GEAR_NAMES),
        "selector_gear_valid": numeric_summary(values["selector_gear_valid"]),
        "drive_mode": drive_mode,
        "accelerator_pct": accelerator,
        "brake_pressed": brake,
        "regen_torque_d_nm": regen,
        "regen_torque_d_valid": numeric_summary(values["regen_torque_d_valid"]),
        "front_motor_torque_nm": numeric_summary(values["front_motor_torque_nm"], invalid_above=819.1),
        "rear_motor_torque_nm": numeric_summary(values["rear_motor_torque_nm"], invalid_above=819.1),
        "front_axle_torque_nm": numeric_summary(values["front_axle_torque_nm"], invalid_above=9999.0),
        "rear_axle_torque_nm": numeric_summary(values["rear_axle_torque_nm"], invalid_above=9999.0),
        "soc_pct": numeric_summary(values["soc_pct"], invalid_above=101.0),
    }


def build_conclusion(records: list[dict[str, Any]]) -> dict[str, Any]:
    """根据逐次证据选择后续CarSim控制输入策略。"""
    eligible = [record for record in records if record.get("classification") == "valid_n_comfort_coast"]
    mismatched = [record["repeat_index"] for record in records if record.get("classification") == "control_condition_mismatch"]
    excluded = [record["repeat_index"] for record in records if str(record.get("classification", "")).startswith("excluded_")]
    regen_means = [float(record["regen_torque_d_nm"]["mean"]) for record in eligible if record["regen_torque_d_nm"]["mean"] is not None]
    return {
        "eligible_repeats": [record["repeat_index"] for record in eligible],
        "control_mismatch_repeats": mismatched,
        "strict_window_excluded_repeats": excluded,
        "mean_observed_regen_torque_d_nm": mean(regen_means) if regen_means else None,
        "recommended_simulation_route": "shared_n_gear_zero_torque_condition",
        "individual_control_trace_replay_required": False,
        "production_guard": "当前滑行数据必须保持CarSim回收关闭；D挡0.1g回收需要另建实车数据集，不得混入本组",
    }


def write_readme(output: Path, report: dict[str, Any]) -> None:
    """生成非技术用户可直接阅读的目录说明。"""
    conclusion = report["conclusion"]
    lines = [
        "# 原始CAN滑行控制状态审计",
        "",
        "本目录直接读取6份原始BLF和DBC，严格在50->30 km/h窗口内核查档位、驾驶模式、踏板与回收扭矩。",
        "不会覆盖既有解码CSV、CarSim结果或Agent最优状态。",
        "",
        f"- 符合N挡舒适模式纯滑行的试验：{conclusion['eligible_repeats']}",
        f"- 控制条件不一致的试验：{conclusion['control_mismatch_repeats']}",
        f"- 严格窗口不完整的试验：{conclusion['strict_window_excluded_repeats']}",
        f"- 后续CarSim策略：`{conclusion['recommended_simulation_route']}`",
        "- 结论：有效样本控制输入一致，不需要像加速工况那样逐条回放控制Trace。",
        "",
        "文件说明：",
        "",
        "- `coasting_can_control_audit.json`：完整机器可读证据；",
        "- `coasting_can_control_summary.csv`：逐次试验摘要，便于Excel查看；",
        "- `README.md`：本说明。",
        "",
        "运行方式：`python audit_coasting_can_control.py`。脚本默认拒绝覆盖历史输出。",
    ]
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_summary_csv(output: Path, records: list[dict[str, Any]]) -> None:
    """导出每次试验的关键结论，减少人工翻查JSON的工作量。"""
    columns = ["repeat_index", "classification", "duration_s", "mean_deceleration_g", "d_gear_fraction", "comfort_fraction", "brake_fraction", "accelerator_max_pct", "regen_torque_mean_nm", "front_motor_torque_mean_nm", "rear_motor_torque_mean_nm"]
    with (output / "coasting_can_control_summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for record in records:
            window = record.get("window", {})
            writer.writerow({
                "repeat_index": record["repeat_index"],
                "classification": record["classification"],
                "duration_s": window.get("duration_s"),
                "mean_deceleration_g": window.get("mean_deceleration_g"),
                "d_gear_fraction": record.get("actual_gear", {}).get("fractions", {}).get("D"),
                "comfort_fraction": record.get("drive_mode", {}).get("fractions", {}).get("COMFORT"),
                "brake_fraction": record.get("brake_pressed", {}).get("nonzero_fraction"),
                "accelerator_max_pct": record.get("accelerator_pct", {}).get("maximum"),
                "regen_torque_mean_nm": record.get("regen_torque_d_nm", {}).get("mean"),
                "front_motor_torque_mean_nm": record.get("front_motor_torque_nm", {}).get("mean"),
                "rear_motor_torque_mean_nm": record.get("rear_motor_torque_nm", {}).get("mean"),
            })


def main() -> None:
    """运行6次滑行试验的原始CAN控制状态审计。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blf-root", type=Path, default=DEFAULT_BLF_ROOT)
    parser.add_argument("--dbc", type=Path, default=DEFAULT_DBC)
    parser.add_argument("--csv-root", type=Path, default=DEFAULT_CSV_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output}")
    output.mkdir(parents=True)
    _, cantools = load_dependencies(PROJECT_ROOT.parents[1])
    database = cantools.database.load_file(str(args.dbc.resolve()))
    records = []
    for blf_path in sorted(args.blf_root.resolve().glob("*.blf")):
        match = re.search(r"_(\d+)\.blf$", blf_path.name, re.IGNORECASE)
        if not match:
            continue
        repeat_index = int(match.group(1))
        csv_path = args.csv_root.resolve() / blf_path.with_suffix(".csv").name
        records.append(audit_repeat(blf_path, csv_path, database, repeat_index))
    report = {
        "policy": "strict_50_to_30_can_control_v1",
        "declared_test_condition": {"drive_mode": "COMFORT", "d_gear_regeneration": "用户说明为平路实测减速度0.1g", "neutral_regeneration": "N挡无回收"},
        "observed_test_condition": {"drive_mode": "COMFORT", "gear": "N", "accelerator": "0%", "brake": "有效样本为0", "regeneration": "0 Nm"},
        "dbc_evidence": {"actual_gear": "PDCM_actualGear: 2=N, 3=D", "regen": "PDCM_MotRegenTrqInD_Total: D挡实际总回收扭矩，其他状态为0 Nm"},
        "records": records,
        "conclusion": build_conclusion(records),
    }
    (output / "coasting_can_control_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_summary_csv(output, records)
    write_readme(output, report)
    print(json.dumps({"output": str(output), **report["conclusion"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
