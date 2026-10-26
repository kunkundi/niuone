"""Shared notification models, protocols, errors, and configuration constants."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol


GLOBAL_ENABLED_ENV = "DASHBOARD_NOTIFICATION_ENABLED"
TIMEOUT_ENV = "DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS"

FEISHU_ENABLED_ENV = "DASHBOARD_FEISHU_NOTIFICATION_ENABLED"
FEISHU_WEBHOOK_ENV = "DASHBOARD_FEISHU_WEBHOOK_URL"
FEISHU_SECRET_ENV = "DASHBOARD_FEISHU_SIGNING_SECRET"

DINGTALK_ENABLED_ENV = "DASHBOARD_DINGTALK_NOTIFICATION_ENABLED"
DINGTALK_WEBHOOK_ENV = "DASHBOARD_DINGTALK_WEBHOOK_URL"
DINGTALK_SECRET_ENV = "DASHBOARD_DINGTALK_SIGNING_SECRET"

WECOM_ENABLED_ENV = "DASHBOARD_WECOM_NOTIFICATION_ENABLED"
WECOM_WEBHOOK_ENV = "DASHBOARD_WECOM_WEBHOOK_URL"

TELEGRAM_ENABLED_ENV = "DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED"
TELEGRAM_TOKEN_ENV = "DASHBOARD_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID_ENV = "DASHBOARD_TELEGRAM_CHAT_ID"

SERVERCHAN_ENABLED_ENV = "DASHBOARD_SERVERCHAN_NOTIFICATION_ENABLED"
SERVERCHAN_SENDKEY_ENV = "DASHBOARD_SERVERCHAN_SENDKEY"

DEFAULT_TIMEOUT_SECONDS = 5.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 30.0
MAX_RESPONSE_BYTES = 64 * 1024
# Enterprise WeChat has the tightest plain-text allowance of the built-in
# channels. A shared conservative cap keeps one rendering valid everywhere.
MAX_MESSAGE_BYTES = 1900
TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


class NotificationConfigError(ValueError):
    """A notification channel is enabled but its configuration is invalid."""


class NotificationDeliveryError(RuntimeError):
    """A remote endpoint returned an unusable or unsuccessful response."""


def _truncate_utf8(value: str, max_bytes: int) -> str:
    """Truncate text without splitting a UTF-8 code point."""

    encoded = str(value or "").encode("utf-8")
    if len(encoded) <= max_bytes:
        return str(value or "")
    suffix = "\n…内容已截断".encode("utf-8")
    prefix = encoded[: max(0, max_bytes - len(suffix))]
    while prefix:
        try:
            text = prefix.decode("utf-8")
            return text.rstrip() + suffix.decode("utf-8")
        except UnicodeDecodeError:
            prefix = prefix[:-1]
    return suffix.decode("utf-8")


def _escape_markdown(value: Any) -> str:
    text = html.escape(str(value or ""), quote=False)
    return re.sub(r"([\\`*_\[\]~#])", r"\\\1", text)


@dataclass(frozen=True)
class Notification:
    """Channel-neutral notification content."""

    event_type: str
    title: str
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    markdown: str = ""
    html: str = ""
    card_sections: tuple[Mapping[str, Any], ...] = ()

    def plain_text(self) -> str:
        title = str(self.title or "").strip()
        body = str(self.text or "").strip()
        rendered = "\n".join(part for part in (title, body) if part)
        return _truncate_utf8(rendered, MAX_MESSAGE_BYTES)

    def markdown_text(self, *, include_title: bool = True) -> str:
        """Return bounded rich content, or blank when plain text is safer."""

        body = str(self.markdown or "").strip()
        if not body:
            return ""
        title = str(self.title or "").strip()
        parts = ([f"### {_escape_markdown(title)}"] if include_title and title else []) + [body]
        rendered = "\n\n".join(parts)
        return rendered if len(rendered.encode("utf-8")) <= MAX_MESSAGE_BYTES else ""

    def html_text(self) -> str:
        """Return bounded Telegram-safe HTML, or blank to request plain text."""

        body = str(self.html or "").strip()
        if not body:
            return ""
        title = str(self.title or "").strip()
        parts = ([f"<b>{html.escape(title, quote=False)}</b>"] if title else []) + [body]
        rendered = "\n\n".join(parts)
        return rendered if len(rendered.encode("utf-8")) <= MAX_MESSAGE_BYTES else ""


@dataclass(frozen=True)
class DeliveryResult:
    """Sanitized outcome for one channel delivery attempt."""

    channel: str
    ok: bool
    error: str = ""


JsonTransport = Callable[[str, Mapping[str, Any], float], Mapping[str, Any]]
Clock = Callable[[], float]


class NotificationChannel(Protocol):
    """Adapter contract used by the channel registry."""

    name: str

    def send(
        self,
        notification: Notification,
        *,
        timeout: float,
        transport: JsonTransport,
        clock: Clock,
    ) -> None:
        """Send once or raise a sanitized notification-specific exception."""


ChannelFactory = Callable[[Mapping[str, Any]], NotificationChannel]


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
]
