#!/usr/bin/env python3
"""hot_stocks_dashboard_api.py — A股成交额/换手率/涨幅排行榜（腾讯行情）"""
import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard_json_cache import read_json_cache, write_json_cache
from niuone_paths import get_dashboard_home
from dashboard.apis.bounded_fetch import run_bounded_market_fetch

if __package__ == "app":
    from .dashboard.apis.cache import load_cached_payload
    from .dashboard.apis.hot_stocks import select_hot_stock_ranking
else:
    from dashboard.apis.cache import load_cached_payload
    from dashboard.apis.hot_stocks import select_hot_stock_ranking

MAIN_PREFIX = ("600", "601", "603", "605", "000", "001", "002", "003")
UA = {"User-Agent": "Mozilla/5.0"}
CACHE_BASE = get_dashboard_home(Path(__file__).resolve().parents[1]) / "cron" / "output"
CACHE_PATH = CACHE_BASE / "hot_stocks_dashboard_cache.json"
CACHE_TTL = 75


def market_prefix(code):
    return "sh" if code.startswith(("600", "601", "603", "605")) else "sz"


def _urlopen(url, timeout=10):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read()


def _parse_qt_line(line):
    if not line or '="' not in line:
        return None
    left, val = line.split('="', 1)
    symbol = left.split('_')[-1]
    parts = val.rstrip('";\n\r').split('~')
    if len(parts) < 50:
        return None
    try:
        code = parts[2]
        name = parts[1]
        price = float(parts[3] or 0)
        prev_close = float(parts[4] or 0)
        pct = float(parts[32] or 0)
        amount_wan = float(parts[37] or 0)  # 万元
        turnover = float(parts[38] or 0) if parts[38] else 0
        volume_lot = float(parts[36] or parts[6] or 0)
    except Exception:
        return None
    if price <= 0 and prev_close > 0:
        price = prev_close
    return {
        "symbol": symbol, "code": code, "name": name,
        "price": price, "pct": pct,
        "amount_wan": amount_wan,
        "amount_yi": round(amount_wan / 10000, 2),
        "turnover": turnover,
        "volume_lot": volume_lot,
    }


def _load_universe():
    from app.market_data.listing_cache import load_current_listing

    day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    rows = load_current_listing(CACHE_BASE / 'a_share_listing_latest.json', day)
    return [code for code, name in rows
            if code.startswith(MAIN_PREFIX) and 'ST' not in name.upper() and '退' not in name]


def _fetch_quotes(codes, chunk=120):
    deadline = time.monotonic() + 20
    codes = sorted(set(codes))

    def fetch_batch(batch):
        last_error = None
        expected = set(batch)
        url = 'https://qt.gtimg.cn/q=' + urllib.parse.quote(
            ','.join(market_prefix(code) + code for code in batch), safe=',')
        for _ in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('hot-stock quote deadline exceeded') from last_error
            try:
                text = _urlopen(url, timeout=min(8, remaining)).decode('gbk', 'ignore')
                rows = [_parse_qt_line(line) for line in text.splitlines()]
                rows = [row for row in rows if row and row.get('price', 0) > 0]
                observed = [row['code'] for row in rows]
                if set(observed) != expected or len(observed) != len(expected):
                    raise ValueError('hot-stock quote batch incomplete or duplicated')
                return rows
            except (OSError, ValueError) as exc:
                last_error = exc
        raise RuntimeError('hot-stock quote batch failed') from last_error

    batches = [codes[i:i + chunk] for i in range(0, len(codes), chunk)]
    pool = ThreadPoolExecutor(max_workers=6)
    try:
        futures = [pool.submit(fetch_batch, batch) for batch in batches]
        rows = []
        for future in as_completed(futures, timeout=max(.01, deadline - time.monotonic())):
            rows.extend(future.result())
        return sorted(rows, key=lambda row: row['code'])
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _compute():
    return run_bounded_market_fetch("hot_stocks", cache_path=CACHE_PATH)


def _compute_in_process():
    codes = _load_universe()
    quotes = _fetch_quotes(codes)
    tradable = [q for q in quotes if q.get('amount_wan', 0) > 0]
    if not tradable:
        raise RuntimeError("hot-stock providers returned no usable rows")
    if {row['code'] for row in quotes} != set(codes) or len(quotes) != len(codes):
        raise RuntimeError('hot-stock quote universe incomplete')
    by_amount = sorted(tradable, key=lambda x: x.get('amount_wan', 0), reverse=True)[:10]
    by_turnover = sorted([q for q in tradable if q.get('turnover', 0) > 0], key=lambda x: x.get('turnover', 0), reverse=True)[:10]
    by_volume = sorted(tradable, key=lambda x: x.get('volume_lot', 0), reverse=True)[:10]
    by_pct = sorted(tradable, key=lambda x: x.get('pct', 0), reverse=True)[:10]
    return {
        "generated_at": time.strftime('%Y-%m-%d %H:%M:%S'),
        "universe_count": len(codes),
        "quote_count": len(quotes),
        "items": by_amount,
        "amount_top": by_amount,
        "turnover_top": by_turnover,
        "volume_top": by_volume,
        "gain_top": by_pct,
    }


def fetch_hot_stocks(sort_by="amount"):
    empty = {"items": [], "amount_top": [], "turnover_top": [], "volume_top": [], "gain_top": []}
    data = load_cached_payload(
        CACHE_PATH,
        CACHE_TTL,
        compute=_compute,
        empty=empty,
        read_cache=read_json_cache,
        write_cache=write_json_cache,
    )
    return select_hot_stock_ranking(data, sort_by)

if __name__ == '__main__':
    print(json.dumps(fetch_hot_stocks(), ensure_ascii=False, indent=2))
