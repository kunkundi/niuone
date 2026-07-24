#!/usr/bin/env python3
import importlib.util
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

from fastapi.testclient import TestClient

from app.dashboard.fastapi_app import SPA_DASHBOARD_PATHS, create_app

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
    path.read_text(encoding='utf-8')
    for path in (
        FRONTEND / 'dashboard.css',
        *sorted((ROOT / 'web' / 'src').rglob('*.js')),
        *sorted((ROOT / 'web' / 'src' / 'components').rglob('*.vue')),
    )
)
ADMIN_FRONTEND = '\n'.join(
    path.read_text(encoding='utf-8')
    for path in (
        FRONTEND / 'admin.css',
        ROOT / 'web' / 'src' / 'composables' / 'useAdminConfig.js',
        *sorted((ROOT / 'web' / 'src' / 'components').glob('Admin*.vue')),
    )
)
MARKET_MONITOR_UTILS_PATH = ROOT / 'web' / 'src' / 'utils' / 'marketMonitorDisplay.js'
MARKET_MONITOR_UTILS = MARKET_MONITOR_UTILS_PATH.read_text(encoding='utf-8')
MARKET_MONITOR_DATA = (
    ROOT / 'web' / 'src' / 'composables' / 'useMarketMonitorData.js'
).read_text(encoding='utf-8')
MARKET_MONITOR_COMPONENTS = '\n'.join(
    path.read_text(encoding='utf-8')
    for path in (
        ROOT / 'web' / 'src' / 'components' / 'MarketMonitorPanel.vue',
        ROOT / 'web' / 'src' / 'components' / 'market-monitor' / 'MarketMonitorCard.vue',
        ROOT / 'web' / 'src' / 'components' / 'market-monitor' / 'MarketDetail.vue',
        ROOT / 'web' / 'src' / 'components' / 'market-monitor' / 'MarketSection.vue',
        ROOT / 'web' / 'src' / 'components' / 'market-monitor' / 'UsMarketSummaryCard.vue',
    )
)
INDUSTRY_FLOW_DATA_UTIL_PATH = ROOT / 'web' / 'src' / 'utils' / 'industryFlowData.js'
RESPONSIVE_STAGE_UTIL_PATH = ROOT / 'web' / 'src' / 'utils' / 'responsiveStage.js'
ASYNC_PAYLOAD_UTIL_PATH = ROOT / 'web' / 'src' / 'utils' / 'asyncPayload.js'
US_RATING_UTILS_PATH = ROOT / 'web' / 'src' / 'utils' / 'usRatingDisplay.js'
US_RATING_UTILS = US_RATING_UTILS_PATH.read_text(encoding='utf-8')
US_RATING_DATA = (
    ROOT / 'web' / 'src' / 'composables' / 'useUsRatingsData.js'
).read_text(encoding='utf-8')
US_RATING_COMPONENTS = '\n'.join(
    path.read_text(encoding='utf-8')
    for path in (
        ROOT / 'web' / 'src' / 'components' / 'UsRatingsPanel.vue',
        ROOT / 'web' / 'src' / 'components' / 'us-ratings' / 'UsRatingCard.vue',
        ROOT / 'web' / 'src' / 'components' / 'us-ratings' / 'RatingText.vue',
    )
)
X_MONITOR_UTILS_PATH = ROOT / 'web' / 'src' / 'utils' / 'xMonitorDisplay.js'
X_MONITOR_UTILS = X_MONITOR_UTILS_PATH.read_text(encoding='utf-8')
X_MONITOR_DATA = (
    ROOT / 'web' / 'src' / 'composables' / 'useXMonitorData.js'
).read_text(encoding='utf-8')
X_MONITOR_COMPONENTS = '\n'.join(
    path.read_text(encoding='utf-8')
    for path in (
        ROOT / 'web' / 'src' / 'components' / 'XMonitorPanel.vue',
        ROOT / 'web' / 'src' / 'components' / 'x-monitor' / 'XMonitorRow.vue',
        ROOT / 'web' / 'src' / 'components' / 'x-monitor' / 'XMediaGallery.vue',
        ROOT / 'web' / 'src' / 'components' / 'x-monitor' / 'XImageViewer.vue',
    )
)
PRACTICE_CANDIDATE_UTILS_PATH = (
    ROOT / 'web' / 'src' / 'utils' / 'practiceCandidateDisplay.js'
)
PRACTICE_CANDIDATE_UTILS = PRACTICE_CANDIDATE_UTILS_PATH.read_text(encoding='utf-8')
PRACTICE_CANDIDATE_DATA = (
    ROOT / 'web' / 'src' / 'composables' / 'usePracticeCandidatesData.js'
).read_text(encoding='utf-8')
PUBLIC_PROJECTION_DATA = (
    ROOT / 'web' / 'src' / 'composables' / 'usePublicProjection.js'
).read_text(encoding='utf-8')
PRACTICE_DATA = (
    ROOT / 'web' / 'src' / 'composables' / 'usePracticeData.js'
).read_text(encoding='utf-8')
PRACTICE_PAYLOAD_UTILS_PATH = ROOT / 'web' / 'src' / 'utils' / 'practicePayload.js'
PRACTICE_PAYLOAD_UTILS = PRACTICE_PAYLOAD_UTILS_PATH.read_text(encoding='utf-8')
PRACTICE_CHART_UTILS_PATH = ROOT / 'web' / 'src' / 'utils' / 'practiceChart.js'
PRACTICE_CHART_UTILS = PRACTICE_CHART_UTILS_PATH.read_text(encoding='utf-8')
INDUSTRY_FLOW_ANIMATION_PATH = (
    ROOT / 'web' / 'src' / 'composables' / 'useIndustryFlowAnimation.js'
)
PRACTICE_LOG_UTILS = (
    ROOT / 'web' / 'src' / 'utils' / 'practiceLogs.js'
).read_text(encoding='utf-8')
PRACTICE_CANDIDATE_COMPONENTS = '\n'.join(
    path.read_text(encoding='utf-8')
    for path in (
        ROOT / 'web' / 'src' / 'components' / 'PracticeCandidatesPanel.vue',
        ROOT / 'web' / 'src' / 'components' / 'practice' / 'PracticeCandidateCard.vue',
    )
)
PRACTICE_COMPONENTS = '\n'.join(
    path.read_text(encoding='utf-8')
    for path in (
        ROOT / 'web' / 'src' / 'components' / 'DashboardPage.vue',
        ROOT / 'web' / 'src' / 'components' / 'PracticePanel.vue',
        ROOT / 'web' / 'src' / 'components' / 'practice' / 'PracticeAccountOverview.vue',
        ROOT / 'web' / 'src' / 'components' / 'practice' / 'PracticeCalendar.vue',
        ROOT / 'web' / 'src' / 'components' / 'practice' / 'PracticeEquityChart.vue',
        ROOT / 'web' / 'src' / 'components' / 'practice' / 'PracticeMarketSummary.vue',
        ROOT / 'web' / 'src' / 'components' / 'practice' / 'PracticeOperationLog.vue',
        ROOT / 'web' / 'src' / 'components' / 'practice' / 'PracticePositionCard.vue',
        ROOT / 'web' / 'src' / 'components' / 'practice' / 'PracticePositions.vue',
        ROOT / 'web' / 'src' / 'components' / 'practice' / 'PracticeRule.vue',
        ROOT / 'web' / 'src' / 'components' / 'practice' / 'PracticeSoldCard.vue',
    )
)


class FakeHandler:
    """Temporary response-shaped wrapper around the production ASGI app."""

    def __init__(self, path='/', method='GET', headers=None, body=b'', ip='127.0.0.1'):
        self.path = path
        self.command = method
        self.headers = headers or {}
        self.body = body
        self.ip = ip
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.sent_headers = []

    def dispatch(self, method):
        app = create_app(
            legacy_module=dashboard,
            web_dist_dir=ROOT / 'web' / 'dist',
            enable_background_services=False,
        )
        with TestClient(app, client=(self.ip, 12345)) as client:
            response = client.request(
                method,
                self.path,
                headers=self.headers,
                content=self.body,
            )
        self.status = response.status_code
        self.sent_headers = list(response.headers.multi_items())
        self.wfile = io.BytesIO(response.content)

    def do_GET(self):
        self.dispatch('GET')

    def do_HEAD(self):
        self.dispatch('HEAD')

    def do_POST(self):
        self.dispatch('POST')

    def client_ip(self):
        if dashboard.is_trusted_proxy_ip(self.ip):
            forwarded = dashboard.first_forwarded_ip(
                self.headers.get('CF-Connecting-IP'),
                self.headers.get('X-Forwarded-For'),
            )
            if forwarded:
                return forwarded
        return self.ip

    def is_secure_request(self):
        if not dashboard.is_trusted_proxy_ip(self.ip):
            return False
        if dashboard.is_truthy_header(self.headers.get('X-Forwarded-Proto')):
            return True
        cf_visitor = self.headers.get('CF-Visitor') or ''
        return '"scheme":"https"' in cf_visitor.replace(' ', '').lower()

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
        self.original_indices_snapshot_file = dashboard.INDICES_SNAPSHOT_FILE
        self.original_iwencai_snapshot_file = dashboard.IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE
        self.original_market_breadth_history_file = dashboard.MARKET_BREADTH_HISTORY_FILE
        self.original_industry_flow_history_file = dashboard.INDUSTRY_FLOW_HISTORY_FILE
        self.original_money_flow_snapshot_file = dashboard.MONEY_FLOW_SNAPSHOT_FILE
        self.original_admin_password = dashboard.ADMIN_PASSWORD
        self.original_public_data_dir = dashboard.PUBLIC_DATA_DIR
        self.original_public_snapshot_publisher = dashboard.PUBLIC_SNAPSHOT_PUBLISHER
        self.saved_env = {
            name: os.environ.get(name)
            for name in (
                'DASHBOARD_ADMIN_PASSWORD',
                'X_WATCHLIST_ACCOUNTS',
                'DASHBOARD_X_WATCHLIST_STATE',
                dashboard.STRATEGY_SOURCE_ENV,
                dashboard.PERSONA_STRATEGY_ENV,
                dashboard.PRESET_STRATEGY_TEXT_ENV,
                'IWENCAI_ENABLED',
                'IWENCAI_BASE_URL',
                'IWENCAI_API_KEY',
                'IWENCAI_TIMEOUT_SECONDS',
                'IWENCAI_MAX_RETRIES',
                'IWENCAI_MAX_CONCURRENCY',
                'IWENCAI_CACHE_TTL_SECONDS',
                'IWENCAI_DRAGON_TIGER_CRON',
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
        dashboard.INDICES_SNAPSHOT_FILE = self.tmp_path / 'cron' / 'output' / 'indices_dashboard_cache.json'
        dashboard.IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE = self.tmp_path / 'cron' / 'output' / 'iwencai_dragon_tiger_latest.json'
        dashboard.MARKET_BREADTH_HISTORY_FILE = self.tmp_path / 'cron' / 'output' / 'market_breadth_history.json'
        dashboard.INDUSTRY_FLOW_HISTORY_FILE = self.tmp_path / 'cron' / 'output' / 'industry_main_flow_history.json'
        dashboard.MONEY_FLOW_SNAPSHOT_FILE = self.tmp_path / 'cron' / 'output' / 'industry_main_money_flow_cache.json'
        dashboard.PUBLIC_DATA_DIR = self.tmp_path / 'public-data'
        dashboard.PUBLIC_SNAPSHOT_PUBLISHER = None
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
        dashboard.INDICES_SNAPSHOT_FILE = self.original_indices_snapshot_file
        dashboard.IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE = self.original_iwencai_snapshot_file
        dashboard.MARKET_BREADTH_HISTORY_FILE = self.original_market_breadth_history_file
        dashboard.INDUSTRY_FLOW_HISTORY_FILE = self.original_industry_flow_history_file
        dashboard.MONEY_FLOW_SNAPSHOT_FILE = self.original_money_flow_snapshot_file
        dashboard.ADMIN_PASSWORD = self.original_admin_password
        dashboard.PUBLIC_DATA_DIR = self.original_public_data_dir
        dashboard.PUBLIC_SNAPSHOT_PUBLISHER = self.original_public_snapshot_publisher
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
        self.assertIn('<div id="app">', home.wfile.getvalue().decode('utf-8'))

        admin = FakeHandler(path='/admin')
        admin.do_GET()
        self.assertEqual(admin.status, 200)
        admin_body = admin.wfile.getvalue().decode('utf-8')
        self.assertIn('<div id="app">', admin_body)
        self.assertIn('<script type="module"', admin_body)
        self.assertNotIn('name="admin_password"', admin_body)
        self.assertNotIn("name='env__DASHBOARD_GROK_API_KEY'", admin_body)
        self.assertIn("fetch('/api/admin/config'", ADMIN_FRONTEND)
        self.assertIn('<AdminLogin', ADMIN_FRONTEND)

        config = FakeHandler(path='/api/admin/config')
        config.do_GET()
        self.assertEqual(config.status, 403)
        self.assertEqual(
            json.loads(config.wfile.getvalue().decode('utf-8'))['error'],
            'admin_password_required',
        )

    def test_incremental_snapshot_and_admin_share_the_dashboard_port(self):
        publisher = dashboard.public_snapshot_publisher()
        latest_value = publisher.publish({'account': {'cash': 100}}, generated_at='now')

        health = FakeHandler(path='/healthz')
        health.do_GET()
        self.assertEqual(health.status, 200)
        self.assertEqual(json.loads(health.wfile.getvalue())['plane'], 'fastapi')

        latest = FakeHandler(path='/api/v2/public/latest')
        latest.do_GET()
        self.assertEqual(latest.status, 200)
        self.assertIn('s-maxage=5', latest.header('Cache-Control'))
        self.assertTrue(latest.header('ETag'))

        unchanged = FakeHandler(
            path='/api/v2/public/latest',
            headers={'If-None-Match': latest.header('ETag')},
        )
        unchanged.do_GET()
        self.assertEqual(unchanged.status, 304)
        self.assertEqual(unchanged.wfile.getvalue(), b'')

        manifest = FakeHandler(path=f"/api/v2/public/manifests/{latest_value['revision']}.json")
        manifest.do_GET()
        manifest_value = json.loads(manifest.wfile.getvalue())
        self.assertIn('immutable', manifest.header('Cache-Control'))
        digest = manifest_value['sections']['account']['digest']

        section = FakeHandler(path=f'/api/v2/public/objects/{digest}.json')
        section.do_GET()
        self.assertEqual(json.loads(section.wfile.getvalue()), {'cash': 100})
        self.assertEqual(section.header('ETag'), f'"{digest}"')

        admin = FakeHandler(path='/admin')
        admin.do_GET()
        self.assertEqual(admin.status, 200)

    def test_vue_pages_and_vite_assets_are_served_by_fastapi(self):
        expected_page = (ROOT / 'web' / 'dist' / 'index.html').read_bytes()
        home = FakeHandler(path='/')
        home.do_GET()
        admin = FakeHandler(path='/admin')
        admin.do_GET()
        removed_legacy_asset = FakeHandler(path='/static/dashboard.js')
        removed_legacy_asset.do_GET()

        self.assertEqual(home.wfile.getvalue(), expected_page)
        self.assertEqual(admin.wfile.getvalue(), expected_page)
        self.assertEqual(removed_legacy_asset.status, 404)
        index_body = expected_page.decode('utf-8')
        asset_path = next(
            line.split('src="', 1)[1].split('"', 1)[0]
            for line in index_body.splitlines()
            if 'src="/assets/' in line
        )
        asset = FakeHandler(path=asset_path)
        asset.do_GET()
        self.assertEqual(asset.status, 200)
        self.assertEqual(
            asset.wfile.getvalue(),
            (ROOT / 'web' / 'dist' / asset_path.removeprefix('/')).read_bytes(),
        )
        self.assertIn('max-age=31536000', asset.header('Cache-Control'))
        self.assertIn('immutable', asset.header('Cache-Control'))
        self.assertTrue(asset.header('ETag'))

        backend_source = MODULE_PATH.read_text(encoding='utf-8')
        self.assertNotIn('BaseHTTPRequestHandler', backend_source)
        self.assertNotIn('FRONTEND_ASSETS', backend_source)
        self.assertNotIn('<!doctype html>', backend_source)

    def test_vite_assets_support_gzip_and_conditional_get(self):
        index_body = (ROOT / 'web' / 'dist' / 'index.html').read_text(encoding='utf-8')
        asset_path = next(
            line.split('src="', 1)[1].split('"', 1)[0]
            for line in index_body.splitlines()
            if 'src="/assets/' in line
        )
        first = FakeHandler(path=asset_path, headers={'Accept-Encoding': 'gzip'})
        first.do_GET()
        self.assertEqual(first.status, 200)
        self.assertEqual(first.header('Content-Encoding'), 'gzip')
        self.assertIn('Accept-Encoding', first.header('Vary') or '')

        conditional = FakeHandler(
            path=asset_path,
            headers={'If-None-Match': first.header('ETag'), 'Accept-Encoding': 'gzip'},
        )
        conditional.do_GET()
        self.assertEqual(conditional.status, 304)
        self.assertEqual(conditional.wfile.getvalue(), b'')

    def test_dashboard_categories_have_independent_page_routes(self):
        expected_paths = {
            '/',
            '/practice',
            '/indices',
            '/industry-flow',
            '/dragon-tiger',
            '/market-monitor',
            '/x-monitor',
            '/us-ratings',
        }
        self.assertEqual(set(SPA_DASHBOARD_PATHS), expected_paths)
        expected_page = (ROOT / 'web' / 'dist' / 'index.html').read_bytes()
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
        router_source = (ROOT / 'web' / 'src' / 'router.js').read_text(encoding='utf-8')
        tab_source = (
            ROOT / 'web' / 'src' / 'composables' / 'useDashboardTabs.js'
        ).read_text(encoding='utf-8')
        for path in expected_paths - {'/'}:
            self.assertIn(f"'{path}'", router_source)
        self.assertIn('createWebHistory()', router_source)
        self.assertIn("practice: '/practice'", tab_source)

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

    def test_dashboard_starts_version_check_when_component_mounts(self):
        source = (
            ROOT / 'web' / 'src' / 'components' / 'VersionStatus.vue'
        ).read_text(encoding='utf-8')
        self.assertIn('id="versionStatus"', source)
        self.assertIn("fetch('/api/version'", source)
        self.assertIn('onMounted(loadVersionStatus)', source)
        self.assertIn("state.value = 'update'", source)
        self.assertIn('requestController?.abort()', source)

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
        self.assertEqual(write_only.status, 405)

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

            def run_decision_after_b1(self, payload):
                calls['decision_payload'] = payload
                return {'decision': {'summary': '持仓退出检查完成'}, 'executed': []}

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

        self.assertEqual(result['decision']['summary'], '持仓退出检查完成')
        self.assertEqual(calls['decision_payload']['items'], [])
        self.assertEqual(calls['refresh_payload']['market_snapshot']['sample_count'], 3000)
        entry, mark_done = calls['entries'][0]
        self.assertFalse(mark_done)
        self.assertEqual(entry['market_decision_context']['tone'], 'balanced')
        self.assertEqual(entry['decision']['market_guidance']['source_title'], 'B1定时选股实时盘面')
        self.assertIn('继续检查已有持仓的原策略退出规则', entry['decision']['summary'])

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

    def test_manual_practice_cycle_status_omits_large_internal_decision_result(self):
        original_state = dashboard.PRACTICE_MANUAL_CYCLE_STATE
        try:
            dashboard.PRACTICE_MANUAL_CYCLE_STATE = {
                'running': False,
                'stage': 'completed',
                'stage_label': '本轮选股及买卖已完成',
                'candidate_count': 10,
                'decision_result': {
                    'raw_context': 'x' * 500_000,
                    'actions': [{'reason': 'private'}],
                },
                'error': '',
            }

            status = dashboard.practice_manual_cycle_status()
        finally:
            dashboard.PRACTICE_MANUAL_CYCLE_STATE = original_state

        self.assertEqual(status['stage'], 'completed')
        self.assertEqual(status['candidate_count'], 10)
        self.assertNotIn('decision_result', status)
        self.assertLess(len(json.dumps(status, ensure_ascii=False)), 2_000)

    def test_manual_practice_cycle_scan_failure_never_enters_trade_decision(self):
        decision_calls = []
        original_scan = dashboard.trigger_b1_scan
        original_decision = dashboard.run_practice_decision_logged
        original_recent_candidates = dashboard.recent_practice_candidates_for_manual_cycle
        original_lock = dashboard.PRACTICE_MANUAL_CYCLE_LOCK
        original_state = dashboard.PRACTICE_MANUAL_CYCLE_STATE
        try:
            dashboard.trigger_b1_scan = lambda **_kwargs: {
                'error': 'Tencent quote batch=7/21 failed after 3/3 attempts: timeout',
                'items': [],
                'count': 0,
                'generated_at': '',
            }
            dashboard.run_practice_decision_logged = lambda *_args, **_kwargs: decision_calls.append(True)
            dashboard.recent_practice_candidates_for_manual_cycle = lambda: None
            dashboard.PRACTICE_MANUAL_CYCLE_LOCK = threading.Lock()
            dashboard.PRACTICE_MANUAL_CYCLE_LOCK.acquire()
            dashboard.PRACTICE_MANUAL_CYCLE_STATE = {'running': True, 'stage': 'starting'}

            dashboard._run_practice_manual_cycle()

            status = dashboard.practice_manual_cycle_status()
            self.assertEqual(status['stage'], 'error')
            self.assertIn('batch=7/21', status['error'])
            self.assertEqual(decision_calls, [])
            self.assertTrue(dashboard.PRACTICE_MANUAL_CYCLE_LOCK.acquire(blocking=False))
        finally:
            if dashboard.PRACTICE_MANUAL_CYCLE_LOCK.locked():
                dashboard.PRACTICE_MANUAL_CYCLE_LOCK.release()
            dashboard.trigger_b1_scan = original_scan
            dashboard.run_practice_decision_logged = original_decision
            dashboard.recent_practice_candidates_for_manual_cycle = original_recent_candidates
            dashboard.PRACTICE_MANUAL_CYCLE_LOCK = original_lock
            dashboard.PRACTICE_MANUAL_CYCLE_STATE = original_state

    def test_b1_scan_failure_summary_keeps_stage_and_final_error(self):
        stderr = "\n".join([
            "Step 1: Loading A-share code pool...",
            "Step 2: Fetching real-time batch quotes...",
            "Traceback (most recent call last):",
            '  File "/private/runtime.py", line 10, in https_open',
            "TencentQuoteBatchError: Tencent quote batch=7/21 failed after 3/3 attempts: timeout",
        ])

        summary = dashboard.summarize_b1_scan_failure(stderr, "")

        self.assertEqual(
            summary,
            "Step 2: Fetching real-time batch quotes...；"
            "TencentQuoteBatchError: Tencent quote batch=7/21 failed after 3/3 attempts: timeout",
        )
        self.assertNotIn("/private/runtime.py", summary)

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

    def test_full_practice_payload_is_a_side_effect_free_local_snapshot(self):
        calls = []

        class TraderStub:
            MODEL = 'local-model'
            PROVIDER_DISPLAY_NAME = 'local-provider'

            def load_state(self):
                calls.append('load_state')
                return {
                    'updated_at': '2026-07-23 10:00:00',
                    'initial_cash': 1_000_000,
                    'cash': 1_000_100,
                    'positions': {},
                    'equity_history': [
                        {'time': '2026-07-22 15:00:00', 'equity': 1_000_000},
                        {'time': '2026-07-23 10:00:00', 'equity': 1_000_100},
                    ],
                    'daily_equity_history': [],
                    'trade_log': [],
                    'decision_log': [],
                }

            def enrich_portfolio(self, state):
                calls.append('enrich_portfolio')
                return {
                    'initial_cash': state['initial_cash'],
                    'cash': state['cash'],
                    'positions': [],
                    'trade_log': [],
                    'decision_log': [],
                }

            def get_dashboard_payload(self):
                raise AssertionError('full reads must not refresh quotes or persist state')

            def track_strategy_performance(self, state):
                calls.append('track_strategy_performance')
                return {}

        original_get_trader = dashboard.get_trader_module
        original_current_cn_datetime = dashboard.current_cn_datetime
        original_heartbeat = dashboard.record_practice_equity_heartbeat
        try:
            dashboard.get_trader_module = lambda: TraderStub()
            dashboard.current_cn_datetime = lambda: datetime(2026, 7, 23, 10, 1, 0)
            dashboard.record_practice_equity_heartbeat = (
                lambda *_args, **_kwargs: calls.append('heartbeat') or True
            )

            payload = dashboard.get_practice_payload()
        finally:
            dashboard.get_trader_module = original_get_trader
            dashboard.current_cn_datetime = original_current_cn_datetime
            dashboard.record_practice_equity_heartbeat = original_heartbeat

        self.assertEqual(
            calls,
            ['load_state', 'enrich_portfolio', 'track_strategy_performance'],
        )
        self.assertEqual(payload['snapshot_mode'], 'full')
        self.assertEqual(payload['equity_history_scope'], 'retained_history')
        self.assertEqual(len(payload['equity_history']), 2)
        self.assertEqual(payload['decision_model'], 'local-model')

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
        self.assertIn('normalizePracticeOperationLogs', PRACTICE_LOG_UTILS)
        self.assertIn('class="practice-log-scroll"', PRACTICE_COMPONENTS)
        self.assertIn('overflow-y:auto', DASHBOARD_FRONTEND)
        self.assertIn('aria-label="当日所有操作日志"', PRACTICE_COMPONENTS)

    def test_index_template_can_open_full_practice_log_modal(self):
        self.assertIn("const selectedKey = ref('')", PRACTICE_COMPONENTS)
        self.assertIn('practiceLogRawText', PRACTICE_LOG_UTILS)
        self.assertIn('@click="selectedKey = item.key"', PRACTICE_COMPONENTS)
        self.assertIn('class="practice-log-detail-backdrop"', PRACTICE_COMPONENTS)
        self.assertIn('class="practice-log-detail-text"', PRACTICE_COMPONENTS)
        self.assertIn('<Teleport to="body">', PRACTICE_COMPONENTS)
        self.assertIn("event.key === 'Escape'", PRACTICE_COMPONENTS)
        self.assertNotIn('practice-log-detail-json', DASHBOARD_FRONTEND)
        self.assertNotIn('practice-log-detail-field', DASHBOARD_FRONTEND)

    def test_practice_rule_note_is_owned_by_its_vue_modal(self):
        rule_source = (
            ROOT / 'web' / 'src' / 'components' / 'practice' / 'PracticeRule.vue'
        ).read_text(encoding='utf-8')
        log_source = (
            ROOT / 'web' / 'src' / 'components' / 'practice' / 'PracticeOperationLog.vue'
        ).read_text(encoding='utf-8')
        self.assertIn('const open = ref(false)', rule_source)
        self.assertIn('@click="open = true"', rule_source)
        self.assertIn('class="practice-rule-backdrop"', rule_source)
        self.assertIn('props.practice.trade_rule_note', rule_source)
        self.assertNotIn('trade_rule_note', log_source)

    def test_vue_data_layers_use_revision_endpoints_instead_of_zero_limit_polling(self):
        self.assertEqual(dashboard.clamp_limit('0'), 0)
        data_sources = '\n'.join((MARKET_MONITOR_DATA, X_MONITOR_DATA, US_RATING_DATA))
        self.assertNotIn('/api/messages?limit=0', data_sources)
        self.assertIn('/api/messages/revision?category=${CATEGORY}', data_sources)
        self.assertNotIn('function isMessageCategory(', data_sources)

    def test_x_monitor_uses_vue_page_fingerprints_and_recent_page_cache(self):
        panel = (
            ROOT / 'web' / 'src' / 'components' / 'XMonitorPanel.vue'
        ).read_text(encoding='utf-8')
        self.assertIn('const CACHE_TTL_MS = 5 * 60 * 1000', X_MONITOR_DATA)
        self.assertIn('const CACHE_MAX_ENTRIES = 6', X_MONITOR_DATA)
        self.assertIn("const CACHE_KEY = 'niuniu-dashboard-x-pages-v2'", X_MONITOR_DATA)
        self.assertIn('const REFRESH_INTERVAL_MS = 15 * 1000', X_MONITOR_DATA)
        self.assertIn('function prefetchAdjacentPages(offset, total)', X_MONITOR_DATA)
        self.assertIn('/api/messages/revision?category=${CATEGORY}&limit=${X_MONITOR_PAGE_SIZE}&offset=', X_MONITOR_DATA)
        self.assertIn('xPageRevisionKey(revision) !== state.revision', X_MONITOR_DATA)
        self.assertIn("new URLSearchParams(location.search).get('page')", panel)
        self.assertIn('function cancelPendingMedia()', X_MONITOR_COMPONENTS)
        self.assertIn("if (!image.complete) image.removeAttribute('src')", X_MONITOR_COMPONENTS)
        self.assertIn('fetchpriority="low"', X_MONITOR_COMPONENTS)
        self.assertIn('<XImageViewer', X_MONITOR_COMPONENTS)
        self.assertIn('export function summarizeXRecord', X_MONITOR_UTILS)
        self.assertIn('export function parseXThread', X_MONITOR_UTILS)
        self.assertIn('export function xPageRevisionKey', X_MONITOR_UTILS)

    def test_x_monitor_display_parser_keeps_threads_media_and_page_revisions(self):
        scenario = r"""
import {parseXThread, summarizeXRecord, xMediaGroups, xPageRevisionKey} from SOURCE;
const content = `原帖｜@origin｜2026-07-20 08:00\n│ 原帖正文\n回复｜@reply｜2026-07-21 09:30\n│ 回复正文`;
const record = {
  content,
  metadata:{post:{
    reply_to_media:[
      {url:'https://pbs.twimg.com/media/example.jpg',type:'photo'},
      {url:'https://evil.example/media/example.jpg',type:'photo'},
    ],
    media:[{url:'https://pbs.twimg.com/tweet_video_thumb/video.png',type:'video'}],
  }},
};
const thread = parseXThread(content);
const summary = summarizeXRecord(record);
const groups = xMediaGroups(record);
const base = {category:'x_monitor',count:20,page:{limit:10,offset:0,count:10,fingerprint:'a'}};
const changed = {...base,page:{...base.page,fingerprint:'b'}};
console.log(JSON.stringify({
  hasOriginal:thread.originalPost.includes('原帖正文'),
  hasReply:thread.reply.includes('回复正文'),
  author:summary.author,
  label:summary.label,
  preview:summary.preview,
  groupLabels:groups.map(group => group.label),
  mediaUrls:groups.flatMap(group => group.items.map(item => item.url)),
  revisionChanged:xPageRevisionKey(base) !== xPageRevisionKey(changed),
}));
"""
        output = subprocess.check_output(
            [
                'node', '--input-type=module', '-e',
                scenario.replace('SOURCE', json.dumps(X_MONITOR_UTILS_PATH.as_uri())),
            ],
            cwd=ROOT,
            text=True,
        )
        result = json.loads(output)
        self.assertTrue(result['hasOriginal'])
        self.assertTrue(result['hasReply'])
        self.assertEqual(result['author'], '@reply')
        self.assertEqual(result['label'], '回复')
        self.assertIn('回复正文', result['preview'])
        self.assertEqual(result['groupLabels'], ['原帖图片', '推文图片'])
        self.assertEqual(len(result['mediaUrls']), 2)
        self.assertTrue(result['mediaUrls'][0].endswith('.jpg:large'))
        self.assertTrue(result['revisionChanged'])

    def test_practice_candidate_vue_display_preserves_strategy_tiers(self):
        scenario = r"""
import {
  practiceCandidateIndustryLabel,
  practiceCandidateStrategyMeta,
  practiceCandidateTier,
  practiceCandidateTierCounts,
} from SOURCE;
const rows = [
  {actionable:true, best_score:8, entry_threshold:8, hard_blockers:[]},
  {actionable:true, best_score:9, entry_threshold:8, hard_blockers:['停牌']},
  {actionable:false, best_score:6, entry_threshold:8, hard_blockers:[]},
];
const meta = practiceCandidateStrategyMeta({trend_pullback:{label:'自定义趋势', color:'#123456'}});
process.stdout.write(JSON.stringify({
  tiers:rows.map(practiceCandidateTier),
  counts:practiceCandidateTierCounts(rows),
  override:meta.trend_pullback,
  fallback:meta.breakout,
  boardLabel:practiceCandidateIndustryLabel({industry:'main_board'}),
}));
"""
        output = subprocess.check_output(
            [
                'node', '--input-type=module', '-e',
                scenario.replace('SOURCE', json.dumps(PRACTICE_CANDIDATE_UTILS_PATH.as_uri())),
            ],
            cwd=ROOT,
            text=True,
        )
        result = json.loads(output)
        self.assertEqual(result['tiers'], ['high', 'mid', 'low'])
        self.assertEqual(result['counts'], {'high': 1, 'mid': 1, 'low': 1})
        self.assertEqual(result['override'], {'label': '自定义趋势', 'color': '#123456'})
        self.assertEqual(result['fallback']['label'], '突破确认')
        self.assertEqual(result['boardLabel'], '主板')

    def test_market_monitor_uses_vue_cache_and_revision_polling(self):
        self.assertIn("const CACHE_KEY = 'niuniu-dashboard-market-page-v2'", MARKET_MONITOR_DATA)
        self.assertIn('const REFRESH_INTERVAL_MS = 15 * 1000', MARKET_MONITOR_DATA)
        self.assertIn('const SUMMARY_REFRESH_INTERVAL_MS = 5 * 60 * 1000', MARKET_MONITOR_DATA)
        self.assertIn("fetchJson(`/api/messages/revision?category=${CATEGORY}`", MARKET_MONITOR_DATA)
        self.assertIn('revisionKey(revision) !== state.revision', MARKET_MONITOR_DATA)
        self.assertIn('return loadHistory({ background: state.records.length > 0 })', MARKET_MONITOR_DATA)
        self.assertIn("fetchJson('/api/us_market_summary'", MARKET_MONITOR_DATA)
        self.assertIn("for (const category of ['market_monitor', 'x_monitor', 'us_ratings'])", MARKET_MONITOR_DATA)
        self.assertIn('publishMessageCategoryCounts()', MARKET_MONITOR_DATA)
        self.assertIn('aria-controls="us-market-summary-body"', MARKET_MONITOR_COMPONENTS)
        self.assertIn('class="market-chevron us-market-chevron"', MARKET_MONITOR_COMPONENTS)
        self.assertIn('class="market-card-preview us-market-preview"', MARKET_MONITOR_COMPONENTS)
        self.assertIn('class="market-detail-overview us-market-overview"', MARKET_MONITOR_COMPONENTS)
        self.assertIn('class="market-card-detail us-market-summary-body"', MARKET_MONITOR_COMPONENTS)
        self.assertIn('.us-market-summary-card.open .us-market-preview { display:none; }', DASHBOARD_FRONTEND)
        self.assertIn('.us-market-summary-card.collapsed::before { opacity:0; }', DASHBOARD_FRONTEND)
        self.assertIn('.us-market-summary-card.collapsed .us-market-tone', DASHBOARD_FRONTEND)
        self.assertNotIn('class="us-market-brief"', DASHBOARD_FRONTEND)
        self.assertNotIn('class="us-market-metrics"', DASHBOARD_FRONTEND)
        self.assertIn('.us-market-summary-card.open .market-chevron', DASHBOARD_FRONTEND)
        self.assertIn('.market-monitor-card:hover, .us-market-summary-card:hover', DASHBOARD_FRONTEND)
        self.assertIn('.market-monitor-card.open, .us-market-summary-card.open', DASHBOARD_FRONTEND)
        self.assertIn("usMarketSummaryMatchesDay(selectedDay.value, state.summary)", MARKET_MONITOR_COMPONENTS)
        self.assertIn('selectedRecords.value.filter(record => !isUsMarketSummaryRecord(record))', MARKET_MONITOR_COMPONENTS)
        self.assertNotIn('function renderMarketMonitor(', DASHBOARD_FRONTEND)
        self.assertNotIn('function loadMarketMonitorAuxData()', DASHBOARD_FRONTEND)

    def test_us_ratings_use_vue_revision_polling_and_lazy_enrichment(self):
        self.assertIn('const HISTORY_LIMIT = 120', US_RATING_DATA)
        self.assertIn('const REFRESH_INTERVAL_MS = 10 * 60 * 1000', US_RATING_DATA)
        self.assertIn("const CACHE_KEY = 'niuniu-dashboard-us-ratings-v1'", US_RATING_DATA)
        self.assertIn('/api/messages/revision?category=${CATEGORY}', US_RATING_DATA)
        self.assertIn('revisionKey(revision) !== state.revision', US_RATING_DATA)
        self.assertIn("kind === 'quotes' ? '/api/us_quotes' : '/api/us_profiles'", US_RATING_DATA)
        self.assertIn('loadQuotesForRecords(records)', US_RATING_DATA)
        self.assertIn('function loadProfile(ticker)', US_RATING_DATA)
        self.assertIn('watch(selectedRecords, records => loadQuotesForRecords(records)', US_RATING_COMPONENTS)
        self.assertIn('if (opening) props.loadProfile(row.ticker)', US_RATING_COMPONENTS)
        self.assertIn('class="rating-table"', US_RATING_COMPONENTS)
        self.assertIn('class="rating-detail-row"', US_RATING_COMPONENTS)
        self.assertIn('export function parseRatingReport', US_RATING_UTILS)
        self.assertIn('export function groupRatingRecordsByDay', US_RATING_UTILS)

    def test_market_monitor_only_uses_live_us_summary_for_its_target_day(self):
        scenario = r"""
import {isUsMarketSummaryRecord, usMarketSummaryMatchesDay} from SOURCE;
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
            [
                'node', '--input-type=module', '-e',
                scenario.replace('SOURCE', json.dumps(MARKET_MONITOR_UTILS_PATH.as_uri())),
            ],
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
        scenario = r"""
import {marketReportType} from SOURCE;
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
            [
                'node', '--input-type=module', '-e',
                scenario.replace('SOURCE', json.dumps(MARKET_MONITOR_UTILS_PATH.as_uri())),
            ],
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
        self.assertNotIn("if (activeCategory === 'market_monitor')", DASHBOARD_FRONTEND)
        self.assertIn('.us-market-summary-card.collapsed', DASHBOARD_FRONTEND)

    def test_dashboard_uses_asgi_server_without_legacy_http_listener(self):
        source = (ROOT / 'app' / 'dashboard' / 'server.py').read_text(encoding='utf-8')
        self.assertNotIn('ThreadingHTTPServer', source)
        self.assertNotIn('BaseHTTPRequestHandler', source)

    def test_equity_heartbeat_records_without_dashboard_requests_and_invalidates_snapshots(self):
        calls = []

        class FakeTrader:
            @staticmethod
            def maybe_record_session_equity_heartbeat():
                calls.append('recorded')
                return True

        dashboard.API_RESPONSE_CACHE['niuniu_practice'] = {'ts': 1.0, 'payload': b'{}'}
        dashboard.API_RESPONSE_CACHE[dashboard.PRACTICE_FAST_CACHE_KEY] = {'ts': 1.0, 'payload': b'{}'}

        self.assertTrue(dashboard.record_practice_equity_heartbeat(FakeTrader()))
        self.assertEqual(calls, ['recorded'])
        self.assertNotIn('niuniu_practice', dashboard.API_RESPONSE_CACHE)
        self.assertNotIn(dashboard.PRACTICE_FAST_CACHE_KEY, dashboard.API_RESPONSE_CACHE)

    def test_equity_heartbeat_loop_polls_independently_of_http_requests(self):
        calls = []
        waits = []
        original_recorder = dashboard.record_practice_equity_heartbeat

        class StopAfterFirstPoll:
            @staticmethod
            def is_set():
                return False

            @staticmethod
            def wait(seconds):
                waits.append(seconds)
                return True

        try:
            dashboard.record_practice_equity_heartbeat = lambda: calls.append('heartbeat') or True
            dashboard.practice_equity_heartbeat_loop(
                stop_event=StopAfterFirstPoll(),
                poll_seconds=5,
            )
        finally:
            dashboard.record_practice_equity_heartbeat = original_recorder

        self.assertEqual(calls, ['heartbeat'])
        self.assertEqual(waits, [5.0])

    def test_equity_heartbeat_starts_as_single_daemon_worker(self):
        created = []
        original_thread_class = dashboard.threading.Thread
        original_worker = dashboard.PRACTICE_EQUITY_HEARTBEAT_THREAD

        class FakeThread:
            def __init__(self, *, target, name, daemon):
                self.target = target
                self.name = name
                self.daemon = daemon
                self.started = False
                created.append(self)

            def is_alive(self):
                return self.started

            def start(self):
                self.started = True

        try:
            dashboard.threading.Thread = FakeThread
            dashboard.PRACTICE_EQUITY_HEARTBEAT_THREAD = None
            dashboard.start_practice_equity_heartbeat()
            dashboard.start_practice_equity_heartbeat()
        finally:
            dashboard.threading.Thread = original_thread_class
            dashboard.PRACTICE_EQUITY_HEARTBEAT_THREAD = original_worker

        self.assertEqual(len(created), 1)
        self.assertIs(created[0].target, dashboard.practice_equity_heartbeat_loop)
        self.assertEqual(created[0].name, 'practice-equity-heartbeat')
        self.assertTrue(created[0].daemon)
        self.assertTrue(created[0].started)

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

    def test_industry_flow_page_api_and_animation_are_wired(self):
        original_producer = dashboard.produce_industry_flow_data
        try:
            dashboard.produce_industry_flow_data = lambda: {
                'available': True,
                'generated_at': '2026-07-20 11:00:00',
                'nodes': [{'id': 'sector-a', 'name': '行业A', 'role': 'outflow'}],
                'links': [],
            }
            page = FakeHandler(path='/industry-flow')
            page.do_GET()
            api = FakeHandler(path='/api/industry-flow')
            api.do_GET()
            payload = json.loads(api.wfile.getvalue().decode('utf-8'))
        finally:
            dashboard.produce_industry_flow_data = original_producer

        self.assertEqual(page.status, 200)
        self.assertEqual(api.status, 200)
        self.assertEqual(payload['nodes'][0]['name'], '行业A')
        component = (
            ROOT / 'web' / 'src' / 'components' / 'IndustryFlowPanel.vue'
        ).read_text(encoding='utf-8')
        data_source = (
            ROOT / 'web' / 'src' / 'composables' / 'useIndustryFlowData.js'
        ).read_text(encoding='utf-8')
        animation_source = INDUSTRY_FLOW_ANIMATION_PATH.read_text(encoding='utf-8')
        self.assertIn("fetch('/api/industry-flow?compact=1'", data_source)
        self.assertIn('useIndustryFlowAnimation(payload)', component)
        self.assertIn('<TransitionGroup', component)
        self.assertIn('id="industryFlowSeek"', component)
        self.assertIn('@pointerdown="beginPointerSeek"', component)
        self.assertIn('@pointermove="movePointerSeek"', component)
        self.assertIn('@pointerup="finishPointerSeek"', component)
        self.assertIn('setPointerCapture?.(event.pointerId)', component)
        self.assertIn('export function frameAt', animation_source)
        self.assertIn('export function seekValueFromClientX', animation_source)
        self.assertIn('export function splitSortedNodes', animation_source)
        self.assertIn('const SPEED_OPTIONS = [0.5, 0.75, 1, 1.5, 2]', animation_source)

    def test_industry_flow_seek_track_is_thin_and_pointer_position_is_clamped(self):
        stylesheet = (ROOT / 'frontend' / 'dashboard.css').read_text(encoding='utf-8')
        self.assertIn('.industry-flow-progress-track .industry-flow-progress-seek {', stylesheet)
        self.assertIn('width:calc(100% + 16px);', stylesheet)
        self.assertIn('height:28px;', stylesheet)
        self.assertIn('top:50%;', stylesheet)
        self.assertIn('transform:translateY(-50%);', stylesheet)
        self.assertIn('background:transparent;', stylesheet)
        self.assertRegex(
            stylesheet,
            r'\.industry-flow-progress-track \{[^}]*height:2px;',
        )

        scenario = r"""
const {seekValueFromClientX} = await import(SOURCE);
console.log(JSON.stringify([
  seekValueFromClientX(10, 20, 100),
  seekValueFromClientX(20, 20, 100),
  seekValueFromClientX(70, 20, 100),
  seekValueFromClientX(120, 20, 100),
  seekValueFromClientX(180, 20, 100),
]));
"""
        output = subprocess.check_output(
            ['node', '--input-type=module', '-e', scenario.replace(
                'SOURCE', json.dumps(INDUSTRY_FLOW_ANIMATION_PATH.as_uri()),
            )],
            cwd=ROOT,
            text=True,
        )
        self.assertEqual(json.loads(output), [0, 0, 500, 1000, 1000])

    def test_industry_flow_sampling_window_and_history_file_are_bounded_to_local_data(self):
        original_calendar = dashboard.is_a_share_trading_day_for_dashboard
        original_history_file = dashboard.INDUSTRY_FLOW_HISTORY_FILE
        original_interval = dashboard.INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS
        try:
            self.assertEqual(dashboard.MONEY_FLOW_SNAPSHOT_FILE.name, 'industry_main_money_flow_cache.json')
            self.assertEqual(original_history_file.name, 'industry_main_flow_history.json')
            dashboard.is_a_share_trading_day_for_dashboard = lambda _now: True
            dashboard.INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS = 120
            self.assertFalse(dashboard.is_industry_flow_sampling_window(datetime(2026, 7, 20, 9, 24)))
            self.assertTrue(dashboard.is_industry_flow_sampling_window(datetime(2026, 7, 20, 9, 25)))
            self.assertTrue(dashboard.is_industry_flow_sampling_window(datetime(2026, 7, 20, 11, 31)))
            self.assertFalse(dashboard.is_industry_flow_sampling_window(datetime(2026, 7, 20, 11, 32)))
            self.assertFalse(dashboard.is_industry_flow_sampling_window(datetime(2026, 7, 20, 12, 0)))
            self.assertFalse(dashboard.is_industry_flow_sampling_window(datetime(2026, 7, 20, 12, 59)))
            self.assertTrue(dashboard.is_industry_flow_sampling_window(datetime(2026, 7, 20, 13, 0)))
            self.assertTrue(dashboard.is_industry_flow_sampling_window(datetime(2026, 7, 20, 15, 1)))
            self.assertFalse(dashboard.is_industry_flow_sampling_window(datetime(2026, 7, 20, 15, 2)))

            dashboard.INDUSTRY_FLOW_HISTORY_FILE = self.tmp_path / 'industry_flow_history.json'
            sample = {
                'generated_at': '2026-07-20 10:00:00',
                'items': [{'name': '半导体', 'net_flow_yi': 12}],
            }
            first = dashboard.record_industry_flow_sample(
                sample,
                now=datetime(2026, 7, 20, 10, 0),
            )
            second = dashboard.record_industry_flow_sample(
                sample,
                now=datetime(2026, 7, 20, 10, 0),
            )
            too_soon = dashboard.record_industry_flow_sample({
                'generated_at': '2026-07-20 10:01:00',
                'items': [{'name': '银行', 'net_flow_yi': -3}],
            }, now=datetime(2026, 7, 20, 10, 1))
            due = dashboard.record_industry_flow_sample({
                'generated_at': '2026-07-20 10:02:00',
                'items': [{'name': '银行', 'net_flow_yi': -4}],
            }, now=datetime(2026, 7, 20, 10, 2))
            lunch = dashboard.record_industry_flow_sample({
                'generated_at': '2026-07-20 12:00:00',
                'items': [{'name': '银行', 'net_flow_yi': -5}],
            }, now=datetime(2026, 7, 20, 12, 0))
            stored = json.loads(dashboard.INDUSTRY_FLOW_HISTORY_FILE.read_text(encoding='utf-8'))

            self.assertEqual(len(first), 1)
            self.assertEqual(second, first)
            self.assertEqual(too_soon, first)
            self.assertEqual(len(due), 2)
            self.assertEqual(lunch, due)
            self.assertEqual(len(stored['samples']), 2)
            self.assertEqual(stored['interval_seconds'], 120)
        finally:
            dashboard.is_a_share_trading_day_for_dashboard = original_calendar
            dashboard.INDUSTRY_FLOW_HISTORY_FILE = original_history_file
            dashboard.INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS = original_interval

    def test_money_flow_fetch_preserves_morning_history_after_afternoon_refresh(self):
        original_runner = dashboard.run_dashboard_helper
        original_clock = dashboard.current_cn_datetime
        original_calendar = dashboard.is_a_share_trading_day_for_dashboard
        snapshots = iter((
            {
                'generated_at': '2026-07-20 11:30:00',
                'inflow': [{'name': '半导体', 'net_flow_yi': 12}],
                'outflow': [{'name': '银行', 'net_flow_yi': -3}],
            },
            {
                'generated_at': '2026-07-20 13:00:00',
                'inflow': [{'name': '软件开发', 'net_flow_yi': 9}],
                'outflow': [{'name': '银行', 'net_flow_yi': -4}],
            },
        ))
        clocks = iter((
            datetime(2026, 7, 20, 11, 45),
            datetime(2026, 7, 20, 13, 0),
        ))
        calls = []
        try:
            def fake_runner(script_name, fallback, timeout=90, args=()):
                calls.append((script_name, fallback, timeout, args))
                return next(snapshots)

            dashboard.run_dashboard_helper = fake_runner
            dashboard.current_cn_datetime = lambda: next(clocks)
            dashboard.is_a_share_trading_day_for_dashboard = lambda _now: True

            morning = dashboard.produce_money_flow_data()
            afternoon = dashboard.produce_money_flow_data()
        finally:
            dashboard.run_dashboard_helper = original_runner
            dashboard.current_cn_datetime = original_clock
            dashboard.is_a_share_trading_day_for_dashboard = original_calendar

        stored = json.loads(
            dashboard.INDUSTRY_FLOW_HISTORY_FILE.read_text(encoding='utf-8')
        )
        self.assertEqual(morning['generated_at'], '2026-07-20 11:30:00')
        self.assertEqual(afternoon['generated_at'], '2026-07-20 13:00:00')
        self.assertEqual(
            [sample['generated_at'] for sample in stored['samples']],
            ['2026-07-20 11:30:00', '2026-07-20 13:00:00'],
        )
        self.assertEqual(
            calls,
            [
                ('money_flow_dashboard_api.py', {'inflow': [], 'outflow': []}, 120, ()),
                ('money_flow_dashboard_api.py', {'inflow': [], 'outflow': []}, 120, ()),
            ],
        )

    def test_industry_flow_sampler_waits_on_a_fixed_minute_cadence(self):
        class StopAfterFirstWait:
            def __init__(self):
                self.wait_seconds = None

            def is_set(self):
                return False

            def wait(self, seconds):
                self.wait_seconds = seconds
                return True

        stop_event = StopAfterFirstWait()
        original_window = dashboard.is_industry_flow_sampling_window
        original_refresh = dashboard.refresh_industry_flow_sample
        original_monotonic = dashboard.time.monotonic
        try:
            dashboard.is_industry_flow_sampling_window = lambda: True
            dashboard.refresh_industry_flow_sample = lambda: True
            ticks = iter((100.0, 112.5))
            dashboard.time.monotonic = lambda: next(ticks)

            dashboard.industry_flow_sampling_loop(stop_event=stop_event, poll_seconds=60)

            self.assertAlmostEqual(stop_event.wait_seconds, 47.5)
        finally:
            dashboard.is_industry_flow_sampling_window = original_window
            dashboard.refresh_industry_flow_sample = original_refresh
            dashboard.time.monotonic = original_monotonic

    def test_industry_flow_refresh_invalidates_full_and_compact_caches(self):
        original_fetch = dashboard.fetch_and_record_money_flow
        original_invalidate = dashboard.invalidate_api_cache
        original_invalidate_prefix = dashboard.invalidate_api_cache_prefix
        invalidated = []
        prefixes = []
        sample = {'generated_at': '2026-07-20 10:00:00'}
        try:
            dashboard.fetch_and_record_money_flow = lambda **_kwargs: (sample, [sample])
            dashboard.invalidate_api_cache = lambda *keys: invalidated.append(keys)
            dashboard.invalidate_api_cache_prefix = lambda prefix: prefixes.append(prefix)

            self.assertTrue(dashboard.refresh_industry_flow_sample())
        finally:
            dashboard.fetch_and_record_money_flow = original_fetch
            dashboard.invalidate_api_cache = original_invalidate
            dashboard.invalidate_api_cache_prefix = original_invalidate_prefix

        self.assertEqual(invalidated, [('money_flow',)])
        self.assertEqual(prefixes, ['industry_flow'])

    def test_api_cache_can_skip_empty_industry_flow_payloads(self):
        cache_key = 'industry_flow:compact:skip-empty-test'
        dashboard.invalidate_api_cache(cache_key)
        empty, empty_hit = dashboard.cache_get_json(
            cache_key,
            30,
            lambda: {'available': False, 'nodes': []},
            cacheable=lambda payload: bool(payload.get('nodes')),
        )
        self.assertFalse(empty_hit)
        self.assertEqual(json.loads(empty)['nodes'], [])
        self.assertNotIn(cache_key, dashboard.API_RESPONSE_CACHE)

        populated, populated_hit = dashboard.cache_get_json(
            cache_key,
            30,
            lambda: {'available': True, 'nodes': [{'id': 'semi'}]},
            cacheable=lambda payload: bool(payload.get('nodes')),
        )
        self.assertFalse(populated_hit)
        self.assertEqual(json.loads(populated)['nodes'][0]['id'], 'semi')
        self.assertIn(cache_key, dashboard.API_RESPONSE_CACHE)
        dashboard.invalidate_api_cache(cache_key)

    def test_cold_money_flow_cache_skips_empty_durable_snapshot(self):
        cache_key = 'money_flow:skip-empty-seed-test'
        cache_path = self.tmp_path / 'money-flow-seed.json'
        usable = lambda payload: bool(payload.get('inflow') or payload.get('outflow'))
        dashboard.invalidate_api_cache(cache_key)

        cache_path.write_text(
            json.dumps({'retention_date': '2026-07-23', 'inflow': [], 'outflow': []}),
            encoding='utf-8',
        )
        self.assertFalse(dashboard.seed_api_cache_from_json_file(
            cache_key,
            cache_path,
            60,
            cacheable=usable,
        ))
        self.assertNotIn(cache_key, dashboard.API_RESPONSE_CACHE)

        cache_path.write_text(json.dumps({
            'generated_at': '2026-07-23 15:00:00',
            'inflow': [{'name': '半导体', 'net_flow_yi': 12}],
            'outflow': [{'name': '银行', 'net_flow_yi': -6}],
        }), encoding='utf-8')
        self.assertTrue(dashboard.seed_api_cache_from_json_file(
            cache_key,
            cache_path,
            60,
            cacheable=usable,
        ))
        stored = json.loads(dashboard.API_RESPONSE_CACHE[cache_key]['payload'])
        self.assertEqual(len(stored['inflow']), 1)
        self.assertTrue(stored['stale_cache'])
        dashboard.invalidate_api_cache(cache_key)

    def test_industry_flow_empty_response_preserves_data_and_retries_quickly(self):
        scenario = r"""
const {
  INDUSTRY_FLOW_EMPTY_RETRY_DELAYS_MS,
  hasIndustryMoneyFlowRows,
  mergeIndustryFlowPayload,
} = await import(SOURCE);
const current = {
  loaded:true,
  nodes:[{id:'semi', name:'半导体', net_flow_yi:12}],
  generated_at:'2026-07-20 10:00:00',
};
const merged = mergeIndustryFlowPayload(current, {
  available:false,
  nodes:[],
  money_flow:{inflow:[], outflow:[]},
});
console.log(JSON.stringify({
  delays:INDUSTRY_FLOW_EMPTY_RETRY_DELAYS_MS,
  nodes:merged.payload.nodes,
  preserved:merged.preservedData,
  stale:merged.payload.stale_cache,
  hasMoneyFlow:hasIndustryMoneyFlowRows({money_flow:{inflow:[], outflow:[]}}),
}));
"""
        output = subprocess.check_output(
            ['node', '--input-type=module', '-e', scenario.replace(
                'SOURCE', json.dumps(INDUSTRY_FLOW_DATA_UTIL_PATH.as_uri()),
            )],
            cwd=ROOT,
            text=True,
        )
        result = json.loads(output)
        self.assertEqual(result['delays'], [1000, 2500, 5000])
        self.assertEqual(result['nodes'][0]['name'], '半导体')
        self.assertTrue(result['preserved'])
        self.assertTrue(result['stale'])
        self.assertFalse(result['hasMoneyFlow'])

    def test_industry_flow_stage_height_tracks_desktop_and_mobile_viewports(self):
        scenario = r"""
const {responsiveStageHeight} = await import(SOURCE);
const height = options => responsiveStageHeight(options);
console.log(JSON.stringify({
  compactDesktop:height({
    viewportBottom:720,
    stageTop:332,
    footerHeight:50,
    bottomPadding:48,
  }),
  tallDesktop:height({
    viewportBottom:1080,
    stageTop:332,
    footerHeight:50,
    bottomPadding:48,
  }),
  mobile:height({
    viewportBottom:844,
    stageTop:430,
    footerHeight:50,
    bottomPadding:28,
    mobile:true,
  }),
  mobileMinimum:height({
    viewportBottom:520,
    stageTop:400,
    footerHeight:50,
    bottomPadding:28,
    mobile:true,
  }),
  desktopMinimum:height({
    viewportBottom:520,
    stageTop:400,
    footerHeight:50,
    bottomPadding:48,
  }),
  desktopMaximum:height({
    viewportBottom:1800,
    stageTop:200,
    footerHeight:50,
    bottomPadding:48,
  }),
}));
"""
        output = subprocess.check_output(
            ['node', '--input-type=module', '-e', scenario.replace(
                'SOURCE', json.dumps(RESPONSIVE_STAGE_UTIL_PATH.as_uri()),
            )],
            cwd=ROOT,
            text=True,
        )
        result = json.loads(output)
        self.assertEqual(result['compactDesktop'], 288)
        self.assertEqual(result['tallDesktop'], 648)
        self.assertEqual(result['mobile'], 334)
        self.assertEqual(result['mobileMinimum'], 236)
        self.assertEqual(result['desktopMinimum'], 220)
        self.assertEqual(result['desktopMaximum'], 840)

        component = (
            ROOT / 'web' / 'src' / 'components' / 'IndustryFlowPanel.vue'
        ).read_text(encoding='utf-8')
        styles = (ROOT / 'frontend' / 'dashboard.css').read_text(encoding='utf-8')
        self.assertIn('window.visualViewport', component)
        self.assertIn('new ResizeObserver(scheduleStageHeight)', component)
        self.assertIn('--industry-flow-stage-height', component)
        self.assertIn('calc(100dvh - 430px)', styles)
        self.assertIn('calc(100dvh - 460px)', styles)

    def test_indices_apply_market_breadth_before_unrelated_requests_finish(self):
        scenario = r"""
const {applyPayloadAsReady} = await import(SOURCE);
const events = [];
let releaseSlow;
const slow = new Promise(resolve => {
  releaseSlow = () => resolve({name:'us-sectors'});
});
const breadthTask = applyPayloadAsReady(
  Promise.resolve({name:'market-breadth'}),
  payload => events.push(payload.name),
);
const slowTask = applyPayloadAsReady(
  slow,
  payload => events.push(payload.name),
);
await breadthTask;
const beforeSlowFinished = [...events];
releaseSlow();
await slowTask;
console.log(JSON.stringify({beforeSlowFinished, afterAll:events}));
"""
        output = subprocess.check_output(
            ['node', '--input-type=module', '-e', scenario.replace(
                'SOURCE', json.dumps(ASYNC_PAYLOAD_UTIL_PATH.as_uri()),
            )],
            cwd=ROOT,
            text=True,
        )
        result = json.loads(output)
        self.assertEqual(result['beforeSlowFinished'], ['market-breadth'])
        self.assertEqual(
            result['afterAll'],
            ['market-breadth', 'us-sectors'],
        )
        indices_data = (
            ROOT / 'web' / 'src' / 'composables' / 'useIndicesData.js'
        ).read_text(encoding='utf-8')
        self.assertIn("fetchJson('/api/market_breadth'", indices_data)
        self.assertIn('applyPayloadAsReady(', indices_data)
        self.assertIn('state.marketBreadth = marketBreadth.error', indices_data)

    def test_industry_flow_timeline_interpolates_node_amounts(self):
        scenario = r"""
globalThis.window = {matchMedia:() => ({matches:false})};
const {frameAt} = await import(SOURCE);
const payload = {
  nodes:[
    {id:'a', name:'行业A', role:'inflow', net_flow_yi:20},
    {id:'b', name:'行业B', role:'outflow', net_flow_yi:-4},
  ],
  timeline:[
    {generated_at:'2026-07-20 10:00:00', nodes:[
      {id:'a', net_flow_yi:10, inflow_yi:20, outflow_yi:10},
      {id:'b', net_flow_yi:-8, inflow_yi:4, outflow_yi:12},
    ]},
    {generated_at:'2026-07-20 10:01:00', nodes:[
      {id:'a', net_flow_yi:20, inflow_yi:35, outflow_yi:15},
      {id:'b', net_flow_yi:-4, inflow_yi:10, outflow_yi:14},
    ]},
  ],
};
console.log(JSON.stringify(frameAt(payload, 0.5)));
"""
        output = subprocess.check_output(
            ['node', '--input-type=module', '-e', scenario.replace(
                'SOURCE', json.dumps(INDUSTRY_FLOW_ANIMATION_PATH.as_uri()),
            )],
            cwd=ROOT,
            text=True,
        )
        frame = json.loads(output)
        nodes = {node['id']: node for node in frame['nodes']}
        self.assertEqual(frame['generated_at'], '2026-07-20 10:00:30')
        self.assertEqual(nodes['a']['net_flow_yi'], 15)
        self.assertEqual(nodes['b']['net_flow_yi'], -6)

    def test_industry_flow_timeline_keeps_leaders_from_both_neighboring_minutes(self):
        scenario = r"""
globalThis.window = {matchMedia:() => ({matches:false})};
const {frameAt} = await import(SOURCE);
const payload = {
  nodes:[
    {id:'new-in', name:'新流入', net_flow_yi:20},
    {id:'new-out', name:'新流出', net_flow_yi:-20},
  ],
  timeline:[
    {generated_at:'2026-07-20 10:00:00', nodes:[
      {id:'old-in', name:'旧流入', net_flow_yi:10},
      {id:'old-out', name:'旧流出', net_flow_yi:-8},
    ]},
    {generated_at:'2026-07-20 10:01:00', nodes:[
      {id:'new-in', name:'新流入', net_flow_yi:20},
      {id:'new-out', name:'新流出', net_flow_yi:-4},
    ]},
  ],
};
console.log(JSON.stringify(frameAt(payload, 0.5).nodes.map(node => node.id).sort()));
"""
        output = subprocess.check_output(
            ['node', '--input-type=module', '-e', scenario.replace(
                'SOURCE', json.dumps(INDUSTRY_FLOW_ANIMATION_PATH.as_uri()),
            )],
            cwd=ROOT,
            text=True,
        )
        self.assertEqual(json.loads(output), ['new-in', 'new-out', 'old-in', 'old-out'])

    def test_industry_flow_timeline_respects_configured_industry_count(self):
        scenario = r"""
globalThis.window = {matchMedia:() => ({matches:false})};
const {frameAt} = await import(SOURCE);
const payload = {
  settings:{side_limit:1},
  nodes:[],
  timeline:[
    {generated_at:'2026-07-20 10:00:00', nodes:[
      {id:'in-a', name:'流入A', net_flow_yi:10},
      {id:'in-b', name:'流入B', net_flow_yi:8},
      {id:'out-a', name:'流出A', net_flow_yi:-10},
      {id:'out-b', name:'流出B', net_flow_yi:-8},
    ]},
    {generated_at:'2026-07-20 10:01:00', nodes:[
      {id:'in-a', name:'流入A', net_flow_yi:12},
      {id:'in-b', name:'流入B', net_flow_yi:9},
      {id:'out-a', name:'流出A', net_flow_yi:-12},
      {id:'out-b', name:'流出B', net_flow_yi:-9},
    ]},
  ],
};
console.log(JSON.stringify(frameAt(payload, 0.5).nodes.map(node => node.id)));
"""
        output = subprocess.check_output(
            ['node', '--input-type=module', '-e', scenario.replace(
                'SOURCE', json.dumps(INDUSTRY_FLOW_ANIMATION_PATH.as_uri()),
            )],
            cwd=ROOT,
            text=True,
        )
        self.assertEqual(set(json.loads(output)), {'in-a', 'out-a'})

    def test_industry_flow_initial_load_shows_latest_frame_without_autoplay(self):
        scenario = r"""
globalThis.window = {matchMedia:() => ({matches:false})};
const {configureIndustryFlowAnimation, useIndustryFlowAnimation} = await import(SOURCE);
const payload = {
  nodes:[{id:'sector-a', name:'行业A', net_flow_yi:18}],
  timeline:[
    {generated_at:'2026-07-20 10:00:00', nodes:[
      {id:'sector-a', name:'行业A', net_flow_yi:10},
    ]},
    {generated_at:'2026-07-20 10:01:00', nodes:[
      {id:'sector-a', name:'行业A', net_flow_yi:18},
    ]},
  ],
};
configureIndustryFlowAnimation(payload, false);
const flow = useIndustryFlowAnimation({value:payload});
console.log(JSON.stringify({
  progress:flow.animation.progress,
  playing:flow.animation.playing,
  currentTime:flow.currentTime.value,
  netFlow:flow.sides.value.inflow[0].net_flow_yi,
}));
"""
        output = subprocess.check_output(
            ['node', '--input-type=module', '-e', scenario.replace(
                'SOURCE', json.dumps(INDUSTRY_FLOW_ANIMATION_PATH.as_uri()),
            )],
            cwd=ROOT,
            text=True,
        )
        state = json.loads(output)
        self.assertEqual(state, {
            'progress': 1,
            'playing': False,
            'currentTime': '10:01:00',
            'netFlow': 18,
        })

    def test_industry_flow_rank_changes_use_vue_transition_group(self):
        component = (
            ROOT / 'web' / 'src' / 'components' / 'IndustryFlowPanel.vue'
        ).read_text(encoding='utf-8')
        self.assertEqual(component.count('<TransitionGroup'), 2)
        self.assertIn('name="industry-flow-rank"', component)
        self.assertEqual(component.count('@before-leave="pinLeavingRow"'), 2)
        self.assertIn('.industry-flow-rank-move,', component)
        self.assertIn('transition: transform 420ms cubic-bezier(.22,.8,.24,1)', component)
        self.assertIn('.flow-bars-col-list { position: relative; }', component)
        self.assertIn('const rowRect = element.getBoundingClientRect()', component)
        self.assertIn("element.style.position = 'absolute'", component)
        self.assertIn("element.style.transform = 'none'", component)
        self.assertIn('transition: opacity 180ms ease-out;', component)
        self.assertIn("@media (prefers-reduced-motion: reduce)", component)

    def test_industry_flow_playback_duration_keeps_sample_transitions_readable(self):
        scenario = r"""
globalThis.window = {matchMedia:() => ({matches:false})};
const {playbackDuration} = await import(SOURCE);
console.log(JSON.stringify([
  playbackDuration(2),
  playbackDuration(96),
  playbackDuration(242),
]));
"""
        output = subprocess.check_output(
            ['node', '--input-type=module', '-e', scenario.replace(
                'SOURCE', json.dumps(INDUSTRY_FLOW_ANIMATION_PATH.as_uri()),
            )],
            cwd=ROOT,
            text=True,
        )
        self.assertEqual(json.loads(output), [9000, 43700, 110000])

    def test_indices_market_panel_switches_to_us_sectors_with_index_session(self):
        panel = (
            ROOT / 'web' / 'src' / 'components' / 'IndicesPanel.vue'
        ).read_text(encoding='utf-8')
        data_source = (
            ROOT / 'web' / 'src' / 'composables' / 'useIndicesData.js'
        ).read_text(encoding='utf-8')
        overview = (
            ROOT / 'web' / 'src' / 'components' / 'indices' / 'MarketOverview.vue'
        ).read_text(encoding='utf-8')
        display = (
            ROOT / 'web' / 'src' / 'utils' / 'marketDisplay.js'
        ).read_text(encoding='utf-8')
        self.assertIn("fetchJson('/api/us_sectors'", data_source)
        self.assertIn('indicesSwitchSession(aIndexItems.value)', panel)
        self.assertIn("marketRegion.value === 'us'", panel)
        self.assertIn('aria-label="行情市场切换"', panel)
        self.assertIn("@click=\"setMarketRegion('a_share')\"", panel)
        self.assertIn("@click=\"setMarketRegion('us')\"", panel)
        self.assertIn("v-if=\"region === 'us'\"", overview)
        self.assertIn('export function indicesSwitchSession', display)
        self.assertIn('暂无上涨板块', overview)
        self.assertIn('暂无下跌板块', overview)

    def test_previous_day_market_label_uses_beijing_calendar_date(self):
        source = (
            ROOT / 'web' / 'src' / 'utils' / 'marketDisplay.js'
        ).as_uri()
        scenario = """
import { previousDayMarketLabel } from SOURCE;
const afterMidnight = new Date('2026-07-23T16:30:00Z');
console.log(JSON.stringify([
  previousDayMarketLabel('2026-07-23 15:00:00', afterMidnight),
  previousDayMarketLabel('2026-07-24 09:30:00', afterMidnight),
  previousDayMarketLabel('2026-07-22 15:00:00', afterMidnight),
]));
"""
        output = subprocess.check_output(
            ['node', '--input-type=module', '-e', scenario.replace('SOURCE', json.dumps(source))],
            cwd=ROOT,
            text=True,
        )
        self.assertEqual(json.loads(output), ['前一日数据（07-23）', '', ''])

    def test_indices_panel_can_put_a_share_or_us_indices_first(self):
        panel = (
            ROOT / 'web' / 'src' / 'components' / 'IndicesPanel.vue'
        ).read_text(encoding='utf-8')
        overview = (
            ROOT / 'web' / 'src' / 'components' / 'indices' / 'IndexOverview.vue'
        ).read_text(encoding='utf-8')
        self.assertIn("const INDEX_PRIORITY_STATE_KEY = 'niuniu-dashboard-index-priority-v1'", panel)
        self.assertIn('function setIndexPriority(value)', panel)
        self.assertIn('window.sessionStorage.setItem(INDEX_PRIORITY_STATE_KEY, value)', panel)
        self.assertIn('aria-label="指数排序切换"', panel)
        self.assertIn('A股在上', panel)
        self.assertIn('美股在上', panel)
        self.assertIn("props.priority === 'a_share'", overview)
        self.assertIn("[['A股指数', aShare], ['美股指数', us]]", overview)
        self.assertIn("[['美股指数', us], ['A股指数', aShare]]", overview)

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

    def test_practice_vue_components_preserve_account_chart_and_calendar_details(self):
        self.assertNotIn('renderPracticePage', DASHBOARD_FRONTEND)
        self.assertNotIn('loadPracticePage', DASHBOARD_FRONTEND)
        self.assertIn("main_board: '主板'", PRACTICE_CANDIDATE_UTILS)
        self.assertIn('item.industry || item.sector || item.board_label || item.board', PRACTICE_CANDIDATE_UTILS)
        self.assertIn('{{ industryLabel }}', PRACTICE_CANDIDATE_COMPONENTS)
        self.assertNotIn('所属板块', PRACTICE_CANDIDATE_COMPONENTS)

        for label in ('买入理由', '卖出归因', '最低/最高', '仓位占比', '可卖/持有'):
            self.assertIn(label, PRACTICE_COMPONENTS)
        self.assertIn('<PracticePositionCard', PRACTICE_COMPONENTS)
        self.assertIn('<PracticeSoldCard', PRACTICE_COMPONENTS)
        self.assertIn("next.searchParams.set('holdings', 'sold')", PRACTICE_COMPONENTS)
        self.assertIn("next.searchParams.set('brief', '1')", PRACTICE_COMPONENTS)

        self.assertIn('buildPracticeChartModel', PRACTICE_CHART_UTILS)
        self.assertIn("timeZone: 'Asia/Shanghai'", PRACTICE_CHART_UTILS)
        self.assertIn('tradingClockMinuteOfDay', PRACTICE_CHART_UTILS)
        self.assertIn('normalizePracticeTradeMarkers', PRACTICE_CHART_UTILS)
        self.assertIn('class="practice-chart-title-measure"', PRACTICE_COMPONENTS)
        self.assertIn('class="practice-chart-hover-layer"', PRACTICE_COMPONENTS)
        self.assertIn('class="practice-trade-marker-tooltip"', PRACTICE_COMPONENTS)
        self.assertIn("trade.action === 'SELL' && trade.isFullExit", PRACTICE_COMPONENTS)
        self.assertIn('touch-action:none', DASHBOARD_FRONTEND)
        self.assertIn('.practice-trade-marker.sell-partial', DASHBOARD_FRONTEND)
        self.assertIn('.practice-trade-marker.sell-full', DASHBOARD_FRONTEND)

        self.assertIn('buildPracticeCalendarRows', PRACTICE_CHART_UTILS)
        self.assertIn('practiceCalendarHistoryCoversDate', PRACTICE_CHART_UTILS)
        self.assertIn('class="practice-calendar-popover"', PRACTICE_COMPONENTS)
        self.assertIn('class="practice-calendar-day-curve"', PRACTICE_COMPONENTS)
        self.assertIn('@ensure-full="ensureFullSnapshot"', PRACTICE_COMPONENTS)
        self.assertIn('class="practice-calendar-today weekend-today"', PRACTICE_COMPONENTS)
        self.assertIn('grid-template-columns:repeat(5, minmax(0, 1.14fr)) repeat(2, minmax(30px, .72fr))', DASHBOARD_FRONTEND)
        self.assertIn('background:linear-gradient(180deg, #172033, #101827)', DASHBOARD_FRONTEND)
        self.assertNotIn('practice-calendar-backdrop', PRACTICE_COMPONENTS)

    def test_index_template_loads_calendar_history_without_waiting_for_full_snapshot(self):
        self.assertIn("'/api/niuniu_practice?fast=1&calendar_schema=1'", PRACTICE_DATA)
        self.assertIn("fetchJson('/api/niuniu_practice?snapshot_schema=2'", PRACTICE_DATA)
        self.assertEqual(PRACTICE_DATA.count('/api/niuniu_practice?snapshot_schema=2'), 1)
        self.assertIn('async function ensureFullSnapshot()', PRACTICE_DATA)
        self.assertIn('FULL_HISTORY_RETRY_MS = 5 * 60 * 1000', PRACTICE_DATA)
        self.assertIn("state.fullSnapshotStatus = 'loading'", PRACTICE_DATA)
        self.assertIn("state.fullSnapshotStatus = 'loaded'", PRACTICE_DATA)
        self.assertIn("state.fullSnapshotStatus = 'error'", PRACTICE_DATA)
        self.assertIn('subscribePublicProjection(handleProjection', PRACTICE_DATA)
        self.assertIn("fetchJson('/api/v2/public/latest'", PUBLIC_PROJECTION_DATA)
        self.assertIn('mergePracticePayloadSnapshots', PRACTICE_PAYLOAD_UTILS)
        self.assertIn('mergePracticeEquityRows', PRACTICE_PAYLOAD_UTILS)
        self.assertIn('comparePracticePayloadFreshness', PRACTICE_PAYLOAD_UTILS)
        self.assertIn("String(payload.equity_history_scope || '') === 'unavailable'", PRACTICE_PAYLOAD_UTILS)
        self.assertIn('compactPracticeCalendarHistoryPoints', PRACTICE_CHART_UTILS)
        self.assertIn('calendar.complete !== true', PRACTICE_CHART_UTILS)
        self.assertIn('@ensure-full="ensureFullSnapshot"', PRACTICE_COMPONENTS)
        self.assertNotIn('loadPracticePage', DASHBOARD_FRONTEND)

    def test_index_template_separates_single_stock_retries_from_quote_channels(self):
        self.assertIn(
            '`行情：${quote.quote_time} 更新${quote.updated ?? 0}只 腾讯${channels.tencent ?? 0}/东财${channels.eastmoney ?? 0}/Sina${channels.sina ?? 0}${singleRetryCount',
            PRACTICE_COMPONENTS,
        )
        self.assertIn('const singleRetryCount = Math.max', PRACTICE_COMPONENTS)
        self.assertIn('`，单股重试${singleRetryCount}只`', PRACTICE_COMPONENTS)
        self.assertNotIn('/单票${channels.single', PRACTICE_COMPONENTS)

    def test_vue_router_and_components_own_independent_category_routes(self):
        router_source = (ROOT / 'web' / 'src' / 'router.js').read_text(encoding='utf-8')
        tabs_source = (
            ROOT / 'web' / 'src' / 'composables' / 'useDashboardTabs.js'
        ).read_text(encoding='utf-8')
        dragon_source = (
            ROOT / 'web' / 'src' / 'components' / 'DragonTigerPanel.vue'
        ).read_text(encoding='utf-8')
        dashboard_page = (
            ROOT / 'web' / 'src' / 'components' / 'DashboardPage.vue'
        ).read_text(encoding='utf-8')

        for route in ('/practice', '/indices', '/industry-flow', '/dragon-tiger', '/market-monitor', '/x-monitor', '/us-ratings'):
            self.assertIn(f"'{route}'", router_source)
        self.assertIn("const CATEGORY_ORDER = ['practice', 'indices', 'market_monitor', 'dragon_tiger', 'x_monitor', 'us_ratings']", tabs_source)
        self.assertIn("industry_flow: '/industry-flow'", tabs_source)
        self.assertIn("const LEGACY_CATEGORY_ALIASES = { b1_screen: 'practice' }", tabs_source)
        self.assertIn("fetch(`/api/iwencai/dragon-tiger${query}`", dragon_source)
        self.assertIn("const SORT_FIELDS = new Set(['name', 'sector', 'change_pct', 'net_amount_yuan'])", dragon_source)
        self.assertIn("record?.seat_category === 'institution'", dragon_source)
        self.assertIn('<PracticePanel />', dashboard_page)
        self.assertIn('<DragonTigerPanel />', dashboard_page)
        self.assertIn('subscribePublicProjection(handleProjection)', PRACTICE_CANDIDATE_DATA)
        self.assertIn("fetchJson('/api/v2/public/latest'", PUBLIC_PROJECTION_DATA)
        self.assertNotIn('/static/dashboard.js', dashboard_page)

    def test_index_snapshot_merge_handles_business_errors_and_stale_full_responses(self):
        functions = (
            "import { isUsablePracticePayload, mergePracticePayloadSnapshots } "
            f"from {json.dumps(PRACTICE_PAYLOAD_UTILS_PATH.as_uri())};\n"
        )
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
            ['node', '--input-type=module', '-e', functions + scenario],
            check=True,
            capture_output=True,
            text=True,
        )
        checks = json.loads(result.stdout)

        self.assertTrue(all(checks.values()), checks)

    def test_index_template_does_not_guess_missing_decision_model(self):
        self.assertIn("const model = String(props.practice.decision_model || '').trim()", PRACTICE_COMPONENTS)
        self.assertIn("props.fullSnapshotStatus === 'error' ? '未知' : '加载中'", PRACTICE_COMPONENTS)
        self.assertIn('delete state.practice.decision_model', PRACTICE_DATA)
        self.assertIn('delete state.practice.decision_provider', PRACTICE_DATA)
        self.assertIn("cache: 'no-cache'", PRACTICE_DATA)
        self.assertNotIn("decision_model || 'deepseek-v4-pro'", PRACTICE_COMPONENTS)

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

    def test_indices_snapshot_only_replaces_cache_with_nonempty_success(self):
        valid = {
            'generated_at': '2026-07-17 10:00:00',
            'items': [{'code': 'sh000001', 'name': '上证指数', 'price': 3500}],
            'stale_cache': True,
        }
        self.assertTrue(dashboard.persist_indices_snapshot(valid))
        stored = json.loads(dashboard.INDICES_SNAPSHOT_FILE.read_text(encoding='utf-8'))
        self.assertEqual(stored['items'], valid['items'])
        self.assertNotIn('stale_cache', stored)

        self.assertFalse(dashboard.persist_indices_snapshot({'items': []}))
        self.assertFalse(dashboard.persist_indices_snapshot({'items': valid['items'], 'error': 'upstream failed'}))
        unchanged = json.loads(dashboard.INDICES_SNAPSHOT_FILE.read_text(encoding='utf-8'))
        self.assertEqual(unchanged, stored)

    def test_indices_route_serves_snapshot_while_refreshing_in_background(self):
        dashboard.INDICES_SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
        dashboard.INDICES_SNAPSHOT_FILE.write_text(
            json.dumps({
                'generated_at': '2026-07-17 09:30:00',
                'items': [{'code': 'sh000001', 'name': '上证指数', 'price': 3490}],
            }),
            encoding='utf-8',
        )
        original_producer = dashboard.produce_indices_data
        producer_started = threading.Event()
        release_producer = threading.Event()

        def slow_producer():
            producer_started.set()
            self.assertTrue(release_producer.wait(timeout=2))
            return {
                'generated_at': '2026-07-17 10:01:00',
                'items': [{'code': 'sh000001', 'name': '上证指数', 'price': 3510}],
            }

        dashboard.produce_indices_data = slow_producer
        try:
            handler = FakeHandler(path='/api/indices')
            started_at = dashboard.time.monotonic()
            handler.do_GET()
            elapsed = dashboard.time.monotonic() - started_at
            payload = json.loads(handler.wfile.getvalue().decode('utf-8'))

            self.assertEqual(handler.status, 200)
            self.assertLess(elapsed, 0.5)
            self.assertTrue(payload['stale_cache'])
            self.assertEqual(payload['items'][0]['price'], 3490)
            self.assertTrue(producer_started.wait(timeout=1))
        finally:
            release_producer.set()
            dashboard.produce_indices_data = original_producer

        deadline = dashboard.time.time() + 2
        while dashboard.time.time() < deadline:
            cached = dashboard.API_RESPONSE_CACHE.get('indices', {})
            if b'3510' in cached.get('payload', b''):
                break
            dashboard.time.sleep(0.01)
        refreshed = json.loads(dashboard.API_RESPONSE_CACHE['indices']['payload'])
        self.assertEqual(refreshed['items'][0]['price'], 3510)

    def test_indices_frontend_prioritizes_primary_quotes_and_labels_stale_cache(self):
        index_fetch = DASHBOARD_FRONTEND.index("fetchJson('/api/indices'")
        sector_fetch = DASHBOARD_FRONTEND.index("fetchJson('/api/sectors'")
        self.assertLess(index_fetch, sector_fetch)
        self.assertIn('正在后台更新实时行情', DASHBOARD_FRONTEND)
        self.assertIn('indices-cache-notice', DASHBOARD_FRONTEND)

    def test_index_template_intraday_curve_renders_single_point_from_opening_base(self):
        scenario = f"""
import {{ buildPracticeChartModel }} from {json.dumps(PRACTICE_CHART_UTILS_PATH.as_uri())};
const chart = buildPracticeChartModel({{
  initial_cash: 1000,
  current_date: '2026-07-22',
  equity_history: [{{time:'2026-07-22 10:05:00', equity:1010}}],
  daily_equity_history: [{{time:'2026-07-21 15:00:00', equity:1005}}],
}}, 'intraday');
process.stdout.write(JSON.stringify({{
  available: chart.available,
  base: chart.baseEquity,
  synthetic: chart.points[0]?.synthetic === true,
  openTime: chart.points[0]?.time,
  oneLivePoint: chart.points.length === 2,
}}));
"""
        result = subprocess.run(
            ['node', '--input-type=module', '-e', scenario],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            'available': True,
            'base': 1005,
            'synthetic': True,
            'openTime': '2026-07-22 09:30:00',
            'oneLivePoint': True,
        })

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
        self.assertIn('<div id="app">', unlocked_page.wfile.getvalue().decode('utf-8'))

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

    def test_model_test_api_requires_admin_action_and_whitelists_target_fields(self):
        original_sender = dashboard.send_model_connection_test
        original_admin_limit = dashboard.RATE_LIMIT_ADMIN
        original_test_limit = dashboard.RATE_LIMIT_MODEL_TEST
        calls = []
        body = urllib.parse.urlencode({
            'target': 'decision-model',
            'env__DASHBOARD_DECISION_MODEL': 'unsaved-model',
            'env__DASHBOARD_DECISION_BASE_URL': 'https://unsaved.example/v1',
            'env__DASHBOARD_DECISION_API_KEY': 'unsaved-key',
            'env__DASHBOARD_NEWS_API_KEY': 'must-be-ignored',
            'env__DASHBOARD_ADMIN_PASSWORD': 'must-be-ignored',
            'unrelated': 'must-be-ignored',
        }).encode('utf-8')
        try:
            dashboard.RATE_LIMIT_ADMIN = 100
            dashboard.RATE_LIMIT_MODEL_TEST = 100
            dashboard.send_model_connection_test = (
                lambda target, overrides: calls.append((target, dict(overrides)))
                or {'ok': True, 'target': target, 'message': '买卖决策模型已接通'}
            )

            unauthorized = FakeHandler(
                path='/api/admin/models/test',
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
                path='/api/admin/models/test',
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
                path='/api/admin/models/test',
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
            dashboard.send_model_connection_test = original_sender
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit
            dashboard.RATE_LIMIT_MODEL_TEST = original_test_limit

        self.assertEqual(handler.status, 200)
        self.assertTrue(response['ok'])
        self.assertEqual(calls, [(
            'decision-model',
            {
                'DASHBOARD_DECISION_MODEL': 'unsaved-model',
                'DASHBOARD_DECISION_BASE_URL': 'https://unsaved.example/v1',
                'DASHBOARD_DECISION_API_KEY': 'unsaved-key',
            },
        )])

    def test_iwencai_test_api_requires_admin_action_whitelists_and_rate_limits(self):
        original_sender = dashboard.send_iwencai_connection_test
        original_admin_limit = dashboard.RATE_LIMIT_ADMIN
        original_test_limit = dashboard.RATE_LIMIT_IWENCAI_TEST
        calls = []
        body = urllib.parse.urlencode({
            'env__IWENCAI_BASE_URL': 'https://unsaved.example',
            'env__IWENCAI_API_KEY': 'unsaved-key',
            'env__IWENCAI_TIMEOUT_SECONDS': '12',
            'env__IWENCAI_MAX_RETRIES': '2',
            'env__DASHBOARD_DECISION_API_KEY': 'must-be-ignored',
            'unrelated': 'must-be-ignored',
        }).encode('utf-8')
        try:
            dashboard.RATE_LIMIT_ADMIN = 100
            dashboard.RATE_LIMIT_IWENCAI_TEST = 1
            dashboard.RATE_LIMIT_BUCKETS.clear()
            dashboard.send_iwencai_connection_test = (
                lambda overrides: calls.append(dict(overrides))
                or {'ok': True, 'target': 'iwencai', 'message': '问财接口已接通'}
            )

            unauthorized = FakeHandler(
                path='/api/admin/iwencai/test',
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
                path='/api/admin/iwencai/test',
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

            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Content-Length': str(len(body)),
                'Cookie': self.admin_cookie(),
                dashboard.ACTION_HEADER_NAME: '1',
            }
            handler = FakeHandler(
                path='/api/admin/iwencai/test', method='POST', headers=headers, body=body,
            )
            handler.do_POST()
            response = json.loads(handler.wfile.getvalue().decode('utf-8'))
            limited = FakeHandler(
                path='/api/admin/iwencai/test', method='POST', headers=headers, body=body,
            )
            limited.do_POST()
        finally:
            dashboard.send_iwencai_connection_test = original_sender
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit
            dashboard.RATE_LIMIT_IWENCAI_TEST = original_test_limit
            dashboard.RATE_LIMIT_BUCKETS.clear()

        self.assertEqual(handler.status, 200)
        self.assertTrue(response['ok'])
        self.assertEqual(limited.status, 429)
        self.assertEqual(limited.rfile.tell(), 0)
        self.assertEqual(calls, [{
            'IWENCAI_BASE_URL': 'https://unsaved.example',
            'IWENCAI_API_KEY': 'unsaved-key',
            'IWENCAI_TIMEOUT_SECONDS': '12',
        }])

    def test_iwencai_test_uses_saved_secret_when_password_input_is_blank(self):
        names = dashboard.IWENCAI_TEST_FIELD_NAMES
        original_env = {name: dashboard.os.environ.get(name) for name in names}
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, limit=-1):
                return b'{"datas":[]}'

        def opener(request, timeout=0):
            captured['url'] = request.full_url
            captured['authorization'] = request.get_header('Authorization')
            captured['timeout'] = timeout
            return Response()

        try:
            for name in names:
                dashboard.os.environ.pop(name, None)
            dashboard.DASHBOARD_ENV_FILE.write_text(
                'IWENCAI_BASE_URL=https://saved.example\n'
                'IWENCAI_API_KEY=saved-key\n'
                'IWENCAI_TIMEOUT_SECONDS=18\n',
                encoding='utf-8',
            )
            result = dashboard.send_iwencai_connection_test(
                {
                    'IWENCAI_BASE_URL': 'https://unsaved.example',
                    'IWENCAI_API_KEY': '',
                },
                opener=opener,
            )
        finally:
            for name, value in original_env.items():
                if value is None:
                    dashboard.os.environ.pop(name, None)
                else:
                    dashboard.os.environ[name] = value

        self.assertTrue(result['ok'])
        self.assertEqual(captured['url'], 'https://unsaved.example/v1/query2data')
        self.assertEqual(captured['authorization'], 'Bearer saved-key')
        self.assertEqual(captured['timeout'], 18)

    def test_model_test_uses_saved_secret_when_password_input_is_blank(self):
        original_config_path = dashboard.CONFIG_PATH
        names = dashboard.model_test_setting_names()
        original_env = {name: dashboard.os.environ.get(name) for name in names}
        captured = {}

        class Response:
            headers = {'Content-Type': 'application/json'}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"ok"}}]}'

        def opener(request, timeout=0):
            captured['url'] = request.full_url
            captured['authorization'] = request.get_header('Authorization')
            captured['payload'] = json.loads(request.data.decode('utf-8'))
            captured['timeout'] = timeout
            return Response()

        try:
            for name in names:
                dashboard.os.environ.pop(name, None)
            dashboard.CONFIG_PATH = self.tmp_path / 'missing-config.yaml'
            dashboard.DASHBOARD_ENV_FILE.write_text(
                'DASHBOARD_DECISION_MODEL=saved-model\n'
                'DASHBOARD_DECISION_BASE_URL=https://saved.example/v1\n'
                'DASHBOARD_DECISION_API_KEY=saved-key\n',
                encoding='utf-8',
            )
            result = dashboard.send_model_connection_test(
                'decision-model',
                {
                    'DASHBOARD_DECISION_MODEL': 'unsaved-model',
                    'DASHBOARD_DECISION_API_KEY': '',
                },
                opener=opener,
            )
        finally:
            dashboard.CONFIG_PATH = original_config_path
            for name, value in original_env.items():
                if value is None:
                    dashboard.os.environ.pop(name, None)
                else:
                    dashboard.os.environ[name] = value

        self.assertTrue(result['ok'])
        self.assertEqual(captured['url'], 'https://saved.example/v1/chat/completions')
        self.assertEqual(captured['authorization'], 'Bearer saved-key')
        self.assertEqual(captured['payload']['model'], 'unsaved-model')
        self.assertLessEqual(captured['timeout'], 30)

    def test_practice_market_summary_status_is_public_and_generation_requires_admin_action(self):
        original_status = dashboard.get_practice_market_summary_status
        original_start = dashboard.start_practice_market_summary
        original_admin_limit = dashboard.RATE_LIMIT_ADMIN
        calls = []
        try:
            dashboard.RATE_LIMIT_ADMIN = 100
            dashboard.get_practice_market_summary_status = lambda: {
                'ok': True, 'available': False, 'scan_count': 2,
            }
            dashboard.start_practice_market_summary = lambda: calls.append(True) or {
                'ok': True, 'accepted': True, 'running': True, 'stage': 'starting',
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
            self.assertEqual(generated.status, 202)
            self.assertTrue(json.loads(generated.wfile.getvalue().decode('utf-8'))['accepted'])
            self.assertEqual(calls, [True])
        finally:
            dashboard.get_practice_market_summary_status = original_status
            dashboard.start_practice_market_summary = original_start
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit

    def test_practice_market_summary_generation_runs_in_one_background_thread(self):
        original_generate = dashboard.generate_practice_market_summary
        original_state = dict(dashboard.PRACTICE_MARKET_SUMMARY_STATE)
        started = threading.Event()
        release = threading.Event()

        def fake_generate():
            started.set()
            if not release.wait(2):
                raise TimeoutError("test did not release summary generation")
            return {
                "ok": True,
                "available": True,
                "generated_at": "2026-07-14 12:00:00",
            }

        try:
            dashboard.generate_practice_market_summary = fake_generate
            accepted = dashboard.start_practice_market_summary()
            self.assertTrue(accepted["accepted"])
            self.assertTrue(accepted["running"])
            self.assertTrue(started.wait(1))

            duplicate = dashboard.start_practice_market_summary()
            self.assertFalse(duplicate["accepted"])
            self.assertTrue(duplicate["running"])

            release.set()
            for _ in range(100):
                status = dashboard.practice_market_summary_generation_status()
                if not status["running"]:
                    break
                threading.Event().wait(0.01)
            self.assertFalse(status["running"])
            self.assertEqual(status["stage"], "completed")
            self.assertEqual(status["generated_at"], "2026-07-14 12:00:00")
            self.assertEqual(status["error"], "")

            dashboard.generate_practice_market_summary = lambda: {
                "ok": False,
                "error": "实时盘面抓取不完整：缺少A股实时指数",
            }
            failed = dashboard.start_practice_market_summary()
            self.assertTrue(failed["accepted"])
            for _ in range(100):
                failed_status = dashboard.practice_market_summary_generation_status()
                if not failed_status["running"]:
                    break
                threading.Event().wait(0.01)
            self.assertEqual(failed_status["stage"], "error")
            self.assertEqual(
                failed_status["error"],
                "实时盘面抓取不完整：缺少A股实时指数",
            )
        finally:
            release.set()
            for _ in range(100):
                if not dashboard.practice_market_summary_generation_status()["running"]:
                    break
                threading.Event().wait(0.01)
            dashboard.generate_practice_market_summary = original_generate
            with dashboard.PRACTICE_MARKET_SUMMARY_STATE_LOCK:
                dashboard.PRACTICE_MARKET_SUMMARY_STATE.clear()
                dashboard.PRACTICE_MARKET_SUMMARY_STATE.update(original_state)

        self.assertIn("marketSummaryPollTimer", PRACTICE_DATA)
        self.assertIn("scheduleMarketSummaryPoll()", PRACTICE_DATA)
        self.assertIn("盘面总结启动请求超时", PRACTICE_DATA)

    def test_practice_market_summary_prompts_for_admin_and_retries_generation(self):
        self.assertIn("error?.status === 403", PRACTICE_DATA)
        self.assertIn("error?.code === 'admin_password_required'", PRACTICE_DATA)
        self.assertIn("return 'admin_password_required'", PRACTICE_DATA)
        self.assertIn("await authenticateAdmin(adminAuth.credential)", PRACTICE_COMPONENTS)
        self.assertIn('@market-summary="generateMarketSummary"', PRACTICE_COMPONENTS)
        self.assertIn('class="dragon-tiger-admin-backdrop"', PRACTICE_COMPONENTS)
        self.assertIn("submitLabel: '验证并生成'", PRACTICE_COMPONENTS)

    def test_practice_manual_cycle_prompts_for_admin_and_retries_strategy(self):
        self.assertIn("const previousManualCycle = { ...state.manualCycle }", PRACTICE_DATA)
        self.assertIn("return 'admin_password_required'", PRACTICE_DATA)
        self.assertIn('@manual-cycle="runManualCycle"', PRACTICE_COMPONENTS)
        self.assertIn("requestAdminAuthentication('manual-cycle')", PRACTICE_COMPONENTS)
        self.assertIn("if (retryAction === 'manual-cycle') await runManualCycle()", PRACTICE_COMPONENTS)
        self.assertIn("title: '手动运行选股与交易策略'", PRACTICE_COMPONENTS)
        self.assertIn("submitLabel: '验证并运行'", PRACTICE_COMPONENTS)

    def test_manual_market_summary_snapshot_force_refreshes_live_channels(self):
        original_runner = dashboard.run_dashboard_helper
        original_builder = dashboard.practice_market_summary_impl.build_realtime_market_snapshot
        original_breadth = dashboard.produce_market_breadth_data
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
            dashboard.produce_market_breadth_data = lambda: {
                "available": True,
                "latest": {
                    "generated_at": "2026-07-14 12:00:00",
                    "red": 3000,
                    "green": 2000,
                    "limit_up": 50,
                    "limit_down": 5,
                    "broken_limit": 12,
                },
                "timeline": [],
            }
            now = datetime(2026, 7, 14, 12, 0, 0)

            result = dashboard.fetch_practice_realtime_market_snapshot(now)
        finally:
            dashboard.run_dashboard_helper = original_runner
            dashboard.practice_market_summary_impl.build_realtime_market_snapshot = original_builder
            dashboard.produce_market_breadth_data = original_breadth

        self.assertTrue(result["complete"])
        self.assertEqual({call[0] for call in calls}, {
            "indices_dashboard_api.py",
            "sectors_dashboard_api.py",
            "money_flow_dashboard_api.py",
        })
        self.assertTrue(all(call[1:] == (120, ("--force-refresh",)) for call in calls))
        self.assertEqual(captured["indices"]["script"], "indices_dashboard_api.py")
        self.assertEqual(captured["now"], now)
        self.assertTrue(result["reference_pages"]["market_breadth"])

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

    def test_model_test_api_has_dedicated_rate_limit_and_body_limit(self):
        original_sender = dashboard.send_model_connection_test
        original_admin_limit = dashboard.RATE_LIMIT_ADMIN
        original_test_limit = dashboard.RATE_LIMIT_MODEL_TEST
        calls = []
        body = b'target=decision-model&env__DASHBOARD_DECISION_MODEL=test-model'
        try:
            dashboard.RATE_LIMIT_ADMIN = 100
            dashboard.RATE_LIMIT_MODEL_TEST = 1
            dashboard.RATE_LIMIT_BUCKETS.clear()
            dashboard.send_model_connection_test = (
                lambda target, overrides: calls.append((target, dict(overrides)))
                or {'ok': True, 'target': target, 'message': 'ok'}
            )
            headers = {
                'Content-Length': str(len(body)),
                'Cookie': self.admin_cookie(),
                dashboard.ACTION_HEADER_NAME: '1',
            }
            first = FakeHandler(
                path='/api/admin/models/test', method='POST', headers=headers, body=body,
            )
            first.do_POST()
            second = FakeHandler(
                path='/api/admin/models/test', method='POST', headers=headers, body=body,
            )
            second.do_POST()

            dashboard.RATE_LIMIT_BUCKETS.clear()
            dashboard.RATE_LIMIT_MODEL_TEST = 100
            oversized = FakeHandler(
                path='/api/admin/models/test',
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
            dashboard.send_model_connection_test = original_sender
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit
            dashboard.RATE_LIMIT_MODEL_TEST = original_test_limit
            dashboard.RATE_LIMIT_BUCKETS.clear()

        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 429)
        self.assertEqual(second.rfile.tell(), 0)
        self.assertEqual(calls, [('decision-model', {'DASHBOARD_DECISION_MODEL': 'test-model'})])
        self.assertEqual(oversized.status, 413)
        self.assertEqual(oversized.rfile.tell(), 0)
        self.assertEqual(
            json.loads(oversized.wfile.getvalue().decode('utf-8'))['error'],
            'request_too_large',
        )

    def test_admin_test_apis_get_and_head_are_method_not_allowed(self):
        for path in (
            '/api/admin/notifications/test',
            '/api/admin/models/test',
            '/api/admin/iwencai/test',
        ):
            with self.subTest(path=path):
                get_handler = FakeHandler(path=path)
                get_handler.do_GET()
                head_handler = FakeHandler(path=path, method='HEAD')
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
        self.assertEqual(len(payload['groups']), 13)
        self.assertEqual(item_names, set(dashboard.ADMIN_VISIBLE_ENV_NAMES))
        self.assertIn('<div id="app">', index_body)
        self.assertNotIn("name='env__", index_body)
        self.assertIn('<AdminSettingsIndex', ADMIN_FRONTEND)
        self.assertIn('<AdminSettingsGroup', ADMIN_FRONTEND)
        self.assertIn('`/api/admin/config/env/${props.slug}`', ADMIN_FRONTEND)
        self.assertIn("'X-NiuOne-Action': '1'", ADMIN_FRONTEND)
        self.assertIn('onBeforeRouteLeave', ADMIN_FRONTEND)
        self.assertIn('保存本组设置', ADMIN_FRONTEND)
        self.assertIn('<AdminEnvInput', ADMIN_FRONTEND)
        self.assertEqual(
            [item['id'] for item in payload['model_tests']],
            ['news-precheck', 'decision-model', 'grok-model', 'us-rating-model', 'a-share-summary-model'],
        )
        self.assertIn("fetch('/api/admin/models/test'", ADMIN_FRONTEND)
        self.assertEqual(payload['iwencai_test']['group_slug'], 'iwencai')
        self.assertIn("fetch('/api/admin/iwencai/test'", ADMIN_FRONTEND)
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
            self.assertIn('<div id="app">', route.wfile.getvalue().decode('utf-8'))

        self.assertEqual(len(groups), 13)
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertEqual(slugs[:2], ['access-control', 'notifications'])
        self.assertEqual(grouped_names, set(dashboard.ADMIN_VISIBLE_ENV_NAMES))
        self.assertIn(':to="`/admin/settings/${group.slug}`"', ADMIN_FRONTEND)
        self.assertIn('保存本组设置', ADMIN_FRONTEND)
        self.assertEqual(len(dashboard.admin_setting_group_env_names('us-market')), 16)
        self.assertEqual(len(dashboard.admin_setting_group_env_names('iwencai')), 8)

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
        self.assertIn('<div id="app">', locked_body)
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
        self.assertEqual(missing.wfile.getvalue(), b'')

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
        dashboard.DASHBOARD_ENV_FILE.write_text(
            'DASHBOARD_US_FEATURES_ENABLED=0\n',
            encoding='utf-8',
        )
        disabled = FakeHandler(path='/api/dashboard/bootstrap')
        disabled.do_GET()
        disabled_payload = json.loads(disabled.wfile.getvalue().decode('utf-8'))

        dashboard.DASHBOARD_ENV_FILE.write_text(
            'DASHBOARD_US_FEATURES_ENABLED=1\n',
            encoding='utf-8',
        )
        enabled = FakeHandler(path='/api/dashboard/bootstrap')
        enabled.do_GET()
        enabled_payload = json.loads(enabled.wfile.getvalue().decode('utf-8'))

        self.assertEqual(disabled.status, 200)
        self.assertFalse(disabled_payload['us_features_enabled'])
        self.assertEqual(enabled.status, 200)
        self.assertTrue(enabled_payload['us_features_enabled'])
        tabs_source = (
            ROOT / 'web' / 'src' / 'composables' / 'useDashboardTabs.js'
        ).read_text(encoding='utf-8')
        self.assertIn("const US_FEATURE_CATEGORIES = new Set(['x_monitor', 'us_ratings'])", tabs_source)
        self.assertIn("fetch('/api/dashboard/bootstrap'", tabs_source)
        self.assertIn('usFeaturesEnabled.value = payload.us_features_enabled === true', tabs_source)
        self.assertIn('.filter(categoryAvailable)', tabs_source)

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

        self.assertIn('默认 4096 tokens；按所选接口映射为兼容的输出长度参数', ADMIN_FRONTEND)
        self.assertIn('默认 128000 tokens；填写后保存为数字 tokens', ADMIN_FRONTEND)

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
        original_flow_speed = dashboard.INDUSTRY_FLOW_PLAYBACK_SPEED
        original_flow_side_limit = dashboard.INDUSTRY_FLOW_SIDE_LIMIT
        original_flow_sample_interval = dashboard.INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS
        original_flow_windows = dashboard.INDUSTRY_FLOW_SAMPLING_WINDOWS
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
                'env__DASHBOARD_INDUSTRY_FLOW_PLAYBACK_SPEED': '0.75',
                'env__DASHBOARD_INDUSTRY_FLOW_SIDE_LIMIT': '6',
                'env__DASHBOARD_INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS': '120',
                'env__DASHBOARD_INDUSTRY_FLOW_MORNING_START': '09:20',
                'env__DASHBOARD_INDUSTRY_FLOW_MORNING_END': '11:32',
                'env__DASHBOARD_INDUSTRY_FLOW_AFTERNOON_START': '12:59',
                'env__DASHBOARD_INDUSTRY_FLOW_AFTERNOON_END': '15:02',
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
            runtime_flow_settings = (
                dashboard.INDUSTRY_FLOW_PLAYBACK_SPEED,
                dashboard.INDUSTRY_FLOW_SIDE_LIMIT,
                dashboard.INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS,
                dashboard.INDUSTRY_FLOW_SAMPLING_WINDOWS,
            )
        finally:
            dashboard.DASHBOARD_ENV_FILE = original_env_file
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit
            dashboard.schedule_niuone_services_restart = original_restart
            dashboard.B1_SCHEDULE_TIMES = original_b1_times
            dashboard.B1_SCHEDULE_ENABLED = original_b1_enabled
            dashboard.API_TTLS["indices"] = original_indices_ttl
            dashboard.INDUSTRY_FLOW_PLAYBACK_SPEED = original_flow_speed
            dashboard.INDUSTRY_FLOW_SIDE_LIMIT = original_flow_side_limit
            dashboard.INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS = original_flow_sample_interval
            dashboard.INDUSTRY_FLOW_SAMPLING_WINDOWS = original_flow_windows
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
        self.assertIn('industry_flow', response['runtime']['applied'])
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
        self.assertEqual(parsed['DASHBOARD_INDUSTRY_FLOW_PLAYBACK_SPEED'], '0.75')
        self.assertEqual(parsed['DASHBOARD_INDUSTRY_FLOW_SIDE_LIMIT'], '6')
        self.assertEqual(parsed['DASHBOARD_INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS'], '120')
        self.assertEqual(parsed['DASHBOARD_INDUSTRY_FLOW_MORNING_START'], '09:20')
        self.assertEqual(parsed['DASHBOARD_INDUSTRY_FLOW_MORNING_END'], '11:32')
        self.assertEqual(parsed['DASHBOARD_INDUSTRY_FLOW_AFTERNOON_START'], '12:59')
        self.assertEqual(parsed['DASHBOARD_INDUSTRY_FLOW_AFTERNOON_END'], '15:02')
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
        self.assertEqual(runtime_flow_settings, (
            0.75,
            6,
            120,
            (("09:20", "11:32"), ("12:59", "15:02")),
        ))
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

    def test_iwencai_settings_are_hot_applied_masked_and_invalidate_cache(self):
        names = {
            'IWENCAI_ENABLED',
            'IWENCAI_BASE_URL',
            'IWENCAI_API_KEY',
            'IWENCAI_TIMEOUT_SECONDS',
            'IWENCAI_MAX_RETRIES',
            'IWENCAI_MAX_CONCURRENCY',
            'IWENCAI_CACHE_TTL_SECONDS',
        }
        original_values = {name: dashboard.os.environ.get(name) for name in names}
        original_ttl = dashboard.API_TTLS['iwencai_dragon_tiger']
        try:
            dashboard.API_RESPONSE_CACHE['iwencai_dragon_tiger:2026-07-16:1:100'] = {
                'ts': 1,
                'payload': b'{}',
            }
            updates = dashboard.normalize_business_updates({
                'IWENCAI_ENABLED': '1',
                'IWENCAI_BASE_URL': 'https://openapi.iwencai.com/',
                'IWENCAI_API_KEY': 'test-secret',
                'IWENCAI_TIMEOUT_SECONDS': '18',
                'IWENCAI_MAX_RETRIES': '2',
                'IWENCAI_MAX_CONCURRENCY': '3',
                'IWENCAI_CACHE_TTL_SECONDS': '180',
            })
            dashboard.validate_business_updates(updates)
            result = dashboard.write_env_file_values(updates)
            runtime = dashboard.sync_business_runtime_settings(result['changed_names'])
            stored = dashboard.parse_env_file(
                dashboard.DASHBOARD_ENV_FILE,
                include_container_overrides=False,
            )
            payload = dashboard.build_admin_config_payload()
        finally:
            dashboard.API_TTLS['iwencai_dragon_tiger'] = original_ttl
            for name, value in original_values.items():
                if value is None:
                    dashboard.os.environ.pop(name, None)
                else:
                    dashboard.os.environ[name] = value

        by_name = {item['name']: item for item in payload['items']}
        self.assertEqual(stored['IWENCAI_BASE_URL'], 'https://openapi.iwencai.com')
        self.assertEqual(stored['IWENCAI_API_KEY'], 'test-secret')
        self.assertEqual(by_name['IWENCAI_API_KEY']['current_state'], '已设置')
        self.assertEqual(by_name['IWENCAI_API_KEY']['file_value'], '')
        self.assertNotIn('test-secret', json.dumps(payload, ensure_ascii=False))
        self.assertIn('iwencai', runtime['applied'])
        self.assertNotIn('iwencai_dragon_tiger:2026-07-16:1:100', dashboard.API_RESPONSE_CACHE)

    def test_iwencai_dragon_tiger_route_is_bounded_and_cached(self):
        original_fetch = dashboard.fetch_dragon_tiger
        original_ttl = dashboard.API_TTLS['iwencai_dragon_tiger']
        calls = []

        def fake_fetch(trade_date, *, page, limit):
            calls.append((trade_date, page, limit))
            return {
                'enabled': True,
                'available': True,
                'source': '同花顺问财',
                'date': trade_date,
                'items': [{'code': '000001.SZ'}],
            }

        try:
            dashboard.fetch_dragon_tiger = fake_fetch
            dashboard.API_TTLS['iwencai_dragon_tiger'] = 60
            auth_headers = {'Cookie': self.admin_cookie()}
            first = FakeHandler(
                '/api/iwencai/dragon-tiger?date=2026-07-16&page=2&limit=10',
                headers=auth_headers,
            )
            first.do_GET()
            second = FakeHandler(
                '/api/iwencai/dragon-tiger?date=2026-07-16&page=2&limit=10',
                headers=auth_headers,
            )
            second.do_GET()
            invalid = FakeHandler('/api/iwencai/dragon-tiger?page=1&limit=101')
            invalid.do_GET()
        finally:
            dashboard.fetch_dragon_tiger = original_fetch
            dashboard.API_TTLS['iwencai_dragon_tiger'] = original_ttl

        payload = json.loads(first.wfile.getvalue().decode('utf-8'))
        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 200)
        self.assertEqual(payload['items'], [{'code': '000001.SZ'}])
        self.assertEqual(calls, [('2026-07-16', 2, 10)])
        self.assertEqual(first.header('X-Dashboard-Cache'), 'MISS')
        self.assertEqual(second.header('X-Dashboard-Cache'), 'HIT')
        self.assertEqual(invalid.status, 400)
        self.assertEqual(
            json.loads(invalid.wfile.getvalue().decode('utf-8'))['error'],
            'invalid_iwencai_dragon_tiger_request',
        )

    def test_iwencai_dashboard_uses_latest_snapshot_without_upstream_call(self):
        snapshot = {
            'enabled': True,
            'available': True,
            'source': '同花顺问财',
            'date': '2026-07-16',
            'generated_at': '2026-07-16T18:00:00+08:00',
            'items': [{'code': '000001.SZ', 'name': '平安银行'}],
        }
        self.assertTrue(
            dashboard.write_dragon_tiger_snapshot(
                dashboard.IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE,
                snapshot,
            )
        )
        original_fetch = dashboard.fetch_dragon_tiger
        try:
            dashboard.fetch_dragon_tiger = lambda *_args, **_kwargs: self.fail('must not call upstream')
            payload = dashboard.produce_iwencai_dragon_tiger_data(
                '2026-07-17',
                page=1,
                limit=dashboard.IWENCAI_DRAGON_TIGER_DEFAULT_LIMIT,
                allow_latest_snapshot=True,
            )
        finally:
            dashboard.fetch_dragon_tiger = original_fetch

        self.assertTrue(payload['snapshot'])
        self.assertTrue(payload['stale'])
        self.assertEqual(payload['date'], '2026-07-16')
        self.assertEqual(payload['requested_date'], '2026-07-17')
        self.assertEqual(payload['scheduled_refresh_time'], '18:00')

    def test_iwencai_dashboard_current_empty_query_falls_back_to_latest_snapshot(self):
        current_date = dashboard.normalize_iwencai_trade_date('')
        snapshot = {
            'enabled': True,
            'available': True,
            'source': '同花顺问财',
            'date': '2000-01-05',
            'generated_at': '2000-01-05T18:00:00+08:00',
            'items': [{'code': '000001.SZ', 'name': '平安银行'}],
        }
        self.assertTrue(
            dashboard.write_dragon_tiger_snapshot(
                dashboard.IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE,
                snapshot,
            )
        )
        calls = []
        original_fetch = dashboard.fetch_dragon_tiger
        try:
            dashboard.fetch_dragon_tiger = lambda trade_date, **kwargs: calls.append(
                (trade_date, kwargs)
            ) or {
                'enabled': True,
                'available': True,
                'source': '同花顺问财',
                'date': trade_date,
                'items': [],
            }
            payload = dashboard.produce_iwencai_dragon_tiger_data(
                current_date,
                page=1,
                limit=dashboard.IWENCAI_DRAGON_TIGER_DEFAULT_LIMIT,
                allow_latest_snapshot=False,
                fallback_to_latest_on_empty=True,
            )
        finally:
            dashboard.fetch_dragon_tiger = original_fetch

        self.assertEqual(calls, [(current_date, {'page': 1, 'limit': 100})])
        self.assertTrue(payload['snapshot'])
        self.assertTrue(payload['stale'])
        self.assertEqual(payload['date'], '2000-01-05')
        self.assertEqual(payload['requested_date'], current_date)
        self.assertEqual(payload['items'][0]['code'], '000001.SZ')

    def test_iwencai_dashboard_historical_query_ignores_legacy_archive(self):
        legacy_archive = {
            'enabled': True,
            'available': True,
            'source': '同花顺问财',
            'date': '2026-07-15',
            'institution_available': True,
            'items': [{
                'code': '000001.SZ',
                'name': '平安银行',
                'institution_seats': [{
                    'seat_name': '机构专用',
                    'side': 'buy',
                    'rank': 1,
                    'buy_amount_yuan': 100.0,
                    'sell_amount_yuan': 0.0,
                    'net_amount_yuan': 100.0,
                }],
            }],
        }
        self.assertTrue(
            dashboard.write_dragon_tiger_archive(
                dashboard.iwencai_dragon_tiger_archive_dir(),
                legacy_archive,
            )
        )
        calls = []
        original_fetch = dashboard.fetch_dragon_tiger
        try:
            dashboard.fetch_dragon_tiger = lambda trade_date, **kwargs: calls.append(
                (trade_date, kwargs)
            ) or {
                'enabled': True,
                'available': True,
                'source': '同花顺问财',
                'date': trade_date,
                'items': [{'code': '600000.SH', 'name': '浦发银行'}],
            }
            payload = dashboard.produce_iwencai_dragon_tiger_data(
                '2026-07-15',
                page=1,
                limit=dashboard.IWENCAI_DRAGON_TIGER_DEFAULT_LIMIT,
                allow_latest_snapshot=False,
            )
        finally:
            dashboard.fetch_dragon_tiger = original_fetch

        self.assertEqual(len(calls), 1)
        self.assertEqual(payload['date'], '2026-07-15')
        self.assertEqual(payload['items'][0]['code'], '600000.SH')
        self.assertNotIn('archive', payload)
        self.assertFalse(dashboard.IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE.exists())
        self.assertTrue(
            dashboard.dragon_tiger_archive_path(
                dashboard.iwencai_dragon_tiger_archive_dir(),
                '2026-07-15',
            ).is_file()
        )

    def test_iwencai_dashboard_latest_success_expires_legacy_archives(self):
        legacy_archive = {
            'enabled': True,
            'available': True,
            'source': '同花顺问财',
            'date': '2026-07-16',
            'items': [{'code': '000001.SZ', 'name': '平安银行'}],
        }
        self.assertTrue(
            dashboard.write_dragon_tiger_archive(
                dashboard.iwencai_dragon_tiger_archive_dir(),
                legacy_archive,
            )
        )
        original_fetch = dashboard.fetch_dragon_tiger
        try:
            dashboard.fetch_dragon_tiger = lambda trade_date, **_kwargs: {
                'enabled': True,
                'available': True,
                'source': '同花顺问财',
                'date': trade_date,
                'items': [{'code': '600000.SH', 'name': '浦发银行'}],
            }
            payload = dashboard.produce_iwencai_dragon_tiger_data(
                '2026-07-17',
                page=1,
                limit=dashboard.IWENCAI_DRAGON_TIGER_DEFAULT_LIMIT,
                allow_latest_snapshot=True,
            )
        finally:
            dashboard.fetch_dragon_tiger = original_fetch

        self.assertTrue(payload['snapshot_saved'])
        self.assertEqual(payload['expired_archive_count'], 1)
        self.assertFalse(dashboard.iwencai_dragon_tiger_archive_dir().exists())
        latest = dashboard.read_dragon_tiger_snapshot(
            dashboard.IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE,
            trade_date='2026-07-17',
        )
        self.assertIsNotNone(latest)
        self.assertEqual(latest['items'][0]['code'], '600000.SH')

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
