"""Chart-ready current-day history for A-share market breadth."""
from __future__ import annotations

from bisect import bisect_left
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
TURNOVER_ESTIMATE_OUTPUT_KEYS = (
    "estimated_turnover_yi",
    "previous_turnover_yi",
    "turnover_increment_yi",
    "turnover_comparison_date",
    "turnover_comparison_source",
    "turnover_comparison_source_url",
    "turnover_estimate_model",
    "turnover_estimate_model_label",
    "turnover_estimate_source",
    "turnover_estimate_source_url",
    "turnover_profile_days",
    "turnover_profile_start",
    "turnover_profile_end",
    "turnover_profile_interval_minutes",
    "turnover_estimate_warning",
)
DEFAULT_HISTORY_LIMIT = 300
DEFAULT_SAMPLE_INTERVAL_SECONDS = 60
HISTORY_SCHEMA_VERSION = 4
PREVIOUS_TURNOVER_MATCH_TOLERANCE_SECONDS = 90
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


def _session_progress_seconds(value: Any) -> int | None:
    moment = value if isinstance(value, datetime) else _sample_time(value)
    if moment is None:
        return None
    seconds = moment.hour * 3600 + moment.minute * 60 + moment.second
    morning_start = 9 * 3600 + 30 * 60
    morning_end = 11 * 3600 + 30 * 60
    afternoon_start = 13 * 3600
    afternoon_end = 15 * 3600
    if morning_start <= seconds <= morning_end:
        return seconds - morning_start
    if afternoon_start <= seconds <= afternoon_end:
        return 2 * 3600 + seconds - afternoon_start
    return None


def _without_incompatible_turnover_estimate(
    payload: dict[str, Any],
    active_model: str,
) -> dict[str, Any]:
    """Keep real observations while hiding estimates from a different model."""

    result = dict(payload)
    if not active_model or "estimated_turnover_yi" not in result:
        return result
    sample_model = str(result.get("turnover_estimate_model") or "").strip()
    if sample_model == active_model:
        return result
    for key in TURNOVER_ESTIMATE_OUTPUT_KEYS:
        result.pop(key, None)
    return result


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


def _compact_actual_turnover_sample(
    payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    source = payload if isinstance(payload, dict) else {}
    generated_at = str(source.get("generated_at") or "").strip()
    if not is_market_breadth_session_timestamp(generated_at):
        return None
    try:
        actual_turnover = float(source.get("actual_turnover_yi"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(actual_turnover) or actual_turnover < 0:
        return None
    sample: dict[str, Any] = {
        "generated_at": generated_at,
        "actual_turnover_yi": round(actual_turnover, 2),
    }
    for key in ("turnover_actual_source", "turnover_actual_source_url"):
        value = str(source.get(key) or "").strip()
        if value:
            sample[key] = value
    return sample


def compact_previous_turnover_history(
    payload: dict[str, Any] | None,
    *,
    before_date: str = "",
    max_points: int = DEFAULT_HISTORY_LIMIT,
) -> dict[str, Any] | None:
    """Validate the retained previous trading day's actual-turnover curve."""

    source = payload if isinstance(payload, dict) else {}
    day = str(source.get("date") or "").strip()
    if _sample_time(f"{day} 00:00:00") is None or (before_date and day >= before_date):
        return None
    by_time: dict[str, dict[str, Any]] = {}
    for raw in source.get("samples") or []:
        sample = _compact_actual_turnover_sample(
            raw if isinstance(raw, dict) else None
        )
        if sample is None or sample["generated_at"][:10] != day:
            continue
        by_time[sample["generated_at"]] = sample
    limit = max(2, min(600, int(max_points)))
    samples = [by_time[key] for key in sorted(by_time)][-limit:]
    if not samples:
        return None
    result: dict[str, Any] = {"date": day, "samples": samples}
    for key in ("source", "source_url"):
        value = str(source.get(key) or "").strip()
        if value:
            result[key] = value
    if "source" not in result:
        result["source"] = str(
            samples[-1].get("turnover_actual_source") or ""
        )
    if "source_url" not in result:
        result["source_url"] = str(
            samples[-1].get("turnover_actual_source_url") or ""
        )
    return result


def _archive_actual_turnover_samples(
    samples: list[Any],
    *,
    before_date: str,
    max_points: int,
) -> dict[str, Any] | None:
    by_day: dict[str, list[dict[str, Any]]] = {}
    for raw in samples:
        sample = _compact_actual_turnover_sample(
            raw if isinstance(raw, dict) else None
        )
        if sample is None:
            continue
        day = sample["generated_at"][:10]
        if day < before_date:
            by_day.setdefault(day, []).append(sample)
    if not by_day:
        return None
    day = max(by_day)
    return compact_previous_turnover_history(
        {"date": day, "samples": by_day[day]},
        before_date=before_date,
        max_points=max_points,
    )


def roll_market_breadth_history(
    history: dict[str, Any] | None,
    current_day: str,
    *,
    max_points: int = DEFAULT_HISTORY_LIMIT,
    interval_seconds: int = DEFAULT_SAMPLE_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Retain current-day breadth plus one prior actual-turnover curve."""

    if _sample_time(f"{current_day} 00:00:00") is None:
        raise ValueError(f"Invalid market-breadth history date: {current_day!r}")
    existing = history if isinstance(history, dict) else {}
    limit = max(2, min(600, int(max_points)))
    current_by_time: dict[str, dict[str, Any]] = {}
    for raw in existing.get("samples") or []:
        sample = compact_market_breadth_sample(
            raw if isinstance(raw, dict) else None
        )
        if (
            sample is not None
            and sample["generated_at"][:10] == current_day
            and is_market_breadth_session_timestamp(sample["generated_at"])
        ):
            current_by_time[sample["generated_at"]] = sample
    current_samples = [
        current_by_time[key] for key in sorted(current_by_time)
    ][-limit:]

    previous_candidates = [
        compact_previous_turnover_history(
            existing.get("previous_turnover")
            if isinstance(existing.get("previous_turnover"), dict)
            else None,
            before_date=current_day,
            max_points=limit,
        ),
        _archive_actual_turnover_samples(
            list(existing.get("samples") or []),
            before_date=current_day,
            max_points=limit,
        ),
    ]
    valid_previous = [candidate for candidate in previous_candidates if candidate]
    previous = max(valid_previous, key=lambda item: item["date"]) if valid_previous else None
    result: dict[str, Any] = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "date": current_day,
        "interval_seconds": max(60, min(600, int(interval_seconds))),
        "samples": current_samples,
    }
    if previous is not None:
        result["previous_turnover"] = previous
    return result


def append_market_breadth_sample(
    history: dict[str, Any] | None,
    payload: dict[str, Any] | None,
    *,
    max_points: int = DEFAULT_HISTORY_LIMIT,
    interval_seconds: int = DEFAULT_SAMPLE_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Append one current-day observation and retain one prior turnover curve."""

    sample = compact_market_breadth_sample(payload)
    existing = history if isinstance(history, dict) else {}
    if sample is None or not is_market_breadth_session_timestamp(sample["generated_at"]):
        return existing
    sample_day = sample["generated_at"][:10]
    rolled = roll_market_breadth_history(
        existing,
        sample_day,
        max_points=max_points,
        interval_seconds=interval_seconds,
    )
    by_time: dict[str, dict[str, Any]] = {}
    for raw in rolled.get("samples") or []:
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
    rolled["samples"] = samples
    return rolled


def _attach_previous_turnover_overlay(
    timeline: list[dict[str, Any]],
    previous_turnover: dict[str, Any] | None,
    *,
    tolerance_seconds: int,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if not timeline:
        return timeline, None
    current_day = timeline[-1]["generated_at"][:10]
    previous = compact_previous_turnover_history(
        previous_turnover,
        before_date=current_day,
    )
    if previous is None:
        return timeline, None

    previous_by_progress: dict[int, float] = {}
    for sample in previous["samples"]:
        progress = _session_progress_seconds(sample["generated_at"])
        if progress is None:
            continue
        actual = float(sample["actual_turnover_yi"])
        previous_by_progress[progress] = max(
            actual,
            previous_by_progress.get(progress, actual),
        )
    progresses = sorted(previous_by_progress)
    if not progresses:
        return timeline, None

    tolerance = max(30, min(300, int(tolerance_seconds)))
    result: list[dict[str, Any]] = []
    matched_points = 0
    for raw in timeline:
        point = dict(raw)
        progress = _session_progress_seconds(point.get("generated_at"))
        if progress is None:
            result.append(point)
            continue
        insertion = bisect_left(progresses, progress)
        candidates = progresses[max(0, insertion - 1):min(len(progresses), insertion + 1)]
        if not candidates:
            result.append(point)
            continue
        matched_progress = min(
            candidates,
            key=lambda candidate: (abs(candidate - progress), -candidate),
        )
        if abs(matched_progress - progress) > tolerance:
            result.append(point)
            continue
        previous_actual = round(previous_by_progress[matched_progress], 2)
        point["previous_actual_turnover_yi"] = previous_actual
        try:
            current_actual = float(point.get("actual_turnover_yi"))
        except (TypeError, ValueError):
            current_actual = math.nan
        if math.isfinite(current_actual) and current_actual >= 0:
            point["turnover_same_time_delta_yi"] = round(
                current_actual - previous_actual,
                2,
            )
        result.append(point)
        matched_points += 1
    if not matched_points:
        return result, None
    return result, {
        "date": previous["date"],
        "source": str(previous.get("source") or ""),
        "source_url": str(previous.get("source_url") or ""),
        "point_count": len(previous["samples"]),
        "matched_point_count": matched_points,
    }


def build_market_breadth_payload(
    latest: dict[str, Any] | None,
    *,
    history_samples: list[dict[str, Any]] | None = None,
    previous_turnover: dict[str, Any] | None = None,
    interval_seconds: int = DEFAULT_SAMPLE_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Project a latest snapshot and persisted samples to the public chart model."""

    source = latest if isinstance(latest, dict) else {}
    raw_points = [*(history_samples or []), source]
    active_estimate_model = ""
    for raw in reversed(raw_points):
        compact = compact_market_breadth_sample(
            raw if isinstance(raw, dict) else None
        )
        model = str((compact or {}).get("turnover_estimate_model") or "").strip()
        if compact is not None and "estimated_turnover_yi" in compact and model:
            active_estimate_model = model
            break

    def compact_for_public(raw: dict[str, Any] | None) -> dict[str, Any] | None:
        candidate = raw if isinstance(raw, dict) else {}
        return compact_market_breadth_sample(
            _without_incompatible_turnover_estimate(
                candidate,
                active_estimate_model,
            )
        )

    current = compact_for_public(source)
    current_day = current["generated_at"][:10] if current else ""
    comparison_reference = current if current and "previous_turnover_yi" in current else None
    if comparison_reference is None:
        for raw in reversed(history_samples or []):
            compact_reference = compact_for_public(
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
    for raw in raw_points:
        candidate = (
            _without_incompatible_turnover_estimate(raw, active_estimate_model)
            if isinstance(raw, dict)
            else None
        )
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
    timeline, previous_actual = _attach_previous_turnover_overlay(
        timeline,
        previous_turnover,
        tolerance_seconds=max(
            PREVIOUS_TURNOVER_MATCH_TOLERANCE_SECONDS,
            int(interval_seconds) + 30,
        ),
    )
    public_by_time = {point["generated_at"]: point for point in timeline}
    public_current = (
        public_by_time.get(current["generated_at"], current)
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
            "historical_backfill": {
                "available": previous_actual is not None,
                "days": 1 if previous_actual is not None else 0,
                "date": str((previous_actual or {}).get("date") or ""),
            },
        },
    }
    if previous_actual is not None:
        result["turnover_previous_actual"] = previous_actual
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
    "compact_previous_turnover_history",
    "is_market_breadth_session_timestamp",
    "roll_market_breadth_history",
]
