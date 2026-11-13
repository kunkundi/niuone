import json
import sys
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'app'), str(ROOT / 'app/compat')]
from dashboard.apis import hot_stocks_service as hot
from dashboard.apis import indices_service as indices
from app.market_data import listing_cache


class DecisionMarketSourceTests(unittest.TestCase):
    def test_hot_universe_uses_verified_catalog_and_preserves_mainboard_filter(self):
        rows = [('000001', '主板'), ('001001', '新主板'), ('003001', '主板三'),
                ('605001', '沪市主板'), ('600001', '*ST测试'), ('600002', '退市测试'),
                ('300001', '创业板'), ('688001', '科创板')]
        with patch.object(listing_cache, 'load_current_listing', return_value=rows) as load:
            self.assertEqual(hot._load_universe(), ['000001', '001001', '003001', '605001'])
        self.assertEqual(load.call_args.args[0], hot.CACHE_BASE / 'a_share_listing_latest.json')

    def test_missing_catalog_does_not_invent_symbol_ranges(self):
        with patch.object(listing_cache, 'load_current_listing', side_effect=listing_cache.ListingCacheError('unavailable')):
            with self.assertRaises(listing_cache.ListingCacheError):
                hot._load_universe()

    def test_quote_batches_run_concurrently_and_keep_exact_rows(self):
        codes = ['000001', '600001', '001001']
        barrier = threading.Barrier(len(codes))
        def response(url, timeout):
            barrier.wait(timeout=5)
            code = url.rsplit('=', 1)[1][-6:]
            return json.dumps({'code': code, 'price': 1.23456789012345}).encode()
        with patch.object(hot, '_urlopen', side_effect=response), patch.object(hot, '_parse_qt_line', side_effect=json.loads):
            rows = hot._fetch_quotes(codes, chunk=1)
        self.assertEqual(rows, [{'code': code, 'price': 1.23456789012345} for code in sorted(codes)])

    def test_failed_or_incomplete_batch_never_returns_a_partial_ranking(self):
        row = json.dumps({'code': '600001', 'price': 12}).encode()
        with patch.object(hot, '_urlopen', return_value=row) as request, patch.object(hot, '_parse_qt_line', side_effect=json.loads):
            with self.assertRaisesRegex(RuntimeError, 'batch failed'):
                hot._fetch_quotes(['600001', '600002'])
        self.assertEqual(request.call_count, 2)

    def test_batch_retries_transient_failure_without_dropping_members(self):
        row = json.dumps({'code': '600001', 'price': 12}).encode()
        with patch.object(hot, '_urlopen', side_effect=[TimeoutError('transient'), row]), patch.object(hot, '_parse_qt_line', side_effect=json.loads):
            self.assertEqual(hot._fetch_quotes(['600001']), [{'code': '600001', 'price': 12}])

    def test_compute_rejects_partial_mocked_transport(self):
        with patch.object(hot, '_load_universe', return_value=['600001', '600002']), patch.object(
            hot, '_fetch_quotes', return_value=[{'code': '600001', 'price': 12, 'amount_wan': 10}]
        ):
            with self.assertRaisesRegex(RuntimeError, 'universe incomplete'):
                hot._compute_in_process()

    def test_index_quotes_match_full_payload_without_loading_charts(self):
        index_defs = [('sh', 'sh000001', '上证指数', 'domestic', 'a_index'),
                      ('dow', 'usDJI', '道指', 'global', 'us_index')]
        sina_defs = [('a50_fut', 'hf_CHA50CFD', 'A50', 'global', 'a_futures', 'CHA50CFD')]
        quote = {'price': 123.123456789, 'prev_close': 120, 'change': 3.12,
                 'change_pct': 2.6, 'high': 124, 'low': 119, 'time': '2026-11-13 10:00:00'}
        with ExitStack() as stack:
            for name, value in {'_CACHE': {'ts': 0, 'data': None}, '_QUOTE_CACHE': {'ts': 0, 'data': None},
                                'INDEX_DEFS': index_defs, 'SINA_DEFS': sina_defs}.items():
                stack.enter_context(patch.object(indices, name, value))
            for name in ('_qt_query', '_sina_us_index_query', '_sina_query'):
                stack.enter_context(patch.object(indices, name, return_value='fixture'))
            stack.enter_context(patch.object(indices, '_parse_qt', return_value={'sh000001': quote}))
            stack.enter_context(patch.object(indices, '_parse_sina_us_indices', return_value={'usDJI': quote}))
            stack.enter_context(patch.object(indices, '_fetch_minute_quote', return_value={}))
            stack.enter_context(patch.object(indices, '_parse_sina', return_value={'hf_CHA50CFD': quote}))
            chart = stack.enter_context(patch.object(indices, '_fetch_minute_line', return_value=[{'time': '10:00', 'price': 1}, {'time': '10:01', 'price': 2}]))
            global_chart = stack.enter_context(patch.object(indices, '_fetch_sina_global_minute_line', return_value=[]))
            daily = stack.enter_context(patch.object(indices, '_fetch_kline', return_value=[]))
            lean = indices.fetch_indices_quote_data()
            chart.assert_not_called(); global_chart.assert_not_called(); daily.assert_not_called()
            self.assertIsNone(indices._CACHE['data'])
            full = indices.fetch_indices_data()
            for simple, complete in zip(lean['items'], full['items']):
                self.assertEqual(simple, {key: complete[key] for key in simple})
            self.assertTrue(full['items'][0]['minute_line'])


if __name__ == '__main__':
    unittest.main()
