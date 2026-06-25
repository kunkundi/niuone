#!/usr/bin/env python3
import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"


def load_scheduler_module():
    module_name = "niuone_cron_scheduler_under_test"
    sys.path.insert(0, str(SRC))
    spec = importlib.util.spec_from_file_location(module_name, SRC / "niuone_cron_scheduler.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class NiuoneCronSchedulerTests(unittest.TestCase):
    def test_archive_only_job_retries_until_archive_marker(self):
        scheduler = load_scheduler_module()
        job = scheduler.Job(
            "TEST_CRON",
            "0 11 * * *",
            "test-job",
            "Test Job",
            ("does_not_run.py", "--archive-only"),
            5,
            False,
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

            def fake_run(*_args, **_kwargs):
                calls.append(True)
                if len(calls) == 1:
                    return SimpleNamespace(returncode=1, stdout="", stderr="upstream timeout")
                return SimpleNamespace(returncode=0, stdout="", stderr="archived: /tmp/report.md")

            scheduler.subprocess.run = fake_run
            result = scheduler.run_job(job, datetime(2026, 6, 25, 11, 0, tzinfo=scheduler.CN_TZ))
        finally:
            scheduler.subprocess.run = old_run
            scheduler.time.sleep = old_sleep
            scheduler.parse_env_file = old_parse_env
            scheduler.log = old_log
            scheduler.STOP = False

        self.assertTrue(result.success)
        self.assertEqual(result.archive_path, "/tmp/report.md")
        self.assertEqual(len(calls), 2)
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


if __name__ == "__main__":
    unittest.main()
