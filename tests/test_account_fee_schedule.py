from __future__ import annotations

import json
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "app" / "compat")]
from trading.fees import (  # noqa: E402
    FEE_PROFILE_ENV, LEGACY_FEE_SCHEDULE, RETAIL_FEE_SCHEDULE,
    account_fee_schedule, calculate_a_share_trade_fees, fee_schedule_for_profile,
)
import niuniu_practice_trader as trader  # noqa: E402


class AccountFeeTests(unittest.TestCase):
    def test_retail_minimum_applies_each_side_and_zero_order_is_free(self):
        for gross, buy, sell in [(1000, 5.01, 5.51), (3000, 5.03, 6.53), (10000, 5.1, 10.1)]:
            self.assertEqual(calculate_a_share_trade_fees(gross, "BUY", RETAIL_FEE_SCHEDULE)["total_fee"], buy)
            self.assertEqual(calculate_a_share_trade_fees(gross, "SELL", RETAIL_FEE_SCHEDULE)["total_fee"], sell)
        self.assertEqual(calculate_a_share_trade_fees(0, "BUY", RETAIL_FEE_SCHEDULE)["total_fee"], 0)
        self.assertEqual(calculate_a_share_trade_fees(1000, "BUY")["total_fee"], .11)

    def test_profile_is_frozen_and_old_account_does_not_inherit_new_default(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {FEE_PROFILE_ENV: "retail_025_min5"}), patch.object(trader, "_reconcile_exit_feedback_policy_from_db"):
            path = Path(temp) / "state.json"
            old = {"created_at": "2026-01-01 00:00:00", "initial_cash": 10000, "cash": 10000}
            path.write_text(json.dumps(old), encoding="utf-8")
            with patch.object(trader, "STATE_FILE", path):
                for _ in range(2):
                    self.assertEqual(account_fee_schedule(trader.load_state()), LEGACY_FEE_SCHEDULE)
            self.assertEqual(account_fee_schedule(trader.default_state()), RETAIL_FEE_SCHEDULE)
        self.assertEqual(trader.calc_trade_fees(1000, "BUY", {"fee_schedule": RETAIL_FEE_SCHEDULE.to_dict()})["total_fee"], 5.01)

    def test_invalid_persisted_assumptions_fail_closed(self):
        for raw in ({}, {**RETAIL_FEE_SCHEDULE.to_dict(), "commission_rate": float("nan")}, {**RETAIL_FEE_SCHEDULE.to_dict(), "commission_min": -1}):
            with self.assertRaises(ValueError):
                account_fee_schedule({"fee_schedule": raw})
        with self.assertRaises(ValueError):
            fee_schedule_for_profile("unknown")

    def test_live_execution_and_automatic_exit_use_frozen_minimum_commission(self):
        from test_niuone_strategy import niu_candidate
        state = {"initial_cash": 10000, "cash": 10000, "positions": {}, "trade_log": [],
                 "fee_schedule": RETAIL_FEE_SCHEDULE.to_dict()}
        market = {"allow_new_buys": True, "max_open_positions": 6,
                  "max_new_buys_per_decision": 2, "max_total_position_pct": 80,
                  "min_cash_reserve_pct": 20}
        decision = {"actions": [{"action": "BUY", "code": "600000", "shares": 100,
                                  "reason": "牛牛领涨确认"}]}
        quote = {"turnover": 4, "price": 10, "name": "测试股", "source": "test"}
        with patch.object(trader, "is_a_share_execution_time", return_value=(True, "连续竞价交易时段")), patch.object(trader, "execution_quote", return_value=quote):
            bought = trader.execute_actions(state, decision, [niu_candidate()], True, "连续竞价交易时段", market)
            self.assertEqual(len(bought), 1, decision)
            self.assertEqual(bought[0]["fee"], 5.01)
            self.assertAlmostEqual(state["cash"], 8994.99)
            position = state["positions"]["600000"]
            position["buy_date_lots"] = {"2026-07-24": 100}
            position["last_price"] = position["entry_stop_price"] - .01
            position["close"] = position["last_price"]
            sold = trader.check_auto_exits(state, datetime(2026, 7, 27, 10))
        self.assertEqual(len(sold), 1)
        expected_fee = calculate_a_share_trade_fees(sold[0]["amount"], "SELL", RETAIL_FEE_SCHEDULE)["total_fee"]
        self.assertEqual(sold[0]["fee"], expected_fee)
        self.assertAlmostEqual(state["cash"], 8994.99 + sold[0]["amount"] - expected_fee)

    def test_new_settings_are_visible_validated_and_protocol_locked(self):
        import niuone_dashboard as dashboard
        from app.trading.niuone_forward_service import (
            PROTOCOL_DERIVED_RUNTIME_SETTING_NAMES, PROTOCOL_RUNTIME_SETTING_DEFAULTS,
        )
        self.assertEqual(len(set(PROTOCOL_RUNTIME_SETTING_DEFAULTS) | set(PROTOCOL_DERIVED_RUNTIME_SETTING_NAMES)), 59)
        for name in (FEE_PROFILE_ENV, "DASHBOARD_EXECUTION_PREFLIGHT_ENABLED"):
            self.assertIn(name, dashboard.ADMIN_VISIBLE_ENV_NAMES)
            self.assertIn(name, PROTOCOL_RUNTIME_SETTING_DEFAULTS)
        self.assertEqual(dashboard.normalize_env_update(FEE_PROFILE_ENV, "retail_025_min5", "text"), "retail_025_min5")
        with self.assertRaises(ValueError):
            dashboard.normalize_env_update(FEE_PROFILE_ENV, "free", "text")

    def test_fee_aware_lot_ceiling_uses_real_available_cash(self):
        kwargs = dict(price=10, total_equity=10000, cash=1002, current_position_value=0,
                      current_market_value=0, other_industry_value=0, dynamic_position_cap_pct=100,
                      total_position_cap_pct=100, sector_position_cap_pct=100,
                      effective_loss_distance_pct_value=5, max_open_risk_pct=5,
                      existing_open_risk_pct=0, max_sector_risk_pct=5,
                      existing_sector_risk_pct=0, required_cash_pct=0)
        self.assertEqual(trader.dynamic_risk_order_ceiling(**kwargs)["maximum_permitted_shares"], 100)
        result = trader.dynamic_risk_order_ceiling(**kwargs, fee_schedule=RETAIL_FEE_SCHEDULE)
        self.assertEqual(result["maximum_permitted_shares"], 0)
        self.assertIn("cash_after_fees", result["binding_constraints"])

    def test_capacity_preview_is_read_only_and_preserves_risk_limits(self):
        state = {"initial_cash": 10000, "cash": 10000, "positions": {},
                 "fee_schedule": RETAIL_FEE_SCHEDULE.to_dict()}
        candidate = {"code": "600000", "price": 50, "best_strategy": "niu_reversal_probe",
                     "signal_theme": "测试题材", "market_regime": "offensive", "market_allows_buys": True,
                     "stop_price": 49, "atr": 1}
        before = copy.deepcopy((state, candidate))
        rows = trader.preview_niuone_buy_capacity(state, [candidate], {})
        self.assertEqual(rows[0]["maximum_permitted_shares"], 0)
        self.assertEqual(rows[0]["one_lot_cash_required"], 5005.05)
        self.assertTrue(rows[0]["execution_recheck_required"])
        self.assertEqual((state, candidate), before)
        candidate["price"], candidate["stop_price"], candidate["atr"] = 5, 4.9, .1
        rows = trader.preview_niuone_buy_capacity(state, [candidate], {})
        self.assertGreater(rows[0]["maximum_permitted_shares"], 0)
        blocked = trader.preview_niuone_buy_capacity(state, [candidate], {"allow_new_buys": False})
        self.assertEqual(blocked[0]["maximum_permitted_shares"], 0)
        self.assertIn("market_guidance", blocked[0]["binding_constraints"])
        candidate["market_hard_stop"] = True
        rows = trader.preview_niuone_buy_capacity(state, [candidate], {})
        self.assertEqual(rows[0]["maximum_permitted_shares"], 0)
        self.assertIn("market_guidance", rows[0]["binding_constraints"])


if __name__ == "__main__":
    unittest.main()
