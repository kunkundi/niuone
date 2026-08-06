"""Bounded connectivity checks for model settings managed by the Dashboard."""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from core.model_api import ModelResponseParseError, build_model_request, request_model


MODEL_TEST_PROMPT = "这是一次模型连通性测试。无需解释，请只回复：连接成功"


@dataclass(frozen=True)
class ModelTestTarget:
    id: str
    group_slug: str
    label: str
    description: str
    model_names: tuple[str, ...]
    base_url_names: tuple[str, ...]
    api_key_names: tuple[str, ...]
    api_mode_names: tuple[str, ...]
    override_names: tuple[str, ...]
    default_model: str = ""
    default_api_mode: str = "chat"
    tool_type: str = ""


MODEL_TEST_TARGETS: tuple[ModelTestTarget, ...] = (
    ModelTestTarget(
        id="news-precheck",
        group_slug="news-precheck",
        label="消息面预检模型",
        description="按当前接口模式验证模型和搜索工具接口。",
        model_names=("DASHBOARD_NEWS_MODEL",),
        base_url_names=("DASHBOARD_NEWS_BASE_URL",),
        api_key_names=("DASHBOARD_NEWS_API_KEY",),
        api_mode_names=("DASHBOARD_NEWS_API_MODE",),
        override_names=(
            "DASHBOARD_NEWS_MODEL",
            "DASHBOARD_NEWS_BASE_URL",
            "DASHBOARD_NEWS_API_KEY",
            "DASHBOARD_NEWS_API_MODE",
        ),
        default_api_mode="auto",
        tool_type="web_search",
    ),
    ModelTestTarget(
        id="decision-model",
        group_slug="decision-model",
        label="买卖决策模型",
        description="验证交易决策使用的 Chat Completions 接口。",
        model_names=("DASHBOARD_DECISION_MODEL",),
        base_url_names=("DASHBOARD_DECISION_BASE_URL", "CROSSDESK_BASE_URL"),
        api_key_names=("DASHBOARD_DECISION_API_KEY", "CROSSDESK_API_KEY"),
        api_mode_names=(),
        override_names=(
            "DASHBOARD_DECISION_MODEL",
            "DASHBOARD_DECISION_BASE_URL",
            "DASHBOARD_DECISION_API_KEY",
        ),
        default_model="deepseek-v4-pro",
    ),
    ModelTestTarget(
        id="grok-model",
        group_slug="us-market",
        label="Grok 模型",
        description="验证 X 监控与美股功能共用的 Grok 接口。",
        model_names=("DASHBOARD_GROK_MODEL",),
        base_url_names=("DASHBOARD_GROK_BASE_URL", "CROSSDESK_BASE_URL"),
        api_key_names=("DASHBOARD_GROK_API_KEY", "CROSSDESK_API_KEY"),
        api_mode_names=("DASHBOARD_GROK_API_MODE",),
        override_names=(
            "DASHBOARD_GROK_MODEL",
            "DASHBOARD_GROK_BASE_URL",
            "DASHBOARD_GROK_API_KEY",
            "DASHBOARD_GROK_API_MODE",
        ),
        default_model="grok-4.20-multi-agent-xhigh",
        default_api_mode="auto",
    ),
    ModelTestTarget(
        id="us-rating-model",
        group_slug="us-market",
        label="美股评级模型",
        description="优先验证美股评级专用模型、地址和密钥，留空时复用 Grok。",
        model_names=("US_RATING_MODEL", "DASHBOARD_GROK_MODEL"),
        base_url_names=(
            "US_RATING_BASE_URL",
            "DASHBOARD_GROK_BASE_URL",
            "CROSSDESK_BASE_URL",
        ),
        api_key_names=(
            "US_RATING_API_KEY",
            "DASHBOARD_GROK_API_KEY",
            "CROSSDESK_API_KEY",
        ),
        api_mode_names=("DASHBOARD_GROK_API_MODE",),
        override_names=(
            "US_RATING_MODEL",
            "US_RATING_BASE_URL",
            "US_RATING_API_KEY",
            "DASHBOARD_GROK_MODEL",
            "DASHBOARD_GROK_BASE_URL",
            "DASHBOARD_GROK_API_KEY",
            "DASHBOARD_GROK_API_MODE",
        ),
        default_model="grok-4.20-multi-agent-xhigh",
        default_api_mode="auto",
        tool_type="web_search",
    ),
    ModelTestTarget(
        id="a-share-summary-model",
        group_slug="market-monitoring",
        label="A 股盘面总结模型",
        description="优先验证 A 股总结专用配置，留空时复用 Grok。",
        model_names=(
            "A_SHARE_MODEL_SUMMARY_MODEL",
            "A_SHARE_GROK_SUMMARY_MODEL",
            "DASHBOARD_GROK_MODEL",
        ),
        base_url_names=(
            "A_SHARE_MODEL_SUMMARY_BASE_URL",
            "A_SHARE_GROK_SUMMARY_BASE_URL",
            "DASHBOARD_GROK_BASE_URL",
            "CROSSDESK_BASE_URL",
        ),
        api_key_names=(
            "A_SHARE_MODEL_SUMMARY_API_KEY",
            "A_SHARE_GROK_SUMMARY_API_KEY",
            "DASHBOARD_GROK_API_KEY",
            "CROSSDESK_API_KEY",
        ),
        api_mode_names=(),
        override_names=(
            "A_SHARE_MODEL_SUMMARY_MODEL",
            "A_SHARE_MODEL_SUMMARY_BASE_URL",
            "A_SHARE_MODEL_SUMMARY_API_KEY",
        ),
        default_model="grok-4.20-multi-agent-xhigh",
    ),
)

MODEL_TEST_TARGET_BY_ID = {target.id: target for target in MODEL_TEST_TARGETS}


@dataclass(frozen=True)
class ResolvedModelTestConfig:
    target: ModelTestTarget
    model: str
    base_url: str
    api_key: str
    api_mode: str


def model_test_metadata() -> list[dict[str, Any]]:
    """Return safe UI metadata without resolved credentials."""

    return [
        {
            "id": target.id,
            "group_slug": target.group_slug,
            "label": target.label,
            "description": target.description,
            "field_names": list(target.override_names),
        }
        for target in MODEL_TEST_TARGETS
    ]


def model_test_override_names(target_id: str) -> set[str]:
    target = MODEL_TEST_TARGET_BY_ID.get(str(target_id or "").strip())
    return set(target.override_names) if target else set()


def model_test_setting_names() -> set[str]:
    names: set[str] = set()
    for target in MODEL_TEST_TARGETS:
        names.update(target.model_names)
        names.update(target.base_url_names)
        names.update(target.api_key_names)
        names.update(target.api_mode_names)
    return names


def _first_value(values: Mapping[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = str(values.get(name) or "").strip()
        if value:
            return value
    return ""


def resolve_model_test_config(
    target_id: str,
    values: Mapping[str, Any],
    *,
    provider_fallback: Mapping[str, Any] | None = None,
) -> ResolvedModelTestConfig:
    target = MODEL_TEST_TARGET_BY_ID.get(str(target_id or "").strip())
    if target is None:
        raise ValueError("不支持的模型测试目标")

    fallback = provider_fallback or {}
    model = _first_value(values, target.model_names) or target.default_model
    base_url = _first_value(values, target.base_url_names)
    api_key = _first_value(values, target.api_key_names)
    fallback_base_url = str(fallback.get("base_url") or "").strip()
    fallback_api_key = str(fallback.get("api_key") or "").strip()
    # Runtime loaders select a complete YAML provider only when the environment
    # chain does not already contain both values. Do not combine a user-entered
    # address with an unrelated provider secret.
    if not (base_url and api_key) and fallback_base_url and fallback_api_key:
        base_url = fallback_base_url
        api_key = fallback_api_key
    api_mode = _first_value(values, target.api_mode_names) or target.default_api_mode
    return ResolvedModelTestConfig(
        target=target,
        model=model,
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        api_mode=api_mode,
    )


def _validate_config(config: ResolvedModelTestConfig) -> str:
    missing = []
    if not config.model:
        missing.append("模型")
    if not config.base_url:
        missing.append("API 地址")
    if not config.api_key:
        missing.append("API Key")
    if missing:
        return "请先配置" + "、".join(missing)
    parsed = urlsplit(config.base_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return "API 地址必须是有效的 http 或 https 地址"
    return ""


def _http_error_message(code: int) -> str:
    if code == 400:
        return "请求格式、接口模式或模型名称不受支持（HTTP 400）"
    if code == 401:
        return "API Key 验证失败（HTTP 401）"
    if code == 403:
        return "模型服务拒绝访问，请检查 API Key 权限（HTTP 403）"
    if code == 404:
        return "模型接口或模型名称不存在（HTTP 404）"
    if code == 408:
        return "模型服务请求超时（HTTP 408）"
    if code == 429:
        return "模型服务触发限流或额度不足（HTTP 429）"
    if 500 <= code <= 599:
        return f"模型服务暂时不可用（HTTP {code}）"
    return f"模型服务返回错误（HTTP {code}）"


def test_model_connection(
    target_id: str,
    values: Mapping[str, Any],
    *,
    provider_fallback: Mapping[str, Any] | None = None,
    timeout: float = 20,
    opener=urllib.request.urlopen,
    monotonic=time.monotonic,
) -> dict[str, Any]:
    """Send one small model request and return only non-sensitive diagnostics."""

    try:
        config = resolve_model_test_config(
            target_id,
            values,
            provider_fallback=provider_fallback,
        )
    except ValueError as exc:
        return {"ok": False, "target": str(target_id or ""), "error": str(exc)}

    result: dict[str, Any] = {
        "ok": False,
        "target": config.target.id,
        "label": config.target.label,
        "model": config.model,
    }
    validation_error = _validate_config(config)
    if validation_error:
        result.update({"error": validation_error, "error_code": "invalid_config"})
        return result

    tools = [{"type": config.target.tool_type}] if config.target.tool_type else None
    model_request = build_model_request(
        config.base_url,
        config.model,
        [{"role": "user", "content": MODEL_TEST_PROMPT}],
        max_tokens=256,
        api_mode=config.api_mode,
        tools=tools,
        reasoning={"effort": "low"},
        stream=False,
        extra_payload={"stream": False},
    )
    result["api_mode"] = model_request.api_mode
    started = monotonic()
    try:
        parsed = request_model(
            model_request,
            config.api_key,
            timeout=max(5.0, min(30.0, float(timeout))),
            opener=opener,
        )
        if not str(parsed.content or "").strip():
            result.update(
                {
                    "error": "模型已响应，但未返回可用文本",
                    "error_code": "empty_response",
                }
            )
            return result
    except urllib.error.HTTPError as exc:
        result.update(
            {
                "error": _http_error_message(int(exc.code)),
                "error_code": f"http_{int(exc.code)}",
            }
        )
        return result
    except (TimeoutError, socket.timeout):
        result.update({"error": "模型连接超时", "error_code": "timeout"})
        return result
    except urllib.error.URLError:
        result.update({"error": "无法连接模型服务，请检查地址和网络", "error_code": "connection_failed"})
        return result
    except ModelResponseParseError:
        result.update({"error": "模型返回格式无法识别", "error_code": "invalid_response"})
        return result
    except (OSError, ValueError):
        result.update({"error": "模型连接失败，请检查接口配置", "error_code": "request_failed"})
        return result
    except Exception:
        result.update({"error": "模型测试失败", "error_code": "unexpected_error"})
        return result

    elapsed_ms = max(0, int(round((monotonic() - started) * 1000)))
    mode_label = "Responses API" if model_request.api_mode == "responses" else "Chat Completions"
    result.update(
        {
            "ok": True,
            "elapsed_ms": elapsed_ms,
            "message": f"{config.target.label}已接通（{mode_label}，{elapsed_ms} ms）",
        }
    )
    return result
