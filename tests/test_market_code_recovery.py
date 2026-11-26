"""Active 302-coded ChiNext stocks share the established board rules."""
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'app/compat')]
from app.screening.stock_universe import stock_board, stock_in_universe
from app.market_data.tencent_market_breadth import _symbols
from app.backtesting.selection import HistoricalBar, a_share_price_limits
from app.trading.niuone_forward_service import PROTOCOL_SOURCE_PATHS
import niuniu_practice_trader as trader
import a_share_auction_summary as auction


class MarketCodeRecoveryTests(unittest.TestCase):
    def test_302_is_classified_and_selected_as_chinext(self):
        self.assertEqual(stock_board('302132'),'chi_next')
        self.assertTrue(stock_in_universe('302132','中航成飞','chi_next'))
        self.assertFalse(stock_in_universe('302132','中航成飞','main_board'))
        self.assertFalse(stock_in_universe('302132','*ST测试','chi_next'))
        self.assertTrue(stock_in_universe('302132','*ST测试','st'))

    def test_market_breadth_requests_302_quotes(self):
        self.assertIn('sz302132',_symbols())

    def test_auction_fallback_requests_302_quotes(self):
        requests=[]
        def opened(request,**kwargs):
            requests.append(request.full_url)
            return BytesIO(b'')
        with tempfile.TemporaryDirectory() as tmp, patch.object(auction,'DASHBOARD_HOME',Path(tmp)), patch.object(auction,'urlopen',side_effect=opened):
            auction.fetch_tencent_auction_snapshot()
        self.assertTrue(any('sz302132' in url for url in requests))

    def test_live_buy_limit_check_uses_the_chinext_limit(self):
        self.assertFalse(trader.quote_is_at_limit_up('302132','中航成飞',{'price':11.5,'prev_close':10}))
        self.assertTrue(trader.quote_is_at_limit_up('302132','中航成飞',{'price':12,'prev_close':10}))

    def test_backtest_limit_matches_the_same_board(self):
        bar=HistoricalBar(symbol='302132',date='2026-11-12',open=10,high=10,low=10,close=10,volume=1)
        self.assertEqual(a_share_price_limits(bar,10),(12.,8.))

    def test_stock_classification_is_part_of_frozen_forward_evidence(self):
        self.assertIn('app/screening/stock_universe.py',PROTOCOL_SOURCE_PATHS)


if __name__=='__main__':unittest.main()
