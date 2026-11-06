"""Durable Tencent daily-K-line cache used by full-market scans.

The cache stores one compact JSON series per symbol in SQLite.  A pre-market
refresh replaces only successfully downloaded symbols, so transient upstream
failures never delete the latest valid local history.
"""
from __future__ import annotations

import concurrent.futures
import json
import math
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

try:
    from app.core.paths import get_dashboard_home
except ImportError:  # pragma: no cover - legacy top-level import path
    from core.paths import get_dashboard_home


SCHEMA_VERSION = 2
DEFAULT_KLINE_COUNT = 120
MAX_KLINE_COUNT = 500
MAX_KLINE_MERGED_COUNT = MAX_KLINE_COUNT + 1
DEFAULT_PREWARM_WORKERS = 12
DEFAULT_HTTP_TIMEOUT_SECONDS = 15.0
TENCENT_KLINE_URLS = (
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
    "https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get",
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
)
TENCENT_KLINE_URL = TENCENT_KLINE_URLS[0]
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_PATH = get_dashboard_home(PROJECT_ROOT) / "market_data" / "tencent_daily_klines.sqlite3"


def kline_cache_path() -> Path:
    """Return the private runtime cache path, allowing an explicit override."""
    return Path(
        os.environ.get("DASHBOARD_KLINE_CACHE_DB") or DEFAULT_CACHE_PATH
    ).expanduser()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS kline_series (
            symbol TEXT PRIMARY KEY,
            adjustment TEXT NOT NULL,
            first_trade_date TEXT NOT NULL,
            last_trade_date TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            rows_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            updated_ts REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_kline_series_last_date
        ON kline_series(last_trade_date);

        CREATE TABLE IF NOT EXISTS kline_attempts (
            symbol TEXT PRIMARY KEY,
            attempted_at TEXT NOT NULL,
            error_code TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS prewarm_runs (
            target_date TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT '',
            requested_count INTEGER NOT NULL DEFAULT 0,
            completed_count INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            duration_seconds REAL NOT NULL DEFAULT 0,
            error_summary TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS prewarm_targets (
            target_date TEXT NOT NULL, symbol TEXT NOT NULL,
            PRIMARY KEY (target_date, symbol)
        );
        """
    )
    prewarm_columns = {
        str(row["name"] or "")
        for row in connection.execute("PRAGMA table_info(prewarm_runs)")
    }
    schema_row = connection.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()
    if (
        "completed_count" not in prewarm_columns
        or "updated_at" not in prewarm_columns
        or not schema_row
        or str(schema_row["value"] or "") != str(SCHEMA_VERSION)
    ):
        connection.execute("BEGIN IMMEDIATE")
        try:
            prewarm_columns = {
                str(row["name"] or "")
                for row in connection.execute("PRAGMA table_info(prewarm_runs)")
            }
            if "completed_count" not in prewarm_columns:
                connection.execute(
                    "ALTER TABLE prewarm_runs ADD COLUMN completed_count INTEGER NOT NULL DEFAULT 0"
                )
            if "updated_at" not in prewarm_columns:
                connection.execute(
                    "ALTER TABLE prewarm_runs ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise
    return connection


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_kline_rows(rows: Iterable[Mapping[str, Any]], *, limit: int = DEFAULT_KLINE_COUNT) -> list[dict[str, Any]]:
    """Validate, deduplicate and order a bounded daily series."""
    by_date: dict[str, dict[str, Any]] = {}
    for raw in rows or []:
        if not isinstance(raw, Mapping):
            continue
        matched = re.search(r"\d{4}-\d{2}-\d{2}", str(raw.get("date") or ""))
        if not matched:
            continue
        values = {
            key: _finite_float(raw.get(key))
            for key in ("open", "close", "high", "low", "volume")
        }
        if any(values[key] is None for key in ("open", "close", "high", "low")):
            continue
        if any(float(values[key] or 0) <= 0 for key in ("open", "close", "high", "low")):
            continue
        by_date[matched.group(0)] = {
            "date": matched.group(0),
            "open": float(values["open"] or 0),
            "close": float(values["close"] or 0),
            "high": float(values["high"] or 0),
            "low": float(values["low"] or 0),
            "volume": max(0.0, float(values["volume"] or 0)),
        }
    ordered = [by_date[key] for key in sorted(by_date)]
    return ordered[-max(1, int(limit or DEFAULT_KLINE_COUNT)):]


def _unit_factor_dates(raw: bytes, symbol: str) -> tuple[str, ...]:
    """Accept only a complete, security-bound forward-factor history of exact ones."""
    if not raw or len(raw) > 256 * 1024 or not re.fullmatch(r"(sh|sz)\d{6}", symbol):
        return ()
    try:
        text = raw.decode("utf-8")
        prefix = re.match(r"\s*var\s+" + re.escape(symbol) + r"qfq\s*=\s*", text)
        if not prefix:
            return ()
        data, end = json.JSONDecoder().raw_decode(text, prefix.end())
        tail = text[end:].strip().removeprefix(";").strip()
        if tail and not (tail.startswith("/*") and tail.find("*/") == len(tail) - 2):
            return ()
        rows = data["data"]
        if not isinstance(rows,list) or type(data["total"]) is not int or not 2 <= len(rows) == data["total"] <= 4096:
            return ()
        dates = []
        for row in rows:
            factor, stamp = row["f"], row["d"]
            if not isinstance(factor,str) or not re.fullmatch(r"\d{1,20}(?:\.\d{1,40})?",factor) or Decimal(factor) != 1:
                return ()
            if not isinstance(stamp,str) or date.fromisoformat(stamp).isoformat()!=stamp:
                return ()
            dates.append(stamp)
        if dates != sorted(set(dates), reverse=True) or dates[-1] != "1900-01-01":
            return ()
        return tuple(dates)
    except (UnicodeError, ValueError, TypeError, KeyError):
        return ()


def _fetch_unit_factor_dates(symbol: str, timeout_seconds: float) -> tuple[str, ...]:
    req = urllib.request.Request(
        f"https://finance.sina.com.cn/realstock/company/{symbol}/qfq.js",
        headers={"Referer": "https://finance.sina.com.cn/", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=min(6.0,max(1.0,timeout_seconds))) as response:
            return _unit_factor_dates(response.read(256*1024+1), symbol)
    except (OSError, ValueError):
        return ()


def fetch_tencent_daily_klines(
    symbol: str,
    count: int = DEFAULT_KLINE_COUNT,
    *,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Fetch one bounded qfq daily series through Tencent's bounded aliases."""
    normalized_symbol = re.sub(r"[^a-zA-Z0-9]", "", str(symbol or "")).lower()
    if not re.fullmatch(r"(?:sh|sz)\d{6}", normalized_symbol):
        return []
    bounded_count = max(
        1,
        min(MAX_KLINE_MERGED_COUNT, int(count or DEFAULT_KLINE_COUNT)),
    )
    query = urllib.parse.urlencode({
        "param": f"{normalized_symbol},day,,,{bounded_count},qfq",
    })
    unit_dates: tuple[str, ...] | None = None
    for base_url in TENCENT_KLINE_URLS:
        request = urllib.request.Request(
            f"{base_url}?{query}",
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://gu.qq.com/",
                "Connection": "close",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=max(1.0, float(timeout_seconds)),
            ) as response:
                payload = json.loads(response.read().decode("utf-8", "ignore"))
        except (OSError, TypeError, ValueError):
            continue
        if not isinstance(payload, Mapping):
            continue
        data = payload.get("data")
        symbol_payload = data.get(normalized_symbol) if isinstance(data, Mapping) else None
        if not isinstance(symbol_payload, Mapping):
            continue
        raw_rows = symbol_payload.get("qfqday")
        generic = not raw_rows
        if generic:
            raw_rows = symbol_payload.get("day")
            if not raw_rows or payload.get("code",0) != 0 or symbol_payload.get("hfqday"):
                continue
        parsed = []
        for item in raw_rows or []:
            if not isinstance(item, list) or len(item) < 6:
                continue
            parsed.append({
                "date": item[0],
                "open": item[1],
                "close": item[2],
                "high": item[3],
                "low": item[4],
                "volume": item[5],
            })
        normalized = normalize_kline_rows(parsed, limit=bounded_count)
        if not normalized:
            continue
        if generic:
            # Tencent can label unchanged prices "day" even on a qfq request.
            # Use the original numbers only with an independent identity proof.
            if unit_dates is None:
                unit_dates = _fetch_unit_factor_dates(normalized_symbol,timeout_seconds)
            if not unit_dates or unit_dates[-2] > normalized[0]["date"] or unit_dates[0] > normalized[-1]["date"]:
                continue
        return normalized
    return []


def fetch_eastmoney_daily_klines(
    symbol: str,
    count: int = DEFAULT_KLINE_COUNT,
    *,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Fetch one bounded qfq daily series from Eastmoney."""
    normalized_symbol = re.sub(r"[^a-zA-Z0-9]", "", str(symbol or "")).lower()
    if not re.fullmatch(r"(?:sh|sz)\d{6}", normalized_symbol):
        return []
    bounded_count = max(
        1,
        min(MAX_KLINE_MERGED_COUNT, int(count or DEFAULT_KLINE_COUNT)),
    )
    market = "1" if normalized_symbol.startswith("sh") else "0"
    query = urllib.parse.urlencode({
        "secid": f"{market}.{normalized_symbol[-6:]}",
        "klt": "101",
        "fqt": "1",
        "lmt": str(bounded_count),
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    })
    request = urllib.request.Request(
        f"{EASTMONEY_KLINE_URL}?{query}",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://quote.eastmoney.com/",
            "Connection": "close",
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=max(1.0, float(timeout_seconds)),
        ) as response:
            payload = json.loads(response.read().decode("utf-8", "ignore"))
        data = payload.get("data") if isinstance(payload, Mapping) else None
        raw_rows = data.get("klines") if isinstance(data, Mapping) else None
    except (OSError, TypeError, ValueError):
        return []
    parsed = []
    for item in raw_rows or []:
        fields = str(item or "").split(",")
        if len(fields) < 6:
            continue
        parsed.append({
            "date": fields[0],
            "open": fields[1],
            "close": fields[2],
            "high": fields[3],
            "low": fields[4],
            "volume": fields[5],
        })
    return normalize_kline_rows(parsed, limit=bounded_count)


class IncompleteDailySeries(ValueError):
    """All responding providers returned too few rows or an out-of-window date."""


def fetch_a_share_daily_klines(
    symbol: str,
    count: int = DEFAULT_KLINE_COUNT,
    *,
    accepted_last_dates: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Select the first same-adjustment source that also meets the warmup window."""
    incomplete = False
    for fetcher in (fetch_tencent_daily_klines, fetch_eastmoney_daily_klines):
        rows = fetcher(symbol, count)
        if rows:
            if accepted_last_dates and (len(rows) < 30 or rows[-1]["date"] not in accepted_last_dates):
                incomplete = True
                continue
            return rows
    if incomplete:
        raise IncompleteDailySeries("insufficient_or_stale_series")
    return []


def store_kline_series(
    series_by_symbol: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    path: Path | None = None,
    fetched_at: str = "",
) -> int:
    """Atomically replace successful symbol series and retain all others."""
    normalized: list[tuple[Any, ...]] = []
    timestamp = fetched_at or _now_text()
    updated_ts = time.time()
    for raw_symbol, raw_rows in (series_by_symbol or {}).items():
        symbol = re.sub(r"[^a-zA-Z0-9]", "", str(raw_symbol or "")).lower()
        if not re.fullmatch(r"(?:sh|sz)\d{6}", symbol):
            continue
        rows = normalize_kline_rows(raw_rows, limit=MAX_KLINE_COUNT)
        if not rows:
            continue
        normalized.append((
            symbol,
            "qfq",
            str(rows[0]["date"]),
            str(rows[-1]["date"]),
            len(rows),
            json.dumps(rows, ensure_ascii=False, separators=(",", ":")),
            timestamp,
            updated_ts,
        ))
    if not normalized:
        return 0
    connection = _open_database(Path(path or kline_cache_path()))
    try:
        with connection:
            connection.executemany(
                """
                INSERT INTO kline_series(
                    symbol, adjustment, first_trade_date, last_trade_date,
                    row_count, rows_json, fetched_at, updated_ts
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    adjustment=excluded.adjustment,
                    first_trade_date=excluded.first_trade_date,
                    last_trade_date=excluded.last_trade_date,
                    row_count=excluded.row_count,
                    rows_json=excluded.rows_json,
                    fetched_at=excluded.fetched_at,
                    updated_ts=excluded.updated_ts
                """,
                normalized,
            )
            connection.executemany(
                "DELETE FROM kline_attempts WHERE symbol=?",
                [(row[0],) for row in normalized],
            )
    finally:
        connection.close()
    return len(normalized)


def record_kline_failures(
    failures: Mapping[str, str],
    *,
    path: Path | None = None,
    attempted_at: str = "",
) -> None:
    """Record bounded diagnostics without overwriting previously valid rows."""
    rows = [
        (
            re.sub(r"[^a-zA-Z0-9]", "", str(symbol or "")).lower(),
            attempted_at or _now_text(),
            re.sub(r"[^a-zA-Z0-9_.-]", "", str(error or "unavailable"))[:80] or "unavailable",
        )
        for symbol, error in (failures or {}).items()
    ]
    rows = [row for row in rows if re.fullmatch(r"(?:sh|sz)\d{6}", row[0])]
    if not rows:
        return
    connection = _open_database(Path(path or kline_cache_path()))
    try:
        with connection:
            connection.executemany(
                """
                INSERT INTO kline_attempts(symbol, attempted_at, error_code)
                VALUES(?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    attempted_at=excluded.attempted_at,
                    error_code=excluded.error_code
                """,
                rows,
            )
    finally:
        connection.close()


def load_kline_series_map(
    symbols: Iterable[str],
    *,
    path: Path | None = None,
    accepted_last_dates: set[str] | None = None,
    min_rows: int = 30,
    count: int = DEFAULT_KLINE_COUNT,
) -> dict[str, list[dict[str, Any]]]:
    """Bulk-load fresh cached histories with one SQLite read per symbol chunk."""
    unique = list(dict.fromkeys(
        re.sub(r"[^a-zA-Z0-9]", "", str(symbol or "")).lower()
        for symbol in symbols
    ))
    unique = [symbol for symbol in unique if re.fullmatch(r"(?:sh|sz)\d{6}", symbol)]
    cache_path = Path(path or kline_cache_path())
    if not unique or not cache_path.exists():
        return {}
    accepted = {str(value)[:10] for value in (accepted_last_dates or set()) if str(value)[:10]}
    result: dict[str, list[dict[str, Any]]] = {}
    connection = _open_database(cache_path)
    try:
        for offset in range(0, len(unique), 800):
            chunk = unique[offset:offset + 800]
            placeholders = ",".join("?" for _ in chunk)
            query = (
                "SELECT symbol, last_trade_date, row_count, rows_json "
                f"FROM kline_series WHERE symbol IN ({placeholders})"
            )
            for row in connection.execute(query, chunk):
                if int(row["row_count"] or 0) < max(1, int(min_rows or 1)):
                    continue
                if accepted and str(row["last_trade_date"] or "")[:10] not in accepted:
                    continue
                try:
                    parsed = json.loads(str(row["rows_json"] or "[]"))
                except (TypeError, ValueError):
                    continue
                normalized_rows = normalize_kline_rows(parsed, limit=count)
                if len(normalized_rows) >= max(1, int(min_rows or 1)):
                    result[str(row["symbol"])] = normalized_rows
    finally:
        connection.close()
    return result


def load_cached_kline_symbols(
    *,
    path: Path | None = None,
    min_rows: int = 1,
) -> tuple[str, ...]:
    """Return cached symbols without loading their JSON histories."""
    cache_path = Path(path or kline_cache_path())
    if not cache_path.exists():
        return ()
    connection = _open_database(cache_path)
    try:
        rows = connection.execute(
            "SELECT symbol FROM kline_series WHERE row_count >= ? ORDER BY symbol",
            (max(1, int(min_rows or 1)),),
        )
        return tuple(str(row["symbol"] or "") for row in rows if row["symbol"])
    finally:
        connection.close()


def quote_trade_date(quote: Mapping[str, Any] | None) -> str:
    matched = re.search(
        r"(?P<year>\d{4})[-/]?(?P<month>\d{2})[-/]?(?P<day>\d{2})",
        str((quote or {}).get("quote_time") or ""),
    )
    if not matched:
        return ""
    return f"{matched.group('year')}-{matched.group('month')}-{matched.group('day')}"


def merge_live_quote(
    historical_rows: Iterable[Mapping[str, Any]],
    quote: Mapping[str, Any] | None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Append or replace today's bar without mutating cached completed history."""
    source_rows = list(historical_rows)
    row_limit = (
        DEFAULT_KLINE_COUNT
        if limit is None
        else max(1, min(MAX_KLINE_MERGED_COUNT, int(limit)))
    )
    rows = normalize_kline_rows(source_rows, limit=row_limit)
    trade_date = quote_trade_date(quote)
    price = _finite_float((quote or {}).get("price"))
    if not trade_date or price is None or price <= 0:
        return rows
    open_price = _finite_float((quote or {}).get("open")) or price
    high = _finite_float((quote or {}).get("high")) or price
    low = _finite_float((quote or {}).get("low")) or price
    volume = _finite_float((quote or {}).get("volume")) or 0.0
    live = {
        "date": trade_date,
        "bar_status": "live",
        "observed_at": str((quote or {}).get("quote_time") or ""),
        "open": open_price,
        "close": price,
        "high": max(high, open_price, price),
        "low": min(low, open_price, price),
        "volume": max(0.0, volume),
    }
    if rows and rows[-1]["date"] == trade_date:
        rows[-1] = live
    else:
        rows.append(live)
    return rows[-row_limit:]


def prewarm_completed_for_date(
    target_date: str,
    *,
    path: Path | None = None,
    minimum_coverage: float = 0.90,
) -> bool:
    cache_path = Path(path or kline_cache_path())
    if not cache_path.exists():
        return False
    connection = _open_database(cache_path)
    try:
        row = connection.execute(
            "SELECT requested_count, success_count, status, "
            "(SELECT count(*) FROM prewarm_targets t WHERE t.target_date=r.target_date) AS targets "
            "FROM prewarm_runs r WHERE target_date=?",
            (str(target_date)[:10],),
        ).fetchone()
    finally:
        connection.close()
    if not row or str(row["status"] or "") != "completed":
        return False
    requested = int(row["requested_count"] or 0)
    success = int(row["success_count"] or 0)
    return requested > 0 and row['targets'] == requested and success / requested >= max(0.0, min(1.0, minimum_coverage))


def kline_cache_readiness(
    *,
    accepted_last_dates: set[str] | None = None,
    path: Path | None = None,
    minimum_coverage: float = 0.90,
    min_rows: int = 30,
) -> dict[str, Any]:
    """Return bounded cache and prewarm progress metadata for readiness gates."""
    cache_path = Path(path or kline_cache_path())
    accepted = {
        str(value)[:10]
        for value in (accepted_last_dates or set())
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)[:10])
    }
    base = {
        "ready": False,
        "cache_exists": cache_path.exists(),
        "accepted_last_dates": sorted(accepted),
        "cached_count": 0,
        "fresh_count": 0,
        "requested_count": 0,
        "completed_count": 0,
        "success_count": 0,
        "failure_count": 0,
        "coverage": 0.0,
        "status": "missing",
        "target_date": "",
        "started_at": "",
        "finished_at": "",
        "updated_at": "",
        "duration_seconds": 0.0,
        "error_code": "kline_cache_missing",
    }
    if not cache_path.exists():
        return base
    try:
        connection = _open_database(cache_path)
        try:
            cached_count = int(connection.execute(
                "SELECT count(*) FROM kline_series WHERE row_count >= ?",
                (max(1, int(min_rows or 1)),),
            ).fetchone()[0] or 0)
            if accepted:
                placeholders = ",".join("?" for _ in accepted)
                fresh_count = int(connection.execute(
                    "SELECT count(*) FROM kline_series "
                    f"WHERE row_count >= ? AND last_trade_date IN ({placeholders})",
                    (max(1, int(min_rows or 1)), *sorted(accepted)),
                ).fetchone()[0] or 0)
            else:
                fresh_count = cached_count
            run = connection.execute(
                "SELECT target_date, started_at, finished_at, requested_count, "
                "completed_count, success_count, failure_count, status, "
                "duration_seconds, error_summary, updated_at "
                "FROM prewarm_runs ORDER BY target_date DESC, started_at DESC LIMIT 1"
            ).fetchone()
            manifest_count = 0
            if run:
                run_day = run['target_date']
                manifest_count = connection.execute('SELECT count(*) FROM prewarm_targets WHERE target_date=?', (run_day,)).fetchone()[0]
                if manifest_count:
                    date_clause = (f" AND s.last_trade_date IN ({placeholders})" if accepted else '')
                    if accepted:
                        date_clause += " AND (s.last_trade_date=? OR s.fetched_at >= s.last_trade_date || ' 15:00:00')"
                    fresh_count = connection.execute(
                        'SELECT count(*) FROM prewarm_targets t JOIN kline_series s ON s.symbol=t.symbol '
                        'WHERE t.target_date=? AND s.row_count>=?' + date_clause,
                        (run_day,max(1,int(min_rows or 1)),*sorted(accepted),*([max(run_day,*accepted)] if accepted else [])),
                    ).fetchone()[0]
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as exc:
        return {
            **base,
            "cache_exists": True,
            "status": "error",
            "error_code": f"kline_cache_{type(exc).__name__.lower()}",
        }

    requested_count = int(run["requested_count"] or 0) if run else 0
    completed_count = int(run["completed_count"] or 0) if run else 0
    success_count = int(run["success_count"] or 0) if run else 0
    failure_count = int(run["failure_count"] or 0) if run else 0
    denominator = manifest_count or max(requested_count, cached_count)
    coverage = fresh_count / denominator if denominator > 0 else 0.0
    ready = (denominator > 0 and manifest_count == requested_count and manifest_count > 0
             and coverage >= max(0.0, min(1.0, minimum_coverage)))
    status = str(run["status"] or "unknown") if run else "untracked"
    if ready:
        error_code = ""
    elif status == "running":
        error_code = "kline_cache_initializing"
    elif status == "error":
        error_code = str(run["error_summary"] or "kline_prewarm_failed")[:80]
    else:
        error_code = "kline_cache_incomplete"
    return {
        **base,
        "ready": ready,
        "cache_exists": True,
        "cached_count": cached_count,
        "fresh_count": fresh_count,
        "requested_count": requested_count,
        "completed_count": completed_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "coverage": round(coverage, 4),
        "status": status,
        "target_date": str(run["target_date"] or "") if run else "",
        "started_at": str(run["started_at"] or "") if run else "",
        "finished_at": str(run["finished_at"] or "") if run else "",
        "updated_at": str(run["updated_at"] or "") if run else "",
        "duration_seconds": float(run["duration_seconds"] or 0.0) if run else 0.0,
        "error_code": error_code,
    }


def mark_prewarm_run_failed(
    target_date: str,
    error_code: str,
    *,
    path: Path | None = None,
) -> None:
    """Persist a terminal aggregate failure without deleting usable K-line rows."""
    cache_path = Path(path or kline_cache_path())
    now_text = _now_text()
    connection = _open_database(cache_path)
    try:
        with connection:
            connection.execute(
                "INSERT INTO prewarm_runs("
                "target_date, started_at, finished_at, status, error_summary, updated_at"
                ") VALUES(?, ?, ?, 'error', ?, ?) "
                "ON CONFLICT(target_date) DO UPDATE SET "
                "status='error', finished_at=excluded.finished_at, "
                "updated_at=excluded.updated_at, error_summary=excluded.error_summary",
                (
                    str(target_date)[:10],
                    now_text,
                    now_text,
                    re.sub(r"[^a-zA-Z0-9_.-]", "", str(error_code or "prewarm_failed"))[:80],
                    now_text,
                ),
            )
    finally:
        connection.close()


def prewarm_kline_cache(
    symbols: Iterable[str],
    *,
    path: Path | None = None,
    target_date: str = "",
    workers: int = DEFAULT_PREWARM_WORKERS,
    count: int = DEFAULT_KLINE_COUNT,
    max_attempts: int = 2,
    accepted_last_dates: set[str] | None = None,
    fetcher: Callable[[str, int], list[dict[str, Any]]] | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> dict[str, Any]:
    """Refresh all symbols concurrently and commit successes in bounded batches."""
    unique = list(dict.fromkeys(
        re.sub(r"[^a-zA-Z0-9]", "", str(symbol or "")).lower()
        for symbol in symbols
    ))
    unique = [symbol for symbol in unique if re.fullmatch(r"(?:sh|sz)\d{6}", symbol)]
    cache_path = Path(path or kline_cache_path())
    run_date = str(target_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    started_at = _now_text()
    started = time.monotonic()
    active_fetcher = fetcher or fetch_a_share_daily_klines
    accepted = {
        str(value)[:10]
        for value in (accepted_last_dates or set())
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)[:10])
    }
    fresh_symbols = set()
    if accepted and cache_path.exists():
        conn = _open_database(cache_path)
        try:
            placeholders = ','.join('?' for _ in accepted)
            fresh_symbols = set(unique).intersection(row[0] for row in conn.execute(
                'SELECT symbol FROM kline_series WHERE row_count>=30 '
                f'AND last_trade_date IN ({placeholders}) '
                "AND (last_trade_date=? OR fetched_at >= last_trade_date || ' 15:00:00')",
                (*sorted(accepted), run_date)))
        finally:
            conn.close()
    pending_symbols = [symbol for symbol in unique if symbol not in fresh_symbols]
    worker_count = max(
        1,
        min(16, int(workers or DEFAULT_PREWARM_WORKERS), len(pending_symbols) or 1),
    )
    completed = len(fresh_symbols)
    successes = len(fresh_symbols)
    connection = _open_database(cache_path)
    try:
        with connection:
            connection.execute('DELETE FROM prewarm_targets WHERE target_date=?', (run_date,))
            connection.executemany('INSERT INTO prewarm_targets(target_date,symbol) VALUES(?,?)', ((run_date,s) for s in unique))
            connection.execute('DELETE FROM prewarm_targets WHERE target_date NOT IN '
                               '(SELECT DISTINCT target_date FROM prewarm_targets ORDER BY target_date DESC LIMIT 5)')
            connection.execute(
                """
                INSERT INTO prewarm_runs(
                    target_date, started_at, requested_count, completed_count,
                    success_count, status, updated_at
                ) VALUES(?, ?, ?, ?, ?, 'running', ?)
                ON CONFLICT(target_date) DO UPDATE SET
                    started_at=excluded.started_at,
                    finished_at='',
                    requested_count=excluded.requested_count,
                    completed_count=excluded.completed_count,
                    success_count=excluded.success_count,
                    failure_count=0,
                    status='running',
                    duration_seconds=0,
                    error_summary='',
                    updated_at=excluded.updated_at
                """,
                (run_date, started_at, len(unique), completed, successes, started_at),
            )
    finally:
        connection.close()

    def fetch_one(symbol: str) -> tuple[str, list[dict[str, Any]], str]:
        last_error = "empty_response"
        for attempt in range(max(1, int(max_attempts or 1))):
            try:
                raw = (fetch_a_share_daily_klines(symbol, count, accepted_last_dates=accepted)
                       if fetcher is None and accepted else active_fetcher(symbol, count))
                rows = normalize_kline_rows(raw, limit=count)
            except IncompleteDailySeries:
                rows = []
                last_error = 'insufficient_or_stale_series'
            except Exception as exc:
                rows = []
                last_error = type(exc).__name__
            if rows and accepted and (len(rows) < 30 or rows[-1]['date'] not in accepted):
                rows = []
                last_error = 'insufficient_or_stale_series'
            if rows:
                return symbol, rows, ""
            if attempt + 1 < max_attempts:
                time.sleep(0.15 * (attempt + 1))
        return symbol, [], last_error

    failures: dict[str, str] = {}
    pending: dict[str, list[dict[str, Any]]] = {}

    def persist_progress() -> None:
        progress_connection = _open_database(cache_path)
        try:
            with progress_connection:
                progress_connection.execute(
                    "UPDATE prewarm_runs SET completed_count=?, success_count=?, "
                    "failure_count=?, updated_at=? WHERE target_date=?",
                    (completed, successes, len(failures), _now_text(), run_date),
                )
        finally:
            progress_connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(fetch_one, symbol) for symbol in pending_symbols]
        for future in concurrent.futures.as_completed(futures):
            symbol, rows, error = future.result()
            completed += 1
            if rows:
                pending[symbol] = rows
            else:
                failures[symbol] = error or "unavailable"
            if len(pending) >= 100:
                successes += store_kline_series(pending, path=cache_path, fetched_at=started_at)
                pending.clear()
                persist_progress()
            if progress and (completed % 100 == 0 or completed == len(unique)):
                progress(completed, len(unique), len(failures))
    if pending:
        successes += store_kline_series(pending, path=cache_path, fetched_at=started_at)
    persist_progress()
    record_kline_failures(failures, path=cache_path, attempted_at=started_at)

    duration = round(time.monotonic() - started, 3)
    error_summary = ",".join(sorted(set(failures.values())))[:500]
    connection = _open_database(cache_path)
    try:
        with connection:
            connection.execute(
                """
                UPDATE prewarm_runs
                SET finished_at=?, completed_count=?, success_count=?, failure_count=?,
                    status='completed', duration_seconds=?, error_summary=?, updated_at=?
                WHERE target_date=?
                """,
                (
                    _now_text(), completed, successes, len(failures), duration,
                    error_summary, _now_text(), run_date,
                ),
            )
    finally:
        connection.close()
    return {
        "target_date": run_date,
        "requested_count": len(unique),
        "success_count": successes,
        "failure_count": len(failures),
        "completed_count": completed,
        "reused_count": len(fresh_symbols),
        "workers": worker_count,
        "duration_seconds": duration,
        "cache_path": str(cache_path),
        "status": "completed",
    }


__all__ = [
    "DEFAULT_CACHE_PATH",
    "DEFAULT_KLINE_COUNT",
    "DEFAULT_PREWARM_WORKERS",
    "fetch_a_share_daily_klines",
    "fetch_eastmoney_daily_klines",
    "fetch_tencent_daily_klines",
    "kline_cache_path",
    "kline_cache_readiness",
    "load_cached_kline_symbols",
    "load_kline_series_map",
    "merge_live_quote",
    "mark_prewarm_run_failed",
    "normalize_kline_rows",
    "prewarm_completed_for_date",
    "prewarm_kline_cache",
    "quote_trade_date",
    "record_kline_failures",
    "store_kline_series",
]
