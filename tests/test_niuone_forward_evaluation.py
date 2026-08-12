#!/usr/bin/env python3
import json
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from app.strategies.lifecycle import NIUONE_LIFECYCLE_STAGES
from app.trading.niuone_forward import (
    DEFAULT_COHORT_START,
    evaluate_niuone_forward as _evaluate_niuone_forward,
    load_niuone_forward_daily_equity_from_db,
    load_niuone_forward_decisions_from_db,
    load_niuone_forward_trades_from_db,
    merge_forward_trade_rows,
)
from app.trading.niuone_forward_service import (
    FORWARD_COHORT_START_ENV,
    PROTOCOL_DERIVED_RUNTIME_SETTING_NAMES,
    PROTOCOL_RUNTIME_SETTING_DEFAULTS,
    PROTOCOL_SOURCE_PATHS,
    _apply_operational_coverage,
    _build_protocol_identity,
    _capture_account_baseline,
    _expected_operating_days,
    _freeze_protocol_lock,
    _mark_non_durable_overlay,
    _protocol_fingerprint,
    _resolved_protocol_settings,
    main as forward_service_main,
)


ROOT = Path(__file__).resolve().parents[1]
LEGACY_TEST_COHORT_START = "2026-08-04"


def evaluate_niuone_forward(*args, **kwargs):
    """Keep historical fixtures explicit while production starts a v33 cohort."""

    kwargs.setdefault("cohort_start", LEGACY_TEST_COHORT_START)
    return _evaluate_niuone_forward(*args, **kwargs)


def complete_context(**overrides):
    context = {
        "entry_niuone_lifecycle_stage": "brewing",
        "entry_niuone_lifecycle_label": "主线酝酿",
        "entry_niuone_lifecycle_order": 10,
        "entry_niuone_lifecycle_entry_policy": "probe_only",
        "entry_mainline_state": "candidate",
        "entry_signal_score": 9.1,
        "entry_stock_activity_score": 84.25,
        "entry_stock_market_amount_percentile": 90.0,
        "entry_stock_theme_amount_percentile": 75.0,
        "entry_stock_activity_confirmed": True,
        "entry_same_stage_candidate_rank": 1,
        "entry_execution_gap_pct": 0.5,
        "entry_daily_v_recovery_ratio": 1.5,
        "entry_signal_generated_at": "2026-08-04 09:25:10",
        "entry_schedule_slot": "2026-08-04 09:25",
        "entry_schedule_run_kind": "scheduled",
        "entry_schedule_triggered_at": "2026-08-04 09:25:00",
        "entry_execution_mode": "deferred",
        "entry_industry": "半导体",
        "entry_theme": "半导体",
        "entry_theme_basis": "eastmoney_concept",
        "entry_theme_attribution_score": 82.0,
        "entry_theme_attribution_weight": 1.0,
        "entry_theme_historical_prior_score": 80.0,
        "entry_theme_cohort_alignment_score": 78.0,
        "entry_theme_peer_resonance_score": 84.0,
        "entry_theme_return_correlation_score": 91.0,
        "entry_theme_return_correlation_rank_score": 96.0,
        "entry_theme_return_correlation_observation_count": 20,
        "entry_theme_return_correlation_peer_count": 12,
        "entry_theme_specificity_score": 88.0,
        "entry_theme_membership_source": "eastmoney_concept",
        "entry_theme_unattributed_weight": 0.0,
        "entry_model_requested_shares": 100,
        "entry_executed_shares": 100,
        "entry_maximum_permitted_shares": 200,
        "entry_risk_ceiling_utilization_pct": 50.0,
        "entry_risk_ceiling_binding_constraints": ["single_name_risk"],
        "entry_risk_ceiling_auto_reduced": False,
    }
    context.update(overrides)
    return context


def complete_lifecycle_evidence(
    *,
    entry_context=None,
    exit_time: str = "2026-08-05 10:00:00",
    stage_sequence=("brewing",),
):
    context = entry_context or complete_context()
    stages = list(stage_sequence)
    entry_time = str(context["entry_signal_generated_at"])
    entry_dt = datetime.fromisoformat(entry_time)
    exit_dt = datetime.fromisoformat(exit_time)
    span = max(0.0, (exit_dt - entry_dt).total_seconds())
    stage_times = [
        entry_dt + timedelta(
            seconds=(span * index / max(1, len(stages) - 1))
        )
        for index in range(len(stages))
    ]
    observations_by_stage = [[] for _stage in stages]
    for index, stage_time in enumerate(stage_times):
        source = (
            "entry_signal" if index == 0
            else "exit_fill" if index == len(stages) - 1
            else "mainline_scan"
        )
        observations_by_stage[index].append({
            "observed_at": stage_time.isoformat(sep=" "),
            "source": source,
            "mainline_state": "candidate",
            "mainline_score": 65.0,
        })
    if exit_time != entry_time and len(stages) == 1:
        observations_by_stage[0].append({
            "observed_at": exit_time,
            "source": "exit_fill",
            "mainline_state": "candidate",
            "mainline_score": 65.0,
        })
    current_day = entry_dt.date()
    while current_day <= exit_dt.date():
        if (
            current_day.weekday() < 5
            and current_day > entry_dt.date()
            and not any(
            str(observation["observed_at"])[:10] == current_day.isoformat()
            and observation["source"] == "mainline_scan"
            for observations in observations_by_stage
            for observation in observations
            )
        ):
            observation_dt = datetime.combine(
                current_day,
                datetime.strptime("09:25", "%H:%M").time(),
            )
            if observation_dt >= exit_dt:
                observation_dt = exit_dt - timedelta(seconds=1)
            active_index = max(
                index
                for index, stage_time in enumerate(stage_times)
                if stage_time <= observation_dt
            )
            observations_by_stage[active_index].append({
                "observed_at": observation_dt.isoformat(sep=" "),
                "source": "mainline_scan",
                "mainline_state": "candidate",
                "mainline_score": 65.0,
            })
        current_day += timedelta(days=1)
    path = []
    for index, stage in enumerate(stages):
        definition = NIUONE_LIFECYCLE_STAGES[stage]
        observations = sorted(
            observations_by_stage[index],
            key=lambda item: str(item["observed_at"]),
        )
        entered_at = str(observations[0]["observed_at"])
        last_observed_at = str(observations[-1]["observed_at"])
        path.append({
            "stage": stage,
            "label": definition["label"],
            "order": definition["order"],
            "entry_policy": definition["entry_policy"],
            "entered_at": entered_at,
            "last_observed_at": last_observed_at,
            "observation_count": len(observations),
            "source": str(observations[0]["source"]),
            "observations": observations,
            "mainline_state_at_entry": "candidate",
            "mainline_score_at_entry": 65.0,
            "last_mainline_state": "candidate",
            "last_mainline_score": 65.0,
        })
    exit_stage = stages[-1]
    exit_definition = NIUONE_LIFECYCLE_STAGES[exit_stage]
    return {
        "schema_version": 1,
        "path_complete_from_entry": True,
        "exit_niuone_lifecycle_stage": exit_stage,
        "exit_niuone_lifecycle_label": exit_definition["label"],
        "exit_niuone_lifecycle_order": exit_definition["order"],
        "exit_niuone_lifecycle_entry_policy": (
            exit_definition["entry_policy"]
        ),
        "stage_sequence": stages,
        "transition_count": len(stages) - 1,
        "reached_markup": "markup" in stages,
        "reached_climax": "climax" in stages,
        "reached_divergence": "divergence" in stages,
        "reached_fade": "fade" in stages,
        "path": path,
    }


def trade(
    time: str,
    action: str,
    code: str,
    shares: int,
    amount: float,
    *,
    strategy: str = "niu_reversal_probe",
    fee: float = 0.0,
    context=None,
    lifecycle_evidence=None,
    before_qty=None,
    after_qty=None,
):
    row = {
        "time": time,
        "action": action,
        "code": code,
        "shares": shares,
        "amount": amount,
        "fee": fee,
        "buy_strategy": strategy,
    }
    if action == "BUY":
        row["total_cost"] = amount + fee
    else:
        row["net_proceeds"] = amount - fee
        if lifecycle_evidence is not False:
            row["niuone_lifecycle_evidence"] = (
                dict(lifecycle_evidence)
                if isinstance(lifecycle_evidence, dict)
                else complete_lifecycle_evidence(
                    entry_context=context or complete_context(),
                    exit_time=time,
                )
            )
    if context is not None:
        row["niuone_entry_context"] = context
        if action == "BUY":
            row["model_requested_shares"] = context.get(
                "entry_model_requested_shares"
            )
            row["maximum_permitted_shares"] = context.get(
                "entry_maximum_permitted_shares"
            )
            row["risk_ceiling_utilization_pct"] = context.get(
                "entry_risk_ceiling_utilization_pct"
            )
            row["risk_ceiling_binding_constraints"] = context.get(
                "entry_risk_ceiling_binding_constraints"
            )
            row["risk_ceiling_auto_reduced"] = context.get(
                "entry_risk_ceiling_auto_reduced"
            )
            row["position_opened"] = before_qty == 0
    if before_qty is not None:
        row["position_before_qty"] = before_qty
    if after_qty is not None:
        row["position_after_qty"] = after_qty
    return row


def clean_account_baseline(**overrides):
    baseline = {
        "status": "captured",
        "source": "test",
        "captured_at": "2026-08-02T15:30:00+08:00",
        "account_created_at": "2026-07-01 09:00:00",
        "initial_cash": 1_000_000.0,
        "cash": 1_000_000.0,
        "total_equity": 1_000_000.0,
        "open_position_count": 0,
        "niuone_position_count": 0,
        "non_niuone_position_count": 0,
        "unknown_position_strategy_count": 0,
        "clean_zero_position_boundary": True,
    }
    baseline.update(overrides)
    return baseline


def daily_equity_point(
    day: str,
    equity: float,
    *,
    cash: float | None = None,
    hhmm: str = "15:15",
):
    cash_value = equity if cash is None else cash
    return {
        "date": day,
        "equity": equity,
        "cash": cash_value,
        "market_value": equity - cash_value,
        "pnl_pct": round((equity / 1_000_000.0 - 1.0) * 100.0, 2),
        "account_created_at": "2026-07-01 09:00:00",
        "created_at": f"{day} {hhmm}:00",
        "_forward_payload_available": True,
    }


def complete_operating_states(
    start: date,
    end: date,
    *,
    schedule_times: tuple[str, ...] = ("09:25", "10:00"),
):
    scheduler_state = {"job_history": {}}
    b1_state = {"day_history": {}}
    decision_rows = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            day_key = current.isoformat()
            scheduler_state["job_history"][day_key] = {
                "DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON": [{
                    "scheduled_at": f"{day_key}T09:05:00+08:00",
                    "completed_at": f"{day_key}T09:06:00+08:00",
                    "success": True,
                    "status": "ok",
                    "exit_code": 0,
                }],
                "DASHBOARD_B3_EXIT_TIME": [{
                    "scheduled_at": f"{day_key}T09:37:00+08:00",
                    "completed_at": f"{day_key}T09:37:01+08:00",
                    "success": True,
                    "status": "ok",
                    "exit_code": 0,
                }],
                "DASHBOARD_TIME_EXIT_TIME": [{
                    "scheduled_at": f"{day_key}T14:45:00+08:00",
                    "completed_at": f"{day_key}T14:45:01+08:00",
                    "success": True,
                    "status": "ok",
                    "exit_code": 0,
                }],
                "DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON": [{
                    "scheduled_at": f"{day_key}T15:15:00+08:00",
                    "completed_at": f"{day_key}T15:15:01+08:00",
                    "success": True,
                    "status": "ok",
                    "exit_code": 0,
                }],
                "DASHBOARD_NIUONE_FORWARD_CRON": [{
                    "scheduled_at": f"{day_key}T15:20:00+08:00",
                    "completed_at": f"{day_key}T15:20:01+08:00",
                    "success": True,
                    "status": "ok",
                    "exit_code": 0,
                }],
            }
            b1_state["day_history"][day_key] = {
                "slots": {
                    hhmm: {
                        "scheduled_at": f"{day_key} {hhmm}",
                        "status": "ok",
                        "run_kind": "scheduled",
                    }
                    for hhmm in schedule_times
                },
            }
            decision_rows.extend({
                "_forward_payload_available": True,
                "candidate_evidence_schema_version": 2,
                "execution_evidence_schema_version": 2,
                "candidate_evidence": [],
                "schedule_slot": f"{day_key} {hhmm}",
                "schedule_run_kind": "scheduled",
                "decision": {"actions": []},
                "executed": [],
            } for hhmm in schedule_times)
        current += timedelta(days=1)
    return scheduler_state, b1_state, decision_rows


def candidate_evidence(
    code: str,
    *,
    stage: str = "brewing",
    strategy_id: str = "niu_reversal_probe",
    score: float = 9.0,
    eligible: bool = True,
    rank: int = 1,
) -> dict[str, object]:
    return {
        "code": code,
        "strategy_id": strategy_id,
        "best_score": score,
        "observed_rank": rank,
        "eligible_for_decision": eligible,
        "eligibility_blockers": [] if eligible else [
            "not_selected_for_decision"
        ],
        "niuone_lifecycle_stage": stage,
        "stock_activity_data_available": True,
        "stock_market_amount_percentile": 90.0,
        "stock_theme_amount_percentile": 75.0,
        "stock_activity_score": 84.25,
        "stock_activity_confirmed": True,
    }


def operating_settings(*times: str) -> dict[str, str]:
    return {
        "DASHBOARD_B1_SCHEDULE_ENABLED": "true",
        "DASHBOARD_PRACTICE_SCHEDULE_TIMES": ",".join(times),
    }


class NiuOneForwardEvaluationTests(unittest.TestCase):
    def test_protocol_identity_covers_evidence_pipeline_and_effective_paths(self):
        self.assertEqual(DEFAULT_COHORT_START, "2026-08-13")
        expected_sources = {
            "app/automation/cron.py",
            "app/automation/scheduler_service.py",
            "app/dashboard/server.py",
            "app/entrypoints/evaluate_niuone_forward.py",
            "app/storage/practice_db.py",
            "app/strategies/display.py",
            "app/trading/niuone_forward.py",
            "app/trading/niuone_forward_service.py",
            "app/trading/practice_trader.py",
        }
        self.assertTrue(expected_sources.issubset(PROTOCOL_SOURCE_PATHS))
        self.assertTrue({
            "DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON",
            "DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON",
            "DASHBOARD_NIUONE_FORWARD_CRON",
            "DASHBOARD_NIUNIU_DB",
            "DASHBOARD_PORTFOLIO_STATE",
        }.issubset(PROTOCOL_RUNTIME_SETTING_DEFAULTS))
        self.assertEqual(
            set(PROTOCOL_DERIVED_RUNTIME_SETTING_NAMES),
            {
                "NIUONE_CRON_SCHEDULER_STATE_PATH",
                "NIUONE_B1_SCHEDULE_STATE_PATH",
                "NIUONE_A_SHARE_CALENDAR_CACHE_PATH",
            },
        )

        with tempfile.TemporaryDirectory(
            prefix="niuone-forward-settings-"
        ) as directory:
            root = Path(directory)
            with mock.patch.dict(os.environ, {}, clear=True):
                settings = _resolved_protocol_settings({
                    "DASHBOARD_HOME": str(root / "runtime"),
                    "DASHBOARD_NIUNIU_DB": str(root / "custom.db"),
                    "DASHBOARD_PORTFOLIO_STATE": str(root / "custom.json"),
                })

        self.assertEqual(
            settings["DASHBOARD_NIUNIU_DB"],
            str((root / "custom.db").resolve()),
        )
        self.assertEqual(
            settings["DASHBOARD_PORTFOLIO_STATE"],
            str((root / "custom.json").resolve()),
        )
        self.assertEqual(
            settings["NIUONE_CRON_SCHEDULER_STATE_PATH"],
            str(
                (root / "runtime" / "cron" / "state"
                 / "niuone_cron_scheduler.json").resolve()
            ),
        )
        self.assertEqual(
            settings["NIUONE_A_SHARE_CALENDAR_CACHE_PATH"],
            str(
                (root / "runtime" / "cron" / "state"
                 / "a_share_trading_calendar.json").resolve()
            ),
        )
        protocol = evaluate_niuone_forward(
            [],
            cohort_start="2026-08-03",
            as_of="2026-08-02",
        )["protocol"]
        identity = _build_protocol_identity(
            protocol,
            runtime_settings={FORWARD_COHORT_START_ENV: "2026-08-03"},
        )
        self.assertEqual(
            identity["protocol"]["execution_evidence_schema_version"],
            2,
        )
        self.assertEqual(
            identity["protocol"]["required_executed_buy_sizing_fields"],
            protocol["required_executed_buy_sizing_fields"],
        )
        self.assertEqual(
            identity["protocol"]["sell_execution_evidence_schema_version"],
            1,
        )
        self.assertEqual(
            identity["protocol"]["required_executed_sell_sizing_fields"],
            protocol["required_executed_sell_sizing_fields"],
        )
        self.assertEqual(
            identity["protocol"]
            ["holding_lifecycle_evidence_schema_version"],
            1,
        )
        self.assertEqual(
            identity["protocol"]["required_exit_context_fields"],
            protocol["required_exit_context_fields"],
        )
        self.assertEqual(
            identity["protocol"]
            ["holding_lifecycle_daily_coverage_rule"],
            protocol["holding_lifecycle_daily_coverage_rule"],
        )
        self.assertEqual(
            identity["protocol"]
            ["maximum_new_niuone_positions_per_trading_day"],
            2,
        )
        self.assertEqual(
            identity["protocol"]["daily_new_position_limit_rule"],
            protocol["daily_new_position_limit_rule"],
        )
        self.assertEqual(
            identity["protocol"]
            ["niuone_reversal_minimum_recovery_ratio_inclusive"],
            0.60,
        )
        self.assertEqual(
            identity["protocol"]
            ["niuone_reversal_maximum_recovery_ratio_exclusive"],
            2.0,
        )
        self.assertEqual(
            identity["protocol"]
            ["niuone_reversal_minimum_strong_stock_count"],
            6,
        )
        self.assertEqual(
            identity["protocol"]["niuone_reversal_minimum_state_streak"],
            3,
        )
        self.assertEqual(
            identity["protocol"]["niuone_reversal_daily_candidate_limit"],
            2,
        )
        self.assertEqual(
            identity["protocol"]
            ["niuone_reversal_absolute_position_cap_pct"],
            6.25,
        )
        self.assertEqual(
            identity["protocol"]["niuone_reversal_recovery_rule"],
            protocol["niuone_reversal_recovery_rule"],
        )
        self.assertEqual(
            identity["protocol"]
            ["niuone_leader_minimum_sector_rank_inclusive"],
            80.0,
        )
        self.assertEqual(
            identity["protocol"]
            ["niuone_leader_minimum_today_strength_inclusive"],
            60.0,
        )
        self.assertEqual(
            identity["protocol"]["niuone_startup_allowed_mainline_states"],
            ["emerging"],
        )
        self.assertEqual(
            identity["protocol"]["lifecycle_entry_strategy_routes"],
            {
                "brewing": ["niu_reversal_probe"],
                "markup": ["niu_emerging", "niu_leader"],
                "climax": ["niu_leader", "niu_pullback"],
                "divergence": ["niu_leader", "niu_pullback"],
                "fade": [],
            },
        )
        self.assertEqual(
            identity["protocol"]["historical_reference_win_rate_pct"],
            59.71,
        )
        self.assertEqual(
            identity["protocol"]["performance_assessment_rule"],
            protocol["performance_assessment_rule"],
        )
        self.assertEqual(
            identity["protocol"]["performance_cluster_unit"],
            "entry_date_x_entry_theme",
        )
        self.assertEqual(
            identity["protocol"]["minimum_unique_performance_clusters"],
            30,
        )
        self.assertEqual(
            identity["protocol"]["minimum_effective_performance_clusters"],
            30,
        )
        self.assertEqual(
            identity["protocol"]["performance_cluster_confidence_rule"],
            protocol["performance_cluster_confidence_rule"],
        )

    def test_protocol_lock_can_refresh_only_before_cohort_start(self):
        base_protocol = evaluate_niuone_forward(
            [],
            cohort_start="2026-08-03",
            as_of="2026-08-02",
        )["protocol"]
        settings = {FORWARD_COHORT_START_ENV: "2026-08-03"}
        old_protocol = {**base_protocol, "version": "niuone-strict-forward-v3"}
        old_identity = _build_protocol_identity(
            old_protocol,
            runtime_settings=settings,
        )
        current_identity = _build_protocol_identity(
            base_protocol,
            runtime_settings=settings,
        )

        with tempfile.TemporaryDirectory(
            prefix="niuone-forward-pre-cohort-"
        ) as directory:
            lock_path = Path(directory) / "protocol.json"
            first = _freeze_protocol_lock(
                lock_path,
                old_identity,
                frozen_at="2026-08-02T08:00:00+08:00",
                refresh_date=date(2026, 8, 2),
            )
            refreshed = _freeze_protocol_lock(
                lock_path,
                current_identity,
                frozen_at="2026-08-02T09:00:00+08:00",
                refresh_date=date(2026, 8, 2),
            )
            refreshed_bytes = lock_path.read_bytes()
            post_start_identity = {
                **current_identity,
                "runtime_settings": {
                    **current_identity["runtime_settings"],
                    "test_drift": "changed",
                },
            }
            blocked = _freeze_protocol_lock(
                lock_path,
                post_start_identity,
                frozen_at="2026-08-03T09:05:00+08:00",
                refresh_date=date(2026, 8, 3),
            )
            blocked_bytes = lock_path.read_bytes()

        self.assertEqual(first["status"], "frozen")
        self.assertEqual(refreshed["status"], "refrozen_pre_cohort")
        self.assertTrue(refreshed["cohort_valid"])
        self.assertIn("protocol.version", refreshed["changed_fields"])
        self.assertEqual(blocked["status"], "mismatch")
        self.assertFalse(blocked["cohort_valid"])
        self.assertEqual(blocked_bytes, refreshed_bytes)

    def test_protocol_only_freezes_before_database_exists_and_detects_drift(self):
        with tempfile.TemporaryDirectory(
            prefix="niuone-forward-preflight-"
        ) as directory:
            root = Path(directory)
            environment = {
                "DASHBOARD_HOME": directory,
                "DASHBOARD_ENV_FILE": str(root / "missing.env"),
                FORWARD_COHORT_START_ENV: "2026-08-02",
                "DASHBOARD_MAX_TOTAL_POSITION_PCT": "80",
            }
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, environment, clear=True):
                with redirect_stdout(stdout):
                    first_code = forward_service_main([
                        "--runtime",
                        "--protocol-only",
                        "--as-of", "2026-08-03",
                    ])
                lock_path = (
                    root / "cron" / "state" / "niuone_forward_protocol.json"
                )
                report_path = (
                    root / "cron" / "output" / "niuone_forward_evaluation.json"
                )
                first_lock = lock_path.read_bytes()
                with redirect_stdout(stdout):
                    matched_code = forward_service_main([
                        "--runtime",
                        "--protocol-only",
                        "--as-of", "2026-08-03",
                    ])
                os.environ["DASHBOARD_MAX_TOTAL_POSITION_PCT"] = "70"
                with redirect_stdout(stdout):
                    drift_code = forward_service_main([
                        "--runtime",
                        "--protocol-only",
                        "--as-of", "2026-08-03",
                    ])
                drift_lock = lock_path.read_bytes()
                report_created = report_path.exists()

        output = stdout.getvalue()
        self.assertEqual(first_code, 0)
        self.assertEqual(matched_code, 0)
        self.assertEqual(drift_code, 2)
        self.assertIn("status=frozen", output)
        self.assertIn("status=matched", output)
        self.assertIn("status=mismatch", output)
        self.assertFalse(report_created)
        self.assertEqual(drift_lock, first_lock)

    def test_protocol_lock_freezes_code_free_zero_position_account_boundary(self):
        with tempfile.TemporaryDirectory(
            prefix="niuone-forward-account-boundary-"
        ) as directory:
            root = Path(directory)
            state_path = root / "state.json"
            state_path.write_text(json.dumps({
                "created_at": "2026-07-01 09:00:00",
                "initial_cash": 1_000_000.0,
                "cash": 1_000_000.0,
                "positions": {},
            }), encoding="utf-8")
            captured = _capture_account_baseline(
                state_path,
                root / "missing.db",
                captured_at=datetime.fromisoformat(
                    "2026-08-02T15:30:00+08:00"
                ),
            )
            protocol = evaluate_niuone_forward(
                [],
                cohort_start="2026-08-03",
                as_of="2026-08-02",
            )["protocol"]
            identity = _build_protocol_identity(
                protocol,
                runtime_settings={FORWARD_COHORT_START_ENV: "2026-08-03"},
            )
            lock_path = root / "protocol.json"
            result = _freeze_protocol_lock(
                lock_path,
                identity,
                frozen_at="2026-08-02T15:30:00+08:00",
                refresh_date=date(2026, 8, 2),
                account_baseline=captured,
            )
            persisted = json.loads(lock_path.read_text(encoding="utf-8"))

        self.assertEqual(captured["status"], "captured")
        self.assertTrue(captured["clean_zero_position_boundary"])
        self.assertEqual(captured["open_position_count"], 0)
        self.assertNotIn("positions", persisted["account_baseline"])
        self.assertEqual(result["account_baseline"], captured)

    def test_protocol_fingerprint_excludes_daily_as_of_cutoff(self):
        first = evaluate_niuone_forward([], as_of="2026-08-03")
        second = evaluate_niuone_forward([], as_of="2026-08-04")
        settings = {FORWARD_COHORT_START_ENV: "2026-08-03"}

        first_identity = _build_protocol_identity(
            first["protocol"],
            runtime_settings=settings,
        )
        second_identity = _build_protocol_identity(
            second["protocol"],
            runtime_settings=settings,
        )

        self.assertNotEqual(
            first["protocol"]["as_of"],
            second["protocol"]["as_of"],
        )
        self.assertEqual(first_identity, second_identity)
        self.assertEqual(
            _protocol_fingerprint(first_identity),
            _protocol_fingerprint(second_identity),
        )

    def test_cached_exchange_calendar_excludes_weekday_holidays(self):
        with tempfile.TemporaryDirectory(
            prefix="niuone-forward-calendar-"
        ) as directory:
            cache_path = Path(directory) / "calendar.json"
            cache_path.write_text(json.dumps({
                "dates": ["2026-08-03", "2026-08-05"],
                "source": "test",
                "updated_at": "2026-08-01 12:00:00",
            }), encoding="utf-8")
            expected = _expected_operating_days(
                date(2026, 8, 3),
                date(2026, 8, 5),
                calendar_cache_file=cache_path,
            )

        self.assertEqual(
            expected,
            [date(2026, 8, 3), date(2026, 8, 5)],
        )

    def test_runtime_protocol_lock_is_idempotent_and_blocks_config_drift(self):
        with tempfile.TemporaryDirectory(prefix="niuone-forward-lock-") as directory:
            root = Path(directory)
            db_path = root / "practice.db"
            connection = sqlite3.connect(db_path)
            connection.executescript("""
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time TEXT NOT NULL,
                    action TEXT NOT NULL,
                    code TEXT NOT NULL,
                    shares INTEGER NOT NULL,
                    amount REAL NOT NULL
                );
            """)
            connection.commit()
            connection.close()
            environment = {
                "DASHBOARD_HOME": directory,
                "DASHBOARD_ENV_FILE": str(root / "missing.env"),
                "DASHBOARD_NIUNIU_DB": str(db_path),
                FORWARD_COHORT_START_ENV: "2026-08-02",
                "DASHBOARD_MAX_TOTAL_POSITION_PCT": "80",
            }
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, environment, clear=True):
                with redirect_stdout(stdout):
                    first_code = forward_service_main([
                        "--runtime",
                        "--as-of", "2026-11-03",
                    ])
                lock_path = (
                    root / "cron" / "state" / "niuone_forward_protocol.json"
                )
                report_path = (
                    root / "cron" / "output" / "niuone_forward_evaluation.json"
                )
                first_lock = lock_path.read_bytes()
                first_report = json.loads(report_path.read_text(encoding="utf-8"))

                with redirect_stdout(stdout):
                    second_code = forward_service_main([
                        "--runtime",
                        "--as-of", "2026-11-03",
                    ])
                second_lock = lock_path.read_bytes()
                second_report = json.loads(report_path.read_text(encoding="utf-8"))

                os.environ["DASHBOARD_MAX_TOTAL_POSITION_PCT"] = "70"
                with redirect_stdout(stdout):
                    drift_code = forward_service_main([
                        "--runtime",
                        "--as-of", "2026-11-03",
                    ])
                drift_lock = lock_path.read_bytes()
                drift_report = json.loads(report_path.read_text(encoding="utf-8"))

                lock_path.write_text("{invalid", encoding="utf-8")
                os.environ["DASHBOARD_MAX_TOTAL_POSITION_PCT"] = "80"
                with redirect_stdout(stdout):
                    invalid_lock_code = forward_service_main([
                        "--runtime",
                        "--as-of", "2026-11-03",
                    ])
                invalid_lock_bytes = lock_path.read_bytes()
                invalid_lock_report = json.loads(
                    report_path.read_text(encoding="utf-8")
                )

        self.assertEqual(first_code, 0)
        self.assertEqual(first_report["protocol_integrity"]["status"], "frozen")
        self.assertEqual(first_report["protocol_integrity"]["source_file_count"], 22)
        self.assertEqual(first_report["protocol_integrity"]["runtime_setting_count"], 56)
        self.assertEqual(
            first_report["evidence_gate"]["status"],
            "operations_blocked",
        )
        self.assertTrue(
            first_report["evidence_gate"][
                "sample_evidence_gate_met_before_operations"
            ]
        )
        self.assertFalse(first_report["evidence_gate"]["evidence_gate_met"])
        self.assertFalse(
            first_report["operations"]["operational_coverage_gate_met"]
        )
        self.assertEqual(second_code, 0)
        self.assertEqual(second_report["protocol_integrity"]["status"], "matched")
        self.assertEqual(first_lock, second_lock)
        self.assertEqual(drift_code, 2)
        self.assertEqual(drift_lock, first_lock)
        self.assertEqual(
            drift_report["protocol_integrity"]["status"],
            "mismatch",
        )
        self.assertIn(
            "runtime_settings.DASHBOARD_MAX_TOTAL_POSITION_PCT",
            drift_report["protocol_integrity"]["changed_fields"],
        )
        self.assertEqual(
            drift_report["evidence_gate"]["status"],
            "protocol_mismatch",
        )
        self.assertTrue(
            drift_report["evidence_gate"]["sample_evidence_gate_met"]
        )
        self.assertFalse(drift_report["evidence_gate"]["evidence_gate_met"])
        self.assertEqual(invalid_lock_code, 2)
        self.assertEqual(invalid_lock_bytes, b"{invalid")
        self.assertEqual(
            invalid_lock_report["protocol_integrity"]["status"],
            "invalid_lock",
        )
        self.assertEqual(
            invalid_lock_report["evidence_gate"]["decision"],
            "protocol_lock_invalid_requires_operator_review",
        )

    def test_runtime_as_of_cannot_backdate_protocol_replacement_after_start(self):
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(2026, 8, 3, 9, 6, 0)
                return value if tz is None else value.replace(tzinfo=tz)

        with tempfile.TemporaryDirectory(
            prefix="niuone-forward-wall-clock-"
        ) as directory:
            root = Path(directory)
            db_path = root / "practice.db"
            connection = sqlite3.connect(db_path)
            connection.executescript("""
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time TEXT NOT NULL,
                    action TEXT NOT NULL,
                    code TEXT NOT NULL,
                    shares INTEGER NOT NULL,
                    amount REAL NOT NULL
                );
            """)
            connection.commit()
            connection.close()
            environment = {
                "DASHBOARD_HOME": directory,
                "DASHBOARD_ENV_FILE": str(root / "missing.env"),
                "DASHBOARD_NIUNIU_DB": str(db_path),
                FORWARD_COHORT_START_ENV: "2026-08-03",
                "DASHBOARD_MAX_TOTAL_POSITION_PCT": "80",
            }
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, environment, clear=True):
                with mock.patch(
                    "app.trading.niuone_forward_service.datetime",
                    FixedDateTime,
                ):
                    with redirect_stdout(stdout):
                        first_code = forward_service_main([
                            "--runtime",
                            "--as-of", "2026-08-02",
                        ])
                    lock_path = (
                        root / "cron" / "state"
                        / "niuone_forward_protocol.json"
                    )
                    first_lock = lock_path.read_bytes()
                    first_payload = json.loads(first_lock)
                    os.environ["DASHBOARD_MAX_TOTAL_POSITION_PCT"] = "70"
                    with redirect_stdout(stdout):
                        drift_code = forward_service_main([
                            "--runtime",
                            "--as-of", "2026-08-02",
                        ])
                    drift_lock = lock_path.read_bytes()
                    report_path = (
                        root / "cron" / "output"
                        / "niuone_forward_evaluation.json"
                    )
                    drift_report = json.loads(
                        report_path.read_text(encoding="utf-8")
                    )

        self.assertEqual(first_code, 0)
        self.assertTrue(first_payload["frozen_at"].startswith("2026-08-03"))
        self.assertEqual(drift_code, 2)
        self.assertEqual(drift_lock, first_lock)
        self.assertEqual(
            drift_report["protocol_integrity"]["status"],
            "mismatch",
        )
        self.assertIn(
            "runtime_settings.DASHBOARD_MAX_TOTAL_POSITION_PCT",
            drift_report["protocol_integrity"]["changed_fields"],
        )

    def test_operational_coverage_requires_every_configured_cycle(self):
        rows = [
            trade(
                "2026-08-03 10:00:00", "BUY", "coverage", 100, 1000,
                before_qty=0, after_qty=100,
                context=complete_context(
                    entry_signal_generated_at="2026-08-03 10:00:05",
                    entry_schedule_slot="2026-08-03 10:00",
                    entry_schedule_triggered_at="2026-08-03 10:00:00",
                ),
            ),
            trade(
                "2026-08-04 10:00:00", "SELL", "coverage", 100, 1010,
                before_qty=100, after_qty=0,
                context=complete_context(
                    entry_signal_generated_at="2026-08-03 10:00:05",
                    entry_schedule_slot="2026-08-03 10:00",
                    entry_schedule_triggered_at="2026-08-03 10:00:00",
                ),
            ),
        ]
        scheduler_state, b1_state, decision_rows = complete_operating_states(
            date(2026, 8, 3),
            date(2026, 8, 4),
        )
        report = evaluate_niuone_forward(
            rows,
            as_of="2026-08-04",
            minimum_completed_trades=1,
            cohort_start="2026-08-03",
        )

        _apply_operational_coverage(
            report,
            scheduler_state=scheduler_state,
            b1_state=b1_state,
            runtime_settings=operating_settings("09:25", "10:00"),
            decision_rows=decision_rows,
        )

        self.assertTrue(report["evidence_gate"]["evidence_gate_met"])
        self.assertEqual(report["evidence_gate"]["status"], "ready_for_manual_review")
        self.assertEqual(report["operations"]["expected_operating_day_count"], 2)
        self.assertEqual(report["operations"]["complete_operating_day_count"], 2)
        self.assertEqual(report["operations"]["operating_day_coverage_pct"], 100.0)

        incomplete_b1_state = json.loads(json.dumps(b1_state))
        del incomplete_b1_state["day_history"]["2026-08-04"]["slots"]["10:00"]
        blocked = evaluate_niuone_forward(
            rows,
            as_of="2026-08-04",
            minimum_completed_trades=1,
            cohort_start="2026-08-03",
        )
        _apply_operational_coverage(
            blocked,
            scheduler_state=scheduler_state,
            b1_state=incomplete_b1_state,
            runtime_settings=operating_settings("09:25", "10:00"),
            decision_rows=decision_rows,
        )

        self.assertEqual(blocked["evidence_gate"]["status"], "operations_blocked")
        self.assertFalse(blocked["evidence_gate"]["evidence_gate_met"])
        self.assertEqual(
            blocked["operations"]["missing_requirement_counts"],
            {"practice_slot:10:00": 1},
        )
        self.assertEqual(
            blocked["operations"]["incomplete_operating_days"],
            [{"date": "2026-08-04", "missing": ["practice_slot:10:00"]}],
        )

        missing_ledger = evaluate_niuone_forward(
            rows,
            as_of="2026-08-04",
            minimum_completed_trades=1,
        )
        _apply_operational_coverage(
            missing_ledger,
            scheduler_state=scheduler_state,
            b1_state=b1_state,
            runtime_settings=operating_settings("09:25", "10:00"),
            decision_rows=[
                row for row in decision_rows
                if row["schedule_slot"] != "2026-08-04 10:00"
            ],
        )
        self.assertEqual(
            missing_ledger["operations"]["missing_requirement_counts"],
            {"practice_decision_ledger:10:00": 1},
        )

    def test_opportunity_funnel_deduplicates_cycles_and_groups_stages(self):
        rows = [
            {
                "_forward_payload_available": True,
                "candidate_evidence_schema_version": 2,
                "execution_evidence_schema_version": 2,
                "candidate_evidence": [],
                "time": "2026-08-03 09:25:05",
                "b1_generated_at": "2026-08-03 09:25:02",
                "schedule_slot": "2026-08-03 09:25",
                "schedule_run_kind": "scheduled",
                "decision": {"actions": []},
                "executed": [],
            },
            {
                "_forward_payload_available": True,
                "candidate_evidence_schema_version": 2,
                "execution_evidence_schema_version": 2,
                "candidate_evidence": [candidate_evidence("600000")],
                "time": "2026-08-03 09:30:01",
                "b1_generated_at": "2026-08-03 09:25:02",
                "schedule_slot": "2026-08-03 09:25",
                "schedule_run_kind": "scheduled",
                "decision": {
                    "actions": [{
                        "action": "BUY",
                        "code": "600000",
                        "shares": 200,
                        "model_requested_shares": 300,
                        "maximum_permitted_shares": 200,
                        "risk_ceiling_utilization_pct": 100.0,
                        "risk_ceiling_binding_constraints": [
                            "single_name_risk"
                        ],
                        "position_opened": True,
                        "risk_ceiling_auto_reduced": True,
                    }],
                    "execution_blocks": [],
                },
                "executed": [{"action": "BUY", "code": "600000"}],
            },
            {
                "_forward_payload_available": True,
                "candidate_evidence_schema_version": 2,
                "execution_evidence_schema_version": 2,
                "candidate_evidence": [candidate_evidence(
                    "600001",
                    stage="markup",
                    strategy_id="niu_leader",
                    eligible=False,
                )],
                "time": "2026-08-03 10:00:05",
                "b1_generated_at": "2026-08-03 10:00:02",
                "schedule_slot": "2026-08-03 10:00",
                "schedule_run_kind": "scheduled",
                "decision": {"actions": []},
                "executed": [],
            },
            {
                "_forward_payload_available": True,
                "time": "2026-08-03 14:45:00",
                "b1_generated_at": "",
                "decision": {
                    "actions": [{"action": "SELL", "code": "600000"}],
                },
                "executed": [{"action": "SELL", "code": "600000"}],
            },
        ]

        executed_fill = trade(
            "2026-08-03 09:30:01",
            "BUY",
            "600000",
            200,
            2000,
            context=complete_context(
                entry_signal_generated_at="2026-08-03 09:25:02",
                entry_schedule_slot="2026-08-03 09:25",
                entry_schedule_triggered_at="2026-08-03 09:25:00",
                entry_model_requested_shares=300,
                entry_executed_shares=200,
                entry_maximum_permitted_shares=200,
                entry_risk_ceiling_utilization_pct=100.0,
                entry_risk_ceiling_auto_reduced=True,
            ),
            before_qty=0,
            after_qty=200,
        )
        executed_fill["_forward_payload_available"] = True
        report = evaluate_niuone_forward(
            [executed_fill],
            decision_rows=rows,
            as_of="2026-08-03",
            cohort_start="2026-08-03",
        )
        funnel = report["opportunities"]

        self.assertEqual(funnel["retained_decision_cycle_count"], 2)
        self.assertEqual(funnel["duplicate_decision_cycle_count"], 1)
        self.assertEqual(funnel["ignored_non_opportunity_decision_count"], 1)
        self.assertEqual(funnel["valid_candidate_evidence_cycle_count"], 2)
        self.assertEqual(funnel["observed_candidate_count"], 2)
        self.assertEqual(funnel["eligible_candidate_count"], 1)
        self.assertEqual(funnel["model_buy_candidate_count"], 1)
        self.assertEqual(funnel["executed_buy_candidate_count"], 1)
        self.assertEqual(funnel["eligibility_rate_pct"], 50.0)
        self.assertEqual(funnel["model_buy_rate_of_eligible_pct"], 100.0)
        self.assertEqual(funnel["execution_rate_of_model_buys_pct"], 100.0)
        self.assertTrue(funnel["funnel_data_quality_gate_met"])
        sizing = funnel["execution_sizing"]
        self.assertEqual(sizing["model_buy_order_count"], 1)
        self.assertEqual(sizing["executed_buy_order_count"], 1)
        self.assertEqual(sizing["requested_share_count"], 300)
        self.assertEqual(sizing["executed_share_count"], 200)
        self.assertEqual(sizing["auto_reduced_buy_order_count"], 1)
        self.assertEqual(sizing["auto_reduced_share_count"], 100)
        self.assertEqual(
            sizing["aggregate_risk_ceiling_utilization_pct"],
            100.0,
        )
        self.assertEqual(
            sizing["by_lifecycle_stage"]["brewing"]
            ["executed_risk_ceiling_evidence_count"],
            1,
        )
        self.assertTrue(sizing["sizing_data_quality_gate_met"])
        self.assertEqual(
            funnel["decision_executed_without_durable_fill_count"],
            0,
        )
        self.assertEqual(
            funnel["by_lifecycle_stage"]["brewing"]
            ["executed_buy_candidate_count"],
            1,
        )
        self.assertEqual(
            funnel["by_lifecycle_stage"]["markup"]
            ["eligible_candidate_count"],
            0,
        )

        rows[1]["executed"] = []
        reconciled_report = evaluate_niuone_forward(
            [executed_fill],
            decision_rows=rows,
            as_of="2026-08-03",
            cohort_start="2026-08-03",
        )
        reconciled = reconciled_report["opportunities"]
        self.assertEqual(reconciled["executed_buy_candidate_count"], 1)
        self.assertEqual(
            reconciled["durable_fill_without_decision_executed_count"],
            1,
        )
        self.assertFalse(reconciled["funnel_data_quality_gate_met"])

        scheduler_state, b1_state, _decision_rows = complete_operating_states(
            date(2026, 8, 3),
            date(2026, 8, 3),
            schedule_times=("09:25",),
        )
        reconciled_report["evidence_gate"]["evidence_gate_met"] = True
        _apply_operational_coverage(
            reconciled_report,
            scheduler_state=scheduler_state,
            b1_state=b1_state,
            runtime_settings=operating_settings("09:25"),
            decision_rows=[rows[1]],
        )
        self.assertTrue(
            reconciled_report["operations"]
            ["operational_coverage_gate_met"]
        )
        self.assertEqual(
            reconciled_report["evidence_gate"]["status"],
            "data_quality_blocked",
        )
        self.assertEqual(
            reconciled_report["evidence_gate"]["decision"],
            "inconsistent_forward_opportunity_evidence",
        )

    def test_malformed_candidate_evidence_cannot_satisfy_slot_ledger(self):
        scheduler_state, b1_state, decision_rows = complete_operating_states(
            date(2026, 8, 3),
            date(2026, 8, 3),
            schedule_times=("09:25",),
        )
        decision_rows[0]["candidate_evidence"] = [{
            "code": "600000",
            "best_strategy": "niu_leader",
        }]
        report = evaluate_niuone_forward(
            [],
            decision_rows=decision_rows,
            as_of="2026-08-03",
            cohort_start="2026-08-03",
        )
        _apply_operational_coverage(
            report,
            scheduler_state=scheduler_state,
            b1_state=b1_state,
            runtime_settings=operating_settings("09:25"),
            decision_rows=decision_rows,
        )

        self.assertEqual(
            report["opportunities"]["invalid_candidate_evidence_cycle_count"],
            1,
        )
        self.assertIn(
            "candidate_evidence.strategy_id",
            report["opportunities"]["invalid_opportunity_evidence_fields"],
        )
        self.assertEqual(
            report["operations"]["missing_requirement_counts"],
            {"practice_decision_ledger:09:25": 1},
        )

    def test_eligible_candidate_with_mismatched_route_is_invalid_evidence(self):
        row = {
            "_forward_payload_available": True,
            "candidate_evidence_schema_version": 2,
            "execution_evidence_schema_version": 2,
            "candidate_evidence": [candidate_evidence(
                "600000",
                stage="climax",
                strategy_id="niu_emerging",
            )],
            "time": "2026-08-03 10:00:05",
            "b1_generated_at": "2026-08-03 10:00:02",
            "schedule_slot": "2026-08-03 10:00",
            "schedule_run_kind": "scheduled",
            "decision": {"actions": []},
            "executed": [],
        }

        report = evaluate_niuone_forward(
            [],
            decision_rows=[row],
            as_of="2026-08-03",
            cohort_start="2026-08-03",
        )

        self.assertEqual(
            report["opportunities"]["invalid_candidate_evidence_cycle_count"],
            1,
        )
        self.assertIn(
            "candidate_evidence.niuone_lifecycle_strategy_route",
            report["opportunities"]["invalid_opportunity_evidence_fields"],
        )

    def test_rejected_model_buy_retains_structured_sizing_evidence(self):
        row = {
            "_forward_payload_available": True,
            "candidate_evidence_schema_version": 2,
            "execution_evidence_schema_version": 2,
            "candidate_evidence": [candidate_evidence("600000")],
            "time": "2026-08-03 10:00:05",
            "b1_generated_at": "2026-08-03 10:00:02",
            "schedule_slot": "2026-08-03 10:00",
            "schedule_run_kind": "scheduled",
            "decision": {
                "actions": [{
                    "action": "BUY",
                    "code": "600000",
                    "shares": 300,
                    "model_requested_shares": 300,
                    "maximum_permitted_shares": 0,
                    "risk_ceiling_utilization_pct": None,
                    "risk_ceiling_binding_constraints": [
                        "single_name_risk"
                    ],
                    "position_opened": True,
                }],
                "execution_blocks": [{
                    "code": "600000",
                    "category": "risk_ceiling",
                    "reason": "风险许可股数为零",
                }],
            },
            "executed": [],
        }

        report = evaluate_niuone_forward(
            [],
            decision_rows=[row],
            as_of="2026-08-03",
            cohort_start="2026-08-03",
        )
        sizing = report["opportunities"]["execution_sizing"]

        self.assertEqual(sizing["model_buy_order_count"], 1)
        self.assertEqual(sizing["rejected_buy_order_count"], 1)
        self.assertEqual(sizing["risk_ceiling_rejection_count"], 1)
        self.assertEqual(
            sizing["rejection_category_counts"],
            {"risk_ceiling": 1},
        )
        self.assertTrue(sizing["sizing_data_quality_gate_met"])
        self.assertTrue(
            report["opportunities"]["funnel_data_quality_gate_met"]
        )

        early_rejection = dict(row)
        early_rejection["decision"] = {
            "actions": [{
                "action": "BUY",
                "code": "600000",
                "shares": 300,
            }],
            "execution_blocks": [{
                "code": "600000",
                "category": "risk_ceiling",
                "reason": "主题敞口在结构止损计算前已超过上限",
            }],
        }
        early_sizing = evaluate_niuone_forward(
            [],
            decision_rows=[early_rejection],
            as_of="2026-08-03",
            cohort_start="2026-08-03",
        )["opportunities"]["execution_sizing"]
        self.assertTrue(early_sizing["sizing_data_quality_gate_met"])
        self.assertEqual(
            early_sizing["executed_risk_ceiling_evidence_count"],
            0,
        )

        missed_reduction = dict(row)
        missed_reduction["decision"] = {
            "actions": [{
                "action": "BUY",
                "code": "600000",
                "shares": 300,
                "model_requested_shares": 300,
                "maximum_permitted_shares": 200,
                "risk_ceiling_utilization_pct": 150.0,
                "risk_ceiling_binding_constraints": [
                    "single_name_risk"
                ],
                "position_opened": True,
            }],
            "execution_blocks": [{
                "code": "600000",
                "category": "risk_ceiling",
                "reason": "超限订单没有按 v18 规则裁到风险上限",
            }],
        }
        missed_sizing = evaluate_niuone_forward(
            [],
            decision_rows=[missed_reduction],
            as_of="2026-08-03",
            cohort_start="2026-08-03",
        )["opportunities"]["execution_sizing"]
        self.assertFalse(missed_sizing["sizing_data_quality_gate_met"])
        self.assertEqual(
            missed_sizing["invalid_sizing_evidence_fields"],
            {"rejected_buy.oversized_niuone_buy_not_auto_reduced": 1},
        )

        del row["decision"]["execution_blocks"]
        invalid = evaluate_niuone_forward(
            [],
            decision_rows=[row],
            as_of="2026-08-03",
            cohort_start="2026-08-03",
        )["opportunities"]
        self.assertFalse(
            invalid["execution_sizing_data_quality_gate_met"]
        )
        self.assertEqual(
            invalid["execution_sizing"]["invalid_sizing_evidence_fields"],
            {
                "decision.execution_blocks": 1,
                "rejected_buy.execution_block": 1,
            },
        )

    def test_sell_execution_audits_niuone_t1_quantity_reduction(self):
        sell = trade(
            "2026-08-04 10:00:00",
            "SELL",
            "600000",
            800,
            8000,
        )
        sell.update({
            "_forward_payload_available": True,
            "sell_execution_evidence_schema_version": 1,
            "sell_execution_source": "model_action",
            "model_requested_sell_shares": 1000,
            "available_sell_shares": 800,
            "sell_quantity_auto_reduced": True,
        })
        automatic_exit = trade(
            "2026-08-04 10:01:00",
            "SELL",
            "600001",
            100,
            1000,
        )
        automatic_exit["_forward_payload_available"] = True

        opportunities = evaluate_niuone_forward(
            [sell, automatic_exit],
            as_of="2026-08-04",
        )["opportunities"]
        audit = opportunities["sell_execution"]

        self.assertEqual(audit["schema_version"], 1)
        self.assertEqual(audit["model_sell_fill_count"], 1)
        self.assertEqual(audit["automatic_sell_fill_count"], 1)
        self.assertEqual(audit["auto_reduced_sell_fill_count"], 1)
        self.assertEqual(audit["requested_share_count"], 1000)
        self.assertEqual(audit["executed_share_count"], 800)
        self.assertEqual(audit["auto_reduced_share_count"], 200)
        self.assertTrue(audit["sell_execution_data_quality_gate_met"])
        self.assertTrue(opportunities["funnel_data_quality_gate_met"])

        invalid_sell = dict(sell)
        invalid_sell["shares"] = 1000
        invalid = evaluate_niuone_forward(
            [invalid_sell],
            as_of="2026-08-04",
        )["opportunities"]

        self.assertFalse(
            invalid["sell_execution"]
            ["sell_execution_data_quality_gate_met"]
        )
        self.assertEqual(
            invalid["sell_execution"]
            ["invalid_sell_execution_evidence_fields"],
            {
                "durable_sell_fill.sell_quantity_auto_reduced": 1,
                "durable_sell_fill.shares": 1,
            },
        )
        self.assertFalse(invalid["funnel_data_quality_gate_met"])

        invalid_report = evaluate_niuone_forward(
            [invalid_sell],
            as_of="2026-08-04",
        )
        scheduler_state, b1_state, decision_rows = (
            complete_operating_states(
                date(2026, 8, 3),
                date(2026, 8, 4),
                schedule_times=("09:25",),
            )
        )
        _apply_operational_coverage(
            invalid_report,
            scheduler_state=scheduler_state,
            b1_state=b1_state,
            runtime_settings=operating_settings("09:25"),
            decision_rows=decision_rows,
        )
        self.assertTrue(
            invalid_report["operations"]
            ["opportunity_funnel_quality_applicable"]
        )
        self.assertFalse(
            invalid_report["operations"]
            ["opportunity_funnel_data_quality_gate_met"]
        )

    def test_operational_coverage_rejects_late_protocol_preflight(self):
        scheduler_state, b1_state, decision_rows = complete_operating_states(
            date(2026, 8, 3),
            date(2026, 8, 3),
        )
        preflight = scheduler_state["job_history"]["2026-08-03"][
            "DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON"
        ][0]
        preflight["completed_at"] = "2026-08-03T09:25:00+08:00"
        rows = [
            trade(
                "2026-08-03 10:00:00", "BUY", "late-lock", 100, 1000,
                before_qty=0, after_qty=100,
                context=complete_context(
                    entry_signal_generated_at="2026-08-03 10:00:05",
                    entry_schedule_slot="2026-08-03 10:00",
                    entry_schedule_triggered_at="2026-08-03 10:00:00",
                ),
            ),
            trade(
                "2026-08-03 14:00:00", "SELL", "late-lock", 100, 1010,
                before_qty=100, after_qty=0,
                context=complete_context(
                    entry_signal_generated_at="2026-08-03 10:00:05",
                    entry_schedule_slot="2026-08-03 10:00",
                    entry_schedule_triggered_at="2026-08-03 10:00:00",
                ),
            ),
        ]
        report = evaluate_niuone_forward(
            rows,
            cohort_start="2026-08-03",
            as_of="2026-08-03",
            minimum_completed_trades=1,
        )
        _apply_operational_coverage(
            report,
            scheduler_state=scheduler_state,
            b1_state=b1_state,
            runtime_settings=operating_settings("09:25", "10:00"),
            decision_rows=decision_rows,
        )

        self.assertEqual(report["evidence_gate"]["status"], "operations_blocked")
        self.assertEqual(
            report["operations"]["missing_requirement_counts"],
            {"protocol_preflight_before_first_decision": 1},
        )

    def test_operational_gap_does_not_mask_attribution_failure(self):
        rows = [
            trade(
                "2026-08-03 10:00:00", "BUY", "legacy", 100, 1000,
                before_qty=0, after_qty=100,
            ),
            trade(
                "2026-08-04 10:00:00", "SELL", "legacy", 100, 1010,
                before_qty=100, after_qty=0,
            ),
        ]
        report = evaluate_niuone_forward(
            rows,
            as_of="2026-08-04",
            minimum_completed_trades=1,
            cohort_start="2026-08-03",
        )
        _apply_operational_coverage(
            report,
            scheduler_state={},
            b1_state={},
            runtime_settings=operating_settings("09:25", "10:00"),
        )

        self.assertEqual(report["evidence_gate"]["status"], "data_quality_blocked")
        self.assertEqual(
            report["evidence_gate"]["decision"],
            "incomplete_forward_attribution",
        )
        self.assertFalse(
            report["evidence_gate"]["sample_evidence_gate_met_before_operations"]
        )

    def test_protocol_lock_detects_source_code_identity_drift(self):
        original = {
            "schema_version": 1,
            "protocol": {"version": "test"},
            "source_files": {"strategy.py": "old"},
            "runtime_settings": {},
        }
        changed = {
            **original,
            "source_files": {"strategy.py": "new"},
        }
        with tempfile.TemporaryDirectory(
            prefix="niuone-forward-source-lock-"
        ) as directory:
            lock_path = Path(directory) / "protocol.json"
            frozen = _freeze_protocol_lock(
                lock_path,
                original,
                frozen_at="2026-08-03",
                refresh_date=date(2026, 8, 3),
            )
            before = lock_path.read_bytes()
            mismatch = _freeze_protocol_lock(
                lock_path,
                changed,
                frozen_at="2026-08-04",
                refresh_date=date(2026, 8, 4),
            )
            after = lock_path.read_bytes()

        self.assertEqual(frozen["status"], "frozen")
        self.assertEqual(mismatch["status"], "mismatch")
        self.assertEqual(
            mismatch["changed_fields"],
            ["source_files.strategy.py"],
        )
        self.assertEqual(after, before)

    def test_uses_complete_lifecycles_with_partial_exits_and_fees(self):
        context = complete_context()
        rows = [
            trade("2026-08-01 10:00:00", "BUY", "pre", 100, 1000, before_qty=0, after_qty=100),
            trade("2026-08-04 10:00:00", "SELL", "pre", 100, 1100, before_qty=100, after_qty=0),
            trade("2026-08-04 10:00:00", "SELL", "orphan", 100, 1000),
            trade(
                "2026-08-04 10:01:00", "BUY", "win", 100, 1000,
                fee=5, context=context, before_qty=0, after_qty=100,
            ),
            trade("2026-08-05 10:01:00", "BUY", "win", 100, 1000, fee=5, before_qty=100, after_qty=200),
            trade("2026-08-06 10:01:00", "SELL", "win", 100, 1205, fee=5, before_qty=200, after_qty=100),
            trade("2026-08-07 10:01:00", "SELL", "win", 100, 905, fee=5, before_qty=100, after_qty=0),
            trade(
                "2026-08-05 10:00:00", "BUY", "loss", 100, 1000,
                fee=5, before_qty=0, after_qty=100, context={
                    "entry_mainline_state": "emerging",
                    "entry_signal_score": 8.7,
                    "entry_same_stage_candidate_rank": 2,
                    "entry_execution_gap_pct": 1.5,
                    "entry_daily_v_recovery_ratio": 2.5,
                },
            ),
            trade("2026-08-08 10:00:00", "SELL", "loss", 100, 905, fee=5, before_qty=100, after_qty=0),
            trade(
                "2026-08-05 11:00:00", "BUY", "other", 100, 1000,
                strategy="trend_pullback", before_qty=0, after_qty=100,
            ),
            trade(
                "2026-08-08 11:00:00", "SELL", "other", 100, 1100,
                strategy="trend_pullback", before_qty=100, after_qty=0,
            ),
            trade("2026-08-09 10:00:00", "BUY", "open", 100, 1000, before_qty=0, after_qty=100),
        ]

        report = evaluate_niuone_forward(rows, as_of="2026-08-20")

        self.assertEqual(report["overall"]["completed_trade_count"], 2)
        self.assertEqual(report["overall"]["win_count"], 1)
        self.assertEqual(report["overall"]["win_rate_pct"], 50.0)
        self.assertEqual(
            report["overall"]["win_rate_wilson_95_lower_pct"],
            9.4531,
        )
        self.assertEqual(
            report["overall"]["win_rate_wilson_95_upper_pct"],
            90.5469,
        )
        self.assertEqual(report["overall"]["realized_pnl"], -15.0)
        self.assertEqual(report["overall"]["profit_factor"], 0.8571)
        self.assertEqual(report["coverage"]["orphan_sell_count"], 1)
        self.assertEqual(report["coverage"]["open_niuone_lifecycle_count"], 1)
        self.assertEqual(report["coverage"]["entry_signal_timestamp_trade_count"], 1)
        self.assertEqual(report["coverage"]["entry_signal_timestamp_coverage_pct"], 50.0)
        self.assertEqual(report["coverage"]["deferred_entry_trade_count"], 1)
        self.assertEqual(
            report["coverage"]["complete_entry_attribution_trade_count"],
            1,
        )
        self.assertEqual(
            report["coverage"]["complete_entry_attribution_coverage_pct"],
            50.0,
        )
        self.assertFalse(report["evidence_gate"]["data_quality_gate_met"])
        self.assertEqual(
            report["groups"]["entry_stage"]["niu_reversal_probe"]
            ["completed_trade_count"],
            2,
        )
        self.assertEqual(
            report["groups"]["entry_lifecycle_stage"]["brewing"]
            ["completed_trade_count"],
            1,
        )
        self.assertEqual(
            report["groups"]["shadow_execution_gap"]["le_1_pct"]
            ["win_rate_pct"],
            100.0,
        )
        self.assertEqual(
            report["groups"]["shadow_execution_gap"]["gt_1_pct"]
            ["win_rate_pct"],
            0.0,
        )
        self.assertEqual(
            report["groups"]["shadow_recovery_ratio"]["lt_2"]
            ["win_rate_pct"],
            100.0,
        )
        self.assertEqual(
            report["groups"]["shadow_recovery_ratio"]["ge_2"]
            ["win_rate_pct"],
            0.0,
        )
        self.assertEqual(report["evidence_gate"]["status"], "collecting")
        self.assertFalse(report["evidence_gate"]["evidence_gate_met"])

    def test_evidence_gate_opens_after_three_full_months(self):
        before = evaluate_niuone_forward([], as_of="2026-11-03")
        at_gate = evaluate_niuone_forward([], as_of="2026-11-04")

        self.assertEqual(
            before["evidence_gate"]["full_calendar_months_elapsed"],
            2,
        )
        self.assertFalse(before["evidence_gate"]["evidence_gate_met"])
        self.assertEqual(
            at_gate["evidence_gate"]["full_calendar_months_elapsed"],
            3,
        )
        self.assertTrue(at_gate["evidence_gate"]["calendar_month_gate_met"])
        self.assertEqual(
            at_gate["evidence_gate"]["decision"],
            "eligible_for_manual_review",
        )
        self.assertEqual(
            at_gate["evidence_gate"]["review_scope"],
            "frequency_and_operations_only",
        )
        self.assertEqual(
            at_gate["performance_assessment"]["status"],
            "insufficient_completed_lifecycles",
        )
        self.assertFalse(
            at_gate["performance_assessment"]
            ["high_win_rate_and_positive_return_claim_supported"]
        )
        self.assertFalse(
            at_gate["interpretation"]["automatic_promotion_allowed"]
        )

    def test_completed_trade_gate_is_independent_of_time_gate(self):
        rows = []
        for index in range(30):
            code = f"{index:06d}"
            context = complete_context(entry_theme=f"题材-{index:02d}")
            rows.extend([
                trade(
                    "2026-08-04 10:00:00", "BUY", code, 100, 1000,
                    before_qty=0, after_qty=100,
                    context=context,
                ),
                trade("2026-08-05 10:00:00", "SELL", code, 100, 1010, before_qty=100, after_qty=0),
            ])

        report = evaluate_niuone_forward(rows, as_of="2026-08-06")

        self.assertTrue(report["evidence_gate"]["completed_trade_gate_met"])
        self.assertFalse(report["evidence_gate"]["calendar_month_gate_met"])
        self.assertTrue(report["evidence_gate"]["data_quality_gate_met"])
        self.assertTrue(report["evidence_gate"]["evidence_gate_met"])
        self.assertEqual(
            report["performance_assessment"]["status"],
            "portfolio_evidence_blocked",
        )
        self.assertFalse(
            report["performance_assessment"]
            ["high_win_rate_and_positive_return_claim_supported"]
        )
        self.assertTrue(
            report["performance_assessment"]
            ["lifecycle_performance_criteria_met"]
        )
        self.assertEqual(report["performance_clustering"]["cluster_count"], 30)
        self.assertEqual(
            report["performance_clustering"]
            ["herfindahl_effective_cluster_count"],
            30.0,
        )
        self.assertTrue(
            report["performance_clustering"]["cluster_guardrail_met"]
        )

    def test_same_date_industry_wave_cannot_count_as_independent_trades(self):
        rows = []
        for index in range(30):
            code = f"same-wave-{index:02d}"
            rows.extend([
                trade(
                    "2026-08-04 10:00:00",
                    "BUY",
                    code,
                    100,
                    1000,
                    before_qty=0,
                    after_qty=100,
                    context=complete_context(),
                ),
                trade(
                    "2026-08-05 10:00:00",
                    "SELL",
                    code,
                    100,
                    1010,
                    before_qty=100,
                    after_qty=0,
                ),
            ])

        report = evaluate_niuone_forward(rows, as_of="2026-08-06")
        clustering = report["performance_clustering"]
        assessment = report["performance_assessment"]

        self.assertEqual(report["overall"]["win_rate_pct"], 100.0)
        self.assertTrue(
            assessment["trade_level_lifecycle_performance_criteria_met"]
        )
        self.assertEqual(clustering["cluster_count"], 1)
        self.assertEqual(clustering["largest_cluster_size"], 30)
        self.assertEqual(
            clustering["herfindahl_effective_cluster_count"],
            1.0,
        )
        self.assertFalse(clustering["cluster_guardrail_met"])
        self.assertFalse(assessment["lifecycle_performance_criteria_met"])
        self.assertEqual(
            assessment["status"],
            "insufficient_independent_clusters",
        )

    def test_effective_cluster_count_blocks_concentrated_wave_mix(self):
        rows = []
        industries = ["集中行业"] * 10 + [
            f"分散行业-{index:02d}" for index in range(29)
        ]
        for index, industry in enumerate(industries):
            code = f"cluster-mix-{index:02d}"
            context = complete_context(entry_theme=industry)
            rows.extend([
                trade(
                    "2026-08-04 10:00:00",
                    "BUY",
                    code,
                    100,
                    1000,
                    before_qty=0,
                    after_qty=100,
                    context=context,
                ),
                trade(
                    "2026-08-05 10:00:00",
                    "SELL",
                    code,
                    100,
                    1010,
                    before_qty=100,
                    after_qty=0,
                ),
            ])

        report = evaluate_niuone_forward(rows, as_of="2026-08-06")
        clustering = report["performance_clustering"]

        self.assertEqual(clustering["cluster_count"], 30)
        self.assertTrue(clustering["unique_cluster_gate_met"])
        self.assertEqual(
            clustering["herfindahl_effective_cluster_count"],
            11.7907,
        )
        self.assertFalse(clustering["effective_cluster_gate_met"])
        self.assertEqual(
            report["performance_assessment"]["status"],
            "performance_cluster_concentration_too_high",
        )

    def test_complete_daily_account_curve_enables_risk_adjusted_claim_review(self):
        rows = []
        for index in range(30):
            code = f"portfolio-{index:02d}"
            industry = f"行业-{index:02d}"
            rows.extend([
                trade(
                    "2026-08-03 10:00:00",
                    "BUY",
                    code,
                    100,
                    1000,
                    before_qty=0,
                    after_qty=100,
                    context=complete_context(
                        entry_theme=industry,
                        entry_signal_generated_at="2026-08-03 10:00:01",
                        entry_schedule_slot="2026-08-03 10:00",
                        entry_schedule_triggered_at="2026-08-03 10:00:00",
                    ),
                ),
                trade(
                    "2026-08-05 10:00:00",
                    "SELL",
                    code,
                    100,
                    1666.6666667,
                    before_qty=100,
                    after_qty=0,
                    context=complete_context(
                        entry_theme=industry,
                        entry_signal_generated_at="2026-08-03 10:00:01",
                        entry_schedule_slot="2026-08-03 10:00",
                        entry_schedule_triggered_at="2026-08-03 10:00:00",
                    ),
                ),
            ])
        equity_rows = [
            daily_equity_point("2026-08-03", 1_000_000, cash=970_000),
            daily_equity_point("2026-08-04", 990_000, cash=970_000),
            daily_equity_point("2026-08-05", 1_020_000),
        ]
        report = evaluate_niuone_forward(
            rows,
            daily_equity_rows=equity_rows,
            account_baseline=clean_account_baseline(),
            as_of="2026-08-05",
            cohort_start="2026-08-03",
        )

        self.assertEqual(report["portfolio"]["status"], "portfolio_guardrail_met")
        self.assertTrue(
            report["portfolio"]
            ["portfolio_return_and_drawdown_evidence_available"]
        )
        self.assertEqual(report["portfolio"]["portfolio_return_pct"], 2.0)
        self.assertEqual(report["portfolio"]["maximum_drawdown_pct"], -1.0)
        self.assertEqual(report["portfolio"]["return_to_drawdown_ratio"], 2.0)
        self.assertEqual(
            report["performance_assessment"]["status"],
            "pending_operations_review",
        )
        self.assertFalse(
            report["performance_assessment"]
            ["high_win_rate_and_positive_return_claim_supported"]
        )

        scheduler_state, b1_state, decision_rows = complete_operating_states(
            date(2026, 8, 3),
            date(2026, 8, 5),
        )
        _apply_operational_coverage(
            report,
            scheduler_state=scheduler_state,
            b1_state=b1_state,
            runtime_settings=operating_settings("09:25", "10:00"),
            decision_rows=decision_rows,
        )
        self.assertEqual(
            report["performance_assessment"]["status"],
            "claim_supported_for_manual_review",
        )
        self.assertTrue(
            report["performance_assessment"]
            ["high_win_rate_and_positive_return_claim_supported"]
        )
        self.assertTrue(
            report["performance_assessment"]
            ["positive_risk_adjusted_portfolio_return_supported"]
        )
        self.assertFalse(
            report["performance_assessment"]
            ["high_portfolio_return_claim_supported"]
        )

        blocked_scheduler = json.loads(json.dumps(scheduler_state))
        del blocked_scheduler["job_history"]["2026-08-04"][
            "DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON"
        ]
        blocked = evaluate_niuone_forward(
            rows,
            daily_equity_rows=equity_rows,
            account_baseline=clean_account_baseline(),
            as_of="2026-08-05",
            cohort_start="2026-08-03",
        )
        _apply_operational_coverage(
            blocked,
            scheduler_state=blocked_scheduler,
            b1_state=b1_state,
            runtime_settings=operating_settings("09:25", "10:00"),
            decision_rows=decision_rows,
        )
        self.assertEqual(
            blocked["performance_assessment"]["status"],
            "operations_blocked",
        )
        self.assertFalse(
            blocked["performance_assessment"]
            ["high_win_rate_and_positive_return_claim_supported"]
        )
        self.assertEqual(
            blocked["operations"]["missing_requirement_counts"],
            {"closing_equity_snapshot_ok": 1},
        )

    def test_account_curve_fails_closed_on_contamination_or_missing_close(self):
        rows = [
            trade(
                "2026-08-03 10:00:00",
                "BUY",
                "niuone",
                100,
                1000,
                before_qty=0,
                after_qty=100,
                context=complete_context(),
            ),
            trade(
                "2026-08-04 10:00:00",
                "SELL",
                "niuone",
                100,
                1010,
                before_qty=100,
                after_qty=0,
            ),
        ]
        equity_rows = [
            daily_equity_point("2026-08-03", 999_000, cash=998_000),
            daily_equity_point("2026-08-04", 1_001_000),
        ]
        contaminated = evaluate_niuone_forward(
            [
                *rows,
                trade(
                    "2026-08-04 11:00:00",
                    "BUY",
                    "other",
                    100,
                    1000,
                    strategy="shaofu_b1",
                    before_qty=0,
                    after_qty=100,
                ),
            ],
            daily_equity_rows=equity_rows,
            account_baseline=clean_account_baseline(),
            as_of="2026-08-04",
            minimum_completed_trades=1,
        )
        self.assertEqual(
            contaminated["portfolio"]["status"],
            "non_niuone_account_activity_detected",
        )
        self.assertEqual(contaminated["portfolio"]["non_niuone_trade_count"], 1)
        self.assertFalse(
            contaminated["portfolio"]
            ["portfolio_return_and_drawdown_evidence_available"]
        )

        early_mark = [dict(point) for point in equity_rows]
        early_mark[-1]["created_at"] = "2026-08-04 14:50:00"
        incomplete = evaluate_niuone_forward(
            rows,
            daily_equity_rows=early_mark,
            account_baseline=clean_account_baseline(),
            as_of="2026-08-04",
            minimum_completed_trades=1,
        )
        self.assertEqual(
            incomplete["portfolio"]["status"],
            "daily_equity_quality_blocked",
        )
        self.assertEqual(
            incomplete["portfolio"]["invalid_daily_equity_fields"],
            {"daily_equity.closing_snapshot": 1},
        )

        reset_curve = [dict(point) for point in equity_rows]
        reset_curve[-1]["account_created_at"] = "2026-08-04 09:00:00"
        reset = evaluate_niuone_forward(
            rows,
            daily_equity_rows=reset_curve,
            account_baseline=clean_account_baseline(),
            as_of="2026-08-04",
            minimum_completed_trades=1,
        )
        self.assertEqual(
            reset["portfolio"]["invalid_daily_equity_fields"],
            {"daily_equity.account_session": 1},
        )

    def test_high_point_win_rate_remains_uncertain_when_wilson_crosses_half(self):
        rows = []
        for index in range(30):
            code = f"uncertain-{index:02d}"
            exit_amount = 1010 if index < 18 else 995
            rows.extend([
                trade(
                    "2026-08-04 10:00:00",
                    "BUY",
                    code,
                    100,
                    1000,
                    before_qty=0,
                    after_qty=100,
                    context=complete_context(),
                ),
                trade(
                    "2026-08-05 10:00:00",
                    "SELL",
                    code,
                    100,
                    exit_amount,
                    before_qty=100,
                    after_qty=0,
                ),
            ])

        report = evaluate_niuone_forward(rows, as_of="2026-08-06")
        assessment = report["performance_assessment"]

        self.assertEqual(report["overall"]["win_rate_pct"], 60.0)
        self.assertEqual(
            report["overall"]["win_rate_wilson_95_lower_pct"],
            42.3204,
        )
        self.assertTrue(assessment["historical_reference_win_rate_met"])
        self.assertTrue(assessment["return_quality_guardrail_met"])
        self.assertFalse(assessment["positive_win_rate_edge_met"])
        self.assertEqual(
            assessment["status"],
            "win_rate_statistically_uncertain",
        )

    def test_high_win_rate_cannot_hide_negative_return_quality(self):
        rows = []
        for index in range(30):
            code = f"bad-payoff-{index:02d}"
            exit_amount = 1001 if index < 21 else 990
            rows.extend([
                trade(
                    "2026-08-04 10:00:00",
                    "BUY",
                    code,
                    100,
                    1000,
                    before_qty=0,
                    after_qty=100,
                    context=complete_context(),
                ),
                trade(
                    "2026-08-05 10:00:00",
                    "SELL",
                    code,
                    100,
                    exit_amount,
                    before_qty=100,
                    after_qty=0,
                ),
            ])

        report = evaluate_niuone_forward(rows, as_of="2026-08-06")
        assessment = report["performance_assessment"]

        self.assertEqual(report["overall"]["win_rate_pct"], 70.0)
        self.assertTrue(assessment["positive_win_rate_edge_met"])
        self.assertFalse(assessment["return_quality_guardrail_met"])
        self.assertEqual(
            assessment["status"],
            "return_quality_below_break_even",
        )

    def test_completed_trade_gate_is_blocked_by_incomplete_attribution(self):
        rows = []
        for index in range(30):
            code = f"{index:06d}"
            buy = trade(
                "2026-08-04 10:00:00", "BUY", code, 100, 1000,
                before_qty=0, after_qty=100,
                context=complete_context(),
            )
            buy["_forward_payload_available"] = False
            rows.extend([
                buy,
                trade(
                    "2026-08-05 10:00:00", "SELL", code, 100, 1010,
                    before_qty=100, after_qty=0,
                ),
            ])

        report = evaluate_niuone_forward(rows, as_of="2026-08-06")

        self.assertTrue(report["evidence_gate"]["sample_gate_met"])
        self.assertFalse(report["evidence_gate"]["data_quality_gate_met"])
        self.assertFalse(report["evidence_gate"]["evidence_gate_met"])
        self.assertEqual(
            report["evidence_gate"]["status"],
            "data_quality_blocked",
        )
        self.assertEqual(
            report["evidence_gate"]["decision"],
            "incomplete_forward_attribution",
        )
        self.assertEqual(
            report["coverage"]["legacy_entry_payload_trade_count"],
            30,
        )
        self.assertEqual(
            report["coverage"]["missing_entry_attribution_fields"],
            {"entry_payload": 30},
        )

    def test_scheduled_entry_without_slot_is_incomplete_attribution(self):
        rows = [
            trade(
                "2026-08-04 10:00:00", "BUY", "missing-slot", 100, 1000,
                before_qty=0, after_qty=100,
                context=complete_context(entry_schedule_slot=""),
            ),
            trade(
                "2026-08-05 10:00:00", "SELL", "missing-slot", 100, 1010,
                before_qty=100, after_qty=0,
            ),
        ]

        report = evaluate_niuone_forward(rows, as_of="2026-08-06")

        self.assertEqual(
            report["coverage"]["missing_entry_attribution_fields"],
            {"entry_schedule_slot": 1},
        )
        self.assertEqual(
            report["protocol"]["conditional_entry_context_rules"],
            {
                "entry_schedule_slot": (
                    "required_when_entry_schedule_run_kind_is_scheduled_or_catchup"
                ),
            },
        )

    def test_missing_entry_industry_blocks_performance_attribution(self):
        rows = [
            trade(
                "2026-08-04 10:00:00",
                "BUY",
                "missing-industry",
                100,
                1000,
                before_qty=0,
                after_qty=100,
                context=complete_context(entry_industry=""),
            ),
            trade(
                "2026-08-05 10:00:00",
                "SELL",
                "missing-industry",
                100,
                1010,
                before_qty=100,
                after_qty=0,
            ),
        ]

        report = evaluate_niuone_forward(rows, as_of="2026-08-06")

        self.assertEqual(
            report["coverage"]["missing_entry_attribution_fields"],
            {"entry_industry": 1},
        )
        self.assertEqual(
            report["groups"]["entry_industry"]["missing"]
            ["completed_trade_count"],
            1,
        )

    def test_missing_entry_theme_blocks_theme_cluster_attribution(self):
        rows = [
            trade(
                "2026-08-04 10:00:00",
                "BUY",
                "missing-theme",
                100,
                1000,
                before_qty=0,
                after_qty=100,
                context=complete_context(entry_theme=""),
            ),
            trade(
                "2026-08-05 10:00:00",
                "SELL",
                "missing-theme",
                100,
                1010,
                before_qty=100,
                after_qty=0,
            ),
        ]

        report = evaluate_niuone_forward(rows, as_of="2026-08-06")

        self.assertEqual(
            report["coverage"]["missing_entry_attribution_fields"],
            {"entry_theme": 1},
        )
        self.assertEqual(
            report["groups"]["entry_theme"]["missing"]
            ["completed_trade_count"],
            1,
        )

    def test_out_of_range_theme_attribution_is_incomplete(self):
        rows = [
            trade(
                "2026-08-04 10:00:00",
                "BUY",
                "invalid-theme-attribution",
                100,
                1000,
                before_qty=0,
                after_qty=100,
                context=complete_context(
                    entry_theme_attribution_score=101.0,
                ),
            ),
            trade(
                "2026-08-05 10:00:00",
                "SELL",
                "invalid-theme-attribution",
                100,
                1010,
                before_qty=100,
                after_qty=0,
            ),
        ]

        report = evaluate_niuone_forward(rows, as_of="2026-08-06")

        self.assertEqual(
            report["coverage"]["missing_entry_attribution_fields"],
            {"entry_theme_attribution_score": 1},
        )

    def test_state_overlay_rows_cannot_replace_durable_payload_evidence(self):
        rows = _mark_non_durable_overlay([
            trade(
                "2026-08-04 10:00:00", "BUY", "overlay", 100, 1000,
                before_qty=0, after_qty=100,
                context=complete_context(),
            ),
        ])

        self.assertIs(rows[0]["_forward_payload_available"], False)

    def test_completed_lifecycle_requires_holding_path_and_exit_stage(self):
        rows = [
            trade(
                "2026-08-04 10:00:00", "BUY", "missing-path", 100,
                1000, before_qty=0, after_qty=100,
                context=complete_context(),
            ),
            trade(
                "2026-08-05 10:00:00", "SELL", "missing-path", 100,
                1010, before_qty=100, after_qty=0,
                lifecycle_evidence=False,
            ),
        ]

        report = evaluate_niuone_forward(
            rows,
            as_of="2026-08-05",
            minimum_completed_trades=1,
            cohort_start="2026-08-03",
        )

        self.assertFalse(report["evidence_gate"]["data_quality_gate_met"])
        self.assertEqual(
            report["coverage"]["missing_lifecycle_attribution_fields"],
            {"niuone_lifecycle_evidence": 1},
        )
        self.assertEqual(
            report["coverage"]
            ["complete_holding_lifecycle_path_coverage_pct"],
            0.0,
        )

    def test_holding_lifecycle_summary_tracks_stage_path_and_exit_stage(self):
        context = complete_context()
        sequence = ("brewing", "markup", "divergence", "fade")
        rows = [
            trade(
                "2026-08-04 10:00:00", "BUY", "stage-path", 100,
                1000, before_qty=0, after_qty=100, context=context,
            ),
            trade(
                "2026-08-05 10:00:00", "SELL", "stage-path", 100,
                1020, before_qty=100, after_qty=0,
                lifecycle_evidence=complete_lifecycle_evidence(
                    entry_context=context,
                    exit_time="2026-08-05 10:00:00",
                    stage_sequence=sequence,
                ),
            ),
        ]

        report = evaluate_niuone_forward(
            rows,
            as_of="2026-08-05",
            minimum_completed_trades=1,
            cohort_start="2026-08-03",
        )

        self.assertTrue(report["evidence_gate"]["data_quality_gate_met"])
        lifecycle = report["holding_lifecycle"]
        self.assertEqual(lifecycle["complete_path_count"], 1)
        self.assertEqual(lifecycle["stage_reach"]["markup"]["count"], 1)
        self.assertEqual(lifecycle["stage_reach"]["climax"]["count"], 0)
        self.assertEqual(
            lifecycle["transitions"],
            {
                "brewing->markup": 1,
                "divergence->fade": 1,
                "markup->divergence": 1,
            },
        )
        self.assertEqual(
            report["groups"]["exit_lifecycle_stage"]["fade"]
            ["completed_trade_count"],
            1,
        )
        self.assertEqual(
            report["groups"]["holding_lifecycle_path"]
            ["brewing->markup->divergence->fade"]
            ["completed_trade_count"],
            1,
        )

    def test_holding_lifecycle_path_requires_each_operating_day(self):
        context = complete_context(
            entry_signal_generated_at="2026-08-03 10:00:01",
            entry_schedule_slot="2026-08-03 10:00",
            entry_schedule_triggered_at="2026-08-03 10:00:00",
        )
        evidence = complete_lifecycle_evidence(
            entry_context=context,
            exit_time="2026-08-05 10:00:00",
        )
        evidence["path"][0]["observations"] = [
            observation
            for observation in evidence["path"][0]["observations"]
            if not str(observation["observed_at"]).startswith("2026-08-04")
        ]
        evidence["path"][0]["observation_count"] = len(
            evidence["path"][0]["observations"]
        )
        rows = [
            trade(
                "2026-08-03 10:00:00", "BUY", "missing-day", 100,
                1000, before_qty=0, after_qty=100, context=context,
            ),
            trade(
                "2026-08-05 10:00:00", "SELL", "missing-day", 100,
                1010, before_qty=100, after_qty=0,
                lifecycle_evidence=evidence,
            ),
        ]

        report = evaluate_niuone_forward(
            rows,
            as_of="2026-08-05",
            minimum_completed_trades=1,
            cohort_start="2026-08-03",
        )

        self.assertFalse(report["evidence_gate"]["data_quality_gate_met"])
        self.assertEqual(
            report["coverage"]["missing_lifecycle_attribution_fields"],
            {
                "holding_lifecycle.operating_day_coverage": 1,
                "holding_lifecycle.operating_day_scan_coverage": 1,
            },
        )

    def test_mismatched_lifecycle_metadata_is_incomplete_attribution(self):
        rows = [
            trade(
                "2026-08-04 10:00:00", "BUY", "bad-stage", 100, 1000,
                before_qty=0, after_qty=100,
                context=complete_context(
                    entry_niuone_lifecycle_label="主线高潮",
                    entry_niuone_lifecycle_order=30,
                ),
            ),
            trade(
                "2026-08-05 10:00:00", "SELL", "bad-stage", 100, 1010,
                before_qty=100, after_qty=0,
            ),
        ]

        report = evaluate_niuone_forward(rows, as_of="2026-08-06")

        self.assertEqual(
            report["coverage"]["missing_entry_attribution_fields"],
            {
                "entry_niuone_lifecycle_label": 1,
                "entry_niuone_lifecycle_order": 1,
            },
        )

    def test_mismatched_entry_strategy_route_is_incomplete_attribution(self):
        rows = [
            trade(
                "2026-08-04 10:00:00",
                "BUY",
                "wrong-route",
                100,
                1000,
                strategy="niu_leader",
                before_qty=0,
                after_qty=100,
                context=complete_context(),
            ),
            trade(
                "2026-08-05 10:00:00",
                "SELL",
                "wrong-route",
                100,
                1010,
                strategy="niu_leader",
                before_qty=100,
                after_qty=0,
            ),
        ]

        report = evaluate_niuone_forward(rows, as_of="2026-08-06")

        self.assertEqual(
            report["coverage"]["missing_entry_attribution_fields"],
            {"entry_niuone_lifecycle_strategy_route": 1},
        )

    def test_excludes_a_trimmed_log_that_starts_with_an_add(self):
        rows = [
            trade(
                "2026-08-04 10:00:00", "BUY", "trimmed", 100, 1000,
                before_qty=100, after_qty=200,
            ),
            trade(
                "2026-08-05 10:00:00", "SELL", "trimmed", 200, 2200,
                before_qty=200, after_qty=0,
            ),
        ]

        report = evaluate_niuone_forward(rows, as_of="2026-08-06")

        self.assertEqual(report["overall"]["completed_trade_count"], 0)
        self.assertEqual(report["coverage"]["unverified_open_count"], 1)

    def test_exact_duplicate_fill_rows_are_idempotent(self):
        buy = trade(
            "2026-08-04 10:00:00", "BUY", "dup", 100, 1000,
            before_qty=0, after_qty=100,
        )
        sell = trade(
            "2026-08-05 10:00:00", "SELL", "dup", 100, 1100,
            before_qty=100, after_qty=0,
        )

        report = evaluate_niuone_forward(
            [buy, buy, sell, sell],
            as_of="2026-08-06",
        )

        self.assertEqual(report["overall"]["completed_trade_count"], 1)
        self.assertEqual(report["coverage"]["duplicate_trade_count"], 2)
        self.assertEqual(report["protocol"]["version"], "niuone-strict-forward-v33")
        self.assertEqual(
            report["protocol"][
                "niuone_markup_upgrade_absolute_position_cap_pct"
            ],
            20.0,
        )
        self.assertEqual(
            report["protocol"]["niuone_markup_upgrade_minimum_pnl_pct"],
            2.0,
        )
        self.assertEqual(
            report["protocol"][
                "niuone_markup_upgrade_maximum_pnl_pct"
            ],
            12.0,
        )
        self.assertEqual(
            report["protocol"][
                "niuone_markup_early_upgrade_absolute_position_cap_pct"
            ],
            10.0,
        )
        self.assertAlmostEqual(
            report["protocol"]["niuone_climax_partial_ratio"],
            1.0 / 3.0,
        )
        self.assertEqual(
            report["protocol"]["niuone_markup_rebalance_pullback_atr"],
            1.0,
        )
        self.assertEqual(
            report["protocol"]["niuone_markup_rebalance_stall_sessions"],
            3,
        )
        self.assertEqual(
            report["protocol"]["niuone_markup_rebalance_rebound_atr"],
            0.5,
        )
        self.assertIsNone(
            report["protocol"]
            ["niuone_markup_rebalance_lifetime_add_limit"]
        )
        self.assertEqual(report["protocol"]["candidate_evidence_schema_version"], 2)
        self.assertEqual(report["protocol"]["execution_evidence_schema_version"], 2)
        self.assertEqual(
            report["protocol"]["required_executed_buy_sizing_fields"],
            [
                "model_requested_shares",
                "maximum_permitted_shares",
                "risk_ceiling_utilization_pct",
                "risk_ceiling_binding_constraints",
                "position_opened",
                "risk_ceiling_auto_reduced",
            ],
        )
        self.assertIn(
            "execute at that ceiling",
            report["protocol"]["oversized_niuone_buy_rule"],
        )
        self.assertEqual(
            report["protocol"]["sell_execution_evidence_schema_version"],
            1,
        )
        self.assertEqual(
            report["protocol"]["required_executed_sell_sizing_fields"],
            [
                "sell_execution_source",
                "model_requested_sell_shares",
                "available_sell_shares",
                "sell_quantity_auto_reduced",
            ],
        )
        self.assertIn(
            "T+1 available quantity",
            report["protocol"]["oversized_niuone_sell_rule"],
        )
        self.assertEqual(
            report["protocol"]["required_candidate_evidence_fields"],
            [
                "code",
                "strategy_id",
                "observed_rank",
                "eligible_for_decision",
                "eligibility_blockers",
            ],
        )
        self.assertNotIn(
            "recovery_ratio",
            report["protocol"]["shadow_candidates"],
        )
        self.assertEqual(
            report["protocol"]
            ["niuone_reversal_minimum_recovery_ratio_inclusive"],
            0.60,
        )
        self.assertEqual(
            report["protocol"]
            ["niuone_reversal_maximum_recovery_ratio_exclusive"],
            2.0,
        )
        self.assertEqual(
            report["protocol"]
            ["niuone_leader_minimum_sector_rank_inclusive"],
            80.0,
        )
        self.assertEqual(
            report["protocol"]
            ["niuone_leader_minimum_today_strength_inclusive"],
            60.0,
        )
        self.assertEqual(
            report["protocol"]["niuone_startup_allowed_mainline_states"],
            ["emerging"],
        )

    def test_durable_db_retains_lifecycle_beyond_json_log_limit(self):
        with tempfile.TemporaryDirectory(prefix="niuone-forward-") as directory:
            db_path = Path(directory) / "practice.db"
            connection = sqlite3.connect(db_path)
            connection.executescript("""
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time TEXT NOT NULL,
                    action TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT DEFAULT '',
                    shares INTEGER NOT NULL,
                    price REAL NOT NULL,
                    amount REAL NOT NULL,
                    commission REAL DEFAULT 0,
                    transfer_fee REAL DEFAULT 0,
                    stamp_duty REAL DEFAULT 0,
                    reason TEXT DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT ''
                );
            """)
            all_rows = []
            for index in range(101):
                code = f"{index:06d}"
                buy = trade(
                    "2026-08-04 10:00:00", "BUY", code, 100, 1000,
                    before_qty=0, after_qty=100,
                )
                sell = trade(
                    "2026-08-05 10:00:00", "SELL", code, 100, 1010,
                    before_qty=100, after_qty=0,
                )
                all_rows.extend((buy, sell))
                for row in (buy, sell):
                    connection.execute(
                        """
                        INSERT INTO trades (
                            time, action, code, shares, price, amount, reason,
                            payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row["time"], row["action"], row["code"],
                            row["shares"], 10.0, row["amount"],
                            row.get("reason", ""),
                            json.dumps(row, ensure_ascii=False),
                        ),
                    )
            connection.commit()
            connection.close()

            db_rows, diagnostics = load_niuone_forward_trades_from_db(db_path)
            merged, duplicates = merge_forward_trade_rows(
                db_rows,
                all_rows[-200:],
            )
            report = evaluate_niuone_forward(merged, as_of="2026-08-06")

        self.assertEqual(diagnostics["rich_payload_trade_row_count"], 202)
        self.assertEqual(duplicates, 200)
        self.assertEqual(report["overall"]["completed_trade_count"], 101)
        self.assertEqual(report["coverage"]["rich_payload_trade_count"], 202)

    def test_daily_equity_loader_reads_durable_marks_without_writes(self):
        with tempfile.TemporaryDirectory(
            prefix="niuone-daily-equity-db-"
        ) as directory:
            db_path = Path(directory) / "practice.db"
            connection = sqlite3.connect(db_path)
            connection.execute("""
                CREATE TABLE daily_equity (
                    date TEXT PRIMARY KEY,
                    equity REAL NOT NULL,
                    cash REAL NOT NULL,
                    market_value REAL NOT NULL,
                    pnl_pct REAL NOT NULL,
                    account_created_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute(
                "INSERT INTO daily_equity VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "2026-08-03",
                    1_001_000.0,
                    900_000.0,
                    101_000.0,
                    0.1,
                    "2026-07-01 09:00:00",
                    "2026-08-03 15:15:00",
                ),
            )
            connection.commit()
            connection.close()

            rows, diagnostics = (
                load_niuone_forward_daily_equity_from_db(db_path)
            )
            connection = sqlite3.connect(db_path)
            row_count = connection.execute(
                "SELECT COUNT(*) FROM daily_equity"
            ).fetchone()[0]
            connection.close()

        self.assertEqual(diagnostics["database_daily_equity_row_count"], 1)
        self.assertEqual(row_count, 1)
        self.assertTrue(rows[0]["_forward_payload_available"])
        self.assertEqual(
            rows[0]["account_created_at"],
            "2026-07-01 09:00:00",
        )
        self.assertEqual(rows[0]["created_at"], "2026-08-03 15:15:00")

    def test_practice_database_persists_full_trade_payload_idempotently(self):
        with tempfile.TemporaryDirectory(prefix="niuone-practice-db-") as directory:
            db_path = Path(directory) / "practice.db"
            state_path = Path(directory) / "state.json"
            payload = trade(
                "2026-08-04 10:00:00", "BUY", "600000", 100, 1000,
                before_qty=0, after_qty=100,
                context={"entry_daily_v_recovery_ratio": 1.5},
            )
            code = (
                "import json; import niuniu_db; "
                f"row=json.loads({json.dumps(payload)!r}); "
                "niuniu_db.record_trade(row); niuniu_db.record_trade(row)"
            )
            env = os.environ.copy()
            env.update({
                "DASHBOARD_HOME": directory,
                "DASHBOARD_NIUNIU_DB": str(db_path),
                "DASHBOARD_PORTFOLIO_STATE": str(state_path),
                "PYTHONPATH": os.pathsep.join((
                    str(ROOT / "app" / "compat"),
                    str(ROOT / "app"),
                    str(ROOT),
                )),
            })
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            connection = sqlite3.connect(db_path)
            stored = connection.execute(
                "SELECT payload_json FROM trades"
            ).fetchall()
            connection.close()

        self.assertEqual(len(stored), 1)
        restored = json.loads(stored[0][0])
        self.assertEqual(restored["position_before_qty"], 0)
        self.assertEqual(
            restored["niuone_entry_context"]["entry_daily_v_recovery_ratio"],
            1.5,
        )

    def test_practice_database_persists_candidate_evidence_idempotently(self):
        with tempfile.TemporaryDirectory(prefix="niuone-decision-db-") as directory:
            db_path = Path(directory) / "practice.db"
            state_path = Path(directory) / "state.json"
            payload = {
                "time": "2026-08-04 10:00:06",
                "b1_generated_at": "2026-08-04 10:00:05",
                "schedule_slot": "2026-08-04 10:00",
                "schedule_run_kind": "scheduled",
                "trade_allowed": True,
                "candidate_evidence_schema_version": 2,
                "candidate_evidence": [{
                    "code": "600000",
                    "best_strategy": "niu_emerging",
                    "strategy_id": "niu_emerging",
                    "best_score": 9.0,
                    "niuone_lifecycle_stage": "markup",
                    "eligible_for_decision": True,
                    "eligibility_blockers": [],
                    "observed_rank": 1,
                }],
                "decision": {
                    "model": "test",
                    "provider": "local",
                    "summary": "hold",
                    "actions": [],
                },
                "executed": [],
            }
            code = (
                "import json; import niuniu_db; "
                f"row=json.loads({json.dumps(payload)!r}); "
                "assert niuniu_db.record_decision(row); "
                "assert niuniu_db.record_decision(row)"
            )
            env = os.environ.copy()
            env.update({
                "DASHBOARD_HOME": directory,
                "DASHBOARD_NIUNIU_DB": str(db_path),
                "DASHBOARD_PORTFOLIO_STATE": str(state_path),
                "PYTHONPATH": os.pathsep.join((
                    str(ROOT / "app" / "compat"),
                    str(ROOT / "app"),
                    str(ROOT),
                )),
            })
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            decision_rows, diagnostics = (
                load_niuone_forward_decisions_from_db(db_path)
            )

        self.assertEqual(diagnostics["database_decision_row_count"], 1)
        self.assertEqual(diagnostics["rich_payload_decision_row_count"], 1)
        self.assertEqual(decision_rows[0]["candidate_evidence"], payload["candidate_evidence"])
        self.assertTrue(decision_rows[0]["_forward_payload_available"])

    def test_practice_database_upgrade_preserves_legacy_trade_rows(self):
        with tempfile.TemporaryDirectory(prefix="niuone-practice-upgrade-") as directory:
            db_path = Path(directory) / "practice.db"
            connection = sqlite3.connect(db_path)
            connection.executescript("""
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time TEXT NOT NULL,
                    action TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT DEFAULT '',
                    shares INTEGER NOT NULL,
                    price REAL NOT NULL,
                    amount REAL NOT NULL,
                    commission REAL DEFAULT 0,
                    transfer_fee REAL DEFAULT 0,
                    stamp_duty REAL DEFAULT 0,
                    pnl REAL,
                    reason TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
                INSERT INTO trades (
                    time, action, code, shares, price, amount, reason, created_at
                ) VALUES (
                    '2026-08-01 10:00:00', 'BUY', 'legacy', 100, 10, 1000,
                    'legacy row', '2026-08-01 10:00:00'
                );
                CREATE TABLE decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time TEXT NOT NULL,
                    model TEXT DEFAULT '',
                    provider TEXT DEFAULT '',
                    trade_allowed INTEGER DEFAULT 1,
                    trade_reason TEXT DEFAULT '',
                    summary TEXT DEFAULT '',
                    actions_json TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
                INSERT INTO decisions (
                    time, summary, created_at
                ) VALUES (
                    '2026-08-01 10:00:01', 'legacy decision',
                    '2026-08-01 10:00:01'
                );
            """)
            connection.commit()
            connection.close()
            env = os.environ.copy()
            env.update({
                "DASHBOARD_HOME": directory,
                "DASHBOARD_NIUNIU_DB": str(db_path),
                "DASHBOARD_PORTFOLIO_STATE": str(Path(directory) / "state.json"),
                "PYTHONPATH": os.pathsep.join((
                    str(ROOT / "app" / "compat"),
                    str(ROOT / "app"),
                    str(ROOT),
                )),
            })
            result = subprocess.run(
                [sys.executable, "-c", "import niuniu_db"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            connection = sqlite3.connect(db_path)
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(trades)")
            }
            legacy = connection.execute(
                "SELECT code, payload_json FROM trades WHERE code = 'legacy'"
            ).fetchone()
            decision_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(decisions)")
            }
            legacy_decision = connection.execute(
                "SELECT summary, payload_json, event_key FROM decisions"
            ).fetchone()
            connection.close()

        self.assertIn("payload_json", columns)
        self.assertEqual(legacy, ("legacy", ""))
        self.assertTrue({
            "b1_generated_at",
            "schedule_slot",
            "schedule_run_kind",
            "event_key",
            "payload_json",
        }.issubset(decision_columns))
        self.assertEqual(legacy_decision, ("legacy decision", "", None))


if __name__ == "__main__":
    unittest.main()
