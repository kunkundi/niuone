"""Small atomic JSON cache helpers for data producers."""
from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any


_VERSIONED_JSON_CACHE_MAX_ENTRIES = 64
_VERSIONED_JSON_CACHE: OrderedDict[
    str,
    tuple[tuple[int, int, int, int], dict[str, Any] | None],
] = OrderedDict()
_VERSIONED_JSON_CACHE_LOCK = threading.RLock()
_VERSIONED_JSON_PATH_LOCKS: dict[str, threading.Lock] = {}


def _file_version(stat: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_size),
        int(stat.st_mtime_ns),
    )


def read_json_cache(path: Path, ttl_seconds: int | float | None = None) -> dict[str, Any] | None:
    """Return cached JSON when it exists, parses, and is fresh enough."""
    try:
        stat = path.stat()
        if ttl_seconds is not None and time.time() - stat.st_mtime >= ttl_seconds:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def read_versioned_json_cache(path: Path) -> dict[str, Any] | None:
    """Parse a small JSON object once per atomic file version.

    Callers should only use this for bounded read-model files.  The returned
    object is shared until the file identity, size, or nanosecond mtime changes
    and therefore must be treated as read-only.
    """
    path = Path(path)
    key = str(path.resolve(strict=False))
    try:
        version = _file_version(path.stat())
    except OSError:
        return None

    with _VERSIONED_JSON_CACHE_LOCK:
        cached = _VERSIONED_JSON_CACHE.get(key)
        if cached is not None and cached[0] == version:
            _VERSIONED_JSON_CACHE.move_to_end(key)
            return cached[1]
        path_lock = _VERSIONED_JSON_PATH_LOCKS.setdefault(key, threading.Lock())

    with path_lock:
        try:
            before = _file_version(path.stat())
        except OSError:
            return None
        with _VERSIONED_JSON_CACHE_LOCK:
            cached = _VERSIONED_JSON_CACHE.get(key)
            if cached is not None and cached[0] == before:
                _VERSIONED_JSON_CACHE.move_to_end(key)
                return cached[1]
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            value = parsed if isinstance(parsed, dict) else None
        except (OSError, json.JSONDecodeError, TypeError):
            value = None
        try:
            after = _file_version(path.stat())
        except OSError:
            return None
        if before != after:
            return value
        with _VERSIONED_JSON_CACHE_LOCK:
            _VERSIONED_JSON_CACHE[key] = (after, value)
            _VERSIONED_JSON_CACHE.move_to_end(key)
            while len(_VERSIONED_JSON_CACHE) > _VERSIONED_JSON_CACHE_MAX_ENTRIES:
                _VERSIONED_JSON_CACHE.popitem(last=False)
        return value


def clear_versioned_json_cache(path: Path | None = None) -> None:
    """Clear parsed read models, primarily for tests and explicit invalidation."""
    with _VERSIONED_JSON_CACHE_LOCK:
        if path is None:
            _VERSIONED_JSON_CACHE.clear()
            _VERSIONED_JSON_PATH_LOCKS.clear()
            return
        key = str(Path(path).resolve(strict=False))
        _VERSIONED_JSON_CACHE.pop(key, None)
        _VERSIONED_JSON_PATH_LOCKS.pop(key, None)


def write_json_cache(path: Path, data: dict[str, Any]) -> None:
    """Atomically write a JSON cache file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        # Large research snapshots must not coexist with a second complete
        # Unicode serialization and its UTF-8 byte copy in memory.
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.write("\n")
        tmp.replace(path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
