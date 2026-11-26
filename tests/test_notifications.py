#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import threading
import urllib.error
import urllib.parse
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
COMPAT = APP / "compat"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
if str(COMPAT) not in sys.path:
    sys.path.insert(0, str(COMPAT))

import notifications  # noqa: E402


FEISHU_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/12345678-abcd-efgh"
DINGTALK_URL = "https://oapi.dingtalk.com/robot/send?access_token=dingtalk-access-token"
WECOM_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=12345678-abcd-efgh-ijkl"
TELEGRAM_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
TELEGRAM_CHAT_ID = "-1001234567890"
SERVERCHAN_SENDKEY = "SCT123456abcdefghijklmn"
FIXED_TIME = 1_700_000_000.25


def sample_trades() -> list[dict]:
    return [
        {
            "time": "2026-07-11 10:00:01",
            "action": "BUY",
            "code": "000001",
            "name": "平安银行",
            "shares": 100,
            "price": 10.123,
            "amount": 1012.3,
            "fee": 0.11,
            "order_position_pct": 1.01,
            "position_after_trade_pct": 3.21,
            "buy_strategy": "trend_pullback",
            "reason": "趋势回踩确认",
        },
        {
            "time": "2026-07-11 14:45:02",
            "action": "SELL",
            "code": "600000",
            "name": "浦发银行",
            "shares": 200,
            "price": 12.5,
            "amount": 2500,
            "fee": 1.27,
            "order_position_pct": 2.5,
            "pnl": 215.5,
            "pnl_pct": 9.43,
            "cumulative_realized_pnl": 215.5,
            "realized_return_pct": 4.72,
            "exit_rule": "time_stop",
            "reason": "尾盘时间止损检查",
        },
    ]


def all_channels_env(*, signed: bool = True) -> dict[str, str]:
    env = {
        notifications.GLOBAL_ENABLED_ENV: "1",
        notifications.TIMEOUT_ENV: "7",
        notifications.FEISHU_ENABLED_ENV: "1",
        notifications.FEISHU_WEBHOOK_ENV: FEISHU_URL,
        notifications.DINGTALK_ENABLED_ENV: "1",
        notifications.DINGTALK_WEBHOOK_ENV: DINGTALK_URL,
        notifications.WECOM_ENABLED_ENV: "1",
        notifications.WECOM_WEBHOOK_ENV: WECOM_URL,
        notifications.TELEGRAM_ENABLED_ENV: "1",
        notifications.TELEGRAM_TOKEN_ENV: TELEGRAM_TOKEN,
        notifications.TELEGRAM_CHAT_ID_ENV: TELEGRAM_CHAT_ID,
        notifications.SERVERCHAN_ENABLED_ENV: "1",
        notifications.SERVERCHAN_SENDKEY_ENV: SERVERCHAN_SENDKEY,
    }
    if signed:
        env[notifications.FEISHU_SECRET_ENV] = "feishu-signing-secret"
        env[notifications.DINGTALK_SECRET_ENV] = "SEC-dingtalk-signing-secret"
    return env


def single_channel_env(channel: str) -> dict[str, str]:
    env = {notifications.GLOBAL_ENABLED_ENV: "1"}
    if channel == "feishu":
        env.update({
            notifications.FEISHU_ENABLED_ENV: "1",
            notifications.FEISHU_WEBHOOK_ENV: FEISHU_URL,
        })
    elif channel == "dingtalk":
        env.update({
            notifications.DINGTALK_ENABLED_ENV: "1",
            notifications.DINGTALK_WEBHOOK_ENV: DINGTALK_URL,
        })
    elif channel == "wecom":
        env.update({
            notifications.WECOM_ENABLED_ENV: "1",
            notifications.WECOM_WEBHOOK_ENV: WECOM_URL,
        })
    elif channel == "telegram":
        env.update({
            notifications.TELEGRAM_ENABLED_ENV: "1",
            notifications.TELEGRAM_TOKEN_ENV: TELEGRAM_TOKEN,
            notifications.TELEGRAM_CHAT_ID_ENV: TELEGRAM_CHAT_ID,
        })
    elif channel == "serverchan":
        env.update({
            notifications.SERVERCHAN_ENABLED_ENV: "1",
            notifications.SERVERCHAN_SENDKEY_ENV: SERVERCHAN_SENDKEY,
        })
    else:
        raise AssertionError(channel)
    return env


def channel_for_url(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname
    if host in {"open.feishu.cn", "open.larksuite.com"}:
        return "feishu"
    if host == "oapi.dingtalk.com":
        return "dingtalk"
    if host == "qyapi.weixin.qq.com":
        return "wecom"
    if host == "api.telegram.org":
        return "telegram"
    if host == "sctapi.ftqq.com" or str(host or "").endswith(".push.ft07.com"):
        return "serverchan"
    raise AssertionError(f"unexpected URL host: {host}")


SUCCESS_RESPONSES = {
    "feishu": {"code": 0, "msg": "success"},
    "dingtalk": {"errcode": 0, "errmsg": "ok"},
    "wecom": {"errcode": 0, "errmsg": "ok"},
    "telegram": {"ok": True, "result": {"message_id": 1}},
    "serverchan": {"code": 0, "message": ""},
}


class RecordingTransport:
    def __init__(self, responses=None):
        self.responses = responses or SUCCESS_RESPONSES
        self.calls: list[dict] = []
        self.lock = threading.Lock()

    def __call__(self, url, payload, timeout):
        channel = channel_for_url(url)
        with self.lock:
            self.calls.append({
                "channel": channel,
                "url": url,
                "payload": json.loads(json.dumps(payload, ensure_ascii=False)),
                "timeout": timeout,
            })
        response = self.responses[channel]
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return response(url, payload, timeout)
        return response


class NotificationTests(unittest.TestCase):
    def test_builtin_registry_has_stable_order(self):
        self.assertEqual(
            notifications.registered_channels(),
            ("feishu", "dingtalk", "wecom", "telegram", "serverchan"),
        )

    def test_trade_batch_builds_all_payloads_and_signatures(self):
        transport = RecordingTransport()

        results = notifications.notify_trade_executions(
            sample_trades(),
            all_channels_env(),
            transport=transport,
            clock=lambda: FIXED_TIME,
        )

        self.assertEqual([result.channel for result in results], ["feishu", "dingtalk", "wecom", "telegram", "serverchan"])
        self.assertTrue(all(result.ok for result in results), results)
        self.assertEqual(len(transport.calls), 5)
        calls = {call["channel"]: call for call in transport.calls}
        self.assertTrue(all(call["timeout"] == 7 for call in transport.calls))

        notification = notifications._trade_notification(sample_trades())
        self.assertIsNotNone(notification)
        plain_text = notification.plain_text()
        self.assertIn("成交信息（2笔）", plain_text)
        self.assertNotIn("牛牛1号", plain_text)
        self.assertNotIn("模拟成交", plain_text)
        self.assertNotIn("非实盘", plain_text)
        self.assertIn("1. 买入｜平安银行（000001）", plain_text)
        self.assertIn("成交：100 股 × ¥10.123", plain_text)
        self.assertIn("金额：¥1,012.30\n本笔成交仓位：1.01%", plain_text)
        self.assertNotIn("费用", plain_text)
        self.assertNotIn("成交后仓位", plain_text)
        self.assertIn("2. 卖出｜浦发银行（600000）", plain_text)
        self.assertIn("已实现盈亏 / 收益率：+¥215.50（+4.72%）", plain_text)

        feishu_payload = calls["feishu"]["payload"]
        self.assertEqual(feishu_payload["msg_type"], "interactive")
        self.assertEqual(
            feishu_payload["card"]["header"]["title"]["content"],
            "成交信息（2笔）",
        )
        feishu_elements = feishu_payload["card"]["elements"]
        self.assertEqual(
            [element["tag"] for element in feishu_elements],
            ["div", "div", "div", "hr", "div", "div", "div"],
        )
        self.assertEqual(
            feishu_elements[0]["text"]["content"],
            "**1. 平安银行（000001）**　<font color='red'>买入</font>",
        )
        buy_fields = feishu_elements[1]["fields"]
        self.assertEqual(
            [field["is_short"] for field in buy_fields],
            [True, True, True, True],
        )
        self.assertEqual(buy_fields[0]["text"]["content"], "**成交数量**\n100 股")
        self.assertEqual(buy_fields[1]["text"]["content"], "**成交价格**\n¥10.123")
        self.assertEqual(buy_fields[2]["text"]["content"], "**成交金额**\n¥1,012.30")
        self.assertEqual(buy_fields[3]["text"]["content"], "**本笔成交仓位**\n1.01%")
        self.assertEqual(
            feishu_elements[2]["text"]["content"],
            "**时间**　2026-07-11 10:00:01\n"
            "**策略**　trend\\_pullback\n"
            "**理由**　趋势回踩确认",
        )

        dingtalk_payload = calls["dingtalk"]["payload"]
        self.assertEqual(dingtalk_payload["msgtype"], "markdown")
        self.assertEqual(dingtalk_payload["markdown"]["title"], "成交信息（2笔）")
        dingtalk_markdown = dingtalk_payload["markdown"]["text"]
        self.assertIn("### 成交信息（2笔）", dingtalk_markdown)
        self.assertIn("**已实现盈亏 / 收益率**　+¥215.50（+4.72%）", dingtalk_markdown)
        self.assertNotIn("费用", dingtalk_markdown)
        self.assertNotIn("\n- **", dingtalk_markdown)

        wecom_payload = calls["wecom"]["payload"]
        self.assertEqual(wecom_payload, {
            "msgtype": "markdown",
            "markdown": {"content": dingtalk_markdown},
        })

        telegram_payload = calls["telegram"]["payload"]
        self.assertEqual(telegram_payload["parse_mode"], "HTML")
        self.assertIn("<b>成交信息（2笔）</b>", telegram_payload["text"])
        self.assertIn("<b>成交</b>　100 股 × ¥10.123", telegram_payload["text"])

        feishu_timestamp = str(int(FIXED_TIME))
        feishu_key = f"{feishu_timestamp}\nfeishu-signing-secret".encode()
        expected_feishu_sign = base64.b64encode(
            hmac.new(feishu_key, digestmod=hashlib.sha256).digest()
        ).decode()
        self.assertEqual(feishu_payload["timestamp"], feishu_timestamp)
        self.assertEqual(feishu_payload["sign"], expected_feishu_sign)

        ding_query = urllib.parse.parse_qs(urllib.parse.urlsplit(calls["dingtalk"]["url"]).query)
        ding_timestamp = str(int(FIXED_TIME * 1000))
        expected_ding_sign = base64.b64encode(
            hmac.new(
                b"SEC-dingtalk-signing-secret",
                f"{ding_timestamp}\nSEC-dingtalk-signing-secret".encode(),
                hashlib.sha256,
            ).digest()
        ).decode()
        self.assertEqual(ding_query["access_token"], ["dingtalk-access-token"])
        self.assertEqual(ding_query["timestamp"], [ding_timestamp])
        self.assertEqual(ding_query["sign"], [expected_ding_sign])
        self.assertEqual(calls["dingtalk"]["payload"]["at"], {"isAtAll": False})

        telegram_call = calls["telegram"]
        self.assertEqual(
            telegram_call["url"],
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        )
        self.assertEqual(telegram_call["payload"]["chat_id"], TELEGRAM_CHAT_ID)
        self.assertTrue(telegram_call["payload"]["disable_web_page_preview"])

    def test_signing_fields_are_omitted_when_secrets_are_blank(self):
        transport = RecordingTransport()

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            all_channels_env(signed=False),
            transport=transport,
            clock=lambda: FIXED_TIME,
        )

        self.assertTrue(all(result.ok for result in results), results)
        calls = {call["channel"]: call for call in transport.calls}
        self.assertNotIn("timestamp", calls["feishu"]["payload"])
        self.assertNotIn("sign", calls["feishu"]["payload"])
        self.assertEqual(
            urllib.parse.parse_qs(urllib.parse.urlsplit(calls["dingtalk"]["url"]).query),
            {"access_token": ["dingtalk-access-token"]},
        )

    def test_feishu_legacy_success_response_is_supported(self):
        transport = RecordingTransport({"feishu": {"StatusCode": 0, "StatusMessage": "success"}})

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            single_channel_env("feishu"),
            transport=transport,
        )

        self.assertEqual(results, [notifications.DeliveryResult("feishu", True, "")])

    def test_disabled_and_non_trade_batches_make_no_requests(self):
        transport = RecordingTransport()
        enabled = single_channel_env("wecom")

        self.assertEqual(
            notifications.notify_trade_executions(sample_trades(), {}, transport=transport),
            [],
        )
        self.assertEqual(
            notifications.notify_trade_executions([], enabled, transport=transport),
            [],
        )
        self.assertEqual(
            notifications.notify_trade_executions([{"action": "HOLD"}], enabled, transport=transport),
            [],
        )
        self.assertEqual(transport.calls, [])

    def test_only_enabled_channels_are_constructed_and_sent(self):
        transport = RecordingTransport()

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            single_channel_env("wecom"),
            transport=transport,
        )

        self.assertEqual(results, [notifications.DeliveryResult("wecom", True, "")])
        self.assertEqual([call["channel"] for call in transport.calls], ["wecom"])

    def test_sell_only_batch_is_sent_to_every_enabled_channel(self):
        transport = RecordingTransport()

        results = notifications.notify_trade_executions(
            [sample_trades()[1]],
            all_channels_env(signed=False),
            transport=transport,
        )

        self.assertEqual(
            [result.channel for result in results],
            ["feishu", "dingtalk", "wecom", "telegram", "serverchan"],
        )
        self.assertTrue(all(result.ok for result in results), results)
        calls = {call["channel"]: call for call in transport.calls}
        feishu_json = json.dumps(calls["feishu"]["payload"], ensure_ascii=False)
        self.assertIn("**1. 浦发银行（600000）**", feishu_json)
        self.assertIn("<font color='green'>卖出</font>", feishu_json)
        self.assertIn("+¥215.50（+4.72%）", feishu_json)
        dingtalk_text = calls["dingtalk"]["payload"]["markdown"]["text"]
        self.assertIn("卖出｜浦发银行（600000）", dingtalk_text)
        self.assertIn("**卖出仓位（占总资产）**　**2.50%**", dingtalk_text)
        self.assertIn("**已实现盈亏 / 收益率**　+¥215.50（+4.72%）", dingtalk_text)
        self.assertIn("卖出｜浦发银行（600000）", calls["wecom"]["payload"]["markdown"]["content"])
        self.assertIn("卖出｜浦发银行（600000）", calls["telegram"]["payload"]["text"])

    def test_dispatch_to_channel_ignores_switches_and_sends_only_selected_channel_once(self):
        env = all_channels_env()
        env[notifications.GLOBAL_ENABLED_ENV] = "0"
        env[notifications.DINGTALK_ENABLED_ENV] = "0"
        transport = RecordingTransport()

        result = notifications.dispatch_to_channel(
            notifications.Notification("notification.test", "通知测试", "这是一条测试消息"),
            "dingtalk",
            env,
            transport=transport,
            clock=lambda: FIXED_TIME,
        )

        self.assertEqual(result, notifications.DeliveryResult("dingtalk", True, ""))
        self.assertEqual([call["channel"] for call in transport.calls], ["dingtalk"])
        self.assertEqual(transport.calls[0]["payload"]["msgtype"], "text")
        self.assertEqual(
            transport.calls[0]["payload"]["text"]["content"],
            "通知测试\n这是一条测试消息",
        )

    def test_dispatch_to_channel_rejects_unknown_channel_without_request(self):
        transport = RecordingTransport()

        result = notifications.dispatch_to_channel(
            notifications.Notification("notification.test", "通知测试", "未知渠道"),
            "not-registered",
            all_channels_env(),
            transport=transport,
        )

        self.assertEqual(result.channel, "not-registered")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "unknown notification channel")
        self.assertEqual(transport.calls, [])

    def test_dispatch_to_channel_timeout_and_config_errors_are_safe(self):
        timeout_env = single_channel_env("wecom")
        timeout_env[notifications.TIMEOUT_ENV] = "not-a-number"
        timeout_transport = RecordingTransport()

        timeout_result = notifications.dispatch_to_channel(
            notifications.Notification("notification.test", "通知测试", "超时配置"),
            "wecom",
            timeout_env,
            transport=timeout_transport,
        )

        self.assertEqual(timeout_result.channel, "wecom")
        self.assertFalse(timeout_result.ok)
        self.assertTrue(timeout_result.error)
        self.assertEqual(timeout_transport.calls, [])

        leaked_secret = "CONFIG-SECRET-123"
        config_env = single_channel_env("feishu")
        config_env[notifications.FEISHU_WEBHOOK_ENV] = (
            f"https://evil.example/open-apis/bot/v2/hook/{leaked_secret}"
        )
        config_transport = RecordingTransport()

        config_result = notifications.dispatch_to_channel(
            notifications.Notification("notification.test", "通知测试", "渠道配置"),
            "feishu",
            config_env,
            transport=config_transport,
        )

        self.assertFalse(config_result.ok)
        self.assertNotIn(leaked_secret, config_result.error)
        self.assertNotIn(config_env[notifications.FEISHU_WEBHOOK_ENV], config_result.error)
        self.assertEqual(config_transport.calls, [])

    def test_dispatch_to_channel_transport_exception_does_not_leak_secret(self):
        env = single_channel_env("telegram")
        calls = []

        def failing_transport(url, payload, timeout):
            calls.append((url, payload, timeout))
            raise RuntimeError(f"request failed for {TELEGRAM_TOKEN}")

        result = notifications.dispatch_to_channel(
            notifications.Notification("notification.test", "通知测试", "传输失败"),
            "telegram",
            env,
            transport=failing_transport,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "RuntimeError")
        self.assertNotIn(TELEGRAM_TOKEN, result.error)
        self.assertEqual(len(calls), 1)

    def test_invalid_timeout_fails_enabled_channels_without_requests(self):
        for timeout in ("not-a-number", "0", "31", "nan"):
            with self.subTest(timeout=timeout):
                env = all_channels_env()
                env[notifications.TIMEOUT_ENV] = timeout
                transport = RecordingTransport()

                results = notifications.notify_trade_executions(
                    sample_trades()[:1],
                    env,
                    transport=transport,
                )

                self.assertEqual([item.channel for item in results], ["feishu", "dingtalk", "wecom", "telegram", "serverchan"])
                self.assertTrue(all(not item.ok for item in results))
                self.assertEqual(transport.calls, [])

    def test_invalid_urls_and_credentials_never_reach_transport(self):
        cases = []

        env = single_channel_env("feishu")
        env[notifications.FEISHU_WEBHOOK_ENV] = "http://open.feishu.cn/open-apis/bot/v2/hook/SUPERSECRET"
        cases.append(("feishu_http", env, "SUPERSECRET"))
        env = single_channel_env("feishu")
        env[notifications.FEISHU_WEBHOOK_ENV] = "https://evil.example/open-apis/bot/v2/hook/SUPERSECRET"
        cases.append(("feishu_host", env, "SUPERSECRET"))
        env = single_channel_env("feishu")
        env[notifications.FEISHU_WEBHOOK_ENV] = "https://user:pass@open.feishu.cn/open-apis/bot/v2/hook/SUPERSECRET"
        cases.append(("feishu_url_credentials", env, "SUPERSECRET"))
        env = single_channel_env("feishu")
        env[notifications.FEISHU_WEBHOOK_ENV] = "https://open.feishu.cn:444/open-apis/bot/v2/hook/SUPERSECRET"
        cases.append(("feishu_port", env, "SUPERSECRET"))
        env = single_channel_env("feishu")
        env[notifications.FEISHU_WEBHOOK_ENV] = FEISHU_URL + "#SUPERSECRET"
        cases.append(("feishu_fragment", env, "SUPERSECRET"))

        env = single_channel_env("dingtalk")
        env[notifications.DINGTALK_WEBHOOK_ENV] = "https://oapi.dingtalk.com/robot/send"
        cases.append(("dingtalk_missing_token", env, "dingtalk-access-token"))
        env = single_channel_env("dingtalk")
        env[notifications.DINGTALK_WEBHOOK_ENV] = DINGTALK_URL + "&extra=SUPERSECRET"
        cases.append(("dingtalk_extra_query", env, "SUPERSECRET"))

        env = single_channel_env("wecom")
        env[notifications.WECOM_WEBHOOK_ENV] = "https://qyapi.weixin.qq.com/cgi-bin/not-webhook/send?key=SUPERSECRET"
        cases.append(("wecom_path", env, "SUPERSECRET"))
        env = single_channel_env("wecom")
        env[notifications.WECOM_WEBHOOK_ENV] = WECOM_URL + "%0Asecret"
        cases.append(("wecom_control", env, "secret"))

        env = single_channel_env("telegram")
        env[notifications.TELEGRAM_TOKEN_ENV] = "SUPERSECRET"
        cases.append(("telegram_token", env, "SUPERSECRET"))
        env = single_channel_env("telegram")
        env[notifications.TELEGRAM_CHAT_ID_ENV] = "bad chat SUPERSECRET"
        cases.append(("telegram_chat", env, "SUPERSECRET"))
        env = single_channel_env("telegram")
        env[notifications.TELEGRAM_TOKEN_ENV] = TELEGRAM_TOKEN + "\nSUPERSECRET"
        cases.append(("telegram_control", env, "SUPERSECRET"))

        for label, env, secret in cases:
            with self.subTest(label=label):
                transport = RecordingTransport()
                results = notifications.notify_trade_executions(
                    sample_trades()[:1],
                    env,
                    transport=transport,
                )
                self.assertEqual(len(results), 1)
                self.assertFalse(results[0].ok)
                self.assertNotIn(secret, results[0].error)
                self.assertEqual(transport.calls, [])

    def test_provider_business_errors_are_isolated_and_sanitized(self):
        responses = {
            "feishu": {"code": 19001, "msg": "feishu-signing-secret"},
            "dingtalk": {"errcode": 310000, "errmsg": "SEC-dingtalk-signing-secret"},
            "wecom": {"errcode": 93000, "errmsg": "12345678-abcd-efgh-ijkl"},
            "telegram": {"ok": False, "description": TELEGRAM_TOKEN},
        }
        transport = RecordingTransport(responses)

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            all_channels_env(),
            transport=transport,
        )

        self.assertEqual([item.channel for item in results], ["feishu", "dingtalk", "wecom", "telegram", "serverchan"])
        self.assertTrue(all(not item.ok for item in results))
        combined_errors = "\n".join(item.error for item in results)
        for secret in (
            "feishu-signing-secret",
            "SEC-dingtalk-signing-secret",
            "12345678-abcd-efgh-ijkl",
            TELEGRAM_TOKEN,
        ):
            self.assertNotIn(secret, combined_errors)

    def test_all_channels_run_concurrently_once_and_keep_result_order(self):
        barrier = threading.Barrier(5)
        counts = {name: 0 for name in ("feishu", "dingtalk", "wecom", "telegram", "serverchan")}
        lock = threading.Lock()

        def concurrent_transport(url, _payload, _timeout):
            channel = channel_for_url(url)
            with lock:
                counts[channel] += 1
            barrier.wait(timeout=3)
            if channel == "dingtalk":
                raise RuntimeError("transport leaked SEC-dingtalk-signing-secret")
            return SUCCESS_RESPONSES[channel]

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            all_channels_env(),
            transport=concurrent_transport,
            clock=lambda: FIXED_TIME,
        )

        self.assertEqual([item.channel for item in results], ["feishu", "dingtalk", "wecom", "telegram", "serverchan"])
        self.assertEqual([item.ok for item in results], [True, False, True, True, True])
        self.assertEqual(results[1].error, "RuntimeError")
        self.assertEqual(
            counts,
            {"feishu": 1, "dingtalk": 1, "wecom": 1, "telegram": 1, "serverchan": 1},
        )

    def test_http_and_network_errors_do_not_expose_request_urls(self):
        env = single_channel_env("telegram")

        def http_failure(url, _payload, _timeout):
            raise urllib.error.HTTPError(url, 500, TELEGRAM_TOKEN, {}, None)

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            env,
            transport=http_failure,
        )
        self.assertEqual(results[0].error, "HTTP error 500")
        self.assertNotIn(TELEGRAM_TOKEN, results[0].error)

        def network_failure(_url, _payload, _timeout):
            raise urllib.error.URLError(f"network failed for {TELEGRAM_TOKEN}")

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            env,
            transport=network_failure,
        )
        self.assertEqual(results[0].error, "network error")
        self.assertNotIn(TELEGRAM_TOKEN, results[0].error)

        def nominally_sanitized_failure(_url, _payload, _timeout):
            raise notifications.NotificationDeliveryError(f"provider echoed {TELEGRAM_TOKEN}")

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            env,
            transport=nominally_sanitized_failure,
        )
        self.assertEqual(results[0].error, "provider echoed [redacted]")
        self.assertNotIn(TELEGRAM_TOKEN, results[0].error)

    def test_serverchan_sends_markdown_body_with_a_bounded_single_line_title(self):
        transport = RecordingTransport()
        trade = sample_trades()[0]
        trade["reason"] = "第一行\n第二行 结尾"

        results = notifications.notify_trade_executions(
            [trade],
            single_channel_env("serverchan"),
            transport=transport,
        )

        self.assertTrue(results[0].ok, results)
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["url"], f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send")
        self.assertEqual(set(call["payload"]), {"title", "desp"})
        self.assertEqual(call["payload"]["title"], "成交信息（1笔）")
        self.assertNotIn("\n", call["payload"]["title"])
        self.assertLessEqual(len(call["payload"]["title"]), 32)
        self.assertIn("#### 1. 买入｜平安银行（000001）", call["payload"]["desp"])
        self.assertIn("**理由**", call["payload"]["desp"])

    def test_serverchan_truncates_an_overlong_title_to_the_provider_limit(self):
        transport = RecordingTransport()
        notification = notifications.Notification(
            "notification.test",
            "标题" * 40,
            "正文",
        )

        result = notifications.dispatch_to_channel(
            notification,
            "serverchan",
            single_channel_env("serverchan"),
            transport=transport,
        )

        self.assertTrue(result.ok, result)
        title = transport.calls[0]["payload"]["title"]
        self.assertEqual(len(title), 32)
        self.assertTrue(title.endswith("…"))

    def test_serverchan_sc3_sendkey_uses_its_uid_push_host(self):
        sendkey = "sctp1234tABCDEFGHIJ"
        transport = RecordingTransport()

        result = notifications.dispatch_to_channel(
            notifications.Notification("notification.test", "标题", "正文"),
            "serverchan",
            {
                notifications.SERVERCHAN_ENABLED_ENV: "1",
                notifications.SERVERCHAN_SENDKEY_ENV: sendkey,
            },
            transport=transport,
        )

        self.assertTrue(result.ok, result)
        self.assertEqual(
            transport.calls[0]["url"],
            f"https://1234.push.ft07.com/send/{sendkey}.send",
        )

    def test_serverchan_falls_back_to_plain_text_when_markdown_exceeds_the_shared_limit(self):
        transport = RecordingTransport()
        notification = notifications.Notification(
            "notification.test",
            "标题",
            "纯文本正文" * 900,
        )

        result = notifications.dispatch_to_channel(
            notification,
            "serverchan",
            single_channel_env("serverchan"),
            transport=transport,
        )

        self.assertTrue(result.ok, result)
        desp = transport.calls[0]["payload"]["desp"]
        self.assertIn("纯文本正文", desp)
        self.assertLessEqual(len(desp.encode("utf-8")), notifications.MAX_MESSAGE_BYTES)
        desp.encode("utf-8").decode("utf-8")

    def test_serverchan_business_error_and_invalid_sendkey_are_sanitized(self):
        transport = RecordingTransport({"serverchan": {"code": 40001, "message": SERVERCHAN_SENDKEY}})
        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            single_channel_env("serverchan"),
            transport=transport,
        )

        self.assertFalse(results[0].ok)
        self.assertNotIn(SERVERCHAN_SENDKEY, results[0].error)
        self.assertEqual(len(transport.calls), 1)

        cases = [
            ("blank", ""),
            ("full_url", f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send"),
            ("lowercase", SERVERCHAN_SENDKEY.lower()),
            ("control", SERVERCHAN_SENDKEY + "\nSUPERSECRET"),
        ]
        for label, value in cases:
            with self.subTest(label=label):
                env = {
                    notifications.GLOBAL_ENABLED_ENV: "1",
                    notifications.SERVERCHAN_ENABLED_ENV: "1",
                    notifications.SERVERCHAN_SENDKEY_ENV: value,
                }
                invalid_transport = RecordingTransport()
                invalid = notifications.notify_trade_executions(
                    sample_trades()[:1],
                    env,
                    transport=invalid_transport,
                )
                self.assertEqual(len(invalid), 1)
                self.assertFalse(invalid[0].ok)
                self.assertEqual(invalid_transport.calls, [])
                self.assertNotIn("SUPERSECRET", invalid[0].error)

    def test_long_unicode_message_is_truncated_on_a_character_boundary(self):
        trade = sample_trades()[0]
        trade["reason"] = "很长的交易原因" * 1000
        transport = RecordingTransport()

        results = notifications.notify_trade_executions(
            [trade] * 30,
            single_channel_env("wecom"),
            transport=transport,
        )

        self.assertTrue(results[0].ok)
        self.assertEqual(transport.calls[0]["payload"]["msgtype"], "text")
        text = transport.calls[0]["payload"]["text"]["content"]
        self.assertLessEqual(len(text.encode("utf-8")), notifications.MAX_MESSAGE_BYTES)
        text.encode("utf-8").decode("utf-8")

    def test_feishu_card_keeps_complete_trade_reason_beyond_shared_rich_text_limit(self):
        trade = sample_trades()[1]
        reason = "卖出理由需要保留完整上下文，包含趋势、板块、止损和仓位依据。" * 20
        trade["reason"] = reason
        transport = RecordingTransport()

        results = notifications.notify_trade_executions(
            [trade],
            single_channel_env("feishu"),
            transport=transport,
        )

        self.assertTrue(results[0].ok)
        payload = transport.calls[0]["payload"]
        self.assertEqual(payload["msg_type"], "interactive")
        details = payload["card"]["elements"][2]["text"]["content"]
        self.assertIn(f"**理由**　{reason}", details)
        self.assertNotIn("…", details)
        self.assertNotIn("内容已截断", details)

    def test_feishu_splits_large_trade_batches_without_cutting_sections(self):
        trade = sample_trades()[1]
        reason = "完整卖出依据" * 150
        trade["reason"] = reason
        transport = RecordingTransport()

        results = notifications.notify_trade_executions(
            [dict(trade) for _ in range(20)],
            single_channel_env("feishu"),
            transport=transport,
        )

        self.assertTrue(results[0].ok)
        self.assertGreater(len(transport.calls), 1)
        heading_count = 0
        reason_count = 0
        for call in transport.calls:
            payload = call["payload"]
            self.assertEqual(payload["msg_type"], "interactive")
            self.assertLessEqual(
                len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                28 * 1024,
            )
            for element in payload["card"]["elements"]:
                content = element.get("text", {}).get("content", "")
                heading_count += content.count("<font color='green'>卖出</font>")
                reason_count += content.count(f"**理由**　{reason}")
        self.assertEqual(heading_count, 20)
        self.assertEqual(reason_count, 20)

    def test_rich_trade_content_escapes_provider_markup(self):
        trade = sample_trades()[0]
        trade["name"] = "<b>测试</b>"
        trade["buy_strategy"] = "trend_pullback_*"
        trade["reason"] = "[伪链接](https://example.invalid) > 非引用"
        transport = RecordingTransport()

        results = notifications.notify_trade_executions(
            [trade],
            all_channels_env(signed=False),
            transport=transport,
        )

        self.assertTrue(all(result.ok for result in results), results)
        calls = {call["channel"]: call for call in transport.calls}
        feishu_json = json.dumps(calls["feishu"]["payload"], ensure_ascii=False)
        self.assertIn("&lt;b&gt;测试&lt;/b&gt;", feishu_json)
        self.assertNotIn("<b>测试</b>", feishu_json)
        markdown = calls["dingtalk"]["payload"]["markdown"]["text"]
        self.assertIn("&lt;b&gt;测试&lt;/b&gt;", markdown)
        self.assertIn(r"trend\_pullback\_\*", markdown)
        self.assertIn(r"\[伪链接\](https://example.invalid) &gt; 非引用", markdown)
        telegram_html = calls["telegram"]["payload"]["text"]
        self.assertIn("&lt;b&gt;测试&lt;/b&gt;", telegram_html)
        self.assertNotIn("<b>测试</b>", telegram_html)

    def test_only_order_position_above_ten_percent_is_highlighted(self):
        for order_position_pct, expected_red in ((10.0, False), (10.01, True)):
            with self.subTest(order_position_pct=order_position_pct):
                trade = sample_trades()[0]
                trade["order_position_pct"] = order_position_pct
                trade["position_after_trade_pct"] = 20.0
                transport = RecordingTransport()

                results = notifications.notify_trade_executions(
                    [trade],
                    single_channel_env("feishu"),
                    transport=transport,
                )

                self.assertTrue(results[0].ok)
                fields = transport.calls[0]["payload"]["card"]["elements"][1]["fields"]
                order_position_field = next(
                    field for field in fields
                    if "本笔成交仓位" in field["text"]["content"]
                )
                self.assertFalse(any(
                    "成交后仓位" in field["text"]["content"]
                    for field in fields
                ))
                content = order_position_field["text"]["content"]
                if expected_red:
                    self.assertEqual(
                        content,
                        "<font color='red'>**本笔成交仓位**\n10.01%</font>",
                    )
                    notification = notifications._trade_notification([trade])
                    self.assertIn("**本笔成交仓位**　**10.01%**", notification.markdown_text())
                    self.assertIn("<b>本笔成交仓位　10.01%</b>", notification.html_text())
                else:
                    self.assertEqual(content, "**本笔成交仓位**\n10.00%")

    def test_sell_ratio_and_full_exit_are_emphasized_in_every_channel(self):
        for after, label, ratio in (
            (200, "卖出50.00%持仓", "50.00%"),
            (0, "清仓 · 卖出100%持仓", "100%"),
        ):
            with self.subTest(after=after):
                trade = {
                    **sample_trades()[1], "position_before_qty": 200 + after,
                    "position_after_qty": after, "position_fully_closed": after == 0,
                }
                transport = RecordingTransport()
                results = notifications.notify_trade_executions(
                    [trade], all_channels_env(signed=False), transport=transport,
                )
                self.assertTrue(all(result.ok for result in results))
                calls = {call["channel"]: call["payload"] for call in transport.calls}
                feishu = calls["feishu"]["card"]["elements"]
                self.assertIn(f"<font color='green'>**{label}**</font>", feishu[0]["text"]["content"])
                notification = notifications._trade_notification([trade])
                self.assertIn(f"{label}｜", notification.plain_text())
                self.assertIn(f"卖出比例（占该股持仓）：{ratio}", notification.plain_text())
                self.assertIn(f"卖后持股：{after:,} 股", notification.plain_text())
                for markdown in (calls["dingtalk"]["markdown"]["text"], calls["wecom"]["markdown"]["content"]):
                    self.assertIn(label, markdown)
                    self.assertIn(f"**卖出比例（占该股持仓）**　**{ratio}**", markdown)
                    self.assertIn("**卖出仓位（占总资产）**　**2.50%**", markdown)
                self.assertIn(f"<b>卖出比例（占该股持仓）　{ratio}</b>", calls["telegram"]["text"])
                self.assertIn("已实现盈亏 / 收益率", notification.plain_text())

    def test_sell_status_uses_share_counts_and_never_rounded_equity_percent(self):
        cases = (
            ({"position_before_qty": 400}, "卖出50.00%持仓"),
            ({"position_after_qty": 200}, "卖出50.00%持仓"),
            ({"position_fully_closed": True}, "清仓 · 卖出100%持仓"),
            ({"position_before_qty": 200, "position_after_qty": 0}, "清仓 · 卖出100%持仓"),
            ({"position_after_trade_pct": 0}, "卖出｜"),
            ({"position_before_qty": 400, "position_after_qty": 0}, "卖出｜"),
            ({"position_before_qty": 100}, "卖出｜"),
            ({"position_before_qty": "nan"}, "卖出｜"),
            ({"position_after_qty": -1}, "卖出｜"),
            ({"position_after_qty": 200, "position_fully_closed": True}, "卖出｜"),
            ({"position_after_qty": 0, "position_fully_closed": False}, "卖出｜"),
            ({"shares": 200.5, "position_fully_closed": True}, "卖出｜"),
        )
        for fields, expected in cases:
            with self.subTest(fields=fields):
                text = notifications._trade_notification([{**sample_trades()[1], **fields}]).plain_text()
                self.assertIn(expected, text)
                if expected == "卖出｜":
                    self.assertNotIn("清仓", text)
                    self.assertIn("卖出比例（占该股持仓）：暂不可用", text)

    def test_partial_sell_cannot_round_to_full_exit(self):
        trade = {**sample_trades()[1], "shares": 100000,
                 "position_before_qty": 100001, "position_after_qty": 1,
                 "position_after_trade_pct": 0.0}
        text = notifications._trade_notification([trade]).plain_text()
        self.assertIn("卖出<100%持仓", text)
        self.assertIn("卖后持股：1 股", text)
        self.assertNotIn("清仓", text)

    def test_order_position_is_shown_and_highlighted(self):
        trade = sample_trades()[0]
        trade["order_position_pct"] = 12.5
        trade["position_after_trade_pct"] = 20.0
        transport = RecordingTransport()

        results = notifications.notify_trade_executions(
            [trade],
            single_channel_env("feishu"),
            transport=transport,
        )

        self.assertTrue(results[0].ok)
        fields = transport.calls[0]["payload"]["card"]["elements"][1]["fields"]
        contents = [field["text"]["content"] for field in fields]
        self.assertIn(
            "<font color='red'>**本笔成交仓位**\n12.50%</font>",
            contents,
        )
        self.assertFalse(any("成交后仓位" in content for content in contents))
        notification = notifications._trade_notification([trade])
        self.assertIn("**本笔成交仓位**　**12.50%**", notification.markdown_text())
        self.assertNotIn("成交后仓位", notification.markdown_text())

    def test_registry_can_extend_dispatch_without_changing_core(self):
        class CustomChannel:
            name = "custom_test"

            def send(self, notification, *, timeout, transport, clock):
                transport("https://custom.invalid/send", {"text": notification.plain_text()}, timeout)

        notifications.register_channel(
            "custom_test",
            lambda _env: CustomChannel(),
            enabled_env="DASHBOARD_CUSTOM_TEST_NOTIFICATION_ENABLED",
            replace=True,
        )
        calls = []

        def transport(url, payload, timeout):
            calls.append((url, payload, timeout))
            return {"ok": True}

        result = notifications.dispatch(
            notifications.Notification("test", "Title", "Body"),
            {
                notifications.GLOBAL_ENABLED_ENV: "1",
                "DASHBOARD_CUSTOM_TEST_NOTIFICATION_ENABLED": "1",
            },
            transport=transport,
        )

        self.assertEqual(result, [notifications.DeliveryResult("custom_test", True, "")])
        self.assertEqual(calls[0][0], "https://custom.invalid/send")
        self.assertEqual(calls[0][1], {"text": "Title\nBody"})


class HttpTransportTests(unittest.TestCase):
    class FakeResponse:
        status = 200

        def __init__(self, body):
            self.body = body
            self.read_limit = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=-1):
            self.read_limit = limit
            return self.body

    class FakeOpener:
        def __init__(self, response):
            self.response = response
            self.request = None
            self.timeout = None

        def open(self, request, timeout=0):
            self.request = request
            self.timeout = timeout
            return self.response

    def with_fake_opener(self, body, callback):
        response = self.FakeResponse(body)
        opener = self.FakeOpener(response)
        captured_handlers = []
        original = notifications.urllib.request.build_opener
        try:
            def fake_build_opener(*handlers):
                captured_handlers.extend(handlers)
                return opener

            notifications.urllib.request.build_opener = fake_build_opener
            return callback(opener, response, captured_handlers)
        finally:
            notifications.urllib.request.build_opener = original

    def test_post_json_sets_headers_timeout_and_disables_redirects(self):
        def scenario(opener, response, handlers):
            result = notifications._post_json(
                "https://example.invalid/hook",
                {"message": "中文"},
                6.5,
            )
            self.assertEqual(result, {"ok": True})
            self.assertEqual(opener.timeout, 6.5)
            self.assertEqual(opener.request.get_method(), "POST")
            self.assertEqual(json.loads(opener.request.data.decode()), {"message": "中文"})
            headers = dict(opener.request.header_items())
            self.assertEqual(headers["Content-type"], "application/json; charset=utf-8")
            self.assertEqual(headers["Accept"], "application/json")
            self.assertEqual(headers["User-agent"], "NiuOne/1.0")
            self.assertEqual(response.read_limit, notifications.MAX_RESPONSE_BYTES + 1)
            self.assertEqual(len(handlers), 1)
            self.assertIsInstance(handlers[0], notifications._NoRedirectHandler)
            self.assertIsNone(handlers[0].redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid"))

        self.with_fake_opener(b'{"ok":true}', scenario)

    def test_post_json_rejects_invalid_or_oversized_responses(self):
        bad_bodies = [
            b"not-json",
            b"[]",
            b"x" * (notifications.MAX_RESPONSE_BYTES + 1),
        ]
        for body in bad_bodies:
            with self.subTest(body_length=len(body)):
                def scenario(_opener, _response, _handlers):
                    with self.assertRaises(notifications.NotificationDeliveryError):
                        notifications._post_json("https://example.invalid/hook", {"ok": True}, 5)

                self.with_fake_opener(body, scenario)


if __name__ == "__main__":
    unittest.main()
