#!/usr/bin/env python3
import importlib.util
import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
sys.path.insert(0, str(SRC))
MODULE_PATH = SRC / "a_share_auction_summary.py"


def load_module():
    spec = importlib.util.spec_from_file_location("a_share_auction_summary_under_test", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class AShareAuctionSummaryTests(unittest.TestCase):
    def test_extract_auction_snapshot_uses_open_price_and_turnover(self):
        mod = load_module()

        rows = mod.extract_auction_snapshot_rows([
            {"f12": "600001", "f14": "测试科技", "f17": 11.0, "f18": 10.0, "f2": 11.2, "f5": 12345, "f6": 67890000, "f100": "半导体"},
            {"f12": "830000", "f14": "北交测试", "f17": 9.0, "f18": 10.0, "f5": 1, "f6": 1, "f100": "其他"},
            {"f12": "300001", "f14": "N新股", "f17": 12.0, "f18": 10.0, "f5": 1, "f6": 1, "f100": "其他"},
        ])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "600001")
        self.assertAlmostEqual(rows[0]["auction_pct"], 10.0)
        self.assertEqual(rows[0]["amount"], 67890000)
        self.assertEqual(rows[0]["volume_lot"], 12345)
        self.assertEqual(rows[0]["industry"], "半导体")

    def test_build_report_uses_auction_sections_not_fund_flow(self):
        mod = load_module()
        mod.NOW = datetime(2026, 7, 2, 9, 25, tzinfo=mod.CN_TZ)
        mod.fetch_auction_snapshot = lambda: ([
            {"code": "600001", "name": "测试科技", "industry": "半导体", "open_price": 11.0, "latest_price": 11.0, "prev_close": 10.0, "auction_pct": 10.0, "change_pct": 10.0, "amount": 120000000, "volume_lot": 120000, "vol_ratio": 2.1},
            {"code": "000001", "name": "测试银行", "industry": "银行", "open_price": 9.8, "latest_price": 9.8, "prev_close": 10.0, "auction_pct": -2.0, "change_pct": -2.0, "amount": 50000000, "volume_lot": 50000, "vol_ratio": 1.1},
        ], None)
        mod.fetch_zt_pool = lambda: (mod.pd.DataFrame([
            {"代码": "600001", "名称": "测试科技", "涨跌幅": 10.0, "最新价": 11.0, "封单资金": 30000000},
        ]), None)
        mod.fetch_dt_pool = lambda: (mod.pd.DataFrame([
            {"代码": "000001", "名称": "测试银行", "涨跌幅": -10.0, "最新价": 9.0, "封单资金": 10000000},
        ]), None)

        report = mod.build_report()

        self.assertIn("开盘价强弱", report)
        self.assertIn("竞价强势板块", report)
        self.assertIn("竞价成交活跃", report)
        self.assertIn("跌停风险Top5", report)
        self.assertIn("竞价额", report)
        self.assertNotIn("资金流向", report)
        self.assertNotIn("资金净流入", report)

    def test_fetch_auction_snapshot_keeps_partial_pages_on_remote_disconnect(self):
        mod = load_module()
        original_urlopen = mod.urlopen
        original_env = {
            "A_SHARE_AUCTION_SNAPSHOT_MAX_PAGES": os.environ.get("A_SHARE_AUCTION_SNAPSHOT_MAX_PAGES"),
            "A_SHARE_AUCTION_SNAPSHOT_WORKERS": os.environ.get("A_SHARE_AUCTION_SNAPSHOT_WORKERS"),
            "A_SHARE_AUCTION_SNAPSHOT_RETRIES": os.environ.get("A_SHARE_AUCTION_SNAPSHOT_RETRIES"),
        }

        class Resp:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def fake_urlopen(req, timeout=0):
            page = int(parse_qs(urlparse(req.full_url).query)["pn"][0])
            if page == 2:
                raise mod.RemoteDisconnected("Remote end closed connection without response")
            code = "600001" if page == 1 else "000003"
            return Resp({
                "data": {
                    "total": 300,
                    "diff": [
                        {"f12": code, "f14": f"测试{page}", "f17": 11.0, "f18": 10.0, "f2": 11.0, "f5": 100, "f6": 1000000, "f100": "半导体"}
                    ],
                }
            })

        try:
            os.environ["A_SHARE_AUCTION_SNAPSHOT_MAX_PAGES"] = "3"
            os.environ["A_SHARE_AUCTION_SNAPSHOT_WORKERS"] = "2"
            os.environ["A_SHARE_AUCTION_SNAPSHOT_RETRIES"] = "1"
            mod.urlopen = fake_urlopen

            rows, err = mod.fetch_auction_snapshot()
        finally:
            mod.urlopen = original_urlopen
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual([row["code"] for row in rows], ["600001", "000003"])
        self.assertIn("竞价快照部分页失败", err)
        self.assertIn("p2 RemoteDisconnected", err)


if __name__ == "__main__":
    unittest.main()
