"""Backward-compatible facade for the split :mod:`messaging` package.

New notification code lives in ``app/messaging`` by responsibility. Existing
imports continue to use this module so application code, tests, and runtime
monkeypatches keep their established behavior.
"""
from __future__ import annotations

import importlib
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping


# Load the repository-qualified package even when this legacy facade is
# imported with ``app`` itself on sys.path. This avoids binding to an unrelated
# third-party package named ``messaging`` and gives both import styles one
# registry owner.
_project_root = str(Path(__file__).resolve().parent.parent)
_added_project_root = _project_root not in sys.path
if _added_project_root:
    sys.path.insert(0, _project_root)
try:
    _messaging = importlib.import_module("app.messaging")
finally:
    if _added_project_root:
        try:
            sys.path.remove(_project_root)
        except ValueError:
            pass


# Constants, models, protocols, and adapters
GLOBAL_ENABLED_ENV = _messaging.GLOBAL_ENABLED_ENV
TIMEOUT_ENV = _messaging.TIMEOUT_ENV
FEISHU_ENABLED_ENV = _messaging.FEISHU_ENABLED_ENV
FEISHU_WEBHOOK_ENV = _messaging.FEISHU_WEBHOOK_ENV
FEISHU_SECRET_ENV = _messaging.FEISHU_SECRET_ENV
DINGTALK_ENABLED_ENV = _messaging.DINGTALK_ENABLED_ENV
DINGTALK_WEBHOOK_ENV = _messaging.DINGTALK_WEBHOOK_ENV
DINGTALK_SECRET_ENV = _messaging.DINGTALK_SECRET_ENV
WECOM_ENABLED_ENV = _messaging.WECOM_ENABLED_ENV
WECOM_WEBHOOK_ENV = _messaging.WECOM_WEBHOOK_ENV
TELEGRAM_ENABLED_ENV = _messaging.TELEGRAM_ENABLED_ENV
TELEGRAM_TOKEN_ENV = _messaging.TELEGRAM_TOKEN_ENV
TELEGRAM_CHAT_ID_ENV = _messaging.TELEGRAM_CHAT_ID_ENV
SERVERCHAN_ENABLED_ENV = _messaging.SERVERCHAN_ENABLED_ENV
SERVERCHAN_SENDKEY_ENV = _messaging.SERVERCHAN_SENDKEY_ENV
DEFAULT_TIMEOUT_SECONDS = _messaging.DEFAULT_TIMEOUT_SECONDS
MIN_TIMEOUT_SECONDS = _messaging.MIN_TIMEOUT_SECONDS
MAX_TIMEOUT_SECONDS = _messaging.MAX_TIMEOUT_SECONDS
MAX_RESPONSE_BYTES = _messaging.MAX_RESPONSE_BYTES
MAX_MESSAGE_BYTES = _messaging.MAX_MESSAGE_BYTES
TRUTHY_VALUES = _messaging.TRUTHY_VALUES

NotificationConfigError = _messaging.NotificationConfigError
NotificationDeliveryError = _messaging.NotificationDeliveryError
Notification = _messaging.Notification
DeliveryResult = _messaging.DeliveryResult
JsonTransport = _messaging.JsonTransport
Clock = _messaging.Clock
NotificationChannel = _messaging.NotificationChannel
ChannelFactory = _messaging.ChannelFactory
FeishuChannel = _messaging.FeishuChannel
DingTalkChannel = _messaging.DingTalkChannel
WeComChannel = _messaging.WeComChannel
TelegramChannel = _messaging.TelegramChannel
ServerChanChannel = _messaging.ServerChanChannel


# Registry functions share the package's single state owner. Dispatch stays a
# thin facade wrapper so legacy runtime hooks (including the dashboard's test
# action and transport patches) continue to resolve in this module.
register_channel = _messaging.register_channel
registered_channels = _messaging.registered_channels


def dispatch(
    notification: Notification,
    env: Mapping[str, Any] | None = None,
    *,
    transport: JsonTransport | None = None,
    clock: Clock | None = None,
) -> list[DeliveryResult]:
    """Dispatch through the facade's current transport compatibility hook."""

    return _messaging.dispatch(
        notification,
        env,
        transport=transport or _post_json,
        clock=clock,
    )


def dispatch_to_channel(
    notification: Notification,
    channel_name: str,
    env: Mapping[str, Any] | None = None,
    *,
    transport: JsonTransport | None = None,
    clock: Clock | None = None,
) -> DeliveryResult:
    """Dispatch one channel through the facade's current transport hook."""

    return _messaging.dispatch_to_channel(
        notification,
        channel_name,
        env,
        transport=transport or _post_json,
        clock=clock,
    )


def notify_trade_executions(
    trades: Iterable[Mapping[str, Any]],
    env: Mapping[str, Any] | None = None,
    *,
    transport: JsonTransport | None = None,
    clock: Clock | None = None,
) -> list[DeliveryResult]:
    """Format and send a trade batch through the shared messaging package."""

    # Pass the facade's current dispatch attribute to preserve the old module's
    # runtime patchability while keeping the implementation in trades.py.
    return _messaging._trades_module.notify_trade_executions(
        trades,
        env,
        transport=transport,
        clock=clock,
        _dispatch=dispatch,
    )


# Compatibility aliases for callers and tests that used implementation details
# from the previous single-file module. ``urllib`` intentionally remains a
# module global; patching ``notifications.urllib.request`` affects the transport
# module because Python shares the same urllib package object.
_truncate_utf8 = _messaging._truncate_utf8
_reject_controls = _messaging._reject_controls
_required_credential = _messaging._required_credential
_optional_credential = _messaging._optional_credential
_split_https_url = _messaging._split_https_url
_require_host = _messaging._require_host
_single_query_value = _messaging._single_query_value
_validate_feishu_webhook = _messaging._validate_feishu_webhook
_validate_dingtalk_webhook = _messaging._validate_dingtalk_webhook
_validate_wecom_webhook = _messaging._validate_wecom_webhook
_safe_provider_code = _messaging._safe_provider_code
_zero_code = _messaging._zero_code
_require_mapping_response = _messaging._require_mapping_response
_feishu_factory = _messaging._feishu_factory
_dingtalk_factory = _messaging._dingtalk_factory
_wecom_factory = _messaging._wecom_factory
_telegram_factory = _messaging._telegram_factory
_serverchan_factory = _messaging._serverchan_factory
_serverchan_endpoint = _messaging._serverchan_endpoint
_NoRedirectHandler = _messaging._NoRedirectHandler
_post_json = _messaging._post_json
_sensitive_values = _messaging._sensitive_values
_redact_error_text = _messaging._redact_error_text
_sanitized_error = _messaging._sanitized_error
_ChannelRegistration = _messaging._ChannelRegistration
_CHANNEL_REGISTRY = _messaging._CHANNEL_REGISTRY
_REGISTRY_LOCK = _messaging._REGISTRY_LOCK
_env_text = _messaging._env_text
_enabled = _messaging._enabled
_timeout_seconds = _messaging._timeout_seconds
_deliver_once = _messaging._deliver_once
_clean_trade_text = _messaging._clean_trade_text
_finite_float = _messaging._finite_float
_money = _messaging._money
_price = _messaging._price
_percentage = _messaging._percentage
_trade_notification = _messaging._trade_notification


__all__ = [
    "GLOBAL_ENABLED_ENV",
    "TIMEOUT_ENV",
    "FEISHU_ENABLED_ENV",
    "FEISHU_WEBHOOK_ENV",
    "FEISHU_SECRET_ENV",
    "DINGTALK_ENABLED_ENV",
    "DINGTALK_WEBHOOK_ENV",
    "DINGTALK_SECRET_ENV",
    "WECOM_ENABLED_ENV",
    "WECOM_WEBHOOK_ENV",
    "TELEGRAM_ENABLED_ENV",
    "TELEGRAM_TOKEN_ENV",
    "TELEGRAM_CHAT_ID_ENV",
    "SERVERCHAN_ENABLED_ENV",
    "SERVERCHAN_SENDKEY_ENV",
    "DEFAULT_TIMEOUT_SECONDS",
    "MIN_TIMEOUT_SECONDS",
    "MAX_TIMEOUT_SECONDS",
    "MAX_RESPONSE_BYTES",
    "MAX_MESSAGE_BYTES",
    "TRUTHY_VALUES",
    "NotificationConfigError",
    "NotificationDeliveryError",
    "Notification",
    "DeliveryResult",
    "JsonTransport",
    "Clock",
    "NotificationChannel",
    "ChannelFactory",
    "FeishuChannel",
    "DingTalkChannel",
    "WeComChannel",
    "TelegramChannel",
    "ServerChanChannel",
    "register_channel",
    "registered_channels",
    "dispatch",
    "dispatch_to_channel",
    "notify_trade_executions",
    "_NoRedirectHandler",
    "_post_json",
]
