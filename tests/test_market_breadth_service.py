#!/usr/bin/env python3
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from app.dashboard.apis.market_breadth import (
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_SAMPLE_INTERVAL_SECONDS,
    append_market_breadth_sample,
    build_market_breadth_payload,
    compact_market_breadth_sample,
)
from app.market_data import tencent_market_breadth
from app.compat import niuone_dashboard as dashboard


def quote_record(
    code: str,
    name: str,
    *,
    price: float,
    prev_close: float,
    pct: float,
    high: float,
    upper: float,
    lower: float,
    amount_wan: float = 25_000,
    quote_time: str = "20260722102030",
) -> str:
    fields = [""] * 49
    fields[1] = name
    fields[2] = code
    fields[3] = str(price)
    fields[4] = str(prev_close)
    fields[5] = str(prev_close)
    fields[6] = "123456"
    fields[30] = quote_time
    fields[32] = str(pct)
    fields[33] = str(high)
    fields[34] = str(min(price, prev_close))
    fields[37] = str(amount_wan)
    fields[38] = "2.5"
    fields[47] = str(upper)
    fields[48] = str(lower)
    return f'v_{code}="' + "~".join(fields) + '";'


def sample(generated_at: str, *, red: int = 3000, green: int = 2000) -> dict:
    return {
        "generated_at": generated_at,
        "quote_count": red + green + 100,
        "limit_price_count": red + green,
        "red": red,
        "green": green,
        "flat": 100,
        "limit_up": 42,
        "limit_down": 6,
        "broken_limit": 13,
    }


class TencentMarketBreadthTests(unittest.TestCase):
    def test_parses_quote_limits_and_computes_all_five_series(self):
        body = "".join([
            quote_record(
                "600001", "涨停股", price=11, prev_close=10, pct=10,
                high=11, upper=11, lower=9,
            ),
            quote_record(
                "000001", "跌停股", price=9, prev_close=10, pct=-10,
                high=10, upper=11, lower=9,
            ),
            quote_record(
                "300001", "炸板股", price=11.5, prev_close=10, pct=15,
                high=12, upper=12, lower=8,
            ),
            quote_record(
                "688001", "平盘股", price=10, prev_close=10, pct=0,
                high=10.2, upper=12, lower=8,
            ),
        ])

        rows = tencent_market_breadth.parse_tencent_quote_body(body)
        result = tencent_market_breadth.summarize_market_breadth(rows)

        self.assertEqual(result["quote_count"], 4)
        self.assertEqual(result["limit_price_count"], 4)
        self.assertEqual(result["limit_up"], 1)
        self.assertEqual(result["limit_down"], 1)
        self.assertEqual(result["broken_limit"], 1)
        self.assertEqual(result["red"], 2)
        self.assertEqual(result["green"], 1)
        self.assertEqual(result["flat"], 1)
        self.assertEqual(result["generated_at"], "2026-07-22 10:20:30")
        self.assertEqual(result["turnover_amount_count"], 4)
        self.assertEqual(result["actual_turnover_yi"], 10)
        self.assertNotIn("estimated_turnover_yi", result)
        self.assertEqual(
            result["turnover_actual_source"],
            "腾讯证券沪深A股实时行情（兜底）",
        )
        quote_snapshot = tencent_market_breadth.build_tencent_quote_snapshot(rows)
        self.assertEqual(quote_snapshot["generated_at"], "2026-07-22 10:20:30")
        self.assertEqual(quote_snapshot["quotes"]["sh600001"]["price"], 11)
        self.assertEqual(
            quote_snapshot["quotes"]["sh600001"]["quote_time"],
            "20260722102030",
        )
        self.assertEqual(quote_snapshot["market_snapshot"]["up"], 2)
        self.assertEqual(quote_snapshot["market_snapshot"]["down"], 1)
        self.assertEqual(quote_snapshot["quotes"]["sh600001"]["upper_limit"], 11)
        self.assertEqual(quote_snapshot["quotes"]["sh600001"]["lower_limit"], 9)

    def test_previous_market_turnover_uses_latest_common_prior_trading_day(self):
        bodies = {
            "1.000001": json.dumps({
                "data": {
                    "code": "000001",
                    "klines": [
                        "2026-07-21,1,1,1,1,10,1100000000000",
                        "2026-07-22,1,1,1,1,10,1250000000000",
                        "2026-07-23,1,1,1,1,10,100000000000",
                    ],
                },
            }),
            "0.399001": json.dumps({
                "data": {
                    "code": "399001",
                    "klines": [
                        "2026-07-21,1,1,1,1,10,1200000000000",
                        "2026-07-22,1,1,1,1,10,1350000000000",
                        "2026-07-23,1,1,1,1,10,100000000000",
                    ],
                },
            }),
        }

        result = tencent_market_breadth.fetch_previous_market_turnover(
            datetime(2026, 7, 23).date(),
            downloader=lambda secid, _timeout: bodies[secid],
            monotonic=lambda: 100.0,
        )

        self.assertEqual(result["date"], "2026-07-22")
        self.assertEqual(result["turnover_yi"], 26000)

    def test_turnover_increment_compares_projection_with_previous_close(self):
        snapshot = {
            "schema_version": 3,
            "estimated_turnover_yi": 27_000,
            "actual_turnover_yi": 5_000,
        }

        result = tencent_market_breadth.add_turnover_comparison(snapshot, {
            "date": "2026-07-22",
            "turnover_yi": 26_000,
            "source": "测试日线",
            "source_url": "https://example.test/",
        })

        self.assertEqual(result["previous_turnover_yi"], 26_000)
        self.assertEqual(result["turnover_increment_yi"], 1_000)
        self.assertEqual(result["turnover_comparison_date"], "2026-07-22")

    def test_fetch_retries_a_failed_chunk_and_requires_complete_rows(self):
        calls = []
        consumed = []
        body = quote_record(
            "600001", "浦发测试", price=10.1, prev_close=10, pct=1,
            high=10.2, upper=11, lower=9,
        )

        def downloader(symbols, timeout):
            calls.append((symbols, timeout))
            if len(calls) == 1:
                raise TimeoutError("temporary failure")
            return body

        with (
            patch.object(tencent_market_breadth, "_symbols", return_value=["sh600001"]),
            patch.dict(os.environ, {"DASHBOARD_MARKET_BREADTH_WORKERS": "1"}),
        ):
            result = tencent_market_breadth.fetch_tencent_market_breadth(
                min_rows=1,
                downloader=downloader,
                turnover_estimate_fetcher=lambda _moment, _actual: {
                    "actual_turnover_yi": 2.5,
                    "estimated_turnover_yi": 12,
                },
                previous_turnover_fetcher=lambda _date: {
                    "date": "2026-07-21",
                    "turnover_yi": 10,
                },
                quote_snapshot_consumer=consumed.append,
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["quote_count"], 1)
        self.assertEqual(result["red"], 1)
        self.assertEqual(result["turnover_increment_yi"], 2)
        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0]["quotes"]["sh600001"]["price"], 10.1)
        self.assertNotIn("quotes", result)

    def test_previous_turnover_failure_does_not_hide_valid_tencent_snapshot(self):
        body = quote_record(
            "600001", "浦发测试", price=10.1, prev_close=10, pct=1,
            high=10.2, upper=11, lower=9,
        )

        def failing_reference(_date):
            raise TimeoutError("comparison timeout")

        with patch.object(tencent_market_breadth, "_symbols", return_value=["sh600001"]):
            result = tencent_market_breadth.fetch_tencent_market_breadth(
                min_rows=1,
                downloader=lambda _symbols, _timeout: body,
                previous_turnover_fetcher=failing_reference,
                turnover_estimate_fetcher=lambda _moment, _actual: {
                    "actual_turnover_yi": 2.5,
                    "estimated_turnover_yi": 12,
                },
            )

        self.assertEqual(result["quote_count"], 1)
        self.assertIn("estimated_turnover_yi", result)
        self.assertNotIn("turnover_increment_yi", result)

    def test_latest_complete_close_overrides_projection_profile_comparison(self):
        body = quote_record(
            "600001", "浦发测试", price=10.1, prev_close=10, pct=1,
            high=10.2, upper=11, lower=9,
        )

        with patch.object(tencent_market_breadth, "_symbols", return_value=["sh600001"]):
            result = tencent_market_breadth.fetch_tencent_market_breadth(
                min_rows=1,
                downloader=lambda _symbols, _timeout: body,
                turnover_estimate_fetcher=lambda _moment, _actual: {
                    "actual_turnover_yi": 2.5,
                    "estimated_turnover_yi": 12,
                    "previous_turnover_yi": 20,
                    "turnover_increment_yi": -8,
                    "turnover_comparison_date": "2026-07-21",
                    "turnover_comparison_source": "过期训练样本",
                },
                previous_turnover_fetcher=lambda _date: {
                    "date": "2026-07-22",
                    "turnover_yi": 10,
                    "source": "完整上一交易日",
                    "source_url": "https://example.test/previous-close",
                },
            )

        self.assertEqual(result["previous_turnover_yi"], 10)
        self.assertEqual(result["turnover_increment_yi"], 2)
        self.assertEqual(result["turnover_comparison_date"], "2026-07-22")
        self.assertEqual(result["turnover_comparison_source"], "完整上一交易日")


class MarketBreadthHistoryTests(unittest.TestCase):
    def test_startup_recovery_waits_for_validation_then_runs_once(self):
        class StopEvent:
            def __init__(self):
                self.waits = []

            def is_set(self):
                return False

            def wait(self, timeout):
                self.waits.append(timeout)
                return False

        stop_event = StopEvent()
        runner = Mock(return_value="succeeded")
        with patch.object(
            dashboard,
            "market_breadth_auto_recovery_state",
            side_effect=[
                {"status": "waiting_validation"},
                {"status": "ready"},
            ],
        ), patch.object(dashboard, "invalidate_api_cache") as invalidate:
            dashboard.market_breadth_auto_recovery_loop(
                stop_event=stop_event,
                poll_seconds=0.25,
                runner=runner,
            )

        self.assertEqual(stop_event.waits, [0.25])
        runner.assert_called_once_with()
        invalidate.assert_called_once_with("market_breadth")

    def test_startup_recovery_state_requires_a_safe_same_day_boundary(self):
        with patch.object(
            dashboard,
            "is_a_share_trading_day_for_dashboard",
            return_value=True,
        ), patch.object(
            dashboard,
            "load_market_breadth_samples",
            return_value=[
                sample("2026-08-10 10:43:07"),
                sample("2026-08-10 10:44:07"),
                sample("2026-08-10 10:45:07"),
            ],
        ):
            ready = dashboard.market_breadth_auto_recovery_state(
                datetime(2026, 8, 10, 10, 45, 30),
            )

        self.assertEqual(ready["status"], "ready")
        self.assertEqual(len(ready["validation_targets"]), 3)

    def test_startup_recovery_waits_for_a_post_start_sample_before_planning(self):
        with patch.object(
            dashboard,
            "is_a_share_trading_day_for_dashboard",
            return_value=True,
        ), patch.object(
            dashboard,
            "load_market_breadth_samples",
            return_value=[sample("2026-08-10 10:00:00")],
        ):
            state = dashboard.market_breadth_auto_recovery_state(
                datetime(2026, 8, 10, 11, 0, 0),
                started_at=datetime(2026, 8, 10, 10, 59, 59),
            )

        self.assertEqual(state["status"], "waiting_startup_sample")

    def test_startup_recovery_after_close_detects_a_terminal_gap(self):
        with patch.object(
            dashboard,
            "is_a_share_trading_day_for_dashboard",
            return_value=True,
        ), patch.object(
            dashboard,
            "load_market_breadth_samples",
            return_value=[
                sample("2026-08-10 09:31:00"),
                sample("2026-08-10 09:32:00"),
                sample("2026-08-10 09:33:00"),
            ],
        ):
            state = dashboard.market_breadth_auto_recovery_state(
                datetime(2026, 8, 10, 15, 30, 0),
                started_at=datetime(2026, 8, 10, 15, 29, 0),
            )

        self.assertEqual(state["status"], "ready")
        self.assertEqual(
            state["backfill_targets"][-1],
            datetime(2026, 8, 10, 15, 0),
        )

    def test_startup_recovery_stops_after_bounded_failures(self):
        class StopEvent:
            def is_set(self):
                return False

            def wait(self, _timeout):
                return False

        runner = Mock(return_value="failed")
        with patch.object(
            dashboard,
            "market_breadth_auto_recovery_state",
            return_value={"status": "ready"},
        ):
            dashboard.market_breadth_auto_recovery_loop(
                stop_event=StopEvent(),
                runner=runner,
            )

        self.assertEqual(
            runner.call_count,
            dashboard.MARKET_BREADTH_AUTO_RECOVERY_MAX_ATTEMPTS,
        )

    def test_startup_recovery_process_is_bounded_and_cross_process_leased(self):
        with tempfile.TemporaryDirectory(prefix="niuone-breadth-auto-recovery-") as temp_dir:
            completed = Mock(returncode=0)
            with patch.object(
                dashboard,
                "CRON_STATE_DIR",
                Path(temp_dir),
            ), patch.object(
                dashboard.subprocess,
                "run",
                return_value=completed,
            ) as run:
                outcome = dashboard.run_market_breadth_auto_recovery_process(
                    deadline_seconds=45,
                    process_timeout_seconds=75,
                )

        self.assertEqual(outcome, "succeeded")
        command = run.call_args.args[0]
        self.assertEqual(command[1].endswith("recover_market_breadth_history.py"), True)
        self.assertIn("--write", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 75)
        self.assertEqual(run.call_args.kwargs["stdout"], dashboard.subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["stderr"], dashboard.subprocess.DEVNULL)

    def test_default_thirty_second_history_retains_a_complete_trading_day(self):
        morning = datetime(2026, 7, 22, 9, 30)
        afternoon = datetime(2026, 7, 22, 13, 0)
        moments = [
            morning + timedelta(seconds=30 * index)
            for index in range(240)
        ] + [
            afternoon + timedelta(seconds=30 * index)
            for index in range(240)
        ]
        history = {}
        for moment in moments:
            history = append_market_breadth_sample(
                history,
                sample(moment.strftime("%Y-%m-%d %H:%M:%S")),
            )

        self.assertEqual(DEFAULT_SAMPLE_INTERVAL_SECONDS, 30)
        self.assertEqual(DEFAULT_HISTORY_LIMIT, 600)
        self.assertEqual(history["interval_seconds"], 30)
        self.assertEqual(len(history["samples"]), 480)
        payload = build_market_breadth_payload(
            history["samples"][-1],
            history_samples=history["samples"][:-1],
        )
        self.assertEqual(payload["sampling"]["interval_seconds"], 30)
        self.assertEqual(len(payload["timeline"]), 480)

    def test_background_sampler_allows_a_thirty_second_floor(self):
        class StopAfterFirstWait:
            timeout = None

            def is_set(self):
                return False

            def wait(self, timeout):
                self.timeout = timeout
                return True

        stop_event = StopAfterFirstWait()
        with patch.object(
            dashboard,
            "is_market_breadth_sampling_window",
            return_value=False,
        ), patch.object(
            dashboard.time,
            "monotonic",
            side_effect=[100.0, 100.0],
        ):
            dashboard.market_breadth_sampling_loop(
                stop_event=stop_event,
                poll_seconds=1,
            )

        self.assertEqual(stop_event.timeout, 30.0)

    def test_daily_reset_archives_complete_breadth_curve_at_nine(self):
        original_breadth_file = dashboard.MARKET_BREADTH_HISTORY_FILE
        original_flow_file = dashboard.INDUSTRY_FLOW_HISTORY_FILE
        original_money_file = dashboard.MONEY_FLOW_SNAPSHOT_FILE
        try:
            with tempfile.TemporaryDirectory(prefix="niuone-daily-market-reset-") as temp_dir:
                root = Path(temp_dir)
                dashboard.MARKET_BREADTH_HISTORY_FILE = root / "market_breadth.json"
                dashboard.INDUSTRY_FLOW_HISTORY_FILE = root / "industry_flow.json"
                dashboard.MONEY_FLOW_SNAPSHOT_FILE = root / "money_flow.json"
                dashboard.MARKET_BREADTH_HISTORY_FILE.write_text(json.dumps({
                    "date": "2026-07-22",
                    "samples": [{
                        **sample("2026-07-22 15:00:00"),
                        "actual_turnover_yi": 12_000,
                        "turnover_actual_source": "测试分钟线",
                    }],
                }), encoding="utf-8")
                dashboard.INDUSTRY_FLOW_HISTORY_FILE.write_text(json.dumps({
                    "date": "2026-07-22",
                    "samples": [{
                        "generated_at": "2026-07-22 15:00:00",
                        "items": [{"name": "半导体", "net_flow_yi": 12}],
                    }],
                }), encoding="utf-8")
                dashboard.MONEY_FLOW_SNAPSHOT_FILE.write_text(json.dumps({
                    "generated_at": "2026-07-22 15:00:00",
                    "inflow": [{"name": "半导体", "net_flow_yi": 12}],
                    "outflow": [],
                }), encoding="utf-8")

                dashboard.reset_daily_market_histories(
                    datetime(2026, 7, 23, 0, 1, 0)
                )
                retained_breadth = json.loads(
                    dashboard.MARKET_BREADTH_HISTORY_FILE.read_text(encoding="utf-8")
                )
                retained_flow = json.loads(
                    dashboard.INDUSTRY_FLOW_HISTORY_FILE.read_text(encoding="utf-8")
                )
                retained_money = json.loads(
                    dashboard.MONEY_FLOW_SNAPSHOT_FILE.read_text(encoding="utf-8")
                )
                changed = dashboard.reset_daily_market_histories(
                    datetime(2026, 7, 23, 9, 0, 0)
                )
                repeated = dashboard.reset_daily_market_histories(
                    datetime(2026, 7, 23, 9, 1, 0)
                )

                breadth = json.loads(dashboard.MARKET_BREADTH_HISTORY_FILE.read_text(encoding="utf-8"))
                flow = json.loads(dashboard.INDUSTRY_FLOW_HISTORY_FILE.read_text(encoding="utf-8"))
                flow_recovery = json.loads(
                    dashboard._industry_flow_history_recovery_file().read_text(
                        encoding="utf-8"
                    )
                )
                money = json.loads(dashboard.MONEY_FLOW_SNAPSHOT_FILE.read_text(encoding="utf-8"))
                self.assertEqual(retained_breadth["date"], "2026-07-22")
                self.assertEqual(len(retained_breadth["samples"]), 1)
                self.assertEqual(retained_flow["date"], "2026-07-22")
                self.assertEqual(len(retained_flow["samples"]), 1)
                self.assertEqual(retained_money["generated_at"], "2026-07-22 15:00:00")
                self.assertTrue(changed)
                self.assertFalse(repeated)
                self.assertEqual(breadth["date"], "2026-07-23")
                self.assertEqual(breadth["samples"], [])
                self.assertEqual(breadth["previous_day"]["date"], "2026-07-22")
                self.assertEqual(len(breadth["previous_day"]["samples"]), 1)
                self.assertEqual(breadth["previous_day"]["samples"][0]["red"], 3000)
                self.assertEqual(breadth["previous_turnover"]["date"], "2026-07-22")
                self.assertEqual(len(breadth["previous_turnover"]["samples"]), 1)
                self.assertEqual(
                    set(breadth["previous_turnover"]["samples"][0]),
                    {
                        "generated_at",
                        "actual_turnover_yi",
                        "turnover_actual_source",
                    },
                )
                self.assertEqual(flow["date"], "2026-07-23")
                self.assertEqual(flow["samples"], [])
                self.assertEqual(flow_recovery["date"], "2026-07-22")
                self.assertEqual(len(flow_recovery["samples"]), 1)
                self.assertEqual(money["retention_date"], "2026-07-23")
                self.assertEqual(money["inflow"], [])
                self.assertEqual(money["outflow"], [])
        finally:
            dashboard.MARKET_BREADTH_HISTORY_FILE = original_breadth_file
            dashboard.INDUSTRY_FLOW_HISTORY_FILE = original_flow_file
            dashboard.MONEY_FLOW_SNAPSHOT_FILE = original_money_file

    def test_daily_reset_keeps_current_samples_when_history_metadata_is_stale(self):
        original_breadth_file = dashboard.MARKET_BREADTH_HISTORY_FILE
        original_flow_file = dashboard.INDUSTRY_FLOW_HISTORY_FILE
        original_money_file = dashboard.MONEY_FLOW_SNAPSHOT_FILE
        try:
            with tempfile.TemporaryDirectory(prefix="niuone-industry-flow-roll-") as temp_dir:
                root = Path(temp_dir)
                dashboard.MARKET_BREADTH_HISTORY_FILE = root / "market_breadth.json"
                dashboard.INDUSTRY_FLOW_HISTORY_FILE = root / "industry_flow.json"
                dashboard.MONEY_FLOW_SNAPSHOT_FILE = root / "money_flow.json"
                dashboard.INDUSTRY_FLOW_HISTORY_FILE.write_text(json.dumps({
                    "date": "2026-07-22",
                    "samples": [
                        {
                            "generated_at": "2026-07-22 15:00:00",
                            "items": [{"name": "银行", "net_flow_yi": -3}],
                        },
                        {
                            "generated_at": "2026-07-23 09:25:00",
                            "items": [{"name": "半导体", "net_flow_yi": 12}],
                        },
                        {
                            "generated_at": "2026-07-23 10:00:00",
                            "items": [{"name": "软件开发", "net_flow_yi": 8}],
                        },
                    ],
                }), encoding="utf-8")

                changed = dashboard.reset_daily_market_histories(
                    datetime(2026, 7, 23, 11, 0, 0)
                )
                first = dashboard.INDUSTRY_FLOW_HISTORY_FILE.read_text(encoding="utf-8")
                repeated = dashboard.reset_daily_market_histories(
                    datetime(2026, 7, 23, 11, 1, 0)
                )
                second = dashboard.INDUSTRY_FLOW_HISTORY_FILE.read_text(encoding="utf-8")
                stored = json.loads(second)

                self.assertTrue(changed)
                self.assertFalse(repeated)
                self.assertEqual(first, second)
                self.assertEqual(stored["date"], "2026-07-23")
                self.assertEqual(
                    [item["generated_at"] for item in stored["samples"]],
                    ["2026-07-23 09:25:00", "2026-07-23 10:00:00"],
                )
                self.assertEqual(
                    json.loads(
                        dashboard._industry_flow_history_recovery_file().read_text(
                            encoding="utf-8"
                        )
                    ),
                    stored,
                )
        finally:
            dashboard.MARKET_BREADTH_HISTORY_FILE = original_breadth_file
            dashboard.INDUSTRY_FLOW_HISTORY_FILE = original_flow_file
            dashboard.MONEY_FLOW_SNAPSHOT_FILE = original_money_file

    def test_daily_reset_restores_current_samples_from_recovery_mirror(self):
        original_breadth_file = dashboard.MARKET_BREADTH_HISTORY_FILE
        original_flow_file = dashboard.INDUSTRY_FLOW_HISTORY_FILE
        original_money_file = dashboard.MONEY_FLOW_SNAPSHOT_FILE
        original_calendar = dashboard.is_a_share_trading_day_for_dashboard
        try:
            with tempfile.TemporaryDirectory(prefix="niuone-industry-flow-recover-") as temp_dir:
                root = Path(temp_dir)
                dashboard.MARKET_BREADTH_HISTORY_FILE = root / "market_breadth.json"
                dashboard.INDUSTRY_FLOW_HISTORY_FILE = root / "industry_flow.json"
                dashboard.MONEY_FLOW_SNAPSHOT_FILE = root / "money_flow.json"
                dashboard.is_a_share_trading_day_for_dashboard = lambda _now: True
                dashboard.record_industry_flow_sample({
                    "generated_at": "2026-07-23 09:25:00",
                    "items": [{"name": "半导体", "net_flow_yi": 12}],
                }, now=datetime(2026, 7, 23, 9, 25, 0))
                dashboard.record_industry_flow_sample({
                    "generated_at": "2026-07-23 10:00:00",
                    "items": [{"name": "软件开发", "net_flow_yi": 8}],
                }, now=datetime(2026, 7, 23, 10, 0, 0))
                mirrored = json.loads(
                    dashboard._industry_flow_history_recovery_file().read_text(
                        encoding="utf-8"
                    )
                )
                dashboard.INDUSTRY_FLOW_HISTORY_FILE.write_text(
                    json.dumps(dashboard._empty_industry_flow_history("2026-07-23")),
                    encoding="utf-8",
                )

                changed = dashboard.reset_daily_market_histories(
                    datetime(2026, 7, 23, 11, 0, 0)
                )
                restored = json.loads(
                    dashboard.INDUSTRY_FLOW_HISTORY_FILE.read_text(encoding="utf-8")
                )
                repeated = dashboard.reset_daily_market_histories(
                    datetime(2026, 7, 23, 11, 1, 0)
                )

                self.assertEqual(len(mirrored["samples"]), 2)
                self.assertTrue(changed)
                self.assertFalse(repeated)
                self.assertEqual(restored, mirrored)
        finally:
            dashboard.MARKET_BREADTH_HISTORY_FILE = original_breadth_file
            dashboard.INDUSTRY_FLOW_HISTORY_FILE = original_flow_file
            dashboard.MONEY_FLOW_SNAPSHOT_FILE = original_money_file
            dashboard.is_a_share_trading_day_for_dashboard = original_calendar

    def test_daily_reset_restores_current_breadth_from_unusable_primary(self):
        original_breadth_file = dashboard.MARKET_BREADTH_HISTORY_FILE
        original_flow_file = dashboard.INDUSTRY_FLOW_HISTORY_FILE
        original_money_file = dashboard.MONEY_FLOW_SNAPSHOT_FILE
        try:
            for failure_mode in ("missing", "corrupt", "empty"):
                with self.subTest(failure_mode=failure_mode), tempfile.TemporaryDirectory(
                    prefix="niuone-market-breadth-recover-"
                ) as temp_dir:
                    root = Path(temp_dir)
                    dashboard.MARKET_BREADTH_HISTORY_FILE = root / "market_breadth.json"
                    dashboard.INDUSTRY_FLOW_HISTORY_FILE = root / "industry_flow.json"
                    dashboard.MONEY_FLOW_SNAPSHOT_FILE = root / "money_flow.json"
                    dashboard.record_market_breadth_sample(
                        sample("2026-07-23 10:00:00"),
                        now=datetime(2026, 7, 23, 10, 0),
                    )
                    dashboard.record_market_breadth_sample(
                        sample("2026-07-23 10:01:00", red=3100, green=1900),
                        now=datetime(2026, 7, 23, 10, 1),
                    )
                    recovery_file = dashboard._market_breadth_history_recovery_file()
                    mirrored = json.loads(recovery_file.read_text(encoding="utf-8"))

                    if failure_mode == "missing":
                        dashboard.MARKET_BREADTH_HISTORY_FILE.unlink()
                    elif failure_mode == "corrupt":
                        dashboard.MARKET_BREADTH_HISTORY_FILE.write_text(
                            "{not-json", encoding="utf-8"
                        )
                    else:
                        dashboard.MARKET_BREADTH_HISTORY_FILE.write_text(
                            json.dumps(
                                dashboard._empty_market_breadth_history("2026-07-23")
                            ),
                            encoding="utf-8",
                        )

                    changed = dashboard.reset_daily_market_histories(
                        datetime(2026, 7, 23, 11, 0)
                    )
                    restored = json.loads(
                        dashboard.MARKET_BREADTH_HISTORY_FILE.read_text(encoding="utf-8")
                    )
                    repeated = dashboard.reset_daily_market_histories(
                        datetime(2026, 7, 23, 11, 1)
                    )

                    self.assertEqual(len(mirrored["samples"]), 2)
                    self.assertTrue(changed)
                    self.assertFalse(repeated)
                    self.assertEqual(restored, mirrored)
        finally:
            dashboard.MARKET_BREADTH_HISTORY_FILE = original_breadth_file
            dashboard.INDUSTRY_FLOW_HISTORY_FILE = original_flow_file
            dashboard.MONEY_FLOW_SNAPSHOT_FILE = original_money_file

    def test_next_breadth_sample_merges_richer_recovery_before_persisting(self):
        original_breadth_file = dashboard.MARKET_BREADTH_HISTORY_FILE
        original_flow_file = dashboard.INDUSTRY_FLOW_HISTORY_FILE
        original_money_file = dashboard.MONEY_FLOW_SNAPSHOT_FILE
        try:
            with tempfile.TemporaryDirectory(
                prefix="niuone-market-breadth-append-recover-"
            ) as temp_dir:
                root = Path(temp_dir)
                dashboard.MARKET_BREADTH_HISTORY_FILE = root / "market_breadth.json"
                dashboard.INDUSTRY_FLOW_HISTORY_FILE = root / "industry_flow.json"
                dashboard.MONEY_FLOW_SNAPSHOT_FILE = root / "money_flow.json"
                dashboard.record_market_breadth_sample(
                    sample("2026-07-23 10:00:00"),
                    now=datetime(2026, 7, 23, 10, 0),
                )
                dashboard.record_market_breadth_sample(
                    sample("2026-07-23 10:01:00", red=3100, green=1900),
                    now=datetime(2026, 7, 23, 10, 1),
                )
                dashboard.MARKET_BREADTH_HISTORY_FILE.write_text(
                    json.dumps(dashboard._empty_market_breadth_history("2026-07-23")),
                    encoding="utf-8",
                )

                recorded = dashboard.record_market_breadth_sample(
                    sample("2026-07-23 10:02:00", red=3200, green=1800),
                    now=datetime(2026, 7, 23, 10, 2),
                )
                stored = json.loads(
                    dashboard.MARKET_BREADTH_HISTORY_FILE.read_text(encoding="utf-8")
                )
                recovery = json.loads(
                    dashboard._market_breadth_history_recovery_file().read_text(
                        encoding="utf-8"
                    )
                )

                expected_times = [
                    "2026-07-23 10:00:00",
                    "2026-07-23 10:01:00",
                    "2026-07-23 10:02:00",
                ]
                self.assertEqual(
                    [item["generated_at"] for item in recorded], expected_times
                )
                self.assertEqual(
                    [item["generated_at"] for item in stored["samples"]],
                    expected_times,
                )
                self.assertEqual(recovery, stored)
        finally:
            dashboard.MARKET_BREADTH_HISTORY_FILE = original_breadth_file
            dashboard.INDUSTRY_FLOW_HISTORY_FILE = original_flow_file
            dashboard.MONEY_FLOW_SNAPSHOT_FILE = original_money_file

    def test_market_breadth_keeps_previous_curve_after_nine(self):
        original_breadth_file = dashboard.MARKET_BREADTH_HISTORY_FILE
        original_flow_file = dashboard.INDUSTRY_FLOW_HISTORY_FILE
        original_money_file = dashboard.MONEY_FLOW_SNAPSHOT_FILE
        try:
            with tempfile.TemporaryDirectory(prefix="niuone-after-midnight-") as temp_dir:
                root = Path(temp_dir)
                dashboard.MARKET_BREADTH_HISTORY_FILE = root / "market_breadth.json"
                dashboard.INDUSTRY_FLOW_HISTORY_FILE = root / "industry_flow.json"
                dashboard.MONEY_FLOW_SNAPSHOT_FILE = root / "money_flow.json"
                yesterday_breadth = {
                    "date": "2026-07-22",
                    "samples": [sample("2026-07-22 15:00:00")],
                }
                dashboard.MARKET_BREADTH_HISTORY_FILE.write_text(
                    json.dumps(yesterday_breadth), encoding="utf-8"
                )
                yesterday_flow = {
                    "generated_at": "2026-07-22 15:00:00",
                    "inflow": [{"name": "半导体", "net_flow_yi": 12}],
                    "outflow": [{"name": "银行", "net_flow_yi": -6}],
                }
                with patch.object(
                    dashboard,
                    "current_cn_datetime",
                    return_value=datetime(2026, 7, 23, 0, 1, 0),
                ), patch.object(
                    dashboard,
                    "fetch_tencent_market_breadth",
                ) as fetch, patch.object(
                    dashboard,
                    "cached_json_data",
                    return_value=yesterday_flow,
                ):
                    breadth_payload = dashboard.produce_market_breadth_data()
                    flow_payload = dashboard.produce_industry_flow_data()

                fetch.assert_not_called()
                self.assertTrue(breadth_payload["available"])
                self.assertEqual(
                    breadth_payload["timeline"][-1]["generated_at"],
                    "2026-07-22 15:00:00",
                )
                self.assertTrue(flow_payload["available"])
                self.assertTrue(flow_payload["nodes"])
                self.assertEqual(
                    flow_payload["timeline"][-1]["generated_at"],
                    "2026-07-22 15:00:00",
                )

                with patch.object(
                    dashboard,
                    "current_cn_datetime",
                    return_value=datetime(2026, 7, 23, 9, 0, 0),
                ), patch.object(
                    dashboard,
                    "fetch_tencent_market_breadth",
                ) as fetch_at_nine, patch.object(
                    dashboard,
                    "cached_json_data",
                    return_value=yesterday_flow,
                ):
                    breadth_at_nine = dashboard.produce_market_breadth_data()
                    flow_at_nine = dashboard.produce_industry_flow_data()

                fetch_at_nine.assert_not_called()
                self.assertTrue(breadth_at_nine["available"])
                self.assertTrue(breadth_at_nine["displaying_previous_trading_day"])
                self.assertEqual(breadth_at_nine["display_date"], "2026-07-22")
                self.assertEqual(
                    breadth_at_nine["timeline"][-1]["generated_at"],
                    "2026-07-22 15:00:00",
                )
                self.assertFalse(flow_at_nine["available"])
                self.assertEqual(flow_at_nine["nodes"], [])
                self.assertEqual(flow_at_nine["timeline"], [])
        finally:
            dashboard.MARKET_BREADTH_HISTORY_FILE = original_breadth_file
            dashboard.INDUSTRY_FLOW_HISTORY_FILE = original_flow_file
            dashboard.MONEY_FLOW_SNAPSHOT_FILE = original_money_file

    def test_daily_reset_wait_is_aligned_to_next_beijing_nine(self):
        self.assertEqual(
            dashboard.seconds_until_next_market_retention_rollover(
                datetime(2026, 7, 23, 8, 59, 30)
            ),
            30,
        )
        self.assertEqual(
            dashboard.seconds_until_next_market_retention_rollover(
                datetime(2026, 7, 23, 9, 0, 0)
            ),
            24 * 60 * 60,
        )
        self.assertEqual(
            dashboard.seconds_until_next_market_retention_rollover(
                datetime(2026, 7, 23, 8, 59, 30, tzinfo=dashboard.CN_TZ)
            ),
            30,
        )

    def test_history_replaces_same_timestamp_and_resets_on_next_day(self):
        history = append_market_breadth_sample({}, sample("2026-07-22 09:30:00"))
        history = append_market_breadth_sample(
            history,
            sample("2026-07-22 09:31:00", red=3100, green=1900),
        )
        history = append_market_breadth_sample(
            history,
            sample("2026-07-22 09:31:00", red=3200, green=1800),
        )

        self.assertEqual(len(history["samples"]), 2)
        self.assertEqual(history["samples"][-1]["red"], 3200)

        next_day = append_market_breadth_sample(
            history,
            sample("2026-07-23 09:30:00", red=1000, green=4000),
        )
        self.assertEqual(next_day["date"], "2026-07-23")
        self.assertEqual(len(next_day["samples"]), 1)

    def test_next_day_retains_previous_breadth_and_turnover_curves(self):
        history = append_market_breadth_sample({}, {
            **sample("2026-07-22 09:30:00"),
            "actual_turnover_yi": 100,
            "turnover_actual_source": "测试分钟线",
            "turnover_actual_source_url": "https://example.test/minute",
        })
        history = append_market_breadth_sample(history, {
            **sample("2026-07-22 09:31:00"),
            "actual_turnover_yi": 220,
            "turnover_actual_source": "测试分钟线",
            "turnover_actual_source_url": "https://example.test/minute",
        })

        next_day = append_market_breadth_sample(history, {
            **sample("2026-07-23 09:30:00"),
            "actual_turnover_yi": 120,
        })

        self.assertEqual(next_day["schema_version"], 5)
        self.assertEqual(len(next_day["samples"]), 1)
        previous_day = next_day["previous_day"]
        self.assertEqual(previous_day["date"], "2026-07-22")
        self.assertEqual(len(previous_day["samples"]), 2)
        self.assertEqual(previous_day["samples"][0]["red"], 3000)
        self.assertEqual(previous_day["samples"][-1]["actual_turnover_yi"], 220)
        previous = next_day["previous_turnover"]
        self.assertEqual(previous["date"], "2026-07-22")
        self.assertEqual(previous["source"], "测试分钟线")
        self.assertEqual(len(previous["samples"]), 2)
        self.assertNotIn("red", previous["samples"][0])
        self.assertEqual(previous["samples"][-1]["actual_turnover_yi"], 220)

    def test_previous_breadth_survives_consecutive_closed_day_rolls(self):
        friday = append_market_breadth_sample(
            {},
            sample("2026-07-24 15:00:00", red=3400, green=1600),
        )

        saturday = dashboard.roll_market_breadth_history(friday, "2026-07-25")
        sunday = dashboard.roll_market_breadth_history(saturday, "2026-07-26")
        monday = dashboard.roll_market_breadth_history(sunday, "2026-07-27")

        self.assertEqual(monday["samples"], [])
        self.assertEqual(monday["previous_day"]["date"], "2026-07-24")
        self.assertEqual(
            monday["previous_day"]["samples"][-1]["generated_at"],
            "2026-07-24 15:00:00",
        )
        self.assertEqual(monday["previous_day"]["samples"][-1]["red"], 3400)

    def test_money_flow_uses_previous_trading_day_recovery_when_refresh_is_empty(self):
        original_industry_file = dashboard.INDUSTRY_FLOW_HISTORY_FILE
        original_money_file = dashboard.MONEY_FLOW_SNAPSHOT_FILE
        original_runner = dashboard.run_dashboard_helper
        original_clock = dashboard.current_cn_datetime
        original_calendar = dashboard.dashboard_trading_day_status
        try:
            with tempfile.TemporaryDirectory(prefix="niuone-money-flow-previous-") as temp_dir:
                root = Path(temp_dir)
                dashboard.INDUSTRY_FLOW_HISTORY_FILE = root / "industry_flow.json"
                dashboard.MONEY_FLOW_SNAPSHOT_FILE = root / "money_flow.json"
                dashboard.INDUSTRY_FLOW_HISTORY_FILE.write_text(json.dumps({
                    "date": "2026-07-26",
                    "samples": [],
                }), encoding="utf-8")
                dashboard._industry_flow_history_recovery_file().write_text(json.dumps({
                    "date": "2026-07-24",
                    "samples": [{
                        "generated_at": "2026-07-24 15:00:00",
                        "items": [
                            {"name": "半导体", "net_flow_yi": 12},
                            {"name": "软件开发", "net_flow_yi": 8},
                            {"name": "银行", "net_flow_yi": -6},
                        ],
                    }],
                }), encoding="utf-8")
                dashboard.run_dashboard_helper = lambda *_args, **_kwargs: {
                    "inflow": [],
                    "outflow": [],
                    "error": "upstream unavailable",
                }
                dashboard.current_cn_datetime = lambda: datetime(2026, 7, 26, 10, 0)
                dashboard.dashboard_trading_day_status = lambda _now=None: {
                    "date": "2026-07-26",
                    "is_trading_day": False,
                    "previous_trading_day": "2026-07-24",
                }

                payload = dashboard.produce_money_flow_data()

                self.assertTrue(payload["displaying_previous_trading_day"])
                self.assertTrue(payload["displaying_historical_data"])
                self.assertEqual(payload["display_date"], "2026-07-24")
                self.assertEqual(
                    [row["name"] for row in payload["inflow"]],
                    ["半导体", "软件开发"],
                )
                self.assertEqual(payload["outflow"][0]["name"], "银行")
                self.assertEqual(payload["inflow"][0]["net_flow_yi"], 12)
                self.assertTrue(payload["stale_cache"])
                self.assertEqual(payload["error"], "upstream unavailable")
        finally:
            dashboard.INDUSTRY_FLOW_HISTORY_FILE = original_industry_file
            dashboard.MONEY_FLOW_SNAPSHOT_FILE = original_money_file
            dashboard.run_dashboard_helper = original_runner
            dashboard.current_cn_datetime = original_clock
            dashboard.dashboard_trading_day_status = original_calendar

    def test_invalid_or_lunch_samples_never_replace_valid_history(self):
        history = append_market_breadth_sample({}, sample("2026-07-22 10:00:00"))
        invalid = sample("2026-07-22 10:01:00")
        invalid["quote_count"] += 1

        self.assertIsNone(compact_market_breadth_sample(invalid))
        self.assertEqual(append_market_breadth_sample(history, invalid), history)
        self.assertEqual(
            append_market_breadth_sample(history, sample("2026-07-22 12:00:00")),
            history,
        )

    def test_legacy_samples_remain_valid_without_synthesized_turnover(self):
        legacy = compact_market_breadth_sample(sample("2026-07-22 10:00:00"))
        self.assertIsNotNone(legacy)
        self.assertNotIn("estimated_turnover_yi", legacy)
        self.assertNotIn("actual_turnover_yi", legacy)

        incomplete = sample("2026-07-22 10:01:00")
        incomplete["actual_turnover_yi"] = 1234
        actual_only = compact_market_breadth_sample(incomplete)
        self.assertIsNotNone(actual_only)
        self.assertEqual(actual_only["actual_turnover_yi"], 1234)
        self.assertNotIn("estimated_turnover_yi", actual_only)

        incomplete_comparison = {
            **sample("2026-07-22 10:02:00"),
            "estimated_turnover_yi": 12_000,
            "actual_turnover_yi": 3_000,
            "turnover_increment_yi": -500,
        }
        self.assertIsNone(compact_market_breadth_sample(incomplete_comparison))

    def test_public_payload_exposes_current_day_timeline_and_source(self):
        first = sample("2026-07-22 09:30:00")
        prior_with_turnover = {
            **sample("2026-07-22 09:45:00", red=3300, green=1700),
            "estimated_turnover_yi": 11_500,
            "actual_turnover_yi": 2_900,
        }
        latest = {
            **sample("2026-07-22 10:00:00", red=3500, green=1500),
            "estimated_turnover_yi": 12_345.67,
            "actual_turnover_yi": 3_456.78,
            "previous_turnover_yi": 12_000,
            "turnover_increment_yi": 345.67,
            "turnover_comparison_date": "2026-07-21",
            "turnover_comparison_source": "测试指数日线",
            "turnover_comparison_source_url": "https://example.test/",
            "turnover_amount_count": 5100,
            "turnover_actual_source": "东方财富沪深指数分钟线",
            "turnover_actual_source_url": "https://push2his.eastmoney.com/",
            "turnover_estimate_model": "eastmoney_20d_intraday_median",
            "turnover_estimate_model_label": "东方财富近20日5分钟成交分布中位数",
            "turnover_estimate_source": "东方财富沪深指数分钟线",
            "turnover_estimate_source_url": "https://push2his.eastmoney.com/",
            "turnover_profile_days": 20,
            "turnover_profile_start": "2026-06-23",
            "turnover_profile_end": "2026-07-21",
            "turnover_profile_interval_minutes": 5,
            "source": "腾讯证券沪深A股实时行情",
            "source_url": "https://gu.qq.com/",
            "universe": "沪深A股测试口径",
        }

        payload = build_market_breadth_payload(
            latest,
            history_samples=[first, prior_with_turnover],
        )

        self.assertTrue(payload["available"])
        self.assertEqual(payload["latest"]["red"], 3500)
        self.assertEqual(payload["latest"]["estimated_turnover_yi"], 12_345.67)
        self.assertEqual(payload["latest"]["actual_turnover_yi"], 3_456.78)
        self.assertEqual(payload["latest"]["turnover_increment_yi"], 345.67)
        self.assertEqual(payload["turnover_comparison"]["date"], "2026-07-21")
        self.assertEqual(payload["turnover_comparison"]["previous_turnover_yi"], 12_000)
        self.assertEqual(payload["turnover_actual"]["source"], "东方财富沪深指数分钟线")
        self.assertEqual(payload["turnover_estimate"]["profile_days"], 20)
        self.assertEqual(
            payload["turnover_estimate"]["model"],
            "eastmoney_20d_intraday_median",
        )
        self.assertEqual(len(payload["timeline"]), 3)
        self.assertNotIn("actual_turnover_yi", payload["timeline"][0])
        self.assertEqual(payload["timeline"][1]["actual_turnover_yi"], 2_900)
        self.assertNotIn("estimated_turnover_yi", payload["timeline"][1])
        self.assertNotIn("turnover_increment_yi", payload["timeline"][1])
        self.assertEqual(payload["sampling"]["point_count"], 3)
        self.assertEqual(payload["sampling"]["timezone"], "Asia/Shanghai")
        self.assertEqual(payload["source"], "腾讯证券沪深A股实时行情")
        self.assertEqual(payload["universe"], "沪深A股测试口径")

    def test_public_payload_overlays_previous_turnover_at_same_progress(self):
        first = {
            **sample("2026-07-22 09:30:20"),
            "actual_turnover_yi": 120,
        }
        latest = {
            **sample("2026-07-22 09:31:20"),
            "actual_turnover_yi": 250,
        }
        previous_turnover = {
            "date": "2026-07-21",
            "source": "测试前日分钟线",
            "source_url": "https://example.test/previous-minute",
            "samples": [
                {
                    "generated_at": "2026-07-21 09:30:00",
                    "actual_turnover_yi": 100,
                },
                {
                    "generated_at": "2026-07-21 09:31:00",
                    "actual_turnover_yi": 200,
                },
            ],
        }

        payload = build_market_breadth_payload(
            latest,
            history_samples=[first],
            previous_turnover=previous_turnover,
        )

        self.assertEqual(payload["timeline"][0]["previous_actual_turnover_yi"], 100)
        self.assertEqual(payload["timeline"][0]["turnover_same_time_delta_yi"], 20)
        self.assertEqual(payload["latest"]["previous_actual_turnover_yi"], 200)
        self.assertEqual(payload["latest"]["turnover_same_time_delta_yi"], 50)
        self.assertEqual(payload["turnover_previous_actual"]["date"], "2026-07-21")
        self.assertEqual(payload["turnover_previous_actual"]["point_count"], 2)
        self.assertEqual(payload["turnover_previous_actual"]["matched_point_count"], 2)
        self.assertTrue(payload["sampling"]["historical_backfill"]["available"])

    def test_public_payload_does_not_mix_projection_models_on_one_line(self):
        legacy = {
            **sample("2026-07-22 09:45:00", red=3300, green=1700),
            "estimated_turnover_yi": 80_000,
            "actual_turnover_yi": 2_900,
            "previous_turnover_yi": 12_000,
            "turnover_increment_yi": 68_000,
            "turnover_comparison_date": "2026-07-21",
            "turnover_estimate_model": "elapsed_minutes_linear",
        }
        latest = {
            **sample("2026-07-22 10:00:00", red=3500, green=1500),
            "estimated_turnover_yi": 12_500,
            "actual_turnover_yi": 3_500,
            "previous_turnover_yi": 12_000,
            "turnover_increment_yi": 500,
            "turnover_comparison_date": "2026-07-21",
            "turnover_estimate_model": "eastmoney_20d_intraday_median",
        }

        payload = build_market_breadth_payload(
            latest,
            history_samples=[legacy],
        )

        earlier = payload["timeline"][0]
        self.assertEqual(earlier["red"], 3300)
        self.assertEqual(earlier["actual_turnover_yi"], 2_900)
        self.assertNotIn("estimated_turnover_yi", earlier)
        self.assertNotIn("turnover_increment_yi", earlier)
        self.assertEqual(
            payload["latest"]["estimated_turnover_yi"],
            12_500,
        )

    def test_public_payload_reuses_persisted_turnover_reference_after_source_failure(self):
        reference_sample = {
            **sample("2026-07-22 10:00:00"),
            "estimated_turnover_yi": 12_500,
            "actual_turnover_yi": 3_500,
            "previous_turnover_yi": 12_000,
            "turnover_increment_yi": 500,
            "turnover_comparison_date": "2026-07-21",
        }
        latest = {
            **sample("2026-07-22 10:01:00"),
            "estimated_turnover_yi": 11_800,
            "actual_turnover_yi": 3_600,
        }

        payload = build_market_breadth_payload(
            latest,
            history_samples=[reference_sample],
        )

        self.assertEqual(payload["latest"]["turnover_increment_yi"], -200)
        self.assertEqual(payload["turnover_comparison"]["previous_turnover_yi"], 12_000)

    def test_dashboard_estimate_injects_persistent_profile_cache_path(self):
        original_cache_file = dashboard.TURNOVER_PROFILE_CACHE_FILE
        try:
            with tempfile.TemporaryDirectory(prefix="niuone-turnover-profile-") as temp_dir:
                cache_path = Path(temp_dir) / "profile.json"
                dashboard.TURNOVER_PROFILE_CACHE_FILE = cache_path

                def estimate(generated_at, fallback_actual, *, profile_fetcher):
                    profile_fetcher(generated_at.date())
                    return {"actual_turnover_yi": fallback_actual}

                with patch.object(
                    dashboard,
                    "fetch_market_turnover_estimate",
                    side_effect=estimate,
                ), patch.object(
                    dashboard,
                    "fetch_turnover_profile",
                    return_value={"profile_days": 20},
                ) as profile_fetch:
                    result = (
                        dashboard._fetch_market_turnover_estimate_with_persistent_profile(
                            datetime(2026, 7, 22, 10, 0),
                            3_500,
                        )
                    )

                self.assertEqual(result["actual_turnover_yi"], 3_500)
                profile_fetch.assert_called_once_with(
                    datetime(2026, 7, 22).date(),
                    persistent_cache_path=cache_path,
                )
        finally:
            dashboard.TURNOVER_PROFILE_CACHE_FILE = original_cache_file

    def test_producer_retains_previous_valid_sample_when_fetch_fails(self):
        original_history_file = dashboard.MARKET_BREADTH_HISTORY_FILE
        try:
            with tempfile.TemporaryDirectory(prefix="niuone-market-breadth-") as temp_dir:
                dashboard.MARKET_BREADTH_HISTORY_FILE = Path(temp_dir) / "history.json"
                recorded = dashboard.record_market_breadth_sample(
                    sample("2026-07-22 10:00:00", red=3456, green=1544),
                    now=datetime(2026, 7, 22, 10, 0),
                )
                with patch.object(
                    dashboard,
                    "fetch_tencent_market_breadth",
                    side_effect=TimeoutError("upstream timeout"),
                ), patch.object(
                    dashboard,
                    "current_cn_datetime",
                    return_value=datetime(2026, 7, 22, 10, 10),
                ):
                    payload = dashboard.produce_market_breadth_data()

                self.assertEqual(len(recorded), 1)
                self.assertTrue(payload["available"])
                self.assertTrue(payload["stale_cache"])
                self.assertIn("TimeoutError", payload["error"])
                self.assertEqual(payload["latest"]["red"], 3456)
                self.assertEqual(payload["latest"]["green"], 1544)
        finally:
            dashboard.MARKET_BREADTH_HISTORY_FILE = original_history_file

    def test_previous_curve_recovers_when_active_history_loses_its_archive(self):
        original_history_file = dashboard.MARKET_BREADTH_HISTORY_FILE
        try:
            with tempfile.TemporaryDirectory(prefix="niuone-market-breadth-recovery-") as temp_dir:
                history_file = Path(temp_dir) / "history.json"
                dashboard.MARKET_BREADTH_HISTORY_FILE = history_file
                dashboard.record_market_breadth_sample(
                    sample("2026-07-24 09:30:00", red=2800, green=2200),
                    now=datetime(2026, 7, 24, 9, 30),
                )
                dashboard.record_market_breadth_sample(
                    sample("2026-07-24 15:00:00", red=3400, green=1600),
                    now=datetime(2026, 7, 24, 15, 0),
                )
                recovery_file = dashboard._market_breadth_history_recovery_file()
                self.assertTrue(recovery_file.exists())

                history_file.write_text(json.dumps(
                    dashboard.roll_market_breadth_history(None, "2026-07-26"),
                ), encoding="utf-8")
                recovered = dashboard.load_previous_market_breadth_samples(
                    now=datetime(2026, 7, 26, 12, 0),
                )

                self.assertEqual(len(recovered), 2)
                self.assertEqual(recovered[0]["generated_at"], "2026-07-24 09:30:00")
                self.assertEqual(recovered[-1]["red"], 3400)
        finally:
            dashboard.MARKET_BREADTH_HISTORY_FILE = original_history_file

    def test_producer_shares_each_new_quote_batch_with_theme_refresh(self):
        original_history_file = dashboard.MARKET_BREADTH_HISTORY_FILE
        accepted = []

        def fetch(*, turnover_estimate_fetcher=None, quote_snapshot_consumer=None):
            self.assertIsNotNone(turnover_estimate_fetcher)
            self.assertIsNotNone(quote_snapshot_consumer)
            quote_snapshot_consumer({
                "generated_at": "2026-07-22 10:10:00",
                "quote_count": 5100,
                "quotes": {"sh600001": {"price": 10.1}},
            })
            return sample("2026-07-22 10:10:00")

        try:
            with tempfile.TemporaryDirectory(prefix="niuone-market-breadth-share-") as temp_dir:
                dashboard.MARKET_BREADTH_HISTORY_FILE = Path(temp_dir) / "history.json"
                with patch.object(
                    dashboard,
                    "fetch_tencent_market_breadth",
                    side_effect=fetch,
                ), patch.object(
                    dashboard,
                    "accept_niuone_mainline_quote_snapshot",
                    side_effect=accepted.append,
                ), patch.object(
                    dashboard,
                    "NIUONE_MAINLINE_MINUTE_REFRESH_ENABLED",
                    True,
                ), patch.object(
                    dashboard,
                    "current_cn_datetime",
                    return_value=datetime(2026, 7, 22, 10, 10),
                ):
                    payload = dashboard.produce_market_breadth_data()

            self.assertTrue(payload["available"])
            self.assertEqual(len(accepted), 1)
            self.assertEqual(accepted[0]["generated_at"], "2026-07-22 10:10:00")
        finally:
            dashboard.MARKET_BREADTH_HISTORY_FILE = original_history_file

    def test_minute_theme_refresh_cooldown_limits_expensive_cpu_duty_cycle(self):
        self.assertEqual(
            dashboard.niuone_mainline_minute_cooldown_seconds(
                5_000,
                sample_interval_seconds=30,
            ),
            25.0,
        )
        self.assertEqual(
            dashboard.niuone_mainline_minute_cooldown_seconds(
                24_000,
                sample_interval_seconds=30,
            ),
            72.0,
        )

    def test_minute_theme_worker_waits_and_keeps_latest_pending_snapshot(self):
        self.enterContext(patch.object(dashboard, "current_cn_datetime",
                           side_effect=lambda: datetime(2026,8,4,10,30) + timedelta(seconds=clock["now"])))
        original_pending = dashboard.NIUONE_MAINLINE_MINUTE_PENDING
        original_thread = dashboard.NIUONE_MAINLINE_MINUTE_THREAD
        original_next_allowed = (
            dashboard.NIUONE_MAINLINE_MINUTE_NEXT_ALLOWED_MONOTONIC
        )
        clock = {"now": 0.0}
        sleeps = []
        processed = []

        def monotonic():
            return clock["now"]

        def sleep(seconds):
            sleeps.append(seconds)
            clock["now"] += seconds

        def refresh_isolated(snapshot):
            processed.append(snapshot["generated_at"])
            clock["now"] += 24.0
            if len(processed) == 1:
                with dashboard.NIUONE_MAINLINE_MINUTE_STATE_LOCK:
                    dashboard.NIUONE_MAINLINE_MINUTE_PENDING = {
                        "generated_at": "2026-08-04 10:31:00"
                    }
            return True

        try:
            dashboard.NIUONE_MAINLINE_MINUTE_PENDING = {
                "generated_at": "2026-08-04 10:30:00"
            }
            dashboard.NIUONE_MAINLINE_MINUTE_THREAD = object()
            dashboard.NIUONE_MAINLINE_MINUTE_NEXT_ALLOWED_MONOTONIC = 0.0
            with patch.object(
                dashboard.time,
                "monotonic",
                side_effect=monotonic,
            ), patch.object(
                dashboard.time,
                "sleep",
                side_effect=sleep,
            ), patch.object(
                dashboard,
                "run_niuone_mainline_minute_refresh_isolated",
                side_effect=refresh_isolated,
            ):
                dashboard._niuone_mainline_minute_worker()

            self.assertEqual(
                processed,
                ["2026-08-04 10:30:00", "2026-08-04 10:31:00"],
            )
            self.assertEqual(sleeps, [72.0])
            self.assertIsNone(dashboard.NIUONE_MAINLINE_MINUTE_THREAD)
        finally:
            dashboard.NIUONE_MAINLINE_MINUTE_PENDING = original_pending
            dashboard.NIUONE_MAINLINE_MINUTE_THREAD = original_thread
            dashboard.NIUONE_MAINLINE_MINUTE_NEXT_ALLOWED_MONOTONIC = (
                original_next_allowed
            )

    def test_minute_theme_worker_defers_while_complete_scan_is_running(self):
        self.enterContext(patch.object(dashboard, "current_cn_datetime",
                           side_effect=lambda: datetime(2026,8,4,10,30) + timedelta(seconds=clock["now"])))
        original_pending = dashboard.NIUONE_MAINLINE_MINUTE_PENDING
        original_thread = dashboard.NIUONE_MAINLINE_MINUTE_THREAD
        original_next_allowed = (
            dashboard.NIUONE_MAINLINE_MINUTE_NEXT_ALLOWED_MONOTONIC
        )
        clock = {"now": 0.0}
        sleeps = []
        processed = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock["now"] += seconds

        try:
            dashboard.NIUONE_MAINLINE_MINUTE_PENDING = {
                "generated_at": "2026-08-04 10:30:00"
            }
            dashboard.NIUONE_MAINLINE_MINUTE_THREAD = object()
            dashboard.NIUONE_MAINLINE_MINUTE_NEXT_ALLOWED_MONOTONIC = 0.0
            with patch.object(
                dashboard.time,
                "monotonic",
                side_effect=lambda: clock["now"],
            ), patch.object(
                dashboard.time,
                "sleep",
                side_effect=sleep,
            ), patch.object(
                dashboard,
                "niuone_mainline_heavy_scan_in_progress",
                side_effect=[True, False, False],
            ), patch.object(
                dashboard,
                "run_niuone_mainline_minute_refresh_isolated",
                side_effect=lambda snapshot: processed.append(snapshot) or True,
            ):
                dashboard._niuone_mainline_minute_worker()

            self.assertEqual(
                sleeps,
                [dashboard.NIUONE_MAINLINE_MINUTE_BUSY_RETRY_SECONDS],
            )
            self.assertEqual(
                [item["generated_at"] for item in processed],
                ["2026-08-04 10:30:00"],
            )
        finally:
            dashboard.NIUONE_MAINLINE_MINUTE_PENDING = original_pending
            dashboard.NIUONE_MAINLINE_MINUTE_THREAD = original_thread
            dashboard.NIUONE_MAINLINE_MINUTE_NEXT_ALLOWED_MONOTONIC = (
                original_next_allowed
            )

    def test_expired_waiting_snapshot_does_not_start_another_process(self):
        with patch.object(dashboard, "NIUONE_MAINLINE_MINUTE_PENDING", {"generated_at":"2026-08-04 10:00:00"}), \
             patch.object(dashboard, "NIUONE_MAINLINE_MINUTE_THREAD", object()), \
             patch.object(dashboard, "NIUONE_MAINLINE_MINUTE_NEXT_ALLOWED_MONOTONIC", 0.0), \
             patch.object(dashboard, "niuone_mainline_heavy_scan_in_progress", return_value=False), \
             patch.object(dashboard, "current_cn_datetime", return_value=datetime(2026,8,4,10,30)), \
             patch.object(dashboard, "run_niuone_mainline_minute_refresh_isolated") as refresh:
            dashboard._niuone_mainline_minute_worker()
            refresh.assert_not_called()
            self.assertIsNone(dashboard.NIUONE_MAINLINE_MINUTE_PENDING)

    def test_isolated_minute_theme_refresh_is_spawned_and_bounded(self):
        class FakeProcess:
            returncode = 0

            def __init__(self):
                self.input = None
                self.timeout = None

            def communicate(self, *, input=None, timeout=None):
                self.input = input
                self.timeout = timeout

        process = FakeProcess()

        with patch.object(
            dashboard.subprocess,
            "Popen",
            return_value=process,
        ) as popen:
            updated = dashboard.run_niuone_mainline_minute_refresh_isolated(
                {"generated_at": "2026-08-04 10:30:00"},
                timeout_seconds=45,
            )

        self.assertTrue(updated)
        command = popen.call_args.args[0]
        self.assertEqual(command[:2], [dashboard.sys.executable, "-B"])
        self.assertEqual(Path(command[2]).name, "niuone_minute_refresh.py")
        self.assertEqual(popen.call_args.kwargs["stdin"], dashboard.subprocess.PIPE)
        self.assertEqual(
            json.loads(process.input.decode("utf-8")),
            {"generated_at": "2026-08-04 10:30:00"},
        )
        self.assertEqual(process.timeout, 45.0)

    def test_isolated_minute_theme_refresh_terminates_on_timeout(self):
        class TimedOutProcess:
            returncode = None

            def __init__(self):
                self.communicate_calls = 0
                self.terminated = False

            def communicate(self, *, input=None, timeout=None):
                self.communicate_calls += 1
                if self.communicate_calls == 1:
                    raise dashboard.subprocess.TimeoutExpired(
                        cmd="niuone-minute-refresh",
                        timeout=timeout,
                    )

            def terminate(self):
                self.terminated = True

        process = TimedOutProcess()
        with patch.object(
            dashboard.subprocess,
            "Popen",
            return_value=process,
        ):
            updated = dashboard.run_niuone_mainline_minute_refresh_isolated(
                {"generated_at": "2026-08-04 10:30:00"},
                timeout_seconds=45,
            )

        self.assertFalse(updated)
        self.assertTrue(process.terminated)
        self.assertEqual(process.communicate_calls, 2)

    def test_producer_reuses_fresh_background_sample_without_duplicate_fetch(self):
        original_history_file = dashboard.MARKET_BREADTH_HISTORY_FILE
        try:
            with tempfile.TemporaryDirectory(prefix="niuone-market-breadth-") as temp_dir:
                dashboard.MARKET_BREADTH_HISTORY_FILE = Path(temp_dir) / "history.json"
                dashboard.record_market_breadth_sample(
                    sample("2026-07-22 10:00:00", red=3200, green=1800),
                    now=datetime(2026, 7, 22, 10, 0),
                )
                with patch.object(
                    dashboard,
                    "fetch_tencent_market_breadth",
                ) as fetch, patch.object(
                    dashboard,
                    "current_cn_datetime",
                    return_value=datetime(2026, 7, 22, 10, 0, 20),
                ):
                    payload = dashboard.produce_market_breadth_data()

                fetch.assert_not_called()
                self.assertEqual(payload["latest"]["red"], 3200)
                self.assertEqual(payload["sampling"]["point_count"], 1)
        finally:
            dashboard.MARKET_BREADTH_HISTORY_FILE = original_history_file

    def test_background_sample_older_than_twenty_five_seconds_is_not_fresh(self):
        original_history_file = dashboard.MARKET_BREADTH_HISTORY_FILE
        try:
            with tempfile.TemporaryDirectory(prefix="niuone-market-breadth-stale-") as temp_dir:
                dashboard.MARKET_BREADTH_HISTORY_FILE = Path(temp_dir) / "history.json"
                dashboard.record_market_breadth_sample(
                    sample("2026-07-22 10:00:00"),
                    now=datetime(2026, 7, 22, 10, 0),
                )

                cached = dashboard._cached_market_breadth_payload(
                    datetime(2026, 7, 22, 10, 0, 26),
                )

            self.assertIsNone(cached)
        finally:
            dashboard.MARKET_BREADTH_HISTORY_FILE = original_history_file

    def test_producer_exposes_retained_previous_turnover_overlay(self):
        original_history_file = dashboard.MARKET_BREADTH_HISTORY_FILE
        try:
            with tempfile.TemporaryDirectory(prefix="niuone-market-breadth-previous-") as temp_dir:
                dashboard.MARKET_BREADTH_HISTORY_FILE = Path(temp_dir) / "history.json"
                dashboard.record_market_breadth_sample(
                    {
                        **sample("2026-07-21 10:00:00"),
                        "actual_turnover_yi": 3_000,
                    },
                    now=datetime(2026, 7, 21, 10, 0),
                )
                dashboard.record_market_breadth_sample(
                    {
                        **sample("2026-07-22 10:00:00"),
                        "actual_turnover_yi": 3_500,
                    },
                    now=datetime(2026, 7, 22, 10, 0),
                )
                with patch.object(
                    dashboard,
                    "fetch_tencent_market_breadth",
                ) as fetch, patch.object(
                    dashboard,
                    "current_cn_datetime",
                    return_value=datetime(2026, 7, 22, 10, 0, 20),
                ):
                    payload = dashboard.produce_market_breadth_data()

                fetch.assert_not_called()
                self.assertEqual(payload["latest"]["previous_actual_turnover_yi"], 3_000)
                self.assertEqual(payload["latest"]["turnover_same_time_delta_yi"], 500)
                self.assertEqual(payload["turnover_previous_actual"]["date"], "2026-07-21")
        finally:
            dashboard.MARKET_BREADTH_HISTORY_FILE = original_history_file


if __name__ == "__main__":
    unittest.main()
