#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "compat"))

from dashboard.apis import money_flow_service  # noqa: E402


def eastmoney_row(
    code: str,
    name: str,
    main_net_yuan: float,
    *,
    timestamp: int = 1784530800,
) -> dict:
    return {
        "f12": code,
        "f14": name,
        "f2": 1234.5,
        "f3": -1.25,
        "f62": main_net_yuan,
        "f184": -2.59,
        "f66": -11_682_443_264,
        "f69": -2.63,
        "f72": 197_025_792,
        "f75": 0.04,
        "f78": 5_429_112_832,
        "f81": 1.22,
        "f84": 6_044_376_320,
        "f87": 1.36,
        "f204": "海光信息",
        "f205": "688041",
        "f124": timestamp,
    }


class MoneyFlowServiceTests(unittest.TestCase):
    def test_yesterday_cache_remains_available_before_beijing_nine(self):
        yesterday = {
            "generated_at": "2026-07-22 15:00:00",
            "inflow": [{"name": "半导体", "net_flow_yi": 12}],
            "outflow": [{"name": "银行", "net_flow_yi": -6}],
        }
        with tempfile.TemporaryDirectory(prefix="niuone-money-flow-day-") as temp_dir:
            cache_path = Path(temp_dir) / "money_flow.json"
            cache_path.write_text(json.dumps(yesterday), encoding="utf-8")
            with patch.object(money_flow_service, "CACHE_PATH", cache_path), patch.object(
                money_flow_service,
                "_beijing_now",
                return_value=datetime(2026, 7, 23, 0, 1, tzinfo=money_flow_service.BEIJING_TZ),
            ), patch.object(
                money_flow_service,
                "_compute",
                return_value=yesterday,
            ) as compute:
                payload = money_flow_service.fetch_money_flow()

            compute.assert_not_called()
            self.assertEqual(payload["inflow"], yesterday["inflow"])
            self.assertEqual(payload["outflow"], yesterday["outflow"])
            stored = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(stored, yesterday)

    def test_overnight_force_refresh_preserves_yesterday_on_unusable_results(self):
        yesterday = {
            "generated_at": "2026-07-22 15:00:00",
            "inflow": [{"name": "半导体", "net_flow_yi": 12}],
            "outflow": [{"name": "银行", "net_flow_yi": -6}],
        }
        unusable_results = {
            "new_calendar_day": {
                "generated_at": "2026-07-23 00:01:00",
                "inflow": [{"name": "证券", "net_flow_yi": 2}],
                "outflow": [{"name": "银行", "net_flow_yi": -1}],
            },
            "incomplete_rankings": {
                "generated_at": "2026-07-22 15:00:00",
                "inflow": [],
                "outflow": [],
            },
        }
        for case, upstream in unusable_results.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory(
                prefix="niuone-money-flow-overnight-"
            ) as temp_dir:
                cache_path = Path(temp_dir) / "money_flow.json"
                cache_path.write_text(json.dumps(yesterday), encoding="utf-8")
                with patch.object(money_flow_service, "CACHE_PATH", cache_path), patch.object(
                    money_flow_service,
                    "_beijing_now",
                    return_value=datetime(
                        2026,
                        7,
                        23,
                        0,
                        1,
                        tzinfo=money_flow_service.BEIJING_TZ,
                    ),
                ), patch.object(
                    money_flow_service,
                    "_compute",
                    return_value=upstream,
                ) as compute:
                    payload = money_flow_service.fetch_money_flow(force_refresh=True)

                compute.assert_called_once_with()
                self.assertEqual(payload["inflow"], yesterday["inflow"])
                self.assertEqual(payload["outflow"], yesterday["outflow"])
                self.assertTrue(payload["stale_cache"])
                self.assertIn("retained market date", payload["error"])
                stored = json.loads(cache_path.read_text(encoding="utf-8"))
                self.assertEqual(stored, yesterday)

    def test_yesterday_cache_and_upstream_snapshot_are_empty_at_beijing_nine(self):
        yesterday = {
            "generated_at": "2026-07-22 15:00:00",
            "inflow": [{"name": "半导体", "net_flow_yi": 12}],
            "outflow": [{"name": "银行", "net_flow_yi": -6}],
        }
        with tempfile.TemporaryDirectory(prefix="niuone-money-flow-day-") as temp_dir:
            cache_path = Path(temp_dir) / "money_flow.json"
            cache_path.write_text(json.dumps(yesterday), encoding="utf-8")
            with patch.object(money_flow_service, "CACHE_PATH", cache_path), patch.object(
                money_flow_service,
                "_beijing_now",
                return_value=datetime(2026, 7, 23, 9, 0, tzinfo=money_flow_service.BEIJING_TZ),
            ), patch.object(
                money_flow_service,
                "_compute",
                return_value=yesterday,
            ) as compute:
                payload = money_flow_service.fetch_money_flow()

            compute.assert_called_once_with()
            self.assertEqual(payload["inflow"], [])
            self.assertEqual(payload["outflow"], [])
            stored = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["inflow"], [])
            self.assertEqual(stored["outflow"], [])
            self.assertEqual(stored["retention_date"], "2026-07-23")

    def test_compute_paginates_and_maps_today_main_net_amount(self):
        first_page = [
            eastmoney_row(f"BK{i:04d}", f"流入行业{i}", i * 100_000_000)
            for i in range(1, 21)
        ]
        second_page = [
            eastmoney_row("BK1036", "半导体", -10_210_000_000),
            *(
                eastmoney_row(f"BKX{i:03d}", f"流出行业{i}", -i * 100_000_000)
                for i in range(1, 11)
            ),
        ]
        calls = []

        def fake_fetch_page(page):
            calls.append(page)
            return (first_page, 101) if page == 1 else (second_page, 101)

        with patch.object(money_flow_service, "_fetch_page", side_effect=fake_fetch_page):
            payload = money_flow_service._compute()

        self.assertEqual(calls, [1, 2])
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["metric"], "industry_main_net_flow")
        self.assertEqual(payload["metric_label"], "今日主力净额")
        self.assertEqual(payload["source"], "东方财富行业板块主力净额")
        self.assertEqual(payload["count"], 31)
        self.assertTrue(all(row["net_flow_yi"] > 0 for row in payload["inflow"]))
        self.assertTrue(all(row["net_flow_yi"] < 0 for row in payload["outflow"]))

        semiconductor = next(row for row in payload["outflow"] if row["name"] == "半导体")
        self.assertEqual(semiconductor["code"], "BK1036")
        self.assertEqual(semiconductor["net_flow"], -10_210_000_000)
        self.assertEqual(semiconductor["net_flow_yi"], -102.1)
        self.assertEqual(semiconductor["main_net_flow_yi"], -102.1)
        self.assertEqual(semiconductor["large_net_flow_yi"], 1.9703)
        self.assertEqual(semiconductor["leader"], "海光信息")
        self.assertNotIn("inflow_yi", semiconductor)
        self.assertNotIn("outflow_yi", semiconductor)

    def test_compute_filters_zero_and_wrong_sign_from_each_ranking(self):
        rows = [
            *(eastmoney_row(f"P{i}", f"正{i}", i * 100_000_000) for i in range(1, 11)),
            *(eastmoney_row(f"N{i}", f"负{i}", -i * 100_000_000) for i in range(1, 11)),
            eastmoney_row("ZERO", "零", 0),
        ]
        with patch.object(money_flow_service, "_fetch_page", return_value=(rows, len(rows))):
            payload = money_flow_service._compute()

        self.assertEqual([row["name"] for row in payload["inflow"][:2]], ["正10", "正9"])
        self.assertEqual([row["name"] for row in payload["outflow"][:2]], ["负10", "负9"])
        self.assertNotIn("零", {row["name"] for row in payload["inflow"] + payload["outflow"]})

    def test_compute_retries_transient_zero_main_net_snapshot(self):
        zero_rows = [
            eastmoney_row(f"ZERO{i}", f"零行业{i}", 0)
            for i in range(20)
        ]
        valid_rows = [
            *(eastmoney_row(f"P{i}", f"正{i}", i * 100_000_000) for i in range(1, 11)),
            *(eastmoney_row(f"N{i}", f"负{i}", -i * 100_000_000) for i in range(1, 11)),
        ]
        snapshots = [zero_rows, valid_rows]
        calls = []
        sleeps = []

        def fake_fetch_page(page):
            calls.append(page)
            return snapshots.pop(0), 20

        with patch.object(money_flow_service, "_fetch_page", side_effect=fake_fetch_page):
            payload = money_flow_service._compute(sleep=sleeps.append)

        self.assertEqual(calls, [1, 1])
        self.assertEqual(sleeps, [money_flow_service.SNAPSHOT_RETRY_DELAYS_SECONDS[0]])
        self.assertEqual(payload["count"], 20)
        self.assertEqual(len(payload["inflow"]), 10)
        self.assertEqual(len(payload["outflow"]), 10)

    def test_compute_stops_retrying_persistently_incomplete_snapshots(self):
        zero_rows = [
            eastmoney_row(f"ZERO{i}", f"零行业{i}", 0)
            for i in range(20)
        ]
        calls = []
        sleeps = []

        def fake_fetch_page(page):
            calls.append(page)
            return zero_rows, 20

        with patch.object(money_flow_service, "_fetch_page", side_effect=fake_fetch_page):
            with self.assertRaisesRegex(RuntimeError, "returned only 0 usable rows"):
                money_flow_service._compute(sleep=sleeps.append)

        self.assertEqual(
            len(calls),
            len(money_flow_service.SNAPSHOT_RETRY_DELAYS_SECONDS) + 1,
        )
        self.assertEqual(
            sleeps,
            list(money_flow_service.SNAPSHOT_RETRY_DELAYS_SECONDS),
        )

    def test_download_json_retries_with_bounded_curl_runner(self):
        calls = []
        sleeps = []

        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))
            if len(calls) < 3:
                return SimpleNamespace(returncode=28, stdout=b"", stderr=b"timeout")
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"data": {"total": 1, "diff": []}}).encode("utf-8"),
                stderr=b"",
            )

        payload = money_flow_service._download_json(
            "https://example.test/data",
            runner=fake_runner,
            sleep=sleeps.append,
            curl_path="/usr/bin/curl",
        )

        self.assertEqual(payload["data"]["total"], 1)
        self.assertEqual(len(calls), 3)
        self.assertEqual(
            sleeps,
            list(money_flow_service.REQUEST_RETRY_DELAYS_SECONDS),
        )
        self.assertEqual(calls[0][1]["timeout"], money_flow_service.REQUEST_TIMEOUT_SECONDS + 2)
        self.assertIn("--connect-timeout", calls[0][0])
        self.assertIn("--max-time", calls[0][0])

    def test_download_json_retries_semantically_incomplete_payload(self):
        calls = []
        sleeps = []

        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))
            data = (
                None
                if len(calls) == 1
                else {"total": 1, "diff": [{"f12": "BK0001"}]}
            )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"data": data}).encode("utf-8"),
                stderr=b"",
            )

        payload = money_flow_service._download_json(
            "https://example.test/data",
            runner=fake_runner,
            sleep=sleeps.append,
            curl_path="/usr/bin/curl",
            validate_payload=money_flow_service._validate_page_payload,
        )

        self.assertEqual(payload["data"]["total"], 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [money_flow_service.REQUEST_RETRY_DELAYS_SECONDS[0]])

    def test_new_cache_name_does_not_reuse_legacy_total_flow_file(self):
        self.assertEqual(money_flow_service.CACHE_TTL, 60)
        self.assertEqual(money_flow_service.CACHE_PATH.name, "industry_main_money_flow_cache.json")
        self.assertNotEqual(money_flow_service.CACHE_PATH.name, "money_flow_dashboard_cache.json")

    def test_official_chart_returns_complete_timestamped_f62_without_extra_page(self):
        now = datetime(2026, 7, 23, 11, tzinfo=money_flow_service.BEIJING_TZ)
        rows = [eastmoney_row(f'BK{i:04d}', f'行业{i}', (i-59)*1e8, timestamp=int(now.timestamp())) for i in range(128)]
        calls = []
        def download(url, *, validate_payload, attempts):
            from urllib.parse import parse_qs, urlsplit
            calls.append(url)
            self.assertEqual(attempts, 1)
            params = parse_qs(urlsplit(url).query)
            self.assertEqual(params['code'], ['m:90+s:4'])
            self.assertIn('f124', params['key'][0].split(','))
            payload = {'data': {'diff': rows, 'total': len(rows)}}
            validate_payload(payload)
            return payload
        with patch.object(money_flow_service, '_beijing_now', return_value=now), patch.object(money_flow_service, '_download_json', side_effect=download):
            result = money_flow_service._compute_once()
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['count'], 127)  # zero-net board is excluded from rankings
        self.assertEqual(result['generated_at'], '2026-07-23 11:00:00')
        self.assertEqual(result['inflow'][0]['net_flow_yi'], 68)
        self.assertEqual(result['outflow'][0]['net_flow_yi'], -59)

    def test_invalid_chart_falls_back_to_quote_pages(self):
        now = datetime(2026, 7, 23, 11, tzinfo=money_flow_service.BEIJING_TZ)
        rows = [eastmoney_row(f'BK{i:04d}', f'行业{i}', (i-9)*1e8, timestamp=int(now.timestamp())) for i in range(20)]
        cases = [({'total': 21, 'diff': rows}), ({'total': 20, 'diff': rows[:-1] + [rows[0]]})]
        cases += [{'total': 20, 'diff': [{**rows[0], **bad}, *rows[1:]]} for bad in (
            {'f124': None}, {'f124': int(now.timestamp())-86400}, {'f124': int(now.timestamp())+3600}, {'f62': '-'}, {'f12': ''})]
        for bad in cases:
            calls = []
            def download(url, *, validate_payload, **kwargs):
                calls.append(url)
                data = bad if 'getbkzj' in url else {'total': 20, 'diff': rows}
                validate_payload({'data': data})
                return {'data': data}
            with patch.object(money_flow_service, '_beijing_now', return_value=now), patch.object(money_flow_service, '_download_json', side_effect=download):
                self.assertEqual(money_flow_service._fetch_page(1), (rows, 20))
            self.assertEqual(len(calls), 2)

    def test_missing_source_clock_cannot_create_a_fresh_snapshot(self):
        with self.assertRaisesRegex(RuntimeError, 'source time is missing'):
            money_flow_service._source_time([{'f124': '-'}])

    def test_midday_freshness_uses_session_close_and_preserves_valid_cache(self):
        tz = money_flow_service.BEIJING_TZ
        now = datetime(2026, 7, 23, 12, 20, tzinfo=tz)
        self.assertTrue(money_flow_service._source_clock_usable(now.replace(hour=11, minute=30), now))
        self.assertFalse(money_flow_service._source_clock_usable(now.replace(hour=9, minute=30), now))
        saved = {'generated_at': '2026-07-23 11:30:00', 'inflow': [{'name':'A'}], 'outflow': [{'name':'B'}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'cache.json'
            path.write_text(json.dumps(saved), encoding='utf-8')
            with patch.object(money_flow_service, 'CACHE_PATH', path), patch.object(money_flow_service, '_beijing_now', return_value=now), patch.object(money_flow_service, '_compute', return_value={**saved, 'generated_at':'2026-07-22 15:00:00'}):
                result = money_flow_service.fetch_money_flow(force_refresh=True)
            self.assertTrue(result['stale_cache'])
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), saved)

    def test_download_switches_to_same_source_fallback_after_failure(self):
        urls = []
        def runner(command, **kwargs):
            urls.append(command[-1])
            if len(urls) == 1:
                return SimpleNamespace(returncode=22, stdout=b"", stderr=b"HTTP 502")
            return SimpleNamespace(returncode=0, stdout=b'{"data":{"total":20}}', stderr=b"")
        result = money_flow_service._download_json(
            "https://primary.test/data", fallback_urls=("https://fallback.test/data",),
            runner=runner, curl_path="curl", sleep=lambda _: None,
        )
        self.assertEqual(urls, ["https://primary.test/data", "https://fallback.test/data"])
        self.assertEqual(result["data"]["total"], 20)

    def test_snapshot_budget_is_shared_across_page_retries(self):
        clock = [0.0]
        calls = []
        def runner(command, **kwargs):
            calls.append(kwargs["timeout"])
            clock[0] += 8
            return SimpleNamespace(returncode=28, stdout=b"", stderr=b"timeout")
        token = money_flow_service._SNAPSHOT_DEADLINE.set(10.0)
        try:
            with patch.object(money_flow_service.time, "monotonic", side_effect=lambda: clock[0]):
                with self.assertRaisesRegex(TimeoutError, "snapshot deadline"):
                    money_flow_service._download_json(
                        "https://example.test/data", runner=runner, curl_path="curl",
                        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                    )
            self.assertEqual(calls, [10, 3.5])
        finally:
            money_flow_service._SNAPSHOT_DEADLINE.reset(token)

    def test_compute_resets_budget_after_failure(self):
        with patch.object(money_flow_service, "_compute_once", side_effect=RuntimeError("source down")):
            with self.assertRaises(RuntimeError):
                money_flow_service._compute()
        self.assertIsNone(money_flow_service._SNAPSHOT_DEADLINE.get())


if __name__ == "__main__":
    unittest.main()
