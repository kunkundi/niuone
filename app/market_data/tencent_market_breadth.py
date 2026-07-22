"""Bounded Tencent full-market snapshots for A-share breadth statistics."""
from __future__ import annotations

import datetime as dt
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from typing import Any, Callable
from urllib.request import Request, urlopen


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


def parse_tencent_quote_body(body: str) -> list[dict[str, Any]]:
    """Parse only the fields needed for breadth and limit-board statistics."""

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
        rows.append({
            "code": code,
            "name": str(fields[1] or "").strip(),
            "price": price,
            "pct": pct,
            "high": high,
            "upper_limit": upper_limit,
            "lower_limit": lower_limit,
            "quote_ts": _quote_timestamp(fields[30]),
        })
    return rows


def summarize_market_breadth(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate five observable market-breadth counts from one quote snapshot."""

    deduplicated = {
        str(row.get("code") or ""): row
        for row in rows
        if isinstance(row, dict) and str(row.get("code") or "")
    }
    quotes = list(deduplicated.values())
    red = green = flat = limit_up = limit_down = broken_limit = 0
    limit_price_count = 0
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

    generated = (
        dt.datetime.fromtimestamp(latest_quote_ts, tz=CN_TZ)
        if latest_quote_ts > 0
        else dt.datetime.now(CN_TZ)
    )
    return {
        "schema_version": 1,
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "universe": UNIVERSE_LABEL,
        "generated_at": generated.strftime("%Y-%m-%d %H:%M:%S"),
        "quote_count": len(quotes),
        "limit_price_count": limit_price_count,
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
    if errors:
        raise RuntimeError(
            f"Tencent market breadth incomplete: {len(errors)} quote batches failed after retry; "
            f"first error: {errors[0]}"
        )
    return snapshot


__all__ = [
    "fetch_tencent_market_breadth",
    "parse_tencent_quote_body",
    "summarize_market_breadth",
]
