"""Independent portfolio backtesting for one frozen prompt strategy version."""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

from app.strategies.prompt_runtime import (
    evaluate_frozen_strategy_stage,
    materialize_stage_context,
    resolve_prompt_order_shares,
)
from app.strategies.rules import (
    EvaluationContext,
    build_action_intent,
    build_rule_evaluation_audit,
    evaluate_plan_stage,
)
from app.strategies.rules.schema import (
    SUPPORTED_PROMPT_RULE_ENGINE_VERSIONS,
    sha256_json,
)

from .selection import (
    HistoricalBar,
    PortfolioEntryDecision,
    PositionExitSignal,
    SelectionContext,
    SelectionCostModel,
    SelectionSignal,
)


PROMPT_BACKTEST_PROTOCOL_VERSION = "prompt-backtest-v2"
PROMPT_BACKTEST_INITIAL_CASH = 1_000_000.0
PROMPT_BACKTEST_BOARD_LOT = 100
PROMPT_BACKTEST_MAX_OPEN_POSITIONS = 6
PROMPT_BACKTEST_MAX_NEW_POSITIONS = 2
PROMPT_BACKTEST_MAX_SINGLE_POSITION_PCT = 10.0
PROMPT_BACKTEST_MAX_TOTAL_POSITION_PCT = 80.0
PROMPT_BACKTEST_MIN_CASH_RESERVE_PCT = 20.0


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def validate_prompt_backtest_version(
    version: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and detach the immutable version used by a backtest job."""
    snapshot = _plain(version)
    version_id = str(snapshot.get("version_id") or "").strip()
    plan = snapshot.get("execution_plan")
    if not version_id or not isinstance(plan, dict):
        raise ValueError("文字策略回测缺少冻结版本或执行计划")
    embedded_hash = str(plan.get("plan_sha256") or "")
    version_hash = str(snapshot.get("plan_sha256") or embedded_hash)
    hash_payload = dict(plan)
    hash_payload.pop("plan_sha256", None)
    if (
        len(embedded_hash) != 64
        or version_hash != embedded_hash
        or sha256_json(hash_payload) != embedded_hash
    ):
        raise ValueError("文字策略回测版本计划指纹无效")
    engine_version = str(plan.get("engine_version") or "")
    if engine_version not in SUPPORTED_PROMPT_RULE_ENGINE_VERSIONS:
        raise ValueError("文字策略回测版本的规则引擎不受支持")
    strategy = plan.get("strategy")
    if not isinstance(strategy, dict):
        raise ValueError("文字策略回测版本缺少结构化策略")
    contract = strategy.get("data_contract") or {}
    if str(contract.get("timeframe") or "") != "1d":
        raise ValueError("文字策略回测当前只支持日线规则")
    if str(contract.get("bar_status") or "") != "closed":
        raise ValueError("文字策略回测当前只支持已收盘日 K 规则")
    snapshot.update({
        "version_id": version_id,
        "plan_sha256": embedded_hash,
        "engine_version": engine_version,
        "execution_plan": plan,
    })
    return snapshot


def prompt_backtest_version_snapshot(version: Mapping[str, Any]) -> dict[str, Any]:
    """Return the non-secret immutable subset persisted with a backtest job."""
    validated = validate_prompt_backtest_version(version)
    return {
        "version_id": validated["version_id"],
        "strategy_key": str(validated.get("strategy_key") or "preset_text"),
        "revision": int(validated.get("revision") or 0),
        "status": str(validated.get("status") or ""),
        "plan_sha256": validated["plan_sha256"],
        "engine_version": validated["engine_version"],
        "activated_at": str(validated.get("activated_at") or ""),
        "execution_plan": _plain(validated["execution_plan"]),
    }


def _rows(history: Sequence[HistoricalBar]) -> list[dict[str, Any]]:
    return [bar.as_strategy_row() for bar in history]


def _data_context(trading_date: str) -> dict[str, str]:
    return {
        "expected_closed_date": str(trading_date or "")[:10],
        "evaluated_at": f"{str(trading_date or '')[:10]} 15:00:00",
    }


def _holding_days(entry_date: str, current_date: str) -> int:
    try:
        return max(
            0,
            (date.fromisoformat(current_date) - date.fromisoformat(entry_date)).days,
        )
    except ValueError:
        return 0


def _available_units(position: Mapping[str, Any], session_index: int) -> int:
    return sum(
        int(lot.get("units") or 0)
        for lot in position.get("lots") or ()
        if isinstance(lot, Mapping)
        and int(lot.get("session_index") or 0) < int(session_index)
    )


def _available_units_on_date(position: Mapping[str, Any], trading_date: str) -> int:
    return sum(
        int(lot.get("units") or 0)
        for lot in position.get("lots") or ()
        if isinstance(lot, Mapping)
        and str(lot.get("date") or "") < str(trading_date or "")
    )


def _entry_snapshot(
    version: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    trading_date: str,
) -> dict[str, Any]:
    plan = version["execution_plan"]
    materialized = materialize_stage_context(
        plan,
        "entry",
        rows,
        data_context=_data_context(trading_date),
    )
    context = materialized.pop("context")
    return {
        "facts": _plain(context.facts),
        "previous_facts": _plain(context.previous_facts),
        "history_facts": _plain(context.history_facts),
        "data_quality": _plain(materialized.get("data_quality") or {}),
        "feature_metadata": _plain(materialized.get("feature_metadata") or {}),
        "feature_errors": _plain(materialized.get("feature_errors") or ()),
        "as_of": str(context.as_of or trading_date),
    }


class PromptStrategyHistoricalSelector:
    """Causally select stocks and freeze entry facts after each close."""

    uses_prepared_strategy_rows = False
    strategy_ids = ("preset_text",)

    def __init__(
        self,
        version: Mapping[str, Any],
        *,
        eligible_symbols: Iterable[str] = (),
    ) -> None:
        self.version = validate_prompt_backtest_version(version)
        plan = self.version["execution_plan"]
        strategy = plan["strategy"]
        requirements = plan.get("stage_requirements") or {}
        self.candidate_limit = int(strategy.get("candidate_limit") or 60)
        self.backtest_warmup_sessions = max(
            int((requirements.get(stage) or {}).get("minimum_bars") or 1)
            for stage in ("selection", "entry", "exit")
        ) - 1
        self.eligible_symbols = frozenset(str(item) for item in eligible_symbols)
        self._signal_generation_enabled = True
        self._diagnostics_enabled = True
        self.reset()

    def reset(self) -> None:
        self._evaluated_count = 0
        self._matched_count = 0
        self._unknown_count = 0

    def set_signal_generation_enabled(self, enabled: bool) -> None:
        self._signal_generation_enabled = bool(enabled)

    def set_diagnostics_enabled(self, enabled: bool) -> None:
        self._diagnostics_enabled = bool(enabled)

    def set_exit_tracking_symbols(self, _symbols: Iterable[str]) -> None:
        """Exit evaluation is owned by the independent portfolio policy."""

    def diagnostics(self) -> dict[str, Any]:
        return {
            "engine": "prompt_strategy",
            "protocol_version": PROMPT_BACKTEST_PROTOCOL_VERSION,
            "strategy_version_id": self.version["version_id"],
            "plan_sha256": self.version["plan_sha256"],
            "rule_engine_version": self.version["engine_version"],
            "evaluated_stock_sessions": self._evaluated_count,
            "matched_stock_sessions": self._matched_count,
            "unknown_stock_sessions": self._unknown_count,
            "candidate_limit": self.candidate_limit,
        }

    def on_close(self, context: SelectionContext) -> tuple[SelectionSignal, ...]:
        if not self._signal_generation_enabled:
            return ()
        matched: list[SelectionSignal] = []
        for symbol in sorted(context.bars):
            if self.eligible_symbols and symbol not in self.eligible_symbols:
                continue
            bar = context.bars[symbol]
            history_rows = _rows(context.history(symbol))
            result = evaluate_frozen_strategy_stage(
                self.version,
                "selection",
                history_rows,
                code=symbol[-6:],
                name=bar.name,
                data_context=_data_context(context.date),
            )
            status = str(result["evaluation"].get("status") or "unknown")
            if self._diagnostics_enabled:
                self._evaluated_count += 1
                self._matched_count += status == "true"
                self._unknown_count += status == "unknown"
            if status != "true":
                continue
            evidence = str(
                (result["evaluation"].get("root") or {}).get("evidence") or ""
            )
            matched.append(SelectionSignal(
                symbol=symbol,
                strategy_id="preset_text",
                reason=evidence or "冻结文字策略选股条件成立",
                score=10.0,
                metadata={
                    "prompt_strategy_version_id": self.version["version_id"],
                    "prompt_plan_sha256": self.version["plan_sha256"],
                    "prompt_selection_audit": _plain(result["audit"]),
                    "prompt_entry_snapshot": _entry_snapshot(
                        self.version,
                        history_rows,
                        trading_date=context.date,
                    ),
                },
            ))
            if len(matched) >= self.candidate_limit:
                break
        return tuple(matched)


class PromptStrategyBacktestPolicy:
    """Frozen prompt entry, sizing, holding-monitor, and exit state machine."""

    portfolio_mode = True
    initial_cash = PROMPT_BACKTEST_INITIAL_CASH
    board_lot = PROMPT_BACKTEST_BOARD_LOT

    def __init__(
        self,
        version: Mapping[str, Any],
        *,
        max_open_positions: int = PROMPT_BACKTEST_MAX_OPEN_POSITIONS,
        max_new_positions_per_session: int = PROMPT_BACKTEST_MAX_NEW_POSITIONS,
        max_single_position_pct: float = PROMPT_BACKTEST_MAX_SINGLE_POSITION_PCT,
        max_total_position_pct: float = PROMPT_BACKTEST_MAX_TOTAL_POSITION_PCT,
        min_cash_reserve_pct: float = PROMPT_BACKTEST_MIN_CASH_RESERVE_PCT,
    ) -> None:
        self.version = validate_prompt_backtest_version(version)
        strategy = self.version["execution_plan"]["strategy"]
        self.position_policy = dict(strategy.get("position") or {})
        self.max_open_positions = max(1, int(max_open_positions))
        self.max_new_positions_per_session = min(
            max(0, int(max_new_positions_per_session)),
            int(strategy.get("max_new_buys_per_cycle") or 0),
        )
        self.max_single_position_pct = max(0.0, float(max_single_position_pct))
        self.min_cash_reserve_pct = min(
            100.0,
            max(0.0, float(min_cash_reserve_pct)),
        )
        self.max_total_position_pct = min(
            max(0.0, float(max_total_position_pct)),
            100.0 - self.min_cash_reserve_pct,
        )
        self.allow_add = bool(self.position_policy.get("allow_add", False))
        self.reset()

    def reset(self) -> None:
        self._cash = float(self.initial_cash)

    def schedule_block_reason(
        self,
        position: Mapping[str, Any],
        _signal: SelectionSignal,
        _trading_date: str,
    ) -> str:
        if position and not self.allow_add:
            return "prompt_add_disabled"
        return ""

    def _evaluate_entry(
        self,
        signal: SelectionSignal,
        runtime_facts: Mapping[str, Any],
        execution_date: str,
    ) -> dict[str, Any]:
        snapshot = signal.metadata.get("prompt_entry_snapshot")
        if not isinstance(snapshot, Mapping):
            raise ValueError("文字策略信号缺少买前事实快照")
        plan = self.version["execution_plan"]
        context = EvaluationContext(
            facts=dict(snapshot.get("facts") or {}),
            previous_facts=dict(snapshot.get("previous_facts") or {}),
            history_facts=tuple(
                dict(item) for item in snapshot.get("history_facts") or ()
                if isinstance(item, Mapping)
            ),
            runtime_facts=dict(runtime_facts),
            as_of=f"{execution_date} 09:30:00",
        )
        evaluation = evaluate_plan_stage(plan, "entry", context)
        intent = build_action_intent(
            plan,
            evaluation,
            code=signal.symbol[-6:],
        )
        audit = build_rule_evaluation_audit(
            strategy_version_id=self.version["version_id"],
            plan=plan,
            stage="entry",
            code=signal.symbol[-6:],
            fact_snapshot=context.facts,
            previous_facts=context.previous_facts,
            history_facts=context.history_facts,
            runtime_facts=context.runtime_facts,
            evaluation=evaluation,
            action_intent=intent,
            evaluated_at=context.as_of,
            data_quality=dict(snapshot.get("data_quality") or {}),
        )
        return {"evaluation": evaluation, "action_intent": intent, "audit": audit}

    def size_entry(
        self,
        signal: SelectionSignal,
        entry_bar: HistoricalBar,
        entry_price: float,
        position: Mapping[str, Any] | None,
        positions: Mapping[str, Mapping[str, Any]],
        marks: Mapping[str, float],
        cash: float,
        total_equity: float,
        new_positions_today: int,
        cost_model: SelectionCostModel,
    ) -> PortfolioEntryDecision:
        existing_units = int((position or {}).get("remaining_units") or 0)
        avg_cost = float((position or {}).get("avg_cost") or 0.0)
        runtime_facts = {
            "account.cash": float(cash),
            "position.quantity": existing_units,
            "position.available_shares": (
                _available_units_on_date(position or {}, entry_bar.date)
                if position else 0
            ),
            "position.avg_cost": avg_cost,
            "position.pnl_pct": (
                (entry_price / avg_cost - 1.0) * 100.0
                if avg_cost > 0 else None
            ),
            "position.hold_days": (
                _holding_days(str(position.get("entry_date") or ""), entry_bar.date)
                if position else 0
            ),
        }
        evaluated = self._evaluate_entry(signal, runtime_facts, entry_bar.date)
        decision_metadata = {
            "prompt_strategy_version_id": self.version["version_id"],
            "prompt_plan_sha256": self.version["plan_sha256"],
            "prompt_entry_audit": _plain(evaluated["audit"]),
        }
        status = str(evaluated["evaluation"].get("status") or "unknown")
        state = {"decision_metadata": decision_metadata}
        if status != "true":
            return PortfolioEntryDecision(
                units=0,
                action="reject",
                reason=f"prompt_entry_{status}",
                state=state,
            )
        if position is None and (
            self.max_new_positions_per_session <= 0
            or new_positions_today >= self.max_new_positions_per_session
        ):
            return PortfolioEntryDecision(
                units=0,
                action="reject",
                reason="prompt_max_new_positions",
                state=state,
            )
        if position is None and len(positions) >= self.max_open_positions:
            return PortfolioEntryDecision(
                units=0,
                action="reject",
                reason="max_open_positions",
                state=state,
            )
        sized = resolve_prompt_order_shares(
            self.position_policy,
            price=entry_price,
            total_equity=total_equity,
            current_position_value=existing_units * entry_price,
            existing_quantity=existing_units,
        )
        units = int(sized.get("shares") or 0)
        if units <= 0:
            return PortfolioEntryDecision(
                units=0,
                action="reject",
                reason=(
                    "prompt_add_disabled"
                    if existing_units and not self.allow_add
                    else "prompt_position_too_small"
                ),
                state=state,
            )
        current_position_value = existing_units * entry_price
        requested_gross = units * entry_price
        position_after_pct = (
            (current_position_value + requested_gross) / total_equity * 100.0
        )
        current_market_value = sum(
            int(item.get("remaining_units") or 0)
            * float(
                marks.get(symbol)
                or item.get("last_price")
                or item.get("entry_price")
                or 0.0
            )
            for symbol, item in positions.items()
        )
        total_after_pct = (
            (current_market_value + requested_gross) / total_equity * 100.0
        )
        if position_after_pct > self.max_single_position_pct + 1e-9:
            return PortfolioEntryDecision(
                units=0,
                action="reject",
                reason="prompt_single_position_limit",
                state=state,
            )
        if total_after_pct > self.max_total_position_pct + 1e-9:
            return PortfolioEntryDecision(
                units=0,
                action="reject",
                reason="prompt_total_position_limit",
                state=state,
            )
        entry_total = entry_price * units + cost_model.entry_fee(entry_price * units)
        if entry_total > float(cash) + 1e-9:
            return PortfolioEntryDecision(
                units=0,
                action="reject",
                reason="insufficient_cash",
                state=state,
            )
        self._cash = float(cash) - entry_total
        state.update({
            "prompt_strategy_version_id": self.version["version_id"],
            "prompt_strategy_plan_sha256": self.version["plan_sha256"],
            "prompt_entry_audit_sha256": evaluated["audit"]["audit_sha256"],
        })
        return PortfolioEntryDecision(
            units=units,
            action="add" if position else "open",
            reason="冻结文字策略买前复核成立",
            state=state,
        )

    def on_entry(
        self,
        signal: SelectionSignal,
        _entry_bar: HistoricalBar,
        _entry_price: float,
    ) -> Mapping[str, Any]:
        selection_audit = signal.metadata.get("prompt_selection_audit")
        return {
            "prompt_strategy_version_id": self.version["version_id"],
            "prompt_strategy_plan_sha256": self.version["plan_sha256"],
            "prompt_selection_audit_sha256": str(
                (selection_audit or {}).get("audit_sha256")
                if isinstance(selection_audit, Mapping) else ""
            ),
        }

    def on_close(
        self,
        position: dict[str, Any],
        context: SelectionContext,
        _selector: Any,
    ) -> PositionExitSignal | None:
        symbol = str(position.get("symbol") or "")
        bar = context.bars.get(symbol)
        if bar is None:
            return None
        quantity = int(position.get("remaining_units") or 0)
        avg_cost = float(position.get("avg_cost") or 0.0)
        result = evaluate_frozen_strategy_stage(
            self.version,
            "exit",
            _rows(context.history(symbol)),
            code=symbol[-6:],
            name=str(bar.name or (position.get("trade") or {}).get("name") or ""),
            runtime_facts={
                "account.cash": self._cash,
                "position.quantity": quantity,
                "position.available_shares": _available_units(
                    position,
                    context.session_index,
                ),
                "position.avg_cost": avg_cost,
                "position.pnl_pct": (
                    (bar.close / avg_cost - 1.0) * 100.0
                    if avg_cost > 0 else None
                ),
                "position.hold_days": _holding_days(
                    str(position.get("entry_date") or ""),
                    context.date,
                ),
            },
            data_context=_data_context(context.date),
        )
        audit = _plain(result["audit"])
        trade = position.get("trade")
        if isinstance(trade, dict):
            trade.setdefault("prompt_monitor_audits", []).append(audit)
        if str(result["evaluation"].get("status") or "unknown") != "true":
            return None
        reason = str(
            (result["evaluation"].get("root") or {}).get("evidence") or ""
        )
        return PositionExitSignal(
            signal="prompt_strategy_exit",
            reason=reason or "冻结文字策略退出条件成立",
            sell_ratio=1.0,
            metadata={
                "prompt_strategy_version_id": self.version["version_id"],
                "prompt_plan_sha256": self.version["plan_sha256"],
                "prompt_exit_audit": audit,
            },
        )

    def on_exit_filled(
        self,
        _position: Mapping[str, Any],
        _decision: PositionExitSignal,
        exit_leg: Mapping[str, Any],
        _context: SelectionContext,
    ) -> None:
        self._cash += (
            float(exit_leg.get("price") or 0.0)
            * int(exit_leg.get("units") or 0)
            - float(exit_leg.get("fee") or 0.0)
        )


__all__ = [
    "PROMPT_BACKTEST_BOARD_LOT",
    "PROMPT_BACKTEST_INITIAL_CASH",
    "PROMPT_BACKTEST_MAX_NEW_POSITIONS",
    "PROMPT_BACKTEST_MAX_OPEN_POSITIONS",
    "PROMPT_BACKTEST_MAX_SINGLE_POSITION_PCT",
    "PROMPT_BACKTEST_MAX_TOTAL_POSITION_PCT",
    "PROMPT_BACKTEST_MIN_CASH_RESERVE_PCT",
    "PROMPT_BACKTEST_PROTOCOL_VERSION",
    "PromptStrategyBacktestPolicy",
    "PromptStrategyHistoricalSelector",
    "prompt_backtest_version_snapshot",
    "validate_prompt_backtest_version",
]
