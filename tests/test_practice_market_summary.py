#!/usr/bin/env python3
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.dashboard import practice_market_summary


class PracticeMarketSummaryTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {
                "time_text": "2026-07-14 11:40:08",
                "title": "A股午盘总结",
                "content": "💬 午盘跌停收敛，但资金仍然分散。",
                "metadata": {"decision_guidance": ["风险级别：谨慎", "开仓节奏：午后最多1笔"]},
            },
            {
                "time_text": "2026-07-14 08:00:04",
                "title": "隔夜美股盘面总结",
                "content": "隔夜美股盘面总结来了",
                "metadata": {"decision_guidance": ["风险级别：防守"]},
            },
            {
                "time_text": "2026-07-14 09:25:10",
                "title": "A股竞价盘前总结",
                "content": "💬 竞价风险端偏强。",
                "metadata": {"decision_guidance": ["风险级别：防守"]},
            },
            {
                "time_text": "2026-07-13 15:10:00",
                "title": "A股盘后总结",
                "content": "前一日盘面",
            },
        ]

    def test_collects_all_today_a_share_scans_in_time_order(self):
        scans = practice_market_summary.collect_daily_a_share_scans(self.records, "2026-07-14")

        self.assertEqual([scan["title"] for scan in scans], ["A股竞价盘前总结", "A股午盘总结"])
        self.assertEqual(scans[-1]["guidance_lines"], ["风险级别：谨慎", "开仓节奏：午后最多1笔"])

    def test_replay_sources_include_prior_us_session_summary_before_a_share_scans(self):
        sources = practice_market_summary.collect_market_replay_sources(self.records, "2026-07-14")

        self.assertEqual([source["source_kind"] for source in sources], [
            "overnight_us", "a_share_scan", "a_share_scan",
        ])
        self.assertEqual(sources[0]["title"], "隔夜美股盘面总结")

    def test_status_marks_cached_summary_stale_after_new_scan(self):
        now = datetime(2026, 7, 14, 12, 0, 0)
        scans = practice_market_summary.collect_daily_a_share_scans(self.records, "2026-07-14")
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "summary.json"
            cache_file.write_text(json.dumps({
                "ok": True,
                "available": True,
                "date": "2026-07-14",
                "schema_version": practice_market_summary.SUMMARY_SCHEMA_VERSION,
                "summary": "旧总结",
                "source_fingerprint": practice_market_summary.source_fingerprint(scans[:1]),
            }, ensure_ascii=False), encoding="utf-8")

            status = practice_market_summary.summary_status(self.records, cache_file, now)

        self.assertTrue(status["available"])
        self.assertTrue(status["stale"])
        self.assertEqual(status["scan_count"], 2)
        self.assertEqual(status["us_summary_count"], 1)
        self.assertEqual(status["source_count"], 3)

    def test_local_fallback_summarizes_market_evolution_without_trade_guidance(self):
        scans = practice_market_summary.collect_daily_a_share_scans(self.records, "2026-07-14")

        result = practice_market_summary._local_summary(scans, "2026-07-14")

        self.assertIn("由防守演变为谨慎", result["summary"])
        self.assertEqual(result["tone_label"], "谨慎")
        self.assertEqual(len(result["trend_lines"]), 2)
        self.assertNotIn("午后最多1笔", json.dumps(result, ensure_ascii=False))

    def test_local_fallback_keeps_us_backdrop_separate_from_a_share_evolution(self):
        sources = practice_market_summary.collect_market_replay_sources(self.records, "2026-07-14")

        result = practice_market_summary._local_summary(sources, "2026-07-14")

        self.assertIn("前一美股交易日整体呈防守基调", result["summary"])
        self.assertIn("2次A股盘面扫描", result["summary"])
        self.assertTrue(result["trend_lines"][0].startswith("前日美股"))


if __name__ == "__main__":
    unittest.main()
