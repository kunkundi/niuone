#!/usr/bin/env python3
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from app.market_data import tencent_kline_cache as cache


def sample_rows(last_day: str = "2026-07-28", count: int = 60) -> list[dict]:
    year, month, day = [int(value) for value in last_day.split("-")]
    rows = []
    for index in range(count):
        current_day = max(1, day - count + index + 1)
        date_text = f"{year:04d}-{month:02d}-{current_day:02d}"
        price = 10 + index / 100
        rows.append({
            "date": date_text,
            "open": price,
            "close": price + 0.02,
            "high": price + 0.05,
            "low": price - 0.05,
            "volume": 1000 + index,
        })
    # The cache only requires ordered ISO dates; use unique earlier months for
    # synthetic histories that would otherwise underflow the calendar month.
    for index, row in enumerate(rows):
        row["date"] = f"2026-{5 + index // 28:02d}-{index % 28 + 1:02d}"
    rows[-1]["date"] = last_day
    return rows


class TencentKlineCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="niuone-kline-cache-")
        self.path = Path(self.temp.name) / "daily.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_tencent_fetch_uses_web_endpoint_then_compatibility_alias(self):
        payload = json.dumps({
            "data": {
                "sh600519": {
                    "qfqday": [
                        ["2026-07-28", "10", "10.1", "10.2", "9.9", "1000"],
                    ],
                },
            },
        }).encode("utf-8")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return payload

        with mock.patch.object(
            cache.urllib.request,
            "urlopen",
            side_effect=[OSError("primary unavailable"), Response()],
        ) as urlopen:
            rows = cache.fetch_tencent_daily_klines("sh600519", 120)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-07-28")
        requested_urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertEqual(
            [url.split("?", 1)[0] for url in requested_urls],
            list(cache.TENCENT_KLINE_URLS[:2]),
        )

    def test_eastmoney_fetch_parses_qfq_daily_rows(self):
        payload = json.dumps({
            "data": {
                "klines": [
                    "2026-07-28,10,10.1,10.2,9.9,1000,100000,0,0,0,1.2",
                    "2026-07-29,10.2,10.3,10.4,10.1,1200,120000,0,0,0,1.3",
                ],
            },
        }).encode("utf-8")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return payload

        with mock.patch.object(cache.urllib.request, "urlopen", return_value=Response()) as urlopen:
            rows = cache.fetch_eastmoney_daily_klines("sh600519", 120)

        self.assertEqual([row["date"] for row in rows], ["2026-07-28", "2026-07-29"])
        self.assertEqual(rows[-1]["close"], 10.3)
        self.assertIn("secid=1.600519", urlopen.call_args.args[0].full_url)
        self.assertIn("fqt=1", urlopen.call_args.args[0].full_url)

    def test_multi_source_fetch_falls_back_from_tencent_to_eastmoney(self):
        expected = sample_rows()
        with (
            mock.patch.object(cache, "fetch_tencent_daily_klines", return_value=[]) as tencent,
            mock.patch.object(cache, "fetch_eastmoney_daily_klines", return_value=expected) as eastmoney,
        ):
            rows = cache.fetch_a_share_daily_klines("sh600519", 120)

        self.assertEqual(rows, expected)
        tencent.assert_called_once_with("sh600519", 120)
        eastmoney.assert_called_once_with("sh600519", 120)

    def test_prewarm_defaults_to_multi_source_fetcher(self):
        with mock.patch.object(
            cache,
            "fetch_a_share_daily_klines",
            return_value=sample_rows(),
        ) as fetcher:
            result = cache.prewarm_kline_cache(
                ["sh600519"],
                path=self.path,
                target_date="2026-07-29",
                workers=1,
                max_attempts=1,
            )

        self.assertEqual(result["success_count"], 1)
        fetcher.assert_called_once_with("sh600519", cache.DEFAULT_KLINE_COUNT)

    def test_store_and_bulk_load_only_accept_fresh_completed_history(self):
        stored = cache.store_kline_series(
            {"sh600001": sample_rows(), "sz000001": sample_rows("2026-07-25")},
            path=self.path,
            fetched_at="2026-07-29 09:10:00",
        )

        self.assertEqual(stored, 2)
        loaded = cache.load_kline_series_map(
            ["sh600001", "sz000001"],
            path=self.path,
            accepted_last_dates={"2026-07-28"},
            min_rows=55,
        )
        self.assertEqual(list(loaded), ["sh600001"])
        self.assertEqual(loaded["sh600001"][-1]["date"], "2026-07-28")

    def test_failed_refresh_preserves_previous_valid_series(self):
        original = sample_rows()
        cache.store_kline_series({"sh600001": original}, path=self.path)
        cache.record_kline_failures({"sh600001": "timeout"}, path=self.path)

        loaded = cache.load_kline_series_map(
            ["sh600001"],
            path=self.path,
            accepted_last_dates={"2026-07-28"},
            min_rows=55,
        )
        self.assertEqual(len(loaded["sh600001"]), 60)
        self.assertEqual(loaded["sh600001"][-1]["close"], original[-1]["close"])

    def test_lists_cached_symbols_without_loading_history_payloads(self):
        cache.store_kline_series(
            {"sh600001": sample_rows(), "sz000001": sample_rows(count=20)},
            path=self.path,
        )

        self.assertEqual(
            cache.load_cached_kline_symbols(path=self.path, min_rows=30),
            ("sh600001",),
        )

    def test_merge_live_quote_appends_or_replaces_without_mutating_cache(self):
        historical = sample_rows()
        original_close = historical[-1]["close"]
        quote = {
            "quote_time": "20260729100501",
            "open": 11.0,
            "price": 11.5,
            "high": 11.8,
            "low": 10.9,
            "volume": 8888,
        }

        merged = cache.merge_live_quote(historical, quote)
        replaced = cache.merge_live_quote(merged, {**quote, "price": 11.7, "high": 12.0})

        self.assertEqual(historical[-1]["close"], original_close)
        self.assertEqual(merged[-1]["date"], "2026-07-29")
        self.assertEqual(merged[-1]["close"], 11.5)
        self.assertEqual(replaced[-1]["close"], 11.7)
        self.assertEqual(len(replaced), len(merged))

        iso_merged = cache.merge_live_quote(
            historical,
            {**quote, "quote_time": "2026-07-29 10:05:01", "price": 11.6},
        )
        self.assertEqual(iso_merged[-1]["date"], "2026-07-29")
        self.assertEqual(iso_merged[-1]["close"], 11.6)

    def test_merge_keeps_five_hundred_closed_bars_plus_live_buffer(self):
        start = date(2024, 1, 1)
        historical = [
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "open": 10.0,
                "close": 10.0,
                "high": 10.2,
                "low": 9.8,
                "volume": 1000,
            }
            for index in range(500)
        ]
        quote_day = (start + timedelta(days=500)).strftime("%Y%m%d")

        merged = cache.merge_live_quote(
            historical,
            {"quote_time": quote_day + "100000", "price": 10.1},
            limit=501,
        )

        self.assertEqual(len(merged), 501)
        self.assertEqual(sum(row.get("bar_status") == "live" for row in merged), 1)

    def test_prewarm_records_coverage_and_keeps_successes(self):
        def fetcher(symbol, _count):
            if symbol == "sz000002":
                return []
            return sample_rows()

        result = cache.prewarm_kline_cache(
            ["sh600001", "sz000001", "sz000002"],
            path=self.path,
            target_date="2026-07-29",
            workers=3,
            max_attempts=1,
            fetcher=fetcher,
        )

        self.assertEqual(result["success_count"], 2)
        self.assertEqual(result["failure_count"], 1)
        self.assertFalse(
            cache.prewarm_completed_for_date(
                "2026-07-29", path=self.path, minimum_coverage=0.90
            )
        )
        self.assertTrue(
            cache.prewarm_completed_for_date(
                "2026-07-29", path=self.path, minimum_coverage=0.60
            )
        )

        readiness = cache.kline_cache_readiness(
            accepted_last_dates={"2026-07-28"},
            path=self.path,
            minimum_coverage=0.60,
        )
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["requested_count"], 3)
        self.assertEqual(readiness["completed_count"], 3)
        self.assertEqual(readiness["fresh_count"], 2)
        self.assertEqual(readiness["coverage"], 0.6667)

    def test_resume_fetches_only_missing_or_stale_symbols(self):
        cache.store_kline_series(
            {"sh600001": sample_rows("2026-07-28")},
            path=self.path,
        )
        calls = []

        def fetcher(symbol, _count):
            calls.append(symbol)
            return sample_rows("2026-07-28")

        result = cache.prewarm_kline_cache(
            ["sh600001", "sz000001"],
            path=self.path,
            target_date="2026-07-29",
            accepted_last_dates={"2026-07-28"},
            workers=2,
            max_attempts=1,
            fetcher=fetcher,
        )

        self.assertEqual(calls, ["sz000001"])
        self.assertEqual(result["requested_count"], 2)
        self.assertEqual(result["reused_count"], 1)
        self.assertEqual(result["success_count"], 2)

    def test_coverage_counts_only_expected_symbols_and_rejects_legacy_partial_run(self):
        import sqlite3
        from contextlib import closing
        day='2026-07-29'
        cache.prewarm_kline_cache(['sh600001','sz000001'],path=self.path,target_date=day,workers=1,
                                 max_attempts=1,fetcher=lambda s,_: sample_rows() if s.startswith('sh') else [])
        cache.store_kline_series({'sh600002':sample_rows()},path=self.path)
        result=cache.kline_cache_readiness(accepted_last_dates={'2026-07-28'},path=self.path)
        self.assertFalse(result['ready'])
        self.assertEqual((result['fresh_count'],result['coverage']), (1,0.5))
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('DELETE FROM prewarm_targets');conn.execute('UPDATE prewarm_runs SET requested_count=1,success_count=1');conn.commit()
        self.assertFalse(cache.prewarm_completed_for_date(day,path=self.path))
        self.assertFalse(cache.kline_cache_readiness(accepted_last_dates={'2026-07-28'},path=self.path)['ready'])

    def test_resume_refetches_previous_session_intraday_bar_and_rejects_stale_provider(self):
        cache.store_kline_series({'sh600001':sample_rows('2026-07-28')},path=self.path,fetched_at='2026-07-28 10:00:00')
        calls=[]
        result=cache.prewarm_kline_cache(['sh600001'],path=self.path,target_date='2026-07-29',workers=1,
                                        accepted_last_dates={'2026-07-28'},max_attempts=1,
                                        fetcher=lambda s,_: calls.append(s) or sample_rows('2026-07-27'))
        self.assertEqual(calls,['sh600001'])
        self.assertEqual((result['success_count'],result['failure_count']), (0,1))
        self.assertFalse(cache.prewarm_completed_for_date('2026-07-29',path=self.path))
        self.assertFalse(cache.kline_cache_readiness(accepted_last_dates={'2026-07-28'},path=self.path)['ready'])
    def test_aggregate_failure_is_visible_without_deleting_cache(self):
        cache.prewarm_kline_cache(
            ["sh600001"],
            path=self.path,
            target_date="2026-07-29",
            workers=1,
            max_attempts=1,
            fetcher=lambda *_args: sample_rows(),
        )

        cache.mark_prewarm_run_failed(
            "2026-07-29",
            "aggregate_timeout",
            path=self.path,
        )
        readiness = cache.kline_cache_readiness(
            accepted_last_dates={"2026-07-28"},
            path=self.path,
        )

        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["status"], "error")
        self.assertEqual(readiness["error_code"], "")
        self.assertEqual(readiness["fresh_count"], 1)

    def test_aggregate_failure_is_recorded_before_any_series_exists(self):
        cache.mark_prewarm_run_failed(
            "2026-07-29",
            "prewarm_process_failed",
            path=self.path,
        )

        readiness = cache.kline_cache_readiness(
            accepted_last_dates={"2026-07-28"},
            path=self.path,
        )

        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["status"], "error")
        self.assertEqual(readiness["error_code"], "prewarm_process_failed")


if __name__ == "__main__":
    unittest.main()
