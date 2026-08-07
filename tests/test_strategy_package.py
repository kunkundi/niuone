#!/usr/bin/env python3
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
COMPAT = APP / "compat"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(COMPAT))

import multi_strategy_screen as screen  # noqa: E402
import niuniu_practice_trader as trader  # noqa: E402
import strategies  # noqa: E402
import strategy_registry as legacy_registry  # noqa: E402
from strategies import registry  # noqa: E402
from strategies.scoring import STRATEGY_SCORERS, analyze_enriched_rows  # noqa: E402
from strategies.prompts import build_position_exit_prompt_section, build_strategy_prompt_sections  # noqa: E402


class StrategyPackageTests(unittest.TestCase):
    def test_niuone_is_the_default_strategy_suite(self):
        names = (
            registry.ACTIVE_STRATEGY_ENV,
            registry.STRATEGY_SOURCE_ENV,
            registry.PERSONA_STRATEGY_ENV,
        )
        saved = {name: os.environ.get(name) for name in names}
        try:
            for name in names:
                os.environ.pop(name, None)

            self.assertEqual(registry.default_enabled_persona_strategies_value(), "niuone")
            self.assertEqual(registry.active_strategy_suite(), "niuone")
            self.assertEqual(
                registry.enabled_strategy_ids(),
                {"niu_leader", "niu_pullback", "niu_emerging", "niu_reversal_probe"},
            )
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_strategy_suite_prompts_do_not_include_inactive_suites(self):
        cases = {
            "base": ("基础策略：", ("Z哥评分基准", "李大霄")),
            "zettaranc": ("Z哥评分基准", ("基础策略：", "李大霄")),
            "li_daxiao_bottom": ("李大霄", ("Z哥评分基准", "基础策略：")),
            "sector_tide": ("板块潮汐（市场→行业→个股", ("Z哥评分基准", "基础策略：", "李大霄")),
            "niuone": ("牛牛战法（主线酝酿→主线主升→主线高潮→主线分歧→主线退幕", ("板块潮汐（市场→行业→个股", "Z哥评分基准", "基础策略：", "李大霄")),
        }
        for suite, (included, excluded) in cases.items():
            sections = build_strategy_prompt_sections(
                suite,
                "",
                registry.enabled_strategy_ids(strategy_suite_raw=suite),
                b3_exit_hhmm="09:37",
                time_exit_hhmm="14:45",
            )
            active = sections["active_strategy_section"]
            self.assertIn(included, active)
            for text in excluded:
                self.assertNotIn(text, active)

    def test_niuone_prompt_matches_execution_risk_budget_and_position_caps(self):
        active = build_strategy_prompt_sections(
            "niuone",
            "",
            registry.enabled_strategy_ids(strategy_suite_raw="niuone"),
            b3_exit_hhmm="09:37",
            time_exit_hhmm="14:45",
        )["active_strategy_section"]

        self.assertIn("30%是单票绝对上限", active)
        self.assertIn("25%是单票绝对上限", active)
        self.assertIn("15%是单票绝对上限", active)
        self.assertIn("牛牛试仓仅≤0.35%/0.30%/0.25%/0.15%", active)
        self.assertIn("左侧回落至少5个交易日且跌幅≥8%", active)
        self.assertIn(
            "右侧反弹≥6%、收复左侧跌幅须在60%（含）至200%（不含）之间",
            active,
        )
        self.assertIn("达到200%后不再按早期试仓", active)
        self.assertIn("上涨交易日占比至少2/3", active)
        self.assertIn("每天最多保留评分最高的2只", active)
        self.assertIn("至少有6只强势股", active)
        self.assertIn("酝酿状态已连续3个交易日", active)
        self.assertIn("单票绝对上限6.25%", active)
        self.assertIn("已确认主线阶段必须改走牛牛领涨", active)
        self.assertNotIn("已确认mainline", active)
        self.assertNotIn("mainline再加速", active)
        self.assertIn("止损距离不得超过6%/2ATR", active)
        self.assertIn("单笔权益风险≤1.50%/1.00%/0.60%/0.30%", active)
        self.assertIn("总仓≤70%/55%/35%/20%", active)
        self.assertIn("主题敞口≤55%/40%/25%/12%", active)
        self.assertIn("主题敞口≤12%/10%/8%/5%", active)
        self.assertIn("防守允许开仓，复合风险硬停止才禁止新仓", active)
        self.assertIn("策略同时最多持有5只", active)
        self.assertIn("strong_score前三且仍为强势股", active)
        self.assertIn("第一名涨停或无有效买点时可顺延", active)
        self.assertIn(
            "高潮仍允许满足领涨或企稳转强条件的核心股开仓，分歧只观察核心股调整后转强或减仓，持续回落不触发买点，退幕只退出",
            active,
        )
        self.assertIn("不设固定单日涨幅上限，实际执行价达到涨停才拒绝", active)
        self.assertIn("连续两个交易日跌出行业前三龙头梯队时换出", active)
        self.assertNotIn("单笔权益风险≤0.25%/0.18%/0.10%", active)
        self.assertNotIn("总仓≤40%/28%/15%", active)

    def test_niuone_custom_discipline_appends_current_execution_limits(self):
        saved = {
            trader.ACTIVE_STRATEGY_ENV: os.environ.get(trader.ACTIVE_STRATEGY_ENV),
            trader.STRATEGY_SOURCE_ENV: os.environ.get(trader.STRATEGY_SOURCE_ENV),
            trader.PERSONA_STRATEGY_ENV: os.environ.get(trader.PERSONA_STRATEGY_ENV),
            trader.TRADE_DISCIPLINE_TEXT_ENV: os.environ.get(trader.TRADE_DISCIPLINE_TEXT_ENV),
        }
        try:
            os.environ[trader.ACTIVE_STRATEGY_ENV] = "niuone"
            os.environ[trader.STRATEGY_SOURCE_ENV] = "builtin"
            os.environ[trader.PERSONA_STRATEGY_ENV] = "niuone"
            os.environ[trader.TRADE_DISCIPLINE_TEXT_ENV] = "自定义牛牛纪律"

            discipline = trader.current_trade_discipline_text("")
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.assertIn("单笔权益风险分别≤1.50%/1.00%/0.60%/0.30%", discipline)
        self.assertIn("总仓≤70%/55%/35%/20%", discipline)
        self.assertIn("仅市场复合硬停止禁止新仓", discipline)
        self.assertIn("领涨/转强/启动/试仓单票30%/25%/15%/6.25%", discipline)
        self.assertIn("同时最多持有5只", discipline)
        self.assertNotIn("单笔权益风险分别≤0.25%/0.18%/0.10%", discipline)

    def test_position_exit_prompt_uses_held_strategy_marks_not_active_suite(self):
        active = build_strategy_prompt_sections(
            "sector_tide",
            "",
            registry.enabled_strategy_ids(strategy_suite_raw="sector_tide"),
            b3_exit_hhmm="09:37",
            time_exit_hhmm="14:45",
        )["active_strategy_section"]
        exits = build_position_exit_prompt_section(
            {"b2_confirm"},
            b3_exit_hhmm="09:37",
            time_exit_hhmm="14:45",
        )

        self.assertIn("板块潮汐（市场→行业→个股", active)
        self.assertNotIn("Z哥", active)
        self.assertIn("Z哥历史持仓退出纪律", exits)
        self.assertIn("strategy_mark=B2确认", exits)
        self.assertNotIn("板块潮汐历史持仓退出纪律", exits)

        niuone_exits = build_position_exit_prompt_section(
            {"niu_leader"},
            b3_exit_hhmm="09:37",
            time_exit_hhmm="14:45",
        )
        self.assertIn("牛牛战法历史持仓退出纪律", niuone_exits)
        self.assertIn("strategy_mark=牛牛领涨", niuone_exits)
        self.assertIn("连续两个交易日跌出强势行业前三龙头梯队时换出", niuone_exits)

    def test_legacy_registry_is_a_compatibility_view(self):
        self.assertIs(legacy_registry.STRATEGY_DEFINITIONS, registry.STRATEGY_DEFINITIONS)
        self.assertIs(legacy_registry.STRATEGY_META, registry.STRATEGY_META)
        self.assertIs(legacy_registry.STRATEGY_SCORE_PROFILES, registry.STRATEGY_SCORE_PROFILES)
        self.assertIs(legacy_registry._ALIAS_TO_STRATEGY, registry._ALIAS_TO_STRATEGY)

    def test_registry_compatibility_imports_from_repo_root(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import app.compat.strategy_registry as r; assert 'b3_accelerate' in r.STRATEGY_DEFINITIONS",
            ],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=30,
        )

    def test_registry_compatibility_supports_direct_file_loading(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        code = f"""
import importlib.util
spec = importlib.util.spec_from_file_location('strategy_registry_file_compat', {str(APP / 'compat' / 'strategy_registry.py')!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert 'b3_accelerate' in module.STRATEGY_DEFINITIONS
"""
        subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=30,
        )

    def test_every_registered_strategy_has_a_scorer_in_registry_order(self):
        self.assertEqual(list(STRATEGY_SCORERS), list(registry.STRATEGY_DEFINITIONS))
        self.assertTrue(all(callable(scorer) for scorer in STRATEGY_SCORERS.values()))

    def test_legacy_scanner_and_trader_apis_point_into_strategy_package(self):
        self.assertIs(screen.STRATEGY_SCORERS, STRATEGY_SCORERS)
        self.assertTrue(screen.score_trend_pullback.__module__.startswith("strategies.scoring"))
        self.assertEqual(trader.classify_buy_strategy.__module__, "strategies.attribution")
        self.assertEqual(trader.track_strategy_performance.__module__, "strategies.performance")
        self.assertIs(strategies.select_trade_candidates, screen.select_trade_candidates)

    def test_trader_policy_adapter_matches_strategy_policy(self):
        candidate = {
            "best_score": 7.5,
            "entry_threshold": 8.0,
            "distance_pct": 7.0,
            "hard_blockers": [],
            "actionable": False,
        }
        self.assertEqual(
            trader.candidate_buy_blockers(candidate),
            strategies.candidate_buy_blockers(candidate, max_bbi_distance_pct=trader.COMMON_MAX_BBI_DISTANCE_PCT),
        )
        self.assertEqual(
            trader.strategy_position_limit_pct("b3_accelerate"),
            strategies.strategy_position_limit_pct("b3_accelerate", trader.MAX_SINGLE_POSITION_PCT),
        )

    def test_scoring_engine_isolates_rows_and_prefers_an_actionable_strategy(self):
        def watch_only(rows):
            rows[0]["private_annotation"] = True
            return {
                "score": 9.5,
                "entry_threshold": 10.0,
                "strategy_priority": 99,
                "decision_score": 9.5,
                "verdict": "观察",
            }

        def actionable(rows):
            self.assertNotIn("private_annotation", rows[0])
            return {
                "score": 8.0,
                "entry_threshold": 8.0,
                "strategy_priority": 10,
                "decision_score": 8.1,
                "verdict": "可执行",
            }

        def blocked_high_score(rows):
            return {
                "score": 9.8,
                "entry_threshold": 8.0,
                "strategy_priority": 100,
                "decision_score": 8.3,
                "verdict": "高分但被阶段门槛阻止",
                "actionable": False,
                "hard_blockers": ["等待下一阶段"],
            }

        source_rows = [{"close": 10.0}]
        result = analyze_enriched_rows(source_rows, {
            "watch": watch_only,
            "blocked": blocked_high_score,
            "action": actionable,
        })

        self.assertEqual(result["best_strategy"], "action")
        self.assertEqual(result["consensus_count"], 3)
        self.assertEqual(result["consensus_boost"], 1)
        self.assertNotIn("private_annotation", source_rows[0])


if __name__ == "__main__":
    unittest.main()
