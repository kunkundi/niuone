"""Cache orchestration shared by dashboard data APIs."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


Payload = dict[str, Any]
CacheReader = Callable[[Path, int | float | None], Payload | None]
CacheWriter = Callable[[Path, Payload], None]


def load_cached_payload(
    cache_path: Path,
    ttl_seconds: int | float,
    *,
    compute: Callable[[], Payload],
    empty: Payload,
    read_cache: CacheReader,
    write_cache: CacheWriter,
    force_refresh: bool = False,
) -> Payload:
    """Load fresh data, recompute it, or annotate a stale fallback."""
    if not force_refresh:
        cached = read_cache(cache_path, ttl_seconds)
        if cached is not None:
            return cached
    try:
        data = compute()
        write_cache(cache_path, data)
        return data
    except Exception as exc:
        stale = read_cache(cache_path, None)
        if stale is not None:
            stale["stale_cache"] = True
            stale["error"] = str(exc)
            return stale
        return {**empty, "error": str(exc)}
