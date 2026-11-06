"""Exchange row counts, trade dates and stock identities are all required."""
import io
import json
import unittest
from zipfile import ZipFile
from openpyxl import Workbook
from app.market_data.exchange_listing import fetch_exchange_listing, SH, SZ


class ExchangeListingTests(unittest.TestCase):
    def setUp(self):
        book = Workbook()
        sheet = book.active
        sheet.append(['A股代码', 'A股简称'])
        sheet.append([1, '深市主板'])
        sheet.append(['300001', '*ST测试'])
        output = io.BytesIO()
        book.save(output)
        book.close()
        self.workbook = output.getvalue()
        self.count = 2
        self.stamp = '2026-11-06'
        self.total = 1

    def fetch(self, url, params, deadline):
        if url == SZ:
            return self.workbook
        if url == SZ + '/data':
            return json.dumps([{'metadata': {'tabkey':'tab1','recordcount':self.count,'subname':self.stamp}}])
        self.assertEqual(url, SH)
        self.assertEqual(params['COMPANY_STATUS'], '2,4,5,7,8')
        code = '600001' if params['STOCK_TYPE']=='1' else '688001'
        return json.dumps({'pageHelp': {'total':self.total,'pageCount':1,'pageNo':1},
                           'result':[{'A_STOCK_CODE':code,'SEC_NAME_CN':'沪市'}]})

    def test_four_segments_keep_exact_codes_names_and_st_markers(self):
        rows = dict(fetch_exchange_listing({self.stamp}, get=self.fetch))
        self.assertEqual(rows, {'000001':'深市主板','300001':'*ST测试','600001':'沪市','688001':'沪市'})

    def test_incomplete_reports_never_replace_a_catalog(self):
        for attr, value, pattern in [('count',3,'Shenzhen'), ('total',2,'Shanghai'), ('stamp','2026-10-26','stale')]:
            with self.subTest(attribute=attr):
                original = getattr(self, attr)
                setattr(self, attr, value)
                with self.assertRaisesRegex(ValueError, pattern):
                    fetch_exchange_listing({'2026-11-06'}, get=self.fetch)
                setattr(self, attr, original)

    def test_incorrect_a1_dimension_in_exchange_export(self):
        fixed = io.BytesIO()
        with ZipFile(io.BytesIO(self.workbook)) as src, ZipFile(fixed, 'w') as dst:
            for name in src.namelist():
                raw = src.read(name)
                if name == 'xl/worksheets/sheet1.xml':
                    self.assertIn(b'<dimension ref="A1:B3"', raw)
                    raw = raw.replace(b'<dimension ref="A1:B3"', b'<dimension ref="A1"')
                dst.writestr(name, raw)
        self.workbook = fixed.getvalue()
        self.assertEqual(len(fetch_exchange_listing({self.stamp}, get=self.fetch)), 4)
