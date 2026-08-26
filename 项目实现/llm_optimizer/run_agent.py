"""LLM参数优化Agent第一阶段入口：生成并安全校验候选。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .llm_client import request_json
from .experience_memory import build_prompt_memory
from .objective import summarize_formal_result
from .parameter_space import baseline_parameters, load_agent_config, load_registry, validate_proposal
from .prompt_builder import build_messages
from .state_store import create_initial_state, record_proposal, write_state
from config_loader import stamp_state_config, state_config_sync_status


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FORMAL_RESULT = PROJECT_ROOT / "输出" / "正式联合基线" / "当前配置基线" / "formal_acceptance.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "输出" / "LLM参数优化Agent" / "manual_dry_run"


def mock_response(current: dict[str, float]) -> dict[str, Any]:
    """模拟LLM提出一个小步候选，只用于验证协议与边界拦截。"""
    next_torque_scale = round(min(1.1, current["motor_low_speed_torque_scale"] + 0.02), 4)
    return {
        "diagnosis": "0-100峰值加速度偏低，应先小幅提高低速扭矩；保持滚阻不变以避免破坏滑行基线。",
        "candidates": [
            {
                "candidate_id": "candidate_low_torque_plus_03",
                "rationale": "低速电机扭矩直接影响0-100峰值加速度，先使用单轮允许的最大小步验证灵敏度。",
                "changes": [{"parameter": "motor_low_speed_torque_scale", "value": next_torque_scale}],
                "expected_effects": ["提高0-100峰值加速度精度", "缩短0-100时间"],
                "risks": ["可能使起步加速度过冲，因此必须复验全部加速工况"],
            },
            {
                "candidate_id": "candidate_invalid_guard_test",
                "rationale": "故意修改锁定参数，用于证明安全拦截有效。",
                "changes": [{"parameter": "front_final_drive", "value": 12.0}],
                "expected_effects": [],
                "risks": ["违反锁定参数约束"],
            },
        ],
        "stop_reason": None,
    }


def run(formal_result: Path, output: Path, use_mock: bool, state_path: Path | None = None) -> dict[str, Any]:
    """构建提示词、获取候选、校验并保存可追溯的第一轮状态。"""
    if output.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output}")
    output.mkdir(parents=True)
    formal = json.loads(formal_result.read_text(encoding="utf-8"))
    registry = load_registry()
    config = load_agent_config()
    if state_path is None:
        parameters = baseline_parameters(registry)
        summary = summarize_formal_result(formal)
        state = create_initial_state(parameters, summary)
    else:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        sync = state_config_sync_status(state, config)
        if sync["status"] == "stale":
            raise RuntimeError(
                "上一轮状态与当前config.json评价口径不一致；请先重新运行正式基线或迁移基线，"
                f"当前配置指纹={sync['expected']}，历史状态原因：{sync.get('reason', '指纹不一致')}"
            )
        parameters = {name: float(value) for name, value in state["best"]["parameters"].items()}
        summary = state["best"]["summary"]
    state = stamp_state_config(state, config)
    # 只向LLM发送压缩经验，既能利用前几轮结果，也避免完整历史导致上下文失控。
    prompt_memory = build_prompt_memory(state.get("optimization_memory"))
    messages = build_messages(summary, parameters, registry, config, prompt_memory)
    response = mock_response(parameters) if use_mock else request_json(messages, config)
    validation = validate_proposal(response, parameters)
    state = record_proposal(state, validation)

    (output / "llm_request_messages.json").write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "llm_response.json").write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "candidate_validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    write_state(output / "agent_state.json", state)
    (output / "README.md").write_text(
        "# LLM参数优化Agent第一阶段输出\n\n"
        "本目录是候选生成与安全校验的干运行，不调用CarSim、不修改正式基线。\n\n"
        "- `llm_request_messages.json`：发送给LLM的结构化上下文；\n"
        "- `llm_response.json`：本轮LLM或模拟响应；\n"
        "- `candidate_validation.json`：物理边界和锁定参数校验；\n"
        "- `agent_state.json`：最优基线及待评价候选状态。\n\n"
        "运行命令：`python -m llm_optimizer.run_agent --dry-run`。\n",
        encoding="utf-8",
    )
    return {"output": str(output), "accepted": len(validation["accepted"]), "rejected": len(validation["rejected"]), "best_unchanged": True}


def main() -> None:
    """默认要求显式选择干运行或真实API，避免误调用付费接口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="使用内置模拟响应，不调用网络")
    parser.add_argument("--use-api", action="store_true", help="读取环境变量并调用OpenAI兼容接口")
    parser.add_argument("--formal-result", type=Path, default=DEFAULT_FORMAL_RESULT)
    parser.add_argument("--state", type=Path, help="读取上一轮agent_state.json并从当前最优参数续跑")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.dry_run == args.use_api:
        parser.error("必须且只能选择 --dry-run 或 --use-api")
    state_path = args.state.resolve() if args.state else None
    result = run(args.formal_result.resolve(), args.output.resolve(), args.dry_run, state_path)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
