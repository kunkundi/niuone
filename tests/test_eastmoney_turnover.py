#!/usr/bin/env python3
import datetime as dt
import json
import unittest

from app.market_data import eastmoney_turnover


def kline_body(secid: str, rows: list[str]) -> str:
    return json.dumps({
        "data": {
            "code": secid.split(".", 1)[-1],
            "klines": rows,
        },
    })


def profile_series(day_count: int = 21) -> dict[str, dict[str, dict[float, float]]]:
    result = {secid: {} for secid in eastmoney_turnover.SECIDS}
    start = dt.date(2026, 6, 1)
    for offset in range(day_count):
        day = (start + dt.timedelta(days=offset)).isoformat()
        for secid in eastmoney_turnover.SECIDS:
            result[secid][day] = {
                float(progress): 100_000_000.0
                for progress in range(5, 241, 5)
            }
    return result


def profile_bodies(day_count: int = 21) -> dict[str, str]:
    series = profile_series(day_count)
    bodies = {}
    for secid, by_date in series.items():
        rows = []
        for day, amounts in by_date.items():
            for progress, amount in amounts.items():
                if progress <= 120:
                    minute = 9 * 60 + 30 + int(progress)
                else:
                    minute = 13 * 60 + int(progress - 120)
                timestamp = f"{day} {minute // 60:02d}:{minute % 60:02d}"
                rows.append(f"{timestamp},1,1,1,1,10,{amount:.2f},0.1")
        bodies[secid] = kline_body(secid, rows)
    return bodies


class EastmoneyTurnoverTests(unittest.TestCase):
    def test_parses_minute_amount_field_and_trading_progress(self):
        body = kline_body("1.000001", [
            "2026-07-23 09:31,1,1,1,1,10,120000000.00,0.1",
            "2026-07-23 13:05,1,1,1,1,10,230000000.00,0.1",
            "invalid,row",
        ])

        result = eastmoney_turnover.parse_kline_amounts(body, "1.000001")

        self.assertEqual(result["2026-07-23"][1.0], 120_000_000)
        self.assertEqual(result["2026-07-23"][125.0], 230_000_000)

    def test_builds_latest_twenty_complete_common_days(self):
        series = profile_series()

        profile = eastmoney_turnover.build_turnover_profile(
            series,
            dt.date(2026, 7, 1),
        )

        self.assertEqual(profile["profile_days"], 20)
        self.assertEqual(profile["profile_start"], "2026-06-02")
        self.assertEqual(profile["profile_end"], "2026-06-21")
        self.assertEqual(len(profile["daily_profiles"]), 20)
        self.assertEqual(profile["daily_profiles"][-1]["turnover_yi"], 96)

    def test_rejects_profile_when_twenty_complete_days_are_unavailable(self):
        with self.assertRaisesRegex(ValueError, "requires 20"):
            eastmoney_turnover.build_turnover_profile(
                profile_series(19),
                dt.date(2026, 7, 1),
            )

    def test_profile_fetch_is_cached_once_per_trading_day(self):
        bodies = profile_bodies()
        calls = []
        eastmoney_turnover._PROFILE_CACHE.clear()
        try:
            def downloader(secid, interval, limit, timeout):
                calls.append((secid, interval, limit, timeout))
                return bodies[secid]

            first = eastmoney_turnover.fetch_turnover_profile(
                dt.date(2026, 7, 1),
                downloader=downloader,
                monotonic=lambda: 100.0,
            )
            second = eastmoney_turnover.fetch_turnover_profile(
                dt.date(2026, 7, 1),
                downloader=downloader,
                monotonic=lambda: 200.0,
            )
        finally:
            eastmoney_turnover._PROFILE_CACHE.clear()

        self.assertEqual(first["profile_days"], 20)
        self.assertEqual(second["profile_end"], first["profile_end"])
        self.assertEqual(len(calls), 2)

    def test_profile_failure_waits_before_retrying(self):
        calls = []
        eastmoney_turnover._PROFILE_CACHE.clear()
        try:
            def downloader(secid, _interval, _limit, _timeout):
                calls.append(secid)
                raise TimeoutError("upstream timeout")

            with self.assertRaises(TimeoutError):
                eastmoney_turnover.fetch_turnover_profile(
                    dt.date(2026, 7, 1),
                    downloader=downloader,
                    monotonic=lambda: 100.0,
                )
            with self.assertRaisesRegex(RuntimeError, "waiting to retry"):
                eastmoney_turnover.fetch_turnover_profile(
                    dt.date(2026, 7, 1),
                    downloader=downloader,
                    monotonic=lambda: 101.0,
                )
        finally:
            eastmoney_turnover._PROFILE_CACHE.clear()

        self.assertEqual(len(calls), 2)

    def test_estimate_uses_twenty_day_profile_after_first_five_minutes(self):
        profile = eastmoney_turnover.build_turnover_profile(
            profile_series(),
            dt.date(2026, 7, 1),
        )

        result = eastmoney_turnover.estimate_full_day_turnover_yi(
            5_000,
            dt.datetime(2026, 7, 1, 10, 30),
            profile,
        )

        self.assertEqual(result, 20_000)

    def test_first_five_minutes_use_a_stable_auction_prior(self):
        auction_profile = {"opening_estimated_turnover_yi": 96}
        intraday_profile = eastmoney_turnover.build_turnover_profile(
            profile_series(),
            dt.date(2026, 7, 1),
        )

        at_open = eastmoney_turnover.estimate_full_day_turnover_yi(
            1,
            dt.datetime(2026, 7, 1, 9, 30),
            auction_profile,
        )
        before_first_bucket = eastmoney_turnover.estimate_full_day_turnover_yi(
            1.8,
            dt.datetime(2026, 7, 1, 9, 34),
            auction_profile,
        )
        at_first_bucket = eastmoney_turnover.estimate_full_day_turnover_yi(
            2,
            dt.datetime(2026, 7, 1, 9, 35),
            intraday_profile,
        )

        self.assertEqual(at_open, 96)
        self.assertEqual(before_first_bucket, 96)
        self.assertEqual(at_first_bucket, 96)

    def test_auction_prior_does_not_read_current_actual_as_a_factor(self):
        profile = {"opening_estimated_turnover_yi": 96}

        result = eastmoney_turnover.estimate_full_day_turnover_yi(
            120,
            dt.datetime(2026, 7, 1, 9, 34),
            profile,
        )

        self.assertEqual(result, 96)

    def test_opening_prior_uses_the_auction_factor_estimate(self):
        profile = {"opening_estimated_turnover_yi": 120}

        result = eastmoney_turnover.estimate_full_day_turnover_yi(
            1,
            dt.datetime(2026, 7, 1, 9, 31),
            profile,
        )

        self.assertEqual(result, 120)

    def test_current_turnover_sums_eastmoney_shanghai_and_shenzhen(self):
        bodies = {
            secid: kline_body(secid, [
                "2026-07-23 09:31,1,1,1,1,10,100000000.00,0.1",
                "2026-07-23 09:32,1,1,1,1,10,150000000.00,0.1",
            ])
            for secid in eastmoney_turnover.SECIDS
        }

        result = eastmoney_turnover.fetch_current_turnover_yi(
            dt.datetime(2026, 7, 23, 9, 32),
            downloader=lambda secid, _interval, _limit, _timeout: bodies[secid],
        )

        self.assertEqual(result, 5)

    def test_estimate_prefers_eastmoney_current_turnover(self):
        profile = eastmoney_turnover.build_turnover_profile(
            profile_series(),
            dt.date(2026, 7, 1),
        )
        profile["opening_estimated_turnover_yi"] = 19_200

        result = eastmoney_turnover.fetch_market_turnover_estimate(
            dt.datetime(2026, 7, 1, 10, 30),
            9_999,
            profile_fetcher=lambda _date: profile,
            auction_profile_fetcher=lambda _date: (_ for _ in ()).throw(
                AssertionError("auction profile should not be fetched")
            ),
            current_fetcher=lambda _moment: 4_800,
        )

        self.assertEqual(result["actual_turnover_yi"], 4_800)
        self.assertEqual(result["estimated_turnover_yi"], 19_200)
        self.assertEqual(result["turnover_actual_source"], eastmoney_turnover.SOURCE_NAME)
        self.assertEqual(result["turnover_profile_days"], 20)

    def test_opening_estimate_uses_auction_profile_without_eastmoney_profile(self):
        auction_profile = {
            "model": eastmoney_turnover.ESTIMATE_MODEL,
            "model_label": eastmoney_turnover.ESTIMATE_MODEL_LABEL,
            "source": "测试竞价记录",
            "source_url": "https://example.test/auction",
            "profile_days": 11,
            "profile_start": "2026-06-15",
            "profile_end": "2026-06-30",
            "opening_estimated_turnover_yi": 12_000,
            "daily_profiles": [
                {"date": "2026-06-30", "turnover_yi": 10_000},
            ],
        }

        result = eastmoney_turnover.fetch_market_turnover_estimate(
            dt.datetime(2026, 7, 1, 9, 31),
            100,
            profile_fetcher=lambda _date: (_ for _ in ()).throw(
                AssertionError("five-minute profile should not be fetched")
            ),
            auction_profile_fetcher=lambda _date: auction_profile,
            current_fetcher=lambda _moment: 200,
        )

        self.assertEqual(result["estimated_turnover_yi"], 12_000)
        self.assertEqual(result["turnover_profile_days"], 11)
        self.assertNotIn("turnover_profile_interval_minutes", result)
        self.assertNotIn("previous_turnover_yi", result)
        self.assertNotIn("turnover_increment_yi", result)
        self.assertNotIn("turnover_comparison_date", result)

    def test_estimate_falls_back_to_tencent_current_turnover(self):
        profile = eastmoney_turnover.build_turnover_profile(
            profile_series(),
            dt.date(2026, 7, 1),
        )
        profile["opening_estimated_turnover_yi"] = 20_000

        result = eastmoney_turnover.fetch_market_turnover_estimate(
            dt.datetime(2026, 7, 1, 10, 30),
            5_000,
            profile_fetcher=lambda _date: profile,
            auction_profile_fetcher=lambda _date: (_ for _ in ()).throw(
                AssertionError("auction profile should not be fetched")
            ),
            current_fetcher=lambda _moment: (_ for _ in ()).throw(TimeoutError()),
        )

        self.assertEqual(result["actual_turnover_yi"], 5_000)
        self.assertEqual(result["estimated_turnover_yi"], 20_000)
        self.assertEqual(
            result["turnover_actual_source"],
            eastmoney_turnover.FALLBACK_SOURCE_NAME,
        )

    def test_profile_failure_keeps_actual_turnover_without_linear_projection(self):
        result = eastmoney_turnover.fetch_market_turnover_estimate(
            dt.datetime(2026, 7, 1, 10, 30),
            5_000,
            profile_fetcher=lambda _date: (_ for _ in ()).throw(
                TimeoutError()
            ),
            current_fetcher=lambda _moment: 4_900,
        )

        self.assertEqual(result["actual_turnover_yi"], 4_900)
        self.assertNotIn("estimated_turnover_yi", result)
        self.assertIn("turnover_estimate_warning", result)


if __name__ == "__main__":
    unittest.main()
