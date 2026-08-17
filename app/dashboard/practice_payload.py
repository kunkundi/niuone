"""Pure transformations for practice-dashboard history payloads.

The HTTP/dashboard composition module owns runtime concerns such as the current
clock, the A-share trading calendar, and response schema settings.  Those
dependencies are passed in explicitly here so these transformations stay
deterministic and reusable.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Callable

try:
    from trading.accounting import trade_counts_for_account
except ModuleNotFoundError:  # package import when only the repository root is on sys.path
    from app.trading.accounting import trade_counts_for_account


TradingDayPredicate = Callable[[datetime], bool]
TimestampParser = Callable[[str], datetime | None]
HistoryFilter = Callable[..., list[dict[str, Any]]]
SequenceSampler = Callable[[list[Any], int], list[Any]]
ElapsedMinuteParser = Callable[[str], float | None]


def latest_valid_equity_time(history: list[dict[str, Any]]) -> str:
    candidates: list[str] = []
    for point in history or []:
        if not isinstance(point, dict):
            continue
        time_text = str(point.get("time") or "")
        try:
            equity = float(point.get("equity"))
            datetime.strptime(time_text, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            continue
        if math.isfinite(equity):
            candidates.append(time_text)
    return max(candidates, default="")


def downsample_sequence(items: list[Any], max_points: int) -> list[Any]:
    items = list(items or [])
    if max_points <= 0 or len(items) <= max_points:
        return items
    last_idx = len(items) - 1
    selected: list[Any] = []
    seen: set[int] = set()
    for i in range(max_points):
        idx = int(i * last_idx / max(1, max_points - 1))
        if idx in seen:
            continue
        seen.add(idx)
        selected.append(items[idx])
    if selected and selected[-1] is not items[-1]:
        selected[-1] = items[-1]
    return selected


def parse_dashboard_ts(value: str) -> datetime | None:
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def filter_future_equity_points(
    history: list[dict[str, Any]],
    *,
    now: datetime,
    is_trading_day: TradingDayPredicate,
    grace_seconds: int = 120,
    parse_timestamp: TimestampParser | None = None,
) -> list[dict[str, Any]]:
    parse_timestamp = parse_dashboard_ts if parse_timestamp is None else parse_timestamp
    cutoff = now + timedelta(seconds=max(0, int(grace_seconds or 0)))
    filtered: list[dict[str, Any]] = []
    trading_day_cache: dict[str, bool] = {}
    for point in history or []:
        if not isinstance(point, dict):
            continue
        dt = parse_timestamp(str(point.get("time") or ""))
        if dt is not None and (dt.date() > now.date() or (dt.date() == now.date() and dt > cutoff)):
            continue
        if dt is not None:
            date_key = dt.strftime("%Y-%m-%d")
            if date_key not in trading_day_cache:
                trading_day_cache[date_key] = is_trading_day(dt)
            if not trading_day_cache[date_key]:
                continue
        filtered.append(point)
    return filtered


def compact_intraday_equity_history(
    history: list[dict[str, Any]],
    *,
    max_points: int,
    now: datetime,
    is_trading_day: TradingDayPredicate,
    filter_points: HistoryFilter | None = None,
    downsample: SequenceSampler | None = None,
) -> list[dict[str, Any]]:
    filter_points = filter_future_equity_points if filter_points is None else filter_points
    downsample = downsample_sequence if downsample is None else downsample
    points = sorted(
        filter_points(
            history or [],
            now=now,
            is_trading_day=is_trading_day,
        ),
        key=lambda point: str(point.get("time") or "") if isinstance(point, dict) else "",
    )
    if not points:
        return []
    latest_day = max(
        (str(point.get("time") or "")[:10] for point in points if len(str(point.get("time") or "")) >= 10),
        default="",
    )
    day_points = [p for p in points if str(p.get("time") or "").startswith(latest_day)] if latest_day else points
    return downsample(day_points, max_points)


def dashboard_session_elapsed_minute(
    value: str,
    *,
    parse_timestamp: TimestampParser | None = None,
) -> float | None:
    parser = parse_dashboard_ts if parse_timestamp is None else parse_timestamp
    dt = parser(value)
    if dt is None:
        return None
    minute = dt.hour * 60 + dt.minute
    second_fraction = dt.second / 60
    if 9 * 60 + 30 <= minute <= 11 * 60 + 30:
        elapsed = minute - (9 * 60 + 30) + second_fraction
        return min(math.nextafter(120.0, 0.0), elapsed)
    if 13 * 60 <= minute <= 15 * 60:
        return minute - (9 * 60 + 30) - 90 + second_fraction
    return None


def build_compact_calendar_history(
    history: list[dict[str, Any]],
    *,
    source_updated_at: str,
    max_days: int,
    bucket_minutes: int,
    default_bucket_minutes: int,
    schema_version: int,
    now: datetime,
    is_trading_day: TradingDayPredicate,
    filter_points: HistoryFilter | None = None,
    elapsed_minute: ElapsedMinuteParser | None = None,
) -> dict[str, Any]:
    """Build a bounded M4 (first/last/min/max) multi-day series."""
    bucket_minutes = max(1, int(bucket_minutes or default_bucket_minutes))
    by_date: dict[str, dict[str, dict[str, Any]]] = {}
    valid_points: list[dict[str, Any]] = []
    filter_points = filter_future_equity_points if filter_points is None else filter_points
    elapsed_minute = dashboard_session_elapsed_minute if elapsed_minute is None else elapsed_minute
    filtered_history = filter_points(
        history or [],
        now=now,
        is_trading_day=is_trading_day,
    )
    for point in filtered_history:
        if not isinstance(point, dict):
            continue
        time_text = str(point.get("time") or "")
        elapsed = elapsed_minute(time_text)
        try:
            equity = float(point.get("equity"))
        except (TypeError, ValueError):
            continue
        if elapsed is None or not math.isfinite(equity):
            continue
        date = time_text[:10]
        if not date:
            continue
        normalized = {"time": time_text, "equity": equity}
        by_date.setdefault(date, {})[time_text] = normalized
        valid_points.append(normalized)

    dates = sorted(by_date)
    truncated = max_days > 0 and len(dates) > max_days
    if max_days > 0:
        dates = dates[-max_days:]
    compact_days: dict[str, list[dict[str, Any]]] = {}
    max_bucket = max(0, math.ceil(240 / bucket_minutes) - 1)
    for date in dates:
        points = sorted(by_date[date].values(), key=lambda point: str(point.get("time") or ""))
        if not points:
            continue
        selected: dict[str, dict[str, Any]] = {}
        buckets: dict[int, list[dict[str, Any]]] = {}
        for point in points:
            elapsed = elapsed_minute(str(point.get("time") or ""))
            if elapsed is None:
                continue
            bucket = min(max_bucket, int(min(239.999, max(0.0, elapsed)) // bucket_minutes))
            buckets.setdefault(bucket, []).append(point)
        for bucket_points in buckets.values():
            first = bucket_points[0]
            last = bucket_points[-1]
            low = min(bucket_points, key=lambda point: float(point.get("equity") or 0))
            high = max(bucket_points, key=lambda point: float(point.get("equity") or 0))
            selected[str(first["time"])] = first
            selected[str(last["time"])] = last
            selected[str(low["time"])] = low
            selected[str(high["time"])] = high
        compact_days[date] = [
            {"clock": str(point["time"])[11:], "equity": point["equity"]}
            for point in sorted(selected.values(), key=lambda item: str(item.get("time") or ""))
        ]

    source_points = sorted(valid_points, key=lambda point: str(point.get("time") or ""))
    return {
        "schema_version": schema_version,
        "timezone": "Asia/Shanghai",
        "bucket_minutes": bucket_minutes,
        "max_days": max_days,
        "complete": True,
        "truncated": truncated,
        "coverage_start": dates[0] if dates else "",
        "coverage_end": dates[-1] if dates else "",
        "source_updated_at": str(source_updated_at or ""),
        "source_last_equity_time": str(source_points[-1].get("time") or "") if source_points else "",
        "days": compact_days,
    }


def compact_daily_equity_history(
    history: list[dict[str, Any]],
    *,
    max_days: int,
    now: datetime,
    is_trading_day: TradingDayPredicate,
    filter_points: HistoryFilter | None = None,
) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    points = sorted(
        (filter_future_equity_points if filter_points is None else filter_points)(
            history or [],
            now=now,
            is_trading_day=is_trading_day,
        ),
        key=lambda point: str(point.get("time") or "") if isinstance(point, dict) else "",
    )
    for point in points:
        if not isinstance(point, dict):
            continue
        date = str(point.get("time") or "")[:10]
        if date:
            by_date[date] = point
    return [by_date[date] for date in sorted(by_date.keys())][-max_days:]


def compact_strategy_performance(perf: dict[str, Any], *, max_exit_items: int = 12) -> dict[str, Any]:
    if not isinstance(perf, dict):
        return {}
    result = dict(perf)
    exit_rules = perf.get("exit_rule")
    if isinstance(exit_rules, dict):
        compact_rules: dict[str, Any] = {}
        for key, value in exit_rules.items():
            if not isinstance(value, dict):
                compact_rules[key] = value
                continue
            next_value = dict(value)
            items = next_value.get("items")
            if isinstance(items, list) and len(items) > max_exit_items:
                next_value["items"] = items[-max_exit_items:]
                next_value["items_truncated"] = len(items) - max_exit_items
            compact_rules[key] = next_value
        result["exit_rule"] = compact_rules
    return result


def compact_trade_markers(
    entries: list[Any],
    *,
    max_items: int = 200,
) -> list[dict[str, Any]]:
    """Keep the compact fill fields needed to annotate equity charts."""
    fields = (
        "time", "action", "code", "name", "shares", "price", "pnl", "pnl_pct",
        "position_after_trade_pct", "position_after_trade_qty",
    )
    source_rows: list[dict[str, Any]] = []
    for item in entries or []:
        if not isinstance(item, dict):
            continue
        if not trade_counts_for_account(item):
            continue
        action = str(item.get("action") or "").upper()
        time_text = str(item.get("time") or "")
        if action not in {"BUY", "SELL"} or not time_text:
            continue
        row = {key: item.get(key) for key in fields if item.get(key) is not None}
        row["action"] = action
        row["time"] = time_text
        source_rows.append(row)

    source_rows.sort(key=lambda item: str(item.get("time") or ""))
    inferred_positions: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for row in source_rows:
        action = str(row.get("action") or "")
        code = str(row.get("code") or "")
        try:
            shares = max(0, int(float(row.get("shares") or 0)))
        except (TypeError, ValueError):
            shares = 0
        before_qty = inferred_positions.get(code, 0)
        if action == "BUY":
            inferred_positions[code] = before_qty + shares
        else:
            inferred_after_qty = max(0, before_qty - shares)
            explicit_after_qty = row.get("position_after_trade_qty")
            try:
                after_qty = max(0, int(float(explicit_after_qty))) if explicit_after_qty is not None else inferred_after_qty
            except (TypeError, ValueError):
                after_qty = inferred_after_qty
            inferred_positions[code] = after_qty

            explicit_after_pct = row.get("position_after_trade_pct")
            try:
                is_full_exit = float(explicit_after_pct) <= 0 if explicit_after_pct is not None else before_qty > 0 and after_qty <= 0
            except (TypeError, ValueError):
                is_full_exit = before_qty > 0 and after_qty <= 0
            row["is_full_exit"] = bool(is_full_exit)
        rows.append(row)
    return rows[-max(0, int(max_items or 0)):] if max_items else rows
