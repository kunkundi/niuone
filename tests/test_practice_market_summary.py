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

    def realtime_source(self, now=None):
        now = now or datetime(2026, 7, 14, 12, 0, 0)
        return practice_market_summary.build_realtime_market_snapshot(
            {
                "generated_at": "2026-07-14 12:00:00",
                "items": [
                    {"key": "sh", "name": "上证指数", "market_type": "a_index", "price": 3520.1, "change_pct": 0.42, "time": "2026-07-14 12:00:00"},
                    {"key": "cyb", "name": "创业板指", "market_type": "a_index", "price": 2240.2, "change_pct": -0.18, "time": "2026-07-14 12:00:00"},
                ],
            },
            {
                "generated_at": "2026-07-14 12:00:01",
                "gain_top": [{"name": "半导体", "pct": 2.3}, {"name": "通信", "pct": 1.8}],
                "loss_top": [{"name": "煤炭", "pct": -1.2}],
            },
            {
                "generated_at": "2026-07-14 12:00:02",
                "inflow": [
                    {"name": "半导体", "pct": 2.3, "net_flow_yi": 18.6},
                    {"name": "通信", "pct": 1.8, "net_flow_yi": 8.2},
                ],
                "outflow": [
                    {"name": "煤炭", "pct": -1.2, "net_flow_yi": -11.4},
                    {"name": "银行", "pct": -0.4, "net_flow_yi": -5.1},
                ],
            },
            now,
        )

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

    def test_realtime_snapshot_contains_indices_sectors_and_industry_fund_flow(self):
        snapshot = self.realtime_source()

        self.assertTrue(snapshot["complete"])
        self.assertIn("实时核心指数：上证指数", snapshot["content"])
        self.assertIn("行业板块涨幅前列：半导体 +2.30%", snapshot["content"])
        self.assertIn("行业主力净流入前列：半导体 +18.60亿", snapshot["content"])
        self.assertIn("实时热门行业综合榜：半导体", snapshot["content"])
        self.assertEqual(snapshot["snapshot"]["hot_sectors"][0]["name"], "半导体")
        self.assertEqual(snapshot["snapshot"]["captured_at"], "2026-07-14 12:00:00")

    def test_realtime_snapshot_does_not_let_concepts_displace_leading_industry(self):
        snapshot = practice_market_summary.build_realtime_market_snapshot(
            {
                "items": [
                    {"key": "sh", "name": "上证指数", "market_type": "a_index", "price": 3520.1, "change_pct": 1.2},
                ],
            },
            {
                "gain_top": [
                    {"name": "国家大基金持股", "pct": 10.78, "source": "概念"},
                    {"name": "半导体", "pct": 9.83, "source": "行业"},
                ],
                "loss_top": [
                    {"name": "可燃冰", "pct": -2.27, "source": "概念"},
                    {"name": "油气开采及服务", "pct": -3.84, "source": "行业"},
                ],
                "industry_gain_top": [
                    {"name": "半导体", "pct": 9.83, "source": "行业"},
                    {"name": "通信设备", "pct": 4.92, "source": "行业"},
                ],
                "industry_loss_top": [
                    {"name": "油气开采及服务", "pct": -3.84, "source": "行业"},
                ],
                "concept_gain_top": [
                    {"name": "国家大基金持股", "pct": 10.78, "source": "概念"},
                ],
                "concept_loss_top": [
                    {"name": "可燃冰", "pct": -2.27, "source": "概念"},
                ],
            },
            {
                "inflow": [
                    {"name": "半导体", "pct": 9.85, "net_flow_yi": 166.18},
                    {"name": "通信设备", "pct": 4.92, "net_flow_yi": 47.04},
                ],
                "outflow": [
                    {"name": "银行Ⅱ", "pct": -1.88, "net_flow_yi": -28.70},
                ],
            },
            datetime(2026, 7, 21, 11, 40, 0),
        )

        self.assertTrue(snapshot["complete"])
        self.assertEqual(snapshot["snapshot"]["sectors"]["gain_top"][0]["name"], "半导体")
        self.assertEqual(snapshot["snapshot"]["sectors"]["concept_gain_top"][0]["name"], "国家大基金持股")
        self.assertEqual(snapshot["snapshot"]["hot_sectors"][0]["name"], "半导体")
        self.assertTrue(snapshot["snapshot"]["hot_sectors"][0]["confirmed"])
        self.assertIn("实时热门行业综合榜为半导体", snapshot["summary"])

    def test_dashboard_fund_flow_and_market_breadth_pages_are_model_references(self):
        snapshot = self.realtime_source()
        enriched = practice_market_summary.add_dashboard_market_references(
            snapshot,
            industry_flow_payload={
                "available": True,
                "generated_at": "2026-07-14 12:00:02",
                "metric": "industry_main_net_flow",
                "metric_label": "今日主力净额",
                "nodes": [
                    {"name": "半导体", "role": "inflow", "net_flow_yi": 18.6},
                    {"name": "煤炭", "role": "outflow", "net_flow_yi": -11.4},
                ],
                "totals": {
                    "visible_inflow_yi": 18.6,
                    "visible_outflow_yi": 11.4,
                    "visible_balance_yi": 7.2,
                },
                "timeline": [
                    {
                        "generated_at": "2026-07-14 09:31:00",
                        "nodes": [{"name": "通信", "role": "inflow", "net_flow_yi": 8.2}],
                        "totals": {"visible_balance_yi": 2.0},
                    },
                    {
                        "generated_at": "2026-07-14 12:00:02",
                        "nodes": [{"name": "半导体", "role": "inflow", "net_flow_yi": 18.6}],
                        "totals": {"visible_balance_yi": 7.2},
                    },
                ],
            },
            market_breadth_payload={
                "available": True,
                "generated_at": "2026-07-14 12:00:03",
                "latest": {
                    "generated_at": "2026-07-14 12:00:03",
                    "red": 3100,
                    "green": 1900,
                    "flat": 100,
                    "limit_up": 68,
                    "limit_down": 7,
                    "broken_limit": 21,
                    "actual_turnover_yi": 7200,
                    "estimated_turnover_yi": 13200,
                    "previous_turnover_yi": 12500,
                    "turnover_increment_yi": 700,
                },
                "timeline": [{"generated_at": "2026-07-14 12:00:03"}],
            },
        )

        self.assertEqual(enriched["reference_pages"], {
            "industry_flow": True,
            "market_breadth": True,
        })
        self.assertIn("资金流动页日内对比", enriched["content"])
        self.assertIn("市场情绪页最新值：红盘3100只", enriched["content"])
        self.assertEqual(
            enriched["snapshot"]["industry_flow_page"]["sample_count"],
            2,
        )
        self.assertEqual(
            enriched["snapshot"]["market_breadth_page"]["turnover_increment_yi"],
            700,
        )

        sources = [
            *practice_market_summary.collect_market_replay_sources(
                self.records,
                "2026-07-14",
            ),
            enriched,
        ]
        local = practice_market_summary._local_summary(sources, "2026-07-14")
        prompt = practice_market_summary._model_messages(sources, "2026-07-14")
        self.assertTrue(any("资金流动页显示" in line for line in local["structure_lines"]))
        self.assertTrue(any("市场情绪页显示" in line for line in local["structure_lines"]))
        self.assertIn("资金流动页的行业主力净额", prompt[0]["content"])
        self.assertIn("市场情绪页的红绿盘", prompt[0]["content"])
        self.assertIn("红盘3100只", prompt[1]["content"])

    def test_model_prompt_requires_live_snapshot_comparison_conclusion(self):
        sources = practice_market_summary.collect_market_replay_sources(self.records, "2026-07-14")
        sources.append(self.realtime_source())

        messages = practice_market_summary._model_messages(sources, "2026-07-14")
        prompt = "\n".join(message["content"] for message in messages)

        self.assertIn("手动按钮刚抓取的实时A股盘面", prompt)
        self.assertIn("行业主力净流入前列", prompt)
        self.assertIn("实时热门行业综合榜", prompt)
        self.assertIn("不得用单个概念标签替代", prompt)
        self.assertIn('"comparison_lines"', prompt)
        self.assertIn("延续/强化/弱化/反转/轮动", prompt)

    def test_local_fallback_compares_realtime_snapshot_with_latest_existing_summary(self):
        sources = practice_market_summary.collect_market_replay_sources(self.records, "2026-07-14")
        sources.append(self.realtime_source())

        result = practice_market_summary._local_summary(sources, "2026-07-14")

        self.assertTrue(result["comparison_lines"])
        self.assertIn("点击时核心指数平均涨跌幅", result["comparison_lines"][0])
        self.assertTrue(any("轮动" in line for line in result["comparison_lines"]))
        self.assertIn("行业主力净流入集中在半导体", result["summary"])

    def test_generation_persists_forced_realtime_snapshot_and_previous_summary_context(self):
        now = datetime(2026, 7, 14, 12, 0, 0)
        captured = {}
        original_builder = practice_market_summary.build_daily_market_summary
        try:
            def fake_builder(sources, day):
                captured["sources"] = sources
                return {
                    "tone": "balanced",
                    "tone_label": "平衡",
                    "summary": "实时盘面对比完成。",
                    "comparison_lines": ["半导体资金方向得到强化。"],
                    "trend_lines": [],
                    "structure_lines": [],
                    "risk_lines": [],
                    "model_used": False,
                    "model_error": "",
                }

            practice_market_summary.build_daily_market_summary = fake_builder
            with tempfile.TemporaryDirectory() as tmp:
                cache_file = Path(tmp) / "summary.json"
                cache_file.write_text(json.dumps({
                    "ok": True,
                    "available": True,
                    "schema_version": practice_market_summary.SUMMARY_SCHEMA_VERSION,
                    "date": "2026-07-14",
                    "generated_at": "2026-07-14 11:50:00",
                    "summary": "上一版总结",
                    "source_fingerprint": "old",
                }, ensure_ascii=False), encoding="utf-8")

                result = practice_market_summary.generate_and_store_summary(
                    self.records,
                    cache_file,
                    now,
                    realtime_snapshot_provider=lambda _now: self.realtime_source(_now),
                    require_realtime=True,
                )
                status = practice_market_summary.summary_status(self.records, cache_file, now)
        finally:
            practice_market_summary.build_daily_market_summary = original_builder

        source_kinds = [source["source_kind"] for source in captured["sources"]]
        self.assertIn("previous_generated_summary", source_kinds)
        self.assertEqual(source_kinds[-1], "realtime_snapshot")
        self.assertEqual(result["live_snapshot_count"], 1)
        self.assertEqual(result["previous_summary_count"], 1)
        self.assertEqual(result["realtime_snapshot"]["industry_fund_flow"]["inflow"][0]["name"], "半导体")
        self.assertTrue(result["hot_sector_lines"][0].startswith("半导体"))
        self.assertFalse(status["stale"])
        self.assertEqual(status["live_snapshot_count"], 1)

    def test_status_marks_live_snapshot_stale_after_five_minutes_during_session(self):
        now = datetime(2026, 7, 14, 10, 10, 1)
        scans = practice_market_summary.collect_market_replay_sources(self.records, "2026-07-14")
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "summary.json"
            cache_file.write_text(json.dumps({
                "ok": True,
                "available": True,
                "date": "2026-07-14",
                "schema_version": practice_market_summary.SUMMARY_SCHEMA_VERSION,
                "summary": "旧实时总结",
                "source_fingerprint": practice_market_summary.source_fingerprint(scans),
                "live_snapshot_at": "2026-07-14 10:05:00",
            }, ensure_ascii=False), encoding="utf-8")

            status = practice_market_summary.summary_status(self.records, cache_file, now)

        self.assertTrue(status["stale"])
        self.assertIn("实时快照已超过5分钟", status["stale_reasons"])

    def test_required_realtime_snapshot_rejects_stale_or_incomplete_data(self):
        now = datetime(2026, 7, 14, 12, 0, 0)
        incomplete = self.realtime_source(now)
        incomplete["complete"] = False
        incomplete["missing_channels"] = ["行业板块资金流"]

        with tempfile.TemporaryDirectory() as tmp:
            result = practice_market_summary.generate_and_store_summary(
                self.records,
                Path(tmp) / "summary.json",
                now,
                realtime_snapshot_provider=lambda _now: incomplete,
                require_realtime=True,
            )

        self.assertFalse(result["ok"])
        self.assertIn("行业板块资金流", result["error"])


if __name__ == "__main__":
    unittest.main()
