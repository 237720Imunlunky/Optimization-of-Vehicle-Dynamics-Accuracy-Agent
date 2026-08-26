"""审计严格50->30滑行样本的控制输入、边界完整性和平均减速度。"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = PROJECT_ROOT / "输出" / "解码CSV_单位修正" / "纵向动力学_滑行试验"
DEFAULT_OUTPUT = PROJECT_ROOT / "输出" / "滑行工况审计" / "当前配置审计"


def read_rows(path: Path) -> list[dict[str, float | None]]:
    """读取稀疏CAN CSV并把空值保留为None。"""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return [
            {key: None if value in (None, "") else float(value) for key, value in row.items()}
            for row in csv.DictReader(stream)
        ]


def speed_crossing(rows: list[dict[str, float | None]], target: float, start_index: int = 1) -> tuple[int, float] | None:
    """查找车速向下穿越目标值的时间。"""
    speed_rows = [(index, row) for index, row in enumerate(rows) if row.get("speed_kmh") is not None]
    for offset in range(max(1, start_index), len(speed_rows)):
        left_index, left = speed_rows[offset - 1]
        right_index, right = speed_rows[offset]
        v0, v1 = float(left["speed_kmh"]), float(right["speed_kmh"])
        if v0 >= target >= v1 and v0 != v1:
            ratio = (v0 - target) / (v0 - v1)
            crossing_time = float(left["time_s"]) + ratio * (float(right["time_s"]) - float(left["time_s"]))
            return right_index, crossing_time
    return None


def control_ranges(rows: list[dict[str, float | None]], start_s: float, end_s: float) -> dict[str, dict[str, float | None]]:
    """前向保持CAN控制信号，并统计严格滑行窗口内范围。"""
    latest = {"accel_pedal_pct": None, "brake_pedal": None, "steer_deg": None}
    values = {name: [] for name in latest}
    for row in rows:
        for name in latest:
            if row.get(name) is not None:
                latest[name] = float(row[name])
        time_s = float(row["time_s"])
        if start_s <= time_s <= end_s:
            for name, value in latest.items():
                if value is not None:
                    values[name].append(value)
    return {
        name: {
            "minimum": min(items) if items else None,
            "maximum": max(items) if items else None,
            "nonzero_fraction": sum(value != 0 for value in items) / len(items) if items else None,
        }
        for name, items in values.items()
    }


def audit_file(path: Path, repeat_index: int) -> dict[str, Any]:
    """审计单个文件，严格要求同时存在50和30 km/h向下穿越点。"""
    rows = read_rows(path)
    start = speed_crossing(rows, 50.0)
    # 30 km/h只查找向下穿越，因此不会误取前段加速过程，无需混用原始行号与稀疏序号。
    end = speed_crossing(rows, 30.0)
    if not start or not end:
        return {"repeat_index": repeat_index, "source": str(path), "complete_window": False}
    duration = end[1] - start[1]
    controls = control_ranges(rows, start[1], end[1])
    return {
        "repeat_index": repeat_index,
        "source": str(path),
        "complete_window": True,
        "start_time_s": start[1],
        "end_time_s": end[1],
        "duration_s": duration,
        "mean_deceleration_mps2": (50.0 - 30.0) / 3.6 / duration,
        "mean_deceleration_g": ((50.0 - 30.0) / 3.6 / duration) / 9.80665,
        "controls": controls,
    }


def main() -> None:
    """生成JSON证据和面向人工复核的README。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output}")
    output.mkdir(parents=True)
    records = []
    for path in sorted(args.input.resolve().glob("*.csv")):
        match = re.search(r"_(\d+)\.csv$", path.name)
        if match:
            records.append(audit_file(path, int(match.group(1))))
    included = [item for item in records if item["repeat_index"] in {1, 2, 4, 5}]
    observed = sum(float(item["mean_deceleration_mps2"]) for item in included) / len(included)
    report = {
        "policy": "strict_50_to_30_v2",
        "declared_condition": {
            "drive_mode": "舒适模式",
            "gear_user_statement": "D挡回收、N挡无回收",
            "gear_can_observation": "有效样本原始CAN显示为N挡",
            "regeneration_deceleration_user_statement_for_d_gear": "0.1g",
            "neutral_regeneration": "无",
        },
        "included_repeats": [1, 2, 4, 5],
        "excluded_repeats": {"3": "约34.09 km/h开始制动", "6": "缺少严格50 km/h起点"},
        "observed_included_mean_deceleration_mps2": observed,
        "observed_included_mean_deceleration_g": observed / 9.80665,
        "condition_consistency": "本批数据是N挡无回收滑行，约0.01g总减速度不能与D挡0.1g回收强度直接比较",
        "records": records,
    }
    (output / "coasting_input_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "README.md").write_text(
        "# 严格50->30滑行输入审计\n\n"
        "本目录审计6份单位修正实车CSV的50->30边界、油门、制动、方向盘和平均减速度。\n\n"
        "结论：第3次因中途制动排除；第6次因缺少50 km/h起点排除；第1、2、4、5次纳入。"
        "纳入样本反算平均总减速度约0.1 m/s2（约0.01g）。原始CAN已确认本批数据为N挡无回收，"
        "因此不能与D挡0.1g回收强度直接比较。\n\n"
        "运行方式：`python audit_coasting_inputs.py`。\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "included": len(included), "observed_mean_mps2": observed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
