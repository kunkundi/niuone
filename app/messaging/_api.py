"""Public API assembly for the notification delivery package.

The package is split by responsibility while keeping one dispatcher registry
for both direct package consumers and the legacy :mod:`notifications` facade.
"""
from __future__ import annotations

from . import channels as _channels_module
from . import dispatcher as _dispatcher_module
from . import models as _models_module
from . import trades as _trades_module
from . import transport as _transport_module


# Models and constants
GLOBAL_ENABLED_ENV = _models_module.GLOBAL_ENABLED_ENV
TIMEOUT_ENV = _models_module.TIMEOUT_ENV
FEISHU_ENABLED_ENV = _models_module.FEISHU_ENABLED_ENV
FEISHU_WEBHOOK_ENV = _models_module.FEISHU_WEBHOOK_ENV
FEISHU_SECRET_ENV = _models_module.FEISHU_SECRET_ENV
DINGTALK_ENABLED_ENV = _models_module.DINGTALK_ENABLED_ENV
DINGTALK_WEBHOOK_ENV = _models_module.DINGTALK_WEBHOOK_ENV
DINGTALK_SECRET_ENV = _models_module.DINGTALK_SECRET_ENV
WECOM_ENABLED_ENV = _models_module.WECOM_ENABLED_ENV
WECOM_WEBHOOK_ENV = _models_module.WECOM_WEBHOOK_ENV
TELEGRAM_ENABLED_ENV = _models_module.TELEGRAM_ENABLED_ENV
TELEGRAM_TOKEN_ENV = _models_module.TELEGRAM_TOKEN_ENV
TELEGRAM_CHAT_ID_ENV = _models_module.TELEGRAM_CHAT_ID_ENV
SERVERCHAN_ENABLED_ENV = _models_module.SERVERCHAN_ENABLED_ENV
SERVERCHAN_SENDKEY_ENV = _models_module.SERVERCHAN_SENDKEY_ENV
DEFAULT_TIMEOUT_SECONDS = _models_module.DEFAULT_TIMEOUT_SECONDS
MIN_TIMEOUT_SECONDS = _models_module.MIN_TIMEOUT_SECONDS
MAX_TIMEOUT_SECONDS = _models_module.MAX_TIMEOUT_SECONDS
MAX_RESPONSE_BYTES = _models_module.MAX_RESPONSE_BYTES
MAX_MESSAGE_BYTES = _models_module.MAX_MESSAGE_BYTES
TRUTHY_VALUES = _models_module.TRUTHY_VALUES

NotificationConfigError = _models_module.NotificationConfigError
NotificationDeliveryError = _models_module.NotificationDeliveryError
Notification = _models_module.Notification
DeliveryResult = _models_module.DeliveryResult
JsonTransport = _models_module.JsonTransport
Clock = _models_module.Clock
NotificationChannel = _models_module.NotificationChannel
ChannelFactory = _models_module.ChannelFactory

# Built-in adapters
FeishuChannel = _channels_module.FeishuChannel
DingTalkChannel = _channels_module.DingTalkChannel
WeComChannel = _channels_module.WeComChannel
TelegramChannel = _channels_module.TelegramChannel
ServerChanChannel = _channels_module.ServerChanChannel

# Registry and dispatch
register_channel = _dispatcher_module.register_channel
registered_channels = _dispatcher_module.registered_channels
dispatch = _dispatcher_module.dispatch
dispatch_to_channel = _dispatcher_module.dispatch_to_channel

# Domain-specific entry point
notify_trade_executions = _trades_module.notify_trade_executions

# Compatibility helpers retained for the old single-module API. These are
# aliases to the implementation objects, not copied implementations or state.
_truncate_utf8 = _models_module._truncate_utf8
_reject_controls = _channels_module._reject_controls
_required_credential = _channels_module._required_credential
_optional_credential = _channels_module._optional_credential
_split_https_url = _channels_module._split_https_url
_require_host = _channels_module._require_host
_single_query_value = _channels_module._single_query_value
_validate_feishu_webhook = _channels_module._validate_feishu_webhook
_validate_dingtalk_webhook = _channels_module._validate_dingtalk_webhook
_validate_wecom_webhook = _channels_module._validate_wecom_webhook
_safe_provider_code = _channels_module._safe_provider_code
_zero_code = _channels_module._zero_code
_require_mapping_response = _channels_module._require_mapping_response
_feishu_factory = _channels_module._feishu_factory
_dingtalk_factory = _channels_module._dingtalk_factory
_wecom_factory = _channels_module._wecom_factory
_telegram_factory = _channels_module._telegram_factory
_serverchan_factory = _channels_module._serverchan_factory
_serverchan_endpoint = _channels_module._serverchan_endpoint
_NoRedirectHandler = _transport_module._NoRedirectHandler
_post_json = _transport_module._post_json
_sensitive_values = _transport_module._sensitive_values
_redact_error_text = _transport_module._redact_error_text
_sanitized_error = _transport_module._sanitized_error
_ChannelRegistration = _dispatcher_module._ChannelRegistration
_CHANNEL_REGISTRY = _dispatcher_module._CHANNEL_REGISTRY
_REGISTRY_LOCK = _dispatcher_module._REGISTRY_LOCK
_env_text = _dispatcher_module._env_text
_enabled = _dispatcher_module._enabled
_timeout_seconds = _dispatcher_module._timeout_seconds
_deliver_once = _dispatcher_module._deliver_once
_clean_trade_text = _trades_module._clean_trade_text
_finite_float = _trades_module._finite_float
_money = _trades_module._money
_price = _trades_module._price
_percentage = _trades_module._percentage
_trade_notification = _trades_module._trade_notification


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
