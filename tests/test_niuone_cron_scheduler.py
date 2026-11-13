#!/usr/bin/env python3
import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
    def test_report_timeout_respects_model_deadline_with_bounded_headroom(self):
        scheduler = load_scheduler_module()
        report = next(job for job in scheduler.JOBS if job.env_name == "DASHBOARD_MARKET_CLOSE_CRON")
        settings = {"A_SHARE_MODEL_SUMMARY_DEADLINE_SECONDS": "300"}
        self.assertEqual(scheduler.job_timeout_seconds(report, {}), 240)
        self.assertEqual(scheduler.job_timeout_seconds(report, settings), 480)
        self.assertEqual(scheduler.job_timeout_seconds(report, {
            **settings, "A_SHARE_MODEL_SUMMARY_ENABLED": "0",
        }), 180)
        self.assertEqual(scheduler.job_timeout_seconds(report, {
            "A_SHARE_MODEL_SUMMARY_DEADLINE_SECONDS": "999999",
        }), 1800)
        exit_job = next(job for job in scheduler.JOBS if job.env_name == "DASHBOARD_B3_EXIT_TIME")
        self.assertEqual(scheduler.job_timeout_seconds(exit_job, settings), exit_job.timeout_seconds)
        with (
            patch.object(scheduler, "parse_env_file", return_value=settings),
            patch.object(scheduler, "log"),
            patch.object(scheduler.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as run,
        ):
            result = scheduler.run_job_once(report, datetime(2026, 11, 5, 15, 10, tzinfo=scheduler.CN_TZ))
        self.assertTrue(result.success)
        self.assertEqual(run.call_args.kwargs["timeout"], 480)

    def test_scheduler_log_rotates_at_a_bounded_size(self):
        scheduler = load_scheduler_module()
        original_values = (
            scheduler.LOG_DIR,
            scheduler.LOG_PATH,
            scheduler.LOG_MAX_BYTES,
            scheduler.LOG_BACKUP_COUNT,
        )
        try:
            with tempfile.TemporaryDirectory(
                prefix="niuone-scheduler-log-"
            ) as directory:
                scheduler.LOG_DIR = Path(directory)
                scheduler.LOG_PATH = scheduler.LOG_DIR / "scheduler.log"
                scheduler.LOG_MAX_BYTES = 96
                scheduler.LOG_BACKUP_COUNT = 2
                for index in range(12):
                    scheduler.log(f"bounded scheduler message {index}")

                self.assertTrue(scheduler.LOG_PATH.is_file())
                self.assertTrue(Path(f"{scheduler.LOG_PATH}.1").is_file())
                self.assertTrue(Path(f"{scheduler.LOG_PATH}.2").is_file())
                self.assertFalse(Path(f"{scheduler.LOG_PATH}.3").exists())
                self.assertLessEqual(
                    sum(path.stat().st_size for path in scheduler.LOG_DIR.iterdir()),
                    scheduler.LOG_MAX_BYTES * (scheduler.LOG_BACKUP_COUNT + 1),
                )
        finally:
            (
                scheduler.LOG_DIR,
                scheduler.LOG_PATH,
                scheduler.LOG_MAX_BYTES,
                scheduler.LOG_BACKUP_COUNT,
            ) = original_values

    def test_scheduler_state_is_atomic_and_corruption_fails_closed(self):
        scheduler = load_scheduler_module()
        original_path = scheduler.STATE_PATH
        original_log = scheduler.log
        logs = []
        try:
            with tempfile.TemporaryDirectory(
                prefix="niuone-scheduler-state-"
            ) as directory:
                scheduler.STATE_PATH = Path(directory) / "scheduler.json"
                scheduler.log = logs.append
                expected = {
                    "run_keys": ["test:202608030905"],
                    "job_history": {"2026-08-03": {}},
                }
                scheduler.save_state(expected)
                loaded = scheduler.load_state()
                temporary_files = list(Path(directory).glob(".*.tmp"))
                scheduler.STATE_PATH.write_text("{invalid", encoding="utf-8")
                corrupted = scheduler.load_state()
        finally:
            scheduler.STATE_PATH = original_path
            scheduler.log = original_log

        self.assertEqual(loaded, expected)
        self.assertEqual(temporary_files, [])
        self.assertEqual(corrupted, {"run_keys": []})
        self.assertTrue(any("fail-closed history" in item for item in logs))

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
                calls.append({
                    'run_key': kwargs['env'].get('NIUONE_CRON_RUN_KEY'),
                    'job_env': kwargs['env'].get('NIUONE_CRON_JOB_ENV'),
                    'scheduled_at': kwargs['env'].get('NIUONE_CRON_SCHEDULED_AT'),
                })
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
        self.assertEqual(
            [item['run_key'] for item in calls],
            ['test-job:202606251100', 'test-job:202606251100'],
        )
        self.assertEqual({item['job_env'] for item in calls}, {'TEST_CRON'})
        self.assertEqual(
            {item['scheduled_at'] for item in calls},
            {'2026-06-25T11:00:00+08:00'},
        )
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

    def test_us_rating_job_is_not_registered(self):
        scheduler = load_scheduler_module()
        self.assertNotIn("DASHBOARD_US_RATING_CRON", {job.env_name for job in scheduler.JOBS})
        self.assertFalse(hasattr(scheduler, "us_features_enabled"))

    def test_us_market_summary_runs_at_8_on_weekdays(self):
        scheduler = load_scheduler_module()
        job = next(job for job in scheduler.JOBS if job.env_name == "DASHBOARD_US_MARKET_SUMMARY_CRON")

        self.assertEqual(job.default_expr, "0 8 * * 1-5")
        self.assertEqual(job.command, ("us_market_summary.py", "--store"))
        self.assertEqual(scheduler.normalize_job_expr(job, "08:00"), "0 8 * * 1-5")
        self.assertTrue(scheduler.job_enabled(job, {}))
        self.assertFalse(scheduler.job_enabled(job, {"DASHBOARD_US_MARKET_SUMMARY_ENABLED": "0"}))
        self.assertTrue(scheduler.job_enabled(job, {"DASHBOARD_US_MARKET_SUMMARY_ENABLED": "1"}))

    def test_iwencai_dragon_tiger_runs_at_18_on_weekdays_when_enabled(self):
        scheduler = load_scheduler_module()
        job = next(job for job in scheduler.JOBS if job.env_name == "IWENCAI_DRAGON_TIGER_CRON")
        original_enabled = scheduler.os.environ.get("IWENCAI_ENABLED")
        try:
            scheduler.os.environ["IWENCAI_ENABLED"] = "1"
            self.assertEqual(job.default_expr, "0 18 * * 1-5")
            self.assertEqual(job.command, ("iwencai_dragon_tiger_snapshot.py",))
            self.assertEqual(scheduler.normalize_job_expr(job, "18:00"), "0 18 * * 1-5")
            self.assertFalse(scheduler.job_enabled(job, {}))
            self.assertTrue(scheduler.job_enabled(job, {"IWENCAI_ENABLED": "1"}))
        finally:
            if original_enabled is None:
                scheduler.os.environ.pop("IWENCAI_ENABLED", None)
            else:
                scheduler.os.environ["IWENCAI_ENABLED"] = original_enabled

    def test_iwencai_startup_catch_up_runs_only_when_source_is_enabled(self):
        scheduler = load_scheduler_module()
        job = scheduler.IWENCAI_STARTUP_CATCH_UP_JOB
        calls = []
        original_run_job = scheduler.run_job
        try:
            scheduler.run_job = lambda selected, run_time: calls.append(
                (selected, run_time)
            ) or "done"
            now = datetime(2026, 7, 26, 18, 10, tzinfo=scheduler.CN_TZ)
            disabled = scheduler.run_startup_catch_up(now, {})
            enabled = scheduler.run_startup_catch_up(now, {"IWENCAI_ENABLED": "1"})
        finally:
            scheduler.run_job = original_run_job

        self.assertIsNone(disabled)
        self.assertEqual(enabled, "done")
        self.assertEqual(job.command, ("iwencai_dragon_tiger_snapshot.py", "--catch-up"))
        self.assertEqual(calls, [(job, now)])

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

    def test_niuone_forward_evaluation_runs_after_market_close(self):
        scheduler = load_scheduler_module()
        snapshot_job = scheduler.NIUONE_EQUITY_SNAPSHOT_JOB
        job = next(
            job
            for job in scheduler.JOBS
            if job.env_name == "DASHBOARD_NIUONE_FORWARD_CRON"
        )

        self.assertIn(snapshot_job, scheduler.JOBS)
        self.assertEqual(snapshot_job.default_expr, "15 15 * * 1-5")
        self.assertEqual(
            snapshot_job.command,
            ("niuniu_practice_trader.py", "--snapshot-equity"),
        )
        self.assertEqual(job.default_expr, "20 15 * * 1-5")
        self.assertEqual(
            job.command,
            ("evaluate_niuone_forward.py", "--runtime"),
        )
        self.assertEqual(
            scheduler.normalize_job_expr(job, "15:25"),
            "25 15 * * 1-5",
        )
        self.assertTrue(scheduler.job_enabled(job, {}))

    def test_niuone_protocol_preflight_runs_before_open_and_at_startup(self):
        scheduler = load_scheduler_module()
        job = scheduler.NIUONE_FORWARD_PROTOCOL_PREFLIGHT_JOB
        calls = []
        original_run_job = scheduler.run_job
        try:
            scheduler.run_job = lambda selected, run_time: calls.append(
                (selected, run_time)
            ) or "done"
            now = datetime(2026, 8, 3, 8, 42, tzinfo=scheduler.CN_TZ)
            result = scheduler.run_startup_protocol_preflight(now)
        finally:
            scheduler.run_job = original_run_job

        self.assertEqual(job.default_expr, "5 9 * * 1-5")
        self.assertEqual(
            job.command,
            (
                "evaluate_niuone_forward.py",
                "--runtime",
                "--protocol-only",
            ),
        )
        self.assertEqual(result, "done")
        self.assertEqual(calls, [(job, now)])
        self.assertIn(job, scheduler.JOBS)
        self.assertTrue(scheduler.job_enabled(job, {}))
        self.assertEqual(scheduler.retry_settings(job, {}), (1, 0))

    def test_scheduler_retains_bounded_daily_job_outcomes(self):
        scheduler = load_scheduler_module()
        job = scheduler.NIUONE_FORWARD_PROTOCOL_PREFLIGHT_JOB
        state = {"job_history": {}}
        result = scheduler.JobRunResult(
            success=True,
            status="ok",
            exit_code=0,
            elapsed=0.25,
        )
        scheduled = datetime(2026, 8, 3, 9, 5, tzinfo=scheduler.CN_TZ)
        completed = datetime(2026, 8, 3, 9, 5, 1, tzinfo=scheduler.CN_TZ)

        first_day = datetime(2025, 1, 1, 9, 5, tzinfo=scheduler.CN_TZ)
        for offset in range(scheduler.JOB_HISTORY_RETENTION_DAYS + 5):
            run_at = first_day + timedelta(days=offset)
            scheduler.record_job_result(
                state,
                job,
                run_at,
                result,
                completed_at=run_at + timedelta(seconds=1),
            )
        for _index in range(12):
            scheduler.record_job_result(
                state,
                job,
                scheduled,
                result,
                completed_at=completed,
            )

        history = state["job_history"]
        self.assertEqual(len(history), scheduler.JOB_HISTORY_RETENTION_DAYS)
        self.assertNotIn("2025-01-01", history)
        runs = history["2026-08-03"][job.env_name]
        self.assertEqual(len(runs), scheduler.JOB_HISTORY_RUNS_PER_JOB)
        self.assertTrue(runs[-1]["success"])
        self.assertEqual(runs[-1]["exit_code"], 0)
        self.assertEqual(runs[-1]["completed_at"], completed.isoformat())


if __name__ == "__main__":
    unittest.main()
