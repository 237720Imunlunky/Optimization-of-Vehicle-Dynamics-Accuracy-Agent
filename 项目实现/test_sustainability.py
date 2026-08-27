"""可持续运行、数据准入、扩展框架和历史保护测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import history_manager
import runtime_paths
from condition_framework import enabled_conditions, optimization_phases
from data_admission import acceleration_checks, automatic_status, coasting_checks, missing_required_evidence
from llm_optimizer.experience_memory import deduplicate_records, fit_prompt_budget
from llm_optimizer.state_store import compact_state_history, create_initial_state


def simple_summary() -> dict:
    """建立状态测试所需的最小正式摘要。"""
    return {"longitudinal_score_pct": 90.0, "group_scores_pct": {"acceleration": 90.0, "coasting": 90.0}}


def test_state_history_is_bounded_and_archived() -> None:
    """详细历史超限后只裁剪旧记录，同时保留状态计数审计。"""
    state = create_initial_state({"p": 1.0}, simple_summary())
    state["history"] = [{"status": "accepted" if index % 2 else "rejected"} for index in range(100)]
    compact_state_history(state, limit=12)
    assert len(state["history"]) == 12
    assert state["history_archive"]["compacted_records"] == 88
    assert sum(state["history_archive"]["status_counts"].values()) == 88


def test_experience_is_deduplicated_and_fits_budget() -> None:
    """同参数方向只保留最近记录，提示词经验不得超过预算。"""
    records = [
        {"candidate_id": f"C{index}", "parameter_changes": {"rr_c": {"delta": 0.001}}, "detail": "x" * 1000}
        for index in range(5)
    ]
    deduplicated = deduplicate_records(records, "policy-v1")
    assert [item["candidate_id"] for item in deduplicated] == ["C4"]
    payload = {
        "recent_rounds": [{"detail": "x" * 800}],
        "validated_fusion_sources": [{"detail": "y" * 800}],
        "recent_rejected_directions": [{"detail": "z" * 800}],
    }
    fitted = fit_prompt_budget(payload, 1000)
    assert fitted["serialized_characters"] <= 1000


def test_missing_manual_evidence_stays_pending_review() -> None:
    """自动信号全部通过时，缺挡位/模式/路面证据仍不得直接进入优化。"""
    checks = [{"passed": True, "reason": "通过"}]
    status, reasons = automatic_status(checks, ["gear", "drive_mode", "road_condition"], None, None)
    assert status == "pending_review"
    assert "gear" in reasons[0]


def test_verified_can_evidence_is_not_requested_again() -> None:
    """原始CAN已证明的挡位和模式不得因统一CSV缺列而重复要求人工确认。"""
    evidence = {
        "gear": {"verified": True, "source": "原始BLF+DBC"},
        "drive_mode": {"verified": True, "source": "原始BLF+DBC"},
    }
    missing = missing_required_evidence(["gear", "drive_mode", "road_condition"], evidence)
    assert missing == ["road_condition"]


def test_scoped_review_cannot_override_unverified_can_evidence() -> None:
    """只确认路面的人工记录不能顺带放行尚未验证的挡位证据。"""
    review = {
        "decision": "accepted", "basis": "已确认平直正常附着路面",
        "evidence_scope": ["road_condition"],
    }
    status, reasons = automatic_status(
        [{"passed": True, "reason": "通过"}], ["gear", "road_condition"], review, None,
    )
    assert status == "pending_review"
    assert "gear" in reasons[0]


def test_known_coasting_failures_are_detected() -> None:
    """缺少50 km/h起点或窗口内制动均应被自动规则拒绝。"""
    rules = {
        "window_kmh": [50.0, 30.0], "accelerator_max_pct": 0.5, "accelerator_max_fraction": 0.01,
        "brake_active_threshold": 0.0, "brake_max_fraction": 0.01, "steer_range_max_deg": 15.0,
    }
    missing_start = [{"time_s": index, "speed_kmh": 48.0 - index, "accel_pedal_pct": 0.0, "brake_pedal": 0.0} for index in range(20)]
    assert not coasting_checks(missing_start, rules)[0]["passed"]
    braking = [
        {"time_s": index, "speed_kmh": 51.0 - index, "accel_pedal_pct": 0.0, "brake_pedal": 1.0 if index == 10 else 0.0}
        for index in range(23)
    ]
    checks = coasting_checks(braking, rules)
    assert not next(item for item in checks if item["rule"] == "no_brake")["passed"]


def write_verified_summary(path: Path, task_id: str) -> None:
    """建立历史管理测试使用的自校验摘要。"""
    payload = {"task_id": task_id, "experience_extracted": True, "experience_summary": [{"iteration": 1}]}
    payload["summary_sha256"] = history_manager.canonical_checksum(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_history_protects_recent_and_current_tasks(tmp_path: Path, monkeypatch) -> None:
    """只允许清理已有验证摘要、不是最近三次且不是当前状态来源的任务。"""
    output = tmp_path / "LLM参数优化Agent"
    for index in range(5):
        task_id = f"ui_2026010{index + 1}_120000"
        folder = output / f"{task_id}_iter_01_C1_carsim_eval"
        folder.mkdir(parents=True)
        (folder / "payload.bin").write_bytes(b"x" * 100)
        (folder / "agent_state.json").write_text(json.dumps({"best": {"source": "C1"}}), encoding="utf-8")
        os.utime(folder / "agent_state.json", (1000 + index, 1000 + index))
        write_verified_summary(output / "任务档案" / task_id / "task_summary.json", task_id)
    monkeypatch.setattr(history_manager, "load_runtime_paths", lambda: {"output_root": tmp_path})
    overview = history_manager.history_overview()
    eligible = [item["task_id"] for item in overview["tasks"] if item["cleanup_eligible"]]
    assert eligible == ["ui_20260102_120000", "ui_20260101_120000"]
    assert overview["current_task_id"] == "ui_20260105_120000"


def test_history_cleanup_requires_explicit_confirmation(tmp_path: Path, monkeypatch) -> None:
    """确认文字错误时必须在扫描和删除前终止，真实历史不得受影响。"""
    protected_file = tmp_path / "LLM参数优化Agent" / "ui_20260101_120000_iter_01" / "payload.bin"
    protected_file.parent.mkdir(parents=True)
    protected_file.write_bytes(b"keep")
    monkeypatch.setattr(history_manager, "load_runtime_paths", lambda: {"output_root": tmp_path})
    try:
        history_manager.cleanup_eligible_tasks("错误确认文字")
    except ValueError as error:
        assert "未删除任何文件" in str(error)
    else:
        raise AssertionError("错误确认文字不应允许清理")
    assert protected_file.read_bytes() == b"keep"


def test_runtime_path_allows_other_drives_but_requires_ascii() -> None:
    """公开版允许用户选择盘符，但CarSim Runtime必须是绝对ASCII路径。"""
    valid = {"output_root": Path("F:/Agent/output"), "runtime_root": Path("F:/Agent/runtime")}
    runtime_paths.ensure_f_drive_for_mutable_paths(valid)
    other_drive = {"output_root": Path("C:/Agent/output"), "runtime_root": Path("C:/Agent/runtime")}
    runtime_paths.ensure_f_drive_for_mutable_paths(other_drive)

    chinese_runtime = {"output_root": Path("F:/Agent/output"), "runtime_root": Path("F:/车辆/运行目录")}
    try:
        runtime_paths.ensure_f_drive_for_mutable_paths(chinese_runtime)
    except ValueError as error:
        assert "纯英文ASCII" in str(error)
    else:
        raise AssertionError("CarSim中文运行目录不应通过检查")


def test_runtime_config_accepts_windows_powershell_bom(tmp_path: Path, monkeypatch) -> None:
    """Windows PowerShell 5生成的带BOM本机配置必须可以正常读取。"""
    config_path = tmp_path / "runtime.local.json"
    config_path.write_text('{"runtime_root":"F:/runtime"}', encoding="utf-8-sig")
    monkeypatch.setattr(runtime_paths, "LOCAL_PATH_CONFIG", config_path)
    assert runtime_paths._read_local_config()["runtime_root"] == "F:/runtime"


def test_explicit_carsim_path_is_not_silently_replaced(tmp_path: Path) -> None:
    """用户显式配置的CarSim路径即使缺失也应原样返回，由体检明确报错。"""
    configured = tmp_path / "custom_carsim"
    assert runtime_paths.discover_carsim_root(configured) == configured.resolve()


def test_public_runtime_assets_are_self_contained() -> None:
    """GitHub干净克隆所需的转换器和演示基线必须位于项目仓库内。"""
    assert runtime_paths.BUNDLED_CONVERTER.is_file()
    assert runtime_paths.DEMO_FORMAL_RESULT.is_file()
    demo = json.loads(runtime_paths.DEMO_FORMAL_RESULT.read_text(encoding="utf-8"))
    assert len(demo["results"]) == 18
    assert all(str(item["truth"]).startswith("demo://") for item in demo["results"])


def test_condition_registry_exposes_staged_global_regression() -> None:
    """现有工况通过注册表启用，并进入纵向阶段的全工况回归集合。"""
    conditions = enabled_conditions()
    assert set(conditions) == {"zero_to_100", "overtaking", "coasting"}
    phases = optimization_phases()
    assert phases[0]["phase"] == "longitudinal"
    assert set(phases[0]["regression_conditions"]) == set(conditions)
