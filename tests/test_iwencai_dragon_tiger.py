#!/usr/bin/env python3
"""Regression tests for the normalized iWencai dragon-tiger service."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.dashboard.apis.iwencai_service import (
    dragon_tiger_archive_path,
    fetch_dragon_tiger,
    normalize_trade_date,
    read_dragon_tiger_archive,
    read_dragon_tiger_snapshot,
    write_dragon_tiger_archive,
    write_dragon_tiger_snapshot,
)
from app.market_data.iwencai_client import IwencaiRequestError


ENABLED_ENV = {
    "IWENCAI_ENABLED": "1",
    "IWENCAI_BASE_URL": "https://openapi.iwencai.com",
    "IWENCAI_API_KEY": "test-secret",
    "IWENCAI_TIMEOUT_SECONDS": "20",
    "IWENCAI_MAX_RETRIES": "1",
    "IWENCAI_MAX_CONCURRENCY": "2",
}


class FakeClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def query(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.error:
            raise self.error
        if callable(self.result):
            return self.result(query, **kwargs)
        return self.result


class IwencaiDragonTigerTests(unittest.TestCase):
    def test_normalizes_dynamic_fields_sorts_and_marks_count_mismatch(self):
        client = FakeClient({
            "code_count": 1,
            "trace_id": "trace-1",
            "datas": [
                {
                    "股票代码": "000001.SZ",
                    "股票简称": "平安银行",
                    "所属同花顺行业": ["银行", "股份制银行"],
                    "最新价": "10.50",
                    "最新涨跌幅": "2.5",
                    "连续涨停天数[20260716]": "3天",
                    "榜单类型": "单日榜",
                    "上榜原因": "日涨幅偏离值达7%的证券",
                    "买入额[20260716]": 100.0,
                    "卖出额[20260716]": 80.0,
                    "净买入额[20260716]": 20.0,
                    "净买入额占成交额比例[20260716]": "1.25",
                    "上榜日期": "20260716",
                },
                {
                    "股票代码": "600000.SH",
                    "股票简称": "浦发银行",
                    "所属同花顺行业": ["银行", "股份制银行"],
                    "最新价": "12.00",
                    "最新涨跌幅": -1.0,
                    "最近连续跌停天数[20260716]": 2.0,
                    "榜单类型": "单日榜",
                    "上榜原因": "日跌幅偏离值达7%的证券",
                    "买入额[20260716]": 40.0,
                    "卖出额[20260716]": 90.0,
                    "净买入额[20260716]": -50.0,
                    "上榜日期": "2026-07-16",
                },
            ],
        })

        payload = fetch_dragon_tiger(
            "2026-07-16",
            page=1,
            limit=80,
            env=ENABLED_ENV,
            client=client,
        )

        self.assertTrue(payload["available"])
        self.assertEqual(payload["source"], "同花顺问财")
        self.assertEqual(payload["reported_count"], 1)
        self.assertEqual(payload["returned_count"], 2)
        self.assertEqual(payload["expected_returned_count"], 1)
        self.assertTrue(payload["count_mismatch"])
        self.assertEqual([item["code"] for item in payload["items"]], ["000001.SZ", "600000.SH"])
        self.assertEqual(payload["items"][0]["net_amount_yuan"], 20.0)
        self.assertEqual(payload["items"][0]["net_ratio_pct"], 1.25)
        self.assertEqual(payload["items"][0]["limit_up_streak"], 3)
        self.assertIsNone(payload["items"][0]["limit_down_streak"])
        self.assertEqual(payload["items"][1]["limit_down_streak"], 2)
        self.assertEqual(payload["items"][0]["list_date"], "2026-07-16")
        self.assertEqual(payload["items"][0]["sector"], "股份制银行")
        self.assertEqual(payload["unique_count"], 2)
        query, kwargs = client.calls[0]
        self.assertEqual(
            query,
            "2026年7月16日龙虎榜上榜股票、上榜原因、龙虎榜买入金额、卖出金额、净买入额、连续涨停天数、最近连续跌停天数",
        )
        self.assertEqual(kwargs, {"page": 1, "limit": 100})
        self.assertEqual(client.calls[1][0], "2026年7月16日龙虎榜上榜股票、所属行业")
        self.assertEqual(client.calls[2][0], "2026年7月16日龙虎榜营业部")

    def test_normalizes_all_seat_records_and_keeps_institution_summary(self):
        main_rows = [{
            "股票代码": "000001.SZ",
            "股票简称": "平安银行",
            "榜单类型": "单日榜",
            "上榜原因": "日涨幅偏离值达7%的证券",
            "净买入额[20260716]": 20.0,
        }]
        seat_rows = [
            {
                "股票代码": "000001.SZ",
                "股票简称": "平安银行",
                "上榜日期": "20260716",
                "上榜原因": "日涨幅偏离值达7%的证券",
                "营业部名称": "机构专用",
                "买卖席位": "买5席位",
                "买入额[20260716]": 100.0,
                "卖出额[20260716]": 10.0,
                "净买入额[20260716]": 90.0,
                "买入额占成交额比例[20260716]": 3.5,
                "卖出额占成交额比例[20260716]": 0.35,
            },
            {
                "股票代码": "000001.SZ",
                "股票简称": "平安银行",
                "上榜日期": "20260716",
                "营业部名称": "机构专用",
                "买卖席位": "卖2席位",
                "买入额[20260716]": 5.0,
                "卖出额[20260716]": 40.0,
                "净买入额[20260716]": -35.0,
            },
            {
                "股票代码": "000001.SZ",
                "营业部名称": "某证券营业部",
                "买卖席位": "卖1席位,买1席位",
                "买入额[20260716]": 500.0,
                "卖出额[20260716]": 400.0,
                "净买入额[20260716]": 100.0,
            },
        ]

        def result_for(query, **_kwargs):
            if "所属行业" in query:
                return {"code_count": 1, "datas": [{"股票代码": "000001.SZ", "所属行业": "银行"}]}
            if query.endswith("龙虎榜营业部"):
                return {"code_count": 1, "trace_id": "seat-trace", "datas": seat_rows}
            return {"code_count": 1, "datas": main_rows}

        payload = fetch_dragon_tiger(
            "2026-07-16",
            env=ENABLED_ENV,
            client=FakeClient(result_for),
        )

        item = payload["items"][0]
        self.assertTrue(payload["seat_available"])
        self.assertTrue(payload["seat_data_complete"])
        self.assertEqual(payload["seat_stock_count"], 1)
        self.assertEqual(payload["seat_record_count"], 3)
        self.assertEqual(payload["seat_trace_id"], "seat-trace")
        self.assertTrue(payload["institution_available"])
        self.assertEqual(payload["institution_stock_count"], 1)
        self.assertEqual(payload["institution_record_count"], 2)
        self.assertEqual(payload["institution_trace_id"], "seat-trace")
        self.assertEqual(item["seat_record_count"], 3)
        self.assertEqual(item["seat_buy_seat_count"], 2)
        self.assertEqual(item["seat_sell_seat_count"], 2)
        self.assertEqual(item["seat_buy_amount_yuan"], 600.0)
        self.assertEqual(item["seat_sell_amount_yuan"], 440.0)
        self.assertEqual(item["seat_net_amount_yuan"], 155.0)
        self.assertEqual(item["institution_record_count"], 2)
        self.assertEqual(item["institution_buy_seat_count"], 1)
        self.assertEqual(item["institution_sell_seat_count"], 1)
        self.assertEqual(item["institution_buy_amount_yuan"], 100.0)
        self.assertEqual(item["institution_sell_amount_yuan"], 40.0)
        self.assertEqual(item["institution_net_amount_yuan"], 55.0)
        self.assertEqual(
            {
                (record["seat_name"], record["side"], record["buy_rank"], record["sell_rank"])
                for record in item["seats"]
            },
            {
                ("机构专用", "buy", 5, None),
                ("机构专用", "sell", None, 2),
                ("某证券营业部", "both", 1, 1),
            },
        )
        self.assertEqual(len(item["institution_seats"]), 2)
        buy_institution = next(
            record for record in item["institution_seats"] if record["side"] == "buy"
        )
        self.assertEqual(buy_institution["buy_ratio_pct"], 3.5)
        self.assertEqual(buy_institution["sell_ratio_pct"], 0.35)
        brokerage = next(
            record for record in item["seats"] if record["seat_category"] == "brokerage"
        )
        self.assertEqual(brokerage["position"], "卖1席位,买1席位")

    def test_deduplicates_by_stock_and_retains_distinct_details(self):
        main_rows = [
            {
                "股票代码": "000001.SZ",
                "股票简称": "平安银行",
                "最新涨跌幅": "2.5",
                "连续涨停天数[20260716]": 3,
                "榜单类型": "三日榜",
                "上榜原因": "连续三个交易日涨幅偏离值累计达20%",
                "净买入额[20260716]": 35.0,
            },
            {
                "股票代码": "000001.SZ",
                "股票简称": "平安银行",
                "最新涨跌幅": "2.5",
                "连续涨停天数[20260716]": 2,
                "榜单类型": "单日榜",
                "上榜原因": "日涨幅偏离值达7%的证券",
                "买入额[20260716]": 100.0,
                "卖出额[20260716]": 80.0,
                "净买入额[20260716]": 20.0,
            },
            {
                "股票代码": "600000.SH",
                "股票简称": "浦发银行",
                "最新涨跌幅": "-1.0",
                "榜单类型": "单日榜",
                "上榜原因": "日跌幅偏离值达7%的证券",
                "净买入额[20260716]": -50.0,
            },
        ]
        sector_rows = [
            {
                "股票代码": "000001.SZ",
                "所属同花顺行业": ["金融", "银行", "股份制银行"],
            },
            {
                "股票代码": "600000.SH",
                "所属同花顺行业": ["金融", "银行", "股份制银行"],
            },
        ]

        def result_for(query, **_kwargs):
            return {
                "code_count": 2,
                "datas": sector_rows if "所属行业" in query else main_rows,
            }

        payload = fetch_dragon_tiger(
            "2026-07-16",
            env=ENABLED_ENV,
            client=FakeClient(result_for),
        )

        self.assertEqual(payload["unique_count"], 2)
        self.assertEqual(payload["returned_count"], 2)
        self.assertFalse(payload["count_mismatch"])
        first = next(item for item in payload["items"] if item["code"] == "000001.SZ")
        self.assertEqual(first["sector"], "股份制银行")
        self.assertEqual(first["sector_path"], "金融 / 银行 / 股份制银行")
        self.assertEqual(first["list_type"], "单日榜")
        self.assertEqual(first["net_amount_yuan"], 20.0)
        self.assertEqual(first["limit_up_streak"], 3)
        self.assertEqual(first["detail_count"], 2)
        self.assertEqual(
            {detail["list_type"] for detail in first["details"]},
            {"单日榜", "三日榜"},
        )
        self.assertEqual(
            {detail["reason"] for detail in first["details"]},
            {
                "日涨幅偏离值达7%的证券",
                "连续三个交易日涨幅偏离值累计达20%",
            },
        )

    def test_full_source_page_does_not_drop_later_details_for_seen_stock(self):
        first_detail = {
            "股票代码": "000001.SZ",
            "股票简称": "平安银行",
            "榜单类型": "单日榜",
            "上榜原因": "日涨幅偏离值达7%的证券",
            "净买入额[20260716]": 20.0,
        }
        later_detail = {
            "股票代码": "000001.SZ",
            "股票简称": "平安银行",
            "榜单类型": "三日榜",
            "上榜原因": "连续三个交易日涨幅偏离值累计达20%",
            "净买入额[20260716]": 35.0,
        }

        def result_for(query, *, page, **_kwargs):
            if "所属行业" in query:
                return {
                    "code_count": 1,
                    "datas": [{"股票代码": "000001.SZ", "所属同花顺行业": ["银行"]}],
                }
            if query.endswith("龙虎榜营业部"):
                return {"code_count": 0, "datas": []}
            return {
                "code_count": 1,
                "datas": [first_detail] * 100 if page == 1 else [later_detail],
            }

        client = FakeClient(result_for)
        payload = fetch_dragon_tiger(
            "2026-07-16",
            env=ENABLED_ENV,
            client=client,
        )

        self.assertEqual(payload["unique_count"], 1)
        self.assertEqual(payload["items"][0]["detail_count"], 2)
        main_pages = [
            kwargs["page"]
            for query, kwargs in client.calls
            if "所属行业" not in query and not query.endswith("龙虎榜营业部")
        ]
        self.assertEqual(main_pages, [1, 2])

    def test_seat_query_collects_all_pages_beyond_main_list_cap(self):
        main_row = {
            "股票代码": "000001.SZ",
            "股票简称": "平安银行",
            "榜单类型": "单日榜",
            "净买入额[20260716]": 20.0,
        }

        def result_for(query, *, page, **_kwargs):
            if "所属行业" in query:
                return {"code_count": 1, "datas": []}
            if query.endswith("龙虎榜营业部"):
                size = 100 if page <= 7 else 19 if page == 8 else 0
                return {
                    "code_count": 1,
                    "datas": [
                        {
                            "股票代码": "000001.SZ",
                            "营业部名称": f"测试营业部{page}-{index}",
                            "买卖席位": f"买{index % 5 + 1}席位",
                            "买入额[20260716]": page * 1000 + index,
                        }
                        for index in range(size)
                    ],
                }
            return {"code_count": 1, "datas": [main_row]}

        client = FakeClient(result_for)
        payload = fetch_dragon_tiger(
            "2026-07-16",
            env=ENABLED_ENV,
            client=client,
        )

        seat_pages = [
            kwargs["page"]
            for query, kwargs in client.calls
            if query.endswith("龙虎榜营业部")
        ]
        self.assertEqual(seat_pages, list(range(1, 9)))
        self.assertEqual(payload["seat_raw_returned_count"], 719)
        self.assertEqual(payload["seat_record_count"], 719)
        self.assertEqual(payload["items"][0]["seat_record_count"], 719)

    def test_disabled_or_missing_key_degrades_without_remote_call(self):
        client = FakeClient(error=AssertionError("must not call"))
        disabled = fetch_dragon_tiger(
            "2026-07-16",
            env={**ENABLED_ENV, "IWENCAI_ENABLED": "0"},
            client=client,
        )
        missing = fetch_dragon_tiger(
            "2026-07-16",
            env={**ENABLED_ENV, "IWENCAI_API_KEY": ""},
            client=client,
        )
        self.assertEqual(disabled["error"], "iwencai_disabled")
        self.assertEqual(missing["error"], "iwencai_not_configured")
        self.assertEqual(client.calls, [])

    def test_network_error_is_diagnostic_and_does_not_raise(self):
        client = FakeClient(error=IwencaiRequestError("network_error", "temporary"))
        payload = fetch_dragon_tiger(
            "2026-07-16",
            env=ENABLED_ENV,
            client=client,
        )
        self.assertTrue(payload["enabled"])
        self.assertFalse(payload["available"])
        self.assertEqual(payload["error"], "network_error")
        self.assertEqual(payload["items"], [])

    def test_seat_detail_failure_keeps_main_list_available(self):
        def result_for(query, **_kwargs):
            if query.endswith("龙虎榜营业部"):
                raise IwencaiRequestError("seat_timeout", "temporary")
            if "所属行业" in query:
                return {"code_count": 1, "datas": [{"股票代码": "000001.SZ", "所属行业": "银行"}]}
            return {
                "code_count": 1,
                "datas": [{
                    "股票代码": "000001.SZ",
                    "股票简称": "平安银行",
                    "榜单类型": "单日榜",
                    "上榜原因": "日涨幅偏离值达7%的证券",
                }],
            }

        payload = fetch_dragon_tiger(
            "2026-07-16",
            env=ENABLED_ENV,
            client=FakeClient(result_for),
        )

        self.assertTrue(payload["available"])
        self.assertFalse(payload["seat_available"])
        self.assertEqual(payload["seat_error"], "seat_timeout")
        self.assertEqual(payload["seat_record_count"], 0)
        self.assertEqual(payload["items"][0]["seats"], [])
        self.assertFalse(payload["institution_available"])
        self.assertEqual(payload["institution_error"], "seat_timeout")
        self.assertEqual(payload["institution_record_count"], 0)
        self.assertEqual(payload["items"][0]["institution_seats"], [])

    def test_validates_date_and_pagination(self):
        self.assertEqual(normalize_trade_date("20260716"), "2026-07-16")
        with self.assertRaises(ValueError):
            fetch_dragon_tiger("2026-02-30", env=ENABLED_ENV)
        with self.assertRaises(ValueError):
            fetch_dragon_tiger("2026-07-16", page=0, env=ENABLED_ENV)
        with self.assertRaises(ValueError):
            fetch_dragon_tiger("2026-07-16", limit=101, env=ENABLED_ENV)

    def test_last_page_uses_page_size_for_count_consistency(self):
        client = FakeClient({
            "code_count": 79,
            "datas": [
                {
                    "股票代码": f"{index:06d}.SZ",
                    "股票简称": f"样本{index}",
                    "榜单类型": "单日榜",
                    "上榜原因": f"原因{index}",
                }
                for index in range(1, 80)
            ],
        })
        payload = fetch_dragon_tiger(
            "2026-07-16",
            page=8,
            limit=10,
            env=ENABLED_ENV,
            client=client,
        )
        self.assertEqual(payload["expected_returned_count"], 9)
        self.assertEqual(payload["returned_count"], 9)
        self.assertFalse(payload["count_mismatch"])

    def test_snapshot_write_is_atomic_and_empty_result_preserves_last_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            valid = {
                "enabled": True,
                "available": True,
                "source": "同花顺问财",
                "date": "2026-07-16",
                "items": [{"code": "000001.SZ", "name": "平安银行"}],
            }
            self.assertTrue(write_dragon_tiger_snapshot(path, valid))
            original = path.read_bytes()
            loaded = read_dragon_tiger_snapshot(path, trade_date="20260716")
            self.assertIsNotNone(loaded)
            self.assertTrue(loaded["snapshot"])
            self.assertEqual(loaded["items"][0]["code"], "000001.SZ")
            self.assertEqual(loaded["items"][0]["detail_count"], 0)
            self.assertIsNone(loaded["seat_available"])
            self.assertFalse(loaded["seat_data_complete"])
            self.assertIsNone(loaded["institution_available"])
            self.assertNotIn("institution_error", loaded)
            self.assertIsNone(read_dragon_tiger_snapshot(path, trade_date="2026-07-17"))

            self.assertFalse(
                write_dragon_tiger_snapshot(
                    path,
                    {**valid, "date": "2026-07-17", "items": []},
                )
            )
            self.assertEqual(path.read_bytes(), original)

    def test_daily_archive_uses_exact_date_and_preserves_same_day_seat_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_dir = Path(tmp) / "iwencai_dragon_tiger"
            valid = {
                "enabled": True,
                "available": True,
                "source": "同花顺问财",
                "date": "2026-07-16",
                "seat_available": True,
                "seat_data_complete": True,
                "items": [{
                    "code": "000001.SZ",
                    "name": "平安银行",
                    "seats": [{
                        "seat_name": "机构专用",
                        "seat_type": "机构专用",
                        "seat_category": "institution",
                        "side": "buy",
                        "rank": 1,
                        "buy_rank": 1,
                        "sell_rank": None,
                        "buy_amount_yuan": 100.0,
                        "sell_amount_yuan": 0.0,
                        "net_amount_yuan": 100.0,
                    }, {
                        "seat_name": "某证券营业部",
                        "seat_type": "营业部",
                        "seat_category": "brokerage",
                        "side": "sell",
                        "rank": 2,
                        "buy_rank": None,
                        "sell_rank": 2,
                        "buy_amount_yuan": 0.0,
                        "sell_amount_yuan": 80.0,
                        "net_amount_yuan": -80.0,
                    }],
                }],
            }
            self.assertTrue(write_dragon_tiger_archive(archive_dir, valid))
            path = dragon_tiger_archive_path(archive_dir, "20260716")
            self.assertTrue(path.is_file())
            loaded = read_dragon_tiger_archive(archive_dir, trade_date="2026-07-16")
            self.assertIsNotNone(loaded)
            self.assertTrue(loaded["archive"])
            self.assertTrue(loaded["seat_data_complete"])
            self.assertEqual(loaded["items"][0]["seat_record_count"], 2)
            self.assertEqual(loaded["items"][0]["institution_record_count"], 1)

            partial = {
                **valid,
                "seat_available": False,
                "seat_error": "network_error",
                "items": [{"code": "000001.SZ", "name": "平安银行"}],
            }
            self.assertTrue(write_dragon_tiger_archive(archive_dir, partial))
            preserved = read_dragon_tiger_archive(archive_dir, trade_date="2026-07-16")
            self.assertTrue(preserved["seat_preserved_from_previous"])
            self.assertTrue(preserved["institution_preserved_from_previous"])
            self.assertEqual(preserved["items"][0]["seat_record_count"], 2)
            self.assertEqual(preserved["items"][0]["institution_record_count"], 1)
            self.assertIsNone(read_dragon_tiger_archive(archive_dir, trade_date="2026-07-17"))


if __name__ == "__main__":
    unittest.main()
