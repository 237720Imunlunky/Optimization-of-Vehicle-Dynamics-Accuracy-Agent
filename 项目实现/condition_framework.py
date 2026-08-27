"""通用工况扩展框架：注册工况、评价插件和分阶段优化顺序。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from config_loader import load_condition_registry
from evaluate_longitudinal import compare_pair


Evaluator = Callable[[Path, Path, str, dict[str, Any], dict[str, Any]], dict[str, Any]]


def _longitudinal_acceleration(
    truth: Path, simulation: Path, condition_id: str, rules: dict[str, Any], condition: dict[str, Any],
) -> dict[str, Any]:
    """现有加速类工况评价插件。"""
    return compare_pair(truth, simulation, condition_id, rules)


def _longitudinal_coasting(
    truth: Path, simulation: Path, condition_id: str, rules: dict[str, Any], condition: dict[str, Any],
) -> dict[str, Any]:
    """现有滑行类工况评价插件。"""
    return compare_pair(truth, simulation, condition_id, rules, condition["admission"]["window_kmh"])


EVALUATORS: dict[str, Evaluator] = {
    "longitudinal_acceleration": _longitudinal_acceleration,
    "longitudinal_coasting": _longitudinal_coasting,
}


def enabled_conditions(domain: str | None = None) -> dict[str, dict[str, Any]]:
    """按注册表返回已启用工况，可选择单一动力学方向。"""
    conditions = load_condition_registry()["conditions"]
    return {
        name: condition for name, condition in conditions.items()
        if condition.get("enabled") and (domain is None or condition.get("domain") == domain)
    }


def evaluate_registered_condition(
    truth: Path, simulation: Path, condition_id: str, rules: dict[str, Any],
) -> dict[str, Any]:
    """按工况配置选择评价插件，未知插件明确报错而不是静默跳过。"""
    condition = enabled_conditions().get(condition_id)
    if condition is None:
        raise ValueError(f"工况未启用或不存在：{condition_id}")
    evaluator_name = str(condition["evaluator"])
    evaluator = EVALUATORS.get(evaluator_name)
    if evaluator is None:
        raise ValueError(f"工况{condition_id}引用了未注册评价器：{evaluator_name}")
    return evaluator(truth, simulation, condition_id, rules, condition)


def optimization_phases() -> list[dict[str, Any]]:
    """生成分方向优化、全工况回归的稳定阶段定义。"""
    domains = []
    for condition in enabled_conditions().values():
        domain = str(condition["domain"])
        if domain not in domains:
            domains.append(domain)
    return [
        {"phase": domain, "open_parameter_domains": [domain, "shared"], "regression_conditions": list(enabled_conditions())}
        for domain in domains
    ]
