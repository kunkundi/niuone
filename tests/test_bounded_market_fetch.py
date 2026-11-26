import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app/compat"))
from core.process_lease import FileLease
from dashboard.apis import bounded_fetch, hot_stocks_service


class BoundedMarketFetchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="niuone-bounded-market-")
        self.addCleanup(temp.cleanup)
        self.cache = Path(temp.name) / "snapshot.json"

    def test_worker_preserves_rows_and_uses_a_hard_timeout(self):
        payload = [{"name": "完整行业", "price": 12.123456789012345}]
        result = subprocess.CompletedProcess([], 0, json.dumps(payload, ensure_ascii=False), "")
        with patch.object(bounded_fetch.subprocess, "run", return_value=result) as run:
            self.assertEqual(bounded_fetch.run_bounded_market_fetch("sector_rows", cache_path=self.cache), payload)
        self.assertEqual(run.call_args.kwargs["timeout"], 20)
        self.assertEqual(run.call_args.kwargs["env"]["PYTHONIOENCODING"], "utf-8")
        self.assertFalse(self.cache.with_suffix(".fetch.lock").exists())

    def test_timed_out_worker_releases_lease_and_can_be_retried(self):
        with patch.object(bounded_fetch.subprocess, "run", side_effect=[
            subprocess.TimeoutExpired("test", 45), subprocess.CompletedProcess([], 0, '{"items":[]}', ""),
        ]):
            with self.assertRaisesRegex(TimeoutError, "45s"):
                bounded_fetch.run_bounded_market_fetch("hot_stocks", cache_path=self.cache)
            self.assertEqual(bounded_fetch.run_bounded_market_fetch("hot_stocks", cache_path=self.cache), {"items": []})

    def test_concurrent_request_does_not_spawn_another_worker(self):
        with FileLease(self.cache.with_suffix(".fetch.lock"), stale_after_seconds=55), patch.object(
            bounded_fetch.subprocess, "run",
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "already running"):
                bounded_fetch.run_bounded_market_fetch("hot_stocks", cache_path=self.cache)
            run.assert_not_called()

    def test_timeout_preserves_old_hot_stock_snapshot(self):
        old = {"items": [{"name": "旧榜单", "amount_wan": 100}], "amount_top": [{"name": "旧榜单", "amount_wan": 100}]}
        self.cache.write_text(json.dumps(old), encoding="utf-8")
        with patch.object(hot_stocks_service, "CACHE_PATH", self.cache), patch.object(
            hot_stocks_service, "CACHE_TTL", 0,
        ), patch.object(hot_stocks_service, "run_bounded_market_fetch", side_effect=TimeoutError("bounded timeout")):
            result = hot_stocks_service.fetch_hot_stocks()
        self.assertTrue(result["stale_cache"])
        self.assertEqual(result["items"], old["items"])
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8")), old)

    def test_empty_quotes_do_not_overwrite_a_valid_cache(self):
        with patch.object(hot_stocks_service, "_load_universe", return_value=["600000"]), patch.object(
            hot_stocks_service, "_fetch_quotes", return_value=[],
        ):
            with self.assertRaisesRegex(RuntimeError, "no usable rows"):
                hot_stocks_service._compute_in_process()

    def test_malformed_worker_payload_releases_lease(self):
        with patch.object(bounded_fetch.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{}', '')):
            with self.assertRaisesRegex(ValueError, "invalid payload"):
                bounded_fetch.run_bounded_market_fetch("sector_rows", cache_path=self.cache)
        self.assertFalse(self.cache.with_suffix(".fetch.lock").exists())


if __name__ == "__main__":
    unittest.main()
