"""将 Carsim 滑行 VS/VSB 转换为项目统一 CSV。"""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path


def load_converter():
    """加载工作区已有的 Carsim VS/VSB 转换器。"""
    path = Path(__file__).resolve().parents[2] / "自动化闭环总控" / "03_数据转换" / "convert_carsim_vsb.py"
    spec = importlib.util.spec_from_file_location("carsim_converter", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载转换器：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    """读取代表性滑行结果并输出标准 CSV。"""
    converter = load_converter()
    root = Path(__file__).resolve().parent
    run_dir = root / "输出" / "滑行工况" / "carsim_coast" / "repeat_01"
    source_dir = Path("F:/Carsim/AgentRuntime/parameter_agent/coast_50_to_30/repeat_01")
    vs_path = source_dir / "result.vs"
    vsb_path = source_dir / "result.vsb"
    group, names = converter.read_vs_metadata(vs_path)
    raw_rows = converter.read_vsb(vsb_path, len(names))
    scenario = {"steer_target_deg": 0.0, "steer_step_start_s": 0.0, "accelerator_pedal_pct": 0.0}
    signal_names = ["vxdot", "ax", "speed_kmh", "accel_mps2"]
    rows = converter.build_standard_rows(raw_rows, names, group, signal_names, scenario)
    for row in rows:
        row["speed_kmh"] = row["vxdot"] * 3.6
        row["accel_mps2"] = row["ax"]
    output = run_dir / "carsim_coast.csv"
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["time_s", *signal_names], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(output)


if __name__ == "__main__":
    main()
