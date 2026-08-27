"""Agent交互服务的只读数据与安全配置测试。"""

import threading
import time
from pathlib import Path

import server


def test_subprocess_environment_forces_utf8() -> None:
    """Windows管道日志必须统一为UTF-8，避免中文路径显示乱码。"""
    source = {"EXAMPLE": "保留原值"}
    environment = server.prepare_subprocess_environment(source)
    assert environment["EXAMPLE"] == "保留原值"
    assert environment["PYTHONIOENCODING"] == "utf-8"
    assert environment["PYTHONUTF8"] == "1"
    assert "PYTHONIOENCODING" not in source


def test_dashboard_never_exposes_api_key() -> None:
    """看板接口不得包含密钥字段内容。"""
    payload = server.dashboard_payload()
    assert "api_key" not in payload["api"]
    assert "scores" in payload
    assert "memory_rounds" in payload["agent"]


def test_latest_state_can_be_absent_after_history_cleanup(tmp_path, monkeypatch) -> None:
    """清理优化历史后应返回None，让任务从正式基线冷启动。"""
    monkeypatch.setattr(server, "OUTPUT_ROOT", tmp_path)
    assert server.latest_evaluation_state() is None


def test_cold_start_dashboard_uses_formal_baseline(tmp_path, monkeypatch) -> None:
    """没有历史状态时，看板仍应按当前16份有效样本正常显示。"""
    monkeypatch.setattr(server, "OUTPUT_ROOT", tmp_path)
    payload = server.dashboard_payload()
    assert payload["system"]["state_source"] == "formal_baseline"
    assert payload["system"]["state_path"] is None
    assert payload["agent"]["best_source"] == "formal_baseline_current_config"
    assert payload["scores"]["metric_checks"]["current"]["total"] == 64
    assert len(payload["failed_details"]) == payload["scores"]["metric_checks"]["current"]["failed"]


def test_cold_start_command_does_not_pass_missing_state(tmp_path) -> None:
    """首次运行命令不能把None字符串误传给--state。"""
    command = server.proposal_command(tmp_path / "proposal", False, None)
    assert "--state" not in command
    assert command[-1] == "--dry-run"

    continued = server.proposal_command(tmp_path / "proposal_2", True, Path("state.json"))
    assert continued[-1] == "--use-api"
    assert continued[continued.index("--state") + 1] == "state.json"


def test_metric_rows_apply_current_threshold() -> None:
    """优化看板状态应随当前分数变化，不能写死某个指标一定失败。"""
    rows = server.dashboard_payload()["metrics"]
    peak = [item for item in rows if item["role"] == "zero_to_100" and item["metric"] == "peak_ax"]
    assert len(peak) == 1
    assert peak[0]["passed"] == (peak[0]["score_pct"] >= peak[0]["target_pct"])


def test_dashboard_exposes_failed_metric_details() -> None:
    """未通过数量必须能追溯到具体工况、指标、当前精度和目标值。"""
    payload = server.dashboard_payload()
    details = payload["failed_details"]
    assert len(details) == payload["scores"]["current"]["failed_metric_count"]
    assert all({
        "role_label", "metric_label", "repeat_index", "dataset_split_label", "score_pct", "target_pct",
    } <= set(item) for item in details)
    assert all(float(item["score_pct"]) < float(item["target_pct"]) for item in details)


def test_dashboard_separates_mean_metrics_from_sample_checks() -> None:
    """12个工况均值不得与64次逐样本指标检查混用同一分母。"""
    payload = server.dashboard_payload()
    checks = payload["scores"]["metric_checks"]
    assert len(payload["metrics"]) == 12
    for name in ("current", "baseline"):
        assert checks[name]["total"] == 64
        assert checks[name]["failed"] == payload["scores"][name]["failed_metric_count"]
        assert checks[name]["passed"] + checks[name]["failed"] == checks[name]["total"]


def test_task_patience_does_not_inherit_previous_run() -> None:
    """新的完整优化任务必须从零计数，不能因上一任务回退过而首轮停止。"""
    assert server.task_no_improvement_after_round(2, False) == 3
    assert server.task_no_improvement_after_round(0, False) == 1
    assert server.task_no_improvement_after_round(2, True) == 0


def test_fresh_memory_mode_ignores_existing_history(monkeypatch) -> None:
    """全新优化必须从正式基线和空经验开始，不读取已有优化状态。"""
    expected = {"best": {"source": "formal_baseline"}, "optimization_memory": {"rounds": []}}
    monkeypatch.setattr(server, "latest_evaluation_state", lambda: Path("existing_state.json"))
    monkeypatch.setattr(server, "create_formal_baseline_state", lambda *_args: expected)
    state_path, state = server.load_start_state({}, {}, {}, memory_mode="fresh")
    assert state_path is None
    assert state is expected
    assert state["optimization_memory"]["rounds"] == []


def test_invalid_memory_mode_is_rejected() -> None:
    """非约定经验模式不得静默退化为继承模式。"""
    try:
        server.load_start_state({}, {}, {}, memory_mode="unknown")
    except ValueError as error:
        assert "inherit或fresh" in str(error)
    else:
        raise AssertionError("非法经验模式应被拒绝")


def test_full_optimization_preflight_blocks_missing_admission(tmp_path, monkeypatch) -> None:
    """即使CarSim文件存在，合格数据不足也必须阻止真实闭环。"""
    solver = tmp_path / "solver.exe"
    library = tmp_path / "carsim.dll"
    solver.write_bytes(b"placeholder")
    library.write_bytes(b"placeholder")
    monkeypatch.setattr(server, "CARSIM_SOLVER", solver)
    monkeypatch.setitem(server.RUNTIME_PATHS, "carsim_dll", library)
    monkeypatch.setattr(server, "admission_payload", lambda: {"ready_for_optimization": False})
    monkeypatch.setattr(
        server, "load_project_config",
        lambda: {"data_admission": {"enforce_for_full_optimization": True}},
    )
    try:
        server.full_optimization_preflight()
    except RuntimeError as error:
        assert "合格实车数据不足" in str(error)
    else:
        raise AssertionError("准入未就绪时不应启动真实闭环")


def test_parameter_change_summary_is_dynamic() -> None:
    """状态卡应汇总所有偏离基线的参数，不能固定展示低速扭矩。"""
    registry = {
        "parameters": {
            "a": {"label_zh": "参数A", "baseline": 1.0, "unit": "ratio"},
            "b": {"label_zh": "参数B", "baseline": 2.0, "unit": "kg"},
        },
    }
    baseline = server.parameter_change_summary({"a": 1.0, "b": 2.0}, registry)
    changed = server.parameter_change_summary({"a": 1.1, "b": 2.2}, registry)
    assert baseline == {"count": 0, "text": "正式基线", "details": []}
    assert changed["count"] == 2
    assert changed["text"] == "已调整 2 项"


def wait_until_status(manager: server.JobManager, expected: str) -> None:
    """短时间等待控制线程进入预期状态，避免测试依赖固定长延时。"""
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if manager.snapshot()["status"] == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"任务未进入状态：{expected}")


def test_pause_resume_at_safe_boundary() -> None:
    """暂停应阻塞在候选边界，继续后原工作线程恢复。"""
    manager = server.JobManager()
    manager.state.update({"status": "running", "mode": "full_iteration"})
    manager.request_pause()
    result: list[bool] = []
    worker = threading.Thread(target=lambda: result.append(manager.wait_for_control_boundary("继续评价")))
    worker.start()
    wait_until_status(manager, "paused")
    manager.resume()
    worker.join(timeout=2.0)
    assert result == [True]
    assert manager.snapshot()["status"] == "running"


def test_safe_stop_unblocks_paused_worker() -> None:
    """暂停状态请求停止时必须唤醒线程并返回停止信号。"""
    manager = server.JobManager()
    manager.state.update({"status": "running", "mode": "full_iteration"})
    manager.request_pause()
    result: list[bool] = []
    worker = threading.Thread(target=lambda: result.append(manager.wait_for_control_boundary("继续评价")))
    worker.start()
    wait_until_status(manager, "paused")
    manager.request_stop()
    worker.join(timeout=2.0)
    assert result == [False]
    assert manager.snapshot()["status"] == "stopping"
