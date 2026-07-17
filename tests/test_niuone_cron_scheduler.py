#!/usr/bin/env python3
import importlib.util
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
COMPAT = SRC / "compat"
ENTRYPOINTS = SRC / "entrypoints"


def load_scheduler_module():
    module_name = "niuone_cron_scheduler_under_test"
    sys.path.insert(0, str(SRC))
    sys.path.insert(0, str(COMPAT))
    spec = importlib.util.spec_from_file_location(module_name, COMPAT / "niuone_cron_scheduler.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class NiuoneCronSchedulerTests(unittest.TestCase):
    def test_database_only_job_retries_until_process_succeeds(self):
        scheduler = load_scheduler_module()
        job = scheduler.Job(
            "TEST_CRON",
            "0 11 * * *",
            "test-job",
            "Test Job",
            ("does_not_run.py", "--store-only"),
            5,
        )
        calls = []
        logs = []
        old_run = scheduler.subprocess.run
        old_sleep = scheduler.time.sleep
        old_parse_env = scheduler.parse_env_file
        old_log = scheduler.log
        try:
            scheduler.STOP = False
            scheduler.parse_env_file = lambda: {
                "DASHBOARD_CRON_MAX_ATTEMPTS": "2",
                "DASHBOARD_CRON_RETRY_DELAY_SECONDS": "0",
            }
            scheduler.log = lambda message: logs.append(message)
            scheduler.time.sleep = lambda _seconds: None

            def fake_run(*_args, **kwargs):
                calls.append(kwargs['env'].get('NIUONE_CRON_RUN_KEY'))
                if len(calls) == 1:
                    return SimpleNamespace(returncode=1, stdout="", stderr="upstream timeout")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            scheduler.subprocess.run = fake_run
            result = scheduler.run_job(job, datetime(2026, 6, 25, 11, 0, tzinfo=scheduler.CN_TZ))
        finally:
            scheduler.subprocess.run = old_run
            scheduler.time.sleep = old_sleep
            scheduler.parse_env_file = old_parse_env
            scheduler.log = old_log
            scheduler.STOP = False

        self.assertTrue(result.success)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls, ['test-job:202606251100', 'test-job:202606251100'])
        self.assertFalse(hasattr(scheduler, 'archive_job_output'))
        self.assertTrue(any("retry scheduled job=test-job" in item for item in logs))

    def test_retry_settings_clamp_invalid_values(self):
        scheduler = load_scheduler_module()
        old_log = scheduler.log
        try:
            scheduler.log = lambda _message: None
            attempts, delay = scheduler.retry_settings(
                scheduler.JOBS[-1],
                {"DASHBOARD_CRON_MAX_ATTEMPTS": "bad", "DASHBOARD_CRON_RETRY_DELAY_SECONDS": "-10"},
            )
        finally:
            scheduler.log = old_log
        self.assertEqual(attempts, 2)
        self.assertEqual(delay, 0)

    def test_us_feature_gate_controls_us_rating_job(self):
        scheduler = load_scheduler_module()
        us_job = next(job for job in scheduler.JOBS if job.env_name == "DASHBOARD_US_RATING_CRON")
        cn_job = next(job for job in scheduler.JOBS if job.env_name == "DASHBOARD_MARKET_AUCTION_CRON")

        self.assertFalse(scheduler.us_features_enabled({}))
        self.assertFalse(scheduler.job_enabled(us_job, {}))
        self.assertTrue(scheduler.job_enabled(us_job, {"DASHBOARD_US_FEATURES_ENABLED": "1"}))
        self.assertTrue(scheduler.job_enabled(us_job, {"DASHBOARD_US_FEATURES_ENABLED": "true"}))
        self.assertTrue(scheduler.job_enabled(cn_job, {}))
        self.assertEqual(us_job.command, ("us_rating_report.py", "--store-only"))

    def test_us_market_summary_runs_at_8_on_weekdays(self):
        scheduler = load_scheduler_module()
        job = next(job for job in scheduler.JOBS if job.env_name == "DASHBOARD_US_MARKET_SUMMARY_CRON")

        self.assertEqual(job.default_expr, "0 8 * * 1-5")
        self.assertEqual(job.command, ("us_market_summary.py", "--store"))
        self.assertEqual(scheduler.normalize_job_expr(job, "08:00"), "0 8 * * 1-5")
        self.assertTrue(scheduler.job_enabled(job, {}))

    def test_iwencai_dragon_tiger_runs_at_18_on_weekdays_when_enabled(self):
        scheduler = load_scheduler_module()
        job = next(job for job in scheduler.JOBS if job.env_name == "IWENCAI_DRAGON_TIGER_CRON")

        self.assertEqual(job.default_expr, "0 18 * * 1-5")
        self.assertEqual(job.command, ("iwencai_dragon_tiger_snapshot.py",))
        self.assertEqual(scheduler.normalize_job_expr(job, "18:00"), "0 18 * * 1-5")
        self.assertFalse(scheduler.job_enabled(job, {}))
        self.assertTrue(scheduler.job_enabled(job, {"IWENCAI_ENABLED": "1"}))

    def test_time_exit_job_uses_hhmm_setting(self):
        scheduler = load_scheduler_module()
        b3_job = next(job for job in scheduler.JOBS if job.env_name == "DASHBOARD_B3_EXIT_TIME")
        job = next(job for job in scheduler.JOBS if job.env_name == "DASHBOARD_TIME_EXIT_TIME")

        original_env_values = {
            "DASHBOARD_TIME_EXIT_TIME": os.environ.get("DASHBOARD_TIME_EXIT_TIME"),
            "DASHBOARD_TIME_STOP_EXIT_TIME": os.environ.get("DASHBOARD_TIME_STOP_EXIT_TIME"),
        }
        try:
            for name in original_env_values:
                os.environ.pop(name, None)
            self.assertEqual(b3_job.command, ("niuniu_practice_trader.py", "--auto-exits"))
            self.assertEqual(b3_job.default_expr, "37 9 * * 1-5")
            self.assertEqual(scheduler.normalize_job_expr(b3_job, "09:37"), "37 9 * * 1-5")
            self.assertEqual(job.command, ("niuniu_practice_trader.py", "--auto-exits"))
            self.assertEqual(scheduler.normalize_job_expr(job, "14:45"), "45 14 * * 1-5")
            self.assertEqual(
                scheduler.job_expr_value(job, {"DASHBOARD_TIME_STOP_EXIT_TIME": "14:46"}),
                "14:46",
            )
        finally:
            for name, value in original_env_values.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
