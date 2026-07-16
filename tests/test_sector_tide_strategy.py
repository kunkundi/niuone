#!/usr/bin/env python3
import sys
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
COMPAT = APP / "compat"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(COMPAT))

import niuniu_practice_trader as trader  # noqa: E402
from strategies.scoring import (  # noqa: E402
    analyze_enriched_rows,
    build_sector_tide_context,
    enrich_rows,
    score_tide_leader,
)
from strategies.scoring.common import with_strategy_profile  # noqa: E402
from strategies.selection import candidate_is_trade_ready  # noqa: E402
from strategies.sector_tide_risk import (  # noqa: E402
    downside_gap_buffer_pct,
    risk_sized_position_cap_pct,
    sector_tide_risk_budget,
)


def make_rows(code: str, industry: str, daily_step: float = 0.04) -> list[dict]:
    rows = []
    for index in range(65):
        close = 10.0 + index * daily_step
        rows.append({
            "date": f"2026-05-{index + 1:02d}",
            "open": close * 0.997,
            "close": close,
            "high": close * 1.008,
            "low": close * 0.992,
            "volume": 1000.0,
        })
    enrich_rows(rows)
    rows[-1]["symbol_code"] = code
    rows[-1]["stock_name"] = f"测试{code}"
    rows[-1]["industry"] = industry
    rows[-1]["quote_amount"] = 1.5e9
    return rows


def tide_candidate(**updates) -> dict:
    candidate = {
        "code": "600000",
        "name": "潮汐测试",
        "best_strategy": "tide_leader",
        "best_score": 9.0,
        "entry_threshold": 8.0,
        "actionable": True,
        "hard_blockers": [],
        "industry": "半导体",
        "sector": "半导体",
        "market_regime": "offensive",
        "market_score": 78.0,
        "market_hard_stop": False,
        "market_allows_buys": True,
        "sector_status": "leading",
        "sector_score": 82.0,
        "stock_sector_rank": 92.0,
        "distance_pct": 1.0,
        "stop_price": 9.5,
        "stop_source": "tide_structure_low",
        "stop_distance_pct": 5.0,
        "atr20": 0.3,
        "gap_buffer_pct": 1.0,
        "execution_buffer_pct": 0.2,
        "effective_loss_distance_pct": 6.2,
        "per_trade_risk_budget_pct": 0.3,
        "max_position_pct_by_risk": 4.8387,
    }
    candidate.update(updates)
    return candidate


class SectorTideStrategyTests(unittest.TestCase):
    def test_context_builds_one_cross_section_and_uses_volume_fallback(self):
        prepared = []
        for sector_index, industry in enumerate(("半导体", "银行", "汽车", "医药")):
            for member_index in range(3):
                code = f"{600000 + sector_index * 10 + member_index:06d}"
                rows = make_rows(code, industry, daily_step=0.06 - sector_index * 0.012)
                prepared.append({
                    "code": code,
                    "name": f"测试{code}",
                    "industry": industry,
                    "quote": {"amount": 1.5e9 + sector_index * 1e8},
                    "rows": rows,
                })

        context = build_sector_tide_context(
            prepared,
            market_snapshot={
                "up": 100,
                "down": 20,
                "median_change_pct": 1.0,
                "limit_up": 12,
                "limit_down": 0,
                "core_index_count": 3,
                "index_below_ma20_count": 0,
                "index_average_change_pct": 1.0,
            },
            flow_rows={"inflow": [], "outflow": []},
        )

        self.assertEqual(context["market"]["state"], "offensive")
        self.assertTrue(context["market"]["allow_new_buys"])
        self.assertEqual(context["market"]["per_trade_risk_pct"], 0.3)
        self.assertEqual(context["market"]["max_open_risk_pct"], 1.5)
        self.assertEqual(context["market"]["max_total_position_pct"], 45.0)
        self.assertEqual(context["market"]["max_sector_position_pct"], 12.0)
        self.assertEqual(context["sector_count"], 4)
        self.assertEqual(context["mapped_stock_count"], 12)
        self.assertEqual(context["data_coverage"], 1.0)
        self.assertTrue(all(row["eligible_data"] for row in context["sectors"].values()))
        self.assertTrue(all(row["flow_source"] == "volume_participation_fallback" for row in context["sectors"].values()))

    def test_tide_scorer_consumes_shared_context(self):
        rows = make_rows("600000", "半导体")
        context = {
            "market": {"state": "offensive", "score": 80, "hard_stop": False, "allow_new_buys": True},
            "sectors": {
                "半导体": {
                    "status": "leading", "score": 88, "member_count": 8, "eligible_data": True,
                    "relative_5d_pct": 3, "relative_20d_pct": 10, "rank_acceleration": 5,
                    "breadth20": 90, "flow_net_yi": None, "flow_source": "volume_participation_fallback",
                }
            },
            "stocks": {"600000": {"sector_relative_rank": 95, "market_relative_rank": 92}},
        }

        result = score_tide_leader(rows, context)

        self.assertIsNotNone(result)
        self.assertEqual(result["industry"], "半导体")
        self.assertEqual(result["market_regime"], "offensive")
        self.assertEqual(result["sector_status"], "leading")
        self.assertEqual(result["stock_sector_rank"], 95)
        self.assertEqual(result["stop_source"], "tide_structure_low")
        self.assertGreater(result["gap_buffer_pct"], 0)
        self.assertGreater(result["effective_loss_distance_pct"], result["stop_distance_pct"])
        self.assertEqual(result["per_trade_risk_budget_pct"], 0.3)
        self.assertLessEqual(result["max_position_pct_by_risk"], 8.0)

    def test_tide_hard_gates_do_not_mislabel_ema_distance_as_bbi(self):
        payload = with_strategy_profile("tide_leader", {
            "score": 9.0,
            "distance_pct": 10.0,
            "extension_atr": 1.0,
            "market_allows_buys": True,
            "market_hard_stop": False,
            "market_regime": "offensive",
            "sector_data_eligible": True,
            "sector_status": "leading",
            "stock_sector_rank": 90,
            "breakout": True,
            "pullback": False,
            "risk_ok": True,
            "effective_loss_distance_pct": 5.0,
            "max_position_pct_by_risk": 6.0,
            "risk_flags": [],
        })

        self.assertTrue(payload["actionable"])
        self.assertFalse(any("BBI" in blocker for blocker in payload["hard_blockers"]))
        self.assertTrue(candidate_is_trade_ready({**payload, "best_strategy": "tide_leader", "best_score": 9.0}))

    def test_execution_enforces_equity_risk_and_persists_tide_marks(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "潮汐测试", "source": "test"}
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {"actions": [{"action": "BUY", "code": "600000", "shares": 400, "reason": "主线领航确认"}]}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }

            executed = trader.execute_actions(state, decision, [tide_candidate()], True, "连续竞价交易时段", market)
            self.assertEqual(len(executed), 1)
            self.assertEqual(state["positions"]["600000"]["industry"], "半导体")
            self.assertEqual(state["positions"]["600000"]["entry_stop_price"], 9.5)
            self.assertEqual(state["positions"]["600000"]["risk_budget_regime"], "offensive")
            self.assertAlmostEqual(state["positions"]["600000"]["effective_loss_distance_pct"], 6.2)
            self.assertLessEqual(state["positions"]["600000"]["position_open_risk_pct"], 0.3)

            risk_state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            risk_decision = {"actions": [{"action": "BUY", "code": "600000", "shares": 600, "reason": "主线领航确认"}]}
            blocked = trader.execute_actions(risk_state, risk_decision, [tide_candidate()], True, "连续竞价交易时段", market)
            self.assertEqual(blocked, [])
            self.assertIn("风险预算动态上限", risk_decision["execution_blocked_reason"])
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_sector_weakness_counts_once_per_day_and_triggers_exit(self):
        state = {
            "positions": {
                "600000": {
                    "code": "600000", "name": "潮汐测试", "qty": 500,
                    "avg_cost": 10.0, "last_price": 10.2, "close": 10.2,
                    "buy_strategy": "tide_leader", "industry": "半导体",
                    "entry_stop_price": 9.5, "entry_stop_source": "tide_structure_low",
                    "buy_date_lots": {"2026-07-10": 500},
                }
            }
        }

        def payload(day: str) -> dict:
            return {
                "generated_at": f"{day} 14:30:00",
                "sector_tide_context": {
                    "market": {"state": "rotation", "score": 55, "hard_stop": False, "allow_new_buys": True},
                    "sectors": {"半导体": {"score": 50, "status": "lagging", "rank_acceleration": -10, "breadth20": 30}},
                    "stocks": {"600000": {"industry": "半导体", "sector_relative_rank": 20}},
                },
            }

        trader.sync_sector_tide_position_context(state, payload("2026-07-15"))
        trader.sync_sector_tide_position_context(state, payload("2026-07-15"))
        self.assertEqual(state["positions"]["600000"]["sector_weak_count"], 1)
        trader.sync_sector_tide_position_context(state, payload("2026-07-16"))
        self.assertEqual(state["positions"]["600000"]["sector_weak_count"], 2)

        signal = trader.evaluate_sell_signal("600000", state["positions"]["600000"], "2026-07-16", time_exit_allowed=False)
        self.assertEqual(signal["signal"], "tide_sector_weak")

    def test_tide_uses_two_r_partial_instead_of_fixed_profit_targets(self):
        pos = {
            "qty": 500,
            "avg_cost": 10.0,
            "last_price": 12.0,
            "close": 12.0,
            "buy_strategy": "tide_leader",
            "entry_stop_price": 9.0,
            "entry_stop_source": "tide_structure_low",
            "sector_score": 80,
            "sector_status": "leading",
            "sector_weak_count": 0,
            "buy_date_lots": {"2026-07-15": 500},
        }

        signal = trader.evaluate_sell_signal("600000", pos, "2026-07-16", time_exit_allowed=False)

        self.assertEqual(signal["signal"], "tide_2r_partial")
        self.assertEqual(signal["sell_ratio"], 0.5)

    def test_dynamic_budget_and_gap_buffer_math(self):
        self.assertEqual(sector_tide_risk_budget("offensive")["per_trade_risk_pct"], 0.3)
        self.assertEqual(sector_tide_risk_budget("rotation")["max_total_position_pct"], 30.0)
        self.assertEqual(sector_tide_risk_budget("recovery")["max_sector_position_pct"], 6.0)
        self.assertEqual(sector_tide_risk_budget("defensive")["max_open_risk_pct"], 0.0)
        self.assertAlmostEqual(
            risk_sized_position_cap_pct(
                per_trade_risk_pct=0.3,
                effective_loss_distance_pct_value=6.0,
                absolute_cap_pct=8.0,
            ),
            5.0,
        )
        rows = []
        prior_close = 10.0
        for index in range(30):
            open_price = prior_close * (0.97 if index >= 25 else 1.001)
            close = 10.0 + index * 0.01
            rows.append({"open": open_price, "close": close})
            prior_close = close
        self.assertGreaterEqual(downside_gap_buffer_pct(rows, atr=0.1, close=10.3), 2.9)

    def test_rotation_rejects_new_buy_when_strategy_open_risk_is_full(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "轮动测试", "source": "test"}
            positions = {}
            for index, industry in enumerate(("银行", "汽车", "医药")):
                code = f"60010{index}"
                positions[code] = {
                    "code": code,
                    "name": industry,
                    "qty": 500,
                    "avg_cost": 10.0,
                    "last_price": 10.0,
                    "buy_strategy": "tide_leader",
                    "industry": industry,
                    "entry_stop_price": 9.5,
                    "entry_atr20": 0.3,
                    "gap_buffer_pct": 1.0,
                    "execution_buffer_pct": 0.2,
                    "effective_loss_distance_pct": 6.2,
                    "buy_date_lots": {"2026-07-15": 500},
                }
            state = {"cash": 85000.0, "positions": positions, "trade_log": []}
            decision = {"actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "轮动初升确认"}]}
            candidate = tide_candidate(
                best_strategy="tide_rotation",
                market_regime="rotation",
                sector_status="improving",
                sector="电子",
                industry="电子",
                per_trade_risk_budget_pct=0.2,
            )
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }

            executed = trader.execute_actions(state, decision, [candidate], True, "连续竞价交易时段", market)

            self.assertEqual(executed, [])
            self.assertIn("策略内未实现止损风险", decision["execution_blocked_reason"])
            self.assertIn("0.80%", decision["execution_blocked_reason"])
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_rotation_rejects_buy_when_sector_risk_budget_is_full(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"price": 10.0, "name": "行业风险测试", "source": "test"}
            existing = {
                "600001": {
                    "code": "600001",
                    "name": "同行业持仓",
                    "qty": 400,
                    "avg_cost": 10.0,
                    "last_price": 10.0,
                    "buy_strategy": "tide_rotation",
                    "industry": "电子",
                    "entry_stop_price": 9.5,
                    "entry_atr20": 0.3,
                    "gap_buffer_pct": 1.0,
                    "execution_buffer_pct": 0.2,
                    "effective_loss_distance_pct": 6.2,
                    "buy_date_lots": {"2026-07-15": 400},
                }
            }
            state = {"cash": 96000.0, "positions": existing, "trade_log": []}
            decision = {"actions": [{"action": "BUY", "code": "600000", "shares": 300, "reason": "轮动初升确认"}]}
            candidate = tide_candidate(
                best_strategy="tide_rotation",
                market_regime="rotation",
                sector_status="improving",
                sector="电子",
                industry="电子",
                per_trade_risk_budget_pct=0.2,
            )
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }

            executed = trader.execute_actions(state, decision, [candidate], True, "连续竞价交易时段", market)

            self.assertEqual(executed, [])
            self.assertIn("电子行业买入后未实现止损风险", decision["execution_blocked_reason"])
            self.assertIn("0.40%", decision["execution_blocked_reason"])
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_scoring_to_buy_to_automatic_sell_runs_end_to_end(self):
        rows = make_rows("600000", "半导体")
        context = {
            "market": {"state": "offensive", "score": 80, "hard_stop": False, "allow_new_buys": True},
            "sectors": {
                "半导体": {
                    "status": "leading", "score": 88, "member_count": 8, "eligible_data": True,
                    "relative_5d_pct": 3, "relative_20d_pct": 10, "rank_acceleration": 5,
                    "breadth20": 90, "flow_net_yi": None, "flow_source": "volume_participation_fallback",
                }
            },
            "stocks": {"600000": {"sector_relative_rank": 95, "market_relative_rank": 92}},
        }
        multi = analyze_enriched_rows(rows, {"tide_leader": score_tide_leader}, context)
        self.assertIsNotNone(multi)
        self.assertEqual(multi["best_strategy"], "tide_leader")
        scored = multi["strategies"]["tide_leader"]
        self.assertIsNotNone(scored)
        self.assertTrue(scored["actionable"])
        candidate = {
            **scored,
            "code": "600000",
            "name": "潮汐全链路",
            "price": rows[-1]["close"],
            "best_strategy": multi["best_strategy"],
            "best_score": multi["best_score"],
        }
        self.assertTrue(candidate_is_trade_ready(candidate))
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {
                "price": rows[-1]["close"], "name": "潮汐全链路", "source": "test",
            }
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {"actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "主线领航确认"}]}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }

            bought = trader.execute_actions(state, decision, [candidate], True, "连续竞价交易时段", market)

            self.assertEqual(len(bought), 1)
            self.assertEqual(bought[0]["action"], "BUY")
            self.assertEqual(state["positions"]["600000"]["buy_strategy"], "tide_leader")
            position = state["positions"]["600000"]
            position["buy_date_lots"] = {"2026-07-15": 100}
            position["last_price"] = position["entry_stop_price"] - 0.01

            sold = trader.check_auto_exits(state, datetime(2026, 7, 16, 10, 0, 0))

            self.assertEqual(len(sold), 1)
            self.assertEqual(sold[0]["action"], "SELL")
            self.assertEqual(sold[0]["exit_signal"], "tide_structure_stop")
            self.assertNotIn("600000", state["positions"])
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote


if __name__ == "__main__":
    unittest.main()
