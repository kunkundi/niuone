#!/usr/bin/env python3
"""实战页面：A股模拟账户 + 实战候选后的模型决策。

This is a paper-trading simulator, not a real broker integration.
Rules implemented:
- Initial capital: 1,000,000 CNY
- A-share round lot: buy in 100-share lots
- T+1: shares bought today cannot be sold today
- No shorting, no negative cash
- Only book simulated fills during A-share executable windows on weekdays
- Model decision provider: OpenAI-compatible chat/completions service; DeepSeek is the default recommendation
"""
from __future__ import annotations

import concurrent.futures
import json
import math
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any

from a_share_calendar import is_a_share_trading_day as calendar_is_a_share_trading_day, trading_day_status
from niuone_paths import get_dashboard_env_file, get_dashboard_home
from screening.stock_universe import (
    STOCK_UNIVERSE_ENV,
    friendly_stock_universe,
    selected_stock_universe,
    stock_in_universe,
)
from strategies.registry import (
    ACTIVE_STRATEGY_ENV,
    PRESET_STRATEGY_TEXT_ENV,
    PERSONA_STRATEGY_ENV,
    STRATEGY_SOURCE_ENV,
    STRATEGY_SOURCE_PRESET_TEXT,
    TRADE_DISCIPLINE_TEXT_ENV,
    STRATEGY_DEFINITIONS,
    STRATEGY_POSITION_LIMIT_PCT,
    active_strategy_source,
    active_strategy_suite,
    classify_strategy_text,
    decode_trade_discipline_text,
    default_trade_discipline_text,
    decode_preset_strategy_text,
    enabled_strategy_ids,
    known_strategy_ids,
    strategy_prompt_labels,
)
from strategies.attribution import (
    EXIT_RULE_LABELS,
    _append_strategy_mark_history,
    apply_entry_strategy_mark,
    apply_exit_strategy_mark,
    build_entry_strategy_mark,
    build_exit_strategy_mark,
    buy_strategy_label,
    classify_buy_strategy,
    classify_exit_rule,
    compact_position_strategy_mark,
)
from strategies.exits import evaluate_strategy_time_exit
from strategies.performance import (
    _add_perf_open_position,
    _add_perf_trade,
    _empty_perf_bucket,
    _finalize_perf,
    latest_buy_strategy_for_code,
    track_strategy_performance,
)
from strategies.policy import (
    candidate_buy_blockers as _strategy_candidate_buy_blockers,
    strategy_position_limit_pct as _strategy_position_limit_pct,
)
from strategies.prompts import build_strategy_prompt_sections, format_preset_strategy_section
from strategies.scoring.common import find_n_structure_prior_low as _find_n_structure_prior_low
from strategies.sector_tide_risk import (
    SECTOR_TIDE_EXECUTION_BUFFER_PCT,
    effective_loss_distance_pct,
    position_open_risk_pct,
    risk_sized_position_cap_pct,
    sector_tide_risk_budget,
    stored_position_effective_loss_distance_pct,
    structural_stop_distance_pct,
)
if "_sell_signals" not in globals():
    from trading import sell_signals as _sell_signals

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


def env_int(name: str, default: int) -> int:
    try:
        value = os.environ.get(name)
        return int(value) if value else default
    except (TypeError, ValueError):
        return default


def env_token_count(name: str, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    compact = raw.replace(",", "").replace("_", "").strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", compact)
    if not match:
        return default
    number = float(match.group(1))
    unit = match.group(2).lower()
    multiplier = 1_000_000 if unit == "m" else 1_000 if unit == "k" else 1
    value = int(number * multiplier)
    return value if value > 0 else default


def env_float(name: str, default: float) -> float:
    try:
        value = os.environ.get(name)
        return float(value) if value else default
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def env_hhmm(name: str, default: str) -> dtime:
    raw = str(os.environ.get(name) or default).strip()
    if not re.fullmatch(r"\d{1,2}:\d{2}", raw):
        raw = default
    try:
        hour, minute = [int(part) for part in raw.split(":", 1)]
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return dtime(hour, minute)
    except Exception:
        pass
    hour, minute = [int(part) for part in default.split(":", 1)]
    return dtime(hour, minute)


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)


def load_dashboard_env() -> None:
    allowed = {
        "DASHBOARD_NEWS_MODEL",
        "DASHBOARD_NEWS_CONTEXT_LENGTH",
        "DASHBOARD_NEWS_MAX_TOKENS",
        "DASHBOARD_NEWS_BASE_URL",
        "DASHBOARD_NEWS_API_KEY",
        "DASHBOARD_NEWS_TIMEOUT",
        "DASHBOARD_NEWS_MAX_RETRIES",
        "DASHBOARD_NEWS_CONCURRENCY",
        "DASHBOARD_DECISION_MODEL",
        "DASHBOARD_DECISION_CONTEXT_LENGTH",
        "DASHBOARD_DECISION_BASE_URL",
        "DASHBOARD_DECISION_API_KEY",
        "DASHBOARD_DECISION_MAX_TOKENS",
        "DASHBOARD_DECISION_TIMEOUT",
        "DASHBOARD_DECISION_INTELLIGENCE_ENABLED",
        "DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS",
        "DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS",
        "DASHBOARD_NOTIFICATION_ENABLED",
        "DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS",
        "DASHBOARD_FEISHU_NOTIFICATION_ENABLED",
        "DASHBOARD_FEISHU_WEBHOOK_URL",
        "DASHBOARD_FEISHU_SIGNING_SECRET",
        "DASHBOARD_DINGTALK_NOTIFICATION_ENABLED",
        "DASHBOARD_DINGTALK_WEBHOOK_URL",
        "DASHBOARD_DINGTALK_SIGNING_SECRET",
        "DASHBOARD_WECOM_NOTIFICATION_ENABLED",
        "DASHBOARD_WECOM_WEBHOOK_URL",
        "DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED",
        "DASHBOARD_TELEGRAM_BOT_TOKEN",
        "DASHBOARD_TELEGRAM_CHAT_ID",
        "DASHBOARD_B3_EXIT_TIME",
        "DASHBOARD_TIME_EXIT_TIME",
        "DASHBOARD_TIME_STOP_EXIT_TIME",
        "DASHBOARD_MAX_OPEN_POSITIONS",
        "DASHBOARD_MAX_NEW_BUYS_PER_DECISION",
        "DASHBOARD_MAX_SINGLE_POSITION_PCT",
        "DASHBOARD_MAX_TOTAL_POSITION_PCT",
        "DASHBOARD_MIN_CASH_RESERVE_PCT",
        "DASHBOARD_MARKET_GUIDANCE_ENABLED",
        "DASHBOARD_MORNING_MAX_OPEN_POSITIONS",
        STOCK_UNIVERSE_ENV,
        STRATEGY_SOURCE_ENV,
        PERSONA_STRATEGY_ENV,
        ACTIVE_STRATEGY_ENV,
        PRESET_STRATEGY_TEXT_ENV,
        TRADE_DISCIPLINE_TEXT_ENV,
        "CROSSDESK_BASE_URL",
        "CROSSDESK_API_KEY",
    }
    path = get_dashboard_env_file(PROJECT_ROOT)
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in allowed or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("\"'")


load_dashboard_env()
STATE_FILE = Path(os.environ.get("DASHBOARD_PORTFOLIO_STATE", DASHBOARD_HOME / "cron" / "output" / "niuniu_practice_portfolio.json")).expanduser()
MULTI_STRATEGY_CACHE_FILE = DASHBOARD_HOME / "cron" / "output" / "multi_strategy_latest.json"
CONFIG_PATH = Path(os.environ.get("DASHBOARD_CONFIG", DASHBOARD_HOME / "config.yaml")).expanduser()
STOCK_TOOLS_SCRIPT = Path(
    os.environ.get("DASHBOARD_CN_STOCK_TOOLS", SCRIPT_DIR / "entrypoints" / "cn_stock_tools.py")
).expanduser()
INITIAL_CASH = 1_000_000.0
# 交易费率：万一免五 = 佣金 0.01%，免 5 元最低佣金。
# A股另计：印花税仅卖出 0.05%，过户费双向 0.001%。
COMMISSION_RATE = 0.0001
COMMISSION_MIN = 0.0
STAMP_DUTY_SELL_RATE = 0.0005
TRANSFER_FEE_RATE = 0.00001
REALTIME_QUOTE_MAX_AGE_SECONDS = 8
EQUITY_HEARTBEAT_MIN_SECONDS = 60
INTRADAY_CACHE_TTL_SECONDS = 45
INTRADAY_MAX_POINTS = 260
TENCENT_MINUTE_URL = "https://ifzq.gtimg.cn/appstock/app/minute/query"
INTRADAY_CACHE: dict[str, dict[str, Any]] = {}


# ====== 大盘环境提示 ======

MARKET_ENV_CACHE: dict[str, Any] = {"ts": 0.0, "bullish": True, "index": "", "ema20": 0.0, "close": 0.0}
MARKET_ENV_TTL_SECONDS = 300  # 5分钟缓存
MARKET_SENTIMENT_CACHE: dict[str, Any] = {"ts": 0.0, "limit_up_count": 0, "sentiment": "neutral", "detail": ""}
MARKET_SENTIMENT_TTL = 600  # 10分钟缓存


def check_market_sentiment() -> dict[str, Any]:
    """用涨停家数代理市场情绪。>80热 / 30-80中性 / <30冷"""
    global MARKET_SENTIMENT_CACHE
    now_ts_val = time.time()
    if now_ts_val - MARKET_SENTIMENT_CACHE.get("ts", 0) < MARKET_SENTIMENT_TTL:
        return dict(MARKET_SENTIMENT_CACHE)
    
    try:
        import akshare as ak
        today_str = datetime.now().strftime("%Y%m%d")
        df = ak.stock_zt_pool_em(date=today_str)
        zt_count = len(df) if df is not None else 0
        
        if zt_count >= 80:
            sentiment = "hot"
            detail = f"涨停{zt_count}家→市场🔥活跃，可积极建仓"
        elif zt_count >= 30:
            sentiment = "neutral"
            detail = f"涨停{zt_count}家→市场正常，正常建仓"
        else:
            sentiment = "cold"
            detail = f"涨停{zt_count}家→市场🥶冷清，谨慎建仓"
        
        # 统计热门板块（从涨停股中提取行业）
        hot_sectors = []
        if df is not None and not df.empty:
            from collections import Counter
            sector_counts = Counter()
            for _, row in df.iterrows():
                sector = str(row.get("所属行业", "")).strip()
                if sector and sector != "nan":
                    sector_counts[sector] += 1
            hot_sectors = [f"{s}({c}只)" for s, c in sector_counts.most_common(5)]
        
        MARKET_SENTIMENT_CACHE = {
            "ts": now_ts_val, "limit_up_count": zt_count,
            "sentiment": sentiment, "detail": detail,
            "hot_sectors": hot_sectors,
        }
    except Exception as e:
        MARKET_SENTIMENT_CACHE = {
            "ts": now_ts_val, "limit_up_count": 0,
            "sentiment": "unknown", "detail": f"情绪数据获取失败({e})",
            "hot_sectors": [],
        }
    
    return dict(MARKET_SENTIMENT_CACHE)

def check_market_environment() -> dict[str, Any]:
    """检查A股大盘环境，供模型参考。
    
    返回 {"bullish": bool, "index": str, "detail": str}
    """
    global MARKET_ENV_CACHE
    now_ts_val = time.time()
    if now_ts_val - MARKET_ENV_CACHE.get("ts", 0) < MARKET_ENV_TTL_SECONDS:
        return dict(MARKET_ENV_CACHE)
    
    try:
        import urllib.request as _ur
        url = "https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,60,qfq"
        req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _ur.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        kdata = data.get("data", {}).get("sh000001", {}).get("day", []) or \
                data.get("data", {}).get("sh000001", {}).get("qfqday", [])
        if len(kdata) < 25:
            raise RuntimeError("K线不足")
        
        closes = [float(item[2]) for item in kdata if len(item) >= 6]
        # EMA20
        k = 2 / 21
        ema = closes[0]
        for c in closes[1:]:
            ema = c * k + ema * (1 - k)
        
        latest_close = closes[-1]
        bullish = latest_close > ema
        detail = f"上证{latest_close:.0f} {'>' if bullish else '<'} EMA20({ema:.0f})"
        
        MARKET_ENV_CACHE = {
            "ts": now_ts_val, "bullish": bullish,
            "index": "sh000001", "ema20": round(ema, 2),
            "close": round(latest_close, 2), "detail": detail,
        }
    except Exception as e:
        # 获取失败时默认允许交易（避免阻断）
        MARKET_ENV_CACHE = {
            "ts": now_ts_val, "bullish": True, "index": "sh000001",
            "detail": f"指数数据获取失败({e})，默认放行",
        }
    
    return dict(MARKET_ENV_CACHE)


# ====== 止盈止损规则 ======
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
SINA_QUOTE_URL = "https://hq.sinajs.cn/list="
EASTMONEY_STOCK_URL = "https://push2.eastmoney.com/api/qt/stock/get"
EASTMONEY_UT = "bd1d9ddb04089700cf9c27f6f7426281"
MODEL = os.environ.get("DASHBOARD_DECISION_MODEL") or "deepseek-v4-pro"
DECISION_CONTEXT_LENGTH = env_token_count("DASHBOARD_DECISION_CONTEXT_LENGTH", 128000)
DECISION_MAX_TOKENS = env_int("DASHBOARD_DECISION_MAX_TOKENS", 4096)
DECISION_REQUEST_TIMEOUT = env_int("DASHBOARD_DECISION_TIMEOUT", 180)
NEWS_PRECHECK_REQUEST_TIMEOUT = max(5, env_int("DASHBOARD_NEWS_TIMEOUT", 45))
NEWS_PRECHECK_MAX_RETRIES = max(1, env_int("DASHBOARD_NEWS_MAX_RETRIES", 1))
NEWS_PRECHECK_CONCURRENCY = max(1, min(5, env_int("DASHBOARD_NEWS_CONCURRENCY", 5)))
NEWS_PRECHECK_CONTEXT_LENGTH = env_token_count("DASHBOARD_NEWS_CONTEXT_LENGTH", 128000)
NEWS_PRECHECK_MAX_TOKENS = env_token_count("DASHBOARD_NEWS_MAX_TOKENS", 4096)
PROVIDER_DISPLAY_NAME = "Crossdesk.ccwu.cc"
CROSSDESK_PROVIDER_NAME = "Crossdesk.ccwu.cc"
TRADE_LOG_LIMIT = 200
EQUITY_HISTORY_LIMIT = 500


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _notify_trade_executions_safely(executed: list[dict[str, Any]]) -> None:
    """Fan out persisted simulated fills without affecting trade execution."""
    if not executed:
        return
    try:
        from notifications import notify_trade_executions

        results = notify_trade_executions(executed)
        failed_count = sum(1 for result in (results or []) if not bool(getattr(result, "ok", False)))
        if failed_count:
            print(
                f"[WARN] 交易通知有 {failed_count} 个渠道发送失败",
                file=sys.stderr,
                flush=True,
            )
    except Exception as exc:
        try:
            # Malformed third-party responses can echo credentials. Only log the
            # exception class here; channel-level errors are already sanitized.
            print(f"[WARN] 交易通知发送失败: {type(exc).__name__}", file=sys.stderr, flush=True)
        except Exception:
            pass


def today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def is_a_share_trading_day(dt: datetime | None = None) -> bool:
    return calendar_is_a_share_trading_day(dt or datetime.now())


def is_a_share_trading_time(dt: datetime | None = None) -> tuple[bool, str]:
    """A-share market trading/auction clock, including the opening auction."""
    dt = dt or datetime.now()
    if not is_a_share_trading_day(dt):
        return False, "非A股交易日"
    t = dt.time()
    if dtime(9, 15) <= t < dtime(9, 25):
        return True, "早盘集合竞价申报时段"
    if dtime(9, 30) <= t <= dtime(11, 30):
        return True, "上午连续竞价交易时段"
    if dtime(13, 0) <= t < dtime(14, 57):
        return True, "下午连续竞价交易时段"
    if dtime(14, 57) <= t <= dtime(15, 0):
        return True, "尾盘集合竞价交易时段"
    return False, "非A股交易时段（09:25-09:30为开盘集合竞价静默期；连续竞价09:30开始）"


def is_a_share_execution_time(dt: datetime | None = None) -> tuple[bool, str]:
    """Whether the paper account may immediately book a simulated fill.

    Opening auction orders are not modeled as instant fills: 09:15-09:25 only
    accepts auction declarations, and 09:25-09:30 is a quiet period before
    continuous auction starts.
    """
    dt = dt or datetime.now()
    if not is_a_share_trading_day(dt):
        return False, "非A股交易日"
    t = dt.time()
    if dtime(9, 15) <= t < dtime(9, 25):
        return False, "早盘集合竞价申报时段，仅记录观察/委托参考，不模拟即时成交"
    if dtime(9, 25) <= t < dtime(9, 30):
        return False, "开盘集合竞价静默期（09:25-09:30），不接受申报且不模拟成交"
    if dtime(9, 30) <= t <= dtime(11, 30):
        return True, "上午连续竞价交易时段"
    if dtime(13, 0) <= t < dtime(14, 57):
        return True, "下午连续竞价交易时段"
    if dtime(14, 57) <= t <= dtime(15, 0):
        return True, "尾盘集合竞价交易时段"
    return False, "非A股可成交时段（模拟成交仅允许09:30-11:30、13:00-15:00）"


def is_a_share_auction_time(dt: datetime | None = None) -> bool:
    dt = dt or datetime.now()
    if not is_a_share_trading_day(dt):
        return False
    t = dt.time()
    return dtime(9, 15) <= t < dtime(9, 30) or dtime(14, 57) <= t <= dtime(15, 0)


def is_time_exit_check_time(dt: datetime | None = None) -> bool:
    dt = dt or datetime.now()
    if not is_a_share_trading_day(dt):
        return False
    return TIME_EXIT_TIME <= dt.time() <= dtime(15, 0)


def is_b3_exit_check_time(dt: datetime | None = None) -> bool:
    dt = dt or datetime.now()
    if not is_a_share_trading_day(dt):
        return False
    return dt.strftime("%H:%M") == B3_EXIT_HHMM


def is_time_stop_exit_check_time(dt: datetime | None = None) -> bool:
    return is_time_exit_check_time(dt)


def is_a_share_session_clock(dt: datetime | None = None) -> bool:
    """Full A-share dashboard session clock: 09:15-15:00, including auction and lunch break."""
    dt = dt or datetime.now()
    if not is_a_share_trading_day(dt):
        return False
    return dtime(9, 15) <= dt.time() <= dtime(15, 0)


def parse_ts(value: str) -> datetime | None:
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def current_session_minute(dt: datetime | None = None) -> int:
    """Return the latest valid A-share minute that should exist at this clock time."""
    dt = dt or datetime.now()
    t = dt.time()
    if t < dtime(9, 30):
        return -1
    if t <= dtime(11, 30):
        return int((dt.hour * 60 + dt.minute) - (9 * 60 + 30))
    if t < dtime(13, 0):
        return 120
    if t <= dtime(15, 0):
        return 120 + int((dt.hour * 60 + dt.minute) - (13 * 60))
    return 240


def prune_future_intraday_equity_points(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
    grace_seconds: int = 120,
) -> bool:
    """Drop same-day equity points that are ahead of the dashboard clock.

    Tencent minute data can briefly serve the previous full trading day before
    today's stream is populated. Those points used to be relabeled as today and
    made the intraday account curve show 15:00 before the session got there.
    """
    now = now or datetime.now()
    cutoff = now.timestamp() + max(0, int(grace_seconds or 0))
    changed = False
    for key in ("equity_history", "daily_equity_history"):
        history = state.get(key)
        if not isinstance(history, list):
            continue
        kept: list[Any] = []
        for point in history:
            if not isinstance(point, dict):
                kept.append(point)
                continue
            dt = parse_ts(str(point.get("time") or ""))
            if dt is None:
                kept.append(point)
                continue
            if dt.date() > now.date() or (dt.date() == now.date() and dt.timestamp() > cutoff):
                changed = True
                continue
            kept.append(point)
        if len(kept) != len(history):
            state[key] = kept[-(2000 if key == "equity_history" else EQUITY_HISTORY_LIMIT):]
    return changed


def prune_non_trading_day_equity_points(state: dict[str, Any]) -> bool:
    changed = False
    for key in ("equity_history", "daily_equity_history"):
        history = state.get(key)
        if not isinstance(history, list):
            continue
        kept: list[Any] = []
        for point in history:
            if not isinstance(point, dict):
                kept.append(point)
                continue
            dt = parse_ts(str(point.get("time") or ""))
            if dt is not None and not is_a_share_trading_day(dt):
                changed = True
                continue
            kept.append(point)
        if len(kept) != len(history):
            state[key] = kept[-(2000 if key == "equity_history" else EQUITY_HISTORY_LIMIT):]
    return changed


def normalize_daily_equity_history(state: dict[str, Any]) -> bool:
    history = state.get("daily_equity_history")
    if not isinstance(history, list):
        return False
    by_date: dict[str, dict[str, Any]] = {}
    for point in history:
        if not isinstance(point, dict):
            continue
        date = str(point.get("time") or "")[:10]
        if not date:
            continue
        prev = by_date.get(date)
        if prev is None or str(point.get("time") or "") >= str(prev.get("time") or ""):
            by_date[date] = point
    normalized = [by_date[date] for date in sorted(by_date.keys())][-EQUITY_HISTORY_LIMIT:]
    if normalized == history:
        return False
    state["daily_equity_history"] = normalized
    return True


def sort_equity_history(state: dict[str, Any]) -> bool:
    changed = False
    for key in ("equity_history", "daily_equity_history"):
        history = state.get(key)
        if not isinstance(history, list):
            continue
        sorted_history = sorted(
            history,
            key=lambda point: str(point.get("time") or "") if isinstance(point, dict) else "",
        )
        if sorted_history != history:
            state[key] = sorted_history[-(2000 if key == "equity_history" else EQUITY_HISTORY_LIMIT):]
            changed = True
    return changed


def default_state() -> dict[str, Any]:
    return {
        "created_at": now_ts(),
        "updated_at": now_ts(),
        "initial_cash": INITIAL_CASH,
        "cash": INITIAL_CASH,
        "positions": {},
        "trade_log": [],
        "decision_log": [],
        "pending_decisions": [],
        "equity_history": [],
        "last_b1_generated_at": "",
        "last_decision_at": "",
        "last_error": "",
    }


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        state = default_state()
        save_state(state)
        return state
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:
        state = default_state()
    base = default_state()
    base.update(state)
    base.setdefault("positions", {})
    base.setdefault("trade_log", [])
    base.setdefault("decision_log", [])
    base.setdefault("pending_decisions", [])
    base.setdefault("equity_history", [])
    return base


_STATE_FILE_THREAD_LOCK = threading.RLock()


@contextmanager
def state_file_write_lock():
    """Serialize portfolio state read/merge/write cycles across threads and processes."""
    lock_file = STATE_FILE.with_name(f"{STATE_FILE.name}.lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with _STATE_FILE_THREAD_LOCK:
        with lock_file.open("a+b") as handle:
            if os.name == "nt":  # pragma: no cover - exercised on Windows deployments
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def reconcile_positions_with_trade_log(state: dict[str, Any]) -> list[str]:
    """Prevent a stale snapshot from resurrecting positions already sold.

    Only reconcile codes whose retained ledger starts with a BUY, so a trimmed
    legacy log that begins mid-position cannot incorrectly remove holdings.
    The function is deliberately one-way: it may reduce a stale position to
    the ledger quantity, but never creates or increases a position.
    """
    def trade_shares(value: Any) -> int:
        try:
            return max(0, int(float(value or 0)))
        except (TypeError, ValueError):
            return 0

    positions = state.get("positions") or {}
    trades_by_code: dict[str, list[dict[str, Any]]] = {}
    for trade in state.get("trade_log") or []:
        if not isinstance(trade, dict):
            continue
        action = str(trade.get("action") or "").upper()
        code = normalize_code(str(trade.get("code") or ""))
        shares = trade_shares(trade.get("shares"))
        if action not in {"BUY", "SELL"} or not code or shares <= 0:
            continue
        trades_by_code.setdefault(code, []).append(trade)

    reconciled: list[str] = []
    for code in list(positions):
        ledger = sorted(
            trades_by_code.get(normalize_code(code), []),
            key=lambda item: str(item.get("time") or ""),
        )
        if not ledger or str(ledger[0].get("action") or "").upper() != "BUY":
            continue
        ledger_qty = 0
        for trade in ledger:
            shares = trade_shares(trade.get("shares"))
            if str(trade.get("action") or "").upper() == "BUY":
                ledger_qty += shares
            else:
                ledger_qty -= shares
        ledger_qty = max(0, ledger_qty)
        position = positions.get(code) or {}
        current_qty = position_qty(position)
        if ledger_qty >= current_qty:
            continue
        if ledger_qty <= 0:
            positions.pop(code, None)
        else:
            position["qty"] = ledger_qty
            position.pop("shares", None)
            lots = position.get("buy_date_lots") or {}
            excess = max(0, sum(trade_shares(qty) for qty in lots.values()) - ledger_qty)
            for day in sorted(list(lots)):
                if excess <= 0:
                    break
                use = min(trade_shares(lots.get(day)), excess)
                lots[day] = trade_shares(lots.get(day)) - use
                excess -= use
                if lots[day] <= 0:
                    lots.pop(day, None)
        reconciled.append(code)
    state["positions"] = positions
    return reconciled


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with state_file_write_lock():
        state["updated_at"] = now_ts()

        # Merge append-only logs with the on-disk copy before replacing the file.
        # The lock must cover the complete read/merge/replace transaction. Atomic
        # replace alone prevents partial JSON, but it does not prevent a slower
        # writer that loaded stale state from replacing a newer decision record.
        if STATE_FILE.exists():
            current = json.loads(STATE_FILE.read_text())

            def merge_list(key: str, identity_fields: tuple[str, ...], prefer_state: bool = False) -> None:
                merged = []
                seen = set()
                first = state.get(key) if prefer_state else current.get(key)
                second = current.get(key) if prefer_state else state.get(key)
                for item in (first or []) + (second or []):
                    if not isinstance(item, dict):
                        continue
                    ident = tuple(json.dumps(item.get(f, ""), ensure_ascii=False, sort_keys=True) for f in identity_fields)
                    if ident in seen:
                        continue
                    seen.add(ident)
                    merged.append(item)
                state[key] = merged

            state_trade_ids = {
                tuple(json.dumps(item.get(f, ""), ensure_ascii=False, sort_keys=True)
                      for f in ("time", "action", "code", "shares", "price", "reason"))
                for item in (state.get("trade_log") or [])
                if isinstance(item, dict)
            }
            current_has_unseen_trades = any(
                tuple(json.dumps(item.get(f, ""), ensure_ascii=False, sort_keys=True)
                      for f in ("time", "action", "code", "shares", "price", "reason")) not in state_trade_ids
                for item in (current.get("trade_log") or [])
                if isinstance(item, dict)
            )
            if current_has_unseen_trades:
                # A slow dashboard quote refresh can save an old portfolio after
                # the trade engine has already appended fills. Keep the traded
                # cash/positions from disk; quote refresh can safely run again.
                state["cash"] = current.get("cash", state.get("cash"))
                state["positions"] = current.get("positions", state.get("positions", {}))

            merge_list("decision_log", ("time", "b1_generated_at", "decision"))
            merge_list("trade_log", ("time", "action", "code", "shares", "price", "reason"))
            merge_list("pending_decisions", ("id",), prefer_state=True)
            merge_list("equity_history", ("time",), prefer_state=True)
            merge_list("daily_equity_history", ("time",), prefer_state=True)

            # Position snapshots are mutable and can be stale even when the
            # append-only trade merge succeeded. Re-apply the retained ledger
            # after merging so a completed SELL cannot be resurrected.
            reconcile_positions_with_trade_log(state)

            # Preserve the newest decision marker and its error as one logical
            # value. A stale quote refresh must not clear an error written by a
            # newer decision; a later successful decision may clear it.
            state_decision_at = str(state.get("last_decision_at") or "")
            current_decision_at = str(current.get("last_decision_at") or "")
            if current_decision_at > state_decision_at:
                state["last_decision_at"] = current.get("last_decision_at")
                state["last_error"] = current.get("last_error") or ""
            elif current_decision_at == state_decision_at:
                state["last_error"] = state.get("last_error") or current.get("last_error") or ""

            if str(current.get("last_b1_generated_at") or "") > str(state.get("last_b1_generated_at") or ""):
                state["last_b1_generated_at"] = current.get("last_b1_generated_at")

        prune_non_trading_day_equity_points(state)
        prune_future_intraday_equity_points(state)
        normalize_daily_equity_history(state)
        sort_equity_history(state)

        tmp = STATE_FILE.with_name(f"{STATE_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        tmp.replace(STATE_FILE)


def normalize_code(code: str) -> str:
    code = re.sub(r"\D", "", str(code or ""))[-6:]
    return code


def quote_one(code: str) -> dict[str, Any]:
    code = normalize_code(code)
    if not code:
        return {"code": code, "price": None, "name": ""}
    script = STOCK_TOOLS_SCRIPT
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "quote", code],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout)
    except Exception as exc:
        return {"code": code, "price": None, "name": "", "error": f"{type(exc).__name__}: {exc}"}
    return {"code": code, "price": None, "name": "", "error": "quote failed"}


def market_symbol(code: str) -> str:
    code = normalize_code(code)
    prefix = "sh" if code.startswith(("6", "9")) else ("bj" if code.startswith(("4", "8")) else "sz")
    return prefix + code


def intraday_minute_index(hhmm: str) -> int | None:
    text = str(hhmm or "").strip().replace(":", "")
    if len(text) < 4 or not text[:4].isdigit():
        return None
    hour = int(text[:2])
    minute = int(text[2:4])
    minute_of_day = hour * 60 + minute
    am_start = 9 * 60 + 30
    am_end = 11 * 60 + 30
    pm_start = 13 * 60
    pm_end = 15 * 60
    if minute_of_day < am_start or minute_of_day > pm_end or (am_end < minute_of_day < pm_start):
        return None
    if minute_of_day <= am_end:
        return minute_of_day - am_start
    return 120 + (minute_of_day - pm_start)


def parse_intraday_minute_rows(rows: list[Any], prev_close: float | None = None) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    base = float(prev_close or 0)
    for item in rows:
        parts = str(item or "").strip().split()
        if len(parts) < 2:
            continue
        minute_idx = intraday_minute_index(parts[0])
        if minute_idx is None:
            continue
        try:
            price = float(parts[1])
        except Exception:
            continue
        if price <= 0:
            continue
        if base <= 0:
            base = price
        volume = None
        amount = None
        try:
            volume = float(parts[2]) if len(parts) >= 3 else None
        except Exception:
            volume = None
        try:
            amount = float(parts[3]) if len(parts) >= 4 else None
        except Exception:
            amount = None
        hhmm = parts[0].replace(":", "")
        time_text = f"{hhmm[:2]}:{hhmm[2:4]}"
        points.append({
            "time": time_text,
            "minute": minute_idx,
            "price": round(price, 3),
            "pct": round((price / base - 1) * 100, 3) if base > 0 else 0.0,
            "volume": volume,
            "amount": amount,
        })
    return points[-INTRADAY_MAX_POINTS:]


def fetch_intraday_minutes(code: str, prev_close: float | None = None) -> dict[str, Any]:
    code = normalize_code(code)
    symbol = market_symbol(code)
    now_value = time.time()
    cached = INTRADAY_CACHE.get(symbol)
    if cached and now_value - float(cached.get("ts") or 0) < INTRADAY_CACHE_TTL_SECONDS:
        return dict(cached.get("data") or {})
    url = f"{TENCENT_MINUTE_URL}?code={symbol}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        payload = json.loads(resp.read().decode("utf-8", "ignore"))
    stock_data = ((payload.get("data") or {}).get(symbol) or {}).get("data") or {}
    raw_rows = stock_data.get("data") or []
    points = parse_intraday_minute_rows(raw_rows, prev_close=prev_close)
    if not points:
        raise RuntimeError("empty intraday minute data")
    latest = points[-1]
    data = {
        "source": "Tencent ifzq minute/query",
        "updated_at": now_ts(),
        "symbol": symbol,
        "prev_close": prev_close,
        "points": points,
        "last_price": latest.get("price"),
        "last_pct": latest.get("pct"),
    }
    INTRADAY_CACHE[symbol] = {"ts": now_value, "data": data}
    return dict(data)


def safe_quote_float(value: str) -> float | None:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return None


def normalize_quote_price(price: float | None, *fallbacks: float | None) -> float | None:
    if price and price > 0:
        return price
    for fallback in fallbacks:
        if fallback and fallback > 0:
            return fallback
    return None


def build_quote(code: str, name: str, price: float, prev_close: float | None, open_price: float | None,
                high: float | None, low: float | None, turnover_yuan: float | None, source: str,
                quote_time: str | None = None) -> dict[str, Any]:
    change = round(price - prev_close, 2) if prev_close else None
    change_pct = round((change / prev_close) * 100, 2) if change is not None and prev_close else None
    return {
        "code": normalize_code(code),
        "name": name,
        "price": price,
        "prev_close": prev_close,
        "open": open_price,
        "high": high,
        "low": low,
        "change": change,
        "change_pct": change_pct,
        "turnover_yuan": turnover_yuan,
        "quote_time": quote_time or now_ts(),
        "source": source,
    }


def parse_tencent_quote_line(line: str) -> dict[str, Any] | None:
    if "=" not in line or "~" not in line:
        return None
    key, raw = line.split("=", 1)
    symbol = key.strip().lstrip("v_")
    parts = raw.strip().strip('";').split("~")
    if len(parts) < 38:
        return None
    price = safe_quote_float(parts[3])
    prev_close = safe_quote_float(parts[4])
    open_price = safe_quote_float(parts[5])
    high = safe_quote_float(parts[33])
    low = safe_quote_float(parts[34])
    turnover_wan = safe_quote_float(parts[37])
    price = normalize_quote_price(price, prev_close, open_price)
    if not price:
        return None
    return build_quote(
        code=symbol,
        name=parts[1] if len(parts) > 1 else "",
        price=price,
        prev_close=prev_close,
        open_price=open_price,
        high=high,
        low=low,
        turnover_yuan=turnover_wan * 10000 if turnover_wan is not None else None,
        source="Tencent qt realtime quote",
    )


def parse_sina_quote_line(line: str) -> dict[str, Any] | None:
    if "=" not in line or '"' not in line:
        return None
    key, raw = line.split("=", 1)
    symbol = key.strip().split("hq_str_", 1)[-1]
    parts = raw.strip().strip('";').split(",")
    if len(parts) < 32 or not parts[0]:
        return None
    open_price = safe_quote_float(parts[1])
    prev_close = safe_quote_float(parts[2])
    price = safe_quote_float(parts[3])
    high = safe_quote_float(parts[4])
    low = safe_quote_float(parts[5])
    turnover_yuan = safe_quote_float(parts[9])
    price = normalize_quote_price(price, prev_close, open_price)
    if not price:
        return None
    quote_time = now_ts()
    if len(parts) > 31 and parts[30] and parts[31]:
        quote_time = f"{parts[30]} {parts[31]}"
    return build_quote(
        code=symbol,
        name=parts[0],
        price=price,
        prev_close=prev_close,
        open_price=open_price,
        high=high,
        low=low,
        turnover_yuan=turnover_yuan,
        source="Sina hq realtime quote",
        quote_time=quote_time,
    )


def quote_one_as_realtime(code: str) -> dict[str, Any] | None:
    q = quote_one(code)
    price = q.get("price") if isinstance(q.get("price"), (int, float)) else None
    if not price or price <= 0:
        return None
    return build_quote(
        code=code,
        name=q.get("name") or "",
        price=float(price),
        prev_close=q.get("prev_close") if isinstance(q.get("prev_close"), (int, float)) else None,
        open_price=q.get("open") if isinstance(q.get("open"), (int, float)) else None,
        high=q.get("high") if isinstance(q.get("high"), (int, float)) else None,
        low=q.get("low") if isinstance(q.get("low"), (int, float)) else None,
        turnover_yuan=q.get("turnover_yuan") if isinstance(q.get("turnover_yuan"), (int, float)) else None,
        source=q.get("source") or "cn_stock_tools quote fallback",
    )


def eastmoney_secid(code: str) -> str:
    code = normalize_code(code)
    market = "1" if code.startswith(("6", "9")) else "0"
    return f"{market}.{code}"


def parse_eastmoney_stock(data: dict[str, Any]) -> dict[str, Any] | None:
    if not data:
        return None
    price = data.get("f43")
    prev_close = data.get("f60")
    open_price = data.get("f46")
    high = data.get("f44")
    low = data.get("f45")
    price = normalize_quote_price(price if isinstance(price, (int, float)) else None,
                                  prev_close if isinstance(prev_close, (int, float)) else None,
                                  open_price if isinstance(open_price, (int, float)) else None)
    if not price:
        return None
    return build_quote(
        code=str(data.get("f57") or ""),
        name=str(data.get("f58") or ""),
        price=float(price),
        prev_close=prev_close if isinstance(prev_close, (int, float)) else None,
        open_price=open_price if isinstance(open_price, (int, float)) else None,
        high=high if isinstance(high, (int, float)) else None,
        low=low if isinstance(low, (int, float)) else None,
        turnover_yuan=data.get("f48") if isinstance(data.get("f48"), (int, float)) else None,
        source="Eastmoney push2 stock/get realtime quote",
    )


def fetch_tencent_quotes(codes: list[str]) -> tuple[dict[str, dict[str, Any]], str]:
    symbols = [market_symbol(code) for code in codes if normalize_code(code)]
    if not symbols:
        return {}, ""
    try:
        url = TENCENT_QUOTE_URL + ",".join(symbols)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            text = resp.read().decode("gbk", "ignore")
        quotes = {}
        for line in text.split(";"):
            parsed = parse_tencent_quote_line(line)
            if parsed and parsed.get("code"):
                quotes[parsed["code"]] = parsed
        return quotes, ""
    except Exception as exc:
        return {}, f"Tencent {type(exc).__name__}: {exc}"


def fetch_eastmoney_quotes(codes: list[str]) -> tuple[dict[str, dict[str, Any]], str]:
    normalized = [normalize_code(code) for code in codes if normalize_code(code)]
    if not normalized:
        return {}, ""
    quotes: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for code in normalized:
        try:
            # Eastmoney often closes Python urllib connections on this machine;
            # curl works reliably, so use it only for this fallback channel.
            proc = subprocess.run([
                "curl", "-L", "--max-time", "8", "-sS",
                EASTMONEY_STOCK_URL,
                "-H", "User-Agent: Mozilla/5.0",
                "-H", "Referer: https://quote.eastmoney.com/",
                "--get",
                "--data-urlencode", f"secid={eastmoney_secid(code)}",
                "--data-urlencode", f"ut={EASTMONEY_UT}",
                "--data-urlencode", "fltt=2",
                "--data-urlencode", "invt=2",
                "--data-urlencode", "fields=f43,f57,f58,f60,f169,f170,f46,f44,f45,f47,f48,f50",
            ], capture_output=True, text=True, timeout=10)
            if proc.returncode != 0 or not proc.stdout.strip():
                errors.append(f"{code}:curl{proc.returncode}")
                continue
            data = json.loads(proc.stdout)
            quote = parse_eastmoney_stock((data or {}).get("data") or {})
            if quote and quote.get("code"):
                quotes[quote["code"]] = quote
            else:
                errors.append(f"{code}:empty")
        except Exception as exc:
            errors.append(f"{code}:{type(exc).__name__}")
    return quotes, ("Eastmoney " + ",".join(errors)) if errors else ""


def fetch_sina_quotes(codes: list[str]) -> tuple[dict[str, dict[str, Any]], str]:
    symbols = [market_symbol(code) for code in codes if normalize_code(code)]
    if not symbols:
        return {}, ""
    try:
        url = SINA_QUOTE_URL + ",".join(symbols)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            text = resp.read().decode("gbk", "ignore")
        quotes = {}
        for line in text.splitlines():
            parsed = parse_sina_quote_line(line)
            if parsed and parsed.get("code"):
                quotes[parsed["code"]] = parsed
        return quotes, ""
    except Exception as exc:
        return {}, f"Sina {type(exc).__name__}: {exc}"


def fetch_realtime_quotes(codes: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    normalized_codes = [normalize_code(code) for code in codes if normalize_code(code)]
    meta = {"channel_counts": {"tencent": 0, "eastmoney": 0, "sina": 0, "single": 0}, "errors": []}
    quotes: dict[str, dict[str, Any]] = {}
    tencent_quotes, tencent_error = fetch_tencent_quotes(normalized_codes)
    if tencent_error:
        meta["errors"].append(tencent_error)
    for code, quote in tencent_quotes.items():
        if code in normalized_codes and code not in quotes:
            quotes[code] = quote
            meta["channel_counts"]["tencent"] += 1
    missing = [code for code in normalized_codes if code not in quotes]
    eastmoney_quotes, eastmoney_error = fetch_eastmoney_quotes(missing)
    if eastmoney_error:
        meta["errors"].append(eastmoney_error)
    for code, quote in eastmoney_quotes.items():
        if code in missing and code not in quotes:
            quotes[code] = quote
            meta["channel_counts"]["eastmoney"] += 1
    missing = [code for code in normalized_codes if code not in quotes]
    sina_quotes, sina_error = fetch_sina_quotes(missing)
    if sina_error:
        meta["errors"].append(sina_error)
    for code, quote in sina_quotes.items():
        if code in missing and code not in quotes:
            quotes[code] = quote
            meta["channel_counts"]["sina"] += 1
    missing = [code for code in normalized_codes if code not in quotes]
    for code in missing:
        quote = quote_one_as_realtime(code)
        if quote:
            quotes[code] = quote
            meta["channel_counts"]["single"] += 1
    final_missing = [code for code in normalized_codes if code not in quotes]
    if final_missing:
        meta["errors"].append("missing quotes: " + ",".join(final_missing))
    return quotes, meta


def execution_quote(code: str, dt: datetime | None = None) -> dict[str, Any]:
    dt = dt or datetime.now()
    if is_a_share_auction_time(dt):
        quotes, _ = fetch_realtime_quotes([code])
        quote = quotes.get(normalize_code(code)) or {}
        price = quote.get("price") if isinstance(quote.get("price"), (int, float)) else None
        if price and price > 0:
            return {
                **quote,
                "price": float(price),
                "execution_price_source": f"auction_reference:{quote.get('source') or 'realtime_quote'}",
            }
    quote = quote_one(code)
    price = quote.get("price") if isinstance(quote.get("price"), (int, float)) else None
    if price and price > 0:
        return {**quote, "price": float(price), "execution_price_source": quote.get("source") or "quote_one"}
    return quote


def refresh_realtime_prices(state: dict[str, Any]) -> dict[str, Any]:
    positions = state.get("positions") or {}
    codes = [normalize_code(code) for code, pos in positions.items() if position_qty(pos) > 0]
    meta = {"enabled": True, "source": "Tencent→Eastmoney→Sina→single quote redundant realtime", "quote_time": now_ts(),
            "updated": 0, "fallback": 0, "error": "", "channel_counts": {"tencent": 0, "eastmoney": 0, "sina": 0, "single": 0}}
    if not codes:
        state["last_quote_refresh"] = meta
        return meta
    quotes, quote_meta = fetch_realtime_quotes(codes)
    meta["channel_counts"] = quote_meta.get("channel_counts", meta["channel_counts"])
    errors = quote_meta.get("errors") or []
    for code in codes:
        pos = positions.get(code) or positions.get(str(code))
        quote = quotes.get(code)
        if not pos or not quote or not quote.get("price"):
            meta["fallback"] += 1
            continue
        pos["last_price"] = quote["price"]
        pos["quote_time"] = quote["quote_time"]
        pos["quote_source"] = quote["source"]
        pos["change_pct"] = quote.get("change_pct")
        pos["prev_close"] = quote.get("prev_close")
        if quote.get("high") is not None:
            pos["day_high"] = quote.get("high")
        if quote.get("low") is not None:
            pos["day_low"] = quote.get("low")
        if quote.get("name"):
            pos["name"] = pos.get("name") or quote["name"]
        meta["updated"] += 1
    if errors:
        meta["error"] = " | ".join(errors)
    state["last_quote_refresh"] = meta
    return meta


def refresh_position_intraday(state: dict[str, Any]) -> dict[str, Any]:
    positions = state.get("positions") or {}
    meta = {"enabled": True, "source": "Tencent ifzq minute/query", "updated": 0, "error": "", "quote_time": now_ts()}
    errors: list[str] = []
    for code, pos in positions.items():
        if position_qty(pos) <= 0:
            continue
        try:
            prev_close = pos.get("prev_close") if isinstance(pos.get("prev_close"), (int, float)) else None
            intraday = fetch_intraday_minutes(code, prev_close=prev_close)
            pos["intraday"] = intraday
            if intraday.get("last_price"):
                pos["last_price"] = intraday["last_price"]
            if intraday.get("last_pct") is not None:
                pos["change_pct"] = round(float(intraday["last_pct"]), 2)
            meta["updated"] += 1
        except Exception as exc:
            errors.append(f"{code}:{type(exc).__name__}")
    if errors:
        meta["error"] = ",".join(errors[:6])
    state["last_intraday_refresh"] = meta
    return meta


def enrich_portfolio(state: dict[str, Any]) -> dict[str, Any]:
    positions = state.get("positions") or {}
    total_mv = 0.0
    rows = []
    today = today_key()
    for code, pos in positions.items():
        # Use last_price from portfolio state first to avoid network hangs
        price = pos.get("last_price") or pos.get("avg_cost") or 0
        qty = int(pos.get("qty") or pos.get("shares") or 0)
        price_float = float(price or 0)
        prev_close = pos.get("prev_close")
        try:
            prev_close_float = float(prev_close or 0)
        except Exception:
            prev_close_float = 0.0
        mv = price_float * qty
        cost = float(pos.get("avg_cost") or 0) * qty
        pnl = mv - cost
        today_pnl, today_pnl_pct = position_today_pnl(pos, price_float, qty, prev_close_float)
        change_pct = pos.get("change_pct")
        if change_pct is None and prev_close_float > 0:
            change_pct = (price_float / prev_close_float - 1) * 100
        day_high = pos.get("day_high") if pos.get("day_high") is not None else pos.get("high")
        day_low = pos.get("day_low") if pos.get("day_low") is not None else pos.get("low")
        try:
            day_high_float = float(day_high or 0)
        except Exception:
            day_high_float = 0.0
        try:
            day_low_float = float(day_low or 0)
        except Exception:
            day_low_float = 0.0
        day_high_pct = (day_high_float / prev_close_float - 1) * 100 if day_high_float > 0 and prev_close_float > 0 else None
        day_low_pct = (day_low_float / prev_close_float - 1) * 100 if day_low_float > 0 and prev_close_float > 0 else None
        buy_date_lots = pos.get("buy_date_lots") or {}
        today_buy_qty = min(qty, int(buy_date_lots.get(today, 0) or 0)) if isinstance(buy_date_lots, dict) else 0
        strategy_mark = compact_position_strategy_mark(pos)
        strategy_history = pos.get("strategy_mark_history") if isinstance(pos.get("strategy_mark_history"), list) else []
        total_mv += mv
        row = {
            "code": code,
            "name": pos.get("name") or "",
            "qty": qty,
            "available_qty": available_to_sell(pos),
            "avg_cost": pos.get("avg_cost") or 0,
            "last_price": price,
            "day_high": day_high,
            "day_low": day_low,
            "day_high_pct": round(day_high_pct, 2) if day_high_pct is not None else None,
            "day_low_pct": round(day_low_pct, 2) if day_low_pct is not None else None,
            "quote_time": pos.get("quote_time") or "",
            "quote_source": pos.get("quote_source") or "state_last_price",
            "change_pct": round(float(change_pct), 2) if isinstance(change_pct, (int, float)) else change_pct,
            "prev_close": prev_close,
            "today_pnl": round(today_pnl, 2) if today_pnl is not None else None,
            "today_pnl_pct": round(today_pnl_pct, 2) if today_pnl_pct is not None else None,
            "market_value": round(mv, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round((pnl / cost * 100), 2) if cost > 0 else 0,
            "buy_date_lots": buy_date_lots,
            "today_buy_qty": today_buy_qty,
            "bought_today": today_buy_qty > 0,
            "buy_strategy": pos.get("buy_strategy") or "",
            "entry_reason": pos.get("entry_reason") or "",
            "strategy_mark": strategy_mark,
            "strategy_mark_id": strategy_mark.get("strategy_id") or "",
            "strategy_mark_label": strategy_mark.get("label") or "",
            "strategy_mark_history": strategy_history[-4:],
            "last_exit_rule": pos.get("last_exit_rule") or "",
            "last_exit_label": pos.get("last_exit_label") or "",
            "last_exit_reason": pos.get("last_exit_reason") or "",
            "last_exit_strategy_mark": pos.get("last_exit_strategy_mark") or {},
            "exit_state": {
                "highest_price": pos.get("highest_price"),
                "max_pnl_pct": pos.get("max_pnl_pct"),
                "bbi": pos.get("bbi"),
                "bbi_distance_pct": pos.get("bbi_distance_pct"),
                "bbi_break_days": pos.get("bbi_break_days"),
                "atr20": pos.get("atr20"),
                "low10": pos.get("low10"),
                "chandelier_stop": pos.get("chandelier_stop"),
                "trailing_gap_pct": pos.get("trailing_gap_pct"),
                "shaofu_stop_price": pos.get("shaofu_stop_price"),
                "sell_score": pos.get("sell_score"),
                "sell_score_reason": pos.get("sell_score_reason"),
                "z_white": pos.get("z_white"),
                "z_yellow": pos.get("z_yellow"),
                "z_white_break_days": pos.get("z_white_break_days"),
                "z_dead_cross": pos.get("z_dead_cross"),
                "s123_signal": pos.get("s123_signal"),
                "s123_reason": pos.get("s123_reason"),
                "chuhuo_wushi": pos.get("chuhuo_wushi"),
                "luzhu_half_signal": pos.get("luzhu_half_signal"),
            },
        }
        pos["last_price"] = price
        rows.append(row)
    cash = float(state.get("cash") or 0)
    total_equity = cash + total_mv
    sector_tide_open_risk_pct = 0.0
    for row in rows:
        row["position_pct"] = position_pct_of_equity(row.get("market_value"), total_equity)
        source_pos = positions.get(row.get("code")) if isinstance(positions.get(row.get("code")), dict) else {}
        if is_sector_tide_strategy(position_entry_strategy(source_pos)):
            effective_distance = stored_position_effective_loss_distance_pct(
                source_pos,
                mark_price=_safe_float(row.get("last_price"), 0.0),
            )
            if effective_distance <= 0:
                effective_distance = _safe_float(source_pos.get("effective_loss_distance_pct"), 0.0)
            open_risk = position_open_risk_pct(row.get("market_value"), total_equity, effective_distance)
            source_pos["effective_loss_distance_pct"] = round(effective_distance, 3)
            source_pos["position_open_risk_pct"] = round(open_risk, 4)
            row.update({
                "industry": source_pos.get("industry") or source_pos.get("sector") or "",
                "entry_stop_price": source_pos.get("entry_stop_price"),
                "gap_buffer_pct": source_pos.get("gap_buffer_pct"),
                "execution_buffer_pct": source_pos.get("execution_buffer_pct"),
                "effective_loss_distance_pct": round(effective_distance, 3),
                "position_open_risk_pct": round(open_risk, 4),
                "dynamic_position_cap_pct": source_pos.get("dynamic_position_cap_pct"),
                "risk_budget_regime": source_pos.get("risk_budget_regime"),
                "per_trade_risk_budget_pct": source_pos.get("per_trade_risk_budget_pct"),
                "max_open_risk_pct": source_pos.get("max_open_risk_pct"),
                "max_sector_risk_pct": source_pos.get("max_sector_risk_pct"),
            })
            sector_tide_open_risk_pct += open_risk
    source_equity_times: list[str] = []
    for point in state.get("equity_history", []):
        if not isinstance(point, dict):
            continue
        time_text = str(point.get("time") or "")
        try:
            equity = float(point.get("equity"))
        except (TypeError, ValueError):
            continue
        if parse_ts(time_text) is not None and math.isfinite(equity):
            source_equity_times.append(time_text)
    source_last_equity_time = max(source_equity_times, default="")
    return {
        "generated_at": now_ts(),
        "source_updated_at": str(state.get("updated_at") or ""),
        "source_last_equity_time": source_last_equity_time,
        "initial_cash": float(state.get("initial_cash") or INITIAL_CASH),
        "cash": round(cash, 2),
        "market_value": round(total_mv, 2),
        "total_equity": round(total_equity, 2),
        "total_pnl": round(total_equity - float(state.get("initial_cash") or INITIAL_CASH), 2),
        "total_pnl_pct": round((total_equity / float(state.get("initial_cash") or INITIAL_CASH) - 1) * 100, 2),
        "sector_tide_open_risk_pct": round(sector_tide_open_risk_pct, 4),
        "positions": rows,
        "trade_log": list(reversed(state.get("trade_log", [])[-TRADE_LOG_LIMIT:])),
        "decision_log": list(reversed(state.get("decision_log", [])[-50:])),
        "pending_decisions": [
            item for item in state.get("pending_decisions", [])
            if isinstance(item, dict) and item.get("status") == "pending"
        ],
        "today_sold_stocks": state.get("today_sold_stocks", []),
        "today_sold_quote_refresh": state.get("today_sold_quote_refresh", {}),
        "equity_history": state.get("equity_history", [])[-EQUITY_HISTORY_LIMIT:],
        "last_b1_generated_at": state.get("last_b1_generated_at") or "",
        "last_decision_at": state.get("last_decision_at") or "",
        "last_quote_refresh": state.get("last_quote_refresh") or {},
        "last_intraday_refresh": state.get("last_intraday_refresh") or {},
        "last_error": state.get("last_error") or "",
        "market_decision_context": state.get("market_decision_context") or {},
    }


def available_to_sell(pos: dict[str, Any], today: str | None = None) -> int:
    lots = pos.get("buy_date_lots") or {}
    qty = int(pos.get("qty") or pos.get("shares") or 0)
    # Legacy positions created before lot tracking are historical holdings.
    if not lots:
        return qty
    today = today or today_key()
    total = 0
    for date, lot_qty in lots.items():
        if date != today:
            total += int(lot_qty or 0)
    return min(qty, total)


def position_today_pnl(pos: dict[str, Any], price: float, qty: int, prev_close: float) -> tuple[float | None, float | None]:
    if qty <= 0:
        return None, None
    avg_cost = float(pos.get("avg_cost") or 0)
    lots = pos.get("buy_date_lots") or {}
    today_qty = min(qty, int(lots.get(today_key(), 0) or 0))
    historical_qty = max(0, qty - today_qty)
    pnl = 0.0
    base = 0.0

    if historical_qty > 0:
        if prev_close <= 0:
            return None, None
        pnl += (price - prev_close) * historical_qty
        base += prev_close * historical_qty

    if today_qty > 0:
        if avg_cost <= 0:
            return None, None
        pnl += (price - avg_cost) * today_qty
        base += avg_cost * today_qty

    if base <= 0:
        return None, None
    return pnl, pnl / base * 100


def calc_trade_fees(amount: float, side: str) -> dict[str, float]:
    """Calculate A-share paper-trading fees for 万一免五 account."""
    amount = float(amount or 0)
    commission = max(amount * COMMISSION_RATE, COMMISSION_MIN)
    transfer_fee = amount * TRANSFER_FEE_RATE
    stamp_duty = amount * STAMP_DUTY_SELL_RATE if side.upper() == "SELL" else 0.0
    total_fee = commission + transfer_fee + stamp_duty
    return {
        "commission": round(commission, 2),
        "transfer_fee": round(transfer_fee, 2),
        "stamp_duty": round(stamp_duty, 2),
        "total_fee": round(total_fee, 2),
    }


def position_qty(pos: dict[str, Any]) -> int:
    return int(pos.get("qty") or pos.get("shares") or 0)


def parse_model_action_shares(action: dict[str, Any]) -> int | None:
    raw = (action or {}).get("shares")
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw) if raw.is_integer() else None
    text = str(raw).strip()
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        return int(float(text))
    return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def position_market_value(pos: dict[str, Any], fallback_price: float | None = None) -> float:
    qty = position_qty(pos)
    if qty <= 0:
        return 0.0
    price = _safe_float(pos.get("last_price") or pos.get("close") or fallback_price or pos.get("avg_cost"))
    return max(0.0, qty * price)


def open_position_count(positions: dict[str, Any]) -> int:
    return sum(1 for pos in (positions or {}).values() if isinstance(pos, dict) and position_qty(pos) > 0)


def portfolio_market_value(positions: dict[str, Any]) -> float:
    return sum(position_market_value(pos) for pos in (positions or {}).values() if isinstance(pos, dict))


def portfolio_total_equity_for_limits(cash: float, positions: dict[str, Any]) -> float:
    total = float(cash or 0) + portfolio_market_value(positions)
    return total if total > 0 else float(cash or 0)


def position_pct_of_equity(value: float | int | None, total_equity: float | int | None) -> float | None:
    try:
        value_float = float(value or 0)
        equity_float = float(total_equity or 0)
    except (TypeError, ValueError):
        return None
    if equity_float <= 0:
        return None
    return round(value_float / equity_float * 100, 2)


def strategy_position_limit_pct(strategy: str) -> float:
    return _strategy_position_limit_pct(strategy, MAX_SINGLE_POSITION_PCT)


def candidate_buy_blockers(candidate: dict[str, Any] | None) -> list[str]:
    return _strategy_candidate_buy_blockers(
        candidate,
        max_bbi_distance_pct=COMMON_MAX_BBI_DISTANCE_PCT,
    )


def candidate_is_buyable(candidate: dict[str, Any] | None) -> bool:
    return not candidate_buy_blockers(candidate)


def current_stock_universe() -> tuple[str, ...]:
    return selected_stock_universe(os.environ.get(STOCK_UNIVERSE_ENV))


def candidate_in_stock_universe(candidate: dict[str, Any] | None) -> bool:
    candidate = candidate or {}
    return stock_in_universe(
        candidate.get("code"),
        candidate.get("name"),
        current_stock_universe(),
    )


def add_execution_block(decision: dict[str, Any], code: str, reason: str) -> None:
    blocks = decision.setdefault("execution_blocked_reasons", [])
    text = f"{code}: {reason}" if code else reason
    blocks.append(text)
    decision["execution_blocked_reason"] = "；".join(blocks[-5:])


def record_equity(state: dict[str, Any]) -> None:
    if not is_a_share_trading_day():
        prune_non_trading_day_equity_points(state)
        return
    prune_non_trading_day_equity_points(state)
    prune_future_intraday_equity_points(state)
    normalize_daily_equity_history(state)
    sort_equity_history(state)
    snap = enrich_portfolio(state)
    history = state.setdefault("equity_history", [])
    now = now_ts()
    today = now[:10]
    
    # 获取今天已有的所有记录
    today_records = [h for h in history if h.get("time", "").startswith(today)]
    
    # 获取每日结算净值历史（按天存储）
    daily_history = state.setdefault("daily_equity_history", [])
    
    # 动态降采样保存日内点：
    # 每2分钟保存一次，或者收盘(15:00)时强制保存
    should_save = False
    is_closing_point = False
    
    if not today_records:
        should_save = True
    else:
        last_time_str = today_records[-1].get("time", "")
        if last_time_str:
            import datetime
            try:
                last_dt = datetime.datetime.strptime(last_time_str, "%Y-%m-%d %H:%M:%S")
                now_dt = datetime.datetime.strptime(now, "%Y-%m-%d %H:%M:%S")
                if (now_dt - last_dt).total_seconds() >= EQUITY_HEARTBEAT_MIN_SECONDS:
                    should_save = True
                elif "15:00:" in now and "15:00:" not in last_time_str:
                    should_save = True
                    is_closing_point = True
            except Exception:
                should_save = True
                
    if should_save:
        pt = {
            "time": now,
            "equity": snap["total_equity"],
            "cash": snap["cash"],
            "market_value": snap["market_value"],
            "pnl_pct": snap["total_pnl_pct"],
        }
        history.append(pt)
        state["equity_history"] = history[-2000:]
        
        # 每日结算逻辑：如果是 15:00 之后的第一个点，或者当天最后一次刷新
        # 我们可以用当天的最后一条记录更新 daily_history
        if daily_history and daily_history[-1].get("time", "").startswith(today):
            # 如果今天已经有记录，覆盖为最新（收盘价）
            daily_history[-1] = pt
        else:
            # 新的一天，添加记录
            daily_history.append(pt)
        
        # 同步写入 SQLite
        try:
            from niuniu_db import record_daily_equity as _record_db
            _record_db(pt)
        except Exception:
            pass


def rebuild_intraday_equity_curve(
    state: dict[str, Any],
    today: str | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """Rebuild today's account equity from per-position minute prices.

    This keeps dashboard refreshes accurate without executing trades. It is most
    useful after data repair, where sparse heartbeat points can otherwise make
    the intraday curve look flat or jumpy.

    On a day with trades, the current cash and positions cannot reconstruct the
    part of the session before the latest execution.  In that case, preserve all
    recorded points and fill missing minute marks strictly after the latest
    trade.  Existing recorded minutes always win over reconstructed data.  This
    safely repairs sparse post-trade curves after a restart without rewriting
    trade history.
    """
    now = now or datetime.now()
    today = today or now.strftime("%Y-%m-%d")
    if not is_a_share_trading_day(now):
        prune_non_trading_day_equity_points(state)
        return False
    today_trades = [
        trade
        for trade in state.get("trade_log", [])
        if isinstance(trade, dict) and str(trade.get("time", "")).startswith(today)
    ]
    latest_trade_dt = None
    if today_trades:
        trade_times = [parse_ts(trade.get("time", "")) for trade in today_trades]
        # An unparseable execution timestamp makes append-only reconstruction
        # unsafe, so retain the previous conservative behaviour.
        if any(trade_time is None for trade_time in trade_times):
            return False
        latest_trade_dt = max(trade_time for trade_time in trade_times if trade_time is not None)
    session_cutoff = current_session_minute(now) if today == now.strftime("%Y-%m-%d") else 240
    cash = float(state.get("cash") or 0)
    initial_cash = float(state.get("initial_cash") or INITIAL_CASH)
    positions = state.get("positions") or {}

    minute_prices: dict[str, dict[int, tuple[str, float]]] = {}
    for code, pos in positions.items():
        if position_qty(pos) <= 0:
            continue
        series: dict[int, tuple[str, float]] = {}
        for point in ((pos.get("intraday") or {}).get("points") or []):
            minute = point.get("minute")
            price = point.get("price")
            time_text = point.get("time")
            if isinstance(minute, int) and minute > session_cutoff:
                continue
            if isinstance(minute, int) and isinstance(price, (int, float)) and time_text:
                series[int(minute)] = (str(time_text), float(price))
        if series:
            minute_prices[code] = series

    if not minute_prices:
        return False

    last_price_by_code: dict[str, float] = {}
    rebuilt: list[dict[str, Any]] = []
    all_minutes = sorted(set().union(*(set(series.keys()) for series in minute_prices.values())))
    for minute in all_minutes:
        time_text = ""
        for code, series in minute_prices.items():
            if minute in series:
                time_text, price = series[minute]
                last_price_by_code[code] = price
        if len(last_price_by_code) < len(minute_prices) or not time_text:
            continue

        market_value = 0.0
        for code, pos in positions.items():
            qty = position_qty(pos)
            if qty <= 0:
                continue
            price = last_price_by_code.get(code)
            if price is None:
                break
            market_value += qty * price
        else:
            equity = cash + market_value
            rebuilt.append({
                "time": f"{today} {time_text}:00",
                "equity": round(equity, 2),
                "cash": round(cash, 2),
                "market_value": round(market_value, 2),
                "pnl_pct": round((equity / initial_cash - 1) * 100, 2) if initial_cash > 0 else 0.0,
            })

    if not rebuilt:
        return False

    for code, price in last_price_by_code.items():
        if code in positions:
            positions[code]["last_price"] = round(price, 3)

    if latest_trade_dt is not None:
        history = list(state.get("equity_history", []))
        existing_today_minutes = {
            parsed.strftime("%Y-%m-%d %H:%M")
            for item in history
            if str(item.get("time", "")).startswith(today)
            for parsed in [parse_ts(item.get("time", ""))]
            if parsed is not None
        }
        appended = [
            point
            for point in rebuilt
            if (parse_ts(point.get("time", "")) or datetime.min) > latest_trade_dt
            and str(point.get("time", ""))[:16] not in existing_today_minutes
        ]
        if not appended:
            return False
        history.extend(appended)
        history.sort(key=lambda item: str(item.get("time", "")))
        final_point = max(
            (item for item in history if str(item.get("time", "")).startswith(today)),
            key=lambda item: str(item.get("time", "")),
        )
    else:
        if len(rebuilt) < 2:
            return False
        history = [h for h in state.get("equity_history", []) if not str(h.get("time", "")).startswith(today)]
        history.extend(rebuilt)
        final_point = rebuilt[-1]
    state["equity_history"] = history[-2000:]

    daily_history = [h for h in state.get("daily_equity_history", []) if not str(h.get("time", "")).startswith(today)]
    daily_history.append(final_point)
    state["daily_equity_history"] = daily_history[-EQUITY_HISTORY_LIMIT:]

    try:
        from niuniu_db import record_daily_equity as _record_db
        _record_db(final_point)
    except Exception:
        pass
    return True


def refresh_today_sold_stocks(state: dict[str, Any], today: str | None = None) -> list[dict[str, Any]]:
    """Aggregate today's SELL trades and refresh quotes for post-sale tracking."""
    today = today or today_key()
    sold: dict[str, dict[str, Any]] = {}
    for trade in state.get("trade_log", []) or []:
        if not isinstance(trade, dict):
            continue
        if str(trade.get("action") or "").upper() != "SELL":
            continue
        if not str(trade.get("time") or "").startswith(today):
            continue
        code = normalize_code(trade.get("code") or "")
        shares = int(trade.get("shares") or 0)
        if not code or shares <= 0:
            continue
        row = sold.setdefault(code, {
            "code": code,
            "name": trade.get("name") or "",
            "shares": 0,
            "sell_amount": 0.0,
            "net_proceeds": 0.0,
            "realized_pnl": 0.0,
            "fee": 0.0,
            "reasons": [],
            "exit_rules": [],
            "buy_strategies": [],
            "first_sell_time": trade.get("time") or "",
            "last_sell_time": trade.get("time") or "",
        })
        amount = float(trade.get("amount") or (float(trade.get("price") or 0) * shares))
        fee = float(trade.get("fee") or 0)
        net_proceeds = float(trade.get("net_proceeds") or (amount - fee))
        pnl = float(trade.get("pnl") or 0)
        row["shares"] += shares
        row["sell_amount"] += amount
        row["net_proceeds"] += net_proceeds
        row["realized_pnl"] += pnl
        row["fee"] += fee
        row["last_sell_time"] = max(str(row.get("last_sell_time") or ""), str(trade.get("time") or ""))
        reason = str(trade.get("reason") or "").strip()
        if reason and reason not in row["reasons"]:
            row["reasons"].append(reason)
        exit_rule = str(trade.get("exit_rule") or classify_exit_rule(reason, trade.get("exit_signal"))).strip()
        if exit_rule and exit_rule not in row["exit_rules"]:
            row["exit_rules"].append(exit_rule)
        buy_strategy = str(trade.get("buy_strategy") or trade.get("entry_strategy") or "").strip()
        if buy_strategy and buy_strategy not in row["buy_strategies"]:
            row["buy_strategies"].append(buy_strategy)

    if not sold:
        state["today_sold_stocks"] = []
        state["today_sold_quote_refresh"] = {"quote_time": now_ts(), "updated": 0}
        return []

    quote_map: dict[str, dict[str, Any]] = {}
    quote_meta: dict[str, Any] = {"quote_time": now_ts(), "updated": 0}
    try:
        quote_map, quote_meta = fetch_realtime_quotes(sorted(sold.keys()))
    except Exception as exc:
        quote_meta = {"quote_time": now_ts(), "updated": 0, "error": f"{type(exc).__name__}: {exc}"}

    rows: list[dict[str, Any]] = []
    for code, row in sold.items():
        shares = int(row["shares"] or 0)
        avg_sell_price = (float(row["sell_amount"]) / shares) if shares > 0 else 0.0
        cost_basis = float(row["net_proceeds"]) - float(row["realized_pnl"])
        quote = quote_map.get(code) or {}
        current_price = quote.get("price") if isinstance(quote.get("price"), (int, float)) else None
        change_after_sell = ((float(current_price) / avg_sell_price - 1) * 100) if current_price and avg_sell_price > 0 else None
        after_sell_pnl = ((float(current_price) - avg_sell_price) * shares) if current_price and shares > 0 else None
        realized_pnl = float(row["realized_pnl"])
        rows.append({
            "code": code,
            "name": row.get("name") or quote.get("name") or "",
            "shares": shares,
            "avg_sell_price": round(avg_sell_price, 3),
            "current_price": round(float(current_price), 3) if current_price else None,
            "current_change_pct": quote.get("change_pct"),
            "realized_pnl": round(realized_pnl, 2),
            "realized_pnl_pct": round((realized_pnl / cost_basis * 100), 2) if cost_basis > 0 else 0,
            "sell_amount": round(float(row["sell_amount"]), 2),
            "net_proceeds": round(float(row["net_proceeds"]), 2),
            "fee": round(float(row["fee"]), 2),
            "change_after_sell_pct": round(change_after_sell, 2) if change_after_sell is not None else None,
            "after_sell_pnl": round(after_sell_pnl, 2) if after_sell_pnl is not None else None,
            "first_sell_time": row.get("first_sell_time") or "",
            "last_sell_time": row.get("last_sell_time") or "",
            "reason": "；".join(row.get("reasons") or []),
            "exit_rule": ",".join(row.get("exit_rules") or []),
            "exit_rules": row.get("exit_rules") or [],
            "buy_strategy": ",".join(row.get("buy_strategies") or []),
            "buy_strategies": row.get("buy_strategies") or [],
            "quote_time": quote.get("quote_time") or quote_meta.get("quote_time") or "",
            "quote_source": quote.get("source") or "",
        })
    rows.sort(key=lambda item: item.get("last_sell_time") or "", reverse=True)
    state["today_sold_stocks"] = rows
    state["today_sold_quote_refresh"] = quote_meta
    return rows


# ====== 自动止盈止损规则 ======

TAKE_PROFIT_PCT = 12.0     # 止盈线（清仓）
TAKE_PROFIT_PARTIAL_PCT = 8.0   # 第一批止盈（卖一半）
TAKE_PROFIT_PARTIAL_RATIO = 0.5  # 第一批卖出的比例
TRAILING_STOP_ACTIVATE_PCT = 5.0  # 移动止损激活线
TRAILING_MIN_GIVEBACK_PCT = 3.0   # 盈利回撤最小容忍
TRAILING_MAX_GIVEBACK_PCT = 6.5   # 盈利回撤最大容忍
TRAILING_GIVEBACK_RATIO = 0.45    # 峰值盈利回撤比例
S1_FAIL_BBI_PCT = -1.0            # S1/B1右侧确认失效：跌破BBI缓冲
S1_FAIL_CONFIRM_DAYS = 2          # 连续跌破BBI天数确认
DONCHIAN_EXIT_LOOKBACK_DAYS = 10  # 经典趋势系统：跌破近N日低点退出
ATR_LOOKBACK_DAYS = 20
ATR_CHANDELIER_MULT = 3.0
N_STRUCTURE_STOP_LOOKBACK_DAYS = 30  # N型结构前低最多回看交易日
N_STRUCTURE_LOW_TOLERANCE_PCT = 0.02  # 后低允许比前低低不超过2%
NO_PROGRESS_HOLD_DAYS = 3         # 买入后没涨，最少观察天数
NO_PROGRESS_MAX_PNL_PCT = 1.0
LUZHU_MEDIUM_YANG_PCT = 2.0       # 卤煮：连续中/大阳线的保守量化阈值
SELL_SCORE_REDUCE_THRESHOLD = 3
SELL_SCORE_EXIT_THRESHOLD = 2
B3_EXIT_TIME = env_hhmm("DASHBOARD_B3_EXIT_TIME", "09:37")
B3_EXIT_HHMM = B3_EXIT_TIME.strftime("%H:%M")
TIME_EXIT_TIME = env_hhmm("DASHBOARD_TIME_EXIT_TIME", os.environ.get("DASHBOARD_TIME_STOP_EXIT_TIME", "14:45") or "14:45")
TIME_EXIT_HHMM = TIME_EXIT_TIME.strftime("%H:%M")
TIME_STOP_EXIT_TIME = TIME_EXIT_TIME
TIME_STOP_EXIT_HHMM = TIME_EXIT_HHMM
S1_HIGH_ZONE_PCT = 0.90
S1_UPTREND_MIN_PCT = 15.0
S1_VOLUME_RATIO = 1.5
S1_CLOSE_LOW_POSITION = 0.30
MAX_HOLD_DAYS = 25         # 最大持仓天数
BBI_BREAKDOWN_PCT = -2.0   # 收盘跌破BBI -2%触发
DAILY_LOSS_BUDGET_PCT = -3.0  # 单日最大亏损预算
CONSENSUS_POSITION_BOOST = 1.5  # 策略共识≥3时仓位放大系数
SELF_OPTIMIZATION_COOLDOWN = 3600  # 自优化最小间隔（秒）
HIGH_VOL_REDUCTION = 0.7  # 高波动率仓位缩小系数
LOW_VOL_BOOST = 1.3       # 低波动率仓位放大系数
MAX_OPEN_POSITIONS = env_int("DASHBOARD_MAX_OPEN_POSITIONS", 6)
MAX_NEW_BUYS_PER_DECISION = env_int("DASHBOARD_MAX_NEW_BUYS_PER_DECISION", 2)
MAX_SINGLE_POSITION_PCT = env_float("DASHBOARD_MAX_SINGLE_POSITION_PCT", 10.0)
MAX_TOTAL_POSITION_PCT = env_float("DASHBOARD_MAX_TOTAL_POSITION_PCT", 80.0)
MIN_CASH_RESERVE_PCT = env_float("DASHBOARD_MIN_CASH_RESERVE_PCT", 20.0)
COMMON_MAX_BBI_DISTANCE_PCT = 6.5
MARKET_GUIDANCE_ENABLED = env_bool("DASHBOARD_MARKET_GUIDANCE_ENABLED", True)
MORNING_MAX_OPEN_POSITIONS = env_int("DASHBOARD_MORNING_MAX_OPEN_POSITIONS", min(3, MAX_OPEN_POSITIONS))
MORNING_MAX_OPEN_POSITIONS = max(1, min(MAX_OPEN_POSITIONS, MORNING_MAX_OPEN_POSITIONS))
MARKET_REPORT_LOOKBACK = 12
OVERNIGHT_US_MARKET_TITLE = "隔夜美股盘面总结"
PERIODIC_MARKET_MIN_SAMPLE = 1000
PERIODIC_MARKET_MIN_COVERAGE = 0.80
PERIODIC_MARKET_MIN_ACTIVE_RATIO = 0.20
PERIODIC_MARKET_SNAPSHOT_MAX_AGE_SECONDS = 10 * 60
MARKET_HARD_STOP_CONFIRMATIONS = 2
MARKET_HARD_STOP_RECOVERY_CONFIRMATIONS = 2
MARKET_HARD_STOP_LIQUIDITY_RATE_RATIO = 0.75
DECISION_INTELLIGENCE_ENABLED = env_bool("DASHBOARD_DECISION_INTELLIGENCE_ENABLED", True)
DECISION_INTELLIGENCE_TTL_SECONDS = max(15, env_int("DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS", 75))
DECISION_INTELLIGENCE_MAX_ITEMS = max(1, min(8, env_int("DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS", 5)))
DECISION_INTELLIGENCE_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}


def market_session_phase(now: datetime | None = None) -> str:
    now = now or datetime.now()
    t = now.time()
    if t < dtime(11, 30):
        return "morning"
    if t < dtime(13, 0):
        return "lunch"
    if t <= dtime(15, 0):
        return "afternoon"
    return "after_close"


def previous_a_share_trading_day_text(now: datetime | None = None) -> str:
    now = now or datetime.now()
    try:
        previous = str(trading_day_status(now, allow_refresh=False).get("previous_trading_day") or "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", previous):
            return previous
    except Exception:
        pass
    cur = now.date()
    for _ in range(10):
        cur -= timedelta(days=1)
        if cur.weekday() < 5:
            return cur.strftime("%Y-%m-%d")
    return ""


def _market_monitor_report_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
    time_text = str(record.get("time_text") or record.get("time") or "")
    content = str(record.get("content") or "")
    if not content.strip():
        return None
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    return {
        "title": record.get("title") or record.get("chat_label") or "盘面监控",
        "time": time_text,
        "content": content,
        "metadata": metadata,
    }


def _market_report_date_text(report: dict[str, Any]) -> str:
    text = str(report.get("time") or "")
    m = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return m.group(0) if m else ""


def _is_post_close_market_report(report: dict[str, Any]) -> bool:
    title = str(report.get("title") or "")
    content = str(report.get("content") or "")
    if any(keyword in title for keyword in ("盘后", "收盘")):
        return True
    if "次日盘前指引" in content or "次日买卖计划" in content:
        return True
    m = re.search(r"\d{2}:\d{2}", str(report.get("time") or ""))
    return bool(m and m.group(0) >= "15:00")


def _is_overnight_us_market_report(report: dict[str, Any]) -> bool:
    title = str(report.get("title") or "")
    content = str(report.get("content") or "")
    return (
        OVERNIGHT_US_MARKET_TITLE in title
        or "隔夜美股盘面总结" in content
        or ("美股概况" in content and "关键资产" in content)
    )


def _load_cached_overnight_us_market_report(now: datetime | None = None) -> dict[str, Any] | None:
    try:
        import us_market_summary as _us_market_summary

        summary = _us_market_summary.load_cached_summary_for_today(now)
        if not summary:
            return None
        guidance = [f"风险级别：{summary.get('tone_label') or '中性'}"]
        guidance.extend(str(line).strip() for line in (summary.get("guidance_lines") or []) if str(line).strip())
        content = _us_market_summary.build_us_market_report_text(summary)
        return {
            "title": OVERNIGHT_US_MARKET_TITLE,
            "time": str(summary.get("generated_at") or ""),
            "content": content,
            "metadata": {
                "decision_guidance": guidance[:8],
                "summary": summary.get("summary") or "",
                "target_us_date": summary.get("target_us_date") or "",
                "sector_mappings": summary.get("sector_mappings") or [],
            },
        }
    except Exception:
        return None


def load_today_market_monitor_reports(now: datetime | None = None, limit: int = 3) -> list[dict[str, Any]]:
    """Load current-day market reports plus prior close guidance when still relevant."""
    if not MARKET_GUIDANCE_ENABLED:
        return []
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    previous_trading_day = previous_a_share_trading_day_text(now)
    phase = market_session_phase(now)
    try:
        import push_history as _push_history
        data = _push_history.query_messages(category="market_monitor", limit=MARKET_REPORT_LOOKBACK)
    except Exception:
        data = {"records": []}
    same_day_reports: list[dict[str, Any]] = []
    overnight_us_report: dict[str, Any] | None = None
    previous_close_report: dict[str, Any] | None = None
    for record in data.get("records") or []:
        if not isinstance(record, dict):
            continue
        report = _market_monitor_report_from_record(record)
        if not report:
            continue
        report_date = _market_report_date_text(report)
        if report_date == today:
            if _is_overnight_us_market_report(report):
                if overnight_us_report is None:
                    overnight_us_report = report
            else:
                same_day_reports.append(report)
        elif (
            previous_close_report is None
            and report_date == previous_trading_day
            and _is_post_close_market_report(report)
        ):
            previous_close_report = report

    limit = max(int(limit or 1), 1)
    if overnight_us_report is None:
        overnight_us_report = _load_cached_overnight_us_market_report(now)

    same_day_limit = max(limit - (1 if overnight_us_report else 0), 1) if same_day_reports else 0
    reports = same_day_reports[:same_day_limit]
    if not reports and previous_close_report:
        reports.append(previous_close_report)
    if overnight_us_report and len(reports) < limit:
        reports.append(overnight_us_report)
    if previous_close_report and reports and previous_close_report not in reports and phase in {"morning", "lunch"} and len(reports) < limit:
        reports.append(previous_close_report)
    return reports


load_current_market_monitor_reports = load_today_market_monitor_reports


def extract_market_guidance_lines(content: str, metadata: dict[str, Any] | None = None, max_lines: int = 8) -> list[str]:
    guidance = (metadata or {}).get("decision_guidance")
    if isinstance(guidance, list):
        cleaned = [str(line).strip() for line in guidance if str(line).strip()]
        if cleaned:
            return cleaned[:max_lines]

    lines = [line.strip() for line in str(content or "").splitlines()]
    out: list[str] = []
    in_section = False
    for line in lines:
        if not line:
            if in_section and out:
                break
            continue
        if any(key in line for key in ("买卖指引", "买卖计划", "盘前指引")):
            in_section = True
            continue
        if in_section and line.startswith(("📊", "🔥", "💰", "⚡", "📈", "👀", "📌", "🧭", "⚠️", "🌡️", "💡")) and "**" in line:
            break
        if in_section:
            out.append(line.lstrip("·- ").strip())
    if out:
        return out[:max_lines]

    keywords = ("风险级别", "开仓", "买入", "卖出", "控仓", "仓位", "追高", "只卖")
    fallback = [
        line.lstrip("·- ").strip()
        for line in lines
        if any(keyword in line for keyword in keywords)
    ]
    return fallback[:max_lines]


def classify_market_guidance_tone(text: str) -> str:
    raw = str(text or "")
    compact = re.sub(r"\s+", "", raw)
    m = re.search(r"风险级别[：:]\s*([^\n。；;，,]+)", raw)
    level = (m.group(1) if m else "").strip()
    if any(word in level for word in ("防守", "极弱", "只卖", "暂停")):
        return "defensive"
    if any(word in level for word in ("谨慎", "偏弱", "控仓")):
        return "cautious"
    if any(word in level for word in ("进攻", "积极", "强")):
        return "offensive"
    if any(word in level for word in ("平衡", "中性")):
        return "balanced"

    defensive_hits = ("只卖不买", "暂停新开仓", "空头占优", "风险端更强", "竞价偏弱", "跌停风险不弱")
    cautious_hits = ("结构性偏弱", "中性偏谨慎", "谨慎追高", "控仓", "仓位和追高保守", "独苗")
    offensive_hits = ("多头占优", "进攻较强", "赚钱效应较活跃", "竞价进攻较强")
    balanced_hits = ("结构性偏强", "有一定进攻", "正常建仓")
    if any(hit in compact for hit in defensive_hits):
        return "defensive"
    if any(hit in compact for hit in cautious_hits):
        return "cautious"
    if any(hit in compact for hit in offensive_hits):
        return "offensive"
    if any(hit in compact for hit in balanced_hits):
        return "balanced"
    return "neutral"


def market_guidance_blocks_new_buys(text: str) -> bool:
    raw = str(text or "")
    compact = re.sub(r"\s+", "", raw)
    m = re.search(r"风险级别[：:]\s*([^\n。；;，,]+)", raw)
    level = (m.group(1) if m else "").strip()
    if any(word in level for word in ("极弱", "只卖", "暂停")):
        return True
    hard_pause_hits = (
        "只卖不买",
        "只卖/不买",
        "只卖不买入",
        "暂停新开仓",
        "暂停买入",
        "禁止买入",
        "停止买入",
        "只允许卖出",
        "只允许卖出/持有",
        "只允许持有/卖出",
        "仅允许卖出",
        "仅允许卖出/持有",
        "仅允许持有/卖出",
    )
    return any(hit in compact for hit in hard_pause_hits)


def _market_tone_label(tone: str) -> str:
    return {
        "offensive": "进攻",
        "balanced": "平衡",
        "neutral": "中性",
        "cautious": "谨慎",
        "defensive": "防守",
    }.get(tone, "中性")


def _extract_market_report_summary_line(report: dict[str, Any]) -> str:
    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    summary = str(metadata.get("summary") or "").strip()
    if summary:
        return summary
    for line in str(report.get("content") or "").splitlines():
        clean = line.strip()
        if clean.startswith("💬"):
            return clean.lstrip("💬").strip()
    return ""


def _format_overnight_sector_mapping(item: Any) -> str:
    if isinstance(item, dict):
        sector = str(item.get("us_sector") or item.get("label") or item.get("name") or "").strip()
        proxy = str(item.get("proxy") or item.get("symbol") or "").strip()
        pct = str(item.get("change_pct_text") or "").strip()
        mapping_raw = item.get("a_share_mapping") or item.get("mapping") or []
        if isinstance(mapping_raw, str):
            mapping = mapping_raw
        else:
            mapping = "、".join(str(x).strip() for x in mapping_raw if str(x).strip())
        strategy = str(item.get("strategy") or item.get("bias") or "").strip()
        head = sector
        if proxy:
            head = f"{head}({proxy})" if head else proxy
        if pct:
            head = f"{head} {pct}".strip()
        parts = [head]
        if mapping:
            parts.append(f"A股：{mapping}")
        if strategy:
            parts.append(strategy)
        return "；".join(part for part in parts if part)
    return str(item or "").strip().strip("`").lstrip("·- ").strip()


def extract_overnight_us_sector_mappings(
    content: str,
    metadata: dict[str, Any] | None = None,
    max_lines: int = 5,
) -> list[str]:
    raw = (metadata or {}).get("sector_mappings")
    out: list[str] = []
    if isinstance(raw, list):
        out = [_format_overnight_sector_mapping(item) for item in raw]
        out = [line for line in out if line]
        if out:
            return out[:max_lines]

    lines = [line.strip() for line in str(content or "").splitlines()]
    in_section = False
    for line in lines:
        clean = line.strip()
        if not clean:
            if in_section and out:
                break
            continue
        if "美股板块映射" in clean:
            in_section = True
            continue
        if in_section and clean.startswith(("📊", "🔥", "💰", "⚡", "📈", "👀", "📌", "🧭", "🎯", "⚠️", "🌡️", "💡")) and "**" in clean:
            break
        if in_section:
            text = clean.lstrip("·- ").replace("`", "").strip()
            if text and "暂不可用" not in text:
                out.append(text)
    return out[:max_lines]


def _overnight_us_context_from_report(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {"available": False}
    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else None
    guidance = extract_market_guidance_lines(str(report.get("content") or ""), metadata, max_lines=8)
    sector_mappings = extract_overnight_us_sector_mappings(str(report.get("content") or ""), metadata, max_lines=5)
    tone_text = "\n".join(guidance) or str(report.get("content") or "")
    tone = classify_market_guidance_tone(tone_text)
    return {
        "available": True,
        "tone": tone,
        "tone_label": _market_tone_label(tone),
        "source_title": report.get("title") or OVERNIGHT_US_MARKET_TITLE,
        "source_time": report.get("time") or "",
        "summary": _extract_market_report_summary_line(report),
        "guidance_lines": guidance,
        "sector_mappings": sector_mappings,
    }


def _apply_overnight_us_adjustment(ctx: dict[str, Any]) -> None:
    overnight_us = ctx.get("overnight_us") if isinstance(ctx.get("overnight_us"), dict) else {}
    if not overnight_us or not overnight_us.get("available"):
        return
    tone = str(overnight_us.get("tone") or "neutral")
    if tone == "defensive":
        ctx["max_open_positions"] = min(int(ctx.get("max_open_positions", MAX_OPEN_POSITIONS)), min(MAX_OPEN_POSITIONS, 3))
        ctx["max_new_buys_per_decision"] = min(int(ctx.get("max_new_buys_per_decision", MAX_NEW_BUYS_PER_DECISION)), 1)
        ctx["max_total_position_pct"] = min(float(ctx.get("max_total_position_pct", MAX_TOTAL_POSITION_PCT)), 50.0)
        ctx["min_cash_reserve_pct"] = max(float(ctx.get("min_cash_reserve_pct", MIN_CASH_RESERVE_PCT)), 45.0)
        ctx["buy_budget_multiplier"] = min(float(ctx.get("buy_budget_multiplier", 1.0)), 0.55)
    elif tone == "cautious":
        ctx["max_new_buys_per_decision"] = min(int(ctx.get("max_new_buys_per_decision", MAX_NEW_BUYS_PER_DECISION)), 1)
        ctx["max_total_position_pct"] = min(float(ctx.get("max_total_position_pct", MAX_TOTAL_POSITION_PCT)), 60.0)
        ctx["min_cash_reserve_pct"] = max(float(ctx.get("min_cash_reserve_pct", MIN_CASH_RESERVE_PCT)), 35.0)
        ctx["buy_budget_multiplier"] = min(float(ctx.get("buy_budget_multiplier", 1.0)), 0.8)


def _market_context_base(now: datetime | None = None) -> dict[str, Any]:
    phase = market_session_phase(now)
    return {
        "enabled": MARKET_GUIDANCE_ENABLED,
        "available": False,
        "tone": "neutral",
        "tone_label": "中性",
        "phase": phase,
        "max_open_positions": MAX_OPEN_POSITIONS,
        "max_new_buys_per_decision": MAX_NEW_BUYS_PER_DECISION,
        "max_total_position_pct": MAX_TOTAL_POSITION_PCT,
        "min_cash_reserve_pct": MIN_CASH_RESERVE_PCT,
        "buy_budget_multiplier": 1.0,
        "allow_new_buys": True,
        "guidance_lines": [],
        "reports": [],
        "source_title": "",
        "source_time": "",
        "session_note": "",
        "overnight_us": {"available": False},
    }


def derive_market_strategy_context(reports: list[dict[str, Any]] | None, now: datetime | None = None) -> dict[str, Any]:
    """Turn the latest market-monitor summaries into enforceable trading limits."""
    ctx = _market_context_base(now)
    reports = [r for r in (reports or []) if isinstance(r, dict)]
    overnight_us_report = next((r for r in reports if _is_overnight_us_market_report(r)), None)
    primary_reports = [r for r in reports if not _is_overnight_us_market_report(r)]
    latest = (primary_reports or reports)[0] if reports else {}
    guidance_lines = extract_market_guidance_lines(
        str(latest.get("content") or ""),
        latest.get("metadata") if isinstance(latest.get("metadata"), dict) else None,
    ) if latest else []
    tone_text = "\n".join(guidance_lines) or str(latest.get("content") or "")
    tone = classify_market_guidance_tone(tone_text)
    block_new_buys = market_guidance_blocks_new_buys(tone_text)
    ctx.update({
        "available": bool(reports),
        "tone": tone,
        "tone_label": _market_tone_label(tone),
        "guidance_lines": guidance_lines,
        "source_title": latest.get("title") or "",
        "source_time": latest.get("time") or "",
        "overnight_us": _overnight_us_context_from_report(overnight_us_report),
        "reports": [
            {
                "title": r.get("title") or "盘面监控",
                "time": r.get("time") or "",
                "guidance": extract_market_guidance_lines(
                    str(r.get("content") or ""),
                    r.get("metadata") if isinstance(r.get("metadata"), dict) else None,
                    max_lines=5,
                ),
            }
            for r in reports[:3]
        ],
    })

    if tone == "offensive":
        ctx["max_new_buys_per_decision"] = min(MAX_NEW_BUYS_PER_DECISION, 2)
    elif tone == "balanced":
        ctx["max_open_positions"] = min(MAX_OPEN_POSITIONS, 4)
        ctx["max_new_buys_per_decision"] = min(MAX_NEW_BUYS_PER_DECISION, 1)
        ctx["max_total_position_pct"] = min(MAX_TOTAL_POSITION_PCT, 65.0)
        ctx["min_cash_reserve_pct"] = max(MIN_CASH_RESERVE_PCT, 30.0)
    elif tone == "cautious":
        ctx["max_open_positions"] = min(MAX_OPEN_POSITIONS, 3)
        ctx["max_new_buys_per_decision"] = min(MAX_NEW_BUYS_PER_DECISION, 1)
        ctx["max_total_position_pct"] = min(MAX_TOTAL_POSITION_PCT, 50.0)
        ctx["min_cash_reserve_pct"] = max(MIN_CASH_RESERVE_PCT, 40.0)
        ctx["buy_budget_multiplier"] = 0.6
    elif tone == "defensive":
        ctx["max_open_positions"] = min(MAX_OPEN_POSITIONS, 2)
        ctx["max_new_buys_per_decision"] = min(MAX_NEW_BUYS_PER_DECISION, 1)
        ctx["max_total_position_pct"] = min(MAX_TOTAL_POSITION_PCT, 35.0)
        ctx["min_cash_reserve_pct"] = max(MIN_CASH_RESERVE_PCT, 60.0)
        ctx["buy_budget_multiplier"] = 0.35

    if block_new_buys:
        ctx["allow_new_buys"] = False
        ctx["max_new_buys_per_decision"] = 0
        ctx["buy_budget_multiplier"] = 0.0

    _apply_overnight_us_adjustment(ctx)

    if ctx["phase"] in {"morning", "lunch"}:
        before = int(ctx["max_open_positions"])
        ctx["max_open_positions"] = min(before, MORNING_MAX_OPEN_POSITIONS)
        if int(ctx["max_open_positions"]) < MAX_OPEN_POSITIONS:
            reserve_slots = MAX_OPEN_POSITIONS - int(ctx["max_open_positions"])
            ctx["session_note"] = f"午盘前最多持有{ctx['max_open_positions']}只，保留{reserve_slots}个仓位给午后确认"
        if tone in {"neutral", "balanced"}:
            ctx["max_new_buys_per_decision"] = min(int(ctx["max_new_buys_per_decision"]), 1)

    ctx["max_open_positions"] = max(0, int(ctx["max_open_positions"]))
    ctx["max_new_buys_per_decision"] = max(0, int(ctx["max_new_buys_per_decision"]))
    ctx["max_total_position_pct"] = round(float(ctx["max_total_position_pct"]), 2)
    ctx["min_cash_reserve_pct"] = round(float(ctx["min_cash_reserve_pct"]), 2)
    ctx["buy_budget_multiplier"] = round(float(ctx["buy_budget_multiplier"]), 3)
    return ctx


def _market_session_elapsed_minutes(source_dt: datetime) -> int:
    minute = source_dt.hour * 60 + source_dt.minute
    morning_start = 9 * 60 + 30
    morning_end = 11 * 60 + 30
    afternoon_start = 13 * 60
    if minute <= morning_start:
        return 1
    if minute <= morning_end:
        return minute - morning_start
    if minute < afternoon_start:
        return 120
    return min(240, 120 + minute - afternoon_start)


def evaluate_market_hard_stop(
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None,
    source_dt: datetime,
) -> dict[str, Any]:
    """Apply a confirmed composite market stop and symmetric recovery gate."""
    result = dict(snapshot)
    previous = previous_snapshot if isinstance(previous_snapshot, dict) else {}
    previous_dt = parse_ts(str(previous.get("quote_time") or previous.get("captured_at") or ""))
    same_day = previous_dt is not None and previous_dt.date() == source_dt.date()
    same_snapshot = same_day and previous_dt == source_dt

    elapsed = _market_session_elapsed_minutes(source_dt)
    total_amount = max(0.0, _safe_float(result.get("total_amount"), 0.0))
    amount_per_minute = total_amount / elapsed if elapsed > 0 else 0.0
    previous_rate = _safe_float(previous.get("amount_per_minute"), 0.0) if same_day else 0.0
    liquidity_cold = (
        previous_rate > 0
        and amount_per_minute <= previous_rate * MARKET_HARD_STOP_LIQUIDITY_RATE_RATIO
    )

    up = max(0, int(_safe_float(result.get("up"), 0.0)))
    down = max(0, int(_safe_float(result.get("down"), 0.0)))
    limit_up = max(0, int(_safe_float(result.get("limit_up"), 0.0)))
    limit_down = max(0, int(_safe_float(result.get("limit_down"), 0.0)))
    median_pct = _safe_float(result.get("median_change_pct"), 0.0)
    core_count = max(0, int(_safe_float(result.get("core_index_count"), 0.0)))
    below_count = max(0, int(_safe_float(result.get("index_below_ma20_count"), 0.0)))
    index_average_pct = _safe_float(result.get("index_average_change_pct"), 0.0)

    index_break = core_count >= 3 and below_count >= 2 and index_average_pct <= -0.5
    breadth_break = down >= max(100, int(up * 1.5)) and median_pct <= -0.8
    limit_down_spread = limit_down >= max(5, limit_up)
    candidate = index_break and breadth_break and (limit_down_spread or liquidity_cold)
    recovery_candidate = (
        core_count >= 3
        and below_count <= 1
        and (up >= down or median_pct >= -0.2)
        and limit_down <= max(3, limit_up)
    )

    state_keys = (
        "hard_stop_candidate", "hard_stop_confirmations", "hard_stop_active",
        "recovery_candidate", "recovery_confirmations", "hard_stop_reasons",
    )
    if same_snapshot:
        for key in state_keys:
            if key in previous:
                result[key] = previous[key]
    else:
        previous_active = bool(previous.get("hard_stop_active")) if same_day else False
        if candidate:
            confirmations = (
                int(previous.get("hard_stop_confirmations") or 0) + 1
                if same_day and previous.get("hard_stop_candidate")
                else 1
            )
            recovery_confirmations = 0
            active = previous_active or confirmations >= MARKET_HARD_STOP_CONFIRMATIONS
        elif previous_active:
            confirmations = int(previous.get("hard_stop_confirmations") or MARKET_HARD_STOP_CONFIRMATIONS)
            recovery_confirmations = (
                int(previous.get("recovery_confirmations") or 0) + 1
                if recovery_candidate and previous.get("recovery_candidate")
                else (1 if recovery_candidate else 0)
            )
            active = recovery_confirmations < MARKET_HARD_STOP_RECOVERY_CONFIRMATIONS
        else:
            confirmations = 0
            recovery_confirmations = 0
            active = False
        reasons = []
        if index_break:
            reasons.append(f"核心指数{below_count}/{core_count}跌破20日线")
        if breadth_break:
            reasons.append(f"下跌{down}家/上涨{up}家，中位数{median_pct:+.2f}%")
        if limit_down_spread:
            reasons.append(f"跌停{limit_down}家扩散")
        if liquidity_cold:
            reasons.append("成交速率较上次快照下降25%以上")
        result.update({
            "hard_stop_candidate": candidate,
            "hard_stop_confirmations": confirmations,
            "hard_stop_active": active,
            "recovery_candidate": recovery_candidate,
            "recovery_confirmations": recovery_confirmations,
            "hard_stop_reasons": reasons,
        })

    result["amount_per_minute"] = round(amount_per_minute, 2)
    result["liquidity_cold"] = liquidity_cold
    return result


def _periodic_market_snapshot_report(
    b1_payload: dict[str, Any] | None,
    now: datetime | None = None,
    previous_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build a synthetic market report from the quote batch embedded in a B1 run."""
    payload = b1_payload if isinstance(b1_payload, dict) else {}
    snapshot = payload.get("market_snapshot") if isinstance(payload.get("market_snapshot"), dict) else {}
    if not snapshot:
        return None

    now = now or datetime.now()
    sample_count = max(0, int(_safe_float(snapshot.get("sample_count"), 0.0)))
    pool_count = max(sample_count, int(_safe_float(snapshot.get("pool_count"), sample_count)))
    coverage = _safe_float(snapshot.get("coverage"), sample_count / pool_count if pool_count else 0.0)
    up = max(0, int(_safe_float(snapshot.get("up"), 0.0)))
    down = max(0, int(_safe_float(snapshot.get("down"), 0.0)))
    flat = max(0, int(_safe_float(snapshot.get("flat"), max(sample_count - up - down, 0))))
    limit_up = max(0, int(_safe_float(snapshot.get("limit_up"), 0.0)))
    limit_down = max(0, int(_safe_float(snapshot.get("limit_down"), 0.0)))
    if sample_count < PERIODIC_MARKET_MIN_SAMPLE or coverage < PERIODIC_MARKET_MIN_COVERAGE:
        return None
    counted = up + down + flat
    if abs(counted - sample_count) > max(5, int(sample_count * 0.02)):
        return None
    if up + down < max(100, int(sample_count * PERIODIC_MARKET_MIN_ACTIVE_RATIO)):
        return None

    source_time = str(snapshot.get("quote_time") or snapshot.get("captured_at") or payload.get("generated_at") or "")
    source_dt = parse_ts(source_time)
    if source_dt is None or source_dt.date() != now.date():
        return None
    if source_dt.time() < dtime(9, 30) or source_dt.time() > dtime(15, 0):
        return None
    age_seconds = (now - source_dt).total_seconds()
    if age_seconds < -60 or age_seconds > PERIODIC_MARKET_SNAPSHOT_MAX_AGE_SECONDS:
        return None

    snapshot = evaluate_market_hard_stop(snapshot, previous_snapshot, source_dt)

    if up > down * 1.4 and limit_up >= max(limit_down * 2, 5):
        tone = "offensive"
    elif down > up * 1.3 and limit_down >= max(limit_up, 3):
        tone = "defensive"
    elif up > down:
        tone = "balanced"
    else:
        tone = "cautious"

    label = _market_tone_label(tone)
    if snapshot.get("hard_stop_active"):
        pace = "复合风险条件连续确认，停止新开仓，只允许卖出/持有"
        buy = "候选股即使技术达标也不买，等待指数、广度和风险端连续修复"
        sell = "按原策略处理破位和弱势持仓，不因市场硬停止无差别清仓"
    elif tone == "offensive":
        pace = "主板广度和涨停端共振，可围绕确认后的主线分批试错；单轮新仓不超过2笔"
        buy = "只做板块联动、回踩承接或右侧突破确认，不因标签转强直接追高"
        sell = "强势持仓可跟随，放量滞涨或跌回关键均线时执行移动止盈"
    elif tone == "balanced":
        pace = "结构性偏强，先试错1笔，再根据板块承接决定是否扩仓"
        buy = "优先选择资金与板块共振的候选，弱分支和独立冲高不买"
        sell = "持仓强弱分层，弱于指数或板块的低效仓位优先处理"
    elif tone == "cautious":
        pace = "涨跌广度偏弱或分化，本轮新仓不超过1笔并保留现金"
        buy = "只看贴近BBI/均线且有板块承接的高确定性候选，不追高"
        sell = "弱于板块、破位或冲高回落的持仓优先降风险"
    else:
        pace = "防守观察；复合风险尚未连续确认，新仓最多1笔且必须高确定性"
        buy = "只看贴近关键支撑且有板块承接的候选，不追高、不扩仓"
        sell = "弱于板块、跌破BBI/白线或放量回落的持仓优先减仓或退出"

    average_pct = _safe_float(snapshot.get("average_change_pct"), 0.0)
    median_pct = _safe_float(snapshot.get("median_change_pct"), 0.0)
    snapshot_universe_label = str(snapshot.get("stock_universe_label") or "主板（非ST）")
    breadth_line = (
        f"定时重评：{snapshot_universe_label}样本{sample_count}只，上涨{up}、下跌{down}、平盘{flat}，"
        f"涨停{limit_up}、跌停{limit_down}，均值{average_pct:+.2f}%、中位数{median_pct:+.2f}%"
    )
    guidance = [
        f"风险级别：{label}",
        breadth_line,
        f"开仓节奏：{pace}",
        f"买入指引：{buy}",
        f"卖出/风控：{sell}",
    ]
    title = "实战定时选股实时盘面" if payload.get("schedule_slot") else "实战选股实时盘面"
    return {
        "title": title,
        "time": source_time,
        "content": "🎯 **今日买卖指引**\n" + "\n".join(f"· {line}" for line in guidance),
        "metadata": {
            "decision_guidance": guidance,
            "refresh_mode": "b1_periodic",
            "market_snapshot": {
                **snapshot,
                "source": snapshot.get("source") or "b1_mainboard_quotes",
                "universe": snapshot.get("universe") or "mainboard_non_st",
                "quote_time": source_time,
                "pool_count": pool_count,
                "sample_count": sample_count,
                "coverage": round(coverage, 4),
                "average_change_pct": round(average_pct, 3),
                "median_change_pct": round(median_pct, 3),
            },
        },
    }


def market_strategy_context_for_b1(
    b1_payload: dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Use the current B1 breadth snapshot first, with archived reports as fallback."""
    state = load_state()
    previous_ctx = state.get("market_decision_context") if isinstance(state.get("market_decision_context"), dict) else {}
    previous_snapshot = previous_ctx.get("market_snapshot") if isinstance(previous_ctx.get("market_snapshot"), dict) else {}
    live_report = _periodic_market_snapshot_report(b1_payload, now, previous_snapshot)
    if not live_report:
        return current_market_strategy_context(now)
    reports = load_today_market_monitor_reports(now)
    live_dt = parse_ts(str(live_report.get("time") or ""))
    newest_report_dt = max(
        (
            parsed
            for report in reports
            if not _is_overnight_us_market_report(report)
            for parsed in [parse_ts(str(report.get("time") or ""))]
            if parsed is not None
        ),
        default=None,
    )
    if newest_report_dt is not None and live_dt is not None and newest_report_dt > live_dt:
        return derive_market_strategy_context(reports, now)
    reports = [live_report, *reports]
    ctx = derive_market_strategy_context(reports, now)
    metadata = live_report.get("metadata") if isinstance(live_report.get("metadata"), dict) else {}
    ctx["context_kind"] = "current"
    ctx["context_as_of"] = live_report.get("time") or ""
    ctx["refresh_mode"] = "b1_periodic"
    ctx["market_snapshot"] = metadata.get("market_snapshot") or {}
    return ctx


def refresh_market_strategy_context_for_b1(
    b1_payload: dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist the latest periodic context even when a B1 scan has no candidates."""
    ctx = market_strategy_context_for_b1(b1_payload, now)
    compact = compact_market_strategy_context(ctx)
    state = load_state()
    state["market_decision_context"] = compact
    save_state(state)
    return ctx


def current_market_strategy_context(now: datetime | None = None) -> dict[str, Any]:
    return derive_market_strategy_context(load_today_market_monitor_reports(now), now)


def compact_market_strategy_context(ctx: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "enabled", "available", "tone", "tone_label", "phase", "max_open_positions",
        "max_new_buys_per_decision", "allow_new_buys", "source_title", "source_time",
        "session_note", "guidance_lines", "overnight_us", "context_kind", "context_as_of",
        "refresh_mode", "market_snapshot",
    ):
        value = ctx.get(key)
        if key == "overnight_us" and not (isinstance(value, dict) and value.get("available")):
            continue
        if value not in (None, "", []):
            out[key] = value
    return out


def select_current_market_strategy_context(
    state: dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the newest current context from reports or the last B1 refresh."""
    report_ctx = compact_market_strategy_context(current_market_strategy_context(now))
    saved = (state or {}).get("market_decision_context")
    saved_ctx = dict(saved) if isinstance(saved, dict) else {}
    report_dt = parse_ts(str(report_ctx.get("source_time") or ""))
    saved_dt = parse_ts(str(saved_ctx.get("source_time") or ""))
    if saved_ctx and saved_dt and (report_dt is None or saved_dt > report_dt):
        selected = saved_ctx
    else:
        selected = report_ctx or saved_ctx
    if selected:
        selected["context_kind"] = "current"
        selected["context_as_of"] = selected.get("source_time") or selected.get("context_as_of") or ""
    return selected


def format_market_strategy_context_for_prompt(ctx: dict[str, Any]) -> str:
    if not ctx.get("enabled"):
        return "【今日盘面监控指引】已关闭。"
    tone = str(ctx.get("tone") or "neutral")
    if not ctx.get("allow_new_buys", True):
        position_bias = "暂停新买，只处理卖出/持有"
    elif tone == "offensive":
        position_bias = "可提高集中度，但必须给出高确定性理由"
    elif tone == "balanced":
        position_bias = "分批试错，避免一次性把节奏打满"
    elif tone == "cautious":
        position_bias = "缩小试错，优先等待承接确认"
    elif tone == "defensive":
        position_bias = "轻仓观察，除非极高确定性否则不加仓"
    else:
        position_bias = "按候选确定性和账户状态自定仓位"
    lines = [
        "【今日盘面监控指引】",
        (
            f"风险级别：{ctx.get('tone_label', '中性')}；阶段：{ctx.get('phase', '-')}; "
            f"节奏：最多{ctx.get('max_open_positions')}只、单轮新仓≤{ctx.get('max_new_buys_per_decision')}笔；"
            f"仓位倾向：{position_bias}。"
        ),
    ]
    if not ctx.get("allow_new_buys", True):
        lines.append("执行层当前按盘面指引暂停买入，只允许卖出/持有。")
    if ctx.get("session_note"):
        lines.append(str(ctx.get("session_note")))
    if ctx.get("source_title") or ctx.get("source_time"):
        lines.append(f"最新来源：{ctx.get('source_title') or '盘面监控'} {ctx.get('source_time') or ''}".strip())
    overnight_us = ctx.get("overnight_us") if isinstance(ctx.get("overnight_us"), dict) else {}
    if overnight_us.get("available"):
        lines.append("【隔夜美股盘面】")
        lines.append(
            f"风险级别：{overnight_us.get('tone_label', '中性')}；"
            f"来源：{overnight_us.get('source_title') or OVERNIGHT_US_MARKET_TITLE} {overnight_us.get('source_time') or ''}".strip()
        )
        if overnight_us.get("summary"):
            lines.append(f"摘要：{overnight_us.get('summary')}")
        sector_mappings = [
            str(line).strip()
            for line in (overnight_us.get("sector_mappings") or [])
            if str(line).strip()
        ]
        if sector_mappings:
            lines.append("板块映射：" + "；".join(sector_mappings[:5]))
        us_guidance = [
            str(line).strip()
            for line in (overnight_us.get("guidance_lines") or [])
            if str(line).strip() and not str(line).strip().startswith("风险级别")
        ]
        lines.extend(f"- {line}" for line in us_guidance[:6])
    guidance = ctx.get("guidance_lines") or []
    if guidance:
        lines.extend(f"- {line}" for line in guidance[:8])
    else:
        if ctx.get("phase") in {"morning", "lunch"}:
            lines.append("- 暂无今日盘面总结，按午盘前保留仓位和静态风控执行。")
        else:
            lines.append("- 暂无今日盘面总结，按静态风控执行。")
    return "\n".join(lines)


def _compact_number(value: Any, digits: int = 2) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(str(value).replace(",", "").replace("%", "").strip())
        if not math.isfinite(number):
            return None
        return round(number, digits)
    except Exception:
        return None


def _compact_text(value: Any, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _source_status(data: dict[str, Any] | None) -> str:
    if not isinstance(data, dict) or not data:
        return "empty"
    if data.get("error"):
        return "stale" if data.get("stale_cache") else "error"
    if data.get("stale_cache"):
        return "stale"
    return "ok"


def _fetch_decision_source(label: str, fetcher, empty: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = fetcher()
        if isinstance(payload, dict):
            return payload
        return {**empty, "error": f"{label} returned {type(payload).__name__}"}
    except Exception as exc:
        return {**empty, "error": f"{type(exc).__name__}: {exc}"}


def fetch_global_decision_sources(force: bool = False) -> dict[str, Any]:
    """Fetch reusable dashboard market channels for model decisions.

    Each producer already has its own cache/stale fallback. This wrapper adds a
    short decision-level cache so a single B1 decision does not refetch the same
    dashboard channels several times.
    """
    if not DECISION_INTELLIGENCE_ENABLED:
        return {"enabled": False, "generated_at": now_ts(), "sources": {}}
    now_value = time.time()
    cached = DECISION_INTELLIGENCE_CACHE.get("data")
    if (
        not force
        and isinstance(cached, dict)
        and now_value - float(DECISION_INTELLIGENCE_CACHE.get("ts") or 0) < DECISION_INTELLIGENCE_TTL_SECONDS
    ):
        return cached

    data: dict[str, Any] = {"enabled": True, "generated_at": now_ts(), "sources": {}}
    try:
        from indices_dashboard_api import fetch_indices_data
        data["sources"]["indices"] = _fetch_decision_source(
            "indices",
            fetch_indices_data,
            {"items": []},
        )
    except Exception as exc:
        data["sources"]["indices"] = {"items": [], "error": f"{type(exc).__name__}: {exc}"}

    try:
        from sectors_dashboard_api import fetch_sector_data
        data["sources"]["sectors"] = _fetch_decision_source(
            "sectors",
            fetch_sector_data,
            {"gain_top": [], "loss_top": [], "items": []},
        )
    except Exception as exc:
        data["sources"]["sectors"] = {"gain_top": [], "loss_top": [], "items": [], "error": f"{type(exc).__name__}: {exc}"}

    try:
        from money_flow_dashboard_api import fetch_money_flow
        data["sources"]["money_flow"] = _fetch_decision_source(
            "money_flow",
            fetch_money_flow,
            {"inflow": [], "outflow": []},
        )
    except Exception as exc:
        data["sources"]["money_flow"] = {"inflow": [], "outflow": [], "error": f"{type(exc).__name__}: {exc}"}

    try:
        from hot_stocks_dashboard_api import fetch_hot_stocks
        data["sources"]["hot_stocks"] = _fetch_decision_source(
            "hot_stocks",
            lambda: fetch_hot_stocks("amount"),
            {"items": [], "amount_top": [], "turnover_top": [], "gain_top": []},
        )
    except Exception as exc:
        data["sources"]["hot_stocks"] = {
            "items": [],
            "amount_top": [],
            "turnover_top": [],
            "gain_top": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        from market_flow_dashboard_api import fetch_market_flow
        data["sources"]["market_flow"] = _fetch_decision_source(
            "market_flow",
            fetch_market_flow,
            {"total_inflow_yi": None, "total_outflow_yi": None, "net_flow_yi": None},
        )
    except Exception as exc:
        data["sources"]["market_flow"] = {
            "total_inflow_yi": None,
            "total_outflow_yi": None,
            "net_flow_yi": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    DECISION_INTELLIGENCE_CACHE.update({"ts": now_value, "data": data})
    return data


def compact_indices_for_decision(payload: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
    wanted_order = {
        "sh": 10, "sz": 11, "cyb": 12, "kc50": 13,
        "a50_fut": 20,
        "dow": 30, "nas": 31, "spx": 32,
        "spx_fut": 40, "nas_fut": 41, "dow_fut": 42,
        "xau": 50, "brent": 51,
    }
    items: list[dict[str, Any]] = []
    for raw in payload.get("items") or []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "")
        market_type = str(raw.get("market_type") or "")
        if key not in wanted_order and market_type not in {"a_index", "us_index", "a_futures", "us_futures", "commodity"}:
            continue
        item = {
            "key": key,
            "name": raw.get("name") or key,
            "market_type": market_type,
            "price": _compact_number(raw.get("price"), 3),
            "change_pct": _compact_number(raw.get("change_pct"), 2),
            "time": raw.get("time") or "",
        }
        items.append(item)
    max_items = limit or DECISION_INTELLIGENCE_MAX_ITEMS * 3
    return sorted(items, key=lambda row: wanted_order.get(str(row.get("key") or ""), 999))[:max_items]


def _compact_rank_rows(rows: list[Any], *, pct_key: str = "pct", value_key: str | None = None,
                       limit: int | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        row = {
            "name": raw.get("name") or raw.get("leader") or raw.get("code") or "",
            "code": raw.get("code") or "",
            "pct": _compact_number(raw.get(pct_key), 2),
        }
        if value_key:
            row[value_key] = _compact_number(raw.get(value_key), 2)
        if raw.get("leader"):
            row["leader"] = raw.get("leader")
        if raw.get("amount_yi") is not None:
            row["amount_yi"] = _compact_number(raw.get("amount_yi"), 2)
        if raw.get("turnover") is not None:
            row["turnover"] = _compact_number(raw.get("turnover"), 2)
        out.append({k: v for k, v in row.items() if v not in (None, "", [])})
    return out[: (limit or DECISION_INTELLIGENCE_MAX_ITEMS)]


def compact_portfolio_exposure_for_decision(portfolio: dict[str, Any]) -> dict[str, Any]:
    total_equity = _compact_number(portfolio.get("total_equity"), 2) or 0.0
    cash = _compact_number(portfolio.get("cash"), 2) or 0.0
    market_value = _compact_number(portfolio.get("market_value"), 2) or 0.0
    positions = [p for p in (portfolio.get("positions") or []) if isinstance(p, dict)]
    cash_pct = round(cash / total_equity * 100, 2) if total_equity > 0 else None
    position_pct = round(market_value / total_equity * 100, 2) if total_equity > 0 else None
    top_positions = []
    for pos in sorted(positions, key=lambda p: float(p.get("market_value") or 0), reverse=True)[:DECISION_INTELLIGENCE_MAX_ITEMS]:
        mv = _compact_number(pos.get("market_value"), 2) or 0.0
        top_positions.append({
            "code": pos.get("code"),
            "name": pos.get("name"),
            "strategy_mark_id": pos.get("strategy_mark_id") or pos.get("buy_strategy") or "",
            "strategy_mark_label": pos.get("strategy_mark_label") or buy_strategy_label(str(pos.get("buy_strategy") or "")),
            "last_exit_rule": pos.get("last_exit_rule") or "",
            "position_pct": round(mv / total_equity * 100, 2) if total_equity > 0 else None,
            "pnl_pct": _compact_number(pos.get("pnl_pct"), 2),
            "today_pnl_pct": _compact_number(pos.get("today_pnl_pct"), 2),
            "available_qty": pos.get("available_qty"),
        })
    return {
        "cash_pct": cash_pct,
        "position_pct": position_pct,
        "position_count": len(positions),
        "total_equity": total_equity,
        "cash": cash,
        "market_value": market_value,
        "top_positions": top_positions,
    }


def _topic_name(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    return re.sub(r"(行业|板块|概念|指数)$", "", text)


def build_candidate_market_alignment(
    candidates: list[dict[str, Any]],
    sectors: dict[str, Any],
    money_flow: dict[str, Any],
    hot_stocks: dict[str, Any],
) -> list[dict[str, Any]]:
    strong_topics = {_topic_name(row.get("name")) for row in (sectors.get("gain_top") or [])[:DECISION_INTELLIGENCE_MAX_ITEMS] if isinstance(row, dict)}
    weak_topics = {_topic_name(row.get("name")) for row in (sectors.get("loss_top") or [])[:DECISION_INTELLIGENCE_MAX_ITEMS] if isinstance(row, dict)}
    inflow_topics = {_topic_name(row.get("name")) for row in (money_flow.get("inflow") or [])[:DECISION_INTELLIGENCE_MAX_ITEMS] if isinstance(row, dict)}
    outflow_topics = {_topic_name(row.get("name")) for row in (money_flow.get("outflow") or [])[:DECISION_INTELLIGENCE_MAX_ITEMS] if isinstance(row, dict)}
    hot_codes: set[str] = set()
    for key in ("amount_top", "turnover_top", "gain_top", "items"):
        for row in (hot_stocks.get(key) or [])[:DECISION_INTELLIGENCE_MAX_ITEMS]:
            if isinstance(row, dict):
                code = normalize_code(row.get("code") or "")
                if code:
                    hot_codes.add(code)

    out: list[dict[str, Any]] = []
    for raw in candidates[:8]:
        if not isinstance(raw, dict):
            continue
        code = normalize_code(raw.get("code") or "")
        topic = _topic_name(raw.get("industry") or raw.get("sector") or "")
        flags: list[str] = []
        if topic:
            if any(topic in item or item in topic for item in strong_topics if item):
                flags.append("强势板块")
            if any(topic in item or item in topic for item in weak_topics if item):
                flags.append("弱势板块")
            if any(topic in item or item in topic for item in inflow_topics if item):
                flags.append("资金流入")
            if any(topic in item or item in topic for item in outflow_topics if item):
                flags.append("资金流出")
        if code in hot_codes:
            flags.append("热门榜")
        if flags:
            out.append({
                "code": code,
                "name": raw.get("name") or "",
                "industry": topic,
                "signals": flags[:4],
            })
    return out


def derive_decision_intelligence_notes(ctx: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    indices = ctx.get("indices") or []
    a_indices = [row for row in indices if row.get("market_type") == "a_index" and row.get("change_pct") is not None]
    if a_indices:
        avg = sum(float(row["change_pct"]) for row in a_indices) / len(a_indices)
        if avg <= -1.0:
            notes.append(f"A股核心指数平均{avg:.2f}%，新仓应降级或缩量")
        elif avg >= 1.0:
            notes.append(f"A股核心指数平均+{avg:.2f}%，可优先选择与主线共振候选")
    futures = {row.get("key"): row for row in indices if row.get("change_pct") is not None}
    a50_pct = futures.get("a50_fut", {}).get("change_pct") if isinstance(futures.get("a50_fut"), dict) else None
    if isinstance(a50_pct, (int, float)) and a50_pct <= -0.8:
        notes.append(f"A50期货{a50_pct:.2f}%，上午追高需收紧")
    money_flow = ctx.get("money_flow") or {}
    inflow = sum(float(row.get("net_flow_yi") or 0) for row in (money_flow.get("inflow") or [])[:3] if isinstance(row, dict))
    outflow = abs(sum(float(row.get("net_flow_yi") or 0) for row in (money_flow.get("outflow") or [])[:3] if isinstance(row, dict)))
    if outflow > inflow * 1.3 and outflow > 0:
        notes.append("行业资金流出强于流入，买入需压仓并要求更高确定性")
    portfolio = ctx.get("portfolio") or {}
    cash_pct = portfolio.get("cash_pct")
    position_pct = portfolio.get("position_pct")
    if isinstance(position_pct, (int, float)) and position_pct >= 90:
        notes.append("账户接近满仓，新增买入需有极高确定性或替换弱持仓")
    if isinstance(cash_pct, (int, float)) and cash_pct <= 10:
        notes.append("现金缓冲很薄，继续加仓需在reason说明必要性")
    alignment = ctx.get("candidate_alignment") or []
    if alignment:
        notes.append("候选需结合板块/资金/热门榜共振或背离逐只降权")
    return notes[:8]


def build_decision_intelligence_context(
    portfolio: dict[str, Any],
    candidates: list[dict[str, Any]],
    market_strategy_ctx: dict[str, Any],
    news_context: str = "",
) -> dict[str, Any]:
    if not DECISION_INTELLIGENCE_ENABLED:
        return {"enabled": False}
    raw = fetch_global_decision_sources()
    sources = raw.get("sources") if isinstance(raw, dict) else {}
    sources = sources if isinstance(sources, dict) else {}
    sectors = sources.get("sectors") if isinstance(sources.get("sectors"), dict) else {}
    money_flow = sources.get("money_flow") if isinstance(sources.get("money_flow"), dict) else {}
    hot_stocks = sources.get("hot_stocks") if isinstance(sources.get("hot_stocks"), dict) else {}
    market_flow = sources.get("market_flow") if isinstance(sources.get("market_flow"), dict) else {}
    ctx = {
        "enabled": True,
        "generated_at": raw.get("generated_at") if isinstance(raw, dict) else now_ts(),
        "source_status": {key: _source_status(value if isinstance(value, dict) else {}) for key, value in sources.items()},
        "portfolio": compact_portfolio_exposure_for_decision(portfolio),
        "market_guidance": compact_market_strategy_context(market_strategy_ctx),
        "indices": compact_indices_for_decision(sources.get("indices") if isinstance(sources.get("indices"), dict) else {}),
        "sectors": {
            "gain_top": _compact_rank_rows(sectors.get("gain_top") or sectors.get("items") or []),
            "loss_top": _compact_rank_rows(sectors.get("loss_top") or []),
        },
        "money_flow": {
            "inflow": _compact_rank_rows(money_flow.get("inflow") or [], value_key="net_flow_yi"),
            "outflow": _compact_rank_rows(money_flow.get("outflow") or [], value_key="net_flow_yi"),
        },
        "market_flow": {
            "net_flow_yi": _compact_number(market_flow.get("net_flow_yi"), 2),
            "total_inflow_yi": _compact_number(market_flow.get("total_inflow_yi"), 2),
            "total_outflow_yi": _compact_number(market_flow.get("total_outflow_yi"), 2),
        },
        "hot_stocks": {
            "amount_top": _compact_rank_rows(hot_stocks.get("amount_top") or hot_stocks.get("items") or [], value_key="amount_yi"),
            "turnover_top": _compact_rank_rows(hot_stocks.get("turnover_top") or [], value_key="turnover"),
            "gain_top": _compact_rank_rows(hot_stocks.get("gain_top") or []),
        },
        "news_precheck": {
            "available": bool(str(news_context or "").strip()),
            "text": _compact_text(news_context, 1200) if news_context else "",
        },
    }
    ctx["candidate_alignment"] = build_candidate_market_alignment(candidates, sectors, money_flow, hot_stocks)
    ctx["decision_notes"] = derive_decision_intelligence_notes(ctx)
    return ctx


def safe_decision_intelligence_context(
    portfolio: dict[str, Any],
    candidates: list[dict[str, Any]],
    market_strategy_ctx: dict[str, Any],
    news_context: str = "",
) -> dict[str, Any]:
    try:
        return build_decision_intelligence_context(portfolio, candidates, market_strategy_ctx, news_context)
    except Exception as exc:
        return {
            "enabled": DECISION_INTELLIGENCE_ENABLED,
            "generated_at": now_ts(),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _format_pct(value: Any) -> str:
    number = _compact_number(value, 2)
    if number is None:
        return "--"
    return f"{number:+.2f}%"


def _format_rank_line(rows: list[dict[str, Any]], value_key: str | None = None) -> str:
    parts: list[str] = []
    for row in rows[:DECISION_INTELLIGENCE_MAX_ITEMS]:
        name = str(row.get("name") or row.get("code") or "").strip()
        if not name:
            continue
        suffix = _format_pct(row.get("pct"))
        if value_key and row.get(value_key) is not None:
            suffix += f"/{row.get(value_key)}"
            if value_key.endswith("_yi"):
                suffix += "亿"
        parts.append(f"{name}{suffix}")
    return "；".join(parts) or "无数据"


def format_decision_intelligence_context_for_prompt(ctx: dict[str, Any]) -> str:
    if not ctx.get("enabled"):
        return "【综合决策参考】已关闭。"
    portfolio = ctx.get("portfolio") or {}
    lines = [
        "【综合决策参考】",
        (
            f"账户暴露：持仓{portfolio.get('position_count', 0)}只，"
            f"总仓{portfolio.get('position_pct')}%，现金{portfolio.get('cash_pct')}%，"
            f"权益{portfolio.get('total_equity')}。"
        ),
    ]
    top_positions = portfolio.get("top_positions") or []
    if top_positions:
        lines.append(
            "主要持仓：" + "；".join(
                f"{item.get('code')} {item.get('name')} {item.get('strategy_mark_label') or item.get('strategy_mark_id') or '未标记'} "
                f"仓位{item.get('position_pct')}% 盈亏{_format_pct(item.get('pnl_pct'))}"
                for item in top_positions[:DECISION_INTELLIGENCE_MAX_ITEMS]
            )
        )

    indices = ctx.get("indices") or []
    if indices:
        lines.append(
            "指数/外盘：" + "；".join(
                f"{item.get('name')}{_format_pct(item.get('change_pct'))}"
                for item in indices[:DECISION_INTELLIGENCE_MAX_ITEMS * 3]
            )
        )
    market_guidance = ctx.get("market_guidance") or {}
    overnight = market_guidance.get("overnight_us") if isinstance(market_guidance.get("overnight_us"), dict) else {}
    if overnight and overnight.get("available"):
        summary = _compact_text(overnight.get("summary"), 120)
        lines.append(f"隔夜美股：{overnight.get('tone_label', '中性')}；{summary}")
        sector_mappings = [
            _compact_text(line, 90)
            for line in (overnight.get("sector_mappings") or [])
            if str(line).strip()
        ]
        if sector_mappings:
            lines.append("隔夜美股映射：" + "；".join(sector_mappings[:DECISION_INTELLIGENCE_MAX_ITEMS]))

    sectors = ctx.get("sectors") or {}
    lines.append("板块涨跌：涨幅 " + _format_rank_line(sectors.get("gain_top") or []))
    lines.append("板块涨跌：跌幅 " + _format_rank_line(sectors.get("loss_top") or []))
    money_flow = ctx.get("money_flow") or {}
    lines.append("行业资金：流入 " + _format_rank_line(money_flow.get("inflow") or [], "net_flow_yi"))
    lines.append("行业资金：流出 " + _format_rank_line(money_flow.get("outflow") or [], "net_flow_yi"))
    hot_stocks = ctx.get("hot_stocks") or {}
    lines.append("热门股票：成交额 " + _format_rank_line(hot_stocks.get("amount_top") or [], "amount_yi"))
    if hot_stocks.get("turnover_top"):
        lines.append("热门股票：换手 " + _format_rank_line(hot_stocks.get("turnover_top") or [], "turnover"))

    alignment = ctx.get("candidate_alignment") or []
    if alignment:
        lines.append(
            "候选共振/背离：" + "；".join(
                f"{item.get('code')} {item.get('name')}({','.join(item.get('signals') or [])})"
                for item in alignment[:DECISION_INTELLIGENCE_MAX_ITEMS]
            )
        )
    notes = ctx.get("decision_notes") or []
    if notes:
        lines.append("决策提示：" + "；".join(str(note) for note in notes))
    source_status = ctx.get("source_status") or {}
    if source_status:
        lines.append("来源状态：" + "；".join(f"{key}={value}" for key, value in sorted(source_status.items())))
    lines.append(
        "决策要求：每个BUY/SELL/HOLD都必须同时考虑盘面指引、隔夜美股/美股映射、指数/期货、板块与资金、候选消息面、账户仓位和现金状态；"
        "若任一关键渠道与技术评分冲突，优先降仓、等待确认或HOLD，并在reason写明冲突来源。"
    )
    return "\n".join(lines)


def get_volatility_adjustment(code: str) -> float:
    """根据个股20日波动率调整仓位。高波缩仓，低波加仓。"""
    try:
        import json, urllib.request as _ur
        prefix = "sh" if code.startswith(("6","9")) else "sz"
        url = f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,25,qfq"
        req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _ur.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8","ignore"))
        kd = data.get("data",{}).get(f"{prefix}{code}",{}).get("day",[]) or \
             data.get("data",{}).get(f"{prefix}{code}",{}).get("qfqday",[])
        if len(kd) < 22: return 1.0
        closes = [float(x[2]) for x in kd[-21:] if len(x) >= 6]
        returns = [(closes[i]/closes[i-1]-1)*100 for i in range(1,len(closes))]
        vol = statistics.stdev(returns) if len(returns) > 1 else 0
        
        if vol > 3.5: return HIGH_VOL_REDUCTION       # 高波(>3.5%)→仓位×0.7
        elif vol < 1.5: return LOW_VOL_BOOST           # 低波(<1.5%)→仓位×1.3
        return 1.0
    except Exception:
        return 1.0


def get_adaptive_params() -> dict[str, float]:
    """根据市场情绪自适应调整仓位参考。"""
    sent = check_market_sentiment()
    if sent["sentiment"] == "hot":
        return {"position_mult": 1.0, "label": "热-只排序不放宽风控"}
    elif sent["sentiment"] == "cold":
        return {"position_mult": 0.5, "label": "冷-减半观察"}
    else:
        return {"position_mult": 1.0, "label": "中性"}


def check_daily_loss_budget(state: dict[str, Any]) -> tuple[bool, float]:
    """检查今日累计亏损是否超过预算。"""
    trade_log = state.get("trade_log", [])
    today = today_key()
    today_pnl = sum(t.get("pnl", 0) or 0 for t in trade_log if t.get("time","").startswith(today) and t.get("action")=="SELL")
    positions = state.get("positions") or {}
    unrealized = sum(
        (float(p.get("last_price") or p.get("avg_cost") or 0) - float(p.get("avg_cost") or 0))
        * int(p.get("qty") or p.get("shares") or 0)
        for p in positions.values()
    )
    total_eq = float(state.get("initial_cash") or INITIAL_CASH) + today_pnl + unrealized
    pnl_pct = (total_eq / float(state.get("initial_cash") or INITIAL_CASH) - 1) * 100
    return pnl_pct <= DAILY_LOSS_BUDGET_PCT, pnl_pct


def holding_days(pos: dict[str, Any], today: str | None = None) -> int:
    """Calendar holding days based on the earliest open lot."""
    today = today or today_key()
    lots = pos.get("buy_date_lots") or {}
    open_dates = sorted(date for date, qty in lots.items() if int(qty or 0) > 0)
    if not open_dates:
        return 0
    try:
        return (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(open_dates[0], "%Y-%m-%d")).days
    except Exception:
        return 0


def _sell_signal_config() -> _sell_signals.SellSignalConfig:
    return _sell_signals.SellSignalConfig(
        luzhu_medium_yang_pct=LUZHU_MEDIUM_YANG_PCT,
        s1_high_zone_pct=S1_HIGH_ZONE_PCT,
        s1_uptrend_min_pct=S1_UPTREND_MIN_PCT,
        s1_volume_ratio=S1_VOLUME_RATIO,
        s1_close_low_position=S1_CLOSE_LOW_POSITION,
    )


def _compute_atr(rows: list[dict[str, Any]], lookback: int = ATR_LOOKBACK_DAYS) -> float | None:
    return _sell_signals._compute_atr(rows, lookback)


def _compute_latest_kdj(rows: list[dict[str, Any]]) -> tuple[float | None, float | None, float | None]:
    """Return latest J, previous J and 10-day minimum J."""
    return _sell_signals._compute_latest_kdj(
        rows,
        compute_snapshot=_compute_kdj_snapshot,
    )


def _row_float(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    return _sell_signals._row_float(row, key, default)


def _ma_last(values: list[float], n: int, end: int | None = None) -> float | None:
    return _sell_signals._ma_last(values, n, end)


def _ema_series(values: list[float], n: int) -> list[float]:
    return _sell_signals._ema_series(values, n)


def _compute_bbi_series(closes: list[float]) -> list[float | None]:
    return _sell_signals._compute_bbi_series(closes, ma_last=_ma_last)


def _compute_kdj_snapshot(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    return _sell_signals._compute_kdj_snapshot(rows)


def _compute_macd_dif_series(rows: list[dict[str, Any]]) -> list[float]:
    return _sell_signals._compute_macd_dif_series(
        rows,
        row_float=_row_float,
        ema_series=_ema_series,
    )


def _compute_z_lines(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    return _sell_signals._compute_z_lines(
        rows,
        row_float=_row_float,
        ema_series=_ema_series,
        ma_last=_ma_last,
    )


def _is_fangliang_yinxian(rows: list[dict[str, Any]], index: int) -> bool:
    return _sell_signals._is_fangliang_yinxian(
        rows,
        index,
        row_float=_row_float,
    )


def _compute_sell_score(rows: list[dict[str, Any]], bbi: float | None) -> dict[str, Any]:
    """Zettaranc 防卖飞 V1.4: 5-point hold/reduce/exit score."""
    return _sell_signals._compute_sell_score(
        rows,
        bbi,
        row_float=_row_float,
        compute_bbi=_compute_bbi_series,
        compute_kdj=_compute_kdj_snapshot,
        is_volume_bear=_is_fangliang_yinxian,
        ma_last=_ma_last,
    )


def _detect_luzhu_half(rows: list[dict[str, Any]], bbi: float | None) -> dict[str, Any] | None:
    """Zettaranc 卤煮：站上BBI后连续中/大阳，先放飞半仓。"""
    return _sell_signals._detect_luzhu_half(
        rows,
        bbi,
        config=_sell_signal_config(),
        row_float=_row_float,
    )


def _detect_chuhuo_wushi(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """主力出货五式：涨多后放量阴线/双头/阶梯/绿肥红瘦。"""
    return _sell_signals._detect_chuhuo_wushi(rows, row_float=_row_float)


def _detect_s1_s2_s3(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _sell_signals._detect_s1_s2_s3(
        rows,
        config=_sell_signal_config(),
        row_float=_row_float,
        is_volume_bear=_is_fangliang_yinxian,
        compute_macd=_compute_macd_dif_series,
    )


def find_n_structure_prior_low(
    rows: list[dict[str, Any]],
    entry_idx: int,
    *,
    lookback: int = N_STRUCTURE_STOP_LOOKBACK_DAYS,
) -> dict[str, Any] | None:
    """Return the latest higher swing low before entry in an N-shaped setup."""
    return _find_n_structure_prior_low(
        rows,
        entry_idx,
        lookback=lookback,
        tolerance_pct=N_STRUCTURE_LOW_TOLERANCE_PCT,
    )


def is_zettaranc_strategy(strategy_id: str) -> bool:
    return STRATEGY_DEFINITIONS.get(str(strategy_id or ""), {}).get("persona") == "zettaranc"


def is_sector_tide_strategy(strategy_id: str) -> bool:
    return STRATEGY_DEFINITIONS.get(str(strategy_id or ""), {}).get("persona") == "sector_tide"


def sector_tide_position_open_risk_pct(pos: dict[str, Any], total_equity: float) -> float:
    """Mark one open Sector Tide position to its current stressed stop risk."""
    mark_price = _safe_float(pos.get("last_price") or pos.get("close") or pos.get("avg_cost"), 0.0)
    effective_distance = stored_position_effective_loss_distance_pct(pos, mark_price=mark_price)
    if effective_distance <= 0:
        effective_distance = _safe_float(pos.get("effective_loss_distance_pct"), 0.0)
    return position_open_risk_pct(position_market_value(pos, mark_price), total_equity, effective_distance)


def sector_tide_existing_open_risk_pct(
    positions: dict[str, Any],
    total_equity: float,
    *,
    excluding_code: str = "",
    industry: str | None = None,
) -> float:
    total = 0.0
    normalized_exclusion = normalize_code(excluding_code)
    for position_code, pos in positions.items():
        if not isinstance(pos, dict) or position_qty(pos) <= 0:
            continue
        if normalize_code(position_code) == normalized_exclusion:
            continue
        if not is_sector_tide_strategy(position_entry_strategy(pos)):
            continue
        if industry is not None and str(pos.get("industry") or pos.get("sector") or "").strip() != industry:
            continue
        total += sector_tide_position_open_risk_pct(pos, total_equity)
    return total


def sync_sector_tide_position_context(state: dict[str, Any], b1_payload: dict[str, Any] | None) -> int:
    """Persist the latest market/industry tide on open positions once per scan day."""
    payload = b1_payload if isinstance(b1_payload, dict) else {}
    context = payload.get("sector_tide_context") if isinstance(payload.get("sector_tide_context"), dict) else {}
    market = context.get("market") if isinstance(context.get("market"), dict) else {}
    sectors = context.get("sectors") if isinstance(context.get("sectors"), dict) else {}
    stocks = context.get("stocks") if isinstance(context.get("stocks"), dict) else {}
    if not context or not market:
        return 0

    candidates: dict[str, dict[str, Any]] = {}
    for key in ("trade_items", "items", "candidates"):
        for item in payload.get(key) or []:
            if isinstance(item, dict):
                candidate_code = normalize_code(item.get("code") or "")
                if candidate_code:
                    candidates[candidate_code] = item

    generated_at = str(payload.get("generated_at") or now_ts())
    context_date = generated_at[:10] if len(generated_at) >= 10 else today_key()
    updated = 0
    for code, pos in (state.get("positions") or {}).items():
        if not isinstance(pos, dict):
            continue
        candidate = candidates.get(normalize_code(code), {})
        stock = stocks.get(normalize_code(code)) if isinstance(stocks.get(normalize_code(code)), dict) else {}
        industry = str(
            candidate.get("industry")
            or candidate.get("sector")
            or pos.get("industry")
            or pos.get("sector")
            or stock.get("industry")
            or ""
        ).strip()
        if industry:
            pos["industry"] = industry
            pos["sector"] = industry
        if not is_sector_tide_strategy(position_entry_strategy(pos)):
            continue
        sector = sectors.get(industry) if isinstance(sectors.get(industry), dict) else {}
        if not industry or not sector:
            continue

        score = _safe_float(sector.get("score"), -1.0)
        if score >= 0:
            if score < 55:
                if pos.get("sector_weak_last_date") != context_date:
                    pos["sector_weak_count"] = int(pos.get("sector_weak_count") or 0) + 1
                    pos["sector_weak_last_date"] = context_date
            else:
                pos["sector_weak_count"] = 0
                pos.pop("sector_weak_last_date", None)
        pos["sector_score"] = sector.get("score")
        pos["sector_status"] = sector.get("status")
        pos["sector_rank_acceleration"] = sector.get("rank_acceleration")
        pos["sector_breadth20"] = sector.get("breadth20")
        pos["market_regime"] = market.get("state")
        current_budget = sector_tide_risk_budget(str(market.get("state") or ""))
        pos["risk_budget_regime"] = market.get("state")
        pos["per_trade_risk_budget_pct"] = current_budget["per_trade_risk_pct"]
        pos["max_open_risk_pct"] = current_budget["max_open_risk_pct"]
        pos["max_sector_risk_pct"] = current_budget["max_sector_risk_pct"]
        pos["max_total_position_pct"] = current_budget["max_total_position_pct"]
        pos["max_sector_position_pct"] = current_budget["max_sector_position_pct"]
        pos["market_tide_score"] = market.get("score")
        pos["market_hard_stop"] = bool(market.get("hard_stop"))
        pos["market_allows_buys"] = bool(market.get("allow_new_buys"))
        pos["stock_sector_rank"] = candidate.get("stock_sector_rank", stock.get("sector_relative_rank"))
        pos["sector_context_at"] = generated_at
        updated += 1
    return updated


def load_latest_sector_tide_payload() -> dict[str, Any]:
    try:
        payload = json.loads(MULTI_STRATEGY_CACHE_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def position_entry_strategy(pos: dict[str, Any]) -> str:
    mark = pos.get("strategy_mark") if isinstance(pos.get("strategy_mark"), dict) else {}
    return str(
        pos.get("buy_strategy")
        or pos.get("strategy_mark_id")
        or mark.get("strategy_id")
        or mark.get("entry_strategy_id")
        or ""
    )


def zettaranc_entry_stop(rows: list[dict[str, Any]], entry_idx: int, strategy_id: str) -> dict[str, Any] | None:
    """Resolve the canonical stop anchor for one Zettaranc entry strategy."""
    if entry_idx < 0 or entry_idx >= len(rows):
        return None
    if strategy_id == "shaofu_b1":
        stop = find_n_structure_prior_low(rows, entry_idx)
        return {**stop, "source": "n_structure_low"} if stop else None
    if strategy_id == "b2_confirm":
        start = max(0, entry_idx - 3)
        candidates = [(idx, _row_float(rows[idx], "low")) for idx in range(start, entry_idx)]
        candidates = [(idx, low) for idx, low in candidates if low > 0]
        if not candidates:
            return None
        idx, price = min(candidates, key=lambda item: item[1])
        return {"price": round(price, 3), "date": str(rows[idx].get("date") or ""), "source": "b1_low"}
    if strategy_id == "b3_accelerate":
        entry_low = _row_float(rows[entry_idx], "low")
        if entry_low > 0:
            return {"price": round(entry_low, 3), "date": str(rows[entry_idx].get("date") or ""), "source": "b3_kline_low"}
        for idx in range(entry_idx - 1, max(-1, entry_idx - 4), -1):
            row = rows[idx]
            if _row_float(row, "close") > _row_float(row, "open"):
                midpoint = (_row_float(row, "open") + _row_float(row, "close")) / 2
                if midpoint > 0:
                    return {"price": round(midpoint, 3), "date": str(row.get("date") or ""), "source": "b2_midpoint"}
        return None
    if strategy_id == "super_b1":
        start = max(0, entry_idx - 6)
        bearish = [
            (idx, _row_float(rows[idx], "volume"), _row_float(rows[idx], "low"))
            for idx in range(start, entry_idx)
            if _row_float(rows[idx], "close") < _row_float(rows[idx], "open") and _row_float(rows[idx], "low") > 0
        ]
        if not bearish:
            return None
        idx, _, price = max(bearish, key=lambda item: item[1])
        return {"price": round(price, 3), "date": str(rows[idx].get("date") or ""), "source": "super_b1_washout_low"}
    return None


def zettaranc_confirmed_rows(rows: list[dict[str, Any]], as_of: datetime) -> list[dict[str, Any]]:
    """Exclude an unfinished current-day bar before the A-share close."""
    if as_of.time() >= dtime(15, 0):
        return rows
    today_compact = as_of.strftime("%Y%m%d")
    return [r for r in rows if str(r.get("date") or "").replace("-", "") != today_compact]


def _sell_signal(reason: str, signal: str, sell_ratio: float = 1.0) -> dict[str, Any]:
    return _sell_signals._sell_signal(reason, signal, sell_ratio)


def evaluate_sell_signal(
    code: str,
    pos: dict[str, Any],
    today: str | None = None,
    *,
    time_exit_allowed: bool = True,
    b3_exit_allowed: bool | None = None,
    time_stop_allowed: bool | None = None,
) -> dict[str, Any] | None:
    """Evaluate the local sell rule stack for one open position.

    The rule stack combines fixed risk control, S1/B1-style failed confirmation,
    volatility/trailing exits, and time-based exits. It mutates lightweight per-position
    tracking fields such as peak price and consecutive BBI-break days.
    """
    today = today or today_key()
    entry_strategy = position_entry_strategy(pos)
    zettaranc_position = is_zettaranc_strategy(entry_strategy)
    sector_tide_position = is_sector_tide_strategy(entry_strategy)
    realtime_price = float(pos.get("last_price") or pos.get("close") or pos.get("avg_cost") or 0)
    price = float(
        (pos.get("confirmed_close") if zettaranc_position else pos.get("close"))
        or pos.get("close")
        or realtime_price
        or pos.get("avg_cost")
        or 0
    )
    avg_cost = float(pos.get("avg_cost") or 0)
    if price <= 0 or avg_cost <= 0:
        return None
    if time_stop_allowed is not None:
        time_exit_allowed = time_stop_allowed
    if b3_exit_allowed is None:
        b3_exit_allowed = time_exit_allowed

    pnl_pct = (price / avg_cost - 1) * 100
    realtime_pnl_pct = (realtime_price / avg_cost - 1) * 100
    prior_high = float(pos.get("highest_price") or price)
    highest_price = max(prior_high, price)
    pos["highest_price"] = round(highest_price, 3)
    prior_max = pos.get("max_pnl_pct")
    try:
        max_pnl_pct = max(float(prior_max), pnl_pct) if prior_max is not None else pnl_pct
    except Exception:
        max_pnl_pct = pnl_pct
    pos["max_pnl_pct"] = round(max_pnl_pct, 2)

    bbi = float(pos.get("bbi") or 0)
    bbi_dist = ((price / bbi - 1) * 100) if bbi > 0 else None
    if bbi_dist is not None:
        pos["bbi_distance_pct"] = round(bbi_dist, 2)
        if bbi_dist >= 0.3:
            pos["s1_reclaim_seen"] = True
        if bbi_dist <= S1_FAIL_BBI_PCT:
            if pos.get("bbi_break_last_date") != today:
                pos["bbi_break_days"] = int(pos.get("bbi_break_days") or 0) + 1
                pos["bbi_break_last_date"] = today
        else:
            pos["bbi_break_days"] = 0
            pos.pop("bbi_break_last_date", None)
    else:
        pos["bbi_break_days"] = 0

    if pnl_pct >= TRAILING_STOP_ACTIVATE_PCT:
        pos["trailing_stop_activated"] = True

    hold_days = holding_days(pos, today)
    j_now = pos.get("kdj_j")
    j_prev = pos.get("kdj_j_prev")
    j_turning_down = (
        isinstance(j_now, (int, float))
        and isinstance(j_prev, (int, float))
        and float(j_now) < float(j_prev) - 3
    )

    # Legacy positions may still carry the removed fixed-percentage fallback.
    # Ignore it while preserving genuine entry-candle/previous-low stops.
    shaofu_stop = 0.0 if pos.get("shaofu_stop_source") == "fallback_pct" else float(
        pos.get("shaofu_stop_price") or pos.get("entry_stop_price") or 0
    )
    if shaofu_stop > 0 and price < shaofu_stop:
        stop_labels = {
            "n_structure_low": "N型结构前低",
            "b1_low": "前置B1低点",
            "b3_kline_low": "B3当日低点",
            "b2_midpoint": "B2大阳线中位",
            "super_b1_washout_low": "超级B1洗盘阴线低点",
            "tide_structure_low": "板块潮汐结构低点",
        }
        stop_source = str(pos.get("shaofu_stop_source") or pos.get("entry_stop_source") or "")
        stop_label = stop_labels.get(stop_source, "入场止损")
        stop_signal = "tide_structure_stop" if sector_tide_position else "shaofu_entry_stop"
        return _sell_signal(f"收盘价破{stop_label} (收盘{price:.2f} < 止损{shaofu_stop:.2f})", stop_signal)

    if sector_tide_position:
        sector_score = _safe_float(pos.get("sector_score"), 100.0)
        sector_status = str(pos.get("sector_status") or "")
        if pos.get("market_hard_stop") and (sector_score < 55 or sector_status in {"weakening", "lagging"}):
            return _sell_signal(
                f"市场复合风险硬停止且行业转弱 ({pos.get('industry') or '-'}分数{sector_score:.1f}，潮位{sector_status or '-'})",
                "tide_market_hard_stop",
            )
        if int(pos.get("sector_weak_count") or 0) >= 2:
            return _sell_signal(
                f"行业退潮连续两日 ({pos.get('industry') or '-'}分数{sector_score:.1f}<55)",
                "tide_sector_weak",
            )

        strategy_time_exit = evaluate_strategy_time_exit(
            entry_strategy=entry_strategy,
            hold_days=hold_days,
            max_pnl_pct=max_pnl_pct,
            pnl_pct=pnl_pct,
            time_exit_allowed=time_exit_allowed,
            b3_exit_allowed=False,
            b3_exit_hhmm=B3_EXIT_HHMM,
            time_exit_hhmm=TIME_EXIT_HHMM,
            no_progress_hold_days=NO_PROGRESS_HOLD_DAYS,
            no_progress_max_pnl_pct=NO_PROGRESS_MAX_PNL_PCT,
        )
        if strategy_time_exit:
            return strategy_time_exit

        entry_stop = _safe_float(pos.get("entry_stop_price"), 0.0)
        initial_risk = avg_cost - entry_stop if 0 < entry_stop < avg_cost else 0.0
        two_r_price = avg_cost + 2 * initial_risk if initial_risk > 0 else 0.0
        if two_r_price > 0:
            pos["two_r_price"] = round(two_r_price, 3)
            if price >= two_r_price and not pos.get("partial_tp_done"):
                return _sell_signal(
                    f"板块潮汐达到2R先减半 (现价{price:.2f} ≥ 2R目标{two_r_price:.2f})",
                    "tide_2r_partial",
                    TAKE_PROFIT_PARTIAL_RATIO,
                )

        atr20 = _safe_float(pos.get("atr20") or pos.get("entry_atr20"), 0.0)
        if atr20 > 0 and (pos.get("partial_tp_done") or (two_r_price > 0 and highest_price >= two_r_price)):
            tide_trailing_stop = highest_price - 2.0 * atr20
            pos["tide_trailing_stop"] = round(tide_trailing_stop, 3)
            if tide_trailing_stop > avg_cost and price <= tide_trailing_stop:
                return _sell_signal(
                    f"板块潮汐2ATR跟踪退出 (现价{price:.2f} ≤ 跟踪线{tide_trailing_stop:.2f})",
                    "tide_atr_trail",
                )
        if hold_days >= MAX_HOLD_DAYS:
            return _sell_signal(f"持仓到期 ({hold_days}d ≥ {MAX_HOLD_DAYS}d)", "max_hold_days")
        return None

    chuhuo = pos.get("chuhuo_wushi") or {}
    if chuhuo.get("is_selling"):
        patterns = chuhuo.get("patterns") or []
        top = patterns[0].get("type") if patterns and isinstance(patterns[0], dict) else "出货五式"
        return _sell_signal(f"出货五式触发 ({top}，评分{chuhuo.get('total_score')})", "chuhuo_wushi")

    s123_signal = str(pos.get("s123_signal") or "")
    if s123_signal:
        return _sell_signal(str(pos.get("s123_reason") or "S1/S2/S3逃顶信号触发"), s123_signal)

    if pos.get("z_dead_cross"):
        return _sell_signal("白线死叉黄线 (牛绳断，按Z哥双线纪律清仓)", "z_dead_cross")
    if int(pos.get("z_white_break_days") or 0) >= S1_FAIL_CONFIRM_DAYS:
        return _sell_signal(f"白线两日破位 (连续{pos.get('z_white_break_days')}日收盘低于白线)", "z_white_break")

    if bbi_dist is not None:
        if pos.get("s1_reclaim_seen") and bbi_dist <= S1_FAIL_BBI_PCT and max_pnl_pct >= 0:
            return _sell_signal(
                f"S1反抽失败 (重新站上BBI后又跌至{bbi_dist:.1f}%，退出等待新买点)",
                "s1_reclaim_failed",
            )
        if int(pos.get("bbi_break_days") or 0) >= S1_FAIL_CONFIRM_DAYS and (pnl_pct < TAKE_PROFIT_PARTIAL_PCT or j_turning_down):
            return _sell_signal(
                f"S1趋势确认失效 (连续{pos.get('bbi_break_days')}日低于BBI，距BBI {bbi_dist:.1f}%)",
                "s1_bbi_failed",
            )
        if bbi_dist <= BBI_BREAKDOWN_PCT:
            return _sell_signal(
                f"BBI跌破触发 (距BBI {bbi_dist:.1f}% ≤ {BBI_BREAKDOWN_PCT}%)",
                "bbi_breakdown",
            )

    if max_pnl_pct > 0.8 and pnl_pct <= 0:
        return _sell_signal(f"盈转亏退出 (最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)", "profit_to_loss")
    strategy_time_exit = evaluate_strategy_time_exit(
        entry_strategy=entry_strategy,
        hold_days=hold_days,
        max_pnl_pct=max_pnl_pct,
        pnl_pct=realtime_pnl_pct if entry_strategy == "b3_accelerate" else pnl_pct,
        time_exit_allowed=time_exit_allowed,
        b3_exit_allowed=bool(b3_exit_allowed),
        b3_exit_hhmm=B3_EXIT_HHMM,
        time_exit_hhmm=TIME_EXIT_HHMM,
        no_progress_hold_days=NO_PROGRESS_HOLD_DAYS,
        no_progress_max_pnl_pct=NO_PROGRESS_MAX_PNL_PCT,
    )
    if strategy_time_exit:
        return strategy_time_exit
    if time_exit_allowed:
        if hold_days >= NO_PROGRESS_HOLD_DAYS and max_pnl_pct < NO_PROGRESS_MAX_PNL_PCT and pnl_pct <= 0:
            return _sell_signal(f"买入后{hold_days}日未兑现离场 ({TIME_EXIT_HHMM}尾盘检查，最高盈利{max_pnl_pct:.1f}%，先收队)", "no_progress")

    sell_score = pos.get("sell_score")
    if isinstance(sell_score, (int, float)):
        if sell_score <= SELL_SCORE_EXIT_THRESHOLD:
            return _sell_signal(
                f"防卖飞评分过低 ({sell_score}/5，{pos.get('sell_score_reason','')})",
                "sell_score_exit",
            )
        if sell_score <= SELL_SCORE_REDUCE_THRESHOLD and not pos.get("sell_score_half_done") and not pos.get("partial_tp_done"):
            return _sell_signal(
                f"防卖飞评分中性 ({sell_score}/5，先减半观察BBI两日破位)",
                "sell_score_reduce",
                TAKE_PROFIT_PARTIAL_RATIO,
            )

    low10 = float(pos.get("low10") or 0)
    if low10 > 0 and hold_days >= 3 and price <= low10 * 0.995:
        return _sell_signal(
            f"{DONCHIAN_EXIT_LOOKBACK_DAYS}日低点跌破 (现价{price:.2f} < 低点{low10:.2f})",
            "donchian_low_break",
        )

    if max_pnl_pct >= TRAILING_STOP_ACTIVATE_PCT:
        giveback = max_pnl_pct - pnl_pct
        trailing_gap = max(
            TRAILING_MIN_GIVEBACK_PCT,
            min(TRAILING_MAX_GIVEBACK_PCT, max_pnl_pct * TRAILING_GIVEBACK_RATIO),
        )
        pos["trailing_gap_pct"] = round(trailing_gap, 2)
        if giveback >= trailing_gap:
            return _sell_signal(
                f"峰值回撤止盈 (最高盈利{max_pnl_pct:.1f}%，回撤{giveback:.1f}% ≥ {trailing_gap:.1f}%)",
                "profit_giveback",
            )
        atr20 = float(pos.get("atr20") or 0)
        if atr20 > 0:
            chandelier_stop = highest_price - ATR_CHANDELIER_MULT * atr20
            pos["chandelier_stop"] = round(chandelier_stop, 3)
            if chandelier_stop > avg_cost * 0.99 and price <= chandelier_stop:
                return _sell_signal(
                    f"ATR吊灯止盈 (现价{price:.2f} ≤ {ATR_CHANDELIER_MULT:.0f}ATR止损{chandelier_stop:.2f})",
                    "atr_chandelier",
                )
        if pos.get("trailing_stop_activated") and pnl_pct < 1.0:
            return _sell_signal(f"移动止损保本 (曾盈利>5%，回落至{pnl_pct:.1f}%)", "breakeven_trail")

    if pos.get("luzhu_half_signal") and not pos.get("partial_tp_done"):
        return _sell_signal(
            "卤煮止盈 (站上BBI后连续中/大阳，按Z哥纪律放飞半仓)",
            "luzhu_half",
            TAKE_PROFIT_PARTIAL_RATIO,
        )

    if not zettaranc_position and pnl_pct >= TAKE_PROFIT_PARTIAL_PCT and pnl_pct < TAKE_PROFIT_PCT and not pos.get("partial_tp_done"):
        return _sell_signal(
            f"第一批止盈 (盈亏{pnl_pct:.1f}% ≥ {TAKE_PROFIT_PARTIAL_PCT}%，卖一半)",
            "partial_take_profit",
            TAKE_PROFIT_PARTIAL_RATIO,
        )

    if not zettaranc_position and pnl_pct >= TAKE_PROFIT_PCT:
        return _sell_signal(f"止盈清仓 (盈亏{pnl_pct:.1f}% ≥ {TAKE_PROFIT_PCT}%)", "take_profit")

    if hold_days >= MAX_HOLD_DAYS:
        return _sell_signal(f"持仓到期 ({hold_days}d ≥ {MAX_HOLD_DAYS}d)", "max_hold_days")
    if hold_days > 12 and pnl_pct < -3.0:
        return _sell_signal(f"信号未兑现 ({hold_days}d 仍亏{pnl_pct:.1f}%，离场等新信号)", "stale_loser")
    if hold_days >= 10 and pnl_pct < 1.0 and bbi_dist is not None and bbi_dist < 0:
        return _sell_signal(f"低效持仓退出 ({hold_days}d 盈亏{pnl_pct:.1f}%，且未站回BBI)", "stale_below_bbi")

    return None


def _refresh_position_bbi(state: dict[str, Any], dt: datetime | None = None) -> None:
    """Fetch daily K-lines for open positions and cache sell-rule indicators."""
    positions = state.get("positions") or {}
    if not positions:
        return
    import statistics as _st
    for code, pos in positions.items():
        try:
            script = STOCK_TOOLS_SCRIPT
            proc = subprocess.run(
                [sys.executable, str(script), "kline", code, "130"],
                capture_output=True, text=True, timeout=20,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                continue
            data = json.loads(proc.stdout)
            raw_rows = [r for r in (data.get("rows") or []) if isinstance(r, dict) and r.get("close")]
            entry_strategy = position_entry_strategy(pos)
            zettaranc_position = is_zettaranc_strategy(entry_strategy)
            sector_tide_position = is_sector_tide_strategy(entry_strategy)
            rows = raw_rows
            as_of = dt or datetime.now()
            if zettaranc_position:
                rows = zettaranc_confirmed_rows(raw_rows, as_of)
            closes = [float(r.get("close")) for r in rows] if rows else (data.get("closes") or [])
            if len(closes) < 24:
                continue
            # Compute BBI from closes
            def _ma(vals, n):
                return [None] * (n - 1) + [_st.mean(vals[i - n + 1:i + 1]) for i in range(n - 1, len(vals))]
            ma3, ma6, ma12, ma24 = _ma(closes, 3), _ma(closes, 6), _ma(closes, 12), _ma(closes, 24)
            bbi_val = None
            for i in range(len(closes) - 1, -1, -1):
                if all(m[i] is not None for m in [ma3, ma6, ma12, ma24]):
                    bbi_val = (ma3[i] + ma6[i] + ma12[i] + ma24[i]) / 4
                    break
            if bbi_val:
                pos["bbi"] = round(bbi_val, 2)
                pos["close"] = closes[-1]
            if rows:
                if rows:
                    pos["close"] = float(rows[-1].get("close") or pos.get("close") or 0)
                    if zettaranc_position:
                        pos["confirmed_close"] = pos["close"]
                    pos["last_kline_date"] = rows[-1].get("date") or ""
                    desired_stop_sources = {
                        "shaofu_b1": {"n_structure_low"},
                        "b2_confirm": {"b1_low"},
                        "b3_accelerate": {"b3_kline_low", "b2_midpoint"},
                        "super_b1": {"super_b1_washout_low"},
                    }
                    current_source = str(pos.get("shaofu_stop_source") or "")
                    should_refresh_z_stop = zettaranc_position and current_source not in desired_stop_sources.get(entry_strategy, set())
                    should_refresh_legacy_stop = not zettaranc_position and not sector_tide_position and (
                        not pos.get("shaofu_stop_price") or current_source in {"fallback_pct", "entry_kline_low"}
                    )
                    if should_refresh_z_stop or should_refresh_legacy_stop:
                        lots = pos.get("buy_date_lots") or {}
                        open_dates = sorted(date for date, qty in lots.items() if int(qty or 0) > 0)
                        entry_date = open_dates[0] if open_dates else ""
                        entry_idx = next((idx for idx, row in enumerate(rows) if str(row.get("date") or "") == entry_date), None)
                        if entry_idx is not None:
                            structure_low = (
                                zettaranc_entry_stop(rows, entry_idx, entry_strategy)
                                if zettaranc_position
                                else find_n_structure_prior_low(rows, entry_idx)
                            )
                            pos.pop("entry_kline_low", None)
                            if structure_low:
                                if structure_low.get("source") == "n_structure_low" or not zettaranc_position:
                                    pos["n_structure_low"] = structure_low["price"]
                                    pos["n_structure_low_date"] = structure_low["date"]
                                    pos["n_structure_previous_low"] = structure_low.get("previous_price")
                                    pos["n_structure_previous_low_date"] = structure_low.get("previous_date")
                                pos["shaofu_stop_price"] = structure_low["price"]
                                pos["shaofu_stop_source"] = str(structure_low.get("source") or "n_structure_low")
                                pos["shaofu_stop_date"] = structure_low.get("date") or ""
                            else:
                                pos.pop("n_structure_low", None)
                                pos.pop("n_structure_low_date", None)
                                pos.pop("n_structure_previous_low", None)
                                pos.pop("n_structure_previous_low_date", None)
                                pos.pop("shaofu_stop_price", None)
                                pos.pop("shaofu_stop_source", None)
                                pos.pop("shaofu_stop_date", None)
                        else:
                            pos.pop("shaofu_stop_price", None)
                            pos.pop("shaofu_stop_source", None)
                if len(rows) >= DONCHIAN_EXIT_LOOKBACK_DAYS + 1:
                    prev_rows = rows[-(DONCHIAN_EXIT_LOOKBACK_DAYS + 1):-1]
                    lows = [float(r.get("low") or 0) for r in prev_rows if float(r.get("low") or 0) > 0]
                    if lows:
                        pos["low10"] = round(min(lows), 3)
                if len(rows) >= 20:
                    highs = [float(r.get("high") or 0) for r in rows[-20:] if float(r.get("high") or 0) > 0]
                    if highs:
                        pos["high20"] = round(max(highs), 3)
                atr = _compute_atr(rows)
                if atr:
                    pos["atr20"] = round(atr, 3)
                kdj = _compute_kdj_snapshot(rows)
                for key, dest in [
                    ("k", "kdj_k"), ("d", "kdj_d"), ("j", "kdj_j"),
                    ("k_prev", "kdj_k_prev"), ("d_prev", "kdj_d_prev"), ("j_prev", "kdj_j_prev"),
                    ("min_j_10d", "kdj_min_j_10d"),
                ]:
                    val = kdj.get(key)
                    if val is not None:
                        pos[dest] = round(float(val), 2)

                z_lines = _compute_z_lines(rows)
                for key, dest in [
                    ("white", "z_white"), ("white_prev", "z_white_prev"),
                    ("yellow", "z_yellow"), ("yellow_prev", "z_yellow_prev"),
                ]:
                    val = z_lines.get(key)
                    if val is not None:
                        pos[dest] = round(float(val), 3)
                pos["z_dead_cross"] = bool(z_lines.get("dead_cross"))
                z_white = z_lines.get("white")
                if z_white and _row_float(rows[-1], "close") < float(z_white) * (1 + S1_FAIL_BBI_PCT / 100):
                    if pos.get("z_white_break_last_date") != pos.get("last_kline_date"):
                        pos["z_white_break_days"] = int(pos.get("z_white_break_days") or 0) + 1
                        pos["z_white_break_last_date"] = pos.get("last_kline_date")
                else:
                    pos["z_white_break_days"] = 0
                    pos.pop("z_white_break_last_date", None)

                score = _compute_sell_score(rows, float(pos.get("bbi") or 0) or None)
                pos["sell_score"] = score.get("score")
                pos["sell_score_reason"] = score.get("reason")
                pos["sell_score_items"] = score.get("items")

                chuhuo = _detect_chuhuo_wushi(rows)
                pos["chuhuo_wushi"] = chuhuo
                s123 = _detect_s1_s2_s3(rows)
                if s123.get("signal"):
                    pos["s123_signal"] = s123.get("signal")
                    pos["s123_reason"] = s123.get("reason")
                else:
                    pos.pop("s123_signal", None)
                    pos.pop("s123_reason", None)
                luzhu = _detect_luzhu_half(rows, float(pos.get("bbi") or 0) or None)
                pos["luzhu_half_signal"] = bool(luzhu)
                if luzhu:
                    pos["luzhu_half_detail"] = luzhu
        except Exception:
            continue

def check_auto_exits(state: dict[str, Any], dt: datetime | None = None) -> list[dict[str, Any]]:
    """检查所有持仓是否触发自动止盈/止损/技术退出条件。
    
    退出优先级由 evaluate_sell_signal 统一维护：
    硬止损、S1/BBI失效、10日低点、峰值回撤/ATR吊灯、
    分批止盈、目标止盈、持仓时间离场。
    """
    trade_allowed, _ = is_a_share_execution_time(dt)
    if not trade_allowed:
        return []

    positions = state.get("positions") or {}
    if not positions:
        return []
    
    today = (dt or datetime.now()).strftime("%Y-%m-%d")
    time_exit_allowed = is_time_exit_check_time(dt)
    b3_exit_allowed = is_b3_exit_check_time(dt)
    executed = []
    cash = float(state.get("cash") or 0)
    
    for code in list(positions.keys()):
        pos = positions[code]
        sellable = available_to_sell(pos, today)
        if sellable <= 0:
            continue
        
        price = pos.get("last_price") or pos.get("avg_cost") or 0
        if price <= 0:
            continue
        
        avg_cost = float(pos.get("avg_cost") or 0)
        if avg_cost <= 0:
            continue
        
        exit_signal = evaluate_sell_signal(code, pos, today, time_exit_allowed=time_exit_allowed, b3_exit_allowed=b3_exit_allowed)
        if not exit_signal:
            continue
        exit_reason = str(exit_signal.get("reason") or "")
        entry_strategy = str(
            pos.get("buy_strategy")
            or latest_buy_strategy_for_code(state, code)
            or classify_buy_strategy(str(pos.get("entry_reason") or ""))
        )
        exit_rule = classify_exit_rule(exit_reason, str(exit_signal.get("signal") or ""))
        sell_ratio = float(exit_signal.get("sell_ratio") or 1.0)
        
        # 执行卖出
        qty = min(sellable, position_qty(pos))
        if sell_ratio < 1.0:
            qty = max(100, int(qty * sell_ratio) // 100 * 100)
            if exit_signal.get("signal") == "sell_score_reduce":
                pos["sell_score_half_done"] = True
            if exit_signal.get("signal") == "luzhu_half":
                pos["luzhu_half_done"] = True
            pos["partial_tp_done"] = True
        qty = qty // 100 * 100
        if qty <= 0:
            continue
        total_equity = portfolio_total_equity_for_limits(cash, positions)
        current_position_value = position_market_value(pos, float(price))
        current_market_value = portfolio_market_value(positions)
        current_market_value = max(0.0, current_market_value - position_market_value(pos) + current_position_value)
        gross = qty * price
        order_position_pct = position_pct_of_equity(gross, total_equity)
        position_before_trade_pct = position_pct_of_equity(current_position_value, total_equity)
        position_after_trade_value = max(0.0, current_position_value - gross)
        position_after_trade_pct = position_pct_of_equity(position_after_trade_value, total_equity)
        total_position_after_trade_pct = position_pct_of_equity(max(0.0, current_market_value - gross), total_equity)
        entry_mark = compact_position_strategy_mark(pos, entry_strategy)
        exit_mark = apply_exit_strategy_mark(pos, entry_strategy, exit_rule, exit_reason, source="AUTO_EXIT")
        
        fees = calc_trade_fees(gross, "SELL")
        net_proceeds = gross - fees["total_fee"]
        cost_basis = qty * avg_cost
        realized_pnl = net_proceeds - cost_basis
        realized_pnl_pct = (realized_pnl / cost_basis * 100) if cost_basis > 0 else 0.0
        
        pos["qty"] = position_qty(pos) - qty
        pos.pop("shares", None)
        
        # FIFO式消耗买入批次
        remaining = qty
        lots = pos.get("buy_date_lots") or {}
        for date in sorted(list(lots.keys())):
            if date == today or remaining <= 0:
                continue
            use = min(int(lots.get(date) or 0), remaining)
            lots[date] = int(lots.get(date) or 0) - use
            remaining -= use
            if lots[date] <= 0:
                lots.pop(date, None)
        
        if pos["qty"] <= 0:
            positions.pop(code, None)
        cash += net_proceeds
        
        executed.append({
            "time": now_ts(),
            "action": "SELL",
            "code": code,
            "name": pos.get("name") or "",
            "shares": qty,
            "price": round(price, 3),
            "amount": round(gross, 2),
            "commission": fees["commission"],
            "transfer_fee": fees["transfer_fee"],
            "stamp_duty": fees["stamp_duty"],
            "fee": fees["total_fee"],
            "net_proceeds": round(net_proceeds, 2),
            "pnl": round(realized_pnl, 2),
            "pnl_pct": round(realized_pnl_pct, 2),
            "order_position_pct": order_position_pct,
            "position_before_trade_pct": position_before_trade_pct,
            "position_after_trade_pct": position_after_trade_pct,
            "total_position_after_trade_pct": total_position_after_trade_pct,
            "exit_signal": exit_signal.get("signal") or "",
            "buy_strategy": entry_strategy,
            "exit_rule": exit_rule,
            "strategy_mark": entry_mark,
            "exit_strategy_mark": exit_mark,
            "reason": exit_reason,
        })
    
    if executed:
        state["cash"] = round(cash, 2)
        state.setdefault("trade_log", []).extend(executed)
        del state["trade_log"][:-TRADE_LOG_LIMIT]
        # 同步写入 DB
        for e in executed:
            try:
                from niuniu_db import record_trade as _rt
                _rt(e)
            except Exception: pass
        try:
            from niuniu_db import snapshot_positions as _sp
            _sp(state.get("positions", {}))
        except Exception: pass
        # 记录系统自动退出决策
        log_entry = {
            "time": now_ts(),
            "b1_generated_at": "",
            "trade_allowed": True,
            "trade_reason": "系统自动离场检查",
            "decision": {
                "summary": f"自动止盈止损：{len(executed)}笔卖出",
                "actions": [{"action": "SELL", "code": e["code"], "shares": e["shares"], "reason": e["reason"]} for e in executed],
                "model": "SYSTEM_AUTO_EXIT",
                "provider": "local_rule",
            },
            "executed": executed,
        }
        state.setdefault("decision_log", []).append(log_entry)
        _sync_decision_to_db(log_entry)
    
    return executed


def run_auto_exits_once(dt: datetime | None = None) -> dict[str, Any]:
    """Run the side-effectful automatic exit script once for scheduled checks."""
    dt = dt or datetime.now()
    state = load_state()
    sync_sector_tide_position_context(state, load_latest_sector_tide_payload())
    refresh_realtime_prices(state)
    refresh_position_intraday(state)
    _refresh_position_bbi(state, dt)
    executed = check_auto_exits(state, dt)
    record_equity(state)
    save_state(state)
    if executed:
        _notify_trade_executions_safely(executed)
    return {
        "ok": True,
        "checked_at": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "b3_exit_time": B3_EXIT_HHMM,
        "time_exit_time": TIME_EXIT_HHMM,
        "executed": executed,
        "executed_count": len(executed),
        "portfolio": enrich_portfolio(state),
    }


def maybe_record_session_equity_heartbeat(min_interval_seconds: int = EQUITY_HEARTBEAT_MIN_SECONDS) -> bool:
    """Record account equity during the full 09:30-15:00 dashboard session, independent of trades/B1 candidates."""
    now = datetime.now()
    if not is_a_share_session_clock(now):
        return False
    state = load_state()
    pruned = prune_future_intraday_equity_points(state, now=now)
    history = state.setdefault("equity_history", [])
    last_dt = None
    for item in reversed(history):
        last_dt = parse_ts(item.get("time", ""))
        if last_dt:
            break
    if last_dt and (now - last_dt).total_seconds() < min_interval_seconds:
        if pruned:
            save_state(state)
        return False
    # Keep current holdings marked-to-market before taking a snapshot.
    try:
        refresh_position_quotes(state)
    except Exception as exc:
        state["last_quote_refresh"] = {"time": now_ts(), "updated": 0, "error": f"{type(exc).__name__}: {exc}"}
    record_equity(state)
    save_state(state)
    return True


def load_crossdesk_config(base_url_env: str = "", api_key_env: str = "") -> tuple[str, str]:
    env_base_url = os.environ.get(base_url_env) if base_url_env else ""
    env_api_key = os.environ.get(api_key_env) if api_key_env else ""
    env_base_url = env_base_url or os.environ.get("CROSSDESK_BASE_URL")
    env_api_key = env_api_key or os.environ.get("CROSSDESK_API_KEY")
    if env_base_url and env_api_key:
        return env_base_url.rstrip("/"), env_api_key
    if yaml is None:
        raise RuntimeError("PyYAML is required")
    cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    providers = cfg.get("custom_providers") or []
    for provider in providers:
        if isinstance(provider, dict) and str(provider.get("name") or "").lower() == CROSSDESK_PROVIDER_NAME.lower():
            base_url = (provider.get("base_url") or "").rstrip("/")
            api_key = provider.get("api_key") or ""
            if base_url and api_key:
                return base_url, api_key
    raise RuntimeError(f"Missing custom provider {CROSSDESK_PROVIDER_NAME}")


def extract_json(text: str) -> Any:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(text)
        return obj
    except Exception:
        m = re.search(r"[\[{]", text)
        if not m:
            raise ValueError(
                f"模型回复无JSON起始符号。max_tokens可能需要上调。前150字符: {clip_text(text, 150)}"
            )
        try:
            obj, _ = decoder.raw_decode(text[m.start():])
            return obj
        except Exception as e:
            raise ValueError(
                f"模型回复JSON解析失败：{e}。max_tokens可能不足或回复被截断。前150字符: {clip_text(text, 150)}"
            )


def clip_text(text: str, limit: int = 600) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def format_http_error(exc: urllib.error.HTTPError, model_name: str) -> RuntimeError:
    try:
        body = exc.read().decode("utf-8", "ignore")
    except Exception:
        body = ""
    detail = ""
    if body.strip():
        try:
            obj = json.loads(body)
            err = obj.get("error") if isinstance(obj, dict) else None
            if isinstance(err, dict):
                detail = err.get("message") or json.dumps(err, ensure_ascii=False)
            else:
                detail = json.dumps(obj, ensure_ascii=False)
        except Exception:
            detail = body
    message = f"model={model_name} HTTP {exc.code}: {detail or exc.reason or 'Service Unavailable'}"
    return RuntimeError(clip_text(message, 900))


def parse_chat_completion_content(raw: str) -> tuple[str, str]:
    """Return visible assistant content plus compact response metadata."""
    if not (raw or "").strip():
        raise ValueError("空响应")

    if raw.lstrip().startswith("data:"):
        parts = []
        finish_reasons = []
        usage = None
        chunks = 0
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                obj = json.loads(chunk)
            except Exception:
                continue
            chunks += 1
            if obj.get("usage"):
                usage = obj.get("usage")
            choice = (obj.get("choices") or [{}])[0]
            if choice.get("finish_reason"):
                finish_reasons.append(str(choice.get("finish_reason")))
            delta = choice.get("delta") or {}
            message = choice.get("message") or {}
            parts.append(delta.get("content") or message.get("content") or "")
        detail_bits = [f"sse_chunks={chunks}"]
        if finish_reasons:
            detail_bits.append(f"finish_reason={finish_reasons[-1]}")
        if usage:
            detail_bits.append(f"usage={usage}")
        return "".join(parts), ", ".join(detail_bits)

    try:
        data = json.loads(raw)
    except Exception:
        raise ValueError(f"模型返回了非JSON内容，请检查max_tokens/超时是否够用。前150字符: {clip_text(raw, 150)}")
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content") or ""
    detail_bits = []
    if choice.get("finish_reason"):
        detail_bits.append(f"finish_reason={choice.get('finish_reason')}")
    if data.get("usage"):
        detail_bits.append(f"usage={data.get('usage')}")
    return content, ", ".join(detail_bits)


def request_chat_content(base_url: str, api_key: str, payload: dict, model_name: str,
                         max_retries: int = 3, timeout: int = 60) -> str:
    """Call chat/completions and require non-empty visible assistant content."""
    import time as _time
    last_err: Exception | None = None
    request_payload = {**payload, "model": model_name}
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                base_url + "/chat/completions",
                data=json.dumps(request_payload).encode("utf-8"),
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "NiuOne/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            content, detail = parse_chat_completion_content(raw)
            if not (content or "").strip():
                if "finish_reason=length" in detail:
                    current_max = int(request_payload.get("max_tokens") or 0)
                    if current_max > 0:
                        request_payload["max_tokens"] = min(12000, max(current_max + 2000, current_max * 2))
                raise RuntimeError(f"model={model_name} returned empty content ({detail or 'no response metadata'})")
            return content
        except urllib.error.HTTPError as exc:
            last_err = format_http_error(exc, model_name)
        except Exception as exc:
            last_err = exc
        if attempt < max_retries - 1:
            _time.sleep(2 ** attempt)
    raise last_err or RuntimeError(f"model={model_name} request failed")


def request_chat_json_object(
    base_url: str,
    api_key: str,
    payload: dict,
    model_name: str,
    *,
    max_parse_attempts: int = 3,
    timeout: int = 60,
) -> dict[str, Any]:
    """Request a JSON object, retrying truncated/malformed non-empty responses."""
    request_payload = dict(payload)
    last_error: Exception | None = None
    for attempt in range(max(1, max_parse_attempts)):
        content = request_chat_content(
            base_url,
            api_key,
            request_payload,
            model_name,
            max_retries=3,
            timeout=timeout,
        )
        try:
            result = extract_json(content)
            if not isinstance(result, dict):
                raise ValueError("model did not return object")
            return result
        except ValueError as exc:
            last_error = exc
            if attempt >= max_parse_attempts - 1:
                break
            current_max = int(request_payload.get("max_tokens") or 0)
            if current_max > 0:
                request_payload["max_tokens"] = min(12000, max(current_max + 2000, current_max * 2))
    raise last_error or RuntimeError(f"model={model_name} did not return a JSON object")


def api_call_with_retry(base_url: str, api_key: str, payload: dict, max_retries: int = 3, timeout: int = 60) -> dict:
    """带重试的 API 调用。空响应/JSON解析失败时自动重试。"""
    import time as _time
    last_err = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                base_url + "/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "NiuOne/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            if not raw.strip():
                raise ValueError("空响应")
            return json.loads(raw)
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                _time.sleep(2 ** attempt)  # 1s, 2s, 4s 退避
    raise last_err


def load_news_precheck_config() -> tuple[str, str, str] | None:
    base_url = os.environ.get("DASHBOARD_NEWS_BASE_URL", "").strip()
    api_key = os.environ.get("DASHBOARD_NEWS_API_KEY", "").strip()
    model = os.environ.get("DASHBOARD_NEWS_MODEL", "").strip()
    if not any((base_url, api_key, model)):
        return None
    missing = [
        label
        for label, value in (
            ("DASHBOARD_NEWS_BASE_URL", base_url),
            ("DASHBOARD_NEWS_API_KEY", api_key),
            ("DASHBOARD_NEWS_MODEL", model),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("消息面预检配置不完整：" + "、".join(missing))
    return base_url.rstrip("/"), api_key, model


def compact_portfolio_for_decision(portfolio: dict[str, Any]) -> dict[str, Any]:
    """Keep only account fields that can affect the next trading decision."""
    compact_positions = []
    for pos in portfolio.get("positions", []) or []:
        exit_state = pos.get("exit_state") or {}
        compact_positions.append({
            "code": pos.get("code"),
            "name": pos.get("name"),
            "qty": pos.get("qty"),
            "available_qty": pos.get("available_qty"),
            "avg_cost": pos.get("avg_cost"),
            "last_price": pos.get("last_price"),
            "prev_close": pos.get("prev_close"),
            "change_pct": pos.get("change_pct"),
            "today_pnl": pos.get("today_pnl"),
            "today_pnl_pct": pos.get("today_pnl_pct"),
            "day_high_pct": pos.get("day_high_pct"),
            "day_low_pct": pos.get("day_low_pct"),
            "market_value": pos.get("market_value"),
            "position_pct": pos.get("position_pct"),
            "industry": pos.get("industry") or "",
            "entry_stop_price": pos.get("entry_stop_price"),
            "gap_buffer_pct": pos.get("gap_buffer_pct"),
            "effective_loss_distance_pct": pos.get("effective_loss_distance_pct"),
            "position_open_risk_pct": pos.get("position_open_risk_pct"),
            "dynamic_position_cap_pct": pos.get("dynamic_position_cap_pct"),
            "risk_budget_regime": pos.get("risk_budget_regime"),
            "per_trade_risk_budget_pct": pos.get("per_trade_risk_budget_pct"),
            "max_open_risk_pct": pos.get("max_open_risk_pct"),
            "max_sector_risk_pct": pos.get("max_sector_risk_pct"),
            "pnl": pos.get("pnl"),
            "pnl_pct": pos.get("pnl_pct"),
            "buy_strategy": pos.get("buy_strategy"),
            "entry_reason": pos.get("entry_reason"),
            "strategy_mark": pos.get("strategy_mark") or {},
            "strategy_mark_id": pos.get("strategy_mark_id") or "",
            "strategy_mark_label": pos.get("strategy_mark_label") or "",
            "strategy_mark_history": (pos.get("strategy_mark_history") or [])[-4:],
            "last_exit_rule": pos.get("last_exit_rule") or "",
            "last_exit_label": pos.get("last_exit_label") or "",
            "last_exit_reason": pos.get("last_exit_reason") or "",
            "buy_date_lots": pos.get("buy_date_lots") or {},
            "exit_state": {
                key: exit_state.get(key)
                for key in [
                    "highest_price", "max_pnl_pct", "bbi", "bbi_distance_pct",
                    "bbi_break_days", "atr20", "low10", "chandelier_stop",
                    "trailing_gap_pct", "shaofu_stop_price", "sell_score",
                    "sell_score_reason", "z_white", "z_yellow",
                    "z_white_break_days", "z_dead_cross", "s123_signal",
                    "s123_reason", "chuhuo_wushi", "luzhu_half_signal",
                ]
                if exit_state.get(key) not in (None, "", [])
            },
        })
    return {
        "generated_at": portfolio.get("generated_at"),
        "initial_cash": portfolio.get("initial_cash"),
        "cash": portfolio.get("cash"),
        "market_value": portfolio.get("market_value"),
        "total_equity": portfolio.get("total_equity"),
        "total_pnl": portfolio.get("total_pnl"),
        "total_pnl_pct": portfolio.get("total_pnl_pct"),
        "sector_tide_open_risk_pct": portfolio.get("sector_tide_open_risk_pct"),
        "positions": compact_positions,
        "recent_trades": (portfolio.get("trade_log") or [])[:8],
        "last_b1_generated_at": portfolio.get("last_b1_generated_at"),
        "last_decision_at": portfolio.get("last_decision_at"),
        "last_quote_refresh": portfolio.get("last_quote_refresh"),
        "last_intraday_refresh": portfolio.get("last_intraday_refresh"),
        "last_error": portfolio.get("last_error"),
    }


def format_candidate_label(candidate: dict[str, Any]) -> str:
    code = str(candidate.get("code") or "").strip()
    name = str(candidate.get("name") or "").strip()
    return " ".join(part for part in [code, name] if part).strip() or "未知股票"


def build_single_candidate_news_prompt(candidate: dict[str, Any]) -> str:
    label = format_candidate_label(candidate)
    return f"""搜索以下A股最近3天的重大消息（利好/利空/中性），只针对这一只股票：
{label}

格式：
- 代码 名称：一句话总结（利好/利空/中性）
如没有明确重大消息，输出：
- 代码 名称：最近3天无明确重大消息（中性）"""


def request_single_candidate_news_precheck(
    candidate: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    model: str,
) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": build_single_candidate_news_prompt(candidate)}],
        "max_tokens": NEWS_PRECHECK_MAX_TOKENS,
    }
    return request_chat_content(
        base_url,
        api_key,
        payload,
        model,
        max_retries=NEWS_PRECHECK_MAX_RETRIES,
        timeout=NEWS_PRECHECK_REQUEST_TIMEOUT,
    ).strip()


def format_news_precheck_error(candidate: dict[str, Any], exc: Exception) -> str:
    detail = clip_text(f"{type(exc).__name__}: {exc}", 160)
    return f"- {format_candidate_label(candidate)}：消息面预检失败（{detail}）"


def check_candidate_news_precheck(candidates: list[dict[str, Any]]) -> str:
    """并发搜索 top5 候选股的最新消息面，返回结构化摘要。

    Returns: 格式化的消息面文本，供决策 prompt 使用。
    """
    top_candidates = [c for c in candidates[:5] if isinstance(c, dict)]
    if not top_candidates:
        return ""

    news_config = load_news_precheck_config()
    if news_config is None:
        return ""
    base_url, api_key, model = news_config

    def fetch(candidate: dict[str, Any]) -> str:
        return request_single_candidate_news_precheck(
            candidate,
            base_url=base_url,
            api_key=api_key,
            model=model,
        )

    results: list[str] = [""] * len(top_candidates)
    failures: list[str] = []
    success_count = 0
    workers = min(NEWS_PRECHECK_CONCURRENCY, len(top_candidates))
    if workers <= 1:
        for idx, candidate in enumerate(top_candidates):
            try:
                results[idx] = fetch(candidate)
                success_count += 1
            except Exception as exc:
                failures.append(format_news_precheck_error(candidate, exc))
                results[idx] = failures[-1]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            future_by_index = {
                pool.submit(fetch, candidate): idx
                for idx, candidate in enumerate(top_candidates)
            }
            for future in concurrent.futures.as_completed(future_by_index):
                idx = future_by_index[future]
                candidate = top_candidates[idx]
                try:
                    results[idx] = future.result()
                    success_count += 1
                except Exception as exc:
                    failures.append(format_news_precheck_error(candidate, exc))
                    results[idx] = failures[-1]

    if failures and success_count == 0:
        raise RuntimeError("全部股票消息面预检失败：" + "；".join(failures[:3]))

    content = "\n".join(item.strip() for item in results if item and item.strip()).strip()
    return f"【消息面预检（实时搜索，并发{workers}）】\n{content}"


def current_strategy_source() -> str:
    """Compatibility view of the old source dimension."""
    suite = current_strategy_suite()
    return STRATEGY_SOURCE_PRESET_TEXT if suite == STRATEGY_SOURCE_PRESET_TEXT else "builtin"


def current_strategy_suite() -> str:
    return active_strategy_suite(
        os.environ.get(ACTIVE_STRATEGY_ENV),
        os.environ.get(STRATEGY_SOURCE_ENV),
        os.environ.get(PERSONA_STRATEGY_ENV),
    )


def current_preset_strategy_text() -> str:
    return decode_preset_strategy_text(os.environ.get(PRESET_STRATEGY_TEXT_ENV, ""))


def current_trade_discipline_text(position_limit_desc: str, adaptive: dict[str, Any] | None = None) -> str:
    custom = decode_trade_discipline_text(os.environ.get(TRADE_DISCIPLINE_TEXT_ENV, ""))
    if custom:
        # Remove the legacy fixed-percentage stop from saved discipline text so
        # an older dashboard.env cannot reintroduce it through the model prompt.
        custom = custom.replace("、-4%硬止损", "")
        custom = re.sub(r"（止损-?4(?:\.0+)?%，仓位系数", "（仓位系数", custom)
        enabled = enabled_strategy_ids(
            os.environ.get(PERSONA_STRATEGY_ENV),
            os.environ.get(STRATEGY_SOURCE_ENV),
            os.environ.get(ACTIVE_STRATEGY_ENV),
        )
        if any(is_zettaranc_strategy(strategy_id) for strategy_id in enabled):
            custom = custom.replace(
                "- 仓位不按固定百分比硬卡：首次建仓、加仓、减仓比例由你结合评分、战法确定性、风险标记、盘面级别、现有仓位和盈亏状态决定；极端高确定性且风险可解释时，单票重仓甚至满仓也允许，但必须在reason写清楚为什么值得集中。",
                "- Z哥人格仓位必须硬执行注册战法上限（单票最高10%）、总仓位≤80%、现金≥20%，高确定性也不得突破；其他人格仓位仍结合评分、风险和盘面决定。",
            )
            custom = custom.replace(
                "- 注册策略仓位纪律只作为参考：无固定百分比硬限制。",
                "- Z哥注册策略仓位上限是执行层硬限制，不是参考值。",
            )
            custom = custom.replace(
                "- 系统底线风控：买入K线/前低止损、持仓超25日退出；",
                "- 系统底线风控：Z哥按入场战法使用专属结构止损、持仓超25日退出；",
            )
        if any(is_sector_tide_strategy(strategy_id) for strategy_id in enabled):
            custom += (
                "\n- 板块潮汐执行层动态风险预算：防守/复合风险禁止新仓；进攻/轮动/修复的单笔权益风险分别≤0.30%/0.20%/0.10%，"
                "策略内组合风险≤1.50%/0.80%/0.30%，总仓≤45%/30%/15%，行业风险≤0.60%/0.40%/0.20%，行业敞口≤12%/10%/6%；"
                "单票8%/6%/4%仅为绝对上限，同一行业最多2只。"
                "\n- 有效损失距离=结构止损距离+max(近60日向下跳空P95,0.5ATR占比)+0.20%费用滑点。"
                "\n- 板块潮汐退出：行业分数<55连续两次、潮位硬停止、策略时间窗不延续、2R减半和2ATR跟踪。"
            )
        return custom
    adaptive = adaptive or {}
    enabled = enabled_strategy_ids(
        os.environ.get(PERSONA_STRATEGY_ENV),
        os.environ.get(STRATEGY_SOURCE_ENV),
        os.environ.get(ACTIVE_STRATEGY_ENV),
    )
    return default_trade_discipline_text(
        max_open_positions=MAX_OPEN_POSITIONS,
        max_new_buys_per_decision=MAX_NEW_BUYS_PER_DECISION,
        position_limit_desc=position_limit_desc or "无固定百分比硬限制",
        adaptive_label=str(adaptive.get("label") or "中性"),
        adaptive_position_mult=float(adaptive.get("position_mult", 1.0)),
        zettaranc_enabled=any(is_zettaranc_strategy(strategy_id) for strategy_id in enabled),
        sector_tide_enabled=any(is_sector_tide_strategy(strategy_id) for strategy_id in enabled),
    )


def active_strategy_ids_for_decision() -> set[str]:
    return enabled_strategy_ids(
        os.environ.get(PERSONA_STRATEGY_ENV),
        os.environ.get(STRATEGY_SOURCE_ENV),
        os.environ.get(ACTIVE_STRATEGY_ENV),
    )


def load_decision_model_config() -> tuple[str, str]:
    # Provider selection: most models use the configured OpenAI-compatible endpoint;
    # this legacy alias keeps the OpenCode Zen free-model path working.
    if MODEL == "deepseek-v4-flash-free":
        base_url = "https://opencode.ai/zen/v1"
        if yaml is None:
            raise RuntimeError("PyYAML is required")
        cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        api_key = cfg.get("model", {}).get("api_key", "")
        if not api_key:
            raise RuntimeError("Missing OpenCode Zen API key in config.yaml")
    else:
        # 使用 Crossdesk
        base_url, api_key = load_crossdesk_config("DASHBOARD_DECISION_BASE_URL", "DASHBOARD_DECISION_API_KEY")
    return base_url, api_key


def call_model_decision(
    candidates: list[dict[str, Any]],
    portfolio: dict[str, Any],
    trade_allowed: bool,
    trade_reason: str,
    market_strategy_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_url, api_key = load_decision_model_config()
    market_env = check_market_environment()
    market_sent = check_market_sentiment()
    market_strategy_ctx = market_strategy_ctx or current_market_strategy_context()
    market_strategy_prompt = format_market_strategy_context_for_prompt(market_strategy_ctx)
    sentiment_note = ""
    if market_sent.get("sentiment") == "cold":
        sentiment_note = f"⚠️市场情绪偏冷({market_sent.get('detail','')})，建议仓位减半"
    
    # === 实时消息面预检（top5候选） ===
    news_context = ""
    try:
        top5 = candidates[:5]
        if top5:
            news_context = check_candidate_news_precheck(top5)
    except Exception as e:
        news_context = f"（消息面预检失败: {e}）"
    
    compact_candidates = candidates[:8]
    # 自适应参数（市场情绪驱动）
    adaptive = get_adaptive_params()
    # 多战法上下文：统计战法分布，给每个候选标注最优战法
    strategy_suite = current_strategy_suite()
    preset_strategy_text = current_preset_strategy_text()
    active_strategy_ids = active_strategy_ids_for_decision()
    strategy_prompt_sections = build_strategy_prompt_sections(
        strategy_suite,
        preset_strategy_text,
        active_strategy_ids,
        b3_exit_hhmm=B3_EXIT_HHMM,
        time_exit_hhmm=TIME_EXIT_HHMM,
    )
    strategy_source_label = strategy_prompt_sections["strategy_source_label"]
    strategy_labels = strategy_prompt_sections["strategy_labels"]
    active_strategy_section = strategy_prompt_sections["active_strategy_section"]
    position_limit_desc = strategy_prompt_sections["position_limit_desc"]
    portfolio_positions = [p for p in (portfolio.get("positions") or []) if isinstance(p, dict)]
    position_by_code = {
        normalize_code(pos.get("code") or ""): pos
        for pos in portfolio_positions
        if normalize_code(pos.get("code") or "")
    }
    # Build compact candidate list with strategy context
    cand_lines = []
    for c in compact_candidates:
        strat = c.get("best_strategy", "")
        strat_label = strategy_labels.get(strat, strat)
        tide_detail = ""
        if is_sector_tide_strategy(strat):
            tide_detail = (
                f"市场:{c.get('market_regime','-')}/{c.get('market_score','-')} "
                f"行业:{c.get('industry') or c.get('sector') or '-'} "
                f"潮位:{c.get('sector_status','-')}/{c.get('sector_score','-')} "
                f"行业排名:{c.get('stock_sector_rank','-')} "
                f"止损:{c.get('stop_price','-')}({c.get('stop_distance_pct','-')}%) "
                f"有效损失:{c.get('effective_loss_distance_pct','-')}% "
                f"单笔预算:{c.get('per_trade_risk_budget_pct','-')}% "
                f"动态仓位上限:{c.get('max_position_pct_by_risk','-')}% "
            )
        cand_lines.append(
            f"  {c.get('code')} {c.get('name')} 现价{c.get('price')} "
            f"涨跌{c.get('change_pct')}% "
            f"战法:{strat_label} "
            f"评分:{c.get('best_score')}/{c.get('score_total',10)} "
            f"基准:{c.get('entry_threshold','-')} "
            f"定位:{c.get('score_basis','-')} "
            f"仓位纪律:{c.get('position_hint','-')} "
            f"时间纪律:{c.get('time_stop','-')} "
            f"共识:{c.get('consensus_count',1)}/多战法 "
            f"{'距EMA20' if is_sector_tide_strategy(strat) else '距BBI'}:{c.get('distance_pct')}% "
            f"{tide_detail}"
            f"硬过滤:{','.join(c.get('hard_blockers',[]) or ['无'])} "
            f"风险:{','.join(c.get('risk_flags',[]) or ['无'])}"
        )
    candidates_section = "\n".join(cand_lines) if cand_lines else "（无候选股）"
    held_candidate_lines = []
    for c in candidates[:20]:
        code = normalize_code(c.get("code") or "")
        pos = position_by_code.get(code)
        if not pos:
            continue
        strat = c.get("best_strategy", "")
        strat_label = strategy_labels.get(strat, strat)
        tide_detail = ""
        if is_sector_tide_strategy(strat):
            tide_detail = (
                f" 行业:{c.get('industry') or c.get('sector') or '-'}"
                f" 潮位:{c.get('sector_status','-')}/{c.get('sector_score','-')}"
            )
        held_candidate_lines.append(
            f"  {code} {c.get('name') or pos.get('name')} 当前仓位{pos.get('position_pct')}% "
            f"盈亏{pos.get('pnl_pct')}% 今日{pos.get('today_pnl_pct')}% "
            f"候选战法:{strat_label} 评分:{c.get('best_score')}/{c.get('score_total',10)} "
            f"基准:{c.get('entry_threshold','-')} "
            f"{'距EMA20' if is_sector_tide_strategy(strat) else '距BBI'}:{c.get('distance_pct')}%{tide_detail} "
            f"风险:{','.join(c.get('risk_flags',[]) or ['无'])}"
        )
    held_candidates_section = "\n".join(held_candidate_lines) if held_candidate_lines else "（无当前持仓进入本轮候选池）"
    decision_portfolio = compact_portfolio_for_decision(portfolio)
    decision_intelligence_ctx = safe_decision_intelligence_context(
        portfolio,
        compact_candidates,
        market_strategy_ctx,
        news_context,
    )
    decision_intelligence_prompt = format_decision_intelligence_context_for_prompt(decision_intelligence_ctx)
    trade_discipline_text = current_trade_discipline_text(position_limit_desc, adaptive)
    prompt = f"""你是A股模拟账户交易决策器。账户初始资金100万，只做A股模拟交易，不是真实下单。
必须遵守：
{trade_discipline_text}

当前激活策略：{strategy_source_label}
当前选股范围：{friendly_stock_universe(current_stock_universe())}

【当前独立策略规则】
{active_strategy_section}

隔离要求：本轮新开仓只能依据上述当前策略及其候选；不得引用、混合或补充其他未启用策略。已有持仓继续按各自 strategy_mark 执行原策略退出纪律。

⚠️ 有风险标记的候选股，请结合其近期消息面（利空/减持/监管）综合判断，不要只看技术面。

当前是否允许交易：{trade_allowed}，原因：{trade_reason}
大盘环境：{market_env.get('detail', '未知')}
市场情绪：{market_sent.get('detail', '未知')}
{sentiment_note}
热门板块(涨停集中)：{', '.join(market_sent.get('hot_sectors', [])[:5]) or '无数据'}

{market_strategy_prompt}

{decision_intelligence_prompt}

当前账户JSON：
{json.dumps(decision_portfolio, ensure_ascii=False)}

{news_context}

本次多战法候选股（每只标注最优战法+评分）：
{candidates_section}

当前持仓与候选池重合（加仓/减仓/继续观察的重点）：
{held_candidates_section}

加仓语义与纪律：
- 对当前账户JSON里已有持仓输出 BUY，表示加仓/补仓；shares 是本次新增股数，不是目标总股数。
- 加仓只用于顺势确认或强势回踩重新达标；亏损扩大、跌破原止损、今日新买T+1锁仓、盘面谨慎/防守时，不得为了摊低成本而加仓。
- 加仓理由必须写明：原入场战法、当前盈亏/仓位、加仓后仓位占比、失效/止损条件，以及为何优于新开仓或继续HOLD。

严格返回JSON，不要markdown，不要解释，格式：
{{
  "summary":"一句中文结论（含战法偏好+总体判断）",
  "actions":[
    {{"action":"BUY|SELL|HOLD","code":"600000","name":"股票名","shares":100,"target_position_pct":3.5,"reason":"中文理由（含战法名和仓位依据）"}}
  ]
}}
如果不适合交易，返回 actions 为空或 HOLD。
"""
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": DECISION_MAX_TOKENS,
    }

    result = request_chat_json_object(
        base_url,
        api_key,
        payload,
        MODEL,
        max_parse_attempts=3,
        timeout=DECISION_REQUEST_TIMEOUT,
    )
    result["model"] = MODEL
    result["provider"] = PROVIDER_DISPLAY_NAME
    result["market_guidance"] = compact_market_strategy_context(market_strategy_ctx)
    result["decision_intelligence"] = decision_intelligence_ctx
    return result


def executable_buy_actions(decision: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    positions = state.get("positions") or {}
    buys = []
    for action in decision.get("actions") or []:
        act = str(action.get("action") or "HOLD").upper()
        code = normalize_code(action.get("code") or "")
        if act != "BUY" or not code:
            continue
        if position_qty(positions.get(code) or {}) > 0:
            continue
        shares = parse_model_action_shares(action)
        if shares is None or shares <= 0 or shares % 100 != 0:
            continue
        buys.append(action)
    return buys


def _action_code_set(actions: list[dict[str, Any]]) -> set[str]:
    return {normalize_code(action.get("code") or "") for action in actions if normalize_code(action.get("code") or "")}


def _candidate_digest_for_codes(candidates: list[dict[str, Any]], codes: set[str]) -> list[dict[str, Any]]:
    by_code = {normalize_code(c.get("code") or ""): c for c in candidates if isinstance(c, dict)}
    rows = []
    for code in codes:
        c = by_code.get(code) or {}
        rows.append({
            "code": code,
            "name": c.get("name"),
            "price": c.get("price"),
            "best_strategy": c.get("best_strategy"),
            "best_score": c.get("best_score"),
            "entry_threshold": c.get("entry_threshold"),
            "score_basis": c.get("score_basis"),
            "position_hint": c.get("position_hint"),
            "time_stop": c.get("time_stop"),
            "distance_pct": c.get("distance_pct"),
            "effective_loss_distance_pct": c.get("effective_loss_distance_pct"),
            "per_trade_risk_budget_pct": c.get("per_trade_risk_budget_pct"),
            "max_position_pct_by_risk": c.get("max_position_pct_by_risk"),
            "max_open_risk_pct": c.get("max_open_risk_pct"),
            "max_sector_risk_pct": c.get("max_sector_risk_pct"),
            "risk_flags": c.get("risk_flags") or [],
            "hard_blockers": c.get("hard_blockers") or [],
            "consensus_count": c.get("consensus_count"),
        })
    return rows


def _fallback_refine_overlimit_buys(
    decision: dict[str, Any],
    buy_actions: list[dict[str, Any]],
    max_new_buys: int,
    reason: str,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cand_by_code = {normalize_code(c.get("code") or ""): c for c in (candidates or []) if isinstance(c, dict)}

    def fallback_rank(action: dict[str, Any]) -> tuple[float, int, float]:
        code = normalize_code(action.get("code") or "")
        c = cand_by_code.get(code) or {}
        score = _safe_float(c.get("best_score", c.get("score", 0)), 0.0)
        risk_count = len(c.get("risk_flags") or [])
        dist = abs(_safe_float(c.get("distance_pct", c.get("dist_bbi_pct", 99)), 99.0))
        return (-score, risk_count, dist)

    ranked_actions = sorted(buy_actions, key=fallback_rank)
    kept_codes = _action_code_set(ranked_actions[:max(0, max_new_buys)])
    dropped = []
    for action in decision.get("actions") or []:
        code = normalize_code(action.get("code") or "")
        if str(action.get("action") or "").upper() == "BUY" and code and code not in kept_codes:
            action["action"] = "HOLD"
            action["reason"] = f"二次取舍降级为HOLD：{reason}"
            dropped.append({
                "code": code,
                "name": action.get("name") or "",
                "reason": reason,
            })
    refinement = {
        "status": "fallback",
        "max_new_buys": max_new_buys,
        "kept_codes": sorted(kept_codes),
        "dropped": dropped,
        "reason": reason,
    }
    decision["buy_refinement"] = refinement
    if dropped:
        decision["summary"] = f"{decision.get('summary') or '模型决策'}；二次取舍保留{len(kept_codes)}笔，放弃{len(dropped)}笔"
    return refinement


def refine_overlimit_buy_actions(
    decision: dict[str, Any],
    state: dict[str, Any],
    candidates: list[dict[str, Any]],
    portfolio: dict[str, Any],
    market_strategy_ctx: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    market_strategy_ctx = market_strategy_ctx or current_market_strategy_context()
    max_new_buys = max(0, int(market_strategy_ctx.get("max_new_buys_per_decision", MAX_NEW_BUYS_PER_DECISION)))
    buy_actions = executable_buy_actions(decision, state)
    if max_new_buys <= 0:
        if buy_actions:
            return _fallback_refine_overlimit_buys(decision, buy_actions, 0, "本轮盘面指引不允许新开仓", candidates)
        return None
    if len(buy_actions) <= max_new_buys:
        return None

    original_actions = _json_safe_copy(decision.get("actions") or [])
    buy_codes = _action_code_set(buy_actions)
    prompt = f"""你是A股模拟账户交易决策器的二次风控审稿人。
上一轮模型给出的新开仓BUY数量超过本轮盘面上限，必须重新思考取舍。

本轮最多允许新开仓：{max_new_buys}笔
盘面动态约束：
{json.dumps(compact_market_strategy_context(market_strategy_ctx), ensure_ascii=False)}

当前账户摘要：
{json.dumps(compact_portfolio_for_decision(portfolio), ensure_ascii=False)}

原始模型决策：
{json.dumps({"summary": decision.get("summary"), "actions": original_actions}, ensure_ascii=False)}

候选BUY对应的战法与风险摘要：
{json.dumps(_candidate_digest_for_codes(candidates, buy_codes), ensure_ascii=False)}

请只在原始BUY动作中选择最多{max_new_buys}个保留，其余必须放弃；不要新增股票，不要修改SELL动作。
选择优先级：确定性、盈亏比、距BBI/止损空间、板块资金共振、账户已有持仓集中度、盘面节奏。
严格返回JSON，不要markdown：
{{
  "summary":"一句话说明取舍逻辑",
  "keep_buy_codes":["600000"],
  "drop_buys":[{{"code":"600001","reason":"放弃原因"}}]
}}
"""
    try:
        base_url, api_key = load_decision_model_config()
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": min(DECISION_MAX_TOKENS, 2500),
        }
        content = request_chat_content(base_url, api_key, payload, MODEL, max_retries=2, timeout=DECISION_REQUEST_TIMEOUT)
        result = extract_json(content)
        if not isinstance(result, dict):
            raise RuntimeError("model did not return object")
        requested_keep = [
            normalize_code(code)
            for code in (result.get("keep_buy_codes") or [])
            if normalize_code(code) in buy_codes
        ]
        keep_codes = set(requested_keep[:max_new_buys])
        if not keep_codes:
            raise RuntimeError("model returned no valid keep_buy_codes")
        drop_reason_by_code = {
            normalize_code(item.get("code") or ""): str(item.get("reason") or "二次取舍放弃").strip()
            for item in (result.get("drop_buys") or [])
            if isinstance(item, dict)
        }
        dropped = []
        for action in decision.get("actions") or []:
            code = normalize_code(action.get("code") or "")
            if str(action.get("action") or "").upper() == "BUY" and code in buy_codes and code not in keep_codes:
                reason = drop_reason_by_code.get(code) or "超过本轮新开仓上限，二次思考后放弃"
                action["action"] = "HOLD"
                action["reason"] = f"二次取舍放弃：{reason}"
                dropped.append({"code": code, "name": action.get("name") or "", "reason": reason})
        refinement = {
            "status": "model_refined",
            "max_new_buys": max_new_buys,
            "original_buy_count": len(buy_actions),
            "kept_codes": sorted(keep_codes),
            "dropped": dropped,
            "summary": str(result.get("summary") or "").strip(),
            "model": MODEL,
            "provider": PROVIDER_DISPLAY_NAME,
        }
        decision["buy_refinement"] = refinement
        decision["summary"] = (
            f"{decision.get('summary') or '模型决策'}；二次取舍保留{len(keep_codes)}笔，"
            f"放弃{len(dropped)}笔：{refinement['summary'] or '按盘面上限择优'}"
        )
        return refinement
    except Exception as exc:
        return _fallback_refine_overlimit_buys(
            decision,
            buy_actions,
            max_new_buys,
            f"二次取舍模型失败({type(exc).__name__}: {exc})，按候选评分/风险/距BBI兜底保留前{max_new_buys}笔",
            candidates,
        )


def execute_actions(
    state: dict[str, Any],
    decision: dict[str, Any],
    candidates: list[dict[str, Any]],
    trade_allowed: bool,
    trade_reason: str,
    market_strategy_ctx: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    executed = []
    cand_by_code = {normalize_code(c.get("code", "")): c for c in candidates}
    positions = state.setdefault("positions", {})
    cash = float(state.get("cash") or 0)
    new_buys = 0
    market_strategy_ctx = market_strategy_ctx or current_market_strategy_context()
    effective_max_open_positions = int(market_strategy_ctx.get("max_open_positions", MAX_OPEN_POSITIONS))
    effective_max_new_buys = int(market_strategy_ctx.get("max_new_buys_per_decision", MAX_NEW_BUYS_PER_DECISION))
    allow_market_guidance_buys = bool(market_strategy_ctx.get("allow_new_buys", True))
    if not trade_allowed:
        return executed
    for action in (decision.get("actions") or [])[:5]:
        current_allowed, current_reason = is_a_share_execution_time()
        if not current_allowed:
            decision["execution_blocked_reason"] = f"执行前复核失败：{current_reason}"
            break
        act = str(action.get("action") or "HOLD").upper()
        code = normalize_code(action.get("code") or "")
        if not code or act == "HOLD":
            continue
        q = execution_quote(code)
        price = q.get("price") if isinstance(q.get("price"), (int, float)) else None
        if not price or price <= 0:
            continue
        price_source = q.get("execution_price_source") or q.get("source") or "quote"
        candidate = cand_by_code.get(code) or {}
        name = action.get("name") or q.get("name") or candidate.get("name") or ""
        reason = _fallback_action_reason(action, candidate, act, name)
        action["reason"] = reason
        shares = parse_model_action_shares(action)
        if shares is None or shares <= 0:
            add_execution_block(decision, code, "模型未给出有效仓位 shares，本轮不自动补默认仓位")
            continue
        if shares % 100 != 0:
            add_execution_block(decision, code, f"模型仓位{shares}股不是100股整数倍，本轮不自动取整")
            continue
        if act == "BUY":
            if not candidate or not candidate_in_stock_universe(candidate):
                add_execution_block(decision, code, "买入标的不在当前选股范围")
                continue
            if not allow_market_guidance_buys:
                add_execution_block(decision, code, f"盘面指引为{market_strategy_ctx.get('tone_label', '防守')}，暂停买入")
                continue
            blockers = candidate_buy_blockers(candidate)
            if blockers:
                add_execution_block(decision, code, "买入拦截：" + "、".join(blockers))
                continue
            buy_strategy = classify_buy_strategy(reason, candidate)
            existing_pos = positions.get(code)
            old_qty = position_qty(existing_pos or {})
            existing_entry_strategy = position_entry_strategy(existing_pos or {}) if old_qty > 0 else ""
            if old_qty > 0 and is_sector_tide_strategy(buy_strategy) and existing_entry_strategy != buy_strategy:
                add_execution_block(
                    decision,
                    code,
                    f"板块潮汐不得把{buy_strategy_label(buy_strategy)}加到原{buy_strategy_label(existing_entry_strategy)}持仓形成混合策略",
                )
                continue
            if old_qty <= 0 and open_position_count(positions) >= effective_max_open_positions:
                add_execution_block(
                    decision,
                    code,
                    f"盘面动态持仓已达{effective_max_open_positions}只上限（静态{MAX_OPEN_POSITIONS}只）",
                )
                continue
            if old_qty <= 0 and new_buys >= effective_max_new_buys:
                add_execution_block(decision, code, f"盘面动态本轮新开仓已达{effective_max_new_buys}笔上限")
                continue

            total_equity = portfolio_total_equity_for_limits(cash, positions)
            current_position_value = position_market_value(existing_pos or {}, float(price))
            current_market_value = portfolio_market_value(positions)
            if existing_pos:
                current_market_value = max(0.0, current_market_value - position_market_value(existing_pos) + current_position_value)
            requested_gross = shares * float(price)
            order_position_pct = position_pct_of_equity(requested_gross, total_equity)
            position_after_trade_value = current_position_value + requested_gross
            position_after_trade_pct = position_pct_of_equity(position_after_trade_value, total_equity)
            total_position_after_trade_pct = position_pct_of_equity(current_market_value + requested_gross, total_equity)
            tide_total_limit_pct: float | None = None
            tide_effective_stop_price = 0.0
            tide_gap_buffer_pct = 0.0
            tide_execution_buffer_pct = SECTOR_TIDE_EXECUTION_BUFFER_PCT
            tide_effective_loss_distance_pct = 0.0
            tide_position_open_risk_pct = 0.0
            tide_dynamic_position_cap_pct = 0.0
            tide_risk_budget: dict[str, float] = {}
            if is_zettaranc_strategy(buy_strategy):
                single_limit_pct = strategy_position_limit_pct(buy_strategy)
                market_total_limit_pct = float(market_strategy_ctx.get("max_total_position_pct", MAX_TOTAL_POSITION_PCT))
                reserve_pct = max(
                    MIN_CASH_RESERVE_PCT,
                    float(market_strategy_ctx.get("min_cash_reserve_pct", MIN_CASH_RESERVE_PCT)),
                )
                total_limit_pct = min(MAX_TOTAL_POSITION_PCT, market_total_limit_pct, 100.0 - reserve_pct)
                if position_after_trade_pct > single_limit_pct + 1e-9:
                    add_execution_block(
                        decision,
                        code,
                        f"Z哥{buy_strategy_label(buy_strategy)}单票仓位{position_after_trade_pct:.2f}%超过{single_limit_pct:g}%硬上限",
                    )
                    continue
                if total_position_after_trade_pct > total_limit_pct + 1e-9:
                    add_execution_block(
                        decision,
                        code,
                        f"Z哥买入后总仓位{total_position_after_trade_pct:.2f}%超过{total_limit_pct:g}%硬上限（至少保留{100-total_limit_pct:g}%现金）",
                    )
                    continue
            elif is_sector_tide_strategy(buy_strategy):
                regime = str(candidate.get("market_regime") or "")
                if candidate.get("market_hard_stop") or not candidate.get("market_allows_buys", False):
                    add_execution_block(decision, code, "板块潮汐市场风控禁止新开仓")
                    continue
                if regime not in {"offensive", "rotation", "recovery"}:
                    add_execution_block(decision, code, f"板块潮汐市场状态{regime or '缺失'}不可买入")
                    continue
                if buy_strategy == "tide_recovery" and old_qty > 0:
                    today_lots = int(((existing_pos or {}).get("buy_date_lots") or {}).get(today_key(), 0) or 0)
                    if today_lots > 0:
                        add_execution_block(decision, code, "冰点修复观察仓当日禁止加仓，须次日确认")
                        continue

                tide_risk_budget = sector_tide_risk_budget(regime)
                single_limit_pct = strategy_position_limit_pct(buy_strategy)
                market_total_limit_pct = float(market_strategy_ctx.get("max_total_position_pct", MAX_TOTAL_POSITION_PCT))
                reserve_pct = max(
                    MIN_CASH_RESERVE_PCT,
                    float(market_strategy_ctx.get("min_cash_reserve_pct", MIN_CASH_RESERVE_PCT)),
                )
                tide_total_limit_pct = min(
                    tide_risk_budget["max_total_position_pct"],
                    market_total_limit_pct,
                    100.0 - reserve_pct,
                )
                exact_position_after_pct = position_after_trade_value / total_equity * 100 if total_equity > 0 else 100.0
                exact_total_after_pct = (current_market_value + requested_gross) / total_equity * 100 if total_equity > 0 else 100.0
                if exact_total_after_pct > tide_total_limit_pct + 1e-9:
                    add_execution_block(
                        decision,
                        code,
                        f"板块潮汐{regime}状态买入后总仓位{exact_total_after_pct:.2f}%超过{tide_total_limit_pct:g}%硬上限",
                    )
                    continue

                industry = str(candidate.get("industry") or candidate.get("sector") or "").strip()
                if not industry:
                    add_execution_block(decision, code, "板块潮汐候选缺少行业归属")
                    continue
                same_industry_positions = [
                    pos_item
                    for pos_code, pos_item in positions.items()
                    if pos_code != code
                    and isinstance(pos_item, dict)
                    and position_qty(pos_item) > 0
                    and str(pos_item.get("industry") or pos_item.get("sector") or "").strip() == industry
                ]
                if old_qty <= 0 and len(same_industry_positions) >= 2:
                    add_execution_block(decision, code, f"{industry}行业已有2只持仓，达到板块潮汐上限")
                    continue
                industry_value_after = position_after_trade_value + sum(
                    position_market_value(pos_item) for pos_item in same_industry_positions
                )
                industry_pct_after = industry_value_after / total_equity * 100 if total_equity > 0 else 100.0
                sector_position_limit_pct = tide_risk_budget["max_sector_position_pct"]
                if industry_pct_after > sector_position_limit_pct + 1e-9:
                    add_execution_block(
                        decision,
                        code,
                        f"{industry}行业买入后敞口{industry_pct_after:.2f}%超过{regime}状态动态上限{sector_position_limit_pct:g}%",
                    )
                    continue

                candidate_stop_price = _safe_float(candidate.get("stop_price"), 0.0)
                existing_stop_price = _safe_float((existing_pos or {}).get("entry_stop_price"), 0.0) if old_qty > 0 else 0.0
                tide_effective_stop_price = max(candidate_stop_price, existing_stop_price)
                actual_stop_distance_pct = structural_stop_distance_pct(float(price), tide_effective_stop_price)
                if actual_stop_distance_pct <= 0 or actual_stop_distance_pct > 6:
                    add_execution_block(decision, code, "板块潮汐缺少有效结构止损，或止损距离超过6%")
                    continue
                tide_gap_buffer_pct = max(
                    _safe_float(candidate.get("gap_buffer_pct"), 0.0),
                    _safe_float((existing_pos or {}).get("gap_buffer_pct"), 0.0),
                )
                if tide_gap_buffer_pct <= 0:
                    add_execution_block(decision, code, "板块潮汐缺少历史跳空/ATR缓冲，动态风险预算无法计算")
                    continue
                tide_execution_buffer_pct = max(
                    SECTOR_TIDE_EXECUTION_BUFFER_PCT,
                    _safe_float(candidate.get("execution_buffer_pct"), SECTOR_TIDE_EXECUTION_BUFFER_PCT),
                    _safe_float((existing_pos or {}).get("execution_buffer_pct"), 0.0),
                )
                tide_effective_loss_distance_pct = effective_loss_distance_pct(
                    float(price),
                    tide_effective_stop_price,
                    gap_buffer_pct=tide_gap_buffer_pct,
                    execution_buffer_pct=tide_execution_buffer_pct,
                )
                tide_dynamic_position_cap_pct = risk_sized_position_cap_pct(
                    per_trade_risk_pct=tide_risk_budget["per_trade_risk_pct"],
                    effective_loss_distance_pct_value=tide_effective_loss_distance_pct,
                    absolute_cap_pct=single_limit_pct,
                )
                if exact_position_after_pct > tide_dynamic_position_cap_pct + 1e-9:
                    add_execution_block(
                        decision,
                        code,
                        f"板块潮汐{buy_strategy_label(buy_strategy)}买入后仓位{exact_position_after_pct:.2f}%超过风险预算动态上限"
                        f"{tide_dynamic_position_cap_pct:.2f}%（绝对上限{single_limit_pct:g}%）",
                    )
                    continue
                tide_position_open_risk_pct = position_open_risk_pct(
                    position_after_trade_value,
                    total_equity,
                    tide_effective_loss_distance_pct,
                )
                if tide_position_open_risk_pct > tide_risk_budget["per_trade_risk_pct"] + 1e-9:
                    add_execution_block(
                        decision,
                        code,
                        f"买入后有效损失风险{tide_position_open_risk_pct:.3f}%超过{regime}状态单笔预算"
                        f"{tide_risk_budget['per_trade_risk_pct']:.2f}%",
                    )
                    continue
                open_risk_after = sector_tide_existing_open_risk_pct(
                    positions,
                    total_equity,
                    excluding_code=code,
                ) + tide_position_open_risk_pct
                if open_risk_after > tide_risk_budget["max_open_risk_pct"] + 1e-9:
                    add_execution_block(
                        decision,
                        code,
                        f"板块潮汐买入后策略内未实现止损风险{open_risk_after:.3f}%超过{regime}状态组合预算"
                        f"{tide_risk_budget['max_open_risk_pct']:.2f}%",
                    )
                    continue
                sector_risk_after = sector_tide_existing_open_risk_pct(
                    positions,
                    total_equity,
                    excluding_code=code,
                    industry=industry,
                ) + tide_position_open_risk_pct
                if sector_risk_after > tide_risk_budget["max_sector_risk_pct"] + 1e-9:
                    add_execution_block(
                        decision,
                        code,
                        f"{industry}行业买入后未实现止损风险{sector_risk_after:.3f}%超过{regime}状态行业预算"
                        f"{tide_risk_budget['max_sector_risk_pct']:.2f}%",
                    )
                    continue
            qty = shares
            gross = qty * float(price)
            fees = calc_trade_fees(gross, "BUY")
            total_cost = gross + fees["total_fee"]
            if total_cost > cash:
                add_execution_block(decision, code, f"模型买入仓位{shares}股现金不足，本轮不自动缩小")
                continue
            if is_zettaranc_strategy(buy_strategy) or is_sector_tide_strategy(buy_strategy):
                equity_after_fees = max(0.0, total_equity - float(fees["total_fee"]))
                cash_after_trade = cash - total_cost
                cash_after_trade_pct = position_pct_of_equity(cash_after_trade, equity_after_fees)
                required_cash_pct = (
                    100.0 - float(tide_total_limit_pct)
                    if is_sector_tide_strategy(buy_strategy) and tide_total_limit_pct is not None
                    else max(
                        MIN_CASH_RESERVE_PCT,
                        float(market_strategy_ctx.get("min_cash_reserve_pct", MIN_CASH_RESERVE_PCT)),
                    )
                )
                if float(cash_after_trade_pct or 0) + 1e-9 < required_cash_pct:
                    add_execution_block(
                        decision,
                        code,
                        f"{buy_strategy_label(buy_strategy)}买入后现金{float(cash_after_trade_pct or 0):.2f}%低于{required_cash_pct:g}%硬下限（含交易费用）",
                    )
                    continue
            pos = positions.setdefault(code, {"code": code, "name": name, "qty": 0, "avg_cost": 0.0, "buy_date_lots": {}, "last_price": price})
            old_cost = old_qty * float(pos.get("avg_cost") or 0)
            new_qty = old_qty + qty
            pos["qty"] = new_qty
            pos.pop("shares", None)
            # Avg cost includes buy-side transaction fees.
            pos["avg_cost"] = round((old_cost + total_cost) / new_qty, 4)
            pos["name"] = name
            pos["last_price"] = price
            if is_sector_tide_strategy(buy_strategy):
                pos["industry"] = str(candidate.get("industry") or candidate.get("sector") or "").strip()
                pos["sector"] = pos["industry"]
                pos["entry_stop_price"] = round(tide_effective_stop_price, 3)
                pos["entry_stop_source"] = str(candidate.get("stop_source") or "tide_structure_low")
                pos["entry_stop_distance_pct"] = round(
                    structural_stop_distance_pct(float(price), pos["entry_stop_price"]),
                    3,
                )
                pos["entry_atr20"] = round(_safe_float(candidate.get("atr20")), 3)
                pos["gap_buffer_pct"] = round(tide_gap_buffer_pct, 3)
                pos["execution_buffer_pct"] = round(tide_execution_buffer_pct, 3)
                pos["effective_loss_distance_pct"] = round(tide_effective_loss_distance_pct, 3)
                pos["position_open_risk_pct"] = round(tide_position_open_risk_pct, 4)
                pos["dynamic_position_cap_pct"] = round(tide_dynamic_position_cap_pct, 3)
                pos["absolute_position_cap_pct"] = round(strategy_position_limit_pct(buy_strategy), 3)
                pos["risk_budget_regime"] = str(candidate.get("market_regime") or "")
                pos["per_trade_risk_budget_pct"] = tide_risk_budget.get("per_trade_risk_pct")
                pos["max_open_risk_pct"] = tide_risk_budget.get("max_open_risk_pct")
                pos["max_sector_risk_pct"] = tide_risk_budget.get("max_sector_risk_pct")
                pos["max_total_position_pct"] = tide_risk_budget.get("max_total_position_pct")
                pos["max_sector_position_pct"] = tide_risk_budget.get("max_sector_position_pct")
                pos["entry_market_regime"] = str(candidate.get("market_regime") or "")
                pos["market_regime"] = str(candidate.get("market_regime") or "")
                pos["sector_score"] = candidate.get("sector_score")
                pos["sector_status"] = candidate.get("sector_status")
                pos["stock_sector_rank"] = candidate.get("stock_sector_rank")
                pos["sector_weak_count"] = 0
            if old_qty <= 0 or not pos.get("buy_strategy"):
                pos["buy_strategy"] = buy_strategy
                pos["entry_reason"] = reason
                entry_mark_strategy = buy_strategy
                entry_mark_component = ""
                entry_mark_source = "BUY"
            elif pos.get("buy_strategy") != buy_strategy:
                pos["buy_strategy"] = "mixed"
                pos["entry_reason"] = "多批次买入：" + str(pos.get("entry_reason") or reason)
                entry_mark_strategy = "mixed"
                entry_mark_component = buy_strategy
                entry_mark_source = "BUY_ADD"
            else:
                entry_mark_strategy = buy_strategy
                entry_mark_component = ""
                entry_mark_source = "BUY_ADD"
            entry_mark = apply_entry_strategy_mark(
                pos,
                entry_mark_strategy,
                reason,
                source=entry_mark_source,
                component_strategy=entry_mark_component,
            )
            action["strategy_mark"] = entry_mark
            action["order_position_pct"] = order_position_pct
            action["position_after_trade_pct"] = position_after_trade_pct
            action["total_position_after_trade_pct"] = total_position_after_trade_pct
            if is_sector_tide_strategy(buy_strategy):
                action["effective_loss_distance_pct"] = round(tide_effective_loss_distance_pct, 3)
                action["position_open_risk_pct"] = round(tide_position_open_risk_pct, 4)
                action["dynamic_position_cap_pct"] = round(tide_dynamic_position_cap_pct, 3)
            pos["highest_price"] = round(max(float(pos.get("highest_price") or price), float(price)), 3)
            current_pnl_pct = ((float(price) / float(pos["avg_cost"]) - 1) * 100) if pos.get("avg_cost") else 0.0
            prior_max_pnl = float(pos.get("max_pnl_pct") or current_pnl_pct)
            pos["max_pnl_pct"] = round(max(prior_max_pnl, current_pnl_pct), 2)
            lots = pos.setdefault("buy_date_lots", {})
            lots[today_key()] = int(lots.get(today_key(), 0)) + qty
            cash -= total_cost
            if old_qty <= 0:
                new_buys += 1
            executed.append({"time": now_ts(), "action": "BUY", "code": code, "name": name,
                             "shares": qty, "price": round(price, 3), "amount": round(gross, 2),
                             "commission": fees["commission"], "transfer_fee": fees["transfer_fee"],
                             "stamp_duty": fees["stamp_duty"], "fee": fees["total_fee"],
                             "total_cost": round(total_cost, 2), "price_source": price_source,
                             "quote_time": q.get("quote_time") or now_ts(),
                             "quote_source": q.get("source") or price_source,
                             "order_position_pct": order_position_pct,
                             "position_after_trade_pct": position_after_trade_pct,
                             "total_position_after_trade_pct": total_position_after_trade_pct,
                             "trade_reason": current_reason, "reason": reason,
                             "buy_strategy": buy_strategy, "strategy_mark": entry_mark})
        elif act == "SELL":
            pos = positions.get(code)
            if not pos:
                continue
            avg_cost = float(pos.get("avg_cost") or 0)
            available_qty = available_to_sell(pos)
            if shares > available_qty:
                add_execution_block(
                    decision,
                    code,
                    f"模型卖出仓位{shares}股超过可卖{available_qty}股，本轮不自动缩小",
                )
                continue
            qty = shares
            gross = qty * float(price)
            total_equity = portfolio_total_equity_for_limits(cash, positions)
            current_position_value = position_market_value(pos, float(price))
            current_market_value = portfolio_market_value(positions)
            current_market_value = max(0.0, current_market_value - position_market_value(pos) + current_position_value)
            order_position_pct = position_pct_of_equity(gross, total_equity)
            position_before_trade_pct = position_pct_of_equity(current_position_value, total_equity)
            position_after_trade_value = max(0.0, current_position_value - gross)
            position_after_trade_pct = position_pct_of_equity(position_after_trade_value, total_equity)
            total_position_after_trade_pct = position_pct_of_equity(max(0.0, current_market_value - gross), total_equity)
            fees = calc_trade_fees(gross, "SELL")
            net_proceeds = gross - fees["total_fee"]
            cost_basis = qty * avg_cost
            realized_pnl = net_proceeds - cost_basis
            realized_pnl_pct = (realized_pnl / cost_basis * 100) if cost_basis > 0 else 0.0
            entry_strategy = str(
                pos.get("buy_strategy")
                or latest_buy_strategy_for_code(state, code)
                or classify_buy_strategy(str(pos.get("entry_reason") or ""))
            )
            exit_rule = classify_exit_rule(reason)
            entry_mark = compact_position_strategy_mark(pos, entry_strategy)
            exit_mark = apply_exit_strategy_mark(pos, entry_strategy, exit_rule, reason, source="SELL")
            action["strategy_mark"] = entry_mark
            action["exit_strategy_mark"] = exit_mark
            action["order_position_pct"] = order_position_pct
            action["position_before_trade_pct"] = position_before_trade_pct
            action["position_after_trade_pct"] = position_after_trade_pct
            action["total_position_after_trade_pct"] = total_position_after_trade_pct
            pos["qty"] = position_qty(pos) - qty
            pos.pop("shares", None)
            pos["last_price"] = price
            # consume non-today lots FIFO-ish
            remaining = qty
            lots = pos.get("buy_date_lots") or {}
            for date in sorted(list(lots.keys())):
                if date == today_key() or remaining <= 0:
                    continue
                use = min(int(lots.get(date) or 0), remaining)
                lots[date] = int(lots.get(date) or 0) - use
                remaining -= use
                if lots[date] <= 0:
                    lots.pop(date, None)
            if pos["qty"] <= 0:
                positions.pop(code, None)
            cash += net_proceeds
            executed.append({"time": now_ts(), "action": "SELL", "code": code, "name": pos.get("name") or name,
                             "shares": qty, "price": round(price, 3), "amount": round(gross, 2),
                             "commission": fees["commission"], "transfer_fee": fees["transfer_fee"],
                             "stamp_duty": fees["stamp_duty"], "fee": fees["total_fee"],
                             "net_proceeds": round(net_proceeds, 2), "pnl": round(realized_pnl, 2),
                             "pnl_pct": round(realized_pnl_pct, 2), "price_source": price_source,
                             "quote_time": q.get("quote_time") or now_ts(),
                             "quote_source": q.get("source") or price_source,
                             "order_position_pct": order_position_pct,
                             "position_before_trade_pct": position_before_trade_pct,
                             "position_after_trade_pct": position_after_trade_pct,
                             "total_position_after_trade_pct": total_position_after_trade_pct,
                             "trade_reason": current_reason, "reason": reason,
                             "buy_strategy": entry_strategy, "exit_rule": exit_rule,
                             "strategy_mark": entry_mark, "exit_strategy_mark": exit_mark})
    state["cash"] = round(cash, 2)
    state.setdefault("trade_log", []).extend(executed)
    del state["trade_log"][:-TRADE_LOG_LIMIT]
    return executed


def _sync_decision_to_db(log_entry: dict):
    """将决策日志同步写入 SQLite。"""
    try:
        from niuniu_db import record_decision as _rd
        _rd(log_entry)
    except Exception: pass


def _sync_trades_to_db(executed: list[dict[str, Any]]):
    """将已成交记录同步写入 SQLite。"""
    if not executed:
        return
    try:
        from niuniu_db import record_trade as _rt
        for item in executed:
            _rt(item)
    except Exception: pass


def _sync_positions_to_db(state: dict[str, Any]):
    """将当前持仓快照同步写入 SQLite。"""
    try:
        from niuniu_db import snapshot_positions as _sp
        _sp(state.get("positions", {}))
    except Exception: pass


def record_decision_log_entry(log_entry: dict[str, Any], *, mark_b1_done: bool = False) -> None:
    """Append a visible practice decision/event log and sync it to SQLite."""
    state = load_state()
    generated_at = log_entry.get("b1_generated_at") or ""
    state.setdefault("decision_log", []).append(log_entry)
    del state["decision_log"][:-50]
    state["last_decision_at"] = log_entry.get("time") or now_ts()
    if log_entry.get("decision", {}).get("error"):
        state["last_error"] = log_entry["decision"]["error"]
    if mark_b1_done and generated_at:
        state["last_b1_generated_at"] = generated_at
    _sync_decision_to_db(log_entry)
    save_state(state)


def _fallback_action_reason(action: dict[str, Any], candidate: dict[str, Any] | None, act: str, name: str) -> str:
    """Build a non-empty trade reason when the model omits one."""
    explicit = str(action.get("reason") or "").strip()
    if explicit:
        return explicit
    if act == "BUY" and candidate:
        strategy = candidate.get("score_basis") or candidate.get("best_strategy") or "候选战法"
        score = candidate.get("best_score", candidate.get("score"))
        threshold = candidate.get("entry_threshold")
        dist = candidate.get("distance_pct")
        risk_flags = ",".join(candidate.get("risk_flags") or []) or "无"
        parts = [f"{strategy}达标"]
        if score is not None:
            parts.append(f"评分{score}")
        if threshold is not None:
            parts.append(f"基准{threshold}")
        if dist is not None:
            parts.append(f"距BBI{dist}%")
        parts.append(f"风险标记{risk_flags}")
        return "模型买入：" + "，".join(parts)
    if act == "SELL":
        return f"模型卖出：{name or action.get('code') or '持仓'}风控/调仓，模型未返回详细理由"
    return "模型操作：模型未返回详细理由，按组合规则执行"


def parse_schedule_slot_minute(value: str) -> datetime | None:
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d %H:%M")
    except Exception:
        return None


def deferred_execution_due_at(schedule_slot: str, now: datetime | None = None) -> str:
    """Return the next execution timestamp for a morning schedule that completed during lunch."""
    now = now or datetime.now()
    slot_dt = parse_schedule_slot_minute(schedule_slot)
    if not slot_dt or slot_dt.date() != now.date():
        return ""
    if not (dtime(9, 30) <= slot_dt.time() <= dtime(11, 30)):
        return ""
    if dtime(11, 30) < now.time() < dtime(13, 0):
        return now.replace(hour=13, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    return ""


def decision_has_executable_actions(decision: dict[str, Any]) -> bool:
    for action in decision.get("actions") or []:
        act = str(action.get("action") or "HOLD").upper()
        code = normalize_code(action.get("code") or "")
        shares = parse_model_action_shares(action)
        if act in {"BUY", "SELL"} and code and shares is not None and shares > 0 and shares % 100 == 0:
            return True
    return False


def _json_safe_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return value


def queue_deferred_decision(
    state: dict[str, Any],
    *,
    generated_at: str,
    schedule_slot: str,
    schedule_run_kind: str,
    schedule_triggered_at: str,
    due_at: str,
    decision: dict[str, Any],
    candidates: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    pending_id = f"{schedule_slot or 'unscheduled'}|{generated_at}"
    pending = state.setdefault("pending_decisions", [])
    entry = {
        "id": pending_id,
        "status": "pending",
        "created_at": now_ts(),
        "due_at": due_at,
        "b1_generated_at": generated_at,
        "schedule_slot": schedule_slot,
        "schedule_run_kind": schedule_run_kind,
        "schedule_triggered_at": schedule_triggered_at,
        "reason": reason,
        "decision": _json_safe_copy(decision),
        "candidates": _json_safe_copy(candidates[:20]),
    }
    for idx, old in enumerate(pending):
        if isinstance(old, dict) and old.get("id") == pending_id:
            if old.get("status") == "pending":
                pending[idx] = {**old, **entry}
                return pending[idx]
            return old
    pending.append(entry)
    state["pending_decisions"] = pending[-30:]
    return entry


def execute_due_pending_decisions(now: datetime | None = None) -> dict[str, Any]:
    """Execute queued model decisions once the next A-share executable window opens."""
    now = now or datetime.now()
    trade_allowed, trade_reason = is_a_share_execution_time(now)
    if not trade_allowed:
        return {"executed": [], "attempted": 0, "reason": trade_reason}
    state = load_state()
    pending = state.get("pending_decisions") or []
    if not pending:
        return {"executed": [], "attempted": 0}

    all_executed: list[dict[str, Any]] = []
    attempted = 0
    changed = False
    for entry in pending:
        if not isinstance(entry, dict) or entry.get("status") != "pending":
            continue
        due_dt = parse_ts(entry.get("due_at") or "")
        if due_dt and now < due_dt:
            continue
        if due_dt and now.date() > due_dt.date():
            entry["status"] = "expired"
            entry["expired_at"] = now_ts()
            changed = True
            continue
        if now.time() > dtime(15, 0):
            entry["status"] = "expired"
            entry["expired_at"] = now_ts()
            changed = True
            continue

        attempted += 1
        decision = _json_safe_copy(entry.get("decision") or {})
        original_summary = str(decision.get("summary") or "").strip()
        decision["summary"] = f"延迟成交执行：{original_summary}" if original_summary else "延迟成交执行"
        decision["deferred_execution"] = {
            "source": "pending_decision",
            "created_at": entry.get("created_at") or "",
            "due_at": entry.get("due_at") or "",
            "schedule_slot": entry.get("schedule_slot") or "",
        }
        candidates = entry.get("candidates") or []
        market_strategy_ctx = current_market_strategy_context()
        refine_overlimit_buy_actions(
            decision,
            state,
            candidates if isinstance(candidates, list) else [],
            enrich_portfolio(state),
            market_strategy_ctx,
        )
        executed = execute_actions(
            state,
            decision,
            candidates if isinstance(candidates, list) else [],
            True,
            f"延迟成交触发：原计划{entry.get('schedule_slot') or '-'}，{trade_reason}",
            market_strategy_ctx,
        )
        entry["status"] = "executed"
        entry["executed_at"] = now_ts()
        entry["executed_count"] = len(executed)
        changed = True
        all_executed.extend(executed)
        log_entry = {
            "time": now_ts(),
            "b1_generated_at": entry.get("b1_generated_at") or "",
            "trade_allowed": True,
            "trade_reason": f"延迟成交触发：原计划{entry.get('schedule_slot') or '-'}，{trade_reason}",
            "decision": decision,
            "executed": executed,
        }
        for key in ("schedule_slot", "schedule_run_kind", "schedule_triggered_at"):
            if entry.get(key):
                log_entry[key] = entry.get(key)
        state.setdefault("decision_log", []).append(log_entry)
        del state["decision_log"][:-50]
        state["last_decision_at"] = log_entry["time"]
        _sync_decision_to_db(log_entry)

    if changed:
        if all_executed:
            _sync_trades_to_db(all_executed)
            _sync_positions_to_db(state)
        record_equity(state)
        save_state(state)
        if all_executed:
            _notify_trade_executions_safely(all_executed)
    return {"executed": all_executed, "attempted": attempted}


def run_decision_after_b1(b1_payload: dict[str, Any], force: bool = False) -> dict[str, Any]:
    state = load_state()
    generated_at = b1_payload.get("generated_at") or now_ts()
    schedule_slot = b1_payload.get("schedule_slot") or ""
    schedule_run_kind = b1_payload.get("schedule_run_kind") or ""
    schedule_triggered_at = b1_payload.get("schedule_triggered_at") or ""
    if not force and state.get("last_b1_generated_at") == generated_at:
        return {"skipped": True, "reason": "already_decided_for_this_b1", "state": enrich_portfolio(state)}
    sync_sector_tide_position_context(state, b1_payload)
    market_strategy_ctx = market_strategy_context_for_b1(b1_payload)
    compact_market_ctx = compact_market_strategy_context(market_strategy_ctx)
    state["market_decision_context"] = compact_market_ctx
    
    # 日内亏损预算检查
    budget_exceeded, today_pnl = check_daily_loss_budget(state)
    if budget_exceeded and not force:
        decision = {
            "summary": f"🛑 日内亏损预算触发（今日累计{today_pnl:.1f}% ≤ {DAILY_LOSS_BUDGET_PCT}%），暂停当日开仓",
            "actions": [],
            "model": "SYSTEM_RISK_BUDGET",
            "provider": "local_rule",
            "market_guidance": compact_market_ctx,
            "decision_intelligence": safe_decision_intelligence_context(enrich_portfolio(state), [], market_strategy_ctx, ""),
        }
        state["trading_paused"] = True
        state["pause_reason"] = f"日内亏损预算({today_pnl:.1f}%)"
        state["pause_since"] = now_ts()
        # 触发自优化
        try:
            from self_optimizer import run_optimization
            run_optimization()
        except Exception: pass
        log_entry = {
            "time": now_ts(), "b1_generated_at": generated_at,
            "trade_allowed": False, "trade_reason": f"日内亏损预算({today_pnl:.1f}%)",
            "decision": decision, "executed": [],
            "market_decision_context": compact_market_ctx,
        }
        if schedule_slot:
            log_entry["schedule_slot"] = schedule_slot
            log_entry["schedule_run_kind"] = schedule_run_kind
            log_entry["schedule_triggered_at"] = schedule_triggered_at
        state.setdefault("decision_log", []).append(log_entry)
        _sync_decision_to_db(log_entry)
        save_state(state)
        return {"decision": decision, "executed": [], "portfolio": enrich_portfolio(state)}
    
    # 自适应参数
    adaptive = get_adaptive_params()
    
    raw_candidates = b1_payload.get("trade_items") or b1_payload.get("items") or b1_payload.get("candidates") or []
    candidates = [
        c for c in raw_candidates
        if isinstance(c, dict) and candidate_in_stock_universe(c) and candidate_is_buyable(c)
    ]
    
    # 本轮允许交易 → 清除之前的暂停标记
    if "trading_paused" in state:
        del state["trading_paused"]
        if "pause_reason" in state:
            del state["pause_reason"]
        if "pause_since" in state:
            del state["pause_since"]
    
    trade_allowed, trade_reason = is_a_share_execution_time()
    deferred_due_at = "" if trade_allowed else deferred_execution_due_at(schedule_slot)
    market_env = check_market_environment()
    market_sent = check_market_sentiment()
    # 市场情绪过冷时降低仓位上限（模型自行判断，此处仅提示）
    sentiment_note = ""
    if market_sent["sentiment"] == "cold" and trade_allowed:
        sentiment_note = f"⚠️市场情绪偏冷({market_sent['detail']})，建议仓位减半或不建仓"
    portfolio = enrich_portfolio(state)
    try:
        if not trade_allowed and deferred_due_at:
            model_trade_reason = (
                f"计划{schedule_slot[-5:]}选股属于上午连续竞价时段；当前{trade_reason}。"
                f"请正常生成买卖策略，系统会在{deferred_due_at[-8:-3]}开盘后复核并成交。"
            )
            decision = call_model_decision(candidates, portfolio, True, model_trade_reason, market_strategy_ctx)
            refine_overlimit_buy_actions(decision, state, candidates, portfolio, market_strategy_ctx)
            execution_allowed, execution_reason = is_a_share_execution_time()
            if execution_allowed:
                trade_allowed = True
                trade_reason = execution_reason
                executed = execute_actions(state, decision, candidates, execution_allowed, execution_reason, market_strategy_ctx)
            else:
                trade_allowed = False
                trade_reason = f"{trade_reason}；已生成买卖策略，等待{deferred_due_at[-8:-3]}成交"
                executed = []
                if decision_has_executable_actions(decision):
                    pending = queue_deferred_decision(
                        state,
                        generated_at=generated_at,
                        schedule_slot=schedule_slot,
                        schedule_run_kind=schedule_run_kind,
                        schedule_triggered_at=schedule_triggered_at,
                        due_at=deferred_due_at,
                        decision=decision,
                        candidates=candidates,
                        reason=trade_reason,
                    )
                    decision["deferred_execution"] = {
                        "status": pending.get("status"),
                        "due_at": pending.get("due_at"),
                        "schedule_slot": pending.get("schedule_slot"),
                    }
                else:
                    decision["deferred_execution"] = {
                        "status": "not_queued",
                        "reason": "模型未给出可执行BUY/SELL动作",
                        "due_at": deferred_due_at,
                    }
        elif not trade_allowed:
            decision = {
                "summary": f"{trade_reason}，本轮只记录候选，不执行买卖",
                "actions": [],
                "model": MODEL,
                "provider": PROVIDER_DISPLAY_NAME,
                "market_guidance": compact_market_ctx,
                "decision_intelligence": safe_decision_intelligence_context(portfolio, candidates, market_strategy_ctx, ""),
            }
            executed = []
        else:
            decision = call_model_decision(candidates, portfolio, trade_allowed, trade_reason, market_strategy_ctx)
            refine_overlimit_buy_actions(decision, state, candidates, portfolio, market_strategy_ctx)
            execution_allowed, execution_reason = is_a_share_execution_time()
            if not execution_allowed:
                decision["decision_trade_reason"] = trade_reason
                decision["execution_blocked_reason"] = f"模型返回后复核失败：{execution_reason}"
                trade_allowed = False
                trade_reason = execution_reason
                executed = []
            else:
                if execution_reason != trade_reason:
                    decision["decision_trade_reason"] = trade_reason
                    trade_reason = execution_reason
                executed = execute_actions(state, decision, candidates, execution_allowed, execution_reason, market_strategy_ctx)
        state["last_error"] = ""
    except Exception as exc:
        decision = {
            "summary": "模型决策失败，本轮不交易",
            "actions": [],
            "model": MODEL,
            "provider": PROVIDER_DISPLAY_NAME,
            "error": f"{type(exc).__name__}: {exc}",
            "market_guidance": compact_market_ctx,
            "decision_intelligence": safe_decision_intelligence_context(
                portfolio if "portfolio" in locals() else enrich_portfolio(state),
                candidates if "candidates" in locals() else [],
                market_strategy_ctx,
                "",
            ),
        }
        executed = []
        state["last_error"] = decision["error"]
    state["last_b1_generated_at"] = generated_at
    state["last_decision_at"] = now_ts()
    log_entry = {
        "time": now_ts(),
        "b1_generated_at": generated_at,
        "trade_allowed": trade_allowed,
        "trade_reason": trade_reason,
        "decision": decision,
        "executed": executed,
        "market_decision_context": compact_market_ctx,
    }
    if schedule_slot:
        log_entry["schedule_slot"] = schedule_slot
        log_entry["schedule_run_kind"] = schedule_run_kind
        log_entry["schedule_triggered_at"] = schedule_triggered_at
    state.setdefault("decision_log", []).append(log_entry)
    del state["decision_log"][:-50]
    _sync_decision_to_db(log_entry)
    if executed:
        _sync_trades_to_db(executed)
        _sync_positions_to_db(state)
    record_equity(state)
    save_state(state)
    if executed:
        _notify_trade_executions_safely(executed)
    return {"decision": decision, "executed": executed, "portfolio": enrich_portfolio(state)}


def resume_trading() -> dict[str, Any]:
    """手动恢复交易（清除所有暂停标记）。"""
    state = load_state()
    cleared = []
    for key in ["trading_paused", "pause_reason", "pause_since"]:
        if key in state:
            del state[key]
            cleared.append(key)
    state.setdefault("decision_log", []).append({
        "time": now_ts(), "b1_generated_at": "",
        "trade_allowed": True, "trade_reason": "手动恢复交易",
        "decision": {"summary": "🔄 手动恢复交易", "actions": [], "model": "MANUAL_RESUME", "provider": "local_rule"},
        "executed": [],
    })
    save_state(state)
    return {"resumed": True, "cleared": cleared, "state": enrich_portfolio(state)}


def build_trade_rule_note() -> str:
    return (
        f"100股整数倍、T+1；模拟成交仅允许09:30-11:30、13:00-15:00，"
        f"09:15-09:25只作开盘集合竞价观察/申报参考，09:25-09:30静默期不按参考价记成交。"
        f"买入硬约束：最多{MAX_OPEN_POSITIONS}只持仓、单轮最多{MAX_NEW_BUYS_PER_DECISION}笔新仓、"
        f"午盘前默认最多{MORNING_MAX_OPEN_POSITIONS}只；Z哥单票按战法硬限制且最高{MAX_SINGLE_POSITION_PCT:g}%，"
        f"总仓位最高{MAX_TOTAL_POSITION_PCT:g}%并至少保留{MIN_CASH_RESERVE_PCT:g}%现金；其他人格仓位由模型结合盘面与风险决定。"
        f"板块潮汐另行按市场状态硬执行单笔/组合/行业动态风险预算、总仓45%/30%/15%、行业敞口12%/10%/6%；"
        f"单票8%/6%/4%仅为绝对天花板。"
        f"系统底线风控：峰值回撤/ATR吊灯保护、持仓超25日退出；"
        f"Z哥卖出风控：防卖飞5分评分、B3次日不涨离场({B3_EXIT_HHMM}开盘检查)、B2两日不延续离场、超级B1未兑现离场({TIME_EXIT_HHMM}尾盘检查)、"
        f"卤煮半仓、S1/S2/S3逃顶、出货五式、BBI/白线两日破位、白线死叉黄线。"
        f"板块潮汐按行业连续两日退潮、市场硬停止、时间窗、2R减半和2ATR跟踪退出。"
        f"买入按万一免五计费。"
    )


def get_dashboard_payload() -> dict[str, Any]:
    state = load_state()
    now = datetime.now()
    sync_sector_tide_position_context(state, load_latest_sector_tide_payload())
    prune_future_intraday_equity_points(state, now=now)
    # 看板读取必须是无交易副作用的：只刷新行情/指标和权益曲线。
    # 自动止盈止损只能由明确的交易调度流程触发，避免页面刷新造成非预定成交。
    refresh_realtime_prices(state)
    refresh_position_intraday(state)
    _refresh_position_bbi(state)
    refresh_today_sold_stocks(state)
    if not rebuild_intraday_equity_curve(state, now=now) and is_a_share_session_clock(now):
        record_equity(state)
    _sync_positions_to_db(state)
    current_market_ctx = select_current_market_strategy_context(state, now)
    if current_market_ctx:
        state["market_decision_context"] = current_market_ctx
    save_state(state)
    
    payload = enrich_portfolio(state)
    payload["equity_history"] = state.get("equity_history", [])
    payload["daily_equity_history"] = state.get("daily_equity_history", [])
    payload["trading_calendar"] = trading_day_status()
    # 补充从 DB 读取的每日资金快照（作为兜底）
    try:
        from niuniu_db import query_daily_equity as _qde
        db_daily = _qde()
        if db_daily and not payload["daily_equity_history"]:
            payload["daily_equity_history"] = db_daily
    except Exception: pass
    payload["market_environment"] = check_market_environment()
    payload["market_sentiment"] = check_market_sentiment()
    payload["market_decision_context"] = current_market_ctx
    payload["trading_paused"] = state.get("trading_paused", False)
    payload["pause_reason"] = state.get("pause_reason", "")
    payload["pause_since"] = state.get("pause_since", "")
    payload["strategy_performance"] = track_strategy_performance(state)
    payload["trade_rule_note"] = build_trade_rule_note()
    payload["fee_rule"] = {
        "commission_rate": COMMISSION_RATE,
        "commission_min": COMMISSION_MIN,
        "stamp_duty_sell_rate": STAMP_DUTY_SELL_RATE,
        "transfer_fee_rate": TRANSFER_FEE_RATE,
        "label": "万一免五；买入=佣金+过户费，卖出=佣金+过户费+印花税",
    }
    payload["decision_model"] = MODEL
    payload["decision_provider"] = PROVIDER_DISPLAY_NAME
    return payload


if __name__ == "__main__":
    if "--auto-exits" in sys.argv:
        print(json.dumps(run_auto_exits_once(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(get_dashboard_payload(), ensure_ascii=False, indent=2))
