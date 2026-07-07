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
SRC = ROOT / 'app'


class UsRatingReportTests(unittest.TestCase):
    def test_call_api_omits_temperature_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            code = f"""
import importlib.util, json, sys
sys.path.insert(0, {str(SRC)!r})
spec = importlib.util.spec_from_file_location('us_rating_report_under_test', {str(SRC / 'us_rating_report.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
captured = {{}}
class Resp:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def read(self):
        return b'{{"choices":[{{"message":{{"content":"ok"}}}}]}}'
def fake_urlopen(req, timeout=0, context=None):
    captured['payload'] = json.loads(req.data.decode('utf-8'))
    captured['headers'] = dict(req.header_items())
    return Resp()
m.urlopen = fake_urlopen
m._call_api('https://rating.example/v1', 'secret', [{{'role':'user','content':'hello'}}], max_tokens=123)
print(json.dumps(captured, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            captured = json.loads(out)
            payload = captured['payload']
            self.assertEqual(payload['max_tokens'], 123)
            self.assertNotIn('temperature', payload)
            self.assertEqual(captured['headers']['User-agent'], 'NiuOne/1.0')
            self.assertEqual(captured['headers']['Accept'], 'application/json')

    def test_us_rating_context_length_sets_report_max_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['US_RATING_CONTEXT_LENGTH'] = '128K'
            code = f"""
import importlib.util, json, sys
sys.path.insert(0, {str(SRC)!r})
spec = importlib.util.spec_from_file_location('us_rating_report_under_test', {str(SRC / 'us_rating_report.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m._get_crossdesk_credentials = lambda: ('https://rating.example/v1', 'secret')
captured = {{}}
def fake_call(base_url, api_key, messages, max_tokens=8192):
    captured.update({{'base_url': base_url, 'api_key': api_key, 'max_tokens': max_tokens}})
    return '- TEST: upgraded by Example'
m._call_api = fake_call
result = m.generate_report(test_mode=False)
print(json.dumps({{
  'result': result,
  'max_tokens': captured.get('max_tokens'),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertEqual(data['max_tokens'], 128000)
            self.assertIn('TEST', data['result'])

    def test_paths_are_dashboard_home_scoped_and_prompt_has_no_telegram(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            code = f"""
import importlib.util, json, sys
sys.path.insert(0, {str(SRC)!r})
spec = importlib.util.spec_from_file_location('us_rating_report_under_test', {str(SRC / 'us_rating_report.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
prompt = m.build_user_prompt()
print(json.dumps({{
  'dashboard_home': str(m.DASHBOARD_HOME),
  'config_path': str(m.CONFIG_PATH),
  'output_dir': str(m.OUTPUT_DIR),
  'mentions_telegram': 'telegram' in prompt.lower(),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', code], env=env, text=True)
            data = json.loads(out)
            self.assertEqual(data['dashboard_home'], tmp)
            self.assertEqual(data['config_path'], str(Path(tmp) / 'config.yaml'))
            self.assertEqual(data['output_dir'], str(Path(tmp) / 'cron' / 'output' / 'fd0b807138f4'))
            self.assertFalse(data['mentions_telegram'])

    def test_archive_and_db_write_create_dashboard_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            sample = """**牛牛大王，美股机构买入评级日报（2026年06月23日）**

- TEST / Test Corp
  机构/分析师：Example Bank / Analyst
  评级动作：新覆盖 Buy
  目标价：100美元
  核心理由/催化剂：测试催化剂
  风险点：测试风险
  适合关注类型：中线趋势
"""
            code = f"""
import importlib.util, json, sys
from datetime import datetime, timezone
sys.path.insert(0, {str(SRC)!r})
spec = importlib.util.spec_from_file_location('us_rating_report_under_test', {str(SRC / 'us_rating_report.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
now = datetime(2026, 6, 23, 3, 0, 0, tzinfo=timezone.utc)
path = m.archive_report({sample!r}, now=now)
count = m.write_report_to_db({sample!r}, path, now=now)
import push_history
data = push_history.query_messages(category='us_ratings', limit=5)
record = data['records'][0]
print(json.dumps({{
  'archive_path': str(path),
  'db_count': count,
  'record_category': record.get('category'),
  'record_kind': record.get('kind'),
  'record_source_type': record.get('source_type'),
  'record_contains_sample': 'TEST / Test Corp' in record.get('content', ''),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertEqual(data['archive_path'], str(Path(tmp) / 'cron' / 'output' / 'fd0b807138f4' / '2026-06-23_11-00-00.md'))
            self.assertEqual(data['db_count'], 1)
            self.assertEqual(data['record_category'], 'us_ratings')
            self.assertEqual(data['record_kind'], 'cron_output')
            self.assertEqual(data['record_source_type'], 'us_ratings')
            self.assertTrue(data['record_contains_sample'])

    def test_failure_does_not_create_dashboard_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            code = f"""
import importlib.util, json, sys
from pathlib import Path
sys.path.insert(0, {str(SRC)!r})
spec = importlib.util.spec_from_file_location('us_rating_report_under_test', {str(SRC / 'us_rating_report.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
def fail_generate_report(test_mode=False):
    raise RuntimeError('boom')
m.generate_report = fail_generate_report
sys.argv = ['us_rating_report.py', '--archive-only']
try:
    m.main()
except SystemExit as exc:
    code = int(exc.code or 0)
else:
    code = 0
import push_history
data = push_history.query_messages(category='us_ratings', limit=5)
out_dir = Path({str(Path(tmp) / 'cron' / 'output' / 'fd0b807138f4')!r})
print(json.dumps({{
  'code': code,
  'record_count': len(data['records']),
  'archive_count': len(list(out_dir.glob('*.md'))) if out_dir.exists() else 0,
}}, ensure_ascii=False))
"""
            proc = subprocess.run([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            data = json.loads(proc.stdout)
            self.assertEqual(data['code'], 1)
            self.assertEqual(data['record_count'], 0)
            self.assertEqual(data['archive_count'], 0)
            self.assertIn('ERROR: RuntimeError: boom', proc.stderr)


if __name__ == '__main__':
    unittest.main()
