"""Resolve the one model configuration shared by Dashboard model consumers.

``DASHBOARD_DECISION_*`` remains the persisted key family so existing trading
deployments keep their credentials.  The older ``A_SHARE_MODEL_SUMMARY_*``
family is accepted only as an upgrade fallback when the shared endpoint is not
configured yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

try:
    import yaml
except ImportError:  # pragma: no cover - present in supported installs
    yaml = None


SHARED_MODEL_NAMES = {
    "model": "DASHBOARD_DECISION_MODEL",
    "base_url": "DASHBOARD_DECISION_BASE_URL",
    "api_key": "DASHBOARD_DECISION_API_KEY",
    "stream_mode": "DASHBOARD_DECISION_STREAM_MODE",
    "reasoning_effort": "DASHBOARD_DECISION_REASONING_EFFORT",
    "context_length": "DASHBOARD_DECISION_CONTEXT_LENGTH",
    "max_tokens": "DASHBOARD_DECISION_MAX_TOKENS",
}

LEGACY_SUMMARY_MODEL_NAMES = {
    "model": "A_SHARE_MODEL_SUMMARY_MODEL",
    "base_url": "A_SHARE_MODEL_SUMMARY_BASE_URL",
    "api_key": "A_SHARE_MODEL_SUMMARY_API_KEY",
    "stream_mode": "A_SHARE_MODEL_SUMMARY_STREAM_MODE",
    "reasoning_effort": "A_SHARE_MODEL_SUMMARY_REASONING_EFFORT",
    "context_length": "A_SHARE_MODEL_SUMMARY_CONTEXT_LENGTH",
    "max_tokens": "A_SHARE_MODEL_SUMMARY_MAX_TOKENS",
}

SHARED_MODEL_ENV_NAMES = frozenset(SHARED_MODEL_NAMES.values())
LEGACY_SUMMARY_MODEL_ENV_NAMES = frozenset(LEGACY_SUMMARY_MODEL_NAMES.values())


@dataclass(frozen=True)
class SharedModelConfig:
    model: str
    base_url: str
    api_key: str
    stream_mode: str
    reasoning_effort: str
    context_length: str
    max_tokens: str
    source: str


def _value(values: Mapping[str, Any], name: str) -> str:
    return str(values.get(name) or "").strip()


def model_provider_display_name(base_url: str) -> str:
    """Identify official OpenAI without exposing endpoint paths or credentials."""
    try:
        parsed = urlsplit(str(base_url or "").strip())
        if parsed.scheme == "https" and parsed.hostname == "api.openai.com":
            return "OpenAI"
    except ValueError:
        pass
    return "Crossdesk.ccwu.cc"  # Preserve the legacy gateway display contract.


def resolve_shared_model_config(
    values: Mapping[str, Any],
    *,
    provider_fallback: Mapping[str, Any] | None = None,
    default_model: str = "deepseek-v4-pro",
    default_context_length: str = "128000",
    default_max_tokens: str = "4096",
) -> SharedModelConfig:
    """Resolve a coherent shared endpoint with bounded legacy fallbacks."""

    shared = {key: _value(values, name) for key, name in SHARED_MODEL_NAMES.items()}
    legacy = {
        key: _value(values, name)
        for key, name in LEGACY_SUMMARY_MODEL_NAMES.items()
    }
    provider = {
        key: str((provider_fallback or {}).get(key) or "").strip()
        for key in ("model", "base_url", "api_key")
    }

    if shared["base_url"] and shared["api_key"]:
        selected = shared
        source = "shared"
    elif legacy["base_url"] and legacy["api_key"]:
        selected = legacy
        source = "legacy_summary"
    elif provider["base_url"] and provider["api_key"]:
        selected = {**shared, **provider}
        source = "provider"
    else:
        # Preserve the most useful diagnostics for partially configured installs
        # without combining an endpoint with a key from a different source.
        selected = shared if shared["base_url"] or shared["api_key"] else legacy
        source = "shared" if selected is shared else "legacy_summary"

    def optional(name: str, default: str = "") -> str:
        if source == "legacy_summary":
            return legacy[name] or default
        return shared[name] or (legacy[name] if source == "provider" else "") or default

    model = (
        legacy["model"]
        if source == "legacy_summary"
        else shared["model"] or legacy["model"]
    ) or default_model
    return SharedModelConfig(
        model=model,
        base_url=str(selected.get("base_url") or "").rstrip("/"),
        api_key=str(selected.get("api_key") or ""),
        stream_mode=optional("stream_mode", "auto"),
        reasoning_effort=optional("reasoning_effort"),
        context_length=optional("context_length", default_context_length),
        max_tokens=optional("max_tokens", default_max_tokens),
        source=source,
    )


def legacy_summary_migration_values(values: Mapping[str, Any]) -> dict[str, str]:
    """Return legacy values that can safely seed missing shared settings."""

    migrated: dict[str, str] = {}
    for field, shared_name in SHARED_MODEL_NAMES.items():
        if _value(values, shared_name):
            continue
        legacy_value = _value(values, LEGACY_SUMMARY_MODEL_NAMES[field])
        if legacy_value:
            migrated[shared_name] = legacy_value
    return migrated


def load_crossdesk_provider(config_path: str | Path | None) -> dict[str, str]:
    """Load the historical complete provider fallback without exposing it."""

    if not config_path or yaml is None:
        return {}
    path = Path(config_path).expanduser()
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, TypeError):
        return {}
    providers = loaded.get("custom_providers", []) if isinstance(loaded, Mapping) else []
    for raw_provider in providers if isinstance(providers, list) else []:
        if not isinstance(raw_provider, Mapping):
            continue
        identity = " ".join(
            (
                str(raw_provider.get("name") or ""),
                str(raw_provider.get("base_url") or ""),
            )
        ).lower()
        if "crossdesk" not in identity:
            continue
        provider = {
            "model": str(raw_provider.get("model") or "").strip(),
            "base_url": str(raw_provider.get("base_url") or "").strip(),
            "api_key": str(raw_provider.get("api_key") or "").strip(),
        }
        return provider if provider["base_url"] and provider["api_key"] else {}
    return {}
