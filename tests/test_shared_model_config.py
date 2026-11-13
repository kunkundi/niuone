#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

from core.shared_model_config import (  # noqa: E402
    load_crossdesk_provider,
    model_provider_display_name,
    resolve_shared_model_config,
    yaml,
)


class SharedModelConfigTests(unittest.TestCase):
    def test_official_provider_is_not_mislabeled_as_legacy_gateway(self):
        self.assertEqual(model_provider_display_name("https://api.openai.com/v1/"), "OpenAI")
        for address in ("", "https://gateway.example/v1", "https://api.openai.com.example/v1", "https://api.openai.com@other.example/private?key=hidden"):
            self.assertEqual(model_provider_display_name(address), "Crossdesk.ccwu.cc")

    def test_shared_settings_drive_every_model_consumer(self):
        config = resolve_shared_model_config({
            "DASHBOARD_DECISION_MODEL": "shared-model",
            "DASHBOARD_DECISION_BASE_URL": "https://shared.example/v1/",
            "DASHBOARD_DECISION_API_KEY": "shared-key",
            "DASHBOARD_DECISION_STREAM_MODE": "stream",
            "DASHBOARD_DECISION_REASONING_EFFORT": "high",
            "DASHBOARD_DECISION_CONTEXT_LENGTH": "256000",
            "DASHBOARD_DECISION_MAX_TOKENS": "8192",
            "A_SHARE_MODEL_SUMMARY_MODEL": "old-summary-model",
            "A_SHARE_MODEL_SUMMARY_BASE_URL": "https://old.example/v1",
            "A_SHARE_MODEL_SUMMARY_API_KEY": "old-key",
        })

        self.assertEqual(config.source, "shared")
        self.assertEqual(config.model, "shared-model")
        self.assertEqual(config.base_url, "https://shared.example/v1")
        self.assertEqual(config.api_key, "shared-key")
        self.assertEqual(config.stream_mode, "stream")
        self.assertEqual(config.reasoning_effort, "high")
        self.assertEqual(config.context_length, "256000")
        self.assertEqual(config.max_tokens, "8192")

    def test_complete_legacy_summary_settings_are_an_upgrade_fallback(self):
        config = resolve_shared_model_config({
            "A_SHARE_MODEL_SUMMARY_MODEL": "old-summary-model",
            "A_SHARE_MODEL_SUMMARY_BASE_URL": "https://old.example/v1/",
            "A_SHARE_MODEL_SUMMARY_API_KEY": "old-key",
            "A_SHARE_MODEL_SUMMARY_REASONING_EFFORT": "medium",
        })

        self.assertEqual(config.source, "legacy_summary")
        self.assertEqual(config.model, "old-summary-model")
        self.assertEqual(config.base_url, "https://old.example/v1")
        self.assertEqual(config.api_key, "old-key")
        self.assertEqual(config.reasoning_effort, "medium")

    @unittest.skipIf(yaml is None, "PyYAML unavailable")
    def test_yaml_provider_is_available_to_all_shared_consumers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(
                "custom_providers:\n"
                "  - name: Crossdesk.ccwu.cc\n"
                "    model: provider-model\n"
                "    base_url: https://provider.example/v1\n"
                "    api_key: provider-key\n",
                encoding="utf-8",
            )
            provider = load_crossdesk_provider(path)
            config = resolve_shared_model_config({}, provider_fallback=provider)

        self.assertEqual(config.source, "provider")
        self.assertEqual(config.model, "deepseek-v4-pro")
        self.assertEqual(config.base_url, "https://provider.example/v1")
        self.assertEqual(config.api_key, "provider-key")


if __name__ == "__main__":
    unittest.main()
