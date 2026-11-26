"""Regression coverage for Cron dispatch independently of market-report latency."""
from datetime import datetime, timedelta
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "app"), str(ROOT / "app/compat")]
import niuone_cron_scheduler as s


class CronDispatchTests(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.enterContext(patch.object(s, "save_state"))
        self.enterContext(patch.object(s, "log"))
        self.at = datetime(2025, 2, 7, 15, 10, tzinfo=s.CN_TZ)
        self.d = s._CronDispatch(self.state)
        self.addCleanup(self.d.close)
        self.report = next(j for j in s.JOBS if j.env_name == "DASHBOARD_MARKET_CLOSE_CRON")
        self.snapshot = s.NIUONE_EQUITY_SNAPSHOT_JOB
        self.eval = next(j for j in s.JOBS if j.env_name == "DASHBOARD_NIUONE_FORWARD_CRON")
        self.exit_job = next(j for j in s.JOBS if j.env_name == "DASHBOARD_TIME_EXIT_TIME")

    def test_report_can_span_both_account_ticks_without_losing_or_reordering_evidence(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def execute(job, at):
            calls.append(job.env_name)
            if job == self.report:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test report timed out")
            if job == self.eval:
                day = self.state["job_history"][self.at.date().isoformat()]
                self.assertTrue(day[self.snapshot.env_name][-1]["success"])
            return s.JobRunResult(True, "ok", 0, 0)
        with patch.object(s, "run_job", side_effect=execute):
            try:
                self.d.claim(self.report, self.at)
                self.d.start_ready(self.at)
                self.assertTrue(entered.wait(3))
                for minute, job in ((5, self.snapshot), (10, self.eval)):
                    now = self.at + timedelta(minutes=minute)
                    self.d.collect()
                    self.d.claim(job, now)
                    self.d.claim(job, now)
                    self.d.start_ready(now)
                    self.d.running[True][2].result(timeout=3)
                self.d.collect()
            finally:
                release.set()
                self.d.running[False][2].result(timeout=3)
        self.d.collect()
        self.assertEqual(calls, [self.report.env_name, self.snapshot.env_name, self.eval.env_name])
        self.assertTrue(self.state["job_history"][self.at.date().isoformat()][self.eval.env_name][-1]["success"])
        restored = s._CronDispatch(self.state)
        try:
            restored.claim(self.report, self.at)
            self.assertFalse(restored.waiting[False])
        finally:
            restored.close()

    def test_expired_exit_and_overlapping_report_get_explicit_failed_outcomes(self):
        with patch.object(s, "run_job", return_value=s.JobRunResult(True, "ok", 0, 0)) as run:
            self.d.claim(self.exit_job, self.at)
            self.d.start_ready(self.at + timedelta(minutes=1))
            run.assert_not_called()
            self.d.claim(self.report, self.at)
            self.d.claim(self.report, self.at + timedelta(minutes=1))
        day = self.state["job_history"][self.at.date().isoformat()]
        self.assertEqual(day[self.exit_job.env_name][-1]["status"], "missed execution window")
        self.assertEqual(day[self.report.env_name][-1]["status"], "overlapping job")
        self.assertEqual(len(self.d.waiting[False]), 1)

    def test_worker_error_is_recorded_and_the_next_job_can_run(self):
        with patch.object(s, "run_job", side_effect=[RuntimeError("injected"), s.JobRunResult(True, "ok", 0, 0)]):
            for i, job in enumerate((self.snapshot, self.eval)):
                now = self.at + timedelta(minutes=i)
                self.d.claim(job, now)
                self.d.start_ready(now)
                self.d.running[True][2].result(timeout=3)
                self.d.collect()
        day = self.state["job_history"][self.at.date().isoformat()]
        self.assertEqual(day[self.snapshot.env_name][-1]["status"], "worker failed")
        self.assertTrue(day[self.eval.env_name][-1]["success"])
