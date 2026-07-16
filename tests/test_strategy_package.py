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
from strategies.prompts import build_strategy_prompt_sections  # noqa: E402


class StrategyPackageTests(unittest.TestCase):
    def test_strategy_suite_prompts_do_not_include_inactive_suites(self):
        cases = {
            "base": ("基础策略：", ("Z哥评分基准", "李大霄")),
            "zettaranc": ("Z哥评分基准", ("基础策略：", "李大霄")),
            "li_daxiao_bottom": ("李大霄", ("Z哥评分基准", "基础策略：")),
            "sector_tide": ("板块潮汐（市场→行业→个股", ("Z哥评分基准", "基础策略：", "李大霄")),
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

        source_rows = [{"close": 10.0}]
        result = analyze_enriched_rows(source_rows, {"watch": watch_only, "action": actionable})

        self.assertEqual(result["best_strategy"], "action")
        self.assertEqual(result["consensus_count"], 2)
        self.assertEqual(result["consensus_boost"], 0.5)
        self.assertNotIn("private_annotation", source_rows[0])


if __name__ == "__main__":
    unittest.main()
