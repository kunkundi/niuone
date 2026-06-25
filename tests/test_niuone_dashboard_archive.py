#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"


class NiuoneDashboardArchiveTests(unittest.TestCase):
    def test_archive_market_report_writes_file_and_push_history_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["DASHBOARD_HOME"] = tmp
            sample = "牛牛大王，A股盘后总结\n\n市场情绪：测试。"
            code = f"""
import importlib.util, json, sys
from datetime import datetime, timezone
sys.path.insert(0, {str(SRC)!r})
spec = importlib.util.spec_from_file_location('archive_under_test', {str(SRC / 'niuone_dashboard_archive.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
now = datetime(2026, 6, 23, 7, 10, 0, tzinfo=timezone.utc)
path = m.archive_market_report({sample!r}, job_id='67ac98149ead', title='A股盘后总结', run_dt=now)
import push_history
data = push_history.query_messages(category='market_monitor', limit=5)
record = data['records'][0]
print(json.dumps({{
  'archive_path': str(path),
  'archive_exists': path.exists(),
  'record_category': record.get('category'),
  'record_kind': record.get('kind'),
  'record_source_type': record.get('source_type'),
  'record_raw_path': record.get('raw_path'),
  'record_contains_sample': '市场情绪：测试' in record.get('content', ''),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, "-c", textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            expected_path = Path(tmp) / "cron" / "output" / "67ac98149ead" / "2026-06-23_15-10-00.md"
            self.assertEqual(data["archive_path"], str(expected_path))
            self.assertTrue(data["archive_exists"])
            self.assertEqual(data["record_category"], "market_monitor")
            self.assertEqual(data["record_kind"], "cron_output")
            self.assertEqual(data["record_source_type"], "market_monitor")
            self.assertEqual(data["record_raw_path"], str(expected_path))
            self.assertTrue(data["record_contains_sample"])


if __name__ == "__main__":
    unittest.main()
