#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

from dashboard.model_connectivity import (  # noqa: E402
    model_test_metadata,
    resolve_model_test_config,
    test_model_connection as run_model_connection_test,
)


class _Response:
    headers = {"Content-Type": "application/json"}

    def __init__(self, content: str = "连接成功") -> None:
        self.body = json.dumps(
            {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
            ensure_ascii=False,
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.body


class ModelConnectivityTests(unittest.TestCase):
    def test_all_model_setting_sections_publish_test_metadata(self):
        metadata = model_test_metadata()

        self.assertEqual(
            [item["id"] for item in metadata],
            [
                "news-precheck",
                "decision-model",
                "grok-model",
                "us-rating-model",
                "a-share-summary-model",
            ],
        )
        self.assertEqual(
            {item["group_slug"] for item in metadata},
            {"news-precheck", "decision-model", "us-market", "market-monitoring"},
        )
        self.assertTrue(all("API_KEY" in " ".join(item["field_names"]) for item in metadata))
        rating = next(item for item in metadata if item["id"] == "us-rating-model")
        self.assertIn("US_RATING_MODEL", rating["field_names"])
        self.assertIn("US_RATING_BASE_URL", rating["field_names"])
        self.assertIn("US_RATING_API_KEY", rating["field_names"])

    def test_successful_decision_test_sends_one_small_authenticated_request(self):
        calls = []

        def opener(request, timeout=0):
            calls.append((request, timeout))
            return _Response()

        ticks = iter((10.0, 10.125))
        result = run_model_connection_test(
            "decision-model",
            {
                "DASHBOARD_DECISION_MODEL": "decision-test-model",
                "DASHBOARD_DECISION_BASE_URL": "https://model.example/v1/",
                "DASHBOARD_DECISION_API_KEY": "private-key",
            },
            timeout=90,
            opener=opener,
            monotonic=lambda: next(ticks),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["api_mode"], "chat")
        self.assertEqual(result["elapsed_ms"], 125)
        self.assertNotIn("private-key", json.dumps(result, ensure_ascii=False))
        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        self.assertEqual(request.full_url, "https://model.example/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer private-key")
        self.assertEqual(timeout, 30.0)
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "decision-test-model")
        self.assertEqual(payload["max_tokens"], 256)

    def test_news_test_uses_operational_responses_mode_and_search_tool(self):
        requests = []

        def opener(request, timeout=0):
            requests.append(request)
            return _Response()

        result = run_model_connection_test(
            "news-precheck",
            {
                "DASHBOARD_NEWS_MODEL": "gpt-5-search",
                "DASHBOARD_NEWS_BASE_URL": "https://search.example/v1",
                "DASHBOARD_NEWS_API_KEY": "search-key",
                "DASHBOARD_NEWS_API_MODE": "auto",
            },
            opener=opener,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["api_mode"], "responses")
        payload = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(payload["tools"], [{"type": "web_search"}])
        self.assertNotIn("messages", payload)

    def test_complete_provider_fallback_is_not_mixed_with_partial_override(self):
        config = resolve_model_test_config(
            "decision-model",
            {
                "DASHBOARD_DECISION_MODEL": "decision-test-model",
                "DASHBOARD_DECISION_BASE_URL": "https://partial.example/v1",
            },
            provider_fallback={
                "base_url": "https://provider.example/v1",
                "api_key": "provider-key",
            },
        )

        self.assertEqual(config.base_url, "https://provider.example/v1")
        self.assertEqual(config.api_key, "provider-key")

    def test_summary_and_rating_targets_reuse_grok_values(self):
        values = {
            "DASHBOARD_GROK_MODEL": "shared-grok",
            "DASHBOARD_GROK_BASE_URL": "https://grok.example/v1",
            "DASHBOARD_GROK_API_KEY": "grok-key",
            "DASHBOARD_GROK_API_MODE": "responses",
        }

        summary = resolve_model_test_config("a-share-summary-model", values)
        rating = resolve_model_test_config("us-rating-model", values)

        self.assertEqual((summary.model, summary.base_url, summary.api_key, summary.api_mode), (
            "shared-grok", "https://grok.example/v1", "grok-key", "chat",
        ))
        self.assertEqual((rating.model, rating.base_url, rating.api_key, rating.api_mode), (
            "shared-grok", "https://grok.example/v1", "grok-key", "responses",
        ))

    def test_rating_target_prefers_complete_dedicated_configuration(self):
        rating = resolve_model_test_config(
            "us-rating-model",
            {
                "US_RATING_MODEL": "gpt-5-search",
                "US_RATING_BASE_URL": "https://rating.example/v1",
                "US_RATING_API_KEY": "rating-key",
                "DASHBOARD_GROK_MODEL": "shared-grok",
                "DASHBOARD_GROK_BASE_URL": "https://grok.example/v1",
                "DASHBOARD_GROK_API_KEY": "grok-key",
                "DASHBOARD_GROK_API_MODE": "auto",
            },
        )

        self.assertEqual(
            (rating.model, rating.base_url, rating.api_key, rating.api_mode),
            ("gpt-5-search", "https://rating.example/v1", "rating-key", "auto"),
        )

    def test_failures_are_actionable_and_do_not_expose_provider_bodies(self):
        missing = run_model_connection_test("news-precheck", {})
        self.assertFalse(missing["ok"])
        self.assertIn("模型", missing["error"])
        self.assertIn("API Key", missing["error"])

        def unauthorized(request, timeout=0):
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "Unauthorized private-key-in-reason",
                {},
                io.BytesIO(b'{"error":"private-key-in-body"}'),
            )

        failed = run_model_connection_test(
            "decision-model",
            {
                "DASHBOARD_DECISION_BASE_URL": "https://model.example/v1",
                "DASHBOARD_DECISION_API_KEY": "private-key",
            },
            opener=unauthorized,
        )

        serialized = json.dumps(failed, ensure_ascii=False)
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error_code"], "http_401")
        self.assertNotIn("private-key", serialized)


if __name__ == "__main__":
    unittest.main()
