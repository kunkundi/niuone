"""Chart-ready current-day history for A-share market breadth."""
from __future__ import annotations

from datetime import datetime
from typing import Any


SERIES_KEYS = (
    "limit_down",
    "limit_up",
    "broken_limit",
    "red",
    "green",
)
DEFAULT_HISTORY_LIMIT = 300
DEFAULT_SAMPLE_INTERVAL_SECONDS = 60
SAMPLING_WINDOWS = ((9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60))


def _sample_time(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value or "").strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def is_market_breadth_session_timestamp(value: Any) -> bool:
    moment = value if isinstance(value, datetime) else _sample_time(value)
    if moment is None:
        return False
    minute = moment.hour * 60 + moment.minute
    return any(start <= minute <= end for start, end in SAMPLING_WINDOWS)


def compact_market_breadth_sample(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    source = payload if isinstance(payload, dict) else {}
    generated_at = str(source.get("generated_at") or "").strip()
    if _sample_time(generated_at) is None:
        return None
    sample: dict[str, Any] = {"generated_at": generated_at}
    for key in (*SERIES_KEYS, "flat", "quote_count", "limit_price_count"):
        try:
            value = int(source.get(key))
        except (TypeError, ValueError):
            return None
        if value < 0:
            return None
        sample[key] = value
    if sample["red"] + sample["green"] + sample["flat"] != sample["quote_count"]:
        return None
    if sample["limit_price_count"] > sample["quote_count"]:
        return None
    for key in ("source", "source_url", "universe"):
        value = str(source.get(key) or "").strip()
        if value:
            sample[key] = value
    return sample


def append_market_breadth_sample(
    history: dict[str, Any] | None,
    payload: dict[str, Any] | None,
    *,
    max_points: int = DEFAULT_HISTORY_LIMIT,
    interval_seconds: int = DEFAULT_SAMPLE_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Append one valid observation and retain only the current trading day."""

    sample = compact_market_breadth_sample(payload)
    existing = history if isinstance(history, dict) else {}
    if sample is None or not is_market_breadth_session_timestamp(sample["generated_at"]):
        return existing
    sample_day = sample["generated_at"][:10]
    by_time: dict[str, dict[str, Any]] = {}
    for raw in existing.get("samples") or []:
        compact = compact_market_breadth_sample(raw if isinstance(raw, dict) else None)
        if (
            compact is None
            or compact["generated_at"][:10] != sample_day
            or not is_market_breadth_session_timestamp(compact["generated_at"])
        ):
            continue
        by_time[compact["generated_at"]] = compact
    by_time[sample["generated_at"]] = sample
    limit = max(2, min(600, int(max_points)))
    samples = [by_time[key] for key in sorted(by_time)][-limit:]
    return {
        "schema_version": 1,
        "date": sample_day,
        "interval_seconds": max(60, min(600, int(interval_seconds))),
        "samples": samples,
    }


def build_market_breadth_payload(
    latest: dict[str, Any] | None,
    *,
    history_samples: list[dict[str, Any]] | None = None,
    interval_seconds: int = DEFAULT_SAMPLE_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Project a latest snapshot and persisted samples to the public chart model."""

    source = latest if isinstance(latest, dict) else {}
    current = compact_market_breadth_sample(source)
    current_day = current["generated_at"][:10] if current else ""
    by_time: dict[str, dict[str, Any]] = {}
    for raw in [*(history_samples or []), source]:
        compact = compact_market_breadth_sample(raw if isinstance(raw, dict) else None)
        if (
            compact is None
            or not is_market_breadth_session_timestamp(compact["generated_at"])
            or (current_day and compact["generated_at"][:10] != current_day)
        ):
            continue
        by_time[compact["generated_at"]] = compact
    timeline = [by_time[key] for key in sorted(by_time)][-DEFAULT_HISTORY_LIMIT:]
    result: dict[str, Any] = {
        "schema_version": 1,
        "available": bool(timeline),
        "generated_at": str(source.get("generated_at") or ""),
        "source": str(source.get("source") or "腾讯证券沪深A股实时行情"),
        "source_url": str(source.get("source_url") or "https://gu.qq.com/"),
        "universe": str(source.get("universe") or "沪深A股"),
        "latest": current or {},
        "timeline": timeline,
        "sampling": {
            "interval_seconds": max(60, min(600, int(interval_seconds))),
            "timezone": "Asia/Shanghai",
            "windows": [
                {"start": "09:30", "end": "11:30"},
                {"start": "13:00", "end": "15:00"},
            ],
            "point_count": len(timeline),
            "historical_backfill": {"available": False},
        },
    }
    for key in ("stale_cache", "error", "warning"):
        if key in source:
            result[key] = source[key]
    return result


__all__ = [
    "append_market_breadth_sample",
    "build_market_breadth_payload",
    "compact_market_breadth_sample",
    "is_market_breadth_session_timestamp",
]
