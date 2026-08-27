"""构造只允许输出结构化候选的LLM提示词。"""

from __future__ import annotations

import json
from typing import Any


def build_messages(
    baseline_summary: dict[str, Any],
    current_parameters: dict[str, float],
    registry: dict[str, Any],
    config: dict[str, Any],
    optimization_memory: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """把偏差、参数边界和耦合风险组织成OpenAI兼容消息。"""
    compact_parameters = {
        name: {
            "current": current_parameters[name],
            "range": [spec["minimum"], spec["maximum"]],
            "max_delta": spec["max_change_per_iteration"],
            "effects": spec["main_effects"],
            "risk": spec["risk"],
        }
        for name, spec in registry["parameters"].items()
    }
    system = (
        "你是CarSim纵向动力学参数优化器。你只能提出参数候选，不能修改控制Trace、评价公式、"
        "主减速比或CarSim底层模型。必须考虑参数耦合、物理边界和单轮变化限制。"
        "不要输出思考或分析过程，响应第一个字符必须是{。"
        "输出必须是简短JSON对象，字段为diagnosis、candidates、stop_reason；禁止输出Markdown，整个响应不超过1200 tokens。"
        "每个候选的changes必须是数组，每项必须且只需包含parameter和value，"
        "例如{\"parameter\":\"rr_c\",\"value\":0.0065}；不要使用old_value或new_value。"
        "candidate_id必须是字符串，例如C1、C2、C3，不要输出数字类型。"
        "三个候选必须分工：C1沿当前最优基线小步利用；C2只融合经验记忆中已通过全部验收但未胜出的非冲突变化；"
        "C3避开近期回退方向并探索新的参数组合。任何历史变化都不能未经本轮CarSim验证直接写入最优状态。"
    )
    user_payload = {
        "task": f"在保持加速、滑行和纵向综合精度均不低于{float(config['optimization_target_pct']):g}%的前提下，优先减少未通过的单项指标",
        "baseline": baseline_summary,
        "current_parameters": current_parameters,
        "parameter_constraints": compact_parameters,
        "locked_parameters": registry["locked_parameters"],
        "candidate_limits": {
            "maximum_candidates": config["maximum_candidates_per_iteration"],
            "maximum_changes_each": config["maximum_parameter_changes_per_candidate"],
        },
        "data_split": {
            "by_role": config.get("dataset_splits"),
            "rule": "LLM只依据标定集提出参数；验证集仅由程序进行接受或回退判断",
        },
        "coasting_test_condition": config.get("coasting_test_condition"),
        "optimization_memory": optimization_memory or {"available": False},
        "candidate_strategy": {
            "C1": "沿当前最优参数继续小步优化当前最弱指标",
            "C2": "当前最优基线加已验证非胜者的非冲突收益；没有可靠融合来源时采用与C1不同的互补参数组",
            "C3": "避开近期回退的相同参数与方向，探索新的物理合理方向",
            "all_rolled_back_rule": "若最近一轮全部回退，不得原样重复相同参数组合；应根据验证集退化和保护线失败原因缩小、反向或更换参数组",
        },
        "required_candidate_fields": ["candidate_id", "rationale", "changes", "expected_effects", "risks"],
        "required_change_format": [{"parameter": "parameter_name", "value": "numeric_value"}],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
