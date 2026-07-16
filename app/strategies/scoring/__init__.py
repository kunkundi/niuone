"""Strategy scoring public API and scorer registry."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..registry import STRATEGY_DEFINITIONS
from .base import score_breakout, score_trend_pullback
from .common import (
    B1_CORE_J_CEILING,
    B1_WATCH_J_CEILING,
    COMMON_MAX_BBI_DISTANCE_PCT,
    LI_DAXIAO_HOT_TURNOVER,
    LI_DAXIAO_MAX_BBI_DISTANCE,
    LI_DAXIAO_MAX_DAILY_CHASE_PCT,
    LI_DAXIAO_MAX_TURNOVER,
    LI_DAXIAO_MIN_AMOUNT,
    candle_amplitude_pct,
    candle_body_pct,
    combine_z_yellow,
    compute_bbi,
    compute_ema,
    compute_kdj,
    enrich_rows,
    find_n_structure_prior_low,
    is_yang,
    is_yin,
    li_daxiao_bottom_stage,
    moving_avg,
    n_structure_ok,
    pct_change,
    pct_returns,
    return_pct,
    safe_float,
    safe_round,
    strategy_hard_blockers,
    volatility_pct,
    with_strategy_profile,
)
from .li_daxiao import score_li_daxiao_bottom
from .engine import StrategyScorer, analyze_enriched_rows
from .sector_tide import (
    SECTOR_TIDE_STRATEGY_IDS,
    build_sector_tide_context,
    score_tide_leader,
    score_tide_recovery,
    score_tide_rotation,
)
from .zettaranc import (
    recent_b1_indices,
    score_b2_confirm,
    score_b3_accelerate,
    score_shaofu_b1,
    score_super_b1,
)


_SCORER_BY_NAME: dict[str, Callable[[list[dict[str, Any]]], dict[str, Any] | None]] = {
    "score_trend_pullback": score_trend_pullback,
    "score_breakout": score_breakout,
    "score_shaofu_b1": score_shaofu_b1,
    "score_b2_confirm": score_b2_confirm,
    "score_b3_accelerate": score_b3_accelerate,
    "score_super_b1": score_super_b1,
    "score_li_daxiao_bottom": score_li_daxiao_bottom,
    "score_tide_leader": score_tide_leader,
    "score_tide_rotation": score_tide_rotation,
    "score_tide_recovery": score_tide_recovery,
}

STRATEGY_SCORERS: dict[str, Callable[[list[dict[str, Any]]], dict[str, Any] | None]] = {
    strategy_id: _SCORER_BY_NAME[str(definition["scorer"])]
    for strategy_id, definition in STRATEGY_DEFINITIONS.items()
    if str(definition.get("scorer") or "") in _SCORER_BY_NAME
}


__all__ = [
    "B1_CORE_J_CEILING",
    "B1_WATCH_J_CEILING",
    "COMMON_MAX_BBI_DISTANCE_PCT",
    "LI_DAXIAO_HOT_TURNOVER",
    "LI_DAXIAO_MAX_BBI_DISTANCE",
    "LI_DAXIAO_MAX_DAILY_CHASE_PCT",
    "LI_DAXIAO_MAX_TURNOVER",
    "LI_DAXIAO_MIN_AMOUNT",
    "STRATEGY_SCORERS",
    "SECTOR_TIDE_STRATEGY_IDS",
    "StrategyScorer",
    "analyze_enriched_rows",
    "build_sector_tide_context",
    "candle_amplitude_pct",
    "candle_body_pct",
    "combine_z_yellow",
    "compute_bbi",
    "compute_ema",
    "compute_kdj",
    "enrich_rows",
    "find_n_structure_prior_low",
    "is_yang",
    "is_yin",
    "li_daxiao_bottom_stage",
    "moving_avg",
    "n_structure_ok",
    "pct_change",
    "pct_returns",
    "recent_b1_indices",
    "return_pct",
    "safe_float",
    "safe_round",
    "score_b2_confirm",
    "score_b3_accelerate",
    "score_breakout",
    "score_li_daxiao_bottom",
    "score_tide_leader",
    "score_tide_rotation",
    "score_tide_recovery",
    "score_shaofu_b1",
    "score_super_b1",
    "score_trend_pullback",
    "strategy_hard_blockers",
    "volatility_pct",
    "with_strategy_profile",
]
