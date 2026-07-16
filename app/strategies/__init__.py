"""Trading-strategy domain package.

The package owns strategy metadata, scoring, attribution, strategy-specific
exit rules, and prompt fragments. Market-data I/O, scheduling, persistence,
and simulated order execution stay in their respective application modules.
"""

from .registry import (
    ACTIVE_STRATEGY_ENV,
    CONFIGURABLE_STRATEGY_GROUPS,
    DISPLAY_STRATEGY_ORDER,
    STRATEGY_DEFINITIONS,
    STRATEGY_META,
    STRATEGY_POSITION_LIMIT_PCT,
    STRATEGY_SCORE_PROFILES,
    STRATEGY_SUITES,
    active_strategy_suite,
    enabled_strategy_ids,
    known_strategy_ids,
)
from .attribution import (
    apply_entry_strategy_mark,
    apply_exit_strategy_mark,
    classify_buy_strategy,
    classify_exit_rule,
)
from .exits import evaluate_strategy_time_exit
from .performance import track_strategy_performance
from .policy import candidate_buy_blockers, candidate_is_buyable, strategy_position_limit_pct
from .prompts import build_strategy_prompt_sections, format_preset_strategy_section
from .scoring import (
    SECTOR_TIDE_STRATEGY_IDS,
    STRATEGY_SCORERS,
    analyze_enriched_rows,
    build_sector_tide_context,
    enrich_rows,
)
from .selection import candidate_is_trade_ready, select_display_candidates, select_trade_candidates

__all__ = [
    "CONFIGURABLE_STRATEGY_GROUPS",
    "ACTIVE_STRATEGY_ENV",
    "DISPLAY_STRATEGY_ORDER",
    "STRATEGY_DEFINITIONS",
    "STRATEGY_META",
    "STRATEGY_POSITION_LIMIT_PCT",
    "STRATEGY_SCORE_PROFILES",
    "STRATEGY_SUITES",
    "STRATEGY_SCORERS",
    "SECTOR_TIDE_STRATEGY_IDS",
    "analyze_enriched_rows",
    "build_sector_tide_context",
    "active_strategy_suite",
    "apply_entry_strategy_mark",
    "apply_exit_strategy_mark",
    "build_strategy_prompt_sections",
    "candidate_buy_blockers",
    "candidate_is_buyable",
    "candidate_is_trade_ready",
    "classify_buy_strategy",
    "classify_exit_rule",
    "enabled_strategy_ids",
    "enrich_rows",
    "evaluate_strategy_time_exit",
    "format_preset_strategy_section",
    "known_strategy_ids",
    "select_display_candidates",
    "select_trade_candidates",
    "strategy_position_limit_pct",
    "track_strategy_performance",
]
