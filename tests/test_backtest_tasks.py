from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from app.backtesting.tasks import (
    _selector_for_request,
    _missing_requested_module,
    _worker_error_message,
    BACKTEST_STATE_SCHEMA_VERSION,
    BacktestTaskError,
    BacktestTaskManager,
    NIUONE_BACKTEST_PROTOCOL_VERSION,
    backtest_strategy_options,
    load_strategy_universe,
    normalize_backtest_request,
    run_strategy_backtest_request,
)


class BacktestTaskTests(unittest.TestCase):
    def test_worker_error_message_removes_duplicate_task_prefix(self):
        self.assertEqual(
            _worker_error_message(
                {
                    "error_type": "BacktestTaskError",
                    "error": "BacktestTaskError: 候选范围构建失败",
                },
                1,
            ),
            "候选范围构建失败",
        )
        self.assertEqual(
            _worker_error_message(
                {
                    "error_type": "ModuleNotFoundError",
                    "error": "ModuleNotFoundError: No module named 'core'",
                },
                1,
            ),
            "ModuleNotFoundError: No module named 'core'",
        )

    def test_compatibility_fallback_does_not_hide_internal_import_errors(self):
        missing_app = ModuleNotFoundError("missing app", name="app")
        missing_dependency = ModuleNotFoundError("missing core", name="core")

        self.assertTrue(_missing_requested_module(
            missing_app,
            "app.screening.multi_strategy",
        ))
        self.assertFalse(_missing_requested_module(
            missing_dependency,
            "app.screening.multi_strategy",
        ))

    def test_niuone_backtest_caps_reversal_without_capping_mature_paths(self):
        eligible = tuple(f"sh{600000 + index:06d}" for index in range(8))
        selector = _selector_for_request(
            {
                "strategy": {
                    "id": "niuone",
                    "strategy_ids": ["niu_emerging", "niu_reversal_probe"],
                },
            },
            eligible_symbols=eligible,
        )

        self.assertEqual(selector.max_signals_per_session, len(eligible))
        self.assertEqual(
            dict(selector.max_signals_per_strategy_per_session),
            {"niu_reversal_probe": 2},
        )
        self.assertEqual(selector.eligible_symbols, frozenset(eligible))

    def test_options_expose_each_suite_and_include_daily_v_reversal(self):
        payload = backtest_strategy_options(today=date(2026, 7, 31))
        by_id = {item["id"]: item for item in payload["strategies"]}

        self.assertEqual(payload["defaults"]["start_date"], "2026-04-27")
        self.assertEqual(payload["defaults"]["end_date"], "2026-06-26")
        self.assertEqual(payload["defaults"]["universe_mode"], "strategy_auto")
        self.assertEqual(payload["defaults"]["risk_profile"], "aggressive")
        self.assertNotIn("niuone_risk_profiles", payload)
        self.assertTrue(by_id["niuone"]["supported"])
        self.assertEqual(
            by_id["niuone"]["backtest_protocol_version"],
            NIUONE_BACKTEST_PROTOCOL_VERSION,
        )
        self.assertIn("niu_reversal_probe", by_id["niuone"]["strategy_ids"])
        self.assertEqual(by_id["niuone"]["excluded_strategy_ids"], [])
        self.assertFalse(by_id["preset_text"]["supported"])

    def test_request_validation_uses_automatic_universe_and_data_sources(self):
        request = normalize_backtest_request({
            "strategy_id": "base",
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
            "adjustment": "qfq",
            "source": "auto",
        })

        self.assertNotIn("symbols", request)
        self.assertEqual(request["sources"], ("eastmoney", "tencent"))
        self.assertEqual(request["risk_profile"], "balanced")
        unadjusted = normalize_backtest_request({
            "strategy_id": "base",
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
            "adjustment": "none",
            "source": "auto",
        })
        self.assertEqual(
            unadjusted["sources"],
            ("eastmoney", "tencent", "sina"),
        )
        with self.assertRaisesRegex(BacktestTaskError, "仅支持不复权"):
            normalize_backtest_request({
                **request,
                "strategy_id": "base",
                "source": "sina",
            })
        fixed_aggressive = normalize_backtest_request({
            "strategy_id": "niuone",
            "start_date": "2026-06-01",
            "end_date": "2026-06-30",
            "adjustment": "qfq",
            "source": "tencent",
            "risk_profile": "balanced",
        })
        self.assertEqual(fixed_aggressive["risk_profile"], "aggressive")
        self.assertEqual(
            fixed_aggressive["protocol_version"],
            NIUONE_BACKTEST_PROTOCOL_VERSION,
        )
        stale_client = normalize_backtest_request({
            **fixed_aggressive,
            "strategy_id": "niuone",
            "source": "tencent",
            "risk_profile": "unknown",
        })
        self.assertEqual(stale_client["risk_profile"], "aggressive")

    def test_niuone_automatic_universe_separates_reference_and_eligible_symbols(self):
        listed = [
            *((f"{600000 + index:06d}", f"沪市{index}") for index in range(60)),
            *((f"{300001 + index:06d}", f"创业板{index}") for index in range(60)),
        ]
        universe = load_strategy_universe(
            {"id": "niuone"},
            pool_loader=lambda _scope: listed,
            configured_loader=lambda: ("main_board",),
            friendly_loader=lambda scope: "/".join(scope),
        )

        self.assertEqual(len(universe["reference_symbols"]), 120)
        self.assertEqual(len(universe["eligible_symbols"]), 60)
        self.assertTrue(all(symbol.startswith("sh6") for symbol in universe["eligible_symbols"]))
        self.assertEqual(universe["metadata"]["mode"], "strategy_auto")
        self.assertEqual(
            universe["metadata"]["source"],
            "current_a_share_listing_interfaces",
        )

    def test_strategy_runner_fetches_exact_range_without_daily_kline_cache(self):
        request = normalize_backtest_request({
            "strategy_id": "base",
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
            "adjustment": "qfq",
            "source": "auto",
        })
        run = Mock()
        run.to_dict.return_value = {"selection": {}, "warnings": []}
        series = Mock()
        series.symbol = "sh600000"
        series.source = "eastmoney"
        series.adjustment = "qfq"
        series.bars = ({"date": "2026-01-01"}, {"date": "2026-02-01"})
        series.attempts = ()
        run.data.series = {"sh600000": series}
        run.data.failures = {}
        universe = {
            "reference_symbols": ("sh600000",),
            "eligible_symbols": ("sh600000",),
            "name_by_symbol": {"sh600000": "浦发银行"},
            "metadata": {
                "mode": "strategy_auto",
                "source": "current_a_share_listing_interfaces",
            },
        }

        with patch(
            "app.backtesting.tasks.run_historical_selection_backtest",
            return_value=run,
        ) as historical:
            payload = run_strategy_backtest_request(
                request,
                universe_loader=lambda _strategy: universe,
            )

        call = historical.call_args
        self.assertNotIn("source_fetchers", call.kwargs)
        self.assertIsNone(call.kwargs["position_exit_strategy"])
        self.assertEqual(call.kwargs["fetch_config"].sources, ("eastmoney", "tencent"))
        self.assertEqual(call.kwargs["fetch_config"].max_workers, 16)
        self.assertEqual(
            payload["universe"]["source"],
            "current_a_share_listing_interfaces",
        )
        self.assertEqual(payload["data"]["series"]["sh600000"]["name"], "浦发银行")
        self.assertEqual(payload["data"]["source_counts"], {"eastmoney": 1})

    def test_niuone_runner_reports_the_actual_classification_fallback(self):
        request = normalize_backtest_request({
            "strategy_id": "niuone",
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
            "adjustment": "qfq",
            "source": "auto",
        })
        run = Mock()
        run.to_dict.return_value = {
            "selection": {},
            "warnings": [],
            "industry_quality": {
                "source": "iwencai_current_industry_concept",
            },
        }
        run.data.series = {}
        run.data.failures = {}
        universe = {
            "reference_symbols": ("sh600000",),
            "eligible_symbols": ("sh600000",),
            "name_by_symbol": {"sh600000": "浦发银行"},
            "metadata": {"mode": "strategy_auto"},
        }

        with patch(
            "app.backtesting.tasks.run_historical_selection_backtest",
            return_value=run,
        ):
            payload = run_strategy_backtest_request(
                request,
                universe_loader=lambda _strategy: universe,
            )

        self.assertEqual(payload["universe"]["classification_provider"], "iwencai")
        self.assertEqual(payload["universe"]["classification_basis"], "iwencai_concept")

    def test_niuone_runner_uses_trade_lifecycle_exit_strategy_without_cooldown(self):
        request = normalize_backtest_request({
            "strategy_id": "niuone",
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
            "adjustment": "qfq",
            "source": "auto",
            "risk_profile": "aggressive",
        })
        run = Mock()
        run.to_dict.return_value = {"selection": {}, "warnings": []}
        series = Mock()
        series.symbol = "sh600000"
        series.source = "tencent"
        series.adjustment = "qfq"
        series.bars = ({"date": "2026-01-01"},)
        series.attempts = ()
        run.data.series = {"sh600000": series}
        run.data.failures = {}
        universe = {
            "reference_symbols": ("sh600000",),
            "eligible_symbols": ("sh600000",),
            "name_by_symbol": {"sh600000": "浦发银行"},
            "metadata": {"mode": "strategy_auto", "source": "test"},
        }

        with patch(
            "app.backtesting.tasks.run_historical_selection_backtest",
            return_value=run,
        ) as historical:
            payload = run_strategy_backtest_request(
                request,
                universe_loader=lambda _strategy: universe,
            )

        call = historical.call_args
        self.assertEqual(call.kwargs["selection_config"].cooldown_sessions, 0)
        self.assertEqual(
            type(call.kwargs["position_exit_strategy"]).__name__,
            "NiuOneStrategyBacktestPolicy",
        )
        self.assertTrue(
            call.kwargs["position_exit_strategy"].markup_upgrade_only
        )
        self.assertTrue(
            call.kwargs["position_exit_strategy"].markup_rebalance_enabled
        )
        self.assertEqual(
            call.kwargs["position_exit_strategy"].max_new_positions_per_session,
            3,
        )
        self.assertEqual(
            call.kwargs["position_exit_strategy"].max_open_positions,
            6,
        )
        self.assertAlmostEqual(
            call.kwargs["position_exit_strategy"].risk_budget_scale,
            1.35,
        )
        self.assertAlmostEqual(
            call.kwargs["position_exit_strategy"]
            .lifecycle_climax_partial_ratio,
            1.0 / 3.0,
        )
        self.assertIsNotNone(call.kwargs["classification_loader"])
        self.assertNotIn("industry_loader", call.kwargs)
        self.assertNotIn("theme_loader", call.kwargs)
        self.assertTrue(any(
            "completed daily low as the trigger" in warning
            for warning in payload["warnings"]
        ))
        self.assertEqual(
            payload["execution_assumptions"],
            {
                "entry_sizing": "maximum_permitted_risk_ceiling",
                "entry_order_scale": 1.0,
                "risk_profile": "aggressive",
                "risk_budget_scale": 1.35,
                "position_budget_scale": 1.15,
                "max_new_positions_per_session": 3,
                "max_open_positions": 6,
                "max_industry_positions": 3,
                "board_lot": 100,
                "model_order_units_replayed": False,
            },
        )
        self.assertEqual(
            payload["protocol"],
            {
                "version": NIUONE_BACKTEST_PROTOCOL_VERSION,
                "risk_profile": "aggressive",
                "risk_profile_label": "进取",
            },
        )
        self.assertTrue(any(
            "maximum-sizing scenario" in warning
            for warning in payload["warnings"]
        ))

    def test_manager_reports_progress_and_completed_result(self):
        entered = threading.Event()
        release = threading.Event()

        def runner(request, *, progress_callback):
            progress_callback(42, "fetching", "正在获取 sh600519")
            entered.set()
            release.wait(timeout=2)
            return {"request": request, "selection": {"statistics": {"signal_count": 1}}}

        manager = BacktestTaskManager(runner=runner)
        try:
            created = manager.start({
                "strategy_id": "base",
                "start_date": "2026-01-01",
                "end_date": "2026-02-01",
                "adjustment": "qfq",
                "source": "eastmoney",
            })
            self.assertTrue(entered.wait(timeout=2))
            running = manager.get(created["id"])
            self.assertEqual(running["status"], "running")
            self.assertEqual(running["phase"], "fetching")
            self.assertEqual(running["progress"], 42)
            latest = manager.latest("base")
            self.assertIsNotNone(latest)
            self.assertEqual(latest["id"], created["id"])
            self.assertIsNone(manager.latest("niuone"))
            with self.assertRaisesRegex(BacktestTaskError, "已有回测任务"):
                manager.start({
                    "strategy_id": "base",
                    "start_date": "2026-02-02",
                    "end_date": "2026-03-01",
                    "adjustment": "qfq",
                    "source": "eastmoney",
                })
            release.set()
            for _ in range(100):
                completed = manager.get(created["id"])
                if completed and completed["status"] == "succeeded":
                    break
                time.sleep(0.01)
            self.assertEqual(completed["status"], "succeeded")
            self.assertEqual(completed["progress"], 100)
            self.assertEqual(completed["result"]["selection"]["statistics"]["signal_count"], 1)
        finally:
            release.set()
            manager.shutdown()

    def test_manager_persists_structured_day_timing_details(self):
        entered = threading.Event()
        release = threading.Event()

        def runner(_request, *, progress_callback):
            progress_callback.report(
                76,
                "scoring",
                "正在执行策略评分",
                {
                    "trading_date": "2026-01-12",
                    "day_elapsed_seconds": 2.5,
                    "eta_seconds": 25.0,
                },
            )
            entered.set()
            release.wait(timeout=2)
            return {"ok": True}

        manager = BacktestTaskManager(runner=runner)
        try:
            created = manager.start({
                "strategy_id": "base",
                "start_date": "2026-01-01",
                "end_date": "2026-02-01",
                "adjustment": "qfq",
                "source": "eastmoney",
            })
            self.assertTrue(entered.wait(timeout=1))
            running = manager.get(created["id"])
            self.assertEqual(running["phase"], "scoring")
            self.assertEqual(running["trading_date"], "2026-01-12")
            self.assertEqual(running["day_elapsed_seconds"], 2.5)
            self.assertEqual(running["eta_seconds"], 25.0)
        finally:
            release.set()
            manager.shutdown()

    def test_default_production_runner_uses_isolated_subprocess(self):
        with tempfile.TemporaryDirectory(prefix="niuone-backtest-") as tmp, patch.object(
            BacktestTaskManager,
            "_run_default_subprocess",
            autospec=True,
            return_value={"selection": {"statistics": {"signal_count": 0}}},
        ) as isolated:
            manager = BacktestTaskManager(state_dir=Path(tmp))
            try:
                created = manager.start({
                    "strategy_id": "base",
                    "start_date": "2026-01-01",
                    "end_date": "2026-02-01",
                    "adjustment": "qfq",
                    "source": "eastmoney",
                })
                for _ in range(100):
                    completed = manager.get(created["id"])
                    if completed and completed["status"] == "succeeded":
                        break
                    time.sleep(0.01)
                self.assertEqual(completed["status"], "succeeded")
                isolated.assert_called_once()
                self.assertEqual(isolated.call_args.args[1], created["id"])
            finally:
                manager.shutdown()

    def test_manager_persists_progress_restores_result_and_resets_on_next_run(self):
        entered = threading.Event()
        release = threading.Event()

        def first_runner(request, *, progress_callback):
            progress_callback(37, "fetching", "正在获取历史行情")
            entered.set()
            release.wait(timeout=2)
            return {"marker": "first", "request": request}

        with tempfile.TemporaryDirectory(prefix="niuone-backtest-") as tmp:
            state_dir = Path(tmp)
            manager = BacktestTaskManager(runner=first_runner, state_dir=state_dir)
            try:
                first = manager.start({
                    "strategy_id": "base",
                    "start_date": "2026-01-01",
                    "end_date": "2026-02-01",
                    "adjustment": "qfq",
                    "source": "eastmoney",
                })
                self.assertTrue(entered.wait(timeout=2))
                persisted = json.loads(
                    (state_dir / "base.json").read_text(encoding="utf-8")
                )["job"]
                self.assertEqual(persisted["id"], first["id"])
                self.assertEqual(persisted["progress"], 37)
                self.assertIsNone(persisted["result"])
                release.set()
                for _ in range(100):
                    completed = manager.latest("base")
                    if completed and completed["status"] == "succeeded":
                        break
                    time.sleep(0.01)
                self.assertEqual(completed["result"]["marker"], "first")
            finally:
                release.set()
                manager.shutdown()

            second_entered = threading.Event()
            second_release = threading.Event()

            def second_runner(request, *, progress_callback):
                second_entered.set()
                second_release.wait(timeout=2)
                return {"marker": "second", "request": request}

            restored = BacktestTaskManager(runner=second_runner, state_dir=state_dir)
            try:
                restored_first = restored.latest("base")
                self.assertIsNotNone(restored_first)
                self.assertEqual(restored_first["id"], first["id"])
                self.assertEqual(restored_first["result"]["marker"], "first")

                second = restored.start({
                    "strategy_id": "base",
                    "start_date": "2026-02-02",
                    "end_date": "2026-03-01",
                    "adjustment": "qfq",
                    "source": "eastmoney",
                })
                self.assertTrue(second_entered.wait(timeout=2))
                self.assertNotEqual(second["id"], first["id"])
                self.assertIsNone(restored.get(first["id"]))
                reset = json.loads(
                    (state_dir / "base.json").read_text(encoding="utf-8")
                )["job"]
                self.assertEqual(reset["id"], second["id"])
                self.assertIsNone(reset["result"])
            finally:
                second_release.set()
                restored.shutdown()

    def test_manager_ignores_result_from_an_old_backtest_protocol(self):
        with tempfile.TemporaryDirectory(prefix="niuone-backtest-") as tmp:
            state_dir = Path(tmp)
            (state_dir / "niuone.json").write_text(
                json.dumps({
                    "schema_version": BACKTEST_STATE_SCHEMA_VERSION,
                    "job": {
                        "id": "stale-niuone-result",
                        "status": "succeeded",
                        "phase": "completed",
                        "strategy": {"id": "niuone"},
                        "request": {
                            "start_date": "2026-06-01",
                            "end_date": "2026-06-30",
                            "adjustment": "qfq",
                            "sources": ["tencent"],
                            "risk_profile": "balanced",
                            "protocol_version": "niuone-backtest-v21",
                        },
                        "result": {
                            "protocol": {"version": "niuone-backtest-v21"},
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
                self.assertIsNone(manager.latest("niuone"))
            finally:
                manager.shutdown()

    def test_manager_ignores_removed_balanced_niuone_result(self):
        with tempfile.TemporaryDirectory(prefix="niuone-backtest-") as tmp:
            state_dir = Path(tmp)
            (state_dir / "niuone.json").write_text(
                json.dumps({
                    "schema_version": BACKTEST_STATE_SCHEMA_VERSION,
                    "job": {
                        "id": "removed-balanced-result",
                        "status": "succeeded",
                        "phase": "completed",
                        "strategy": {"id": "niuone"},
                        "request": {
                            "start_date": "2026-06-01",
                            "end_date": "2026-06-30",
                            "adjustment": "qfq",
                            "sources": ["tencent"],
                            "risk_profile": "balanced",
                            "protocol_version": NIUONE_BACKTEST_PROTOCOL_VERSION,
                        },
                        "result": {
                            "protocol": {
                                "version": NIUONE_BACKTEST_PROTOCOL_VERSION,
                                "risk_profile": "balanced",
                            },
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
                self.assertIsNone(manager.latest("niuone"))
            finally:
                manager.shutdown()

    def test_manager_requeues_a_persisted_interrupted_job_after_restart(self):
        seed_entered = threading.Event()
        seed_release = threading.Event()

        def seed_runner(request, *, progress_callback):
            progress_callback(23, "fetching", "正在获取历史行情")
            seed_entered.set()
            seed_release.wait(timeout=2)
            return {"marker": "before-restart", "request": request}

        with tempfile.TemporaryDirectory(prefix="niuone-backtest-") as tmp:
            state_dir = Path(tmp)
            state_file = state_dir / "base.json"
            seed = BacktestTaskManager(runner=seed_runner, state_dir=state_dir)
            try:
                created = seed.start({
                    "strategy_id": "base",
                    "start_date": "2026-01-01",
                    "end_date": "2026-02-01",
                    "adjustment": "qfq",
                    "source": "eastmoney",
                })
                self.assertTrue(seed_entered.wait(timeout=2))
                interrupted_snapshot = json.loads(state_file.read_text(encoding="utf-8"))
                self.assertEqual(interrupted_snapshot["job"]["status"], "running")
            finally:
                seed_release.set()
                seed.shutdown()

            state_file.write_text(
                json.dumps(interrupted_snapshot, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            resumed_entered = threading.Event()
            resumed_release = threading.Event()

            def resumed_runner(request, *, progress_callback):
                resumed_entered.set()
                resumed_release.wait(timeout=2)
                return {"marker": "after-restart", "request": request}

            resumed = BacktestTaskManager(runner=resumed_runner, state_dir=state_dir)
            try:
                self.assertTrue(resumed_entered.wait(timeout=2))
                running = resumed.latest("base")
                self.assertEqual(running["id"], created["id"])
                self.assertEqual(running["status"], "running")
                self.assertIsNone(running["result"])
                resumed_release.set()
                for _ in range(100):
                    completed = resumed.latest("base")
                    if completed and completed["status"] == "succeeded":
                        break
                    time.sleep(0.01)
                self.assertEqual(completed["result"]["marker"], "after-restart")
            finally:
                resumed_release.set()
                resumed.shutdown()

    def test_manager_cancels_running_job_and_restores_terminal_state(self):
        entered = threading.Event()
        release = threading.Event()

        def runner(request, *, progress_callback):
            progress_callback(28, "evaluating", "正在回放选股信号")
            entered.set()
            release.wait(timeout=2)
            progress_callback(29, "evaluating", "不应在终止后继续")
            return {"unexpected": True, "request": request}

        with tempfile.TemporaryDirectory(prefix="niuone-backtest-") as tmp:
            state_dir = Path(tmp)
            manager = BacktestTaskManager(runner=runner, state_dir=state_dir)
            try:
                created = manager.start({
                    "strategy_id": "base",
                    "start_date": "2026-01-01",
                    "end_date": "2026-02-01",
                    "adjustment": "qfq",
                    "source": "eastmoney",
                })
                self.assertTrue(entered.wait(timeout=2))
                cancelled = manager.cancel(created["id"])
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertEqual(cancelled["phase"], "cancelled")
                self.assertEqual(cancelled["progress"], 28)
                self.assertIsNone(cancelled["result"])
                persisted = json.loads(
                    (state_dir / "base.json").read_text(encoding="utf-8")
                )["job"]
                self.assertEqual(persisted["status"], "cancelled")
            finally:
                release.set()
                manager.shutdown()

            resumed_runner_called = threading.Event()

            def resumed_runner(request, *, progress_callback):
                resumed_runner_called.set()
                return {"unexpected": True, "request": request}

            restored = BacktestTaskManager(
                runner=resumed_runner,
                state_dir=state_dir,
            )
            try:
                latest = restored.latest("base")
                self.assertEqual(latest["status"], "cancelled")
                self.assertFalse(resumed_runner_called.wait(timeout=0.05))
            finally:
                restored.shutdown()

    def test_cancelled_job_releases_worker_for_next_run(self):
        first_started = threading.Event()
        second_started = threading.Event()

        def runner(request, *, progress_callback):
            if request["start_date"] == "2026-01-01":
                first_started.set()
                cancellation_check = progress_callback.check_cancelled
                while True:
                    cancellation_check()
                    time.sleep(0.01)
            second_started.set()
            return {"marker": "second"}

        manager = BacktestTaskManager(runner=runner)
        try:
            first = manager.start({
                "strategy_id": "base",
                "start_date": "2026-01-01",
                "end_date": "2026-02-01",
                "adjustment": "qfq",
                "source": "eastmoney",
            })
            self.assertTrue(first_started.wait(timeout=1))
            manager.cancel(first["id"])
            second = manager.start({
                "strategy_id": "base",
                "start_date": "2026-02-02",
                "end_date": "2026-03-01",
                "adjustment": "qfq",
                "source": "eastmoney",
            })
            self.assertEqual(second["status"], "queued")
            self.assertTrue(second_started.wait(timeout=0.5))
            for _ in range(100):
                completed = manager.get(second["id"])
                if completed and completed["status"] == "succeeded":
                    break
                time.sleep(0.01)
            self.assertEqual(completed["status"], "succeeded")
        finally:
            manager.shutdown()


if __name__ == "__main__":
    unittest.main()
