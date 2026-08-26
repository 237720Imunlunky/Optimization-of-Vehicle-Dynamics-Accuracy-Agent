"""使用统一修正版车辆模型执行纵向动力学正式联合验收。"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from build_trace_control import read_trace, replace_throttle
from evaluate_longitudinal import aggregate, compare_pair
from config_loader import load_project_config
from run_coast_parameter_scan import replace_all_scalar
from run_coast_simulation import build_par
from run_parameter_sensitivity import PROJECT_ROOT, convert_result, run_solver


DEFAULT_TEMPLATE = PROJECT_ROOT / "输出" / "动力总成修正" / "当前配置模型" / "closed_loop_acceptance" / "actual_trace" / "Run_all.par"
DEFAULT_TRUTH_ROOT = PROJECT_ROOT / "输出" / "解码CSV_单位修正"
DEFAULT_OUTPUT = PROJECT_ROOT / "输出" / "正式联合基线" / "当前配置基线"
DEFAULT_RUNTIME = Path("F:/Carsim/AgentRuntime/parameter_agent/formal_longitudinal/当前配置基线")
RR_C_BASELINE = 0.0065
TRACE_STEP_S = 0.02
EVIDENCE_FILES = (
    "Run_all.par", "run.sim", "result.vs", "result.vsb", "result_echo.par",
    "result_end.par", "result_log.txt", "solver_stdout.txt", "solver_stderr.txt",
)


def load_config() -> dict[str, Any]:
    """读取项目唯一配置入口。"""
    return load_project_config()


def first_valid_speed(path: Path) -> float:
    """读取实车文件首个有效车速，供60-100仿真使用相同起点。"""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("speed_kmh") not in (None, ""):
                return float(row["speed_kmh"])
    raise ValueError(f"实车文件没有有效车速：{path}")


def last_time(path: Path) -> float:
    """读取实车记录结束时间，用于设置足够的仿真时长。"""
    result = 0.0
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("time_s") not in (None, ""):
                result = max(result, float(row["time_s"]))
    return result


def replace_single_scalar(text: str, keyword: str, value: float) -> str:
    """替换唯一标量并校验数量，避免静默使用错误初值。"""
    pattern = rf"(?m)^(?P<prefix>\s*{re.escape(keyword)}\s+)(?P<value>[-+0-9.eE]+)(?P<suffix>\s*(?:[;!].*)?)$"
    updated, count = re.subn(pattern, rf"\g<prefix>{value:.8g}\g<suffix>", text, count=1)
    if count != 1:
        raise ValueError(f"{keyword} 预期替换1处，实际替换{count}处")
    return updated


def build_acceleration_par(template: Path, truth: Path, role: str, destination: Path) -> dict[str, Any]:
    """生成一条与实车油门Trace和实际起点对应的加速输入文件。"""
    text = template.read_bytes().decode("ascii")
    text, rr_count = replace_all_scalar(text, "RR_C", RR_C_BASELINE)
    if rr_count != 4:
        raise ValueError(f"RR_C预期替换4处，实际替换{rr_count}处")

    table = read_trace(truth, step_s=TRACE_STEP_S)
    text = replace_throttle(text, table)
    initial_speed = 0.0 if role == "zero_to_100" else first_valid_speed(truth)
    text = replace_single_scalar(text, "SV_VXS", initial_speed)
    text = replace_single_scalar(text, "TSTOP", max(last_time(truth), table[-1][0]) + 1.0)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(text.encode("ascii"))
    return {"initial_speed_kmh": initial_speed, "trace_step_s": TRACE_STEP_S, "trace_samples": len(table)}


def copy_evidence(runtime: Path, archive: Path) -> None:
    """分类归档求解输入、原始输出和日志，支持后续独立复核。"""
    archive.mkdir(parents=True, exist_ok=True)
    for name in EVIDENCE_FILES:
        source = runtime / name
        if source.exists():
            shutil.copy2(source, archive / name)


def write_trace_csv(path: Path, truth: Path) -> None:
    """保存实际送入CarSim的离散油门表。"""
    table = read_trace(truth, step_s=TRACE_STEP_S)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time_s", "throttle_fraction"])
        writer.writerows(table)


def run_acceleration_case(template: Path, truth: Path, role: str, archive: Path, runtime: Path, rules: dict[str, Any]) -> dict[str, Any]:
    """执行并评价一条0-100或60-100实车Trace闭环仿真。"""
    runtime.mkdir(parents=True, exist_ok=True)
    inputs = build_acceleration_par(template, truth, role, runtime / "Run_all.par")
    solver = run_solver(runtime, runtime / "Run_all.par")
    copy_evidence(runtime, archive)
    simulation_csv = archive / "simulation.csv"
    convert_result(runtime, simulation_csv)
    write_trace_csv(archive / "trace_control.csv", truth)
    comparison = compare_pair(truth, simulation_csv, role, rules)
    result = {"solver": solver, "inputs": inputs, "comparison": comparison}
    (archive / "case_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (archive / "README.md").write_text(
        "# 单条加速Trace闭环结果\n\n"
        "`trace_control.csv` 是实际送入CarSim的油门，`Run_all.par` 是完整求解输入，"
        "`simulation.csv` 是纵向输出，`case_result.json` 是正式规则评价结果。\n",
        encoding="utf-8",
    )
    return comparison


def run_all_acceleration(template: Path, truth_root: Path, output: Path, runtime: Path, rules: dict[str, Any]) -> list[dict[str, Any]]:
    """逐条运行两类加速工况，确保六次试验均有独立仿真证据。"""
    definitions = (
        ("zero_to_100", truth_root / "纵向动力学_加速试验" / "0-100全油门起步加速", "zero_to_100"),
        ("overtaking", truth_root / "纵向动力学_加速试验" / "60-100超越加速", "overtaking"),
    )
    comparisons: list[dict[str, Any]] = []
    for folder, source, role in definitions:
        files = sorted(source.glob("*.csv"))
        if len(files) != 6:
            raise ValueError(f"{folder} 实车文件应为6条，实际为{len(files)}")
        for index, truth in enumerate(files, start=1):
            name = f"repeat_{index:02d}"
            comparison = run_acceleration_case(
                template, truth, role, output / folder / name, runtime / folder / name, rules,
            )
            comparisons.append(comparison)
            print(json.dumps({"case": f"{folder}/{name}", "score_pct": comparison["maneuver_score_pct"]}, ensure_ascii=False))
    return comparisons


def run_coasting_case(
    template: Path, truth_root: Path, output: Path, runtime: Path, rules: dict[str, Any],
    condition: dict[str, Any],
) -> list[dict[str, Any]]:
    """用同一参数基线运行一次确定性滑行，并与六次实车试验分别比较。"""
    runtime.mkdir(parents=True, exist_ok=True)
    run_all = runtime / "Run_all.par"
    build_par(template, run_all, float(condition["simulation_duration_s"]), condition=condition)
    text = run_all.read_bytes().decode("ascii")
    text, count = replace_all_scalar(text, "RR_C", RR_C_BASELINE)
    if count != 4:
        raise ValueError(f"RR_C预期替换4处，实际替换{count}处")
    run_all.write_bytes(text.encode("ascii"))
    solver = run_solver(runtime, run_all)
    copy_evidence(runtime, output)
    simulation_csv = output / "simulation.csv"
    convert_result(runtime, simulation_csv)
    truth_files = sorted((truth_root / "纵向动力学_滑行试验").glob("*.csv"))
    if len(truth_files) != 6:
        raise ValueError(f"coasting 实车文件应为6条，实际为{len(truth_files)}")
    comparisons = [compare_pair(path, simulation_csv, "coasting", rules, condition["window_kmh"]) for path in truth_files]
    result = {"solver": solver, "RR_C": RR_C_BASELINE, "comparisons": comparisons}
    (output / "case_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "README.md").write_text(
        "# 50→30 km/h滑行正式基线\n\n"
        "本目录与加速工况使用同一动力总成和四轮RR_C=0.0065。"
        "`simulation.csv` 与六条实车数据分别计算时间、距离、R²和NRMSE。\n",
        encoding="utf-8",
    )
    return comparisons


def mean_metric_scores(results: list[dict[str, Any]], role: str) -> dict[str, float]:
    """按工况汇总各指标平均得分，便于定位未通过项。"""
    selected = [item for item in results if item["role"] == role]
    names = sorted({name for item in selected for name in item["metrics"]})
    return {name: sum(item["metrics"][name]["score_pct"] for item in selected) / len(selected) for name in names}


def write_report(output: Path, results: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    """生成面向非技术使用者的结论报告和机器可读完整结果。"""
    metric_summary = {role: mean_metric_scores(results, role) for role in ("zero_to_100", "overtaking", "coasting")}
    actual_starts = [item["actual_start_speed_kmh"] for item in results if item["role"] == "overtaking"]
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model_baseline": {
            "powertrain": "当前配置模型",
            "parameter_source": "identified_from_real_vehicle_trace",
            "RR_C_all_wheels": RR_C_BASELINE,
        },
        "evaluation_formula": "maneuver=0.4*time_domain+0.6*feature; longitudinal=0.5833*acceleration+0.4167*coasting",
        "results": results,
        "mean_metric_scores_pct": metric_summary,
        "summary": summary,
        "overtaking_actual_start_speeds_kmh": actual_starts,
    }
    (output / "formal_acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 纵向动力学正式联合验收报告", "",
        f"纵向综合精度：**{summary['longitudinal_score_pct']:.2f}%**",
        f"正式阈值：{summary['formal_acceptance_threshold_pct']:.0f}%",
        f"结论：**{'通过' if summary['formal_passed'] else '未通过'}**", "",
        "## 统一模型基线", "",
        "- 动力总成：`当前配置模型`（实车Trace识别值，不冒充厂家参数）",
        f"- 四轮滚阻：`RR_C={RR_C_BASELINE}`",
        "- 加速控制：每条实车文件自己的油门Trace",
        "- 聚合规则：单工况40%时域+60%特征；纵向为加速0.5833+滑行0.4167", "",
        "## 分组结果", "",
        f"- 加速平均精度：{summary['group_scores_pct']['acceleration']:.2f}%",
        f"- 滑行平均精度：{summary['group_scores_pct']['coasting']:.2f}%", "",
        "## 数据边界", "",
        "60-100实车文件并非全部恰好从60 km/h开始，实际首点为："
        + ", ".join(f"{value:.2f}" for value in actual_starts)
        + " km/h。对应仿真使用相同实际起点，报告不把缺失区间伪造为实测数据。", "",
        "## 文件说明", "",
        "每个 `repeat_*` 目录保存独立控制表、Run_all、CarSim原始结果、标准CSV和评价JSON；"
        "`formal_acceptance.json` 保存全部18组比较和最终聚合结果。",
    ]
    (output / "正式联合验收报告.md").write_text("\n".join(lines), encoding="utf-8")
    (output / "README.md").write_text(
        "# 正式联合基线（当前配置）\n\n"
        "本目录保存0-100六次、60-100六次、滑行六次对比的统一参数正式验收。\n\n"
        "运行方式：`python run_formal_longitudinal_acceptance.py`。脚本拒绝覆盖已有输出。\n",
        encoding="utf-8",
    )


def run_acceptance(template: Path, truth_root: Path, output: Path, runtime: Path) -> dict[str, Any]:
    """编排全部正式工况并返回纵向综合结果。"""
    if output.exists():
        raise FileExistsError(f"归档目录已存在，拒绝覆盖：{output}")
    if runtime.exists():
        raise FileExistsError(f"运行目录已存在，拒绝混用旧结果：{runtime}")
    output.mkdir(parents=True)
    runtime.mkdir(parents=True)
    config = load_config()
    rules = config["metric_thresholds"]
    results = run_all_acceleration(template, truth_root, output, runtime, rules)
    results.extend(run_coasting_case(
        template, truth_root, output / "coasting", runtime / "coasting", rules,
        config["agent"]["coasting_test_condition"],
    ))
    summary = aggregate(results, config)
    write_report(output, results, summary)
    return summary


def main() -> None:
    """解析路径参数并执行正式联合验收。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--truth-root", type=Path, default=DEFAULT_TRUTH_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    args = parser.parse_args()
    summary = run_acceptance(args.template.resolve(), args.truth_root.resolve(), args.output.resolve(), args.runtime.resolve())
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
