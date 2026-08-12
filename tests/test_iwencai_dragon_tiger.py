#!/usr/bin/env python3
"""Regression tests for the normalized iWencai dragon-tiger service."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.dashboard.apis.iwencai_service import (
    dragon_tiger_archive_path,
    enrich_consecutive_dragon_tiger_news,
    expire_dragon_tiger_archives,
    fetch_dragon_tiger,
    mark_consecutive_dragon_tiger_items,
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
    def test_core_payload_is_published_before_seat_enrichment(self):
        observed = []

        def result_for(query, **_kwargs):
            if "所属行业" in query:
                return {
                    "code_count": 1,
                    "datas": [{"股票代码": "000001.SZ", "所属行业": "银行"}],
                }
            if query.endswith("龙虎榜营业部"):
                self.assertEqual(len(observed), 1)
                raise IwencaiRequestError("seat_timeout", "temporary")
            return {
                "code_count": 1,
                "datas": [{
                    "股票代码": "000001.SZ",
                    "股票简称": "平安银行",
                    "榜单类型": "单日榜",
                }],
            }

        payload = fetch_dragon_tiger(
            "2026-07-16",
            env=ENABLED_ENV,
            client=FakeClient(result_for),
            on_core_payload=lambda core: observed.append(dict(core)),
        )

        self.assertEqual(observed[0]["items"][0]["code"], "000001.SZ")
        self.assertEqual(observed[0]["items"][0]["sector"], "银行")
        self.assertTrue(observed[0]["seat_enrichment_pending"])
        self.assertFalse(observed[0]["seat_data_complete"])
        self.assertFalse(payload["seat_enrichment_pending"])
        self.assertEqual(payload["seat_error"], "seat_timeout")

    def test_marks_only_stocks_present_on_adjacent_trading_day(self):
        previous = {
            "date": "2026-07-15",
            "items": [{
                "code": "000001.SZ",
                "name": "平安银行",
                "consecutive_list_days": 2,
                "consecutive_list_dates": ["2026-07-14", "2026-07-15"],
            }, {
                "code": "600000.SH",
                "name": "浦发银行",
            }],
        }
        current = {
            "available": True,
            "date": "2026-07-16",
            "items": [
                {"code": "000001.SZ", "name": "平安银行"},
                {"code": "000002.SZ", "name": "万科A"},
            ],
        }

        marked = mark_consecutive_dragon_tiger_items(
            current,
            previous,
            previous_trading_day="2026-07-15",
        )

        self.assertEqual(marked["continuous_list_count"], 1)
        by_code = {item["code"]: item for item in marked["items"]}
        self.assertTrue(by_code["000001.SZ"]["consecutive_listed"])
        self.assertEqual(by_code["000001.SZ"]["consecutive_list_days"], 3)
        self.assertEqual(
            by_code["000001.SZ"]["consecutive_list_dates"],
            ["2026-07-14", "2026-07-15", "2026-07-16"],
        )
        self.assertFalse(by_code["000002.SZ"]["consecutive_listed"])
        self.assertEqual(by_code["000002.SZ"]["consecutive_list_days"], 1)

    def test_consecutive_marker_resets_across_missing_snapshot_and_is_idempotent(self):
        current = {
            "available": True,
            "date": "2026-07-16",
            "items": [{"code": "000001.SZ", "name": "平安银行"}],
        }
        stale_previous = {
            "date": "2026-07-14",
            "items": [{
                "code": "000001.SZ",
                "name": "平安银行",
                "consecutive_list_days": 4,
            }],
        }
        reset = mark_consecutive_dragon_tiger_items(
            current,
            stale_previous,
            previous_trading_day="2026-07-15",
        )
        repeated = mark_consecutive_dragon_tiger_items(
            reset,
            reset,
            previous_trading_day="2026-07-15",
        )

        self.assertEqual(reset["items"][0]["consecutive_list_days"], 1)
        self.assertFalse(reset["items"][0]["consecutive_listed"])
        self.assertEqual(repeated["items"][0]["consecutive_list_days"], 1)

    def test_news_precheck_enrichment_queries_limit_up_or_consecutive_listing_stocks(self):
        captured = {}

        def fake_fetcher(candidates, config, **kwargs):
            captured["candidates"] = candidates
            captured["config"] = config
            captured["kwargs"] = kwargs
            return [{
                "code": candidate["code"],
                "name": candidate["name"],
                "checked": True,
                "available": True,
                "tone": "positive" if candidate["code"] == "000001.SZ" else "neutral",
                "tone_label": "利好" if candidate["code"] == "000001.SZ" else "中性",
                "summary": "重大合同落地（利好）" if candidate["code"] == "000001.SZ" else "暂无重大消息（中性）",
                "window_days": 3,
                "fetched_at": "2026-07-16T18:00:00+08:00",
                "error": "",
            } for candidate in candidates]

        payload = enrich_consecutive_dragon_tiger_news(
            {
                "date": "2026-07-16",
                "items": [{
                    "code": "000001.SZ",
                    "name": "平安银行",
                    "limit_up_streak": 2,
                    "consecutive_listed": False,
                    "consecutive_list_days": 1,
                    "net_amount_yuan": 20,
                }, {
                    "code": "000002.SZ",
                    "name": "万科A",
                    "limit_up_streak": 1,
                    "consecutive_listed": True,
                    "consecutive_list_days": 2,
                }, {
                    "code": "000003.SZ",
                    "name": "未连续样本",
                    "limit_up_streak": 1,
                    "consecutive_listed": False,
                    "consecutive_list_days": 1,
                }],
            },
            env={
                "DASHBOARD_NEWS_BASE_URL": "https://news.example/v1",
                "DASHBOARD_NEWS_API_KEY": "news-secret",
                "DASHBOARD_NEWS_MODEL": "search-model",
                "DASHBOARD_NEWS_API_MODE": "responses",
            },
            fetcher=fake_fetcher,
        )

        self.assertEqual(
            [item["code"] for item in captured["candidates"]],
            ["000001.SZ", "000002.SZ"],
        )
        self.assertEqual(captured["config"].base_url, "https://news.example/v1")
        self.assertEqual(captured["config"].api_key, "news-secret")
        self.assertEqual(captured["config"].model, "search-model")
        self.assertEqual(captured["config"].api_mode, "responses")
        self.assertEqual(captured["kwargs"]["max_candidates"], 2)
        self.assertEqual(payload["continuous_news_checked_count"], 2)
        self.assertEqual(payload["continuous_news_pending_count"], 0)
        self.assertEqual(
            payload["continuous_news_checked_codes"],
            ["000001.SZ", "000002.SZ"],
        )
        self.assertEqual(payload["continuous_news_pending_codes"], [])
        self.assertTrue(payload["continuous_news_complete"])
        self.assertEqual(payload["continuous_news_available_count"], 2)
        self.assertEqual(payload["limit_up_news_candidate_count"], 2)
        self.assertEqual(
            payload["limit_up_news_checked_codes"],
            ["000001.SZ", "000002.SZ"],
        )
        self.assertEqual(payload["limit_up_news_pending_codes"], [])
        self.assertTrue(payload["limit_up_news_complete"])
        self.assertEqual(payload["items"][0]["news_precheck"]["provider"], "消息面预检模型")
        self.assertEqual(payload["items"][0]["news_precheck"]["summary"], "重大合同落地（利好）")
        self.assertEqual(payload["items"][1]["news_precheck"]["tone_label"], "中性")
        self.assertNotIn("news_precheck", payload["items"][2])
        self.assertNotIn("news-secret", str(payload))

    def test_news_precheck_does_not_fall_back_to_grok_configuration(self):
        payload = enrich_consecutive_dragon_tiger_news(
            {
                "date": "2026-07-16",
                "items": [{
                    "code": "000001.SZ",
                    "name": "平安银行",
                    "limit_up_streak": 2,
                    "consecutive_listed": True,
                    "consecutive_list_days": 2,
                }],
            },
            env={
                "DASHBOARD_GROK_BASE_URL": "https://grok.example/v1",
                "DASHBOARD_GROK_API_KEY": "grok-secret",
                "DASHBOARD_GROK_MODEL": "grok-test",
            },
            fetcher=lambda *_args, **_kwargs: self.fail("不应调用消息面预检模型"),
        )

        self.assertFalse(payload["continuous_news_configured"])
        self.assertEqual(payload["continuous_news_error"], "news_precheck_not_configured")
        self.assertEqual(
            payload["items"][0]["news_precheck"]["error"],
            "news_precheck_not_configured",
        )

    def test_news_precheck_reuses_same_day_success_without_duplicate_request(self):
        cached_news = {
            "checked": True,
            "available": True,
            "tone": "neutral",
            "tone_label": "中性",
            "summary": "暂无新增重大消息（中性）",
            "provider": "消息面预检模型",
        }
        payload = enrich_consecutive_dragon_tiger_news(
            {
                "date": "2026-07-16",
                "items": [{
                    "code": "000001.SZ",
                    "name": "平安银行",
                    "limit_up_streak": 2,
                    "consecutive_listed": True,
                    "consecutive_list_days": 2,
                }],
            },
            env={},
            previous_snapshot={
                "date": "2026-07-16",
                "continuous_news_configured": True,
                "continuous_news_model": "search-model",
                "items": [{
                    "code": "000001.SZ",
                    "name": "平安银行",
                    "news_precheck": cached_news,
                }],
            },
            fetcher=lambda *_args, **_kwargs: self.fail("不应重复调用消息面预检模型"),
        )

        self.assertTrue(payload["continuous_news_configured"])
        self.assertEqual(payload["continuous_news_model"], "search-model")
        self.assertEqual(payload["continuous_news_available_count"], 1)
        self.assertTrue(payload["items"][0]["news_precheck"]["cached"])

    def test_news_precheck_repairs_cached_unclassified_summary_without_request(self):
        payload = enrich_consecutive_dragon_tiger_news(
            {
                "date": "2026-07-24",
                "limit_up_news_complete": True,
                "items": [{
                    "code": "000595.SZ",
                    "name": "新能股份",
                    "limit_up_streak": 3,
                    "news_precheck": {
                        "checked": True,
                        "available": False,
                        "tone": "neutral",
                        "tone_label": "未识别",
                        "summary": "股东增持构成重大利好，无其他利空或中性消息。",
                        "error": "unclassified_response",
                    },
                }],
            },
            env={},
            fetcher=lambda *_args, **_kwargs: self.fail("不应重复调用消息面预检模型"),
        )

        record = payload["items"][0]["news_precheck"]
        self.assertTrue(record["available"])
        self.assertEqual(record["tone_label"], "利好")
        self.assertTrue(record["repaired_locally"])
        self.assertEqual(payload["limit_up_news_available_count"], 1)

    def test_news_precheck_queries_every_unchecked_stock_with_bounded_concurrency(self):
        captured = {}

        def fake_fetcher(candidates, config, **kwargs):
            captured["codes"] = [item["code"] for item in candidates]
            captured["concurrency"] = config.concurrency
            captured["max_candidates"] = kwargs["max_candidates"]
            return [{
                "code": item["code"],
                "name": item["name"],
                "checked": True,
                "available": True,
                "tone": "neutral",
                "tone_label": "中性",
                "summary": f"{item['name']}暂无重大消息（中性）",
                "error": "",
            } for item in candidates]

        stocks = [{
            "code": f"00000{index}.SZ",
            "name": f"样本{index}",
            "limit_up_streak": 2,
            "consecutive_listed": True,
            "consecutive_list_days": 2,
            "net_amount_yuan": index,
        } for index in range(1, 8)]
        payload = enrich_consecutive_dragon_tiger_news(
            {
                "date": "2026-07-16",
                "generated_at": "2026-07-16T19:23:00+08:00",
                "items": stocks,
            },
            env={
                "DASHBOARD_NEWS_BASE_URL": "https://news.example/v1",
                "DASHBOARD_NEWS_API_KEY": "news-secret",
                "DASHBOARD_NEWS_MODEL": "search-model",
                "DASHBOARD_NEWS_CONCURRENCY": "5",
                "IWENCAI_DRAGON_TIGER_CRON": "5 17 * * 1-5",
            },
            fetcher=fake_fetcher,
            now=datetime.fromisoformat("2026-07-16T19:24:30+08:00"),
        )

        self.assertEqual(len(captured["codes"]), 7)
        self.assertEqual(captured["max_candidates"], 7)
        self.assertEqual(captured["concurrency"], 5)
        self.assertEqual(payload["continuous_news_checked_count"], 7)
        self.assertEqual(payload["continuous_news_pending_count"], 0)
        self.assertTrue(payload["continuous_news_complete"])
        self.assertEqual(
            payload["continuous_news_started_at"],
            "2026-07-16T17:05:00+08:00",
        )
        self.assertEqual(
            payload["continuous_news_completed_at"],
            "2026-07-16T19:24:30+08:00",
        )

    def test_news_precheck_only_queries_unchecked_stocks_on_same_day_refresh(self):
        captured = {}
        checked_record = {
            "checked": True,
            "available": False,
            "tone": "neutral",
            "tone_label": "不可用",
            "summary": "",
            "error": "request_RuntimeError",
            "provider": "消息面预检模型",
        }

        def fake_fetcher(candidates, _config, **kwargs):
            captured["codes"] = [item["code"] for item in candidates]
            captured["max_candidates"] = kwargs["max_candidates"]
            return [{
                "code": "000002.SZ",
                "name": "万科A",
                "checked": True,
                "available": True,
                "tone": "positive",
                "tone_label": "利好",
                "summary": "新增重大合同（利好）",
                "error": "",
            }]

        payload = enrich_consecutive_dragon_tiger_news(
            {
                "date": "2026-07-16",
                "generated_at": "2026-07-16T20:10:00+08:00",
                "items": [{
                    "code": "000001.SZ",
                    "name": "平安银行",
                    "limit_up_streak": 2,
                    "consecutive_listed": True,
                    "consecutive_list_days": 2,
                }, {
                    "code": "000002.SZ",
                    "name": "万科A",
                    "limit_up_streak": 2,
                    "consecutive_listed": True,
                    "consecutive_list_days": 2,
                }],
            },
            env={
                "DASHBOARD_NEWS_BASE_URL": "https://news.example/v1",
                "DASHBOARD_NEWS_API_KEY": "news-secret",
                "DASHBOARD_NEWS_MODEL": "search-model",
            },
            previous_snapshot={
                "date": "2026-07-16",
                "continuous_news_started_at": "2026-07-16T18:47:00+08:00",
                "items": [{
                    "code": "000001.SZ",
                    "name": "平安银行",
                    "news_precheck": checked_record,
                }, {
                    "code": "000002.SZ",
                    "name": "万科A",
                    "news_precheck": {
                        "checked": False,
                        "available": False,
                        "error": "pending_news_precheck",
                    },
                }],
            },
            fetcher=fake_fetcher,
        )

        self.assertEqual(captured["codes"], ["000002.SZ"])
        self.assertEqual(captured["max_candidates"], 1)
        self.assertEqual(payload["continuous_news_checked_count"], 2)
        self.assertEqual(payload["continuous_news_pending_codes"], [])
        self.assertTrue(payload["continuous_news_complete"])
        self.assertEqual(
            payload["continuous_news_started_at"],
            "2026-07-16T18:47:00+08:00",
        )
        self.assertEqual(
            payload["items"][0]["news_precheck"]["error"],
            "request_RuntimeError",
        )

    def test_news_precheck_uses_scheduled_run_key_for_start_time(self):
        payload = enrich_consecutive_dragon_tiger_news(
            {
                "date": "2026-07-16",
                "generated_at": "2026-07-16T19:23:00+08:00",
                "items": [{
                    "code": "000001.SZ",
                    "name": "平安银行",
                    "limit_up_streak": 2,
                    "consecutive_listed": True,
                    "consecutive_list_days": 2,
                }],
            },
            env={
                "DASHBOARD_NEWS_BASE_URL": "https://news.example/v1",
                "DASHBOARD_NEWS_API_KEY": "news-secret",
                "DASHBOARD_NEWS_MODEL": "search-model",
                "IWENCAI_DRAGON_TIGER_CRON": "*/10 17-20 * * 1-5",
                "NIUONE_CRON_RUN_KEY": "6a72470cc5e1:202607161740",
            },
            fetcher=lambda candidates, _config, **_kwargs: [{
                "code": candidates[0]["code"],
                "name": candidates[0]["name"],
                "checked": True,
                "available": True,
                "tone": "neutral",
                "tone_label": "中性",
                "summary": "暂无重大消息（中性）",
                "error": "",
            }],
            now=datetime.fromisoformat("2026-07-16T19:24:30+08:00"),
        )

        self.assertEqual(
            payload["continuous_news_started_at"],
            "2026-07-16T17:40:00+08:00",
        )

    def test_news_precheck_records_pending_stocks_when_model_is_not_configured(self):
        payload = enrich_consecutive_dragon_tiger_news(
            {
                "date": "2026-07-16",
                "generated_at": "2026-07-16T17:35:00+08:00",
                "items": [{
                    "code": "000001.SZ",
                    "name": "平安银行",
                    "limit_up_streak": 2,
                    "consecutive_listed": True,
                    "consecutive_list_days": 2,
                }],
            },
            env={},
            fetcher=lambda *_args, **_kwargs: self.fail("未配置时不应发起检索"),
        )

        self.assertEqual(payload["continuous_news_checked_codes"], [])
        self.assertEqual(payload["continuous_news_pending_codes"], ["000001.SZ"])
        self.assertEqual(payload["continuous_news_pending_count"], 1)
        self.assertFalse(payload["continuous_news_complete"])
        self.assertEqual(
            payload["continuous_news_started_at"],
            "2026-07-16T18:00:00+08:00",
        )

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
                    "涨停原因[20260716]": "--",
                    "涨停原因类别[20260716]": "金融科技+股份回购",
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
        self.assertEqual(
            payload["items"][0]["limit_up_reason"],
            "金融科技+股份回购",
        )
        self.assertEqual(
            payload["items"][0]["limit_up_reason_category"],
            "金融科技+股份回购",
        )
        self.assertIsNone(payload["items"][0]["limit_down_streak"])
        self.assertEqual(payload["items"][1]["limit_down_streak"], 2)
        self.assertEqual(payload["items"][0]["list_date"], "2026-07-16")
        self.assertEqual(payload["items"][0]["sector"], "股份制银行")
        self.assertEqual(payload["unique_count"], 2)
        query, kwargs = client.calls[0]
        self.assertEqual(
            query,
            "2026年7月16日龙虎榜上榜股票、上榜原因、龙虎榜买入金额、卖出金额、净买入额、连续涨停天数、最近连续跌停天数、涨停原因、涨停原因类别",
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
                "涨停原因[20260716]": "国产大模型应用持续活跃",
                "涨停原因类别[20260716]": "人工智能",
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
        self.assertEqual(first["limit_up_reason"], "国产大模型应用持续活跃")
        self.assertEqual(first["limit_up_reason_category"], "人工智能")
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

    def test_core_snapshot_keeps_explicit_seat_incomplete_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            core = {
                "enabled": True,
                "available": True,
                "source": "同花顺问财",
                "date": "2026-07-16",
                "seat_query": "2026年7月16日龙虎榜营业部",
                "seat_data_complete": False,
                "seat_enrichment_pending": True,
                "snapshot_stage": "core",
                "items": [{
                    "code": "000001.SZ",
                    "name": "平安银行",
                    "seats": [],
                }],
            }

            self.assertTrue(write_dragon_tiger_snapshot(path, core))
            loaded = read_dragon_tiger_snapshot(path, trade_date="2026-07-16")

            self.assertIsNotNone(loaded)
            self.assertFalse(loaded["seat_data_complete"])
            self.assertTrue(loaded["seat_enrichment_pending"])

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

    def test_expire_legacy_archives_removes_only_dated_snapshot_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_dir = Path(tmp) / "iwencai_dragon_tiger"
            archive_dir.mkdir()
            for name in ("2026-07-15.json", "2026-07-16.json"):
                (archive_dir / name).write_text("{}", encoding="utf-8")
            marker = archive_dir / "README.txt"
            marker.write_text("keep", encoding="utf-8")

            self.assertEqual(expire_dragon_tiger_archives(archive_dir), 2)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertEqual(expire_dragon_tiger_archives(archive_dir), 0)


if __name__ == "__main__":
    unittest.main()
