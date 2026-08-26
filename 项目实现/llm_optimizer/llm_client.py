"""最小化OpenAI兼容LLM客户端，密钥仅从环境变量读取。"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
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


def request_json(messages: list[dict[str, str]], config: dict[str, Any], timeout_s: float | None = None) -> dict[str, Any]:
    """调用OpenAI兼容chat/completions接口并解析JSON响应。"""
    connection = load_connection(config)
    timeout = resolve_timeout_s(config, timeout_s)
    url = build_endpoint(connection["base_url"])
    body = json.dumps({
        "model": connection["model"],
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
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
    # 429和临时5xx错误最多重试两次，避免限流时立即失败，也避免长时间重复扣费。
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as error:
            if error.code in {429, 500, 502, 503, 504} and attempt < 2:
                retry_after = error.headers.get("Retry-After", "")
                try:
                    delay_s = min(30.0, max(1.0, float(retry_after)))
                except ValueError:
                    delay_s = 2.0 ** attempt
                time.sleep(delay_s)
                continue
            raise RuntimeError(format_http_error(error)) from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt < 2:
                time.sleep(2.0 ** attempt)
                continue
            raise RuntimeError(f"LLM接口连接失败：{error}") from error
    else:
        raise RuntimeError("LLM接口请求失败：重试次数已用尽")
    try:
        content = payload["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("LLM响应格式不符合预期：未找到可解析的JSON候选内容") from error
