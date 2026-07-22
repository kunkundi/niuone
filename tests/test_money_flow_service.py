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
    def test_yesterday_cache_and_upstream_snapshot_are_empty_after_beijing_midnight(self):
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

            compute.assert_called_once_with()
            self.assertEqual(payload["inflow"], [])
            self.assertEqual(payload["outflow"], [])
            stored = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["inflow"], [])
            self.assertEqual(stored["outflow"], [])

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

    def test_download_json_retries_once_with_bounded_curl_runner(self):
        calls = []
        sleeps = []

        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))
            if len(calls) == 1:
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
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [0.25])
        self.assertEqual(calls[0][1]["timeout"], money_flow_service.REQUEST_TIMEOUT_SECONDS + 2)
        self.assertIn("--connect-timeout", calls[0][0])
        self.assertIn("--max-time", calls[0][0])

    def test_new_cache_name_does_not_reuse_legacy_total_flow_file(self):
        self.assertEqual(money_flow_service.CACHE_TTL, 60)
        self.assertEqual(money_flow_service.CACHE_PATH.name, "industry_main_money_flow_cache.json")
        self.assertNotEqual(money_flow_service.CACHE_PATH.name, "money_flow_dashboard_cache.json")


if __name__ == "__main__":
    unittest.main()
