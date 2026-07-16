#!/usr/bin/env python3
"""indices_dashboard_api.py — 综合指数行情 + 全球夜盘 + 黄金/外汇
供牛牛1号主服务的 /api/indices 动态导入。

输出: {"items": [...], "generated_at": "..."}
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

SSL_CTX = ssl._create_unverified_context()
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com/"}

# 腾讯 qt.gtimg.cn 可以同时取 A股指数、港股指数、美股指数
INDEX_DEFS = [
    ("sh", "sh000001", "上证指数", "domestic", "a_index"),
    ("sz", "sz399001", "深证成指", "domestic", "a_index"),
    ("cyb", "sz399006", "创业板指", "domestic", "a_index"),
    ("kc50", "sh000688", "科创50", "domestic", "a_index"),
    ("dow", "usDJI", "道琼斯指数", "global", "us_index"),
    ("nas", "usIXIC", "纳斯达克指数", "global", "us_index"),
    ("spx", "usINX", "标普500指数", "global", "us_index"),
]

# 新浪提供期货、黄金和原油
SINA_DEFS = [
    ("a50_fut", "hf_CHA50CFD", "富时中国A50期货", "global", "a_futures", "CHA50CFD"),
    ("xau", "hf_XAU", "伦敦金", "commodity", "commodity", "XAU"),
    ("brent", "hf_OIL", "布伦特原油", "commodity", "commodity", "OIL"),
    ("spx_fut", "hf_ES", "标普500期货", "global", "us_futures", "ES"),
    ("nas_fut", "hf_NQ", "纳斯达克期货", "global", "us_futures", "NQ"),
    ("dow_fut", "hf_YM", "道琼斯期货", "global", "us_futures", "YM"),
]

KLINE_URL = "https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={qt_code},day,,,60,qfq"
MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={qt_code}"
SINA_GLOBAL_MINUTE_URL = "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20t=/GlobalFuturesService.getGlobalFuturesMinLine?symbol={symbol}"
SINA_US_MINUTE_URL = "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20t=/US_MinKService.getMinK?symbol={symbol}&type=1"
EASTMONEY_US_MINUTE_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get?secid={secid}&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13,f14,f17&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&iscca=0&ndays=1"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d&includePrePost=true"
YAHOO_US_INDEX_SYMBOLS = {
    "usDJI": "^DJI",
    "usIXIC": "^IXIC",
    "usINX": "^GSPC",
}
SINA_US_INDEX_CODES = {
    "usDJI": "gb_dji",
    "usIXIC": "gb_ixic",
    "usINX": "gb_inx",
}
SINA_US_MINUTE_SYMBOLS = {
    "usDJI": ".DJI",
    "usIXIC": ".IXIC",
    "usINX": ".INX",
}
EASTMONEY_US_INDEX_SECIDS = {
    "usDJI": "100.DJIA",
    "usIXIC": "100.NDX",
    "usINX": "100.SPX",
}
NY_TZ = ZoneInfo("America/New_York")
CN_TZ = ZoneInfo("Asia/Shanghai")

_CACHE = {"ts": 0, "data": None}
CACHE_TTL = 45


def _env_int(name, default):
    try:
        return max(2, int(os.environ.get(name, str(default)) or str(default)))
    except (TypeError, ValueError):
        return default


MINUTE_LINE_MAX_POINTS = _env_int("DASHBOARD_INDEX_MINUTE_MAX_POINTS", 96)
INCLUDE_LEGACY_GROUPS = os.environ.get("DASHBOARD_INDICES_INCLUDE_GROUPS", "0").lower() in {"1", "true", "yes", "on"}


def _downsample(items, max_points):
    items = list(items or [])
    if len(items) <= max_points:
        return items
    last_idx = len(items) - 1
    selected = []
    seen = set()
    for i in range(max_points):
        idx = int(i * last_idx / max(1, max_points - 1))
        if idx in seen:
            continue
        seen.add(idx)
        selected.append(items[idx])
    if selected[-1] is not items[-1]:
        selected[-1] = items[-1]
    return selected


def _compact_price(value):
    try:
        return round(float(value), 4)
    except Exception:
        return value


def _compact_minute_line(points):
    compacted = []
    for point in _downsample(points, MINUTE_LINE_MAX_POINTS):
        compacted.append({**point, "price": _compact_price(point.get("price"))})
    return compacted


def _open(url, timeout=8, headers=None):
    req = urllib.request.Request(url, headers=headers or UA)
    return urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX).read()


def _qt_query(codes):
    if not codes:
        return ""
    url = "https://qt.gtimg.cn/q=" + ",".join(codes)
    try:
        return _open(url, timeout=8).decode("gbk", errors="replace")
    except Exception:
        try:
            return _open(url.replace("https://", "http://"), timeout=10).decode("gbk", errors="replace")
        except Exception:
            return ""


def _fmt_time(raw):
    raw = str(raw or "")
    if len(raw) >= 14 and raw[:14].isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]} {raw[8:10]}:{raw[10:12]}:{raw[12:14]}"
    return raw


def _quote_from_qt_parts(code, parts):
    if len(parts) < 33:
        return None
    try:
        price = float(parts[3] or 0)
        prev_close = float(parts[4] or 0)
        change = float(parts[31] or 0) if len(parts) > 31 else (price - prev_close)
        change_pct = float(parts[32] or 0) if len(parts) > 32 else ((price - prev_close) / prev_close * 100 if prev_close else 0)
        return {
            "name": parts[1] or code,
            "price": price,
            "prev_close": prev_close,
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "high": float(parts[33] or 0) if len(parts) > 33 else None,
            "low": float(parts[34] or 0) if len(parts) > 34 else None,
            "time": _fmt_time(parts[30] if len(parts) > 30 else ""),
        }
    except Exception:
        return None


def _parse_qt(raw):
    results = {}
    for line in raw.strip().splitlines():
        m = re.match(r'v_([^=]+)="(.*)";?', line.strip())
        if not m:
            continue
        code, fields = m.group(1), m.group(2)
        parts = fields.rstrip('";').split("~")
        quote = _quote_from_qt_parts(code, parts)
        if quote:
            results[code] = quote
    return results


def _sina_query(codes):
    url = "https://hq.sinajs.cn/list=" + ",".join(codes)
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
    try:
        return _open(url, timeout=8, headers=headers).decode("gbk", errors="replace")
    except Exception:
        return ""


def _parse_sina(raw):
    results = {}
    for line in raw.strip().splitlines():
        m = re.match(r'var hq_str_([^=]+)="(.*)";?', line.strip())
        if not m:
            continue
        code, fields = m.group(1), m.group(2)
        p = fields.rstrip('";').split(',')
        try:
            if code.startswith('hf_'):
                # hf_*: current, prev/settle-ish, open, bid, high, low, time, ... date, name
                price = float(p[0] or 0)
                base = float((p[7] or p[2] or p[1] or 0))
                change = price - base if base else 0
                pct = change / base * 100 if base else 0
                name = p[13] if len(p) > 13 and p[13] else code
                if code == 'hf_CHA50CFD':
                    name = '富时中国A50期货'
                elif code == 'hf_XAU':
                    name = '伦敦金'
                elif code == 'hf_OIL':
                    name = '布伦特原油'
                elif code == 'hf_ES':
                    name = '标普500期货'
                elif code == 'hf_NQ':
                    name = '纳斯达克期货'
                elif code == 'hf_YM':
                    name = '道琼斯期货'
                t = f"{p[12]} {p[6]}" if len(p) > 12 else ""
            else:
                # USDCNY/DINIW: time,current,prev/open-ish,low,...,name,date
                price = float(p[1] or 0)
                base = float((p[2] or p[5] or 0))
                change = price - base if base else 0
                pct = change / base * 100 if base else 0
                name = p[9] if len(p) > 9 and p[9] else code
                t = f"{p[10]} {p[0]}" if len(p) > 10 else ""
            results[code] = {"name": name, "price": price, "prev_close": base, "change": round(change, 2), "change_pct": round(pct, 2), "time": t}
        except Exception:
            continue
    return results


def _parse_sina_us_indices(raw):
    results = {}
    reverse = {v: k for k, v in SINA_US_INDEX_CODES.items()}
    for line in raw.strip().splitlines():
        m = re.match(r'var hq_str_([^=]+)="(.*)";?', line.strip())
        if not m:
            continue
        sina_code, fields = m.group(1), m.group(2)
        code = reverse.get(sina_code)
        if not code:
            continue
        p = fields.rstrip('";').split(',')
        if len(p) < 5:
            continue
        try:
            price = float(p[1] or 0)
            change_pct = float(p[2] or 0)
            change = float(p[4] or 0)
            prev_close = price - change if price and change else 0
            results[code] = {
                "name": p[0] or code,
                "price": price,
                "prev_close": prev_close,
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
                "high": float(p[6] or 0) if len(p) > 6 else None,
                "low": float(p[7] or 0) if len(p) > 7 else None,
                "time": p[3] if len(p) > 3 else "",
            }
        except Exception:
            continue
    return results


def _sina_us_index_query():
    return _sina_query(list(SINA_US_INDEX_CODES.values()))


def _trade_minute_from_hhmm(hhmm):
    raw = str(hhmm or "").replace(":", "")
    if len(raw) < 4 or not raw[:4].isdigit():
        return None
    try:
        hour = int(raw[:2])
        minute = int(raw[2:4])
    except Exception:
        return None
    minutes = hour * 60 + minute
    am_start, am_end, pm_start, pm_end = 9 * 60 + 30, 11 * 60 + 30, 13 * 60, 15 * 60
    if minutes < am_start or minutes > pm_end or (am_end < minutes < pm_start):
        return None
    if minutes <= am_end:
        return minutes - am_start
    return 120 + (minutes - pm_start)


@lru_cache(maxsize=32)
def _fetch_tencent_minute_snapshot(qt_code):
    """Fetch Tencent minute payload for A-share and US cash indices."""
    try:
        raw = _open(MINUTE_URL.format(qt_code=qt_code), timeout=8, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}).decode("utf-8", "replace")
        data = json.loads(raw)
        node = data.get("data", {}).get(qt_code, {})
        rows = (((node.get("data") or {}).get("data")) if isinstance(node.get("data"), dict) else None) or node.get("min_data") or []
        points = []
        for row in rows:
            if isinstance(row, (list, tuple)):
                if len(row) < 2:
                    continue
                hhmm, price_raw = row[0], row[1]
            else:
                parts = str(row).split()
                if len(parts) < 2:
                    continue
                hhmm, price_raw = parts[0], parts[1]
            minute = _trade_minute_from_hhmm(hhmm) if qt_code.startswith(("sh", "sz")) else None
            if qt_code.startswith(("sh", "sz")) and minute is None:
                continue
            try:
                price = float(price_raw)
            except Exception:
                continue
            if price > 0:
                point = {"time": str(hhmm), "price": price}
                if minute is not None:
                    point["minute"] = minute
                points.append(point)
        quote = None
        qt_parts = (((node.get("qt") or {}).get(qt_code)) if isinstance(node.get("qt"), dict) else None) or []
        if qt_parts:
            quote = _quote_from_qt_parts(qt_code, list(qt_parts))
        return {"minute_line": points, "quote": quote or {}}
    except Exception:
        return {"minute_line": [], "quote": {}}


def _fetch_minute_line(qt_code):
    """Fetch intraday minute line for A-share and US cash indices.

    The old dashboard sparkline used daily K closes, so the tiny line looked
    static during the session. Tencent minute/query returns the current trading
    day's per-minute points and updates as time advances.
    """
    if not qt_code.startswith(("sh", "sz", "us")):
        return []
    return _fetch_tencent_minute_snapshot(qt_code).get("minute_line") or []


def _fetch_minute_quote(qt_code):
    if not qt_code.startswith("us"):
        return {}
    return _fetch_tencent_minute_snapshot(qt_code).get("quote") or {}


def _fetch_sina_global_minute_line(symbol):
    """Fetch Sina global futures/spot minute line, e.g. XAU for 伦敦金."""
    try:
        raw = _open(
            SINA_GLOBAL_MINUTE_URL.format(symbol=symbol),
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"},
        ).decode("utf-8", "replace")
        m = re.search(r"var\s+\w+\s*=\s*\((.*)\);?\s*$", raw, re.S)
        if not m:
            return []
        payload = json.loads(m.group(1))
        rows = payload.get("minLine_1d") or []
        points = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            # First row can be [date, price, market, ..., hh:mm, ..., datetime]
            # Later rows are usually [hh:mm, price, ..., datetime].
            time_raw = row[4] if len(str(row[0])) == 10 and len(row) > 4 else row[0]
            price_raw = row[1]
            try:
                price = float(price_raw)
            except Exception:
                continue
            if price > 0:
                points.append({"time": str(time_raw), "price": price})
        return points
    except Exception:
        return []


def _fetch_yahoo_minute_line(symbol):
    """Fetch 1-minute intraday line for US cash indices from Yahoo Finance."""
    if not symbol:
        return []
    try:
        url = YAHOO_CHART_URL.format(symbol=urllib.parse.quote(symbol, safe=""))
        raw = _open(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}).decode("utf-8", "replace")
        payload = json.loads(raw)
        result = (payload.get("chart", {}).get("result") or [None])[0] or {}
        timestamps = result.get("timestamp") or []
        quote = ((result.get("indicators", {}).get("quote") or [{}])[0]) or {}
        closes = quote.get("close") or []
        points = []
        for ts, close in zip(timestamps, closes):
            try:
                price = float(close)
                dt = datetime.fromtimestamp(float(ts), NY_TZ)
            except Exception:
                continue
            if price > 0:
                points.append({"time": dt.strftime("%H:%M"), "price": price})
        return points
    except Exception:
        return []


def _fetch_sina_us_minute_line(symbol):
    """Fetch US cash-index minute line from Sina, rejecting stale samples."""
    if not symbol:
        return []
    try:
        raw = _open(
            SINA_US_MINUTE_URL.format(symbol=urllib.parse.quote(symbol, safe="")),
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"},
        ).decode("utf-8", "replace")
        m = re.search(r"var\s+\w+\s*=\s*\((.*)\);?\s*$", raw, re.S)
        if not m:
            return []
        rows = json.loads(m.group(1))
        if not isinstance(rows, list) or not rows:
            return []
        parsed = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                dt = datetime.strptime(str(row.get("d") or ""), "%Y-%m-%d %H:%M:%S")
                price = float(row.get("c") or 0)
            except Exception:
                continue
            if price > 0:
                parsed.append((dt, price))
        if len(parsed) < 2:
            return []
        latest_day = max(dt.date() for dt, _ in parsed)
        if latest_day < (datetime.now(NY_TZ).date() - timedelta(days=7)):
            return []
        points = [
            {"time": dt.strftime("%H:%M"), "price": price}
            for dt, price in parsed
            if dt.date() == latest_day
        ]
        return points if len(points) >= 2 else []
    except Exception:
        return []


def _fetch_eastmoney_us_minute_line(qt_code):
    """Fetch real US cash-index minute line from Eastmoney trends2."""
    secid = EASTMONEY_US_INDEX_SECIDS.get(qt_code)
    if not secid:
        return []
    try:
        raw = _open(
            EASTMONEY_US_MINUTE_URL.format(secid=urllib.parse.quote(secid, safe=".")),
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
        ).decode("utf-8", "replace")
        payload = json.loads(raw)
        trends = ((payload.get("data") or {}).get("trends")) or []
        points = []
        for row in trends:
            parts = str(row or "").split(",")
            if len(parts) < 2:
                continue
            try:
                dt_cn = datetime.strptime(parts[0], "%Y-%m-%d %H:%M").replace(tzinfo=CN_TZ)
                price = float(parts[1])
            except Exception:
                continue
            if price > 0:
                dt_ny = dt_cn.astimezone(NY_TZ)
                points.append({"time": dt_ny.strftime("%H:%M"), "price": price})
        return points if len(points) >= 2 else []
    except Exception:
        return []


def _fetch_kline(qt_code, count=45):
    # 只给 A股指数拉日K，海外/期货接口不共用这个格式
    if not qt_code.startswith(("sh", "sz")):
        return []
    try:
        raw = _open(KLINE_URL.format(qt_code=qt_code), timeout=8, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}).decode("utf-8", "replace")
        data = json.loads(raw)
        day = data.get("data", {}).get(qt_code, {}).get("day", [])
        return [float(k[2]) for k in day[-count:] if len(k) > 2]
    except Exception:
        return []


def fetch_indices_data(force_refresh=False):
    now = time.time()
    if not force_refresh and _CACHE["data"] is not None and now - _CACHE["ts"] < CACHE_TTL:
        return _CACHE["data"]

    qt_codes = [code for _, code, _, _, _ in INDEX_DEFS]
    qt_data = _parse_qt(_qt_query(qt_codes))
    sina_us_index_data = _parse_sina_us_indices(_sina_us_index_query())
    sina_data = _parse_sina(_sina_query([code for _, code, _, _, _, _ in SINA_DEFS]))

    def build_index_item(defn):
        key, code, name, group, market_type = defn
        q = qt_data.get(code, {})
        if market_type == "us_index" and not q:
            q = _fetch_minute_quote(code) or sina_us_index_data.get(code, {})
        minute_line = _fetch_minute_line(code)
        if len(minute_line) < 2 and market_type == "us_index":
            eastmoney_minute_line = _fetch_eastmoney_us_minute_line(code)
            if len(eastmoney_minute_line) >= 2:
                minute_line = eastmoney_minute_line
        if len(minute_line) < 2 and market_type == "us_index":
            yahoo_minute_line = _fetch_yahoo_minute_line(YAHOO_US_INDEX_SYMBOLS.get(code, ""))
            if len(yahoo_minute_line) >= 2:
                minute_line = yahoo_minute_line
        if len(minute_line) < 2 and market_type == "us_index":
            sina_minute_line = _fetch_sina_us_minute_line(SINA_US_MINUTE_SYMBOLS.get(code, ""))
            if len(sina_minute_line) >= 2:
                minute_line = sina_minute_line
        minute_line = _compact_minute_line(minute_line)
        sparkline = [] if minute_line else [_compact_price(p) for p in _downsample(_fetch_kline(code), MINUTE_LINE_MAX_POINTS)]
        return {
            "key": key, "code": code, "name": name, "group": group, "market_type": market_type,
            "price": q.get("price", 0),
            "prev_close": q.get("prev_close", 0),
            "change": q.get("change", 0),
            "change_pct": q.get("change_pct", 0),
            "high": q.get("high"), "low": q.get("low"),
            "sparkline": sparkline,
            "minute_line": minute_line,
            "sparkline_type": "minute" if minute_line else "daily",
            "time": q.get("time") or time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def build_sina_item(defn):
        key, code, name, group, market_type, minute_symbol = defn
        q = sina_data.get(code, {})
        minute_line = _fetch_sina_global_minute_line(minute_symbol) if minute_symbol else []
        minute_line = _compact_minute_line(minute_line)
        return {
            "key": key, "code": code, "name": name, "group": group, "market_type": market_type,
            "price": q.get("price", 0),
            "prev_close": q.get("prev_close", 0),
            "change": q.get("change", 0),
            "change_pct": q.get("change_pct", 0),
            "sparkline": [],
            "minute_line": minute_line,
            "sparkline_type": "minute" if minute_line else None,
            "time": q.get("time") or time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    with ThreadPoolExecutor(max_workers=10) as pool:
        index_items = list(pool.map(build_index_item, INDEX_DEFS))
        sina_items = list(pool.map(build_sina_item, SINA_DEFS))
    items = index_items + sina_items

    data = {"items": items, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if INCLUDE_LEGACY_GROUPS:
        data["groups"] = {
            "domestic": [x for x in items if x.get("group") == "domestic"],
            "global": [x for x in items if x.get("group") == "global"],
            "commodity": [x for x in items if x.get("group") == "commodity"],
        }
        data["market_groups"] = {
            "a_index": [x for x in items if x.get("market_type") == "a_index"],
            "us_index": [x for x in items if x.get("market_type") == "us_index"],
            "a_futures": [x for x in items if x.get("market_type") == "a_futures"],
            "us_futures": [x for x in items if x.get("market_type") == "us_futures"],
            "commodity": [x for x in items if x.get("market_type") == "commodity"],
        }
    _CACHE.update({"ts": now, "data": data})
    return data


if __name__ == "__main__":
    print(json.dumps(fetch_indices_data(force_refresh="--force-refresh" in sys.argv[1:]), ensure_ascii=False, indent=2))
