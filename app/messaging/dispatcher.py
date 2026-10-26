"""Channel registry and best-effort notification dispatch orchestration."""
from __future__ import annotations

import concurrent.futures
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .channels import (
    _dingtalk_factory,
    _feishu_factory,
    _serverchan_factory,
    _telegram_factory,
    _wecom_factory,
)
from .models import (
    DEFAULT_TIMEOUT_SECONDS,
    DINGTALK_ENABLED_ENV,
    FEISHU_ENABLED_ENV,
    GLOBAL_ENABLED_ENV,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    SERVERCHAN_ENABLED_ENV,
    TELEGRAM_ENABLED_ENV,
    TIMEOUT_ENV,
    TRUTHY_VALUES,
    WECOM_ENABLED_ENV,
    ChannelFactory,
    Clock,
    DeliveryResult,
    JsonTransport,
    Notification,
    NotificationChannel,
    NotificationConfigError,
)
from .transport import _post_json, _sanitized_error, _sensitive_values


@dataclass(frozen=True)
class _ChannelRegistration:
    name: str
    enabled_env: str
    factory: ChannelFactory


# This is the only registry used by both the messaging package and the legacy
# notifications facade. Keeping the mutable state here prevents facade imports
# or reloads from silently creating an independent channel list.
_CHANNEL_REGISTRY: dict[str, _ChannelRegistration] = {}
_REGISTRY_LOCK = threading.RLock()

# Every built-in channel is delivered concurrently so one slow provider cannot
# delay the others. The cap bounds thread usage if callers register more.
MAX_DELIVERY_WORKERS = 8


def register_channel(
    name: str,
    factory: ChannelFactory,
    *,
    enabled_env: str,
    replace: bool = False,
) -> None:
    """Register a channel factory without coupling callers to its adapter.

    Factories are only called when ``enabled_env`` is truthy. Registration
    order is also delivery-result order, even though network calls are made in
    parallel.
    """

    normalized_name = str(name or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", normalized_name):
        raise ValueError("invalid notification channel name")
    if not callable(factory):
        raise TypeError("notification channel factory must be callable")
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", str(enabled_env or "")):
        raise ValueError("invalid notification channel enabled setting")
    with _REGISTRY_LOCK:
        if normalized_name in _CHANNEL_REGISTRY and not replace:
            raise ValueError(f"notification channel already registered: {normalized_name}")
        _CHANNEL_REGISTRY[normalized_name] = _ChannelRegistration(
            normalized_name,
            str(enabled_env),
            factory,
        )


def registered_channels() -> tuple[str, ...]:
    """Return a stable snapshot of registered channel names."""

    with _REGISTRY_LOCK:
        return tuple(_CHANNEL_REGISTRY)


def _env_text(env: Mapping[str, Any], name: str) -> str:
    value = env.get(name, "")
    return str(value if value is not None else "").strip()


def _enabled(env: Mapping[str, Any], name: str) -> bool:
    return _env_text(env, name).lower() in TRUTHY_VALUES


def _timeout_seconds(env: Mapping[str, Any]) -> float:
    raw = _env_text(env, TIMEOUT_ENV)
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except (TypeError, ValueError) as exc:
        raise NotificationConfigError(f"{TIMEOUT_ENV} must be a number") from exc
    if not math.isfinite(timeout) or not MIN_TIMEOUT_SECONDS <= timeout <= MAX_TIMEOUT_SECONDS:
        raise NotificationConfigError(
            f"{TIMEOUT_ENV} must be between "
            f"{int(MIN_TIMEOUT_SECONDS)} and {int(MAX_TIMEOUT_SECONDS)}"
        )
    return timeout


def _deliver_once(
    channel: NotificationChannel,
    notification: Notification,
    timeout: float,
    transport: JsonTransport,
    clock: Clock,
    secrets: tuple[str, ...],
) -> DeliveryResult:
    try:
        channel.send(notification, timeout=timeout, transport=transport, clock=clock)
        return DeliveryResult(channel=channel.name, ok=True)
    except Exception as exc:  # channel isolation is intentional
        return DeliveryResult(
            channel=channel.name,
            ok=False,
            error=_sanitized_error(exc, secrets),
        )


def dispatch(
    notification: Notification,
    env: Mapping[str, Any] | None = None,
    *,
    transport: JsonTransport | None = None,
    clock: Clock | None = None,
) -> list[DeliveryResult]:
    """Deliver to every enabled channel once, concurrently and in stable order."""

    settings: Mapping[str, Any] = os.environ if env is None else env
    if not _enabled(settings, GLOBAL_ENABLED_ENV):
        return []
    with _REGISTRY_LOCK:
        registrations = tuple(_CHANNEL_REGISTRY.values())
    active = [
        registration
        for registration in registrations
        if _enabled(settings, registration.enabled_env)
    ]
    if not active:
        return []
    secrets = _sensitive_values(settings)

    try:
        timeout = _timeout_seconds(settings)
    except Exception as exc:
        error = _sanitized_error(exc, secrets)
        return [DeliveryResult(item.name, False, error) for item in active]

    selected_transport = transport or _post_json
    selected_clock = clock or time.time
    slots: list[DeliveryResult | None] = [None] * len(active)
    ready: list[tuple[int, NotificationChannel]] = []
    for index, registration in enumerate(active):
        try:
            channel = registration.factory(settings)
            ready.append((index, channel))
        except Exception as exc:
            slots[index] = DeliveryResult(
                registration.name,
                False,
                _sanitized_error(exc, secrets),
            )

    if ready:
        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(MAX_DELIVERY_WORKERS, len(ready))
            ) as executor:
                futures = {
                    executor.submit(
                        _deliver_once,
                        channel,
                        notification,
                        timeout,
                        selected_transport,
                        selected_clock,
                        secrets,
                    ): index
                    for index, channel in ready
                }
                for future, index in futures.items():
                    try:
                        slots[index] = future.result()
                    except Exception as exc:  # guard executor/future failures too
                        slots[index] = DeliveryResult(
                            active[index].name,
                            False,
                            _sanitized_error(exc, secrets),
                        )
        except Exception as exc:
            error = _sanitized_error(exc, secrets)
            for index, _channel in ready:
                if slots[index] is None:
                    slots[index] = DeliveryResult(active[index].name, False, error)

    return [
        result
        if result is not None
        else DeliveryResult(active[index].name, False, "delivery error")
        for index, result in enumerate(slots)
    ]


def dispatch_to_channel(
    notification: Notification,
    channel_name: str,
    env: Mapping[str, Any] | None = None,
    *,
    transport: JsonTransport | None = None,
    clock: Clock | None = None,
) -> DeliveryResult:
    """Deliver one notification to one channel for a configuration test.

    This deliberately ignores the global and per-channel enabled flags. It is
    intended for an explicit, authenticated test action where the caller has
    already selected exactly one channel.
    """

    normalized_name = str(channel_name or "").strip().lower()
    with _REGISTRY_LOCK:
        registration = _CHANNEL_REGISTRY.get(normalized_name)
    if registration is None:
        return DeliveryResult(
            normalized_name or "notification",
            False,
            "unknown notification channel",
        )

    settings: Mapping[str, Any] = os.environ if env is None else env
    secrets = _sensitive_values(settings)
    try:
        timeout = _timeout_seconds(settings)
        channel = registration.factory(settings)
    except Exception as exc:
        return DeliveryResult(
            registration.name,
            False,
            _sanitized_error(exc, secrets),
        )

    return _deliver_once(
        channel,
        notification,
        timeout,
        transport or _post_json,
        clock or time.time,
        secrets,
    )


register_channel("feishu", _feishu_factory, enabled_env=FEISHU_ENABLED_ENV)
register_channel("dingtalk", _dingtalk_factory, enabled_env=DINGTALK_ENABLED_ENV)
register_channel("wecom", _wecom_factory, enabled_env=WECOM_ENABLED_ENV)
register_channel("telegram", _telegram_factory, enabled_env=TELEGRAM_ENABLED_ENV)
register_channel("serverchan", _serverchan_factory, enabled_env=SERVERCHAN_ENABLED_ENV)


__all__ = [
    "register_channel",
    "registered_channels",
    "dispatch",
    "dispatch_to_channel",
]
