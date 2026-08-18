#!/usr/bin/env python3
import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
COMPAT = SRC / "compat"
ENTRYPOINTS = SRC / "entrypoints"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
    sys.path.insert(0, str(COMPAT))

_tmp_home = tempfile.TemporaryDirectory()
os.environ.setdefault("DASHBOARD_HOME", _tmp_home.name)

import niuniu_practice_trader as trader  # noqa: E402


@contextlib.contextmanager
def patched(**updates):
    originals = {name: getattr(trader, name) for name in updates}
    try:
        for name, value in updates.items():
            setattr(trader, name, value)
        yield
    finally:
        for name, value in originals.items():
            setattr(trader, name, value)


def sample_sell() -> dict:
    return {
        "time": "2026-07-11 10:00:00",
        "action": "SELL",
        "code": "600000",
        "name": "浦发银行",
        "shares": 100,
        "price": 10.5,
        "amount": 1050.0,
        "fee": 0.54,
        "pnl": 49.46,
        "pnl_pct": 4.95,
        "reason": "测试卖出",
    }


class TradeNotificationHookTests(unittest.TestCase):
    def test_rejected_fill_is_not_returned_to_notification_dispatcher(self):
        active = sample_sell()
        rejected = {
            **sample_sell(),
            "time": "2026-07-11 10:01:00",
            "accounting_status": "rejected",
            "accounting_rejected": True,
        }
        dispatched = []
        original = sys.modules.get("notifications")
        sys.modules["notifications"] = types.SimpleNamespace(
            notify_trade_executions=lambda trades: dispatched.append(trades) or []
        )
        try:
            trader._notify_trade_executions_safely([rejected, active])
        finally:
            if original is None:
                sys.modules.pop("notifications", None)
            else:
                sys.modules["notifications"] = original

        self.assertEqual(dispatched, [[active]])

    def test_auto_exit_notifies_only_after_state_is_saved(self):
        events = []
        executed = [sample_sell()]

        def check_auto_exits(state, _dt):
            state.setdefault("trade_log", []).extend(executed)
            state.setdefault("decision_log", []).append({
                "time": "2026-07-11 10:00:00",
                "decision": {"summary": "测试自动离场"},
                "executed": executed,
            })
            return executed

        with patched(
            load_state=lambda: {
                "positions": {},
                "trade_log": [],
                "decision_log": [],
                "cash": 1000.0,
            },
            refresh_realtime_prices=lambda state: None,
            refresh_position_intraday=lambda state: None,
            _refresh_position_bbi=lambda state, dt=None: None,
            check_auto_exits=check_auto_exits,
            record_equity=lambda state: None,
            save_state=lambda state: events.append("save"),
            _sync_decision_to_db=lambda entry: events.append("decision") or True,
            _sync_trades_to_db=lambda trades: events.append("trades") or True,
            _sync_positions_to_db=lambda state: events.append("positions"),
            _notify_trade_executions_safely=lambda trades: events.append(("notify", trades)),
            enrich_portfolio=lambda state: {},
        ):
            result = trader.run_auto_exits_once(datetime(2026, 7, 11, 10, 0))

        self.assertEqual(result["executed"], executed)
        self.assertEqual(
            events,
            ["save", "decision", "trades", "positions", ("notify", executed)],
        )

    def test_auto_exit_with_no_fill_does_not_notify(self):
        events = []
        with patched(
            load_state=lambda: {"positions": {}, "trade_log": [], "cash": 1000.0},
            refresh_realtime_prices=lambda state: None,
            refresh_position_intraday=lambda state: None,
            _refresh_position_bbi=lambda state, dt=None: None,
            check_auto_exits=lambda state, dt: [],
            record_equity=lambda state: None,
            save_state=lambda state: events.append("save"),
            _notify_trade_executions_safely=lambda trades: events.append("notify"),
            enrich_portfolio=lambda state: {},
        ):
            trader.run_auto_exits_once(datetime(2026, 7, 11, 10, 0))

        self.assertEqual(events, ["save"])

    def test_deferred_fill_notifies_once_after_state_is_saved(self):
        events = []
        executed = [sample_sell()]
        state = {
            "cash": 1000.0,
            "positions": {},
            "trade_log": [],
            "decision_log": [],
            "pending_decisions": [{
                "id": "pending-1",
                "status": "pending",
                "due_at": "",
                "decision": {"summary": "延迟测试", "actions": []},
                "candidates": [],
                "schedule_slot": "2026-07-11 09:25",
            }],
        }
        with patched(
            is_a_share_execution_time=lambda now=None: (True, "连续竞价交易时段"),
            load_state=lambda: state,
            current_market_strategy_context=lambda: {},
            refine_overlimit_buy_actions=lambda *args, **kwargs: {},
            execute_actions=lambda *args, **kwargs: executed,
            enrich_portfolio=lambda value: {},
            _sync_decision_to_db=lambda entry: events.append("decision") or True,
            _sync_trades_to_db=lambda trades: events.append("trades") or True,
            _sync_positions_to_db=lambda value: events.append("positions"),
            record_equity=lambda value: None,
            save_state=lambda value: events.append("save"),
            _notify_trade_executions_safely=lambda trades: events.append(("notify", trades)),
        ):
            result = trader.execute_due_pending_decisions(datetime(2026, 7, 11, 13, 0))

        self.assertEqual(result["executed"], executed)
        self.assertEqual(
            events,
            ["save", "decision", "trades", "positions", ("notify", executed)],
        )

    def test_model_fill_notifies_once_after_state_is_saved(self):
        events = []
        executed = [sample_sell()]
        state = {
            "cash": 1000.0,
            "positions": {"600000": {"qty": 100, "avg_cost": 10.0, "last_price": 10.5}},
            "trade_log": [],
            "decision_log": [],
            "equity_history": [],
        }
        decision = {"summary": "测试决策", "actions": [{"action": "SELL", "code": "600000", "shares": 100}]}
        market_context = {
            "tone_label": "中性",
            "max_open_positions": 6,
            "max_new_buys_per_decision": 2,
            "allow_new_buys": True,
        }
        with patched(
            load_state=lambda: state,
            market_strategy_context_for_b1=lambda payload: market_context,
            compact_market_strategy_context=lambda value: value,
            run_position_exit_checks_before_decision=lambda state, dt=None: [],
            check_daily_loss_budget=lambda value: (False, 0.0),
            get_adaptive_params=lambda: {},
            is_a_share_execution_time=lambda now=None: (True, "连续竞价交易时段"),
            check_market_environment=lambda: {"bullish": True},
            check_market_sentiment=lambda: {"sentiment": "neutral", "detail": ""},
            enrich_portfolio=lambda value: {},
            call_model_decision=lambda *args, **kwargs: decision,
            refine_overlimit_buy_actions=lambda *args, **kwargs: {},
            execute_actions=lambda *args, **kwargs: executed,
            _sync_decision_to_db=lambda entry: events.append("decision") or True,
            _sync_trades_to_db=lambda trades: events.append("trades") or True,
            _sync_positions_to_db=lambda value: events.append("positions"),
            record_equity=lambda value: None,
            save_state=lambda value: events.append("save"),
            _notify_trade_executions_safely=lambda trades: events.append(("notify", trades)),
        ):
            result = trader.run_decision_after_b1({"generated_at": "2026-07-11 10:00:00"}, force=True)

        self.assertEqual(result["executed"], executed)
        self.assertEqual(
            events,
            ["save", "decision", "trades", "positions", ("notify", executed)],
        )

    def test_model_fill_state_failure_does_not_project_or_notify(self):
        events = []
        executed = [sample_sell()]
        state = {
            "cash": 1000.0,
            "positions": {
                "600000": {"qty": 100, "avg_cost": 10.0, "last_price": 10.5}
            },
            "trade_log": [],
            "decision_log": [],
            "equity_history": [],
        }

        def fail_save(_state):
            events.append("save")
            raise PermissionError("state file is not writable")

        with patched(
            load_state=lambda: state,
            market_strategy_context_for_b1=lambda payload: {
                "tone_label": "中性",
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "allow_new_buys": True,
            },
            compact_market_strategy_context=lambda value: value,
            run_position_exit_checks_before_decision=lambda state, dt=None: [],
            check_daily_loss_budget=lambda value: (False, 0.0),
            get_adaptive_params=lambda: {},
            is_a_share_execution_time=lambda now=None: (True, "连续竞价交易时段"),
            check_market_environment=lambda: {"bullish": True},
            check_market_sentiment=lambda: {"sentiment": "neutral", "detail": ""},
            enrich_portfolio=lambda value: {},
            call_model_decision=lambda *args, **kwargs: {
                "summary": "测试决策",
                "actions": [{"action": "SELL", "code": "600000", "shares": 100}],
            },
            refine_overlimit_buy_actions=lambda *args, **kwargs: {},
            execute_actions=lambda *args, **kwargs: executed,
            _sync_decision_to_db=lambda entry: events.append("decision") or True,
            _sync_trades_to_db=lambda trades: events.append("trades") or True,
            _sync_positions_to_db=lambda value: events.append("positions"),
            record_equity=lambda value: None,
            save_state=fail_save,
            _notify_trade_executions_safely=lambda trades: events.append("notify"),
        ):
            with self.assertRaises(PermissionError):
                trader.run_decision_after_b1(
                    {"generated_at": "2026-07-11 10:00:00"},
                    force=True,
                )

        self.assertEqual(events, ["save"])

    def test_auto_exit_state_failure_does_not_project_or_notify(self):
        events = []
        executed = [sample_sell()]

        def check_auto_exits(state, _dt):
            state.setdefault("trade_log", []).extend(executed)
            state.setdefault("decision_log", []).append({
                "time": "2026-07-11 10:00:00",
                "decision": {"summary": "测试自动离场"},
                "executed": executed,
            })
            return executed

        def fail_save(_state):
            events.append("save")
            raise PermissionError("state file is not writable")

        with patched(
            load_state=lambda: {
                "positions": {},
                "trade_log": [],
                "decision_log": [],
                "cash": 1000.0,
            },
            refresh_realtime_prices=lambda state: None,
            refresh_position_intraday=lambda state: None,
            _refresh_position_bbi=lambda state, dt=None: None,
            check_auto_exits=check_auto_exits,
            record_equity=lambda state: None,
            save_state=fail_save,
            _sync_decision_to_db=lambda entry: events.append("decision") or True,
            _sync_trades_to_db=lambda trades: events.append("trades") or True,
            _sync_positions_to_db=lambda state: events.append("positions"),
            _notify_trade_executions_safely=lambda trades: events.append("notify"),
            enrich_portfolio=lambda state: {},
        ):
            with self.assertRaises(PermissionError):
                trader.run_auto_exits_once(datetime(2026, 7, 11, 10, 0))

        self.assertEqual(events, ["save"])

    def test_deferred_fill_state_failure_does_not_project_or_notify(self):
        events = []
        state = {
            "cash": 1000.0,
            "positions": {},
            "trade_log": [],
            "decision_log": [],
            "pending_decisions": [{
                "id": "pending-1",
                "status": "pending",
                "due_at": "",
                "decision": {"summary": "延迟测试", "actions": []},
                "candidates": [],
                "schedule_slot": "2026-07-11 09:25",
            }],
        }

        def fail_save(_state):
            events.append("save")
            raise PermissionError("state file is not writable")

        with patched(
            is_a_share_execution_time=lambda now=None: (True, "连续竞价交易时段"),
            load_state=lambda: state,
            current_market_strategy_context=lambda: {},
            refine_overlimit_buy_actions=lambda *args, **kwargs: {},
            execute_actions=lambda *args, **kwargs: [sample_sell()],
            enrich_portfolio=lambda value: {},
            _sync_decision_to_db=lambda entry: events.append("decision") or True,
            _sync_trades_to_db=lambda trades: events.append("trades") or True,
            _sync_positions_to_db=lambda value: events.append("positions"),
            record_equity=lambda value: None,
            save_state=fail_save,
            _notify_trade_executions_safely=lambda trades: events.append("notify"),
        ):
            with self.assertRaises(PermissionError):
                trader.execute_due_pending_decisions(
                    datetime(2026, 7, 11, 13, 0)
                )

        self.assertEqual(events, ["save"])

    def test_decision_log_state_failure_does_not_project(self):
        events = []

        def fail_save(_state):
            events.append("save")
            raise PermissionError("state file is not writable")

        with patched(
            load_state=lambda: {"decision_log": []},
            save_state=fail_save,
            _sync_decision_to_db=lambda entry: events.append("decision") or True,
        ):
            with self.assertRaises(PermissionError):
                trader.record_decision_log_entry({
                    "time": "2026-07-11 10:00:00",
                    "b1_generated_at": "",
                    "decision": {"summary": "只记录决策"},
                    "executed": [],
                })

        self.assertEqual(events, ["save"])

    def test_position_exit_check_runs_before_model_even_without_candidates(self):
        events = []
        state = {
            "cash": 1000.0,
            "positions": {"600000": {"qty": 100, "avg_cost": 10.0, "last_price": 9.8}},
            "trade_log": [],
            "decision_log": [],
            "equity_history": [],
        }

        def local_exit_check(value, dt=None):
            events.append("local_exit")
            return []

        def model_decision(*args, **kwargs):
            events.append("model")
            self.assertEqual(args[0], [])
            return {"summary": "检查原策略退出", "actions": []}

        with patched(
            load_state=lambda: state,
            market_strategy_context_for_b1=lambda payload: {
                "tone_label": "中性",
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "allow_new_buys": True,
            },
            compact_market_strategy_context=lambda value: value,
            run_position_exit_checks_before_decision=local_exit_check,
            check_daily_loss_budget=lambda value: (False, 0.0),
            get_adaptive_params=lambda: {},
            is_a_share_execution_time=lambda now=None: (True, "连续竞价交易时段"),
            check_market_environment=lambda: {"bullish": True},
            check_market_sentiment=lambda: {"sentiment": "neutral", "detail": ""},
            enrich_portfolio=lambda value: {"positions": []},
            call_model_decision=model_decision,
            refine_overlimit_buy_actions=lambda *args, **kwargs: {},
            execute_actions=lambda *args, **kwargs: [],
            _sync_decision_to_db=lambda entry: None,
            record_equity=lambda value: None,
            save_state=lambda value: None,
        ):
            result = trader.run_decision_after_b1({"generated_at": "2026-07-11 10:00:00", "items": []}, force=True)

        self.assertEqual(events, ["local_exit", "model"])
        self.assertEqual(result["executed"], [])

    def test_daily_loss_budget_blocks_buy_but_keeps_sell_decision(self):
        state = {
            "cash": 1000.0,
            "positions": {"600000": {"qty": 100, "avg_cost": 10.0, "last_price": 9.5}},
            "trade_log": [],
            "decision_log": [],
            "equity_history": [],
        }
        captured = {}
        sell = sample_sell()

        def model_decision(candidates, portfolio, trade_allowed, trade_reason, market_ctx):
            captured["model_called"] = True
            captured["trade_reason"] = trade_reason
            captured["market_ctx"] = dict(market_ctx)
            return {
                "summary": "停止开仓但卖出风险仓",
                "actions": [
                    {"action": "BUY", "code": "600001", "shares": 100},
                    {"action": "SELL", "code": "600000", "shares": 100},
                ],
            }

        def execute(value, decision, candidates, allowed, reason, market_ctx):
            captured["actions_at_execution"] = decision["actions"]
            captured["execution_ctx"] = dict(market_ctx)
            return [sell]

        with patched(
            load_state=lambda: state,
            market_strategy_context_for_b1=lambda payload: {
                "tone_label": "防守",
                "max_open_positions": 6,
                "max_new_buys_per_decision": 2,
                "allow_new_buys": True,
            },
            compact_market_strategy_context=lambda value: value,
            run_position_exit_checks_before_decision=lambda state, dt=None: [],
            check_daily_loss_budget=lambda value: (True, -3.0),
            get_adaptive_params=lambda: {},
            is_a_share_execution_time=lambda now=None: (True, "连续竞价交易时段"),
            check_market_environment=lambda: {"bullish": False},
            check_market_sentiment=lambda: {"sentiment": "cold", "detail": "test"},
            enrich_portfolio=lambda value: {"positions": []},
            call_model_decision=model_decision,
            execute_actions=execute,
            _sync_decision_to_db=lambda entry: None,
            _sync_trades_to_db=lambda trades: None,
            _sync_positions_to_db=lambda value: None,
            record_equity=lambda value: None,
            save_state=lambda value: None,
            _notify_trade_executions_safely=lambda trades: None,
        ):
            result = trader.run_decision_after_b1({
                "generated_at": "2026-07-11 10:00:00",
                "items": [{"code": "600001", "actionable": True, "hard_blockers": []}],
            }, force=True)

        self.assertTrue(captured["model_called"])
        self.assertFalse(captured["market_ctx"]["allow_new_buys"])
        self.assertEqual(captured["market_ctx"]["max_new_buys_per_decision"], 0)
        self.assertIn("仅允许SELL/HOLD", captured["trade_reason"])
        self.assertEqual(captured["actions_at_execution"][0]["action"], "HOLD")
        self.assertEqual(captured["actions_at_execution"][1]["action"], "SELL")
        self.assertEqual(result["executed"], [sell])
        self.assertTrue(state["trading_paused"])

    def test_dispatcher_exception_is_isolated_without_echoing_secret(self):
        original = sys.modules.get("notifications")
        secret = "private-webhook-token"
        sys.modules["notifications"] = types.SimpleNamespace(
            notify_trade_executions=lambda trades: (_ for _ in ()).throw(RuntimeError(secret))
        )
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                trader._notify_trade_executions_safely([sample_sell()])
        finally:
            if original is None:
                sys.modules.pop("notifications", None)
            else:
                sys.modules["notifications"] = original

        self.assertIn("RuntimeError", stderr.getvalue())
        self.assertNotIn(secret, stderr.getvalue())

    def test_failed_delivery_log_does_not_echo_channel_or_error_secrets(self):
        original = sys.modules.get("notifications")
        secret = "private-provider-error"
        sys.modules["notifications"] = types.SimpleNamespace(
            notify_trade_executions=lambda trades: [
                types.SimpleNamespace(channel="feishu", ok=False, error=secret),
                types.SimpleNamespace(channel=f"telegram-{secret}", ok=False, error=secret),
            ]
        )
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                trader._notify_trade_executions_safely([sample_sell()])
        finally:
            if original is None:
                sys.modules.pop("notifications", None)
            else:
                sys.modules["notifications"] = original

        self.assertIn("2 个渠道", stderr.getvalue())
        self.assertNotIn(secret, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
