#!/usr/bin/env python3
"""A-share 9:25 auction summary cron script.

Generates a deterministic market-open report and mirrors it to the dashboard.
Designed to avoid LLM/model-gateway calls during market-open peak load.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sys
import time
import html
import subprocess
from http.client import RemoteDisconnected
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from niuone_paths import get_dashboard_home

if __package__ == "app":
    from .reports.a_share import common as report_common
else:
    from reports.a_share import common as report_common

# Avoid user proxy breaking Eastmoney when possible.
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

try:
    import akshare as ak  # type: ignore
    import pandas as pd  # type: ignore
except Exception as e:  # durable environment problem: still alert user briefly
    print(f"牛牛大王，A股竞价总结生成失败：本机 akshare/pandas 不可用：{e}")
    sys.exit(1)

CN_TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime.now(CN_TZ)
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
STATE_DIR = DASHBOARD_HOME / "cron" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = STATE_DIR / "a_share_auction_summary.json"


class AuctionSnapshotUnavailable(RuntimeError):
    """Raised when a scheduled auction summary lacks a complete market snapshot."""


def is_trading_day_guess(day: dt.date) -> bool:
    return report_common.is_trading_day_guess(day)


def safe_float(value: Any, default: float = 0.0) -> float:
    return report_common.safe_float(value, default)


def safe_int(value: Any, default: int = 0) -> int:
    return report_common.safe_int(value, default, safe_number=safe_float)


def fmt_amt_yuan(value: float | int | None) -> str:
    return report_common.fmt_amt_yuan(value)


def fmt_volume_lot(v: float | int | None) -> str:
    if v is None:
        return "-"
    v = float(v)
    if abs(v) >= 1e8:
        return f"{v/1e8:.2f}亿手"
    if abs(v) >= 1e4:
        return f"{v/1e4:.2f}万手"
    return f"{v:.0f}手"


def fmt_price(v: float | int | None) -> str:
    v = safe_float(v)
    return f"{v:.2f}" if v > 0 else "-"


def fmt_pct(v: float | int | None) -> str:
    v = safe_float(v)
    return f"{v:+.2f}%"


def parse_money_to_yuan(value: Any) -> float:
    return report_common.parse_money_to_yuan(value, safe_number=safe_float)


def time_limit(seconds: int):
    return report_common.time_limit(seconds)


def normalize_industry_name(name: str) -> str:
    return report_common.normalize_industry_name(name)


def normalize_code(code: Any) -> str:
    return report_common.normalize_code(code)


def is_normal_a_share(code: str, name: str) -> bool:
    return report_common.is_normal_a_share(code, name, code_normalizer=normalize_code)


def first_col(frame, names: list[str]) -> str | None:
    return report_common.first_col(frame, names)


def fetch_zt_pool() -> tuple[Any | None, str | None]:
    # Try multiple akshare names/date formats. Around 9:25 the latest pool may be sparse but useful.
    funcs = [
        ("stock_zt_pool_em", {"date": NOW.strftime("%Y%m%d")}),
        ("stock_zt_pool_em", {}),
    ]
    last_err = None
    for fname, kwargs in funcs:
        try:
            fn = getattr(ak, fname)
            df = fn(**kwargs)
            if df is not None and len(df) >= 0:
                return df, None
        except Exception as e:
            last_err = f"{fname}: {type(e).__name__}: {e}"
    return None, last_err


def fetch_dt_pool() -> tuple[Any | None, str | None]:
    funcs = [
        ("stock_zt_pool_dtgc_em", {"date": NOW.strftime("%Y%m%d")}),
        ("stock_zt_pool_dtgc_em", {}),
    ]
    last_err = None
    for fname, kwargs in funcs:
        try:
            fn = getattr(ak, fname)
            df = fn(**kwargs)
            return df, None
        except Exception as e:
            last_err = f"{fname}: {type(e).__name__}: {e}"
    return None, last_err


def fetch_industry_boards() -> tuple[Any | None, str | None]:
    # Full industry board fetch is often slow around 9:25 and can dominate runtime.
    # Keep it opt-in; the report can still be useful from zt/dt pools alone.
    if os.getenv("A_SHARE_AUCTION_FETCH_BOARDS", "0") != "1":
        return None, "行业板块全量接口已跳过以保证9:25稳定性"
    try:
        df = ak.stock_board_industry_name_em()
        return df, None
    except Exception as e:
        return None, f"stock_board_industry_name_em: {type(e).__name__}: {e}"


def fetch_spot() -> tuple[Any | None, str | None]:
    # Full A-share spot list can take 40-60s via akshare in this environment.
    # Skip by default; use conservative name-based direction labels instead.
    if os.getenv("A_SHARE_AUCTION_FETCH_SPOT", "0") != "1":
        return None, "现货全量列表已跳过以保证9:25稳定性"
    for fname in ["stock_zh_a_spot_em", "stock_zh_a_spot"]:
        try:
            fn = getattr(ak, fname)
            df = fn()
            if df is not None and len(df):
                return df, None
        except Exception as e:
            last_err = f"{fname}: {type(e).__name__}: {e}"
    return None, locals().get("last_err", "no spot function")


def fetch_industry_fund_flow() -> tuple[Any | None, str | None]:
    """Fetch industry fund-flow/heat data with a hard short timeout.

    Uses TongHuaShun via akshare. This is optional; if it times out or the
    upstream rejects, the auction report still succeeds.
    """
    timeout_s = safe_int(os.getenv("A_SHARE_AUCTION_FUND_TIMEOUT", "5"), 5)
    if os.getenv("A_SHARE_AUCTION_FETCH_FUNDS", "1") != "1":
        return None, "行业资金流接口已按配置跳过"
    try:
        with time_limit(timeout_s):
            df = ak.stock_fund_flow_industry(symbol="即时")
        if df is None or len(df) == 0:
            return None, "行业资金流返回空"
        return df, None
    except Exception as e:
        return None, f"行业资金流：{type(e).__name__}: {e}"


def fetch_auction_snapshot() -> tuple[list[dict[str, Any]], str | None]:
    fields = "f12,f14,f2,f3,f4,f5,f6,f10,f15,f16,f17,f18,f100"
    page_size = 100
    max_pages = safe_int(os.getenv("A_SHARE_AUCTION_SNAPSHOT_MAX_PAGES", "70"), 70)
    deadline = time.monotonic() + safe_int(os.getenv("A_SHARE_AUCTION_SNAPSHOT_DEADLINE", "20"), 20)
    workers = max(1, min(12, safe_int(os.getenv("A_SHARE_AUCTION_SNAPSHOT_WORKERS", "8"), 8)))
    attempts = max(1, min(4, safe_int(os.getenv("A_SHARE_AUCTION_SNAPSHOT_RETRIES", "3"), 3)))
    endpoints = [
        item.strip().rstrip("?")
        for item in os.getenv(
            "A_SHARE_AUCTION_SNAPSHOT_ENDPOINTS",
            "https://push2.eastmoney.com/api/qt/clist/get,"
            "https://82.push2.eastmoney.com/api/qt/clist/get,"
            "http://push2.eastmoney.com/api/qt/clist/get",
        ).split(",")
        if item.strip()
    ] or ["https://push2.eastmoney.com/api/qt/clist/get"]
    all_items: list[dict[str, Any]] = []
    page_errors: list[str] = []

    def is_retryable_error(exc: Exception) -> bool:
        if isinstance(exc, HTTPError):
            return exc.code in {408, 429, 500, 502, 503, 504}
        return isinstance(exc, (RemoteDisconnected, URLError, TimeoutError, OSError))

    def describe_error(exc: Exception) -> str:
        return f"{type(exc).__name__}: {exc}"

    def fetch_page_once(page: int, endpoint: str) -> tuple[int, int, list[dict[str, Any]]]:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            return 0, page, []
        params = {
            "pn": str(page),
            "pz": str(page_size),
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f6",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": fields,
        }
        url = endpoint + "?" + urlencode(params)
        req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
        with urlopen(req, timeout=min(5, max(1, remaining))) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
        data = ((payload or {}).get("data") or {})
        return safe_int(data.get("total"), 0), page, data.get("diff") or []

    def fetch_page(page: int) -> tuple[int, int, list[dict[str, Any]]]:
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                endpoint = endpoints[(page + attempt - 2) % len(endpoints)]
                return fetch_page_once(page, endpoint)
            except Exception as exc:
                last_exc = exc
                if attempt < attempts and is_retryable_error(exc) and (deadline - time.monotonic()) > 1.5:
                    time.sleep(min(0.25 * attempt, max(0.0, deadline - time.monotonic() - 1)))
                    continue
                raise
        raise RuntimeError(describe_error(last_exc)) if last_exc else RuntimeError("unknown fetch page error")

    try:
        try:
            total, _, first_items = fetch_page(1)
        except Exception as e:
            page_errors.append(f"p1 {describe_error(e)}")
            total, first_items = 0, []
        all_items.extend(first_items)
        total_pages = min(max_pages, max(1, math.ceil((total or len(first_items)) / page_size)))
        if total_pages > 1 and time.monotonic() < deadline:
            page_items: dict[int, list[dict[str, Any]]] = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(fetch_page, page): page for page in range(2, total_pages + 1)}
                try:
                    for future in as_completed(futures, timeout=max(1, deadline - time.monotonic())):
                        try:
                            _total, page, diff = future.result()
                        except Exception as e:
                            page = futures[future]
                            page_errors.append(f"p{page} {describe_error(e)}")
                            continue
                        if _total:
                            total = _total
                        if diff:
                            page_items[page] = diff
                except FuturesTimeoutError:
                    pass
            for page in sorted(page_items):
                all_items.extend(page_items[page])
        rows = extract_auction_snapshot_rows(all_items)
        # Eastmoney occasionally closes the connection without returning even
        # page 1 around 09:25.  An empty snapshot must not silently become a
        # "successful" report with every auction section missing.  Tencent's
        # quote service is independent and exposes the same open/previous-close
        # plus cumulative amount fields, so use it as the live fallback.
        materially_incomplete = not rows or (total >= 1000 and len(all_items) < total * 0.9)
        if materially_incomplete:
            fallback_rows, fallback_err = fetch_tencent_auction_snapshot()
            if fallback_rows:
                # A complete fallback is a successful snapshot.  Do not expose
                # failures from the discarded primary source in the report.
                return fallback_rows, None
            if fallback_err:
                page_errors.append(f"腾讯备用行情 {fallback_err}")
        if not rows:
            if page_errors:
                return [], "竞价快照：" + "；".join(page_errors[:2])
            return [], "竞价快照返回空"
        if page_errors:
            prefix = f"竞价快照部分页失败 {len(page_errors)} 页"
            if total and len(all_items) < total:
                prefix += f"，只取到 {len(all_items)}/{total} 只"
            return rows, f"{prefix}（{'；'.join(page_errors[:2])}），已按现有样本生成"
        if total and len(all_items) < total:
            return rows, f"竞价快照只取到 {len(all_items)}/{total} 只，已按现有样本生成"
        return rows, None
    except Exception as e:
        return [], f"竞价快照：{type(e).__name__}: {e}"


def fetch_tencent_auction_snapshot() -> tuple[list[dict[str, Any]], str | None]:
    """Fetch a full A-share opening snapshot from Tencent as a live fallback."""
    deadline = time.monotonic() + safe_int(os.getenv("A_SHARE_AUCTION_FALLBACK_DEADLINE", "25"), 25)
    workers = max(1, min(12, safe_int(os.getenv("A_SHARE_AUCTION_FALLBACK_WORKERS", "10"), 10)))
    chunk_size = max(50, min(300, safe_int(os.getenv("A_SHARE_AUCTION_FALLBACK_CHUNK", "200"), 200)))
    symbols = (
        [f"sz{i:06d}" for i in range(1, 4000)]
        + [f"sz{i:06d}" for i in range(300001, 302000)]
        + [f"sh{i:06d}" for i in range(600000, 606000)]
        + [f"sh{i:06d}" for i in range(688000, 690000)]
    )
    chunks = [symbols[i:i + chunk_size] for i in range(0, len(symbols), chunk_size)]

    industry_map: dict[str, str] = {}
    cache_path = DASHBOARD_HOME / "cron" / "output" / "stock_industry_cache.json"
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(cached, dict):
            industry_map = {normalize_code(k): str(v) for k, v in cached.items() if v}
    except Exception:
        pass

    def fetch_chunk(chunk: list[str]) -> list[dict[str, Any]]:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            return []
        url = "https://qt.gtimg.cn/q=" + ",".join(chunk)
        req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://stock.qq.com/"})
        with urlopen(req, timeout=min(6, max(1, remaining))) as resp:
            body = resp.read().decode("gb18030", errors="ignore")
        out: list[dict[str, Any]] = []
        for raw in body.split(";"):
            match = re.search(r'="(.*)"', raw, re.S)
            if not match:
                continue
            parts = match.group(1).split("~")
            if len(parts) < 38:
                continue
            code, name = normalize_code(parts[2]), parts[1].strip()
            if not is_normal_a_share(code, name):
                continue
            latest, prev_close, open_price = safe_float(parts[3]), safe_float(parts[4]), safe_float(parts[5])
            if open_price <= 0 or prev_close <= 0:
                continue
            amount = 0.0
            trade = parts[35].split("/")
            if len(trade) >= 3:
                amount = safe_float(trade[2])
            estimated_volume_lot = amount / max(latest, open_price, 0.01) / 100
            pct = (open_price / prev_close - 1) * 100
            out.append({
                "code": code,
                "name": name,
                "industry": industry_map.get(code) or simple_industry_guess(name),
                "open_price": open_price,
                "latest_price": latest,
                "prev_close": prev_close,
                "auction_pct": pct,
                "change_pct": safe_float(parts[32]),
                "amount": amount,
                # Tencent mixes shares and lots across boards in field 6.
                # Derive a comparable lot estimate from turnover instead.
                "volume_lot": estimated_volume_lot,
                "vol_ratio": 0.0,
                "high": safe_float(parts[33]),
                "low": safe_float(parts[34]),
                "_quote_time": parts[30] if re.fullmatch(r"\d{14}", parts[30]) else "",
            })
        return out

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_chunk, chunk) for chunk in chunks]
        try:
            for future in as_completed(futures, timeout=max(1, deadline - time.monotonic())):
                try:
                    rows.extend(future.result())
                except Exception as exc:
                    errors.append(f"{type(exc).__name__}: {exc}")
        except FuturesTimeoutError:
            errors.append("抓取超时")
    deduped = {row["code"]: row for row in rows}
    result = list(deduped.values())
    result.sort(key=lambda row: row["code"])
    if len(result) < 4000:
        detail = f"仅取到 {len(result)} 只"
        if errors:
            detail += f"；{errors[0]}"
        return [], detail
    notes: list[str] = []
    if errors:
        notes.append(f"部分请求失败 {len(errors)} 组")
    quote_times = [str(row.get("_quote_time") or "") for row in result]
    latest_quote = max((value for value in quote_times if value), default="")
    if latest_quote:
        quote_dt = dt.datetime.strptime(latest_quote, "%Y%m%d%H%M%S")
        if quote_dt.time() >= dt.time(9, 27):
            notes.append(f"补全快照截至 {quote_dt:%H:%M:%S}，成交额/量含开盘后连续竞价")
    for row in result:
        row.pop("_quote_time", None)
    return result, ("；".join(notes) if notes else None)


def extract_auction_snapshot_rows(diff: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_by_code: dict[str, dict[str, Any]] = {}
    for item in diff or []:
        code = normalize_code(item.get("f12"))
        name = str(item.get("f14") or "").strip()
        if not is_normal_a_share(code, name):
            continue
        open_price = safe_float(item.get("f17"))
        latest_price = safe_float(item.get("f2"))
        if open_price <= 0:
            open_price = latest_price
        prev_close = safe_float(item.get("f18"))
        auction_pct = ((open_price / prev_close - 1) * 100) if open_price > 0 and prev_close > 0 else safe_float(item.get("f3"))
        industry = normalize_industry_name(str(item.get("f100") or "")) or simple_industry_guess(name)
        rows_by_code[code] = {
            "code": code,
            "name": name,
            "industry": industry if industry.lower() != "nan" else simple_industry_guess(name),
            "open_price": open_price,
            "latest_price": latest_price,
            "prev_close": prev_close,
            "auction_pct": auction_pct,
            "change_pct": safe_float(item.get("f3")),
            "amount": safe_float(item.get("f6")),
            "volume_lot": safe_float(item.get("f5")),
            "vol_ratio": safe_float(item.get("f10")),
            "high": safe_float(item.get("f15")),
            "low": safe_float(item.get("f16")),
        }
    return list(rows_by_code.values())


def opening_strength_label(pct: float) -> str:
    if pct >= 7:
        return "强高开"
    if pct >= 3:
        return "明显高开"
    if pct >= 0.5:
        return "小高开"
    if pct > -0.5:
        return "平开"
    if pct > -3:
        return "小低开"
    if pct > -7:
        return "明显低开"
    return "深低开"


def summarize_auction_snapshot(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    high_open = sum(1 for r in rows if safe_float(r.get("auction_pct")) >= 0.5)
    low_open = sum(1 for r in rows if safe_float(r.get("auction_pct")) <= -0.5)
    flat_open = max(total - high_open - low_open, 0)
    strong_open = sum(1 for r in rows if safe_float(r.get("auction_pct")) >= 3)
    weak_open = sum(1 for r in rows if safe_float(r.get("auction_pct")) <= -3)
    total_amount = sum(max(safe_float(r.get("amount")), 0) for r in rows)
    total_volume_lot = sum(max(safe_float(r.get("volume_lot")), 0) for r in rows)
    return {
        "total": total,
        "high_open": high_open,
        "flat_open": flat_open,
        "low_open": low_open,
        "strong_open": strong_open,
        "weak_open": weak_open,
        "total_amount": total_amount,
        "total_volume_lot": total_volume_lot,
    }


def auction_snapshot_issue(rows: list[dict[str, Any]], snapshot_err: str | None) -> str | None:
    """Return a retry-worthy completeness issue for the opening snapshot."""
    if not rows:
        return snapshot_err or "竞价快照返回空"
    min_rows = max(1, safe_int(os.getenv("A_SHARE_AUCTION_SNAPSHOT_MIN_ROWS", "4000"), 4000))
    unique_codes = {normalize_code(row.get("code")) for row in rows if normalize_code(row.get("code"))}
    if len(unique_codes) < min_rows:
        return f"唯一有效A股竞价样本仅 {len(unique_codes)} 只，低于完整性下限 {min_rows} 只"
    if len(unique_codes) < len(rows) * 0.98:
        return f"竞价快照重复代码过多：{len(unique_codes)}/{len(rows)}"
    open_coverage = sum(1 for row in rows if safe_float(row.get("open_price")) > 0) / len(rows)
    prev_close_coverage = sum(1 for row in rows if safe_float(row.get("prev_close")) > 0) / len(rows)
    if open_coverage < 0.80:
        return f"竞价开盘价有效覆盖率仅 {open_coverage:.1%}"
    if prev_close_coverage < 0.80:
        return f"竞价昨收价有效覆盖率仅 {prev_close_coverage:.1%}"
    return None


def top_industry_auction_stats(rows: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        ind = normalize_industry_name(str(r.get("industry") or "")) or "所属方向待复核"
        st = grouped.setdefault(ind, {"industry": ind, "count": 0, "amount": 0.0, "volume_lot": 0.0, "pct_sum": 0.0, "high_open": 0, "low_open": 0, "leaders": []})
        pct = safe_float(r.get("auction_pct"))
        st["count"] += 1
        st["amount"] += max(safe_float(r.get("amount")), 0)
        st["volume_lot"] += max(safe_float(r.get("volume_lot")), 0)
        st["pct_sum"] += pct
        st["high_open"] += 1 if pct >= 0.5 else 0
        st["low_open"] += 1 if pct <= -0.5 else 0
        st["leaders"].append((pct, safe_float(r.get("amount")), r.get("name") or "", r.get("code") or ""))
    stats = []
    for st in grouped.values():
        count = max(int(st["count"]), 1)
        avg_pct = st["pct_sum"] / count
        leaders = sorted(st["leaders"], key=lambda x: (x[0], x[1]), reverse=True)[:3]
        # Liquidity is only a confidence modifier here.  A linear amount term
        # made very liquid but broadly falling industries look "strongest".
        score = avg_pct * 2.5 + math.log1p(st["amount"] / 1e8) * 0.2 + (st["high_open"] - st["low_open"]) / count * 1.5
        stats.append({**st, "avg_pct": avg_pct, "leaders": leaders, "score": score})
    broad_stats = [item for item in stats if item["count"] >= 3]
    ranked = broad_stats or stats
    return sorted(ranked, key=lambda x: (x["score"], x["amount"]), reverse=True)[:limit]


def extract_limit_pool_rows(pool, industry_map: dict[str, str], *, default_pct: float = 10.0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if pool is None or len(pool) == 0:
        return rows
    code_col, name_col = get_code_name_cols(pool)
    pct_col = first_col(pool, ["涨跌幅", "涨幅"])
    price_col = first_col(pool, ["最新价", "价格"])
    if not (code_col and name_col):
        return rows
    for _, row in pool.iterrows():
        code = normalize_code(row.get(code_col))
        name = str(row.get(name_col) or "").strip()
        if not is_normal_a_share(code, name):
            continue
        ind = industry_map.get(code) or simple_industry_guess(name)
        rows.append({
            "code": code,
            "name": name,
            "industry": ind,
            "amount": estimate_seal_amount(row, pool),
            "pct": safe_float(row.get(pct_col), default_pct) if pct_col else default_pct,
            "price": safe_float(row.get(price_col)) if price_col else 0.0,
        })
    rows.sort(key=lambda x: (x["amount"], abs(x["pct"])), reverse=True)
    return rows


def get_code_name_cols(df):
    return first_col(df, ["代码", "股票代码", "symbol", "code"]), first_col(df, ["名称", "股票简称", "name"])


def estimate_seal_amount(row, df) -> float:
    # Eastmoney zt pool often has 封单资金/封板资金/最后封板资金/封单额; otherwise estimate by 最新价 * 封单量.
    for col in ["封单资金", "封板资金", "最后封板资金", "封单额", "涨停封单额", "封单金额"]:
        c = first_col(df, [col])
        if c is not None:
            val = safe_float(row.get(c))
            if val > 0:
                return val
    vol_col = first_col(df, ["封单量", "封板量", "买一量", "委买量"])
    price_col = first_col(df, ["最新价", "涨停价", "价格", "今开"])
    if vol_col is not None and price_col is not None:
        return safe_float(row.get(vol_col)) * safe_float(row.get(price_col))
    return 0.0


def enrich_industries_from_spot(spot) -> dict[str, str]:
    out = {}
    if spot is None:
        return out
    code_col, name_col = get_code_name_cols(spot)
    ind_col = first_col(spot, ["行业", "所属行业", "板块"])
    if code_col is None or ind_col is None:
        return out
    for _, r in spot.iterrows():
        code = normalize_code(r.get(code_col))
        ind = str(r.get(ind_col) or "").strip()
        if code and ind and ind.lower() != "nan":
            out[code] = ind
    return out


def simple_industry_guess(name: str) -> str:
    # Very conservative fallback for common auction labels.
    table = [
        ("银行", "银行/大金融"), ("证券", "证券/大金融"), ("保险", "保险/大金融"),
        ("通信", "通信设备"), ("光", "光通信/光电方向"), ("电子", "电子/元件"),
        ("科技", "科技/信息技术"), ("软件", "软件/信创"), ("芯", "半导体/芯片"),
        ("电力", "电力/公用事业"), ("能源", "能源方向"), ("煤", "煤炭/资源"),
        ("稀土", "稀土/小金属"), ("黄金", "黄金/贵金属"), ("铜", "有色金属"),
        ("汽车", "汽车产业链"), ("机器人", "机器人/高端制造"),
        ("药", "医药"), ("生物", "生物医药"), ("食品", "食品消费"),
        ("旅游", "旅游消费"), ("地产", "房地产"), ("建设", "基建/建筑"),
        ("水泥", "水泥/建材"), ("玻璃", "玻璃/建材"),
    ]
    for k, v in table:
        if k in name:
            return v
    return "所属方向待复核"


def markdown_to_html(text: str) -> str:
    """Tiny Markdown subset renderer suitable for this deterministic report."""
    out = []
    for raw in text.splitlines():
        line = html.escape(raw)
        line = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        if line.startswith("- "):
            out.append(f"<p class='bullet'>• {line[2:]}</p>")
        elif line.strip() == "":
            out.append("<div class='gap'></div>")
        else:
            out.append(f"<p>{line}</p>")
    return "\n".join(out)


def write_report_pdf(text: str) -> Path | None:
    """Render the report to PDF via reportlab with a Chinese system font."""
    out_dir = DASHBOARD_HOME / "cron" / "attachments" / "a_share_auction_summary"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = NOW.strftime("%Y-%m-%d_%H-%M_A股竞价盘前总结")
    pdf_path = out_dir / f"{stem}.pdf"
    title = f"A股竞价盘前总结 {NOW.strftime('%Y-%m-%d %H:%M')}"
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

        font_candidates = [
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/PingFang.ttc",
        ]
        font_name = "Helvetica"
        for fp in font_candidates:
            if Path(fp).exists():
                try:
                    pdfmetrics.registerFont(TTFont("CNFont", fp, subfontIndex=0))
                    font_name = "CNFont"
                    break
                except Exception:
                    continue

        styles = getSampleStyleSheet()
        normal = ParagraphStyle(
            "CNNormal", parent=styles["Normal"], fontName=font_name, fontSize=10.5,
            leading=16, spaceAfter=3, textColor=colors.HexColor("#111111"),
            wordWrap="CJK",
        )
        heading = ParagraphStyle(
            "CNHeading", parent=normal, fontName=font_name, fontSize=14,
            leading=20, spaceBefore=8, spaceAfter=5, textColor=colors.HexColor("#111111"),
        )
        title_style = ParagraphStyle(
            "CNTitle", parent=normal, fontName=font_name, fontSize=18,
            leading=24, spaceAfter=12, alignment=1,
        )
        bullet = ParagraphStyle(
            "CNBullet", parent=normal, leftIndent=10, firstLineIndent=-10,
        )
        code_style = "<font backColor='#f2f3f5'>\\1</font>"

        def inline_markup(s: str) -> str:
            s = html.escape(s)
            s = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", s)
            s = re.sub(r"`([^`]+)`", code_style, s)
            return s

        story = [Paragraph(html.escape(title), title_style)]
        for raw in text.splitlines():
            if not raw.strip():
                story.append(Spacer(1, 4))
            elif raw.startswith("**") and raw.endswith("**"):
                story.append(Paragraph(inline_markup(raw), heading))
            elif raw.startswith("- "):
                story.append(Paragraph("• " + inline_markup(raw[2:]), bullet))
            else:
                story.append(Paragraph(inline_markup(raw), normal))
        story.append(Spacer(1, 10))
        story.append(Paragraph("由牛牛1号自动生成，仅作盘前观察，不构成投资建议。", normal))
        doc = SimpleDocTemplate(str(pdf_path), pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm, title=title)
        doc.build(story)
        if pdf_path.exists() and pdf_path.stat().st_size > 1000:
            return pdf_path
    except Exception:
        return None
    return None


def build_decision_guidance(
    *,
    mood: str,
    zt_count: int,
    dt_count: int,
    industry_top: list[dict[str, Any]],
    amount_top: list[dict[str, Any]],
    snapshot_summary: dict[str, Any],
) -> list[str]:
    hot_dirs = "、".join(r.get("industry", "") for r in industry_top[:3] if r.get("industry"))
    if not hot_dirs:
        hot_dirs = "强势方向待开盘确认"
    active_names = "、".join(r.get("name", "") for r in amount_top[:3] if r.get("name")) or "竞价额前排股"
    strong_open = int(snapshot_summary.get("strong_open") or 0)
    weak_open = int(snapshot_summary.get("weak_open") or 0)

    if "偏弱" in mood or dt_count > max(zt_count, 0) or weak_open > max(strong_open, 0):
        risk = "防守"
        pace = "上午只观察或卖出，原则上不新开仓；先等跌停/风险端收缩"
        buy = "竞价强股只列观察，不追高开和独苗；至少等开盘15分钟承接和成交额延续"
        sell = "已有弱仓若低开不修复、跌破BBI/白线或板块掉队，优先按纪律处理"
    elif "进攻较强" in mood:
        risk = "进攻"
        pace = "上午最多2-3只，保留至少3个仓位给午后；单轮新开仓≤2笔"
        buy = f"优先看封单强、竞价额放大且明显高开的方向：{hot_dirs}；重点盯 {active_names}"
        sell = "弱于主线或开盘冲高回落的持仓可调出，给强主线留现金"
    elif "一定进攻" in mood:
        risk = "平衡"
        pace = "上午最多2-3只；先试错1笔，10:30后再看是否加仓"
        buy = f"围绕竞价强势板块：{hot_dirs}；高开过度、封单虚胖和开盘撤单明显的不买"
        sell = "开盘不及预期、板块承接差的持仓先降风险"
    else:
        risk = "谨慎"
        pace = "上午最多2只；本轮新开仓≤1笔，主要等待开盘5-15分钟确认"
        buy = f"只看涨停封单、竞价额和开盘强弱共振方向：{hot_dirs}；独苗不追"
        sell = "弱势持仓优先控仓，避免为了补票把上午仓位打满"
    return [
        "🎯 **今日买卖指引**",
        f"· 风险级别：{risk}",
        f"· 开仓节奏：{pace}",
        f"· 买入指引：{buy}",
        f"· 卖出/风控：{sell}",
    ]


def build_report(*, require_complete_snapshot: bool = False) -> str:
    if not is_trading_day_guess(NOW.date()):
        return ""

    snapshot_rows, snapshot_err = fetch_auction_snapshot()
    completeness_issue = auction_snapshot_issue(snapshot_rows, snapshot_err)
    if require_complete_snapshot and completeness_issue:
        raise AuctionSnapshotUnavailable(completeness_issue)
    post_open_fill = NOW.time() >= dt.time(9, 27)
    zt, zt_err = fetch_zt_pool()
    dtpool, dt_err = fetch_dt_pool()
    industry_map = {
        r["code"]: r["industry"]
        for r in snapshot_rows
        if r.get("code") and r.get("industry")
    }

    issues = []
    if snapshot_err:
        issues.append(snapshot_err)
    if snapshot_rows and post_open_fill:
        issues.append(f"本次为 {NOW:%H:%M} 开盘后补全：开盘价强弱可复核，成交额/量含连续竞价，不等同于纯9:25撮合值")
    if zt_err:
        issues.append(f"涨停池：{zt_err}")
    if dt_err:
        issues.append(f"跌停池：{dt_err}")

    zt_rows = extract_limit_pool_rows(zt, industry_map, default_pct=10.0)
    dt_rows = extract_limit_pool_rows(dtpool, industry_map, default_pct=-10.0)
    dt_count = len(dt_rows)
    snapshot_summary = summarize_auction_snapshot(snapshot_rows)
    industry_top = top_industry_auction_stats(snapshot_rows)
    amount_top = sorted(snapshot_rows, key=lambda x: x["amount"], reverse=True)[:8]
    gain_top = sorted(snapshot_rows, key=lambda x: (x["auction_pct"], x["amount"]), reverse=True)[:5]
    loss_top = sorted(snapshot_rows, key=lambda x: (x["auction_pct"], -x["amount"]))[:5]

    by_ind = defaultdict(lambda: {"amount": 0.0, "names": []})
    for r in zt_rows:
        by_ind[r["industry"]]["amount"] += r["amount"]
        if len(by_ind[r["industry"]]["names"]) < 3:
            by_ind[r["industry"]]["names"].append(r["name"])
    ind_top = sorted(by_ind.items(), key=lambda kv: kv[1]["amount"], reverse=True)[:5]

    time_s = NOW.strftime("%Y-%m-%d %H:%M")
    zt_count = len(zt_rows)
    strong_open = int(snapshot_summary["strong_open"])
    weak_open = int(snapshot_summary["weak_open"])
    high_open = int(snapshot_summary["high_open"])
    low_open = int(snapshot_summary["low_open"])
    if not snapshot_rows and not zt_rows:
        mood = "竞价关键数据不足，今天不放大盘前结论。"
    elif dt_count > max(zt_count, 0) and weak_open >= strong_open:
        mood = "竞价偏弱，风险端强于进攻端。"
    elif zt_count >= 30 and dt_count <= 5 and strong_open >= max(weak_open, 5):
        mood = "竞价进攻较强，涨停扩散且强高开家数占优。"
    elif weak_open > max(strong_open * 1.5, 5) or low_open > high_open * 1.5:
        mood = "竞价偏弱，低开家数占优，先控风险。"
    elif zt_count >= 10 or high_open > low_open * 1.2:
        mood = "竞价有一定进攻，但仍要看开盘承接。"
    else:
        mood = "竞价中性偏谨慎，结构性机会为主。"

    lines = []
    lines.append("牛牛大王，今日竞价总结补全来了：" if post_open_fill else "牛牛大王，9:25竞价总结来了：")
    lines.append("")
    lines.append(f"📊 **竞价情绪** · {time_s}")
    lines.append(f"涨停池 `{zt_count}` 只 · 跌停池 `{dt_count}` 只")
    if snapshot_rows:
        lines.append(f"样本 `{snapshot_summary['total']}` 只 | 高开 `{high_open}` · 平开 `{snapshot_summary['flat_open']}` · 低开 `{low_open}`")
        amount_label = "补全时点成交额" if post_open_fill else "竞价额"
        volume_label = "补全时点成交量" if post_open_fill else "竞价量"
        lines.append(f"强高开 `{strong_open}` · 深低开 `{weak_open}` | {amount_label} `{fmt_amt_yuan(snapshot_summary['total_amount'])}` · {volume_label} `{fmt_volume_lot(snapshot_summary['total_volume_lot'])}`")
    lines.append(f"💬 {mood}")
    lines.append("")

    lines.append("🚦 **开盘价强弱**")
    if gain_top or loss_top:
        if gain_top:
            lines.append("高开：" + " · ".join([f"`{r['code']} {r['name']}` {fmt_pct(r['auction_pct'])} 开{fmt_price(r['open_price'])}" for r in gain_top[:5]]))
        if loss_top:
            lines.append("低开：" + " · ".join([f"`{r['code']} {r['name']}` {fmt_pct(r['auction_pct'])} 开{fmt_price(r['open_price'])}" for r in loss_top[:5]]))
    else:
        lines.append("数据暂不可用")
    lines.append("")

    lines.append("🔥 **竞价强势板块**")
    if industry_top:
        for r in industry_top[:5]:
            leader_txt = "、".join(f"{name} {fmt_pct(pct)}" for pct, _, name, _ in r.get("leaders", [])[:2] if name) or "-"
            amount_label = "补全时点成交额" if post_open_fill else "竞价额"
            lines.append(f"`{r['industry']}` 均涨 {fmt_pct(r['avg_pct'])} | 高开 {r['high_open']}/{r['count']} | {amount_label} {fmt_amt_yuan(r['amount'])} | {leader_txt}")
    else:
        lines.append("数据暂不可用")
    lines.append("")

    lines.append("💰 **竞价成交活跃**")
    if amount_top:
        for r in amount_top[:6]:
            amount_label = "补全时点成交额" if post_open_fill else "竞价额"
            lines.append(f"`{r['code']} {r['name']}` {fmt_pct(r['auction_pct'])} | 开{fmt_price(r['open_price'])} | {amount_label} {fmt_amt_yuan(r['amount'])} · 量 {fmt_volume_lot(r['volume_lot'])}")
    else:
        lines.append("数据暂不可用")
    lines.append("")

    lines.append("📌 **涨停封单Top5板块**")
    if ind_top:
        for ind, v in ind_top:
            names = "、".join(v["names"])
            amt = fmt_amt_yuan(v["amount"]) if v["amount"] > 0 else "-"
            lines.append(f"`{ind}` {amt} | {names}")
    else:
        lines.append("数据暂不可用")
    lines.append("")

    lines.append("⚡ **封单Top5个股**")
    if zt_rows[:5]:
        for r in zt_rows[:5]:
            amt = fmt_amt_yuan(r["amount"]) if r["amount"] > 0 else "-"
            lines.append(f"`{r['code']} {r['name']}` {fmt_pct(r['pct'])} | 封单 {amt}")
    else:
        lines.append("数据暂不可用")
    lines.append("")

    lines.append("🧊 **跌停风险Top5**")
    if dt_rows[:5]:
        for r in dt_rows[:5]:
            amt = fmt_amt_yuan(r["amount"]) if r["amount"] > 0 else "-"
            lines.append(f"`{r['code']} {r['name']}` {fmt_pct(r['pct'])} | 封单 {amt}")
    else:
        lines.append("数据暂不可用")
    lines.append("")

    lines.append("👀 **重点观察**")
    if zt_rows[:3]:
        for r in zt_rows[:3]:
            lines.append(f"· `{r['name']}` 辨识度靠前，看板块跟风+开盘不炸")
    if amount_top[:2]:
        for r in amount_top[:2]:
            lines.append(f"· `{r['name']}` 竞价额靠前，开盘强弱 {opening_strength_label(safe_float(r.get('auction_pct')))}，看 9:30 后是否继续放量")
    if industry_top[:1]:
        top = industry_top[0]
        lines.append(f"· `{top['industry']}` 竞价板块强度靠前，确认同板块是否扩散")
    if not zt_rows[:3] and not amount_top[:2] and not industry_top[:1]:
        lines.append("· 强信号不密集，等开盘5-15分钟确认")
    lines.append("")

    lines.extend(build_decision_guidance(
        mood=mood,
        zt_count=zt_count,
        dt_count=dt_count,
        industry_top=industry_top,
        amount_top=amount_top,
        snapshot_summary=snapshot_summary,
    ))
    lines.append("")

    lines.append("⚠️ **风险**")
    if zt_count <= 3:
        lines.append("· 涨停数量少，独苗/孤立高开不追")
    if dt_count > zt_count:
        lines.append("· 跌停/风险票不低，情绪非无脑强")
    lines.append("· 高开过度、封单虚胖、开盘撤单和竞价额断档都要等二次确认")
    if post_open_fill:
        lines.append("· 本次补全的开盘价为真实开盘价；成交额/量含连续竞价，只用于辅助排序，不作为纯竞价额解读")
    else:
        lines.append("· 竞价成交额/成交量是 9:25 撮合快照，连续竞价承接以 9:30 后为准")
    lines.append("· 板块联动 + BBI右侧优先，不追单纯J值低")
    if issues:
        lines.append("")
        lines.append("ℹ️ " + " · ".join(issues[:3]))

    return "\n".join(lines).strip()


def main():
    try:
        text = build_report(require_complete_snapshot=True)
        if text:
            from a_share_grok_summary import apply_grok_to_a_share_report
            from market_report_store import store_market_report
            text = apply_grok_to_a_share_report(text, title="A股竞价盘前总结")
            store_market_report(text, job_id="8453b3f28cd3", title="A股竞价盘前总结", run_dt=NOW)
            print(text)
        else:
            # Weekend/holiday silence.
            print("")
    except AuctionSnapshotUnavailable as e:
        print(f"牛牛大王，A股竞价快照缺失或不完整，本轮不入库，等待调度器重试：{e}")
        sys.exit(1)
    except Exception as e:
        print(f"牛牛大王，A股竞价总结今天没有成功生成：{type(e).__name__}: {e}\n建议先手动看东方财富涨停池/行业板块，稍后我可以帮你补一版盘中总结。")
        sys.exit(1)


if __name__ == "__main__":
    main()
