#!/usr/bin/env python3
import concurrent.futures
import os
import sys
import time
import types
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
COMPAT = SRC / "compat"
ENTRYPOINTS = SRC / "entrypoints"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(COMPAT))

import multi_strategy_screen as screen  # noqa: E402


class MultiStrategyRuleTests(unittest.TestCase):
    def setUp(self):
        self._saved_strategy_source = os.environ.get(screen.STRATEGY_SOURCE_ENV)
        self._saved_active_strategy = os.environ.pop(screen.ACTIVE_STRATEGY_ENV, None)
        os.environ[screen.STRATEGY_SOURCE_ENV] = "builtin"

    def tearDown(self):
        if self._saved_strategy_source is None:
            os.environ.pop(screen.STRATEGY_SOURCE_ENV, None)
        else:
            os.environ[screen.STRATEGY_SOURCE_ENV] = self._saved_strategy_source
        if self._saved_active_strategy is None:
            os.environ.pop(screen.ACTIVE_STRATEGY_ENV, None)
        else:
            os.environ[screen.ACTIVE_STRATEGY_ENV] = self._saved_active_strategy

    def test_build_market_snapshot_reuses_full_quote_batch(self):
        snapshot = screen.build_market_snapshot({
            "sh600001": {"price": 11.0, "prev_close": 10.0, "change_pct": 10.0, "amount": 2e8, "quote_time": "20260710100001"},
            "sh600002": {"price": 9.0, "prev_close": 10.0, "change_pct": -10.0, "amount": 1e8, "quote_time": "20260710100002"},
            "sz000001": {"price": 10.1, "prev_close": 10.0, "change_pct": 1.0, "amount": 3e8, "quote_time": "20260710100003"},
            "sz000002": {"price": 10.0, "prev_close": 10.0, "change_pct": 0.0, "amount": 4e8, "quote_time": "20260710100004"},
        }, captured_at="2026-07-10 10:00:05", pool_count=5)

        self.assertEqual(snapshot["universe"], "mainboard_non_st")
        self.assertEqual(snapshot["sample_count"], 4)
        self.assertEqual(snapshot["pool_count"], 5)
        self.assertEqual(snapshot["coverage"], 0.8)
        self.assertEqual((snapshot["up"], snapshot["down"], snapshot["flat"]), (2, 1, 1))
        self.assertEqual((snapshot["limit_up"], snapshot["limit_down"]), (1, 1))
        self.assertEqual(snapshot["quote_time"], "2026-07-10 10:00:04")
        self.assertEqual(snapshot["total_amount"], 1e9)

    def test_sector_tide_loads_only_exact_previous_trading_day_archive(self):
        calls = {}

        def status_loader(value, *, allow_refresh=True):
            calls["status_value"] = value
            calls["allow_refresh"] = allow_refresh
            return {
                "previous_trading_day": "2026-07-16",
                "source": "test_calendar",
            }

        def archive_reader(path, *, trade_date):
            calls["archive_path"] = path
            calls["trade_date"] = trade_date
            return {
                "available": True,
                "archive": True,
                "source": "同花顺问财",
                "date": trade_date,
                "items": [{"code": "600000.SH"}],
            }

        archive_dir = Path("/tmp/niuone-sector-tide-dragon-tiger")
        payload = screen.load_previous_sector_tide_dragon_tiger(
            datetime(2026, 7, 17, 10, 0, 0),
            archive_dir=archive_dir,
            status_loader=status_loader,
            archive_reader=archive_reader,
        )

        self.assertFalse(calls["allow_refresh"])
        self.assertEqual(calls["trade_date"], "2026-07-16")
        self.assertEqual(calls["archive_path"], archive_dir)
        self.assertEqual(payload["date"], "2026-07-16")
        self.assertEqual(payload["requested_date"], "2026-07-16")
        self.assertEqual(payload["calendar_source"], "test_calendar")

    def test_sector_tide_missing_previous_archive_degrades_without_latest_fallback(self):
        requested = []

        payload = screen.load_previous_sector_tide_dragon_tiger(
            datetime(2026, 7, 17, 10, 0, 0),
            archive_dir=Path("/tmp/niuone-sector-tide-dragon-tiger"),
            status_loader=lambda _value, **_kwargs: {
                "previous_trading_day": "2026-07-16",
                "source": "test_calendar",
            },
            archive_reader=lambda _path, *, trade_date: requested.append(trade_date),
        )

        self.assertEqual(requested, ["2026-07-16"])
        self.assertFalse(payload["available"])
        self.assertEqual(payload["error"], "archive_missing")
        self.assertEqual(payload["items"], [])

    def test_stock_universe_classifies_boards_and_st_as_additive_scopes(self):
        self.assertEqual(
            screen.normalize_stock_universe("main_board,ST,chi_next"),
            "st,chi_next,main_board",
        )
        self.assertTrue(screen.stock_in_universe("600000", "浦发银行", "main_board"))
        self.assertFalse(screen.stock_in_universe("300001", "特锐德", "main_board"))
        self.assertTrue(screen.stock_in_universe("300001", "特锐德", "chi_next"))
        self.assertTrue(screen.stock_in_universe("688001", "华兴源创", "star_market"))
        self.assertFalse(screen.stock_in_universe("600001", "ST测试", "main_board"))
        self.assertTrue(screen.stock_in_universe("600001", "ST测试", "st"))
        self.assertTrue(screen.stock_in_universe("300002", "*ST测试", "st"))
        with self.assertRaises(ValueError):
            screen.normalize_stock_universe("")
        with self.assertRaises(ValueError):
            screen.normalize_stock_universe("beijing")

    def test_market_snapshot_records_non_default_stock_universe(self):
        snapshot = screen.build_market_snapshot(
            {},
            stock_universe="st,chi_next,star_market,main_board",
        )

        self.assertEqual(snapshot["universe"], "configured_a_share")
        self.assertEqual(snapshot["stock_universe"], ["st", "chi_next", "star_market", "main_board"])
        self.assertEqual(snapshot["stock_universe_label"], "ST、创业板、科创板、主板")

    def test_code_pool_applies_configured_boards_and_st_scope(self):
        class FakeFrame:
            def __init__(self, rows):
                self.rows = rows

            def iterrows(self):
                return enumerate(self.rows)

        sh_calls = []
        fake_akshare = types.SimpleNamespace(
            stock_info_sh_name_code=lambda symbol: (
                sh_calls.append(symbol)
                or FakeFrame(
                    [{"证券代码": "600001", "证券简称": "主板测试"}]
                    if symbol == "主板A股"
                    else [{"证券代码": "688001", "证券简称": "科创测试"}]
                )
            ),
            stock_info_sz_name_code=lambda symbol: FakeFrame([
                {"A股代码": "000001", "A股简称": "深主板"},
                {"A股代码": "300001", "A股简称": "创业测试"},
                {"A股代码": "300002", "A股简称": "*ST创业"},
                {"A股代码": "920001", "A股简称": "北交测试"},
            ]),
            stock_info_a_code_name=lambda: FakeFrame([]),
        )
        original = sys.modules.get("akshare")
        sys.modules["akshare"] = fake_akshare
        try:
            pool = screen.load_a_share_code_pool("st,star_market,main_board")
        finally:
            if original is None:
                sys.modules.pop("akshare", None)
            else:
                sys.modules["akshare"] = original

        self.assertEqual(sh_calls, ["主板A股", "科创板"])
        self.assertEqual(pool, [
            ("000001", "深主板"),
            ("300002", "*ST创业"),
            ("600001", "主板测试"),
            ("688001", "科创测试"),
        ])

    def test_build_index_risk_snapshot_counts_core_indices_below_ma20(self):
        quotes = {
            "sh000001": {"price": 9.8, "change_pct": -1.2},
            "sz399001": {"price": 9.7, "change_pct": -1.5},
            "sz399006": {"price": 10.2, "change_pct": -0.2},
        }
        rows = [{"close": 10.0} for _ in range(21)]

        snapshot = screen.build_index_risk_snapshot(quotes, kline_loader=lambda symbol, count: rows)

        self.assertEqual(snapshot["core_index_count"], 3)
        self.assertEqual(snapshot["index_below_ma20_count"], 2)
        self.assertAlmostEqual(snapshot["index_average_change_pct"], -0.967, places=3)

    def test_recent_b1_indices_require_core_negative_j(self):
        rows = [{"j": None, "open": 10.0, "close": 10.0} for _ in range(10)]
        rows[4]["j"] = -9.5
        rows[6]["j"] = -10.5

        self.assertEqual(screen.recent_b1_indices(rows, lookback=9, end_offset=1), [6])

    def test_b2_confirmation_rejects_b1_older_than_three_days(self):
        rows = [
            {"open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9, "volume": 100, "j": 20.0, "bbi": 10.0, "change_pct": 0.0}
            for _ in range(40)
        ]
        rows[35]["j"] = -12.0
        rows[-1].update({"open": 10.0, "close": 10.5, "high": 10.6, "low": 9.95, "volume": 150, "j": 40.0, "bbi": 10.0, "change_pct": 5.0})

        self.assertIsNone(screen.score_b2_confirm(rows))

        rows[35]["j"] = 20.0
        rows[36]["j"] = -12.0
        result = screen.score_b2_confirm(rows)
        self.assertIsNotNone(result)
        self.assertEqual(result["days_from_b1"], 3)

    def test_n_structure_filter_uses_local_swing_lows(self):
        rising = [{"low": low} for low in [10.4, 10.0, 9.5, 9.8, 10.5, 10.2, 10.0, 10.3, 10.8]]
        falling = [{"low": low} for low in [10.4, 10.0, 9.5, 9.8, 10.5, 9.4, 9.2, 9.5, 10.0]]

        self.assertTrue(screen.n_structure_ok(rising, lookback=20))
        self.assertFalse(screen.n_structure_ok(falling, lookback=20))

    def test_shaofu_b1_above_core_j_is_watch_only(self):
        payload = screen.with_strategy_profile("shaofu_b1", {
            "score": 9.0,
            "distance_pct": 1.0,
            "current_j": -5.0,
            "vol_shrink": True,
            "pullback_shrink": True,
            "n_structure": True,
            "bull_rope": True,
            "stop_space_pct": 4.0,
            "pressure_space_pct": 8.0,
            "risk_flags": [],
        })

        self.assertFalse(payload["actionable"])
        self.assertIn("B1核心J未≤-10", payload["hard_blockers"])

    def test_select_trade_candidates_excludes_hard_blocked_items(self):
        good = {
            "code": "600001",
            "best_score": 9.0,
            "entry_threshold": 8.0,
            "distance_pct": 1.0,
            "actionable": True,
            "hard_blockers": [],
        }
        blocked = {
            "code": "600002",
            "best_score": 9.5,
            "entry_threshold": 8.0,
            "distance_pct": 1.0,
            "actionable": False,
            "hard_blockers": ["B1核心J未≤-10"],
        }

        self.assertEqual(screen.select_trade_candidates([blocked, good]), [good])

    def test_candidate_counts_follow_runtime_settings(self):
        candidates = [
            {
                "code": f"600{i:03d}",
                "best_score": 10.0 - i / 100,
                "entry_threshold": 8.0,
                "distance_pct": 1.0,
                "actionable": True,
                "hard_blockers": [],
                "best_strategy": "shaofu_b1",
            }
            for i in range(20)
        ]
        display_name = "DASHBOARD_DISPLAY_CANDIDATE_LIMIT"
        trade_name = "DASHBOARD_TRADE_CANDIDATE_LIMIT"
        saved_display = os.environ.get(display_name)
        saved_trade = os.environ.get(trade_name)
        try:
            os.environ[display_name] = "12"
            os.environ[trade_name] = "5"
            self.assertEqual(len(screen.select_display_candidates(candidates)), 12)
            self.assertEqual(len(screen.select_trade_candidates(candidates)), 5)
            self.assertEqual(len(screen.select_display_candidates(candidates, limit=7)), 7)
            self.assertEqual(len(screen.select_trade_candidates(candidates, limit=3)), 3)
        finally:
            if saved_display is None:
                os.environ.pop(display_name, None)
            else:
                os.environ[display_name] = saved_display
            if saved_trade is None:
                os.environ.pop(trade_name, None)
            else:
                os.environ[trade_name] = saved_trade

    def test_market_enrichment_reuses_market_wide_downloads_across_workers(self):
        import pandas as pd

        margin_calls = []
        block_calls = []
        margin_frame = pd.DataFrame([
            {
                '标的证券代码': '600001',
                '融资买入额': 4_000_000,
                '融资偿还额': 1_000_000,
                '融资余额': 100_000_000,
            },
            {
                '标的证券代码': '600002',
                '融资买入额': 2_000_000,
                '融资偿还额': 1_000_000,
                '融资余额': 100_000_000,
            },
        ])
        block_frame = pd.DataFrame([
            {'证券代码': '600001', '成交额': 1_000_000, '折溢率': 2.5},
            {'证券代码': '600002', '成交额': 2_000_000, '折溢率': -1.0},
        ])

        def load_margin(date):
            time.sleep(0.03)
            margin_calls.append(date)
            return margin_frame

        def load_block(**kwargs):
            time.sleep(0.03)
            block_calls.append(kwargs)
            return block_frame

        fake_akshare = types.SimpleNamespace(
            stock_margin_detail_sse=load_margin,
            stock_margin_detail_szse=lambda date: margin_calls.append(date) or margin_frame,
            stock_dzjy_mrmx=load_block,
        )
        original_akshare = sys.modules.get('akshare')
        screen._MARGIN_DETAIL_CACHE.clear()
        screen._BLOCK_TRADE_CACHE.clear()
        sys.modules['akshare'] = fake_akshare
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                margin_results = list(pool.map(screen.get_margin_signal, ['600001', '600002']))
                block_results = list(
                    pool.map(screen.get_block_trade_signal, ['600001', '600002'])
                )
        finally:
            screen._MARGIN_DETAIL_CACHE.clear()
            screen._BLOCK_TRADE_CACHE.clear()
            if original_akshare is None:
                sys.modules.pop('akshare', None)
            else:
                sys.modules['akshare'] = original_akshare

        self.assertTrue(all(result is not None for result in margin_results))
        self.assertTrue(all(result is not None for result in block_results))
        self.assertEqual(len(margin_calls), 1)
        self.assertEqual(len(block_calls), 1)

    def test_extract_industry_from_individual_info_rows(self):
        rows = [
            {"item": "股票简称", "value": "测试股份"},
            {"item": "行业", "value": "半导体行业"},
        ]

        self.assertEqual(screen.extract_industry_from_individual_info(rows), "半导体")
        self.assertEqual(
            screen.extract_industry_from_individual_info([{"所属板块": "消费电子板块"}]),
            "消费电子",
        )

    def test_extract_industry_from_cninfo_prefers_sw_short_name(self):
        rows = [
            {"分类标准": "中证行业分类标准", "行业中类": "游戏", "变更日期": "2021-12-17"},
            {"分类标准": "申银万国行业分类标准", "行业中类": "游戏Ⅲ", "变更日期": "2021-07-30"},
        ]

        self.assertEqual(screen.extract_industry_from_cninfo_change(rows), "游戏")

    def test_annotate_candidate_industries_adds_sector_alias_once(self):
        display = [{"code": "600001", "name": "测试A"}]
        trade = [{"code": "600001", "name": "测试A"}]
        calls: list[str] = []

        def fake_lookup(code: str) -> str:
            calls.append(code)
            return "银行板块"

        screen.annotate_candidate_industries(display, trade, lookup=fake_lookup)

        self.assertEqual(calls, ["600001"])
        self.assertEqual(display[0]["industry"], "银行")
        self.assertEqual(display[0]["sector"], "银行")
        self.assertEqual(trade[0]["industry"], "银行")
        self.assertEqual(trade[0]["sector"], "银行")

    def test_persona_strategies_are_registered(self):
        old = os.environ.get(screen.PERSONA_STRATEGY_ENV)
        try:
            os.environ.pop(screen.PERSONA_STRATEGY_ENV, None)
            self.assertIn("li_daxiao_bottom", screen.STRATEGY_META)
            self.assertNotIn("buffett_value", screen.STRATEGY_META)
            self.assertIn("li_daxiao_bottom", screen.STRATEGY_SCORERS)
            self.assertNotIn("buffett_value", screen.STRATEGY_SCORERS)
            self.assertEqual(screen.STRATEGY_META["shaofu_b1"]["family"], "persona")
            self.assertEqual(screen.STRATEGY_META["breakout"]["family"], "local")
            self.assertEqual(screen.enabled_persona_strategy_ids(), {"zettaranc"})
        finally:
            if old is None:
                os.environ.pop(screen.PERSONA_STRATEGY_ENV, None)
            else:
                os.environ[screen.PERSONA_STRATEGY_ENV] = old

    def test_active_strategy_scorers_follow_suite_setting(self):
        old = os.environ.get(screen.ACTIVE_STRATEGY_ENV)
        try:
            os.environ[screen.ACTIVE_STRATEGY_ENV] = "base"
            active = screen.active_strategy_scorers()
            self.assertNotIn("buffett_value", active)
            self.assertNotIn("li_daxiao_bottom", active)
            self.assertNotIn("shaofu_b1", active)
            self.assertIn("trend_pullback", active)
            self.assertIn("breakout", active)

            os.environ[screen.ACTIVE_STRATEGY_ENV] = "zettaranc"
            active = screen.active_strategy_scorers()
            self.assertNotIn("buffett_value", active)
            self.assertNotIn("li_daxiao_bottom", active)
            self.assertNotIn("trend_pullback", active)
            self.assertNotIn("breakout", active)
            self.assertIn("shaofu_b1", active)
            self.assertIn("b3_accelerate", active)

            os.environ[screen.ACTIVE_STRATEGY_ENV] = "li_daxiao_bottom"
            active = screen.active_strategy_scorers()
            self.assertIn("li_daxiao_bottom", active)
            self.assertNotIn("shaofu_b1", active)
            self.assertNotIn("b3_accelerate", active)
            self.assertNotIn("trend_pullback", active)
            self.assertNotIn("breakout", active)

            os.environ[screen.ACTIVE_STRATEGY_ENV] = "base"
            active = screen.active_strategy_scorers()
            self.assertIn("trend_pullback", active)
            self.assertIn("breakout", active)
            self.assertNotIn("li_daxiao_bottom", active)
            self.assertNotIn("shaofu_b1", active)

        finally:
            if old is None:
                os.environ.pop(screen.ACTIVE_STRATEGY_ENV, None)
            else:
                os.environ[screen.ACTIVE_STRATEGY_ENV] = old

    def test_preset_text_suite_uses_only_neutral_base_scorers(self):
        old = os.environ.get(screen.ACTIVE_STRATEGY_ENV)
        try:
            os.environ[screen.ACTIVE_STRATEGY_ENV] = "preset_text"
            active = screen.active_strategy_scorers()

            self.assertNotIn("li_daxiao_bottom", active)
            self.assertNotIn("shaofu_b1", active)
            self.assertIn("trend_pullback", active)
            self.assertIn("breakout", active)
        finally:
            if old is None:
                os.environ.pop(screen.ACTIVE_STRATEGY_ENV, None)
            else:
                os.environ[screen.ACTIVE_STRATEGY_ENV] = old

    def test_active_strategy_suites_are_isolated(self):
        old = os.environ.get(screen.ACTIVE_STRATEGY_ENV)
        try:
            expected = {
                "base": {"breakout", "trend_pullback"},
                "zettaranc": {"b3_accelerate", "b2_confirm", "shaofu_b1", "super_b1"},
                "li_daxiao_bottom": {"li_daxiao_bottom"},
                "preset_text": {"breakout", "trend_pullback"},
            }
            for suite, scorer_ids in expected.items():
                os.environ[screen.ACTIVE_STRATEGY_ENV] = suite
                self.assertEqual(set(screen.active_strategy_scorers()), scorer_ids)
        finally:
            if old is None:
                os.environ.pop(screen.ACTIVE_STRATEGY_ENV, None)
            else:
                os.environ[screen.ACTIVE_STRATEGY_ENV] = old

    def test_li_daxiao_profile_applies_hard_blockers(self):
        payload = screen.with_strategy_profile("li_daxiao_bottom", {
            "score": 9.0,
            "distance_pct": 1.0,
            "bottom_zone": False,
            "stabilizing": False,
            "bluechip_liquidity_proxy": True,
            "value_anchor_proxy": True,
            "anti_black_five_proxy": True,
            "not_fresh_listing_proxy": True,
            "no_chase_zone": True,
            "speculation_heat": False,
            "breakdown_risk": False,
            "volatility_20d_pct": 2.0,
            "risk_flags": [],
        })

        self.assertFalse(payload["actionable"])
        self.assertIn("未处低位区", payload["hard_blockers"])
        self.assertIn("底部未企稳", payload["hard_blockers"])

    def test_li_daxiao_profile_blocks_speculative_chasing(self):
        payload = screen.with_strategy_profile("li_daxiao_bottom", {
            "score": 9.0,
            "distance_pct": 3.2,
            "bottom_zone": True,
            "stabilizing": True,
            "bluechip_liquidity_proxy": True,
            "value_anchor_proxy": False,
            "anti_black_five_proxy": False,
            "not_fresh_listing_proxy": False,
            "no_chase_zone": False,
            "speculation_heat": True,
            "breakdown_risk": False,
            "volatility_20d_pct": 2.0,
            "risk_flags": [],
        })

        self.assertFalse(payload["actionable"])
        self.assertIn("低估蓝筹代理不足", payload["hard_blockers"])
        self.assertIn("黑五类/题材热度代理偏高", payload["hard_blockers"])
        self.assertIn("次新代理风险", payload["hard_blockers"])
        self.assertIn("李大霄不追高", payload["hard_blockers"])
        self.assertIn("换手/涨幅过热", payload["hard_blockers"])


if __name__ == "__main__":
    unittest.main()
