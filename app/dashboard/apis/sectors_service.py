#!/usr/bin/env python3
"""sectors_dashboard_api.py — 板块涨跌幅前十（涨幅/跌幅）
恢复 Dashboard 指数行情页的板块涨跌幅模块：涨幅前十 + 跌幅前十。
优先使用 akshare 可用的 fund flow industry/concept 即时接口（返回行业指数、涨跌幅）。
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from dashboard_json_cache import read_json_cache, write_json_cache
from niuone_paths import get_dashboard_home

if __package__ == "app":
    from .dashboard.apis.cache import load_cached_payload
else:
    from dashboard.apis.cache import load_cached_payload

CACHE_BASE = get_dashboard_home(Path(__file__).resolve().parents[1]) / "cron" / "output"
CACHE_PATH = CACHE_BASE / "sectors_dashboard_cache.json"
CACHE_TTL = 75
UA = {"User-Agent": "Mozilla/5.0"}

# 兜底行业指数（腾讯）
FALLBACK_CODES = [
    ("sh000928", "中证能源"), ("sh000929", "800材料"), ("sh000930", "800工业"),
    ("sh000931", "800可选"), ("sh000932", "中证消费"), ("sh000933", "中证医药"),
    ("sh000934", "中证金融"), ("sh000935", "中证信息"), ("sh000936", "800通信"),
    ("sh000937", "800公用"), ("sh000990", "全指消费"), ("sh000991", "全指医药"),
    ("sh000992", "全指金融"), ("sh000993", "全指信息"), ("sh000994", "全指通信"),
    ("sh000995", "全指公用"), ("sh000998", "中证TMT"),
]


def _num(v):
    try:
        return float(str(v).replace(',', '').replace('%', '').strip())
    except Exception:
        return 0.0


def _row(name, price, pct, source=""):
    return {"name": str(name), "price": _num(price), "pct": round(_num(pct), 2), "source": source}


def _ak_rows():
    import akshare as ak
    rows = []
    for source, func in [("行业", ak.stock_fund_flow_industry), ("概念", ak.stock_fund_flow_concept)]:
        try:
            df = func(symbol="即时")
            for _, r in df.iterrows():
                name = str(r.get("行业", "")).strip()
                if not name or name == 'nan':
                    continue
                rows.append(_row(name, r.get("行业指数"), r.get("行业-涨跌幅"), source))
        except Exception:
            continue
    # 去重：同名保留绝对涨跌幅更大的一个
    dedup = {}
    for r in rows:
        old = dedup.get(r["name"])
        if old is None or abs(r["pct"]) > abs(old["pct"]):
            dedup[r["name"]] = r
    return list(dedup.values())


def _fallback_rows():
    url = 'https://qt.gtimg.cn/q=' + ','.join(c for c, _ in FALLBACK_CODES)
    try:
        text = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=8).read().decode('gbk', 'ignore')
    except Exception:
        return []
    rows = []
    for line in text.splitlines():
        m = re.match(r'v_(\w+)="(.*)";?', line.strip())
        if not m:
            continue
        code, fields = m.group(1), m.group(2)
        p = fields.rstrip('";').split('~')
        if len(p) < 33:
            continue
        rows.append(_row(p[1] or code, p[3], p[32], "指数"))
    return rows


def _compute():
    rows = _ak_rows()
    if not rows:
        rows = _fallback_rows()
    gain_top = sorted(rows, key=lambda x: x.get('pct', 0), reverse=True)[:10]
    loss_top = sorted(rows, key=lambda x: x.get('pct', 0))[:10]
    return {
        "generated_at": time.strftime('%Y-%m-%d %H:%M:%S'),
        "count": len(rows),
        "gain_top": gain_top,
        "loss_top": loss_top,
        # 兼容旧前端：sectors/items 默认给涨幅榜
        "sectors": gain_top,
        "items": gain_top,
    }


def fetch_sector_data(force_refresh=False):
    empty = {"sectors": [], "items": [], "gain_top": [], "loss_top": []}
    return load_cached_payload(
        CACHE_PATH,
        CACHE_TTL,
        compute=_compute,
        empty=empty,
        read_cache=read_json_cache,
        write_cache=write_json_cache,
        force_refresh=force_refresh,
    )

if __name__ == '__main__':
    print(json.dumps(fetch_sector_data(force_refresh='--force-refresh' in sys.argv[1:]), ensure_ascii=False, indent=2))
