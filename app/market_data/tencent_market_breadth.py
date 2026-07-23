"""Bounded Tencent full-market snapshots for A-share breadth statistics."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from typing import Any, Callable
from urllib.request import Request, urlopen

from .eastmoney_turnover import (
    FALLBACK_SOURCE_NAME as TURNOVER_FALLBACK_SOURCE_NAME,
    FALLBACK_SOURCE_URL as TURNOVER_FALLBACK_SOURCE_URL,
    fetch_market_turnover_estimate,
)


CN_TZ = dt.timezone(dt.timedelta(hours=8))
SOURCE_NAME = "腾讯证券沪深A股实时行情"
SOURCE_URL = "https://gu.qq.com/"
UNIVERSE_LABEL = "沪深A股（含ST，不含B股、北交所及无有效现价证券）"
DEFAULT_MIN_ROWS = 5_000
DEFAULT_DEADLINE_SECONDS = 25
DEFAULT_WORKERS = 10
DEFAULT_CHUNK_SIZE = 200
MAX_WORKERS = 12
MAX_CHUNK_SIZE = 300
MAX_ATTEMPTS = 2
PREVIOUS_TURNOVER_SOURCE_NAME = "东方财富沪深指数日线"
PREVIOUS_TURNOVER_SOURCE_URL = "https://push2his.eastmoney.com/"
PREVIOUS_TURNOVER_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    "?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
    "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    "&klt=101&fqt=1&end=20500101&lmt=10"
)
PREVIOUS_TURNOVER_SECIDS = ("1.000001", "0.399001")
PREVIOUS_TURNOVER_RETRY_SECONDS = 300.0
_PREVIOUS_TURNOVER_CACHE_LOCK = threading.Lock()
_PREVIOUS_TURNOVER_CACHE: dict[str, dict[str, Any]] = {}


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(os.environ.get(name, str(default)) or str(default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _symbols() -> list[str]:
    """Return the bounded Shanghai/Shenzhen code space used by Tencent quotes."""

    return (
        [f"sz{i:06d}" for i in range(1, 4_000)]
        + [f"sz{i:06d}" for i in range(300_001, 302_000)]
        + [f"sh{i:06d}" for i in range(600_000, 606_000)]
        + [f"sh{i:06d}" for i in range(688_000, 690_000)]
    )


def _finite_float(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _quote_timestamp(value: Any) -> int:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{14}", text):
        return 0
    try:
        return int(dt.datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=CN_TZ).timestamp())
    except ValueError:
        return 0


def _download_previous_turnover(secid: str, timeout: float) -> str:
    request = Request(
        PREVIOUS_TURNOVER_URL.format(secid=secid),
        headers={
            "User-Agent": "Mozilla/5.0 NiuOne/1.0",
            "Referer": "https://quote.eastmoney.com/",
            "Connection": "close",
        },
    )
    with urlopen(request, timeout=max(1.0, timeout)) as response:
        return response.read().decode("utf-8", errors="ignore")


def _parse_daily_turnover_by_date(body: str, secid: str) -> dict[str, float]:
    """Parse Eastmoney daily index turnover amounts as yuan keyed by date."""

    payload = json.loads(str(body or "{}"))
    data = payload.get("data") if isinstance(payload, dict) else None
    expected_code = secid.split(".", 1)[-1]
    if not isinstance(data, dict) or str(data.get("code") or "") != expected_code:
        raise ValueError(f"Eastmoney turnover response missing index {secid}")
    result: dict[str, float] = {}
    for raw in data.get("klines") or []:
        fields = str(raw or "").split(",")
        if len(fields) < 7 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fields[0]):
            continue
        amount_yuan = _finite_float(fields[6])
        if amount_yuan is not None and amount_yuan > 0:
            result[fields[0]] = amount_yuan
    if not result:
        raise ValueError(f"Eastmoney turnover response has no daily amounts for {secid}")
    return result


def fetch_previous_market_turnover(
    before_date: dt.date,
    *,
    downloader: Callable[[str, float], str] = _download_previous_turnover,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any] | None:
    """Return the latest Shanghai/Shenzhen turnover before ``before_date``.

    A positive result is cached for the trading day. Failures are cached briefly
    so a degraded comparison source cannot cause a request storm while the main
    Tencent snapshot continues to update.
    """

    cache_key = before_date.isoformat()
    now = monotonic()
    with _PREVIOUS_TURNOVER_CACHE_LOCK:
        cached = _PREVIOUS_TURNOVER_CACHE.get(cache_key)
        if cached and (
            cached.get("value") is not None
            or now < float(cached.get("retry_after") or 0)
        ):
            value = cached.get("value")
            return dict(value) if isinstance(value, dict) else None
        try:
            daily_rows: list[dict[str, float]] = []
            with ThreadPoolExecutor(max_workers=len(PREVIOUS_TURNOVER_SECIDS)) as pool:
                futures = {
                    secid: pool.submit(downloader, secid, 5.0)
                    for secid in PREVIOUS_TURNOVER_SECIDS
                }
                for secid, future in futures.items():
                    daily_rows.append(_parse_daily_turnover_by_date(future.result(), secid))
            common_dates = set.intersection(*(set(rows) for rows in daily_rows))
            eligible_dates = sorted(day for day in common_dates if day < cache_key)
            if not eligible_dates:
                raise ValueError("Eastmoney turnover response has no prior common trading date")
            reference_date = eligible_dates[-1]
            turnover_yuan = sum(rows[reference_date] for rows in daily_rows)
            value = {
                "date": reference_date,
                "turnover_yi": round(turnover_yuan / 100_000_000, 2),
                "source": PREVIOUS_TURNOVER_SOURCE_NAME,
                "source_url": PREVIOUS_TURNOVER_SOURCE_URL,
            }
            _PREVIOUS_TURNOVER_CACHE[cache_key] = {"value": value, "retry_after": 0.0}
            return dict(value)
        except Exception as exc:
            _PREVIOUS_TURNOVER_CACHE[cache_key] = {
                "value": None,
                "retry_after": now + PREVIOUS_TURNOVER_RETRY_SECONDS,
            }
            print(
                f"[WARN] Previous market turnover unavailable error={type(exc).__name__}",
                flush=True,
            )
            return None


def add_turnover_comparison(
    snapshot: dict[str, Any],
    reference: dict[str, Any] | None,
) -> dict[str, Any]:
    """Attach projected turnover change versus the previous trading day."""

    result = dict(snapshot)
    estimated = _finite_float(result.get("estimated_turnover_yi"))
    previous = _finite_float((reference or {}).get("turnover_yi"))
    reference_date = str((reference or {}).get("date") or "").strip()
    if estimated is None or previous is None or previous <= 0 or not reference_date:
        return result
    result.update({
        "schema_version": 3,
        "previous_turnover_yi": round(previous, 2),
        "turnover_increment_yi": round(estimated - previous, 2),
        "turnover_comparison_date": reference_date,
        "turnover_comparison_source": str(
            (reference or {}).get("source") or PREVIOUS_TURNOVER_SOURCE_NAME
        ),
        "turnover_comparison_source_url": str(
            (reference or {}).get("source_url") or PREVIOUS_TURNOVER_SOURCE_URL
        ),
    })
    return result


def parse_tencent_quote_body(body: str) -> list[dict[str, Any]]:
    """Parse fields needed for breadth, limit-board, and turnover statistics."""

    rows: list[dict[str, Any]] = []
    for raw in str(body or "").split(";"):
        match = re.search(r'=\"(.*)\"', raw, re.S)
        if not match:
            continue
        fields = match.group(1).split("~")
        if len(fields) < 49:
            continue
        code = str(fields[2] or "").strip()
        if not re.fullmatch(r"(?:60|68|00|30)\d{4}", code):
            continue
        price = _finite_float(fields[3])
        prev_close = _finite_float(fields[4])
        high = _finite_float(fields[33])
        upper_limit = _finite_float(fields[47])
        lower_limit = _finite_float(fields[48])
        if price is None or prev_close is None or price <= 0 or prev_close <= 0:
            continue
        pct = _finite_float(fields[32])
        if pct is None:
            pct = (price / prev_close - 1) * 100
        turnover_amount_wan = _finite_float(fields[37])
        if turnover_amount_wan is not None and turnover_amount_wan < 0:
            turnover_amount_wan = None
        rows.append({
            "code": code,
            "name": str(fields[1] or "").strip(),
            "price": price,
            "pct": pct,
            "high": high,
            "upper_limit": upper_limit,
            "lower_limit": lower_limit,
            "quote_ts": _quote_timestamp(fields[30]),
            "turnover_amount_wan": turnover_amount_wan,
        })
    return rows


def summarize_market_breadth(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate breadth counts and market turnover from one quote snapshot."""

    deduplicated = {
        str(row.get("code") or ""): row
        for row in rows
        if isinstance(row, dict) and str(row.get("code") or "")
    }
    quotes = list(deduplicated.values())
    red = green = flat = limit_up = limit_down = broken_limit = 0
    limit_price_count = 0
    turnover_amount_count = 0
    turnover_amount_wan = 0.0
    latest_quote_ts = 0
    for row in quotes:
        pct = _finite_float(row.get("pct"))
        if pct is not None and pct > 0:
            red += 1
        elif pct is not None and pct < 0:
            green += 1
        else:
            flat += 1

        price = _finite_float(row.get("price"))
        high = _finite_float(row.get("high"))
        upper_limit = _finite_float(row.get("upper_limit"))
        lower_limit = _finite_float(row.get("lower_limit"))
        if (
            price is not None
            and high is not None
            and upper_limit is not None
            and lower_limit is not None
            and upper_limit > 0
            and lower_limit > 0
        ):
            limit_price_count += 1
            if price >= upper_limit:
                limit_up += 1
            elif high >= upper_limit:
                broken_limit += 1
            if price <= lower_limit:
                limit_down += 1
        latest_quote_ts = max(latest_quote_ts, int(row.get("quote_ts") or 0))
        amount = _finite_float(row.get("turnover_amount_wan"))
        if amount is not None and amount >= 0:
            turnover_amount_count += 1
            turnover_amount_wan += amount

    generated = (
        dt.datetime.fromtimestamp(latest_quote_ts, tz=CN_TZ)
        if latest_quote_ts > 0
        else dt.datetime.now(CN_TZ)
    )
    actual_turnover_yi = round(turnover_amount_wan / 10_000, 2)
    return {
        "schema_version": 3,
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "universe": UNIVERSE_LABEL,
        "generated_at": generated.strftime("%Y-%m-%d %H:%M:%S"),
        "quote_count": len(quotes),
        "limit_price_count": limit_price_count,
        "turnover_amount_count": turnover_amount_count,
        "actual_turnover_yi": actual_turnover_yi,
        "turnover_actual_source": TURNOVER_FALLBACK_SOURCE_NAME,
        "turnover_actual_source_url": TURNOVER_FALLBACK_SOURCE_URL,
        "red": red,
        "green": green,
        "flat": flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "broken_limit": broken_limit,
    }


def _download_chunk(symbols: list[str], timeout: float) -> str:
    request = Request(
        "https://qt.gtimg.cn/q=" + ",".join(symbols),
        headers={
            "User-Agent": "Mozilla/5.0 NiuOne/1.0",
            "Referer": "https://stock.qq.com/",
            "Connection": "close",
        },
    )
    with urlopen(request, timeout=max(1.0, timeout)) as response:
        return response.read().decode("gb18030", errors="ignore")


def fetch_tencent_market_breadth(
    *,
    min_rows: int | None = None,
    downloader: Callable[[list[str], float], str] = _download_chunk,
    previous_turnover_fetcher: Callable[[dt.date], dict[str, Any] | None] = (
        fetch_previous_market_turnover
    ),
    turnover_estimate_fetcher: Callable[[dt.datetime, Any], dict[str, Any]] = (
        fetch_market_turnover_estimate
    ),
) -> dict[str, Any]:
    """Fetch a validated snapshot with bounded timeout, retries, and concurrency."""

    deadline_seconds = _bounded_int(
        "DASHBOARD_MARKET_BREADTH_DEADLINE_SECONDS",
        DEFAULT_DEADLINE_SECONDS,
        5,
        60,
    )
    workers = _bounded_int(
        "DASHBOARD_MARKET_BREADTH_WORKERS",
        DEFAULT_WORKERS,
        1,
        MAX_WORKERS,
    )
    chunk_size = _bounded_int(
        "DASHBOARD_MARKET_BREADTH_CHUNK_SIZE",
        DEFAULT_CHUNK_SIZE,
        50,
        MAX_CHUNK_SIZE,
    )
    required_rows = (
        max(1, int(min_rows))
        if min_rows is not None
        else _bounded_int(
            "DASHBOARD_MARKET_BREADTH_MIN_ROWS",
            DEFAULT_MIN_ROWS,
            1_000,
            10_000,
        )
    )
    deadline = time.monotonic() + deadline_seconds
    symbols = _symbols()
    chunks = [symbols[index:index + chunk_size] for index in range(0, len(symbols), chunk_size)]

    def fetch_chunk(chunk: list[str]) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            remaining = deadline - time.monotonic()
            if remaining <= 1:
                break
            try:
                return parse_tencent_quote_body(downloader(chunk, min(6.0, remaining)))
            except Exception as exc:
                last_error = exc
                if attempt + 1 < MAX_ATTEMPTS:
                    time.sleep(min(0.2 * (attempt + 1), max(0.0, remaining - 1)))
        if last_error is not None:
            raise last_error
        raise TimeoutError("Tencent market-breadth deadline reached")

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(fetch_chunk, chunk) for chunk in chunks]
        try:
            for future in as_completed(futures, timeout=max(1.0, deadline - time.monotonic())):
                try:
                    rows.extend(future.result())
                except Exception as exc:
                    errors.append(f"{type(exc).__name__}: {exc}")
        except FuturesTimeoutError:
            errors.append("TimeoutError: Tencent market-breadth batch deadline reached")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    snapshot = summarize_market_breadth(rows)
    if snapshot["quote_count"] < required_rows:
        detail = (
            f"Tencent market breadth returned {snapshot['quote_count']} quotes; "
            f"minimum is {required_rows}"
        )
        if errors:
            detail += f"; {errors[0]}"
        raise RuntimeError(detail)
    if snapshot["turnover_amount_count"] < required_rows:
        raise RuntimeError(
            f"Tencent market breadth returned turnover for "
            f"{snapshot['turnover_amount_count']} quotes; minimum is {required_rows}"
        )
    if errors:
        raise RuntimeError(
            f"Tencent market breadth incomplete: {len(errors)} quote batches failed after retry; "
            f"first error: {errors[0]}"
        )
    generated = dt.datetime.strptime(
        snapshot["generated_at"],
        "%Y-%m-%d %H:%M:%S",
    )
    try:
        turnover = turnover_estimate_fetcher(
            generated,
            snapshot.get("actual_turnover_yi"),
        )
    except Exception as exc:
        print(
            f"[WARN] Market turnover estimate unavailable error={type(exc).__name__}",
            flush=True,
        )
        snapshot["turnover_estimate_warning"] = "近20日量能模型暂不可用"
    else:
        if isinstance(turnover, dict):
            snapshot.update(turnover)
    if "estimated_turnover_yi" not in snapshot:
        return snapshot
    if "turnover_increment_yi" in snapshot:
        return snapshot
    try:
        reference = previous_turnover_fetcher(generated.date())
    except Exception as exc:
        print(
            f"[WARN] Previous market turnover unavailable error={type(exc).__name__}",
            flush=True,
        )
        reference = None
    return add_turnover_comparison(snapshot, reference)


__all__ = [
    "add_turnover_comparison",
    "fetch_previous_market_turnover",
    "fetch_tencent_market_breadth",
    "parse_tencent_quote_body",
    "summarize_market_breadth",
]
