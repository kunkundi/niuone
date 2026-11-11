"""Daily listing recovery and bounded A-share reporting contracts."""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime
from io import BytesIO, StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'app/compat')]
from app.market_data import exchange_listing as listing
from app.market_data.listing_cache import catalog_readiness
from reports.a_share import provider_worker, tencent_spot
import niuone_dashboard as dashboard
import multi_strategy_screen as scanner


class RuntimeRecoveryTests(unittest.TestCase):
    day = '2026-11-12'

    def get(self, url, params, deadline):
        if url == listing.SZ:
            raise TimeoutError('workbook timeout')
        if url == listing.SZ + '/data':
            page = int(params['PAGENO'])
            return json.dumps([{'metadata':{'tabkey':'tab1', 'subname':self.day,
                'recordcount':2,'pagesize':1,'pagecount':2,'pageno':page},
                'data':[{'agdm':'000001' if page == 1 else '300001',
                         'agjc':'<a><u>*ST测试</u></a>' if page == 1 else '创业'}]}])
        code = '600001' if params['STOCK_TYPE'] == '1' else '688001'
        return json.dumps({'pageHelp':{'total':1,'pageCount':1,'pageNo':1},
                           'result':[{'A_STOCK_CODE':code,'SEC_NAME_CN':'沪市'}]})

    def test_json_pages_recover_a_workbook_failure_with_exact_identities(self):
        rows = dict(listing.fetch_exchange_listing({self.day}, get=self.get))
        self.assertEqual(rows, {'000001':'*ST测试','300001':'创业','600001':'沪市','688001':'沪市'})

    def test_json_duplicate_or_metadata_drift_fails_closed(self):
        for field in ('agdm', 'recordcount'):
            def changed(url, params, deadline):
                raw = self.get(url, params, deadline)
                if url == listing.SZ + '/data' and params['PAGENO'] == '2':
                    value = json.loads(raw)
                    if field == 'agdm':value[0]['data'][0]['agdm'] = '000001'
                    else:value[0]['metadata']['recordcount'] = 3
                    return json.dumps(value)
                return raw
            with self.subTest(field=field), self.assertRaises(listing.ListingFetchError):
                listing.fetch_exchange_listing({self.day}, get=changed)

    def test_partial_success_is_shared_only_within_its_verified_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'parts.json'
            def first(url, params, deadline):
                if url == listing.SH and params['STOCK_TYPE'] == '1':raise TimeoutError()
                return self.get(url, params, deadline)
            with self.assertRaises(listing.ListingFetchError):
                listing.fetch_exchange_listing({self.day}, get=first, cache_path=path, cache_day=self.day)
            self.assertEqual(set(json.loads(path.read_text())['parts']), {'Shanghai STAR','Shenzhen'})
            def second(url, params, deadline):
                self.assertEqual(url, listing.SH)
                self.assertEqual(params['STOCK_TYPE'], '1')
                return self.get(url, params, deadline)
            self.assertEqual(len(listing.fetch_exchange_listing({self.day},get=second,cache_path=path,cache_day=self.day)),4)
            with self.assertRaises(listing.ListingFetchError):
                listing.fetch_exchange_listing({'2026-11-13'},get=second,cache_path=path,cache_day='2026-11-13')

    def test_error_summary_preserves_stage_and_http_status(self):
        exc = HTTPError('https://provider.invalid/private-query', 502, 'upstream body', {}, None)
        self.assertEqual(listing.listing_error_summary(exc), 'HTTPError(HTTP 502)')

    def test_shared_catalog_validator_is_frozen_in_forward_evidence(self):
        from app.trading.niuone_forward_service import PROTOCOL_SOURCE_PATHS
        self.assertIn('app/market_data/listing_cache.py', PROTOCOL_SOURCE_PATHS)

    def test_scan_surfaces_the_worker_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(scanner, 'B1_OUTPUT_DIR', Path(tmp)), patch('subprocess.run', return_value=SimpleNamespace(
            returncode=2, stdout=json.dumps({'error_code':'exchange_listing_unavailable','diagnostic':'Shenzhen: HTTPError(HTTP 502)'}).encode())):
            with self.assertRaisesRegex(scanner.IncompleteListingError, 'Shenzhen.*502'):
                scanner.load_daily_scan_pool('main_board')

    def test_readiness_requires_a_current_complete_listing_even_with_ready_klines(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            stack.enter_context(patch.object(dashboard, 'CRON_OUTPUT_DIR', root))
            stack.enter_context(patch.object(dashboard, 'kline_cache_readiness', return_value={'ready':True}))
            stack.enter_context(patch.object(dashboard, '_runtime_storage_status', return_value={'writable':True,'containerized':False}))
            at = datetime(2026,11,12,8)
            result = dashboard.market_data_readiness(at)
            self.assertFalse(result['ready'])
            self.assertIn('exchange_listing_unavailable', result['blockers'])
            rows = [[f'{prefix}{i:03d}', '沪市'] for prefix in ('600','601','603','605','688') for i in range(1000)] + [['000001','深市'],['300001','创业']]
            path = root/'a_share_listing_latest.json'
            path.write_text(json.dumps({'schema_version':2,'date':self.day,'rows':rows}),encoding='utf-8')
            self.assertTrue(dashboard.market_data_readiness(at)['ready'])
            self.assertFalse(catalog_readiness(path,'2026-11-13')['ready'])

    def test_report_worker_timeout_kills_the_child_before_fallback(self):
        with patch.object(provider_worker.subprocess, 'run', side_effect=subprocess.TimeoutExpired('worker',10)):
            with self.assertRaisesRegex(TimeoutError, 'stock_zh_a_spot.*exceeded'):
                provider_worker.fetch_frame('stock_zh_a_spot')

    def test_completed_warming_still_retries_when_the_catalog_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            stack.enter_context(patch.object(dashboard,'CRON_OUTPUT_DIR',Path(tmp)))
            stack.enter_context(patch.object(dashboard,'CRON_STATE_DIR',Path(tmp)/'state'))
            stack.enter_context(patch.object(dashboard,'prewarm_completed_for_date',return_value=True))
            stack.enter_context(patch.object(dashboard,'kline_cache_readiness',return_value={}))
            runner = unittest.mock.Mock(return_value=SimpleNamespace(returncode=0,stderr=''))
            result = dashboard.run_kline_prewarm(self.day,runner=runner)
            runner.assert_called_once()
            self.assertTrue(result['updated'])

    def test_report_worker_roundtrips_frame_and_date_arguments(self):
        response = SimpleNamespace(returncode=0,stdout=json.dumps({'columns':['代码','金额'],'data':[['920001',1.25],['000001',None]]}))
        with patch.object(provider_worker.subprocess,'run',return_value=response) as call:
            frame = provider_worker.fetch_frame('stock_zt_pool_em',date='20261112')
        self.assertEqual(frame['代码'].tolist(),['920001','000001'])
        self.assertEqual(frame.iloc[0]['金额'],1.25)
        self.assertEqual(json.loads(call.call_args.kwargs['input'])['kwargs'], {'date':'20261112'})
        self.assertLessEqual(call.call_args.kwargs['timeout'],60)

    def test_worker_serialization_preserves_full_float_precision_and_missing_values(self):
        import pandas as pd
        frame = pd.DataFrame({'value':[10.123456789012345,float('nan')]})
        output = StringIO()
        provider = SimpleNamespace(stock_zh_a_spot_em=lambda:frame)
        with patch.dict(sys.modules,{'akshare':provider}), patch.object(sys,'argv',['worker','stock_zh_a_spot_em']), patch.object(sys,'stdin',StringIO('{"args":[],"kwargs":{}}')), patch.object(sys,'stdout',output):
            provider_worker.main()
        values = json.loads(output.getvalue())['data']
        self.assertEqual(values[0][0],frame.iloc[0]['value'])
        self.assertIsNone(values[1][0])

    def test_tencent_report_includes_bse_st_and_new_listings(self):
        symbols = set(tencent_spot._symbols())
        for symbol in ('bj920001','bj920799','bj920999'):
            self.assertIn(symbol,symbols)
        self.assertEqual(sum(s.startswith('bj') for s in symbols),1000)
        self.assertNotIn('bj830799',symbols)
        for code,name in [('920001','北交'),('600001','*ST测试'),('300001','N新股')]:
            self.assertTrue(tencent_spot._is_full_market_a_share(code,name))

    def test_tencent_partial_chunks_cannot_be_presented_as_complete(self):
        fields = [''] * 38
        for index,value in {1:'测试',2:'600001',3:'10',4:'9',30:'20261111150000',32:'1',37:'100'}.items():fields[index]=value
        class Response(BytesIO):
            def read(self, *args):return super().read(*args)
        def open_url(request, **kwargs):
            if request.full_url.endswith('sh600051'):raise TimeoutError()
            return Response(('v_sh600001="'+'~'.join(fields)+'";').encode('gb18030'))
        with tempfile.TemporaryDirectory() as tmp, patch.object(tencent_spot,'_symbols',return_value=['sh600001','sh600002']), patch.object(tencent_spot,'urlopen',side_effect=open_url), patch.dict('os.environ',{'RECOVERY_CHUNK':'50'}):
            # One failed batch must invalidate even a snapshot above min_rows.
            with patch.object(tencent_spot,'_symbols',return_value=[]):
                rows,issue = tencent_spot.fetch_tencent_spot_snapshot(Path(tmp),env_prefix='RECOVERY',min_rows=1,listed_codes={f'{600000+i:06d}' for i in range(1,52)})
        self.assertEqual(rows,[])
        self.assertIn('部分请求失败',issue)

    def test_queued_batches_expiring_before_start_are_recorded_as_failures(self):
        stamps=iter([0,6])
        with tempfile.TemporaryDirectory() as tmp, patch.object(tencent_spot,'_symbols',return_value=['sh600001']), patch.object(tencent_spot.time,'monotonic',side_effect=lambda:next(stamps,6)), patch.dict('os.environ',{'QUEUE_DEADLINE':'5'}), patch.object(tencent_spot,'urlopen') as request:
            rows,issue=tencent_spot.fetch_tencent_spot_snapshot(Path(tmp),env_prefix='QUEUE',min_rows=1,listed_codes={'600001'})
        request.assert_not_called()
        self.assertEqual(rows,[])
        self.assertIn('部分请求失败',issue)

    def test_tencent_helper_imports_through_the_canonical_package(self):
        script = "import sys; from pathlib import Path; root=Path.cwd(); excluded={str(root/'app'),str(root/'app/compat')}; sys.path=[p for p in sys.path if p not in excluded]; import app.reports.a_share.tencent_spot"
        result=subprocess.run([sys.executable,'-c',script],cwd=ROOT,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_corrupt_fragment_cache_is_rebuilt_from_verified_exchange_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'parts.json'
            path.write_bytes(b'\xff')
            rows=listing.fetch_exchange_listing({self.day},get=self.get,cache_path=path,cache_day=self.day)
            self.assertEqual(len(rows),4)
            self.assertEqual(json.loads(path.read_text())['date'],self.day)

    def test_corrupt_daily_catalog_is_refreshed_without_reusing_its_contents(self):
        rows=[[f'{prefix}{i:03d}','测试'] for prefix in ('600','601','603','605','688') for i in range(1000)]+[['000001','深市'],['300001','创业']]
        with tempfile.TemporaryDirectory() as tmp, patch.object(scanner,'B1_OUTPUT_DIR',Path(tmp)), patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps({'schema_version':2,'date':datetime.now().strftime('%Y-%m-%d'),'rows':rows}).encode())):
            (Path(tmp)/'a_share_listing_latest.json').write_bytes(b'\xff')
            self.assertEqual(scanner.load_daily_scan_pool('chi_next'),[('300001','创业')])

    def test_report_quotes_are_intersected_with_the_current_exchange_listing(self):
        from app.market_data.listing_cache import validate_catalog
        fields=['']*38
        for index,value in {1:'已退市别名',2:'600002',3:'10',4:'10',30:'20261111090000',32:'0',37:'0'}.items():fields[index]=value
        class Response(BytesIO):
            pass
        with tempfile.TemporaryDirectory() as tmp, patch.object(tencent_spot,'_symbols',return_value=['sh600001']), patch.object(tencent_spot,'urlopen',return_value=Response(('v_sh600002="'+'~'.join(fields)+'";').encode('gb18030'))):
            rows,issue=tencent_spot.fetch_tencent_spot_snapshot(Path(tmp),min_rows=1,listed_codes={'600001'})
        self.assertEqual(rows,[])
        self.assertIn('低于完整性下限',issue)
        catalog={'schema_version':1,'date':self.day,'rows':[['600001','沪市'],['688001','科创'],['000001','深市'],['300001','创业']]}
        self.assertIsNone(validate_catalog(catalog,self.day,minimum_rows=1))

    def test_report_requests_the_actual_exchange_code_beyond_legacy_ranges(self):
        fields=['']*38
        for index,value in {1:'中航成飞',2:'302132',3:'11',4:'10',30:'20261111150000',32:'10',37:'100'}.items():fields[index]=value
        requests=[]
        def opened(request,**kwargs):
            requests.append(request.full_url)
            return BytesIO(('v_sz302132="'+'~'.join(fields)+'";').encode('gb18030'))
        with tempfile.TemporaryDirectory() as tmp, patch.object(tencent_spot,'_symbols',return_value=[]), patch.object(tencent_spot,'urlopen',side_effect=opened):
            rows,issue=tencent_spot.fetch_tencent_spot_snapshot(Path(tmp),min_rows=1,listed_codes={'302132'})
        self.assertIsNone(issue)
        self.assertEqual([row['code'] for row in rows],['302132'])
        self.assertEqual(requests,['https://qt.gtimg.cn/q=sz302132'])


if __name__ == '__main__':
    unittest.main()
