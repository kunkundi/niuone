#!/usr/bin/env python3
import json
import math
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
COMPAT = APP / "compat"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(COMPAT))

_TEST_HOME = tempfile.TemporaryDirectory(prefix="niuone-strategy-")
os.environ["DASHBOARD_HOME"] = _TEST_HOME.name

import niuniu_practice_trader as trader  # noqa: E402
import backtesting.niuone_exits as backtest_niuone_exits  # noqa: E402
from backtesting.selection import (  # noqa: E402
    SelectionCostModel,
    _compact_niuone_previous_context,
)
from strategies.exits import (  # noqa: E402
    NIUONE_BREAK_EVEN_AFTER_PARTIAL,
    NIUONE_CLIMAX_RUNNER_ENABLED,
    NIUONE_CLIMAX_RUNNER_LEADER_LOSS_CONFIRMATIONS,
    NIUONE_CLIMAX_RUNNER_TRAILING_ATR,
    NIUONE_LIFECYCLE_CLIMAX_PARTIAL_RATIO,
    NIUONE_PARTIAL_TAKE_PROFIT_R,
    NIUONE_LEADER_LOSS_CONFIRMATIONS,
    NIUONE_MAINLINE_WEAK_CONFIRMATIONS,
    NIUONE_MAX_HOLD_CALENDAR_DAYS,
    NIUONE_PARTIAL_TAKE_PROFIT_RATIO,
    NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_R,
    NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_RATIO,
    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES,
    NIUONE_REVERSAL_MAINLINE_WEAK_CONFIRMATIONS,
)
from strategies.niuone_risk import (  # noqa: E402
    NIUONE_ABSOLUTE_POSITION_CAP_PCT,
    NIUONE_MARKUP_MOMENTUM_PROBE_MAX_EXECUTION_GAP_PCT,
    NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MIN_SCORE,
    NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT,
    NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
    NIUONE_MARKUP_EARLY_UPGRADE_POSITION_CAP_PCT,
    NIUONE_MARKUP_REBALANCE_TRIM_RATIO,
    NIUONE_MARKUP_UPGRADE_MAX_PNL_PCT,
    NIUONE_MARKUP_UPGRADE_MIN_PNL_PCT,
    NIUONE_MARKUP_UPGRADE_POSITION_CAP_PCT,
    NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY,
    NIUONE_REPLACEMENT_PRIORITY_MARGIN,
    niuone_chase_limits,
    niuone_priority_is_higher,
    niuone_risk_budget,
    niuone_structural_stop_limits,
    niuone_structure_risk_ok,
)
from strategies.scoring import (  # noqa: E402
    analyze_enriched_rows,
    build_niuone_context,
    enrich_rows,
    score_niu_emerging,
    score_niu_leader,
    score_niu_pullback,
    score_niu_reversal_probe,
)
from strategies.scoring.common import with_strategy_profile  # noqa: E402
from strategies.scoring.common import (  # noqa: E402
    niu_reversal_entry_stage_blocker,
)
from strategies.scoring.niuone import (  # noqa: E402
    _apply_theme_attributions,
    _apply_markup_momentum_probe,
    _cohort_alignment_score,
    _cohort_alignment_score_reference,
    _daily_v_reversal_metrics,
    _peer_resonance_score,
    _peer_resonance_score_reference,
    _rank_theme_leaders,
    _shared_entry_metrics,
    _theme_peer_statistics,
)
from strategies.selection import candidate_is_trade_ready, select_trade_candidates  # noqa: E402
from trading.fees import (  # noqa: E402
    A_SHARE_COMMISSION_RATE,
    A_SHARE_MINIMUM_COMMISSION,
    A_SHARE_SELL_STAMP_DUTY_RATE,
    A_SHARE_TRANSFER_FEE_RATE,
)


def make_rows(code: str, industry: str, daily_step: float = 0.04) -> list[dict]:
    rows = []
    for index in range(65):
        close = 10.0 + index * daily_step
        rows.append({
            "date": f"2026-{index // 28 + 5:02d}-{index % 28 + 1:02d}",
            "open": close * 0.997,
            "close": close,
            "high": close * 1.008,
            "low": close * 0.992,
            "volume": 1000.0,
        })
    enrich_rows(rows)
    rows[-1].update({
        "quote_turnover": 4.0,
        "symbol_code": code,
        "stock_name": f"测试{code}",
        "industry": industry,
        "quote_amount": 1.5e9,
    })
    return rows


def make_daily_v_rows(code: str, industry: str) -> list[dict]:
    rows = make_rows(code, industry, 0.0)
    closes = (
        11.8, 11.6, 11.2, 10.8, 10.4, 10.0, 9.6, 9.2,
        9.4, 9.6, 9.9, 10.3, 10.5, 10.8,
    )
    for row, close in zip(rows[-len(closes):], closes):
        row.update({
            "open": close * 0.995,
            "close": close,
            "high": close * 1.012,
            "low": close * 0.988,
            "volume": 1200.0,
        })
    enrich_rows(rows)
    rows[-1].update({
        "quote_turnover": 4.0,
        "symbol_code": code,
        "stock_name": f"测试{code}",
        "industry": industry,
        "quote_amount": 1.5e9,
    })
    return rows


def niu_candidate(**updates) -> dict:
    candidate = {
        "turnover": 4.0,
        "code": "600000",
        "name": "牛牛测试",
        "best_strategy": "niu_leader",
        "best_score": 9.0,
        "entry_threshold": 8.0,
        "actionable": True,
        "hard_blockers": [],
        "industry": "半导体",
        "sector": "半导体",
        "signal_theme": "半导体",
        "theme_basis": "eastmoney_concept",
        "signal_theme_attribution_score": 86.0,
        "signal_theme_attribution_weight": 1.0,
        "signal_theme_historical_prior_score": 84.0,
        "signal_theme_cohort_alignment_score": 82.0,
        "signal_theme_peer_resonance_score": 88.0,
        "signal_theme_return_correlation_score": 90.0,
        "signal_theme_return_correlation_rank_score": 95.0,
        "signal_theme_return_correlation_observation_count": 20,
        "signal_theme_return_correlation_peer_count": 10,
        "signal_theme_specificity_score": 88.0,
        "signal_theme_membership_source": "eastmoney_concept",
        "unattributed_theme_weight": 0.0,
        "market_regime": "offensive",
        "market_score": 78.0,
        "market_hard_stop": False,
        "market_allows_buys": True,
        "mainline_state": "mainline",
        "mainline_score": 86.0,
        "sector_status": "mainline",
        "sector_score": 86.0,
        "strong_stock_count": 4,
        "effective_strong_count": 3.6,
        "leader_concentration": 0.3,
        "single_stock_dominated": False,
        "stock_strong": True,
        "stock_role": "leader",
        "stock_leader_rank": 1,
        "stock_leader_tier": True,
        "stock_strong_score": 92.0,
        "stock_activity_gate_required": True,
        "stock_activity_data_available": True,
        "stock_market_amount_percentile": 90.0,
        "stock_theme_amount_percentile": 75.0,
        "stock_volume_participation_percentile": 80.0,
        "stock_activity_score": 84.25,
        "stock_activity_confirmed": True,
        "stock_sector_rank": 95.0,
        "today_strength_score": 80.0,
        "distance_pct": 1.0,
        "stop_price": 9.5,
        "stop_source": "niu_structure_low",
        "stop_distance_pct": 5.0,
        "atr": 0.3,
        "atr_period": 14,
        "atr20": 0.3,
        "gap_buffer_pct": 1.0,
        "execution_buffer_pct": 0.2,
        "effective_loss_distance_pct": 6.2,
        "per_trade_risk_budget_pct": 1.5,
        "max_position_pct_by_risk": 24.1935,
    }
    candidate.update(updates)
    return candidate


def reversal_candidate(**updates) -> dict:
    candidate = niu_candidate(
        best_strategy="niu_reversal_probe",
        entry_threshold=7.6,
        mainline_state="candidate",
        sector_status="candidate",
        mainline_score=45.0,
        mainline_state_streak=1,
        mainline_cross_day_persistent=False,
        mainline_confirmed=False,
        niuone_lifecycle_stage="brewing",
        niuone_lifecycle_label="主线酝酿",
        niuone_lifecycle_order=10,
        niuone_lifecycle_entry_policy="probe_only",
        stock_strong=False,
        stock_role="follower",
        stock_leader_rank=4,
        stock_leader_tier=False,
        reversal_basis="daily_v",
        daily_v_reversal=True,
        daily_v_left_days=7,
        daily_v_right_days=5,
        daily_v_decline_pct=12.0,
        daily_v_rebound_pct=9.0,
        daily_v_recovery_ratio=0.7,
        daily_v_right_trend_confirmed=True,
        stock_reversal_strong=True,
        stock_reversal_leader_rank=1,
        stock_reversal_leader_tier=True,
        strong_stock_count=6,
        effective_strong_count=5.6,
        today_breadth_pct=75.0,
        stop_price=9.7,
        stop_source="niu_reversal_right_low",
        stop_distance_pct=3.0,
        atr=0.3,
        atr20=0.3,
        gap_buffer_pct=1.0,
        execution_buffer_pct=0.2,
        effective_loss_distance_pct=4.2,
        per_trade_risk_budget_pct=0.35,
        max_position_pct_by_risk=8.3333,
    )
    candidate.update(updates)
    return candidate


class NiuOneStrategyTests(unittest.TestCase):
    def test_weak_theme_probe_cannot_pass_selection_or_execution_with_high_score(self):
        candidate = reversal_candidate(
            best_score=9.8,
            signal_theme_attribution_score=22.0,
            signal_theme_attribution_weight=0.008,
            theme_attribution_confident=False,
        )
        self.assertFalse(candidate_is_trade_ready(candidate))
        self.assertTrue(any("题材归因" in reason for reason in trader.candidate_buy_blockers(candidate)))
        state = {"cash": 100000.0, "positions": {}, "trade_log": []}
        with patch.object(trader, "is_a_share_execution_time", return_value=(True, "test")), \
                patch.object(trader, "execution_quote", return_value={
                    "price": 10.0, "prev_close": 10.0, "turnover": 4.0, "source": "test",
                }):
            fills = trader.execute_actions(
                state, {"actions": [{"action": "BUY", "code": "600000", "shares": 100}]},
                [candidate], True, "test", {}, evaluated_at=datetime(2026, 6, 24, 10, 0),
            )
        self.assertEqual(fills, [])
        self.assertEqual(state["positions"], {})
        self.assertEqual(state["cash"], 100000.0)

    def test_compact_stock_context_fails_closed_without_strong_score(self):
        rows = make_rows("600000", "半导体", 0.02)
        context = {
            "market": {
                "state": "offensive",
                "score": 78,
                "hard_stop": False,
                "allow_new_buys": True,
            },
            "themes": {
                "半导体": {
                    "state": "mainline",
                    "score": 82.0,
                    "cross_day_confirmed": True,
                    "mainline_confirmed": True,
                },
            },
            "stocks": {
                "600000": {
                    "theme_attributions": [{
                        "theme": "半导体",
                        "attribution_score": 88.0,
                    }],
                },
            },
        }

        result = score_niu_leader(rows, context)

        self.assertIsNone(result)

    def test_candidate_evidence_distinguishes_same_code_observations(self):
        first = {
            "code": "600000",
            "best_strategy": "other_strategy",
            "best_score": 9.0,
        }
        filtered_duplicate = {
            "code": "600000",
            "best_strategy": "other_strategy",
            "best_score": 3.0,
        }
        originals = {
            "candidate_in_stock_universe": trader.candidate_in_stock_universe,
            "candidate_matches_active_strategy": trader.candidate_matches_active_strategy,
            "candidate_buy_blockers": trader.candidate_buy_blockers,
        }
        try:
            trader.candidate_in_stock_universe = lambda _row: True
            trader.candidate_matches_active_strategy = lambda _row: True
            trader.candidate_buy_blockers = lambda _row: []
            evidence = trader.build_practice_candidate_evidence(
                [first, filtered_duplicate],
                [dict(first)],
            )
        finally:
            for name, value in originals.items():
                setattr(trader, name, value)

        self.assertTrue(evidence[0]["eligible_for_decision"])
        self.assertFalse(evidence[1]["eligible_for_decision"])
        self.assertIn(
            "not_selected_for_decision",
            evidence[1]["eligibility_blockers"],
        )
        self.assertEqual([row["observed_rank"] for row in evidence], [1, 2])
    def test_live_and_backtest_share_niuone_exit_parameters(self):
        expected = {
            "NIUONE_LEADER_LOSS_CONFIRMATIONS": NIUONE_LEADER_LOSS_CONFIRMATIONS,
            "NIUONE_MAINLINE_WEAK_CONFIRMATIONS": NIUONE_MAINLINE_WEAK_CONFIRMATIONS,
            "NIUONE_MAX_HOLD_CALENDAR_DAYS": NIUONE_MAX_HOLD_CALENDAR_DAYS,
            "NIUONE_PARTIAL_TAKE_PROFIT_R": NIUONE_PARTIAL_TAKE_PROFIT_R,
            "NIUONE_PARTIAL_TAKE_PROFIT_RATIO": NIUONE_PARTIAL_TAKE_PROFIT_RATIO,
            "NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES": (
                NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
            ),
            "NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_R": (
                NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_R
            ),
            "NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_RATIO": (
                NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_RATIO
            ),
            "NIUONE_BREAK_EVEN_AFTER_PARTIAL": NIUONE_BREAK_EVEN_AFTER_PARTIAL,
            "NIUONE_REVERSAL_MAINLINE_WEAK_CONFIRMATIONS": (
                NIUONE_REVERSAL_MAINLINE_WEAK_CONFIRMATIONS
            ),
        }
        for name, value in expected.items():
            self.assertEqual(getattr(trader, name), value)
            self.assertEqual(getattr(backtest_niuone_exits, name), value)

    def test_live_and_backtest_share_paper_account_fees(self):
        cost_model = SelectionCostModel()
        self.assertEqual(trader.COMMISSION_RATE, A_SHARE_COMMISSION_RATE)
        self.assertEqual(trader.COMMISSION_MIN, A_SHARE_MINIMUM_COMMISSION)
        self.assertEqual(
            trader.STAMP_DUTY_SELL_RATE,
            A_SHARE_SELL_STAMP_DUTY_RATE,
        )
        self.assertEqual(trader.TRANSFER_FEE_RATE, A_SHARE_TRANSFER_FEE_RATE)
        self.assertEqual(cost_model.commission_rate, trader.COMMISSION_RATE)
        self.assertEqual(cost_model.minimum_commission, trader.COMMISSION_MIN)
        self.assertEqual(
            cost_model.sell_stamp_duty_rate,
            trader.STAMP_DUTY_SELL_RATE,
        )
        self.assertEqual(cost_model.transfer_fee_rate, trader.TRANSFER_FEE_RATE)

    def _prepared_market(self) -> list[dict]:
        prepared = []
        for theme_index, industry in enumerate(("半导体", "银行", "汽车", "医药")):
            for member_index in range(4):
                code = f"{600000 + theme_index * 10 + member_index:06d}"
                step = 0.09 if theme_index == 0 else 0.025 - theme_index * 0.008
                rows = make_rows(code, industry, step)
                if theme_index == 0:
                    for row in rows[-5:]:
                        row["volume"] = 1800.0
                    enrich_rows(rows)
                    rows[-1].update({
                        "quote_turnover": 4.0,
                        "symbol_code": code,
                        "stock_name": f"测试{code}",
                        "industry": industry,
                        "quote_amount": 2.5e9,
                    })
                prepared.append({
                    "code": code,
                    "name": f"测试{code}",
                    "industry": industry,
                    "quote": {"amount": 2.5e9 if theme_index == 0 else 1.2e9},
                    "rows": rows,
                })
        return prepared

    def _prepared_reversal_market(self) -> list[dict]:
        prepared = []
        rebound_changes = (3.6, 3.4, 3.2, 2.0)
        for theme_index, industry in enumerate(("半导体", "银行", "汽车", "医药")):
            for member_index in range(4):
                code = f"{600000 + theme_index * 10 + member_index:06d}"
                rows = make_rows(code, industry, -0.03 if theme_index == 0 else 0.005)
                for row in rows[-20:]:
                    row["high"] = float(row["close"]) * 1.02
                    row["low"] = float(row["close"]) * 0.98
                enrich_rows(rows)
                rows[-1].update({
                    "quote_turnover": 4.0,
                    "symbol_code": code,
                    "stock_name": f"测试{code}",
                    "industry": industry,
                    "quote_amount": 2.5e9,
                })
                previous_close = float(rows[-2]["close"])
                if theme_index == 0:
                    change_pct = rebound_changes[member_index]
                    quote = {
                        "turnover": 4.0,
                        "price": previous_close * (1 + change_pct / 100),
                        "prev_close": previous_close,
                        "low": previous_close * 0.997,
                        "change_pct": change_pct,
                        "amount": 2.5e9 - member_index * 1e8,
                    }
                else:
                    quote = {
                        "turnover": 4.0,
                        "price": previous_close * 0.995,
                        "prev_close": previous_close,
                        "low": previous_close * 0.99,
                        "change_pct": -0.5,
                        "amount": 1e9,
                    }
                prepared.append({
                    "code": code,
                    "name": f"测试{code}",
                    "industry": industry,
                    "quote": quote,
                    "rows": rows,
                })
        return prepared

    def test_context_omits_intraday_v_observation_state(self):
        prepared = self._prepared_reversal_market()
        context_args = {
            "market_snapshot": {
                "up": 3000,
                "down": 1500,
                "median_change_pct": 0.8,
                "limit_up": 20,
                "limit_down": 2,
                "core_index_count": 3,
                "index_below_ma20_count": 0,
            },
            "flow_rows": {"inflow": [{"name": "半导体", "net_flow_yi": 10}], "outflow": []},
            "as_of_date": "2026-07-31",
            "previous_trading_day": "2026-07-30",
        }
        context = build_niuone_context(
            prepared,
            sample_at="2026-07-31 10:00:00",
            **context_args,
        )

        self.assertEqual(context["mainline"]["mode"], "none")
        self.assertFalse(any(
            key.startswith("reversal_")
            for key in context["mainline"]
        ))
        for theme in context["themes"].values():
            self.assertFalse(any(
                key.startswith("reversal_")
                for key in theme
            ))

    def test_fresh_turnover_overrides_cached_activity_for_every_scorer(self):
        prepared = self._prepared_market()
        context = build_niuone_context(prepared)
        code = "600000"
        stock = context["stocks"][code]
        self.assertTrue(all("turnover" in item for item in stock["theme_profiles"]))
        original = json.dumps(context, sort_keys=True)
        for scorer in (score_niu_reversal_probe, score_niu_emerging,
                       score_niu_leader, score_niu_pullback):
            for turnover in (None, 2.999, 3.0):
                with self.subTest(scorer=scorer.__name__, turnover=turnover):
                    rows = [dict(row) for row in prepared[0]["rows"]]
                    rows[-1]["quote_turnover"] = turnover
                    result = scorer(rows, context)
                    self.assertIsNotNone(result)
                    self.assertEqual(result["turnover"], turnover)
                    self.assertEqual(result["stock_activity_confirmed"], turnover == 3.0)
        self.assertEqual(json.dumps(context, sort_keys=True), original)

    def test_all_niuone_routes_require_market_and_theme_amount_activity(self):
        prepared = self._prepared_market()
        prepared[0]["quote"]["amount"] = 1.0e6

        context = build_niuone_context(prepared)
        cold = context["stocks"]["600000"]

        self.assertTrue(cold["activity_gate_required"])
        self.assertTrue(cold["amount_available"])
        self.assertLess(cold["market_amount_percentile"], 60.0)
        self.assertLess(cold["theme_amount_percentile"], 50.0)
        self.assertFalse(cold["activity_confirmed"])

        mature_candidate = niu_candidate(
            stock_market_amount_percentile=cold["market_amount_percentile"],
            stock_theme_amount_percentile=cold["theme_amount_percentile"],
            stock_activity_score=cold["activity_score"],
            stock_activity_confirmed=False,
        )
        mature_blockers = trader.candidate_buy_blockers(mature_candidate)
        self.assertIn(
            "个股成交活跃度不足（全市场成交额分位需≥60，题材内需≥50）",
            mature_blockers,
        )

        probe_blockers = trader.candidate_buy_blockers(reversal_candidate(
            stock_market_amount_percentile=cold["market_amount_percentile"],
            stock_theme_amount_percentile=cold["theme_amount_percentile"],
            stock_activity_score=cold["activity_score"],
            stock_activity_confirmed=False,
        ))
        self.assertTrue(any("成交活跃度" in item for item in probe_blockers))

    def test_reversal_probe_uses_multi_session_daily_v(self):
        rows = make_daily_v_rows("600000", "半导体")
        context = {
            "market": {
                "state": "offensive",
                "score": 78,
                "hard_stop": False,
                "allow_new_buys": True,
            },
            "mainline": {"mode": "none", "primary": "", "secondary": ""},
            "dragon_tiger": {"available": False},
            "news": {"configured": False},
            "themes": {
                "半导体": {
                    "state": "candidate",
                    "raw_state": "candidate",
                    "score": 72,
                    "member_count": 8,
                    "eligible_data": True,
                    "strong_stock_count": 6,
                    "effective_strong_count": 6.0,
                    "leader_concentration": 0.4,
                    "single_stock_dominated": False,
                    "state_streak": 1,
                },
            },
            "stocks": {
                "600000": {
                    "amount_available": True,
                    "market_amount_percentile": 90.0,
                    "theme_amount_percentile": 75.0,
                    "turnover": 4.0,
                    "theme_rank": 78,
                    "market_rank": 75,
                    "strong_score": 78,
                    "strong": False,
                    "role": "follower",
                    "leader_rank": 4,
                    "leader_tier": False,
                    "news_precheck": {},
                },
            },
        }

        context["stocks"]["600000"].update({
            "attribution_score": 80.0,
            "attribution_weight": 0.5,
        })
        result = score_niu_reversal_probe(rows, context)

        self.assertIsNotNone(result)
        self.assertTrue(result["daily_v_reversal"])
        self.assertEqual(result["reversal_basis"], "daily_v")
        self.assertGreaterEqual(result["daily_v_left_days"], 5)
        self.assertGreaterEqual(result["daily_v_right_days"], 3)
        self.assertGreaterEqual(result["daily_v_decline_pct"], 8)
        self.assertGreaterEqual(result["daily_v_rebound_pct"], 6)
        self.assertGreaterEqual(result["daily_v_recovery_ratio"], 0.60)
        self.assertGreaterEqual(result["entry_extension_atr"], 1.0)
        self.assertEqual(result["min_entry_extension_atr"], 1.0)
        self.assertEqual(result["stop_source"], "niu_reversal_right_low")
        self.assertEqual(result["per_trade_risk_budget_pct"], 0.35)
        self.assertEqual(result["absolute_position_cap_pct"], 10.0)
        self.assertEqual(result["hard_blockers"], [])
        self.assertTrue(result["actionable"])
        self.assertTrue(candidate_is_trade_ready(result))

        # A weak routing fallback remains visible but cannot enter a trade.
        context["stocks"]["600000"].update({
            "attribution_score": 22.0,
            "attribution_weight": 0.008,
        })
        weak = score_niu_reversal_probe(rows, context)
        self.assertIsNotNone(weak)
        self.assertTrue(weak["daily_v_reversal"])
        self.assertFalse(weak["actionable"])
        self.assertTrue(any("题材归因" in reason for reason in weak["hard_blockers"]))

    def test_reversal_probe_requires_controlled_right_side_extension(self):
        payload = with_strategy_profile("niu_reversal_probe", {
            "stock_activity_data_available": True,
            "stock_market_amount_percentile": 90.0,
            "stock_theme_amount_percentile": 75.0,
            "turnover": 4.0,
            "score": 9.0,
            "entry_extension_atr": 0.99,
            "min_entry_extension_atr": 1.0,
            "max_entry_extension_atr": 1.5,
            "max_entry_change_pct": 5.0,
            "change_pct": 2.0,
            "market_allows_buys": True,
            "market_hard_stop": False,
            "market_regime": "rotation",
            "sector_data_eligible": True,
            "sector_status": "candidate",
            "daily_v_reversal": True,
            "daily_v_left_days": 8,
            "daily_v_right_days": 7,
            "daily_v_decline_pct": 12.0,
            "daily_v_rebound_pct": 9.0,
            "daily_v_recovery_ratio": 0.75,
            "daily_v_right_trend_confirmed": True,
            "strong_stock_count": 6,
            "mainline_state_streak": 1,
            "risk_ok": True,
            "effective_loss_distance_pct": 5.0,
            "max_position_pct_by_risk": 5.0,
            "risk_flags": [],
        })

        self.assertFalse(payload["actionable"])
        self.assertIn(
            "日线V型反转距EMA20不足1ATR，右侧确认不足",
            payload["hard_blockers"],
        )

        weak_recovery = with_strategy_profile("niu_reversal_probe", {
            **payload,
            "entry_extension_atr": 1.0,
            "daily_v_recovery_ratio": 0.5999,
        })
        self.assertIn(
            "V型右侧尚未收复左侧跌幅的60%",
            weak_recovery["hard_blockers"],
        )
        self.assertIn(
            "V型右侧尚未收复左侧跌幅的60%",
            trader.candidate_buy_blockers(weak_recovery),
        )
        confirmed_recovery = with_strategy_profile("niu_reversal_probe", {
            **payload,
            "entry_extension_atr": 1.0,
            "daily_v_recovery_ratio": 0.60,
        })
        self.assertNotIn(
            "V型右侧尚未收复左侧跌幅的60%",
            confirmed_recovery["hard_blockers"],
        )
        bounded_recovery = with_strategy_profile("niu_reversal_probe", {
            **payload,
            "entry_extension_atr": 1.0,
            "daily_v_recovery_ratio": 1.9999,
        })
        self.assertNotIn(
            "V型右侧修复已达到左侧跌幅的200%，不再按早期试仓",
            bounded_recovery["hard_blockers"],
        )
        over_recovered = with_strategy_profile("niu_reversal_probe", {
            **payload,
            "entry_extension_atr": 1.0,
            "daily_v_recovery_ratio": 2.0,
        })
        recovery_cap_blocker = (
            "V型右侧修复已达到左侧跌幅的200%，不再按早期试仓"
        )
        self.assertIn(recovery_cap_blocker, over_recovered["hard_blockers"])
        self.assertIn(
            recovery_cap_blocker,
            trader.candidate_buy_blockers(over_recovered),
        )
        self.assertFalse(over_recovered["actionable"])

    def test_defensive_reversal_probe_remains_actionable_without_hard_stop(self):
        base = {
            "signal_theme_attribution_score": 80.0,
            "signal_theme_attribution_weight": 0.5,
            "stock_activity_data_available": True,
            "stock_market_amount_percentile": 90.0,
            "stock_theme_amount_percentile": 75.0,
            "turnover": 4.0,
            "score": 9.0,
            "entry_extension_atr": 1.2,
            "min_entry_extension_atr": 1.0,
            "max_entry_extension_atr": 1.5,
            "change_pct": 2.0,
            "market_allows_buys": True,
            "market_hard_stop": False,
            "market_regime": "defensive",
            "sector_data_eligible": True,
            "sector_status": "candidate",
            "mainline_state": "candidate",
            "niuone_lifecycle_stage": "brewing",
            "daily_v_reversal": True,
            "daily_v_left_days": 8,
            "daily_v_right_days": 7,
            "daily_v_decline_pct": 12.0,
            "daily_v_rebound_pct": 9.0,
            "daily_v_recovery_ratio": 0.75,
            "daily_v_right_trend_confirmed": True,
            "strong_stock_count": 6,
            "mainline_state_streak": 1,
            "risk_ok": True,
            "effective_loss_distance_pct": 5.0,
            "max_position_pct_by_risk": 3.0,
            "risk_flags": [],
        }

        candidate = with_strategy_profile("niu_reversal_probe", dict(base))

        self.assertTrue(candidate["actionable"])
        self.assertEqual(candidate["hard_blockers"], [])

        hard_stopped = with_strategy_profile(
            "niu_reversal_probe",
            {**base, "market_hard_stop": True},
        )
        self.assertFalse(hard_stopped["actionable"])
        self.assertIn("市场风控禁止新开仓", hard_stopped["hard_blockers"])

    def test_reversal_probe_requires_breadth_or_sustained_brewing(self):
        base = {
            "stock_activity_data_available": True,
            "stock_market_amount_percentile": 90.0,
            "stock_theme_amount_percentile": 75.0,
            "turnover": 4.0,
            "score": 9.0,
            "entry_extension_atr": 1.2,
            "min_entry_extension_atr": 1.0,
            "max_entry_extension_atr": 1.5,
            "max_entry_change_pct": 5.0,
            "change_pct": 2.0,
            "market_allows_buys": True,
            "market_hard_stop": False,
            "market_regime": "rotation",
            "sector_data_eligible": True,
            "sector_status": "candidate",
            "daily_v_reversal": True,
            "daily_v_left_days": 8,
            "daily_v_right_days": 7,
            "daily_v_decline_pct": 12.0,
            "daily_v_rebound_pct": 9.0,
            "daily_v_recovery_ratio": 0.75,
            "daily_v_right_trend_confirmed": True,
            "risk_ok": True,
            "effective_loss_distance_pct": 5.0,
            "max_position_pct_by_risk": 6.0,
            "risk_flags": [],
        }
        weak = with_strategy_profile("niu_reversal_probe", {
            **base,
            "strong_stock_count": 5,
            "mainline_state_streak": 2,
        })
        blocker = (
            "牛牛试仓需题材至少6只强势股，或酝酿状态连续至少3个交易日"
        )
        self.assertIn(blocker, weak["hard_blockers"])
        self.assertIn(blocker, trader.candidate_buy_blockers(weak))

        broad = with_strategy_profile("niu_reversal_probe", {
            **base,
            "strong_stock_count": 6,
            "mainline_state_streak": 1,
        })
        sustained = with_strategy_profile("niu_reversal_probe", {
            **base,
            "strong_stock_count": 3,
            "mainline_state_streak": 3,
        })
        self.assertNotIn(blocker, broad["hard_blockers"])
        self.assertNotIn(blocker, sustained["hard_blockers"])

    def test_reversal_probe_routes_mature_and_early_strong_names_to_later_stages(self):
        early = reversal_candidate()
        self.assertIsNone(niu_reversal_entry_stage_blocker(early))
        self.assertTrue(candidate_is_trade_ready(early))

        candidate_strong = {
            **early,
            "stock_strong": True,
            "stock_strong_score": 72.0,
        }
        self.assertEqual(
            niu_reversal_entry_stage_blocker(candidate_strong),
            "候选题材中的强势股需等待牛牛启动确认",
        )
        self.assertFalse(candidate_is_trade_ready(candidate_strong))

        emerging_strong = {
            **candidate_strong,
            "mainline_state": "emerging",
            "sector_status": "emerging",
        }
        self.assertIsNone(niu_reversal_entry_stage_blocker(emerging_strong))
        self.assertTrue(candidate_is_trade_ready(emerging_strong))

        mature = {
            **early,
            "mainline_state": "mainline",
            "sector_status": "mainline",
            "mainline_confirmed": True,
            "niuone_lifecycle_stage": "markup",
        }
        self.assertEqual(
            niu_reversal_entry_stage_blocker(mature),
            "主线主升阶段不允许牛牛试仓开新仓",
        )
        self.assertFalse(candidate_is_trade_ready(mature))

        profiled = with_strategy_profile(
            "niu_reversal_probe",
            {
                **mature,
                "score": 9.0,
                "strategy_id": "niu_reversal_probe",
                "market_allows_buys": True,
                "market_hard_stop": False,
                "sector_data_eligible": True,
                "risk_ok": True,
                "entry_extension_atr": 1.2,
                "min_entry_extension_atr": 1.0,
                "max_entry_extension_atr": 1.5,
                "max_entry_change_pct": 5.0,
                "change_pct": 2.0,
                "risk_flags": [],
            },
        )
        self.assertFalse(profiled["actionable"])
        self.assertIn(
            "主线主升阶段不允许牛牛试仓开新仓",
            profiled["hard_blockers"],
        )

    def test_daily_v_requires_at_least_two_thirds_rising_sessions(self):
        def rows_for(closes):
            return [
                {
                    "date": f"2026-05-{index + 1:02d}",
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 1000.0,
                }
                for index, close in enumerate(closes)
            ]

        sixty_percent = _daily_v_reversal_metrics(rows_for((
            12.0, 11.5, 11.0, 10.5, 10.0, 9.5, 9.2,
            9.8, 9.6, 10.2, 10.0, 10.9,
        )))
        two_thirds = _daily_v_reversal_metrics(rows_for((
            12.0, 11.5, 11.0, 10.5, 10.0, 9.5, 9.2,
            9.8, 9.6, 10.2, 10.0, 10.4, 10.9,
        )))

        self.assertEqual(sixty_percent["daily_v_rising_ratio"], 0.6)
        self.assertFalse(sixty_percent["daily_v_reversal"])
        self.assertAlmostEqual(two_thirds["daily_v_rising_ratio"], 2 / 3, places=4)
        self.assertTrue(two_thirds["daily_v_reversal"])

    def test_trade_candidates_do_not_cap_daily_reversals_at_two(self):
        candidates = [
            reversal_candidate(code="600001", best_score=8.9, score=8.9),
            reversal_candidate(code="600002", best_score=8.7, score=8.7),
            reversal_candidate(code="600003", best_score=8.6, score=8.6),
            niu_candidate(code="600004", best_score=8.5, score=8.5),
        ]

        selected = select_trade_candidates(candidates, limit=4)

        self.assertEqual(
            [item["code"] for item in selected],
            ["600001", "600002", "600003", "600004"],
        )
        self.assertEqual(selected[0]["selection_candidate_pool_size"], 4)
        self.assertEqual(selected[0]["selection_same_stage_candidate_count"], 3)
        self.assertEqual(selected[0]["selection_same_stage_candidate_rank"], 1)
        self.assertEqual(selected[0]["selection_same_stage_top_score_gap"], 0.2)
        self.assertEqual(selected[1]["selection_same_stage_candidate_rank"], 2)
        self.assertEqual(selected[2]["selection_same_stage_candidate_rank"], 3)

    def test_context_confirms_mainline_from_multiple_strong_stocks(self):
        prepared = self._prepared_market()
        prepared[0]["quote"]["change_pct"] = 7.35
        market_snapshot = {
                "up": 120,
                "down": 30,
                "median_change_pct": 0.8,
                "limit_up": 12,
                "limit_down": 1,
                "core_index_count": 3,
                "index_below_ma20_count": 0,
            }
        context = build_niuone_context(
            prepared,
            market_snapshot=market_snapshot,
            flow_rows={"inflow": [{"name": "半导体", "net_flow_yi": 30}], "outflow": []},
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )
        confirmed = build_niuone_context(
            prepared,
            market_snapshot=market_snapshot,
            flow_rows={"inflow": [{"name": "半导体", "net_flow_yi": 30}], "outflow": []},
            previous_context=context,
            as_of_date="2026-07-28",
            previous_trading_day="2026-07-27",
        )

        theme = confirmed["themes"]["半导体"]
        self.assertEqual(context["theme_basis"], "industry_proxy")
        self.assertEqual(confirmed["market"]["state"], "offensive")
        self.assertEqual(confirmed["market"]["per_trade_risk_pct"], 1.5)
        self.assertGreaterEqual(theme["strong_stock_count"], 3)
        self.assertGreaterEqual(theme["effective_strong_count"], 2.4)
        self.assertAlmostEqual(
            theme["effective_breadth_pct"],
            theme["effective_strong_count"] / theme["member_count"] * 100,
            delta=0.2,
        )
        self.assertFalse(theme["single_stock_dominated"])
        self.assertEqual(theme["strong_stocks"][0]["code"], "600000")
        self.assertEqual(theme["strong_stocks"][0]["role"], "leader")
        self.assertEqual(theme["strong_stocks"][0]["leader_rank"], 1)
        self.assertTrue(all(stock["leader_tier"] for stock in theme["strong_stocks"][:3]))
        self.assertFalse(theme["strong_stocks"][3]["leader_tier"])
        self.assertEqual(theme["strong_stocks"][0]["change_pct"], 7.35)
        self.assertTrue(all(stock["role"] == "core" for stock in theme["strong_stocks"][1:]))
        self.assertEqual(theme["state"], "mainline")
        self.assertTrue(theme["cross_day_confirmed"])
        self.assertGreaterEqual(theme["core_overlap_count"], 2)
        self.assertEqual(theme["confirmation_count"], 2)
        self.assertEqual(confirmed["mainline"]["mode"], "single")
        self.assertEqual(confirmed["mainline"]["primary"], "半导体")

    def test_eastmoney_concepts_group_one_stock_into_multiple_themes(self):
        prepared = self._prepared_market()
        semiconductor_codes = {
            item["code"] for item in prepared if item["industry"] == "半导体"
        }
        for item in prepared:
            if item["code"] in semiconductor_codes:
                item["themes"] = ["存储芯片", "先进封装概念"]

        context = build_niuone_context(
            prepared,
            theme_basis="eastmoney_concept",
        )

        self.assertEqual(context["theme_basis"], "eastmoney_concept")
        self.assertEqual(context["themes"]["存储芯片"]["member_count"], 4)
        self.assertEqual(context["themes"]["先进封装"]["member_count"], 4)
        memberships = context["stocks"]["600000"]["theme_memberships"]
        self.assertCountEqual(memberships, ["存储芯片", "先进封装"])
        self.assertIn(context["stocks"]["600000"]["industry"], memberships)
        self.assertCountEqual(
            [
                item["industry"]
                for item in context["stocks"]["600000"]["theme_profiles"]
            ],
            ["存储芯片", "先进封装"],
        )
        attributions = context["stocks"]["600000"]["theme_attributions"]
        self.assertCountEqual(
            [item["theme"] for item in attributions],
            ["存储芯片", "先进封装"],
        )
        total_weight = sum(
            float(item["attribution_weight"])
            for item in attributions
        )
        self.assertGreater(total_weight, 0.25)
        self.assertLessEqual(total_weight, 1.0)
        self.assertAlmostEqual(
            float(attributions[0]["attribution_weight"]),
            float(attributions[1]["attribution_weight"]),
            places=5,
        )

    def test_theme_peer_summaries_match_reference_leave_one_out_scores(self):
        members = [
            {
                "code": "600001", "ret5": 3.0, "ret20": -1.5,
                "strong": True, "live_change_available": True,
                "change_pct": 2.0,
            },
            {
                "code": "600002", "ret5": 0.0, "ret20": -1.5,
                "strong": False, "live_change_available": True,
                "change_pct": -0.4,
            },
            {
                "code": "600003", "ret5": -2.0, "ret20": 4.0,
                "strong": True, "live_change_available": False,
                "change_pct": 8.0,
            },
            {
                "code": "600004", "ret5": -2.0, "ret20": 0.0,
                "strong": False, "live_change_available": True,
                "change_pct": 0.0,
            },
            {
                "code": "600005", "ret5": 1.25, "ret20": 4.0,
                "strong": True, "live_change_available": True,
                "change_pct": 1.1,
            },
        ]
        summary = _theme_peer_statistics(members)

        for member in members:
            with self.subTest(code=member["code"]):
                self.assertAlmostEqual(
                    _cohort_alignment_score(
                        member,
                        members,
                        peer_statistics=summary,
                    ),
                    _cohort_alignment_score_reference(member, members),
                    places=12,
                )
                self.assertAlmostEqual(
                    _peer_resonance_score(
                        member,
                        members,
                        market_breadth_pct=43.25,
                        peer_statistics=summary,
                    ),
                    _peer_resonance_score_reference(
                        member,
                        members,
                        market_breadth_pct=43.25,
                    ),
                    places=12,
                )

    def test_theme_peer_summaries_preserve_single_and_duplicate_code_edges(self):
        single = [{
            "code": "600001", "ret5": 1.0, "ret20": 2.0,
            "strong": True, "live_change_available": True,
            "change_pct": 1.0,
        }]
        single_summary = _theme_peer_statistics(single)
        self.assertEqual(
            _cohort_alignment_score(
                single[0], single, peer_statistics=single_summary,
            ),
            50.0,
        )
        self.assertEqual(
            _peer_resonance_score(
                single[0], single, market_breadth_pct=50.0,
                peer_statistics=single_summary,
            ),
            0.0,
        )

        duplicate_codes = [
            {
                "code": "600001", "ret5": 1.0, "ret20": 2.0,
                "strong": True, "live_change_available": True,
                "change_pct": 1.0,
            },
            {
                "code": "600001", "ret5": -3.0, "ret20": -4.0,
                "strong": False, "live_change_available": True,
                "change_pct": -2.0,
            },
            {
                "code": "600002", "ret5": 0.5, "ret20": -1.0,
                "strong": False, "live_change_available": False,
                "change_pct": 0.0,
            },
        ]
        duplicate_summary = _theme_peer_statistics(duplicate_codes)
        for member in duplicate_codes:
            self.assertAlmostEqual(
                _cohort_alignment_score(
                    member,
                    duplicate_codes,
                    peer_statistics=duplicate_summary,
                ),
                _cohort_alignment_score_reference(member, duplicate_codes),
                places=12,
            )
            self.assertAlmostEqual(
                _peer_resonance_score(
                    member,
                    duplicate_codes,
                    market_breadth_pct=50.0,
                    peer_statistics=duplicate_summary,
                ),
                _peer_resonance_score_reference(
                    member,
                    duplicate_codes,
                    market_breadth_pct=50.0,
                ),
                places=12,
            )

    def test_wide_theme_peer_scores_reuse_summary_without_member_scans(self):
        class NoIterationList(list):
            def __iter__(self):
                raise AssertionError("cached peer scoring rescanned the theme")

        members = [
            {
                "code": f"{index:06d}",
                "ret5": (index % 11 - 5) * 0.25,
                "ret20": (index % 17 - 8) * 0.4,
                "strong": index % 3 == 0,
                "live_change_available": index % 5 != 0,
                "change_pct": (index % 9 - 4) * 0.3,
            }
            for index in range(1_351)
        ]
        summary = _theme_peer_statistics(members)
        guarded_members = NoIterationList(members)

        for member in members:
            _cohort_alignment_score(
                member,
                guarded_members,
                peer_statistics=summary,
            )
            _peer_resonance_score(
                member,
                guarded_members,
                market_breadth_pct=48.0,
                peer_statistics=summary,
            )

    def test_niuone_context_builds_one_peer_summary_per_theme(self):
        prepared = self._prepared_market()
        for item in prepared:
            item["themes"] = ["全市场宽题材"]

        with patch(
            "strategies.scoring.niuone._theme_peer_statistics",
            wraps=_theme_peer_statistics,
        ) as build_summary:
            context = build_niuone_context(
                prepared,
                theme_basis="eastmoney_concept",
            )

        self.assertEqual(context["theme_count"], 1)
        self.assertEqual(build_summary.call_count, 1)

    def test_optimized_peer_summaries_preserve_complete_context_output(self):
        prepared = self._prepared_market()
        for index, item in enumerate(prepared):
            item["themes"] = ["全市场宽题材", f"分支题材{index % 3}"]
        market_snapshot = {
            "up": 9,
            "down": 7,
            "median_change_pct": 0.25,
            "limit_up": 1,
            "limit_down": 0,
        }
        optimized = build_niuone_context(
            prepared,
            market_snapshot=market_snapshot,
            theme_basis="eastmoney_concept",
            as_of_date="2026-08-04",
        )

        with (
            patch(
                "strategies.scoring.niuone._cohort_alignment_score",
                side_effect=lambda member, theme_members, **_kwargs: (
                    _cohort_alignment_score_reference(member, theme_members)
                ),
            ),
            patch(
                "strategies.scoring.niuone._peer_resonance_score",
                side_effect=lambda member, theme_members, **kwargs: (
                    _peer_resonance_score_reference(
                        member,
                        theme_members,
                        market_breadth_pct=kwargs["market_breadth_pct"],
                    )
                ),
            ),
        ):
            reference = build_niuone_context(
                prepared,
                market_snapshot=market_snapshot,
                theme_basis="eastmoney_concept",
                as_of_date="2026-08-04",
            )

        self.assertEqual(optimized, reference)

    def test_shared_niuone_entry_metrics_preserve_all_four_scorer_outputs(self):
        prepared = self._prepared_market()
        context = build_niuone_context(
            prepared,
            as_of_date="2026-08-04",
        )
        rows = prepared[0]["rows"]
        scorers = (
            score_niu_leader,
            score_niu_pullback,
            score_niu_emerging,
            score_niu_reversal_probe,
        )
        baseline = [scorer(rows, context) for scorer in scorers]
        shared = _shared_entry_metrics(rows)
        optimized = [
            scorer(rows, context, shared_metrics=shared)
            for scorer in scorers
        ]

        self.assertIsNotNone(shared)
        self.assertEqual(optimized, baseline)

    def test_multi_strategy_engine_builds_niuone_shared_metrics_once(self):
        prepared = self._prepared_market()
        context = build_niuone_context(prepared, as_of_date="2026-08-04")
        rows = prepared[0]["rows"]
        scorers = {
            "niu_leader": score_niu_leader,
            "niu_pullback": score_niu_pullback,
            "niu_emerging": score_niu_emerging,
            "niu_reversal_probe": score_niu_reversal_probe,
        }
        expected = {
            strategy_id: scorer(rows, context)
            for strategy_id, scorer in scorers.items()
        }
        calls = 0

        def counted_builder(values):
            nonlocal calls
            calls += 1
            return _shared_entry_metrics(values)

        original_builders = {
            scorer: scorer.shared_input_builder
            for scorer in scorers.values()
        }
        try:
            for scorer in scorers.values():
                scorer.shared_input_builder = counted_builder
            result = analyze_enriched_rows(rows, scorers, context)
        finally:
            for scorer, builder in original_builders.items():
                scorer.shared_input_builder = builder

        self.assertIsNotNone(result)
        self.assertEqual(calls, 1)
        self.assertEqual(result["strategies"], expected)

    def test_compact_previous_context_preserves_next_close_output(self):
        prepared = self._prepared_market()
        market_snapshot = {
            "up": 120,
            "down": 30,
            "median_change_pct": 0.8,
            "limit_up": 12,
            "limit_down": 1,
            "core_index_count": 3,
            "index_below_ma20_count": 0,
        }
        first = build_niuone_context(
            prepared,
            market_snapshot=market_snapshot,
            as_of_date="2026-08-03",
        )
        full = build_niuone_context(
            prepared,
            market_snapshot=market_snapshot,
            previous_context=first,
            as_of_date="2026-08-04",
            previous_trading_day="2026-08-03",
        )
        compact_state = _compact_niuone_previous_context(first)
        compact = build_niuone_context(
            prepared,
            market_snapshot=market_snapshot,
            previous_context=compact_state,
            as_of_date="2026-08-04",
            previous_trading_day="2026-08-03",
        )

        self.assertNotIn(
            "strong_stocks",
            compact_state["themes"]["半导体"],
        )
        self.assertNotIn(
            "theme_profiles",
            compact_state["stocks"]["600000"],
        )
        self.assertEqual(compact, full)

    def test_weak_multi_concept_candidates_keep_unattributed_mass(self):
        profiles = _apply_theme_attributions(
            [
                {
                    "industry": theme,
                    "peer_resonance_score": 0,
                    "cohort_alignment_score": 0,
                    "today_rank_score": 0,
                    "theme_rank": 0,
                    "strong": False,
                }
                for theme in ("标签甲", "标签乙", "标签丙")
            ],
            previous_stock=None,
            same_trading_day=False,
        )

        self.assertAlmostEqual(
            sum(float(item["attribution_weight"]) for item in profiles),
            0.25,
            places=6,
        )
        self.assertTrue(all(item["current_attribution_score"] == 0.0 for item in profiles))
        self.assertTrue(all(item["unattributed_weight"] == 0.75 for item in profiles))

    def test_primary_high_evidence_theme_survives_multi_label_weight_dilution(self):
        profiles = _apply_theme_attributions(
            [
                {
                    "industry": f"题材{index:02d}",
                    "theme_member_count": 50,
                    "return_correlation_score": 88.0,
                    "return_correlation_observation_count": 20,
                    "peer_resonance_score": 82.0,
                    "cohort_alignment_score": 80.0,
                    "today_rank_score": 90.0,
                    "theme_rank": 90.0,
                    "strong": True,
                }
                for index in range(12)
            ],
            previous_stock=None,
            same_trading_day=False,
        )

        self.assertLess(profiles[0]["attribution_weight"], 0.15)
        self.assertGreaterEqual(profiles[0]["attribution_score"], 60.0)
        self.assertTrue(profiles[0]["leadership_eligible"])
        self.assertFalse(any(
            item["leadership_eligible"] for item in profiles[1:]
        ))

    def test_qualified_theme_leaders_rank_by_stock_strength_not_weight_mass(self):
        members = [
            {
                "code": "600522",
                "strong_score": 96.0,
                "change_pct": 6.0,
                "amount": 1.0e9,
            },
            {
                "code": "600001",
                "strong_score": 91.0,
                "change_pct": 4.0,
                "amount": 2.0e9,
            },
            {
                "code": "600002",
                "strong_score": 99.0,
                "change_pct": 9.0,
                "amount": 3.0e9,
            },
        ]
        attributions = {
            "600522": {
                "attribution_score": 84.0,
                "attribution_weight": 0.09,
                "leadership_eligible": True,
            },
            "600001": {
                "attribution_score": 75.0,
                "attribution_weight": 0.70,
                "leadership_eligible": True,
            },
            "600002": {
                "attribution_score": 59.0,
                "attribution_weight": 0.14,
                "leadership_eligible": False,
            },
        }

        structural = _rank_theme_leaders(
            members,
            attributions,
            intraday=False,
        )
        intraday = _rank_theme_leaders(
            members,
            attributions,
            intraday=True,
        )

        self.assertEqual([item["code"] for item in structural], ["600522", "600001"])
        self.assertEqual([item["code"] for item in intraday], ["600522", "600001"])

    def test_nested_theme_tie_prefers_the_more_specific_cohort(self):
        profiles = _apply_theme_attributions(
            [
                {
                    "industry": theme,
                    "theme_member_count": member_count,
                    "return_correlation_score": 90.0,
                    "return_correlation_observation_count": 20,
                    "peer_resonance_score": 80.0,
                    "cohort_alignment_score": 80.0,
                    "today_rank_score": 80.0,
                    "theme_rank": 80.0,
                    "strong": True,
                }
                for theme, member_count in (
                    ("军工", 300),
                    ("商业航天", 60),
                )
            ],
            previous_stock=None,
            same_trading_day=False,
        )

        self.assertEqual(profiles[0]["industry"], "商业航天")
        self.assertEqual(profiles[0]["theme_specificity_score"], 100.0)
        self.assertEqual(profiles[1]["theme_specificity_score"], 0.0)
        self.assertGreater(
            profiles[0]["attribution_score"],
            profiles[1]["attribution_score"],
        )

    def test_multi_concept_leader_is_attributed_to_independent_peer_narrative(self):
        def prepared_item(
            code: str,
            *,
            step: float,
            change_pct: float,
            themes: list[str],
            amount: float,
            name: str = "测试",
        ) -> dict:
            rows = make_rows(code, "有色金属", step)
            previous_close = float(rows[-2]["close"])
            return {
                "code": code,
                "name": name,
                "industry": "有色金属",
                "themes": themes,
                "rows": rows,
                "quote": {
                    "turnover": 4.0,
                    "price": previous_close * (1 + change_pct / 100),
                    "prev_close": previous_close,
                    "low": previous_close,
                    "change_pct": change_pct,
                    "amount": amount,
                },
            }

        prepared = [
            prepared_item(
                "002149",
                step=0.12,
                change_pct=7.4,
                themes=["纳米银", "核能核电", "商业航天"],
                amount=3e9,
                name="西部材料",
            )
        ]
        prepared.extend(
            prepared_item(
                f"60010{index}",
                step=0.10,
                change_pct=4.5 - index * 0.3,
                themes=["商业航天"],
                amount=1.8e9,
            )
            for index in range(3)
        )
        prepared.extend(
            prepared_item(
                f"60020{index}",
                step=-0.01,
                change_pct=0.3 - index * 0.2,
                themes=["纳米银"],
                amount=0.6e9,
            )
            for index in range(6)
        )
        prepared.extend(
            prepared_item(
                f"60030{index}",
                step=0.01,
                change_pct=0.8 - index * 0.15,
                themes=["核能核电"],
                amount=0.8e9,
            )
            for index in range(8)
        )

        context = build_niuone_context(
            prepared,
            market_snapshot={
                "up": 9,
                "down": 9,
                "median_change_pct": 0,
                "limit_up": 0,
                "limit_down": 0,
            },
            news_snapshot={
                "configured": True,
                "records": [{
                    "code": "002149",
                    "checked": True,
                    "available": True,
                    "tone": "positive",
                    "summary": "公司核电材料业务获得市场关注",
                }],
            },
            theme_basis="eastmoney_concept",
            as_of_date="2026-08-03",
        )

        stock = context["stocks"]["002149"]
        weights = {
            item["theme"]: float(item["attribution_weight"])
            for item in stock["theme_attributions"]
        }
        self.assertEqual(stock["dominant_theme"], "商业航天")
        self.assertIn("商业航天", stock["theme_memberships"])
        self.assertTrue(stock["theme_attribution_confident"])
        self.assertGreater(weights["商业航天"], 0.75)
        self.assertLess(weights["纳米银"], 0.15)
        self.assertLess(weights["核能核电"], 0.15)
        self.assertEqual(
            context["themes"]["商业航天"]["today_leaders"][0]["code"],
            "002149",
        )
        self.assertNotIn(
            "002149",
            {
                item["code"]
                for theme_name in ("纳米银", "核能核电")
                for item in context["themes"][theme_name]["today_leaders"]
            },
        )
        self.assertGreater(
            context["themes"]["商业航天"]["today_strength_score"],
            context["themes"]["纳米银"]["today_strength_score"],
        )
        self.assertGreater(
            context["themes"]["商业航天"]["today_strength_score"],
            context["themes"]["核能核电"]["today_strength_score"],
        )
        self.assertEqual(context["mainline"]["today_primary"], "商业航天")
        commercial_attribution = next(
            item
            for item in stock["theme_attributions"]
            if item["theme"] == "商业航天"
        )
        self.assertEqual(
            commercial_attribution["membership_source"],
            "eastmoney_concept",
        )

        no_news_context = build_niuone_context(
            prepared,
            market_snapshot={
                "up": 9,
                "down": 9,
                "median_change_pct": 0,
                "limit_up": 0,
                "limit_down": 0,
            },
            theme_basis="eastmoney_concept",
            as_of_date="2026-08-03",
        )
        self.assertEqual(
            stock["theme_attributions"],
            no_news_context["stocks"]["002149"]["theme_attributions"],
        )
        self.assertEqual(
            context["themes"]["商业航天"]["score"],
            no_news_context["themes"]["商业航天"]["score"],
        )

        minute_context = build_niuone_context(
            prepared,
            market_snapshot={
                "up": 9,
                "down": 9,
                "median_change_pct": 0,
                "limit_up": 0,
                "limit_down": 0,
            },
            previous_context=context,
            reuse_previous_external_context=True,
            theme_basis="eastmoney_concept",
            as_of_date="2026-08-03",
        )
        minute_attribution = next(
            item
            for item in minute_context["stocks"]["002149"]["theme_attributions"]
            if item["theme"] == "商业航天"
        )
        self.assertEqual(
            minute_attribution["membership_source"],
            "eastmoney_concept",
        )

    def test_market_neutral_return_wave_breaks_aggregate_theme_tie(self):
        target_returns = [
            1.2, -0.9, 2.1, -1.3, 0.8,
            1.5, -0.7, 2.4, -1.1, 1.8,
            0.6, -0.5, 2.2, -1.0, 1.4,
            0.9, -0.4, 1.7, -0.8, 2.6,
        ]

        def patterned_item(
            code: str,
            returns: list[float],
            themes: list[str],
            *,
            name: str = "测试",
        ) -> dict:
            rows = make_rows(code, "有色金属", 0.02)
            close = float(rows[-len(returns) - 1]["close"])
            for row, change_pct in zip(rows[-len(returns):], returns):
                close *= 1.0 + change_pct / 100.0
                row.update({
                    "open": close * 0.997,
                    "close": close,
                    "high": close * 1.008,
                    "low": close * 0.992,
                    "volume": 1200.0,
                })
            enrich_rows(rows)
            rows[-1].update({
                "quote_turnover": 4.0,
                "symbol_code": code,
                "stock_name": name,
                "industry": "有色金属",
                "quote_amount": 2e9,
            })
            return {
                "code": code,
                "name": name,
                "industry": "有色金属",
                "themes": themes,
                "rows": rows,
                "quote": {
                    "turnover": 4.0,
                    "price": float(rows[-1]["close"]),
                    "prev_close": float(rows[-2]["close"]),
                    "change_pct": returns[-1],
                    "amount": 2e9,
                },
            }

        prepared = [
            patterned_item(
                "002149",
                target_returns,
                ["商业航天", "水利建设"],
                name="西部材料",
            )
        ]
        prepared.extend(
            patterned_item(
                f"60040{index}",
                [value * (0.72 + index * 0.04) for value in target_returns],
                ["商业航天"],
            )
            for index in range(4)
        )
        rotated = target_returns[5:] + target_returns[:5]
        prepared.extend(
            patterned_item(
                f"60050{index}",
                [value * (1.08 + index * 0.04) for value in rotated],
                ["水利建设"],
            )
            for index in range(4)
        )

        context = build_niuone_context(
            prepared,
            market_snapshot={
                "up": 5,
                "down": 4,
                "median_change_pct": 0.2,
                "limit_up": 0,
                "limit_down": 0,
            },
            theme_basis="eastmoney_concept",
            as_of_date="2026-08-03",
        )

        stock = context["stocks"]["002149"]
        attributions = {
            item["theme"]: item
            for item in stock["theme_attributions"]
        }
        self.assertEqual(stock["dominant_theme"], "商业航天")
        self.assertGreater(
            attributions["商业航天"]["return_correlation_score"],
            attributions["水利建设"]["return_correlation_score"],
        )
        self.assertGreater(
            attributions["商业航天"]["return_correlation_rank_score"],
            attributions["水利建设"]["return_correlation_rank_score"],
        )
        self.assertEqual(
            attributions["商业航天"]["return_correlation_observation_count"],
            20,
        )
        self.assertEqual(
            attributions["商业航天"]["return_correlation_peer_count"],
            4,
        )

    def test_same_stage_multi_concept_route_prefers_attribution_evidence(self):
        rows = make_rows("600000", "通信设备", 0.02)
        theme = {
            "state": "mainline", "score": 76.0,
            "niuone_lifecycle_stage": "markup",
            "member_count": 8, "eligible_data": True,
            "strong_stock_count": 4, "effective_strong_count": 3.5,
            "single_stock_dominated": False,
            "cross_day_persistent": True,
            "cross_day_confirmed": True,
            "mainline_confirmed": True,
            "today_strength_score": 75.0,
        }
        context = {
            "market": {
                "state": "offensive", "score": 78, "hard_stop": False,
                "allow_new_buys": True,
            },
            "mainline": {"mode": "single", "primary": "通信主题"},
            "dragon_tiger": {"available": False},
            "news": {"configured": False},
            "themes": {
                "通信主题": dict(theme),
                "数字货币": dict(theme),
            },
            "stocks": {
                "600000": {
                    "amount_available": True,
                    "market_amount_percentile": 90.0,
                    "theme_amount_percentile": 75.0,
                    "turnover": 4.0,
                    "industry": "通信主题", "theme_rank": 100.0,
                    "market_rank": 92.0, "strong_score": 92.0,
                    "strong": True, "role": "leader", "leader_rank": 1,
                    "leader_tier": True, "news_precheck": {},
                    "classification_industry": "通信设备",
                    "theme_profiles": [
                        {
                            "industry": "通信主题",
                            "classification_industry": "通信设备",
                            "role": "leader", "leader_rank": 1,
                            "leader_tier": True, "theme_rank": 100.0,
                            "attribution_score": 61.0,
                        },
                        {
                            "industry": "数字货币",
                            "classification_industry": "通信设备",
                            "role": "leader", "leader_rank": 1,
                            "leader_tier": True, "theme_rank": 90.0,
                            "attribution_score": 88.0,
                        },
                    ],
                },
            },
        }

        result = score_niu_leader(rows, context)

        self.assertIsNotNone(result)
        self.assertEqual(result["industry"], "数字货币")
        self.assertEqual(result["signal_theme"], "数字货币")
        self.assertEqual(result["classification_industry"], "通信设备")

    def test_multi_concept_stock_routes_each_action_to_a_compatible_branch(self):
        rows = make_rows("600000", "成熟分支", 0.02)
        context = {
            "market": {
                "state": "offensive", "score": 78, "hard_stop": False,
                "allow_new_buys": True,
            },
            "mainline": {"mode": "single", "primary": "成熟分支"},
            "dragon_tiger": {"available": False},
            "news": {"configured": False},
            "themes": {
                "成熟分支": {
                    "state": "mainline", "score": 76.0,
                    "niuone_lifecycle_stage": "markup",
                    "member_count": 8, "eligible_data": True,
                    "strong_stock_count": 4, "effective_strong_count": 3.5,
                    "single_stock_dominated": False,
                    "cross_day_persistent": True,
                    "cross_day_confirmed": True,
                    "mainline_confirmed": True,
                    "today_strength_score": 75.0,
                },
                "启动分支": {
                    "state": "emerging", "score": 69.0,
                    "niuone_lifecycle_stage": "markup",
                    "member_count": 7, "eligible_data": True,
                    "strong_stock_count": 3, "effective_strong_count": 2.5,
                    "single_stock_dominated": False,
                    "cross_day_persistent": True,
                    "cross_day_confirmed": False,
                    "mainline_confirmed": False,
                    "today_strength_score": 68.0,
                },
            },
            "stocks": {
                "600000": {
                    "amount_available": True,
                    "market_amount_percentile": 90.0,
                    "theme_amount_percentile": 75.0,
                    "turnover": 4.0,
                    "industry": "成熟分支", "theme_rank": 100.0,
                    "market_rank": 92.0, "strong_score": 92.0,
                    "strong": True, "role": "leader", "leader_rank": 1,
                    "leader_tier": True, "news_precheck": {},
                    "theme_profiles": [
                        {
                            "industry": "成熟分支", "role": "leader",
                            "leader_rank": 1, "leader_tier": True,
                            "theme_rank": 100.0,
                        },
                        {
                            "industry": "启动分支", "role": "leader",
                            "leader_rank": 1, "leader_tier": True,
                            "theme_rank": 100.0,
                        },
                    ],
                },
            },
        }

        startup = score_niu_emerging(rows, context)
        leader = score_niu_leader(rows, context)

        self.assertIsNotNone(startup)
        self.assertEqual(startup["industry"], "启动分支")
        self.assertEqual(startup["mainline_state"], "emerging")
        self.assertEqual(startup["stock_leader_rank"], 1)
        self.assertTrue(startup["stock_leader_tier"])
        self.assertNotIn(
            "主题不处于跨日延续的待确认启动阶段",
            startup["hard_blockers"],
        )
        self.assertNotIn(
            "个股未进入强势行业龙头梯队",
            startup["hard_blockers"],
        )
        self.assertIsNotNone(leader)
        self.assertEqual(leader["industry"], "成熟分支")
        self.assertEqual(leader["mainline_state"], "mainline")
        self.assertTrue(leader["stock_leader_tier"])

    def test_divergence_leader_is_not_blocked_by_markup_only_gates(self):
        rows = make_rows("600000", "分歧分支", 0.02)
        context = {
            "market": {
                "state": "rotation", "score": 62, "hard_stop": False,
                "allow_new_buys": True,
            },
            # The branch is intentionally not the primary/secondary display
            # mainline. That label is a rank hint, not an eligibility gate.
            "mainline": {"mode": "single", "primary": "另一主线"},
            "dragon_tiger": {"available": False},
            "news": {"configured": False},
            "themes": {
                "分歧分支": {
                    "state": "diverging", "score": 73.0,
                    "niuone_lifecycle_stage": "divergence",
                    "member_count": 8, "eligible_data": True,
                    "strong_stock_count": 4, "effective_strong_count": 3.2,
                    "single_stock_dominated": False,
                    "cross_day_persistent": False,
                    "cross_day_confirmed": False,
                    "mainline_confirmed": True,
                    "today_strength_score": 35.0,
                },
            },
            "stocks": {
                "600000": {
                    "amount_available": True,
                    "market_amount_percentile": 90.0,
                    "theme_amount_percentile": 75.0,
                    "turnover": 4.0,
                    "industry": "分歧分支", "theme_rank": 95.0,
                    "market_rank": 92.0, "strong_score": 92.0,
                    "strong": True, "role": "leader", "leader_rank": 1,
                    "leader_tier": True, "news_precheck": {},
                    "theme_profiles": [{
                        "industry": "分歧分支", "role": "leader",
                        "leader_rank": 1, "leader_tier": True,
                        "theme_rank": 95.0,
                    }],
                },
            },
        }

        result = score_niu_leader(rows, context)

        self.assertIsNotNone(result)
        self.assertEqual(result["industry"], "分歧分支")
        self.assertEqual(result["niuone_lifecycle_stage"], "divergence")
        self.assertNotIn(
            "主题不处于已确认主线或有效分歧", result["hard_blockers"]
        )
        self.assertNotIn(
            "主线未完成跨交易日核心股延续确认",
            result["hard_blockers"],
        )
        self.assertNotIn(
            "主题未进入当前主线/双主线", result["hard_blockers"]
        )
        self.assertNotIn(
            "牛牛领涨需题材当日强度≥60", result["hard_blockers"]
        )

    def test_single_strong_stock_does_not_force_a_mainline(self):
        prepared = self._prepared_market()
        for item in prepared:
            if item["industry"] == "半导体" and item["code"] != "600000":
                item["rows"] = make_rows(item["code"], "半导体", -0.005)
                item["quote"] = {"amount": 8e8}

        context = build_niuone_context(prepared)
        theme = context["themes"]["半导体"]

        self.assertTrue(theme["single_stock_dominated"])
        self.assertNotEqual(theme["state"], "mainline")
        self.assertEqual(context["mainline"]["mode"], "none")

    def test_today_metrics_surface_broad_rebound_without_rewriting_structure(self):
        prepared = self._prepared_market()
        today_changes = [6.0, 5.5, 5.0, 4.5]
        for item in prepared:
            item["quote"]["change_pct"] = -1.0
            if item["industry"] != "半导体":
                continue
            member_index = int(item["code"][-1])
            item["rows"] = make_rows(item["code"], "半导体", -0.08)
            item["quote"] = {
                "amount": 5e8,
                "change_pct": today_changes[member_index],
            }

        context = build_niuone_context(prepared)
        theme = context["themes"]["半导体"]

        self.assertEqual(theme["strong_stock_count"], 0)
        self.assertEqual(theme["effective_breadth_pct"], 0)
        self.assertEqual(theme["leader_concentration"], 0)
        self.assertEqual(theme["concentration_penalty"], 0)
        self.assertFalse(theme["single_stock_dominated"])
        self.assertTrue(theme["today_eligible_data"])
        self.assertEqual(theme["today_quote_count"], 4)
        self.assertEqual(theme["today_up_count"], 4)
        self.assertEqual(theme["today_3pct_count"], 4)
        self.assertEqual(theme["today_5pct_count"], 3)
        self.assertEqual(theme["today_breadth_pct"], 100)
        self.assertEqual(theme["today_attributed_breadth_pct"], 100)
        self.assertEqual(theme["today_adjusted_breadth_pct"], 75)
        self.assertEqual(theme["today_median_change_pct"], 5.25)
        self.assertEqual(theme["today_strength_score"], 72.92)
        self.assertEqual(theme["today_leadership_score"], 55)
        self.assertEqual(theme["today_leaders"][0]["change_pct"], 6)
        self.assertEqual(context["mainline"]["today_primary"], "半导体")
        self.assertEqual(context["mainline"]["today_primary_score"], 72.92)
        self.assertEqual(context["mainline"]["today_primary_breadth_pct"], 75)
        self.assertEqual(context["mainline"]["mode"], "none")

    def test_today_ranking_requires_fresh_quote_coverage(self):
        prepared = self._prepared_market()
        for item in prepared:
            item["quote"].pop("change_pct", None)
        semiconductor = [item for item in prepared if item["industry"] == "半导体"]
        semiconductor[0]["quote"]["change_pct"] = 6.0
        semiconductor[1]["quote"]["change_pct"] = 5.0

        context = build_niuone_context(prepared)
        theme = context["themes"]["半导体"]

        self.assertEqual(theme["today_quote_count"], 2)
        self.assertEqual(theme["today_data_coverage"], 0.5)
        self.assertFalse(theme["today_eligible_data"])
        self.assertEqual(context["mainline"]["today_primary"], "")

    def test_context_classifies_every_uncovered_reference_stock(self):
        valid = {
            "code": "600001",
            "name": "有效样本",
            "industry": "半导体",
            "quote": {"amount": 1.5e9},
            "rows": make_rows("600001", "半导体"),
        }
        missing_industry = {
            "code": "600002",
            "name": "无行业样本",
            "industry": "",
            "quote": {"amount": 1.5e9},
            "rows": make_rows("600002", ""),
        }
        insufficient = {
            "code": "600003",
            "name": "历史不足样本",
            "industry": "银行",
            "quote": {"amount": 1.5e9},
            "rows": make_rows("600003", "银行")[:40],
        }
        invalid_rows = make_rows("600004", "汽车")
        invalid_rows[-21]["close"] = 0
        invalid_metrics = {
            "code": "600004",
            "name": "指标无效样本",
            "industry": "汽车",
            "quote": {"amount": 1.5e9},
            "rows": invalid_rows,
        }

        context = build_niuone_context(
            [valid, missing_industry, insufficient, invalid_metrics],
            reference_pool_count=5,
        )

        diagnostics = context["coverage_diagnostics"]
        reasons = {reason["key"]: reason["count"] for reason in diagnostics["reasons"]}
        self.assertEqual(context["mapped_stock_count"], 1)
        self.assertEqual(context["data_coverage"], 0.2)
        self.assertEqual(diagnostics["uncovered_stock_count"], 4)
        self.assertEqual(reasons, {
            "kline_unavailable": 1,
            "insufficient_history": 1,
            "invalid_metrics": 1,
            "industry_unmapped": 1,
        })

    def test_context_sanitizes_non_finite_market_values_before_serialization(self):
        prepared = self._prepared_market()
        prepared[0]["quote"]["amount"] = float("nan")
        prepared[1]["quote"]["price"] = float("inf")

        context = build_niuone_context(
            prepared,
            market_snapshot={
                "up": 100,
                "down": 20,
                "median_change_pct": float("nan"),
                "limit_up": 5,
                "limit_down": 1,
            },
        )

        self.assertTrue(math.isfinite(context["market"]["score"]))
        json.dumps(context, ensure_ascii=False, allow_nan=False)

    def test_raw_defensive_market_uses_reduced_new_buy_budget(self):
        context = build_niuone_context(
            self._prepared_market(),
            market_snapshot={
                "up": 1,
                "down": 199,
                "median_change_pct": -1.5,
                "limit_up": 0,
                "limit_down": 20,
                "core_index_count": 0,
                "index_below_ma20_count": 0,
            },
            previous_context={
                "market": {
                    "state": "offensive",
                    "raw_state": "offensive",
                    "confirmation_count": 2,
                }
            },
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )

        market = context["market"]
        self.assertEqual(market["raw_state"], "defensive")
        self.assertEqual(market["risk_state"], "defensive")
        self.assertTrue(market["allow_new_buys"])
        self.assertEqual(market["per_trade_risk_pct"], 0.30)
        self.assertEqual(market["max_open_risk_pct"], 0.90)
        self.assertEqual(market["max_total_position_pct"], 20.0)

    def test_niuone_market_hard_stop_still_blocks_new_buys(self):
        context = build_niuone_context(
            self._prepared_market(),
            market_snapshot={
                "up": 100,
                "down": 300,
                "median_change_pct": -1.5,
                "limit_up": 1,
                "limit_down": 20,
                "core_index_count": 3,
                "index_below_ma20_count": 2,
            },
        )

        market = context["market"]
        self.assertEqual(market["risk_state"], "defensive")
        self.assertTrue(market["hard_stop"])
        self.assertFalse(market["allow_new_buys"])
        self.assertGreater(market["per_trade_risk_pct"], 0.0)

    def test_same_day_repeated_scans_remain_intraday_observation(self):
        prepared = self._prepared_market()
        first = build_niuone_context(
            prepared,
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )
        second = build_niuone_context(
            prepared,
            previous_context=first,
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )

        theme = second["themes"]["半导体"]
        self.assertEqual(theme["state"], "emerging")
        self.assertEqual(theme["intraday_state"], "intraday_mainline")
        self.assertEqual(theme["confirmation_count"], 1)
        self.assertEqual(theme["intraday_confirmation_count"], 2)
        self.assertFalse(theme["cross_day_confirmed"])
        self.assertEqual(second["mainline"]["mode"], "none")
        self.assertEqual(second["mainline"]["intraday_primary"], "半导体")

        rows = make_rows("600000", "半导体", 0.01)
        emerging = score_niu_emerging(rows, second)
        self.assertIsNotNone(emerging)
        self.assertFalse(emerging["actionable"])
        self.assertIn("启动主题尚未跨交易日延续", emerging["hard_blockers"])

    def test_changed_core_stocks_do_not_confirm_mainline_next_day(self):
        first = build_niuone_context(
            self._prepared_market(),
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )
        first["themes"]["半导体"]["core_stock_codes"] = ["601001", "601002", "601003"]
        second = build_niuone_context(
            self._prepared_market(),
            previous_context=first,
            as_of_date="2026-07-28",
            previous_trading_day="2026-07-27",
        )

        theme = second["themes"]["半导体"]
        self.assertEqual(theme["state"], "emerging")
        self.assertEqual(theme["core_overlap_count"], 0)
        self.assertFalse(theme["core_continuity_met"])
        self.assertFalse(theme["cross_day_confirmed"])

    def test_context_lifecycle_uses_previous_causal_stage(self):
        first = build_niuone_context(
            self._prepared_market(),
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )
        previous_theme = first["themes"]["半导体"]
        previous_theme.update({
            "state": "emerging",
            "raw_state": "mainline",
            "mainline_confirmed": False,
            "cross_day_confirmed": False,
            "cross_day_persistent": True,
            "niuone_lifecycle_stage": "markup",
            "niuone_lifecycle_label": "主线主升",
            "niuone_lifecycle_order": 20,
            "niuone_lifecycle_entry_policy": "participate",
            "core_stock_codes": ["601001", "601002", "601003"],
        })

        current = build_niuone_context(
            self._prepared_market(),
            previous_context=first,
            as_of_date="2026-07-28",
            previous_trading_day="2026-07-27",
        )

        theme = current["themes"]["半导体"]
        self.assertEqual(theme["state"], "emerging")
        self.assertFalse(theme["cross_day_persistent"])
        self.assertEqual(theme["niuone_lifecycle_stage"], "divergence")
        self.assertEqual(theme["niuone_lifecycle_label"], "主线分歧")
        self.assertEqual(
            theme["niuone_lifecycle_entry_policy"],
            "selective_repair_reclaim_or_reduce",
        )

    def test_legacy_same_day_mainline_cache_is_not_trusted_as_cross_day_confirmation(self):
        prepared = self._prepared_market()
        legacy = build_niuone_context(
            prepared,
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )
        legacy["version"] = 1
        theme = legacy["themes"]["半导体"]
        theme["state"] = "mainline"
        theme.pop("mainline_confirmed", None)
        theme.pop("cross_day_confirmed", None)

        current = build_niuone_context(
            prepared,
            previous_context=legacy,
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )

        self.assertEqual(current["themes"]["半导体"]["state"], "emerging")
        self.assertFalse(current["themes"]["半导体"]["cross_day_confirmed"])

    def test_scorer_uses_mainline_context_and_ema_hard_gates(self):
        rows = make_rows("600000", "半导体", 0.02)
        context = {
            "market": {"state": "offensive", "score": 78, "hard_stop": False, "allow_new_buys": True},
            "mainline": {"mode": "single", "primary": "半导体"},
            "dragon_tiger": {"available": False},
            "news": {"configured": False},
            "themes": {
                "半导体": {
                    "state": "mainline", "raw_state": "mainline", "score": 77.9,
                    "member_count": 8, "eligible_data": True, "strong_stock_count": 4,
                    "effective_strong_count": 3.5, "leader_concentration": 0.3,
                    "single_stock_dominated": False, "confirmation_count": 2, "state_streak": 2,
                    "cross_day_persistent": True, "cross_day_confirmed": True,
                    "mainline_confirmed": True, "core_overlap_count": 3,
                    "today_strength_score": 80.0,
                }
            },
            "stocks": {
                "600000": {
                    "amount_available": True,
                    "market_amount_percentile": 90.0,
                    "theme_amount_percentile": 75.0,
                    "turnover": 4.0,
                    "theme_rank": 95, "market_rank": 92, "strong_score": 92,
                    "strong": True, "role": "leader", "leader_rank": 1,
                    "leader_tier": True, "news_precheck": {},
                }
            },
        }

        result = score_niu_leader(rows, context)

        self.assertIsNotNone(result)
        self.assertEqual(result["mainline_state"], "mainline")
        self.assertEqual(result["stock_role"], "leader")
        self.assertEqual(result["stop_source"], "niu_structure_low")
        self.assertEqual(result["atr_period"], 14)
        self.assertEqual(result["atr"], result["atr20"])
        self.assertEqual(result["per_trade_risk_budget_pct"], 1.5)
        self.assertFalse(any("BBI" in blocker for blocker in result["hard_blockers"]))

        context["stocks"]["600000"].update({
            "role": "core",
            "leader_rank": 2,
            "leader_tier": True,
            "theme_rank": 66,
        })
        second_rank = score_niu_leader(rows, context)
        self.assertIsNotNone(second_rank)
        self.assertEqual(second_rank["stock_leader_rank"], 2)
        self.assertTrue(second_rank["stock_leader_tier"])
        self.assertNotIn("个股未进入强势行业龙头梯队", second_rank["hard_blockers"])
        self.assertNotIn(
            "牛牛领涨需题材当日强度≥60",
            second_rank["hard_blockers"],
        )

        edge_leader = with_strategy_profile("niu_leader", {
            "score": 9.0,
            "stock_leader_rank": 3,
            "stock_leader_tier": True,
            "stock_strong": True,
            "stock_sector_rank": 90.0,
            "today_strength_score": 59.99,
            "risk_flags": [],
        })
        self.assertIn(
            "牛牛领涨需题材当日强度≥60",
            edge_leader["hard_blockers"],
        )
        confirmed_edge_leader = with_strategy_profile("niu_leader", {
            **edge_leader,
            "today_strength_score": 60.0,
        })
        self.assertNotIn(
            "牛牛领涨需题材当日强度≥60",
            confirmed_edge_leader["hard_blockers"],
        )
        outside_top_twenty = with_strategy_profile("niu_leader", {
            **confirmed_edge_leader,
            "stock_sector_rank": 79.99,
            "today_strength_score": 80.0,
        })
        self.assertIn(
            "牛牛领涨个股需处于主线前20%",
            outside_top_twenty["hard_blockers"],
        )
        self.assertIn(
            "牛牛领涨需题材当日强度≥60",
            trader.candidate_buy_blockers(niu_candidate(
                stock_role="core",
                stock_leader_rank=1,
                stock_leader_tier=True,
                today_strength_score=59.99,
            )),
        )

        rows[-1]["quote_change_pct"] = 5.1
        expanded = score_niu_leader(rows, context)
        self.assertIsNotNone(expanded)
        self.assertNotIn("领涨动作单日涨幅>4%", expanded["hard_blockers"])
        self.assertFalse(any(
            "单日涨幅" in blocker for blocker in expanded["hard_blockers"]
        ))

        rows[-1]["quote_change_pct"] = 7.1
        chased = score_niu_leader(rows, context)
        self.assertIsNotNone(chased)
        self.assertFalse(any(
            "单日涨幅" in blocker for blocker in chased["hard_blockers"]
        ))

        payload = with_strategy_profile("niu_leader", {
            "stock_activity_data_available": True,
            "stock_market_amount_percentile": 90.0,
            "stock_theme_amount_percentile": 75.0,
            "turnover": 4.0,
            "score": 9.0,
            "distance_pct": 10.0,
            "extension_atr": 1.0,
            "market_allows_buys": True,
            "market_hard_stop": False,
            "market_regime": "offensive",
            "sector_data_eligible": True,
            "sector_status": "mainline",
            "mainline_score": 77.9,
            "mainline_selected": True,
            "mainline_cross_day_confirmed": True,
            "mainline_confirmed": True,
            "single_stock_dominated": False,
            "stock_strong": True,
            "stock_role": "leader",
            "stock_leader_rank": 1,
            "stock_leader_tier": True,
            "stock_sector_rank": 90,
            "today_strength_score": 80.0,
            "breakout": True,
            "pullback": False,
            "risk_ok": True,
            "effective_loss_distance_pct": 5.0,
            "max_position_pct_by_risk": 5.0,
            "risk_flags": [],
        })
        self.assertTrue(payload["actionable"])
        self.assertEqual(payload["strategy_id"], "niu_leader")
        self.assertTrue(candidate_is_trade_ready(payload))
        self.assertTrue(candidate_is_trade_ready({**payload, "best_strategy": "niu_leader", "best_score": 9.0}))
        self.assertTrue(candidate_is_trade_ready({
            **payload,
            "best_strategy": "niu_leader",
            "best_score": 9.0,
            "stock_role": "core",
            "stock_leader_rank": 2,
        }))
        self.assertFalse(candidate_is_trade_ready({
            **payload,
            "best_strategy": "niu_leader",
            "best_score": 9.0,
            "stock_role": "core",
            "stock_leader_rank": 4,
            "stock_leader_tier": False,
        }))

    def test_niuone_entry_limits_expand_only_in_stronger_market_states(self):
        self.assertEqual(
            niuone_chase_limits("niu_leader", "offensive"),
            {"max_entry_extension_atr": 1.5},
        )
        self.assertEqual(
            niuone_chase_limits("niu_leader", "rotation"),
            {"max_entry_extension_atr": 1.25},
        )
        self.assertTrue(niuone_structure_risk_ok(9.5, 2.4, "offensive"))
        self.assertTrue(niuone_structure_risk_ok(7.5, 1.9, "rotation"))
        self.assertFalse(niuone_structure_risk_ok(8.1, 1.9, "rotation"))
        self.assertFalse(niuone_structure_risk_ok(6.1, 1.5, "recovery"))

        payload = with_strategy_profile("niu_leader", {
            "stock_activity_data_available": True,
            "stock_market_amount_percentile": 90.0,
            "stock_theme_amount_percentile": 75.0,
            "turnover": 4.0,
            "score": 9.0,
            "extension_atr": 1.25,
            "max_entry_change_pct": 5.0,
            "max_entry_extension_atr": 1.25,
            "change_pct": 5.000000000000003,
            "market_allows_buys": True,
            "market_hard_stop": False,
            "market_regime": "rotation",
            "sector_data_eligible": True,
            "sector_status": "mainline",
            "mainline_selected": True,
            "mainline_cross_day_confirmed": True,
            "single_stock_dominated": False,
            "stock_strong": True,
            "stock_leader_tier": True,
            "stock_sector_rank": 90.0,
            "today_strength_score": 80.0,
            "breakout": True,
            "pullback": False,
            "risk_ok": True,
            "effective_loss_distance_pct": 7.0,
            "max_position_pct_by_risk": 10.0,
            "risk_flags": [],
        })
        self.assertTrue(payload["actionable"])
        self.assertNotIn("领涨动作单日涨幅>5%", payload["hard_blockers"])

        non_limit_breakout = with_strategy_profile(
            "niu_leader",
            {**payload, "change_pct": 8.84},
        )
        self.assertTrue(non_limit_breakout["actionable"])
        self.assertFalse(any(
            "单日涨幅" in blocker
            for blocker in non_limit_breakout["hard_blockers"]
        ))

    def test_breakout_uses_prior_high_instead_of_ema20_as_entry_anchor(self):
        rows = make_rows("600000", "半导体", 0.08)
        breakout_level = max(float(row["high"]) for row in rows[-21:-1])
        rows[-1].update({
            "open": float(rows[-2]["close"]) * 1.002,
            "close": breakout_level * 1.004,
            "high": breakout_level * 1.007,
            "low": float(rows[-2]["close"]) * 0.998,
            "volume": 1500.0,
        })
        enrich_rows(rows)
        rows[-1].update({
            "quote_turnover": 4.0,
            "symbol_code": "600000",
            "stock_name": "突破测试",
            "industry": "半导体",
            "quote_amount": 2.5e9,
        })
        context = {
            "market": {
                "state": "offensive", "risk_state": "offensive", "score": 78,
                "hard_stop": False, "allow_new_buys": True,
            },
            "mainline": {"mode": "single", "primary": "半导体"},
            "dragon_tiger": {"available": False},
            "news": {"configured": False},
            "themes": {
                "半导体": {
                    "state": "mainline", "raw_state": "mainline", "score": 77.9,
                    "member_count": 8, "eligible_data": True, "strong_stock_count": 4,
                    "effective_strong_count": 3.5, "leader_concentration": 0.3,
                    "single_stock_dominated": False, "confirmation_count": 2,
                    "state_streak": 2, "cross_day_persistent": True,
                    "cross_day_confirmed": True, "mainline_confirmed": True,
                    "core_overlap_count": 3, "today_strength_score": 80.0,
                }
            },
            "stocks": {
                "600000": {
                    "amount_available": True,
                    "market_amount_percentile": 90.0,
                    "theme_amount_percentile": 75.0,
                    "turnover": 4.0,
                    "theme_rank": 95, "market_rank": 92, "strong_score": 92,
                    "strong": True, "role": "leader", "leader_rank": 1,
                    "leader_tier": True, "news_precheck": {},
                }
            },
        }

        result = score_niu_leader(rows, context)

        self.assertIsNotNone(result)
        self.assertTrue(result["breakout"])
        self.assertGreater(result["extension_atr"], 1.5)
        self.assertLess(result["entry_extension_atr"], 1.5)
        self.assertEqual(result["entry_extension_source"], "breakout_level")
        self.assertEqual(result["entry_setup"], "breakout")
        self.assertEqual(result["stop_source"], "niu_breakout_pivot")
        self.assertTrue(result["risk_ok"])
        self.assertTrue(result["actionable"])
        self.assertFalse(any("距EMA20" in item for item in result["hard_blockers"]))

        overrun = with_strategy_profile("niu_leader", {
            **result,
            "score": 9.0,
            "entry_extension_atr": 1.51,
            "max_entry_extension_atr": 1.5,
        })
        self.assertFalse(overrun["actionable"])
        self.assertIn("领涨动作突破价超过前高1.5ATR", overrun["hard_blockers"])

    def test_pullback_keeps_ema20_as_entry_anchor(self):
        payload = with_strategy_profile("niu_pullback", {
            "stock_activity_data_available": True,
            "stock_market_amount_percentile": 90.0,
            "stock_theme_amount_percentile": 75.0,
            "turnover": 4.0,
            "score": 9.0,
            "extension_atr": 1.26,
            "entry_extension_atr": 1.26,
            "entry_extension_source": "ema20",
            "max_entry_change_pct": 5.0,
            "max_entry_extension_atr": 1.25,
            "change_pct": 1.0,
            "market_allows_buys": True,
            "market_hard_stop": False,
            "market_regime": "offensive",
            "sector_data_eligible": True,
            "sector_status": "diverging",
            "niuone_lifecycle_stage": "divergence",
            "mainline_score": 75,
            "mainline_confirmed": True,
            "mainline_selected": True,
            "stock_strong": True,
            "stock_leader_tier": True,
            "pullback": True,
            "reclaim": False,
            "risk_ok": True,
            "effective_loss_distance_pct": 4.0,
            "max_position_pct_by_risk": 10.0,
            "risk_flags": [],
        })

        self.assertFalse(payload["actionable"])
        self.assertIn("转强动作距EMA20超过1.25ATR", payload["hard_blockers"])

    def test_emerging_accepts_only_cross_day_emerging_theme_without_relaxing_chase_risk(self):
        payload = {
            "stock_activity_data_available": True,
            "stock_market_amount_percentile": 90.0,
            "stock_theme_amount_percentile": 75.0,
            "turnover": 4.0,
            "score": 8.399999999,
            "entry_extension_atr": 1.4,
            "entry_extension_source": "breakout_level",
            "max_entry_change_pct": 7.0,
            "max_entry_extension_atr": 1.5,
            "change_pct": 6.8,
            "market_allows_buys": True,
            "market_hard_stop": False,
            "market_regime": "rotation",
            "sector_data_eligible": True,
            "sector_status": "emerging",
            "mainline_score": 72.0,
            "mainline_cross_day_persistent": True,
            "mainline_confirmed": False,
            "strong_stock_count": 3,
            "single_stock_dominated": False,
            "stock_strong": True,
            "stock_leader_tier": True,
            "breakout": True,
            "reclaim": False,
            "risk_ok": True,
            "effective_loss_distance_pct": 5.0,
            "max_position_pct_by_risk": 8.0,
            "risk_flags": [],
        }

        emerging = with_strategy_profile("niu_emerging", payload)
        self.assertEqual(emerging["score"], 8.4)
        self.assertTrue(emerging["actionable"])
        self.assertNotIn(
            "主题不处于跨日延续的待确认启动阶段",
            emerging["hard_blockers"],
        )

        confirmed_mainline = with_strategy_profile(
            "niu_emerging",
            {
                **payload,
                "sector_status": "mainline",
                "mainline_score": 77.9,
                "mainline_confirmed": True,
            },
        )
        self.assertFalse(confirmed_mainline["actionable"])
        self.assertIn(
            "主题不处于跨日延续的待确认启动阶段",
            confirmed_mainline["hard_blockers"],
        )
        self.assertIn(
            "主题不处于跨日延续的待确认启动阶段",
            trader.candidate_buy_blockers(niu_candidate(
                best_strategy="niu_emerging",
                entry_threshold=8.4,
                mainline_state="mainline",
                sector_status="mainline",
            )),
        )
        self.assertIn(
            "启动主题尚未跨交易日延续",
            trader.candidate_buy_blockers(niu_candidate(
                best_strategy="niu_emerging",
                entry_threshold=8.4,
                mainline_state="emerging",
                sector_status="emerging",
                mainline_cross_day_persistent=False,
            )),
        )

        chased = with_strategy_profile(
            "niu_emerging",
            {**payload, "change_pct": 7.01},
        )
        self.assertTrue(chased["actionable"])
        self.assertFalse(any(
            "单日涨幅" in blocker for blocker in chased["hard_blockers"]
        ))

        unsafe_stop = with_strategy_profile(
            "niu_emerging",
            {**payload, "risk_ok": False},
        )
        self.assertFalse(unsafe_stop["actionable"])
        self.assertTrue(
            any(item.startswith("结构止损超过") for item in unsafe_stop["hard_blockers"])
        )

    def test_markup_momentum_probe_is_conditional_and_sizes_wide_stop_to_micro_position(self):
        payload = {
            "stock_activity_data_available": True,
            "stock_market_amount_percentile": 90.0,
            "stock_theme_amount_percentile": 75.0,
            "turnover": 4.0,
            "score": 8.0,
            "market_allows_buys": True,
            "market_hard_stop": False,
            "market_regime": "recovery",
            "sector_data_eligible": True,
            "sector_status": "emerging",
            "mainline_state": "emerging",
            "niuone_lifecycle_stage": "markup",
            "mainline_cross_day_persistent": True,
            "mainline_confirmed": False,
            "strong_stock_count": 3,
            "single_stock_dominated": False,
            "stock_strong": True,
            "stock_leader_tier": True,
            "stock_leader_rank": 1,
            "stock_strong_score": 92.92,
            "breakout": False,
            "reclaim": False,
            "entry_extension_atr": 3.02,
            "entry_extension_source": "ema20",
            "change_pct": 10.01,
            "volume_ratio": 1.0,
            "stop_distance_pct": 17.87,
            "stop_atr": 2.8,
            "effective_loss_distance_pct": 20.5,
            "risk_ok": False,
            "max_stop_distance_pct": 6.0,
            "max_stop_atr": 1.5,
            "max_position_pct_by_risk": 0.0,
            "risk_flags": [
                "结构止损超过当前行情上限(6%或1.5ATR)",
                "启动买点尚未确认",
                "启动战法拒绝追高",
            ],
        }

        probe = with_strategy_profile(
            "niu_emerging",
            _apply_markup_momentum_probe(dict(payload)),
        )

        self.assertEqual(
            probe["niuone_entry_subroute"],
            NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
        )
        self.assertEqual(probe["entry_threshold"], 8.0)
        self.assertTrue(probe["niuone_markup_momentum_acceleration"])
        self.assertTrue(probe["actionable"])
        self.assertTrue(probe["risk_ok"])
        self.assertEqual(
            probe["absolute_position_cap_pct"],
            NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT,
        )
        self.assertGreater(probe["max_position_pct_by_risk"], 0)
        self.assertLessEqual(
            probe["max_position_pct_by_risk"],
            NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT,
        )
        self.assertNotIn("启动买点未确认", probe["hard_blockers"])

        rank_two = with_strategy_profile(
            "niu_emerging",
            _apply_markup_momentum_probe({**payload, "stock_leader_rank": 2}),
        )
        self.assertNotIn("niuone_entry_subroute", rank_two)
        self.assertEqual(rank_two["entry_threshold"], 8.4)
        self.assertFalse(rank_two["actionable"])
        self.assertIn("启动买点未确认", rank_two["hard_blockers"])

        ordinary = with_strategy_profile(
            "niu_emerging",
            _apply_markup_momentum_probe({
                **payload,
                "score": NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MIN_SCORE,
                "mainline_score": 70.0,
                "entry_extension_atr": 1.0,
                "change_pct": 5.0,
                "volume_ratio": 1.5,
                "stop_distance_pct": 8.0,
                "stop_atr": 1.2,
            }),
        )
        self.assertTrue(ordinary["actionable"])
        self.assertEqual(
            ordinary["entry_threshold"],
            NIUONE_MARKUP_MOMENTUM_PROBE_ORDINARY_MIN_SCORE,
        )
        self.assertFalse(ordinary["niuone_markup_momentum_acceleration"])

        middle_extension = _apply_markup_momentum_probe({
            **payload,
            "score": 8.2,
            "mainline_score": 72.0,
            "entry_extension_atr": 1.2,
            "change_pct": 9.8,
            "volume_ratio": 1.1,
        })
        self.assertNotIn("niuone_entry_subroute", middle_extension)

        low_ordinary_score = _apply_markup_momentum_probe({
            **payload,
            "entry_extension_atr": 0.8,
            "mainline_score": 72.0,
            "change_pct": 5.0,
            "volume_ratio": 1.5,
        })
        self.assertNotIn("niuone_entry_subroute", low_ordinary_score)

        weak_ordinary_theme = _apply_markup_momentum_probe({
            **payload,
            "score": 8.2,
            "entry_extension_atr": 0.8,
            "mainline_score": 69.99,
            "change_pct": 5.0,
            "volume_ratio": 1.5,
        })
        self.assertNotIn("niuone_entry_subroute", weak_ordinary_theme)

        explosive_acceleration_volume = _apply_markup_momentum_probe({
            **payload,
            "entry_extension_atr": 3.0,
            "change_pct": 10.0,
            "volume_ratio": 1.21,
        })
        self.assertNotIn(
            "niuone_entry_subroute",
            explosive_acceleration_volume,
        )

        execution_recheck = trader.candidate_buy_blockers({
            **probe,
            "best_strategy": "niu_emerging",
            "best_score": probe["score"],
            "stock_leader_rank": 2,
            "hard_blockers": [],
        })
        self.assertIn(
            "主升动量试仓身份条件不完整",
            execution_recheck,
        )

        unsafe = with_strategy_profile(
            "niu_emerging",
            _apply_markup_momentum_probe({
                **payload,
                "stop_distance_pct": 18.01,
            }),
        )
        self.assertFalse(unsafe["actionable"])
        self.assertTrue(any(
            item.startswith("结构止损超过当前行情上限")
            for item in unsafe["hard_blockers"]
        ))

    def test_all_niuone_profiles_require_the_strong_industry_leader(self):
        for strategy_id in ("niu_leader", "niu_pullback", "niu_emerging"):
            with self.subTest(strategy_id=strategy_id):
                blocked = with_strategy_profile(strategy_id, {
                    "score": 10.0,
                    "stock_role": "follower",
                    "stock_leader_rank": 4,
                    "stock_leader_tier": False,
                    "stock_strong": True,
                    "risk_flags": [],
                })
                self.assertIn("个股未进入强势行业龙头梯队", blocked["hard_blockers"])

                leader_tier = with_strategy_profile(strategy_id, {
                    "score": 10.0,
                    "stock_role": "core",
                    "stock_leader_rank": 2,
                    "stock_leader_tier": True,
                    "stock_strong": True,
                    "risk_flags": [],
                })
                self.assertNotIn("个股未进入强势行业龙头梯队", leader_tier["hard_blockers"])

                weak_leader = with_strategy_profile(strategy_id, {
                    "score": 10.0,
                    "stock_role": "leader",
                    "stock_leader_rank": 1,
                    "stock_leader_tier": True,
                    "stock_strong": False,
                    "risk_flags": [],
                })
                self.assertIn("个股未进入强势行业龙头梯队", weak_leader["hard_blockers"])

    def test_execution_enforces_niuone_budget_and_persists_mainline_marks(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"turnover": 4.0, "price": 10.0, "name": "牛牛测试", "source": "test"}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {"actions": [{"action": "BUY", "code": "600000", "shares": 2400, "reason": "牛牛领航确认"}]}

            executed = trader.execute_actions(state, decision, [niu_candidate()], True, "连续竞价交易时段", market)

            self.assertEqual(len(executed), 1)
            action = decision["actions"][0]
            self.assertEqual(action["model_requested_shares"], 2400)
            self.assertEqual(action["maximum_permitted_shares"], 2400)
            self.assertEqual(action["risk_ceiling_utilization_pct"], 100.0)
            self.assertFalse(action["risk_ceiling_auto_reduced"])
            self.assertIn(
                "single_name_risk",
                action["risk_ceiling_binding_constraints"],
            )
            self.assertEqual(executed[0]["maximum_permitted_shares"], 2400)
            self.assertFalse(executed[0]["risk_ceiling_auto_reduced"])
            pos = state["positions"]["600000"]
            self.assertEqual(pos["entry_stop_source"], "niu_structure_low")
            self.assertEqual(pos["entry_atr"], 0.3)
            self.assertEqual(pos["entry_atr_period"], 14)
            self.assertEqual(pos["entry_atr20"], 0.3)
            self.assertEqual(pos["mainline_state"], "mainline")
            self.assertEqual(pos["stock_role"], "leader")
            self.assertEqual(pos["risk_budget_regime"], "offensive")
            self.assertEqual(pos["entry_market_regime"], "offensive")
            self.assertEqual(pos["entry_signal_score"], 9.0)
            self.assertEqual(pos["last_buy_signal_score"], 9.0)
            self.assertEqual(pos["highest_buy_signal_score"], 9.0)
            self.assertEqual(pos["niuone_buy_signal_count"], 1)
            self.assertGreater(pos["position_open_risk_pct"], 1.4)
            self.assertLessEqual(pos["position_open_risk_pct"], 1.5)

            reduced_state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            reduced_decision = {"actions": [{"action": "BUY", "code": "600000", "shares": 2500, "reason": "牛牛领航确认"}]}
            reduced = trader.execute_actions(reduced_state, reduced_decision, [niu_candidate()], True, "连续竞价交易时段", market)
            self.assertEqual(len(reduced), 1)
            reduced_action = reduced_decision["actions"][0]
            self.assertEqual(reduced_action["shares"], 2400)
            self.assertEqual(reduced_action["model_requested_shares"], 2500)
            self.assertEqual(reduced_action["maximum_permitted_shares"], 2400)
            self.assertEqual(
                reduced_action["risk_ceiling_utilization_pct"],
                100.0,
            )
            self.assertTrue(reduced_action["risk_ceiling_auto_reduced"])
            self.assertEqual(reduced_state["positions"]["600000"]["qty"], 2400)
            self.assertEqual(
                reduced_state["positions"]["600000"][
                    "entry_model_requested_shares"
                ],
                2500,
            )
            self.assertEqual(
                reduced_state["positions"]["600000"][
                    "entry_executed_shares"
                ],
                2400,
            )
            self.assertTrue(
                reduced_state["positions"]["600000"][
                    "entry_risk_ceiling_auto_reduced"
                ]
            )
            self.assertEqual(reduced[0]["shares"], 2400)
            self.assertEqual(reduced[0]["model_requested_shares"], 2500)
            self.assertTrue(reduced[0]["risk_ceiling_auto_reduced"])
            self.assertEqual(reduced_decision["execution_blocks"], [])
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_holding_fast_cycle_cannot_open_a_new_position(self):
        state = {"cash": 100000.0, "positions": {}, "trade_log": []}
        decision = {
            "holding_cycle_only": True,
            "decision_cycle_kind": "holding_fast",
            "actions": [{
                "action": "BUY",
                "code": "600000",
                "shares": 100,
                "reason": "快周期模型建议买入",
            }],
        }
        market = {
            "allow_new_buys": True,
            "max_open_positions": 6,
            "max_new_buys_per_decision": 2,
            "max_total_position_pct": 80,
            "min_cash_reserve_pct": 20,
        }

        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda _now=None: (
                True,
                "连续竞价交易时段",
            )
            trader.execution_quote = lambda _code: {
                "turnover": 4.0,
                "price": 10.0,
                "name": "牛牛测试",
                "source": "test",
            }
            executed = trader.execute_actions(
                state,
                decision,
                [niu_candidate()],
                True,
                "连续竞价交易时段",
                market,
            )
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

        self.assertEqual(executed, [])
        self.assertNotIn("600000", state["positions"])
        self.assertEqual(
            decision["execution_blocks"][0]["category"],
            "candidate_eligibility",
        )
        self.assertIn("不允许首次建仓", decision["execution_blocks"][0]["reason"])

    def test_execution_allows_defensive_niuone_opening_with_reduced_budget(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (
                True,
                "连续竞价交易时段",
            )
            trader.execution_quote = lambda code: {
                "turnover": 4.0,
                "price": 10.0,
                "name": "防守开仓测试",
                "source": "test",
            }
            budget = niuone_risk_budget("defensive", "niu_leader")
            candidate = niu_candidate(
                market_regime="defensive",
                stop_price=9.6,
                stop_distance_pct=4.0,
                effective_loss_distance_pct=5.2,
                per_trade_risk_budget_pct=budget["per_trade_risk_pct"],
                max_open_risk_pct=budget["max_open_risk_pct"],
                max_sector_risk_pct=budget["max_sector_risk_pct"],
                max_total_position_pct=budget["max_total_position_pct"],
                max_sector_position_pct=budget["max_sector_position_pct"],
                max_position_pct_by_risk=5.76,
            )
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {"actions": [{
                "action": "BUY",
                "code": "600000",
                "shares": 100,
                "reason": "牛牛领涨防守轻仓",
            }]}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 2,
                "max_new_buys_per_decision": 1,
                "max_total_position_pct": 35,
                "min_cash_reserve_pct": 60,
            }

            executed = trader.execute_actions(
                state,
                decision,
                [candidate],
                True,
                "连续竞价交易时段",
                market,
            )

            self.assertEqual(len(executed), 1)
            self.assertEqual(
                state["positions"]["600000"]["entry_market_regime"],
                "defensive",
            )
            self.assertLessEqual(
                state["positions"]["600000"]["position_open_risk_pct"],
                0.30,
            )
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_legacy_selected_score_uses_probe_gates_and_ceiling_at_execution(self):
        subroute = NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE
        facts = {"niuone_entry_subroute": subroute, "entry_setup": subroute,
                 "entry_extension_atr": 0.5, "volume_ratio": 1.0}
        candidate = niu_candidate(best_strategy="niu_emerging", best_score=8.8, entry_threshold=8.1,
                                  niuone_lifecycle_stage="markup", mainline_state="emerging", sector_status="emerging",
                                  mainline_cross_day_persistent=True, price=8, recent_close=8, stop_price=7.8, atr=.2,
                                  strategies={"niu_emerging": facts, "niu_leader": {"entry_setup": "unrelated"}})
        market = {"allow_new_buys": True, "max_open_positions": 6, "max_new_buys_per_decision": 2,
                  "max_total_position_pct": 80, "min_cash_reserve_pct": 20}
        self.assertEqual(trader.decision_candidate_rows({"trade_items": [], "items": [candidate]}), [])
        complete = trader.observed_candidate_rows({"items": [candidate]})[0]
        self.assertEqual(complete["entry_setup"], subroute)
        self.assertNotIn("entry_setup", candidate)
        self.assertEqual(trader.candidate_buy_blockers(candidate), [])
        self.assertIn("主升动量试仓身份条件不完整",
                      trader.candidate_buy_blockers({**candidate, "stock_leader_rank": 2}))
        for price, expected in ((8.01, 0), (8.00, 100), (6.40, 100)):
            with self.subTest(price=price):
                row = {**candidate, "price": price, "stop_price": price - .2, "recent_close": price}
                state = {"cash": 20000., "initial_cash": 20000., "positions": {}, "trade_log": []}
                decision = {"actions": [{"action": "BUY", "code": row["code"], "shares": 300, "reason": "试仓"}]}
                with patch.object(trader, "execution_quote", return_value={
                    "price": price, "prev_close": price, "turnover": 4.0, "name": "合成样本", "source": "test",
                }), patch.object(trader, "is_a_share_execution_time", return_value=(True, "test")):
                    filled = trader.execute_actions(state, decision, [row], True, "test", market)
                self.assertEqual(sum(order["shares"] for order in filled), expected, decision.get("execution_blocks"))
                self.assertEqual(trader.niuone_opening_position_cap("niu_emerging", complete, opening=False), 15)

    def test_markup_momentum_probe_execution_accepts_wide_stop_but_caps_position(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (
                True,
                "连续竞价交易时段",
            )
            trader.execution_quote = lambda code: {
                "turnover": 4.0,
                "price": 38.81,
                "prev_close": 38.0,
                "name": "主升动量测试",
                "source": "test",
            }
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }
            candidate = niu_candidate(
                best_strategy="niu_emerging",
                best_score=8.0,
                entry_threshold=8.0,
                mainline_state="emerging",
                sector_status="emerging",
                mainline_cross_day_persistent=True,
                mainline_confirmed=False,
                niuone_lifecycle_stage="markup",
                niuone_entry_subroute=(
                    NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE
                ),
                recent_close=38.0,
                stop_price=32.08,
                stop_distance_pct=17.35,
                atr=2.5,
                atr20=2.5,
                entry_extension_atr=3.02,
                change_pct=10.01,
                volume_ratio=1.0,
                gap_buffer_pct=1.0,
                effective_loss_distance_pct=18.55,
                risk_ok=True,
            )
            state = {
                "cash": 1_000_000.0,
                "positions": {},
                "trade_log": [],
            }
            decision = {"actions": [{
                "action": "BUY",
                "code": "600000",
                "shares": 2000,
                "reason": "主升动量试仓",
            }]}

            executed = trader.execute_actions(
                state,
                decision,
                [candidate],
                True,
                "连续竞价交易时段",
                market,
            )

            self.assertEqual(len(executed), 1)
            action = decision["actions"][0]
            self.assertLess(action["shares"], 2000)
            self.assertTrue(action["risk_ceiling_auto_reduced"])
            self.assertLessEqual(action["position_after_trade_pct"], 4.0)
            self.assertEqual(
                action["niuone_entry_subroute"],
                NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
            )
            self.assertEqual(
                state["positions"]["600000"]["absolute_position_cap_pct"],
                4.0,
            )
            self.assertEqual(
                state["positions"]["600000"]["niuone_entry_subroute"],
                NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
            )

            trader.execution_quote = lambda code: {
                "turnover": 4.0,
                "price": 41.0,
                "prev_close": 38.0,
                "name": "主升动量测试",
                "source": "test",
            }
            blocked_state = {
                "cash": 1_000_000.0,
                "positions": {},
                "trade_log": [],
            }
            blocked_decision = {"actions": [{
                "action": "BUY",
                "code": "600000",
                "shares": 100,
                "reason": "主升动量试仓",
            }]}
            blocked = trader.execute_actions(
                blocked_state,
                blocked_decision,
                [candidate],
                True,
                "连续竞价交易时段",
                market,
            )
            self.assertEqual(blocked, [])
            self.assertTrue(any(
                f"超过{NIUONE_MARKUP_MOMENTUM_PROBE_MAX_EXECUTION_GAP_PCT:g}%"
                in item["reason"]
                for item in blocked_decision["execution_blocks"]
            ))
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_dynamic_risk_order_ceiling_accounts_for_fee_reserve_and_lots(self):
        ceiling = trader.dynamic_risk_order_ceiling(
            price=10.0,
            total_equity=100000.0,
            cash=100000.0,
            current_position_value=0.0,
            current_market_value=0.0,
            other_industry_value=0.0,
            dynamic_position_cap_pct=100.0,
            total_position_cap_pct=100.0,
            sector_position_cap_pct=100.0,
            effective_loss_distance_pct_value=5.0,
            max_open_risk_pct=5.0,
            existing_open_risk_pct=0.0,
            max_sector_risk_pct=5.0,
            existing_sector_risk_pct=0.0,
            required_cash_pct=20.0,
        )

        self.assertEqual(ceiling["maximum_permitted_shares"], 7900)
        self.assertEqual(ceiling["maximum_permitted_gross"], 79000.0)
        self.assertIn(
            "cash_reserve_after_fees",
            ceiling["binding_constraints"],
        )

    def test_reversal_new_entry_rechecks_price_and_requires_quote_previous_close(self):
        for previous_close in (10.0, None, 0.0, float("nan"), float("inf")):
            with self.subTest(previous_close=previous_close), patch.object(
                trader, "is_a_share_execution_time", return_value=(True, "连续竞价交易时段"),
            ), patch.object(trader, "execution_quote", return_value={
                "turnover": 4.0,
                "price": 10.3, "prev_close": previous_close, "source": "test",
            }):
                state = {"cash": 100000.0, "positions": {}, "trade_log": []}
                decision = {"actions": [{
                    "action": "BUY", "code": "600000", "shares": 100,
                    "reason": "牛牛试仓确认",
                }]}
                # The scan price/percentage cannot replace the quote's previous close.
                candidate = reversal_candidate(
                    recent_close=10.3, change_pct=0.5, stop_price=10.0,
                )
                for _ in range(2):
                    executed = trader.execute_actions(
                        state, decision, [candidate], True, "连续竞价交易时段",
                        {"allow_new_buys": True, "max_open_positions": 6},
                        datetime(2026, 9, 8, 10, 0),
                    )
                    self.assertEqual(executed, [])
                    self.assertEqual(state["cash"], 100000.0)
                    self.assertEqual(state["positions"], {})
                    self.assertEqual(state["trade_log"], [])
                    self.assertEqual(
                        decision["execution_blocks"][-1]["category"],
                        "entry_price_quality",
                    )

    def test_all_buy_routes_recheck_actual_turnover_and_retry_after_it_rises(self):
        candidates = (
            reversal_candidate(),
            niu_candidate(),
            niu_candidate(best_strategy="niu_emerging", sector_status="emerging",
                          mainline_state="emerging", mainline_cross_day_persistent=True),
            niu_candidate(best_strategy="niu_pullback", niuone_lifecycle_stage="climax"),
        )
        for candidate in candidates:
            with self.subTest(strategy=candidate["best_strategy"]), patch.object(
                trader, "is_a_share_execution_time", return_value=(True, "连续竞价交易时段"),
            ), patch.object(trader, "execution_quote") as quote:
                state = {"cash": 100000.0, "positions": {}, "trade_log": []}
                for turnover in (None, 2.999, float("nan"), float("inf"), 3.0):
                    quote.return_value = {"price": 10.0, "prev_close": 10.0,
                                          "turnover": turnover, "source": "test"}
                    decision = {"actions": [{"action": "BUY", "code": "600000",
                                              "shares": 100, "reason": "活跃股确认"}]}
                    fills = trader.execute_actions(
                        state, decision, [candidate], True, "连续竞价交易时段",
                        {"allow_new_buys": True, "max_open_positions": 6},
                        datetime(2026, 9, 8, 10, 0),
                    )
                    if turnover == 3.0:
                        self.assertEqual(len(fills), 1, decision)
                        self.assertEqual(fills[0]["niuone_entry_context"]["entry_turnover_pct"], 3.0)
                    else:
                        self.assertEqual(fills, [])
                        self.assertEqual(state["cash"], 100000.0)
                        self.assertEqual(state["positions"], {})
                        self.assertEqual(state["trade_log"], [])
                        self.assertEqual(decision["execution_blocks"][-1]["category"], "stock_activity")
                cash_after_entry = state["cash"]
                quote.return_value = {"price": 10.0, "prev_close": 10.0,
                                      "turnover": 2.0, "source": "test"}
                add_decision = {"actions": [{"action": "BUY", "code": "600000",
                                             "shares": 100, "reason": "持仓加仓"}]}
                added = trader.execute_actions(
                    state, add_decision, [candidate], True, "连续竞价交易时段",
                    {"allow_new_buys": True, "max_open_positions": 6},
                    datetime(2026, 9, 9, 10, 0),
                )
                self.assertEqual(added, [])
                self.assertEqual(add_decision["execution_blocks"][-1]["category"], "stock_activity")
                self.assertEqual(state["cash"], cash_after_entry)
                self.assertEqual(state["positions"]["600000"]["qty"], 100)
                self.assertEqual(len(state["trade_log"]), 1)

    def test_probe_shadow_observes_both_price_groups_after_other_gates_pass(self):
        for price, turnover, cash, expected_observations, expected_fills in (
            (10.2, 4.0, 100000.0, 1, 1), (10.3, 4.0, 100000.0, 1, 0),
            (10.3, 2.0, 100000.0, 0, 0), (10.3, 4.0, 10.0, 0, 0),
        ):
            with self.subTest(price=price, turnover=turnover, cash=cash), patch.object(
                trader, "is_a_share_execution_time", return_value=(True, "连续竞价交易时段"),
            ), patch.object(trader, "execution_quote", return_value={
                "price": price, "prev_close": 10.0, "turnover": turnover, "source": "test",
            }):
                state = {"cash": cash, "positions": {}, "trade_log": []}
                decision = {"actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "试仓"}]}
                fills = trader.execute_actions(
                    state, decision, [reversal_candidate(recent_close=price, stop_price=10.0)],
                    True, "连续竞价交易时段", {"allow_new_buys": True, "max_open_positions": 6},
                    datetime(2026, 9, 8, 10, 0),
                )
                observations = decision.get("probe_chase_observations") or []
                self.assertEqual(len(observations), expected_observations, decision)
                self.assertEqual(len(fills), expected_fills, decision)
                if observations:
                    self.assertEqual(observations[0]["filtered_accepts"], price < 10.3)
                if not expected_fills:
                    self.assertEqual(state["cash"], cash)
                    self.assertEqual(state["positions"], {})
                    self.assertEqual(state["trade_log"], [])

    def test_execution_quote_parsers_preserve_observed_turnover(self):
        parts = [""] * 60
        for index, value in {1: "测试", 3: "10", 4: "10", 5: "10", 6: "1000",
                             30: "20260908100000", 33: "10", 34: "10",
                             37: "100", 38: "3.25"}.items():
            parts[index] = value
        quote = trader.parse_tencent_quote_line('v_sh600000="' + "~".join(parts) + '";')
        self.assertEqual(quote["turnover"], 3.25)
        self.assertEqual(quote["turnover_yuan"], 1e6)
        eastmoney = trader.parse_eastmoney_stock({"f43": 10.0, "f60": 10.0,
                                                 "f57": "600000", "f168": 3.25, "f8": 99})
        self.assertEqual(eastmoney["turnover"], 3.25)
        with patch.object(trader.subprocess, "run") as request:
            request.return_value.returncode = 0
            request.return_value.stdout = json.dumps({"data": {
                "f43": 10.0, "f60": 10.0, "f57": "600000", "f168": 3.25,
            }})
            quotes, error = trader.fetch_eastmoney_quotes(["600000"])
        self.assertEqual(error, "")
        self.assertEqual(quotes["600000"]["turnover"], 3.25)
        fields = next(arg for arg in request.call_args.args[0] if arg.startswith("fields="))
        self.assertIn("f168", fields.split("=", 1)[1].split(","))
        self.assertIn("fltt=2", request.call_args.args[0])

    def test_reversal_price_guard_reassesses_after_price_retreats(self):
        state = {"cash": 100000.0, "positions": {}, "trade_log": []}
        candidate = reversal_candidate(recent_close=10.4, stop_price=10.0)
        with patch.object(
            trader, "is_a_share_execution_time", return_value=(True, "连续竞价交易时段"),
        ), patch.object(trader, "execution_quote") as quote:
            for price, expected_count in ((10.3, 0), (10.299, 1)):
                quote.return_value = {"turnover": 4.0, "price": price, "prev_close": 10.0, "source": "test"}
                decision = {"actions": [{
                    "action": "BUY", "code": "600000", "shares": 100,
                    "reason": "牛牛试仓确认",
                }]}
                executed = trader.execute_actions(
                    state, decision, [candidate], True, "连续竞价交易时段",
                    {"allow_new_buys": True, "max_open_positions": 6},
                    datetime(2026, 9, 8, 10, 0),
                )
                self.assertEqual(len(executed), expected_count)
            self.assertEqual(state["positions"]["600000"]["qty"], 100)
            self.assertEqual(len(state["trade_log"]), 1)

    def test_probe_price_guard_does_not_apply_to_existing_holdings_or_leaders(self):
        for candidate, position in (
            (reversal_candidate(), {"qty": 100, "avg_cost": 10.0, "buy_strategy": "niu_reversal_probe"}),
            (niu_candidate(), None),
        ):
            with self.subTest(strategy=candidate["best_strategy"]), patch.object(
                trader, "is_a_share_execution_time", return_value=(True, "连续竞价交易时段"),
            ), patch.object(trader, "execution_quote", return_value={
                "turnover": 4.0,
                "price": 10.4, "prev_close": 10.0, "source": "test",
            }), patch.object(trader, "niu_reversal_entry_price_blocker") as guard:
                state = {
                    "cash": 100000.0, "trade_log": [],
                    "positions": {"600000": position} if position else {},
                }
                decision = {"actions": [{
                    "action": "BUY", "code": "600000", "shares": 100,
                    "reason": "牛牛战法确认",
                }]}
                trader.execute_actions(
                    state, decision, [candidate], True, "连续竞价交易时段",
                    {"allow_new_buys": True, "max_open_positions": 6},
                    datetime(2026, 9, 8, 10, 0),
                )
                guard.assert_not_called()

    def test_execution_records_niuone_entry_gap_without_blocking_the_trade(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (
                True,
                "连续竞价交易时段",
            )
            trader.execution_quote = lambda code: {
                "turnover": 4.0,
                "price": 10.1,
                "prev_close": 10.0,
                "name": "牛牛反转测试",
                "source": "test",
            }
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {"actions": [{
                "action": "BUY",
                "code": "600000",
                "shares": 400,
                "reason": "牛牛反转确认",
            }]}
            decision["_niuone_execution_context"] = {
                "entry_signal_generated_at": "2026-08-03 09:25:10",
                "entry_schedule_slot": "2026-08-03 09:25",
                "entry_schedule_run_kind": "scheduled",
                "entry_schedule_triggered_at": "2026-08-03 09:25:00",
                "entry_execution_mode": "deferred",
            }

            executed = trader.execute_actions(
                state,
                decision,
                [reversal_candidate(
                    industry="通信设备",
                    sector="通信设备",
                    signal_theme="数字货币",
                    recent_close=10.0,
                    mainline_score_change=2.5,
                    mainline_state_streak=3,
                    today_strength_score=42.0,
                    selection_signal_score=9.0,
                    selection_candidate_pool_size=5,
                    selection_same_stage_candidate_count=4,
                    selection_same_stage_candidate_rank=1,
                    selection_same_stage_top_score_gap=0.2,
                )],
                True,
                "连续竞价交易时段",
                market,
            )

            self.assertEqual(len(executed), 1)
            position = state["positions"]["600000"]
            self.assertEqual(position["entry_execution_reference_price"], 10.0)
            self.assertEqual(position["industry"], "通信设备")
            self.assertEqual(position["entry_theme"], "数字货币")
            self.assertEqual(position["active_theme"], "数字货币")
            self.assertEqual(position["entry_execution_gap_pct"], 1.0)
            self.assertEqual(position["entry_mainline_score_change"], 2.5)
            self.assertEqual(position["entry_mainline_state_streak"], 3)
            self.assertEqual(position["entry_today_strength_score"], 42.0)
            self.assertEqual(position["niuone_lifecycle_stage"], "brewing")
            self.assertEqual(position["niuone_lifecycle_label"], "主线酝酿")
            self.assertEqual(position["mainline_peak_score"], 45.0)
            self.assertEqual(position["mainline_peak_drawdown_points"], 0.0)
            self.assertEqual(executed[0]["execution_gap_pct"], 1.0)
            self.assertEqual(executed[0]["position_before_qty"], 0)
            self.assertEqual(executed[0]["position_after_qty"], 400)
            self.assertTrue(executed[0]["position_opened"])
            self.assertEqual(decision["actions"][0]["execution_gap_pct"], 1.0)
            expected_context = {
                "entry_turnover_pct": 4.0,
                "entry_niuone_lifecycle_stage": "brewing",
                "entry_niuone_lifecycle_label": "主线酝酿",
                "entry_niuone_lifecycle_order": 10,
                "entry_niuone_lifecycle_entry_policy": "probe_only",
                "entry_mainline_state": "candidate",
                "entry_mainline_score": 45.0,
                "entry_mainline_score_change": 2.5,
                "entry_mainline_state_streak": 3,
                "entry_mainline_cross_day_persistent": False,
                "entry_mainline_confirmed": False,
                "entry_today_strength_score": 42.0,
                "entry_strong_stock_count": 6,
                "entry_effective_strong_count": 5.6,
                "entry_stock_sector_rank": 95.0,
                "entry_stock_strong": False,
                "entry_stock_leader_tier": False,
                "entry_stock_activity_score": 84.25,
                "entry_stock_market_amount_percentile": 90.0,
                "entry_stock_theme_amount_percentile": 75.0,
                "entry_stock_activity_confirmed": True,
                "entry_daily_v_recovery_ratio": 0.7,
                "entry_signal_score": 9.0,
                "entry_candidate_pool_size": 5,
                "entry_same_stage_candidate_count": 4,
                "entry_same_stage_candidate_rank": 1,
                "entry_same_stage_top_score_gap": 0.2,
                "entry_execution_reference_price": 10.0,
                "entry_execution_gap_pct": 1.0,
                "entry_signal_generated_at": "2026-08-03 09:25:10",
                "entry_schedule_slot": "2026-08-03 09:25",
                "entry_schedule_run_kind": "scheduled",
                "entry_schedule_triggered_at": "2026-08-03 09:25:00",
                "entry_execution_mode": "deferred",
                "entry_industry": "通信设备",
                "entry_theme": "数字货币",
                "entry_theme_basis": "eastmoney_concept",
                "entry_theme_attribution_score": 86.0,
                "entry_theme_attribution_weight": 1.0,
                "entry_theme_historical_prior_score": 84.0,
                "entry_theme_cohort_alignment_score": 82.0,
                "entry_theme_peer_resonance_score": 88.0,
                "entry_theme_return_correlation_score": 90.0,
                "entry_theme_return_correlation_rank_score": 95.0,
                "entry_theme_return_correlation_observation_count": 20,
                "entry_theme_return_correlation_peer_count": 10,
                "entry_theme_specificity_score": 88.0,
                "entry_theme_membership_source": "eastmoney_concept",
                "entry_theme_unattributed_weight": 0.0,
                "entry_model_requested_shares": 400,
                "entry_executed_shares": 400,
                "entry_maximum_permitted_shares": 600,
                "entry_risk_ceiling_utilization_pct": 66.6667,
                "entry_risk_ceiling_binding_constraints": [
                    "single_name_risk"
                ],
                "entry_risk_ceiling_auto_reduced": False,
            }
            self.assertEqual(
                executed[0]["niuone_entry_context"],
                expected_context,
            )
            self.assertEqual(
                decision["actions"][0]["niuone_entry_context"],
                expected_context,
            )

            position["buy_date_lots"] = {"2026-07-31": 400}
            position["mainline_state"] = "inactive"
            sell_decision = {"actions": [{
                "action": "SELL",
                "code": "600000",
                "shares": 400,
                "reason": "牛牛主线衰落退出",
            }]}
            sold = trader.execute_actions(
                state,
                sell_decision,
                [],
                True,
                "连续竞价交易时段",
                market,
            )

            self.assertEqual(len(sold), 1)
            self.assertTrue(sold[0]["position_fully_closed"])
            self.assertEqual(sold[0]["position_before_qty"], 400)
            self.assertEqual(sold[0]["position_after_qty"], 0)
            self.assertEqual(sold[0]["niuone_entry_context"], expected_context)
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_execution_rechecks_niuone_structural_limits_by_market_regime(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"turnover": 4.0, "price": 10.0, "name": "牛牛测试", "source": "test"}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }

            offensive_state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            offensive_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "牛牛领航确认"}]
            }
            offensive_candidate = niu_candidate(
                stop_price=9.28,
                stop_distance_pct=7.2,
                stop_atr=2.4,
                atr=None,
                atr_period=None,
                atr20=0.3,
                effective_loss_distance_pct=8.4,
            )
            executed = trader.execute_actions(
                offensive_state,
                offensive_decision,
                [offensive_candidate],
                True,
                "连续竞价交易时段",
                market,
            )
            self.assertEqual(len(executed), 1)
            self.assertEqual(offensive_state["positions"]["600000"]["risk_budget_regime"], "offensive")

            atr_blocked_state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            atr_blocked_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "牛牛领航确认"}]
            }
            atr_blocked_candidate = niu_candidate(
                stop_price=9.1,
                stop_distance_pct=9.0,
                stop_atr=3.0,
                atr=0.3,
                atr20=0.3,
                effective_loss_distance_pct=10.2,
            )
            blocked = trader.execute_actions(
                atr_blocked_state,
                atr_blocked_decision,
                [atr_blocked_candidate],
                True,
                "连续竞价交易时段",
                market,
            )
            self.assertEqual(blocked, [])
            self.assertIn("10%/2.5ATR", atr_blocked_decision["execution_blocked_reason"])

            rotation_state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            rotation_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "牛牛领航确认"}]
            }
            rotation_candidate = niu_candidate(
                market_regime="rotation",
                stop_price=9.3,
                stop_distance_pct=7.0,
                stop_atr=1.75,
                atr=0.4,
                atr20=0.4,
                effective_loss_distance_pct=8.2,
                per_trade_risk_budget_pct=1.0,
            )
            executed = trader.execute_actions(
                rotation_state,
                rotation_decision,
                [rotation_candidate],
                True,
                "连续竞价交易时段",
                market,
            )
            self.assertEqual(len(executed), 1)
            self.assertEqual(rotation_state["positions"]["600000"]["risk_budget_regime"], "rotation")
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_execution_accepts_second_rank_and_rejects_stock_outside_leader_tier(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"turnover": 4.0, "price": 10.0, "name": "行业跟随股", "source": "test"}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }

            second_state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            second_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "第一名涨停，顺延第二名"}]
            }
            second_rank = niu_candidate(
                stock_role="core",
                stock_leader_rank=2,
                stock_leader_tier=True,
            )
            executed = trader.execute_actions(
                second_state,
                second_decision,
                [second_rank],
                True,
                "连续竞价交易时段",
                market,
            )
            self.assertEqual(len(executed), 1)
            self.assertEqual(second_state["positions"]["600000"]["stock_leader_rank"], 2)

            blocked_state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            blocked_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "模型误选第四名"}]
            }
            blocked = trader.execute_actions(
                blocked_state,
                blocked_decision,
                [niu_candidate(stock_role="core", stock_leader_rank=4, stock_leader_tier=False)],
                True,
                "连续竞价交易时段",
                market,
            )
            self.assertEqual(blocked, [])
            self.assertEqual(blocked_state["positions"], {})
            self.assertIn("个股未进入强势行业龙头梯队", blocked_decision["execution_blocked_reason"])

            trader.execution_quote = lambda code: {
                "turnover": 4.0,
                "price": 11.0,
                "prev_close": 10.0,
                "change_pct": 10.0,
                "name": "涨停龙头",
                "source": "test",
            }
            limit_state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            limit_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "第一名已涨停"}]
            }
            at_limit = trader.execute_actions(
                limit_state,
                limit_decision,
                [niu_candidate(name="涨停龙头")],
                True,
                "连续竞价交易时段",
                market,
            )
            self.assertEqual(at_limit, [])
            self.assertIn("不在涨停价模拟买入", limit_decision["execution_blocked_reason"])
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_niuone_emerging_position_adds_in_two_markup_tiers(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"turnover": 4.0, "price": 10.2, "name": "牛牛启动", "source": "test"}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }
            original_position = {
                "code": "600000",
                "name": "牛牛启动",
                "qty": 100,
                "avg_cost": 10.0,
                "last_price": 10.0,
                "buy_strategy": "niu_emerging",
                "strategy_mark": {"strategy_id": "niu_emerging"},
                "industry": "半导体",
                "entry_reason": "牛牛启动观察仓",
                "entry_stop_price": 9.5,
                "gap_buffer_pct": 1.0,
                "execution_buffer_pct": 0.2,
                "buy_date_lots": {"2000-01-01": 100},
                "entry_signal_score": 8.8,
                "last_buy_signal_score": 8.8,
                "highest_buy_signal_score": 8.8,
                "niuone_buy_signal_count": 1,
            }

            early_state = {"cash": 99000.0, "positions": {"600000": dict(original_position)}, "trade_log": []}
            early_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "启动主题延续"}]
            }
            early_candidate = niu_candidate(
                best_strategy="niu_emerging",
                mainline_state="emerging",
                sector_status="emerging",
                mainline_cross_day_persistent=True,
                mainline_confirmed=False,
                niuone_lifecycle_stage="markup",
            )

            early_added = trader.execute_actions(
                early_state,
                early_decision,
                [early_candidate],
                True,
                "连续竞价交易时段",
                market,
            )

            self.assertEqual(len(early_added), 1)
            early_position = early_state["positions"]["600000"]
            self.assertEqual(early_position["qty"], 200)
            self.assertEqual(
                early_position["absolute_position_cap_pct"],
                NIUONE_MARKUP_EARLY_UPGRADE_POSITION_CAP_PCT,
            )
            self.assertIs(
                early_position["niuone_markup_early_scale_in_done"],
                True,
            )
            self.assertEqual(
                early_position["niuone_markup_scale_in_tier"],
                "early",
            )
            self.assertEqual(
                early_position["niuone_markup_scale_in_max_pnl_pct"],
                NIUONE_MARKUP_UPGRADE_MAX_PNL_PCT,
            )

            repeated_early_decision = {
                "actions": [{
                    "action": "BUY",
                    "code": "600000",
                    "shares": 100,
                    "reason": "启动主题重复加仓",
                }],
            }
            self.assertEqual(trader.execute_actions(
                early_state,
                repeated_early_decision,
                [early_candidate],
                True,
                "连续竞价交易时段",
                market,
            ), [])
            self.assertEqual(
                early_state["positions"]["600000"]["qty"],
                200,
            )
            self.assertIn(
                "评分严格创新高",
                repeated_early_decision["execution_blocked_reason"],
            )

            trader.execution_quote = lambda code: {
                "turnover": 4.0,
                "price": 10.5,
                "name": "牛牛启动",
                "source": "test",
            }
            stronger_signal_decision = {
                "actions": [{
                    "action": "BUY",
                    "code": "600000",
                    "shares": 100,
                    "reason": "启动主题评分继续提高",
                }],
            }
            stronger_signal = dict(early_candidate)
            stronger_signal["best_score"] = 9.2
            stronger_signal["stop_price"] = 9.8
            stronger_added = trader.execute_actions(
                early_state,
                stronger_signal_decision,
                [stronger_signal],
                True,
                "连续竞价交易时段",
                market,
            )
            self.assertEqual(
                len(stronger_added),
                1,
                stronger_signal_decision.get("execution_blocked_reason"),
            )
            stronger_position = early_state["positions"]["600000"]
            self.assertEqual(stronger_position["qty"], 300)
            self.assertEqual(stronger_position["last_buy_signal_score"], 9.2)
            self.assertEqual(
                stronger_position["highest_buy_signal_score"],
                9.2,
            )
            self.assertEqual(stronger_position["niuone_buy_signal_count"], 3)
            self.assertEqual(
                stronger_added[0]["niuone_add_signal_score_audit"][
                    "previous_score"
                ],
                9.0,
            )
            self.assertEqual(
                stronger_position["niuone_buy_signal_score_history"][-1][
                    "route"
                ],
                "score_progression",
            )

            upgraded_state = {"cash": 99000.0, "positions": {"600000": dict(original_position)}, "trade_log": []}
            upgraded_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "升级主线后加仓"}]
            }
            upgraded_candidate = niu_candidate(
                best_strategy="niu_leader",
                mainline_state="mainline",
                sector_status="mainline",
                mainline_score=77.9,
                mainline_confirmed=True,
                niuone_lifecycle_stage="markup",
                stop_price=9.6,
                atr=0.5,
                atr20=0.5,
            )

            upgraded = trader.execute_actions(
                upgraded_state,
                upgraded_decision,
                [upgraded_candidate],
                True,
                "连续竞价交易时段",
                market,
            )

            self.assertEqual(len(upgraded), 1)
            position = upgraded_state["positions"]["600000"]
            self.assertEqual(position["qty"], 200)
            self.assertEqual(position["initial_buy_strategy"], "niu_emerging")
            self.assertEqual(position["buy_strategy"], "niu_leader")
            self.assertEqual(position["strategy_mark"]["strategy_id"], "niu_leader")
            self.assertEqual(position["strategy_mark"]["source"], "BUY_UPGRADE")
            self.assertEqual(
                position["absolute_position_cap_pct"],
                NIUONE_MARKUP_UPGRADE_POSITION_CAP_PCT,
            )
            self.assertEqual(position["niuone_markup_scale_in_count"], 1)
            self.assertGreaterEqual(
                position["niuone_markup_scale_in_signal_pnl_pct"],
                NIUONE_MARKUP_UPGRADE_MIN_PNL_PCT,
            )

            divergence_decision = {
                "actions": [{
                    "action": "BUY",
                    "code": "600000",
                    "shares": 100,
                    "reason": "分歧阶段继续加仓",
                }],
            }
            divergence_candidate = niu_candidate(
                best_strategy="niu_leader",
                best_score=9.2,
                mainline_state="mainline",
                sector_status="mainline",
                mainline_score=77.9,
                mainline_confirmed=True,
                niuone_lifecycle_stage="divergence",
            )
            self.assertEqual(trader.execute_actions(
                upgraded_state,
                divergence_decision,
                [divergence_candidate],
                True,
                "连续竞价交易时段",
                market,
            ), [])
            self.assertIn(
                "只允许主升阶段",
                divergence_decision["execution_blocked_reason"],
            )
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_niuone_local_rule_adds_qualifying_markup_holding_without_model_add(self):
        original_suite = trader.current_strategy_suite
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.current_strategy_suite = lambda: "niuone"
            trader.is_a_share_execution_time = lambda dt=None: (
                True,
                "连续竞价交易时段",
            )
            trader.execution_quote = lambda code: {
                "turnover": 4.0,
                "price": 10.2,
                "name": "本地分级加仓",
                "source": "test",
            }
            position = {
                "code": "600000",
                "name": "本地分级加仓",
                "qty": 100,
                "avg_cost": 10.0,
                "last_price": 10.0,
                "buy_strategy": "niu_emerging",
                "initial_buy_strategy": "niu_emerging",
                "strategy_mark": {"strategy_id": "niu_emerging"},
                "industry": "半导体",
                "signal_theme": "半导体",
                "entry_stop_price": 9.5,
                "gap_buffer_pct": 1.0,
                "execution_buffer_pct": 0.2,
                "buy_date_lots": {"2000-01-01": 100},
                "entry_signal_score": 9.8,
                "last_buy_signal_score": 9.8,
                "highest_buy_signal_score": 9.8,
                "niuone_buy_signal_count": 1,
            }
            state = {
                "cash": 99000.0,
                "positions": {"600000": position},
                "trade_log": [],
            }
            decision = {
                "actions": [{
                    "action": "HOLD",
                    "code": "600000",
                    "reason": "模型未主动提出加仓",
                }],
            }
            candidate = niu_candidate(
                name="本地分级加仓",
                best_strategy="niu_emerging",
                best_score=9.0,
                recent_close=10.2,
                mainline_state="emerging",
                sector_status="emerging",
                mainline_cross_day_persistent=True,
                mainline_confirmed=False,
                niuone_lifecycle_stage="markup",
            )

            generated = trader.append_niuone_deterministic_scale_in_actions(
                decision,
                state,
                [candidate],
                {"daily_loss_budget_exceeded": False},
            )

            self.assertEqual(len(generated), 1)
            self.assertEqual(decision["actions"][0]["action"], "BUY")
            self.assertEqual(
                decision["actions"][0]["target_position_pct"],
                NIUONE_MARKUP_EARLY_UPGRADE_POSITION_CAP_PCT,
            )
            self.assertIs(
                decision["actions"][0]["niuone_deterministic_scale_in"],
                True,
            )
            executed = trader.execute_actions(
                state,
                decision,
                [candidate],
                True,
                "连续竞价交易时段",
                {
                    "allow_new_buys": True,
                    "max_total_position_pct": 80,
                    "min_cash_reserve_pct": 20,
                },
            )
            self.assertEqual(len(executed), 1)
            self.assertEqual(state["positions"]["600000"]["qty"], 900)
            self.assertIs(
                state["positions"]["600000"][
                    "niuone_markup_early_scale_in_done"
                ],
                True,
            )
            self.assertEqual(
                state["positions"]["600000"][
                    "niuone_buy_signal_score_history"
                ][-1]["route"],
                "deterministic_stage_scale_in",
            )
            self.assertIs(executed[0]["niuone_deterministic_scale_in"], True)

            sell_decision = {
                "actions": [{
                    "action": "SELL",
                    "code": "600000",
                    "shares": 100,
                    "reason": "模型明确退出",
                }],
            }
            self.assertEqual(
                trader.append_niuone_deterministic_scale_in_actions(
                    sell_decision,
                    state,
                    [candidate],
                    {},
                ),
                [],
            )
            self.assertEqual(sell_decision["actions"][0]["action"], "SELL")
        finally:
            trader.current_strategy_suite = original_suite
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_rotation_reversal_probe_can_use_one_percent_risk_budget(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (
                True,
                "连续竞价交易时段",
            )
            trader.execution_quote = lambda code: {
                "turnover": 4.0,
                "price": 10.0,
                "name": "轮动试仓",
                "prev_close": 10.0,
                "source": "test",
            }
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {
                "actions": [{
                    "action": "BUY",
                    "code": "600000",
                    "shares": 2000,
                    "reason": "轮动V形试仓",
                }],
            }
            candidate = reversal_candidate(
                name="轮动试仓",
                market_regime="rotation",
                stop_price=9.41,
                stop_distance_pct=5.9,
                gap_buffer_pct=2.0,
            )

            executed = trader.execute_actions(
                state,
                decision,
                [candidate],
                True,
                "连续竞价交易时段",
                {
                    "allow_new_buys": True,
                    "max_total_position_pct": 80,
                    "min_cash_reserve_pct": 20,
                },
            )

            self.assertEqual(len(executed), 1)
            position = state["positions"]["600000"]
            self.assertEqual(position["qty"], 1000)
            self.assertEqual(position["absolute_position_cap_pct"], 10.0)
            self.assertEqual(position["per_trade_risk_budget_pct"], 1.0)
            self.assertGreater(position["position_open_risk_pct"], 0.60)
            self.assertLessEqual(position["position_open_risk_pct"], 1.0)
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_niuone_markup_rebalance_can_readd_after_each_new_wave(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (
                True,
                "连续竞价交易时段",
            )
            trader.execution_quote = lambda code: {
                "turnover": 4.0,
                "price": 10.6,
                "name": "牛牛领涨",
                "source": "test",
            }
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }
            position = {
                "code": "600000",
                "name": "牛牛领涨",
                "qty": 100,
                "avg_cost": 10.0,
                "last_price": 10.6,
                "buy_strategy": "niu_leader",
                "strategy_mark": {"strategy_id": "niu_leader"},
                "industry": "半导体",
                "entry_reason": "确认主线领涨",
                "entry_stop_price": 9.5,
                "entry_market_regime": "offensive",
                "gap_buffer_pct": 1.0,
                "execution_buffer_pct": 0.2,
                "buy_date_lots": {"2000-01-01": 100},
                "niuone_markup_confirmed_scale_in_done": True,
                "niuone_markup_rebalance_armed": True,
                "niuone_markup_rebalance_reduced": True,
                "niuone_markup_rebalance_reentry_price": 10.5,
            }
            state = {
                "cash": 99000.0,
                "positions": {"600000": position},
                "trade_log": [],
            }
            candidate = niu_candidate(
                best_strategy="niu_leader",
                mainline_state="mainline",
                sector_status="mainline",
                mainline_score=77.9,
                mainline_confirmed=True,
                niuone_lifecycle_stage="markup",
                stop_price=9.6,
                atr=0.5,
                atr20=0.5,
            )

            for expected_count in (1, 2):
                decision = {
                    "actions": [{
                        "action": "BUY",
                        "code": "600000",
                        "shares": 100,
                        "reason": "波段回落后重新转强",
                    }],
                }
                executed = trader.execute_actions(
                    state,
                    decision,
                    [candidate],
                    True,
                    "连续竞价交易时段",
                    market,
                )
                self.assertEqual(
                    len(executed),
                    1,
                    decision.get("execution_blocked_reason"),
                )
                position = state["positions"]["600000"]
                self.assertEqual(
                    position["niuone_markup_rebalance_reentry_count"],
                    expected_count,
                )
                self.assertIs(
                    position["niuone_markup_rebalance_armed"],
                    False,
                )
                if expected_count == 1:
                    position.update({
                        "niuone_markup_rebalance_armed": True,
                        "niuone_markup_rebalance_reduced": True,
                        "niuone_markup_rebalance_reentry_price": 10.5,
                    })
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_reversal_probe_is_small_and_cannot_add_on_entry_day(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"turnover": 4.0, "price": 10.0, "prev_close": 10.0, "name": "牛牛反转", "source": "test"}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            first_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "V形反转双确认"}]
            }

            first = trader.execute_actions(
                state,
                first_decision,
                [reversal_candidate()],
                True,
                "连续竞价交易时段",
                market,
            )

            self.assertEqual(len(first), 1)
            position = state["positions"]["600000"]
            self.assertEqual(position["buy_strategy"], "niu_reversal_probe")
            self.assertEqual(position["reversal_basis"], "daily_v")
            self.assertEqual(position["entry_stop_source"], "niu_reversal_right_low")
            self.assertEqual(position["absolute_position_cap_pct"], 10.0)
            self.assertEqual(position["per_trade_risk_budget_pct"], 0.35)

            add_decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "反转继续走强"}]
            }
            added = trader.execute_actions(
                state,
                add_decision,
                [reversal_candidate()],
                True,
                "连续竞价交易时段",
                market,
            )

            self.assertEqual(added, [])
            self.assertEqual(state["positions"]["600000"]["qty"], 100)
            self.assertIn("当日只建立一次轻仓", add_decision["execution_blocked_reason"])
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_reversal_probe_does_not_chase_above_markup_profit_window(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"turnover": 4.0, "price": 11.3, "name": "牛牛反转", "source": "test"}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }
            position = {
                "code": "600000",
                "name": "牛牛反转",
                "qty": 100,
                "avg_cost": 10.0,
                "last_price": 10.0,
                "buy_strategy": "niu_reversal_probe",
                "strategy_mark": {"strategy_id": "niu_reversal_probe"},
                "industry": "半导体",
                "entry_reason": "V形反转双确认",
                "entry_stop_price": 9.7,
                "entry_stop_source": "niu_reversal_low",
                "entry_market_regime": "offensive",
                "risk_budget_regime": "offensive",
                "gap_buffer_pct": 1.0,
                "execution_buffer_pct": 0.2,
                "buy_date_lots": {"2000-01-01": 100},
            }
            state = {"cash": 99000.0, "positions": {"600000": position}, "trade_log": []}
            decision = {
                "actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "跨日延续升级启动"}]
            }
            candidate = niu_candidate(
                best_strategy="niu_emerging",
                market_regime="rotation",
                mainline_state="emerging",
                sector_status="emerging",
                mainline_cross_day_persistent=True,
                mainline_confirmed=False,
                niuone_lifecycle_stage="markup",
            )

            upgraded = trader.execute_actions(
                state,
                decision,
                [candidate],
                True,
                "连续竞价交易时段",
                market,
            )

            self.assertEqual(upgraded, [])
            position = state["positions"]["600000"]
            self.assertEqual(position["qty"], 100)
            self.assertNotIn("initial_buy_strategy", position)
            self.assertEqual(position["buy_strategy"], "niu_reversal_probe")
            self.assertIn(
                f"≤{NIUONE_MARKUP_UPGRADE_MAX_PNL_PCT:g}%",
                decision["execution_blocked_reason"],
            )
            self.assertEqual(position["entry_market_regime"], "offensive")
            self.assertEqual(position["risk_budget_regime"], "offensive")
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_limit_up_execution_guard_respects_board_specific_limits(self):
        self.assertTrue(trader.quote_is_at_limit_up(
            "600000",
            "主板龙头",
            {"price": 11.0, "prev_close": 10.0, "change_pct": 10.0},
        ))
        self.assertFalse(trader.quote_is_at_limit_up(
            "300001",
            "创业板龙头",
            {"price": 11.0, "prev_close": 10.0, "change_pct": 10.0},
        ))
        self.assertTrue(trader.quote_is_at_limit_up(
            "300001",
            "创业板龙头",
            {"price": 12.0, "prev_close": 10.0, "change_pct": 20.0},
        ))
        self.assertTrue(trader.quote_is_at_limit_up(
            "600001",
            "ST测试",
            {"price": 10.5, "prev_close": 10.0, "change_pct": 5.0},
        ))
        self.assertFalse(trader.quote_is_at_limit_up(
            "600000",
            "主板接近涨停",
            {"price": 10.99, "prev_close": 10.0, "change_pct": 9.9},
        ))
        self.assertFalse(trader.quote_is_at_limit_up(
            "600000",
            "主板四舍五入前一档",
            {"price": 11.02, "prev_close": 10.03, "change_pct": 9.87},
        ))
        self.assertTrue(trader.quote_is_at_limit_up(
            "600000",
            "主板四舍五入涨停",
            {"price": 11.03, "prev_close": 10.03, "change_pct": 9.97},
        ))

    def test_niuone_execution_blocks_chinext_when_configured_universe_is_main_board(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        saved_active = os.environ.get(trader.ACTIVE_STRATEGY_ENV)
        saved_universe = os.environ.get(trader.STOCK_UNIVERSE_ENV)
        try:
            os.environ[trader.ACTIVE_STRATEGY_ENV] = "niuone"
            os.environ[trader.STOCK_UNIVERSE_ENV] = "main_board"
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"turnover": 4.0, "price": 10.0, "name": "创业板牛牛", "source": "test"}
            candidate = niu_candidate(code="300001", name="创业板牛牛")
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {
                "actions": [{"action": "BUY", "code": "300001", "shares": 100, "reason": "牛牛领航确认"}]
            }
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }

            executed = trader.execute_actions(state, decision, [candidate], True, "连续竞价交易时段", market)

            self.assertEqual(executed, [])
            self.assertEqual(state["positions"], {})
            self.assertIn("不在当前选股范围", decision["execution_blocked_reason"])
            self.assertEqual(trader.current_stock_universe(), ("main_board",))
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote
            if saved_active is None:
                os.environ.pop(trader.ACTIVE_STRATEGY_ENV, None)
            else:
                os.environ[trader.ACTIVE_STRATEGY_ENV] = saved_active
            if saved_universe is None:
                os.environ.pop(trader.STOCK_UNIVERSE_ENV, None)
            else:
                os.environ[trader.STOCK_UNIVERSE_ENV] = saved_universe

    def test_mainline_weakness_counts_once_per_day_and_exits(self):
        state = {
            "positions": {
                "600000": {
                    "code": "600000", "name": "牛牛测试", "qty": 400,
                    "avg_cost": 10.0, "last_price": 10.2, "close": 10.2,
                    "buy_strategy": "niu_leader", "industry": "半导体",
                    "entry_stop_price": 9.5, "entry_stop_source": "niu_structure_low",
                    "buy_date_lots": {"2026-07-10": 400},
                }
            }
        }

        def payload(day: str) -> dict:
            return {
                "generated_at": f"{day} 14:30:00",
                "niuone_context": {
                    "market": {"state": "rotation", "score": 55, "hard_stop": False, "allow_new_buys": True},
                    "themes": {"半导体": {"score": 50, "state": "fading", "raw_state": "fading"}},
                    "stocks": {"600000": {"industry": "半导体", "theme_rank": 20}},
                },
            }

        trader.sync_niuone_position_context(state, payload("2026-07-15"))
        trader.sync_niuone_position_context(state, payload("2026-07-15"))
        self.assertEqual(state["positions"]["600000"]["mainline_weak_count"], 1)
        trader.sync_niuone_position_context(state, payload("2026-07-16"))
        self.assertEqual(state["positions"]["600000"]["mainline_weak_count"], 2)

        signal = trader.evaluate_sell_signal("600000", state["positions"]["600000"], "2026-07-16", time_exit_allowed=False)
        self.assertEqual(signal["signal"], "niu_mainline_faded")

    def test_active_theme_switch_requires_two_days_and_keeps_entry_theme(self):
        state = {
            "positions": {
                "600000": {
                    "code": "600000", "name": "多概念测试", "qty": 400,
                    "avg_cost": 10.0, "last_price": 10.2,
                    "buy_strategy": "niu_leader",
                    "industry": "通信设备", "sector": "通信设备",
                    "entry_industry": "通信设备",
                    "entry_theme": "数字货币",
                    "active_theme": "数字货币",
                },
            },
        }

        def payload(day: str, previous_day: str) -> dict:
            return {
                "generated_at": f"{day} 14:30:00",
                "items": [{
                    "code": "600000",
                    "best_strategy": "niu_leader",
                    "industry": "通信设备",
                    "sector": "通信设备",
                    "signal_theme": "eSIM",
                }],
                "niuone_context": {
                    "previous_trading_day": previous_day,
                    "market": {
                        "state": "rotation", "score": 70,
                        "hard_stop": False, "allow_new_buys": True,
                    },
                    "themes": {
                        "数字货币": {"score": 68, "state": "mainline"},
                        "eSIM": {"score": 82, "state": "mainline"},
                    },
                    "stocks": {
                        "600000": {
                            "classification_industry": "通信设备",
                            "theme_profiles": [
                                {
                                    "industry": "数字货币", "strong": True,
                                    "role": "core", "leader_rank": 2,
                                    "leader_tier": True,
                                },
                                {
                                    "industry": "eSIM", "strong": True,
                                    "role": "leader", "leader_rank": 1,
                                    "leader_tier": True,
                                },
                            ],
                            "theme_attributions": [
                                {"theme": "数字货币", "attribution_score": 65, "attribution_weight": 0.35},
                                {"theme": "eSIM", "attribution_score": 82, "attribution_weight": 0.65},
                            ],
                        },
                    },
                },
            }

        trader.sync_niuone_position_context(
            state,
            payload("2026-07-15", "2026-07-14"),
        )
        position = state["positions"]["600000"]
        self.assertEqual(position["entry_theme"], "数字货币")
        self.assertEqual(position["active_theme"], "数字货币")
        self.assertEqual(position["pending_theme_switch_count"], 1)

        trader.sync_niuone_position_context(
            state,
            payload("2026-07-16", "2026-07-15"),
        )
        self.assertEqual(position["entry_theme"], "数字货币")
        self.assertEqual(position["active_theme"], "eSIM")
        self.assertEqual(position["industry"], "通信设备")
        self.assertEqual(position["mainline_score"], 82)
        self.assertEqual(position["theme_switch_history"][-1]["from_theme"], "数字货币")
        self.assertEqual(position["theme_switch_history"][-1]["to_theme"], "eSIM")

    def test_mainline_context_records_peak_and_current_drawdown(self):
        state = {
            "positions": {
                "600000": {
                    "code": "600000",
                    "name": "牛牛测试",
                    "qty": 400,
                    "avg_cost": 10.0,
                    "buy_strategy": "niu_reversal_probe",
                    "industry": "半导体",
                    "mainline_peak_score": 80.0,
                }
            }
        }

        def payload(day: str, score: float) -> dict:
            return {
                "generated_at": f"{day} 14:30:00",
                "niuone_context": {
                    "market": {
                        "state": "rotation",
                        "score": 70,
                        "hard_stop": False,
                        "allow_new_buys": True,
                    },
                    "themes": {
                        "半导体": {
                            "score": score,
                            "state": "emerging",
                            "raw_state": "emerging",
                            "niuone_lifecycle_stage": "divergence",
                            "niuone_lifecycle_label": "主线分歧",
                            "niuone_lifecycle_order": 40,
                            "niuone_lifecycle_entry_policy": (
                                "selective_repair_reclaim_or_reduce"
                            ),
                        }
                    },
                    "stocks": {
                        "600000": {
                            "industry": "半导体",
                            "theme_rank": 80,
                        }
                    },
                },
            }

        trader.sync_niuone_position_context(
            state,
            payload("2026-07-15", 74.0),
        )
        position = state["positions"]["600000"]
        self.assertEqual(position["mainline_peak_score"], 80.0)
        self.assertEqual(position["mainline_peak_drawdown_points"], 6.0)
        self.assertEqual(position["niuone_lifecycle_stage"], "divergence")
        self.assertEqual(position["niuone_lifecycle_label"], "主线分歧")

        trader.sync_niuone_position_context(
            state,
            payload("2026-07-16", 82.0),
        )
        self.assertEqual(position["mainline_peak_score"], 82.0)
        self.assertEqual(position["mainline_peak_drawdown_points"], 0.0)

    def test_holding_lifecycle_path_records_transitions_and_exit_stage(self):
        position = {
            "buy_strategy": "niu_reversal_probe",
            "mainline_state": "candidate",
            "mainline_score": 64.0,
            "niuone_lifecycle_stage": "brewing",
        }
        self.assertTrue(trader.record_niuone_lifecycle_observation(
            position,
            observed_at="2026-07-14 09:25:01",
            source="entry_signal",
            complete_from_entry=True,
        ))
        position.update({
            "mainline_state": "emerging",
            "mainline_score": 72.0,
            "niuone_lifecycle_stage": "markup",
        })
        self.assertTrue(trader.record_niuone_lifecycle_observation(
            position,
            observed_at="2026-07-15 10:00:00",
            source="mainline_scan",
        ))
        position.update({
            "mainline_state": "diverging",
            "mainline_score": 68.0,
            "niuone_lifecycle_stage": "divergence",
        })
        evidence = trader.niuone_lifecycle_exit_evidence_from_position(
            position,
            observed_at="2026-07-16 14:45:00",
        )

        self.assertTrue(evidence["path_complete_from_entry"])
        self.assertEqual(
            evidence["stage_sequence"],
            ["brewing", "markup", "divergence"],
        )
        self.assertEqual(evidence["transition_count"], 2)
        self.assertEqual(
            evidence["exit_niuone_lifecycle_stage"],
            "divergence",
        )
        self.assertTrue(evidence["reached_markup"])
        self.assertTrue(evidence["reached_divergence"])
        self.assertFalse(evidence["reached_climax"])
        self.assertEqual(
            evidence["path"][1]["label"],
            "主线主升",
        )

    def test_lost_leader_status_requires_two_observed_trading_days_before_exit(self):
        state = {
            "positions": {
                "600000": {
                    "code": "600000", "name": "牛牛测试", "qty": 400,
                    "avg_cost": 10.0, "last_price": 10.2, "close": 10.2,
                    "buy_strategy": "niu_leader", "industry": "半导体",
                    "entry_stop_price": 9.5, "entry_stop_source": "niu_structure_low",
                    "stock_role": "leader", "stock_leader_rank": 1,
                    "stock_leader_tier": True, "stock_strong": True,
                    "buy_date_lots": {"2026-07-10": 400},
                }
            }
        }

        def payload(day: str, stock: dict) -> dict:
            return {
                "generated_at": f"{day} 14:30:00",
                "niuone_context": {
                    "previous_trading_day": {
                        "2026-07-15": "2026-07-14",
                        "2026-07-16": "2026-07-15",
                        "2026-07-17": "2026-07-16",
                    }[day],
                    "market": {"state": "rotation", "score": 72, "hard_stop": False, "allow_new_buys": True},
                    "themes": {"半导体": {"score": 82, "state": "mainline", "raw_state": "mainline"}},
                    "stocks": {"600000": {"industry": "半导体", "theme_rank": 80, **stock}},
                },
            }

        trader.sync_niuone_position_context(state, payload("2026-07-15", {}))
        self.assertNotIn("niu_leader_lost_count", state["positions"]["600000"])

        trader.sync_niuone_position_context(
            state,
            payload("2026-07-16", {"role": "core", "leader_rank": 4, "leader_tier": False, "strong": True}),
        )
        trader.sync_niuone_position_context(
            state,
            payload("2026-07-16", {"role": "core", "leader_rank": 4, "leader_tier": False, "strong": True}),
        )
        self.assertEqual(state["positions"]["600000"]["niu_leader_lost_count"], 1)
        first_reduction = trader.evaluate_sell_signal(
            "600000",
            state["positions"]["600000"],
            "2026-07-16",
            time_exit_allowed=False,
        )
        self.assertEqual(first_reduction["signal"], "niu_leader_lost")
        self.assertEqual(first_reduction["soft_exit_stage"], "reduce")
        self.assertEqual(first_reduction["sell_ratio"], 0.5)
        state["positions"]["600000"]["soft_exit_reduced"] = True

        trader.sync_niuone_position_context(
            state,
            payload("2026-07-17", {"role": "core", "leader_rank": 4, "leader_tier": False, "strong": True}),
        )
        signal = trader.evaluate_sell_signal(
            "600000",
            state["positions"]["600000"],
            "2026-07-17",
            time_exit_allowed=False,
        )
        self.assertEqual(signal["signal"], "niu_leader_lost")
        self.assertIn("连续2个交易日跌出强势行业龙头梯队", signal["reason"])

    def test_climax_runner_delays_relative_rank_exit_and_uses_wider_trail(self):
        pos = {
            "qty": 600,
            "avg_cost": 10.0,
            "last_price": 10.6,
            "close": 10.6,
            "highest_price": 12.0,
            "buy_strategy": "niu_leader",
            "industry": "被动元件",
            "entry_stop_price": 9.0,
            "entry_stop_source": "niu_structure_low",
            "mainline_score": 70.0,
            "mainline_state": "diverging",
            "mainline_weak_count": 0,
            "stock_leader_rank": 12,
            "stock_leader_tier": False,
            "stock_strong": True,
            "niu_leader_lost_count": 2,
            "niuone_lifecycle_stage": "divergence",
            "niuone_lifecycle_climax_partial_done": True,
            "partial_tp_done": True,
            "atr20": 0.5,
            "buy_date_lots": {"2026-07-10": 600},
        }

        self.assertTrue(NIUONE_CLIMAX_RUNNER_ENABLED)
        self.assertEqual(
            NIUONE_CLIMAX_RUNNER_LEADER_LOSS_CONFIRMATIONS,
            3,
        )
        self.assertEqual(NIUONE_CLIMAX_RUNNER_TRAILING_ATR, 3.0)
        self.assertIsNone(trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-16",
            time_exit_allowed=False,
        ))
        self.assertEqual(pos["niu_trailing_stop"], 10.5)

        missing_theme_context = {
            **pos,
            "mainline_state": "",
            "niu_leader_lost_count": 2,
        }
        fail_closed = trader.evaluate_sell_signal(
            "600000",
            missing_theme_context,
            "2026-07-16",
            time_exit_allowed=False,
        )
        self.assertEqual(fail_closed["signal"], "niu_leader_lost")
        self.assertIn("连续2个交易日", fail_closed["reason"])

        pos["niu_leader_lost_count"] = 3
        signal = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-17",
            time_exit_allowed=False,
        )

        self.assertEqual(signal["signal"], "niu_leader_lost")
        self.assertIn("连续3个交易日", signal["reason"])
        self.assertIn("高潮减仓后余仓", signal["reason"])

    def test_niuone_uses_one_r_partial_and_independent_risk_budget(self):
        self.assertEqual(
            NIUONE_ABSOLUTE_POSITION_CAP_PCT["niu_reversal_probe"],
            10.0,
        )
        self.assertEqual(niuone_risk_budget("offensive")["per_trade_risk_pct"], 1.5)
        self.assertEqual(
            niuone_risk_budget("offensive", "niu_reversal_probe")["per_trade_risk_pct"],
            0.35,
        )
        self.assertEqual(
            niuone_risk_budget("recovery", "niu_reversal_probe")["max_sector_position_pct"],
            8.0,
        )
        self.assertEqual(
            niuone_structural_stop_limits("offensive", "niu_reversal_probe"),
            {"max_stop_distance_pct": 6.0, "max_stop_atr": 2.0},
        )
        self.assertEqual(niuone_risk_budget("offensive")["max_total_position_pct"], 70.0)
        self.assertEqual(niuone_risk_budget("rotation")["max_total_position_pct"], 55.0)
        self.assertEqual(
            niuone_risk_budget("rotation", "niu_reversal_probe")[
                "per_trade_risk_pct"
            ],
            1.0,
        )
        self.assertEqual(
            niuone_risk_budget("rotation", "niu_reversal_probe")[
                "max_sector_risk_pct"
            ],
            1.0,
        )
        self.assertEqual(niuone_risk_budget("defensive")["per_trade_risk_pct"], 0.30)
        self.assertEqual(niuone_risk_budget("defensive")["max_open_risk_pct"], 0.90)
        self.assertEqual(
            niuone_risk_budget("defensive", "niu_reversal_probe")["per_trade_risk_pct"],
            0.15,
        )
        pos = {
            "qty": 400,
            "avg_cost": 10.0,
            "last_price": 11.0,
            "close": 11.0,
            "buy_strategy": "niu_leader",
            "entry_stop_price": 9.0,
            "entry_stop_source": "niu_structure_low",
            "mainline_score": 82,
            "mainline_state": "mainline",
            "mainline_weak_count": 0,
            "buy_date_lots": {"2026-07-15": 400},
        }

        signal = trader.evaluate_sell_signal("600000", pos, "2026-07-16", time_exit_allowed=False)

        self.assertEqual(NIUONE_PARTIAL_TAKE_PROFIT_R, 1.0)
        self.assertEqual(signal["signal"], "niu_r_partial")
        self.assertEqual(signal["sell_ratio"], 0.45)

    def test_niuone_climax_reduces_one_third_only_once(self):
        pos = {
            "qty": 600,
            "avg_cost": 10.0,
            "last_price": 10.5,
            "close": 10.5,
            "buy_strategy": "niu_leader",
            "entry_stop_price": 9.0,
            "entry_stop_source": "niu_structure_low",
            "mainline_score": 82,
            "mainline_state": "mainline",
            "mainline_weak_count": 0,
            "niuone_lifecycle_stage": "climax",
            "buy_date_lots": {"2026-07-15": 600},
        }

        signal = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-16",
            time_exit_allowed=False,
        )

        self.assertEqual(signal["signal"], "niu_lifecycle_climax_partial")
        self.assertEqual(
            signal["sell_ratio"],
            NIUONE_LIFECYCLE_CLIMAX_PARTIAL_RATIO,
        )
        pos["niuone_lifecycle_climax_partial_done"] = True
        self.assertIsNone(trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-16",
            time_exit_allowed=False,
        ))

    def test_niuone_markup_pullback_reduces_before_waiting_to_readd(self):
        pos = {
            "qty": 600,
            "avg_cost": 10.0,
            "last_price": 11.0,
            "close": 11.0,
            "buy_strategy": "niu_leader",
            "entry_stop_price": 9.0,
            "entry_stop_source": "niu_structure_low",
            "mainline_score": 70,
            "mainline_state": "diverging",
            "mainline_weak_count": 0,
            "niu_leader_lost_count": 0,
            "niuone_lifecycle_stage": "divergence",
            "stock_leader_tier": True,
            "stock_strong": True,
            "atr20": 0.5,
            "niuone_markup_rebalance_cycle_peak_price": 12.0,
            "niuone_markup_rebalance_observation_count": 2,
            "niuone_markup_rebalance_last_observation": "2026-07-15",
            "buy_date_lots": {"2026-07-10": 600},
        }

        signal = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-16",
            time_exit_allowed=True,
        )

        self.assertEqual(signal["signal"], "niu_markup_rebalance_partial")
        self.assertEqual(
            signal["sell_ratio"],
            NIUONE_MARKUP_REBALANCE_TRIM_RATIO,
        )
        self.assertIn("释放波段仓位", signal["reason"])

    def test_niuone_reversal_uses_regime_aware_early_profit_protection(self):
        offensive = {
            "qty": 400,
            "avg_cost": 10.0,
            "last_price": 10.75,
            "close": 10.75,
            "buy_strategy": "niu_reversal_probe",
            "entry_stop_price": 9.0,
            "entry_stop_source": "niu_reversal_right_low",
            "entry_market_regime": "offensive",
            "market_regime": "rotation",
            "mainline_score": 70,
            "mainline_state": "candidate",
            "buy_date_lots": {"2026-07-15": 400},
        }

        early = trader.evaluate_sell_signal(
            "600000", offensive, "2026-07-16", time_exit_allowed=False,
        )

        self.assertEqual(
            NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES,
            frozenset({"offensive", "recovery", "defensive"}),
        )
        self.assertEqual(NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_R, 0.75)
        self.assertEqual(
            NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_RATIO, 0.5,
        )
        self.assertEqual(early["signal"], "niu_r_partial")
        self.assertEqual(early["sell_ratio"], 0.5)
        self.assertIn("0.75R", early["reason"])

        rotation = {
            **offensive,
            "entry_market_regime": "rotation",
            "market_regime": "offensive",
            "partial_tp_done": False,
        }
        self.assertIsNone(trader.evaluate_sell_signal(
            "600000", rotation, "2026-07-16", time_exit_allowed=False,
        ))
        rotation["last_price"] = 11.0
        rotation["close"] = 11.0
        normal = trader.evaluate_sell_signal(
            "600000", rotation, "2026-07-16", time_exit_allowed=False,
        )
        self.assertEqual(normal["sell_ratio"], 0.45)
        self.assertIn("1R", normal["reason"])

    def test_niuone_intraday_exits_use_realtime_price_not_stale_daily_close(self):
        position = {
            "qty": 400,
            "avg_cost": 10.0,
            "last_price": 10.75,
            "close": 10.2,
            "buy_strategy": "niu_reversal_probe",
            "entry_stop_price": 9.0,
            "entry_stop_source": "niu_reversal_right_low",
            "entry_market_regime": "offensive",
            "mainline_score": 70,
            "mainline_state": "candidate",
            "buy_date_lots": {"2026-07-15": 400},
        }

        partial = trader.evaluate_sell_signal(
            "600000", position, "2026-07-16", time_exit_allowed=False,
        )

        self.assertEqual(partial["signal"], "niu_r_partial")
        self.assertIn("现价10.75", partial["reason"])
        self.assertEqual(position["highest_price"], 10.75)

        position.update({
            "last_price": 10.9,
            "close": 11.5,
            "partial_tp_done": True,
            "highest_price": 12.0,
            "atr20": 0.5,
        })
        trailing = trader.evaluate_sell_signal(
            "600000", position, "2026-07-16", time_exit_allowed=False,
        )

        self.assertEqual(trailing["signal"], "niu_atr_trail")
        self.assertIn("现价10.90", trailing["reason"])

    def test_niuone_context_sync_backfills_entry_regime_only_once(self):
        state = {
            "positions": {
                "600000": {
                    "code": "600000",
                    "qty": 100,
                    "avg_cost": 10.0,
                    "last_price": 10.0,
                    "buy_strategy": "niu_reversal_probe",
                    "industry": "半导体",
                    "risk_budget_regime": "offensive",
                },
            },
        }

        def payload(regime: str, day: str) -> dict:
            return {
                "generated_at": f"{day} 14:30:00",
                "niuone_context": {
                    "market": {
                        "state": regime,
                        "score": 70,
                        "hard_stop": False,
                        "allow_new_buys": True,
                    },
                    "themes": {
                        "半导体": {
                            "score": 70,
                            "state": "candidate",
                            "raw_state": "candidate",
                        },
                    },
                    "stocks": {
                        "600000": {
                            "industry": "半导体",
                            "theme_rank": 50,
                        },
                    },
                },
            }

        trader.sync_niuone_position_context(
            state, payload("rotation", "2026-07-16"),
        )
        position = state["positions"]["600000"]
        self.assertEqual(position["entry_market_regime"], "offensive")
        self.assertEqual(position["market_regime"], "rotation")

        trader.sync_niuone_position_context(
            state, payload("recovery", "2026-07-17"),
        )
        self.assertEqual(position["entry_market_regime"], "offensive")
        self.assertEqual(position["market_regime"], "recovery")

    def test_niuone_reversal_exits_after_one_observed_theme_failure(self):
        state = {
            "positions": {
                "600000": {
                    "code": "600000", "name": "牛牛反转", "qty": 400,
                    "avg_cost": 10.0, "last_price": 10.1, "close": 10.1,
                    "buy_strategy": "niu_reversal_probe", "industry": "半导体",
                    "entry_stop_price": 9.5,
                    "entry_stop_source": "niu_reversal_right_low",
                    "buy_date_lots": {"2026-07-14": 400},
                }
            }
        }
        payload = {
            "generated_at": "2026-07-16 14:30:00",
            "niuone_context": {
                "previous_trading_day": "2026-07-15",
                "market": {
                    "state": "rotation", "score": 55,
                    "hard_stop": False, "allow_new_buys": True,
                },
                "themes": {
                    "半导体": {"score": 50, "state": "fading", "raw_state": "fading"},
                },
                "stocks": {
                    "600000": {"industry": "半导体", "theme_rank": 20},
                },
            },
        }

        trader.sync_niuone_position_context(state, payload)
        signal = trader.evaluate_sell_signal(
            "600000",
            state["positions"]["600000"],
            "2026-07-16",
            time_exit_allowed=False,
        )

        self.assertEqual(signal["signal"], "niu_reversal_theme_failed")

    def test_niuone_partial_profit_protects_runner_at_cost(self):
        pos = {
            "qty": 200,
            "avg_cost": 10.0,
            "last_price": 9.99,
            "close": 9.99,
            "buy_strategy": "niu_reversal_probe",
            "entry_stop_price": 9.0,
            "entry_stop_source": "niu_reversal_right_low",
            "partial_tp_done": True,
            "mainline_score": 80,
            "mainline_state": "mainline",
            "mainline_weak_count": 0,
            "buy_date_lots": {"2026-07-15": 200},
        }

        signal = trader.evaluate_sell_signal(
            "600000", pos, "2026-07-16", time_exit_allowed=False,
        )

        self.assertEqual(signal["signal"], "niu_structure_stop")
        self.assertEqual(pos["entry_stop_price"], 10.0)
        self.assertEqual(pos["entry_stop_source"], "niu_breakeven")

    def test_niuone_time_exit_counts_trading_days_across_a_weekend(self):
        pos = {
            "qty": 100,
            "avg_cost": 10.0,
            "last_price": 10.0,
            "close": 10.0,
            "buy_strategy": "niu_emerging",
            "entry_stop_price": 9.0,
            "entry_stop_source": "niu_structure_low",
            "mainline_score": 70,
            "mainline_state": "emerging",
            "mainline_weak_count": 0,
            "buy_date_lots": {"2026-07-24": 100},
        }

        monday = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-27",
            time_exit_allowed=True,
        )
        tuesday = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-28",
            time_exit_allowed=True,
        )

        self.assertIsNone(monday)
        self.assertEqual(tuesday["signal"], "niu_emerging_unconfirmed")

    def test_reversal_probe_observes_t1_and_reduces_on_unconfirmed_t2(self):
        pos = {
            "qty": 100,
            "avg_cost": 10.0,
            "last_price": 10.0,
            "close": 10.0,
            "buy_strategy": "niu_reversal_probe",
            "entry_stop_price": 9.7,
            "entry_stop_source": "niu_reversal_low",
            "mainline_score": 45,
            "mainline_state": "candidate",
            "buy_date_lots": {"2026-07-24": 100},
        }

        monday = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-27",
            time_exit_allowed=True,
        )
        self.assertIsNone(monday)

        pos["mainline_cross_day_persistent"] = True
        confirmed_monday = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-27",
            time_exit_allowed=True,
        )
        self.assertIsNone(confirmed_monday)

        confirmed_tuesday = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-28",
            time_exit_allowed=True,
        )
        self.assertIsNone(confirmed_tuesday)

        pos.pop("mainline_cross_day_persistent", None)
        tuesday = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-28",
            time_exit_allowed=True,
        )
        self.assertEqual(tuesday["signal"], "niu_reversal_not_upgraded")
        self.assertEqual(tuesday["soft_exit_stage"], "reduce")
        self.assertEqual(tuesday["sell_ratio"], 0.5)

    def test_daily_v_reversal_uses_three_day_no_progress_exit(self):
        pos = {
            "qty": 100,
            "avg_cost": 10.0,
            "last_price": 10.1,
            "close": 10.1,
            "buy_strategy": "niu_reversal_probe",
            "reversal_basis": "daily_v",
            "entry_stop_price": 9.5,
            "entry_stop_source": "niu_reversal_right_low",
            "mainline_score": 55,
            "mainline_state": "candidate",
            "buy_date_lots": {"2026-07-24": 100},
        }

        monday = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-27",
            time_exit_allowed=True,
        )
        tuesday = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-28",
            time_exit_allowed=True,
        )
        wednesday = trader.evaluate_sell_signal(
            "600000",
            pos,
            "2026-07-29",
            time_exit_allowed=True,
        )

        self.assertIsNone(monday)
        self.assertIsNone(tuesday)
        self.assertEqual(wednesday["signal"], "niu_reversal_no_progress")

    def test_niuone_full_book_replaces_lower_priority_holding(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {"turnover": 4.0, "price": 10.0, "name": "牛牛测试", "source": "test"}
            positions = {
                f"60001{index}": {
                    "code": f"60001{index}",
                    "name": f"已有持仓{index}",
                    "qty": 100,
                    "avg_cost": 10.0,
                    "last_price": 10.0,
                    "buy_strategy": "niu_leader",
                    "industry": f"行业{index}",
                    "entry_stop_price": 9.5,
                    "gap_buffer_pct": 1.0,
                    "execution_buffer_pct": 0.2,
                    "effective_loss_distance_pct": 6.2,
                    "buy_date_lots": {"2026-07-24": 100},
                }
                for index in range(trader.NIUONE_MAX_OPEN_POSITIONS)
            }
            state = {"cash": 95000.0, "positions": positions, "trade_log": []}
            candidate = niu_candidate(code="600099", industry="电子", sector="电子")
            decision = {"actions": [{"action": "BUY", "code": "600099", "shares": 100, "reason": "牛牛领航确认"}]}
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }

            executed = trader.execute_actions(state, decision, [candidate], True, "连续竞价交易时段", market)

            self.assertEqual(
                [(item["action"], item["code"]) for item in executed],
                [("SELL", "600010"), ("BUY", "600099")],
            )
            self.assertEqual(
                executed[0]["sell_execution_source"],
                "priority_replacement",
            )
            self.assertEqual(
                executed[1]["replacement_source_code"],
                "600010",
            )
            self.assertEqual(
                len(state["positions"]),
                trader.NIUONE_MAX_OPEN_POSITIONS,
            )
            self.assertNotIn("600010", state["positions"])
            self.assertIn("600099", state["positions"])
            self.assertEqual(
                decision["actions"][0]["intent"],
                "REPLACE",
            )
            self.assertEqual(
                decision["niuone_replacement_plan"][0]["buy_code"],
                "600099",
            )
            observation = decision["niuone_capacity_observation"]
            self.assertEqual(
                observation["open_position_count"],
                trader.NIUONE_MAX_OPEN_POSITIONS,
            )
            self.assertEqual(observation["candidates"][0]["code"], "600099")
            self.assertEqual(
                observation["candidates"][0]["outcome"],
                "replacement_planned",
            )
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_niuone_full_book_keeps_holding_when_candidate_priority_is_lower(self):
        positions = {
            f"60001{index}": {
                "code": f"60001{index}",
                "name": f"强持仓{index}",
                "qty": 100,
                "buy_strategy": "niu_leader",
                "buy_date_lots": {"2026-07-24": 100},
                "current_decision_score": 9.2,
                "mainline_score": 90,
                "mainline_state": "mainline",
                "mainline_confirmed": True,
                "niuone_lifecycle_stage": "markup",
                "stock_strong": True,
                "stock_leader_tier": True,
            }
            for index in range(trader.NIUONE_MAX_OPEN_POSITIONS)
        }
        state = {"positions": positions}
        candidate = niu_candidate(
            code="600099",
            industry="电子",
            sector="电子",
        )
        candidate.update({
            "best_strategy": "niu_reversal_probe",
            "best_decision_score": 7.7,
            "mainline_score": 55,
            "mainline_state": "brewing",
            "niuone_lifecycle_stage": "brewing",
            "stock_strong": False,
            "stock_leader_tier": False,
        })
        decision = {
            "actions": [{
                "action": "BUY",
                "code": "600099",
                "shares": 100,
                "reason": "牛牛试仓候选",
            }]
        }

        actions = trader.prepare_niuone_portfolio_actions(
            decision,
            state,
            [candidate],
            execution_date="2026-07-25",
        )

        self.assertEqual(actions[0]["action"], "HOLD")
        self.assertEqual(actions[0]["intent"], "HOLD_PRIORITY")
        self.assertEqual(decision["niuone_replacement_plan"], [])
        self.assertIn("未达到换仓滞回门槛", actions[0]["reason"])
        self.assertEqual(
            decision["execution_blocks"][0]["category"],
            "portfolio_priority",
        )

    def test_niuone_replacement_requires_material_priority_margin(self):
        holding = {
            "strategy_id": "niu_emerging",
            "score": 7.0,
            "mainline_score": 60,
            "mainline_state": "emerging",
        }
        immaterial = {**holding, "score": 8.0}
        material = {**holding, "score": 8.5}

        self.assertEqual(NIUONE_REPLACEMENT_PRIORITY_MARGIN, 3.0)
        self.assertFalse(niuone_priority_is_higher(
            immaterial,
            holding,
            incoming_strategy="niu_emerging",
            holding_strategy="niu_emerging",
        ))
        self.assertTrue(niuone_priority_is_higher(
            material,
            holding,
            incoming_strategy="niu_emerging",
            holding_strategy="niu_emerging",
        ))

    def test_niuone_full_book_logs_buyable_candidate_without_model_buy(self):
        positions = {
            f"60001{index}": {
                "code": f"60001{index}",
                "name": f"已有持仓{index}",
                "qty": 100,
                "avg_cost": 10.0,
                "last_price": 10.0,
                "buy_strategy": "niu_leader",
            }
            for index in range(trader.NIUONE_MAX_OPEN_POSITIONS)
        }
        state = {"cash": 50000.0, "positions": positions, "trade_log": []}
        candidate = niu_candidate(
            code="600099",
            name="满仓新候选",
            industry="电子",
            sector="电子",
        )
        decision = {"summary": "维持现有组合", "actions": []}

        executed = trader.execute_actions(
            state,
            decision,
            [candidate],
            True,
            "连续竞价交易时段",
            {
                "allow_new_buys": True,
                "max_open_positions": trader.NIUONE_MAX_OPEN_POSITIONS,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            },
        )

        self.assertEqual(executed, [])
        observation = decision["niuone_capacity_observation"]
        self.assertEqual(observation["status"], "full")
        self.assertEqual(observation["candidate_count"], 1)
        self.assertEqual(observation["candidates"][0]["code"], "600099")
        self.assertEqual(
            observation["candidates"][0]["outcome"],
            "candidate_recorded",
        )
        self.assertIn("满仓新候选", observation["summary"])
        self.assertEqual(state["trade_log"], [])

    def test_niuone_uses_expanded_configured_book_before_replacement(self):
        original_limit = trader.NIUONE_MAX_OPEN_POSITIONS
        try:
            trader.NIUONE_MAX_OPEN_POSITIONS = 10
            positions = {
                f"60001{index}": {
                    "code": f"60001{index}",
                    "qty": 100,
                    "buy_strategy": "niu_leader",
                    "buy_date_lots": {"2026-07-24": 100},
                }
                for index in range(5)
            }
            candidate = niu_candidate(
                code="600099",
                industry="电子",
                sector="电子",
            )
            decision = {
                "actions": [{
                    "action": "BUY",
                    "code": "600099",
                    "shares": 100,
                    "reason": "牛牛新增候选",
                }],
            }

            actions = trader.prepare_niuone_portfolio_actions(
                decision,
                {"positions": positions},
                [candidate],
                execution_date="2026-07-25",
            )

            self.assertEqual(actions[0]["action"], "BUY")
            self.assertEqual(decision["niuone_replacement_plan"], [])
            self.assertNotIn("execution_blocks", decision)
        finally:
            trader.NIUONE_MAX_OPEN_POSITIONS = original_limit

    def test_niuone_replacement_preflight_keeps_holding_when_buy_cannot_fill(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (
                True,
                "连续竞价交易时段",
            )
            trader.execution_quote = lambda code: {
                "turnover": 4.0,
                "price": 11.0 if code == "600099" else 10.0,
                "prev_close": 10.0,
                "name": code,
                "source": "test",
            }
            positions = {
                f"60001{index}": {
                    "code": f"60001{index}",
                    "name": f"已有持仓{index}",
                    "qty": 100,
                    "avg_cost": 10.0,
                    "last_price": 10.0,
                    "buy_strategy": "niu_leader",
                    "industry": f"行业{index}",
                    "entry_stop_price": 9.5,
                    "gap_buffer_pct": 1.0,
                    "execution_buffer_pct": 0.2,
                    "effective_loss_distance_pct": 6.2,
                    "buy_date_lots": {"2026-07-24": 100},
                }
                for index in range(trader.NIUONE_MAX_OPEN_POSITIONS)
            }
            state = {
                "cash": 95000.0,
                "positions": positions,
                "trade_log": [],
            }
            candidate = niu_candidate(
                code="600099",
                industry="电子",
                sector="电子",
                signal_theme="电子",
            )
            decision = {
                "actions": [{
                    "action": "BUY",
                    "code": "600099",
                    "shares": 100,
                    "reason": "牛牛领航确认",
                }],
            }

            executed = trader.execute_actions(
                state,
                decision,
                [candidate],
                True,
                "连续竞价交易时段",
                {
                    "allow_new_buys": True,
                    "max_open_positions": 6,
                    "max_new_buys_per_decision": 1,
                    "max_total_position_pct": 80,
                    "min_cash_reserve_pct": 20,
                },
            )

            self.assertEqual(executed, [])
            self.assertEqual(
                len(state["positions"]),
                trader.NIUONE_MAX_OPEN_POSITIONS,
            )
            self.assertIn("600010", state["positions"])
            self.assertNotIn("600099", state["positions"])
            self.assertEqual(decision["niuone_replacement_plan"], [])
            self.assertTrue(any(
                block["category"] == "replacement_preflight"
                for block in decision["execution_blocks"]
            ))
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

    def test_niuone_openings_ignore_market_evaluation_count_and_pause(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (
                True,
                "连续竞价交易时段",
            )
            trader.execution_quote = lambda code: {
                "turnover": 4.0,
                "price": 10.0,
                "prev_close": 10.0,
                "name": f"牛牛{code}",
                "source": "test",
            }
            candidates = [
                niu_candidate(
                    code=code,
                    industry=f"行业{index}",
                    sector=f"行业{index}",
                    signal_theme=f"行业{index}",
                )
                for index, code in enumerate(("600001", "600002"), start=1)
            ]
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {
                "actions": [
                    {
                        "action": "BUY",
                        "code": candidate["code"],
                        "shares": 100,
                        "reason": "牛牛领航确认",
                    }
                    for candidate in candidates
                ]
            }
            market = {
                "tone": "defensive",
                "tone_label": "防守",
                "allow_new_buys": False,
                "max_open_positions": 0,
                "max_new_buys_per_decision": 0,
                "max_total_position_pct": 35,
                "min_cash_reserve_pct": 60,
                "buy_budget_multiplier": 0.35,
            }

            refinement = trader.refine_overlimit_buy_actions(
                decision,
                state,
                candidates,
                {
                    "positions": [],
                    "trade_log": [],
                    "cash": 100000.0,
                    "total_equity": 100000.0,
                },
                market,
            )
            executed = trader.execute_actions(
                state,
                decision,
                candidates,
                True,
                "连续竞价交易时段",
                market,
            )
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote

        self.assertIsNone(refinement)
        self.assertEqual([item["code"] for item in executed], ["600001", "600002"])
        self.assertEqual(set(state["positions"]), {"600001", "600002"})
        self.assertFalse(any(
            block["category"] == "market_guidance"
            for block in decision["execution_blocks"]
        ))

    def test_niuone_new_positions_are_not_limited_by_day_or_same_theme_count(self):
        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        original_today_key = trader.today_key
        original_now_ts = trader.now_ts
        try:
            trader.is_a_share_execution_time = lambda dt=None: (
                True,
                "连续竞价交易时段",
            )
            trader.execution_quote = lambda code: {
                "turnover": 4.0,
                "price": 10.0,
                "prev_close": 10.0,
                "name": f"牛牛{code}",
                "source": "test",
            }
            trader.today_key = lambda: "2026-07-24"
            trader.now_ts = lambda: "2026-07-24 10:00:00"
            market = {
                "allow_new_buys": True,
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "max_total_position_pct": 80,
                "min_cash_reserve_pct": 20,
            }
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}

            for code in ("600001", "600002", "600003"):
                candidate = niu_candidate(
                    code=code,
                    industry="化学制药",
                    sector="化学制药",
                    signal_theme="化学制药",
                )
                decision = {
                    "actions": [{
                        "action": "BUY",
                        "code": code,
                        "shares": 100,
                        "reason": "牛牛领航确认",
                    }]
                }
                executed = trader.execute_actions(
                    state,
                    decision,
                    [candidate],
                    True,
                    "连续竞价交易时段",
                    market,
                )
                self.assertEqual(len(executed), 1)
            self.assertEqual(set(state["positions"]), {
                "600001",
                "600002",
                "600003",
            })
            self.assertEqual(len(state["trade_log"]), 3)
            self.assertEqual(
                backtest_niuone_exits.NIUONE_MAX_NEW_POSITIONS_PER_SESSION,
                NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY,
            )
            self.assertIsNone(NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY)
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote
            trader.today_key = original_today_key
            trader.now_ts = original_now_ts

    def test_niuone_daily_opening_count_is_idempotent_and_excludes_adds(self):
        opening = {
            "time": "2026-07-24 10:00:00",
            "action": "BUY",
            "code": "600001",
            "position_before_qty": 0,
            "position_opened": True,
            "buy_strategy": "niu_leader",
        }
        state = {
            "trade_log": [
                opening,
                dict(opening),
                {
                    **opening,
                    "time": "2026-07-24 10:30:00",
                    "position_before_qty": 100,
                    "position_opened": False,
                },
                {
                    **opening,
                    "code": "600002",
                    "buy_strategy": "shaofu_b1",
                },
                {
                    **opening,
                    "time": "2026-07-23 10:00:00",
                    "code": "600003",
                },
                {
                    **opening,
                    "code": "600004",
                    "buy_strategy": "",
                    "strategy_mark": {"strategy_id": "niu_pullback"},
                },
            ]
        }

        self.assertEqual(
            trader.niuone_opened_position_codes_on_date(
                state,
                "2026-07-24",
            ),
            {"600001", "600004"},
        )

    def test_mainline_detection_to_selection_buy_and_automatic_sell_runs_end_to_end(self):
        prepared = self._prepared_market()
        for index, item in enumerate(
            row for row in prepared if row["industry"] == "半导体"
        ):
            item["quote"]["change_pct"] = 4.0 - index * 0.5
        market_snapshot = {
            "up": 120,
            "down": 30,
            "median_change_pct": 0.8,
            "limit_up": 12,
            "limit_down": 1,
            "core_index_count": 3,
            "index_below_ma20_count": 0,
        }
        flow_rows = {"inflow": [{"name": "半导体", "net_flow_yi": 30}], "outflow": []}
        dragon_tiger = {
            "available": True,
            "date": "2026-07-24",
            "items": [{"code": "600000", "net_ratio_pct": 20}],
        }
        first = build_niuone_context(
            prepared,
            market_snapshot=market_snapshot,
            flow_rows=flow_rows,
            dragon_tiger_snapshot=dragon_tiger,
            as_of_date="2026-07-24",
            previous_trading_day="2026-07-23",
        )
        context = build_niuone_context(
            prepared,
            market_snapshot=market_snapshot,
            flow_rows=flow_rows,
            previous_context=first,
            dragon_tiger_snapshot=dragon_tiger,
            as_of_date="2026-07-27",
            previous_trading_day="2026-07-24",
        )
        rows = make_rows("600000", "半导体", 0.01)

        multi = analyze_enriched_rows(rows, {"niu_leader": score_niu_leader}, context)

        self.assertIsNotNone(multi)
        self.assertEqual(multi["best_strategy"], "niu_leader")
        scored = multi["strategies"]["niu_leader"]
        self.assertTrue(scored["actionable"])
        candidate = {
            **scored,
            "code": "600000",
            "name": "牛牛全链路",
            "price": rows[-1]["close"],
            "best_strategy": multi["best_strategy"],
            "best_score": multi["best_score"],
        }
        self.assertEqual(select_trade_candidates([candidate], limit=1), [candidate])

        original_time = trader.is_a_share_execution_time
        original_quote = trader.execution_quote
        try:
            trader.is_a_share_execution_time = lambda dt=None: (True, "连续竞价交易时段")
            trader.execution_quote = lambda code: {
                "turnover": 4.0,
                "price": rows[-1]["close"],
                "name": "牛牛全链路",
                "source": "test",
            }
            state = {"cash": 100000.0, "positions": {}, "trade_log": []}
            decision = {"actions": [{"action": "BUY", "code": "600000", "shares": 100, "reason": "牛牛领航确认"}]}
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
            position = state["positions"]["600000"]
            self.assertEqual(position["buy_strategy"], "niu_leader")
            self.assertEqual(position["strategy_mark_id"], "niu_leader")
            position["buy_date_lots"] = {"2026-07-24": 100}
            position["last_price"] = position["entry_stop_price"] - 0.01
            position["close"] = position["last_price"]

            sold = trader.check_auto_exits(state, datetime(2026, 7, 27, 10, 0, 0))

            self.assertEqual(len(sold), 1)
            self.assertEqual(sold[0]["action"], "SELL")
            self.assertEqual(sold[0]["exit_signal"], "niu_structure_stop")
            self.assertEqual(sold[0]["buy_strategy"], "niu_leader")
            self.assertNotIn("600000", state["positions"])
        finally:
            trader.is_a_share_execution_time = original_time
            trader.execution_quote = original_quote


if __name__ == "__main__":
    unittest.main()
