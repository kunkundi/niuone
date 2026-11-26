from __future__ import annotations

import io
import json
import ssl
import unittest
import urllib.error

from app.core.model_api import build_model_request, request_model, stream_model_response
from app.core.model_reasoning import (
    UnsupportedReasoningEffortError,
    reasoning_effort_capability,
    resolve_model_reasoning_effort,
)


class OpenAISolCompatibilityTests(unittest.TestCase):
    def test_sol_61_recognizes_only_documented_efforts(self):
        capability = reasoning_effort_capability("gpt-6.1-sol")
        self.assertIsNotNone(capability)
        self.assertEqual(capability.default_effort, "medium")
        for effort in ("low", "medium", "high", "xhigh", "max"):
            self.assertEqual(
                resolve_model_reasoning_effort("gpt-6.1-sol", effort).effective_effort,
                effort,
            )
        for effort in ("none", "minimal", "ultra"):
            with self.assertRaises(UnsupportedReasoningEffortError):
                resolve_model_reasoning_effort("gpt-6.1-sol", effort)
        self.assertIsNone(reasoning_effort_capability("gpt-6.1-sol-custom"))

    def test_sol_61_defaults_to_bounded_standard_responses(self):
        request = build_model_request(
            "https://api.openai.com/v1", "gpt-6.1-sol",
            [{"role": "user", "content": "Return JSON."}],
            reasoning_effort="max", max_tokens=16384,
            extra_payload={"temperature": 0.2, "top_p": 0.9, "logprobs": True},
        )
        self.assertEqual(request.api_mode, "responses")
        self.assertEqual(request.payload["max_output_tokens"], 16384)
        self.assertEqual(request.payload["reasoning"], {"effort": "max"})
        self.assertEqual(request.payload["service_tier"], "default")
        self.assertFalse(request.payload["store"])
        for name in ("max_tokens", "max_completion_tokens", "temperature", "top_p", "logprobs"):
            self.assertNotIn(name, request.payload)

    def test_explicit_sol_chat_uses_reasoning_inclusive_limit(self):
        request = build_model_request(
            "https://api.openai.com/v1", "gpt-6.1-sol", [],
            api_mode="chat", reasoning_effort="max", max_tokens=16384,
        )
        self.assertEqual(request.api_mode, "chat")
        self.assertEqual(request.payload["max_completion_tokens"], 16384)
        self.assertEqual(request.payload["reasoning_effort"], "max")
        self.assertNotIn("max_tokens", request.payload)

    def test_sol_output_limit_is_never_removed_after_http_400(self):
        request = build_model_request(
            "https://api.openai.com/v1", "gpt-6.1-sol", [],
            api_mode="responses", reasoning_effort="max", max_tokens=100,
        )
        for streaming in (False, True):
            calls = []

            def opener(req, **kwargs):
                calls.append(json.loads(req.data))
                raise urllib.error.HTTPError(
                    req.full_url, 400, "Bad Request", {},
                    io.BytesIO(b'{"error":"Unsupported parameter: max_output_tokens"}'),
                )

            with self.subTest(streaming=streaming), self.assertRaises(urllib.error.HTTPError):
                if streaming:
                    list(stream_model_response(request, "test-key", timeout=1, opener=opener))
                else:
                    request_model(request, "test-key", timeout=1, opener=opener)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["max_output_tokens"], 100)

    def test_unknown_model_and_existing_gpt_56_keep_legacy_defaults(self):
        for model in ("gpt-5.6-sol", "gpt-6.1-sol-custom", "legacy"):
            request = build_model_request(
                "https://model.example/v1", model, [], max_tokens=100,
                extra_payload={"temperature": 0.2},
            )
            self.assertEqual(request.api_mode, "chat")
            self.assertEqual(request.payload["max_tokens"], 100)
            self.assertEqual(request.payload["temperature"], 0.2)
            self.assertNotIn("service_tier", request.payload)
            self.assertNotIn("store", request.payload)

    def test_official_api_rejects_legacy_unverified_tls_context(self):
        request = build_model_request("https://api.openai.com/v1", "gpt-6.1-sol", [], max_tokens=100)
        insecure = ssl._create_unverified_context()
        seen = []
        def opener(req, **kwargs):
            seen.append(kwargs["context"])
            raise urllib.error.HTTPError(req.full_url, 401, "test", {}, io.BytesIO(b"{}"))
        with self.assertRaises(urllib.error.HTTPError):
            request_model(request, "test-key", timeout=1, opener=opener, ssl_context=insecure)
        self.assertEqual(seen[0].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(seen[0].check_hostname)
        verified = ssl.create_default_context()
        with self.assertRaises(urllib.error.HTTPError):
            request_model(request, "test-key", timeout=1, opener=opener, ssl_context=verified)
        self.assertIs(seen[1], verified)


if __name__ == "__main__":
    unittest.main()
