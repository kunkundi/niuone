"""Bounded client for the iWencai SkillHub query gateway."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlparse


DEFAULT_BASE_URL = "https://openapi.iwencai.com"
QUERY_PATH = "/v1/query2data"
SKILL_ID = "hithink-market-query"
SKILL_VERSION = "1.0.0"
MAX_QUERY_CHARS = 500
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class IwencaiError(RuntimeError):
    """Base error carrying a stable, non-secret diagnostic code."""

    def __init__(self, code: str, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class IwencaiConfigurationError(IwencaiError):
    """Raised when the local iWencai configuration is unusable."""


class IwencaiRequestError(IwencaiError):
    """Raised when the remote request fails after bounded retries."""


class IwencaiResponseError(IwencaiError):
    """Raised when the gateway returns an invalid or unexpected payload."""


def normalize_base_url(value: str) -> str:
    """Validate an HTTPS gateway base URL without query or credentials."""

    normalized = str(value or DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("IWENCAI_BASE_URL 必须是有效的 HTTPS 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("IWENCAI_BASE_URL 不能包含凭据、查询参数或片段")
    return normalized


def _bounded_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = str(env.get(name, default) or default).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise IwencaiConfigurationError(
            "invalid_configuration",
            f"{name} 必须是整数",
        ) from exc
    if value < minimum or value > maximum:
        raise IwencaiConfigurationError(
            "invalid_configuration",
            f"{name} 必须在 {minimum} 到 {maximum} 之间",
        )
    return value


@dataclass(frozen=True)
class IwencaiConfig:
    """Explicit runtime settings for the iWencai gateway."""

    enabled: bool
    base_url: str
    api_key: str
    timeout_seconds: int = 20
    max_retries: int = 1
    max_concurrency: int = 2

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "IwencaiConfig":
        values = os.environ if env is None else env
        enabled = str(values.get("IWENCAI_ENABLED", "0") or "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        try:
            base_url = normalize_base_url(str(values.get("IWENCAI_BASE_URL") or DEFAULT_BASE_URL))
        except ValueError as exc:
            raise IwencaiConfigurationError("invalid_base_url", str(exc)) from exc
        return cls(
            enabled=enabled,
            base_url=base_url,
            api_key=str(values.get("IWENCAI_API_KEY") or "").strip(),
            timeout_seconds=_bounded_int(values, "IWENCAI_TIMEOUT_SECONDS", 20, 2, 60),
            max_retries=_bounded_int(values, "IWENCAI_MAX_RETRIES", 1, 0, 2),
            max_concurrency=_bounded_int(values, "IWENCAI_MAX_CONCURRENCY", 2, 1, 4),
        )

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith(QUERY_PATH):
            return self.base_url
        return self.base_url + QUERY_PATH


_SEMAPHORES: dict[int, threading.BoundedSemaphore] = {}
_SEMAPHORE_LOCK = threading.Lock()


def _shared_semaphore(limit: int) -> threading.BoundedSemaphore:
    with _SEMAPHORE_LOCK:
        return _SEMAPHORES.setdefault(limit, threading.BoundedSemaphore(limit))


class IwencaiClient:
    """Call the natural-language query gateway with bounded I/O and retries."""

    def __init__(
        self,
        config: IwencaiConfig,
        *,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        semaphore: threading.BoundedSemaphore | None = None,
    ):
        self.config = config
        self._opener = opener or urllib.request.urlopen
        self._sleep = sleep
        self._semaphore = semaphore or _shared_semaphore(config.max_concurrency)

    def query(
        self,
        query: str,
        *,
        page: int = 1,
        limit: int = 10,
        is_cache: bool = True,
        expand_index: bool = True,
    ) -> dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            raise ValueError("query 不能为空")
        if len(query) > MAX_QUERY_CHARS:
            raise ValueError(f"query 不能超过 {MAX_QUERY_CHARS} 个字符")
        if not 1 <= int(page) <= 1000:
            raise ValueError("page 必须在 1 到 1000 之间")
        if not 1 <= int(limit) <= 100:
            raise ValueError("limit 必须在 1 到 100 之间")
        if not self.config.api_key:
            raise IwencaiConfigurationError(
                "api_key_missing",
                "IWENCAI_API_KEY 未配置",
            )

        payload = {
            "query": query,
            "page": str(page),
            "limit": str(limit),
            "is_cache": "1" if is_cache else "0",
            "expand_index": "true" if expand_index else "false",
        }
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error: IwencaiRequestError | None = None
        for attempt in range(self.config.max_retries + 1):
            trace_id = secrets.token_hex(32)
            request = urllib.request.Request(
                self.config.endpoint,
                data=encoded,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "NiuOne/iwencai-client",
                    "X-Claw-Call-Type": "normal" if attempt == 0 else "retry",
                    "X-Claw-Skill-Id": SKILL_ID,
                    "X-Claw-Skill-Version": SKILL_VERSION,
                    "X-Claw-Plugin-Id": "none",
                    "X-Claw-Plugin-Version": "none",
                    "X-Claw-Trace-Id": trace_id,
                },
                method="POST",
            )
            acquired = self._semaphore.acquire(timeout=self.config.timeout_seconds)
            if not acquired:
                raise IwencaiRequestError("concurrency_timeout", "问财请求并发等待超时")
            try:
                try:
                    with self._opener(request, timeout=self.config.timeout_seconds) as response:
                        body = response.read(MAX_RESPONSE_BYTES + 1)
                except urllib.error.HTTPError as exc:
                    retryable = exc.code == 429 or 500 <= exc.code < 600
                    last_error = IwencaiRequestError(
                        "http_error",
                        f"问财网关返回 HTTP {exc.code}",
                        status_code=exc.code,
                    )
                    if not retryable or attempt >= self.config.max_retries:
                        raise last_error from exc
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    last_error = IwencaiRequestError(
                        "network_error",
                        f"问财网络请求失败: {type(exc).__name__}",
                    )
                    if attempt >= self.config.max_retries:
                        raise last_error from exc
                else:
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise IwencaiResponseError(
                            "response_too_large",
                            "问财响应超过大小上限",
                        )
                    try:
                        parsed = json.loads(body.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise IwencaiResponseError(
                            "invalid_json",
                            "问财响应不是有效 JSON",
                        ) from exc
                    if not isinstance(parsed, dict):
                        raise IwencaiResponseError(
                            "invalid_response",
                            "问财响应必须是 JSON 对象",
                        )
                    datas = parsed.get("datas")
                    if not isinstance(datas, list):
                        raise IwencaiResponseError(
                            "upstream_error",
                            "问财响应缺少 datas 列表",
                        )
                    result = dict(parsed)
                    result.setdefault("trace_id", trace_id)
                    return result
            finally:
                self._semaphore.release()

            if attempt < self.config.max_retries:
                self._sleep(min(0.25 * (2**attempt), 1.0))

        if last_error is not None:  # pragma: no cover - defensive guard
            raise last_error
        raise IwencaiRequestError("request_failed", "问财请求失败")
