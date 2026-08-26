"""多候选经验记忆和轮次状态汇总测试。"""

from .experience_memory import build_prompt_memory, build_round_experience, consolidate_round_state


def make_summary(score: float, acceleration: float, coasting: float, failed: int) -> dict:
    """构造包含分组和单项指标的最小评价摘要。"""
    return {
        "longitudinal_score_pct": score,
        "group_scores_pct": {"acceleration": acceleration, "coasting": coasting},
        "failed_metric_count": failed,
        "mean_metric_scores_pct": {
            "zero_to_100": {"peak_ax": acceleration},
            "coasting": {"target_time": coasting},
        },
    }


def make_decision(baseline: dict, candidate: dict, accepted: bool, validation_guard: str | None = None) -> dict:
    """构造与真实candidate_executor一致的三层验收结构。"""
    decisions = {}
    summaries = {}
    for split in ("calibration", "validation", "all_data"):
        guard_failures = [validation_guard] if split == "validation" and validation_guard else []
        split_accepted = accepted and not guard_failures
        decisions[split] = {
            "accepted": split_accepted,
            "hard_guards_passed": not guard_failures,
            "guard_failures": guard_failures,
            "longitudinal_improvement_pct": candidate["longitudinal_score_pct"] - baseline["longitudinal_score_pct"],
            "failed_metric_reduction": baseline["failed_metric_count"] - candidate["failed_metric_count"],
        }
        summaries[split] = {"baseline": baseline, "candidate": candidate}
    return {"accepted": accepted and not validation_guard, "decisions": decisions, "summaries": summaries}


def make_proposal_state() -> dict:
    """建立所有候选共享的本轮基线状态。"""
    return {
        "current_iteration": 1,
        "no_improvement_iterations": 0,
        "best": {
            "source": "baseline",
            "parameters": {"rr_c": 0.0068, "vehicle_mass_kg": 2808.0},
            "summary": make_summary(93.5, 95.0, 91.0, 15),
        },
        "history": [{"iteration": 1, "status": "awaiting_carsim_evaluation"}],
        "data_policy": {"version": "strict_50_to_30_v4_n_gear_can_verified"},
    }


def make_candidate_record(candidate_id: str, rr_c: float, summary: dict, decision: dict) -> dict:
    """建立一条独立候选状态，模拟完成CarSim后的输出。"""
    parameters = {"rr_c": rr_c, "vehicle_mass_kg": 2808.0}
    state = make_proposal_state()
    state["history"].append({"candidate_id": candidate_id, "parameters": parameters})
    if decision["accepted"]:
        state["best"] = {"source": candidate_id, "parameters": parameters, "summary": summary}
    return {"candidate_id": candidate_id, "decision": decision, "state": state}


def test_winner_and_validated_non_winner_are_both_remembered() -> None:
    """胜者继承参数，另一通过候选应成为下一轮C2的融合来源。"""
    proposal = make_proposal_state()
    baseline = proposal["best"]["summary"]
    c1_summary = make_summary(94.2, 95.5, 91.3, 13)
    c2_summary = make_summary(94.0, 95.1, 91.8, 14)
    records = [
        make_candidate_record("C1", 0.0067, c1_summary, make_decision(baseline, c1_summary, True)),
        make_candidate_record("C2", 0.0066, c2_summary, make_decision(baseline, c2_summary, True)),
    ]
    experience = build_round_experience(1, proposal, records, "C1")
    state = consolidate_round_state(proposal, records, experience, "C1")
    prompt_memory = build_prompt_memory(state["optimization_memory"])

    assert state["best"]["source"] == "C1"
    assert state["best"]["parameters"]["rr_c"] == 0.0067
    assert len(state["history"]) == 3
    assert experience["candidates"][1]["outcome"] == "validated_non_winner"
    assert prompt_memory["validated_fusion_sources"][0]["candidate_id"] == "C2"


def test_all_rollback_keeps_best_and_teaches_next_round() -> None:
    """全部回退时最优点不变，但失败方向和无提升次数必须持久化。"""
    proposal = make_proposal_state()
    baseline = proposal["best"]["summary"]
    bad_summary = make_summary(93.8, 96.0, 89.0, 14)
    record = make_candidate_record(
        "C3", 0.0065, bad_summary,
        make_decision(baseline, bad_summary, False, "滑行精度跌破保护线"),
    )
    experience = build_round_experience(1, proposal, [record], None)
    state = consolidate_round_state(proposal, [record], experience, None)
    prompt_memory = build_prompt_memory(state["optimization_memory"])

    assert state["best"] == proposal["best"]
    assert state["no_improvement_iterations"] == 1
    assert prompt_memory["recent_rounds"][0]["all_rolled_back"] is True
    rejected = prompt_memory["recent_rejected_directions"][0]
    assert rejected["candidate_id"] == "C3"
    assert rejected["validation"]["guard_failures"] == ["滑行精度跌破保护线"]
