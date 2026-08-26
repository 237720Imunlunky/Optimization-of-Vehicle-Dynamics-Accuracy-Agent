"""把实车油门 Trace 转成 Carsim 可读的时间—踏板控制表。"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


def read_trace(path: Path, step_s: float = 0.1) -> list[tuple[float, float]]:
    """读取油门信号并按固定时间步前向保持，输出归一化踏板。"""
    raw = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("time_s") and row.get("accel_pedal_pct"):
                raw.append((float(row["time_s"]), max(0.0, min(100.0, float(row["accel_pedal_pct"]))) / 100.0))
    if not raw:
        raise ValueError(f"没有有效油门信号：{path}")
    end = raw[-1][0]
    output = []
    index = 0
    latest = raw[0][1]
    for i in range(int(end / step_s) + 1):
        target = i * step_s
        while index < len(raw) and raw[index][0] <= target:
            latest = raw[index][1]
            index += 1
        output.append((round(target, 6), latest))
    return output


def replace_throttle(text: str, table: list[tuple[float, float]]) -> str:
    """替换展开参数中的油门函数表。"""
    lines = ["! Trace-derived accelerator pedal control", "THROTTLE_ENGINE_TABLE LINEAR_FLAT"]
    lines.extend(f"{time_s:.6f}, {value:.8f}" for time_s, value in table)
    lines.append("ENDTABLE")
    replacement = "\n".join(lines)
    pattern = r"THROTTLE_ENGINE_TABLE LINEAR_FLAT\r?\n.*?ENDTABLE"
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise ValueError("没有找到 Carsim 油门时间表")
    return updated


def main() -> None:
    """生成 0-100 工况 Trace 控制文件。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    template = Path("F:/Carsim/AgentRuntime/parameter_agent/iteration_222/carsim2023_conditions_20260823_231653/condition_01_0_to_100_wot/repeat_01/Run_all.par")
    output = root / "输出" / "Trace控制输入" / "condition_01_trace_control"
    table = read_trace(args.trace.resolve())
    text = template.read_bytes().decode("utf-8")
    text = replace_throttle(text, table)
    text = re.sub(r"^TSTOP\s+[-+0-9.]+", f"TSTOP {table[-1][0]:.3f}", text, count=1, flags=re.MULTILINE)
    text = text.replace("condition_01_0_to_100_wot", "condition_01_trace_control")
    output.mkdir(parents=True, exist_ok=True)
    (output / "Run_all.par").write_bytes(text.encode("utf-8"))
    with (output / "trace_control.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time_s", "throttle_fraction"])
        writer.writerows(table)
    metadata = {"source_trace": str(args.trace.resolve()), "sample_step_s": 0.1, "samples": len(table), "max_throttle_fraction": max(value for _, value in table), "control_signal": "accel_pedal_pct -> THROTTLE_ENGINE_TABLE"}
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()

