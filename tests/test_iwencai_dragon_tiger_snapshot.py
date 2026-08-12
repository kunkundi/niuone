#!/usr/bin/env python3
"""Regression tests for rolling iWencai dragon-tiger snapshot retention."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
COMPAT = APP / "compat"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(COMPAT))

from dashboard.apis.iwencai_service import (  # noqa: E402
    read_dragon_tiger_snapshot,
    write_dragon_tiger_archive,
    write_dragon_tiger_snapshot,
)
from reports.a_share import iwencai_dragon_tiger_snapshot as snapshot_job  # noqa: E402


def _payload(trade_date: str, code: str) -> dict[str, object]:
    return {
        "enabled": True,
        "available": True,
        "source": "同花顺问财",
        "date": trade_date,
        "items": [{"code": code, "name": "样本股票"}],
    }


class IwencaiDragonTigerSnapshotTests(unittest.TestCase):
    def test_complete_snapshot_refreshes_expanded_candidate_tracking_without_query(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            payload = _payload("2026-07-24", "000001.SZ")
            payload["items"] = [
                {
                    "code": "000001.SZ",
                    "name": "连板样本",
                    "limit_up_streak": 2,
                    "consecutive_listed": False,
                    "consecutive_list_days": 1,
                    "news_precheck": {
                        "checked": True,
                        "available": True,
                        "tone": "neutral",
                        "summary": "连板样本暂无重大消息",
                    },
                },
                {
                    "code": "000002.SZ",
                    "name": "连榜样本",
                    "limit_up_streak": 0,
                    "consecutive_listed": True,
                    "consecutive_list_days": 2,
                    "news_precheck": {
                        "checked": True,
                        "available": True,
                        "tone": "positive",
                        "summary": "连榜样本出现利好消息",
                    },
                },
            ]
            payload.update({
                "limit_up_news_candidate_count": 1,
                "limit_up_news_checked_codes": ["000001.SZ"],
                "limit_up_news_pending_codes": [],
                "limit_up_news_checked_count": 1,
                "limit_up_news_pending_count": 0,
                "limit_up_news_available_count": 1,
                "limit_up_news_complete": True,
                "continuous_news_checked_codes": ["000001.SZ"],
                "continuous_news_pending_codes": [],
                "continuous_news_checked_count": 1,
                "continuous_news_pending_count": 0,
                "continuous_news_available_count": 1,
                "continuous_news_complete": True,
            })
            self.assertTrue(write_dragon_tiger_snapshot(path, payload))

            first, first_saved = snapshot_job.backfill_snapshot_news(path, env={})
            second, second_saved = snapshot_job.backfill_snapshot_news(path, env={})

            self.assertTrue(first_saved)
            self.assertFalse(second_saved)
            self.assertEqual(first["limit_up_news_candidate_count"], 2)
            self.assertEqual(first["limit_up_news_checked_count"], 2)
            self.assertEqual(first["limit_up_news_checked_codes"], ["000001.SZ", "000002.SZ"])
            self.assertEqual(first["limit_up_news_pending_count"], 0)
            self.assertTrue(first["limit_up_news_complete"])
            self.assertEqual(second["continuous_news_checked_count"], 2)

    def test_complete_snapshot_backfills_unchecked_consecutive_listing(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            payload = _payload("2026-07-24", "920117.BJ")
            payload["limit_up_news_complete"] = True
            payload["items"][0].update({
                "limit_up_streak": 0,
                "consecutive_listed": True,
                "consecutive_list_days": 2,
            })
            self.assertTrue(write_dragon_tiger_snapshot(path, payload))
            calls = []
            original_enrich = snapshot_job.enrich_consecutive_dragon_tiger_news
            try:
                def fake_enrich(current, **_kwargs):
                    calls.append(current["date"])
                    result = dict(current)
                    result["items"] = [dict(item) for item in current["items"]]
                    result["items"][0]["news_precheck"] = {
                        "checked": True,
                        "available": True,
                        "tone": "neutral",
                        "tone_label": "中性",
                        "summary": "暂无重大消息（中性）",
                    }
                    result["limit_up_news_candidate_count"] = 1
                    result["limit_up_news_checked_codes"] = ["920117.BJ"]
                    result["limit_up_news_pending_codes"] = []
                    result["limit_up_news_checked_count"] = 1
                    result["limit_up_news_pending_count"] = 0
                    result["limit_up_news_available_count"] = 1
                    result["limit_up_news_complete"] = True
                    result["continuous_news_checked_codes"] = ["920117.BJ"]
                    result["continuous_news_pending_codes"] = []
                    result["continuous_news_checked_count"] = 1
                    result["continuous_news_pending_count"] = 0
                    result["continuous_news_available_count"] = 1
                    result["continuous_news_complete"] = True
                    return result

                snapshot_job.enrich_consecutive_dragon_tiger_news = fake_enrich
                first, first_saved = snapshot_job.backfill_snapshot_news(path, env={})
                second, second_saved = snapshot_job.backfill_snapshot_news(path, env={})
            finally:
                snapshot_job.enrich_consecutive_dragon_tiger_news = original_enrich

            self.assertTrue(first_saved)
            self.assertFalse(second_saved)
            self.assertEqual(calls, ["2026-07-24"])
            self.assertEqual(first["items"][0]["news_precheck"]["tone_label"], "中性")

    def test_complete_snapshot_locally_repairs_unclassified_news_once(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            payload = _payload("2026-07-24", "000595.SZ")
            payload["limit_up_news_complete"] = True
            payload["continuous_news_complete"] = True
            payload["items"][0].update({
                "limit_up_streak": 3,
                "news_precheck": {
                    "checked": True,
                    "available": False,
                    "tone": "neutral",
                    "tone_label": "未识别",
                    "summary": "股东增持构成重大利好，无其他利空或中性消息。",
                    "fetched_at": "2026-07-26T18:35:49+08:00",
                    "error": "unclassified_response",
                },
            })
            self.assertTrue(write_dragon_tiger_snapshot(path, payload))

            first, first_saved = snapshot_job.backfill_snapshot_news(path, env={})
            second, second_saved = snapshot_job.backfill_snapshot_news(path, env={})

            self.assertTrue(first_saved)
            self.assertFalse(second_saved)
            self.assertEqual(first["items"][0]["news_precheck"]["tone_label"], "利好")
            self.assertTrue(first["items"][0]["news_precheck"]["repaired_locally"])
            self.assertTrue(second["items"][0]["news_precheck"]["available"])

    def test_backfill_queries_pending_snapshot_once_and_stops_after_completion(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            pending = _payload("2026-07-24", "000001.SZ")
            pending["items"][0].update({
                "limit_up_streak": 2,
                "consecutive_listed": False,
                "consecutive_list_days": 1,
                "news_precheck": {
                    "checked": False,
                    "available": False,
                    "error": "news_precheck_not_configured",
                },
            })
            self.assertTrue(write_dragon_tiger_snapshot(path, pending))
            calls = []
            original_enrich = snapshot_job.enrich_consecutive_dragon_tiger_news
            try:
                def fake_enrich(payload, **_kwargs):
                    calls.append(payload["date"])
                    result = dict(payload)
                    result["items"] = [dict(item) for item in payload["items"]]
                    result["items"][0]["news_precheck"] = {
                        "checked": True,
                        "available": True,
                        "summary": "周五消息面已补检（中性）",
                    }
                    result["limit_up_news_complete"] = True
                    result["limit_up_news_candidate_count"] = 1
                    result["limit_up_news_checked_codes"] = ["000001.SZ"]
                    result["limit_up_news_pending_codes"] = []
                    result["limit_up_news_checked_count"] = 1
                    result["limit_up_news_pending_count"] = 0
                    result["limit_up_news_available_count"] = 1
                    result["continuous_news_complete"] = True
                    result["continuous_news_checked_codes"] = ["000001.SZ"]
                    result["continuous_news_pending_codes"] = []
                    result["continuous_news_checked_count"] = 1
                    result["continuous_news_pending_count"] = 0
                    result["continuous_news_available_count"] = 1
                    return result

                snapshot_job.enrich_consecutive_dragon_tiger_news = fake_enrich
                first, first_saved = snapshot_job.backfill_snapshot_news(path, env={})
                second, second_saved = snapshot_job.backfill_snapshot_news(path, env={})
            finally:
                snapshot_job.enrich_consecutive_dragon_tiger_news = original_enrich

            self.assertTrue(first_saved)
            self.assertFalse(second_saved)
            self.assertEqual(calls, ["2026-07-24"])
            self.assertTrue(first["limit_up_news_complete"])
            self.assertTrue(second["limit_up_news_complete"])

    def test_weekend_catch_up_targets_friday_after_configured_query_time(self):
        original_calendar = snapshot_job.trading_day_status
        try:
            snapshot_job.trading_day_status = lambda *_args, **_kwargs: {
                "date": "2026-07-26",
                "is_trading_day": False,
                "previous_trading_day": "2026-07-24",
            }
            target = snapshot_job.catch_up_trade_date(
                env={"IWENCAI_DRAGON_TIGER_CRON": "35 17 * * 1-5"},
                now=datetime.fromisoformat("2026-07-26T18:10:00+08:00"),
            )
        finally:
            snapshot_job.trading_day_status = original_calendar

        self.assertEqual(target, "2026-07-24")

    def test_catch_up_fetches_missing_friday_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            self.assertTrue(write_dragon_tiger_snapshot(path, _payload("2026-07-23", "000001.SZ")))
            calls = []
            original_calendar = snapshot_job.trading_day_status
            original_refresh = snapshot_job.refresh_snapshot
            try:
                snapshot_job.trading_day_status = lambda *_args, **_kwargs: {
                    "date": "2026-07-26",
                    "is_trading_day": False,
                    "previous_trading_day": "2026-07-24",
                }

                def fake_refresh(_path, *, env=None, trade_date=None):
                    calls.append((env, trade_date))
                    return _payload(str(trade_date), "000002.SZ"), True

                snapshot_job.refresh_snapshot = fake_refresh
                payload, saved = snapshot_job.catch_up_snapshot(
                    path,
                    env={"IWENCAI_DRAGON_TIGER_CRON": "0 18 * * 1-5"},
                    now=datetime.fromisoformat("2026-07-26T18:10:00+08:00"),
                )
            finally:
                snapshot_job.trading_day_status = original_calendar
                snapshot_job.refresh_snapshot = original_refresh

            self.assertTrue(saved)
            self.assertEqual(payload["date"], "2026-07-24")
            self.assertEqual(calls, [({"IWENCAI_DRAGON_TIGER_CRON": "0 18 * * 1-5"}, "2026-07-24")])

    def test_refresh_carries_consecutive_streak_into_new_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            self.assertTrue(write_dragon_tiger_snapshot(path, _payload("2026-07-15", "000001.SZ")))
            original_fetch = snapshot_job.fetch_dragon_tiger
            original_calendar = snapshot_job.trading_day_status
            try:
                def fake_fetch(*, on_core_payload=None):
                    result = _payload("2026-07-16", "000001.SZ")
                    result["items"][0]["limit_up_streak"] = 2
                    if on_core_payload is not None:
                        on_core_payload(result)
                    return result

                snapshot_job.fetch_dragon_tiger = fake_fetch
                snapshot_job.trading_day_status = lambda *_args, **_kwargs: {
                    "previous_trading_day": "2026-07-15",
                }
                payload, saved = snapshot_job.refresh_snapshot(path, env={})
            finally:
                snapshot_job.fetch_dragon_tiger = original_fetch
                snapshot_job.trading_day_status = original_calendar

            self.assertTrue(saved)
            self.assertEqual(payload["continuous_list_count"], 1)
            self.assertEqual(payload["items"][0]["consecutive_list_days"], 2)
            self.assertTrue(payload["items"][0]["consecutive_listed"])
            self.assertEqual(
                payload["items"][0]["news_precheck"]["error"],
                "news_precheck_not_configured",
            )
            latest = read_dragon_tiger_snapshot(path, trade_date="2026-07-16")
            self.assertEqual(latest["limit_up_news_pending_codes"], ["000001.SZ"])
            self.assertFalse(latest["limit_up_news_complete"])
            self.assertEqual(latest["continuous_news_checked_codes"], [])
            self.assertEqual(latest["continuous_news_pending_codes"], ["000001.SZ"])
            self.assertFalse(latest["continuous_news_complete"])
            self.assertEqual(
                latest["continuous_news_started_at"],
                "2026-07-16T18:00:00+08:00",
            )

    def test_refresh_persists_core_list_before_later_fetch_stage_is_interrupted(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            self.assertTrue(write_dragon_tiger_snapshot(path, _payload("2026-07-15", "000001.SZ")))
            original_fetch = snapshot_job.fetch_dragon_tiger
            original_calendar = snapshot_job.trading_day_status
            try:
                def interrupted_fetch(*, on_core_payload=None):
                    current = _payload("2026-07-16", "600000.SH")
                    current["items"][0]["limit_up_streak"] = 2
                    on_core_payload(current)
                    raise TimeoutError("seat enrichment exceeded the outer deadline")

                snapshot_job.fetch_dragon_tiger = interrupted_fetch
                snapshot_job.trading_day_status = lambda *_args, **_kwargs: {
                    "previous_trading_day": "2026-07-15",
                }
                with self.assertRaises(TimeoutError):
                    snapshot_job.refresh_snapshot(path, env={})
            finally:
                snapshot_job.fetch_dragon_tiger = original_fetch
                snapshot_job.trading_day_status = original_calendar

            latest = read_dragon_tiger_snapshot(path, trade_date="2026-07-16")
            self.assertIsNotNone(latest)
            self.assertEqual(latest["snapshot_stage"], "core")
            self.assertEqual(latest["items"][0]["code"], "600000.SH")
            self.assertEqual(latest["items"][0]["consecutive_list_days"], 1)

    def test_same_day_core_interruption_does_not_downgrade_complete_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            complete = _payload("2026-07-16", "600000.SH")
            complete.update({
                "seat_query": "2026年7月16日龙虎榜营业部",
                "seat_data_complete": True,
                "snapshot_stage": "news",
            })
            complete["items"][0].update({
                "limit_up_streak": 2,
                "seats": [{
                    "seat_name": "已保存席位",
                    "seat_category": "brokerage",
                }],
                "news_precheck": {
                    "checked": True,
                    "available": True,
                    "summary": "已保存消息",
                },
            })
            self.assertTrue(write_dragon_tiger_snapshot(path, complete))
            original_fetch = snapshot_job.fetch_dragon_tiger
            original_calendar = snapshot_job.trading_day_status
            try:
                def interrupted_fetch(*, on_core_payload=None):
                    current = _payload("2026-07-16", "600000.SH")
                    current["items"][0]["limit_up_streak"] = 2
                    on_core_payload(current)
                    raise TimeoutError("seat enrichment exceeded the outer deadline")

                snapshot_job.fetch_dragon_tiger = interrupted_fetch
                snapshot_job.trading_day_status = lambda *_args, **_kwargs: {
                    "previous_trading_day": "2026-07-15",
                }
                with self.assertRaises(TimeoutError):
                    snapshot_job.refresh_snapshot(path, env={})
            finally:
                snapshot_job.fetch_dragon_tiger = original_fetch
                snapshot_job.trading_day_status = original_calendar

            latest = read_dragon_tiger_snapshot(path, trade_date="2026-07-16")
            self.assertIsNotNone(latest)
            self.assertEqual(latest["snapshot_stage"], "news")
            self.assertEqual(latest["items"][0]["seats"][0]["seat_name"], "已保存席位")
            self.assertEqual(
                latest["items"][0]["news_precheck"]["summary"],
                "已保存消息",
            )

    def test_refresh_persists_details_before_news_enrichment_is_interrupted(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            original_fetch = snapshot_job.fetch_dragon_tiger
            original_enrich = snapshot_job.enrich_consecutive_dragon_tiger_news
            original_calendar = snapshot_job.trading_day_status
            try:
                def fake_fetch(*, on_core_payload=None):
                    current = _payload("2026-07-16", "600000.SH")
                    on_core_payload(current)
                    current["items"][0]["seats"] = [{"seat_name": "测试营业部"}]
                    return current

                snapshot_job.fetch_dragon_tiger = fake_fetch
                snapshot_job.enrich_consecutive_dragon_tiger_news = (
                    lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        TimeoutError("news enrichment exceeded the outer deadline")
                    )
                )
                snapshot_job.trading_day_status = lambda *_args, **_kwargs: {
                    "previous_trading_day": "2026-07-15",
                }
                with self.assertRaises(TimeoutError):
                    snapshot_job.refresh_snapshot(path, env={})
            finally:
                snapshot_job.fetch_dragon_tiger = original_fetch
                snapshot_job.enrich_consecutive_dragon_tiger_news = original_enrich
                snapshot_job.trading_day_status = original_calendar

            latest = read_dragon_tiger_snapshot(path, trade_date="2026-07-16")
            self.assertIsNotNone(latest)
            self.assertEqual(latest["snapshot_stage"], "details")
            self.assertEqual(latest["items"][0]["code"], "600000.SH")

    def test_same_day_news_interruption_does_not_downgrade_complete_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            complete = _payload("2026-07-16", "600000.SH")
            complete["snapshot_stage"] = "news"
            complete["items"][0]["news_precheck"] = {
                "checked": True,
                "available": True,
                "summary": "已保存消息",
            }
            self.assertTrue(write_dragon_tiger_snapshot(path, complete))
            original_fetch = snapshot_job.fetch_dragon_tiger
            original_enrich = snapshot_job.enrich_consecutive_dragon_tiger_news
            original_calendar = snapshot_job.trading_day_status
            try:
                def fake_fetch(*, on_core_payload=None):
                    current = _payload("2026-07-16", "600000.SH")
                    on_core_payload(current)
                    current["items"][0]["seats"] = [{"seat_name": "新席位"}]
                    return current

                snapshot_job.fetch_dragon_tiger = fake_fetch
                snapshot_job.enrich_consecutive_dragon_tiger_news = (
                    lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        TimeoutError("news enrichment exceeded the outer deadline")
                    )
                )
                snapshot_job.trading_day_status = lambda *_args, **_kwargs: {
                    "previous_trading_day": "2026-07-15",
                }
                with self.assertRaises(TimeoutError):
                    snapshot_job.refresh_snapshot(path, env={})
            finally:
                snapshot_job.fetch_dragon_tiger = original_fetch
                snapshot_job.enrich_consecutive_dragon_tiger_news = original_enrich
                snapshot_job.trading_day_status = original_calendar

            latest = read_dragon_tiger_snapshot(path, trade_date="2026-07-16")
            self.assertIsNotNone(latest)
            self.assertEqual(latest["snapshot_stage"], "news")
            self.assertEqual(
                latest["items"][0]["news_precheck"]["summary"],
                "已保存消息",
            )

    def test_same_day_seat_failure_preserves_complete_snapshot_enrichment(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            complete = _payload("2026-07-16", "600000.SH")
            complete.update({
                "seat_query": "2026年7月16日龙虎榜营业部",
                "seat_data_complete": True,
                "snapshot_stage": "news",
            })
            complete["items"][0].update({
                "limit_up_streak": 2,
                "seats": [{
                    "seat_name": "已保存席位",
                    "seat_category": "brokerage",
                }],
                "news_precheck": {
                    "checked": True,
                    "available": True,
                    "summary": "已保存消息",
                },
            })
            self.assertTrue(write_dragon_tiger_snapshot(path, complete))
            original_fetch = snapshot_job.fetch_dragon_tiger
            original_calendar = snapshot_job.trading_day_status
            try:
                def failed_seat_fetch(*, on_core_payload=None):
                    current = _payload("2026-07-16", "600000.SH")
                    current.update({
                        "seat_query": "2026年7月16日龙虎榜营业部",
                        "seat_data_complete": False,
                        "seat_enrichment_pending": True,
                    })
                    current["items"][0]["limit_up_streak"] = 2
                    on_core_payload(current)
                    current["seat_enrichment_pending"] = False
                    current["seat_error"] = "seat_timeout"
                    current["institution_error"] = "seat_timeout"
                    return current

                snapshot_job.fetch_dragon_tiger = failed_seat_fetch
                snapshot_job.trading_day_status = lambda *_args, **_kwargs: {
                    "previous_trading_day": "2026-07-15",
                }
                _payload_result, saved = snapshot_job.refresh_snapshot(path, env={})
            finally:
                snapshot_job.fetch_dragon_tiger = original_fetch
                snapshot_job.trading_day_status = original_calendar

            self.assertTrue(saved)
            latest = read_dragon_tiger_snapshot(path, trade_date="2026-07-16")
            self.assertIsNotNone(latest)
            self.assertEqual(latest["snapshot_stage"], "news")
            self.assertTrue(latest["seat_preserved_from_previous"])
            self.assertEqual(latest["items"][0]["seats"][0]["seat_name"], "已保存席位")
            self.assertEqual(
                latest["items"][0]["news_precheck"]["summary"],
                "已保存消息",
            )

    def test_catch_up_retries_same_day_core_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            core = _payload("2026-07-16", "600000.SH")
            core["snapshot_stage"] = "core"
            self.assertTrue(write_dragon_tiger_snapshot(path, core))
            calls = []
            original_target = snapshot_job.catch_up_trade_date
            original_refresh = snapshot_job.refresh_snapshot
            try:
                snapshot_job.catch_up_trade_date = lambda **_kwargs: "2026-07-16"

                def fake_refresh(_path, *, env=None, trade_date=None):
                    calls.append((env, trade_date))
                    result = _payload(str(trade_date), "600000.SH")
                    result["snapshot_stage"] = "details"
                    return result, True

                snapshot_job.refresh_snapshot = fake_refresh
                payload, saved = snapshot_job.catch_up_snapshot(path, env={})
            finally:
                snapshot_job.catch_up_trade_date = original_target
                snapshot_job.refresh_snapshot = original_refresh

            self.assertTrue(saved)
            self.assertEqual(payload["snapshot_stage"], "details")
            self.assertEqual(calls, [({}, "2026-07-16")])

    def test_failed_new_date_refresh_backfills_retained_snapshot_after_query(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            pending = _payload("2026-07-15", "000001.SZ")
            pending["items"][0].update({
                "limit_up_streak": 2,
                "news_precheck": {
                    "checked": False,
                    "available": False,
                    "error": "pending_news_precheck",
                },
            })
            self.assertTrue(write_dragon_tiger_snapshot(path, pending))
            calls = []
            original_fetch = snapshot_job.fetch_dragon_tiger
            original_enrich = snapshot_job.enrich_consecutive_dragon_tiger_news
            try:
                snapshot_job.fetch_dragon_tiger = lambda **_kwargs: {
                    "enabled": True,
                    "available": False,
                    "date": "2026-07-16",
                    "items": [],
                    "error": "upstream_unavailable",
                }

                def fake_enrich(current, **_kwargs):
                    calls.append(str(current.get("date") or ""))
                    result = dict(current)
                    result["items"] = [dict(item) for item in current["items"]]
                    result["items"][0]["news_precheck"] = {
                        "checked": True,
                        "available": True,
                        "summary": "旧快照消息已补检",
                    }
                    return result

                snapshot_job.enrich_consecutive_dragon_tiger_news = fake_enrich
                payload, saved = snapshot_job.refresh_snapshot(path, env={})
            finally:
                snapshot_job.fetch_dragon_tiger = original_fetch
                snapshot_job.enrich_consecutive_dragon_tiger_news = original_enrich

            self.assertFalse(saved)
            self.assertEqual(payload["error"], "upstream_unavailable")
            self.assertEqual(calls, ["2026-07-15"])
            retained = read_dragon_tiger_snapshot(path, trade_date="2026-07-15")
            self.assertIsNotNone(retained)
            self.assertEqual(
                retained["items"][0]["news_precheck"]["summary"],
                "旧快照消息已补检",
            )

    def test_next_success_replaces_latest_and_expires_legacy_archives(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            archive_dir = path.parent / "iwencai_dragon_tiger"
            self.assertTrue(write_dragon_tiger_snapshot(path, _payload("2026-07-15", "000001.SZ")))
            self.assertTrue(write_dragon_tiger_archive(archive_dir, _payload("2026-07-14", "000002.SZ")))
            original_fetch = snapshot_job.fetch_dragon_tiger
            try:
                snapshot_job.fetch_dragon_tiger = lambda **_kwargs: _payload("2026-07-16", "600000.SH")
                payload, saved = snapshot_job.refresh_snapshot(path)
            finally:
                snapshot_job.fetch_dragon_tiger = original_fetch

            self.assertTrue(saved)
            self.assertEqual(payload["expired_archive_count"], 1)
            self.assertFalse(archive_dir.exists())
            latest = read_dragon_tiger_snapshot(path, trade_date="2026-07-16")
            self.assertIsNotNone(latest)
            self.assertEqual(latest["items"][0]["code"], "600000.SH")

    def test_failed_or_empty_query_preserves_latest_and_legacy_data(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            archive_dir = path.parent / "iwencai_dragon_tiger"
            self.assertTrue(write_dragon_tiger_snapshot(path, _payload("2026-07-15", "000001.SZ")))
            self.assertTrue(write_dragon_tiger_archive(archive_dir, _payload("2026-07-14", "000002.SZ")))
            original_latest = path.read_bytes()
            original_fetch = snapshot_job.fetch_dragon_tiger
            try:
                snapshot_job.fetch_dragon_tiger = lambda **_kwargs: {
                    "enabled": True,
                    "available": True,
                    "date": "2026-07-16",
                    "items": [],
                }
                payload, saved = snapshot_job.refresh_snapshot(path)
            finally:
                snapshot_job.fetch_dragon_tiger = original_fetch

            self.assertFalse(saved)
            self.assertNotIn("expired_archive_count", payload)
            self.assertEqual(path.read_bytes(), original_latest)
            self.assertTrue((archive_dir / "2026-07-14.json").is_file())


if __name__ == "__main__":
    unittest.main()
