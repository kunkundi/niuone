"""Compose historical data clients with selection-signal backtesting."""
from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .historical_data import (
    HistoricalDataError,
    HistoricalDataResult,
    HistoricalFetchConfig,
    SourceFetcher,
    fetch_historical_data,
    normalize_a_share_symbol,
)
from .selection import (
    HistoricalBar,
    PositionExitStrategy,
    ReplaySelectionStrategy,
    SelectionBacktestConfig,
    SelectionBacktestResult,
    SelectionFunction,
    SelectionStrategy,
    build_selection_replay_tape,
    run_selection_backtest,
)
from .replay_cache import ReplayTapeCache, build_replay_cache_key


IndustryMapLoader = Callable[[set[str]], Mapping[str, str]]
ThemeMapLoader = Callable[[set[str]], Mapping[str, Iterable[str]]]
ClassificationSnapshotLoader = Callable[[set[str]], Any]
BacktestProgress = Callable[[int, str, str], None]
AnnotationProgress = Callable[[int, int, str], None]


NIUONE_REPLAY_SCORED_FIELDS = (
    "atr",
    "atr20",
    "execution_buffer_pct",
    "gap_buffer_pct",
    "industry",
    "mainline_confirmed",
    "mainline_cross_day_persistent",
    "mainline_score",
    "mainline_state",
    "market_allows_buys",
    "market_hard_stop",
    "market_regime",
    "niuone_entry_subroute",
    "niuone_lifecycle_stage",
    "recent_close",
    "reversal_basis",
    "stock_leader_rank",
    "stock_leader_tier",
    "stock_strong",
    "stop_price",
    "stop_source",
)


def _report_progress(
    callback: BacktestProgress | None,
    percent: int,
    phase: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> None:
    if callback is None:
        return
    combined_reporter = getattr(callback, "report", None)
    if callable(combined_reporter):
        combined_reporter(percent, phase, message, dict(details or {}))
    else:
        callback(percent, phase, message)


def _code(value: Any) -> str:
    matched = re.search(r"\d{6}", str(value or ""))
    return matched.group(0) if matched else ""


def _normalized_metadata_map(values: Mapping[str, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_symbol, raw_value in (values or {}).items():
        value = str(raw_value or "").strip()
        if not value:
            continue
        try:
            symbol = normalize_a_share_symbol(raw_symbol)
        except HistoricalDataError:
            continue
        result[symbol] = value
        result[symbol[-6:]] = value
    return result


def _normalized_theme_map(
    values: Mapping[str, Iterable[str]] | None,
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for raw_symbol, raw_values in (values or {}).items():
        if isinstance(raw_values, str):
            candidates: Iterable[Any] = raw_values.split(",")
        else:
            candidates = raw_values or ()
        labels = tuple(dict.fromkeys(
            str(item or "").strip() for item in candidates if str(item or "").strip()
        ))
        if not labels:
            continue
        try:
            symbol = normalize_a_share_symbol(raw_symbol)
        except HistoricalDataError:
            continue
        result[symbol] = labels
        result[symbol[-6:]] = labels
    return result


class _ClassificationMap(dict):
    def __init__(
        self,
        values: Mapping[str, Any],
        *,
        source: str,
        as_of_date: str,
        stale: bool,
    ) -> None:
        super().__init__(values)
        self.source = source
        self.as_of_date = as_of_date
        self.stale = stale


class CurrentClassificationError(RuntimeError):
    """Raised when no complete current classification snapshot is available."""


def load_current_classification_snapshot(
    symbols: Iterable[str],
    *,
    env: Mapping[str, str] | None = None,
    eastmoney_loader: Callable[..., Any] | None = None,
    iwencai_loader: Callable[..., Any] | None = None,
):
    """Prefer Eastmoney, then use configured iWencai only when no snapshot exists."""

    codes = {_code(symbol) for symbol in symbols}
    codes.discard("")
    if not codes:
        return None
    try:
        from app.core.paths import get_dashboard_home
        from app.market_data.eastmoney_boards import (
            EastmoneyBoardError,
            load_eastmoney_board_snapshot,
        )
        from app.market_data.iwencai_boards import (
            IwencaiBoardError,
            fetch_iwencai_board_snapshot,
            load_iwencai_board_snapshot,
        )
        from app.market_data.iwencai_client import (
            IwencaiConfig,
            IwencaiConfigurationError,
            IwencaiError,
        )
    except ImportError:  # pragma: no cover - legacy top-level import path
        from core.paths import get_dashboard_home
        from market_data.eastmoney_boards import (
            EastmoneyBoardError,
            load_eastmoney_board_snapshot,
        )
        from market_data.iwencai_boards import (
            IwencaiBoardError,
            fetch_iwencai_board_snapshot,
            load_iwencai_board_snapshot,
        )
        from market_data.iwencai_client import (
            IwencaiConfig,
            IwencaiConfigurationError,
            IwencaiError,
        )
    project_root = Path(__file__).resolve().parents[2]
    output_dir = get_dashboard_home(project_root) / "cron" / "output"
    active_eastmoney_loader = eastmoney_loader or load_eastmoney_board_snapshot
    eastmoney_error: Exception | None = None
    try:
        return active_eastmoney_loader(
            cache_path=output_dir / "eastmoney_stock_boards.json"
        )
    except (EastmoneyBoardError, OSError) as exc:
        eastmoney_error = exc

    values = os.environ if env is None else env
    try:
        iwencai_config = IwencaiConfig.from_env(values)
    except IwencaiConfigurationError as exc:
        raise CurrentClassificationError(
            "板块分类数据不可用：东方财富获取失败，问财备用源配置无效"
        ) from exc
    if not iwencai_config.enabled or not iwencai_config.api_key:
        raise CurrentClassificationError(
            "板块分类数据不可用：东方财富获取失败且没有可用旧快照；"
            "可在设置中启用并配置问财数据源作为首次部署备用源"
        ) from eastmoney_error
    try:
        cache_path = output_dir / "iwencai_stock_boards.json"
        if iwencai_loader is not None:
            return iwencai_loader(cache_path=cache_path)
        return load_iwencai_board_snapshot(
            cache_path=cache_path,
            fetcher=lambda: fetch_iwencai_board_snapshot(config=iwencai_config),
        )
    except (IwencaiBoardError, IwencaiError, OSError) as exc:
        raise CurrentClassificationError(
            "板块分类数据不可用：东方财富和问财备用源均获取失败"
        ) from exc


def load_current_industry_map(symbols: Iterable[str]) -> dict[str, str]:
    """Return a validated current industry map for one bounded universe."""
    codes = {_code(symbol) for symbol in symbols}
    codes.discard("")
    if not codes:
        return {}
    snapshot = load_current_classification_snapshot(codes)
    if snapshot is None:
        return {}
    return _ClassificationMap(
        snapshot.industry_map(codes),
        source=snapshot.source,
        as_of_date=snapshot.as_of_date,
        stale=snapshot.stale,
    )


def load_current_theme_map(symbols: Iterable[str]) -> Mapping[str, Iterable[str]]:
    """Return validated current concepts, falling back only to source industry."""
    codes = {_code(symbol) for symbol in symbols}
    codes.discard("")
    if not codes:
        return {}
    snapshot = load_current_classification_snapshot(codes)
    if snapshot is None:
        return {}
    return _ClassificationMap(
        snapshot.theme_map(codes),
        source=snapshot.source,
        as_of_date=snapshot.as_of_date,
        stale=snapshot.stale,
    )


@dataclass(frozen=True)
class IndustryAnnotationQuality:
    mode: str
    total_bar_count: int
    matched_bar_count: int
    missing_bar_count: int
    covered_symbol_count: int
    requested_symbol_count: int
    source: str = ""
    snapshot_summary: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "total_bar_count": self.total_bar_count,
            "matched_bar_count": self.matched_bar_count,
            "missing_bar_count": self.missing_bar_count,
            "bar_coverage_ratio": (
                self.matched_bar_count / self.total_bar_count
                if self.total_bar_count else 0.0
            ),
            "covered_symbol_count": self.covered_symbol_count,
            "requested_symbol_count": self.requested_symbol_count,
            "symbol_coverage_ratio": (
                self.covered_symbol_count / self.requested_symbol_count
                if self.requested_symbol_count else 0.0
            ),
            "source": self.source,
            "snapshot_summary": dict(self.snapshot_summary or {}),
        }


@dataclass(frozen=True)
class HistoricalSelectionBacktestRun:
    data: HistoricalDataResult
    selection: SelectionBacktestResult
    warnings: tuple[str, ...] = ()
    industry_quality: IndustryAnnotationQuality | None = None
    replay_cache: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.data.to_dict(),
            "selection": self.selection.to_dict(),
            "warnings": list(self.warnings),
            "industry_quality": (
                self.industry_quality.to_dict() if self.industry_quality else None
            ),
            "replay_cache": dict(self.replay_cache or {}),
        }


def _annotated_bars(
    data: HistoricalDataResult,
    symbols: tuple[str, ...],
    *,
    industry_by_symbol: Mapping[str, str] | None,
    industry_loader: IndustryMapLoader | None,
    theme_by_symbol: Mapping[str, Iterable[str]] | None,
    theme_loader: ThemeMapLoader | None,
    name_by_symbol: Mapping[str, str] | None,
    classification_loader: ClassificationSnapshotLoader | None = None,
    progress_callback: AnnotationProgress | None = None,
) -> tuple[
    Mapping[str, tuple[HistoricalBar, ...]],
    list[str],
    IndustryAnnotationQuality,
]:
    warnings: list[str] = []
    total = len(data.bars_by_symbol)
    if progress_callback is not None:
        progress_callback(0, total, "")
    static_industries = _normalized_metadata_map(industry_by_symbol)
    static_themes = _normalized_theme_map(theme_by_symbol)
    classification_source = ""
    classification_summary: dict[str, Any] = {}
    if classification_loader is not None:
        snapshot = classification_loader({_code(symbol) for symbol in symbols})
        if snapshot is not None:
            static_industries.update(
                _normalized_metadata_map(snapshot.industry_map(symbols))
            )
            static_themes.update(_normalized_theme_map(snapshot.theme_map(symbols)))
            classification_source = str(getattr(snapshot, "source", "") or "")
            classification_summary.update({
                "as_of_date": str(getattr(snapshot, "as_of_date", "") or ""),
                "stale": bool(getattr(snapshot, "stale", False)),
            })
    elif industry_loader is not None:
        loaded = industry_loader({_code(symbol) for symbol in symbols})
        static_industries.update(_normalized_metadata_map(loaded))
        classification_source = str(getattr(loaded, "source", "") or "")
        classification_summary.update({
            "as_of_date": str(getattr(loaded, "as_of_date", "") or ""),
            "stale": bool(getattr(loaded, "stale", False)),
        })
    if classification_loader is None and theme_loader is not None:
        loaded_themes = theme_loader({_code(symbol) for symbol in symbols})
        static_themes.update(_normalized_theme_map(loaded_themes))
        classification_source = str(
            getattr(loaded_themes, "source", "") or classification_source
        )
        classification_summary.update({
            "as_of_date": str(
                getattr(loaded_themes, "as_of_date", "")
                or classification_summary.get("as_of_date")
                or ""
            ),
            "stale": bool(
                getattr(loaded_themes, "stale", False)
                or classification_summary.get("stale")
            ),
        })
    names = _normalized_metadata_map(name_by_symbol)
    bars_by_symbol: dict[str, tuple[HistoricalBar, ...]] = {}
    total_bar_count = 0
    matched_bar_count = 0
    covered_symbols: set[str] = set()
    for completed, (symbol, rows) in enumerate(data.bars_by_symbol.items(), start=1):
        annotated: list[HistoricalBar] = []
        for raw in rows:
            total_bar_count += 1
            row = dict(raw)
            industry = str(
                static_industries.get(symbol)
                or static_industries.get(symbol[-6:])
                or ""
            ).strip()
            themes = tuple(
                static_themes.get(symbol)
                or static_themes.get(symbol[-6:])
                or ()
            )
            if not themes and industry:
                themes = (industry,)
            if themes or industry:
                row["themes"] = list(themes)
                row["industry"] = industry or themes[0]
                matched_bar_count += 1
                covered_symbols.add(symbol)
            else:
                # Raw rows do not own classification metadata. Missing current
                # provider coverage must remain visibly unclassified.
                row.pop("industry", None)
                row.pop("sector", None)
                row.pop("themes", None)
            name = names.get(symbol) or names.get(symbol[-6:])
            if name:
                row["name"] = name
            annotated.append(HistoricalBar.from_value(symbol, row))
        bars_by_symbol[symbol] = tuple(annotated)
        if progress_callback is not None:
            progress_callback(completed, total, symbol)
    fallback_symbols = [
        symbol for symbol, series in data.series.items() if series.attempts
    ]
    if fallback_symbols:
        displayed = ", ".join(fallback_symbols[:10])
        remaining = len(fallback_symbols) - 10
        suffix = f" (+{remaining} more)" if remaining > 0 else ""
        warnings.append(
            "fallback source used after earlier source failures for "
            f"{len(fallback_symbols)} symbols: {displayed}{suffix}"
        )
    if data.failures:
        warnings.append(
            "partial universe fetched because HistoricalFetchConfig.strict=False: "
            + ", ".join(data.failures)
        )
    missing_bar_count = max(0, total_bar_count - matched_bar_count)
    snapshot_summary: Mapping[str, Any] | None = classification_summary or None
    source = classification_source
    if source == "iwencai_current_industry_concept":
        warnings.append(
            "current classification fallback used: iwencai_current_industry_concept"
        )
    if classification_summary.get("stale"):
        warnings.append(
            "stale current classification snapshot used: "
            f"{classification_summary.get('as_of_date') or 'unknown date'}"
        )
    mode = "missing"
    if static_industries or static_themes:
        mode = (
            "iwencai_current"
            if source == "iwencai_current_industry_concept"
            else "eastmoney_current"
        )
    quality = IndustryAnnotationQuality(
        mode=mode,
        total_bar_count=total_bar_count,
        matched_bar_count=matched_bar_count,
        missing_bar_count=missing_bar_count,
        covered_symbol_count=len(covered_symbols),
        requested_symbol_count=len(data.bars_by_symbol),
        source=source,
        snapshot_summary=snapshot_summary,
    )
    return MappingProxyType(bars_by_symbol), warnings, quality


def run_historical_selection_backtest(
    symbols: Iterable[str],
    signal_start_date: str,
    signal_end_date: str,
    selector: SelectionStrategy | SelectionFunction,
    *,
    fetch_config: HistoricalFetchConfig | None = None,
    selection_config: SelectionBacktestConfig | None = None,
    position_exit_strategy: PositionExitStrategy | None = None,
    warmup_calendar_days: int = 150,
    forward_calendar_days: int = 45,
    minimum_coverage_ratio: float = 0.0,
    source_fetchers: Mapping[str, SourceFetcher] | None = None,
    industry_by_symbol: Mapping[str, str] | None = None,
    classification_loader: ClassificationSnapshotLoader | None = None,
    industry_loader: IndustryMapLoader | None = None,
    theme_by_symbol: Mapping[str, Iterable[str]] | None = None,
    theme_loader: ThemeMapLoader | None = None,
    name_by_symbol: Mapping[str, str] | None = None,
    progress_callback: BacktestProgress | None = None,
    replay_cache: ReplayTapeCache | None = None,
    replay_cache_identity: Mapping[str, Any] | None = None,
) -> HistoricalSelectionBacktestRun:
    """Download warmup/forward buffers and evaluate selected stocks."""
    try:
        start = datetime.strptime(str(signal_start_date)[:10], "%Y-%m-%d").date()
        end = datetime.strptime(str(signal_end_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HistoricalDataError("signal dates must use YYYY-MM-DD") from None
    if start > end:
        raise HistoricalDataError("signal_start_date cannot be after signal_end_date")
    if not 0 <= int(warmup_calendar_days) <= 730:
        raise HistoricalDataError("warmup_calendar_days must be between 0 and 730")
    if not 0 <= int(forward_calendar_days) <= 366:
        raise HistoricalDataError("forward_calendar_days must be between 0 and 366")
    if not 0 <= float(minimum_coverage_ratio) <= 1:
        raise HistoricalDataError("minimum_coverage_ratio must be between 0 and 1")
    normalized_symbols = tuple(dict.fromkeys(
        normalize_a_share_symbol(symbol) for symbol in symbols
    ))
    if not normalized_symbols:
        raise HistoricalDataError("at least one symbol is required")
    fetch_start = (start - timedelta(days=int(warmup_calendar_days))).isoformat()
    fetch_end = (end + timedelta(days=int(forward_calendar_days))).isoformat()
    if progress_callback is not None:
        progress_callback(2, "preparing", "正在校验回测参数")

    def fetch_progress(completed: int, total: int, symbol: str, succeeded: bool) -> None:
        if progress_callback is None:
            return
        percent = 5 + round(completed / max(1, total) * 52)
        action = "已获取" if succeeded else "获取失败"
        progress_callback(percent, "fetching", f"{action} {symbol}（{completed}/{total}）")

    cancellation_check = getattr(progress_callback, "check_cancelled", None)
    data = fetch_historical_data(
        normalized_symbols,
        fetch_start,
        fetch_end,
        config=fetch_config,
        source_fetchers=source_fetchers,
        progress_callback=fetch_progress,
        cancellation_check=(
            cancellation_check if callable(cancellation_check) else None
        ),
    )
    coverage_ratio = len(data.series) / len(normalized_symbols)
    if coverage_ratio + 1e-12 < float(minimum_coverage_ratio):
        raise HistoricalDataError(
            "historical universe coverage below minimum: "
            f"{len(data.series)}/{len(normalized_symbols)} "
            f"({coverage_ratio:.1%} < {float(minimum_coverage_ratio):.1%})"
        )
    def annotation_progress(completed: int, total: int, _symbol: str) -> None:
        if progress_callback is None:
            return
        bounded_completed = min(max(0, completed), max(0, total))
        pending = max(0, total - bounded_completed)
        percent = 58 + round(bounded_completed / max(1, total) * 5)
        progress_callback(
            percent,
            "annotating",
            f"正在补充行业信息：已处理 {bounded_completed} 只 / 待处理 {pending} 只",
        )

    bars_by_symbol, warnings, industry_quality = _annotated_bars(
        data,
        tuple(data.bars_by_symbol),
        industry_by_symbol=industry_by_symbol,
        classification_loader=classification_loader,
        industry_loader=industry_loader,
        theme_by_symbol=theme_by_symbol,
        theme_loader=theme_loader,
        name_by_symbol=name_by_symbol,
        progress_callback=annotation_progress,
    )
    if len(data.series) < len(normalized_symbols):
        warnings.append(
            "historical universe coverage: "
            f"{len(data.series)}/{len(normalized_symbols)} ({coverage_ratio:.1%})"
        )
    resolved_selection = replace(
        selection_config or SelectionBacktestConfig(),
        signal_start_date=start.isoformat(),
        signal_end_date=end.isoformat(),
    )
    def normalization_progress(completed: int, total: int) -> None:
        if progress_callback is None:
            return
        bounded_completed = min(max(0, completed), max(0, total))
        pending = max(0, total - bounded_completed)
        percent = 64 + round(bounded_completed / max(1, total))
        progress_callback(
            percent,
            "normalizing",
            f"正在整理历史行情：已处理 {bounded_completed} 只 / 待处理 {pending} 只",
        )

    def preparation_progress(completed: int, total: int) -> None:
        if progress_callback is None:
            return
        bounded_completed = min(max(0, completed), max(0, total))
        pending = max(0, total - bounded_completed)
        percent = 65 + round(bounded_completed / max(1, total) * 2)
        progress_callback(
            percent,
            "precomputing",
            f"正在预计算技术指标：已处理 {bounded_completed} 只 / 待处理 {pending} 只",
        )

    def selection_progress(completed: int, total: int, trading_date: str) -> None:
        if progress_callback is None:
            return
        percent = 68 + round(completed / max(1, total) * 29)
        progress_callback(
            percent,
            "evaluating",
            f"正在回放 {trading_date}（{completed}/{total}）",
        )
    cache_info: dict[str, Any] = {"enabled": False, "hit": False}
    execution_selector = selector
    use_replay_cache = replay_cache is not None and replay_cache_identity is not None
    if use_replay_cache:
        identity = dict(replay_cache_identity or {})
        _report_progress(
            progress_callback,
            64,
            "replay_cache",
            "正在校验选股回放缓存",
        )
        cache_key = build_replay_cache_key(
            bars_by_symbol,
            protocol_version=str(identity.get("protocol_version") or ""),
            selector_id=str(identity.get("selector_id") or ""),
            strategy_ids=identity.get("strategy_ids") or (),
            signal_start_date=start.isoformat(),
            signal_end_date=end.isoformat(),
            sources=identity.get("sources") or (),
            adjustment=str(identity.get("adjustment") or ""),
            stock_pool=identity.get("stock_pool") or (),
            source_by_symbol=data.source_by_symbol,
        )
        tape = replay_cache.load(cache_key)
        cache_hit = tape is not None
        if tape is None:
            with replay_cache.build_lock(cache_key):
                # Another Dashboard may have completed the same miss while this
                # worker waited for the bounded cross-process build lock.
                tape = replay_cache.load(cache_key)
                cache_hit = tape is not None
                if tape is None:
                    def replay_progress(
                        completed: int,
                        total: int,
                        trading_date: str,
                        phase: str,
                        day_elapsed: float,
                        eta_seconds: float | None,
                    ) -> None:
                        bounded = min(max(0, completed), max(0, total))
                        percent = 68 + round(bounded / max(1, total) * 20)
                        label = (
                            "正在重建题材截面"
                            if phase == "rebuilding_context" else "正在执行策略评分"
                        )
                        _report_progress(
                            progress_callback,
                            percent,
                            phase,
                            f"{label}：{trading_date}（{bounded}/{total}）",
                            details={
                                "trading_date": trading_date,
                                "day_elapsed_seconds": round(day_elapsed, 2),
                                "eta_seconds": (
                                    round(eta_seconds, 1)
                                    if eta_seconds is not None else None
                                ),
                            },
                        )

                    tape = build_selection_replay_tape(
                        bars_by_symbol,
                        selector,
                        config=resolved_selection,
                        normalization_progress_callback=normalization_progress,
                        preparation_progress_callback=preparation_progress,
                        replay_progress_callback=replay_progress,
                        scored_fields=(
                            NIUONE_REPLAY_SCORED_FIELDS
                            if position_exit_strategy is not None else None
                        ),
                    )
                    if not replay_cache.store(cache_key, tape):
                        warnings.append(
                            "selection replay cache could not be persisted; this run "
                            "completed with an in-memory tape"
                        )
        cache_info = {
            "enabled": True,
            "hit": cache_hit,
            "key": cache_key.digest[:16],
            "schema_version": int(cache_key.descriptor["schema_version"]),
            "classification_snapshot_hash": str(
                cache_key.descriptor["classification_snapshot_hash"]
            ),
        }
        if cache_hit:
            _report_progress(
                progress_callback,
                88,
                "replay_cache",
                "已复用选股回放缓存，跳过题材截面重建与评分",
            )
        execution_selector = ReplaySelectionStrategy(tape)

    execution_started_at = time.perf_counter()
    previous_execution_progress_at = execution_started_at
    execution_elapsed: list[float] = []

    def execution_progress(completed: int, total: int, trading_date: str) -> None:
        nonlocal previous_execution_progress_at
        now = time.perf_counter()
        day_elapsed = max(0.0, now - previous_execution_progress_at)
        previous_execution_progress_at = now
        execution_elapsed.append(day_elapsed)
        average_elapsed = sum(execution_elapsed) / len(execution_elapsed)
        bounded = min(max(0, completed), max(0, total))
        phase = (
            "replaying_exits"
            if position_exit_strategy is not None else "evaluating"
        )
        label = (
            "正在回放持仓与退出"
            if position_exit_strategy is not None else "正在回放选股信号"
        )
        _report_progress(
            progress_callback,
            89 + round(bounded / max(1, total) * 10),
            phase,
            f"{label}：{trading_date}（{bounded}/{total}）",
            details={
                "trading_date": trading_date,
                "day_elapsed_seconds": round(day_elapsed, 2),
                "eta_seconds": round(
                    average_elapsed * max(0, total - bounded),
                    1,
                ),
            },
        )

    selection = run_selection_backtest(
        bars_by_symbol,
        execution_selector,
        config=resolved_selection,
        position_exit_strategy=position_exit_strategy,
        progress_callback=(execution_progress if use_replay_cache else selection_progress),
        normalization_progress_callback=(
            None if use_replay_cache else normalization_progress
        ),
        preparation_progress_callback=(
            None if use_replay_cache else preparation_progress
        ),
    )
    if progress_callback is not None:
        progress_callback(100, "completed", "回测完成")
    return HistoricalSelectionBacktestRun(
        data=data,
        selection=selection,
        warnings=tuple(warnings),
        industry_quality=industry_quality,
        replay_cache=MappingProxyType(cache_info),
    )


__all__ = [
    "HistoricalSelectionBacktestRun",
    "BacktestProgress",
    "ClassificationSnapshotLoader",
    "CurrentClassificationError",
    "IndustryMapLoader",
    "ThemeMapLoader",
    "IndustryAnnotationQuality",
    "load_current_classification_snapshot",
    "load_current_industry_map",
    "load_current_theme_map",
    "run_historical_selection_backtest",
]
