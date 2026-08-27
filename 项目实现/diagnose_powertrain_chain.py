"""记录 CarSim 电驱内部通道，定位油门到轮端响应链的饱和位置。"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from pathlib import Path
from runtime_paths import load_runtime_paths

from run_control_input_acceptance import prepare_case
from run_parameter_sensitivity import DEFAULT_TEMPLATE, PROJECT_ROOT, load_converter, run_solver


DEFAULT_OUTPUT = PROJECT_ROOT / "输出" / "动力链内部诊断" / "iteration_powertrain_chain_001"
DEFAULT_RUNTIME = load_runtime_paths()["runtime_root"] / "powertrain_chain" / "iteration_powertrain_chain_001"

# 这些是 CarSim 自带动力总成图表使用的合法 WRT 命令。
WRT_COMMANDS = (
    "WRT_THROTTLE", "WRT_THR_ENG", "WRT_THR_HEV",
    "WRT_M_MOTOR", "WRT_M_MOTCMD", "WRT_M_MTR_D1", "WRT_M_MTC_D1",
    "WRT_M_MTR_D2", "WRT_M_MTC_D2", "WRT_PWRMOTOR", "WRT_PWRBTTRY",
    "WRT_PWRMT_D1", "WRT_PWMTC_D1", "WRT_PWRMT_D2", "WRT_PWMTC_D2",
    "WRT_A_BTTRY", "WRT_VOCBTTRY", "WRT_SOCBTTRY", "WRT_EFFBTTRY",
    "WRT_EFFMOTOR", "WRT_AV_MT_D1", "WRT_AV_MT_D2", "WRT_VX", "WRT_AX",
)


def inject_diagnostic_outputs(path: Path) -> None:
    """在 Run_all 末尾 END 前注入动力链输出请求。"""
    text = path.read_bytes().decode("ascii")
    matches = list(re.finditer(r"(?m)^END\r*$", text))
    if not matches:
        raise ValueError("Run_all.par 末尾未找到 END")
    block = "\n! Agent powertrain chain diagnostic outputs\n" + "\n".join(WRT_COMMANDS) + "\n"
    marker = matches[-1]
    text = text[:marker.start()] + block + text[marker.start():]
    path.write_bytes(text.encode("ascii"))


def convert_all_channels(runtime: Path, output_csv: Path) -> list[str]:
    """导出 VSB 中全部通道，不推测或重命名内部变量。"""
    converter = load_converter()
    group, names = converter.read_vs_metadata(runtime / "result.vs")
    rows = converter.read_vsb(runtime / "result.vsb", len(names))
    step = float(group["XStep"])
    start = float(group.get("XStart", 0.0))
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time_s", *names])
        for index, row in enumerate(rows):
            writer.writerow([start + index * step, *row])
    return names


def summarize_channels(csv_path: Path, names: list[str], window_s: float = 5.63) -> dict:
    """汇总每个内部通道在实车有效时间窗内的最大绝对值和末值。"""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if float(row["time_s"]) <= window_s]
    summary = {}
    for name in names:
        values = [float(row[name]) for row in rows if row.get(name) not in (None, "")]
        if values:
            summary[name] = {"last": values[-1], "min": min(values), "max": max(values), "max_abs": max(abs(value) for value in values)}
    return summary


def run_diagnostics(template: Path, output_root: Path, runtime_root: Path, tstop: float) -> dict:
    """运行四档油门并比较内部请求、限幅和实际输出。"""
    if output_root.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output_root}")
    output_root.mkdir(parents=True)
    results = []
    for level in (0.1, 0.25, 0.5, 1.0):
        name = f"throttle_{int(level * 100):03d}pct"
        case_dir = output_root / name
        runtime = runtime_root / name
        case_dir.mkdir(parents=True)
        runtime.mkdir(parents=True, exist_ok=True)
        run_all = runtime / "Run_all.par"
        throttle = prepare_case(template, run_all, name, level, tstop)
        inject_diagnostic_outputs(run_all)
        solver = run_solver(runtime, run_all)
        channels = convert_all_channels(runtime, case_dir / "internal_channels.csv")
        summary = summarize_channels(case_dir / "internal_channels.csv", channels)
        for evidence in ("Run_all.par", "run.sim", "result.vs", "result.vsb", "result_echo.par", "result_log.txt", "solver_stdout.txt", "solver_stderr.txt"):
            source = runtime / evidence
            if source.exists():
                shutil.copy2(source, case_dir / evidence)
        result = {"case": name, "throttle": throttle, "solver": solver, "channel_count": len(channels), "channels": channels, "summary_0_to_5p63s": summary}
        (case_dir / "diagnostic.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (case_dir / "README.md").write_text("# 动力链内部通道\n\n`internal_channels.csv` 保存 CarSim 原始内部通道，`diagnostic.json` 保存 0–5.63 s 摘要。\n", encoding="utf-8")
        results.append(result)
        print(json.dumps({"case": name, "channels": len(channels)}, ensure_ascii=False))
    report = {"type": "powertrain_internal_chain_diagnostic", "tstop_s": tstop, "cases": results}
    (output_root / "powertrain_chain_diagnostic.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_root / "README.md").write_text("# 动力链内部诊断\n\n四档油门下记录油门、需求扭矩、电机扭矩、电机功率和电池状态，用于定位饱和层。\n", encoding="utf-8")
    return report


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--tstop", type=float, default=10.0)
    args = parser.parse_args()
    result = run_diagnostics(args.template.resolve(), args.output.resolve(), args.runtime.resolve(), args.tstop)
    print(json.dumps({"output": str(args.output.resolve()), "case_count": len(result["cases"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
