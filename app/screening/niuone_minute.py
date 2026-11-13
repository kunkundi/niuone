"""Minute-level NiuOne theme refresh from one validated quote snapshot.

The fast path deliberately owns no network I/O.  It reuses the current
full-market quote batch, reads completed daily history and industry mappings
from private local caches, and keeps slow external confirmation factors from
the newest full research scan.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from app.market_data.tencent_kline_cache import (
        DEFAULT_KLINE_COUNT,
        load_kline_series_map,
        merge_live_quote,
        normalize_kline_rows,
    )
    from app.screening.stock_universe import (
        FULL_SUPPORTED_NON_ST_UNIVERSE,
        friendly_stock_universe,
        stock_in_universe,
    )
    from app.strategies.scoring.niuone import build_niuone_context
except ImportError:  # pragma: no cover - standalone entrypoints add app/ to sys.path
    from market_data.tencent_kline_cache import (
        DEFAULT_KLINE_COUNT,
        load_kline_series_map,
        merge_live_quote,
        normalize_kline_rows,
    )
    from screening.stock_universe import (
        FULL_SUPPORTED_NON_ST_UNIVERSE,
        friendly_stock_universe,
        stock_in_universe,
    )
    from strategies.scoring.niuone import build_niuone_context

try:
    from app.reports.a_share.calendar import trading_day_status as default_trading_day_status
except ImportError:  # pragma: no cover - standalone entrypoints add app/compat to sys.path
    from a_share_calendar import trading_day_status as default_trading_day_status


MINUTE_REFRESH_MODE = "minute_quotes"
DEFAULT_MINIMUM_COVERAGE = 0.75
DEFAULT_MAX_QUOTE_AGE_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class _EmaState:
    """Final EMA values for one exact sequence of historical closes."""

    row_count: int
    ema20: float
    ema50: float


def _next_ema(previous: float, value: float, period: int) -> float:
    weight = 2 / (period + 1)
    return value * weight + previous * (1 - weight)


def _ema_state_from_rows(rows: Iterable[Mapping[str, Any]]) -> _EmaState | None:
    """Compute both historical EMA seeds in one pass.

    This work is performed only when a symbol's completed daily history enters
    the engine cache. Subsequent live quotes advance each seed by one value.
    """

    ema20: float | None = None
    ema50: float | None = None
    row_count = 0
    for row in rows:
        close = _finite_float(row.get("close"))
        if close is None:
            return None
        ema20 = close if ema20 is None else _next_ema(ema20, close, 20)
        ema50 = close if ema50 is None else _next_ema(ema50, close, 50)
        row_count += 1
    if ema20 is None or ema50 is None:
        return None
    return _EmaState(row_count=row_count, ema20=ema20, ema50=ema50)


def _live_ema_seed(
    historical_rows: list[dict[str, Any]],
    *,
    quote_date: str,
) -> _EmaState | None:
    """Precompute the exact prefix retained before today's live bar.

    ``merge_live_quote`` bounds the merged series to ``DEFAULT_KLINE_COUNT``.
    When a full historical window ends on the previous trading day, appending a
    live bar drops its oldest row. If today's row is already cached, that row is
    replaced instead. Seeding from these exact prefixes keeps the incremental
    result bit-for-bit equivalent to recomputing the merged bounded series.
    """

    if not historical_rows:
        return None
    if str(historical_rows[-1].get("date") or "")[:10] == quote_date:
        prefix = historical_rows[:-1]
    else:
        prefix = historical_rows[-max(1, DEFAULT_KLINE_COUNT - 1):]
    return _ema_state_from_rows(prefix)


def _ema_values_for_live_rows(
    rows: list[dict[str, Any]],
    *,
    quote_date: str,
    seed: _EmaState | None,
) -> tuple[float, float] | None:
    """Advance cached EMA seeds once, with an exact full-series fallback."""

    if (
        seed is not None
        and len(rows) == seed.row_count + 1
        and str(rows[-1].get("date") or "")[:10] == quote_date
    ):
        close = _finite_float(rows[-1].get("close"))
        if close is not None:
            return (
                _next_ema(seed.ema20, close, 20),
                _next_ema(seed.ema50, close, 50),
            )
    fallback = _ema_state_from_rows(rows)
    if fallback is None:
        return None
    return fallback.ema20, fallback.ema50


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stock_code(symbol: Any, quote: Mapping[str, Any]) -> str:
    for value in (quote.get("code"), symbol):
        match = re.search(r"\d{6}", str(value or ""))
        if match:
            return match.group(0)
    return ""


def _quote_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()[:19]
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _industry_cache_signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (int(stat.st_mtime_ns), int(stat.st_size))


def load_stock_industry_map(path: Path) -> dict[str, str]:
    """Read the private scanner-produced industry cache without network fallback."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    result: dict[str, str] = {}
    for raw_code, raw_industry in payload.items():
        match = re.search(r"\d{6}", str(raw_code or ""))
        industry = re.sub(r"\s+", "", str(raw_industry or "")).strip()
        if match and industry:
            result[match.group(0)] = industry
    return result


def _classification_record(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        industry = re.sub(r"\s+", "", str(value.get("industry") or "")).strip()
        raw_themes = value.get("concepts") or value.get("themes") or ()
    else:
        industry = re.sub(r"\s+", "", str(value or "")).strip()
        raw_themes = ()
    if isinstance(raw_themes, str):
        raw_themes = raw_themes.split(",")
    themes = list(dict.fromkeys(
        label
        for item in raw_themes
        if (label := re.sub(r"\s+", "", str(item or "")).strip())
    ))
    if not themes and industry:
        themes = [industry]
    return {"industry": industry, "themes": themes}


def load_stock_board_map(path: Path) -> dict[str, dict[str, Any]]:
    """Read the private Eastmoney classification snapshot without network I/O."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    raw_stocks = payload.get("stocks")
    source = raw_stocks if isinstance(raw_stocks, Mapping) else payload
    result: dict[str, dict[str, Any]] = {}
    for raw_code, raw_value in source.items():
        match = re.search(r"\d{6}", str(raw_code or ""))
        record = _classification_record(raw_value)
        if match and (record["industry"] or record["themes"]):
            result[match.group(0)] = record
    return result


class NiuOneMinuteEngine:
    """Cache slow local inputs and rebuild themes from each fresh quote batch."""

    def __init__(
        self,
        *,
        kline_cache_path: Path,
        industry_cache_path: Path,
        kline_loader: Callable[..., dict[str, list[dict[str, Any]]]] = load_kline_series_map,
        industry_loader: Callable[[Path], Mapping[str, Any]] = load_stock_board_map,
        trading_day_status_loader: Callable[..., Mapping[str, Any]] = default_trading_day_status,
        minimum_coverage: float = DEFAULT_MINIMUM_COVERAGE,
        max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
        history_batch_size: int | None = None,
    ) -> None:
        self.kline_cache_path = Path(kline_cache_path)
        self.industry_cache_path = Path(industry_cache_path)
        self.kline_loader = kline_loader
        self.industry_loader = industry_loader
        self.trading_day_status_loader = trading_day_status_loader
        self.minimum_coverage = max(0.0, min(1.0, float(minimum_coverage)))
        self.max_quote_age_seconds = max(30.0, float(max_quote_age_seconds))
        self.history_batch_size = max(1, history_batch_size) if history_batch_size is not None else None
        self._lock = threading.Lock()
        self._history_date = ""
        self._histories: dict[str, list[dict[str, Any]]] = {}
        self._live_ema_seeds: dict[str, _EmaState | None] = {}
        self._industry_signature = (0, 0)
        self._industries: dict[str, dict[str, Any]] = {}

    def _eligible_quotes(
        self,
        quote_snapshot: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        raw_quotes = quote_snapshot.get("quotes")
        if not isinstance(raw_quotes, Mapping):
            return {}
        eligible: dict[str, dict[str, Any]] = {}
        for raw_symbol, raw_quote in raw_quotes.items():
            if not isinstance(raw_quote, Mapping):
                continue
            symbol = re.sub(r"[^a-zA-Z0-9]", "", str(raw_symbol or "")).lower()
            if not re.fullmatch(r"(?:sh|sz)\d{6}", symbol):
                continue
            quote = dict(raw_quote)
            code = _stock_code(symbol, quote)
            name = str(quote.get("name") or "").strip()
            price = _finite_float(quote.get("price"))
            if (
                not code
                or price is None
                or price <= 0
                or not stock_in_universe(code, name, FULL_SUPPORTED_NON_ST_UNIVERSE)
            ):
                continue
            quote["code"] = code
            eligible[symbol] = quote
        return eligible

    def _load_slow_inputs(
        self,
        symbols: Iterable[str],
        *,
        quote_date: str,
        accepted_last_dates: set[str],
    ) -> tuple[
        dict[str, list[dict[str, Any]]],
        dict[str, dict[str, Any]],
        dict[str, _EmaState | None],
    ]:
        unique_symbols = list(dict.fromkeys(symbols))
        with self._lock:
            if self._history_date != quote_date:
                self._history_date = quote_date
                self._histories = {}
                self._live_ema_seeds = {}
            missing = [symbol for symbol in unique_symbols if symbol not in self._histories]
            if missing:
                loaded = self.kline_loader(
                    missing,
                    path=self.kline_cache_path,
                    accepted_last_dates=accepted_last_dates,
                    min_rows=30,
                    count=DEFAULT_KLINE_COUNT,
                )
                for symbol, rows in (loaded or {}).items():
                    normalized = normalize_kline_rows(
                        rows or [],
                        limit=DEFAULT_KLINE_COUNT,
                    )
                    resolved_symbol = str(symbol)
                    self._histories[resolved_symbol] = normalized
                    self._live_ema_seeds[resolved_symbol] = _live_ema_seed(
                        normalized,
                        quote_date=quote_date,
                    )

            signature = _industry_cache_signature(self.industry_cache_path)
            if not self._industries or signature != self._industry_signature:
                self._industries = {
                    str(code): _classification_record(value)
                    for code, value in self.industry_loader(self.industry_cache_path).items()
                }
                self._industry_signature = signature

            return (
                {symbol: self._histories.get(symbol, []) for symbol in unique_symbols},
                dict(self._industries),
                {
                    symbol: self._live_ema_seeds.get(symbol)
                    for symbol in unique_symbols
                },
            )

    def build_scan(
        self,
        quote_snapshot: Mapping[str, Any],
        *,
        previous_payload: Mapping[str, Any] | None = None,
        flow_rows: Any = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Build one cache-ready scan or raise without replacing valid state."""

        started = time.monotonic()
        quote_generated_at = str(quote_snapshot.get("generated_at") or "")[:19]
        quote_time = _quote_timestamp(quote_generated_at)
        current = now or datetime.now()
        if quote_time is None:
            raise ValueError("minute quote snapshot has no valid timestamp")
        quote_age = (current - quote_time).total_seconds()
        if quote_age < -120 or quote_age > self.max_quote_age_seconds:
            raise ValueError("minute quote snapshot is stale")

        quotes = self._eligible_quotes(quote_snapshot)
        if not quotes:
            raise ValueError("minute quote snapshot has no supported non-ST stocks")

        previous_payload = previous_payload if isinstance(previous_payload, Mapping) else {}
        previous_context = (
            previous_payload.get("niuone_context")
            if isinstance(previous_payload.get("niuone_context"), Mapping)
            else {}
        )
        quote_date = quote_time.strftime("%Y-%m-%d")
        previous_context_date = str(previous_context.get("as_of_date") or "")[:10]
        try:
            calendar_status = self.trading_day_status_loader(quote_date, allow_refresh=False)
            previous_trading_day = str(calendar_status.get("previous_trading_day") or "")[:10]
        except Exception:
            previous_trading_day = ""
        if not previous_trading_day and previous_context_date == quote_date:
            previous_trading_day = str(previous_context.get("previous_trading_day") or "")[:10]
        accepted_dates = {
            value
            for value in (
                quote_date,
                previous_trading_day,
            )
            if value
        }
        previous_pool_count = int(previous_payload.get("reference_pool_count") or 0)
        reference_pool_count = max(previous_pool_count, len(quotes))

        def prepared_items():
            symbols = list(quotes)
            size = self.history_batch_size or len(symbols)
            count = 0
            for start in range(0, len(symbols), size):
                batch = symbols[start:start + size]
                histories, industries, seeds = self._load_slow_inputs(
                    batch, quote_date=quote_date, accepted_last_dates=accepted_dates,
                )
                if self.history_batch_size:
                    # An isolated one-shot worker retains only the current batch.
                    with self._lock:
                        self._histories.clear()
                        self._live_ema_seeds.clear()
                for symbol in batch:
                    quote = quotes[symbol]
                    rows = merge_live_quote(histories.get(symbol, []), quote)
                    if len(rows) < 30:
                        continue
                    ema_values = _ema_values_for_live_rows(rows, quote_date=quote_date, seed=seeds.get(symbol))
                    if ema_values is None:
                        continue
                    rows[-1]["ema20"], rows[-1]["ema50"] = ema_values
                    code = str(quote.get("code") or "")
                    classification = industries.get(code) or {}
                    count += 1
                    yield {"code": code, "name": str(quote.get("name") or ""),
                           "industry": str(classification.get("industry") or ""),
                           "themes": list(classification.get("themes") or ()), "quote": quote, "rows": rows}
            coverage = count / reference_pool_count
            if coverage < self.minimum_coverage:
                raise ValueError(f"minute K-line coverage {coverage:.1%} is below {self.minimum_coverage:.0%}")

        market_snapshot = (
            dict(quote_snapshot.get("market_snapshot") or {})
            if isinstance(quote_snapshot.get("market_snapshot"), Mapping)
            else {}
        )
        context = build_niuone_context(
            prepared_items(),
            reference_pool_count=reference_pool_count,
            market_snapshot=market_snapshot,
            flow_rows=flow_rows,
            previous_context=dict(previous_context),
            as_of_date=quote_date,
            previous_trading_day=previous_trading_day,
            sample_at=quote_generated_at,
            reuse_previous_external_context=True,
            theme_basis="eastmoney_concept",
        )
        if float(context.get("data_coverage") or 0.0) < self.minimum_coverage:
            raise ValueError("minute theme coverage is below the safe publish threshold")
        context["quote_generated_at"] = quote_generated_at
        context["refresh_mode"] = MINUTE_REFRESH_MODE
        context["external_context_mode"] = "cached_full_scan"

        generated_at = current.strftime("%Y-%m-%d %H:%M:%S")
        return {
            "generated_at": generated_at,
            "quote_generated_at": quote_generated_at,
            "refresh_mode": MINUTE_REFRESH_MODE,
            "calculation_duration_ms": int((time.monotonic() - started) * 1000),
            "reference_stock_universe": list(FULL_SUPPORTED_NON_ST_UNIVERSE),
            "reference_stock_universe_label": (
                f"{friendly_stock_universe(FULL_SUPPORTED_NON_ST_UNIVERSE)}（全量非 ST）"
            ),
            "reference_pool_count": reference_pool_count,
            "reference_analysis_count": len(quotes),
            "niuone_context": context,
        }


__all__ = [
    "DEFAULT_MAX_QUOTE_AGE_SECONDS",
    "DEFAULT_MINIMUM_COVERAGE",
    "MINUTE_REFRESH_MODE",
    "NiuOneMinuteEngine",
    "load_stock_board_map",
    "load_stock_industry_map",
]
