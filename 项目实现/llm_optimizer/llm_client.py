"""最小化OpenAI兼容LLM客户端，密钥仅从环境变量读取。"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def load_connection(config: dict[str, Any]) -> dict[str, str]:
    """读取环境变量但不把密钥写入日志或输出文件。"""
    names = config.get("llm_environment", {
        "api_key": "CARSIM_LLM_API_KEY",
        "base_url": "CARSIM_LLM_BASE_URL",
        "model": "CARSIM_LLM_MODEL",
    })
    values = {key: os.environ.get(env_name, "").strip() for key, env_name in names.items()}
    missing = [names[key] for key, value in values.items() if not value]
    if missing:
        raise RuntimeError("缺少LLM环境变量：" + ", ".join(missing))
    return values


def build_endpoint(base_url: str) -> str:
    """把用户填写的兼容接口地址整理为chat/completions请求地址。"""
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return normalized + "/chat/completions"


def format_http_error(error: urllib.error.HTTPError) -> str:
    """提取服务端错误，并识别网页入口误填为API地址的情况。"""
    detail = error.read().decode("utf-8", errors="replace").strip()[:1000]
    if "<html" in detail.lower() or "<!doctype" in detail.lower():
        return (
            f"LLM接口HTTP {error.code}：服务端返回了网页HTML而不是API JSON。"
            "请检查base_url是否为API地址（DeepSeek应填写https://api.deepseek.com），"
            "不要填写platform.deepseek.com网页地址。"
        )
    return f"LLM接口HTTP {error.code}：{detail or '服务端未返回详细错误'}"


def resolve_timeout_s(config: dict[str, Any], timeout_s: float | None = None) -> float:
    """解析API超时，优先使用显式值，其次使用界面传入的本地配置环境变量。"""
    raw_value: Any = timeout_s
    if raw_value is None:
        raw_value = os.environ.get("CARSIM_LLM_TIMEOUT_S")
    if raw_value in (None, ""):
        raw_value = config.get("llm_timeout_s", 120.0)
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"LLM接口timeout_s必须是正数，当前值：{raw_value}") from error
    if value <= 0:
        raise RuntimeError(f"LLM接口timeout_s必须是正数，当前值：{value}")
    return value


def content_to_text(content: Any) -> str:
    """兼容字符串和OpenAI分段content，并合并其中的文本片段。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text", item.get("content"))
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def extract_assistant_text(payload: dict[str, Any]) -> tuple[str, str | None]:
    """读取兼容接口正文；content为空时允许从reasoning_content中恢复JSON。"""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("响应缺少choices[0]")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("响应缺少message对象")
    content = message.get("content")
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False), choice.get("finish_reason")
    text = content_to_text(content).strip()
    if not text:
        # 部分推理模型会偶发把最终JSON放入reasoning_content；这里只恢复结构化对象，不记录推理全文。
        text = content_to_text(message.get("reasoning_content")).strip()
    return text, choice.get("finish_reason")


def parse_json_object(text: str) -> dict[str, Any]:
    """解析纯JSON、Markdown代码块或前后带说明文字的JSON对象。"""
    cleaned = text.strip().lstrip("\ufeff")
    if not cleaned:
        raise ValueError("模型正文为空")
    candidates = [cleaned]
    if "```" in cleaned:
        for block in cleaned.split("```")[1::2]:
            block = block.strip()
            if block.lower().startswith("json"):
                block = block[4:].lstrip()
            if block:
                candidates.append(block)

    decoder = json.JSONDecoder()
    parsed_objects: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                parsed_objects.append(value)
        except json.JSONDecodeError:
            # 兼容“下面是JSON：{...}”形式，从每个左花括号尝试解码完整对象。
            for index, character in enumerate(candidate):
                if character != "{":
                    continue
                try:
                    value, _ = decoder.raw_decode(candidate[index:])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    parsed_objects.append(value)

    # 只有同时包含诊断和候选才是完整协议，禁止把截断分析中的局部候选对象当作最终结果。
    for value in parsed_objects:
        if "candidates" in value and "diagnosis" in value:
            return value
    if parsed_objects:
        keys = sorted({key for value in parsed_objects for key in value})
        raise ValueError(f"找到JSON但缺少完整候选协议，已有字段：{keys}")
    raise ValueError("正文中没有完整JSON对象")


def normalize_proposal_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """兼容常见外层包装和字段别名，最终仍交给参数安全校验器严格检查。"""
    normalized = dict(payload)
    for wrapper in ("proposal", "result", "output"):
        nested = normalized.get(wrapper)
        if isinstance(nested, dict) and isinstance(nested.get("candidates"), list):
            normalized = dict(nested)
            break
    if not isinstance(normalized.get("diagnosis"), str):
        for alias in ("analysis", "diagnostic", "summary"):
            if isinstance(normalized.get(alias), str) and normalized[alias].strip():
                normalized["diagnosis"] = normalized[alias]
                break
    if not isinstance(normalized.get("candidates"), list):
        for alias in ("proposals", "parameter_candidates"):
            if isinstance(normalized.get(alias), list):
                normalized["candidates"] = normalized[alias]
                break
    normalized.setdefault("stop_reason", None)
    return normalized


def proposal_protocol_error(payload: dict[str, Any]) -> str | None:
    """返回候选协议缺陷；此检查用于决定是否重试，不替代物理边界校验。"""
    diagnosis = payload.get("diagnosis")
    candidates = payload.get("candidates")
    if not isinstance(diagnosis, str) or not diagnosis.strip():
        return "缺少非空diagnosis"
    if not isinstance(candidates, list) or not candidates:
        return "缺少非空candidates数组"
    return None


def write_response_diagnostics(path: Path | None, diagnostics: list[dict[str, Any]]) -> None:
    """保存不含API密钥和推理全文的响应诊断，供前端报错后人工检查。"""
    if path is None:
        return
    path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")


def response_request_body(
    model: str,
    messages: list[dict[str, str]],
    config: dict[str, Any],
    retrying_format: bool,
) -> bytes:
    """构造单次请求；格式重试时强化只输出JSON的约束。"""
    request_messages = list(messages)
    if retrying_format:
        request_messages.append({
            "role": "system",
            "content": (
                "上一次响应因输出分析过程而被截断。禁止继续分析，立即输出不超过1200 tokens的完整JSON；"
                "第一个字符必须是{，不要代码块或解释文字。"
            ),
        })
    payload: dict[str, Any] = {
        "model": model,
        "messages": request_messages,
        "temperature": 0.1 if retrying_format else 0.2,
        "max_tokens": int(config.get("llm_max_output_tokens", 8192)),
        "response_format": {"type": "json_object"},
    }
    # DeepSeek V4支持显式关闭思考模式；其他兼容服务未配置此项时不会收到额外字段。
    thinking_mode = str(config.get("llm_thinking_mode", "")).strip().lower()
    if thinking_mode in {"enabled", "disabled"}:
        payload["thinking"] = {"type": thinking_mode}
    return json.dumps(payload).encode("utf-8")


def request_json(
    messages: list[dict[str, str]],
    config: dict[str, Any],
    timeout_s: float | None = None,
    diagnostic_path: Path | None = None,
) -> dict[str, Any]:
    """调用OpenAI兼容接口；HTTP或正文格式异常时按统一配置自动重试。"""
    connection = load_connection(config)
    timeout = resolve_timeout_s(config, timeout_s)
    url = build_endpoint(connection["base_url"])
    maximum_attempts = max(1, min(5, int(config.get("llm_response_max_attempts", 3))))
    diagnostics: list[dict[str, Any]] = []
    last_format_error = "未知格式错误"
    retrying_format = False

    for attempt in range(maximum_attempts):
        body = response_request_body(connection["model"], messages, config, retrying_format)
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": "Bearer " + connection["api_key"],
                "Content-Type": "application/json",
                "User-Agent": "VectorTuneAgent/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code in {429, 500, 502, 503, 504} and attempt < maximum_attempts - 1:
                retry_after = error.headers.get("Retry-After", "")
                try:
                    delay_s = min(30.0, max(1.0, float(retry_after)))
                except ValueError:
                    delay_s = 2.0 ** attempt
                print(f"LLM接口临时错误HTTP {error.code}，{delay_s:g}秒后进行第{attempt + 2}次尝试", flush=True)
                time.sleep(delay_s)
                continue
            raise RuntimeError(format_http_error(error)) from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            if attempt < maximum_attempts - 1:
                print(f"LLM接口连接或外层JSON异常，准备第{attempt + 2}次尝试", flush=True)
                time.sleep(2.0 ** attempt)
                continue
            raise RuntimeError(f"LLM接口连接失败：{error}") from error

        try:
            content, finish_reason = extract_assistant_text(payload)
            result = normalize_proposal_fields(parse_json_object(content))
            protocol_error = proposal_protocol_error(result)
            if finish_reason == "length":
                raise ValueError("响应达到长度上限，不能采用其中的局部JSON")
            if protocol_error:
                raise ValueError(protocol_error)
            diagnostics.append({
                "attempt": attempt + 1,
                "parsed": True,
                "finish_reason": finish_reason,
                "content_length": len(content),
                "response_fields": sorted(result.keys()),
            })
            write_response_diagnostics(diagnostic_path, diagnostics)
            return result
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            last_format_error = str(error)
            finish_reason = None
            content = ""
            try:
                content, finish_reason = extract_assistant_text(payload)
            except ValueError:
                pass
            diagnostics.append({
                "attempt": attempt + 1,
                "parsed": False,
                "finish_reason": finish_reason,
                "content_length": len(content),
                "content_preview": content[:500],
                "error": last_format_error,
            })
            write_response_diagnostics(diagnostic_path, diagnostics)
            if attempt < maximum_attempts - 1:
                retrying_format = True
                print(
                    f"LLM第{attempt + 1}次响应不是完整JSON（{last_format_error}），"
                    f"正在进行第{attempt + 2}/{maximum_attempts}次格式重试",
                    flush=True,
                )
                time.sleep(1.0)
                continue

    raise RuntimeError(
        f"LLM响应连续{maximum_attempts}次无法解析：{last_format_error}；"
        "诊断已保存到本轮输出目录的llm_response_attempts.json"
    )
