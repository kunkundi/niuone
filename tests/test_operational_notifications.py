from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "app"), str(ROOT / "app" / "compat")]
from app.messaging.operations import (  # noqa: E402
    DAILY_ENABLED_ENV, FAULT_ENABLED_ENV, build_daily_summary, notify_daily_summary, notify_operation,
)
from app.messaging.models import DeliveryResult  # noqa: E402
from app.messaging.runtime import _daily_trades, maybe_notify_daily_summary  # noqa: E402


class OperationalNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "delivery.sqlite3"
        self.env = {FAULT_ENABLED_ENV: "1", DAILY_ENABLED_ENV: "1"}
        self.deliver = Mock(return_value=[DeliveryResult("telegram", True)])

    def notify(self, ok=False, now=1000, **kwargs):
        return notify_operation("decision", "决策", ok=ok, now=now, env=self.env,
                                state_path=self.path, deliver=self.deliver, **kwargs)

    def test_opt_in_critical_retry_and_sanitized_messages(self):
        self.notify(final_attempt=False, error="timeout")
        self.deliver.assert_not_called()
        self.notify(final_attempt=False, error="HTTP Error 403: secret-private-key")
        self.assertEqual(self.deliver.call_count, 1)
        message = self.deliver.call_args.args[0].plain_text()
        self.assertIn("关键故障", message)
        self.assertNotIn("secret-private-key", message)
        self.env[FAULT_ENABLED_ENV] = "0"
        self.notify(now=2000)
        self.assertEqual(self.deliver.call_count, 1)

    def test_deduplication_boundary_and_one_recovery(self):
        self.notify()
        self.notify(now=2799)
        self.assertEqual(self.deliver.call_count, 1)
        self.notify(now=2800)
        self.assertEqual(self.deliver.call_count, 2)
        self.notify(ok=True, now=2801)
        self.notify(ok=True, now=2802)
        self.assertEqual(self.deliver.call_count, 3)
        self.assertEqual(self.deliver.call_args.args[0].event_type, "runtime.recovered")
        self.notify(now=2803)
        self.assertEqual(self.deliver.call_count, 4)

    def test_delivery_failure_and_recovery_failure_are_retryable(self):
        self.deliver.return_value = [DeliveryResult("telegram", False)]
        self.notify()
        self.notify(now=1059)
        self.assertEqual(self.deliver.call_count, 1)
        self.deliver.return_value = [DeliveryResult("telegram", True)]
        self.notify(now=1060)
        self.deliver.return_value = [DeliveryResult("telegram", False)]
        self.notify(ok=True, now=1061)
        self.deliver.return_value = [DeliveryResult("telegram", True)]
        self.notify(ok=True, now=1121)
        self.notify(ok=True, now=1181)
        self.assertEqual(self.deliver.call_count, 4)

    def test_critical_escalation_bypasses_ordinary_fault_dedupe(self):
        self.notify(error="timeout")
        self.notify(now=1001, error="PermissionError", final_attempt=False)
        self.assertEqual(self.deliver.call_count, 2)

    def test_decision_recovery_requires_persisted_evidence(self):
        from app.dashboard import server as dashboard
        payload = {"items": [{"code": "600000"}], "observed_items": []}
        with patch.object(dashboard, "normalize_b1_payload_for_trader", return_value=payload), patch.object(dashboard, "get_trader_module"), patch.object(dashboard, "record_practice_decision_event"), patch.object(dashboard, "run_practice_decision") as decision, patch("app.messaging.runtime.report_operation_safely") as notify:
            for persisted in (False, True):
                decision.return_value = {"decision": {"actions": []}, "durable_evidence_persisted": persisted}
                dashboard.run_practice_decision_logged(payload, refresh_market_summary=False)
                self.assertEqual(notify.call_args.kwargs["ok"], persisted)
            self.assertEqual(notify.call_count, 2)

    def test_parallel_callers_share_one_lease(self):
        entered, release = threading.Event(), threading.Event()
        def send(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return [DeliveryResult("telegram", True)]
        self.deliver.side_effect = send
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.notify)
            self.assertTrue(entered.wait(5))
            second = pool.submit(self.notify)
            self.assertEqual(second.result(timeout=5), [])
            release.set()
            first.result(timeout=5)
        self.assertEqual(self.deliver.call_count, 1)

    def state(self):
        return {"created_at": "2026-11-06 08:00:00", "initial_cash": 10000, "positions": {},
                "daily_equity_history": [{"time": "2026-11-06 15:15:00", "equity": 10010}],
                "trade_log": [{"time": "2026-11-06 10:00:00", "action": "BUY", "code": "600000", "shares": 100, "price": 5, "fee": 5.01}]}

    def test_daily_waits_for_real_close_and_report_then_sends_once(self):
        state = self.state()
        self.assertIsNone(build_daily_summary(state, "2026-11-05", "报告"))
        self.assertIsNone(build_daily_summary(state, "2026-11-06", ""))
        message = build_daily_summary(state, "2026-11-06", "已有A股报告")
        self.assertIn("当日盈亏 +10.00元", message.text)
        self.assertIn("费用 5.01元", message.text)
        self.assertIn("已有A股报告", message.text)
        for now in (1000, 1060, 5000):
            notify_daily_summary(state, "2026-11-06", "已有A股报告", env=self.env,
                                 state_path=self.path, now=now, deliver=self.deliver)
        self.assertEqual(self.deliver.call_count, 1)

    def test_daily_adapter_uses_beijing_day_and_no_old_report(self):
        home = Path(self.temp.name)
        state_path, report_db = home / "state.json", home / "report.db"
        state_path.write_text(json.dumps(self.state()), encoding="utf-8")
        with closing(sqlite3.connect(report_db)) as conn:
            conn.execute("CREATE TABLE dashboard_messages(source_id TEXT,time_text TEXT,timestamp REAL,content TEXT)")
            conn.execute("INSERT INTO dashboard_messages VALUES ('cron_output_67ac98149ead','2026-11-05 15:10:00',1,'旧报告')")
            conn.commit()
        env = {**self.env, "DASHBOARD_HOME": str(home), "DASHBOARD_PORTFOLIO_STATE": str(state_path), "DASHBOARD_PUSH_HISTORY_DB": str(report_db)}
        with patch("app.messaging.runtime.notify_daily_summary") as notify:
            maybe_notify_daily_summary(env, datetime(2026, 11, 6, 8, 0, tzinfo=timezone.utc))
            notify.assert_not_called()
            with closing(sqlite3.connect(report_db)) as conn:
                conn.execute("INSERT INTO dashboard_messages VALUES ('cron_output_67ac98149ead','2026-11-06 15:10:00',2,'新报告')")
                conn.commit()
            maybe_notify_daily_summary(env, datetime(2026, 11, 6, 8, 0, tzinfo=timezone.utc))
            self.assertEqual(notify.call_args.args[1:3], ("2026-11-06", "新报告"))

    def test_prefunded_account_uses_its_declared_opening_session(self):
        state = {"created_at": "2024-04-12 16:30:00", "initial_cash": 72500,
                 "performance_start_date": "2024-04-15", "daily_equity_history": [
                     {"time": "2024-04-15 15:15:00", "equity": 72512.75}]}
        report = build_daily_summary(state, "2024-04-15", "盘后报告")
        self.assertIn("当日盈亏 +12.75元", report.text)
        state.pop("performance_start_date")
        self.assertIn("基准缺失", build_daily_summary(state, "2024-04-15", "盘后报告").text)
        state["performance_start_date"] = "2024-04-15"
        state["daily_equity_history"].append({"time": "2024-04-12 15:15:00", "equity": 72490})
        self.assertIn("当日盈亏 +22.75元", build_daily_summary(state, "2024-04-15", "盘后报告").text)

    def test_daily_trade_archive_is_read_only_and_current_revision_wins(self):
        state = self.state()
        current = state["trade_log"][0]
        second = {**current, "time": "2026-11-06 11:00:00", "fee": 6.01}
        old = {**current, "time": "2026-11-06 07:00:00", "fee": 999}
        path = Path(self.temp.name) / "account.db"
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TABLE account_history(id INTEGER PRIMARY KEY, history_kind TEXT, logical_key TEXT, event_time TEXT, payload_json TEXT)")
            for key, row in (("first", {**current, "fee": 999}), ("first", {**current, "fee": 888}), ("second", second), ("old_account", old)):
                conn.execute("INSERT INTO account_history(history_kind, logical_key, event_time, payload_json) VALUES ('trade_log',?,?,?)", (key, row["time"], json.dumps(row)))
            conn.commit()
        before = path.read_bytes()
        for _ in range(2):
            rows = _daily_trades(state, path, "2026-11-06")
            self.assertEqual(rows, [current, second])
            report_state = {**state, "trade_log": rows}
            self.assertIn("费用 11.02元", build_daily_summary(report_state, "2026-11-06", "报告").text)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
