"""Historical backtesting for stock-selection signals.

The engine owns no market-data I/O and models no funded account. A selector
sees only data available after each historical close and emits stock-selection
signals. Each signal is evaluated from the next session's open to one or more
future closes.
"""
from __future__ import annotations

import math
import statistics
import time
from bisect import bisect_left
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from types import MappingProxyType
from typing import Any, Protocol

try:
    from app.strategies.scoring import (
        NIUONE_MIN_ROWS,
        STRATEGY_SCORERS,
        build_niuone_context,
        build_sector_tide_context,
        enrich_rows,
        invoke_strategy_scorer,
    )
except ImportError:  # pragma: no cover - legacy top-level import path
    from strategies.scoring import (
        NIUONE_MIN_ROWS,
        STRATEGY_SCORERS,
        build_niuone_context,
        build_sector_tide_context,
        enrich_rows,
        invoke_strategy_scorer,
    )

try:
    from app.trading.fees import (
        A_SHARE_COMMISSION_RATE,
        A_SHARE_MINIMUM_COMMISSION,
        A_SHARE_SELL_STAMP_DUTY_RATE,
        A_SHARE_TRANSFER_FEE_RATE,
    )
except ImportError:  # pragma: no cover - legacy top-level import path
    from trading.fees import (
        A_SHARE_COMMISSION_RATE,
        A_SHARE_MINIMUM_COMMISSION,
        A_SHARE_SELL_STAMP_DUTY_RATE,
        A_SHARE_TRANSFER_FEE_RATE,
    )


TRADING_DAYS_PER_YEAR = 252
BUILTIN_STRATEGY_HISTORY_LIMIT = 120
NIUONE_CONTEXT_WARMUP_SESSIONS = 60
REPLAY_ETA_RECENT_SESSION_COUNT = 10
DIAGNOSTIC_SCORE_THRESHOLD_OFFSETS = (-1.0, -0.5, -0.25, 0.0, 0.25, 0.5, 1.0)


def _diagnostic_blocker_family(reason: str) -> str:
    """Map human-readable blockers to stable research ablation families."""
    text = str(reason or "")
    if any(token in text for token in ("结构止损", "动态风险", "风险预算")):
        return "risk_structure"
    if "V型" in text or "日线区间" in text:
        return "daily_v_structure"
    if any(token in text for token in (
        "突破/首次缩量回踩",
        "突破或首次缩量回踩",
        "EMA20企稳转强/收复",
        "EMA20企稳转强或收复",
        "启动买点",
        "距EMA20",
        "超过前高",
        "买点偏扩张",
    )):
        return "price_structure"
    if any(token in text for token in ("阶段不允许", "阶段不可识别", "主线阶段")):
        return "lifecycle_route"
    if "市场" in text or "进攻/轮动行情" in text:
        return "market_regime"
    if any(token in text for token in (
        "龙头梯队", "主线前", "题材当日强度", "单只强股",
    )):
        return "leadership_quality"
    if any(token in text for token in (
        "主线", "主题", "强势股共同确认", "行业有效样本",
    )):
        return "theme_quality"
    return "other"


def _new_scorer_diagnostic_bucket() -> dict[str, Any]:
    return {
        "evaluated_count": 0,
        "unscored_count": 0,
        "scored_count": 0,
        "below_threshold_count": 0,
        "threshold_met_count": 0,
        "actionable_candidate_count": 0,
        "maximum_score": None,
        "entry_threshold": None,
        "entry_threshold_counts": {},
        "blocker_counts": {},
        "blocker_family_counts": {},
        "single_gate_ablation_counts": {},
        "family_ablation_counts": {},
        "score_sensitivity_counts": {
            f"{offset:+g}": 0
            for offset in DIAGNOSTIC_SCORE_THRESHOLD_OFFSETS
        },
        "joint_family_sensitivity_counts": {},
        "leader_branches": {},
        "near_misses": [],
    }


class SelectionBacktestError(RuntimeError):
    """Raised when historical data or a selector violates the contract."""


def _finite_float(value: Any, *, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SelectionBacktestError(f"{field_name} must be a finite number") from None
    if not math.isfinite(number):
        raise SelectionBacktestError(f"{field_name} must be a finite number")
    return number


def _optional_float(value: Any, *, field_name: str) -> float | None:
    if value in (None, ""):
        return None
    return _finite_float(value, field_name=field_name)


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise SelectionBacktestError(f"invalid trading date: {value!r}") from None


def _normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().lower()
    if not symbol:
        raise SelectionBacktestError("symbol is required")
    return symbol


@dataclass(frozen=True)
class HistoricalBar:
    """One completed daily bar plus optional session metadata."""

    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float | None = None
    turnover: float | None = None
    previous_close: float | None = None
    limit_up: float | None = None
    limit_down: float | None = None
    suspended: bool = False
    is_st: bool = False
    name: str = ""
    industry: str = ""
    extras: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _normalize_symbol(self.symbol))
        object.__setattr__(self, "date", _date_text(self.date))
        for field_name in ("open", "high", "low", "close"):
            value = _finite_float(getattr(self, field_name), field_name=field_name)
            if value <= 0:
                raise SelectionBacktestError(
                    f"{self.symbol} {self.date} contains a non-positive price"
                )
            object.__setattr__(self, field_name, value)
        if self.high + 1e-12 < max(self.open, self.low, self.close):
            raise SelectionBacktestError(
                f"{self.symbol} {self.date} has inconsistent OHLC values"
            )
        if self.low - 1e-12 > min(self.open, self.high, self.close):
            raise SelectionBacktestError(
                f"{self.symbol} {self.date} has inconsistent OHLC values"
            )
        object.__setattr__(
            self,
            "volume",
            max(0.0, _finite_float(self.volume, field_name="volume")),
        )
        for field_name in ("amount", "turnover", "previous_close", "limit_up", "limit_down"):
            object.__setattr__(
                self,
                field_name,
                _optional_float(getattr(self, field_name), field_name=field_name),
            )
        object.__setattr__(self, "extras", MappingProxyType(dict(self.extras or {})))

    @classmethod
    def from_value(
        cls,
        symbol: str,
        value: HistoricalBar | Mapping[str, Any],
    ) -> HistoricalBar:
        if isinstance(value, HistoricalBar):
            expected = _normalize_symbol(symbol)
            if expected != value.symbol:
                raise SelectionBacktestError(
                    f"bar symbol mismatch: expected {expected}, got {value.symbol}"
                )
            return value
        if not isinstance(value, Mapping):
            raise SelectionBacktestError("bars must be HistoricalBar objects or mappings")
        resolved_symbol = _normalize_symbol(value.get("symbol") or symbol)
        resolved_date = _date_text(
            value.get("date") or value.get("trade_date") or value.get("datetime")
        )
        known = {
            "symbol", "date", "trade_date", "datetime", "open", "high", "low", "close",
            "volume", "amount", "turnover", "previous_close", "prev_close", "limit_up",
            "limit_down", "suspended", "is_suspended", "is_st", "name", "stock_name",
            "industry", "sector",
        }
        extras = {str(key): item for key, item in value.items() if key not in known}
        return cls(
            symbol=resolved_symbol,
            date=resolved_date,
            open=value.get("open"),
            high=value.get("high"),
            low=value.get("low"),
            close=value.get("close"),
            volume=value.get("volume") or 0.0,
            amount=value.get("amount"),
            turnover=value.get("turnover"),
            previous_close=(
                value.get("previous_close")
                if value.get("previous_close") is not None
                else value.get("prev_close")
            ),
            limit_up=value.get("limit_up"),
            limit_down=value.get("limit_down"),
            suspended=bool(value.get("suspended") or value.get("is_suspended")),
            is_st=bool(value.get("is_st")),
            name=str(value.get("name") or value.get("stock_name") or ""),
            industry=str(value.get("industry") or value.get("sector") or ""),
            extras=extras,
        )

    def as_strategy_row(self) -> dict[str, Any]:
        row = dict(self.extras)
        row.update({
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "amount": self.amount,
            "turnover": self.turnover,
            "prev_close": self.previous_close,
            "symbol_code": self.symbol[-6:],
            "stock_name": self.name,
            "industry": self.industry,
        })
        return row


@dataclass(frozen=True)
class SelectionSignal:
    """A stock selected after a historical session has closed."""

    symbol: str
    strategy_id: str = ""
    reason: str = ""
    score: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _normalize_symbol(self.symbol))
        if self.score is not None:
            object.__setattr__(self, "score", _finite_float(self.score, field_name="score"))
        object.__setattr__(self, "strategy_id", str(self.strategy_id or "").strip())
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata or {})))


@dataclass(frozen=True)
class SelectionCostModel:
    """Costs used only to estimate a signal's hypothetical net return."""

    commission_rate: float = A_SHARE_COMMISSION_RATE
    minimum_commission: float = A_SHARE_MINIMUM_COMMISSION
    transfer_fee_rate: float = A_SHARE_TRANSFER_FEE_RATE
    sell_stamp_duty_rate: float = A_SHARE_SELL_STAMP_DUTY_RATE

    def __post_init__(self) -> None:
        for name in (
            "commission_rate", "minimum_commission", "transfer_fee_rate",
            "sell_stamp_duty_rate",
        ):
            value = _finite_float(getattr(self, name), field_name=name)
            if value < 0:
                raise SelectionBacktestError(f"{name} cannot be negative")
            object.__setattr__(self, name, value)

    def entry_fee(self, amount: float) -> float:
        gross = max(0.0, _finite_float(amount, field_name="entry amount"))
        commission = max(gross * self.commission_rate, self.minimum_commission)
        transfer_fee = gross * self.transfer_fee_rate
        return round(commission + transfer_fee, 2)

    def exit_fee(self, amount: float) -> float:
        gross = max(0.0, _finite_float(amount, field_name="exit amount"))
        commission = max(gross * self.commission_rate, self.minimum_commission)
        transfer_fee = gross * self.transfer_fee_rate
        stamp_duty = gross * self.sell_stamp_duty_rate
        return round(commission + transfer_fee + stamp_duty, 2)


PriceLimitResolver = Callable[
    [HistoricalBar, float | None],
    tuple[float | None, float | None],
]


def _limit_price(previous_close: float, ratio: float, *, upper: bool) -> float:
    multiplier = Decimal("1") + (Decimal(str(ratio)) if upper else -Decimal(str(ratio)))
    return float(
        (Decimal(str(previous_close)) * multiplier).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )


def a_share_price_limits(
    bar: HistoricalBar,
    previous_close: float | None,
) -> tuple[float | None, float | None]:
    """Resolve common A-share daily limits; explicit source values win."""
    if bar.limit_up is not None or bar.limit_down is not None:
        return bar.limit_up, bar.limit_down
    base = bar.previous_close or previous_close
    if base is None or base <= 0:
        return None, None
    code = "".join(character for character in bar.symbol if character.isdigit())[-6:]
    ratio = 0.05 if bar.is_st else 0.10
    if not bar.is_st and code.startswith(("688", "689")):
        ratio = 0.20
    elif not bar.is_st and code.startswith(("300", "301")) and bar.date >= "2020-08-24":
        ratio = 0.20
    elif not bar.is_st and code.startswith(("4", "8")):
        ratio = 0.30
    return _limit_price(base, ratio, upper=True), _limit_price(base, ratio, upper=False)


class _PrefixSequence(Sequence[Any]):
    """Read-only historical view over a growing or precomputed sequence."""

    __slots__ = ("_values", "_stop")

    def __init__(self, values: Sequence[Any], stop: int) -> None:
        self._values = values
        self._stop = max(0, min(len(values), int(stop)))

    def __len__(self) -> int:
        return self._stop

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(self._stop)
            return [self._values[position] for position in range(start, stop, step)]
        resolved = int(index)
        if resolved < 0:
            resolved += self._stop
        if resolved < 0 or resolved >= self._stop:
            raise IndexError(index)
        return self._values[resolved]

    def __iter__(self):
        for index in range(self._stop):
            yield self._values[index]


class _PrefixMapping(Mapping[str, Sequence[Any]]):
    """Create history views lazily instead of copying every symbol every day."""

    def __init__(
        self,
        values: Mapping[str, Sequence[Any]],
        stops: Mapping[str, int],
    ) -> None:
        self._values = values
        self._stops = stops

    def __getitem__(self, key: str) -> Sequence[Any]:
        values = self._values[key]
        return _PrefixSequence(values, self._stops.get(key, 0))

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


@dataclass(frozen=True)
class SelectionContext:
    """Read-only session data passed to a selector after the close."""

    date: str
    session_index: int
    bars: Mapping[str, HistoricalBar]
    histories: Mapping[str, Sequence[HistoricalBar]]
    strategy_rows: Mapping[str, Sequence[Mapping[str, Any]]] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )

    def history(self, symbol: str) -> Sequence[HistoricalBar]:
        return self.histories.get(_normalize_symbol(symbol), ())

    def strategy_history(self, symbol: str) -> Sequence[Mapping[str, Any]]:
        """Return causally enriched rows available at this historical close."""
        return self.strategy_rows.get(_normalize_symbol(symbol), ())


class SelectionStrategy(Protocol):
    def on_close(self, context: SelectionContext) -> Iterable[SelectionSignal]:
        """Return the stocks selected after this close."""


@dataclass(frozen=True)
class PositionExitSignal:
    """One deterministic close-time exit decision for an open backtest trade."""

    signal: str
    reason: str
    sell_ratio: float = 1.0
    fill_reference_price: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        signal = str(self.signal or "").strip()
        reason = str(self.reason or "").strip()
        ratio = _finite_float(self.sell_ratio, field_name="sell_ratio")
        if not signal:
            raise SelectionBacktestError("exit signal is required")
        if not reason:
            raise SelectionBacktestError("exit reason is required")
        if ratio <= 0 or ratio > 1:
            raise SelectionBacktestError("sell_ratio must be within (0, 1]")
        fill_reference = (
            _finite_float(
                self.fill_reference_price,
                field_name="fill_reference_price",
            )
            if self.fill_reference_price is not None else None
        )
        if fill_reference is not None and fill_reference <= 0:
            raise SelectionBacktestError("fill_reference_price must be positive")
        object.__setattr__(self, "signal", signal)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "sell_ratio", ratio)
        object.__setattr__(self, "fill_reference_price", fill_reference)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata or {})))


class PositionExitStrategy(Protocol):
    """Strategy-owned state machine used only by the independent backtester."""

    def on_entry(
        self,
        signal: SelectionSignal,
        entry_bar: HistoricalBar,
        entry_price: float,
    ) -> Mapping[str, Any]:
        """Return initial state derived from the accepted entry signal."""

    def on_close(
        self,
        position: dict[str, Any],
        context: SelectionContext,
        selector: SelectionStrategy | SelectionFunction,
    ) -> PositionExitSignal | None:
        """Return an exit decision using information visible by this close."""


@dataclass(frozen=True)
class PortfolioEntryDecision:
    """A strategy-owned, risk-sized order for the portfolio replay engine."""

    units: int
    action: str
    reason: str = ""
    state: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        units = int(self.units)
        action = str(self.action or "").strip()
        if units < 0:
            raise SelectionBacktestError("entry units cannot be negative")
        if action not in {"open", "add", "reject"}:
            raise SelectionBacktestError("entry action must be open, add, or reject")
        if action == "reject" and units:
            raise SelectionBacktestError("a rejected entry cannot contain units")
        object.__setattr__(self, "units", units)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(self, "state", MappingProxyType(dict(self.state or {})))


SelectionFunction = Callable[[SelectionContext], Iterable[SelectionSignal]]
ScorerContextProvider = Callable[[SelectionContext], Mapping[str, Any] | None]
HistoricalFlowProvider = Callable[[SelectionContext], Any]
SelectionProgress = Callable[[int, int, str], None]
SelectionPreparation = Callable[[str], None]
SelectionPhaseProgress = Callable[[int, int], None]
SelectionReplayProgress = Callable[
    [int, int, str, str, float, float | None],
    None,
]
SelectionSignalFilter = Callable[[SelectionSignal], bool]


@dataclass(frozen=True)
class SelectionBacktestConfig:
    """Evaluation policy for close-time stock-selection signals."""

    holding_sessions: tuple[int, ...] = (1, 3, 5, 10, 20)
    signal_start_date: str = ""
    signal_end_date: str = ""
    cooldown_sessions: int = 20
    slippage_bps: float = 5.0
    evaluation_lot_size: int = 100
    reject_zero_volume: bool = True
    price_limit_resolver: PriceLimitResolver | None = a_share_price_limits
    cost_model: SelectionCostModel = field(default_factory=SelectionCostModel)

    def __post_init__(self) -> None:
        horizons = tuple(sorted(set(int(value) for value in self.holding_sessions)))
        if not horizons or horizons[0] <= 0 or horizons[-1] > TRADING_DAYS_PER_YEAR:
            raise SelectionBacktestError("holding_sessions must be between 1 and 252")
        start = _date_text(self.signal_start_date) if self.signal_start_date else ""
        end = _date_text(self.signal_end_date) if self.signal_end_date else ""
        if start and end and start > end:
            raise SelectionBacktestError("signal_start_date cannot be after signal_end_date")
        if int(self.cooldown_sessions) < 0:
            raise SelectionBacktestError("cooldown_sessions cannot be negative")
        if not math.isfinite(float(self.slippage_bps)) or self.slippage_bps < 0:
            raise SelectionBacktestError("slippage_bps cannot be negative")
        if int(self.evaluation_lot_size) <= 0:
            raise SelectionBacktestError("evaluation_lot_size must be positive")
        object.__setattr__(self, "holding_sessions", horizons)
        object.__setattr__(self, "signal_start_date", start)
        object.__setattr__(self, "signal_end_date", end)


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_value(item) for item in value]
    return value


@dataclass(frozen=True)
class SelectionBacktestResult:
    """Selection signals plus fixed-horizon or completed-trade statistics."""

    signals: tuple[Mapping[str, Any], ...]
    statistics: Mapping[str, Any]
    trades: tuple[Mapping[str, Any], ...] = ()
    portfolio: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    diagnostics: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "signals": [_plain_value(row) for row in self.signals],
            "statistics": _plain_value(self.statistics),
            "trades": [_plain_value(row) for row in self.trades],
            "portfolio": _plain_value(self.portfolio),
            "diagnostics": _plain_value(self.diagnostics),
        }


@dataclass(frozen=True)
class SelectionReplayFrame:
    """Signals, scorer state, and an optional close-time cross section."""

    date: str
    signals: tuple[SelectionSignal, ...] = ()
    scored: Mapping[str, Mapping[str, Mapping[str, Any]]] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )
    cross_section: Mapping[str, Mapping[str, Any]] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )


@dataclass(frozen=True)
class SelectionReplayTape:
    """Reusable historical selector output for execution-policy research."""

    frames: Mapping[str, SelectionReplayFrame]
    diagnostics: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )


class ReplaySelectionStrategy:
    """Replay a recorded selector tape without recomputing cross sections."""

    backtest_warmup_sessions = 0
    uses_prepared_strategy_rows = False

    def __init__(
        self,
        tape: SelectionReplayTape,
        *,
        signal_filter: SelectionSignalFilter | None = None,
        max_signals_per_session: int | None = None,
        max_signals_per_strategy_per_session: Mapping[str, int] | None = None,
    ) -> None:
        if max_signals_per_session is not None and int(max_signals_per_session) <= 0:
            raise SelectionBacktestError("max_signals_per_session must be positive")
        strategy_limits = {
            str(strategy_id): int(limit)
            for strategy_id, limit in (
                max_signals_per_strategy_per_session or {}
            ).items()
        }
        if any(limit <= 0 for limit in strategy_limits.values()):
            raise SelectionBacktestError("strategy signal limits must be positive")
        self.tape = tape
        self.signal_filter = signal_filter
        self.max_signals_per_session = (
            int(max_signals_per_session)
            if max_signals_per_session is not None else None
        )
        self.max_signals_per_strategy_per_session = MappingProxyType(
            strategy_limits
        )
        self._signal_generation_enabled = True
        self._latest_scored: dict[tuple[str, str], Mapping[str, Any]] = {}

    def reset(self) -> None:
        self._signal_generation_enabled = True
        self._latest_scored = {}

    def set_diagnostics_enabled(self, _enabled: bool) -> None:
        """Keep the recorded diagnostic window unchanged."""

    def set_signal_generation_enabled(self, enabled: bool) -> None:
        self._signal_generation_enabled = bool(enabled)

    def set_exit_tracking_symbols(self, _symbols: Iterable[str]) -> None:
        """The tape already contains scorer state for all possible entries."""

    def latest_scored(
        self,
        symbol: str,
        strategy_id: str,
    ) -> Mapping[str, Any] | None:
        return self._latest_scored.get(
            (_normalize_symbol(symbol), str(strategy_id or "").strip())
        )

    def diagnostics(self) -> dict[str, Any]:
        return _plain_value(self.tape.diagnostics)

    def on_close(self, context: SelectionContext) -> Iterable[SelectionSignal]:
        frame = self.tape.frames.get(context.date)
        latest: dict[tuple[str, str], Mapping[str, Any]] = {}
        if frame is not None:
            for symbol, by_strategy in frame.scored.items():
                for strategy_id, scored in by_strategy.items():
                    latest[(symbol, strategy_id)] = scored
        self._latest_scored = latest
        if frame is None or not self._signal_generation_enabled:
            return ()
        if (
            self.signal_filter is None
            and self.max_signals_per_session is None
            and not self.max_signals_per_strategy_per_session
        ):
            return frame.signals
        selections: list[SelectionSignal] = []
        selected_symbols: set[str] = set()
        selected_by_strategy: dict[str, int] = {}
        for signal in frame.signals:
            if self.signal_filter is not None and not self.signal_filter(signal):
                continue
            if (
                self.max_signals_per_session is not None
                and len(selections) >= self.max_signals_per_session
            ):
                break
            if signal.symbol in selected_symbols:
                continue
            strategy_limit = self.max_signals_per_strategy_per_session.get(
                signal.strategy_id
            )
            if (
                strategy_limit is not None
                and selected_by_strategy.get(signal.strategy_id, 0)
                >= strategy_limit
            ):
                continue
            selected_symbols.add(signal.symbol)
            selected_by_strategy[signal.strategy_id] = (
                selected_by_strategy.get(signal.strategy_id, 0) + 1
            )
            selections.append(signal)
        return tuple(selections)


def _normalized_bars(
    bars_by_symbol: Mapping[str, Iterable[HistoricalBar | Mapping[str, Any]]],
    *,
    progress_callback: SelectionPhaseProgress | None = None,
) -> tuple[dict[str, dict[str, HistoricalBar]], tuple[str, ...]]:
    if not isinstance(bars_by_symbol, Mapping) or not bars_by_symbol:
        raise SelectionBacktestError("bars_by_symbol must contain at least one symbol")
    result: dict[str, dict[str, HistoricalBar]] = {}
    dates: set[str] = set()
    total = len(bars_by_symbol)
    if progress_callback is not None:
        progress_callback(0, total)
    for completed, (raw_symbol, raw_bars) in enumerate(
        bars_by_symbol.items(),
        start=1,
    ):
        symbol = _normalize_symbol(raw_symbol)
        by_date: dict[str, HistoricalBar] = {}
        for raw_bar in raw_bars or []:
            if isinstance(raw_bar, HistoricalBar):
                if raw_bar.symbol != symbol:
                    raise SelectionBacktestError(
                        f"bar symbol mismatch: expected {symbol}, got {raw_bar.symbol}"
                    )
                bar = raw_bar
            else:
                bar = HistoricalBar.from_value(symbol, raw_bar)
            by_date[bar.date] = bar
            dates.add(bar.date)
        if by_date:
            result[symbol] = by_date
        if progress_callback is not None and (
            completed == 1 or completed % 25 == 0 or completed == total
        ):
            progress_callback(completed, total)
    if not result or not dates:
        raise SelectionBacktestError("no valid historical bars were supplied")
    return result, tuple(sorted(dates))


def _call_selector(
    selector: SelectionStrategy | SelectionFunction,
    context: SelectionContext,
) -> tuple[SelectionSignal, ...]:
    try:
        generated = selector.on_close(context) if hasattr(selector, "on_close") else selector(context)
        values = tuple(generated or ())
    except Exception as exc:
        raise SelectionBacktestError(
            f"selector failed after {context.date} close: {exc}"
        ) from exc
    if not all(isinstance(value, SelectionSignal) for value in values):
        raise SelectionBacktestError("selector must return SelectionSignal objects")
    return values


def _fill_price(
    bar: HistoricalBar,
    *,
    entry: bool,
    slippage_bps: float,
    reference_price: float | None = None,
) -> float:
    multiplier = 1.0 + (slippage_bps / 10_000.0 if entry else -slippage_bps / 10_000.0)
    reference = (
        float(reference_price)
        if reference_price is not None else (bar.open if entry else bar.close)
    )
    return round(min(bar.high, max(bar.low, reference * multiplier)), 4)


def _horizon_statistics(
    evaluated: Sequence[Mapping[str, Any]],
    config: SelectionBacktestConfig,
) -> dict[str, dict[str, Any]]:
    by_horizon: dict[str, dict[str, Any]] = {}
    for horizon in config.holding_sessions:
        details = [
            detail
            for row in evaluated
            if isinstance((returns := row.get("forward_returns")), Mapping)
            and isinstance((detail := returns.get(horizon)), Mapping)
            and detail.get("net_return_pct") is not None
        ]
        net_values = [float(detail["net_return_pct"]) for detail in details]
        gross_values = [float(detail["gross_return_pct"]) for detail in details]
        by_horizon[str(horizon)] = {
            "sample_count": len(net_values),
            "average_gross_return_pct": round(statistics.mean(gross_values), 4) if gross_values else None,
            "median_gross_return_pct": round(statistics.median(gross_values), 4) if gross_values else None,
            "average_net_return_pct": round(statistics.mean(net_values), 4) if net_values else None,
            "median_net_return_pct": round(statistics.median(net_values), 4) if net_values else None,
            "win_rate_pct": (
                round(sum(value > 0 for value in net_values) / len(net_values) * 100, 4)
                if net_values else None
            ),
            "best_net_return_pct": round(max(net_values), 4) if net_values else None,
            "worst_net_return_pct": round(min(net_values), 4) if net_values else None,
        }
    return by_horizon


def _selection_statistics(
    signals: Sequence[Mapping[str, Any]],
    config: SelectionBacktestConfig,
) -> dict[str, Any]:
    evaluated = [row for row in signals if row.get("status") == "evaluated"]
    strategy_ids = sorted({str(row.get("strategy_id") or "unattributed") for row in signals})
    by_strategy: dict[str, dict[str, Any]] = {}
    for strategy_id in strategy_ids:
        strategy_signals = [
            row for row in signals
            if str(row.get("strategy_id") or "unattributed") == strategy_id
        ]
        strategy_evaluated = [row for row in strategy_signals if row.get("status") == "evaluated"]
        by_strategy[strategy_id] = {
            "signal_count": len(strategy_signals),
            "evaluated_signal_count": len(strategy_evaluated),
            "by_horizon": _horizon_statistics(strategy_evaluated, config),
        }
    signal_dates = [str(row.get("signal_date") or "") for row in signals]
    return {
        "evaluation_mode": "fixed_horizon",
        "signal_start_date": config.signal_start_date or (min(signal_dates) if signal_dates else ""),
        "signal_end_date": config.signal_end_date or (max(signal_dates) if signal_dates else ""),
        "holding_sessions": list(config.holding_sessions),
        "signal_count": len(signals),
        "evaluated_signal_count": len(evaluated),
        "duplicate_signal_count": sum(row.get("status_reason") == "cooldown" for row in signals),
        "rejected_signal_count": sum(row.get("status") == "rejected" for row in signals),
        "by_horizon": _horizon_statistics(evaluated, config),
        "by_strategy": by_strategy,
    }


def _prepared_strategy_rows(
    bars: Mapping[str, Mapping[str, HistoricalBar]],
    *,
    preparation_callback: SelectionPreparation | None = None,
    progress_callback: SelectionPhaseProgress | None = None,
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    """Enrich each complete series once; all indicators are backward-looking."""
    prepared: dict[str, tuple[Mapping[str, Any], ...]] = {}
    total = len(bars)
    if progress_callback is not None:
        progress_callback(0, total)
    for completed, (symbol, by_date) in enumerate(bars.items(), start=1):
        rows = [by_date[trading_date].as_strategy_row() for trading_date in sorted(by_date)]
        enrich_rows(rows)
        prepared[symbol] = tuple(MappingProxyType(row) for row in rows)
        if preparation_callback is not None and (
            completed == 1 or completed % 25 == 0 or completed == total
        ):
            preparation_callback(
                f"正在预计算技术指标（{completed}/{total}）"
            )
        if progress_callback is not None and (
            completed == 1 or completed % 25 == 0 or completed == total
        ):
            progress_callback(completed, total)
    return prepared


def _first_selector_session(
    trading_dates: Sequence[str],
    selector: SelectionStrategy | SelectionFunction,
    signal_start_date: str,
) -> int:
    warmup = getattr(selector, "backtest_warmup_sessions", None)
    if not signal_start_date or warmup is None:
        return 0
    signal_index = bisect_left(trading_dates, signal_start_date)
    first_session = max(0, signal_index - max(0, int(warmup)))
    earliest_state_session = getattr(
        selector,
        "backtest_earliest_state_session",
        None,
    )
    if earliest_state_session is not None:
        first_session = min(
            first_session,
            max(0, int(earliest_state_session)),
        )
    return first_session


def _estimate_replay_eta(
    elapsed_sessions: Sequence[float],
    remaining_sessions: int,
    *,
    current_session_elapsed: float = 0.0,
) -> float | None:
    """Estimate replay time without letting cheap warmup days dominate."""
    remaining = max(0, int(remaining_sessions))
    if remaining == 0:
        return 0.0
    samples = [
        max(0.0, float(value))
        for value in elapsed_sessions
        if math.isfinite(float(value))
    ]
    current_elapsed = max(0.0, float(current_session_elapsed))
    if not samples:
        return current_elapsed * remaining if current_elapsed > 0 else None
    recent = samples[-REPLAY_ETA_RECENT_SESSION_COUNT:]
    seconds_per_session = max(
        statistics.mean(samples),
        statistics.mean(recent),
        current_elapsed,
    )
    return seconds_per_session * remaining


def build_selection_replay_tape(
    bars_by_symbol: Mapping[str, Iterable[HistoricalBar | Mapping[str, Any]]],
    selector: SelectionStrategy | SelectionFunction,
    *,
    config: SelectionBacktestConfig | None = None,
    progress_callback: SelectionProgress | None = None,
    preparation_callback: SelectionPreparation | None = None,
    normalization_progress_callback: SelectionPhaseProgress | None = None,
    preparation_progress_callback: SelectionPhaseProgress | None = None,
    replay_progress_callback: SelectionReplayProgress | None = None,
    scored_fields: Sequence[str] | None = None,
    cross_section_fields: Sequence[str] | None = None,
) -> SelectionReplayTape:
    """Record expensive historical selections once for cheap policy replay.

    The tape stores signals independently of an account. Scorer snapshots are
    retained only for symbols that have emitted a signal, which is sufficient
    for any replay that filters the recorded signal set or changes execution,
    sizing, and exit rules without adding new entry candidates. When requested,
    provider-level cross-sectional rows are projected separately so relative
    market or theme ranks can be researched without retaining every stock score.
    """
    resolved = config or SelectionBacktestConfig()
    bars, trading_dates = _normalized_bars(
        bars_by_symbol,
        progress_callback=normalization_progress_callback,
    )
    prepared_rows: Mapping[str, Sequence[Mapping[str, Any]]] = {}
    if getattr(selector, "uses_prepared_strategy_rows", False):
        prepared_rows = _prepared_strategy_rows(
            bars,
            preparation_callback=preparation_callback,
            progress_callback=preparation_progress_callback,
        )
    histories: dict[str, list[HistoricalBar]] = {symbol: [] for symbol in bars}
    tracked_symbols: set[str] = set()
    frames: dict[str, SelectionReplayFrame] = {}
    strategy_ids = tuple(
        str(item or "").strip()
        for item in (getattr(selector, "strategy_ids", ()) or ())
        if str(item or "").strip()
    )
    scored_reader = getattr(selector, "latest_scored", None)
    cross_section_reader = getattr(selector, "latest_cross_section", None)
    if callable(scored_reader) and not strategy_ids:
        raise SelectionBacktestError(
            "a replayable scorer must expose its strategy_ids"
        )
    reset_selector = getattr(selector, "reset", None)
    if callable(reset_selector):
        reset_selector()
    first_selector_session = _first_selector_session(
        trading_dates,
        selector,
        resolved.signal_start_date,
    )
    evaluation_dates = tuple(trading_dates[first_selector_session:])
    completed_sessions = 0
    elapsed_sessions: list[float] = []

    for session_index, trading_date in enumerate(trading_dates):
        current_bars = {
            symbol: bar
            for symbol, series in bars.items()
            if (bar := series.get(trading_date)) is not None
        }
        for symbol, bar in current_bars.items():
            histories[symbol].append(bar)
        if session_index < first_selector_session:
            continue
        history_stops = {
            symbol: len(symbol_history)
            for symbol, symbol_history in histories.items()
        }
        context = SelectionContext(
            date=trading_date,
            session_index=session_index,
            bars=MappingProxyType(dict(current_bars)),
            histories=_PrefixMapping(histories, history_stops),
            strategy_rows=(
                _PrefixMapping(prepared_rows, history_stops)
                if prepared_rows else MappingProxyType({})
            ),
        )
        within_signal_window = (
            (not resolved.signal_start_date or trading_date >= resolved.signal_start_date)
            and (not resolved.signal_end_date or trading_date <= resolved.signal_end_date)
        )
        set_diagnostics_enabled = getattr(selector, "set_diagnostics_enabled", None)
        if callable(set_diagnostics_enabled):
            set_diagnostics_enabled(within_signal_window)
        set_signal_generation_enabled = getattr(
            selector,
            "set_signal_generation_enabled",
            None,
        )
        if callable(set_signal_generation_enabled):
            set_signal_generation_enabled(within_signal_window)
        set_exit_tracking_symbols = getattr(selector, "set_exit_tracking_symbols", None)
        if callable(set_exit_tracking_symbols):
            set_exit_tracking_symbols(tracked_symbols)
        session_started_at = time.perf_counter()

        def replay_phase(phase: str, _date: str = trading_date) -> None:
            if replay_progress_callback is None:
                return
            current_elapsed = max(0.0, time.perf_counter() - session_started_at)
            replay_progress_callback(
                completed_sessions,
                len(evaluation_dates),
                trading_date,
                str(phase or "scoring"),
                current_elapsed,
                _estimate_replay_eta(
                    elapsed_sessions,
                    len(evaluation_dates) - completed_sessions,
                    current_session_elapsed=current_elapsed,
                ),
            )

        phase_setter = getattr(selector, "set_replay_phase_callback", None)
        if callable(phase_setter):
            phase_setter(replay_phase)
        try:
            generated_signals = _call_selector(selector, context)
        finally:
            if callable(phase_setter):
                phase_setter(None)
        if within_signal_window:
            tracked_symbols.update(signal.symbol for signal in generated_signals)

        scored_by_symbol: dict[str, Mapping[str, Mapping[str, Any]]] = {}
        if callable(scored_reader):
            for symbol in sorted(tracked_symbols):
                by_strategy: dict[str, Mapping[str, Any]] = {}
                for strategy_id in strategy_ids:
                    scored = scored_reader(symbol, strategy_id)
                    if isinstance(scored, Mapping):
                        projected = (
                            {
                                field_name: scored[field_name]
                                for field_name in scored_fields
                                if field_name in scored
                            }
                            if scored_fields is not None
                            else dict(scored)
                        )
                        by_strategy[strategy_id] = MappingProxyType(projected)
                if by_strategy:
                    scored_by_symbol[symbol] = MappingProxyType(by_strategy)
        cross_section: dict[str, Mapping[str, Any]] = {}
        if cross_section_fields is not None and callable(cross_section_reader):
            latest_cross_section = cross_section_reader()
            if isinstance(latest_cross_section, Mapping):
                for item_id, values in latest_cross_section.items():
                    if not isinstance(values, Mapping):
                        continue
                    projected = {
                        field_name: values[field_name]
                        for field_name in cross_section_fields
                        if field_name in values
                    }
                    cross_section[str(item_id)] = MappingProxyType(projected)
        if (
            not resolved.signal_start_date
            or trading_date >= resolved.signal_start_date
        ):
            frames[trading_date] = SelectionReplayFrame(
                date=trading_date,
                signals=(generated_signals if within_signal_window else ()),
                scored=MappingProxyType(scored_by_symbol),
                cross_section=MappingProxyType(cross_section),
            )
        completed_sessions += 1
        session_elapsed = max(0.0, time.perf_counter() - session_started_at)
        elapsed_sessions.append(session_elapsed)
        if replay_progress_callback is not None:
            replay_progress_callback(
                min(completed_sessions, len(evaluation_dates)),
                len(evaluation_dates),
                trading_date,
                "scoring",
                session_elapsed,
                _estimate_replay_eta(
                    elapsed_sessions,
                    len(evaluation_dates) - completed_sessions,
                ),
            )
        if progress_callback is not None:
            progress_callback(
                min(completed_sessions, len(evaluation_dates)),
                len(evaluation_dates),
                trading_date,
            )

    diagnostics_reader = getattr(selector, "diagnostics", None)
    diagnostics = diagnostics_reader() if callable(diagnostics_reader) else {}
    return SelectionReplayTape(
        frames=MappingProxyType(frames),
        diagnostics=MappingProxyType(dict(diagnostics or {})),
    )


def _trade_summary(values: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    completed = [row for row in values if row.get("status") == "completed"]
    returns = [float(row["net_return_pct"]) for row in completed]
    holding_sessions = [int(row.get("holding_sessions") or 0) for row in completed]
    gains = sum(value for value in returns if value > 0)
    losses = abs(sum(value for value in returns if value < 0))
    return {
        "trade_count": len(values),
        "completed_trade_count": len(completed),
        "open_trade_count": sum(row.get("status") == "open" for row in values),
        "average_net_return_pct": (
            round(statistics.mean(returns), 4) if returns else None
        ),
        "median_net_return_pct": (
            round(statistics.median(returns), 4) if returns else None
        ),
        "win_rate_pct": (
            round(sum(value > 0 for value in returns) / len(returns) * 100, 4)
            if returns else None
        ),
        "best_net_return_pct": round(max(returns), 4) if returns else None,
        "worst_net_return_pct": round(min(returns), 4) if returns else None,
        "average_holding_sessions": (
            round(statistics.mean(holding_sessions), 2)
            if holding_sessions else None
        ),
        "profit_factor": (
            round(gains / losses, 4) if losses > 0 else (None if not gains else None)
        ),
    }


def _trade_statistics(
    signals: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
    config: SelectionBacktestConfig,
) -> dict[str, Any]:
    strategy_ids = sorted({
        str(row.get("strategy_id") or "unattributed")
        for row in (*signals, *trades)
    })
    by_strategy: dict[str, dict[str, Any]] = {}
    for strategy_id in strategy_ids:
        strategy_signals = [
            row for row in signals
            if str(row.get("strategy_id") or "unattributed") == strategy_id
        ]
        strategy_trades = [
            row for row in trades
            if str(row.get("strategy_id") or "unattributed") == strategy_id
        ]
        by_strategy[strategy_id] = {
            "signal_count": len(strategy_signals),
            "evaluated_signal_count": sum(
                row.get("status") == "evaluated" for row in strategy_signals
            ),
            **_trade_summary(strategy_trades),
        }
    signal_dates = [str(row.get("signal_date") or "") for row in signals]
    return {
        "evaluation_mode": "trade_lifecycle",
        "signal_start_date": config.signal_start_date or (
            min(signal_dates) if signal_dates else ""
        ),
        "signal_end_date": config.signal_end_date or (
            max(signal_dates) if signal_dates else ""
        ),
        "signal_count": len(signals),
        "evaluated_signal_count": sum(
            row.get("status") == "evaluated" for row in signals
        ),
        "duplicate_signal_count": sum(
            row.get("status_reason") in {"position_open", "entry_pending"}
            for row in signals
        ),
        "rejected_signal_count": sum(
            row.get("status") == "rejected" for row in signals
        ),
        **_trade_summary(trades),
        "by_strategy": by_strategy,
    }


def _trade_mark_to_market(
    position: Mapping[str, Any],
    price: float,
    config: SelectionBacktestConfig,
) -> float | None:
    entry_total = float(position.get("entry_total") or 0.0)
    remaining_units = float(position.get("remaining_units") or 0.0)
    if entry_total <= 0 or remaining_units <= 0 or price <= 0:
        return None
    remaining_amount = price * remaining_units
    estimated_fee = config.cost_model.exit_fee(remaining_amount)
    net_proceeds = (
        float(position.get("realized_net_proceeds") or 0.0)
        + remaining_amount
        - estimated_fee
    )
    return round((net_proceeds / entry_total - 1.0) * 100, 4)


def _run_trade_lifecycle_backtest(
    bars: Mapping[str, Mapping[str, HistoricalBar]],
    trading_dates: Sequence[str],
    prepared_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    selector: SelectionStrategy | SelectionFunction,
    exit_strategy: PositionExitStrategy,
    config: SelectionBacktestConfig,
    *,
    progress_callback: SelectionProgress | None,
) -> SelectionBacktestResult:
    """Replay entries and deterministic exits without touching a real account."""
    histories: dict[str, list[HistoricalBar]] = {symbol: [] for symbol in bars}
    signals: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    positions: dict[str, dict[str, Any]] = {}
    pending_entries: dict[
        str, tuple[dict[str, Any], SelectionSignal, int]
    ] = {}
    first_selector_session = _first_selector_session(
        trading_dates,
        selector,
        config.signal_start_date,
    )
    reset_selector = getattr(selector, "reset", None)
    if callable(reset_selector):
        reset_selector()
    evaluation_dates = tuple(trading_dates[first_selector_session:])
    completed_sessions = 0

    for session_index, trading_date in enumerate(trading_dates):
        current_bars = {
            symbol: bar
            for symbol, series in bars.items()
            if (bar := series.get(trading_date)) is not None
        }
        for symbol, bar in current_bars.items():
            histories[symbol].append(bar)

        for symbol, (record, selected, entry_session_index) in tuple(
            pending_entries.items()
        ):
            if entry_session_index != session_index:
                continue
            pending_entries.pop(symbol, None)
            entry_bar = current_bars.get(symbol)
            record["entry_date"] = trading_date
            if entry_bar is None:
                record["status_reason"] = "missing_next_session_bar"
                continue
            if entry_bar.suspended or (
                config.reject_zero_volume and entry_bar.volume <= 0
            ):
                record["status_reason"] = "suspended_or_zero_volume"
                continue
            signal_bar = bars[symbol].get(str(record.get("signal_date") or ""))
            limits = (
                config.price_limit_resolver(
                    entry_bar,
                    signal_bar.close if signal_bar is not None else None,
                )
                if config.price_limit_resolver is not None
                else (entry_bar.limit_up, entry_bar.limit_down)
            )
            if limits[0] is not None and entry_bar.open >= limits[0] - 1e-9:
                record["status_reason"] = "open_at_limit_up"
                continue
            entry_price = _fill_price(
                entry_bar,
                entry=True,
                slippage_bps=float(config.slippage_bps),
            )
            units = float(config.evaluation_lot_size)
            entry_amount = entry_price * units
            entry_fee = config.cost_model.entry_fee(entry_amount)
            trade_id = f"trade-{len(trades) + 1}"
            trade = {
                "id": trade_id,
                "status": "open",
                "signal_date": str(record.get("signal_date") or ""),
                "entry_date": trading_date,
                "exit_date": "",
                "symbol": symbol,
                "name": str(entry_bar.name or record.get("name") or ""),
                "strategy_id": selected.strategy_id,
                "score": selected.score,
                "entry_price": entry_price,
                "exit_price": None,
                "holding_sessions": None,
                "gross_return_pct": None,
                "net_return_pct": None,
                "mark_date": trading_date,
                "mark_price": entry_bar.close,
                "mark_net_return_pct": None,
                "exit_signal": "",
                "exit_reason": "",
                "exit_legs": [],
            }
            position = {
                "trade": trade,
                "symbol": symbol,
                "strategy_id": selected.strategy_id,
                "entry_date": trading_date,
                "entry_session_index": session_index,
                "entry_price": entry_price,
                "entry_units": units,
                "remaining_units": units,
                "entry_amount": entry_amount,
                "entry_fee": entry_fee,
                "entry_total": entry_amount + entry_fee,
                "realized_gross_proceeds": 0.0,
                "realized_net_proceeds": 0.0,
                "partial_tp_done": False,
            }
            try:
                position.update(dict(
                    exit_strategy.on_entry(selected, entry_bar, entry_price) or {}
                ))
            except Exception as exc:
                raise SelectionBacktestError(
                    f"exit strategy failed while opening {symbol}: {exc}"
                ) from exc
            positions[symbol] = position
            trades.append(trade)
            record["status"] = "evaluated"
            record["entry_open"] = entry_bar.open
            record["entry_price"] = entry_price
            record["trade_id"] = trade_id

        if (
            config.signal_end_date
            and trading_date > config.signal_end_date
            and not positions
            and not pending_entries
        ):
            break
        if session_index < first_selector_session:
            continue
        history_stops = {
            symbol: len(symbol_history)
            for symbol, symbol_history in histories.items()
        }
        context = SelectionContext(
            date=trading_date,
            session_index=session_index,
            bars=MappingProxyType(dict(current_bars)),
            histories=_PrefixMapping(histories, history_stops),
            strategy_rows=(
                _PrefixMapping(prepared_rows, history_stops)
                if prepared_rows else MappingProxyType({})
            ),
        )
        within_signal_window = (
            (not config.signal_start_date or trading_date >= config.signal_start_date)
            and (not config.signal_end_date or trading_date <= config.signal_end_date)
        )
        set_diagnostics_enabled = getattr(selector, "set_diagnostics_enabled", None)
        if callable(set_diagnostics_enabled):
            set_diagnostics_enabled(within_signal_window)
        set_signal_generation_enabled = getattr(
            selector,
            "set_signal_generation_enabled",
            None,
        )
        if callable(set_signal_generation_enabled):
            set_signal_generation_enabled(within_signal_window)
        set_exit_tracking_symbols = getattr(
            selector,
            "set_exit_tracking_symbols",
            None,
        )
        if callable(set_exit_tracking_symbols):
            set_exit_tracking_symbols(positions)
        generated_signals = _call_selector(selector, context)
        completed_sessions += 1
        if progress_callback is not None:
            progress_callback(
                min(completed_sessions, len(evaluation_dates)),
                len(evaluation_dates),
                trading_date,
            )

        exited_symbols: set[str] = set()
        for symbol, position in tuple(positions.items()):
            current_bar = current_bars.get(symbol)
            if current_bar is None:
                continue
            trade = position["trade"]
            trade["mark_date"] = trading_date
            trade["mark_price"] = current_bar.close
            trade["mark_net_return_pct"] = _trade_mark_to_market(
                position,
                current_bar.close,
                config,
            )
            if session_index <= int(position["entry_session_index"]):
                continue
            try:
                decision = exit_strategy.on_close(position, context, selector)
            except Exception as exc:
                raise SelectionBacktestError(
                    f"exit strategy failed after {trading_date} close for {symbol}: {exc}"
                ) from exc
            if decision is None:
                continue
            history = context.history(symbol)
            previous_close = history[-2].close if len(history) >= 2 else None
            limits = (
                config.price_limit_resolver(current_bar, previous_close)
                if config.price_limit_resolver is not None
                else (current_bar.limit_up, current_bar.limit_down)
            )
            if limits[1] is not None and current_bar.close <= limits[1] + 1e-9:
                position["deferred_exit_signal"] = decision.signal
                position["deferred_exit_reason"] = decision.reason
                continue
            exit_price = _fill_price(
                current_bar,
                entry=False,
                slippage_bps=float(config.slippage_bps),
                reference_price=decision.fill_reference_price,
            )
            remaining_units = float(position.get("remaining_units") or 0.0)
            exit_units = (
                remaining_units
                if decision.sell_ratio >= 1.0 - 1e-12
                else remaining_units * decision.sell_ratio
            )
            exit_amount = exit_price * exit_units
            exit_fee = config.cost_model.exit_fee(exit_amount)
            leg = {
                "date": trading_date,
                "price": exit_price,
                "units": round(exit_units, 6),
                "sell_ratio": round(exit_units / remaining_units, 6),
                "signal": decision.signal,
                "reason": decision.reason,
                "fee": exit_fee,
                "metadata": dict(decision.metadata),
            }
            trade["exit_legs"].append(leg)
            position["remaining_units"] = max(0.0, remaining_units - exit_units)
            position["realized_gross_proceeds"] = (
                float(position.get("realized_gross_proceeds") or 0.0)
                + exit_amount
            )
            position["realized_net_proceeds"] = (
                float(position.get("realized_net_proceeds") or 0.0)
                + exit_amount
                - exit_fee
            )
            if position["remaining_units"] > 1e-8:
                position["partial_tp_done"] = True
                trade["mark_net_return_pct"] = _trade_mark_to_market(
                    position,
                    current_bar.close,
                    config,
                )
                continue
            weighted_exit = (
                sum(float(item["price"]) * float(item["units"]) for item in trade["exit_legs"])
                / float(position["entry_units"])
            )
            entry_amount = float(position["entry_amount"])
            entry_total = float(position["entry_total"])
            gross_return = (
                float(position["realized_gross_proceeds"]) / entry_amount - 1.0
                if entry_amount > 0 else 0.0
            )
            net_return = (
                float(position["realized_net_proceeds"]) / entry_total - 1.0
                if entry_total > 0 else 0.0
            )
            trade.update({
                "status": "completed",
                "exit_date": trading_date,
                "exit_price": round(weighted_exit, 4),
                "holding_sessions": (
                    session_index - int(position["entry_session_index"])
                ),
                "gross_return_pct": round(gross_return * 100, 4),
                "net_return_pct": round(net_return * 100, 4),
                "mark_net_return_pct": None,
                "exit_signal": decision.signal,
                "exit_reason": decision.reason,
            })
            positions.pop(symbol, None)
            exited_symbols.add(symbol)

        if not within_signal_window:
            continue
        for selected in generated_signals:
            signal_bar = current_bars.get(selected.symbol)
            record: dict[str, Any] = {
                "signal_date": trading_date,
                "entry_date": "",
                "symbol": selected.symbol,
                "name": str(signal_bar.name or "") if signal_bar is not None else "",
                "strategy_id": selected.strategy_id,
                "score": selected.score,
                "reason": selected.reason,
                "metadata": dict(selected.metadata),
                "status": "rejected",
                "status_reason": "",
                "entry_open": None,
                "entry_price": None,
                "forward_returns": {},
            }
            signals.append(record)
            if selected.symbol not in bars:
                record["status_reason"] = "unknown_symbol"
                continue
            if selected.symbol in positions and selected.symbol not in exited_symbols:
                record["status"] = "skipped"
                record["status_reason"] = "position_open"
                continue
            if selected.symbol in pending_entries:
                record["status"] = "skipped"
                record["status_reason"] = "entry_pending"
                continue
            if session_index + 1 >= len(trading_dates):
                record["status_reason"] = "no_next_session"
                continue
            pending_entries[selected.symbol] = (
                record,
                selected,
                session_index + 1,
            )

    for record, _selected, _entry_session in pending_entries.values():
        record["status_reason"] = "no_next_session"

    frozen_signals: list[Mapping[str, Any]] = []
    for raw in signals:
        row = dict(raw)
        row["metadata"] = MappingProxyType(dict(row.get("metadata") or {}))
        row["forward_returns"] = MappingProxyType({})
        frozen_signals.append(MappingProxyType(row))
    frozen_trades = tuple(MappingProxyType({
        **dict(raw),
        "exit_legs": tuple(
            MappingProxyType(dict(item)) for item in raw.get("exit_legs") or ()
        ),
    }) for raw in trades)
    diagnostics_reader = getattr(selector, "diagnostics", None)
    diagnostics = diagnostics_reader() if callable(diagnostics_reader) else {}
    return SelectionBacktestResult(
        signals=tuple(frozen_signals),
        statistics=MappingProxyType(_trade_statistics(signals, trades, config)),
        trades=frozen_trades,
        diagnostics=MappingProxyType(dict(diagnostics or {})),
    )


def _portfolio_equity(
    cash: float,
    positions: Mapping[str, Mapping[str, Any]],
    marks: Mapping[str, float],
) -> tuple[float, float]:
    market_value = sum(
        max(0.0, float(position.get("remaining_units") or 0.0))
        * max(
            0.0,
            float(
                marks.get(symbol)
                or position.get("last_price")
                or position.get("entry_price")
                or 0.0
            ),
        )
        for symbol, position in positions.items()
    )
    return cash + market_value, market_value


def _consume_position_lots(
    position: dict[str, Any],
    units: int,
    session_index: int,
) -> None:
    remaining = int(units)
    kept: list[dict[str, Any]] = []
    for raw_lot in position.get("lots") or ():
        lot = dict(raw_lot)
        lot_units = int(lot.get("units") or 0)
        if remaining > 0 and int(lot.get("session_index") or 0) < session_index:
            consumed = min(remaining, lot_units)
            lot_units -= consumed
            remaining -= consumed
        if lot_units > 0:
            lot["units"] = lot_units
            kept.append(lot)
    position["lots"] = kept


def _portfolio_result(
    initial_cash: float,
    cash: float,
    positions: Mapping[str, Mapping[str, Any]],
    marks: Mapping[str, float],
    equity_curve: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    final_equity, final_market_value = _portfolio_equity(cash, positions, marks)
    peak = initial_cash
    peak_date = ""
    max_drawdown = 0.0
    max_drawdown_peak_date = ""
    max_drawdown_trough_date = ""
    for point in equity_curve:
        equity = float(point.get("equity") or 0.0)
        point_date = str(point.get("date") or "")
        if equity > peak:
            peak = equity
            peak_date = point_date
        if peak > 0:
            drawdown = (equity / peak - 1.0) * 100.0
            if drawdown < max_drawdown:
                max_drawdown = drawdown
                max_drawdown_peak_date = peak_date
                max_drawdown_trough_date = point_date
    entry_legs = [
        leg for trade in trades for leg in (trade.get("entry_legs") or ())
    ]
    exit_legs = [
        leg for trade in trades for leg in (trade.get("exit_legs") or ())
    ]
    daily_returns: list[float] = []
    previous_equity = initial_cash
    for point in equity_curve:
        equity = float(point.get("equity") or 0.0)
        if previous_equity > 0 and equity > 0:
            daily_returns.append(equity / previous_equity - 1.0)
        previous_equity = equity
    trading_sessions = len(equity_curve)
    annualized_return = (
        (final_equity / initial_cash) ** (TRADING_DAYS_PER_YEAR / trading_sessions)
        - 1.0
        if initial_cash > 0 and final_equity > 0 and trading_sessions > 0
        else None
    )
    daily_volatility = (
        statistics.stdev(daily_returns) if len(daily_returns) >= 2 else None
    )
    sharpe_ratio = (
        statistics.mean(daily_returns) / daily_volatility
        * math.sqrt(TRADING_DAYS_PER_YEAR)
        if daily_volatility is not None and daily_volatility > 0
        else None
    )
    downside_deviation = (
        math.sqrt(
            sum(min(value, 0.0) ** 2 for value in daily_returns)
            / len(daily_returns)
        )
        if daily_returns else None
    )
    sortino_ratio = (
        statistics.mean(daily_returns) / downside_deviation
        * math.sqrt(TRADING_DAYS_PER_YEAR)
        if downside_deviation is not None and downside_deviation > 0
        else None
    )
    exposures = [
        float(point.get("market_value") or 0.0)
        / float(point.get("equity") or 1.0) * 100.0
        for point in equity_curve
        if float(point.get("equity") or 0.0) > 0
    ]
    position_counts = [
        int(point.get("position_count") or 0) for point in equity_curve
    ]
    average_equity = (
        statistics.mean(
            float(point.get("equity") or 0.0) for point in equity_curve
        )
        if equity_curve else initial_cash
    )
    buy_gross = sum(
        float(leg.get("price") or 0.0) * float(leg.get("units") or 0.0)
        for leg in entry_legs
    )
    sell_gross = sum(
        float(leg.get("price") or 0.0) * float(leg.get("units") or 0.0)
        for leg in exit_legs
    )
    turnover_pct = (
        (buy_gross + sell_gross) / 2.0 / average_equity * 100.0
        if average_equity > 0 else None
    )
    return {
        "initial_cash": round(initial_cash, 2),
        "final_cash": round(cash, 2),
        "final_market_value": round(final_market_value, 2),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(
            (final_equity / initial_cash - 1.0) * 100.0,
            4,
        ) if initial_cash > 0 else None,
        "max_drawdown_pct": round(max_drawdown, 4),
        "max_drawdown_peak_date": max_drawdown_peak_date,
        "max_drawdown_trough_date": max_drawdown_trough_date,
        "trading_session_count": trading_sessions,
        "annualized_return_pct": (
            round(annualized_return * 100.0, 4)
            if annualized_return is not None else None
        ),
        "annualized_volatility_pct": (
            round(
                daily_volatility * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0,
                4,
            )
            if daily_volatility is not None else None
        ),
        "sharpe_ratio": (
            round(sharpe_ratio, 4) if sharpe_ratio is not None else None
        ),
        "sortino_ratio": (
            round(sortino_ratio, 4) if sortino_ratio is not None else None
        ),
        "calmar_ratio": (
            round(annualized_return * 100.0 / abs(max_drawdown), 4)
            if annualized_return is not None and max_drawdown < 0 else None
        ),
        "average_exposure_pct": (
            round(statistics.mean(exposures), 4) if exposures else 0.0
        ),
        "max_exposure_pct": round(max(exposures), 4) if exposures else 0.0,
        "average_position_count": (
            round(statistics.mean(position_counts), 4)
            if position_counts else 0.0
        ),
        "turnover_pct": (
            round(turnover_pct, 4) if turnover_pct is not None else None
        ),
        "open_position_count": len(positions),
        "buy_order_count": len(entry_legs),
        "open_order_count": sum(leg.get("action") == "open" for leg in entry_legs),
        "add_order_count": sum(leg.get("action") == "add" for leg in entry_legs),
        "sell_order_count": len(exit_legs),
        "equity_curve": [dict(point) for point in equity_curve],
    }


def _strategy_portfolio_statistics(
    signals: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
    config: SelectionBacktestConfig,
    portfolio: Mapping[str, Any],
) -> dict[str, Any]:
    values = _trade_statistics(signals, trades, config)
    values.update({
        "evaluation_mode": "strategy_portfolio",
        "portfolio_return_pct": portfolio.get("total_return_pct"),
        "max_drawdown_pct": portfolio.get("max_drawdown_pct"),
        "buy_order_count": portfolio.get("buy_order_count", 0),
        "open_order_count": portfolio.get("open_order_count", 0),
        "add_order_count": portfolio.get("add_order_count", 0),
        "sell_order_count": portfolio.get("sell_order_count", 0),
        "annualized_return_pct": portfolio.get("annualized_return_pct"),
        "annualized_volatility_pct": portfolio.get("annualized_volatility_pct"),
        "sharpe_ratio": portfolio.get("sharpe_ratio"),
        "sortino_ratio": portfolio.get("sortino_ratio"),
        "calmar_ratio": portfolio.get("calmar_ratio"),
        "average_exposure_pct": portfolio.get("average_exposure_pct"),
        "max_exposure_pct": portfolio.get("max_exposure_pct"),
        "turnover_pct": portfolio.get("turnover_pct"),
        "entry_status_counts": dict(Counter(
            str(row.get("status") or "unknown") for row in signals
        )),
        "entry_rejection_counts": dict(Counter(
            str(row.get("status_reason") or "unknown")
            for row in signals
            if row.get("status") != "evaluated"
        )),
        "duplicate_signal_count": sum(
            row.get("status") == "skipped" for row in signals
        ),
    })
    return values


def _run_strategy_portfolio_backtest(
    bars: Mapping[str, Mapping[str, HistoricalBar]],
    trading_dates: Sequence[str],
    prepared_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    selector: SelectionStrategy | SelectionFunction,
    strategy: PositionExitStrategy,
    config: SelectionBacktestConfig,
    *,
    progress_callback: SelectionProgress | None,
) -> SelectionBacktestResult:
    """Replay a strategy-owned account with cash, lots, adds and T+1 exits."""
    histories: dict[str, list[HistoricalBar]] = {symbol: [] for symbol in bars}
    signals: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    positions: dict[str, dict[str, Any]] = {}
    pending_entries: dict[str, tuple[dict[str, Any], SelectionSignal, int]] = {}
    initial_cash = float(getattr(strategy, "initial_cash", 1_000_000.0))
    cash = initial_cash
    marks: dict[str, float] = {}
    equity_curve: list[dict[str, Any]] = []
    first_selector_session = _first_selector_session(
        trading_dates,
        selector,
        config.signal_start_date,
    )
    reset_selector = getattr(selector, "reset", None)
    if callable(reset_selector):
        reset_selector()
    reset_strategy = getattr(strategy, "reset", None)
    if callable(reset_strategy):
        reset_strategy()
    evaluation_dates = tuple(trading_dates[first_selector_session:])
    completed_sessions = 0

    for session_index, trading_date in enumerate(trading_dates):
        current_bars = {
            symbol: bar
            for symbol, series in bars.items()
            if (bar := series.get(trading_date)) is not None
        }
        for symbol, bar in current_bars.items():
            histories[symbol].append(bar)
            if symbol in positions:
                positions[symbol]["last_price"] = bar.open
                marks[symbol] = bar.open

        new_positions_today = 0
        for symbol, (record, selected, entry_session_index) in tuple(
            pending_entries.items()
        ):
            if entry_session_index != session_index:
                continue
            pending_entries.pop(symbol, None)
            entry_bar = current_bars.get(symbol)
            record["entry_date"] = trading_date
            if entry_bar is None:
                record["status_reason"] = "missing_next_session_bar"
                continue
            if entry_bar.suspended or (
                config.reject_zero_volume and entry_bar.volume <= 0
            ):
                record["status_reason"] = "suspended_or_zero_volume"
                continue
            signal_bar = bars[symbol].get(str(record.get("signal_date") or ""))
            limits = (
                config.price_limit_resolver(
                    entry_bar,
                    signal_bar.close if signal_bar is not None else None,
                )
                if config.price_limit_resolver is not None
                else (entry_bar.limit_up, entry_bar.limit_down)
            )
            if limits[0] is not None and entry_bar.open >= limits[0] - 1e-9:
                record["status_reason"] = "open_at_limit_up"
                continue
            position = positions.get(symbol)
            schedule_block = getattr(strategy, "schedule_block_reason", None)
            if position is not None and callable(schedule_block):
                blocked = str(
                    schedule_block(position, selected, str(record.get("signal_date") or ""))
                    or ""
                )
                if blocked:
                    record["status"] = "skipped"
                    record["status_reason"] = blocked
                    continue
            entry_price = _fill_price(
                entry_bar,
                entry=True,
                slippage_bps=float(config.slippage_bps),
            )
            total_equity, _market_value = _portfolio_equity(cash, positions, marks)
            try:
                decision = strategy.size_entry(
                    selected,
                    entry_bar,
                    entry_price,
                    position,
                    positions,
                    marks,
                    cash,
                    total_equity,
                    new_positions_today,
                    config.cost_model,
                )
            except Exception as exc:
                raise SelectionBacktestError(
                    f"entry strategy failed while sizing {symbol}: {exc}"
                ) from exc
            if not isinstance(decision, PortfolioEntryDecision):
                raise SelectionBacktestError(
                    "portfolio entry strategy must return PortfolioEntryDecision"
                )
            decision_metadata = decision.state.get("decision_metadata")
            if isinstance(decision_metadata, Mapping):
                record["entry_decision_metadata"] = dict(decision_metadata)
            if decision.action == "reject" or decision.units <= 0:
                record["status_reason"] = decision.reason or "entry_risk_rejected"
                continue
            units = int(decision.units)
            entry_amount = entry_price * units
            entry_fee = config.cost_model.entry_fee(entry_amount)
            entry_total = entry_amount + entry_fee
            if entry_total > cash + 1e-9:
                record["status_reason"] = "insufficient_cash"
                continue
            cash -= entry_total
            lot = {
                "date": trading_date,
                "session_index": session_index,
                "price": entry_price,
                "units": units,
                "strategy_id": selected.strategy_id,
                "action": decision.action,
                "fee": entry_fee,
                "metadata": (
                    dict(decision_metadata)
                    if isinstance(decision_metadata, Mapping) else {}
                ),
            }
            if position is None:
                trade_id = f"trade-{len(trades) + 1}"
                trade = {
                    "id": trade_id,
                    "status": "open",
                    "signal_date": str(record.get("signal_date") or ""),
                    "entry_date": trading_date,
                    "exit_date": "",
                    "symbol": symbol,
                    "name": str(entry_bar.name or record.get("name") or ""),
                    "strategy_id": selected.strategy_id,
                    "current_strategy_id": selected.strategy_id,
                    "strategy_path": [selected.strategy_id],
                    "score": selected.score,
                    "entry_price": entry_price,
                    "entry_units": units,
                    "exit_price": None,
                    "holding_sessions": None,
                    "gross_return_pct": None,
                    "net_return_pct": None,
                    "mark_date": trading_date,
                    "mark_price": entry_bar.close,
                    "mark_net_return_pct": None,
                    "exit_signal": "",
                    "exit_reason": "",
                    "entry_legs": [lot],
                    "exit_legs": [],
                }
                position = {
                    "trade": trade,
                    "symbol": symbol,
                    "strategy_id": selected.strategy_id,
                    "entry_date": trading_date,
                    "entry_session_index": session_index,
                    "last_entry_session_index": session_index,
                    "entry_price": entry_price,
                    "avg_cost": entry_total / units,
                    "entry_units": units,
                    "remaining_units": units,
                    "entry_amount": entry_amount,
                    "entry_fee": entry_fee,
                    "entry_total": entry_total,
                    "realized_gross_proceeds": 0.0,
                    "realized_net_proceeds": 0.0,
                    "partial_tp_done": False,
                    "lots": [lot],
                    "last_price": entry_price,
                }
                try:
                    position.update(dict(
                        strategy.on_entry(selected, entry_bar, entry_price) or {}
                    ))
                except Exception as exc:
                    raise SelectionBacktestError(
                        f"exit strategy failed while opening {symbol}: {exc}"
                    ) from exc
                position.update(dict(decision.state))
                positions[symbol] = position
                trades.append(trade)
                new_positions_today += 1
            else:
                trade = position["trade"]
                old_units = int(position.get("remaining_units") or 0)
                old_avg_cost = float(position.get("avg_cost") or 0.0)
                old_stop = float(position.get("entry_stop_price") or 0.0)
                position["remaining_units"] = old_units + units
                position["entry_units"] = int(position.get("entry_units") or 0) + units
                position["entry_amount"] = float(position.get("entry_amount") or 0.0) + entry_amount
                position["entry_fee"] = float(position.get("entry_fee") or 0.0) + entry_fee
                position["entry_total"] = float(position.get("entry_total") or 0.0) + entry_total
                position["avg_cost"] = (
                    old_units * old_avg_cost + entry_total
                ) / position["remaining_units"]
                position["entry_price"] = position["entry_amount"] / position["entry_units"]
                position["last_entry_session_index"] = session_index
                position.setdefault("lots", []).append(lot)
                on_add = getattr(strategy, "on_add", None)
                if callable(on_add):
                    position.update(dict(
                        on_add(position, selected, entry_bar, entry_price) or {}
                    ))
                position.update(dict(decision.state))
                position["entry_stop_price"] = max(
                    old_stop,
                    float(position.get("entry_stop_price") or 0.0),
                )
                resulting_strategy_id = selected.strategy_id
                strategy_id_after_add = getattr(
                    strategy,
                    "strategy_id_after_add",
                    None,
                )
                if callable(strategy_id_after_add):
                    try:
                        resulting_strategy_id = str(
                            strategy_id_after_add(position, selected)
                            or selected.strategy_id
                        )
                    except Exception as exc:
                        raise SelectionBacktestError(
                            "portfolio entry strategy failed while resolving "
                            f"the post-add strategy for {symbol}: {exc}"
                        ) from exc
                position["strategy_id"] = resulting_strategy_id
                trade["current_strategy_id"] = resulting_strategy_id
                if selected.strategy_id not in trade["strategy_path"]:
                    trade["strategy_path"].append(selected.strategy_id)
                trade["entry_price"] = round(float(position["entry_price"]), 4)
                trade["entry_units"] = int(position["entry_units"])
                trade["entry_legs"].append(lot)
                trade_id = str(trade["id"])
            entry_subroute = str(
                decision.state.get("niuone_entry_subroute") or ""
            )
            if entry_subroute:
                lot["niuone_entry_subroute"] = entry_subroute
                trade["niuone_entry_subroute"] = entry_subroute
            marks[symbol] = entry_price
            record.update({
                "status": "evaluated",
                "entry_open": entry_bar.open,
                "entry_price": entry_price,
                "entry_units": units,
                "entry_action": decision.action,
                "entry_total_equity": total_equity,
                "entry_target_position_pct": decision.state.get(
                    "target_position_pct"
                ),
                "entry_effective_loss_distance_pct": decision.state.get(
                    "effective_loss_distance_pct"
                ),
                "entry_position_before_trade_pct": decision.state.get(
                    "position_before_trade_pct"
                ),
                "entry_order_position_pct": decision.state.get(
                    "order_position_pct"
                ),
                "entry_position_after_trade_pct": decision.state.get(
                    "position_after_trade_pct"
                ),
                "trade_id": trade_id,
            })

        if (
            config.signal_end_date
            and trading_date > config.signal_end_date
            and not positions
            and not pending_entries
        ):
            break
        if session_index < first_selector_session:
            continue
        history_stops = {
            symbol: len(symbol_history)
            for symbol, symbol_history in histories.items()
        }
        context = SelectionContext(
            date=trading_date,
            session_index=session_index,
            bars=MappingProxyType(dict(current_bars)),
            histories=_PrefixMapping(histories, history_stops),
            strategy_rows=(
                _PrefixMapping(prepared_rows, history_stops)
                if prepared_rows else MappingProxyType({})
            ),
        )
        within_signal_window = (
            (not config.signal_start_date or trading_date >= config.signal_start_date)
            and (not config.signal_end_date or trading_date <= config.signal_end_date)
        )
        set_diagnostics_enabled = getattr(selector, "set_diagnostics_enabled", None)
        if callable(set_diagnostics_enabled):
            set_diagnostics_enabled(within_signal_window)
        set_signal_generation_enabled = getattr(
            selector,
            "set_signal_generation_enabled",
            None,
        )
        if callable(set_signal_generation_enabled):
            set_signal_generation_enabled(within_signal_window)
        set_exit_tracking_symbols = getattr(selector, "set_exit_tracking_symbols", None)
        if callable(set_exit_tracking_symbols):
            set_exit_tracking_symbols(positions)
        generated_signals = _call_selector(selector, context)
        completed_sessions += 1
        had_portfolio_activity = within_signal_window or bool(positions)

        for symbol, position in tuple(positions.items()):
            current_bar = current_bars.get(symbol)
            if current_bar is None:
                continue
            marks[symbol] = current_bar.close
            position["last_price"] = current_bar.close
            trade = position["trade"]
            trade["mark_date"] = trading_date
            trade["mark_price"] = current_bar.close
            trade["mark_net_return_pct"] = _trade_mark_to_market(
                position,
                current_bar.close,
                config,
            )
            available_units = sum(
                int(lot.get("units") or 0)
                for lot in position.get("lots") or ()
                if int(lot.get("session_index") or 0) < session_index
            )
            try:
                decision = strategy.on_close(position, context, selector)
            except Exception as exc:
                raise SelectionBacktestError(
                    f"exit strategy failed after {trading_date} close for {symbol}: {exc}"
                ) from exc
            if decision is None:
                continue
            if available_units <= 0:
                position["deferred_exit_signal"] = decision.signal
                position["deferred_exit_reason"] = decision.reason
                continue
            history = context.history(symbol)
            previous_close = history[-2].close if len(history) >= 2 else None
            limits = (
                config.price_limit_resolver(current_bar, previous_close)
                if config.price_limit_resolver is not None
                else (current_bar.limit_up, current_bar.limit_down)
            )
            if limits[1] is not None and current_bar.close <= limits[1] + 1e-9:
                position["deferred_exit_signal"] = decision.signal
                position["deferred_exit_reason"] = decision.reason
                continue
            exit_price = _fill_price(
                current_bar,
                entry=False,
                slippage_bps=float(config.slippage_bps),
                reference_price=decision.fill_reference_price,
            )
            if decision.sell_ratio >= 1.0 - 1e-12:
                exit_units = available_units
            else:
                exit_units = max(
                    int(getattr(strategy, "board_lot", 100)),
                    int(available_units * decision.sell_ratio)
                    // int(getattr(strategy, "board_lot", 100))
                    * int(getattr(strategy, "board_lot", 100)),
                )
                exit_units = min(available_units, exit_units)
            exit_amount = exit_price * exit_units
            exit_fee = config.cost_model.exit_fee(exit_amount)
            cash += exit_amount - exit_fee
            remaining_before = int(position.get("remaining_units") or 0)
            exit_leg = {
                "date": trading_date,
                "price": exit_price,
                "units": exit_units,
                "sell_ratio": round(exit_units / remaining_before, 6),
                "signal": decision.signal,
                "reason": decision.reason,
                "fee": exit_fee,
                "metadata": dict(decision.metadata),
            }
            trade["exit_legs"].append(exit_leg)
            position["remaining_units"] = max(0, remaining_before - exit_units)
            _consume_position_lots(position, exit_units, session_index)
            position["realized_gross_proceeds"] = (
                float(position.get("realized_gross_proceeds") or 0.0) + exit_amount
            )
            position["realized_net_proceeds"] = (
                float(position.get("realized_net_proceeds") or 0.0)
                + exit_amount - exit_fee
            )
            on_exit_filled = getattr(strategy, "on_exit_filled", None)
            if callable(on_exit_filled):
                try:
                    on_exit_filled(position, decision, exit_leg, context)
                except Exception as exc:
                    raise SelectionBacktestError(
                        "portfolio strategy failed while recording filled "
                        f"exit for {symbol}: {exc}"
                    ) from exc
            if decision.sell_ratio < 1.0 - 1e-12:
                position["partial_tp_done"] = True
            if int(position["remaining_units"]) > 0:
                trade["mark_net_return_pct"] = _trade_mark_to_market(
                    position,
                    current_bar.close,
                    config,
                )
                continue
            weighted_exit = sum(
                float(item["price"]) * float(item["units"])
                for item in trade["exit_legs"]
            ) / float(position["entry_units"])
            entry_amount = float(position["entry_amount"])
            entry_total = float(position["entry_total"])
            gross_return = float(position["realized_gross_proceeds"]) / entry_amount - 1.0
            net_return = float(position["realized_net_proceeds"]) / entry_total - 1.0
            trade.update({
                "status": "completed",
                "exit_date": trading_date,
                "exit_price": round(weighted_exit, 4),
                "holding_sessions": session_index - int(position["entry_session_index"]),
                "gross_return_pct": round(gross_return * 100.0, 4),
                "net_return_pct": round(net_return * 100.0, 4),
                "mark_net_return_pct": None,
                "exit_signal": decision.signal,
                "exit_reason": decision.reason,
            })
            positions.pop(symbol, None)
            marks.pop(symbol, None)

        if within_signal_window:
            for selected in generated_signals:
                signal_bar = current_bars.get(selected.symbol)
                record: dict[str, Any] = {
                    "signal_date": trading_date,
                    "entry_date": "",
                    "symbol": selected.symbol,
                    "name": str(signal_bar.name or "") if signal_bar is not None else "",
                    "strategy_id": selected.strategy_id,
                    "score": selected.score,
                    "reason": selected.reason,
                    "metadata": dict(selected.metadata),
                    "status": "rejected",
                    "status_reason": "",
                    "entry_open": None,
                    "entry_price": None,
                    "entry_units": None,
                    "entry_action": "",
                    "forward_returns": {},
                }
                signals.append(record)
                if selected.symbol not in bars:
                    record["status_reason"] = "unknown_symbol"
                    continue
                if selected.symbol in pending_entries:
                    record["status"] = "skipped"
                    record["status_reason"] = "entry_pending"
                    continue
                position = positions.get(selected.symbol)
                schedule_block = getattr(strategy, "schedule_block_reason", None)
                if position is not None and callable(schedule_block):
                    blocked = str(schedule_block(position, selected, trading_date) or "")
                    if blocked:
                        record["status"] = "skipped"
                        record["status_reason"] = blocked
                        continue
                if session_index + 1 >= len(trading_dates):
                    record["status_reason"] = "no_next_session"
                    continue
                pending_entries[selected.symbol] = (
                    record,
                    selected,
                    session_index + 1,
                )

        if (
            (not config.signal_start_date or trading_date >= config.signal_start_date)
            and (had_portfolio_activity or positions)
        ):
            close_marks = {
                symbol: (
                    current_bars[symbol].close
                    if symbol in current_bars else float(position.get("last_price") or 0.0)
                )
                for symbol, position in positions.items()
            }
            marks.update(close_marks)
            equity, market_value = _portfolio_equity(cash, positions, marks)
            equity_curve.append({
                "date": trading_date,
                "cash": round(cash, 2),
                "market_value": round(market_value, 2),
                "equity": round(equity, 2),
                "position_count": len(positions),
            })
        if progress_callback is not None:
            progress_callback(
                min(completed_sessions, len(evaluation_dates)),
                len(evaluation_dates),
                trading_date,
            )

    for record, _selected, _entry_session in pending_entries.values():
        record["status_reason"] = "no_next_session"

    portfolio = _portfolio_result(
        initial_cash,
        cash,
        positions,
        marks,
        equity_curve,
        trades,
    )
    frozen_signals: list[Mapping[str, Any]] = []
    for raw in signals:
        row = dict(raw)
        row["metadata"] = MappingProxyType(dict(row.get("metadata") or {}))
        row["forward_returns"] = MappingProxyType({})
        frozen_signals.append(MappingProxyType(row))
    frozen_trades = tuple(MappingProxyType({
        **dict(raw),
        "strategy_path": tuple(raw.get("strategy_path") or ()),
        "entry_legs": tuple(
            MappingProxyType(dict(item)) for item in raw.get("entry_legs") or ()
        ),
        "exit_legs": tuple(
            MappingProxyType(dict(item)) for item in raw.get("exit_legs") or ()
        ),
    }) for raw in trades)
    diagnostics_reader = getattr(selector, "diagnostics", None)
    diagnostics = diagnostics_reader() if callable(diagnostics_reader) else {}
    return SelectionBacktestResult(
        signals=tuple(frozen_signals),
        statistics=MappingProxyType(
            _strategy_portfolio_statistics(signals, trades, config, portfolio)
        ),
        trades=frozen_trades,
        portfolio=MappingProxyType(portfolio),
        diagnostics=MappingProxyType(dict(diagnostics or {})),
    )


def run_selection_backtest(
    bars_by_symbol: Mapping[str, Iterable[HistoricalBar | Mapping[str, Any]]],
    selector: SelectionStrategy | SelectionFunction,
    *,
    config: SelectionBacktestConfig | None = None,
    position_exit_strategy: PositionExitStrategy | None = None,
    progress_callback: SelectionProgress | None = None,
    preparation_callback: SelectionPreparation | None = None,
    normalization_progress_callback: SelectionPhaseProgress | None = None,
    preparation_progress_callback: SelectionPhaseProgress | None = None,
) -> SelectionBacktestResult:
    """Evaluate future returns of close-time selections without an account."""
    resolved = config or SelectionBacktestConfig()
    bars, trading_dates = _normalized_bars(
        bars_by_symbol,
        progress_callback=normalization_progress_callback,
    )
    histories: dict[str, list[HistoricalBar]] = {symbol: [] for symbol in bars}
    signals: list[dict[str, Any]] = []
    last_selected_session: dict[str, int] = {}
    first_selector_session = _first_selector_session(
        trading_dates,
        selector,
        resolved.signal_start_date,
    )
    prepared_rows: Mapping[str, Sequence[Mapping[str, Any]]] = {}
    if getattr(selector, "uses_prepared_strategy_rows", False):
        prepared_rows = _prepared_strategy_rows(
            bars,
            preparation_callback=preparation_callback,
            progress_callback=preparation_progress_callback,
        )
    if position_exit_strategy is not None:
        if getattr(position_exit_strategy, "portfolio_mode", False):
            return _run_strategy_portfolio_backtest(
                bars,
                trading_dates,
                prepared_rows,
                selector,
                position_exit_strategy,
                resolved,
                progress_callback=progress_callback,
            )
        return _run_trade_lifecycle_backtest(
            bars,
            trading_dates,
            prepared_rows,
            selector,
            position_exit_strategy,
            resolved,
            progress_callback=progress_callback,
        )
    reset_selector = getattr(selector, "reset", None)
    if callable(reset_selector):
        reset_selector()

    evaluation_dates = tuple(
        item for item in trading_dates[first_selector_session:]
        if not resolved.signal_end_date or item <= resolved.signal_end_date
    )
    completed_sessions = 0
    for session_index, trading_date in enumerate(trading_dates):
        if resolved.signal_end_date and trading_date > resolved.signal_end_date:
            break
        current_bars = {
            symbol: bar
            for symbol, series in bars.items()
            if (bar := series.get(trading_date)) is not None
        }
        for symbol, bar in current_bars.items():
            histories[symbol].append(bar)
        if session_index < first_selector_session:
            continue
        history_stops = {
            symbol: len(symbol_history)
            for symbol, symbol_history in histories.items()
        }
        context = SelectionContext(
            date=trading_date,
            session_index=session_index,
            bars=MappingProxyType(dict(current_bars)),
            histories=_PrefixMapping(histories, history_stops),
            strategy_rows=(
                _PrefixMapping(prepared_rows, history_stops)
                if prepared_rows else MappingProxyType({})
            ),
        )
        set_diagnostics_enabled = getattr(selector, "set_diagnostics_enabled", None)
        signal_generation_enabled = (
            not resolved.signal_start_date
            or trading_date >= resolved.signal_start_date
        )
        if callable(set_diagnostics_enabled):
            set_diagnostics_enabled(signal_generation_enabled)
        set_signal_generation_enabled = getattr(
            selector,
            "set_signal_generation_enabled",
            None,
        )
        if callable(set_signal_generation_enabled):
            set_signal_generation_enabled(signal_generation_enabled)
        generated_signals = _call_selector(selector, context)
        completed_sessions += 1
        if progress_callback is not None:
            progress_callback(
                min(completed_sessions, len(evaluation_dates)),
                len(evaluation_dates),
                trading_date,
            )
        if resolved.signal_start_date and trading_date < resolved.signal_start_date:
            continue

        for selected in generated_signals:
            signal_bar = current_bars.get(selected.symbol)
            record: dict[str, Any] = {
                "signal_date": trading_date,
                "entry_date": "",
                "symbol": selected.symbol,
                "name": str(signal_bar.name or "") if signal_bar is not None else "",
                "strategy_id": selected.strategy_id,
                "score": selected.score,
                "reason": selected.reason,
                "metadata": dict(selected.metadata),
                "status": "rejected",
                "status_reason": "",
                "entry_open": None,
                "entry_price": None,
                "forward_returns": {},
            }
            signals.append(record)
            if selected.symbol not in bars:
                record["status_reason"] = "unknown_symbol"
                continue
            previous_selection = last_selected_session.get(selected.symbol)
            if previous_selection is not None and (
                session_index - previous_selection <= int(resolved.cooldown_sessions)
            ):
                record["status"] = "skipped"
                record["status_reason"] = "cooldown"
                continue
            if session_index + 1 >= len(trading_dates):
                record["status_reason"] = "no_next_session"
                continue
            entry_session_index = session_index + 1
            entry_date = trading_dates[entry_session_index]
            entry_bar = bars[selected.symbol].get(entry_date)
            record["entry_date"] = entry_date
            if entry_bar is None:
                record["status_reason"] = "missing_next_session_bar"
                continue
            if entry_bar.suspended or (resolved.reject_zero_volume and entry_bar.volume <= 0):
                record["status_reason"] = "suspended_or_zero_volume"
                continue
            limits = (
                resolved.price_limit_resolver(
                    entry_bar,
                    signal_bar.close if signal_bar is not None else None,
                )
                if resolved.price_limit_resolver is not None
                else (entry_bar.limit_up, entry_bar.limit_down)
            )
            if limits[0] is not None and entry_bar.open >= limits[0] - 1e-9:
                record["status_reason"] = "open_at_limit_up"
                continue

            entry_price = _fill_price(
                entry_bar,
                entry=True,
                slippage_bps=float(resolved.slippage_bps),
            )
            record["status"] = "evaluated"
            record["entry_open"] = entry_bar.open
            record["entry_price"] = entry_price
            last_selected_session[selected.symbol] = session_index
            forward_returns: dict[int, dict[str, Any]] = {}
            for horizon in resolved.holding_sessions:
                exit_session_index = entry_session_index + horizon - 1
                if exit_session_index >= len(trading_dates):
                    continue
                exit_date = trading_dates[exit_session_index]
                exit_bar = bars[selected.symbol].get(exit_date)
                if exit_bar is None:
                    continue
                exit_price = _fill_price(
                    exit_bar,
                    entry=False,
                    slippage_bps=float(resolved.slippage_bps),
                )
                evaluation_units = int(resolved.evaluation_lot_size)
                entry_amount = entry_price * evaluation_units
                exit_amount = exit_price * evaluation_units
                entry_fee = resolved.cost_model.entry_fee(entry_amount)
                exit_fee = resolved.cost_model.exit_fee(exit_amount)
                gross_return = exit_bar.close / entry_bar.open - 1.0
                net_return = (
                    (exit_amount - exit_fee) / (entry_amount + entry_fee) - 1.0
                    if entry_amount + entry_fee > 0 else 0.0
                )
                forward_returns[horizon] = {
                    "exit_date": exit_date,
                    "exit_close": exit_bar.close,
                    "exit_price": exit_price,
                    "gross_return_pct": round(gross_return * 100, 4),
                    "net_return_pct": round(net_return * 100, 4),
                }
            record["forward_returns"] = forward_returns
            if not forward_returns:
                record["status"] = "rejected"
                record["status_reason"] = "insufficient_forward_data"
                last_selected_session.pop(selected.symbol, None)

    frozen_signals: list[Mapping[str, Any]] = []
    for raw in signals:
        row = dict(raw)
        row["metadata"] = MappingProxyType(dict(row.get("metadata") or {}))
        returns = row.get("forward_returns")
        if isinstance(returns, Mapping):
            row["forward_returns"] = MappingProxyType({
                int(horizon): MappingProxyType(dict(detail))
                for horizon, detail in returns.items()
                if isinstance(detail, Mapping)
            })
        frozen_signals.append(MappingProxyType(row))
    diagnostics_reader = getattr(selector, "diagnostics", None)
    diagnostics = diagnostics_reader() if callable(diagnostics_reader) else {}
    return SelectionBacktestResult(
        signals=tuple(frozen_signals),
        statistics=MappingProxyType(_selection_statistics(signals, resolved)),
        diagnostics=MappingProxyType(dict(diagnostics or {})),
    )


class _StrategyRowsView(Sequence[Mapping[str, Any]]):
    """Read-only tail window with an optional one-row metadata overlay."""

    __slots__ = ("_values", "_start", "_stop", "_overlay_index", "_overlay")

    def __init__(
        self,
        values: Sequence[Mapping[str, Any]],
        *,
        start: int = 0,
        stop: int | None = None,
        overlay_index: int | None = None,
        overlay: Mapping[str, Any] | None = None,
    ) -> None:
        resolved_stop = len(values) if stop is None else int(stop)
        self._values = values
        self._start = max(0, min(len(values), int(start)))
        self._stop = max(self._start, min(len(values), resolved_stop))
        self._overlay_index = overlay_index
        self._overlay = overlay

    def __len__(self) -> int:
        return self._stop - self._start

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step == 1:
                return type(self)(
                    self._values,
                    start=self._start + start,
                    stop=self._start + stop,
                    overlay_index=self._overlay_index,
                    overlay=self._overlay,
                )
            return tuple(self[position] for position in range(start, stop, step))
        resolved = int(index)
        if resolved < 0:
            resolved += len(self)
        if resolved < 0 or resolved >= len(self):
            raise IndexError(index)
        absolute = self._start + resolved
        if absolute == self._overlay_index and self._overlay is not None:
            return self._overlay
        return self._values[absolute]

    def __iter__(self):
        for absolute in range(self._start, self._stop):
            if absolute == self._overlay_index and self._overlay is not None:
                yield self._overlay
            else:
                yield self._values[absolute]

    def with_latest(self, values: Mapping[str, Any]) -> _StrategyRowsView:
        if not self:
            return self
        latest = dict(self[-1])
        latest.update(values)
        return type(self)(
            self._values,
            start=self._start,
            stop=self._stop,
            overlay_index=self._stop - 1,
            overlay=MappingProxyType(latest),
        )


def _strategy_rows_with_latest_values(
    rows: Sequence[Mapping[str, Any]],
    values: Mapping[str, Any],
) -> Sequence[Mapping[str, Any]]:
    if not rows:
        return rows
    if isinstance(rows, _StrategyRowsView):
        return rows.with_latest(values)
    copied = list(rows)
    latest = dict(copied[-1])
    latest.update(values)
    copied[-1] = latest
    return copied


def _strategy_rows_at_close(
    context: SelectionContext,
    symbol: str,
    *,
    history_limit: int | None,
) -> Sequence[Mapping[str, Any]]:
    prepared = context.strategy_history(symbol)
    if prepared:
        if history_limit is not None:
            return _StrategyRowsView(
                prepared,
                start=max(0, len(prepared) - history_limit),
            )
        return list(prepared)
    rows: list[dict[str, Any]] = [
        bar.as_strategy_row() for bar in context.history(symbol)
    ]
    enrich_rows(rows)
    if history_limit is not None:
        rows = rows[-history_limit:]
    return rows


class RegisteredScorerSelector:
    """Adapt registered entry scorers to stock-selection signals."""

    def __init__(
        self,
        strategy_ids: Sequence[str],
        *,
        max_signals_per_session: int = 5,
        max_signals_per_strategy_per_session: Mapping[str, int] | None = None,
        context_provider: ScorerContextProvider | None = None,
        eligible_symbols: Iterable[str] | None = None,
        scorers: Mapping[str, Callable[..., dict[str, Any] | None]] | None = None,
    ) -> None:
        requested = tuple(dict.fromkeys(str(item or "").strip() for item in strategy_ids))
        available = dict(scorers or STRATEGY_SCORERS)
        unknown = [strategy_id for strategy_id in requested if strategy_id not in available]
        if not requested:
            raise SelectionBacktestError("at least one strategy_id is required")
        if unknown:
            raise SelectionBacktestError(f"unknown strategy ids: {', '.join(unknown)}")
        if int(max_signals_per_session) <= 0:
            raise SelectionBacktestError("max_signals_per_session must be positive")
        strategy_limits = {
            str(strategy_id): int(limit)
            for strategy_id, limit in (max_signals_per_strategy_per_session or {}).items()
        }
        if any(strategy_id not in requested for strategy_id in strategy_limits):
            raise SelectionBacktestError("strategy signal limit contains an unknown strategy")
        if any(limit <= 0 for limit in strategy_limits.values()):
            raise SelectionBacktestError("strategy signal limits must be positive")
        self.strategy_ids = requested
        self.scorers = {strategy_id: available[strategy_id] for strategy_id in requested}
        self.max_signals_per_session = int(max_signals_per_session)
        self.max_signals_per_strategy_per_session = MappingProxyType(strategy_limits)
        self.context_provider = context_provider
        self.uses_prepared_strategy_rows = True
        # Technical indicators retain the full downloaded history. Stateful
        # providers may declare a smaller bounded replay window for their own
        # market/theme state; unknown providers keep the legacy full warmup.
        self.backtest_warmup_sessions = (
            getattr(context_provider, "backtest_warmup_sessions", None)
            if context_provider is not None
            else 0
        )
        self.backtest_earliest_state_session = (
            getattr(context_provider, "backtest_earliest_state_session", None)
            if context_provider is not None
            else None
        )
        self._trusted_scorers = scorers is None
        self._history_limit = (
            BUILTIN_STRATEGY_HISTORY_LIMIT if self._trusted_scorers else None
        )
        self.eligible_symbols = (
            frozenset(_normalize_symbol(item) for item in eligible_symbols)
            if eligible_symbols is not None else None
        )
        self._diagnostics_enabled = False
        self._signal_generation_enabled = True
        self._exit_tracking_symbols: frozenset[str] = frozenset()
        self._latest_scored_by_symbol_strategy: dict[
            tuple[str, str], Mapping[str, Any]
        ] = {}
        self._replay_phase_callback: Callable[[str, str], None] | None = None
        self._reset_diagnostics()

    def _reset_diagnostics(self) -> None:
        self._diagnostics_by_strategy: dict[str, dict[str, Any]] = {
            strategy_id: _new_scorer_diagnostic_bucket()
            for strategy_id in self.strategy_ids
        }
        self._diagnostics_by_period: dict[
            str, dict[str, dict[str, Any]]
        ] = {}

    def reset(self) -> None:
        reset_provider = getattr(self.context_provider, "reset", None)
        if callable(reset_provider):
            reset_provider()
        self._diagnostics_enabled = False
        self._signal_generation_enabled = True
        self._exit_tracking_symbols = frozenset()
        self._latest_scored_by_symbol_strategy = {}
        self._replay_phase_callback = None
        self._reset_diagnostics()

    def set_replay_phase_callback(
        self,
        callback: Callable[[str, str], None] | None,
    ) -> None:
        """Expose the context/scoring boundary to a historical replay UI."""
        self._replay_phase_callback = callback

    def set_diagnostics_enabled(self, enabled: bool) -> None:
        """Limit persisted diagnostics to the user-requested signal window."""
        self._diagnostics_enabled = bool(enabled)

    def set_signal_generation_enabled(self, enabled: bool) -> None:
        """Skip stateless scorers while stateful context providers warm up."""
        self._signal_generation_enabled = bool(enabled)

    def set_exit_tracking_symbols(self, symbols: Iterable[str]) -> None:
        """Restrict post-window scoring to symbols with open backtest trades."""
        self._exit_tracking_symbols = frozenset(
            _normalize_symbol(symbol) for symbol in symbols
        )

    def latest_scored(
        self,
        symbol: str,
        strategy_id: str,
    ) -> Mapping[str, Any] | None:
        """Expose the current close's scorer snapshot to an exit state machine."""
        return self._latest_scored_by_symbol_strategy.get(
            (_normalize_symbol(symbol), str(strategy_id or "").strip())
        )

    def latest_cross_section(self) -> Mapping[str, Mapping[str, Any]]:
        """Expose a provider-owned compact cross section for replay research."""
        reader = getattr(self.context_provider, "latest_cross_section", None)
        if not callable(reader):
            return MappingProxyType({})
        values = reader()
        return values if isinstance(values, Mapping) else MappingProxyType({})

    def _record_diagnostic(
        self,
        strategy_id: str,
        context: SelectionContext,
        symbol: str,
        scored: Mapping[str, Any] | None,
    ) -> None:
        if not self._diagnostics_enabled:
            return
        period = str(context.date or "")[:7]
        period_buckets = self._diagnostics_by_period.setdefault(
            period,
            {
                item: _new_scorer_diagnostic_bucket()
                for item in self.strategy_ids
            },
        )
        self._record_diagnostic_bucket(
            self._diagnostics_by_strategy[strategy_id],
            context,
            symbol,
            scored,
        )
        self._record_diagnostic_bucket(
            period_buckets[strategy_id],
            context,
            symbol,
            scored,
        )

    @staticmethod
    def _record_diagnostic_bucket(
        bucket: dict[str, Any],
        context: SelectionContext,
        symbol: str,
        scored: Mapping[str, Any] | None,
    ) -> None:
        bucket["evaluated_count"] += 1
        if not isinstance(scored, Mapping):
            bucket["unscored_count"] += 1
            return
        bucket["scored_count"] += 1
        score = float(scored.get("score") or 0.0)
        threshold = float(scored.get("entry_threshold") or 8.0)
        blockers = [
            str(item)[:160]
            for item in (scored.get("hard_blockers") or ())
            if str(item).strip()
        ]
        actionable = bool(
            score >= threshold
            and not blockers
            and scored.get("actionable") is not False
        )
        current_maximum = bucket["maximum_score"]
        bucket["maximum_score"] = (
            score if current_maximum is None else max(float(current_maximum), score)
        )
        bucket["entry_threshold"] = threshold
        threshold_key = f"{threshold:g}"
        threshold_counts = bucket["entry_threshold_counts"]
        threshold_counts[threshold_key] = int(
            threshold_counts.get(threshold_key) or 0
        ) + 1
        if score < threshold:
            bucket["below_threshold_count"] += 1
        else:
            bucket["threshold_met_count"] += 1

        explicit_non_score_block = bool(
            not blockers
            and score >= threshold
            and scored.get("actionable") is False
        )
        if not blockers and not explicit_non_score_block:
            for offset in DIAGNOSTIC_SCORE_THRESHOLD_OFFSETS:
                if score + 1e-9 >= threshold + offset:
                    bucket["score_sensitivity_counts"][f"{offset:+g}"] += 1

        unique_blockers = tuple(dict.fromkeys(blockers))
        blocker_families = tuple(dict.fromkeys(
            _diagnostic_blocker_family(reason)
            for reason in unique_blockers
        ))
        family_counts = bucket["blocker_family_counts"]
        for family in blocker_families:
            family_counts[family] = int(family_counts.get(family) or 0) + 1
        if len(unique_blockers) == 1:
            reason = unique_blockers[0]
            exact_ablation = bucket["single_gate_ablation_counts"]
            if score + 1e-9 >= threshold:
                exact_ablation[reason] = int(exact_ablation.get(reason) or 0) + 1
        if len(blocker_families) == 1:
            family = blocker_families[0]
            family_ablation = bucket["family_ablation_counts"]
            if score + 1e-9 >= threshold:
                family_ablation[family] = int(
                    family_ablation.get(family) or 0
                ) + 1
            joint = bucket["joint_family_sensitivity_counts"].setdefault(
                family,
                {
                    f"{offset:+g}": 0
                    for offset in DIAGNOSTIC_SCORE_THRESHOLD_OFFSETS
                },
            )
            for offset in DIAGNOSTIC_SCORE_THRESHOLD_OFFSETS:
                if score + 1e-9 >= threshold + offset:
                    joint[f"{offset:+g}"] += 1

        industry = str(scored.get("industry") or "").strip()
        if industry and scored.get("stock_leader_tier") is True:
            branches = bucket["leader_branches"]
            branch = branches.setdefault(industry, {
                "industry": industry,
                "evaluated_count": 0,
                "threshold_met_count": 0,
                "actionable_candidate_count": 0,
                "maximum_score": None,
                "best_symbol": "",
                "best_date": "",
                "best_strategy_id": "",
                "best_entry_threshold": None,
                "best_actionable": False,
                "best_reasons": [],
                "best_blocker_family_counts": {},
                "lifecycle_stages": {},
                "blocker_family_counts": {},
            })
            branch["evaluated_count"] += 1
            if score >= threshold:
                branch["threshold_met_count"] += 1
            if actionable:
                branch["actionable_candidate_count"] += 1
            replace_best = bool(
                branch["maximum_score"] is None
                or score > float(branch["maximum_score"])
                or (
                    abs(score - float(branch["maximum_score"])) <= 1e-9
                    and actionable
                    and not branch["best_actionable"]
                )
            )
            if replace_best:
                branch["maximum_score"] = score
                branch["best_symbol"] = symbol
                branch["best_date"] = context.date
                branch["best_strategy_id"] = str(
                    scored.get("strategy_id") or ""
                )
                branch["best_entry_threshold"] = threshold
                branch["best_actionable"] = actionable
                best_reasons = list(unique_blockers)
                if score < threshold:
                    best_reasons.insert(
                        0,
                        f"评分{score:g}低于门槛{threshold:g}",
                    )
                elif explicit_non_score_block:
                    best_reasons.append("评分器标记为不可执行")
                branch["best_reasons"] = best_reasons
                branch["best_blocker_family_counts"] = {
                    family: 1 for family in blocker_families
                }
            lifecycle_stage = str(
                scored.get("niuone_lifecycle_stage") or "unknown"
            )
            stages = branch["lifecycle_stages"]
            stages[lifecycle_stage] = int(stages.get(lifecycle_stage) or 0) + 1
            branch_families = branch["blocker_family_counts"]
            for family in blocker_families:
                branch_families[family] = int(
                    branch_families.get(family) or 0
                ) + 1

        if actionable:
            bucket["actionable_candidate_count"] += 1
            return

        if score >= threshold:
            counts = bucket["blocker_counts"]
            counted_reasons = blockers or ["评分器标记为不可执行"]
            for reason in counted_reasons:
                counts[reason] = int(counts.get(reason) or 0) + 1

        rank = (
            0 if score >= threshold else 1,
            len(blockers) + (1 if score < threshold or not blockers else 0),
            -score,
            context.date,
            symbol,
        )
        nearest = bucket["near_misses"]
        if len(nearest) >= 5 and rank >= nearest[-1]["_rank"]:
            return
        reasons = list(blockers)
        if score < threshold:
            reasons.insert(0, f"评分不足（{score:g} < {threshold:g}）")
        if not reasons:
            reasons.append("评分器标记为不可执行")
        current_bar = context.bars.get(symbol)
        near_miss = {
            "date": context.date,
            "symbol": symbol,
            "name": str(current_bar.name or "") if current_bar is not None else "",
            "score": round(score, 2),
            "entry_threshold": round(threshold, 2),
            "reasons": reasons[:6],
            "_rank": rank,
        }
        nearest.append(near_miss)
        nearest.sort(key=lambda item: item["_rank"])
        del nearest[5:]

    @staticmethod
    def _serialize_diagnostics(
        buckets: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        by_strategy: dict[str, dict[str, Any]] = {}
        for strategy_id, bucket in buckets.items():
            blocker_counts = bucket["blocker_counts"]
            blockers = [
                {"reason": reason, "count": count}
                for reason, count in sorted(
                    blocker_counts.items(),
                    key=lambda item: (-int(item[1]), str(item[0])),
                )[:10]
            ]
            near_misses = [
                {key: value for key, value in item.items() if key != "_rank"}
                for item in bucket["near_misses"]
            ]
            threshold_counts = {
                str(key): int(value)
                for key, value in bucket["entry_threshold_counts"].items()
            }
            applicable_thresholds = sorted(
                float(key) for key in threshold_counts
            )
            single_threshold = (
                applicable_thresholds[0]
                if len(applicable_thresholds) == 1
                else None
            )
            by_strategy[strategy_id] = {
                key: (
                    round(float(value), 2)
                    if key == "maximum_score" and value is not None
                    else value
                )
                for key, value in bucket.items()
                if key not in {
                    "blocker_counts",
                    "blocker_family_counts",
                    "entry_threshold",
                    "entry_threshold_counts",
                    "single_gate_ablation_counts",
                    "family_ablation_counts",
                    "score_sensitivity_counts",
                    "joint_family_sensitivity_counts",
                    "leader_branches",
                    "near_misses",
                }
            }
            by_strategy[strategy_id]["entry_threshold"] = (
                round(single_threshold, 2)
                if single_threshold is not None else None
            )
            by_strategy[strategy_id]["entry_thresholds"] = [
                {
                    "threshold": round(value, 2),
                    "evaluated_count": threshold_counts[f"{value:g}"],
                }
                for value in applicable_thresholds
            ]
            by_strategy[strategy_id]["conditional_entry_threshold"] = (
                len(applicable_thresholds) > 1
            )
            by_strategy[strategy_id]["blockers"] = blockers
            by_strategy[strategy_id]["near_misses"] = near_misses
            by_strategy[strategy_id]["score_sensitivity"] = [
                {
                    "threshold_offset": offset,
                    "threshold": (
                        round(single_threshold + offset, 2)
                        if single_threshold is not None else None
                    ),
                    "applicable_thresholds": [
                        round(value + offset, 2)
                        for value in applicable_thresholds
                    ],
                    "candidate_count": int(
                        bucket["score_sensitivity_counts"].get(
                            f"{offset:+g}", 0
                        )
                    ),
                }
                for offset in DIAGNOSTIC_SCORE_THRESHOLD_OFFSETS
            ]
            family_names = sorted(set(
                bucket["blocker_family_counts"]
            ).union(bucket["family_ablation_counts"]))
            by_strategy[strategy_id]["hard_gate_family_ablation"] = [
                {
                    "family": family,
                    "blocked_candidate_count": int(
                        bucket["blocker_family_counts"].get(family, 0)
                    ),
                    "rescued_at_production_threshold": int(
                        bucket["family_ablation_counts"].get(family, 0)
                    ),
                    "threshold_sensitivity": [
                        {
                            "threshold_offset": offset,
                            "rescued_candidate_count": int(
                                (
                                    bucket["joint_family_sensitivity_counts"].get(
                                        family, {}
                                    )
                                ).get(f"{offset:+g}", 0)
                            ),
                        }
                        for offset in DIAGNOSTIC_SCORE_THRESHOLD_OFFSETS
                    ],
                }
                for family in family_names
            ]
            by_strategy[strategy_id]["single_hard_gate_ablation"] = [
                {
                    "reason": reason,
                    "rescued_at_production_threshold": int(count),
                }
                for reason, count in sorted(
                    bucket["single_gate_ablation_counts"].items(),
                    key=lambda item: (-int(item[1]), str(item[0])),
                )[:20]
            ]
            branches = sorted(
                bucket["leader_branches"].values(),
                key=lambda item: (
                    -int(item["actionable_candidate_count"]),
                    -int(item["threshold_met_count"]),
                    -int(item["evaluated_count"]),
                    str(item["industry"]),
                ),
            )
            by_strategy[strategy_id]["leader_branch_coverage"] = [
                {
                    **dict(item),
                    "maximum_score": (
                        round(float(item["maximum_score"]), 2)
                        if item["maximum_score"] is not None else None
                    ),
                    "lifecycle_stages": dict(sorted(
                        item["lifecycle_stages"].items()
                    )),
                    "blocker_family_counts": dict(sorted(
                        item["blocker_family_counts"].items()
                    )),
                    "monthly_blocker_family_counts": dict(sorted(
                        item["blocker_family_counts"].items()
                    )),
                    "best_blocker_family_counts": dict(sorted(
                        item["best_blocker_family_counts"].items()
                    )),
                }
                for item in branches
            ]
        return {
            "by_strategy": by_strategy,
            "threshold_met_count": sum(
                int(item["threshold_met_count"])
                for item in buckets.values()
            ),
            "actionable_candidate_count": sum(
                int(item["actionable_candidate_count"])
                for item in buckets.values()
            ),
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return bounded monthly threshold and hard-gate diagnostics."""
        overall = self._serialize_diagnostics(self._diagnostics_by_strategy)
        overall["periods"] = {
            period: self._serialize_diagnostics(buckets)
            for period, buckets in sorted(self._diagnostics_by_period.items())
        }
        return overall

    def on_close(self, context: SelectionContext) -> Iterable[SelectionSignal]:
        self._latest_scored_by_symbol_strategy = {}
        if self._replay_phase_callback is not None:
            self._replay_phase_callback("rebuilding_context", context.date)
        shared_context = (
            dict(self.context_provider(context) or {}) if self.context_provider is not None else {}
        )
        if not self._signal_generation_enabled and not self._exit_tracking_symbols:
            return ()
        if self._replay_phase_callback is not None:
            self._replay_phase_callback("scoring", context.date)
        candidates: list[tuple[float, float, int, str, str, dict[str, Any]]] = []
        for symbol, current_bar in context.bars.items():
            if self.eligible_symbols is not None and symbol not in self.eligible_symbols:
                continue
            if (
                not self._signal_generation_enabled
                and symbol not in self._exit_tracking_symbols
            ):
                continue
            rows = _strategy_rows_at_close(
                context,
                symbol,
                history_limit=self._history_limit,
            )
            if len(rows) < 2:
                continue
            rows = _strategy_rows_with_latest_values(rows, {
                "symbol_code": symbol[-6:],
                "stock_name": current_bar.name,
                "industry": current_bar.industry,
                "quote_amount": current_bar.amount,
                "quote_turnover": current_bar.turnover,
            })
            shared_inputs: dict[Callable[..., Any], Any] = {}
            for strategy_id, scorer in self.scorers.items():
                isolated = rows if self._trusted_scorers else [dict(row) for row in rows]
                scored = invoke_strategy_scorer(
                    scorer,
                    isolated,
                    shared_context,
                    shared_inputs=shared_inputs,
                )
                if isinstance(scored, Mapping):
                    self._latest_scored_by_symbol_strategy[(symbol, strategy_id)] = (
                        MappingProxyType(dict(scored))
                    )
                self._record_diagnostic(
                    strategy_id,
                    context,
                    symbol,
                    scored if isinstance(scored, Mapping) else None,
                )
                if not isinstance(scored, dict):
                    continue
                if not self._signal_generation_enabled:
                    continue
                score = float(scored.get("score") or 0.0)
                threshold = float(scored.get("entry_threshold") or 8.0)
                blockers = scored.get("hard_blockers") or []
                if score < threshold or blockers or scored.get("actionable") is False:
                    continue
                candidates.append((
                    float(scored.get("decision_score") or score),
                    score,
                    int(scored.get("strategy_priority") or 0),
                    symbol,
                    strategy_id,
                    scored,
                ))
        candidates.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
        selected_symbols: set[str] = set()
        selected_by_strategy: dict[str, int] = {}
        selections: list[SelectionSignal] = []
        for _decision, score, _priority, symbol, strategy_id, scored in candidates:
            if len(selections) >= self.max_signals_per_session:
                break
            if symbol in selected_symbols:
                continue
            strategy_limit = self.max_signals_per_strategy_per_session.get(strategy_id)
            if (
                strategy_limit is not None
                and selected_by_strategy.get(strategy_id, 0) >= strategy_limit
            ):
                continue
            selected_symbols.add(symbol)
            selected_by_strategy[strategy_id] = selected_by_strategy.get(strategy_id, 0) + 1
            selections.append(SelectionSignal(
                symbol=symbol,
                strategy_id=strategy_id,
                score=score,
                reason=str(scored.get("verdict") or "registered scorer selection"),
                metadata={"scored": dict(scored)},
            ))
        return selections


_NIUONE_PREVIOUS_THEME_FIELDS = (
    "score",
    "raw_state",
    "state",
    "niuone_lifecycle_stage",
    "confirmation_count",
    "intraday_confirmation_count",
    "state_streak",
    "as_of_date",
    "cross_day_persistent",
    "cross_day_confirmed",
    "mainline_confirmed",
    "core_stock_codes",
    "confirmation_component",
)
_NIUONE_PREVIOUS_ATTRIBUTION_FIELDS = (
    "theme",
    "historical_prior_score",
    "attribution_score",
    "observation_count",
    "wave_count",
)


def _compact_niuone_previous_context(
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep only fields read by the next historical NiuOne close."""
    market = context.get("market") if isinstance(context.get("market"), Mapping) else {}
    raw_themes = (
        context.get("themes")
        if isinstance(context.get("themes"), Mapping)
        else {}
    )
    raw_stocks = (
        context.get("stocks")
        if isinstance(context.get("stocks"), Mapping)
        else {}
    )
    themes = {
        str(theme): {
            field_name: values[field_name]
            for field_name in _NIUONE_PREVIOUS_THEME_FIELDS
            if field_name in values
        }
        for theme, values in raw_themes.items()
        if isinstance(values, Mapping)
    }
    stocks: dict[str, dict[str, Any]] = {}
    for code, values in raw_stocks.items():
        if not isinstance(values, Mapping):
            continue
        attributions = [
            {
                field_name: item[field_name]
                for field_name in _NIUONE_PREVIOUS_ATTRIBUTION_FIELDS
                if field_name in item
            }
            for item in (values.get("theme_attributions") or ())
            if isinstance(item, Mapping)
        ]
        if attributions:
            stocks[str(code)] = {"theme_attributions": attributions}
    return {
        "version": context.get("version"),
        "as_of_date": str(context.get("as_of_date") or "")[:10],
        "market": {
            field_name: market[field_name]
            for field_name in ("raw_state", "state", "confirmation_count")
            if field_name in market
        },
        "themes": themes,
        "stocks": stocks,
    }


class NiuOneHistoricalContextProvider:
    """Rebuild NiuOne's cross-sectional context at each historical close."""

    backtest_warmup_sessions = NIUONE_CONTEXT_WARMUP_SESSIONS
    # Before this zero-based session index build_niuone_context cannot emit a
    # member, so skipping those closes is exactly equivalent to replaying them.
    backtest_earliest_state_session = NIUONE_MIN_ROWS - 1

    def __init__(self, *, flow_provider: HistoricalFlowProvider | None = None) -> None:
        self.flow_provider = flow_provider
        self._previous_context: dict[str, Any] = {}
        self._previous_trading_day = ""
        self._latest_theme_cross_section: Mapping[str, Mapping[str, Any]] = (
            MappingProxyType({})
        )

    def reset(self) -> None:
        self._previous_context = {}
        self._previous_trading_day = ""
        self._latest_theme_cross_section = MappingProxyType({})

    def latest_cross_section(self) -> Mapping[str, Mapping[str, Any]]:
        """Return the full theme context built for this close."""
        return self._latest_theme_cross_section

    @staticmethod
    def _market_snapshot(context: SelectionContext) -> dict[str, Any]:
        changes: list[float] = []
        up = 0
        down = 0
        limit_up_count = 0
        limit_down_count = 0
        for symbol, bar in context.bars.items():
            history = context.history(symbol)
            previous_close = history[-2].close if len(history) >= 2 else bar.previous_close
            if previous_close is None or previous_close <= 0:
                continue
            change = (bar.close / previous_close - 1.0) * 100.0
            changes.append(change)
            if change > 0:
                up += 1
            elif change < 0:
                down += 1
            limit_up, limit_down = a_share_price_limits(bar, previous_close)
            if limit_up is not None and bar.close >= limit_up - 1e-9:
                limit_up_count += 1
            if limit_down is not None and bar.close <= limit_down + 1e-9:
                limit_down_count += 1
        return {
            "up": up,
            "down": down,
            "median_change_pct": statistics.median(changes) if changes else 0.0,
            "limit_up": limit_up_count,
            "limit_down": limit_down_count,
            "core_index_count": 0,
            "index_below_ma20_count": 0,
            "captured_at": f"{context.date} 15:00:00",
        }

    def __call__(self, context: SelectionContext) -> Mapping[str, Any]:
        if context.session_index == 0:
            self.reset()
        prepared_items: list[dict[str, Any]] = []
        for symbol, current_bar in context.bars.items():
            rows = _strategy_rows_at_close(
                context,
                symbol,
                history_limit=BUILTIN_STRATEGY_HISTORY_LIMIT,
            )
            if not rows:
                continue
            previous_close = rows[-2].get("close") if len(rows) >= 2 else None
            change_pct = (
                (current_bar.close / float(previous_close) - 1.0) * 100.0
                if previous_close is not None and float(previous_close) > 0 else 0.0
            )
            rows = _strategy_rows_with_latest_values(rows, {
                "symbol_code": symbol[-6:],
                "stock_name": current_bar.name,
                "industry": current_bar.industry,
                "quote_amount": current_bar.amount,
                "quote_turnover": current_bar.turnover,
                "change_pct": change_pct,
            })
            latest = rows[-1]
            prepared_items.append({
                "code": symbol[-6:],
                "name": current_bar.name,
                "industry": current_bar.industry,
                "themes": list(latest.get("themes") or ()),
                "rows": rows,
                "quote": {
                    "price": current_bar.close,
                    "prev_close": previous_close,
                    "low": current_bar.low,
                    "change_pct": change_pct,
                    "amount": current_bar.amount or 0.0,
                },
            })
        flow_rows = self.flow_provider(context) if self.flow_provider is not None else None
        built = build_niuone_context(
            prepared_items,
            reference_pool_count=len(context.histories),
            market_snapshot=self._market_snapshot(context),
            flow_rows=flow_rows,
            previous_context=self._previous_context,
            as_of_date=context.date,
            previous_trading_day=self._previous_trading_day,
            sample_at=f"{context.date} 15:00:00",
            theme_basis="eastmoney_concept",
        )
        themes = built.get("themes") or {}
        self._latest_theme_cross_section = MappingProxyType({
            str(theme): MappingProxyType(dict(values))
            for theme, values in themes.items()
            if isinstance(values, Mapping)
        })
        self._previous_context = _compact_niuone_previous_context(built)
        self._previous_trading_day = context.date
        return built


class SectorTideHistoricalContextProvider:
    """Rebuild Sector Tide's cross-sectional context for each close."""

    def __init__(self, *, flow_provider: HistoricalFlowProvider | None = None) -> None:
        self.flow_provider = flow_provider
        self._previous_market: dict[str, Any] = {}

    def reset(self) -> None:
        self._previous_market = {}

    def __call__(self, context: SelectionContext) -> Mapping[str, Any]:
        if context.session_index == 0:
            self.reset()
        prepared_items: list[dict[str, Any]] = []
        changes: list[float] = []
        up = 0
        down = 0
        limit_up_count = 0
        limit_down_count = 0
        for symbol, current_bar in context.bars.items():
            rows = _strategy_rows_at_close(
                context,
                symbol,
                history_limit=BUILTIN_STRATEGY_HISTORY_LIMIT,
            )
            if not rows:
                continue
            previous_close = rows[-2].get("close") if len(rows) >= 2 else None
            change_pct = (
                (current_bar.close / float(previous_close) - 1.0) * 100.0
                if previous_close is not None and float(previous_close) > 0 else 0.0
            )
            changes.append(change_pct)
            if change_pct > 0:
                up += 1
            elif change_pct < 0:
                down += 1
            limit_up, limit_down = a_share_price_limits(current_bar, previous_close)
            if limit_up is not None and current_bar.close >= limit_up - 1e-9:
                limit_up_count += 1
            if limit_down is not None and current_bar.close <= limit_down + 1e-9:
                limit_down_count += 1
            rows = _strategy_rows_with_latest_values(rows, {
                "symbol_code": symbol[-6:],
                "stock_name": current_bar.name,
                "industry": current_bar.industry,
                "quote_amount": current_bar.amount,
                "quote_turnover": current_bar.turnover,
                "change_pct": change_pct,
            })
            prepared_items.append({
                "code": symbol[-6:],
                "name": current_bar.name,
                "industry": current_bar.industry,
                "rows": rows,
                "quote": {
                    "price": current_bar.close,
                    "prev_close": previous_close,
                    "low": current_bar.low,
                    "change_pct": change_pct,
                    "amount": current_bar.amount or 0.0,
                },
            })
        market_snapshot = {
            "up": up,
            "down": down,
            "median_change_pct": statistics.median(changes) if changes else 0.0,
            "limit_up": limit_up_count,
            "limit_down": limit_down_count,
            "core_index_count": 0,
            "index_below_ma20_count": 0,
            "captured_at": f"{context.date} 15:00:00",
        }
        flow_rows = self.flow_provider(context) if self.flow_provider is not None else None
        built = build_sector_tide_context(
            prepared_items,
            market_snapshot=market_snapshot,
            flow_rows=flow_rows,
            previous_market=self._previous_market,
        )
        self._previous_market = dict(built.get("market") or {})
        return built


__all__ = [
    "HistoricalBar",
    "HistoricalFlowProvider",
    "NiuOneHistoricalContextProvider",
    "PortfolioEntryDecision",
    "PositionExitSignal",
    "PositionExitStrategy",
    "PriceLimitResolver",
    "RegisteredScorerSelector",
    "ReplaySelectionStrategy",
    "SectorTideHistoricalContextProvider",
    "ScorerContextProvider",
    "SelectionBacktestConfig",
    "SelectionBacktestError",
    "SelectionBacktestResult",
    "SelectionContext",
    "SelectionCostModel",
    "SelectionFunction",
    "SelectionPreparation",
    "SelectionPhaseProgress",
    "SelectionProgress",
    "SelectionReplayFrame",
    "SelectionReplayProgress",
    "SelectionReplayTape",
    "SelectionSignal",
    "SelectionSignalFilter",
    "SelectionStrategy",
    "a_share_price_limits",
    "build_selection_replay_tape",
    "run_selection_backtest",
]
