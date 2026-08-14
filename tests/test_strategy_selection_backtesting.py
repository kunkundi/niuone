from __future__ import annotations

import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from backtesting.selection import (  # noqa: E402
    HistoricalBar,
    NiuOneHistoricalContextProvider,
    PositionExitSignal,
    RegisteredScorerSelector,
    ReplaySelectionStrategy,
    SectorTideHistoricalContextProvider,
    SelectionBacktestConfig,
    SelectionBacktestError,
    SelectionCostModel,
    SelectionSignal,
    a_share_price_limits,
    build_selection_replay_tape,
    run_selection_backtest,
)
from backtesting.niuone_exits import (  # noqa: E402
    NiuOneDailyExitStrategy,
    NiuOneStrategyBacktestPolicy,
)
import backtesting.selection as selection_module  # noqa: E402
from strategies.scoring import enrich_rows as enrich_strategy_rows  # noqa: E402


def daily_bar(
    trading_date: str,
    open_price: float,
    close: float | None = None,
    *,
    high: float | None = None,
    low: float | None = None,
    volume: float = 10_000,
    **extra,
) -> dict:
    close = open_price if close is None else close
    return {
        "date": trading_date,
        "open": open_price,
        "high": max(open_price, close) if high is None else high,
        "low": min(open_price, close) if low is None else low,
        "close": close,
        "volume": volume,
        **extra,
    }


class StrategySelectionBacktestingTests(unittest.TestCase):
    def test_replay_tape_reuses_signals_and_daily_exit_context(self):
        rows = [
            daily_bar("2026-01-05", 10.0, 10.0),
            daily_bar("2026-01-06", 10.0, 10.0),
            daily_bar("2026-01-07", 10.5, 10.5),
        ]

        class CountingSelector:
            strategy_ids = ("recorded",)

            def __init__(self):
                self.calls = 0
                self.current = {}

            def on_close(self, context):
                self.calls += 1
                self.current = {
                    "exit_now": context.date == "2026-01-07",
                    "unused": "large scorer payload",
                }
                if context.date == "2026-01-05":
                    return [SelectionSignal(
                        "600000",
                        strategy_id="recorded",
                        score=9.0,
                    )]
                return []

            def latest_scored(self, _symbol, _strategy_id):
                return self.current

            def latest_cross_section(self):
                return {
                    "半导体": {
                        "score": self.calls,
                        "unused": "large provider payload",
                    }
                }

        class RecordedExit:
            def on_entry(self, _signal, _entry_bar, _entry_price):
                return {}

            def on_close(self, position, _context, selector):
                scored = selector.latest_scored(
                    position["symbol"],
                    position["strategy_id"],
                ) or {}
                if scored.get("exit_now"):
                    return PositionExitSignal("recorded_exit", "录制退出")
                return None

        config = SelectionBacktestConfig(
            holding_sessions=(1,),
            signal_start_date="2026-01-05",
            signal_end_date="2026-01-05",
            slippage_bps=0,
            price_limit_resolver=None,
            cost_model=SelectionCostModel(
                commission_rate=0,
                transfer_fee_rate=0,
                sell_stamp_duty_rate=0,
            ),
        )
        source_selector = CountingSelector()
        tape = build_selection_replay_tape(
            {"600000": rows},
            source_selector,
            config=config,
            scored_fields=("exit_now",),
            cross_section_fields=("score",),
        )
        self.assertEqual(source_selector.calls, 3)
        self.assertTrue(
            tape.frames["2026-01-07"].scored["600000"]["recorded"]
            ["exit_now"]
        )
        self.assertNotIn(
            "unused",
            tape.frames["2026-01-07"].scored["600000"]["recorded"],
        )
        self.assertEqual(
            tape.frames["2026-01-07"].cross_section["半导体"],
            {"score": 3},
        )
        self.assertNotIn(
            "unused",
            tape.frames["2026-01-07"].cross_section["半导体"],
        )

        replayed = run_selection_backtest(
            {"600000": rows},
            ReplaySelectionStrategy(tape),
            config=config,
            position_exit_strategy=RecordedExit(),
        )
        self.assertEqual(source_selector.calls, 3)
        self.assertEqual(replayed.statistics["completed_trade_count"], 1)
        self.assertEqual(replayed.trades[0]["exit_date"], "2026-01-07")
        self.assertEqual(
            replayed.trades[0]["exit_signal"],
            "recorded_exit",
        )

        filtered = run_selection_backtest(
            {"600000": rows},
            ReplaySelectionStrategy(tape, signal_filter=lambda _signal: False),
            config=config,
            position_exit_strategy=RecordedExit(),
        )
        self.assertEqual(filtered.statistics["signal_count"], 0)
        self.assertEqual(filtered.statistics["trade_count"], 0)

    def test_replay_filter_reapplies_daily_strategy_limit_after_filtering(self):
        frame = selection_module.SelectionReplayFrame(
            date="2026-01-05",
            signals=(
                SelectionSignal("600001", strategy_id="reversal", score=9.5),
                SelectionSignal("600002", strategy_id="reversal", score=9.0),
                SelectionSignal("600003", strategy_id="leader", score=8.8),
            ),
        )
        tape = selection_module.SelectionReplayTape(
            frames={"2026-01-05": frame},
        )
        replay = ReplaySelectionStrategy(
            tape,
            signal_filter=lambda signal: signal.symbol != "600001",
            max_signals_per_strategy_per_session={"reversal": 1},
        )
        context = selection_module.SelectionContext(
            date="2026-01-05",
            session_index=0,
            bars={},
            histories={},
        )

        selected = tuple(replay.on_close(context))

        self.assertEqual(
            [(item.symbol, item.strategy_id) for item in selected],
            [("600002", "reversal"), ("600003", "leader")],
        )

    def test_niuone_strategy_portfolio_risk_sizes_and_upgrades_a_position(self):
        rows = [
            daily_bar("2026-01-05", 10.0, 10.0, industry="半导体"),
            daily_bar("2026-01-06", 10.0, 10.0, industry="半导体"),
            daily_bar("2026-01-07", 10.0, 10.0, industry="半导体"),
            daily_bar("2026-01-08", 10.0, 10.0, industry="半导体"),
            daily_bar("2026-01-09", 10.0, 10.0, industry="半导体"),
        ]

        def scored(strategy_id):
            return {
                "stop_price": 9.5,
                "stop_source": "niu_structure_low",
                "atr20": 0.5,
                "gap_buffer_pct": 0.5,
                "execution_buffer_pct": 0.2,
                "industry": "半导体",
                "market_regime": "offensive",
                "market_allows_buys": True,
                "market_hard_stop": False,
                "mainline_score": 80,
                "mainline_state": (
                    "emerging" if strategy_id == "niu_emerging" else "reversal"
                ),
                "mainline_cross_day_persistent": True,
                "stock_leader_tier": True,
                "stock_strong": True,
            }

        class UpgradeSelector:
            def __init__(self, *, holding_upgrade=False):
                self.holding_upgrade = holding_upgrade

            def on_close(self, context):
                if context.date == "2026-01-05":
                    return [SelectionSignal(
                        "600000",
                        strategy_id="niu_reversal_probe",
                        score=8.5,
                        metadata={"scored": scored("niu_reversal_probe")},
                    )]
                if context.date == "2026-01-07":
                    return [SelectionSignal(
                        "600000",
                        strategy_id="niu_emerging",
                        score=8.6,
                        metadata={
                            "scored": scored("niu_emerging"),
                            **(
                                {"holding_upgrade": True}
                                if self.holding_upgrade else {}
                            ),
                        },
                    )]
                return []

            def latest_scored(self, _symbol, strategy_id):
                return scored(strategy_id)

        result = run_selection_backtest(
            {"600000": rows},
            UpgradeSelector(),
            position_exit_strategy=NiuOneStrategyBacktestPolicy(),
            config=SelectionBacktestConfig(
                holding_sessions=(1,),
                signal_start_date="2026-01-05",
                signal_end_date="2026-01-07",
                slippage_bps=0,
                price_limit_resolver=None,
                cost_model=SelectionCostModel(
                    commission_rate=0,
                    transfer_fee_rate=0,
                    sell_stamp_duty_rate=0,
                ),
            ),
        )

        self.assertEqual(result.statistics["evaluation_mode"], "strategy_portfolio")
        self.assertEqual(result.statistics["evaluated_signal_count"], 2)
        self.assertEqual(result.portfolio["initial_cash"], 1_000_000.0)
        self.assertEqual(result.portfolio["open_order_count"], 1)
        self.assertEqual(result.portfolio["add_order_count"], 1)
        self.assertGreater(result.portfolio["trading_session_count"], 0)
        self.assertIn("annualized_return_pct", result.portfolio)
        self.assertIn("sharpe_ratio", result.portfolio)
        self.assertGreater(result.portfolio["average_exposure_pct"], 0)
        self.assertGreater(result.portfolio["turnover_pct"], 0)
        trade = result.trades[0]
        self.assertEqual(trade["strategy_path"], (
            "niu_reversal_probe",
            "niu_emerging",
        ))
        self.assertEqual(trade["current_strategy_id"], "niu_emerging")
        self.assertEqual([leg["action"] for leg in trade["entry_legs"]], ["open", "add"])
        self.assertEqual(trade["entry_legs"][0]["units"], 6100)
        self.assertGreater(trade["entry_legs"][1]["units"], 0)

        preserved = run_selection_backtest(
            {"600000": rows},
            UpgradeSelector(holding_upgrade=True),
            position_exit_strategy=NiuOneStrategyBacktestPolicy(
                holding_upgrade_preserves_strategy=True,
            ),
            config=SelectionBacktestConfig(
                holding_sessions=(1,),
                signal_start_date="2026-01-05",
                signal_end_date="2026-01-07",
                slippage_bps=0,
                price_limit_resolver=None,
                cost_model=SelectionCostModel(
                    commission_rate=0,
                    transfer_fee_rate=0,
                    sell_stamp_duty_rate=0,
                ),
            ),
        )
        preserved_trade = preserved.trades[0]
        self.assertEqual(
            preserved_trade["strategy_path"],
            ("niu_reversal_probe", "niu_emerging"),
        )
        self.assertEqual(
            preserved_trade["current_strategy_id"],
            "niu_reversal_probe",
        )

    def test_niuone_strategy_portfolio_limits_new_positions_per_session(self):
        symbols = ("600000", "600001", "600002")
        rows = {
            symbol: [
                daily_bar("2026-01-05", 10.0, 10.0, industry=f"行业{index}"),
                daily_bar("2026-01-06", 10.0, 10.0, industry=f"行业{index}"),
                daily_bar("2026-01-07", 10.0, 10.0, industry=f"行业{index}"),
            ]
            for index, symbol in enumerate(symbols)
        }

        class ThreeSignalSelector:
            def on_close(self, context):
                if context.date != "2026-01-05":
                    return []
                return [SelectionSignal(
                    symbol,
                    strategy_id="niu_leader",
                    score=9.0 - index / 10,
                    metadata={"scored": {
                        "stop_price": 9.5,
                        "atr20": 0.5,
                        "gap_buffer_pct": 0.5,
                        "execution_buffer_pct": 0.2,
                        "industry": f"行业{index}",
                        "market_regime": "offensive",
                        "market_allows_buys": True,
                        "market_hard_stop": False,
                    }},
                ) for index, symbol in enumerate(symbols)]

            def latest_scored(self, _symbol, _strategy_id):
                return {
                    "mainline_score": 80,
                    "mainline_state": "mainline",
                    "stock_leader_tier": True,
                    "stock_strong": True,
                    "atr20": 0.5,
                }

        result = run_selection_backtest(
            rows,
            ThreeSignalSelector(),
            position_exit_strategy=NiuOneStrategyBacktestPolicy(),
            config=SelectionBacktestConfig(
                holding_sessions=(1,),
                signal_start_date="2026-01-05",
                signal_end_date="2026-01-05",
                slippage_bps=0,
                price_limit_resolver=None,
                cost_model=SelectionCostModel(
                    commission_rate=0,
                    transfer_fee_rate=0,
                    sell_stamp_duty_rate=0,
                ),
            ),
        )

        self.assertEqual(result.statistics["evaluated_signal_count"], 2)
        self.assertEqual(result.portfolio["open_position_count"], 2)
        self.assertEqual(
            [row["status_reason"] for row in result.signals if row["status"] == "rejected"],
            ["max_new_positions"],
        )
        self.assertEqual(
            result.statistics["entry_rejection_counts"],
            {"max_new_positions": 1},
        )

    def test_niuone_strategy_portfolio_accepts_research_new_position_limit(self):
        self.assertEqual(
            NiuOneStrategyBacktestPolicy().max_new_positions_per_session,
            2,
        )
        self.assertEqual(
            NiuOneStrategyBacktestPolicy(
                max_new_positions_per_session=1,
            ).max_new_positions_per_session,
            1,
        )
        with self.assertRaisesRegex(ValueError, "must be positive"):
            NiuOneStrategyBacktestPolicy(max_new_positions_per_session=0)

    def test_niuone_strategy_portfolio_accepts_research_slot_limits(self):
        production = NiuOneStrategyBacktestPolicy()
        self.assertEqual(production.max_open_positions, 5)
        self.assertEqual(production.max_industry_positions, 2)

        diversified = NiuOneStrategyBacktestPolicy(
            max_open_positions=7,
            max_industry_positions=1,
        )
        self.assertEqual(diversified.max_open_positions, 7)
        self.assertEqual(diversified.max_industry_positions, 1)

        with self.assertRaisesRegex(ValueError, "max_open_positions"):
            NiuOneStrategyBacktestPolicy(max_open_positions=0)
        with self.assertRaisesRegex(ValueError, "max_industry_positions"):
            NiuOneStrategyBacktestPolicy(max_industry_positions=0)

    def test_niuone_structure_gate_uses_market_open_before_slippage(self):
        scored = {
            "stop_price": 9.4,
            "atr20": 0.5,
            "gap_buffer_pct": 0.5,
            "execution_buffer_pct": 0.2,
            "industry": "半导体",
            "market_regime": "recovery",
            "market_allows_buys": True,
            "market_hard_stop": False,
        }

        class OneSignalSelector:
            def on_close(self, context):
                if context.date != "2026-01-05":
                    return []
                return [SelectionSignal(
                    "600000",
                    strategy_id="niu_leader",
                    score=9.0,
                    metadata={"scored": scored},
                )]

        def replay(next_open):
            rows = [
                daily_bar("2026-01-05", 10.0, 10.0, industry="半导体"),
                daily_bar(
                    "2026-01-06",
                    next_open,
                    10.1,
                    industry="半导体",
                ),
                daily_bar("2026-01-07", 10.1, 10.1, industry="半导体"),
            ]
            return run_selection_backtest(
                {"600000": rows},
                OneSignalSelector(),
                position_exit_strategy=NiuOneStrategyBacktestPolicy(),
                config=SelectionBacktestConfig(
                    holding_sessions=(1,),
                    signal_start_date="2026-01-05",
                    signal_end_date="2026-01-05",
                    slippage_bps=5,
                    price_limit_resolver=None,
                    cost_model=SelectionCostModel(
                        commission_rate=0,
                        transfer_fee_rate=0,
                        sell_stamp_duty_rate=0,
                    ),
                ),
            )

        accepted = replay(10.0)
        self.assertEqual(accepted.statistics["trade_count"], 1)
        self.assertEqual(accepted.signals[0]["entry_open"], 10.0)
        self.assertEqual(accepted.signals[0]["entry_price"], 10.005)
        self.assertGreater(
            accepted.signals[0]["entry_effective_loss_distance_pct"],
            6.0,
        )

        blocked = replay(10.001)
        self.assertEqual(blocked.statistics["trade_count"], 0)
        self.assertEqual(
            blocked.statistics["entry_rejection_counts"],
            {"structure_risk_block": 1},
        )

    def test_niuone_aggressive_profile_scales_account_budgets_only(self):
        balanced = NiuOneStrategyBacktestPolicy()
        aggressive = NiuOneStrategyBacktestPolicy(
            risk_budget_scale=1.35,
            position_budget_scale=1.15,
            max_new_positions_per_session=3,
            max_open_positions=6,
            max_industry_positions=3,
        )

        balanced_budget = balanced._risk_budget("rotation", "niu_leader")
        aggressive_budget = aggressive._risk_budget("rotation", "niu_leader")
        self.assertEqual(balanced_budget["per_trade_risk_pct"], 1.0)
        self.assertAlmostEqual(aggressive_budget["per_trade_risk_pct"], 1.35)
        self.assertAlmostEqual(aggressive_budget["max_open_risk_pct"], 4.05)
        self.assertAlmostEqual(aggressive_budget["max_sector_risk_pct"], 2.7)
        self.assertAlmostEqual(
            aggressive_budget["max_total_position_pct"],
            63.25,
        )
        self.assertAlmostEqual(
            aggressive_budget["max_sector_position_pct"],
            46.0,
        )
        self.assertEqual(aggressive.max_new_positions_per_session, 3)
        self.assertEqual(aggressive.max_open_positions, 6)
        self.assertEqual(aggressive.max_industry_positions, 3)
        defensive_budget = balanced._risk_budget(
            "defensive",
            "niu_leader",
        )
        self.assertEqual(defensive_budget["per_trade_risk_pct"], 0.30)
        self.assertEqual(defensive_budget["max_open_risk_pct"], 0.90)
        self.assertEqual(defensive_budget["max_total_position_pct"], 20.0)

        with self.assertRaisesRegex(ValueError, "risk_budget_scale"):
            NiuOneStrategyBacktestPolicy(risk_budget_scale=2.1)
        with self.assertRaisesRegex(ValueError, "position_budget_scale"):
            NiuOneStrategyBacktestPolicy(position_budget_scale=0)

    def test_niuone_backtest_allows_defensive_entry_but_not_hard_stop(self):
        scored = {
            "recent_close": 10.0,
            "stop_price": 9.5,
            "stop_source": "niu_structure_low",
            "atr20": 0.5,
            "gap_buffer_pct": 0.5,
            "execution_buffer_pct": 0.2,
            "industry": "半导体",
            "market_regime": "defensive",
            "market_allows_buys": True,
            "market_hard_stop": False,
        }
        bar = HistoricalBar.from_value(
            "600000",
            daily_bar(
                "2026-01-06",
                10.0,
                10.0,
                industry="半导体",
            ),
        )
        policy = NiuOneStrategyBacktestPolicy()

        allowed = policy.size_entry(
            SelectionSignal(
                "600000",
                strategy_id="niu_leader",
                score=9.0,
                metadata={"scored": scored},
            ),
            bar,
            10.0,
            None,
            {},
            {},
            100_000.0,
            100_000.0,
            0,
            SelectionCostModel(),
        )
        blocked = policy.size_entry(
            SelectionSignal(
                "600000",
                strategy_id="niu_leader",
                score=9.0,
                metadata={
                    "scored": {**scored, "market_hard_stop": True},
                },
            ),
            bar,
            10.0,
            None,
            {},
            {},
            100_000.0,
            100_000.0,
            0,
            SelectionCostModel(),
        )

        self.assertEqual(allowed.action, "open")
        self.assertGreater(allowed.units, 0)
        self.assertEqual(blocked.action, "reject")
        self.assertEqual(blocked.reason, "market_risk_block")

    def test_holding_upgrade_signal_cannot_reopen_an_exited_position(self):
        signal = SelectionSignal(
            "600000",
            strategy_id="niu_emerging",
            score=8.4,
            metadata={"holding_upgrade": True, "scored": {}},
        )

        reason = NiuOneStrategyBacktestPolicy().schedule_block_reason(
            None,
            signal,
            "2026-01-06",
        )

        self.assertEqual(reason, "holding_upgrade_missing_position")

    def test_holding_upgrade_can_preserve_the_existing_exit_strategy(self):
        signal = SelectionSignal(
            "600000",
            strategy_id="niu_emerging",
            score=8.4,
            metadata={"holding_upgrade": True, "scored": {}},
        )
        policy = NiuOneStrategyBacktestPolicy(
            holding_upgrade_preserves_strategy=True,
        )

        resulting = policy.strategy_id_after_add(
            {"strategy_id": "niu_reversal_probe"},
            signal,
        )

        self.assertEqual(resulting, "niu_reversal_probe")
        self.assertEqual(
            NiuOneStrategyBacktestPolicy().strategy_id_after_add(
                {"strategy_id": "niu_reversal_probe"},
                signal,
            ),
            "niu_emerging",
        )

    def test_holding_upgrade_position_cap_is_research_configurable(self):
        self.assertEqual(
            NiuOneStrategyBacktestPolicy(
                holding_upgrade_position_cap_pct=10,
            ).holding_upgrade_position_cap_pct,
            10.0,
        )
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            NiuOneStrategyBacktestPolicy(
                holding_upgrade_position_cap_pct=0,
            )

        self.assertEqual(
            NiuOneStrategyBacktestPolicy(
                holding_upgrade_early_position_cap_pct=10,
            ).holding_upgrade_early_position_cap_pct,
            10.0,
        )
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            NiuOneStrategyBacktestPolicy(
                holding_upgrade_early_position_cap_pct=0,
            )

    def test_production_markup_upgrade_requires_profit_and_markup_stage(self):
        policy = NiuOneStrategyBacktestPolicy(markup_upgrade_only=True)
        scored = {
            "mainline_confirmed": True,
            "mainline_state": "mainline",
            "niuone_lifecycle_stage": "markup",
            "stock_leader_tier": True,
            "stock_strong": True,
        }
        signal = SelectionSignal(
            "600000",
            strategy_id="niu_leader",
            score=9.0,
            metadata={"scored": scored},
        )
        position = {
            "strategy_id": "niu_reversal_probe",
            "avg_cost": 10.0,
            "last_price": 10.2,
            "lots": [{"date": "2026-01-05"}],
        }

        self.assertEqual(
            policy.schedule_block_reason(position, signal, "2026-01-06"),
            "",
        )
        position["last_price"] = 10.1
        self.assertEqual(
            policy.schedule_block_reason(position, signal, "2026-01-06"),
            "markup_upgrade_rule",
        )
        position["last_price"] = 10.2
        scored["niuone_lifecycle_stage"] = "divergence"
        self.assertEqual(
            policy.schedule_block_reason(position, signal, "2026-01-06"),
            "markup_upgrade_rule",
        )

        early_scored = {
            **scored,
            "mainline_confirmed": False,
            "mainline_state": "emerging",
            "mainline_cross_day_persistent": True,
            "niuone_lifecycle_stage": "markup",
        }
        early_signal = SelectionSignal(
            "600000",
            strategy_id="niu_emerging",
            score=9.0,
            metadata={"scored": early_scored},
        )
        self.assertEqual(
            policy.schedule_block_reason(
                position,
                early_signal,
                "2026-01-06",
            ),
            "",
        )
        position["niuone_markup_early_scale_in_done"] = True
        self.assertEqual(
            policy.schedule_block_reason(
                position,
                early_signal,
                "2026-01-06",
            ),
            "markup_upgrade_early_done",
        )
        position.pop("niuone_markup_early_scale_in_done")
        position["last_price"] = 11.21
        self.assertEqual(
            policy.schedule_block_reason(
                position,
                early_signal,
                "2026-01-06",
            ),
            "markup_upgrade_rule",
        )

    def test_markup_rebalance_reentry_requires_a_filled_trim_not_a_fixed_count(self):
        policy = NiuOneStrategyBacktestPolicy(
            markup_upgrade_only=True,
            markup_rebalance_enabled=True,
        )
        scored = {
            "mainline_confirmed": True,
            "mainline_state": "mainline",
            "niuone_lifecycle_stage": "markup",
            "stock_leader_tier": True,
            "stock_strong": True,
        }
        signal = SelectionSignal(
            "600000",
            strategy_id="niu_leader",
            score=9.0,
            metadata={
                "holding_upgrade": True,
                "niuone_markup_rebalance_reentry": True,
                "scored": scored,
            },
        )
        position = {
            "strategy_id": "niu_leader",
            "avg_cost": 10.0,
            "last_price": 12.0,
            "atr20": 0.5,
            "lots": [{"date": "2026-01-05"}],
        }

        self.assertEqual(
            policy.schedule_block_reason(position, signal, "2026-01-06"),
            "markup_rebalance_rule",
        )
        position.update({
            "niuone_markup_rebalance_armed": True,
            "niuone_markup_rebalance_reentry_price": 11.5,
        })
        self.assertEqual(
            policy.schedule_block_reason(position, signal, "2026-01-06"),
            "",
        )

        entry_bar = HistoricalBar.from_value("600000", daily_bar(
            "2026-01-07",
            12.0,
            industry="半导体",
        ))
        for expected_count in (1, 2):
            policy.on_exit_filled(
                position,
                PositionExitSignal(
                    "niu_markup_rebalance_partial",
                    "波段减仓",
                    sell_ratio=1.0 / 3.0,
                ),
                {"price": 11.8},
                type("Context", (), {"date": "2026-01-06"})(),
            )
            self.assertIs(position["niuone_markup_rebalance_armed"], True)
            position.update(policy.on_add(
                position,
                signal,
                entry_bar,
                12.0,
            ))
            self.assertIs(position["niuone_markup_rebalance_armed"], False)
            self.assertEqual(
                position["niuone_markup_rebalance_reentry_count"],
                expected_count,
            )

    def test_entry_order_scale_replays_model_sizing_below_the_risk_ceiling(self):
        rows = [
            daily_bar("2026-01-05", 10.0, 10.0, industry="半导体"),
            daily_bar("2026-01-06", 10.0, 10.0, industry="半导体"),
            daily_bar("2026-01-07", 10.0, 10.0, industry="半导体"),
        ]
        scored = {
            "stop_price": 9.5,
            "atr20": 0.5,
            "gap_buffer_pct": 0.5,
            "execution_buffer_pct": 0.2,
            "industry": "半导体",
            "market_regime": "offensive",
            "market_allows_buys": True,
            "market_hard_stop": False,
        }

        class OneSignalSelector:
            def on_close(self, context):
                if context.date == "2026-01-05":
                    return [SelectionSignal(
                        "600000",
                        strategy_id="niu_reversal_probe",
                        score=9.0,
                        metadata={"scored": scored},
                    )]
                return []

        def replay(scale):
            return run_selection_backtest(
                {"600000": rows},
                OneSignalSelector(),
                position_exit_strategy=NiuOneStrategyBacktestPolicy(
                    entry_order_scale=scale,
                ),
                config=SelectionBacktestConfig(
                    holding_sessions=(1,),
                    signal_start_date="2026-01-05",
                    signal_end_date="2026-01-05",
                    slippage_bps=0,
                    price_limit_resolver=None,
                    cost_model=SelectionCostModel(
                        commission_rate=0,
                        transfer_fee_rate=0,
                        sell_stamp_duty_rate=0,
                    ),
                ),
            )

        full = replay(1.0)
        half = replay(0.5)
        full_units = full.trades[0]["entry_units"]
        half_units = half.trades[0]["entry_units"]

        self.assertGreater(full_units, half_units)
        self.assertEqual(half_units, int(full_units * 0.5 / 100) * 100)
        self.assertEqual(full.signals[0]["entry_total_equity"], 1_000_000.0)
        self.assertGreater(
            full.signals[0]["entry_target_position_pct"],
            0,
        )
        self.assertGreater(
            full.signals[0]["entry_effective_loss_distance_pct"],
            0,
        )
        self.assertEqual(
            full.signals[0]["entry_position_before_trade_pct"],
            0,
        )
        self.assertGreater(
            full.signals[0]["entry_order_position_pct"],
            0,
        )
        self.assertEqual(
            full.signals[0]["entry_order_position_pct"],
            full.signals[0]["entry_position_after_trade_pct"],
        )
        with self.assertRaisesRegex(ValueError, r"within \(0, 1\]"):
            NiuOneStrategyBacktestPolicy(entry_order_scale=0)

    def test_reversal_execution_gap_cap_is_research_configurable(self):
        rows = [
            daily_bar("2026-01-05", 10.0, 10.0, industry="半导体"),
            daily_bar("2026-01-06", 10.1, 10.2, industry="半导体"),
            daily_bar("2026-01-07", 10.2, 10.2, industry="半导体"),
        ]
        scored = {
            "score": 8.0,
            "recent_close": 10.0,
            "stop_price": 9.5,
            "atr20": 0.5,
            "gap_buffer_pct": 0.5,
            "execution_buffer_pct": 0.2,
            "industry": "半导体",
            "market_regime": "offensive",
            "market_allows_buys": True,
            "market_hard_stop": False,
        }

        class OneSignalSelector:
            def on_close(self, context):
                if context.date == "2026-01-05":
                    return [SelectionSignal(
                        "600000",
                        strategy_id="niu_reversal_probe",
                        score=9.0,
                        metadata={"scored": scored},
                    )]
                return []

        def replay(maximum_gap_pct):
            return run_selection_backtest(
                {"600000": rows},
                OneSignalSelector(),
                position_exit_strategy=NiuOneStrategyBacktestPolicy(
                    reversal_max_execution_gap_pct=maximum_gap_pct,
                ),
                config=SelectionBacktestConfig(
                    holding_sessions=(1,),
                    signal_start_date="2026-01-05",
                    signal_end_date="2026-01-05",
                    slippage_bps=0,
                    price_limit_resolver=None,
                    cost_model=SelectionCostModel(
                        commission_rate=0,
                        transfer_fee_rate=0,
                        sell_stamp_duty_rate=0,
                    ),
                ),
            )

        blocked = replay(0.0)
        accepted = replay(1.0)

        self.assertEqual(
            blocked.statistics["entry_rejection_counts"],
            {"reversal_execution_gap": 1},
        )
        self.assertEqual(blocked.statistics["trade_count"], 0)
        self.assertEqual(accepted.statistics["trade_count"], 1)
        with self.assertRaisesRegex(ValueError, "between -10 and 10"):
            NiuOneStrategyBacktestPolicy(
                reversal_max_execution_gap_pct=float("nan"),
            )

    def test_markup_momentum_probe_replays_wide_stop_with_four_percent_cap(self):
        scored = {
            "score": 8.0,
            "recent_close": 10.0,
            "stop_price": 8.4,
            "atr20": 0.6,
            "gap_buffer_pct": 0.5,
            "execution_buffer_pct": 0.2,
            "industry": "半导体",
            "market_regime": "recovery",
            "market_allows_buys": True,
            "market_hard_stop": False,
            "mainline_state": "emerging",
            "niuone_lifecycle_stage": "markup",
            "mainline_cross_day_persistent": True,
            "stock_leader_tier": True,
            "stock_strong": True,
            "stock_leader_rank": 1,
            "stock_strong_score": 92.0,
            "entry_extension_atr": 3.0,
            "change_pct": 10.0,
            "volume_ratio": 1.0,
            "niuone_entry_subroute": "markup_momentum_probe",
        }

        class OneSignalSelector:
            def on_close(self, context):
                if context.date == "2026-01-05":
                    return [SelectionSignal(
                        "600000",
                        strategy_id="niu_emerging",
                        score=8.0,
                        metadata={"scored": scored},
                    )]
                return []

        def replay(next_open):
            rows = [
                daily_bar("2026-01-05", 10.0, 10.0, industry="半导体"),
                daily_bar("2026-01-06", next_open, 10.2, industry="半导体"),
                daily_bar("2026-01-07", 10.3, 10.3, industry="半导体"),
            ]
            return run_selection_backtest(
                {"600000": rows},
                OneSignalSelector(),
                position_exit_strategy=NiuOneStrategyBacktestPolicy(),
                config=SelectionBacktestConfig(
                    holding_sessions=(1,),
                    signal_start_date="2026-01-05",
                    signal_end_date="2026-01-05",
                    slippage_bps=0,
                    price_limit_resolver=None,
                    cost_model=SelectionCostModel(
                        commission_rate=0,
                        transfer_fee_rate=0,
                        sell_stamp_duty_rate=0,
                    ),
                ),
            )

        accepted = replay(10.2)
        self.assertEqual(accepted.statistics["trade_count"], 1)
        self.assertLessEqual(
            accepted.signals[0]["entry_target_position_pct"],
            4.0,
        )
        self.assertEqual(
            accepted.trades[0]["niuone_entry_subroute"],
            "markup_momentum_probe",
        )

        blocked = replay(10.31)
        self.assertEqual(blocked.statistics["trade_count"], 0)
        self.assertEqual(
            blocked.statistics["entry_rejection_counts"],
            {"markup_momentum_execution_gap": 1},
        )

    def test_niuone_strategy_portfolio_enforces_t1_for_an_upgrade_lot(self):
        rows = [
            daily_bar("2026-01-05", 10.0, 10.0, industry="半导体"),
            daily_bar("2026-01-06", 10.0, 10.0, industry="半导体"),
            daily_bar("2026-01-07", 10.0, 10.0, industry="半导体"),
            daily_bar("2026-01-08", 10.0, 10.0, industry="半导体"),
            daily_bar("2026-01-09", 10.0, 10.0, industry="半导体"),
        ]

        def signal(strategy_id):
            return SelectionSignal(
                "600000",
                strategy_id=strategy_id,
                score=9.0,
                metadata={"scored": {
                    "stop_price": 9.5,
                    "atr20": 0.5,
                    "gap_buffer_pct": 0.5,
                    "execution_buffer_pct": 0.2,
                    "industry": "半导体",
                    "market_regime": "offensive",
                    "market_allows_buys": True,
                    "market_hard_stop": False,
                    "mainline_state": "emerging",
                    "mainline_cross_day_persistent": True,
                }},
            )

        class UpgradeSelector:
            def on_close(self, context):
                if context.date == "2026-01-05":
                    return [signal("niu_reversal_probe")]
                if context.date == "2026-01-07":
                    return [signal("niu_emerging")]
                return []

        class ExitAfterUpgrade(NiuOneStrategyBacktestPolicy):
            def on_close(self, _position, context, _selector):
                if context.date >= "2026-01-08":
                    return PositionExitSignal("test_full_exit", "测试全量卖出")
                return None

        result = run_selection_backtest(
            {"600000": rows},
            UpgradeSelector(),
            position_exit_strategy=ExitAfterUpgrade(),
            config=SelectionBacktestConfig(
                holding_sessions=(1,),
                signal_start_date="2026-01-05",
                signal_end_date="2026-01-07",
                slippage_bps=0,
                price_limit_resolver=None,
                cost_model=SelectionCostModel(
                    commission_rate=0,
                    transfer_fee_rate=0,
                    sell_stamp_duty_rate=0,
                ),
            ),
        )

        trade = result.trades[0]
        self.assertEqual(trade["status"], "completed")
        self.assertEqual(
            [leg["date"] for leg in trade["exit_legs"]],
            ["2026-01-08", "2026-01-09"],
        )
        self.assertEqual(
            trade["exit_legs"][0]["units"],
            trade["entry_legs"][0]["units"],
        )
        self.assertEqual(
            trade["exit_legs"][1]["units"],
            trade["entry_legs"][1]["units"],
        )
        equity_curve = result.portfolio["equity_curve"]
        self.assertEqual(equity_curve[-1]["date"], trade["exit_date"])
        self.assertEqual(
            equity_curve[-1]["equity"],
            result.portfolio["final_equity"],
        )
        self.assertEqual(equity_curve[-1]["market_value"], 0.0)
        self.assertEqual(equity_curve[-1]["position_count"], 0)
        self.assertEqual(
            result.portfolio["trading_session_count"],
            len(equity_curve),
        )

    def test_trade_lifecycle_allows_reentry_after_a_completed_exit(self):
        rows = [
            daily_bar("2026-01-05", 10.0, 10.0),
            daily_bar("2026-01-06", 10.0, 10.0, name="浦发银行"),
            daily_bar("2026-01-07", 10.5, 11.0),
            daily_bar("2026-01-08", 10.0, 10.0),
            daily_bar("2026-01-09", 10.0, 10.0),
            daily_bar("2026-01-12", 9.5, 9.0),
        ]

        class RepeatSelector:
            def on_close(self, context):
                if context.date in {"2026-01-05", "2026-01-08"}:
                    return [SelectionSignal(
                        "600000", strategy_id="repeat", score=9.0,
                    )]
                return []

        class NextDayExit:
            def on_entry(self, _signal, _entry_bar, _entry_price):
                return {}

            def on_close(self, position, context, _selector):
                if context.session_index - position["entry_session_index"] >= 1:
                    return PositionExitSignal("test_exit", "测试卖出")
                return None

        result = run_selection_backtest(
            {"600000": rows},
            RepeatSelector(),
            position_exit_strategy=NextDayExit(),
            config=SelectionBacktestConfig(
                holding_sessions=(1,),
                signal_start_date="2026-01-05",
                signal_end_date="2026-01-08",
                slippage_bps=0,
                price_limit_resolver=None,
                cost_model=SelectionCostModel(
                    commission_rate=0,
                    transfer_fee_rate=0,
                    sell_stamp_duty_rate=0,
                ),
            ),
        )

        self.assertEqual(result.statistics["evaluation_mode"], "trade_lifecycle")
        self.assertEqual(result.statistics["signal_count"], 2)
        self.assertEqual(result.statistics["completed_trade_count"], 2)
        self.assertEqual(result.statistics["duplicate_signal_count"], 0)
        self.assertEqual(
            [trade["entry_date"] for trade in result.trades],
            ["2026-01-06", "2026-01-09"],
        )
        self.assertEqual(
            [trade["exit_date"] for trade in result.trades],
            ["2026-01-07", "2026-01-12"],
        )
        self.assertEqual(
            [trade["net_return_pct"] for trade in result.trades],
            [10.0, -10.0],
        )
        self.assertEqual(result.statistics["win_rate_pct"], 50.0)

    def test_niuone_daily_exit_replays_partial_2r_and_atr_trailing_sale(self):
        rows = [
            daily_bar("2026-01-05", 10.0, 10.0),
            daily_bar("2026-01-06", 10.0, 10.0, name="浦发银行"),
            daily_bar("2026-01-07", 11.8, 12.2),
            daily_bar("2026-01-08", 11.2, 11.0),
        ]

        class NiuSelector:
            def on_close(self, context):
                if context.date == "2026-01-05":
                    return [SelectionSignal(
                        "600000",
                        strategy_id="niu_leader",
                        score=9.0,
                        metadata={"scored": {
                            "stop_price": 9.0,
                            "stop_source": "niu_structure_low",
                            "atr20": 0.6,
                            "industry": "银行",
                        }},
                    )]
                return []

            def latest_scored(self, _symbol, _strategy_id):
                return {
                    "mainline_score": 80,
                    "mainline_state": "mainline",
                    "stock_leader_tier": True,
                    "stock_strong": True,
                    "atr20": 0.6,
                }

        result = run_selection_backtest(
            {"600000": rows},
            NiuSelector(),
            position_exit_strategy=NiuOneDailyExitStrategy(
                partial_take_profit_r=2.0,
                partial_take_profit_ratio=0.5,
                intraday_profit_target=False,
                break_even_after_partial=False,
                reversal_mainline_weak_confirmations=None,
            ),
            config=SelectionBacktestConfig(
                holding_sessions=(1,),
                signal_start_date="2026-01-05",
                signal_end_date="2026-01-05",
                slippage_bps=0,
                price_limit_resolver=None,
                cost_model=SelectionCostModel(
                    commission_rate=0,
                    transfer_fee_rate=0,
                    sell_stamp_duty_rate=0,
                ),
            ),
        )

        self.assertEqual(result.statistics["completed_trade_count"], 1)
        trade = result.trades[0]
        self.assertEqual(trade["exit_date"], "2026-01-08")
        self.assertEqual(trade["holding_sessions"], 2)
        self.assertEqual(trade["net_return_pct"], 16.0)
        self.assertEqual(
            [leg["signal"] for leg in trade["exit_legs"]],
            ["niu_2r_partial", "niu_atr_trail"],
        )

    def test_daily_v_no_progress_confirmation_exemption_is_research_only(self):
        bar = HistoricalBar.from_value(
            "600000",
            daily_bar("2026-01-08", 10.0, 10.1, high=10.1, low=9.8),
        )
        context = selection_module.SelectionContext(
            date=bar.date,
            session_index=3,
            bars={bar.symbol: bar},
            histories={bar.symbol: (bar,)},
        )
        position = {
            "symbol": bar.symbol,
            "strategy_id": "niu_reversal_probe",
            "reversal_basis": "daily_v",
            "entry_date": "2026-01-05",
            "entry_session_index": 0,
            "entry_price": 10.0,
            "entry_stop_price": 9.5,
            "highest_price": 10.1,
            "max_pnl_pct": 1.0,
        }

        class ConfirmedSelector:
            @staticmethod
            def latest_scored(_symbol, _strategy_id):
                return {
                    "mainline_score": 70,
                    "mainline_state": "emerging",
                    "mainline_cross_day_persistent": True,
                    "mainline_confirmed": False,
                    "atr20": 0.5,
                }

        production = NiuOneDailyExitStrategy().on_close(
            dict(position), context, ConfirmedSelector(),
        )
        research_candidate = NiuOneDailyExitStrategy(
            daily_v_no_progress_requires_unconfirmed=True,
        ).on_close(dict(position), context, ConfirmedSelector())

        self.assertEqual(production.signal, "niu_reversal_no_progress")
        self.assertIsNone(research_candidate)

    def test_niuone_intraday_r_partial_moves_remaining_stop_to_breakeven(self):
        rows = [
            daily_bar("2026-01-05", 10.0, 10.0),
            daily_bar("2026-01-06", 10.0, 10.0),
            daily_bar("2026-01-07", 10.0, 10.1, high=10.8, low=9.8),
            daily_bar("2026-01-08", 10.0, 9.95, high=10.1, low=9.9),
        ]

        class NiuSelector:
            def on_close(self, context):
                if context.date != "2026-01-05":
                    return []
                return [SelectionSignal(
                    "600000",
                    strategy_id="niu_reversal_probe",
                    score=9.0,
                    metadata={"scored": {
                        "stop_price": 9.0,
                        "stop_source": "niu_reversal_right_low",
                        "atr20": 0.5,
                        "industry": "银行",
                    }},
                )]

        result = run_selection_backtest(
            {"600000": rows},
            NiuSelector(),
            position_exit_strategy=NiuOneDailyExitStrategy(
                partial_take_profit_r=0.75,
                partial_take_profit_ratio=0.5,
                intraday_profit_target=True,
                break_even_after_partial=True,
            ),
            config=SelectionBacktestConfig(
                holding_sessions=(1,),
                signal_start_date="2026-01-05",
                signal_end_date="2026-01-05",
                slippage_bps=0,
                price_limit_resolver=None,
                cost_model=SelectionCostModel(
                    commission_rate=0,
                    transfer_fee_rate=0,
                    sell_stamp_duty_rate=0,
                ),
            ),
        )

        trade = result.trades[0]
        self.assertEqual(
            [leg["signal"] for leg in trade["exit_legs"]],
            ["niu_r_partial", "niu_structure_stop"],
        )
        self.assertEqual(
            [leg["price"] for leg in trade["exit_legs"]],
            [10.75, 10.0],
        )
        self.assertEqual(trade["net_return_pct"], 3.75)

    def test_niuone_reversal_uses_entry_regime_for_early_profit_protection(self):
        policy = NiuOneStrategyBacktestPolicy()
        early_bar = HistoricalBar.from_value(
            "600000",
            daily_bar(
                "2026-01-07", 10.0, 10.2, high=10.75, low=9.8,
            ),
        )
        one_r_bar = HistoricalBar.from_value(
            "600000",
            daily_bar(
                "2026-01-07", 10.0, 10.2, high=11.0, low=9.8,
            ),
        )
        reversal = {
            "strategy_id": "niu_reversal_probe",
            "entry_market_regime": "offensive",
        }

        early = policy._partial_take_profit(reversal, early_bar, 10.0, 9.0)

        self.assertIsNotNone(early)
        self.assertEqual(early.sell_ratio, 0.5)
        self.assertEqual(early.fill_reference_price, 10.75)
        self.assertIn("0.75R", early.reason)

        reversal["entry_market_regime"] = "rotation"
        reversal["market_regime"] = "offensive"
        self.assertIsNone(
            policy._partial_take_profit(reversal, early_bar, 10.0, 9.0)
        )
        normal = policy._partial_take_profit(reversal, one_r_bar, 10.0, 9.0)
        self.assertIsNotNone(normal)
        self.assertEqual(normal.sell_ratio, 0.45)
        self.assertIn("1R", normal.reason)

        mature = {
            "strategy_id": "niu_leader",
            "entry_market_regime": "offensive",
        }
        self.assertIsNone(
            policy._partial_take_profit(mature, early_bar, 10.0, 9.0)
        )

    def test_niuone_entry_state_freezes_market_regime(self):
        policy = NiuOneStrategyBacktestPolicy()
        signal = SelectionSignal(
            "600000",
            strategy_id="niu_reversal_probe",
            score=8.0,
            metadata={"scored": {
                "stop_price": 9.0,
                "market_regime": "recovery",
            }},
        )
        bar = HistoricalBar.from_value(
            "600000", daily_bar("2026-01-06", 10.0, 10.0),
        )

        state = dict(policy.on_entry(signal, bar, 10.0))
        updated = dict(policy.on_add(
            {**state, "entry_market_regime": "recovery"},
            SelectionSignal(
                "600000",
                strategy_id="niu_emerging",
                score=9.0,
                metadata={"scored": {"market_regime": "rotation"}},
            ),
            bar,
            10.1,
        ))

        self.assertEqual(state["entry_market_regime"], "recovery")
        self.assertNotIn("entry_market_regime", updated)

    def test_niuone_reversal_can_exit_when_theme_recovery_fails(self):
        policy = NiuOneDailyExitStrategy(
            reversal_mainline_weak_confirmations=1,
        )
        bar = HistoricalBar.from_value(
            "600000",
            daily_bar("2026-01-07", 10.1, 10.0, high=10.2, low=9.8),
        )
        context = selection_module.SelectionContext(
            date=bar.date,
            session_index=2,
            bars={bar.symbol: bar},
            histories={bar.symbol: (bar,)},
        )

        class ThemeSelector:
            def latest_scored(self, _symbol, _strategy_id):
                return {
                    "mainline_score": 50,
                    "mainline_state": "fading",
                }

        decision = policy.on_close(
            {
                "symbol": bar.symbol,
                "strategy_id": "niu_reversal_probe",
                "avg_cost": 10.0,
                "entry_session_index": 1,
                "entry_stop_price": 9.0,
                "entry_stop_source": "niu_reversal_right_low",
                "mainline_weak_count": 0,
            },
            context,
            ThemeSelector(),
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.signal, "niu_reversal_theme_failed")

    def test_niuone_lifecycle_candidate_trims_climax_once_and_exits_fade(self):
        policy = NiuOneDailyExitStrategy(
            lifecycle_climax_partial_ratio=1.0 / 3.0,
            lifecycle_climax_min_pnl_pct=0.0,
            lifecycle_fade_exit=True,
        )
        position = {
            "symbol": "600000",
            "strategy_id": "niu_reversal_probe",
            "avg_cost": 10.0,
            "entry_session_index": 0,
            "entry_stop_price": 9.0,
            "mainline_state": "mainline",
            "mainline_confirmed": True,
            "niuone_lifecycle_stage": "markup",
            "trade": {"exit_legs": []},
        }

        def context(trading_date, session_index, close):
            bar = HistoricalBar.from_value(
                "600000",
                daily_bar(
                    trading_date,
                    close,
                    close,
                    high=close + 0.1,
                    low=close - 0.1,
                ),
            )
            return selection_module.SelectionContext(
                date=bar.date,
                session_index=session_index,
                bars={bar.symbol: bar},
                histories={bar.symbol: (bar,)},
            )

        class ClimaxSelector:
            @staticmethod
            def latest_scored(_symbol, _strategy_id):
                return {
                    "mainline_score": 78.5,
                    "mainline_state": "mainline",
                    "mainline_confirmed": True,
                    "atr20": 0.5,
                }

        climax = policy.on_close(
            position,
            context("2026-01-07", 1, 10.5),
            ClimaxSelector(),
        )
        self.assertIsNotNone(climax)
        self.assertEqual(climax.signal, "niu_lifecycle_climax_partial")
        self.assertAlmostEqual(climax.sell_ratio, 1.0 / 3.0)
        self.assertEqual(position["niuone_lifecycle_stage"], "climax")

        position["trade"]["exit_legs"].append({
            "signal": "niu_lifecycle_climax_partial",
        })
        self.assertIsNone(policy.on_close(
            position,
            context("2026-01-07", 1, 10.6),
            ClimaxSelector(),
        ))

        class FadeSelector:
            @staticmethod
            def latest_scored(_symbol, _strategy_id):
                return {
                    "mainline_score": 52.0,
                    "mainline_state": "fading",
                    "mainline_confirmed": False,
                    "atr20": 0.5,
                }

        fade = policy.on_close(
            position,
            context("2026-01-08", 2, 10.3),
            FadeSelector(),
        )
        self.assertIsNotNone(fade)
        self.assertEqual(fade.signal, "niu_lifecycle_fade_exit")
        self.assertEqual(position["niuone_lifecycle_stage"], "fade")

    def test_niuone_climax_runner_waits_three_rank_losses_with_three_atr_trail(self):
        policy = NiuOneDailyExitStrategy()
        position = {
            "symbol": "600000",
            "strategy_id": "niu_leader",
            "avg_cost": 10.0,
            "entry_session_index": 0,
            "entry_date": "2026-01-05",
            "entry_stop_price": 9.0,
            "entry_stop_source": "niu_structure_low",
            "mainline_state": "diverging",
            "mainline_score": 70.0,
            "mainline_weak_count": 0,
            "niu_leader_lost_count": 1,
            "niu_leader_lost_last_session": 1,
            "niuone_lifecycle_stage": "divergence",
            "niuone_lifecycle_climax_partial_done": True,
            "partial_tp_done": True,
            "highest_price": 12.0,
            "trade": {"exit_legs": [{
                "signal": "niu_lifecycle_climax_partial",
            }]},
        }

        class HealthyRelativeLagSelector:
            @staticmethod
            def latest_scored(_symbol, _strategy_id):
                return {
                    "mainline_score": 70.0,
                    "mainline_state": "diverging",
                    "stock_leader_rank": 12,
                    "stock_leader_tier": False,
                    "stock_strong": True,
                    "niuone_lifecycle_stage": "divergence",
                    "atr20": 0.5,
                }

        def context(trading_date: str, session_index: int):
            bar = HistoricalBar.from_value(
                "600000",
                daily_bar(
                    trading_date,
                    10.6,
                    10.6,
                    high=10.7,
                    low=10.4,
                ),
            )
            return selection_module.SelectionContext(
                date=bar.date,
                session_index=session_index,
                bars={bar.symbol: bar},
                histories={bar.symbol: (bar,)},
            )

        self.assertIsNone(policy.on_close(
            position,
            context("2026-01-07", 2),
            HealthyRelativeLagSelector(),
        ))
        self.assertEqual(position["niu_leader_lost_count"], 2)
        self.assertEqual(position["niu_trailing_stop"], 10.5)

        exit_signal = policy.on_close(
            position,
            context("2026-01-08", 3),
            HealthyRelativeLagSelector(),
        )

        self.assertIsNotNone(exit_signal)
        self.assertEqual(exit_signal.signal, "niu_leader_lost")
        self.assertIn("连续3个交易日", exit_signal.reason)
        self.assertIn("高潮减仓后余仓", exit_signal.reason)

    def test_niuone_exit_policy_prefers_canonical_scorer_lifecycle(self):
        policy = NiuOneDailyExitStrategy()
        position = {
            "symbol": "600000",
            "strategy_id": "niu_emerging",
            "avg_cost": 10.0,
            "entry_session_index": 0,
            "entry_stop_price": 9.0,
            "mainline_state": "candidate",
            "niuone_lifecycle_stage": "brewing",
        }
        bar = HistoricalBar.from_value(
            "600000",
            daily_bar(
                "2026-01-07",
                10.1,
                10.1,
                high=10.2,
                low=10.0,
            ),
        )
        context = selection_module.SelectionContext(
            date=bar.date,
            session_index=1,
            bars={bar.symbol: bar},
            histories={bar.symbol: (bar,)},
        )

        class DivergenceSelector:
            @staticmethod
            def latest_scored(_symbol, _strategy_id):
                return {
                    "mainline_score": 64.0,
                    "mainline_state": "emerging",
                    "mainline_cross_day_persistent": False,
                    "mainline_confirmed": False,
                    "niuone_lifecycle_stage": "divergence",
                    "atr20": 0.5,
                }

        self.assertIsNone(policy.on_close(
            position,
            context,
            DivergenceSelector(),
        ))
        self.assertEqual(position["niuone_lifecycle_stage"], "divergence")

    def test_niuone_reversal_strong_leader_can_promote_only_exit_identity(self):
        policy = NiuOneDailyExitStrategy(
            reversal_strong_leader_exit_promotion=True,
        )
        position = {
            "symbol": "600000",
            "strategy_id": "niu_reversal_probe",
            "reversal_basis": "daily_v",
            "avg_cost": 10.0,
            "entry_session_index": 0,
            "entry_stop_price": 9.0,
            "entry_stop_source": "niu_reversal_right_low",
            "mainline_weak_count": 0,
            "niu_leader_lost_count": 0,
            "highest_price": 10.1,
            "max_pnl_pct": 1.0,
        }
        leader_bar = HistoricalBar.from_value(
            "600000",
            daily_bar("2026-01-08", 10.0, 10.0, high=10.1, low=9.8),
        )
        leader_context = selection_module.SelectionContext(
            date=leader_bar.date,
            session_index=3,
            bars={leader_bar.symbol: leader_bar},
            histories={leader_bar.symbol: (leader_bar,)},
        )

        class StrongLeaderSelector:
            @staticmethod
            def latest_scored(_symbol, _strategy_id):
                return {
                    "mainline_score": 72.0,
                    "mainline_state": "emerging",
                    "stock_leader_rank": 1,
                    "stock_leader_tier": True,
                    "stock_strong": True,
                    "atr20": 0.5,
                }

        self.assertIsNone(
            policy.on_close(position, leader_context, StrongLeaderSelector())
        )
        self.assertTrue(position["reversal_strong_leader_exit_promoted"])
        self.assertEqual(
            position["reversal_strong_leader_exit_promotion_date"],
            "2026-01-08",
        )
        self.assertEqual(position["strategy_id"], "niu_reversal_probe")

        fading_bar = HistoricalBar.from_value(
            "600000",
            daily_bar("2026-01-09", 10.0, 10.0, high=10.1, low=9.8),
        )
        fading_context = selection_module.SelectionContext(
            date=fading_bar.date,
            session_index=4,
            bars={fading_bar.symbol: fading_bar},
            histories={fading_bar.symbol: (leader_bar, fading_bar)},
        )

        class FadingSelector:
            @staticmethod
            def latest_scored(_symbol, _strategy_id):
                return {
                    "mainline_score": 50.0,
                    "mainline_state": "fading",
                    "stock_leader_rank": 5,
                    "stock_leader_tier": False,
                    "stock_strong": False,
                    "atr20": 0.5,
                }

        self.assertIsNone(
            policy.on_close(position, fading_context, FadingSelector())
        )
        second_fading_context = selection_module.SelectionContext(
            date="2026-01-12",
            session_index=5,
            bars={fading_bar.symbol: HistoricalBar.from_value(
                "600000",
                daily_bar("2026-01-12", 10.0, 10.0, high=10.1, low=9.8),
            )},
            histories={fading_bar.symbol: (leader_bar, fading_bar)},
        )
        decision = policy.on_close(
            position,
            second_fading_context,
            FadingSelector(),
        )

        self.assertIsNotNone(decision)
        self.assertIn(
            decision.signal,
            {"niu_leader_lost", "niu_mainline_faded"},
        )

    def test_niuone_strong_leader_can_use_only_confirmed_mainline_exit(self):
        policy = NiuOneDailyExitStrategy(
            reversal_strong_leader_mainline_exit=True,
        )
        position = {
            "symbol": "600000",
            "strategy_id": "niu_reversal_probe",
            "reversal_basis": "daily_v",
            "avg_cost": 10.0,
            "entry_session_index": 0,
            "entry_stop_price": 9.0,
            "entry_stop_source": "niu_reversal_right_low",
            "mainline_weak_count": 0,
            "niu_leader_lost_count": 0,
            "highest_price": 10.1,
            "max_pnl_pct": 1.0,
        }

        class StrongLeaderSelector:
            @staticmethod
            def latest_scored(_symbol, _strategy_id):
                return {
                    "mainline_score": 72.0,
                    "mainline_state": "emerging",
                    "stock_leader_rank": 1,
                    "stock_leader_tier": True,
                    "stock_strong": True,
                    "atr20": 0.5,
                }

        def context(date, session_index):
            bar = HistoricalBar.from_value(
                "600000",
                daily_bar(date, 10.0, 10.0, high=10.1, low=9.8),
            )
            return selection_module.SelectionContext(
                date=bar.date,
                session_index=session_index,
                bars={bar.symbol: bar},
                histories={bar.symbol: (bar,)},
            )

        self.assertIsNone(
            policy.on_close(
                position,
                context("2026-01-08", 3),
                StrongLeaderSelector(),
            )
        )

        class FadingSelector:
            @staticmethod
            def latest_scored(_symbol, _strategy_id):
                return {
                    "mainline_score": 50.0,
                    "mainline_state": "fading",
                    "stock_leader_rank": 5,
                    "stock_leader_tier": False,
                    "stock_strong": False,
                    "atr20": 0.5,
                }

        self.assertIsNone(
            policy.on_close(
                position,
                context("2026-01-09", 4),
                FadingSelector(),
            )
        )
        self.assertEqual(position["niu_leader_lost_count"], 0)
        decision = policy.on_close(
            position,
            context("2026-01-12", 5),
            FadingSelector(),
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.signal, "niu_mainline_faded")

    def test_niuone_daily_v_can_exit_t2_unconfirmed_failure(self):
        policy = NiuOneDailyExitStrategy(
            daily_v_unconfirmed_failure_hold_days=2,
        )
        position = {
            "symbol": "600000",
            "strategy_id": "niu_reversal_probe",
            "reversal_basis": "daily_v",
            "avg_cost": 10.0,
            "entry_session_index": 0,
            "entry_stop_price": 9.0,
            "entry_stop_source": "niu_reversal_right_low",
            "mainline_weak_count": 0,
            "highest_price": 10.1,
            "max_pnl_pct": 1.0,
        }
        bar = HistoricalBar.from_value(
            "600000",
            daily_bar("2026-01-08", 10.0, 9.9, high=10.0, low=9.8),
        )
        context = selection_module.SelectionContext(
            date=bar.date,
            session_index=2,
            bars={bar.symbol: bar},
            histories={bar.symbol: (bar,)},
        )

        class UnconfirmedSelector:
            @staticmethod
            def latest_scored(_symbol, _strategy_id):
                return {
                    "mainline_score": 65.0,
                    "mainline_state": "emerging",
                    "mainline_cross_day_persistent": False,
                    "mainline_confirmed": False,
                    "atr20": 0.5,
                }

        decision = policy.on_close(
            dict(position),
            context,
            UnconfirmedSelector(),
        )
        self.assertIsNotNone(decision)
        self.assertEqual(
            decision.signal,
            "niu_reversal_unconfirmed_failure",
        )

        class ConfirmedSelector(UnconfirmedSelector):
            @staticmethod
            def latest_scored(_symbol, _strategy_id):
                return {
                    "mainline_score": 70.0,
                    "mainline_state": "emerging",
                    "mainline_cross_day_persistent": True,
                    "mainline_confirmed": False,
                    "atr20": 0.5,
                }

        self.assertIsNone(
            policy.on_close(dict(position), context, ConfirmedSelector())
        )

        with self.assertRaisesRegex(ValueError, "must be positive"):
            NiuOneDailyExitStrategy(
                daily_v_unconfirmed_failure_hold_days=0,
            )

    def test_niuone_peak_mainline_decay_exit_is_research_configurable(self):
        policy = NiuOneDailyExitStrategy(
            reversal_mainline_weak_confirmations=None,
            reversal_mainline_peak_drawdown_points=5.0,
        )
        entry_bar = HistoricalBar.from_value(
            "600000",
            daily_bar("2026-01-06", 10.0, 10.0),
        )
        signal = SelectionSignal(
            "600000",
            strategy_id="niu_reversal_probe",
            score=8.0,
            metadata={"scored": {
                "stop_price": 9.0,
                "mainline_score": 70.0,
                "mainline_state": "emerging",
            }},
        )
        position = {
            **policy.on_entry(signal, entry_bar, 10.0),
            "symbol": "600000",
            "strategy_id": "niu_reversal_probe",
            "avg_cost": 10.0,
            "entry_session_index": 1,
        }
        exit_bar = HistoricalBar.from_value(
            "600000",
            daily_bar("2026-01-07", 10.0, 10.1, high=10.2, low=9.8),
        )
        context = selection_module.SelectionContext(
            date=exit_bar.date,
            session_index=2,
            bars={exit_bar.symbol: exit_bar},
            histories={exit_bar.symbol: (entry_bar, exit_bar)},
        )

        class DecaySelector:
            def latest_scored(self, _symbol, _strategy_id):
                return {
                    "mainline_score": 65.0,
                    "mainline_state": "emerging",
                }

        decision = policy.on_close(position, context, DecaySelector())

        self.assertIsNotNone(decision)
        self.assertEqual(
            decision.signal,
            "niu_reversal_mainline_peak_decay",
        )
        self.assertEqual(position["mainline_peak_score"], 70.0)
        self.assertEqual(position["mainline_peak_drawdown_points"], 5.0)

    def test_niuone_research_exit_options_validate_bounds(self):
        with self.assertRaises(ValueError):
            NiuOneDailyExitStrategy(partial_take_profit_r=0)
        with self.assertRaises(ValueError):
            NiuOneDailyExitStrategy(partial_take_profit_ratio=1)
        with self.assertRaises(ValueError):
            NiuOneDailyExitStrategy(reversal_mainline_weak_confirmations=0)
        with self.assertRaises(ValueError):
            NiuOneDailyExitStrategy(
                reversal_mainline_peak_drawdown_points=0,
            )
        with self.assertRaises(ValueError):
            NiuOneDailyExitStrategy(
                reversal_mainline_peak_drawdown_points=float("nan"),
            )
        with self.assertRaises(ValueError):
            NiuOneDailyExitStrategy(reversal_early_partial_take_profit_r=0)
        with self.assertRaises(ValueError):
            NiuOneDailyExitStrategy(reversal_early_partial_take_profit_ratio=1)
        with self.assertRaises(ValueError):
            NiuOneDailyExitStrategy(
                reversal_early_profit_regimes=("unknown",),
            )
        with self.assertRaises(ValueError):
            NiuOneDailyExitStrategy(lifecycle_climax_partial_ratio=0)
        with self.assertRaises(ValueError):
            NiuOneDailyExitStrategy(lifecycle_climax_partial_ratio=1)
        with self.assertRaises(ValueError):
            NiuOneDailyExitStrategy(lifecycle_climax_min_pnl_pct=-0.1)
        with self.assertRaises(ValueError):
            NiuOneDailyExitStrategy(
                lifecycle_climax_min_pnl_pct=float("nan"),
            )

    def test_niuone_structure_stop_uses_intraday_stop_reference_not_close(self):
        rows = [
            daily_bar("2026-01-05", 10.0, 10.0),
            daily_bar("2026-01-06", 10.0, 10.0),
            daily_bar(
                "2026-01-07",
                10.0,
                8.8,
                high=10.1,
                low=8.5,
            ),
        ]

        class StopSelector:
            def on_close(self, context):
                if context.date != "2026-01-05":
                    return []
                return [SelectionSignal(
                    "600000",
                    strategy_id="niu_reversal_probe",
                    score=9.0,
                    metadata={"scored": {
                        "stop_price": 9.5,
                        "stop_source": "niu_reversal_low",
                        "atr20": 0.5,
                        "industry": "银行",
                    }},
                )]

        result = run_selection_backtest(
            {"600000": rows},
            StopSelector(),
            position_exit_strategy=NiuOneDailyExitStrategy(),
            config=SelectionBacktestConfig(
                holding_sessions=(1,),
                signal_start_date="2026-01-05",
                signal_end_date="2026-01-05",
                slippage_bps=0,
                price_limit_resolver=None,
                cost_model=SelectionCostModel(
                    commission_rate=0,
                    transfer_fee_rate=0,
                    sell_stamp_duty_rate=0,
                ),
            ),
        )

        self.assertEqual(result.trades[0]["exit_signal"], "niu_structure_stop")
        self.assertEqual(result.trades[0]["exit_price"], 9.5)
        self.assertEqual(result.trades[0]["net_return_pct"], -5.0)

    def test_niuone_structure_stop_uses_open_after_gap_through(self):
        policy = NiuOneDailyExitStrategy()
        bar = HistoricalBar.from_value(
            "600000",
            daily_bar(
                "2026-01-07",
                9.0,
                9.1,
                high=9.2,
                low=8.8,
            ),
        )
        context = selection_module.SelectionContext(
            date=bar.date,
            session_index=2,
            bars={bar.symbol: bar},
            histories={bar.symbol: (bar,)},
        )
        decision = policy.on_close(
            {
                "symbol": bar.symbol,
                "strategy_id": "niu_reversal_probe",
                "avg_cost": 10.0,
                "entry_session_index": 1,
                "entry_stop_price": 9.5,
                "entry_stop_source": "niu_reversal_low",
            },
            context,
            lambda _context: (),
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.fill_reference_price, 9.0)

    def test_prevalidated_bars_preserve_mapping_input_results(self):
        rows = [
            daily_bar("2026-01-05", 10.0, 10.2),
            daily_bar("2026-01-06", 10.3, 10.5),
            daily_bar("2026-01-07", 10.6, 10.8),
        ]
        config = SelectionBacktestConfig(
            holding_sessions=(1,),
            slippage_bps=0,
            price_limit_resolver=None,
            cost_model=SelectionCostModel(
                commission_rate=0,
                transfer_fee_rate=0,
                sell_stamp_duty_rate=0,
            ),
        )

        def selector(context):
            return (
                [SelectionSignal("600000", strategy_id="equivalence")]
                if context.date == "2026-01-05" else []
            )

        mapping_result = run_selection_backtest(
            {"600000": rows},
            selector,
            config=config,
        )
        object_result = run_selection_backtest(
            {
                "600000": [
                    HistoricalBar.from_value("600000", row)
                    for row in rows
                ],
            },
            selector,
            config=config,
        )
        self.assertEqual(object_result.to_dict(), mapping_result.to_dict())

    def test_measures_next_open_forward_returns_and_deduplicates(self):
        bars = {"600000": [
            daily_bar("2026-01-05", 9.8, 10.0),
            daily_bar("2026-01-06", 10.0, 10.0, name="浦发银行"),
            daily_bar("2026-01-07", 10.0, 11.0),
            daily_bar("2026-01-08", 11.0, 12.0),
            daily_bar("2026-01-09", 12.0, 11.5),
        ]}
        observed = []

        def selector(context):
            observed.append((context.date, [bar.date for bar in context.history("600000")]))
            return (
                [SelectionSignal("600000", strategy_id="niu_leader", score=9.1)]
                if context.date <= "2026-01-07" else []
            )

        result = run_selection_backtest(
            bars,
            selector,
            config=SelectionBacktestConfig(
                holding_sessions=(1, 2),
                signal_start_date="2026-01-06",
                signal_end_date="2026-01-07",
                cooldown_sessions=10,
                slippage_bps=0,
                price_limit_resolver=None,
                cost_model=SelectionCostModel(
                    commission_rate=0,
                    transfer_fee_rate=0,
                    sell_stamp_duty_rate=0,
                ),
            ),
        )
        self.assertEqual(result.statistics["signal_count"], 2)
        self.assertEqual(result.statistics["evaluated_signal_count"], 1)
        self.assertEqual(result.statistics["duplicate_signal_count"], 1)
        self.assertEqual(result.signals[0]["entry_date"], "2026-01-07")
        self.assertEqual(result.signals[0]["name"], "浦发银行")
        self.assertEqual(result.signals[0]["forward_returns"][1]["net_return_pct"], 10.0)
        self.assertEqual(result.signals[0]["forward_returns"][2]["net_return_pct"], 20.0)
        self.assertEqual(result.statistics["by_horizon"]["2"]["win_rate_pct"], 100.0)
        self.assertEqual(
            result.statistics["by_strategy"]["niu_leader"]["by_horizon"]["2"]
            ["average_net_return_pct"],
            20.0,
        )
        self.assertEqual(observed[0], ("2026-01-05", ["2026-01-05"]))
        self.assertIn('"niu_leader"', json.dumps(result.to_dict()))

    def test_rejects_untradable_limit_up_entry(self):
        bars = {"600000": [
            daily_bar("2026-01-05", 10.0, 10.0),
            daily_bar("2026-01-06", 11.0, 11.0, previous_close=10.0),
        ]}
        result = run_selection_backtest(
            bars,
            lambda context: (
                [SelectionSignal("600000")] if context.date == "2026-01-05" else []
            ),
            config=SelectionBacktestConfig(holding_sessions=(1,)),
        )
        self.assertEqual(result.signals[0]["status"], "rejected")
        self.assertEqual(result.signals[0]["status_reason"], "open_at_limit_up")

    def test_registered_selector_chooses_highest_scoring_strategy(self):
        def scorer(score, decision, verdict):
            def run(_rows):
                return {
                    "score": score, "entry_threshold": 8.0,
                    "strategy_priority": 50, "decision_score": decision,
                    "verdict": verdict, "hard_blockers": [], "actionable": True,
                }
            return run

        bars = {"600000": [
            daily_bar("2026-01-05", 10.0),
            daily_bar("2026-01-06", 10.1, name="浦发银行"),
            daily_bar("2026-01-07", 10.2),
        ]}
        selector = RegisteredScorerSelector(
            ["lower", "higher"],
            max_signals_per_session=1,
            scorers={
                "lower": scorer(8.1, 8.6, "lower"),
                "higher": scorer(8.8, 9.6, "higher"),
            },
        )
        result = run_selection_backtest(
            bars,
            selector,
            config=SelectionBacktestConfig(
                holding_sessions=(1,), signal_start_date="2026-01-06",
                signal_end_date="2026-01-06", slippage_bps=0,
                price_limit_resolver=None,
            ),
        )
        self.assertEqual(result.signals[0]["strategy_id"], "higher")
        self.assertEqual(result.signals[0]["entry_date"], "2026-01-07")

    def test_replay_progress_separates_context_building_and_scoring(self):
        phases = []

        def context_provider(_context):
            return {"market": {"state": "risk_on"}}

        selector = RegisteredScorerSelector(
            ["recorded"],
            context_provider=context_provider,
            scorers={
                "recorded": lambda _rows: {
                    "score": 9.0,
                    "entry_threshold": 8.0,
                    "hard_blockers": [],
                    "actionable": True,
                },
            },
        )
        build_selection_replay_tape(
            {"600000": [
                daily_bar("2026-01-05", 10.0),
                daily_bar("2026-01-06", 10.1),
            ]},
            selector,
            config=SelectionBacktestConfig(
                holding_sessions=(1,),
                signal_start_date="2026-01-05",
                signal_end_date="2026-01-06",
            ),
            replay_progress_callback=(
                lambda completed, total, trading_date, phase, elapsed, eta:
                phases.append((completed, total, trading_date, phase, elapsed, eta))
            ),
        )

        phase_names = [item[3] for item in phases]
        self.assertIn("rebuilding_context", phase_names)
        self.assertIn("scoring", phase_names)
        self.assertEqual(phases[-1][0:4], (2, 2, "2026-01-06", "scoring"))

    def test_replay_eta_uses_recent_or_current_slow_sessions(self):
        self.assertIsNone(selection_module._estimate_replay_eta([], 5))
        self.assertEqual(selection_module._estimate_replay_eta([], 0), 0.0)
        self.assertEqual(
            selection_module._estimate_replay_eta(
                [],
                5,
                current_session_elapsed=2.0,
            ),
            10.0,
        )
        self.assertEqual(
            selection_module._estimate_replay_eta(
                [1.0] * 10 + [3.0] * 10,
                5,
            ),
            15.0,
        )
        self.assertEqual(
            selection_module._estimate_replay_eta(
                [1.0] * 20,
                5,
                current_session_elapsed=4.0,
            ),
            20.0,
        )

    def test_registered_selector_caps_one_strategy_without_hiding_other_paths(self):
        def reversal(_rows):
            return {
                "score": 9.0, "entry_threshold": 8.0,
                "strategy_priority": 50, "decision_score": 9.0,
                "verdict": "reversal", "hard_blockers": [], "actionable": True,
            }

        def mature(rows):
            if str(rows[-1].get("symbol_code") or "") != "600003":
                return None
            return {
                "score": 8.5, "entry_threshold": 8.0,
                "strategy_priority": 80, "decision_score": 10.0,
                "verdict": "mature", "hard_blockers": [], "actionable": True,
            }

        bars = {
            f"60000{index}": [
                daily_bar("2026-01-05", 10.0 + index),
                daily_bar("2026-01-06", 10.1 + index),
                daily_bar("2026-01-07", 10.2 + index),
            ]
            for index in range(4)
        }
        selector = RegisteredScorerSelector(
            ["reversal", "mature"],
            max_signals_per_session=4,
            max_signals_per_strategy_per_session={"reversal": 2},
            scorers={"reversal": reversal, "mature": mature},
        )

        result = run_selection_backtest(
            bars,
            selector,
            config=SelectionBacktestConfig(
                holding_sessions=(1,), signal_start_date="2026-01-06",
                signal_end_date="2026-01-06", price_limit_resolver=None,
            ),
        )

        strategies = [item["strategy_id"] for item in result.signals]
        self.assertEqual(strategies.count("reversal"), 2)
        self.assertEqual(strategies.count("mature"), 1)
        self.assertIn(
            "600003",
            [item["symbol"] for item in result.signals if item["strategy_id"] == "mature"],
        )

    def test_registered_selector_scores_only_eligible_symbols(self):
        def scorer(_rows):
            return {
                "score": 9.0, "entry_threshold": 8.0,
                "strategy_priority": 50, "decision_score": 9.0,
                "verdict": "selected", "hard_blockers": [], "actionable": True,
            }

        bars = {
            symbol: [
                daily_bar("2026-01-05", price),
                daily_bar("2026-01-06", price + 0.1),
                daily_bar("2026-01-07", price + 0.2),
            ]
            for symbol, price in (("600000", 10.0), ("300001", 20.0))
        }
        result = run_selection_backtest(
            bars,
            RegisteredScorerSelector(
                ["eligible"],
                eligible_symbols=("600000",),
                scorers={"eligible": scorer},
            ),
            config=SelectionBacktestConfig(
                holding_sessions=(1,), signal_start_date="2026-01-06",
                signal_end_date="2026-01-06", price_limit_resolver=None,
            ),
        )

        self.assertEqual([item["symbol"] for item in result.signals], ["600000"])

    def test_registered_selector_reports_bounded_rejection_diagnostics(self):
        def blocked_scorer(_rows, _context):
            return {
                "score": 8.7,
                "entry_threshold": 8.0,
                "hard_blockers": ["距EMA20过远", "结构止损过大"],
                "actionable": False,
            }

        blocked_scorer.requires_context = True

        def below_threshold_scorer(_rows, _context):
            return {
                "score": 7.9,
                "entry_threshold": 8.0,
                "hard_blockers": [],
                "actionable": False,
            }

        below_threshold_scorer.requires_context = True
        bars = {"600000": [
            daily_bar("2026-01-05", 10.0),
            daily_bar("2026-01-06", 10.1, name="浦发银行"),
            daily_bar("2026-01-07", 10.2),
        ]}
        result = run_selection_backtest(
            bars,
            RegisteredScorerSelector(
                ["blocked", "below"],
                context_provider=lambda _context: {},
                scorers={
                    "blocked": blocked_scorer,
                    "below": below_threshold_scorer,
                },
            ),
            config=SelectionBacktestConfig(
                holding_sessions=(1,),
                signal_start_date="2026-01-06",
                signal_end_date="2026-01-06",
                price_limit_resolver=None,
            ),
        )

        self.assertEqual(result.statistics["signal_count"], 0)
        self.assertEqual(result.diagnostics["threshold_met_count"], 1)
        blocked = result.diagnostics["by_strategy"]["blocked"]
        self.assertEqual(blocked["evaluated_count"], 1)
        self.assertEqual(blocked["threshold_met_count"], 1)
        self.assertEqual(blocked["actionable_candidate_count"], 0)
        self.assertEqual(blocked["maximum_score"], 8.7)
        self.assertEqual(
            blocked["blockers"][0],
            {"reason": "结构止损过大", "count": 1},
        )
        self.assertEqual(blocked["near_misses"][0]["date"], "2026-01-06")
        self.assertEqual(blocked["near_misses"][0]["symbol"], "600000")
        self.assertEqual(blocked["near_misses"][0]["name"], "浦发银行")
        below = result.diagnostics["by_strategy"]["below"]
        self.assertEqual(below["below_threshold_count"], 1)
        self.assertEqual(below["blockers"], [])
        self.assertIn("评分不足", below["near_misses"][0]["reasons"][0])
        sensitivity = {
            item["threshold_offset"]: item["candidate_count"]
            for item in below["score_sensitivity"]
        }
        self.assertEqual(sensitivity[-0.25], 1)
        self.assertEqual(sensitivity[0.0], 0)
        self.assertEqual(
            result.diagnostics["periods"]["2026-01"]
            ["by_strategy"]["below"]["score_sensitivity"],
            below["score_sensitivity"],
        )
        self.assertIn("diagnostics", result.to_dict())

    def test_registered_selector_reports_price_gate_ablation_by_month_and_branch(self):
        def price_blocked_scorer(_rows, _context):
            return {
                "score": 8.7,
                "entry_threshold": 8.0,
                "hard_blockers": ["未形成突破/首次缩量回踩"],
                "actionable": False,
                "industry": "存储芯片",
                "stock_leader_tier": True,
                "niuone_lifecycle_stage": "markup",
            }

        price_blocked_scorer.requires_context = True
        result = run_selection_backtest(
            {"600000": [
                daily_bar("2026-06-01", 10.0),
                daily_bar("2026-06-02", 10.1, name="分支龙头"),
            ]},
            RegisteredScorerSelector(
                ["price"],
                context_provider=lambda _context: {},
                scorers={"price": price_blocked_scorer},
            ),
            config=SelectionBacktestConfig(
                holding_sessions=(1,),
                signal_start_date="2026-06-02",
                signal_end_date="2026-06-02",
                price_limit_resolver=None,
            ),
        )

        diagnostics = result.diagnostics["periods"]["2026-06"]
        strategy = diagnostics["by_strategy"]["price"]
        price_family = next(
            item for item in strategy["hard_gate_family_ablation"]
            if item["family"] == "price_structure"
        )
        self.assertEqual(price_family["blocked_candidate_count"], 1)
        self.assertEqual(price_family["rescued_at_production_threshold"], 1)
        self.assertEqual(
            strategy["single_hard_gate_ablation"],
            [{
                "reason": "未形成突破/首次缩量回踩",
                "rescued_at_production_threshold": 1,
            }],
        )
        self.assertEqual(
            strategy["leader_branch_coverage"][0]["industry"],
            "存储芯片",
        )
        self.assertEqual(
            strategy["leader_branch_coverage"][0]
            ["blocker_family_counts"]["price_structure"],
            1,
        )
        branch = strategy["leader_branch_coverage"][0]
        self.assertEqual(
            branch["monthly_blocker_family_counts"]["price_structure"],
            1,
        )
        self.assertEqual(
            branch["best_reasons"],
            ["未形成突破/首次缩量回踩"],
        )
        self.assertEqual(
            branch["best_blocker_family_counts"],
            {"price_structure": 1},
        )
        self.assertFalse(branch["best_actionable"])

    def test_diagnostics_report_conditional_thresholds_without_false_single_value(self):
        def conditional_scorer(rows, _context):
            threshold = 8.0 if rows[-1]["date"] == "2026-06-02" else 8.4
            return {
                "score": threshold,
                "entry_threshold": threshold,
                "hard_blockers": [],
                "actionable": True,
            }

        conditional_scorer.requires_context = True
        result = run_selection_backtest(
            {"600000": [
                daily_bar("2026-06-01", 10.0),
                daily_bar("2026-06-02", 10.1),
                daily_bar("2026-06-03", 10.2),
            ]},
            RegisteredScorerSelector(
                ["conditional"],
                context_provider=lambda _context: {},
                scorers={"conditional": conditional_scorer},
            ),
            config=SelectionBacktestConfig(
                holding_sessions=(1,),
                signal_start_date="2026-06-02",
                signal_end_date="2026-06-03",
                price_limit_resolver=None,
            ),
        )

        diagnostic = result.diagnostics["periods"]["2026-06"][
            "by_strategy"
        ]["conditional"]
        self.assertIsNone(diagnostic["entry_threshold"])
        self.assertTrue(diagnostic["conditional_entry_threshold"])
        self.assertEqual(diagnostic["entry_thresholds"], [
            {"threshold": 8.0, "evaluated_count": 1},
            {"threshold": 8.4, "evaluated_count": 1},
        ])
        production = next(
            item for item in diagnostic["score_sensitivity"]
            if item["threshold_offset"] == 0
        )
        self.assertIsNone(production["threshold"])
        self.assertEqual(production["applicable_thresholds"], [8.0, 8.4])
        self.assertEqual(production["candidate_count"], 2)

    def test_context_scorer_receives_historical_provider_dates(self):
        provider_dates = []

        def scorer(rows, shared_context):
            if len(rows) < 2 or not shared_context.get("allow"):
                return None
            return {
                "score": 9.0, "entry_threshold": 8.0,
                "strategy_priority": 90, "decision_score": 9.9,
                "verdict": "context selection", "hard_blockers": [],
                "actionable": True,
            }

        scorer.requires_context = True

        def context_provider(context):
            provider_dates.append(context.date)
            return {"allow": context.date == "2026-01-06"}

        bars = {"600000": [
            daily_bar("2026-01-05", 10.0),
            daily_bar("2026-01-06", 10.1),
            daily_bar("2026-01-07", 10.2),
        ]}
        result = run_selection_backtest(
            bars,
            RegisteredScorerSelector(
                ["context"], context_provider=context_provider,
                scorers={"context": scorer},
            ),
            config=SelectionBacktestConfig(
                holding_sessions=(1,), slippage_bps=0, price_limit_resolver=None,
            ),
        )
        self.assertEqual(provider_dates, ["2026-01-05", "2026-01-06", "2026-01-07"])
        self.assertEqual(result.signals[0]["signal_date"], "2026-01-06")

    def test_registered_selector_precomputes_indicators_once_and_skips_unused_warmup(self):
        start = date(2026, 1, 1)
        dates = [(start + timedelta(days=index)).isoformat() for index in range(45)]
        raw_rows = [
            daily_bar(
                trading_date,
                10 + index * 0.05,
                10.02 + index * 0.05,
                volume=10_000 + index * 100,
            )
            for index, trading_date in enumerate(dates)
        ]
        expected = {}
        for index in (39,):
            rows = [dict(item) for item in raw_rows[:index + 1]]
            enrich_strategy_rows(rows)
            expected[dates[index]] = {
                key: rows[-1].get(key) for key in ("bbi", "ema20", "ema50", "j")
            }

        observed = {}
        preparation = []
        normalization_progress = []
        preparation_progress = []

        def scorer(rows):
            observed[rows[-1]["date"]] = {
                key: rows[-1].get(key) for key in ("bbi", "ema20", "ema50", "j")
            }
            return None

        progress = []
        selector = RegisteredScorerSelector(
            ["cached"],
            scorers={"cached": scorer},
        )
        with patch.object(
            selection_module,
            "enrich_rows",
            wraps=selection_module.enrich_rows,
        ) as enriched:
            run_selection_backtest(
                {"600000": raw_rows},
                selector,
                config=SelectionBacktestConfig(
                    holding_sessions=(1,),
                    signal_start_date=dates[39],
                    signal_end_date=dates[39],
                ),
                progress_callback=lambda completed, total, day: progress.append(
                    (completed, total, day)
                ),
                preparation_callback=preparation.append,
                normalization_progress_callback=lambda completed, total: (
                    normalization_progress.append((completed, total))
                ),
                preparation_progress_callback=lambda completed, total: (
                    preparation_progress.append((completed, total))
                ),
            )

        self.assertEqual(enriched.call_count, 1)
        self.assertEqual(observed, expected)
        self.assertEqual(progress, [(1, 1, dates[39])])
        self.assertEqual(preparation, ["正在预计算技术指标（1/1）"])
        self.assertEqual(normalization_progress, [(0, 1), (1, 1)])
        self.assertEqual(preparation_progress, [(0, 1), (1, 1)])

    def test_registered_selector_reuses_declared_scorer_input_once_per_stock(self):
        builder_calls = []
        observed_inputs = []

        def builder(rows):
            builder_calls.append(rows[-1]["date"])
            return {"date": rows[-1]["date"]}

        def scorer(rows, *, prepared):
            observed_inputs.append(prepared)
            return {
                "score": 9.0,
                "entry_threshold": 8.0,
                "hard_blockers": [],
                "actionable": True,
            }

        scorer.shared_input_builder = builder
        scorer.shared_input_keyword = "prepared"
        result = run_selection_backtest(
            {"600000": [
                daily_bar("2026-01-05", 10.0),
                daily_bar("2026-01-06", 10.1),
                daily_bar("2026-01-07", 10.2),
            ]},
            RegisteredScorerSelector(
                ["shared_a", "shared_b"],
                scorers={"shared_a": scorer, "shared_b": scorer},
            ),
            config=SelectionBacktestConfig(
                holding_sessions=(1,),
                signal_start_date="2026-01-06",
                signal_end_date="2026-01-06",
                price_limit_resolver=None,
            ),
        )

        self.assertEqual(builder_calls, ["2026-01-06"])
        self.assertEqual(len(observed_inputs), 2)
        self.assertIs(observed_inputs[0], observed_inputs[1])
        self.assertEqual(result.statistics["signal_count"], 1)

    def test_prepared_strategy_tail_is_a_read_only_overlay_view(self):
        prepared = tuple(
            {"date": f"session-{index}", "close": float(index)}
            for index in range(200)
        )
        context = selection_module.SelectionContext(
            date="session-199",
            session_index=199,
            bars={},
            histories={},
            strategy_rows={"600000": prepared},
        )

        rows = selection_module._strategy_rows_at_close(
            context,
            "600000",
            history_limit=120,
        )
        overlaid = selection_module._strategy_rows_with_latest_values(
            rows,
            {"symbol_code": "600000"},
        )

        self.assertIsInstance(rows, selection_module._StrategyRowsView)
        self.assertIsInstance(rows[-20:-1], selection_module._StrategyRowsView)
        self.assertEqual(len(rows), 120)
        self.assertIs(rows[0], prepared[80])
        self.assertNotIn("symbol_code", prepared[-1])
        self.assertEqual(overlaid[-1]["symbol_code"], "600000")
        with self.assertRaises(TypeError):
            rows[-1] = {}

    def test_niuone_bounded_state_warmup_matches_full_valid_replay(self):
        start = date(2026, 1, 1)
        dates = [
            (start + timedelta(days=index)).isoformat()
            for index in range(110)
        ]
        bars = {
            f"60000{member}": [
                daily_bar(
                    trading_date,
                    10.0 + index * (0.03 + member * 0.002),
                    10.02 + index * (0.03 + member * 0.002),
                    high=10.08 + index * (0.03 + member * 0.002),
                    low=9.96 + index * (0.03 + member * 0.002),
                    volume=1_000 + index * 10,
                    amount=1_000_000_000 + member * 100_000_000,
                    industry="半导体",
                    themes=["半导体"],
                    name=f"测试{member}",
                )
                for index, trading_date in enumerate(dates)
            ]
            for member in range(4)
        }

        class CountingProvider(NiuOneHistoricalContextProvider):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def __call__(self, context):
                self.calls += 1
                return super().__call__(context)

        class FullWarmupProvider(CountingProvider):
            backtest_warmup_sessions = None

        def run(provider):
            result = run_selection_backtest(
                bars,
                RegisteredScorerSelector(
                    ["niu_leader", "niu_pullback", "niu_emerging", "niu_reversal_probe"],
                    context_provider=provider,
                    eligible_symbols=bars,
                ),
                config=SelectionBacktestConfig(
                    holding_sessions=(1,),
                    signal_start_date=dates[100],
                    signal_end_date=dates[100],
                    price_limit_resolver=None,
                ),
            )
            return result.to_dict()

        bounded_provider = CountingProvider()
        full_provider = FullWarmupProvider()
        bounded = run(bounded_provider)
        full = run(full_provider)

        self.assertEqual(bounded, full)
        self.assertEqual(bounded_provider.calls, 61)
        self.assertEqual(full_provider.calls, 101)

    def test_stateful_context_warms_up_without_running_discarded_scorers(self):
        dates = [f"2026-01-0{index}" for index in range(1, 5)]
        bars = {"600000": [
            daily_bar(trading_date, 10 + index * 0.1)
            for index, trading_date in enumerate(dates)
        ]}

        class LegacyRegisteredSelector(RegisteredScorerSelector):
            def set_signal_generation_enabled(self, _enabled):
                pass

        def run(selector_type):
            provider_dates = []
            scorer_dates = []

            def provider(context):
                provider_dates.append(context.date)
                return {"seen_sessions": len(provider_dates)}

            def scorer(rows, shared_context):
                scorer_dates.append(rows[-1]["date"])
                if rows[-1]["date"] != dates[2] or shared_context["seen_sessions"] != 3:
                    return None
                return {
                    "score": 9.0,
                    "entry_threshold": 8.0,
                    "decision_score": 9.0,
                    "strategy_priority": 1,
                    "verdict": "same result",
                    "hard_blockers": [],
                    "actionable": True,
                }

            scorer.requires_context = True
            result = run_selection_backtest(
                bars,
                selector_type(
                    ["stateful"],
                    context_provider=provider,
                    scorers={"stateful": scorer},
                ),
                config=SelectionBacktestConfig(
                    holding_sessions=(1,),
                    signal_start_date=dates[2],
                    signal_end_date=dates[2],
                    slippage_bps=0,
                    price_limit_resolver=None,
                ),
            )
            return result.to_dict(), provider_dates, scorer_dates

        optimized, provider_dates, scorer_dates = run(RegisteredScorerSelector)
        baseline, baseline_provider_dates, baseline_scorer_dates = run(
            LegacyRegisteredSelector
        )
        self.assertEqual(optimized, baseline)
        self.assertEqual(provider_dates, dates[:3])
        self.assertEqual(baseline_provider_dates, dates[:3])
        self.assertEqual(scorer_dates, [dates[2]])
        self.assertEqual(baseline_scorer_dates, dates[1:3])

    def test_adapter_runs_an_actual_registered_strategy(self):
        bars = {"600000": []}
        for index in range(47):
            close = 10.0 + index * 0.05
            bars["600000"].append(daily_bar(
                (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                close - 0.02,
                close,
                high=close + 0.08,
                low=close - 0.08,
                volume=500 if index >= 40 else 1_000,
            ))
        result = run_selection_backtest(
            bars,
            RegisteredScorerSelector(["trend_pullback"], max_signals_per_session=1),
            config=SelectionBacktestConfig(
                holding_sessions=(1,), cooldown_sessions=20,
                slippage_bps=0, price_limit_resolver=None,
            ),
        )
        self.assertTrue(result.signals)
        self.assertEqual(result.signals[0]["strategy_id"], "trend_pullback")

    def test_niuone_context_provider_rolls_historical_cross_section(self):
        bars = {}
        for member_index in range(4):
            symbol = f"6000{member_index:02d}"
            rows = []
            for index in range(57):
                close = 10.0 + index * (0.04 + member_index * 0.005)
                rows.append(daily_bar(
                    (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                    close - 0.02,
                    close,
                    high=close + 0.08,
                    low=close - 0.08,
                    volume=1_000 + index * 10 + member_index,
                    amount=1_000_000_000 + member_index * 100_000_000,
                    industry="半导体",
                    name=f"测试{symbol}",
                ))
            bars[symbol] = rows
        provider = NiuOneHistoricalContextProvider()
        observed = []

        def selector(context):
            if context.session_index >= 54:
                observed.append(provider(context))
            return []

        run_selection_backtest(
            bars, selector, config=SelectionBacktestConfig(price_limit_resolver=None)
        )
        self.assertEqual(len(observed), 3)
        self.assertEqual(observed[0]["as_of_date"], "2026-02-24")
        self.assertEqual(observed[1]["previous_trading_day"], "2026-02-24")
        self.assertEqual(observed[-1]["coverage_diagnostics"]["reference_pool_count"], 4)
        self.assertEqual(observed[-1]["mapped_stock_count"], 4)
        self.assertIn("半导体", observed[-1]["themes"])
        self.assertEqual(
            provider.latest_cross_section()["半导体"]["score"],
            observed[-1]["themes"]["半导体"]["score"],
        )

    def test_sector_tide_context_provider_rolls_historical_market_state(self):
        bars = {}
        for member_index in range(4):
            symbol = f"6000{member_index:02d}"
            bars[symbol] = [
                daily_bar(
                    (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                    10.0 + index * 0.04,
                    10.02 + index * 0.04,
                    high=10.08 + index * 0.04,
                    low=9.96 + index * 0.04,
                    volume=1_000 + index,
                    amount=1_000_000_000,
                    industry="半导体",
                    name=f"测试{symbol}",
                )
                for index in range(57)
            ]
        provider = SectorTideHistoricalContextProvider()
        observed = []

        def selector(context):
            if context.session_index >= 54:
                observed.append(provider(context))
            return []

        run_selection_backtest(
            bars, selector, config=SelectionBacktestConfig(price_limit_resolver=None)
        )
        self.assertEqual(len(observed), 3)
        self.assertEqual(observed[-1]["mapped_stock_count"], 4)
        self.assertIn(observed[-1]["market"]["state"], {"offensive", "rotation", "defensive"})
        self.assertGreater(observed[-1]["market"]["score"], 0)

    def test_a_share_limit_resolver_handles_common_boards(self):
        def bar(symbol, trading_date="2026-01-06", **kwargs):
            return HistoricalBar(
                symbol=symbol, date=trading_date, open=10, high=10,
                low=10, close=10, volume=1, **kwargs,
            )

        self.assertEqual(a_share_price_limits(bar("600000", is_st=True), 10), (10.5, 9.5))
        self.assertEqual(a_share_price_limits(bar("300001", "2020-08-21"), 10), (11.0, 9.0))
        self.assertEqual(a_share_price_limits(bar("300001", "2020-08-24"), 10), (12.0, 8.0))
        self.assertEqual(a_share_price_limits(bar("830799"), 10), (13.0, 7.0))

    def test_invalid_data_and_selector_failures_are_diagnostic(self):
        with self.assertRaisesRegex(SelectionBacktestError, "inconsistent OHLC"):
            HistoricalBar(
                symbol="600000", date="2026-01-05", open=10,
                high=9, low=8, close=8.5, volume=1,
            )
        bars = {"600000": [daily_bar("2026-01-05", 10.0)]}

        def broken(_context):
            raise ValueError("bad signal")

        with self.assertRaisesRegex(
            SelectionBacktestError, "selector failed after 2026-01-05 close"
        ):
            run_selection_backtest(bars, broken)


if __name__ == "__main__":
    unittest.main()
