from __future__ import annotations

import threading
import time
import unittest

from app.backtesting.historical_data import (
    DEFAULT_HISTORICAL_SOURCE_PRIORITY,
    HistoricalDataError,
    HistoricalFetchConfig,
    fetch_historical_data,
    fetch_historical_series,
    normalize_a_share_symbol,
    parse_eastmoney_daily_klines,
    parse_sina_daily_klines,
    parse_tencent_daily_klines,
)


class HistoricalMarketDataTests(unittest.TestCase):
    def test_default_source_priority_prefers_eastmoney_then_tencent_then_sina(self):
        self.assertEqual(
            DEFAULT_HISTORICAL_SOURCE_PRIORITY,
            ("eastmoney", "tencent", "sina"),
        )
        self.assertEqual(
            HistoricalFetchConfig(adjustment="none").sources,
            ("eastmoney", "tencent", "sina"),
        )

    def test_normalizes_a_share_symbols(self):
        self.assertEqual(normalize_a_share_symbol("600519"), "sh600519")
        self.assertEqual(normalize_a_share_symbol("SZ.000001"), "sz000001")
        self.assertEqual(normalize_a_share_symbol("830799"), "bj830799")
        with self.assertRaises(HistoricalDataError):
            normalize_a_share_symbol("AAPL")

    def test_parses_eastmoney_amount_turnover_and_range(self):
        body = (
            '{"data":{"klines":['
            '"2026-01-01,10,10.1,10.2,9.9,1000,100000,0,0,0,1.5",'
            '"2026-01-02,10.2,10.3,10.4,10.1,1200,120000,0,0,0,1.8",'
            '"2026-01-03,10.4,10.5,10.6,10.3,1300,130000,0,0,0,2.0"'
            ']}}'
        )
        rows = parse_eastmoney_daily_klines(
            body, "600519", "2026-01-02", "2026-01-03", "qfq"
        )
        self.assertEqual([row["date"] for row in rows], ["2026-01-02", "2026-01-03"])
        self.assertEqual(rows[0]["amount"], 120000.0)
        self.assertEqual(rows[0]["turnover"], 1.8)
        self.assertEqual(rows[1]["previous_close"], 10.3)
        self.assertEqual(rows[0]["data_source"], "eastmoney")

    def test_parses_tencent_adjusted_rows(self):
        body = (
            '{"data":{"sh600519":{"qfqday":['
            '["2026-01-02","10","10.2","10.3","9.9","1000"],'
            '["2026-01-03","10.2","10.4","10.5","10.1","1200"]'
            ']}}}'
        )
        rows = parse_tencent_daily_klines(
            body, "sh600519", "2026-01-01", "2026-01-03", "qfq"
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["close"], 10.4)
        self.assertEqual(rows[-1]["previous_close"], 10.2)
        self.assertEqual(rows[-1]["adjustment"], "qfq")

        unadjusted_only = (
            '{"data":{"sh600519":{"day":'
            '[["2026-01-02","10","10.2","10.3","9.9","1000"]]}}}'
        )
        with self.assertRaisesRegex(HistoricalDataError, "unadjusted"):
            parse_tencent_daily_klines(
                unadjusted_only, "sh600519", "2026-01-01", "2026-01-03", "qfq"
            )

    def test_sina_parser_accepts_unadjusted_jsonp_only(self):
        body = (
            'var _sh600519_niuone=('
            '[{"day":"2026-01-02","open":"10",'
            '"high":"10.3","low":"9.9","close":"10.2","volume":"1000"}]'
            ');'
        )
        with self.assertRaisesRegex(HistoricalDataError, "does not provide"):
            parse_sina_daily_klines(
                body, "sh600519", "2026-01-01", "2026-01-03", "qfq"
            )
        rows = parse_sina_daily_klines(
            body, "sh600519", "2026-01-01", "2026-01-03", "none"
        )
        self.assertEqual(rows[0]["close"], 10.2)
        self.assertEqual(rows[0]["data_source"], "sina")

    def test_retries_then_falls_back_with_provenance(self):
        calls = {"eastmoney": 0, "tencent": 0}

        def eastmoney(*_args):
            calls["eastmoney"] += 1
            raise TimeoutError("temporary")

        def tencent(_symbol, _start, _end, _adjustment, _timeout):
            calls["tencent"] += 1
            return [{
                "date": "2026-01-02", "open": 10, "high": 10.2,
                "low": 9.9, "close": 10.1, "volume": 100,
            }]

        result = fetch_historical_series(
            "600519",
            "2026-01-01",
            "2026-01-03",
            config=HistoricalFetchConfig(
                sources=("eastmoney", "tencent"),
                max_attempts_per_source=2,
                retry_backoff_seconds=0,
            ),
            source_fetchers={"eastmoney": eastmoney, "tencent": tencent},
        )
        self.assertEqual(calls, {"eastmoney": 2, "tencent": 1})
        self.assertEqual(result.source, "tencent")
        self.assertEqual(len(result.attempts), 2)
        self.assertIn("TimeoutError", result.attempts[0]["error"])

    def test_deterministic_short_history_falls_back_without_retry(self):
        calls = {"eastmoney": 0, "tencent": 0}

        def row(trading_date):
            return {
                "date": trading_date, "open": 10, "high": 10.2,
                "low": 9.9, "close": 10.1, "volume": 100,
            }

        def eastmoney(*_args):
            calls["eastmoney"] += 1
            return [row("2026-01-02")]

        def tencent(*_args):
            calls["tencent"] += 1
            return [row("2026-01-02"), row("2026-01-03")]

        result = fetch_historical_series(
            "600519",
            "2026-01-01",
            "2026-01-03",
            config=HistoricalFetchConfig(
                sources=("eastmoney", "tencent"),
                minimum_rows=2,
                max_attempts_per_source=2,
                retry_backoff_seconds=0,
            ),
            source_fetchers={"eastmoney": eastmoney, "tencent": tencent},
        )
        self.assertEqual(calls, {"eastmoney": 1, "tencent": 1})
        self.assertEqual(result.source, "tencent")
        self.assertEqual(len(result.attempts), 1)

    def test_fetched_rows_are_immutable_and_reuse_repeated_metadata(self):
        def fetcher(symbol, *_args):
            return [{
                "date": "2026-01-02",
                "open": 10,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "volume": 100,
                "custom": symbol,
            }]

        result = fetch_historical_data(
            ["600000", "000001"],
            "2026-01-01",
            "2026-01-03",
            config=HistoricalFetchConfig(
                sources=("eastmoney",),
                max_attempts_per_source=1,
                max_workers=2,
            ),
            source_fetchers={"eastmoney": fetcher},
        )
        first = result.series["sh600000"].bars[0]
        second = result.series["sz000001"].bars[0]

        self.assertEqual(
            dict(first),
            {
                "date": "2026-01-02",
                "open": 10.0,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "volume": 100.0,
                "symbol": "sh600000",
                "data_source": "eastmoney",
                "adjustment": "qfq",
            },
        )
        self.assertEqual(
            list(first),
            [
                "symbol",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "data_source",
                "adjustment",
            ],
        )
        self.assertIs(first["date"], second["date"])
        self.assertIs(first["data_source"], second["data_source"])
        self.assertIs(first["adjustment"], second["adjustment"])
        with self.assertRaises(TypeError):
            first["close"] = 0

    def test_automatic_batch_opens_unhealthy_primary_source_circuit(self):
        calls = {"eastmoney": 0, "tencent": 0}
        symbols = [f"6000{index:02d}" for index in range(8)]

        def eastmoney(*_args):
            calls["eastmoney"] += 1
            raise TimeoutError("primary unavailable")

        def tencent(*_args):
            calls["tencent"] += 1
            return [{
                "date": "2026-01-02", "open": 10, "high": 10.2,
                "low": 9.9, "close": 10.1, "volume": 100,
            }]

        result = fetch_historical_data(
            symbols,
            "2026-01-01",
            "2026-01-03",
            config=HistoricalFetchConfig(
                sources=("eastmoney", "tencent"),
                max_attempts_per_source=1,
                max_workers=1,
                strict=False,
                source_circuit_min_samples=4,
                source_circuit_failure_ratio=1.0,
            ),
            source_fetchers={"eastmoney": eastmoney, "tencent": tencent},
        )
        self.assertEqual(calls, {"eastmoney": 4, "tencent": 8})
        self.assertEqual(len(result.series), 8)
        self.assertEqual(set(result.source_by_symbol.values()), {"tencent"})
        self.assertEqual(
            result.series["sh600004"].attempts[0]["error"],
            "run_source_circuit_open",
        )

    def test_single_explicit_source_never_uses_run_circuit(self):
        calls = []

        def eastmoney(symbol, *_args):
            calls.append(symbol)
            raise TimeoutError("unavailable")

        result = fetch_historical_data(
            [f"6000{index:02d}" for index in range(6)],
            "2026-01-01",
            "2026-01-03",
            config=HistoricalFetchConfig(
                sources=("eastmoney",),
                max_attempts_per_source=1,
                max_workers=2,
                strict=False,
                source_circuit_min_samples=4,
                source_circuit_failure_ratio=1.0,
            ),
            source_fetchers={"eastmoney": eastmoney},
        )
        self.assertEqual(len(calls), 6)
        self.assertEqual(len(result.failures), 6)

    def test_batch_cancellation_does_not_wait_for_queued_requests(self):
        started = threading.Event()
        cancelled = threading.Event()
        release = threading.Event()
        calls = []

        class FetchCancelled(RuntimeError):
            pass

        def fetcher(symbol, *_args):
            calls.append(symbol)
            started.set()
            release.wait(timeout=2)
            return [{
                "date": "2026-01-02", "open": 10, "high": 10.2,
                "low": 9.9, "close": 10.1, "volume": 100,
            }]

        def cancellation_check():
            if cancelled.is_set():
                raise FetchCancelled("cancelled")

        def trigger_cancel():
            if started.wait(timeout=1):
                cancelled.set()

        trigger = threading.Thread(target=trigger_cancel)
        trigger.start()
        began = time.monotonic()
        try:
            with self.assertRaises(FetchCancelled):
                fetch_historical_data(
                    [f"{600000 + index:06d}" for index in range(40)],
                    "2026-01-01",
                    "2026-01-03",
                    config=HistoricalFetchConfig(
                        sources=("eastmoney",),
                        max_attempts_per_source=1,
                        max_workers=4,
                        strict=False,
                    ),
                    source_fetchers={"eastmoney": fetcher},
                    cancellation_check=cancellation_check,
                )
            self.assertLess(time.monotonic() - began, 1.0)
            self.assertLessEqual(len(calls), 4)
        finally:
            release.set()
            trigger.join(timeout=1)

    def test_batch_refuses_partial_universe_by_default(self):
        def fetcher(symbol, *_args):
            if symbol == "sh600000":
                raise TimeoutError("missing")
            return [{
                "date": "2026-01-02", "open": 10, "high": 10,
                "low": 10, "close": 10, "volume": 100,
            }]

        with self.assertRaisesRegex(HistoricalDataError, "incomplete"):
            fetch_historical_data(
                ["600000", "000001"],
                "2026-01-01",
                "2026-01-03",
                config=HistoricalFetchConfig(
                    sources=("eastmoney",),
                    max_attempts_per_source=1,
                ),
                source_fetchers={"eastmoney": fetcher},
            )

    def test_rejects_unbounded_date_ranges_before_requesting_source(self):
        called = []

        def fetcher(*_args):
            called.append(True)
            return []

        with self.assertRaisesRegex(HistoricalDataError, "max_calendar_days"):
            fetch_historical_series(
                "600519",
                "2026-01-01",
                "2026-02-01",
                config=HistoricalFetchConfig(
                    sources=("eastmoney",),
                    max_calendar_days=10,
                ),
                source_fetchers={"eastmoney": fetcher},
            )
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
