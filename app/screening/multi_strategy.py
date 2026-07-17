#!/usr/bin/env python3
"""
牛牛1号 · 多战法扫描器 — A股主板全市场综合评分。

评估多战法（趋势/突破策略 + Z哥），每只票输出多战法分数
+ 最优战法标签，供实战页面模型决策时参考。

数据源（全部绕过Eastmoney代理封锁）：
  1. akshare.stock_info_a_code_name() — 代码池
  2. 腾讯 qt.gtimg.cn 批量行情 — 实时报价
  3. 腾讯 web.ifzq.gtimg.cn fqkline — 日K数据

用法：
  cd /path/to/NiuOne/app
  DASHBOARD_HOME=/path/to/NiuOne/.local-data/runtime python multi_strategy_screen.py [--json]

输出格式（JSON）：
{
  "generated_at": "2026-06-20 10:00:00",
  "candidates": [
    {
      "code": "603019", "name": "中科曙光",
      "price": 45.20, "change_pct": 2.3,
      "best_strategy": "shaofu_b1",
      "best_score": 8,
      "strategies": {
        "shaofu_b1":    {"score": 8, "verdict": "高匹配少妇B1", ...},
        "trend_pullback":{"score": 6, "verdict": "中等匹配趋势回踩", ...},
        "breakout":     {"score": 4, "verdict": "弱匹配突破", ...}
      }
    }
  ],
  "total_analyzed": 387
}
"""
import concurrent.futures
import json
import os
import re
import shlex
import statistics
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any

from core.model_api import build_model_request, request_model
from niuone_paths import get_dashboard_env_file, get_dashboard_home
from screening.stock_universe import (
    DEFAULT_STOCK_UNIVERSE,
    STOCK_UNIVERSE_ENV,
    friendly_stock_universe,
    normalize_stock_universe,
    selected_stock_universe,
    stock_in_universe,
    stock_universe_metadata,
)
from strategies.registry import (
    ACTIVE_STRATEGY_ENV,
    DISPLAY_STRATEGY_ORDER,
    PERSONA_STRATEGY_ENV,
    STRATEGY_SOURCE_ENV,
    STRATEGY_DEFINITIONS,
    STRATEGY_META,
    STRATEGY_SCORE_PROFILES,
    enabled_persona_strategy_ids,
    enabled_strategy_ids,
    enabled_strategy_meta,
    enabled_strategy_score_profiles,
)
from strategies.scoring import (
    B1_CORE_J_CEILING,
    B1_WATCH_J_CEILING,
    COMMON_MAX_BBI_DISTANCE_PCT,
    LI_DAXIAO_HOT_TURNOVER,
    LI_DAXIAO_MAX_BBI_DISTANCE,
    LI_DAXIAO_MAX_DAILY_CHASE_PCT,
    LI_DAXIAO_MAX_TURNOVER,
    LI_DAXIAO_MIN_AMOUNT,
    SECTOR_TIDE_STRATEGY_IDS,
    STRATEGY_SCORERS,
    analyze_enriched_rows,
    build_sector_tide_context,
    candle_amplitude_pct,
    candle_body_pct,
    combine_z_yellow,
    compute_bbi,
    compute_ema,
    compute_kdj,
    enrich_rows,
    is_yang,
    is_yin,
    li_daxiao_bottom_stage,
    moving_avg,
    n_structure_ok,
    pct_change,
    pct_returns,
    recent_b1_indices,
    return_pct,
    safe_float,
    safe_round,
    score_b2_confirm,
    score_b3_accelerate,
    score_breakout,
    score_li_daxiao_bottom,
    score_shaofu_b1,
    score_super_b1,
    score_trend_pullback,
    strategy_hard_blockers,
    volatility_pct,
    with_strategy_profile,
)
from strategies.selection import (
    candidate_is_trade_ready,
    select_display_candidates,
    select_trade_candidates,
)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TENCENT_QUOTE = "https://qt.gtimg.cn/q="
TENCENT_KLINE = "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
DASHBOARD_HOME = get_dashboard_home(Path(__file__).resolve().parents[1])
DASHBOARD_ENV_FILE = get_dashboard_env_file(Path(__file__).resolve().parents[1])
B1_OUTPUT_DIR = DASHBOARD_HOME / "cron" / "output"
B1_CACHE_FILE = B1_OUTPUT_DIR / "b1_screen_latest.json"
MULTI_STRATEGY_CACHE = B1_OUTPUT_DIR / "multi_strategy_latest.json"
STOCK_INDUSTRY_CACHE = B1_OUTPUT_DIR / "stock_industry_cache.json"
B1_HISTORY_DIR = B1_OUTPUT_DIR / "b1_history"
MULTI_STRATEGY_HISTORY = B1_OUTPUT_DIR / "multi_strategy_history"
DISPLAY_CANDIDATE_LIMIT = 16
DISPLAY_HEAD_LIMIT = 8
TRADE_CANDIDATE_LIMIT = 8
_LOCAL_SITE_PACKAGES_READY = False
_STOCK_INDUSTRY_MEMORY_CACHE: dict[str, str] | None = None
_MARGIN_DETAIL_CACHE: dict[tuple[str, str], Any] = {}
_MARGIN_DETAIL_CACHE_LOCK = threading.Lock()
_BLOCK_TRADE_CACHE: dict[tuple[str, str], Any] = {}
_BLOCK_TRADE_CACHE_LOCK = threading.Lock()


# ========== helpers ==========

def _load_cached_market_frame(
    cache: dict[tuple[str, str], Any],
    cache_lock: Any,
    cache_key: tuple[str, str],
    loader: Callable[[], Any],
) -> Any:
    """Load one market-wide frame per cache key, including under concurrent scans."""
    with cache_lock:
        if cache_key not in cache:
            try:
                cache[cache_key] = loader()
            except Exception:
                cache[cache_key] = None
        return cache[cache_key]

def dashboard_env_value(name: str) -> str | None:
    if name in os.environ:
        return os.environ.get(name)
    try:
        lines = DASHBOARD_ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if key.strip() != name:
            continue
        try:
            parsed = shlex.split(raw_value.strip(), posix=True)
            return parsed[0] if parsed else ""
        except ValueError:
            return raw_value.strip().strip("\"'")
    return None


def enabled_persona_strategy_setting() -> str | None:
    return dashboard_env_value(PERSONA_STRATEGY_ENV)


def strategy_source_setting() -> str | None:
    return dashboard_env_value(STRATEGY_SOURCE_ENV)


def active_strategy_setting() -> str | None:
    return dashboard_env_value(ACTIVE_STRATEGY_ENV)


def active_strategy_scorers() -> dict[str, Callable[[list[dict[str, Any]]], dict[str, Any] | None]]:
    enabled = enabled_strategy_ids(enabled_persona_strategy_setting(), strategy_source_setting(), active_strategy_setting())
    return {strategy_id: scorer for strategy_id, scorer in STRATEGY_SCORERS.items() if strategy_id in enabled}


def active_strategy_meta() -> dict[str, dict[str, Any]]:
    return enabled_strategy_meta(enabled_persona_strategy_setting(), strategy_source_setting(), active_strategy_setting())


def active_strategy_score_profiles() -> dict[str, dict[str, Any]]:
    return enabled_strategy_score_profiles(enabled_persona_strategy_setting(), strategy_source_setting(), active_strategy_setting())


def configured_stock_universe() -> tuple[str, ...]:
    return selected_stock_universe(dashboard_env_value(STOCK_UNIVERSE_ENV))


def candidate_in_configured_stock_universe(candidate: dict[str, Any]) -> bool:
    return stock_in_universe(
        candidate.get("code"),
        candidate.get("name"),
        configured_stock_universe(),
    )


# ========== Tencent data fetchers ==========

def tencent_batch_quote(codes):
    url = TENCENT_QUOTE + ",".join(codes)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("gbk", "ignore")
    results = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip().lstrip("v_")
        val = val.strip().strip('"')
        parts = val.split("~")
        if len(parts) < 38:
            continue
        price = safe_float(parts[3])
        prev_close = safe_float(parts[4])
        change_pct = ((price / prev_close - 1) * 100) if price and prev_close else None
        amount_wan = safe_float(parts[37])
        amount = amount_wan * 10000 if amount_wan else 0
        results[key] = {
            "name": parts[1],
            "price": price,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "amount": amount,
            "volume": safe_float(parts[6]),
            "high": safe_float(parts[33]),
            "low": safe_float(parts[34]),
            "turnover": safe_float(parts[38]),
            "quote_time": parts[30] if len(parts) > 30 else "",
        }
    return results


def build_market_snapshot(
    quotes: dict[str, dict[str, Any]],
    captured_at: str = "",
    pool_count: int = 0,
    stock_universe: object | None = None,
) -> dict[str, Any]:
    """Summarize the full quote batch already fetched by the B1 scan.

    This lets every periodic scan refresh the decision label without issuing a
    second all-market request. The snapshot retains its configured universe so
    downstream consumers do not treat it as a whole-market statistic.
    """
    rows: list[dict[str, float]] = []
    quote_times: list[str] = []
    for quote in (quotes or {}).values():
        if not isinstance(quote, dict):
            continue
        price = safe_float(quote.get("price"))
        prev_close = safe_float(quote.get("prev_close"))
        change_pct = safe_float(quote.get("change_pct"))
        if price is None or price <= 0 or prev_close is None or prev_close <= 0 or change_pct is None:
            continue
        rows.append({
            "change_pct": change_pct,
            "amount": max(0.0, safe_float(quote.get("amount")) or 0.0),
        })
        raw_quote_time = re.sub(r"\D", "", str(quote.get("quote_time") or ""))
        if len(raw_quote_time) >= 14:
            quote_times.append(
                f"{raw_quote_time[:4]}-{raw_quote_time[4:6]}-{raw_quote_time[6:8]} "
                f"{raw_quote_time[8:10]}:{raw_quote_time[10:12]}:{raw_quote_time[12:14]}"
            )

    changes = [row["change_pct"] for row in rows]
    pool_count = max(int(pool_count or 0), len(quotes or {}), len(changes))
    up = sum(1 for pct in changes if pct > 0)
    down = sum(1 for pct in changes if pct < 0)
    flat = max(0, len(changes) - up - down)
    universe_values = selected_stock_universe(stock_universe)
    legacy_universe = universe_values == (DEFAULT_STOCK_UNIVERSE,)
    return {
        "source": "b1_mainboard_quotes" if legacy_universe else "b1_configured_universe_quotes",
        "universe": "mainboard_non_st" if legacy_universe else "configured_a_share",
        "stock_universe": list(universe_values),
        "stock_universe_label": friendly_stock_universe(universe_values),
        "captured_at": captured_at or time.strftime("%Y-%m-%d %H:%M:%S"),
        "quote_time": max(quote_times) if quote_times else "",
        "pool_count": pool_count,
        "sample_count": len(changes),
        "coverage": round(len(changes) / pool_count, 4) if pool_count else 0.0,
        "up": up,
        "down": down,
        "flat": flat,
        "limit_up": sum(1 for pct in changes if pct >= 9.8),
        "limit_down": sum(1 for pct in changes if pct <= -9.8),
        "average_change_pct": round(statistics.mean(changes), 3) if changes else None,
        "median_change_pct": round(statistics.median(changes), 3) if changes else None,
        "total_amount": round(sum(row["amount"] for row in rows), 2),
    }


CORE_INDEX_SYMBOLS = {
    "sh": "sh000001",
    "sz": "sz399001",
    "cyb": "sz399006",
}


def build_index_risk_snapshot(
    quotes: dict[str, dict[str, Any]],
    *,
    kline_loader=None,
) -> dict[str, Any]:
    """Build a compact core-index trend snapshot for the market risk gate."""
    kline_loader = kline_loader or tencent_klines
    items = []
    for key, symbol in CORE_INDEX_SYMBOLS.items():
        quote = quotes.get(symbol) if isinstance(quotes.get(symbol), dict) else {}
        price = safe_float(quote.get("price"))
        change_pct = safe_float(quote.get("change_pct"))
        rows = kline_loader(symbol, 30) or []
        completed_closes = [safe_float(row.get("close")) for row in rows[-21:-1]] if len(rows) >= 21 else []
        completed_closes = [value for value in completed_closes if value is not None and value > 0]
        ma20 = statistics.mean(completed_closes[-20:]) if len(completed_closes) >= 20 else None
        if price is None or price <= 0 or ma20 is None:
            continue
        items.append({
            "key": key,
            "symbol": symbol,
            "price": round(price, 3),
            "change_pct": round(change_pct, 3) if change_pct is not None else None,
            "ma20": round(ma20, 3),
            "below_ma20": price < ma20,
        })
    changes = [item["change_pct"] for item in items if item.get("change_pct") is not None]
    return {
        "core_indices": items,
        "core_index_count": len(items),
        "index_below_ma20_count": sum(1 for item in items if item["below_ma20"]),
        "index_average_change_pct": round(statistics.mean(changes), 3) if changes else None,
    }


def tencent_klines(symbol, count=120):
    url = f"{TENCENT_KLINE}?param={symbol},day,,,{count},qfq"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return []
    try:
        kdata = (data.get("data", {}).get(symbol, {}).get("day", []) or
                 data.get("data", {}).get(symbol, {}).get("qfqday", []))
    except Exception:
        return []
    rows = []
    for item in kdata:
        if len(item) >= 6:
            rows.append({
                "date": item[0],
                "open": float(item[1]), "close": float(item[2]),
                "high": float(item[3]), "low": float(item[4]),
                "volume": float(item[5]),
            })
    return rows


# ========== Multi-Strategy Analysis ==========

def prepare_strategy_rows(
    symbol: str,
    tencent_key: str,
    *,
    quote: dict[str, Any] | None = None,
    name: str = "",
    industry: str = "",
) -> list[dict[str, Any]] | None:
    """Fetch and enrich a stock once so cross-sectional suites can reuse it."""
    try:
        rows = tencent_klines(tencent_key, 120)
    except Exception:
        return None
    if len(rows) < 30:
        return None

    # Enrich once (BBI, J, EMA20, EMA50, change_pct)
    enrich_rows(rows)
    if rows:
        rows[-1]["symbol_code"] = symbol
        rows[-1]["stock_name"] = name or (quote or {}).get("name", "")
        rows[-1]["industry"] = normalize_industry_name(industry)
        if quote:
            rows[-1]["quote_amount"] = quote.get("amount")
            rows[-1]["quote_turnover"] = quote.get("turnover")
            rows[-1]["quote_price"] = quote.get("price")
            rows[-1]["quote_change_pct"] = quote.get("change_pct")

    return rows


def analyze_all_strategies(
    symbol,
    tencent_key,
    quote: dict[str, Any] | None = None,
    name: str = "",
    *,
    industry: str = "",
    rows: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
    scorers: dict[str, Callable[..., dict[str, Any] | None]] | None = None,
):
    """Run all active strategies, optionally in one shared cross-sectional context."""
    prepared = rows or prepare_strategy_rows(
        symbol,
        tencent_key,
        quote=quote,
        name=name,
        industry=industry,
    )
    if not prepared:
        return None

    return analyze_enriched_rows(prepared, scorers or active_strategy_scorers(), context)


def load_previous_sector_tide_market() -> dict[str, Any] | None:
    """Load only the prior persisted tide state used for two-scan confirmation."""
    try:
        payload = json.loads(MULTI_STRATEGY_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None
    context = payload.get("sector_tide_context") if isinstance(payload, dict) else None
    market = context.get("market") if isinstance(context, dict) else None
    return market if isinstance(market, dict) else None


def fetch_sector_tide_money_flow() -> dict[str, Any]:
    """Return cached industry flows; an empty result activates volume fallback."""
    try:
        from dashboard.apis.money_flow_service import fetch_money_flow

        payload = fetch_money_flow()
        return payload if isinstance(payload, dict) else {"inflow": [], "outflow": []}
    except Exception as exc:
        print(f"[WARN] sector tide money flow unavailable: {type(exc).__name__}; using volume fallback", file=sys.stderr)
        return {"inflow": [], "outflow": []}


def sector_tide_dragon_tiger_archive_dir() -> Path:
    """Resolve the archive beside the configured latest-snapshot file."""
    snapshot_file = Path(
        os.environ.get("IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE")
        or B1_OUTPUT_DIR / "iwencai_dragon_tiger_latest.json"
    ).expanduser()
    return snapshot_file.parent / "iwencai_dragon_tiger"


def load_previous_sector_tide_dragon_tiger(
    now: datetime | None = None,
    *,
    archive_dir: Path | None = None,
    status_loader: Callable[..., dict[str, Any]] | None = None,
    archive_reader: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Load only the exact prior A-share trading-day archive; never use same-day data."""
    if status_loader is None:
        from a_share_calendar import trading_day_status

        status_loader = trading_day_status
    if archive_reader is None:
        from dashboard.apis.iwencai_service import read_dragon_tiger_archive

        archive_reader = read_dragon_tiger_archive

    current = now or datetime.now()
    try:
        calendar = status_loader(current, allow_refresh=False)
    except Exception as exc:
        return {
            "available": False,
            "source": "local_dragon_tiger_archive",
            "date": "",
            "requested_date": "",
            "items": [],
            "error": f"calendar_{type(exc).__name__}",
        }
    previous_date = str(calendar.get("previous_trading_day") or "")
    unavailable = {
        "available": False,
        "source": "local_dragon_tiger_archive",
        "date": previous_date,
        "requested_date": previous_date,
        "items": [],
        "calendar_source": str(calendar.get("source") or ""),
    }
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", previous_date):
        unavailable["error"] = "previous_trading_day_unavailable"
        return unavailable
    try:
        snapshot = archive_reader(
            archive_dir or sector_tide_dragon_tiger_archive_dir(),
            trade_date=previous_date,
        )
    except Exception as exc:
        unavailable["error"] = f"archive_read_{type(exc).__name__}"
        return unavailable
    if not isinstance(snapshot, dict):
        unavailable["error"] = "archive_missing"
        return unavailable
    payload = dict(snapshot)
    payload["requested_date"] = previous_date
    payload["calendar_source"] = str(calendar.get("source") or "")
    return payload


def load_a_share_code_pool(stock_universe: object | None = None):
    """Load the configured沪深 A-share pool without pulling Beijing-board data."""
    import akshare as ak
    candidates = []
    selected = selected_stock_universe(stock_universe)

    def add(code, name):
        code = str(code or "").strip().split(".")[0].zfill(6)
        name = str(name or "").strip()
        if not code or not name:
            return
        if "退" in name or not stock_in_universe(code, name, selected):
            return
        candidates.append((code, name))

    errors = []
    try:
        sh_symbols = []
        if "main_board" in selected or "st" in selected:
            sh_symbols.append("主板A股")
        if "star_market" in selected or "st" in selected:
            sh_symbols.append("科创板")
        for symbol in sh_symbols:
            sh = ak.stock_info_sh_name_code(symbol=symbol)
            for _, row in sh.iterrows():
                add(row.get("证券代码"), row.get("证券简称"))
    except Exception as exc:
        errors.append(f"SH:{type(exc).__name__}")

    try:
        sz = ak.stock_info_sz_name_code(symbol="A股列表")
        for _, row in sz.iterrows():
            add(row.get("A股代码"), row.get("A股简称"))
    except Exception as exc:
        errors.append(f"SZ:{type(exc).__name__}")

    if not candidates:
        df = ak.stock_info_a_code_name()
        for _, row in df.iterrows():
            add(row.get("code"), row.get("name"))

    deduped = {}
    for code, name in candidates:
        deduped[code] = name
    if errors:
        print("  Code pool partial fallback: " + ", ".join(errors), file=sys.stderr)
    return sorted(deduped.items())


def load_main_board_code_pool():
    """Backward-compatible legacy pool helper."""
    return load_a_share_code_pool(DEFAULT_STOCK_UNIVERSE)


def get_margin_signal(code: str) -> dict | None:
    """获取个股融资融券信号。返回 {net_buy_ratio, signal, detail} 或 None。"""
    try:
        import akshare as ak
        from datetime import datetime as dt_mod, timedelta
        
        market = "sse" if code.startswith(('6','9')) else "szse" if code.startswith(('0','2','3')) else ""
        if not market:
            return None

        # 找最近一个可用交易日（融资数据非交易日为空）。同一轮扫描复用整张市场表，
        # 避免为每只候选重复下载相同的上交所/深交所明细。
        today = dt_mod.now()
        df = None
        for offset in range(5):
            check_date = (today - timedelta(days=offset)).strftime("%Y%m%d")
            cache_key = (market, check_date)
            df = _load_cached_market_frame(
                _MARGIN_DETAIL_CACHE,
                _MARGIN_DETAIL_CACHE_LOCK,
                cache_key,
                lambda: (
                    ak.stock_margin_detail_sse(date=check_date)
                    if market == "sse"
                    else ak.stock_margin_detail_szse(date=check_date)
                ),
            )
            if df is not None and not df.empty:
                break
        else:
            return None
        
        # 查找该股票（沪市深市列名不同）
        if code.startswith(('6','9')):
            row = df[df['标的证券代码'].astype(str).str.zfill(6) == code]
            if row.empty: return None
            r = row.iloc[0]
            buy_amt = float(r.get('融资买入额', 0) or 0)
            repay_amt = float(r.get('融资偿还额', 0) or 0)
            balance = float(r.get('融资余额', 0) or 0)
        else:
            row = df[df['证券代码'].astype(str).str.zfill(6) == code]
            if row.empty: return None
            r = row.iloc[0]
            buy_amt = float(r.get('融资买入额', 0) or 0)
            repay_amt = 0  # 深市无此字段
            balance = float(r.get('融资余额', 0) or 0)
        
        if buy_amt + repay_amt == 0 and repay_amt == 0:
            # 深市无偿还数据，仅用融资余额判断
            if balance > 1e8:
                return {"signal": "neutral", "detail": f"融资余额{balance/1e8:.1f}亿(买入{buy_amt/1e4:.0f}万)", "net_flow_wan": round(buy_amt/1e4,1)}
            return None
        elif buy_amt + repay_amt == 0:
            return None
        
        net_flow = buy_amt - repay_amt
        ratio = net_flow / balance if balance > 0 else 0
        
        if ratio > 0.03:
            signal, detail = "bullish", f"融资净买入{net_flow/1e4:.0f}万(余额{balance/1e8:.1f}亿)"
        elif ratio > 0:
            signal, detail = "slightly_bullish", f"融资小幅净买入{net_flow/1e4:.0f}万"
        elif ratio > -0.03:
            signal, detail = "slightly_bearish", f"融资小幅净偿还{abs(net_flow)/1e4:.0f}万"
        else:
            signal, detail = "bearish", f"融资净偿还{abs(net_flow)/1e4:.0f}万(余额{balance/1e8:.1f}亿)"
        
        return {"signal": signal, "detail": detail, "net_flow_wan": round(net_flow/1e4, 1)}
    except Exception:
        return None


def get_block_trade_signal(code: str, name: str = "") -> dict | None:
    """获取个股近期大宗交易信号。溢价买入=看多，折价卖出=看空。"""
    try:
        import akshare as ak
        from datetime import datetime as dt_mod, timedelta
        end = dt_mod.now().strftime("%Y%m%d")
        start = (dt_mod.now() - timedelta(days=5)).strftime("%Y%m%d")
        
        cache_key = (start, end)
        df = _load_cached_market_frame(
            _BLOCK_TRADE_CACHE,
            _BLOCK_TRADE_CACHE_LOCK,
            cache_key,
            lambda: ak.stock_dzjy_mrmx(
                symbol='A股',
                start_date=start,
                end_date=end,
            ),
        )
        if df is None or df.empty:
            return None
        
        # 匹配该股票
        matches = df[df['证券代码'].astype(str).str.zfill(6) == code]
        if matches.empty:
            return None
        
        total_amt = matches['成交额'].sum()
        avg_premium = matches['折溢率'].mean()
        count = len(matches)
        
        if avg_premium is None or not isinstance(avg_premium, (int, float)):
            return None
        
        if avg_premium > 2:
            signal, detail = "bullish", f"大宗溢价{avg_premium:+.1f}%({count}笔{total_amt/1e4:.0f}万)"
        elif avg_premium > 0.5:
            signal, detail = "slightly_bullish", f"大宗小幅溢价{avg_premium:+.1f}%({count}笔)"
        elif avg_premium < -2:
            signal, detail = "bearish", f"大宗折价{avg_premium:+.1f}%({count}笔{total_amt/1e4:.0f}万)"
        elif avg_premium < -0.5:
            signal, detail = "slightly_bearish", f"大宗小幅折价{avg_premium:+.1f}%({count}笔)"
        else:
            signal, detail = "neutral", f"大宗平价({count}笔{total_amt/1e4:.0f}万)"
        
        return {"signal": signal, "detail": detail, "count": count, "avg_premium": round(float(avg_premium), 1)}
    except Exception:
        return None


def normalize_industry_name(name: Any) -> str:
    text = str(name or "").strip()
    if not text or text.lower() in {"nan", "none", "null"} or text in {"-", "--"}:
        return ""
    text = re.sub(r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+$", "", text).strip()
    for suffix in ("行业", "板块", "概念"):
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            text = text[: -len(suffix)].strip()
    return text


def normalize_stock_code(code: Any) -> str:
    raw = str(code or "").strip()
    if not raw:
        return ""
    match = re.search(r"(\d{6})", raw)
    if match:
        return match.group(1)
    digits = re.sub(r"\D", "", raw)
    return digits.zfill(6) if digits else ""


def _record_value(row: Any, key: str) -> Any:
    if hasattr(row, "get"):
        return row.get(key)
    try:
        return row[key]
    except Exception:
        return None


def _iter_record_rows(data: Any):
    if data is None:
        return
    iterrows = getattr(data, "iterrows", None)
    if callable(iterrows):
        for _, row in iterrows():
            yield row
        return
    if isinstance(data, dict):
        yield data
        return
    try:
        for row in data:
            yield row
    except TypeError:
        return


def extract_industry_from_individual_info(info: Any) -> str:
    """Read the industry/sector name from akshare.stock_individual_info_em output."""
    direct_keys = ("行业", "所属行业", "板块", "所属板块")
    item_keys = ("item", "项目", "指标")
    value_keys = ("value", "值", "内容")

    for row in _iter_record_rows(info):
        for key in direct_keys:
            industry = normalize_industry_name(_record_value(row, key))
            if industry:
                return industry

        item_name = ""
        for key in item_keys:
            item_name = str(_record_value(row, key) or "").strip()
            if item_name:
                break
        if item_name not in direct_keys:
            continue

        for key in value_keys:
            industry = normalize_industry_name(_record_value(row, key))
            if industry:
                return industry
    return ""


def extract_industry_from_cninfo_change(info: Any) -> str:
    rows = list(_iter_record_rows(info) or [])
    standard_priority = (
        "申银万国行业分类标准",
        "中证行业分类标准",
        "巨潮行业分类标准",
        "中国上市公司协会上市公司行业分类标准",
    )
    value_keys = ("行业中类", "行业大类", "行业次类", "行业门类")

    def row_date(row: Any) -> str:
        return str(_record_value(row, "变更日期") or "")

    def row_industry(row: Any) -> str:
        for key in value_keys:
            industry = normalize_industry_name(_record_value(row, key))
            if industry:
                return industry
        return ""

    for standard in standard_priority:
        selected = [
            row for row in rows
            if standard in str(_record_value(row, "分类标准") or "")
        ]
        for row in sorted(selected, key=row_date, reverse=True):
            industry = row_industry(row)
            if industry:
                return industry

    for row in sorted(rows, key=row_date, reverse=True):
        industry = row_industry(row)
        if industry:
            return industry
    return ""


def _add_local_runtime_site_packages() -> None:
    global _LOCAL_SITE_PACKAGES_READY
    if _LOCAL_SITE_PACKAGES_READY:
        return
    _LOCAL_SITE_PACKAGES_READY = True
    version_dir = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_packages = DASHBOARD_HOME.parent / ".venv" / "lib" / version_dir / "site-packages"
    if site_packages.exists() and str(site_packages) not in sys.path:
        sys.path.insert(0, str(site_packages))


def load_stock_industry_cache() -> dict[str, str]:
    global _STOCK_INDUSTRY_MEMORY_CACHE
    if _STOCK_INDUSTRY_MEMORY_CACHE is not None:
        return dict(_STOCK_INDUSTRY_MEMORY_CACHE)
    try:
        raw = json.loads(STOCK_INDUSTRY_CACHE.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    cache = {
        normalize_stock_code(code): normalize_industry_name(industry)
        for code, industry in (raw or {}).items()
        if normalize_stock_code(code) and normalize_industry_name(industry)
    }
    _STOCK_INDUSTRY_MEMORY_CACHE = cache
    return dict(cache)


def save_stock_industry_cache(cache: dict[str, str]) -> None:
    global _STOCK_INDUSTRY_MEMORY_CACHE
    clean = {
        normalize_stock_code(code): normalize_industry_name(industry)
        for code, industry in (cache or {}).items()
        if normalize_stock_code(code) and normalize_industry_name(industry)
    }
    _STOCK_INDUSTRY_MEMORY_CACHE = clean
    try:
        STOCK_INDUSTRY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STOCK_INDUSTRY_CACHE.with_suffix(STOCK_INDUSTRY_CACHE.suffix + ".new")
        tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(STOCK_INDUSTRY_CACHE)
    except Exception as exc:
        print(f"[WARN] stock industry cache save failed: {type(exc).__name__}", file=sys.stderr)


def lookup_stock_industry(code: str, ak_module: Any | None = None) -> str:
    code = normalize_stock_code(code)
    if not code:
        return ""
    if ak_module is None:
        _add_local_runtime_site_packages()
        import akshare as ak_module

    for attempt in range(2):
        try:
            info = ak_module.stock_industry_change_cninfo(
                symbol=code,
                start_date="19900101",
                end_date=time.strftime("%Y%m%d"),
            )
            industry = extract_industry_from_cninfo_change(info)
            if industry:
                return industry
        except Exception:
            if attempt == 0:
                time.sleep(0.4)
                continue
            break

    info = ak_module.stock_individual_info_em(symbol=code)
    return extract_industry_from_individual_info(info)


def annotate_candidate_industries(
    *groups: list[dict[str, Any]],
    lookup: Callable[[str], str | None] | None = None,
    max_workers: int = 1,
) -> None:
    """Attach industry/sector labels to candidate rows without making them required."""
    missing_by_code: dict[str, list[dict[str, Any]]] = {}

    for group in groups:
        for item in group or []:
            if not isinstance(item, dict):
                continue
            industry = normalize_industry_name(
                item.get("industry") or item.get("sector") or item.get("board")
            )
            if industry:
                item["industry"] = industry
                item["sector"] = industry
                continue
            code = normalize_stock_code(item.get("code"))
            if not code:
                continue
            missing_by_code.setdefault(code, []).append(item)

    def fill_code(code: str, industry: str) -> None:
        industry = normalize_industry_name(industry)
        if not industry:
            return
        for item in missing_by_code.get(code, []):
            item["industry"] = industry
            item["sector"] = industry
        missing_by_code.pop(code, None)

    if lookup is None and missing_by_code:
        cache = load_stock_industry_cache()
        for code in list(missing_by_code):
            fill_code(code, cache.get(code, ""))

        if missing_by_code:
            cache_changed = False
            missing_codes = list(missing_by_code)
            resolved: dict[str, str] = {}
            workers = max(1, min(int(max_workers or 1), 12, len(missing_codes)))
            if workers > 1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                    future_by_code = {pool.submit(lookup_stock_industry, code): code for code in missing_codes}
                    for future in concurrent.futures.as_completed(future_by_code):
                        code = future_by_code[future]
                        try:
                            resolved[code] = normalize_industry_name(future.result())
                        except Exception:
                            resolved[code] = ""
            else:
                for code in missing_codes:
                    try:
                        resolved[code] = normalize_industry_name(lookup_stock_industry(code))
                    except Exception:
                        resolved[code] = ""
                    time.sleep(0.08)
            for code, industry in resolved.items():
                if industry:
                    cache[code] = industry
                    cache_changed = True
                    fill_code(code, industry)
            if cache_changed:
                save_stock_industry_cache(cache)
        return

    failures: list[str] = []
    for code, items in missing_by_code.items():
        try:
            industry = normalize_industry_name((lookup or lookup_stock_industry)(code))
        except Exception as exc:
            failures.append(f"{code}:{type(exc).__name__}")
            continue
        if not industry:
            continue
        for item in items:
            item["industry"] = industry
            item["sector"] = industry

    if failures:
        sample = ", ".join(failures[:5])
        more = f" (+{len(failures) - 5})" if len(failures) > 5 else ""
        print(f"[WARN] candidate industry lookup failed: {sample}{more}", file=sys.stderr)


# ========== Main ==========

def grok_industry_classify(candidates: list[dict]) -> None:
    """用 Grok 一次性查询所有候选股的行业分类。"""
    if not candidates:
        return
    try:
        import yaml
        cfg_path = Path(os.environ.get("DASHBOARD_CONFIG", DASHBOARD_HOME / "config.yaml")).expanduser()
        cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
        providers = cfg.get("custom_providers", [])
        crossdesk = next((p for p in providers if "crossdesk" in str(p.get("name","")).lower()), None)
        if not crossdesk: return
        base = crossdesk["base_url"].rstrip("/"); api_key = crossdesk["api_key"]
        stock_list = "\n".join(f"{c['code']} {c['name']}" for c in candidates)
        prompt = f"对以下A股每只给一个简短行业标签（如通信设备、半导体、汽车零部件）。只输出：代码 名称：行业\n\n{stock_list}"
        model = "grok-4.20-multi-agent-xhigh"
        model_request = build_model_request(
            base,
            model,
            [{"role": "user", "content": prompt}],
            max_tokens=200,
            api_mode="chat",
        )
        parsed = request_model(
            model_request,
            api_key,
            timeout=10,
            opener=urllib.request.urlopen,
        )
        for line in parsed.content.strip().split("\n"):
            for c in candidates:
                if c["code"] in line and c["name"] in line:
                    parts = line.split("：",1) if "：" in line else line.split(":",1) if ":" in line else [line,""]
                    if len(parts) >= 2: c["industry"] = parts[1].strip()
                    break
    except Exception: pass


def write_outputs(json_str: str, generated_at: str) -> None:
    """Write B1 cache (backward compat), multi-strategy cache, and archives."""
    B1_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Multi-strategy cache (primary)
    tmp_ms = MULTI_STRATEGY_CACHE.with_suffix(MULTI_STRATEGY_CACHE.suffix + ".new")
    tmp_ms.write_text(json_str + "\n", encoding="utf-8")
    tmp_ms.replace(MULTI_STRATEGY_CACHE)

    # B1 cache (backward compat for dashboard/现有pipeline)
    tmp_b1 = B1_CACHE_FILE.with_suffix(B1_CACHE_FILE.suffix + ".new")
    tmp_b1.write_text(json_str + "\n", encoding="utf-8")
    tmp_b1.replace(B1_CACHE_FILE)

    # Archive
    safe_ts = str(generated_at).replace(":", "-").replace(" ", "_")
    date_part = safe_ts.split("_")[0]

    for archive_dir in [B1_HISTORY_DIR, MULTI_STRATEGY_HISTORY]:
        d = archive_dir / date_part
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{safe_ts}.json"
        ft = f.with_suffix(f.suffix + ".new")
        ft.write_text(json_str + "\n", encoding="utf-8")
        ft.replace(f)


def main():
    print("Step 1: Loading A-share code pool...", file=sys.stderr)
    stock_universe = configured_stock_universe()
    candidates = load_a_share_code_pool(stock_universe)

    print(f"  Configured universe ({friendly_stock_universe(stock_universe)}): {len(candidates)} stocks", file=sys.stderr)

    print("Step 2: Fetching real-time batch quotes...", file=sys.stderr)
    tencent_keys = {}
    all_keys = []
    for code, name in candidates:
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        tk = prefix + code
        tencent_keys[code] = tk
        all_keys.append(tk)

    quotes = {}
    batch_size = 150
    for i in range(0, len(all_keys), batch_size):
        batch = all_keys[i:i + batch_size]
        q = tencent_batch_quote(batch)
        quotes.update(q)
        time.sleep(0.05)
    market_snapshot = build_market_snapshot(quotes, pool_count=len(all_keys), stock_universe=stock_universe)
    try:
        index_quotes = tencent_batch_quote(list(CORE_INDEX_SYMBOLS.values()))
        market_snapshot.update(build_index_risk_snapshot(index_quotes))
    except Exception:
        pass

    # Filter by liquidity
    liquid = []
    for code, name in candidates:
        tk = tencent_keys[code]
        q = quotes.get(tk, {})
        price = q.get("price")
        amount = q.get("amount") or 0
        if price is None or price <= 0:
            continue
        if amount < 8e8:
            continue
        liquid.append((code, name, q))

    liquid.sort(key=lambda x: x[2].get("amount", 0), reverse=True)
    top_n = min(500, len(liquid))
    to_analyze = liquid[:top_n]
    print(f"  High liquidity (成交额>8亿): {len(liquid)}, analyzing top {top_n}", file=sys.stderr)

    try:
        scan_workers = int(dashboard_env_value("DASHBOARD_B1_SCAN_WORKERS") or "6")
    except (TypeError, ValueError):
        scan_workers = 6
    scan_workers = max(1, min(16, scan_workers, len(to_analyze) or 1))
    print(
        f"Step 3: Multi-strategy scoring (registered strategy profiles, {scan_workers} workers)...",
        file=sys.stderr,
    )
    scorers = active_strategy_scorers()
    sector_tide_enabled = bool(SECTOR_TIDE_STRATEGY_IDS.intersection(scorers))
    sector_tide_context: dict[str, Any] | None = None
    prepared_by_code: dict[str, list[dict[str, Any]]] = {}
    industry_by_code: dict[str, str] = {}

    if sector_tide_enabled:
        print("  Building shared market/sector tide context...", file=sys.stderr)
        sector_members = [
            {"code": code, "name": name, "quote": q}
            for code, name, q in to_analyze
        ]
        annotate_candidate_industries(sector_members, max_workers=8)
        prepared_items: list[dict[str, Any]] = []
        for index, item in enumerate(sector_members):
            code = str(item["code"])
            name = str(item["name"])
            industry = normalize_industry_name(item.get("industry"))
            quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
            rows = prepare_strategy_rows(
                code,
                tencent_keys[code],
                quote=quote,
                name=name,
                industry=industry,
            )
            if rows:
                prepared_by_code[code] = rows
                industry_by_code[code] = industry
                prepared_items.append({
                    "code": code,
                    "name": name,
                    "industry": industry,
                    "quote": quote,
                    "rows": rows,
                })
            if (index + 1) % 50 == 0:
                print(f"  ... {index + 1}/{len(sector_members)} tide members prepared", file=sys.stderr)
            time.sleep(0.02)
        sector_tide_context = build_sector_tide_context(
            prepared_items,
            market_snapshot=market_snapshot,
            flow_rows=fetch_sector_tide_money_flow(),
            previous_market=load_previous_sector_tide_market(),
            dragon_tiger_snapshot=load_previous_sector_tide_dragon_tiger(),
        )
        market = sector_tide_context.get("market") or {}
        dragon_tiger = sector_tide_context.get("dragon_tiger") or {}
        print(
            "  Tide context: "
            f"market={market.get('state')} score={market.get('score')} "
            f"sectors={sector_tide_context.get('sector_count')} "
            f"coverage={sector_tide_context.get('data_coverage')} "
            f"dragon_tiger={dragon_tiger.get('as_of_date') or 'unavailable'} "
            f"matched={dragon_tiger.get('matched_stock_count', 0)}",
            file=sys.stderr,
        )

    def analyze_candidate(candidate):
        code, name, q = candidate
        tencent_key = tencent_keys[code]
        try:
            multi = analyze_all_strategies(
                code,
                tencent_key,
                quote=q,
                name=name,
                industry=industry_by_code.get(code, ""),
                rows=prepared_by_code.get(code),
                context=sector_tide_context,
                scorers=scorers,
            )
        except Exception as exc:
            print(
                f"[WARN] candidate analysis failed for {code}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return None
        if multi is None:
            return None
        # Backward compat fields
        best = multi["strategies"].get(multi["best_strategy"], {})
        return {
            "code": code,
            "name": name,
            **stock_universe_metadata(code, name),
            "price": q.get("price"),
            "change_pct": q.get("change_pct"),
            "amount": q.get("amount"),
            "amount_yi": round(q.get("amount", 0) / 1e8, 1) if q.get("amount") else None,
            "turnover": q.get("turnover"),
            "industry": best.get("industry") or industry_by_code.get(code, ""),
            "sector": best.get("industry") or industry_by_code.get(code, ""),
            # backward compat (the practice candidates panel expects these)
            "score": best.get("score", 0),
            "score_total": best.get("score_total", 10),
            "verdict": best.get("verdict", ""),
            "bbi": best.get("bbi"),
            "distance_pct": best.get("distance_pct"),
            "bbi_upward": best.get("bbi_upward", False),
            "above_bbi": best.get("above_bbi", False),
            "min_j_10d": best.get("min_j_10d"),
            "current_j": best.get("current_j"),
            "j_recovering": best.get("j_recovering", False),
            "j_oversold": best.get("j_oversold", False),
            "risk_flags": best.get("risk_flags", []),
            "change_pct": q.get("change_pct"),
            # multi-strategy fields
            "best_strategy": multi["best_strategy"],
            "best_score": multi["best_score"],
            "best_decision_score": multi.get("best_decision_score", multi["best_score"]),
            "best_verdict": multi["best_verdict"],
            "entry_threshold": best.get("entry_threshold"),
            "strategy_priority": best.get("strategy_priority"),
            "score_basis": best.get("score_basis"),
            "position_hint": best.get("position_hint"),
            "time_stop": best.get("time_stop"),
            "actionable": best.get("actionable"),
            "hard_blockers": best.get("hard_blockers", []),
            "market_regime": best.get("market_regime"),
            "market_score": best.get("market_score"),
            "market_hard_stop": best.get("market_hard_stop"),
            "market_allows_buys": best.get("market_allows_buys"),
            "sector_status": best.get("sector_status"),
            "sector_score": best.get("sector_score"),
            "sector_rank_acceleration": best.get("sector_rank_acceleration"),
            "sector_breadth20": best.get("sector_breadth20"),
            "stock_sector_rank": best.get("stock_sector_rank"),
            "stock_market_rank": best.get("stock_market_rank"),
            "score_before_dragon_tiger": best.get("score_before_dragon_tiger"),
            "dragon_tiger_available": best.get("dragon_tiger_available"),
            "dragon_tiger_as_of_date": best.get("dragon_tiger_as_of_date"),
            "dragon_tiger_source": best.get("dragon_tiger_source"),
            "dragon_tiger_seat_data_complete": best.get("dragon_tiger_seat_data_complete"),
            "dragon_tiger_listed": best.get("dragon_tiger_listed"),
            "dragon_tiger_score": best.get("dragon_tiger_score"),
            "dragon_tiger_signal": best.get("dragon_tiger_signal"),
            "dragon_tiger_confidence": best.get("dragon_tiger_confidence"),
            "dragon_tiger_adjustment": best.get("dragon_tiger_adjustment"),
            "dragon_tiger_positive_suppressed": best.get("dragon_tiger_positive_suppressed"),
            "dragon_tiger_net_amount_yuan": best.get("dragon_tiger_net_amount_yuan"),
            "dragon_tiger_net_ratio_pct": best.get("dragon_tiger_net_ratio_pct"),
            "dragon_tiger_seat_net_amount_yuan": best.get("dragon_tiger_seat_net_amount_yuan"),
            "dragon_tiger_institution_net_amount_yuan": best.get("dragon_tiger_institution_net_amount_yuan"),
            "dragon_tiger_seat_record_count": best.get("dragon_tiger_seat_record_count"),
            "dragon_tiger_institution_record_count": best.get("dragon_tiger_institution_record_count"),
            "sector_dragon_tiger_score": best.get("sector_dragon_tiger_score"),
            "sector_dragon_tiger_adjustment": best.get("sector_dragon_tiger_adjustment"),
            "sector_dragon_tiger_listed_count": best.get("sector_dragon_tiger_listed_count"),
            "ema20": best.get("ema20"),
            "ema50": best.get("ema50"),
            "atr20": best.get("atr20"),
            "stop_price": best.get("stop_price"),
            "stop_source": best.get("stop_source"),
            "stop_distance_pct": best.get("stop_distance_pct"),
            "stop_atr": best.get("stop_atr"),
            "gap_buffer_pct": best.get("gap_buffer_pct"),
            "execution_buffer_pct": best.get("execution_buffer_pct"),
            "effective_loss_distance_pct": best.get("effective_loss_distance_pct"),
            "per_trade_risk_budget_pct": best.get("per_trade_risk_budget_pct"),
            "max_open_risk_pct": best.get("max_open_risk_pct"),
            "max_sector_risk_pct": best.get("max_sector_risk_pct"),
            "max_total_position_pct": best.get("max_total_position_pct"),
            "max_sector_position_pct": best.get("max_sector_position_pct"),
            "absolute_position_cap_pct": best.get("absolute_position_cap_pct"),
            "max_position_pct_by_risk": best.get("max_position_pct_by_risk"),
            "risk_ok": best.get("risk_ok"),
            "trade_ready": candidate_is_trade_ready(best),
            "strategies": multi["strategies"],
            "consensus_count": multi.get("consensus_count", 0),
            "consensus_boost": multi.get("consensus_boost", 0),
        }

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=scan_workers) as pool:
        for completed, item in enumerate(pool.map(analyze_candidate, to_analyze), 1):
            if item is not None:
                results.append(item)
            if completed % 50 == 0:
                print(f"  ... {completed}/{len(to_analyze)} analyzed", file=sys.stderr)

    # Sort: best_score desc, above_bbi bonus, closer to BBI better
    def sort_key(item):
        s = item.get("best_decision_score") or item["best_score"]
        above = 1 if item.get("above_bbi") else 0
        dist = abs(item.get("distance_pct") or 99)
        return (s, above, -dist)

    results.sort(key=sort_key, reverse=True)
    display_candidates = select_display_candidates(results)
    trade_candidates = select_trade_candidates(results)
    annotate_candidate_industries(display_candidates, trade_candidates)

    print(f"  Analyzed: {len(results)} stocks", file=sys.stderr)
    print(f"  Strategy distribution:", file=sys.stderr)
    from collections import Counter
    strat_counts = Counter(r["best_strategy"] for r in results)
    for k, v in strat_counts.most_common():
        print(f"    {active_strategy_meta().get(k, {}).get('label', k)}: {v}", file=sys.stderr)

    # Output
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    
    # 融资 + 大宗交易信号（优先展示候选）
    for item in display_candidates[:10]:
        try:
            ms = get_margin_signal(item["code"])
            if ms: item["margin_signal"] = ms
        except Exception: pass
        try:
            bt = get_block_trade_signal(item["code"])
            if bt: item["block_trade_signal"] = bt
        except Exception: pass
    
    output = {
        "generated_at": generated_at,
        "stock_universe": list(stock_universe),
        "stock_universe_label": friendly_stock_universe(stock_universe),
        "items": display_candidates,
        "candidates": display_candidates,
        "count": len(display_candidates),
        "trade_items": trade_candidates,
        "trade_count": len(trade_candidates),
        "total_analyzed": len(results),
        "strategy_distribution": dict(strat_counts),
        "strategy_meta": active_strategy_meta(),
        "strategy_score_profiles": active_strategy_score_profiles(),
        "market_snapshot": market_snapshot,
    }
    if sector_tide_context is not None:
        output["sector_tide_context"] = sector_tide_context
    json_str = json.dumps(output, ensure_ascii=False, indent=2)
    print(json_str)
    write_outputs(json_str, generated_at)


if __name__ == "__main__":
    main()
