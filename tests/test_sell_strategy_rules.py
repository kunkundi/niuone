#!/usr/bin/env python3
import os
import json
import subprocess
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
COMPAT = SRC / "compat"
ENTRYPOINTS = SRC / "entrypoints"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(COMPAT))

_tmp_home = tempfile.TemporaryDirectory()
os.environ["DASHBOARD_HOME"] = _tmp_home.name

import niuniu_practice_trader as trader  # noqa: E402


def permissive_market_context() -> dict:
    return {
        "tone_label": "中性",
        "max_open_positions": trader.MAX_OPEN_POSITIONS,
        "max_new_buys_per_decision": trader.MAX_NEW_BUYS_PER_DECISION,
        "max_total_position_pct": trader.MAX_TOTAL_POSITION_PCT,
        "min_cash_reserve_pct": trader.MIN_CASH_RESERVE_PCT,
        "buy_budget_multiplier": 1.0,
        "allow_new_buys": True,
    }


class SellStrategyRuleTests(unittest.TestCase):
    def test_trading_time_tracks_auction_and_static_period(self):
        allowed, reason = trader.is_a_share_trading_time(datetime(2026, 6, 24, 9, 15))
        self.assertTrue(allowed)
        self.assertIn("集合竞价", reason)

        allowed, reason = trader.is_a_share_trading_time(datetime(2026, 6, 24, 9, 25, 20))
        self.assertFalse(allowed)
        self.assertIn("静默期", reason)

        allowed, reason = trader.is_a_share_trading_time(datetime(2026, 6, 24, 14, 58))
        self.assertTrue(allowed)
        self.assertIn("尾盘", reason)

        self.assertFalse(trader.is_a_share_trading_time(datetime(2026, 6, 24, 9, 10))[0])
        self.assertFalse(trader.is_a_share_trading_time(datetime(2026, 6, 24, 11, 45))[0])

    def test_execution_time_blocks_opening_auction_before_continuous_session(self):
        allowed, reason = trader.is_a_share_execution_time(datetime(2026, 6, 24, 9, 15, 20))
        self.assertFalse(allowed)
        self.assertIn("不模拟即时成交", reason)

        allowed, reason = trader.is_a_share_execution_time(datetime(2026, 6, 24, 9, 25, 20))
        self.assertFalse(allowed)
        self.assertIn("静默期", reason)

        allowed, reason = trader.is_a_share_execution_time(datetime(2026, 6, 24, 9, 30))
        self.assertTrue(allowed)
        self.assertIn("连续竞价", reason)

    def test_intraday_minute_rows_are_normalized_to_session_axis(self):
        points = trader.parse_intraday_minute_rows([
            "0925 9.90 10 99",
            "0930 10.00 100 1000",
            "1130 10.50 200 2100",
            "1145 10.60 210 2200",
            "1300 10.30 220 2300",
            "1500 10.80 300 3240",
        ], prev_close=10.0)

        self.assertEqual([p["time"] for p in points], ["09:30", "11:30", "13:00", "15:00"])
        self.assertEqual([p["minute"] for p in points], [0, 120, 120, 240])
        self.assertEqual(points[-1]["pct"], 8.0)

    def test_realtime_high_low_pct_are_exposed_in_portfolio_rows(self):
        state = {
            "cash": 0.0,
            "positions": {
                "600000": {
                    "code": "600000",
                    "name": "测试股",
                    "qty": 1000,
                    "avg_cost": 10.0,
                    "last_price": 10.0,
                    "buy_date_lots": {"2026-06-23": 1000},
                    "buy_strategy": "b3_accelerate",
                    "entry_reason": "B3中继评分10.0达标",
                }
            },
            "trade_log": [],
            "decision_log": [],
            "equity_history": [],
        }

        original_fetch = trader.fetch_realtime_quotes
        try:
            trader.fetch_realtime_quotes = lambda codes: ({
                "600000": {
                    "code": "600000",
                    "name": "测试股",
                    "price": 10.5,
                    "prev_close": 10.0,
                    "high": 10.8,
                    "low": 10.1,
                    "change_pct": 5.0,
                    "quote_time": "2026-06-24 10:00:00",
                    "source": "test",
                }
            }, {"channel_counts": {"tencent": 1}, "errors": []})

            trader.refresh_realtime_prices(state)
            row = trader.enrich_portfolio(state)["positions"][0]
        finally:
            trader.fetch_realtime_quotes = original_fetch

        self.assertEqual(row["day_high"], 10.8)
        self.assertEqual(row["day_low"], 10.1)
        self.assertEqual(row["day_high_pct"], 8.0)
        self.assertEqual(row["day_low_pct"], 1.0)
        self.assertEqual(row["change_pct"], 5.0)
        self.assertFalse(row["bought_today"])
        self.assertEqual(row["today_buy_qty"], 0)
        self.assertEqual(row["buy_strategy"], "b3_accelerate")
        self.assertEqual(row["entry_reason"], "B3中继评分10.0达标")

    def test_portfolio_marks_only_today_bought_positions(self):
        original_today_key = trader.today_key
        try:
            trader.today_key = lambda: "2026-06-24"
            state = {
                "cash": 0.0,
                "positions": {
                    "600000": {
                        "code": "600000",
                        "qty": 1000,
                        "avg_cost": 10.0,
                        "last_price": 10.2,
                        "buy_date_lots": {"2026-06-24": 300, "2026-06-23": 700},
                        "buy_strategy": "b3_accelerate",
                        "entry_reason": "B3中继评分10.0达标",
                    },
                    "600001": {
                        "code": "600001",
                        "qty": 1000,
                        "avg_cost": 10.0,
                        "last_price": 10.2,
                        "buy_date_lots": {"2026-06-23": 1000},
                        "buy_strategy": "trend_pullback",
                        "entry_reason": "趋势回踩评分9.0达标",
                    },
                },
            }
            rows = {row["code"]: row for row in trader.enrich_portfolio(state)["positions"]}
        finally:
            trader.today_key = original_today_key

        self.assertTrue(rows["600000"]["bought_today"])
        self.assertEqual(rows["600000"]["today_buy_qty"], 300)
        self.assertFalse(rows["600001"]["bought_today"])
        self.assertEqual(rows["600001"]["today_buy_qty"], 0)

    def test_available_to_sell_treats_legacy_positions_as_historical(self):
        original_today_key = trader.today_key
        try:
            trader.today_key = lambda: "2026-06-24"
            self.assertEqual(trader.available_to_sell({"shares": 13000}), 13000)
            self.assertEqual(
                trader.available_to_sell({"qty": 3300, "buy_date_lots": {"2026-06-24": 3300}}),
                0,
            )
            self.assertEqual(
                trader.available_to_sell({
                    "qty": 1500,
                    "buy_date_lots": {"2026-06-23": 1000, "2026-06-24": 500},
                }),
                1000,
            )
        finally:
            trader.today_key = original_today_key

    def test_today_bought_position_today_pnl_uses_cost_basis(self):
        original_today_key = trader.today_key
        try:
            trader.today_key = lambda: "2026-06-24"
            state = {
                "cash": 0.0,
                "positions": {
                    "000833": {
                        "code": "000833",
                        "name": "粤桂股份",
                        "qty": 3300,
                        "avg_cost": 24.9327,
                        "last_price": 25.0,
                        "prev_close": 24.8,
                        "buy_date_lots": {"2026-06-24": 3300},
                    }
                },
                "trade_log": [],
                "decision_log": [],
                "equity_history": [],
            }
            row = trader.enrich_portfolio(state)["positions"][0]
        finally:
            trader.today_key = original_today_key

        self.assertEqual(row["pnl"], 222.09)
        self.assertEqual(row["pnl_pct"], 0.27)
        self.assertEqual(row["today_pnl"], 222.09)
        self.assertEqual(row["today_pnl_pct"], 0.27)

    def test_historical_position_today_pnl_still_uses_prev_close(self):
        original_today_key = trader.today_key
        try:
            trader.today_key = lambda: "2026-06-24"
            state = {
                "cash": 0.0,
                "positions": {
                    "600999": {
                        "code": "600999",
                        "name": "招商证券",
                        "shares": 13000,
                        "avg_cost": 19.09,
                        "last_price": 20.28,
                        "prev_close": 20.1,
                    }
                },
                "trade_log": [],
                "decision_log": [],
                "equity_history": [],
            }
            row = trader.enrich_portfolio(state)["positions"][0]
        finally:
            trader.today_key = original_today_key

        self.assertEqual(row["pnl"], 15470.0)
        self.assertEqual(row["today_pnl"], 2340.0)
        self.assertEqual(row["today_pnl_pct"], 0.9)

    def test_execute_actions_rechecks_trading_time_before_order(self):
        original_execution_time = trader.is_a_share_execution_time
        try:
            trader.is_a_share_execution_time = lambda dt=None: (False, "非A股交易时段")
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {
                "actions": [{"action": "BUY", "code": "600000", "name": "测试股", "shares": 1000, "reason": "test"}]
            }

            executed = trader.execute_actions(state, decision, [], True, "尾盘集合竞价交易时段")
        finally:
            trader.is_a_share_execution_time = original_execution_time

        self.assertEqual(executed, [])
        self.assertEqual(state["cash"], 100000.0)
        self.assertIn("执行前复核失败", decision["execution_blocked_reason"])

    def test_execute_actions_fills_missing_trade_reason(self):
        original_execution_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "测试股", "source": "test"}
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {
                "actions": [{"action": "BUY", "code": "600000", "name": "测试股", "shares": 1000}]
            }
            candidates = [{
                "code": "600000",
                "name": "测试股",
                "score_basis": "B3中继",
                "best_score": 10.0,
                "entry_threshold": 8.5,
                "distance_pct": 1.2,
                "risk_flags": [],
            }]

            executed = trader.execute_actions(
                state,
                decision,
                candidates,
                True,
                "连续竞价交易时段",
                permissive_market_context(),
            )
        finally:
            trader.is_a_share_execution_time = original_execution_time
            trader.execution_quote = original_quote

        self.assertEqual(len(executed), 1)
        self.assertIn("模型买入", executed[0]["reason"])
        self.assertIn("B3中继", executed[0]["reason"])
        self.assertEqual(executed[0]["buy_strategy"], "b3_accelerate")
        self.assertEqual(decision["actions"][0]["reason"], executed[0]["reason"])

    def test_execute_actions_marks_buy_strategy_for_next_decision(self):
        original_execution_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "测试股", "source": "test"}
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {
                "actions": [{
                    "action": "BUY", "code": "600000", "name": "测试股",
                    "shares": 1000, "reason": "B3中继评分10.0达标，小仓试错",
                }]
            }
            candidates = [{
                "code": "600000",
                "name": "测试股",
                "best_strategy": "b3_accelerate",
                "best_score": 10.0,
                "entry_threshold": 8.5,
                "distance_pct": 1.2,
                "actionable": True,
                "hard_blockers": [],
                "risk_flags": [],
            }]

            executed = trader.execute_actions(
                state,
                decision,
                candidates,
                True,
                "连续竞价交易时段",
                permissive_market_context(),
            )
        finally:
            trader.is_a_share_execution_time = original_execution_time
            trader.execution_quote = original_quote

        self.assertEqual(len(executed), 1)
        pos = state["positions"]["600000"]
        self.assertEqual(pos["buy_strategy"], "b3_accelerate")
        self.assertEqual(pos["strategy_mark"]["strategy_id"], "b3_accelerate")
        self.assertEqual(executed[0]["strategy_mark"]["strategy_id"], "b3_accelerate")
        self.assertEqual(executed[0]["order_position_pct"], 10.0)
        self.assertEqual(executed[0]["position_after_trade_pct"], 10.0)
        self.assertEqual(executed[0]["total_position_after_trade_pct"], 10.0)
        self.assertEqual(decision["actions"][0]["strategy_mark"]["strategy_id"], "b3_accelerate")
        self.assertEqual(decision["actions"][0]["order_position_pct"], 10.0)

        compact = trader.compact_portfolio_for_decision(trader.enrich_portfolio(state))
        compact_pos = compact["positions"][0]
        self.assertEqual(compact_pos["strategy_mark"]["strategy_id"], "b3_accelerate")
        self.assertEqual(compact_pos["strategy_mark_id"], "b3_accelerate")
        self.assertEqual(compact_pos["position_pct"], 10.0)
        self.assertEqual(compact_pos["strategy_mark_history"][-1]["action"], "BUY")

    def test_execute_actions_marks_sell_rule_on_remaining_position(self):
        original_execution_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "测试股", "source": "test"}
            state = {
                "cash": 0.0,
                "positions": {
                    "600000": {
                        "code": "600000",
                        "name": "测试股",
                        "qty": 1000,
                        "avg_cost": 9.0,
                        "last_price": 10.0,
                        "buy_strategy": "b2_confirm",
                        "entry_reason": "B2确认评分9.0达标",
                        "buy_date_lots": {"2026-01-01": 1000},
                    }
                },
                "trade_log": [],
            }
            trader.apply_entry_strategy_mark(
                state["positions"]["600000"],
                "b2_confirm",
                "B2确认评分9.0达标",
            )
            decision = {
                "actions": [{
                    "action": "SELL", "code": "600000", "name": "测试股",
                    "shares": 500, "reason": "卖出评分降至2分，先减仓",
                }]
            }

            executed = trader.execute_actions(
                state,
                decision,
                [],
                True,
                "连续竞价交易时段",
                permissive_market_context(),
            )
        finally:
            trader.is_a_share_execution_time = original_execution_time
            trader.execution_quote = original_quote

        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0]["strategy_mark"]["strategy_id"], "b2_confirm")
        self.assertEqual(executed[0]["exit_rule"], "sell_score")
        self.assertEqual(executed[0]["exit_strategy_mark"]["exit_rule"], "sell_score")
        self.assertEqual(executed[0]["order_position_pct"], 50.0)
        self.assertEqual(executed[0]["position_before_trade_pct"], 100.0)
        self.assertEqual(executed[0]["position_after_trade_pct"], 50.0)
        self.assertEqual(executed[0]["total_position_after_trade_pct"], 50.0)
        pos = state["positions"]["600000"]
        self.assertEqual(pos["qty"], 500)
        self.assertEqual(pos["last_exit_rule"], "sell_score")
        self.assertEqual(pos["last_exit_strategy_mark"]["entry_strategy_id"], "b2_confirm")

        compact = trader.compact_portfolio_for_decision(trader.enrich_portfolio(state))
        compact_pos = compact["positions"][0]
        self.assertEqual(compact_pos["strategy_mark"]["strategy_id"], "b2_confirm")
        self.assertAlmostEqual(compact_pos["position_pct"], 50.02, places=2)
        self.assertEqual(compact_pos["last_exit_rule"], "sell_score")
        self.assertEqual(compact_pos["strategy_mark_history"][-1]["action"], "SELL")

    def test_execute_actions_blocks_non_actionable_buy_candidate(self):
        original_execution_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "测试股", "source": "test"}
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {
                "actions": [{"action": "BUY", "code": "600000", "name": "测试股", "shares": 1000}]
            }
            candidates = [{
                "code": "600000",
                "name": "测试股",
                "best_strategy": "shaofu_b1",
                "best_score": 9.0,
                "entry_threshold": 8.0,
                "distance_pct": 1.2,
                "actionable": False,
                "hard_blockers": ["B1核心J未≤-10"],
            }]

            executed = trader.execute_actions(
                state,
                decision,
                candidates,
                True,
                "连续竞价交易时段",
                permissive_market_context(),
            )
        finally:
            trader.is_a_share_execution_time = original_execution_time
            trader.execution_quote = original_quote

        self.assertEqual(executed, [])
        self.assertEqual(state["positions"], {})
        self.assertIn("B1核心", decision["execution_blocked_reason"])

    def test_execute_actions_blocks_buy_outside_current_stock_universe(self):
        original_execution_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        saved_universe = os.environ.get(trader.STOCK_UNIVERSE_ENV)
        try:
            os.environ[trader.STOCK_UNIVERSE_ENV] = "main_board"
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "创业测试", "source": "test"}
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {
                "actions": [{"action": "BUY", "code": "300001", "name": "创业测试", "shares": 1000}]
            }
            candidates = [{
                "code": "300001",
                "name": "创业测试",
                "best_strategy": "b3_accelerate",
                "best_score": 10.0,
                "entry_threshold": 8.5,
                "distance_pct": 1.2,
                "actionable": True,
                "hard_blockers": [],
                "risk_flags": [],
            }]

            executed = trader.execute_actions(
                state,
                decision,
                candidates,
                True,
                "连续竞价交易时段",
                permissive_market_context(),
            )
        finally:
            trader.is_a_share_execution_time = original_execution_time
            trader.execution_quote = original_quote
            if saved_universe is None:
                os.environ.pop(trader.STOCK_UNIVERSE_ENV, None)
            else:
                os.environ[trader.STOCK_UNIVERSE_ENV] = saved_universe

        self.assertEqual(executed, [])
        self.assertEqual(state["positions"], {})
        self.assertIn("不在当前选股范围", decision["execution_blocked_reason"])

    def test_stock_universe_allows_st_across_supported_boards(self):
        saved_universe = os.environ.get(trader.STOCK_UNIVERSE_ENV)
        try:
            os.environ[trader.STOCK_UNIVERSE_ENV] = "st"
            self.assertTrue(trader.candidate_in_stock_universe({"code": "300001", "name": "*ST测试"}))
            self.assertFalse(trader.candidate_in_stock_universe({"code": "300001", "name": "创业测试"}))
        finally:
            if saved_universe is None:
                os.environ.pop(trader.STOCK_UNIVERSE_ENV, None)
            else:
                os.environ[trader.STOCK_UNIVERSE_ENV] = saved_universe

    def test_execute_actions_blocks_new_buy_when_position_count_limit_reached(self):
        original_execution_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "新股", "source": "test"}
            positions = {
                f"60000{i}": {
                    "code": f"60000{i}",
                    "name": f"持仓{i}",
                    "qty": 100,
                    "avg_cost": 10.0,
                    "last_price": 10.0,
                }
                for i in range(trader.MAX_OPEN_POSITIONS)
            }
            state = {"cash": 100000.0, "positions": positions, "trade_log": []}
            decision = {
                "actions": [{"action": "BUY", "code": "601999", "name": "新股", "shares": 1000}]
            }
            candidates = [{
                "code": "601999",
                "name": "新股",
                "best_strategy": "b3_accelerate",
                "best_score": 10.0,
                "entry_threshold": 8.5,
                "distance_pct": 1.0,
                "actionable": True,
                "hard_blockers": [],
            }]

            executed = trader.execute_actions(
                state,
                decision,
                candidates,
                True,
                "连续竞价交易时段",
                permissive_market_context(),
            )
        finally:
            trader.is_a_share_execution_time = original_execution_time
            trader.execution_quote = original_quote

        self.assertEqual(executed, [])
        self.assertNotIn("601999", state["positions"])
        self.assertIn("持仓已达", decision["execution_blocked_reason"])

    def test_execute_actions_blocks_zettaranc_position_over_strategy_cap(self):
        original_execution_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "测试股", "source": "test"}
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {
                "actions": [{"action": "BUY", "code": "600000", "name": "测试股", "shares": 2000}]
            }
            candidates = [{
                "code": "600000",
                "name": "测试股",
                "best_strategy": "b3_accelerate",
                "best_score": 10.0,
                "entry_threshold": 8.5,
                "distance_pct": 1.0,
                "actionable": True,
                "hard_blockers": [],
            }]

            executed = trader.execute_actions(
                state,
                decision,
                candidates,
                True,
                "连续竞价交易时段",
                permissive_market_context(),
            )
        finally:
            trader.is_a_share_execution_time = original_execution_time
            trader.execution_quote = original_quote

        self.assertEqual(executed, [])
        self.assertNotIn("600000", state["positions"])
        self.assertIn("单票仓位20.00%超过10%硬上限", decision["execution_blocked_reason"])

    def test_execute_actions_blocks_zettaranc_add_above_position_cap(self):
        original_execution_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "测试股", "source": "test"}
            state = {
                "cash": 100000.0,
                "positions": {
                    "600000": {
                        "code": "600000",
                        "name": "测试股",
                        "qty": 1000,
                        "avg_cost": 9.0,
                        "last_price": 10.0,
                        "buy_strategy": "b3_accelerate",
                    },
                    "600001": {"code": "600001", "name": "持仓1", "qty": 100, "avg_cost": 10.0, "last_price": 10.0},
                    "600002": {"code": "600002", "name": "持仓2", "qty": 100, "avg_cost": 10.0, "last_price": 10.0},
                },
                "trade_log": [],
            }
            decision = {
                "actions": [{"action": "BUY", "code": "600000", "name": "测试股", "shares": 500, "reason": "B3中继顺势确认加仓"}]
            }
            candidates = [{
                "code": "600000",
                "name": "测试股",
                "best_strategy": "b3_accelerate",
                "best_score": 10.0,
                "entry_threshold": 8.5,
                "distance_pct": 1.0,
                "actionable": True,
                "hard_blockers": [],
            }]
            market_ctx = {**permissive_market_context(), "max_open_positions": 3, "max_new_buys_per_decision": 0}

            executed = trader.execute_actions(
                state,
                decision,
                candidates,
                True,
                "连续竞价交易时段",
                market_ctx,
            )
        finally:
            trader.is_a_share_execution_time = original_execution_time
            trader.execution_quote = original_quote

        self.assertEqual(executed, [])
        self.assertEqual(state["positions"]["600000"]["qty"], 1000)
        self.assertIn("单票仓位", decision["execution_blocked_reason"])
        self.assertIn("超过10%硬上限", decision["execution_blocked_reason"])

    def test_execute_actions_blocks_near_full_zettaranc_position(self):
        original_execution_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "测试股", "source": "test"}
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {
                "actions": [{"action": "BUY", "code": "600000", "name": "测试股", "shares": 9900}]
            }
            candidates = [{
                "code": "600000",
                "name": "测试股",
                "best_strategy": "b3_accelerate",
                "best_score": 10.0,
                "entry_threshold": 8.5,
                "distance_pct": 1.0,
                "actionable": True,
                "hard_blockers": [],
            }]

            executed = trader.execute_actions(
                state,
                decision,
                candidates,
                True,
                "连续竞价交易时段",
                permissive_market_context(),
            )
        finally:
            trader.is_a_share_execution_time = original_execution_time
            trader.execution_quote = original_quote

        self.assertEqual(executed, [])
        self.assertNotIn("600000", state["positions"])
        self.assertIn("单票仓位99.00%超过10%硬上限", decision["execution_blocked_reason"])

    def test_execute_actions_blocks_zettaranc_buy_over_total_cap(self):
        original_execution_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "测试股", "source": "test"}
            state = {
                "cash": 25000.0,
                "positions": {
                    "600001": {"qty": 2500, "avg_cost": 10.0, "last_price": 10.0},
                    "600002": {"qty": 2500, "avg_cost": 10.0, "last_price": 10.0},
                    "600003": {"qty": 2500, "avg_cost": 10.0, "last_price": 10.0},
                },
                "trade_log": [],
            }
            decision = {"actions": [{"action": "BUY", "code": "600000", "name": "测试股", "shares": 1000}]}
            candidates = [{
                "code": "600000", "name": "测试股", "best_strategy": "b3_accelerate",
                "best_score": 10.0, "entry_threshold": 8.5, "distance_pct": 1.0,
                "actionable": True, "hard_blockers": [],
            }]

            executed = trader.execute_actions(
                state, decision, candidates, True, "连续竞价交易时段", permissive_market_context()
            )
        finally:
            trader.is_a_share_execution_time = original_execution_time
            trader.execution_quote = original_quote

        self.assertEqual(executed, [])
        self.assertIn("总仓位85.00%超过80%硬上限", decision["execution_blocked_reason"])

    def test_execute_actions_blocks_non_lot_model_size_without_rounding(self):
        original_execution_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "测试股", "source": "test"}
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {
                "actions": [{"action": "BUY", "code": "600000", "name": "测试股", "shares": 150}]
            }
            candidates = [{
                "code": "600000",
                "name": "测试股",
                "best_strategy": "b3_accelerate",
                "best_score": 10.0,
                "entry_threshold": 8.5,
                "distance_pct": 1.0,
                "actionable": True,
                "hard_blockers": [],
            }]

            executed = trader.execute_actions(
                state,
                decision,
                candidates,
                True,
                "连续竞价交易时段",
                permissive_market_context(),
            )
        finally:
            trader.is_a_share_execution_time = original_execution_time
            trader.execution_quote = original_quote

        self.assertEqual(executed, [])
        self.assertEqual(state["positions"], {})
        self.assertIn("不是100股整数倍", decision["execution_blocked_reason"])

    def test_execute_actions_blocks_model_sell_size_over_available_without_clipping(self):
        original_execution_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "测试股", "source": "test"}
            state = {
                "cash": 0.0,
                "positions": {
                    "600000": {
                        "code": "600000",
                        "name": "测试股",
                        "qty": 1000,
                        "avg_cost": 9.0,
                        "last_price": 10.0,
                        "buy_date_lots": {"2026-06-23": 1000},
                    }
                },
                "trade_log": [],
            }
            decision = {
                "actions": [{"action": "SELL", "code": "600000", "name": "测试股", "shares": 1200}]
            }

            executed = trader.execute_actions(state, decision, [], True, "连续竞价交易时段", permissive_market_context())
        finally:
            trader.is_a_share_execution_time = original_execution_time
            trader.execution_quote = original_quote

        self.assertEqual(executed, [])
        self.assertEqual(state["positions"]["600000"]["qty"], 1000)
        self.assertIn("模型卖出仓位1200股超过可卖1000股", decision["execution_blocked_reason"])
        self.assertIn("不自动缩小", decision["execution_blocked_reason"])

    def test_market_guidance_derives_morning_position_pace(self):
        reports = [{
            "title": "A股竞价盘前总结",
            "time": "2026-06-24 09:25:00",
            "content": "\n".join([
                "🎯 **今日买卖指引**",
                "· 风险级别：平衡",
                "· 开仓节奏：上午最多2-3只；先试错1笔",
            ]),
        }]

        ctx = trader.derive_market_strategy_context(reports, datetime(2026, 6, 24, 10, 0, 0))

        self.assertEqual(ctx["tone"], "balanced")
        self.assertEqual(ctx["max_open_positions"], min(3, trader.MAX_OPEN_POSITIONS))
        self.assertEqual(ctx["max_new_buys_per_decision"], 1)
        self.assertIn("午盘前", ctx["session_note"])

    def test_defensive_market_guidance_allows_reduced_buy_budget(self):
        reports = [{
            "title": "A股竞价盘前总结",
            "time": "2026-07-02 09:25:04",
            "content": "\n".join([
                "🎯 **今日买卖指引**",
                "· 风险级别：防守",
                "· 开仓节奏：上午只观察或卖出，原则上不新开仓；先等风险端收缩",
                "· 买入指引：竞价强股只列观察，至少等开盘15分钟承接确认",
            ]),
        }]

        ctx = trader.derive_market_strategy_context(reports, datetime(2026, 7, 2, 10, 0, 0))

        self.assertEqual(ctx["tone"], "defensive")
        self.assertTrue(ctx["allow_new_buys"])
        self.assertEqual(ctx["max_open_positions"], 2)
        self.assertEqual(ctx["max_new_buys_per_decision"], 1)
        self.assertEqual(ctx["max_total_position_pct"], 35.0)
        self.assertEqual(ctx["min_cash_reserve_pct"], 60.0)
        self.assertEqual(ctx["buy_budget_multiplier"], 0.35)

    def test_periodic_b1_snapshot_overrides_stale_report_tone(self):
        original_loader = trader.load_today_market_monitor_reports
        try:
            trader.load_today_market_monitor_reports = lambda now=None, limit=3: [{
                "title": "A股竞价盘前总结",
                "time": "2026-07-10 09:25:06",
                "content": "🎯 **今日买卖指引**\n· 风险级别：防守\n· 开仓节奏：上午只观察",
            }]
            payload = {
                "generated_at": "2026-07-10 10:05:00",
                "schedule_slot": "2026-07-10 10:00",
                "market_snapshot": {
                    "source": "b1_mainboard_quotes",
                    "universe": "mainboard_non_st",
                    "captured_at": "2026-07-10 10:00:05",
                    "quote_time": "2026-07-10 10:00:04",
                    "pool_count": 3100,
                    "sample_count": 3000,
                    "coverage": 0.9677,
                    "up": 2300,
                    "down": 600,
                    "flat": 100,
                    "limit_up": 80,
                    "limit_down": 2,
                    "average_change_pct": 1.23,
                    "median_change_pct": 0.88,
                },
            }

            ctx = trader.market_strategy_context_for_b1(payload, datetime(2026, 7, 10, 10, 5, 0))
        finally:
            trader.load_today_market_monitor_reports = original_loader

        self.assertEqual(ctx["tone"], "offensive")
        self.assertEqual(ctx["source_title"], "实战定时选股实时盘面")
        self.assertEqual(ctx["source_time"], "2026-07-10 10:00:04")
        self.assertEqual(ctx["refresh_mode"], "b1_periodic")
        self.assertEqual(ctx["market_snapshot"]["up"], 2300)
        self.assertEqual(ctx["max_open_positions"], min(trader.MAX_OPEN_POSITIONS, trader.MORNING_MAX_OPEN_POSITIONS))
        self.assertEqual(ctx["max_new_buys_per_decision"], min(trader.MAX_NEW_BUYS_PER_DECISION, 2))

    def test_periodic_b1_snapshot_with_low_coverage_falls_back(self):
        original_current = trader.current_market_strategy_context
        calls = {"current": 0}
        try:
            def fake_current(now=None):
                calls["current"] += 1
                return {"tone": "cautious", "tone_label": "谨慎", "source_time": "2026-07-10 09:25:06"}

            trader.current_market_strategy_context = fake_current
            ctx = trader.market_strategy_context_for_b1({
                "market_snapshot": {
                    "quote_time": "2026-07-10 10:00:00",
                    "pool_count": 3000,
                    "sample_count": 1200,
                    "coverage": 0.4,
                    "up": 900,
                    "down": 250,
                    "flat": 50,
                    "limit_up": 40,
                    "limit_down": 1,
                }
            }, datetime(2026, 7, 10, 10, 2, 0))
        finally:
            trader.current_market_strategy_context = original_current

        self.assertEqual(calls["current"], 1)
        self.assertEqual(ctx["tone"], "cautious")

    def test_periodic_b1_defensive_snapshot_without_composite_confirmation_only_reduces_risk(self):
        original_loader = trader.load_today_market_monitor_reports
        try:
            trader.load_today_market_monitor_reports = lambda now=None, limit=3: []
            ctx = trader.market_strategy_context_for_b1({
                "schedule_slot": "2026-07-10 10:30",
                "market_snapshot": {
                    "quote_time": "2026-07-10 10:30:03",
                    "pool_count": 3000,
                    "sample_count": 3000,
                    "coverage": 1.0,
                    "up": 600,
                    "down": 2300,
                    "flat": 100,
                    "limit_up": 2,
                    "limit_down": 20,
                },
            }, datetime(2026, 7, 10, 10, 31, 0))
        finally:
            trader.load_today_market_monitor_reports = original_loader

        self.assertEqual(ctx["tone"], "defensive")
        self.assertTrue(ctx["allow_new_buys"])
        self.assertEqual(ctx["max_new_buys_per_decision"], 1)

    def test_market_hard_stop_requires_two_composite_confirmations_and_two_recoveries(self):
        risk = {
            "quote_time": "2026-07-10 10:00:00",
            "total_amount": 6e11,
            "up": 600,
            "down": 2300,
            "limit_up": 2,
            "limit_down": 20,
            "median_change_pct": -1.3,
            "core_index_count": 3,
            "index_below_ma20_count": 2,
            "index_average_change_pct": -1.1,
        }

        first = trader.evaluate_market_hard_stop(risk, {}, datetime(2026, 7, 10, 10, 0))
        second_risk = {**risk, "quote_time": "2026-07-10 10:30:00", "total_amount": 8e11}
        second = trader.evaluate_market_hard_stop(second_risk, first, datetime(2026, 7, 10, 10, 30))

        self.assertTrue(first["hard_stop_candidate"])
        self.assertFalse(first["hard_stop_active"])
        self.assertEqual(first["hard_stop_confirmations"], 1)
        self.assertTrue(second["hard_stop_active"])
        self.assertEqual(second["hard_stop_confirmations"], 2)

        recovery = {
            **second_risk,
            "quote_time": "2026-07-10 11:00:00",
            "up": 1700,
            "down": 1200,
            "limit_up": 12,
            "limit_down": 2,
            "median_change_pct": 0.1,
            "index_below_ma20_count": 1,
            "index_average_change_pct": 0.2,
            "total_amount": 1.1e12,
        }
        recovery_first = trader.evaluate_market_hard_stop(recovery, second, datetime(2026, 7, 10, 11, 0))
        recovery_second_input = {**recovery, "quote_time": "2026-07-10 11:20:00", "total_amount": 1.3e12}
        recovery_second = trader.evaluate_market_hard_stop(
            recovery_second_input, recovery_first, datetime(2026, 7, 10, 11, 20)
        )

        self.assertTrue(recovery_first["hard_stop_active"])
        self.assertEqual(recovery_first["recovery_confirmations"], 1)
        self.assertFalse(recovery_second["hard_stop_active"])
        self.assertEqual(recovery_second["recovery_confirmations"], 2)

    def test_confirmed_market_hard_stop_emits_execution_block(self):
        prior = {
            "quote_time": "2026-07-10 10:00:00",
            "hard_stop_candidate": True,
            "hard_stop_confirmations": 1,
            "hard_stop_active": False,
            "amount_per_minute": 1e10,
        }
        payload = {
            "market_snapshot": {
                "quote_time": "2026-07-10 10:30:00",
                "pool_count": 3000,
                "sample_count": 3000,
                "coverage": 1.0,
                "up": 600,
                "down": 2300,
                "flat": 100,
                "limit_up": 2,
                "limit_down": 20,
                "average_change_pct": -1.2,
                "median_change_pct": -1.3,
                "total_amount": 7e11,
                "core_index_count": 3,
                "index_below_ma20_count": 2,
                "index_average_change_pct": -1.1,
            }
        }

        report = trader._periodic_market_snapshot_report(
            payload, datetime(2026, 7, 10, 10, 31), prior
        )
        ctx = trader.derive_market_strategy_context([report], datetime(2026, 7, 10, 10, 31))

        self.assertTrue(report["metadata"]["market_snapshot"]["hard_stop_active"])
        self.assertFalse(ctx["allow_new_buys"])
        self.assertEqual(ctx["max_new_buys_per_decision"], 0)

    def test_current_market_context_prefers_newer_b1_or_report_source(self):
        original_current = trader.current_market_strategy_context
        try:
            trader.current_market_strategy_context = lambda now=None: {
                "tone": "cautious",
                "tone_label": "谨慎",
                "source_title": "A股午盘总结",
                "source_time": "2026-07-10 11:40:04",
            }
            newer_b1 = trader.select_current_market_strategy_context({
                "market_decision_context": {
                    "tone": "offensive",
                    "tone_label": "进攻",
                    "source_title": "B1定时选股实时盘面",
                    "source_time": "2026-07-10 13:00:05",
                }
            }, datetime(2026, 7, 10, 13, 1, 0))
            older_b1 = trader.select_current_market_strategy_context({
                "market_decision_context": {
                    "tone": "defensive",
                    "tone_label": "防守",
                    "source_title": "B1定时选股实时盘面",
                    "source_time": "2026-07-10 11:20:05",
                }
            }, datetime(2026, 7, 10, 11, 50, 0))
        finally:
            trader.current_market_strategy_context = original_current

        self.assertEqual(newer_b1["tone"], "offensive")
        self.assertEqual(older_b1["tone"], "cautious")
        self.assertEqual(newer_b1["context_kind"], "current")

    def test_offensive_market_guidance_uses_qualitative_position_bias(self):
        reports = [{
            "title": "A股午盘总结",
            "time": "2026-07-02 13:00:00",
            "content": "\n".join([
                "🎯 **今日买卖指引**",
                "· 风险级别：进攻",
                "· 开仓节奏：赚钱效应较活跃，可围绕主线正常试错",
            ]),
        }]

        ctx = trader.derive_market_strategy_context(reports, datetime(2026, 7, 2, 13, 30, 0))
        prompt = trader.format_market_strategy_context_for_prompt(ctx)

        self.assertEqual(ctx["tone"], "offensive")
        self.assertIn("仓位倾向：可提高集中度", prompt)
        self.assertNotIn("单票≤", prompt)
        self.assertNotIn("总仓≤", prompt)
        self.assertNotIn("现金≥", prompt)

    def test_explicit_market_guidance_pause_still_blocks_new_buys(self):
        reports = [{
            "title": "A股竞价盘前总结",
            "time": "2026-07-02 09:25:04",
            "content": "\n".join([
                "🎯 **今日买卖指引**",
                "· 风险级别：防守",
                "· 开仓节奏：暂停新开仓，只卖不买；先等跌停风险收缩",
            ]),
        }]

        ctx = trader.derive_market_strategy_context(reports, datetime(2026, 7, 2, 10, 0, 0))

        self.assertEqual(ctx["tone"], "defensive")
        self.assertFalse(ctx["allow_new_buys"])
        self.assertEqual(ctx["max_new_buys_per_decision"], 0)
        self.assertEqual(ctx["buy_budget_multiplier"], 0.0)

    def test_after_1430_does_not_automatically_block_new_buys(self):
        reports = [{
            "title": "A股盘中总结",
            "time": "2026-07-02 14:35:04",
            "content": "\n".join([
                "🎯 **今日买卖指引**",
                "· 风险级别：平衡",
                "· 开仓节奏：只做高确定性机会",
            ]),
        }]

        ctx = trader.derive_market_strategy_context(reports, datetime(2026, 7, 2, 14, 45, 0))

        self.assertEqual(ctx["phase"], "afternoon")
        self.assertTrue(ctx["allow_new_buys"])
        self.assertGreater(ctx["max_new_buys_per_decision"], 0)

    def test_market_guidance_extracts_next_day_premarket_heading(self):
        lines = trader.extract_market_guidance_lines("\n".join([
            "🎯 **次日盘前指引**",
            "· 风险级别：谨慎",
            "· 开仓节奏：次日最多1笔",
            "",
            "📌 **次日关注池**",
            "· 主线方向：半导体",
        ]))

        self.assertEqual(lines, ["风险级别：谨慎", "开仓节奏：次日最多1笔"])

    def test_market_guidance_loads_prior_close_before_today_reports(self):
        original_push_history = sys.modules.get("push_history")
        original_cached_overnight = trader._load_cached_overnight_us_market_report
        previous_close = {
            "title": "A股盘后总结",
            "time_text": "2026-07-01 15:10:02",
            "content": "\n".join([
                "🎯 **次日盘前指引**",
                "· 风险级别：进攻",
                "· 开仓节奏：次日可正常试错",
            ]),
            "metadata": {
                "decision_guidance": ["风险级别：进攻", "开仓节奏：次日可正常试错"],
            },
        }
        sys.modules["push_history"] = types.SimpleNamespace(
            query_messages=lambda **kwargs: {"records": [previous_close]}
        )
        try:
            trader._load_cached_overnight_us_market_report = lambda now=None: None
            reports = trader.load_today_market_monitor_reports(datetime(2026, 7, 2, 9, 20, 0))
        finally:
            trader._load_cached_overnight_us_market_report = original_cached_overnight
            if original_push_history is None:
                sys.modules.pop("push_history", None)
            else:
                sys.modules["push_history"] = original_push_history

        ctx = trader.derive_market_strategy_context(reports, datetime(2026, 7, 2, 9, 20, 0))
        self.assertEqual([r["time"] for r in reports], ["2026-07-01 15:10:02"])
        self.assertEqual(ctx["tone"], "offensive")
        self.assertEqual(ctx["source_title"], "A股盘后总结")
        self.assertEqual(ctx["source_time"], "2026-07-01 15:10:02")

    def test_market_guidance_keeps_today_auction_ahead_of_prior_close(self):
        original_push_history = sys.modules.get("push_history")
        original_cached_overnight = trader._load_cached_overnight_us_market_report
        auction_report = {
            "title": "A股竞价盘前总结",
            "time_text": "2026-07-02 09:25:09",
            "content": "\n".join([
                "🎯 **今日买卖指引**",
                "· 风险级别：谨慎",
                "· 开仓节奏：先观察再试错",
            ]),
            "metadata": {
                "decision_guidance": ["风险级别：谨慎", "开仓节奏：先观察再试错"],
            },
        }
        previous_close = {
            "title": "A股盘后总结",
            "time_text": "2026-07-01 15:10:02",
            "content": "\n".join([
                "🎯 **次日盘前指引**",
                "· 风险级别：进攻",
                "· 开仓节奏：次日可正常试错",
            ]),
            "metadata": {
                "decision_guidance": ["风险级别：进攻", "开仓节奏：次日可正常试错"],
            },
        }
        sys.modules["push_history"] = types.SimpleNamespace(
            query_messages=lambda **kwargs: {"records": [auction_report, previous_close]}
        )
        try:
            trader._load_cached_overnight_us_market_report = lambda now=None: None
            reports = trader.load_today_market_monitor_reports(datetime(2026, 7, 2, 9, 26, 0))
        finally:
            trader._load_cached_overnight_us_market_report = original_cached_overnight
            if original_push_history is None:
                sys.modules.pop("push_history", None)
            else:
                sys.modules["push_history"] = original_push_history

        ctx = trader.derive_market_strategy_context(reports, datetime(2026, 7, 2, 9, 26, 0))
        self.assertEqual([r["time"] for r in reports], ["2026-07-02 09:25:09", "2026-07-01 15:10:02"])
        self.assertEqual(ctx["tone"], "cautious")
        self.assertEqual(ctx["source_title"], "A股竞价盘前总结")
        self.assertEqual(ctx["source_time"], "2026-07-02 09:25:09")

    def test_market_guidance_includes_overnight_us_as_overlay(self):
        original_push_history = sys.modules.get("push_history")
        auction_report = {
            "title": "A股竞价盘前总结",
            "time_text": "2026-07-02 09:25:09",
            "content": "\n".join([
                "🎯 **今日买卖指引**",
                "· 风险级别：进攻",
                "· 开仓节奏：可正常试错",
            ]),
            "metadata": {
                "decision_guidance": ["风险级别：进攻", "开仓节奏：可正常试错"],
            },
        }
        us_report = {
            "title": "隔夜美股盘面总结",
            "time_text": "2026-07-02 08:00:00",
            "content": "\n".join([
                "牛牛大王，隔夜美股盘面总结来了：",
                "📊 **美股概况** · 2026-07-02 08:00:00",
                "💬 隔夜美股偏弱或分化，今日不急着追高。",
                "",
                "🧭 **美股板块映射**",
                "`半导体(SMH)` +1.20% → A股：半导体、芯片设备；正映射，竞价确认后加分。",
                "",
                "🎯 **今日买卖指引**",
                "· 风险级别：谨慎",
                "· 买入节奏：降低预算，先观察开盘 15 分钟。",
                "· 选股方向：只看有资金承接的科技映射和强趋势票。",
            ]),
            "metadata": {
                "decision_guidance": [
                    "风险级别：谨慎",
                    "买入节奏：降低预算，先观察开盘 15 分钟。",
                    "选股方向：只看有资金承接的科技映射和强趋势票。",
                ],
                "summary": "隔夜美股偏弱或分化，今日不急着追高。",
            },
        }
        previous_close = {
            "title": "A股盘后总结",
            "time_text": "2026-07-01 15:10:02",
            "content": "\n".join([
                "🎯 **次日盘前指引**",
                "· 风险级别：平衡",
                "· 开仓节奏：次日可正常试错",
            ]),
        }
        sys.modules["push_history"] = types.SimpleNamespace(
            query_messages=lambda **kwargs: {"records": [auction_report, us_report, previous_close]}
        )
        try:
            reports = trader.load_today_market_monitor_reports(datetime(2026, 7, 2, 9, 26, 0))
        finally:
            if original_push_history is None:
                sys.modules.pop("push_history", None)
            else:
                sys.modules["push_history"] = original_push_history

        ctx = trader.derive_market_strategy_context(reports, datetime(2026, 7, 2, 9, 26, 0))
        prompt = trader.format_market_strategy_context_for_prompt(ctx)

        self.assertEqual(
            [r["time"] for r in reports],
            ["2026-07-02 09:25:09", "2026-07-02 08:00:00", "2026-07-01 15:10:02"],
        )
        self.assertEqual(ctx["tone"], "offensive")
        self.assertEqual(ctx["source_title"], "A股竞价盘前总结")
        self.assertEqual(ctx["overnight_us"]["tone"], "cautious")
        self.assertIn("半导体(SMH)", ctx["overnight_us"]["sector_mappings"][0])
        self.assertEqual(ctx["max_new_buys_per_decision"], 1)
        self.assertLessEqual(ctx["max_total_position_pct"], 60.0)
        self.assertIn("【隔夜美股盘面】", prompt)
        self.assertIn("板块映射", prompt)
        self.assertIn("芯片设备", prompt)
        self.assertIn("降低预算", prompt)

    def test_execute_actions_uses_market_guidance_position_cap(self):
        original_execution_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        original_market_context = trader.current_market_strategy_context
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "上午连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "新股", "source": "test"}
            trader.current_market_strategy_context = lambda now=None: {
                "tone_label": "平衡",
                "max_open_positions": 3,
                "max_new_buys_per_decision": 1,
                "max_total_position_pct": trader.MAX_TOTAL_POSITION_PCT,
                "min_cash_reserve_pct": trader.MIN_CASH_RESERVE_PCT,
                "buy_budget_multiplier": 1.0,
                "allow_new_buys": True,
            }
            positions = {
                f"60010{i}": {
                    "code": f"60010{i}",
                    "name": f"持仓{i}",
                    "qty": 100,
                    "avg_cost": 10.0,
                    "last_price": 10.0,
                }
                for i in range(3)
            }
            state = {"cash": 100000.0, "positions": positions, "trade_log": []}
            decision = {"actions": [{"action": "BUY", "code": "601999", "name": "新股", "shares": 1000}]}
            candidates = [{
                "code": "601999",
                "name": "新股",
                "best_strategy": "b3_accelerate",
                "best_score": 10.0,
                "entry_threshold": 8.5,
                "distance_pct": 1.0,
                "actionable": True,
                "hard_blockers": [],
            }]

            executed = trader.execute_actions(state, decision, candidates, True, "上午连续竞价交易时段")
        finally:
            trader.is_a_share_execution_time = original_execution_time
            trader.execution_quote = original_quote
            trader.current_market_strategy_context = original_market_context

        self.assertEqual(executed, [])
        self.assertNotIn("601999", state["positions"])
        self.assertIn("盘面动态持仓已达3只上限", decision["execution_blocked_reason"])

    def test_overlimit_buy_decision_is_refined_before_execution(self):
        original_execution_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        original_load_config = trader.load_decision_model_config
        original_request = trader.request_chat_content
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "上午连续竞价交易时段")
            trader.execution_quote = lambda code: {
                "600001": {"price": 10.0, "name": "先给股", "source": "test"},
                "600002": {"price": 20.0, "name": "优选股", "source": "test"},
            }[code]
            trader.load_decision_model_config = lambda: ("https://decision.example/v1", "key")

            def fake_request(base_url, api_key, payload, model_name, max_retries=3, timeout=60):
                self.assertIn("最多允许新开仓：1笔", payload["messages"][0]["content"])
                return json.dumps({
                    "summary": "优选股确定性更高，放弃先给股",
                    "keep_buy_codes": ["600002"],
                    "drop_buys": [{"code": "600001", "reason": "确定性不如优选股"}],
                }, ensure_ascii=False)

            trader.request_chat_content = fake_request
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {
                "summary": "初次给出两笔买入",
                "actions": [
                    {"action": "BUY", "code": "600001", "name": "先给股", "shares": 1000, "reason": "第一笔"},
                    {"action": "BUY", "code": "600002", "name": "优选股", "shares": 1000, "reason": "第二笔"},
                ],
            }
            candidates = [
                {"code": "600001", "name": "先给股", "best_score": 9.0, "entry_threshold": 8.0, "distance_pct": 1.0, "actionable": True, "hard_blockers": []},
                {"code": "600002", "name": "优选股", "best_score": 10.0, "entry_threshold": 8.0, "distance_pct": 1.0, "actionable": True, "hard_blockers": []},
            ]
            market_ctx = {**permissive_market_context(), "max_new_buys_per_decision": 1}

            refinement = trader.refine_overlimit_buy_actions(
                decision,
                state,
                candidates,
                {"positions": [], "trade_log": [], "cash": 100000, "total_equity": 100000},
                market_ctx,
            )
            executed = trader.execute_actions(state, decision, candidates, True, "上午连续竞价交易时段", market_ctx)
        finally:
            trader.is_a_share_execution_time = original_execution_time
            trader.execution_quote = original_quote
            trader.load_decision_model_config = original_load_config
            trader.request_chat_content = original_request

        self.assertEqual(refinement["status"], "model_refined")
        self.assertEqual(refinement["kept_codes"], ["600002"])
        self.assertEqual(decision["actions"][0]["action"], "HOLD")
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0]["code"], "600002")
        self.assertIn("600002", state["positions"])
        self.assertNotIn("600001", state["positions"])

    def test_run_decision_after_b1_records_market_context_each_round(self):
        state = {
            "cash": 100000.0,
            "positions": {},
            "trade_log": [],
            "decision_log": [],
            "equity_history": [],
            "daily_equity_history": [],
        }
        market_ctx = {
            "enabled": True,
            "available": True,
            "tone": "defensive",
            "tone_label": "防守",
            "phase": "morning",
            "max_open_positions": 2,
            "max_new_buys_per_decision": 1,
            "max_total_position_pct": 35.0,
            "min_cash_reserve_pct": 60.0,
            "buy_budget_multiplier": 0.35,
            "allow_new_buys": True,
            "source_title": "A股竞价盘前总结",
            "source_time": "2026-07-02 09:25:04",
            "guidance_lines": ["风险级别：防守"],
        }
        calls = {"market_context": 0, "saved": 0}
        originals = {
            "load_state": trader.load_state,
            "save_state": trader.save_state,
            "current_market_strategy_context": trader.current_market_strategy_context,
            "check_daily_loss_budget": trader.check_daily_loss_budget,
            "get_adaptive_params": trader.get_adaptive_params,
            "is_a_share_execution_time": trader.is_a_share_execution_time,
            "deferred_execution_due_at": trader.deferred_execution_due_at,
            "check_market_environment": trader.check_market_environment,
            "check_market_sentiment": trader.check_market_sentiment,
            "record_equity": trader.record_equity,
            "_sync_decision_to_db": trader._sync_decision_to_db,
        }
        try:
            trader.load_state = lambda: state
            trader.save_state = lambda _state: calls.__setitem__("saved", calls["saved"] + 1)

            def fake_market_context(now=None):
                calls["market_context"] += 1
                return dict(market_ctx)

            trader.current_market_strategy_context = fake_market_context
            trader.check_daily_loss_budget = lambda _state: (False, 0.0)
            trader.get_adaptive_params = lambda: {}
            trader.is_a_share_execution_time = lambda dt=None: (False, "非A股可成交时段")
            trader.deferred_execution_due_at = lambda schedule_slot: ""
            trader.check_market_environment = lambda: {"bullish": True, "detail": "test"}
            trader.check_market_sentiment = lambda: {"sentiment": "neutral", "detail": "test", "hot_sectors": []}
            trader.record_equity = lambda _state: None
            trader._sync_decision_to_db = lambda _log: None

            result = trader.run_decision_after_b1({"generated_at": "2026-07-02 10:00:00", "items": []})
        finally:
            for name, value in originals.items():
                setattr(trader, name, value)

        self.assertEqual(calls["market_context"], 1)
        self.assertEqual(calls["saved"], 1)
        self.assertEqual(state["market_decision_context"]["tone"], "defensive")
        self.assertEqual(state["decision_log"][-1]["market_decision_context"]["tone_label"], "防守")
        self.assertEqual(state["decision_log"][-1]["decision"]["market_guidance"]["max_new_buys_per_decision"], 1)
        self.assertEqual(result["portfolio"]["market_decision_context"]["tone"], "defensive")

    def test_morning_schedule_completed_during_lunch_defers_to_13(self):
        due_at = trader.deferred_execution_due_at(
            "2026-06-25 11:20",
            datetime(2026, 6, 25, 11, 43, 49),
        )
        self.assertEqual(due_at, "2026-06-25 13:00:00")

    def test_due_pending_decision_executes_when_session_reopens(self):
        original_state_file = trader.STATE_FILE
        original_execution_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        original_sync_decision = trader._sync_decision_to_db
        original_sync_trades = trader._sync_trades_to_db
        original_sync_positions = trader._sync_positions_to_db
        original_market_context = trader.current_market_strategy_context
        with tempfile.TemporaryDirectory() as td:
            try:
                trader.STATE_FILE = Path(td) / "portfolio.json"
                trader.is_a_share_execution_time = lambda dt=None: (True, "下午连续竞价交易时段")
                trader.execution_quote = lambda code: {"price": 10.0, "name": "测试股", "source": "test"}
                trader._sync_decision_to_db = lambda log: None
                trader._sync_trades_to_db = lambda items: None
                trader._sync_positions_to_db = lambda state: None
                trader.current_market_strategy_context = lambda now=None: permissive_market_context()
                trader.save_state({
                    "initial_cash": 100000.0,
                    "cash": 100000.0,
                    "positions": {},
                    "trade_log": [],
                    "decision_log": [],
                    "equity_history": [],
                    "pending_decisions": [{
                        "id": "2026-06-25 11:20|2026-06-25 11:43:28",
                        "status": "pending",
                        "created_at": "2026-06-25 11:43:49",
                        "due_at": "2026-06-25 13:00:00",
                        "b1_generated_at": "2026-06-25 11:43:28",
                        "schedule_slot": "2026-06-25 11:20",
                        "schedule_run_kind": "catchup",
                        "decision": {
                            "summary": "午休前策略",
                            "actions": [{"action": "BUY", "code": "600000", "name": "测试股", "shares": 1000}],
                        },
                        "candidates": [{
                            "code": "600000",
                            "name": "测试股",
                            "score_basis": "B3中继",
                            "best_score": 10.0,
                            "entry_threshold": 8.5,
                            "distance_pct": 1.0,
                            "risk_flags": [],
                        }],
                    }],
                })

                result = trader.execute_due_pending_decisions(datetime(2026, 6, 25, 13, 0, 1))
                state = trader.load_state()
            finally:
                trader.STATE_FILE = original_state_file
                trader.is_a_share_execution_time = original_execution_time
                trader.execution_quote = original_quote
                trader._sync_decision_to_db = original_sync_decision
                trader._sync_trades_to_db = original_sync_trades
                trader._sync_positions_to_db = original_sync_positions
                trader.current_market_strategy_context = original_market_context

        self.assertEqual(result["attempted"], 1)
        self.assertEqual(len(result["executed"]), 1)
        self.assertEqual(result["executed"][0]["action"], "BUY")
        self.assertEqual(state["pending_decisions"][0]["status"], "executed")
        self.assertEqual(len(state["trade_log"]), 1)
        self.assertIn("延迟成交触发", state["decision_log"][-1]["trade_reason"])

    def test_save_state_preserves_positions_when_disk_has_unseen_trades(self):
        original_state_file = trader.STATE_FILE
        with tempfile.TemporaryDirectory() as td:
            try:
                trader.STATE_FILE = Path(td) / "portfolio.json"
                current = {
                    "initial_cash": 100000.0,
                    "cash": 89990.0,
                    "positions": {
                        "001257": {
                            "code": "001257",
                            "name": "盛龙股份",
                            "qty": 300,
                            "avg_cost": 33.3667,
                        }
                    },
                    "trade_log": [{
                        "time": "2026-06-25 14:58:13",
                        "action": "BUY",
                        "code": "001257",
                        "name": "盛龙股份",
                        "shares": 300,
                        "price": 33.33,
                        "reason": "B3中继",
                    }],
                    "decision_log": [],
                    "equity_history": [],
                }
                trader.STATE_FILE.write_text(json.dumps(current, ensure_ascii=False))

                stale = {
                    "initial_cash": 100000.0,
                    "cash": 100000.0,
                    "positions": {},
                    "trade_log": [],
                    "decision_log": [],
                    "equity_history": [],
                }
                trader.save_state(stale)
                saved = trader.load_state()
            finally:
                trader.STATE_FILE = original_state_file

        self.assertEqual(saved["cash"], 89990.0)
        self.assertIn("001257", saved["positions"])
        self.assertEqual(saved["positions"]["001257"]["qty"], 300)
        self.assertEqual(len(saved["trade_log"]), 1)

    def test_save_state_does_not_resurrect_position_after_merged_sell(self):
        original_state_file = trader.STATE_FILE
        with tempfile.TemporaryDirectory() as td:
            try:
                trader.STATE_FILE = Path(td) / "portfolio.json"
                buy = {
                    "time": "2026-07-10 13:36:41", "action": "BUY", "code": "002654",
                    "name": "万润科技", "shares": 2000, "price": 18.4, "reason": "B3中继",
                }
                unrelated = {
                    "time": "2026-07-13 09:29:59", "action": "BUY", "code": "600001",
                    "name": "其他股票", "shares": 100, "price": 10.0, "reason": "并发成交",
                }
                current = {
                    "initial_cash": 100000.0,
                    "cash": 63200.0,
                    "positions": {"002654": {"code": "002654", "name": "万润科技", "qty": 2000, "avg_cost": 18.4}},
                    "trade_log": [buy, unrelated],
                    "decision_log": [],
                    "equity_history": [],
                }
                trader.STATE_FILE.write_text(json.dumps(current, ensure_ascii=False))

                sell = {
                    "time": "2026-07-13 09:30:09", "action": "SELL", "code": "002654",
                    "name": "万润科技", "shares": 2000, "price": 17.31, "reason": "止损",
                }
                just_sold = {
                    "initial_cash": 100000.0,
                    "cash": 97800.0,
                    "positions": {},
                    "trade_log": [buy, sell],
                    "decision_log": [],
                    "equity_history": [],
                }

                trader.save_state(just_sold)
                saved = trader.load_state()
            finally:
                trader.STATE_FILE = original_state_file

        self.assertNotIn("002654", saved["positions"])
        self.assertEqual({row["code"] for row in saved["trade_log"]}, {"002654", "600001"})
        self.assertTrue(any(row["action"] == "SELL" for row in saved["trade_log"]))

    def test_save_state_preserves_newer_decision_error_during_stale_refresh(self):
        original_state_file = trader.STATE_FILE
        with tempfile.TemporaryDirectory() as td:
            try:
                trader.STATE_FILE = Path(td) / "portfolio.json"
                failure_error = "ValueError: 模型回复JSON解析失败"
                failure = {
                    "time": "2026-07-15 11:34:03",
                    "b1_generated_at": "2026-07-15 11:31:29",
                    "trade_allowed": False,
                    "trade_reason": "午间休市",
                    "decision": {
                        "summary": "模型决策失败，本轮不交易",
                        "actions": [],
                        "model": "deepseek-v4-pro",
                        "error": failure_error,
                    },
                    "executed": [],
                }
                trader.STATE_FILE.write_text(json.dumps({
                    "initial_cash": 100000.0,
                    "cash": 100000.0,
                    "positions": {},
                    "trade_log": [],
                    "decision_log": [failure],
                    "equity_history": [],
                    "last_decision_at": failure["time"],
                    "last_error": failure_error,
                }, ensure_ascii=False))

                stale_refresh = {
                    "initial_cash": 100000.0,
                    "cash": 100000.0,
                    "positions": {},
                    "trade_log": [],
                    "decision_log": [],
                    "equity_history": [],
                    "last_decision_at": "2026-07-15 11:31:54",
                    "last_error": "",
                }
                trader.save_state(stale_refresh)
                saved = trader.load_state()
            finally:
                trader.STATE_FILE = original_state_file

        self.assertEqual(saved["last_decision_at"], "2026-07-15 11:34:03")
        self.assertEqual(saved["last_error"], failure_error)
        self.assertTrue(any(row.get("time") == failure["time"] for row in saved["decision_log"]))

    def test_state_file_write_lock_blocks_another_process(self):
        original_state_file = trader.STATE_FILE
        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "portfolio.json"
            ready_file = Path(td) / "child-ready"
            acquired_file = Path(td) / "child-acquired"
            env = os.environ.copy()
            env["DASHBOARD_HOME"] = td
            env["DASHBOARD_PORTFOLIO_STATE"] = str(state_file)
            env["PYTHONPATH"] = os.pathsep.join([str(SRC), str(COMPAT), env.get("PYTHONPATH", "")])
            child_code = (
                "from pathlib import Path\n"
                "import niuniu_practice_trader as trader\n"
                f"Path({str(ready_file)!r}).write_text('ready')\n"
                "with trader.state_file_write_lock():\n"
                f"    Path({str(acquired_file)!r}).write_text('acquired')\n"
            )
            proc = None
            try:
                trader.STATE_FILE = state_file
                with trader.state_file_write_lock():
                    proc = subprocess.Popen(
                        [sys.executable, "-c", child_code],
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    for _ in range(250):
                        if ready_file.exists():
                            break
                        if proc.poll() is not None:
                            break
                        time.sleep(0.02)
                    self.assertTrue(ready_file.exists())
                    time.sleep(0.1)
                    self.assertFalse(acquired_file.exists())
                stdout, stderr = proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 0, msg=f"stdout={stdout}\nstderr={stderr}")
                self.assertTrue(acquired_file.exists())
            finally:
                if proc is not None and proc.poll() is None:
                    proc.kill()
                    proc.communicate()
                trader.STATE_FILE = original_state_file

    def test_request_chat_json_object_retries_truncated_response_with_more_tokens(self):
        original_request = trader.request_chat_content
        payloads = []
        responses = iter([
            '{"summary":"截断", "actions":[{"action":"HOLD"',
            '{"summary":"重试成功","actions":[]}',
        ])
        try:
            def fake_request(base_url, api_key, payload, model_name, max_retries=3, timeout=60):
                payloads.append(dict(payload))
                return next(responses)

            trader.request_chat_content = fake_request
            result = trader.request_chat_json_object(
                "https://decision.example/v1",
                "key",
                {"model": "deepseek-v4-pro", "messages": [], "max_tokens": 4096},
                "deepseek-v4-pro",
                timeout=180,
            )
        finally:
            trader.request_chat_content = original_request

        self.assertEqual(result["summary"], "重试成功")
        self.assertEqual([payload["max_tokens"] for payload in payloads], [4096, 8192])

    def test_strategy_performance_splits_entry_and_exit_dimensions(self):
        state = {
            "trade_log": [
                {
                    "time": "2026-06-24 10:00:00",
                    "action": "BUY",
                    "code": "600000",
                    "shares": 1000,
                    "reason": "B3中继评分10.0达标",
                },
                {
                    "time": "2026-06-25 10:00:00",
                    "action": "SELL",
                    "code": "600000",
                    "shares": 1000,
                    "pnl": -421.87,
                    "reason": "止损触发",
                },
                {
                    "time": "2026-06-25 10:01:00",
                    "action": "BUY",
                    "code": "600001",
                    "shares": 1000,
                    "reason": "趋势回踩评分9.0达标",
                },
                {
                    "time": "2026-06-25 10:02:00",
                    "action": "SELL",
                    "code": "600001",
                    "shares": 1000,
                    "pnl": 0.0,
                    "exit_signal": "z_white_break",
                    "reason": "白线两日破位",
                },
            ],
            "positions": {
                "600002": {
                    "qty": 1000,
                    "avg_cost": 10.0,
                    "last_price": 11.2,
                    "buy_strategy": "b3_accelerate",
                },
                "600003": {
                    "qty": 1000,
                    "avg_cost": 10.0,
                    "last_price": 9.8,
                    "entry_reason": "趋势回踩评分9.0达标",
                },
            },
        }

        perf = trader.track_strategy_performance(state)

        self.assertEqual(perf["buy_strategy"]["b3_accelerate"]["losses"], 1)
        self.assertEqual(perf["buy_strategy"]["b3_accelerate"]["open_wins"], 1)
        self.assertEqual(perf["buy_strategy"]["b3_accelerate"]["open_pnl"], 1200.0)
        self.assertEqual(perf["buy_strategy"]["trend_pullback"]["flats"], 1)
        self.assertEqual(perf["buy_strategy"]["trend_pullback"]["open_losses"], 1)
        self.assertEqual(perf["exit_rule"]["stop_loss"]["losses"], 1)
        self.assertEqual(perf["exit_rule"]["technical_break"]["flats"], 1)
        self.assertEqual(perf["exit_rule"]["stop_loss"]["trigger_count"], 1)
        self.assertEqual(perf["exit_rule"]["stop_loss"]["items"][0]["code"], "600000")
        self.assertEqual(perf["exit_rule"]["stop_loss"]["items"][0]["pnl"], -421.87)
        self.assertIn("止损", perf["exit_rule"]["stop_loss"]["items"][0]["reason"])
        self.assertEqual(perf["summary"]["closed_trades"], 2)
        self.assertEqual(perf["summary"]["open_positions"], 2)

    def test_execution_quote_marks_auction_reference_price(self):
        original_auction = trader.is_a_share_auction_time
        original_fetch = trader.fetch_realtime_quotes
        try:
            trader.is_a_share_auction_time = lambda dt=None: True
            trader.fetch_realtime_quotes = lambda codes: ({
                "600000": {"code": "600000", "name": "测试股", "price": 10.25, "source": "test auction"}
            }, {"channel_counts": {"test": 1}, "errors": []})

            quote = trader.execution_quote("600000")
        finally:
            trader.is_a_share_auction_time = original_auction
            trader.fetch_realtime_quotes = original_fetch

        self.assertEqual(quote["price"], 10.25)
        self.assertEqual(quote["execution_price_source"], "auction_reference:test auction")

    def test_fixed_percentage_loss_does_not_trigger_exit(self):
        pos = {
            "qty": 1000,
            "avg_cost": 10.0,
            "last_price": 9.35,
            "buy_date_lots": {"2026-06-23": 1000},
        }
        signal = trader.evaluate_sell_signal("600000", pos, "2026-06-24")
        self.assertIsNone(signal)

    def test_legacy_fixed_percentage_fallback_does_not_trigger_exit(self):
        pos = {
            "qty": 1000,
            "avg_cost": 10.0,
            "last_price": 9.5,
            "shaofu_stop_price": 9.6,
            "shaofu_stop_source": "fallback_pct",
            "buy_date_lots": {"2026-06-23": 1000},
        }
        signal = trader.evaluate_sell_signal("600000", pos, "2026-06-24")
        self.assertIsNone(signal)

    def test_buy_strategy_classifier_drops_legacy_b1_aliases(self):
        self.assertEqual(trader.classify_buy_strategy("超级B1放量破位洗盘"), "super_b1")
        self.assertEqual(trader.classify_buy_strategy("少妇B1缩量回调"), "shaofu_b1")
        self.assertEqual(trader.classify_buy_strategy("巴菲特价值评分达标"), "unknown_buy")
        self.assertEqual(trader.classify_buy_strategy("李大霄低位企稳"), "li_daxiao_bottom")
        self.assertEqual(trader.classify_buy_strategy("李大霄底部低位企稳"), "li_daxiao_bottom")
        self.assertEqual(trader.classify_buy_strategy("中庸动量评分达标"), "unknown_buy")
        self.assertEqual(trader.classify_buy_strategy("高匹配B1评分达标"), "unknown_buy")
        self.assertEqual(trader.classify_buy_strategy("B1旧版买入"), "unknown_buy")
        self.assertEqual(trader.classify_buy_strategy(candidate={"best_strategy": "balanced_momentum"}), "unknown_buy")
        self.assertEqual(trader.classify_buy_strategy(candidate={"best_strategy": "legacy_b1"}), "unknown_buy")

    def test_preset_text_strategy_is_in_decision_prompt(self):
        saved_env = {
            trader.ACTIVE_STRATEGY_ENV: os.environ.get(trader.ACTIVE_STRATEGY_ENV),
            trader.STRATEGY_SOURCE_ENV: os.environ.get(trader.STRATEGY_SOURCE_ENV),
            trader.PERSONA_STRATEGY_ENV: os.environ.get(trader.PERSONA_STRATEGY_ENV),
            trader.PRESET_STRATEGY_TEXT_ENV: os.environ.get(trader.PRESET_STRATEGY_TEXT_ENV),
            trader.TRADE_DISCIPLINE_TEXT_ENV: os.environ.get(trader.TRADE_DISCIPLINE_TEXT_ENV),
        }
        originals = {
            "load_crossdesk_config": trader.load_crossdesk_config,
            "check_market_environment": trader.check_market_environment,
            "check_market_sentiment": trader.check_market_sentiment,
            "current_market_strategy_context": trader.current_market_strategy_context,
            "check_candidate_news_precheck": trader.check_candidate_news_precheck,
            "request_chat_content": trader.request_chat_content,
        }
        captured: dict[str, dict] = {}
        try:
            os.environ[trader.ACTIVE_STRATEGY_ENV] = "preset_text"
            os.environ[trader.PERSONA_STRATEGY_ENV] = "li_daxiao_bottom"
            os.environ[trader.PRESET_STRATEGY_TEXT_ENV] = "只做主线强趋势回踩\\n跌破5日线离场"
            os.environ.pop(trader.TRADE_DISCIPLINE_TEXT_ENV, None)
            trader.load_crossdesk_config = lambda *args, **kwargs: ("https://decision.example/v1", "key")
            trader.check_market_environment = lambda: {"bullish": True, "detail": "test"}
            trader.check_market_sentiment = lambda: {"sentiment": "neutral", "detail": "test", "hot_sectors": []}
            trader.current_market_strategy_context = lambda now=None: {"enabled": False}
            trader.check_candidate_news_precheck = lambda candidates: ""

            def fake_request(base_url, api_key, payload, model_name, max_retries=3, timeout=60):
                captured["payload"] = payload
                return '{"summary":"ok","actions":[]}'

            trader.request_chat_content = fake_request
            result = trader.call_model_decision(
                [{
                    "code": "600000",
                    "name": "测试股",
                    "price": 10.0,
                    "change_pct": 1.2,
                    "best_strategy": "trend_pullback",
                    "best_score": 8.5,
                    "score_total": 10,
                    "entry_threshold": 8.0,
                    "score_basis": "趋势回踩",
                    "position_hint": "低吸仓",
                    "time_stop": "跌破支撑走",
                    "consensus_count": 1,
                    "distance_pct": 1.0,
                    "hard_blockers": [],
                    "risk_flags": [],
                }],
                {"positions": [], "trade_log": [], "cash": 1000000, "total_equity": 1000000},
                True,
                "测试交易时段",
            )
        finally:
            for name, value in originals.items():
                setattr(trader, name, value)
            for name, value in saved_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        prompt = captured["payload"]["messages"][0]["content"]
        self.assertEqual(result["summary"], "ok")
        self.assertNotIn("temperature", captured["payload"])
        self.assertIn("当前激活策略：预设文字策略", prompt)
        self.assertIn("系统底线风控", prompt)
        self.assertNotIn("-4%硬止损", prompt)
        self.assertNotIn("止损-4%", prompt)
        self.assertNotIn("Z哥评分基准", prompt)
        self.assertIn("预设文字策略（当前激活）", prompt)
        self.assertIn("用户原文：\n只做主线强趋势回踩\n跌破5日线离场", prompt)
        self.assertIn("先将用户原文分析并优化成清晰的选股条件", prompt)
        self.assertIn("其他策略不得影响本轮新增仓判断", prompt)
        self.assertIn("基础扫描结果只作为原始候选池", prompt)
        self.assertIn("不得引用、混合或补充其他未启用策略", prompt)
        self.assertIn("每条 BUY/SELL 的仓位大小由你决定", prompt)
        self.assertIn("参考价或成交价 × shares ÷ 当前总权益 × 100%", prompt)
        self.assertIn("执行层不会替你补默认仓位", prompt)
        self.assertIn("不会替你补默认仓位或自动缩量", prompt)
        self.assertIn("首次建仓、加仓、减仓比例由评分、风险标记、盘面级别和账户状态决定", prompt)
        self.assertNotIn("单票仓位 ≤ 总资金15%", prompt)
        self.assertNotIn("总资金15%", prompt)
        self.assertNotIn("单票≤", prompt)
        self.assertNotIn("总仓≤", prompt)
        self.assertNotIn("现金≥", prompt)
        self.assertIn('"target_position_pct"', prompt)

    def test_held_candidate_add_rule_is_in_decision_prompt(self):
        saved_env = {
            trader.STRATEGY_SOURCE_ENV: os.environ.get(trader.STRATEGY_SOURCE_ENV),
            trader.PERSONA_STRATEGY_ENV: os.environ.get(trader.PERSONA_STRATEGY_ENV),
            trader.TRADE_DISCIPLINE_TEXT_ENV: os.environ.get(trader.TRADE_DISCIPLINE_TEXT_ENV),
        }
        originals = {
            "load_crossdesk_config": trader.load_crossdesk_config,
            "check_market_environment": trader.check_market_environment,
            "check_market_sentiment": trader.check_market_sentiment,
            "current_market_strategy_context": trader.current_market_strategy_context,
            "check_candidate_news_precheck": trader.check_candidate_news_precheck,
            "request_chat_content": trader.request_chat_content,
        }
        captured: dict[str, dict] = {}
        try:
            os.environ[trader.STRATEGY_SOURCE_ENV] = "builtin"
            os.environ[trader.PERSONA_STRATEGY_ENV] = "zettaranc"
            os.environ.pop(trader.TRADE_DISCIPLINE_TEXT_ENV, None)
            trader.load_crossdesk_config = lambda *args, **kwargs: ("https://decision.example/v1", "key")
            trader.check_market_environment = lambda: {"bullish": True, "detail": "test"}
            trader.check_market_sentiment = lambda: {"sentiment": "neutral", "detail": "test", "hot_sectors": []}
            trader.current_market_strategy_context = lambda now=None: {**permissive_market_context(), "enabled": True}
            trader.check_candidate_news_precheck = lambda candidates: ""

            def fake_request(base_url, api_key, payload, model_name, max_retries=3, timeout=60):
                captured["payload"] = payload
                return '{"summary":"ok","actions":[]}'

            trader.request_chat_content = fake_request
            trader.call_model_decision(
                [{
                    "code": "600000",
                    "name": "测试股",
                    "price": 10.5,
                    "change_pct": 2.0,
                    "best_strategy": "b3_accelerate",
                    "best_score": 10.0,
                    "score_total": 10,
                    "entry_threshold": 8.5,
                    "score_basis": "确定性最高",
                    "position_hint": "快进快出",
                    "time_stop": "次日不涨走",
                    "consensus_count": 1,
                    "distance_pct": 1.2,
                    "hard_blockers": [],
                    "risk_flags": [],
                }],
                {
                    "positions": [{
                        "code": "600000",
                        "name": "测试股",
                        "qty": 1000,
                        "position_pct": 10.5,
                        "pnl_pct": 4.2,
                        "today_pnl_pct": 2.0,
                        "buy_strategy": "b3_accelerate",
                    }],
                    "trade_log": [],
                    "cash": 895000,
                    "total_equity": 1000000,
                },
                True,
                "测试交易时段",
            )
        finally:
            for name, value in originals.items():
                setattr(trader, name, value)
            for name, value in saved_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        prompt = captured["payload"]["messages"][0]["content"]
        self.assertIn("当前持仓与候选池重合", prompt)
        self.assertIn("600000 测试股 当前仓位10.5%", prompt)
        self.assertIn("对当前账户JSON里已有持仓输出 BUY，表示加仓/补仓", prompt)
        self.assertIn("shares 是本次新增股数，不是目标总股数", prompt)
        self.assertIn("不得为了摊低成本而加仓", prompt)

    def test_custom_trade_discipline_text_is_in_decision_prompt(self):
        saved_env = {
            trader.STRATEGY_SOURCE_ENV: os.environ.get(trader.STRATEGY_SOURCE_ENV),
            trader.PERSONA_STRATEGY_ENV: os.environ.get(trader.PERSONA_STRATEGY_ENV),
            trader.TRADE_DISCIPLINE_TEXT_ENV: os.environ.get(trader.TRADE_DISCIPLINE_TEXT_ENV),
        }
        originals = {
            "load_crossdesk_config": trader.load_crossdesk_config,
            "check_market_environment": trader.check_market_environment,
            "check_market_sentiment": trader.check_market_sentiment,
            "current_market_strategy_context": trader.current_market_strategy_context,
            "check_candidate_news_precheck": trader.check_candidate_news_precheck,
            "request_chat_content": trader.request_chat_content,
        }
        captured: dict[str, dict] = {}
        try:
            os.environ[trader.STRATEGY_SOURCE_ENV] = "builtin"
            os.environ[trader.PERSONA_STRATEGY_ENV] = "zettaranc"
            os.environ[trader.TRADE_DISCIPLINE_TEXT_ENV] = "自定义纪律：只在高确定性时开仓\\n重仓必须说明集中理由"
            trader.load_crossdesk_config = lambda *args, **kwargs: ("https://decision.example/v1", "key")
            trader.check_market_environment = lambda: {"bullish": True, "detail": "test"}
            trader.check_market_sentiment = lambda: {"sentiment": "neutral", "detail": "test", "hot_sectors": []}
            trader.current_market_strategy_context = lambda now=None: {"enabled": False}
            trader.check_candidate_news_precheck = lambda candidates: ""

            def fake_request(base_url, api_key, payload, model_name, max_retries=3, timeout=60):
                captured["payload"] = payload
                return '{"summary":"ok","actions":[]}'

            trader.request_chat_content = fake_request
            result = trader.call_model_decision(
                [],
                {"positions": [], "trade_log": [], "cash": 1000000, "total_equity": 1000000},
                True,
                "测试交易时段",
            )
        finally:
            for name, value in originals.items():
                setattr(trader, name, value)
            for name, value in saved_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        prompt = captured["payload"]["messages"][0]["content"]
        self.assertEqual(result["summary"], "ok")
        self.assertIn("自定义纪律：只在高确定性时开仓\n重仓必须说明集中理由", prompt)
        self.assertNotIn("单次决策最多给2条新买入", prompt)
        self.assertNotIn("当前持仓达到", prompt)

    def test_b3_next_day_no_progress_exits(self):
        pos = {
            "qty": 1000,
            "avg_cost": 10.0,
            "last_price": 9.98,
            "buy_strategy": "b3_accelerate",
            "buy_date_lots": {"2026-06-23": 1000},
        }
        signal = trader.evaluate_sell_signal("600000", pos, "2026-06-24")
        self.assertIsNotNone(signal)
        self.assertEqual(signal["signal"], "b3_next_day_no_progress")

    def test_b3_time_exit_uses_open_check_gate(self):
        pos = {
            "qty": 1000,
            "avg_cost": 10.0,
            "last_price": 9.98,
            "buy_strategy": "b3_accelerate",
            "buy_date_lots": {"2026-06-23": 1000},
        }

        self.assertFalse(trader.is_b3_exit_check_time(datetime(2026, 6, 24, 9, 29)))
        self.assertFalse(trader.is_b3_exit_check_time(datetime(2026, 6, 24, 9, 30)))
        self.assertTrue(trader.is_b3_exit_check_time(datetime(2026, 6, 24, 9, 37)))
        self.assertFalse(trader.is_b3_exit_check_time(datetime(2026, 6, 24, 14, 45)))

        blocked = trader.evaluate_sell_signal(
            "600000",
            dict(pos),
            "2026-06-24",
            time_exit_allowed=True,
            b3_exit_allowed=False,
        )
        self.assertIsNone(blocked)

        opened = trader.evaluate_sell_signal(
            "600000",
            dict(pos),
            "2026-06-24",
            time_exit_allowed=False,
            b3_exit_allowed=True,
        )
        self.assertIsNotNone(opened)
        self.assertEqual(opened["signal"], "b3_next_day_no_progress")
        self.assertIn("09:37", opened["reason"])
        self.assertIn("开盘", opened["reason"])

    def test_b2_two_day_no_follow_through_exits(self):
        pos = {
            "qty": 1000,
            "avg_cost": 10.0,
            "last_price": 10.02,
            "max_pnl_pct": 1.0,
            "buy_strategy": "b2_confirm",
            "buy_date_lots": {"2026-06-22": 1000},
        }
        signal = trader.evaluate_sell_signal("600000", pos, "2026-06-24")
        self.assertIsNotNone(signal)
        self.assertEqual(signal["signal"], "b2_no_follow_through")

    def test_time_exit_rules_wait_for_tail_check_gate(self):
        cases = [
            (
                {
                    "qty": 1000,
                    "avg_cost": 10.0,
                    "last_price": 10.02,
                    "max_pnl_pct": 1.0,
                    "buy_strategy": "b2_confirm",
                    "buy_date_lots": {"2026-06-22": 1000},
                },
                "2026-06-24",
                "b2_no_follow_through",
            ),
            (
                {
                    "qty": 1000,
                    "avg_cost": 10.0,
                    "last_price": 10.02,
                    "max_pnl_pct": 0.5,
                    "buy_strategy": "super_b1",
                    "buy_date_lots": {"2026-06-21": 1000},
                },
                "2026-06-24",
                "super_b1_no_progress",
            ),
        ]

        for pos, today, expected_signal in cases:
            with self.subTest(expected_signal=expected_signal):
                early = trader.evaluate_sell_signal(
                    "600000",
                    dict(pos),
                    today,
                    time_exit_allowed=False,
                )
                self.assertIsNone(early)

                tail = trader.evaluate_sell_signal(
                    "600000",
                    dict(pos),
                    today,
                    time_exit_allowed=True,
                )
                self.assertIsNotNone(tail)
                self.assertEqual(tail["signal"], expected_signal)
                self.assertIn("14:45", tail["reason"])

    def test_auto_exit_b3_triggers_at_open_and_tail_rules_at_1445(self):
        def make_b3_state():
            return {
                "cash": 0.0,
                "positions": {
                    "600000": {
                        "code": "600000",
                        "name": "测试股",
                        "qty": 1000,
                        "avg_cost": 10.0,
                        "last_price": 9.98,
                        "buy_strategy": "b3_accelerate",
                        "buy_date_lots": {"2026-06-23": 1000},
                    }
                },
                "trade_log": [],
                "decision_log": [],
            }

        state = make_b3_state()
        before_check = trader.check_auto_exits(state, datetime(2026, 6, 24, 9, 30))
        self.assertEqual(before_check, [])

        state = make_b3_state()
        at_open = trader.check_auto_exits(state, datetime(2026, 6, 24, 9, 37))
        self.assertEqual(len(at_open), 1)
        self.assertEqual(at_open[0]["exit_signal"], "b3_next_day_no_progress")
        self.assertIn("09:37", at_open[0]["reason"])

        state = make_b3_state()
        at_tail = trader.check_auto_exits(state, datetime(2026, 6, 24, 14, 45))
        self.assertEqual(at_tail, [])
        self.assertEqual(state["positions"]["600000"]["qty"], 1000)

        def make_b2_state():
            return {
                "cash": 0.0,
                "positions": {
                    "600000": {
                        "code": "600000",
                        "name": "测试股",
                        "qty": 1000,
                        "avg_cost": 10.0,
                        "last_price": 10.02,
                        "max_pnl_pct": 1.0,
                        "buy_strategy": "b2_confirm",
                        "buy_date_lots": {"2026-06-22": 1000},
                    }
                },
                "trade_log": [],
                "decision_log": [],
            }

        state = make_b2_state()
        before_tail = trader.check_auto_exits(state, datetime(2026, 6, 24, 14, 44))
        self.assertEqual(before_tail, [])
        self.assertEqual(state["positions"]["600000"]["qty"], 1000)

        state = make_b2_state()
        at_tail = trader.check_auto_exits(state, datetime(2026, 6, 24, 14, 45))
        self.assertEqual(len(at_tail), 1)
        self.assertEqual(at_tail[0]["exit_signal"], "b2_no_follow_through")
        self.assertIn("14:45", at_tail[0]["reason"])

    def test_partial_profit_does_not_auto_sell_rest_at_same_band(self):
        state = {
            "cash": 0.0,
            "positions": {
                "600000": {
                    "code": "600000",
                    "name": "测试股",
                    "qty": 1000,
                    "avg_cost": 10.0,
                    "last_price": 10.9,
                    "buy_date_lots": {"2026-06-23": 1000},
                }
            },
            "trade_log": [],
            "decision_log": [],
        }

        trade_dt = datetime(2026, 6, 24, 10, 0)
        first = trader.check_auto_exits(state, trade_dt)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["exit_signal"], "partial_take_profit")
        self.assertEqual(first[0]["shares"], 500)
        self.assertEqual(state["positions"]["600000"]["qty"], 500)

        second = trader.check_auto_exits(state, trade_dt)
        self.assertEqual(second, [])
        self.assertEqual(state["positions"]["600000"]["qty"], 500)

    def test_auto_exit_skips_outside_trading_time(self):
        state = {
            "cash": 0.0,
            "positions": {
                "600000": {
                    "code": "600000",
                    "name": "测试股",
                    "qty": 1000,
                    "avg_cost": 10.0,
                    "last_price": 9.3,
                    "buy_date_lots": {"2026-06-23": 1000},
                }
            },
            "trade_log": [],
            "decision_log": [],
        }

        executed = trader.check_auto_exits(state, datetime(2026, 6, 24, 2, 27, 22))
        self.assertEqual(executed, [])
        self.assertEqual(state["positions"]["600000"]["qty"], 1000)

    def test_auto_exit_skips_opening_auction_and_static_period(self):
        def make_state():
            return {
                "cash": 0.0,
                "positions": {
                    "600000": {
                        "code": "600000",
                        "name": "测试股",
                        "qty": 1000,
                        "avg_cost": 10.0,
                        "last_price": 9.3,
                        "buy_date_lots": {"2026-06-23": 1000},
                    }
                },
                "trade_log": [],
                "decision_log": [],
            }

        state = make_state()
        executed = trader.check_auto_exits(state, datetime(2026, 6, 24, 9, 15, 20))
        self.assertEqual(executed, [])
        self.assertEqual(state["positions"]["600000"]["qty"], 1000)

        state = make_state()
        executed = trader.check_auto_exits(state, datetime(2026, 6, 24, 9, 25, 20))
        self.assertEqual(executed, [])
        self.assertEqual(state["positions"]["600000"]["qty"], 1000)

        state = make_state()
        executed = trader.check_auto_exits(state, datetime(2026, 6, 24, 9, 30))
        self.assertEqual(executed, [])
        self.assertEqual(state["positions"]["600000"]["qty"], 1000)

    def test_dashboard_payload_does_not_trigger_auto_exits(self):
        state = {
            "cash": 100000.0,
            "positions": {
                "600000": {
                    "code": "600000",
                    "name": "测试股",
                    "qty": 1000,
                    "avg_cost": 10.0,
                    "last_price": 9.3,
                    "buy_date_lots": {"2026-06-23": 1000},
                }
            },
            "trade_log": [],
            "decision_log": [],
            "equity_history": [],
            "daily_equity_history": [],
        }

        originals = {
            "load_state": trader.load_state,
            "save_state": trader.save_state,
            "refresh_realtime_prices": trader.refresh_realtime_prices,
            "refresh_position_intraday": trader.refresh_position_intraday,
            "_refresh_position_bbi": trader._refresh_position_bbi,
            "record_equity": trader.record_equity,
            "check_auto_exits": trader.check_auto_exits,
            "check_market_environment": trader.check_market_environment,
            "check_market_sentiment": trader.check_market_sentiment,
        }
        try:
            trader.load_state = lambda: state
            trader.save_state = lambda _state: None
            trader.refresh_realtime_prices = lambda _state: {}
            trader.refresh_position_intraday = lambda _state: {}
            trader._refresh_position_bbi = lambda _state: None
            trader.record_equity = lambda _state: None
            trader.check_market_environment = lambda: {"bullish": True, "detail": "test"}
            trader.check_market_sentiment = lambda: {"sentiment": "neutral", "detail": "test"}

            def fail_auto_exit(_state, dt=None):
                raise AssertionError("dashboard payload must not execute trades")

            trader.check_auto_exits = fail_auto_exit
            payload = trader.get_dashboard_payload()
        finally:
            for name, value in originals.items():
                setattr(trader, name, value)

        self.assertEqual(payload["cash"], 100000.0)
        self.assertEqual(state["trade_log"], [])
        self.assertEqual(state["positions"]["600000"]["qty"], 1000)

    def test_intraday_curve_rebuild_skips_days_with_trades(self):
        state = {
            "cash": 90000.0,
            "positions": {
                "600000": {
                    "code": "600000",
                    "qty": 1000,
                    "avg_cost": 10.0,
                    "last_price": 10.2,
                    "intraday": {"points": [
                        {"time": "09:30", "minute": 0, "price": 10.0},
                        {"time": "09:31", "minute": 1, "price": 10.1},
                    ]},
                }
            },
            "trade_log": [{"time": "2026-06-24 09:31:00", "action": "BUY", "code": "600000"}],
            "equity_history": [{"time": "2026-06-24 09:30:00", "equity": 100000.0}],
        }

        rebuilt = trader.rebuild_intraday_equity_curve(state, today="2026-06-24")

        self.assertFalse(rebuilt)
        self.assertEqual(len(state["equity_history"]), 1)

    def test_intraday_curve_rebuild_appends_closing_point_after_latest_trade(self):
        existing_point = {
            "time": "2026-06-24 14:57:00",
            "equity": 100100.0,
            "cash": 90000.0,
            "market_value": 10100.0,
            "pnl_pct": 0.1,
        }
        state = {
            "cash": 90000.0,
            "initial_cash": 100000.0,
            "positions": {
                "600000": {
                    "code": "600000",
                    "qty": 1000,
                    "avg_cost": 10.0,
                    "last_price": 10.1,
                    "intraday": {"points": [
                        {"time": "14:57", "minute": 237, "price": 10.1},
                        {"time": "15:00", "minute": 240, "price": 10.3},
                    ]},
                }
            },
            "trade_log": [{"time": "2026-06-24 14:02:18", "action": "BUY", "code": "600000"}],
            "equity_history": [existing_point],
            "daily_equity_history": [existing_point],
        }

        rebuilt = trader.rebuild_intraday_equity_curve(
            state,
            today="2026-06-24",
            now=datetime(2026, 6, 24, 15, 1, 0),
        )

        self.assertTrue(rebuilt)
        self.assertEqual(
            [point["time"] for point in state["equity_history"]],
            ["2026-06-24 14:57:00", "2026-06-24 15:00:00"],
        )
        self.assertIs(state["equity_history"][0], existing_point)
        self.assertEqual(state["equity_history"][-1]["equity"], 100300.0)
        self.assertEqual(state["daily_equity_history"][-1]["time"], "2026-06-24 15:00:00")

        self.assertFalse(trader.rebuild_intraday_equity_curve(
            state,
            today="2026-06-24",
            now=datetime(2026, 6, 24, 15, 1, 0),
        ))
        self.assertEqual(len(state["equity_history"]), 2)

    def test_intraday_curve_rebuild_fills_post_trade_gaps_without_replacing_recorded_minutes(self):
        recorded_1457 = {
            "time": "2026-06-24 14:57:18",
            "equity": 100110.0,
            "cash": 90000.0,
            "market_value": 10110.0,
            "pnl_pct": 0.11,
        }
        recorded_close = {
            "time": "2026-06-24 15:00:00",
            "equity": 100300.0,
            "cash": 90000.0,
            "market_value": 10300.0,
            "pnl_pct": 0.3,
        }
        state = {
            "cash": 90000.0,
            "initial_cash": 100000.0,
            "positions": {
                "600000": {
                    "code": "600000",
                    "qty": 1000,
                    "avg_cost": 10.0,
                    "last_price": 10.3,
                    "intraday": {"points": [
                        {"time": "14:56", "minute": 236, "price": 10.0},
                        {"time": "14:57", "minute": 237, "price": 10.1},
                        {"time": "14:58", "minute": 238, "price": 10.2},
                        {"time": "14:59", "minute": 239, "price": 10.25},
                        {"time": "15:00", "minute": 240, "price": 10.3},
                    ]},
                }
            },
            "trade_log": [{"time": "2026-06-24 14:02:18", "action": "BUY", "code": "600000"}],
            "equity_history": [recorded_1457, recorded_close],
            "daily_equity_history": [recorded_close],
        }

        rebuilt = trader.rebuild_intraday_equity_curve(
            state,
            today="2026-06-24",
            now=datetime(2026, 6, 24, 15, 1, 0),
        )

        self.assertTrue(rebuilt)
        self.assertEqual(
            [point["time"] for point in state["equity_history"]],
            [
                "2026-06-24 14:56:00",
                "2026-06-24 14:57:18",
                "2026-06-24 14:58:00",
                "2026-06-24 14:59:00",
                "2026-06-24 15:00:00",
            ],
        )
        self.assertIs(state["equity_history"][1], recorded_1457)
        self.assertIs(state["equity_history"][-1], recorded_close)
        self.assertIs(state["daily_equity_history"][-1], recorded_close)

    def test_intraday_curve_rebuild_clamps_today_points_to_current_clock(self):
        state = {
            "cash": 90000.0,
            "initial_cash": 100000.0,
            "positions": {
                "600000": {
                    "code": "600000",
                    "qty": 1000,
                    "avg_cost": 10.0,
                    "last_price": 10.2,
                    "intraday": {"points": [
                        {"time": "09:30", "minute": 0, "price": 10.0},
                        {"time": "09:31", "minute": 1, "price": 10.1},
                        {"time": "15:00", "minute": 240, "price": 10.8},
                    ]},
                }
            },
            "trade_log": [],
            "equity_history": [],
            "daily_equity_history": [],
        }

        rebuilt = trader.rebuild_intraday_equity_curve(
            state,
            today="2026-06-24",
            now=datetime(2026, 6, 24, 9, 31, 30),
        )

        self.assertTrue(rebuilt)
        self.assertEqual(
            [p["time"] for p in state["equity_history"]],
            ["2026-06-24 09:30:00", "2026-06-24 09:31:00"],
        )
        self.assertEqual(state["daily_equity_history"][-1]["time"], "2026-06-24 09:31:00")

    def test_prune_future_intraday_equity_points_removes_today_future_points(self):
        state = {
            "equity_history": [
                {"time": "2026-06-25 15:00:00", "equity": 99000.0},
                {"time": "2026-06-26 09:39:00", "equity": 100000.0},
                {"time": "2026-06-26 15:00:00", "equity": 105000.0},
            ],
            "daily_equity_history": [
                {"time": "2026-06-25 15:00:00", "equity": 99000.0},
                {"time": "2026-06-26 15:00:00", "equity": 105000.0},
            ],
        }

        changed = trader.prune_future_intraday_equity_points(
            state,
            now=datetime(2026, 6, 26, 9, 39, 30),
        )

        self.assertTrue(changed)
        self.assertEqual(
            [p["time"] for p in state["equity_history"]],
            ["2026-06-25 15:00:00", "2026-06-26 09:39:00"],
        )
        self.assertEqual([p["time"] for p in state["daily_equity_history"]], ["2026-06-25 15:00:00"])

    def test_non_trading_day_equity_points_are_pruned(self):
        state = {
            "equity_history": [
                {"time": "2026-06-26 15:00:00", "equity": 100000.0},
                {"time": "2026-06-27 11:29:00", "equity": 100500.0},
            ],
            "daily_equity_history": [
                {"time": "2026-06-26 15:00:00", "equity": 100000.0},
                {"time": "2026-06-27 11:29:00", "equity": 100500.0},
            ],
        }

        changed = trader.prune_non_trading_day_equity_points(state)

        self.assertTrue(changed)
        self.assertEqual([p["time"] for p in state["equity_history"]], ["2026-06-26 15:00:00"])
        self.assertEqual([p["time"] for p in state["daily_equity_history"]], ["2026-06-26 15:00:00"])

    def test_intraday_curve_rebuild_skips_non_trading_day(self):
        state = {
            "cash": 1000.0,
            "initial_cash": 1000.0,
            "positions": {
                "600000": {
                    "code": "600000",
                    "qty": 100,
                    "avg_cost": 10.0,
                    "last_price": 10.0,
                    "intraday": {"points": [
                        {"time": "09:30", "minute": 0, "price": 10.0},
                        {"time": "11:29", "minute": 119, "price": 10.5},
                    ]},
                }
            },
            "trade_log": [],
            "equity_history": [{"time": "2026-06-27 11:29:00", "equity": 2050.0}],
            "daily_equity_history": [{"time": "2026-06-27 11:29:00", "equity": 2050.0}],
        }

        rebuilt = trader.rebuild_intraday_equity_curve(
            state,
            today="2026-06-27",
            now=datetime(2026, 6, 27, 11, 29, 0),
        )

        self.assertFalse(rebuilt)
        self.assertEqual(state["equity_history"], [])
        self.assertEqual(state["daily_equity_history"], [])

    def test_daily_equity_history_is_normalized_to_latest_point_per_day(self):
        state = {
            "daily_equity_history": [
                {"time": "2026-06-25 10:00:00", "equity": 99000.0},
                {"time": "2026-06-26 09:30:00", "equity": 100000.0},
                {"time": "2026-06-25 15:00:00", "equity": 101000.0},
                {"time": "2026-06-26 10:00:00", "equity": 100500.0},
            ],
        }

        changed = trader.normalize_daily_equity_history(state)

        self.assertTrue(changed)
        self.assertEqual(
            [(p["time"], p["equity"]) for p in state["daily_equity_history"]],
            [("2026-06-25 15:00:00", 101000.0), ("2026-06-26 10:00:00", 100500.0)],
        )

    def test_equity_history_is_sorted_by_time(self):
        state = {
            "equity_history": [
                {"time": "2026-06-26 10:00:00", "equity": 100500.0},
                {"time": "2026-06-25 15:00:00", "equity": 101000.0},
            ],
            "daily_equity_history": [
                {"time": "2026-06-26 10:00:00", "equity": 100500.0},
                {"time": "2026-06-25 15:00:00", "equity": 101000.0},
            ],
        }

        changed = trader.sort_equity_history(state)

        self.assertTrue(changed)
        self.assertEqual(
            [p["time"] for p in state["equity_history"]],
            ["2026-06-25 15:00:00", "2026-06-26 10:00:00"],
        )
        self.assertEqual(
            [p["time"] for p in state["daily_equity_history"]],
            ["2026-06-25 15:00:00", "2026-06-26 10:00:00"],
        )

    def test_today_sold_stocks_are_aggregated_with_quote_delta(self):
        original_fetch = trader.fetch_realtime_quotes
        try:
            trader.fetch_realtime_quotes = lambda codes: ({
                "600000": {
                    "code": "600000",
                    "name": "测试股",
                    "price": 10.5,
                    "change_pct": 2.0,
                    "quote_time": "2026-06-24 10:10:00",
                    "source": "test",
                }
            }, {"quote_time": "2026-06-24 10:10:00", "updated": 1})
            state = {
                "trade_log": [
                    {
                        "time": "2026-06-24 10:00:00",
                        "action": "SELL",
                        "code": "600000",
                        "name": "测试股",
                        "shares": 1000,
                        "price": 10.0,
                        "amount": 10000.0,
                        "net_proceeds": 9990.0,
                        "fee": 10.0,
                        "pnl": 990.0,
                        "reason": "止盈",
                        "exit_rule": "take_profit",
                        "buy_strategy": "b2_confirm",
                    },
                    {
                        "time": "2026-06-23 10:00:00",
                        "action": "SELL",
                        "code": "600001",
                        "shares": 1000,
                    },
                ]
            }

            rows = trader.refresh_today_sold_stocks(state, today="2026-06-24")
        finally:
            trader.fetch_realtime_quotes = original_fetch

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "600000")
        self.assertEqual(rows[0]["shares"], 1000)
        self.assertEqual(rows[0]["avg_sell_price"], 10.0)
        self.assertEqual(rows[0]["current_price"], 10.5)
        self.assertEqual(rows[0]["after_sell_pnl"], 500.0)
        self.assertIn("止盈", rows[0]["reason"])
        self.assertEqual(rows[0]["exit_rule"], "take_profit")
        self.assertEqual(rows[0]["exit_rules"], ["take_profit"])
        self.assertEqual(rows[0]["buy_strategy"], "b2_confirm")
        self.assertEqual(rows[0]["buy_strategies"], ["b2_confirm"])

    def test_profit_giveback_triggers_after_peak(self):
        pos = {
            "qty": 1000,
            "avg_cost": 10.0,
            "last_price": 10.55,
            "highest_price": 11.3,
            "max_pnl_pct": 13.0,
            "buy_date_lots": {"2026-06-01": 1000},
        }
        signal = trader.evaluate_sell_signal("600000", pos, "2026-06-24")
        self.assertIsNotNone(signal)
        self.assertEqual(signal["signal"], "profit_giveback")

    def test_s1_bbi_failure_after_two_days_below_bbi(self):
        pos = {
            "qty": 1000,
            "avg_cost": 10.0,
            "last_price": 9.85,
            "bbi": 10.0,
            "bbi_break_days": 1,
            "bbi_break_last_date": "2026-06-23",
            "buy_date_lots": {"2026-06-01": 1000},
        }
        signal = trader.evaluate_sell_signal("600000", pos, "2026-06-24")
        self.assertIsNotNone(signal)
        self.assertEqual(signal["signal"], "s1_bbi_failed")
        self.assertEqual(pos["bbi_break_days"], 2)

    def test_shaofu_entry_stop_uses_configured_stop_price(self):
        pos = {
            "qty": 1000,
            "avg_cost": 10.0,
            "last_price": 9.6,
            "shaofu_stop_price": 9.7,
            "buy_date_lots": {"2026-06-23": 1000},
        }
        signal = trader.evaluate_sell_signal("600000", pos, "2026-06-24")
        self.assertIsNotNone(signal)
        self.assertEqual(signal["signal"], "shaofu_entry_stop")

    def test_n_structure_stop_uses_latest_higher_swing_low(self):
        lows = [10.4, 10.0, 9.5, 9.8, 10.5, 10.2, 10.0, 10.3, 10.8, 10.6]
        rows = [
            {"date": f"2026-06-{idx + 1:02d}", "low": low, "close": low + 0.2}
            for idx, low in enumerate(lows)
        ]

        result = trader.find_n_structure_prior_low(rows, entry_idx=9)

        self.assertIsNotNone(result)
        self.assertEqual(result["price"], 10.0)
        self.assertEqual(result["date"], "2026-06-07")
        self.assertEqual(result["previous_price"], 9.5)

    def test_n_structure_stop_rejects_lower_low_pattern(self):
        lows = [10.4, 10.0, 9.5, 9.8, 10.5, 9.4, 9.2, 9.5, 10.0, 9.8]
        rows = [
            {"date": f"2026-06-{idx + 1:02d}", "low": low, "close": low + 0.2}
            for idx, low in enumerate(lows)
        ]

        result = trader.find_n_structure_prior_low(rows, entry_idx=9)

        self.assertIsNone(result)

    def test_zettaranc_entry_strategies_use_distinct_stop_anchors(self):
        rows = [
            {"date": "2026-06-18", "open": 10.2, "close": 10.0, "low": 9.8, "volume": 100},
            {"date": "2026-06-19", "open": 10.0, "close": 9.8, "low": 9.5, "volume": 120},
            {"date": "2026-06-22", "open": 9.9, "close": 10.0, "low": 9.7, "volume": 90},
            {"date": "2026-06-23", "open": 10.0, "close": 10.6, "low": 9.9, "volume": 180},
            {"date": "2026-06-24", "open": 10.55, "close": 10.58, "low": 10.4, "volume": 100},
        ]

        b2_stop = trader.zettaranc_entry_stop(rows, 3, "b2_confirm")
        b3_stop = trader.zettaranc_entry_stop(rows, 4, "b3_accelerate")
        super_b1_stop = trader.zettaranc_entry_stop(rows, 3, "super_b1")

        self.assertEqual((b2_stop["source"], b2_stop["price"]), ("b1_low", 9.5))
        self.assertEqual((b3_stop["source"], b3_stop["price"]), ("b3_kline_low", 10.4))
        self.assertEqual((super_b1_stop["source"], super_b1_stop["price"]), ("super_b1_washout_low", 9.5))

    def test_zettaranc_intraday_daily_bar_is_not_a_confirmed_close(self):
        rows = [
            {"date": "2026-07-10", "close": 10.2},
            {"date": "2026-07-13", "close": 9.5},
        ]

        intraday = trader.zettaranc_confirmed_rows(rows, datetime(2026, 7, 13, 9, 37))
        after_close = trader.zettaranc_confirmed_rows(rows, datetime(2026, 7, 13, 15, 0))

        self.assertEqual([row["date"] for row in intraday], ["2026-07-10"])
        self.assertEqual(after_close, rows)

    def test_zettaranc_position_skips_fixed_percent_take_profit(self):
        pos = {
            "qty": 1000,
            "avg_cost": 10.0,
            "last_price": 11.3,
            "confirmed_close": 11.3,
            "buy_strategy": "shaofu_b1",
            "buy_date_lots": {"2026-06-23": 1000},
        }

        signal = trader.evaluate_sell_signal(
            "600000", pos, "2026-06-24", time_exit_allowed=False, b3_exit_allowed=False
        )

        self.assertIsNone(signal)

    def test_sell_score_three_reduces_half_once(self):
        pos = {
            "qty": 1000,
            "avg_cost": 10.0,
            "last_price": 10.2,
            "sell_score": 3,
            "sell_score_reason": "中性",
            "buy_date_lots": {"2026-06-23": 1000},
        }
        signal = trader.evaluate_sell_signal("600000", pos, "2026-06-24")
        self.assertIsNotNone(signal)
        self.assertEqual(signal["signal"], "sell_score_reduce")
        self.assertEqual(signal["sell_ratio"], trader.TAKE_PROFIT_PARTIAL_RATIO)

    def test_luzhu_signal_reduces_half(self):
        pos = {
            "qty": 1000,
            "avg_cost": 10.0,
            "last_price": 10.4,
            "luzhu_half_signal": True,
            "buy_date_lots": {"2026-06-23": 1000},
        }
        signal = trader.evaluate_sell_signal("600000", pos, "2026-06-24")
        self.assertIsNotNone(signal)
        self.assertEqual(signal["signal"], "luzhu_half")
        self.assertEqual(signal["sell_ratio"], trader.TAKE_PROFIT_PARTIAL_RATIO)

    def test_chuhuo_wushi_detects_big_volume_distribution(self):
        rows = []
        close = 10.0
        for i in range(19):
            close *= 1.012
            rows.append({
                "date": f"2026-06-{i+1:02d}",
                "open": close * 0.99,
                "close": close,
                "high": close * 1.02,
                "low": close * 0.98,
                "volume": 1000 + i * 10,
            })
        rows.append({
            "date": "2026-06-20",
            "open": close * 1.04,
            "close": close * 0.96,
            "high": close * 1.05,
            "low": close * 0.95,
            "volume": 5000,
        })
        chuhuo = trader._detect_chuhuo_wushi(rows)
        self.assertTrue(chuhuo["is_selling"])


if __name__ == "__main__":
    unittest.main()
