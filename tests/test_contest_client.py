import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

SRC = Path(__file__).resolve().parents[1] / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import contest_client  # noqa: E402


class ContestClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env_patch = patch.dict(os.environ, {
            "DASHBOARD_CONTEST_STATE": str(Path(self.tmp.name) / "contest_state.json"),
            "DASHBOARD_CONTEST_ENABLED": "1",
            "DASHBOARD_CONTEST_SERVER_URL": "http://contest.test",
            "DASHBOARD_CONTEST_ID": "demo",
            "DASHBOARD_CONTEST_NICKNAME": "tester",
        }, clear=False)
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.tmp.cleanup()

    def trade(self):
        return {
            "time": "2026-07-10 10:00:00",
            "action": "BUY",
            "code": "600519",
            "name": "贵州茅台",
            "shares": 100,
            "price": 100.0,
            "amount": 10000.0,
            "price_source": "test",
            "reason": "unit test",
        }

    def test_disabled_has_no_side_effect(self):
        with patch.dict(os.environ, {"DASHBOARD_CONTEST_ENABLED": "0"}, clear=False):
            item = self.trade()
            contest_client.submit_trades([item])
            self.assertNotIn("contest_status", item)

    def test_submit_trade_registers_signs_and_marks_official(self):
        calls = []

        def fake_post(path, payload):
            calls.append((path, payload))
            if path.endswith("/register"):
                return {"ok": True, "participant_id": "p1", "participant_secret": "s1"}
            self.assertTrue(path.endswith("/orders"))
            signature = payload.pop("signature")
            self.assertEqual(signature, contest_client.sign_payload("s1", payload))
            payload["signature"] = signature
            return {
                "ok": True,
                "client_order_id": payload["client_order_id"],
                "status": "official",
                "server_received_at": "2026-07-10 10:00:01",
                "official_fill_price": payload["client_fill_price"],
                "server_quote_price": payload["client_fill_price"],
                "price_diff_pct": 0,
            }

        item = self.trade()
        with patch.object(contest_client, "post_json", side_effect=fake_post):
            contest_client.submit_trades([item])
        self.assertEqual(len(calls), 2)
        self.assertEqual(item["contest_status"], "official")
        self.assertEqual(item["official_fill_price"], 100.0)
        state = contest_client.load_state()
        self.assertEqual(state["contests"]["demo"]["last_seq"], 1)

    def test_upload_failure_marks_trade_without_raising(self):
        item = self.trade()
        with patch.object(contest_client, "post_json", side_effect=RuntimeError("boom")):
            contest_client.submit_trades([item])
        self.assertEqual(item["contest_status"], "register_failed")
        self.assertIn("boom", item["contest_reject_reason"])

    def test_login_user_stores_server_token_and_fetches_contests(self):
        with patch.object(contest_client, "post_json", return_value={
            "ok": True,
            "user_token": "ut_abc",
            "user": {"username": "alice", "nickname": "Alice"},
        }) as post:
            result = contest_client.login_user("http://contest.example/", "alice", "secret123")
        self.assertTrue(result["ok"])
        self.assertEqual(post.call_args.args[0], "/api/users/login")

        state = contest_client.load_state()
        self.assertEqual(state["server_url"], "http://contest.example")
        self.assertEqual(state["user_token"], "ut_abc")
        self.assertEqual(state["user"]["nickname"], "Alice")

        with patch.object(contest_client, "get_json", return_value={
            "ok": True,
            "items": [{"contest_id": "demo", "joined": False}],
        }) as get:
            contests = contest_client.fetch_contests()
        self.assertTrue(contests["ok"])
        self.assertEqual(get.call_args.kwargs["user_token"], "ut_abc")

    def test_start_linuxdo_login_requests_authorize_url_and_remembers_server(self):
        with patch.object(contest_client, "post_json", return_value={
            "ok": True,
            "provider": "linuxdo",
            "auth_url": "https://connect.linux.do/oauth2/authorize?state=abc",
        }) as post:
            result = contest_client.start_linuxdo_login(
                "http://contest.example/",
                "http://127.0.0.1:8787/api/contest/linuxdo/complete",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["auth_url"], "https://connect.linux.do/oauth2/authorize?state=abc")
        self.assertEqual(post.call_args.args[0], "/api/auth/linuxdo/start")
        self.assertEqual(post.call_args.args[1]["client_callback"], "http://127.0.0.1:8787/api/contest/linuxdo/complete")
        self.assertEqual(post.call_args.kwargs["server_url"], "http://contest.example")
        self.assertEqual(contest_client.load_state()["server_url"], "http://contest.example")

    def test_complete_linuxdo_login_exchanges_ticket_and_stores_user_token(self):
        with patch.object(contest_client, "post_json", return_value={
            "ok": True,
            "provider": "linuxdo",
            "user_token": "ut_linuxdo",
            "user": {"username": "linuxdo_42", "nickname": "Alice LinuxDo"},
        }) as post:
            result = contest_client.complete_linuxdo_login("http://contest.example/", "lt_ticket")

        self.assertTrue(result["ok"])
        self.assertEqual(post.call_args.args[0], "/api/auth/linuxdo/ticket")
        self.assertEqual(post.call_args.args[1], {"ticket": "lt_ticket"})
        self.assertEqual(post.call_args.kwargs["server_url"], "http://contest.example")
        state = contest_client.load_state()
        self.assertEqual(state["server_url"], "http://contest.example")
        self.assertEqual(state["user_token"], "ut_linuxdo")
        self.assertEqual(state["user"]["nickname"], "Alice LinuxDo")

    def test_join_contest_stores_active_credentials_and_enables_reporting_from_state(self):
        state = contest_client.load_state()
        state.update({"server_url": "http://contest.test", "user_token": "ut_abc"})
        contest_client.save_state(state)
        with patch.dict(os.environ, {
            "DASHBOARD_CONTEST_ENABLED": "",
            "DASHBOARD_CONTEST_ID": "",
            "DASHBOARD_CONTEST_PARTICIPANT_ID": "",
            "DASHBOARD_CONTEST_SECRET": "",
        }, clear=False):
            with patch.object(contest_client, "post_json", return_value={
                "ok": True,
                "contest_id": "demo",
                "participant_id": "p1",
                "participant_secret": "s1",
                "nickname": "Alice",
            }) as post:
                result = contest_client.join_contest("demo")
            self.assertTrue(result["ok"])
            self.assertEqual(post.call_args.args[0], "/api/contests/demo/join")
            self.assertEqual(post.call_args.kwargs["user_token"], "ut_abc")
            self.assertTrue(contest_client.is_enabled())
            self.assertEqual(contest_client.contest_id(), "demo")
            joined = contest_client.load_state()["contests"]["demo"]
            self.assertEqual(joined["participant_id"], "p1")
            self.assertEqual(joined["participant_secret"], "s1")

    def test_parse_and_fetch_contest_sse_events(self):
        raw = (
            "id: 7\n"
            "event: contest\n"
            "data: {\"type\":\"contest_upserted\",\"contest_id\":\"demo\"}\n\n"
            ": heartbeat\n\n"
        )
        events = contest_client.parse_sse_events(raw)
        self.assertEqual(events[0]["id"], 7)
        self.assertEqual(events[0]["event"], "contest")
        self.assertEqual(events[0]["data"]["contest_id"], "demo")

        state = contest_client.load_state()
        state.update({"server_url": "http://contest.test", "user_token": "ut_abc", "last_event_id": 6})
        contest_client.save_state(state)
        with patch.object(contest_client, "get_text", return_value=raw) as get_text:
            result = contest_client.fetch_contest_events()
        self.assertTrue(result["ok"])
        self.assertEqual(result["events"][0]["id"], 7)
        self.assertEqual(contest_client.load_state()["last_event_id"], 7)
        self.assertEqual(get_text.call_args.args[0], "/api/contests/events?since=6")
        self.assertEqual(get_text.call_args.kwargs["user_token"], "ut_abc")


if __name__ == "__main__":
    unittest.main()
