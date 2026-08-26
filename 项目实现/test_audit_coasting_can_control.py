"""滑行原始CAN审计的关键策略测试。"""

from audit_coasting_can_control import build_conclusion


def test_n_gear_samples_use_one_shared_zero_torque_simulation() -> None:
    """有效N挡样本控制一致时，不应要求像加速工况一样逐条回放。"""
    records = [
        {
            "repeat_index": repeat_index,
            "classification": "valid_n_comfort_coast",
            "regen_torque_d_nm": {"mean": 0.0},
        }
        for repeat_index in (1, 2, 4, 5)
    ]
    records.extend([
        {"repeat_index": 3, "classification": "control_condition_mismatch"},
        {"repeat_index": 6, "classification": "excluded_missing_strict_50_to_30_window"},
    ])

    conclusion = build_conclusion(records)

    assert conclusion["eligible_repeats"] == [1, 2, 4, 5]
    assert conclusion["control_mismatch_repeats"] == [3]
    assert conclusion["strict_window_excluded_repeats"] == [6]
    assert conclusion["recommended_simulation_route"] == "shared_n_gear_zero_torque_condition"
    assert conclusion["individual_control_trace_replay_required"] is False
