"""Strict-forward evaluation for completed NiuOne paper-trading lifecycles."""
from __future__ import annotations

import math
import json
import sqlite3
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.strategies.scoring.niuone import NIUONE_STRATEGY_IDS
from app.strategies.lifecycle import (
    NIUONE_LIFECYCLE_STAGES,
    niuone_lifecycle_entry_blocker,
)
from app.strategies.niuone_risk import (
    NIUONE_ABSOLUTE_POSITION_CAP_PCT,
    NIUONE_MARKUP_EARLY_UPGRADE_POSITION_CAP_PCT,
    NIUONE_MARKUP_REBALANCE_MIN_SESSIONS_AFTER_ADD,
    NIUONE_MARKUP_REBALANCE_PULLBACK_ATR,
    NIUONE_MARKUP_REBALANCE_REBOUND_ATR,
    NIUONE_MARKUP_REBALANCE_STALL_MIN_ATR,
    NIUONE_MARKUP_REBALANCE_STALL_SESSIONS,
    NIUONE_MARKUP_REBALANCE_TRIM_RATIO,
    NIUONE_MARKUP_UPGRADE_MAX_PNL_PCT,
    NIUONE_MARKUP_UPGRADE_MIN_PNL_PCT,
    NIUONE_MARKUP_UPGRADE_POSITION_CAP_PCT,
    NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY,
)
from app.strategies.exits import (
    NIUONE_LIFECYCLE_CLIMAX_MIN_PNL_PCT,
    NIUONE_LIFECYCLE_CLIMAX_PARTIAL_RATIO,
)
from app.strategies.policy import (
    NIUONE_DAILY_V_MAX_RECOVERY_RATIO,
    NIUONE_DAILY_V_MIN_RECOVERY_RATIO,
    NIUONE_LEADER_MIN_SECTOR_RANK,
    NIUONE_REVERSAL_CONTINUATION_MIN_STATE_STREAK,
    NIUONE_REVERSAL_CONTINUATION_MIN_STRONG_COUNT,
    NIUONE_TODAY_OBSERVATION_THRESHOLD,
)
from app.strategies.selection import strategy_daily_candidate_limit


DEFAULT_COHORT_START = "2026-08-13"
DEFAULT_MIN_COMPLETED_TRADES = 30
DEFAULT_MIN_CALENDAR_MONTHS = 3
DEFAULT_SHADOW_EXECUTION_GAP_PCT = 1.0
DEFAULT_SHADOW_RECOVERY_RATIO_CAP = 2.0
DEFAULT_HISTORICAL_REFERENCE_WIN_RATE_PCT = 59.71
DEFAULT_WIN_RATE_CONFIDENCE_LEVEL = 0.95
DEFAULT_MAX_PORTFOLIO_DRAWDOWN_PCT = 6.0
DEFAULT_MIN_RETURN_TO_DRAWDOWN_RATIO = 1.0
FORWARD_PROTOCOL_VERSION = "niuone-strict-forward-v33"
FORWARD_PERFORMANCE_CLUSTER_UNIT = "entry_date_x_entry_theme"
FORWARD_SHADOW_CANDIDATES = {
    "execution_gap": "round13_execution_gap_le_1pct",
}
FORWARD_REQUIRED_ENTRY_CONTEXT_FIELDS = (
    "entry_niuone_lifecycle_stage",
    "entry_niuone_lifecycle_label",
    "entry_niuone_lifecycle_order",
    "entry_niuone_lifecycle_entry_policy",
    "entry_mainline_state",
    "entry_signal_score",
    "entry_stock_activity_score",
    "entry_stock_market_amount_percentile",
    "entry_stock_theme_amount_percentile",
    "entry_stock_activity_confirmed",
    "entry_same_stage_candidate_rank",
    "entry_execution_gap_pct",
    "entry_daily_v_recovery_ratio",
    "entry_signal_generated_at",
    "entry_schedule_run_kind",
    "entry_schedule_triggered_at",
    "entry_execution_mode",
    "entry_industry",
    "entry_theme",
    "entry_theme_basis",
    "entry_theme_attribution_score",
    "entry_theme_attribution_weight",
    "entry_theme_historical_prior_score",
    "entry_theme_cohort_alignment_score",
    "entry_theme_peer_resonance_score",
    "entry_theme_return_correlation_score",
    "entry_theme_return_correlation_rank_score",
    "entry_theme_return_correlation_observation_count",
    "entry_theme_return_correlation_peer_count",
    "entry_theme_specificity_score",
    "entry_theme_membership_source",
    "entry_theme_unattributed_weight",
    "entry_model_requested_shares",
    "entry_executed_shares",
    "entry_maximum_permitted_shares",
    "entry_risk_ceiling_utilization_pct",
    "entry_risk_ceiling_binding_constraints",
    "entry_risk_ceiling_auto_reduced",
)
FORWARD_HOLDING_LIFECYCLE_EVIDENCE_SCHEMA_VERSION = 1
FORWARD_REQUIRED_EXIT_CONTEXT_FIELDS = (
    "schema_version",
    "path_complete_from_entry",
    "exit_niuone_lifecycle_stage",
    "exit_niuone_lifecycle_label",
    "exit_niuone_lifecycle_order",
    "exit_niuone_lifecycle_entry_policy",
    "stage_sequence",
    "transition_count",
    "reached_markup",
    "reached_climax",
    "reached_divergence",
    "reached_fade",
    "path",
)
FORWARD_SCHEDULED_RUN_KINDS = frozenset({"scheduled", "catchup"})
FORWARD_ALLOWED_RUN_KINDS = frozenset({*FORWARD_SCHEDULED_RUN_KINDS, "manual"})
FORWARD_ALLOWED_EXECUTION_MODES = frozenset({"direct", "deferred"})
FORWARD_CONDITIONAL_ENTRY_CONTEXT_RULES = {
    "entry_schedule_slot": (
        "required_when_entry_schedule_run_kind_is_scheduled_or_catchup"
    ),
}
FORWARD_REQUIRED_OPERATING_DAY_EVENTS = (
    "protocol_preflight_before_first_decision",
    "all_configured_practice_schedule_slots_ok",
    "durable_decision_evidence_for_all_practice_slots",
    "opening_exit_check_ok",
    "closing_exit_check_ok",
    "closing_equity_snapshot_ok",
    "post_close_forward_evaluation_ok",
)
FORWARD_CANDIDATE_EVIDENCE_SCHEMA_VERSION = 2
FORWARD_EXECUTION_EVIDENCE_SCHEMA_VERSION = 2
FORWARD_SELL_EXECUTION_EVIDENCE_SCHEMA_VERSION = 1
FORWARD_REQUIRED_EXECUTED_BUY_SIZING_FIELDS = (
    "model_requested_shares",
    "maximum_permitted_shares",
    "risk_ceiling_utilization_pct",
    "risk_ceiling_binding_constraints",
    "position_opened",
    "risk_ceiling_auto_reduced",
)
FORWARD_REQUIRED_EXECUTED_SELL_SIZING_FIELDS = (
    "sell_execution_source",
    "model_requested_sell_shares",
    "available_sell_shares",
    "sell_quantity_auto_reduced",
)
FORWARD_REQUIRED_CANDIDATE_EVIDENCE_FIELDS = (
    "code",
    "strategy_id",
    "observed_rank",
    "eligible_for_decision",
    "eligibility_blockers",
)


def _trade_identity(trade: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the same stable fill identity used by the practice ledger."""
    return tuple(
        json.dumps(trade.get(field, ""), ensure_ascii=False, sort_keys=True)
        for field in ("time", "action", "code", "shares", "price", "reason")
    )


def merge_forward_trade_rows(
    *sources: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Merge persisted trade sources without duplicating simulated fills."""
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    duplicate_count = 0
    for source in sources:
        for row in source:
            if not isinstance(row, Mapping):
                continue
            materialized = dict(row)
            identity = _trade_identity(materialized)
            if identity in seen:
                duplicate_count += 1
                continue
            seen.add(identity)
            merged.append(materialized)
    return merged, duplicate_count


def _legacy_db_trade(columns: list[str], values: tuple[Any, ...]) -> dict[str, Any]:
    row = dict(zip(columns, values))
    fee = sum(
        _number(row.get(name)) or 0.0
        for name in ("commission", "transfer_fee", "stamp_duty")
    )
    action = str(row.get("action") or "").upper()
    materialized = {
        key: row.get(key)
        for key in ("time", "action", "code", "name", "shares", "price", "amount", "reason")
    }
    materialized["fee"] = fee
    if action == "BUY":
        materialized["total_cost"] = (_number(row.get("amount")) or 0.0) + fee
    elif action == "SELL":
        materialized["net_proceeds"] = (_number(row.get("amount")) or 0.0) - fee
    materialized["_forward_payload_available"] = False
    return materialized


def load_niuone_forward_trades_from_db(
    path: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Read the durable practice ledger without opening the live DB for writes."""
    db_path = Path(path).expanduser().resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"practice database does not exist: {db_path}")
    uri = f"{db_path.as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'"
        ).fetchone()
        if table is None:
            raise ValueError("practice database does not contain a trades table")
        columns = [
            str(item[1])
            for item in connection.execute("PRAGMA table_info(trades)").fetchall()
        ]
        selected = [
            name
            for name in (
                "time", "action", "code", "name", "shares", "price", "amount",
                "commission", "transfer_fee", "stamp_duty", "reason", "payload_json",
            )
            if name in columns
        ]
        if not {"time", "action", "code", "shares", "amount"}.issubset(selected):
            raise ValueError("practice trades table is missing required columns")
        rows = connection.execute(
            f"SELECT {', '.join(selected)} FROM trades ORDER BY time, id"
        ).fetchall()

    materialized: list[dict[str, Any]] = []
    rich_payload_count = 0
    legacy_payload_count = 0
    payload_index = selected.index("payload_json") if "payload_json" in selected else None
    for values in rows:
        payload: dict[str, Any] | None = None
        if payload_index is not None:
            raw_payload = values[payload_index]
            if raw_payload:
                try:
                    parsed = json.loads(str(raw_payload))
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    payload = parsed
        if payload is not None:
            payload["_forward_payload_available"] = True
            materialized.append(payload)
            rich_payload_count += 1
        else:
            materialized.append(_legacy_db_trade(selected, values))
            legacy_payload_count += 1
    return materialized, {
        "database_trade_row_count": len(materialized),
        "rich_payload_trade_row_count": rich_payload_count,
        "legacy_payload_trade_row_count": legacy_payload_count,
    }


def load_niuone_forward_decisions_from_db(
    path: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Read durable Practice opportunity-set evidence without DB writes."""
    db_path = Path(path).expanduser().resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"practice database does not exist: {db_path}")
    uri = f"{db_path.as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='decisions'"
        ).fetchone()
        if table is None:
            return [], {
                "database_decision_row_count": 0,
                "rich_payload_decision_row_count": 0,
                "legacy_payload_decision_row_count": 0,
            }
        columns = [
            str(item[1])
            for item in connection.execute("PRAGMA table_info(decisions)").fetchall()
        ]
        selected = [
            name
            for name in (
                "time", "b1_generated_at", "schedule_slot",
                "schedule_run_kind", "payload_json",
            )
            if name in columns
        ]
        if "time" not in selected:
            raise ValueError("practice decisions table is missing required columns")
        order_column = "id" if "id" in columns else "rowid"
        rows = connection.execute(
            f"SELECT {', '.join(selected)} FROM decisions ORDER BY {order_column}"
        ).fetchall()

    materialized: list[dict[str, Any]] = []
    rich_payload_count = 0
    legacy_payload_count = 0
    payload_index = (
        selected.index("payload_json") if "payload_json" in selected else None
    )
    for values in rows:
        payload: dict[str, Any] | None = None
        if payload_index is not None and values[payload_index]:
            try:
                parsed = json.loads(str(values[payload_index]))
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                payload = parsed
        if payload is not None:
            payload["_forward_payload_available"] = True
            materialized.append(payload)
            rich_payload_count += 1
            continue
        legacy = dict(zip(selected, values))
        legacy["_forward_payload_available"] = False
        materialized.append(legacy)
        legacy_payload_count += 1
    return materialized, {
        "database_decision_row_count": len(materialized),
        "rich_payload_decision_row_count": rich_payload_count,
        "legacy_payload_decision_row_count": legacy_payload_count,
    }


def load_niuone_forward_daily_equity_from_db(
    path: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Read durable daily mark-to-market account points without DB writes."""
    db_path = Path(path).expanduser().resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"practice database does not exist: {db_path}")
    uri = f"{db_path.as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='daily_equity'"
        ).fetchone()
        if table is None:
            return [], {"database_daily_equity_row_count": 0}
        columns = [
            str(item[1])
            for item in connection.execute(
                "PRAGMA table_info(daily_equity)"
            ).fetchall()
        ]
        required = {
            "date", "equity", "cash", "market_value", "pnl_pct",
            "created_at",
        }
        if not required.issubset(columns):
            raise ValueError(
                "practice daily_equity table is missing required columns"
            )
        has_account_created_at = "account_created_at" in columns
        selected_account_created_at = (
            "account_created_at" if has_account_created_at else "''"
        )
        rows = connection.execute(
            "SELECT date, equity, cash, market_value, pnl_pct, "
            f"{selected_account_created_at}, created_at "
            "FROM daily_equity ORDER BY date"
        ).fetchall()
    materialized = [{
        "date": values[0],
        "equity": values[1],
        "cash": values[2],
        "market_value": values[3],
        "pnl_pct": values[4],
        "account_created_at": values[5],
        "created_at": values[6],
        "_forward_payload_available": True,
    } for values in rows]
    return materialized, {
        "database_daily_equity_row_count": len(materialized),
    }


def _candidate_strategy_id(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("strategy_id") or "").strip()


def decision_candidate_evidence_gaps(
    decision_row: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return stable schema gaps for one observed-opportunity payload."""
    gaps: set[str] = set()
    if (
        decision_row.get("candidate_evidence_schema_version")
        != FORWARD_CANDIDATE_EVIDENCE_SCHEMA_VERSION
    ):
        gaps.add("candidate_evidence_schema_version")
    candidates = decision_row.get("candidate_evidence")
    if not isinstance(candidates, list):
        return tuple(sorted({*gaps, "candidate_evidence"}))
    candidate_codes: set[str] = set()
    observed_ranks: set[int] = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            gaps.add("candidate_evidence.record")
            continue
        code = str(candidate.get("code") or "").strip()
        if not code:
            gaps.add("candidate_evidence.code")
        elif code in candidate_codes:
            gaps.add("candidate_evidence.duplicate_code")
        else:
            candidate_codes.add(code)
        strategy_id = _candidate_strategy_id(candidate)
        if not strategy_id:
            gaps.add("candidate_evidence.strategy_id")
        observed_rank = _number(candidate.get("observed_rank"))
        if (
            observed_rank is None
            or observed_rank <= 0
            or not float(observed_rank).is_integer()
        ):
            gaps.add("candidate_evidence.observed_rank")
        elif int(observed_rank) in observed_ranks:
            gaps.add("candidate_evidence.duplicate_observed_rank")
        else:
            observed_ranks.add(int(observed_rank))
        if not isinstance(candidate.get("eligible_for_decision"), bool):
            gaps.add("candidate_evidence.eligible_for_decision")
        blockers = candidate.get("eligibility_blockers")
        if not isinstance(blockers, list) or any(
            not isinstance(value, str) for value in blockers
        ):
            gaps.add("candidate_evidence.eligibility_blockers")
        elif (
            candidate.get("eligible_for_decision") is True and blockers
        ) or (
            candidate.get("eligible_for_decision") is False and not blockers
        ):
            gaps.add("candidate_evidence.eligibility_consistency")
        if strategy_id in NIUONE_STRATEGY_IDS:
            stage = str(candidate.get("niuone_lifecycle_stage") or "")
            if stage not in NIUONE_LIFECYCLE_STAGES:
                gaps.add("candidate_evidence.niuone_lifecycle_stage")
            elif (
                candidate.get("eligible_for_decision") is True
                and niuone_lifecycle_entry_blocker(
                    strategy_id,
                    candidate,
                )
            ):
                gaps.add(
                    "candidate_evidence.niuone_lifecycle_strategy_route"
                )
            score = _number(
                candidate.get("best_score")
                if candidate.get("best_score") is not None
                else candidate.get("score")
            )
            if score is None:
                gaps.add("candidate_evidence.niuone_score")
            for field in (
                "stock_activity_score",
                "stock_market_amount_percentile",
                "stock_theme_amount_percentile",
            ):
                value = _number(candidate.get(field))
                if value is None or value < 0 or value > 100:
                    gaps.add(f"candidate_evidence.{field}")
            if not isinstance(
                candidate.get("stock_activity_data_available"),
                bool,
            ):
                gaps.add("candidate_evidence.stock_activity_data_available")
            activity_confirmed = candidate.get("stock_activity_confirmed")
            if not isinstance(activity_confirmed, bool):
                gaps.add("candidate_evidence.stock_activity_confirmed")
            elif (
                candidate.get("eligible_for_decision") is True
                and strategy_id
                in {"niu_leader", "niu_pullback", "niu_emerging"}
                and activity_confirmed is not True
            ):
                gaps.add("candidate_evidence.stock_activity_consistency")
    return tuple(sorted(gaps))


def decision_has_durable_candidate_evidence(
    decision_row: Mapping[str, Any],
) -> bool:
    """Return whether one decision contains a fully inspectable opportunity set."""
    return not decision_candidate_evidence_gaps(decision_row)


def _decision_cycle_date(decision_row: Mapping[str, Any]) -> date | None:
    for field in ("schedule_slot", "b1_generated_at", "time"):
        try:
            return _date_value(
                decision_row.get(field),
                field_name=f"decision {field}",
            )
        except ValueError:
            continue
    return None


def _decision_cycle_key(
    decision_row: Mapping[str, Any],
    index: int,
) -> str:
    run_kind = str(decision_row.get("schedule_run_kind") or "").strip()
    slot = str(decision_row.get("schedule_slot") or "").strip()[:16]
    if run_kind in FORWARD_SCHEDULED_RUN_KINDS and slot:
        return f"scheduled:{slot}"
    generated_at = str(
        decision_row.get("b1_generated_at")
        or decision_row.get("time")
        or ""
    ).strip()
    return f"{run_kind or 'manual'}:{generated_at or index}"


def _summarize_niuone_sell_execution(
    rows: Iterable[Mapping[str, Any]],
    *,
    cohort_start: date,
    as_of: date,
) -> dict[str, Any]:
    """Audit durable model-directed NiuOne SELL quantity reductions."""
    model_sell_fill_count = 0
    automatic_sell_fill_count = 0
    auto_reduced_sell_fill_count = 0
    requested_share_count = 0
    executed_share_count = 0
    auto_reduced_share_count = 0
    invalid_fill_count = 0
    invalid_field_counts: dict[str, int] = defaultdict(int)
    seen: set[tuple[str, ...]] = set()
    evidence_fields = {
        "sell_execution_evidence_schema_version",
        *FORWARD_REQUIRED_EXECUTED_SELL_SIZING_FIELDS,
    }

    for fill in rows:
        if (
            not isinstance(fill, Mapping)
            or str(fill.get("action") or "").upper() != "SELL"
            or _strategy_id(fill) not in NIUONE_STRATEGY_IDS
        ):
            continue
        identity = _trade_identity(fill)
        if identity in seen:
            continue
        seen.add(identity)
        has_model_evidence = any(field in fill for field in evidence_fields)
        fill_date = _decision_cycle_date({"time": fill.get("time")})
        if fill_date is None:
            if has_model_evidence:
                invalid_fill_count += 1
                invalid_field_counts["durable_sell_fill.time"] += 1
            continue
        if fill_date < cohort_start or fill_date > as_of:
            continue
        if not has_model_evidence:
            automatic_sell_fill_count += 1
            continue

        model_sell_fill_count += 1
        gaps: set[str] = set()
        requested = _shares(fill.get("model_requested_sell_shares"))
        available = _optional_quantity(fill.get("available_sell_shares"))
        executed = _shares(fill.get("shares"))
        auto_reduced = fill.get("sell_quantity_auto_reduced")

        if fill.get("_forward_payload_available") is not True:
            gaps.add("durable_sell_fill.payload")
        if (
            fill.get("sell_execution_evidence_schema_version")
            != FORWARD_SELL_EXECUTION_EVIDENCE_SCHEMA_VERSION
        ):
            gaps.add("durable_sell_fill.schema_version")
        if fill.get("sell_execution_source") != "model_action":
            gaps.add("durable_sell_fill.sell_execution_source")
        if requested <= 0 or requested % 100:
            gaps.add("durable_sell_fill.model_requested_sell_shares")
        if available is None or available <= 0:
            gaps.add("durable_sell_fill.available_sell_shares")
        if executed <= 0 or executed % 100:
            gaps.add("durable_sell_fill.shares")
        if (
            available is not None
            and available > 0
            and executed > available
        ):
            gaps.add("durable_sell_fill.shares")
        if not isinstance(auto_reduced, bool):
            gaps.add("durable_sell_fill.sell_quantity_auto_reduced")
        elif auto_reduced:
            if not (
                requested > (available or 0) > 0
                and available % 100 == 0
                and executed == available
            ):
                gaps.add("durable_sell_fill.sell_quantity_auto_reduced")
        elif requested != executed:
            gaps.add("durable_sell_fill.sell_quantity_auto_reduced")

        requested_share_count += requested
        executed_share_count += executed
        if auto_reduced is True and not gaps:
            auto_reduced_sell_fill_count += 1
            auto_reduced_share_count += requested - executed
        if gaps:
            invalid_fill_count += 1
            for field in gaps:
                invalid_field_counts[field] += 1

    return {
        "unit_of_analysis": "deduplicated_durable_model_niuone_sell_fill",
        "schema_version": FORWARD_SELL_EXECUTION_EVIDENCE_SCHEMA_VERSION,
        "model_sell_fill_count": model_sell_fill_count,
        "automatic_sell_fill_count": automatic_sell_fill_count,
        "auto_reduced_sell_fill_count": auto_reduced_sell_fill_count,
        "requested_share_count": requested_share_count,
        "executed_share_count": executed_share_count,
        "auto_reduced_share_count": auto_reduced_share_count,
        "invalid_sell_execution_fill_count": invalid_fill_count,
        "invalid_sell_execution_evidence_fields": dict(
            sorted(invalid_field_counts.items())
        ),
        "sell_execution_data_quality_gate_met": invalid_fill_count == 0,
    }


def summarize_niuone_forward_opportunities(
    decision_rows: Iterable[Mapping[str, Any]],
    *,
    execution_rows: Iterable[Mapping[str, Any]] | None = None,
    cohort_start: str | date = DEFAULT_COHORT_START,
    as_of: str | date | None = None,
) -> dict[str, Any]:
    """Profile the observed-to-filled NiuOne funnel at decision-cycle grain."""
    start = _date_value(cohort_start, field_name="cohort_start")
    cutoff = _date_value(as_of or date.today(), field_name="as_of")
    materialized_execution_rows = (
        list(execution_rows) if execution_rows is not None else []
    )
    sell_execution = _summarize_niuone_sell_execution(
        materialized_execution_rows,
        cohort_start=start,
        as_of=cutoff,
    )
    cycles: dict[str, tuple[int, Mapping[str, Any]]] = {}
    duplicate_cycle_count = 0
    invalid_timestamp_count = 0
    ignored_non_opportunity_decision_count = 0
    for index, row in enumerate(decision_rows):
        if not isinstance(row, Mapping):
            invalid_timestamp_count += 1
            continue
        if not (
            str(row.get("b1_generated_at") or "").strip()
            or str(row.get("schedule_run_kind") or "").strip()
            in FORWARD_ALLOWED_RUN_KINDS
            or "candidate_evidence" in row
            or "candidate_evidence_schema_version" in row
        ):
            ignored_non_opportunity_decision_count += 1
            continue
        cycle_date = _decision_cycle_date(row)
        if cycle_date is None:
            invalid_timestamp_count += 1
            continue
        if cycle_date < start or cycle_date > cutoff:
            continue
        key = _decision_cycle_key(row, index)
        if key in cycles:
            duplicate_cycle_count += 1
        cycles[key] = (index, row)

    execution_codes_by_cycle: dict[str, set[str]] = defaultdict(set)
    execution_fills_by_cycle: dict[
        str,
        dict[str, list[Mapping[str, Any]]],
    ] = defaultdict(lambda: defaultdict(list))
    non_durable_buy_fill_count = 0
    unmapped_durable_buy_fill_count = 0
    seen_execution_ids: set[tuple[str, ...]] = set()
    if execution_rows is not None:
        for fill in materialized_execution_rows:
            if not isinstance(fill, Mapping):
                continue
            if str(fill.get("action") or "").upper() != "BUY":
                continue
            identity = _trade_identity(fill)
            if identity in seen_execution_ids:
                continue
            seen_execution_ids.add(identity)
            fill_date = _decision_cycle_date({"time": fill.get("time")})
            if fill_date is None or fill_date < start or fill_date > cutoff:
                continue
            if fill.get("_forward_payload_available") is not True:
                non_durable_buy_fill_count += 1
                continue
            context = fill.get("niuone_entry_context")
            context = context if isinstance(context, Mapping) else {}
            run_kind = str(
                context.get("entry_schedule_run_kind") or "manual"
            ).strip()
            slot = str(context.get("entry_schedule_slot") or "").strip()[:16]
            generated_at = str(
                context.get("entry_signal_generated_at") or ""
            ).strip()
            if run_kind in FORWARD_SCHEDULED_RUN_KINDS and slot:
                cycle_key = f"scheduled:{slot}"
            elif generated_at:
                cycle_key = f"{run_kind or 'manual'}:{generated_at}"
            else:
                unmapped_durable_buy_fill_count += 1
                continue
            code = str(fill.get("code") or "").strip()
            if code:
                execution_codes_by_cycle[cycle_key].add(code)
                execution_fills_by_cycle[cycle_key][code].append(fill)
            else:
                unmapped_durable_buy_fill_count += 1

    stage_funnel = {
        stage: {
            "label": str(metadata.get("label") or stage),
            "order": int(metadata.get("order") or 0),
            "observed_candidate_count": 0,
            "eligible_candidate_count": 0,
            "model_buy_candidate_count": 0,
            "executed_buy_candidate_count": 0,
        }
        for stage, metadata in sorted(
            NIUONE_LIFECYCLE_STAGES.items(),
            key=lambda item: int(item[1].get("order") or 0),
        )
    }
    observed_count = 0
    eligible_count = 0
    model_buy_count = 0
    executed_buy_count = 0
    zero_candidate_cycle_count = 0
    valid_cycle_count = 0
    invalid_cycle_count = 0
    invalid_field_counts: dict[str, int] = defaultdict(int)
    model_buy_without_eligibility_count = 0
    executed_buy_without_model_count = 0
    unmatched_model_buy_code_count = 0
    unmatched_executed_buy_code_count = 0
    decision_executed_without_durable_fill_count = 0
    durable_fill_without_decision_executed_count = 0
    sizing_records: list[dict[str, Any]] = []
    invalid_sizing_field_counts: dict[str, int] = defaultdict(int)
    rejection_category_counts: dict[str, int] = defaultdict(int)
    duplicate_model_buy_action_count = 0

    for cycle_key, (_index, row) in sorted(
        cycles.items(),
        key=lambda item: item[1][0],
    ):
        gaps = decision_candidate_evidence_gaps(row)
        if row.get("_forward_payload_available") is not True:
            gaps = tuple(sorted({*gaps, "durable_decision_payload"}))
        decision = row.get("decision")
        if not isinstance(decision, Mapping):
            gaps = tuple(sorted({*gaps, "decision_payload"}))
        else:
            if decision.get("error"):
                gaps = tuple(sorted({*gaps, "decision_error"}))
            if not isinstance(decision.get("actions"), list):
                gaps = tuple(sorted({*gaps, "decision_actions"}))
        if not isinstance(row.get("executed"), list):
            gaps = tuple(sorted({*gaps, "executed_actions"}))
        if gaps:
            invalid_cycle_count += 1
            for field in gaps:
                invalid_field_counts[field] += 1
            continue
        valid_cycle_count += 1
        candidates = [
            candidate
            for candidate in row.get("candidate_evidence") or []
            if isinstance(candidate, Mapping)
        ]
        candidate_codes = {
            str(candidate.get("code") or "").strip()
            for candidate in candidates
        }
        decision = row.get("decision")
        decision = decision if isinstance(decision, Mapping) else {}
        model_buy_actions_by_code: dict[
            str,
            list[Mapping[str, Any]],
        ] = defaultdict(list)
        for action in decision.get("actions") or []:
            if (
                isinstance(action, Mapping)
                and str(action.get("action") or "").upper() == "BUY"
            ):
                action_code = str(action.get("code") or "").strip()
                if action_code:
                    model_buy_actions_by_code[action_code].append(action)
        model_buy_codes = set(model_buy_actions_by_code)
        structured_blocks = decision.get("execution_blocks")
        blocks_by_code: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        if isinstance(structured_blocks, list):
            for block in structured_blocks:
                if isinstance(block, Mapping):
                    block_code = str(block.get("code") or "").strip()
                    if block_code:
                        blocks_by_code[block_code].append(block)
        decision_executed_buy_codes = {
            str(fill.get("code") or "").strip()
            for fill in row.get("executed") or []
            if isinstance(fill, Mapping)
            and str(fill.get("action") or "").upper() == "BUY"
            and str(fill.get("code") or "").strip()
        }
        executed_buy_codes = (
            set(execution_codes_by_cycle.get(cycle_key) or ())
            if execution_rows is not None
            else decision_executed_buy_codes
        )
        if execution_rows is not None:
            decision_executed_without_durable_fill_count += len(
                decision_executed_buy_codes - executed_buy_codes
            )
            durable_fill_without_decision_executed_count += len(
                executed_buy_codes - decision_executed_buy_codes
            )
        unmatched_model_buy_code_count += len(
            model_buy_codes - candidate_codes
        )
        unmatched_executed_buy_code_count += len(
            executed_buy_codes - candidate_codes
        )
        cycle_niuone_count = 0
        for candidate in candidates:
            if _candidate_strategy_id(candidate) not in NIUONE_STRATEGY_IDS:
                continue
            cycle_niuone_count += 1
            observed_count += 1
            code = str(candidate.get("code") or "").strip()
            eligible = candidate.get("eligible_for_decision") is True
            model_buy = code in model_buy_codes
            executed_buy = code in executed_buy_codes
            if eligible:
                eligible_count += 1
            if model_buy and eligible:
                model_buy_count += 1
            if model_buy and not eligible:
                model_buy_without_eligibility_count += 1
            if executed_buy and model_buy and eligible:
                executed_buy_count += 1
            if executed_buy and not model_buy:
                executed_buy_without_model_count += 1
            stage = str(candidate.get("niuone_lifecycle_stage") or "")
            stage_bucket = stage_funnel[stage]
            stage_bucket["observed_candidate_count"] += 1
            stage_bucket["eligible_candidate_count"] += int(eligible)
            stage_bucket["model_buy_candidate_count"] += int(
                model_buy and eligible
            )
            stage_bucket["executed_buy_candidate_count"] += int(
                executed_buy and model_buy and eligible
            )
            if model_buy and eligible:
                sizing_gaps: set[str] = set()
                actions = model_buy_actions_by_code.get(code) or []
                if (
                    row.get("execution_evidence_schema_version")
                    != FORWARD_EXECUTION_EVIDENCE_SCHEMA_VERSION
                ):
                    sizing_gaps.add("execution_evidence_schema_version")
                if len(actions) != 1:
                    sizing_gaps.add("decision_actions.unique_buy_code")
                    if len(actions) > 1:
                        duplicate_model_buy_action_count += len(actions) - 1
                action = actions[0] if len(actions) == 1 else {}
                decision_shares = _shares(action.get("shares"))
                requested_shares = _shares(
                    action.get("model_requested_shares")
                ) or decision_shares
                if decision_shares <= 0 or decision_shares % 100:
                    sizing_gaps.add("model_buy.shares")
                if not isinstance(structured_blocks, list):
                    sizing_gaps.add("decision.execution_blocks")

                fills = list(
                    execution_fills_by_cycle.get(cycle_key, {}).get(code, ())
                )
                fill = fills[0] if len(fills) == 1 else {}
                executed_shares = 0
                maximum_shares = _optional_quantity(
                    (fill or action).get("maximum_permitted_shares")
                )
                utilization = _number(
                    (fill or action).get("risk_ceiling_utilization_pct")
                )
                auto_reduced = (fill or action).get(
                    "risk_ceiling_auto_reduced"
                )
                binding = (fill or action).get(
                    "risk_ceiling_binding_constraints"
                )
                position_opened = (fill or action).get("position_opened")
                rejection_categories: list[str] = []

                if executed_buy:
                    if len(fills) != 1:
                        sizing_gaps.add("durable_fill.unique_buy_code")
                    else:
                        executed_shares = _shares(fill.get("shares"))
                        if (
                            executed_shares <= 0
                            or executed_shares % 100
                            or executed_shares != decision_shares
                        ):
                            sizing_gaps.add("durable_fill.shares")
                        if fill.get("model_requested_shares") != requested_shares:
                            sizing_gaps.add(
                                "durable_fill.model_requested_shares"
                            )
                        if action.get("maximum_permitted_shares") != (
                            fill.get("maximum_permitted_shares")
                        ):
                            sizing_gaps.add(
                                "decision_fill.maximum_permitted_shares"
                            )
                        if action.get("risk_ceiling_utilization_pct") != (
                            fill.get("risk_ceiling_utilization_pct")
                        ):
                            sizing_gaps.add(
                                "decision_fill.risk_ceiling_utilization_pct"
                            )
                        if action.get("risk_ceiling_auto_reduced") is not (
                            fill.get("risk_ceiling_auto_reduced")
                        ):
                            sizing_gaps.add(
                                "decision_fill.risk_ceiling_auto_reduced"
                            )
                    if blocks_by_code.get(code):
                        sizing_gaps.add("executed_buy.execution_blocks")
                    if not isinstance(position_opened, bool):
                        sizing_gaps.add("durable_fill.position_opened")
                    if (
                        maximum_shares is None
                        or maximum_shares <= 0
                        or maximum_shares % 100
                        or executed_shares > maximum_shares
                    ):
                        sizing_gaps.add(
                            "durable_fill.maximum_permitted_shares"
                        )
                    expected_utilization = (
                        executed_shares / maximum_shares * 100.0
                        if executed_shares > 0
                        and maximum_shares is not None
                        and maximum_shares > 0
                        else None
                    )
                    if not isinstance(auto_reduced, bool):
                        sizing_gaps.add(
                            "durable_fill.risk_ceiling_auto_reduced"
                        )
                    elif auto_reduced:
                        if not (
                            requested_shares > (maximum_shares or 0) > 0
                            and decision_shares == maximum_shares
                            and executed_shares == maximum_shares
                        ):
                            sizing_gaps.add(
                                "durable_fill.risk_ceiling_auto_reduced"
                            )
                    elif not (
                        requested_shares == decision_shares
                        == executed_shares
                    ):
                        sizing_gaps.add(
                            "durable_fill.risk_ceiling_auto_reduced"
                        )
                    if (
                        utilization is None
                        or utilization <= 0
                        or utilization > 100.0 + 1e-9
                        or (
                            expected_utilization is not None
                            and not math.isclose(
                                utilization,
                                expected_utilization,
                                rel_tol=0.0,
                                abs_tol=1e-4,
                            )
                        )
                    ):
                        sizing_gaps.add(
                            "durable_fill.risk_ceiling_utilization_pct"
                        )
                    if (
                        not isinstance(binding, list)
                        or not binding
                        or any(
                            not str(value or "").strip()
                            for value in binding
                        )
                    ):
                        sizing_gaps.add(
                            "durable_fill.risk_ceiling_binding_constraints"
                        )
                else:
                    code_blocks = blocks_by_code.get(code) or []
                    if not code_blocks:
                        sizing_gaps.add("rejected_buy.execution_block")
                    for block in code_blocks:
                        category = str(block.get("category") or "").strip()
                        reason = str(block.get("reason") or "").strip()
                        if not category:
                            sizing_gaps.add(
                                "rejected_buy.execution_block.category"
                            )
                        else:
                            rejection_categories.append(category)
                            rejection_category_counts[category] += 1
                        if not reason:
                            sizing_gaps.add(
                                "rejected_buy.execution_block.reason"
                            )
                    if (
                        "risk_ceiling" in rejection_categories
                        and (
                            maximum_shares is not None
                            or utilization is not None
                        )
                    ):
                        if (
                            maximum_shares is None
                            or maximum_shares < 0
                            or maximum_shares % 100
                        ):
                            sizing_gaps.add(
                                "rejected_buy.maximum_permitted_shares"
                            )
                        elif maximum_shares > 0:
                            if decision_shares > maximum_shares:
                                sizing_gaps.add(
                                    "rejected_buy.oversized_niuone_buy_"
                                    "not_auto_reduced"
                                )
                            expected_utilization = (
                                decision_shares / maximum_shares * 100.0
                                if decision_shares > 0 else None
                            )
                            if (
                                utilization is None
                                or expected_utilization is None
                                or not math.isclose(
                                    utilization,
                                    expected_utilization,
                                    rel_tol=0.0,
                                    abs_tol=1e-4,
                                )
                            ):
                                sizing_gaps.add(
                                    "rejected_buy.risk_ceiling_utilization_pct"
                                )

                for field in sizing_gaps:
                    invalid_sizing_field_counts[field] += 1
                sizing_records.append({
                    "stage": stage,
                    "requested_shares": requested_shares,
                    "decision_shares": decision_shares,
                    "executed_shares": executed_shares,
                    "maximum_permitted_shares": maximum_shares,
                    "risk_ceiling_utilization_pct": utilization,
                    "position_opened": position_opened,
                    "risk_ceiling_auto_reduced": auto_reduced,
                    "executed": executed_buy,
                    "rejection_categories": tuple(
                        sorted(set(rejection_categories))
                    ),
                    "valid": not sizing_gaps,
                })
        if cycle_niuone_count == 0:
            zero_candidate_cycle_count += 1

    def rate(numerator: int, denominator: int) -> float | None:
        return (
            round(numerator / denominator * 100.0, 4)
            if denominator else None
        )

    for bucket in stage_funnel.values():
        bucket["eligibility_rate_pct"] = rate(
            bucket["eligible_candidate_count"],
            bucket["observed_candidate_count"],
        )
        bucket["model_buy_rate_of_eligible_pct"] = rate(
            bucket["model_buy_candidate_count"],
            bucket["eligible_candidate_count"],
        )
        bucket["execution_rate_of_model_buys_pct"] = rate(
            bucket["executed_buy_candidate_count"],
            bucket["model_buy_candidate_count"],
        )

    def sizing_summary(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        materialized = list(records)
        executed = [row for row in materialized if row.get("executed") is True]
        rejected = [row for row in materialized if row.get("executed") is not True]
        valid = [row for row in materialized if row.get("valid") is True]
        executed_with_ceiling = [
            row
            for row in valid
            if row.get("executed") is True
            and (_optional_quantity(row.get("maximum_permitted_shares")) or 0) > 0
            and _number(row.get("risk_ceiling_utilization_pct")) is not None
        ]
        utilizations = [
            float(row["risk_ceiling_utilization_pct"])
            for row in executed_with_ceiling
        ]
        maximum_share_total = sum(
            int(row["maximum_permitted_shares"])
            for row in executed_with_ceiling
        )
        executed_share_total_for_ceiling = sum(
            int(row["executed_shares"])
            for row in executed_with_ceiling
        )
        return {
            "model_buy_order_count": len(materialized),
            "valid_model_buy_order_count": len(valid),
            "invalid_model_buy_order_count": len(materialized) - len(valid),
            "executed_buy_order_count": len(executed),
            "rejected_buy_order_count": len(rejected),
            "opening_buy_order_count": sum(
                row.get("position_opened") is True for row in executed
            ),
            "add_buy_order_count": sum(
                row.get("position_opened") is False for row in executed
            ),
            "requested_share_count": sum(
                max(0, int(row.get("requested_shares") or 0))
                for row in materialized
            ),
            "executed_share_count": sum(
                max(0, int(row.get("executed_shares") or 0))
                for row in materialized
            ),
            "auto_reduced_buy_order_count": sum(
                row.get("executed") is True
                and row.get("risk_ceiling_auto_reduced") is True
                for row in valid
            ),
            "auto_reduced_share_count": sum(
                max(
                    0,
                    int(row.get("requested_shares") or 0)
                    - int(row.get("executed_shares") or 0),
                )
                for row in valid
                if row.get("executed") is True
                and row.get("risk_ceiling_auto_reduced") is True
            ),
            "executed_risk_ceiling_evidence_count": len(
                executed_with_ceiling
            ),
            "executed_risk_ceiling_evidence_coverage_pct": rate(
                len(executed_with_ceiling),
                len(executed),
            ),
            "aggregate_risk_ceiling_utilization_pct": round(
                executed_share_total_for_ceiling
                / maximum_share_total * 100.0,
                4,
            ) if maximum_share_total else None,
            "average_risk_ceiling_utilization_pct": round(
                statistics.mean(utilizations),
                4,
            ) if utilizations else None,
            "median_risk_ceiling_utilization_pct": round(
                statistics.median(utilizations),
                4,
            ) if utilizations else None,
            "minimum_risk_ceiling_utilization_pct": round(
                min(utilizations),
                4,
            ) if utilizations else None,
            "maximum_risk_ceiling_utilization_pct": round(
                max(utilizations),
                4,
            ) if utilizations else None,
            "risk_ceiling_rejection_count": sum(
                "risk_ceiling" in (row.get("rejection_categories") or ())
                for row in rejected
            ),
        }

    overall_sizing = sizing_summary(sizing_records)
    stage_sizing = {
        stage: {
            "label": str(metadata.get("label") or stage),
            "order": int(metadata.get("order") or 0),
            **sizing_summary(
                row for row in sizing_records if row.get("stage") == stage
            ),
        }
        for stage, metadata in sorted(
            NIUONE_LIFECYCLE_STAGES.items(),
            key=lambda item: int(item[1].get("order") or 0),
        )
    }
    invalid_sizing_order_count = overall_sizing[
        "invalid_model_buy_order_count"
    ]

    funnel_anomaly_count = sum((
        invalid_timestamp_count,
        invalid_cycle_count,
        non_durable_buy_fill_count,
        unmapped_durable_buy_fill_count,
        model_buy_without_eligibility_count,
        executed_buy_without_model_count,
        unmatched_model_buy_code_count,
        unmatched_executed_buy_code_count,
        decision_executed_without_durable_fill_count,
        durable_fill_without_decision_executed_count,
        invalid_sizing_order_count,
        sell_execution["invalid_sell_execution_fill_count"],
    ))
    unmatched_durable_buy_fill_count = sum(
        len(codes)
        for cycle_key, codes in execution_codes_by_cycle.items()
        if cycle_key not in cycles
    )
    funnel_anomaly_count += unmatched_durable_buy_fill_count

    return {
        "unit_of_analysis": "deduplicated_decision_cycle_candidate_observation",
        "retained_decision_cycle_count": len(cycles),
        "valid_candidate_evidence_cycle_count": valid_cycle_count,
        "invalid_candidate_evidence_cycle_count": invalid_cycle_count,
        "duplicate_decision_cycle_count": duplicate_cycle_count,
        "invalid_decision_timestamp_count": invalid_timestamp_count,
        "ignored_non_opportunity_decision_count": (
            ignored_non_opportunity_decision_count
        ),
        "zero_niuone_candidate_cycle_count": zero_candidate_cycle_count,
        "observed_candidate_count": observed_count,
        "eligible_candidate_count": eligible_count,
        "model_buy_candidate_count": model_buy_count,
        "executed_buy_candidate_count": executed_buy_count,
        "eligibility_rate_pct": rate(eligible_count, observed_count),
        "model_buy_rate_of_eligible_pct": rate(
            model_buy_count,
            eligible_count,
        ),
        "execution_rate_of_model_buys_pct": rate(
            executed_buy_count,
            model_buy_count,
        ),
        "execution_sizing": {
            "unit_of_analysis": (
                "deduplicated_decision_cycle_eligible_niuone_model_buy"
            ),
            "schema_version": FORWARD_EXECUTION_EVIDENCE_SCHEMA_VERSION,
            **overall_sizing,
            "duplicate_model_buy_action_count": (
                duplicate_model_buy_action_count
            ),
            "rejection_category_counts": dict(
                sorted(rejection_category_counts.items())
            ),
            "invalid_sizing_evidence_fields": dict(
                sorted(invalid_sizing_field_counts.items())
            ),
            "sizing_data_quality_gate_met": (
                invalid_sizing_order_count == 0
            ),
            "by_lifecycle_stage": stage_sizing,
        },
        "sell_execution": sell_execution,
        "model_buy_without_eligibility_count": (
            model_buy_without_eligibility_count
        ),
        "executed_buy_without_model_count": executed_buy_without_model_count,
        "unmatched_model_buy_code_count": unmatched_model_buy_code_count,
        "unmatched_executed_buy_code_count": (
            unmatched_executed_buy_code_count
        ),
        "decision_executed_without_durable_fill_count": (
            decision_executed_without_durable_fill_count
        ),
        "durable_fill_without_decision_executed_count": (
            durable_fill_without_decision_executed_count
        ),
        "unmatched_durable_buy_fill_count": unmatched_durable_buy_fill_count,
        "non_durable_buy_fill_count": non_durable_buy_fill_count,
        "unmapped_durable_buy_fill_count": unmapped_durable_buy_fill_count,
        "funnel_evidence_valid": (
            invalid_timestamp_count == 0 and invalid_cycle_count == 0
        ),
        "decision_execution_consistency_gate_met": all(
            count == 0
            for count in (
                model_buy_without_eligibility_count,
                executed_buy_without_model_count,
                unmatched_model_buy_code_count,
                unmatched_executed_buy_code_count,
                decision_executed_without_durable_fill_count,
                durable_fill_without_decision_executed_count,
                unmatched_durable_buy_fill_count,
            )
        ),
        "execution_sizing_data_quality_gate_met": (
            invalid_sizing_order_count == 0
        ),
        "funnel_data_quality_gate_met": funnel_anomaly_count == 0,
        "invalid_opportunity_evidence_fields": dict(
            sorted(invalid_field_counts.items())
        ),
        "by_lifecycle_stage": stage_funnel,
    }


def _date_value(value: Any, *, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        raise ValueError(f"{field_name} must use YYYY-MM-DD") from None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _shares(value: Any) -> int:
    number = _number(value)
    if number is None or number <= 0 or not float(number).is_integer():
        return 0
    return int(number)


def _optional_quantity(value: Any) -> int | None:
    number = _number(value)
    if number is None or number < 0 or not float(number).is_integer():
        return None
    return int(number)


def _valid_timestamp(value: Any) -> bool:
    return _timestamp_value(value) is not None


def _timestamp_value(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    offset = parsed.utcoffset()
    if offset is not None:
        parsed = (parsed - offset).replace(tzinfo=None)
    return parsed


def _entry_attribution_gaps(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Return stable field names that make one lifecycle non-auditable."""
    gaps: list[str] = []
    if row.get("entry_payload_available") is False:
        gaps.append("entry_payload")
    context = row.get("entry_context")
    context = context if isinstance(context, Mapping) else {}
    lifecycle_stage = str(
        context.get("entry_niuone_lifecycle_stage") or ""
    ).strip()
    lifecycle = NIUONE_LIFECYCLE_STAGES.get(lifecycle_stage)
    if lifecycle is None:
        gaps.append("entry_niuone_lifecycle_stage")
        if not str(
            context.get("entry_niuone_lifecycle_label") or ""
        ).strip():
            gaps.append("entry_niuone_lifecycle_label")
        lifecycle_order = _number(
            context.get("entry_niuone_lifecycle_order")
        )
        if lifecycle_order is None or not float(lifecycle_order).is_integer():
            gaps.append("entry_niuone_lifecycle_order")
        if not str(
            context.get("entry_niuone_lifecycle_entry_policy") or ""
        ).strip():
            gaps.append("entry_niuone_lifecycle_entry_policy")
    else:
        if str(
            context.get("entry_niuone_lifecycle_label") or ""
        ).strip() != str(lifecycle.get("label") or ""):
            gaps.append("entry_niuone_lifecycle_label")
        lifecycle_order = _number(
            context.get("entry_niuone_lifecycle_order")
        )
        if (
            lifecycle_order is None
            or not float(lifecycle_order).is_integer()
            or int(lifecycle_order) != int(lifecycle.get("order") or -1)
        ):
            gaps.append("entry_niuone_lifecycle_order")
        if str(
            context.get("entry_niuone_lifecycle_entry_policy") or ""
        ).strip() != str(lifecycle.get("entry_policy") or ""):
            gaps.append("entry_niuone_lifecycle_entry_policy")
        if str(row.get("entry_strategy") or "").strip() not in tuple(
            lifecycle.get("allowed_entry_strategy_ids") or ()
        ):
            gaps.append("entry_niuone_lifecycle_strategy_route")
    if not str(context.get("entry_mainline_state") or "").strip():
        gaps.append("entry_mainline_state")
    if not str(context.get("entry_industry") or "").strip():
        gaps.append("entry_industry")
    if not str(context.get("entry_theme") or "").strip():
        gaps.append("entry_theme")
    if not str(context.get("entry_theme_basis") or "").strip():
        gaps.append("entry_theme_basis")
    for field in (
        "entry_theme_attribution_score",
        "entry_theme_historical_prior_score",
        "entry_theme_cohort_alignment_score",
        "entry_theme_peer_resonance_score",
        "entry_theme_specificity_score",
    ):
        value = _number(context.get(field))
        if value is None or value < 0 or value > 100:
            gaps.append(field)
    correlation_observations = _number(
        context.get("entry_theme_return_correlation_observation_count")
    )
    correlation_peers = _number(
        context.get("entry_theme_return_correlation_peer_count")
    )
    for field, value in (
        (
            "entry_theme_return_correlation_observation_count",
            correlation_observations,
        ),
        ("entry_theme_return_correlation_peer_count", correlation_peers),
    ):
        if value is None or value < 0 or not float(value).is_integer():
            gaps.append(field)
    for field in (
        "entry_theme_return_correlation_score",
        "entry_theme_return_correlation_rank_score",
    ):
        value = _number(context.get(field))
        evidence_expected = bool(
            correlation_observations is not None
            and correlation_observations >= 8
            and correlation_peers is not None
            and correlation_peers >= 3
        )
        if (
            value is not None
            and (value < 0 or value > 100)
        ) or (evidence_expected and value is None):
            gaps.append(field)
    attribution_weight = _number(
        context.get("entry_theme_attribution_weight")
    )
    if (
        attribution_weight is None
        or attribution_weight <= 0
        or attribution_weight > 1
    ):
        gaps.append("entry_theme_attribution_weight")
    unattributed_weight = _number(
        context.get("entry_theme_unattributed_weight")
    )
    if (
        unattributed_weight is None
        or unattributed_weight < 0
        or unattributed_weight > 1
    ):
        gaps.append("entry_theme_unattributed_weight")
    if not str(context.get("entry_theme_membership_source") or "").strip():
        gaps.append("entry_theme_membership_source")
    for field in (
        "entry_signal_score",
        "entry_execution_gap_pct",
        "entry_daily_v_recovery_ratio",
    ):
        if _number(context.get(field)) is None:
            gaps.append(field)
    for field in (
        "entry_stock_activity_score",
        "entry_stock_market_amount_percentile",
        "entry_stock_theme_amount_percentile",
    ):
        value = _number(context.get(field))
        if value is None or value < 0 or value > 100:
            gaps.append(field)
    activity_confirmed = context.get("entry_stock_activity_confirmed")
    if not isinstance(activity_confirmed, bool):
        gaps.append("entry_stock_activity_confirmed")
    elif (
        str(row.get("entry_strategy") or "").strip()
        in {"niu_leader", "niu_pullback", "niu_emerging"}
        and activity_confirmed is not True
    ):
        gaps.append("entry_stock_activity_confirmed")
    rank = _number(context.get("entry_same_stage_candidate_rank"))
    if rank is None or rank <= 0 or not float(rank).is_integer():
        gaps.append("entry_same_stage_candidate_rank")
    if not _valid_timestamp(context.get("entry_signal_generated_at")):
        gaps.append("entry_signal_generated_at")
    run_kind = str(context.get("entry_schedule_run_kind") or "").strip()
    if run_kind not in FORWARD_ALLOWED_RUN_KINDS:
        gaps.append("entry_schedule_run_kind")
    if not _valid_timestamp(context.get("entry_schedule_triggered_at")):
        gaps.append("entry_schedule_triggered_at")
    if (
        run_kind in FORWARD_SCHEDULED_RUN_KINDS
        and not str(context.get("entry_schedule_slot") or "").strip()
    ):
        gaps.append("entry_schedule_slot")
    execution_mode = str(context.get("entry_execution_mode") or "").strip()
    if execution_mode not in FORWARD_ALLOWED_EXECUTION_MODES:
        gaps.append("entry_execution_mode")
    requested_shares = _shares(context.get("entry_model_requested_shares"))
    executed_shares = _shares(context.get("entry_executed_shares"))
    maximum_shares = _shares(
        context.get("entry_maximum_permitted_shares")
    )
    utilization = _number(
        context.get("entry_risk_ceiling_utilization_pct")
    )
    if requested_shares <= 0 or requested_shares % 100:
        gaps.append("entry_model_requested_shares")
    if executed_shares <= 0 or executed_shares % 100:
        gaps.append("entry_executed_shares")
    entry_fill_shares = _shares(row.get("entry_fill_shares"))
    if entry_fill_shares <= 0 or entry_fill_shares != executed_shares:
        gaps.append("entry_executed_shares")
    if maximum_shares <= 0 or maximum_shares % 100:
        gaps.append("entry_maximum_permitted_shares")
    if (
        executed_shares > 0
        and maximum_shares > 0
        and executed_shares > maximum_shares
    ):
        gaps.append("entry_maximum_permitted_shares")
    auto_reduced = context.get("entry_risk_ceiling_auto_reduced")
    if not isinstance(auto_reduced, bool):
        gaps.append("entry_risk_ceiling_auto_reduced")
    elif auto_reduced:
        if not (
            requested_shares > maximum_shares > 0
            and executed_shares == maximum_shares
        ):
            gaps.append("entry_risk_ceiling_auto_reduced")
    elif requested_shares != executed_shares:
        gaps.append("entry_risk_ceiling_auto_reduced")
    expected_utilization = (
        executed_shares / maximum_shares * 100.0
        if executed_shares > 0 and maximum_shares > 0 else None
    )
    if (
        utilization is None
        or utilization <= 0
        or utilization > 100.0 + 1e-9
        or (
            expected_utilization is not None
            and not math.isclose(
                utilization,
                expected_utilization,
                rel_tol=0.0,
                abs_tol=1e-4,
            )
        )
    ):
        gaps.append("entry_risk_ceiling_utilization_pct")
    binding = context.get("entry_risk_ceiling_binding_constraints")
    if (
        not isinstance(binding, list)
        or not binding
        or any(not str(value or "").strip() for value in binding)
    ):
        gaps.append("entry_risk_ceiling_binding_constraints")
    return tuple(gaps)


def _holding_lifecycle_attribution_gaps(
    row: Mapping[str, Any],
) -> tuple[str, ...]:
    """Validate a completed holding's causal stage path and exit stage."""
    gaps: list[str] = []
    if row.get("exit_payload_available") is False:
        gaps.append("exit_payload")
    evidence = row.get("exit_context")
    if not isinstance(evidence, Mapping) or not evidence:
        return (*gaps, "niuone_lifecycle_evidence")
    if (
        _number(evidence.get("schema_version"))
        != FORWARD_HOLDING_LIFECYCLE_EVIDENCE_SCHEMA_VERSION
    ):
        gaps.append("holding_lifecycle.schema_version")
    if evidence.get("path_complete_from_entry") is not True:
        gaps.append("holding_lifecycle.path_complete_from_entry")

    entry_context = row.get("entry_context")
    entry_context = (
        entry_context if isinstance(entry_context, Mapping) else {}
    )
    entry_stage = str(
        entry_context.get("entry_niuone_lifecycle_stage") or ""
    ).strip()
    raw_path = evidence.get("path")
    if not isinstance(raw_path, list) or not raw_path:
        return (*gaps, "holding_lifecycle.path")

    path_stages: list[str] = []
    previous_stage = ""
    previous_last_observed_at = ""
    first_entered_at = ""
    final_last_observed_at = ""
    observed_dates: set[str] = set()
    observation_sources_by_date: dict[str, set[str]] = defaultdict(set)
    for raw_segment in raw_path:
        if not isinstance(raw_segment, Mapping):
            gaps.append("holding_lifecycle.path_segment")
            continue
        stage = str(raw_segment.get("stage") or "").strip()
        definition = NIUONE_LIFECYCLE_STAGES.get(stage)
        if definition is None:
            gaps.append("holding_lifecycle.path_stage")
            continue
        path_stages.append(stage)
        if previous_stage == stage:
            gaps.append("holding_lifecycle.duplicate_adjacent_stage")
        previous_stage = stage
        if str(raw_segment.get("label") or "").strip() != str(
            definition.get("label") or ""
        ):
            gaps.append("holding_lifecycle.path_label")
        order = _number(raw_segment.get("order"))
        if (
            order is None
            or not float(order).is_integer()
            or int(order) != int(definition.get("order") or -1)
        ):
            gaps.append("holding_lifecycle.path_order")
        if str(raw_segment.get("entry_policy") or "").strip() != str(
            definition.get("entry_policy") or ""
        ):
            gaps.append("holding_lifecycle.path_entry_policy")
        entered_at = str(raw_segment.get("entered_at") or "").strip()
        last_observed_at = str(
            raw_segment.get("last_observed_at") or ""
        ).strip()
        if not first_entered_at:
            first_entered_at = entered_at
        final_last_observed_at = last_observed_at
        if not _valid_timestamp(entered_at):
            gaps.append("holding_lifecycle.entered_at")
        if not _valid_timestamp(last_observed_at):
            gaps.append("holding_lifecycle.last_observed_at")
        if (
            _timestamp_value(entered_at) is not None
            and _timestamp_value(last_observed_at) is not None
            and _timestamp_value(last_observed_at)
            < _timestamp_value(entered_at)
        ):
            gaps.append("holding_lifecycle.segment_time_order")
        if (
            _timestamp_value(previous_last_observed_at) is not None
            and _timestamp_value(entered_at) is not None
            and _timestamp_value(entered_at)
            < _timestamp_value(previous_last_observed_at)
        ):
            gaps.append("holding_lifecycle.path_time_order")
        previous_last_observed_at = last_observed_at
        observations = _number(raw_segment.get("observation_count"))
        raw_observations = raw_segment.get("observations")
        if (
            observations is None
            or observations <= 0
            or not float(observations).is_integer()
        ):
            gaps.append("holding_lifecycle.observation_count")
        if not isinstance(raw_observations, list) or not raw_observations:
            gaps.append("holding_lifecycle.observations")
            continue
        if (
            observations is None
            or int(observations) != len(raw_observations)
        ):
            gaps.append("holding_lifecycle.observation_count")
        previous_observed_at = ""
        normalized_observation_times: list[str] = []
        for raw_observation in raw_observations:
            if not isinstance(raw_observation, Mapping):
                gaps.append("holding_lifecycle.observation")
                continue
            observed_at = str(
                raw_observation.get("observed_at") or ""
            ).strip()
            if not _valid_timestamp(observed_at):
                gaps.append("holding_lifecycle.observed_at")
                continue
            if (
                _timestamp_value(previous_observed_at) is not None
                and _timestamp_value(observed_at) is not None
                and _timestamp_value(observed_at)
                < _timestamp_value(previous_observed_at)
            ):
                gaps.append("holding_lifecycle.observation_time_order")
            previous_observed_at = observed_at
            normalized_observation_times.append(observed_at)
            observed_dates.add(observed_at[:10])
            observation_source = str(
                raw_observation.get("source") or ""
            )
            observation_sources_by_date[observed_at[:10]].add(
                observation_source
            )
            if observation_source not in {
                "entry_signal", "mainline_scan", "exit_fill"
            }:
                gaps.append("holding_lifecycle.observation_source")
        if normalized_observation_times and (
            _timestamp_value(normalized_observation_times[0])
            != _timestamp_value(entered_at)
            or _timestamp_value(normalized_observation_times[-1])
            != _timestamp_value(last_observed_at)
        ):
            gaps.append("holding_lifecycle.observation_time_alignment")

    if not path_stages:
        return (*gaps, "holding_lifecycle.path_stage")
    if path_stages[0] != entry_stage:
        gaps.append("holding_lifecycle.entry_stage_alignment")
    if _timestamp_value(first_entered_at) != _timestamp_value(
        entry_context.get("entry_signal_generated_at")
    ):
        gaps.append("holding_lifecycle.entry_time_alignment")
    if _timestamp_value(final_last_observed_at) != _timestamp_value(
        row.get("exit_time")
    ):
        gaps.append("holding_lifecycle.exit_time_alignment")
    required_dates = row.get("required_holding_dates")
    required_date_set = (
        {str(value or "")[:10] for value in required_dates}
        if isinstance(required_dates, list) else set()
    )
    if required_date_set - observed_dates:
        gaps.append("holding_lifecycle.operating_day_coverage")
    entry_date = str(row.get("entry_date") or "")[:10]
    exit_date = str(row.get("exit_date") or "")[:10]
    if "entry_signal" not in observation_sources_by_date.get(
        entry_date, set()
    ):
        gaps.append("holding_lifecycle.entry_observation_source")
    if any(
        "mainline_scan" not in observation_sources_by_date.get(day, set())
        for day in required_date_set
        if day > entry_date
    ):
        gaps.append("holding_lifecycle.operating_day_scan_coverage")
    if "exit_fill" not in observation_sources_by_date.get(
        exit_date, set()
    ):
        gaps.append("holding_lifecycle.exit_observation_source")
    sequence = evidence.get("stage_sequence")
    if not isinstance(sequence, list) or [
        str(value or "") for value in sequence
    ] != path_stages:
        gaps.append("holding_lifecycle.stage_sequence")
    transition_count = _number(evidence.get("transition_count"))
    if (
        transition_count is None
        or not float(transition_count).is_integer()
        or int(transition_count) != len(path_stages) - 1
    ):
        gaps.append("holding_lifecycle.transition_count")

    exit_stage = str(
        evidence.get("exit_niuone_lifecycle_stage") or ""
    ).strip()
    exit_definition = NIUONE_LIFECYCLE_STAGES.get(exit_stage)
    if exit_definition is None or exit_stage != path_stages[-1]:
        gaps.append("exit_niuone_lifecycle_stage")
    else:
        if str(
            evidence.get("exit_niuone_lifecycle_label") or ""
        ).strip() != str(exit_definition.get("label") or ""):
            gaps.append("exit_niuone_lifecycle_label")
        exit_order = _number(
            evidence.get("exit_niuone_lifecycle_order")
        )
        if (
            exit_order is None
            or not float(exit_order).is_integer()
            or int(exit_order) != int(exit_definition.get("order") or -1)
        ):
            gaps.append("exit_niuone_lifecycle_order")
        if str(
            evidence.get("exit_niuone_lifecycle_entry_policy") or ""
        ).strip() != str(exit_definition.get("entry_policy") or ""):
            gaps.append("exit_niuone_lifecycle_entry_policy")
    for stage in ("markup", "climax", "divergence", "fade"):
        if evidence.get(f"reached_{stage}") is not (stage in path_stages):
            gaps.append(f"holding_lifecycle.reached_{stage}")
    return tuple(dict.fromkeys(gaps))


def _lifecycle_attribution_gaps(
    row: Mapping[str, Any],
) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        (*_entry_attribution_gaps(row),
         *_holding_lifecycle_attribution_gaps(row))
    ))


def _strategy_id(trade: Mapping[str, Any]) -> str:
    mark = trade.get("strategy_mark")
    mark = mark if isinstance(mark, Mapping) else {}
    return str(
        trade.get("buy_strategy")
        or trade.get("entry_strategy_id")
        or mark.get("strategy_id")
        or ""
    ).strip()


def _entry_context(trade: Mapping[str, Any]) -> dict[str, Any]:
    context = trade.get("niuone_entry_context")
    return dict(context) if isinstance(context, Mapping) else {}


def _exit_context(trade: Mapping[str, Any]) -> dict[str, Any]:
    context = trade.get("niuone_lifecycle_evidence")
    return dict(context) if isinstance(context, Mapping) else {}


def _buy_cost(trade: Mapping[str, Any]) -> float | None:
    explicit = _number(trade.get("total_cost"))
    if explicit is not None and explicit > 0:
        return explicit
    amount = _number(trade.get("amount"))
    fee = _number(trade.get("fee")) or 0.0
    if amount is None or amount <= 0:
        return None
    return amount + fee


def _sell_proceeds(trade: Mapping[str, Any]) -> float | None:
    explicit = _number(trade.get("net_proceeds"))
    if explicit is not None and explicit >= 0:
        return explicit
    amount = _number(trade.get("amount"))
    fee = _number(trade.get("fee")) or 0.0
    if amount is None or amount <= 0 or amount < fee:
        return None
    return amount - fee


def _full_months(start: date, end: date) -> int:
    if end < start:
        return 0
    months = (end.year - start.year) * 12 + end.month - start.month
    if end.day < start.day:
        months -= 1
    return max(0, months)


def _wilson_score_interval(
    wins: int,
    total: int,
    *,
    confidence_level: float = DEFAULT_WIN_RATE_CONFIDENCE_LEVEL,
) -> tuple[float | None, float | None]:
    """Return a two-sided Wilson score interval in percentage points."""
    if total <= 0:
        return None, None
    if wins < 0 or wins > total:
        raise ValueError("wins must be between zero and total")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be between zero and one")
    z_score = statistics.NormalDist().inv_cdf(
        0.5 + confidence_level / 2.0
    )
    observed = wins / total
    z_squared = z_score * z_score
    denominator = 1.0 + z_squared / total
    center = (observed + z_squared / (2.0 * total)) / denominator
    margin = z_score / denominator * math.sqrt(
        observed * (1.0 - observed) / total
        + z_squared / (4.0 * total * total)
    )
    return (
        round(max(0.0, center - margin) * 100.0, 4),
        round(min(1.0, center + margin) * 100.0, 4),
    )


def _metric_summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    returns = [float(row["net_return_pct"]) for row in materialized]
    pnls = [float(row["realized_pnl"]) for row in materialized]
    wins = sum(value > 0 for value in pnls)
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = abs(sum(value for value in pnls if value < 0))
    lower_win_rate, upper_win_rate = _wilson_score_interval(
        wins,
        len(materialized),
    )
    winning_returns = [value for value in returns if value > 0]
    losing_returns = [value for value in returns if value < 0]
    return {
        "completed_trade_count": len(materialized),
        "win_count": wins,
        "loss_count": sum(value < 0 for value in pnls),
        "breakeven_count": sum(value == 0 for value in pnls),
        "win_rate_pct": round(wins / len(materialized) * 100.0, 4)
        if materialized else None,
        "win_rate_wilson_95_lower_pct": lower_win_rate,
        "win_rate_wilson_95_upper_pct": upper_win_rate,
        "average_net_return_pct": round(statistics.mean(returns), 4)
        if returns else None,
        "median_net_return_pct": round(statistics.median(returns), 4)
        if returns else None,
        "average_win_net_return_pct": round(
            statistics.mean(winning_returns),
            4,
        ) if winning_returns else None,
        "average_loss_net_return_pct": round(
            statistics.mean(losing_returns),
            4,
        ) if losing_returns else None,
        "realized_pnl": round(sum(pnls), 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_profit / gross_loss, 4)
        if gross_loss > 0 else None,
        "profit_factor_is_infinite": bool(
            gross_profit > 0 and gross_loss == 0
        ),
    }


def _performance_cluster_summary(
    rows: Iterable[Mapping[str, Any]],
    *,
    minimum_cluster_count: int,
    historical_reference_win_rate_pct: float,
) -> dict[str, Any]:
    """Summarize lifecycle results without overweighting one mainline wave."""
    materialized = list(rows)
    clusters: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    missing_cluster_key_count = 0
    for row in materialized:
        entry_date = str(row.get("entry_date") or "").strip()[:10]
        context = row.get("entry_context")
        theme = (
            str(context.get("entry_theme") or "").strip()
            if isinstance(context, Mapping) else ""
        )
        try:
            valid_entry_date = (
                _date_value(entry_date, field_name="cluster entry_date")
                .isoformat()
            )
        except ValueError:
            valid_entry_date = ""
        if not valid_entry_date or not theme:
            missing_cluster_key_count += 1
            continue
        clusters[(valid_entry_date, theme)].append(row)

    cluster_rows: list[dict[str, Any]] = []
    cluster_win_rates: list[float] = []
    cluster_average_returns: list[float] = []
    cluster_sizes: list[int] = []
    for (entry_date, theme), group in sorted(clusters.items()):
        size = len(group)
        wins = sum(float(row["realized_pnl"]) > 0.0 for row in group)
        win_rate = wins / size
        average_return = statistics.mean(
            float(row["net_return_pct"]) for row in group
        )
        cluster_sizes.append(size)
        cluster_win_rates.append(win_rate)
        cluster_average_returns.append(average_return)
        cluster_rows.append({
            "entry_date": entry_date,
            "entry_theme": theme,
            "completed_trade_count": size,
            "win_count": wins,
            "win_rate_pct": round(win_rate * 100.0, 4),
            "average_net_return_pct": round(average_return, 4),
        })

    clustered_trade_count = sum(cluster_sizes)
    cluster_count = len(cluster_rows)
    effective_cluster_count = (
        clustered_trade_count * clustered_trade_count
        / sum(size * size for size in cluster_sizes)
        if cluster_sizes else 0.0
    )
    cluster_balanced_win_rate = (
        statistics.mean(cluster_win_rates)
        if cluster_win_rates else None
    )
    cluster_balanced_average_return = (
        statistics.mean(cluster_average_returns)
        if cluster_average_returns else None
    )
    cluster_win_rate_standard_error: float | None = None
    cluster_win_rate_lower: float | None = None
    cluster_win_rate_upper: float | None = None
    if cluster_balanced_win_rate is not None:
        cluster_win_rate_standard_error = (
            statistics.stdev(cluster_win_rates) / math.sqrt(cluster_count)
            if cluster_count > 1 else 0.0
        )
        z_score = statistics.NormalDist().inv_cdf(
            0.5 + DEFAULT_WIN_RATE_CONFIDENCE_LEVEL / 2.0
        )
        cluster_win_rate_lower = max(
            0.0,
            cluster_balanced_win_rate
            - z_score * cluster_win_rate_standard_error,
        )
        cluster_win_rate_upper = min(
            1.0,
            cluster_balanced_win_rate
            + z_score * cluster_win_rate_standard_error,
        )

    complete_cluster_keys = missing_cluster_key_count == 0
    unique_cluster_gate_met = cluster_count >= minimum_cluster_count
    effective_cluster_gate_met = (
        effective_cluster_count + 1e-12 >= minimum_cluster_count
    )
    historical_reference_met = bool(
        cluster_balanced_win_rate is not None
        and cluster_balanced_win_rate * 100.0
        >= historical_reference_win_rate_pct
    )
    positive_win_rate_edge_met = bool(
        cluster_win_rate_lower is not None
        and cluster_win_rate_lower * 100.0 > 50.0
    )
    positive_average_return_met = bool(
        cluster_balanced_average_return is not None
        and cluster_balanced_average_return > 0.0
    )
    cluster_guardrail_met = all((
        complete_cluster_keys,
        unique_cluster_gate_met,
        effective_cluster_gate_met,
        historical_reference_met,
        positive_win_rate_edge_met,
        positive_average_return_met,
    ))
    if not materialized:
        status = "insufficient_completed_lifecycles"
    elif not complete_cluster_keys:
        status = "missing_performance_cluster_keys"
    elif not unique_cluster_gate_met:
        status = "insufficient_independent_clusters"
    elif not effective_cluster_gate_met:
        status = "performance_cluster_concentration_too_high"
    elif not positive_average_return_met:
        status = "cluster_balanced_return_below_break_even"
    elif not historical_reference_met:
        status = "cluster_balanced_win_rate_below_reference"
    elif not positive_win_rate_edge_met:
        status = "cluster_win_rate_statistically_uncertain"
    else:
        status = "cluster_guardrail_met"
    largest_cluster_size = max(cluster_sizes, default=0)
    return {
        "status": status,
        "cluster_unit": FORWARD_PERFORMANCE_CLUSTER_UNIT,
        "completed_trade_count": len(materialized),
        "clustered_trade_count": clustered_trade_count,
        "missing_cluster_key_count": missing_cluster_key_count,
        "cluster_count": cluster_count,
        "minimum_cluster_count": minimum_cluster_count,
        "unique_cluster_gate_met": unique_cluster_gate_met,
        "herfindahl_effective_cluster_count": round(
            effective_cluster_count,
            4,
        ),
        "minimum_effective_cluster_count": minimum_cluster_count,
        "effective_cluster_gate_met": effective_cluster_gate_met,
        "largest_cluster_size": largest_cluster_size,
        "largest_cluster_share_pct": round(
            largest_cluster_size / clustered_trade_count * 100.0,
            4,
        ) if clustered_trade_count else None,
        "cluster_size_herfindahl_index": round(
            sum(
                (size / clustered_trade_count) ** 2
                for size in cluster_sizes
            ),
            6,
        ) if clustered_trade_count else None,
        "cluster_balanced_win_rate_pct": round(
            cluster_balanced_win_rate * 100.0,
            4,
        ) if cluster_balanced_win_rate is not None else None,
        "cluster_balanced_win_rate_standard_error_pct": round(
            cluster_win_rate_standard_error * 100.0,
            4,
        ) if cluster_win_rate_standard_error is not None else None,
        "cluster_balanced_win_rate_95_lower_pct": round(
            cluster_win_rate_lower * 100.0,
            4,
        ) if cluster_win_rate_lower is not None else None,
        "cluster_balanced_win_rate_95_upper_pct": round(
            cluster_win_rate_upper * 100.0,
            4,
        ) if cluster_win_rate_upper is not None else None,
        "historical_reference_win_rate_pct": (
            historical_reference_win_rate_pct
        ),
        "historical_reference_win_rate_met": historical_reference_met,
        "positive_win_rate_edge_rule": (
            "cluster_balanced_normal_95_lower_pct > 50"
        ),
        "positive_win_rate_edge_met": positive_win_rate_edge_met,
        "cluster_balanced_average_net_return_pct": round(
            cluster_balanced_average_return,
            4,
        ) if cluster_balanced_average_return is not None else None,
        "positive_average_net_return_met": positive_average_return_met,
        "cluster_guardrail_met": cluster_guardrail_met,
        "clusters": cluster_rows,
    }


def _expected_weekday_dates(start: date, cutoff: date) -> list[str]:
    expected: list[str] = []
    current = start
    while current <= cutoff:
        if current.weekday() < 5:
            expected.append(current.isoformat())
        current = date.fromordinal(current.toordinal() + 1)
    return expected


def _portfolio_assessment(
    daily_equity_rows: Iterable[Mapping[str, Any]],
    trade_rows: Iterable[Mapping[str, Any]],
    *,
    account_baseline: Mapping[str, Any] | None,
    cohort_start: date,
    cutoff: date,
    expected_operating_dates: Iterable[str] | None,
    maximum_drawdown_pct: float,
    minimum_return_to_drawdown_ratio: float,
) -> dict[str, Any]:
    """Validate and summarize a NiuOne-only daily account curve."""
    baseline = (
        dict(account_baseline)
        if isinstance(account_baseline, Mapping) else {}
    )
    baseline_status = str(baseline.get("status") or "missing")
    baseline_captured_at = str(baseline.get("captured_at") or "")
    try:
        baseline_capture_date = _date_value(
            baseline_captured_at,
            field_name="account baseline captured_at",
        )
    except ValueError:
        baseline_capture_date = None
    baseline_equity = _number(baseline.get("total_equity"))
    baseline_cash = _number(baseline.get("cash"))
    baseline_initial_cash = _number(baseline.get("initial_cash"))
    baseline_account_created_at = str(
        baseline.get("account_created_at") or ""
    ).strip()
    open_position_count = _optional_quantity(
        baseline.get("open_position_count")
    )
    non_niuone_position_count = _optional_quantity(
        baseline.get("non_niuone_position_count")
    )
    unknown_position_strategy_count = _optional_quantity(
        baseline.get("unknown_position_strategy_count")
    )
    clean_zero_position_boundary = bool(
        baseline.get("clean_zero_position_boundary") is True
        and open_position_count == 0
        and non_niuone_position_count == 0
        and unknown_position_strategy_count == 0
        and baseline_equity is not None
        and baseline_equity > 0
        and baseline_cash is not None
        and math.isclose(
            baseline_cash,
            baseline_equity,
            rel_tol=0.0,
            abs_tol=0.05,
        )
        and baseline_initial_cash is not None
        and baseline_initial_cash > 0
        and _valid_timestamp(baseline_account_created_at)
        and baseline_capture_date is not None
        and baseline_capture_date < cohort_start
    )

    non_niuone_trade_count = 0
    unknown_strategy_trade_count = 0
    for trade in trade_rows:
        if not isinstance(trade, Mapping):
            continue
        action = str(trade.get("action") or "").upper()
        if action not in {"BUY", "SELL"}:
            continue
        try:
            trade_date = _date_value(
                trade.get("time"),
                field_name="portfolio trade time",
            )
        except ValueError:
            continue
        if trade_date < cohort_start or trade_date > cutoff:
            continue
        strategy_id = _strategy_id(trade)
        if not strategy_id:
            unknown_strategy_trade_count += 1
        elif strategy_id not in NIUONE_STRATEGY_IDS:
            non_niuone_trade_count += 1
    account_trade_isolation_met = not (
        non_niuone_trade_count or unknown_strategy_trade_count
    )

    invalid_fields: dict[str, int] = defaultdict(int)
    duplicate_date_count = 0
    outside_cohort_row_count = 0
    durable_row_count = 0
    materialized_by_date: dict[str, dict[str, Any]] = {}
    for raw in daily_equity_rows:
        if not isinstance(raw, Mapping):
            invalid_fields["daily_equity.row"] += 1
            continue
        try:
            row_date = _date_value(
                raw.get("date") or raw.get("time"),
                field_name="daily equity date",
            )
        except ValueError:
            invalid_fields["daily_equity.date"] += 1
            continue
        if row_date < cohort_start or row_date > cutoff:
            outside_cohort_row_count += 1
            continue
        date_text = row_date.isoformat()
        if date_text in materialized_by_date:
            duplicate_date_count += 1
        if raw.get("_forward_payload_available") is True:
            durable_row_count += 1
        else:
            invalid_fields["daily_equity.durable_payload"] += 1
        equity = _number(raw.get("equity"))
        cash = _number(raw.get("cash"))
        market_value = _number(raw.get("market_value"))
        pnl_pct = _number(raw.get("pnl_pct"))
        account_created_at = str(
            raw.get("account_created_at") or ""
        ).strip()
        created_at = str(raw.get("created_at") or raw.get("time") or "")
        valid = True
        if equity is None or equity <= 0:
            invalid_fields["daily_equity.equity"] += 1
            valid = False
        if cash is None or cash < 0:
            invalid_fields["daily_equity.cash"] += 1
            valid = False
        if market_value is None or market_value < 0:
            invalid_fields["daily_equity.market_value"] += 1
            valid = False
        if pnl_pct is None:
            invalid_fields["daily_equity.pnl_pct"] += 1
            valid = False
        if (
            not _valid_timestamp(account_created_at)
            or account_created_at != baseline_account_created_at
        ):
            invalid_fields["daily_equity.account_session"] += 1
            valid = False
        try:
            created = datetime.fromisoformat(
                created_at.replace("Z", "+00:00")
            )
        except ValueError:
            created = None
            invalid_fields["daily_equity.created_at"] += 1
            valid = False
        if created is not None and created.date() != row_date:
            invalid_fields["daily_equity.created_at_date"] += 1
            valid = False
        closing_snapshot = bool(
            created is not None
            and (created.hour, created.minute) >= (15, 0)
        )
        if not closing_snapshot:
            invalid_fields["daily_equity.closing_snapshot"] += 1
            valid = False
        if (
            equity is not None
            and cash is not None
            and market_value is not None
            and not math.isclose(
                equity,
                cash + market_value,
                rel_tol=0.0,
                abs_tol=0.05,
            )
        ):
            invalid_fields["daily_equity.accounting_identity"] += 1
            valid = False
        if (
            equity is not None
            and pnl_pct is not None
            and baseline_initial_cash is not None
            and baseline_initial_cash > 0
            and not math.isclose(
                pnl_pct,
                (equity / baseline_initial_cash - 1.0) * 100.0,
                rel_tol=0.0,
                abs_tol=0.011,
            )
        ):
            invalid_fields["daily_equity.initial_cash_continuity"] += 1
            valid = False
        materialized_by_date[date_text] = {
            "date": date_text,
            "equity": equity,
            "cash": cash,
            "market_value": market_value,
            "pnl_pct": pnl_pct,
            "created_at": created_at,
            "closing_snapshot": closing_snapshot,
            "valid": valid,
        }

    expected_dates = (
        sorted({str(value)[:10] for value in expected_operating_dates})
        if expected_operating_dates is not None
        else _expected_weekday_dates(cohort_start, cutoff)
    )
    observed_dates = sorted(
        date_text
        for date_text, row in materialized_by_date.items()
        if row["valid"]
    )
    missing_dates = sorted(set(expected_dates) - set(observed_dates))
    daily_equity_quality_gate_met = bool(expected_dates) and not (
        invalid_fields or duplicate_date_count or missing_dates
    )
    structural_evidence_available = bool(
        baseline_status in {"captured", "new_account_default"}
        and clean_zero_position_boundary
        and account_trade_isolation_met
        and daily_equity_quality_gate_met
    )

    total_return_pct: float | None = None
    max_drawdown_pct: float | None = None
    peak_date: str | None = None
    trough_date: str | None = None
    if structural_evidence_available and baseline_equity is not None:
        curve = [("baseline", baseline_equity)] + [
            (date_text, float(materialized_by_date[date_text]["equity"]))
            for date_text in observed_dates
        ]
        total_return_pct = round(
            (curve[-1][1] / baseline_equity - 1.0) * 100.0,
            4,
        )
        running_peak = curve[0][1]
        running_peak_date = curve[0][0]
        worst_drawdown = 0.0
        worst_peak_date = running_peak_date
        worst_trough_date = running_peak_date
        for point_date, equity in curve:
            if equity > running_peak:
                running_peak = equity
                running_peak_date = point_date
            drawdown = (equity / running_peak - 1.0) * 100.0
            if drawdown < worst_drawdown:
                worst_drawdown = drawdown
                worst_peak_date = running_peak_date
                worst_trough_date = point_date
        max_drawdown_pct = round(worst_drawdown, 4)
        peak_date = worst_peak_date
        trough_date = worst_trough_date

    positive_return_met = bool(
        total_return_pct is not None and total_return_pct > 0.0
    )
    drawdown_guardrail_met = bool(
        max_drawdown_pct is not None
        and abs(max_drawdown_pct) <= maximum_drawdown_pct
    )
    return_to_drawdown_ratio: float | None = None
    return_to_drawdown_ratio_is_infinite = False
    if total_return_pct is not None and max_drawdown_pct is not None:
        if max_drawdown_pct < 0:
            return_to_drawdown_ratio = round(
                total_return_pct / abs(max_drawdown_pct),
                4,
            )
        elif total_return_pct > 0:
            return_to_drawdown_ratio_is_infinite = True
    return_to_drawdown_guardrail_met = bool(
        return_to_drawdown_ratio_is_infinite
        or (
            return_to_drawdown_ratio is not None
            and return_to_drawdown_ratio
            >= minimum_return_to_drawdown_ratio
        )
    )
    portfolio_guardrail_met = all((
        structural_evidence_available,
        positive_return_met,
        drawdown_guardrail_met,
        return_to_drawdown_guardrail_met,
    ))
    if baseline_status not in {"captured", "new_account_default"}:
        status = "missing_pre_cohort_account_baseline"
    elif not clean_zero_position_boundary:
        status = "contaminated_pre_cohort_account_boundary"
    elif not account_trade_isolation_met:
        status = "non_niuone_account_activity_detected"
    elif not daily_equity_quality_gate_met:
        status = "daily_equity_quality_blocked"
    elif not portfolio_guardrail_met:
        status = "portfolio_guardrail_not_met"
    else:
        status = "portfolio_guardrail_met"
    return {
        "status": status,
        "portfolio_return_and_drawdown_evidence_available": (
            structural_evidence_available
        ),
        "baseline_status": baseline_status,
        "baseline_captured_at": baseline_captured_at or None,
        "baseline_total_equity": (
            round(baseline_equity, 2)
            if baseline_equity is not None else None
        ),
        "baseline_initial_cash": (
            round(baseline_initial_cash, 2)
            if baseline_initial_cash is not None else None
        ),
        "clean_zero_position_boundary": clean_zero_position_boundary,
        "open_position_count": open_position_count,
        "non_niuone_position_count": non_niuone_position_count,
        "unknown_position_strategy_count": (
            unknown_position_strategy_count
        ),
        "account_trade_isolation_met": account_trade_isolation_met,
        "non_niuone_trade_count": non_niuone_trade_count,
        "unknown_strategy_trade_count": unknown_strategy_trade_count,
        "expected_daily_equity_date_count": len(expected_dates),
        "observed_daily_equity_date_count": len(observed_dates),
        "durable_daily_equity_row_count": durable_row_count,
        "missing_daily_equity_dates": missing_dates,
        "duplicate_daily_equity_date_count": duplicate_date_count,
        "outside_cohort_daily_equity_row_count": outside_cohort_row_count,
        "invalid_daily_equity_fields": dict(sorted(invalid_fields.items())),
        "daily_equity_quality_gate_met": daily_equity_quality_gate_met,
        "portfolio_return_pct": total_return_pct,
        "maximum_drawdown_pct": max_drawdown_pct,
        "maximum_drawdown_peak_date": peak_date,
        "maximum_drawdown_trough_date": trough_date,
        "positive_portfolio_return_met": positive_return_met,
        "maximum_drawdown_limit_pct": maximum_drawdown_pct,
        "maximum_drawdown_guardrail_met": drawdown_guardrail_met,
        "return_to_drawdown_ratio": return_to_drawdown_ratio,
        "return_to_drawdown_ratio_is_infinite": (
            return_to_drawdown_ratio_is_infinite
        ),
        "minimum_return_to_drawdown_ratio": (
            minimum_return_to_drawdown_ratio
        ),
        "return_to_drawdown_guardrail_met": (
            return_to_drawdown_guardrail_met
        ),
        "portfolio_guardrail_met": portfolio_guardrail_met,
    }


def _performance_assessment(
    summary: Mapping[str, Any],
    *,
    clustering: Mapping[str, Any],
    portfolio_assessment: Mapping[str, Any],
    data_quality_gate_met: bool,
    minimum_completed_trades: int,
    historical_reference_win_rate_pct: float,
) -> dict[str, Any]:
    """Separate review timing from evidence for a performance claim."""
    completed = int(summary.get("completed_trade_count") or 0)
    observed_win_rate = _number(summary.get("win_rate_pct"))
    lower_win_rate = _number(
        summary.get("win_rate_wilson_95_lower_pct")
    )
    average_return = _number(summary.get("average_net_return_pct"))
    realized_pnl = _number(summary.get("realized_pnl"))
    profit_factor = _number(summary.get("profit_factor"))
    performance_sample_gate_met = completed >= minimum_completed_trades
    historical_reference_met = bool(
        observed_win_rate is not None
        and observed_win_rate >= historical_reference_win_rate_pct
    )
    positive_win_rate_edge_met = bool(
        lower_win_rate is not None and lower_win_rate > 50.0
    )
    positive_average_return_met = bool(
        average_return is not None and average_return > 0.0
    )
    positive_realized_pnl_met = bool(
        realized_pnl is not None and realized_pnl > 0.0
    )
    profit_factor_above_break_even = bool(
        summary.get("profit_factor_is_infinite") is True
        or (profit_factor is not None and profit_factor > 1.0)
    )
    return_quality_guardrail_met = all((
        positive_average_return_met,
        positive_realized_pnl_met,
        profit_factor_above_break_even,
    ))
    trade_level_lifecycle_performance_criteria_met = all((
        performance_sample_gate_met,
        data_quality_gate_met,
        historical_reference_met,
        positive_win_rate_edge_met,
        return_quality_guardrail_met,
    ))
    cluster_guardrail_met = bool(
        clustering.get("cluster_guardrail_met") is True
    )
    lifecycle_performance_criteria_met = all((
        trade_level_lifecycle_performance_criteria_met,
        cluster_guardrail_met,
    ))
    portfolio_evidence_available = bool(
        portfolio_assessment.get(
            "portfolio_return_and_drawdown_evidence_available"
        ) is True
    )
    portfolio_guardrail_met = bool(
        portfolio_assessment.get("portfolio_guardrail_met") is True
    )
    performance_criteria_met_before_operations = all((
        lifecycle_performance_criteria_met,
        portfolio_evidence_available,
        portfolio_guardrail_met,
    ))
    if not performance_sample_gate_met:
        status = "insufficient_completed_lifecycles"
        decision = "continue_collecting_completed_lifecycles"
    elif not data_quality_gate_met:
        status = "data_quality_blocked"
        decision = "repair_forward_attribution_before_performance_review"
    elif not return_quality_guardrail_met:
        status = "return_quality_below_break_even"
        decision = "do_not_claim_high_win_rate_or_positive_return"
    elif not historical_reference_met:
        status = "historical_win_rate_not_reproduced"
        decision = "review_stage_and_exit_diagnostics_without_promotion"
    elif not positive_win_rate_edge_met:
        status = "win_rate_statistically_uncertain"
        decision = "continue_collecting_without_promotion"
    elif not cluster_guardrail_met:
        status = str(
            clustering.get("status")
            or "performance_cluster_guardrail_not_met"
        )
        decision = "collect_more_independent_mainline_clusters"
    elif not portfolio_evidence_available:
        status = "portfolio_evidence_blocked"
        decision = "repair_daily_account_curve_before_performance_review"
    elif not portfolio_guardrail_met:
        status = "portfolio_guardrail_not_met"
        decision = "do_not_claim_positive_risk_adjusted_return"
    else:
        status = "pending_operations_review"
        decision = "verify_operations_and_opportunity_coverage"
    return {
        "status": status,
        "decision": decision,
        "minimum_completed_lifecycles": minimum_completed_trades,
        "performance_sample_gate_met": performance_sample_gate_met,
        "historical_reference_win_rate_pct": (
            historical_reference_win_rate_pct
        ),
        "historical_reference_win_rate_met": historical_reference_met,
        "win_rate_confidence_level": DEFAULT_WIN_RATE_CONFIDENCE_LEVEL,
        "positive_win_rate_edge_rule": "wilson_95_lower_pct > 50",
        "positive_win_rate_edge_met": positive_win_rate_edge_met,
        "positive_average_net_return_met": positive_average_return_met,
        "positive_realized_pnl_met": positive_realized_pnl_met,
        "profit_factor_above_break_even_met": (
            profit_factor_above_break_even
        ),
        "return_quality_guardrail_rule": (
            "average_net_return_pct > 0 AND realized_pnl > 0 AND "
            "profit_factor > 1"
        ),
        "return_quality_guardrail_met": return_quality_guardrail_met,
        "trade_level_lifecycle_performance_criteria_met": (
            trade_level_lifecycle_performance_criteria_met
        ),
        "performance_cluster_guardrail_met": cluster_guardrail_met,
        "lifecycle_performance_criteria_met": (
            lifecycle_performance_criteria_met
        ),
        "portfolio_return_and_drawdown_evidence_available": (
            portfolio_evidence_available
        ),
        "portfolio_guardrail_met": portfolio_guardrail_met,
        "performance_criteria_met_before_operations": (
            performance_criteria_met_before_operations
        ),
        "high_win_rate_and_positive_return_claim_supported": (
            False
        ),
        "positive_risk_adjusted_portfolio_return_supported": False,
        "high_portfolio_return_claim_supported": False,
        "automatic_promotion_allowed": False,
    }


def _group_summary(
    rows: Iterable[Mapping[str, Any]],
    key,
) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(key(row))].append(row)
    return {
        name: _metric_summary(group)
        for name, group in sorted(groups.items())
    }


def _holding_lifecycle_summary(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    materialized = list(rows)
    transition_counts: dict[str, int] = defaultdict(int)
    path_groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    reached_counts = {
        stage: 0 for stage in NIUONE_LIFECYCLE_STAGES
    }
    evidence_count = 0
    complete_count = 0
    for row in materialized:
        evidence = row.get("exit_context")
        if not isinstance(evidence, Mapping) or not evidence:
            continue
        evidence_count += 1
        if not _holding_lifecycle_attribution_gaps(row):
            complete_count += 1
        sequence = evidence.get("stage_sequence")
        stages = (
            [str(value or "") for value in sequence]
            if isinstance(sequence, list) else []
        )
        valid_stages = [
            stage for stage in stages
            if stage in NIUONE_LIFECYCLE_STAGES
        ]
        for stage in set(valid_stages):
            reached_counts[stage] += 1
        for left, right in zip(valid_stages, valid_stages[1:]):
            transition_counts[f"{left}->{right}"] += 1
        pattern = "->".join(valid_stages) or "missing"
        path_groups[pattern].append(row)
    denominator = len(materialized)
    return {
        "completed_lifecycle_count": denominator,
        "exit_evidence_count": evidence_count,
        "complete_path_count": complete_count,
        "complete_path_coverage_pct": (
            round(complete_count / denominator * 100.0, 4)
            if denominator else None
        ),
        "stage_reach": {
            stage: {
                "count": reached_counts[stage],
                "pct_of_completed": (
                    round(reached_counts[stage] / denominator * 100.0, 4)
                    if denominator else None
                ),
            }
            for stage in NIUONE_LIFECYCLE_STAGES
        },
        "transitions": dict(sorted(transition_counts.items())),
        "path_patterns": {
            name: _metric_summary(group)
            for name, group in sorted(path_groups.items())
        },
    }


def evaluate_niuone_forward(
    trades: Iterable[Mapping[str, Any]],
    *,
    decision_rows: Iterable[Mapping[str, Any]] = (),
    daily_equity_rows: Iterable[Mapping[str, Any]] = (),
    account_baseline: Mapping[str, Any] | None = None,
    expected_operating_dates: Iterable[str] | None = None,
    cohort_start: str | date = DEFAULT_COHORT_START,
    as_of: str | date | None = None,
    minimum_completed_trades: int = DEFAULT_MIN_COMPLETED_TRADES,
    minimum_calendar_months: int = DEFAULT_MIN_CALENDAR_MONTHS,
    shadow_execution_gap_pct: float = DEFAULT_SHADOW_EXECUTION_GAP_PCT,
    shadow_recovery_ratio_cap: float = DEFAULT_SHADOW_RECOVERY_RATIO_CAP,
    historical_reference_win_rate_pct: float = (
        DEFAULT_HISTORICAL_REFERENCE_WIN_RATE_PCT
    ),
    maximum_portfolio_drawdown_pct: float = (
        DEFAULT_MAX_PORTFOLIO_DRAWDOWN_PCT
    ),
    minimum_return_to_drawdown_ratio: float = (
        DEFAULT_MIN_RETURN_TO_DRAWDOWN_RATIO
    ),
) -> dict[str, Any]:
    """Evaluate only complete, post-cohort NiuOne position lifecycles.

    A lifecycle begins when a code moves from zero shares to a BUY position and
    ends only when cumulative SELL shares return it to zero.  This preserves
    partial exits and upgrades instead of treating every SELL as an independent
    trade.  The function is read-only and never infers missing opening trades.
    """
    start = _date_value(cohort_start, field_name="cohort_start")
    cutoff = _date_value(as_of or date.today(), field_name="as_of")
    resolved_expected_operating_dates = (
        sorted({str(value)[:10] for value in expected_operating_dates})
        if expected_operating_dates is not None
        else _expected_weekday_dates(start, cutoff)
    )
    trade_rows = list(trades)
    opportunities = summarize_niuone_forward_opportunities(
        decision_rows,
        execution_rows=trade_rows,
        cohort_start=start,
        as_of=cutoff,
    )
    if minimum_completed_trades <= 0:
        raise ValueError("minimum_completed_trades must be positive")
    if minimum_calendar_months <= 0:
        raise ValueError("minimum_calendar_months must be positive")
    if shadow_execution_gap_pct < 0:
        raise ValueError("shadow_execution_gap_pct cannot be negative")
    if shadow_recovery_ratio_cap <= 0:
        raise ValueError("shadow_recovery_ratio_cap must be positive")
    if not 0.0 < historical_reference_win_rate_pct < 100.0:
        raise ValueError(
            "historical_reference_win_rate_pct must be between zero and 100"
        )
    if maximum_portfolio_drawdown_pct <= 0:
        raise ValueError("maximum_portfolio_drawdown_pct must be positive")
    if minimum_return_to_drawdown_ratio <= 0:
        raise ValueError(
            "minimum_return_to_drawdown_ratio must be positive"
        )

    normalized: list[tuple[date, int, Mapping[str, Any]]] = []
    seen_trade_ids: set[tuple[str, ...]] = set()
    duplicate_trade_count = 0
    invalid_timestamp_count = 0
    for index, trade in enumerate(trade_rows):
        if not isinstance(trade, Mapping):
            invalid_timestamp_count += 1
            continue
        identity = _trade_identity(trade)
        if identity in seen_trade_ids:
            duplicate_trade_count += 1
            continue
        seen_trade_ids.add(identity)
        try:
            trade_date = _date_value(trade.get("time"), field_name="trade time")
        except ValueError:
            invalid_timestamp_count += 1
            continue
        if trade_date <= cutoff:
            normalized.append((trade_date, index, trade))
    normalized.sort(key=lambda item: (item[0], str(item[2].get("time") or ""), item[1]))

    active: dict[str, dict[str, Any]] = {}
    completed: list[dict[str, Any]] = []
    orphan_sell_count = 0
    invalid_trade_count = 0
    oversold_lifecycle_count = 0
    unverified_open_count = 0
    inconsistent_quantity_count = 0
    for trade_date, _index, trade in normalized:
        action = str(trade.get("action") or "").upper()
        code = str(trade.get("code") or "").strip()
        if action not in {"BUY", "SELL"} or not code:
            continue
        quantity = _shares(trade.get("shares"))
        if quantity <= 0:
            invalid_trade_count += 1
            continue
        if action == "BUY":
            cost = _buy_cost(trade)
            if cost is None:
                invalid_trade_count += 1
                continue
            before_quantity = _optional_quantity(
                trade.get("position_before_qty")
            )
            after_quantity = _optional_quantity(
                trade.get("position_after_qty")
            )
            lifecycle = active.get(code)
            if lifecycle is None:
                verified_open = before_quantity == 0
                if not verified_open:
                    unverified_open_count += 1
                lifecycle = {
                    "entry_date": trade_date,
                    "entry_time": str(trade.get("time") or ""),
                    "entry_strategy": _strategy_id(trade),
                    "entry_context": _entry_context(trade),
                    "entry_fill_shares": quantity,
                    "entry_payload_available": trade.get(
                        "_forward_payload_available"
                    ),
                    "exit_context": {},
                    "exit_payload_available": None,
                    "quantity": before_quantity or 0,
                    "buy_cost": 0.0,
                    "sell_proceeds": 0.0,
                    "transaction_count": 0,
                    "verified_open": verified_open,
                }
                active[code] = lifecycle
            elif (
                before_quantity is not None
                and before_quantity != int(lifecycle["quantity"])
            ):
                lifecycle["verified_open"] = False
                inconsistent_quantity_count += 1
            expected_after = int(lifecycle["quantity"]) + quantity
            if after_quantity is not None and after_quantity != expected_after:
                lifecycle["verified_open"] = False
                inconsistent_quantity_count += 1
            lifecycle["quantity"] = (
                after_quantity if after_quantity is not None else expected_after
            )
            lifecycle["buy_cost"] += cost
            lifecycle["transaction_count"] += 1
            continue

        lifecycle = active.get(code)
        if lifecycle is None:
            orphan_sell_count += 1
            continue
        proceeds = _sell_proceeds(trade)
        if proceeds is None:
            invalid_trade_count += 1
            continue
        before_quantity = _optional_quantity(trade.get("position_before_qty"))
        after_quantity = _optional_quantity(trade.get("position_after_qty"))
        if (
            before_quantity is not None
            and before_quantity != int(lifecycle["quantity"])
        ):
            lifecycle["verified_open"] = False
            inconsistent_quantity_count += 1
        if quantity > int(lifecycle["quantity"]):
            oversold_lifecycle_count += 1
            active.pop(code, None)
            continue
        expected_after = int(lifecycle["quantity"]) - quantity
        if after_quantity is not None and after_quantity != expected_after:
            lifecycle["verified_open"] = False
            inconsistent_quantity_count += 1
        lifecycle["quantity"] = (
            after_quantity if after_quantity is not None else expected_after
        )
        lifecycle["sell_proceeds"] += proceeds
        lifecycle["transaction_count"] += 1
        lifecycle["exit_context"] = _exit_context(trade)
        lifecycle["exit_payload_available"] = trade.get(
            "_forward_payload_available"
        )
        if lifecycle["quantity"] > 0:
            continue

        active.pop(code, None)
        entry_date = lifecycle["entry_date"]
        entry_strategy = str(lifecycle["entry_strategy"] or "")
        buy_cost = float(lifecycle["buy_cost"])
        if (
            entry_date < start
            or entry_strategy not in NIUONE_STRATEGY_IDS
            or buy_cost <= 0
            or lifecycle["verified_open"] is not True
        ):
            continue
        realized_pnl = float(lifecycle["sell_proceeds"]) - buy_cost
        context = lifecycle["entry_context"]
        completed.append({
            "entry_date": entry_date.isoformat(),
            "entry_time": lifecycle["entry_time"],
            "exit_date": trade_date.isoformat(),
            "exit_time": str(trade.get("time") or ""),
            "entry_strategy": entry_strategy,
            "net_return_pct": realized_pnl / buy_cost * 100.0,
            "realized_pnl": realized_pnl,
            "holding_calendar_days": (trade_date - entry_date).days,
            "transaction_count": int(lifecycle["transaction_count"]),
            "entry_context": context,
            "entry_fill_shares": lifecycle["entry_fill_shares"],
            "entry_payload_available": lifecycle[
                "entry_payload_available"
            ],
            "exit_context": lifecycle["exit_context"],
            "exit_payload_available": lifecycle[
                "exit_payload_available"
            ],
            "required_holding_dates": [
                value for value in resolved_expected_operating_dates
                if entry_date.isoformat() <= value <= trade_date.isoformat()
            ],
        })

    elapsed_days = max(0, (cutoff - start).days)
    elapsed_months = _full_months(start, cutoff)
    trade_gate_met = len(completed) >= minimum_completed_trades
    time_gate_met = elapsed_months >= minimum_calendar_months
    sample_gate_met = trade_gate_met or time_gate_met
    entry_attribution_gaps = [
        _entry_attribution_gaps(row) for row in completed
    ]
    complete_entry_attribution_count = sum(
        not gaps for gaps in entry_attribution_gaps
    )
    missing_entry_attribution_fields: dict[str, int] = defaultdict(int)
    for gaps in entry_attribution_gaps:
        for field in gaps:
            missing_entry_attribution_fields[field] += 1
    lifecycle_attribution_gaps = [
        _lifecycle_attribution_gaps(row) for row in completed
    ]
    complete_attribution_count = sum(
        not gaps for gaps in lifecycle_attribution_gaps
    )
    missing_attribution_fields: dict[str, int] = defaultdict(int)
    for gaps in lifecycle_attribution_gaps:
        for field in gaps:
            missing_attribution_fields[field] += 1
    data_quality_gate_met = complete_attribution_count == len(completed)
    evidence_gate_met = sample_gate_met and data_quality_gate_met
    overall_metrics = _metric_summary(completed)
    performance_clustering = _performance_cluster_summary(
        completed,
        minimum_cluster_count=minimum_completed_trades,
        historical_reference_win_rate_pct=(
            historical_reference_win_rate_pct
        ),
    )
    portfolio_assessment = _portfolio_assessment(
        daily_equity_rows,
        trade_rows,
        account_baseline=account_baseline,
        cohort_start=start,
        cutoff=cutoff,
        expected_operating_dates=resolved_expected_operating_dates,
        maximum_drawdown_pct=maximum_portfolio_drawdown_pct,
        minimum_return_to_drawdown_ratio=(
            minimum_return_to_drawdown_ratio
        ),
    )
    performance_assessment = _performance_assessment(
        overall_metrics,
        clustering=performance_clustering,
        portfolio_assessment=portfolio_assessment,
        data_quality_gate_met=data_quality_gate_met,
        minimum_completed_trades=minimum_completed_trades,
        historical_reference_win_rate_pct=(
            historical_reference_win_rate_pct
        ),
    )
    status = (
        "pre_start" if cutoff < start
        else "data_quality_blocked"
        if sample_gate_met and not data_quality_gate_met
        else "ready_for_manual_review" if evidence_gate_met
        else "collecting"
    )

    def context_value(row: Mapping[str, Any], field: str) -> Any:
        context = row.get("entry_context")
        return context.get(field) if isinstance(context, Mapping) else None

    def exit_context_value(row: Mapping[str, Any], field: str) -> Any:
        context = row.get("exit_context")
        return context.get(field) if isinstance(context, Mapping) else None

    def lifecycle_path_pattern(row: Mapping[str, Any]) -> str:
        sequence = exit_context_value(row, "stage_sequence")
        if not isinstance(sequence, list):
            return "missing"
        stages = [
            str(stage or "") for stage in sequence
            if str(stage or "") in NIUONE_LIFECYCLE_STAGES
        ]
        return "->".join(stages) or "missing"

    def score_bin(row: Mapping[str, Any]) -> str:
        score = _number(context_value(row, "entry_signal_score"))
        if score is None:
            return "missing"
        if score >= 9.0:
            return "ge_9_0"
        if score >= 8.5:
            return "8_5_to_9_0"
        return "lt_8_5"

    def rank_bin(row: Mapping[str, Any]) -> str:
        rank = _number(context_value(row, "entry_same_stage_candidate_rank"))
        if rank is None:
            return "missing"
        return "rank_1" if rank == 1 else "rank_2_plus"

    def gap_bin(row: Mapping[str, Any]) -> str:
        gap = _number(context_value(row, "entry_execution_gap_pct"))
        if gap is None:
            return "missing"
        return (
            f"le_{shadow_execution_gap_pct:g}_pct"
            if gap <= shadow_execution_gap_pct
            else f"gt_{shadow_execution_gap_pct:g}_pct"
        )

    def recovery_ratio_bin(row: Mapping[str, Any]) -> str:
        ratio = _number(
            context_value(row, "entry_daily_v_recovery_ratio")
        )
        if ratio is None:
            return "missing"
        return (
            f"lt_{shadow_recovery_ratio_cap:g}"
            if ratio < shadow_recovery_ratio_cap
            else f"ge_{shadow_recovery_ratio_cap:g}"
        )

    def risk_ceiling_utilization_bin(row: Mapping[str, Any]) -> str:
        utilization = _number(
            context_value(row, "entry_risk_ceiling_utilization_pct")
        )
        if utilization is None:
            return "missing"
        if utilization <= 25:
            return "le_25_pct"
        if utilization <= 50:
            return "25_to_50_pct"
        if utilization <= 75:
            return "50_to_75_pct"
        return "75_to_100_pct"

    entry_context_count = sum(
        bool(row.get("entry_context")) for row in completed
    )
    entry_signal_timestamp_count = sum(
        bool(context_value(row, "entry_signal_generated_at"))
        for row in completed
    )
    deferred_entry_count = sum(
        context_value(row, "entry_execution_mode") == "deferred"
        for row in completed
    )
    exit_lifecycle_evidence_count = sum(
        bool(row.get("exit_context")) for row in completed
    )
    complete_holding_lifecycle_path_count = sum(
        not _holding_lifecycle_attribution_gaps(row)
        for row in completed
    )
    holding_lifecycle = _holding_lifecycle_summary(completed)
    warnings: list[str] = []
    if orphan_sell_count:
        warnings.append(
            "Retained input contains SELL rows without an opening BUY; those "
            "rows were not reconstructed or counted."
        )
    if invalid_timestamp_count or invalid_trade_count or oversold_lifecycle_count:
        warnings.append(
            "Malformed or internally inconsistent trade rows were excluded."
        )
    if unverified_open_count or inconsistent_quantity_count:
        warnings.append(
            "Lifecycles without an explicit zero-share opening boundary, or "
            "with inconsistent before/after quantities, were not counted."
        )
    if duplicate_trade_count:
        warnings.append(
            "Exact duplicate fill rows were collapsed by the practice-ledger "
            "event identity before lifecycle reconstruction."
        )
    if not data_quality_gate_met:
        warnings.append(
            "Completed lifecycles with incomplete entry attribution or holding-"
            "stage/exit-stage paths remain in descriptive performance totals, "
            "but cannot advance the strict-forward evidence gate."
        )
    if not portfolio_assessment[
        "portfolio_return_and_drawdown_evidence_available"
    ]:
        warnings.append(
            "A complete, durable, closing daily-equity curve from a frozen "
            "zero-position pre-cohort account boundary is unavailable or "
            "contaminated; lifecycle PnL cannot support a portfolio-return "
            "claim."
        )

    rich_payload_count = sum(
        trade.get("_forward_payload_available") is True
        for _trade_date, _index, trade in normalized
    )
    legacy_payload_count = sum(
        trade.get("_forward_payload_available") is False
        for _trade_date, _index, trade in normalized
    )

    return {
        "protocol": {
            "version": FORWARD_PROTOCOL_VERSION,
            "cohort_start": start.isoformat(),
            "as_of": cutoff.isoformat(),
            "minimum_completed_trades": minimum_completed_trades,
            "minimum_calendar_months": minimum_calendar_months,
            "historical_reference_win_rate_pct": (
                historical_reference_win_rate_pct
            ),
            "win_rate_confidence_level": (
                DEFAULT_WIN_RATE_CONFIDENCE_LEVEL
            ),
            "evidence_gate_rule": (
                "(completed_trades >= minimum OR full_calendar_months >= minimum) "
                "AND complete_entry_and_holding_lifecycle_attribution = 100% "
                "AND complete_consistent_"
                "opportunity_funnel_and_execution_sizing_evidence = 100%"
            ),
            "performance_assessment_rule": (
                "completed_lifecycles >= minimum AND complete_entry_and_"
                "holding_lifecycle_attribution = 100% AND observed_win_rate "
                ">= historical_"
                "reference AND wilson_95_lower_win_rate > 50% AND average_"
                "net_return > 0 AND realized_pnl > 0 AND profit_factor > 1 "
                "AND unique(entry_date x entry_theme) >= minimum AND "
                "herfindahl_effective_clusters >= minimum AND cluster_"
                "balanced_win_rate >= historical_reference AND cluster_"
                "balanced_normal_95_lower_win_rate > 50% AND cluster_"
                "balanced_average_net_return > 0 "
                "AND clean_pre_cohort_account_boundary AND complete_closing_"
                "daily_equity = 100% AND portfolio_return > 0 AND maximum_"
                "drawdown <= limit AND return_to_drawdown >= minimum AND "
                "complete_operations_and_opportunity_evidence = 100%"
            ),
            "portfolio_unit_of_analysis": (
                "durable_daily_closing_mark_to_market_account_equity"
            ),
            "account_boundary_rule": (
                "pre_cohort_capture_date < cohort_start AND open_positions = 0"
            ),
            "portfolio_daily_coverage_rule": (
                "one durable post-close equity point per configured A-share "
                "operating day; cached exchange calendar with weekday fallback"
            ),
            "holding_lifecycle_daily_coverage_rule": (
                "entry_signal on entry day; at least one mainline_scan on "
                "each later configured operating day held; exit_fill on the "
                "closing day"
            ),
            "maximum_new_niuone_positions_per_trading_day": (
                NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY
            ),
            "daily_new_position_limit_rule": (
                "distinct durable NiuOne opening BUY codes per Beijing "
                "trading date; adds and non-NiuOne openings excluded"
            ),
            "niuone_reversal_minimum_recovery_ratio_inclusive": (
                NIUONE_DAILY_V_MIN_RECOVERY_RATIO
            ),
            "niuone_reversal_maximum_recovery_ratio_exclusive": (
                NIUONE_DAILY_V_MAX_RECOVERY_RATIO
            ),
            "niuone_reversal_recovery_rule": (
                "NiuOne reversal probes require daily-V recovery >= minimum "
                "and < maximum at every scoring and execution boundary"
            ),
            "niuone_reversal_minimum_strong_stock_count": (
                NIUONE_REVERSAL_CONTINUATION_MIN_STRONG_COUNT
            ),
            "niuone_reversal_minimum_state_streak": (
                NIUONE_REVERSAL_CONTINUATION_MIN_STATE_STREAK
            ),
            "niuone_reversal_continuation_rule": (
                "NiuOne reversal probes require either the minimum strong-"
                "stock breadth or the minimum consecutive brewing-state "
                "streak at scoring and execution boundaries"
            ),
            "niuone_reversal_daily_candidate_limit": (
                strategy_daily_candidate_limit("niu_reversal_probe")
            ),
            "niuone_reversal_absolute_position_cap_pct": (
                NIUONE_ABSOLUTE_POSITION_CAP_PCT["niu_reversal_probe"]
            ),
            "niuone_markup_upgrade_minimum_pnl_pct": (
                NIUONE_MARKUP_UPGRADE_MIN_PNL_PCT
            ),
            "niuone_markup_upgrade_maximum_pnl_pct": (
                NIUONE_MARKUP_UPGRADE_MAX_PNL_PCT
            ),
            "niuone_markup_early_upgrade_absolute_position_cap_pct": (
                NIUONE_MARKUP_EARLY_UPGRADE_POSITION_CAP_PCT
            ),
            "niuone_markup_upgrade_absolute_position_cap_pct": (
                NIUONE_MARKUP_UPGRADE_POSITION_CAP_PCT
            ),
            "niuone_markup_rebalance_pullback_atr": (
                NIUONE_MARKUP_REBALANCE_PULLBACK_ATR
            ),
            "niuone_markup_rebalance_stall_sessions": (
                NIUONE_MARKUP_REBALANCE_STALL_SESSIONS
            ),
            "niuone_markup_rebalance_stall_min_atr": (
                NIUONE_MARKUP_REBALANCE_STALL_MIN_ATR
            ),
            "niuone_markup_rebalance_rebound_atr": (
                NIUONE_MARKUP_REBALANCE_REBOUND_ATR
            ),
            "niuone_markup_rebalance_min_sessions_after_add": (
                NIUONE_MARKUP_REBALANCE_MIN_SESSIONS_AFTER_ADD
            ),
            "niuone_markup_rebalance_trim_ratio": (
                NIUONE_MARKUP_REBALANCE_TRIM_RATIO
            ),
            "niuone_markup_rebalance_lifetime_add_limit": None,
            "niuone_markup_upgrade_rule": (
                "Only profitable Probe/Launch holdings inside the configured "
                "PnL window, markup lifecycle stage, and a strong leading-"
                "tier stock may make the initial staged adds: persistent "
                "emerging leadership toward the early cap, then confirmed "
                "mainline leadership toward the final cap"
            ),
            "niuone_markup_rebalance_rule": (
                "After a profitable confirmed leader releases one third on "
                "a causal pullback or consolidation, it may replace the "
                "released risk only after price reclaims the rebound trigger, "
                "the lifecycle is markup, and strong leader status returns. "
                "Every filled re-entry starts a new independent cycle; there "
                "is no lifetime add-count limit. Climax, unrecovered "
                "divergence, and fade cannot add"
            ),
            "niuone_climax_partial_ratio": (
                NIUONE_LIFECYCLE_CLIMAX_PARTIAL_RATIO
            ),
            "niuone_climax_partial_minimum_pnl_pct": (
                NIUONE_LIFECYCLE_CLIMAX_MIN_PNL_PCT
            ),
            "niuone_climax_partial_rule": (
                "A non-losing holding entering lifecycle climax reduces one "
                "third once before existing breakeven and 2ATR protection"
            ),
            "niuone_leader_minimum_sector_rank_inclusive": (
                NIUONE_LEADER_MIN_SECTOR_RANK
            ),
            "niuone_leader_minimum_today_strength_inclusive": (
                NIUONE_TODAY_OBSERVATION_THRESHOLD
            ),
            "niuone_leader_quality_rule": (
                "NiuOne leading entries require both top-20% within-theme "
                "strength and same-day theme strength >= 60 at scoring and "
                "execution boundaries"
            ),
            "niuone_startup_allowed_mainline_states": ["emerging"],
            "niuone_startup_state_rule": (
                "NiuOne startup entries are limited to cross-day persistent "
                "emerging themes; confirmed mainlines must use leading"
            ),
            "lifecycle_entry_strategy_routes": {
                stage: list(
                    definition.get("allowed_entry_strategy_ids") or ()
                )
                for stage, definition in NIUONE_LIFECYCLE_STAGES.items()
            },
            "oversized_niuone_buy_rule": (
                "valid whole-lot NiuOne BUY requests above a positive "
                "deterministic risk ceiling execute at that ceiling; "
                "all other eligibility and risk checks remain fail-closed"
            ),
            "oversized_niuone_sell_rule": (
                "valid whole-lot model-directed NiuOne SELL requests above "
                "a positive whole-lot T+1 available quantity execute at "
                "that available quantity; zero or non-whole-lot availability "
                "and all non-NiuOne model SELL requests remain fail-closed"
            ),
            "performance_cluster_unit": (
                FORWARD_PERFORMANCE_CLUSTER_UNIT
            ),
            "minimum_unique_performance_clusters": (
                minimum_completed_trades
            ),
            "minimum_effective_performance_clusters": (
                minimum_completed_trades
            ),
            "performance_cluster_confidence_rule": (
                "unweighted_mean_cluster_win_rate_minus_normal_95_se > 50%"
            ),
            "maximum_portfolio_drawdown_pct": (
                maximum_portfolio_drawdown_pct
            ),
            "minimum_return_to_drawdown_ratio": (
                minimum_return_to_drawdown_ratio
            ),
            "unit_of_analysis": "complete_zero_to_zero_position_lifecycle",
            "opportunity_unit_of_analysis": opportunities[
                "unit_of_analysis"
            ],
            "candidate_evidence_schema_version": (
                FORWARD_CANDIDATE_EVIDENCE_SCHEMA_VERSION
            ),
            "execution_evidence_schema_version": (
                FORWARD_EXECUTION_EVIDENCE_SCHEMA_VERSION
            ),
            "sell_execution_evidence_schema_version": (
                FORWARD_SELL_EXECUTION_EVIDENCE_SCHEMA_VERSION
            ),
            "required_candidate_evidence_fields": list(
                FORWARD_REQUIRED_CANDIDATE_EVIDENCE_FIELDS
            ),
            "required_entry_context_fields": list(
                FORWARD_REQUIRED_ENTRY_CONTEXT_FIELDS
            ),
            "holding_lifecycle_evidence_schema_version": (
                FORWARD_HOLDING_LIFECYCLE_EVIDENCE_SCHEMA_VERSION
            ),
            "required_exit_context_fields": list(
                FORWARD_REQUIRED_EXIT_CONTEXT_FIELDS
            ),
            "required_executed_buy_sizing_fields": list(
                FORWARD_REQUIRED_EXECUTED_BUY_SIZING_FIELDS
            ),
            "required_executed_sell_sizing_fields": list(
                FORWARD_REQUIRED_EXECUTED_SELL_SIZING_FIELDS
            ),
            "conditional_entry_context_rules": dict(
                FORWARD_CONDITIONAL_ENTRY_CONTEXT_RULES
            ),
            "allowed_schedule_run_kinds": sorted(FORWARD_ALLOWED_RUN_KINDS),
            "allowed_execution_modes": sorted(
                FORWARD_ALLOWED_EXECUTION_MODES
            ),
            "shadow_execution_gap_pct": shadow_execution_gap_pct,
            "shadow_recovery_ratio_cap": shadow_recovery_ratio_cap,
            "shadow_candidates": dict(FORWARD_SHADOW_CANDIDATES),
            "required_operating_day_events": list(
                FORWARD_REQUIRED_OPERATING_DAY_EVENTS
            ),
            "operating_day_coverage_rule": (
                "100% of configured Monday-Friday operating days must have "
                "all required events before manual review"
            ),
        },
        "evidence_gate": {
            "status": status,
            "evidence_gate_met": evidence_gate_met,
            "completed_trade_gate_met": trade_gate_met,
            "calendar_month_gate_met": time_gate_met,
            "sample_gate_met": sample_gate_met,
            "data_quality_gate_met": data_quality_gate_met,
            "calendar_days_elapsed": elapsed_days,
            "full_calendar_months_elapsed": elapsed_months,
            "decision": (
                "eligible_for_manual_review" if evidence_gate_met
                else "incomplete_forward_attribution"
                if sample_gate_met else "insufficient_forward_evidence"
            ),
            "review_scope": (
                "performance_and_operations"
                if trade_gate_met
                else "frequency_and_operations_only"
                if time_gate_met
                else "not_yet_reviewable"
            ),
        },
        "performance_assessment": performance_assessment,
        "performance_clustering": performance_clustering,
        "portfolio": portfolio_assessment,
        "overall": overall_metrics,
        "opportunities": opportunities,
        "holding_lifecycle": holding_lifecycle,
        "coverage": {
            "retained_trade_row_count": len(normalized),
            "entry_context_trade_count": entry_context_count,
            "entry_context_coverage_pct": round(
                entry_context_count / len(completed) * 100.0,
                4,
            ) if completed else None,
            "entry_signal_timestamp_trade_count": entry_signal_timestamp_count,
            "entry_signal_timestamp_coverage_pct": round(
                entry_signal_timestamp_count / len(completed) * 100.0,
                4,
            ) if completed else None,
            "complete_entry_attribution_trade_count": (
                complete_entry_attribution_count
            ),
            "complete_entry_attribution_coverage_pct": round(
                complete_entry_attribution_count / len(completed) * 100.0,
                4,
            ) if completed else None,
            "incomplete_entry_attribution_trade_count": (
                len(completed) - complete_entry_attribution_count
            ),
            "missing_entry_attribution_fields": dict(
                sorted(missing_entry_attribution_fields.items())
            ),
            "complete_lifecycle_attribution_trade_count": (
                complete_attribution_count
            ),
            "complete_lifecycle_attribution_coverage_pct": round(
                complete_attribution_count / len(completed) * 100.0,
                4,
            ) if completed else None,
            "incomplete_lifecycle_attribution_trade_count": (
                len(completed) - complete_attribution_count
            ),
            "missing_lifecycle_attribution_fields": dict(
                sorted(missing_attribution_fields.items())
            ),
            "exit_lifecycle_evidence_trade_count": (
                exit_lifecycle_evidence_count
            ),
            "exit_lifecycle_evidence_coverage_pct": round(
                exit_lifecycle_evidence_count / len(completed) * 100.0,
                4,
            ) if completed else None,
            "complete_holding_lifecycle_path_count": (
                complete_holding_lifecycle_path_count
            ),
            "complete_holding_lifecycle_path_coverage_pct": round(
                complete_holding_lifecycle_path_count
                / len(completed) * 100.0,
                4,
            ) if completed else None,
            "rich_entry_payload_trade_count": sum(
                row.get("entry_payload_available") is True
                for row in completed
            ),
            "legacy_entry_payload_trade_count": sum(
                row.get("entry_payload_available") is False
                for row in completed
            ),
            "unknown_entry_payload_trade_count": sum(
                row.get("entry_payload_available") is None
                for row in completed
            ),
            "deferred_entry_trade_count": deferred_entry_count,
            "open_niuone_lifecycle_count": sum(
                lifecycle["verified_open"] is True
                and lifecycle["entry_date"] >= start
                and lifecycle["entry_strategy"] in NIUONE_STRATEGY_IDS
                for lifecycle in active.values()
            ),
            "invalid_timestamp_count": invalid_timestamp_count,
            "invalid_trade_count": invalid_trade_count,
            "orphan_sell_count": orphan_sell_count,
            "oversold_lifecycle_count": oversold_lifecycle_count,
            "unverified_open_count": unverified_open_count,
            "inconsistent_quantity_count": inconsistent_quantity_count,
            "duplicate_trade_count": duplicate_trade_count,
            "rich_payload_trade_count": rich_payload_count,
            "legacy_payload_trade_count": legacy_payload_count,
        },
        "groups": {
            "entry_stage": _group_summary(
                completed,
                lambda row: row["entry_strategy"],
            ),
            "entry_mainline_state": _group_summary(
                completed,
                lambda row: context_value(row, "entry_mainline_state")
                or "missing",
            ),
            "entry_lifecycle_stage": _group_summary(
                completed,
                lambda row: context_value(
                    row, "entry_niuone_lifecycle_stage"
                ) or "missing",
            ),
            "exit_lifecycle_stage": _group_summary(
                completed,
                lambda row: exit_context_value(
                    row, "exit_niuone_lifecycle_stage"
                ) or "missing",
            ),
            "holding_lifecycle_path": _group_summary(
                completed,
                lifecycle_path_pattern,
            ),
            "entry_industry": _group_summary(
                completed,
                lambda row: context_value(row, "entry_industry")
                or "missing",
            ),
            "entry_theme": _group_summary(
                completed,
                lambda row: context_value(row, "entry_theme")
                or "missing",
            ),
            "entry_signal_score": _group_summary(completed, score_bin),
            "entry_same_stage_rank": _group_summary(completed, rank_bin),
            "entry_schedule_run_kind": _group_summary(
                completed,
                lambda row: context_value(
                    row, "entry_schedule_run_kind"
                ) or "missing",
            ),
            "shadow_execution_gap": _group_summary(completed, gap_bin),
            "shadow_recovery_ratio": _group_summary(
                completed,
                recovery_ratio_bin,
            ),
            "entry_risk_ceiling_utilization": _group_summary(
                completed,
                risk_ceiling_utilization_bin,
            ),
        },
        "interpretation": {
            "shadow_groups_are_descriptive_only": True,
            "portfolio_counterfactual_available": False,
            "automatic_promotion_allowed": False,
            "limitations": [
                "A single realized account cannot identify the portfolio return of a trade that was not taken.",
                "Entry-context groups include completed trades only and may be affected by exit timing and censoring.",
                "Wilson intervals treat completed lifecycles as independent; shared entry dates and themes can make the effective sample smaller.",
                "Portfolio return and drawdown are admissible only when every configured weekday has one durable post-close mark and the frozen account began with zero positions.",
                "A trimmed trade log can omit opening BUY rows; unmatched SELL rows are deliberately excluded.",
            ],
        },
        "warnings": warnings,
    }
