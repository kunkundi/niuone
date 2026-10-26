"""Bounded HTTP transport and secret-safe notification error handling."""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

from .models import MAX_RESPONSE_BYTES, NotificationConfigError, NotificationDeliveryError


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _post_json(url: str, payload: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
    """POST JSON without following redirects and return a bounded JSON object."""

    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "NiuOne/1.0",
        },
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    with opener.open(request, timeout=timeout) as response:
        status = getattr(response, "status", None)
        if status is None and hasattr(response, "getcode"):
            status = response.getcode()
        if status is not None and not 200 <= int(status) < 300:
            raise NotificationDeliveryError(f"HTTP status {int(status)}")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise NotificationDeliveryError("provider response is too large")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NotificationDeliveryError("provider returned invalid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise NotificationDeliveryError("provider returned an invalid response")
    return decoded


def _sensitive_values(env: Mapping[str, Any]) -> tuple[str, ...]:
    values: set[str] = set()
    sensitive_name = re.compile(r"(?:SECRET|TOKEN|WEBHOOK|PASSWORD|API_KEY|CREDENTIAL|SENDKEY)")
    try:
        items = tuple(env.items())
    except Exception:
        return ()
    for name, raw_value in items:
        if not sensitive_name.search(str(name).upper()):
            continue
        value = str(raw_value if raw_value is not None else "").strip()
        if len(value) < 4:
            continue
        values.add(value)
        try:
            parsed = urllib.parse.urlsplit(value)
            if parsed.scheme and parsed.path:
                tail = parsed.path.rstrip("/").rsplit("/", 1)[-1]
                if len(tail) >= 4:
                    values.add(tail)
                for _key, query_value in urllib.parse.parse_qsl(
                    parsed.query,
                    keep_blank_values=True,
                ):
                    if len(query_value) >= 4:
                        values.add(query_value)
        except (TypeError, ValueError):
            pass
    return tuple(sorted(values, key=len, reverse=True))


def _redact_error_text(value: str, secrets: tuple[str, ...]) -> str:
    redacted = str(value or "")
    for secret in secrets:
        redacted = redacted.replace(secret, "[redacted]")
    return redacted[:240]


def _sanitized_error(exc: BaseException, secrets: tuple[str, ...] = ()) -> str:
    if isinstance(exc, (NotificationConfigError, NotificationDeliveryError)):
        return _redact_error_text(str(exc), secrets)
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP error {int(exc.code)}"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "request timed out"
        return "network error"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "request timed out"
    # Never include arbitrary exception text: it may contain a webhook URL or
    # bot token supplied by a transport implementation.
    return type(exc).__name__ or "delivery error"


__all__ = ["_NoRedirectHandler", "_post_json"]
