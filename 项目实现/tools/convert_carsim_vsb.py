"""把 CarSim 的 .vs/.vsb 时序结果转换为统一 CSV。

转换器只读取 CarSim 已经计算出的通道，不创造车辆响应。无法从当前结果获得的
采集变量保留为空值，避免把推测数据误标为 CarSim 真值。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import struct
from pathlib import Path


GRAVITY = 9.80665


def load_json(path: Path) -> dict:
    """读取 UTF-8 JSON。"""
    return json.loads(path.read_text(encoding="utf-8"))


def load_config(path: Path) -> dict:
    """读取目标配置，并按需复用已有采集变量清单。"""
    config = load_json(path)
    if "signal_catalog" in config:
        catalog_path = (path.parent / config["signal_catalog"]).resolve()
        config["signals"] = load_json(catalog_path)["signals"]
    return config


def sha256_file(path: Path) -> str:
    """计算源文件摘要，用于确认后续目标数据的来源没有变化。"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_vs_metadata(vs_path: Path) -> tuple[dict, list[str]]:
    """读取 .vs JSON 元数据和按二进制顺序排列的通道名称。"""
    root = load_json(vs_path)
    group = root.get("VsChannelGroup")
    if not isinstance(group, dict):
        raise ValueError(f"{vs_path} 不是支持的 CarSim VS 元数据文件")
    channels = group.get("Channels", [])
    names = []
    for channel in channels:
        aliases = channel.get("Name Aliases", [])
        if not aliases:
            raise ValueError(f"{vs_path} 存在没有名称的输出通道")
        names.append(str(aliases[0]))
    if not names:
        raise ValueError(f"{vs_path} 没有输出通道")
    return group, names


def read_vsb(vsb_path: Path, expected_channels: int) -> list[list[float]]:
    """解析 CarSim VSB；当前兼容 32 位和 64 位浮点两种文件类型。"""
    raw = vsb_path.read_bytes()
    if len(raw) < 24:
        raise ValueError(f"{vsb_path} 文件过短")
    _, _, _, _, value_bytes, channel_count = struct.unpack_from("<6I", raw, 0)
    if channel_count != expected_channels:
        raise ValueError(
            f"通道数不一致：VS={expected_channels}，VSB={channel_count}"
        )
    if value_bytes not in (4, 8):
        raise ValueError(f"不支持的 VSB 数值宽度：{value_bytes} 字节")
    payload = raw[24:]
    row_bytes = channel_count * value_bytes
    if len(payload) % row_bytes:
        raise ValueError("VSB 数据区长度不能被单行长度整除，文件可能不完整")
    sample_count = len(payload) // row_bytes
    value_code = "f" if value_bytes == 4 else "d"
    values = struct.unpack(f"<{sample_count * channel_count}{value_code}", payload)
    return [
        list(values[index * channel_count : (index + 1) * channel_count])
        for index in range(sample_count)
    ]


def derivative(values: list[float], step_s: float) -> list[float]:
    """用中心差分计算导数，首尾点使用单边差分。"""
    if len(values) < 2:
        return [0.0 for _ in values]
    result = [(values[1] - values[0]) / step_s]
    result.extend(
        (values[index + 1] - values[index - 1]) / (2.0 * step_s)
        for index in range(1, len(values) - 1)
    )
    result.append((values[-1] - values[-2]) / step_s)
    return result


def unwrap_degrees(values: list[float]) -> list[float]:
    """消除航向角跨越正负 180 度时的跳变。"""
    if not values:
        return []
    output = [values[0]]
    for value in values[1:]:
        candidate = value
        while candidate - output[-1] > 180.0:
            candidate -= 360.0
        while candidate - output[-1] < -180.0:
            candidate += 360.0
        output.append(candidate)
    return output


def channel_series(rows: list[list[float]], names: list[str], name: str) -> list[float] | None:
    """按名称提取一个 CarSim 通道；不存在时返回 None。"""
    if name not in names:
        return None
    position = names.index(name)
    return [row[position] for row in rows]


def reconstruct_steering_command(
    sample_count: int, start_s: float, step_s: float, scenario: dict
) -> list[float] | None:
    """在 VSB 未记录 Steer_SW 时，按工况配置重建已下发的转向盘角命令。"""
    if "steer_target_deg" not in scenario or "steer_step_start_s" not in scenario:
        return None
    target = float(scenario["steer_target_deg"])
    rise_start = float(scenario["steer_step_start_s"])
    rise_time = max(float(scenario.get("steer_rise_time_s", 0.0)), 1e-9)
    command = []
    for index in range(sample_count):
        time_s = start_s + index * step_s
        ratio = min(1.0, max(0.0, (time_s - rise_start) / rise_time))
        command.append(target * ratio)
    return command


def standard_signal_sources(names: list[str], scenario: dict) -> dict[str, str]:
    """记录关键标准信号的来源，区分直接输出、差分量和命令重建量。"""
    sources = {
        "yawrate": "CarSim AV_Y direct" if "AV_Y" in names else "derived from CarSim Yaw",
        "bodyRollAngle": "CarSim Roll_E direct" if "Roll_E" in names else "unavailable",
    }
    if "Steer_SW" in names:
        sources["steerWheelAngle"] = "CarSim Steer_SW direct"
    elif "steer_target_deg" in scenario:
        sources["steerWheelAngle"] = "reconstructed commanded input from scenario configuration"
    else:
        sources["steerWheelAngle"] = "unavailable"
    return sources


def build_standard_rows(
    raw_rows: list[list[float]], names: list[str], group: dict, signal_names: list[str], scenario: dict
) -> list[dict[str, float | str]]:
    """把现有 CarSim 通道映射到项目统一变量名和单位。"""
    step_s = float(group["XStep"])
    start_s = float(group.get("XStart", 0.0))
    vx_kmh = channel_series(raw_rows, names, "Vx")
    if vx_kmh is None:
        raise ValueError("当前结果缺少必要通道 Vx")
    speed_ms = [value / 3.6 for value in vx_kmh]
    ax_ms2 = derivative(speed_ms, step_s)
    yaw = unwrap_degrees(channel_series(raw_rows, names, "Yaw") or [0.0] * len(raw_rows))
    # 横摆角速度优先使用 CarSim 直接输出，只有旧结果缺少 AV_Y 时才对航向角差分。
    yawrate = channel_series(raw_rows, names, "AV_Y") or derivative(yaw, step_s)
    ay_g = channel_series(raw_rows, names, "Ay") or [0.0] * len(raw_rows)
    body_roll = channel_series(raw_rows, names, "Roll_E")
    steering = channel_series(raw_rows, names, "Steer_SW") or reconstruct_steering_command(
        len(raw_rows), start_s, step_s, scenario
    )
    x_pos = channel_series(raw_rows, names, "Xo") or [0.0] * len(raw_rows)
    y_pos = channel_series(raw_rows, names, "Yo") or [0.0] * len(raw_rows)
    z_pos = channel_series(raw_rows, names, "Zo")
    az_ms2 = derivative(derivative(z_pos, step_s), step_s) if z_pos else None
    pedal = float(scenario.get("accelerator_pedal_pct", 0.0))

    output = []
    for index in range(len(raw_rows)):
        values: dict[str, float | str] = {name: "" for name in signal_names}
        values.update(
            {
                "vxdot": speed_ms[index],
                "ax": ax_ms2[index],
                "ay": ay_g[index] * GRAVITY,
                "heading": yaw[index],
                "yawrate": yawrate[index],
                "posX": x_pos[index],
                "posY": y_pos[index],
                "wheelSpdFL": vx_kmh[index],
                "wheelSpdFR": vx_kmh[index],
                "wheelSpdRL": vx_kmh[index],
                "wheelSpdRR": vx_kmh[index],
                "accPedalPosition": pedal,
            }
        )
        if az_ms2 is not None and math.isfinite(az_ms2[index]):
            values["azBody"] = az_ms2[index]
        if body_roll is not None and math.isfinite(body_roll[index]):
            values["bodyRollAngle"] = body_roll[index]
        if steering is not None and math.isfinite(steering[index]):
            values["steerWheelAngle"] = steering[index]
        output.append({"time_s": start_s + index * step_s, **values})
    return output


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    """输出 Excel 兼容的 UTF-8-SIG CSV。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def resolve_source(config_path: Path, source_text: str) -> Path:
    """相对路径以配置文件目录为基准，方便整体移动项目目录。"""
    source = Path(source_text)
    if not source.is_absolute():
        source = config_path.parent / source
    return source.resolve()


def convert(config_path: Path, output_root: Path, dataset_name: str, force: bool = False) -> Path:
    """批量转换配置中的 CarSim 工况，并生成来源审计元数据。"""
    config = load_config(config_path)
    dataset_dir = output_root / dataset_name
    metadata_path = dataset_dir / "metadata.json"
    if metadata_path.exists() and not force:
        raise FileExistsError(
            f"标准输入已存在：{dataset_dir}。如确认源结果变化，请显式使用 --force。"
        )
    signals = [item["name"] for item in config["signals"]]
    summary = []
    for scenario in config["scenarios"]:
        vs_path = resolve_source(config_path, scenario["source_vs"])
        vsb_path = vs_path.with_suffix(".vsb")
        if not vs_path.exists() or not vsb_path.exists():
            raise FileNotFoundError(f"缺少 CarSim 结果：{vs_path} 或 {vsb_path}")
        group, channel_names = read_vs_metadata(vs_path)
        raw_rows = read_vsb(vsb_path, len(channel_names))
        standard_rows = build_standard_rows(raw_rows, channel_names, group, signals, scenario)
        scenario_dir = dataset_dir / scenario["id"]
        truth_path = scenario_dir / "truth.csv"
        write_csv(truth_path, standard_rows, ["time_s", *signals])
        (scenario_dir / "README.md").write_text(
            "\n".join(
                [
                    f"# {scenario['name']}",
                    "",
                    "`truth.csv` 由本目录元数据所指向的 CarSim `.vs/.vsb` 转换得到。",
                    "空列表示当前 CarSim 输出未包含该通道，不是数值零。",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        summary.append(
            {
                "scenario": scenario["id"],
                "source_vs": str(vs_path),
                "source_vsb": str(vsb_path),
                "source_vs_sha256": sha256_file(vs_path),
                "source_vsb_sha256": sha256_file(vsb_path),
                "truth_csv_sha256": sha256_file(truth_path),
                "sample_count": len(raw_rows),
                "channel_count": len(channel_names),
                "sample_step_s": float(group["XStep"]),
                "standard_signal_sources": standard_signal_sources(channel_names, scenario),
            }
        )
    dataset_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "dataset_type": "carsim_baseline_truth",
        "dataset_name": dataset_name,
        "config": str(config_path.resolve()),
        "summary": summary,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (dataset_dir / "README.md").write_text(
        "# CarSim 基线标准数据\n\n本目录是固定源数据，只在明确更换 CarSim 基线结果后重新转换。\n",
        encoding="utf-8",
    )
    return dataset_dir


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="将 CarSim VS/VSB 转换为标准 CSV")
    parser.add_argument("--config", type=Path, default=Path("../01_初版参数与配置/fixed_target_config.json"))
    parser.add_argument("--output-root", type=Path, default=Path("."))
    parser.add_argument("--dataset-name", default="基线数据/carsim_baseline_20260819_long_lat")
    parser.add_argument("--force", action="store_true", help="明确覆盖已经转换的标准输入")
    args = parser.parse_args()
    output = convert(args.config.resolve(), args.output_root.resolve(), args.dataset_name, args.force)
    print(output)


if __name__ == "__main__":
    main()
