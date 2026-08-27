"""扫描修正版模型的滚动阻力，匹配实车50→30 km/h滑行曲线。"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from pathlib import Path
from runtime_paths import load_runtime_paths
from typing import Any

from config_loader import load_project_config
from evaluate_longitudinal import compare_pair
from run_coast_simulation import DEFAULT_TEMPLATE, build_par
from run_parameter_sensitivity import PROJECT_ROOT, convert_result, run_solver


DEFAULT_OUTPUT = PROJECT_ROOT / "输出" / "滑行参数扫描" / "当前配置扫描"
DEFAULT_RUNTIME = load_runtime_paths()["runtime_root"] / "coast_parameter_scan" / "current_config"
DEFAULT_TRUTH = PROJECT_ROOT / "输出" / "解码CSV_单位修正" / "纵向动力学_滑行试验"
RR_C_CANDIDATES = (0.0038, 0.0050, 0.0065, 0.0080, 0.0100, 0.0120)


def replace_all_scalar(text: str, keyword: str, value: float) -> tuple[str, int]:
    """同步替换四个车轮的同名标量，避免前后轮参数不一致。"""
    pattern = re.compile(rf"(?m)^(?P<prefix>\s*{re.escape(keyword)}\s+)(?P<value>[-+0-9.eE]+)(?P<suffix>\s*(?:[;!].*)?)$")
    replacement = rf"\g<prefix>{value:.8g}\g<suffix>"
    return pattern.subn(replacement, text)


def time_to_speed(csv_path: Path, target_kmh: float) -> float | None:
    """计算下降穿越目标车速的时间，用于快速判断是否覆盖完整滑行。"""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = [(float(row["time_s"]), float(row["speed_kmh"])) for row in csv.DictReader(stream)]
    for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
        if v0 >= target_kmh > v1:
            ratio = (v0 - target_kmh) / (v0 - v1) if v0 != v1 else 0.0
            return t0 + ratio * (t1 - t0)
    return None


def evaluate_candidate(
    simulation_csv: Path, truth_root: Path, rules: dict[str, Any], window_kmh: list[float],
) -> dict[str, Any]:
    """与6条实车滑行曲线比较并汇总同口径指标。"""
    comparisons = [compare_pair(path, simulation_csv, "coasting", rules, window_kmh) for path in sorted(truth_root.glob("*.csv"))]
    metric_names = sorted({name for item in comparisons for name in item["metrics"]})
    return {
        "comparison_count": len(comparisons),
        "mean_score_pct": sum(item["maneuver_score_pct"] for item in comparisons) / len(comparisons),
        "mean_metric_scores_pct": {
            name: sum(item["metrics"].get(name, {"score_pct": 0.0})["score_pct"] for item in comparisons) / len(comparisons)
            for name in metric_names
        },
        "comparisons": comparisons,
    }


def run_scan(template: Path, output_root: Path, runtime_root: Path, truth_root: Path, duration_s: float) -> dict[str, Any]:
    """运行全部滚阻候选并选择滑行平均分最高者。"""
    if output_root.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output_root}")
    output_root.mkdir(parents=True)
    project_config = load_project_config()
    rules = project_config["metric_thresholds"]
    condition = project_config["agent"]["coasting_test_condition"]
    trials = []
    for index, rr_c in enumerate(RR_C_CANDIDATES, start=1):
        name = f"trial_{index:02d}_RR_C_{rr_c:g}"
        trial_output = output_root / name
        runtime = runtime_root / name
        trial_output.mkdir(parents=True)
        runtime.mkdir(parents=True, exist_ok=True)

        # 先生成统一的50 km/h零油门工况，再只改变滚阻常数。
        run_all = runtime / "Run_all.par"
        build_par(template, run_all, duration_s, condition=condition)
        text = run_all.read_bytes().decode("ascii")
        text, count = replace_all_scalar(text, "RR_C", rr_c)
        if count != 4:
            raise ValueError(f"RR_C预期替换4处，实际替换{count}处")
        run_all.write_bytes(text.encode("ascii"))

        solver = run_solver(runtime, run_all)
        for evidence in ("Run_all.par", "run.sim", "result.vs", "result.vsb", "result_echo.par", "result_log.txt", "solver_stdout.txt", "solver_stderr.txt"):
            source = runtime / evidence
            if source.exists():
                shutil.copy2(source, trial_output / evidence)
        simulation_csv = trial_output / "simulation.csv"
        convert_result(runtime, simulation_csv)
        evaluation = evaluate_candidate(simulation_csv, truth_root, rules, condition["window_kmh"])
        trial = {
            "trial": name,
            "RR_C": rr_c,
            "solver": solver,
            "time_to_30_s": time_to_speed(simulation_csv, float(condition["window_kmh"][1])),
            "evaluation": evaluation,
        }
        (trial_output / "trial.json").write_text(json.dumps(trial, ensure_ascii=False, indent=2), encoding="utf-8")
        (trial_output / "README.md").write_text(
            f"# {name}\n\n本候选将四轮 `RR_C` 同步设为 {rr_c:g}，其余参数保持修正版动力总成基线。\n",
            encoding="utf-8",
        )
        trials.append(trial)
        print(json.dumps({"trial": name, "time_to_30_s": trial["time_to_30_s"], "score": evaluation["mean_score_pct"]}, ensure_ascii=False))

    best = max(trials, key=lambda item: item["evaluation"]["mean_score_pct"])
    result = {"type": "coasting_rr_c_scan", "duration_s": duration_s, "trials": trials, "best_trial": best["trial"], "best_RR_C": best["RR_C"]}
    (output_root / "scan_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 滑行滚动阻力扫描", "", "| 候选 | RR_C | 50→30时间 | 滑行平均精度 |", "|---|---:|---:|---:|"]
    for trial in trials:
        duration = "未达到" if trial["time_to_30_s"] is None else f"{trial['time_to_30_s']:.2f} s"
        lines.append(f"| {trial['trial']} | {trial['RR_C']:.4f} | {duration} | {trial['evaluation']['mean_score_pct']:.2f}% |")
    lines += ["", f"最佳候选：`{best['trial']}`，RR_C={best['RR_C']:.4f}。"]
    (output_root / "滑行滚阻扫描报告.md").write_text("\n".join(lines), encoding="utf-8")
    (output_root / "README.md").write_text(
        "# 滑行参数扫描输出\n\n每个 `trial_*` 保存独立Run_all、CarSim原始结果、统一CSV和评价JSON。"
        "运行方式：`python run_coast_parameter_scan.py`。脚本拒绝覆盖已有目录。\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--truth-root", type=Path, default=DEFAULT_TRUTH)
    config = load_project_config()
    parser.add_argument(
        "--duration", type=float,
        default=float(config["agent"]["coasting_test_condition"]["simulation_duration_s"]),
    )
    args = parser.parse_args()
    result = run_scan(args.template.resolve(), args.output.resolve(), args.runtime.resolve(), args.truth_root.resolve(), args.duration)
    print(json.dumps({"output": str(args.output.resolve()), "best_trial": result["best_trial"], "best_RR_C": result["best_RR_C"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
