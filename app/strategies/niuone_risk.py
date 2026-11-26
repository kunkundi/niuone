"""Dynamic risk budgets for the independent 牛牛战法 strategy suite."""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any


NIUONE_ABSOLUTE_POSITION_CAP_PCT = {
    "niu_leader": 30.0,
    "niu_pullback": 25.0,
    "niu_emerging": 15.0,
    "niu_reversal_probe": 10.0,
}

NIUONE_DEFAULT_MAX_OPEN_POSITIONS = 6
# Compatibility name retained for protocol snapshots and older imports.  This
# is only the default when no composition-layer setting is supplied; Practice,
# strict-forward evaluation, and administrator backtests pass the configured
# DASHBOARD_MAX_OPEN_POSITIONS value explicitly.  NiuOne openings are not
# capped by a per-cycle, session, or trading-day counter.
NIUONE_MAX_OPEN_POSITIONS = NIUONE_DEFAULT_MAX_OPEN_POSITIONS
NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY: int | None = None
NIUONE_BUY_SIGNAL_SCORE_FIELDS = (
    "best_decision_score",
    "decision_score",
    "selection_signal_score",
    "best_score",
    "score",
)
NIUONE_STRATEGY_PRIORITY = {
    "niu_leader": 91.0,
    "niu_pullback": 84.0,
    "niu_emerging": 76.0,
    "niu_reversal_probe": 70.0,
}
NIUONE_LIFECYCLE_PRIORITY_ADJUSTMENT = {
    "markup": 4.0,
    "climax": 2.0,
    "divergence": 1.0,
    "brewing": 0.0,
    "fade": -20.0,
}
# Replacing a whole holding has friction and timing risk.  A tiny floating
# point edge is not enough evidence to rotate the book.
NIUONE_REPLACEMENT_PRIORITY_MARGIN = 3.0
NIUONE_ENTRY_REGIMES = frozenset({
    "offensive",
    "rotation",
    "recovery",
    "defensive",
})

# A narrowly conditioned emerging-theme route for a first position after the
# lifecycle has already moved into markup.  The wider price geometry is paid
# for with a much smaller absolute position cap; it is not a global relaxation
# of the ordinary emerging/reversal entry rules.
NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE = "markup_momentum_probe"
NIUONE_MARKUP_MOMENTUM_PROBE_MIN_SCORE = 8.0
NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MIN_SCORE = 8.1
NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MIN_MAINLINE_SCORE = 70.0
NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MAX_EXTENSION_ATR = 1.0
NIUONE_MARKUP_MOMENTUM_PROBE_MIN_STRONG_SCORE = 90.0
NIUONE_MARKUP_MOMENTUM_PROBE_REQUIRED_LEADER_RANK = 1
NIUONE_MARKUP_MOMENTUM_PROBE_ACCELERATION_MIN_EXTENSION_ATR = 2.5
NIUONE_MARKUP_MOMENTUM_PROBE_ACCELERATION_MIN_CHANGE_PCT = 9.5
NIUONE_MARKUP_MOMENTUM_PROBE_ACCELERATION_MAX_VOLUME_RATIO = 1.2
NIUONE_MARKUP_MOMENTUM_PROBE_MAX_ENTRY_EXTENSION_ATR = 3.2
NIUONE_MARKUP_MOMENTUM_PROBE_MAX_STOP_DISTANCE_PCT = 18.0
NIUONE_MARKUP_MOMENTUM_PROBE_MAX_STOP_ATR = 3.0
NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT = 4.0
NIUONE_MARKUP_MOMENTUM_PROBE_MAX_EXECUTION_GAP_PCT = 3.0

# A 10% reversal probe is only the brewing-stage starting size. During
# markup, a persistent emerging leader may first scale toward the early cap;
# once the mainline is confirmed it may continue toward the final cap. The
# structural-stop distance and portfolio risk budgets below still determine
# the smaller executable target.
NIUONE_MARKUP_EARLY_UPGRADE_POSITION_CAP_PCT = 10.0
NIUONE_MARKUP_UPGRADE_POSITION_CAP_PCT = 20.0
NIUONE_MARKUP_UPGRADE_MIN_PNL_PCT = 2.0
NIUONE_MARKUP_UPGRADE_MAX_PNL_PCT = 12.0

# Markup holdings use event-driven rebalance cycles instead of a fixed number
# of adds.  A confirmed leader first has to give back enough of its latest
# closing-price peak, or spend several sessions below that peak, before one
# third is released.  The released risk may be added back only after a fresh
# rebound.  Completing an add starts a new cycle; there is deliberately no
# lifetime add counter limit.
NIUONE_MARKUP_REBALANCE_PULLBACK_ATR = 1.0
NIUONE_MARKUP_REBALANCE_STALL_SESSIONS = 3
NIUONE_MARKUP_REBALANCE_STALL_MIN_ATR = 0.25
NIUONE_MARKUP_REBALANCE_REBOUND_ATR = 0.5
NIUONE_MARKUP_REBALANCE_MIN_SESSIONS_AFTER_ADD = 2
NIUONE_MARKUP_REBALANCE_TRIM_RATIO = 1.0 / 3.0

# Structural-stop limits become gradually more permissive as the market risk
# state improves.  The execution layer still sizes every order from the
# effective loss distance, so widening a structural stop does not widen the
# account-risk budget for that trade.
NIUONE_STRUCTURAL_STOP_LIMITS = {
    "offensive": {"max_stop_distance_pct": 10.0, "max_stop_atr": 2.5},
    "rotation": {"max_stop_distance_pct": 8.0, "max_stop_atr": 2.0},
    "recovery": {"max_stop_distance_pct": 6.0, "max_stop_atr": 1.5},
    "defensive": {"max_stop_distance_pct": 6.0, "max_stop_atr": 1.5},
}

NIUONE_CHASE_LIMITS = {
    "niu_leader": {
        "offensive": {"max_entry_extension_atr": 1.5},
        "rotation": {"max_entry_extension_atr": 1.25},
    },
    "niu_pullback": {
        "offensive": {"max_entry_extension_atr": 1.25},
        "rotation": {"max_entry_extension_atr": 1.0},
        "recovery": {"max_entry_extension_atr": 1.0},
    },
    "niu_emerging": {
        "offensive": {"max_entry_extension_atr": 1.5},
        "rotation": {"max_entry_extension_atr": 1.5},
        "recovery": {"max_entry_extension_atr": 1.5},
    },
    "niu_reversal_probe": {
        "offensive": {
            "min_entry_extension_atr": 1.0,
            "max_entry_extension_atr": 1.5,
        },
        "rotation": {
            "min_entry_extension_atr": 1.0,
            "max_entry_extension_atr": 1.5,
        },
        "recovery": {
            "min_entry_extension_atr": 1.0,
            "max_entry_extension_atr": 1.5,
        },
    },
}

NIUONE_REVERSAL_STOP_LIMITS = {
    "max_stop_distance_pct": 6.0,
    "max_stop_atr": 2.0,
}

# Risk values are percentages of account equity. Exposure values are
# percentages of gross account equity. The suite normally concentrates in up
# to three names and hard-stops at five, so its loss budgets are paired with
# explicit single-name, theme, and total-exposure limits.
NIUONE_REGIME_RISK_BUDGETS = {
    "offensive": {
        "per_trade_risk_pct": 1.50,
        "max_open_risk_pct": 4.50,
        "max_sector_risk_pct": 3.00,
        "max_total_position_pct": 70.0,
        "max_sector_position_pct": 55.0,
    },
    "rotation": {
        "per_trade_risk_pct": 1.00,
        "max_open_risk_pct": 3.00,
        "max_sector_risk_pct": 2.00,
        "max_total_position_pct": 55.0,
        "max_sector_position_pct": 40.0,
    },
    "recovery": {
        "per_trade_risk_pct": 0.60,
        "max_open_risk_pct": 1.80,
        "max_sector_risk_pct": 1.20,
        "max_total_position_pct": 35.0,
        "max_sector_position_pct": 25.0,
    },
    "defensive": {
        "per_trade_risk_pct": 0.30,
        "max_open_risk_pct": 0.90,
        "max_sector_risk_pct": 0.60,
        "max_total_position_pct": 20.0,
        "max_sector_position_pct": 12.0,
    },
}

# The daily V-reversal path remains an early, lower-certainty entry. Keep its
# loss budget deliberately below the confirmed NiuOne paths while preserving
# the suite-wide exposure ceilings used by established positions.
NIUONE_REVERSAL_RISK_BUDGETS = {
    "offensive": {
        **NIUONE_REGIME_RISK_BUDGETS["offensive"],
        "per_trade_risk_pct": 0.35,
        "max_sector_risk_pct": 0.70,
        "max_sector_position_pct": 12.0,
    },
    "rotation": {
        **NIUONE_REGIME_RISK_BUDGETS["rotation"],
        "per_trade_risk_pct": 1.00,
        # Keep the theme ceiling at least as wide as one isolated Probe order;
        # otherwise the requested 1% rotation budget would still be clipped to
        # the old 0.60% theme-risk limit before execution.
        "max_sector_risk_pct": 1.00,
        "max_sector_position_pct": 10.0,
    },
    "recovery": {
        **NIUONE_REGIME_RISK_BUDGETS["recovery"],
        "per_trade_risk_pct": 0.25,
        "max_sector_risk_pct": 0.50,
        "max_sector_position_pct": 8.0,
    },
    "defensive": {
        **NIUONE_REGIME_RISK_BUDGETS["defensive"],
        "per_trade_risk_pct": 0.15,
        "max_sector_risk_pct": 0.30,
        "max_sector_position_pct": 5.0,
    },
}


def _priority_number(value: Any, default: float = 0.0) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return default
    return resolved if resolved == resolved else default


def niuone_buy_signal_score(
    item: Mapping[str, Any] | None,
    *,
    fallback: Any = None,
) -> tuple[float | None, str]:
    """Resolve the auditable score attached to one NiuOne BUY signal."""
    values = item if isinstance(item, Mapping) else {}
    for key in NIUONE_BUY_SIGNAL_SCORE_FIELDS:
        if values.get(key) in (None, ""):
            continue
        score = _priority_number(values.get(key), math.nan)
        if math.isfinite(score):
            return round(score, 4), key
    score = _priority_number(fallback, math.nan)
    if math.isfinite(score):
        return round(score, 4), "fallback"
    return None, "unavailable"


def niuone_add_signal_score_audit(
    position: Mapping[str, Any] | None,
    candidate: Mapping[str, Any] | None,
    *,
    fallback_signal_score: Any = None,
) -> dict[str, Any]:
    """Compare a repeated BUY signal with the holding's strongest filled BUY.

    The hurdle is the highest score that actually produced a BUY fill, not the
    latest observation score.  This keeps repeated intraday scans from
    manufacturing an add and prevents a lower-score rebalance fill from
    lowering the future scale-in hurdle.
    """
    values = position if isinstance(position, Mapping) else {}
    previous_score: float | None = None
    previous_source = "unavailable"
    for key in (
        "highest_buy_signal_score",
        "last_buy_signal_score",
        "entry_signal_score",
    ):
        score = _priority_number(values.get(key), math.nan)
        if math.isfinite(score):
            previous_score = round(score, 4)
            previous_source = key
            break
    current_score, current_source = niuone_buy_signal_score(
        candidate,
        fallback=fallback_signal_score,
    )
    improved = bool(
        previous_score is not None
        and current_score is not None
        and current_score > previous_score + 1e-9
    )
    return {
        "eligible": improved,
        "previous_score": previous_score,
        "previous_score_source": previous_source,
        "current_score": current_score,
        "current_score_source": current_source,
        "score_delta": (
            round(current_score - previous_score, 4)
            if previous_score is not None and current_score is not None
            else None
        ),
    }


def niuone_portfolio_priority(
    item: Mapping[str, Any] | None,
    strategy_name: str | None = None,
) -> dict[str, Any]:
    """Return an auditable current priority for a candidate or open holding.

    The registered strategy certainty is the base.  A holding that has since
    become a confirmed strong leader is promoted to the leader tier, while a
    faded/inactive theme is explicitly demoted.  Current decision score is
    preferred; entry score is only a fallback when the holding is absent from
    the latest candidate set.
    """
    values = item if isinstance(item, Mapping) else {}
    resolved_strategy = str(
        strategy_name
        or values.get("best_strategy")
        or values.get("buy_strategy")
        or values.get("strategy_id")
        or values.get("initial_buy_strategy")
        or ""
    ).strip()
    strategy_priority = NIUONE_STRATEGY_PRIORITY.get(resolved_strategy, 0.0)

    lifecycle_stage = str(
        values.get("niuone_lifecycle_stage")
        or values.get("lifecycle_stage")
        or ""
    ).strip().lower()
    mainline_state = str(values.get("mainline_state") or "").strip().lower()
    strong = values.get("stock_strong") is True
    leader = values.get("stock_leader_tier") is True
    if strong and leader:
        strategy_priority = max(
            strategy_priority,
            NIUONE_STRATEGY_PRIORITY["niu_leader"],
        )
    elif (
        values.get("mainline_confirmed") is True
        and lifecycle_stage in {"markup", "climax", "divergence"}
    ):
        strategy_priority = max(
            strategy_priority,
            NIUONE_STRATEGY_PRIORITY["niu_pullback"],
        )
    elif (
        values.get("mainline_cross_day_persistent") is True
        and mainline_state == "emerging"
    ):
        strategy_priority = max(
            strategy_priority,
            NIUONE_STRATEGY_PRIORITY["niu_emerging"],
        )

    signal_score = 0.0
    signal_score_source = "unavailable"
    for key in (
        "best_decision_score",
        "decision_score",
        "current_decision_score",
        "selection_signal_score",
        "entry_signal_score",
        "best_score",
        "score",
    ):
        if values.get(key) not in (None, ""):
            signal_score = _priority_number(values.get(key), 0.0)
            signal_score_source = key
            break
    mainline_score = max(
        0.0,
        min(100.0, _priority_number(values.get("mainline_score"), 0.0)),
    )
    lifecycle_adjustment = NIUONE_LIFECYCLE_PRIORITY_ADJUSTMENT.get(
        lifecycle_stage,
        0.0,
    )
    if mainline_state in {"fading", "inactive"}:
        lifecycle_adjustment = min(lifecycle_adjustment, -20.0)
    strength_adjustment = (4.0 if leader else 0.0) + (3.0 if strong else 0.0)
    priority_score = (
        strategy_priority
        + signal_score * 2.0
        + mainline_score * 0.05
        + lifecycle_adjustment
        + strength_adjustment
    )
    return {
        "score": round(priority_score, 4),
        "strategy_id": resolved_strategy,
        "strategy_priority": round(strategy_priority, 4),
        "signal_score": round(signal_score, 4),
        "signal_score_source": signal_score_source,
        "mainline_score": round(mainline_score, 4),
        "lifecycle_stage": lifecycle_stage,
        "lifecycle_adjustment": round(lifecycle_adjustment, 4),
        "strength_adjustment": round(strength_adjustment, 4),
    }


def niuone_priority_is_higher(
    incoming: Mapping[str, Any] | None,
    holding: Mapping[str, Any] | None,
    *,
    incoming_strategy: str | None = None,
    holding_strategy: str | None = None,
    minimum_margin: float = NIUONE_REPLACEMENT_PRIORITY_MARGIN,
) -> bool:
    """Return true only for a material, non-tied portfolio priority upgrade."""
    incoming_priority = niuone_portfolio_priority(
        incoming,
        incoming_strategy,
    )["score"]
    holding_priority = niuone_portfolio_priority(
        holding,
        holding_strategy,
    )["score"]
    return float(incoming_priority) >= (
        float(holding_priority) + max(0.0, float(minimum_margin))
    )


def niuone_risk_budget(
    regime: str | None,
    strategy_name: str | None = None,
) -> dict[str, float]:
    """Return an isolated budget mapping for one market regime."""
    key = str(regime or "defensive").strip().lower()
    budgets = (
        NIUONE_REVERSAL_RISK_BUDGETS
        if str(strategy_name or "") == "niu_reversal_probe"
        else NIUONE_REGIME_RISK_BUDGETS
    )
    return dict(budgets.get(key, budgets["defensive"]))


def niuone_structural_stop_limits(
    regime: str | None,
    strategy_name: str | None = None,
    entry_subroute: str | None = None,
) -> dict[str, float]:
    """Return the hard structural-stop limits for one market regime."""
    if (
        str(strategy_name or "") == "niu_emerging"
        and str(entry_subroute or "")
        == NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE
    ):
        return {
            "max_stop_distance_pct": (
                NIUONE_MARKUP_MOMENTUM_PROBE_MAX_STOP_DISTANCE_PCT
            ),
            "max_stop_atr": NIUONE_MARKUP_MOMENTUM_PROBE_MAX_STOP_ATR,
        }
    if str(strategy_name or "") == "niu_reversal_probe":
        return dict(NIUONE_REVERSAL_STOP_LIMITS)
    key = str(regime or "defensive").strip().lower()
    return dict(NIUONE_STRUCTURAL_STOP_LIMITS.get(key, NIUONE_STRUCTURAL_STOP_LIMITS["defensive"]))


def niuone_structure_risk_ok(
    stop_distance_pct: float,
    stop_atr: float,
    regime: str | None,
    strategy_name: str | None = None,
    entry_subroute: str | None = None,
) -> bool:
    """Check a proposed structural stop against the regime-aware hard limits."""
    limits = niuone_structural_stop_limits(
        regime,
        strategy_name,
        entry_subroute,
    )
    return bool(
        0 < stop_distance_pct <= limits["max_stop_distance_pct"]
        and 0 < stop_atr <= limits["max_stop_atr"]
    )


def niuone_chase_limits(strategy_name: str, regime: str | None) -> dict[str, float]:
    """Return ATR expansion limits; daily gain is gated only by limit-up execution."""
    key = str(regime or "defensive").strip().lower()
    by_regime = NIUONE_CHASE_LIMITS.get(strategy_name, {})
    fallback_strategy = (
        strategy_name
        if strategy_name in {"niu_emerging", "niu_reversal_probe"}
        else "niu_pullback"
    )
    fallback = NIUONE_CHASE_LIMITS[fallback_strategy]["recovery"]
    return dict(by_regime.get(key, fallback))


NIUONE_ENTRY_GEOMETRY_FIELDS = (
    "niuone_entry_subroute", "niuone_entry_subroute_label",
    "niuone_markup_momentum_acceleration", "entry_setup", "entry_extension_atr",
    "entry_extension_source", "min_entry_extension_atr", "extension_atr",
    "breakout_level", "breakout_extension_atr", "volume_ratio",
    "breakout", "pullback", "reclaim", "reclaim_previous_close",
)
NIUONE_MODEL_ENTRY_FIELDS = (
    "turnover", "stock_market_amount_percentile", "stock_theme_amount_percentile",
    "stock_volume_participation_percentile", "stock_activity_gate_required",
    "stock_activity_data_available", "stock_activity_confirmed",
    "atr", "atr_period", "stop_atr", "max_stop_atr", "max_entry_extension_atr",
    "absolute_position_cap_pct", "max_position_pct_by_risk",
    *NIUONE_ENTRY_GEOMETRY_FIELDS,
)


def complete_niuone_entry_fields(candidate: dict[str, Any]) -> dict[str, Any]:
    """Read missing fields from the selected score of the same scan generation."""
    strategy = candidate.get("best_strategy")
    scores = candidate.get("strategies")
    if not isinstance(strategy, str) or strategy not in NIUONE_ABSOLUTE_POSITION_CAP_PCT or not isinstance(scores, Mapping):
        return candidate
    score = scores.get(strategy)
    if not isinstance(score, Mapping):
        return candidate
    missing = {key: score[key] for key in NIUONE_MODEL_ENTRY_FIELDS
               if key not in candidate and key in score}
    return {**candidate, **missing} if missing else candidate


def niuone_opening_position_cap(
    strategy: str, candidate: Mapping[str, Any], *, opening: bool,
) -> float:
    """Apply the selected route's absolute ceiling before live risk sizing."""
    cap = NIUONE_ABSOLUTE_POSITION_CAP_PCT[strategy]
    if not opening:
        return cap
    if candidate.get("niuone_entry_subroute") == NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE:
        cap = min(cap, NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT)
    value = candidate.get("absolute_position_cap_pct")
    if value is None:
        return cap
    try:
        scored_cap = float(value)
    except (ValueError, TypeError, OverflowError):
        return 0.0
    return min(cap, scored_cap) if math.isfinite(scored_cap) and scored_cap > 0 else 0.0


def niuone_markup_momentum_probe_eligible(values: Mapping[str, Any]) -> bool:
    """Return whether a scored emerging leader has the probe's identity gates."""

    def number(key: str) -> float | None:
        try:
            value = float(values.get(key))
        except (TypeError, ValueError):
            return None
        return value

    stage = str(values.get("niuone_lifecycle_stage") or "").strip().lower()
    state = str(
        values.get("mainline_state")
        or values.get("sector_status")
        or ""
    ).strip().lower()
    regime = str(values.get("market_regime") or "").strip().lower()
    score = number("score")
    if score is None:
        score = number("best_score")
    strong_score = number("stock_strong_score")
    leader_rank = number("stock_leader_rank")
    mainline_score = number("mainline_score")
    extension_atr = number("entry_extension_atr")
    change_pct = number("change_pct")
    volume_ratio = number("volume_ratio")
    base_eligible = bool(
        stage == "markup"
        and state == "emerging"
        and values.get("mainline_cross_day_persistent") is True
        and values.get("stock_leader_tier") is True
        and values.get("stock_strong") is True
        and leader_rank == NIUONE_MARKUP_MOMENTUM_PROBE_REQUIRED_LEADER_RANK
        and strong_score is not None
        and strong_score >= NIUONE_MARKUP_MOMENTUM_PROBE_MIN_STRONG_SCORE
        and score is not None
        and round(score, 1) >= NIUONE_MARKUP_MOMENTUM_PROBE_MIN_SCORE
        and regime in NIUONE_ENTRY_REGIMES
        and values.get("market_allows_buys") is True
        and values.get("market_hard_stop") is not True
    )
    if not base_eligible:
        return False
    ordinary = bool(
        round(float(score), 1)
        >= NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MIN_SCORE
        and mainline_score is not None
        and mainline_score
        >= NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MIN_MAINLINE_SCORE
        and extension_atr is not None
        and extension_atr
        <= NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MAX_EXTENSION_ATR + 1e-9
    )
    return ordinary or niuone_markup_momentum_probe_is_acceleration(values)


def niuone_markup_momentum_probe_is_acceleration(
    values: Mapping[str, Any],
) -> bool:
    """Keep only the bounded near-limit-up, non-explosive-volume exception."""
    def number(key: str) -> float | None:
        try:
            return float(values.get(key))
        except (TypeError, ValueError):
            return None

    extension_atr = number("entry_extension_atr")
    change_pct = number("change_pct")
    volume_ratio = number("volume_ratio")
    return bool(
        extension_atr is not None
        and NIUONE_MARKUP_MOMENTUM_PROBE_ACCELERATION_MIN_EXTENSION_ATR
        <= extension_atr
        <= NIUONE_MARKUP_MOMENTUM_PROBE_MAX_ENTRY_EXTENSION_ATR + 1e-9
        and change_pct is not None
        and change_pct
        >= NIUONE_MARKUP_MOMENTUM_PROBE_ACCELERATION_MIN_CHANGE_PCT
        and volume_ratio is not None
        and volume_ratio
        <= NIUONE_MARKUP_MOMENTUM_PROBE_ACCELERATION_MAX_VOLUME_RATIO
    )
