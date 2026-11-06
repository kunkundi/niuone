"""Regression coverage for partial listings and overlapping schedule slots."""
from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'app/compat')]
import multi_strategy_screen as scanner
import niuone_dashboard as dashboard


class Rows(list):
    def iterrows(self):
        return enumerate(self)


class RuntimeSlotIsolationTests(unittest.TestCase):
    def test_protocol_freezes_all_scan_input_and_transport_sources(self):
        from app.trading.niuone_forward_service import PROTOCOL_SOURCE_PATHS
        for path in ("app/screening/scan_artifact.py", "app/market_data/tencent_kline_cache.py",
                     "app/market_data/exchange_listing.py", "app/screening/niuone_minute.py"):
            self.assertIn(path, PROTOCOL_SOURCE_PATHS)
        self.assertEqual(len(set(PROTOCOL_SOURCE_PATHS)), 36)

    def test_partial_listing_requires_verified_second_exchange(self):
        fallback = Rows([{'code': '000001', 'name': '深市主板'}, {'code': '300001', 'name': '创业板'}])
        def sh(symbol):
            code = '600001' if symbol == '主板A股' else '688001'
            return Rows([{'证券代码': code, '证券简称': '沪市'}])
        def unavailable(**kwargs):
            raise TimeoutError('listing timed out')
        provider = types.SimpleNamespace(stock_info_sh_name_code=sh, stock_info_sz_name_code=unavailable,
                                         stock_info_a_code_name=lambda: fallback)
        with patch.dict(sys.modules, {'akshare': provider}):
            result = scanner.load_a_share_code_pool(scanner.FULL_SUPPORTED_NON_ST_UNIVERSE)
            self.assertEqual({s for s, _ in result}, {'600001','688001','000001','300001'})
            fallback.pop()
            with self.assertRaisesRegex(scanner.IncompleteListingError, 'sz_chi_next'):
                scanner.load_a_share_code_pool(scanner.FULL_SUPPORTED_NON_ST_UNIVERSE)

    def test_failed_catalog_never_replaces_same_day_complete_catalog_with_partial_data(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            path = Path(tmp) / 'a_share_listing_latest.json'
            stack.enter_context(patch.object(scanner, 'B1_OUTPUT_DIR', Path(tmp)))
            runner = stack.enter_context(patch('subprocess.run'))
            yesterday = {'schema_version': 1, 'date': '2000-01-01', 'rows': []}
            path.write_text(json.dumps(yesterday), encoding='utf-8')
            today = datetime.now().strftime('%Y-%m-%d')
            sh_only = [[f'{prefix}{i:03d}', f'测试{i}'] for prefix in ('600','601','603','605','688') for i in range(1000)]
            runner.return_value = types.SimpleNamespace(returncode=0,stdout=json.dumps({'date':today,'schema_version':1,'rows':sh_only}).encode())
            with self.assertRaises(scanner.IncompleteListingError): scanner.load_daily_scan_pool('main_board')
            self.assertEqual(json.loads(path.read_text()),yesterday)
            complete = sh_only + [['000001','深市'],['300001','创业']]
            runner.return_value.stdout = json.dumps({'date':today,'schema_version':1,'rows':complete}).encode()
            self.assertEqual(scanner.load_daily_scan_pool('chi_next'), [('300001','创业')])
            runner.side_effect = AssertionError('fresh complete catalog should be shared')
            self.assertEqual(len(scanner.load_daily_scan_pool('star_market')), 1000)

    def test_next_slot_waits_for_active_cycle_and_preserves_failed_slot_history(self):
        now = datetime(2026, 11, 6, 10, 30)
        first, second = '2026-11-06 10:00', '2026-11-06 10:30'
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            stack.enter_context(patch.object(dashboard,'B1_SCHEDULE_STATE_FILE',Path(tmp)/'slots.json'))
            stack.enter_context(patch.object(dashboard,'PRACTICE_SCHEDULE_TIMES',('10:00','10:30')))
            stack.enter_context(patch.object(dashboard,'B1_SCHEDULE_RUN_KEYS',{first}))
            dashboard._mark_b1_schedule_slot(first,'running')
            self.assertIsNone(dashboard.claim_due_b1_schedule_slot(now))
            dashboard._mark_b1_schedule_slot(first,'error',error='expired',error_code='model_timeout')
            self.assertEqual(dashboard.claim_due_b1_schedule_slot(now),second)
            state=dashboard._load_b1_schedule_state_unlocked()
            self.assertEqual(state['day_history']['2026-11-06']['slots']['10:00']['error_code'],'model_timeout')
            dashboard._mark_b1_schedule_slot(second,'ok')
            self.assertIsNone(dashboard.claim_due_b1_schedule_slot(now))

    def test_concurrent_listing_reader_joins_the_verified_publication(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            stack.enter_context(patch.object(scanner, 'B1_OUTPUT_DIR', root))
            runner = stack.enter_context(patch('subprocess.run'))
            lock = root / 'a_share_listing_latest.lock'
            lock.touch()
            rows = [[f'{prefix}{i:03d}', '测试'] for prefix in ('600','601','603','605','688') for i in range(1000)]
            rows += [['000001','深市'],['300001','创业板']]
            def publish(_delay):
                (root/'a_share_listing_latest.json').write_text(json.dumps({
                    'schema_version':1, 'date':datetime.now().strftime('%Y-%m-%d'), 'rows':rows}), encoding='utf-8')
                lock.unlink()
            stack.enter_context(patch.object(scanner.time,'sleep',side_effect=publish))
            self.assertEqual(scanner.load_daily_scan_pool('chi_next'), [('300001','创业板')])
            runner.assert_not_called()

    def test_retry_clears_previous_stage_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(dashboard,'B1_SCHEDULE_STATE_FILE',Path(tmp)/'slots.json'), patch.object(dashboard,'B1_SCHEDULE_RUN_KEYS',set()):
            key='2026-11-06 09:25'
            dashboard._mark_b1_schedule_slot(key,'error',error='coverage',error_code='kline_cache_failed',failure_stage='cache')
            dashboard._mark_b1_schedule_slot(key,'running')
            dashboard._mark_b1_schedule_slot(key,'error',error='expired',reason='practice_decision_failed')
            row=dashboard._load_b1_schedule_state_unlocked()['slots'][key]
            self.assertEqual(row['error'],'expired')
            self.assertNotIn('failure_stage',row)
            self.assertNotIn('error_code',row)

    def test_other_slot_scan_is_never_relabeled_or_traded(self):
        with ExitStack() as stack:
            for name,value in {'market_data_readiness':{'ready':True}, 'b1_cache_generated_for_slot':False,
                               'refresh_practice_market_summary_for_decision':{}, '_b1_schedule_slot_lag_seconds':0,
                               'trigger_b1_scan':{'busy':True}, 'wait_for_b1_scan_result':{'schedule_slot':'2026-11-06 10:00'},
                               'start_independent_niuone_mainline_scan':False}.items():
                stack.enter_context(patch.object(dashboard,name,return_value=value))
            marked=stack.enter_context(patch.object(dashboard,'_mark_b1_schedule_slot'))
            decision=stack.enter_context(patch.object(dashboard,'run_practice_decision_logged'))
            dashboard.run_scheduled_b1_scan('2026-11-06 10:30')
            decision.assert_not_called()
            self.assertEqual(marked.call_args.kwargs['error_code'],'scan_owned_by_other_slot')
