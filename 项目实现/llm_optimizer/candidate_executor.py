"""执行通过安全校验的LLM候选，并按标定集/验证集自动接受或回退。"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from build_trace_control import read_trace, replace_throttle
from evaluate_longitudinal import aggregate, compare_pair, find_speed_crossing, normalize_coasting_window, read_rows, resample_rows
from run_coast_simulation import build_par
from run_formal_longitudinal_acceptance import copy_evidence, first_valid_speed, last_time, replace_single_scalar
from run_parameter_sensitivity import convert_result, run_solver

from .model_patcher import audit_parameter_bindings, build_candidate_template
from .objective import should_accept_candidate, summarize_formal_result
from .parameter_space import load_agent_config
from config_loader import load_project_config, state_config_sync_status
from .state_store import record_evaluation, write_state


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_TEMPLATE = PROJECT_ROOT / "输出" / "动力总成修正" / "当前配置模型" / "closed_loop_acceptance" / "actual_trace" / "Run_all.par"
TRUTH_ROOT = PROJECT_ROOT / "输出" / "解码CSV_单位修正"
FORMAL_RESULT = PROJECT_ROOT / "输出" / "正式联合基线" / "当前配置基线" / "formal_acceptance.json"
PROPOSAL_ROOT = PROJECT_ROOT / "输出" / "LLM参数优化Agent" / "manual_dry_run"
DEFAULT_OUTPUT = PROJECT_ROOT / "输出" / "LLM参数优化Agent" / "manual_carsim_eval"
DEFAULT_RUNTIME = Path("F:/Carsim/AgentRuntime/parameter_agent/llm_optimizer/manual_carsim_eval")
TRACE_STEP_S = 0.02


def read_json(path: Path) -> dict[str, Any]:
    """读取UTF-8 JSON文件。"""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    """写入便于人工复核的缩进JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_folder_readme(path: Path, title: str, description: str) -> None:
    """为每个分类输出目录写入用途说明。"""
    path.mkdir(parents=True, exist_ok=True)
    readme = path / "README.md"
    if not readme.exists():
        readme.write_text(f"# {title}\n\n{description}\n", encoding="utf-8")


def select_candidate(proposal_root: Path, candidate_id: str | None) -> dict[str, Any]:
    """从已通过安全校验的候选中选择本轮执行对象。"""
    validation = read_json(proposal_root / "candidate_validation.json")
    accepted = validation["accepted"]
    if candidate_id is None:
        if not accepted:
            raise ValueError("没有通过安全校验的候选，已停止CarSim评价；请检查LLM返回格式和参数边界")
        if len(accepted) > 1:
            raise ValueError("存在多个有效候选，请通过--candidate-id明确指定")
        return accepted[0]
    # 兼容修复前历史输出中的数字候选编号，例如JSON数字1与命令行字符串"1"。
    match = next((item for item in accepted if str(item["candidate_id"]) == str(candidate_id)), None)
    if match is None:
        raise ValueError(f"候选未通过安全校验或不存在：{candidate_id}")
    return match


def merge_parameters(state: dict[str, Any], candidate: dict[str, Any]) -> dict[str, float]:
    """在当前最优参数上应用候选变化，不接受LLM提供的未校验字段。"""
    parameters = {name: float(value) for name, value in state["best"]["parameters"].items()}
    parameters.update({name: float(value) for name, value in candidate["normalized_changes"].items()})
    return parameters


def write_trace_csv(path: Path, truth: Path) -> None:
    """归档实际注入CarSim的油门离散表。"""
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time_s", "throttle_fraction"])
        writer.writerows(read_trace(truth, step_s=TRACE_STEP_S))


def prepare_acceleration_input(template: Path, truth: Path, role: str, destination: Path) -> dict[str, float]:
    """使用该次实车油门Trace和实际初始车速生成独立加速输入。"""
    text = template.read_bytes().decode("ascii")
    table = read_trace(truth, step_s=TRACE_STEP_S)
    text = replace_throttle(text, table)
    initial_speed = 0.0 if role == "zero_to_100" else first_valid_speed(truth)
    text = replace_single_scalar(text, "SV_VXS", initial_speed)
    text = replace_single_scalar(text, "TSTOP", max(last_time(truth), table[-1][0]) + 1.0)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(text.encode("ascii"))
    return {"initial_speed_kmh": initial_speed, "trace_step_s": TRACE_STEP_S}


def run_acceleration_repeat(
    template: Path,
    truth: Path,
    role: str,
    split: str,
    repeat_index: int,
    archive: Path,
    runtime: Path,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """运行一条加速工况并保存输入、原始输出和评价结果。"""
    write_folder_readme(archive, f"{role} 第{repeat_index}次", "保存本次实车Trace对应的CarSim输入、输出和正式评价。")
    runtime.mkdir(parents=True, exist_ok=True)
    inputs = prepare_acceleration_input(template, truth, role, runtime / "Run_all.par")
    solver = run_solver(runtime, runtime / "Run_all.par")
    copy_evidence(runtime, archive)
    simulation = archive / "simulation.csv"
    convert_result(runtime, simulation)
    write_trace_csv(archive / "trace_control.csv", truth)
    comparison = compare_pair(truth, simulation, role, rules)
    comparison.update({"dataset_split": split, "repeat_index": repeat_index})
    write_json(archive / "evaluation.json", {"solver": solver, "inputs": inputs, "comparison": comparison})
    return comparison


def list_truth_files(role: str) -> list[Path]:
    """按试验编号稳定排序读取六条实车数据。"""
    folder = (
        TRUTH_ROOT / "纵向动力学_加速试验" / "0-100全油门起步加速"
        if role == "zero_to_100"
        else TRUTH_ROOT / "纵向动力学_加速试验" / "60-100超越加速"
        if role == "overtaking"
        else TRUTH_ROOT / "纵向动力学_滑行试验"
    )
    files = sorted(folder.glob("*.csv"))
    if len(files) != 6:
        raise ValueError(f"{role}实车文件应为6条，实际为{len(files)}")
    return files


def role_split_config(role: str, config: dict[str, Any]) -> dict[str, Any]:
    """读取工况专属数据划分，并兼容旧版全局划分配置。"""
    by_role = config.get("dataset_splits", {})
    if role in by_role:
        return by_role[role]
    return {
        "calibration": config["calibration_repeats"],
        "validation": config["validation_repeats"],
        "excluded": {},
    }


def excluded_reason(role: str, repeat_index: int, config: dict[str, Any]) -> str | None:
    """返回人工审计确认的排除原因；JSON对象键统一按字符串读取。"""
    excluded = role_split_config(role, config).get("excluded", {})
    return excluded.get(str(repeat_index)) or excluded.get(repeat_index)


def split_name(repeat_index: int, config: dict[str, Any], role: str = "zero_to_100") -> str:
    """根据工况专属配置把重复试验划入标定集或独立验证集。"""
    split = role_split_config(role, config)
    if repeat_index in split["calibration"]:
        return "calibration"
    if repeat_index in split["validation"]:
        return "validation"
    reason = excluded_reason(role, repeat_index, config)
    if reason:
        return "excluded"
    raise ValueError(f"{role}第{repeat_index}次试验未配置数据分组")


def has_strict_coasting_window(path: Path, window_kmh: tuple[float, float] | list[float] | None = None) -> bool:
    """确认实车数据真实向下穿越配置窗口，不允许从窗口内部补造起点。"""
    rows = resample_rows(read_rows(path))
    start_speed, end_speed = normalize_coasting_window(window_kmh)
    start = find_speed_crossing(rows, start_speed, "down")
    if start is None:
        return False
    return find_speed_crossing(rows, end_speed, "down", start[0]) is not None


def run_all_acceleration(
    template: Path, output: Path, runtime: Path, rules: dict[str, Any], config: dict[str, Any],
) -> list[dict[str, Any]]:
    """运行0-100与60-100共12条独立Trace仿真。"""
    results = []
    for role in ("zero_to_100", "overtaking"):
        files = list_truth_files(role)
        for repeat_index, truth in enumerate(files, start=1):
            split = split_name(repeat_index, config, role)
            role_output = output / split / role
            write_folder_readme(role_output, role, f"{split}数据中的{role}工况，按重复试验独立归档。")
            archive = role_output / f"repeat_{repeat_index:02d}"
            case_runtime = runtime / split / role / f"repeat_{repeat_index:02d}"
            result = run_acceleration_repeat(template, truth, role, split, repeat_index, archive, case_runtime, rules)
            results.append(result)
            print(json.dumps({"role": role, "repeat": repeat_index, "split": split, "score_pct": result["maneuver_score_pct"]}, ensure_ascii=False))
    return results


def run_coasting(
    template: Path, output: Path, runtime: Path, rules: dict[str, Any], config: dict[str, Any],
) -> list[dict[str, Any]]:
    """运行一次经CAN验证的N挡零回收滑行仿真，再评价各有效实车样本。"""
    archive = output / "shared_simulation" / "coasting"
    write_folder_readme(archive, "共享滑行仿真", "候选参数下的确定性50到30 km/h零油门滑行原始结果。")
    runtime.mkdir(parents=True, exist_ok=True)
    condition = config.get("coasting_test_condition", {})
    window = normalize_coasting_window(condition.get("window_kmh"))
    duration_s = float(condition.get("simulation_duration_s", 100.0))
    # 原始CAN确认有效样本为N挡且回收扭矩0 Nm，控制绑定由config.json写入并审计。
    inputs = build_par(
        template, runtime / "Run_all.par", duration_s, off_throttle_regen=False,
        coast_window_kmh=window, condition=condition,
    )
    solver = run_solver(runtime, runtime / "Run_all.par")
    copy_evidence(runtime, archive)
    simulation = archive / "simulation.csv"
    convert_result(runtime, simulation)

    results = []
    sample_manifest = []
    for repeat_index, truth in enumerate(list_truth_files("coasting"), start=1):
        split = split_name(repeat_index, config, "coasting")
        reason = excluded_reason("coasting", repeat_index, config)
        if split == "excluded":
            sample_manifest.append({
                "repeat_index": repeat_index,
                "source": str(truth),
                "status": "excluded",
                "reason": reason,
            })
            continue
        if not has_strict_coasting_window(truth, window):
            raise ValueError(f"滑行第{repeat_index}次缺少严格{window[0]:g}->{window[1]:g} km/h完整窗口，禁止进入评价")
        comparison = compare_pair(truth, simulation, "coasting", rules, window)
        comparison.update({"dataset_split": split, "repeat_index": repeat_index})
        results.append(comparison)
        sample_manifest.append({
            "repeat_index": repeat_index,
            "source": str(truth),
            "status": "included",
            "split": split,
            "actual_start_speed_kmh": comparison.get("actual_start_speed_kmh"),
        })
    write_json(archive / "evaluation_all_repeats.json", {
        "solver": solver,
        "condition": condition,
        "inputs": inputs,
        "sample_manifest": sample_manifest,
        "comparisons": results,
    })
    for split in ("calibration", "validation"):
        folder = output / split / "coasting"
        selected = [item for item in results if item["dataset_split"] == split]
        write_folder_readme(folder, f"{split}滑行评价", "引用共享滑行仿真，与本数据分组中的实车曲线比较。")
        write_json(folder / "evaluation.json", selected)
    return results


def evaluation_summary(results: list[dict[str, Any]], project_config: dict[str, Any]) -> dict[str, Any]:
    """聚合指定数据分组，并统计仍未通过的单项数量。"""
    summary = aggregate(results, project_config)
    summary["failed_metric_count"] = sum(
        not metric["passed"] for result in results for metric in result["metrics"].values()
    )
    summary["comparison_count"] = len(results)
    metric_means = {}
    for role in ("zero_to_100", "overtaking", "coasting"):
        selected = [item for item in results if item["role"] == role]
        if not selected:
            continue
        names = sorted({name for item in selected for name in item["metrics"]})
        metric_means[role] = {
            name: sum(float(item["metrics"][name]["score_pct"]) for item in selected) / len(selected)
            for name in names
        }
    summary["mean_metric_scores_pct"] = metric_means
    return summary


def split_formal_baseline(formal: dict[str, Any], config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """按与候选相同的重复编号拆分92.34%正式基线。"""
    groups = {"calibration": [], "validation": []}
    for role in ("zero_to_100", "overtaking", "coasting"):
        selected = [item for item in formal["results"] if item["role"] == role]
        if len(selected) != 6:
            raise ValueError(f"正式基线中的{role}比较数量不是6")
        for repeat_index, item in enumerate(selected, start=1):
            split = split_name(repeat_index, config, role)
            if split != "excluded":
                groups[split].append(item)
    return groups


def current_best_summaries(
    state: dict[str, Any], formal: dict[str, Any], project_config: dict[str, Any], agent_config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """读取当前最优点的三层评价；首轮使用正式基线，后续使用最近一次已接受候选。"""
    if state["best"]["source"] == "formal_baseline_current_config":
        baseline_splits = split_formal_baseline(formal, agent_config)
        return {
            "calibration": evaluation_summary(baseline_splits["calibration"], project_config),
            "validation": evaluation_summary(baseline_splits["validation"], project_config),
            "all_data": summarize_formal_result(formal),
        }

    for entry in reversed(state.get("history", [])):
        if entry.get("status") != "accepted" or entry.get("candidate_id") != state["best"]["source"]:
            continue
        summaries = entry.get("decision", {}).get("summaries", {})
        if all(name in summaries and "candidate" in summaries[name] for name in ("calibration", "validation", "all_data")):
            return {name: summaries[name]["candidate"] for name in ("calibration", "validation", "all_data")}
    raise ValueError("当前最优候选缺少三层评价摘要，禁止与错误基线比较")


def make_acceptance_decision(
    state: dict[str, Any], formal: dict[str, Any], candidate_results: list[dict[str, Any]],
    project_config: dict[str, Any], agent_config: dict[str, Any],
) -> dict[str, Any]:
    """候选必须相对当前最优点在标定集、验证集和全部数据三层通过。"""
    current_summaries = current_best_summaries(state, formal, project_config, agent_config)
    candidate_splits = {
        split: [item for item in candidate_results if item["dataset_split"] == split]
        for split in ("calibration", "validation")
    }
    decisions = {}
    summaries = {}
    for split in ("calibration", "validation"):
        current = current_summaries[split]
        candidate = evaluation_summary(candidate_splits[split], project_config)
        summaries[split] = {"baseline": current, "candidate": candidate}
        decisions[split] = should_accept_candidate(current, candidate, agent_config)

    current_all = current_summaries["all_data"]
    candidate_all = evaluation_summary(candidate_results, project_config)
    decisions["all_data"] = should_accept_candidate(current_all, candidate_all, agent_config)
    summaries["all_data"] = {"baseline": current_all, "candidate": candidate_all}
    accepted = all(decisions[name]["accepted"] for name in ("calibration", "validation", "all_data"))
    return {
        "accepted": accepted,
        "action": "promote_candidate" if accepted else "rollback_to_formal_baseline",
        "decisions": decisions,
        "summaries": summaries,
    }


def write_report(
    output: Path,
    candidate: dict[str, Any],
    parameters: dict[str, float],
    decision: dict[str, Any],
    agent_config: dict[str, Any],
) -> None:
    """生成面向项目决策的候选评价报告。"""
    lines = [
        "# LLM候选CarSim闭环评价", "",
        f"候选：`{candidate['candidate_id']}`", f"最终动作：**{decision['action']}**", "",
        "## 参数变化", "",
    ]
    for name, value in candidate["normalized_changes"].items():
        lines.append(f"- `{name}` -> `{value}`")
    lines += ["", "## 分组判定", "", "| 数据 | 基线综合精度 | 候选综合精度 | 未通过项变化 | 判定 |", "|---|---:|---:|---:|---|"]
    for split in ("calibration", "validation", "all_data"):
        item = decision["summaries"][split]
        check = decision["decisions"][split]
        lines.append(
            f"| {split} | {item['baseline']['longitudinal_score_pct']:.2f}% | "
            f"{item['candidate']['longitudinal_score_pct']:.2f}% | {check['failed_metric_reduction']:+d} | "
            f"{'接受' if check['accepted'] else '拒绝'} |"
        )
    guards = agent_config["hard_guards"]
    lines += [
        "", "## 规则", "",
        "候选必须同时通过标定集、独立验证集和全部数据判定。"
        f"完整数据执行{float(guards['longitudinal_score_min_pct']):g}%绝对保护线；"
        f"加速和滑行分组保护线分别为{float(guards['acceleration_score_min_pct']):g}%/"
        f"{float(guards['coasting_score_min_pct']):g}%。",
        "若某个分组的既有基线低于配置保护线，候选不得继续降低该分组。",
        "当前最优参数只会在最终动作是`promote_candidate`时更新。",
    ]
    (output / "候选闭环评价报告.md").write_text("\n".join(lines), encoding="utf-8")
    write_json(output / "candidate_parameters.json", parameters)


def execute(
    proposal_root: Path, output: Path, runtime: Path, candidate_id: str | None,
) -> dict[str, Any]:
    """编排模型生成、13次CarSim求解、分组评价和状态更新。"""
    if output.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output}")
    if runtime.exists():
        raise FileExistsError(f"运行目录已存在，拒绝混入旧结果：{runtime}")
    write_folder_readme(output, "LLM候选CarSim评价", "保存候选模型、13次CarSim求解、标定/验证结果和接受或回退结论。")
    runtime.mkdir(parents=True)
    candidate = select_candidate(proposal_root, candidate_id)
    state = read_json(proposal_root / "agent_state.json")
    parameters = merge_parameters(state, candidate)
    agent_config = load_agent_config()
    project_config = load_project_config()
    sync = state_config_sync_status(state, project_config)
    if sync["status"] == "stale":
        raise RuntimeError(
            "候选评价被阻止：proposal使用的状态与当前config.json评价口径不一致；"
            "请先重新生成正式基线或迁移状态。"
        )
    formal = read_json(FORMAL_RESULT)

    template_folder = output / "candidate_template"
    write_folder_readme(template_folder, "候选模型模板", "由正式修正版基线和已校验参数生成，不覆盖原始模型。")
    candidate_template = template_folder / "Run_all.par"
    model_hash = build_candidate_template(BASELINE_TEMPLATE, candidate_template, parameters)
    model_audit = audit_parameter_bindings(candidate_template.read_bytes().decode("ascii"), parameters)
    if not model_audit["passed"]:
        raise RuntimeError("候选模型写入审计失败，禁止运行CarSim")
    write_json(template_folder / "model_manifest.json", {"sha256": model_hash, "parameters": parameters, "audit": model_audit})

    for split in ("calibration", "validation"):
        write_folder_readme(output / split, f"{split}数据", "按重复试验分类保存候选的评价证据。")
    results = run_all_acceleration(candidate_template, output, runtime, project_config["metric_thresholds"], agent_config)
    results.extend(run_coasting(candidate_template, output, runtime / "shared_coasting", project_config["metric_thresholds"], agent_config))
    decision = make_acceptance_decision(state, formal, results, project_config, agent_config)
    write_json(output / "acceptance_decision.json", decision)

    candidate_summary = decision["summaries"]["all_data"]["candidate"]
    state = record_evaluation(state, candidate["candidate_id"], parameters, candidate_summary, decision)
    write_state(output / "agent_state.json", state)
    write_report(output, candidate, parameters, decision, agent_config)
    write_json(output / "run_manifest.json", {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "candidate_id": candidate["candidate_id"],
        "candidate_source": str(proposal_root),
        "baseline_template": str(BASELINE_TEMPLATE),
        "model_sha256": model_hash,
        "carsim_run_count": 13,
        "real_comparison_count": len(results),
        "decision": decision["action"],
    })
    return {"candidate_id": candidate["candidate_id"], "output": str(output), "decision": decision["action"], "summary": candidate_summary}


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal-root", type=Path, default=PROPOSAL_ROOT)
    parser.add_argument("--candidate-id")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    args = parser.parse_args()
    result = execute(args.proposal_root.resolve(), args.output.resolve(), args.runtime.resolve(), args.candidate_id)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
