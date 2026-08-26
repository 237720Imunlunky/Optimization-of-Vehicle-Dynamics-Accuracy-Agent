"""参数注册表读取与LLM候选安全校验。"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from config_loader import load_agent_config as load_central_agent_config


ROOT = Path(__file__).resolve().parent


def load_json(path: Path) -> dict[str, Any]:
    """读取JSON配置，统一UTF-8处理。"""
    return json.loads(path.read_text(encoding="utf-8"))


def load_registry() -> dict[str, Any]:
    """读取可调参数和锁定参数注册表。"""
    return load_json(ROOT / "config" / "parameter_registry.json")


def load_agent_config() -> dict[str, Any]:
    """读取项目根config.json中的Agent运行配置。"""
    return load_central_agent_config()


def baseline_parameters(registry: dict[str, Any]) -> dict[str, float]:
    """提取注册表中的当前修正版基线值。"""
    return {name: float(spec["baseline"]) for name, spec in registry["parameters"].items()}


def validate_candidate(
    candidate: dict[str, Any],
    current: dict[str, float],
    registry: dict[str, Any],
    maximum_changes: int,
) -> dict[str, Any]:
    """拒绝未知、重复、越界或单轮变化过大的参数候选。"""
    errors: list[str] = []
    changes = candidate.get("changes")
    # 兼容部分模型将参数变更输出为对象的情况，同时统一转换为安全校验所需数组。
    if isinstance(changes, dict):
        changes = [{"parameter": name, "value": value} for name, value in changes.items()]
    if not isinstance(changes, list) or not changes:
        return {"valid": False, "errors": ["changes必须是非空数组"], "normalized_changes": {}}
    if len(changes) > maximum_changes:
        errors.append(f"单候选最多修改{maximum_changes}项，实际为{len(changes)}项")

    normalized: dict[str, float] = {}
    available = registry["parameters"]
    locked = registry["locked_parameters"]
    for item in changes:
        name = item.get("parameter") if isinstance(item, dict) else None
        # 优先读取标准value；兼容部分模型输出的old_value/new_value结构。
        value = item.get("value", item.get("new_value")) if isinstance(item, dict) else None
        if name in locked:
            errors.append(f"禁止修改锁定参数：{name}")
            continue
        if name not in available:
            errors.append(f"未知参数：{name}")
            continue
        if name in normalized:
            errors.append(f"参数重复出现：{name}")
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
            errors.append(f"参数{name}必须是有限数值")
            continue

        spec = available[name]
        numeric = round(float(value), int(spec["precision"]))
        if not float(spec["minimum"]) <= numeric <= float(spec["maximum"]):
            errors.append(f"参数{name}={numeric}超出物理边界[{spec['minimum']}, {spec['maximum']}]")
            continue
        previous = float(current.get(name, spec["baseline"]))
        max_delta = float(spec["max_change_per_iteration"])
        if abs(numeric - previous) > max_delta + 1e-12:
            errors.append(f"参数{name}单轮变化{abs(numeric - previous):g}超过上限{max_delta:g}")
            continue
        normalized[name] = numeric

    return {"valid": not errors and bool(normalized), "errors": errors, "normalized_changes": normalized}


def validate_proposal(payload: dict[str, Any], current: dict[str, float]) -> dict[str, Any]:
    """校验完整LLM响应，并分别给出可执行和被拒绝候选。"""
    registry = load_registry()
    config = load_agent_config()
    candidates = payload.get("candidates")
    if not isinstance(payload.get("diagnosis"), str) or not payload["diagnosis"].strip():
        raise ValueError("LLM响应缺少diagnosis")
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= int(config["maximum_candidates_per_iteration"]):
        raise ValueError("LLM响应的candidates数量不合规")

    accepted, rejected = [], []
    identifiers: set[str] = set()
    for candidate in candidates:
        raw_identifier = candidate.get("candidate_id") if isinstance(candidate, dict) else None
        # 命令行参数只能传递字符串，因此在安全校验阶段统一候选编号类型。
        identifier = str(raw_identifier).strip() if isinstance(raw_identifier, (str, int)) and not isinstance(raw_identifier, bool) else ""
        if not identifier or identifier in identifiers:
            rejected.append({"candidate_id": identifier or raw_identifier, "valid": False, "errors": ["candidate_id缺失或重复"]})
            continue
        identifiers.add(identifier)
        checked = validate_candidate(
            candidate, current, registry, int(config["maximum_parameter_changes_per_candidate"]),
        )
        source = dict(candidate)
        source["candidate_id"] = identifier
        item = {"candidate_id": identifier, **checked, "source": source}
        (accepted if checked["valid"] else rejected).append(item)
    return {"diagnosis": payload["diagnosis"], "accepted": accepted, "rejected": rejected}
