"""Stateless security and request-parsing helpers for the dashboard."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import time
from collections.abc import Callable, Iterable
from http import cookies
from pathlib import Path
from typing import Any


Network = ipaddress.IPv4Network | ipaddress.IPv6Network
NetworkParser = Callable[[str], Network | None]


def hash_token(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def load_or_create_admin_token(token_file: Path, lock: Any) -> str:
    """Read or securely create the local admin bootstrap token."""

    with lock:
        if token_file.is_symlink():
            raise RuntimeError(f"admin token file must not be a symlink: {token_file}")
        if token_file.exists():
            token = token_file.read_text(encoding="utf-8").strip()
            if token:
                try:
                    token_file.chmod(0o600)
                except OSError:
                    pass
                return token
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token = "na_" + secrets.token_urlsafe(36)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(token_file), flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
        try:
            token_file.chmod(0o600)
        except OSError:
            pass
        return token


def derive_admin_session_signing_key(
    bootstrap_token: str,
    admin_password: str,
) -> bytes:
    credential_fingerprint = hashlib.sha256(admin_password.encode("utf-8")).digest()
    return hmac.new(
        bootstrap_token.encode("utf-8"),
        b"niuone-admin-session-v1\0" + credential_fingerprint,
        hashlib.sha256,
    ).digest()


def create_admin_session(signing_key: bytes, now: float | None = None) -> str:
    issued_at = int(time.time() if now is None else now)
    payload = f"{issued_at}.{secrets.token_urlsafe(18)}"
    signature = hmac.new(signing_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"ad_{payload}.{signature}"


def validate_admin_session(
    cookie_value: str,
    signing_key: bytes,
    *,
    ttl_seconds: int,
    now: float | None = None,
) -> bool:
    raw = str(cookie_value or "")
    if not raw.startswith("ad_"):
        return False
    try:
        issued_text, nonce, signature = raw[3:].split(".", 2)
        issued_at = int(issued_text)
    except (TypeError, ValueError):
        return False
    if not nonce or not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", nonce):
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", signature):
        return False
    current = int(time.time() if now is None else now)
    ttl = max(60, ttl_seconds)
    if issued_at > current + 60 or current - issued_at > ttl:
        return False
    payload = f"{issued_at}.{nonce}"
    expected = hmac.new(signing_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return secrets.compare_digest(signature, expected)


def verify_admin_credential(value: str, expected: str) -> bool:
    supplied = str(value or "")
    return bool(supplied) and secrets.compare_digest(
        supplied.encode("utf-8"),
        expected.encode("utf-8"),
    )


def consume_rate_limit(
    scope: str,
    key: str,
    limit: int,
    *,
    enabled: bool,
    default_window: int,
    buckets: dict[tuple[str, str], tuple[float, int]],
    lock: Any,
    window: int | None = None,
    now: float | None = None,
) -> tuple[bool, int]:
    if not enabled or limit <= 0:
        return True, 0
    resolved_window = window or default_window
    current_time = time.time() if now is None else now
    bucket_key = (scope, key or "unknown")
    with lock:
        started_at, count = buckets.get(bucket_key, (current_time, 0))
        if current_time - started_at >= resolved_window:
            started_at, count = current_time, 0
        if count >= limit:
            return False, max(1, int(resolved_window - (current_time - started_at)))
        buckets[bucket_key] = (started_at, count + 1)
        if len(buckets) > 10000:
            cutoff = current_time - resolved_window * 3
            for old_key, (old_started, _) in list(buckets.items()):
                if old_started < cutoff:
                    buckets.pop(old_key, None)
    return True, 0


def parse_request_cookies(header: str | None) -> dict[str, str]:
    jar = cookies.SimpleCookie()
    if header:
        try:
            jar.load(header)
        except cookies.CookieError:
            return {}
    return {key: morsel.value for key, morsel in jar.items()}


def is_truthy_header(value: str | None) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on", "https"})


def parse_ip_network(value: str) -> Network | None:
    try:
        return ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        return None


def is_trusted_proxy_ip(
    ip_text: str,
    trusted_proxy_cidrs: Iterable[str],
    *,
    parse_network: NetworkParser = parse_ip_network,
) -> bool:
    try:
        ip = ipaddress.ip_address(str(ip_text or "").strip())
    except ValueError:
        return False
    networks = [parse_network(item) for item in trusted_proxy_cidrs]
    return any(network is not None and ip in network for network in networks)


def first_forwarded_ip(*headers: str | None) -> str:
    for header in headers:
        for part in str(header or "").split(","):
            candidate = part.strip()
            if not candidate:
                continue
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                continue
            return candidate
    return ""


def clamp_limit(raw: str | None, *, default: int, maximum: int) -> int:
    try:
        value = int(raw) if raw else default
    except (TypeError, ValueError):
        value = default
    if value == 0:
        return 0
    return max(1, min(maximum, value))


def clamp_offset(raw: str | None, *, maximum: int) -> int:
    try:
        value = int(raw) if raw else 0
    except (TypeError, ValueError):
        value = 0
    return max(0, min(maximum, value))
