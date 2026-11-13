import concurrent.futures
import sys
import threading
import tempfile
import subprocess
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'app/compat')]
import niuniu_practice_trader as trader
from market_data import market_sentiment
from market_data.market_sentiment import fetch_market_sentiment


class DecisionSourceConcurrencyTests(unittest.TestCase):
    def test_sources_are_parallel_and_concurrent_readers_share_one_collection(self):
        barrier = threading.Barrier(5)
        calls = []
        def fetch(label):
            def run(*args):
                calls.append(label)
                barrier.wait(timeout=5)
                return {'source': label, 'items': [{'price': 1.23456789012345}]}
            return run
        fake = {
            'indices_dashboard_api': types.SimpleNamespace(fetch_indices_data=lambda: self.fail('chart endpoint used'), fetch_indices_quote_data=fetch('indices')),
            'sectors_dashboard_api': types.SimpleNamespace(fetch_sector_data=fetch('sectors')),
            'money_flow_dashboard_api': types.SimpleNamespace(fetch_money_flow=fetch('money_flow')),
            'hot_stocks_dashboard_api': types.SimpleNamespace(fetch_hot_stocks=fetch('hot_stocks')),
            'market_flow_dashboard_api': types.SimpleNamespace(fetch_market_flow=fetch('market_flow')),
        }
        with patch.dict(sys.modules, fake), patch.object(trader, 'DECISION_INTELLIGENCE_ENABLED', True), patch.object(
            trader, 'DECISION_INTELLIGENCE_CACHE', {'ts': 0, 'data': None}
        ):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(trader.fetch_global_decision_sources)
                second = pool.submit(trader.fetch_global_decision_sources)
                a, b = first.result(timeout=8), second.result(timeout=8)
            self.assertIs(a, b)
            self.assertEqual(set(calls), {'indices', 'sectors', 'money_flow', 'hot_stocks', 'market_flow'})
            self.assertEqual(len(calls), 5)
            self.assertEqual(set(a['source_timings_s']), set(calls))
            self.assertEqual(a['sources']['indices']['items'][0]['price'], 1.23456789012345)

    def test_one_failed_channel_does_not_discard_other_current_sources(self):
        def source(label, fetcher, empty):
            if label == 'hot_stocks':
                return {'items': [{'code': '600000'}], 'error': 'timeout', 'stale_cache': True}
            return {'items': [{'source': label}]}
        with patch.object(trader, '_fetch_decision_source', side_effect=source), patch.object(
            trader, 'DECISION_INTELLIGENCE_ENABLED', True
        ), patch.object(trader, 'DECISION_INTELLIGENCE_CACHE', {'ts': 0, 'data': None}):
            result = trader.fetch_global_decision_sources()
        self.assertEqual(trader._source_status(result['sources']['hot_stocks']), 'stale')
        self.assertEqual(result['sources']['indices']['items'], [{'source': 'indices'}])

    def test_prompt_preparation_records_the_actual_remaining_request_budget(self):
        timing = {}
        portfolio = {'cash': 10000, 'total_equity': 10000, 'market_value': 0, 'positions': [],
                     '_decision_attempt': {'timing': timing}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(trader, 'DECISION_REASONING_EFFORT', 'max'))
            for name, value in {'load_decision_model_config': ('https://example.test', 'test-key'),
                                'check_market_environment': {'bullish': True, 'detail': ''},
                                'check_market_sentiment': {'sentiment': 'neutral', 'detail': ''},
                                'check_candidate_news_precheck': '',
                                'get_adaptive_params': {'position_mult': 1.0, 'label': '中性'},
                                'safe_decision_intelligence_context': {'enabled': False},
                                'current_preset_strategy_text': '',
                                'current_strategy_suite': 'niuone',
                                'active_strategy_ids_for_decision': ['niu_leader']}.items():
                stack.enter_context(patch.object(trader, name, return_value=value))
            request = stack.enter_context(patch.object(trader, 'request_chat_json_object', return_value={'summary': '观察', 'actions': []}))
            with trader.model_request_budget(90):
                result = trader.call_model_decision([], portfolio, True, 'test', {'enabled': False})
        self.assertEqual(result['actions'], [])
        self.assertGreater(timing['request_budget_s'], 80)
        self.assertLessEqual(timing['request_budget_s'], 90)
        self.assertIn('intelligence_elapsed_s', timing)
        self.assertEqual(request.call_args.args[2]['reasoning_effort'], 'max')

    def test_sentiment_worker_preserves_count_thresholds_and_sector_counts(self):
        class Frame:
            empty = False
            def __len__(self):return count
            def iterrows(self):return enumerate([{'所属行业': '半导体'}, {'所属行业': '半导体'}, {'所属行业': '银行'}])
        api = types.SimpleNamespace(stock_zt_pool_em=lambda **kwargs: Frame())
        for count, label in ((29, 'cold'), (30, 'neutral'), (79, 'neutral'), (80, 'hot')):
            with self.subTest(count=count), patch.dict(sys.modules, {'akshare': api}):
                result = fetch_market_sentiment()
            self.assertEqual(result['sentiment'], label)
            self.assertEqual(result['limit_up_count'], count)
            self.assertEqual(result['hot_sectors'], ['半导体(2只)', '银行(1只)'])

    def test_sentiment_uses_bounded_worker_and_keeps_existing_unknown_fallback(self):
        with patch.object(trader, 'MARKET_SENTIMENT_CACHE', {'ts': 0}), patch.object(
            market_sentiment, 'read_current_market_sentiment', side_effect=TimeoutError('bounded')
        ) as worker:
            first = trader.check_market_sentiment()
            second = trader.check_market_sentiment()
        self.assertEqual(first['sentiment'], 'unknown')
        self.assertEqual(first, second)
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(worker.call_args.args[0].name, 'market_sentiment.json')

    def test_forward_protocol_freezes_all_decision_source_implementations(self):
        from app.trading.niuone_forward_service import PROTOCOL_SOURCE_PATHS
        for path in ('app/dashboard/apis/indices_service.py', 'app/dashboard/apis/hot_stocks_service.py',
                     'app/dashboard/apis/sectors_service.py', 'app/dashboard/apis/money_flow_service.py',
                     'app/dashboard/apis/cache.py', 'app/entrypoints/market_sentiment_worker.py',
                     'app/market_data/market_sentiment.py'):
            self.assertIn(path, PROTOCOL_SOURCE_PATHS)

    def test_sentiment_timeout_terminates_worker_and_releases_lease(self):
        with tempfile.TemporaryDirectory(prefix='niuone-sentiment-') as folder:
            cache = Path(folder) / 'sentiment.json'
            with patch.object(market_sentiment.subprocess, 'run', side_effect=subprocess.TimeoutExpired('worker', 20)) as run:
                with self.assertRaisesRegex(TimeoutError, '20s'):
                    market_sentiment.read_current_market_sentiment(cache)
            self.assertEqual(run.call_args.kwargs['timeout'], 20)
            self.assertFalse(cache.with_suffix('.fetch.lock').exists())


if __name__ == '__main__':
    unittest.main()
