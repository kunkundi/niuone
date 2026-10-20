"""Cross-process model admission and minute-scale upstream backoff.

Only hashes, lease IDs and timing metadata are persisted. Portfolio locks and
market-data requests never use this coordinator.

Admission grants up to ``MAX_CONCURRENT_REQUESTS`` in-flight leases per scope
instead of a single one, so independent consumers (news precheck, buy/sell
decisions, report summaries) no longer serialise behind each other. The scope
still caps total in-flight load against the provider credential budget.
"""
from __future__ import annotations

import hashlib
import inspect
import math
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timezone
from email.utils import parsedate_to_datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

from .paths import get_dashboard_home

MIN_REQUEST_INTERVAL = 2.0
MAX_QUEUE_SECONDS = 5.0
DEFAULT_RATE_BACKOFF = 60.0
MAX_RATE_BACKOFF = 300.0
MAX_CONCURRENT_REQUESTS = 3
# Spacing exists to keep a burst of consumers off the provider's rate limit.
# It was calibrated for one in-flight request at a time, so it stays there and
# shrinks as the shared concurrency cap grows; otherwise raising the cap alone
# leaves total throughput pinned at one request per interval.
SPACING_REFERENCE_CONCURRENCY = 1
MIN_SPACING_SECONDS = 0.2
_deadline: ContextVar[float | None] = ContextVar("model_deadline", default=None)


def _positive_env_int(name: str, default: int, *, low: int = 1, high: int = 64) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _positive_env_float(name: str, default: float, *, low: float, high: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(low, min(high, value))


def max_concurrent_requests() -> int:
    """Read the shared in-flight cap once per call so tests can patch env."""

    return _positive_env_int(
        "DASHBOARD_MODEL_MAX_CONCURRENCY",
        MAX_CONCURRENT_REQUESTS,
        low=1,
        high=32,
    )


def request_spacing_seconds(max_concurrency: int | None = None) -> float:
    """Spacing between admissions for one credential.

    ``DASHBOARD_MODEL_REQUEST_SPACING_SECONDS`` overrides the derived value so a
    provider with a documented rate limit can be matched exactly.
    """

    explicit = os.environ.get("DASHBOARD_MODEL_REQUEST_SPACING_SECONDS")
    if explicit is not None and str(explicit).strip():
        return _positive_env_float(
            "DASHBOARD_MODEL_REQUEST_SPACING_SECONDS",
            MIN_REQUEST_INTERVAL,
            low=0.0,
            high=MIN_REQUEST_INTERVAL,
        )
    cap = max(1, max_concurrency if max_concurrency is not None else max_concurrent_requests())
    scaled = MIN_REQUEST_INTERVAL * SPACING_REFERENCE_CONCURRENCY / cap
    return max(MIN_SPACING_SECONDS, min(MIN_REQUEST_INTERVAL, scaled))


class ModelRequestExpired(TimeoutError):
    """The model result can no longer be used within this request budget."""


class ModelAdmissionError(RuntimeError):
    """A model request was not sent; a later cycle may try again."""

    def __init__(self, reason: str, retry_after: float = 0.0):
        self.reason = reason
        self.retry_after = max(0.0, retry_after)
        super().__init__(f"model_request_{reason}; retry_after={self.retry_after:.1f}s")


def remaining_model_seconds(timeout: float) -> float:
    deadline = _deadline.get()
    remaining = min(float(timeout), deadline - time.monotonic()) if deadline is not None else float(timeout)
    if not math.isfinite(remaining) or remaining <= 0:
        raise ModelRequestExpired("model_request_deadline_exceeded")
    return remaining


def budgeted_model_call(function: Callable[..., Any]) -> Callable[..., Any]:
    """Share one deadline across transport and malformed-response retries."""
    signature = inspect.signature(function)
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        timeout = float(bound.arguments.get("timeout", 60))
        with model_request_budget(timeout):
            result = function(*args, **kwargs)
            remaining_model_seconds(timeout)
            return result
    return wrapped


@contextmanager
def model_request_budget(timeout: float) -> Iterator[None]:
    remaining_model_seconds(timeout)
    own_deadline = time.monotonic() + timeout
    inherited = _deadline.get()
    token = _deadline.set(min(own_deadline, inherited) if inherited is not None else own_deadline)
    try:
        yield
        remaining_model_seconds(timeout)
    finally:
        _deadline.reset(token)


def retry_after_seconds(value: str | None, now: float) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(str(value))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = date.timestamp() - now
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def request_scope(endpoint: str, api_key: str) -> str:
    url = urlsplit(endpoint)
    # Chat/Responses, models, and processes share the provider's credential
    # budget. Neither the credential nor endpoint appears in the database.
    identity = f"{url.scheme.lower()}://{url.netloc.lower()}\0{api_key}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class ModelRequestCoordinator:
    # ``lease``/``lease_until`` remain on the scope row for readers that predate
    # the multi-lease table. They mirror the newest active lease so an older
    # process still observes a busy scope, and are cleared on release.
    _SCHEMA = (
        "CREATE TABLE IF NOT EXISTS model_admission (scope TEXT PRIMARY KEY, next_start REAL NOT NULL DEFAULT 0, cooldown REAL NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0, lease TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS model_admission_lease (scope TEXT NOT NULL, token TEXT PRIMARY KEY, expires_at REAL NOT NULL)",
        "CREATE INDEX IF NOT EXISTS model_admission_lease_scope ON model_admission_lease(scope, expires_at)",
    )

    def __init__(self, path: Path, *, interval: float | None = None,
                 clock: Callable[[], float] = time.time,
                 max_concurrency: int | None = None):
        self.path = path
        self.clock = clock
        self.max_concurrency = (
            max_concurrency if max_concurrency is not None else max_concurrent_requests()
        )
        # An explicit interval keeps its historical meaning for callers and tests
        # that pin it; the default follows the configured concurrency instead.
        self.interval = (
            request_spacing_seconds(self.max_concurrency)
            if interval is None
            else max(0.0, float(interval))
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=0.5)
            for statement in self._SCHEMA:
                conn.execute(statement)
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except (OSError, sqlite3.Error) as exc:
            if conn is not None:
                conn.rollback()
            raise ModelAdmissionError("coordination_unavailable") from exc
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _expire_leases(conn: sqlite3.Connection, scope: str, now: float) -> list[tuple[float, str]]:
        """Drop expired rows and return the remaining leases, oldest deadline first."""

        conn.execute("DELETE FROM model_admission_lease WHERE scope=? AND expires_at<=?", (scope, now))
        return conn.execute(
            "SELECT expires_at, token FROM model_admission_lease WHERE scope=? ORDER BY expires_at ASC",
            (scope,),
        ).fetchall()

    @staticmethod
    def _sync_legacy_columns(conn: sqlite3.Connection, scope: str,
                             active: list[tuple[float, str]]) -> None:
        if not active:
            conn.execute(
                "UPDATE model_admission SET lease='',lease_until=0 WHERE scope=?", (scope,)
            )
            return
        expires_at, token = active[-1]
        conn.execute(
            "UPDATE model_admission SET lease=?,lease_until=? WHERE scope=?",
            (token, expires_at, scope),
        )

    def acquire(self, scope: str, *, duration: float) -> str:
        if not math.isfinite(duration) or duration <= 0:
            raise ModelRequestExpired("model_request_deadline_exceeded")
        now = self.clock()
        with self._transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO model_admission(scope) VALUES (?)", (scope,))
            next_start, cooldown, failures, lease, lease_until = conn.execute(
                "SELECT next_start,cooldown,failures,lease,lease_until FROM model_admission WHERE scope=?", (scope,)
            ).fetchone()
            if cooldown > now:
                raise ModelAdmissionError("rate_limited", cooldown - now)
            active = self._expire_leases(conn, scope, now)
            # A pre-multi-lease process holds only the mirrored columns, so treat
            # it as a full scope: its in-flight request is invisible to the lease
            # table and must not be overlapped.
            if len(active) >= self.max_concurrency:
                # Report the wait for the lease that frees first, not the newest.
                raise ModelAdmissionError("busy", max(0.0, active[0][0] - now))
            if not active and lease and lease_until > now:
                raise ModelAdmissionError("busy", lease_until - now)
            if next_start > now:
                raise ModelAdmissionError("spacing", next_start - now)
            token = uuid.uuid4().hex
            conn.execute("UPDATE model_admission SET next_start=? WHERE scope=?",
                         (now + self.interval, scope))
            conn.execute("INSERT INTO model_admission_lease(scope, token, expires_at) VALUES (?,?,?)",
                         (scope, token, now + duration + 5))
            active = self._expire_leases(conn, scope, now)
            self._sync_legacy_columns(conn, scope, active)
            return token

    def release(self, scope: str, lease: str, *, success: bool = False) -> None:
        with self._transaction() as conn:
            removed = conn.execute(
                "DELETE FROM model_admission_lease WHERE scope=? AND token=?", (scope, lease)
            ).rowcount > 0
            if not removed:
                # A stale owner must not touch the scope: the mirrored columns
                # may belong to a pre-multi-lease process or a newer lease.
                return
            # Only the holder of a live lease may clear the backoff streak.
            if success:
                conn.execute("UPDATE model_admission SET failures=0 WHERE scope=?", (scope,))
            active = self._expire_leases(conn, scope, self.clock())
            self._sync_legacy_columns(conn, scope, active)

    def rate_limited(self, scope: str, lease: str, retry_after: str | None) -> float:
        now = self.clock()
        with self._transaction() as conn:
            row = conn.execute("SELECT failures FROM model_admission WHERE scope=?", (scope,)).fetchone()
            if row is None:
                return DEFAULT_RATE_BACKOFF
            failures = min(10, int(row[0]) + 1)
            delay = min(MAX_RATE_BACKOFF, DEFAULT_RATE_BACKOFF * 2 ** (failures - 1))
            requested = retry_after_seconds(retry_after, now)
            # A long Retry-After is a durable do-not-send timestamp, never a
            # blocking sleep. Do not retry early by truncating the header.
            delay = max(delay, requested or 0.0)
            conn.execute("UPDATE model_admission SET cooldown=MAX(cooldown,?),failures=? WHERE scope=?",
                         (now + delay, failures, scope))
            return delay


def shared_model_coordinator() -> ModelRequestCoordinator:
    root = Path(__file__).resolve().parents[2]
    return ModelRequestCoordinator(get_dashboard_home(root) / "model_admission.sqlite3")
