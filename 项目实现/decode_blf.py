"""把实车 BLF 按 DBC 解码成统一 CSV，供评价和 Carsim 输入适配使用。"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any


STANDARD_GRAVITY_MPS2 = 9.80665


def load_dependencies(project_root: Path):
    """加载项目内置 CAN 解码依赖，避免污染系统 Python 环境。"""
    tool_path = project_root / "_tools" / "blf_parser"
    if str(tool_path) not in sys.path:
        sys.path.insert(0, str(tool_path))
    import can  # type: ignore
    import cantools  # type: ignore
    return can, cantools


def choose_file_signal(samples: dict[str, list[tuple[float, Any]]], candidates: list[str]) -> str | None:
    """为整个文件选择一个最高优先级有效信号，避免不同单位的候选逐报文混写。"""
    for name in candidates:
        if samples.get(name):
            return name
    return None


def normalize_signal_value(output_name: str, source_unit: str | None, value: Any) -> float:
    """依据DBC单位转换为项目统一单位，目前重点处理g到m/s2。"""
    numeric = float(value)
    normalized_unit = (source_unit or "").strip().lower().replace("^", "")
    if output_name == "accel_mps2" and normalized_unit == "g":
        return numeric * STANDARD_GRAVITY_MPS2
    return numeric


def build_signal_unit_map(database: Any) -> dict[str, str | None]:
    """从DBC数据库提取信号单位，供解码结果标准化和审计。"""
    units: dict[str, str | None] = {}
    for message in database.messages:
        for signal in message.signals:
            units.setdefault(signal.name, signal.unit)
    return units


def decode_file(blf_path: Path, dbc_path: Path, output_path: Path, config: dict) -> dict:
    """解码单个 BLF，并把原始信号映射成项目统一列名。"""
    can, cantools = load_dependencies(Path(__file__).resolve().parents[2])
    database = cantools.database.load_file(str(dbc_path))
    signal_units = build_signal_unit_map(database)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    aliases = config["signals"]
    columns = ["time_s", "speed_kmh", "accel_mps2", "accel_pedal_pct", "brake_pedal", "steer_deg",
               "wheel_speed_fl_kmh", "wheel_speed_fr_kmh", "wheel_speed_rl_kmh", "wheel_speed_rr_kmh"]
    samples = {
        output_name: {name: [] for name in candidates}
        for output_name, candidates in aliases.items()
    }
    first_timestamp = None
    decode_errors = 0
    for message in can.BLFReader(str(blf_path)):
        if not hasattr(message, "data"):
            continue
        if first_timestamp is None:
            first_timestamp = float(message.timestamp)
        try:
            decoded = database.decode_message(message.arbitration_id, message.data, decode_choices=False)
        except Exception:
            decode_errors += 1
            continue
        time_s = round(float(message.timestamp) - first_timestamp, 6)
        for output_name, candidates in aliases.items():
            for source_name in candidates:
                if source_name in decoded and decoded[source_name] is not None:
                    samples[output_name][source_name].append((time_s, decoded[source_name]))

    # 文件级锁定一个信号源，再按时间合并不同输出列，杜绝候选信号相互覆盖。
    selected_signals: dict[str, dict[str, Any]] = {}
    rows_by_time: dict[float, dict[str, Any]] = {}
    for output_name, candidates in aliases.items():
        source_name = choose_file_signal(samples[output_name], candidates)
        if source_name is None:
            continue
        source_unit = signal_units.get(source_name)
        source_samples = samples[output_name][source_name]
        selected_signals[output_name] = {
            "source_signal": source_name,
            "source_unit": source_unit,
            "output_unit": "m/s2" if output_name == "accel_mps2" else source_unit,
            "sample_count": len(source_samples),
            "conversion": "multiply_by_9.80665" if output_name == "accel_mps2" and (source_unit or "").strip().lower() == "g" else "identity",
        }
        for time_s, value in source_samples:
            row = rows_by_time.setdefault(time_s, {"time_s": time_s})
            row[output_name] = normalize_signal_value(output_name, source_unit, value)
    rows = [rows_by_time[time_s] for time_s in sorted(rows_by_time)]

    with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return {"source": str(blf_path), "output": str(output_path), "rows": len(rows), "decode_errors": decode_errors,
            "available_columns": [column for column in columns if any(row.get(column) is not None for row in rows)],
            "selected_signals": selected_signals}


def decode_all(data_root: Path, dbc_path: Path, output_root: Path, config: dict) -> list[dict]:
    """批量解码所有 BLF，保留工况目录结构并生成 manifest。"""
    manifest = []
    for blf_path in sorted(data_root.rglob("*.blf")):
        relative = blf_path.relative_to(data_root).with_suffix(".csv")
        manifest.append(decode_file(blf_path, dbc_path, output_root / relative, config))
    (output_root / "manifest.json").parent.mkdir(parents=True, exist_ok=True)
    (output_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
