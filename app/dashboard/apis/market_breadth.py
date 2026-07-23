"""Chart-ready current-day history for A-share market breadth."""
from __future__ import annotations

from datetime import datetime
import math
from typing import Any


SERIES_KEYS = (
    "limit_down",
    "limit_up",
    "broken_limit",
    "red",
    "green",
)
TURNOVER_SERIES_KEYS = (
    "estimated_turnover_yi",
    "actual_turnover_yi",
)
TURNOVER_METADATA_STRING_KEYS = (
    "turnover_actual_source",
    "turnover_actual_source_url",
    "turnover_estimate_model",
    "turnover_estimate_model_label",
    "turnover_estimate_source",
    "turnover_estimate_source_url",
    "turnover_profile_start",
    "turnover_profile_end",
    "turnover_estimate_warning",
)
TURNOVER_COMPARISON_KEYS = (
    "previous_turnover_yi",
    "turnover_increment_yi",
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
    turnover_keys_present = {key: key in source for key in TURNOVER_SERIES_KEYS}
    if turnover_keys_present["estimated_turnover_yi"] and not turnover_keys_present[
        "actual_turnover_yi"
    ]:
        return None
    if any(turnover_keys_present.values()):
        for key in TURNOVER_SERIES_KEYS:
            if not turnover_keys_present[key]:
                continue
            try:
                value = float(source.get(key))
            except (TypeError, ValueError):
                return None
            if not math.isfinite(value) or value < 0:
                return None
            sample[key] = round(value, 2)
    comparison_keys_present = [key in source for key in TURNOVER_COMPARISON_KEYS]
    if any(comparison_keys_present):
        if (
            not all(comparison_keys_present)
            or not turnover_keys_present["estimated_turnover_yi"]
        ):
            return None
        try:
            previous_turnover = float(source.get("previous_turnover_yi"))
            turnover_increment = float(source.get("turnover_increment_yi"))
        except (TypeError, ValueError):
            return None
        comparison_date = str(source.get("turnover_comparison_date") or "").strip()
        comparison_moment = _sample_time(f"{comparison_date} 00:00:00")
        if (
            not math.isfinite(previous_turnover)
            or previous_turnover <= 0
            or not math.isfinite(turnover_increment)
            or comparison_moment is None
            or comparison_date >= generated_at[:10]
            or abs(
                sample["estimated_turnover_yi"]
                - previous_turnover
                - turnover_increment
            ) > 0.02
        ):
            return None
        sample.update({
            "previous_turnover_yi": round(previous_turnover, 2),
            "turnover_increment_yi": round(turnover_increment, 2),
            "turnover_comparison_date": comparison_date,
        })
        for key in ("turnover_comparison_source", "turnover_comparison_source_url"):
            value = str(source.get(key) or "").strip()
            if value:
                sample[key] = value
    for key in TURNOVER_METADATA_STRING_KEYS:
        value = str(source.get(key) or "").strip()
        if value:
            sample[key] = value
    if "turnover_profile_days" in source:
        try:
            profile_days = int(source.get("turnover_profile_days"))
        except (TypeError, ValueError):
            return None
        if profile_days < 1 or profile_days > 20:
            return None
        sample["turnover_profile_days"] = profile_days
    if "turnover_profile_interval_minutes" in source:
        try:
            profile_interval = int(source.get("turnover_profile_interval_minutes"))
        except (TypeError, ValueError):
            return None
        if profile_interval < 1 or profile_interval > 30:
            return None
        sample["turnover_profile_interval_minutes"] = profile_interval
    if "turnover_amount_count" in source:
        try:
            turnover_amount_count = int(source.get("turnover_amount_count"))
        except (TypeError, ValueError):
            return None
        if turnover_amount_count < 0 or turnover_amount_count > sample["quote_count"]:
            return None
        sample["turnover_amount_count"] = turnover_amount_count
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
        "schema_version": 3,
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
    comparison_reference = current if current and "previous_turnover_yi" in current else None
    if comparison_reference is None:
        for raw in reversed(history_samples or []):
            compact_reference = compact_market_breadth_sample(
                raw if isinstance(raw, dict) else None
            )
            if (
                compact_reference is not None
                and (not current_day or compact_reference["generated_at"][:10] == current_day)
                and "previous_turnover_yi" in compact_reference
            ):
                comparison_reference = compact_reference
                break
    by_time: dict[str, dict[str, Any]] = {}
    for raw in [*(history_samples or []), source]:
        candidate = dict(raw) if isinstance(raw, dict) else None
        if (
            candidate is not None
            and comparison_reference is not None
            and "estimated_turnover_yi" in candidate
            and not any(key in candidate for key in TURNOVER_COMPARISON_KEYS)
        ):
            previous = comparison_reference["previous_turnover_yi"]
            try:
                increment = round(float(candidate["estimated_turnover_yi"]) - previous, 2)
            except (TypeError, ValueError):
                pass
            else:
                candidate.update({
                    "previous_turnover_yi": previous,
                    "turnover_increment_yi": increment,
                    "turnover_comparison_date": comparison_reference[
                        "turnover_comparison_date"
                    ],
                    "turnover_comparison_source": comparison_reference.get(
                        "turnover_comparison_source",
                        "",
                    ),
                    "turnover_comparison_source_url": comparison_reference.get(
                        "turnover_comparison_source_url",
                        "",
                    ),
                })
        compact = compact_market_breadth_sample(candidate)
        if (
            compact is None
            or not is_market_breadth_session_timestamp(compact["generated_at"])
            or (current_day and compact["generated_at"][:10] != current_day)
        ):
            continue
        by_time[compact["generated_at"]] = compact
    timeline = [by_time[key] for key in sorted(by_time)][-DEFAULT_HISTORY_LIMIT:]
    public_current = (
        by_time.get(current["generated_at"], current)
        if current is not None
        else None
    )
    result: dict[str, Any] = {
        "schema_version": 3,
        "available": bool(timeline),
        "generated_at": str(source.get("generated_at") or ""),
        "source": str(source.get("source") or "腾讯证券沪深A股实时行情"),
        "source_url": str(source.get("source_url") or "https://gu.qq.com/"),
        "universe": str(source.get("universe") or "沪深A股"),
        "latest": public_current or {},
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
    if public_current and "previous_turnover_yi" in public_current:
        result["turnover_comparison"] = {
            "date": public_current["turnover_comparison_date"],
            "previous_turnover_yi": public_current["previous_turnover_yi"],
            "source": str(public_current.get("turnover_comparison_source") or ""),
            "source_url": str(public_current.get("turnover_comparison_source_url") or ""),
        }
    if public_current and "actual_turnover_yi" in public_current:
        result["turnover_actual"] = {
            "source": str(public_current.get("turnover_actual_source") or ""),
            "source_url": str(public_current.get("turnover_actual_source_url") or ""),
        }
    if public_current and "estimated_turnover_yi" in public_current:
        result["turnover_estimate"] = {
            "model": str(public_current.get("turnover_estimate_model") or ""),
            "model_label": str(
                public_current.get("turnover_estimate_model_label") or ""
            ),
            "source": str(public_current.get("turnover_estimate_source") or ""),
            "source_url": str(
                public_current.get("turnover_estimate_source_url") or ""
            ),
            "profile_days": int(public_current.get("turnover_profile_days") or 0),
            "profile_start": str(public_current.get("turnover_profile_start") or ""),
            "profile_end": str(public_current.get("turnover_profile_end") or ""),
            "interval_minutes": int(
                public_current.get("turnover_profile_interval_minutes") or 0
            ),
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
