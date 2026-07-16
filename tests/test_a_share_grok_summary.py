#!/usr/bin/env python3
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
COMPAT = SRC / "compat"
ENTRYPOINTS = SRC / "entrypoints"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(COMPAT))
MODULE_PATH = COMPAT / "a_share_grok_summary.py"


def load_module():
    spec = importlib.util.spec_from_file_location("a_share_grok_summary_under_test", MODULE_PATH)
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


class AShareGrokSummaryTests(unittest.TestCase):
    def test_context_length_does_not_set_model_summary_max_tokens_default(self):
        mod = load_module_with_env({"A_SHARE_MODEL_SUMMARY_CONTEXT_LENGTH": "256K"})

        self.assertEqual(mod.A_SHARE_MODEL_SUMMARY_CONTEXT_LENGTH, 256000)
        self.assertEqual(mod.A_SHARE_MODEL_SUMMARY_MAX_TOKENS, 4096)
        self.assertEqual(mod.call_grok_api.__kwdefaults__["max_tokens"], 4096)

    def test_max_tokens_env_sets_model_summary_output_tokens(self):
        mod = load_module_with_env({
            "A_SHARE_MODEL_SUMMARY_CONTEXT_LENGTH": "256K",
            "A_SHARE_MODEL_SUMMARY_MAX_TOKENS": "4096",
        })

        self.assertEqual(mod.A_SHARE_MODEL_SUMMARY_CONTEXT_LENGTH, 256000)
        self.assertEqual(mod.A_SHARE_MODEL_SUMMARY_MAX_TOKENS, 4096)
        self.assertEqual(mod.call_grok_api.__kwdefaults__["max_tokens"], 4096)

    def test_call_grok_api_omits_temperature_by_default(self):
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
            mod._get_grok_credentials = lambda: ("https://ashare.example/v1", "secret")

            def fake_urlopen(req, timeout=0, context=None):
                captured["payload"] = json.loads(req.data.decode("utf-8"))
                captured["headers"] = dict(req.header_items())
                return Resp()

            mod.urlopen = fake_urlopen
            mod.call_grok_api([{"role": "user", "content": "hello"}], max_tokens=123)
        finally:
            mod._get_grok_credentials = original_credentials
            mod.urlopen = original_urlopen

        self.assertEqual(captured["payload"]["max_tokens"], 123)
        self.assertNotIn("temperature", captured["payload"])
        self.assertEqual(captured["headers"]["User-agent"], "NiuOne/1.0")
        self.assertEqual(captured["headers"]["Accept"], "application/json")

    def test_parse_accepts_json_fence(self):
        mod = load_module()

        parsed = mod.parse_a_share_grok_content("""```json
{"tone":"cautious","tone_label":"谨慎","summary":"盘面分化，先控仓。","comparison_lines":["实时资金结构弱化"],"guidance_lines":["风险级别：谨慎","开仓节奏：本轮最多1笔"],"focus_lines":["资金流向"],"risk_lines":["跌停扩散"]}
```""")

        self.assertEqual(parsed["tone"], "cautious")
        self.assertEqual(parsed["tone_label"], "谨慎")
        self.assertEqual(parsed["comparison_lines"], ["实时资金结构弱化"])
        self.assertEqual(parsed["guidance_lines"][1], "开仓节奏：本轮最多1笔")

    def test_apply_grok_report_puts_model_guidance_first(self):
        mod = load_module()
        original_call = mod.call_grok_api
        original_model = mod.A_SHARE_MODEL_SUMMARY_MODEL
        try:
            mod.A_SHARE_MODEL_SUMMARY_MODEL = "model-test"
            mod.call_grok_api = lambda messages, max_tokens=4096: json.dumps({
                "tone": "defensive",
                "tone_label": "防守",
                "summary": "A股午盘涨少跌多，资金分散，午后先防守。",
                "guidance_lines": [
                    "风险级别：防守",
                    "开仓节奏：午后只观察，除非主线回封确认。",
                    "买入指引：只看资金净流入且回踩不破的方向。",
                    "卖出/风控：弱于板块和放量回落的持仓先处理。",
                ],
                "focus_lines": ["观察跌停数量是否扩散"],
                "risk_lines": ["下跌家数占优时不追高"],
            }, ensure_ascii=False)
            local_report = """牛牛大王，A股午盘总结来了：

📊 **市场概况**
上涨 `1000` · 下跌 `3900`

🎯 **今日买卖指引**
· 风险级别：平衡
· 开仓节奏：午后最多3-4只

🔥 **热门板块**
`通信` +1.20%
"""

            report = mod.apply_grok_to_a_share_report(local_report, title="A股午盘总结")
        finally:
            mod.call_grok_api = original_call
            mod.A_SHARE_MODEL_SUMMARY_MODEL = original_model

        self.assertIn("生成模型 `model-test`", report)
        self.assertIn("A股午盘涨少跌多", report)
        self.assertLess(report.index("风险级别：防守"), report.index("本地规则快照"))
        self.assertNotIn("风险级别：平衡", report)
        self.assertIn("`通信` +1.20%", report)

    def test_close_report_uses_next_day_premarket_heading(self):
        mod = load_module()
        original_call = mod.call_grok_api
        original_model = mod.A_SHARE_MODEL_SUMMARY_MODEL
        try:
            mod.A_SHARE_MODEL_SUMMARY_MODEL = "model-test"
            captured = {}

            def fake_call(messages, max_tokens=4096):
                captured["prompt"] = messages[-1]["content"]
                return json.dumps({
                    "tone": "cautious",
                    "tone_label": "谨慎",
                    "summary": "盘后结构分化，次日先看竞价是否承接。",
                    "guidance_lines": [
                        "风险级别：谨慎",
                        "开仓节奏：次日最多1笔，开盘15分钟后确认。",
                        "买入指引：只看半导体资金延续和回踩不破。",
                        "卖出/风控：低开不修复和弱于板块的持仓先处理。",
                    ],
                    "focus_lines": ["观察半导体竞价溢价"],
                    "risk_lines": ["跌停扩散则暂停新开仓"],
                }, ensure_ascii=False)

            mod.call_grok_api = fake_call
            local_report = """牛牛大王，A股盘后总结来了：

📊 **市场概况**
上涨 `1800` · 下跌 `3100`

🎯 **次日买卖计划**
· 风险级别：平衡
· 开仓节奏：次日计划最多3-4只

🧭 **次日盘前指引**
· 盘前基准：风险级别 `平衡`
· 竞价确认：半导体有溢价

📌 **次日关注池**
· 主线方向：`半导体`
"""

            report = mod.apply_grok_to_a_share_report(local_report, title="A股盘后总结")
        finally:
            mod.call_grok_api = original_call
            mod.A_SHARE_MODEL_SUMMARY_MODEL = original_model

        self.assertIn("次日盘前可执行计划", captured["prompt"])
        self.assertIn("🎯 **次日盘前指引**", report)
        self.assertNotIn("🎯 **今日买卖指引**", report)
        self.assertLess(report.index("开仓节奏：次日最多1笔"), report.index("本地规则快照"))
        self.assertNotIn("风险级别：平衡", report)
        self.assertIn("次日关注池", report)


if __name__ == "__main__":
    unittest.main()
