#!/usr/bin/env python3
from __future__ import annotations

import unittest

from app.core.model_reasoning import (
    REASONING_EFFORT_CAPABILITIES_VERIFIED_ON,
    UnsupportedReasoningEffortError,
    chat_reasoning_request_settings,
    effective_reasoning_effort,
    reasoning_effort_capability,
    reasoning_effort_capability_catalog,
    resolve_model_reasoning_effort,
)


class ModelReasoningCapabilityTests(unittest.TestCase):
    def test_qwen_38_supports_responses_levels_and_chat_native_mapping(self):
        capability = reasoning_effort_capability("QWEN3.8-MAX")
        self.assertEqual(capability.key, "qwen-3.8-max")
        self.assertEqual(capability.default_effort, "xhigh")
        self.assertEqual(
            chat_reasoning_request_settings("qwen3.8-max", "none"),
            {"enable_thinking": False},
        )
        self.assertEqual(
            chat_reasoning_request_settings("qwen3.8-max", "minimal"),
            {"reasoning_effort": "minimal"},
        )
        self.assertEqual(
            effective_reasoning_effort("qwen3.8-max", "minimal", api_mode="responses"),
            "minimal",
        )
        self.assertEqual(
            effective_reasoning_effort("qwen3.8-max", "minimal", api_mode="chat"),
            "low",
        )
        with self.assertRaisesRegex(UnsupportedReasoningEffortError, "允许值"):
            resolve_model_reasoning_effort("qwen3.8-max", "ultra")

    def test_qwen_common_models_distinguish_responses_chat_and_thinking_only(self):
        for model in (
            "qwen3.7-max",
            "qwen3.7-flash-2026-07-15",
            "qwen3.6-plus",
            "qwen3.5-397b-a17b",
            "qwen3-max",
            "qwen3-coder-plus",
            "qwen-plus",
        ):
            with self.subTest(model=model):
                self.assertEqual(reasoning_effort_capability(model).key, "qwen-responses")
                self.assertEqual(
                    chat_reasoning_request_settings(model, "high"),
                    {"enable_thinking": True},
                )
                self.assertEqual(
                    effective_reasoning_effort(model, "high", api_mode="chat"),
                    "enabled",
                )
        self.assertEqual(reasoning_effort_capability("qwen-plus-latest").key, "qwen-chat-default-off")
        self.assertEqual(reasoning_effort_capability("qwen3-32b").key, "qwen-chat-default-on")
        self.assertEqual(
            chat_reasoning_request_settings("qwen3-32b", "disabled"),
            {"enable_thinking": False},
        )
        thinking_only = reasoning_effort_capability("qwen3.7-max-preview")
        self.assertEqual(thinking_only.key, "qwen-thinking-only")
        with self.assertRaisesRegex(UnsupportedReasoningEffortError, "仅可留空"):
            resolve_model_reasoning_effort("qwen3.7-max-preview", "enabled")

    def test_minimax_m3_adaptive_and_m2_always_on_are_not_fake_levels(self):
        m3 = reasoning_effort_capability("MiniMax-M3")
        self.assertEqual(m3.effective_efforts, ("none", "adaptive"))
        self.assertEqual(
            chat_reasoning_request_settings("MiniMax-M3", "none"),
            {"thinking": {"type": "disabled"}},
        )
        self.assertEqual(
            chat_reasoning_request_settings("MiniMax-M3", "high"),
            {"thinking": {"type": "adaptive"}},
        )
        for model in ("MiniMax-M2", "MiniMax-M2.5", "MiniMax-M2.7-highspeed"):
            with self.subTest(model=model):
                self.assertEqual(reasoning_effort_capability(model).key, "minimax-m2")
                self.assertEqual(
                    resolve_model_reasoning_effort(model, "none").effective_effort,
                    "always-on",
                )
                self.assertEqual(chat_reasoning_request_settings(model, "none"), {})

    def test_glm_52_supports_reasoning_levels_and_enables_thinking(self):
        capability = reasoning_effort_capability("glm-5.2")

        self.assertEqual(capability.default_effort, "max")
        self.assertEqual(
            resolve_model_reasoning_effort("glm-5.2", "medium").effective_effort,
            "high",
        )
        self.assertEqual(
            chat_reasoning_request_settings("glm-5.2", "max"),
            {"thinking": {"type": "enabled"}, "reasoning_effort": "max"},
        )

    def test_older_glm_models_use_thinking_switch_not_fake_effort_levels(self):
        for model in ("glm-4.5", "glm-4.5-air", "glm-4.6", "glm-4.7", "glm-5", "glm-5.1"):
            with self.subTest(model=model):
                self.assertEqual(reasoning_effort_capability(model).key, "glm-thinking-toggle")
                self.assertEqual(
                    chat_reasoning_request_settings(model, "disabled"),
                    {"thinking": {"type": "disabled"}},
                )
        with self.assertRaisesRegex(UnsupportedReasoningEffortError, "enabled"):
            resolve_model_reasoning_effort("glm-4.7", "high")

    def test_mimo_25_maps_responses_levels_and_chat_thinking_switch(self):
        for model in ("mimo-v2.5", "mimo-v2.5-pro"):
            with self.subTest(model=model):
                self.assertEqual(reasoning_effort_capability(model).key, "mimo-v2.5")
                self.assertEqual(
                    chat_reasoning_request_settings(model, "none"),
                    {"thinking": {"type": "disabled"}},
                )
                self.assertEqual(
                    chat_reasoning_request_settings(model, "medium"),
                    {"thinking": {"type": "enabled"}},
                )
        with self.assertRaisesRegex(UnsupportedReasoningEffortError, "允许值"):
            resolve_model_reasoning_effort("mimo-v2.5-pro", "xhigh")

    def test_deepseek_v4_pro_documents_inputs_and_effective_mappings(self):
        capability = reasoning_effort_capability("deepseek-v4-pro")

        self.assertIsNotNone(capability)
        self.assertEqual(capability.effective_efforts, ("high", "max"))
        self.assertEqual(
            resolve_model_reasoning_effort("deepseek-v4-pro", " LOW ").effective_effort,
            "high",
        )
        self.assertEqual(
            resolve_model_reasoning_effort("deepseek-v4-pro", "xhigh").effective_effort,
            "max",
        )

    def test_known_model_rejects_typo_with_actionable_allowed_values(self):
        with self.assertRaises(UnsupportedReasoningEffortError) as raised:
            resolve_model_reasoning_effort("deepseek-v4-pro", "highh")

        self.assertIn("不支持思考强度“highh”", str(raised.exception))
        self.assertIn("high", str(raised.exception))
        self.assertIn("max", str(raised.exception))

    def test_current_openai_and_xai_aliases_match_only_documented_families(self):
        self.assertEqual(reasoning_effort_capability("GPT-5.6-SOL").key, "gpt-5.6")
        self.assertEqual(
            reasoning_effort_capability("gpt-5.4-pro-2026-08-01").key,
            "gpt-5.4-pro",
        )
        self.assertEqual(reasoning_effort_capability("grok-4.3").key, "grok-4.3")
        self.assertEqual(reasoning_effort_capability("grok-4.3-latest").key, "grok-4.3")
        self.assertEqual(reasoning_effort_capability("grok-latest").key, "grok-4.3")
        self.assertEqual(reasoning_effort_capability("grok-4.5-latest").key, "grok-4.5")
        self.assertIsNone(reasoning_effort_capability("grok-4.20-multi-agent-xhigh"))
        self.assertIsNone(reasoning_effort_capability("gpt-5.6-gateway-custom"))

    def test_grok_43_supports_none_through_high_and_rejects_xhigh(self):
        for effort in ("none", "low", "medium", "high"):
            with self.subTest(effort=effort):
                resolution = resolve_model_reasoning_effort("grok-4.3", effort)
                self.assertEqual(resolution.effective_effort, effort)
        with self.assertRaisesRegex(UnsupportedReasoningEffortError, "允许值"):
            resolve_model_reasoning_effort("grok-4.3", "xhigh")

    def test_unknown_gateway_model_remains_free_form(self):
        resolution = resolve_model_reasoning_effort(
            "gateway-custom-model",
            " Provider.Custom-HIGH ",
        )

        self.assertEqual(resolution.configured_effort, "provider.custom-high")
        self.assertEqual(resolution.effective_effort, "provider.custom-high")
        self.assertIsNone(resolution.capability)

    def test_catalog_is_serializable_reference_data(self):
        catalog = reasoning_effort_capability_catalog()

        self.assertGreaterEqual(len(catalog), 10)
        self.assertTrue(all(row["source_url"].startswith("https://") for row in catalog))
        self.assertTrue(
            all(row["verified_on"] == (
                "2026-11-05" if row["key"] == "gpt-6.1-sol"
                else REASONING_EFFORT_CAPABILITIES_VERIFIED_ON
            ) for row in catalog)
        )
        self.assertTrue(all(row["model_pattern"].startswith("^") for row in catalog))


if __name__ == "__main__":
    unittest.main()
