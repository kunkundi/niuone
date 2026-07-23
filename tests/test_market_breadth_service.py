#!/usr/bin/env python3
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.dashboard.apis.market_breadth import (
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
    fields[30] = quote_time
    fields[32] = str(pct)
    fields[33] = str(high)
    fields[37] = str(amount_wan)
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
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["quote_count"], 1)
        self.assertEqual(result["red"], 1)
        self.assertEqual(result["turnover_increment_yi"], 2)

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


class MarketBreadthHistoryTests(unittest.TestCase):
    def test_midnight_reset_clears_daily_market_files_and_cached_payloads(self):
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
                    "samples": [sample("2026-07-22 15:00:00")],
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

                changed = dashboard.reset_daily_market_histories(
                    datetime(2026, 7, 23, 0, 0, 0)
                )
                repeated = dashboard.reset_daily_market_histories(
                    datetime(2026, 7, 23, 0, 1, 0)
                )

                breadth = json.loads(dashboard.MARKET_BREADTH_HISTORY_FILE.read_text(encoding="utf-8"))
                flow = json.loads(dashboard.INDUSTRY_FLOW_HISTORY_FILE.read_text(encoding="utf-8"))
                money = json.loads(dashboard.MONEY_FLOW_SNAPSHOT_FILE.read_text(encoding="utf-8"))
                self.assertTrue(changed)
                self.assertFalse(repeated)
                self.assertEqual(breadth["date"], "2026-07-23")
                self.assertEqual(breadth["samples"], [])
                self.assertEqual(flow["date"], "2026-07-23")
                self.assertEqual(flow["samples"], [])
                self.assertEqual(money["retention_date"], "2026-07-23")
                self.assertEqual(money["inflow"], [])
                self.assertEqual(money["outflow"], [])
        finally:
            dashboard.MARKET_BREADTH_HISTORY_FILE = original_breadth_file
            dashboard.INDUSTRY_FLOW_HISTORY_FILE = original_flow_file
            dashboard.MONEY_FLOW_SNAPSHOT_FILE = original_money_file

    def test_after_midnight_apis_do_not_refetch_or_publish_yesterday_data(self):
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
                self.assertFalse(breadth_payload["available"])
                self.assertEqual(breadth_payload["timeline"], [])
                self.assertFalse(flow_payload["available"])
                self.assertEqual(flow_payload["nodes"], [])
                self.assertEqual(flow_payload["timeline"], [])
        finally:
            dashboard.MARKET_BREADTH_HISTORY_FILE = original_breadth_file
            dashboard.INDUSTRY_FLOW_HISTORY_FILE = original_flow_file
            dashboard.MONEY_FLOW_SNAPSHOT_FILE = original_money_file

    def test_daily_reset_wait_is_aligned_to_next_beijing_midnight(self):
        self.assertEqual(
            dashboard.seconds_until_next_cn_midnight(datetime(2026, 7, 22, 23, 59, 30)),
            30,
        )
        self.assertEqual(
            dashboard.seconds_until_next_cn_midnight(datetime(2026, 7, 23, 0, 0, 0)),
            24 * 60 * 60,
        )
        self.assertEqual(
            dashboard.seconds_until_next_cn_midnight(
                datetime(2026, 7, 22, 23, 59, 30, tzinfo=dashboard.CN_TZ)
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
        self.assertEqual(payload["timeline"][1]["turnover_increment_yi"], -500)
        self.assertEqual(payload["sampling"]["point_count"], 3)
        self.assertEqual(payload["sampling"]["timezone"], "Asia/Shanghai")
        self.assertEqual(payload["source"], "腾讯证券沪深A股实时行情")
        self.assertEqual(payload["universe"], "沪深A股测试口径")

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
                    return_value=datetime(2026, 7, 22, 10, 0, 30),
                ):
                    payload = dashboard.produce_market_breadth_data()

                fetch.assert_not_called()
                self.assertEqual(payload["latest"]["red"], 3200)
                self.assertEqual(payload["sampling"]["point_count"], 1)
        finally:
            dashboard.MARKET_BREADTH_HISTORY_FILE = original_history_file


if __name__ == "__main__":
    unittest.main()
