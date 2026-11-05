import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "compat"))

from app.dashboard import server as dashboard


class HoldingFastCycleFreshnessTests(unittest.TestCase):
    def run_cycle(self, *, after_summary, states=None, allowed=None):
        events = []
        states = states or [
            {"positions": {"600001": {"qty": 100}}},
            {"positions": {"600002": {"qty": 200}}},
        ]
        trader = SimpleNamespace(
            load_state=Mock(side_effect=states),
            is_a_share_execution_time=Mock(side_effect=allowed or [
                (True, "open"), (True, "open"),
            ]),
        )
        summary = {"available": True, "summary": "fresh summary"}

        def refresh(_trigger):
            events.append("summary")
            return summary

        def build(holdings, context, *, now):
            events.append("quotes_and_scores")
            self.assertEqual(now, after_summary)
            return {"generated_at": now.strftime("%Y-%m-%d %H:%M:%S")}

        builder = Mock(side_effect=build)
        decision = Mock(return_value={"executed": []})
        with (
            patch.object(dashboard, "PRACTICE_FAST_CYCLE_ENABLED", True),
            patch.object(dashboard, "refresh_practice_market_summary_for_decision", side_effect=refresh) as refresh_mock,
            patch.object(dashboard, "practice_fast_cycle_context_payload", return_value={}),
            patch.object(dashboard, "run_practice_decision_logged", decision),
            patch.object(dashboard, "invalidate_api_cache"),
        ):
            result = dashboard.run_practice_fast_cycle_once(
                datetime(2026, 11, 5, 11, 25), trader=trader,
                payload_builder=builder, clock=lambda: after_summary,
            )
        return result, events, builder, decision, refresh_mock, trader

    def test_summary_precedes_fresh_signal_and_positions_are_reloaded(self):
        fresh = datetime(2026, 11, 5, 11, 29)
        result, events, builder, decision, refresh, trader = self.run_cycle(after_summary=fresh)
        self.assertEqual(events, ["summary", "quotes_and_scores"])
        self.assertEqual(builder.call_args.args[0], [{"code": "600002", "qty": 200}])
        payload = decision.call_args.args[0]
        self.assertEqual(payload["generated_at"], "2026-11-05 11:29:00")
        self.assertEqual(payload["holding_cycle_codes"], ["600002"])
        self.assertTrue(payload["market_summary"]["available"])
        self.assertFalse(decision.call_args.kwargs["refresh_market_summary"])
        self.assertEqual(trader.load_state.call_count, 2)
        refresh.assert_called_once()
        self.assertEqual(result["decision_cycle_kind"], "holding_fast")

    def test_summary_crossing_session_close_does_not_build_or_decide(self):
        result, _, builder, decision, _, _ = self.run_cycle(
            after_summary=datetime(2026, 11, 5, 11, 31),
            allowed=[(True, "open"), (False, "lunch")],
        )
        self.assertEqual(result["reason"], "outside_a_share_execution_time")
        builder.assert_not_called()
        decision.assert_not_called()

    def test_positions_closed_during_summary_do_not_use_old_holdings(self):
        result, _, builder, decision, _, _ = self.run_cycle(
            after_summary=datetime(2026, 11, 5, 11, 29),
            states=[{"positions": {"600001": {"qty": 100}}}, {"positions": {}}],
        )
        self.assertEqual(result["reason"], "no_open_positions")
        builder.assert_not_called()
        decision.assert_not_called()

    def test_empty_account_does_not_request_summary(self):
        result, _, builder, decision, refresh, _ = self.run_cycle(
            after_summary=datetime(2026, 11, 5, 11, 29), states=[{"positions": {}}],
        )
        self.assertEqual(result["reason"], "no_open_positions")
        refresh.assert_not_called()
        builder.assert_not_called()
        decision.assert_not_called()


if __name__ == "__main__":
    unittest.main()
