"""将 Trace 控制 Carsim 结果转换成统一 CSV。"""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path


def load_converter():
    """加载工作区已有 VS/VSB 转换器。"""
    path = Path(__file__).resolve().parents[2] / "自动化闭环总控" / "03_数据转换" / "convert_carsim_vsb.py"
    spec = importlib.util.spec_from_file_location("carsim_converter", path)
    if spec is None or spec.loader is None:
        raise ImportError("无法加载 Carsim 转换器")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    """输出标准 CSV。"""
    converter = load_converter()
    root = Path(__file__).resolve().parent
    run_dir = root / "输出" / "Trace控制输入" / "condition_01_trace_control" / "carsim_run"
    source_dir = Path("F:/Carsim/AgentRuntime/parameter_agent/condition_01_trace_control/repeat_01")
    group, names = converter.read_vs_metadata(source_dir / "result.vs")
    raw_rows = converter.read_vsb(source_dir / "result.vsb", len(names))
    signals = ["vxdot", "ax", "speed_kmh", "accel_mps2"]
    rows = converter.build_standard_rows(raw_rows, names, group, signals, {"accelerator_pedal_pct": 0.0})
    for row in rows:
        row["speed_kmh"] = row["vxdot"] * 3.6
        row["accel_mps2"] = row["ax"]
    output = run_dir / "carsim_trace.csv"
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["time_s", *signals], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(output)


if __name__ == "__main__":
    main()

