#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'app'
COMPAT = SRC / 'compat'
ENTRYPOINTS = SRC / 'entrypoints'


class XWatchlistMonitorTests(unittest.TestCase):
    def test_x_watchlist_request_timeout_can_be_overridden(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env['X_WATCHLIST_REQUEST_TIMEOUT_SECONDS'] = '50'
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print(json.dumps({{'timeout': m.REQUEST_TIMEOUT_SECONDS}}))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            self.assertEqual(json.loads(out)['timeout'], 50)

    def test_context_length_does_not_set_x_watchlist_max_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env['X_WATCHLIST_CONTEXT_LENGTH'] = '128K'
            env.pop('DASHBOARD_X_WATCHLIST_STATE', None)
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
captured = {{}}
def fake_openai_chat_json(base_url, api_key, prompt, max_tokens, timeout=m.REQUEST_TIMEOUT_SECONDS, x_handles=None):
    captured['max_tokens'] = max_tokens
    return {{'accounts': []}}
m.openai_chat_json = fake_openai_chat_json
m.call_grok_once('https://x.example/v1', 'secret', ['foo'], {{}}, timeout=3)
print(json.dumps({{
  'context_length': m.X_WATCHLIST_CONTEXT_LENGTH,
  'configured_max_tokens': m.X_WATCHLIST_MAX_TOKENS,
  'max_tokens': captured.get('max_tokens'),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertEqual(data['context_length'], 128000)
            self.assertEqual(data['configured_max_tokens'], 4096)
            self.assertEqual(data['max_tokens'], 4096)

    def test_x_watchlist_max_tokens_sets_output_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env['X_WATCHLIST_CONTEXT_LENGTH'] = '128K'
            env['X_WATCHLIST_MAX_TOKENS'] = '4096'
            env.pop('DASHBOARD_X_WATCHLIST_STATE', None)
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
captured = {{}}
def fake_openai_chat_json(base_url, api_key, prompt, max_tokens, timeout=m.REQUEST_TIMEOUT_SECONDS, x_handles=None):
    captured['max_tokens'] = max_tokens
    return {{'accounts': []}}
m.openai_chat_json = fake_openai_chat_json
m.call_grok_once('https://x.example/v1', 'secret', ['foo'], {{}}, timeout=3)
print(json.dumps({{
  'context_length': m.X_WATCHLIST_CONTEXT_LENGTH,
  'configured_max_tokens': m.X_WATCHLIST_MAX_TOKENS,
  'max_tokens': captured.get('max_tokens'),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertEqual(data['context_length'], 128000)
            self.assertEqual(data['configured_max_tokens'], 4096)
            self.assertEqual(data['max_tokens'], 4096)

    def test_openai_chat_json_omits_temperature_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env['DASHBOARD_GROK_MODEL'] = 'grok-4.20-multi-agent-xhigh'
            env['DASHBOARD_GROK_API_MODE'] = 'auto'
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
captured = {{}}
class Resp:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def read(self):
        return b'{{"choices":[{{"message":{{"content":"{{\\\\\\"accounts\\\\\\":[]}}"}}}}]}}'
def fake_urlopen(req, timeout=0):
    captured['payload'] = json.loads(req.data.decode('utf-8'))
    captured['headers'] = dict(req.header_items())
    return Resp()
m.urllib.request.urlopen = fake_urlopen
m.openai_chat_json('https://x.example/v1', 'secret', 'return JSON', 123, timeout=3)
print(json.dumps(captured, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            captured = json.loads(out)
            payload = captured['payload']
            self.assertEqual(payload['max_tokens'], 123)
            self.assertNotIn('temperature', payload)
            self.assertEqual(captured['headers']['User-agent'], 'NiuOne/1.0')
            self.assertEqual(captured['headers']['Accept'], 'application/json')

    def test_gpt_x_search_stays_on_chat_in_auto_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env['DASHBOARD_GROK_MODEL'] = 'gpt-5.6-sol'
            env['DASHBOARD_GROK_API_MODE'] = 'auto'
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
captured = {{}}
class Resp:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def read(self):
        return b'{{"choices":[{{"message":{{"content":"{{\\\\\\"accounts\\\\\":[]}}"}}}}]}}'
def fake_urlopen(req, timeout=0):
    captured['url'] = req.full_url
    captured['payload'] = json.loads(req.data.decode('utf-8'))
    return Resp()
m.urllib.request.urlopen = fake_urlopen
result = m.openai_chat_json('https://x.example/v1', 'secret', 'return JSON', 321, timeout=3, x_handles=['@Foo'])
print(json.dumps({{'captured': captured, 'result': result}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            payload = data['captured']['payload']
            self.assertEqual(data['captured']['url'], 'https://x.example/v1/chat/completions')
            self.assertEqual(payload['max_tokens'], 321)
            self.assertNotIn('tools', payload)
            self.assertEqual(data['result'], {'accounts': []})

    def test_empty_and_non_json_model_responses_are_temporary_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env['DASHBOARD_GROK_MODEL'] = 'gpt-5.6-sol'
            env['DASHBOARD_GROK_API_MODE'] = 'auto'
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
class Resp:
    def __init__(self, body):
        self.body = body
        self.headers = {{'Content-Type': 'text/plain'}}
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def read(self):
        return self.body
results = []
for body in (b'', b'<html>gateway error</html>'):
    m.urllib.request.urlopen = lambda req, timeout=0, body=body: Resp(body)
    try:
        m.openai_chat_json('https://x.example/v1', 'secret', 'return JSON', 321, timeout=3)
    except Exception as exc:
        results.append({{'type': type(exc).__name__, 'temporary': m.is_temporary_error(exc)}})
print(json.dumps(results))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertEqual(
                data,
                [
                    {'type': 'ModelResponseParseError', 'temporary': True},
                    {'type': 'ModelResponseParseError', 'temporary': True},
                ],
            )

    def test_grok_45_uses_responses_x_search_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env['DASHBOARD_GROK_MODEL'] = 'grok-4.5'
            env['DASHBOARD_GROK_API_MODE'] = 'auto'
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
captured = {{}}
class Resp:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def read(self):
        return b'{{"output":[{{"content":[{{"type":"output_text","text":"{{\\\\\\"accounts\\\\\\":[]}}"}}]}}]}}'
def fake_urlopen(req, timeout=0):
    captured['url'] = req.full_url
    captured['payload'] = json.loads(req.data.decode('utf-8'))
    return Resp()
m.urllib.request.urlopen = fake_urlopen
result = m.openai_chat_json('https://x.example/v1', 'secret', 'return JSON', 321, timeout=3, x_handles=['@Foo'])
print(json.dumps({{'captured': captured, 'result': result}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            payload = data['captured']['payload']
            self.assertEqual(data['captured']['url'], 'https://x.example/v1/responses')
            self.assertEqual(payload['max_output_tokens'], 321)
            self.assertEqual(payload['tools'], [{'type': 'x_search', 'allowed_x_handles': ['foo']}])
            self.assertEqual(payload['reasoning'], {'effort': 'low'})
            self.assertEqual(data['result'], {'accounts': []})

    def test_api_mode_can_force_responses_for_gateway_model_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env['DASHBOARD_GROK_MODEL'] = 'gateway-grok-latest'
            env['DASHBOARD_GROK_API_MODE'] = 'responses'
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
captured = {{}}
class Resp:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def read(self):
        return b'{{"output":[{{"content":[{{"type":"output_text","text":"{{\\\\\\\"accounts\\\\\\\":[]}}"}}]}}]}}'
def fake_urlopen(req, timeout=0):
    captured['url'] = req.full_url
    captured['payload'] = json.loads(req.data.decode('utf-8'))
    return Resp()
m.urllib.request.urlopen = fake_urlopen
result = m.openai_chat_json('https://x.example/v1', 'secret', 'return JSON', 222, timeout=3, x_handles=['@Foo'])
print(json.dumps({{'captured': captured, 'result': result}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            payload = data['captured']['payload']
            self.assertEqual(data['captured']['url'], 'https://x.example/v1/responses')
            self.assertEqual(payload['max_output_tokens'], 222)
            self.assertEqual(payload['tools'], [{'type': 'x_search', 'allowed_x_handles': ['foo']}])
            self.assertEqual(data['result'], {'accounts': []})

    def test_context_lookup_does_not_restrict_x_search_to_monitored_handles(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
captured_handles = []
def fake_openai_chat_json(base_url, api_key, prompt, max_tokens, timeout=m.REQUEST_TIMEOUT_SECONDS, x_handles=None):
    captured_handles.append(x_handles)
    return {{'posts': []}}
m.openai_chat_json = fake_openai_chat_json
post = {{
    'time': '2026-07-16 10:00:00',
    'chinese_text': '回复内容',
    'conversation_type': 'reply',
}}
m.hydrate_posts(
    'https://x.example/v1',
    'secret',
    [('监控账号', post, '123456789', 'monitored')],
    timeout=3,
)
m.repair_context_from_x_html = lambda *args, **kwargs: dict(post)
m.has_recovered_context = lambda _post: False
m.repair_one_context(
    'https://x.example/v1',
    'secret',
    '监控账号',
    post,
    '123456789',
    'monitored',
    timeout=3,
)
print(json.dumps({{'captured_handles': captured_handles}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            self.assertEqual(json.loads(out)['captured_handles'], [None, None])

    def test_paths_are_dashboard_home_scoped_and_telegram_helpers_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env.pop('DASHBOARD_X_WATCHLIST_STATE', None)
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print(json.dumps({{
  'dashboard_home': str(m.DASHBOARD_HOME),
  'state_path': str(m.STATE_PATH),
  'config_path': str(m.CONFIG_PATH),
  'has_telegram_delivery': hasattr(m, 'deliver_cards_directly') or hasattr(m, 'telegram_api_call'),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', code], env=env, text=True)
            data = json.loads(out)
            self.assertEqual(data['dashboard_home'], tmp)
            self.assertEqual(data['state_path'], str(Path(tmp) / 'cron' / 'state' / 'x_watchlist_latest.json'))
            self.assertEqual(data['config_path'], str(Path(tmp) / 'config.yaml'))
            self.assertFalse(data['has_telegram_delivery'])

    def test_accounts_can_be_overridden_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env.pop('DASHBOARD_X_WATCHLIST_STATE', None)
            env['X_WATCHLIST_ACCOUNTS'] = '@Foo, bar;Foo invalid-handle-too-long'
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print(json.dumps({{
  'accounts': m.ACCOUNTS,
  'parsed_empty_len': len(m.parse_watchlist_accounts('')),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertEqual(data['accounts'], ['foo', 'bar'])
            self.assertEqual(data['parsed_empty_len'], 0)

    def test_accounts_fall_back_to_existing_state_when_env_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / 'cron' / 'state' / 'x_watchlist_latest.json'
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({
                'latest': {'Foo': {}, 'bar': {}},
                'seen_ids': {'baz': [], 'foo': []},
                'sent_missing_context': [{'handle': 'qux'}],
            }), encoding='utf-8')
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env.pop('DASHBOARD_X_WATCHLIST_STATE', None)
            env.pop('X_WATCHLIST_ACCOUNTS', None)
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print(json.dumps({{
  'accounts': m.ACCOUNTS,
  'state_accounts': m.watchlist_accounts_from_state(),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertEqual(data['accounts'], ['foo', 'bar', 'baz', 'qux'])
            self.assertEqual(data['state_accounts'], ['foo', 'bar', 'baz', 'qux'])

    def test_empty_accounts_skip_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env.pop('DASHBOARD_X_WATCHLIST_STATE', None)
            env.pop('X_WATCHLIST_ACCOUNTS', None)
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
accounts = m.call_grok('', '', {{}})
print(json.dumps({{
  'configured_accounts': m.ACCOUNTS,
  'returned_accounts': accounts,
  'last_issue': getattr(m.call_grok, 'last_issue', ''),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertEqual(data['configured_accounts'], [])
            self.assertEqual(data['returned_accounts'], [])
            self.assertEqual(data['last_issue'], 'watchlist_accounts_empty')

    def test_send_ready_items_writes_to_dashboard_database_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env.pop('DASHBOARD_X_WATCHLIST_STATE', None)
            code = f"""
import importlib.util, json, sys, time
from pathlib import Path
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
state = {{'seen_ids': {{}}, 'latest': {{}}}}
post = {{
  'post_id': 'unit-post-1',
  'time': '2026-06-23 10:00:00',
  'chinese_text': '测试推文正文',
  'conversation_type': 'original',
  'media': [],
}}
ok = m.send_ready_items('', '', state, [('测试账号', post, 'unit-post-1', 'tester')], {{}}, time.monotonic() + 30)
con = m.push_history.connect()
try:
    row = con.execute(
        "SELECT category, content, metadata_json, delivery_json, raw_path FROM dashboard_messages WHERE external_id = ?",
        ('unit-post-1',),
    ).fetchone()
finally:
    con.close()
metadata = json.loads(row['metadata_json']) if row and row['metadata_json'] else {{}}
delivery = json.loads(row['delivery_json']) if row and row['delivery_json'] else {{}}
print(json.dumps({{
  'ok': ok,
  'mode': state.get('last_delivery_mode'),
  'seen': state.get('seen_ids'),
  'record_category': row['category'] if row else '',
  'record_raw_path': row['raw_path'] if row else None,
  'record_contains_text': '测试推文正文' in (row['content'] if row else ''),
  'metadata_post_id': (metadata.get('post') or {{}}).get('post_id'),
  'delivery_mode': delivery.get('mode'),
  'markdown_count': len(list(Path({tmp!r}).rglob('*.md'))),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertTrue(data['ok'])
            self.assertEqual(data['mode'], 'dashboard_database_only')
            self.assertEqual(data['seen'], {'tester': ['unit-post-1']})
            self.assertEqual(data['record_category'], 'x_monitor')
            self.assertEqual(data['record_raw_path'], '')
            self.assertTrue(data['record_contains_text'])
            self.assertEqual(data['metadata_post_id'], 'unit-post-1')
            self.assertEqual(data['delivery_mode'], 'dashboard_database_only')
            self.assertEqual(data['markdown_count'], 0)

    def test_database_time_uses_numeric_post_id_instead_of_model_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env.pop('DASHBOARD_X_WATCHLIST_STATE', None)
            code = f"""
import importlib.util, json, sys
from datetime import datetime, timezone
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
actual_utc = datetime(2026, 7, 16, 15, 21, tzinfo=timezone.utc)
post_id = str((int(actual_utc.timestamp() * 1000) - 1288834974657) << 22)
post = {{
  'post_id': post_id,
  'time': '2026-07-17 07:21:00',
  'chinese_text': '时间应由推文 ID 校准',
  'conversation_type': 'original',
  'media': [],
}}
count = m.write_direct_x_alerts_to_db([('测试账号', post, post_id, 'tester')])
con = m.push_history.connect()
try:
    row = con.execute(
        "SELECT timestamp, time_text, content, metadata_json FROM dashboard_messages WHERE external_id = ?",
        (post_id,),
    ).fetchone()
finally:
    con.close()
metadata = json.loads(row['metadata_json'])
print(json.dumps({{
  'count': count,
  'timestamp': row['timestamp'],
  'time_text': row['time_text'],
  'content': row['content'],
  'metadata_time': metadata['post']['time'],
  'state_changes': m.normalize_monitor_state_times({{
      'latest': {{'tester': {{'post_id': post_id, 'time': '2026-07-17 07:21:00'}}}},
  }}),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            expected_timestamp = datetime(2026, 7, 16, 15, 21, tzinfo=timezone.utc).timestamp()
            self.assertEqual(data['count'], 1)
            self.assertEqual(data['timestamp'], expected_timestamp)
            self.assertEqual(data['time_text'], '2026-07-16 23:21:00')
            self.assertIn('2026-07-16 23:21:00', data['content'])
            self.assertNotIn('2026-07-17 07:21:00', data['content'])
            self.assertEqual(data['metadata_time'], '2026-07-16 23:21:00')
            self.assertEqual(data['state_changes'], 1)

    def test_database_failure_does_not_advance_seen_or_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env.pop('DASHBOARD_X_WATCHLIST_STATE', None)
            code = f"""
import importlib.util, json, sys, time
from pathlib import Path
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.write_direct_x_alerts_to_db = lambda _items: 0
state = {{'seen_ids': {{}}, 'latest': {{}}}}
post = {{
  'post_id': 'failed-post-1',
  'time': '2026-06-23 10:00:00',
  'chinese_text': '数据库失败时必须重试',
  'conversation_type': 'original',
  'media': [],
}}
ok = m.send_ready_items('', '', state, [('测试账号', post, 'failed-post-1', 'tester')], {{}}, time.monotonic() + 30)
print(json.dumps({{
  'ok': ok,
  'seen': state.get('seen_ids'),
  'latest': state.get('latest'),
  'database_error': state.get('last_database_error'),
  'markdown_count': len(list(Path({tmp!r}).rglob('*.md'))),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertFalse(data['ok'])
            self.assertEqual(data['seen'], {})
            self.assertEqual(data['latest'], {})
            self.assertEqual(data['database_error'], 'incomplete_write:0/1')
            self.assertEqual(data['markdown_count'], 0)

    def test_database_exception_and_partial_batch_keep_all_posts_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env.pop('DASHBOARD_X_WATCHLIST_STATE', None)
            code = f"""
import importlib.util, json, sys, time
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
def item(post_id, minute):
    return ('测试账号', {{
        'post_id': post_id,
        'time': f'2026-06-23 10:{{minute}}:00',
        'chinese_text': post_id,
        'conversation_type': 'original',
        'media': [],
    }}, post_id, 'tester')
items = [item('batch-post-1', '00'), item('batch-post-2', '01')]
exception_state = {{'seen_ids': {{}}, 'latest': {{}}}}
def raise_write(_items):
    raise RuntimeError('database unavailable')
m.write_direct_x_alerts_to_db = raise_write
exception_ok = m.send_ready_items('', '', exception_state, items, {{}}, time.monotonic() + 30)
partial_state = {{'seen_ids': {{}}, 'latest': {{}}}}
m.write_direct_x_alerts_to_db = lambda _items: 1
partial_ok = m.send_ready_items('', '', partial_state, items, {{}}, time.monotonic() + 30)
print(json.dumps({{
  'exception_ok': exception_ok,
  'exception_seen': exception_state.get('seen_ids'),
  'exception_latest': exception_state.get('latest'),
  'exception_error': exception_state.get('last_database_error'),
  'partial_ok': partial_ok,
  'partial_seen': partial_state.get('seen_ids'),
  'partial_latest': partial_state.get('latest'),
  'partial_error': partial_state.get('last_database_error'),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertFalse(data['exception_ok'])
            self.assertEqual(data['exception_seen'], {})
            self.assertEqual(data['exception_latest'], {})
            self.assertEqual(data['exception_error'], 'RuntimeError: database unavailable')
            self.assertFalse(data['partial_ok'])
            self.assertEqual(data['partial_seen'], {})
            self.assertEqual(data['partial_latest'], {})
            self.assertEqual(data['partial_error'], 'incomplete_write:1/2')

    def test_sent_missing_context_is_repaired_in_dashboard_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env.pop('DASHBOARD_X_WATCHLIST_STATE', None)
            code = f"""
import importlib.util, json, sqlite3, sys, time
from pathlib import Path
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
state = {{'seen_ids': {{}}, 'latest': {{}}}}
post = {{
  'post_id': 'reply-post-1',
  'time': '2026-06-23 10:00:00',
  'chinese_text': '这是一条短回复',
  'conversation_type': 'reply',
  'media': [],
}}
ok = m.send_ready_items('', '', state, [('投研荟', post, 'reply-post-1', 'freearkshaw')], {{}}, time.monotonic() + 30)
queued_before = len(state.get('sent_missing_context') or [])
def fake_repair(_base_url, _api_key, display_name, original_post, post_id, handle, timeout=10):
    repaired = dict(original_post)
    repaired.update({{
      'reply_to_author': '上文作者',
      'reply_to_text': '上文原文',
      'reply_to_chinese_text': '上文原文',
      'conversation_type': 'reply',
    }})
    return repaired
m.repair_one_context = fake_repair
repaired_count = m.repair_sent_missing_contexts('', '', state, time.monotonic() + 30, max_items=1)
con = m.push_history.connect()
try:
    row = con.execute(
        "SELECT content, metadata_json FROM dashboard_messages WHERE external_id = ?",
        ('reply-post-1',),
    ).fetchone()
finally:
    con.close()
content = row['content'] if row else ''
metadata = json.loads(row['metadata_json']) if row and row['metadata_json'] else {{}}
print(json.dumps({{
  'ok': ok,
  'queued_before': queued_before,
  'repaired_count': repaired_count,
  'queue_after': len(state.get('sent_missing_context') or []),
  'warning_present': '未取到被回复原推' in content,
  'has_parent': '原帖｜上文作者' in content and '上文原文' in content,
  'metadata_parent': metadata.get('post', {{}}).get('reply_to_author'),
}}, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            data = json.loads(out)
            self.assertTrue(data['ok'])
            self.assertEqual(data['queued_before'], 1)
            self.assertEqual(data['repaired_count'], 1)
            self.assertEqual(data['queue_after'], 0)
            self.assertFalse(data['warning_present'])
            self.assertTrue(data['has_parent'])
            self.assertEqual(data['metadata_parent'], '上文作者')

    def test_extract_x_media_normalizes_and_deduplicates_pbs_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['DASHBOARD_HOME'] = tmp
            env['DASHBOARD_ENV_FILE'] = str(Path(tmp) / 'dashboard.env')
            env.pop('DASHBOARD_X_WATCHLIST_STATE', None)
            code = f"""
import importlib.util, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(SRC)!r}]
spec = importlib.util.spec_from_file_location('x_watchlist_monitor_under_test', {str(COMPAT / 'x_watchlist_monitor.py')!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
raw = '''
<meta property="og:image" content="https://pbs.twimg.com/media/ABC123.jpg:large">
<meta property="twitter:image" content="https://pbs.twimg.com/profile_images/1990755625417203712/9WXSzgqU_200x200.jpg">
<script type="application/ld+json">{{"@type":"SocialMediaPosting","image":"https://pbs.twimg.com/media/ABC123.jpg"}}</script>
relayRecords={{media_url_https:"https://pbs.twimg.com/media/DEF456.png:large"}}
'''
social = m.parse_social_posting(raw)
items = m.extract_x_media(raw, social=social)
print(json.dumps(items, ensure_ascii=False))
"""
            out = subprocess.check_output([sys.executable, '-c', textwrap.dedent(code)], env=env, text=True)
            items = json.loads(out)
            self.assertEqual([item['url'] for item in items], [
                'https://pbs.twimg.com/media/ABC123.jpg:large',
                'https://pbs.twimg.com/media/DEF456.png:large',
            ])
            self.assertTrue(all(item['type'] == 'image' for item in items))


if __name__ == '__main__':
    unittest.main()
