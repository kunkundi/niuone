"""Bounded, isolated jobs for the admin stock-selection backtest page."""
from __future__ import annotations

import copy
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

try:
    from app.core.json_cache import read_json_cache, write_json_cache
    from app.core.paths import get_dashboard_home
    from app.strategies.registry import STRATEGY_DEFINITIONS, STRATEGY_SUITES
    from app.strategies.selection import strategy_daily_candidate_limit
    from app.strategies.exits import (
        NIUONE_LIFECYCLE_CLIMAX_MIN_PNL_PCT,
        NIUONE_LIFECYCLE_CLIMAX_PARTIAL_RATIO,
    )
except ImportError:  # pragma: no cover - legacy top-level import path
    from core.json_cache import read_json_cache, write_json_cache
    from core.paths import get_dashboard_home
    from strategies.registry import STRATEGY_DEFINITIONS, STRATEGY_SUITES
    from strategies.selection import strategy_daily_candidate_limit
    from strategies.exits import (
        NIUONE_LIFECYCLE_CLIMAX_MIN_PNL_PCT,
        NIUONE_LIFECYCLE_CLIMAX_PARTIAL_RATIO,
    )

from .historical_data import (
    DEFAULT_HISTORICAL_SOURCE_PRIORITY,
    HistoricalFetchConfig,
    SUPPORTED_HISTORICAL_SOURCES,
)
from .niuone_exits import NiuOneStrategyBacktestPolicy
from .replay_cache import ReplayTapeCache
from .selection import (
    NiuOneHistoricalContextProvider,
    RegisteredScorerSelector,
    SectorTideHistoricalContextProvider,
    SelectionBacktestConfig,
)
from .service import (
    load_current_classification_snapshot,
    run_historical_selection_backtest,
)


MAX_BACKTEST_RANGE_DAYS = 366
MAX_ACTIVE_JOBS = 2
MIN_HISTORICAL_COVERAGE_RATIO = 0.85
DEFAULT_HORIZONS = (1, 3, 5, 10, 20)
UNSUPPORTED_SUITE_ID = "preset_text"
BACKTEST_STATE_SCHEMA_VERSION = 2
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BACKTEST_RISK_PROFILE = "aggressive"
GENERIC_BACKTEST_RISK_PROFILE = "balanced"
NIUONE_BACKTEST_PROTOCOL_VERSION = "niuone-backtest-v32"
GENERIC_BACKTEST_PROTOCOL_VERSION = "selection-backtest-v2"
NIUONE_BACKTEST_RISK_PROFILES: dict[str, dict[str, Any]] = {
    "aggressive": {
        "id": "aggressive",
        "label": "进取",
        "description": (
            "默认档位；允许更高账户风险、总仓和同题材持仓以争取收益；"
            "仍保留结构止损、涨停、T+1 和单票绝对上限。"
        ),
        "policy_options": {
            "risk_budget_scale": 1.35,
            "position_budget_scale": 1.15,
            "max_new_positions_per_session": 3,
            "max_open_positions": 6,
            "max_industry_positions": 3,
        },
    },
}


class BacktestTaskError(ValueError):
    """Raised when an admin backtest request is invalid or cannot be queued."""


class _BacktestCancelled(RuntimeError):
    """Stop a worker cooperatively without converting cancellation to failure."""


def _missing_requested_module(exc: ImportError, module_name: str) -> bool:
    """Distinguish a missing compatibility path from an internal import bug."""
    if not isinstance(exc, ModuleNotFoundError):
        return False
    parts = module_name.split(".")
    requested_names = {
        ".".join(parts[:index]) for index in range(1, len(parts) + 1)
    }
    return str(exc.name or "") in requested_names


def default_backtest_state_dir() -> Path:
    """Return the private runtime directory for durable backtest task state."""
    return get_dashboard_home(PROJECT_ROOT) / "backtesting"


def _suite_payload(suite_id: str, definition: dict[str, Any]) -> dict[str, Any]:
    strategy_ids = tuple(str(item) for item in definition.get("strategy_ids") or ())
    return {
        "id": suite_id,
        "label": str(definition.get("label") or suite_id),
        "desc": str(definition.get("desc") or ""),
        "color": str(definition.get("color") or "#94a3b8"),
        "strategy_ids": list(strategy_ids),
        "strategy_labels": [
            str(STRATEGY_DEFINITIONS.get(item, {}).get("label") or item)
            for item in strategy_ids
        ],
        "excluded_strategy_ids": [],
        "supported": bool(strategy_ids),
        "unsupported_reason": "",
        "backtest_protocol_version": (
            NIUONE_BACKTEST_PROTOCOL_VERSION
            if suite_id == "niuone"
            else GENERIC_BACKTEST_PROTOCOL_VERSION
        ),
    }


def backtest_strategy_options(*, today: date | None = None) -> dict[str, Any]:
    resolved_today = today or date.today()
    default_end = resolved_today - timedelta(days=35)
    options = [
        _suite_payload(str(suite_id), dict(definition))
        for suite_id, definition in STRATEGY_SUITES.items()
    ]
    options.append({
        "id": UNSUPPORTED_SUITE_ID,
        "label": "预设文字策略",
        "desc": "运行时由模型解释用户文字规则",
        "color": "#2dd4bf",
        "strategy_ids": [],
        "strategy_labels": [],
        "excluded_strategy_ids": [],
        "supported": False,
        "unsupported_reason": "预设文字策略依赖运行时模型解释，暂不提供确定性历史回测。",
    })
    return {
        "strategies": options,
        "defaults": {
            "start_date": (default_end - timedelta(days=60)).isoformat(),
            "end_date": default_end.isoformat(),
            "holding_sessions": list(DEFAULT_HORIZONS),
            "adjustment": "qfq",
            "sources": list(DEFAULT_HISTORICAL_SOURCE_PRIORITY),
            "universe_mode": "strategy_auto",
            "risk_profile": DEFAULT_BACKTEST_RISK_PROFILE,
        },
        "limits": {
            "max_range_days": MAX_BACKTEST_RANGE_DAYS,
        },
    }


def _parse_date(value: Any, field_name: str) -> date:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        raise BacktestTaskError(f"{field_name}必须使用 YYYY-MM-DD") from None


def normalize_backtest_request(values: dict[str, Any]) -> dict[str, Any]:
    suite_id = str(values.get("strategy_id") or "").strip()
    definition = STRATEGY_SUITES.get(suite_id)
    if definition is None:
        if suite_id == UNSUPPORTED_SUITE_ID:
            raise BacktestTaskError("预设文字策略暂不支持确定性历史回测")
        raise BacktestTaskError("未知策略")
    strategy = _suite_payload(suite_id, dict(definition))
    if not strategy["supported"]:
        raise BacktestTaskError("该策略暂不支持历史回测")
    start = _parse_date(values.get("start_date"), "开始日期")
    end = _parse_date(values.get("end_date"), "结束日期")
    if start > end:
        raise BacktestTaskError("开始日期不能晚于结束日期")
    if (end - start).days + 1 > MAX_BACKTEST_RANGE_DAYS:
        raise BacktestTaskError(f"回测区间不能超过 {MAX_BACKTEST_RANGE_DAYS} 天")
    if end > date.today():
        raise BacktestTaskError("结束日期不能晚于今天")
    adjustment = str(values.get("adjustment") or "qfq").strip().lower()
    if adjustment not in {"qfq", "hfq", "none"}:
        raise BacktestTaskError("复权方式必须是 qfq、hfq 或 none")
    source = str(values.get("source") or "auto").strip().lower()
    if source == "auto":
        sources = DEFAULT_HISTORICAL_SOURCE_PRIORITY
    elif source in SUPPORTED_HISTORICAL_SOURCES:
        sources = (source,)
    else:
        raise BacktestTaskError("未知历史行情来源")
    if adjustment != "none":
        sources = tuple(item for item in sources if item != "sina")
    if not sources:
        raise BacktestTaskError("新浪历史接口仅支持不复权回测")
    if suite_id == "niuone":
        # NiuOne backtests intentionally have one fixed risk policy. Ignore
        # stale clients that still submit the removed balanced selector.
        risk_profile = DEFAULT_BACKTEST_RISK_PROFILE
    else:
        risk_profile = GENERIC_BACKTEST_RISK_PROFILE
    return {
        "strategy": strategy,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "adjustment": adjustment,
        "sources": tuple(sources),
        "risk_profile": risk_profile,
        "protocol_version": (
            NIUONE_BACKTEST_PROTOCOL_VERSION
            if suite_id == "niuone"
            else GENERIC_BACKTEST_PROTOCOL_VERSION
        ),
    }


def _market_symbol(code: Any) -> str:
    digits = re.sub(r"\D", "", str(code or ""))[-6:]
    if not re.fullmatch(r"\d{6}", digits):
        return ""
    return ("sh" if digits.startswith(("6", "9")) else "sz") + digits


def load_strategy_universe(
    strategy: Mapping[str, Any],
    *,
    pool_loader: Callable[[object | None], Iterable[tuple[str, str]]] | None = None,
    configured_loader: Callable[[], tuple[str, ...]] | None = None,
    friendly_loader: Callable[[object | None], str] | None = None,
) -> dict[str, Any]:
    """Build the strategy's candidate and market-reference universes."""
    try:
        from app.screening.stock_universe import (
            FULL_SUPPORTED_NON_ST_UNIVERSE,
            STOCK_UNIVERSE_ENV,
            friendly_stock_universe,
            selected_stock_universe,
            stock_in_universe,
        )
    except ImportError as exc:  # pragma: no cover - legacy top-level import path
        if not _missing_requested_module(exc, "app.screening.stock_universe"):
            raise
        from screening.stock_universe import (
            FULL_SUPPORTED_NON_ST_UNIVERSE,
            STOCK_UNIVERSE_ENV,
            friendly_stock_universe,
            selected_stock_universe,
            stock_in_universe,
        )

    load_configured = configured_loader or (
        lambda: selected_stock_universe(os.environ.get(STOCK_UNIVERSE_ENV))
    )
    friendly = friendly_loader or friendly_stock_universe
    configured_scope = tuple(load_configured())
    is_niuone = str(strategy.get("id") or "") == "niuone"
    reference_scope = FULL_SUPPORTED_NON_ST_UNIVERSE if is_niuone else configured_scope

    names: dict[str, str] = {}

    def current_pool(scope: object | None) -> tuple[tuple[str, str], ...]:
        if pool_loader is not None:
            return tuple(pool_loader(scope))
        try:
            from app.screening.multi_strategy import load_a_share_code_pool
        except ImportError as exc:
            if not _missing_requested_module(
                exc,
                "app.screening.multi_strategy",
            ):
                raise
            try:  # pragma: no cover - active production compatibility path
                from screening.multi_strategy import load_a_share_code_pool
            except ImportError as exc:
                if not _missing_requested_module(
                    exc,
                    "screening.multi_strategy",
                ):
                    raise
                raise BacktestTaskError(
                    "A 股列表接口不可用，暂时无法构建回测候选范围"
                ) from exc
        return tuple(load_a_share_code_pool(scope))

    reference_pairs = current_pool(reference_scope)
    reference_symbols = tuple(dict.fromkeys(
        symbol
        for code, _name in reference_pairs
        if (symbol := _market_symbol(code))
    ))
    names.update({
        symbol: str(name or "").strip()
        for code, name in reference_pairs
        if (symbol := _market_symbol(code))
    })
    eligible_symbols = tuple(
        symbol for symbol in reference_symbols
        if stock_in_universe(symbol[-6:], names.get(symbol, ""), configured_scope)
    )
    if "st" in configured_scope and is_niuone:
        for code, name in current_pool(configured_scope):
            symbol = _market_symbol(code)
            if not symbol:
                continue
            names[symbol] = str(name or "").strip()
            if symbol not in eligible_symbols:
                eligible_symbols += (symbol,)

    reference_symbols = tuple(sorted(dict.fromkeys(reference_symbols)))
    eligible_symbols = tuple(sorted(dict.fromkeys(eligible_symbols)))
    if not reference_symbols:
        raise BacktestTaskError("未能从现有行情接口构建历史候选范围")
    if not eligible_symbols:
        raise BacktestTaskError("当前设置的选股范围内没有可回测股票")
    return {
        "reference_symbols": reference_symbols,
        "eligible_symbols": eligible_symbols,
        "name_by_symbol": names,
        "metadata": {
            "mode": "strategy_auto",
            "source": "current_a_share_listing_interfaces",
            "configured_scope": list(configured_scope),
            "configured_scope_label": friendly(configured_scope),
            "reference_scope": list(reference_scope),
            "reference_scope_label": friendly(reference_scope),
            "reference_symbol_count": len(reference_symbols),
            "eligible_symbol_count": len(eligible_symbols),
        },
}


def _selector_for_request(
    request: dict[str, Any],
    *,
    eligible_symbols: Iterable[str],
) -> RegisteredScorerSelector:
    suite_id = str(request["strategy"]["id"])
    resolved_eligible_symbols = tuple(dict.fromkeys(eligible_symbols))
    context_provider = None
    if suite_id == "niuone":
        context_provider = NiuOneHistoricalContextProvider()
    elif suite_id == "sector_tide":
        context_provider = SectorTideHistoricalContextProvider()
    strategy_limits = {
        strategy_id: limit
        for strategy_id in request["strategy"]["strategy_ids"]
        if (limit := strategy_daily_candidate_limit(strategy_id)) is not None
    }
    return RegisteredScorerSelector(
        request["strategy"]["strategy_ids"],
        # Preserve every mature NiuOne path while bounding the lower-certainty
        # reversal probe so it cannot flood results or consume all daily slots.
        max_signals_per_session=(
            max(1, len(resolved_eligible_symbols)) if suite_id == "niuone" else 5
        ),
        max_signals_per_strategy_per_session=(
            strategy_limits or None
        ),
        context_provider=context_provider,
        eligible_symbols=resolved_eligible_symbols,
    )


def run_strategy_backtest_request(
    request: dict[str, Any],
    *,
    progress_callback=None,
    universe_loader=load_strategy_universe,
    replay_cache_dir: Path | None = None,
) -> dict[str, Any]:
    suite_id = str(request["strategy"]["id"])
    needs_industry = suite_id in {"niuone", "sector_tide"}
    protocol_version = str(
        request.get("protocol_version")
        or (
            NIUONE_BACKTEST_PROTOCOL_VERSION
            if suite_id == "niuone"
            else GENERIC_BACKTEST_PROTOCOL_VERSION
        )
    )
    minimum_rows = 55 if needs_industry else 30
    if progress_callback is not None:
        progress_callback(2, "universe", "正在从 A 股列表接口构建策略候选范围")
    universe = universe_loader(request["strategy"])
    reference_symbols = tuple(universe["reference_symbols"])
    eligible_symbols = tuple(universe["eligible_symbols"])

    risk_profile_id = (
        DEFAULT_BACKTEST_RISK_PROFILE
        if suite_id == "niuone"
        else GENERIC_BACKTEST_RISK_PROFILE
    )
    risk_profile = (
        NIUONE_BACKTEST_RISK_PROFILES[DEFAULT_BACKTEST_RISK_PROFILE]
        if suite_id == "niuone"
        else {"label": "标准", "policy_options": {}}
    )

    position_exit_strategy = (
        NiuOneStrategyBacktestPolicy(
            markup_upgrade_only=True,
            markup_rebalance_enabled=True,
            lifecycle_climax_partial_ratio=(
                NIUONE_LIFECYCLE_CLIMAX_PARTIAL_RATIO
            ),
            lifecycle_climax_min_pnl_pct=(
                NIUONE_LIFECYCLE_CLIMAX_MIN_PNL_PCT
            ),
            **dict(risk_profile.get("policy_options") or {}),
        )
        if suite_id == "niuone" else None
    )
    if progress_callback is not None:
        progress_callback(
            4,
            "universe",
            f"候选范围已就绪：参考 {len(reference_symbols)} 只，可选 {len(eligible_symbols)} 只",
        )
    run = run_historical_selection_backtest(
        reference_symbols,
        request["start_date"],
        request["end_date"],
        _selector_for_request(request, eligible_symbols=eligible_symbols),
        fetch_config=HistoricalFetchConfig(
            sources=tuple(request["sources"]),
            adjustment=str(request["adjustment"]),
            strict=False,
            minimum_rows=minimum_rows,
            max_workers=16,
        ),
        selection_config=SelectionBacktestConfig(
            holding_sessions=DEFAULT_HORIZONS,
            cooldown_sessions=(0 if suite_id == "niuone" else 20),
            slippage_bps=5,
        ),
        position_exit_strategy=position_exit_strategy,
        minimum_coverage_ratio=MIN_HISTORICAL_COVERAGE_RATIO,
        classification_loader=(
            load_current_classification_snapshot if needs_industry else None
        ),
        name_by_symbol=universe.get("name_by_symbol"),
        progress_callback=progress_callback,
        replay_cache=(
            ReplayTapeCache(replay_cache_dir)
            if replay_cache_dir is not None else None
        ),
        replay_cache_identity=(
            {
                "protocol_version": protocol_version,
                "selector_id": suite_id,
                "strategy_ids": tuple(request["strategy"]["strategy_ids"]),
                "sources": tuple(request["sources"]),
                "adjustment": str(request["adjustment"]),
                "stock_pool": eligible_symbols,
            }
            if replay_cache_dir is not None else None
        ),
    )
    payload = run.to_dict()
    name_by_symbol = universe.get("name_by_symbol") or {}
    source_counts: dict[str, int] = {}
    for series in run.data.series.values():
        source_counts[series.source] = source_counts.get(series.source, 0) + 1
    payload["data"] = {
        "series": {
            symbol: {
                "symbol": series.symbol,
                "name": str(
                    name_by_symbol.get(symbol)
                    or name_by_symbol.get(symbol[-6:])
                    or ""
                ),
                "source": series.source,
                "adjustment": series.adjustment,
                "bar_count": len(series.bars),
                "first_date": str(series.bars[0].get("date") or "") if series.bars else "",
                "last_date": str(series.bars[-1].get("date") or "") if series.bars else "",
                "attempts": [dict(item) for item in series.attempts],
            }
            for symbol, series in run.data.series.items()
        },
        "failures": dict(run.data.failures),
        "source_counts": source_counts,
    }
    payload["strategy"] = copy.deepcopy(request["strategy"])
    payload["protocol"] = {
        "version": protocol_version,
        "risk_profile": risk_profile_id,
        "risk_profile_label": str(risk_profile["label"]),
    }
    payload["universe"] = copy.deepcopy(universe["metadata"])
    if needs_industry:
        quality = payload.get("industry_quality")
        classification_source = str(
            quality.get("source") if isinstance(quality, Mapping) else ""
        )
        classification_provider = (
            "iwencai"
            if classification_source == "iwencai_current_industry_concept"
            else "eastmoney"
        )
        payload["universe"]["classification_provider"] = classification_provider
        payload["universe"]["classification_basis"] = (
            f"{classification_provider}_concept"
            if suite_id == "niuone"
            else f"{classification_provider}_industry"
        )
    payload["warnings"].append(
        "automatic universe uses today's listed-stock membership; delisted and historically "
        "not-yet-listed membership changes may cause survivorship bias"
    )
    if suite_id == "niuone":
        payload["execution_assumptions"] = {
            "entry_sizing": "maximum_permitted_risk_ceiling",
            "entry_order_scale": position_exit_strategy.entry_order_scale,
            "risk_profile": risk_profile_id,
            "risk_budget_scale": position_exit_strategy.risk_budget_scale,
            "position_budget_scale": (
                position_exit_strategy.position_budget_scale
            ),
            "max_new_positions_per_session": (
                position_exit_strategy.max_new_positions_per_session
            ),
            "max_open_positions": position_exit_strategy.max_open_positions,
            "max_industry_positions": (
                position_exit_strategy.max_industry_positions
            ),
            "board_lot": position_exit_strategy.board_lot,
            "model_order_units_replayed": False,
        }
        payload["warnings"].append(
            "NiuOne structural stops use the completed daily low as the trigger and the "
            "stop/open as the fill reference; other exits use the close, while exact "
            "intraday timing and queue priority cannot be reconstructed from daily data"
        )
        payload["warnings"].append(
            "NiuOne entries use 100% of the deterministic maximum risk-permitted "
            "board-lot order; Practice uses model-specified units and rejects an "
            "oversized order instead of auto-sizing it, so portfolio return and "
            "drawdown represent a maximum-sizing scenario"
        )
        if risk_profile_id == "aggressive":
            payload["warnings"].append(
                "NiuOne aggressive backtest profile increases account-risk, "
                "portfolio/theme exposure, and position-count budgets; it does "
                "not weaken price-pattern, structural-stop, limit-up, or T+1 rules"
            )
    payload["request"] = {
        "start_date": request["start_date"],
        "end_date": request["end_date"],
        "adjustment": request["adjustment"],
        "sources": list(request["sources"]),
        "risk_profile": risk_profile_id,
        "protocol_version": protocol_version,
    }
    return payload


def _safe_error(exc: Exception) -> str:
    text = re.sub(r"https?://\S+", "<url>", str(exc or "")).strip()
    return f"{type(exc).__name__}: {text[:400]}" if text else type(exc).__name__


def _worker_error_message(payload: Mapping[str, Any], returncode: int) -> str:
    error_text = str(payload.get("error") or "").strip()
    error_type = str(payload.get("error_type") or "").strip()
    if not error_text:
        return f"回测子进程异常退出（{returncode}）"
    duplicated_prefix = f"{error_type}: " if error_type else ""
    if error_type == "BacktestTaskError" and error_text.startswith(
        duplicated_prefix
    ):
        return error_text[len(duplicated_prefix):]
    return error_text


class BacktestTaskManager:
    """Thread-safe registry with one isolated production backtest at a time."""

    def __init__(
        self,
        *,
        runner=run_strategy_backtest_request,
        state_dir: Path | None = None,
    ) -> None:
        self._runner = runner
        self._uses_subprocess = runner is run_strategy_backtest_request
        self._state_dir = Path(state_dir).expanduser() if state_dir is not None else None
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._processes: dict[str, subprocess.Popen[Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="niuone-backtest")
        resumed = self._load_persisted_jobs()
        for job_id, request in resumed:
            self._cancel_events[job_id] = threading.Event()
            self._executor.submit(self._execute, job_id, request)

    def options(self) -> dict[str, Any]:
        return backtest_strategy_options()

    def start(self, values: dict[str, Any]) -> dict[str, Any]:
        request = normalize_backtest_request(values)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        job_id = uuid.uuid4().hex
        with self._lock:
            strategy_id = str(request["strategy"]["id"])
            previous = self._latest_locked(strategy_id)
            if previous is not None and previous.get("status") in {"queued", "running"}:
                raise BacktestTaskError("该策略已有回测任务正在执行")
            active = sum(
                job.get("status") in {"queued", "running"}
                for job in self._jobs.values()
            )
            if active >= MAX_ACTIVE_JOBS:
                raise BacktestTaskError("已有回测任务排队，请等待完成后再试")
            job = {
                "id": job_id,
                "status": "queued",
                "phase": "queued",
                "progress": 0,
                "message": "任务已进入队列",
                "trading_date": "",
                "day_elapsed_seconds": None,
                "eta_seconds": None,
                "created_at": now,
                "updated_at": now,
                "started_at": "",
                "finished_at": "",
                "strategy": copy.deepcopy(request["strategy"]),
                "request": {
                    **{key: value for key, value in request.items() if key != "strategy"},
                    "sources": list(request["sources"]),
                },
                "result": None,
                "error": "",
            }
            if previous is not None:
                previous_id = str(previous.get("id") or "")
                self._jobs.pop(previous_id, None)
                self._cancel_events.pop(previous_id, None)
            self._jobs[job_id] = job
            self._cancel_events[job_id] = threading.Event()
            try:
                self._persist_job_locked(job)
            except (OSError, TypeError, ValueError) as exc:
                self._jobs.pop(job_id, None)
                self._cancel_events.pop(job_id, None)
                if previous is not None:
                    self._jobs[str(previous["id"])] = previous
                raise BacktestTaskError("无法保存回测任务，请检查运行数据目录") from exc
        self._executor.submit(self._execute, job_id, request)
        return self.get(job_id) or {}

    def _state_path(self, strategy_id: str) -> Path | None:
        if self._state_dir is None:
            return None
        if not re.fullmatch(r"[a-z0-9_]+", strategy_id):
            raise ValueError("invalid strategy id")
        return self._state_dir / f"{strategy_id}.json"

    def _persist_job_locked(self, job: dict[str, Any]) -> None:
        strategy = job.get("strategy") or {}
        path = self._state_path(str(strategy.get("id") or ""))
        if path is None:
            return
        write_json_cache(path, {
            "schema_version": BACKTEST_STATE_SCHEMA_VERSION,
            "job": copy.deepcopy(job),
        })

    def _latest_locked(self, strategy_id: str) -> dict[str, Any] | None:
        for job in reversed(tuple(self._jobs.values())):
            strategy = job.get("strategy") or {}
            if str(strategy.get("id") or "") == strategy_id:
                return job
        return None

    @staticmethod
    def _request_from_job(job: Mapping[str, Any]) -> dict[str, Any] | None:
        strategy = job.get("strategy")
        stored = job.get("request")
        if not isinstance(strategy, Mapping) or not isinstance(stored, Mapping):
            return None
        sources = tuple(
            str(item) for item in stored.get("sources") or ()
            if str(item) in SUPPORTED_HISTORICAL_SOURCES
        )
        if not sources:
            return None
        strategy_id = str(strategy.get("id") or "")
        expected_protocol = (
            NIUONE_BACKTEST_PROTOCOL_VERSION
            if strategy_id == "niuone"
            else GENERIC_BACKTEST_PROTOCOL_VERSION
        )
        if str(stored.get("protocol_version") or "") != expected_protocol:
            return None
        risk_profile = str(
            stored.get("risk_profile") or DEFAULT_BACKTEST_RISK_PROFILE
        )
        if (
            strategy_id == "niuone"
            and risk_profile not in NIUONE_BACKTEST_RISK_PROFILES
        ):
            return None
        return {
            "strategy": copy.deepcopy(dict(strategy)),
            "start_date": str(stored.get("start_date") or ""),
            "end_date": str(stored.get("end_date") or ""),
            "adjustment": str(stored.get("adjustment") or "qfq"),
            "sources": sources,
            "risk_profile": risk_profile,
            "protocol_version": expected_protocol,
        }

    def _load_persisted_jobs(self) -> list[tuple[str, dict[str, Any]]]:
        if self._state_dir is None:
            return []
        resumed: list[tuple[str, dict[str, Any]]] = []
        with self._lock:
            for strategy_id in STRATEGY_SUITES:
                path = self._state_path(str(strategy_id))
                payload = read_json_cache(path) if path is not None else None
                job = payload.get("job") if isinstance(payload, dict) else None
                if (
                    payload is None
                    or payload.get("schema_version") != BACKTEST_STATE_SCHEMA_VERSION
                    or not isinstance(job, dict)
                    or not str(job.get("id") or "")
                    or str((job.get("strategy") or {}).get("id") or "") != strategy_id
                    or job.get("status") not in {
                        "queued", "running", "succeeded", "failed", "cancelled",
                    }
                ):
                    continue
                request = self._request_from_job(job)
                if request is None:
                    continue
                job_id = str(job["id"])
                if job.get("status") in {"queued", "running"}:
                    now = datetime.now().astimezone().isoformat(timespec="seconds")
                    job.update({
                        "status": "queued",
                        "phase": "queued",
                        "progress": 0,
                        "message": "服务重启后重新排队执行",
                        "trading_date": "",
                        "day_elapsed_seconds": None,
                        "eta_seconds": None,
                        "updated_at": now,
                        "started_at": "",
                        "finished_at": "",
                        "result": None,
                        "error": "",
                    })
                    try:
                        self._persist_job_locked(job)
                    except (OSError, TypeError, ValueError):
                        job.update({
                            "status": "failed",
                            "phase": "failed",
                            "message": "中断任务重新排队失败",
                            "error": "BacktestPersistenceError: 无法保存服务端任务状态",
                            "finished_at": now,
                        })
                    else:
                        resumed.append((job_id, request))
                self._jobs[job_id] = job
        return resumed

    def _worker_directory(self, job_id: str) -> Path:
        if self._state_dir is None or not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("invalid backtest worker directory")
        return self._state_dir / "workers" / job_id

    @staticmethod
    def _terminate_process(process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        except OSError:
            pass

    def _run_default_subprocess(
        self,
        job_id: str,
        request: dict[str, Any],
        *,
        progress: Callable[[int, str, str], None],
        check_cancelled: Callable[[], None],
        cancel_event: threading.Event | None,
    ) -> dict[str, Any]:
        worker_dir = self._worker_directory(job_id)
        request_path = worker_dir / "request.json"
        progress_path = worker_dir / "progress.json"
        result_path = worker_dir / "result.json"
        error_path = worker_dir / "error.json"
        write_json_cache(request_path, {"request": copy.deepcopy(request)})
        command = [
            sys.executable,
            "-m",
            "app.entrypoints.backtest_worker",
            "--request",
            str(request_path),
            "--progress",
            str(progress_path),
            "--result",
            str(result_path),
            "--error",
            str(error_path),
            "--cache-dir",
            str(self._state_dir / "replay-cache"),
        ]
        creationflags = (
            int(getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
            if os.name == "nt" else 0
        )
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        with self._lock:
            self._processes[job_id] = process
        last_sequence = -1

        def forward_progress() -> None:
            nonlocal last_sequence
            payload = read_json_cache(progress_path)
            if not isinstance(payload, dict):
                return
            sequence = int(payload.get("sequence") or 0)
            if sequence <= last_sequence:
                return
            last_sequence = sequence
            reporter = getattr(progress, "report", None)
            details = payload.get("details")
            if callable(reporter):
                reporter(
                    int(payload.get("progress") or 0),
                    str(payload.get("phase") or "running"),
                    str(payload.get("message") or "正在回测"),
                    details if isinstance(details, Mapping) else {},
                )
            else:
                progress(
                    int(payload.get("progress") or 0),
                    str(payload.get("phase") or "running"),
                    str(payload.get("message") or "正在回测"),
                )

        try:
            while process.poll() is None:
                check_cancelled()
                forward_progress()
                if cancel_event is not None:
                    cancel_event.wait(0.1)
                else:
                    threading.Event().wait(0.1)
            forward_progress()
            check_cancelled()
            if process.returncode != 0:
                error = read_json_cache(error_path) or {}
                raise BacktestTaskError(
                    _worker_error_message(error, int(process.returncode or 1))
                )
            payload = read_json_cache(result_path)
            result = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(result, dict):
                raise BacktestTaskError("回测子进程未返回有效结果")
            return result
        finally:
            self._terminate_process(process)
            with self._lock:
                self._processes.pop(job_id, None)
            shutil.rmtree(worker_dir, ignore_errors=True)

    def _execute(self, job_id: str, request: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            cancel_event = self._cancel_events.get(job_id)
            if (
                job is None
                or job.get("status") not in {"queued", "running"}
                or (cancel_event is not None and cancel_event.is_set())
            ):
                return
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            job.update({
                "status": "running",
                "phase": "preparing",
                "progress": 1,
                "message": "正在准备回测",
                "trading_date": "",
                "day_elapsed_seconds": None,
                "eta_seconds": None,
                "updated_at": now,
                "started_at": now,
            })
            try:
                self._persist_job_locked(job)
            except (OSError, TypeError, ValueError):
                job.update({
                    "status": "failed",
                    "phase": "failed",
                    "message": "回测任务状态保存失败",
                    "error": "BacktestPersistenceError: 无法保存服务端任务状态",
                    "finished_at": now,
                })
                return

        def check_cancelled() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise _BacktestCancelled("backtest cancelled")
            with self._lock:
                current = self._jobs.get(job_id)
                if current is None or current.get("status") != "running":
                    raise _BacktestCancelled("backtest cancelled")

        def update_progress(
            percent: int,
            phase: str,
            message: str,
            details: Mapping[str, Any] | None = None,
        ) -> None:
            check_cancelled()
            with self._lock:
                current = self._jobs.get(job_id)
                if current is None or current.get("status") != "running":
                    raise _BacktestCancelled("backtest cancelled")
                previous_progress = int(current.get("progress") or 0)
                previous_phase = str(current.get("phase") or "")
                previous_details = (
                    current.get("trading_date"),
                    current.get("day_elapsed_seconds"),
                    current.get("eta_seconds"),
                )
                current["progress"] = max(
                    previous_progress,
                    min(99, int(percent)),
                )
                current["phase"] = str(phase or "running")
                current["message"] = str(message or "正在回测")[:200]
                if details:
                    current["trading_date"] = str(
                        details.get("trading_date") or ""
                    )[:10]
                    current["day_elapsed_seconds"] = details.get(
                        "day_elapsed_seconds"
                    )
                    current["eta_seconds"] = details.get("eta_seconds")
                current["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                if (
                    current["progress"] == previous_progress
                    and current["phase"] == previous_phase
                    and previous_details == (
                        current.get("trading_date"),
                        current.get("day_elapsed_seconds"),
                        current.get("eta_seconds"),
                    )
                ):
                    return
                try:
                    self._persist_job_locked(current)
                except (OSError, TypeError, ValueError) as exc:
                    raise BacktestTaskError("回测进度无法保存到服务端") from exc

        def progress(percent: int, phase: str, message: str) -> None:
            update_progress(percent, phase, message)

        setattr(progress, "check_cancelled", check_cancelled)
        setattr(progress, "report", update_progress)
        try:
            if self._uses_subprocess and self._state_dir is not None:
                result = self._run_default_subprocess(
                    job_id,
                    request,
                    progress=progress,
                    check_cancelled=check_cancelled,
                    cancel_event=cancel_event,
                )
            else:
                result = self._runner(request, progress_callback=progress)
        except _BacktestCancelled:
            return
        except Exception as exc:
            with self._lock:
                current = self._jobs.get(job_id)
                if current is not None and current.get("status") == "running":
                    current.update({
                        "status": "failed",
                        "phase": "failed",
                        "message": "回测失败",
                        "error": _safe_error(exc),
                        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    })
                    try:
                        self._persist_job_locked(current)
                    except (OSError, TypeError, ValueError):
                        pass
            return
        with self._lock:
            current = self._jobs.get(job_id)
            if current is not None and current.get("status") == "running":
                now = datetime.now().astimezone().isoformat(timespec="seconds")
                current.update({
                    "status": "succeeded",
                    "phase": "completed",
                    "progress": 100,
                    "message": "回测完成",
                    "eta_seconds": 0.0,
                    "result": result,
                    "updated_at": now,
                    "finished_at": now,
                })
                try:
                    self._persist_job_locked(current)
                except (OSError, TypeError, ValueError):
                    current.update({
                        "status": "failed",
                        "phase": "failed",
                        "message": "回测结果保存失败",
                        "error": "BacktestPersistenceError: 无法保存回测结果",
                    })

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        """Persist a terminal state and signal the server worker to stop."""
        resolved = str(job_id or "").strip()
        if not resolved:
            return None
        process: subprocess.Popen[Any] | None = None
        with self._lock:
            job = self._jobs.get(resolved)
            if job is None:
                return None
            if job.get("status") not in {"queued", "running"}:
                return copy.deepcopy(job)
            event = self._cancel_events.get(resolved)
            if event is None:
                event = threading.Event()
                self._cancel_events[resolved] = event
            event.set()
            process = self._processes.get(resolved)
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            job.update({
                "status": "cancelled",
                "phase": "cancelled",
                "message": "回测已由用户终止",
                "updated_at": now,
                "finished_at": now,
                "result": None,
                "error": "",
            })
            try:
                self._persist_job_locked(job)
            except (OSError, TypeError, ValueError) as exc:
                raise BacktestTaskError("回测已终止，但终止状态保存失败") from exc
            result = copy.deepcopy(job)
        if process is not None:
            self._terminate_process(process)
        return result

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(str(job_id or ""))
            return copy.deepcopy(job) if job is not None else None

    def latest(self, strategy_id: str) -> dict[str, Any] | None:
        """Return the newest retained job for one strategy suite."""
        resolved = str(strategy_id or "").strip()
        if not resolved:
            return None
        with self._lock:
            job = self._latest_locked(resolved)
            return copy.deepcopy(job) if job is not None else None

    def shutdown(self, *, wait: bool = True) -> None:
        """Release the worker; primarily useful for isolated app/tests."""
        with self._lock:
            processes = tuple(self._processes.values())
        for process in processes:
            self._terminate_process(process)
        self._executor.shutdown(wait=wait, cancel_futures=True)


_TASK_MANAGER: BacktestTaskManager | None = None
_TASK_MANAGER_LOCK = threading.Lock()


def get_backtest_task_manager() -> BacktestTaskManager:
    global _TASK_MANAGER
    with _TASK_MANAGER_LOCK:
        if _TASK_MANAGER is None:
            _TASK_MANAGER = BacktestTaskManager(state_dir=default_backtest_state_dir())
        return _TASK_MANAGER


__all__ = [
    "BacktestTaskError",
    "BacktestTaskManager",
    "backtest_strategy_options",
    "get_backtest_task_manager",
    "load_strategy_universe",
    "normalize_backtest_request",
    "run_strategy_backtest_request",
]
