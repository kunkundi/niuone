#!/usr/bin/env python3
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.backtesting.selection import (
    HistoricalBar,
    SelectionReplayFrame,
    SelectionReplayTape,
    SelectionSignal,
)
from scripts import research_niuone_walk_forward as research


class NiuOneWalkForwardResearchTests(unittest.TestCase):
    def test_zero_historical_coverage_fails_before_replay(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "eastmoney.*zero usable series.*4997.*before replay",
        ):
            research._require_historical_coverage(
                SimpleNamespace(series={}),
                reference_count=4997,
                source="eastmoney",
            )

        research._require_historical_coverage(
            SimpleNamespace(series={"sh600000": object()}),
            reference_count=4997,
            source="tencent",
        )

    def test_round62_cap_sensitivity_is_preregistered(self):
        candidates = {
            name: candidate
            for name, candidate in research.CANDIDATES.items()
            if candidate.get("research_status")
            == "round62_user_requested_cap_sensitivity"
        }

        self.assertEqual(
            set(candidates),
            {
                "round62_brewing_cap_7_5_2_signal",
                "round62_brewing_cap_8_75_2_signal",
                "round62_brewing_cap_10_0_2_signal",
                "round62_brewing_cap_15_0_2_signal",
                "round62_brewing_cap_20_0_2_signal",
                "round62_brewing_cap_30_0_2_signal",
            },
        )
        self.assertEqual(
            {
                candidate["policy_options"][
                    "reversal_entry_position_cap_pct"
                ]
                for candidate in candidates.values()
            },
            {7.5, 8.75, 10.0, 15.0, 20.0, 30.0},
        )
        self.assertTrue(all(
            candidate["reversal_signals_per_session"] == 2
            for candidate in candidates.values()
        ))

    def test_round63_dynamic_lifecycle_sizing_is_preregistered(self):
        candidates = {
            name: candidate
            for name, candidate in research.CANDIDATES.items()
            if candidate.get("research_status")
            == "round63_dynamic_lifecycle_sizing"
        }

        self.assertEqual(len(candidates), 24)
        self.assertEqual(
            {
                candidate["holding_upgrade_mode"]
                for candidate in candidates.values()
            },
            {"confirmed_mainline", "strong_leader_then_mainline"},
        )
        self.assertEqual(
            {
                candidate["holding_upgrade_min_pnl_pct"]
                for candidate in candidates.values()
            },
            {0.0, 2.0, 5.0},
        )
        self.assertEqual(
            {
                candidate["policy_options"][
                    "holding_upgrade_position_cap_pct"
                ]
                for candidate in candidates.values()
            },
            {15.0, 20.0, 25.0, 30.0},
        )

    def test_round63_mainline_scale_reduce_is_preregistered(self):
        candidates = {
            name: candidate
            for name, candidate in research.CANDIDATES.items()
            if candidate.get("research_status")
            == "round63_mainline_scale_reduce"
        }

        self.assertEqual(len(candidates), 10)
        self.assertTrue(all(
            candidate["holding_upgrade_mode"] == "confirmed_mainline"
            for candidate in candidates.values()
        ))
        self.assertEqual(
            {
                candidate["policy_options"][
                    "holding_upgrade_position_cap_pct"
                ]
                for candidate in candidates.values()
            },
            {15.0, 20.0},
        )
        self.assertEqual(
            {
                candidate["filter_options"]["reduction_policy"]
                for candidate in candidates.values()
            },
            {
                "climax_25",
                "climax_33",
                "climax_50",
                "fade",
                "climax_33_fade",
            },
        )

    def test_round64_markup_only_scale_reduce_is_preregistered(self):
        candidates = {
            name: candidate
            for name, candidate in research.CANDIDATES.items()
            if candidate.get("research_status")
            == "round64_markup_only_scale_reduce"
        }

        self.assertEqual(len(candidates), 6)
        self.assertTrue(all(
            candidate["holding_upgrade_mode"] == "confirmed_markup"
            and candidate["holding_upgrade_min_pnl_pct"] == 2.0
            and candidate["filter_options"]["required_lifecycle_stage"]
            == "markup"
            for candidate in candidates.values()
        ))
        self.assertEqual(
            {
                candidate["policy_options"][
                    "holding_upgrade_position_cap_pct"
                ]
                for candidate in candidates.values()
            },
            {15.0, 20.0, 30.0},
        )

    def test_round64_production_v19_policy_is_frozen(self):
        candidate = research.CANDIDATES[
            "production_v19_markup_scale_climax_reduce"
        ]

        self.assertEqual(
            candidate["research_status"],
            "promoted_round64_production_v19",
        )
        self.assertEqual(candidate["holding_upgrade_mode"], "confirmed_markup")
        self.assertEqual(candidate["holding_upgrade_min_pnl_pct"], 2.0)
        self.assertIs(candidate["policy_options"]["markup_upgrade_only"], True)
        self.assertEqual(
            candidate["policy_options"][
                "holding_upgrade_position_cap_pct"
            ],
            20.0,
        )
        self.assertAlmostEqual(
            candidate["policy_options"][
                "lifecycle_climax_partial_ratio"
            ],
            1.0 / 3.0,
        )

    def test_round65_staged_markup_scale_in_is_preregistered(self):
        candidates = {
            name: candidate
            for name, candidate in research.CANDIDATES.items()
            if candidate.get("research_status")
            == "round65_staged_markup_scale_in"
        }

        self.assertEqual(len(candidates), 12)
        self.assertTrue(all(
            candidate["holding_upgrade_mode"] == "staged_markup"
            and candidate["holding_upgrade_min_pnl_pct"] == 2.0
            for candidate in candidates.values()
        ))
        self.assertEqual(
            {
                candidate["holding_upgrade_max_pnl_pct"]
                for candidate in candidates.values()
            },
            {8.0, 10.0, 12.0, 15.0},
        )
        self.assertEqual(
            {
                candidate["policy_options"]
                ["holding_upgrade_early_position_cap_pct"]
                for candidate in candidates.values()
            },
            {8.0, 10.0, 12.0},
        )

    def test_round65_production_v20_policy_is_frozen(self):
        candidate = research.CANDIDATES[
            "production_v20_staged_markup_scale_climax_reduce"
        ]

        self.assertEqual(
            candidate["research_status"],
            "promoted_round65_production_v20",
        )
        self.assertEqual(candidate["holding_upgrade_mode"], "staged_markup")
        self.assertEqual(candidate["holding_upgrade_min_pnl_pct"], 2.0)
        self.assertEqual(candidate["holding_upgrade_max_pnl_pct"], 12.0)
        self.assertIs(candidate["policy_options"]["markup_upgrade_only"], True)
        self.assertEqual(
            candidate["policy_options"][
                "holding_upgrade_early_position_cap_pct"
            ],
            10.0,
        )
        self.assertEqual(
            candidate["policy_options"]["holding_upgrade_position_cap_pct"],
            20.0,
        )

    def test_round66_repeatable_markup_rebalance_is_preregistered(self):
        candidates = {
            name: candidate
            for name, candidate in research.CANDIDATES.items()
            if candidate.get("research_status")
            == "round66_repeatable_markup_rebalance"
        }

        self.assertEqual(len(candidates), 6)
        self.assertTrue(all(
            candidate["holding_upgrade_mode"]
            == "staged_markup_rebalance"
            and candidate["policy_options"]["markup_rebalance_enabled"]
            is True
            and candidate["filter_options"]["lifetime_add_limit"] is None
            for candidate in candidates.values()
        ))
        self.assertEqual(
            {
                candidate["policy_options"]
                ["markup_rebalance_pullback_atr"]
                for candidate in candidates.values()
            },
            {1.0, 1.25},
        )
        self.assertEqual(
            {
                candidate["policy_options"]
                ["markup_rebalance_stall_sessions"]
                for candidate in candidates.values()
            },
            {3, 4, 20},
        )

    def test_round66_production_v21_has_no_lifetime_add_limit(self):
        candidate = research.CANDIDATES[
            "production_v21_repeatable_markup_rebalance"
        ]

        self.assertEqual(
            candidate["research_status"],
            "promoted_round66_production_v21",
        )
        self.assertEqual(
            candidate["holding_upgrade_mode"],
            "staged_markup_rebalance",
        )
        self.assertIs(
            candidate["policy_options"]["markup_rebalance_enabled"],
            True,
        )
        self.assertIsNone(
            candidate["filter_options"]["lifetime_add_limit"]
        )
        self.assertEqual(
            candidate["policy_options"]["markup_rebalance_trim_ratio"],
            1.0 / 3.0,
        )

    def test_round67_cross_theme_slot_sensitivity_is_preregistered(self):
        candidates = {
            name: candidate
            for name, candidate in research.CANDIDATES.items()
            if candidate.get("research_status")
            == "round67_cross_theme_slot_sensitivity"
        }

        self.assertEqual(len(candidates), 5)
        self.assertEqual(
            {
                (
                    candidate["policy_options"]["max_open_positions"],
                    candidate["policy_options"]["max_industry_positions"],
                )
                for candidate in candidates.values()
            },
            {(5, 1), (6, 1), (7, 1), (6, 2), (7, 2)},
        )
        self.assertTrue(all(
            candidate["policy_options"]["markup_rebalance_enabled"] is True
            and candidate["filter_options"]["capital_route"]
            == "cross_theme_leaders_before_same_theme_followers"
            for candidate in candidates.values()
        ))

    def test_round67_leader_precedence_is_labeled_post_hoc(self):
        candidates = {
            name: candidate
            for name, candidate in research.CANDIDATES.items()
            if candidate.get("research_status")
            == "round67_post_hoc_leader_precedence_diagnostic"
        }

        self.assertEqual(len(candidates), 2)
        self.assertEqual(
            {
                candidate["policy_options"]["max_open_positions"]
                for candidate in candidates.values()
            },
            {5, 6},
        )
        self.assertTrue(all(
            candidate["signal_order"][0] == "niu_leader"
            and candidate["filter_options"]["capital_route"]
            == "mature_leaders_before_brewing_probes"
            for candidate in candidates.values()
        ))

    def test_cached_eligible_count_prefers_symbols_with_bars(self):
        self.assertEqual(
            research._cached_eligible_symbol_count({
                "eligible_symbol_count": 3045,
                "eligible_symbols_with_bars": 3033,
            }),
            3033,
        )
        self.assertEqual(
            research._cached_eligible_symbol_count({
                "eligible_symbol_count": 3045,
            }),
            3045,
        )

    def test_replay_cache_version_freezes_causal_lifecycle_semantics(self):
        self.assertEqual(
            research.REPLAY_CACHE_FORMAT,
            "niuone-stage-replay-v2",
        )

    def test_named_candidate_is_blocked_for_threshold_matrix_analysis(self):
        flags = {
            "stage_entry_analysis": False,
            "reversal_shape_analysis": False,
            "pullback_geometry_analysis": False,
            "pullback_recovery_analysis": False,
        }
        self.assertFalse(research._candidate_uses_threshold_matrix(
            SimpleNamespace(**flags)
        ))
        flags["stage_entry_analysis"] = True
        self.assertTrue(research._candidate_uses_threshold_matrix(
            SimpleNamespace(**flags)
        ))

    def test_trade_features_use_initial_signal_when_later_adds_share_trade(self):
        result = SimpleNamespace(
            signals=(
                {
                    "trade_id": "trade-1",
                    "signal_date": "2025-12-18",
                    "entry_open": 10.1,
                    "metadata": {"scored": {
                        "recent_close": 10.0,
                        "mainline_state": "emerging",
                        "mainline_score_change": -1.73,
                        "niuone_lifecycle_stage": "divergence",
                        "niuone_lifecycle_label": "主线分歧",
                        "niuone_lifecycle_order": 40,
                        "niuone_lifecycle_entry_policy": (
                            "selective_repair_reclaim_or_reduce"
                        ),
                    }},
                },
                {
                    "trade_id": "trade-1",
                    "signal_date": "2025-12-23",
                    "entry_open": 11.1,
                    "metadata": {"scored": {
                        "recent_close": 11.0,
                        "mainline_score_change": 5.54,
                    }},
                },
            ),
            trades=({
                "id": "trade-1",
                "status": "completed",
                "signal_date": "2025-12-18",
                "entry_date": "2025-12-19",
                "entry_price": 10.2,
                "exit_date": "2025-12-25",
                "holding_sessions": 4,
                "symbol": "sh601336",
                "strategy_id": "niu_pullback",
                "net_return_pct": 0.7,
            },),
        )

        features = research._trade_features(result)

        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["window_signal_date"], "2025-12-18")
        self.assertEqual(features[0]["mainline_score_change"], -1.73)
        self.assertEqual(features[0]["next_open_gap_pct"], 1.0)
        self.assertEqual(features[0]["niuone_lifecycle_stage"], "divergence")
        self.assertEqual(features[0]["niuone_lifecycle_label"], "主线分歧")
        self.assertEqual(features[0]["niuone_lifecycle_order"], 40)

    def test_development_features_exclude_overlapping_recent_window(self):
        windows = {
            "old_sealed": {"completed_trade_features": ({"id": "old"},)},
            "train_a": {"completed_trade_features": ({"id": "a"},)},
            "train_b": {"completed_trade_features": ({"id": "b"},)},
            "validation": {"completed_trade_features": ({"id": "v"},)},
            "recent": {"completed_trade_features": ({"id": "recent"},)},
        }

        features = research._development_completed_features(windows)

        self.assertEqual(
            [feature["id"] for feature in features],
            ["old", "a", "b", "v"],
        )

    def test_feature_groups_use_current_pattern_and_extension_scales(self):
        groups = research._feature_groups([{
            "strategy_id": "niu_reversal_probe",
            "net_return_pct": 1.0,
            "market_regime": "rotation",
            "mainline_state": "candidate",
            "right_days": 7,
            "recovery_ratio": 0.9,
            "rising_ratio": 0.8,
            "decline_pct": 12.0,
            "rebound_pct": 10.0,
            "pattern_score": 94.0,
            "signal_change_pct": 2.0,
            "entry_extension_atr": 1.2,
            "actual_stop_distance_pct": 4.0,
            "next_open_gap_pct": 0.5,
            "exit_signal": "niu_structure_stop",
        }])

        self.assertEqual(list(groups["pattern_score"]), ["[90,95)"])
        self.assertEqual(
            list(groups["entry_extension_atr"]),
            ["[1.1,1.25)"],
        )

    def test_current_default_and_explicit_research_policy_stay_distinct(self):
        self.assertIsNone(
            research.CANDIDATES["frozen_production_default"]["policy_options"]
        )
        self.assertIs(
            research.CANDIDATES["frozen_production_default"]["signal_filter"],
            research._production_stage_filter,
        )
        legacy = research.CANDIDATES["round58_legacy_v15_baseline"]
        self.assertEqual(legacy["research_status"], "historical_round58")
        self.assertIs(
            legacy["signal_filter"],
            research._round58_legacy_v15_stage_filter,
        )
        recovery_cap = research.CANDIDATES[
            "production_reversal_max_recovery_2"
        ]
        self.assertEqual(recovery_cap["research_status"], "promoted_round58")
        self.assertEqual(
            recovery_cap["filter_options"],
            {"maximum_recovery_ratio": 2.0},
        )
        result = SimpleNamespace(
            statistics={}, portfolio={}, signals=(), trades=(),
        )
        with (
            patch.object(
                research, "NiuOneStrategyBacktestPolicy",
            ) as policy_factory,
            patch.object(research, "run_selection_backtest", return_value=result),
        ):
            research._run_window({}, None, "2026-01-01", "2026-01-31")
            policy_factory.assert_called_once_with()

            policy_factory.reset_mock()
            research._run_window(
                {}, None, "2026-01-01", "2026-01-31", policy_options={},
            )
            policy_factory.assert_called_once_with(
                reversal_early_profit_regimes=(),
            )

        candidate = research.CANDIDATES[
            "production_daily_v_no_progress_requires_unconfirmed"
        ]
        self.assertEqual(candidate["research_status"], "rejected_round10")
        json.dumps(candidate["policy_options"])

        execution_candidate = research.CANDIDATES[
            "production_reversal_max_execution_gap_10"
        ]
        self.assertEqual(
            execution_candidate["research_status"],
            "shadow_round13",
        )
        self.assertEqual(
            execution_candidate["policy_options"],
            {
                "reversal_max_execution_gap_pct": 1.0,
                "reversal_early_profit_regimes": tuple(sorted(
                    research.NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
                )),
            },
        )
        json.dumps(execution_candidate["policy_options"])

        round14_candidates = {
            name: candidate
            for name, candidate in research.CANDIDATES.items()
            if candidate.get("research_status") == "rejected_round14"
        }
        self.assertEqual(len(round14_candidates), 8)
        self.assertEqual(
            round14_candidates[
                "production_reversal_mainline_peak_decay_50"
            ]["policy_options"][
                "reversal_mainline_peak_drawdown_points"
            ],
            5.0,
        )
        self.assertEqual(
            round14_candidates[
                "production_reversal_unconfirmed_min_strength_30"
            ]["filter_options"],
            {"minimum_unconfirmed_today_strength": 30.0},
        )

        exit_promotion = research.CANDIDATES[
            "production_reversal_strong_leader_exit_promotion"
        ]
        self.assertEqual(
            exit_promotion["research_status"],
            "rejected_round25",
        )
        self.assertEqual(
            exit_promotion["policy_options"][
                "reversal_strong_leader_exit_promotion"
            ],
            True,
        )
        mainline_exit = research.CANDIDATES[
            "production_reversal_strong_leader_mainline_exit"
        ]
        self.assertEqual(
            mainline_exit["research_status"],
            "rejected_round26",
        )
        self.assertEqual(
            mainline_exit["policy_options"][
                "reversal_strong_leader_mainline_exit"
            ],
            True,
        )
        early_failure = research.CANDIDATES[
            "production_daily_v_unconfirmed_failure_t2"
        ]
        self.assertEqual(
            early_failure["research_status"],
            "rejected_round27",
        )
        self.assertEqual(
            early_failure["policy_options"][
                "daily_v_unconfirmed_failure_hold_days"
            ],
            2,
        )
        lifecycle_candidate = research.CANDIDATES[
            "production_lifecycle_early_recovery_lt2"
        ]
        self.assertEqual(
            lifecycle_candidate["research_status"],
            "shadow_round29",
        )
        self.assertEqual(
            lifecycle_candidate["filter_options"],
            {
                "entry_mainline_states": ("candidate", "emerging"),
                "observed_entry_lifecycle_stages": (
                    "brewing",
                ),
                "lifecycle_stage_is_filter": True,
                "entry_strategy_ids": ("niu_reversal_probe",),
                "maximum_daily_v_recovery_ratio_exclusive": 2.0,
                "climax_new_entries": False,
                "fade_new_entries": False,
            },
        )
        stage_contract = research.CANDIDATES[
            "production_lifecycle_stage_entry_contract"
        ]
        self.assertEqual(stage_contract["research_status"], "promoted_current")
        self.assertEqual(
            stage_contract["filter_options"][
                "divergence_entry_strategy_ids"
            ],
            ("niu_leader", "niu_pullback"),
        )
        stage_routed = research.CANDIDATES[
            "production_lifecycle_stage_routed_early_recovery_lt2"
        ]
        self.assertEqual(stage_routed["research_status"], "promoted_current")
        self.assertEqual(
            stage_routed["filter_options"][
                "maximum_daily_v_recovery_ratio_exclusive"
            ],
            2.0,
        )
        round36_candidates = {
            name: candidate
            for name, candidate in research.CANDIDATES.items()
            if candidate.get("research_status") == "rejected_round36"
        }
        self.assertEqual(len(round36_candidates), 2)
        self.assertEqual(
            round36_candidates[
                "production_lifecycle_early_recovery_lt2_"
                "upgrade_top5_persistent"
            ]["holding_upgrade_mode"],
            "full_theme_top5_persistent",
        )
        self.assertEqual(
            round36_candidates[
                "production_lifecycle_early_recovery_lt2_"
                "upgrade_new_top5_persistent"
            ]["filter_options"]["required_theme_transition"],
            "new_top5",
        )
        self.assertTrue(all(
            candidate["policy_options"]
            ["holding_upgrade_position_cap_pct"] == 10.0
            for candidate in round36_candidates.values()
        ))
        round37_candidates = {
            name: candidate
            for name, candidate in research.CANDIDATES.items()
            if candidate.get("research_status") == "rejected_round37"
        }
        self.assertEqual(len(round37_candidates), 2)
        self.assertTrue(all(
            candidate["policy_options"]
            ["holding_upgrade_preserves_strategy"] is True
            for candidate in round37_candidates.values()
        ))
        self.assertEqual(
            round37_candidates[
                "production_lifecycle_early_recovery_lt2_"
                "scale_new_top5_persistent"
            ]["holding_upgrade_mode"],
            "full_theme_new_top5_persistent",
        )
        stage_exit_candidate = research.CANDIDATES[
            "production_lifecycle_early_recovery_lt2_stage_exit"
        ]
        self.assertEqual(
            stage_exit_candidate["research_status"],
            "rejected_round31",
        )
        self.assertEqual(
            stage_exit_candidate["policy_options"],
            {
                "lifecycle_climax_partial_ratio": 1.0 / 3.0,
                "lifecycle_climax_min_pnl_pct": 0.0,
                "lifecycle_fade_exit": True,
            },
        )
        round32_options = {
            name: research.CANDIDATES[name]["filter_options"]
            for name in (
                "production_lifecycle_early_recovery_lt2_breadth60",
                "production_lifecycle_early_recovery_lt2_today_strength40",
                "production_lifecycle_early_recovery_lt2_signal_stop3",
            )
        }
        self.assertTrue(all(
            research.CANDIDATES[name]["research_status"]
            == "rejected_round32"
            for name in round32_options
        ))
        self.assertEqual(
            round32_options[
                "production_lifecycle_early_recovery_lt2_breadth60"
            ]["minimum_today_breadth_pct"],
            60.0,
        )
        self.assertEqual(
            round32_options[
                "production_lifecycle_early_recovery_lt2_today_strength40"
            ]["minimum_today_strength_score"],
            40.0,
        )
        self.assertEqual(
            round32_options[
                "production_lifecycle_early_recovery_lt2_signal_stop3"
            ]["minimum_signal_stop_distance_pct"],
            3.0,
        )
        round33_options = {
            name: research.CANDIDATES[name]["filter_options"]
            for name in (
                "production_lifecycle_early_recovery_lt2_theme_top2",
                "production_lifecycle_early_recovery_lt2_theme_top5",
            )
        }
        self.assertTrue(all(
            research.CANDIDATES[name]["research_status"]
            == "rejected_round33"
            for name in round33_options
        ))
        self.assertEqual(
            round33_options[
                "production_lifecycle_early_recovery_lt2_theme_top2"
            ]["maximum_theme_rank"],
            2,
        )
        self.assertEqual(
            round33_options[
                "production_lifecycle_early_recovery_lt2_theme_top5"
            ]["maximum_theme_rank"],
            5,
        )

        round15_candidates = {
            name: candidate
            for name, candidate in research.CANDIDATES.items()
            if candidate.get("research_status") == "rejected_round15"
        }
        self.assertEqual(len(round15_candidates), 8)
        self.assertEqual(
            round15_candidates[
                "production_reversal_min_signal_score_90"
            ]["filter_options"],
            {"minimum_signal_score": 9.0},
        )
        self.assertEqual(
            round15_candidates[
                "production_reversal_max_top_score_gap_02"
            ]["filter_options"],
            {"maximum_top_score_gap": 0.2},
        )

        round16_candidates = {
            name: candidate
            for name, candidate in research.CANDIDATES.items()
            if candidate.get("research_status") == "rejected_round16"
        }
        self.assertEqual(len(round16_candidates), 8)
        self.assertEqual(
            round16_candidates[
                "production_reversal_min_decline_12_max_recovery_120"
            ]["filter_options"],
            {
                "minimum_decline_pct": 12.0,
                "maximum_recovery_ratio": 1.2,
            },
        )
        stage_candidates = research.ROUND16_STAGE_ENTRY_CANDIDATES
        self.assertEqual(len(stage_candidates), 16)
        self.assertEqual(
            sum(
                candidate.get("research_status") == "rejected_round16"
                for candidate in stage_candidates.values()
            ),
            10,
        )
        self.assertEqual(
            sum(
                candidate.get("research_status") == "rejected_round34"
                for candidate in stage_candidates.values()
            ),
            2,
        )
        self.assertEqual(
            sum(
                candidate.get("research_status") == "rejected_round34"
                for candidate in research.CANDIDATES.values()
            ),
            2,
        )
        self.assertEqual(
            sum(
                candidate.get("research_status") == "rejected_round35"
                for candidate in stage_candidates.values()
            ),
            2,
        )
        self.assertEqual(
            stage_candidates[
                "stage_emerging_score_80_breakout_050_reversal_recovery_cap_200"
            ]["research_status"],
            "rejected_round22",
        )
        self.assertEqual(len(research.ROUND17_PULLBACK_VARIANT_IDS), 10)
        self.assertEqual(len(research.ROUND17_PULLBACK_CANDIDATES), 19)
        self.assertEqual(
            sum(
                candidate.get("research_status") == "rejected_round17"
                for candidate in research.ROUND17_PULLBACK_CANDIDATES.values()
            ),
            18,
        )
        self.assertEqual(
            len(research.ROUND18_PULLBACK_RECOVERY_CANDIDATES),
            6,
        )
        self.assertEqual(
            sum(
                candidate.get("research_status") == "rejected_round18"
                for candidate in (
                    research.ROUND18_PULLBACK_RECOVERY_CANDIDATES.values()
                )
            ),
            5,
        )

    def test_round49_entry_order_scale_candidates_isolate_sizing(self):
        expected_scales = {
            "production_entry_order_scale_075": 0.75,
            "production_entry_order_scale_050": 0.50,
            "production_entry_order_scale_025": 0.25,
        }
        expected_regimes = tuple(sorted(
            research.NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES
        ))

        for name, expected_scale in expected_scales.items():
            with self.subTest(name=name):
                candidate = research.CANDIDATES[name]
                self.assertEqual(
                    candidate["research_status"],
                    "diagnostic_round49",
                )
                self.assertIs(
                    candidate["signal_filter"],
                    research._production_stage_filter,
                )
                self.assertEqual(
                    candidate["policy_options"],
                    {
                        "entry_order_scale": expected_scale,
                        "reversal_early_profit_regimes": expected_regimes,
                    },
                )

    def test_production_stage_filter_replays_shared_reversal_routing(self):
        def signal(**scored):
            return SelectionSignal(
                symbol="600000",
                strategy_id="niu_reversal_probe",
                reason="fixture",
                score=8.5,
                metadata={"scored": scored},
            )

        self.assertTrue(research._production_stage_filter(signal(
            mainline_state="candidate",
            mainline_confirmed=False,
            stock_strong=False,
            daily_v_recovery_ratio=0.60,
            strong_stock_count=6,
            mainline_state_streak=1,
        )))
        self.assertFalse(research._production_stage_filter(signal(
            mainline_state="candidate",
            mainline_confirmed=False,
            stock_strong=True,
            daily_v_recovery_ratio=0.60,
        )))
        self.assertFalse(research._production_stage_filter(signal(
            mainline_state="mainline",
            mainline_confirmed=True,
            stock_strong=True,
            daily_v_recovery_ratio=0.60,
        )))
        self.assertFalse(research._production_stage_filter(signal(
            mainline_state="candidate",
            mainline_confirmed=False,
            stock_strong=False,
            daily_v_recovery_ratio=0.5999,
        )))
        self.assertTrue(research._production_stage_filter(signal(
            mainline_state="candidate",
            mainline_confirmed=False,
            stock_strong=False,
            daily_v_recovery_ratio=1.9999,
            strong_stock_count=1,
            mainline_state_streak=3,
        )))
        self.assertFalse(research._production_stage_filter(signal(
            mainline_state="candidate",
            mainline_confirmed=False,
            stock_strong=False,
            daily_v_recovery_ratio=2.0,
        )))
        self.assertTrue(research._round58_legacy_v15_stage_filter(signal(
            mainline_state="candidate",
            mainline_confirmed=False,
            stock_strong=False,
            daily_v_recovery_ratio=2.0,
        )))
        self.assertTrue(research._production_stage_filter(SelectionSignal(
            symbol="600000",
            strategy_id="niu_leader",
            reason="fixture",
            score=8.5,
            metadata={},
        )))
        self.assertFalse(research._production_stage_filter(SelectionSignal(
            symbol="600000",
            strategy_id="niu_leader",
            reason="fixture",
            score=8.5,
            metadata={"scored": {
                "stock_leader_rank": 3,
                "today_strength_score": 59.99,
            }},
        )))

    def test_lifecycle_stage_entry_contract_routes_actions_by_phase(self):
        def signal(strategy_id: str, **scored):
            if strategy_id == "niu_reversal_probe":
                scored = {
                    "strong_stock_count": 6,
                    "mainline_state_streak": 1,
                    **scored,
                }
            return SelectionSignal(
                symbol="600000",
                strategy_id=strategy_id,
                reason="fixture",
                score=8.5,
                metadata={"scored": scored},
            )

        self.assertTrue(research._lifecycle_stage_entry_contract_filter(
            signal(
                "niu_reversal_probe",
                mainline_state="candidate",
                mainline_confirmed=False,
                stock_strong=False,
                daily_v_recovery_ratio=0.8,
            )
        ))
        self.assertTrue(research._lifecycle_stage_entry_contract_filter(
            signal(
                "niu_leader",
                mainline_state="mainline",
                mainline_confirmed=True,
                mainline_score=77.9,
                stock_leader_rank=1,
                stock_sector_rank=80.0,
                today_strength_score=60.0,
            )
        ))
        self.assertTrue(research._lifecycle_stage_entry_contract_filter(
            signal(
                "niu_leader",
                mainline_state="mainline",
                mainline_confirmed=True,
                mainline_score=78.0,
                stock_leader_rank=1,
                stock_sector_rank=80.0,
                today_strength_score=60.0,
            )
        ))
        self.assertTrue(research._lifecycle_stage_entry_contract_filter(
            signal(
                "niu_pullback",
                mainline_state="mainline",
                mainline_confirmed=True,
                mainline_score=78.0,
                niuone_lifecycle_stage="climax",
            )
        ))
        self.assertTrue(research._lifecycle_stage_entry_contract_filter(
            signal("niu_pullback", mainline_state="diverging")
        ))
        self.assertFalse(research._lifecycle_stage_entry_contract_filter(
            signal(
                "niu_reversal_probe",
                mainline_state="candidate",
                mainline_confirmed=False,
                stock_strong=False,
                daily_v_recovery_ratio=0.8,
                niuone_lifecycle_stage="divergence",
            )
        ))
        self.assertFalse(research._lifecycle_stage_entry_contract_filter(
            signal("niu_emerging", mainline_state="diverging")
        ))
        self.assertTrue(
            research._lifecycle_stage_routed_early_recovery_filter(signal(
                "niu_reversal_probe",
                mainline_state="candidate",
                mainline_confirmed=False,
                stock_strong=False,
                daily_v_recovery_ratio=1.99,
            ))
        )
        self.assertFalse(
            research._lifecycle_stage_routed_early_recovery_filter(signal(
                "niu_reversal_probe",
                mainline_state="candidate",
                mainline_confirmed=False,
                stock_strong=False,
                daily_v_recovery_ratio=2.0,
            ))
        )
        self.assertTrue(
            research._lifecycle_stage_routed_early_recovery_filter(signal(
                "niu_emerging",
                mainline_state="emerging",
                mainline_cross_day_persistent=True,
            ))
        )

    def test_reversal_quality_filter_layers_on_production_boundaries(self):
        quality_filter = research._production_reversal_quality_filter(
            minimum_mainline_score=60.0,
            minimum_strong_count=3,
            minimum_today_strength=30.0,
            maximum_recovery_ratio=2.0,
        )

        def signal(**overrides):
            scored = {
                "mainline_state": "candidate",
                "mainline_confirmed": False,
                "stock_strong": False,
                "daily_v_recovery_ratio": 0.80,
                "mainline_score": 60.0,
                "strong_stock_count": 3,
                "mainline_state_streak": 3,
                "today_strength_score": 30.0,
                **overrides,
            }
            return SelectionSignal(
                symbol="600000",
                strategy_id="niu_reversal_probe",
                reason="fixture",
                score=8.5,
                metadata={"scored": scored},
            )

        self.assertTrue(quality_filter(signal()))
        self.assertFalse(quality_filter(signal(mainline_score=59.99)))
        self.assertFalse(quality_filter(signal(strong_stock_count=2)))
        self.assertFalse(quality_filter(signal(today_strength_score=29.99)))
        self.assertTrue(quality_filter(signal(daily_v_recovery_ratio=1.9999)))
        self.assertFalse(quality_filter(signal(daily_v_recovery_ratio=2.0)))
        self.assertFalse(quality_filter(signal(daily_v_recovery_ratio=0.5999)))

    def test_lifecycle_early_recovery_filter_rejects_mature_entries(self):
        def signal(strategy_id="niu_reversal_probe", **overrides):
            scored = {
                "mainline_state": "emerging",
                "mainline_confirmed": False,
                "stock_strong": False,
                "daily_v_recovery_ratio": 1.9999,
                "strong_stock_count": 6,
                "mainline_state_streak": 1,
                **overrides,
            }
            return SelectionSignal(
                symbol="600000",
                strategy_id=strategy_id,
                reason="fixture",
                score=8.5,
                metadata={"scored": scored},
            )

        self.assertTrue(research._lifecycle_early_recovery_filter(signal()))
        self.assertTrue(research._lifecycle_early_recovery_filter(signal(
            mainline_state="candidate",
        )))
        self.assertFalse(research._lifecycle_early_recovery_filter(signal(
            daily_v_recovery_ratio=2.0,
        )))
        self.assertFalse(research._lifecycle_early_recovery_filter(signal(
            mainline_state="mainline",
            mainline_confirmed=True,
        )))
        self.assertFalse(research._lifecycle_early_recovery_filter(signal(
            strategy_id="niu_leader",
        )))

    def test_lifecycle_early_quality_filters_apply_only_selected_factor(self):
        def signal(**overrides):
            scored = {
                "mainline_state": "candidate",
                "mainline_confirmed": False,
                "stock_strong": False,
                "daily_v_recovery_ratio": 1.0,
                "today_breadth_pct": 60.0,
                "today_strength_score": 40.0,
                "stop_distance_pct": 3.0,
                "strong_stock_count": 6,
                "mainline_state_streak": 1,
                **overrides,
            }
            return SelectionSignal(
                symbol="600000",
                strategy_id="niu_reversal_probe",
                reason="fixture",
                score=8.5,
                metadata={"scored": scored},
            )

        self.assertTrue(research.LIFECYCLE_EARLY_BREADTH_60_FILTER(
            signal(today_strength_score=None, stop_distance_pct=None)
        ))
        self.assertFalse(research.LIFECYCLE_EARLY_BREADTH_60_FILTER(
            signal(today_breadth_pct=59.99)
        ))
        self.assertTrue(research.LIFECYCLE_EARLY_TODAY_STRENGTH_40_FILTER(
            signal(today_breadth_pct=None, stop_distance_pct=None)
        ))
        self.assertFalse(research.LIFECYCLE_EARLY_TODAY_STRENGTH_40_FILTER(
            signal(today_strength_score=39.99)
        ))
        self.assertTrue(research.LIFECYCLE_EARLY_SIGNAL_STOP_3_FILTER(
            signal(today_breadth_pct=None, today_strength_score=None)
        ))
        self.assertFalse(research.LIFECYCLE_EARLY_SIGNAL_STOP_3_FILTER(
            signal(stop_distance_pct=2.99)
        ))

    def test_unconfirmed_strength_filter_does_not_bypass_lifecycle_route(self):
        quality_filter = research._production_reversal_quality_filter(
            minimum_unconfirmed_today_strength=30.0,
        )

        def signal(**overrides):
            scored = {
                "mainline_state": "emerging",
                "mainline_confirmed": False,
                "mainline_cross_day_persistent": False,
                "stock_strong": False,
                "daily_v_recovery_ratio": 0.80,
                "mainline_score": 65.0,
                "strong_stock_count": 3,
                "mainline_state_streak": 3,
                "today_strength_score": 29.99,
                **overrides,
            }
            return SelectionSignal(
                symbol="600000",
                strategy_id="niu_reversal_probe",
                reason="fixture",
                score=8.5,
                metadata={"scored": scored},
            )

        self.assertFalse(quality_filter(signal()))
        self.assertTrue(quality_filter(signal(today_strength_score=30.0)))
        self.assertFalse(quality_filter(signal(
            mainline_cross_day_persistent=True,
            today_strength_score=10.0,
        )))

    def test_reversal_shape_filter_layers_on_production_boundaries(self):
        shape_filter = research._production_reversal_shape_filter(
            minimum_decline_pct=12.0,
            maximum_recovery_ratio=1.2,
            allowed_mainline_states=frozenset({"candidate", "emerging"}),
        )

        def signal(**overrides):
            scored = {
                "mainline_state": "candidate",
                "mainline_confirmed": False,
                "stock_strong": False,
                "daily_v_decline_pct": 12.0,
                "daily_v_recovery_ratio": 1.1999,
                "strong_stock_count": 6,
                "mainline_state_streak": 1,
                **overrides,
            }
            return SelectionSignal(
                symbol="600000",
                strategy_id="niu_reversal_probe",
                score=8.5,
                metadata={"scored": scored},
            )

        self.assertTrue(shape_filter(signal()))
        self.assertFalse(shape_filter(signal(daily_v_decline_pct=11.99)))
        self.assertFalse(shape_filter(signal(daily_v_recovery_ratio=1.2)))
        self.assertFalse(shape_filter(signal(mainline_state="mainline")))
        self.assertFalse(shape_filter(signal(
            stock_strong=True,
            mainline_state="candidate",
        )))

    def test_stage_entry_filter_reapplies_thresholds_and_breakout_geometry(self):
        production = research._stage_entry_filter()
        relaxed = research._stage_entry_filter(
            leader_score=7.0,
            emerging_score=7.0,
            minimum_emerging_breakout_atr=0.5,
        )

        def signal(strategy_id, score, **scored):
            return SelectionSignal(
                symbol="600000",
                strategy_id=strategy_id,
                score=score,
                metadata={"scored": scored},
            )

        leader = signal(
            "niu_leader",
            7.9,
            stock_leader_rank=1,
            stock_sector_rank=80.0,
            today_strength_score=60.0,
        )
        self.assertFalse(production(leader))
        self.assertTrue(relaxed(leader))
        emerging = signal(
            "niu_emerging",
            7.9,
            entry_setup="breakout",
            entry_extension_atr=0.5,
        )
        self.assertFalse(production(emerging))
        self.assertTrue(relaxed(emerging))
        self.assertFalse(relaxed(signal(
            "niu_emerging",
            7.9,
            entry_setup="breakout",
            entry_extension_atr=0.4999,
        )))
        self.assertFalse(relaxed(signal(
            "niu_emerging",
            7.9,
            entry_setup="reclaim",
            entry_extension_atr=0.8,
        )))

        combined = research._stage_entry_with_reversal_recovery_cap(
            emerging_score=8.0,
            minimum_emerging_breakout_atr=0.5,
            maximum_reversal_recovery_ratio=2.0,
        )
        self.assertTrue(combined(signal(
            "niu_emerging",
            8.0,
            entry_setup="breakout",
            entry_extension_atr=0.5,
        )))
        self.assertTrue(combined(signal(
            "niu_reversal_probe",
            8.8,
            mainline_state="candidate",
            mainline_score=60.0,
            strong_stock_count=2,
            mainline_state_streak=3,
            today_strength_score=30.0,
            daily_v_recovery_ratio=1.9999,
        )))
        self.assertFalse(combined(signal(
            "niu_reversal_probe",
            8.8,
            mainline_state="candidate",
            mainline_score=60.0,
            strong_stock_count=2,
            mainline_state_streak=3,
            today_strength_score=30.0,
            daily_v_recovery_ratio=2.0,
        )))

    def test_stage_entry_cache_requires_a_complete_wide_threshold_tape(self):
        valid = {
            "round16_thresholds": {
                "niu_leader": 7.0,
                "niu_pullback": 7.0,
                "niu_emerging": 7.0,
                "niu_reversal_probe": 7.6,
            },
        }
        research._validate_stage_entry_cache(valid)
        with self.assertRaisesRegex(ValueError, "round16_thresholds"):
            research._validate_stage_entry_cache({})
        with self.assertRaisesRegex(ValueError, "niu_emerging"):
            research._validate_stage_entry_cache({
                "round16_thresholds": {
                    **valid["round16_thresholds"],
                    "niu_emerging": 8.4,
                },
            })

    def test_research_threshold_scorer_only_lowers_emission_floor(self):
        observed = {}

        def source_scorer(rows, context):
            observed["context"] = context
            return {
                "score": 7.2,
                "entry_threshold": 8.4,
                "hard_blockers": [],
                "actionable": False,
                "decision_score": 7.96,
            }

        source_scorer.requires_context = True
        with patch.dict(
            research.STRATEGY_SCORERS,
            {"niu_emerging": source_scorer},
        ):
            scorer = research._research_threshold_scorer(
                "niu_emerging",
                7.0,
            )
            scored = scorer([{"close": 10.0}], {"date": "2026-01-05"})

        self.assertEqual(observed["context"], {"date": "2026-01-05"})
        self.assertEqual(scored["entry_threshold"], 7.0)
        self.assertTrue(scored["actionable"])
        self.assertEqual(scored["decision_score"], 7.96)

    def test_research_threshold_scorer_preserves_hard_blockers(self):
        def source_scorer(_rows):
            return {
                "score": 8.0,
                "entry_threshold": 8.4,
                "hard_blockers": ["risk"],
                "actionable": False,
            }

        with patch.dict(
            research.STRATEGY_SCORERS,
            {"niu_emerging": source_scorer},
        ):
            scorer = research._research_threshold_scorer(
                "niu_emerging",
                7.0,
            )
            scored = scorer([{"close": 10.0}])

        self.assertFalse(scored["actionable"])
        self.assertEqual(scored["hard_blockers"], ["risk"])

    def test_pullback_geometry_separates_prior_touch_from_current_volume(self):
        rows = [
            {
                "date": f"2026-01-{index + 1:02d}",
                "close": 10.0,
                "high": 10.4,
                "low": 10.7,
                "ema20": 10.0,
            }
            for index in range(20)
        ]
        rows[-2]["low"] = 10.2
        rows[-1].update({
            "close": 10.4,
            "high": 10.5,
            "low": 10.8,
        })
        payload = {
            "atr": 1.0,
            "recent_close": 10.4,
            "ema20": 10.0,
            "change_pct": 4.0,
            "volume_ratio": 1.5,
            "pullback": False,
            "reclaim": False,
            "entry_setup": "none",
        }

        variants = research._pullback_research_geometries(rows, payload)

        self.assertFalse(variants["production_ema20"]["matched"])
        self.assertFalse(
            variants["ema20_same_session_atr050"]["matched"]
        )
        self.assertTrue(
            variants["ema20_prior_confirm_atr050"]["matched"]
        )
        self.assertEqual(
            variants["ema20_prior_confirm_atr050"]["support_date"],
            "2026-01-19",
        )

    def test_pullback_geometry_filter_enforces_score_and_own_anchor(self):
        accepted = research._pullback_geometry_filter(
            "ema10_confirm_atr025",
            pullback_score=8.2,
        )

        def signal(*, score=8.2, extension=1.0, maximum=1.0):
            return SelectionSignal(
                symbol="600000",
                strategy_id="niu_pullback",
                score=score,
                metadata={"scored": {
                    "max_entry_extension_atr": maximum,
                    "pullback_research_variants": {
                        "ema10_confirm_atr025": {
                            "matched": True,
                            "entry_extension_atr": extension,
                        },
                    },
                }},
            )

        self.assertTrue(accepted(signal()))
        self.assertFalse(accepted(signal(score=8.19)))
        self.assertFalse(accepted(signal(extension=1.0001)))

    def test_pullback_recovery_filter_requires_theme_and_stock_confirmation(self):
        directional = research._pullback_recovery_filter()
        rescued = research._pullback_recovery_filter(
            intraday_rescue_strength=40.0,
        )
        ema10 = research._pullback_recovery_filter(
            intraday_rescue_strength=40.0,
            require_ema10_confirmation=True,
            maximum_ema10_extension_atr=1.5,
        )

        def signal(**overrides):
            scored = {
                "mainline_state": "diverging",
                "mainline_confirmed": True,
                "mainline_score": 72.0,
                "mainline_score_change": 2.0,
                "today_strength_score": 30.0,
                "today_breadth_pct": 50.0,
                "change_pct": 1.0,
                "hard_blockers": [],
                "pullback_research_variants": {
                    "ema10_confirm_atr050": {
                        "matched": True,
                        "entry_extension_atr": 1.5,
                    },
                },
            }
            scored.update(overrides)
            return SelectionSignal(
                symbol="600000",
                strategy_id="niu_pullback",
                score=7.8,
                metadata={"scored": scored},
            )

        self.assertTrue(directional(signal()))
        self.assertTrue(ema10(signal()))
        self.assertFalse(directional(signal(mainline_score_change=-1.0)))
        self.assertTrue(rescued(signal(
            mainline_score_change=-1.0,
            today_strength_score=40.0,
        )))
        self.assertFalse(rescued(signal(
            mainline_score_change=-1.0,
            today_strength_score=39.99,
        )))
        self.assertFalse(directional(signal(today_breadth_pct=49.99)))
        self.assertFalse(directional(signal(change_pct=0.0)))
        self.assertFalse(directional(signal(mainline_state="mainline")))
        self.assertFalse(ema10(signal(
            pullback_research_variants={
                "ema10_confirm_atr050": {
                    "matched": True,
                    "entry_extension_atr": 1.5001,
                },
            },
        )))

        leader = SelectionSignal(
            symbol="600000",
            strategy_id="niu_leader",
            score=8.0,
            metadata={"scored": {
                "stock_sector_rank": 80.0,
                "today_strength_score": 60.0,
            }},
        )
        self.assertTrue(directional(leader))

    def test_pullback_cache_validation_and_merge_are_deterministic(self):
        metadata = {
            "round17_pullback_variants": list(
                research.ROUND17_PULLBACK_VARIANT_IDS
            ),
            "round17_pullback_source_threshold": 7.0,
        }
        research._validate_pullback_geometry_cache(metadata)
        with self.assertRaisesRegex(ValueError, "complete"):
            research._validate_pullback_geometry_cache({
                **metadata,
                "round17_pullback_variants": ["production_ema20"],
            })

        def signal(strategy_id, decision_score):
            return SelectionSignal(
                symbol="600000",
                strategy_id=strategy_id,
                score=8.2,
                metadata={"scored": {
                    "decision_score": decision_score,
                    "strategy_priority": 84,
                }},
            )

        old_pullback = signal("niu_pullback", 8.5)
        leader = signal("niu_leader", 9.0)
        research_pullback = signal("niu_pullback", 9.1)
        base = SelectionReplayTape(frames={
            "2026-01-05": SelectionReplayFrame(
                date="2026-01-05",
                signals=(leader, old_pullback),
                scored={"600000": {"niu_leader": {"score": 8.2}}},
            ),
        })
        pullback = SelectionReplayTape(frames={
            "2026-01-05": SelectionReplayFrame(
                date="2026-01-05",
                signals=(research_pullback,),
                scored={"600000": {"niu_pullback": {"score": 8.2}}},
            ),
        })

        merged = research._merge_pullback_research_tape(base, pullback)

        frame = merged.frames["2026-01-05"]
        self.assertEqual(frame.signals, (research_pullback, leader))
        self.assertEqual(
            set(frame.scored["600000"]),
            {"niu_leader", "niu_pullback"},
        )

    def test_development_aggregate_uses_trade_counts_and_compounds_windows(self):
        windows = {
            "first": {
                "statistics": {
                    "completed_trade_count": 2,
                    "portfolio_return_pct": 2.0,
                    "max_drawdown_pct": -1.0,
                },
                "completed_trade_features": [
                    {"net_return_pct": 1.0},
                    {"net_return_pct": -0.5},
                ],
            },
            "second": {
                "statistics": {
                    "completed_trade_count": 1,
                    "portfolio_return_pct": -1.0,
                    "max_drawdown_pct": -3.0,
                },
                "completed_trade_features": [
                    {"net_return_pct": 2.0},
                ],
            },
        }

        aggregate = research._development_aggregate(
            windows,
            window_names=("first", "second", "missing"),
        )

        self.assertEqual(aggregate["window_names"], ["first", "second"])
        self.assertEqual(aggregate["completed_trade_count"], 3)
        self.assertEqual(aggregate["win_count"], 2)
        self.assertEqual(aggregate["win_rate_pct"], 66.6667)
        self.assertEqual(aggregate["compounded_portfolio_return_pct"], 0.98)
        self.assertEqual(aggregate["positive_window_count"], 1)
        self.assertEqual(aggregate["evaluated_window_count"], 2)
        self.assertEqual(aggregate["worst_max_drawdown_pct"], -3.0)
        self.assertTrue(research._production_stage_filter(SelectionSignal(
            symbol="600000",
            strategy_id="niu_leader",
            reason="fixture",
            score=8.5,
            metadata={"scored": {
                "stock_leader_rank": 3,
                "stock_sector_rank": 80.0,
                "today_strength_score": 60.0,
            }},
        )))

    def test_development_aggregate_recovers_wins_from_summary_only_windows(self):
        windows = {
            "first": {
                "statistics": {
                    "completed_trade_count": 40,
                    "win_rate_pct": 62.5,
                    "portfolio_return_pct": 5.0,
                    "max_drawdown_pct": -2.0,
                },
            },
            "second": {
                "statistics": {
                    "completed_trade_count": 39,
                    "win_rate_pct": 51.2821,
                    "portfolio_return_pct": -1.0,
                    "max_drawdown_pct": -5.0,
                },
            },
        }

        aggregate = research._development_aggregate(
            windows,
            window_names=("first", "second"),
        )

        self.assertEqual(aggregate["completed_trade_count"], 79)
        self.assertEqual(aggregate["win_count"], 45)
        self.assertEqual(aggregate["win_rate_pct"], 56.962)

    def test_stage_development_aggregate_keeps_empty_stages_explicit(self):
        windows = {
            "first": {"statistics": {"by_strategy": {
                "niu_reversal_probe": {
                    "completed_trade_count": 3,
                    "win_rate_pct": 66.6667,
                    "average_net_return_pct": 2.0,
                },
            }}},
            "second": {"statistics": {"by_strategy": {
                "niu_reversal_probe": {
                    "completed_trade_count": 2,
                    "win_rate_pct": 50.0,
                    "average_net_return_pct": -1.0,
                },
                "niu_leader": {
                    "completed_trade_count": 1,
                    "win_rate_pct": 100.0,
                    "average_net_return_pct": 4.0,
                },
            }}},
        }

        aggregate = research._stage_development_aggregate(
            windows,
            window_names=("first", "second"),
        )

        self.assertEqual(
            aggregate["niu_reversal_probe"],
            {
                "completed_trade_count": 5,
                "win_count": 3,
                "win_rate_pct": 60.0,
                "average_net_return_pct": 0.8,
            },
        )
        self.assertEqual(aggregate["niu_leader"]["win_count"], 1)
        self.assertEqual(aggregate["niu_pullback"]["completed_trade_count"], 0)
        self.assertIsNone(aggregate["niu_pullback"]["win_rate_pct"])

    def test_trade_features_report_actual_strategy_lifecycle(self):
        result = SimpleNamespace(
            signals=({
                "trade_id": "trade-1",
                "signal_date": "2026-01-05",
                "entry_open": 10.0,
                "metadata": {"scored": {"recent_close": 10.0}},
            },),
            trades=({
                "id": "trade-1",
                "status": "completed",
                "symbol": "600000",
                "strategy_id": "niu_reversal_probe",
                "current_strategy_id": "niu_leader",
                "strategy_path": (
                    "niu_reversal_probe",
                    "niu_emerging",
                    "niu_leader",
                ),
                "entry_price": 10.0,
                "entry_date": "2026-01-06",
                "exit_date": "2026-01-12",
                "holding_sessions": 5,
                "net_return_pct": 12.5,
                "exit_signal": "theme_failed",
            },),
        )
        tape = SelectionReplayTape(
            frames={
                "2026-01-12": SelectionReplayFrame(
                    date="2026-01-12",
                    signals=(),
                    scored={
                        "600000": {
                            "niu_leader": {
                                "mainline_score": 72.5,
                                "mainline_state": "diverging",
                                "mainline_cross_day_persistent": True,
                                "mainline_confirmed": True,
                            },
                        },
                    },
                ),
            },
        )

        features = research._trade_features(result)
        research._attach_exit_stage_context(features, tape)
        lifecycle = research._lifecycle_summary(features)

        self.assertEqual(
            features[0]["strategy_path"],
            ["niu_reversal_probe", "niu_emerging", "niu_leader"],
        )
        self.assertEqual(features[0]["current_strategy_id"], "niu_leader")
        self.assertEqual(features[0]["entry_date"], "2026-01-06")
        self.assertEqual(features[0]["exit_date"], "2026-01-12")
        self.assertEqual(features[0]["holding_sessions"], 5)
        self.assertEqual(features[0]["exit_mainline_score"], 72.5)
        self.assertEqual(features[0]["exit_mainline_state"], "diverging")
        self.assertIs(features[0]["exit_mainline_confirmed"], True)
        self.assertEqual(features[0]["first_diverging_date"], "2026-01-12")
        self.assertEqual(
            features[0]["first_mainline_confirmed_date"],
            "2026-01-12",
        )
        self.assertEqual(features[0]["mainline_confirmed_sessions"], 1)
        self.assertEqual(lifecycle["upgraded_trade_count"], 1)
        self.assertEqual(lifecycle["upgrade_rate_pct"], 100.0)
        self.assertEqual(
            lifecycle["path_counts"],
            {"niu_reversal_probe -> niu_emerging -> niu_leader": 1},
        )
        self.assertEqual(
            lifecycle["path_performance"][
                "niu_reversal_probe -> niu_emerging -> niu_leader"
            ]["average_return_pct"],
            12.5,
        )

    def test_holding_lifecycle_uses_causal_five_stage_hysteresis(self):
        result = SimpleNamespace(
            signals=({
                "trade_id": "trade-1",
                "signal_date": "2026-01-04",
                "entry_open": 10.0,
                "metadata": {"scored": {"recent_close": 10.0}},
            },),
            trades=({
                "id": "trade-1",
                "status": "completed",
                "symbol": "600000",
                "strategy_id": "niu_reversal_probe",
                "entry_price": 10.0,
                "entry_date": "2026-01-05",
                "exit_date": "2026-01-09",
                "holding_sessions": 5,
                "net_return_pct": 1.0,
                "exit_signal": "theme_failed",
            },),
        )
        observations = (
            ("2026-01-05", {
                "mainline_state": "emerging",
                "mainline_cross_day_persistent": True,
            }),
            ("2026-01-06", {
                "mainline_state": "mainline",
                "mainline_confirmed": True,
                "mainline_score": 78.5,
            }),
            ("2026-01-07", {"mainline_state": "diverging"}),
            ("2026-01-08", {
                "mainline_state": "emerging",
                "mainline_cross_day_persistent": False,
            }),
            ("2026-01-09", {"mainline_state": "inactive"}),
        )
        tape = SelectionReplayTape(frames={
            date: SelectionReplayFrame(
                date=date,
                signals=(),
                scored={"600000": {"niu_reversal_probe": scored}},
            )
            for date, scored in observations
        })

        features = research._trade_features(result)
        research._attach_exit_stage_context(features, tape)

        self.assertEqual(
            features[0]["holding_lifecycle_path"],
            ["markup", "climax", "divergence", "fade"],
        )
        self.assertEqual(features[0]["holding_lifecycle_transition_count"], 3)

    def test_ordered_replay_tape_prioritizes_requested_stages(self):
        signals = (
            SelectionSignal("600001", "niu_reversal_probe", score=9.5),
            SelectionSignal("600002", "niu_emerging", score=8.4),
            SelectionSignal("600003", "niu_leader", score=8.0),
        )
        tape = SelectionReplayTape(frames={
            "2026-01-05": SelectionReplayFrame(
                date="2026-01-05",
                signals=signals,
                scored={},
            ),
        })

        ordered = research._ordered_replay_tape(
            tape,
            ("niu_leader", "niu_emerging", "niu_reversal_probe"),
        )

        self.assertEqual(
            [signal.strategy_id for signal in ordered.frames["2026-01-05"].signals],
            ["niu_leader", "niu_emerging", "niu_reversal_probe"],
        )
        self.assertIsNot(ordered, tape)

    def test_reversal_ranking_context_is_historical_and_filterable(self):
        top = SelectionSignal(
            "600001",
            "niu_reversal_probe",
            score=9.0,
            metadata={"scored": {
                "mainline_state": "candidate",
                "stock_strong": False,
                "daily_v_recovery_ratio": 0.7,
                "strong_stock_count": 6,
                "mainline_state_streak": 1,
            }},
        )
        second = SelectionSignal(
            "600002",
            "niu_reversal_probe",
            score=8.8,
            metadata={"scored": {
                "mainline_state": "candidate",
                "stock_strong": False,
                "daily_v_recovery_ratio": 0.7,
                "strong_stock_count": 6,
                "mainline_state_streak": 1,
            }},
        )
        leader = SelectionSignal("600003", "niu_leader", score=8.6)
        tape = SelectionReplayTape(frames={
            "2026-01-05": SelectionReplayFrame(
                date="2026-01-05",
                signals=(second, leader, top),
                scored={},
            ),
        })

        annotated = research._with_reversal_ranking_context(tape)
        signals = annotated.frames["2026-01-05"].signals
        second_scored = signals[0].metadata["scored"]
        top_scored = signals[2].metadata["scored"]

        self.assertEqual(signals[1], leader)
        self.assertEqual(second_scored["reversal_candidate_count"], 2)
        self.assertEqual(second_scored["reversal_candidate_rank"], 2)
        self.assertEqual(top_scored["reversal_candidate_rank"], 1)
        self.assertEqual(top_scored["reversal_top_score_gap"], 0.2)
        self.assertFalse(
            research.PRODUCTION_REVERSAL_SCORE_FILTERS[9.0](signals[0])
        )
        self.assertTrue(
            research.PRODUCTION_REVERSAL_SCORE_FILTERS[9.0](signals[2])
        )
        self.assertTrue(
            research.PRODUCTION_REVERSAL_CANDIDATE_COUNT_FILTERS[2](signals[2])
        )
        self.assertTrue(
            research.PRODUCTION_REVERSAL_TOP_GAP_FILTERS[0.2](signals[2])
        )

    def test_theme_ranking_context_is_historical_and_filterable(self):
        top = SelectionSignal(
            "600001",
            "niu_reversal_probe",
            score=9.0,
            metadata={"scored": {
                "industry": "银行",
                "mainline_score": 70.0,
                "mainline_state": "candidate",
                "stock_strong": False,
                "daily_v_recovery_ratio": 0.7,
                "strong_stock_count": 6,
                "mainline_state_streak": 1,
            }},
        )
        tied_second = SelectionSignal(
            "600002",
            "niu_reversal_probe",
            score=8.8,
            metadata={"scored": {
                "industry": "电子",
                "mainline_score": 68.0,
                "mainline_state": "candidate",
                "stock_strong": False,
                "daily_v_recovery_ratio": 0.7,
                "strong_stock_count": 6,
                "mainline_state_streak": 1,
            }},
        )
        missing = SelectionSignal(
            "600003",
            "niu_reversal_probe",
            score=8.7,
            metadata={"scored": {"industry": ""}},
        )
        tape = SelectionReplayTape(frames={
            "2026-01-05": SelectionReplayFrame(
                date="2026-01-05",
                signals=(tied_second, missing, top),
                scored={
                    "600001": {"niu_reversal_probe": {
                        "industry": "银行", "mainline_score": 70.0,
                    }},
                    "600002": {"niu_reversal_probe": {
                        "industry": "电子", "mainline_score": 68.0,
                    }},
                    "600004": {"niu_leader": {
                        "industry": "电力", "mainline_score": 68.0,
                    }},
                },
            ),
        })

        annotated = research._with_theme_ranking_context(tape)
        signals = annotated.frames["2026-01-05"].signals
        second_scored = signals[0].metadata["scored"]
        top_scored = signals[2].metadata["scored"]

        self.assertEqual(signals[1], missing)
        self.assertNotIn("mainline_theme_rank", missing.metadata["scored"])
        self.assertEqual(top_scored["mainline_theme_rank"], 1)
        self.assertEqual(top_scored["mainline_theme_count"], 3)
        self.assertEqual(top_scored["mainline_theme_score_gap_to_top"], 0.0)
        self.assertTrue(top_scored["mainline_theme_top5"])
        self.assertEqual(
            top_scored["mainline_theme_rank_scope"],
            "tracked_replay_themes",
        )
        self.assertEqual(second_scored["mainline_theme_rank"], 3)
        self.assertEqual(second_scored["mainline_theme_score_gap_to_top"], 2.0)
        self.assertFalse(
            research.LIFECYCLE_EARLY_THEME_TOP2_FILTER(signals[0])
        )
        self.assertTrue(
            research.LIFECYCLE_EARLY_THEME_TOP5_FILTER(signals[0])
        )
        self.assertTrue(
            research.LIFECYCLE_EARLY_THEME_TOP2_FILTER(signals[2])
        )
        self.assertEqual(
            annotated.frames["2026-01-05"].scored["600001"]
            ["niu_reversal_probe"]["mainline_theme_rank"],
            1,
        )
        self.assertEqual(
            annotated.frames["2026-01-05"].scored["600002"]
            ["niu_reversal_probe"]["mainline_theme_rank"],
            3,
        )
        self.assertNotIn(
            "mainline_theme_rank",
            tape.frames["2026-01-05"].signals[0].metadata["scored"],
        )
        self.assertNotIn(
            "mainline_theme_rank",
            tape.frames["2026-01-05"].scored["600001"]
            ["niu_reversal_probe"],
        )

    def test_theme_ranking_uses_full_cross_section_and_prior_session_momentum(self):
        signal = SelectionSignal(
            "600001",
            "niu_emerging",
            score=8.8,
            metadata={"scored": {
                "industry": "半导体",
                "mainline_score": 95.0,
            }},
        )
        tape = SelectionReplayTape(frames={
            "2026-01-06": SelectionReplayFrame(
                date="2026-01-06",
                signals=(signal,),
                scored={
                    "600001": {"niu_emerging": {
                        "industry": "半导体",
                        "mainline_score": 95.0,
                    }},
                },
                cross_section={
                    "银行": {"score": 100.0},
                    "半导体": {"score": 95.0},
                    "电子": {"score": 90.0},
                    "电力": {"score": 80.0},
                    "汽车": {"score": 70.0},
                    "机械": {"score": 60.0},
                },
            ),
            "2026-01-05": SelectionReplayFrame(
                date="2026-01-05",
                cross_section={
                    "银行": {"score": 100.0},
                    "电子": {"score": 90.0},
                    "电力": {"score": 80.0},
                    "汽车": {"score": 70.0},
                    "机械": {"score": 60.0},
                    "半导体": {"score": 0.0},
                },
            ),
        })

        annotated = research._with_theme_ranking_context(tape)
        scored = annotated.frames["2026-01-06"].signals[0].metadata["scored"]

        self.assertEqual(
            scored["mainline_theme_rank_scope"],
            "full_historical_theme_cross_section",
        )
        self.assertEqual(scored["mainline_theme_rank"], 2)
        self.assertEqual(scored["mainline_theme_previous_rank"], 6)
        self.assertEqual(scored["mainline_theme_rank_change"], 4)
        self.assertEqual(scored["mainline_theme_count"], 6)
        self.assertEqual(scored["mainline_theme_percentile"], 80.0)
        self.assertEqual(scored["mainline_theme_percentile_change"], 80.0)
        self.assertTrue(scored["mainline_theme_new_top5"])
        holding_scored = annotated.frames["2026-01-06"].scored
        self.assertEqual(
            holding_scored["600001"]["niu_emerging"]
            ["mainline_theme_rank"],
            2,
        )
        self.assertEqual(
            holding_scored["600001"]["niu_emerging"]
            ["mainline_theme_previous_rank"],
            6,
        )
        self.assertIs(
            annotated.frames["2026-01-06"].cross_section,
            tape.frames["2026-01-06"].cross_section,
        )
        self.assertNotIn(
            "mainline_theme_rank",
            tape.frames["2026-01-06"].signals[0].metadata["scored"],
        )
        self.assertNotIn(
            "mainline_theme_rank",
            tape.frames["2026-01-06"].scored["600001"]["niu_emerging"],
        )

    def test_markup_theme_momentum_filters_fail_closed_on_incomplete_rank_scope(self):
        def signal(change, scope="full_historical_theme_cross_section"):
            return SelectionSignal(
                "600001",
                "niu_emerging",
                score=8.8,
                metadata={"scored": {
                    "mainline_theme_rank_scope": scope,
                    "mainline_theme_percentile_change": change,
                }},
            )

        self.assertTrue(
            research.PRODUCTION_MARKUP_THEME_IMPROVING_FILTER(signal(1.0))
        )
        self.assertFalse(
            research.PRODUCTION_MARKUP_THEME_IMPROVING_FILTER(signal(0.0))
        )
        self.assertTrue(
            research.PRODUCTION_MARKUP_THEME_NON_DECLINING_FILTER(signal(0.0))
        )
        self.assertFalse(
            research.PRODUCTION_MARKUP_THEME_NON_DECLINING_FILTER(signal(-1.0))
        )
        self.assertFalse(
            research.PRODUCTION_MARKUP_THEME_IMPROVING_FILTER(
                signal(1.0, "tracked_replay_themes")
            )
        )
        self.assertFalse(
            research.PRODUCTION_MARKUP_THEME_NON_DECLINING_FILTER(signal(None))
        )
        self.assertTrue(
            research.STAGE_MARKUP_THEME_IMPROVING_FILTER(signal(1.0))
        )
        below_production_threshold = SelectionSignal(
            "600001",
            "niu_emerging",
            score=8.3,
            metadata=signal(1.0).metadata,
        )
        self.assertFalse(
            research.STAGE_MARKUP_THEME_IMPROVING_FILTER(
                below_production_threshold
            )
        )

    def test_markup_leadership_filters_apply_only_to_relevant_actions(self):
        def signal(strategy_id, **scored):
            return SelectionSignal(
                "600001",
                strategy_id,
                score=8.8,
                metadata={"scored": scored},
            )

        leader = signal(
            "niu_leader",
            mainline_state="mainline",
            mainline_confirmed=True,
            mainline_score=75.0,
            stock_sector_rank=80.0,
            stock_leader_rank=1,
            today_strength_score=60.0,
        )
        self.assertTrue(
            research.PRODUCTION_LEADER_RANK_80_STRENGTH_60_FILTER(leader)
        )
        weak_percentile = signal(
            "niu_leader",
            mainline_state="mainline",
            mainline_confirmed=True,
            mainline_score=75.0,
            stock_sector_rank=79.99,
            stock_leader_rank=1,
            today_strength_score=80.0,
        )
        self.assertFalse(
            research.PRODUCTION_LEADER_RANK_80_STRENGTH_60_FILTER(
                weak_percentile
            )
        )
        weak_theme = signal(
            "niu_leader",
            mainline_state="mainline",
            mainline_confirmed=True,
            mainline_score=75.0,
            stock_sector_rank=90.0,
            stock_leader_rank=1,
            today_strength_score=59.99,
        )
        self.assertFalse(
            research.PRODUCTION_LEADER_RANK_80_STRENGTH_60_FILTER(weak_theme)
        )
        self.assertTrue(research.PRODUCTION_MARKUP_QUALITY_FILTER(signal(
            "niu_emerging",
            mainline_state="emerging",
            mainline_cross_day_persistent=True,
        )))
        self.assertFalse(research.PRODUCTION_MARKUP_QUALITY_FILTER(signal(
            "niu_emerging",
            mainline_state="mainline",
            mainline_confirmed=True,
            mainline_score=75.0,
        )))
        self.assertTrue(research.PRODUCTION_MARKUP_QUALITY_FILTER(signal(
            "niu_pullback",
            mainline_state="diverging",
        )))

    def test_stage_emerging_transition_filters_relax_only_full_pit_breakouts(self):
        def signal(
            *,
            score=8.3,
            rank=4,
            rank_change=3,
            new_top5=True,
            setup="breakout",
            scope="full_historical_theme_cross_section",
            strategy_id="niu_emerging",
        ):
            return SelectionSignal(
                "600001",
                strategy_id,
                score=score,
                metadata={"scored": {
                    "mainline_theme_rank_scope": scope,
                    "mainline_theme_rank": rank,
                    "mainline_theme_rank_change": rank_change,
                    "mainline_theme_new_top5": new_top5,
                    "entry_setup": setup,
                }},
            )

        self.assertTrue(
            research.STAGE_EMERGING_NEW_TOP5_BREAKOUT_FILTER(signal())
        )
        self.assertTrue(
            research.STAGE_EMERGING_TOP5_IMPROVING_BREAKOUT_FILTER(signal())
        )
        self.assertFalse(
            research.STAGE_EMERGING_NEW_TOP5_BREAKOUT_FILTER(
                signal(new_top5=False)
            )
        )
        self.assertFalse(
            research.STAGE_EMERGING_TOP5_IMPROVING_BREAKOUT_FILTER(
                signal(rank=6)
            )
        )
        self.assertFalse(
            research.STAGE_EMERGING_TOP5_IMPROVING_BREAKOUT_FILTER(
                signal(rank_change=0)
            )
        )
        self.assertFalse(
            research.STAGE_EMERGING_NEW_TOP5_BREAKOUT_FILTER(
                signal(setup="reclaim")
            )
        )
        self.assertFalse(
            research.STAGE_EMERGING_NEW_TOP5_BREAKOUT_FILTER(
                signal(scope="tracked_replay_themes")
            )
        )
        self.assertTrue(
            research.STAGE_EMERGING_NEW_TOP5_BREAKOUT_FILTER(
                signal(score=8.5, setup="reclaim", scope="tracked_replay_themes")
            )
        )
        self.assertFalse(
            research.STAGE_EMERGING_NEW_TOP5_BREAKOUT_FILTER(
                signal(strategy_id="niu_leader", score=7.9)
            )
        )

    def test_stage_trajectory_summary_marks_post_entry_groups_descriptive(self):
        rows = [
            {
                "entry_date": "2026-01-06",
                "strategy_id": "niu_reversal_probe",
                "net_return_pct": 8.0,
                "holding_stage_path": ["emerging", "mainline"],
                "holding_stage_transition_count": 1,
                "first_cross_day_persistent_date": "2026-01-07",
                "first_mainline_confirmed_date": "2026-01-08",
                "first_strong_leader_date": "2026-01-08",
                "first_mainline_date": "2026-01-08",
                "first_diverging_date": "",
                "exit_mainline_state": "mainline",
            },
            {
                "entry_date": "2026-01-06",
                "strategy_id": "niu_reversal_probe",
                "net_return_pct": -2.0,
                "holding_stage_path": ["candidate"],
                "holding_stage_transition_count": 0,
                "first_cross_day_persistent_date": "",
                "first_mainline_confirmed_date": "",
                "first_strong_leader_date": "",
                "first_mainline_date": "",
                "first_diverging_date": "",
                "exit_mainline_state": "candidate",
            },
        ]

        summary = research._stage_trajectory_summary(rows)

        self.assertEqual(summary["interpretation"], "post_entry_descriptive_only")
        self.assertEqual(summary["coverage"]["holding_stage_path_count"], 2)
        self.assertEqual(
            summary["reversal_groups"]["strong_leader"]["observed"]["count"],
            1,
        )
        self.assertEqual(
            summary["reversal_groups"]["strong_leader"]["observed"]
            ["win_rate_pct"],
            100.0,
        )
        self.assertEqual(
            summary["reversal_groups"]["strong_leader"]["not_observed"]
            ["average_return_pct"],
            -2.0,
        )

    def test_holding_upgrade_replay_emits_next_session_strong_leader_signal(self):
        scored = {
            "score": 8.4,
            "mainline_state": "emerging",
            "mainline_cross_day_persistent": True,
            "mainline_confirmed": False,
            "stock_leader_tier": True,
            "stock_strong": True,
        }
        tape = SelectionReplayTape(frames={
            "2026-01-06": SelectionReplayFrame(
                date="2026-01-06",
                signals=(),
                scored={"600000": {"niu_emerging": scored}},
            ),
        })
        selector = research._HoldingUpgradeReplayStrategy(
            tape,
            holding_upgrade_mode="strong_leader",
        )
        selector.set_exit_tracking_symbols({
            "600000": {"strategy_id": "niu_reversal_probe"},
        })
        context = research.SelectionContext(
            date="2026-01-06",
            session_index=1,
            bars={},
            histories={},
        )

        signals = list(selector.on_close(context))

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].strategy_id, "niu_emerging")
        self.assertIs(signals[0].metadata["holding_upgrade"], True)

    def test_confirmed_markup_upgrade_excludes_divergence_stage(self):
        scored = {
            "mainline_state": "mainline",
            "mainline_confirmed": True,
            "stock_leader_tier": True,
            "stock_strong": True,
            "niuone_lifecycle_stage": "markup",
        }

        self.assertEqual(
            research._holding_upgrade_strategy_id(
                "niu_reversal_probe",
                scored,
                "confirmed_markup",
            ),
            "niu_leader",
        )
        scored["niuone_lifecycle_stage"] = "divergence"
        self.assertEqual(
            research._holding_upgrade_strategy_id(
                "niu_reversal_probe",
                scored,
                "confirmed_markup",
            ),
            "",
        )

    def test_staged_markup_upgrade_accepts_early_and_confirmed_markup(self):
        early = {
            "mainline_state": "emerging",
            "mainline_cross_day_persistent": True,
            "mainline_confirmed": False,
            "stock_leader_tier": True,
            "stock_strong": True,
            "niuone_lifecycle_stage": "markup",
        }
        confirmed = {
            **early,
            "mainline_state": "mainline",
            "mainline_confirmed": True,
        }

        self.assertEqual(
            research._holding_upgrade_strategy_id(
                "niu_reversal_probe", early, "staged_markup",
            ),
            "niu_emerging",
        )
        self.assertEqual(
            research._holding_upgrade_strategy_id(
                "niu_emerging", confirmed, "staged_markup",
            ),
            "niu_leader",
        )
        early["niuone_lifecycle_stage"] = "divergence"
        self.assertEqual(
            research._holding_upgrade_strategy_id(
                "niu_reversal_probe", early, "staged_markup",
            ),
            "",
        )

    def test_staged_markup_replay_enumerates_early_markup_candidate(self):
        scored = {
            "score": 8.4,
            "mainline_state": "emerging",
            "mainline_cross_day_persistent": True,
            "mainline_confirmed": False,
            "stock_leader_tier": True,
            "stock_strong": True,
            "niuone_lifecycle_stage": "markup",
        }
        tape = SelectionReplayTape(frames={
            "2026-01-06": SelectionReplayFrame(
                date="2026-01-06",
                signals=(),
                scored={"600000": {"niu_emerging": scored}},
            ),
        })
        selector = research._HoldingUpgradeReplayStrategy(
            tape,
            holding_upgrade_mode="staged_markup",
            holding_upgrade_min_pnl_pct=2.0,
            holding_upgrade_max_pnl_pct=10.0,
        )
        selector.set_exit_tracking_symbols({
            "600000": {
                "strategy_id": "niu_reversal_probe",
                "avg_cost": 10.0,
            },
        })
        context = research.SelectionContext(
            date="2026-01-06",
            session_index=1,
            bars={"600000": HistoricalBar.from_value("600000", {
                "date": "2026-01-06",
                "open": 10.4,
                "high": 10.6,
                "low": 10.3,
                "close": 10.5,
                "volume": 1000,
            })},
            histories={},
        )

        signals = list(selector.on_close(context))

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].strategy_id, "niu_emerging")

    def test_holding_upgrade_replay_does_not_duplicate_source_signal(self):
        scored = {
            "score": 8.4,
            "mainline_state": "emerging",
            "mainline_cross_day_persistent": True,
            "mainline_confirmed": False,
            "stock_leader_tier": True,
            "stock_strong": True,
        }
        source_signal = SelectionSignal(
            "600000",
            "niu_emerging",
            score=8.4,
            metadata={"scored": scored},
        )
        tape = SelectionReplayTape(frames={
            "2026-01-06": SelectionReplayFrame(
                date="2026-01-06",
                signals=(source_signal,),
                scored={"600000": {"niu_emerging": scored}},
            ),
        })
        selector = research._HoldingUpgradeReplayStrategy(
            tape,
            holding_upgrade_mode="strong_leader",
        )
        selector.set_exit_tracking_symbols({
            "600000": {"strategy_id": "niu_reversal_probe"},
        })
        context = research.SelectionContext(
            date="2026-01-06",
            session_index=1,
            bars={},
            histories={},
        )

        signals = list(selector.on_close(context))

        self.assertEqual(signals, [source_signal])

    def test_holding_upgrade_replay_requires_current_profit_when_configured(self):
        scored = {
            "score": 8.4,
            "mainline_state": "emerging",
            "mainline_cross_day_persistent": True,
            "mainline_confirmed": False,
            "stock_leader_tier": True,
            "stock_strong": True,
        }
        tape = SelectionReplayTape(frames={
            "2026-01-06": SelectionReplayFrame(
                date="2026-01-06",
                signals=(),
                scored={"600000": {"niu_emerging": scored}},
            ),
        })
        selector = research._HoldingUpgradeReplayStrategy(
            tape,
            holding_upgrade_mode="strong_leader",
            holding_upgrade_min_pnl_pct=2.0,
        )
        selector.set_exit_tracking_symbols({
            "600000": {
                "strategy_id": "niu_reversal_probe",
                "avg_cost": 10.0,
            },
        })
        context = research.SelectionContext(
            date="2026-01-06",
            session_index=1,
            bars={"600000": HistoricalBar.from_value("600000", {
                "date": "2026-01-06",
                "open": 10.0,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "volume": 1000,
            })},
            histories={},
        )

        signals = list(selector.on_close(context))

        self.assertEqual(signals, [])

    def test_holding_upgrade_replay_rejects_late_profit_when_configured(self):
        scored = {
            "score": 8.4,
            "mainline_state": "emerging",
            "mainline_cross_day_persistent": True,
            "mainline_confirmed": False,
            "stock_leader_tier": True,
            "stock_strong": True,
            "niuone_lifecycle_stage": "markup",
        }
        tape = SelectionReplayTape(frames={
            "2026-01-06": SelectionReplayFrame(
                date="2026-01-06",
                signals=(),
                scored={"600000": {"niu_emerging": scored}},
            ),
        })
        selector = research._HoldingUpgradeReplayStrategy(
            tape,
            holding_upgrade_mode="staged_markup",
            holding_upgrade_min_pnl_pct=2.0,
            holding_upgrade_max_pnl_pct=10.0,
        )
        selector.set_exit_tracking_symbols({
            "600000": {
                "strategy_id": "niu_reversal_probe",
                "avg_cost": 10.0,
            },
        })
        context = research.SelectionContext(
            date="2026-01-06",
            session_index=1,
            bars={"600000": HistoricalBar.from_value("600000", {
                "date": "2026-01-06",
                "open": 11.1,
                "high": 11.3,
                "low": 11.0,
                "close": 11.2,
                "volume": 1000,
            })},
            histories={},
        )

        self.assertEqual(list(selector.on_close(context)), [])

    def test_full_theme_holding_upgrade_modes_require_complete_pit_rank(self):
        base = {
            "mainline_state": "emerging",
            "mainline_cross_day_persistent": True,
            "mainline_theme_rank_scope": (
                "full_historical_theme_cross_section"
            ),
            "mainline_theme_rank": 5,
            "mainline_theme_new_top5": True,
        }

        self.assertEqual(
            research._holding_upgrade_strategy_id(
                "niu_reversal_probe",
                base,
                "full_theme_top5_persistent",
            ),
            "niu_emerging",
        )
        self.assertEqual(
            research._holding_upgrade_strategy_id(
                "niu_reversal_probe",
                base,
                "full_theme_new_top5_persistent",
            ),
            "niu_emerging",
        )
        self.assertEqual(
            research._holding_upgrade_strategy_id(
                "niu_reversal_probe",
                {**base, "mainline_theme_new_top5": False},
                "full_theme_new_top5_persistent",
            ),
            "",
        )
        self.assertEqual(
            research._holding_upgrade_strategy_id(
                "niu_reversal_probe",
                {**base, "mainline_theme_rank_scope": "tracked_replay_themes"},
                "full_theme_top5_persistent",
            ),
            "",
        )

    def test_replay_cache_round_trip_preserves_bars_signals_and_metadata(self):
        bar = HistoricalBar.from_value("600000", {
            "date": "2026-01-05",
            "open": 10.0,
            "high": 10.5,
            "low": 9.8,
            "close": 10.2,
            "volume": 1000,
            "industry": "银行",
        })
        signal = SelectionSignal(
            symbol="600000",
            strategy_id="niu_reversal_probe",
            reason="fixture",
            score=8.5,
            metadata={"scored": {"mainline_state": "candidate"}},
        )
        tape = SelectionReplayTape(
            frames={
                bar.date: SelectionReplayFrame(
                    date=bar.date,
                    signals=(signal,),
                    scored={
                        bar.symbol: {
                            signal.strategy_id: {
                                "mainline_state": "candidate",
                            }
                        }
                    },
                    cross_section={
                        "银行": {"score": 72.5, "state": "mainline"},
                    },
                )
            },
            diagnostics={"warnings": ["fixture warning"]},
        )
        metadata = {
            "source": "tencent",
            "adjustment": "qfq",
            "signal_generation_start": "2026-01-05",
            "signal_generation_end": "2026-01-05",
        }

        with tempfile.TemporaryDirectory(prefix="niuone-replay-") as directory:
            path = Path(directory) / "replay.jsonl.gz"
            research._write_replay_cache(
                path,
                bars={bar.symbol: {bar.date: bar}},
                tape=tape,
                metadata=metadata,
            )
            loaded_bars, loaded_tape, loaded_metadata = (
                research._load_replay_cache(path)
            )

        self.assertEqual(loaded_metadata, metadata)
        self.assertEqual(loaded_bars[bar.symbol][0].close, 10.2)
        loaded_frame = loaded_tape.frames[bar.date]
        self.assertEqual(loaded_frame.signals[0].strategy_id, signal.strategy_id)
        self.assertEqual(
            loaded_frame.scored[bar.symbol][signal.strategy_id]["mainline_state"],
            "candidate",
        )
        self.assertEqual(
            loaded_frame.cross_section["银行"],
            {"score": 72.5, "state": "mainline"},
        )
        self.assertEqual(loaded_tape.diagnostics["warnings"], ["fixture warning"])

    def test_legacy_replay_cache_without_cross_section_remains_readable(self):
        records = (
            {
                "kind": "metadata",
                "format": research.REPLAY_CACHE_FORMAT,
                "metadata": {"source": "tencent"},
            },
            {
                "kind": "bars",
                "symbol": "600000",
                "rows": [{
                    "date": "2026-01-05",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 1000,
                }],
            },
            {
                "kind": "frame",
                "date": "2026-01-05",
                "signals": [],
                "scored": {},
            },
            {"kind": "diagnostics", "value": {}},
        )
        with tempfile.TemporaryDirectory(prefix="niuone-legacy-replay-") as directory:
            path = Path(directory) / "replay.jsonl.gz"
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record) + "\n")
            _bars, tape, _metadata = research._load_replay_cache(path)

        self.assertEqual(tape.frames["2026-01-05"].cross_section, {})

    def test_replay_cache_rejects_unknown_format(self):
        with tempfile.TemporaryDirectory(prefix="niuone-replay-") as directory:
            path = Path(directory) / "replay.jsonl.gz"
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "kind": "metadata",
                    "format": "unknown",
                    "metadata": {},
                }) + "\n")

            with self.assertRaisesRegex(ValueError, "unsupported"):
                research._load_replay_cache(path)


if __name__ == "__main__":
    unittest.main()
