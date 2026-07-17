#!/usr/bin/env python3
"""Regression tests for the built-in iWencai gateway client."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error

from app.market_data.iwencai_client import (
    IwencaiClient,
    IwencaiConfig,
    IwencaiConfigurationError,
    IwencaiResponseError,
    normalize_base_url,
)


class FakeResponse:
    def __init__(self, payload: object):
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, _size: int = -1) -> bytes:
        return self.body


class IwencaiClientTests(unittest.TestCase):
    def config(self, **overrides) -> IwencaiConfig:
        values = {
            "enabled": True,
            "base_url": "https://openapi.iwencai.com",
            "api_key": "test-secret",
            "timeout_seconds": 8,
            "max_retries": 1,
            "max_concurrency": 2,
        }
        values.update(overrides)
        return IwencaiConfig(**values)

    def test_query_sends_required_headers_and_string_pagination(self):
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return FakeResponse({"datas": [{"股票代码": "000001.SZ"}], "code_count": 1})

        result = IwencaiClient(
            self.config(),
            opener=opener,
            sleep=lambda _seconds: None,
            semaphore=threading.BoundedSemaphore(1),
        ).query("平安银行最新价", page=2, limit=20)

        self.assertEqual(result["code_count"], 1)
        self.assertEqual(len(result["trace_id"]), 64)
        request, timeout = calls[0]
        headers = {name.lower(): value for name, value in request.header_items()}
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://openapi.iwencai.com/v1/query2data")
        self.assertEqual(timeout, 8)
        self.assertEqual(headers["authorization"], "Bearer test-secret")
        self.assertEqual(headers["x-claw-call-type"], "normal")
        self.assertEqual(headers["x-claw-skill-id"], "hithink-market-query")
        self.assertEqual(len(headers["x-claw-trace-id"]), 64)
        self.assertEqual(payload["page"], "2")
        self.assertEqual(payload["limit"], "20")
        self.assertEqual(payload["is_cache"], "1")

    def test_retry_uses_retry_call_type_and_new_trace_id(self):
        headers = []
        sleeps = []

        def opener(request, timeout):
            del timeout
            headers.append({name.lower(): value for name, value in request.header_items()})
            if len(headers) == 1:
                raise urllib.error.URLError("temporary")
            return FakeResponse({"datas": [], "code_count": 0})

        result = IwencaiClient(
            self.config(),
            opener=opener,
            sleep=sleeps.append,
            semaphore=threading.BoundedSemaphore(1),
        ).query("今日龙虎榜")

        self.assertEqual(result["datas"], [])
        self.assertEqual([item["x-claw-call-type"] for item in headers], ["normal", "retry"])
        self.assertNotEqual(headers[0]["x-claw-trace-id"], headers[1]["x-claw-trace-id"])
        self.assertEqual(sleeps, [0.25])

    def test_configuration_requires_https_and_key(self):
        with self.assertRaises(ValueError):
            normalize_base_url("http://openapi.iwencai.com")
        with self.assertRaises(ValueError):
            normalize_base_url("https://user:password@example.com")
        with self.assertRaises(IwencaiConfigurationError) as caught:
            IwencaiClient(self.config(api_key="")).query("贵州茅台最新价")
        self.assertEqual(caught.exception.code, "api_key_missing")

    def test_response_without_datas_is_rejected_without_echoing_body(self):
        def opener(_request, timeout):
            del timeout
            return FakeResponse({"message": "upstream secret detail"})

        with self.assertRaises(IwencaiResponseError) as caught:
            IwencaiClient(
                self.config(max_retries=0),
                opener=opener,
                semaphore=threading.BoundedSemaphore(1),
            ).query("贵州茅台最新价")
        self.assertEqual(caught.exception.code, "upstream_error")
        self.assertNotIn("upstream secret detail", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
