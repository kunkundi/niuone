"""Tencent full-market quote fallback shared by A-share scheduled reports."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

if __package__ == "app":
    from .reports.a_share import common as report_common
else:
    from reports.a_share import common as report_common

CN_TZ = dt.timezone(dt.timedelta(hours=8))


def _safe_float(value: Any, default: float = 0.0) -> float:
    return report_common.safe_float(value, default)


def _normalize_code(value: Any) -> str:
    return report_common.normalize_code(value)


def _is_full_market_a_share(code: str, name: str) -> bool:
    return report_common.is_full_market_a_share(code, name, code_normalizer=_normalize_code)


def _symbols() -> list[str]:
    # Querying invalid codes is harmless; Tencent only returns listed symbols.
    return (
        [f"sz{i:06d}" for i in range(1, 4000)]
        + [f"sz{i:06d}" for i in range(300001, 302000)]
        + [f"sh{i:06d}" for i in range(600000, 606000)]
        + [f"sh{i:06d}" for i in range(688000, 690000)]
        + [f"bj{i:06d}" for start, stop in ((430000, 440000), (830000, 840000),
                                            (870000, 890000), (920000, 930000))
           for i in range(start, stop)]
    )


def _industry_map(dashboard_home: Path) -> dict[str, str]:
    cache_path = dashboard_home / "cron" / "output" / "stock_industry_cache.json"
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(cached, dict):
            return {_normalize_code(code): str(industry) for code, industry in cached.items() if industry}
    except Exception:
        pass
    return {}


def fetch_tencent_spot_snapshot(
    dashboard_home: Path,
    *,
    env_prefix: str = "A_SHARE_SUMMARY_TENCENT",
    min_rows: int = 4000,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch and validate a current full-market A-share snapshot from Tencent."""
    deadline_seconds = max(5, int(os.getenv(f"{env_prefix}_DEADLINE", "25")))
    workers = max(1, min(12, int(os.getenv(f"{env_prefix}_WORKERS", "10"))))
    chunk_size = max(50, min(300, int(os.getenv(f"{env_prefix}_CHUNK", "200"))))
    deadline = time.monotonic() + deadline_seconds
    symbols = _symbols()
    chunks = [symbols[i:i + chunk_size] for i in range(0, len(symbols), chunk_size)]
    industry_map = _industry_map(Path(dashboard_home))

    def fetch_chunk(chunk: list[str]) -> list[dict[str, Any]]:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            return []
        url = "https://qt.gtimg.cn/q=" + ",".join(chunk)
        req = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://stock.qq.com/",
                "Connection": "close",
            },
        )
        with urlopen(req, timeout=min(6, max(1, remaining))) as response:
            body = response.read().decode("gb18030", errors="ignore")
        output: list[dict[str, Any]] = []
        for raw in body.split(";"):
            match = re.search(r'="(.*)"', raw, re.S)
            if not match:
                continue
            parts = match.group(1).split("~")
            if len(parts) < 38:
                continue
            code, name = _normalize_code(parts[2]), parts[1].strip()
            if not _is_full_market_a_share(code, name):
                continue
            price = _safe_float(parts[3])
            prev_close = _safe_float(parts[4])
            if price <= 0 or prev_close <= 0:
                continue
            quote_time = parts[30] if re.fullmatch(r"\d{14}", parts[30]) else ""
            quote_ts = 0
            if quote_time:
                try:
                    quote_dt = dt.datetime.strptime(quote_time, "%Y%m%d%H%M%S").replace(tzinfo=CN_TZ)
                    quote_ts = int(quote_dt.timestamp())
                except ValueError:
                    pass
            amount = 0.0
            trade = parts[35].split("/")
            if len(trade) >= 3:
                amount = _safe_float(trade[2])
            if amount <= 0:
                amount = _safe_float(parts[37]) * 10_000
            pct = _safe_float(parts[32], (price / prev_close - 1) * 100)
            output.append({
                "code": code,
                "name": name,
                "pct": pct,
                "price": price,
                "amount": amount,
                "vol_ratio": 0.0,
                "industry": industry_map.get(code) or "所属方向待复核",
                "quote_ts": quote_ts,
            })
        return output

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(fetch_chunk, chunk) for chunk in chunks]
        try:
            for future in as_completed(futures, timeout=max(1, deadline - time.monotonic())):
                try:
                    rows.extend(future.result())
                except Exception as exc:
                    errors.append(f"{type(exc).__name__}: {exc}")
        except FuturesTimeoutError:
            errors.append("抓取超时")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    result = sorted({row["code"]: row for row in rows}.values(), key=lambda row: row["code"])
    if errors:
        return [], f"腾讯全市场行情部分请求失败 {len(errors)} 组，等待完整快照"
    if len(result) < min_rows:
        detail = f"腾讯全市场行情仅取到 {len(result)} 只，低于完整性下限 {min_rows} 只"
        if errors:
            detail += f"；{errors[0]}"
        return [], detail
    return result, None
