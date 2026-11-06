#!/usr/bin/env python3
import copy
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from unittest.mock import patch
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
COMPAT = SRC / "compat"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(COMPAT))

_tmp_home = tempfile.TemporaryDirectory()
os.environ["DASHBOARD_HOME"] = _tmp_home.name

import niuniu_practice_trader as trader  # noqa: E402
import niuniu_db as practice_db  # noqa: E402
from dashboard.practice_payload import compact_trade_markers  # noqa: E402
from trading.accounting import trade_counts_for_account  # noqa: E402
from trading.niuone_forward import (  # noqa: E402
    evaluate_niuone_forward,
    load_niuone_forward_trades_from_db,
    merge_forward_trade_rows,
)


class TradeAccountingTests(unittest.TestCase):
    def test_performance_reads_durable_opening_fill_outside_recent_json(self):
        db_path = Path(self.temp_dir.name) / "performance.db"
        buy = {"time": "2026-09-08 10:00:00", "action": "BUY", "code": "600000",
               "shares": 100, "price": 10, "amount": 1000, "fee": 2,
               "position_before_qty": 0, "position_after_qty": 100,
               "buy_strategy": "niu_reversal_probe"}
        sell = {**buy, "time": "2026-09-09 10:00:00", "action": "SELL", "price": 11,
                "amount": 1100, "pnl": 96, "position_before_qty": 100, "position_after_qty": 0}
        state = self._base_state(trade_log=[sell])
        with patch.object(practice_db, "DB_PATH", db_path):
            practice_db.init_db()
            self.assertTrue(practice_db.record_trade(buy))
            self.assertTrue(practice_db.record_trade(sell))
            performance = trader.build_strategy_performance(state)
        self.assertTrue(performance["history_complete"])
        self.assertEqual(performance["source"]["database_trade_row_count"], 2)
        self.assertEqual(performance["summary"]["closed_trades"], 1)
        self.assertEqual(performance["summary"]["win_rate"], 100)
        self.assertEqual(performance["summary"]["total_pnl"], 96)
        self.assertEqual(state["trade_log"], [sell])
        with patch.object(practice_db, "DB_PATH", db_path.with_name("missing.db")):
            fallback = trader.build_strategy_performance(self._base_state(trade_log=[buy, sell]))
        self.assertFalse(fallback["history_complete"])
        self.assertEqual(fallback["source"]["error_type"], "FileNotFoundError")
        self.assertIsNone(fallback["summary"]["win_rate"])
        self.assertIsNone(fallback["buy_strategy"]["niu_reversal_probe"]["win_rate"])

    def setUp(self):
        self.original_state_file = trader.STATE_FILE
        self.original_archive = trader._archive_account_history_before_compaction
        self.temp_dir = tempfile.TemporaryDirectory()
        trader.STATE_FILE = Path(self.temp_dir.name) / "portfolio.json"
        trader._archive_account_history_before_compaction = lambda _state: False

    def tearDown(self):
        trader.STATE_FILE = self.original_state_file
        trader._archive_account_history_before_compaction = self.original_archive
        self.temp_dir.cleanup()

    @staticmethod
    def _base_state(**overrides):
        state = {
            "created_at": "2026-08-17 09:00:00",
            "updated_at": "2026-08-17 09:00:00",
            "initial_cash": 100_000.0,
            "cash": 100_000.0,
            "positions": {},
            "trade_log": [],
            "decision_log": [],
            "pending_decisions": [],
            "equity_history": [],
            "daily_equity_history": [],
        }
        state.update(overrides)
        return state

    def test_save_state_rejects_divergent_oversell_without_crediting_cash(self):
        buy = {
            "time": "2026-08-14 10:00:00",
            "action": "BUY",
            "code": "600000",
            "name": "测试股",
            "shares": 1000,
            "price": 10.0,
            "amount": 10_000.0,
            "fee": 0.0,
            "total_cost": 10_000.0,
            "reason": "测试建仓",
        }
        first_sell = {
            "time": "2026-08-17 09:37:01",
            "action": "SELL",
            "code": "600000",
            "name": "测试股",
            "shares": 1000,
            "price": 10.9,
            "amount": 10_900.0,
            "fee": 0.0,
            "net_proceeds": 10_900.0,
            "pnl": 900.0,
            "reason": "自动离场",
        }
        duplicate_sell = {
            **first_sell,
            "time": "2026-08-17 09:38:01",
            "price": 10.8,
            "amount": 10_800.0,
            "net_proceeds": 10_800.0,
            "pnl": 800.0,
        }
        canonical_cash = 100_900.0
        current = self._base_state(
            cash=canonical_cash,
            positions={},
            trade_log=[buy, first_sell],
        )
        trader.STATE_FILE.write_text(
            json.dumps(current, ensure_ascii=False),
            encoding="utf-8",
        )
        stale_branch = self._base_state(
            cash=100_800.0,
            positions={},
            trade_log=[buy, duplicate_sell],
        )

        trader.save_state(stale_branch)
        saved = trader.load_state()

        self.assertEqual(saved["cash"], canonical_cash)
        self.assertEqual(saved["positions"], {})
        self.assertEqual(len(saved["trade_log"]), 3)
        rejected = next(
            trade
            for trade in saved["trade_log"]
            if trade.get("time") == duplicate_sell["time"]
        )
        self.assertEqual(rejected["accounting_status"], "rejected")
        self.assertEqual(
            rejected["accounting_rejection_reason"],
            "concurrent_sell_exceeds_available_position",
        )
        self.assertFalse(trade_counts_for_account(rejected))
        self.assertEqual(trader._trade_cash_delta(rejected), 0.0)

    def test_save_state_does_not_roll_back_cash_with_identical_trade_ledger(self):
        sell = {
            "time": "2026-08-17 09:37:01",
            "action": "SELL",
            "code": "600000",
            "shares": 1000,
            "price": 10.9,
            "amount": 10_900.0,
            "fee": 0.0,
            "net_proceeds": 10_900.0,
            "reason": "自动离场",
        }
        current = self._base_state(
            cash=100_900.0,
            positions={},
            trade_log=[sell],
        )
        trader.STATE_FILE.write_text(
            json.dumps(current, ensure_ascii=False),
            encoding="utf-8",
        )
        stale = self._base_state(
            cash=90_000.0,
            positions={},
            trade_log=[copy.deepcopy(sell)],
        )

        trader.save_state(stale)

        saved = trader.load_state()
        self.assertEqual(saved["cash"], 100_900.0)

    def test_current_day_equity_curve_replays_cash_at_trade_minute_boundaries(self):
        prior_point = {
            "time": "2026-08-16 15:00:00",
            "equity": 100_000.0,
            "cash": 50_000.0,
            "market_value": 50_000.0,
            "pnl_pct": 0.0,
        }
        first_sell = {
            "time": "2026-08-17 09:31:15",
            "action": "SELL",
            "code": "600000",
            "shares": 1000,
            "price": 10.0,
            "amount": 10_000.0,
            "fee": 0.0,
            "net_proceeds": 10_000.0,
            "reason": "第一笔自动离场",
        }
        second_sell = {
            "time": "2026-08-17 09:33:15",
            "action": "SELL",
            "code": "000001",
            "shares": 1000,
            "price": 10.0,
            "amount": 10_000.0,
            "fee": 0.0,
            "net_proceeds": 10_000.0,
            "reason": "第二笔自动离场",
        }
        today_points = [
            {
                "time": "2026-08-17 09:30:00",
                "equity": 100_000.0,
                "cash": 50_000.0,
                "market_value": 50_000.0,
                "pnl_pct": 0.0,
            },
            {
                "time": "2026-08-17 09:31:00",
                "equity": 100_000.0,
                "cash": 50_000.0,
                "market_value": 50_000.0,
                "pnl_pct": 0.0,
            },
            {
                "time": "2026-08-17 09:32:00",
                "equity": 80_000.0,
                "cash": 40_000.0,
                "market_value": 40_000.0,
                "pnl_pct": -20.0,
            },
            {
                "time": "2026-08-17 09:33:00",
                "equity": 60_000.0,
                "cash": 30_000.0,
                "market_value": 30_000.0,
                "pnl_pct": -40.0,
            },
            {
                "time": "2026-08-17 09:34:00",
                "equity": 100_000.0,
                "cash": 70_000.0,
                "market_value": 30_000.0,
                "pnl_pct": 0.0,
            },
        ]
        state = self._base_state(
            cash=70_000.0,
            trade_log=[first_sell, second_sell],
            equity_history=[prior_point, *copy.deepcopy(today_points)],
            daily_equity_history=[prior_point, copy.deepcopy(today_points[-1])],
        )
        original_trades = copy.deepcopy(state["trade_log"])

        changed = trader.reconcile_current_day_equity_history_from_ledger(
            state,
            today="2026-08-17",
        )

        self.assertTrue(changed)
        repaired = {
            point["time"]: point
            for point in state["equity_history"]
            if point["time"].startswith("2026-08-17")
        }
        self.assertEqual(repaired["2026-08-17 09:31:00"]["cash"], 50_000.0)
        self.assertEqual(repaired["2026-08-17 09:32:00"]["cash"], 60_000.0)
        self.assertEqual(repaired["2026-08-17 09:32:00"]["equity"], 100_000.0)
        self.assertEqual(repaired["2026-08-17 09:33:00"]["cash"], 70_000.0)
        self.assertEqual(repaired["2026-08-17 09:33:00"]["equity"], 100_000.0)
        self.assertTrue(
            repaired["2026-08-17 09:32:00"][
                "cash_reconciled_from_trade_ledger"
            ]
        )
        self.assertEqual(state["daily_equity_history"][-1], repaired["2026-08-17 09:34:00"])
        self.assertEqual(state["trade_log"], original_trades)
        self.assertFalse(trader.reconcile_current_day_equity_history_from_ledger(
            state,
            today="2026-08-17",
        ))

    def test_current_day_equity_curve_repair_fails_closed_on_invalid_snapshot(self):
        prior_point = {
            "time": "2026-08-16 15:00:00",
            "equity": 100_000.0,
            "cash": 50_000.0,
            "market_value": 50_000.0,
            "pnl_pct": 0.0,
        }
        invalid_point = {
            "time": "2026-08-17 09:33:00",
            "equity": 80_001.0,
            "cash": 40_000.0,
            "market_value": 40_000.0,
            "pnl_pct": -20.0,
        }
        state = self._base_state(
            cash=60_000.0,
            trade_log=[{
                "time": "2026-08-17 09:32:15",
                "action": "SELL",
                "code": "600000",
                "shares": 1000,
                "price": 10.0,
                "amount": 10_000.0,
                "fee": 0.0,
                "net_proceeds": 10_000.0,
                "reason": "自动离场",
            }],
            equity_history=[
                prior_point,
                {
                    "time": "2026-08-17 09:30:00",
                    "equity": 100_000.0,
                    "cash": 50_000.0,
                    "market_value": 50_000.0,
                    "pnl_pct": 0.0,
                },
                invalid_point,
            ],
            daily_equity_history=[prior_point, invalid_point],
        )
        original = copy.deepcopy(state)

        self.assertFalse(trader.reconcile_current_day_equity_history_from_ledger(
            state,
            today="2026-08-17",
        ))
        self.assertEqual(state, original)

    def test_save_state_commits_json_before_archiving_history(self):
        trade = {
            "time": "2026-08-17 10:00:00",
            "action": "BUY",
            "code": "600000",
            "shares": 100,
            "price": 10.0,
            "amount": 1000.0,
            "reason": "提交顺序测试",
        }
        observed_states = []

        def archive_after_commit(_state):
            observed_states.append(
                json.loads(trader.STATE_FILE.read_text(encoding="utf-8"))
            )
            return True

        trader._archive_account_history_before_compaction = archive_after_commit
        trader.save_state(self._base_state(
            cash=98_999.0,
            trade_log=[trade],
        ))

        self.assertEqual(len(observed_states), 1)
        self.assertEqual(observed_states[0]["cash"], 98_999.0)
        self.assertEqual(observed_states[0]["trade_log"], [trade])

    def test_save_state_failure_before_commit_does_not_archive_history(self):
        archive_calls = []
        original_writer = trader._write_state_file_atomically

        def fail_before_commit(_payload):
            raise PermissionError("state file is not writable")

        try:
            trader._write_state_file_atomically = fail_before_commit
            trader._archive_account_history_before_compaction = (
                lambda _state: archive_calls.append(True) or True
            )
            with self.assertRaises(PermissionError):
                trader.save_state(self._base_state())
        finally:
            trader._write_state_file_atomically = original_writer

        self.assertEqual(archive_calls, [])

    def test_compaction_failure_keeps_committed_full_history(self):
        original_writer = trader._write_state_file_atomically
        write_count = 0

        def fail_compaction(payload):
            nonlocal write_count
            write_count += 1
            if write_count == 2:
                raise PermissionError("compaction replace failed")
            original_writer(payload)

        trader._archive_account_history_before_compaction = lambda _state: True
        try:
            trader._write_state_file_atomically = fail_compaction
            trader.save_state(self._base_state(
                trade_log=[
                    {
                        "time": f"2026-08-17 10:{index // 60:02d}:{index % 60:02d}",
                        "action": "BUY",
                        "code": f"{index:06d}",
                        "shares": 100,
                        "price": 10.0,
                        "amount": 1000.0,
                        "reason": "压缩降级测试",
                    }
                    for index in range(trader.TRADE_LOG_LIMIT + 1)
                ],
            ))
        finally:
            trader._write_state_file_atomically = original_writer

        saved = json.loads(trader.STATE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(write_count, 2)
        self.assertEqual(len(saved["trade_log"]), trader.TRADE_LOG_LIMIT + 1)

    def test_delayed_position_projection_reads_latest_canonical_state(self):
        first_position = {
            "600000": {
                "code": "600000",
                "qty": 100,
                "avg_cost": 10.0,
            }
        }
        latest_positions = {
            **first_position,
            "600001": {
                "code": "600001",
                "qty": 200,
                "avg_cost": 8.0,
            },
        }
        stale_state = self._base_state(positions=first_position)
        trader.save_state(self._base_state(positions=latest_positions))

        captured = []
        original_db_module = sys.modules.get("niuniu_db")
        sys.modules["niuniu_db"] = types.SimpleNamespace(
            snapshot_positions=lambda positions: captured.append(
                copy.deepcopy(positions)
            )
        )
        try:
            trader._sync_positions_to_db(stale_state)
        finally:
            if original_db_module is None:
                sys.modules.pop("niuniu_db", None)
            else:
                sys.modules["niuniu_db"] = original_db_module

        self.assertEqual(captured, [latest_positions])

    def test_rejected_audit_marker_survives_same_trade_merge(self):
        trade = {
            "time": "2026-08-17 09:38:01",
            "action": "SELL",
            "code": "600000",
            "shares": 1000,
            "price": 10.8,
            "reason": "自动离场",
        }
        trader.STATE_FILE.write_text(
            json.dumps(
                self._base_state(trade_log=[trade]),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        corrected = copy.deepcopy(trade)
        corrected.update({
            "accounting_status": "rejected",
            "accounting_rejected": True,
            "accounting_rejection_reason": "manual_audit_correction",
        })

        trader.save_state(self._base_state(trade_log=[corrected]))
        saved = trader.load_state()

        self.assertEqual(len(saved["trade_log"]), 1)
        self.assertEqual(saved["trade_log"][0]["accounting_status"], "rejected")
        self.assertFalse(trade_counts_for_account(saved["trade_log"][0]))

    def test_predecision_auto_exit_serializes_stale_snapshots(self):
        buy = {
            "time": "2026-06-23 10:00:00",
            "action": "BUY",
            "code": "600000",
            "name": "测试股",
            "shares": 1000,
            "price": 10.0,
            "amount": 10_000.0,
            "fee": 0.0,
            "total_cost": 10_000.0,
            "reason": "测试建仓",
            "buy_strategy": "b2_confirm",
        }
        initial = self._base_state(
            cash=90_000.0,
            positions={
                "600000": {
                    "code": "600000",
                    "name": "测试股",
                    "qty": 1000,
                    "avg_cost": 10.0,
                    "last_price": 9.5,
                    "buy_strategy": "b2_confirm",
                    "buy_date_lots": {"2026-06-23": 1000},
                }
            },
            trade_log=[buy],
        )
        trader.save_state(copy.deepcopy(initial))
        stale_first = copy.deepcopy(initial)
        stale_second = copy.deepcopy(initial)
        originals = {
            "refresh_realtime_prices": trader.refresh_realtime_prices,
            "refresh_position_intraday": trader.refresh_position_intraday,
            "_refresh_position_bbi": trader._refresh_position_bbi,
            "_refresh_frozen_prompt_position_exits": trader._refresh_frozen_prompt_position_exits,
            "update_zettaranc_volume_context": trader.update_zettaranc_volume_context,
            "evaluate_sell_signal": trader.evaluate_sell_signal,
            "_sync_trades_to_db": trader._sync_trades_to_db,
            "_sync_positions_to_db": trader._sync_positions_to_db,
            "_sync_decision_to_db": trader._sync_decision_to_db,
            "record_equity": trader.record_equity,
            "now_ts": trader.now_ts,
        }
        try:
            trader.refresh_realtime_prices = lambda _state: {}
            trader.refresh_position_intraday = lambda _state: {}
            trader._refresh_position_bbi = lambda _state, _dt=None: None
            trader._refresh_frozen_prompt_position_exits = lambda _state, _dt=None: None
            trader.update_zettaranc_volume_context = lambda _state, _dt=None: None
            trader.evaluate_sell_signal = lambda *_args, **_kwargs: {
                "signal": "test_exit",
                "reason": "测试自动离场",
                "sell_ratio": 1.0,
            }
            trader._sync_trades_to_db = lambda _trades: True
            trader._sync_positions_to_db = lambda _state: True
            trader._sync_decision_to_db = lambda _decision: True
            trader.record_equity = lambda _state: False
            trader.now_ts = lambda: "2026-06-24 10:00:01"

            first = trader.run_position_exit_checks_before_decision(
                stale_first,
                datetime(2026, 6, 24, 10, 0),
            )
            second = trader.run_position_exit_checks_before_decision(
                stale_second,
                datetime(2026, 6, 24, 10, 0, 2),
            )
        finally:
            for name, value in originals.items():
                setattr(trader, name, value)

        saved = trader.load_state()
        active_sells = [
            trade
            for trade in saved["trade_log"]
            if trade.get("action") == "SELL" and trade_counts_for_account(trade)
        ]
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(len(active_sells), 1)
        self.assertEqual(saved["positions"], {})
        self.assertEqual(
            saved["cash"],
            round(90_000.0 + float(first[0]["net_proceeds"]), 2),
        )

    def test_auto_exit_refresh_does_not_regress_canonical_exit_state(self):
        baseline = self._base_state(
            positions={
                "600000": {
                    "qty": 1000,
                    "avg_cost": 10.0,
                    "buy_date_lots": {"2026-08-14": 1000},
                    "last_price": 10.0,
                    "highest_price": 10.0,
                    "shaofu_soft_exit_count": 0,
                }
            },
        )
        refreshed = copy.deepcopy(baseline)
        refreshed["positions"]["600000"].update({
            "last_price": 11.0,
            "quote_time": "2026-08-17 10:01:00",
        })
        canonical = copy.deepcopy(baseline)
        canonical["positions"]["600000"].update({
            "last_price": 12.0,
            "highest_price": 12.0,
            "shaofu_soft_exit_count": 1,
        })

        eligible = trader._merge_refreshed_auto_exit_context(
            canonical,
            refreshed,
            trader._auto_exit_refresh_baseline(baseline),
        )

        position = canonical["positions"]["600000"]
        self.assertEqual(eligible, {"600000"})
        self.assertEqual(position["last_price"], 11.0)
        self.assertEqual(position["quote_time"], "2026-08-17 10:01:00")
        self.assertEqual(position["highest_price"], 12.0)
        self.assertEqual(position["shaofu_soft_exit_count"], 1)

    def test_auto_exit_ledger_key_blocks_repeat_after_guard_fields_are_lost(self):
        buy = {
            "time": "2026-06-23 10:00:00",
            "action": "BUY",
            "code": "600000",
            "name": "测试股",
            "shares": 1000,
            "price": 10.0,
            "amount": 10_000.0,
            "total_cost": 10_000.0,
            "position_before_qty": 0,
            "position_after_qty": 1000,
            "position_opened": True,
            "reason": "测试建仓",
        }
        state = self._base_state(
            cash=90_000.0,
            positions={
                "600000": {
                    "code": "600000",
                    "name": "测试股",
                    "qty": 1000,
                    "avg_cost": 10.0,
                    "last_price": 10.9,
                    "buy_date_lots": {"2026-06-23": 1000},
                }
            },
            trade_log=[buy],
        )
        original_now = trader.now_ts
        try:
            trader.now_ts = lambda: "2026-06-24 10:00:01"
            first = trader.check_auto_exits(
                state,
                datetime(2026, 6, 24, 10, 0),
            )
        finally:
            trader.now_ts = original_now

        self.assertEqual(len(first), 1)
        self.assertTrue(first[0]["idempotency_key"].startswith("auto-exit-v1:"))
        self.assertTrue(first[0]["position_lifecycle_id"].startswith(
            "position-lifecycle-v1:"
        ))

        # Simulate the observed failure: quantity and durable fills survive,
        # while an old mutable position snapshot loses every exit marker.
        stale = copy.deepcopy(state)
        position = stale["positions"]["600000"]
        for field in (
            trader.POSITION_LIFECYCLE_ID_FIELD,
            trader.AUTO_EXIT_COMPLETED_KEYS_FIELD,
            "partial_tp_done",
            "last_exit_rule",
            "last_exit_marked_at",
            "last_exit_strategy_mark",
        ):
            position.pop(field, None)

        second = trader.check_auto_exits(
            stale,
            datetime(2026, 6, 24, 10, 1),
        )

        self.assertEqual(second, [])
        recovered = stale["positions"]["600000"]
        self.assertTrue(recovered["partial_tp_done"])
        self.assertEqual(
            recovered[trader.AUTO_EXIT_COMPLETED_KEYS_FIELD],
            [first[0]["idempotency_key"]],
        )
        self.assertEqual(recovered["qty"], 500)

    def test_legacy_partial_fill_restores_guard_without_new_idempotency_key(self):
        state = self._base_state(
            cash=95_450.0,
            positions={
                "600000": {
                    "code": "600000",
                    "qty": 500,
                    "avg_cost": 10.0,
                    "last_price": 10.9,
                    "buy_date_lots": {"2026-06-23": 500},
                }
            },
            trade_log=[
                {
                    "time": "2026-06-23 10:00:00",
                    "action": "BUY",
                    "code": "600000",
                    "shares": 1000,
                    "price": 10.0,
                    "amount": 10_000.0,
                    "reason": "历史建仓",
                },
                {
                    "time": "2026-06-24 09:40:00",
                    "action": "SELL",
                    "code": "600000",
                    "shares": 500,
                    "price": 10.9,
                    "amount": 5_450.0,
                    "reason": "历史首段止盈",
                    "exit_signal": "partial_take_profit",
                },
            ],
        )

        executed = trader.check_auto_exits(
            state,
            datetime(2026, 6, 24, 10, 0),
        )

        self.assertEqual(executed, [])
        self.assertEqual(state["positions"]["600000"]["qty"], 500)
        self.assertTrue(state["positions"]["600000"]["partial_tp_done"])

    def test_auto_exit_recovers_guard_from_sqlite_after_json_compaction(self):
        lifecycle_id = "position-lifecycle-v1:durable"
        position = {
            "code": "600000",
            "qty": 500,
            "avg_cost": 10.0,
            "last_price": 10.9,
            "buy_date_lots": {"2026-06-23": 500},
        }
        idempotency_key = trader._auto_exit_idempotency_key(
            "600000",
            position,
            lifecycle_id,
            {"signal": "partial_take_profit"},
        )
        state = self._base_state(
            cash=95_450.0,
            positions={"600000": position},
            trade_log=[],
        )
        originals = {
            "_durable_trade_idempotency_keys": (
                trader._durable_trade_idempotency_keys
            ),
            "_durable_position_lifecycle_ids": (
                trader._durable_position_lifecycle_ids
            ),
        }
        try:
            trader._durable_trade_idempotency_keys = (
                lambda _state: {idempotency_key}
            )
            trader._durable_position_lifecycle_ids = (
                lambda _codes: {"600000": lifecycle_id}
            )
            executed = trader.check_auto_exits(
                state,
                datetime(2026, 6, 24, 10, 0),
            )
        finally:
            for name, value in originals.items():
                setattr(trader, name, value)

        self.assertEqual(executed, [])
        recovered = state["positions"]["600000"]
        self.assertEqual(recovered["position_lifecycle_id"], lifecycle_id)
        self.assertTrue(recovered["partial_tp_done"])
        self.assertEqual(
            recovered["auto_exit_completed_idempotency_keys"],
            [idempotency_key],
        )

    def test_save_state_preserves_monotonic_auto_exit_guards(self):
        lifecycle_id = "position-lifecycle-v1:test"
        idempotency_key = "auto-exit-v1:test"
        trade = {
            "time": "2026-08-17 10:00:01",
            "action": "SELL",
            "code": "600000",
            "shares": 500,
            "price": 10.9,
            "amount": 5_450.0,
            "reason": "首次减仓",
            "position_lifecycle_id": lifecycle_id,
            "idempotency_key": idempotency_key,
        }
        current = self._base_state(
            cash=95_450.0,
            positions={
                "600000": {
                    "code": "600000",
                    "qty": 500,
                    "avg_cost": 10.0,
                    "last_price": 10.9,
                    "buy_date_lots": {"2026-08-14": 500},
                    "position_lifecycle_id": lifecycle_id,
                    "auto_exit_completed_idempotency_keys": [idempotency_key],
                    "partial_tp_done": True,
                    "last_exit_rule": "take_profit",
                }
            },
            trade_log=[trade],
        )
        trader.STATE_FILE.write_text(
            json.dumps(current, ensure_ascii=False),
            encoding="utf-8",
        )
        stale = copy.deepcopy(current)
        stale_position = stale["positions"]["600000"]
        for field in (
            "position_lifecycle_id",
            "auto_exit_completed_idempotency_keys",
            "partial_tp_done",
            "last_exit_rule",
        ):
            stale_position.pop(field, None)

        trader.save_state(stale)
        saved = trader.load_state()["positions"]["600000"]

        self.assertEqual(saved["position_lifecycle_id"], lifecycle_id)
        self.assertEqual(
            saved["auto_exit_completed_idempotency_keys"],
            [idempotency_key],
        )
        self.assertTrue(saved["partial_tp_done"])
        self.assertEqual(saved["last_exit_rule"], "take_profit")

    def test_practice_db_enforces_trade_idempotency_key(self):
        module = practice_db
        original_db_path = module.DB_PATH
        module.DB_PATH = Path(self.temp_dir.name) / "idempotency.db"
        first = {
            "time": "2026-08-17 10:00:01",
            "action": "SELL",
            "code": "600000",
            "shares": 500,
            "price": 10.9,
            "amount": 5_450.0,
            "reason": "首次减仓",
            "position_lifecycle_id": "position-lifecycle-v1:test",
            "idempotency_key": "auto-exit-v1:test",
        }
        duplicate = {
            **first,
            "time": "2026-08-17 10:01:01",
            "shares": 200,
            "amount": 2_180.0,
            "reason": "旧快照再次减仓",
        }
        try:
            module.init_db()
            self.assertTrue(module.record_trade(first))
            self.assertTrue(module.record_trade(duplicate))
            with closing(sqlite3.connect(module.DB_PATH)) as connection:
                row_count = connection.execute(
                    "SELECT COUNT(*) FROM trades"
                ).fetchone()[0]
            keys = module.query_trade_idempotency_keys()
            archived_only = {
                "time": "2026-08-17 10:02:01",
                "action": "BUY",
                "code": "000001",
                "shares": 100,
                "price": 12.0,
                "amount": 1_200.0,
                "reason": "仅归档建仓",
                "position_lifecycle_id": "position-lifecycle-v1:archive",
            }
            self.assertTrue(module.archive_account_history({
                "trade_log": [archived_only],
            }))
            lifecycle_ids = module.query_latest_position_lifecycle_ids([
                "600000",
                "000001",
            ])
            self.assertTrue(module.archive_account_history({
                "trade_log": [first],
            }))
            corrected = {
                **first,
                "accounting_status": "rejected",
                "accounting_rejected": True,
                "accounting_rejection_reason": "manual_audit_correction",
            }
            self.assertTrue(module.archive_account_history({
                "trade_log": [corrected],
            }))
            corrected_keys = module.query_trade_idempotency_keys()
        finally:
            module.DB_PATH = original_db_path

        self.assertEqual(row_count, 1)
        self.assertEqual(keys, {"auto-exit-v1:test"})
        self.assertEqual(
            lifecycle_ids,
            {
                "600000": "position-lifecycle-v1:test",
                "000001": "position-lifecycle-v1:archive",
            },
        )
        self.assertEqual(corrected_keys, set())

    def test_rejected_oversell_repairs_equity_and_sqlite_position_snapshot(self):
        buy = {
            "time": "2026-08-14 10:00:00",
            "action": "BUY",
            "code": "600000",
            "shares": 1000,
            "price": 10.0,
            "amount": 10_000.0,
            "fee": 0.0,
            "total_cost": 10_000.0,
            "reason": "测试建仓",
        }
        first_sell = {
            "time": "2026-08-17 09:37:01",
            "action": "SELL",
            "code": "600000",
            "shares": 500,
            "price": 10.9,
            "amount": 5_450.0,
            "fee": 0.0,
            "net_proceeds": 5_450.0,
            "reason": "第一笔离场",
        }
        stale_oversell = {
            "time": "2026-08-17 09:38:01",
            "action": "SELL",
            "code": "600000",
            "shares": 1000,
            "price": 10.8,
            "amount": 10_800.0,
            "fee": 0.0,
            "net_proceeds": 10_800.0,
            "reason": "旧快照离场",
        }
        canonical_position = {
            "code": "600000",
            "qty": 500,
            "avg_cost": 10.0,
            "last_price": 10.9,
            "buy_date_lots": {"2026-08-14": 500},
        }
        current = self._base_state(
            cash=95_450.0,
            positions={"600000": canonical_position},
            trade_log=[buy, first_sell],
            equity_history=[{
                "time": "2026-08-17 09:37:00",
                "equity": 100_900.0,
                "cash": 95_450.0,
                "market_value": 5_450.0,
                "pnl_pct": 0.9,
            }],
        )
        trader.STATE_FILE.write_text(
            json.dumps(current, ensure_ascii=False),
            encoding="utf-8",
        )
        pending_time = "2026-08-17 09:38:00"
        stale_branch = self._base_state(
            cash=100_800.0,
            positions={},
            trade_log=[buy, stale_oversell],
            equity_history=[{
                "time": pending_time,
                "equity": 100_800.0,
                "cash": 100_800.0,
                "market_value": 0.0,
                "pnl_pct": 0.8,
            }],
            daily_equity_history=[{
                "time": pending_time,
                "equity": 100_800.0,
                "cash": 100_800.0,
                "market_value": 0.0,
                "pnl_pct": 0.8,
            }],
        )
        stale_branch[trader._PENDING_EQUITY_DB_SYNC_TIME] = pending_time
        position_snapshots = []
        equity_snapshots = []
        original_sync_positions = trader._sync_positions_to_db
        original_db = sys.modules.get("niuniu_db")
        trader._sync_positions_to_db = lambda state: position_snapshots.append(
            copy.deepcopy(state.get("positions") or {})
        )
        sys.modules["niuniu_db"] = types.SimpleNamespace(
            record_daily_equity=lambda point: equity_snapshots.append(
                copy.deepcopy(point)
            )
        )
        try:
            trader.save_state(stale_branch)
        finally:
            trader._sync_positions_to_db = original_sync_positions
            if original_db is None:
                sys.modules.pop("niuniu_db", None)
            else:
                sys.modules["niuniu_db"] = original_db

        saved = trader.load_state()
        repaired = next(
            point
            for point in saved["equity_history"]
            if point.get("time") == pending_time
        )
        rejected = next(
            trade
            for trade in saved["trade_log"]
            if trade.get("time") == stale_oversell["time"]
        )
        self.assertEqual(saved["cash"], 95_450.0)
        self.assertEqual(saved["positions"]["600000"]["qty"], 500)
        self.assertFalse(trade_counts_for_account(rejected))
        self.assertEqual(repaired["cash"], 95_450.0)
        self.assertEqual(repaired["market_value"], 5_450.0)
        self.assertEqual(repaired["equity"], 100_900.0)
        self.assertEqual(position_snapshots[-1]["600000"]["qty"], 500)
        self.assertEqual(equity_snapshots[-1]["equity"], 100_900.0)

    def test_rejected_sell_is_excluded_from_account_summaries(self):
        active_sell = {
            "time": "2026-08-17 10:00:00",
            "action": "SELL",
            "code": "600000",
            "name": "测试股",
            "shares": 1000,
            "price": 10.0,
            "amount": 10_000.0,
            "net_proceeds": 9_990.0,
            "fee": 10.0,
            "pnl": 990.0,
            "reason": "止盈",
            "exit_rule": "take_profit",
            "buy_strategy": "b2_confirm",
        }
        rejected_sell = {
            **active_sell,
            "time": "2026-08-17 10:01:00",
            "accounting_status": "rejected",
            "accounting_rejected": True,
            "accounting_rejection_reason": (
                "concurrent_sell_exceeds_available_position"
            ),
        }
        state = self._base_state(trade_log=[active_sell, rejected_sell])

        performance = trader.track_strategy_performance(state)
        sold_rows = trader.build_today_sold_stocks(
            state,
            today="2026-08-17",
            quote_map={},
        )
        portfolio = trader.enrich_portfolio(state)
        markers = compact_trade_markers(state["trade_log"])

        self.assertEqual(performance["summary"]["closed_trades"], 0)
        self.assertEqual(performance["summary"]["total_pnl"], 0.0)
        self.assertEqual(performance["summary"]["sell_fill_count"], 1)
        self.assertEqual(performance["summary"]["realized_pnl_all_sells"], 990.0)
        self.assertEqual(len(sold_rows), 1)
        self.assertEqual(sold_rows[0]["shares"], 1000)
        self.assertEqual(len(portfolio["trade_log"]), 1)
        self.assertEqual(len(markers), 1)
        self.assertEqual(len(state["trade_log"]), 2)

    def test_forward_merge_prefers_rejected_accounting_revision(self):
        raw = {
            "time": "2026-08-17 10:01:00",
            "action": "SELL",
            "code": "600000",
            "shares": 1000,
            "price": 10.0,
            "amount": 10_000.0,
            "reason": "自动离场",
            "_forward_payload_available": True,
        }
        corrected = {
            **raw,
            "_forward_payload_available": False,
            "accounting_status": "rejected",
            "accounting_rejected": True,
            "accounting_rejection_reason": (
                "concurrent_sell_exceeds_available_position"
            ),
        }

        merged, duplicate_count = merge_forward_trade_rows([raw], [corrected])
        report = evaluate_niuone_forward(
            merged,
            as_of="2026-08-17",
        )

        self.assertEqual(duplicate_count, 1)
        self.assertEqual(len(merged), 1)
        self.assertFalse(trade_counts_for_account(merged[0]))
        self.assertTrue(merged[0]["_forward_payload_available"])
        self.assertEqual(
            report["coverage"]["inactive_accounting_trade_count"],
            1,
        )
        self.assertEqual(report["coverage"]["orphan_sell_count"], 0)

    def test_forward_db_loader_applies_append_only_accounting_revision(self):
        db_path = Path(self.temp_dir.name) / "practice.db"
        raw = {
            "time": "2026-08-17 10:01:00",
            "action": "SELL",
            "code": "600000",
            "shares": 1000,
            "price": 10.0,
            "amount": 10_000.0,
            "reason": "自动离场",
        }
        corrected = {
            **raw,
            "accounting_status": "rejected",
            "accounting_rejected": True,
            "accounting_rejection_reason": (
                "concurrent_sell_exceeds_available_position"
            ),
        }
        with closing(sqlite3.connect(db_path)) as connection, connection:
            connection.executescript("""
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time TEXT,
                    action TEXT,
                    code TEXT,
                    name TEXT,
                    shares INTEGER,
                    price REAL,
                    amount REAL,
                    commission REAL DEFAULT 0,
                    transfer_fee REAL DEFAULT 0,
                    stamp_duty REAL DEFAULT 0,
                    reason TEXT DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE account_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    history_kind TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    logical_key TEXT NOT NULL,
                    event_time TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    archived_at TEXT NOT NULL
                );
            """)
            connection.execute(
                """
                INSERT INTO trades (
                    time, action, code, shares, price, amount, reason, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw["time"], raw["action"], raw["code"], raw["shares"],
                    raw["price"], raw["amount"], raw["reason"],
                    json.dumps(raw, ensure_ascii=False),
                ),
            )
            connection.execute(
                """
                INSERT INTO account_history (
                    history_kind, event_key, logical_key, event_time,
                    payload_json, archived_at
                ) VALUES ('trade_log', ?, ?, ?, ?, ?)
                """,
                (
                    "correction-event",
                    "trade-logical-key",
                    raw["time"],
                    json.dumps(corrected, ensure_ascii=False),
                    "2026-08-17 10:05:00",
                ),
            )
        connection.close()

        rows, diagnostics = load_niuone_forward_trades_from_db(db_path)

        self.assertEqual(len(rows), 1)
        self.assertFalse(trade_counts_for_account(rows[0]))
        self.assertEqual(diagnostics["accounting_revision_overlay_count"], 1)


if __name__ == "__main__":
    unittest.main()
