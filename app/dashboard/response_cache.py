"""Thread-safe JSON response cache used by Dashboard API services."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable


PayloadProducer = Callable[[], dict[str, Any]]
CacheEntry = dict[str, Any]
CacheStore = dict[str, CacheEntry]
KeyLocks = dict[str, threading.Lock]
Generations = dict[str, int]


def store_payload(
    cache_key: str,
    payload: bytes,
    generation: int,
    *,
    entries: CacheStore,
    entries_lock: Any,
    key_locks: KeyLocks,
    generations: Generations,
    max_entries: int,
    now: Callable[[], float] = time.time,
) -> bool:
    """Store a payload unless its producer predates an invalidation."""

    with entries_lock:
        if generations.get(cache_key, 0) != generation:
            return False
        entries[cache_key] = {"ts": now(), "payload": payload}
        if len(entries) > max_entries:
            oldest = sorted(
                entries.items(),
                key=lambda item: float(item[1].get("ts") or 0),
            )
            overflow = max(1, len(entries) - max_entries)
            for old_key, _ in oldest[:overflow]:
                entries.pop(old_key, None)
                old_lock = key_locks.get(old_key)
                if old_lock is None or not old_lock.locked():
                    key_locks.pop(old_key, None)
        return True


def refresh_payload(
    cache_key: str,
    producer: PayloadProducer,
    generation: int,
    key_lock: threading.Lock,
    *,
    store: Callable[[str, bytes, int], bool],
    warn: Callable[[str], None],
) -> None:
    """Refresh one stale entry and always release its claimed key lock."""

    try:
        result = producer()
        payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
        store(cache_key, payload, generation)
    except Exception as exc:
        warn(
            f"dashboard cache refresh failed for {cache_key}: "
            f"{type(exc).__name__}: {exc}"
        )
    finally:
        key_lock.release()


def get_json(
    cache_key: str,
    ttl: int,
    producer: PayloadProducer,
    *,
    entries: CacheStore,
    entries_lock: Any,
    key_locks: KeyLocks,
    generations: Generations,
    stale_while_refresh_seconds: int,
    store: Callable[[str, bytes, int], bool],
    refresh: Callable[[str, PayloadProducer, int, threading.Lock], None],
    now: Callable[[], float] = time.time,
) -> tuple[bytes, bool]:
    """Return fresh JSON or one stale value while a single refresh runs."""

    current_time = now()
    with entries_lock:
        cached = entries.get(cache_key)
        cache_age = current_time - float(cached.get("ts") or 0) if cached else None
        if cached and cache_age is not None and cache_age < ttl:
            return cached["payload"], True
        key_lock = key_locks.setdefault(cache_key, threading.Lock())
        generation = generations.get(cache_key, 0)

    if (
        cached
        and cache_age is not None
        and cache_age < ttl + stale_while_refresh_seconds
    ):
        if key_lock.acquire(blocking=False):
            try:
                threading.Thread(
                    target=refresh,
                    args=(cache_key, producer, generation, key_lock),
                    name=f"dashboard-cache-{cache_key[:32]}",
                    daemon=True,
                ).start()
            except Exception:
                key_lock.release()
                raise
        return cached["payload"], True

    with key_lock:
        current_time = now()
        with entries_lock:
            cached = entries.get(cache_key)
            if cached and current_time - float(cached.get("ts") or 0) < ttl:
                return cached["payload"], True
            generation = generations.get(cache_key, 0)
        result = producer()
        payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
        store(cache_key, payload, generation)
        return payload, False


def seed_from_json_file(
    cache_key: str,
    path: Path,
    ttl: int,
    *,
    entries: CacheStore,
    entries_lock: Any,
    transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    now: Callable[[], float] = time.time,
) -> bool:
    """Seed a cold entry just beyond its TTL for stale-while-refresh use."""

    with entries_lock:
        if cache_key in entries:
            return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        data = dict(data)
        if transform is not None:
            data = transform(data)
        if not isinstance(data, dict):
            return False
        data["stale_cache"] = True
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    except (OSError, ValueError, TypeError):
        return False

    with entries_lock:
        if cache_key in entries:
            return False
        entries[cache_key] = {
            "ts": now() - max(0, ttl) - 0.001,
            "payload": payload,
        }
    return True


def invalidate(
    cache_keys: tuple[str, ...],
    *,
    entries: CacheStore,
    entries_lock: Any,
    generations: Generations,
) -> None:
    with entries_lock:
        for cache_key in cache_keys:
            entries.pop(cache_key, None)
            generations[cache_key] = generations.get(cache_key, 0) + 1


def invalidate_prefix(
    prefix: str,
    *,
    entries: CacheStore,
    entries_lock: Any,
    generations: Generations,
) -> None:
    with entries_lock:
        cache_keys = [key for key in entries if key.startswith(prefix)]
        for cache_key in cache_keys:
            entries.pop(cache_key, None)
            generations[cache_key] = generations.get(cache_key, 0) + 1


def decode_json_data(
    payload: bytes,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    try:
        data = json.loads(payload.decode("utf-8", "ignore"))
        return data if isinstance(data, dict) else dict(fallback)
    except Exception as exc:
        return {**fallback, "error": str(exc)}
