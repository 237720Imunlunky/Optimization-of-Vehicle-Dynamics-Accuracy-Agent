"""扫描CarSim松油门回收形状系数，判断其是否能覆盖实车减速度。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_coast_simulation import DEFAULT_CARSIM_ROOT, build_par, build_simfile, run_one
from run_parameter_sensitivity import convert_result
from validate_coast_regen_control import simulation_summary


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = PROJECT_ROOT / "输出" / "滑行回收控制验收" / "iteration_002_factor_scan"
DEFAULT_RUNTIME = Path("F:/Carsim/AgentRuntime/parameter_agent/coast_regen_acceptance/iteration_002_factor_scan")


def run_trial(template: Path, output: Path, runtime: Path, factor: float) -> dict:
    """运行一个回收形状系数试验并保存量化摘要。"""
    name = f"cf_hev_pbk_{factor:g}".replace(".", "p")
    archive, run_dir = output / name, runtime / name
    archive.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    build_par(template, run_dir / "Run_all.par", 100.0, True, factor)
    build_simfile(run_dir, DEFAULT_CARSIM_ROOT)
    solver = run_one(run_dir, DEFAULT_CARSIM_ROOT, True)
    if solver["status"] != "completed":
        raise RuntimeError(f"系数{factor}求解失败")
    convert_result(run_dir, archive / "simulation.csv")
    summary = {"cf_hev_pbk": factor, **simulation_summary(archive / "simulation.csv")}
    (archive / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    """运行小范围扫描并输出与实车均值的误差排序。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    args = parser.parse_args()
    output, runtime = args.output.resolve(), args.runtime.resolve()
    if output.exists() or runtime.exists():
        raise FileExistsError("扫描输出或运行目录已存在，拒绝覆盖")
    output.mkdir(parents=True)
    runtime.mkdir(parents=True)
    observed_mps2 = 0.08501415801313378
    results = [run_trial(args.template.resolve(), output, runtime, factor) for factor in (0.01, 0.02, 0.04, 0.08, 0.16)]
    for item in results:
        item["absolute_error_to_observed_mps2"] = abs(float(item["mean_deceleration_mps2"]) - observed_mps2)
    results.sort(key=lambda item: item["absolute_error_to_observed_mps2"])
    (output / "scan_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "README.md").write_text(
        "# CarSim松油门回收形状系数扫描\n\n"
        "固定当前最优模型并启用`OPT_REGEN_OFF_THRT=1`，扫描`CF_HEV_PBK`，"
        "比较严格50->30平均减速度与4份有效实车均值0.0850 m/s2。\n\n"
        "运行方式：`python scan_coast_regen_factor.py --template <当前最优Run_all.par>`。\n",
        encoding="utf-8",
    )
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
