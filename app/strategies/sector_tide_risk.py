"""Shared dynamic risk-budget calculations for the Sector Tide strategy."""
from __future__ import annotations

import math
from typing import Any


SECTOR_TIDE_ABSOLUTE_POSITION_CAP_PCT = {
    "tide_leader": 8.0,
    "tide_rotation": 6.0,
    "tide_recovery": 4.0,
}

# Percentages are expressed as percent of account equity, except the two
# position-exposure limits which are percent of gross account exposure.
SECTOR_TIDE_REGIME_RISK_BUDGETS = {
    "offensive": {
        "per_trade_risk_pct": 0.30,
        "max_open_risk_pct": 1.50,
        "max_sector_risk_pct": 0.60,
        "max_total_position_pct": 45.0,
        "max_sector_position_pct": 12.0,
    },
    "rotation": {
        "per_trade_risk_pct": 0.20,
        "max_open_risk_pct": 0.80,
        "max_sector_risk_pct": 0.40,
        "max_total_position_pct": 30.0,
        "max_sector_position_pct": 10.0,
    },
    "recovery": {
        "per_trade_risk_pct": 0.10,
        "max_open_risk_pct": 0.30,
        "max_sector_risk_pct": 0.20,
        "max_total_position_pct": 15.0,
        "max_sector_position_pct": 6.0,
    },
    "defensive": {
        "per_trade_risk_pct": 0.0,
        "max_open_risk_pct": 0.0,
        "max_sector_risk_pct": 0.0,
        "max_total_position_pct": 0.0,
        "max_sector_position_pct": 0.0,
    },
}

SECTOR_TIDE_EXECUTION_BUFFER_PCT = 0.20
SECTOR_TIDE_GAP_LOOKBACK_DAYS = 60
SECTOR_TIDE_GAP_PERCENTILE = 0.95
SECTOR_TIDE_ATR_GAP_MULTIPLIER = 0.50


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _percentile(values: list[float], quantile: float) -> float:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return 0.0
    if len(clean) == 1:
        return clean[0]
    rank = max(0.0, min(1.0, quantile)) * (len(clean) - 1)
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return clean[lower]
    weight = rank - lower
    return clean[lower] * (1.0 - weight) + clean[upper] * weight


def sector_tide_risk_budget(regime: str | None) -> dict[str, float]:
    """Return a copy so callers cannot mutate the registered budget."""
    key = str(regime or "defensive").strip().lower()
    return dict(SECTOR_TIDE_REGIME_RISK_BUDGETS.get(key, SECTOR_TIDE_REGIME_RISK_BUDGETS["defensive"]))


def downside_gap_buffer_pct(
    rows: list[dict[str, Any]],
    *,
    atr: float | None,
    close: float | None,
    lookback: int = SECTOR_TIDE_GAP_LOOKBACK_DAYS,
) -> float:
    """Stress overnight loss with the larger of downside-gap p95 and 0.5 ATR."""
    downside_gaps: list[float] = []
    start = max(1, len(rows) - max(2, int(lookback)))
    for index in range(start, len(rows)):
        prior_close = _float(rows[index - 1].get("close"))
        current_open = _float(rows[index].get("open"))
        if prior_close is None or prior_close <= 0 or current_open is None or current_open <= 0:
            continue
        downside_gaps.append(max(0.0, (prior_close - current_open) / prior_close * 100.0))
    gap_p95 = _percentile(downside_gaps, SECTOR_TIDE_GAP_PERCENTILE)
    atr_value = _float(atr, 0.0) or 0.0
    close_value = _float(close, 0.0) or 0.0
    atr_buffer = atr_value / close_value * 100.0 * SECTOR_TIDE_ATR_GAP_MULTIPLIER if close_value > 0 else 0.0
    return round(max(gap_p95, atr_buffer), 3)


def structural_stop_distance_pct(price: float | None, stop_price: float | None) -> float:
    current = _float(price, 0.0) or 0.0
    stop = _float(stop_price, 0.0) or 0.0
    if current <= 0 or stop <= 0 or stop >= current:
        return 0.0
    return (current - stop) / current * 100.0


def effective_loss_distance_pct(
    price: float | None,
    stop_price: float | None,
    *,
    gap_buffer_pct: float | None,
    execution_buffer_pct: float | None = SECTOR_TIDE_EXECUTION_BUFFER_PCT,
) -> float:
    """Return stop loss plus adverse-gap and execution-cost reserves."""
    structural = structural_stop_distance_pct(price, stop_price)
    if structural <= 0:
        return 0.0
    gap = max(0.0, _float(gap_buffer_pct, 0.0) or 0.0)
    execution = max(0.0, _float(execution_buffer_pct, SECTOR_TIDE_EXECUTION_BUFFER_PCT) or 0.0)
    return round(structural + gap + execution, 4)


def risk_sized_position_cap_pct(
    *,
    per_trade_risk_pct: float | None,
    effective_loss_distance_pct_value: float | None,
    absolute_cap_pct: float | None,
) -> float:
    """Convert a NAV loss budget into a position cap, then apply the hard ceiling."""
    risk_budget = max(0.0, _float(per_trade_risk_pct, 0.0) or 0.0)
    loss_distance = max(0.0, _float(effective_loss_distance_pct_value, 0.0) or 0.0)
    absolute_cap = max(0.0, _float(absolute_cap_pct, 0.0) or 0.0)
    if risk_budget <= 0 or loss_distance <= 0 or absolute_cap <= 0:
        return 0.0
    return round(min(absolute_cap, risk_budget / loss_distance * 100.0), 4)


def position_open_risk_pct(
    position_value: float | None,
    total_equity: float | None,
    effective_loss_distance_pct_value: float | None,
) -> float:
    value = max(0.0, _float(position_value, 0.0) or 0.0)
    equity = max(0.0, _float(total_equity, 0.0) or 0.0)
    loss_distance = max(0.0, _float(effective_loss_distance_pct_value, 0.0) or 0.0)
    if equity <= 0:
        return 100.0
    return value / equity * loss_distance


def stored_position_effective_loss_distance_pct(
    position: dict[str, Any],
    *,
    mark_price: float | None,
) -> float:
    """Revalue an open position's loss-to-stop using its stored stress buffers."""
    stop_price = _float(position.get("entry_stop_price"), 0.0) or 0.0
    price = _float(mark_price, 0.0) or 0.0
    if price <= 0 or stop_price <= 0 or stop_price >= price:
        return 0.0
    gap = _float(position.get("gap_buffer_pct"), 0.0) or 0.0
    if gap <= 0:
        entry_atr = _float(position.get("entry_atr20"), 0.0) or 0.0
        gap = entry_atr / price * 100.0 * SECTOR_TIDE_ATR_GAP_MULTIPLIER if entry_atr > 0 else 0.0
    execution = _float(position.get("execution_buffer_pct"), SECTOR_TIDE_EXECUTION_BUFFER_PCT)
    return effective_loss_distance_pct(
        price,
        stop_price,
        gap_buffer_pct=gap,
        execution_buffer_pct=execution,
    )
