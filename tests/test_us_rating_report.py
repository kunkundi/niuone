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
COMPAT = SRC / 'compat'
ENTRYPOINTS = SRC / 'entrypoints'


class UsRatingReportTests(unittest.TestCase):
    def test_call_api_omits_temperature_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env['DASHBOARD_GROK_MODEL'] = 'grok-4.20-multi-agent-xhigh'
            env['DASHBOARD_GROK_API_MODE'] = 'auto'
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('us_rating_report_under_test', {str(COMPAT / 'us_rating_report.py')!r})
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

    def test_grok_45_uses_responses_web_search_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env['DASHBOARD_GROK_MODEL'] = 'grok-4.5'
            env['DASHBOARD_GROK_API_MODE'] = 'auto'
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('us_rating_report_under_test', {str(COMPAT / 'us_rating_report.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
captured = {{}}
class Resp:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def read(self):
        return b'{{"output":[{{"content":[{{"type":"output_text","text":"ok"}}]}}]}}'
def fake_urlopen(req, timeout=0, context=None):
    captured['url'] = req.full_url
    captured['payload'] = json.loads(req.data.decode('utf-8'))
    return Resp()
m.urlopen = fake_urlopen
result = m._call_api('https://rating.example/v1', 'secret', [{{'role':'user','content':'hello'}}], max_tokens=321)
print(json.dumps({{'captured': captured, 'result': result}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            payload = data['captured']['payload']
            self.assertEqual(data['captured']['url'], 'https://rating.example/v1/responses')
            self.assertEqual(payload['max_output_tokens'], 321)
            self.assertEqual(payload['tools'], [{'type': 'web_search'}])
            self.assertEqual(payload['reasoning'], {'effort': 'low'})
            self.assertEqual(data['result'], 'ok')

    def test_api_mode_can_force_chat_completions_for_grok_45(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env['DASHBOARD_GROK_MODEL'] = 'grok-4.5'
            env['DASHBOARD_GROK_API_MODE'] = 'chat'
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('us_rating_report_under_test', {str(COMPAT / 'us_rating_report.py')!r})
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
    captured['url'] = req.full_url
    captured['payload'] = json.loads(req.data.decode('utf-8'))
    return Resp()
m.urlopen = fake_urlopen
result = m._call_api('https://rating.example/v1', 'secret', [{{'role':'user','content':'hello'}}], max_tokens=222)
print(json.dumps({{'captured': captured, 'result': result}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            payload = data['captured']['payload']
            self.assertEqual(data['captured']['url'], 'https://rating.example/v1/chat/completions')
            self.assertEqual(payload['max_tokens'], 222)
            self.assertNotIn('tools', payload)
            self.assertEqual(data['result'], 'ok')

    def test_us_rating_context_length_does_not_set_report_max_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env['US_RATING_CONTEXT_LENGTH'] = '128K'
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('us_rating_report_under_test', {str(COMPAT / 'us_rating_report.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m._get_crossdesk_credentials = lambda: ('https://rating.example/v1', 'secret')
captured = {{}}
def fake_call(base_url, api_key, messages, max_tokens=4096):
    captured.update({{'base_url': base_url, 'api_key': api_key, 'max_tokens': max_tokens}})
    return '- TEST: upgraded by Example'
m._call_api = fake_call
result = m.generate_report(test_mode=False)
print(json.dumps({{
  'result': result,
  'context_length': m.US_RATING_CONTEXT_LENGTH,
  'max_tokens': captured.get('max_tokens'),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertEqual(data['context_length'], 128000)
            self.assertEqual(data['max_tokens'], 4096)
            self.assertIn('TEST', data['result'])

    def test_us_rating_max_tokens_sets_report_output_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env['US_RATING_CONTEXT_LENGTH'] = '128K'
            env['US_RATING_MAX_TOKENS'] = '4096'
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('us_rating_report_under_test', {str(COMPAT / 'us_rating_report.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m._get_crossdesk_credentials = lambda: ('https://rating.example/v1', 'secret')
captured = {{}}
def fake_call(base_url, api_key, messages, max_tokens=4096):
    captured.update({{'max_tokens': max_tokens}})
    return '- TEST: upgraded by Example'
m._call_api = fake_call
result = m.generate_report(test_mode=False)
print(json.dumps({{
  'result': result,
  'context_length': m.US_RATING_CONTEXT_LENGTH,
  'max_tokens': captured.get('max_tokens'),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertEqual(data['context_length'], 128000)
            self.assertEqual(data['max_tokens'], 4096)
            self.assertIn('TEST', data['result'])

    def test_paths_are_dashboard_home_scoped_and_prompt_has_no_telegram(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('us_rating_report_under_test', {str(COMPAT / 'us_rating_report.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
prompt = m.build_user_prompt()
print(json.dumps({{
  'dashboard_home': str(m.DASHBOARD_HOME),
  'config_path': str(m.CONFIG_PATH),
  'mentions_telegram': 'telegram' in prompt.lower(),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', code], env=env, text=True)
            data = json.loads(out)
            self.assertEqual(data['dashboard_home'], tmp)
            self.assertEqual(data['config_path'], str(Path(tmp) / 'config.yaml'))
            self.assertFalse(data['mentions_telegram'])

    def test_database_write_creates_one_record_without_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
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
import importlib.util, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('us_rating_report_under_test', {str(COMPAT / 'us_rating_report.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
now = datetime(2026, 6, 23, 3, 0, 0, tzinfo=timezone.utc)
later = datetime(2026, 6, 23, 3, 5, 0, tzinfo=timezone.utc)
os.environ['NIUONE_CRON_RUN_KEY'] = 'fd0b807138f4:202606231100'
first_count = m.write_report_to_db({sample!r}, now=now)
second_count = m.write_report_to_db({sample!r}, now=later)
import push_history
data = push_history.query_messages(category='us_ratings', limit=5)
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
  'metadata_run_key': (record.get('metadata') or {{}}).get('run_key'),
  'delivery_mode': (record.get('delivery') or {{}}).get('mode'),
  'record_contains_sample': 'TEST / Test Corp' in record.get('content', ''),
  'markdown_count': len(list(Path({tmp!r}).rglob('*.md'))),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertEqual(data['first_count'], 1)
            self.assertEqual(data['second_count'], 1)
            self.assertEqual(data['record_count'], 1)
            self.assertEqual(data['record_category'], 'us_ratings')
            self.assertEqual(data['record_kind'], 'cron_output')
            self.assertEqual(data['record_source_type'], 'us_ratings')
            self.assertEqual(data['record_raw_path'], '')
            self.assertEqual(data['record_external_id'], 'fd0b807138f4:202606231100')
            self.assertEqual(data['metadata_run_key'], 'fd0b807138f4:202606231100')
            self.assertEqual(data['delivery_mode'], 'dashboard_database_only')
            self.assertTrue(data['record_contains_sample'])
            self.assertEqual(data['markdown_count'], 0)

    def test_failure_does_not_create_dashboard_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            code = f"""
import importlib.util, json, sys
from pathlib import Path
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('us_rating_report_under_test', {str(COMPAT / 'us_rating_report.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
def fail_generate_report(test_mode=False):
    raise RuntimeError('boom')
m.generate_report = fail_generate_report
sys.argv = ['us_rating_report.py', '--store-only']
try:
    m.main()
except SystemExit as exc:
    code = int(exc.code or 0)
else:
    code = 0
import push_history
data = push_history.query_messages(category='us_ratings', limit=5)
print(json.dumps({{
  'code': code,
  'record_count': len(data['records']),
  'markdown_count': len(list(Path({tmp!r}).rglob('*.md'))),
}}, ensure_ascii=False))
"""
            proc = subprocess.run([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            data = json.loads(proc.stdout)
            self.assertEqual(data['code'], 1)
            self.assertEqual(data['record_count'], 0)
            self.assertEqual(data['markdown_count'], 0)
            self.assertIn('ERROR: RuntimeError: boom', proc.stderr)

    def test_database_failure_exits_nonzero_for_scheduler_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            code = f"""
import importlib.util, json, sys
from pathlib import Path
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('us_rating_report_under_test', {str(COMPAT / 'us_rating_report.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.generate_report = lambda test_mode=False: '- TEST / Test Corp: Buy'
def fail_write(*_args, **_kwargs):
    raise RuntimeError('database unavailable')
m.write_report_to_db = fail_write
sys.argv = ['us_rating_report.py', '--store-only']
try:
    m.main()
except SystemExit as exc:
    code = int(exc.code or 0)
else:
    code = 0
print(json.dumps({{
  'code': code,
  'markdown_count': len(list(Path({tmp!r}).rglob('*.md'))),
}}, ensure_ascii=False))
"""
            proc = subprocess.run(
                [sys.executable, '-c', textwrap.dedent(code)],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            data = json.loads(proc.stdout)
            self.assertEqual(data['code'], 1)
            self.assertEqual(data['markdown_count'], 0)
            self.assertIn('ERROR: database write failed: RuntimeError: database unavailable', proc.stderr)


if __name__ == '__main__':
    unittest.main()
