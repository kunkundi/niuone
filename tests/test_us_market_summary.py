#!/usr/bin/env python3
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
sys.path.insert(0, str(SRC))
MODULE_PATH = SRC / "us_market_summary.py"


def load_module():
    spec = importlib.util.spec_from_file_location("us_market_summary_under_test", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_module_with_env(updates: dict[str, str]):
    original = {key: os.environ.get(key) for key in updates}
    try:
        os.environ.update(updates)
        return load_module()
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class UsMarketSummaryTests(unittest.TestCase):
    def test_context_length_does_not_set_grok_max_tokens_default(self):
        mod = load_module_with_env({"US_MARKET_SUMMARY_CONTEXT_LENGTH": "128K"})

        self.assertEqual(mod.US_MARKET_SUMMARY_CONTEXT_LENGTH, 128000)
        self.assertEqual(mod.US_MARKET_SUMMARY_MAX_TOKENS, 4096)
        self.assertEqual(mod._call_grok_api.__kwdefaults__["max_tokens"], 4096)

    def test_max_tokens_env_sets_grok_output_tokens(self):
        mod = load_module_with_env({
            "US_MARKET_SUMMARY_CONTEXT_LENGTH": "128K",
            "US_MARKET_SUMMARY_MAX_TOKENS": "4096",
        })

        self.assertEqual(mod.US_MARKET_SUMMARY_CONTEXT_LENGTH, 128000)
        self.assertEqual(mod.US_MARKET_SUMMARY_MAX_TOKENS, 4096)
        self.assertEqual(mod._call_grok_api.__kwdefaults__["max_tokens"], 4096)

    def test_grok_api_omits_temperature_by_default(self):
        mod = load_module()
        captured = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"ok"}}]}'

        original_credentials = mod._get_grok_credentials
        original_urlopen = mod.urlopen
        try:
            mod._get_grok_credentials = lambda: ("https://summary.example/v1", "secret")

            def fake_urlopen(req, timeout=0, context=None):
                captured["payload"] = json.loads(req.data.decode("utf-8"))
                captured["headers"] = dict(req.header_items())
                return Resp()

            mod.urlopen = fake_urlopen
            mod._call_grok_api([{"role": "user", "content": "hello"}], max_tokens=123)
        finally:
            mod._get_grok_credentials = original_credentials
            mod.urlopen = original_urlopen

        self.assertEqual(captured["payload"]["max_tokens"], 123)
        self.assertNotIn("temperature", captured["payload"])
        self.assertEqual(captured["headers"]["User-agent"], "NiuOne/1.0")
        self.assertEqual(captured["headers"]["Accept"], "application/json")

    def test_previous_us_session_date_uses_friday_on_monday(self):
        mod = load_module()

        self.assertEqual(
            mod.previous_us_session_date(datetime(2026, 6, 29, 8, 0, tzinfo=mod.CN_TZ)).isoformat(),
            "2026-06-26",
        )

    def test_previous_us_session_date_uses_prior_weekday(self):
        mod = load_module()

        self.assertEqual(
            mod.previous_us_session_date(datetime(2026, 6, 30, 8, 0, tzinfo=mod.CN_TZ)).isoformat(),
            "2026-06-29",
        )
        self.assertEqual(
            mod.previous_us_session_date(datetime(2026, 6, 28, 8, 0, tzinfo=mod.CN_TZ)).isoformat(),
            "2026-06-26",
        )

    def test_build_summary_generates_defensive_guidance(self):
        mod = load_module()
        payload = {
            "generated_at": "2026-06-30 08:00:00",
            "items": [
                {"key": "dow", "name": "道琼斯指数", "price": 39000, "change_pct": -0.75, "time": "2026-06-29 16:00:00"},
                {"key": "nas", "name": "纳斯达克指数", "price": 18000, "change_pct": -1.62, "time": "2026-06-29 16:00:00"},
                {"key": "spx", "name": "标普500指数", "price": 5200, "change_pct": -1.12, "time": "2026-06-29 16:00:00"},
                {"key": "a50_fut", "name": "富时中国A50期货", "price": 13000, "change_pct": -0.45},
                {"key": "xau", "name": "伦敦金", "price": 2350, "change_pct": 0.95},
            ],
        }

        summary = mod.build_us_market_summary_from_indices(
            payload,
            now=datetime(2026, 6, 30, 8, 0, tzinfo=mod.CN_TZ),
        )

        self.assertTrue(summary["available"])
        self.assertEqual(summary["target_us_date"], "2026-06-29")
        self.assertEqual(summary["tone"], "defensive")
        self.assertIn("默认防守", "\n".join(summary["guidance_lines"]))
        self.assertIn("纳斯达克指数 -1.62%", summary["summary"])

    def test_build_summary_generates_offensive_guidance(self):
        mod = load_module()
        payload = {
            "items": [
                {"key": "dow", "name": "道琼斯指数", "price": 39000, "change_pct": 0.42},
                {"key": "nas", "name": "纳斯达克指数", "price": 18000, "change_pct": 1.05},
                {"key": "spx", "name": "标普500指数", "price": 5200, "change_pct": 0.71},
                {"key": "a50_fut", "name": "富时中国A50期货", "price": 13000, "change_pct": 0.38},
            ],
        }

        summary = mod.build_us_market_summary_from_indices(
            payload,
            now=datetime(2026, 7, 1, 8, 0, tzinfo=mod.CN_TZ),
            sector_payload={
                "generated_at": "2026-07-01 08:00:00",
                "items": [
                    {"symbol": "SMH", "label": "半导体", "change_pct": 1.42, "a_share_mapping": ["半导体", "芯片设备", "先进封装"]},
                    {"symbol": "XLE", "label": "能源", "change_pct": -0.88, "a_share_mapping": ["油气", "煤炭", "油服"]},
                ],
            },
        )

        self.assertEqual(summary["tone"], "offensive")
        self.assertIn("试仓", "\n".join(summary["guidance_lines"]))
        self.assertIn("A50期货 +0.38%", "\n".join(summary["guidance_lines"]))
        self.assertIn("板块映射", "\n".join(summary["guidance_lines"]))
        self.assertEqual(summary["sector_mappings"][0]["proxy"], "SMH")
        self.assertIn("半导体", summary["sector_mappings"][0]["a_share_mapping"])

    def test_report_text_contains_market_monitor_guidance_block(self):
        mod = load_module()
        summary = {
            "target_us_date": "2026-06-29",
            "generated_at": "2026-06-30 08:00:00",
            "tone_label": "谨慎",
            "model_generated": True,
            "model": "grok-test",
            "summary": "隔夜美股偏弱或分化，今日不急着追高。",
            "metrics": [
                {"label": "纳斯达克指数", "value": "18,000.00", "change_pct_text": "-0.80%"},
            ],
            "sector_mappings": [
                {
                    "us_sector": "半导体",
                    "proxy": "SMH",
                    "change_pct": 1.2,
                    "change_pct_text": "+1.20%",
                    "a_share_mapping": ["半导体", "芯片设备"],
                    "strategy": "正映射，竞价确认后加分。",
                },
            ],
            "guidance_lines": ["买入节奏：降低预算，先观察开盘 15 分钟。"],
        }

        text = mod.build_us_market_report_text(summary)

        self.assertIn("隔夜美股盘面总结来了", text)
        self.assertIn("🎯 **今日买卖指引**", text)
        self.assertIn("风险级别：谨慎", text)
        self.assertIn("生成模型 `grok-test`", text)
        self.assertIn("🧭 **美股板块映射**", text)
        self.assertIn("半导体(SMH)", text)
        self.assertIn("买入节奏：降低预算", text)

    def test_apply_grok_summary_overrides_rule_guidance(self):
        mod = load_module()
        original_call = mod._call_grok_api
        try:
            mod._call_grok_api = lambda messages, max_tokens=4096: json.dumps({
                "tone": "cautious",
                "tone_label": "谨慎",
                "summary": "2026-06-29 美股收盘：三大指数分化，今日 A 股降低追高。",
                "guidance_lines": [
                    "买入节奏：先观察开盘 15 分钟，单轮新仓不超过 1 笔。",
                    "选股方向：只看有资金承接的科技映射和强趋势票。",
                    "卖出/风控：弱于板块、破位或冲高回落的持仓优先处理。",
                    "全天策略：午后再根据主线扩散决定是否补仓。",
                ],
            }, ensure_ascii=False)
            base = {
                "target_cn_date": "2026-06-30",
                "target_us_date": "2026-06-29",
                "tone": "offensive",
                "tone_label": "进攻",
                "summary": "规则摘要",
                "guidance_lines": ["规则指引"],
                "metrics": [],
                "sector_mappings": [{"us_sector": "半导体", "proxy": "SMH", "change_pct_text": "+1.00%"}],
            }

            summary = mod.apply_grok_summary(base)
        finally:
            mod._call_grok_api = original_call

        self.assertTrue(summary["model_generated"])
        self.assertEqual(summary["tone"], "cautious")
        self.assertEqual(summary["tone_label"], "谨慎")
        self.assertIn("降低追高", summary["summary"])
        self.assertIn("开盘 15 分钟", summary["guidance_lines"][0])
        self.assertEqual(summary["sector_mappings"][0]["proxy"], "SMH")

    def test_parse_grok_summary_accepts_json_fence(self):
        mod = load_module()

        parsed = mod.parse_grok_summary_content("""```json
{"tone":"defensive","tone_label":"防守","summary":"防守处理","guidance_lines":["买入节奏：暂停扩仓","卖出/风控：处理弱票"]}
```""")

        self.assertEqual(parsed["tone"], "defensive")
        self.assertEqual(parsed["tone_label"], "防守")
        self.assertEqual(parsed["guidance_lines"][0], "买入节奏：暂停扩仓")

    def test_cached_summary_for_today_is_loaded(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as td:
            mod.SUMMARY_CACHE_FILE = Path(td) / "us_market_summary_latest.json"
            payload = {
                "target_cn_date": "2026-06-30",
                "target_us_date": "2026-06-29",
                "generated_at": "2026-06-30 08:00:00",
                "tone": "balanced",
            }
            mod.SUMMARY_CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            loaded = mod.load_cached_summary_for_today(datetime(2026, 6, 30, 10, 0, tzinfo=mod.CN_TZ))

        self.assertIsNotNone(loaded)
        self.assertTrue(loaded["cached_archive"])
        self.assertEqual(loaded["generated_at"], "2026-06-30 08:00:00")

    def test_fetch_fast_rules_path_uses_provided_indices_without_grok(self):
        mod = load_module()
        original_call = mod._call_grok_api
        try:
            mod._call_grok_api = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not call Grok"))
            payload = {
                "generated_at": "2026-07-01 08:00:00",
                "items": [
                    {"key": "dow", "name": "道琼斯指数", "price": 39000, "change_pct": 0.1},
                    {"key": "nas", "name": "纳斯达克指数", "price": 18000, "change_pct": 0.2},
                    {"key": "spx", "name": "标普500指数", "price": 5200, "change_pct": 0.3},
                ],
            }

            summary = mod.fetch_us_market_summary(
                datetime(2026, 7, 2, 8, 0, tzinfo=mod.CN_TZ),
                prefer_archive=False,
                use_model=False,
                indices_payload=payload,
            )
        finally:
            mod._call_grok_api = original_call

        self.assertTrue(summary["available"])
        self.assertEqual(summary["source_generated_at"], "2026-07-01 08:00:00")
        self.assertNotIn("model_error", summary)


if __name__ == "__main__":
    unittest.main()
