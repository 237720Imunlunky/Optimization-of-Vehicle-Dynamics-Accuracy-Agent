"""项目唯一运行配置入口。"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
CONDITION_REGISTRY_PATH = PROJECT_ROOT / "conditions" / "condition_registry.json"


def load_project_config() -> dict[str, Any]:
    """读取项目根config.json，所有前端和Agent运行参数均从此入口获得。"""
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(f"缺少项目唯一配置文件：{CONFIG_PATH}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"项目配置JSON格式错误：{CONFIG_PATH}: {error}") from error
    validate_project_config(config)
    return config


def validate_project_config(config: dict[str, Any]) -> None:
    """在启动阶段检查关键配置，避免前端和Agent静默使用默认值。"""
    required = (
        "formal_acceptance_threshold_pct", "longitudinal_weights", "metric_thresholds", "agent", "chart_axis",
        "history_retention", "experience_policy", "data_admission",
    )
    missing = [name for name in required if name not in config]
    if missing:
        raise ValueError(f"config.json缺少必填配置：{', '.join(missing)}")
    weights = config["longitudinal_weights"]
    for group in ("acceleration", "coasting"):
        if group not in weights or float(weights[group]) < 0:
            raise ValueError(f"longitudinal_weights缺少有效分组：{group}")
    thresholds = config["metric_thresholds"]
    threshold_names = (
        "speed_r2_min", "speed_nrmse_max", "peak_ax_accuracy_min_pct",
        "coasting_distance_accuracy_min_pct", "target_time_accuracy_min_pct",
    )
    missing_thresholds = [name for name in threshold_names if name not in thresholds]
    if missing_thresholds:
        raise ValueError(f"metric_thresholds缺少必填字段：{', '.join(missing_thresholds)}")
    chart_axis = config["chart_axis"]
    chart_min = float(chart_axis.get("minimum_pct", 0))
    chart_max = float(chart_axis.get("maximum_pct", 0))
    ticks = chart_axis.get("ticks_pct")
    if chart_min >= chart_max or not isinstance(ticks, list) or not ticks:
        raise ValueError("chart_axis必须包含有效的minimum_pct、maximum_pct和ticks_pct")
    if any(chart_min > float(tick) or float(tick) > chart_max for tick in ticks):
        raise ValueError("chart_axis.ticks_pct必须位于坐标轴范围内")
    agent = config["agent"]
    agent_names = (
        "optimization_target_pct", "maximum_iterations", "maximum_candidates_per_iteration",
        "maximum_parameter_changes_per_candidate", "minimum_improvement_pct",
        "stop_after_no_improvement_iterations", "dataset_splits", "hard_guards",
        "coasting_test_condition",
    )
    missing_agent = [name for name in agent_names if name not in agent]
    if missing_agent:
        raise ValueError(f"config.json.agent缺少必填字段：{', '.join(missing_agent)}")
    condition = agent["coasting_test_condition"]
    window = condition.get("window_kmh")
    if not isinstance(window, list) or len(window) != 2 or float(window[0]) <= float(window[1]):
        raise ValueError("config.json.agent.coasting_test_condition.window_kmh必须是递减的两个车速")
    duration = float(condition.get("simulation_duration_s", 0))
    if duration <= 0:
        raise ValueError("滑行仿真时长simulation_duration_s必须大于0")
    retention = config["history_retention"]
    if int(retention.get("full_task_count", 0)) < 1 or int(retention.get("state_history_limit", 0)) < 1:
        raise ValueError("历史保留数量和状态历史上限必须为正整数")
    experience = config["experience_policy"]
    if int(experience.get("prompt_character_budget", 0)) < 1000:
        raise ValueError("经验提示词字符预算不得小于1000")
    minimum = config["data_admission"].get("minimum_samples", {})
    if int(minimum.get("calibration", 0)) < 1 or int(minimum.get("validation", 0)) < 1:
        raise ValueError("标定集和验证集最小样本数必须为正整数")


def load_condition_registry() -> dict[str, Any]:
    """读取通用工况注册表，并检查启用工况的关键扩展接口。"""
    try:
        registry = json.loads(CONDITION_REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(f"缺少工况注册表：{CONDITION_REGISTRY_PATH}") from error
    required = {"domain", "enabled", "simulator_adapter", "evaluator", "metrics", "admission"}
    for name, condition in registry.get("conditions", {}).items():
        missing = required - set(condition)
        if missing:
            raise ValueError(f"工况{name}缺少字段：{', '.join(sorted(missing))}")
    return registry


def load_agent_config() -> dict[str, Any]:
    """返回Agent使用的扁平运行配置，数据源唯一为config.json。"""
    project = load_project_config()
    merged = copy.deepcopy(project)
    merged.update(copy.deepcopy(project["agent"]))
    # 将正式验收阈值映射为Agent内部使用的字段，避免重复维护数值。
    merged["formal_threshold_pct"] = float(project["formal_acceptance_threshold_pct"])
    # 单项目标直接镜像项目评价阈值，避免在agent区再维护一套容易漂移的数值。
    merged["individual_targets"] = copy.deepcopy(project["metric_thresholds"])
    default_split = project["agent"]["dataset_splits"].get("zero_to_100", {})
    merged["calibration_repeats"] = list(default_split.get("calibration", []))
    merged["validation_repeats"] = list(default_split.get("validation", []))
    return merged


def evaluation_config_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    """提取会改变评价结论或Agent接受判定的配置，排除界面展示配置。"""
    registry = load_condition_registry()
    return {
        "formal_acceptance_threshold_pct": config["formal_acceptance_threshold_pct"],
        "longitudinal_weights": config["longitudinal_weights"],
        "metric_thresholds": config["metric_thresholds"],
        "dataset_splits": config["agent"]["dataset_splits"],
        "coasting_test_condition": config["agent"]["coasting_test_condition"],
        "hard_guards": config["agent"]["hard_guards"],
        "condition_registry": registry,
        "parameter_registry_sha256": hashlib.sha256(
            (PROJECT_ROOT / "llm_optimizer" / "config" / "parameter_registry.json").read_bytes(),
        ).hexdigest()[:16],
    }


def evaluation_config_fingerprint(config: dict[str, Any]) -> str:
    """为状态和输出生成稳定配置指纹，配置修改后可识别旧口径结果。"""
    payload = json.dumps(evaluation_config_snapshot(config), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def state_config_sync_status(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """判断历史状态是否与当前评价配置同口径，并给出可操作的原因。"""
    expected = evaluation_config_fingerprint(config)
    stored = state.get("evaluation_config_fingerprint")
    if stored:
        return {"status": "matched" if stored == expected else "stale", "expected": expected, "stored": stored}
    policy = state.get("data_policy")
    if not policy:
        return {"status": "unknown", "expected": expected, "stored": None, "reason": "历史状态没有配置快照"}
    current_agent = config["agent"]
    semantic_condition_keys = ("window_kmh", "drive_mode", "gear", "accelerator_input", "brake_input", "regeneration", "policy_version")
    old_condition = policy.get("coasting_condition", {})
    new_condition = current_agent["coasting_test_condition"]
    condition_matches = all(old_condition.get(key) == new_condition.get(key) for key in semantic_condition_keys if key in old_condition)
    matches = (
        policy.get("dataset_splits") == current_agent["dataset_splits"]
        and condition_matches
        and state.get("best", {}).get("summary", {}).get("weights") == config["longitudinal_weights"]
    )
    return {
        "status": "legacy_matched" if matches else "stale",
        "expected": expected,
        "stored": None,
        "reason": "通过历史数据策略字段核对" if matches else "历史数据划分、权重或滑行工况与当前配置不一致",
    }


def stamp_state_config(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """给新生成的Agent状态写入口径指纹，便于后续配置变更自动发现。"""
    stamped = copy.deepcopy(state)
    stamped["evaluation_config_fingerprint"] = evaluation_config_fingerprint(config)
    return stamped
