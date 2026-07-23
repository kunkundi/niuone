#!/usr/bin/env python3
"""Regression tests for rolling iWencai dragon-tiger snapshot retention."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
COMPAT = APP / "compat"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(COMPAT))

from dashboard.apis.iwencai_service import (  # noqa: E402
    read_dragon_tiger_snapshot,
    write_dragon_tiger_archive,
    write_dragon_tiger_snapshot,
)
from reports.a_share import iwencai_dragon_tiger_snapshot as snapshot_job  # noqa: E402


def _payload(trade_date: str, code: str) -> dict[str, object]:
    return {
        "enabled": True,
        "available": True,
        "source": "同花顺问财",
        "date": trade_date,
        "items": [{"code": code, "name": "样本股票"}],
    }


class IwencaiDragonTigerSnapshotTests(unittest.TestCase):
    def test_next_success_replaces_latest_and_expires_legacy_archives(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            archive_dir = path.parent / "iwencai_dragon_tiger"
            self.assertTrue(write_dragon_tiger_snapshot(path, _payload("2026-07-15", "000001.SZ")))
            self.assertTrue(write_dragon_tiger_archive(archive_dir, _payload("2026-07-14", "000002.SZ")))
            original_fetch = snapshot_job.fetch_dragon_tiger
            try:
                snapshot_job.fetch_dragon_tiger = lambda: _payload("2026-07-16", "600000.SH")
                payload, saved = snapshot_job.refresh_snapshot(path)
            finally:
                snapshot_job.fetch_dragon_tiger = original_fetch

            self.assertTrue(saved)
            self.assertEqual(payload["expired_archive_count"], 1)
            self.assertFalse(archive_dir.exists())
            latest = read_dragon_tiger_snapshot(path, trade_date="2026-07-16")
            self.assertIsNotNone(latest)
            self.assertEqual(latest["items"][0]["code"], "600000.SH")

    def test_failed_or_empty_query_preserves_latest_and_legacy_data(self):
        with tempfile.TemporaryDirectory(prefix="niuone-dragon-tiger-") as tmp:
            path = Path(tmp) / "iwencai_dragon_tiger_latest.json"
            archive_dir = path.parent / "iwencai_dragon_tiger"
            self.assertTrue(write_dragon_tiger_snapshot(path, _payload("2026-07-15", "000001.SZ")))
            self.assertTrue(write_dragon_tiger_archive(archive_dir, _payload("2026-07-14", "000002.SZ")))
            original_latest = path.read_bytes()
            original_fetch = snapshot_job.fetch_dragon_tiger
            try:
                snapshot_job.fetch_dragon_tiger = lambda: {
                    "enabled": True,
                    "available": True,
                    "date": "2026-07-16",
                    "items": [],
                }
                payload, saved = snapshot_job.refresh_snapshot(path)
            finally:
                snapshot_job.fetch_dragon_tiger = original_fetch

            self.assertFalse(saved)
            self.assertNotIn("expired_archive_count", payload)
            self.assertEqual(path.read_bytes(), original_latest)
            self.assertTrue((archive_dir / "2026-07-14.json").is_file())


if __name__ == "__main__":
    unittest.main()
