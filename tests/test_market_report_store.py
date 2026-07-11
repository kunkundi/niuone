#!/usr/bin/env python3
import importlib.util
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


class MarketReportStoreTests(unittest.TestCase):
    def test_extract_decision_guidance_accepts_premarket_heading(self):
        sys.path.insert(0, str(SRC))
        spec = importlib.util.spec_from_file_location("store_under_test", SRC / "market_report_store.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        guidance = mod.extract_decision_guidance("""牛牛大王，A股盘后总结来了：

🎯 **次日盘前指引**
· 风险级别：谨慎
· 开仓节奏：次日最多1笔

📌 **次日关注池**
· 主线方向：半导体
""")

        self.assertEqual(guidance, ["风险级别：谨慎", "开仓节奏：次日最多1笔"])

    def test_store_market_report_writes_database_only_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["DASHBOARD_HOME"] = tmp
            sample = """牛牛大王，A股盘后总结

市场情绪：测试。

🎯 **今日买卖指引**
· 风险级别：谨慎
· 开仓节奏：午后最多2-3只
· 买入指引：只看主线
"""
            code = f"""
import importlib.util, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, {str(SRC)!r})
spec = importlib.util.spec_from_file_location('store_under_test', {str(SRC / 'market_report_store.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
now = datetime(2026, 6, 23, 7, 10, 0, tzinfo=timezone.utc)
later = datetime(2026, 6, 23, 7, 15, 0, tzinfo=timezone.utc)
os.environ['NIUONE_CRON_RUN_KEY'] = '67ac98149ead:202606231510'
first_count = m.store_market_report({sample!r}, job_id='67ac98149ead', title='A股盘后总结', run_dt=now)
second_count = m.store_market_report({sample!r}, job_id='67ac98149ead', title='A股盘后总结', run_dt=later)
import push_history
data = push_history.query_messages(category='market_monitor', limit=5)
record = data['records'][0]
print(json.dumps({{
  'first_count': first_count,
  'second_count': second_count,
  'record_count': len(data['records']),
  'record_category': record.get('category'),
  'record_kind': record.get('kind'),
  'record_source_type': record.get('source_type'),
  'record_raw_path': record.get('raw_path'),
  'record_external_id': record.get('external_id'),
  'record_contains_sample': '市场情绪：测试' in record.get('content', ''),
  'decision_guidance': (record.get('metadata') or {{}}).get('decision_guidance'),
  'metadata_run_key': (record.get('metadata') or {{}}).get('run_key'),
  'delivery_mode': (record.get('delivery') or {{}}).get('mode'),
  'markdown_count': len(list(Path({tmp!r}).rglob('*.md'))),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, "-c", textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertEqual(data["first_count"], 1)
            self.assertEqual(data["second_count"], 1)
            self.assertEqual(data["record_count"], 1)
            self.assertEqual(data["record_category"], "market_monitor")
            self.assertEqual(data["record_kind"], "cron_output")
            self.assertEqual(data["record_source_type"], "market_monitor")
            self.assertEqual(data["record_raw_path"], "")
            self.assertEqual(data["record_external_id"], "67ac98149ead:202606231510")
            self.assertTrue(data["record_contains_sample"])
            self.assertIn("风险级别：谨慎", data["decision_guidance"])
            self.assertEqual(data["metadata_run_key"], "67ac98149ead:202606231510")
            self.assertEqual(data["delivery_mode"], "dashboard_database_only")
            self.assertEqual(data["markdown_count"], 0)


if __name__ == "__main__":
    unittest.main()
