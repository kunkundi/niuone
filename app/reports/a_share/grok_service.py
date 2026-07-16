#!/usr/bin/env python3
"""Grok-assisted A-share market monitor summaries."""
from __future__ import annotations

import json
import os
import re
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from core.model_api import build_model_request, request_model
from niuone_paths import get_dashboard_env_file, get_dashboard_home

if __package__ == "app":
    from .reports.a_share.grok import (
        build_messages as build_a_share_grok_messages,
        parse_content as parse_a_share_grok_content,
        remove_original_guidance as _remove_original_guidance,
        render_report as _render_grok_a_share_report,
        strip_json_fence as _strip_json_fence,
    )
else:
    from reports.a_share.grok import (
        build_messages as build_a_share_grok_messages,
        parse_content as parse_a_share_grok_content,
        remove_original_guidance as _remove_original_guidance,
        render_report as _render_grok_a_share_report,
        strip_json_fence as _strip_json_fence,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def load_dashboard_env() -> None:
    allowed = {
        "A_SHARE_MODEL_SUMMARY_ENABLED",
        "A_SHARE_MODEL_SUMMARY_MODEL",
        "A_SHARE_MODEL_SUMMARY_CONTEXT_LENGTH",
        "A_SHARE_MODEL_SUMMARY_MAX_TOKENS",
        "A_SHARE_MODEL_SUMMARY_BASE_URL",
        "A_SHARE_MODEL_SUMMARY_API_KEY",
        "A_SHARE_MODEL_SUMMARY_DEADLINE_SECONDS",
        "A_SHARE_MODEL_SUMMARY_REQUEST_TIMEOUT_SECONDS",
        "A_SHARE_GROK_SUMMARY_ENABLED",
        "A_SHARE_GROK_SUMMARY_MODEL",
        "A_SHARE_GROK_SUMMARY_MAX_TOKENS",
        "A_SHARE_GROK_SUMMARY_BASE_URL",
        "A_SHARE_GROK_SUMMARY_API_KEY",
        "A_SHARE_GROK_SUMMARY_DEADLINE_SECONDS",
        "A_SHARE_GROK_SUMMARY_REQUEST_TIMEOUT_SECONDS",
        "DASHBOARD_GROK_MODEL",
        "DASHBOARD_GROK_CONTEXT_LENGTH",
        "DASHBOARD_GROK_BASE_URL",
        "DASHBOARD_GROK_API_KEY",
        "CROSSDESK_BASE_URL",
        "CROSSDESK_API_KEY",
    }
    path = get_dashboard_env_file(PROJECT_ROOT)
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in allowed or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("\"'")


load_dashboard_env()


def _int_env(name: str, default: int, *, min_value: int) -> int:
    try:
        value = int(str(os.environ.get(name) or "").strip())
    except (TypeError, ValueError):
        value = default
    return max(min_value, value)


def _token_count_env(*names: str, default: int) -> int:
    for name in names:
        raw = str(os.environ.get(name) or "").strip()
        if not raw:
            continue
        compact = raw.replace(",", "").replace("_", "").strip()
        match = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", compact)
        if not match:
            continue
        number = float(match.group(1))
        unit = match.group(2).lower()
        multiplier = 1_000_000 if unit == "m" else 1_000 if unit == "k" else 1
        value = int(number * multiplier)
        if value > 0:
            return value
    return default


A_SHARE_MODEL_SUMMARY_MODEL = (
    os.environ.get("A_SHARE_MODEL_SUMMARY_MODEL")
    or os.environ.get("A_SHARE_GROK_SUMMARY_MODEL")
    or os.environ.get("DASHBOARD_GROK_MODEL")
    or "grok-4.20-multi-agent-xhigh"
)
A_SHARE_MODEL_SUMMARY_DEADLINE_SECONDS = _int_env(
    "A_SHARE_MODEL_SUMMARY_DEADLINE_SECONDS",
    _int_env("A_SHARE_GROK_SUMMARY_DEADLINE_SECONDS", 60, min_value=15),
    min_value=15,
)
A_SHARE_MODEL_SUMMARY_REQUEST_TIMEOUT_SECONDS = _int_env(
    "A_SHARE_MODEL_SUMMARY_REQUEST_TIMEOUT_SECONDS",
    _int_env("A_SHARE_GROK_SUMMARY_REQUEST_TIMEOUT_SECONDS", 45, min_value=10),
    min_value=10,
)
A_SHARE_MODEL_SUMMARY_CONTEXT_LENGTH = _token_count_env(
    "A_SHARE_MODEL_SUMMARY_CONTEXT_LENGTH",
    "DASHBOARD_GROK_CONTEXT_LENGTH",
    default=128000,
)
A_SHARE_MODEL_SUMMARY_MAX_TOKENS = _token_count_env(
    "A_SHARE_MODEL_SUMMARY_MAX_TOKENS",
    "A_SHARE_GROK_SUMMARY_MAX_TOKENS",
    default=4096,
)


def a_share_grok_enabled() -> bool:
    raw = os.environ.get("A_SHARE_MODEL_SUMMARY_ENABLED")
    if raw is None:
        raw = os.environ.get("A_SHARE_GROK_SUMMARY_ENABLED", "1")
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _load_config() -> dict[str, Any]:
    config_path = Path(os.environ.get("DASHBOARD_CONFIG") or str(DASHBOARD_HOME / "config.yaml")).expanduser()
    try:
        import yaml  # type: ignore

        if config_path.exists():
            return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return {}


def _get_grok_credentials() -> tuple[str, str]:
    env_base_url = (
        os.environ.get("A_SHARE_MODEL_SUMMARY_BASE_URL")
        or os.environ.get("A_SHARE_GROK_SUMMARY_BASE_URL")
        or os.environ.get("DASHBOARD_GROK_BASE_URL")
        or os.environ.get("CROSSDESK_BASE_URL")
    )
    env_api_key = (
        os.environ.get("A_SHARE_MODEL_SUMMARY_API_KEY")
        or os.environ.get("A_SHARE_GROK_SUMMARY_API_KEY")
        or os.environ.get("DASHBOARD_GROK_API_KEY")
        or os.environ.get("CROSSDESK_API_KEY")
    )
    if env_base_url and env_api_key:
        return env_base_url.rstrip("/"), env_api_key

    cfg = _load_config()
    for provider in cfg.get("custom_providers", []) or []:
        base_url = str(provider.get("base_url") or "")
        if "crossdesk.ccwu.cc" in base_url or "grok" in str(provider.get("name") or "").lower():
            return base_url.rstrip("/"), str(provider.get("api_key") or "")
    model_cfg = cfg.get("model", {}) if isinstance(cfg.get("model"), dict) else {}
    return str(model_cfg.get("base_url") or "").rstrip("/"), str(model_cfg.get("api_key") or "")


def _is_transient_error(err: Exception) -> bool:
    if isinstance(err, TimeoutError):
        return True
    if isinstance(err, HTTPError):
        return err.code in {408, 429, 500, 502, 503, 504}
    if isinstance(err, URLError):
        return True
    text = str(err).lower()
    return any(hit in text for hit in ("timed out", "timeout", "temporarily", "connection reset", "empty stream", "ssl"))


def call_grok_api(messages: list[dict[str, str]], *, max_tokens: int = A_SHARE_MODEL_SUMMARY_MAX_TOKENS) -> str:
    base_url, api_key = _get_grok_credentials()
    if not base_url or not api_key:
        raise RuntimeError("model summary credentials not found: set A_SHARE_MODEL_SUMMARY_BASE_URL/API_KEY or DASHBOARD_GROK_BASE_URL/API_KEY")
    model_request = build_model_request(
        base_url,
        A_SHARE_MODEL_SUMMARY_MODEL,
        messages,
        max_tokens=max_tokens,
        api_mode="chat",
        stream=False,
        extra_payload={"stream": False},
    )
    deadline = time.monotonic() + max(15, A_SHARE_MODEL_SUMMARY_DEADLINE_SECONDS)
    last_err: Exception | None = None
    for attempt in range(1, 3):
        remaining = deadline - time.monotonic()
        if remaining <= 5:
            break
        try:
            timeout_seconds = min(max(10, A_SHARE_MODEL_SUMMARY_REQUEST_TIMEOUT_SECONDS), max(10, remaining - 2))
            parsed = request_model(
                model_request,
                api_key,
                timeout=timeout_seconds,
                opener=urlopen,
                ssl_context=_SSL_CONTEXT,
            )
            if str(parsed.content or "").strip():
                return str(parsed.content).strip()
            last_err = RuntimeError("model returned empty content")
        except Exception as exc:
            last_err = exc
            if attempt < 2 and _is_transient_error(exc) and (deadline - time.monotonic()) > 8:
                time.sleep(min(2 * attempt, max(0, deadline - time.monotonic() - 5)))
                continue
            break
    if last_err:
        raise RuntimeError(f"model summary call failed: {last_err}")
    raise RuntimeError("model summary call did not complete before the local deadline")


def render_grok_a_share_report(local_report: str, parsed: dict[str, Any], *, title: str) -> str:
    return _render_grok_a_share_report(
        local_report,
        parsed,
        title=title,
        model=A_SHARE_MODEL_SUMMARY_MODEL,
    )


def apply_grok_to_a_share_report(local_report: str, *, title: str, strict: bool = False) -> str:
    if not local_report.strip() or not a_share_grok_enabled():
        return local_report
    try:
        content = call_grok_api(build_a_share_grok_messages(local_report, title=title))
        parsed = parse_a_share_grok_content(content)
        if not parsed.get("summary"):
            raise ValueError("A-share Grok summary missing summary")
        if len(parsed.get("guidance_lines") or []) < 2:
            raise ValueError("A-share Grok summary missing actionable guidance_lines")
        return render_grok_a_share_report(local_report, parsed, title=title)
    except Exception as exc:
        if strict:
            raise
        return (
            f"{local_report.rstrip()}\n\n"
            f"ℹ️ 模型盘面总结暂不可用，已使用本地规则兜底：{type(exc).__name__}: {exc}"
        )
