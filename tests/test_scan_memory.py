"""Lossless, disk-backed scan transport and completed-result retention."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "compat"))
import niuone_dashboard as dashboard
import multi_strategy_screen as scanner


class ScanMemoryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="niuone-scan-memory-")))
        for name, value in {
            "B1_CACHE_FILE": self.root / "b1_screen_latest.json",
            "CRON_STATE_DIR": self.root / "state",
            "PRACTICE_CANDIDATES_CACHE_FILE": self.root / "practice_candidates_latest.json",
            "B1_FULL_SCAN_LAST_RESULT": {},
            "B1_FULL_SCAN_ACTIVE": {},
            "B1_FULL_SCAN_CONDITION": threading.Condition(),
            "_B1_GENERATION_CACHE": {},
        }.items():
            self.stack.enter_context(patch.object(dashboard, name, value))
        self.stack.enter_context(patch.dict(os.environ, {
            "DASHBOARD_B1_PROGRESS_FILE": str(self.root / "state/progress.json"),
            "DASHBOARD_B1_SCANNER": str(ROOT / "app/entrypoints/multi_strategy_screen.py"),
        }))

    def test_scan_stdout_is_disk_backed_and_preserves_all_context(self):
        payload = {"items": [], "trade_items": [], "generated_at": "2026-11-05 10:00:00",
                   "niuone_context": {"precise": 0.12345678901234567, "text": "完整题材"}}
        def runner(args, **kwargs):
            self.assertNotIn("capture_output", kwargs)
            self.assertNotEqual(kwargs["stdout"], subprocess.PIPE)
            self.assertEqual(kwargs["env"]["PYTHONIOENCODING"], "utf-8")
            json.dump(payload, kwargs["stdout"], ensure_ascii=False)
            kwargs["stdout"].flush()
            return subprocess.CompletedProcess(args, 0, stdout=None, stderr="")
        with patch.object(dashboard.subprocess, "run", side_effect=runner):
            result = dashboard._trigger_b1_scan_unlocked(decision_mode="none")
        self.assertFalse(result.get("error"), result.get("error"))
        self.assertEqual(result["niuone_context"], payload["niuone_context"])

    def test_completed_result_is_not_retained_in_process_memory(self):
        result = {"count": 1, "niuone_context": {"all_rows": [{"value": 1.123456789}] * 100}}
        dashboard._finish_b1_full_scan("job-one", result)
        self.assertIsNone(dashboard.B1_FULL_SCAN_LAST_RESULT["result"])
        joined = dashboard.wait_for_b1_scan_result({"active_job_id": "job-one"}, timeout_seconds=1)
        self.assertEqual(joined["niuone_context"], result["niuone_context"])
        self.assertEqual(joined["job_id"], "job-one")
        self.assertNotIn("job_id", result)

    def test_missing_or_mismatched_join_snapshot_fails_closed(self):
        for corrupt in (None, {"job_id": "different-job", "count": 10}):
            dashboard._finish_b1_full_scan("job-one", {"count": 1})
            path = dashboard.B1_CACHE_FILE.with_name("b1_scan_join_result.json")
            if corrupt is None:
                path.unlink()
            else:
                path.write_text(json.dumps(corrupt), encoding="utf-8")
            with self.assertRaises(dashboard.PracticeCycleError):
                dashboard.wait_for_b1_scan_result({"active_job_id": "job-one"}, timeout_seconds=1)

    def test_scan_snapshots_stream_once_and_remain_identical(self):
        fields = {
            "B1_OUTPUT_DIR": self.root, "B1_CACHE_FILE": self.root / "b1.json",
            "MULTI_STRATEGY_CACHE": self.root / "multi.json",
            "MULTI_STRATEGY_HISTORY": self.root / "history",
            "B1_HISTORY_DIR": self.root / "legacy",
            "PRACTICE_CANDIDATES_CACHE": self.root / "candidates.json",
        }
        for name, value in fields.items():
            self.stack.enter_context(patch.object(scanner, name, value))
        payload = {"generated_at": "2026-11-05 10:00:00", "items": [],
                   "context": {"price": 0.12345678901234567, "label": "不丢精度"}}
        original = json.dumps
        def guard(value, *args, **kwargs):
            if value is payload:
                raise AssertionError("full snapshot serialized in memory")
            return original(value, *args, **kwargs)
        with patch.object(scanner.json, "dumps", side_effect=guard):
            scanner.write_outputs(payload, payload["generated_at"])
        targets = [fields["B1_CACHE_FILE"], fields["MULTI_STRATEGY_CACHE"],
                   self.root / "history/2026-11-05/2026-11-05_10-00-00.json"]
        self.assertEqual(len({path.read_bytes() for path in targets}), 1)
        self.assertEqual(json.loads(targets[0].read_text(encoding="utf-8")), payload)
        self.assertEqual(list(self.root.rglob("*.tmp")), [])

    def test_generation_polling_does_not_reparse_full_context(self):
        path = dashboard.B1_CACHE_FILE
        dashboard.write_json_cache(path, {"generated_at": "2026-11-05 10:00:00", "context": [1] * 100})
        with patch.object(dashboard, "read_json_cache", wraps=dashboard.read_json_cache) as read:
            for _ in range(3):
                self.assertTrue(dashboard.b1_cache_generated_for_slot("2026-11-05 10:00"))
            self.assertEqual(read.call_count, 1)
            dashboard.write_json_cache(path, {"generated_at": "2026-11-05 10:30:00"})
            self.assertTrue(dashboard.b1_cache_generated_for_slot("2026-11-05 10:30"))
            self.assertEqual(read.call_count, 2)

    def test_generation_uses_verified_compact_snapshot(self):
        payload = {"generated_at": "2026-11-05 10:00:00", "items": []}
        dashboard.write_json_cache(dashboard.B1_CACHE_FILE, payload)
        dashboard.write_practice_candidates_cache(
            dashboard.PRACTICE_CANDIDATES_CACHE_FILE, payload, source_path=dashboard.B1_CACHE_FILE,
        )
        with patch.object(dashboard, "read_json_cache", side_effect=AssertionError("full scan read")):
            self.assertTrue(dashboard.b1_cache_generated_for_slot("2026-11-05 10:00"))


if __name__ == "__main__":
    unittest.main()
