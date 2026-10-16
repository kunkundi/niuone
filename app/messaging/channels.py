"""Built-in notification channel validation, adapters, and factories."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Mapping

from .models import (
    DINGTALK_SECRET_ENV,
    DINGTALK_WEBHOOK_ENV,
    FEISHU_SECRET_ENV,
    FEISHU_WEBHOOK_ENV,
    SERVERCHAN_SENDKEY_ENV,
    TELEGRAM_CHAT_ID_ENV,
    TELEGRAM_TOKEN_ENV,
    WECOM_WEBHOOK_ENV,
    Notification,
    NotificationChannel,
    NotificationConfigError,
    NotificationDeliveryError,
    JsonTransport,
    Clock,
    MAX_MESSAGE_BYTES,
    _escape_markdown,
    _truncate_utf8,
)


_FEISHU_MAX_PAYLOAD_BYTES = 28 * 1024

# Server酱 rejects titles longer than 32 characters and titles with line
# breaks, so the channel bounds the title itself instead of failing delivery.
_SERVERCHAN_MAX_TITLE_CHARS = 32
_SERVERCHAN_TURBO_HOST = "sctapi.ftqq.com"
_SERVERCHAN_SC3_HOST_SUFFIX = ".push.ft07.com"
_SERVERCHAN_TURBO_KEY_RE = re.compile(r"SCT[A-Za-z0-9]{8,64}\Z")
_SERVERCHAN_SC3_KEY_RE = re.compile(r"sctp(\d{1,10})t[A-Za-z0-9]{4,64}\Z")


def _reject_controls(value: str, field_name: str) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise NotificationConfigError(f"{field_name} contains control characters")
    return value


def _required_credential(env: Mapping[str, Any], name: str) -> str:
    raw = str(env.get(name, "") if env.get(name, "") is not None else "")
    _reject_controls(raw, name)
    value = raw.strip()
    if not value:
        raise NotificationConfigError(f"{name} is required")
    return value


def _optional_credential(env: Mapping[str, Any], name: str) -> str:
    raw = str(env.get(name, "") if env.get(name, "") is not None else "")
    _reject_controls(raw, name)
    return raw.strip()


def _split_https_url(raw_url: str, field_name: str) -> urllib.parse.SplitResult:
    _reject_controls(raw_url, field_name)
    try:
        parsed = urllib.parse.urlsplit(raw_url)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise NotificationConfigError(f"{field_name} is not a valid URL") from exc
    if parsed.scheme.lower() != "https":
        raise NotificationConfigError(f"{field_name} must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise NotificationConfigError(f"{field_name} must not contain URL credentials")
    if port not in (None, 443):
        raise NotificationConfigError(f"{field_name} must use the default HTTPS port")
    if parsed.fragment:
        raise NotificationConfigError(f"{field_name} must not contain a fragment")
    if not parsed.hostname:
        raise NotificationConfigError(f"{field_name} must contain a host")
    return parsed


def _require_host(parsed: urllib.parse.SplitResult, allowed: set[str], field_name: str) -> None:
    host = str(parsed.hostname or "").lower()
    if host not in allowed:
        raise NotificationConfigError(f"{field_name} host is not allowed")


def _single_query_value(
    parsed: urllib.parse.SplitResult,
    key: str,
    field_name: str,
) -> str:
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {key} or len(query.get(key, [])) != 1 or not query[key][0]:
        raise NotificationConfigError(f"{field_name} must contain one {key} value")
    return _reject_controls(query[key][0], field_name)


def _validate_feishu_webhook(url: str) -> str:
    parsed = _split_https_url(url, FEISHU_WEBHOOK_ENV)
    _require_host(parsed, {"open.feishu.cn", "open.larksuite.com"}, FEISHU_WEBHOOK_ENV)
    if parsed.query:
        raise NotificationConfigError(f"{FEISHU_WEBHOOK_ENV} must not contain a query")
    if not re.fullmatch(r"/open-apis/bot/v2/hook/[A-Za-z0-9_-]{8,}", parsed.path):
        raise NotificationConfigError(f"{FEISHU_WEBHOOK_ENV} path is invalid")
    return url


def _validate_dingtalk_webhook(url: str) -> str:
    parsed = _split_https_url(url, DINGTALK_WEBHOOK_ENV)
    _require_host(parsed, {"oapi.dingtalk.com"}, DINGTALK_WEBHOOK_ENV)
    if parsed.path != "/robot/send":
        raise NotificationConfigError(f"{DINGTALK_WEBHOOK_ENV} path is invalid")
    _single_query_value(parsed, "access_token", DINGTALK_WEBHOOK_ENV)
    return url


def _validate_wecom_webhook(url: str) -> str:
    parsed = _split_https_url(url, WECOM_WEBHOOK_ENV)
    _require_host(parsed, {"qyapi.weixin.qq.com"}, WECOM_WEBHOOK_ENV)
    if parsed.path != "/cgi-bin/webhook/send":
        raise NotificationConfigError(f"{WECOM_WEBHOOK_ENV} path is invalid")
    _single_query_value(parsed, "key", WECOM_WEBHOOK_ENV)
    return url


def _safe_provider_code(value: Any) -> str:
    code = str(value if value is not None else "unknown")
    return code if re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", code) else "unknown"


def _zero_code(value: Any) -> bool:
    return value == 0 or value == "0"


def _require_mapping_response(response: Mapping[str, Any] | Any) -> Mapping[str, Any]:
    if not isinstance(response, Mapping):
        raise NotificationDeliveryError("provider returned an invalid response")
    return response


def _feishu_card_elements(notification: Notification) -> list[dict[str, Any]]:
    """Render structured notification sections with Feishu-native layout."""

    if not notification.card_sections:
        return []
    elements: list[dict[str, Any]] = []
    rendered_sections = 0
    for section in notification.card_sections:
        if not isinstance(section, Mapping):
            continue
        title = str(section.get("title") or "").strip()
        raw_fields = section.get("fields")
        if not title or not isinstance(raw_fields, (list, tuple)):
            continue
        action = str(section.get("action") or "").strip().upper()
        name = str(section.get("name") or "").strip()
        code = str(section.get("code") or "").strip()
        sequence = str(section.get("sequence") or "").strip()
        if action in {"BUY", "SELL"} and name and code:
            direction = "买入" if action == "BUY" else "卖出"
            direction = _escape_markdown(str(section.get("action_label") or direction))
            if section.get("emphasize_action") is True:
                direction = f"**{direction}**"
            direction_color = "red" if action == "BUY" else "green"
            sequence_prefix = f"{_escape_markdown(sequence)}. " if sequence else ""
            heading_content = (
                f"**{sequence_prefix}{_escape_markdown(name)}"
                f"（{_escape_markdown(code)}）**　"
                f"<font color='{direction_color}'>{direction}</font>"
            )
        else:
            heading_content = f"**{_escape_markdown(title)}**"

        short_fields: list[dict[str, Any]] = []
        detail_lines: list[str] = []
        for raw_field in raw_fields:
            if not isinstance(raw_field, Mapping):
                continue
            label = str(raw_field.get("label") or "").strip()
            value = str(raw_field.get("value") or "").strip()
            if not label or not value:
                continue
            escaped_label = _escape_markdown(label)
            escaped_value = _escape_markdown(value)
            field_color = str(raw_field.get("color") or "").strip()
            if bool(raw_field.get("short")):
                field_content = f"**{escaped_label}**\n{escaped_value}"
                if field_color in {"red", "green"}:
                    field_content = f"<font color='{field_color}'>{field_content}</font>"
                short_fields.append({
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": field_content,
                    },
                })
            else:
                detail_content = f"**{escaped_label}**　{escaped_value}"
                if field_color in {"red", "green"}:
                    detail_content = f"<font color='{field_color}'>{detail_content}</font>"
                detail_lines.append(detail_content)
        if not short_fields and not detail_lines:
            continue
        if rendered_sections:
            elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": heading_content,
            },
        })
        if short_fields:
            elements.append({"tag": "div", "fields": short_fields})
        if detail_lines:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "\n".join(detail_lines),
                },
            })
        rendered_sections += 1
    return elements if rendered_sections else []


def _feishu_interactive_payload(
    notification: Notification,
    elements: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {
                    "tag": "plain_text",
                    "content": str(notification.title or "").strip(),
                },
            },
            "elements": elements,
        },
    }


def _feishu_payload_size(payload: Mapping[str, Any]) -> int:
    return len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _feishu_structured_payloads(
    notification: Notification,
    elements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pack complete trade sections into bounded Feishu cards."""

    section_groups: list[list[dict[str, Any]]] = []
    current_group: list[dict[str, Any]] = []
    for element in elements:
        if element.get("tag") == "hr":
            if current_group:
                section_groups.append(current_group)
                current_group = []
            continue
        current_group.append(element)
    if current_group:
        section_groups.append(current_group)

    payloads: list[dict[str, Any]] = []
    packed_elements: list[dict[str, Any]] = []
    for group in section_groups:
        candidate_elements = packed_elements + (
            [{"tag": "hr"}] if packed_elements else []
        ) + group
        candidate_payload = _feishu_interactive_payload(notification, candidate_elements)
        if packed_elements and _feishu_payload_size(candidate_payload) > _FEISHU_MAX_PAYLOAD_BYTES:
            payloads.append(_feishu_interactive_payload(notification, packed_elements))
            packed_elements = list(group)
            continue
        packed_elements = candidate_elements

    if packed_elements:
        payloads.append(_feishu_interactive_payload(notification, packed_elements))
    if any(_feishu_payload_size(payload) > _FEISHU_MAX_PAYLOAD_BYTES for payload in payloads):
        raise NotificationDeliveryError("Feishu card section exceeds the safe payload limit")
    return payloads


@dataclass(frozen=True)
class FeishuChannel:
    webhook_url: str
    signing_secret: str = ""
    name: str = field(default="feishu", init=False)

    def send(
        self,
        notification: Notification,
        *,
        timeout: float,
        transport: JsonTransport,
        clock: Clock,
    ) -> None:
        rich_text = notification.markdown_text(include_title=False)
        card_elements = _feishu_card_elements(notification)
        if card_elements:
            payloads = _feishu_structured_payloads(notification, card_elements)
        elif rich_text:
            payloads = [
                _feishu_interactive_payload(
                    notification,
                    [{"tag": "markdown", "content": rich_text}],
                )
            ]
        else:
            payloads = [{
                "msg_type": "text",
                "content": {"text": notification.plain_text()},
            }]
        signing_fields: dict[str, str] = {}
        if self.signing_secret:
            timestamp = str(int(clock()))
            string_to_sign = f"{timestamp}\n{self.signing_secret}".encode("utf-8")
            digest = hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
            signing_fields = {
                "timestamp": timestamp,
                "sign": base64.b64encode(digest).decode("ascii"),
            }
        for payload in payloads:
            payload.update(signing_fields)
            response = _require_mapping_response(transport(self.webhook_url, payload, timeout))
            code = response.get("code") if "code" in response else response.get("StatusCode")
            if not _zero_code(code):
                raise NotificationDeliveryError(
                    f"provider rejected request (code={_safe_provider_code(code)})"
                )


@dataclass(frozen=True)
class DingTalkChannel:
    webhook_url: str
    signing_secret: str = ""
    name: str = field(default="dingtalk", init=False)

    def _signed_url(self, clock: Clock) -> str:
        if not self.signing_secret:
            return self.webhook_url
        timestamp = str(int(clock() * 1000))
        string_to_sign = f"{timestamp}\n{self.signing_secret}".encode("utf-8")
        digest = hmac.new(
            self.signing_secret.encode("utf-8"),
            string_to_sign,
            digestmod=hashlib.sha256,
        ).digest()
        sign = base64.b64encode(digest).decode("ascii")
        parsed = urllib.parse.urlsplit(self.webhook_url)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query.extend((("timestamp", timestamp), ("sign", sign)))
        return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))

    def send(
        self,
        notification: Notification,
        *,
        timeout: float,
        transport: JsonTransport,
        clock: Clock,
    ) -> None:
        rich_text = notification.markdown_text()
        if rich_text:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": str(notification.title or "").strip(),
                    "text": rich_text,
                },
                "at": {"isAtAll": False},
            }
        else:
            payload = {
                "msgtype": "text",
                "text": {"content": notification.plain_text()},
                "at": {"isAtAll": False},
            }
        response = _require_mapping_response(transport(self._signed_url(clock), payload, timeout))
        code = response.get("errcode")
        if not _zero_code(code):
            raise NotificationDeliveryError(
                f"provider rejected request (code={_safe_provider_code(code)})"
            )


@dataclass(frozen=True)
class WeComChannel:
    webhook_url: str
    name: str = field(default="wecom", init=False)

    def send(
        self,
        notification: Notification,
        *,
        timeout: float,
        transport: JsonTransport,
        clock: Clock,
    ) -> None:
        del clock
        rich_text = notification.markdown_text()
        if rich_text:
            payload = {
                "msgtype": "markdown",
                "markdown": {"content": rich_text},
            }
        else:
            payload = {
                "msgtype": "text",
                "text": {"content": notification.plain_text()},
            }
        response = _require_mapping_response(transport(self.webhook_url, payload, timeout))
        code = response.get("errcode")
        if not _zero_code(code):
            raise NotificationDeliveryError(
                f"provider rejected request (code={_safe_provider_code(code)})"
            )


@dataclass(frozen=True)
class TelegramChannel:
    bot_token: str
    chat_id: str
    name: str = field(default="telegram", init=False)

    @property
    def endpoint(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def send(
        self,
        notification: Notification,
        *,
        timeout: float,
        transport: JsonTransport,
        clock: Clock,
    ) -> None:
        del clock
        rich_text = notification.html_text()
        payload = {
            "chat_id": self.chat_id,
            "text": rich_text or notification.plain_text(),
            "disable_web_page_preview": True,
        }
        if rich_text:
            payload["parse_mode"] = "HTML"
        response = _require_mapping_response(transport(self.endpoint, payload, timeout))
        if response.get("ok") is not True:
            raise NotificationDeliveryError("provider rejected request")


def _serverchan_title(notification: Notification) -> str:
    """Build a Server酱-safe title: one line, never longer than 32 characters."""

    title = re.sub(r"\s+", " ", str(notification.title or "")).strip()
    if not title:
        title = "NiuOne 通知"
    if len(title) <= _SERVERCHAN_MAX_TITLE_CHARS:
        return title
    return title[: _SERVERCHAN_MAX_TITLE_CHARS - 1].rstrip() + "…"


def _serverchan_endpoint(send_key: str) -> str:
    """Select the push endpoint from the SendKey generation prefix."""

    sc3 = _SERVERCHAN_SC3_KEY_RE.fullmatch(send_key)
    if sc3 is not None:
        return f"https://{sc3.group(1)}{_SERVERCHAN_SC3_HOST_SUFFIX}/send/{send_key}.send"
    return f"https://{_SERVERCHAN_TURBO_HOST}/{send_key}.send"


@dataclass(frozen=True)
class ServerChanChannel:
    send_key: str
    name: str = field(default="serverchan", init=False)

    @property
    def endpoint(self) -> str:
        return _serverchan_endpoint(self.send_key)

    def send(
        self,
        notification: Notification,
        *,
        timeout: float,
        transport: JsonTransport,
        clock: Clock,
    ) -> None:
        del clock
        body = notification.markdown_text(include_title=False)
        if not body:
            body = _truncate_utf8(
                str(notification.text or "").strip(),
                MAX_MESSAGE_BYTES,
            )
        payload = {
            "title": _serverchan_title(notification),
            "desp": body,
        }
        response = _require_mapping_response(transport(self.endpoint, payload, timeout))
        code = response.get("code")
        if not _zero_code(code):
            raise NotificationDeliveryError(
                f"provider rejected request (code={_safe_provider_code(code)})"
            )


def _feishu_factory(env: Mapping[str, Any]) -> NotificationChannel:
    webhook = _validate_feishu_webhook(_required_credential(env, FEISHU_WEBHOOK_ENV))
    return FeishuChannel(webhook, _optional_credential(env, FEISHU_SECRET_ENV))


def _dingtalk_factory(env: Mapping[str, Any]) -> NotificationChannel:
    webhook = _validate_dingtalk_webhook(_required_credential(env, DINGTALK_WEBHOOK_ENV))
    return DingTalkChannel(webhook, _optional_credential(env, DINGTALK_SECRET_ENV))


def _wecom_factory(env: Mapping[str, Any]) -> NotificationChannel:
    webhook = _validate_wecom_webhook(_required_credential(env, WECOM_WEBHOOK_ENV))
    return WeComChannel(webhook)


def _telegram_factory(env: Mapping[str, Any]) -> NotificationChannel:
    token = _required_credential(env, TELEGRAM_TOKEN_ENV)
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", token):
        raise NotificationConfigError(f"{TELEGRAM_TOKEN_ENV} format is invalid")
    chat_id = _required_credential(env, TELEGRAM_CHAT_ID_ENV)
    numeric_chat = bool(re.fullmatch(r"-?\d+", chat_id))
    named_chat = bool(re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{4,31}", chat_id))
    if not numeric_chat and not named_chat:
        raise NotificationConfigError(f"{TELEGRAM_CHAT_ID_ENV} format is invalid")
    return TelegramChannel(token, chat_id)


def _serverchan_factory(env: Mapping[str, Any]) -> NotificationChannel:
    send_key = _required_credential(env, SERVERCHAN_SENDKEY_ENV)
    if not _SERVERCHAN_SC3_KEY_RE.fullmatch(send_key) and not _SERVERCHAN_TURBO_KEY_RE.fullmatch(send_key):
        raise NotificationConfigError(f"{SERVERCHAN_SENDKEY_ENV} format is invalid")
    return ServerChanChannel(send_key)


__all__ = [
    "FeishuChannel",
    "DingTalkChannel",
    "WeComChannel",
    "TelegramChannel",
    "ServerChanChannel",
]
