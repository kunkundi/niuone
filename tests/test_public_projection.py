from __future__ import annotations

import unittest

from app.dashboard.public_projection import PUBLIC_SCHEMA_VERSION, build_public_sections


class PublicProjectionTests(unittest.TestCase):
    def test_projection_uses_allow_lists_and_removes_private_paths(self) -> None:
        sections = build_public_sections(
            {
                "generated_at": "2026-07-21 10:00:00",
                "initial_cash": 1_000_000,
                "cash": 400_000,
                "total_equity": 1_030_000,
                "last_error": "/private/runtime/state.json: provider token=secret",
                "positions": [{"code": "600000", "name": "浦发银行", "qty": 100, "secret_note": "private"}],
                "equity_history": [{"time": "2026-07-21 10:00:00", "equity": 1_030_000, "internal_id": 7}],
            },
            messages={
                "dashboard_home": "/private/runtime",
                "db_path": "/private/runtime/push_history.db",
                "total": 1,
                "records": [{"id": 1, "content": "公开摘要", "raw_payload": "secret"}],
            },
            candidates={
                "generated_at": "2026-07-21 10:00:00",
                "running": True,
                "strategy_meta": {
                    "trend_pullback": {
                        "label": "趋势回踩",
                        "color": "#60a5fa",
                        "private_rule": "secret",
                    }
                },
                "strategy_distribution": {"trend_pullback": 2},
                "items": [{
                    "code": "600000",
                    "best_strategy": "trend_pullback",
                    "best_score": 8.5,
                    "score_before_industry_flow": 8.0,
                    "industry_flow_rank": 2,
                    "industry_flow_adjustment": 0.55,
                    "industry_flow_matched": True,
                    "hard_blockers": ["停牌"],
                    "private_note": "secret",
                }],
            },
            market_summary={
                "available": True,
                "summary": "实时指数与资金结构平衡。",
                "tone_label": "平衡",
                "generated_at": "2026-07-21 10:00:05",
                "stage": "completed",
                "model_error": "private provider detail",
            },
        )

        self.assertEqual(sections["metadata"]["schema_version"], PUBLIC_SCHEMA_VERSION)
        self.assertTrue(sections["metadata"]["degraded"])
        self.assertNotIn("generated_at", sections["metadata"])
        self.assertNotIn("last_error", sections["metadata"])
        self.assertNotIn("secret_note", sections["account"]["positions"][0])
        self.assertNotIn("internal_id", sections["history"]["intraday"][0])
        self.assertNotIn("dashboard_home", sections["messages"])
        self.assertNotIn("db_path", sections["messages"])
        self.assertNotIn("raw_payload", sections["messages"]["records"][0])
        self.assertTrue(sections["candidates"]["running"])
        self.assertEqual(sections["candidates"]["items"][0]["best_score"], 8.5)
        self.assertEqual(sections["candidates"]["items"][0]["industry_flow_rank"], 2)
        self.assertEqual(sections["candidates"]["items"][0]["industry_flow_adjustment"], 0.55)
        self.assertEqual(sections["candidates"]["items"][0]["hard_blockers"], ["停牌"])
        self.assertNotIn("private_note", sections["candidates"]["items"][0])
        self.assertEqual(
            sections["candidates"]["strategy_meta"]["trend_pullback"],
            {"label": "趋势回踩", "color": "#60a5fa"},
        )
        self.assertEqual(
            sections["candidates"]["strategy_distribution"],
            {"trend_pullback": 2},
        )
        self.assertNotIn("generated_at", sections["messages"])
        self.assertEqual(sections["market_summary"]["tone_label"], "平衡")
        self.assertEqual(sections["market_summary"]["generated_at"], "2026-07-21 10:00:05")
        self.assertEqual(sections["market_summary"]["status"], "completed")
        self.assertNotIn("model_error", sections["market_summary"])
        serialized = repr(sections)
        self.assertNotIn("/private/runtime", serialized)
        self.assertNotIn("token=secret", serialized)

    def test_projection_bounds_large_history_and_activity(self) -> None:
        practice = {
            "equity_history": [{"time": str(index), "equity": index} for index in range(1_000)],
            "daily_equity_history": [{"time": str(index), "equity": index} for index in range(1_000)],
            "trade_log": [{"time": str(index), "action": "BUY"} for index in range(100)],
            "decision_log": [{"time": str(index), "decision": {"summary": "hold", "actions": []}} for index in range(100)],
        }

        sections = build_public_sections(practice)

        self.assertEqual(len(sections["history"]["intraday"]), 360)
        self.assertEqual(sections["history"]["intraday"][0]["time"], "640")
        self.assertEqual(len(sections["history"]["daily"]), 520)
        self.assertEqual(len(sections["activity"]["trades"]), 50)
        self.assertEqual(len(sections["activity"]["decisions"]), 30)


if __name__ == "__main__":
    unittest.main()
