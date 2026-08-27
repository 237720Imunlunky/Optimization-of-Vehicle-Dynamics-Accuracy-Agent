"""LLM参数优化Agent基础安全机制测试。"""

import json

from . import llm_client
from .objective import should_accept_candidate
from .parameter_space import baseline_parameters, load_agent_config, load_registry, validate_candidate, validate_proposal
from .candidate_executor import current_best_summaries, excluded_reason, split_name
from .model_patcher import apply_parameter_set, audit_parameter_bindings
from .state_store import create_initial_state, record_evaluation, record_proposal
from .run_agent import mock_response
from .llm_client import build_endpoint, extract_assistant_text, normalize_proposal_fields, parse_json_object


def test_build_endpoint_accepts_base_or_full_path() -> None:
    """API地址可填写根地址，也可填写完整chat/completions地址。"""
    assert build_endpoint("https://api.example.com/v1") == "https://api.example.com/v1/chat/completions"
    assert build_endpoint("https://api.example.com/v1/chat/completions") == "https://api.example.com/v1/chat/completions"


def test_llm_json_parser_accepts_markdown_code_fence() -> None:
    """模型偶发增加Markdown代码块时，仍应提取完整候选JSON。"""
    content = '结果如下：\n```json\n{"diagnosis":"测试","candidates":[],"stop_reason":null}\n```'
    parsed = parse_json_object(content)
    assert parsed["diagnosis"] == "测试"
    assert parsed["candidates"] == []


def test_llm_json_parser_prefers_candidate_protocol_object() -> None:
    """说明文字含小JSON示例时，应优先读取真正的候选协议对象。"""
    content = '格式例子：{"parameter":"rr_c"}\n最终：{"diagnosis":"正式结果","candidates":[]}'
    parsed = parse_json_object(content)
    assert parsed["diagnosis"] == "正式结果"


def test_extract_assistant_text_accepts_segmented_content() -> None:
    """OpenAI兼容接口返回分段content时，应正确拼接文本。"""
    payload = {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": [{"type": "text", "text": '{"candidates":[]}' }]},
        }],
    }
    content, finish_reason = extract_assistant_text(payload)
    assert content == '{"candidates":[]}'
    assert finish_reason == "stop"


def test_extract_assistant_text_recovers_reasoning_json_when_content_empty() -> None:
    """部分推理模型把最终JSON放错字段时，允许从reasoning_content恢复。"""
    payload = {
        "choices": [{
            "finish_reason": "stop",
            "message": {
                "content": "",
                "reasoning_content": '{"diagnosis":"备用字段恢复","candidates":[{"candidate_id":"C1"}]}',
            },
        }],
    }
    content, _ = extract_assistant_text(payload)
    assert parse_json_object(content)["candidates"][0]["candidate_id"] == "C1"


def test_normalize_proposal_fields_accepts_common_aliases() -> None:
    """模型使用summary和proposals别名时，应规范化后再进入严格安全校验。"""
    normalized = normalize_proposal_fields({"summary": "候选诊断", "proposals": [{"candidate_id": "C1"}]})
    assert normalized["diagnosis"] == "候选诊断"
    assert normalized["candidates"][0]["candidate_id"] == "C1"


def test_request_json_retries_malformed_model_content(monkeypatch, tmp_path) -> None:
    """首次正文不是JSON时，应自动再次请求并留下两次诊断记录。"""
    responses = [
        "这不是JSON",
        '{"diagnosis":"重试成功","candidates":[{"candidate_id":"C1"}],"stop_reason":null}',
    ]
    call_count = 0

    class FakeResponse:
        """模拟urllib返回的OpenAI兼容响应。"""

        def __init__(self, content: str) -> None:
            self.content = content

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            payload = {"choices": [{"finish_reason": "stop", "message": {"content": self.content}}]}
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def fake_urlopen(request, timeout):
        """依次返回错误正文和正确JSON正文。"""
        nonlocal call_count
        assert timeout == 5.0
        request_payload = json.loads(request.data.decode("utf-8"))
        assert request_payload["thinking"] == {"type": "disabled"}
        response = FakeResponse(responses[call_count])
        call_count += 1
        return response

    monkeypatch.setenv("CARSIM_LLM_API_KEY", "test-key")
    monkeypatch.setenv("CARSIM_LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("CARSIM_LLM_MODEL", "test-model")
    monkeypatch.setattr(llm_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm_client.time, "sleep", lambda _seconds: None)
    diagnostic_path = tmp_path / "llm_response_attempts.json"
    result = llm_client.request_json(
        [{"role": "user", "content": "生成候选"}],
        {
            "llm_response_max_attempts": 3,
            "llm_max_output_tokens": 1024,
            "llm_thinking_mode": "disabled",
        },
        timeout_s=5.0,
        diagnostic_path=diagnostic_path,
    )
    diagnostics = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert result["diagnosis"] == "重试成功"
    assert call_count == 2
    assert [item["parsed"] for item in diagnostics] == [False, True]


def test_parameter_mapping_changes_are_normalized() -> None:
    """模型输出对象格式的changes也必须经过同样的物理边界校验。"""
    registry = load_registry()
    current = baseline_parameters(registry)
    candidate = {"changes": {"motor_low_speed_torque_scale": 1.03}}
    result = validate_candidate(candidate, current, registry, 3)
    assert result["valid"]
    assert result["normalized_changes"]["motor_low_speed_torque_scale"] == 1.03


def test_new_value_change_format_is_normalized() -> None:
    """兼容模型输出的old_value/new_value格式，但仍执行全部边界校验。"""
    registry = load_registry()
    current = baseline_parameters(registry)
    candidate = {
        "changes": [{
            "parameter": "rr_c",
            "old_value": 0.0065,
            "new_value": 0.0068,
        }],
    }
    result = validate_candidate(candidate, current, registry, 3)
    assert result["valid"]
    assert result["normalized_changes"]["rr_c"] == 0.0068


def test_new_value_cannot_bypass_iteration_limit() -> None:
    """new_value格式不能绕过单轮最大变化限制。"""
    registry = load_registry()
    current = baseline_parameters(registry)
    candidate = {"changes": [{"parameter": "rr_c", "new_value": 0.0075}]}
    result = validate_candidate(candidate, current, registry, 3)
    assert not result["valid"]
    assert "单轮变化" in result["errors"][0]


def test_numeric_candidate_id_is_normalized_to_string() -> None:
    """数字候选编号应在进入命令行前统一转换为字符串。"""
    current = baseline_parameters(load_registry())
    payload = {
        "diagnosis": "测试数字编号兼容性",
        "candidates": [{
            "candidate_id": 1,
            "changes": [{"parameter": "rr_c", "value": 0.0066}],
        }],
    }
    result = validate_proposal(payload, current)
    assert result["accepted"][0]["candidate_id"] == "1"
    assert result["accepted"][0]["source"]["candidate_id"] == "1"


def test_numeric_and_string_candidate_ids_are_duplicates() -> None:
    """数字1和字符串1属于同一编号，不得绕过重复编号检查。"""
    current = baseline_parameters(load_registry())
    payload = {
        "diagnosis": "测试重复编号",
        "candidates": [
            {"candidate_id": 1, "changes": [{"parameter": "rr_c", "value": 0.0066}]},
            {"candidate_id": "1", "changes": [{"parameter": "rr_c", "value": 0.0067}]},
        ],
    }
    result = validate_proposal(payload, current)
    assert len(result["accepted"]) == 1
    assert len(result["rejected"]) == 1


def test_coasting_uses_strict_role_specific_split() -> None:
    """滑行第3、6次必须排除，其余样本按新版标定/验证规则划分。"""
    config = load_agent_config()
    assert split_name(1, config, "coasting") == "calibration"
    assert split_name(4, config, "coasting") == "calibration"
    assert split_name(5, config, "coasting") == "validation"
    assert split_name(3, config, "coasting") == "excluded"
    assert split_name(6, config, "coasting") == "excluded"
    assert "制动" in excluded_reason("coasting", 3, config)


def test_valid_small_step_is_accepted() -> None:
    """物理边界内的小步候选应通过预检查。"""
    registry = load_registry()
    current = baseline_parameters(registry)
    candidate = {"changes": [{"parameter": "motor_low_speed_torque_scale", "value": 1.03}]}
    result = validate_candidate(candidate, current, registry, 3)
    assert result["valid"]


def test_locked_parameter_is_rejected() -> None:
    """LLM不得修改主减速比等锁定参数。"""
    registry = load_registry()
    current = baseline_parameters(registry)
    candidate = {"changes": [{"parameter": "front_final_drive", "value": 12.0}]}
    result = validate_candidate(candidate, current, registry, 3)
    assert not result["valid"]
    assert "锁定参数" in result["errors"][0]


def test_large_single_iteration_jump_is_rejected() -> None:
    """即使仍在总边界内，单轮跳变过大也必须拒绝。"""
    registry = load_registry()
    current = baseline_parameters(registry)
    candidate = {"changes": [{"parameter": "vehicle_mass_kg", "value": 2900.0}]}
    result = validate_candidate(candidate, current, registry, 3)
    assert not result["valid"]
    assert "单轮变化" in result["errors"][0]


def test_proposal_does_not_replace_best_before_simulation() -> None:
    """候选只有经过CarSim评价后才有资格替换最优参数。"""
    registry = load_registry()
    current = baseline_parameters(registry)
    payload = {
        "diagnosis": "测试诊断",
        "candidates": [{"candidate_id": "c1", "changes": [{"parameter": "rr_c", "value": 0.0066}]}],
    }
    validation = validate_proposal(payload, current)
    state = create_initial_state(current, {"longitudinal_score_pct": 92.34})
    before = state["best"].copy()
    updated = record_proposal(state, validation)
    assert updated["best"] == before
    assert updated["history"][-1]["status"] == "awaiting_carsim_evaluation"


def test_hard_guard_forces_rollback() -> None:
    """综合分提高但滑行跌破90%时仍不得接受。"""
    config = load_agent_config()
    current = {"longitudinal_score_pct": 92.34, "failed_metric_count": 20, "group_scores_pct": {"acceleration": 92.6, "coasting": 91.9}}
    candidate = {"longitudinal_score_pct": 93.0, "failed_metric_count": 18, "group_scores_pct": {"acceleration": 95.0, "coasting": 89.0}}
    result = should_accept_candidate(current, candidate, config)
    assert not result["accepted"]
    assert not result["hard_guards_passed"]


def test_subgroup_below_absolute_guard_must_not_degrade() -> None:
    """验证集基线已低于90%时，持平可继续评价，进一步降低则拒绝。"""
    config = load_agent_config()
    current = {"longitudinal_score_pct": 90.5, "failed_metric_count": 16, "group_scores_pct": {"acceleration": 91.2, "coasting": 89.7}}
    unchanged = {"longitudinal_score_pct": 91.1, "failed_metric_count": 14, "group_scores_pct": {"acceleration": 92.2, "coasting": 89.7}}
    degraded = {"longitudinal_score_pct": 91.0, "failed_metric_count": 14, "group_scores_pct": {"acceleration": 92.2, "coasting": 89.6}}
    assert should_accept_candidate(current, unchanged, config)["accepted"]
    assert not should_accept_candidate(current, degraded, config)["accepted"]


def test_model_patcher_updates_coupled_scalars() -> None:
    """模型补丁器必须同步写入四轮滚阻和三个功率字段。"""
    motor_table = "MMOTOR_MAX_TABLE LINEAR\n0, 250\n6000, 250\n8000, 200\nENDTABLE"
    carpet = "0, 10, 20\n 0.5, 1, 2"
    text = "\n".join([
        "RR_C 0.0038", "RR_C 0.0038", "RR_C 0.0038", "RR_C 0.0038",
        "M_SU 2808", "AREA_AERO 2.2", "PWR_HEV_DRV_MAX 380", "PWR_EV_MODE 380",
        "PWR_DRV_THROTTLE_COEFFICIENT 380",
        f"R_CHRG_BATTERY_CARPET 2D_SPLINE\n{carpet}\nENDTABLE",
        f"R_DIS_BATTERY_CARPET 2D_SPLINE\n{carpet}\nENDTABLE", motor_table, motor_table,
    ])
    parameters = baseline_parameters(load_registry())
    parameters.update({"motor_low_speed_torque_scale": 1.03, "rr_c": 0.0066, "drive_power_limit_kw": 390.0})
    patched = apply_parameter_set(text, parameters)
    audit = audit_parameter_bindings(patched, parameters)
    assert audit["passed"]
    # 两张电机表各有0和6000 rpm两个低速点，因此共应命中4行。
    assert patched.count("0, 257.5") == 4


def test_rejected_evaluation_keeps_best() -> None:
    """候选破坏保护线时，状态机必须回退到原基线。"""
    parameters = baseline_parameters(load_registry())
    state = create_initial_state(parameters, {"longitudinal_score_pct": 92.34})
    original = state["best"].copy()
    decision = {"accepted": False, "reason": "滑行精度跌破保护线"}
    updated = record_evaluation(state, "bad_candidate", parameters, {"longitudinal_score_pct": 93.0}, decision)
    assert updated["best"] == original
    assert updated["history"][-1]["status"] == "rejected_and_rolled_back"


def test_mock_next_step_uses_current_best() -> None:
    """连续迭代的模拟候选必须从当前最优点继续，而不是重置为1.00。"""
    response = mock_response({"motor_low_speed_torque_scale": 1.03})
    value = response["candidates"][0]["changes"][0]["value"]
    assert value == 1.05


def test_next_iteration_reads_last_accepted_candidate_summary() -> None:
    """连续迭代必须与最近接受候选比较，不能退回最初正式基线。"""
    candidate_summary = {
        "longitudinal_score_pct": 92.97,
        "group_scores_pct": {"acceleration": 93.71, "coasting": 91.94},
        "failed_metric_count": 32,
    }
    state = {
        "best": {"source": "candidate_accepted", "parameters": {}, "summary": candidate_summary},
        "history": [{
            "status": "accepted",
            "candidate_id": "candidate_accepted",
            "decision": {"summaries": {
                "calibration": {"candidate": {**candidate_summary, "longitudinal_score_pct": 93.86}},
                "validation": {"candidate": {**candidate_summary, "longitudinal_score_pct": 91.20}},
                "all_data": {"candidate": candidate_summary},
            }},
        }],
    }
    summaries = current_best_summaries(state, {}, {}, {})
    assert summaries["all_data"]["longitudinal_score_pct"] == 92.97
    assert summaries["calibration"]["longitudinal_score_pct"] == 93.86
