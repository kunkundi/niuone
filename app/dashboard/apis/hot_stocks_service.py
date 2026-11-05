#!/usr/bin/env python3
"""hot_stocks_dashboard_api.py — A股成交额/换手率/涨幅排行榜（腾讯行情）"""
import json
import re
import time
import urllib.parse
import urllib.request
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
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        code_col = 'code' if 'code' in df.columns else df.columns[0]
        name_col = 'name' if 'name' in df.columns else df.columns[1]
        codes = []
        for _, row in df.iterrows():
            code = re.sub(r'\D', '', str(row[code_col]))[-6:]
            name = str(row[name_col]).strip()
            if code.startswith(MAIN_PREFIX) and 'ST' not in name.upper() and '退' not in name:
                codes.append(code)
        return sorted(set(codes))
    except Exception:
        # 兜底：覆盖主板大部分常见区间，避免页面完全空白
        return [f"600{i:03d}" for i in range(0, 1000)] + [f"000{i:03d}" for i in range(1, 1000)] + [f"002{i:03d}" for i in range(1, 1000)]


def _fetch_quotes(codes, chunk=120):
    out = []
    symbols = [market_prefix(c) + c for c in codes]
    for i in range(0, len(symbols), chunk):
        q = ','.join(symbols[i:i+chunk])
        url = 'https://qt.gtimg.cn/q=' + urllib.parse.quote(q, safe=',')
        try:
            text = _urlopen(url, timeout=8).decode('gbk', 'ignore')
            for line in text.splitlines():
                item = _parse_qt_line(line)
                if item and item.get('price', 0) > 0:
                    out.append(item)
        except Exception:
            continue
    return out


def _compute():
    return run_bounded_market_fetch("hot_stocks", cache_path=CACHE_PATH)


def _compute_in_process():
    codes = _load_universe()
    quotes = _fetch_quotes(codes)
    tradable = [q for q in quotes if q.get('amount_wan', 0) > 0]
    if not tradable:
        raise RuntimeError("hot-stock providers returned no usable rows")
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
