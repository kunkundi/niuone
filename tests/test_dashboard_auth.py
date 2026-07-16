#!/usr/bin/env python3
import importlib.util
import gzip
import io
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
import sys
import urllib.parse
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'app'
COMPAT = SRC / 'compat'
ENTRYPOINTS = SRC / 'entrypoints'
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(COMPAT))
MODULE_PATH = COMPAT / 'niuone_dashboard.py'
spec = importlib.util.spec_from_file_location('dashboard_under_test', MODULE_PATH)
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)
FRONTEND = ROOT / 'frontend'
DASHBOARD_FRONTEND = '\n'.join(
    (FRONTEND / name).read_text(encoding='utf-8')
    for name in ('index.html', 'dashboard.css', 'dashboard.js')
)
ADMIN_FRONTEND = '\n'.join(
    (FRONTEND / name).read_text(encoding='utf-8')
    for name in ('admin.html', 'admin.css', 'admin.js')
)


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
        dashboard.FRONTEND_FILE_CACHE.clear()
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
        self.assertIn('<link rel="stylesheet" href="/static/admin.css?v=8">', admin_body)
        self.assertIn('<script src="/static/admin.js?v=17" defer></script>', admin_body)
        self.assertNotIn('name="admin_password"', admin_body)
        self.assertNotIn("name='env__DASHBOARD_GROK_API_KEY'", admin_body)
        self.assertIn("fetch('/api/admin/config'", ADMIN_FRONTEND)
        self.assertIn("renderAdminLogin('')", ADMIN_FRONTEND)

        config = FakeHandler(path='/api/admin/config')
        config.do_GET()
        self.assertEqual(config.status, 403)
        self.assertEqual(
            json.loads(config.wfile.getvalue().decode('utf-8'))['error'],
            'admin_password_required',
        )

    def test_frontend_pages_and_assets_are_served_from_native_static_files(self):
        home = FakeHandler(path='/')
        home.do_GET()
        admin = FakeHandler(path='/admin')
        admin.do_GET()
        dashboard_js = FakeHandler(path='/static/dashboard.js')
        dashboard_js.do_GET()
        admin_js = FakeHandler(path='/static/admin.js')
        admin_js.do_GET()
        versioned_dashboard_js = FakeHandler(path='/static/dashboard.js?v=23')
        versioned_dashboard_js.do_GET()

        self.assertEqual(home.wfile.getvalue(), (FRONTEND / 'index.html').read_bytes())
        self.assertEqual(admin.wfile.getvalue(), (FRONTEND / 'admin.html').read_bytes())
        self.assertEqual(dashboard_js.wfile.getvalue(), (FRONTEND / 'dashboard.js').read_bytes())
        self.assertEqual(admin_js.wfile.getvalue(), (FRONTEND / 'admin.js').read_bytes())
        self.assertEqual(versioned_dashboard_js.wfile.getvalue(), (FRONTEND / 'dashboard.js').read_bytes())
        self.assertIn('<link rel="stylesheet" href="/static/dashboard.css?v=14">', DASHBOARD_FRONTEND)
        self.assertIn('<script src="/static/dashboard.js?v=27" defer></script>', DASHBOARD_FRONTEND)
        self.assertNotIn('document.title', DASHBOARD_FRONTEND)
        self.assertIn("document.title = '牛牛1号';", ADMIN_FRONTEND)
        self.assertNotIn("title + ' · 牛牛1号'", ADMIN_FRONTEND)
        self.assertEqual(dashboard_js.header('Content-Type'), 'application/javascript; charset=utf-8')
        self.assertIn('max-age=31536000', dashboard_js.header('Cache-Control'))
        self.assertIn('immutable', dashboard_js.header('Cache-Control'))
        self.assertTrue(dashboard_js.header('ETag'))

        backend_source = MODULE_PATH.read_text(encoding='utf-8')
        self.assertNotIn('INDEX_HTML', backend_source)
        self.assertNotIn('ADMIN_HTML', backend_source)
        self.assertNotIn('<!doctype html>', backend_source)

    def test_static_assets_reuse_compressed_payload_and_support_conditional_get(self):
        original_compress = dashboard.gzip.compress
        compress_calls = []

        def tracked_compress(payload, *args, **kwargs):
            compress_calls.append(len(payload))
            return original_compress(payload, *args, **kwargs)

        dashboard.gzip.compress = tracked_compress
        try:
            first = FakeHandler(path='/static/dashboard.js', headers={'Accept-Encoding': 'gzip'})
            first.do_GET()
            second = FakeHandler(path='/static/dashboard.js', headers={'Accept-Encoding': 'gzip'})
            second.do_GET()

            self.assertEqual(first.status, 200)
            self.assertEqual(second.status, 200)
            self.assertEqual(first.header('Content-Encoding'), 'gzip')
            self.assertEqual(second.wfile.getvalue(), first.wfile.getvalue())
            self.assertEqual(len(compress_calls), 1)

            conditional = FakeHandler(
                path='/static/dashboard.js',
                headers={'If-None-Match': first.header('ETag'), 'Accept-Encoding': 'gzip'},
            )
            conditional.do_GET()
            self.assertEqual(conditional.status, 304)
            self.assertEqual(conditional.wfile.getvalue(), b'')
            self.assertEqual(len(compress_calls), 1)
        finally:
            dashboard.gzip.compress = original_compress

    def test_dashboard_categories_have_independent_page_routes(self):
        expected_paths = {
            '/',
            '/practice',
            '/indices',
            '/market-monitor',
            '/x-monitor',
            '/us-ratings',
        }
        self.assertEqual(dashboard.DASHBOARD_PAGE_PATHS, expected_paths)
        expected_page = (FRONTEND / 'index.html').read_bytes()
        for path in sorted(expected_paths):
            with self.subTest(path=path):
                page = FakeHandler(path=path)
                page.do_GET()
                self.assertEqual(page.status, 200)
                self.assertEqual(page.wfile.getvalue(), expected_page)
                head = FakeHandler(path=path, method='HEAD')
                head.do_HEAD()
                self.assertEqual(head.status, 200)
                self.assertEqual(head.wfile.getvalue(), b'')

        missing = FakeHandler(path='/not-a-dashboard-page')
        missing.do_GET()
        self.assertEqual(missing.status, 404)
        for category, path in (
            ('practice', '/practice'),
            ('indices', '/indices'),
            ('market_monitor', '/market-monitor'),
            ('x_monitor', '/x-monitor'),
            ('us_ratings', '/us-ratings'),
        ):
            self.assertIn(f"{category}: '{path}'", DASHBOARD_FRONTEND)
        self.assertIn("syncViewUrl({push:true})", DASHBOARD_FRONTEND)
        self.assertIn("window.addEventListener('popstate'", DASHBOARD_FRONTEND)
        self.assertIn("params.set('curve', 'daily')", DASHBOARD_FRONTEND)

    def test_dashboard_bootstrap_owns_visit_count_and_visitor_cookie(self):
        home = FakeHandler(path='/')
        home.do_GET()
        self.assertIsNone(home.header('Set-Cookie'))

        bootstrap = FakeHandler(path='/api/dashboard/bootstrap')
        bootstrap.do_GET()
        payload = json.loads(bootstrap.wfile.getvalue().decode('utf-8'))
        self.assertEqual(bootstrap.status, 200)
        self.assertEqual(payload['visits'], 1)
        self.assertEqual(payload['unique'], 1)
        self.assertIn('us_features_enabled', payload)
        self.assertTrue((bootstrap.header('Set-Cookie') or '').startswith(f'{dashboard.VISITOR_COOKIE_NAME}=nvst_'))

    def test_version_status_api_is_public_and_not_browser_cached(self):
        original = dashboard.get_version_status
        dashboard.get_version_status = lambda: {
            'current_version': 'v1.2.3',
            'latest_version': 'v1.2.4',
            'update_available': True,
            'check_ok': True,
        }
        try:
            handler = FakeHandler(path='/api/version')
            handler.do_GET()
        finally:
            dashboard.get_version_status = original

        payload = json.loads(handler.wfile.getvalue().decode('utf-8'))
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.header('Cache-Control'), 'no-store')
        self.assertTrue(payload['update_available'])

    def test_docker_version_check_uses_highest_strict_semver_tag(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit=-1):
                return json.dumps({
                    'count': 7,
                    'results': [
                        {'name': 'latest'},
                        {'name': '1.9.0'},
                        {'name': 'v1.9.0'},
                        {'name': 'v1.10.0'},
                        {'name': 'v2.0.0-rc.1'},
                        {'name': 'v01.0.0'},
                        {'name': 'v2.0.0'},
                    ],
                }).encode('utf-8')

        original = dashboard.urllib.request.urlopen
        requests = []
        dashboard.urllib.request.urlopen = lambda request, timeout=0: requests.append((request, timeout)) or Response()
        try:
            latest = dashboard.fetch_latest_docker_version()
        finally:
            dashboard.urllib.request.urlopen = original

        self.assertEqual(latest, 'v2.0.0')
        self.assertEqual(dashboard.release_version_tuple('v10.2.3'), (10, 2, 3))
        self.assertIsNone(dashboard.release_version_tuple('v1.2.3-rc.1'))
        self.assertIn('/v2/namespaces/kunkundi/repositories/niuone/tags', requests[0][0].full_url)
        self.assertEqual(requests[0][1], 6)

    def test_dashboard_starts_version_check_on_every_page_load(self):
        self.assertIn('id="versionStatus"', DASHBOARD_FRONTEND)
        self.assertIn("fetch('/api/version'", DASHBOARD_FRONTEND)
        self.assertIn('loadVersionStatus();', DASHBOARD_FRONTEND)
        self.assertIn("status.dataset.state = 'update';", DASHBOARD_FRONTEND)

    def test_visit_stats_reinitializes_database_replaced_at_same_path(self):
        replacement = dashboard.STATS_DB.with_name('replacement_stats.db')
        sqlite3.connect(replacement).close()
        replacement.replace(dashboard.STATS_DB)

        dashboard.ensure_stats_db()
        stats = dashboard.increment_visit_count('replacement-visitor')

        self.assertEqual(stats, {'visits': 1, 'unique': 1})

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

        settings_group = FakeHandler(path='/admin/settings/notifications', method='HEAD')
        settings_group.do_HEAD()
        self.assertEqual(settings_group.status, 200)

        missing_group = FakeHandler(path='/admin/settings/not-a-group', method='HEAD')
        missing_group.do_HEAD()
        self.assertEqual(missing_group.status, 404)

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

    def test_legacy_visit_stats_migration_retries_after_transient_failure(self):
        with closing(sqlite3.connect(dashboard.LEGACY_STATS_DB)) as con:
            con.execute("""
                CREATE TABLE visit_stats (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                )
            """)
            con.execute("INSERT INTO visit_stats(key,value,updated_at) VALUES('home_views',7,20)")
            con.commit()

        original_migrate = dashboard.migrate_legacy_visit_stats
        attempts = 0

        def flaky_migrate(con):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return False
            return original_migrate(con)

        dashboard.migrate_legacy_visit_stats = flaky_migrate
        try:
            dashboard.ensure_stats_db()
            dashboard.ensure_stats_db()
        finally:
            dashboard.migrate_legacy_visit_stats = original_migrate

        with closing(sqlite3.connect(dashboard.STATS_DB)) as con:
            views = con.execute("SELECT value FROM visit_stats WHERE key='home_views'").fetchone()[0]

        self.assertEqual(attempts, 2)
        self.assertEqual(views, 7)

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

    def test_b1_payload_preserves_market_and_sector_tide_snapshots(self):
        snapshot = {'source': 'b1_mainboard_quotes', 'sample_count': 3000, 'up': 2000, 'down': 900}
        tide_context = {'market': {'state': 'rotation'}, 'sectors': {'半导体': {'score': 72}}}

        payload = dashboard.normalize_b1_payload_for_trader({
            'generated_at': '2026-07-10 10:00:05',
            'items': [],
            'market_snapshot': snapshot,
            'sector_tide_context': tide_context,
            'schedule_slot': '2026-07-10 10:00',
        })

        self.assertEqual(payload['market_snapshot'], snapshot)
        self.assertEqual(payload['sector_tide_context'], tide_context)
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

    def test_manual_practice_cycle_stays_locked_until_trade_decision_finishes(self):
        scan_started = threading.Event()
        allow_scan_finish = threading.Event()
        allow_decision_finish = threading.Event()
        calls = []

        def fake_scan(force=False, decision_mode='async', **_kwargs):
            calls.append(('scan', force, decision_mode))
            scan_started.set()
            allow_scan_finish.wait(2)
            return {
                'items': [{'code': '000001'}],
                'count': 1,
                'generated_at': '2026-07-13 10:00:00',
                'market_snapshot': {'sample_count': 3000},
            }

        def fake_decision(payload, record_start=False):
            calls.append(('decision', payload['items'][0]['code'], record_start))
            allow_decision_finish.wait(2)
            return {'executed': [{'action': 'BUY'}]}

        original_scan = dashboard.trigger_b1_scan
        original_decision = dashboard.run_practice_decision_logged
        original_recent_candidates = dashboard.recent_practice_candidates_for_manual_cycle
        original_lock = dashboard.PRACTICE_MANUAL_CYCLE_LOCK
        original_state = dashboard.PRACTICE_MANUAL_CYCLE_STATE
        try:
            dashboard.trigger_b1_scan = fake_scan
            dashboard.run_practice_decision_logged = fake_decision
            dashboard.recent_practice_candidates_for_manual_cycle = lambda: None
            dashboard.PRACTICE_MANUAL_CYCLE_LOCK = threading.Lock()
            dashboard.PRACTICE_MANUAL_CYCLE_STATE = {'running': False, 'stage': 'idle'}

            first = dashboard.start_practice_manual_cycle()
            self.assertTrue(first['accepted'])
            self.assertTrue(scan_started.wait(1))
            duplicate_during_scan = dashboard.start_practice_manual_cycle()
            self.assertFalse(duplicate_during_scan['accepted'])
            self.assertTrue(duplicate_during_scan['running'])

            allow_scan_finish.set()
            for _ in range(100):
                if dashboard.practice_manual_cycle_status().get('stage') == 'trading':
                    break
                threading.Event().wait(0.01)
            duplicate_during_trade = dashboard.start_practice_manual_cycle()
            self.assertFalse(duplicate_during_trade['accepted'])
            self.assertTrue(duplicate_during_trade['running'])

            allow_decision_finish.set()
            for _ in range(100):
                if not dashboard.practice_manual_cycle_status().get('running'):
                    break
                threading.Event().wait(0.01)
            status = dashboard.practice_manual_cycle_status()
            self.assertEqual(status['stage'], 'completed')
            self.assertEqual(status['candidate_count'], 1)
            self.assertEqual(calls, [
                ('scan', True, 'none'),
                ('decision', '000001', True),
            ])
        finally:
            allow_scan_finish.set()
            allow_decision_finish.set()
            dashboard.trigger_b1_scan = original_scan
            dashboard.run_practice_decision_logged = original_decision
            dashboard.recent_practice_candidates_for_manual_cycle = original_recent_candidates
            dashboard.PRACTICE_MANUAL_CYCLE_LOCK = original_lock
            dashboard.PRACTICE_MANUAL_CYCLE_STATE = original_state

    def test_full_b1_scan_rejects_overlapping_requests(self):
        scan_started = threading.Event()
        allow_scan_finish = threading.Event()
        results = []

        def fake_scan(force=False, decision_mode='async', **_kwargs):
            scan_started.set()
            allow_scan_finish.wait(2)
            return {'count': 1, 'force': force, 'decision_mode': decision_mode}

        original_scan = dashboard._trigger_b1_scan_unlocked
        original_lock = dashboard.B1_FULL_SCAN_LOCK
        worker = None
        try:
            dashboard._trigger_b1_scan_unlocked = fake_scan
            dashboard.B1_FULL_SCAN_LOCK = threading.Lock()
            worker = threading.Thread(
                target=lambda: results.append(dashboard.trigger_b1_scan(force=True, decision_mode='none')),
            )
            worker.start()
            self.assertTrue(scan_started.wait(1))

            duplicate = dashboard.trigger_b1_scan(force=True, decision_mode='none')
            self.assertTrue(duplicate['busy'])
            self.assertTrue(duplicate['running'])
            self.assertIn('已有选股扫描正在运行', duplicate['error'])

            allow_scan_finish.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(results, [{'count': 1, 'force': True, 'decision_mode': 'none'}])
        finally:
            allow_scan_finish.set()
            if worker is not None:
                worker.join(2)
            dashboard._trigger_b1_scan_unlocked = original_scan
            dashboard.B1_FULL_SCAN_LOCK = original_lock

    def test_recent_manual_candidates_respect_reuse_window(self):
        original_seconds = dashboard.PRACTICE_MANUAL_SCAN_REUSE_SECONDS
        original_loader = dashboard.load_practice_candidates_cache
        try:
            dashboard.PRACTICE_MANUAL_SCAN_REUSE_SECONDS = 600
            generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            dashboard.load_practice_candidates_cache = lambda: {
                'items': [{'code': '000001'}],
                'count': 1,
                'generated_at': generated_at,
            }

            recent = dashboard.recent_practice_candidates_for_manual_cycle()
            self.assertTrue(recent['manual_scan_reused'])
            self.assertEqual(recent['count'], 1)
            self.assertLessEqual(recent['manual_scan_age_seconds'], 1)

            dashboard.load_practice_candidates_cache = lambda: {
                'items': [{'code': '000001'}],
                'count': 1,
                'generated_at': '2020-01-01 00:00:00',
            }
            self.assertIsNone(dashboard.recent_practice_candidates_for_manual_cycle())
        finally:
            dashboard.PRACTICE_MANUAL_SCAN_REUSE_SECONDS = original_seconds
            dashboard.load_practice_candidates_cache = original_loader

    def test_b1_slot_cache_is_read_as_utf8(self):
        class RecordingCachePath:
            def __init__(self):
                self.encoding = None

            def exists(self):
                return True

            def read_text(self, *, encoding=None):
                self.encoding = encoding
                return json.dumps({'generated_at': '2026-07-15 14:00:00'}, ensure_ascii=False)

        original_cache_file = dashboard.B1_CACHE_FILE
        cache_file = RecordingCachePath()
        try:
            dashboard.B1_CACHE_FILE = cache_file
            self.assertTrue(dashboard.b1_cache_generated_for_slot('2026-07-15 14:00'))
            self.assertEqual(cache_file.encoding, 'utf-8')
        finally:
            dashboard.B1_CACHE_FILE = original_cache_file

    def test_b1_scan_reliability_settings_are_exposed(self):
        workers = dashboard.ENV_CONFIG_BY_NAME['DASHBOARD_B1_SCAN_WORKERS']
        reuse = dashboard.ENV_CONFIG_BY_NAME['DASHBOARD_MANUAL_SCAN_REUSE_SECONDS']

        self.assertEqual(workers['default'], '6')
        self.assertEqual(workers['effect'], 'restart')
        self.assertEqual(reuse['default'], '0')
        self.assertEqual(reuse['effect'], 'restart')

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
        self.assertIn('function renderPracticeOperationLog(payload)', DASHBOARD_FRONTEND)
        self.assertIn('class="practice-log-scroll"', DASHBOARD_FRONTEND)
        self.assertIn('overflow-y:auto', DASHBOARD_FRONTEND)
        self.assertIn('aria-label="当日所有操作日志"', DASHBOARD_FRONTEND)

    def test_index_template_can_open_full_practice_log_modal(self):
        self.assertIn('let practiceLogDetailKey = \'\';', DASHBOARD_FRONTEND)
        self.assertIn('data-practice-log-key=', DASHBOARD_FRONTEND)
        self.assertIn('function renderPracticeLogDetailModal(payload)', DASHBOARD_FRONTEND)
        self.assertIn('function practiceLogRawText(item)', DASHBOARD_FRONTEND)
        self.assertIn('class="practice-log-detail-backdrop"', DASHBOARD_FRONTEND)
        self.assertIn('class="practice-log-detail-text"', DASHBOARD_FRONTEND)
        self.assertIn('data-practice-log-action="close"', DASHBOARD_FRONTEND)
        self.assertIn('practiceLogDetailKey = logTrigger.dataset.practiceLogKey || \'\';', DASHBOARD_FRONTEND)
        self.assertNotIn('practice-log-detail-json', DASHBOARD_FRONTEND)
        self.assertNotIn('practice-log-detail-field', DASHBOARD_FRONTEND)

    def test_index_template_hides_trade_rule_note_in_modal(self):
        self.assertIn('let practiceRuleNoteOpen = false', DASHBOARD_FRONTEND)
        self.assertIn('function renderPracticeRuleNoteModal(note)', DASHBOARD_FRONTEND)
        self.assertIn('data-practice-rule-action="open"', DASHBOARD_FRONTEND)
        self.assertIn('class="practice-rule-backdrop"', DASHBOARD_FRONTEND)
        self.assertIn('${ruleModal}', DASHBOARD_FRONTEND)
        self.assertNotIn('${esc(p.trade_rule_note||', DASHBOARD_FRONTEND)

    def test_non_message_tabs_request_message_counts_without_records(self):
        self.assertEqual(dashboard.clamp_limit('0'), 0)
        self.assertIn(
            "isMessageCategory(categoryAtStart) ? limitAtStart : 0",
            DASHBOARD_FRONTEND,
        )

    def test_x_monitor_loads_in_parallel_and_reuses_recent_pages(self):
        self.assertIn('const X_PAGE_CACHE_TTL_MS = 5 * 60 * 1000;', DASHBOARD_FRONTEND)
        self.assertIn("const X_PAGE_STATE_KEY = 'niuniu-dashboard-x-pages-v1';", DASHBOARD_FRONTEND)
        self.assertIn('function applyCachedXPage(offset = xPageOffset)', DASHBOARD_FRONTEND)
        self.assertIn('rememberXPage(offsetAtStart, nextData);', DASHBOARD_FRONTEND)
        self.assertIn('prefetchAdjacentXPages(offsetAtStart, nextData);', DASHBOARD_FRONTEND)
        self.assertIn('function cancelXMediaRequests()', DASHBOARD_FRONTEND)
        self.assertIn("if (!img.complete) img.removeAttribute('src');", DASHBOARD_FRONTEND)
        self.assertIn('fetchpriority="low" decoding="async"', DASHBOARD_FRONTEND)
        self.assertIn('function xPageRevision(payload)', DASHBOARD_FRONTEND)
        self.assertIn('if (!unchangedXPage && !unchangedMarketPage) render();', DASHBOARD_FRONTEND)
        self.assertIn('media.slice(0, 1)', DASHBOARD_FRONTEND)
        self.assertIn("activeCategory === 'x_monitor' && !hasCachedPage", DASHBOARD_FRONTEND)
        self.assertIn("if (categoryAtStart === 'x_monitor') saveXPageState();", DASHBOARD_FRONTEND)
        self.assertIn('const bootstrapPromise = loadDashboardBootstrap();', DASHBOARD_FRONTEND)
        self.assertIn('updateTabs: true,', DASHBOARD_FRONTEND)
        self.assertIn('waitFor: needsFeatureCheck ? bootstrapPromise : null,', DASHBOARD_FRONTEND)

    def test_market_monitor_prioritizes_messages_and_reuses_page_cache(self):
        self.assertIn("const MARKET_PAGE_STATE_KEY = 'niuniu-dashboard-market-page-v1';", DASHBOARD_FRONTEND)
        self.assertIn('function applyCachedMarketPage()', DASHBOARD_FRONTEND)
        self.assertIn('function marketPageRevision(payload)', DASHBOARD_FRONTEND)
        self.assertIn('const messageRequest = fetch(msgUrl, {signal: controller.signal});', DASHBOARD_FRONTEND)
        self.assertIn("if (categoryAtStart !== 'market_monitor') loadActiveCategoryData(categoryAtStart);", DASHBOARD_FRONTEND)
        self.assertIn("if (categoryAtStart === 'market_monitor') loadActiveCategoryData(categoryAtStart);", DASHBOARD_FRONTEND)
        self.assertIn('function loadMarketMonitorAuxData()', DASHBOARD_FRONTEND)
        self.assertIn("const request = fetch('/api/us_market_summary')", DASHBOARD_FRONTEND)
        self.assertNotIn('function loadIndicesDataInBg()', DASHBOARD_FRONTEND)
        self.assertIn("else if (categoryAtStart === 'market_monitor') saveMarketPageState();", DASHBOARD_FRONTEND)
        self.assertIn('let usMarketSummaryExpanded = false;', DASHBOARD_FRONTEND)
        self.assertIn('data-us-market-action="toggle"', DASHBOARD_FRONTEND)
        self.assertIn('aria-controls="us-market-summary-body"', DASHBOARD_FRONTEND)
        self.assertIn('aria-expanded="${usMarketSummaryExpanded', DASHBOARD_FRONTEND)
        self.assertIn('class="market-chevron us-market-chevron"', DASHBOARD_FRONTEND)
        self.assertIn('class="market-card-preview us-market-preview">${esc(preview)}', DASHBOARD_FRONTEND)
        self.assertIn('function renderUsMarketSummaryDetail(summaryData, summary)', DASHBOARD_FRONTEND)
        self.assertIn('class="market-detail-overview us-market-overview', DASHBOARD_FRONTEND)
        self.assertIn('class="market-card-detail us-market-summary-body"', DASHBOARD_FRONTEND)
        self.assertIn('.us-market-summary-card.open .us-market-preview { display:none; }', DASHBOARD_FRONTEND)
        self.assertIn('.us-market-summary-card.collapsed::before { opacity:0; }', DASHBOARD_FRONTEND)
        self.assertIn('.us-market-summary-card.collapsed .us-market-tone', DASHBOARD_FRONTEND)
        self.assertNotIn('class="us-market-brief"', DASHBOARD_FRONTEND)
        self.assertNotIn('class="us-market-metrics"', DASHBOARD_FRONTEND)
        self.assertIn('.us-market-summary-card.open .market-chevron', DASHBOARD_FRONTEND)
        self.assertIn('.market-monitor-card:hover, .us-market-summary-card:hover', DASHBOARD_FRONTEND)
        self.assertIn('.market-monitor-card.open, .us-market-summary-card.open', DASHBOARD_FRONTEND)
        self.assertNotIn('class="us-market-toggle"', DASHBOARD_FRONTEND)
        self.assertIn('usMarketSummaryExpanded = !usMarketSummaryExpanded;', DASHBOARD_FRONTEND)
        self.assertIn('summaryBody.hidden = !usMarketSummaryExpanded;', DASHBOARD_FRONTEND)
        self.assertIn("summaryCard?.classList.toggle('open', usMarketSummaryExpanded);", DASHBOARD_FRONTEND)
        self.assertIn(
            "${usSummaryHtml}${renderMarketDayPager(records, days, day, dayRecords)}`;",
            DASHBOARD_FRONTEND,
        )
        self.assertIn('const showLiveUsSummary = usMarketSummaryMatchesDay(day);', DASHBOARD_FRONTEND)
        self.assertIn('dayRecords.filter(record => !isUsMarketSummaryRecord(record))', DASHBOARD_FRONTEND)
        self.assertNotIn(
            "return `${usSummaryHtml}<div class=\"market-monitor-grid\">",
            DASHBOARD_FRONTEND,
        )

    def test_market_monitor_only_uses_live_us_summary_for_its_target_day(self):
        start = DASHBOARD_FRONTEND.index('function isUsMarketSummaryRecord')
        end = DASHBOARD_FRONTEND.index('function marketDateKey', start)
        functions = DASHBOARD_FRONTEND[start:end]
        scenario = r"""
const stored = {
  title:'隔夜美股盘面总结',
  source_id:'cron_output_98f0c8a12d3e',
  delivery:{job_id:'98f0c8a12d3e'},
};
const latest = {target_cn_date:'2026-07-13', target_us_date:'2026-07-10'};
const result = {
  matchingDay: usMarketSummaryMatchesDay('2026-07-13', latest),
  historicalDay: usMarketSummaryMatchesDay('2026-07-03', latest),
  missingTarget: usMarketSummaryMatchesDay('2026-07-13', {}),
  storedByTitle: isUsMarketSummaryRecord({title:'隔夜美股盘面总结'}),
  storedBySource: isUsMarketSummaryRecord({source_id:'cron_output_98f0c8a12d3e'}),
  storedByJob: isUsMarketSummaryRecord(stored),
  ordinaryRecord: isUsMarketSummaryRecord({title:'A股盘后总结'}),
};
console.log(JSON.stringify(result));
"""
        output = subprocess.check_output(
            ['node', '-e', functions + scenario],
            cwd=ROOT,
            text=True,
        )
        self.assertEqual(
            json.loads(output),
            {
                'matchingDay': True,
                'historicalDay': False,
                'missingTarget': False,
                'storedByTitle': True,
                'storedBySource': True,
                'storedByJob': True,
                'ordinaryRecord': False,
            },
        )

    def test_market_monitor_classifies_report_type_from_record_identity(self):
        start = DASHBOARD_FRONTEND.index('function marketReportType')
        end = DASHBOARD_FRONTEND.index('function marketSectionLines', start)
        functions = DASHBOARD_FRONTEND[start:end]
        scenario = r"""
const result = {
  close: marketReportType(
    {title:'A股盘后总结'},
    'A股盘后总结\n买入指引：次日竞价有溢价再执行',
  ),
  midday: marketReportType(
    {metadata:{job_name:'A股午盘总结'}},
    '正文也提到开盘竞价',
  ),
  auction: marketReportType(
    {source_id:'cron_output_8453b3f28cd3_2026-07-10'},
    '普通正文',
  ),
  usMarket: marketReportType(
    {title:'隔夜美股盘面总结'},
    '买入节奏：只做竞价有溢价的候选',
  ),
  closeByJob: marketReportType(
    {delivery:{job_id:'67ac98149ead'}},
    '普通正文',
  ),
  fallback: marketReportType({}, '没有类型信息的普通盘面记录'),
};
console.log(JSON.stringify(result));
"""
        output = subprocess.check_output(
            ['node', '-e', functions + scenario],
            cwd=ROOT,
            text=True,
        )
        self.assertEqual(
            json.loads(output),
            {
                'close': '盘后',
                'midday': '午盘',
                'auction': '竞价',
                'usMarket': '美股',
                'closeByJob': '盘后',
                'fallback': '盘面',
            },
        )
        self.assertNotIn("if (activeCategory === 'market_monitor') render();\n    }\n    return;", DASHBOARD_FRONTEND)
        self.assertIn('.us-market-summary-card.collapsed', DASHBOARD_FRONTEND)

    def test_http_server_absorbs_short_media_request_bursts(self):
        self.assertTrue(dashboard.ReusableThreadingHTTPServer.daemon_threads)
        self.assertGreaterEqual(dashboard.ReusableThreadingHTTPServer.request_queue_size, 64)

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
        self.assertIn("let usSectorData = {items: []};", DASHBOARD_FRONTEND)
        self.assertIn("fetch('/api/us_sectors')", DASHBOARD_FRONTEND)
        self.assertIn('function indicesSwitchSession(aIndexItems = [])', DASHBOARD_FRONTEND)
        self.assertIn("let indicesMarketRegionOverride = '';", DASHBOARD_FRONTEND)
        self.assertIn('function resolvedIndicesMarketRegion(aIndexItems = [])', DASHBOARD_FRONTEND)
        self.assertIn('function setIndicesMarketRegion(mode)', DASHBOARD_FRONTEND)
        self.assertIn("const marketRegion = resolvedIndicesMarketRegion(aIndexItems);", DASHBOARD_FRONTEND)
        self.assertIn("const marketUsesUsSectors = marketRegion === 'us';", DASHBOARD_FRONTEND)
        self.assertIn('aria-label="行情市场切换"', DASHBOARD_FRONTEND)
        self.assertIn('data-market-region="a_share"', DASHBOARD_FRONTEND)
        self.assertIn('data-market-region="us"', DASHBOARD_FRONTEND)
        self.assertIn("const activeTitleHtml = activePanel === 'index'", DASHBOARD_FRONTEND)
        self.assertIn('${activeTitleHtml}${indexPrioritySwitchHtml}${marketRegionSwitchHtml}', DASHBOARD_FRONTEND)
        self.assertNotIn('<h2 class="indices-part-title">${activeTitle}</h2>', DASHBOARD_FRONTEND)
        self.assertNotIn('indicesMarketRegionOverride,\n      savedAt', DASHBOARD_FRONTEND)
        self.assertIn('function renderUsSectorMarketBlock()', DASHBOARD_FRONTEND)
        self.assertIn('function renderSectorCloudHeading(source)', DASHBOARD_FRONTEND)
        self.assertIn('更新 ${esc(source.generated_at)}', DASHBOARD_FRONTEND)
        self.assertIn('${renderSectorCloudHeading(sec)}', DASHBOARD_FRONTEND)
        self.assertIn('${renderSectorCloudHeading(usSectorData)}', DASHBOARD_FRONTEND)
        self.assertNotIn('<h3>美股板块涨跌幅', DASHBOARD_FRONTEND)
        self.assertIn('rows.filter(row => Number.isFinite(row.pct) && row.pct > 0)', DASHBOARD_FRONTEND)
        self.assertIn('rows.filter(row => Number.isFinite(row.pct) && row.pct < 0)', DASHBOARD_FRONTEND)
        self.assertIn('暂无上涨板块', DASHBOARD_FRONTEND)
        self.assertIn('暂无下跌板块', DASHBOARD_FRONTEND)
        self.assertIn("s.a_share_mapping.slice(0, 3).join('、')", DASHBOARD_FRONTEND)
        self.assertNotIn("`A股映射 ${s.a_share_mapping.slice(0, 3).join('、')}`", DASHBOARD_FRONTEND)
        self.assertNotIn('const US_MARKET_QUOTE_SYMBOLS', DASHBOARD_FRONTEND)

    def test_indices_panel_can_put_a_share_or_us_indices_first(self):
        self.assertIn("const INDICES_INDEX_PRIORITY_STATE_KEY = 'niuniu-dashboard-index-priority-v1';", DASHBOARD_FRONTEND)
        self.assertIn("let indicesIndexPriorityOverride = '';", DASHBOARD_FRONTEND)
        self.assertIn('function setIndicesIndexPriority(mode)', DASHBOARD_FRONTEND)
        self.assertIn('function resolvedIndicesIndexPriority(aIndexItems = [])', DASHBOARD_FRONTEND)
        self.assertIn("sessionStorage.setItem(INDICES_INDEX_PRIORITY_STATE_KEY, mode)", DASHBOARD_FRONTEND)
        self.assertIn("const indexSections = indexPriority === 'a_share' ? [", DASHBOARD_FRONTEND)
        self.assertIn("['A股指数', aIndexItems],\n      ['美股指数', usIndexItems],", DASHBOARD_FRONTEND)
        self.assertIn("['美股指数', usIndexItems],\n      ['A股指数', aIndexItems],", DASHBOARD_FRONTEND)
        self.assertIn('return [...indexSections, ...supportingSections]', DASHBOARD_FRONTEND)
        self.assertIn('aria-label="指数排序切换"', DASHBOARD_FRONTEND)
        self.assertIn('data-index-priority="a_share"', DASHBOARD_FRONTEND)
        self.assertIn('data-index-priority="us"', DASHBOARD_FRONTEND)
        self.assertIn('A股在上', DASHBOARD_FRONTEND)
        self.assertIn('美股在上', DASHBOARD_FRONTEND)
        self.assertIn('${activeTitleHtml}${indexPrioritySwitchHtml}${marketRegionSwitchHtml}', DASHBOARD_FRONTEND)

    def test_index_template_github_button_links_to_repo_with_icon(self):
        self.assertIn(
            '<a class="header-link" href="https://github.com/kunkundi/niuone"',
            DASHBOARD_FRONTEND,
        )
        self.assertIn('.settings-link, .header-link { align-items:center;', DASHBOARD_FRONTEND)
        self.assertIn('.settings-link:hover, .header-link:hover', DASHBOARD_FRONTEND)
        self.assertIn('rel="noopener noreferrer"', DASHBOARD_FRONTEND)
        self.assertIn('<svg viewBox="0 0 16 16" aria-hidden="true"', DASHBOARD_FRONTEND)
        self.assertNotIn('<span class="header-text" title="开源仓库">GitHub</span>', DASHBOARD_FRONTEND)

    def test_index_template_inlines_trade_reasons_on_stock_cards(self):
        self.assertNotIn('买入战法绩效', DASHBOARD_FRONTEND)
        self.assertNotIn('BUY_COLORS', DASHBOARD_FRONTEND)
        self.assertNotIn('renderStrategyPerformance', DASHBOARD_FRONTEND)
        self.assertNotIn('practice-perf', DASHBOARD_FRONTEND)
        self.assertNotIn('exit-rule-row', DASHBOARD_FRONTEND)
        self.assertIn('x.bought_today', DASHBOARD_FRONTEND)
        self.assertIn('买入理由', DASHBOARD_FRONTEND)
        self.assertIn('卖出归因', DASHBOARD_FRONTEND)
        self.assertIn('最低/最高', DASHBOARD_FRONTEND)
        self.assertNotIn('最低涨幅', DASHBOARD_FRONTEND)
        self.assertNotIn('最高涨幅', DASHBOARD_FRONTEND)
        self.assertIn("main_board: '主板'", DASHBOARD_FRONTEND)
        self.assertIn('industryLabel = item.industry || item.sector || item.board_label || STOCK_BOARD_LABELS[item.board]', DASHBOARD_FRONTEND)
        self.assertNotIn('item.industry || item.sector || item.board ||', DASHBOARD_FRONTEND)
        self.assertIn('${esc(industryLabel)}</span>', DASHBOARD_FRONTEND)
        self.assertIn('white-space:nowrap', DASHBOARD_FRONTEND)
        self.assertNotIn('所属板块', DASHBOARD_FRONTEND)
        self.assertNotIn('板块 ${esc(industryLabel)}', DASHBOARD_FRONTEND)
        self.assertIn('仓位占比', DASHBOARD_FRONTEND)
        self.assertIn('可卖/持有', DASHBOARD_FRONTEND)
        self.assertNotIn('${esc(x.qty)}股', DASHBOARD_FRONTEND)
        self.assertIn('今日收益曲线', DASHBOARD_FRONTEND)
        self.assertIn('isNonTradingCalendarDay', DASHBOARD_FRONTEND)
        self.assertIn('tradingCalendar.is_trading_day === false', DASHBOARD_FRONTEND)
        self.assertIn('function renderPracticeChartTitle', DASHBOARD_FRONTEND)
        self.assertIn('class="practice-chart-title-measure" aria-hidden="true"', DASHBOARD_FRONTEND)
        self.assertIn('.practice-chart-title { display:inline-grid; flex:0 0 auto;', DASHBOARD_FRONTEND)
        self.assertIn('.practice-chart-title-text, .practice-chart-title-measure { grid-area:1 / 1;', DASHBOARD_FRONTEND)
        self.assertIn('.practice-chart-title-measure { visibility:hidden;', DASHBOARD_FRONTEND)
        self.assertIn('currentDateKey', DASHBOARD_FRONTEND)
        self.assertIn("timeZone: 'Asia/Shanghai'", DASHBOARD_FRONTEND)
        self.assertIn('practicePayloadDateKey', DASHBOARD_FRONTEND)
        self.assertIn('等待今日盘中净值点', DASHBOARD_FRONTEND)
        self.assertIn('最近已有分时点', DASHBOARD_FRONTEND)
        self.assertIn('累积收益曲线', DASHBOARD_FRONTEND)
        self.assertIn('practice-hover-tooltip', DASHBOARD_FRONTEND)
        self.assertIn('practice-chart-hover-layer', DASHBOARD_FRONTEND)
        self.assertIn('practiceHoverMove(event, this)', DASHBOARD_FRONTEND)
        self.assertIn('touch-action:none', DASHBOARD_FRONTEND)
        self.assertIn('data-practice-hover-points', DASHBOARD_FRONTEND)
        self.assertIn("layer.classList.toggle('place-left'", DASHBOARD_FRONTEND)
        self.assertIn("layer.classList.toggle('place-bottom'", DASHBOARD_FRONTEND)
        self.assertIn('收益金额', DASHBOARD_FRONTEND)
        self.assertIn('累计收益率', DASHBOARD_FRONTEND)
        self.assertIn('当日收益率', DASHBOARD_FRONTEND)
        self.assertIn('function renderPracticeTradeMarkers', DASHBOARD_FRONTEND)
        self.assertIn('practiceTradeMarkersForDate', DASHBOARD_FRONTEND)
        self.assertIn('practice-trade-marker-tooltip', DASHBOARD_FRONTEND)
        self.assertIn("const side = trade.action === 'BUY' ? '买' : '卖';", DASHBOARD_FRONTEND)
        self.assertIn('function renderPracticeTradeMarkerLine', DASHBOARD_FRONTEND)
        self.assertIn('practice-trade-marker-side', DASHBOARD_FRONTEND)
        self.assertIn('.practice-chart-card { position:relative; z-index:0; isolation:isolate; overflow:hidden;', DASHBOARD_FRONTEND)
        self.assertIn('.practice-trade-marker { --marker-size:18px; --marker-radius:9px; appearance:none;', DASHBOARD_FRONTEND)
        self.assertIn(
            'left:clamp(var(--marker-radius), var(--marker-x), calc(100% - var(--marker-radius)));',
            DASHBOARD_FRONTEND,
        )
        self.assertIn('min-width:var(--marker-size); max-width:var(--marker-size);', DASHBOARD_FRONTEND)
        self.assertIn(
            '.practice-calendar-day-curve-chart .practice-trade-marker { --marker-size:15px; --marker-radius:7.5px;',
            DASHBOARD_FRONTEND,
        )
        self.assertIn('style="--marker-x:${xPct.toFixed(2)}%;top:${yPct.toFixed(2)}%"', DASHBOARD_FRONTEND)
        self.assertIn('font-family:inherit; cursor:default;', DASHBOARD_FRONTEND)
        self.assertNotIn('font-family:inherit; cursor:help;', DASHBOARD_FRONTEND)
        self.assertIn('.practice-trade-marker-pnl.up { color:#ff6b6d; }', DASHBOARD_FRONTEND)
        self.assertIn('.practice-trade-marker-pnl.down { color:#39d98a; }', DASHBOARD_FRONTEND)
        self.assertIn('.practice-trade-marker.sell-partial { background:#f59e0b;', DASHBOARD_FRONTEND)
        self.assertIn('.practice-trade-marker.sell-full { background:#ef4444;', DASHBOARD_FRONTEND)
        self.assertIn("? 'sell-full'", DASHBOARD_FRONTEND)
        self.assertIn("? 'sell-partial' : 'sell-mixed'", DASHBOARD_FRONTEND)
        self.assertIn("trade.action === 'SELL' && trade.isFullExit", DASHBOARD_FRONTEND)
        self.assertIn("${practiceTradeShareText(trade.shares)}股×${practiceTradePriceText(trade.price)}", DASHBOARD_FRONTEND)
        self.assertIn('renderPracticeTradeMarkers(latestDay, xFromTime, plottedPts, w, h)', DASHBOARD_FRONTEND)
        self.assertIn('const tradeMarkerHtml = renderPracticeTradeMarkers(', DASHBOARD_FRONTEND)
        self.assertIn('class="practice-calendar-day-curve-chart"', DASHBOARD_FRONTEND)
        self.assertNotIn('practice-hover-readout', DASHBOARD_FRONTEND)
        self.assertNotIn('拖动查看收益曲线点位', DASHBOARD_FRONTEND)
        self.assertNotIn('每日总收益', DASHBOARD_FRONTEND)
        self.assertNotIn('if (points.length < 2) points = rawPoints.slice(-180);', DASHBOARD_FRONTEND)
        self.assertIn('交易日历', DASHBOARD_FRONTEND)
        self.assertIn('openPracticeCalendar(event)', DASHBOARD_FRONTEND)
        self.assertIn('buildPracticeCalendarRows', DASHBOARD_FRONTEND)
        self.assertIn('practice-calendar-popover', DASHBOARD_FRONTEND)
        self.assertIn('practiceCalendarSelectedDate', DASHBOARD_FRONTEND)
        self.assertIn('renderPracticeCalendarDayCurve', DASHBOARD_FRONTEND)
        self.assertIn('practice-calendar-day-curve', DASHBOARD_FRONTEND)
        self.assertIn('data-practice-calendar-date="${esc(date)}"', DASHBOARD_FRONTEND)
        self.assertIn('data-practice-calendar-action="clear-day"', DASHBOARD_FRONTEND)
        self.assertIn('data-practice-calendar-curve', DASHBOARD_FRONTEND)
        self.assertIn('selectedCls = date === practiceCalendarSelectedDate', DASHBOARD_FRONTEND)
        self.assertIn("practiceCalendarSelectedDate = practiceCalendarSelectedDate === nextDate ? '' : nextDate", DASHBOARD_FRONTEND)
        self.assertIn('sessionDayPoints', DASHBOARD_FRONTEND)
        self.assertIn('allDayHistoryPoints.at(-1)?.equity', DASHBOARD_FRONTEND)
        self.assertIn("? '分时加载失败 · '", DASHBOARD_FRONTEND)
        self.assertIn('practiceCalendarHistoryPoints(p)', DASHBOARD_FRONTEND)
        self.assertIn('practiceCalendarHistoryCoversDate(p, date)', DASHBOARD_FRONTEND)
        self.assertIn(
            'const needsFullHistory = isCurrentDate || (hasPartialHistory && !practiceCalendarHistoryCoversDate(p, date));',
            DASHBOARD_FRONTEND,
        )
        self.assertIn('分时曲线加载中…', DASHBOARD_FRONTEND)
        self.assertIn('分时曲线加载失败', DASHBOARD_FRONTEND)
        self.assertIn("time: `${date} 15:00:00`", DASHBOARD_FRONTEND)
        self.assertIn('const w = 464, h = 96', DASHBOARD_FRONTEND)
        self.assertIn('0轴 ${prevPoint ? esc(String(prevPoint.time || \'\').slice(5, 16)) : \'初始资金\'}', DASHBOARD_FRONTEND)
        self.assertIn('position:absolute; left:0; right:0; bottom:calc(100% + 8px)', DASHBOARD_FRONTEND)
        self.assertIn('overflow:visible', DASHBOARD_FRONTEND)
        self.assertIn('width:min(390px', DASHBOARD_FRONTEND)
        self.assertIn('transform:translate(-50%,-50%)', DASHBOARD_FRONTEND)
        self.assertNotIn('max-height:min(76vh, 640px); display:grid; gap:8px', DASHBOARD_FRONTEND)
        self.assertNotIn('practice-calendar-popover::before', DASHBOARD_FRONTEND)
        self.assertNotIn('filter:blur(18px)', DASHBOARD_FRONTEND)
        self.assertIn('border:1px solid transparent', DASHBOARD_FRONTEND)
        self.assertIn('linear-gradient(135deg, rgba(96,165,250,.68), rgba(124,92,255,.56) 48%, rgba(52,211,153,.32)) border-box', DASHBOARD_FRONTEND)
        self.assertIn('background:linear-gradient(180deg, #172033, #101827)', DASHBOARD_FRONTEND)
        self.assertIn('background:rgba(31,42,62,.72)', DASHBOARD_FRONTEND)
        self.assertIn('practice-calendar-no-data', DASHBOARD_FRONTEND)
        self.assertIn('grid-template-columns:repeat(5, minmax(0, 1.14fr)) repeat(2, minmax(30px, .72fr))', DASHBOARD_FRONTEND)
        self.assertIn('dayOfWeek === 0 || dayOfWeek === 6', DASHBOARD_FRONTEND)
        self.assertIn('practice-calendar-day.weekend', DASHBOARD_FRONTEND)
        self.assertIn('practice-calendar-weekday.weekend', DASHBOARD_FRONTEND)
        self.assertIn('weekendTodayMarker = isToday && isWeekend && !row', DASHBOARD_FRONTEND)
        self.assertIn('inlineTodayMarker = isToday && !weekendTodayMarker', DASHBOARD_FRONTEND)
        self.assertIn('class="practice-calendar-today weekend-today"', DASHBOARD_FRONTEND)
        self.assertIn('grid-row:2; align-self:end; justify-self:start; padding:0 3px', DASHBOARD_FRONTEND)
        self.assertNotIn('practice-calendar-day.weekend { min-height', DASHBOARD_FRONTEND)
        self.assertNotIn('align-self:start', DASHBOARD_FRONTEND)
        self.assertIn("${date}${isWeekend ? ' 周末' : ''}", DASHBOARD_FRONTEND)
        self.assertIn('signedCellPct', DASHBOARD_FRONTEND)
        self.assertIn('signedCellAmount', DASHBOARD_FRONTEND)
        self.assertIn('aria-label="${esc(fullText)}"', DASHBOARD_FRONTEND)
        self.assertIn('practice-calendar-grid', DASHBOARD_FRONTEND)
        self.assertIn('data-practice-calendar-action="prev"', DASHBOARD_FRONTEND)
        self.assertNotIn('practice-calendar-backdrop', DASHBOARD_FRONTEND)
        self.assertNotIn('practiceCalendarAnchor', DASHBOARD_FRONTEND)
        self.assertNotIn('practice-calendar-values empty', DASHBOARD_FRONTEND)
        self.assertNotIn('<h3 class="practice-panel-title"><span>牛牛实战 · 模拟账户</span><button class="practice-calendar-open-btn"', DASHBOARD_FRONTEND)
        self.assertIn('<h3>模拟账户</h3>', DASHBOARD_FRONTEND)
        self.assertNotIn('最近交易日收益', DASHBOARD_FRONTEND)
        self.assertNotIn('getDay() === 0 || nowForCurve.getDay() === 6', DASHBOARD_FRONTEND)

    def test_index_template_loads_calendar_history_without_waiting_for_full_snapshot(self):
        self.assertIn("const VIEW_STATE_KEY = 'niuniu-dashboard-view-state-v5';", DASHBOARD_FRONTEND)
        self.assertIn("'/api/niuniu_practice?fast=1&calendar_schema=1'", DASHBOARD_FRONTEND)
        self.assertIn("fetchJson('/api/niuniu_practice?snapshot_schema=2')", DASHBOARD_FRONTEND)
        self.assertIn('const fullPracticePromise = practiceFullRequest;', DASHBOARD_FRONTEND)
        self.assertIn('function mergePracticePayloadSnapshots', DASHBOARD_FRONTEND)
        self.assertIn('function mergePracticeEquityRows', DASHBOARD_FRONTEND)
        self.assertIn('function comparePracticePayloadFreshness', DASHBOARD_FRONTEND)
        self.assertIn("String(payload.equity_history_scope || '') === 'unavailable'", DASHBOARD_FRONTEND)
        self.assertNotIn("typeof payload !== 'object' || payload.last_error", DASHBOARD_FRONTEND)
        self.assertIn('if (seq !== practiceLoadSeq) return;', DASHBOARD_FRONTEND)
        self.assertIn("practiceFullSnapshotStatus = 'loading';", DASHBOARD_FRONTEND)
        self.assertIn("practiceFullSnapshotStatus = 'loaded';", DASHBOARD_FRONTEND)
        self.assertIn("practiceFullSnapshotStatus = 'error';", DASHBOARD_FRONTEND)
        self.assertIn('function compactPracticeCalendarHistoryPoints', DASHBOARD_FRONTEND)
        self.assertIn('calendar.complete !== true', DASHBOARD_FRONTEND)
        self.assertIn('buildPracticeCalendarRows(practiceCalendarHistoryPoints(p)', DASHBOARD_FRONTEND)
        self.assertIn('renderPracticeCurve(p.equity_history || []', DASHBOARD_FRONTEND)

    def test_index_template_separates_single_stock_retries_from_quote_channels(self):
        self.assertIn(
            '`腾讯${channels.tencent ?? 0}/东财${channels.eastmoney ?? 0}/Sina${channels.sina ?? 0}`',
            DASHBOARD_FRONTEND,
        )
        self.assertNotIn('/单票${channels.single', DASHBOARD_FRONTEND)
        self.assertIn(
            'const singleRetryText = singleRetryCount ? `，单股重试${singleRetryCount}只` : \'\';',
            DASHBOARD_FRONTEND,
        )

    def test_index_template_uses_independent_category_routes(self):
        self.assertIn("let activeCategory = categoryFromLocation(initialParams);", DASHBOARD_FRONTEND)
        self.assertIn("const CATEGORY_ORDER = ['practice', 'indices', 'market_monitor', 'x_monitor', 'us_ratings'];", DASHBOARD_FRONTEND)
        self.assertIn("practice:'模拟交易'", DASHBOARD_FRONTEND)
        self.assertIn("practice: '/practice'", DASHBOARD_FRONTEND)
        self.assertIn("indices: '/indices'", DASHBOARD_FRONTEND)
        self.assertIn("const LEGACY_CATEGORY_ALIASES = {b1_screen:'practice'};", DASHBOARD_FRONTEND)
        self.assertIn('const normalized = LEGACY_CATEGORY_ALIASES[category] || category;', DASHBOARD_FRONTEND)
        self.assertIn("fetchJson('/api/practice_candidates')", DASHBOARD_FRONTEND)
        self.assertIn("actionFetch('/api/practice_candidates/refresh')", DASHBOARD_FRONTEND)
        self.assertIn('async function loadPracticePage()', DASHBOARD_FRONTEND)
        self.assertIn('function renderPracticePage()', DASHBOARD_FRONTEND)
        self.assertIn("location.pathname + location.search !== currentViewUrl()", DASHBOARD_FRONTEND)
        self.assertNotIn('href="/?category=', DASHBOARD_FRONTEND)
        self.assertNotIn('function loadB1Screen', DASHBOARD_FRONTEND)
        self.assertNotIn('function renderB1Screen', DASHBOARD_FRONTEND)
        self.assertNotIn("fetchJson('/api/b1_screen')", DASHBOARD_FRONTEND)
        self.assertNotIn("actionFetch('/api/b1_screen/trigger')", DASHBOARD_FRONTEND)
        self.assertIn("actionFetch('/api/niuniu_practice/manual-cycle')", DASHBOARD_FRONTEND)
        self.assertIn("fetch('/api/niuniu_practice/manual-cycle', {cache:'no-store'})", DASHBOARD_FRONTEND)
        self.assertIn('手动触发选股及买卖策略', DASHBOARD_FRONTEND)
        self.assertIn('盘面评价 · ${esc(marketContext.tone_label', DASHBOARD_FRONTEND)
        self.assertIn('let practiceMarketSummaryExpanded = false;', DASHBOARD_FRONTEND)
        self.assertIn('class="practice-market-summary-card ${expanded ? \'open\' : \'collapsed\'}', DASHBOARD_FRONTEND)
        self.assertIn('class="practice-market-summary-body"${expanded ? \'\' : \' hidden\'}', DASHBOARD_FRONTEND)
        self.assertIn('function togglePracticeMarketSummary()', DASHBOARD_FRONTEND)
        self.assertIn("disabled aria-busy=\"true\"", DASHBOARD_FRONTEND)

    def test_index_snapshot_merge_handles_business_errors_and_stale_full_responses(self):
        start = DASHBOARD_FRONTEND.index('function mergePracticeTimedRows')
        end = DASHBOARD_FRONTEND.index('async function loadPracticePage', start)
        functions = DASHBOARD_FRONTEND[start:end]
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
        self.assertIn("const decisionModel = String(p.decision_model || '').trim();", DASHBOARD_FRONTEND)
        self.assertIn("practiceFullSnapshotStatus === 'error' ? '未知' : '加载中'", DASHBOARD_FRONTEND)
        self.assertIn('delete niuniuPracticeData.decision_model;', DASHBOARD_FRONTEND)
        self.assertIn('delete niuniuPracticeData.decision_provider;', DASHBOARD_FRONTEND)
        self.assertIn("{cache: 'no-cache'}", DASHBOARD_FRONTEND)
        self.assertNotIn("p.decision_model || 'deepseek-v4-pro'", DASHBOARD_FRONTEND)

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

    def test_expired_api_cache_returns_stale_while_only_one_refresh_runs(self):
        cache_key = 'slow-market-data'
        stale_payload = json.dumps({'version': 'stale'}).encode('utf-8')
        dashboard.API_RESPONSE_CACHE[cache_key] = {
            'ts': dashboard.time.time() - 11,
            'payload': stale_payload,
        }
        producer_started = dashboard.threading.Event()
        release_producer = dashboard.threading.Event()
        producer_calls = []

        def producer():
            producer_calls.append(True)
            producer_started.set()
            self.assertTrue(release_producer.wait(timeout=2))
            return {'version': 'fresh'}

        payload, hit = dashboard.cache_get_json(cache_key, 10, producer)
        self.assertTrue(hit)
        self.assertEqual(payload, stale_payload)
        self.assertTrue(producer_started.wait(timeout=2))

        second_payload, second_hit = dashboard.cache_get_json(cache_key, 10, producer)
        self.assertTrue(second_hit)
        self.assertEqual(second_payload, stale_payload)
        self.assertEqual(len(producer_calls), 1)

        release_producer.set()
        deadline = dashboard.time.time() + 2
        while dashboard.time.time() < deadline:
            with dashboard.API_RESPONSE_LOCK:
                cached = dashboard.API_RESPONSE_CACHE.get(cache_key, {})
                if cached.get('payload') != stale_payload:
                    break
            dashboard.time.sleep(0.01)
        with dashboard.API_RESPONSE_LOCK:
            refreshed = dashboard.API_RESPONSE_CACHE[cache_key]['payload']
        self.assertEqual(json.loads(refreshed), {'version': 'fresh'})

    def test_api_cache_older_than_stale_window_refreshes_synchronously(self):
        cache_key = 'too-old-market-data'
        original_window = dashboard.API_STALE_WHILE_REFRESH_SECONDS
        dashboard.API_STALE_WHILE_REFRESH_SECONDS = 1
        dashboard.API_RESPONSE_CACHE[cache_key] = {
            'ts': dashboard.time.time() - 3,
            'payload': json.dumps({'version': 'too-old'}).encode('utf-8'),
        }
        try:
            payload, hit = dashboard.cache_get_json(cache_key, 1, lambda: {'version': 'fresh'})
            self.assertFalse(hit)
            self.assertEqual(json.loads(payload), {'version': 'fresh'})
        finally:
            dashboard.API_STALE_WHILE_REFRESH_SECONDS = original_window

    def test_cold_api_cache_is_seeded_from_durable_snapshot_as_stale(self):
        snapshot = self.tmp_path / 'sectors.json'
        snapshot.write_text(
            json.dumps({'generated_at': '2026-07-11 09:30:00', 'items': [{'name': '银行'}]}),
            encoding='utf-8',
        )
        before = dashboard.time.time()
        seeded = dashboard.seed_api_cache_from_json_file('sectors', snapshot, 60)

        self.assertTrue(seeded)
        cached = dashboard.API_RESPONSE_CACHE['sectors']
        payload = json.loads(cached['payload'])
        self.assertTrue(payload['stale_cache'])
        self.assertEqual(payload['items'], [{'name': '银行'}])
        self.assertLessEqual(cached['ts'], before - 60)
        self.assertFalse(dashboard.seed_api_cache_from_json_file('sectors', snapshot, 60))

    def test_index_template_intraday_curve_renders_single_point_from_opening_base(self):
        self.assertIn('if (rawPoints.length < (isDailyMode ? 2 : 1))', DASHBOARD_FRONTEND)
        self.assertIn('if (sessionPoints.length >= 1)', DASHBOARD_FRONTEND)
        self.assertIn('isNonTradingCalendarDay && dayPoints.length >= 2', DASHBOARD_FRONTEND)
        self.assertIn('if (points.length < 1)', DASHBOARD_FRONTEND)
        self.assertIn(
            'if (points.length < 2) return \'<div class="empty" style="padding:18px">累计收益等待更多交易日净值点…</div>\';',
            DASHBOARD_FRONTEND,
        )
        self.assertIn(
            'const hasIntradayOpenBase = !isDailyMode && Number.isFinite(intradayBaseEquity) && intradayBaseEquity > 0;',
            DASHBOARD_FRONTEND,
        )
        self.assertIn(
            'const chartBase = isDailyMode ? initialCash : (hasIntradayOpenBase ? intradayBaseEquity : vals[0]);',
            DASHBOARD_FRONTEND,
        )
        self.assertIn('const axisPcts = hasIntradayOpenBase ? [0, ...chartPcts] : chartPcts;', DASHBOARD_FRONTEND)
        self.assertIn('const openAnchor = [left, y(0)];', DASHBOARD_FRONTEND)
        self.assertIn('pts.unshift(openAnchor);', DASHBOARD_FRONTEND)
        self.assertIn('hasSyntheticOpenAnchor = true;', DASHBOARD_FRONTEND)
        self.assertIn(
            '} else if (!isDailyMode && points.length > 1 && pts.length > 0 && pts[0][0] > left + 1) {',
            DASHBOARD_FRONTEND,
        )
        self.assertIn('const hasCurveSegment = pts.length > 1;', DASHBOARD_FRONTEND)
        self.assertIn('const drawdownVals = hasIntradayOpenBase ? [chartBase, ...vals] : vals;', DASHBOARD_FRONTEND)
        self.assertIn('time: `${latestDay} 09:30:00`', DASHBOARD_FRONTEND)
        self.assertIn('const intradayBaseLabel = hasIntradayOpenBase', DASHBOARD_FRONTEND)

    def test_configured_admin_password_issues_secure_session_and_unlocks_settings(self):
        dashboard.ADMIN_PASSWORD = '管理员密码'

        locked_api = FakeHandler(path='/api/admin/config')
        locked_api.do_GET()
        self.assertEqual(locked_api.status, 403)

        wrong_body = urllib.parse.urlencode({'admin_password': '错误密码'}).encode('utf-8')
        wrong = FakeHandler(
            path='/api/admin/session',
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
            path='/api/admin/session',
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

        self.assertEqual(login.status, 200)
        self.assertTrue(json.loads(login.wfile.getvalue().decode('utf-8'))['ok'])
        set_cookie = login.header('Set-Cookie') or ''
        self.assertTrue(set_cookie.startswith(f'{dashboard.ADMIN_SESSION_COOKIE_NAME}=ad_'))
        self.assertIn('HttpOnly', set_cookie)
        self.assertIn('SameSite=Lax', set_cookie)
        self.assertIn('Secure', set_cookie)
        session_cookie = set_cookie.split(';', 1)[0]

        unlocked_page = FakeHandler(path='/admin', headers={'Cookie': session_cookie})
        unlocked_page.do_GET()
        self.assertEqual(unlocked_page.status, 200)
        self.assertIn('<script src="/static/admin.js?v=17" defer></script>', unlocked_page.wfile.getvalue().decode('utf-8'))

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
                path='/api/admin/session',
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
                path='/api/admin/session',
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
            path='/api/admin/config/env/access-control',
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
            path='/api/admin/session',
            method='POST',
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Content-Length': str(len(body)),
            },
            body=body,
        )
        login.do_POST()
        self.assertEqual(login.status, 200)

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

    def test_notification_test_api_requires_admin_action_and_whitelists_target_fields(self):
        original_sender = dashboard.send_notification_test
        original_admin_limit = dashboard.RATE_LIMIT_ADMIN
        original_test_limit = dashboard.RATE_LIMIT_NOTIFICATION_TEST
        calls = []
        body = urllib.parse.urlencode({
            'channel': 'telegram',
            'env__DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS': '4',
            'env__DASHBOARD_TELEGRAM_BOT_TOKEN': 'temporary-token',
            'env__DASHBOARD_TELEGRAM_CHAT_ID': '-1001234567890',
            'env__DASHBOARD_FEISHU_WEBHOOK_URL': 'must-be-ignored',
            'env__DASHBOARD_NOTIFICATION_ENABLED': '1',
            'unrelated': 'must-be-ignored',
        }).encode('utf-8')
        try:
            dashboard.RATE_LIMIT_ADMIN = 100
            dashboard.RATE_LIMIT_NOTIFICATION_TEST = 100
            dashboard.send_notification_test = (
                lambda channel, overrides: calls.append((channel, dict(overrides)))
                or {'ok': True, 'channel': channel, 'message': 'Telegram 测试通知已发送'}
            )

            unauthorized = FakeHandler(
                path='/api/admin/notifications/test',
                method='POST',
                headers={
                    'Content-Length': str(len(body)),
                    dashboard.ACTION_HEADER_NAME: '1',
                },
                body=body,
            )
            unauthorized.do_POST()
            self.assertEqual(unauthorized.status, 403)
            self.assertEqual(unauthorized.rfile.tell(), 0)

            missing_action = FakeHandler(
                path='/api/admin/notifications/test',
                method='POST',
                headers={
                    'Content-Length': str(len(body)),
                    'Cookie': self.admin_cookie(),
                },
                body=body,
            )
            missing_action.do_POST()
            self.assertEqual(missing_action.status, 403)
            self.assertEqual(missing_action.rfile.tell(), 0)

            handler = FakeHandler(
                path='/api/admin/notifications/test',
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
            dashboard.send_notification_test = original_sender
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit
            dashboard.RATE_LIMIT_NOTIFICATION_TEST = original_test_limit

        self.assertEqual(handler.status, 200)
        self.assertTrue(response['ok'])
        self.assertEqual(calls, [(
            'telegram',
            {
                'DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS': '4',
                'DASHBOARD_TELEGRAM_BOT_TOKEN': 'temporary-token',
                'DASHBOARD_TELEGRAM_CHAT_ID': '-1001234567890',
            },
        )])

    def test_practice_market_summary_status_is_public_and_generation_requires_admin_action(self):
        original_status = dashboard.get_practice_market_summary_status
        original_generate = dashboard.generate_practice_market_summary
        original_admin_limit = dashboard.RATE_LIMIT_ADMIN
        calls = []
        try:
            dashboard.RATE_LIMIT_ADMIN = 100
            dashboard.get_practice_market_summary_status = lambda: {
                'ok': True, 'available': False, 'scan_count': 2,
            }
            dashboard.generate_practice_market_summary = lambda: calls.append(True) or {
                'ok': True, 'available': True, 'scan_count': 2, 'summary': '今日汇总',
            }

            status_handler = FakeHandler(path=dashboard.PRACTICE_MARKET_SUMMARY_API_PATH)
            status_handler.do_GET()
            self.assertEqual(status_handler.status, 200)
            self.assertEqual(json.loads(status_handler.wfile.getvalue().decode('utf-8'))['scan_count'], 2)

            unauthorized = FakeHandler(
                path=dashboard.PRACTICE_MARKET_SUMMARY_API_PATH,
                method='POST',
                headers={'Content-Length': '0', dashboard.ACTION_HEADER_NAME: '1'},
            )
            unauthorized.do_POST()
            self.assertEqual(unauthorized.status, 403)
            self.assertEqual(calls, [])

            generated = FakeHandler(
                path=dashboard.PRACTICE_MARKET_SUMMARY_API_PATH,
                method='POST',
                headers={
                    'Content-Length': '0',
                    'Cookie': self.admin_cookie(),
                    dashboard.ACTION_HEADER_NAME: '1',
                },
            )
            generated.do_POST()
            self.assertEqual(generated.status, 200)
            self.assertEqual(json.loads(generated.wfile.getvalue().decode('utf-8'))['summary'], '今日汇总')
            self.assertEqual(calls, [True])
        finally:
            dashboard.get_practice_market_summary_status = original_status
            dashboard.generate_practice_market_summary = original_generate
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit

    def test_manual_market_summary_snapshot_force_refreshes_live_channels(self):
        original_runner = dashboard.run_dashboard_helper
        original_builder = dashboard.practice_market_summary_impl.build_realtime_market_snapshot
        calls = []
        captured = {}
        try:
            def fake_runner(script_name, fallback, timeout=90, args=()):
                calls.append((script_name, timeout, args))
                return {"script": script_name}

            def fake_builder(indices, sectors, money_flow, now):
                captured.update({
                    "indices": indices,
                    "sectors": sectors,
                    "money_flow": money_flow,
                    "now": now,
                })
                return {"complete": True, "time": now.strftime('%Y-%m-%d %H:%M:%S')}

            dashboard.run_dashboard_helper = fake_runner
            dashboard.practice_market_summary_impl.build_realtime_market_snapshot = fake_builder
            now = datetime(2026, 7, 14, 12, 0, 0)

            result = dashboard.fetch_practice_realtime_market_snapshot(now)
        finally:
            dashboard.run_dashboard_helper = original_runner
            dashboard.practice_market_summary_impl.build_realtime_market_snapshot = original_builder

        self.assertTrue(result["complete"])
        self.assertEqual({call[0] for call in calls}, {
            "indices_dashboard_api.py",
            "sectors_dashboard_api.py",
            "money_flow_dashboard_api.py",
        })
        self.assertTrue(all(call[1:] == (120, ("--force-refresh",)) for call in calls))
        self.assertEqual(captured["indices"]["script"], "indices_dashboard_api.py")
        self.assertEqual(captured["now"], now)

    def test_notification_test_api_has_dedicated_rate_limit_and_body_limit(self):
        original_sender = dashboard.send_notification_test
        original_admin_limit = dashboard.RATE_LIMIT_ADMIN
        original_test_limit = dashboard.RATE_LIMIT_NOTIFICATION_TEST
        calls = []
        body = b'channel=wecom&env__DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS=5'
        try:
            dashboard.RATE_LIMIT_ADMIN = 100
            dashboard.RATE_LIMIT_NOTIFICATION_TEST = 1
            dashboard.RATE_LIMIT_BUCKETS.clear()
            dashboard.send_notification_test = (
                lambda channel, overrides: calls.append((channel, dict(overrides)))
                or {'ok': True, 'channel': channel, 'message': 'ok'}
            )
            headers = {
                'Content-Length': str(len(body)),
                'Cookie': self.admin_cookie(),
                dashboard.ACTION_HEADER_NAME: '1',
            }
            first = FakeHandler(
                path='/api/admin/notifications/test', method='POST', headers=headers, body=body,
            )
            first.do_POST()
            second = FakeHandler(
                path='/api/admin/notifications/test', method='POST', headers=headers, body=body,
            )
            second.do_POST()

            dashboard.RATE_LIMIT_BUCKETS.clear()
            dashboard.RATE_LIMIT_NOTIFICATION_TEST = 100
            oversized = FakeHandler(
                path='/api/admin/notifications/test',
                method='POST',
                headers={
                    'Content-Length': str(dashboard.MAX_POST_BODY_BYTES + 1),
                    'Cookie': self.admin_cookie(),
                    dashboard.ACTION_HEADER_NAME: '1',
                },
                body=b'credential-must-not-be-read',
            )
            oversized.do_POST()
        finally:
            dashboard.send_notification_test = original_sender
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit
            dashboard.RATE_LIMIT_NOTIFICATION_TEST = original_test_limit
            dashboard.RATE_LIMIT_BUCKETS.clear()

        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 429)
        self.assertEqual(second.rfile.tell(), 0)
        self.assertEqual(calls, [('wecom', {'DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS': '5'})])
        self.assertEqual(oversized.status, 413)
        self.assertEqual(oversized.rfile.tell(), 0)
        self.assertEqual(
            json.loads(oversized.wfile.getvalue().decode('utf-8'))['error'],
            'request_too_large',
        )

    def test_notification_test_api_get_and_head_are_method_not_allowed(self):
        get_handler = FakeHandler(path='/api/admin/notifications/test')
        get_handler.do_GET()
        head_handler = FakeHandler(path='/api/admin/notifications/test', method='HEAD')
        head_handler.do_HEAD()

        self.assertEqual(get_handler.status, 405)
        self.assertEqual(get_handler.header('Allow'), 'POST')
        self.assertEqual(head_handler.status, 405)
        self.assertEqual(head_handler.header('Allow'), 'POST')

    def test_unauthenticated_config_writes_are_rejected_before_reading_body(self):
        original_config_path = dashboard.CONFIG_PATH
        dashboard.CONFIG_PATH = self.tmp_path / 'config.yaml'
        dashboard.DASHBOARD_ENV_FILE.write_text('DASHBOARD_GROK_MODEL=safe\n', encoding='utf-8')
        dashboard.CONFIG_PATH.write_text('model:\n  default: safe\n', encoding='utf-8')
        try:
            cases = (
                ('/api/admin/config/env', b'env__DASHBOARD_GROK_MODEL=attacker'),
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
                    self.assertEqual(handler.status, 403)
                    self.assertEqual(
                        json.loads(handler.wfile.getvalue().decode('utf-8'))['error'],
                        'admin_password_required',
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

    def test_authenticated_config_writes_require_action_header(self):
        original_config_path = dashboard.CONFIG_PATH
        dashboard.CONFIG_PATH = self.tmp_path / 'config.yaml'
        dashboard.DASHBOARD_ENV_FILE.write_text('DASHBOARD_GROK_MODEL=safe\n', encoding='utf-8')
        dashboard.CONFIG_PATH.write_text('model:\n  default: safe\n', encoding='utf-8')
        admin_cookie = self.admin_cookie()
        try:
            cases = (
                ('/api/admin/config/env', b'env__DASHBOARD_GROK_MODEL=attacker'),
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
        payload = dashboard.build_admin_config_payload()
        handler = FakeHandler(path='/admin', headers={'Cookie': self.admin_cookie()})
        handler.do_GET()
        index_body = handler.wfile.getvalue().decode('utf-8')
        item_names = {item['name'] for item in payload['items']}

        self.assertEqual(handler.status, 200)
        self.assertEqual(len(payload['groups']), 12)
        self.assertEqual(item_names, set(dashboard.ADMIN_VISIBLE_ENV_NAMES))
        self.assertIn('<script src="/static/admin.js?v=17" defer></script>', index_body)
        self.assertNotIn("name='env__", index_body)
        self.assertIn('function renderSettingsIndex()', ADMIN_FRONTEND)
        self.assertIn('function renderSettingsGroup(slug)', ADMIN_FRONTEND)
        self.assertIn("data-save-endpoint='/api/admin/config/env/", ADMIN_FRONTEND)
        self.assertIn("'X-NiuOne-Action': '1'", ADMIN_FRONTEND)
        self.assertIn("window.history.pushState({}, '', link.getAttribute('href'))", ADMIN_FRONTEND)
        self.assertIn("class='settings-back-link' href='/admin' data-settings-route", ADMIN_FRONTEND)
        self.assertNotIn("class='toplink' href='/admin' data-settings-route>全部设置", ADMIN_FRONTEND)
        self.assertIn("button.disabled = true;", ADMIN_FRONTEND)
        self.assertIn("button.textContent = '已保存';", ADMIN_FRONTEND)
        self.assertIn("</div></section></form></div>", ADMIN_FRONTEND)
        self.assertIn('data-saved-state="0"] .settings-actions', ADMIN_FRONTEND)
        self.assertIn('function brieflyShowEnvSaved(form)', ADMIN_FRONTEND)
        self.assertIn("String(event.key || '').toLowerCase() !== 's'", ADMIN_FRONTEND)
        self.assertIn('function renderEnvInput(item)', ADMIN_FRONTEND)
        self.assertNotIn('HIDDEN-CODE', ADMIN_FRONTEND)
        self.assertNotIn('/admin/invite', ADMIN_FRONTEND)

    def test_admin_settings_groups_have_standalone_pages(self):
        payload = dashboard.build_admin_config_payload()
        groups = payload['groups']
        slugs = [group['slug'] for group in groups]
        grouped_names = set()
        for slug in slugs:
            names = dashboard.admin_setting_group_env_names(slug)
            self.assertTrue(names, slug)
            self.assertTrue(grouped_names.isdisjoint(names), slug)
            grouped_names.update(names)
            route = FakeHandler(path=f'/admin/settings/{slug}')
            route.do_GET()
            self.assertEqual(route.status, 200)
            self.assertIn('<script src="/static/admin.js?v=17" defer></script>', route.wfile.getvalue().decode('utf-8'))

        self.assertEqual(len(groups), 12)
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertEqual(slugs[:2], ['access-control', 'notifications'])
        self.assertEqual(slugs.index('trading-risk'), slugs.index('decision-model') + 1)
        self.assertEqual(slugs.index('decision-reference'), slugs.index('decision-times') + 1)
        self.assertEqual(slugs.index('us-market'), slugs.index('stock-strategy') + 1)
        self.assertEqual(grouped_names, set(dashboard.ADMIN_VISIBLE_ENV_NAMES))
        self.assertIn("data-save-endpoint='/api/admin/config/env/", ADMIN_FRONTEND)
        self.assertIn("settingsGroupSlug()", ADMIN_FRONTEND)
        self.assertIn('保存本组设置', ADMIN_FRONTEND)
        self.assertEqual(len(dashboard.admin_setting_group_env_names('us-market')), 16)
        decision_group = next(group for group in groups if group['slug'] == 'decision-times')
        self.assertEqual(decision_group['name'], '选股与买卖设置')
        strategy_group = next(group for group in groups if group['slug'] == 'stock-strategy')
        self.assertEqual(strategy_group['name'], '选股与交易策略')
        decision_names = dashboard.admin_setting_group_env_names('decision-times')
        self.assertIn('DASHBOARD_DISPLAY_CANDIDATE_LIMIT', decision_names)
        self.assertIn('DASHBOARD_TRADE_CANDIDATE_LIMIT', decision_names)
        self.assertIn('DASHBOARD_STOCK_UNIVERSE', decision_names)
        config_by_name = {item['name']: item for item in dashboard.ENV_CONFIG_SCHEMA}
        self.assertEqual(config_by_name['DASHBOARD_GROK_API_MODE']['kind'], 'api_mode')
        self.assertEqual(config_by_name['DASHBOARD_GROK_API_MODE']['default'], 'auto')
        self.assertEqual(dashboard.normalize_env_update('DASHBOARD_GROK_API_MODE', 'responses', 'api_mode'), 'responses')
        self.assertEqual(dashboard.normalize_env_update('DASHBOARD_GROK_API_MODE', 'chat-completions', 'api_mode'), 'chat')
        self.assertEqual(
            dashboard.normalize_business_updates({'DASHBOARD_GROK_API_MODE': 'chat-completions'}),
            {'DASHBOARD_GROK_API_MODE': 'chat'},
        )
        with self.assertRaises(ValueError):
            dashboard.normalize_business_updates({'DASHBOARD_GROK_API_MODE': 'invalid'})
        with self.assertRaises(ValueError):
            dashboard.validate_business_updates({'X_WATCHLIST_REQUEST_TIMEOUT_SECONDS': '7'})
        self.assertIn("kind === 'api_mode'", ADMIN_FRONTEND)
        self.assertEqual(config_by_name['DASHBOARD_DISPLAY_CANDIDATE_LIMIT']['default'], '10')
        self.assertEqual(config_by_name['DASHBOARD_TRADE_CANDIDATE_LIMIT']['default'], '10')
        self.assertEqual(config_by_name['DASHBOARD_STOCK_UNIVERSE']['default'], 'main_board')
        universe_item = next(item for item in payload['items'] if item['name'] == 'DASHBOARD_STOCK_UNIVERSE')
        self.assertEqual(universe_item['stock_universe_values'], ['main_board'])
        self.assertEqual(
            [option['label'] for option in universe_item['stock_universe_options']],
            ['ST', '创业板', '科创板', '主板'],
        )
        self.assertIn("kind === 'stock_universe'", ADMIN_FRONTEND)
        decision_model_names = dashboard.admin_setting_group_env_names('decision-model')
        decision_reference_names = dashboard.admin_setting_group_env_names('decision-reference')
        trading_risk_names = dashboard.admin_setting_group_env_names('trading-risk')
        self.assertEqual(len(decision_model_names), 6)
        self.assertEqual(
            decision_reference_names,
            {
                'DASHBOARD_DECISION_INTELLIGENCE_ENABLED',
                'DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS',
                'DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS',
            },
        )
        self.assertEqual(
            config_by_name['DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS']['label'],
            '单类参考数据上限',
        )
        self.assertEqual(
            trading_risk_names,
            {
                'DASHBOARD_MARKET_GUIDANCE_ENABLED',
                'DASHBOARD_TRADE_DISCIPLINE_TEXT',
                'DASHBOARD_MAX_OPEN_POSITIONS',
                'DASHBOARD_MAX_NEW_BUYS_PER_DECISION',
                'DASHBOARD_MAX_SINGLE_POSITION_PCT',
                'DASHBOARD_MAX_TOTAL_POSITION_PCT',
                'DASHBOARD_MIN_CASH_RESERVE_PCT',
                'DASHBOARD_MORNING_MAX_OPEN_POSITIONS',
            },
        )
        self.assertNotIn('DASHBOARD_TRADE_DISCIPLINE_TEXT', decision_model_names)

    def test_candidate_limit_settings_require_positive_integers(self):
        dashboard.validate_business_updates({
            'DASHBOARD_DISPLAY_CANDIDATE_LIMIT': '16',
            'DASHBOARD_TRADE_CANDIDATE_LIMIT': '8',
        })
        for name in ('DASHBOARD_DISPLAY_CANDIDATE_LIMIT', 'DASHBOARD_TRADE_CANDIDATE_LIMIT'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                dashboard.validate_business_updates({name: '0'})

    def test_stock_universe_setting_requires_known_non_empty_choices(self):
        dashboard.validate_business_updates({
            'DASHBOARD_STOCK_UNIVERSE': 'main_board,st,chi_next',
        })
        self.assertEqual(
            dashboard.normalize_business_updates({
                'DASHBOARD_STOCK_UNIVERSE': 'main_board,st,chi_next',
            })['DASHBOARD_STOCK_UNIVERSE'],
            'st,chi_next,main_board',
        )
        for value in ('', 'beijing'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                dashboard.validate_business_updates({'DASHBOARD_STOCK_UNIVERSE': value})

    def test_admin_settings_group_routes_use_static_shell_and_api_auth(self):
        locked = FakeHandler(path='/admin/settings/notifications')
        locked.do_GET()
        locked_body = locked.wfile.getvalue().decode('utf-8')
        self.assertEqual(locked.status, 200)
        self.assertIn('<script src="/static/admin.js?v=17" defer></script>', locked_body)
        self.assertNotIn("name='env__DASHBOARD_NOTIFICATION_ENABLED'", locked_body)

        cookie = self.admin_cookie()
        unlocked = FakeHandler(
            path='/admin/settings/notifications',
            headers={'Cookie': cookie},
        )
        unlocked.do_GET()
        unlocked_body = unlocked.wfile.getvalue().decode('utf-8')
        self.assertEqual(unlocked.status, 200)
        self.assertEqual(unlocked_body, locked_body)

        config = FakeHandler(path='/api/admin/config', headers={'Cookie': cookie})
        config.do_GET()
        self.assertEqual(config.status, 200)
        self.assertIn('DASHBOARD_NOTIFICATION_ENABLED', config.wfile.getvalue().decode('utf-8'))

        missing = FakeHandler(
            path='/admin/settings/not-a-group',
            headers={'Cookie': cookie},
        )
        missing.do_GET()
        self.assertEqual(missing.status, 404)
        self.assertIn('<script src="/static/admin.js?v=17" defer></script>', missing.wfile.getvalue().decode('utf-8'))
        self.assertIn('未找到该设置分组', ADMIN_FRONTEND)

    def test_group_save_ignores_fields_from_other_settings_groups(self):
        original_values = {
            name: dashboard.os.environ.get(name)
            for name in (
                'DASHBOARD_NEWS_MODEL',
                'DASHBOARD_NEWS_API_KEY',
                'DASHBOARD_GROK_MODEL',
            )
        }
        try:
            for name in original_values:
                dashboard.os.environ.pop(name, None)
            dashboard.DASHBOARD_ENV_FILE.write_text(
                'DASHBOARD_NEWS_MODEL=old-news\nDASHBOARD_GROK_MODEL=old-grok\n',
                encoding='utf-8',
            )
            dashboard.os.environ['DASHBOARD_GROK_MODEL'] = 'process-grok'
            dashboard.os.environ['DASHBOARD_NEWS_API_KEY'] = 'process-news-secret'
            body = urllib.parse.urlencode({
                'env__DASHBOARD_NEWS_MODEL': 'new-news',
                'env__DASHBOARD_NEWS_API_KEY': '',
                'env__DASHBOARD_GROK_MODEL': 'cross-group-attempt',
            }).encode('utf-8')
            handler = FakeHandler(
                path='/api/admin/config/env/news-precheck',
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
            result = json.loads(handler.wfile.getvalue().decode('utf-8'))
            stored = dashboard.parse_env_file(
                dashboard.DASHBOARD_ENV_FILE,
                include_container_overrides=False,
            )
            runtime_grok = dashboard.os.environ.get('DASHBOARD_GROK_MODEL')
            runtime_news_secret = dashboard.os.environ.get('DASHBOARD_NEWS_API_KEY')
        finally:
            for name, value in original_values.items():
                if value is None:
                    dashboard.os.environ.pop(name, None)
                else:
                    dashboard.os.environ[name] = value

        self.assertEqual(handler.status, 200)
        self.assertEqual(result['group']['slug'], 'news-precheck')
        self.assertEqual(result['changed_names'], ['DASHBOARD_NEWS_MODEL'])
        self.assertEqual(stored['DASHBOARD_NEWS_MODEL'], 'new-news')
        self.assertEqual(stored['DASHBOARD_GROK_MODEL'], 'old-grok')
        self.assertEqual(runtime_grok, 'process-grok')
        self.assertEqual(runtime_news_secret, 'process-news-secret')

        missing = FakeHandler(
            path='/api/admin/config/env/not-a-group',
            method='POST',
            headers={
                'Cookie': self.admin_cookie(),
                dashboard.ACTION_HEADER_NAME: '1',
            },
        )
        missing.do_POST()
        self.assertEqual(missing.status, 404)
        self.assertEqual(
            json.loads(missing.wfile.getvalue().decode('utf-8'))['error'],
            'unknown_settings_group',
        )

    def test_group_persist_lock_covers_runtime_sync(self):
        original_write = dashboard._write_env_file_values_unlocked
        original_sync = dashboard.sync_business_runtime_settings
        first_sync_started = threading.Event()
        release_first_sync = threading.Event()
        second_write_started = threading.Event()
        events = []

        def fake_write(updates, path=None, *, clear_names=None):
            name = next(iter(updates))
            events.append(f'write:{name}')
            if name == 'SECOND':
                second_write_started.set()
            return {
                'ok': True,
                'changed': True,
                'changed_names': [name],
            }

        def fake_sync(changed, *, sync_names=None):
            name = next(iter(changed))
            events.append(f'sync:{name}')
            if name == 'FIRST':
                first_sync_started.set()
                release_first_sync.wait(2)
            return {'ok': True, 'changed_names': list(changed), 'applied': []}

        first = threading.Thread(
            target=dashboard.persist_and_sync_business_updates,
            args=({'FIRST': '1'},),
        )
        second = threading.Thread(
            target=dashboard.persist_and_sync_business_updates,
            args=({'SECOND': '1'},),
        )
        try:
            dashboard._write_env_file_values_unlocked = fake_write
            dashboard.sync_business_runtime_settings = fake_sync
            first.start()
            self.assertTrue(first_sync_started.wait(1))
            second.start()
            interleaved = second_write_started.wait(0.1)
        finally:
            release_first_sync.set()
            first.join(2)
            second.join(2)
            dashboard._write_env_file_values_unlocked = original_write
            dashboard.sync_business_runtime_settings = original_sync

        self.assertFalse(interleaved)
        self.assertEqual(events, ['write:FIRST', 'sync:FIRST', 'write:SECOND', 'sync:SECOND'])

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
        disabled = FakeHandler(path='/api/dashboard/bootstrap')
        disabled.do_GET()
        disabled_payload = json.loads(disabled.wfile.getvalue().decode('utf-8'))

        dashboard.DASHBOARD_ENV_FILE.write_text('DASHBOARD_US_FEATURES_ENABLED=1\n', encoding='utf-8')
        enabled = FakeHandler(path='/api/dashboard/bootstrap')
        enabled.do_GET()
        enabled_payload = json.loads(enabled.wfile.getvalue().decode('utf-8'))

        self.assertEqual(disabled.status, 200)
        self.assertFalse(disabled_payload['us_features_enabled'])
        self.assertEqual(enabled.status, 200)
        self.assertTrue(enabled_payload['us_features_enabled'])
        self.assertIn('let US_FEATURES_ENABLED = false;', DASHBOARD_FRONTEND)
        self.assertIn("fetch('/api/dashboard/bootstrap'", DASHBOARD_FRONTEND)
        self.assertIn('activeCategory = normalizeActiveCategory(activeCategory);', DASHBOARD_FRONTEND)

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

    def test_admin_config_loads_yaml_once_per_payload(self):
        original_loader = dashboard.load_yaml_config
        provider_names = (
            'DASHBOARD_GROK_BASE_URL',
            'DASHBOARD_GROK_API_KEY',
            'DASHBOARD_DECISION_BASE_URL',
            'DASHBOARD_DECISION_API_KEY',
        )
        original_values = {name: dashboard.os.environ.pop(name, None) for name in provider_names}
        calls = []
        try:
            dashboard.load_yaml_config = lambda: calls.append(True) or {
                'custom_providers': [{
                    'name': 'Crossdesk',
                    'base_url': 'https://crossdesk.example/v1',
                    'api_key': 'crossdesk-secret',
                }],
            }
            payload = dashboard.build_admin_config_payload()
        finally:
            dashboard.load_yaml_config = original_loader
            for name, value in original_values.items():
                if value is None:
                    dashboard.os.environ.pop(name, None)
                else:
                    dashboard.os.environ[name] = value

        self.assertEqual(len(calls), 1)
        by_name = {item['name']: item for item in payload['items']}
        self.assertEqual(by_name['DASHBOARD_GROK_BASE_URL']['effective'], 'https://crossdesk.example/v1')
        self.assertEqual(by_name['DASHBOARD_DECISION_BASE_URL']['effective'], 'https://crossdesk.example/v1')
        self.assertEqual(by_name['DASHBOARD_GROK_API_KEY']['current_state'], '已设置')
        self.assertNotIn('crossdesk-secret', json.dumps(payload, ensure_ascii=False))

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
            for name in [dashboard.ACTIVE_STRATEGY_ENV, dashboard.STRATEGY_SOURCE_ENV, dashboard.PRESET_STRATEGY_TEXT_ENV, dashboard.TRADE_DISCIPLINE_TEXT_ENV]
        }
        try:
            for name in original_env_values:
                dashboard.os.environ.pop(name, None)
            dashboard.DASHBOARD_ENV_FILE.write_text(
                "DASHBOARD_ACTIVE_STRATEGY=preset_text\n"
                "DASHBOARD_PRESET_STRATEGY_TEXT='强趋势回踩\\n跌破5日线离场'\n"
                "DASHBOARD_TRADE_DISCIPLINE_TEXT='纪律一\\n纪律二'\n",
                encoding='utf-8',
            )

            payload = dashboard.build_admin_config_payload()
            source_item = next(item for item in payload['items'] if item['name'] == dashboard.ACTIVE_STRATEGY_ENV)
            text_item = next(item for item in payload['items'] if item['name'] == dashboard.PRESET_STRATEGY_TEXT_ENV)
            discipline_item = next(item for item in payload['items'] if item['name'] == dashboard.TRADE_DISCIPLINE_TEXT_ENV)
        finally:
            for name, value in original_env_values.items():
                if value is None:
                    dashboard.os.environ.pop(name, None)
                else:
                    dashboard.os.environ[name] = value

        self.assertEqual(source_item['effective'], '预设文字策略')
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

        body = ADMIN_FRONTEND
        self.assertIn("'4096；例如 2048 或 8192'", body)
        self.assertIn("'4096 tokens；填写后覆盖请求 max_tokens'", body)
        self.assertIn("'128000；例如 128K、1M 或 1000000'", body)
        self.assertIn("'128000 tokens；填写后保存为数字 tokens'", body)

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
        telegram_chat_id = '-1001234567890'
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
                'env__DASHBOARD_ACTIVE_STRATEGY': 'preset_text',
                'env__DASHBOARD_PRESET_STRATEGY_TEXT': '只做主线强趋势回踩\n跌破5日线离场',
                'env__DASHBOARD_TRADE_DISCIPLINE_TEXT': '纪律一\n纪律二',
                'env__DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED': '1',
                'env__DASHBOARD_TELEGRAM_CHAT_ID': telegram_chat_id,
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
            response_text = handler.wfile.getvalue().decode('utf-8')
            response = json.loads(response_text)
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
        self.assertIn('config', response)
        self.assertNotIn('news-secret', response_text)
        config_by_name = {item['name']: item for item in response['config']['items']}
        self.assertEqual(config_by_name['DASHBOARD_NEWS_API_KEY']['current_state'], '已设置')
        self.assertEqual(config_by_name['DASHBOARD_NEWS_API_KEY']['file_value'], '')
        self.assertEqual(config_by_name['DASHBOARD_GROK_CONTEXT_LENGTH']['current_state'], '1000000')
        self.assertEqual(config_by_name['DASHBOARD_TELEGRAM_CHAT_ID']['current_state'], '已设置')
        self.assertNotEqual(config_by_name['DASHBOARD_TELEGRAM_CHAT_ID']['current_state'], telegram_chat_id)
        self.assertEqual(response['restart']['skipped'], 'hot_applied')
        self.assertTrue(response['runtime']['ok'])
        self.assertIn('b1_schedule_times', response['runtime']['applied'])
        self.assertIn('indices_ttl', response['runtime']['applied'])
        self.assertIn('active_strategy', response['runtime']['applied'])
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
        self.assertEqual(parsed['DASHBOARD_ACTIVE_STRATEGY'], 'preset_text')
        self.assertEqual(parsed['DASHBOARD_PRESET_STRATEGY_TEXT'], '只做主线强趋势回踩\\n跌破5日线离场')
        self.assertEqual(parsed['DASHBOARD_TRADE_DISCIPLINE_TEXT'], '纪律一\\n纪律二')
        self.assertEqual(parsed['DASHBOARD_TELEGRAM_CHAT_ID'], telegram_chat_id)
        self.assertEqual(runtime_b1_times, ('09:25', '10:00', '14:50'))
        self.assertEqual(runtime_indices_ttl, 20)
        self.assertNotIn('DASHBOARD_HOME', parsed)

    def test_admin_config_api_does_not_restart_without_changes(self):
        original_env_file = dashboard.DASHBOARD_ENV_FILE
        original_admin_limit = dashboard.RATE_LIMIT_ADMIN
        original_restart = dashboard.schedule_niuone_services_restart
        original_env_values = {name: dashboard.os.environ.get(name) for name in dashboard.ADMIN_VISIBLE_ENV_NAMES}
        restart_calls = []
        try:
            dashboard.DASHBOARD_ENV_FILE = self.tmp_path / 'dashboard.env'
            dashboard.DASHBOARD_ENV_FILE.write_text(
                'DASHBOARD_GROK_CONTEXT_LENGTH=1000000\n'
                'DASHBOARD_NEWS_API_KEY=news-secret\n',
                encoding='utf-8',
            )
            dashboard.RATE_LIMIT_ADMIN = 100
            dashboard.schedule_niuone_services_restart = (
                lambda: restart_calls.append(True) or {'ok': True}
            )
            body = urllib.parse.urlencode({
                'env__DASHBOARD_GROK_CONTEXT_LENGTH': '1M',
                'env__DASHBOARD_NEWS_API_KEY': '',
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
            response_text = handler.wfile.getvalue().decode('utf-8')
            response = json.loads(response_text)
        finally:
            dashboard.DASHBOARD_ENV_FILE = original_env_file
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit
            dashboard.schedule_niuone_services_restart = original_restart
            for name, value in original_env_values.items():
                if value is None:
                    dashboard.os.environ.pop(name, None)
                else:
                    dashboard.os.environ[name] = value

        self.assertEqual(handler.status, 200)
        self.assertEqual(restart_calls, [])
        self.assertFalse(response['changed'])
        self.assertIn('config', response)
        self.assertNotIn('news-secret', response_text)
        config_by_name = {item['name']: item for item in response['config']['items']}
        self.assertEqual(config_by_name['DASHBOARD_NEWS_API_KEY']['current_state'], '已设置')
        self.assertEqual(config_by_name['DASHBOARD_NEWS_API_KEY']['file_value'], '')
        self.assertEqual(config_by_name['DASHBOARD_GROK_CONTEXT_LENGTH']['current_state'], '1000000')
        self.assertEqual(response['restart']['skipped'], 'unchanged')

    def test_admin_config_api_removing_notification_channel_deletes_its_config(self):
        original_admin_limit = dashboard.RATE_LIMIT_ADMIN
        names = (
            'DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED',
            'DASHBOARD_TELEGRAM_BOT_TOKEN',
            'DASHBOARD_TELEGRAM_CHAT_ID',
            'DASHBOARD_FEISHU_NOTIFICATION_ENABLED',
            'DASHBOARD_FEISHU_WEBHOOK_URL',
        )
        original_env_values = {name: dashboard.os.environ.get(name) for name in names}
        telegram_token = '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi'
        feishu_webhook = 'https://open.feishu.cn/open-apis/bot/v2/hook/preserved-feishu-hook'
        try:
            for name in names:
                dashboard.os.environ.pop(name, None)
            dashboard.write_env_file_values({
                'DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED': '1',
                'DASHBOARD_TELEGRAM_BOT_TOKEN': telegram_token,
                'DASHBOARD_TELEGRAM_CHAT_ID': '-1001234567890',
                'DASHBOARD_FEISHU_NOTIFICATION_ENABLED': '1',
                'DASHBOARD_FEISHU_WEBHOOK_URL': feishu_webhook,
            })
            dashboard.os.environ.update({
                'DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED': '1',
                'DASHBOARD_TELEGRAM_BOT_TOKEN': telegram_token,
                'DASHBOARD_TELEGRAM_CHAT_ID': '-1001234567890',
                'DASHBOARD_FEISHU_NOTIFICATION_ENABLED': '1',
                'DASHBOARD_FEISHU_WEBHOOK_URL': feishu_webhook,
            })
            dashboard.RATE_LIMIT_ADMIN = 100
            body = urllib.parse.urlencode({
                'env__DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED': '0',
                'notification_remove__telegram': '1',
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
            response_text = handler.wfile.getvalue().decode('utf-8')
            response = json.loads(response_text)
            stored = dashboard.parse_env_file(
                dashboard.DASHBOARD_ENV_FILE,
                include_container_overrides=False,
            )
            config_by_name = {item['name']: item for item in response['config']['items']}
            runtime_after_removal = {name: dashboard.os.environ.get(name) for name in names}
        finally:
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit
            for name, value in original_env_values.items():
                if value is None:
                    dashboard.os.environ.pop(name, None)
                else:
                    dashboard.os.environ[name] = value

        self.assertEqual(handler.status, 200)
        self.assertTrue(response['ok'])
        self.assertNotIn(telegram_token, response_text)
        self.assertNotIn(feishu_webhook, response_text)
        self.assertNotIn('DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED', stored)
        self.assertNotIn('DASHBOARD_TELEGRAM_BOT_TOKEN', stored)
        self.assertNotIn('DASHBOARD_TELEGRAM_CHAT_ID', stored)
        self.assertIsNone(runtime_after_removal['DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED'])
        self.assertIsNone(runtime_after_removal['DASHBOARD_TELEGRAM_BOT_TOKEN'])
        self.assertIsNone(runtime_after_removal['DASHBOARD_TELEGRAM_CHAT_ID'])
        self.assertEqual(stored['DASHBOARD_FEISHU_NOTIFICATION_ENABLED'], '1')
        self.assertEqual(stored['DASHBOARD_FEISHU_WEBHOOK_URL'], feishu_webhook)
        self.assertEqual(config_by_name['DASHBOARD_TELEGRAM_BOT_TOKEN']['current_state'], '未设置')
        self.assertEqual(config_by_name['DASHBOARD_TELEGRAM_CHAT_ID']['current_state'], '未设置')

    def test_admin_config_api_deactivating_notification_channel_preserves_its_config(self):
        original_admin_limit = dashboard.RATE_LIMIT_ADMIN
        names = (
            'DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED',
            'DASHBOARD_TELEGRAM_BOT_TOKEN',
            'DASHBOARD_TELEGRAM_CHAT_ID',
        )
        original_env_values = {name: dashboard.os.environ.get(name) for name in names}
        telegram_token = '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi'
        try:
            for name in names:
                dashboard.os.environ.pop(name, None)
            dashboard.write_env_file_values({
                'DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED': '1',
                'DASHBOARD_TELEGRAM_BOT_TOKEN': telegram_token,
                'DASHBOARD_TELEGRAM_CHAT_ID': '-1001234567890',
            })
            dashboard.os.environ.update({
                'DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED': '1',
                'DASHBOARD_TELEGRAM_BOT_TOKEN': telegram_token,
                'DASHBOARD_TELEGRAM_CHAT_ID': '-1001234567890',
            })
            dashboard.RATE_LIMIT_ADMIN = 100
            body = urllib.parse.urlencode({
                'env__DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED': '0',
                'notification_remove__telegram': '0',
            }).encode('utf-8')
            handler = FakeHandler(
                path='/api/admin/config/env/notifications',
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
            response_text = handler.wfile.getvalue().decode('utf-8')
            response = json.loads(response_text)
            stored = dashboard.parse_env_file(
                dashboard.DASHBOARD_ENV_FILE,
                include_container_overrides=False,
            )
            runtime_after_deactivation = {name: dashboard.os.environ.get(name) for name in names}
        finally:
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit
            for name, value in original_env_values.items():
                if value is None:
                    dashboard.os.environ.pop(name, None)
                else:
                    dashboard.os.environ[name] = value

        self.assertEqual(handler.status, 200)
        self.assertTrue(response['ok'])
        self.assertNotIn(telegram_token, response_text)
        self.assertEqual(stored['DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED'], '0')
        self.assertEqual(stored['DASHBOARD_TELEGRAM_BOT_TOKEN'], telegram_token)
        self.assertEqual(stored['DASHBOARD_TELEGRAM_CHAT_ID'], '-1001234567890')
        self.assertEqual(runtime_after_deactivation['DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED'], '0')
        self.assertEqual(runtime_after_deactivation['DASHBOARD_TELEGRAM_BOT_TOKEN'], telegram_token)
        self.assertEqual(runtime_after_deactivation['DASHBOARD_TELEGRAM_CHAT_ID'], '-1001234567890')

    def test_settings_page_omits_contest_panel_and_config(self):
        payload = dashboard.build_admin_config_payload()
        body = ADMIN_FRONTEND

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
