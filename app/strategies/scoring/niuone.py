"""牛牛战法: infer market mainlines from cross-sectional strong-stock resonance."""
from __future__ import annotations

import math
import re
import statistics
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from ..niuone_risk import (
    NIUONE_ABSOLUTE_POSITION_CAP_PCT,
    NIUONE_MARKUP_MOMENTUM_PROBE_MAX_ENTRY_EXTENSION_ATR,
    NIUONE_MARKUP_MOMENTUM_PROBE_MIN_SCORE,
    NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MIN_SCORE,
    NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT,
    NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
    niuone_chase_limits,
    niuone_markup_momentum_probe_eligible,
    niuone_markup_momentum_probe_is_acceleration,
    niuone_risk_budget,
    niuone_structure_risk_ok,
    niuone_structural_stop_limits,
)
from ..policy import (
    NIUONE_DAILY_V_MIN_RECOVERY_RATIO,
    NIUONE_MATURE_MIN_MARKET_AMOUNT_PERCENTILE,
    NIUONE_MATURE_MIN_THEME_AMOUNT_PERCENTILE,
    NIUONE_TODAY_OBSERVATION_THRESHOLD,
    NIUONE_THEME_ATTRIBUTION_CONFIDENCE_SCORE,
    NIUONE_THEME_LEADER_MIN_ATTRIBUTION_WEIGHT,
    niuone_turnover_blocker,
    niuone_stock_activity_blocker,
)
from ..lifecycle import (
    niuone_lifecycle_entry_blocker,
    niuone_lifecycle_metadata,
)
from ..sector_tide_risk import (
    SECTOR_TIDE_EXECUTION_BUFFER_PCT,
    downside_gap_buffer_pct,
    effective_loss_distance_pct,
    risk_sized_position_cap_pct,
    structural_stop_distance_pct,
)
from .common import (
    niu_emerging_theme_eligible,
    safe_float,
    safe_round,
    with_strategy_profile,
)


NIUONE_STRATEGY_IDS = frozenset({
    "niu_leader",
    "niu_pullback",
    "niu_emerging",
    "niu_reversal_probe",
})
NIUONE_MIN_ROWS = 55
NIUONE_MIN_THEME_MEMBERS = 3
NIUONE_STRONG_SCORE_THRESHOLD = 70.0
NIUONE_CORE_STOCK_LIMIT = 5
NIUONE_LEADER_TIER_LIMIT = 3
NIUONE_MIN_CROSS_DAY_CORE_OVERLAP = 2
NIUONE_TODAY_MIN_QUOTE_COVERAGE = 0.8
NIUONE_REVERSAL_MIN_REBOUND_PCT = 1.5
NIUONE_DAILY_V_LOOKBACK = 30
NIUONE_DAILY_V_LEFT_LOOKBACK = 15
NIUONE_DAILY_V_MIN_LEFT_DAYS = 5
NIUONE_DAILY_V_MIN_RIGHT_DAYS = 3
NIUONE_DAILY_V_MAX_RIGHT_DAYS = 15
NIUONE_DAILY_V_MIN_DECLINE_PCT = 8.0
NIUONE_DAILY_V_MIN_REBOUND_PCT = 6.0
NIUONE_DAILY_V_MIN_RISING_RATIO = 2 / 3
NIUONE_THEME_ATTRIBUTION_CURRENT_WEIGHT = 0.75
NIUONE_THEME_ATTRIBUTION_HISTORY_WEIGHT = 0.25
NIUONE_THEME_ATTRIBUTION_HISTORY_DECAY = 0.75
NIUONE_THEME_ATTRIBUTION_CONFIDENCE_GAP = 5.0
NIUONE_THEME_ATTRIBUTION_SOFTMAX_TEMPERATURE = 12.0
NIUONE_THEME_ATTRIBUTION_MIN_MASS = 0.25
NIUONE_THEME_RETURN_CORRELATION_LOOKBACK = 20
NIUONE_THEME_RETURN_CORRELATION_MIN_OBSERVATIONS = 8
NIUONE_THEME_RETURN_CORRELATION_MIN_PEERS = 3
NIUONE_THEME_RETURN_CORRELATION_RANK_FULL_SPREAD = 15.0
NIUONE_MIN_ATTRIBUTED_THEME_MASS = 1.5
NIUONE_TODAY_BREADTH_PRIOR_MASS = 4.0
NIUONE_CONTEXT_VERSION = 13


def _mean(values: list[float], default: float = 0.0) -> float:
    return statistics.mean(values) if values else default


def _weighted_mean(
    values: list[tuple[float, float]],
    *,
    default: float = 0.0,
) -> float:
    clean = [
        (float(value), max(0.0, float(weight)))
        for value, weight in values
        if math.isfinite(float(value)) and math.isfinite(float(weight))
    ]
    total = sum(weight for _value, weight in clean)
    if total <= 0:
        return default
    return sum(value * weight for value, weight in clean) / total


def _weighted_median(values: list[tuple[float, float]]) -> float:
    clean = sorted(
        (
            (float(value), max(0.0, float(weight)))
            for value, weight in values
            if math.isfinite(float(value)) and math.isfinite(float(weight))
        ),
        key=lambda item: item[0],
    )
    total = sum(weight for _value, weight in clean)
    if total <= 0:
        return 0.0
    threshold = total / 2.0
    cumulative = 0.0
    for index, (value, weight) in enumerate(clean):
        cumulative += weight
        if cumulative >= threshold:
            if (
                math.isclose(cumulative, threshold, rel_tol=0.0, abs_tol=1e-12)
                and index + 1 < len(clean)
            ):
                return (value + clean[index + 1][0]) / 2.0
            return value
    return clean[-1][0]


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _industry_name(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).strip()
    for suffix in ("行业", "板块", "概念", "指数"):
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            text = text[: -len(suffix)]
    return text


def _theme_names(value: Any, *, fallback: Any = "") -> tuple[str, ...]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = value
    else:
        values = ()
    labels = tuple(dict.fromkeys(
        label for item in values if (label := _industry_name(item))
    ))
    if labels:
        return labels
    fallback_label = _industry_name(fallback)
    return (fallback_label,) if fallback_label else ()


def _stock_code(value: Any) -> str:
    matched = re.search(r"\d{6}", str(value or ""))
    return matched.group(0) if matched else ""


def _percentile(value: float, population: list[float]) -> float:
    clean = sorted(float(item) for item in population if math.isfinite(float(item)))
    return _percentile_from_sorted(value, clean)


def _percentile_from_sorted(value: float, clean: list[float]) -> float:
    if not clean or len(clean) == 1:
        return 50.0
    below = bisect_left(clean, value)
    equal = bisect_right(clean, value) - below
    return _clamp((below + max(0, equal - 1) / 2) / (len(clean) - 1) * 100)


def _return_pct(
    rows: Sequence[Mapping[str, Any]],
    lookback: int,
    *,
    current_close: float | None = None,
) -> float | None:
    if len(rows) <= lookback:
        return None
    close = current_close if current_close is not None else safe_float(rows[-1].get("close"))
    base = safe_float(rows[-lookback - 1].get("close"))
    if close is None or base is None or base <= 0:
        return None
    return (close / base - 1) * 100


def _daily_return_path(
    rows: Sequence[Mapping[str, Any]],
    *,
    current_close: float | None = None,
) -> dict[str, float]:
    """Return the recent dated close-to-close path used for theme attribution."""
    window = rows[-(NIUONE_THEME_RETURN_CORRELATION_LOOKBACK + 1):]
    closes = [safe_float(row.get("close")) for row in window]
    if current_close is not None and current_close > 0 and closes:
        closes[-1] = current_close
    result: dict[str, float] = {}
    for index in range(1, len(window)):
        previous_close = closes[index - 1]
        close = closes[index]
        date = str(window[index].get("date") or "")[:10]
        if (
            previous_close is None
            or previous_close <= 0
            or close is None
            or close <= 0
            or not date
        ):
            continue
        result[date] = (close / previous_close - 1.0) * 100.0
    return result


NIUONE_ATR_LOOKBACK = 14


def _atr(
    rows: Sequence[Mapping[str, Any]],
    lookback: int = NIUONE_ATR_LOOKBACK,
) -> float | None:
    ranges: list[float] = []
    for index in range(max(1, len(rows) - lookback), len(rows)):
        high = safe_float(rows[index].get("high"))
        low = safe_float(rows[index].get("low"))
        prior = safe_float(rows[index - 1].get("close"))
        if high is not None and low is not None and prior is not None:
            ranges.append(max(high - low, abs(high - prior), abs(low - prior)))
    return _mean(ranges) if ranges else None


def _daily_v_reversal_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    current_close: float | None = None,
) -> dict[str, Any]:
    """Detect a multi-session V using only information visible in daily bars."""
    defaults = {
        "daily_v_reversal": False,
        "daily_v_left_peak_date": "",
        "daily_v_trough_date": "",
        "daily_v_left_days": 0,
        "daily_v_right_days": 0,
        "daily_v_decline_pct": 0.0,
        "daily_v_rebound_pct": 0.0,
        "daily_v_recovery_ratio": 0.0,
        "daily_v_rising_ratio": 0.0,
        "daily_v_right_trend_confirmed": False,
        "daily_v_pattern_score": 0.0,
        "daily_v_stop_price": None,
    }
    window = rows[-(NIUONE_DAILY_V_LOOKBACK + 1):]
    if len(window) < NIUONE_DAILY_V_MIN_LEFT_DAYS + NIUONE_DAILY_V_MIN_RIGHT_DAYS + 1:
        return defaults

    closes: list[float] = []
    lows: list[float] = []
    for row in window:
        close = safe_float(row.get("close"))
        low = safe_float(row.get("low"))
        if close is None or close <= 0:
            return defaults
        closes.append(close)
        lows.append(low if low is not None and low > 0 else close)
    if current_close is not None and current_close > 0:
        closes[-1] = current_close

    latest_index = len(window) - 1
    first_trough = NIUONE_DAILY_V_MIN_LEFT_DAYS
    last_trough = latest_index - NIUONE_DAILY_V_MIN_RIGHT_DAYS
    first_trough = max(first_trough, latest_index - NIUONE_DAILY_V_MAX_RIGHT_DAYS)
    if first_trough > last_trough:
        return defaults
    trough_index = min(
        range(first_trough, last_trough + 1),
        key=lambda index: closes[index],
    )
    left_start = max(0, trough_index - NIUONE_DAILY_V_LEFT_LOOKBACK)
    left_candidates = range(left_start, trough_index)
    if not left_candidates:
        return defaults
    left_peak_index = max(left_candidates, key=lambda index: closes[index])
    left_peak = closes[left_peak_index]
    trough = closes[trough_index]
    current = closes[-1]
    if left_peak <= trough or current <= trough:
        return defaults

    left_days = trough_index - left_peak_index
    right_days = latest_index - trough_index
    decline_pct = (left_peak / trough - 1.0) * 100.0
    rebound_pct = (current / trough - 1.0) * 100.0
    recovery_ratio = (current - trough) / (left_peak - trough)
    right_steps = [
        closes[index] > closes[index - 1]
        for index in range(trough_index + 1, latest_index + 1)
    ]
    rising_ratio = sum(right_steps) / len(right_steps) if right_steps else 0.0
    recent_right = closes[max(trough_index + 1, latest_index - 4):]
    right_trend_confirmed = bool(
        right_steps
        and rising_ratio >= NIUONE_DAILY_V_MIN_RISING_RATIO
        and current >= max(closes[max(trough_index + 1, latest_index - 2):])
        and current >= _mean(recent_right, current)
    )
    pattern_score = _clamp(
        _clamp(decline_pct / 12.0 * 100.0) * 0.20
        + _clamp(rebound_pct / 10.0 * 100.0) * 0.30
        + _clamp(recovery_ratio / 0.80 * 100.0) * 0.30
        + rising_ratio * 100.0 * 0.20
    )
    stop_start = max(trough_index + 1, latest_index - 2)
    stop_price = min(lows[stop_start:]) if lows[stop_start:] else trough
    matched = bool(
        left_days >= NIUONE_DAILY_V_MIN_LEFT_DAYS
        and NIUONE_DAILY_V_MIN_RIGHT_DAYS <= right_days <= NIUONE_DAILY_V_MAX_RIGHT_DAYS
        and decline_pct >= NIUONE_DAILY_V_MIN_DECLINE_PCT
        and rebound_pct >= NIUONE_DAILY_V_MIN_REBOUND_PCT
        and recovery_ratio >= NIUONE_DAILY_V_MIN_RECOVERY_RATIO
        and right_trend_confirmed
    )
    return {
        "daily_v_reversal": matched,
        "daily_v_left_peak_date": str(window[left_peak_index].get("date") or "")[:10],
        "daily_v_trough_date": str(window[trough_index].get("date") or "")[:10],
        "daily_v_left_days": left_days,
        "daily_v_right_days": right_days,
        "daily_v_decline_pct": round(decline_pct, 2),
        "daily_v_rebound_pct": round(rebound_pct, 2),
        "daily_v_recovery_ratio": round(recovery_ratio, 4),
        "daily_v_rising_ratio": round(rising_ratio, 4),
        "daily_v_right_trend_confirmed": right_trend_confirmed,
        "daily_v_pattern_score": round(pattern_score, 2),
        "daily_v_stop_price": stop_price,
    }


def _member_metrics(item: dict[str, Any]) -> dict[str, Any] | None:
    raw_rows = item.get("rows")
    rows = (
        raw_rows
        if isinstance(raw_rows, Sequence)
        and not isinstance(raw_rows, (str, bytes, bytearray))
        else ()
    )
    if len(rows) < NIUONE_MIN_ROWS:
        return None
    latest = rows[-1]
    quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
    close = safe_float(quote.get("price"))
    if close is None or close <= 0:
        close = safe_float(latest.get("close"))
    ema20 = safe_float(latest.get("ema20"))
    ema50 = safe_float(latest.get("ema50"))
    ret5 = _return_pct(rows, 5, current_close=close)
    ret20 = _return_pct(rows, 20, current_close=close)
    if close is None or close <= 0 or ret5 is None or ret20 is None:
        return None
    recent_volumes = [safe_float(row.get("volume")) for row in rows[-5:]]
    prior_volumes = [safe_float(row.get("volume")) for row in rows[-25:-5]]
    recent = [value for value in recent_volumes if value is not None and value >= 0]
    prior = [value for value in prior_volumes if value is not None and value >= 0]
    volume_ratio = _mean(recent) / _mean(prior) if prior and _mean(prior) > 0 else 1.0
    prior_highs = [safe_float(row.get("high")) for row in rows[-21:-1]]
    highs = [value for value in prior_highs if value is not None and value > 0]
    live_change = safe_float(quote.get("change_pct"))
    previous_close = safe_float(quote.get("prev_close"))
    if previous_close is None or previous_close <= 0:
        previous_close = safe_float(rows[-2].get("close")) if len(rows) >= 2 else None
    prior_5_base = safe_float(rows[-7].get("close")) if len(rows) >= 7 else None
    prior_ret5 = (
        (previous_close / prior_5_base - 1) * 100
        if previous_close is not None and previous_close > 0 and prior_5_base is not None and prior_5_base > 0
        else None
    )
    intraday_low = safe_float(quote.get("low"))
    if intraday_low is None or intraday_low <= 0:
        intraday_low = safe_float(latest.get("low"))
    rebound_from_low_pct = (
        (close / intraday_low - 1) * 100
        if intraday_low is not None and intraday_low > 0
        else None
    )
    industry = _industry_name(item.get("industry") or latest.get("industry"))
    themes = _theme_names(
        item.get("themes") or latest.get("themes"),
        fallback=industry,
    )
    quote_amount = safe_float(quote.get("amount"))
    row_amount = safe_float(latest.get("quote_amount"))
    resolved_amount = quote_amount if quote_amount is not None else row_amount
    turnover = safe_float(quote.get("turnover"))
    if turnover is None:
        turnover = safe_float(latest.get("quote_turnover"))
    return {
        "code": _stock_code(item.get("code") or latest.get("symbol_code")),
        "name": str(item.get("name") or latest.get("stock_name") or ""),
        "industry": industry,
        "themes": themes,
        "ret5": ret5,
        "ret20": ret20,
        "above_ema20": bool(ema20 and close >= ema20),
        "trend_aligned": bool(ema20 and ema50 and close >= ema20 >= ema50),
        "new_high20": bool(highs and close >= max(highs)),
        "volume_ratio": volume_ratio,
        "amount": max(0.0, resolved_amount or 0.0),
        "amount_available": (
            resolved_amount is not None
            and math.isfinite(resolved_amount)
            and resolved_amount > 0
        ),
        "turnover": turnover,
        "change_pct": live_change if live_change is not None else (safe_float(latest.get("change_pct")) or 0.0),
        "live_change_available": live_change is not None,
        "previous_close": previous_close,
        "prior_ret5": prior_ret5,
        "return_path": _daily_return_path(rows, current_close=close),
        "intraday_low": intraday_low,
        "rebound_from_low_pct": rebound_from_low_pct,
        "reclaim_previous_close": bool(previous_close and close > previous_close),
    }


def _market_return_path(
    members: list[dict[str, Any]],
) -> dict[str, float]:
    """Build a robust daily market factor from the same prepared stock universe."""
    returns_by_date: dict[str, list[float]] = defaultdict(list)
    for member in members:
        path = member.get("return_path")
        if not isinstance(path, Mapping):
            continue
        for date, raw_value in path.items():
            value = safe_float(raw_value)
            if value is not None:
                returns_by_date[str(date)[:10]].append(value)
    return {
        date: statistics.median(values)
        for date, values in returns_by_date.items()
        if values
    }


def _theme_excess_return_factor(
    theme_members: list[dict[str, Any]],
    *,
    market_returns: Mapping[str, float],
) -> dict[str, list[float]]:
    """Pre-sort theme-member excess returns for fast leave-one-out medians."""
    values_by_date: dict[str, list[float]] = defaultdict(list)
    for member in theme_members:
        path = member.get("return_path")
        if not isinstance(path, Mapping):
            continue
        for date, raw_value in path.items():
            market_value = safe_float(market_returns.get(str(date)[:10]))
            value = safe_float(raw_value)
            if value is not None and market_value is not None:
                values_by_date[str(date)[:10]].append(value - market_value)
    return {
        date: sorted(values)
        for date, values in values_by_date.items()
        if values
    }


def _median_excluding(
    sorted_values: Sequence[float],
    excluded_value: float,
) -> float | None:
    """Return a median after removing one exact member value without copying."""
    index = bisect_left(sorted_values, excluded_value)
    if index >= len(sorted_values) or not math.isclose(
        sorted_values[index],
        excluded_value,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        return None
    remaining = len(sorted_values) - 1
    if remaining <= 0:
        return None

    def remaining_value(position: int) -> float:
        return sorted_values[position if position < index else position + 1]

    midpoint = remaining // 2
    if remaining % 2:
        return remaining_value(midpoint)
    return (
        remaining_value(midpoint - 1) + remaining_value(midpoint)
    ) / 2.0


def _peer_return_correlation(
    member: Mapping[str, Any],
    *,
    theme_factor: Mapping[str, list[float]],
    market_returns: Mapping[str, float],
) -> dict[str, Any]:
    """Measure whether daily excess-return waves match the other theme members."""
    path = member.get("return_path")
    if not isinstance(path, Mapping):
        path = {}
    stock_returns: list[float] = []
    peer_returns: list[float] = []
    peer_counts: list[int] = []
    for date in sorted(str(value)[:10] for value in path)[
        -NIUONE_THEME_RETURN_CORRELATION_LOOKBACK:
    ]:
        raw_stock = safe_float(path.get(date))
        market_value = safe_float(market_returns.get(date))
        values = theme_factor.get(date)
        if raw_stock is None or market_value is None or not isinstance(values, list):
            continue
        stock_excess = raw_stock - market_value
        if len(values) - 1 < NIUONE_THEME_RETURN_CORRELATION_MIN_PEERS:
            continue
        peer_median = _median_excluding(values, stock_excess)
        if peer_median is None:
            continue
        stock_returns.append(stock_excess)
        peer_returns.append(peer_median)
        peer_counts.append(len(values) - 1)

    observation_count = len(stock_returns)
    result = {
        "return_correlation_score": None,
        "return_correlation_observation_count": observation_count,
        "return_correlation_peer_count": min(peer_counts) if peer_counts else 0,
    }
    if observation_count < NIUONE_THEME_RETURN_CORRELATION_MIN_OBSERVATIONS:
        return result
    stock_mean = statistics.mean(stock_returns)
    peer_mean = statistics.mean(peer_returns)
    stock_deviations = [value - stock_mean for value in stock_returns]
    peer_deviations = [value - peer_mean for value in peer_returns]
    denominator = math.sqrt(
        sum(value * value for value in stock_deviations)
        * sum(value * value for value in peer_deviations)
    )
    if denominator <= 1e-12:
        return result
    correlation = sum(
        stock_value * peer_value
        for stock_value, peer_value in zip(
            stock_deviations,
            peer_deviations,
        )
    ) / denominator
    result["return_correlation_score"] = round(
        _clamp((correlation + 1.0) * 50.0),
        2,
    )
    return result


def _previous_theme_attributions(
    stock: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(stock, Mapping):
        return {}
    raw = stock.get("theme_attributions")
    if not isinstance(raw, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        theme = _industry_name(item.get("theme") or item.get("industry"))
        if theme:
            result[theme] = dict(item)
    return result


def _apply_theme_attributions(
    profiles: list[dict[str, Any]],
    *,
    previous_stock: Mapping[str, Any] | None,
    same_trading_day: bool,
) -> list[dict[str, Any]]:
    """Attach bounded current and historical evidence to each concept branch.

    Classification membership is only the candidate set. Attribution is based
    on evidence that excludes the stock from its peer cohort, plus bounded
    historical evidence. A residual ``unattributed_weight`` keeps a weak
    candidate set from being presented as certain narrative attribution.
    """
    previous = _previous_theme_attributions(previous_stock)
    correlation_scores = [
        score
        for source in profiles
        if (score := safe_float(source.get("return_correlation_score")))
        is not None
        and int(source.get("return_correlation_observation_count") or 0)
        >= NIUONE_THEME_RETURN_CORRELATION_MIN_OBSERVATIONS
    ]
    correlation_spread = (
        max(correlation_scores) - min(correlation_scores)
        if len(correlation_scores) >= 2
        else 0.0
    )
    correlation_rank_reliability = _clamp(
        correlation_spread
        / NIUONE_THEME_RETURN_CORRELATION_RANK_FULL_SPREAD,
        0.0,
        1.0,
    )
    theme_member_counts = [
        count
        for source in profiles
        if (count := safe_float(source.get("theme_member_count")))
        is not None
        and count > 0
    ]
    scored: list[dict[str, Any]] = []
    for source in profiles:
        profile = dict(source)
        theme = _industry_name(profile.get("industry"))
        prior = previous.get(theme, {})
        cohort_score = safe_float(profile.get("cohort_alignment_score"))
        correlation_score = safe_float(profile.get("return_correlation_score"))
        if correlation_score is None:
            correlation_rank_score = 0.0
            correlation_score = 0.0
        elif len(correlation_scores) >= 2:
            raw_rank_score = _percentile(correlation_score, correlation_scores)
            correlation_rank_score = _clamp(
                50.0
                + (raw_rank_score - 50.0) * correlation_rank_reliability
            )
        else:
            correlation_rank_score = correlation_score
        theme_member_count = safe_float(profile.get("theme_member_count"))
        specificity_score = (
            100.0 - _percentile(theme_member_count, theme_member_counts)
            if theme_member_count is not None
            and theme_member_count > 0
            and len(theme_member_counts) >= 2
            else 0.0
        )
        current_score = _clamp(
            correlation_rank_score * 0.35
            + correlation_score * 0.10
            + specificity_score * 0.15
            + float(profile.get("peer_resonance_score") or 0.0) * 0.20
            + (cohort_score if cohort_score is not None else 50.0) * 0.10
            + float(profile.get("today_rank_score") or 0.0) * 0.06
            + float(profile.get("theme_rank") or 0.0) * 0.04
        )
        prior_score = safe_float(
            prior.get("historical_prior_score")
            if prior.get("historical_prior_score") is not None
            else prior.get("attribution_score")
        )
        if prior_score is None:
            historical_score = current_score
        elif same_trading_day:
            historical_score = prior_score
        else:
            historical_score = _clamp(
                prior_score * NIUONE_THEME_ATTRIBUTION_HISTORY_DECAY
                + current_score * (1.0 - NIUONE_THEME_ATTRIBUTION_HISTORY_DECAY)
            )
        attribution_score = _clamp(
            current_score * NIUONE_THEME_ATTRIBUTION_CURRENT_WEIGHT
            + historical_score * NIUONE_THEME_ATTRIBUTION_HISTORY_WEIGHT
        )
        observation_count = max(0, int(prior.get("observation_count") or 0))
        wave_count = max(0, int(prior.get("wave_count") or 0))
        if not same_trading_day:
            observation_count += 1
            if (
                profile.get("strong") is True
                and float(profile.get("peer_resonance_score") or 0.0) >= 60.0
            ):
                wave_count += 1
        profile.update({
            "return_correlation_rank_score": round(
                correlation_rank_score,
                2,
            ),
            "theme_specificity_score": round(specificity_score, 2),
            "current_attribution_score": round(current_score, 2),
            "historical_prior_score": round(historical_score, 2),
            "attribution_score": round(attribution_score, 2),
            "attribution_observation_count": observation_count,
            "attribution_wave_count": wave_count,
        })
        scored.append(profile)

    if len(scored) == 1:
        raw_weights = [1.0]
        attributed_mass = 1.0
    elif scored:
        best_score = max(float(item["attribution_score"]) for item in scored)
        exponentials = [
            math.exp(
                (float(item["attribution_score"]) - best_score)
                / NIUONE_THEME_ATTRIBUTION_SOFTMAX_TEMPERATURE
            )
            for item in scored
        ]
        exponential_total = sum(exponentials)
        raw_weights = [
            value / exponential_total if exponential_total > 0 else 1.0 / len(scored)
            for value in exponentials
        ]
        attributed_mass = _clamp(
            (best_score - 30.0) / 40.0,
            NIUONE_THEME_ATTRIBUTION_MIN_MASS,
            1.0,
        )
    else:
        raw_weights = []
        attributed_mass = 0.0
    raw_weights = [weight * attributed_mass for weight in raw_weights]
    weight_scale = 1_000_000
    weight_units = [int(weight * weight_scale) for weight in raw_weights]
    target_units = int(round(attributed_mass * weight_scale))
    remainder = max(0, target_units - sum(weight_units))
    remainder_order = sorted(
        range(len(raw_weights)),
        key=lambda index: (
            -(raw_weights[index] * weight_scale - weight_units[index]),
            index,
        ),
    )
    for index in remainder_order[:remainder]:
        weight_units[index] += 1
    for item, units in zip(scored, weight_units):
        item["attribution_weight"] = units / weight_scale
        item["unattributed_weight"] = round(1.0 - attributed_mass, 6)
    ordered = sorted(
        scored,
        key=lambda item: (
            -float(item.get("attribution_score") or 0.0),
            -float(item.get("theme_rank") or 0.0),
            str(item.get("industry") or ""),
        ),
    )
    for index, item in enumerate(ordered):
        item["leadership_eligible"] = bool(
            float(item.get("attribution_weight") or 0.0)
            >= NIUONE_THEME_LEADER_MIN_ATTRIBUTION_WEIGHT
            or (
                index == 0
                and float(item.get("attribution_score") or 0.0)
                >= NIUONE_THEME_ATTRIBUTION_CONFIDENCE_SCORE
            )
        )
    return ordered


def _theme_leadership_eligible(attribution: Mapping[str, Any]) -> bool:
    """Keep one high-evidence primary theme from being diluted by label count."""
    explicit = attribution.get("leadership_eligible")
    if explicit is not None:
        return bool(explicit)
    return bool(
        float(attribution.get("attribution_weight") or 0.0)
        >= NIUONE_THEME_LEADER_MIN_ATTRIBUTION_WEIGHT
    )


def _rank_theme_leaders(
    members: Iterable[Mapping[str, Any]],
    attributions: Mapping[str, Mapping[str, Any]],
    *,
    intraday: bool,
) -> list[Mapping[str, Any]]:
    """Rank qualified members without multiplying strength by attribution mass."""
    eligible = [
        member
        for member in members
        if _theme_leadership_eligible(
            attributions.get(str(member.get("code") or "")) or {}
        )
    ]

    def rank_key(member: Mapping[str, Any]) -> tuple[float, float, float, float, str]:
        attribution = attributions.get(str(member.get("code") or "")) or {}
        if intraday:
            return (
                -float(member.get("change_pct") or 0.0),
                -float(attribution.get("attribution_score") or 0.0),
                -float(member.get("strong_score") or 0.0),
                -float(member.get("amount") or 0.0),
                str(member.get("code") or ""),
            )
        return (
            -float(member.get("strong_score") or 0.0),
            -float(attribution.get("attribution_score") or 0.0),
            -float(attribution.get("attribution_weight") or 0.0),
            -float(member.get("amount") or 0.0),
            str(member.get("code") or ""),
        )

    return sorted(eligible, key=rank_key)


@dataclass(frozen=True)
class _ThemePeerStatistics:
    """Precomputed theme totals for exact leave-one-stock-out metrics."""

    member_count: int
    nonnegative_ret5_count: int
    nonnegative_ret20_count: int
    strong_count: int
    quoted_count: int
    quoted_up_count: int
    sorted_ret5: tuple[float, ...]
    sorted_ret20: tuple[float, ...]
    fast_member_ids: frozenset[int]


def _theme_peer_statistics(
    theme_members: Sequence[Mapping[str, Any]],
) -> _ThemePeerStatistics:
    """Build one reusable peer summary instead of rescanning for every member."""
    ret5_values: list[float] = []
    ret20_values: list[float] = []
    nonnegative_ret5_count = 0
    nonnegative_ret20_count = 0
    strong_count = 0
    quoted_count = 0
    quoted_up_count = 0
    code_counts: dict[str, int] = defaultdict(int)
    for member in theme_members:
        code = str(member.get("code") or "")
        code_counts[code] += 1
        ret5 = float(member.get("ret5") or 0.0)
        ret20 = float(member.get("ret20") or 0.0)
        ret5_values.append(ret5)
        ret20_values.append(ret20)
        nonnegative_ret5_count += int(ret5 >= 0)
        nonnegative_ret20_count += int(ret20 >= 0)
        strong_count += int(member.get("strong") is True)
        if member.get("live_change_available"):
            quoted_count += 1
            quoted_up_count += int(
                float(member.get("change_pct") or 0.0) > 0
            )
    return _ThemePeerStatistics(
        member_count=len(theme_members),
        nonnegative_ret5_count=nonnegative_ret5_count,
        nonnegative_ret20_count=nonnegative_ret20_count,
        strong_count=strong_count,
        quoted_count=quoted_count,
        quoted_up_count=quoted_up_count,
        sorted_ret5=tuple(sorted(ret5_values)),
        sorted_ret20=tuple(sorted(ret20_values)),
        # Production themes contain one row per stock. Preserve the historical
        # "exclude every row with this code" behavior for malformed duplicate
        # inputs by falling back to the reference scan in that rare case.
        fast_member_ids=frozenset(
            id(member)
            for member in theme_members
            if code_counts[str(member.get("code") or "")] == 1
        ),
    )


def _peer_members_reference(
    member: Mapping[str, Any],
    theme_members: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    code = str(member.get("code") or "")
    return [
        peer
        for peer in theme_members
        if str(peer.get("code") or "") != code
    ]


def _cohort_alignment_score_reference(
    member: Mapping[str, Any],
    theme_members: Sequence[Mapping[str, Any]],
) -> float:
    peers = _peer_members_reference(member, theme_members)
    if not peers:
        return 50.0

    stock_ret5 = float(member.get("ret5") or 0.0)
    stock_ret20 = float(member.get("ret20") or 0.0)

    def same_direction(left: float, right: float) -> bool:
        return (left >= 0 and right >= 0) or (left < 0 and right < 0)

    direction_matches = [
        (
            int(same_direction(stock_ret5, float(peer.get("ret5") or 0.0)))
            + int(same_direction(stock_ret20, float(peer.get("ret20") or 0.0)))
        ) / 2.0
        for peer in peers
    ]
    directional_score = _mean(direction_matches) * 100.0
    peer_strength_score = (
        sum(peer.get("strong") is True for peer in peers) / len(peers) * 100.0
    )
    return _clamp(directional_score * 0.65 + peer_strength_score * 0.35)


def _cohort_alignment_score(
    member: Mapping[str, Any],
    theme_members: Sequence[Mapping[str, Any]],
    *,
    peer_statistics: _ThemePeerStatistics | None = None,
) -> float:
    """Measure peer alignment from one theme-level leave-one-out summary."""
    summary = peer_statistics or _theme_peer_statistics(theme_members)
    if id(member) not in summary.fast_member_ids:
        return _cohort_alignment_score_reference(member, theme_members)
    peer_count = summary.member_count - 1
    if peer_count <= 0:
        return 50.0

    stock_ret5 = float(member.get("ret5") or 0.0)
    stock_ret20 = float(member.get("ret20") or 0.0)
    peer_nonnegative_ret5 = (
        summary.nonnegative_ret5_count - int(stock_ret5 >= 0)
    )
    peer_nonnegative_ret20 = (
        summary.nonnegative_ret20_count - int(stock_ret20 >= 0)
    )
    ret5_direction_matches = (
        peer_nonnegative_ret5
        if stock_ret5 >= 0
        else peer_count - peer_nonnegative_ret5
    )
    ret20_direction_matches = (
        peer_nonnegative_ret20
        if stock_ret20 >= 0
        else peer_count - peer_nonnegative_ret20
    )
    directional_score = (
        (ret5_direction_matches + ret20_direction_matches)
        / (2 * peer_count)
        * 100.0
    )
    peer_strength_score = (
        summary.strong_count - int(member.get("strong") is True)
    ) / peer_count * 100.0
    return _clamp(directional_score * 0.65 + peer_strength_score * 0.35)


def _peer_resonance_score_reference(
    member: Mapping[str, Any],
    theme_members: Sequence[Mapping[str, Any]],
    *,
    market_breadth_pct: float,
) -> float:
    peers = _peer_members_reference(member, theme_members)
    if not peers:
        return 0.0
    strong_pct = sum(peer.get("strong") is True for peer in peers) / len(peers) * 100.0
    quoted = [peer for peer in peers if peer.get("live_change_available")]
    peer_up_pct = (
        sum(float(peer.get("change_pct") or 0.0) > 0 for peer in quoted)
        / len(quoted)
        * 100.0
        if quoted
        else market_breadth_pct
    )
    excess_breadth_score = _clamp(
        50.0 + (peer_up_pct - market_breadth_pct) * 1.5
    )
    ret5_score = _clamp(50.0 + statistics.median(
        float(peer.get("ret5") or 0.0) for peer in peers
    ) * 5.0)
    ret20_score = _clamp(50.0 + statistics.median(
        float(peer.get("ret20") or 0.0) for peer in peers
    ) * 2.5)
    return _clamp(
        strong_pct * 0.35
        + excess_breadth_score * 0.25
        + ret5_score * 0.25
        + ret20_score * 0.15
    )


def _peer_resonance_score(
    member: Mapping[str, Any],
    theme_members: Sequence[Mapping[str, Any]],
    *,
    market_breadth_pct: float,
    peer_statistics: _ThemePeerStatistics | None = None,
) -> float:
    """Score independent peer support without rebuilding peer arrays."""
    summary = peer_statistics or _theme_peer_statistics(theme_members)
    if id(member) not in summary.fast_member_ids:
        return _peer_resonance_score_reference(
            member,
            theme_members,
            market_breadth_pct=market_breadth_pct,
        )
    peer_count = summary.member_count - 1
    if peer_count <= 0:
        return 0.0
    strong_pct = (
        summary.strong_count - int(member.get("strong") is True)
    ) / peer_count * 100.0
    member_quoted = bool(member.get("live_change_available"))
    quoted_count = summary.quoted_count - int(member_quoted)
    quoted_up_count = summary.quoted_up_count - int(
        member_quoted and float(member.get("change_pct") or 0.0) > 0
    )
    peer_up_pct = (
        quoted_up_count / quoted_count * 100.0
        if quoted_count > 0
        else market_breadth_pct
    )
    excess_breadth_score = _clamp(
        50.0 + (peer_up_pct - market_breadth_pct) * 1.5
    )
    peer_ret5_median = _median_excluding(
        summary.sorted_ret5,
        float(member.get("ret5") or 0.0),
    )
    peer_ret20_median = _median_excluding(
        summary.sorted_ret20,
        float(member.get("ret20") or 0.0),
    )
    if peer_ret5_median is None or peer_ret20_median is None:
        return _peer_resonance_score_reference(
            member,
            theme_members,
            market_breadth_pct=market_breadth_pct,
        )
    ret5_score = _clamp(50.0 + peer_ret5_median * 5.0)
    ret20_score = _clamp(50.0 + peer_ret20_median * 2.5)
    return _clamp(
        strong_pct * 0.35
        + excess_breadth_score * 0.25
        + ret5_score * 0.25
        + ret20_score * 0.15
    )


def _today_theme_metrics(
    theme_members: list[dict[str, Any]],
    *,
    attributions: Mapping[str, Mapping[str, Any]],
    market_breadth_pct: float,
) -> dict[str, Any]:
    """Return raw and attribution-weighted intraday participation metrics."""
    total_count = len(theme_members)
    quoted_members = [member for member in theme_members if member.get("live_change_available")]
    quote_count = len(quoted_members)
    coverage = quote_count / total_count if total_count else 0.0
    attributed_member_count = sum(
        max(0.0, float((attributions.get(str(member.get("code") or "")) or {}).get("attribution_weight") or 0.0))
        for member in theme_members
    )
    attributed_quote_count = sum(
        max(0.0, float((attributions.get(str(member.get("code") or "")) or {}).get("attribution_weight") or 0.0))
        for member in quoted_members
    )
    attributed_coverage = (
        attributed_quote_count / attributed_member_count
        if attributed_member_count > 0
        else 0.0
    )
    eligible = bool(
        total_count >= NIUONE_MIN_THEME_MEMBERS
        and quote_count >= NIUONE_MIN_THEME_MEMBERS
        and coverage >= NIUONE_TODAY_MIN_QUOTE_COVERAGE
        and attributed_quote_count >= NIUONE_MIN_ATTRIBUTED_THEME_MASS
        and attributed_coverage >= NIUONE_TODAY_MIN_QUOTE_COVERAGE
    )
    if not quoted_members:
        return {
            "today_eligible_data": False,
            "today_quote_count": 0,
            "today_data_coverage": 0.0,
            "today_up_count": 0,
            "today_1_5pct_count": 0,
            "today_3pct_count": 0,
            "today_5pct_count": 0,
            "today_breadth_pct": None,
            "today_attributed_quote_count": 0.0,
            "today_attributed_up_count": 0.0,
            "today_attributed_breadth_pct": None,
            "today_adjusted_breadth_pct": None,
            "today_median_change_pct": None,
            "today_strength_score": None,
            "today_leadership_score": None,
            "today_leaders": [],
        }

    changes = [float(member["change_pct"]) for member in quoted_members]
    up_count = sum(change > 0 for change in changes)
    advance_1_5_count = sum(change >= 1.5 for change in changes)
    advance_3_count = sum(change >= 3.0 for change in changes)
    advance_5_count = sum(change >= 5.0 for change in changes)
    breadth_pct = up_count / quote_count * 100
    weighted_changes = [
        (
            float(member["change_pct"]),
            max(0.0, float((attributions.get(str(member.get("code") or "")) or {}).get("attribution_weight") or 0.0)),
        )
        for member in quoted_members
    ]
    attributed_up_count = sum(weight for change, weight in weighted_changes if change > 0)
    attributed_3_count = sum(weight for change, weight in weighted_changes if change >= 3.0)
    attributed_5_count = sum(weight for change, weight in weighted_changes if change >= 5.0)
    attributed_breadth_pct = (
        attributed_up_count / attributed_quote_count * 100.0
        if attributed_quote_count > 0
        else 0.0
    )
    market_prior = _clamp(float(market_breadth_pct)) / 100.0
    adjusted_breadth_pct = (
        (
            attributed_up_count
            + NIUONE_TODAY_BREADTH_PRIOR_MASS * market_prior
        )
        / (attributed_quote_count + NIUONE_TODAY_BREADTH_PRIOR_MASS)
        * 100.0
        if attributed_quote_count > 0
        else market_prior * 100.0
    )
    threshold_prior_mass = NIUONE_TODAY_BREADTH_PRIOR_MASS / 2.0
    attributed_3_pct = (
        attributed_3_count / (attributed_quote_count + threshold_prior_mass) * 100.0
        if attributed_quote_count > 0
        else 0.0
    )
    attributed_5_pct = (
        attributed_5_count / (attributed_quote_count + threshold_prior_mass) * 100.0
        if attributed_quote_count > 0
        else 0.0
    )
    median_change = _weighted_median(weighted_changes)
    positive_median_score = _clamp(max(0.0, median_change) / 5.0 * 100)
    strength_score = _clamp(
        adjusted_breadth_pct * 0.45
        + attributed_3_pct * 0.25
        + attributed_5_pct * 0.15
        + positive_median_score * 0.15
    )
    leaders = _rank_theme_leaders(
        quoted_members,
        attributions,
        intraday=True,
    )[:NIUONE_CORE_STOCK_LIMIT]
    top_positive_changes = [
        (
            max(0.0, float(member["change_pct"])),
            float((attributions.get(str(member.get("code") or "")) or {}).get("attribution_weight") or 0.0),
        )
        for member in leaders[:3]
    ]
    leadership_score = _clamp(
        _weighted_mean(top_positive_changes) / 10.0 * 100
    )
    return {
        "today_eligible_data": eligible,
        "today_quote_count": quote_count,
        "today_data_coverage": round(coverage, 4),
        "today_attributed_data_coverage": round(attributed_coverage, 4),
        "today_up_count": up_count,
        "today_1_5pct_count": advance_1_5_count,
        "today_3pct_count": advance_3_count,
        "today_5pct_count": advance_5_count,
        "today_breadth_pct": round(breadth_pct, 2),
        "today_attributed_quote_count": round(attributed_quote_count, 4),
        "today_attributed_up_count": round(attributed_up_count, 4),
        "today_attributed_breadth_pct": round(attributed_breadth_pct, 2),
        "today_adjusted_breadth_pct": round(adjusted_breadth_pct, 2),
        "today_median_change_pct": round(median_change, 2),
        "today_strength_score": round(strength_score, 2),
        "today_leadership_score": round(leadership_score, 2),
        "today_leaders": [
            {
                "code": member["code"],
                "name": member["name"],
                "strong_score": round(float(member["strong_score"]), 2),
                "change_pct": round(float(member["change_pct"]), 2),
                "attribution_score": (attributions.get(str(member.get("code") or "")) or {}).get("attribution_score"),
                "attribution_weight": (attributions.get(str(member.get("code") or "")) or {}).get("attribution_weight"),
                "role": "today_leader" if index == 0 else "today_core",
            }
            for index, member in enumerate(leaders)
        ],
    }


def _theme_core_codes(theme: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(theme, Mapping):
        return []
    explicit = theme.get("core_stock_codes")
    if isinstance(explicit, list):
        codes = [_stock_code(value) for value in explicit]
    else:
        strong_stocks = theme.get("strong_stocks") if isinstance(theme.get("strong_stocks"), list) else []
        codes = [_stock_code(item.get("code")) for item in strong_stocks if isinstance(item, Mapping)]
    return list(dict.fromkeys(code for code in codes if code))[:NIUONE_CORE_STOCK_LIMIT]


def _flow_map(flow_rows: Any) -> dict[str, float]:
    if isinstance(flow_rows, dict):
        rows = [*(flow_rows.get("inflow") or []), *(flow_rows.get("outflow") or [])]
    else:
        rows = flow_rows if isinstance(flow_rows, list) else []
    result: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        industry = _industry_name(row.get("name") or row.get("industry") or row.get("行业"))
        value = safe_float(row.get("net_flow_yi") if row.get("net_flow_yi") is not None else row.get("net_flow"))
        if industry and value is not None:
            result[industry] = value
    return result


def _matched_flow(industry: str, flows: dict[str, float]) -> float | None:
    if industry in flows:
        return flows[industry]
    matches = [value for name, value in flows.items() if industry in name or name in industry]
    return _mean(matches) if matches else None


def _external_context(
    dragon_tiger_snapshot: Any,
    news_snapshot: Any,
    members: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any], dict[str, dict[str, Any]]]:
    member_codes = {str(member["code"]) for member in members if member.get("code")}
    dragon_source = dragon_tiger_snapshot if isinstance(dragon_tiger_snapshot, Mapping) else {}
    dragon_items = dragon_source.get("items") if isinstance(dragon_source.get("items"), list) else []
    dragon_stocks: dict[str, dict[str, Any]] = {}
    for item in dragon_items:
        if not isinstance(item, Mapping):
            continue
        code = _stock_code(item.get("code"))
        if not code or code not in member_codes or code in dragon_stocks:
            continue
        net = safe_float(item.get("net_amount_yuan"))
        buy = safe_float(item.get("buy_amount_yuan")) or 0.0
        sell = safe_float(item.get("sell_amount_yuan")) or 0.0
        if net is None:
            net = buy - sell
        ratio = safe_float(item.get("net_ratio_pct"))
        if ratio is not None:
            strength = _clamp(ratio / 15.0, -1.0, 1.0)
        elif buy + sell > 0:
            strength = _clamp(net / (buy + sell) / 0.4, -1.0, 1.0)
        else:
            strength = _clamp(net / 50_000_000.0, -1.0, 1.0)
        dragon_stocks[code] = {
            "listed": True,
            "strength": round(strength, 4),
            "score": round(50 + strength * 35, 2),
            "signal": "positive" if strength >= 0.15 else ("negative" if strength <= -0.15 else "neutral"),
            "net_amount_yuan": net,
        }
    dragon = {
        "available": dragon_source.get("available") is True and bool(dragon_items),
        "source": str(dragon_source.get("source") or "local_dragon_tiger_snapshot"),
        "as_of_date": str(dragon_source.get("date") or ""),
        "matched_stock_count": len(dragon_stocks),
        "usage": "previous_trading_day_mainline_confirmation",
    }

    news_source = news_snapshot if isinstance(news_snapshot, Mapping) else {}
    news_records = news_source.get("records") if isinstance(news_source.get("records"), list) else []
    news_stocks: dict[str, dict[str, Any]] = {}
    for record in news_records:
        if not isinstance(record, Mapping):
            continue
        code = _stock_code(record.get("code"))
        if not code or code not in member_codes or code in news_stocks:
            continue
        available = record.get("available") is True
        tone = str(record.get("tone") or "neutral") if available else "neutral"
        news_stocks[code] = {
            "checked": record.get("checked") is True,
            "available": available,
            "tone": tone,
            "tone_label": str(record.get("tone_label") or "中性"),
            "summary": str(record.get("summary") or "")[:600],
            "fetched_at": str(record.get("fetched_at") or ""),
            "window_days": int(safe_float(record.get("window_days")) or 3),
            "adjustment": 0.15 if tone == "positive" else (-0.35 if tone == "negative" else 0.0),
            "error": str(record.get("error") or ""),
        }
    news = {
        "configured": news_source.get("configured") is True,
        "available": any(record.get("available") for record in news_stocks.values()),
        "matched_stock_count": len(news_stocks),
        "usage": "shortlisted_candidate_confirmation",
    }
    return dragon, dragon_stocks, news, news_stocks


def _market_context(
    members: list[dict[str, Any]],
    snapshot: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    up = int(safe_float(snapshot.get("up")) or 0)
    down = int(safe_float(snapshot.get("down")) or 0)
    active = up + down
    breadth = up / active * 100 if active else 50.0
    median_change = safe_float(snapshot.get("median_change_pct")) or 0.0
    limit_up = int(safe_float(snapshot.get("limit_up")) or 0)
    limit_down = int(safe_float(snapshot.get("limit_down")) or 0)
    limit_total = limit_up + limit_down
    limit_score = 50 + (limit_up - limit_down) / limit_total * 50 if limit_total else 50.0
    core_count = int(safe_float(snapshot.get("core_index_count")) or 0)
    below_count = int(safe_float(snapshot.get("index_below_ma20_count")) or 0)
    index_score = 100 - below_count / core_count * 100 if core_count else 50.0
    trend_score = _clamp(50 + _mean([float(member["ret20"]) for member in members]) * 4)
    score = (
        breadth * 0.30
        + _clamp(50 + median_change * 20) * 0.20
        + limit_score * 0.15
        + index_score * 0.20
        + trend_score * 0.15
    )
    hard_stop = bool(
        core_count >= 3
        and below_count >= 2
        and down >= max(100, int(up * 1.5))
        and median_change <= -0.8
        and limit_down >= max(5, limit_up)
    )
    raw_state = "defensive" if hard_stop or score < 40 else ("offensive" if score >= 65 and breadth >= 55 else "rotation")
    previous = previous if isinstance(previous, dict) else {}
    prior_state = str(previous.get("state") or "")
    prior_raw = str(previous.get("raw_state") or prior_state)
    confirmation_count = int(previous.get("confirmation_count") or 0) + 1 if prior_raw == raw_state else 1
    if hard_stop:
        state = "defensive"
    elif prior_state == "defensive" and raw_state != "defensive":
        state = "recovery"
    elif confirmation_count >= 2 or not prior_state:
        state = raw_state
    else:
        state = prior_state
    risk_state = "defensive" if raw_state == "defensive" else state
    return {
        "score": round(score, 2),
        "raw_state": raw_state,
        "state": state,
        "confirmation_count": confirmation_count,
        "hard_stop": hard_stop,
        "allow_new_buys": not hard_stop,
        "breadth_score": round(breadth, 2),
        "median_change_pct": round(median_change, 3),
        "limit_up": limit_up,
        "limit_down": limit_down,
        "risk_state": risk_state,
        **niuone_risk_budget(risk_state),
    }


def _theme_state(
    *,
    score: float,
    eligible: bool,
    strong_count: int,
    effective_count: float,
    previous: dict[str, Any],
    core_codes: list[str],
    as_of_date: str,
    previous_context_date: str,
    previous_trading_day: str,
) -> dict[str, Any]:
    if not eligible or score < 45:
        raw_state = "inactive"
    elif score < 55:
        raw_state = "fading"
    elif score >= 75 and strong_count >= 3 and effective_count >= 2.4:
        raw_state = "mainline"
    elif score >= 65 and strong_count >= 2 and effective_count >= 1.7:
        raw_state = "emerging"
    else:
        raw_state = "candidate"

    prior_state = str(previous.get("state") or "")
    prior_raw = str(previous.get("raw_state") or prior_state)
    prior_date = str(previous.get("as_of_date") or previous_context_date or "")[:10]
    same_day = bool(as_of_date and prior_date == as_of_date)
    consecutive_trading_day = bool(
        as_of_date
        and previous_trading_day
        and prior_date == previous_trading_day
        and prior_date != as_of_date
    )
    previous_core_codes = _theme_core_codes(previous)
    continued_core_codes = sorted(set(core_codes).intersection(previous_core_codes))
    core_overlap_count = len(continued_core_codes)
    overlap_base = min(len(core_codes), len(previous_core_codes))
    core_overlap_ratio = core_overlap_count / overlap_base if overlap_base else 0.0
    core_continuity_met = bool(
        consecutive_trading_day
        and core_overlap_count >= NIUONE_MIN_CROSS_DAY_CORE_OVERLAP
    )
    qualified_states = {"emerging", "mainline"}
    cross_day_persistent_now = bool(
        core_continuity_met
        and raw_state in qualified_states
        and prior_raw in qualified_states
    )
    prior_cross_day_persistent = bool(previous.get("cross_day_persistent"))
    cross_day_persistent = cross_day_persistent_now or bool(same_day and prior_cross_day_persistent)
    prior_mainline_confirmed = bool(
        previous.get("mainline_confirmed")
        or previous.get("cross_day_confirmed")
    )

    prior_confirmation = max(1, int(previous.get("confirmation_count") or 1))
    if raw_state in qualified_states:
        if same_day and prior_raw in qualified_states:
            confirmation = prior_confirmation
        elif cross_day_persistent_now:
            confirmation = prior_confirmation + 1
        else:
            confirmation = 1
    else:
        confirmation = 0
    intraday_confirmation = (
        int(previous.get("intraday_confirmation_count") or 1) + 1
        if same_day and prior_raw == raw_state
        else 1
    )

    if raw_state == "mainline":
        if cross_day_persistent_now or (same_day and prior_mainline_confirmed):
            state = "mainline"
        else:
            state = "emerging"
    elif raw_state == "emerging":
        if prior_mainline_confirmed and (same_day or consecutive_trading_day) and score >= 62:
            state = "diverging"
        else:
            state = "emerging"
    elif raw_state == "candidate" and prior_mainline_confirmed and (same_day or consecutive_trading_day):
        state = "diverging"
    elif (
        raw_state in {"fading", "inactive"}
        and prior_mainline_confirmed
        and (same_day or consecutive_trading_day)
        and score >= 45
    ):
        state = "fading"
    else:
        state = raw_state
    if same_day and prior_state == state:
        streak = max(1, int(previous.get("state_streak") or 1))
    elif consecutive_trading_day and prior_state == state:
        streak = max(1, int(previous.get("state_streak") or 1)) + 1
    else:
        streak = 1
    cross_day_confirmed = bool(
        state == "mainline"
        and (cross_day_persistent_now or (same_day and prior_mainline_confirmed))
    )
    mainline_confirmed = bool(
        cross_day_confirmed
        or (
            state in {"diverging", "fading"}
            and prior_mainline_confirmed
            and (same_day or core_continuity_met)
        )
    )
    intraday_state = "intraday_mainline" if raw_state == "mainline" and not cross_day_confirmed else raw_state
    return {
        "raw_state": raw_state,
        "state": state,
        "intraday_state": intraday_state,
        "confirmation_count": confirmation,
        "intraday_confirmation_count": intraday_confirmation,
        "state_streak": streak,
        "same_day_previous_scan": same_day,
        "consecutive_trading_day": consecutive_trading_day,
        "cross_day_persistent": cross_day_persistent,
        "cross_day_confirmed": cross_day_confirmed,
        "mainline_confirmed": mainline_confirmed,
        "previous_as_of_date": prior_date,
        "core_overlap_count": core_overlap_count,
        "core_overlap_ratio": round(core_overlap_ratio, 4),
        "core_continuity_met": core_continuity_met,
        "continued_core_codes": continued_core_codes,
    }


def build_niuone_context(
    prepared_items: Iterable[dict[str, Any]],
    *,
    reference_pool_count: int | None = None,
    market_snapshot: dict[str, Any] | None = None,
    flow_rows: Any = None,
    previous_context: dict[str, Any] | None = None,
    dragon_tiger_snapshot: dict[str, Any] | None = None,
    news_snapshot: dict[str, Any] | None = None,
    as_of_date: str = "",
    previous_trading_day: str = "",
    sample_at: str = "",
    reuse_previous_external_context: bool = False,
    theme_basis: str = "industry_proxy",
) -> dict[str, Any]:
    """Build a market-mainline context without forcing a winner.

    A member may belong to multiple themes. ``industry_proxy`` is
    retained for legacy callers; production supplies Eastmoney concept labels.
    """
    members: list[dict[str, Any]] = []
    insufficient_history_count = 0
    invalid_metrics_count = 0
    prepared_count = 0
    for item in prepared_items:
        prepared_count += 1
        raw_rows = item.get("rows")
        rows = (
            raw_rows
            if isinstance(raw_rows, Sequence)
            and not isinstance(raw_rows, (str, bytes, bytearray))
            else ()
        )
        if len(rows) < NIUONE_MIN_ROWS:
            insufficient_history_count += 1
            continue
        metric = _member_metrics(item)
        if metric is None:
            invalid_metrics_count += 1
            continue
        members.append(metric)
    resolved_reference_pool_count = max(
        prepared_count,
        int(reference_pool_count or 0),
    )
    unavailable_kline_count = max(0, resolved_reference_pool_count - prepared_count)
    resolved_theme_basis = str(theme_basis or "industry_proxy").strip() or "industry_proxy"
    previous_context = previous_context if isinstance(previous_context, dict) else {}
    previous_version = previous_context.get("version")
    if previous_version is not None and previous_version != NIUONE_CONTEXT_VERSION:
        previous_market = (
            dict(previous_context["market"])
            if isinstance(previous_context.get("market"), Mapping)
            else {}
        )
        previous_context = {"market": previous_market}
    as_of_date = str(as_of_date or "")[:10]
    previous_trading_day = str(previous_trading_day or "")[:10]
    sample_at = str(
        sample_at
        or (market_snapshot or {}).get("quote_time")
        or (market_snapshot or {}).get("captured_at")
        or ""
    )[:19]
    previous_context_date = str(previous_context.get("as_of_date") or "")[:10]
    market = _market_context(
        members,
        market_snapshot if isinstance(market_snapshot, dict) else {},
        previous_context.get("market") if isinstance(previous_context.get("market"), dict) else None,
    )
    ret5_population = [float(member["ret5"]) for member in members]
    ret20_population = [float(member["ret20"]) for member in members]
    volume_population = [float(member["volume_ratio"]) for member in members]
    amount_population = [float(member["amount"]) for member in members]
    sorted_ret5 = sorted(ret5_population)
    sorted_ret20 = sorted(ret20_population)
    sorted_volume = sorted(volume_population)
    sorted_amount = sorted(amount_population)
    for member in members:
        member["ret5_percentile"] = _percentile_from_sorted(float(member["ret5"]), sorted_ret5)
        member["ret20_percentile"] = _percentile_from_sorted(float(member["ret20"]), sorted_ret20)
        member["volume_percentile"] = _percentile_from_sorted(float(member["volume_ratio"]), sorted_volume)
        member["amount_percentile"] = _percentile_from_sorted(float(member["amount"]), sorted_amount)
        member["strong_score"] = _clamp(
            member["ret20_percentile"] * 0.30
            + member["ret5_percentile"] * 0.20
            + member["volume_percentile"] * 0.15
            + member["amount_percentile"] * 0.15
            + (100.0 if member["trend_aligned"] else (60.0 if member["above_ema20"] else 0.0)) * 0.10
            + (100.0 if member["new_high20"] else 0.0) * 0.10
        )
        member["strong"] = bool(
            member["strong_score"] >= NIUONE_STRONG_SCORE_THRESHOLD
            and (member["ret5"] > 0 or member["ret20"] > 0 or member["new_high20"])
        )
    market_returns = _market_return_path(members)

    dragon, dragon_stocks, news, news_stocks = _external_context(
        dragon_tiger_snapshot,
        news_snapshot,
        members,
    )
    if reuse_previous_external_context:
        if isinstance(previous_context.get("dragon_tiger"), Mapping):
            dragon = dict(previous_context["dragon_tiger"])
        if isinstance(previous_context.get("news"), Mapping):
            news = dict(previous_context["news"])
    previous_stocks = (
        previous_context.get("stocks")
        if isinstance(previous_context.get("stocks"), Mapping)
        else {}
    )
    missing_theme_count = sum(
        1 for member in members if not member.get("themes")
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for member in members:
        for theme_name in member.get("themes") or ():
            grouped[str(theme_name)].append(member)
    same_trading_day = bool(
        as_of_date
        and previous_context_date
        and as_of_date == previous_context_date
    )
    attribution_inputs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    market_breadth_pct = float(market.get("breadth_score") or 50.0)
    for theme_name, theme_members in grouped.items():
        peer_statistics = _theme_peer_statistics(theme_members)
        theme_return_factor = _theme_excess_return_factor(
            theme_members,
            market_returns=market_returns,
        )
        structural_ranked = sorted(
            theme_members,
            key=lambda member: float(member.get("strong_score") or 0.0),
            reverse=True,
        )
        structural_rank_by_code = {
            str(member.get("code") or ""): index
            for index, member in enumerate(structural_ranked, start=1)
            if member.get("code")
        }
        today_ranked = sorted(
            (member for member in theme_members if member.get("live_change_available")),
            key=lambda member: (
                float(member.get("change_pct") or 0.0),
                float(member.get("amount") or 0.0),
            ),
            reverse=True,
        )
        today_rank_by_code = {
            str(member.get("code") or ""): index
            for index, member in enumerate(today_ranked, start=1)
            if member.get("code")
        }
        for member in theme_members:
            code = str(member.get("code") or "")
            rank = structural_rank_by_code.get(code)
            today_rank = today_rank_by_code.get(code)
            return_correlation = _peer_return_correlation(
                member,
                theme_factor=theme_return_factor,
                market_returns=market_returns,
            )
            attribution_inputs[code].append({
                "industry": theme_name,
                "classification_industry": str(member.get("industry") or ""),
                "membership_source": resolved_theme_basis,
                "strong": bool(member.get("strong")),
                "theme_member_count": len(theme_members),
                "theme_rank": round(
                    100.0 - (rank - 1) / max(1, len(theme_members) - 1) * 100.0,
                    2,
                ) if rank is not None else 0.0,
                "today_rank_score": round(
                    100.0 - (today_rank - 1) / max(1, len(today_ranked) - 1) * 100.0,
                    2,
                ) if today_rank is not None else 0.0,
                "cohort_alignment_score": round(
                    _cohort_alignment_score(
                        member,
                        theme_members,
                        peer_statistics=peer_statistics,
                    ),
                    2,
                ),
                "peer_resonance_score": round(
                    _peer_resonance_score(
                        member,
                        theme_members,
                        market_breadth_pct=market_breadth_pct,
                        peer_statistics=peer_statistics,
                    ),
                    2,
                ),
                **return_correlation,
            })
    attributed_profiles_by_code: dict[str, dict[str, dict[str, Any]]] = {}
    for code, profiles in attribution_inputs.items():
        attributed_profiles_by_code[code] = {
            str(profile.get("industry") or ""): profile
            for profile in _apply_theme_attributions(
                profiles,
                previous_stock=(
                    previous_stocks.get(code)
                    if isinstance(previous_stocks.get(code), Mapping)
                    else None
                ),
                same_trading_day=same_trading_day,
            )
            if str(profile.get("industry") or "")
        }
    flows = _flow_map(flow_rows)
    flow_population = list(flows.values())
    theme_amounts = [
        sum(
            float(member["amount"])
            * float(
                (attributed_profiles_by_code.get(str(member.get("code") or ""), {}).get(theme_name) or {}).get("attribution_weight")
                or 0.0
            )
            for member in group
        )
        for theme_name, group in grouped.items()
    ]
    previous_themes = previous_context.get("themes") if isinstance(previous_context.get("themes"), dict) else {}
    themes: dict[str, dict[str, Any]] = {}
    stock_profiles: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for industry, theme_members in grouped.items():
        theme_attributions = {
            str(member.get("code") or ""): attributed_profiles_by_code.get(
                str(member.get("code") or ""), {}
            ).get(industry, {})
            for member in theme_members
        }
        today_metrics = _today_theme_metrics(
            theme_members,
            attributions=theme_attributions,
            market_breadth_pct=market_breadth_pct,
        )
        today_ranked_members = sorted(
            (member for member in theme_members if member.get("live_change_available")),
            key=lambda member: (float(member["change_pct"]), float(member["amount"])),
            reverse=True,
        )
        today_rank_by_code = {
            str(member.get("code") or ""): index
            for index, member in enumerate(today_ranked_members, start=1)
            if member.get("code")
        }
        def attribution_weight(member: Mapping[str, Any]) -> float:
            return max(0.0, float(
                (theme_attributions.get(str(member.get("code") or "")) or {}).get("attribution_weight")
                or 0.0
            ))

        attributed_member_count = sum(
            attribution_weight(member) for member in theme_members
        )
        raw_strong_members = [
            member for member in theme_members if member["strong"]
        ]
        strong_members = _rank_theme_leaders(
            raw_strong_members,
            theme_attributions,
            intraday=False,
        )
        attributed_strong_count = sum(
            attribution_weight(member) for member in raw_strong_members
        )
        weights = [
            max(1.0, float(member["strong_score"]))
            * math.sqrt(max(1.0, float(member["amount"])))
            * attribution_weight(member)
            for member in strong_members
        ]
        weight_total = sum(weights)
        normalized = [weight / weight_total for weight in weights] if weight_total > 0 else []
        concentration = max(normalized) if normalized else 0.0
        kish_count = 1.0 / sum(weight * weight for weight in normalized) if normalized else 0.0
        effective_count = min(kish_count, attributed_strong_count)
        effective_breadth_pct = _clamp(
            effective_count / attributed_member_count * 100
            if attributed_member_count > 0
            else 0.0
        )
        strong_count = len(strong_members)
        core_codes = [str(member["code"]) for member in strong_members[:NIUONE_CORE_STOCK_LIMIT] if member.get("code")]
        strong_ratio = (
            attributed_strong_count / attributed_member_count * 100
            if attributed_member_count > 0
            else 0.0
        )
        top_scores = [float(member["strong_score"]) for member in strong_members[:3]]
        strength_component = _mean(top_scores) * 0.25
        breadth_signal = _clamp(strong_ratio * 1.2) * 0.55 + _clamp(effective_count / 3 * 100) * 0.45
        breadth_component = breadth_signal * 0.20
        leadership_signal = (_mean(top_scores[:1]) * 0.45 + _mean(top_scores[1:3]) * 0.55) if top_scores else 0.0
        leadership_component = leadership_signal * 0.15
        amount = sum(
            float(member["amount"]) * attribution_weight(member)
            for member in theme_members
        )
        flow_value = _matched_flow(industry, flows)
        amount_percentile = _percentile(amount, theme_amounts)
        flow_score = _percentile(flow_value, flow_population) if flow_value is not None else amount_percentile
        capital_component = (flow_score * 0.65 + amount_percentile * 0.35) * 0.15
        previous = previous_themes.get(industry) if isinstance(previous_themes.get(industry), dict) else {}
        previous_score = safe_float(previous.get("score"))
        persistence_signal = 50.0
        if str(previous.get("state") or "") in {"mainline", "emerging"}:
            persistence_signal += 20.0
        if previous_score is not None:
            provisional = strength_component + breadth_component + leadership_component + capital_component
            persistence_signal += _clamp(provisional - previous_score, -20.0, 20.0)
        persistence_component = _clamp(persistence_signal) * 0.15
        dragon_values = [
            (
                float((dragon_stocks.get(str(member["code"])) or {}).get("strength") or 0.0),
                attribution_weight(member),
            )
            for member in strong_members
        ]
        confirmation_signal = _clamp(
            50
            + _weighted_mean(dragon_values) * 30
        )
        confirmation_component = confirmation_signal * 0.10
        if reuse_previous_external_context:
            previous_confirmation = safe_float(previous.get("confirmation_component"))
            if previous_confirmation is not None:
                confirmation_component = _clamp(previous_confirmation, 0.0, 10.0)
        concentration_penalty = _clamp((concentration - 0.45) / 0.45 * 10, 0.0, 10.0)
        sample_penalty = _clamp(
            (NIUONE_MIN_ATTRIBUTED_THEME_MASS - attributed_member_count)
            / NIUONE_MIN_ATTRIBUTED_THEME_MASS
            * 10.0,
            0.0,
            10.0,
        )
        score = _clamp(
            strength_component
            + breadth_component
            + leadership_component
            + capital_component
            + persistence_component
            + confirmation_component
            - concentration_penalty
            - sample_penalty
        )
        eligible = bool(
            len(theme_members) >= NIUONE_MIN_THEME_MEMBERS
            and attributed_member_count >= NIUONE_MIN_ATTRIBUTED_THEME_MASS
        )
        state_detail = _theme_state(
            score=score,
            eligible=eligible,
            strong_count=strong_count,
            effective_count=effective_count,
            previous=previous,
            core_codes=core_codes,
            as_of_date=as_of_date,
            previous_context_date=previous_context_date,
            previous_trading_day=previous_trading_day,
        )
        raw_state = str(state_detail["raw_state"])
        state = str(state_detail["state"])
        lifecycle = niuone_lifecycle_metadata(
            {
                "state": state,
                "score": score,
                "cross_day_persistent": state_detail[
                    "cross_day_persistent"
                ],
                "cross_day_confirmed": state_detail[
                    "cross_day_confirmed"
                ],
                "mainline_confirmed": state_detail[
                    "mainline_confirmed"
                ],
            },
            previous=previous,
        )
        themes[industry] = {
            "industry": industry,
            "theme_basis": resolved_theme_basis,
            "member_count": len(theme_members),
            "attributed_member_count": round(attributed_member_count, 4),
            **today_metrics,
            "eligible_data": eligible,
            "score": round(score, 2),
            "raw_state": raw_state,
            "state": state,
            **lifecycle,
            "intraday_state": state_detail["intraday_state"],
            "confirmation_count": state_detail["confirmation_count"],
            "intraday_confirmation_count": state_detail["intraday_confirmation_count"],
            "state_streak": state_detail["state_streak"],
            "as_of_date": as_of_date,
            "sample_at": sample_at,
            "previous_as_of_date": state_detail["previous_as_of_date"],
            "same_day_previous_scan": state_detail["same_day_previous_scan"],
            "consecutive_trading_day": state_detail["consecutive_trading_day"],
            "cross_day_persistent": state_detail["cross_day_persistent"],
            "cross_day_confirmed": state_detail["cross_day_confirmed"],
            "mainline_confirmed": state_detail["mainline_confirmed"],
            "core_stock_codes": core_codes,
            "core_overlap_count": state_detail["core_overlap_count"],
            "core_overlap_ratio": state_detail["core_overlap_ratio"],
            "core_continuity_met": state_detail["core_continuity_met"],
            "continued_core_codes": state_detail["continued_core_codes"],
            "previous_score": safe_round(previous_score, 2),
            "score_change": safe_round(score - previous_score, 2) if previous_score is not None else None,
            "raw_strong_stock_count": len(raw_strong_members),
            "strong_stock_count": strong_count,
            "attributed_strong_stock_count": round(attributed_strong_count, 4),
            "strong_stock_ratio": round(strong_ratio, 2),
            "effective_strong_count": round(effective_count, 2),
            "effective_breadth_pct": round(effective_breadth_pct, 2),
            "leader_concentration": round(concentration, 4),
            "single_stock_dominated": bool(strong_count == 1 or concentration > 0.70),
            "flow_net_yi": safe_round(flow_value, 2),
            "flow_source": "industry_net_flow" if flow_value is not None else "liquidity_fallback",
            "strength_component": round(strength_component, 2),
            "breadth_component": round(breadth_component, 2),
            "leadership_component": round(leadership_component, 2),
            "capital_component": round(capital_component, 2),
            "persistence_component": round(persistence_component, 2),
            "confirmation_component": round(confirmation_component, 2),
            "concentration_penalty": round(concentration_penalty, 2),
            "strong_stocks": [
                {
                    "code": member["code"],
                    "name": member["name"],
                    "strong_score": round(float(member["strong_score"]), 2),
                    "change_pct": round(float(member["change_pct"]), 2),
                    "attribution_score": (theme_attributions.get(str(member.get("code") or "")) or {}).get("attribution_score"),
                    "attribution_weight": (theme_attributions.get(str(member.get("code") or "")) or {}).get("attribution_weight"),
                    "role": "leader" if index == 0 else "core",
                    "leader_rank": index + 1,
                    "leader_tier": index < NIUONE_LEADER_TIER_LIMIT,
                }
                for index, member in enumerate(strong_members[:NIUONE_CORE_STOCK_LIMIT])
            ],
        }

        theme_ret5 = sorted(float(member["ret5"]) for member in theme_members)
        theme_ret20 = sorted(float(member["ret20"]) for member in theme_members)
        theme_amounts_sorted = sorted(
            float(member["amount"]) for member in theme_members
        )
        attributed_leader_rank_by_code = {
            str(member.get("code") or ""): index
            for index, member in enumerate(strong_members, start=1)
            if member.get("code")
        }
        attributed_today_rank_by_code = {
            str(item.get("code") or ""): index
            for index, item in enumerate(today_metrics.get("today_leaders") or [], start=1)
            if item.get("code")
        }
        for rank_index, member in enumerate(sorted(theme_members, key=lambda item: float(item["strong_score"]), reverse=True), start=1):
            code = str(member["code"])
            market_amount_percentile = float(member["amount_percentile"])
            theme_amount_percentile = _percentile_from_sorted(
                float(member["amount"]),
                theme_amounts_sorted,
            )
            activity_score = _clamp(
                market_amount_percentile * 0.65
                + theme_amount_percentile * 0.25
                + float(member["volume_percentile"]) * 0.10
            )
            activity_confirmed = bool(
                member.get("amount_available")
                and niuone_turnover_blocker(member.get("turnover")) is None
                and market_amount_percentile
                >= NIUONE_MATURE_MIN_MARKET_AMOUNT_PERCENTILE
                and theme_amount_percentile
                >= NIUONE_MATURE_MIN_THEME_AMOUNT_PERCENTILE
            )
            today_rank = attributed_today_rank_by_code.get(code)
            today_rank_score = (
                100 - (today_rank - 1) / max(1, len(today_ranked_members) - 1) * 100
                if today_rank is not None
                else 0.0
            )
            reversal_strong = bool(
                today_rank is not None
                and today_rank <= NIUONE_LEADER_TIER_LIMIT
                and float(member.get("change_pct") or 0.0) >= 1.5
                and float(member.get("rebound_from_low_pct") or 0.0) >= NIUONE_REVERSAL_MIN_REBOUND_PCT
                and member.get("reclaim_previous_close") is True
            )
            dragon_stock = dragon_stocks.get(code) or {}
            news_stock = news_stocks.get(code) or {}
            attribution = theme_attributions.get(code) or {}
            leader_rank = attributed_leader_rank_by_code.get(code)
            role = (
                "leader"
                if leader_rank == 1
                else "core"
                if leader_rank is not None
                else "follower"
            )
            stock_profiles[code].append({
                "industry": industry,
                "classification_industry": str(member.get("industry") or ""),
                "theme_basis": resolved_theme_basis,
                "theme_membership_source": attribution.get(
                    "membership_source",
                    resolved_theme_basis,
                ),
                "theme_state": state,
                "theme_score": round(score, 2),
                "theme_member_count": attribution.get(
                    "theme_member_count"
                ),
                "cohort_alignment_score": attribution.get("cohort_alignment_score"),
                "peer_resonance_score": attribution.get("peer_resonance_score"),
                "return_correlation_score": attribution.get(
                    "return_correlation_score"
                ),
                "return_correlation_rank_score": attribution.get(
                    "return_correlation_rank_score"
                ),
                "return_correlation_observation_count": attribution.get(
                    "return_correlation_observation_count"
                ),
                "return_correlation_peer_count": attribution.get(
                    "return_correlation_peer_count"
                ),
                "theme_specificity_score": attribution.get(
                    "theme_specificity_score"
                ),
                "current_attribution_score": attribution.get("current_attribution_score"),
                "historical_prior_score": attribution.get("historical_prior_score"),
                "attribution_score": attribution.get("attribution_score"),
                "attribution_weight": attribution.get("attribution_weight"),
                "leadership_eligible": _theme_leadership_eligible(attribution),
                "unattributed_weight": attribution.get("unattributed_weight"),
                "attribution_observation_count": attribution.get("attribution_observation_count"),
                "attribution_wave_count": attribution.get("attribution_wave_count"),
                "strong_score": round(float(member["strong_score"]), 2),
                "amount": round(float(member["amount"]), 2),
                "amount_available": bool(member.get("amount_available")),
                "turnover": member.get("turnover"),
                "market_amount_percentile": round(
                    market_amount_percentile,
                    2,
                ),
                "theme_amount_percentile": round(
                    theme_amount_percentile,
                    2,
                ),
                "volume_participation_percentile": round(
                    float(member["volume_percentile"]),
                    2,
                ),
                "activity_score": round(activity_score, 2),
                "activity_confirmed": activity_confirmed,
                "activity_gate_required": True,
                "strong": bool(
                    member["strong"]
                    and _theme_leadership_eligible(attribution)
                ),
                "role": role,
                "leader_rank": leader_rank,
                "leader_tier": bool(
                    leader_rank is not None
                    and leader_rank <= NIUONE_LEADER_TIER_LIMIT
                ),
                "today_leader_rank": today_rank,
                "today_leader_tier": bool(
                    today_rank is not None and today_rank <= NIUONE_LEADER_TIER_LIMIT
                ),
                "today_rank_score": round(today_rank_score, 2),
                "reversal_strong": reversal_strong,
                "prior_ret5": safe_round(member.get("prior_ret5"), 2),
                "intraday_low": safe_round(member.get("intraday_low"), 3),
                "rebound_from_low_pct": safe_round(member.get("rebound_from_low_pct"), 2),
                "reclaim_previous_close": bool(member.get("reclaim_previous_close")),
                "theme_rank": round(100 - (rank_index - 1) / max(1, len(theme_members) - 1) * 100, 2),
                "theme_ret5_rank": round(
                    _percentile_from_sorted(float(member["ret5"]), theme_ret5), 2
                ),
                "theme_ret20_rank": round(
                    _percentile_from_sorted(float(member["ret20"]), theme_ret20), 2
                ),
                "market_rank": round(float(member["ret20_percentile"]), 2),
                "dragon_tiger_listed": bool(dragon_stock.get("listed")),
                "dragon_tiger_signal": dragon_stock.get("signal", "neutral"),
                "dragon_tiger_score": dragon_stock.get("score", 50.0),
                "dragon_tiger_adjustment": round(float(dragon_stock.get("strength") or 0.0) * 0.25, 3),
                "news_precheck": {
                    "code": code,
                    "name": member.get("name") or "",
                    "checked": bool(news_stock.get("checked")),
                    "available": bool(news_stock.get("available")),
                    "tone": news_stock.get("tone", "neutral"),
                    "tone_label": news_stock.get("tone_label", "中性"),
                    "summary": news_stock.get("summary", ""),
                    "fetched_at": news_stock.get("fetched_at", ""),
                    "window_days": news_stock.get("window_days", 3),
                    "error": news_stock.get("error", ""),
                },
                "news_adjustment": float(news_stock.get("adjustment") or 0.0),
            })

    state_priority = {
        "mainline": 4,
        "emerging": 3,
        "candidate": 2,
        "diverging": 1,
        "fading": 0,
    }
    stocks: dict[str, dict[str, Any]] = {}
    for code, profiles in stock_profiles.items():
        attributed_profiles = profiles
        ordered_profiles = sorted(
            attributed_profiles,
            key=lambda profile: (
                -state_priority.get(str(profile.get("theme_state") or ""), -1),
                -float(profile.get("theme_score") or 0.0),
                -float(profile.get("attribution_score") or 0.0),
                -float(profile.get("strong_score") or 0.0),
                -float(profile.get("theme_rank") or 0.0),
                str(profile.get("industry") or ""),
            ),
        )
        selected = dict(ordered_profiles[0])
        selected["theme_memberships"] = [
            str(profile.get("industry") or "")
            for profile in ordered_profiles
            if str(profile.get("industry") or "")
        ]
        attribution_ordered = sorted(
            attributed_profiles,
            key=lambda profile: (
                -float(profile.get("attribution_score") or 0.0),
                -float(profile.get("theme_rank") or 0.0),
                str(profile.get("industry") or ""),
            ),
        )
        leading_attribution = attribution_ordered[0]
        second_attribution_score = (
            float(attribution_ordered[1].get("attribution_score") or 0.0)
            if len(attribution_ordered) > 1
            else None
        )
        attribution_gap = (
            float(leading_attribution.get("attribution_score") or 0.0)
            - second_attribution_score
            if second_attribution_score is not None
            else None
        )
        selected["dominant_theme"] = str(
            leading_attribution.get("industry") or ""
        )
        selected["theme_attribution_confident"] = bool(
            len(attribution_ordered) == 1
            or (
                float(leading_attribution.get("attribution_score") or 0.0)
                >= NIUONE_THEME_ATTRIBUTION_CONFIDENCE_SCORE
                and float(leading_attribution.get("attribution_weight") or 0.0)
                >= 0.45
                and attribution_gap is not None
                and attribution_gap
                >= NIUONE_THEME_ATTRIBUTION_CONFIDENCE_GAP
            )
        )
        selected["theme_attribution_gap"] = safe_round(
            attribution_gap,
            2,
        )
        selected["unattributed_theme_weight"] = leading_attribution.get(
            "unattributed_weight"
        )
        selected["theme_attributions"] = [
            {
                "theme": str(profile.get("industry") or ""),
                "theme_member_count": profile.get("theme_member_count"),
                "membership_source": profile.get("theme_membership_source"),
                "current_score": profile.get("current_attribution_score"),
                "historical_prior_score": profile.get(
                    "historical_prior_score"
                ),
                "attribution_score": profile.get("attribution_score"),
                "attribution_weight": profile.get("attribution_weight"),
                "leadership_eligible": profile.get("leadership_eligible"),
                "cohort_alignment_score": profile.get(
                    "cohort_alignment_score"
                ),
                "peer_resonance_score": profile.get(
                    "peer_resonance_score"
                ),
                "return_correlation_score": profile.get(
                    "return_correlation_score"
                ),
                "return_correlation_rank_score": profile.get(
                    "return_correlation_rank_score"
                ),
                "return_correlation_observation_count": profile.get(
                    "return_correlation_observation_count"
                ),
                "return_correlation_peer_count": profile.get(
                    "return_correlation_peer_count"
                ),
                "theme_specificity_score": profile.get(
                    "theme_specificity_score"
                ),
                "observation_count": profile.get(
                    "attribution_observation_count"
                ),
                "wave_count": profile.get("attribution_wave_count"),
            }
            for profile in attribution_ordered
        ]
        # A stock may lead one concept while being only a follower in another.
        # Keep the compact per-theme routing fields so every NiuOne action can
        # choose a lifecycle-compatible branch instead of inheriting one global
        # profile selected before the action is known.  External/news fields are
        # intentionally not duplicated for every membership.
        selected["theme_profiles"] = [
            {
                key: profile.get(key)
                for key in (
                    "industry",
                    "classification_industry",
                    "theme_basis",
                    "theme_membership_source",
                    "theme_state",
                    "theme_score",
                    "theme_member_count",
                    "strong",
                    "strong_score",
                    "amount",
                    "amount_available",
                    "turnover",
                    "market_amount_percentile",
                    "theme_amount_percentile",
                    "volume_participation_percentile",
                    "activity_score",
                    "activity_confirmed",
                    "activity_gate_required",
                    "role",
                    "leader_rank",
                    "leader_tier",
                    "today_leader_rank",
                    "today_leader_tier",
                    "today_rank_score",
                    "reversal_strong",
                    "theme_rank",
                    "theme_ret5_rank",
                    "theme_ret20_rank",
                    "cohort_alignment_score",
                    "peer_resonance_score",
                    "return_correlation_score",
                    "return_correlation_rank_score",
                    "return_correlation_observation_count",
                    "return_correlation_peer_count",
                    "theme_specificity_score",
                    "current_attribution_score",
                    "historical_prior_score",
                    "attribution_score",
                    "attribution_weight",
                    "unattributed_weight",
                    "attribution_observation_count",
                    "attribution_wave_count",
                )
            }
            for profile in ordered_profiles
        ]
        stocks[code] = selected

    ordered = sorted(themes.values(), key=lambda theme: float(theme["score"]), reverse=True)
    confirmed = [theme for theme in ordered if theme["state"] == "mainline"]
    intraday = [theme for theme in ordered if theme.get("intraday_state") == "intraday_mainline"]
    today_ordered = sorted(
        (theme for theme in themes.values() if theme.get("today_eligible_data")),
        key=lambda theme: (
            float(theme.get("today_strength_score") or 0.0),
            float(theme.get("today_median_change_pct") or 0.0),
        ),
        reverse=True,
    )
    today_primary = (
        today_ordered[0]
        if today_ordered
        and float(today_ordered[0].get("today_strength_score") or 0.0) >= NIUONE_TODAY_OBSERVATION_THRESHOLD
        else None
    )
    primary = confirmed[0] if confirmed else None

    def driver_codes(theme: Mapping[str, Any]) -> set[str]:
        return {
            str(item.get("code") or "")
            for item in list(theme.get("strong_stocks") or [])[:3]
            if isinstance(item, Mapping) and item.get("code")
        }

    secondary = None
    if primary is not None:
        primary_drivers = driver_codes(primary)
        for candidate in confirmed[1:]:
            if float(primary["score"]) - float(candidate["score"]) > 8:
                break
            candidate_drivers = driver_codes(candidate)
            if not primary_drivers or not candidate_drivers:
                secondary = candidate
                break
            overlap = len(primary_drivers.intersection(candidate_drivers))
            overlap_base = min(len(primary_drivers), len(candidate_drivers))
            if overlap / overlap_base < 0.6:
                secondary = candidate
                break
    summary = {
        "mode": "dual" if secondary else ("single" if primary else "none"),
        "primary": primary["industry"] if primary else "",
        "primary_score": primary["score"] if primary else None,
        "secondary": secondary["industry"] if secondary else "",
        "secondary_score": secondary["score"] if secondary else None,
        "score_gap": round(float(ordered[0]["score"]) - float(ordered[1]["score"]), 2) if len(ordered) > 1 else None,
        "reason": "强势股形成多点共振" if primary else "尚无主题完成主线确认",
        "intraday_primary": intraday[0]["industry"] if intraday else "",
        "intraday_primary_score": intraday[0]["score"] if intraday else None,
        "observation_reason": (
            "日内强势仅作观察，等待下一交易日核心股延续"
            if intraday and not primary
            else ""
        ),
        "today_primary": today_primary["industry"] if today_primary else "",
        "today_primary_score": today_primary["today_strength_score"] if today_primary else None,
        "today_primary_breadth_pct": (
            today_primary.get("today_adjusted_breadth_pct")
            if today_primary
            else None
        ),
        "today_observation_reason": (
            "今日强度仅作观察，不改变原有跨日主线确认门槛"
            if today_primary
            else ""
        ),
    }
    covered_count = len(stocks)
    uncovered_count = max(0, resolved_reference_pool_count - covered_count)
    coverage_reasons = [
        {
            "key": "kline_unavailable",
            "label": "K线不可用或少于30根",
            "count": unavailable_kline_count,
            "description": "行情请求失败、返回空数据，或可用日K少于30根",
        },
        {
            "key": "insufficient_history",
            "label": "历史不足55根",
            "count": insufficient_history_count,
            "description": "已有日K不少于30根，但未达到题材强度计算要求的55根",
        },
        {
            "key": "invalid_metrics",
            "label": "关键指标无效",
            "count": invalid_metrics_count,
            "description": "收盘价或5日、20日收益等关键输入无法形成有效指标",
        },
        {
            "key": "industry_unmapped",
            "label": "题材映射缺失",
            "count": missing_theme_count,
            "description": "强度指标有效，但没有可用于题材聚类的东方财富概念或行业归属",
        },
    ]
    classified_uncovered_count = sum(int(reason["count"]) for reason in coverage_reasons)
    if classified_uncovered_count < uncovered_count:
        coverage_reasons.append({
            "key": "other",
            "label": "其他数据不完整",
            "count": uncovered_count - classified_uncovered_count,
            "description": "未归入已知数据质量分类",
        })
    return {
        "version": NIUONE_CONTEXT_VERSION,
        "strategy": "niuone",
        "theme_basis": resolved_theme_basis,
        "as_of_date": as_of_date,
        "previous_trading_day": previous_trading_day,
        "sample_at": sample_at,
        "market": market,
        "mainline": summary,
        "theme_count": len(themes),
        "mapped_stock_count": covered_count,
        "strong_stock_count": sum(1 for member in members if member["strong"]),
        "data_coverage": (
            round(covered_count / resolved_reference_pool_count, 4)
            if resolved_reference_pool_count
            else 0.0
        ),
        "coverage_diagnostics": {
            "reference_pool_count": resolved_reference_pool_count,
            "prepared_stock_count": prepared_count,
            "covered_stock_count": covered_count,
            "uncovered_stock_count": uncovered_count,
            "reasons": coverage_reasons,
        },
        "dragon_tiger": dragon,
        "news": news,
        "themes": themes,
        "stocks": stocks,
    }


def _action_theme_profile(
    stock: Mapping[str, Any],
    context: Mapping[str, Any],
    strategy_name: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Choose the best concept membership that matches an entry action.

    The old global stock profile preferred the most mature membership before
    the action was known.  For multi-concept names that could route a startup
    leader through an unrelated mature theme where it was only a follower.
    Selection is now action-aware while retaining a deterministic fallback so
    blocked candidates still expose useful diagnostics.
    """
    themes = context.get("themes") if isinstance(context.get("themes"), Mapping) else {}
    raw_profiles = stock.get("theme_profiles")
    profiles = (
        [dict(item) for item in raw_profiles if isinstance(item, Mapping)]
        if isinstance(raw_profiles, list)
        else []
    )
    if not profiles:
        profiles = [dict(stock)]

    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for profile in profiles:
        industry = _industry_name(profile.get("industry"))
        theme = themes.get(industry) if isinstance(themes, Mapping) else None
        if not industry or not isinstance(theme, Mapping):
            continue
        merged = dict(stock)
        merged.update(profile)
        candidates.append((merged, dict(theme)))
    if not candidates:
        return None
    attributed_candidates = [
        item
        for item in candidates
        if _theme_leadership_eligible(item[0])
    ]
    if attributed_candidates:
        candidates = attributed_candidates

    def action_compatible(item: tuple[dict[str, Any], dict[str, Any]]) -> bool:
        profile, theme = item
        if niuone_lifecycle_entry_blocker(strategy_name, theme) is not None:
            return False
        state = str(theme.get("state") or "")
        confirmed = bool(
            theme.get("mainline_confirmed")
            or theme.get("cross_day_confirmed")
        )
        if strategy_name == "niu_reversal_probe":
            return bool(
                state in {"candidate", "emerging"}
                and not confirmed
                and not (state == "candidate" and profile.get("strong") is True)
            )
        if strategy_name == "niu_emerging":
            return bool(
                state == "emerging"
                and theme.get("cross_day_persistent") is True
            )
        if strategy_name in {"niu_leader", "niu_pullback"}:
            return bool(state in {"mainline", "diverging"} and confirmed)
        return True

    compatible = [item for item in candidates if action_compatible(item)]
    routed = compatible or candidates
    state_priority = {
        "mainline": 5,
        "diverging": 4,
        "emerging": 3,
        "candidate": 2,
        "fading": 1,
        "inactive": 0,
    }
    routed.sort(
        key=lambda item: (
            -float(item[0].get("attribution_score") or 0.0),
            -int(item[0].get("leader_tier") is True),
            -float(item[0].get("theme_rank") or 0.0),
            -state_priority.get(str(item[1].get("state") or ""), -1),
            -float(item[1].get("score") or 0.0),
            str(item[0].get("industry") or ""),
        )
    )
    return routed[0]


def _shared_entry_metrics(
    rows: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Compute price/indicator inputs shared by all four NiuOne actions."""
    if len(rows) < NIUONE_MIN_ROWS:
        return None
    latest = rows[-1]
    close = safe_float(latest.get("quote_price"))
    if close is None or close <= 0:
        close = safe_float(latest.get("close"))
    ema20 = safe_float(latest.get("ema20"))
    ema50 = safe_float(latest.get("ema50"))
    atr = _atr(rows)
    if (
        close is None
        or close <= 0
        or ema20 is None
        or ema50 is None
        or atr is None
        or atr <= 0
    ):
        return None
    prior_ema20 = safe_float(rows[-2].get("ema20"))
    prior_close = safe_float(rows[-2].get("close")) or close
    prior_highs = [safe_float(row.get("high")) for row in rows[-21:-1]]
    highs = [value for value in prior_highs if value is not None and value > 0]
    breakout_level = max(highs) if highs else None
    current_volume = safe_float(latest.get("volume")) or 0.0
    prior_volumes = [safe_float(row.get("volume")) for row in rows[-21:-1]]
    volumes = [value for value in prior_volumes if value is not None and value > 0]
    volume_ratio = current_volume / _mean(volumes) if volumes else 1.0
    live_change = safe_float(latest.get("quote_change_pct"))
    change_pct = (
        live_change
        if live_change is not None
        else (safe_float(latest.get("change_pct")) or 0.0)
    )
    breakout = bool(
        breakout_level is not None
        and close >= breakout_level * 1.002
        and 1.15 <= volume_ratio <= 2.5
    )
    recent_lows = [safe_float(row.get("low")) for row in rows[-4:]]
    lows = [value for value in recent_lows if value is not None and value > 0]
    pullback = bool(
        lows
        and min(lows) <= ema20 * 1.02
        and close >= ema20
        and volume_ratio <= 1.15
        and change_pct >= -0.8
    )
    reclaim = bool(
        prior_close <= ema20 * 1.01
        and close > ema20
        and change_pct > 0
        and volume_ratio >= 1.0
    )
    trend_aligned = bool(
        close >= ema20 >= ema50
        and (prior_ema20 is None or ema20 >= prior_ema20)
    )
    previous_close = safe_float(rows[-2].get("close"))
    reclaim_previous_close = bool(
        previous_close is not None
        and previous_close > 0
        and close > previous_close
        and change_pct > 0
    )
    structure_low = min(lows) if lows else close - atr * 1.5
    stop_distance = structural_stop_distance_pct(close, structure_low)
    stop_atr = (close - structure_low) / atr
    gap_buffer = downside_gap_buffer_pct(rows, atr=atr, close=close)
    effective_distance = effective_loss_distance_pct(
        close,
        structure_low,
        gap_buffer_pct=gap_buffer,
        execution_buffer_pct=SECTOR_TIDE_EXECUTION_BUFFER_PCT,
    )
    extension_atr = (close - ema20) / atr
    breakout_extension_atr = (
        max(0.0, (close - breakout_level) / atr)
        if breakout_level is not None
        else None
    )
    breakout_stop_price = (
        breakout_level - atr * 0.5
        if breakout_level is not None and breakout_level - atr * 0.5 > 0
        else None
    )
    return MappingProxyType({
        "close": close,
        "ema20": ema20,
        "ema50": ema50,
        "atr": atr,
        "atr_period": NIUONE_ATR_LOOKBACK,
        "atr20": atr,
        "distance_pct": (close / ema20 - 1) * 100,
        "extension_atr": extension_atr,
        "breakout_level": breakout_level,
        "breakout_extension_atr": breakout_extension_atr,
        "breakout_stop_price": breakout_stop_price,
        "volume_ratio": volume_ratio,
        "change_pct": change_pct,
        "breakout": breakout,
        "pullback": pullback,
        "reclaim": reclaim,
        "row_intraday_low": safe_float(latest.get("low")),
        "reclaim_previous_close": reclaim_previous_close,
        **_daily_v_reversal_metrics(rows, current_close=close),
        "trend_aligned": trend_aligned,
        "stop_price": structure_low,
        "stop_distance_pct": stop_distance,
        "stop_atr": stop_atr,
        "gap_buffer_pct": gap_buffer,
        "effective_loss_distance_pct": effective_distance,
    })


def _entry_metrics(
    rows: Sequence[Mapping[str, Any]],
    context: dict[str, Any],
    strategy_name: str,
    *,
    shared_metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if len(rows) < NIUONE_MIN_ROWS or not isinstance(context, dict):
        return None
    latest = rows[-1]
    common = shared_metrics if shared_metrics is not None else _shared_entry_metrics(rows)
    if not isinstance(common, Mapping):
        return None
    code = _stock_code(latest.get("symbol_code"))
    source_stock = (context.get("stocks") or {}).get(code)
    routed = (
        _action_theme_profile(source_stock, context, strategy_name)
        if isinstance(source_stock, Mapping)
        else None
    )
    if routed is not None:
        stock, theme = routed
    elif isinstance(source_stock, Mapping):
        # Compatibility for older cached/manual contexts whose stock payload
        # predates per-theme routing fields and relies on the row's industry.
        stock = dict(source_stock)
        fallback_industry = _industry_name(
            stock.get("industry") or latest.get("industry")
        )
        fallback_theme = (context.get("themes") or {}).get(fallback_industry)
        theme = dict(fallback_theme) if isinstance(fallback_theme, Mapping) else None
    else:
        stock, theme = None, None
    industry = _industry_name(
        (stock.get("industry") or latest.get("industry"))
        if isinstance(stock, dict) else latest.get("industry")
    )
    market = context.get("market") if isinstance(context.get("market"), dict) else {}
    if not isinstance(theme, dict) or not isinstance(stock, dict):
        return None
    if "quote_turnover" in latest:
        # Holding-only rescans retain the full-market amount ranks, but must
        # use this scan's actual turnover, including a missing fresh value.
        stock["turnover"] = latest.get("quote_turnover")
        stock["activity_confirmed"] = niuone_stock_activity_blocker(
            strategy_name,
            {
                "stock_activity_data_available": stock.get("amount_available"),
                "stock_market_amount_percentile": stock.get("market_amount_percentile"),
                "stock_theme_amount_percentile": stock.get("theme_amount_percentile"),
                "turnover": stock.get("turnover"),
            },
        ) is None
    close = float(common["close"])
    atr = float(common["atr"])
    intraday_low = safe_float(common.get("row_intraday_low"))
    if intraday_low is None or intraday_low <= 0:
        intraday_low = safe_float(stock.get("intraday_low"))
    rebound_from_low_pct = (
        (close / intraday_low - 1) * 100
        if intraday_low is not None and intraday_low > 0
        else 0.0
    )
    regime = str(market.get("risk_state") or market.get("state") or "defensive")
    structural_limits = niuone_structural_stop_limits(regime)
    risk_ok = niuone_structure_risk_ok(
        float(common["stop_distance_pct"]),
        float(common["stop_atr"]),
        regime,
    )
    theme_score = safe_float(theme.get("score"))
    stock_score = safe_float(stock.get("strong_score"))
    if theme_score is None or stock_score is None:
        return None
    score_before_external = (theme_score * 0.55 + stock_score * 0.45) / 10
    raw_external = float(stock.get("dragon_tiger_adjustment") or 0.0) + float(stock.get("news_adjustment") or 0.0)
    positive_suppressed = bool(
        raw_external > 0 and float(common["extension_atr"]) > 1.5
    )
    external = 0.0 if positive_suppressed else _clamp(raw_external, -0.6, 0.4)
    return {
        "code": code,
        "industry": industry,
        "theme": theme,
        "stock": stock,
        "market": market,
        "mainline": context.get("mainline") if isinstance(context.get("mainline"), dict) else {},
        "dragon_tiger": context.get("dragon_tiger") if isinstance(context.get("dragon_tiger"), dict) else {},
        "news": context.get("news") if isinstance(context.get("news"), dict) else {},
        **{
            key: value
            for key, value in common.items()
            if key != "row_intraday_low"
        },
        "intraday_low": intraday_low,
        "rebound_from_low_pct": rebound_from_low_pct,
        "max_stop_distance_pct": structural_limits["max_stop_distance_pct"],
        "max_stop_atr": structural_limits["max_stop_atr"],
        "risk_ok": risk_ok,
        "score_before_external_context": score_before_external,
        "raw_external_context_adjustment": raw_external,
        "external_context_adjustment": external,
        "external_positive_suppressed": positive_suppressed,
        "composite_score": _clamp(score_before_external + external, 0.0, 10.0),
    }


def _strategy_entry_geometry(
    strategy_name: str,
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve chase distance and stop from the entry setup's own price anchor."""
    reversal_probe = strategy_name == "niu_reversal_probe"
    breakout_entry = bool(
        strategy_name in {"niu_leader", "niu_emerging"}
        and metrics.get("breakout")
        and safe_float(metrics.get("breakout_level")) is not None
    )
    if reversal_probe:
        stop_price = safe_float(metrics.get("daily_v_stop_price"))
        stop_source = "niu_reversal_right_low"
        entry_extension_atr = safe_float(metrics.get("extension_atr"))
        entry_extension_source = "ema20"
        entry_setup = "daily_v_reversal"
    elif breakout_entry:
        stop_price = safe_float(metrics.get("breakout_stop_price"))
        stop_source = "niu_breakout_pivot"
        entry_extension_atr = safe_float(metrics.get("breakout_extension_atr"))
        entry_extension_source = "breakout_level"
        entry_setup = "breakout"
    else:
        stop_price = safe_float(metrics.get("stop_price"))
        stop_source = "niu_structure_low"
        entry_extension_atr = safe_float(metrics.get("extension_atr"))
        entry_extension_source = "ema20"
        entry_setup = (
            "pullback" if metrics.get("pullback")
            else "reclaim" if metrics.get("reclaim")
            else "none"
        )
    if stop_price is None or stop_price <= 0:
        stop_price = safe_float(metrics.get("stop_price"))
        stop_source = "niu_structure_low"
    return {
        "stop_price": stop_price,
        "stop_source": stop_source,
        "entry_extension_atr": entry_extension_atr,
        "entry_extension_source": entry_extension_source,
        "entry_setup": entry_setup,
    }


def _payload(
    strategy_name: str,
    metrics: dict[str, Any],
    *,
    verdict: str,
    risk_flags: list[str],
) -> dict[str, Any]:
    theme = metrics["theme"]
    stock = metrics["stock"]
    market = metrics["market"]
    mainline = metrics["mainline"]
    selected_mainlines = {
        str(mainline.get("primary") or ""),
        str(mainline.get("secondary") or ""),
    }
    regime = str(market.get("risk_state") or market.get("state") or "")
    budget = niuone_risk_budget(regime, strategy_name)
    chase_limits = niuone_chase_limits(
        strategy_name,
        str(market.get("risk_state") or market.get("state") or ""),
    )
    absolute_cap = NIUONE_ABSOLUTE_POSITION_CAP_PCT[strategy_name]
    geometry = _strategy_entry_geometry(strategy_name, metrics)
    stop_price = geometry["stop_price"]
    stop_distance_pct = structural_stop_distance_pct(metrics["close"], stop_price)
    stop_atr = (
        (metrics["close"] - stop_price) / metrics["atr"]
        if stop_price is not None and stop_price > 0 and metrics["atr"] > 0
        else 0.0
    )
    structural_limits = niuone_structural_stop_limits(regime, strategy_name)
    risk_ok = niuone_structure_risk_ok(
        stop_distance_pct,
        stop_atr,
        regime,
        strategy_name,
    )
    effective_loss_distance = effective_loss_distance_pct(
        metrics["close"],
        stop_price,
        gap_buffer_pct=metrics["gap_buffer_pct"],
        execution_buffer_pct=SECTOR_TIDE_EXECUTION_BUFFER_PCT,
    )
    dynamic_cap = risk_sized_position_cap_pct(
        per_trade_risk_pct=budget["per_trade_risk_pct"],
        effective_loss_distance_pct_value=effective_loss_distance,
        absolute_cap_pct=absolute_cap,
    )
    news_precheck = stock.get("news_precheck") if isinstance(stock.get("news_precheck"), dict) else {}
    theme_attributions = [
        dict(item)
        for item in (stock.get("theme_attributions") or [])
        if isinstance(item, Mapping)
    ]
    selected_attribution = next(
        (
            item
            for item in theme_attributions
            if _industry_name(item.get("theme")) == metrics["industry"]
        ),
        {},
    )
    return {
        "score": metrics.get("strategy_score", metrics["composite_score"]),
        "score_total": 10,
        "verdict": verdict,
        # ``industry`` remains a compatibility alias for direct scorer and
        # backtest callers. Production candidate rows split the factual
        # Eastmoney f100 industry from this action-selected concept.
        "industry": metrics["industry"],
        "classification_industry": str(
            stock.get("classification_industry") or ""
        ),
        "signal_theme": metrics["industry"],
        "theme_memberships": list(stock.get("theme_memberships") or []),
        "theme_attributions": theme_attributions,
        "signal_theme_attribution_score": selected_attribution.get(
            "attribution_score",
            stock.get("attribution_score"),
        ),
        "signal_theme_attribution_weight": selected_attribution.get(
            "attribution_weight",
            stock.get("attribution_weight"),
        ),
        "signal_theme_historical_prior_score": selected_attribution.get(
            "historical_prior_score",
            stock.get("historical_prior_score"),
        ),
        "signal_theme_cohort_alignment_score": selected_attribution.get(
            "cohort_alignment_score",
            stock.get("cohort_alignment_score"),
        ),
        "signal_theme_peer_resonance_score": selected_attribution.get(
            "peer_resonance_score",
            stock.get("peer_resonance_score"),
        ),
        "signal_theme_return_correlation_score": selected_attribution.get(
            "return_correlation_score",
            stock.get("return_correlation_score"),
        ),
        "signal_theme_return_correlation_rank_score": selected_attribution.get(
            "return_correlation_rank_score",
            stock.get("return_correlation_rank_score"),
        ),
        "signal_theme_return_correlation_observation_count": selected_attribution.get(
            "return_correlation_observation_count",
            stock.get("return_correlation_observation_count"),
        ),
        "signal_theme_return_correlation_peer_count": selected_attribution.get(
            "return_correlation_peer_count",
            stock.get("return_correlation_peer_count"),
        ),
        "signal_theme_specificity_score": selected_attribution.get(
            "theme_specificity_score",
            stock.get("theme_specificity_score"),
        ),
        "signal_theme_membership_source": selected_attribution.get(
            "membership_source",
            stock.get("theme_membership_source"),
        ),
        "unattributed_theme_weight": stock.get("unattributed_theme_weight"),
        "theme_attribution_confident": bool(
            stock.get("theme_attribution_confident")
            and _industry_name(stock.get("dominant_theme"))
            == metrics["industry"]
        ),
        "theme_attribution_gap": stock.get("theme_attribution_gap"),
        "theme_basis": str(
            stock.get("theme_basis")
            or theme.get("theme_basis")
            or "industry_proxy"
        ),
        "mainline_state": theme.get("state"),
        "niuone_lifecycle_stage": theme.get("niuone_lifecycle_stage"),
        "niuone_lifecycle_label": theme.get("niuone_lifecycle_label"),
        "niuone_lifecycle_order": theme.get("niuone_lifecycle_order"),
        "niuone_lifecycle_entry_policy": theme.get(
            "niuone_lifecycle_entry_policy"
        ),
        "mainline_raw_state": theme.get("raw_state"),
        "mainline_intraday_state": theme.get("intraday_state"),
        "mainline_score": theme.get("score"),
        "mainline_mode": mainline.get("mode", "none"),
        "mainline_primary": mainline.get("primary", ""),
        "mainline_secondary": mainline.get("secondary", ""),
        "mainline_selected": metrics["industry"] in selected_mainlines,
        "sector_status": theme.get("state"),
        "sector_score": theme.get("score"),
        "sector_member_count": theme.get("member_count"),
        "sector_data_eligible": bool(theme.get("eligible_data")),
        "strong_stock_count": theme.get("strong_stock_count"),
        "effective_strong_count": theme.get("effective_strong_count"),
        "leader_concentration": theme.get("leader_concentration"),
        "single_stock_dominated": bool(theme.get("single_stock_dominated")),
        "mainline_confirmation_count": theme.get("confirmation_count"),
        "mainline_intraday_confirmation_count": theme.get("intraday_confirmation_count"),
        "mainline_cross_day_persistent": bool(theme.get("cross_day_persistent")),
        "mainline_cross_day_confirmed": bool(theme.get("cross_day_confirmed")),
        "mainline_confirmed": bool(theme.get("mainline_confirmed")),
        "mainline_core_overlap_count": theme.get("core_overlap_count"),
        "mainline_core_overlap_ratio": theme.get("core_overlap_ratio"),
        "mainline_continued_core_codes": list(theme.get("continued_core_codes") or []),
        "mainline_as_of_date": theme.get("as_of_date"),
        "mainline_previous_as_of_date": theme.get("previous_as_of_date"),
        "mainline_state_streak": theme.get("state_streak"),
        "mainline_score_change": theme.get("score_change"),
        "today_eligible_data": bool(theme.get("today_eligible_data")),
        "today_up_count": theme.get("today_up_count"),
        "today_1_5pct_count": theme.get("today_1_5pct_count"),
        "today_breadth_pct": theme.get("today_breadth_pct"),
        "today_median_change_pct": theme.get("today_median_change_pct"),
        "today_strength_score": theme.get("today_strength_score"),
        "today_leadership_score": theme.get("today_leadership_score"),
        "market_regime": regime,
        "market_score": market.get("score"),
        "market_hard_stop": bool(market.get("hard_stop")),
        "market_allows_buys": bool(market.get("allow_new_buys")),
        "stock_role": stock.get("role"),
        "stock_leader_rank": stock.get("leader_rank"),
        "stock_leader_tier": bool(stock.get("leader_tier")),
        "stock_strong": bool(stock.get("strong")),
        "stock_strong_score": stock.get("strong_score"),
        "stock_activity_gate_required": bool(
            stock.get("activity_gate_required")
        ),
        "stock_activity_data_available": bool(stock.get("amount_available")),
        "turnover": stock.get("turnover"),
        "stock_amount": stock.get("amount"),
        "stock_market_amount_percentile": stock.get(
            "market_amount_percentile"
        ),
        "stock_theme_amount_percentile": stock.get(
            "theme_amount_percentile"
        ),
        "stock_volume_participation_percentile": stock.get(
            "volume_participation_percentile"
        ),
        "stock_activity_score": stock.get("activity_score"),
        "stock_activity_confirmed": bool(stock.get("activity_confirmed")),
        "stock_reversal_leader_rank": stock.get("today_leader_rank"),
        "stock_reversal_leader_tier": bool(stock.get("today_leader_tier")),
        "stock_reversal_strong": bool(stock.get("reversal_strong")),
        "stock_today_rank_score": stock.get("today_rank_score"),
        "stock_sector_rank": stock.get("theme_rank"),
        "stock_market_rank": stock.get("market_rank"),
        "score_before_external_context": safe_round(metrics["score_before_external_context"], 3),
        "raw_external_context_adjustment": safe_round(metrics["raw_external_context_adjustment"], 3),
        "external_context_adjustment": safe_round(metrics["external_context_adjustment"], 3),
        "external_positive_suppressed": metrics["external_positive_suppressed"],
        "dragon_tiger_available": bool(metrics["dragon_tiger"].get("available")),
        "dragon_tiger_as_of_date": metrics["dragon_tiger"].get("as_of_date"),
        "dragon_tiger_listed": bool(stock.get("dragon_tiger_listed")),
        "dragon_tiger_signal": stock.get("dragon_tiger_signal", "neutral"),
        "dragon_tiger_score": stock.get("dragon_tiger_score", 50.0),
        "dragon_tiger_adjustment": stock.get("dragon_tiger_adjustment", 0.0),
        "news_precheck_configured": bool(metrics["news"].get("configured")),
        "news_precheck": dict(news_precheck),
        "news_checked": bool(news_precheck.get("checked")),
        "news_available": bool(news_precheck.get("available")),
        "news_tone": news_precheck.get("tone", "neutral"),
        "news_tone_label": news_precheck.get("tone_label", "中性"),
        "news_summary": news_precheck.get("summary", ""),
        "news_fetched_at": news_precheck.get("fetched_at", ""),
        "news_adjustment": stock.get("news_adjustment", 0.0),
        "ema20": safe_round(metrics["ema20"], 3),
        "ema50": safe_round(metrics["ema50"], 3),
        "atr": safe_round(metrics["atr"], 3),
        "atr_period": metrics["atr_period"],
        "atr20": safe_round(metrics["atr20"], 3),
        "distance_pct": safe_round(metrics["distance_pct"], 2),
        "extension_atr": safe_round(metrics["extension_atr"], 2),
        "breakout_level": safe_round(metrics.get("breakout_level"), 3),
        "breakout_extension_atr": safe_round(metrics.get("breakout_extension_atr"), 2),
        "entry_extension_atr": safe_round(geometry["entry_extension_atr"], 2),
        "entry_extension_source": geometry["entry_extension_source"],
        "entry_setup": geometry["entry_setup"],
        "volume_ratio": safe_round(metrics["volume_ratio"], 2),
        "change_pct": safe_round(metrics["change_pct"], 2),
        "trend_aligned": metrics["trend_aligned"],
        "breakout": metrics["breakout"],
        "pullback": metrics["pullback"],
        "reclaim": metrics["reclaim"],
        "reclaim_previous_close": metrics["reclaim_previous_close"],
        "reversal_basis": "daily_v" if strategy_name == "niu_reversal_probe" else "",
        "daily_v_reversal": bool(metrics.get("daily_v_reversal")),
        "daily_v_left_peak_date": metrics.get("daily_v_left_peak_date"),
        "daily_v_trough_date": metrics.get("daily_v_trough_date"),
        "daily_v_left_days": metrics.get("daily_v_left_days"),
        "daily_v_right_days": metrics.get("daily_v_right_days"),
        "daily_v_decline_pct": safe_round(metrics.get("daily_v_decline_pct"), 2),
        "daily_v_rebound_pct": safe_round(metrics.get("daily_v_rebound_pct"), 2),
        "daily_v_recovery_ratio": safe_round(metrics.get("daily_v_recovery_ratio"), 4),
        "daily_v_rising_ratio": safe_round(metrics.get("daily_v_rising_ratio"), 4),
        "daily_v_right_trend_confirmed": bool(
            metrics.get("daily_v_right_trend_confirmed")
        ),
        "daily_v_pattern_score": safe_round(metrics.get("daily_v_pattern_score"), 2),
        "intraday_low": safe_round(metrics["intraday_low"], 3),
        "rebound_from_low_pct": safe_round(metrics["rebound_from_low_pct"], 2),
        "stop_price": safe_round(stop_price, 3),
        "stop_source": geometry["stop_source"],
        "stop_distance_pct": safe_round(stop_distance_pct, 2),
        "stop_atr": safe_round(stop_atr, 2),
        "max_stop_distance_pct": structural_limits["max_stop_distance_pct"],
        "max_stop_atr": structural_limits["max_stop_atr"],
        "min_entry_extension_atr": chase_limits.get(
            "min_entry_extension_atr",
            0.0,
        ),
        # Retained for older Dashboard payloads. Scoring does not cap daily
        # gains; first Probe entries recheck the actual quote at execution,
        # alongside the board-limit guard shared by all entry routes.
        "max_entry_change_pct": chase_limits.get("max_entry_change_pct"),
        "max_entry_extension_atr": chase_limits["max_entry_extension_atr"],
        "gap_buffer_pct": safe_round(metrics["gap_buffer_pct"], 3),
        "execution_buffer_pct": SECTOR_TIDE_EXECUTION_BUFFER_PCT,
        "effective_loss_distance_pct": safe_round(effective_loss_distance, 3),
        "per_trade_risk_budget_pct": budget["per_trade_risk_pct"],
        "max_open_risk_pct": budget["max_open_risk_pct"],
        "max_sector_risk_pct": budget["max_sector_risk_pct"],
        "max_total_position_pct": budget["max_total_position_pct"],
        "max_sector_position_pct": budget["max_sector_position_pct"],
        "absolute_position_cap_pct": absolute_cap,
        "max_position_pct_by_risk": dynamic_cap,
        "risk_ok": risk_ok,
        "risk_flags": risk_flags,
        "recent_close": safe_round(metrics["close"], 3),
    }


def _apply_markup_momentum_probe(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Conditionally exchange wider markup geometry for a micro position."""
    if not niuone_markup_momentum_probe_eligible(payload):
        return payload

    acceleration_entry = niuone_markup_momentum_probe_is_acceleration(payload)

    regime = str(payload.get("market_regime") or "")
    stop_distance_pct = safe_float(payload.get("stop_distance_pct")) or 0.0
    stop_atr = safe_float(payload.get("stop_atr")) or 0.0
    limits = niuone_structural_stop_limits(
        regime,
        "niu_emerging",
        NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
    )
    risk_ok = niuone_structure_risk_ok(
        stop_distance_pct,
        stop_atr,
        regime,
        "niu_emerging",
        NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
    )
    budget = niuone_risk_budget(regime, "niu_emerging")
    effective_loss = safe_float(payload.get("effective_loss_distance_pct")) or 0.0
    dynamic_cap = risk_sized_position_cap_pct(
        per_trade_risk_pct=budget["per_trade_risk_pct"],
        effective_loss_distance_pct_value=effective_loss,
        absolute_cap_pct=NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT,
    )
    standard_only_flags = {
        "启动买点尚未确认",
        "启动战法拒绝追高",
    }
    risk_flags = [
        str(flag)
        for flag in (payload.get("risk_flags") or [])
        if str(flag) not in standard_only_flags
        and not str(flag).startswith("结构止损超过当前行情上限")
    ]
    if not risk_ok:
        risk_flags.append(
            "主升动量试仓结构止损超过"
            f"{limits['max_stop_distance_pct']:g}%或"
            f"{limits['max_stop_atr']:g}ATR"
        )
    extension = safe_float(payload.get("entry_extension_atr"))
    if (
        extension is not None
        and extension > NIUONE_MARKUP_MOMENTUM_PROBE_MAX_ENTRY_EXTENSION_ATR
    ):
        risk_flags.append("主升动量试仓价格扩张过大")

    payload.update({
        "niuone_entry_subroute": NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
        "niuone_entry_subroute_label": "主升动量试仓",
        "niuone_markup_momentum_acceleration": acceleration_entry,
        "entry_threshold_override": (
            NIUONE_MARKUP_MOMENTUM_PROBE_MIN_SCORE
            if acceleration_entry
            else NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MIN_SCORE
        ),
        "entry_setup": NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
        "max_entry_extension_atr": (
            NIUONE_MARKUP_MOMENTUM_PROBE_MAX_ENTRY_EXTENSION_ATR
        ),
        "max_stop_distance_pct": limits["max_stop_distance_pct"],
        "max_stop_atr": limits["max_stop_atr"],
        "absolute_position_cap_pct": (
            NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT
        ),
        "max_position_pct_by_risk": dynamic_cap,
        "risk_ok": risk_ok,
        "risk_flags": list(dict.fromkeys(risk_flags)),
        "verdict": "高匹配主升动量试仓",
    })
    return payload


def _common_risks(
    metrics: dict[str, Any],
    *,
    strategy_name: str,
    require_mature_leader: bool = True,
) -> list[str]:
    risks: list[str] = []
    geometry = _strategy_entry_geometry(strategy_name, metrics)
    stop_price = safe_float(geometry["stop_price"])
    stop_distance_pct = structural_stop_distance_pct(metrics["close"], stop_price)
    stop_atr = (
        (metrics["close"] - stop_price) / metrics["atr"]
        if stop_price is not None and stop_price > 0 and metrics["atr"] > 0
        else 0.0
    )
    regime = str(metrics["market"].get("risk_state") or metrics["market"].get("state") or "")
    if not niuone_structure_risk_ok(
        stop_distance_pct,
        stop_atr,
        regime,
        strategy_name,
    ):
        limits = niuone_structural_stop_limits(regime, strategy_name)
        risks.append(
            "结构止损超过当前行情上限"
            f"({limits['max_stop_distance_pct']:g}%或{limits['max_stop_atr']:g}ATR)"
        )
    if require_mature_leader and metrics["theme"].get("single_stock_dominated"):
        risks.append("主题由单只强股主导")
    if require_mature_leader and (
        metrics["stock"].get("leader_tier") is not True
        or metrics["stock"].get("strong") is not True
    ):
        risks.append("个股未进入强势行业龙头梯队")
    news = metrics["stock"].get("news_precheck") or {}
    if news.get("available") and news.get("tone") == "negative":
        risks.append("近3日个股消息面偏利空")
    return risks


def _reversal_strategy_score(metrics: dict[str, Any]) -> float:
    theme = metrics["theme"]
    stock = metrics["stock"]
    pattern_score = float(metrics.get("daily_v_pattern_score") or 0.0)
    stock_score = float(stock.get("strong_score") or 0.0)
    base_score = (
        pattern_score * 0.75
        + float(theme.get("score") or 0.0) * 0.15
        + stock_score * 0.10
    ) / 10.0
    raw_external = float(metrics.get("raw_external_context_adjustment") or 0.0)
    positive_suppressed = bool(
        raw_external > 0
        and metrics["extension_atr"] > 1.0
    )
    external = 0.0 if positive_suppressed else _clamp(raw_external, -0.6, 0.4)
    metrics["external_context_adjustment"] = external
    metrics["external_positive_suppressed"] = positive_suppressed
    metrics["reversal_stock_score"] = pattern_score
    return _clamp(base_score + external, 0.0, 10.0)


def score_niu_leader(
    rows: Sequence[Mapping[str, Any]],
    context: dict[str, Any],
    *,
    shared_metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    metrics = _entry_metrics(
        rows,
        context,
        "niu_leader",
        shared_metrics=shared_metrics,
    )
    if metrics is None:
        return None
    risks = _common_risks(metrics, strategy_name="niu_leader")
    if metrics["theme"].get("state") not in {"mainline", "diverging"}:
        risks.append("主题不处于已确认主线或有效分歧")
    if not (
        metrics["theme"].get("mainline_confirmed")
        or metrics["theme"].get("cross_day_confirmed")
    ):
        risks.append("主线未完成跨交易日核心股延续确认")
    if not (metrics["breakout"] or metrics["pullback"]):
        risks.append("未形成突破或首次缩量回踩")
    entry_extension = safe_float(
        _strategy_entry_geometry("niu_leader", metrics).get("entry_extension_atr")
    )
    if entry_extension is not None and entry_extension > 1.0:
        risks.append("领涨买点偏扩张，已按行情弹性上限复核")
    verdict = "高匹配牛牛领涨" if metrics["composite_score"] >= 8 else ("观察牛牛领涨" if metrics["composite_score"] >= 6.5 else "不匹配")
    return with_strategy_profile("niu_leader", _payload("niu_leader", metrics, verdict=verdict, risk_flags=risks))


def score_niu_pullback(
    rows: Sequence[Mapping[str, Any]],
    context: dict[str, Any],
    *,
    shared_metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    metrics = _entry_metrics(
        rows,
        context,
        "niu_pullback",
        shared_metrics=shared_metrics,
    )
    if metrics is None:
        return None
    risks = _common_risks(metrics, strategy_name="niu_pullback")
    if metrics["theme"].get("state") not in {"mainline", "diverging"} or float(metrics["theme"].get("score") or 0) < 70:
        risks.append("主线强度不足以参与分歧")
    if not metrics["theme"].get("mainline_confirmed"):
        risks.append("主题没有有效的跨交易日主线确认记录")
    if not (metrics["pullback"] or metrics["reclaim"]):
        risks.append("未出现EMA20企稳转强或收复买点")
    if metrics["extension_atr"] > 1.0:
        risks.append("转强买点偏扩张，已按行情弹性上限复核")
    verdict = "高匹配牛牛转强" if metrics["composite_score"] >= 8.2 else ("观察牛牛转强" if metrics["composite_score"] >= 6.5 else "不匹配")
    return with_strategy_profile("niu_pullback", _payload("niu_pullback", metrics, verdict=verdict, risk_flags=risks))


def score_niu_emerging(
    rows: Sequence[Mapping[str, Any]],
    context: dict[str, Any],
    *,
    shared_metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    metrics = _entry_metrics(
        rows,
        context,
        "niu_emerging",
        shared_metrics=shared_metrics,
    )
    if metrics is None:
        return None
    risks = _common_risks(metrics, strategy_name="niu_emerging")
    if not niu_emerging_theme_eligible(metrics["theme"]):
        risks.append("主题不处于跨日延续的待确认启动阶段")
    if not metrics["theme"].get("cross_day_persistent"):
        risks.append("启动主题尚未跨交易日延续")
    if int(metrics["theme"].get("strong_stock_count") or 0) < 2:
        risks.append("少于两只强势股共同确认")
    if not (metrics["breakout"] or metrics["reclaim"]):
        risks.append("启动买点尚未确认")
    entry_extension = safe_float(
        _strategy_entry_geometry("niu_emerging", metrics).get("entry_extension_atr")
    )
    if entry_extension is not None and entry_extension > 1.5:
        risks.append("启动战法拒绝追高")
    verdict = "高匹配牛牛启动" if metrics["composite_score"] >= 8.4 else ("观察牛牛启动" if metrics["composite_score"] >= 6.5 else "不匹配")
    payload = _payload(
        "niu_emerging",
        metrics,
        verdict=verdict,
        risk_flags=risks,
    )
    return with_strategy_profile(
        "niu_emerging",
        _apply_markup_momentum_probe(payload),
    )


def score_niu_reversal_probe(
    rows: Sequence[Mapping[str, Any]],
    context: dict[str, Any],
    *,
    shared_metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    metrics = _entry_metrics(
        rows,
        context,
        "niu_reversal_probe",
        shared_metrics=shared_metrics,
    )
    if metrics is None:
        return None
    metrics = dict(metrics)
    metrics["strategy_score"] = _reversal_strategy_score(metrics)
    risks = _common_risks(
        metrics,
        strategy_name="niu_reversal_probe",
        require_mature_leader=False,
    )
    if not metrics.get("daily_v_reversal"):
        risks.append("日线区间尚未形成完整V型趋势反转")
    if metrics["extension_atr"] > 1.0:
        risks.append("日线V型反转买点偏扩张")
    score = float(metrics["strategy_score"])
    verdict = "高匹配牛牛试仓" if score >= 8.4 else ("观察牛牛试仓" if score >= 7.0 else "不匹配")
    payload = _payload("niu_reversal_probe", metrics, verdict=verdict, risk_flags=risks)
    payload["reversal_stock_score"] = safe_round(metrics.get("reversal_stock_score"), 2)
    return with_strategy_profile("niu_reversal_probe", payload)


score_niu_leader.requires_context = True  # type: ignore[attr-defined]
score_niu_pullback.requires_context = True  # type: ignore[attr-defined]
score_niu_emerging.requires_context = True  # type: ignore[attr-defined]
score_niu_reversal_probe.requires_context = True  # type: ignore[attr-defined]
for _niuone_scorer in (
    score_niu_leader,
    score_niu_pullback,
    score_niu_emerging,
    score_niu_reversal_probe,
):
    _niuone_scorer.shared_input_builder = _shared_entry_metrics  # type: ignore[attr-defined]
    _niuone_scorer.shared_input_keyword = "shared_metrics"  # type: ignore[attr-defined]
