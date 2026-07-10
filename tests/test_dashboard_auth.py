#!/usr/bin/env python3
import importlib.util
import gzip
import io
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
import sys
import urllib.parse
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'app'
sys.path.insert(0, str(SRC))
MODULE_PATH = SRC / 'niuone_dashboard.py'
spec = importlib.util.spec_from_file_location('dashboard_under_test', MODULE_PATH)
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)


class FakeHandler(dashboard.Handler):
    def __init__(self, path='/', method='GET', headers=None, body=b'', ip='127.0.0.1'):
        self.path = path
        self.command = method
        self.headers = headers or {}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.client_address = (ip, 12345)
        self.status = None
        self.sent_headers = []

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, keyword, value):
        self.sent_headers.append((keyword, value))

    def end_headers(self):
        self.send_security_headers()

    def log_message(self, fmt, *args):
        pass

    def header(self, name):
        for key, value in reversed(self.sent_headers):
            if key.lower() == name.lower():
                return value
        return None


class DashboardAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.original_dashboard_env_file = dashboard.DASHBOARD_ENV_FILE
        self.original_cron_state_dir = dashboard.CRON_STATE_DIR
        self.original_stats_db = dashboard.STATS_DB
        self.original_legacy_stats_db = dashboard.LEGACY_STATS_DB
        self.original_admin_token_file = dashboard.ADMIN_TOKEN_FILE
        self.original_admin_password = dashboard.ADMIN_PASSWORD
        self.saved_env = {
            name: os.environ.get(name)
            for name in (
                'DASHBOARD_ADMIN_PASSWORD',
                'X_WATCHLIST_ACCOUNTS',
                'DASHBOARD_X_WATCHLIST_STATE',
                dashboard.STRATEGY_SOURCE_ENV,
                dashboard.PERSONA_STRATEGY_ENV,
                dashboard.PRESET_STRATEGY_TEXT_ENV,
            )
        }
        for name in self.saved_env:
            os.environ.pop(name, None)
        dashboard.STATS_DB = self.tmp_path / 'dashboard_stats.db'
        dashboard.LEGACY_STATS_DB = self.tmp_path / 'dashboard_users.db'
        dashboard.ADMIN_TOKEN_FILE = self.tmp_path / 'dashboard_admin_token.txt'
        dashboard.ADMIN_PASSWORD = ''
        dashboard.DASHBOARD_ENV_FILE = self.tmp_path / 'dashboard.env'
        dashboard.CRON_STATE_DIR = self.tmp_path / 'cron' / 'state'
        dashboard.API_RESPONSE_CACHE.clear()
        dashboard.API_CACHE_KEY_LOCKS.clear()
        dashboard.API_CACHE_KEY_GENERATIONS.clear()
        dashboard.RATE_LIMIT_BUCKETS.clear()
        dashboard.ensure_stats_db()

    def tearDown(self):
        dashboard.DASHBOARD_ENV_FILE = self.original_dashboard_env_file
        dashboard.CRON_STATE_DIR = self.original_cron_state_dir
        dashboard.STATS_DB = self.original_stats_db
        dashboard.LEGACY_STATS_DB = self.original_legacy_stats_db
        dashboard.ADMIN_TOKEN_FILE = self.original_admin_token_file
        dashboard.ADMIN_PASSWORD = self.original_admin_password
        for name, value in self.saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tmp.cleanup()

    def admin_cookie(self):
        return f'{dashboard.ADMIN_SESSION_COOKIE_NAME}={dashboard.new_admin_session()}'

    def test_dashboard_is_public_but_settings_require_admin(self):
        home = FakeHandler(path='/')
        home.do_GET()
        self.assertEqual(home.status, 200)
        self.assertIn('<title>牛牛1号</title>', home.wfile.getvalue().decode('utf-8'))

        admin = FakeHandler(path='/admin')
        admin.do_GET()
        self.assertEqual(admin.status, 200)
        admin_body = admin.wfile.getvalue().decode('utf-8')
        self.assertIn('<h1>设置页验证</h1>', admin_body)
        self.assertIn('name="admin_password"', admin_body)
        self.assertNotIn('<h1>设置</h1>', admin_body)
        self.assertNotIn("name='env__DASHBOARD_GROK_API_KEY'", admin_body)

        config = FakeHandler(path='/api/admin/config')
        config.do_GET()
        self.assertEqual(config.status, 403)
        self.assertEqual(
            json.loads(config.wfile.getvalue().decode('utf-8'))['error'],
            'admin_password_required',
        )

    def test_admin_head_routes_match_get_and_post_only_contracts(self):
        admin = FakeHandler(path='/admin', method='HEAD')
        admin.do_HEAD()
        self.assertEqual(admin.status, 200)
        self.assertEqual(admin.wfile.getvalue(), b'')

        locked_config = FakeHandler(path='/api/admin/config', method='HEAD')
        locked_config.do_HEAD()
        self.assertEqual(locked_config.status, 403)

        unlocked_config = FakeHandler(
            path='/api/admin/config',
            method='HEAD',
            headers={'Cookie': self.admin_cookie()},
        )
        unlocked_config.do_HEAD()
        self.assertEqual(unlocked_config.status, 200)

        write_only = FakeHandler(path='/api/admin/config/env', method='HEAD')
        write_only.do_HEAD()
        self.assertEqual(write_only.status, 404)

        action = FakeHandler(path='/api/niuniu_practice/resume', method='HEAD')
        action.do_HEAD()
        self.assertEqual(action.status, 405)
        self.assertEqual(action.header('Allow'), 'POST')

        refresh = FakeHandler(path='/api/practice_candidates/refresh', method='HEAD')
        refresh.do_HEAD()
        self.assertEqual(refresh.status, 405)
        self.assertEqual(refresh.header('Allow'), 'POST')

    def test_legacy_visit_stats_are_migrated_once(self):
        with closing(sqlite3.connect(dashboard.STATS_DB)) as con:
            con.execute("INSERT INTO visit_stats(key,value,updated_at) VALUES('home_views',3,10)")
            con.execute("INSERT INTO unique_visitors(visitor_hash,first_seen_at,last_seen_at) VALUES('new',10,10)")
            con.commit()

        with closing(sqlite3.connect(dashboard.LEGACY_STATS_DB)) as con:
            con.execute("""
                CREATE TABLE visit_stats (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE unique_visitors (
                    visitor_hash TEXT PRIMARY KEY,
                    first_seen_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL
                )
            """)
            con.execute("INSERT INTO visit_stats(key,value,updated_at) VALUES('home_views',42,20)")
            con.executemany(
                "INSERT INTO unique_visitors(visitor_hash,first_seen_at,last_seen_at) VALUES(?,?,?)",
                [('old', 1, 2), ('new', 5, 20)],
            )
            con.commit()

        dashboard.ensure_stats_db()
        dashboard.ensure_stats_db()

        with closing(sqlite3.connect(dashboard.STATS_DB)) as con:
            views = con.execute("SELECT value FROM visit_stats WHERE key='home_views'").fetchone()[0]
            visitor_count = con.execute("SELECT COUNT(*) FROM unique_visitors").fetchone()[0]
            new_seen = con.execute(
                "SELECT first_seen_at,last_seen_at FROM unique_visitors WHERE visitor_hash='new'"
            ).fetchone()

        self.assertEqual(views, 45)
        self.assertEqual(visitor_count, 2)
        self.assertEqual(new_seen, (5.0, 20.0))

    def test_compact_intraday_equity_history_keeps_latest_day_endpoints(self):
        old_points = [
            {'time': f'2026-06-24 09:{i:02d}:00', 'equity': 1000000 + i, 'pnl_pct': i / 100}
            for i in range(30)
        ]
        latest_points = [
            {'time': f'2026-06-25 10:{i:02d}:00', 'equity': 1000100 + i, 'pnl_pct': i / 100}
            for i in range(40)
        ]

        compacted = dashboard.compact_intraday_equity_history(old_points + latest_points, max_points=12)

        self.assertEqual(len(compacted), 12)
        self.assertTrue(all(p['time'].startswith('2026-06-25') for p in compacted))
        self.assertEqual(compacted[0], latest_points[0])
        self.assertEqual(compacted[-1], latest_points[-1])

    def test_compact_intraday_equity_history_can_keep_latest_day_full_density(self):
        old_points = [
            {'time': f'2026-06-24 09:{i:02d}:00', 'equity': 1000000 + i, 'pnl_pct': i / 100}
            for i in range(30)
        ]
        latest_points = [
            {'time': f'2026-06-25 10:{i:02d}:00', 'equity': 1000100 + i, 'pnl_pct': i / 100}
            for i in range(40)
        ]

        compacted = dashboard.compact_intraday_equity_history(old_points + latest_points, max_points=0)

        self.assertEqual(compacted, latest_points)

    def test_compact_calendar_history_keeps_multi_day_session_shape(self):
        points = [
            {'time': '2026-06-25 09:29:59', 'equity': 999999},
            {'time': '2026-06-25 09:30:00', 'equity': 1000000},
            {'time': '2026-06-25 09:31:00', 'equity': 999900},
            {'time': '2026-06-25 09:32:00', 'equity': 1000200},
            {'time': '2026-06-25 09:34:00', 'equity': 1000100},
            {'time': '2026-06-25 09:35:00', 'equity': 1000150},
            {'time': '2026-06-25 11:30:00', 'equity': 1000300},
            {'time': '2026-06-25 12:00:00', 'equity': 1},
            {'time': '2026-06-25 13:00:00', 'equity': 1000400},
            {'time': '2026-06-25 15:00:00', 'equity': 1000500},
            {'time': '2026-06-25 15:01:00', 'equity': 2},
            {'time': '2026-06-26 09:30:00', 'equity': 1000500},
            {'time': '2026-06-26 10:00:00', 'equity': 1000600},
            {'time': '2026-06-26 15:00:00', 'equity': 1000700},
            {'time': '2026-06-27 10:00:00', 'equity': 1000800},
        ]

        compacted = dashboard.build_compact_calendar_history(
            points,
            source_updated_at='2026-06-26 15:00:10',
            now=datetime(2026, 6, 27, 12, 0, 0),
        )

        self.assertEqual(compacted['schema_version'], 1)
        self.assertEqual(compacted['bucket_minutes'], 10)
        self.assertEqual(compacted['coverage_start'], '2026-06-25')
        self.assertEqual(compacted['coverage_end'], '2026-06-26')
        self.assertEqual(set(compacted['days']), {'2026-06-25', '2026-06-26'})
        first_day = compacted['days']['2026-06-25']
        first_day_clocks = [point['clock'] for point in first_day]
        self.assertIn('09:30:00', first_day_clocks)
        self.assertIn('09:31:00', first_day_clocks)
        self.assertIn('09:32:00', first_day_clocks)
        self.assertIn('15:00:00', first_day_clocks)
        self.assertNotIn('09:29:59', first_day_clocks)
        self.assertNotIn('12:00:00', first_day_clocks)
        self.assertNotIn('15:01:00', first_day_clocks)
        self.assertLessEqual(len(first_day), 96)
        self.assertEqual(compacted['source_updated_at'], '2026-06-26 15:00:10')

    def test_compact_calendar_history_caps_coverage_to_recent_days(self):
        points = [
            {'time': f'2026-06-{day:02d} 10:00:00', 'equity': 1000000 + day}
            for day in (22, 23, 24, 25, 26)
        ]

        compacted = dashboard.build_compact_calendar_history(
            points,
            max_days=2,
            now=datetime(2026, 6, 26, 15, 1, 0),
        )

        self.assertTrue(compacted['truncated'])
        self.assertEqual(list(compacted['days']), ['2026-06-25', '2026-06-26'])

    def test_compact_calendar_history_uses_bounded_m4_buckets(self):
        points = []
        for bucket in range(24):
            for offset, delta in ((0, 0), (1, -100), (2, 100), (9, 50)):
                elapsed = bucket * 10 + offset
                minute_of_day = 9 * 60 + 30 + elapsed if elapsed < 120 else 13 * 60 + elapsed - 120
                points.append({
                    'time': f'2026-06-26 {minute_of_day // 60:02d}:{minute_of_day % 60:02d}:00',
                    'equity': 1000000 + bucket * 10 + delta,
                })

        compacted = dashboard.build_compact_calendar_history(
            points,
            now=datetime(2026, 6, 26, 15, 1, 0),
        )

        day_points = compacted['days']['2026-06-26']
        self.assertEqual(compacted['bucket_minutes'], 10)
        self.assertEqual(len(day_points), 96)
        self.assertIn('13:39:00', [point['clock'] for point in day_points])

    def test_compact_calendar_history_ignores_invalid_points_in_source_version(self):
        compacted = dashboard.build_compact_calendar_history(
            [
                {'time': '9999-not-a-date', 'equity': 1},
                {'time': '2026-06-26 10:00:00', 'equity': 1000000},
                {'time': '2026-06-26 10:01:00', 'equity': 'not-a-number'},
            ],
            now=datetime(2026, 6, 26, 15, 1, 0),
        )

        self.assertEqual(compacted['source_last_equity_time'], '2026-06-26 10:00:00')

    def test_compact_calendar_history_keeps_session_close_points_with_seconds(self):
        compacted = dashboard.build_compact_calendar_history(
            [
                {'time': '2026-06-26 11:30:59', 'equity': 1000001},
                {'time': '2026-06-26 13:00:00', 'equity': 1000002},
                {'time': '2026-06-26 15:00:59', 'equity': 1000003},
            ],
            now=datetime(2026, 6, 26, 15, 1, 30),
        )

        clocks = [point['clock'] for point in compacted['days']['2026-06-26']]
        self.assertEqual(clocks, ['11:30:59', '13:00:00', '15:00:59'])
        self.assertEqual(compacted['source_last_equity_time'], '2026-06-26 15:00:59')

    def test_snapshot_metadata_ignores_invalid_equity_points(self):
        payload = {
            'equity_history': [
                {'time': '9999-not-a-date', 'equity': 2},
                {'time': '2026-06-26 10:00:00', 'equity': 1000000},
                {'time': '2026-06-26 10:01:00', 'equity': 'not-a-number'},
            ],
        }

        dashboard.annotate_practice_snapshot(payload, mode='full', history_scope='retained_history')

        self.assertEqual(payload['source_last_equity_time'], '2026-06-26 10:00:00')

    def test_compact_intraday_equity_history_filters_future_same_day_points(self):
        points = [
            {'time': '2026-06-26 09:30:00', 'equity': 1000000, 'pnl_pct': 0},
            {'time': '2026-06-26 09:39:00', 'equity': 1000100, 'pnl_pct': 0.01},
            {'time': '2026-06-26 15:00:00', 'equity': 1005000, 'pnl_pct': 0.5},
        ]

        compacted = dashboard.compact_intraday_equity_history(
            points,
            now=datetime(2026, 6, 26, 9, 39, 30),
        )

        self.assertEqual([p['time'] for p in compacted], ['2026-06-26 09:30:00', '2026-06-26 09:39:00'])

    def test_compact_intraday_equity_history_ignores_weekend_points(self):
        points = [
            {'time': '2026-06-25 15:00:00', 'equity': 999000, 'pnl_pct': -0.1},
            {'time': '2026-06-24 09:30:00', 'equity': 998000, 'pnl_pct': -0.2},
            {'time': '2026-06-26 09:30:00', 'equity': 1000000, 'pnl_pct': 0},
            {'time': '2026-06-27 11:29:00', 'equity': 1008000, 'pnl_pct': 0.8},
            {'time': '2026-06-26 15:00:00', 'equity': 1005000, 'pnl_pct': 0.5},
        ]

        compacted = dashboard.compact_intraday_equity_history(
            points,
            now=datetime(2026, 6, 27, 12, 31, 0),
        )

        self.assertEqual([p['time'] for p in compacted], ['2026-06-26 09:30:00', '2026-06-26 15:00:00'])

    def test_filter_future_equity_points_caches_trading_day_lookup_per_date(self):
        points = [
            {'time': '2026-06-25 09:30:00', 'equity': 1000000},
            {'time': '2026-06-25 10:00:00', 'equity': 1000100},
            {'time': '2026-06-26 09:30:00', 'equity': 1000200},
            {'time': '2026-06-26 10:00:00', 'equity': 1000300},
        ]
        checked_dates = []
        original_is_trading_day = dashboard.is_a_share_trading_day_for_dashboard
        try:
            dashboard.is_a_share_trading_day_for_dashboard = lambda dt: checked_dates.append(dt.date()) or True

            filtered = dashboard.filter_future_equity_points(
                points,
                now=datetime(2026, 6, 26, 15, 0, 0),
            )
        finally:
            dashboard.is_a_share_trading_day_for_dashboard = original_is_trading_day

        self.assertEqual(filtered, points)
        self.assertEqual(len(checked_dates), 2)

    def test_compact_daily_equity_history_filters_future_same_day_settlement(self):
        points = [
            {'time': '2026-06-25 15:00:00', 'equity': 1000000, 'pnl_pct': 0},
            {'time': '2026-06-26 15:00:00', 'equity': 1005000, 'pnl_pct': 0.5},
        ]

        compacted = dashboard.compact_daily_equity_history(
            points,
            now=datetime(2026, 6, 26, 9, 39, 30),
        )

        self.assertEqual([p['time'] for p in compacted], ['2026-06-25 15:00:00'])

    def test_compact_trade_markers_only_marks_full_position_exit(self):
        trades = [
            {'time': '2026-07-01 09:31:00', 'action': 'BUY', 'code': '600001', 'name': '示例股', 'shares': 1000, 'price': 10},
            {'time': '2026-07-02 10:00:00', 'action': 'SELL', 'code': '600001', 'name': '示例股', 'shares': 400, 'price': 11, 'pnl': 390},
            {'time': '2026-07-03 10:00:00', 'action': 'SELL', 'code': '600001', 'name': '示例股', 'shares': 600, 'price': 12, 'pnl': 1190},
            {'time': '2026-07-03 13:00:00', 'action': 'BUY', 'code': '600002', 'name': '另一股', 'shares': 1000, 'price': 8},
            {
                'time': '2026-07-06 10:00:00', 'action': 'SELL', 'code': '600002', 'name': '另一股',
                'shares': 900, 'price': 9, 'pnl': 880, 'position_after_trade_pct': 0.1,
            },
            {
                'time': '2026-07-07 10:00:00', 'action': 'SELL', 'code': '600002', 'name': '另一股',
                'shares': 100, 'price': 9.5, 'pnl': 145, 'position_after_trade_pct': 0,
            },
        ]

        markers = dashboard.compact_trade_markers(list(reversed(trades)))
        sells = [marker for marker in markers if marker['action'] == 'SELL']

        self.assertEqual([marker['is_full_exit'] for marker in sells], [False, True, False, True])
        self.assertEqual([marker['time'] for marker in markers], sorted(marker['time'] for marker in markers))

    def test_b1_payload_preserves_market_snapshot(self):
        snapshot = {'source': 'b1_mainboard_quotes', 'sample_count': 3000, 'up': 2000, 'down': 900}

        payload = dashboard.normalize_b1_payload_for_trader({
            'generated_at': '2026-07-10 10:00:05',
            'items': [],
            'market_snapshot': snapshot,
            'schedule_slot': '2026-07-10 10:00',
        })

        self.assertEqual(payload['market_snapshot'], snapshot)
        self.assertEqual(payload['schedule_slot'], '2026-07-10 10:00')

    def test_no_candidate_b1_still_refreshes_and_logs_market_context(self):
        calls = {'refresh_payload': None, 'entries': []}
        refreshed = {
            'tone': 'balanced',
            'tone_label': '平衡',
            'source_title': 'B1定时选股实时盘面',
            'source_time': '2026-07-10 10:00:04',
        }

        class TraderStub:
            def refresh_market_strategy_context_for_b1(self, payload):
                calls['refresh_payload'] = payload
                return dict(refreshed)

            def compact_market_strategy_context(self, ctx):
                return dict(ctx)

            def now_ts(self):
                return '2026-07-10 10:00:06'

            def record_decision_log_entry(self, entry, mark_b1_done=False):
                calls['entries'].append((entry, mark_b1_done))

        original_get_trader = dashboard.get_trader_module
        try:
            dashboard.get_trader_module = lambda: TraderStub()
            result = dashboard.run_practice_decision_logged({
                'generated_at': '2026-07-10 10:00:05',
                'items': [],
                'market_snapshot': {'source': 'b1_mainboard_quotes', 'sample_count': 3000},
                'schedule_slot': '2026-07-10 10:00',
            })
        finally:
            dashboard.get_trader_module = original_get_trader

        self.assertEqual(result['reason'], 'no_candidates')
        self.assertEqual(calls['refresh_payload']['market_snapshot']['sample_count'], 3000)
        entry, mark_done = calls['entries'][0]
        self.assertTrue(mark_done)
        self.assertEqual(entry['market_decision_context']['tone'], 'balanced')
        self.assertEqual(entry['decision']['market_guidance']['source_title'], 'B1定时选股实时盘面')

    def test_fast_practice_payload_derives_daily_calendar_points_from_intraday_history(self):
        class TraderStub:
            def load_state(self):
                return {
                    'initial_cash': 1000000,
                    'cash': 1000000,
                    'positions': {},
                    'equity_history': [
                        {'time': '2026-06-22 09:30:00', 'equity': 1000000, 'pnl_pct': 0},
                        {'time': '2026-06-22 15:00:00', 'equity': 1008000, 'pnl_pct': 0.8},
                        {'time': '2026-06-23 09:30:00', 'equity': 1007000, 'pnl_pct': 0.7},
                        {'time': '2026-06-23 15:00:00', 'equity': 992000, 'pnl_pct': -0.8},
                        {'time': '2026-06-24 15:00:00', 'equity': 995000, 'pnl_pct': -0.5},
                    ],
                    'daily_equity_history': [
                        {'time': '2026-06-24 15:00:00', 'equity': 995000, 'pnl_pct': -0.5},
                    ],
                    'trade_log': [],
                    'decision_log': [],
                }

            def enrich_portfolio(self, state):
                return {
                    'initial_cash': state['initial_cash'],
                    'cash': state['cash'],
                    'positions': [],
                    'trade_log': state['trade_log'],
                    'decision_log': state['decision_log'],
                }

        original_get_trader = dashboard.get_trader_module
        original_trading_day_status = dashboard.trading_day_status
        try:
            dashboard.get_trader_module = lambda: TraderStub()
            dashboard.trading_day_status = lambda *args, **kwargs: {'is_trading_day': True, 'date': '2026-06-24'}
            payload = dashboard.get_practice_payload_fast()
        finally:
            dashboard.get_trader_module = original_get_trader
            dashboard.trading_day_status = original_trading_day_status

        self.assertEqual(
            [p['time'] for p in payload['daily_equity_history']],
            ['2026-06-22 15:00:00', '2026-06-23 15:00:00', '2026-06-24 15:00:00'],
        )

    def test_fast_practice_payload_exposes_current_beijing_date(self):
        class TraderStub:
            def load_state(self):
                return {
                    'initial_cash': 1000000,
                    'cash': 1000000,
                    'positions': {},
                    'equity_history': [
                        {'time': '2026-07-02 09:30:00', 'equity': 1000000, 'pnl_pct': 0},
                        {'time': '2026-07-02 15:00:00', 'equity': 1008000, 'pnl_pct': 0.8},
                    ],
                    'daily_equity_history': [],
                    'trade_log': [{'time': '2026-07-03 00:03:00', 'action': 'CHECK'}],
                    'decision_log': [{'time': '2026-07-02 14:30:00', 'trade_reason': '旧日志'}],
                }

            def enrich_portfolio(self, state):
                return {
                    'initial_cash': state['initial_cash'],
                    'cash': state['cash'],
                    'positions': [],
                    'trade_log': state['trade_log'],
                    'decision_log': state['decision_log'],
                }

        original_get_trader = dashboard.get_trader_module
        original_trading_day_status = dashboard.trading_day_status
        original_current_cn_datetime = dashboard.current_cn_datetime
        try:
            dashboard.get_trader_module = lambda: TraderStub()
            dashboard.current_cn_datetime = lambda: datetime(2026, 7, 3, 0, 5, 0)
            dashboard.trading_day_status = lambda value=None, **kwargs: {
                'date': value.strftime('%Y-%m-%d') if value else '',
                'is_trading_day': True,
            }

            payload = dashboard.get_practice_payload_fast()
        finally:
            dashboard.get_trader_module = original_get_trader
            dashboard.trading_day_status = original_trading_day_status
            dashboard.current_cn_datetime = original_current_cn_datetime

        self.assertEqual(payload['current_date'], '2026-07-03')
        self.assertEqual(payload['current_time'], '2026-07-03 00:05:00')
        self.assertEqual(payload['trading_calendar']['date'], '2026-07-03')
        self.assertEqual([row['time'] for row in payload['trade_log']], ['2026-07-03 00:03:00'])
        self.assertEqual(payload['decision_log'], [])

    def test_compact_strategy_performance_truncates_exit_details(self):
        perf = {
            'summary': {'total_pnl': 100},
            'exit_rule': {
                'stop_loss': {
                    'trades': 20,
                    'items': [{'code': f'{i:06d}', 'pnl': i} for i in range(20)],
                },
            },
        }

        compacted = dashboard.compact_strategy_performance(perf, max_exit_items=5)
        items = compacted['exit_rule']['stop_loss']['items']

        self.assertEqual(len(items), 5)
        self.assertEqual(items[0]['code'], '000015')
        self.assertEqual(items[-1]['code'], '000019')
        self.assertEqual(compacted['exit_rule']['stop_loss']['items_truncated'], 15)

    def test_filter_today_log_entries_keeps_today_items(self):
        rows = [
            {'time': '2026-07-03 09:31:00', 'action': 'BUY', 'code': '600000'},
            {'time': '2026-01-01 09:31:00', 'action': 'SELL', 'code': '000001'},
            {'time': '2026-07-03 10:00:00', 'trade_reason': '测试决策'},
        ]

        filtered = dashboard.filter_today_log_entries(rows, now=datetime(2026, 7, 3, 0, 5, 0))

        self.assertEqual([row['time'] for row in filtered], [rows[0]['time'], rows[2]['time']])

    def test_fast_practice_payload_uses_trader_trade_rule_note(self):
        class FakeTrader:
            MODEL = 'gpt-regression-test'
            PROVIDER_DISPLAY_NAME = 'provider-regression-test'

            def load_state(self):
                return {'equity_history': [], 'daily_equity_history': []}

            def enrich_portfolio(self, state):
                return {
                    'positions': [],
                    'trade_log': [],
                    'decision_log': [],
                    'cash': 1000000,
                    'total_equity': 1000000,
                }

            def track_strategy_performance(self, state):
                return {}

            def build_trade_rule_note(self):
                return '统一风控说明'

        original_get_trader_module = dashboard.get_trader_module
        original_trading_day_status = dashboard.trading_day_status
        try:
            dashboard.get_trader_module = lambda: FakeTrader()
            dashboard.trading_day_status = lambda *args, **kwargs: {'is_trading_day': True, 'date': '2026-06-24'}

            payload = dashboard.get_practice_payload_fast()
        finally:
            dashboard.get_trader_module = original_get_trader_module
            dashboard.trading_day_status = original_trading_day_status

        self.assertEqual(payload['trade_rule_note'], '统一风控说明')
        self.assertEqual(payload['decision_model'], 'gpt-regression-test')
        self.assertEqual(payload['decision_provider'], 'provider-regression-test')
        self.assertEqual(payload['snapshot_mode'], 'fast')

    def test_fast_practice_payload_includes_compact_multi_day_calendar_history(self):
        old_points = [
            {'time': '2026-06-25 09:30:00', 'equity': 1000000},
            {'time': '2026-06-25 10:00:00', 'equity': 1000200},
            {'time': '2026-06-25 15:00:00', 'equity': 1000100},
        ]
        latest_points = [
            {'time': '2026-06-26 09:30:00', 'equity': 1000100},
            {'time': '2026-06-26 10:00:00', 'equity': 1000300},
            {'time': '2026-06-26 15:00:00', 'equity': 1000400},
        ]

        class FakeTrader:
            def load_state(self):
                return {
                    'updated_at': '2026-06-26 15:00:10',
                    'equity_history': old_points + latest_points,
                    'daily_equity_history': [],
                    'trade_log': [],
                }

            def enrich_portfolio(self, state):
                return {
                    'generated_at': '2026-06-26 15:00:11',
                    'positions': [],
                    'trade_log': [],
                    'decision_log': [],
                    'cash': 1000400,
                    'total_equity': 1000400,
                }

            def track_strategy_performance(self, state):
                return {}

        original_get_trader_module = dashboard.get_trader_module
        original_current_cn_datetime = dashboard.current_cn_datetime
        try:
            dashboard.get_trader_module = lambda: FakeTrader()
            dashboard.current_cn_datetime = lambda: datetime(2026, 6, 26, 15, 1, 0)

            payload = dashboard.get_practice_payload_fast()
        finally:
            dashboard.get_trader_module = original_get_trader_module
            dashboard.current_cn_datetime = original_current_cn_datetime

        self.assertEqual(payload['equity_history'], latest_points)
        self.assertEqual(set(payload['calendar_history']['days']), {'2026-06-25', '2026-06-26'})
        self.assertGreaterEqual(len(payload['calendar_history']['days']['2026-06-25']), 2)
        self.assertEqual(payload['calendar_history']['schema_version'], 1)
        self.assertEqual(payload['snapshot_mode'], 'fast')
        self.assertEqual(payload['equity_history_scope'], 'latest_day')
        self.assertEqual(payload['snapshot_meta']['source_updated_at'], '2026-06-26 15:00:10')

    def test_index_template_has_scrollable_practice_operation_log(self):
        self.assertIn('function renderPracticeOperationLog(payload)', dashboard.INDEX_HTML)
        self.assertIn('class="practice-log-scroll"', dashboard.INDEX_HTML)
        self.assertIn('overflow-y:auto', dashboard.INDEX_HTML)
        self.assertIn('aria-label="当日所有操作日志"', dashboard.INDEX_HTML)

    def test_index_template_can_open_full_practice_log_modal(self):
        self.assertIn('let practiceLogDetailKey = \'\';', dashboard.INDEX_HTML)
        self.assertIn('data-practice-log-key=', dashboard.INDEX_HTML)
        self.assertIn('function renderPracticeLogDetailModal(payload)', dashboard.INDEX_HTML)
        self.assertIn('function practiceLogRawText(item)', dashboard.INDEX_HTML)
        self.assertIn('class="practice-log-detail-backdrop"', dashboard.INDEX_HTML)
        self.assertIn('class="practice-log-detail-text"', dashboard.INDEX_HTML)
        self.assertIn('data-practice-log-action="close"', dashboard.INDEX_HTML)
        self.assertIn('practiceLogDetailKey = logTrigger.dataset.practiceLogKey || \'\';', dashboard.INDEX_HTML)
        self.assertNotIn('practice-log-detail-json', dashboard.INDEX_HTML)
        self.assertNotIn('practice-log-detail-field', dashboard.INDEX_HTML)

    def test_index_template_hides_trade_rule_note_in_modal(self):
        self.assertIn('let practiceRuleNoteOpen = false', dashboard.INDEX_HTML)
        self.assertIn('function renderPracticeRuleNoteModal(note)', dashboard.INDEX_HTML)
        self.assertIn('data-practice-rule-action="open"', dashboard.INDEX_HTML)
        self.assertIn('class="practice-rule-backdrop"', dashboard.INDEX_HTML)
        self.assertIn('${ruleModal}', dashboard.INDEX_HTML)
        self.assertNotIn('${esc(p.trade_rule_note||', dashboard.INDEX_HTML)

    def test_non_message_tabs_request_message_counts_without_records(self):
        self.assertEqual(dashboard.clamp_limit('0'), 0)
        self.assertIn(
            "isMessageCategory() ? messagePageLimit() : 0",
            dashboard.INDEX_HTML,
        )

    def test_us_sector_api_returns_sector_snapshot(self):
        original_producer = dashboard.produce_us_sector_data
        try:
            dashboard.produce_us_sector_data = lambda: {
                "items": [{"symbol": "SMH", "label": "半导体", "change_pct": 1.2}],
                "generated_at": "2026-07-10 01:10:00",
            }
            handler = FakeHandler(path='/api/us_sectors')
            handler.do_GET()
            payload = json.loads(handler.wfile.getvalue().decode('utf-8'))
        finally:
            dashboard.produce_us_sector_data = original_producer

        self.assertEqual(handler.status, 200)
        self.assertEqual(payload["items"][0]["symbol"], "SMH")

    def test_indices_market_panel_switches_to_us_sectors_with_index_session(self):
        self.assertIn("let usSectorData = {items: []};", dashboard.INDEX_HTML)
        self.assertIn("fetch('/api/us_sectors')", dashboard.INDEX_HTML)
        self.assertIn('function indicesSwitchSession(aIndexItems = [])', dashboard.INDEX_HTML)
        self.assertIn("let indicesMarketRegionOverride = '';", dashboard.INDEX_HTML)
        self.assertIn('function resolvedIndicesMarketRegion(aIndexItems = [])', dashboard.INDEX_HTML)
        self.assertIn('function setIndicesMarketRegion(mode)', dashboard.INDEX_HTML)
        self.assertIn("const marketRegion = resolvedIndicesMarketRegion(aIndexItems);", dashboard.INDEX_HTML)
        self.assertIn("const marketUsesUsSectors = marketRegion === 'us';", dashboard.INDEX_HTML)
        self.assertIn('aria-label="行情市场切换"', dashboard.INDEX_HTML)
        self.assertIn('data-market-region="a_share"', dashboard.INDEX_HTML)
        self.assertIn('data-market-region="us"', dashboard.INDEX_HTML)
        self.assertIn("const activeTitleHtml = activePanel === 'index'", dashboard.INDEX_HTML)
        self.assertIn('${activeTitleHtml}${indexPrioritySwitchHtml}${marketRegionSwitchHtml}', dashboard.INDEX_HTML)
        self.assertNotIn('<h2 class="indices-part-title">${activeTitle}</h2>', dashboard.INDEX_HTML)
        self.assertNotIn('indicesMarketRegionOverride,\n      savedAt', dashboard.INDEX_HTML)
        self.assertIn('function renderUsSectorMarketBlock()', dashboard.INDEX_HTML)
        self.assertIn('function renderSectorCloudHeading(source)', dashboard.INDEX_HTML)
        self.assertIn('更新 ${esc(source.generated_at)}', dashboard.INDEX_HTML)
        self.assertIn('${renderSectorCloudHeading(sec)}', dashboard.INDEX_HTML)
        self.assertIn('${renderSectorCloudHeading(usSectorData)}', dashboard.INDEX_HTML)
        self.assertNotIn('<h3>美股板块涨跌幅', dashboard.INDEX_HTML)
        self.assertIn('rows.filter(row => Number.isFinite(row.pct) && row.pct > 0)', dashboard.INDEX_HTML)
        self.assertIn('rows.filter(row => Number.isFinite(row.pct) && row.pct < 0)', dashboard.INDEX_HTML)
        self.assertIn('暂无上涨板块', dashboard.INDEX_HTML)
        self.assertIn('暂无下跌板块', dashboard.INDEX_HTML)
        self.assertIn("s.a_share_mapping.slice(0, 3).join('、')", dashboard.INDEX_HTML)
        self.assertNotIn("`A股映射 ${s.a_share_mapping.slice(0, 3).join('、')}`", dashboard.INDEX_HTML)
        self.assertNotIn('const US_MARKET_QUOTE_SYMBOLS', dashboard.INDEX_HTML)

    def test_indices_panel_can_put_a_share_or_us_indices_first(self):
        self.assertIn("const INDICES_INDEX_PRIORITY_STATE_KEY = 'niuniu-dashboard-index-priority-v1';", dashboard.INDEX_HTML)
        self.assertIn("let indicesIndexPriorityOverride = '';", dashboard.INDEX_HTML)
        self.assertIn('function setIndicesIndexPriority(mode)', dashboard.INDEX_HTML)
        self.assertIn('function resolvedIndicesIndexPriority(aIndexItems = [])', dashboard.INDEX_HTML)
        self.assertIn("sessionStorage.setItem(INDICES_INDEX_PRIORITY_STATE_KEY, mode)", dashboard.INDEX_HTML)
        self.assertIn("const indexSections = indexPriority === 'a_share' ? [", dashboard.INDEX_HTML)
        self.assertIn("['A股指数', aIndexItems],\n      ['美股指数', usIndexItems],", dashboard.INDEX_HTML)
        self.assertIn("['美股指数', usIndexItems],\n      ['A股指数', aIndexItems],", dashboard.INDEX_HTML)
        self.assertIn('return [...indexSections, ...supportingSections]', dashboard.INDEX_HTML)
        self.assertIn('aria-label="指数排序切换"', dashboard.INDEX_HTML)
        self.assertIn('data-index-priority="a_share"', dashboard.INDEX_HTML)
        self.assertIn('data-index-priority="us"', dashboard.INDEX_HTML)
        self.assertIn('A股在上', dashboard.INDEX_HTML)
        self.assertIn('美股在上', dashboard.INDEX_HTML)
        self.assertIn('${activeTitleHtml}${indexPrioritySwitchHtml}${marketRegionSwitchHtml}', dashboard.INDEX_HTML)

    def test_index_template_github_button_links_to_repo_with_icon(self):
        self.assertIn(
            '<a class="header-link" href="https://github.com/kunkundi/niuone"',
            dashboard.INDEX_HTML,
        )
        self.assertIn('.settings-link, .header-link { align-items:center;', dashboard.INDEX_HTML)
        self.assertIn('.settings-link:hover, .header-link:hover', dashboard.INDEX_HTML)
        self.assertIn('rel="noopener noreferrer"', dashboard.INDEX_HTML)
        self.assertIn('<svg viewBox="0 0 16 16" aria-hidden="true"', dashboard.INDEX_HTML)
        self.assertNotIn('<span class="header-text" title="开源仓库">GitHub</span>', dashboard.INDEX_HTML)

    def test_index_template_inlines_trade_reasons_on_stock_cards(self):
        self.assertNotIn('买入战法绩效', dashboard.INDEX_HTML)
        self.assertNotIn('BUY_COLORS', dashboard.INDEX_HTML)
        self.assertNotIn('renderStrategyPerformance', dashboard.INDEX_HTML)
        self.assertNotIn('practice-perf', dashboard.INDEX_HTML)
        self.assertNotIn('exit-rule-row', dashboard.INDEX_HTML)
        self.assertIn('x.bought_today', dashboard.INDEX_HTML)
        self.assertIn('买入理由', dashboard.INDEX_HTML)
        self.assertIn('卖出归因', dashboard.INDEX_HTML)
        self.assertIn('最低/最高', dashboard.INDEX_HTML)
        self.assertNotIn('最低涨幅', dashboard.INDEX_HTML)
        self.assertNotIn('最高涨幅', dashboard.INDEX_HTML)
        self.assertIn('industryLabel = item.industry || item.sector || item.board', dashboard.INDEX_HTML)
        self.assertIn('${esc(industryLabel)}</span>', dashboard.INDEX_HTML)
        self.assertIn('white-space:nowrap', dashboard.INDEX_HTML)
        self.assertNotIn('所属板块', dashboard.INDEX_HTML)
        self.assertNotIn('板块 ${esc(industryLabel)}', dashboard.INDEX_HTML)
        self.assertIn('仓位占比', dashboard.INDEX_HTML)
        self.assertIn('可卖/持有', dashboard.INDEX_HTML)
        self.assertNotIn('${esc(x.qty)}股', dashboard.INDEX_HTML)
        self.assertIn('今日收益曲线', dashboard.INDEX_HTML)
        self.assertIn('isNonTradingCalendarDay', dashboard.INDEX_HTML)
        self.assertIn('tradingCalendar.is_trading_day === false', dashboard.INDEX_HTML)
        self.assertIn('（${esc(latestDay)}）', dashboard.INDEX_HTML)
        self.assertIn('currentDateKey', dashboard.INDEX_HTML)
        self.assertIn("timeZone: 'Asia/Shanghai'", dashboard.INDEX_HTML)
        self.assertIn('practicePayloadDateKey', dashboard.INDEX_HTML)
        self.assertIn('等待今日盘中净值点', dashboard.INDEX_HTML)
        self.assertIn('最近已有分时点', dashboard.INDEX_HTML)
        self.assertIn('收益曲线 · 累计收益', dashboard.INDEX_HTML)
        self.assertIn('practice-hover-tooltip', dashboard.INDEX_HTML)
        self.assertIn('practice-chart-hover-layer', dashboard.INDEX_HTML)
        self.assertIn('practiceHoverMove(event, this)', dashboard.INDEX_HTML)
        self.assertIn('touch-action:none', dashboard.INDEX_HTML)
        self.assertIn('data-practice-hover-points', dashboard.INDEX_HTML)
        self.assertIn("layer.classList.toggle('place-left'", dashboard.INDEX_HTML)
        self.assertIn("layer.classList.toggle('place-bottom'", dashboard.INDEX_HTML)
        self.assertIn('收益金额', dashboard.INDEX_HTML)
        self.assertIn('累计收益率', dashboard.INDEX_HTML)
        self.assertIn('当日收益率', dashboard.INDEX_HTML)
        self.assertIn('function renderPracticeTradeMarkers', dashboard.INDEX_HTML)
        self.assertIn('practiceTradeMarkersForDate', dashboard.INDEX_HTML)
        self.assertIn('practice-trade-marker-tooltip', dashboard.INDEX_HTML)
        self.assertIn("const side = trade.action === 'BUY' ? '买' : '卖';", dashboard.INDEX_HTML)
        self.assertIn('function renderPracticeTradeMarkerLine', dashboard.INDEX_HTML)
        self.assertIn('practice-trade-marker-side', dashboard.INDEX_HTML)
        self.assertIn('.practice-chart-card { position:relative; z-index:0; isolation:isolate; overflow:hidden;', dashboard.INDEX_HTML)
        self.assertIn('.practice-trade-marker { --marker-size:18px; --marker-radius:9px; appearance:none;', dashboard.INDEX_HTML)
        self.assertIn(
            'left:clamp(var(--marker-radius), var(--marker-x), calc(100% - var(--marker-radius)));',
            dashboard.INDEX_HTML,
        )
        self.assertIn('min-width:var(--marker-size); max-width:var(--marker-size);', dashboard.INDEX_HTML)
        self.assertIn(
            '.practice-calendar-day-curve-chart .practice-trade-marker { --marker-size:15px; --marker-radius:7.5px;',
            dashboard.INDEX_HTML,
        )
        self.assertIn('style="--marker-x:${xPct.toFixed(2)}%;top:${yPct.toFixed(2)}%"', dashboard.INDEX_HTML)
        self.assertIn('font-family:inherit; cursor:default;', dashboard.INDEX_HTML)
        self.assertNotIn('font-family:inherit; cursor:help;', dashboard.INDEX_HTML)
        self.assertIn('.practice-trade-marker-pnl.up { color:#ff6b6d; }', dashboard.INDEX_HTML)
        self.assertIn('.practice-trade-marker-pnl.down { color:#39d98a; }', dashboard.INDEX_HTML)
        self.assertIn('.practice-trade-marker.sell-partial { background:#f59e0b;', dashboard.INDEX_HTML)
        self.assertIn('.practice-trade-marker.sell-full { background:#ef4444;', dashboard.INDEX_HTML)
        self.assertIn("? 'sell-full'", dashboard.INDEX_HTML)
        self.assertIn("? 'sell-partial' : 'sell-mixed'", dashboard.INDEX_HTML)
        self.assertIn("trade.action === 'SELL' && trade.isFullExit", dashboard.INDEX_HTML)
        self.assertIn("${practiceTradeShareText(trade.shares)}股×${practiceTradePriceText(trade.price)}", dashboard.INDEX_HTML)
        self.assertIn('renderPracticeTradeMarkers(latestDay, xFromTime, plottedPts, w, h)', dashboard.INDEX_HTML)
        self.assertIn('const tradeMarkerHtml = renderPracticeTradeMarkers(', dashboard.INDEX_HTML)
        self.assertIn('class="practice-calendar-day-curve-chart"', dashboard.INDEX_HTML)
        self.assertNotIn('practice-hover-readout', dashboard.INDEX_HTML)
        self.assertNotIn('拖动查看收益曲线点位', dashboard.INDEX_HTML)
        self.assertNotIn('每日总收益', dashboard.INDEX_HTML)
        self.assertNotIn('if (points.length < 2) points = rawPoints.slice(-180);', dashboard.INDEX_HTML)
        self.assertIn('交易日历', dashboard.INDEX_HTML)
        self.assertIn('openPracticeCalendar(event)', dashboard.INDEX_HTML)
        self.assertIn('buildPracticeCalendarRows', dashboard.INDEX_HTML)
        self.assertIn('practice-calendar-popover', dashboard.INDEX_HTML)
        self.assertIn('practiceCalendarSelectedDate', dashboard.INDEX_HTML)
        self.assertIn('renderPracticeCalendarDayCurve', dashboard.INDEX_HTML)
        self.assertIn('practice-calendar-day-curve', dashboard.INDEX_HTML)
        self.assertIn('data-practice-calendar-date="${esc(date)}"', dashboard.INDEX_HTML)
        self.assertIn('data-practice-calendar-action="clear-day"', dashboard.INDEX_HTML)
        self.assertIn('data-practice-calendar-curve', dashboard.INDEX_HTML)
        self.assertIn('selectedCls = date === practiceCalendarSelectedDate', dashboard.INDEX_HTML)
        self.assertIn("practiceCalendarSelectedDate = practiceCalendarSelectedDate === nextDate ? '' : nextDate", dashboard.INDEX_HTML)
        self.assertIn('sessionDayPoints', dashboard.INDEX_HTML)
        self.assertIn('allDayHistoryPoints.at(-1)?.equity', dashboard.INDEX_HTML)
        self.assertIn("? '分时加载失败 · '", dashboard.INDEX_HTML)
        self.assertIn('practiceCalendarHistoryPoints(p)', dashboard.INDEX_HTML)
        self.assertIn('practiceCalendarHistoryCoversDate(p, date)', dashboard.INDEX_HTML)
        self.assertIn(
            'const needsFullHistory = isCurrentDate || (hasPartialHistory && !practiceCalendarHistoryCoversDate(p, date));',
            dashboard.INDEX_HTML,
        )
        self.assertIn('分时曲线加载中…', dashboard.INDEX_HTML)
        self.assertIn('分时曲线加载失败', dashboard.INDEX_HTML)
        self.assertIn("time: `${date} 15:00:00`", dashboard.INDEX_HTML)
        self.assertIn('const w = 464, h = 96', dashboard.INDEX_HTML)
        self.assertIn('0轴 ${prevPoint ? esc(String(prevPoint.time || \'\').slice(5, 16)) : \'初始资金\'}', dashboard.INDEX_HTML)
        self.assertIn('position:absolute; left:0; right:0; bottom:calc(100% + 8px)', dashboard.INDEX_HTML)
        self.assertIn('overflow:visible', dashboard.INDEX_HTML)
        self.assertIn('width:min(390px', dashboard.INDEX_HTML)
        self.assertIn('transform:translate(-50%,-50%)', dashboard.INDEX_HTML)
        self.assertNotIn('max-height:min(76vh, 640px); display:grid; gap:8px', dashboard.INDEX_HTML)
        self.assertNotIn('practice-calendar-popover::before', dashboard.INDEX_HTML)
        self.assertNotIn('filter:blur(18px)', dashboard.INDEX_HTML)
        self.assertIn('border:1px solid transparent', dashboard.INDEX_HTML)
        self.assertIn('linear-gradient(135deg, rgba(96,165,250,.68), rgba(124,92,255,.56) 48%, rgba(52,211,153,.32)) border-box', dashboard.INDEX_HTML)
        self.assertIn('background:linear-gradient(180deg, #172033, #101827)', dashboard.INDEX_HTML)
        self.assertIn('background:rgba(31,42,62,.72)', dashboard.INDEX_HTML)
        self.assertIn('practice-calendar-no-data', dashboard.INDEX_HTML)
        self.assertIn('grid-template-columns:repeat(5, minmax(0, 1.14fr)) repeat(2, minmax(30px, .72fr))', dashboard.INDEX_HTML)
        self.assertIn('dayOfWeek === 0 || dayOfWeek === 6', dashboard.INDEX_HTML)
        self.assertIn('practice-calendar-day.weekend', dashboard.INDEX_HTML)
        self.assertIn('practice-calendar-weekday.weekend', dashboard.INDEX_HTML)
        self.assertIn('weekendTodayMarker = isToday && isWeekend && !row', dashboard.INDEX_HTML)
        self.assertIn('inlineTodayMarker = isToday && !weekendTodayMarker', dashboard.INDEX_HTML)
        self.assertIn('class="practice-calendar-today weekend-today"', dashboard.INDEX_HTML)
        self.assertIn('grid-row:2; align-self:end; justify-self:start; padding:0 3px', dashboard.INDEX_HTML)
        self.assertNotIn('practice-calendar-day.weekend { min-height', dashboard.INDEX_HTML)
        self.assertNotIn('align-self:start', dashboard.INDEX_HTML)
        self.assertIn("${date}${isWeekend ? ' 周末' : ''}", dashboard.INDEX_HTML)
        self.assertIn('signedCellPct', dashboard.INDEX_HTML)
        self.assertIn('signedCellAmount', dashboard.INDEX_HTML)
        self.assertIn('aria-label="${esc(fullText)}"', dashboard.INDEX_HTML)
        self.assertIn('practice-calendar-grid', dashboard.INDEX_HTML)
        self.assertIn('data-practice-calendar-action="prev"', dashboard.INDEX_HTML)
        self.assertNotIn('practice-calendar-backdrop', dashboard.INDEX_HTML)
        self.assertNotIn('practiceCalendarAnchor', dashboard.INDEX_HTML)
        self.assertNotIn('practice-calendar-values empty', dashboard.INDEX_HTML)
        self.assertNotIn('<h3 class="practice-panel-title"><span>牛牛实战 · 模拟账户</span><button class="practice-calendar-open-btn"', dashboard.INDEX_HTML)
        self.assertIn('<h3>实战页面 · 模拟账户</h3>', dashboard.INDEX_HTML)
        self.assertNotIn('最近交易日收益', dashboard.INDEX_HTML)
        self.assertNotIn('getDay() === 0 || nowForCurve.getDay() === 6', dashboard.INDEX_HTML)

    def test_index_template_loads_calendar_history_without_waiting_for_full_snapshot(self):
        self.assertIn("const VIEW_STATE_KEY = 'niuniu-dashboard-view-state-v5';", dashboard.INDEX_HTML)
        self.assertIn("'/api/niuniu_practice?fast=1&calendar_schema=1'", dashboard.INDEX_HTML)
        self.assertIn("fetchJson('/api/niuniu_practice?snapshot_schema=2')", dashboard.INDEX_HTML)
        self.assertIn('const fullPracticePromise = practiceFullRequest;', dashboard.INDEX_HTML)
        self.assertIn('function mergePracticePayloadSnapshots', dashboard.INDEX_HTML)
        self.assertIn('function mergePracticeEquityRows', dashboard.INDEX_HTML)
        self.assertIn('function comparePracticePayloadFreshness', dashboard.INDEX_HTML)
        self.assertIn("String(payload.equity_history_scope || '') === 'unavailable'", dashboard.INDEX_HTML)
        self.assertNotIn("typeof payload !== 'object' || payload.last_error", dashboard.INDEX_HTML)
        self.assertIn('if (seq !== practiceLoadSeq) return;', dashboard.INDEX_HTML)
        self.assertIn("practiceFullSnapshotStatus = 'loading';", dashboard.INDEX_HTML)
        self.assertIn("practiceFullSnapshotStatus = 'loaded';", dashboard.INDEX_HTML)
        self.assertIn("practiceFullSnapshotStatus = 'error';", dashboard.INDEX_HTML)
        self.assertIn('function compactPracticeCalendarHistoryPoints', dashboard.INDEX_HTML)
        self.assertIn('calendar.complete !== true', dashboard.INDEX_HTML)
        self.assertIn('buildPracticeCalendarRows(practiceCalendarHistoryPoints(p)', dashboard.INDEX_HTML)
        self.assertIn('renderPracticeCurve(p.equity_history || []', dashboard.INDEX_HTML)

    def test_index_template_uses_practice_as_the_canonical_page_name(self):
        self.assertIn("let activeCategory = initialParams.get('category') || 'practice';", dashboard.INDEX_HTML)
        self.assertIn("const CATEGORY_ORDER = ['practice', 'indices', 'market_monitor', 'x_monitor', 'us_ratings'];", dashboard.INDEX_HTML)
        self.assertIn("practice:'实战页面'", dashboard.INDEX_HTML)
        self.assertIn("const LEGACY_CATEGORY_ALIASES = {b1_screen:'practice'};", dashboard.INDEX_HTML)
        self.assertIn('const normalized = LEGACY_CATEGORY_ALIASES[category] || category;', dashboard.INDEX_HTML)
        self.assertIn("fetchJson('/api/practice_candidates')", dashboard.INDEX_HTML)
        self.assertIn("actionFetch('/api/practice_candidates/refresh')", dashboard.INDEX_HTML)
        self.assertIn('async function loadPracticePage()', dashboard.INDEX_HTML)
        self.assertIn('function renderPracticePage()', dashboard.INDEX_HTML)
        self.assertIn("initialParams.get('category') !== activeCategory", dashboard.INDEX_HTML)
        self.assertNotIn('function loadB1Screen', dashboard.INDEX_HTML)
        self.assertNotIn('function renderB1Screen', dashboard.INDEX_HTML)
        self.assertNotIn("fetchJson('/api/b1_screen')", dashboard.INDEX_HTML)
        self.assertNotIn("actionFetch('/api/b1_screen/trigger')", dashboard.INDEX_HTML)

    def test_index_snapshot_merge_handles_business_errors_and_stale_full_responses(self):
        start = dashboard.INDEX_HTML.index('function mergePracticeTimedRows')
        end = dashboard.INDEX_HTML.index('async function loadPracticePage', start)
        functions = dashboard.INDEX_HTML[start:end]
        scenario = r"""
const fast = {
  snapshot_mode:'fast', equity_history_scope:'latest_day', source_updated_at:'2026-07-10 15:00:00',
  source_last_equity_time:'2026-07-10 10:00:00', current_time:'2026-07-10 15:00:02',
  total_equity:1001, cash:500, last_error:'模型暂时不可用',
  equity_history:[{time:'2026-07-10 10:00:00', equity:1001}],
  daily_equity_history:[{time:'2026-07-10 15:00:00', equity:1001}],
};
const full = {
  snapshot_mode:'full', equity_history_scope:'retained_history', source_updated_at:'2026-07-10 15:00:00',
  source_last_equity_time:'2026-07-10 10:00:00', current_time:'2026-07-10 14:59:59',
  total_equity:999, cash:499,
  equity_history:[
    {time:'2026-07-09 15:00:00', equity:990},
    {time:'2026-07-10 09:59:00', equity:998},
    {time:'2026-07-10 10:00:00', equity:999},
  ],
  daily_equity_history:[{time:'2026-07-10 14:59:00', equity:999}],
};
const sameSource = mergePracticePayloadSnapshots(fast, full);
const legacyErrorShell = {
  positions:[], cash:0, total_equity:0, initial_cash:0, equity_history:[], last_error:'endpoint failed',
};
const newerFast = {
  ...fast, source_updated_at:'2026-07-10 16:00:00', source_last_equity_time:'2026-07-10 11:00:00',
  current_time:'2026-07-10 16:00:01', total_equity:1010,
  equity_history:[{time:'2026-07-10 11:00:00', equity:1010}],
};
const staleFull = {
  ...full, equity_history:[
    {time:'2026-07-09 15:00:00', equity:990},
    {time:'2026-07-10 09:30:00', equity:995},
    {time:'2026-07-11 09:30:00', equity:2000},
  ],
};
const newerSource = mergePracticePayloadSnapshots(newerFast, staleFull);
const compactFast = {
  ...newerFast,
  calendar_history:{schema_version:1, complete:true, days:{'2026-07-09':[{clock:'15:00:00', equity:990}]}},
};
const compactAuthoritative = mergePracticePayloadSnapshots(compactFast, staleFull);
const manyOld = {
  ...staleFull,
  equity_history:Array.from({length:2500}, (_, idx) => ({time:`2026-07-09 ${String(idx).padStart(5, '0')}`, equity:idx})),
};
const capped = mergePracticePayloadSnapshots(newerFast, manyOld);
const modelRefresh = mergePracticePayloadSnapshots(
  {...full, decision_model:'deepseek-v4-pro', decision_provider:'old-provider'},
  {...fast, decision_model:'gpt-regression-test', decision_provider:'new-provider'},
);
process.stdout.write(JSON.stringify({
  businessErrorUsable:isUsablePracticePayload(fast),
  unavailableRejected:!isUsablePracticePayload({...fast, equity_history_scope:'unavailable'}),
  legacyErrorShellRejected:!isUsablePracticePayload(legacyErrorShell),
  fullWinsSameSource:sameSource.total_equity === 999 && sameSource.equity_history_scope === 'retained_history',
  staleSameDayDropped:JSON.stringify(newerSource.equity_history.map(row => row.time)) === JSON.stringify(['2026-07-09 15:00:00', '2026-07-10 11:00:00']),
  compactDateNotRehydrated:JSON.stringify(compactAuthoritative.equity_history.map(row => row.time)) === JSON.stringify(['2026-07-10 11:00:00']),
  newerErrorPreserved:newerSource.last_error === '模型暂时不可用',
  historyCapped:capped.equity_history.length === 2000 && capped.equity_history.at(-1).time === '2026-07-10 11:00:00',
  modelMetadataUsesIncoming:modelRefresh.decision_model === 'gpt-regression-test' && modelRefresh.decision_provider === 'new-provider',
}));
"""
        result = subprocess.run(
            ['node', '-e', functions + scenario],
            check=True,
            capture_output=True,
            text=True,
        )
        checks = json.loads(result.stdout)

        self.assertTrue(all(checks.values()), checks)

    def test_index_template_does_not_guess_missing_decision_model(self):
        self.assertIn("const decisionModel = String(p.decision_model || '').trim();", dashboard.INDEX_HTML)
        self.assertIn("practiceFullSnapshotStatus === 'error' ? '未知' : '加载中'", dashboard.INDEX_HTML)
        self.assertIn('delete niuniuPracticeData.decision_model;', dashboard.INDEX_HTML)
        self.assertIn('delete niuniuPracticeData.decision_provider;', dashboard.INDEX_HTML)
        self.assertIn("{cache: 'no-cache'}", dashboard.INDEX_HTML)
        self.assertNotIn("p.decision_model || 'deepseek-v4-pro'", dashboard.INDEX_HTML)

    def test_cache_invalidation_prevents_inflight_model_snapshot_from_repopulating_cache(self):
        cache_key = dashboard.PRACTICE_FAST_CACHE_KEY
        producer_started = dashboard.threading.Event()
        release_producer = dashboard.threading.Event()
        result = {}

        def old_model_producer():
            producer_started.set()
            self.assertTrue(release_producer.wait(timeout=2))
            return {'decision_model': 'deepseek-v4-pro'}

        def populate_old_model():
            result['payload'], result['hit'] = dashboard.cache_get_json(cache_key, 60, old_model_producer)

        worker = dashboard.threading.Thread(target=populate_old_model)
        worker.start()
        self.assertTrue(producer_started.wait(timeout=2))
        dashboard.invalidate_api_cache(cache_key)
        release_producer.set()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertFalse(result['hit'])
        self.assertNotIn(cache_key, dashboard.API_RESPONSE_CACHE)

        payload, hit = dashboard.cache_get_json(
            cache_key,
            60,
            lambda: {'decision_model': 'gpt-regression-test'},
        )
        self.assertFalse(hit)
        self.assertEqual(json.loads(payload)['decision_model'], 'gpt-regression-test')

    def test_index_template_intraday_curve_renders_single_point_from_opening_base(self):
        self.assertIn('if (rawPoints.length < (isDailyMode ? 2 : 1))', dashboard.INDEX_HTML)
        self.assertIn('if (sessionPoints.length >= 1)', dashboard.INDEX_HTML)
        self.assertIn('isNonTradingCalendarDay && dayPoints.length >= 2', dashboard.INDEX_HTML)
        self.assertIn('if (points.length < 1)', dashboard.INDEX_HTML)
        self.assertIn(
            'if (points.length < 2) return \'<div class="empty" style="padding:18px">累计收益等待更多交易日净值点…</div>\';',
            dashboard.INDEX_HTML,
        )
        self.assertIn(
            'const hasIntradayOpenBase = !isDailyMode && Number.isFinite(intradayBaseEquity) && intradayBaseEquity > 0;',
            dashboard.INDEX_HTML,
        )
        self.assertIn(
            'const chartBase = isDailyMode ? initialCash : (hasIntradayOpenBase ? intradayBaseEquity : vals[0]);',
            dashboard.INDEX_HTML,
        )
        self.assertIn('const axisPcts = hasIntradayOpenBase ? [0, ...chartPcts] : chartPcts;', dashboard.INDEX_HTML)
        self.assertIn('const openAnchor = [left, y(0)];', dashboard.INDEX_HTML)
        self.assertIn('pts.unshift(openAnchor);', dashboard.INDEX_HTML)
        self.assertIn('hasSyntheticOpenAnchor = true;', dashboard.INDEX_HTML)
        self.assertIn(
            '} else if (!isDailyMode && points.length > 1 && pts.length > 0 && pts[0][0] > left + 1) {',
            dashboard.INDEX_HTML,
        )
        self.assertIn('const hasCurveSegment = pts.length > 1;', dashboard.INDEX_HTML)
        self.assertIn('const drawdownVals = hasIntradayOpenBase ? [chartBase, ...vals] : vals;', dashboard.INDEX_HTML)
        self.assertIn('time: `${latestDay} 09:30:00`', dashboard.INDEX_HTML)
        self.assertIn('const intradayBaseLabel = hasIntradayOpenBase', dashboard.INDEX_HTML)

    def test_configured_admin_password_issues_secure_session_and_unlocks_settings(self):
        dashboard.ADMIN_PASSWORD = '管理员密码'

        locked_api = FakeHandler(path='/api/admin/config')
        locked_api.do_GET()
        self.assertEqual(locked_api.status, 403)

        wrong_body = urllib.parse.urlencode({'admin_password': '错误密码'}).encode('utf-8')
        wrong = FakeHandler(
            path='/admin/password',
            method='POST',
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Content-Length': str(len(wrong_body)),
            },
            body=wrong_body,
        )
        wrong.do_POST()
        self.assertEqual(wrong.status, 403)
        self.assertIn('管理员凭据错误', wrong.wfile.getvalue().decode('utf-8'))

        password_body = urllib.parse.urlencode({'admin_password': '管理员密码'}).encode('utf-8')
        login = FakeHandler(
            path='/admin/password',
            method='POST',
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Content-Length': str(len(password_body)),
                'X-Forwarded-Proto': 'https',
                'CF-Connecting-IP': '203.0.113.11',
            },
            body=password_body,
            ip='127.0.0.1',
        )
        login.do_POST()

        self.assertEqual(login.status, 303)
        self.assertEqual(login.header('Location'), '/admin')
        set_cookie = login.header('Set-Cookie') or ''
        self.assertTrue(set_cookie.startswith(f'{dashboard.ADMIN_SESSION_COOKIE_NAME}=ad_'))
        self.assertIn('HttpOnly', set_cookie)
        self.assertIn('SameSite=Lax', set_cookie)
        self.assertIn('Secure', set_cookie)
        session_cookie = set_cookie.split(';', 1)[0]

        unlocked_page = FakeHandler(path='/admin', headers={'Cookie': session_cookie})
        unlocked_page.do_GET()
        self.assertEqual(unlocked_page.status, 200)
        self.assertIn('<h1>设置</h1>', unlocked_page.wfile.getvalue().decode('utf-8'))

        unlocked_api = FakeHandler(path='/api/admin/config', headers={'Cookie': session_cookie})
        unlocked_api.do_GET()
        self.assertEqual(unlocked_api.status, 200)
        self.assertTrue(json.loads(unlocked_api.wfile.getvalue().decode('utf-8'))['items'])

        dashboard.ADMIN_PASSWORD = 'rotated-pass'
        expired_by_rotation = FakeHandler(path='/api/admin/config', headers={'Cookie': session_cookie})
        expired_by_rotation.do_GET()
        self.assertEqual(expired_by_rotation.status, 403)

    def test_admin_login_rate_limit_cannot_be_bypassed_with_spoofed_forwarded_ips(self):
        original_limit = dashboard.RATE_LIMIT_ADMIN_LOGIN
        dashboard.RATE_LIMIT_ADMIN_LOGIN = 1
        dashboard.RATE_LIMIT_BUCKETS.clear()
        body = urllib.parse.urlencode({'admin_password': 'wrong'}).encode('utf-8')
        try:
            first = FakeHandler(
                path='/admin/password',
                method='POST',
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Content-Length': str(len(body)),
                    'CF-Connecting-IP': '203.0.113.10',
                },
                body=body,
                ip='127.0.0.1',
            )
            first.do_POST()
            self.assertEqual(first.status, 403)

            spoofed = FakeHandler(
                path='/admin/password',
                method='POST',
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Content-Length': str(len(body)),
                    'CF-Connecting-IP': '198.51.100.22',
                },
                body=body,
                ip='127.0.0.1',
            )
            spoofed.do_POST()
            self.assertEqual(spoofed.status, 429)
        finally:
            dashboard.RATE_LIMIT_ADMIN_LOGIN = original_limit
            dashboard.RATE_LIMIT_BUCKETS.clear()

    def test_password_rotation_through_config_invalidates_old_session_immediately(self):
        dashboard.ADMIN_PASSWORD = 'old-pass'
        os.environ['DASHBOARD_ADMIN_PASSWORD'] = 'old-pass'
        dashboard.DASHBOARD_ENV_FILE.write_text(
            'DASHBOARD_ADMIN_PASSWORD=old-pass\n',
            encoding='utf-8',
        )
        old_cookie = self.admin_cookie()
        body = urllib.parse.urlencode({
            'env__DASHBOARD_ADMIN_PASSWORD': 'new-pass',
        }).encode('utf-8')

        rotate = FakeHandler(
            path='/api/admin/config/env',
            method='POST',
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Content-Length': str(len(body)),
                'Cookie': old_cookie,
                dashboard.ACTION_HEADER_NAME: '1',
            },
            body=body,
        )
        rotate.do_POST()

        self.assertEqual(rotate.status, 200)
        payload = json.loads(rotate.wfile.getvalue().decode('utf-8'))
        self.assertTrue(payload['reauth_required'])
        self.assertIn('admin_password', payload['runtime']['applied'])
        self.assertEqual(dashboard.ADMIN_PASSWORD, 'new-pass')
        self.assertEqual(
            dashboard.parse_env_file(dashboard.DASHBOARD_ENV_FILE)['DASHBOARD_ADMIN_PASSWORD'],
            'new-pass',
        )
        self.assertFalse(dashboard.verify_admin_credential('old-pass'))
        self.assertTrue(dashboard.verify_admin_credential('new-pass'))
        self.assertFalse(dashboard.validate_admin_session(old_cookie.split('=', 1)[1]))

        rejected_old_session = FakeHandler(
            path='/api/admin/config',
            headers={'Cookie': old_cookie},
        )
        rejected_old_session.do_GET()
        self.assertEqual(rejected_old_session.status, 403)

    def test_blank_password_uses_private_bootstrap_key_and_sessions_expire(self):
        self.assertEqual(dashboard.ADMIN_PASSWORD, '')
        token = dashboard.get_or_create_admin_token()
        self.assertTrue(token.startswith('na_'))
        self.assertEqual(dashboard.ADMIN_TOKEN_FILE.stat().st_mode & 0o777, 0o600)
        dashboard.ADMIN_TOKEN_FILE.chmod(0o644)
        self.assertEqual(dashboard.get_or_create_admin_token(), token)
        self.assertEqual(dashboard.ADMIN_TOKEN_FILE.stat().st_mode & 0o777, 0o600)
        self.assertFalse(dashboard.verify_admin_credential('错误凭据'))

        body = urllib.parse.urlencode({'admin_password': token}).encode('utf-8')
        login = FakeHandler(
            path='/admin/password',
            method='POST',
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Content-Length': str(len(body)),
            },
            body=body,
        )
        login.do_POST()
        self.assertEqual(login.status, 303)

        session = dashboard.new_admin_session(now=1_000)
        self.assertTrue(dashboard.validate_admin_session(session, now=1_001))
        self.assertFalse(dashboard.validate_admin_session(session + 'tampered', now=1_001))
        self.assertFalse(dashboard.validate_admin_session('ad_1000.AAAAAAAAAAAAAAAAAAAAAAAA.é', now=1_001))
        self.assertFalse(
            dashboard.validate_admin_session(
                session,
                now=1_000 + dashboard.ADMIN_SESSION_TTL_SECONDS + 1,
            )
        )

    def test_admin_login_page_sends_security_headers_without_session_cookie(self):
        handler = FakeHandler(
            path='/admin',
            headers={'X-Forwarded-Proto': 'https', 'CF-Connecting-IP': '203.0.113.11'},
            ip='127.0.0.1',
        )
        handler.do_GET()

        self.assertEqual(handler.status, 200)
        self.assertIsNone(handler.header('Set-Cookie'))
        self.assertEqual(handler.header('X-Frame-Options'), 'DENY')
        self.assertEqual(handler.header('X-Content-Type-Options'), 'nosniff')
        self.assertIn('max-age=31536000', handler.header('Strict-Transport-Security') or '')

    def test_forwarded_headers_are_only_trusted_from_configured_proxies(self):
        untrusted = FakeHandler(
            path='/admin',
            headers={'CF-Connecting-IP': '203.0.113.10', 'X-Forwarded-Proto': 'https'},
            ip='198.51.100.44',
        )
        self.assertEqual(untrusted.client_ip(), '198.51.100.44')
        self.assertFalse(untrusted.is_secure_request())

        trusted = FakeHandler(
            path='/admin',
            headers={'CF-Connecting-IP': '203.0.113.10', 'X-Forwarded-Proto': 'https'},
            ip='127.0.0.1',
        )
        self.assertEqual(trusted.client_ip(), '203.0.113.10')
        self.assertTrue(trusted.is_secure_request())

    def test_public_rate_limit_returns_429(self):
        original_anon_limit = dashboard.RATE_LIMIT_ANON
        dashboard.RATE_LIMIT_ANON = 1
        try:
            first = FakeHandler(path='/', ip='203.0.113.77')
            first.do_GET()
            second = FakeHandler(path='/', ip='203.0.113.77')
            second.do_GET()

            self.assertEqual(first.status, 200)
            self.assertEqual(second.status, 429)
        finally:
            dashboard.RATE_LIMIT_ANON = original_anon_limit

    def test_send_payload_gzips_large_json_when_client_accepts_it(self):
        payload = json.dumps({"items": ["牛" * 50 for _ in range(200)]}, ensure_ascii=False).encode("utf-8")
        handler = FakeHandler(path="/api/messages", headers={"Accept-Encoding": "br, gzip"})

        handler.send_payload(payload)

        body = handler.wfile.getvalue()
        self.assertEqual(handler.header("Content-Encoding"), "gzip")
        self.assertIn("Accept-Encoding", handler.header("Vary") or "")
        self.assertLess(len(body), len(payload))
        self.assertEqual(gzip.decompress(body), payload)

    def test_practice_candidates_cache_prefers_multi_strategy_and_falls_back_to_legacy(self):
        original_multi_strategy_cache_file = dashboard.MULTI_STRATEGY_CACHE_FILE
        original_b1_cache_file = dashboard.B1_CACHE_FILE
        dashboard.MULTI_STRATEGY_CACHE_FILE = self.tmp_path / 'multi_strategy_latest.json'
        dashboard.B1_CACHE_FILE = self.tmp_path / 'b1_screen_latest.json'
        try:
            dashboard.B1_CACHE_FILE.write_text(
                json.dumps({'candidates': [{'code': 'legacy'}], 'generated_at': 'legacy'}),
                encoding='utf-8',
            )
            dashboard.MULTI_STRATEGY_CACHE_FILE.write_text(
                json.dumps({'items': [{'code': 'multi'}], 'generated_at': 'multi'}),
                encoding='utf-8',
            )

            preferred = dashboard.load_practice_candidates_cache()
            self.assertEqual(preferred['items'], [{'code': 'multi'}])
            self.assertEqual(preferred['count'], 1)
            self.assertEqual(preferred['generated_at'], 'multi')

            dashboard.MULTI_STRATEGY_CACHE_FILE.unlink()
            fallback = dashboard.load_practice_candidates_cache()
            self.assertEqual(fallback['items'], [{'code': 'legacy'}])
            self.assertEqual(fallback['count'], 1)
            self.assertEqual(fallback['generated_at'], 'legacy')
        finally:
            dashboard.MULTI_STRATEGY_CACHE_FILE = original_multi_strategy_cache_file
            dashboard.B1_CACHE_FILE = original_b1_cache_file

    def test_practice_candidates_api_uses_canonical_cache_for_legacy_alias(self):
        original_loader = dashboard.load_practice_candidates_cache
        calls = []
        expected = {'items': [{'code': '000001'}], 'count': 1, 'generated_at': '2026-07-10 10:00:00'}
        try:
            dashboard.load_practice_candidates_cache = lambda: calls.append(True) or expected

            canonical = FakeHandler(path='/api/practice_candidates')
            canonical.do_GET()
            legacy = FakeHandler(path='/api/b1_screen')
            legacy.do_GET()

            self.assertEqual(canonical.status, 200)
            self.assertEqual(legacy.status, 200)
            self.assertEqual(json.loads(canonical.wfile.getvalue().decode('utf-8')), expected)
            self.assertEqual(legacy.wfile.getvalue(), canonical.wfile.getvalue())
            self.assertEqual(canonical.header('X-Dashboard-Cache'), 'MISS')
            self.assertEqual(legacy.header('X-Dashboard-Cache'), 'HIT')
            self.assertEqual(calls, [True])
            self.assertIn(dashboard.PRACTICE_CANDIDATES_CACHE_KEY, dashboard.API_RESPONSE_CACHE)
            self.assertNotIn('b1_screen', dashboard.API_RESPONSE_CACHE)

            for path in ('/api/practice_candidates?force=1', '/api/b1_screen?force=1'):
                with self.subTest(path=path):
                    force_get = FakeHandler(path=path)
                    force_get.do_GET()
                    self.assertEqual(force_get.status, 405)
                    self.assertEqual(force_get.header('Allow'), 'POST')
        finally:
            dashboard.load_practice_candidates_cache = original_loader

    def test_state_changing_api_requires_post_action_header(self):
        original_admin_limit = dashboard.RATE_LIMIT_ADMIN
        original_trigger = dashboard.trigger_b1_scan
        calls = []
        try:
            dashboard.RATE_LIMIT_ADMIN = 100
            dashboard.trigger_b1_scan = lambda force=False: calls.append(force) or {'ok': True, 'forced': force}
            admin_cookie = self.admin_cookie()

            unauthorized = FakeHandler(
                path='/api/practice_candidates/refresh',
                method='POST',
                headers={'Content-Length': '0', dashboard.ACTION_HEADER_NAME: '1'},
            )
            unauthorized.do_POST()
            self.assertEqual(unauthorized.status, 403)
            self.assertEqual(
                json.loads(unauthorized.wfile.getvalue().decode('utf-8'))['error'],
                'admin_password_required',
            )
            self.assertEqual(calls, [])

            get_handler = FakeHandler(path='/api/practice_candidates/refresh')
            get_handler.do_GET()
            self.assertEqual(get_handler.status, 405)
            self.assertEqual(get_handler.header('Allow'), 'POST')

            missing_header = FakeHandler(
                path='/api/practice_candidates/refresh',
                method='POST',
                headers={'Content-Length': '0', 'Cookie': admin_cookie},
            )
            missing_header.do_POST()
            self.assertEqual(missing_header.status, 403)
            self.assertEqual(json.loads(missing_header.wfile.getvalue().decode('utf-8'))['error'], 'action_header_required')
            self.assertEqual(calls, [])

            ok = FakeHandler(
                path='/api/practice_candidates/refresh',
                method='POST',
                headers={
                    'Content-Length': '0',
                    'Cookie': admin_cookie,
                    dashboard.ACTION_HEADER_NAME: '1',
                },
            )
            ok.do_POST()
            self.assertEqual(ok.status, 200)
            self.assertEqual(json.loads(ok.wfile.getvalue().decode('utf-8'))['forced'], True)
            self.assertEqual(calls, [True])

            legacy = FakeHandler(
                path='/api/b1_screen/trigger',
                method='POST',
                headers={
                    'Content-Length': '0',
                    'Cookie': admin_cookie,
                    dashboard.ACTION_HEADER_NAME: '1',
                },
            )
            legacy.do_POST()
            self.assertEqual(legacy.status, 200)
            self.assertEqual(json.loads(legacy.wfile.getvalue().decode('utf-8'))['forced'], True)
            self.assertEqual(calls, [True, True])

            legacy_force = FakeHandler(
                path='/api/b1_screen?force=1',
                method='POST',
                headers={
                    'Content-Length': '0',
                    'Cookie': admin_cookie,
                    dashboard.ACTION_HEADER_NAME: '1',
                },
            )
            legacy_force.do_POST()
            self.assertEqual(legacy_force.status, 200)
            self.assertEqual(json.loads(legacy_force.wfile.getvalue().decode('utf-8'))['forced'], True)
            self.assertEqual(calls, [True, True, True])

            legacy_without_force = FakeHandler(
                path='/api/b1_screen',
                method='POST',
                headers={
                    'Content-Length': '0',
                    'Cookie': admin_cookie,
                    dashboard.ACTION_HEADER_NAME: '1',
                },
            )
            legacy_without_force.do_POST()
            self.assertEqual(legacy_without_force.status, 404)
            self.assertEqual(calls, [True, True, True])
        finally:
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit
            dashboard.trigger_b1_scan = original_trigger

    def test_unauthenticated_config_writes_are_rejected_before_reading_body(self):
        original_config_path = dashboard.CONFIG_PATH
        dashboard.CONFIG_PATH = self.tmp_path / 'config.yaml'
        dashboard.DASHBOARD_ENV_FILE.write_text('DASHBOARD_GROK_MODEL=safe\n', encoding='utf-8')
        dashboard.CONFIG_PATH.write_text('model:\n  default: safe\n', encoding='utf-8')
        try:
            cases = (
                ('/admin/config/env', b'env__DASHBOARD_GROK_MODEL=attacker'),
                ('/api/admin/config/env', b'env__DASHBOARD_GROK_MODEL=attacker'),
                ('/admin/config/yaml', b'config_yaml=model%3A+attacker'),
                ('/api/admin/config/yaml', b'config_yaml=model%3A+attacker'),
            )
            for path, body in cases:
                with self.subTest(path=path):
                    handler = FakeHandler(
                        path=path,
                        method='POST',
                        headers={
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'Content-Length': str(len(body)),
                            dashboard.ACTION_HEADER_NAME: '1',
                        },
                        body=body,
                    )
                    handler.do_POST()
                    self.assertEqual(handler.rfile.tell(), 0)
                    if path.startswith('/api/'):
                        self.assertEqual(handler.status, 403)
                        self.assertEqual(
                            json.loads(handler.wfile.getvalue().decode('utf-8'))['error'],
                            'admin_password_required',
                        )
                    else:
                        self.assertEqual(handler.status, 200)
                        self.assertIn('设置页验证', handler.wfile.getvalue().decode('utf-8'))
                    self.assertEqual(
                        dashboard.DASHBOARD_ENV_FILE.read_text(encoding='utf-8'),
                        'DASHBOARD_GROK_MODEL=safe\n',
                    )
                    self.assertEqual(
                        dashboard.CONFIG_PATH.read_text(encoding='utf-8'),
                        'model:\n  default: safe\n',
                    )
        finally:
            dashboard.CONFIG_PATH = original_config_path

    def test_authenticated_config_writes_require_action_header(self):
        original_config_path = dashboard.CONFIG_PATH
        dashboard.CONFIG_PATH = self.tmp_path / 'config.yaml'
        dashboard.DASHBOARD_ENV_FILE.write_text('DASHBOARD_GROK_MODEL=safe\n', encoding='utf-8')
        dashboard.CONFIG_PATH.write_text('model:\n  default: safe\n', encoding='utf-8')
        admin_cookie = self.admin_cookie()
        try:
            cases = (
                ('/admin/config/env', b'env__DASHBOARD_GROK_MODEL=attacker'),
                ('/api/admin/config/env', b'env__DASHBOARD_GROK_MODEL=attacker'),
                ('/admin/config/yaml', b'config_yaml=model%3A+attacker'),
                ('/api/admin/config/yaml', b'config_yaml=model%3A+attacker'),
            )
            for path, body in cases:
                with self.subTest(path=path):
                    handler = FakeHandler(
                        path=path,
                        method='POST',
                        headers={
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'Content-Length': str(len(body)),
                            'Cookie': admin_cookie,
                        },
                        body=body,
                    )
                    handler.do_POST()
                    self.assertEqual(handler.status, 403)
                    self.assertEqual(handler.rfile.tell(), 0)
                    self.assertEqual(
                        json.loads(handler.wfile.getvalue().decode('utf-8'))['error'],
                        'action_header_required',
                    )
                    self.assertEqual(
                        dashboard.DASHBOARD_ENV_FILE.read_text(encoding='utf-8'),
                        'DASHBOARD_GROK_MODEL=safe\n',
                    )
                    self.assertEqual(
                        dashboard.CONFIG_PATH.read_text(encoding='utf-8'),
                        'model:\n  default: safe\n',
                    )
        finally:
            dashboard.CONFIG_PATH = original_config_path

    def test_admin_page_only_shows_business_config_content(self):
        handler = FakeHandler(path='/admin', headers={'Cookie': self.admin_cookie()})
        handler.do_GET()
        body = handler.wfile.getvalue().decode('utf-8')

        self.assertEqual(handler.status, 200)
        self.assertIn('<title>牛牛1号</title>', body)
        self.assertNotIn('<title>牛牛1号 · 设置</title>', body)
        self.assertNotIn('<title>牛牛1号 · 管理</title>', body)
        self.assertIn('开启牛牛美股', body)
        self.assertIn("name='env__DASHBOARD_US_FEATURES_ENABLED' data-feature-toggle='us'", body)
        us_toggle_start = body.index("name='env__DASHBOARD_US_FEATURES_ENABLED' data-feature-toggle='us'")
        us_toggle_end = body.index('</select>', us_toggle_start)
        us_toggle_html = body[us_toggle_start:us_toggle_end]
        self.assertNotIn("value=''", us_toggle_html)
        self.assertIn("value='1'", us_toggle_html)
        self.assertIn("value='0'", us_toggle_html)
        self.assertIn("data-feature-gated='us'", body)
        self.assertIn('[hidden]{display:none!important}', body)
        self.assertIn("document.addEventListener('input', handleUsFeatureToggle);", body)
        self.assertIn('Grok 模型', body)
        self.assertIn('Grok 模型上下文长度', body)
        self.assertIn('Grok API 地址', body)
        self.assertIn('Grok API 密钥', body)
        self.assertIn('美股评级上下文长度', body)
        self.assertIn('推文监控作者', body)
        self.assertIn('推文监控间隔', body)
        self.assertIn('美股买入评级时间', body)
        self.assertLess(body.index('开启牛牛美股'), body.index('消息面预检模型'))
        self.assertLess(body.index('Grok 模型'), body.index('消息面预检模型'))
        self.assertLess(body.index('Grok API 地址'), body.index('Grok API 密钥'))
        self.assertLess(body.index('Grok API 密钥'), body.index('消息面预检模型'))
        self.assertLess(body.index('推文监控作者'), body.index('消息面预检模型'))
        self.assertLess(body.index('美股买入评级时间'), body.index('消息面预检模型'))
        self.assertIn("name='env__DASHBOARD_GROK_API_KEY'", body)
        self.assertNotIn("<div class='config-label'>DASHBOARD_GROK_API_KEY</div>", body)
        self.assertNotIn('推文监控/美股买入评级模型', body)
        self.assertNotIn('<h2>推文监控作者</h2>', body)
        self.assertNotIn('<h2>推文监控周期</h2>', body)
        self.assertNotIn('<h2>美股买入评级周期</h2>', body)
        self.assertIn('买卖决策模型', body)
        self.assertIn('买卖决策上下文长度', body)
        self.assertIn('消息面预检上下文长度', body)
        self.assertIn('默认 128000 tokens', body)
        self.assertIn('默认 4096 tokens', body)
        self.assertIn('选股及买卖决策时间点', body)
        self.assertIn('选股策略', body)
        self.assertIn('当前策略来源', body)
        self.assertIn('内置策略', body)
        self.assertIn('预设文字策略', body)
        self.assertIn("name='env__DASHBOARD_STRATEGY_SOURCE'", body)
        self.assertIn("value='builtin'", body)
        self.assertIn("value='preset_text'", body)
        self.assertIn("data-strategy-source-toggle", body)
        self.assertIn("data-strategy-source-gated='builtin'", body)
        self.assertIn("data-strategy-source-gated='preset_text'", body)
        self.assertIn("name='env__DASHBOARD_PRESET_STRATEGY_TEXT'", body)
        self.assertIn('preset-strategy-textarea', body)
        self.assertIn('交易纪律 Prompt', body)
        self.assertIn("name='env__DASHBOARD_TRADE_DISCIPLINE_TEXT'", body)
        self.assertIn('trade-discipline-textarea', body)
        self.assertIn("document.addEventListener('input', handleStrategySourceToggle);", body)
        self.assertIn("name='env__DASHBOARD_ENABLED_PERSONA_STRATEGIES'", body)
        self.assertIn("type='radio' name='env__DASHBOARD_ENABLED_PERSONA_STRATEGIES'", body)
        self.assertNotIn("type='checkbox' name='env__DASHBOARD_ENABLED_PERSONA_STRATEGIES'", body)
        self.assertIn("value='base'", body)
        self.assertIn('基础策略', body)
        self.assertIn("value='zettaranc'", body)
        self.assertIn('Z哥', body)
        self.assertNotIn('Z哥体系', body)
        self.assertNotIn('Z 哥体系', body)
        self.assertIn("value='li_daxiao_bottom'", body)
        self.assertIn('李大霄', body)
        self.assertNotIn('李大霄底部', body)
        self.assertNotIn("value='buffett_value'", body)
        self.assertNotIn('巴菲特价值', body)
        self.assertIn('每次只启用一个内置策略', body)
        self.assertIn('内置策略和预设文字二选一激活', body)
        self.assertIn('盘面监控生产时间点', body)
        self.assertIn('A股盘面模型总结', body)
        self.assertIn('A股盘面总结上下文长度', body)
        self.assertIn("name='env__A_SHARE_MODEL_SUMMARY_ENABLED'", body)
        a_share_model_toggle_start = body.index("name='env__A_SHARE_MODEL_SUMMARY_ENABLED'")
        a_share_model_toggle_end = body.index('</select>', a_share_model_toggle_start)
        a_share_model_toggle_html = body[a_share_model_toggle_start:a_share_model_toggle_end]
        self.assertNotIn("默认", a_share_model_toggle_html)
        self.assertIn("value='1'", a_share_model_toggle_html)
        self.assertIn("value='0'", a_share_model_toggle_html)
        self.assertNotIn('A股盘面Grok总结', body)
        self.assertIn('指数行情更新周期', body)
        self.assertIn("class='settings-group'", body)
        self.assertIn("class='setting-row'", body)
        self.assertIn("class='settings-actions'", body)
        self.assertIn("data-env-save-status role='status' aria-live='polite'", body)
        self.assertIn("data-env-save-button type='submit'", body)
        self.assertIn("fetch('/api/admin/config/env'", body)
        self.assertIn("'X-NiuOne-Action': '1'", body)
        self.assertIn("window.location.replace('/admin')", body)
        self.assertIn('设置页管理员密码', body)
        self.assertIn("正在保存业务配置", body)
        self.assertIn("配置未变化，无需重新应用", body)
        self.assertIn('.save-button:active,.save-button.pressed', body)
        self.assertIn("document.addEventListener('pointerdown'", body)
        self.assertIn("button.classList.add('pressed')", body)
        self.assertIn('function envFormSnapshot(form)', body)
        self.assertIn('function resetEnvSaveIfDirty(form)', body)
        self.assertIn('markEnvFormSaved(form);', body)
        self.assertIn('有未保存修改', body)
        self.assertNotIn("setEnvSaveFeedback(form, '', ''); }, 3800", body)
        self.assertNotIn('<table class=', body)
        self.assertNotIn("<div class='config-name'>", body)
        self.assertNotIn('<th>生效</th>', body)
        self.assertNotIn('重启后', body)
        self.assertNotIn('下次任务', body)
        self.assertIn("type='time'", body)
        self.assertIn("name='env__DASHBOARD_B1_SCHEDULE_TIMES'", body)
        self.assertIn("type='hidden' name='env__DASHBOARD_B1_SCHEDULE_TIMES'", body)
        self.assertIn('data-time-list-add', body)
        self.assertIn('data-time-list-remove', body)
        self.assertEqual(body.count("type='time' name='env__DASHBOARD_B1_SCHEDULE_TIMES'"), 10)
        self.assertIn('09:25', body)
        self.assertIn('10:30', body)
        self.assertIn('周一至周五', body)
        self.assertNotIn('09:25,10:00', body)
        self.assertNotIn('25 9 * * 1-5', body)
        self.assertNotIn('生成邀请码', body)
        self.assertNotIn('HIDDEN-CODE', body)
        self.assertNotIn('/admin/invite', body)
        self.assertNotIn('启用邀请码登录', body)
        self.assertNotIn('观看者', body)
        self.assertNotIn('<th>来源</th>', body)
        self.assertNotIn('新增配置项', body)
        self.assertNotIn('DASHBOARD_HOME', body)
        self.assertNotIn('LaunchAgent', body)

    def test_admin_password_is_redacted_from_page_and_config_api(self):
        secret = '绝不回显的管理员密码'
        dashboard.ADMIN_PASSWORD = secret
        os.environ['DASHBOARD_ADMIN_PASSWORD'] = secret
        dashboard.DASHBOARD_ENV_FILE.write_text(
            f"DASHBOARD_ADMIN_PASSWORD='{secret}'\n",
            encoding='utf-8',
        )
        admin_cookie = self.admin_cookie()

        page = FakeHandler(path='/admin', headers={'Cookie': admin_cookie})
        page.do_GET()
        self.assertEqual(page.status, 200)
        self.assertNotIn(secret, page.wfile.getvalue().decode('utf-8'))

        config = FakeHandler(path='/api/admin/config', headers={'Cookie': admin_cookie})
        config.do_GET()
        self.assertEqual(config.status, 200)
        config_text = config.wfile.getvalue().decode('utf-8')
        self.assertNotIn(secret, config_text)
        password_item = next(
            item
            for item in json.loads(config_text)['items']
            if item['name'] == 'DASHBOARD_ADMIN_PASSWORD'
        )
        self.assertTrue(password_item['secret'])
        self.assertEqual(password_item['file_value'], '')

    def test_home_page_uses_us_feature_flag_for_tabs_without_deleting_data(self):
        dashboard.DASHBOARD_ENV_FILE.write_text('DASHBOARD_US_FEATURES_ENABLED=0\n', encoding='utf-8')
        disabled = FakeHandler(path='/?category=x_monitor')
        disabled.do_GET()
        disabled_body = disabled.wfile.getvalue().decode('utf-8')

        dashboard.DASHBOARD_ENV_FILE.write_text('DASHBOARD_US_FEATURES_ENABLED=1\n', encoding='utf-8')
        enabled = FakeHandler(path='/?category=x_monitor')
        enabled.do_GET()
        enabled_body = enabled.wfile.getvalue().decode('utf-8')

        self.assertEqual(disabled.status, 200)
        self.assertIn('const US_FEATURES_ENABLED = false;', disabled_body)
        self.assertIn("const US_FEATURE_CATEGORIES = new Set(['x_monitor', 'us_ratings']);", disabled_body)
        self.assertIn('activeCategory = normalizeActiveCategory(activeCategory);', disabled_body)
        self.assertEqual(enabled.status, 200)
        self.assertIn('const US_FEATURES_ENABLED = true;', enabled_body)

    def test_us_feature_flag_reads_dashboard_env_without_touching_records(self):
        dashboard.DASHBOARD_ENV_FILE.write_text('DASHBOARD_US_FEATURES_ENABLED=0\n', encoding='utf-8')
        self.assertFalse(dashboard.us_features_enabled())

        dashboard.DASHBOARD_ENV_FILE.write_text('DASHBOARD_US_FEATURES_ENABLED=yes\n', encoding='utf-8')
        self.assertTrue(dashboard.us_features_enabled())

    def test_admin_config_restores_x_watchlist_accounts_from_state(self):
        dashboard.CRON_STATE_DIR.mkdir(parents=True)
        (dashboard.CRON_STATE_DIR / 'x_watchlist_latest.json').write_text(json.dumps({
            'latest': {'Foo': {}, 'bar': {}},
            'seen_ids': {'baz': [], 'foo': []},
            'sent_missing_context': [{'handle': 'qux'}],
        }), encoding='utf-8')

        payload = dashboard.build_admin_config_payload()
        item = next(item for item in payload['items'] if item['name'] == 'X_WATCHLIST_ACCOUNTS')

        self.assertEqual(item['source'], 'x_watchlist_state')
        self.assertEqual(item['file_value'], 'foo,bar,baz,qux')
        self.assertEqual(item['handle_values'], ['foo', 'bar', 'baz', 'qux'])
        self.assertEqual(item['effective'], 'foo、bar、baz、qux')

    def test_admin_config_respects_explicit_empty_x_watchlist_accounts(self):
        dashboard.CRON_STATE_DIR.mkdir(parents=True)
        (dashboard.CRON_STATE_DIR / 'x_watchlist_latest.json').write_text(json.dumps({
            'latest': {'foo': {}},
        }), encoding='utf-8')
        dashboard.DASHBOARD_ENV_FILE.write_text('X_WATCHLIST_ACCOUNTS=\n', encoding='utf-8')

        payload = dashboard.build_admin_config_payload()
        item = next(item for item in payload['items'] if item['name'] == 'X_WATCHLIST_ACCOUNTS')

        self.assertEqual(item['source'], 'dashboard.env')
        self.assertEqual(item['file_value'], '')
        self.assertEqual(item['handle_values'], [])
        self.assertEqual(item['effective'], '')

    def test_admin_config_decodes_preset_strategy_text(self):
        original_env_values = {
            name: dashboard.os.environ.get(name)
            for name in [dashboard.STRATEGY_SOURCE_ENV, dashboard.PRESET_STRATEGY_TEXT_ENV, dashboard.TRADE_DISCIPLINE_TEXT_ENV]
        }
        try:
            for name in original_env_values:
                dashboard.os.environ.pop(name, None)
            dashboard.DASHBOARD_ENV_FILE.write_text(
                "DASHBOARD_STRATEGY_SOURCE=preset_text\n"
                "DASHBOARD_PRESET_STRATEGY_TEXT='强趋势回踩\\n跌破5日线离场'\n"
                "DASHBOARD_TRADE_DISCIPLINE_TEXT='纪律一\\n纪律二'\n",
                encoding='utf-8',
            )

            payload = dashboard.build_admin_config_payload()
            source_item = next(item for item in payload['items'] if item['name'] == dashboard.STRATEGY_SOURCE_ENV)
            text_item = next(item for item in payload['items'] if item['name'] == dashboard.PRESET_STRATEGY_TEXT_ENV)
            discipline_item = next(item for item in payload['items'] if item['name'] == dashboard.TRADE_DISCIPLINE_TEXT_ENV)
        finally:
            for name, value in original_env_values.items():
                if value is None:
                    dashboard.os.environ.pop(name, None)
                else:
                    dashboard.os.environ[name] = value

        self.assertEqual(source_item['effective'], '预设文字')
        self.assertEqual(source_item['file_value'], 'preset_text')
        self.assertEqual(text_item['file_value'], '强趋势回踩\n跌破5日线离场')
        self.assertEqual(text_item['effective'], '强趋势回踩\n跌破5日线离场')
        self.assertEqual(discipline_item['file_value'], '纪律一\n纪律二')
        self.assertEqual(discipline_item['effective'], '纪律一\n纪律二')

    def test_model_length_defaults_are_explicit_in_admin_config(self):
        payload = dashboard.build_admin_config_payload()
        by_name = {item['name']: item for item in payload['items']}

        for name in [
            'US_RATING_CONTEXT_LENGTH',
            'DASHBOARD_GROK_CONTEXT_LENGTH',
            'DASHBOARD_NEWS_CONTEXT_LENGTH',
            'DASHBOARD_DECISION_CONTEXT_LENGTH',
            'A_SHARE_MODEL_SUMMARY_CONTEXT_LENGTH',
        ]:
            item = by_name[name]
            self.assertEqual(item['default'], '128000')
            self.assertEqual(item['file_value'], '128000')

        for name in [
            'DASHBOARD_DECISION_MAX_TOKENS',
            'US_RATING_MAX_TOKENS',
            'DASHBOARD_GROK_MAX_TOKENS',
            'DASHBOARD_NEWS_MAX_TOKENS',
            'US_MARKET_SUMMARY_MAX_TOKENS',
            'A_SHARE_MODEL_SUMMARY_MAX_TOKENS',
            'X_WATCHLIST_MAX_TOKENS',
        ]:
            item = by_name[name]
            self.assertEqual(item['default'], '4096')
            self.assertEqual(item['file_value'], '4096')

        body = dashboard.render_admin_page().decode('utf-8')
        self.assertIn("placeholder='默认 4096；例如 2048 或 8192'", body)
        self.assertIn('默认 4096 tokens', body)
        self.assertIn("placeholder='默认 128000；例如 128K、1M 或 1000000'", body)
        self.assertIn('默认 128000 tokens', body)

    def test_business_settings_are_local_to_dashboard_env(self):
        original_env_file = dashboard.DASHBOARD_ENV_FILE
        original_b1_times = dashboard.B1_SCHEDULE_TIMES
        original_b1_enabled = dashboard.B1_SCHEDULE_ENABLED
        original_indices_ttl = dashboard.API_TTLS["indices"]
        original_env_values = {name: dashboard.os.environ.get(name) for name in dashboard.ADMIN_VISIBLE_ENV_NAMES}
        try:
            dashboard.DASHBOARD_ENV_FILE = self.tmp_path / 'dashboard.env'
            dashboard.B1_SCHEDULE_ENABLED = False
            updates = {
                'DASHBOARD_US_FEATURES_ENABLED': '1',
                'DASHBOARD_GROK_MODEL': 'grok-new',
                'DASHBOARD_GROK_CONTEXT_LENGTH': '1M',
                'DASHBOARD_NEWS_MODEL': 'search-model',
                'DASHBOARD_NEWS_CONTEXT_LENGTH': '128K',
                'DASHBOARD_NEWS_BASE_URL': 'https://news.example/v1',
                'DASHBOARD_NEWS_API_KEY': 'news-secret',
                'DASHBOARD_B1_SCHEDULE_TIMES': '09:25, 10:00, 14:50',
                'DASHBOARD_US_MARKET_SUMMARY_CRON': '08:01',
                'DASHBOARD_US_RATING_CRON': '10:30',
                'DASHBOARD_MARKET_AUCTION_CRON': '09:26',
                'X_WATCHLIST_ACCOUNTS': '@Foo, bar, foo',
                'X_WATCHLIST_DAEMON_INTERVAL_SECONDS': '900',
            }
            updates = dashboard.normalize_business_updates(updates)
            dashboard.validate_business_updates(updates)
            result = dashboard.write_env_file_values(updates)
            dashboard.sync_business_runtime_settings(result.get('changed_names') or [])
            parsed = dashboard.parse_env_file(dashboard.DASHBOARD_ENV_FILE)
            payload = dashboard.build_admin_config_payload()
        finally:
            dashboard.DASHBOARD_ENV_FILE = original_env_file
            dashboard.B1_SCHEDULE_TIMES = original_b1_times
            dashboard.B1_SCHEDULE_ENABLED = original_b1_enabled
            dashboard.API_TTLS["indices"] = original_indices_ttl
            for name, value in original_env_values.items():
                if value is None:
                    dashboard.os.environ.pop(name, None)
                else:
                    dashboard.os.environ[name] = value

        self.assertEqual(parsed['DASHBOARD_US_FEATURES_ENABLED'], '1')
        self.assertEqual(parsed['DASHBOARD_GROK_MODEL'], 'grok-new')
        self.assertEqual(parsed['DASHBOARD_GROK_CONTEXT_LENGTH'], '1000000')
        self.assertEqual(parsed['DASHBOARD_NEWS_MODEL'], 'search-model')
        self.assertEqual(parsed['DASHBOARD_NEWS_CONTEXT_LENGTH'], '128000')
        self.assertEqual(parsed['DASHBOARD_NEWS_BASE_URL'], 'https://news.example/v1')
        self.assertEqual(parsed['DASHBOARD_NEWS_API_KEY'], 'news-secret')
        self.assertEqual(parsed['DASHBOARD_B1_SCHEDULE_TIMES'], '09:25,10:00,14:50')
        self.assertEqual(parsed['DASHBOARD_US_MARKET_SUMMARY_CRON'], '1 8 * * 1-5')
        self.assertEqual(parsed['DASHBOARD_US_RATING_CRON'], '30 10 * * *')
        self.assertEqual(parsed['DASHBOARD_MARKET_AUCTION_CRON'], '26 9 * * 1-5')
        self.assertEqual(parsed['X_WATCHLIST_ACCOUNTS'], 'foo,bar')
        self.assertEqual(parsed['X_WATCHLIST_DAEMON_INTERVAL_SECONDS'], '900')
        payload_text = json.dumps(payload, ensure_ascii=False)
        self.assertIn('09:25、10:00、14:50', payload_text)
        self.assertIn('北京时间 09:26', payload_text)
        self.assertIn('foo、bar', payload_text)
        self.assertNotIn('26 9 * * 1-5', payload_text)
        self.assertFalse(any('LaunchAgent' in item.get('source', '') for item in payload['items']))

    @unittest.skipIf(dashboard.yaml is None, 'PyYAML unavailable')
    def test_model_api_base_urls_do_not_prefill_defaults(self):
        original_env_file = dashboard.DASHBOARD_ENV_FILE
        original_config_path = dashboard.CONFIG_PATH
        original_env_values = {name: dashboard.os.environ.get(name) for name in dashboard.ADMIN_VISIBLE_ENV_NAMES}
        try:
            dashboard.DASHBOARD_ENV_FILE = self.tmp_path / 'dashboard.env'
            dashboard.DASHBOARD_ENV_FILE.write_text('DASHBOARD_US_FEATURES_ENABLED=1\n', encoding='utf-8')
            dashboard.CONFIG_PATH = self.tmp_path / 'config.yaml'
            dashboard.CONFIG_PATH.write_text(
                'custom_providers:\n'
                '  - name: Crossdesk.ccwu.cc\n'
                '    base_url: https://crossdesk.example/v1\n'
                '    api_key: provider-secret\n',
                encoding='utf-8',
            )
            for name in ['DASHBOARD_GROK_BASE_URL', 'DASHBOARD_DECISION_BASE_URL', 'DASHBOARD_NEWS_BASE_URL']:
                dashboard.os.environ.pop(name, None)
            payload = dashboard.build_admin_config_payload()
        finally:
            dashboard.DASHBOARD_ENV_FILE = original_env_file
            dashboard.CONFIG_PATH = original_config_path
            for name, value in original_env_values.items():
                if value is None:
                    dashboard.os.environ.pop(name, None)
                else:
                    dashboard.os.environ[name] = value

        by_name = {item['name']: item for item in payload['items']}
        for name in ['DASHBOARD_GROK_BASE_URL', 'DASHBOARD_DECISION_BASE_URL']:
            item = by_name[name]
            self.assertEqual(item['default'], '')
            self.assertEqual(item['file_value'], '')
            self.assertEqual(item['effective'], 'https://crossdesk.example/v1')
        news_item = by_name['DASHBOARD_NEWS_BASE_URL']
        self.assertEqual(news_item['default'], '')
        self.assertEqual(news_item['file_value'], '')
        self.assertEqual(news_item['effective'], '')

    def test_env_config_write_preserves_blank_secret_and_quotes_values(self):
        original_env_file = dashboard.DASHBOARD_ENV_FILE
        try:
            dashboard.DASHBOARD_ENV_FILE = self.tmp_path / 'dashboard.env'
            dashboard.DASHBOARD_ENV_FILE.write_text(
                'DASHBOARD_PORT=8787\nUS_RATING_API_KEY=old-secret\n',
                encoding='utf-8',
            )

            dashboard.write_env_file_values({
                'DASHBOARD_PORT': '9000',
                'US_RATING_API_KEY': '',
                'EXTRA_VALUE': 'hello world',
            })
            parsed = dashboard.parse_env_file(dashboard.DASHBOARD_ENV_FILE)
        finally:
            dashboard.DASHBOARD_ENV_FILE = original_env_file

        self.assertEqual(parsed['DASHBOARD_PORT'], '9000')
        self.assertEqual(parsed['US_RATING_API_KEY'], 'old-secret')
        self.assertEqual(parsed['EXTRA_VALUE'], 'hello world')

    def test_env_config_write_reports_no_change(self):
        original_env_file = dashboard.DASHBOARD_ENV_FILE
        try:
            dashboard.DASHBOARD_ENV_FILE = self.tmp_path / 'dashboard.env'
            dashboard.DASHBOARD_ENV_FILE.write_text('DASHBOARD_GROK_MODEL=grok-test\n', encoding='utf-8')
            before = dashboard.DASHBOARD_ENV_FILE.read_text(encoding='utf-8')
            result = dashboard.write_env_file_values({'DASHBOARD_GROK_MODEL': 'grok-test'})
            after = dashboard.DASHBOARD_ENV_FILE.read_text(encoding='utf-8')
        finally:
            dashboard.DASHBOARD_ENV_FILE = original_env_file

        self.assertFalse(result['changed'])
        self.assertEqual(result['changed_count'], 0)
        self.assertEqual(before, after)

    def test_time_list_config_can_be_cleared(self):
        original_env_file = dashboard.DASHBOARD_ENV_FILE
        try:
            dashboard.DASHBOARD_ENV_FILE = self.tmp_path / 'dashboard.env'
            dashboard.write_env_file_values({'DASHBOARD_B1_SCHEDULE_TIMES': ''})
            parsed = dashboard.parse_env_file(dashboard.DASHBOARD_ENV_FILE)
            payload = dashboard.build_admin_config_payload()
        finally:
            dashboard.DASHBOARD_ENV_FILE = original_env_file

        self.assertIn('DASHBOARD_B1_SCHEDULE_TIMES', parsed)
        self.assertEqual(parsed['DASHBOARD_B1_SCHEDULE_TIMES'], '')
        time_item = next(item for item in payload['items'] if item['name'] == 'DASHBOARD_B1_SCHEDULE_TIMES')
        self.assertEqual(time_item['time_values'], [])
        self.assertEqual(time_item['file_value'], '')

    @unittest.skipIf(dashboard.yaml is None, 'PyYAML unavailable')
    def test_yaml_config_redacts_and_preserves_secret_placeholders(self):
        original_config_path = dashboard.CONFIG_PATH
        try:
            dashboard.CONFIG_PATH = self.tmp_path / 'config.yaml'
            dashboard.CONFIG_PATH.write_text(
                'model:\n'
                '  default: old-model\n'
                '  api_key: model-secret\n'
                'custom_providers:\n'
                '  - name: Crossdesk.ccwu.cc\n'
                '    base_url: https://crossdesk.example/v1\n'
                '    api_key: provider-secret\n',
                encoding='utf-8',
            )
            redacted = dashboard.redacted_yaml_text()
            self.assertNotIn('model-secret', redacted)
            self.assertNotIn('provider-secret', redacted)

            dashboard.write_yaml_config(redacted.replace('old-model', 'new-model'))
            restored = dashboard.load_yaml_config()
        finally:
            dashboard.CONFIG_PATH = original_config_path

        self.assertEqual(restored['model']['default'], 'new-model')
        self.assertEqual(restored['model']['api_key'], 'model-secret')
        self.assertEqual(restored['custom_providers'][0]['api_key'], 'provider-secret')

    def test_admin_config_api_saves_business_env_file(self):
        original_env_file = dashboard.DASHBOARD_ENV_FILE
        original_admin_limit = dashboard.RATE_LIMIT_ADMIN
        original_restart = dashboard.schedule_niuone_services_restart
        original_b1_times = dashboard.B1_SCHEDULE_TIMES
        original_b1_enabled = dashboard.B1_SCHEDULE_ENABLED
        original_indices_ttl = dashboard.API_TTLS["indices"]
        original_trader_module = dashboard.TRADER_MODULE
        original_trader_mtime = dashboard.TRADER_MODULE_MTIME
        original_env_values = {name: dashboard.os.environ.get(name) for name in dashboard.ADMIN_VISIBLE_ENV_NAMES}
        restart_calls = []
        try:
            dashboard.DASHBOARD_ENV_FILE = self.tmp_path / 'dashboard.env'
            dashboard.RATE_LIMIT_ADMIN = 100
            dashboard.B1_SCHEDULE_ENABLED = False
            dashboard.schedule_niuone_services_restart = (
                lambda: restart_calls.append(True) or {'ok': True, 'labels': ['ai.niuone.dashboard']}
            )
            body = urllib.parse.urlencode({
                'env__DASHBOARD_US_FEATURES_ENABLED': '1',
                'env__DASHBOARD_GROK_MODEL': 'grok-test',
                'env__DASHBOARD_GROK_CONTEXT_LENGTH': '1M',
                'env__DASHBOARD_NEWS_MODEL': 'search-model',
                'env__DASHBOARD_NEWS_CONTEXT_LENGTH': '1M',
                'env__DASHBOARD_NEWS_BASE_URL': 'https://news.example/v1',
                'env__DASHBOARD_NEWS_API_KEY': 'news-secret',
                'env__DASHBOARD_DECISION_CONTEXT_LENGTH': '256K',
                'env__DASHBOARD_B1_SCHEDULE_TIMES': ['', '09:25', '10:00', '', '14:50'],
                'env__DASHBOARD_INDICES_TTL_SECONDS': '20',
                'env__DASHBOARD_US_MARKET_SUMMARY_CRON': '08:01',
                'env__DASHBOARD_MARKET_AUCTION_CRON': '09:26',
                'env__DASHBOARD_US_RATING_CRON': '10:30',
                'env__X_WATCHLIST_ACCOUNTS': ['', '@Foo', 'bar', 'foo'],
                'env__DASHBOARD_STRATEGY_SOURCE': 'preset_text',
                'env__DASHBOARD_ENABLED_PERSONA_STRATEGIES': ['', 'li_daxiao_bottom'],
                'env__DASHBOARD_PRESET_STRATEGY_TEXT': '只做主线强趋势回踩\n跌破5日线离场',
                'env__DASHBOARD_TRADE_DISCIPLINE_TEXT': '纪律一\n纪律二',
                'env__DASHBOARD_HOME': '/tmp/should-not-be-written',
            }, doseq=True).encode('utf-8')
            handler = FakeHandler(
                path='/api/admin/config/env',
                method='POST',
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Content-Length': str(len(body)),
                    'Cookie': self.admin_cookie(),
                    dashboard.ACTION_HEADER_NAME: '1',
                },
                body=body,
            )
            handler.do_POST()
            parsed = dashboard.parse_env_file(dashboard.DASHBOARD_ENV_FILE)
            response = json.loads(handler.wfile.getvalue().decode('utf-8'))
            runtime_b1_times = dashboard.B1_SCHEDULE_TIMES
            runtime_indices_ttl = dashboard.API_TTLS['indices']
        finally:
            dashboard.DASHBOARD_ENV_FILE = original_env_file
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit
            dashboard.schedule_niuone_services_restart = original_restart
            dashboard.B1_SCHEDULE_TIMES = original_b1_times
            dashboard.B1_SCHEDULE_ENABLED = original_b1_enabled
            dashboard.API_TTLS["indices"] = original_indices_ttl
            dashboard.TRADER_MODULE = original_trader_module
            dashboard.TRADER_MODULE_MTIME = original_trader_mtime
            for name, value in original_env_values.items():
                if value is None:
                    dashboard.os.environ.pop(name, None)
                else:
                    dashboard.os.environ[name] = value

        self.assertEqual(handler.status, 200)
        self.assertEqual(restart_calls, [])
        self.assertTrue(response['changed'])
        self.assertGreater(response['changed_count'], 0)
        self.assertEqual(response['restart']['skipped'], 'hot_applied')
        self.assertTrue(response['runtime']['ok'])
        self.assertIn('b1_schedule_times', response['runtime']['applied'])
        self.assertIn('indices_ttl', response['runtime']['applied'])
        self.assertIn('persona_strategies', response['runtime']['applied'])
        self.assertIn('strategy_settings', response['runtime']['applied'])
        self.assertIn('trader_runtime', response['runtime']['applied'])
        self.assertEqual(parsed['DASHBOARD_US_FEATURES_ENABLED'], '1')
        self.assertEqual(parsed['DASHBOARD_GROK_MODEL'], 'grok-test')
        self.assertEqual(parsed['DASHBOARD_GROK_CONTEXT_LENGTH'], '1000000')
        self.assertEqual(parsed['DASHBOARD_NEWS_MODEL'], 'search-model')
        self.assertEqual(parsed['DASHBOARD_NEWS_CONTEXT_LENGTH'], '1000000')
        self.assertEqual(parsed['DASHBOARD_NEWS_BASE_URL'], 'https://news.example/v1')
        self.assertEqual(parsed['DASHBOARD_NEWS_API_KEY'], 'news-secret')
        self.assertEqual(parsed['DASHBOARD_DECISION_CONTEXT_LENGTH'], '256000')
        self.assertEqual(parsed['DASHBOARD_B1_SCHEDULE_TIMES'], '09:25,10:00,14:50')
        self.assertEqual(parsed['DASHBOARD_INDICES_TTL_SECONDS'], '20')
        self.assertEqual(parsed['DASHBOARD_US_MARKET_SUMMARY_CRON'], '1 8 * * 1-5')
        self.assertEqual(parsed['DASHBOARD_MARKET_AUCTION_CRON'], '26 9 * * 1-5')
        self.assertEqual(parsed['DASHBOARD_US_RATING_CRON'], '30 10 * * *')
        self.assertEqual(parsed['X_WATCHLIST_ACCOUNTS'], 'foo,bar')
        self.assertEqual(parsed['DASHBOARD_STRATEGY_SOURCE'], 'preset_text')
        self.assertEqual(parsed['DASHBOARD_ENABLED_PERSONA_STRATEGIES'], 'li_daxiao_bottom')
        self.assertEqual(parsed['DASHBOARD_PRESET_STRATEGY_TEXT'], '只做主线强趋势回踩\\n跌破5日线离场')
        self.assertEqual(parsed['DASHBOARD_TRADE_DISCIPLINE_TEXT'], '纪律一\\n纪律二')
        self.assertEqual(runtime_b1_times, ('09:25', '10:00', '14:50'))
        self.assertEqual(runtime_indices_ttl, 20)
        self.assertNotIn('DASHBOARD_HOME', parsed)

    def test_admin_config_api_does_not_restart_without_changes(self):
        original_env_file = dashboard.DASHBOARD_ENV_FILE
        original_admin_limit = dashboard.RATE_LIMIT_ADMIN
        original_restart = dashboard.schedule_niuone_services_restart
        restart_calls = []
        try:
            dashboard.DASHBOARD_ENV_FILE = self.tmp_path / 'dashboard.env'
            dashboard.DASHBOARD_ENV_FILE.write_text('DASHBOARD_GROK_MODEL=grok-test\n', encoding='utf-8')
            dashboard.RATE_LIMIT_ADMIN = 100
            dashboard.schedule_niuone_services_restart = (
                lambda: restart_calls.append(True) or {'ok': True}
            )
            body = urllib.parse.urlencode({
                'env__DASHBOARD_GROK_MODEL': 'grok-test',
            }).encode('utf-8')
            handler = FakeHandler(
                path='/api/admin/config/env',
                method='POST',
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Content-Length': str(len(body)),
                    'Cookie': self.admin_cookie(),
                    dashboard.ACTION_HEADER_NAME: '1',
                },
                body=body,
            )
            handler.do_POST()
            response = json.loads(handler.wfile.getvalue().decode('utf-8'))
        finally:
            dashboard.DASHBOARD_ENV_FILE = original_env_file
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit
            dashboard.schedule_niuone_services_restart = original_restart

        self.assertEqual(handler.status, 200)
        self.assertEqual(restart_calls, [])
        self.assertFalse(response['changed'])
        self.assertEqual(response['restart']['skipped'], 'unchanged')

    def test_settings_page_omits_contest_panel_and_config(self):
        payload = dashboard.build_admin_config_payload()
        body = dashboard.render_admin_page().decode('utf-8')

        self.assertFalse(any(str(item.get('name') or '').startswith('DASHBOARD_CONTEST_') for item in payload['items']))
        self.assertNotIn('<h2>策略大赛</h2>', body)
        self.assertNotIn('id="contestPanel"', body)
        self.assertNotIn('/api/contest/status', body)
        self.assertNotIn('LinuxDo', body)

    def test_contest_routes_are_removed(self):
        get_handler = FakeHandler('/api/contest/status')
        get_handler.do_GET()
        self.assertEqual(get_handler.status, 404)

        post_body = json.dumps({'contest_id': 'demo'}).encode('utf-8')
        post_handler = FakeHandler(
            '/api/contest/join',
            method='POST',
            headers={'Content-Length': str(len(post_body)), dashboard.ACTION_HEADER_NAME: '1'},
            body=post_body,
        )
        post_handler.do_POST()
        self.assertEqual(post_handler.status, 404)


if __name__ == '__main__':
    unittest.main()
