"""独立验证CarSim松油门回收开关，不修改正式模型和Agent最优状态。"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from runtime_paths import load_runtime_paths

from evaluate_longitudinal import align_maneuver, read_rows, resample_rows, target_time
from config_loader import load_project_config
from run_coast_simulation import DEFAULT_CARSIM_ROOT, build_par, build_simfile, run_one
from run_parameter_sensitivity import convert_result


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = PROJECT_ROOT / "输出" / "滑行回收控制验收" / "当前配置验收"
DEFAULT_RUNTIME = load_runtime_paths()["runtime_root"] / "coast_regen_acceptance" / "current_config"


def simulation_summary(csv_path: Path, window_kmh: list[float]) -> dict[str, float | None]:
    """计算配置滑行区间时长和平均减速度。"""
    start_speed, end_speed = float(window_kmh[0]), float(window_kmh[1])
    rows = align_maneuver(resample_rows(read_rows(csv_path)), "coasting", window_kmh)
    duration = target_time(rows, end_speed, "down")
    mean_decel = (start_speed - end_speed) / 3.6 / duration if duration and duration > 0 else None
    return {
        "duration_50_to_30_s": duration,
        "mean_deceleration_mps2": mean_decel,
        "mean_deceleration_g": mean_decel / 9.80665 if mean_decel is not None else None,
    }


def run_scenario(template: Path, output: Path, runtime: Path, regen_enabled: bool, condition: dict) -> dict:
    """运行一个独立场景并归档输入、输出和求解日志。"""
    name = "regen_enabled" if regen_enabled else "regen_disabled"
    archive = output / name
    run_dir = runtime / name
    archive.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    scenario_condition = json.loads(json.dumps(condition))
    scenario_condition["carsim_controls"]["regeneration"]["value"] = 1 if regen_enabled else 0
    build_par(
        template, run_dir / "Run_all.par", float(condition["simulation_duration_s"]),
        regen_enabled, condition=scenario_condition,
    )
    build_simfile(run_dir, DEFAULT_CARSIM_ROOT)
    solver = run_one(run_dir, DEFAULT_CARSIM_ROOT, True)
    if solver["status"] != "completed":
        raise RuntimeError(f"{name}求解失败：{solver}")
    convert_result(run_dir, archive / "simulation.csv")
    for filename in ("Run_all.par", "run.sim", "result_end.par", "result_echo.par", "result_log.txt", "solver_stdout.txt", "solver_stderr.txt"):
        source = run_dir / filename
        if source.exists():
            shutil.copy2(source, archive / filename)
    summary = {"scenario": name, "off_throttle_regen": regen_enabled, **simulation_summary(archive / "simulation.csv", condition["window_kmh"])}
    (archive / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    """执行回收开关A/B验收并生成分类说明。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True, help="当前最优候选的Run_all.par")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    args = parser.parse_args()
    output, runtime = args.output.resolve(), args.runtime.resolve()
    if output.exists() or runtime.exists():
        raise FileExistsError("验收输出或运行目录已存在，拒绝覆盖历史结果")
    output.mkdir(parents=True)
    runtime.mkdir(parents=True)
    condition = load_project_config()["agent"]["coasting_test_condition"]
    summaries = [
        run_scenario(args.template.resolve(), output, runtime, False, condition),
        run_scenario(args.template.resolve(), output, runtime, True, condition),
    ]
    (output / "comparison.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "README.md").write_text(
        "# 滑行回收控制A/B验收\n\n"
        "本目录使用同一当前最优模型，分别关闭和启用`OPT_REGEN_OFF_THRT`，"
        "比较严格50->30 km/h的时间及平均减速度。不会修改Agent正式状态。\n\n"
        "- `regen_disabled/`：松油门回收关闭；\n"
        "- `regen_enabled/`：松油门回收开启；\n"
        "- `comparison.json`：两种场景的量化对比。\n\n"
        "运行方式：`python validate_coast_regen_control.py --template <当前最优Run_all.par>`。\n",
        encoding="utf-8",
    )
    print(json.dumps(summaries, ensure_ascii=False))


if __name__ == "__main__":
    main()
