"""验证前端、评价器和Agent共用项目根config.json。"""

import json
from pathlib import Path

from config_loader import load_agent_config, load_project_config
from config_loader import evaluation_config_fingerprint, state_config_sync_status
from Agent交互界面.server import metric_target_pct
from llm_optimizer.llm_client import resolve_timeout_s


def test_agent_runtime_config_is_derived_from_root_config() -> None:
    """Agent关键运行值必须来自项目根config.json中的agent区。"""
    project = load_project_config()
    agent = load_agent_config()

    assert agent["maximum_iterations"] == project["agent"]["maximum_iterations"]
    assert agent["minimum_improvement_pct"] == project["agent"]["minimum_improvement_pct"]
    assert agent["dataset_splits"] == project["agent"]["dataset_splits"]
    assert agent["metric_thresholds"] == project["metric_thresholds"]
    assert agent["individual_targets"] == project["metric_thresholds"]


def test_frontend_metric_targets_follow_root_thresholds() -> None:
    """前端百分制目标应由评价器原始阈值动态换算。"""
    thresholds = load_project_config()["metric_thresholds"]

    assert metric_target_pct("speed_r2", thresholds) == thresholds["speed_r2_min"] * 100.0
    assert metric_target_pct("speed_nrmse", thresholds) == (1.0 - thresholds["speed_nrmse_max"]) * 100.0
    assert metric_target_pct("peak_ax", thresholds) == thresholds["peak_ax_accuracy_min_pct"]


def test_chart_axis_and_api_timeout_are_configurable() -> None:
    """图表范围和API超时应有明确配置来源，不依赖前端或客户端常量。"""
    project = load_project_config()
    assert project["chart_axis"]["ticks_pct"]
    assert resolve_timeout_s({"llm_timeout_s": 17.0}) == 17.0
    assert resolve_timeout_s({}, 8.0) == 8.0


def test_state_policy_mismatch_is_detected() -> None:
    """修改数据划分或评价阈值后，旧状态必须被识别为不同口径。"""
    project = load_project_config()
    state = {
        "evaluation_config_fingerprint": evaluation_config_fingerprint(project),
        "best": {"summary": {"weights": project["longitudinal_weights"]}},
    }
    assert state_config_sync_status(state, project)["status"] == "matched"
    changed = json.loads(json.dumps(project))
    changed["metric_thresholds"]["speed_r2_min"] = 0.95
    assert state_config_sync_status(state, changed)["status"] == "stale"


def test_legacy_agent_config_file_is_removed() -> None:
    """旧版Agent配置文件不应继续存在，避免产生多份配置入口。"""
    legacy_path = Path(__file__).parent / "llm_optimizer" / "config" / "agent_config.json"
    assert not legacy_path.exists()
