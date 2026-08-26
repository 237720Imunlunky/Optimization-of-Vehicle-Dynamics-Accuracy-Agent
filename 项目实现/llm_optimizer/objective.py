"""从正式验收结果构造LLM可理解的优化目标与候选接受规则。"""

from __future__ import annotations

from typing import Any


def summarize_formal_result(formal: dict[str, Any]) -> dict[str, Any]:
    """只暴露优化所需指标，避免把大量原始曲线塞入提示词。"""
    return {
        "longitudinal_score_pct": formal["summary"]["longitudinal_score_pct"],
        "group_scores_pct": formal["summary"]["group_scores_pct"],
        "mean_metric_scores_pct": formal["mean_metric_scores_pct"],
        "all_individual_metrics_passed": all(item["all_metrics_passed"] for item in formal["results"]),
        "failed_metric_count": sum(
            not metric["passed"]
            for result in formal["results"]
            for metric in result["metrics"].values()
        ),
    }


def passes_hard_guards(current: dict[str, Any], candidate: dict[str, Any], config: dict[str, Any]) -> tuple[bool, list[str], dict[str, float]]:
    """执行保护线；若小样本基线已低于90%，候选至少不得继续降低。"""
    guards = config["hard_guards"]
    current_groups = current["group_scores_pct"]
    candidate_groups = candidate["group_scores_pct"]
    floors = {
        "longitudinal": min(float(guards["longitudinal_score_min_pct"]), float(current["longitudinal_score_pct"])),
        "acceleration": min(float(guards["acceleration_score_min_pct"]), float(current_groups["acceleration"])),
        "coasting": min(float(guards["coasting_score_min_pct"]), float(current_groups["coasting"])),
    }
    failures = []
    if float(candidate["longitudinal_score_pct"]) + 1e-9 < floors["longitudinal"]:
        failures.append("纵向综合精度跌破保护线")
    if float(candidate_groups["acceleration"]) + 1e-9 < floors["acceleration"]:
        failures.append("加速精度跌破保护线")
    if float(candidate_groups["coasting"]) + 1e-9 < floors["coasting"]:
        failures.append("滑行精度跌破保护线")
    return not failures, failures, floors


def should_accept_candidate(current: dict[str, Any], candidate: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """优先减少不合格单项，其次提升综合分，并强制执行90%保护线。"""
    guards_passed, guard_failures, effective_floors = passes_hard_guards(current, candidate, config)
    fewer_failures = int(candidate["failed_metric_count"]) < int(current["failed_metric_count"])
    improvement = float(candidate["longitudinal_score_pct"]) - float(current["longitudinal_score_pct"])
    enough_score_gain = improvement >= float(config["minimum_improvement_pct"])
    accepted = guards_passed and (fewer_failures or enough_score_gain)
    return {
        "accepted": accepted,
        "hard_guards_passed": guards_passed,
        "guard_failures": guard_failures,
        "effective_protection_floors_pct": effective_floors,
        "failed_metric_reduction": int(current["failed_metric_count"]) - int(candidate["failed_metric_count"]),
        "longitudinal_improvement_pct": improvement,
        "reason": "减少不合格单项" if accepted and fewer_failures else "综合分有效提升" if accepted else "未满足接受条件",
    }
