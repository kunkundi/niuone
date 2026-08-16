from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from app.backtesting.prompt_strategy import (
    PROMPT_BACKTEST_PROTOCOL_VERSION,
    PromptStrategyBacktestPolicy,
    PromptStrategyHistoricalSelector,
    validate_prompt_backtest_version,
)
from app.backtesting.selection import (
    HistoricalBar,
    SelectionBacktestConfig,
    run_selection_backtest,
)
from app.backtesting.tasks import (
    _prompt_backtest_audit_manifest,
    BACKTEST_STATE_SCHEMA_VERSION,
    BacktestTaskManager,
    backtest_strategy_options,
    normalize_backtest_request,
    run_strategy_backtest_request,
)
from app.storage.prompt_strategies import PromptStrategyStore
from app.strategies.rules import compile_strategy_spec, replay_rule_evaluation_audit
from app.strategies.rules.schema import sha256_json

from test_prompt_rule_engine import kdj_spec, outside_bar_spec


def frozen_version(spec=None, *, version_id="preset_text-v1-test"):
    plan = compile_strategy_spec(spec or kdj_spec())
    return {
        "version_id": version_id,
        "strategy_key": "preset_text",
        "revision": 1,
        "status": "active",
        "plan_sha256": plan["plan_sha256"],
        "engine_version": plan["engine_version"],
        "execution_plan": plan,
    }


def price_bars(values):
    start = date(2026, 1, 1)
    return [
        HistoricalBar(
            symbol="sh600000",
            date=(start + timedelta(days=index)).isoformat(),
            open=float(value),
            high=float(value) + 0.2,
            low=float(value) - 0.2,
            close=float(value),
            volume=1_000 + index,
            name="测试股",
        )
        for index, value in enumerate(values)
    ]


class PromptStrategyBacktestingTests(unittest.TestCase):
    def test_manager_ignores_result_from_old_prompt_backtest_protocol(self):
        with tempfile.TemporaryDirectory(prefix="niuone-prompt-backtest-") as tmp:
            state_dir = Path(tmp)
            version = frozen_version()
            (state_dir / "preset_text.json").write_text(
                json.dumps({
                    "schema_version": BACKTEST_STATE_SCHEMA_VERSION,
                    "job": {
                        "id": "stale-prompt-result",
                        "status": "succeeded",
                        "phase": "completed",
                        "strategy": {"id": "preset_text"},
                        "request": {
                            "start_date": "2026-06-01",
                            "end_date": "2026-06-30",
                            "adjustment": "qfq",
                            "sources": ["tencent"],
                            "risk_profile": "balanced",
                            "protocol_version": "prompt-backtest-v1",
                            "prompt_strategy_version": version,
                        },
                        "result": {
                            "protocol": {"version": "prompt-backtest-v1"},
                        },
                    },
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            manager = BacktestTaskManager(
                runner=lambda *_args, **_kwargs: {},
                state_dir=state_dir,
            )
            try:
                self.assertIsNone(manager.latest("preset_text"))
            finally:
                manager.shutdown()

    def test_existing_v2_frozen_plan_remains_replayable(self):
        current = frozen_version()

        def without_offsets(value):
            if isinstance(value, dict):
                return {
                    key: without_offsets(item)
                    for key, item in value.items()
                    if key != "offset_bars"
                }
            if isinstance(value, list):
                return [without_offsets(item) for item in value]
            return copy.deepcopy(value)

        legacy_plan = without_offsets(current["execution_plan"])
        legacy_plan.pop("plan_sha256", None)
        legacy_plan["engine_version"] = "prompt-rules-v2"
        legacy_plan["plan_sha256"] = sha256_json(legacy_plan)
        legacy_version = {
            **current,
            "plan_sha256": legacy_plan["plan_sha256"],
            "engine_version": "prompt-rules-v2",
            "execution_plan": legacy_plan,
        }

        validated = validate_prompt_backtest_version(legacy_version)

        self.assertEqual(validated["engine_version"], "prompt-rules-v2")
        self.assertEqual(
            PromptStrategyHistoricalSelector(legacy_version).backtest_warmup_sessions,
            38,
        )

    def test_previous_bar_feature_replays_through_selection_and_entry(self):
        version = frozen_version(
            outside_bar_spec(),
            version_id="preset_text-v-offset-test",
        )
        bars = [
            HistoricalBar(
                symbol="sh600000",
                date="2026-08-05",
                open=10,
                high=11,
                low=9,
                close=10,
                volume=1000,
                name="测试股",
            ),
            HistoricalBar(
                symbol="sh600000",
                date="2026-08-06",
                open=10,
                high=12,
                low=8,
                close=10,
                volume=1200,
                name="测试股",
            ),
            HistoricalBar(
                symbol="sh600000",
                date="2026-08-07",
                open=10,
                high=11,
                low=9,
                close=10,
                volume=1100,
                name="测试股",
            ),
        ]
        result = run_selection_backtest(
            {"sh600000": bars},
            PromptStrategyHistoricalSelector(
                version,
                eligible_symbols=("sh600000",),
            ),
            config=SelectionBacktestConfig(
                signal_start_date="2026-08-06",
                signal_end_date="2026-08-07",
                cooldown_sessions=0,
            ),
            position_exit_strategy=PromptStrategyBacktestPolicy(version),
        ).to_dict()

        self.assertEqual(result["portfolio"]["buy_order_count"], 1)
        self.assertEqual(result["portfolio"]["open_position_count"], 1)
        selection_audit = result["signals"][0]["metadata"][
            "prompt_selection_audit"
        ]
        self.assertTrue(replay_rule_evaluation_audit(
            selection_audit,
            plan=version["execution_plan"],
        )["ok"])
        fact_keys = selection_audit["replay_context"]["facts"]
        self.assertTrue(any("~offset=1" in key for key in fact_keys))

    def test_kdj_version_replays_selection_entry_monitor_and_exit_with_audits(self):
        version = frozen_version()
        bars = price_bars([20.0] * 55 + [12.0, 8.0, 5.0, 8.0, 10.0, 12.0, 14.0])
        selector = PromptStrategyHistoricalSelector(
            version,
            eligible_symbols=("sh600000",),
        )
        policy = PromptStrategyBacktestPolicy(version)
        result = run_selection_backtest(
            {"sh600000": bars},
            selector,
            config=SelectionBacktestConfig(
                signal_start_date=bars[50].date,
                signal_end_date=bars[-1].date,
                cooldown_sessions=0,
                slippage_bps=5,
            ),
            position_exit_strategy=policy,
        ).to_dict()

        self.assertEqual(
            result["statistics"]["evaluation_mode"],
            "strategy_portfolio",
        )
        self.assertEqual(result["statistics"]["completed_trade_count"], 1)
        self.assertEqual(result["portfolio"]["open_position_count"], 0)
        self.assertEqual(result["portfolio"]["buy_order_count"], 1)
        self.assertEqual(result["portfolio"]["sell_order_count"], 1)
        trade = result["trades"][0]
        self.assertGreaterEqual(len(trade["prompt_monitor_audits"]), 1)
        self.assertIn(
            "prompt_entry_audit",
            trade["entry_legs"][0]["metadata"],
        )
        self.assertIn(
            "prompt_exit_audit",
            trade["exit_legs"][0]["metadata"],
        )

        manifest = _prompt_backtest_audit_manifest(result, version)
        self.assertEqual(manifest["strategy_version_id"], version["version_id"])
        self.assertTrue(manifest["isolated"])
        self.assertFalse(manifest["production_state_writes"])
        self.assertTrue(manifest["replay_verified"])
        self.assertGreaterEqual(manifest["audit_stage_counts"]["selection"], 1)
        self.assertGreaterEqual(manifest["audit_stage_counts"]["entry"], 1)
        self.assertGreaterEqual(manifest["audit_stage_counts"]["exit"], 1)

        audits = [
            result["signals"][0]["metadata"]["prompt_selection_audit"],
            trade["entry_legs"][0]["metadata"]["prompt_entry_audit"],
            trade["exit_legs"][0]["metadata"]["prompt_exit_audit"],
        ]
        for audit in audits:
            self.assertTrue(replay_rule_evaluation_audit(
                audit,
                plan=version["execution_plan"],
            )["ok"])

    def test_prompt_position_rule_cannot_exceed_system_single_stock_limit(self):
        spec = kdj_spec()
        spec["position"] = {"type": "equity_pct", "value": 50, "allow_add": False}
        version = frozen_version(spec, version_id="preset_text-v-risk-test")
        bars = price_bars([20.0] * 55 + [12.0, 8.0, 5.0, 8.0])
        result = run_selection_backtest(
            {"sh600000": bars},
            PromptStrategyHistoricalSelector(
                version,
                eligible_symbols=("sh600000",),
            ),
            config=SelectionBacktestConfig(
                signal_start_date=bars[50].date,
                signal_end_date=bars[-1].date,
                cooldown_sessions=0,
            ),
            position_exit_strategy=PromptStrategyBacktestPolicy(version),
        ).to_dict()

        self.assertEqual(result["trades"], [])
        self.assertTrue(any(
            signal["status_reason"] == "prompt_single_position_limit"
            for signal in result["signals"]
        ))

    def test_options_and_requests_select_one_immutable_version(self):
        with tempfile.TemporaryDirectory(prefix="niuone-prompt-backtest-") as tmp:
            store = PromptStrategyStore(Path(tmp) / "prompt.db")
            first_draft = store.create_draft("KDJ J值低于0买入，高于15卖出")
            store.save_refinement(
                first_draft["draft_id"],
                kdj_spec(),
                model="test-model",
                provider="test",
            )
            first = store.activate_draft(first_draft["draft_id"])

            second_spec = kdj_spec()
            second_spec["name"] = "KDJ超卖反弹二版"
            second_spec["rules"]["exit"]["right"] = 20
            second_draft = store.create_draft("KDJ J值低于0买入，高于20卖出")
            store.save_refinement(
                second_draft["draft_id"],
                second_spec,
                model="test-model",
                provider="test",
            )
            second = store.activate_draft(second_draft["draft_id"])

            options = backtest_strategy_options(
                today=date(2026, 7, 31),
                prompt_store=store,
            )
            prompt = next(
                item for item in options["strategies"]
                if item["id"] == "preset_text"
            )
            self.assertTrue(prompt["supported"])
            self.assertEqual(
                prompt["backtest_protocol_version"],
                PROMPT_BACKTEST_PROTOCOL_VERSION,
            )
            self.assertEqual(len(prompt["prompt_versions"]), 2)

            base_request = {
                "strategy_id": "preset_text",
                "start_date": "2026-01-01",
                "end_date": "2026-02-01",
                "adjustment": "qfq",
                "source": "tencent",
            }
            first_request = normalize_backtest_request(
                {
                    **base_request,
                    "prompt_strategy_version_id": first["version_id"],
                },
                prompt_store=store,
            )
            second_request = normalize_backtest_request(
                {
                    **base_request,
                    "prompt_strategy_version_id": second["version_id"],
                },
                prompt_store=store,
            )

            first_snapshot = first_request["prompt_strategy_version"]
            second_snapshot = second_request["prompt_strategy_version"]
            self.assertNotEqual(
                first_snapshot["version_id"],
                second_snapshot["version_id"],
            )
            self.assertNotEqual(
                first_snapshot["plan_sha256"],
                second_snapshot["plan_sha256"],
            )
            self.assertNotIn("raw_prompt", first_snapshot)
            self.assertEqual(
                first_request["protocol_version"],
                PROMPT_BACKTEST_PROTOCOL_VERSION,
            )

    def test_admin_runner_routes_prompt_request_to_dedicated_engine(self):
        version = frozen_version()
        request = {
            "strategy": {
                "id": "preset_text",
                "label": "预设文字策略 · KDJ超卖反弹",
                "strategy_ids": ["preset_text"],
            },
            "prompt_strategy_version": version,
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
            "adjustment": "qfq",
            "sources": ("tencent",),
            "risk_profile": "balanced",
            "protocol_version": PROMPT_BACKTEST_PROTOCOL_VERSION,
        }
        run = Mock()
        run.to_dict.return_value = {
            "selection": {
                "signals": [],
                "trades": [],
                "statistics": {},
            },
            "warnings": [],
        }
        run.data.series = {}
        run.data.failures = {}
        universe = {
            "reference_symbols": ("sh600000",),
            "eligible_symbols": ("sh600000",),
            "name_by_symbol": {"sh600000": "测试股"},
            "metadata": {"mode": "strategy_auto"},
        }
        with tempfile.TemporaryDirectory(prefix="niuone-prompt-cache-") as tmp:
            with patch(
                "app.backtesting.tasks.run_historical_selection_backtest",
                return_value=run,
            ) as historical:
                payload = run_strategy_backtest_request(
                    request,
                    universe_loader=lambda _strategy: universe,
                    replay_cache_dir=Path(tmp),
                )

        call = historical.call_args
        self.assertIsInstance(
            call.args[3],
            PromptStrategyHistoricalSelector,
        )
        self.assertIsInstance(
            call.kwargs["position_exit_strategy"],
            PromptStrategyBacktestPolicy,
        )
        self.assertEqual(call.kwargs["warmup_calendar_days"], 730)
        self.assertEqual(call.kwargs["selection_config"].cooldown_sessions, 0)
        self.assertIn(
            version["version_id"],
            call.kwargs["replay_cache_identity"]["selector_id"],
        )
        self.assertIn(
            version["plan_sha256"],
            call.kwargs["replay_cache_identity"]["selector_id"],
        )
        self.assertEqual(
            payload["protocol"]["strategy_version_id"],
            version["version_id"],
        )
        self.assertTrue(payload["execution_assumptions"]["isolated_portfolio"])
        self.assertFalse(payload["prompt_backtest"]["production_state_writes"])


if __name__ == "__main__":
    unittest.main()
