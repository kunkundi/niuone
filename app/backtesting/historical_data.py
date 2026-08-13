"""Bounded multi-source historical daily bars for selection backtesting."""
from __future__ import annotations

import concurrent.futures
import json
import math
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Any


EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
TENCENT_KLINE_URL = "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
SINA_KLINE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/"
    "var%20_{symbol}_niuone=/CN_MarketDataService.getKLineData"
)
USER_AGENT = "Mozilla/5.0 NiuOne/1.0"
SUPPORTED_HISTORICAL_SOURCES = ("eastmoney", "tencent", "sina")
DEFAULT_HISTORICAL_SOURCE_PRIORITY = SUPPORTED_HISTORICAL_SOURCES
SUPPORTED_ADJUSTMENTS = ("qfq", "hfq", "none")
TENCENT_WINDOW_DAYS = 700
SINA_MAX_ROWS = 1023


class HistoricalDataError(RuntimeError):
    """Raised when requested historical data cannot be fetched safely."""


SourceFetcher = Callable[[str, str, str, str, float], Sequence[Mapping[str, Any]]]
FetchProgress = Callable[[int, int, str, bool], None]


def _date_text(value: Any, *, field_name: str) -> str:
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        text = str(value or "").strip()[:10]
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            raise HistoricalDataError(f"{field_name} must use YYYY-MM-DD") from None
    return parsed.isoformat()


def normalize_a_share_symbol(value: Any) -> str:
    """Return a market-prefixed A-share symbol."""
    raw = re.sub(r"[^a-zA-Z0-9]", "", str(value or "")).lower()
    if re.fullmatch(r"(?:sh|sz|bj)\d{6}", raw):
        return sys.intern(raw)
    if not re.fullmatch(r"\d{6}", raw):
        raise HistoricalDataError(f"unsupported A-share symbol: {value!r}")
    if raw.startswith(("6", "9")):
        return sys.intern(f"sh{raw}")
    if raw.startswith(("4", "8")):
        return sys.intern(f"bj{raw}")
    return sys.intern(f"sz{raw}")


def _eastmoney_secid(symbol: str) -> str:
    normalized = normalize_a_share_symbol(symbol)
    market = "1" if normalized.startswith("sh") else "0"
    return f"{market}.{normalized[-6:]}"


def _finite_float(value: Any) -> float | None:
    if type(value) in (int, float):
        number = float(value)
    else:
        try:
            number = float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None
    return number if math.isfinite(number) else None


def _download_text(url: str, timeout_seconds: float) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://quote.eastmoney.com/",
            "Connection": "close",
        },
    )
    with urllib.request.urlopen(
        request,
        timeout=max(1.0, float(timeout_seconds)),
    ) as response:
        payload = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="strict")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("gb18030", errors="ignore")


def _normalize_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    symbol: str,
    start_date: str,
    end_date: str,
    source: str,
    adjustment: str,
) -> list[dict[str, Any]]:
    symbol = sys.intern(symbol)
    source = sys.intern(source)
    adjustment = sys.intern(adjustment)
    by_date: dict[str, dict[str, Any]] = {}
    for raw in rows or []:
        if not isinstance(raw, Mapping):
            continue
        raw_date = raw.get("date") or raw.get("day") or raw.get("trade_date")
        matched = re.search(r"\d{4}-\d{2}-\d{2}", str(raw_date or ""))
        if not matched:
            continue
        trading_date = sys.intern(matched.group(0))
        if trading_date < start_date or trading_date > end_date:
            continue
        values = {
            field_name: _finite_float(raw.get(field_name))
            for field_name in ("open", "high", "low", "close", "volume", "amount", "turnover")
        }
        if any(values[name] is None for name in ("open", "high", "low", "close")):
            continue
        open_price = float(values["open"] or 0.0)
        high = float(values["high"] or 0.0)
        low = float(values["low"] or 0.0)
        close = float(values["close"] or 0.0)
        if min(open_price, high, low, close) <= 0:
            continue
        if high + 1e-12 < max(open_price, low, close):
            continue
        if low - 1e-12 > min(open_price, high, close):
            continue
        row: dict[str, Any] = {
            "symbol": symbol,
            "date": trading_date,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": max(0.0, float(values["volume"] or 0.0)),
            "data_source": source,
            "adjustment": adjustment,
        }
        if values["amount"] is not None:
            row["amount"] = max(0.0, float(values["amount"] or 0.0))
        if values["turnover"] is not None:
            row["turnover"] = max(0.0, float(values["turnover"] or 0.0))
        by_date[trading_date] = row
    ordered = [by_date[key] for key in sorted(by_date)]
    for index in range(1, len(ordered)):
        ordered[index]["previous_close"] = ordered[index - 1]["close"]
    return ordered


def parse_eastmoney_daily_klines(
    body: str,
    symbol: str,
    start_date: str,
    end_date: str,
    adjustment: str,
) -> list[dict[str, Any]]:
    """Parse Eastmoney f51-f61 daily rows."""
    try:
        payload = json.loads(str(body or "{}"))
    except json.JSONDecodeError as exc:
        raise HistoricalDataError("Eastmoney returned invalid JSON") from exc
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        raise HistoricalDataError("Eastmoney returned no K-line data")
    parsed: list[dict[str, Any]] = []
    for item in data.get("klines") or []:
        fields = str(item or "").split(",")
        if len(fields) < 7:
            continue
        parsed.append({
            "date": fields[0],
            "open": fields[1],
            "close": fields[2],
            "high": fields[3],
            "low": fields[4],
            "volume": fields[5],
            "amount": fields[6],
            "turnover": fields[10] if len(fields) > 10 else None,
        })
    return _normalize_rows(
        parsed,
        symbol=normalize_a_share_symbol(symbol),
        start_date=start_date,
        end_date=end_date,
        source="eastmoney",
        adjustment=adjustment,
    )


def parse_tencent_daily_klines(
    body: str,
    symbol: str,
    start_date: str,
    end_date: str,
    adjustment: str,
) -> list[dict[str, Any]]:
    """Parse Tencent day/qfqday/hfqday rows."""
    normalized = normalize_a_share_symbol(symbol)
    try:
        payload = json.loads(str(body or "{}"))
    except json.JSONDecodeError as exc:
        raise HistoricalDataError("Tencent returned invalid JSON") from exc
    data = (payload.get("data") or {}).get(normalized) if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        raise HistoricalDataError("Tencent returned no symbol data")
    preferred_key = {"qfq": "qfqday", "hfq": "hfqday", "none": "day"}[adjustment]
    raw_rows = data.get(preferred_key) or []
    if not raw_rows and adjustment != "none" and data.get("day"):
        raise HistoricalDataError(
            f"Tencent returned unadjusted rows for an {adjustment} request"
        )
    parsed: list[dict[str, Any]] = []
    for item in raw_rows:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) < 6:
            continue
        parsed.append({
            "date": item[0],
            "open": item[1],
            "close": item[2],
            "high": item[3],
            "low": item[4],
            "volume": item[5],
        })
    return _normalize_rows(
        parsed,
        symbol=normalized,
        start_date=start_date,
        end_date=end_date,
        source="tencent",
        adjustment=adjustment,
    )


def parse_sina_daily_klines(
    body: str,
    symbol: str,
    start_date: str,
    end_date: str,
    adjustment: str,
) -> list[dict[str, Any]]:
    """Parse Sina JSONP daily rows; this endpoint is unadjusted only."""
    if adjustment != "none":
        raise HistoricalDataError("Sina daily K-line does not provide qfq/hfq prices")
    text = str(body or "").strip()
    matched = re.search(r"\((\[.*\])\)\s*;?\s*$", text, flags=re.DOTALL)
    if not matched:
        raise HistoricalDataError("Sina returned invalid JSONP")
    try:
        payload = json.loads(matched.group(1))
    except json.JSONDecodeError as exc:
        raise HistoricalDataError("Sina returned invalid JSONP data") from exc
    if not isinstance(payload, list):
        raise HistoricalDataError("Sina returned no K-line rows")
    return _normalize_rows(
        payload,
        symbol=normalize_a_share_symbol(symbol),
        start_date=start_date,
        end_date=end_date,
        source="sina",
        adjustment=adjustment,
    )


def _fetch_eastmoney(
    symbol: str,
    start_date: str,
    end_date: str,
    adjustment: str,
    timeout_seconds: float,
) -> Sequence[Mapping[str, Any]]:
    query = urllib.parse.urlencode({
        "secid": _eastmoney_secid(symbol),
        "klt": "101",
        "fqt": {"none": "0", "qfq": "1", "hfq": "2"}[adjustment],
        "beg": start_date.replace("-", ""),
        "end": end_date.replace("-", ""),
        "lmt": "100000",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    })
    return parse_eastmoney_daily_klines(
        _download_text(f"{EASTMONEY_KLINE_URL}?{query}", timeout_seconds),
        symbol,
        start_date,
        end_date,
        adjustment,
    )


def _fetch_tencent(
    symbol: str,
    start_date: str,
    end_date: str,
    adjustment: str,
    timeout_seconds: float,
) -> Sequence[Mapping[str, Any]]:
    normalized = normalize_a_share_symbol(symbol)
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    collected: list[Mapping[str, Any]] = []
    window_start = start
    suffix = "" if adjustment == "none" else adjustment
    while window_start <= end:
        window_end = min(end, window_start + timedelta(days=TENCENT_WINDOW_DAYS - 1))
        parameter = f"{normalized},day,{window_start.isoformat()},{window_end.isoformat()},640"
        if suffix:
            parameter = f"{parameter},{suffix}"
        url = f"{TENCENT_KLINE_URL}?{urllib.parse.urlencode({'param': parameter})}"
        collected.extend(parse_tencent_daily_klines(
            _download_text(url, timeout_seconds),
            normalized,
            window_start.isoformat(),
            window_end.isoformat(),
            adjustment,
        ))
        window_start = window_end + timedelta(days=1)
    return _normalize_rows(
        collected,
        symbol=normalized,
        start_date=start_date,
        end_date=end_date,
        source="tencent",
        adjustment=adjustment,
    )


def _fetch_sina(
    symbol: str,
    start_date: str,
    end_date: str,
    adjustment: str,
    timeout_seconds: float,
) -> Sequence[Mapping[str, Any]]:
    if adjustment != "none":
        raise HistoricalDataError("Sina daily K-line does not provide qfq/hfq prices")
    normalized = normalize_a_share_symbol(symbol)
    base = SINA_KLINE_URL.format(symbol=normalized)
    query = urllib.parse.urlencode({
        "symbol": normalized,
        "scale": "240",
        "ma": "no",
        "datalen": str(SINA_MAX_ROWS),
    })
    return parse_sina_daily_klines(
        _download_text(f"{base}?{query}", timeout_seconds),
        normalized,
        start_date,
        end_date,
        adjustment,
    )


DEFAULT_SOURCE_FETCHERS: Mapping[str, SourceFetcher] = MappingProxyType({
    "eastmoney": _fetch_eastmoney,
    "tencent": _fetch_tencent,
    "sina": _fetch_sina,
})


_HISTORICAL_ROW_STORAGE_TYPES: dict[
    tuple[str, ...], tuple[type, object]
] = {}


def _immutable_historical_row(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """Freeze a row in a key-sharing dict without changing mapping semantics."""

    keys = tuple(row)
    entry = _HISTORICAL_ROW_STORAGE_TYPES.get(keys)
    if entry is None:
        # Built-in rows have at most four stable schemas. Keep a defensive cap for
        # custom fetchers so a long-lived caller cannot create unbounded classes.
        if len(_HISTORICAL_ROW_STORAGE_TYPES) >= 16:
            return MappingProxyType(dict(row))
        storage_type = type("_HistoricalRowStorage", (), {})
        seed = storage_type()
        for key in keys:
            seed.__dict__[key] = None
        entry = (storage_type, seed)
        _HISTORICAL_ROW_STORAGE_TYPES[keys] = entry
    storage = entry[0]()
    storage.__dict__.update(row)
    return MappingProxyType(storage.__dict__)


@dataclass(frozen=True)
class HistoricalFetchConfig:
    sources: tuple[str, ...] = DEFAULT_HISTORICAL_SOURCE_PRIORITY
    adjustment: str = "qfq"
    timeout_seconds: float = 8.0
    max_attempts_per_source: int = 2
    retry_backoff_seconds: float = 0.25
    max_workers: int = 4
    strict: bool = True
    minimum_rows: int = 1
    max_calendar_days: int = 7_305
    source_circuit_min_samples: int = 24
    source_circuit_failure_ratio: float = 0.90

    def __post_init__(self) -> None:
        sources = tuple(dict.fromkeys(str(item or "").strip().lower() for item in self.sources))
        if not sources or any(item not in SUPPORTED_HISTORICAL_SOURCES for item in sources):
            raise HistoricalDataError("sources must contain eastmoney, tencent, or sina")
        adjustment = str(self.adjustment or "").strip().lower()
        if adjustment not in SUPPORTED_ADJUSTMENTS:
            raise HistoricalDataError("adjustment must be qfq, hfq, or none")
        if not 1 <= float(self.timeout_seconds) <= 60:
            raise HistoricalDataError("timeout_seconds must be between 1 and 60")
        if not 1 <= int(self.max_attempts_per_source) <= 5:
            raise HistoricalDataError("max_attempts_per_source must be between 1 and 5")
        if not 0 <= float(self.retry_backoff_seconds) <= 10:
            raise HistoricalDataError("retry_backoff_seconds must be between 0 and 10")
        if not 1 <= int(self.max_workers) <= 16:
            raise HistoricalDataError("max_workers must be between 1 and 16")
        if not 4 <= int(self.source_circuit_min_samples) <= 1_000:
            raise HistoricalDataError("source_circuit_min_samples must be between 4 and 1000")
        if not 0.5 <= float(self.source_circuit_failure_ratio) <= 1.0:
            raise HistoricalDataError("source_circuit_failure_ratio must be between 0.5 and 1")
        if int(self.minimum_rows) <= 0:
            raise HistoricalDataError("minimum_rows must be positive")
        if not 1 <= int(self.max_calendar_days) <= 20_000:
            raise HistoricalDataError("max_calendar_days must be between 1 and 20000")
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "adjustment", adjustment)


@dataclass(frozen=True, slots=True)
class HistoricalSeries:
    symbol: str
    source: str
    adjustment: str
    bars: tuple[Mapping[str, Any], ...]
    attempts: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "adjustment": self.adjustment,
            "bars": [dict(row) for row in self.bars],
            "attempts": [dict(row) for row in self.attempts],
        }


@dataclass(frozen=True, slots=True)
class HistoricalSeriesSummary:
    """Compact metadata retained after raw bars have entered the replay engine."""

    symbol: str
    source: str
    adjustment: str
    bar_count: int
    first_date: str
    last_date: str
    attempts: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_series(cls, series: HistoricalSeries) -> HistoricalSeriesSummary:
        bars = series.bars
        return cls(
            symbol=series.symbol,
            source=series.source,
            adjustment=series.adjustment,
            bar_count=len(bars),
            first_date=str(bars[0].get("date") or "") if bars else "",
            last_date=str(bars[-1].get("date") or "") if bars else "",
            attempts=series.attempts,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "adjustment": self.adjustment,
            "bar_count": self.bar_count,
            "first_date": self.first_date,
            "last_date": self.last_date,
            "attempts": [dict(row) for row in self.attempts],
        }


@dataclass(frozen=True, slots=True)
class HistoricalDataSummary:
    """Historical fetch metadata that does not retain raw per-session rows."""

    series: Mapping[str, HistoricalSeriesSummary]
    failures: Mapping[str, str] = field(default_factory=dict)

    @property
    def source_by_symbol(self) -> Mapping[str, str]:
        return MappingProxyType({
            symbol: value.source for symbol, value in self.series.items()
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "series": {symbol: value.to_dict() for symbol, value in self.series.items()},
            "failures": dict(self.failures),
        }


@dataclass(frozen=True, slots=True)
class HistoricalDataResult:
    series: Mapping[str, HistoricalSeries]
    failures: Mapping[str, str] = field(default_factory=dict)

    @property
    def bars_by_symbol(self) -> Mapping[str, tuple[Mapping[str, Any], ...]]:
        return MappingProxyType({symbol: value.bars for symbol, value in self.series.items()})

    @property
    def source_by_symbol(self) -> Mapping[str, str]:
        return MappingProxyType({symbol: value.source for symbol, value in self.series.items()})

    def summary(self) -> HistoricalDataSummary:
        """Detach compact fetch metadata so callers can release raw bars early."""
        return HistoricalDataSummary(
            series=MappingProxyType({
                symbol: HistoricalSeriesSummary.from_series(value)
                for symbol, value in self.series.items()
            }),
            failures=MappingProxyType(dict(self.failures)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "series": {symbol: value.to_dict() for symbol, value in self.series.items()},
            "failures": dict(self.failures),
        }


def _validated_range(
    start_date: Any,
    end_date: Any,
    config: HistoricalFetchConfig,
) -> tuple[str, str]:
    start = _date_text(start_date, field_name="start_date")
    end = _date_text(end_date, field_name="end_date")
    start_value = datetime.strptime(start, "%Y-%m-%d").date()
    end_value = datetime.strptime(end, "%Y-%m-%d").date()
    if start_value > end_value:
        raise HistoricalDataError("start_date cannot be after end_date")
    if (end_value - start_value).days + 1 > int(config.max_calendar_days):
        raise HistoricalDataError("requested historical range exceeds max_calendar_days")
    return start, end


def _error_text(exc: Exception) -> str:
    text = re.sub(r"https?://\S+", "<url>", str(exc or "")).strip()
    return f"{type(exc).__name__}: {text[:200]}" if text else type(exc).__name__


def _is_retryable_source_error(exc: Exception) -> bool:
    """Retry transport failures, but not deterministic source/data rejections."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 425, 429} or 500 <= exc.code < 600
    if isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError)):
        return True
    if isinstance(exc, HistoricalDataError):
        text = str(exc or "").lower()
        return "invalid json" in text or "invalid jsonp" in text
    if isinstance(exc, OSError):
        return True
    # Preserve retry compatibility for custom fetchers with their own transient
    # exception types. Built-in deterministic failures use HistoricalDataError.
    return True


class _RunSourceHealth:
    """Share conservative source health across one automatic multi-source run."""

    def __init__(self, config: HistoricalFetchConfig) -> None:
        self._lock = threading.Lock()
        self._skippable = frozenset(config.sources[:-1])
        self._minimum_samples = int(config.source_circuit_min_samples)
        self._failure_ratio = float(config.source_circuit_failure_ratio)
        self._samples: dict[str, int] = {}
        self._failures: dict[str, int] = {}
        self._open: set[str] = set()

    def should_skip(self, source: str) -> bool:
        with self._lock:
            return source in self._open

    def record_success(self, source: str) -> None:
        with self._lock:
            self._samples[source] = self._samples.get(source, 0) + 1

    def record_failure(self, source: str) -> None:
        if source not in self._skippable:
            return
        with self._lock:
            samples = self._samples.get(source, 0) + 1
            failures = self._failures.get(source, 0) + 1
            self._samples[source] = samples
            self._failures[source] = failures
            if (
                samples >= self._minimum_samples
                and failures / samples >= self._failure_ratio
            ):
                self._open.add(source)


def fetch_historical_series(
    symbol: str,
    start_date: Any,
    end_date: Any,
    *,
    config: HistoricalFetchConfig | None = None,
    source_fetchers: Mapping[str, SourceFetcher] | None = None,
    _source_health: _RunSourceHealth | None = None,
) -> HistoricalSeries:
    """Fetch one complete series using bounded retries and source fallback."""
    resolved = config or HistoricalFetchConfig()
    start, end = _validated_range(start_date, end_date, resolved)
    normalized = normalize_a_share_symbol(symbol)
    fetchers = dict(DEFAULT_SOURCE_FETCHERS)
    fetchers.update(source_fetchers or {})
    attempts: list[Mapping[str, Any]] = []
    for source in resolved.sources:
        if _source_health is not None and _source_health.should_skip(source):
            attempts.append(MappingProxyType({
                "source": source,
                "attempt": 0,
                "error": "run_source_circuit_open",
            }))
            continue
        if source == "sina" and resolved.adjustment != "none":
            attempts.append(MappingProxyType({
                "source": source,
                "attempt": 0,
                "error": "unsupported_adjustment",
            }))
            continue
        fetcher = fetchers.get(source)
        if fetcher is None:
            attempts.append(MappingProxyType({
                "source": source,
                "attempt": 0,
                "error": "fetcher_not_configured",
            }))
            continue
        for attempt in range(1, int(resolved.max_attempts_per_source) + 1):
            try:
                raw_rows = fetcher(
                    normalized,
                    start,
                    end,
                    resolved.adjustment,
                    float(resolved.timeout_seconds),
                )
                rows = _normalize_rows(
                    raw_rows,
                    symbol=normalized,
                    start_date=start,
                    end_date=end,
                    source=source,
                    adjustment=resolved.adjustment,
                )
                if len(rows) < int(resolved.minimum_rows):
                    raise HistoricalDataError(
                        f"source returned {len(rows)} rows; minimum is {resolved.minimum_rows}"
                    )
                if _source_health is not None:
                    _source_health.record_success(source)
                return HistoricalSeries(
                    symbol=normalized,
                    source=source,
                    adjustment=resolved.adjustment,
                    bars=tuple(_immutable_historical_row(row) for row in rows),
                    attempts=tuple(attempts),
                )
            except Exception as exc:
                if _source_health is not None:
                    _source_health.record_failure(source)
                attempts.append(MappingProxyType({
                    "source": source,
                    "attempt": attempt,
                    "error": _error_text(exc),
                }))
                should_retry = (
                    attempt < int(resolved.max_attempts_per_source)
                    and _is_retryable_source_error(exc)
                    and not (
                        _source_health is not None
                        and _source_health.should_skip(source)
                    )
                )
                if should_retry:
                    delay = float(resolved.retry_backoff_seconds) * (2 ** (attempt - 1))
                    if delay > 0:
                        time.sleep(delay)
                else:
                    break
    summary = "; ".join(
        f"{row['source']}#{row['attempt']} {row['error']}" for row in attempts
    )
    raise HistoricalDataError(f"failed to fetch {normalized}: {summary}")


def fetch_historical_data(
    symbols: Iterable[str],
    start_date: Any,
    end_date: Any,
    *,
    config: HistoricalFetchConfig | None = None,
    source_fetchers: Mapping[str, SourceFetcher] | None = None,
    progress_callback: FetchProgress | None = None,
    cancellation_check: Callable[[], None] | None = None,
) -> HistoricalDataResult:
    """Fetch an explicit universe without silently hiding missing symbols."""
    resolved = config or HistoricalFetchConfig()
    start, end = _validated_range(start_date, end_date, resolved)
    normalized_symbols = tuple(dict.fromkeys(
        normalize_a_share_symbol(symbol) for symbol in symbols
    ))
    if not normalized_symbols:
        raise HistoricalDataError("at least one symbol is required")
    series: dict[str, HistoricalSeries] = {}
    failures: dict[str, str] = {}
    source_health = _RunSourceHealth(resolved) if len(resolved.sources) > 1 else None

    def fetch_one(symbol: str) -> HistoricalSeries:
        return fetch_historical_series(
            symbol,
            start,
            end,
            config=resolved,
            source_fetchers=source_fetchers,
            _source_health=source_health,
        )

    workers = min(int(resolved.max_workers), len(normalized_symbols))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    completed_normally = False
    try:
        futures = {executor.submit(fetch_one, symbol): symbol for symbol in normalized_symbols}
        pending = set(futures)
        completed = 0
        while pending:
            if cancellation_check is not None:
                cancellation_check()
            done, pending = concurrent.futures.wait(
                pending,
                timeout=0.2,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                symbol = futures.pop(future)
                succeeded = False
                try:
                    series[symbol] = future.result()
                    succeeded = True
                except Exception as exc:
                    failures[symbol] = _error_text(exc)
                completed += 1
                if progress_callback is not None:
                    progress_callback(
                        completed,
                        len(normalized_symbols),
                        symbol,
                        succeeded,
                    )
        completed_normally = True
    finally:
        executor.shutdown(
            wait=completed_normally,
            cancel_futures=not completed_normally,
        )
    ordered_series = {
        symbol: series[symbol] for symbol in normalized_symbols if symbol in series
    }
    ordered_failures = {
        symbol: failures[symbol] for symbol in normalized_symbols if symbol in failures
    }
    if ordered_failures and resolved.strict:
        raise HistoricalDataError(
            "historical universe is incomplete: "
            + ", ".join(f"{symbol} ({error})" for symbol, error in ordered_failures.items())
        )
    return HistoricalDataResult(
        series=MappingProxyType(ordered_series),
        failures=MappingProxyType(ordered_failures),
    )


__all__ = [
    "HistoricalDataError",
    "HistoricalDataResult",
    "HistoricalDataSummary",
    "HistoricalFetchConfig",
    "HistoricalSeries",
    "HistoricalSeriesSummary",
    "FetchProgress",
    "DEFAULT_HISTORICAL_SOURCE_PRIORITY",
    "SUPPORTED_ADJUSTMENTS",
    "SUPPORTED_HISTORICAL_SOURCES",
    "SourceFetcher",
    "fetch_historical_data",
    "fetch_historical_series",
    "normalize_a_share_symbol",
    "parse_eastmoney_daily_klines",
    "parse_sina_daily_klines",
    "parse_tencent_daily_klines",
]
