#!/usr/bin/env python3
"""Reproducible walk-forward diagnostics for the deterministic NiuOne suite.

The default run deliberately stops signal generation before the reserved
holdout.  It writes only research output under /tmp unless an explicit output
path is supplied; it never reads or writes paper-account state.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
for import_root in (PROJECT_ROOT, PROJECT_ROOT / "app", PROJECT_ROOT / "app" / "compat"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.backtesting.historical_data import (  # noqa: E402
    HistoricalFetchConfig,
    fetch_historical_data,
    normalize_a_share_symbol,
)
from app.backtesting.niuone_exits import NiuOneStrategyBacktestPolicy  # noqa: E402
from app.backtesting.selection import (  # noqa: E402
    BUILTIN_STRATEGY_HISTORY_LIMIT,
    HistoricalBar,
    NiuOneHistoricalContextProvider,
    RegisteredScorerSelector,
    ReplaySelectionStrategy,
    SelectionBacktestConfig,
    SelectionContext,
    SelectionReplayFrame,
    SelectionReplayTape,
    SelectionSignal,
    build_selection_replay_tape,
    run_selection_backtest,
)
from app.backtesting.service import (  # noqa: E402
    _annotated_bars,
    load_current_industry_map,
    load_current_theme_map,
)
from app.backtesting.tasks import (  # noqa: E402
    _selector_for_request,
    load_strategy_universe,
    normalize_backtest_request,
)
from app.strategies.scoring.common import (  # noqa: E402
    compute_ema,
    niu_reversal_entry_stage_blocker,
    with_strategy_profile,
)
from app.strategies.scoring import STRATEGY_SCORERS  # noqa: E402
from app.strategies.scoring.niuone import score_niu_pullback  # noqa: E402
from app.strategies.exits import (  # noqa: E402
    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES,
)
from app.strategies.lifecycle import (  # noqa: E402
    niuone_lifecycle_entry_blocker,
    niuone_lifecycle_metadata,
    niuone_lifecycle_stage,
    niuone_lifecycle_transition,
)
from app.strategies.policy import (  # noqa: E402
    NIUONE_DAILY_V_MIN_RECOVERY_RATIO,
    niu_leader_entry_breadth_blocker,
    niu_reversal_continuation_blocker,
    niu_reversal_recovery_blocker,
)
from app.strategies.selection import strategy_daily_candidate_limit  # noqa: E402
from app.screening.stock_universe import stock_in_universe  # noqa: E402


DEVELOPMENT_WINDOWS = {
    "old_sealed": ("2025-05-01", "2025-07-31"),
    "train_a": ("2025-08-01", "2025-10-31"),
    "train_b": ("2025-11-01", "2026-01-31"),
    "validation": ("2026-02-01", "2026-04-30"),
    "recent": ("2026-04-28", "2026-06-27"),
}

# Frozen input for the rejected Round32 experiment. It is intentionally local
# because production no longer has a theme-level intraday V-recovery rule.
REJECTED_ROUND32_MIN_TODAY_BREADTH_PCT = 60.0
PRIMARY_DEVELOPMENT_WINDOW_NAMES = (
    "old_sealed",
    "train_a",
    "train_b",
    "validation",
)
HOLDOUT_WINDOWS = {
    "holdout_2026_06_30": ("2026-06-30", "2026-07-24"),
}
DEFAULT_OUTPUT = Path("/tmp/niuone-walk-forward-development.json")
PRODUCTION_REVERSAL_SIGNALS_PER_SESSION = (
    strategy_daily_candidate_limit("niu_reversal_probe") or 1
)
SCORER_EXIT_FIELDS = (
    "market_regime",
    "market_allows_buys",
    "mainline_score",
    "mainline_state",
    "mainline_cross_day_persistent",
    "mainline_confirmed",
    "market_hard_stop",
    "stock_leader_rank",
    "stock_leader_tier",
    "stock_strong",
    "atr20",
    "atr",
    "industry",
    "stop_price",
    "stop_source",
    "gap_buffer_pct",
    "execution_buffer_pct",
    "niuone_lifecycle_stage",
    "niuone_lifecycle_label",
    "niuone_lifecycle_entry_policy",
)

THEME_CROSS_SECTION_FIELDS = (
    "state",
    "raw_state",
    "intraday_state",
    "score",
    "score_change",
    "state_streak",
    "cross_day_persistent",
    "cross_day_confirmed",
    "mainline_confirmed",
    "strong_stock_count",
    "effective_strong_count",
    "leader_concentration",
    "single_stock_dominated",
    "today_eligible_data",
    "today_breadth_pct",
    "today_strength_score",
    "today_leadership_score",
    "niuone_lifecycle_stage",
    "niuone_lifecycle_label",
    "niuone_lifecycle_order",
    "niuone_lifecycle_entry_policy",
)

STAGE_FEATURE_FIELDS = (
    "mainline_score",
    "mainline_score_change",
    "mainline_state",
    "mainline_state_streak",
    "mainline_cross_day_persistent",
    "mainline_confirmed",
    "strong_stock_count",
    "effective_strong_count",
    "leader_concentration",
    "single_stock_dominated",
    "today_breadth_pct",
    "today_strength_score",
    "stock_strong",
    "stock_leader_tier",
    "stock_reversal_strong",
    "stock_reversal_leader_tier",
    "stock_sector_rank",
    "reversal_signal_score",
    "reversal_candidate_count",
    "reversal_candidate_rank",
    "reversal_top_score_gap",
    "mainline_theme_rank",
    "mainline_theme_previous_rank",
    "mainline_theme_rank_change",
    "mainline_theme_count",
    "mainline_theme_percentile",
    "mainline_theme_percentile_change",
    "mainline_theme_score_gap_to_top",
    "mainline_theme_top5",
    "mainline_theme_new_top5",
    "mainline_theme_rank_scope",
    "niuone_lifecycle_stage",
    "niuone_lifecycle_label",
    "niuone_lifecycle_entry_policy",
)

REPLAY_CACHE_FORMAT = "niuone-stage-replay-v2"
ROUND17_PULLBACK_SOURCE_THRESHOLD = 7.0
ROUND17_PULLBACK_VARIANT_IDS = (
    "production_ema20",
    "control_ema20_chase_only",
    "control_other_gates_only",
    "ema20_same_session_atr050",
    "ema20_prior_confirm_atr050",
    "ema20_prior_confirm_atr075",
    "ema10_confirm_atr025",
    "ema10_confirm_atr050",
    "ema20_or_ema10_confirm_atr050",
    "shallow_structure_confirm",
)
ROUND18_PULLBACK_RECOVERY_SOURCE_THRESHOLD = 7.0


def _progress(prefix: str) -> Callable[[int, int, str, bool], None]:
    last_bucket = -1

    def report(completed: int, total: int, _symbol: str, _ok: bool) -> None:
        nonlocal last_bucket
        bucket = int(completed / max(1, total) * 10)
        if bucket != last_bucket or completed == total:
            last_bucket = bucket
            print(f"{prefix}: {completed}/{total}", flush=True)

    return report


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _bar_payload(bar: HistoricalBar) -> dict[str, Any]:
    payload = dict(bar.extras)
    payload.update({
        "symbol": bar.symbol,
        "date": bar.date,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "amount": bar.amount,
        "turnover": bar.turnover,
        "previous_close": bar.previous_close,
        "limit_up": bar.limit_up,
        "limit_down": bar.limit_down,
        "suspended": bar.suspended,
        "is_st": bar.is_st,
        "name": bar.name,
        "industry": bar.industry,
    })
    return payload


def _write_replay_cache(
    path: Path,
    *,
    bars: Mapping[
        str,
        Iterable[HistoricalBar] | Mapping[str, HistoricalBar],
    ],
    tape: SelectionReplayTape,
    metadata: Mapping[str, Any],
) -> None:
    """Atomically save public research bars and the expensive replay tape."""
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "kind": "metadata",
            "format": REPLAY_CACHE_FORMAT,
            "metadata": _plain(metadata),
        }, ensure_ascii=False) + "\n")
        for symbol, series in bars.items():
            rows = series.values() if isinstance(series, Mapping) else series
            handle.write(json.dumps({
                "kind": "bars",
                "symbol": symbol,
                "rows": [_bar_payload(bar) for bar in rows],
            }, ensure_ascii=False) + "\n")
        for frame in tape.frames.values():
            handle.write(json.dumps({
                "kind": "frame",
                "date": frame.date,
                "signals": [
                    {
                        "symbol": signal.symbol,
                        "strategy_id": signal.strategy_id,
                        "reason": signal.reason,
                        "score": signal.score,
                        "metadata": _plain(signal.metadata),
                    }
                    for signal in frame.signals
                ],
                "scored": _plain(frame.scored),
                "cross_section": _plain(frame.cross_section),
            }, ensure_ascii=False) + "\n")
        handle.write(json.dumps({
            "kind": "diagnostics",
            "value": _plain(tape.diagnostics),
        }, ensure_ascii=False) + "\n")
    os.replace(temporary, target)


def _load_replay_cache(
    path: Path,
) -> tuple[Mapping[str, tuple[HistoricalBar, ...]], SelectionReplayTape, dict[str, Any]]:
    """Load a cache written by this script; the format is JSONL, not pickle."""
    bars: dict[str, tuple[HistoricalBar, ...]] = {}
    frames: dict[str, SelectionReplayFrame] = {}
    metadata: dict[str, Any] = {}
    diagnostics: dict[str, Any] = {}
    with gzip.open(path.expanduser().resolve(), "rt", encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            kind = str(item.get("kind") or "")
            if kind == "metadata":
                if item.get("format") != REPLAY_CACHE_FORMAT:
                    raise ValueError("unsupported NiuOne replay cache format")
                metadata = dict(item.get("metadata") or {})
            elif kind == "bars":
                symbol = str(item.get("symbol") or "")
                bars[symbol] = tuple(
                    HistoricalBar.from_value(symbol, row)
                    for row in (item.get("rows") or ())
                )
            elif kind == "frame":
                date = str(item.get("date") or "")
                signals = tuple(
                    SelectionSignal(
                        symbol=str(signal.get("symbol") or ""),
                        strategy_id=str(signal.get("strategy_id") or ""),
                        reason=str(signal.get("reason") or ""),
                        score=signal.get("score"),
                        metadata=dict(signal.get("metadata") or {}),
                    )
                    for signal in (item.get("signals") or ())
                )
                scored = {
                    str(symbol): MappingProxyType({
                        str(strategy_id): MappingProxyType(dict(values or {}))
                        for strategy_id, values in (by_strategy or {}).items()
                    })
                    for symbol, by_strategy in (item.get("scored") or {}).items()
                }
                cross_section = {
                    str(item_id): MappingProxyType(dict(values or {}))
                    for item_id, values in (item.get("cross_section") or {}).items()
                }
                frames[date] = SelectionReplayFrame(
                    date=date,
                    signals=signals,
                    scored=MappingProxyType(scored),
                    cross_section=MappingProxyType(cross_section),
                )
            elif kind == "diagnostics":
                diagnostics = dict(item.get("value") or {})
    if not metadata or not bars or not frames:
        raise ValueError("NiuOne replay cache is incomplete")
    return (
        MappingProxyType(bars),
        SelectionReplayTape(
            frames=MappingProxyType(frames),
            diagnostics=MappingProxyType(diagnostics),
        ),
        metadata,
    )


def _require_historical_coverage(
    data: Any,
    *,
    reference_count: int,
    source: str,
) -> None:
    """Fail before annotation/replay when a market source returned no bars."""
    successful_count = len(getattr(data, "series", {}) or {})
    if successful_count:
        return
    raise RuntimeError(
        f"historical source {source!r} returned zero usable series "
        f"for {reference_count} reference symbols; aborting before replay"
    )




def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _pullback_support_touch(
    row: Mapping[str, Any],
    *,
    moving_average: float | None,
    atr: float,
    upper_tolerance_atr: float,
) -> bool:
    """Return whether one known session tested a bounded moving-average band."""
    low = _number(row.get("low"))
    if low is None or moving_average is None or moving_average <= 0 or atr <= 0:
        return False
    distance_atr = (low - moving_average) / atr
    return bool(-1.0 <= distance_atr <= upper_tolerance_atr)


def _pullback_research_geometries(
    rows: list[dict[str, Any]],
    payload: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Measure time-safe pullback shapes without changing the live scorer.

    Production currently combines a four-session EMA20 touch with the current
    session's volume condition.  The research variants below separate a
    same-session shrinking support test from a prior-session touch followed by
    a current-session confirmation.  EMA10 and recent-structure alternatives
    test whether the mature leader cohort normally turns before reaching EMA20.
    """
    if len(rows) < 10:
        return {}
    atr = _number(payload.get("atr"))
    close = _number(payload.get("recent_close"))
    ema20 = _number(payload.get("ema20"))
    prior_close = _number(rows[-2].get("close"))
    change_pct = _number(payload.get("change_pct"))
    volume_ratio = _number(payload.get("volume_ratio"))
    if (
        atr is None
        or atr <= 0
        or close is None
        or close <= 0
        or ema20 is None
        or ema20 <= 0
        or prior_close is None
        or prior_close <= 0
        or change_pct is None
        or volume_ratio is None
    ):
        return {}

    closes = [_number(row.get("close")) for row in rows]
    if any(value is None or value <= 0 for value in closes):
        return {}
    ema10_values = compute_ema(
        [float(value) for value in closes if value is not None],
        10,
    )
    current_ema10 = float(ema10_values[-1])
    current_date = str(rows[-1].get("date") or "")[:10]
    prior_indexes = range(max(0, len(rows) - 4), len(rows) - 1)
    confirmation = bool(
        close > prior_close
        and change_pct > 0
        and volume_ratio >= 0.8
    )

    def prior_touch(
        *,
        field: str,
        tolerance: float,
    ) -> tuple[bool, str]:
        for index in reversed(tuple(prior_indexes)):
            average = (
                float(ema10_values[index])
                if field == "ema10"
                else _number(rows[index].get("ema20"))
            )
            if _pullback_support_touch(
                rows[index],
                moving_average=average,
                atr=atr,
                upper_tolerance_atr=tolerance,
            ):
                return True, str(rows[index].get("date") or "")[:10]
        return False, ""

    def same_session(
        *,
        average: float,
        tolerance: float,
    ) -> bool:
        return bool(
            _pullback_support_touch(
                rows[-1],
                moving_average=average,
                atr=atr,
                upper_tolerance_atr=tolerance,
            )
            and close >= average
            and volume_ratio <= 1.15
            and change_pct >= -0.8
        )

    ema20_same = same_session(average=ema20, tolerance=0.5)
    ema20_prior_050, ema20_prior_050_date = prior_touch(
        field="ema20",
        tolerance=0.5,
    )
    ema20_prior_075, ema20_prior_075_date = prior_touch(
        field="ema20",
        tolerance=0.75,
    )
    ema10_same_025 = same_session(average=current_ema10, tolerance=0.25)
    ema10_same_050 = same_session(average=current_ema10, tolerance=0.5)
    ema10_prior_025, ema10_prior_025_date = prior_touch(
        field="ema10",
        tolerance=0.25,
    )
    ema10_prior_050, ema10_prior_050_date = prior_touch(
        field="ema10",
        tolerance=0.5,
    )
    ema20_extension = (close - ema20) / atr
    ema10_extension = (close - current_ema10) / atr

    prior_rows = rows[max(0, len(rows) - 5): -1]
    recent_low_row = min(
        prior_rows,
        key=lambda row: _number(row.get("low")) or math.inf,
    )
    recent_low = _number(recent_low_row.get("low"))
    prior_highs = [
        value
        for row in rows[max(0, len(rows) - 9): -1]
        if (value := _number(row.get("high"))) is not None and value > 0
    ]
    prior_peak = max(prior_highs) if prior_highs else None
    shallow_depth_atr = (
        (prior_peak - recent_low) / atr
        if prior_peak is not None and recent_low is not None
        else None
    )
    shallow_extension_atr = (
        (close - recent_low) / atr if recent_low is not None else None
    )
    shallow_matched = bool(
        confirmation
        and recent_low is not None
        and recent_low >= ema20 - atr * 0.5
        and prior_peak is not None
        and close <= prior_peak * 1.002
        and shallow_depth_atr is not None
        and 0.75 <= shallow_depth_atr <= 2.5
    )

    def geometry(
        matched: bool,
        *,
        extension: float,
        anchor: str,
        support_date: str = "",
    ) -> dict[str, Any]:
        return {
            "matched": bool(matched),
            "entry_extension_atr": round(float(extension), 6),
            "entry_extension_source": anchor,
            "support_date": support_date,
        }

    production_matched = bool(payload.get("pullback") or payload.get("reclaim"))
    production_setup = str(payload.get("entry_setup") or "production_ema20")
    return {
        "production_ema20": geometry(
            production_matched,
            extension=ema20_extension,
            anchor=production_setup,
            support_date=current_date if production_matched else "",
        ),
        "control_ema20_chase_only": geometry(
            True,
            extension=ema20_extension,
            anchor="ema20",
        ),
        "control_other_gates_only": geometry(
            True,
            extension=0.0,
            anchor="none",
        ),
        "ema20_same_session_atr050": geometry(
            ema20_same,
            extension=ema20_extension,
            anchor="ema20",
            support_date=current_date if ema20_same else "",
        ),
        "ema20_prior_confirm_atr050": geometry(
            ema20_prior_050 and confirmation and close >= ema20,
            extension=ema20_extension,
            anchor="ema20",
            support_date=ema20_prior_050_date,
        ),
        "ema20_prior_confirm_atr075": geometry(
            ema20_prior_075 and confirmation and close >= ema20,
            extension=ema20_extension,
            anchor="ema20",
            support_date=ema20_prior_075_date,
        ),
        "ema10_confirm_atr025": geometry(
            (ema10_same_025 or (ema10_prior_025 and confirmation))
            and close >= current_ema10,
            extension=ema10_extension,
            anchor="ema10",
            support_date=(current_date if ema10_same_025 else ema10_prior_025_date),
        ),
        "ema10_confirm_atr050": geometry(
            (ema10_same_050 or (ema10_prior_050 and confirmation))
            and close >= current_ema10,
            extension=ema10_extension,
            anchor="ema10",
            support_date=(current_date if ema10_same_050 else ema10_prior_050_date),
        ),
        "ema20_or_ema10_confirm_atr050": geometry(
            ema20_same
            or (ema20_prior_050 and confirmation and close >= ema20)
            or (
                (ema10_same_050 or (ema10_prior_050 and confirmation))
                and close >= current_ema10
            ),
            extension=min(ema20_extension, ema10_extension),
            anchor="nearest_confirmed_moving_average",
            support_date=(
                current_date
                if ema20_same or ema10_same_050
                else ema20_prior_050_date or ema10_prior_050_date
            ),
        ),
        "shallow_structure_confirm": geometry(
            shallow_matched,
            extension=(shallow_extension_atr or 0.0),
            anchor="recent_structure_low",
            support_date=str(recent_low_row.get("date") or "")[:10],
        ),
    }


def _research_pullback_scorer(
    rows: list[dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, Any] | None:
    """Emit the union of Round17 pullback variants at a wide score floor."""
    production = score_niu_pullback(rows, context)
    if production is None:
        return None
    variants = _pullback_research_geometries(rows, production)
    if set(variants) != set(ROUND17_PULLBACK_VARIANT_IDS):
        return production
    research = dict(production)
    research.update({
        "pullback": True,
        "reclaim": False,
        "entry_setup": "pullback_research_union",
        "entry_extension_atr": 0.0,
        "entry_extension_source": "research_union",
        "pullback_research_source": True,
        "pullback_research_variants": variants,
    })
    scored = with_strategy_profile("niu_pullback", research)
    scored["entry_threshold"] = ROUND17_PULLBACK_SOURCE_THRESHOLD
    scored["actionable"] = bool(
        float(scored.get("score") or 0.0) >= ROUND17_PULLBACK_SOURCE_THRESHOLD
        and not scored.get("hard_blockers")
    )
    return scored


_research_pullback_scorer.requires_context = True  # type: ignore[attr-defined]


def _merge_pullback_research_tape(
    base_tape: SelectionReplayTape,
    pullback_tape: SelectionReplayTape,
) -> SelectionReplayTape:
    """Replace wide pullback rows and restore full-suite decision ordering."""
    frames: dict[str, SelectionReplayFrame] = {}
    for date in sorted(set(base_tape.frames) | set(pullback_tape.frames)):
        base_frame = base_tape.frames.get(date)
        pullback_frame = pullback_tape.frames.get(date)
        signals = [
            signal
            for signal in (base_frame.signals if base_frame else ())
            if signal.strategy_id != "niu_pullback"
        ]
        signals.extend(pullback_frame.signals if pullback_frame else ())

        def signal_rank(signal: SelectionSignal) -> tuple[float, float, int, str]:
            scored = signal.metadata.get("scored")
            values = scored if isinstance(scored, Mapping) else {}
            score = _number(signal.score) or 0.0
            return (
                -(_number(values.get("decision_score")) or score),
                -score,
                -int(_number(values.get("strategy_priority")) or 0),
                signal.symbol,
            )

        scored: dict[str, Mapping[str, Mapping[str, Any]]] = {}
        for frame in (base_frame, pullback_frame):
            if frame is None:
                continue
            for symbol, by_strategy in frame.scored.items():
                combined = dict(scored.get(symbol) or {})
                combined.update(by_strategy)
                scored[symbol] = MappingProxyType(combined)
        frames[date] = SelectionReplayFrame(
            date=date,
            signals=tuple(sorted(signals, key=signal_rank)),
            scored=MappingProxyType(scored),
            cross_section=(
                base_frame.cross_section
                if base_frame is not None and base_frame.cross_section
                else (
                    pullback_frame.cross_section
                    if pullback_frame is not None
                    else MappingProxyType({})
                )
            ),
        )
    base_diagnostics = _plain(base_tape.diagnostics)
    return SelectionReplayTape(
        frames=MappingProxyType(frames),
        diagnostics=MappingProxyType({
            "warnings": list(base_diagnostics.get("warnings") or ()),
            "base": base_diagnostics,
            "pullback_research": _plain(pullback_tape.diagnostics),
        }),
    )


def _return_summary(values: Iterable[float]) -> dict[str, Any]:
    returns = [float(value) for value in values]
    gains = sum(value for value in returns if value > 0)
    losses = abs(sum(value for value in returns if value < 0))
    return {
        "count": len(returns),
        "win_rate_pct": (
            round(sum(value > 0 for value in returns) / len(returns) * 100, 4)
            if returns else None
        ),
        "average_return_pct": round(statistics.mean(returns), 4) if returns else None,
        "median_return_pct": round(statistics.median(returns), 4) if returns else None,
        "profit_factor": round(gains / losses, 4) if losses > 0 else None,
    }


def _bucket(value: float | None, boundaries: tuple[float, ...]) -> str:
    if value is None:
        return "missing"
    lower = "-inf"
    for boundary in boundaries:
        if value < boundary:
            return f"[{lower},{boundary:g})"
        lower = f"{boundary:g}"
    return f"[{lower},inf)"


def _trade_features(result: Any) -> list[dict[str, Any]]:
    signal_by_trade_date: dict[tuple[str, str], Mapping[str, Any]] = {}
    first_signal_by_trade: dict[str, Mapping[str, Any]] = {}
    signals_by_trade: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in result.signals:
        trade_id = str(row.get("trade_id") or "")
        if not trade_id:
            continue
        signal_date = str(row.get("signal_date") or "")
        signal_by_trade_date[(trade_id, signal_date)] = row
        first_signal_by_trade.setdefault(trade_id, row)
        signals_by_trade[trade_id].append(row)
    rows: list[dict[str, Any]] = []
    for trade in result.trades:
        if trade.get("status") != "completed":
            continue
        trade_id = str(trade.get("id") or "")
        trade_signal_date = str(trade.get("signal_date") or "")
        signal = signal_by_trade_date.get(
            (trade_id, trade_signal_date),
            first_signal_by_trade.get(trade_id),
        )
        if signal is None:
            continue
        metadata = signal.get("metadata")
        scored = metadata.get("scored") if isinstance(metadata, Mapping) else None
        scored = scored if isinstance(scored, Mapping) else {}
        lifecycle = niuone_lifecycle_metadata(scored)
        signal_close = _number(scored.get("recent_close"))
        entry_open = _number(signal.get("entry_open"))
        entry_price = _number(trade.get("entry_price"))
        entry_total_equity = _number(signal.get("entry_total_equity"))
        entry_units = _number(signal.get("entry_units"))
        first_entry_price = _number(signal.get("entry_price"))
        entry_target_position_pct = _number(
            signal.get("entry_target_position_pct")
        )
        stop_price = _number(scored.get("stop_price"))
        entry_strategy_id = str(trade.get("strategy_id") or "")
        strategy_path = [
            str(item)
            for item in trade.get("strategy_path") or ()
            if item
        ]
        if not strategy_path and entry_strategy_id:
            strategy_path.append(entry_strategy_id)
        exit_leg_signals = [
            str(leg.get("signal") or "")
            for leg in trade.get("exit_legs") or ()
            if isinstance(leg, Mapping) and leg.get("signal")
        ]
        add_signals = [
            row
            for row in signals_by_trade.get(trade_id, ())
            if row.get("status") == "evaluated"
            and row.get("entry_action") == "add"
        ]
        add_legs = [
            {
                "signal_date": str(row.get("signal_date") or ""),
                "entry_date": str(row.get("entry_date") or ""),
                "strategy_id": str(row.get("strategy_id") or ""),
                "holding_upgrade_mode": str(
                    row.get("metadata", {}).get("holding_upgrade_mode") or ""
                ),
                "markup_rebalance_reentry": bool(
                    row.get("metadata", {}).get(
                        "niuone_markup_rebalance_reentry"
                    ) is True
                ),
                "signal_pnl_pct": _number(
                    row.get("metadata", {}).get("holding_upgrade_signal_pnl_pct")
                ),
                "target_position_pct": _number(
                    row.get("entry_target_position_pct")
                ),
                "position_before_trade_pct": _number(
                    row.get("entry_position_before_trade_pct")
                ),
                "order_position_pct": _number(
                    row.get("entry_order_position_pct")
                ),
                "position_after_trade_pct": _number(
                    row.get("entry_position_after_trade_pct")
                ),
                "effective_loss_distance_pct": _number(
                    row.get("entry_effective_loss_distance_pct")
                ),
                "mainline_state": str(
                    row.get("metadata", {}).get("scored", {}).get(
                        "mainline_state"
                    ) or ""
                ),
                "mainline_confirmed": (
                    row.get("metadata", {}).get("scored", {}).get(
                        "mainline_confirmed"
                    ) is True
                ),
                "stock_strong": (
                    row.get("metadata", {}).get("scored", {}).get(
                        "stock_strong"
                    ) is True
                ),
                "stock_leader_tier": (
                    row.get("metadata", {}).get("scored", {}).get(
                        "stock_leader_tier"
                    ) is True
                ),
                "lifecycle_stage": niuone_lifecycle_metadata(
                    row.get("metadata", {}).get("scored", {})
                )["niuone_lifecycle_stage"],
            }
            for row in add_signals
        ]
        rows.append({
            "window_signal_date": str(signal.get("signal_date") or ""),
            "entry_date": str(trade.get("entry_date") or ""),
            "exit_date": str(trade.get("exit_date") or ""),
            "holding_sessions": int(trade.get("holding_sessions") or 0),
            "symbol": str(trade.get("symbol") or ""),
            "strategy_id": entry_strategy_id,
            "current_strategy_id": str(
                trade.get("current_strategy_id")
                or (strategy_path[-1] if strategy_path else entry_strategy_id)
            ),
            "strategy_path": strategy_path,
            "net_return_pct": _number(trade.get("net_return_pct")),
            "exit_signal": str(trade.get("exit_signal") or ""),
            "exit_leg_signals": exit_leg_signals,
            "add_order_count": len(add_legs),
            "add_legs": add_legs,
            "lifecycle_climax_partial_count": sum(
                signal == "niu_lifecycle_climax_partial"
                for signal in exit_leg_signals
            ),
            "markup_rebalance_trim_count": sum(
                signal == "niu_markup_rebalance_partial"
                for signal in exit_leg_signals
            ),
            "markup_rebalance_reentry_count": sum(
                bool(leg.get("markup_rebalance_reentry"))
                for leg in add_legs
            ),
            "market_regime": str(scored.get("market_regime") or "missing"),
            "mainline_state": str(scored.get("mainline_state") or "missing"),
            **{
                field_name: scored.get(field_name)
                for field_name in STAGE_FEATURE_FIELDS
                if field_name not in {
                    "mainline_state",
                    "niuone_lifecycle_stage",
                    "niuone_lifecycle_label",
                    "niuone_lifecycle_entry_policy",
                }
            },
            **lifecycle,
            "right_days": _number(scored.get("daily_v_right_days")),
            "left_days": _number(scored.get("daily_v_left_days")),
            "decline_pct": _number(scored.get("daily_v_decline_pct")),
            "rebound_pct": _number(scored.get("daily_v_rebound_pct")),
            "recovery_ratio": _number(scored.get("daily_v_recovery_ratio")),
            "rising_ratio": _number(scored.get("daily_v_rising_ratio")),
            "pattern_score": _number(scored.get("daily_v_pattern_score")),
            "signal_change_pct": _number(scored.get("change_pct")),
            "entry_extension_atr": _number(scored.get("entry_extension_atr")),
            "signal_stop_distance_pct": _number(scored.get("stop_distance_pct")),
            "entry_target_position_pct": entry_target_position_pct,
            "entry_actual_position_pct": (
                round(
                    first_entry_price * entry_units / entry_total_equity * 100,
                    4,
                )
                if first_entry_price and entry_units and entry_total_equity else None
            ),
            "entry_effective_loss_distance_pct": _number(
                signal.get("entry_effective_loss_distance_pct")
            ),
            "actual_stop_distance_pct": (
                round((entry_price - stop_price) / entry_price * 100, 4)
                if entry_price and stop_price and 0 < stop_price < entry_price else None
            ),
            "next_open_gap_pct": (
                round((entry_open / signal_close - 1) * 100, 4)
                if entry_open and signal_close else None
            ),
        })
    return rows


def _attach_exit_stage_context(
    rows: list[dict[str, Any]],
    tape: SelectionReplayTape | None,
) -> None:
    """Attach the historical-session scorer state used on each completed exit."""
    if tape is None:
        return
    ordered_frames = tuple(
        frame for _date, frame in sorted(tape.frames.items())
    )
    for row in rows:
        entry_date = str(row.get("entry_date") or "")
        exit_date = str(row.get("exit_date") or "")
        symbol = str(row.get("symbol") or "")
        strategy_ids = tuple(dict.fromkeys((
            str(row.get("current_strategy_id") or ""),
            *reversed(tuple(row.get("strategy_path") or ())),
            str(row.get("strategy_id") or ""),
        )))
        stage_path: list[str] = []
        lifecycle_path: list[str] = []
        persistent_sessions = 0
        confirmed_sessions = 0
        strong_leader_sessions = 0
        first_persistent = ""
        first_confirmed = ""
        first_strong_leader = ""
        first_by_stage: dict[str, str] = {}
        exit_scored: Mapping[str, Any] | None = None
        previous_lifecycle: dict[str, Any] = {}
        for frame in ordered_frames:
            if frame.date < entry_date or frame.date > exit_date:
                continue
            by_strategy = frame.scored.get(symbol)
            if not isinstance(by_strategy, Mapping):
                continue
            scored = next(
                (
                    by_strategy.get(strategy_id)
                    for strategy_id in strategy_ids
                    if strategy_id
                    and isinstance(by_strategy.get(strategy_id), Mapping)
                ),
                None,
            )
            if not isinstance(scored, Mapping):
                continue
            state = str(scored.get("mainline_state") or "")
            if state and (not stage_path or stage_path[-1] != state):
                stage_path.append(state)
            if state and state not in first_by_stage:
                first_by_stage[state] = frame.date
            lifecycle_stage = niuone_lifecycle_transition(
                previous_lifecycle,
                scored,
            )
            previous_lifecycle = {
                **dict(scored),
                "niuone_lifecycle_stage": lifecycle_stage,
            }
            if (
                lifecycle_stage
                and (
                    not lifecycle_path
                    or lifecycle_path[-1] != lifecycle_stage
                )
            ):
                lifecycle_path.append(lifecycle_stage)
            if scored.get("mainline_cross_day_persistent") is True:
                persistent_sessions += 1
                first_persistent = first_persistent or frame.date
            if scored.get("mainline_confirmed") is True:
                confirmed_sessions += 1
                first_confirmed = first_confirmed or frame.date
            if (
                scored.get("stock_leader_tier") is True
                and scored.get("stock_strong") is True
            ):
                strong_leader_sessions += 1
                first_strong_leader = first_strong_leader or frame.date
            if frame.date == exit_date:
                exit_scored = scored
        row.update({
            "holding_stage_path": stage_path,
            "holding_lifecycle_path": lifecycle_path,
            "holding_lifecycle_transition_count": max(
                0, len(lifecycle_path) - 1
            ),
            "holding_stage_transition_count": max(0, len(stage_path) - 1),
            "first_emerging_date": first_by_stage.get("emerging", ""),
            "first_mainline_date": first_by_stage.get("mainline", ""),
            "first_diverging_date": first_by_stage.get("diverging", ""),
            "first_cross_day_persistent_date": first_persistent,
            "first_mainline_confirmed_date": first_confirmed,
            "first_strong_leader_date": first_strong_leader,
            "cross_day_persistent_sessions": persistent_sessions,
            "mainline_confirmed_sessions": confirmed_sessions,
            "strong_leader_sessions": strong_leader_sessions,
        })
        if exit_scored is not None:
            for field_name in SCORER_EXIT_FIELDS:
                row[f"exit_{field_name}"] = exit_scored.get(field_name)


def _ordered_replay_tape(
    tape: SelectionReplayTape,
    strategy_order: Iterable[str] | None,
) -> SelectionReplayTape:
    """Return a tape with deterministic strategy precedence for research."""
    resolved_order = tuple(strategy_order or ())
    if not resolved_order:
        return tape
    rank = {strategy_id: index for index, strategy_id in enumerate(resolved_order)}
    frames = {
        date: SelectionReplayFrame(
            date=frame.date,
            signals=tuple(sorted(
                frame.signals,
                key=lambda signal: (
                    rank.get(signal.strategy_id, len(rank)),
                    -(float(signal.score) if signal.score is not None else -1.0),
                    signal.symbol,
                ),
            )),
            scored=frame.scored,
            cross_section=frame.cross_section,
        )
        for date, frame in tape.frames.items()
    }
    return SelectionReplayTape(
        frames=MappingProxyType(frames),
        diagnostics=tape.diagnostics,
    )


def _holding_upgrade_strategy_id(
    current_strategy_id: str,
    scored: Mapping[str, Any],
    mode: str,
    *,
    rebalance_armed: bool = False,
) -> str:
    """Return a historical-session holding upgrade stage for a research mode."""
    current = str(current_strategy_id or "")
    state = str(scored.get("mainline_state") or "")
    persistent = scored.get("mainline_cross_day_persistent") is True
    confirmed = scored.get("mainline_confirmed") is True
    strong_leader = bool(
        scored.get("stock_leader_tier") is True
        and scored.get("stock_strong") is True
    )
    lifecycle_stage = niuone_lifecycle_metadata(scored)[
        "niuone_lifecycle_stage"
    ]
    theme_rank = _number(scored.get("mainline_theme_rank"))
    full_theme_top5 = bool(
        scored.get("mainline_theme_rank_scope")
        == "full_historical_theme_cross_section"
        and theme_rank is not None
        and theme_rank <= 5
    )
    if (
        mode == "staged_markup_rebalance"
        and rebalance_armed
        and current in {"niu_reversal_probe", "niu_emerging", "niu_leader"}
        and confirmed
        and state == "mainline"
        and lifecycle_stage == "markup"
        and strong_leader
    ):
        return "niu_leader"
    if (
        mode in {"confirmed_mainline", "strong_leader_then_mainline"}
        and current in {"niu_reversal_probe", "niu_emerging"}
        and confirmed
        and state in {"mainline", "diverging"}
        and strong_leader
    ):
        return "niu_leader"
    if (
        mode in {
            "confirmed_markup", "staged_markup", "staged_markup_rebalance"
        }
        and current in {"niu_reversal_probe", "niu_emerging"}
        and confirmed
        and state == "mainline"
        and lifecycle_stage == "markup"
        and strong_leader
    ):
        return "niu_leader"
    if current != "niu_reversal_probe" or state != "emerging" or not persistent:
        return ""
    if (
        mode in {"staged_markup", "staged_markup_rebalance"}
        and lifecycle_stage == "markup"
        and strong_leader
    ):
        return "niu_emerging"
    if mode == "full_theme_top5_persistent" and full_theme_top5:
        return "niu_emerging"
    if (
        mode == "full_theme_new_top5_persistent"
        and full_theme_top5
        and scored.get("mainline_theme_new_top5") is True
    ):
        return "niu_emerging"
    if mode == "persistent_emerging":
        return "niu_emerging"
    if mode in {"strong_leader", "strong_leader_then_mainline"} and strong_leader:
        return "niu_emerging"
    return ""


class _HoldingUpgradeReplayStrategy(ReplaySelectionStrategy):
    """Add account-aware, next-session upgrade signals to a frozen tape."""

    def __init__(
        self,
        *args: Any,
        holding_upgrade_mode: str,
        holding_upgrade_min_pnl_pct: float | None = None,
        holding_upgrade_max_pnl_pct: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.holding_upgrade_mode = str(holding_upgrade_mode or "")
        self.holding_upgrade_min_pnl_pct = (
            float(holding_upgrade_min_pnl_pct)
            if holding_upgrade_min_pnl_pct is not None else None
        )
        self.holding_upgrade_max_pnl_pct = (
            float(holding_upgrade_max_pnl_pct)
            if holding_upgrade_max_pnl_pct is not None else None
        )
        if (
            self.holding_upgrade_min_pnl_pct is not None
            and self.holding_upgrade_max_pnl_pct is not None
            and self.holding_upgrade_max_pnl_pct
            < self.holding_upgrade_min_pnl_pct
        ):
            raise ValueError(
                "holding_upgrade_max_pnl_pct must be at least the minimum"
            )
        self._open_positions: Mapping[str, Mapping[str, Any]] = MappingProxyType({})

    def reset(self) -> None:
        super().reset()
        self._open_positions = MappingProxyType({})

    def set_exit_tracking_symbols(self, symbols: Iterable[str]) -> None:
        if isinstance(symbols, Mapping):
            self._open_positions = MappingProxyType({
                str(symbol): position
                for symbol, position in symbols.items()
                if isinstance(position, Mapping)
            })
        else:
            self._open_positions = MappingProxyType({})

    def on_close(self, context: SelectionContext) -> Iterable[SelectionSignal]:
        selections = list(super().on_close(context))
        if not self._signal_generation_enabled or not self._open_positions:
            return selections
        selected_symbols = {signal.symbol for signal in selections}
        for symbol, position in sorted(self._open_positions.items()):
            if symbol in selected_symbols:
                continue
            current_bar = context.bars.get(symbol)
            avg_cost = _number(position.get("avg_cost"))
            pnl_pct = (
                (float(current_bar.close) / avg_cost - 1.0) * 100.0
                if current_bar is not None and avg_cost and avg_cost > 0
                else None
            )
            rebalance_armed = bool(
                self.holding_upgrade_mode == "staged_markup_rebalance"
                and position.get("niuone_markup_rebalance_armed") is True
            )
            if (
                self.holding_upgrade_min_pnl_pct is not None
                and (
                    pnl_pct is None
                    or pnl_pct < self.holding_upgrade_min_pnl_pct
                )
            ):
                continue
            if (
                self.holding_upgrade_max_pnl_pct is not None
                and not rebalance_armed
                and (
                    pnl_pct is None
                    or pnl_pct > self.holding_upgrade_max_pnl_pct
                )
            ):
                continue
            current_strategy_id = str(position.get("strategy_id") or "")
            candidate_strategy_ids = (
                ("niu_leader", "niu_emerging")
                if self.holding_upgrade_mode in {
                    "strong_leader_then_mainline",
                    "staged_markup",
                    "staged_markup_rebalance",
                }
                else ("niu_leader",)
                if self.holding_upgrade_mode in {
                    "confirmed_mainline",
                    "confirmed_markup",
                }
                else ("niu_emerging",)
            )
            for candidate_strategy_id in candidate_strategy_ids:
                scored = self.latest_scored(symbol, candidate_strategy_id)
                if self.holding_upgrade_mode == "staged_markup_rebalance":
                    current_scored = self.latest_scored(
                        symbol,
                        current_strategy_id,
                    )
                    scored = {
                        **dict(position),
                        **(
                            dict(current_scored)
                            if isinstance(current_scored, Mapping)
                            else {}
                        ),
                        **(
                            dict(scored)
                            if isinstance(scored, Mapping)
                            else {}
                        ),
                    }
                if not isinstance(scored, Mapping):
                    continue
                upgrade_strategy_id = _holding_upgrade_strategy_id(
                    current_strategy_id,
                    scored,
                    self.holding_upgrade_mode,
                    rebalance_armed=rebalance_armed,
                )
                if upgrade_strategy_id != candidate_strategy_id:
                    continue
                rebalance_reentry = bool(
                    rebalance_armed and upgrade_strategy_id == "niu_leader"
                )
                if rebalance_reentry:
                    trigger_price = _number(
                        position.get("niuone_markup_rebalance_reentry_price")
                    )
                    if (
                        current_bar is None
                        or trigger_price is None
                        or float(current_bar.close) + 1e-9 < trigger_price
                        or str(
                            position.get("niuone_markup_rebalance_armed_date")
                            or ""
                        ) == context.date
                    ):
                        continue
                selections.append(SelectionSignal(
                    symbol=symbol,
                    strategy_id=upgrade_strategy_id,
                    score=float(scored.get("score") or 0.0),
                    reason=(
                        "持仓阶段确认后生成次交易日升级信号："
                        f"{current_strategy_id}->{upgrade_strategy_id}"
                    ),
                    metadata={
                        "holding_upgrade": True,
                        "holding_upgrade_mode": self.holding_upgrade_mode,
                        "holding_upgrade_signal_pnl_pct": (
                            round(pnl_pct, 4) if pnl_pct is not None else None
                        ),
                        "niuone_markup_rebalance_reentry": rebalance_reentry,
                        "scored": dict(scored),
                    },
                ))
                selected_symbols.add(symbol)
                break
        return selections


def _lifecycle_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    entry_counts: Counter[str] = Counter()
    final_counts: Counter[str] = Counter()
    path_counts: Counter[str] = Counter()
    path_returns: defaultdict[str, list[float]] = defaultdict(list)
    upgraded_trade_count = 0
    for row in rows:
        entry_strategy_id = str(row.get("strategy_id") or "missing")
        raw_path = row.get("strategy_path")
        strategy_path = [
            str(item)
            for item in raw_path or ()
            if item
        ]
        if not strategy_path:
            strategy_path.append(entry_strategy_id)
        current_strategy_id = str(
            row.get("current_strategy_id") or strategy_path[-1]
        )
        path_label = " -> ".join(strategy_path)
        entry_counts[entry_strategy_id] += 1
        final_counts[current_strategy_id] += 1
        path_counts[path_label] += 1
        if len(strategy_path) > 1 or current_strategy_id != entry_strategy_id:
            upgraded_trade_count += 1
        net_return_pct = _number(row.get("net_return_pct"))
        if net_return_pct is not None:
            path_returns[path_label].append(net_return_pct)
    completed_trade_count = len(rows)
    return {
        "completed_trade_count": completed_trade_count,
        "entry_stage_counts": dict(sorted(entry_counts.items())),
        "final_stage_counts": dict(sorted(final_counts.items())),
        "path_counts": dict(sorted(path_counts.items())),
        "path_performance": {
            key: _return_summary(values)
            for key, values in sorted(path_returns.items())
        },
        "upgraded_trade_count": upgraded_trade_count,
        "upgrade_rate_pct": (
            round(upgraded_trade_count / completed_trade_count * 100, 4)
            if completed_trade_count else None
        ),
    }


def _stage_trajectory_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize post-entry stage observations without treating them as filters."""
    reversal = [
        row for row in rows
        if row.get("strategy_id") == "niu_reversal_probe"
    ]

    def returns_where(predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        return _return_summary(
            float(row["net_return_pct"])
            for row in reversal
            if row.get("net_return_pct") is not None and predicate(row)
        )

    group_definitions: dict[str, Callable[[dict[str, Any]], bool]] = {
        "cross_day_persistent": lambda row: bool(
            row.get("first_cross_day_persistent_date")
        ),
        "mainline_confirmed": lambda row: bool(
            row.get("first_mainline_confirmed_date")
        ),
        "strong_leader": lambda row: bool(row.get("first_strong_leader_date")),
        "mainline_state": lambda row: bool(row.get("first_mainline_date")),
        "diverging_state": lambda row: bool(row.get("first_diverging_date")),
        "stage_transition": lambda row: (
            int(row.get("holding_stage_transition_count") or 0) > 0
        ),
    }
    return {
        "interpretation": "post_entry_descriptive_only",
        "completed_trade_count": len(rows),
        "reversal_trade_count": len(reversal),
        "coverage": {
            "entry_date_count": sum(bool(row.get("entry_date")) for row in rows),
            "holding_stage_path_count": sum(
                bool(row.get("holding_stage_path")) for row in rows
            ),
            "exit_stage_context_count": sum(
                bool(row.get("exit_mainline_state")) for row in rows
            ),
        },
        "reversal_groups": {
            name: {
                "observed": returns_where(predicate),
                "not_observed": returns_where(
                    lambda row, predicate=predicate: not predicate(row)
                ),
            }
            for name, predicate in group_definitions.items()
        },
    }


def _feature_groups(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reversal = [row for row in rows if row["strategy_id"] == "niu_reversal_probe"]
    definitions: dict[str, Callable[[dict[str, Any]], str]] = {
        "market_regime": lambda row: str(row["market_regime"]),
        "mainline_state": lambda row: str(row["mainline_state"]),
        "right_days": lambda row: str(int(row["right_days"])) if row["right_days"] is not None else "missing",
        "recovery_ratio": lambda row: _bucket(row["recovery_ratio"], (0.65, 0.8, 1.0)),
        "rising_ratio": lambda row: _bucket(row["rising_ratio"], (0.75, 0.999)),
        "decline_pct": lambda row: _bucket(row["decline_pct"], (12.0, 18.0)),
        "rebound_pct": lambda row: _bucket(row["rebound_pct"], (10.0, 15.0)),
        "pattern_score": lambda row: _bucket(row["pattern_score"], (90.0, 95.0, 98.0)),
        "signal_change_pct": lambda row: _bucket(row["signal_change_pct"], (0.0, 3.0, 5.0)),
        "entry_extension_atr": lambda row: _bucket(row["entry_extension_atr"], (1.1, 1.25, 1.4)),
        "actual_stop_distance_pct": lambda row: _bucket(row["actual_stop_distance_pct"], (3.0, 4.5, 6.0)),
        "next_open_gap_pct": lambda row: _bucket(row["next_open_gap_pct"], (-1.0, 0.0, 1.0, 2.0)),
    }
    output: dict[str, Any] = {}
    for feature, grouper in definitions.items():
        grouped: defaultdict[str, list[float]] = defaultdict(list)
        for row in reversal:
            value = row.get("net_return_pct")
            if value is not None:
                grouped[grouper(row)].append(float(value))
        output[feature] = {
            key: _return_summary(values)
            for key, values in sorted(grouped.items())
        }
    output["exit_signal"] = {
        key: _return_summary(
            float(row["net_return_pct"])
            for row in reversal
            if row["exit_signal"] == key and row["net_return_pct"] is not None
        )
        for key in sorted({row["exit_signal"] for row in reversal})
    }
    return output


def _run_window(
    bars: Mapping[str, Any],
    tape: Any,
    start: str,
    end: str,
    *,
    signal_filter: Callable[[SelectionSignal], bool] | None = None,
    policy_options: Mapping[str, Any] | None = None,
    signal_order: Iterable[str] | None = None,
    holding_upgrade_mode: str | None = None,
    holding_upgrade_min_pnl_pct: float | None = None,
    holding_upgrade_max_pnl_pct: float | None = None,
    reversal_signals_per_session: int = (
        PRODUCTION_REVERSAL_SIGNALS_PER_SESSION
    ),
) -> tuple[Any, dict[str, Any]]:
    resolved_policy_options = dict(policy_options or {})
    # Explicit research candidates remain historical, comparable benchmarks.
    # Omitting policy_options means replay the current production defaults.
    if policy_options is not None:
        resolved_policy_options.setdefault("reversal_early_profit_regimes", ())
    selector_options = {
        "signal_filter": signal_filter,
        "max_signals_per_strategy_per_session": {
            "niu_reversal_probe": int(reversal_signals_per_session),
        },
    }
    replay_selector = (
        _HoldingUpgradeReplayStrategy(
            _ordered_replay_tape(tape, signal_order),
            holding_upgrade_mode=holding_upgrade_mode,
            holding_upgrade_min_pnl_pct=holding_upgrade_min_pnl_pct,
            holding_upgrade_max_pnl_pct=holding_upgrade_max_pnl_pct,
            **selector_options,
        )
        if holding_upgrade_mode
        else ReplaySelectionStrategy(
            _ordered_replay_tape(tape, signal_order),
            **selector_options,
        )
    )
    result = run_selection_backtest(
        bars,
        replay_selector,
        config=SelectionBacktestConfig(
            holding_sessions=(1, 3, 5, 10, 20),
            signal_start_date=start,
            signal_end_date=end,
            cooldown_sessions=0,
            slippage_bps=5,
        ),
        position_exit_strategy=NiuOneStrategyBacktestPolicy(
            **resolved_policy_options
        ),
    )
    features = _trade_features(result)
    _attach_exit_stage_context(features, tape)
    return result, {
        "statistics": _plain(result.statistics),
        "portfolio": _plain(result.portfolio),
        "lifecycle": _lifecycle_summary(features),
        "reversal_feature_groups": _feature_groups(features),
        "completed_trade_features": features,
    }


def _reversal_filter(
    *,
    minimum_right_days: int = 0,
    minimum_extension_atr: float = 0.0,
) -> Callable[[SelectionSignal], bool]:
    def accepted(signal: SelectionSignal) -> bool:
        if signal.strategy_id != "niu_reversal_probe":
            return True
        scored = signal.metadata.get("scored")
        if not isinstance(scored, Mapping):
            return False
        right_days = _number(scored.get("daily_v_right_days")) or 0.0
        extension = _number(scored.get("entry_extension_atr")) or 0.0
        return bool(
            right_days >= minimum_right_days
            and extension >= minimum_extension_atr
        )

    return accepted


def _production_stage_filter(signal: SelectionSignal) -> bool:
    """Apply the canonical production lifecycle/action route to frozen tape."""
    scored = signal.metadata.get("scored")
    if not isinstance(scored, Mapping):
        return signal.strategy_id != "niu_reversal_probe"
    stage = str(scored.get("niuone_lifecycle_stage") or "").strip()
    if not stage:
        stage = niuone_lifecycle_stage(scored)
    if (
        stage
        and niuone_lifecycle_entry_blocker(signal.strategy_id, scored)
        is not None
    ):
        return False
    if signal.strategy_id == "niu_reversal_probe":
        return bool(
            niu_reversal_entry_stage_blocker(scored) is None
            and niu_reversal_recovery_blocker(scored) is None
            and niu_reversal_continuation_blocker(scored) is None
        )
    if signal.strategy_id == "niu_leader":
        return bool(
            niu_leader_entry_breadth_blocker(scored) is None
        )
    return True


def _round58_legacy_v15_stage_filter(signal: SelectionSignal) -> bool:
    """Replay the pre-Round58 minimum-only recovery rule for comparison."""
    scored = signal.metadata.get("scored")
    if signal.strategy_id == "niu_reversal_probe":
        if not isinstance(scored, Mapping):
            return False
        recovery_ratio = _number(scored.get("daily_v_recovery_ratio"))
        return bool(
            niu_reversal_entry_stage_blocker(scored) is None
            and recovery_ratio is not None
            and recovery_ratio + 1e-9 >= NIUONE_DAILY_V_MIN_RECOVERY_RATIO
        )
    if signal.strategy_id == "niu_leader":
        return bool(
            not isinstance(scored, Mapping)
            or niu_leader_entry_breadth_blocker(scored) is None
        )
    return True


def _lifecycle_early_recovery_filter(signal: SelectionSignal) -> bool:
    """Trade only early lifecycle probes with a bounded V recovery ratio."""
    if (
        signal.strategy_id != "niu_reversal_probe"
        or not _production_stage_filter(signal)
    ):
        return False
    scored = signal.metadata.get("scored")
    if not isinstance(scored, Mapping):
        return False
    state = str(scored.get("mainline_state") or "")
    recovery_ratio = _number(scored.get("daily_v_recovery_ratio"))
    return bool(
        state in {"candidate", "emerging"}
        and recovery_ratio is not None
        and recovery_ratio < 2.0
    )


def _lifecycle_stage_entry_contract_filter(signal: SelectionSignal) -> bool:
    """Recheck the production five-stage action contract on frozen tape."""
    if not _production_stage_filter(signal):
        return False
    scored = signal.metadata.get("scored")
    return bool(
        isinstance(scored, Mapping)
        and niuone_lifecycle_entry_blocker(signal.strategy_id, scored) is None
    )


def _lifecycle_stage_routed_early_recovery_filter(
    signal: SelectionSignal,
) -> bool:
    """Combine the frozen early-probe cap with stage-routed mature actions."""
    if signal.strategy_id == "niu_reversal_probe":
        return _lifecycle_early_recovery_filter(signal)
    return _lifecycle_stage_entry_contract_filter(signal)


def _lifecycle_early_quality_filter(
    *,
    minimum_today_breadth_pct: float | None = None,
    minimum_today_strength_score: float | None = None,
    minimum_signal_stop_distance_pct: float | None = None,
) -> Callable[[SelectionSignal], bool]:
    """Build one-factor historical quality guards on the Round29 early probe."""

    def accepted(signal: SelectionSignal) -> bool:
        if not _lifecycle_early_recovery_filter(signal):
            return False
        scored = signal.metadata.get("scored")
        if not isinstance(scored, Mapping):
            return False
        today_breadth = _number(scored.get("today_breadth_pct"))
        today_strength = _number(scored.get("today_strength_score"))
        stop_distance = _number(scored.get("stop_distance_pct"))
        if minimum_today_breadth_pct is not None and (
            today_breadth is None
            or today_breadth < minimum_today_breadth_pct
        ):
            return False
        if minimum_today_strength_score is not None and (
            today_strength is None
            or today_strength < minimum_today_strength_score
        ):
            return False
        if minimum_signal_stop_distance_pct is not None and (
            stop_distance is None
            or stop_distance < minimum_signal_stop_distance_pct
        ):
            return False
        return True

    return accepted


LIFECYCLE_EARLY_BREADTH_60_FILTER = _lifecycle_early_quality_filter(
    minimum_today_breadth_pct=REJECTED_ROUND32_MIN_TODAY_BREADTH_PCT,
)
LIFECYCLE_EARLY_TODAY_STRENGTH_40_FILTER = _lifecycle_early_quality_filter(
    minimum_today_strength_score=40.0,
)
LIFECYCLE_EARLY_SIGNAL_STOP_3_FILTER = _lifecycle_early_quality_filter(
    minimum_signal_stop_distance_pct=3.0,
)


def _lifecycle_early_theme_rank_filter(
    *,
    maximum_theme_rank: int,
) -> Callable[[SelectionSignal], bool]:
    """Restrict the early lifecycle probe to a historical relative theme slot."""

    def accepted(signal: SelectionSignal) -> bool:
        if not _lifecycle_early_recovery_filter(signal):
            return False
        scored = signal.metadata.get("scored")
        if not isinstance(scored, Mapping):
            return False
        rank = _number(scored.get("mainline_theme_rank"))
        return bool(rank is not None and rank <= maximum_theme_rank)

    return accepted


LIFECYCLE_EARLY_THEME_TOP2_FILTER = _lifecycle_early_theme_rank_filter(
    maximum_theme_rank=2,
)
LIFECYCLE_EARLY_THEME_TOP5_FILTER = _lifecycle_early_theme_rank_filter(
    maximum_theme_rank=5,
)


MARKUP_STRATEGY_IDS = frozenset(("niu_emerging", "niu_leader", "niu_pullback"))


def _production_markup_theme_momentum_filter(
    *,
    require_improvement: bool,
) -> Callable[[SelectionSignal], bool]:
    """Guard mainline-stage entries with full-cross-section rank momentum."""

    def accepted(signal: SelectionSignal) -> bool:
        if not _production_stage_filter(signal):
            return False
        if signal.strategy_id not in MARKUP_STRATEGY_IDS:
            return True
        scored = signal.metadata.get("scored")
        if not isinstance(scored, Mapping):
            return False
        if (
            scored.get("mainline_theme_rank_scope")
            != "full_historical_theme_cross_section"
        ):
            return False
        percentile_change = _number(
            scored.get("mainline_theme_percentile_change")
        )
        if percentile_change is None:
            return False
        return bool(
            percentile_change > 0.0
            if require_improvement else percentile_change >= 0.0
        )

    return accepted


PRODUCTION_MARKUP_THEME_IMPROVING_FILTER = (
    _production_markup_theme_momentum_filter(require_improvement=True)
)
PRODUCTION_MARKUP_THEME_NON_DECLINING_FILTER = (
    _production_markup_theme_momentum_filter(require_improvement=False)
)


def _production_markup_leadership_filter(
    *,
    minimum_leader_sector_rank: float = 0.0,
    minimum_leader_today_strength: float = 0.0,
    require_emerging_theme_for_startup: bool = False,
) -> Callable[[SelectionSignal], bool]:
    """Build causal quality guards for markup-stage entry actions."""

    def accepted(signal: SelectionSignal) -> bool:
        if not _production_stage_filter(signal):
            return False
        if signal.strategy_id not in {"niu_leader", "niu_emerging"}:
            return True
        scored = signal.metadata.get("scored")
        if not isinstance(scored, Mapping):
            return False
        if signal.strategy_id == "niu_leader":
            sector_rank = _number(scored.get("stock_sector_rank"))
            today_strength = _number(scored.get("today_strength_score"))
            return bool(
                sector_rank is not None
                and sector_rank + 1e-9 >= minimum_leader_sector_rank
                and today_strength is not None
                and today_strength + 1e-9 >= minimum_leader_today_strength
            )
        if not require_emerging_theme_for_startup:
            return True
        return str(scored.get("mainline_state") or "") == "emerging"

    return accepted


PRODUCTION_LEADER_SECTOR_RANK_80_FILTER = (
    _production_markup_leadership_filter(
        minimum_leader_sector_rank=80.0,
    )
)
PRODUCTION_LEADER_TODAY_STRENGTH_60_FILTER = (
    _production_markup_leadership_filter(
        minimum_leader_today_strength=60.0,
    )
)
PRODUCTION_LEADER_RANK_80_STRENGTH_60_FILTER = (
    _production_markup_leadership_filter(
        minimum_leader_sector_rank=80.0,
        minimum_leader_today_strength=60.0,
    )
)
PRODUCTION_MARKUP_QUALITY_FILTER = _production_markup_leadership_filter(
    minimum_leader_sector_rank=80.0,
    minimum_leader_today_strength=60.0,
    require_emerging_theme_for_startup=True,
)


def _production_reversal_quality_filter(
    *,
    minimum_mainline_score: float = 0.0,
    minimum_strong_count: int = 0,
    minimum_today_strength: float = 0.0,
    minimum_unconfirmed_today_strength: float | None = None,
    maximum_recovery_ratio: float | None = None,
) -> Callable[[SelectionSignal], bool]:
    """Build auditable entry-quality candidates on top of production rules."""

    def accepted(signal: SelectionSignal) -> bool:
        if not _production_stage_filter(signal):
            return False
        if signal.strategy_id != "niu_reversal_probe":
            return True
        scored = signal.metadata.get("scored")
        if not isinstance(scored, Mapping):
            return False
        mainline_score = _number(scored.get("mainline_score"))
        strong_count = _number(scored.get("strong_stock_count"))
        today_strength = _number(scored.get("today_strength_score"))
        recovery_ratio = _number(scored.get("daily_v_recovery_ratio"))
        if mainline_score is None or mainline_score < minimum_mainline_score:
            return False
        if strong_count is None or strong_count < minimum_strong_count:
            return False
        if today_strength is None or today_strength < minimum_today_strength:
            return False
        if (
            minimum_unconfirmed_today_strength is not None
            and scored.get("mainline_cross_day_persistent") is not True
            and (
                today_strength is None
                or today_strength < minimum_unconfirmed_today_strength
            )
        ):
            return False
        if (
            maximum_recovery_ratio is not None
            and (
                recovery_ratio is None
                or recovery_ratio >= maximum_recovery_ratio
            )
        ):
            return False
        return True

    return accepted


def _production_reversal_continuation_filter(
    *,
    minimum_strong_count: int = 0,
    minimum_state_streak: int = 0,
    combine_any: bool = False,
) -> Callable[[SelectionSignal], bool]:
    """Require entry-time theme breadth or duration for a brewing probe."""
    def accepted(signal: SelectionSignal) -> bool:
        if signal.strategy_id != "niu_reversal_probe":
            return _production_stage_filter(signal)
        scored = signal.metadata.get("scored")
        if not isinstance(scored, Mapping):
            return False
        if (
            niu_reversal_entry_stage_blocker(scored) is not None
            or niu_reversal_recovery_blocker(scored) is not None
        ):
            return False
        checks = []
        if minimum_strong_count > 0:
            strong_count = _number(scored.get("strong_stock_count"))
            checks.append(
                strong_count is not None
                and strong_count >= minimum_strong_count
            )
        if minimum_state_streak > 0:
            state_streak = _number(scored.get("mainline_state_streak"))
            checks.append(
                state_streak is not None
                and state_streak >= minimum_state_streak
            )
        return bool(
            checks
            and (any(checks) if combine_any else all(checks))
        )

    return accepted


def _with_theme_ranking_context(
    tape: SelectionReplayTape,
) -> SelectionReplayTape:
    """Attach causal relative industry rank and one-session rank momentum.

    New caches rank the provider's full historical theme cross section.
    Legacy caches fall back to tracked scorer rows so they remain readable,
    but expose that narrower scope explicitly for downstream safeguards.
    """
    frames: dict[str, SelectionReplayFrame] = {}
    previous_ranks: dict[str, dict[str, int]] = defaultdict(dict)
    previous_percentiles: dict[str, dict[str, float]] = defaultdict(dict)
    for date in sorted(tape.frames):
        frame = tape.frames[date]
        industry_scores: dict[str, float] = {}
        if frame.cross_section:
            rank_scope = "full_historical_theme_cross_section"
            for industry, values in frame.cross_section.items():
                if not isinstance(values, Mapping):
                    continue
                score = _number(values.get("score"))
                if not industry or score is None:
                    continue
                industry_scores[str(industry).strip()] = score
        else:
            rank_scope = "tracked_replay_themes"
            for by_strategy in frame.scored.values():
                for values in by_strategy.values():
                    if not isinstance(values, Mapping):
                        continue
                    industry = str(values.get("industry") or "").strip()
                    score = _number(values.get("mainline_score"))
                    if not industry or score is None:
                        continue
                    industry_scores[industry] = max(
                        score,
                        industry_scores.get(industry, -math.inf),
                    )
        ranked = sorted(industry_scores.items(), key=lambda item: (-item[1], item[0]))
        rank_by_industry = {
            industry: rank
            for rank, (industry, _score) in enumerate(ranked, start=1)
        }
        count = len(ranked)
        percentile_by_industry = {
            industry: (
                100.0
                if count == 1
                else round((count - rank) / (count - 1) * 100.0, 6)
            )
            for industry, rank in rank_by_industry.items()
        }
        prior_rank_by_industry = previous_ranks[rank_scope]
        prior_percentile_by_industry = previous_percentiles[rank_scope]
        top_score = ranked[0][1] if ranked else None

        def with_theme_rank(scored: Mapping[str, Any]) -> dict[str, Any]:
            """Return one immutable-source scorer row with same-close historical rank."""
            scored_payload = dict(scored)
            industry = str(scored_payload.get("industry") or "").strip()
            rank = rank_by_industry.get(industry)
            theme_score = industry_scores.get(industry)
            percentile = percentile_by_industry.get(industry)
            previous_rank = prior_rank_by_industry.get(industry)
            previous_percentile = prior_percentile_by_industry.get(industry)
            if (
                rank is None
                or top_score is None
                or theme_score is None
                or percentile is None
            ):
                return scored_payload
            scored_payload.update({
                "mainline_theme_rank": rank,
                "mainline_theme_previous_rank": previous_rank,
                "mainline_theme_rank_change": (
                    previous_rank - rank
                    if previous_rank is not None else None
                ),
                "mainline_theme_count": count,
                "mainline_theme_percentile": percentile,
                "mainline_theme_percentile_change": (
                    round(percentile - previous_percentile, 6)
                    if previous_percentile is not None else None
                ),
                "mainline_theme_score_gap_to_top": round(
                    top_score - theme_score,
                    6,
                ),
                "mainline_theme_top5": rank <= 5,
                "mainline_theme_new_top5": (
                    previous_rank > 5 and rank <= 5
                    if previous_rank is not None else None
                ),
                "mainline_theme_rank_scope": rank_scope,
            })
            return scored_payload

        scored_by_symbol = {
            symbol: {
                strategy_id: (
                    with_theme_rank(scored)
                    if isinstance(scored, Mapping) else scored
                )
                for strategy_id, scored in by_strategy.items()
            }
            for symbol, by_strategy in frame.scored.items()
        }
        signals: list[SelectionSignal] = []
        for signal in frame.signals:
            metadata = dict(signal.metadata)
            scored = metadata.get("scored")
            if not isinstance(scored, Mapping):
                signals.append(signal)
                continue
            scored_payload = with_theme_rank(scored)
            if "mainline_theme_rank" in scored_payload:
                metadata["scored"] = scored_payload
                signals.append(SelectionSignal(
                    symbol=signal.symbol,
                    strategy_id=signal.strategy_id,
                    reason=signal.reason,
                    score=signal.score,
                    metadata=metadata,
                ))
                continue
            signals.append(signal)
        previous_ranks[rank_scope] = dict(rank_by_industry)
        previous_percentiles[rank_scope] = dict(percentile_by_industry)
        frames[date] = SelectionReplayFrame(
            date=frame.date,
            signals=tuple(signals),
            scored=scored_by_symbol,
            cross_section=frame.cross_section,
        )
    return SelectionReplayTape(
        frames=MappingProxyType(frames),
        diagnostics=tape.diagnostics,
    )


def _with_reversal_ranking_context(
    tape: SelectionReplayTape,
) -> SelectionReplayTape:
    """Attach same-session reversal ranking facts without changing signal order.

    The frozen source emits up to five reversal candidates per close while the
    account replay accepts only one.  These fields make that secondary ranking
    decision auditable and remain causal because they use only signals
    already present in the same replay frame.
    """
    frames: dict[str, SelectionReplayFrame] = {}
    for date, frame in tape.frames.items():
        indexed = [
            (index, signal)
            for index, signal in enumerate(frame.signals)
            if signal.strategy_id == "niu_reversal_probe"
        ]
        ranked = sorted(
            indexed,
            key=lambda item: (
                -(item[1].score if item[1].score is not None else -math.inf),
                item[0],
            ),
        )
        rank_by_index = {
            original_index: rank
            for rank, (original_index, _signal) in enumerate(ranked, start=1)
        }
        top_score = ranked[0][1].score if ranked else None
        second_score = ranked[1][1].score if len(ranked) > 1 else None
        top_gap = (
            round(float(top_score) - float(second_score), 6)
            if top_score is not None and second_score is not None
            else None
        )
        signals: list[SelectionSignal] = []
        for index, signal in enumerate(frame.signals):
            if signal.strategy_id != "niu_reversal_probe":
                signals.append(signal)
                continue
            metadata = dict(signal.metadata)
            scored = metadata.get("scored")
            scored_payload = dict(scored) if isinstance(scored, Mapping) else {}
            scored_payload.update({
                "reversal_signal_score": signal.score,
                "reversal_candidate_count": len(ranked),
                "reversal_candidate_rank": rank_by_index[index],
                "reversal_top_score_gap": top_gap,
            })
            metadata["scored"] = scored_payload
            signals.append(SelectionSignal(
                symbol=signal.symbol,
                strategy_id=signal.strategy_id,
                reason=signal.reason,
                score=signal.score,
                metadata=metadata,
            ))
        frames[date] = SelectionReplayFrame(
            date=frame.date,
            signals=tuple(signals),
            scored=frame.scored,
            cross_section=frame.cross_section,
        )
    return SelectionReplayTape(frames=frames, diagnostics=tape.diagnostics)


def _with_research_ranking_context(
    tape: SelectionReplayTape,
) -> SelectionReplayTape:
    """Attach all historical relative ranking facts used by research."""
    return _with_reversal_ranking_context(_with_theme_ranking_context(tape))


def _production_reversal_ranking_filter(
    *,
    minimum_signal_score: float | None = None,
    minimum_candidate_count: int = 0,
    maximum_top_score_gap: float | None = None,
) -> Callable[[SelectionSignal], bool]:
    """Build entry candidates from same-day historical ranking context."""

    def accepted(signal: SelectionSignal) -> bool:
        if not _production_stage_filter(signal):
            return False
        if signal.strategy_id != "niu_reversal_probe":
            return True
        scored = signal.metadata.get("scored")
        if not isinstance(scored, Mapping):
            return False
        signal_score = _number(scored.get("reversal_signal_score"))
        candidate_count = _number(scored.get("reversal_candidate_count"))
        top_score_gap = _number(scored.get("reversal_top_score_gap"))
        if (
            minimum_signal_score is not None
            and (signal_score is None or signal_score < minimum_signal_score)
        ):
            return False
        if (
            minimum_candidate_count > 0
            and (
                candidate_count is None
                or candidate_count < minimum_candidate_count
            )
        ):
            return False
        if (
            maximum_top_score_gap is not None
            and (
                top_score_gap is None
                or top_score_gap > maximum_top_score_gap
            )
        ):
            return False
        return True

    return accepted


def _production_reversal_shape_filter(
    *,
    minimum_decline_pct: float = 0.0,
    maximum_recovery_ratio: float | None = None,
    allowed_mainline_states: frozenset[str] | None = None,
) -> Callable[[SelectionSignal], bool]:
    """Build interpretable daily-V shape candidates on production routing.

    These values are all known at the signal close.  Keeping this research
    filter separate from the production scorer makes rejected shape hypotheses
    reproducible without changing live candidate generation.
    """

    def accepted(signal: SelectionSignal) -> bool:
        if not _production_stage_filter(signal):
            return False
        if signal.strategy_id != "niu_reversal_probe":
            return True
        scored = signal.metadata.get("scored")
        if not isinstance(scored, Mapping):
            return False
        decline_pct = _number(scored.get("daily_v_decline_pct"))
        recovery_ratio = _number(scored.get("daily_v_recovery_ratio"))
        mainline_state = str(scored.get("mainline_state") or "")
        if decline_pct is None or decline_pct < minimum_decline_pct:
            return False
        if (
            maximum_recovery_ratio is not None
            and (
                recovery_ratio is None
                or recovery_ratio >= maximum_recovery_ratio
            )
        ):
            return False
        if (
            allowed_mainline_states is not None
            and mainline_state not in allowed_mainline_states
        ):
            return False
        return True

    return accepted


def _stage_entry_filter(
    *,
    leader_score: float = 8.0,
    pullback_score: float = 8.2,
    emerging_score: float = 8.4,
    reversal_score: float = 7.6,
    minimum_emerging_breakout_atr: float = 0.0,
) -> Callable[[SelectionSignal], bool]:
    """Reapply production or research thresholds to a wide stage tape.

    A wide tape must have been generated with thresholds no higher than the
    requested values.  Positive breakout confirmation intentionally accepts
    only ``entry_setup=breakout``; the production baseline keeps both breakout
    and reclaim setups by leaving the minimum at zero.
    """
    thresholds = {
        "niu_leader": float(leader_score),
        "niu_pullback": float(pullback_score),
        "niu_emerging": float(emerging_score),
        "niu_reversal_probe": float(reversal_score),
    }
    resolved_breakout = float(minimum_emerging_breakout_atr)

    def accepted(signal: SelectionSignal) -> bool:
        threshold = thresholds.get(signal.strategy_id)
        score = _number(signal.score)
        if threshold is None or score is None or score < threshold:
            return False
        if not _production_stage_filter(signal):
            return False
        if signal.strategy_id != "niu_emerging" or resolved_breakout <= 0:
            return True
        scored = signal.metadata.get("scored")
        if not isinstance(scored, Mapping):
            return False
        extension = _number(scored.get("entry_extension_atr"))
        return bool(
            str(scored.get("entry_setup") or "") == "breakout"
            and extension is not None
            and extension >= resolved_breakout
        )

    return accepted


def _stage_entry_with_reversal_recovery_cap(
    *,
    emerging_score: float,
    minimum_emerging_breakout_atr: float,
    maximum_reversal_recovery_ratio: float,
) -> Callable[[SelectionSignal], bool]:
    """Combine two pre-registered stage/reversal hypotheses for shadow study."""
    stage_filter = _stage_entry_filter(
        emerging_score=emerging_score,
        minimum_emerging_breakout_atr=minimum_emerging_breakout_atr,
    )
    reversal_filter = _production_reversal_quality_filter(
        maximum_recovery_ratio=maximum_reversal_recovery_ratio,
    )

    def accepted(signal: SelectionSignal) -> bool:
        return stage_filter(signal) and reversal_filter(signal)

    return accepted


def _validate_stage_entry_cache(metadata: Mapping[str, Any]) -> None:
    """Reject a replay tape that cannot recover every Round16 candidate."""
    raw_thresholds = metadata.get("round16_thresholds")
    if not isinstance(raw_thresholds, Mapping):
        raise ValueError(
            "stage entry analysis requires a cache with round16_thresholds"
        )
    for strategy_id, maximum_source_threshold in (
        ROUND16_STAGE_SOURCE_THRESHOLDS.items()
    ):
        actual = _number(raw_thresholds.get(strategy_id))
        if actual is None or actual > maximum_source_threshold:
            raise ValueError(
                "stage entry cache threshold for "
                f"{strategy_id} must be <= {maximum_source_threshold:g}"
            )


def _pullback_geometry_filter(
    variant_id: str,
    *,
    pullback_score: float = 8.2,
) -> Callable[[SelectionSignal], bool]:
    """Replay one Round17 shape while preserving production stage thresholds."""
    if variant_id not in ROUND17_PULLBACK_VARIANT_IDS:
        raise ValueError(f"unknown pullback research variant: {variant_id}")
    other_stages = _stage_entry_filter(pullback_score=pullback_score)

    def accepted(signal: SelectionSignal) -> bool:
        if signal.strategy_id != "niu_pullback":
            return other_stages(signal)
        score = _number(signal.score)
        if score is None or score < pullback_score:
            return False
        scored = signal.metadata.get("scored")
        if not isinstance(scored, Mapping):
            return False
        variants = scored.get("pullback_research_variants")
        variant = (
            variants.get(variant_id)
            if isinstance(variants, Mapping) else None
        )
        if not isinstance(variant, Mapping) or variant.get("matched") is not True:
            return False
        extension = _number(variant.get("entry_extension_atr"))
        maximum = _number(scored.get("max_entry_extension_atr"))
        return bool(
            extension is not None
            and maximum is not None
            and extension <= maximum + 1e-9
        )

    return accepted


def _validate_pullback_geometry_cache(metadata: Mapping[str, Any]) -> None:
    """Reject a cache that cannot reproduce the complete Round17 matrix."""
    variants = tuple(metadata.get("round17_pullback_variants") or ())
    threshold = _number(metadata.get("round17_pullback_source_threshold"))
    if set(variants) != set(ROUND17_PULLBACK_VARIANT_IDS):
        raise ValueError(
            "pullback geometry analysis requires the complete "
            "round17_pullback_variants set"
        )
    if threshold is None or threshold > ROUND17_PULLBACK_SOURCE_THRESHOLD:
        raise ValueError(
            "pullback geometry source threshold must be <= "
            f"{ROUND17_PULLBACK_SOURCE_THRESHOLD:g}"
        )


def _pullback_recovery_filter(
    *,
    pullback_score: float = ROUND18_PULLBACK_RECOVERY_SOURCE_THRESHOLD,
    minimum_today_breadth: float = 50.0,
    intraday_rescue_strength: float | None = None,
    require_ema10_confirmation: bool = False,
    maximum_ema10_extension_atr: float = 1.75,
) -> Callable[[SelectionSignal], bool]:
    """Replay a theme-recovery pullback without changing the live scorer.

    A NiuOne pullback is treated as a previously confirmed mainline that is
    still classified as ``diverging`` but has begun to recover.  Recovery is
    confirmed by a positive close-to-close mainline score change, or by an
    explicitly requested strong same-day participation reading.  Positive
    stock price action and broad theme participation keep the rule on the
    right side of the turn.  The optional EMA10 branch tests a faster anchor
    with its own extension cap instead of reusing the production EMA20 cap.
    """
    resolved_score = float(pullback_score)
    resolved_breadth = float(minimum_today_breadth)
    resolved_rescue = (
        float(intraday_rescue_strength)
        if intraday_rescue_strength is not None
        else None
    )
    resolved_ema10_cap = float(maximum_ema10_extension_atr)
    other_stages = _stage_entry_filter()

    def accepted(signal: SelectionSignal) -> bool:
        if signal.strategy_id != "niu_pullback":
            return other_stages(signal)
        score = _number(signal.score)
        scored = signal.metadata.get("scored")
        if score is None or score < resolved_score or not isinstance(scored, Mapping):
            return False
        mainline_score = _number(scored.get("mainline_score"))
        mainline_change = _number(scored.get("mainline_score_change"))
        today_strength = _number(scored.get("today_strength_score"))
        today_breadth = _number(scored.get("today_breadth_pct"))
        stock_change = _number(scored.get("change_pct"))
        if (
            str(scored.get("mainline_state") or "") != "diverging"
            or scored.get("mainline_confirmed") is not True
            or mainline_score is None
            or mainline_score < 70.0
            or today_breadth is None
            or today_breadth < resolved_breadth
            or stock_change is None
            or stock_change <= 0
            or scored.get("hard_blockers")
        ):
            return False
        score_recovered = mainline_change is not None and mainline_change > 0
        intraday_recovered = bool(
            resolved_rescue is not None
            and today_strength is not None
            and today_strength >= resolved_rescue
        )
        if not (score_recovered or intraday_recovered):
            return False
        if not require_ema10_confirmation:
            return True
        variants = scored.get("pullback_research_variants")
        ema10 = (
            variants.get("ema10_confirm_atr050")
            if isinstance(variants, Mapping)
            else None
        )
        extension = (
            _number(ema10.get("entry_extension_atr"))
            if isinstance(ema10, Mapping)
            else None
        )
        return bool(
            isinstance(ema10, Mapping)
            and ema10.get("matched") is True
            and extension is not None
            and extension <= resolved_ema10_cap + 1e-9
        )

    return accepted


def _development_completed_features(
    windows: Mapping[str, Mapping[str, Any]],
    *,
    window_names: Iterable[str] = PRIMARY_DEVELOPMENT_WINDOW_NAMES,
) -> list[dict[str, Any]]:
    """Return completed features from mutually exclusive development windows."""
    return [
        dict(row)
        for name in window_names
        for row in (
            windows.get(name, {}).get("completed_trade_features") or ()
        )
        if isinstance(row, Mapping)
    ]


def _development_aggregate(
    windows: Mapping[str, Mapping[str, Any]],
    *,
    window_names: Iterable[str] = PRIMARY_DEVELOPMENT_WINDOW_NAMES,
) -> dict[str, Any] | None:
    """Summarize mutually exclusive windows without averaging percentages."""
    selected_names = [name for name in window_names if name in windows]
    if not selected_names:
        return None
    completed_trade_count = 0
    win_count = 0
    compounded_return = 1.0
    positive_window_count = 0
    worst_drawdown: float | None = None
    for name in selected_names:
        summary = windows[name]
        statistics_payload = summary.get("statistics")
        statistics_values = (
            statistics_payload if isinstance(statistics_payload, Mapping) else {}
        )
        completed_trade_count += int(
            _number(statistics_values.get("completed_trade_count")) or 0
        )
        features_payload = summary.get("completed_trade_features")
        features = (
            features_payload
            if isinstance(features_payload, (tuple, list)) else ()
        )
        feature_win_count = sum(
            1
            for row in features
            if isinstance(row, Mapping)
            and (_number(row.get("net_return_pct")) or 0.0) > 0
        )
        if len(features) == int(
            _number(statistics_values.get("completed_trade_count")) or 0
        ):
            win_count += feature_win_count
        else:
            statistics_win_count = _number(statistics_values.get("win_count"))
            if statistics_win_count is not None:
                win_count += round(statistics_win_count)
            else:
                window_win_rate = _number(statistics_values.get("win_rate_pct"))
                window_completed = int(
                    _number(statistics_values.get("completed_trade_count")) or 0
                )
                if window_win_rate is not None:
                    win_count += round(window_completed * window_win_rate / 100.0)
        portfolio_return = _number(statistics_values.get("portfolio_return_pct"))
        if portfolio_return is not None:
            compounded_return *= 1.0 + portfolio_return / 100.0
            positive_window_count += int(portfolio_return > 0)
        drawdown = _number(statistics_values.get("max_drawdown_pct"))
        if drawdown is not None:
            worst_drawdown = (
                drawdown
                if worst_drawdown is None else min(worst_drawdown, drawdown)
            )
    return {
        "window_names": selected_names,
        "completed_trade_count": completed_trade_count,
        "win_count": win_count,
        "win_rate_pct": (
            round(win_count / completed_trade_count * 100.0, 4)
            if completed_trade_count else None
        ),
        "compounded_portfolio_return_pct": round(
            (compounded_return - 1.0) * 100.0,
            4,
        ),
        "positive_window_count": positive_window_count,
        "evaluated_window_count": len(selected_names),
        "worst_max_drawdown_pct": (
            round(worst_drawdown, 4) if worst_drawdown is not None else None
        ),
    }


def _stage_development_aggregate(
    windows: Mapping[str, Mapping[str, Any]],
    *,
    window_names: Iterable[str] = PRIMARY_DEVELOPMENT_WINDOW_NAMES,
) -> dict[str, dict[str, Any]]:
    """Aggregate mutually exclusive window statistics by entry stage."""
    stage_ids = (
        "niu_reversal_probe",
        "niu_emerging",
        "niu_leader",
        "niu_pullback",
    )
    totals = {
        stage_id: {"completed_trade_count": 0, "win_count": 0, "net_sum": 0.0}
        for stage_id in stage_ids
    }
    for name in window_names:
        summary = windows.get(name)
        if not isinstance(summary, Mapping):
            continue
        statistics_payload = summary.get("statistics")
        statistics_values = (
            statistics_payload if isinstance(statistics_payload, Mapping) else {}
        )
        by_strategy_payload = statistics_values.get("by_strategy")
        by_strategy = (
            by_strategy_payload
            if isinstance(by_strategy_payload, Mapping) else {}
        )
        for stage_id in stage_ids:
            stage_payload = by_strategy.get(stage_id)
            if not isinstance(stage_payload, Mapping):
                continue
            completed = int(
                _number(stage_payload.get("completed_trade_count")) or 0
            )
            win_rate = _number(stage_payload.get("win_rate_pct"))
            average_return = _number(stage_payload.get("average_net_return_pct"))
            totals[stage_id]["completed_trade_count"] += completed
            if win_rate is not None:
                totals[stage_id]["win_count"] += round(
                    completed * win_rate / 100.0
                )
            if average_return is not None:
                totals[stage_id]["net_sum"] += completed * average_return
    result: dict[str, dict[str, Any]] = {}
    for stage_id, total in totals.items():
        completed = int(total["completed_trade_count"])
        wins = int(total["win_count"])
        result[stage_id] = {
            "completed_trade_count": completed,
            "win_count": wins,
            "win_rate_pct": (
                round(wins / completed * 100.0, 4) if completed else None
            ),
            "average_net_return_pct": (
                round(float(total["net_sum"]) / completed, 4)
                if completed else None
            ),
        }
    return result


def _window_metric_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    statistics_payload = summary.get("statistics")
    statistics_values = (
        statistics_payload if isinstance(statistics_payload, Mapping) else {}
    )
    return {
        "completed_trade_count": int(
            _number(statistics_values.get("completed_trade_count")) or 0
        ),
        "win_rate_pct": _number(statistics_values.get("win_rate_pct")) or 0.0,
        "portfolio_return_pct": (
            _number(statistics_values.get("portfolio_return_pct")) or 0.0
        ),
        "max_drawdown_pct": (
            _number(statistics_values.get("max_drawdown_pct")) or 0.0
        ),
    }


def _window_metric_delta(
    target: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "completed_trade_count": int(target["completed_trade_count"])
        - int(baseline["completed_trade_count"]),
        "win_rate_points": round(
            float(target["win_rate_pct"]) - float(baseline["win_rate_pct"]),
            4,
        ),
        "portfolio_return_points": round(
            float(target["portfolio_return_pct"])
            - float(baseline["portfolio_return_pct"]),
            4,
        ),
        "max_drawdown_points": round(
            float(target["max_drawdown_pct"])
            - float(baseline["max_drawdown_pct"]),
            4,
        ),
    }


def _mainline_stage_filter(
    *,
    allow_reversal: bool = True,
    reversal_states: frozenset[str] | None = None,
    minimum_theme_score: float = 0.0,
    minimum_score_change: float | None = None,
    minimum_strong_count: int = 0,
    minimum_effective_count: float = 0.0,
    require_reversal_leader: bool = False,
    require_stock_strong: bool = False,
    allowed_market_regimes: frozenset[str] | None = None,
) -> Callable[[SelectionSignal], bool]:
    """Build an interpretable NiuOne-stage filter for recorded signals."""

    def accepted(signal: SelectionSignal) -> bool:
        if signal.strategy_id != "niu_reversal_probe":
            return True
        if not allow_reversal:
            return False
        scored = signal.metadata.get("scored")
        if not isinstance(scored, Mapping):
            return False
        state = str(scored.get("mainline_state") or "")
        regime = str(scored.get("market_regime") or "")
        theme_score = _number(scored.get("mainline_score")) or 0.0
        score_change = _number(scored.get("mainline_score_change"))
        strong_count = int(_number(scored.get("strong_stock_count")) or 0)
        effective_count = _number(scored.get("effective_strong_count")) or 0.0
        if reversal_states is not None and state not in reversal_states:
            return False
        if allowed_market_regimes is not None and regime not in allowed_market_regimes:
            return False
        if theme_score < minimum_theme_score:
            return False
        if minimum_score_change is not None and (
            score_change is None or score_change < minimum_score_change
        ):
            return False
        if strong_count < minimum_strong_count:
            return False
        if effective_count < minimum_effective_count:
            return False
        if scored.get("single_stock_dominated") is True:
            return False
        if require_reversal_leader and scored.get("stock_reversal_leader_tier") is not True:
            return False
        if require_stock_strong and scored.get("stock_strong") is not True:
            return False
        return True

    return accepted


ROTATION_GROWING_THEME_FILTER = _mainline_stage_filter(
    reversal_states=frozenset({"candidate", "emerging"}),
    minimum_theme_score=60.0,
    minimum_score_change=0.0,
    minimum_strong_count=2,
    minimum_effective_count=1.7,
    allowed_market_regimes=frozenset({"rotation", "recovery"}),
)
PRODUCTION_REVERSAL_MAINLINE_SCORE_60_FILTER = (
    _production_reversal_quality_filter(minimum_mainline_score=60.0)
)
PRODUCTION_REVERSAL_STRONG_COUNT_3_FILTER = (
    _production_reversal_quality_filter(minimum_strong_count=3)
)
PRODUCTION_REVERSAL_CONTINUATION_FILTERS = {
    threshold: _production_reversal_continuation_filter(
        minimum_strong_count=threshold,
    )
    for threshold in (4, 5, 6)
}
PRODUCTION_REVERSAL_CONTINUATION_OR_FILTERS = {
    streak: _production_reversal_continuation_filter(
        minimum_strong_count=5,
        minimum_state_streak=streak,
        combine_any=True,
    )
    for streak in (2, 3, 4)
}
PRODUCTION_REVERSAL_TODAY_STRENGTH_30_FILTER = (
    _production_reversal_quality_filter(minimum_today_strength=30.0)
)
PRODUCTION_REVERSAL_RECOVERY_CAP_2_FILTER = (
    _production_reversal_quality_filter(maximum_recovery_ratio=2.0)
)
PRODUCTION_REVERSAL_UNCONFIRMED_STRENGTH_FILTERS = {
    threshold: _production_reversal_quality_filter(
        minimum_unconfirmed_today_strength=threshold,
    )
    for threshold in (20.0, 25.0, 30.0, 35.0)
}
PRODUCTION_REVERSAL_SCORE_FILTERS = {
    threshold: _production_reversal_ranking_filter(
        minimum_signal_score=threshold,
    )
    for threshold in (8.8, 8.9, 9.0, 9.1)
}
PRODUCTION_REVERSAL_CANDIDATE_COUNT_FILTERS = {
    threshold: _production_reversal_ranking_filter(
        minimum_candidate_count=threshold,
    )
    for threshold in (2, 4)
}
PRODUCTION_REVERSAL_TOP_GAP_FILTERS = {
    threshold: _production_reversal_ranking_filter(
        maximum_top_score_gap=threshold,
    )
    for threshold in (0.2, 0.5)
}
PRODUCTION_REVERSAL_DECLINE_FILTERS = {
    threshold: _production_reversal_shape_filter(
        minimum_decline_pct=threshold,
    )
    for threshold in (10.0, 12.0, 14.0)
}
PRODUCTION_REVERSAL_RECOVERY_CAP_FILTERS = {
    threshold: _production_reversal_shape_filter(
        maximum_recovery_ratio=threshold,
    )
    for threshold in (1.0, 1.1, 1.2)
}
PRODUCTION_REVERSAL_DECLINE_12_RECOVERY_12_FILTER = (
    _production_reversal_shape_filter(
        minimum_decline_pct=12.0,
        maximum_recovery_ratio=1.2,
    )
)
PRODUCTION_REVERSAL_CANDIDATE_STATE_FILTER = (
    _production_reversal_shape_filter(
        allowed_mainline_states=frozenset({"candidate"}),
    )
)

ROUND16_STAGE_SOURCE_THRESHOLDS = MappingProxyType({
    "niu_leader": 7.0,
    "niu_pullback": 7.0,
    "niu_emerging": 7.0,
    "niu_reversal_probe": 7.6,
})


def _stage_markup_theme_momentum_filter(
    *,
    require_improvement: bool,
) -> Callable[[SelectionSignal], bool]:
    """Apply the pre-registered rank-momentum guard to production thresholds."""
    production_thresholds = _stage_entry_filter()
    momentum_guard = _production_markup_theme_momentum_filter(
        require_improvement=require_improvement,
    )

    def accepted(signal: SelectionSignal) -> bool:
        return production_thresholds(signal) and momentum_guard(signal)

    return accepted


STAGE_MARKUP_THEME_IMPROVING_FILTER = _stage_markup_theme_momentum_filter(
    require_improvement=True,
)
STAGE_MARKUP_THEME_NON_DECLINING_FILTER = _stage_markup_theme_momentum_filter(
    require_improvement=False,
)


def _stage_emerging_lifecycle_transition_filter(
    *,
    transition: str,
) -> Callable[[SelectionSignal], bool]:
    """Relax only emerging entries when a full historical theme transition confirms.

    Production-threshold signals remain unchanged.  Lower-score emerging
    signals must still pass the wide-tape production gates and use a breakout
    entry; the cross-sectional transition is then evaluated fail-closed.
    """
    if transition not in {"new_top5", "top5_improving"}:
        raise ValueError(f"unsupported emerging lifecycle transition: {transition}")
    production_thresholds = _stage_entry_filter()
    wide_thresholds = _stage_entry_filter(emerging_score=7.0)

    def accepted(signal: SelectionSignal) -> bool:
        if production_thresholds(signal):
            return True
        if signal.strategy_id != "niu_emerging" or not wide_thresholds(signal):
            return False
        scored = signal.metadata.get("scored")
        if not isinstance(scored, Mapping):
            return False
        if (
            scored.get("mainline_theme_rank_scope")
            != "full_historical_theme_cross_section"
            or str(scored.get("entry_setup") or "") != "breakout"
        ):
            return False
        if transition == "new_top5":
            return scored.get("mainline_theme_new_top5") is True
        rank = _number(scored.get("mainline_theme_rank"))
        rank_change = _number(scored.get("mainline_theme_rank_change"))
        return bool(
            rank is not None
            and rank <= 5
            and rank_change is not None
            and rank_change > 0
        )

    return accepted


STAGE_EMERGING_NEW_TOP5_BREAKOUT_FILTER = (
    _stage_emerging_lifecycle_transition_filter(transition="new_top5")
)
STAGE_EMERGING_TOP5_IMPROVING_BREAKOUT_FILTER = (
    _stage_emerging_lifecycle_transition_filter(transition="top5_improving")
)

ROUND16_STAGE_ENTRY_CANDIDATES: Mapping[str, Mapping[str, Any]] = {
    "stage_production_thresholds": {
        "signal_filter": _stage_entry_filter(),
        "filter_options": {
            "leader_score": 8.0,
            "pullback_score": 8.2,
            "emerging_score": 8.4,
            "reversal_score": 7.6,
        },
    },
    "stage_production_thresholds_markup_theme_improving": {
        "signal_filter": STAGE_MARKUP_THEME_IMPROVING_FILTER,
        "filter_options": {
            "production_stage_thresholds": True,
            "guarded_entry_strategy_ids": tuple(sorted(MARKUP_STRATEGY_IDS)),
            "required_theme_rank_scope": (
                "full_historical_theme_cross_section"
            ),
            "minimum_theme_percentile_change_exclusive": 0.0,
        },
        "research_status": "rejected_round34",
    },
    "stage_production_thresholds_markup_theme_non_declining": {
        "signal_filter": STAGE_MARKUP_THEME_NON_DECLINING_FILTER,
        "filter_options": {
            "production_stage_thresholds": True,
            "guarded_entry_strategy_ids": tuple(sorted(MARKUP_STRATEGY_IDS)),
            "required_theme_rank_scope": (
                "full_historical_theme_cross_section"
            ),
            "minimum_theme_percentile_change_inclusive": 0.0,
        },
        "research_status": "rejected_round34",
    },
    "stage_emerging_score_70_full_theme_new_top5_breakout": {
        "signal_filter": STAGE_EMERGING_NEW_TOP5_BREAKOUT_FILTER,
        "filter_options": {
            "production_stage_thresholds": True,
            "emerging_score": 7.0,
            "required_theme_rank_scope": (
                "full_historical_theme_cross_section"
            ),
            "required_theme_transition": "new_top5",
            "required_entry_setup": "breakout",
        },
        "research_status": "rejected_round35",
    },
    "stage_emerging_score_70_full_theme_top5_improving_breakout": {
        "signal_filter": STAGE_EMERGING_TOP5_IMPROVING_BREAKOUT_FILTER,
        "filter_options": {
            "production_stage_thresholds": True,
            "emerging_score": 7.0,
            "required_theme_rank_scope": (
                "full_historical_theme_cross_section"
            ),
            "maximum_theme_rank": 5,
            "minimum_theme_rank_change_exclusive": 0.0,
            "required_entry_setup": "breakout",
        },
        "research_status": "rejected_round35",
    },
    "stage_leader_score_70": {
        "signal_filter": _stage_entry_filter(leader_score=7.0),
        "filter_options": {"leader_score": 7.0},
        "research_status": "rejected_round16",
    },
    **{
        f"stage_emerging_score_{str(threshold).replace('.', '')}": {
            "signal_filter": _stage_entry_filter(emerging_score=threshold),
            "filter_options": {"emerging_score": threshold},
            "research_status": "rejected_round16",
        }
        for threshold in (8.3, 8.2, 8.1, 8.0)
    },
    **{
        f"stage_emerging_score_70_breakout_{int(threshold * 100):03d}": {
            "signal_filter": _stage_entry_filter(
                emerging_score=7.0,
                minimum_emerging_breakout_atr=threshold,
            ),
            "filter_options": {
                "emerging_score": 7.0,
                "minimum_emerging_breakout_atr": threshold,
            },
            "research_status": "rejected_round16",
        }
        for threshold in (0.25, 0.5, 0.75, 1.0)
    },
    "stage_emerging_score_80_breakout_050": {
        "signal_filter": _stage_entry_filter(
            emerging_score=8.0,
            minimum_emerging_breakout_atr=0.5,
        ),
        "filter_options": {
            "emerging_score": 8.0,
            "minimum_emerging_breakout_atr": 0.5,
        },
        "research_status": "rejected_round16",
    },
    "stage_emerging_score_80_breakout_050_reversal_recovery_cap_200": {
        "signal_filter": _stage_entry_with_reversal_recovery_cap(
            emerging_score=8.0,
            minimum_emerging_breakout_atr=0.5,
            maximum_reversal_recovery_ratio=2.0,
        ),
        "filter_options": {
            "emerging_score": 8.0,
            "minimum_emerging_breakout_atr": 0.5,
            "maximum_reversal_recovery_ratio": 2.0,
        },
        "research_status": "rejected_round22",
    },
}

ROUND17_PULLBACK_CANDIDATES: Mapping[str, Mapping[str, Any]] = {
    "pullback_production_geometry": {
        "signal_filter": _pullback_geometry_filter("production_ema20"),
        "filter_options": {
            "variant_id": "production_ema20",
            "pullback_score": 8.2,
        },
    },
    **{
        f"pullback_{variant_id}_score_82": {
            "signal_filter": _pullback_geometry_filter(variant_id),
            "filter_options": {
                "variant_id": variant_id,
                "pullback_score": 8.2,
            },
            "research_status": "rejected_round17",
        }
        for variant_id in ROUND17_PULLBACK_VARIANT_IDS
        if variant_id != "production_ema20"
    },
    **{
        f"pullback_{variant_id}_score_80": {
            "signal_filter": _pullback_geometry_filter(
                variant_id,
                pullback_score=8.0,
            ),
            "filter_options": {
                "variant_id": variant_id,
                "pullback_score": 8.0,
            },
            "research_status": "rejected_round17",
        }
        for variant_id in (
            "ema20_prior_confirm_atr050",
            "ema10_confirm_atr025",
            "shallow_structure_confirm",
        )
    },
    **{
        f"pullback_{variant_id}_score_{int(score * 10):02d}": {
            "signal_filter": _pullback_geometry_filter(
                variant_id,
                pullback_score=score,
            ),
            "filter_options": {
                "variant_id": variant_id,
                "pullback_score": score,
            },
            "research_status": "rejected_round17",
        }
        for variant_id, score in (
            ("control_other_gates_only", 7.8),
            ("control_other_gates_only", 7.9),
            ("control_ema20_chase_only", 7.9),
            ("ema20_prior_confirm_atr050", 7.9),
            ("ema10_confirm_atr025", 7.9),
            ("ema10_confirm_atr050", 7.9),
        )
    },
}

ROUND18_PULLBACK_RECOVERY_CANDIDATES: Mapping[str, Mapping[str, Any]] = {
    "pullback_production_geometry": {
        "signal_filter": _pullback_geometry_filter("production_ema20"),
        "filter_options": {
            "variant_id": "production_ema20",
            "pullback_score": 8.2,
        },
    },
    "pullback_directional_recovery": {
        "signal_filter": _pullback_recovery_filter(),
        "filter_options": {
            "pullback_score": 7.0,
            "minimum_today_breadth": 50.0,
            "intraday_rescue_strength": None,
        },
        "research_status": "rejected_round18",
    },
    "pullback_recovery_or_intraday_40": {
        "signal_filter": _pullback_recovery_filter(
            intraday_rescue_strength=40.0,
        ),
        "filter_options": {
            "pullback_score": 7.0,
            "minimum_today_breadth": 50.0,
            "intraday_rescue_strength": 40.0,
        },
        "research_status": "rejected_round18",
    },
    "pullback_recovery_or_intraday_40_score_78": {
        "signal_filter": _pullback_recovery_filter(
            pullback_score=7.8,
            intraday_rescue_strength=40.0,
        ),
        "filter_options": {
            "pullback_score": 7.8,
            "minimum_today_breadth": 50.0,
            "intraday_rescue_strength": 40.0,
        },
        "research_status": "rejected_round18",
    },
    **{
        f"pullback_recovery_ema10_atr_{int(cap * 100):03d}": {
            "signal_filter": _pullback_recovery_filter(
                intraday_rescue_strength=40.0,
                require_ema10_confirmation=True,
                maximum_ema10_extension_atr=cap,
            ),
            "filter_options": {
                "pullback_score": 7.0,
                "minimum_today_breadth": 50.0,
                "intraday_rescue_strength": 40.0,
                "require_ema10_confirmation": True,
                "maximum_ema10_extension_atr": cap,
            },
            "research_status": "rejected_round18",
        }
        for cap in (1.5, 1.75)
    },
}

CANDIDATES: Mapping[str, Mapping[str, Any]] = {
    "round58_legacy_v15_baseline": {
        "signal_filter": _round58_legacy_v15_stage_filter,
        "filter_options": {
            "minimum_daily_v_recovery_ratio_inclusive": 0.60,
            "maximum_daily_v_recovery_ratio_exclusive": None,
        },
        "research_status": "historical_round58",
        "policy_options": None,
    },
    "frozen_production_default": {
        "signal_filter": _production_stage_filter,
        "policy_options": None,
    },
    "production_leader_sector_rank_80": {
        "signal_filter": PRODUCTION_LEADER_SECTOR_RANK_80_FILTER,
        "filter_options": {
            "minimum_leader_sector_rank": 80.0,
        },
        "research_status": "component_round60",
        "policy_options": None,
    },
    "production_leader_today_strength_60": {
        "signal_filter": PRODUCTION_LEADER_TODAY_STRENGTH_60_FILTER,
        "filter_options": {
            "minimum_leader_today_strength": 60.0,
        },
        "research_status": "component_round60",
        "policy_options": None,
    },
    "production_leader_rank_80_strength_60": {
        "signal_filter": PRODUCTION_LEADER_RANK_80_STRENGTH_60_FILTER,
        "filter_options": {
            "minimum_leader_sector_rank": 80.0,
            "minimum_leader_today_strength": 60.0,
        },
        "research_status": "component_round60",
        "policy_options": None,
    },
    "production_markup_quality": {
        "signal_filter": PRODUCTION_MARKUP_QUALITY_FILTER,
        "filter_options": {
            "minimum_leader_sector_rank": 80.0,
            "minimum_leader_today_strength": 60.0,
            "require_emerging_theme_for_startup": True,
        },
        "research_status": "promoted_round60",
        "policy_options": None,
    },
    "production_reversal_min_mainline_score_60": {
        "signal_filter": PRODUCTION_REVERSAL_MAINLINE_SCORE_60_FILTER,
        "filter_options": {"minimum_mainline_score": 60.0},
        "research_status": "rejected_round9",
        "policy_options": None,
    },
    "production_reversal_min_strong_count_3": {
        "signal_filter": PRODUCTION_REVERSAL_STRONG_COUNT_3_FILTER,
        "filter_options": {"minimum_strong_count": 3},
        "research_status": "rejected_round9",
        "policy_options": None,
    },
    **{
        f"round61_brewing_strong_count_{threshold}": {
            "signal_filter": filter_function,
            "filter_options": {"minimum_strong_count": threshold},
            "research_status": "round61_continuation_quality",
            "policy_options": None,
        }
        for threshold, filter_function in (
            PRODUCTION_REVERSAL_CONTINUATION_FILTERS.items()
        )
    },
    **{
        f"round61_brewing_strong5_cap_{str(cap).replace('.', '_')}": {
            "signal_filter": PRODUCTION_REVERSAL_CONTINUATION_FILTERS[5],
            "filter_options": {
                "minimum_strong_count": 5,
                "reversal_entry_position_cap_pct": cap,
            },
            "research_status": "round61_cap_sensitivity",
            "policy_options": {
                "reversal_entry_position_cap_pct": cap,
                "reversal_early_profit_regimes": tuple(sorted(
                    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
                )),
            },
        }
        for cap in (6.25, 7.5, 8.75)
    },
    **{
        f"round61_brewing_strong{threshold}_cap_7_5_two_signals": {
            "signal_filter": filter_function,
            "filter_options": {
                "minimum_strong_count": threshold,
                "reversal_entry_position_cap_pct": 7.5,
                "reversal_signals_per_session": 2,
            },
            "research_status": "round61_joint_quality_utilization",
            "policy_options": {
                "reversal_entry_position_cap_pct": 7.5,
                "reversal_early_profit_regimes": tuple(sorted(
                    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
                )),
            },
            "reversal_signals_per_session": 2,
        }
        for threshold, filter_function in (
            PRODUCTION_REVERSAL_CONTINUATION_FILTERS.items()
        )
    },
    **{
        f"round61_brewing_strong5_or_streak{streak}": {
            "signal_filter": filter_function,
            "filter_options": {
                "minimum_strong_count": 5,
                "minimum_state_streak": streak,
                "combine_any": True,
            },
            "research_status": "round61_continuation_route_sensitivity",
            "policy_options": None,
        }
        for streak, filter_function in (
            PRODUCTION_REVERSAL_CONTINUATION_OR_FILTERS.items()
        )
    },
    **{
        f"round61_brewing_strong5_or_streak3_cap_{str(cap).replace('.', '_')}_{signals}_signal": {
            "signal_filter": PRODUCTION_REVERSAL_CONTINUATION_OR_FILTERS[3],
            "filter_options": {
                "minimum_strong_count": 5,
                "minimum_state_streak": 3,
                "combine_any": True,
                "reversal_entry_position_cap_pct": cap,
                "reversal_signals_per_session": signals,
            },
            "research_status": "round61_joint_quality_utilization",
            "policy_options": {
                "reversal_entry_position_cap_pct": cap,
                "reversal_early_profit_regimes": tuple(sorted(
                    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
                )),
            },
            "reversal_signals_per_session": signals,
        }
        for cap in (6.25, 7.5, 8.75)
        for signals in (1, 2)
    },
    "round61_brewing_strong5_or_streak3_cap_5_0_2_signal": {
        "signal_filter": PRODUCTION_REVERSAL_CONTINUATION_OR_FILTERS[3],
        "filter_options": {
            "minimum_strong_count": 5,
            "minimum_state_streak": 3,
            "combine_any": True,
            "reversal_entry_position_cap_pct": 5.0,
            "reversal_signals_per_session": 2,
        },
        "research_status": "round61_joint_quality_utilization",
        "policy_options": None,
        "reversal_signals_per_session": 2,
    },
    **{
        f"round61_brewing_strong{strong}_or_streak{streak}_cap_6_25_2_signal": {
            "signal_filter": _production_reversal_continuation_filter(
                minimum_strong_count=strong,
                minimum_state_streak=streak,
                combine_any=True,
            ),
            "filter_options": {
                "minimum_strong_count": strong,
                "minimum_state_streak": streak,
                "combine_any": True,
                "reversal_entry_position_cap_pct": 6.25,
                "reversal_signals_per_session": 2,
            },
            "research_status": "round61_route_sensitivity",
            "policy_options": {
                "reversal_entry_position_cap_pct": 6.25,
                "reversal_early_profit_regimes": tuple(sorted(
                    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
                )),
            },
            "reversal_signals_per_session": 2,
        }
        for strong, streak in ((4, 3), (5, 2), (5, 4), (6, 3))
    },
    **{
        f"round61_brewing_strong6_or_streak3_cap_{str(cap).replace('.', '_')}_{signals}_signal": {
            "signal_filter": _production_reversal_continuation_filter(
                minimum_strong_count=6,
                minimum_state_streak=3,
                combine_any=True,
            ),
            "filter_options": {
                "minimum_strong_count": 6,
                "minimum_state_streak": 3,
                "combine_any": True,
                "reversal_entry_position_cap_pct": cap,
                "reversal_signals_per_session": signals,
            },
            "research_status": "round61_selected_neighborhood",
            "policy_options": (
                None
                if cap == 5.0
                else {
                    "reversal_entry_position_cap_pct": cap,
                    "reversal_early_profit_regimes": tuple(sorted(
                        NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
                    )),
                }
            ),
            "reversal_signals_per_session": signals,
        }
        for cap in (5.0, 7.5)
        for signals in (1, 2)
    },
    **{
        f"round61_brewing_strong{strong}_or_streak{streak}_cap_6_25_2_signal": {
            "signal_filter": _production_reversal_continuation_filter(
                minimum_strong_count=strong,
                minimum_state_streak=streak,
                combine_any=True,
            ),
            "filter_options": {
                "minimum_strong_count": strong,
                "minimum_state_streak": streak,
                "combine_any": True,
                "reversal_entry_position_cap_pct": 6.25,
                "reversal_signals_per_session": 2,
            },
            "research_status": "round61_selected_neighborhood",
            "policy_options": {
                "reversal_entry_position_cap_pct": 6.25,
                "reversal_early_profit_regimes": tuple(sorted(
                    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
                )),
            },
            "reversal_signals_per_session": 2,
        }
        for strong, streak in ((6, 2), (6, 4), (7, 3))
    },
    **{
        f"round62_brewing_cap_{str(cap).replace('.', '_')}_2_signal": {
            "signal_filter": _production_stage_filter,
            "filter_options": {
                "reversal_entry_position_cap_pct": cap,
                "reversal_signals_per_session": 2,
            },
            "research_status": "round62_user_requested_cap_sensitivity",
            "policy_options": {
                "reversal_entry_position_cap_pct": cap,
                "reversal_early_profit_regimes": tuple(sorted(
                    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
                )),
            },
            "reversal_signals_per_session": 2,
        }
        for cap in (7.5, 8.75, 10.0, 15.0, 20.0, 30.0)
    },
    **{
        (
            f"round65_staged_markup_early_cap_{int(early_cap)}_"
            f"max_profit_{int(maximum_pnl)}"
        ): {
            "signal_filter": _production_stage_filter,
            "holding_upgrade_mode": "staged_markup",
            "holding_upgrade_min_pnl_pct": 2.0,
            "holding_upgrade_max_pnl_pct": maximum_pnl,
            "filter_options": {
                "holding_upgrade_mode": "staged_markup",
                "holding_upgrade_min_pnl_pct": 2.0,
                "holding_upgrade_max_pnl_pct": maximum_pnl,
                "holding_upgrade_early_position_cap_pct": early_cap,
                "holding_upgrade_position_cap_pct": 20.0,
                "required_lifecycle_stage": "markup",
                "required_stock_strong": True,
                "required_stock_leader_tier": True,
                "execution_timing": "next_session_open",
                "reduction_policy": "climax_33",
            },
            "research_status": "round65_staged_markup_scale_in",
            "policy_options": {
                "holding_upgrade_early_position_cap_pct": early_cap,
                "holding_upgrade_position_cap_pct": 20.0,
                "lifecycle_climax_partial_ratio": 1.0 / 3.0,
                "lifecycle_climax_min_pnl_pct": 0.0,
                "reversal_early_profit_regimes": tuple(sorted(
                    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
                )),
            },
        }
        for early_cap in (8.0, 10.0, 12.0)
        for maximum_pnl in (8.0, 10.0, 12.0, 15.0)
    },
    **{
        (
            f"round66_rebalance_pullback_{str(pullback).replace('.', '_')}_"
            f"stall_{stall}_rebound_{str(rebound).replace('.', '_')}"
        ): {
            "signal_filter": _production_stage_filter,
            "holding_upgrade_mode": "staged_markup_rebalance",
            "holding_upgrade_min_pnl_pct": 2.0,
            "holding_upgrade_max_pnl_pct": 12.0,
            "filter_options": {
                "holding_upgrade_mode": "staged_markup_rebalance",
                "holding_upgrade_min_pnl_pct": 2.0,
                "holding_upgrade_max_pnl_pct": 12.0,
                "holding_upgrade_early_position_cap_pct": 10.0,
                "holding_upgrade_position_cap_pct": 20.0,
                "markup_rebalance_pullback_atr": pullback,
                "markup_rebalance_stall_sessions": stall,
                "markup_rebalance_stall_min_atr": 0.25,
                "markup_rebalance_rebound_atr": rebound,
                "markup_rebalance_min_sessions_after_add": 2,
                "markup_rebalance_trim_ratio": 1.0 / 3.0,
                "execution_timing": "next_session_open",
                "reduction_policy": "repeatable_markup_wave",
                "lifetime_add_limit": None,
            },
            "research_status": "round66_repeatable_markup_rebalance",
            "policy_options": {
                "markup_upgrade_only": True,
                "holding_upgrade_early_position_cap_pct": 10.0,
                "holding_upgrade_position_cap_pct": 20.0,
                "markup_rebalance_enabled": True,
                "markup_rebalance_pullback_atr": pullback,
                "markup_rebalance_stall_sessions": stall,
                "markup_rebalance_stall_min_atr": 0.25,
                "markup_rebalance_rebound_atr": rebound,
                "markup_rebalance_min_sessions_after_add": 2,
                "markup_rebalance_trim_ratio": 1.0 / 3.0,
                "lifecycle_climax_partial_ratio": 1.0 / 3.0,
                "lifecycle_climax_min_pnl_pct": 0.0,
                "reversal_early_profit_regimes": tuple(sorted(
                    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
                )),
            },
        }
        for pullback, stall, rebound in (
            (1.0, 3, 0.5),
            (1.25, 3, 0.5),
            (1.0, 4, 0.5),
            (1.25, 4, 0.5),
            (1.0, 3, 0.75),
            (1.0, 20, 0.5),
        )
    },
    **{
        (
            f"round63_{label}_profit_{int(minimum_pnl)}_"
            f"cap_{int(cap)}"
        ): {
            "signal_filter": _production_stage_filter,
            "holding_upgrade_mode": mode,
            "holding_upgrade_min_pnl_pct": minimum_pnl,
            "filter_options": {
                "holding_upgrade_mode": mode,
                "holding_upgrade_min_pnl_pct": minimum_pnl,
                "holding_upgrade_position_cap_pct": cap,
                "execution_timing": "next_session_open",
            },
            "research_status": "round63_dynamic_lifecycle_sizing",
            "policy_options": {
                "holding_upgrade_position_cap_pct": cap,
                "reversal_early_profit_regimes": tuple(sorted(
                    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
                )),
            },
        }
        for label, mode in (
            ("mainline", "confirmed_mainline"),
            ("progressive", "strong_leader_then_mainline"),
        )
        for minimum_pnl in (0.0, 2.0, 5.0)
        for cap in (15.0, 20.0, 25.0, 30.0)
    },
    **{
        f"round63_mainline_cap_{int(cap)}_reduce_{label}": {
            "signal_filter": _production_stage_filter,
            "holding_upgrade_mode": "confirmed_mainline",
            "holding_upgrade_min_pnl_pct": 0.0,
            "filter_options": {
                "holding_upgrade_mode": "confirmed_mainline",
                "holding_upgrade_min_pnl_pct": 0.0,
                "holding_upgrade_position_cap_pct": cap,
                "execution_timing": "next_session_open",
                "reduction_policy": label,
            },
            "research_status": "round63_mainline_scale_reduce",
            "policy_options": {
                "holding_upgrade_position_cap_pct": cap,
                "reversal_early_profit_regimes": tuple(sorted(
                    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
                )),
                **reduction_options,
            },
        }
        for cap in (15.0, 20.0)
        for label, reduction_options in (
            (
                "climax_25",
                {
                    "lifecycle_climax_partial_ratio": 0.25,
                    "lifecycle_climax_min_pnl_pct": 0.0,
                },
            ),
            (
                "climax_33",
                {
                    "lifecycle_climax_partial_ratio": 1.0 / 3.0,
                    "lifecycle_climax_min_pnl_pct": 0.0,
                },
            ),
            (
                "climax_50",
                {
                    "lifecycle_climax_partial_ratio": 0.50,
                    "lifecycle_climax_min_pnl_pct": 0.0,
                },
            ),
            ("fade", {"lifecycle_fade_exit": True}),
            (
                "climax_33_fade",
                {
                    "lifecycle_climax_partial_ratio": 1.0 / 3.0,
                    "lifecycle_climax_min_pnl_pct": 0.0,
                    "lifecycle_fade_exit": True,
                },
            ),
        )
    },
    **{
        (
            f"round64_markup_profit_2_cap_{int(cap)}"
            f"{'_climax_33' if climax_partial else ''}"
        ): {
            "signal_filter": _production_stage_filter,
            "holding_upgrade_mode": "confirmed_markup",
            "holding_upgrade_min_pnl_pct": 2.0,
            "filter_options": {
                "holding_upgrade_mode": "confirmed_markup",
                "holding_upgrade_min_pnl_pct": 2.0,
                "holding_upgrade_position_cap_pct": cap,
                "required_mainline_state": "mainline",
                "required_lifecycle_stage": "markup",
                "required_stock_strong": True,
                "required_stock_leader_tier": True,
                "execution_timing": "next_session_open",
                "reduction_policy": (
                    "climax_33" if climax_partial else "default"
                ),
            },
            "research_status": "round64_markup_only_scale_reduce",
            "policy_options": {
                "holding_upgrade_position_cap_pct": cap,
                "reversal_early_profit_regimes": tuple(sorted(
                    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
                )),
                **({
                    "lifecycle_climax_partial_ratio": 1.0 / 3.0,
                    "lifecycle_climax_min_pnl_pct": 0.0,
                } if climax_partial else {}),
            },
        }
        for cap in (15.0, 20.0, 30.0)
        for climax_partial in (False, True)
    },
    "production_v19_markup_scale_climax_reduce": {
        "signal_filter": _production_stage_filter,
        "holding_upgrade_mode": "confirmed_markup",
        "holding_upgrade_min_pnl_pct": 2.0,
        "filter_options": {
            "holding_upgrade_mode": "confirmed_markup",
            "holding_upgrade_min_pnl_pct": 2.0,
            "holding_upgrade_position_cap_pct": 20.0,
            "required_mainline_state": "mainline",
            "required_lifecycle_stage": "markup",
            "required_stock_strong": True,
            "required_stock_leader_tier": True,
            "execution_timing": "next_session_open",
            "reduction_policy": "climax_33",
        },
        "research_status": "promoted_round64_production_v19",
        "policy_options": {
            "markup_upgrade_only": True,
            "holding_upgrade_position_cap_pct": 20.0,
            "lifecycle_climax_partial_ratio": 1.0 / 3.0,
            "lifecycle_climax_min_pnl_pct": 0.0,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_v20_staged_markup_scale_climax_reduce": {
        "signal_filter": _production_stage_filter,
        "holding_upgrade_mode": "staged_markup",
        "holding_upgrade_min_pnl_pct": 2.0,
        "holding_upgrade_max_pnl_pct": 12.0,
        "filter_options": {
            "holding_upgrade_mode": "staged_markup",
            "holding_upgrade_min_pnl_pct": 2.0,
            "holding_upgrade_max_pnl_pct": 12.0,
            "holding_upgrade_early_position_cap_pct": 10.0,
            "holding_upgrade_position_cap_pct": 20.0,
            "required_lifecycle_stage": "markup",
            "required_stock_strong": True,
            "required_stock_leader_tier": True,
            "execution_timing": "next_session_open",
            "reduction_policy": "climax_33",
        },
        "research_status": "promoted_round65_production_v20",
        "policy_options": {
            "markup_upgrade_only": True,
            "holding_upgrade_early_position_cap_pct": 10.0,
            "holding_upgrade_position_cap_pct": 20.0,
            "lifecycle_climax_partial_ratio": 1.0 / 3.0,
            "lifecycle_climax_min_pnl_pct": 0.0,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_v21_repeatable_markup_rebalance": {
        "signal_filter": _production_stage_filter,
        "holding_upgrade_mode": "staged_markup_rebalance",
        "holding_upgrade_min_pnl_pct": 2.0,
        "holding_upgrade_max_pnl_pct": 12.0,
        "filter_options": {
            "holding_upgrade_mode": "staged_markup_rebalance",
            "holding_upgrade_min_pnl_pct": 2.0,
            "holding_upgrade_max_pnl_pct": 12.0,
            "holding_upgrade_early_position_cap_pct": 10.0,
            "holding_upgrade_position_cap_pct": 20.0,
            "markup_rebalance_pullback_atr": 1.0,
            "markup_rebalance_stall_sessions": 3,
            "markup_rebalance_stall_min_atr": 0.25,
            "markup_rebalance_rebound_atr": 0.5,
            "markup_rebalance_min_sessions_after_add": 2,
            "markup_rebalance_trim_ratio": 1.0 / 3.0,
            "lifetime_add_limit": None,
            "execution_timing": "next_session_open",
        },
        "research_status": "promoted_round66_production_v21",
        "policy_options": {
            "markup_upgrade_only": True,
            "holding_upgrade_early_position_cap_pct": 10.0,
            "holding_upgrade_position_cap_pct": 20.0,
            "markup_rebalance_enabled": True,
            "markup_rebalance_pullback_atr": 1.0,
            "markup_rebalance_stall_sessions": 3,
            "markup_rebalance_stall_min_atr": 0.25,
            "markup_rebalance_rebound_atr": 0.5,
            "markup_rebalance_min_sessions_after_add": 2,
            "markup_rebalance_trim_ratio": 1.0 / 3.0,
            "lifecycle_climax_partial_ratio": 1.0 / 3.0,
            "lifecycle_climax_min_pnl_pct": 0.0,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    **{
        f"round67_cross_theme_slots_{open_slots}_industry_{industry_slots}": {
            "signal_filter": _production_stage_filter,
            "holding_upgrade_mode": "staged_markup_rebalance",
            "holding_upgrade_min_pnl_pct": 2.0,
            "holding_upgrade_max_pnl_pct": 12.0,
            "filter_options": {
                "holding_upgrade_mode": "staged_markup_rebalance",
                "holding_upgrade_min_pnl_pct": 2.0,
                "holding_upgrade_max_pnl_pct": 12.0,
                "holding_upgrade_early_position_cap_pct": 10.0,
                "holding_upgrade_position_cap_pct": 20.0,
                "max_open_positions": open_slots,
                "max_industry_positions": industry_slots,
                "capital_route": "cross_theme_leaders_before_same_theme_followers",
                "execution_timing": "next_session_open",
            },
            "research_status": "round67_cross_theme_slot_sensitivity",
            "policy_options": {
                "markup_upgrade_only": True,
                "holding_upgrade_early_position_cap_pct": 10.0,
                "holding_upgrade_position_cap_pct": 20.0,
                "markup_rebalance_enabled": True,
                "markup_rebalance_pullback_atr": 1.0,
                "markup_rebalance_stall_sessions": 3,
                "markup_rebalance_stall_min_atr": 0.25,
                "markup_rebalance_rebound_atr": 0.5,
                "markup_rebalance_min_sessions_after_add": 2,
                "markup_rebalance_trim_ratio": 1.0 / 3.0,
                "lifecycle_climax_partial_ratio": 1.0 / 3.0,
                "lifecycle_climax_min_pnl_pct": 0.0,
                "max_open_positions": open_slots,
                "max_industry_positions": industry_slots,
                "reversal_early_profit_regimes": tuple(sorted(
                    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
                )),
            },
        }
        for open_slots, industry_slots in (
            (5, 1),
            (6, 1),
            (7, 1),
            (6, 2),
            (7, 2),
        )
    },
    **{
        f"round67_leader_precedence_slots_{open_slots}": {
            "signal_filter": _production_stage_filter,
            "signal_order": (
                "niu_leader", "niu_pullback", "niu_emerging",
                "niu_reversal_probe",
            ),
            "holding_upgrade_mode": "staged_markup_rebalance",
            "holding_upgrade_min_pnl_pct": 2.0,
            "holding_upgrade_max_pnl_pct": 12.0,
            "filter_options": {
                "holding_upgrade_mode": "staged_markup_rebalance",
                "holding_upgrade_min_pnl_pct": 2.0,
                "holding_upgrade_max_pnl_pct": 12.0,
                "holding_upgrade_early_position_cap_pct": 10.0,
                "holding_upgrade_position_cap_pct": 20.0,
                "max_open_positions": open_slots,
                "max_industry_positions": 2,
                "capital_route": "mature_leaders_before_brewing_probes",
                "execution_timing": "next_session_open",
            },
            "research_status": "round67_post_hoc_leader_precedence_diagnostic",
            "policy_options": {
                "markup_upgrade_only": True,
                "holding_upgrade_early_position_cap_pct": 10.0,
                "holding_upgrade_position_cap_pct": 20.0,
                "markup_rebalance_enabled": True,
                "markup_rebalance_pullback_atr": 1.0,
                "markup_rebalance_stall_sessions": 3,
                "markup_rebalance_stall_min_atr": 0.25,
                "markup_rebalance_rebound_atr": 0.5,
                "markup_rebalance_min_sessions_after_add": 2,
                "markup_rebalance_trim_ratio": 1.0 / 3.0,
                "lifecycle_climax_partial_ratio": 1.0 / 3.0,
                "lifecycle_climax_min_pnl_pct": 0.0,
                "max_open_positions": open_slots,
                "max_industry_positions": 2,
                "reversal_early_profit_regimes": tuple(sorted(
                    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
                )),
            },
        }
        for open_slots in (5, 6)
    },
    "production_reversal_min_today_strength_30": {
        "signal_filter": PRODUCTION_REVERSAL_TODAY_STRENGTH_30_FILTER,
        "filter_options": {"minimum_today_strength": 30.0},
        "research_status": "rejected_round9",
        "policy_options": None,
    },
    "production_reversal_max_recovery_2": {
        "signal_filter": PRODUCTION_REVERSAL_RECOVERY_CAP_2_FILTER,
        "filter_options": {"maximum_recovery_ratio": 2.0},
        "research_status": "promoted_round58",
        "policy_options": None,
    },
    "production_daily_v_no_progress_requires_unconfirmed": {
        "signal_filter": _production_stage_filter,
        "research_status": "rejected_round10",
        "policy_options": {
            "daily_v_no_progress_requires_unconfirmed": True,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_mature_stage_entry_precedence": {
        "signal_filter": _production_stage_filter,
        "signal_order": (
            "niu_leader", "niu_pullback", "niu_emerging",
            "niu_reversal_probe",
        ),
        "research_status": "rejected_round11",
        "policy_options": None,
    },
    "production_one_new_position_per_session": {
        "signal_filter": _production_stage_filter,
        "research_status": "rejected_round11",
        "policy_options": {
            "max_new_positions_per_session": 1,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_entry_order_scale_075": {
        "signal_filter": _production_stage_filter,
        "research_status": "diagnostic_round49",
        "policy_options": {
            "entry_order_scale": 0.75,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_entry_order_scale_050": {
        "signal_filter": _production_stage_filter,
        "research_status": "diagnostic_round49",
        "policy_options": {
            "entry_order_scale": 0.50,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_entry_order_scale_025": {
        "signal_filter": _production_stage_filter,
        "research_status": "diagnostic_round49",
        "policy_options": {
            "entry_order_scale": 0.25,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_upgrade_reversal_on_persistent_emerging": {
        "signal_filter": _production_stage_filter,
        "holding_upgrade_mode": "persistent_emerging",
        "research_status": "rejected_round12",
        "policy_options": None,
    },
    "production_upgrade_reversal_on_strong_leader": {
        "signal_filter": _production_stage_filter,
        "holding_upgrade_mode": "strong_leader",
        "research_status": "rejected_round12",
        "policy_options": None,
    },
    "production_upgrade_on_confirmed_mainline": {
        "signal_filter": _production_stage_filter,
        "holding_upgrade_mode": "confirmed_mainline",
        "research_status": "rejected_round12",
        "policy_options": None,
    },
    "production_upgrade_strong_leader_then_mainline": {
        "signal_filter": _production_stage_filter,
        "holding_upgrade_mode": "strong_leader_then_mainline",
        "research_status": "rejected_round12",
        "policy_options": None,
    },
    "production_scale_reversal_on_persistent_emerging": {
        "signal_filter": _production_stage_filter,
        "holding_upgrade_mode": "persistent_emerging",
        "research_status": "rejected_round12",
        "policy_options": {
            "holding_upgrade_preserves_strategy": True,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_scale_reversal_on_strong_leader": {
        "signal_filter": _production_stage_filter,
        "holding_upgrade_mode": "strong_leader",
        "research_status": "rejected_round12",
        "policy_options": {
            "holding_upgrade_preserves_strategy": True,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_scale_persistent_profit_2_cap_10": {
        "signal_filter": _production_stage_filter,
        "holding_upgrade_mode": "persistent_emerging",
        "holding_upgrade_min_pnl_pct": 2.0,
        "research_status": "rejected_round12",
        "policy_options": {
            "holding_upgrade_preserves_strategy": True,
            "holding_upgrade_position_cap_pct": 10.0,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_scale_persistent_profit_5_cap_10": {
        "signal_filter": _production_stage_filter,
        "holding_upgrade_mode": "persistent_emerging",
        "holding_upgrade_min_pnl_pct": 5.0,
        "research_status": "rejected_round12",
        "policy_options": {
            "holding_upgrade_preserves_strategy": True,
            "holding_upgrade_position_cap_pct": 10.0,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_scale_strong_leader_profit_2_cap_10": {
        "signal_filter": _production_stage_filter,
        "holding_upgrade_mode": "strong_leader",
        "holding_upgrade_min_pnl_pct": 2.0,
        "research_status": "rejected_round12",
        "policy_options": {
            "holding_upgrade_preserves_strategy": True,
            "holding_upgrade_position_cap_pct": 10.0,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_scale_strong_leader_profit_5_cap_10": {
        "signal_filter": _production_stage_filter,
        "holding_upgrade_mode": "strong_leader",
        "holding_upgrade_min_pnl_pct": 5.0,
        "research_status": "rejected_round12",
        "policy_options": {
            "holding_upgrade_preserves_strategy": True,
            "holding_upgrade_position_cap_pct": 10.0,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_reversal_max_execution_gap_0": {
        "signal_filter": _production_stage_filter,
        "research_status": "rejected_round13",
        "policy_options": {
            "reversal_max_execution_gap_pct": 0.0,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_reversal_max_execution_gap_025": {
        "signal_filter": _production_stage_filter,
        "research_status": "rejected_round13",
        "policy_options": {
            "reversal_max_execution_gap_pct": 0.25,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_reversal_max_execution_gap_05": {
        "signal_filter": _production_stage_filter,
        "research_status": "rejected_round13",
        "policy_options": {
            "reversal_max_execution_gap_pct": 0.5,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_reversal_max_execution_gap_075": {
        "signal_filter": _production_stage_filter,
        "research_status": "rejected_round13",
        "policy_options": {
            "reversal_max_execution_gap_pct": 0.75,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_reversal_max_execution_gap_10": {
        "signal_filter": _production_stage_filter,
        "research_status": "shadow_round13",
        "policy_options": {
            "reversal_max_execution_gap_pct": 1.0,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_reversal_max_execution_gap_125": {
        "signal_filter": _production_stage_filter,
        "research_status": "rejected_round13",
        "policy_options": {
            "reversal_max_execution_gap_pct": 1.25,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_reversal_max_execution_gap_15": {
        "signal_filter": _production_stage_filter,
        "research_status": "rejected_round13",
        "policy_options": {
            "reversal_max_execution_gap_pct": 1.5,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_reversal_unconfirmed_min_strength_20": {
        "signal_filter": PRODUCTION_REVERSAL_UNCONFIRMED_STRENGTH_FILTERS[20.0],
        "filter_options": {
            "minimum_unconfirmed_today_strength": 20.0,
        },
        "research_status": "rejected_round14",
        "policy_options": None,
    },
    "production_reversal_unconfirmed_min_strength_25": {
        "signal_filter": PRODUCTION_REVERSAL_UNCONFIRMED_STRENGTH_FILTERS[25.0],
        "filter_options": {
            "minimum_unconfirmed_today_strength": 25.0,
        },
        "research_status": "rejected_round14",
        "policy_options": None,
    },
    "production_reversal_unconfirmed_min_strength_30": {
        "signal_filter": PRODUCTION_REVERSAL_UNCONFIRMED_STRENGTH_FILTERS[30.0],
        "filter_options": {
            "minimum_unconfirmed_today_strength": 30.0,
        },
        "research_status": "rejected_round14",
        "policy_options": None,
    },
    "production_reversal_unconfirmed_min_strength_35": {
        "signal_filter": PRODUCTION_REVERSAL_UNCONFIRMED_STRENGTH_FILTERS[35.0],
        "filter_options": {
            "minimum_unconfirmed_today_strength": 35.0,
        },
        "research_status": "rejected_round14",
        "policy_options": None,
    },
    "production_reversal_mainline_peak_decay_25": {
        "signal_filter": _production_stage_filter,
        "research_status": "rejected_round14",
        "policy_options": {
            "reversal_mainline_peak_drawdown_points": 2.5,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_reversal_mainline_peak_decay_50": {
        "signal_filter": _production_stage_filter,
        "research_status": "rejected_round14",
        "policy_options": {
            "reversal_mainline_peak_drawdown_points": 5.0,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_reversal_mainline_peak_decay_75": {
        "signal_filter": _production_stage_filter,
        "research_status": "rejected_round14",
        "policy_options": {
            "reversal_mainline_peak_drawdown_points": 7.5,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_reversal_mainline_peak_decay_100": {
        "signal_filter": _production_stage_filter,
        "research_status": "rejected_round14",
        "policy_options": {
            "reversal_mainline_peak_drawdown_points": 10.0,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_reversal_strong_leader_exit_promotion": {
        "signal_filter": _production_stage_filter,
        "research_status": "rejected_round25",
        "policy_options": {
            "reversal_strong_leader_exit_promotion": True,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_reversal_strong_leader_mainline_exit": {
        "signal_filter": _production_stage_filter,
        "research_status": "rejected_round26",
        "policy_options": {
            "reversal_strong_leader_mainline_exit": True,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_daily_v_unconfirmed_failure_t2": {
        "signal_filter": _production_stage_filter,
        "research_status": "rejected_round27",
        "policy_options": {
            "daily_v_unconfirmed_failure_hold_days": 2,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_lifecycle_early_recovery_lt2": {
        "signal_filter": _lifecycle_early_recovery_filter,
        "filter_options": {
            "entry_mainline_states": ("candidate", "emerging"),
            "observed_entry_lifecycle_stages": (
                "brewing",
            ),
            "lifecycle_stage_is_filter": True,
            "entry_strategy_ids": ("niu_reversal_probe",),
            "maximum_daily_v_recovery_ratio_exclusive": 2.0,
            "climax_new_entries": False,
            "fade_new_entries": False,
        },
        "research_status": "shadow_round29",
        "policy_options": None,
    },
    "production_lifecycle_stage_entry_contract": {
        "signal_filter": _lifecycle_stage_entry_contract_filter,
        "filter_options": {
            "brewing_entry_strategy_ids": ("niu_reversal_probe",),
            "markup_entry_strategy_ids": (
                "niu_emerging", "niu_leader",
            ),
            "climax_entry_strategy_ids": (),
            "divergence_entry_strategy_ids": (
                "niu_leader", "niu_pullback",
            ),
            "fade_entry_strategy_ids": (),
        },
        "research_status": "promoted_current",
        "policy_options": None,
    },
    "production_lifecycle_stage_routed_early_recovery_lt2": {
        "signal_filter": _lifecycle_stage_routed_early_recovery_filter,
        "filter_options": {
            "brewing_entry_strategy_ids": ("niu_reversal_probe",),
            "markup_entry_strategy_ids": (
                "niu_emerging", "niu_leader",
            ),
            "climax_entry_strategy_ids": (),
            "divergence_entry_strategy_ids": (
                "niu_leader", "niu_pullback",
            ),
            "fade_entry_strategy_ids": (),
            "maximum_daily_v_recovery_ratio_exclusive": 2.0,
        },
        "research_status": "promoted_current",
        "policy_options": None,
    },
    "production_lifecycle_early_recovery_lt2_upgrade_top5_persistent": {
        "signal_filter": _lifecycle_early_recovery_filter,
        "holding_upgrade_mode": "full_theme_top5_persistent",
        "filter_options": {
            "entry_candidate": "production_lifecycle_early_recovery_lt2",
            "holding_source_strategy_id": "niu_reversal_probe",
            "required_lifecycle_state": "emerging",
            "required_cross_day_persistent": True,
            "required_theme_rank_scope": (
                "full_historical_theme_cross_section"
            ),
            "maximum_theme_rank": 5,
            "upgrade_strategy_id": "niu_emerging",
            "execution_timing": "next_session_open",
        },
        "research_status": "rejected_round36",
        "policy_options": {
            "holding_upgrade_position_cap_pct": 10.0,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_lifecycle_early_recovery_lt2_upgrade_new_top5_persistent": {
        "signal_filter": _lifecycle_early_recovery_filter,
        "holding_upgrade_mode": "full_theme_new_top5_persistent",
        "filter_options": {
            "entry_candidate": "production_lifecycle_early_recovery_lt2",
            "holding_source_strategy_id": "niu_reversal_probe",
            "required_lifecycle_state": "emerging",
            "required_cross_day_persistent": True,
            "required_theme_rank_scope": (
                "full_historical_theme_cross_section"
            ),
            "maximum_theme_rank": 5,
            "required_theme_transition": "new_top5",
            "upgrade_strategy_id": "niu_emerging",
            "execution_timing": "next_session_open",
        },
        "research_status": "rejected_round36",
        "policy_options": {
            "holding_upgrade_position_cap_pct": 10.0,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_lifecycle_early_recovery_lt2_scale_top5_persistent": {
        "signal_filter": _lifecycle_early_recovery_filter,
        "holding_upgrade_mode": "full_theme_top5_persistent",
        "filter_options": {
            "entry_candidate": "production_lifecycle_early_recovery_lt2",
            "holding_source_strategy_id": "niu_reversal_probe",
            "required_lifecycle_state": "emerging",
            "required_cross_day_persistent": True,
            "required_theme_rank_scope": (
                "full_historical_theme_cross_section"
            ),
            "maximum_theme_rank": 5,
            "preserve_exit_strategy_id": True,
            "execution_timing": "next_session_open",
        },
        "research_status": "rejected_round37",
        "policy_options": {
            "holding_upgrade_preserves_strategy": True,
            "holding_upgrade_position_cap_pct": 10.0,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_lifecycle_early_recovery_lt2_scale_new_top5_persistent": {
        "signal_filter": _lifecycle_early_recovery_filter,
        "holding_upgrade_mode": "full_theme_new_top5_persistent",
        "filter_options": {
            "entry_candidate": "production_lifecycle_early_recovery_lt2",
            "holding_source_strategy_id": "niu_reversal_probe",
            "required_lifecycle_state": "emerging",
            "required_cross_day_persistent": True,
            "required_theme_rank_scope": (
                "full_historical_theme_cross_section"
            ),
            "maximum_theme_rank": 5,
            "required_theme_transition": "new_top5",
            "preserve_exit_strategy_id": True,
            "execution_timing": "next_session_open",
        },
        "research_status": "rejected_round37",
        "policy_options": {
            "holding_upgrade_preserves_strategy": True,
            "holding_upgrade_position_cap_pct": 10.0,
            "reversal_early_profit_regimes": tuple(sorted(
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            )),
        },
    },
    "production_lifecycle_early_recovery_lt2_stage_exit": {
        "signal_filter": _lifecycle_early_recovery_filter,
        "filter_options": {
            "observed_entry_lifecycle_stages": ("brewing",),
            "lifecycle_stage_is_filter": True,
            "entry_strategy_ids": ("niu_reversal_probe",),
            "maximum_daily_v_recovery_ratio_exclusive": 2.0,
            "climax_new_entries": False,
            "fade_new_entries": False,
        },
        "research_status": "rejected_round31",
        "policy_options": {
            "lifecycle_climax_partial_ratio": 1.0 / 3.0,
            "lifecycle_climax_min_pnl_pct": 0.0,
            "lifecycle_fade_exit": True,
        },
    },
    "production_lifecycle_early_recovery_lt2_breadth60": {
        "signal_filter": LIFECYCLE_EARLY_BREADTH_60_FILTER,
        "filter_options": {
            "observed_entry_lifecycle_stages": ("brewing",),
            "lifecycle_stage_is_filter": True,
            "entry_strategy_ids": ("niu_reversal_probe",),
            "maximum_daily_v_recovery_ratio_exclusive": 2.0,
            "minimum_today_breadth_pct": REJECTED_ROUND32_MIN_TODAY_BREADTH_PCT,
        },
        "research_status": "rejected_round32",
        "policy_options": None,
    },
    "production_lifecycle_early_recovery_lt2_today_strength40": {
        "signal_filter": LIFECYCLE_EARLY_TODAY_STRENGTH_40_FILTER,
        "filter_options": {
            "observed_entry_lifecycle_stages": ("brewing",),
            "lifecycle_stage_is_filter": True,
            "entry_strategy_ids": ("niu_reversal_probe",),
            "maximum_daily_v_recovery_ratio_exclusive": 2.0,
            "minimum_today_strength_score": 40.0,
        },
        "research_status": "rejected_round32",
        "policy_options": None,
    },
    "production_lifecycle_early_recovery_lt2_signal_stop3": {
        "signal_filter": LIFECYCLE_EARLY_SIGNAL_STOP_3_FILTER,
        "filter_options": {
            "observed_entry_lifecycle_stages": ("brewing",),
            "lifecycle_stage_is_filter": True,
            "entry_strategy_ids": ("niu_reversal_probe",),
            "maximum_daily_v_recovery_ratio_exclusive": 2.0,
            "minimum_signal_stop_distance_pct": 3.0,
        },
        "research_status": "rejected_round32",
        "policy_options": None,
    },
    "production_lifecycle_early_recovery_lt2_theme_top2": {
        "signal_filter": LIFECYCLE_EARLY_THEME_TOP2_FILTER,
        "filter_options": {
            "observed_entry_lifecycle_stages": ("brewing",),
            "lifecycle_stage_is_filter": True,
            "entry_strategy_ids": ("niu_reversal_probe",),
            "maximum_daily_v_recovery_ratio_exclusive": 2.0,
            "maximum_theme_rank": 2,
        },
        "research_status": "rejected_round33",
        "policy_options": None,
    },
    "production_lifecycle_early_recovery_lt2_theme_top5": {
        "signal_filter": LIFECYCLE_EARLY_THEME_TOP5_FILTER,
        "filter_options": {
            "observed_entry_lifecycle_stages": ("brewing",),
            "lifecycle_stage_is_filter": True,
            "entry_strategy_ids": ("niu_reversal_probe",),
            "maximum_daily_v_recovery_ratio_exclusive": 2.0,
            "maximum_theme_rank": 5,
        },
        "research_status": "rejected_round33",
        "policy_options": None,
    },
    "production_markup_theme_improving": {
        "signal_filter": PRODUCTION_MARKUP_THEME_IMPROVING_FILTER,
        "filter_options": {
            "guarded_entry_strategy_ids": tuple(sorted(MARKUP_STRATEGY_IDS)),
            "required_theme_rank_scope": (
                "full_historical_theme_cross_section"
            ),
            "minimum_theme_percentile_change_exclusive": 0.0,
        },
        "research_status": "rejected_round34",
        "policy_options": None,
    },
    "production_markup_theme_non_declining": {
        "signal_filter": PRODUCTION_MARKUP_THEME_NON_DECLINING_FILTER,
        "filter_options": {
            "guarded_entry_strategy_ids": tuple(sorted(MARKUP_STRATEGY_IDS)),
            "required_theme_rank_scope": (
                "full_historical_theme_cross_section"
            ),
            "minimum_theme_percentile_change_inclusive": 0.0,
        },
        "research_status": "rejected_round34",
        "policy_options": None,
    },
    "production_reversal_min_signal_score_88": {
        "signal_filter": PRODUCTION_REVERSAL_SCORE_FILTERS[8.8],
        "filter_options": {"minimum_signal_score": 8.8},
        "research_status": "rejected_round15",
        "policy_options": None,
    },
    "production_reversal_min_signal_score_89": {
        "signal_filter": PRODUCTION_REVERSAL_SCORE_FILTERS[8.9],
        "filter_options": {"minimum_signal_score": 8.9},
        "research_status": "rejected_round15",
        "policy_options": None,
    },
    "production_reversal_min_signal_score_90": {
        "signal_filter": PRODUCTION_REVERSAL_SCORE_FILTERS[9.0],
        "filter_options": {"minimum_signal_score": 9.0},
        "research_status": "rejected_round15",
        "policy_options": None,
    },
    "production_reversal_min_signal_score_91": {
        "signal_filter": PRODUCTION_REVERSAL_SCORE_FILTERS[9.1],
        "filter_options": {"minimum_signal_score": 9.1},
        "research_status": "rejected_round15",
        "policy_options": None,
    },
    "production_reversal_min_candidate_count_2": {
        "signal_filter": PRODUCTION_REVERSAL_CANDIDATE_COUNT_FILTERS[2],
        "filter_options": {"minimum_candidate_count": 2},
        "research_status": "rejected_round15",
        "policy_options": None,
    },
    "production_reversal_min_candidate_count_4": {
        "signal_filter": PRODUCTION_REVERSAL_CANDIDATE_COUNT_FILTERS[4],
        "filter_options": {"minimum_candidate_count": 4},
        "research_status": "rejected_round15",
        "policy_options": None,
    },
    "production_reversal_max_top_score_gap_02": {
        "signal_filter": PRODUCTION_REVERSAL_TOP_GAP_FILTERS[0.2],
        "filter_options": {"maximum_top_score_gap": 0.2},
        "research_status": "rejected_round15",
        "policy_options": None,
    },
    "production_reversal_max_top_score_gap_05": {
        "signal_filter": PRODUCTION_REVERSAL_TOP_GAP_FILTERS[0.5],
        "filter_options": {"maximum_top_score_gap": 0.5},
        "research_status": "rejected_round15",
        "policy_options": None,
    },
    "production_reversal_min_decline_10": {
        "signal_filter": PRODUCTION_REVERSAL_DECLINE_FILTERS[10.0],
        "filter_options": {"minimum_decline_pct": 10.0},
        "research_status": "rejected_round16",
        "policy_options": None,
    },
    "production_reversal_min_decline_12": {
        "signal_filter": PRODUCTION_REVERSAL_DECLINE_FILTERS[12.0],
        "filter_options": {"minimum_decline_pct": 12.0},
        "research_status": "rejected_round16",
        "policy_options": None,
    },
    "production_reversal_min_decline_14": {
        "signal_filter": PRODUCTION_REVERSAL_DECLINE_FILTERS[14.0],
        "filter_options": {"minimum_decline_pct": 14.0},
        "research_status": "rejected_round16",
        "policy_options": None,
    },
    "production_reversal_max_recovery_100": {
        "signal_filter": PRODUCTION_REVERSAL_RECOVERY_CAP_FILTERS[1.0],
        "filter_options": {"maximum_recovery_ratio": 1.0},
        "research_status": "rejected_round16",
        "policy_options": None,
    },
    "production_reversal_max_recovery_110": {
        "signal_filter": PRODUCTION_REVERSAL_RECOVERY_CAP_FILTERS[1.1],
        "filter_options": {"maximum_recovery_ratio": 1.1},
        "research_status": "rejected_round16",
        "policy_options": None,
    },
    "production_reversal_max_recovery_120": {
        "signal_filter": PRODUCTION_REVERSAL_RECOVERY_CAP_FILTERS[1.2],
        "filter_options": {"maximum_recovery_ratio": 1.2},
        "research_status": "rejected_round16",
        "policy_options": None,
    },
    "production_reversal_min_decline_12_max_recovery_120": {
        "signal_filter": PRODUCTION_REVERSAL_DECLINE_12_RECOVERY_12_FILTER,
        "filter_options": {
            "minimum_decline_pct": 12.0,
            "maximum_recovery_ratio": 1.2,
        },
        "research_status": "rejected_round16",
        "policy_options": None,
    },
    "production_reversal_candidate_state_only": {
        "signal_filter": PRODUCTION_REVERSAL_CANDIDATE_STATE_FILTER,
        "filter_options": {"allowed_mainline_states": ["candidate"]},
        "research_status": "rejected_round16",
        "policy_options": None,
    },
    # Keep the incumbent policy explicit so this benchmark remains stable if a
    # researched candidate is later promoted to the production defaults.
    "production_baseline": {
        "signal_filter": None,
        "policy_options": {
            "partial_take_profit_r": 2.0,
            "partial_take_profit_ratio": 0.5,
            "intraday_profit_target": False,
            "break_even_after_partial": False,
            "reversal_mainline_weak_confirmations": None,
        },
    },
    "production_intraday_2r": {
        "signal_filter": None,
        "policy_options": {
            "partial_take_profit_r": 2.0,
            "partial_take_profit_ratio": 0.5,
            "intraday_profit_target": True,
            "break_even_after_partial": False,
            "reversal_mainline_weak_confirmations": None,
        },
    },
    "production_intraday_1r_breakeven": {
        "signal_filter": None,
        "policy_options": {
            "partial_take_profit_r": 1.0,
            "partial_take_profit_ratio": 0.5,
            "intraday_profit_target": True,
            "break_even_after_partial": True,
            "reversal_mainline_weak_confirmations": None,
        },
    },
    "production_intraday_125r_breakeven": {
        "signal_filter": None,
        "policy_options": {
            "partial_take_profit_r": 1.25,
            "partial_take_profit_ratio": 0.5,
            "intraday_profit_target": True,
            "break_even_after_partial": True,
            "reversal_mainline_weak_confirmations": None,
        },
    },
    "production_intraday_15r_breakeven": {
        "signal_filter": None,
        "policy_options": {
            "partial_take_profit_r": 1.5,
            "partial_take_profit_ratio": 0.5,
            "intraday_profit_target": True,
            "break_even_after_partial": True,
            "reversal_mainline_weak_confirmations": None,
        },
    },
    "production_intraday_075r_breakeven": {
        "signal_filter": None,
        "policy_options": {
            "partial_take_profit_r": 0.75,
            "partial_take_profit_ratio": 0.5,
            "intraday_profit_target": True,
            "break_even_after_partial": True,
            "reversal_mainline_weak_confirmations": None,
        },
    },
    "production_reversal_theme_exit": {
        "signal_filter": None,
        "policy_options": {
            "partial_take_profit_r": 2.0,
            "partial_take_profit_ratio": 0.5,
            "intraday_profit_target": False,
            "break_even_after_partial": False,
            "reversal_mainline_weak_confirmations": 1,
        },
    },
    "production_intraday_1r_breakeven_theme_exit": {
        "signal_filter": None,
        "policy_options": {
            "partial_take_profit_r": 1.0,
            "partial_take_profit_ratio": 0.5,
            "intraday_profit_target": True,
            "break_even_after_partial": True,
            "reversal_mainline_weak_confirmations": 1,
        },
    },
    "production_intraday_1r_one_third_breakeven_theme_exit": {
        "signal_filter": None,
        "policy_options": {
            "partial_take_profit_r": 1.0,
            "partial_take_profit_ratio": 1.0 / 3.0,
            "intraday_profit_target": True,
            "break_even_after_partial": True,
            "reversal_mainline_weak_confirmations": 1,
        },
    },
    "production_intraday_1r_40pct_breakeven_theme_exit": {
        "signal_filter": None,
        "policy_options": {
            "partial_take_profit_r": 1.0,
            "partial_take_profit_ratio": 0.4,
            "intraday_profit_target": True,
            "break_even_after_partial": True,
            "reversal_mainline_weak_confirmations": 1,
        },
    },
    "production_intraday_1r_45pct_breakeven_theme_exit": {
        "signal_filter": None,
        "policy_options": {
            "partial_take_profit_r": 1.0,
            "partial_take_profit_ratio": 0.45,
            "intraday_profit_target": True,
            "break_even_after_partial": True,
            "reversal_mainline_weak_confirmations": 1,
        },
    },
    "production_intraday_075r_breakeven_theme_exit": {
        "signal_filter": None,
        "policy_options": {
            "partial_take_profit_r": 0.75,
            "partial_take_profit_ratio": 0.5,
            "intraday_profit_target": True,
            "break_even_after_partial": True,
            "reversal_mainline_weak_confirmations": 1,
        },
    },
    "production_intraday_075r_one_third_breakeven_theme_exit": {
        "signal_filter": None,
        "policy_options": {
            "partial_take_profit_r": 0.75,
            "partial_take_profit_ratio": 1.0 / 3.0,
            "intraday_profit_target": True,
            "break_even_after_partial": True,
            "reversal_mainline_weak_confirmations": 1,
        },
    },
    "rotation_growing_theme_reversal": {
        "signal_filter": ROTATION_GROWING_THEME_FILTER,
        "policy_options": {
            "partial_take_profit_r": 2.0,
            "partial_take_profit_ratio": 0.5,
            "intraday_profit_target": False,
            "break_even_after_partial": False,
            "reversal_mainline_weak_confirmations": None,
        },
    },
    "rotation_theme_intraday_1r_breakeven": {
        "signal_filter": ROTATION_GROWING_THEME_FILTER,
        "policy_options": {
            "partial_take_profit_r": 1.0,
            "partial_take_profit_ratio": 0.5,
            "intraday_profit_target": True,
            "break_even_after_partial": True,
            "reversal_mainline_weak_confirmations": None,
        },
    },
    "rotation_theme_intraday_075r_breakeven_exit": {
        "signal_filter": ROTATION_GROWING_THEME_FILTER,
        "policy_options": {
            "partial_take_profit_r": 0.75,
            "partial_take_profit_ratio": 0.5,
            "intraday_profit_target": True,
            "break_even_after_partial": True,
            "reversal_mainline_weak_confirmations": 1,
        },
    },
}

ROUND16_REVERSAL_SHAPE_CANDIDATES: Mapping[str, Mapping[str, Any]] = (
    MappingProxyType({
        name: candidate
        for name, candidate in CANDIDATES.items()
        if candidate.get("research_status") == "rejected_round16"
    })
)


def _research_threshold_scorer(
    strategy_id: str,
    threshold: float,
) -> Callable[..., dict[str, Any] | None]:
    """Lower only the research emission floor, preserving production gates."""
    scorer = STRATEGY_SCORERS[strategy_id]
    requires_context = bool(getattr(scorer, "requires_context", False))

    def scored_rows(
        rows: list[dict[str, Any]],
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        result = (
            scorer(rows, dict(context or {}))
            if requires_context
            else scorer(rows)
        )
        if not isinstance(result, Mapping):
            return None
        payload = dict(result)
        payload["entry_threshold"] = float(threshold)
        payload["actionable"] = bool(
            (_number(payload.get("score")) or 0.0) >= float(threshold)
            and not payload.get("hard_blockers")
        )
        return payload

    scored_rows.requires_context = requires_context  # type: ignore[attr-defined]
    return scored_rows


class _TrustedResearchStageSelector(RegisteredScorerSelector):
    """Emit all four NiuOne stages at the frozen Round16 research floors."""

    def __init__(self, eligible_symbols: Iterable[str]) -> None:
        resolved = tuple(dict.fromkeys(eligible_symbols))
        scorers = {
            strategy_id: _research_threshold_scorer(strategy_id, threshold)
            for strategy_id, threshold in ROUND16_STAGE_SOURCE_THRESHOLDS.items()
        }
        super().__init__(
            tuple(ROUND16_STAGE_SOURCE_THRESHOLDS),
            max_signals_per_session=max(1, len(resolved)),
            context_provider=NiuOneHistoricalContextProvider(),
            eligible_symbols=resolved,
            scorers=scorers,
        )
        self._trusted_scorers = True
        self._history_limit = BUILTIN_STRATEGY_HISTORY_LIMIT


class _TrustedResearchPullbackSelector(RegisteredScorerSelector):
    """Use the pure research wrapper with the built-in bounded row history."""

    def __init__(self, eligible_symbols: Iterable[str]) -> None:
        resolved = tuple(dict.fromkeys(eligible_symbols))
        super().__init__(
            ("niu_pullback",),
            max_signals_per_session=max(1, len(resolved)),
            context_provider=NiuOneHistoricalContextProvider(),
            eligible_symbols=resolved,
            scorers={"niu_pullback": _research_pullback_scorer},
        )
        # The wrapper calls the immutable production scorer and does not mutate
        # input rows.  Bounding history here avoids copying an expanding prefix
        # for every stock/session while keeping the custom-scorer default safe.
        self._trusted_scorers = True
        self._history_limit = BUILTIN_STRATEGY_HISTORY_LIMIT


def _cached_scope_eligible_symbols(
    bars: Mapping[str, Iterable[HistoricalBar]],
    metadata: Mapping[str, Any],
) -> tuple[str, ...]:
    configured_scope = tuple(metadata.get("configured_scope") or ("main_board",))
    return tuple(sorted(
        symbol
        for symbol, series in bars.items()
        if (
            (rows := tuple(series))
            and stock_in_universe(
                symbol[-6:],
                rows[-1].name,
                configured_scope,
            )
        )
    ))


def _cached_eligible_symbol_count(metadata: Mapping[str, Any]) -> int:
    """Prefer the replayable eligible count over the pre-fetch universe size."""
    return int(
        metadata.get("eligible_symbols_with_bars")
        or metadata.get("eligible_symbol_count")
        or 0
    )








def _build_round16_stage_tape(
    bars: Mapping[str, Iterable[HistoricalBar]],
    metadata: Mapping[str, Any],
    *,
    start: str,
    end: str,
) -> tuple[SelectionReplayTape, tuple[str, ...]]:
    """Build the low-floor four-stage source tape from trusted cached bars."""
    eligible = _cached_scope_eligible_symbols(bars, metadata)
    if not eligible:
        raise ValueError("cached bars do not contain an eligible stage universe")
    tape = build_selection_replay_tape(
        bars,
        _TrustedResearchStageSelector(eligible),
        config=SelectionBacktestConfig(
            holding_sessions=(1, 3, 5, 10, 20),
            signal_start_date=start,
            signal_end_date=end,
            cooldown_sessions=0,
            slippage_bps=5,
        ),
        progress_callback=lambda done, total, day: (
            print(f"stage-wide-select: {done}/{total} {day}", flush=True)
            if done == total or done % 20 == 0 else None
        ),
        scored_fields=SCORER_EXIT_FIELDS,
        cross_section_fields=THEME_CROSS_SECTION_FIELDS,
    )
    return tape, eligible




def _build_round17_pullback_tape(
    bars: Mapping[str, Iterable[HistoricalBar]],
    base_tape: SelectionReplayTape,
    metadata: Mapping[str, Any],
    *,
    start: str,
    end: str,
) -> tuple[SelectionReplayTape, tuple[str, ...]]:
    """Build the wide pullback union from cached bars, without network access."""
    _validate_stage_entry_cache(metadata)
    eligible = _cached_scope_eligible_symbols(bars, metadata)
    if not eligible:
        raise ValueError("cached bars do not contain an eligible pullback universe")
    pullback_tape = build_selection_replay_tape(
        bars,
        _TrustedResearchPullbackSelector(eligible),
        config=SelectionBacktestConfig(
            holding_sessions=(1, 3, 5, 10, 20),
            signal_start_date=start,
            signal_end_date=end,
            cooldown_sessions=0,
            slippage_bps=5,
        ),
        progress_callback=lambda done, total, day: (
            print(f"pullback-select: {done}/{total} {day}", flush=True)
            if done == total or done % 20 == 0 else None
        ),
        scored_fields=SCORER_EXIT_FIELDS,
        cross_section_fields=THEME_CROSS_SECTION_FIELDS,
    )
    return _merge_pullback_research_tape(base_tape, pullback_tape), eligible


def _candidate_uses_threshold_matrix(args: Any) -> bool:
    """Return whether named candidates would invalidate a matrix analysis."""
    return any((
        args.stage_entry_analysis,
        args.reversal_shape_analysis,
        args.pullback_geometry_analysis,
        args.pullback_recovery_analysis,
    ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source", choices=("eastmoney", "tencent"), default="eastmoney")
    parser.add_argument(
        "--candidate",
        action="append",
        choices=tuple(sorted(CANDIDATES)),
        help=(
            "Run only the named pre-registered candidate. Repeat the option "
            "to compare a small, pre-specified set without replaying the full "
            "historical candidate catalog."
        ),
    )
    parser.add_argument(
        "--replay-cache",
        type=Path,
        help=(
            "Optional trusted JSONL.gz cache under /tmp. Existing caches skip "
            "network and cross-sectional scoring; missing caches are created."
        ),
    )
    parser.add_argument(
        "--source-replay-cache",
        type=Path,
        help=(
            "Trusted cache used to build a missing Round16 stage, Round17 "
            "or pullback cache without refetching daily bars."
        ),
    )
    parser.add_argument(
        "--stage-entry-analysis",
        action="store_true",
        help=(
            "Replay the isolated Round16 stage-threshold matrix. A missing "
            "wide cache can be built from --source-replay-cache."
        ),
    )
    parser.add_argument(
        "--reversal-shape-analysis",
        action="store_true",
        help=(
            "Replay only the isolated Round16 daily-V shape matrix against "
            "an explicit production-threshold cache."
        ),
    )
    parser.add_argument(
        "--pullback-geometry-analysis",
        action="store_true",
        help=(
            "Replay the isolated Round17 pullback-geometry matrix. A missing "
            "--replay-cache can be built from --source-replay-cache."
        ),
    )
    parser.add_argument(
        "--pullback-recovery-analysis",
        action="store_true",
        help=(
            "Replay the isolated Round18 mainline-recovery pullback matrix "
            "from an existing Round17 pullback cache."
        ),
    )
    phase = parser.add_mutually_exclusive_group()
    phase.add_argument(
        "--include-holdout",
        action="store_true",
        help="Open the reserved holdout only after a candidate has been frozen.",
    )
    phase.add_argument(
        "--post-freeze",
        action="store_true",
        help="Re-evaluate all windows after the holdout has already been opened.",
    )
    args = parser.parse_args()


    opens_holdout = args.include_holdout or args.post_freeze
    analysis_count = sum((
        args.stage_entry_analysis,
        args.reversal_shape_analysis,
        args.pullback_geometry_analysis,
        args.pullback_recovery_analysis,
    ))
    if analysis_count > 1:
        parser.error(
            "isolated analysis modes are mutually exclusive"
        )
    candidate_incompatible_analysis = _candidate_uses_threshold_matrix(args)
    if candidate_incompatible_analysis and args.candidate:
        parser.error(
            "--candidate cannot be combined with an isolated threshold matrix"
        )
    if analysis_count and opens_holdout:
        parser.error("isolated development analysis cannot open the reserved holdout")
    if analysis_count and args.replay_cache is None:
        parser.error("isolated analysis requires --replay-cache")
    if args.source_replay_cache is not None and not (
        args.stage_entry_analysis or args.pullback_geometry_analysis
    ):
        parser.error(
            "--source-replay-cache is only valid for stage-entry or pullback analysis"
        )
    windows = (
        {**DEVELOPMENT_WINDOWS, **HOLDOUT_WINDOWS}
        if args.post_freeze
        else HOLDOUT_WINDOWS if args.include_holdout else DEVELOPMENT_WINDOWS
    )
    earliest = min(start for start, _end in windows.values())
    latest = max(end for _start, end in windows.values())
    cache_path = args.replay_cache.expanduser().resolve() if args.replay_cache else None
    cache_loaded = bool(cache_path and cache_path.exists())
    source_cache_loaded = False
    if args.stage_entry_analysis and not cache_loaded:
        if args.source_replay_cache is None:
            parser.error(
                "a missing Round16 stage cache requires --source-replay-cache"
            )
        source_cache_path = args.source_replay_cache.expanduser().resolve()
        if not source_cache_path.exists():
            parser.error("--source-replay-cache does not exist")
        if cache_path == source_cache_path:
            parser.error("Round16 output cache must differ from its source cache")
        print(f"loading stage source cache: {source_cache_path}", flush=True)
        bars, _source_tape, source_metadata = _load_replay_cache(
            source_cache_path
        )
        cached_source = str(source_metadata.get("source") or "")
        cached_adjustment = str(source_metadata.get("adjustment") or "")
        if cached_source and cached_source != args.source:
            raise ValueError(
                f"source replay cache is {cached_source}, not {args.source}"
            )
        if cached_adjustment and cached_adjustment != "qfq":
            raise ValueError("source replay cache adjustment must be qfq")
        cached_start = str(
            source_metadata.get("signal_generation_start") or ""
        )
        cached_end = str(
            source_metadata.get("signal_generation_end") or ""
        )
        if (cached_start and earliest < cached_start) or (
            cached_end and latest > cached_end
        ):
            raise ValueError(
                "source replay cache does not cover the requested signal windows"
            )
        print("building Round16 low-floor stage tape", flush=True)
        tape, eligible = _build_round16_stage_tape(
            bars,
            source_metadata,
            start=earliest,
            end=latest,
        )
        cache_metadata = {
            **source_metadata,
            "parent_replay_cache": source_cache_path.name,
            "round16_thresholds": dict(ROUND16_STAGE_SOURCE_THRESHOLDS),
            "eligible_symbols_with_bars": len(eligible),
        }
        reference_count = int(
            cache_metadata.get("reference_symbol_count") or len(bars)
        )
        successful_count = int(
            cache_metadata.get("successful_symbol_count") or len(bars)
        )
        eligible_count = len(eligible)
        warnings = [
            str(item) for item in (cache_metadata.get("warnings") or ())
        ]
        print(f"writing Round16 stage replay cache: {cache_path}", flush=True)
        _write_replay_cache(
            cache_path,
            bars=bars,
            tape=tape,
            metadata=cache_metadata,
        )
        source_cache_loaded = True
    elif args.pullback_geometry_analysis and not cache_loaded:
        if args.source_replay_cache is None:
            parser.error(
                "a missing Round17 cache requires --source-replay-cache"
            )
        source_cache_path = args.source_replay_cache.expanduser().resolve()
        if not source_cache_path.exists():
            parser.error("--source-replay-cache does not exist")
        if cache_path == source_cache_path:
            parser.error("Round17 output cache must differ from its source cache")
        print(f"loading pullback source cache: {source_cache_path}", flush=True)
        bars, source_tape, source_metadata = _load_replay_cache(
            source_cache_path
        )
        cached_source = str(source_metadata.get("source") or "")
        cached_adjustment = str(source_metadata.get("adjustment") or "")
        if cached_source and cached_source != args.source:
            raise ValueError(
                f"source replay cache is {cached_source}, not {args.source}"
            )
        if cached_adjustment and cached_adjustment != "qfq":
            raise ValueError(
                "source replay cache adjustment must be qfq"
            )
        cached_start = str(
            source_metadata.get("signal_generation_start") or ""
        )
        cached_end = str(source_metadata.get("signal_generation_end") or "")
        if (cached_start and earliest < cached_start) or (
            cached_end and latest > cached_end
        ):
            raise ValueError(
                "source replay cache does not cover the requested signal windows"
            )
        print("building Round17 pullback union tape", flush=True)
        tape, eligible = _build_round17_pullback_tape(
            bars,
            source_tape,
            source_metadata,
            start=earliest,
            end=latest,
        )
        cache_metadata = {
            **source_metadata,
            "parent_replay_cache": source_cache_path.name,
            "round17_pullback_source_threshold": (
                ROUND17_PULLBACK_SOURCE_THRESHOLD
            ),
            "round17_pullback_variants": list(
                ROUND17_PULLBACK_VARIANT_IDS
            ),
            "eligible_symbols_with_bars": len(eligible),
        }
        reference_count = int(
            cache_metadata.get("reference_symbol_count") or len(bars)
        )
        successful_count = int(
            cache_metadata.get("successful_symbol_count") or len(bars)
        )
        eligible_count = len(eligible)
        warnings = [
            str(item) for item in (cache_metadata.get("warnings") or ())
        ]
        print(f"writing Round17 replay cache: {cache_path}", flush=True)
        _write_replay_cache(
            cache_path,
            bars=bars,
            tape=tape,
            metadata=cache_metadata,
        )
        source_cache_loaded = True
    elif cache_loaded:
        print(f"loading trusted replay cache: {cache_path}", flush=True)
        bars, tape, cache_metadata = _load_replay_cache(cache_path)
        cached_source = str(cache_metadata.get("source") or "")
        cached_adjustment = str(cache_metadata.get("adjustment") or "")
        if cached_source and cached_source != args.source:
            raise ValueError(
                f"replay cache source is {cached_source}, not {args.source}"
            )
        if cached_adjustment and cached_adjustment != "qfq":
            raise ValueError(
                f"replay cache adjustment is {cached_adjustment}, not qfq"
            )
        cached_start = str(cache_metadata.get("signal_generation_start") or "")
        cached_end = str(cache_metadata.get("signal_generation_end") or "")
        if (cached_start and earliest < cached_start) or (cached_end and latest > cached_end):
            raise ValueError(
                "replay cache does not cover the requested signal windows"
            )
        if args.stage_entry_analysis:
            _validate_stage_entry_cache(cache_metadata)
        if args.pullback_geometry_analysis:
            _validate_pullback_geometry_cache(cache_metadata)
        if args.pullback_recovery_analysis:
            _validate_pullback_geometry_cache(cache_metadata)
        reference_count = int(cache_metadata.get("reference_symbol_count") or 0)
        successful_count = int(cache_metadata.get("successful_symbol_count") or len(bars))
        eligible_count = _cached_eligible_symbol_count(cache_metadata)
        warnings = [str(item) for item in (cache_metadata.get("warnings") or ())]
    else:
        request = normalize_backtest_request({
            "strategy_id": "niuone",
            # Request normalization is used only to resolve the suite metadata and
            # data-source policy. The research tape below owns its wider, explicit
            # signal span instead of weakening the admin API's one-year cap.
            "start_date": "2026-01-01",
            "end_date": "2026-04-30",
            "adjustment": "qfq",
            "source": args.source,
        })
        print("building current listing universe", flush=True)
        universe = load_strategy_universe(request["strategy"])
        reference = tuple(universe["reference_symbols"])
        eligible = tuple(universe["eligible_symbols"])
        print(
            f"universe: reference={len(reference)} eligible={len(eligible)}",
            flush=True,
        )

        data = fetch_historical_data(
            reference,
            "2024-12-02",
            "2026-08-01",
            config=HistoricalFetchConfig(
                sources=(args.source,),
                adjustment="qfq",
                strict=False,
                minimum_rows=55,
                max_workers=16,
            ),
            progress_callback=_progress("fetch"),
        )
        print(f"coverage: {len(data.series)}/{len(reference)}", flush=True)
        _require_historical_coverage(
            data,
            reference_count=len(reference),
            source=args.source,
        )
        successful_count = len(data.series)
        raw_series = dict(data.series)
        data_failures = data.failures
        fetched_symbols = tuple(raw_series)
        del data
        bars, warnings, industry_quality = _annotated_bars(
            raw_series,
            data_failures,
            fetched_symbols,
            industry_by_symbol=None,
            industry_loader=load_current_industry_map,
            theme_by_symbol=None,
            theme_loader=load_current_theme_map,
            name_by_symbol=universe.get("name_by_symbol"),
        )
        selector = _selector_for_request(request, eligible_symbols=eligible)
        source_limits = dict(selector.max_signals_per_strategy_per_session)
        source_limits["niu_reversal_probe"] = 5
        selector.max_signals_per_strategy_per_session = MappingProxyType(source_limits)
        print("building selection replay tape", flush=True)
        tape = build_selection_replay_tape(
            bars,
            selector,
            config=SelectionBacktestConfig(
                holding_sessions=(1, 3, 5, 10, 20),
                signal_start_date=earliest,
                signal_end_date=latest,
                cooldown_sessions=0,
                slippage_bps=5,
            ),
            progress_callback=lambda done, total, day: (
                print(f"select: {done}/{total} {day}", flush=True)
                if done == total or done % 20 == 0 else None
            ),
            scored_fields=SCORER_EXIT_FIELDS,
            cross_section_fields=THEME_CROSS_SECTION_FIELDS,
        )
        reference_count = len(reference)
        eligible_count = len(eligible)
        if cache_path is not None:
            print(f"writing replay cache: {cache_path}", flush=True)
            _write_replay_cache(
                cache_path,
                bars=bars,
                tape=tape,
                metadata={
                    "signal_generation_start": earliest,
                    "signal_generation_end": latest,
                    "source": args.source,
                    "adjustment": "qfq",
                    "reference_symbol_count": reference_count,
                    "successful_symbol_count": successful_count,
                    "eligible_symbol_count": eligible_count,
                    "warnings": warnings,
                    "industry_annotation_quality": industry_quality.to_dict(),
                },
            )

    tape = _with_research_ranking_context(tape)
    baseline_filter = (
        ROUND16_STAGE_ENTRY_CANDIDATES["stage_production_thresholds"][
            "signal_filter"
        ]
        if args.stage_entry_analysis
        else ROUND17_PULLBACK_CANDIDATES["pullback_production_geometry"][
            "signal_filter"
        ]
        if args.pullback_geometry_analysis or args.pullback_recovery_analysis
        else _production_stage_filter
    )

    output: dict[str, Any] = {
        "scope": {
            "research_phase": (
                "stage_entry_development" if args.stage_entry_analysis
                else "reversal_shape_development"
                if args.reversal_shape_analysis
                else "pullback_geometry_development"
                if args.pullback_geometry_analysis
                else "pullback_recovery_development"
                if args.pullback_recovery_analysis

                else "post_freeze" if args.post_freeze
                else "holdout" if args.include_holdout else "development"
            ),
            "signal_generation_end": latest,
            "reserved_holdout_start": "2026-06-30",
            "source": args.source,
            "adjustment": "qfq",
            "reference_symbol_count": reference_count,
            "successful_symbol_count": successful_count,
            "eligible_symbol_count": eligible_count,
            "replay_cache_loaded": cache_loaded,
            "source_replay_cache_loaded": source_cache_loaded,
            "stage_entry_analysis": args.stage_entry_analysis,
            "reversal_shape_analysis": args.reversal_shape_analysis,
            "pullback_geometry_analysis": args.pullback_geometry_analysis,
            "pullback_recovery_analysis": args.pullback_recovery_analysis,
        },
        "warnings": [*warnings, *(_plain(tape.diagnostics).get("warnings") or [])],
        "selection_diagnostics": _plain(tape.diagnostics),
        "windows": {},
        "candidates": {},
    }
    for name, (start, end) in windows.items():
        print(f"replay: {name} {start}..{end}", flush=True)
        _result, summary = _run_window(
            bars,
            tape,
            start,
            end,
            signal_filter=baseline_filter,
        )
        output["windows"][name] = summary
    development_aggregate = _development_aggregate(output["windows"])
    if development_aggregate is not None:
        output["development_aggregate"] = development_aggregate
        development_features = _development_completed_features(
            output["windows"]
        )
        output["development_stage_trajectory"] = _stage_trajectory_summary(
            development_features
        )
    combined_features = _development_completed_features(output["windows"])
    if combined_features:
        output["combined_development_reversal_feature_groups"] = _feature_groups(
            combined_features
        )
    candidates = (
        ROUND16_STAGE_ENTRY_CANDIDATES
        if args.stage_entry_analysis
        else ROUND17_PULLBACK_CANDIDATES
        if args.pullback_geometry_analysis
        else ROUND18_PULLBACK_RECOVERY_CANDIDATES
        if args.pullback_recovery_analysis
        else {
            "frozen_production_default": CANDIDATES[
                "frozen_production_default"
            ],
            **ROUND16_REVERSAL_SHAPE_CANDIDATES,
        }
        if args.reversal_shape_analysis
        else {"frozen_production_default": CANDIDATES["frozen_production_default"]}
        if opens_holdout else CANDIDATES
    )
    if args.candidate:
        candidates = {
            name: CANDIDATES[name]
            for name in dict.fromkeys(args.candidate)
        }
    for candidate_name, candidate in candidates.items():
        print(f"candidate: {candidate_name}", flush=True)
        candidate_windows: dict[str, Any] = {}
        for window_name, (start, end) in windows.items():
            _result, summary = _run_window(
                bars,
                tape,
                start,
                end,
                signal_filter=candidate.get("signal_filter"),
                policy_options=candidate.get("policy_options"),
                signal_order=candidate.get("signal_order"),
                holding_upgrade_mode=candidate.get("holding_upgrade_mode"),
                holding_upgrade_min_pnl_pct=candidate.get(
                    "holding_upgrade_min_pnl_pct"
                ),
                holding_upgrade_max_pnl_pct=candidate.get(
                    "holding_upgrade_max_pnl_pct"
                ),
                reversal_signals_per_session=int(
                    candidate.get("reversal_signals_per_session")
                    or PRODUCTION_REVERSAL_SIGNALS_PER_SESSION
                ),
            )
            candidate_windows[window_name] = {
                "statistics": summary["statistics"],
                "portfolio": summary["portfolio"],
                "lifecycle": summary["lifecycle"],
                "completed_trade_features": summary["completed_trade_features"],
            }
        output["candidates"][candidate_name] = {
            "definition": {
                "policy_options": dict(candidate.get("policy_options") or {}),
                "uses_signal_filter": candidate.get("signal_filter") is not None,
                "filter_options": dict(candidate.get("filter_options") or {}),
                "signal_order": list(candidate.get("signal_order") or ()),
                "holding_upgrade_mode": candidate.get("holding_upgrade_mode"),
                "holding_upgrade_min_pnl_pct": candidate.get(
                    "holding_upgrade_min_pnl_pct"
                ),
                "holding_upgrade_max_pnl_pct": candidate.get(
                    "holding_upgrade_max_pnl_pct"
                ),
                "reversal_signals_per_session": int(
                    candidate.get("reversal_signals_per_session")
                    or PRODUCTION_REVERSAL_SIGNALS_PER_SESSION
                ),
                "research_status": candidate.get("research_status"),
            },
            "windows": candidate_windows,
            "development_aggregate": _development_aggregate(candidate_windows),
        }

    target = (
        args.output
        or (
            Path("/tmp/niuone-walk-forward-holdout.json")
            if args.include_holdout
            else Path("/tmp/niuone-walk-forward-post-freeze.json")
            if args.post_freeze else DEFAULT_OUTPUT
        )
    ).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {target}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
