from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.market_data.tencent_kline_cache import merge_live_quote
from app.screening import niuone_minute
from app.screening.niuone_minute import NiuOneMinuteEngine
from app.strategies.scoring.common import compute_ema


def history_rows(price: float = 10.0) -> list[dict[str, object]]:
    start = datetime(2026, 5, 1)
    return [
        {
            "date": (start + timedelta(days=index)).strftime("%Y-%m-%d"),
            "open": price,
            "close": price,
            "high": price * 1.01,
            "low": price * 0.99,
            "volume": 100_000,
        }
        for index in range(60)
    ]


def quote_snapshot(generated_at: str, prices: list[float]) -> dict[str, object]:
    quotes = {}
    for index, price in enumerate(prices, start=1):
        code = f"600{index:03d}"
        quotes[f"sh{code}"] = {
            "code": code,
            "name": f"测试股份{index}",
            "price": price,
            "prev_close": 10.0,
            "open": 10.0,
            "high": price,
            "low": 9.9,
            "volume": 100_000 * index,
            "amount": 100_000_000 * index,
            "turnover": float(index),
            "change_pct": (price / 10.0 - 1) * 100,
            "quote_time": generated_at.replace("-", "").replace(" ", "").replace(":", ""),
        }
    changes = [float(quote["change_pct"]) for quote in quotes.values()]
    return {
        "generated_at": generated_at,
        "quote_count": len(quotes),
        "quotes": quotes,
        "market_snapshot": {
            "up": sum(1 for value in changes if value > 0),
            "down": sum(1 for value in changes if value < 0),
            "flat": sum(1 for value in changes if value == 0),
            "median_change_pct": sorted(changes)[len(changes) // 2],
            "limit_up": 0,
            "limit_down": 0,
        },
    }


class NiuOneMinuteRefreshTests(unittest.TestCase):
    def test_batched_worker_preserves_entire_theme_context_and_releases_bars(self):
        snapshot = quote_snapshot("2026-07-30 10:00:00", [10.01, 11, 10.7, 9.6, 10.9])
        now = datetime(2026, 7, 30, 10, 0, 3)
        batches = []
        def load(symbols, **kwargs):
            batches.append(len(symbols))
            return {symbol: history_rows() for symbol in symbols}
        with tempfile.TemporaryDirectory() as tmp:
            options = dict(kline_cache_path=Path(tmp)/'kline', industry_cache_path=Path(tmp)/'boards',
                           kline_loader=load, industry_loader=lambda _: {f'60000{i}':'板块' for i in range(1,6)},
                           trading_day_status_loader=lambda *args, **kwargs: {'previous_trading_day':'2026-07-29'})
            original = NiuOneMinuteEngine(**options).build_scan(snapshot, now=now)
            batches.clear()
            engine = NiuOneMinuteEngine(**options, history_batch_size=2)
            batched = engine.build_scan(snapshot, now=now)
        self.assertEqual(original['niuone_context'], batched['niuone_context'])
        self.assertEqual(batches, [2,2,1])
        self.assertEqual((engine._histories, engine._live_ema_seeds), ({},{}))
    def _capture_prepared_rows(
        self,
        engine: NiuOneMinuteEngine,
        snapshot: dict[str, object],
        *,
        now: datetime,
    ) -> list[dict[str, object]]:
        captured: list[dict[str, object]] = []

        def context_builder(prepared_items, **_kwargs):
            captured.extend(prepared_items)
            return {"data_coverage": 1.0}

        with patch.object(niuone_minute, "build_niuone_context", context_builder):
            engine.build_scan(snapshot, now=now)
        self.assertEqual(len(captured), 1)
        return captured[0]["rows"]

    def test_incremental_ema_matches_full_recalculation_and_reuses_seed(self) -> None:
        start = datetime(2026, 1, 1)
        rows = []
        for index in range(180):
            price = 8.0 + index * 0.07
            rows.append({
                "date": (start + timedelta(days=index)).strftime("%Y-%m-%d"),
                "open": price * 0.99,
                "close": price,
                "high": price * 1.01,
                "low": price * 0.98,
                "volume": 100_000,
            })
        snapshot_one = quote_snapshot("2026-07-30 10:01:00", [16.25])
        snapshot_two = quote_snapshot("2026-07-30 10:02:00", [17.75])

        with tempfile.TemporaryDirectory(prefix="niuone-minute-ema-") as directory:
            root = Path(directory)
            engine = NiuOneMinuteEngine(
                kline_cache_path=root / "klines.sqlite3",
                industry_cache_path=root / "industries.json",
                kline_loader=lambda symbols, **_kwargs: {
                    symbol: rows for symbol in symbols
                },
                industry_loader=lambda _path: {"600001": "半导体"},
                trading_day_status_loader=lambda _value, **_kwargs: {
                    "previous_trading_day": "2026-07-29"
                },
                minimum_coverage=1.0,
            )
            with patch.object(
                niuone_minute,
                "_ema_state_from_rows",
                wraps=niuone_minute._ema_state_from_rows,
            ) as state_builder:
                first_rows = self._capture_prepared_rows(
                    engine,
                    snapshot_one,
                    now=datetime(2026, 7, 30, 10, 1, 5),
                )
                seed_build_count = state_builder.call_count
                second_rows = self._capture_prepared_rows(
                    engine,
                    snapshot_two,
                    now=datetime(2026, 7, 30, 10, 2, 5),
                )

        self.assertEqual(seed_build_count, 1)
        self.assertEqual(state_builder.call_count, seed_build_count)
        for snapshot, prepared_rows in (
            (snapshot_one, first_rows),
            (snapshot_two, second_rows),
        ):
            expected_rows = merge_live_quote(rows, snapshot["quotes"]["sh600001"])
            closes = [float(row["close"]) for row in expected_rows]
            self.assertEqual(len(expected_rows), 120)
            self.assertEqual(prepared_rows[-1]["ema20"], compute_ema(closes, 20)[-1])
            self.assertEqual(prepared_rows[-1]["ema50"], compute_ema(closes, 50)[-1])

    def test_incremental_ema_replaces_a_cached_same_day_bar(self) -> None:
        start = datetime(2026, 4, 2)
        rows = []
        for index in range(120):
            price = 9.0 + index * 0.03
            rows.append({
                "date": (start + timedelta(days=index)).strftime("%Y-%m-%d"),
                "open": price,
                "close": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "volume": 100_000,
            })
        self.assertEqual(rows[-1]["date"], "2026-07-30")
        snapshot = quote_snapshot("2026-07-30 10:01:00", [18.5])

        with tempfile.TemporaryDirectory(prefix="niuone-minute-ema-replace-") as directory:
            root = Path(directory)
            engine = NiuOneMinuteEngine(
                kline_cache_path=root / "klines.sqlite3",
                industry_cache_path=root / "industries.json",
                kline_loader=lambda symbols, **_kwargs: {
                    symbol: rows for symbol in symbols
                },
                industry_loader=lambda _path: {"600001": "半导体"},
                minimum_coverage=1.0,
            )
            prepared_rows = self._capture_prepared_rows(
                engine,
                snapshot,
                now=datetime(2026, 7, 30, 10, 1, 5),
            )

        expected_rows = merge_live_quote(rows, snapshot["quotes"]["sh600001"])
        closes = [float(row["close"]) for row in expected_rows]
        self.assertEqual(len(expected_rows), 120)
        self.assertEqual(prepared_rows[-1]["close"], 18.5)
        self.assertEqual(prepared_rows[-1]["ema20"], compute_ema(closes, 20)[-1])
        self.assertEqual(prepared_rows[-1]["ema50"], compute_ema(closes, 50)[-1])

    def test_missing_live_trade_date_falls_back_to_full_ema(self) -> None:
        rows = history_rows()
        snapshot = quote_snapshot("2026-07-30 10:01:00", [18.5])
        del snapshot["quotes"]["sh600001"]["quote_time"]

        with tempfile.TemporaryDirectory(prefix="niuone-minute-ema-fallback-") as directory:
            root = Path(directory)
            engine = NiuOneMinuteEngine(
                kline_cache_path=root / "klines.sqlite3",
                industry_cache_path=root / "industries.json",
                kline_loader=lambda symbols, **_kwargs: {
                    symbol: rows for symbol in symbols
                },
                industry_loader=lambda _path: {"600001": "半导体"},
                minimum_coverage=1.0,
            )
            with patch.object(
                niuone_minute,
                "_ema_state_from_rows",
                wraps=niuone_minute._ema_state_from_rows,
            ) as state_builder:
                prepared_rows = self._capture_prepared_rows(
                    engine,
                    snapshot,
                    now=datetime(2026, 7, 30, 10, 1, 5),
                )

        closes = [float(row["close"]) for row in merge_live_quote(rows, {})]
        self.assertEqual(state_builder.call_count, 2)
        self.assertEqual(prepared_rows[-1]["ema20"], compute_ema(closes, 20)[-1])
        self.assertEqual(prepared_rows[-1]["ema50"], compute_ema(closes, 50)[-1])

    def test_new_quote_date_reloads_history_and_rebuilds_ema_seed(self) -> None:
        loader_calls = 0

        def kline_loader(symbols, **_kwargs):
            nonlocal loader_calls
            loader_calls += 1
            return {
                symbol: history_rows(10.0 + loader_calls)
                for symbol in symbols
            }

        with tempfile.TemporaryDirectory(prefix="niuone-minute-ema-date-") as directory:
            root = Path(directory)
            engine = NiuOneMinuteEngine(
                kline_cache_path=root / "klines.sqlite3",
                industry_cache_path=root / "industries.json",
                kline_loader=kline_loader,
                industry_loader=lambda _path: {"600001": "半导体"},
                minimum_coverage=1.0,
            )
            first_rows = self._capture_prepared_rows(
                engine,
                quote_snapshot("2026-07-30 14:59:00", [15.0]),
                now=datetime(2026, 7, 30, 14, 59, 5),
            )
            second_rows = self._capture_prepared_rows(
                engine,
                quote_snapshot("2026-07-31 09:31:00", [16.0]),
                now=datetime(2026, 7, 31, 9, 31, 5),
            )

        first_expected = compute_ema([11.0] * 60 + [15.0], 20)[-1]
        second_expected = compute_ema([12.0] * 60 + [16.0], 20)[-1]
        self.assertEqual(loader_calls, 2)
        self.assertEqual(first_rows[-1]["ema20"], first_expected)
        self.assertEqual(second_rows[-1]["ema20"], second_expected)

    def test_recalculates_from_each_fresh_quote_and_reuses_local_history(self) -> None:
        loader_calls: list[list[str]] = []

        def kline_loader(symbols, **_kwargs):
            requested = list(symbols)
            loader_calls.append(requested)
            return {symbol: history_rows() for symbol in requested}

        with tempfile.TemporaryDirectory(prefix="niuone-minute-") as directory:
            root = Path(directory)
            industry_path = root / "industries.json"
            industry_path.write_text("{}", encoding="utf-8")
            engine = NiuOneMinuteEngine(
                kline_cache_path=root / "klines.sqlite3",
                industry_cache_path=industry_path,
                kline_loader=kline_loader,
                industry_loader=lambda _path: {
                    f"600{index:03d}": "半导体" for index in range(1, 5)
                },
                trading_day_status_loader=lambda _value, **_kwargs: {
                    "previous_trading_day": "2026-07-29"
                },
                minimum_coverage=0.75,
            )
            previous_payload = {
                "generated_at": "2026-07-29 15:00:00",
                "reference_pool_count": 4,
                "niuone_context": {
                    "as_of_date": "2026-07-29",
                    "previous_trading_day": "2026-07-28",
                    "dragon_tiger": {"available": True, "as_of_date": "2026-07-29"},
                    "news": {"configured": True, "available": True},
                    "themes": {
                        "半导体": {
                            "industry": "半导体",
                            "state": "emerging",
                            "raw_state": "emerging",
                            "as_of_date": "2026-07-29",
                            "confirmation_component": 7.5,
                            "core_stock_codes": ["600001", "600002"],
                        }
                    },
                },
            }

            first = engine.build_scan(
                quote_snapshot("2026-07-30 10:01:00", [15.0, 14.0, 12.0, 10.5]),
                previous_payload=previous_payload,
                now=datetime(2026, 7, 30, 10, 1, 5),
            )
            second = engine.build_scan(
                quote_snapshot("2026-07-30 10:02:00", [15.5, 14.2, 12.2, 10.6]),
                previous_payload=first,
                now=datetime(2026, 7, 30, 10, 2, 4),
            )

        self.assertEqual(len(loader_calls), 1)
        self.assertEqual(first["quote_generated_at"], "2026-07-30 10:01:00")
        self.assertEqual(second["quote_generated_at"], "2026-07-30 10:02:00")
        first_theme = first["niuone_context"]["themes"]["半导体"]
        second_theme = second["niuone_context"]["themes"]["半导体"]
        self.assertEqual(first_theme["confirmation_component"], 7.5)
        self.assertEqual(second_theme["confirmation_component"], 7.5)
        self.assertEqual(first_theme["strong_stocks"][0]["change_pct"], 50.0)
        self.assertEqual(second_theme["strong_stocks"][0]["change_pct"], 55.0)
        self.assertEqual(second["niuone_context"]["refresh_mode"], "minute_quotes")

    def test_does_not_treat_a_stale_context_as_the_previous_trading_day(self) -> None:
        accepted_dates: list[set[str]] = []

        def kline_loader(symbols, **kwargs):
            accepted_dates.append(set(kwargs.get("accepted_last_dates") or set()))
            return {symbol: history_rows() for symbol in symbols}

        with tempfile.TemporaryDirectory(prefix="niuone-minute-gap-") as directory:
            root = Path(directory)
            engine = NiuOneMinuteEngine(
                kline_cache_path=root / "klines.sqlite3",
                industry_cache_path=root / "industries.json",
                kline_loader=kline_loader,
                industry_loader=lambda _path: {
                    f"600{index:03d}": "半导体" for index in range(1, 5)
                },
                trading_day_status_loader=lambda _value, **_kwargs: {
                    "previous_trading_day": "2026-07-29"
                },
                minimum_coverage=0.75,
            )
            previous_payload = {
                "generated_at": "2026-07-28 15:00:00",
                "reference_pool_count": 4,
                "niuone_context": {
                    "as_of_date": "2026-07-28",
                    "previous_trading_day": "2026-07-27",
                    "themes": {
                        "半导体": {
                            "industry": "半导体",
                            "state": "emerging",
                            "raw_state": "mainline",
                            "as_of_date": "2026-07-28",
                            "core_stock_codes": ["600001", "600002"],
                        }
                    },
                },
            }

            scan = engine.build_scan(
                quote_snapshot("2026-07-30 10:01:00", [15.0, 14.0, 12.0, 10.5]),
                previous_payload=previous_payload,
                now=datetime(2026, 7, 30, 10, 1, 5),
            )

        theme = scan["niuone_context"]["themes"]["半导体"]
        self.assertEqual(accepted_dates, [{"2026-07-29", "2026-07-30"}])
        self.assertFalse(theme["consecutive_trading_day"])
        self.assertFalse(theme["cross_day_confirmed"])
        self.assertNotEqual(theme["state"], "mainline")

    def test_rejects_stale_quotes_without_producing_a_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="niuone-minute-stale-") as directory:
            root = Path(directory)
            engine = NiuOneMinuteEngine(
                kline_cache_path=root / "klines.sqlite3",
                industry_cache_path=root / "industries.json",
                kline_loader=lambda symbols, **_kwargs: {
                    symbol: history_rows() for symbol in symbols
                },
                industry_loader=lambda _path: {
                    f"600{index:03d}": "半导体" for index in range(1, 5)
                },
            )
            with self.assertRaisesRegex(ValueError, "stale"):
                engine.build_scan(
                    quote_snapshot("2026-07-30 09:59:00", [15.0, 14.0, 12.0, 10.5]),
                    now=datetime(2026, 7, 30, 10, 2, 1),
                )


if __name__ == "__main__":
    unittest.main()
