"""项目总入口：解码实车数据，或评价实车 CSV 与 Carsim CSV。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from decode_blf import decode_all
from evaluate_longitudinal import aggregate, compare_pair
from config_loader import load_project_config


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "实车数据"
OUTPUT_ROOT = ROOT / "项目实现" / "输出"


def load_config() -> dict:
    """读取项目唯一配置入口。"""
    return load_project_config()


def run_decode(config: dict, decoded_root: Path | None = None) -> list[dict]:
    """执行 BLF 批量解码。"""
    target = decoded_root or OUTPUT_ROOT / "解码CSV_单位修正"
    return decode_all(DATA_ROOT, DATA_ROOT / "N_Platform_Matrix_Chasis_CANFD_v6.12.0.dbc", target, config)


def classify(path: Path) -> str | None:
    """依据文件路径识别工况角色。"""
    name = str(path).lower()
    if "滑行" in name or "coast" in name:
        return "coasting"
    if "condition_01" in name or "0-100" in name:
        return "zero_to_100"
    if "condition_02" in name or "60-100" in name:
        return "overtaking"
    return None


def run_evaluate(config: dict, sim_root: Path | None, decoded_root: Path | None = None, report_root: Path | None = None) -> dict:
    """用同名/同工况 CSV 配对评价；缺少仿真文件时输出数据准备状态。"""
    decoded_root = decoded_root or OUTPUT_ROOT / "解码CSV_单位修正"
    results = []
    if sim_root is not None:
        for truth_path in decoded_root.rglob("*.csv"):
            role = classify(truth_path)
            if not role or truth_path.name == "manifest.csv":
                continue
            # 实车文件名含 BLF 后缀，Carsim 标准结果通常只保留 condition 编号，因此按工况角色兜底匹配。
            candidates = list(sim_root.rglob(truth_path.name)) + list(sim_root.rglob(f"*{truth_path.stem}*.csv"))
            if not candidates and role == "zero_to_100":
                candidates = list(sim_root.rglob("condition_01_0_to_100_wot/*.csv"))
            if not candidates and role == "overtaking":
                candidates = list(sim_root.rglob("condition_02_60_to_100_wot/*.csv"))
            if not candidates and role == "coasting":
                candidates = list(sim_root.rglob("*coast*.csv")) + list(sim_root.rglob("*滑行*.csv"))
            if candidates:
                window = config["agent"]["coasting_test_condition"]["window_kmh"]
                results.append(compare_pair(truth_path, candidates[0], role, config["metric_thresholds"], window))
    summary = aggregate(results, config)
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "status": "evaluated" if results else "awaiting_carsim_csv",
              "input_real_data": str(DATA_ROOT), "simulation_root": str(sim_root) if sim_root else None,
              "results": results, "summary": summary, "formal_acceptance_rule": "纵向动力学输出数据精度≥80%",
              "threshold_conflict": "历史流程文字曾出现85%，不作为当前正式验收条件"}
    out = report_root or OUTPUT_ROOT / "评价报告"
    out.mkdir(parents=True, exist_ok=True)
    (out / "accuracy_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 纵向动力学一致性评价报告", "", f"状态：{report['status']}", ""]
    if not results:
        lines += ["当前尚未提供 Carsim 输出 CSV，因此本次仅完成实车 BLF 解码准备；将仿真 CSV 通过 `--sim-root` 传入后即可计算最终分数。", ""]
    completeness = "完整（加速+滑行）" if summary["data_complete"] else "不完整（缺少加速或滑行仿真结果）"
    lines += [f"纵向总分：{summary['longitudinal_score_pct']:.2f}%", f"数据完整性：{completeness}", f"正式验收阈值：{summary['formal_acceptance_threshold_pct']:.0f}%", f"正式验收结论：{'通过' if summary['formal_passed'] else '未通过'}", "", "## 阈值说明", "当前正式验收口径统一为：纵向动力学输出数据精度≥80%。历史流程文字中的85%不作为本项目当前合格条件。"]
    (out / "一致性对比报告.md").write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> None:
    """解析命令行参数并执行对应步骤。"""
    parser = argparse.ArgumentParser(description="实车数据参数一致性对比项目")
    parser.add_argument("--decode", action="store_true", help="批量解码实车 BLF")
    parser.add_argument("--evaluate", action="store_true", help="评价实车与 Carsim CSV")
    parser.add_argument("--sim-root", type=Path, help="Carsim 输出 CSV 根目录")
    parser.add_argument("--decoded-root", type=Path, help="指定解码CSV目录；默认使用当前配置解码目录")
    parser.add_argument("--report-root", type=Path, help="指定评价报告输出目录；默认使用当前配置报告目录")
    args = parser.parse_args()
    config = load_config()
    if args.decode or not args.evaluate:
        decoded_root = args.decoded_root or OUTPUT_ROOT / "解码CSV_单位修正"
        manifest = run_decode(config, decoded_root)
        print(json.dumps({"decoded_files": len(manifest), "output": str(decoded_root)}, ensure_ascii=False))
    if args.evaluate:
        report = run_evaluate(config, args.sim_root, args.decoded_root, args.report_root)
        print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
