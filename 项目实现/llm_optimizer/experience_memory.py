"""提取多候选CarSim评价经验，并生成下一轮可用的压缩记忆。"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any

from config_loader import load_project_config
from .state_store import compact_state_history


MEMORY_VERSION = "1.0"
MAX_MEMORY_ROUNDS = 5
PROMPT_MEMORY_ROUNDS = 3
METRIC_CHANGE_EPSILON = 0.02


def parameter_changes(
    baseline_parameters: dict[str, float], candidate_parameters: dict[str, float],
) -> dict[str, dict[str, float]]:
    """计算候选相对本轮公共基线的参数变化。"""
    changes: dict[str, dict[str, float]] = {}
    for name, candidate_value in candidate_parameters.items():
        if name not in baseline_parameters:
            continue
        baseline_value = float(baseline_parameters[name])
        candidate_value = float(candidate_value)
        if abs(candidate_value - baseline_value) <= 1e-12:
            continue
        changes[name] = {
            "from": baseline_value,
            "to": candidate_value,
            "delta": candidate_value - baseline_value,
        }
    return changes


def flatten_metric_scores(summary: dict[str, Any]) -> dict[str, float]:
    """把分组和单项指标展开为稳定键名，便于跨候选比较。"""
    scores = {"longitudinal": float(summary.get("longitudinal_score_pct", 0.0))}
    for group, value in summary.get("group_scores_pct", {}).items():
        scores[f"group.{group}"] = float(value)
    for role, metrics in summary.get("mean_metric_scores_pct", {}).items():
        for metric, value in metrics.items():
            scores[f"metric.{role}.{metric}"] = float(value)
    return scores


def metric_deltas(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, float]:
    """计算候选相对基线的全部同名指标变化，正数表示改善。"""
    baseline_scores = flatten_metric_scores(baseline)
    candidate_scores = flatten_metric_scores(candidate)
    return {
        name: candidate_scores[name] - baseline_scores[name]
        for name in candidate_scores.keys() & baseline_scores.keys()
    }


def ranked_changes(deltas: dict[str, float], positive: bool, limit: int = 6) -> list[dict[str, float | str]]:
    """筛选最明显的改善或退化，避免把全部指标塞入LLM上下文。"""
    selected = [
        (name, value)
        for name, value in deltas.items()
        if (value >= METRIC_CHANGE_EPSILON if positive else value <= -METRIC_CHANGE_EPSILON)
    ]
    selected.sort(key=lambda item: abs(item[1]), reverse=True)
    return [{"metric": name, "delta_pct": round(value, 6)} for name, value in selected[:limit]]


def split_experience(decision: dict[str, Any], split: str) -> dict[str, Any]:
    """提取标定、验证或全量数据上的结果变化及保护线信息。"""
    summaries = decision["summaries"][split]
    deltas = metric_deltas(summaries["baseline"], summaries["candidate"])
    split_decision = decision["decisions"][split]
    return {
        "accepted": bool(split_decision.get("accepted")),
        "hard_guards_passed": bool(split_decision.get("hard_guards_passed")),
        "guard_failures": list(split_decision.get("guard_failures", [])),
        "score_delta_pct": round(float(split_decision.get("longitudinal_improvement_pct", 0.0)), 6),
        "failed_metric_reduction": int(split_decision.get("failed_metric_reduction", 0)),
        "top_improvements": ranked_changes(deltas, positive=True),
        "top_regressions": ranked_changes(deltas, positive=False),
    }


def build_candidate_experience(
    candidate_id: str,
    baseline_parameters: dict[str, float],
    candidate_parameters: dict[str, float],
    decision: dict[str, Any],
) -> dict[str, Any]:
    """把一份完整候选评价压缩为后续轮次可学习的经验。"""
    return {
        "candidate_id": str(candidate_id),
        "accepted": bool(decision.get("accepted")),
        "selected_as_winner": False,
        "parameter_changes": parameter_changes(baseline_parameters, candidate_parameters),
        "calibration": split_experience(decision, "calibration"),
        "validation": split_experience(decision, "validation"),
        "all_data": split_experience(decision, "all_data"),
    }


def build_round_experience(
    iteration: int,
    baseline_state: dict[str, Any],
    candidate_records: list[dict[str, Any]],
    winner_candidate_id: str | None,
    proposal_rejections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """汇总同一公共基线下的全部候选，标记唯一胜者与整轮回退。"""
    baseline_parameters = baseline_state["best"]["parameters"]
    candidates = [
        build_candidate_experience(
            str(record["candidate_id"]),
            baseline_parameters,
            record["state"]["history"][-1]["parameters"],
            record["decision"],
        )
        for record in candidate_records
    ]
    for candidate in candidates:
        candidate["selected_as_winner"] = candidate["candidate_id"] == winner_candidate_id
        if candidate["selected_as_winner"]:
            candidate["outcome"] = "winner"
        elif candidate["accepted"]:
            candidate["outcome"] = "validated_non_winner"
        else:
            candidate["outcome"] = "rolled_back"
    return {
        "iteration": int(iteration),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "baseline_source": baseline_state["best"].get("source"),
        "baseline_score_pct": baseline_state["best"]["summary"].get("longitudinal_score_pct"),
        "winner_candidate_id": winner_candidate_id,
        "all_rolled_back": winner_candidate_id is None,
        "proposal_rejections": copy.deepcopy(proposal_rejections or []),
        "candidates": candidates,
    }


def empty_memory(policy_version: str | None, source_state: str | None = None) -> dict[str, Any]:
    """建立空经验库；不同数据策略的旧经验不得直接混用。"""
    return {
        "version": MEMORY_VERSION,
        "policy_version": policy_version,
        "initialized_at": datetime.now().isoformat(timespec="seconds"),
        "source_state": source_state,
        "bootstrap_note": "旧候选采用不同滑行样本口径，未导入因果经验；从当前同口径最优点开始累计",
        "rounds": [],
    }


def append_round_memory(state: dict[str, Any], round_experience: dict[str, Any]) -> None:
    """追加一轮经验并限制窗口长度，防止状态文件和提示词无限增长。"""
    policy_version = state.get("data_policy", {}).get("version")
    memory = state.setdefault("optimization_memory", empty_memory(policy_version))
    if memory.get("policy_version") != policy_version:
        memory = empty_memory(policy_version)
        state["optimization_memory"] = memory
    memory["rounds"].append(round_experience)
    limit = int(load_project_config()["experience_policy"].get("state_round_limit", MAX_MEMORY_ROUNDS))
    memory["rounds"] = memory["rounds"][-limit:]


def consolidate_round_state(
    proposal_state: dict[str, Any],
    candidate_records: list[dict[str, Any]],
    round_experience: dict[str, Any],
    winner_candidate_id: str | None,
) -> dict[str, Any]:
    """生成唯一轮次状态，让非胜者经验和全部回退经验都能进入下一轮。"""
    consolidated = copy.deepcopy(proposal_state)
    # proposal_state已包含本轮awaiting记录；每个候选状态的最后一项是各自独立评价结果。
    consolidated["history"].extend(copy.deepcopy(record["state"]["history"][-1]) for record in candidate_records)
    winner = next((record for record in candidate_records if str(record["candidate_id"]) == winner_candidate_id), None)
    if winner is not None:
        consolidated["best"] = copy.deepcopy(winner["state"]["best"])
        consolidated["no_improvement_iterations"] = 0
    else:
        consolidated["no_improvement_iterations"] = int(proposal_state.get("no_improvement_iterations", 0)) + 1
    append_round_memory(consolidated, round_experience)
    consolidated["last_round_experience"] = copy.deepcopy(round_experience)
    return compact_state_history(consolidated)


def compact_parameter_changes(changes: dict[str, Any]) -> dict[str, dict[str, float]]:
    """提示词只保留目标值和变化量，基线值可由两者反推。"""
    return {
        name: {
            "to": round(float(change["to"]), 6),
            "delta": round(float(change["delta"]), 6),
        }
        for name, change in changes.items()
    }


def compact_ranked_metrics(rows: list[dict[str, Any]], limit: int = 2) -> list[dict[str, Any]]:
    """每个数据层只保留最明显的两个改善或退化，避免指标列表重复膨胀。"""
    return [
        {"metric": item["metric"], "delta_pct": round(float(item["delta_pct"]), 4)}
        for item in rows[:limit]
    ]


def compact_split_for_prompt(split: dict[str, Any], include_metrics: bool = True) -> dict[str, Any]:
    """保留接受判断、保护线和主要得失，删除提示词不需要的重复字段。"""
    compact = {
        "accepted": bool(split.get("accepted")),
        "guard_failures": list(split.get("guard_failures", [])),
        "score_delta_pct": round(float(split.get("score_delta_pct", 0.0)), 4),
        "failed_metric_reduction": int(split.get("failed_metric_reduction", 0)),
    }
    if include_metrics:
        compact["top_improvements"] = compact_ranked_metrics(split.get("top_improvements", []))
        compact["top_regressions"] = compact_ranked_metrics(split.get("top_regressions", []))
    return compact


def candidate_prompt_record(candidate: dict[str, Any]) -> dict[str, Any]:
    """生成融合或回退方向所需的紧凑候选经验。"""
    return {
        "candidate_id": candidate["candidate_id"],
        "outcome": candidate["outcome"],
        "parameter_changes": compact_parameter_changes(candidate["parameter_changes"]),
        "calibration": compact_split_for_prompt(candidate["calibration"]),
        "validation": compact_split_for_prompt(candidate["validation"]),
        # 全量层只保留总分与未通过项变化，主要指标得失在标定/验证层已经表达。
        "all_data": compact_split_for_prompt(candidate["all_data"], include_metrics=False),
    }


def parameter_direction_signature(record: dict[str, Any], policy_version: str | None) -> str:
    """用参数名称和变化方向识别重复经验，不把数值微小差异重复塞入提示词。"""
    changes = record.get("parameter_changes", {})
    directions = [f"{name}:{'+' if float(change.get('delta', 0)) > 0 else '-'}" for name, change in sorted(changes.items())]
    return f"{policy_version or 'unknown'}|{'|'.join(directions)}"


def deduplicate_records(records: list[dict[str, Any]], policy_version: str | None) -> list[dict[str, Any]]:
    """相同参数方向只保留最近一次，减少重复经验造成的提示词偏置。"""
    selected: dict[str, dict[str, Any]] = {}
    for record in records:
        selected[parameter_direction_signature(record, policy_version)] = record
    return list(selected.values())


def fit_prompt_budget(payload: dict[str, Any], character_budget: int) -> dict[str, Any]:
    """从最旧的失败/融合记录开始裁剪，确保经验JSON不超过配置预算。"""
    compact = copy.deepcopy(payload)
    while len(json.dumps(compact, ensure_ascii=False, separators=(",", ":"))) > character_budget:
        if compact.get("recent_rejected_directions"):
            compact["recent_rejected_directions"].pop(0)
        elif compact.get("validated_fusion_sources"):
            compact["validated_fusion_sources"].pop(0)
        elif compact.get("recent_rounds"):
            compact["recent_rounds"].pop(0)
        else:
            break
    compact["character_budget"] = character_budget
    compact["serialized_characters"] = len(json.dumps(compact, ensure_ascii=False, separators=(",", ":")))
    return compact


def build_prompt_memory(memory: dict[str, Any] | None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """构造给LLM的短期经验、可融合来源和近期失败方向。"""
    if not memory or not memory.get("rounds"):
        return {
            "available": False,
            "instruction": "暂无同口径历史经验，首轮按C1利用、C2互补、C3探索生成独立候选",
        }
    settings = policy or load_project_config()["experience_policy"]
    recent_rounds = memory["rounds"][-int(settings.get("prompt_round_limit", PROMPT_MEMORY_ROUNDS)):]
    fusion_sources = []
    rejected_directions = []
    for round_item in recent_rounds:
        for candidate in round_item["candidates"]:
            compact = {"iteration": round_item["iteration"], **candidate_prompt_record(candidate)}
            if candidate["outcome"] == "validated_non_winner":
                fusion_sources.append(compact)
            elif candidate["outcome"] == "rolled_back":
                rejected_directions.append(compact)
    fusion_sources = deduplicate_records(fusion_sources, memory.get("policy_version"))
    rejected_directions = deduplicate_records(rejected_directions, memory.get("policy_version"))
    payload = {
        "available": True,
        "policy_version": memory.get("policy_version"),
        "recent_rounds": [
            {
                "iteration": item["iteration"],
                "winner_candidate_id": item["winner_candidate_id"],
                "all_rolled_back": item["all_rolled_back"],
                "proposal_rejections": item.get("proposal_rejections", []),
                # 详细数据已按用途放入融合来源或回退方向，这里只保留轮次索引，避免重复发送。
                "candidates": [
                    {"candidate_id": candidate["candidate_id"], "outcome": candidate["outcome"]}
                    for candidate in item["candidates"]
                ],
            }
            for item in recent_rounds
        ],
        "validated_fusion_sources": fusion_sources[-int(settings.get("maximum_fusion_sources", 4)):],
        "recent_rejected_directions": rejected_directions[-int(settings.get("maximum_rejected_directions", 6)):],
        "causality_warning": "多参数候选只能证明组合效果，不能把单一参数变化直接解释为因果",
    }
    return fit_prompt_budget(payload, int(settings.get("prompt_character_budget", 12000)))


def initialize_state_memory(state: dict[str, Any], source_state: str) -> dict[str, Any]:
    """为当前同口径最优状态初始化经验库，不篡改参数、分数或历史评价。"""
    initialized = copy.deepcopy(state)
    policy_version = initialized.get("data_policy", {}).get("version")
    initialized["optimization_memory"] = empty_memory(policy_version, source_state)
    initialized["last_round_experience"] = None
    # 旧无提升计数来自不同数据口径，经验库启用时从0重新累计。
    initialized["no_improvement_iterations"] = 0
    return initialized
