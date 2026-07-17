#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import unittest
import urllib.error
from pathlib import Path

from app.core.model_api import (
    ModelResponseParseError,
    build_model_request,
    parse_model_response,
    request_model,
    uses_responses_api,
)


class _Response:
    def __init__(self, body: str, content_type: str = "application/json") -> None:
        self._body = body.encode("utf-8")
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


class ModelApiTests(unittest.TestCase):
    def test_model_endpoint_construction_is_centralized(self):
        app_dir = Path(__file__).resolve().parents[1] / "app"
        helper = (app_dir / "core" / "model_api.py").resolve()
        offenders = []
        for path in app_dir.rglob("*.py"):
            if path.resolve() == helper:
                continue
            source = path.read_text(encoding="utf-8")
            endpoint_literals = (
                '"/chat/completions"',
                "'/chat/completions'",
                '"/responses"',
                "'/responses'",
            )
            if any(literal in source for literal in endpoint_literals):
                offenders.append(str(path.relative_to(app_dir)))
        self.assertEqual(offenders, [])

    def test_auto_mode_preserves_legacy_chat_and_enables_known_search_models(self):
        self.assertFalse(uses_responses_api("auto", "legacy-search-model", web_search=True))
        self.assertTrue(uses_responses_api("auto", "grok-4.5", web_search=True))
        self.assertTrue(uses_responses_api("auto", "gpt-5.6-sol", web_search=True))
        self.assertFalse(uses_responses_api("chat-completions", "gpt-5.6-sol", web_search=True))
        self.assertTrue(uses_responses_api("responses", "legacy-model"))

    def test_gpt_x_search_does_not_enable_responses_in_auto_mode(self):
        request = build_model_request(
            "https://model.example/v1",
            "gpt-5.6-sol",
            [{"role": "user", "content": "search X"}],
            max_tokens=123,
            api_mode="auto",
            tools=[{"type": "x_search"}],
        )

        self.assertEqual(request.endpoint, "https://model.example/v1/chat/completions")
        self.assertEqual(request.payload["max_tokens"], 123)
        self.assertNotIn("tools", request.payload)

    def test_chat_request_uses_max_tokens(self):
        request = build_model_request(
            "https://model.example/v1/",
            "legacy-model",
            [{"role": "user", "content": "hello"}],
            max_tokens=123,
            api_mode="chat",
        )

        self.assertEqual(request.endpoint, "https://model.example/v1/chat/completions")
        self.assertEqual(request.payload["max_tokens"], 123)
        self.assertNotIn("max_output_tokens", request.payload)

    def test_grok_responses_request_uses_max_output_tokens(self):
        request = build_model_request(
            "https://model.example/v1",
            "grok-4.5",
            [{"role": "user", "content": "hello"}],
            max_tokens=321,
            api_mode="auto",
            tools=[{"type": "web_search"}],
            reasoning={"effort": "low"},
        )

        self.assertEqual(request.endpoint, "https://model.example/v1/responses")
        self.assertEqual(request.payload["max_output_tokens"], 321)
        self.assertEqual(request.payload["tools"], [{"type": "web_search"}])
        self.assertNotIn("max_tokens", request.payload)

    def test_gpt_56_responses_request_omits_rejected_output_limit(self):
        request = build_model_request(
            "https://model.example/v1",
            "gpt-5.6-sol",
            [{"role": "user", "content": "hello"}],
            max_tokens=4096,
            api_mode="auto",
            tools=[{"type": "web_search"}],
        )

        self.assertEqual(request.endpoint, "https://model.example/v1/responses")
        self.assertNotIn("max_output_tokens", request.payload)
        self.assertNotIn("max_tokens", request.payload)

    def test_parse_chat_json_and_sse(self):
        parsed_json = parse_model_response(
            '{"choices":[{"message":{"content":"json ok"},"finish_reason":"stop"}]}'
        )
        parsed_sse = parse_model_response(
            "event: message\n"
            'data: {"choices":[{"delta":{"content":"sse "}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n",
            "text/event-stream",
        )

        self.assertEqual(parsed_json.content, "json ok")
        self.assertIn("finish_reason=stop", parsed_json.detail)
        self.assertEqual(parsed_sse.content, "sse ok")
        self.assertIn("sse_chunks=2", parsed_sse.detail)

    def test_parse_responses_json_and_forced_sse(self):
        parsed_json = parse_model_response(
            '{"status":"completed","output":[{"content":[{"type":"output_text","text":"json result"}]}]}'
        )
        raw_sse = (
            "event: response.created\n"
            'data: {"type":"response.created","response":{"status":"in_progress"}}\n\n'
            "event: response.web_search_call.searching\n"
            'data: {"type":"response.web_search_call.searching"}\n\n'
            "event: response.output_text.delta\n"
            'data: {"type":"response.output_text.delta","delta":"live "}\n\n'
            "event: response.output_text.delta\n"
            'data: {"type":"response.output_text.delta","delta":"result"}\n\n'
            "event: response.completed\n"
            'data: {"type":"response.completed","response":{"status":"completed","output":[{"content":[{"type":"output_text","text":"live result"}]}]}}\n\n'
        )
        parsed_sse = parse_model_response(raw_sse, "text/event-stream")

        self.assertEqual(parsed_json.content, "json result")
        self.assertEqual(parsed_sse.content, "live result")
        self.assertIn("search_events=1", parsed_sse.detail)

    def test_parse_failures_use_dedicated_exception(self):
        for raw in ("", "<html>gateway error</html>", "[]"):
            with self.subTest(raw=raw):
                with self.assertRaises(ModelResponseParseError):
                    parse_model_response(raw)

    def test_unknown_responses_model_retries_without_unsupported_output_limit(self):
        request = build_model_request(
            "https://model.example/v1",
            "gateway-model",
            [{"role": "user", "content": "hello"}],
            max_tokens=500,
            api_mode="responses",
        )
        error_bodies = (
            b'{"detail":"Unsupported parameter: max_output_tokens"}',
            b'{"detail":"max_output_tokens is unsupported"}',
            b'{"detail":"gateway does not support max_output_tokens"}',
        )

        for error_body in error_bodies:
            with self.subTest(error_body=error_body):
                payloads: list[dict] = []

                def opener(req, timeout=0):
                    payloads.append(json.loads(req.data.decode("utf-8")))
                    if len(payloads) == 1:
                        body = io.BytesIO(error_body)
                        raise urllib.error.HTTPError(
                            req.full_url, 400, "Bad Request", {}, body
                        )
                    return _Response(
                        '{"output":[{"content":[{"type":"output_text","text":"ok"}]}]}'
                    )

                parsed = request_model(request, "secret", timeout=3, opener=opener)

                self.assertEqual(parsed.content, "ok")
                self.assertEqual(len(payloads), 2)
                self.assertIn("max_output_tokens", payloads[0])
                self.assertNotIn("max_output_tokens", payloads[1])

    def test_invalid_output_limit_value_does_not_retry_without_parameter(self):
        request = build_model_request(
            "https://model.example/v1",
            "gateway-model",
            [{"role": "user", "content": "hello"}],
            max_tokens=500,
            api_mode="responses",
        )
        payloads: list[dict] = []

        def opener(req, timeout=0):
            payloads.append(json.loads(req.data.decode("utf-8")))
            body = io.BytesIO(
                b'{"detail":"Invalid parameter: max_output_tokens must be at most 200"}'
            )
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, body)

        with self.assertRaises(urllib.error.HTTPError) as raised:
            request_model(request, "secret", timeout=3, opener=opener)

        self.assertEqual(len(payloads), 1)
        self.assertIn("max_output_tokens", payloads[0])
        self.assertIn(b"Invalid parameter", raised.exception.read())


if __name__ == "__main__":
    unittest.main()
