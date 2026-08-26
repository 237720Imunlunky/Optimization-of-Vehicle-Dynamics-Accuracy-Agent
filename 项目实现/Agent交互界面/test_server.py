"""Agent交互服务的只读数据与安全配置测试。"""

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


def test_latest_state_is_completed_evaluation() -> None:
    """界面必须读取CarSim评价状态，而不是未评价干运行候选。"""
    state = server.latest_evaluation_state()
    assert "carsim_eval" in state.parent.name


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
    assert all({"role_label", "metric_label", "score_pct", "target_pct"} <= set(item) for item in details)
    assert all(float(item["score_pct"]) < float(item["target_pct"]) for item in details)


def test_task_patience_does_not_inherit_previous_run() -> None:
    """新的完整优化任务必须从零计数，不能因上一任务回退过而首轮停止。"""
    assert server.task_no_improvement_after_round(2, False) == 3
    assert server.task_no_improvement_after_round(0, False) == 1
    assert server.task_no_improvement_after_round(2, True) == 0
