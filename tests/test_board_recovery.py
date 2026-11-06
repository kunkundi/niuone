"""Offline coverage for complete classification recovery and atomic publication."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from app.market_data import board_recovery as recovery
from app.market_data import eastmoney_boards as boards


class BoardRecoveryTests(unittest.TestCase):
    day = "2024-04-12"
    now = datetime(2024, 4, 12, 16, 40, tzinfo=recovery.BEIJING)

    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.directory / "boards.json"
        self.enterContext(patch.object(recovery, "MIN_LISTED", 4))
        codes = ["000001", "000002", "300001", "600001", "688001"]
        self.listed = set(codes)
        timestamp = int(self.now.replace(hour=15, minute=0).timestamp())
        self.rows = {code: {"f12":code, "f13":int(code.startswith("6")), "f14":"合成样本",
                            "f100":"新行业", "f102":"地区", "f103":"题材甲,题材乙", "f124":timestamp} for code in codes}
        self.old = boards.EastmoneyBoardSnapshot("2024-04-11 15:00:00", "2024-04-11", {
            code:boards.EastmoneyStockBoard(code, industry="旧行业") for code in codes[:-1]})
        self.path.write_text(json.dumps(self.old.to_dict()), encoding="utf-8")

    def chart(self, query, deadline):
        if query.startswith("i:"):
            codes = [entry.split(".")[1] for entry in query.split(",")]
            return len(codes), [deepcopy(self.rows[code]) for code in codes]
        selected = [code for code in self.rows if query == boards.EASTMONEY_A_SHARE_FILTER or recovery._segment(code)==query]
        # The board list only returns a sample; its declared count is authoritative.
        return len(selected), [deepcopy(self.rows[selected[0]])]

    def build(self, provider=None):
        return recovery.verified_snapshot(self.old, self.listed, self.day, now=self.now,
                                          deadline=time.monotonic()+20, chart=provider or self.chart)

    def test_sample_is_never_published_as_a_full_list_and_new_listings_are_included(self):
        with patch.object(recovery, "BATCH_SIZE", 2):
            refreshed = self.build()
        self.assertEqual(set(refreshed.stocks), self.listed)
        self.assertEqual(refreshed.captured_at, self.day+" 15:00:00")
        self.assertEqual(refreshed.stocks["688001"].themes, ("题材甲","题材乙"))
        self.assertTrue(all(stock.industry=="新行业" for stock in refreshed.stocks.values()))

    def test_genuine_empty_concepts_preserve_the_industry_only_fallback(self):
        for num in range(3, 9):
            code = f"000{num:03d}"
            self.listed.add(code)
            self.rows[code] = {**self.rows["000001"], "f12": code}
        self.rows["688001"]["f103"] = "-"
        refreshed = self.build()
        self.assertEqual(refreshed.stocks["688001"].concepts, ())
        self.assertEqual(refreshed.stocks["688001"].themes, ("新行业",))

    def test_partial_duplicate_wrong_venue_and_missing_fields_retain_prior_snapshot(self):
        initial = self.path.read_bytes()
        for mode in ("missing", "duplicate", "exchange", "field", "count", "no_industries", "no_concepts"):
            def broken(query, deadline):
                total, rows = self.chart(query, deadline)
                if query.startswith("i:"):
                    if mode=="missing": rows.pop()
                    if mode=="duplicate": rows[-1]=rows[0]
                    if mode=="exchange": rows[0]["f13"]=7
                    if mode=="field": del rows[0]["f103"]
                    if mode=="no_industries":
                        for row in rows: row["f100"] = "-"
                    if mode=="no_concepts":
                        for row in rows: row["f103"] = "-"
                elif mode=="count" and query==recovery.SEGMENTS[0]:
                    total+=1
                return total,rows
            with self.subTest(mode=mode), self.assertRaises(boards.EastmoneyBoardError):
                boards.publish_board_snapshot(self.path, self.build(broken))
            self.assertEqual(self.path.read_bytes(), initial)
            self.assertFalse((self.directory/"eastmoney_board_snapshots").exists())

    def test_wrong_day_future_and_expired_deadline_are_rejected(self):
        for moment in (self.now-timedelta(days=1), self.now.replace(hour=10), self.now+timedelta(minutes=5)):
            with self.subTest(moment=moment):
                for row in self.rows.values(): row["f124"] = int(moment.timestamp())
                with self.assertRaises(boards.EastmoneyBoardError): self.build()
        with self.assertRaisesRegex(boards.EastmoneyBoardError, "deadline"):
            recovery.verified_snapshot(self.old,self.listed,self.day,now=self.now,deadline=time.monotonic()-1,
                                       chart=lambda *_: self.fail("expired budget must not start a request"))

    def test_publish_keeps_the_newer_observation_and_preserves_latest_on_archive_failure(self):
        fresh = self.build()
        old_bytes = self.path.read_bytes()
        with patch.object(boards,"write_json_cache",side_effect=OSError("storage full")), self.assertRaises(OSError):
            boards.publish_board_snapshot(self.path, fresh)
        self.assertEqual(self.path.read_bytes(),old_bytes)
        boards.publish_board_snapshot(self.path, fresh)
        current_bytes = self.path.read_bytes()
        self.assertEqual(boards.publish_board_snapshot(self.path, self.old).captured_at, fresh.captured_at)
        self.assertEqual(self.path.read_bytes(),current_bytes)
        archived = self.directory/"eastmoney_board_snapshots"/(self.day+".json")
        self.assertEqual(json.loads(archived.read_bytes()), fresh.to_dict())

    def test_scheduler_skips_an_existing_lease_then_publishes_once(self):
        days = {"calendar_cached":True,"is_trading_day":True,"date":self.day}
        lock = self.path.with_name(self.path.name+".recover.lock")
        lock.touch()
        compute = recovery.verified_snapshot
        with patch.object(recovery, "trading_day_status", return_value=days), \
             patch.object(recovery,"fetch_exchange_listing",return_value=[(c,"样本") for c in self.listed]) as listing, \
             patch.object(recovery,"verified_snapshot",side_effect=lambda *a,**kw: compute(*a,chart=self.chart,**kw)) as build:
            self.assertIn("进行中",recovery.recover_if_due(self.path,self.now))
            listing.assert_not_called()
            lock.unlink()
            self.assertIn("完整分类",recovery.recover_if_due(self.path,self.now))
            self.assertIn("已就绪",recovery.recover_if_due(self.path,self.now))
        build.assert_called_once()
        self.assertFalse(lock.exists())
        self.assertEqual(boards.read_eastmoney_board_snapshot(self.path).captured_at,self.day+" 15:00:00")

    def test_cold_start_and_holidays_do_not_trigger_chart_requests(self):
        with patch.object(recovery,"fetch_exchange_listing") as listing:
            with patch.object(recovery,"trading_day_status",return_value={"calendar_cached":False}):
                recovery.recover_if_due(self.path,self.now)
            with patch.object(recovery,"trading_day_status",return_value={"calendar_cached":True,"is_trading_day":False}):
                recovery.recover_if_due(self.path,self.now)
            with patch.object(recovery,"trading_day_status",return_value={"calendar_cached":True,"is_trading_day":True,"date":self.day}):
                recovery.recover_if_due(self.directory/"missing.json",self.now)
                recovery.recover_if_due(self.path,self.now.replace(hour=10))
        listing.assert_not_called()

    def test_failed_recovery_releases_lease_and_keeps_published_bytes(self):
        before = self.path.read_bytes()
        days = {"calendar_cached":True,"is_trading_day":True,"date":self.day}
        with patch.object(recovery,"trading_day_status",return_value=days), \
             patch.object(recovery,"fetch_exchange_listing",side_effect=OSError("exchange unavailable")), \
             self.assertRaises(OSError):
            recovery.recover_if_due(self.path,self.now)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse(self.path.with_name(self.path.name+".recover.lock").exists())

    def test_chart_request_has_a_bounded_body_and_shared_deadline(self):
        body = json.dumps({"rc":0,"data":{"total":2,"diff":[{"f12":"600001"}]}}).encode()
        class Response:
            def __enter__(inner): return inner
            def __exit__(inner, *_args): pass
            def read(inner, size):
                self.assertLessEqual(size, recovery.MAX_BYTES + 1)
                return body[:size]
        with patch.object(recovery, "urlopen", return_value=Response()) as get:
            total, rows = recovery.get_chart(recovery.SEGMENTS[0], time.monotonic()+4)
            self.assertEqual((total,len(rows)), (2,1))
            self.assertLessEqual(get.call_args.kwargs["timeout"], 4)
        with patch.object(recovery, "urlopen") as get, self.assertRaises(boards.EastmoneyBoardError):
            recovery.get_chart(recovery.SEGMENTS[0], time.monotonic()-1)
        get.assert_not_called()
