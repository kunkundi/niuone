from __future__ import annotations

import os
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.backtesting.historical_data import HistoricalDataError, HistoricalFetchConfig
from app.backtesting.replay_cache import ReplayTapeCache
from app.backtesting.selection import (
    SelectionBacktestConfig,
    SelectionCostModel,
    SelectionSignal,
)
from app.backtesting.service import (
    CurrentClassificationError,
    load_current_classification_snapshot,
    run_historical_selection_backtest,
)
from app.market_data.eastmoney_boards import EastmoneyBoardSnapshot, EastmoneyStockBoard
from app.market_data.iwencai_boards import IwencaiBoardSnapshot, IwencaiStockBoard


class HistoricalSelectionBacktestServiceTests(unittest.TestCase):
    def test_current_classification_prefers_a_stale_eastmoney_snapshot(self):
        expected = EastmoneyBoardSnapshot(
            captured_at="2026-08-05 15:00:00",
            as_of_date="2026-08-05",
            stocks={
                "600519": EastmoneyStockBoard(code="600519", industry="白酒")
            },
            stale=True,
        )

        snapshot = load_current_classification_snapshot(
            {"600519"},
            env={
                "IWENCAI_ENABLED": "1",
                "IWENCAI_BASE_URL": "https://openapi.iwencai.com",
                "IWENCAI_API_KEY": "test-key",
            },
            eastmoney_loader=lambda **_kwargs: expected,
            iwencai_loader=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("iWencai must not replace a validated Eastmoney snapshot")
            ),
        )

        self.assertIs(snapshot, expected)

    def test_current_classification_falls_back_to_configured_iwencai(self):
        expected = IwencaiBoardSnapshot(
            captured_at="2026-08-06 16:00:00",
            as_of_date="2026-08-06",
            stocks={
                "600519": IwencaiStockBoard(
                    code="600519",
                    industry="白酒",
                    concepts=("超级品牌",),
                )
            },
        )
        calls = []

        def eastmoney_loader(**_kwargs):
            raise OSError("eastmoney unavailable")

        def iwencai_loader(**kwargs):
            calls.append(kwargs["cache_path"].name)
            return expected

        snapshot = load_current_classification_snapshot(
            {"600519"},
            env={
                "IWENCAI_ENABLED": "1",
                "IWENCAI_BASE_URL": "https://openapi.iwencai.com",
                "IWENCAI_API_KEY": "test-key",
            },
            eastmoney_loader=eastmoney_loader,
            iwencai_loader=iwencai_loader,
        )

        self.assertIs(snapshot, expected)
        self.assertEqual(calls, ["iwencai_stock_boards.json"])

    def test_current_classification_passes_explicit_env_to_default_iwencai_fetch(self):
        expected = IwencaiBoardSnapshot(
            captured_at="2026-08-06 16:00:00",
            as_of_date="2026-08-06",
            stocks={
                "600519": IwencaiStockBoard(
                    code="600519",
                    industry="白酒",
                    concepts=("超级品牌",),
                )
            },
        )
        explicit_env = {
            "IWENCAI_ENABLED": "1",
            "IWENCAI_BASE_URL": "https://openapi.iwencai.com",
            "IWENCAI_API_KEY": "test-key",
        }
        observed_configs = []

        def fetcher(*, config):
            observed_configs.append(config)
            return expected

        with tempfile.TemporaryDirectory(
            prefix="niuone-current-classification-"
        ) as directory, patch(
            "app.core.paths.get_dashboard_home",
            return_value=Path(directory),
        ), patch(
            "app.market_data.iwencai_boards.fetch_iwencai_board_snapshot",
            side_effect=fetcher,
        ), patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            snapshot = load_current_classification_snapshot(
                {"600519"},
                env=explicit_env,
                eastmoney_loader=lambda **_kwargs: (_ for _ in ()).throw(
                    OSError("eastmoney unavailable")
                ),
            )

        self.assertIs(snapshot, expected)
        self.assertEqual(len(observed_configs), 1)
        self.assertTrue(observed_configs[0].enabled)
        self.assertEqual(observed_configs[0].api_key, "test-key")

    def test_current_classification_does_not_hide_missing_fallback_configuration(self):
        with self.assertRaisesRegex(CurrentClassificationError, "问财数据源"):
            load_current_classification_snapshot(
                {"600519"},
                env={"IWENCAI_ENABLED": "0"},
                eastmoney_loader=lambda **_kwargs: (_ for _ in ()).throw(
                    OSError("eastmoney unavailable")
                ),
            )

    def test_combined_classification_loader_is_called_once_for_both_maps(self):
        rows = [
            {"date": "2026-01-05", "open": 10, "high": 10, "low": 10,
             "close": 10, "volume": 100},
            {"date": "2026-01-06", "open": 10, "high": 10, "low": 10,
             "close": 10, "volume": 100},
        ]
        calls = []
        snapshot = IwencaiBoardSnapshot(
            captured_at="2026-08-06 16:00:00",
            as_of_date="2026-08-06",
            stocks={
                "600519": IwencaiStockBoard(
                    code="600519",
                    industry="白酒",
                    concepts=("超级品牌",),
                )
            },
        )

        def fetcher(_symbol, _start, _end, _adjustment, _timeout):
            return rows

        def classification_loader(symbols):
            calls.append(set(symbols))
            return snapshot

        observed = []

        def selector(context):
            bar = context.bars["sh600519"]
            observed.append((bar.industry, tuple(bar.extras.get("themes") or ())))
            return []

        result = run_historical_selection_backtest(
            ["600519"],
            "2026-01-05",
            "2026-01-05",
            selector,
            warmup_calendar_days=0,
            forward_calendar_days=1,
            fetch_config=HistoricalFetchConfig(
                sources=("tencent",), max_attempts_per_source=1,
            ),
            source_fetchers={"tencent": fetcher},
            classification_loader=classification_loader,
        )

        self.assertEqual(calls, [{"600519"}])
        self.assertEqual(observed, [("白酒", ("超级品牌",))])
        self.assertEqual(result.industry_quality.mode, "iwencai_current")
        self.assertEqual(result.industry_quality.source, "iwencai_current_industry_concept")
        self.assertIn(
            "current classification fallback used: iwencai_current_industry_concept",
            result.warnings,
        )

    def test_reuses_cached_selection_tape_without_calling_selector_again(self):
        rows = [
            {"date": f"2026-01-0{day}", "open": 10, "high": 11,
             "low": 9, "close": 10 + day / 10, "volume": 100}
            for day in range(1, 6)
        ]

        def fetcher(_symbol, _start, _end, _adjustment, _timeout):
            return rows

        selector_calls = 0

        def selector(context):
            nonlocal selector_calls
            selector_calls += 1
            return (
                [SelectionSignal("sh600519", strategy_id="niu_leader")]
                if context.date == "2026-01-03" else []
            )

        identity = {
            "protocol_version": "cache-test-v1",
            "selector_id": "test",
            "strategy_ids": ("niu_leader",),
            "sources": ("eastmoney",),
            "adjustment": "qfq",
            "stock_pool": ("sh600519",),
        }
        common = {
            "warmup_calendar_days": 2,
            "forward_calendar_days": 2,
            "fetch_config": HistoricalFetchConfig(
                sources=("eastmoney",), max_attempts_per_source=1,
            ),
            "selection_config": SelectionBacktestConfig(
                holding_sessions=(1,), slippage_bps=0,
                price_limit_resolver=None,
            ),
            "source_fetchers": {"eastmoney": fetcher},
            "industry_by_symbol": {"600519": "白酒"},
            "replay_cache_identity": identity,
        }
        with tempfile.TemporaryDirectory(prefix="niuone-replay-cache-") as tmp:
            cache = ReplayTapeCache(Path(tmp))
            first = run_historical_selection_backtest(
                ["600519"], "2026-01-03", "2026-01-03", selector,
                replay_cache=cache,
                **common,
            )
            first_call_count = selector_calls

            def selector_must_not_run(_context):
                raise AssertionError("cached selector should not run")

            second = run_historical_selection_backtest(
                ["600519"], "2026-01-03", "2026-01-03",
                selector_must_not_run,
                replay_cache=cache,
                **common,
            )

        self.assertGreater(first_call_count, 0)
        self.assertEqual(selector_calls, first_call_count)
        self.assertFalse(first.replay_cache["hit"])
        self.assertTrue(second.replay_cache["hit"])
        self.assertEqual(first.selection.to_dict(), second.selection.to_dict())

    def test_forwards_server_cancellation_check_to_history_fetch(self):
        class FetchCancelled(RuntimeError):
            pass

        def progress(_percent, _phase, _message):
            pass

        def cancellation_check():
            raise FetchCancelled("cancelled")

        progress.check_cancelled = cancellation_check
        with patch(
            "app.backtesting.service.fetch_historical_data",
            side_effect=FetchCancelled("cancelled"),
        ) as fetch:
            with self.assertRaises(FetchCancelled):
                run_historical_selection_backtest(
                    ["600519"],
                    "2026-01-03",
                    "2026-01-03",
                    lambda _context: [],
                    progress_callback=progress,
                )
        self.assertIs(fetch.call_args.kwargs["cancellation_check"], cancellation_check)

    def test_fetches_warmup_and_forward_buffers_and_evaluates_signal(self):
        requested = []
        observed = []
        progress = []

        def fetcher(_symbol, start, end, _adjustment, _timeout):
            requested.append((start, end))
            return [
                {"date": "2026-01-01", "open": 9, "high": 9, "low": 9, "close": 9, "volume": 100},
                {"date": "2026-01-02", "open": 9, "high": 10, "low": 9, "close": 10, "volume": 100},
                {"date": "2026-01-03", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100},
                {"date": "2026-01-04", "open": 10, "high": 11, "low": 10, "close": 11, "volume": 100},
                {"date": "2026-01-05", "open": 11, "high": 12, "low": 11, "close": 12, "volume": 100},
            ]

        def selector(context):
            observed.append((context.date, context.bars["sh600519"].industry))
            return (
                [SelectionSignal("sh600519", strategy_id="niu_leader")]
                if context.date == "2026-01-03" else []
            )

        run = run_historical_selection_backtest(
            ["600519"],
            "2026-01-03",
            "2026-01-03",
            selector,
            warmup_calendar_days=2,
            forward_calendar_days=2,
            fetch_config=HistoricalFetchConfig(
                sources=("eastmoney",), max_attempts_per_source=1,
            ),
            selection_config=SelectionBacktestConfig(
                holding_sessions=(1, 2), slippage_bps=0,
                price_limit_resolver=None,
                cost_model=SelectionCostModel(
                    commission_rate=0, transfer_fee_rate=0,
                    sell_stamp_duty_rate=0,
                ),
            ),
            source_fetchers={"eastmoney": fetcher},
            industry_by_symbol={"600519": "白酒"},
            progress_callback=lambda percent, phase, message: progress.append(
                (percent, phase, message)
            ),
        )
        self.assertEqual(requested, [("2026-01-01", "2026-01-05")])
        self.assertEqual(run.data.source_by_symbol["sh600519"], "eastmoney")
        self.assertEqual(run.selection.statistics["evaluated_signal_count"], 1)
        self.assertEqual(run.selection.signals[0]["forward_returns"][2]["net_return_pct"], 20.0)
        self.assertIn(("2026-01-03", "白酒"), observed)
        self.assertEqual(run.warnings, ())
        self.assertEqual(progress[0][1], "preparing")
        self.assertIn("fetching", [item[1] for item in progress])
        annotation_messages = [
            message for _percent, phase, message in progress
            if phase == "annotating"
        ]
        self.assertEqual(
            annotation_messages,
            [
                "正在补充行业信息：已处理 0 只 / 待处理 1 只",
                "正在补充行业信息：已处理 1 只 / 待处理 0 只",
            ],
        )
        normalization_messages = [
            message for _percent, phase, message in progress
            if phase == "normalizing"
        ]
        self.assertEqual(
            normalization_messages,
            [
                "正在整理历史行情：已处理 0 只 / 待处理 1 只",
                "正在整理历史行情：已处理 1 只 / 待处理 0 只",
            ],
        )
        self.assertIn("evaluating", [item[1] for item in progress])
        self.assertEqual(progress[-1][:2], (100, "completed"))

    def test_current_industry_map_is_applied_to_historical_bars(self):
        def fetcher(_symbol, _start, _end, _adjustment, _timeout):
            return [
                {"date": "2026-01-05", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100},
                {"date": "2026-01-06", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100},
            ]

        result = run_historical_selection_backtest(
            ["600519"],
            "2026-01-05",
            "2026-01-05",
            lambda _context: [],
            warmup_calendar_days=0,
            forward_calendar_days=1,
            fetch_config=HistoricalFetchConfig(
                sources=("tencent",), max_attempts_per_source=1,
            ),
            source_fetchers={"tencent": fetcher},
            industry_by_symbol={"600519": "白酒"},
        )
        self.assertEqual(result.industry_quality.mode, "eastmoney_current")
        self.assertEqual(result.selection.statistics["signal_count"], 0)

    def test_fallback_warning_summarizes_large_symbol_sets(self):
        symbols = [f"6000{index:02d}" for index in range(12)]

        def primary(*_args):
            raise TimeoutError("primary unavailable")

        def fallback(_symbol, _start, _end, _adjustment, _timeout):
            return [
                {"date": "2026-01-05", "open": 10, "high": 10,
                 "low": 10, "close": 10, "volume": 100},
                {"date": "2026-01-06", "open": 10, "high": 10,
                 "low": 10, "close": 10, "volume": 100},
            ]

        result = run_historical_selection_backtest(
            symbols,
            "2026-01-05",
            "2026-01-05",
            lambda _context: [],
            warmup_calendar_days=0,
            forward_calendar_days=1,
            fetch_config=HistoricalFetchConfig(
                sources=("eastmoney", "tencent"),
                max_attempts_per_source=1,
                max_workers=1,
                source_circuit_min_samples=100,
            ),
            source_fetchers={"eastmoney": primary, "tencent": fallback},
        )

        warning = next(
            item for item in result.warnings if "fallback source used" in item
        )
        self.assertIn("for 12 symbols", warning)
        self.assertIn("sh600009", warning)
        self.assertNotIn("sh600010", warning)
        self.assertTrue(warning.endswith("(+2 more)"))

    def test_current_eastmoney_themes_are_preserved_on_historical_bars(self):
        observed = []

        def fetcher(_symbol, _start, _end, _adjustment, _timeout):
            return [
                {"date": "2026-01-05", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100},
                {"date": "2026-01-06", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100},
            ]

        def selector(context):
            bar = context.bars["sz000977"]
            observed.append((bar.industry, tuple(bar.extras.get("themes") or ())))
            return []

        result = run_historical_selection_backtest(
            ["000977"],
            "2026-01-05",
            "2026-01-05",
            selector,
            warmup_calendar_days=0,
            forward_calendar_days=1,
            fetch_config=HistoricalFetchConfig(
                sources=("eastmoney",), max_attempts_per_source=1,
            ),
            source_fetchers={"eastmoney": fetcher},
            industry_by_symbol={"000977": "计算机设备"},
            theme_by_symbol={"000977": ("存储芯片", "先进封装")},
        )

        self.assertEqual(observed, [("计算机设备", ("存储芯片", "先进封装"))])
        self.assertEqual(result.industry_quality.matched_bar_count, 2)

    def test_automatic_universe_requires_minimum_historical_coverage(self):
        def fetcher(symbol, _start, _end, _adjustment, _timeout):
            if symbol == "sz000001":
                raise TimeoutError("unavailable")
            return [
                {"date": "2026-01-05", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100},
                {"date": "2026-01-06", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100},
            ]

        with self.assertRaisesRegex(HistoricalDataError, "coverage below minimum"):
            run_historical_selection_backtest(
                ["600519", "000001"],
                "2026-01-05",
                "2026-01-05",
                lambda _context: [],
                warmup_calendar_days=0,
                forward_calendar_days=1,
                minimum_coverage_ratio=0.75,
                fetch_config=HistoricalFetchConfig(
                    sources=("tencent",), strict=False,
                    max_attempts_per_source=1,
                ),
                source_fetchers={"tencent": fetcher},
            )


if __name__ == "__main__":
    unittest.main()
