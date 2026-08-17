#!/usr/bin/env python3
import copy
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
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
from dashboard.practice_payload import compact_trade_markers  # noqa: E402
from trading.accounting import trade_counts_for_account  # noqa: E402
from trading.niuone_forward import (  # noqa: E402
    evaluate_niuone_forward,
    load_niuone_forward_trades_from_db,
    merge_forward_trade_rows,
)


class TradeAccountingTests(unittest.TestCase):
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

        self.assertEqual(performance["summary"]["closed_trades"], 1)
        self.assertEqual(performance["summary"]["total_pnl"], 990.0)
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
        with sqlite3.connect(db_path) as connection:
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
