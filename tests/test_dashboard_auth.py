#!/usr/bin/env python3
import importlib.util
import gzip
import io
import json
import os
import sqlite3
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
        self.saved_env = {
            name: os.environ.get(name)
            for name in (
                'X_WATCHLIST_ACCOUNTS',
                'DASHBOARD_X_WATCHLIST_STATE',
                dashboard.STRATEGY_SOURCE_ENV,
                dashboard.PERSONA_STRATEGY_ENV,
                dashboard.PRESET_STRATEGY_TEXT_ENV,
            )
        }
        for name in self.saved_env:
            os.environ.pop(name, None)
        dashboard.AUTH_DB = self.tmp_path / 'dashboard_users.db'
        dashboard.ADMIN_TOKEN_FILE = self.tmp_path / 'dashboard_admin_token.txt'
        dashboard.DASHBOARD_ENV_FILE = self.tmp_path / 'dashboard.env'
        dashboard.CRON_STATE_DIR = self.tmp_path / 'cron' / 'state'
        dashboard.API_RESPONSE_CACHE.clear()
        dashboard.API_CACHE_KEY_LOCKS.clear()
        dashboard.RATE_LIMIT_BUCKETS.clear()
        dashboard.AUTH_TOUCH_CACHE.clear()
        dashboard.ensure_auth_db()

    def tearDown(self):
        dashboard.DASHBOARD_ENV_FILE = self.original_dashboard_env_file
        dashboard.CRON_STATE_DIR = self.original_cron_state_dir
        for name, value in self.saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tmp.cleanup()

    def test_invite_redeem_creates_viewer_token_and_exhausts_limit(self):
        invite = dashboard.create_invite_code(code='TEST-CODE', max_uses=1, ttl_hours=1, note='unittest')
        self.assertEqual(invite['code'], 'TEST-CODE')

        first = dashboard.redeem_invite_code('TEST-CODE', nickname='alpha', ip='127.0.0.1', user_agent='unittest')
        self.assertTrue(first['ok'])
        self.assertTrue(first['token'].startswith('nv_'))

        viewer = dashboard.authenticate_viewer_token(first['token'], ip='127.0.0.1', user_agent='unittest')
        self.assertIsNotNone(viewer)
        self.assertEqual(viewer['nickname'], 'alpha')
        self.assertEqual(viewer['role'], 'viewer')

        second = dashboard.redeem_invite_code('TEST-CODE', nickname='beta', ip='127.0.0.1', user_agent='unittest')
        self.assertFalse(second['ok'])
        self.assertIn('已用完', second['error'])

    def test_disabled_viewer_is_rejected(self):
        dashboard.create_invite_code(code='BAN-CODE', max_uses=1, ttl_hours=1, note='unittest')
        redeemed = dashboard.redeem_invite_code('BAN-CODE', nickname='banme', ip='127.0.0.1', user_agent='unittest')
        self.assertTrue(redeemed['ok'])
        self.assertTrue(dashboard.set_viewer_disabled(redeemed['token'], True)['ok'])
        self.assertIsNone(dashboard.authenticate_viewer_token(redeemed['token'], ip='127.0.0.1', user_agent='unittest'))

    def test_admin_bootstrap_token_can_create_invites_and_list_viewers(self):
        token = dashboard.get_or_create_admin_token()
        admin = dashboard.authenticate_viewer_token(token, ip='127.0.0.1', user_agent='unittest')
        self.assertIsNotNone(admin)
        self.assertEqual(admin['role'], 'admin')

        created = dashboard.create_invite_code(code='ADMIN-CODE', max_uses=2, ttl_hours=24, note='admin')
        self.assertEqual(created['max_uses'], 2)
        self.assertTrue(any(i['code'] == 'ADMIN-CODE' for i in dashboard.list_invite_codes()))
        self.assertIsInstance(dashboard.list_viewers(), list)

    def test_auth_touch_is_throttled_to_reduce_public_polling_writes(self):
        dashboard.create_invite_code(code='TOUCH-CODE', max_uses=1, ttl_hours=1, note='unittest')
        redeemed = dashboard.redeem_invite_code('TOUCH-CODE', nickname='viewer', ip='0.0.0.0', user_agent='redeem')
        self.assertTrue(redeemed['ok'])

        first = dashboard.authenticate_viewer_token(redeemed['token'], ip='1.1.1.1', user_agent='first')
        second = dashboard.authenticate_viewer_token(redeemed['token'], ip='2.2.2.2', user_agent='second')
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)

        with closing(sqlite3.connect(dashboard.AUTH_DB)) as con:
            last_ip, user_agent = con.execute(
                'SELECT last_ip, user_agent FROM viewers WHERE token_hash=?',
                (dashboard.hash_token(redeemed['token']),),
            ).fetchone()
        self.assertEqual(last_ip, '1.1.1.1')
        self.assertEqual(user_agent, 'first')

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
        self.assertEqual(payload['snapshot_mode'], 'fast')

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
        self.assertIn("curveSubPrefix = hasSessionCurve ? '' : '仅有收盘点 · '", dashboard.INDEX_HTML)
        self.assertIn("time: `${date} 15:00:00`", dashboard.INDEX_HTML)
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
        self.assertNotIn('最近交易日收益', dashboard.INDEX_HTML)
        self.assertNotIn('getDay() === 0 || nowForCurve.getDay() === 6', dashboard.INDEX_HTML)

    def test_admin_token_redirect_sets_secure_cookie_and_security_headers(self):
        token = dashboard.get_or_create_admin_token()
        handler = FakeHandler(
            path=f'/admin?token={token}',
            headers={'X-Forwarded-Proto': 'https', 'CF-Connecting-IP': '203.0.113.11'},
            ip='127.0.0.1',
        )
        handler.do_GET()

        self.assertEqual(handler.status, 303)
        self.assertEqual(handler.header('Location'), '/admin')
        self.assertIn('HttpOnly', handler.header('Set-Cookie') or '')
        self.assertIn('SameSite=Lax', handler.header('Set-Cookie') or '')
        self.assertIn('Secure', handler.header('Set-Cookie') or '')
        self.assertEqual(handler.header('X-Frame-Options'), 'DENY')
        self.assertEqual(handler.header('X-Content-Type-Options'), 'nosniff')
        self.assertIn('max-age=31536000', handler.header('Strict-Transport-Security') or '')

    def test_forwarded_headers_are_only_trusted_from_configured_proxies(self):
        untrusted = FakeHandler(
            path='/login',
            headers={'CF-Connecting-IP': '203.0.113.10', 'X-Forwarded-Proto': 'https'},
            ip='198.51.100.44',
        )
        self.assertEqual(untrusted.client_ip(), '198.51.100.44')
        self.assertFalse(untrusted.is_secure_request())

        trusted = FakeHandler(
            path='/login',
            headers={'CF-Connecting-IP': '203.0.113.10', 'X-Forwarded-Proto': 'https'},
            ip='127.0.0.1',
        )
        self.assertEqual(trusted.client_ip(), '203.0.113.10')
        self.assertTrue(trusted.is_secure_request())

    def test_login_rate_limit_returns_429(self):
        original_login_limit = dashboard.RATE_LIMIT_LOGIN
        dashboard.RATE_LIMIT_LOGIN = 1
        try:
            body = b'code=NOPE&nickname=test'
            headers = {'Content-Type': 'application/x-www-form-urlencoded', 'CF-Connecting-IP': '203.0.113.77'}
            first = FakeHandler(
                path='/login',
                method='POST',
                headers={**headers, 'Content-Length': str(len(body))},
                body=body,
            )
            first.do_POST()

            second = FakeHandler(
                path='/login',
                method='POST',
                headers={**headers, 'Content-Length': str(len(body))},
                body=body,
            )
            second.do_POST()

            self.assertEqual(first.status, 403)
            self.assertEqual(second.status, 429)
        finally:
            dashboard.RATE_LIMIT_LOGIN = original_login_limit

    def test_send_payload_gzips_large_json_when_client_accepts_it(self):
        payload = json.dumps({"items": ["牛" * 50 for _ in range(200)]}, ensure_ascii=False).encode("utf-8")
        handler = FakeHandler(path="/api/messages", headers={"Accept-Encoding": "br, gzip"})

        handler.send_payload(payload)

        body = handler.wfile.getvalue()
        self.assertEqual(handler.header("Content-Encoding"), "gzip")
        self.assertIn("Accept-Encoding", handler.header("Vary") or "")
        self.assertLess(len(body), len(payload))
        self.assertEqual(gzip.decompress(body), payload)

    def test_admin_page_requires_configured_password_session(self):
        original_password = dashboard.ADMIN_PASSWORD
        original_admin_limit = dashboard.RATE_LIMIT_ADMIN
        try:
            dashboard.ADMIN_PASSWORD = 'secret-pass'
            dashboard.RATE_LIMIT_ADMIN = 100
            token = dashboard.get_or_create_admin_token()
            auth_cookie = f'{dashboard.AUTH_COOKIE_NAME}={token}'

            locked_page = FakeHandler(path='/admin', headers={'Cookie': auth_cookie})
            locked_page.do_GET()
            self.assertEqual(locked_page.status, 200)
            locked_body = locked_page.wfile.getvalue().decode('utf-8')
            self.assertIn('name="admin_password"', locked_body)
            self.assertIn('enterkeyhint="done"', locked_body)
            self.assertIn("data-admin-password-form", locked_body)
            self.assertIn("form.requestSubmit", locked_body)
            self.assertIn("event.key !== 'Enter'", locked_body)

            locked_api = FakeHandler(path='/api/admin/config', headers={'Cookie': auth_cookie})
            locked_api.do_GET()
            self.assertEqual(locked_api.status, 403)
            self.assertEqual(json.loads(locked_api.wfile.getvalue().decode('utf-8'))['error'], 'admin_password_required')

            wrong_body = urllib.parse.urlencode({'admin_password': 'wrong'}).encode('utf-8')
            wrong = FakeHandler(
                path='/admin/password',
                method='POST',
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Content-Length': str(len(wrong_body)),
                    'Cookie': auth_cookie,
                },
                body=wrong_body,
            )
            wrong.do_POST()
            self.assertEqual(wrong.status, 403)
            self.assertIn('管理员密码错误', wrong.wfile.getvalue().decode('utf-8'))

            correct_body = urllib.parse.urlencode({'admin_password': 'secret-pass'}).encode('utf-8')
            correct = FakeHandler(
                path='/admin/password',
                method='POST',
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Content-Length': str(len(correct_body)),
                    'Cookie': auth_cookie,
                },
                body=correct_body,
            )
            correct.do_POST()
            self.assertEqual(correct.status, 303)
            self.assertEqual(correct.header('Location'), '/admin')
            set_cookies = [value for key, value in correct.sent_headers if key.lower() == 'set-cookie']
            admin_cookie = next(value for value in set_cookies if value.startswith(f'{dashboard.ADMIN_PASSWORD_COOKIE_NAME}='))
            self.assertIn('HttpOnly', admin_cookie)
            session_cookie = admin_cookie.split(';', 1)[0]

            unlocked_api = FakeHandler(path='/api/admin/config', headers={'Cookie': f'{auth_cookie}; {session_cookie}'})
            unlocked_api.do_GET()
            self.assertEqual(unlocked_api.status, 200)
            api_payload = json.loads(unlocked_api.wfile.getvalue().decode('utf-8'))
            self.assertNotIn('env_file', api_payload)
            self.assertTrue(api_payload.get('items'))
        finally:
            dashboard.ADMIN_PASSWORD = original_password
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit

    def test_state_changing_api_requires_post_action_header(self):
        original_password = dashboard.ADMIN_PASSWORD
        original_admin_limit = dashboard.RATE_LIMIT_ADMIN
        original_trigger = dashboard.trigger_b1_scan
        calls = []
        try:
            dashboard.ADMIN_PASSWORD = ''
            dashboard.RATE_LIMIT_ADMIN = 100
            dashboard.trigger_b1_scan = lambda force=False: calls.append(force) or {'ok': True, 'forced': force}
            token = dashboard.get_or_create_admin_token()
            auth_cookie = f'{dashboard.AUTH_COOKIE_NAME}={token}'

            get_handler = FakeHandler(path='/api/b1_screen/trigger', headers={'Cookie': auth_cookie})
            get_handler.do_GET()
            self.assertEqual(get_handler.status, 405)
            self.assertEqual(get_handler.header('Allow'), 'POST')

            missing_header = FakeHandler(
                path='/api/b1_screen/trigger',
                method='POST',
                headers={'Cookie': auth_cookie, 'Content-Length': '0'},
            )
            missing_header.do_POST()
            self.assertEqual(missing_header.status, 403)
            self.assertEqual(json.loads(missing_header.wfile.getvalue().decode('utf-8'))['error'], 'action_header_required')
            self.assertEqual(calls, [])

            ok = FakeHandler(
                path='/api/b1_screen/trigger',
                method='POST',
                headers={
                    'Cookie': auth_cookie,
                    'Content-Length': '0',
                    dashboard.ACTION_HEADER_NAME: '1',
                },
            )
            ok.do_POST()
            self.assertEqual(ok.status, 200)
            self.assertEqual(json.loads(ok.wfile.getvalue().decode('utf-8'))['forced'], True)
            self.assertEqual(calls, [True])
        finally:
            dashboard.ADMIN_PASSWORD = original_password
            dashboard.RATE_LIMIT_ADMIN = original_admin_limit
            dashboard.trigger_b1_scan = original_trigger

    def test_admin_page_only_shows_business_config_content(self):
        original_password = dashboard.ADMIN_PASSWORD
        try:
            dashboard.ADMIN_PASSWORD = ''
            token = dashboard.get_or_create_admin_token()
            dashboard.create_invite_code(code='HIDDEN-CODE', max_uses=1, ttl_hours=1, note='hidden')
            redeemed = dashboard.redeem_invite_code('HIDDEN-CODE', nickname='viewer', ip='127.0.0.1', user_agent='unittest')
            self.assertTrue(redeemed['ok'])

            handler = FakeHandler(
                path='/admin',
                headers={'Cookie': f'{dashboard.AUTH_COOKIE_NAME}={token}'},
            )
            handler.do_GET()
            body = handler.wfile.getvalue().decode('utf-8')
        finally:
            dashboard.ADMIN_PASSWORD = original_password

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
        self.assertIn('美股评级上下文长度', body)
        self.assertIn('推文监控作者', body)
        self.assertIn('推文监控间隔', body)
        self.assertIn('美股买入评级时间', body)
        self.assertLess(body.index('开启牛牛美股'), body.index('消息面预检模型'))
        self.assertLess(body.index('Grok 模型'), body.index('消息面预检模型'))
        self.assertLess(body.index('推文监控作者'), body.index('消息面预检模型'))
        self.assertLess(body.index('美股买入评级时间'), body.index('消息面预检模型'))
        self.assertNotIn('推文监控/美股买入评级模型', body)
        self.assertNotIn('<h2>推文监控作者</h2>', body)
        self.assertNotIn('<h2>推文监控周期</h2>', body)
        self.assertNotIn('<h2>美股买入评级周期</h2>', body)
        self.assertIn('买卖决策模型', body)
        self.assertIn('买卖决策上下文长度', body)
        self.assertIn('消息面预检上下文长度', body)
        self.assertIn('可填 1M、128K 或完整数字', body)
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

    def test_home_page_uses_us_feature_flag_for_tabs_without_deleting_data(self):
        original_auth_enabled = dashboard.AUTH_ENABLED
        try:
            dashboard.AUTH_ENABLED = False
            dashboard.DASHBOARD_ENV_FILE.write_text('DASHBOARD_US_FEATURES_ENABLED=0\n', encoding='utf-8')
            disabled = FakeHandler(path='/?category=x_monitor')
            disabled.do_GET()
            disabled_body = disabled.wfile.getvalue().decode('utf-8')

            dashboard.DASHBOARD_ENV_FILE.write_text('DASHBOARD_US_FEATURES_ENABLED=1\n', encoding='utf-8')
            enabled = FakeHandler(path='/?category=x_monitor')
            enabled.do_GET()
            enabled_body = enabled.wfile.getvalue().decode('utf-8')
        finally:
            dashboard.AUTH_ENABLED = original_auth_enabled

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

        self.assertEqual(source_item['effective'], '预设文字')
        self.assertEqual(source_item['file_value'], 'preset_text')
        self.assertEqual(text_item['file_value'], '强趋势回踩\n跌破5日线离场')
        self.assertEqual(text_item['effective'], '强趋势回踩\n跌破5日线离场')
        self.assertEqual(discipline_item['file_value'], '纪律一\n纪律二')
        self.assertEqual(discipline_item['effective'], '纪律一\n纪律二')

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
            token = dashboard.get_or_create_admin_token()
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
                    'Cookie': f'{dashboard.AUTH_COOKIE_NAME}={token}',
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
            token = dashboard.get_or_create_admin_token()
            body = urllib.parse.urlencode({
                'env__DASHBOARD_GROK_MODEL': 'grok-test',
            }).encode('utf-8')
            handler = FakeHandler(
                path='/api/admin/config/env',
                method='POST',
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Content-Length': str(len(body)),
                    'Cookie': f'{dashboard.AUTH_COOKIE_NAME}={token}',
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


if __name__ == '__main__':
    unittest.main()
