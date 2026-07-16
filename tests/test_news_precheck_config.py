#!/usr/bin/env python3
import importlib.util
import json
import os
import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
COMPAT = SRC / "compat"
ENTRYPOINTS = SRC / "entrypoints"
NEWS_ENV_KEYS = {
    "DASHBOARD_ENV_FILE",
    "DASHBOARD_NEWS_MODEL",
    "DASHBOARD_NEWS_API_MODE",
    "DASHBOARD_NEWS_CONTEXT_LENGTH",
    "DASHBOARD_NEWS_MAX_TOKENS",
    "DASHBOARD_NEWS_BASE_URL",
    "DASHBOARD_NEWS_API_KEY",
    "DASHBOARD_NEWS_TIMEOUT",
    "DASHBOARD_NEWS_MAX_RETRIES",
    "DASHBOARD_NEWS_CONCURRENCY",
    "DASHBOARD_GROK_MODEL",
    "DASHBOARD_GROK_BASE_URL",
    "DASHBOARD_GROK_API_KEY",
}


def import_trader_with_env(updates: dict[str, str]):
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
        sys.path.insert(0, str(COMPAT))
    for key in NEWS_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ["DASHBOARD_ENV_FILE"] = str(ROOT / ".missing-dashboard.env")
    os.environ.update(updates)
    spec = importlib.util.spec_from_file_location(
        f"niuniu_practice_trader_under_test_{len(sys.modules)}",
        COMPAT / "niuniu_practice_trader.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class NewsPrecheckConfigTests(unittest.TestCase):
    def setUp(self):
        self.original_env = {key: os.environ.get(key) for key in NEWS_ENV_KEYS}

    def tearDown(self):
        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_news_precheck_skips_when_unconfigured(self):
        module = import_trader_with_env({})

        self.assertIsNone(module.load_news_precheck_config())
        self.assertEqual(module.check_candidate_news_precheck([{"code": "000001", "name": "平安银行"}]), "")

    def test_news_precheck_requires_complete_config(self):
        module = import_trader_with_env({
            "DASHBOARD_NEWS_BASE_URL": "https://news.example/v1",
            "DASHBOARD_NEWS_MODEL": "search-model",
        })

        with self.assertRaisesRegex(RuntimeError, "DASHBOARD_NEWS_API_KEY"):
            module.load_news_precheck_config()

    def test_news_precheck_uses_independent_config(self):
        module = import_trader_with_env({
            "DASHBOARD_GROK_BASE_URL": "https://grok.example/v1",
            "DASHBOARD_GROK_API_KEY": "grok-secret",
            "DASHBOARD_GROK_MODEL": "grok-model",
            "DASHBOARD_NEWS_BASE_URL": "https://news.example/v1",
            "DASHBOARD_NEWS_API_KEY": "news-secret",
            "DASHBOARD_NEWS_MODEL": "search-model",
        })
        captured = {}

        def fake_request(base_url, api_key, payload, model_name, max_retries=3, timeout=60, **kwargs):
            captured.update({
                "base_url": base_url,
                "api_key": api_key,
                "payload": payload,
                "model_name": model_name,
                "max_retries": max_retries,
                "timeout": timeout,
                "kwargs": kwargs,
            })
            return "- 000001 平安银行：无重大消息（中性）"

        module.request_chat_content = fake_request
        result = module.check_candidate_news_precheck([{"code": "000001", "name": "平安银行"}])

        self.assertEqual(captured["base_url"], "https://news.example/v1")
        self.assertEqual(captured["api_key"], "news-secret")
        self.assertEqual(captured["payload"]["model"], "search-model")
        self.assertNotIn("temperature", captured["payload"])
        self.assertEqual(captured["model_name"], "search-model")
        self.assertEqual(captured["max_retries"], 1)
        self.assertEqual(captured["timeout"], 45)
        self.assertEqual(captured["kwargs"]["api_mode"], "auto")
        self.assertEqual(captured["kwargs"]["tools"], [{"type": "web_search"}])
        self.assertEqual(captured["kwargs"]["reasoning"], {"effort": "low"})
        self.assertIn("【消息面预检（实时搜索", result)
        self.assertNotIn("Grok", result)

    def test_request_chat_content_sends_compatible_user_agent(self):
        module = import_trader_with_env({})
        captured = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"ok"}}]}'

        original_urlopen = module.urllib.request.urlopen
        try:
            def fake_urlopen(req, timeout=0):
                captured["headers"] = dict(req.header_items())
                captured["payload"] = json.loads(req.data.decode("utf-8"))
                return Resp()

            module.urllib.request.urlopen = fake_urlopen
            result = module.request_chat_content(
                "https://news.example/v1",
                "secret",
                {"messages": [{"role": "user", "content": "hello"}]},
                "search-model",
                max_retries=1,
                timeout=3,
            )
        finally:
            module.urllib.request.urlopen = original_urlopen

        self.assertEqual(result, "ok")
        self.assertEqual(captured["payload"]["model"], "search-model")
        self.assertEqual(captured["headers"]["User-agent"], "NiuOne/1.0")
        self.assertEqual(captured["headers"]["Accept"], "application/json")

    def test_gpt_news_precheck_uses_responses_search_and_parses_forced_sse(self):
        module = import_trader_with_env({})
        captured = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return (
                    'event: response.web_search_call.searching\n'
                    'data: {"type":"response.web_search_call.searching"}\n\n'
                    'event: response.output_text.delta\n'
                    'data: {"type":"response.output_text.delta","delta":"搜索结果正常"}\n\n'
                    'event: response.completed\n'
                    'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
                ).encode("utf-8")

        original_urlopen = module.urllib.request.urlopen
        try:
            def fake_urlopen(req, timeout=0):
                captured["url"] = req.full_url
                captured["payload"] = json.loads(req.data.decode("utf-8"))
                return Resp()

            module.urllib.request.urlopen = fake_urlopen
            result = module.request_chat_content(
                "https://news.example/v1",
                "secret",
                {
                    "model": "gpt-5.6-sol",
                    "messages": [{"role": "user", "content": "search"}],
                    "max_tokens": 4096,
                },
                "gpt-5.6-sol",
                max_retries=1,
                timeout=3,
                api_mode="auto",
                tools=[{"type": "web_search"}],
                reasoning={"effort": "low"},
            )
        finally:
            module.urllib.request.urlopen = original_urlopen

        self.assertEqual(result, "搜索结果正常")
        self.assertEqual(captured["url"], "https://news.example/v1/responses")
        self.assertEqual(captured["payload"]["tools"], [{"type": "web_search"}])
        self.assertNotIn("max_output_tokens", captured["payload"])
        self.assertNotIn("max_tokens", captured["payload"])

    def test_api_call_with_retry_sends_compatible_user_agent(self):
        module = import_trader_with_env({})
        captured = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"ok":true}'

        original_urlopen = module.urllib.request.urlopen
        try:
            def fake_urlopen(req, timeout=0):
                captured["headers"] = dict(req.header_items())
                captured["payload"] = json.loads(req.data.decode("utf-8"))
                return Resp()

            module.urllib.request.urlopen = fake_urlopen
            result = module.api_call_with_retry(
                "https://decision.example/v1",
                "secret",
                {"model": "decision-model", "messages": [{"role": "user", "content": "hello"}]},
                max_retries=1,
                timeout=3,
            )
        finally:
            module.urllib.request.urlopen = original_urlopen

        self.assertEqual(result, {"ok": True})
        self.assertEqual(captured["payload"]["model"], "decision-model")
        self.assertEqual(captured["headers"]["User-agent"], "NiuOne/1.0")
        self.assertEqual(captured["headers"]["Accept"], "application/json")

    def test_news_precheck_honors_timeout_overrides(self):
        module = import_trader_with_env({
            "DASHBOARD_NEWS_BASE_URL": "https://news.example/v1",
            "DASHBOARD_NEWS_API_KEY": "news-secret",
            "DASHBOARD_NEWS_MODEL": "search-model",
            "DASHBOARD_NEWS_TIMEOUT": "30",
            "DASHBOARD_NEWS_MAX_RETRIES": "2",
        })
        captured = {}

        def fake_request(base_url, api_key, payload, model_name, max_retries=3, timeout=60, **kwargs):
            captured.update({"max_retries": max_retries, "timeout": timeout})
            return "- 000001 平安银行：无重大消息（中性）"

        module.request_chat_content = fake_request
        module.check_candidate_news_precheck([{"code": "000001", "name": "平安银行"}])

        self.assertEqual(captured["max_retries"], 2)
        self.assertEqual(captured["timeout"], 30)

    def test_news_precheck_checks_candidates_concurrently_and_preserves_order(self):
        module = import_trader_with_env({
            "DASHBOARD_NEWS_BASE_URL": "https://news.example/v1",
            "DASHBOARD_NEWS_API_KEY": "news-secret",
            "DASHBOARD_NEWS_MODEL": "search-model",
            "DASHBOARD_NEWS_CONCURRENCY": "3",
        })
        prompts = []

        def fake_request(base_url, api_key, payload, model_name, max_retries=3, timeout=60, **kwargs):
            prompt = payload["messages"][0]["content"]
            prompts.append(prompt)
            if "000001" in prompt:
                time.sleep(0.05)
                return "- 000001 平安银行：无重大消息（中性）"
            if "000002" in prompt:
                return "- 000002 万科A：融资消息偏弱（利空）"
            return "- 000003 国农科技：订单消息偏强（利好）"

        module.request_chat_content = fake_request
        result = module.check_candidate_news_precheck([
            {"code": "000001", "name": "平安银行"},
            {"code": "000002", "name": "万科A"},
            {"code": "000003", "name": "国农科技"},
        ])

        self.assertIn("并发3", result)
        self.assertEqual(len(prompts), 3)
        self.assertTrue(all(not ("000001" in prompt and "000002" in prompt) for prompt in prompts))
        self.assertLess(result.index("000001"), result.index("000002"))
        self.assertLess(result.index("000002"), result.index("000003"))

    def test_news_precheck_keeps_partial_results_when_one_parallel_request_fails(self):
        module = import_trader_with_env({
            "DASHBOARD_NEWS_BASE_URL": "https://news.example/v1",
            "DASHBOARD_NEWS_API_KEY": "news-secret",
            "DASHBOARD_NEWS_MODEL": "search-model",
            "DASHBOARD_NEWS_CONCURRENCY": "2",
        })

        def fake_request(base_url, api_key, payload, model_name, max_retries=3, timeout=60, **kwargs):
            prompt = payload["messages"][0]["content"]
            if "000002" in prompt:
                raise RuntimeError("rate limited")
            return "- 000001 平安银行：无重大消息（中性）"

        module.request_chat_content = fake_request
        result = module.check_candidate_news_precheck([
            {"code": "000001", "name": "平安银行"},
            {"code": "000002", "name": "万科A"},
        ])

        self.assertIn("000001 平安银行", result)
        self.assertIn("000002 万科A：消息面预检失败", result)

    def test_news_precheck_context_length_does_not_set_max_tokens(self):
        module = import_trader_with_env({
            "DASHBOARD_NEWS_BASE_URL": "https://news.example/v1",
            "DASHBOARD_NEWS_API_KEY": "news-secret",
            "DASHBOARD_NEWS_MODEL": "search-model",
            "DASHBOARD_NEWS_CONTEXT_LENGTH": "128000",
        })
        captured = {}

        def fake_request(base_url, api_key, payload, model_name, max_retries=3, timeout=60, **kwargs):
            captured["payload"] = payload
            return "- 000001 平安银行：无重大消息（中性）"

        module.request_chat_content = fake_request
        module.check_candidate_news_precheck([{"code": "000001", "name": "平安银行"}])

        self.assertEqual(module.NEWS_PRECHECK_CONTEXT_LENGTH, 128000)
        self.assertEqual(captured["payload"]["max_tokens"], 4096)

    def test_news_precheck_max_tokens_sets_output_tokens(self):
        module = import_trader_with_env({
            "DASHBOARD_NEWS_BASE_URL": "https://news.example/v1",
            "DASHBOARD_NEWS_API_KEY": "news-secret",
            "DASHBOARD_NEWS_MODEL": "search-model",
            "DASHBOARD_NEWS_CONTEXT_LENGTH": "128000",
            "DASHBOARD_NEWS_MAX_TOKENS": "1200",
        })
        captured = {}

        def fake_request(base_url, api_key, payload, model_name, max_retries=3, timeout=60, **kwargs):
            captured["payload"] = payload
            return "- 000001 平安银行：无重大消息（中性）"

        module.request_chat_content = fake_request
        module.check_candidate_news_precheck([{"code": "000001", "name": "平安银行"}])

        self.assertEqual(module.NEWS_PRECHECK_CONTEXT_LENGTH, 128000)
        self.assertEqual(captured["payload"]["max_tokens"], 1200)

    def test_news_precheck_context_length_accepts_suffixes(self):
        module = import_trader_with_env({
            "DASHBOARD_NEWS_CONTEXT_LENGTH": "1M",
        })

        self.assertEqual(module.NEWS_PRECHECK_CONTEXT_LENGTH, 1000000)
        self.assertEqual(module.NEWS_PRECHECK_MAX_TOKENS, 4096)

    def test_parse_chat_completion_content_accepts_sse_stream(self):
        module = import_trader_with_env({})
        raw = (
            'data: {"choices":[{"delta":{"content":"- 000001 平安银行："}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"消息稳定（中性）"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )

        content, detail = module.parse_chat_completion_content(raw)

        self.assertEqual(content, "- 000001 平安银行：消息稳定（中性）")
        self.assertIn("sse_chunks=2", detail)
        self.assertIn("finish_reason=stop", detail)


if __name__ == "__main__":
    unittest.main()
