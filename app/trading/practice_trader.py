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
import copy
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
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, time as dtime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from a_share_calendar import is_a_share_trading_day as calendar_is_a_share_trading_day, trading_day_status
from core.model_api import (
    build_model_request,
    parse_model_response,
    request_model,
    request_model_complete,
)
from core.shared_model_config import (
    LEGACY_SUMMARY_MODEL_ENV_NAMES,
    SHARED_MODEL_ENV_NAMES,
    resolve_shared_model_config,
)
from market_data.news_precheck import (
    NewsPrecheckConfig,
    cached_news_record_matches_source,
    fetch_candidate_news_records,
    format_cached_news_record,
    format_cached_news_records,
)
from market_data.tencent_kline_cache import merge_live_quote, quote_trade_date
from niuone_paths import get_dashboard_env_file, get_dashboard_home
from trading.news_decision_context import (
    DEFAULT_DECISION_NEWS_MAX_ITEMS,
    format_important_realtime_news_for_prompt,
    load_important_realtime_news_decision_context,
)
from screening.stock_universe import (
    STOCK_UNIVERSE_ENV,
    friendly_stock_universe,
    selected_stock_universe,
    stock_board,
    stock_in_universe,
    stock_name_is_st,
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
from strategies.exits import (
    NIUONE_BREAK_EVEN_AFTER_PARTIAL,
    NIUONE_CLIMAX_RUNNER_ENABLED,
    NIUONE_CLIMAX_RUNNER_LEADER_LOSS_CONFIRMATIONS,
    NIUONE_CLIMAX_RUNNER_TRAILING_ATR,
    NIUONE_LEADER_LOSS_CONFIRMATIONS,
    NIUONE_LIFECYCLE_CLIMAX_MIN_PNL_PCT,
    NIUONE_LIFECYCLE_CLIMAX_PARTIAL_RATIO,
    NIUONE_MAINLINE_WEAK_CONFIRMATIONS,
    NIUONE_MAX_HOLD_CALENDAR_DAYS,
    NIUONE_PARTIAL_TAKE_PROFIT_R,
    NIUONE_PARTIAL_TAKE_PROFIT_RATIO,
    NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_R,
    NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_RATIO,
    NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES,
    NIUONE_REVERSAL_MAINLINE_WEAK_CONFIRMATIONS,
    SHAOFU_MIN_HOLD_TRADING_DAYS,
    SHAOFU_SOFT_EXIT_CONFIRMATIONS,
    evaluate_shaofu_soft_exit,
    evaluate_strategy_time_exit,
    niuone_climax_runner_active,
    resolve_niuone_partial_take_profit,
)
from strategies.display import (
    localize_decision_display_fields,
    localize_strategy_text,
    mainline_mode_label,
    mainline_state_label,
    stock_role_label,
)
from strategies.lifecycle import NIUONE_LIFECYCLE_STAGES
from trading.accounting import ACCOUNTING_AUDIT_FIELDS, trade_counts_for_account
from trading.fees import (
    A_SHARE_COMMISSION_RATE,
    A_SHARE_MINIMUM_COMMISSION,
    A_SHARE_SELL_STAMP_DUTY_RATE,
    A_SHARE_TRANSFER_FEE_RATE,
    calculate_a_share_trade_fees,
)
from trading.niuone_forward import (
    FORWARD_EXECUTION_EVIDENCE_SCHEMA_VERSION,
    FORWARD_SELL_EXECUTION_EVIDENCE_SCHEMA_VERSION,
    decision_has_durable_candidate_evidence,
)
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
    niuone_markup_rebalance_observation,
    niuone_markup_rebalance_reentry_blocker,
    niuone_markup_upgrade_blocker,
    strategy_position_limit_pct as _strategy_position_limit_pct,
)
from strategies.prompts import (
    build_position_exit_prompt_section,
    build_strategy_prompt_sections,
    format_preset_strategy_section,
)
from strategies.prompt_strategy import (
    build_preset_decision_audit,
    build_preset_exit_audit,
    build_preset_strategy_snapshot,
    format_frozen_preset_exit_section,
    normalize_preset_strategy_interpretation,
    preset_candidate_facts,
    validate_preset_buy_audit,
    validate_preset_sell_audit,
)
from storage.prompt_strategies import PromptStrategyStore
from strategies.prompt_runtime import (
    evaluate_frozen_strategy_stage,
    resolve_prompt_order_shares,
)
from strategies.rules import replay_rule_evaluation_audit
from strategies.niuone_risk import (
    NIUONE_ABSOLUTE_POSITION_CAP_PCT,
    NIUONE_ENTRY_REGIMES,
    NIUONE_MARKUP_MOMENTUM_PROBE_MAX_EXECUTION_GAP_PCT,
    NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT,
    NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
    NIUONE_MARKUP_EARLY_UPGRADE_POSITION_CAP_PCT,
    NIUONE_MARKUP_REBALANCE_MIN_SESSIONS_AFTER_ADD,
    NIUONE_MARKUP_REBALANCE_PULLBACK_ATR,
    NIUONE_MARKUP_REBALANCE_REBOUND_ATR,
    NIUONE_MARKUP_REBALANCE_STALL_MIN_ATR,
    NIUONE_MARKUP_REBALANCE_STALL_SESSIONS,
    NIUONE_MARKUP_REBALANCE_TRIM_RATIO,
    NIUONE_MARKUP_UPGRADE_MAX_PNL_PCT,
    NIUONE_MARKUP_UPGRADE_MIN_PNL_PCT,
    NIUONE_MARKUP_UPGRADE_POSITION_CAP_PCT,
    NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY,
    NIUONE_MAX_OPEN_POSITIONS,
    niuone_risk_budget,
    niuone_structural_stop_limits,
    niuone_structure_risk_ok,
)
from strategies.scoring.common import find_n_structure_prior_low as _find_n_structure_prior_low
from strategies.scoring.zettaranc import zettaranc_industry_flow_signal
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


def token_count_value(raw: Any, default: int) -> int:
    compact = str(raw or "").replace(",", "").replace("_", "").strip()
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
REALTIME_NEWS_CACHE_FILE = DASHBOARD_HOME / "news" / "realtime_news_latest.json"


def load_dashboard_env() -> None:
    allowed = {
        "IWENCAI_NEWS_PRECHECK_ENABLED",
        "IWENCAI_ENABLED",
        "IWENCAI_BASE_URL",
        "IWENCAI_API_KEY",
        "IWENCAI_TIMEOUT_SECONDS",
        "IWENCAI_MAX_RETRIES",
        "IWENCAI_MAX_CONCURRENCY",
        "DASHBOARD_DECISION_TIMEOUT",
        "DASHBOARD_DECISION_INTELLIGENCE_ENABLED",
        "DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS",
        "DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS",
        "NEWSNOW_DECISION_ENABLED",
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
    } | set(SHARED_MODEL_ENV_NAMES) | set(LEGACY_SUMMARY_MODEL_ENV_NAMES)
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
MARKET_BREADTH_HISTORY_FILE = DASHBOARD_HOME / "cron" / "output" / "market_breadth_history.json"
STOCK_INDUSTRY_CACHE_FILE = DASHBOARD_HOME / "cron" / "output" / "stock_industry_cache.json"
CONFIG_PATH = Path(os.environ.get("DASHBOARD_CONFIG", DASHBOARD_HOME / "config.yaml")).expanduser()
STOCK_TOOLS_SCRIPT = Path(
    os.environ.get("DASHBOARD_CN_STOCK_TOOLS", SCRIPT_DIR / "entrypoints" / "cn_stock_tools.py")
).expanduser()
INITIAL_CASH = 1_000_000.0
# 交易费率：万一免五 = 佣金 0.01%，免 5 元最低佣金。
# A股另计：印花税仅卖出 0.05%，过户费双向 0.001%。
COMMISSION_RATE = A_SHARE_COMMISSION_RATE
COMMISSION_MIN = A_SHARE_MINIMUM_COMMISSION
STAMP_DUTY_SELL_RATE = A_SHARE_SELL_STAMP_DUTY_RATE
TRANSFER_FEE_RATE = A_SHARE_TRANSFER_FEE_RATE
REALTIME_QUOTE_MAX_AGE_SECONDS = 8
EQUITY_HEARTBEAT_MIN_SECONDS = 60
_PENDING_EQUITY_DB_SYNC_TIME = "_pending_equity_db_sync_time"
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
_SHARED_MODEL = resolve_shared_model_config(os.environ)
MODEL = _SHARED_MODEL.model
DECISION_STREAM_MODE = _SHARED_MODEL.stream_mode
DECISION_REASONING_EFFORT = _SHARED_MODEL.reasoning_effort
DECISION_CONTEXT_LENGTH = token_count_value(_SHARED_MODEL.context_length, 128000)
DECISION_MAX_TOKENS = token_count_value(_SHARED_MODEL.max_tokens, 4096)
DECISION_REQUEST_TIMEOUT = env_int("DASHBOARD_DECISION_TIMEOUT", 180)
PROVIDER_DISPLAY_NAME = "Crossdesk.ccwu.cc"
CROSSDESK_PROVIDER_NAME = "Crossdesk.ccwu.cc"
TRADE_LOG_LIMIT = 200
EQUITY_HISTORY_LIMIT = 500
JSON_RECENT_HISTORY_LIMITS = {
    "trade_log": TRADE_LOG_LIMIT,
    "decision_log": 50,
    "equity_history": EQUITY_HISTORY_LIMIT,
    "daily_equity_history": EQUITY_HISTORY_LIMIT,
}


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _accounted_trade_executions(
    executed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only fills that remain active after account-state reconciliation."""
    return [
        trade
        for trade in executed
        if isinstance(trade, dict) and trade_counts_for_account(trade)
    ]


def _notify_trade_executions_safely(executed: list[dict[str, Any]]) -> None:
    """Fan out persisted simulated fills without affecting trade execution."""
    accounted_executions = _accounted_trade_executions(executed)
    if not accounted_executions:
        return
    try:
        from notifications import notify_trade_executions

        results = notify_trade_executions(accounted_executions)
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


def is_a_share_equity_heartbeat_clock(dt: datetime | None = None) -> bool:
    """Equity sampling clock, including every second of the 15:00 closing minute."""
    dt = dt or datetime.now()
    if not is_a_share_trading_day(dt):
        return False
    return dtime(9, 15) <= dt.time() < dtime(15, 1)


def parse_ts(value: str) -> datetime | None:
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def equity_heartbeat_due(
    now: datetime,
    last: datetime | None,
    min_interval_seconds: int = EQUITY_HEARTBEAT_MIN_SECONDS,
) -> bool:
    """Return whether a new wall-clock minute bucket should be sampled."""
    if last is None:
        return True
    interval_minutes = max(1, math.ceil(max(0, int(min_interval_seconds or 0)) / 60))
    now_minute = now.replace(second=0, microsecond=0)
    last_minute = last.replace(second=0, microsecond=0)
    return now_minute - last_minute >= timedelta(minutes=interval_minutes)


def latest_equity_timestamp(state: dict[str, Any]) -> datetime | None:
    for item in reversed(state.get("equity_history") or []):
        if not isinstance(item, dict):
            continue
        parsed = parse_ts(item.get("time", ""))
        if parsed is not None:
            return parsed
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
        "daily_equity_history": [],
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
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        state = default_state()
    base = default_state()
    base.update(state)
    base.setdefault("positions", {})
    base.setdefault("trade_log", [])
    base.setdefault("decision_log", [])
    base.setdefault("pending_decisions", [])
    base.setdefault("equity_history", [])
    base.setdefault("daily_equity_history", [])
    return base


def _account_history_identity(kind: str, value: Any) -> str:
    if isinstance(value, Mapping):
        if kind in {"equity_history", "daily_equity_history"}:
            time_text = str(value.get("time") or value.get("date") or "")
            if time_text:
                return f"time:{time_text}"
        if kind == "trade_log":
            identity = {
                field: value.get(field, "")
                for field in (
                    "time",
                    "action",
                    "code",
                    "shares",
                    "price",
                    "reason",
                )
            }
            return "trade:" + json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
    return "payload:" + json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def load_account_history(
    kind: str,
    recent: list[Any] | None = None,
    *,
    limit: int | None = None,
) -> list[Any]:
    """Merge archived history with current JSON, preferring the current state."""
    if kind not in JSON_RECENT_HISTORY_LIMITS:
        raise ValueError(f"unsupported account history kind: {kind}")
    archived: list[Any] = []
    try:
        from niuniu_db import query_account_history as _query_history

        result = _query_history(kind, limit=limit)
        if isinstance(result, list):
            archived = result
    except Exception as exc:
        print(
            "[WARN] 账户历史读取失败，使用近期 JSON: "
            f"{type(exc).__name__}",
            flush=True,
        )

    merged: dict[str, Any] = {}
    order: list[str] = []
    for value in [*archived, *(recent or [])]:
        identity = _account_history_identity(kind, value)
        if identity not in merged:
            order.append(identity)
        merged[identity] = value
    rows = [merged[identity] for identity in order]
    rows.sort(
        key=lambda value: str(
            value.get("time") or value.get("date") or ""
        ) if isinstance(value, Mapping) else ""
    )
    if limit is not None:
        resolved_limit = max(0, int(limit))
        return rows[-resolved_limit:] if resolved_limit else []
    return rows


def _archive_account_history_before_compaction(state: Mapping[str, Any]) -> bool:
    try:
        from niuniu_db import archive_account_history as _archive_history

        return _archive_history(state) is True
    except Exception as exc:
        print(
            "[WARN] 账户历史归档失败，保留完整 JSON: "
            f"{type(exc).__name__}",
            flush=True,
        )
        return False


def _compact_account_state_json(state: Mapping[str, Any]) -> dict[str, Any]:
    compacted = dict(state)
    for kind, limit in JSON_RECENT_HISTORY_LIMITS.items():
        values = state.get(kind)
        if isinstance(values, list):
            compacted[kind] = values[-limit:]
    return compacted


_STATE_FILE_THREAD_LOCK = threading.RLock()
_STATE_FILE_LOCK_DEPTH = threading.local()


@contextmanager
def state_file_write_lock():
    """Serialize portfolio state read/merge/write cycles across threads and processes."""
    lock_file = STATE_FILE.with_name(f"{STATE_FILE.name}.lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with _STATE_FILE_THREAD_LOCK:
        depth = int(getattr(_STATE_FILE_LOCK_DEPTH, "value", 0) or 0)
        if depth:
            _STATE_FILE_LOCK_DEPTH.value = depth + 1
            try:
                yield
            finally:
                _STATE_FILE_LOCK_DEPTH.value = depth
            return

        _STATE_FILE_LOCK_DEPTH.value = 1
        try:
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
        finally:
            _STATE_FILE_LOCK_DEPTH.value = 0


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
        if not trade_counts_for_account(trade):
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


def _trade_cash_delta(trade: Mapping[str, Any]) -> float:
    """Return the signed cash movement recorded by one durable fill."""
    if not trade_counts_for_account(trade):
        return 0.0
    action = str(trade.get("action") or "").upper()
    try:
        shares = max(0, int(float(trade.get("shares") or 0)))
    except (TypeError, ValueError):
        shares = 0
    try:
        price = max(0.0, float(trade.get("price") or 0))
    except (TypeError, ValueError):
        price = 0.0
    try:
        fee = max(0.0, float(trade.get("fee") or 0))
    except (TypeError, ValueError):
        fee = 0.0
    try:
        amount = max(0.0, float(trade.get("amount") or 0))
    except (TypeError, ValueError):
        amount = 0.0
    gross = amount if amount > 0 else shares * price
    if action == "BUY":
        try:
            total_cost = float(trade.get("total_cost"))
        except (TypeError, ValueError):
            total_cost = gross + fee
        return -max(0.0, total_cost)
    if action == "SELL":
        try:
            net_proceeds = float(trade.get("net_proceeds"))
        except (TypeError, ValueError):
            net_proceeds = gross - fee
        return max(0.0, net_proceeds)
    return 0.0


def _apply_trade_to_account_snapshot(
    positions: dict[str, Any],
    trade: Mapping[str, Any],
    templates: Mapping[str, Any],
) -> bool:
    """Replay one branch-only fill onto another branch's position snapshot."""
    if not trade_counts_for_account(trade):
        return False
    action = str(trade.get("action") or "").upper()
    code = normalize_code(str(trade.get("code") or ""))
    try:
        shares = max(0, int(float(trade.get("shares") or 0)))
    except (TypeError, ValueError):
        shares = 0
    if action not in {"BUY", "SELL"} or not code or shares <= 0:
        return False

    template = templates.get(code)
    if not isinstance(template, Mapping):
        template = next(
            (
                value
                for key, value in templates.items()
                if normalize_code(str(key)) == code and isinstance(value, Mapping)
            ),
            {},
        )
    existing = positions.get(code)
    if not isinstance(existing, dict):
        existing = next(
            (
                value
                for key, value in positions.items()
                if normalize_code(str(key)) == code and isinstance(value, dict)
            ),
            None,
        )

    if action == "BUY":
        old_qty = position_qty(existing or {})
        old_avg_cost = _safe_float((existing or {}).get("avg_cost"), 0.0)
        if existing is None:
            position = copy.deepcopy(dict(template)) if template else {}
            position["qty"] = 0
            position.pop("shares", None)
            position["avg_cost"] = 0.0
            position["buy_date_lots"] = {}
        else:
            position = existing
            for key, value in dict(template).items():
                if key not in position and key not in {"qty", "shares", "avg_cost", "buy_date_lots"}:
                    position[key] = copy.deepcopy(value)

        total_cost = -_trade_cash_delta(trade)
        new_qty = old_qty + shares
        position.update({
            "code": code,
            "name": str(trade.get("name") or position.get("name") or ""),
            "qty": new_qty,
            "avg_cost": round((old_qty * old_avg_cost + total_cost) / new_qty, 4),
        })
        position.pop("shares", None)
        if not position.get("last_price"):
            position["last_price"] = _safe_float(trade.get("price"), 0.0)
        if old_qty <= 0:
            if trade.get("buy_strategy"):
                position["buy_strategy"] = trade.get("buy_strategy")
            if trade.get("reason"):
                position["entry_reason"] = trade.get("reason")
            if isinstance(trade.get("strategy_mark"), Mapping):
                position["strategy_mark"] = copy.deepcopy(trade.get("strategy_mark"))
            for key in (
                "preset_strategy_snapshot",
                "preset_strategy_interpretation",
                "preset_strategy_prompt_protocol",
                "preset_strategy_prompt_sha256",
                "preset_strategy_interpretation_sha256",
                "preset_strategy_candidate_pool_sha256",
                "preset_strategy_candidate_pool_count",
                "prompt_strategy_version_id",
                "prompt_strategy_plan_sha256",
                "prompt_strategy_entry_evaluation_id",
                "prompt_strategy_entry_audit",
            ):
                if trade.get(key) not in (None, "", {}):
                    position[key] = copy.deepcopy(trade.get(key))
        lots = position.get("buy_date_lots")
        lots = dict(lots) if isinstance(lots, Mapping) else {}
        trade_date = str(trade.get("time") or "")[:10]
        if trade_date:
            lots[trade_date] = int(lots.get(trade_date, 0) or 0) + shares
        position["buy_date_lots"] = lots
        positions[code] = position
        return True

    if existing is None or shares > position_qty(existing):
        return False
    remaining_qty = max(0, position_qty(existing) - shares)
    lots = existing.get("buy_date_lots")
    lots = dict(lots) if isinstance(lots, Mapping) else {}
    remaining_to_consume = shares
    trade_date = str(trade.get("time") or "")[:10]
    for day in sorted(lots):
        if day == trade_date or remaining_to_consume <= 0:
            continue
        lot_qty = max(0, int(lots.get(day, 0) or 0))
        used = min(lot_qty, remaining_to_consume)
        lots[day] = lot_qty - used
        remaining_to_consume -= used
    lots = {day: qty for day, qty in lots.items() if qty > 0}
    if remaining_qty <= 0:
        positions.pop(code, None)
    else:
        existing["qty"] = remaining_qty
        existing.pop("shares", None)
        existing["buy_date_lots"] = lots
    return True


def merge_divergent_trade_account_state(
    state: dict[str, Any],
    current: Mapping[str, Any],
    state_only_trades: list[dict[str, Any]],
) -> int:
    """Merge disjoint fills without dropping either writer's cash or positions."""
    positions = copy.deepcopy(current.get("positions") or {})
    templates = state.get("positions") or {}
    cash = _safe_float(current.get("cash"), _safe_float(state.get("cash"), 0.0))
    rejected_trade_count = 0
    for trade in sorted(state_only_trades, key=lambda item: str(item.get("time") or "")):
        applied = _apply_trade_to_account_snapshot(positions, trade, templates)
        if applied:
            cash += _trade_cash_delta(trade)
            continue
        try:
            rejected_sell_shares = max(
                0,
                int(float(trade.get("shares") or 0)),
            )
        except (TypeError, ValueError):
            rejected_sell_shares = 0
        if (
            trade_counts_for_account(trade)
            and str(trade.get("action") or "").upper() == "SELL"
            and normalize_code(str(trade.get("code") or ""))
            and rejected_sell_shares > 0
        ):
            trade.update({
                "accounting_status": "rejected",
                "accounting_rejected": True,
                "accounting_rejection_reason": (
                    "concurrent_sell_exceeds_available_position"
                ),
                "accounting_rejected_at": now_ts(),
            })
            rejected_trade_count += 1
    state["cash"] = round(cash, 2)
    state["positions"] = positions
    return rejected_trade_count


def _repair_pending_equity_after_accounting_rejection(
    state: dict[str, Any],
    point_time: str,
) -> bool:
    """Replace a stale branch point with the post-merge canonical account mark."""
    if not point_time:
        return False
    cash = _safe_float(state.get("cash"), 0.0)
    market_value = 0.0
    for position in (state.get("positions") or {}).values():
        if not isinstance(position, Mapping):
            continue
        quantity = position_qty(position)
        price = _safe_float(
            position.get("last_price") or position.get("avg_cost"),
            0.0,
        )
        market_value += max(0, quantity) * max(0.0, price)
    initial_cash = _safe_float(state.get("initial_cash"), INITIAL_CASH)
    if initial_cash <= 0:
        initial_cash = INITIAL_CASH
    total_equity = cash + market_value
    repaired = {
        "time": point_time,
        "equity": round(total_equity, 2),
        "cash": round(cash, 2),
        "market_value": round(market_value, 2),
        "pnl_pct": round((total_equity / initial_cash - 1) * 100, 2),
        "account_created_at": str(state.get("created_at") or ""),
    }
    changed = False
    for key in ("equity_history", "daily_equity_history"):
        history = state.get(key)
        if not isinstance(history, list):
            continue
        for index, point in enumerate(history):
            if not isinstance(point, Mapping):
                continue
            if str(point.get("time") or "") != point_time:
                continue
            history[index] = {**dict(point), **repaired}
            changed = True
    return changed


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with state_file_write_lock():
        pending_equity_sync_time = str(state.pop(_PENDING_EQUITY_DB_SYNC_TIME, "") or "")
        rejected_trade_count = 0
        state["updated_at"] = now_ts()

        # Merge append-only logs with the on-disk copy before replacing the file.
        # The lock must cover the complete read/merge/replace transaction. Atomic
        # replace alone prevents partial JSON, but it does not prevent a slower
        # writer that loaded stale state from replacing a newer decision record.
        if STATE_FILE.exists():
            current = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            current.pop(_PENDING_EQUITY_DB_SYNC_TIME, None)

            def merge_list(key: str, identity_fields: tuple[str, ...], prefer_state: bool = False) -> None:
                merged = []
                merged_by_identity: dict[tuple[str, ...], dict[str, Any]] = {}
                first = state.get(key) if prefer_state else current.get(key)
                second = current.get(key) if prefer_state else state.get(key)
                for item in (first or []) + (second or []):
                    if not isinstance(item, dict):
                        continue
                    ident = tuple(json.dumps(item.get(f, ""), ensure_ascii=False, sort_keys=True) for f in identity_fields)
                    retained = merged_by_identity.get(ident)
                    if retained is not None:
                        if key == "trade_log" and not trade_counts_for_account(item):
                            for field in ACCOUNTING_AUDIT_FIELDS:
                                if field in item:
                                    retained[field] = copy.deepcopy(item[field])
                        continue
                    merged.append(item)
                    merged_by_identity[ident] = item
                state[key] = merged

            trade_identity_fields = ("time", "action", "code", "shares", "price", "reason")

            def trade_id(item: dict[str, Any]) -> tuple[str, ...]:
                return tuple(
                    json.dumps(item.get(field, ""), ensure_ascii=False, sort_keys=True)
                    for field in trade_identity_fields
                )

            state_trade_ids = {
                trade_id(item)
                for item in (state.get("trade_log") or [])
                if isinstance(item, dict)
            }
            current_trade_ids = {
                trade_id(item)
                for item in (current.get("trade_log") or [])
                if isinstance(item, dict)
            }
            current_has_unseen_trades = bool(current_trade_ids - state_trade_ids)
            state_has_unseen_trades = bool(state_trade_ids - current_trade_ids)
            if current_has_unseen_trades:
                if state_has_unseen_trades:
                    state_only_trades = [
                        item
                        for item in (state.get("trade_log") or [])
                        if isinstance(item, dict) and trade_id(item) not in current_trade_ids
                    ]
                    rejected_trade_count += merge_divergent_trade_account_state(
                        state,
                        current,
                        state_only_trades,
                    )
                else:
                    # A slow dashboard quote refresh can save an old portfolio after
                    # the trade engine has already appended fills. Keep the traded
                    # cash/positions from disk; quote refresh can safely run again.
                    state["cash"] = current.get("cash", state.get("cash"))
                    state["positions"] = current.get("positions", state.get("positions", {}))

            merge_list("decision_log", ("time", "b1_generated_at", "decision"))
            merge_list("trade_log", trade_identity_fields)
            merge_list("pending_decisions", ("id",), prefer_state=True)
            # A writer that has not seen an already-persisted trade must not replace
            # the corresponding same-minute post-trade equity point with its stale
            # pre-trade snapshot. A writer carrying a new trade remains authoritative.
            prefer_state_equity = not current_has_unseen_trades or state_has_unseen_trades
            merge_list("equity_history", ("time",), prefer_state=prefer_state_equity)
            merge_list("daily_equity_history", ("time",), prefer_state=prefer_state_equity)

            # Position snapshots are mutable and can be stale even when the
            # append-only trade merge succeeded. Re-apply the retained ledger
            # after merging so a completed SELL cannot be resurrected.
            reconcile_positions_with_trade_log(state)
            if rejected_trade_count:
                _repair_pending_equity_after_accounting_rejection(
                    state,
                    pending_equity_sync_time,
                )

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

            current_market_ctx = current.get("market_decision_context")
            state_market_ctx = state.get("market_decision_context")
            current_market_time = str(
                current_market_ctx.get("source_time") or current_market_ctx.get("context_as_of") or ""
            ) if isinstance(current_market_ctx, dict) else ""
            state_market_time = str(
                state_market_ctx.get("source_time") or state_market_ctx.get("context_as_of") or ""
            ) if isinstance(state_market_ctx, dict) else ""
            if current_market_time > state_market_time:
                state["market_decision_context"] = current_market_ctx

        unarchived_history = {
            kind: list(state.get(kind) or [])
            for kind in JSON_RECENT_HISTORY_LIMITS
            if isinstance(state.get(kind), list)
        }
        history_archived = _archive_account_history_before_compaction(state)

        prune_non_trading_day_equity_points(state)
        prune_future_intraday_equity_points(state)
        normalize_daily_equity_history(state)
        sort_equity_history(state)

        if history_archived:
            persisted_state = _compact_account_state_json(state)
        else:
            # Existing JSON remains the recovery source until a complete archive
            # transaction succeeds, including rows rejected by active-view cleanup.
            persisted_state = dict(state)
            persisted_state.update(unarchived_history)

        equity_point_to_sync = next(
            (
                dict(point)
                for point in reversed(state.get("equity_history") or [])
                if isinstance(point, dict)
                and str(point.get("time") or "") == pending_equity_sync_time
            ),
            None,
        )
        tmp = STATE_FILE.with_name(f"{STATE_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(
            json.dumps(persisted_state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(STATE_FILE)

        if rejected_trade_count:
            # A stale writer may already have replaced today's mutable SQLite
            # position snapshot before its oversell was rejected during merge.
            _sync_positions_to_db(state)

        # Synchronize SQLite only after the canonical same-minute point is chosen.
        # Keeping this under the state-file lock preserves JSON/DB writer ordering.
        if equity_point_to_sync is not None:
            try:
                from niuniu_db import record_daily_equity as _record_db

                _record_db(equity_point_to_sync)
            except Exception:
                pass


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
                quote_time: str | None = None, volume_lots: float | None = None,
                volume_ratio: float | None = None) -> dict[str, Any]:
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
        "volume_lots": volume_lots,
        "volume_ratio": volume_ratio,
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
        volume_lots=safe_quote_float(parts[6]),
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
        volume_lots=q.get("volume_lots") if isinstance(q.get("volume_lots"), (int, float)) else None,
        volume_ratio=q.get("volume_ratio") if isinstance(q.get("volume_ratio"), (int, float)) else None,
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
        volume_lots=data.get("f47") if isinstance(data.get("f47"), (int, float)) else None,
        volume_ratio=data.get("f50") if isinstance(data.get("f50"), (int, float)) else None,
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
        if quote.get("turnover_yuan") is not None:
            pos["turnover_yuan"] = quote.get("turnover_yuan")
        if quote.get("volume_lots") is not None:
            pos["volume_lots"] = quote.get("volume_lots")
        if quote.get("volume_ratio") is not None:
            pos["volume_ratio"] = quote.get("volume_ratio")
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


def apply_realtime_price_snapshot(
    state: dict[str, Any],
    refreshed_state: dict[str, Any],
) -> None:
    """Apply fetched quote fields without restoring stale account positions."""

    refreshed_positions = {
        normalize_code(code): position
        for code, position in (refreshed_state.get("positions") or {}).items()
        if isinstance(position, dict)
    }
    quote_fields = (
        "last_price",
        "quote_time",
        "quote_source",
        "change_pct",
        "prev_close",
        "day_high",
        "day_low",
        "turnover_yuan",
        "volume_lots",
        "volume_ratio",
    )
    for code, position in (state.get("positions") or {}).items():
        if not isinstance(position, dict):
            continue
        refreshed = refreshed_positions.get(normalize_code(code))
        if refreshed is None:
            continue
        for field in quote_fields:
            if field in refreshed:
                position[field] = refreshed[field]
        if not position.get("name") and refreshed.get("name"):
            position["name"] = refreshed["name"]

    refresh_meta = refreshed_state.get("last_quote_refresh")
    if isinstance(refresh_meta, dict):
        state["last_quote_refresh"] = dict(refresh_meta)


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


def _cached_today_sold_quotes(state: dict[str, Any], today: str) -> dict[str, dict[str, Any]]:
    """Return only same-day quote fields from the persisted sold-card cache."""
    quotes: dict[str, dict[str, Any]] = {}
    for item in state.get("today_sold_stocks", []) or []:
        if not isinstance(item, dict):
            continue
        if not str(item.get("last_sell_time") or "").startswith(today):
            continue
        code = normalize_code(item.get("code") or "")
        if not code:
            continue
        quotes[code] = {
            "code": code,
            "name": item.get("name") or "",
            "price": item.get("current_price"),
            "change_pct": item.get("current_change_pct"),
            "quote_time": item.get("quote_time") or "",
            "source": item.get("quote_source") or "",
        }
    return quotes


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
            "industry": pos.get("industry") or pos.get("sector") or "",
            "entry_theme": pos.get("entry_theme") or "",
            "active_theme": pos.get("active_theme") or "",
            "entry_reason": pos.get("entry_reason") or "",
            "prompt_strategy_version_id": pos.get("prompt_strategy_version_id") or "",
            "prompt_strategy_plan_sha256": pos.get("prompt_strategy_plan_sha256") or "",
            "prompt_strategy_exit_status": pos.get("prompt_strategy_exit_status") or "",
            "prompt_strategy_exit_checked_at": pos.get("prompt_strategy_exit_checked_at") or "",
            "prompt_strategy_pending_exit": bool(pos.get("prompt_strategy_pending_exit")),
            "prompt_strategy_pending_exit_ready": bool(pos.get("prompt_strategy_pending_exit_ready")),
            "prompt_strategy_pending_exit_reason": pos.get("prompt_strategy_pending_exit_reason") or "",
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
                "industry_flow_direction": pos.get("industry_flow_direction"),
                "industry_flow_rank": pos.get("industry_flow_rank"),
                "industry_flow_net_yi": pos.get("industry_flow_net_yi"),
                "industry_outflow_rank": pos.get("industry_outflow_rank"),
                "industry_outflow_net_yi": pos.get("industry_outflow_net_yi"),
                "industry_flow_generated_at": pos.get("industry_flow_generated_at"),
                "market_turnover_ratio": pos.get("market_turnover_ratio"),
                "projected_volume_ratio_20d": pos.get("projected_volume_ratio_20d"),
                "shaofu_volume_price_signal": pos.get("shaofu_volume_price_signal"),
                "shaofu_soft_exit_status": pos.get("shaofu_soft_exit_status"),
                "shaofu_soft_exit_signal": pos.get("shaofu_soft_exit_signal"),
                "shaofu_soft_exit_count": pos.get("shaofu_soft_exit_count"),
                "shaofu_soft_exit_required": pos.get("shaofu_soft_exit_required"),
            },
        }
        pos["last_price"] = price
        rows.append(row)
    cash = float(state.get("cash") or 0)
    total_equity = cash + total_mv
    sector_tide_open_risk_pct = 0.0
    niuone_open_risk_pct = 0.0
    for row in rows:
        row["position_pct"] = position_pct_of_equity(row.get("market_value"), total_equity)
        source_pos = positions.get(row.get("code")) if isinstance(positions.get(row.get("code")), dict) else {}
        entry_strategy = position_entry_strategy(source_pos)
        if is_dynamic_risk_strategy(entry_strategy):
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
                "entry_theme": source_pos.get("entry_theme") or "",
                "active_theme": source_pos.get("active_theme") or "",
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
            if is_sector_tide_strategy(entry_strategy):
                sector_tide_open_risk_pct += open_risk
            else:
                niuone_open_risk_pct += open_risk
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
    today_sold_stocks = build_today_sold_stocks(state, today=today)
    today_sold_quote_refresh = state.get("today_sold_quote_refresh") or {}
    if (
        not isinstance(today_sold_quote_refresh, dict)
        or not str(today_sold_quote_refresh.get("quote_time") or "").startswith(today)
    ):
        today_sold_quote_refresh = {}
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
        "niuone_open_risk_pct": round(niuone_open_risk_pct, 4),
        "positions": rows,
        "trade_log": list(reversed([
            trade
            for trade in state.get("trade_log", [])
            if isinstance(trade, dict) and trade_counts_for_account(trade)
        ][-TRADE_LOG_LIMIT:])),
        "decision_log": list(reversed(state.get("decision_log", [])[-50:])),
        "pending_decisions": [
            item for item in state.get("pending_decisions", [])
            if isinstance(item, dict) and item.get("status") == "pending"
        ],
        "today_sold_stocks": today_sold_stocks,
        "today_sold_quote_refresh": today_sold_quote_refresh,
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
    return calculate_a_share_trade_fees(amount, side)


def dynamic_risk_order_ceiling(
    *,
    price: float,
    total_equity: float,
    cash: float,
    current_position_value: float,
    current_market_value: float,
    other_industry_value: float,
    dynamic_position_cap_pct: float,
    total_position_cap_pct: float,
    sector_position_cap_pct: float,
    effective_loss_distance_pct_value: float,
    max_open_risk_pct: float,
    existing_open_risk_pct: float,
    max_sector_risk_pct: float,
    existing_sector_risk_pct: float,
    required_cash_pct: float,
    board_lot: int = 100,
) -> dict[str, Any]:
    """Return the largest whole-lot order allowed by every sizing ceiling.

    Binary admission rules such as market state, candidate eligibility, and
    position count remain with the caller. This helper combines the continuous
    single-name, portfolio, theme, stop-risk, cash, fee, and cash-reserve limits
    without changing whether the execution path accepts or rejects an order.
    """
    resolved_price = float(price)
    equity = float(total_equity)
    resolved_lot = int(board_lot)
    loss_distance = float(effective_loss_distance_pct_value)
    if (
        not math.isfinite(resolved_price)
        or not math.isfinite(equity)
        or not math.isfinite(loss_distance)
        or resolved_price <= 0
        or equity <= 0
        or loss_distance <= 0
        or resolved_lot <= 0
    ):
        return {
            "maximum_permitted_shares": 0,
            "maximum_permitted_gross": 0.0,
            "binding_constraints": ["invalid_risk_inputs"],
        }

    current_value = max(0.0, float(current_position_value))
    market_value = max(0.0, float(current_market_value))
    industry_value = max(0.0, float(other_industry_value))
    open_risk_room = max(
        0.0,
        float(max_open_risk_pct) - float(existing_open_risk_pct),
    )
    sector_risk_room = max(
        0.0,
        float(max_sector_risk_pct) - float(existing_sector_risk_pct),
    )
    gross_caps = {
        "single_name_risk": max(
            0.0,
            float(dynamic_position_cap_pct) / 100.0 * equity - current_value,
        ),
        "total_exposure": max(
            0.0,
            float(total_position_cap_pct) / 100.0 * equity - market_value,
        ),
        "theme_exposure": max(
            0.0,
            float(sector_position_cap_pct) / 100.0 * equity
            - industry_value
            - current_value,
        ),
        "strategy_open_risk": max(
            0.0,
            open_risk_room / loss_distance * equity - current_value,
        ),
        "theme_open_risk": max(
            0.0,
            sector_risk_room / loss_distance * equity - current_value,
        ),
        "cash": max(0.0, float(cash)),
    }
    gross_limit = min(gross_caps.values())
    binding = sorted(
        name
        for name, value in gross_caps.items()
        if math.isclose(value, gross_limit, rel_tol=0.0, abs_tol=1e-7)
    )
    maximum_shares = (
        int(math.floor(gross_limit / resolved_price / resolved_lot))
        * resolved_lot
    )
    required_cash = max(0.0, min(100.0, float(required_cash_pct)))
    reduced_for_cash = False
    reduced_for_reserve = False
    while maximum_shares > 0:
        gross = maximum_shares * resolved_price
        fees = calc_trade_fees(gross, "BUY")
        total_cost = gross + float(fees["total_fee"])
        if total_cost > float(cash) + 1e-9:
            reduced_for_cash = True
            maximum_shares -= resolved_lot
            continue
        equity_after_fees = max(0.0, equity - float(fees["total_fee"]))
        cash_after_trade = float(cash) - total_cost
        cash_after_pct = (
            cash_after_trade / equity_after_fees * 100.0
            if equity_after_fees > 0 else 0.0
        )
        if cash_after_pct + 1e-9 < required_cash:
            reduced_for_reserve = True
            maximum_shares -= resolved_lot
            continue
        break
    if reduced_for_cash or reduced_for_reserve:
        binding = []
        if reduced_for_cash:
            binding.append("cash_after_fees")
        if reduced_for_reserve:
            binding.append("cash_reserve_after_fees")
    return {
        "maximum_permitted_shares": maximum_shares,
        "maximum_permitted_gross": round(
            maximum_shares * resolved_price,
            2,
        ),
        "binding_constraints": sorted(set(binding)),
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


def decision_candidate_rows(b1_payload: dict[str, Any]) -> list[Any]:
    """Select decision candidates without widening an explicit empty trade pool."""
    for key in ("trade_items", "items", "candidates"):
        if key in b1_payload:
            value = b1_payload.get(key)
            return value if isinstance(value, list) else []
    return []


def observed_candidate_rows(b1_payload: dict[str, Any]) -> list[Any]:
    """Return the full displayed opportunity set when the caller preserved it."""
    for key in ("observed_items", "items", "candidates", "trade_items"):
        if key in b1_payload:
            value = b1_payload.get(key)
            return value if isinstance(value, list) else []
    return []


def current_stock_universe() -> tuple[str, ...]:
    return selected_stock_universe(os.environ.get(STOCK_UNIVERSE_ENV))


def candidate_in_stock_universe(candidate: dict[str, Any] | None) -> bool:
    candidate = candidate or {}
    return stock_in_universe(
        candidate.get("code"),
        candidate.get("name"),
        current_stock_universe(),
    )


def candidate_matches_active_strategy(candidate: dict[str, Any] | None) -> bool:
    """Reject identified candidates that were generated by another strategy suite."""
    candidate = candidate or {}
    strategy_id = str(candidate.get("best_strategy") or candidate.get("strategy") or "").strip()
    return not strategy_id or strategy_id in active_strategy_ids_for_decision()


def quote_is_at_limit_up(code: str, name: str, quote: dict[str, Any] | None) -> bool:
    """Reject only quotes that have reached the board's rounded limit price."""
    quote = quote if isinstance(quote, dict) else {}
    limit_ratio = (
        0.05
        if stock_name_is_st(name)
        else 0.20
        if stock_board(code) in {"chi_next", "star_market"}
        else 0.10
    )
    price = _safe_float(quote.get("price"), 0.0)
    prev_close = _safe_float(quote.get("prev_close"), 0.0)
    if price > 0 and prev_close > 0:
        limit_price = float(
            (Decimal(str(prev_close)) * Decimal(str(1.0 + limit_ratio))).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        )
        return price >= limit_price - 1e-9
    change_pct = _safe_float(quote.get("change_pct"), -999.0)
    return change_pct >= limit_ratio * 100


def add_execution_block(
    decision: dict[str, Any],
    code: str,
    reason: str,
    *,
    category: str = "other",
) -> None:
    blocks = decision.setdefault("execution_blocked_reasons", [])
    text = f"{code}: {reason}" if code else reason
    blocks.append(text)
    decision["execution_blocked_reason"] = "；".join(blocks[-5:])
    decision.setdefault("execution_blocks", []).append({
        "code": normalize_code(code),
        "category": str(category or "other"),
        "reason": str(reason or ""),
    })


def record_equity(state: dict[str, Any]) -> bool:
    if not is_a_share_trading_day():
        prune_non_trading_day_equity_points(state)
        return False
    prune_non_trading_day_equity_points(state)
    prune_future_intraday_equity_points(state)
    normalize_daily_equity_history(state)
    sort_equity_history(state)
    snap = enrich_portfolio(state)
    history = state.setdefault("equity_history", [])
    raw_now = now_ts()
    now_dt = parse_ts(raw_now)
    now = (
        now_dt.replace(second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        if now_dt
        else raw_now
    )
    today = now[:10]
    
    # 获取今天已有的所有记录
    today_records = [h for h in history if h.get("time", "").startswith(today)]
    
    # 获取每日结算净值历史（按天存储）
    daily_history = state.setdefault("daily_equity_history", [])
    
    # 按自然分钟保存日内点，或者收盘(15:00)时强制保存。
    should_save = False
    is_closing_point = False
    
    if not today_records:
        should_save = True
    else:
        last_time_str = today_records[-1].get("time", "")
        if last_time_str:
            try:
                last_dt = parse_ts(last_time_str)
                if now_dt is None or last_dt is None:
                    should_save = True
                elif equity_heartbeat_due(now_dt, last_dt):
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
            "account_created_at": str(state.get("created_at") or ""),
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
        
        # Defer SQLite synchronization until save_state() has merged any
        # concurrent trade and selected the canonical same-minute point.
        state[_PENDING_EQUITY_DB_SYNC_TIME] = now
    return should_save


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
        if isinstance(trade, dict)
        and trade_counts_for_account(trade)
        and str(trade.get("time", "")).startswith(today)
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
                "account_created_at": str(state.get("created_at") or ""),
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

    state[_PENDING_EQUITY_DB_SYNC_TIME] = str(final_point.get("time") or "")
    return True


def build_today_sold_stocks(
    state: dict[str, Any],
    today: str | None = None,
    *,
    quote_map: dict[str, dict[str, Any]] | None = None,
    quote_meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build today's sold cards from the trade ledger without external I/O."""
    today = today or today_key()
    sold: dict[str, dict[str, Any]] = {}
    for trade in state.get("trade_log", []) or []:
        if not isinstance(trade, dict):
            continue
        if not trade_counts_for_account(trade):
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
        return []

    resolved_quote_map = quote_map if quote_map is not None else _cached_today_sold_quotes(state, today)
    resolved_quote_meta = quote_meta if isinstance(quote_meta, dict) else {}

    rows: list[dict[str, Any]] = []
    for code, row in sold.items():
        shares = int(row["shares"] or 0)
        avg_sell_price = (float(row["sell_amount"]) / shares) if shares > 0 else 0.0
        cost_basis = float(row["net_proceeds"]) - float(row["realized_pnl"])
        quote = resolved_quote_map.get(code) or {}
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
            "quote_time": quote.get("quote_time") or resolved_quote_meta.get("quote_time") or "",
            "quote_source": quote.get("source") or "",
        })
    rows.sort(key=lambda item: item.get("last_sell_time") or "", reverse=True)
    return rows


def refresh_today_sold_stocks(state: dict[str, Any], today: str | None = None) -> list[dict[str, Any]]:
    """Aggregate today's SELL trades and refresh quotes for post-sale tracking."""
    today = today or today_key()
    rows_without_quotes = build_today_sold_stocks(state, today=today, quote_map={})
    if not rows_without_quotes:
        state["today_sold_stocks"] = []
        state["today_sold_quote_refresh"] = {"quote_time": now_ts(), "updated": 0}
        return []

    quote_map = _cached_today_sold_quotes(state, today)
    quote_meta: dict[str, Any] = {"quote_time": now_ts(), "updated": 0}
    try:
        refreshed_quotes, quote_meta = fetch_realtime_quotes(
            sorted(row["code"] for row in rows_without_quotes)
        )
        quote_map.update(refreshed_quotes)
    except Exception as exc:
        quote_meta = {"quote_time": now_ts(), "updated": 0, "error": f"{type(exc).__name__}: {exc}"}

    rows = build_today_sold_stocks(
        state,
        today=today,
        quote_map=quote_map,
        quote_meta=quote_meta,
    )
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
SHAOFU_SOFT_EXIT_START_TIME = dtime(10, 0)  # 开盘前30分钟仅允许结构性硬退出
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
NEWSNOW_DECISION_ENABLED = env_bool("NEWSNOW_DECISION_ENABLED", True)


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


def _market_summary_tone(summary: dict[str, Any]) -> str:
    tone = str(summary.get("tone") or "").strip().lower()
    if tone in {"offensive", "balanced", "neutral", "cautious", "defensive"}:
        return tone
    label = str(summary.get("tone_label") or "").strip()
    label_tones = {
        "进攻": "offensive",
        "平衡": "balanced",
        "中性": "neutral",
        "谨慎": "cautious",
        "防守": "defensive",
    }
    if label in label_tones:
        return label_tones[label]
    return classify_market_guidance_tone(
        "\n".join(
            str(value or "")
            for value in (summary.get("summary"), *(summary.get("risk_lines") or []))
        )
    )


def market_strategy_context_from_summary(
    summary: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the trading context from the same artifact shown as the market summary."""
    if not isinstance(summary, dict) or not summary.get("available"):
        return _market_context_base(now)
    tone = _market_summary_tone(summary)
    tone_label = _market_tone_label(tone)
    summary_text = str(summary.get("summary") or "").strip()
    guidance = [f"风险级别：{tone_label}"]
    if summary_text:
        guidance.append(f"盘面总结：{summary_text}")
    for key, label, limit in (
        ("comparison_lines", "实时对比", 2),
        ("structure_lines", "市场结构", 2),
        ("risk_lines", "风险变化", 2),
    ):
        rows = [str(item).strip() for item in (summary.get(key) or []) if str(item).strip()]
        guidance.extend(f"{label}：{row}" for row in rows[:limit])
    generated_at = str(summary.get("generated_at") or summary.get("live_snapshot_at") or "")
    # Apply limits from the artifact's explicit tone only. Objective summary text
    # may mention words such as “暂停” or “偏弱” as facts and must not silently
    # create a second, keyword-derived evaluation.
    classifier_level = tone_label if tone != "neutral" else "neutral"
    report = {
        "title": "此刻盘面总结与评价",
        "time": generated_at,
        "content": "",
        "metadata": {"decision_guidance": [f"风险级别：{classifier_level}"]},
    }
    ctx = derive_market_strategy_context([report], now)
    ctx.update({
        "tone": tone,
        "tone_label": tone_label,
        "guidance_lines": guidance[:8],
        "source_kind": "practice_market_summary",
        "source_title": "此刻盘面总结与评价",
        "source_time": generated_at,
        "context_kind": "current",
        "context_as_of": generated_at,
        "refresh_mode": "market_summary",
        "trigger": str(summary.get("trigger") or "manual"),
        "summary": summary_text,
        "model_used": bool(summary.get("model_used")),
    })
    return ctx


def persist_market_summary_context(
    summary: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist a successful unified summary/evaluation without replacing newer state."""
    ctx = market_strategy_context_from_summary(summary, now)
    if not ctx.get("available"):
        raise ValueError("此刻盘面总结不可用")
    compact = compact_market_strategy_context(ctx)
    with state_file_write_lock():
        state = load_state()
        existing = state.get("market_decision_context")
        existing_ctx = existing if isinstance(existing, dict) else {}
        existing_dt = parse_ts(str(existing_ctx.get("source_time") or ""))
        current_dt = parse_ts(str(compact.get("source_time") or ""))
        if existing_dt and current_dt and existing_dt > current_dt:
            return dict(existing_ctx)
        state["market_decision_context"] = compact
        save_state(state)
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
    payload = b1_payload if isinstance(b1_payload, dict) else {}
    unified_summary = payload.get("market_summary")
    if isinstance(unified_summary, dict):
        if unified_summary.get("available"):
            return market_strategy_context_from_summary(unified_summary, now)
        return _market_context_base(now)
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
        "max_new_buys_per_decision", "max_total_position_pct", "min_cash_reserve_pct",
        "buy_budget_multiplier", "allow_new_buys", "source_title", "source_time",
        "session_note", "guidance_lines", "overnight_us", "context_kind", "context_as_of",
        "refresh_mode", "market_snapshot", "source_kind", "trigger", "summary", "model_used",
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
    # Keep the historical no-argument call contract: runtime integrations and
    # tests monkeypatch this helper with zero-argument providers.
    report_ctx = compact_market_strategy_context(current_market_strategy_context())
    saved = (state or {}).get("market_decision_context")
    saved_ctx = dict(saved) if isinstance(saved, dict) else {}
    now = now or datetime.now()
    saved_dt = parse_ts(str(saved_ctx.get("source_time") or ""))
    if (
        saved_ctx.get("source_kind") == "practice_market_summary"
        and saved_dt is not None
        and saved_dt.date() == now.date()
    ):
        selected = saved_ctx
        selected["context_kind"] = "current"
        selected["context_as_of"] = selected.get("source_time") or selected.get("context_as_of") or ""
        return selected
    report_dt = parse_ts(str(report_ctx.get("source_time") or ""))
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
            lines.append("- 暂无此刻盘面总结，按午盘前保留仓位和静态风控执行。")
        else:
            lines.append("- 暂无此刻盘面总结，按静态风控执行。")
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
    try:
        realtime_news = load_important_realtime_news_decision_context(
            REALTIME_NEWS_CACHE_FILE,
            enabled=NEWSNOW_DECISION_ENABLED,
            max_items=DEFAULT_DECISION_NEWS_MAX_ITEMS,
        )
    except Exception as exc:
        realtime_news = {
            "enabled": NEWSNOW_DECISION_ENABLED,
            "available": False,
            "status": "unavailable",
            "items": [],
            "error": type(exc).__name__,
        }
    source_status = {
        key: _source_status(value if isinstance(value, dict) else {})
        for key, value in sources.items()
    }
    if realtime_news.get("enabled"):
        if realtime_news.get("error"):
            news_status = "stale" if realtime_news.get("stale") else "error"
        elif realtime_news.get("stale"):
            news_status = "stale"
        else:
            news_status = str(realtime_news.get("status") or "empty")
        source_status["realtime_news"] = news_status
    ctx = {
        "enabled": True,
        "generated_at": raw.get("generated_at") if isinstance(raw, dict) else now_ts(),
        "source_status": source_status,
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
        "realtime_news": realtime_news,
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

    realtime_news_prompt = format_important_realtime_news_for_prompt(
        ctx.get("realtime_news") if isinstance(ctx.get("realtime_news"), dict) else {}
    )
    if realtime_news_prompt:
        lines.extend(realtime_news_prompt.splitlines())

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
        "决策要求：每个BUY/SELL/HOLD都必须同时考虑盘面指引、隔夜美股/美股映射、指数/期货、板块与资金、"
        "已启用的财经快讯重要信息、候选消息面、账户仓位和现金状态；"
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
    """Check today's equity change without treating lifetime P&L as intraday loss."""
    today = today_key()
    positions = state.get("positions") or {}
    current_equity = float(state.get("cash") or 0) + portfolio_market_value(positions)

    prior_points: list[dict[str, Any]] = []
    for key in ("daily_equity_history", "equity_history"):
        for point in state.get(key) or []:
            if not isinstance(point, dict) or str(point.get("time") or "")[:10] >= today:
                continue
            equity = _safe_float(point.get("equity"), 0.0)
            if equity > 0 and math.isfinite(equity):
                prior_points.append(point)
    if prior_points:
        previous_equity = _safe_float(
            max(prior_points, key=lambda point: str(point.get("time") or "")).get("equity"),
            0.0,
        )
        pnl_pct = (current_equity / previous_equity - 1) * 100 if previous_equity > 0 else 0.0
        return pnl_pct <= DAILY_LOSS_BUDGET_PCT, pnl_pct

    daily_pnl = 0.0
    complete = True
    for trade in state.get("trade_log") or []:
        if not isinstance(trade, dict) or not str(trade.get("time") or "").startswith(today):
            continue
        if not trade_counts_for_account(trade):
            continue
        if str(trade.get("action") or "").upper() != "SELL":
            continue
        trade_day_pnl = _safe_float(trade.get("day_pnl"), float("nan"))
        if not math.isfinite(trade_day_pnl):
            complete = False
        else:
            daily_pnl += trade_day_pnl
    for pos in positions.values():
        if not isinstance(pos, dict):
            continue
        qty = position_qty(pos)
        if qty <= 0:
            continue
        price = _safe_float(pos.get("last_price") or pos.get("close") or pos.get("avg_cost"), 0.0)
        prev_close = _safe_float(pos.get("prev_close"), 0.0)
        position_pnl, _position_pnl_pct = position_today_pnl(pos, price, qty, prev_close)
        if position_pnl is None or not math.isfinite(position_pnl):
            complete = False
        else:
            daily_pnl += position_pnl
    if not complete:
        return False, 0.0
    opening_equity = current_equity - daily_pnl
    pnl_pct = daily_pnl / opening_equity * 100 if opening_equity > 0 else 0.0
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


def trading_holding_days(pos: dict[str, Any], today: str | None = None) -> int:
    """A-share trading days elapsed after the earliest still-open lot."""
    today = today or today_key()
    lots = pos.get("buy_date_lots") or {}
    open_dates = sorted(date for date, qty in lots.items() if int(qty or 0) > 0)
    if not open_dates:
        return 0
    try:
        entry = datetime.strptime(open_dates[0], "%Y-%m-%d").date()
        end = datetime.strptime(today, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return 0
    if end <= entry:
        return 0
    elapsed = 0
    current = entry + timedelta(days=1)
    while current <= end:
        if trading_day_status(current, allow_refresh=False).get("is_trading_day"):
            elapsed += 1
        current += timedelta(days=1)
    return elapsed


def is_shaofu_soft_exit_check_time(dt: datetime | None = None) -> bool:
    current = dt or datetime.now()
    return current.time() >= SHAOFU_SOFT_EXIT_START_TIME


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


def is_niuone_strategy(strategy_id: str) -> bool:
    return STRATEGY_DEFINITIONS.get(str(strategy_id or ""), {}).get("persona") == "niuone"


def niuone_opened_position_codes_on_date(
    state: dict[str, Any],
    trading_date: str | None = None,
) -> set[str]:
    """Return durable NiuOne opening codes for one Beijing trading date.

    Adds do not consume the daily opening budget. Code de-duplication keeps
    replayed or merged copies of the same fill idempotent.
    """
    target_date = str(trading_date or today_key())[:10]
    opened_codes: set[str] = set()
    for raw_trade in state.get("trade_log") or []:
        if not isinstance(raw_trade, dict):
            continue
        if not trade_counts_for_account(raw_trade):
            continue
        if str(raw_trade.get("action") or "").upper() != "BUY":
            continue
        if str(raw_trade.get("time") or "")[:10] != target_date:
            continue
        strategy_mark = raw_trade.get("strategy_mark")
        strategy_id = str(raw_trade.get("buy_strategy") or "")
        if not strategy_id and isinstance(strategy_mark, dict):
            strategy_id = str(strategy_mark.get("strategy_id") or "")
        if not is_niuone_strategy(strategy_id):
            continue
        position_opened = raw_trade.get("position_opened") is True
        before_qty = raw_trade.get("position_before_qty")
        opened_from_quantity = (
            before_qty is not None
            and not isinstance(before_qty, bool)
            and _safe_float(before_qty, -1.0) == 0.0
        )
        if not position_opened and not opened_from_quantity:
            continue
        code = normalize_code(raw_trade.get("code") or "")
        if code:
            opened_codes.add(code)
    return opened_codes


NIUONE_ENTRY_CONTEXT_FIELDS = (
    "entry_niuone_lifecycle_stage",
    "entry_niuone_lifecycle_label",
    "entry_niuone_lifecycle_order",
    "entry_niuone_lifecycle_entry_policy",
    "entry_mainline_state",
    "entry_mainline_score",
    "entry_mainline_score_change",
    "entry_mainline_state_streak",
    "entry_mainline_cross_day_persistent",
    "entry_mainline_confirmed",
    "entry_today_strength_score",
    "entry_strong_stock_count",
    "entry_effective_strong_count",
    "entry_stock_sector_rank",
    "entry_stock_strong",
    "entry_stock_leader_tier",
    "entry_stock_activity_score",
    "entry_stock_market_amount_percentile",
    "entry_stock_theme_amount_percentile",
    "entry_stock_activity_confirmed",
    "entry_daily_v_recovery_ratio",
    "entry_signal_score",
    "entry_candidate_pool_size",
    "entry_same_stage_candidate_count",
    "entry_same_stage_candidate_rank",
    "entry_same_stage_top_score_gap",
    "entry_execution_reference_price",
    "entry_execution_gap_pct",
    "entry_signal_generated_at",
    "entry_schedule_slot",
    "entry_schedule_run_kind",
    "entry_schedule_triggered_at",
    "entry_execution_mode",
    "entry_industry",
    "entry_theme",
    "entry_theme_basis",
    "entry_theme_attribution_score",
    "entry_theme_attribution_weight",
    "entry_theme_historical_prior_score",
    "entry_theme_cohort_alignment_score",
    "entry_theme_peer_resonance_score",
    "entry_theme_return_correlation_score",
    "entry_theme_return_correlation_rank_score",
    "entry_theme_return_correlation_observation_count",
    "entry_theme_return_correlation_peer_count",
    "entry_theme_specificity_score",
    "entry_theme_membership_source",
    "entry_theme_unattributed_weight",
    "entry_model_requested_shares",
    "entry_executed_shares",
    "entry_maximum_permitted_shares",
    "entry_risk_ceiling_utilization_pct",
    "entry_risk_ceiling_binding_constraints",
    "entry_risk_ceiling_auto_reduced",
)

NIUONE_HOLDING_LIFECYCLE_SCHEMA_VERSION = 1
NIUONE_HOLDING_LIFECYCLE_PATH_FIELD = "niuone_holding_lifecycle_path"
NIUONE_THEME_SWITCH_CONFIRMATIONS = 2
NIUONE_THEME_SWITCH_MIN_ATTRIBUTION_GAP = 10.0


def _niuone_lifecycle_path_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 4) if math.isfinite(number) else None


def _niuone_lifecycle_path_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return text


def _niuone_lifecycle_path_datetime(value: Any) -> datetime | None:
    text = _niuone_lifecycle_path_timestamp(value)
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    offset = parsed.utcoffset()
    if offset is not None:
        parsed = (parsed - offset).replace(tzinfo=None)
    return parsed


def record_niuone_lifecycle_observation(
    position: dict[str, Any],
    *,
    observed_at: str,
    source: str,
    complete_from_entry: bool = False,
) -> bool:
    """Append or extend one canonical holding-stage segment.

    This telemetry never changes a buy, sell, score, or position limit.  A
    segment is appended only when the canonical lifecycle stage changes;
    repeated scans extend the last segment so the stored path remains compact.
    """
    timestamp = _niuone_lifecycle_path_timestamp(observed_at)
    stage = str(position.get("niuone_lifecycle_stage") or "").strip()
    definition = NIUONE_LIFECYCLE_STAGES.get(stage)
    if not timestamp or definition is None:
        return False
    observation_detail = {
        "observed_at": timestamp,
        "source": str(source or "holding_context"),
        "mainline_state": str(position.get("mainline_state") or ""),
        "mainline_score": _niuone_lifecycle_path_number(
            position.get("mainline_score")
        ),
    }
    observation = {
        "stage": stage,
        "label": str(definition.get("label") or ""),
        "order": int(definition.get("order") or 0),
        "entry_policy": str(definition.get("entry_policy") or ""),
        "entered_at": timestamp,
        "last_observed_at": timestamp,
        "observation_count": 1,
        "source": str(source or "holding_context"),
        "observations": [observation_detail],
        "mainline_state_at_entry": str(
            position.get("mainline_state") or ""
        ),
        "mainline_score_at_entry": _niuone_lifecycle_path_number(
            position.get("mainline_score")
        ),
        "last_mainline_state": str(position.get("mainline_state") or ""),
        "last_mainline_score": _niuone_lifecycle_path_number(
            position.get("mainline_score")
        ),
    }
    raw_path = position.get(NIUONE_HOLDING_LIFECYCLE_PATH_FIELD)
    path = raw_path if isinstance(raw_path, list) else []
    if not isinstance(raw_path, list):
        position[NIUONE_HOLDING_LIFECYCLE_PATH_FIELD] = path
    if complete_from_entry and not path:
        position["niuone_holding_lifecycle_complete_from_entry"] = True
    else:
        position.setdefault(
            "niuone_holding_lifecycle_complete_from_entry",
            False,
        )
    if not path:
        path.append(observation)
        return True
    last = path[-1]
    if not isinstance(last, dict):
        position["niuone_holding_lifecycle_complete_from_entry"] = False
        path.append(observation)
        return True
    last_timestamp = _niuone_lifecycle_path_timestamp(
        last.get("last_observed_at")
    )
    last_datetime = _niuone_lifecycle_path_datetime(last_timestamp)
    current_datetime = _niuone_lifecycle_path_datetime(timestamp)
    if (
        last_datetime is not None
        and current_datetime is not None
        and current_datetime < last_datetime
    ):
        return False
    if str(last.get("stage") or "") != stage:
        path.append(observation)
        return True
    if last_datetime == current_datetime:
        return False
    raw_observations = last.get("observations")
    observations = (
        raw_observations if isinstance(raw_observations, list) else []
    )
    if not isinstance(raw_observations, list):
        last["observations"] = observations
    observations.append(observation_detail)
    last["last_observed_at"] = timestamp
    last["observation_count"] = len(observations)
    last["last_mainline_state"] = observation["last_mainline_state"]
    last["last_mainline_score"] = observation["last_mainline_score"]
    return True


def niuone_lifecycle_exit_evidence_from_position(
    position: dict[str, Any],
    *,
    observed_at: str,
) -> dict[str, Any]:
    """Freeze the causal stage path visible at one simulated sell."""
    record_niuone_lifecycle_observation(
        position,
        observed_at=observed_at,
        source="exit_fill",
    )
    raw_path = position.get(NIUONE_HOLDING_LIFECYCLE_PATH_FIELD)
    path = [
        _json_safe_copy(item)
        for item in (raw_path if isinstance(raw_path, list) else [])
        if isinstance(item, dict)
    ]
    stage = str(position.get("niuone_lifecycle_stage") or "").strip()
    definition = NIUONE_LIFECYCLE_STAGES.get(stage) or {}
    sequence = [str(item.get("stage") or "") for item in path]
    return {
        "schema_version": NIUONE_HOLDING_LIFECYCLE_SCHEMA_VERSION,
        "path_complete_from_entry": (
            position.get("niuone_holding_lifecycle_complete_from_entry")
            is True
        ),
        "exit_niuone_lifecycle_stage": stage,
        "exit_niuone_lifecycle_label": str(
            definition.get("label") or ""
        ),
        "exit_niuone_lifecycle_order": definition.get("order"),
        "exit_niuone_lifecycle_entry_policy": str(
            definition.get("entry_policy") or ""
        ),
        "stage_sequence": sequence,
        "transition_count": max(0, len(sequence) - 1),
        "reached_markup": "markup" in sequence,
        "reached_climax": "climax" in sequence,
        "reached_divergence": "divergence" in sequence,
        "reached_fade": "fade" in sequence,
        "path": path,
    }


def niuone_entry_context_from_position(
    position: dict[str, Any],
) -> dict[str, Any]:
    """Project immutable entry-era evidence from a NiuOne position."""
    return {
        key: position[key]
        for key in NIUONE_ENTRY_CONTEXT_FIELDS
        if key in position
    }


def niuone_candidate_selection_context(
    candidate: dict[str, Any],
    candidates: list[dict[str, Any]],
    strategy_id: str,
) -> dict[str, Any]:
    """Describe the selected name's rank inside the current decision pool."""
    if "selection_same_stage_candidate_count" in candidate:
        return {
            "entry_signal_score": candidate.get("selection_signal_score"),
            "entry_candidate_pool_size": candidate.get(
                "selection_candidate_pool_size"
            ),
            "entry_same_stage_candidate_count": candidate.get(
                "selection_same_stage_candidate_count"
            ),
            "entry_same_stage_candidate_rank": candidate.get(
                "selection_same_stage_candidate_rank"
            ),
            "entry_same_stage_top_score_gap": candidate.get(
                "selection_same_stage_top_score_gap"
            ),
        }
    indexed: list[tuple[int, dict[str, Any], float]] = []
    for index, item in enumerate(candidates):
        item_strategy = str(
            item.get("best_strategy")
            or item.get("strategy")
            or item.get("strategy_id")
            or ""
        ).strip()
        if item_strategy != strategy_id:
            continue
        score = _safe_float(
            item.get("best_decision_score")
            if item.get("best_decision_score") is not None
            else item.get("best_score")
            if item.get("best_score") is not None
            else item.get("score"),
            float("nan"),
        )
        if math.isfinite(score):
            indexed.append((index, item, score))
    ranked = sorted(indexed, key=lambda item: (-item[2], item[0]))
    candidate_code = normalize_code(candidate.get("code") or "")
    candidate_rank = next(
        (
            rank
            for rank, (_index, item, _score) in enumerate(ranked, start=1)
            if item is candidate
            or normalize_code(item.get("code") or "") == candidate_code
        ),
        None,
    )
    signal_score = next(
        (
            score
            for _index, item, score in indexed
            if item is candidate
            or normalize_code(item.get("code") or "") == candidate_code
        ),
        None,
    )
    top_gap = (
        round(ranked[0][2] - ranked[1][2], 4)
        if len(ranked) > 1
        else None
    )
    return {
        "entry_signal_score": round(signal_score, 4)
        if signal_score is not None else None,
        "entry_candidate_pool_size": len(candidates),
        "entry_same_stage_candidate_count": len(ranked),
        "entry_same_stage_candidate_rank": candidate_rank,
        "entry_same_stage_top_score_gap": top_gap,
    }


PRACTICE_CANDIDATE_EVIDENCE_FIELDS = (
    "code",
    "name",
    "price",
    "change_pct",
    "amount_yi",
    "turnover",
    "industry",
    "sector",
    "signal_theme",
    "signal_theme_attribution_score",
    "signal_theme_attribution_weight",
    "signal_theme_historical_prior_score",
    "signal_theme_cohort_alignment_score",
    "signal_theme_peer_resonance_score",
    "signal_theme_return_correlation_score",
    "signal_theme_return_correlation_rank_score",
    "signal_theme_return_correlation_observation_count",
    "signal_theme_return_correlation_peer_count",
    "signal_theme_specificity_score",
    "signal_theme_membership_source",
    "unattributed_theme_weight",
    "theme_attribution_confident",
    "theme_attribution_gap",
    "best_strategy",
    "strategy",
    "strategy_id",
    "best_score",
    "best_decision_score",
    "score",
    "entry_threshold",
    "actionable",
    "hard_blockers",
    "risk_flags",
    "return_5d_pct",
    "return_20d_pct",
    "distance_ema20_pct",
    "distance_bbi_pct",
    "distance_high_20d_pct",
    "volume_ratio_5d",
    "volatility_20d_pct",
    "current_j",
    "above_ema20",
    "above_bbi",
    "market_regime",
    "market_allows_buys",
    "market_hard_stop",
    "mainline_state",
    "mainline_score",
    "mainline_confirmed",
    "mainline_cross_day_persistent",
    "niuone_lifecycle_stage",
    "niuone_lifecycle_label",
    "niuone_lifecycle_order",
    "niuone_lifecycle_entry_policy",
    "stock_sector_rank",
    "stock_leader_rank",
    "stock_leader_tier",
    "stock_strong",
    "stock_activity_data_available",
    "stock_market_amount_percentile",
    "stock_theme_amount_percentile",
    "stock_volume_participation_percentile",
    "stock_activity_score",
    "stock_activity_confirmed",
    "daily_v_reversal",
    "daily_v_recovery_ratio",
    "stop_price",
    "stop_source",
    "atr",
    "atr20",
    "recent_close",
    "execution_buffer_pct",
    "selection_signal_score",
    "selection_candidate_pool_size",
    "selection_same_stage_candidate_count",
    "selection_same_stage_candidate_rank",
    "selection_same_stage_top_score_gap",
)


def _practice_candidate_evidence_key(candidate: dict[str, Any]) -> str:
    snapshot = {
        key: candidate.get(key)
        for key in PRACTICE_CANDIDATE_EVIDENCE_FIELDS
        if key in candidate
    }
    snapshot["code"] = normalize_code(candidate.get("code") or "")
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def build_practice_candidate_evidence(
    raw_candidates: list[Any],
    eligible_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Freeze the observed opportunity set without raw prompts or news text."""
    eligible_ids = {id(item) for item in eligible_candidates}
    eligible_keys = [
        _practice_candidate_evidence_key(item)
        for item in eligible_candidates
    ]
    evidence: list[dict[str, Any]] = []
    for observed_rank, raw in enumerate(raw_candidates, start=1):
        if not isinstance(raw, dict):
            continue
        code = normalize_code(raw.get("code") or "")
        strategy_id = str(
            raw.get("best_strategy")
            or raw.get("strategy")
            or raw.get("strategy_id")
            or ""
        ).strip()
        blockers: list[str] = []
        if not candidate_in_stock_universe(raw):
            blockers.append("outside_configured_stock_universe")
        if not candidate_matches_active_strategy(raw):
            blockers.append("outside_active_strategy_suite")
        blockers.extend(candidate_buy_blockers(raw))
        candidate_key = _practice_candidate_evidence_key(raw)
        eligible = id(raw) in eligible_ids
        if not eligible and candidate_key in eligible_keys:
            eligible = True
        if eligible and candidate_key in eligible_keys:
            eligible_keys.remove(candidate_key)
        if not eligible:
            blockers.append("not_selected_for_decision")
        snapshot = {
            key: raw.get(key)
            for key in PRACTICE_CANDIDATE_EVIDENCE_FIELDS
            if key in raw
        }
        snapshot.update({
            "code": code,
            "strategy_id": strategy_id,
            "observed_rank": observed_rank,
            "eligible_for_decision": eligible,
            "eligibility_blockers": blockers,
        })
        if is_niuone_strategy(strategy_id):
            snapshot.update(
                niuone_candidate_selection_context(
                    raw,
                    eligible_candidates,
                    strategy_id,
                )
            )
        evidence.append(snapshot)
    return evidence


def is_dynamic_risk_strategy(strategy_id: str) -> bool:
    return is_sector_tide_strategy(strategy_id) or is_niuone_strategy(strategy_id)


def niuone_candidate_theme(candidate: Mapping[str, Any]) -> str:
    """Return the action-selected concept, with a legacy payload fallback."""
    return str(
        candidate.get("signal_theme")
        or candidate.get("active_theme")
        or candidate.get("entry_theme")
        or candidate.get("industry")
        or candidate.get("sector")
        or ""
    ).strip()


def niuone_position_theme(position: Mapping[str, Any]) -> str:
    """Return the concept currently used for NiuOne risk and lifecycle."""
    return str(
        position.get("active_theme")
        or position.get("entry_theme")
        or position.get("entry_industry")
        or position.get("industry")
        or position.get("sector")
        or ""
    ).strip()


def dynamic_strategy_exposure_key(
    value: Mapping[str, Any],
    strategy_id: str,
) -> str:
    if is_niuone_strategy(strategy_id):
        if "signal_theme" in value:
            return niuone_candidate_theme(value)
        return niuone_position_theme(value)
    return str(value.get("industry") or value.get("sector") or "").strip()


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


def dynamic_strategy_existing_open_risk_pct(
    positions: dict[str, Any],
    total_equity: float,
    *,
    persona: str,
    excluding_code: str = "",
    industry: str | None = None,
) -> float:
    """Sum stressed stop risk for one dynamic strategy suite only."""
    total = 0.0
    normalized_exclusion = normalize_code(excluding_code)
    for position_code, pos in positions.items():
        if not isinstance(pos, dict) or position_qty(pos) <= 0:
            continue
        if normalize_code(position_code) == normalized_exclusion:
            continue
        strategy_id = position_entry_strategy(pos)
        if STRATEGY_DEFINITIONS.get(strategy_id, {}).get("persona") != persona:
            continue
        if (
            industry is not None
            and dynamic_strategy_exposure_key(pos, strategy_id) != industry
        ):
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


def sync_niuone_position_context(state: dict[str, Any], b1_payload: dict[str, Any] | None) -> int:
    """Persist the latest 牛牛战法 market/mainline state on its open positions."""
    payload = b1_payload if isinstance(b1_payload, dict) else {}
    context = payload.get("niuone_context") if isinstance(payload.get("niuone_context"), dict) else {}
    market = context.get("market") if isinstance(context.get("market"), dict) else {}
    themes = context.get("themes") if isinstance(context.get("themes"), dict) else {}
    stocks = context.get("stocks") if isinstance(context.get("stocks"), dict) else {}
    if not context or not market:
        return 0

    candidates: dict[str, dict[str, Any]] = {}
    for key in ("trade_items", "items", "candidates"):
        for item in payload.get(key) or []:
            if isinstance(item, dict):
                code = normalize_code(item.get("code") or "")
                if code:
                    candidates[code] = item
    generated_at = str(payload.get("generated_at") or now_ts())
    context_date = generated_at[:10] if len(generated_at) >= 10 else today_key()
    previous_trading_day = str(context.get("previous_trading_day") or "")[:10]
    updated = 0
    for code, pos in (state.get("positions") or {}).items():
        entry_strategy = position_entry_strategy(pos) if isinstance(pos, dict) else ""
        if not isinstance(pos, dict) or not is_niuone_strategy(entry_strategy):
            continue
        regime = str(
            market.get("risk_state")
            or market.get("state")
            or ""
        )
        budget = niuone_risk_budget(regime, entry_strategy)
        normalized_code = normalize_code(code)
        candidate = candidates.get(normalized_code, {})
        stock = stocks.get(normalized_code) if isinstance(stocks.get(normalized_code), dict) else {}
        theme_profiles = {
            str(item.get("industry") or "").strip(): dict(item)
            for item in (stock.get("theme_profiles") or [])
            if isinstance(item, Mapping)
            and str(item.get("industry") or "").strip()
        }
        theme_attributions = {
            str(item.get("theme") or item.get("industry") or "").strip(): dict(item)
            for item in (stock.get("theme_attributions") or [])
            if isinstance(item, Mapping)
            and str(item.get("theme") or item.get("industry") or "").strip()
        }
        entry_theme = str(pos.get("entry_theme") or "").strip()
        if not entry_theme:
            for legacy_theme in (
                pos.get("active_theme"),
                pos.get("entry_industry"),
                niuone_candidate_theme(candidate),
                stock.get("industry"),
                pos.get("industry"),
                pos.get("sector"),
            ):
                normalized_theme = str(legacy_theme or "").strip()
                if normalized_theme and isinstance(themes.get(normalized_theme), dict):
                    entry_theme = normalized_theme
                    pos["entry_theme"] = normalized_theme
                    break
        active_theme = str(pos.get("active_theme") or entry_theme).strip()
        if active_theme:
            pos["active_theme"] = active_theme

        ranked_attributions = sorted(
            theme_attributions.values(),
            key=lambda item: (
                -_safe_float(item.get("attribution_score"), 0.0),
                str(item.get("theme") or item.get("industry") or ""),
            ),
        )
        leading_attribution = ranked_attributions[0] if ranked_attributions else {}
        leading_theme = str(
            leading_attribution.get("theme")
            or leading_attribution.get("industry")
            or ""
        ).strip()
        active_attribution_score = _safe_float(
            (theme_attributions.get(active_theme) or {}).get(
                "attribution_score"
            ),
            0.0,
        )
        leading_attribution_score = _safe_float(
            leading_attribution.get("attribution_score"),
            0.0,
        )
        leading_theme_state = str(
            (themes.get(leading_theme) or {}).get("state") or ""
        )
        switch_supported = bool(
            active_theme
            and leading_theme
            and leading_theme != active_theme
            and leading_theme_state in {"emerging", "mainline", "diverging"}
            and leading_attribution_score
            >= active_attribution_score
            + NIUONE_THEME_SWITCH_MIN_ATTRIBUTION_GAP
        )
        if switch_supported:
            last_switch_date = str(
                pos.get("pending_theme_switch_last_date") or ""
            )[:10]
            if last_switch_date != context_date:
                consecutive = bool(
                    previous_trading_day
                    and last_switch_date == previous_trading_day
                    and pos.get("pending_theme_switch") == leading_theme
                )
                pos["pending_theme_switch_count"] = (
                    int(pos.get("pending_theme_switch_count") or 0) + 1
                    if consecutive
                    else 1
                )
                pos["pending_theme_switch"] = leading_theme
                pos["pending_theme_switch_last_date"] = context_date
            if int(pos.get("pending_theme_switch_count") or 0) >= NIUONE_THEME_SWITCH_CONFIRMATIONS:
                prior_active_theme = active_theme
                active_theme = leading_theme
                pos["active_theme"] = active_theme
                switch_history = pos.get("theme_switch_history")
                if not isinstance(switch_history, list):
                    switch_history = []
                switch_history.append({
                    "from_theme": prior_active_theme,
                    "to_theme": active_theme,
                    "confirmed_at": generated_at,
                    "from_attribution_score": round(
                        active_attribution_score,
                        2,
                    ),
                    "to_attribution_score": round(
                        leading_attribution_score,
                        2,
                    ),
                })
                pos["theme_switch_history"] = switch_history[-20:]
                pos.pop("pending_theme_switch", None)
                pos.pop("pending_theme_switch_count", None)
                pos.pop("pending_theme_switch_last_date", None)
        else:
            pos.pop("pending_theme_switch", None)
            pos.pop("pending_theme_switch_count", None)
            pos.pop("pending_theme_switch_last_date", None)

        theme = themes.get(active_theme) if isinstance(themes.get(active_theme), dict) else {}
        if not active_theme or not theme:
            continue
        if candidate.get("signal_theme"):
            factual_industry = str(
                candidate.get("industry")
                or candidate.get("sector")
                or stock.get("classification_industry")
                or pos.get("industry")
                or pos.get("sector")
                or ""
            ).strip()
            if factual_industry:
                pos["industry"] = factual_industry
                pos["sector"] = factual_industry
        active_profile = theme_profiles.get(active_theme) or {}
        stock_for_theme = dict(stock)
        stock_for_theme.update(active_profile)
        pos["active_theme_attribution_score"] = (
            (theme_attributions.get(active_theme) or {}).get(
                "attribution_score"
            )
        )
        pos["active_theme_attribution_weight"] = (
            (theme_attributions.get(active_theme) or {}).get(
                "attribution_weight"
            )
        )
        pos["entry_theme_score"] = (
            (themes.get(entry_theme) or {}).get("score")
            if entry_theme else None
        )
        pos["entry_theme_state"] = (
            (themes.get(entry_theme) or {}).get("state")
            if entry_theme else ""
        )
        score = _safe_float(theme.get("score"), -1.0)
        state_name = str(theme.get("state") or "")
        weak = score < 55 or state_name in {"fading", "inactive"}
        if weak:
            if pos.get("mainline_weak_last_date") != context_date:
                pos["mainline_weak_count"] = int(pos.get("mainline_weak_count") or 0) + 1
                pos["mainline_weak_last_date"] = context_date
        else:
            pos["mainline_weak_count"] = 0
            pos.pop("mainline_weak_last_date", None)
        reversal_probe = entry_strategy == "niu_reversal_probe"
        stock_role = str(
            stock_for_theme.get("role") or candidate.get("stock_role") or ""
        ).strip()
        stock_strong = stock_for_theme.get("strong")
        if stock_strong is None:
            if "stock_strong" in candidate:
                stock_strong = bool(candidate.get("stock_strong"))
        stock_leader_tier = stock_for_theme.get("leader_tier")
        if stock_leader_tier is None:
            if "stock_leader_tier" in candidate:
                stock_leader_tier = bool(candidate.get("stock_leader_tier"))
        stock_leader_rank = stock_for_theme.get(
            "leader_rank",
            candidate.get("stock_leader_rank"),
        )
        leader_status_observed = stock_leader_tier is not None
        if leader_status_observed and not reversal_probe:
            is_current_leader = stock_leader_tier is True and stock_strong is not False
            if is_current_leader:
                pos["niu_leader_lost_count"] = 0
                pos.pop("niu_leader_lost_last_date", None)
            elif pos.get("niu_leader_lost_last_date") != context_date:
                last_lost_date = str(pos.get("niu_leader_lost_last_date") or "")[:10]
                consecutive = bool(previous_trading_day and last_lost_date == previous_trading_day)
                pos["niu_leader_lost_count"] = (
                    int(pos.get("niu_leader_lost_count") or 0) + 1
                    if consecutive
                    else 1
                )
                pos["niu_leader_lost_last_date"] = context_date
            pos["stock_role"] = stock_role or "unknown"
            pos["stock_leader_tier"] = bool(stock_leader_tier)
            pos["stock_leader_rank"] = stock_leader_rank
            if stock_strong is not None:
                pos["stock_strong"] = bool(stock_strong)
        elif leader_status_observed:
            pos["stock_role"] = stock_role or "today_core"
            pos["stock_leader_tier"] = bool(stock_leader_tier)
            pos["stock_leader_rank"] = stock_leader_rank
            pos["stock_strong"] = bool(stock_strong)
        pos["mainline_score"] = theme.get("score")
        pos["mainline_state"] = state_name
        if score >= 0:
            previous_peak_score = _safe_float(
                pos.get("mainline_peak_score"),
                score,
            )
            mainline_peak_score = max(previous_peak_score, score)
            pos["mainline_peak_score"] = round(mainline_peak_score, 3)
            pos["mainline_peak_drawdown_points"] = round(
                max(0.0, mainline_peak_score - score),
                3,
            )
        pos["mainline_raw_state"] = theme.get("raw_state")
        pos["mainline_confirmation_count"] = theme.get("confirmation_count")
        pos["mainline_cross_day_persistent"] = bool(theme.get("cross_day_persistent"))
        pos["mainline_confirmed"] = bool(theme.get("mainline_confirmed"))
        for key in (
            "niuone_lifecycle_stage",
            "niuone_lifecycle_label",
            "niuone_lifecycle_order",
            "niuone_lifecycle_entry_policy",
        ):
            if key in theme:
                pos[key] = theme[key]
        record_niuone_lifecycle_observation(
            pos,
            observed_at=generated_at,
            source="mainline_scan",
        )
        if reversal_probe:
            for key in (
                "reversal_basis", "daily_v_reversal", "daily_v_left_peak_date",
                "daily_v_trough_date", "daily_v_left_days", "daily_v_right_days",
                "daily_v_decline_pct", "daily_v_rebound_pct", "daily_v_recovery_ratio",
                "daily_v_rising_ratio", "daily_v_pattern_score",
            ):
                if candidate.get(key) is not None:
                    pos[key] = candidate.get(key)
        pos["today_breadth_pct"] = theme.get("today_breadth_pct")
        pos["effective_strong_count"] = theme.get("effective_strong_count")
        pos["leader_concentration"] = theme.get("leader_concentration")
        if not str(pos.get("entry_market_regime") or "").strip():
            # Migrate pre-policy positions once from the best entry-era context
            # still available, before current market state is refreshed below.
            pos["entry_market_regime"] = str(
                pos.get("risk_budget_regime")
                or pos.get("market_regime")
                or regime
                or ""
            )
        pos["market_regime"] = regime
        pos["risk_budget_regime"] = regime
        pos["per_trade_risk_budget_pct"] = budget["per_trade_risk_pct"]
        pos["max_open_risk_pct"] = budget["max_open_risk_pct"]
        pos["max_sector_risk_pct"] = budget["max_sector_risk_pct"]
        pos["max_total_position_pct"] = budget["max_total_position_pct"]
        pos["max_sector_position_pct"] = budget["max_sector_position_pct"]
        pos["market_tide_score"] = market.get("score")
        pos["market_hard_stop"] = bool(market.get("hard_stop"))
        pos["market_allows_buys"] = bool(market.get("allow_new_buys"))
        pos["stock_sector_rank"] = candidate.get("stock_sector_rank", stock.get("theme_rank"))
        pos["mainline_context_at"] = generated_at
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
        mark.get("strategy_id")
        or mark.get("entry_strategy_id")
        or pos.get("strategy_mark_id")
        or pos.get("buy_strategy")
        or ""
    )


def _load_stock_industry_cache() -> dict[str, str]:
    try:
        payload = json.loads(STOCK_INDUSTRY_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        normalize_code(code): str(industry or "").strip()
        for code, industry in payload.items()
        if normalize_code(code) and str(industry or "").strip()
    }


def sync_zettaranc_position_context(state: dict[str, Any], b1_payload: dict[str, Any] | None) -> int:
    """Attach the shared industry-flow snapshot to existing Zettaranc positions."""
    payload = b1_payload if isinstance(b1_payload, dict) else {}
    zettaranc_context = (
        payload.get("zettaranc_context")
        if isinstance(payload.get("zettaranc_context"), dict)
        else {}
    )
    tide_context = (
        payload.get("sector_tide_context")
        if isinstance(payload.get("sector_tide_context"), dict)
        else {}
    )
    niuone_context = (
        payload.get("niuone_context")
        if isinstance(payload.get("niuone_context"), dict)
        else {}
    )
    flow_payload = zettaranc_context.get("industry_money_flow")
    if not isinstance(flow_payload, dict):
        flow_payload = tide_context.get("industry_money_flow")
    if not isinstance(flow_payload, dict):
        flow_payload = niuone_context.get("industry_money_flow")
    if not isinstance(flow_payload, dict):
        return 0

    candidates: dict[str, dict[str, Any]] = {}
    for key in ("trade_items", "items", "candidates"):
        for item in payload.get(key) or []:
            if not isinstance(item, dict):
                continue
            code = normalize_code(item.get("code") or "")
            if code:
                candidates[code] = item
    industry_cache = _load_stock_industry_cache()
    updated = 0
    for code, pos in (state.get("positions") or {}).items():
        if not isinstance(pos, dict) or not is_zettaranc_strategy(position_entry_strategy(pos)):
            continue
        normalized_code = normalize_code(code)
        candidate = candidates.get(normalized_code, {})
        industry = str(
            candidate.get("industry")
            or candidate.get("sector")
            or pos.get("industry")
            or pos.get("sector")
            or industry_cache.get(normalized_code)
            or ""
        ).strip()
        if industry:
            pos["industry"] = industry
            pos["sector"] = industry
        signal = zettaranc_industry_flow_signal(
            [{"industry": industry}],
            {"industry_money_flow": flow_payload},
        )
        previous_generated_at = str(pos.get("industry_flow_generated_at") or "")
        generated_at = str(signal.get("industry_flow_generated_at") or "")
        if generated_at and generated_at != previous_generated_at:
            pos["industry_flow_previous_direction"] = str(
                pos.get("industry_flow_direction") or "neutral"
            )
            pos["industry_flow_previous_rank"] = (
                pos.get("industry_flow_rank")
                if pos.get("industry_flow_direction") == "inflow"
                else pos.get("industry_outflow_rank")
            )
        for key in (
            "industry_flow_available",
            "industry_flow_matched",
            "industry_flow_direction",
            "industry_flow_rank",
            "industry_flow_rank_total",
            "industry_flow_net_yi",
            "industry_outflow_matched",
            "industry_outflow_rank",
            "industry_outflow_rank_total",
            "industry_outflow_net_yi",
            "industry_flow_source",
            "industry_flow_generated_at",
        ):
            pos[key] = signal.get(key)
        updated += 1
    return updated


def load_latest_market_volume_context(now: datetime | None = None) -> dict[str, Any]:
    """Read the latest validated market-turnover prediction without new I/O calls."""
    current = now or datetime.now()
    try:
        payload = json.loads(MARKET_BREADTH_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    samples = [item for item in (payload.get("samples") or []) if isinstance(item, dict)]
    for sample in reversed(samples):
        generated_at = str(sample.get("generated_at") or "")
        if generated_at[:10] != current.strftime("%Y-%m-%d"):
            continue
        try:
            generated = datetime.strptime(generated_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if current.time() <= dtime(15, 0) and not (-120 <= (current - generated).total_seconds() <= 15 * 60):
            continue
        actual = _safe_float(sample.get("actual_turnover_yi"), 0.0)
        estimated = _safe_float(sample.get("estimated_turnover_yi"), 0.0)
        previous = _safe_float(sample.get("previous_turnover_yi"), 0.0)
        if actual <= 0 or estimated <= 0:
            continue
        return {
            "generated_at": generated_at,
            "actual_turnover_yi": actual,
            "estimated_turnover_yi": estimated,
            "previous_turnover_yi": previous if previous > 0 else None,
            "turnover_estimate_model": str(sample.get("turnover_estimate_model") or ""),
            "source": str(sample.get("turnover_estimate_source") or sample.get("source") or ""),
        }
    return {}


def update_zettaranc_volume_context(state: dict[str, Any], now: datetime | None = None) -> int:
    """Project position volume with the existing market same-time turnover model."""
    market = load_latest_market_volume_context(now)
    actual = _safe_float(market.get("actual_turnover_yi"), 0.0)
    estimated = _safe_float(market.get("estimated_turnover_yi"), 0.0)
    previous = _safe_float(market.get("previous_turnover_yi"), 0.0)
    progress_fraction = actual / estimated if actual > 0 and estimated > 0 else 0.0
    market_ratio = estimated / previous if estimated > 0 and previous > 0 else 0.0
    updated = 0
    for pos in (state.get("positions") or {}).values():
        if not isinstance(pos, dict) or not is_zettaranc_strategy(position_entry_strategy(pos)):
            continue
        current_volume = _safe_float(pos.get("volume_lots"), 0.0)
        median_volume = _safe_float(pos.get("median_volume_20"), 0.0)
        projected_ratio = 0.0
        if current_volume > 0 and median_volume > 0 and 0 < progress_fraction <= 1.05:
            projected_ratio = current_volume / progress_fraction / median_volume
        change_pct = _safe_float(pos.get("change_pct"), 0.0)
        volume_price_signal = "neutral"
        if projected_ratio > 0:
            if change_pct <= -0.5 and projected_ratio >= 1.2:
                volume_price_signal = "bearish"
            elif change_pct <= 0 and projected_ratio <= 0.9:
                volume_price_signal = "supportive"
            elif change_pct >= 0 and projected_ratio >= 1.1:
                volume_price_signal = "supportive"
        pos["market_turnover_estimated_yi"] = estimated or None
        pos["market_turnover_previous_yi"] = previous or None
        pos["market_turnover_ratio"] = round(market_ratio, 3) if market_ratio > 0 else None
        pos["market_volume_generated_at"] = str(market.get("generated_at") or "")
        pos["market_volume_source"] = str(market.get("source") or "")
        pos["projected_volume_ratio_20d"] = round(projected_ratio, 3) if projected_ratio > 0 else None
        pos["shaofu_volume_price_signal"] = volume_price_signal
        updated += 1
    return updated


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


def _clear_shaofu_soft_exit_pending(pos: dict[str, Any], status: str = "clear") -> None:
    pos["shaofu_soft_exit_status"] = status
    for key in (
        "shaofu_soft_exit_signal",
        "shaofu_soft_exit_reason",
        "shaofu_soft_exit_count",
        "shaofu_soft_exit_required",
        "shaofu_soft_exit_last_check",
    ):
        pos.pop(key, None)


def _resolve_shaofu_soft_exit(
    pos: dict[str, Any],
    candidate: dict[str, Any],
    *,
    hold_trading_days: int,
    soft_exit_allowed: bool,
    confirmation_key: str,
) -> dict[str, Any] | None:
    direction = str(pos.get("industry_flow_direction") or "neutral")
    volume_signal = str(pos.get("shaofu_volume_price_signal") or "neutral")
    decision = evaluate_shaofu_soft_exit(
        hold_trading_days=hold_trading_days,
        soft_exit_allowed=soft_exit_allowed,
        confirmation_key=confirmation_key,
        previous_key=str(pos.get("shaofu_soft_exit_last_check") or ""),
        previous_count=int(pos.get("shaofu_soft_exit_count") or 0),
        sector_flow_direction=direction,
        volume_price_signal=volume_signal,
        already_reduced=bool(pos.get("shaofu_soft_exit_reduced") or pos.get("partial_tp_done")),
        min_hold_trading_days=SHAOFU_MIN_HOLD_TRADING_DAYS,
        confirmations_required=SHAOFU_SOFT_EXIT_CONFIRMATIONS,
    )
    status = str(decision.get("status") or "pending")
    pos["shaofu_soft_exit_status"] = status
    pos["shaofu_soft_exit_signal"] = str(candidate.get("signal") or "")
    pos["shaofu_soft_exit_reason"] = str(candidate.get("reason") or "")
    pos["shaofu_soft_exit_count"] = int(decision.get("count") or 0)
    pos["shaofu_soft_exit_required"] = int(
        decision.get("required") or SHAOFU_SOFT_EXIT_CONFIRMATIONS
    )
    if confirmation_key:
        pos["shaofu_soft_exit_last_check"] = confirmation_key
    if status in {"min_hold", "morning_hold", "context_hold", "runner_hold"}:
        pos["shaofu_soft_exit_count"] = 0
    if not decision.get("allow_reduce"):
        return None

    context_parts: list[str] = []
    if direction == "outflow":
        rank = pos.get("industry_outflow_rank")
        context_parts.append(f"行业主力净流出{f'第{rank}名' if rank else ''}")
    elif direction == "inflow":
        rank = pos.get("industry_flow_rank")
        context_parts.append(f"行业主力净流入{f'第{rank}名' if rank else ''}")
    projected_ratio = pos.get("projected_volume_ratio_20d")
    if isinstance(projected_ratio, (int, float)):
        context_parts.append(f"预测量比{float(projected_ratio):.2f}")
    context_parts.append(
        f"软信号确认{pos['shaofu_soft_exit_count']}/{pos['shaofu_soft_exit_required']}"
    )
    reason = (
        f"少妇B1软退出确认，先减半保留趋势仓 ({candidate.get('reason') or '-'}；"
        + "；".join(context_parts)
        + ")"
    )
    signal = _sell_signal(reason, "shaofu_soft_reduce", TAKE_PROFIT_PARTIAL_RATIO)
    signal["source_signal"] = str(candidate.get("signal") or "")
    return signal


def evaluate_sell_signal(
    code: str,
    pos: dict[str, Any],
    today: str | None = None,
    *,
    time_exit_allowed: bool = True,
    b3_exit_allowed: bool | None = None,
    time_stop_allowed: bool | None = None,
    soft_exit_allowed: bool = True,
    soft_exit_confirmation_key: str = "",
) -> dict[str, Any] | None:
    """Evaluate the local sell rule stack for one open position.

    The rule stack combines fixed risk control, S1/B1-style failed confirmation,
    volatility/trailing exits, and time-based exits. It mutates lightweight per-position
    tracking fields such as peak price and consecutive BBI-break days.
    """
    today = today or today_key()
    entry_strategy = position_entry_strategy(pos)
    zettaranc_position = is_zettaranc_strategy(entry_strategy)
    shaofu_position = entry_strategy == "shaofu_b1"
    sector_tide_position = is_sector_tide_strategy(entry_strategy)
    niuone_position = is_niuone_strategy(entry_strategy)
    realtime_price = float(pos.get("last_price") or pos.get("close") or pos.get("avg_cost") or 0)
    price = float(
        (pos.get("confirmed_close") if zettaranc_position else pos.get("close"))
        or pos.get("close")
        or realtime_price
        or pos.get("avg_cost")
        or 0
    )
    niuone_execution_price = (
        realtime_price if niuone_position and realtime_price > 0 else price
    )
    avg_cost = float(pos.get("avg_cost") or 0)
    if price <= 0 or avg_cost <= 0:
        return None
    if entry_strategy == STRATEGY_SOURCE_PRESET_TEXT:
        if pos.get("prompt_strategy_version_id"):
            evaluation = pos.get("prompt_strategy_exit_evaluation")
            if (
                pos.get("prompt_strategy_exit_status") == "true"
                and isinstance(evaluation, Mapping)
                and str(evaluation.get("plan_sha256") or "")
                == str(pos.get("prompt_strategy_plan_sha256") or "")
            ):
                evidence = str(
                    (evaluation.get("root") or {}).get("evidence")
                    or "冻结文字策略退出条件成立"
                )
                return _sell_signal(
                    f"冻结文字策略退出：{evidence}",
                    "prompt_strategy_exit",
                )
            return None
        # Legacy prompt positions retain their older model-audited exit path.
        return None
    if time_stop_allowed is not None:
        time_exit_allowed = time_stop_allowed
    if b3_exit_allowed is None:
        b3_exit_allowed = time_exit_allowed

    pnl_pct = (price / avg_cost - 1) * 100
    realtime_pnl_pct = (realtime_price / avg_cost - 1) * 100
    performance_price = niuone_execution_price if niuone_position else price
    performance_pnl_pct = (performance_price / avg_cost - 1) * 100
    prior_high = float(pos.get("highest_price") or performance_price)
    highest_price = max(prior_high, performance_price)
    pos["highest_price"] = round(highest_price, 3)
    prior_max = pos.get("max_pnl_pct")
    try:
        max_pnl_pct = (
            max(float(prior_max), performance_pnl_pct)
            if prior_max is not None else performance_pnl_pct
        )
    except Exception:
        max_pnl_pct = performance_pnl_pct
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
    hold_trading_days = (
        trading_holding_days(pos, today)
        if shaofu_position or sector_tide_position or niuone_position
        else hold_days
    )
    soft_exit_confirmation_key = soft_exit_confirmation_key or today
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
    if (
        niuone_position
        and NIUONE_BREAK_EVEN_AFTER_PARTIAL
        and pos.get("partial_tp_done")
    ):
        shaofu_stop = max(shaofu_stop, avg_cost)
        pos["entry_stop_price"] = round(shaofu_stop, 3)
        pos["entry_stop_source"] = "niu_breakeven"
    if shaofu_stop > 0 and price < shaofu_stop:
        stop_labels = {
            "n_structure_low": "N型结构前低",
            "b1_low": "前置B1低点",
            "b3_kline_low": "B3当日低点",
            "b2_midpoint": "B2大阳线中位",
            "super_b1_washout_low": "超级B1洗盘阴线低点",
            "tide_structure_low": "板块潮汐结构低点",
            "niu_structure_low": "牛牛战法结构低点",
            "niu_breakout_pivot": "牛牛突破位",
            "niu_reversal_low": "牛牛试仓V型低点",
            "niu_reversal_right_low": "牛牛试仓右侧确认低点",
            "niu_breakeven": "牛牛首段止盈后成本保护线",
        }
        stop_source = str(pos.get("shaofu_stop_source") or pos.get("entry_stop_source") or "")
        stop_label = stop_labels.get(stop_source, "入场止损")
        stop_signal = (
            "tide_structure_stop" if sector_tide_position
            else "niu_structure_stop" if niuone_position
            else "shaofu_entry_stop"
        )
        return _sell_signal(f"收盘价破{stop_label} (收盘{price:.2f} < 止损{shaofu_stop:.2f})", stop_signal)

    climax_runner_active = False
    if sector_tide_position or niuone_position:
        if niuone_position:
            reversal_probe = entry_strategy == "niu_reversal_probe"
            theme_score = _safe_float(pos.get("mainline_score"), 100.0)
            theme_state = str(pos.get("mainline_state") or "")
            climax_runner_active = niuone_climax_runner_active(
                enabled=NIUONE_CLIMAX_RUNNER_ENABLED,
                climax_partial_done=bool(
                    pos.get("niuone_lifecycle_climax_partial_done")
                ),
                partial_tp_done=bool(pos.get("partial_tp_done")),
                stock_strong=pos.get("stock_strong") is True,
                theme_score=theme_score,
                theme_state=theme_state,
            )
            leader_loss_confirmations = (
                NIUONE_CLIMAX_RUNNER_LEADER_LOSS_CONFIRMATIONS
                if climax_runner_active
                else NIUONE_LEADER_LOSS_CONFIRMATIONS
            )
            if pos.get("market_hard_stop") and (theme_score < 55 or theme_state in {"fading", "inactive"}):
                return _sell_signal(
                    f"市场硬停止且主线转弱 ({pos.get('industry') or '-'}分数{theme_score:.1f}，状态{theme_state or '-'})",
                    "niu_market_hard_stop",
                )
            if not reversal_probe and int(
                pos.get("niu_leader_lost_count") or 0
            ) >= leader_loss_confirmations:
                return _sell_signal(
                    f"连续{leader_loss_confirmations}个交易日跌出强势行业龙头梯队 "
                    f"({pos.get('industry') or '-'}，当前排名"
                    f"{pos.get('stock_leader_rank') or '-'}"
                    f"{'，高潮减仓后余仓' if climax_runner_active else ''})",
                    "niu_leader_lost",
                )
            if not reversal_probe and (
                int(pos.get("mainline_weak_count") or 0)
                >= NIUONE_MAINLINE_WEAK_CONFIRMATIONS
                or theme_state == "inactive"
            ):
                return _sell_signal(
                    f"主线连续转弱 ({pos.get('industry') or '-'}分数{theme_score:.1f}，状态{theme_state or '-'})",
                    "niu_mainline_faded",
                )
            if reversal_probe and (
                int(pos.get("mainline_weak_count") or 0)
                >= NIUONE_REVERSAL_MAINLINE_WEAK_CONFIRMATIONS
                or theme_state == "inactive"
            ):
                return _sell_signal(
                    "牛牛试仓所属题材未能维持主线酝酿强度 "
                    f"({pos.get('industry') or '-'}分数{theme_score:.1f}，"
                    f"状态{theme_state or '-'})",
                    "niu_reversal_theme_failed",
                )
            if (
                time_exit_allowed
                and (
                    entry_strategy == "niu_leader"
                    or pos.get("niuone_markup_rebalance_reduced") is True
                )
            ):
                rebalance_atr = _safe_float(
                    pos.get("atr20") or pos.get("entry_atr20"),
                    0.0,
                )
                rebalance = niuone_markup_rebalance_observation(
                    pos,
                    current_price=price,
                    atr=rebalance_atr,
                    session_key=today,
                    lifecycle_stage=str(
                        pos.get("niuone_lifecycle_stage") or ""
                    ),
                    current_pnl_pct=pnl_pct,
                    strong_leader=bool(
                        pos.get("stock_leader_tier") is True
                        and pos.get("stock_strong") is True
                    ),
                    pullback_atr=NIUONE_MARKUP_REBALANCE_PULLBACK_ATR,
                    stall_sessions=NIUONE_MARKUP_REBALANCE_STALL_SESSIONS,
                    stall_min_atr=NIUONE_MARKUP_REBALANCE_STALL_MIN_ATR,
                    minimum_sessions_after_add=(
                        NIUONE_MARKUP_REBALANCE_MIN_SESSIONS_AFTER_ADD
                    ),
                )
                pos.update(dict(rebalance.get("state") or {}))
                if rebalance.get("arm_existing_reduction") is True:
                    pos.update({
                        "niuone_markup_rebalance_armed": True,
                        "niuone_markup_rebalance_armed_date": today,
                        "niuone_markup_rebalance_reentry_price": round(
                            price
                            + NIUONE_MARKUP_REBALANCE_REBOUND_ATR
                            * rebalance_atr,
                            3,
                        ),
                        "niuone_markup_rebalance_last_trigger": str(
                            rebalance.get("trigger") or ""
                        ),
                        "niuone_markup_rebalance_arm_count": (
                            int(
                                pos.get(
                                    "niuone_markup_rebalance_arm_count"
                                ) or 0
                            ) + 1
                        ),
                    })
                if rebalance.get("trim") is True:
                    trigger_label = (
                        "回落"
                        if rebalance.get("trigger") == "pullback"
                        else "横盘"
                    )
                    return _sell_signal(
                        f"牛牛主升{trigger_label}释放波段仓位 "
                        f"(距周期高点{rebalance.get('drawdown_atr', 0):g}ATR，"
                        f"横盘计数{rebalance.get('stall_count', 0)})",
                        "niu_markup_rebalance_partial",
                        NIUONE_MARKUP_REBALANCE_TRIM_RATIO,
                    )
            if (
                str(pos.get("niuone_lifecycle_stage") or "") == "climax"
                and pnl_pct + 1e-9 >= NIUONE_LIFECYCLE_CLIMAX_MIN_PNL_PCT
                and not pos.get("niuone_lifecycle_climax_partial_done")
            ):
                return _sell_signal(
                    "牛牛主线进入高潮阶段，先减仓1/3锁定利润 "
                    f"(现盈亏{pnl_pct:.1f}%)",
                    "niu_lifecycle_climax_partial",
                    NIUONE_LIFECYCLE_CLIMAX_PARTIAL_RATIO,
                )
        else:
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
            hold_days=hold_trading_days,
            max_pnl_pct=max_pnl_pct,
            pnl_pct=pnl_pct,
            time_exit_allowed=time_exit_allowed,
            b3_exit_allowed=False,
            b3_exit_hhmm=B3_EXIT_HHMM,
            time_exit_hhmm=TIME_EXIT_HHMM,
            no_progress_hold_days=NO_PROGRESS_HOLD_DAYS,
            no_progress_max_pnl_pct=NO_PROGRESS_MAX_PNL_PCT,
            strategy_confirmation_met=bool(
                pos.get("mainline_cross_day_persistent")
                or pos.get("mainline_confirmed")
            ),
            strategy_variant=str(pos.get("reversal_basis") or ""),
        )
        if strategy_time_exit:
            return strategy_time_exit

        entry_stop = _safe_float(pos.get("entry_stop_price"), 0.0)
        initial_risk = avg_cost - entry_stop if 0 < entry_stop < avg_cost else 0.0
        if niuone_position:
            target_r, partial_take_profit_ratio = resolve_niuone_partial_take_profit(
                strategy_id=entry_strategy,
                entry_market_regime=str(
                    pos.get("entry_market_regime")
                    or pos.get("market_regime")
                    or ""
                ),
                default_r=NIUONE_PARTIAL_TAKE_PROFIT_R,
                default_ratio=NIUONE_PARTIAL_TAKE_PROFIT_RATIO,
                reversal_early_regimes=NIUONE_REVERSAL_EARLY_PROTECTION_REGIMES,
                reversal_early_r=NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_R,
                reversal_early_ratio=(
                    NIUONE_REVERSAL_EARLY_PARTIAL_TAKE_PROFIT_RATIO
                ),
            )
        else:
            target_r = 2.0
            partial_take_profit_ratio = TAKE_PROFIT_PARTIAL_RATIO
        partial_target_price = (
            avg_cost + target_r * initial_risk if initial_risk > 0 else 0.0
        )
        if partial_target_price > 0:
            target_key = "niu_partial_target_price" if niuone_position else "two_r_price"
            pos[target_key] = round(partial_target_price, 3)
            if (
                niuone_execution_price >= partial_target_price
                and not pos.get("partial_tp_done")
            ):
                target_label = f"{target_r:g}R"
                return _sell_signal(
                    f"{'牛牛战法' if niuone_position else '板块潮汐'}达到{target_label}"
                    f"先减仓{partial_take_profit_ratio * 100:g}% "
                    f"(现价{niuone_execution_price:.2f} ≥ {target_label}目标{partial_target_price:.2f})",
                    "niu_r_partial" if niuone_position else "tide_2r_partial",
                    partial_take_profit_ratio,
                )

        atr20 = _safe_float(pos.get("atr20") or pos.get("entry_atr20"), 0.0)
        if atr20 > 0 and (
            pos.get("partial_tp_done")
            or (
                partial_target_price > 0
                and highest_price >= partial_target_price
            )
        ):
            trailing_atr = (
                NIUONE_CLIMAX_RUNNER_TRAILING_ATR
                if niuone_position and climax_runner_active
                else 2.0
            )
            dynamic_trailing_stop = highest_price - trailing_atr * atr20
            pos["niu_trailing_stop" if niuone_position else "tide_trailing_stop"] = round(dynamic_trailing_stop, 3)
            if (
                dynamic_trailing_stop > avg_cost
                and niuone_execution_price <= dynamic_trailing_stop
            ):
                return _sell_signal(
                    f"{'牛牛战法' if niuone_position else '板块潮汐'}"
                    f"{trailing_atr:g}ATR跟踪退出 "
                    f"(现价{niuone_execution_price:.2f} ≤ 跟踪线{dynamic_trailing_stop:.2f})",
                    "niu_atr_trail" if niuone_position else "tide_atr_trail",
                )
        max_hold_days = (
            NIUONE_MAX_HOLD_CALENDAR_DAYS
            if niuone_position else MAX_HOLD_DAYS
        )
        if hold_days >= max_hold_days:
            return _sell_signal(
                f"持仓到期 ({hold_days}d ≥ {max_hold_days}d)",
                "max_hold_days",
            )
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
            signal = _sell_signal(
                f"S1反抽失败 (重新站上BBI后又跌至{bbi_dist:.1f}%，退出等待新买点)",
                "s1_reclaim_failed",
            )
            return _resolve_shaofu_soft_exit(
                pos,
                signal,
                hold_trading_days=hold_trading_days,
                soft_exit_allowed=soft_exit_allowed,
                confirmation_key=soft_exit_confirmation_key,
            ) if shaofu_position else signal
        if int(pos.get("bbi_break_days") or 0) >= S1_FAIL_CONFIRM_DAYS and (pnl_pct < TAKE_PROFIT_PARTIAL_PCT or j_turning_down):
            signal = _sell_signal(
                f"S1趋势确认失效 (连续{pos.get('bbi_break_days')}日低于BBI，距BBI {bbi_dist:.1f}%)",
                "s1_bbi_failed",
            )
            return _resolve_shaofu_soft_exit(
                pos,
                signal,
                hold_trading_days=hold_trading_days,
                soft_exit_allowed=soft_exit_allowed,
                confirmation_key=soft_exit_confirmation_key,
            ) if shaofu_position else signal
        if bbi_dist <= BBI_BREAKDOWN_PCT:
            signal = _sell_signal(
                f"BBI跌破触发 (距BBI {bbi_dist:.1f}% ≤ {BBI_BREAKDOWN_PCT}%)",
                "bbi_breakdown",
            )
            return _resolve_shaofu_soft_exit(
                pos,
                signal,
                hold_trading_days=hold_trading_days,
                soft_exit_allowed=soft_exit_allowed,
                confirmation_key=soft_exit_confirmation_key,
            ) if shaofu_position else signal

    if max_pnl_pct > 0.8 and pnl_pct <= 0:
        signal = _sell_signal(f"盈转亏退出 (最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)", "profit_to_loss")
        return _resolve_shaofu_soft_exit(
            pos,
            signal,
            hold_trading_days=hold_trading_days,
            soft_exit_allowed=soft_exit_allowed,
            confirmation_key=soft_exit_confirmation_key,
        ) if shaofu_position else signal
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
        strategy_variant=str(pos.get("reversal_basis") or ""),
    )
    if strategy_time_exit:
        return strategy_time_exit
    if time_exit_allowed:
        if hold_days >= NO_PROGRESS_HOLD_DAYS and max_pnl_pct < NO_PROGRESS_MAX_PNL_PCT and pnl_pct <= 0:
            signal = _sell_signal(f"买入后{hold_days}日未兑现离场 ({TIME_EXIT_HHMM}尾盘检查，最高盈利{max_pnl_pct:.1f}%，先收队)", "no_progress")
            return _resolve_shaofu_soft_exit(
                pos,
                signal,
                hold_trading_days=hold_trading_days,
                soft_exit_allowed=soft_exit_allowed,
                confirmation_key=soft_exit_confirmation_key,
            ) if shaofu_position else signal

    sell_score = pos.get("sell_score")
    if isinstance(sell_score, (int, float)):
        if sell_score <= SELL_SCORE_EXIT_THRESHOLD:
            signal = _sell_signal(
                f"防卖飞评分过低 ({sell_score}/5，{pos.get('sell_score_reason','')})",
                "sell_score_exit",
            )
            return _resolve_shaofu_soft_exit(
                pos,
                signal,
                hold_trading_days=hold_trading_days,
                soft_exit_allowed=soft_exit_allowed,
                confirmation_key=soft_exit_confirmation_key,
            ) if shaofu_position else signal
        if sell_score <= SELL_SCORE_REDUCE_THRESHOLD and not pos.get("sell_score_half_done") and not pos.get("partial_tp_done"):
            signal = _sell_signal(
                f"防卖飞评分中性 ({sell_score}/5，先减半观察BBI两日破位)",
                "sell_score_reduce",
                TAKE_PROFIT_PARTIAL_RATIO,
            )
            return _resolve_shaofu_soft_exit(
                pos,
                signal,
                hold_trading_days=hold_trading_days,
                soft_exit_allowed=soft_exit_allowed,
                confirmation_key=soft_exit_confirmation_key,
            ) if shaofu_position else signal

    low10 = float(pos.get("low10") or 0)
    if low10 > 0 and hold_days >= 3 and price <= low10 * 0.995:
        signal = _sell_signal(
            f"{DONCHIAN_EXIT_LOOKBACK_DAYS}日低点跌破 (现价{price:.2f} < 低点{low10:.2f})",
            "donchian_low_break",
        )
        return _resolve_shaofu_soft_exit(
            pos,
            signal,
            hold_trading_days=hold_trading_days,
            soft_exit_allowed=soft_exit_allowed,
            confirmation_key=soft_exit_confirmation_key,
        ) if shaofu_position else signal

    if max_pnl_pct >= TRAILING_STOP_ACTIVATE_PCT:
        giveback = max_pnl_pct - pnl_pct
        trailing_gap = max(
            TRAILING_MIN_GIVEBACK_PCT,
            min(TRAILING_MAX_GIVEBACK_PCT, max_pnl_pct * TRAILING_GIVEBACK_RATIO),
        )
        pos["trailing_gap_pct"] = round(trailing_gap, 2)
        if giveback >= trailing_gap:
            signal = _sell_signal(
                f"峰值回撤止盈 (最高盈利{max_pnl_pct:.1f}%，回撤{giveback:.1f}% ≥ {trailing_gap:.1f}%)",
                "profit_giveback",
            )
            return _resolve_shaofu_soft_exit(
                pos,
                signal,
                hold_trading_days=hold_trading_days,
                soft_exit_allowed=soft_exit_allowed,
                confirmation_key=soft_exit_confirmation_key,
            ) if shaofu_position else signal
        atr20 = float(pos.get("atr20") or 0)
        if atr20 > 0:
            chandelier_stop = highest_price - ATR_CHANDELIER_MULT * atr20
            pos["chandelier_stop"] = round(chandelier_stop, 3)
            if chandelier_stop > avg_cost * 0.99 and price <= chandelier_stop:
                signal = _sell_signal(
                    f"ATR吊灯止盈 (现价{price:.2f} ≤ {ATR_CHANDELIER_MULT:.0f}ATR止损{chandelier_stop:.2f})",
                    "atr_chandelier",
                )
                return _resolve_shaofu_soft_exit(
                    pos,
                    signal,
                    hold_trading_days=hold_trading_days,
                    soft_exit_allowed=soft_exit_allowed,
                    confirmation_key=soft_exit_confirmation_key,
                ) if shaofu_position else signal
        if pos.get("trailing_stop_activated") and pnl_pct < 1.0:
            signal = _sell_signal(f"移动止损保本 (曾盈利>5%，回落至{pnl_pct:.1f}%)", "breakeven_trail")
            return _resolve_shaofu_soft_exit(
                pos,
                signal,
                hold_trading_days=hold_trading_days,
                soft_exit_allowed=soft_exit_allowed,
                confirmation_key=soft_exit_confirmation_key,
            ) if shaofu_position else signal

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

    if shaofu_position:
        _clear_shaofu_soft_exit_pending(pos)
    return None


def evaluate_prompt_position_exit(
    code: str,
    pos: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    state: Mapping[str, Any],
    dt: datetime,
    store: PromptStrategyStore | None = None,
) -> dict[str, Any] | None:
    version_id = str(pos.get("prompt_strategy_version_id") or "")
    if not version_id:
        return None
    strategy_store = store or PromptStrategyStore()
    version = strategy_store.get_version(version_id)
    if version is None:
        raise ValueError("持仓绑定的文字策略版本不存在")
    binding = strategy_store.active_position_binding(code)
    if binding is None:
        binding = strategy_store.bind_position(
            code=code,
            strategy_version_id=version_id,
            entry_evaluation_id=str(
                pos.get("prompt_strategy_entry_evaluation_id") or ""
            ),
        )
        pos["prompt_strategy_binding_id"] = binding["binding_id"]
    elif str(binding.get("strategy_version_id") or "") != version_id:
        raise ValueError("持仓文字策略版本绑定不一致")
    price = _safe_float(pos.get("last_price") or pos.get("close"), 0.0)
    avg_cost = _safe_float(pos.get("avg_cost"), 0.0)
    quote = {
        "price": price,
        "open": pos.get("day_open") or price,
        "high": pos.get("day_high") or price,
        "low": pos.get("day_low") or price,
        "volume": pos.get("volume_lots") or 0,
        "quote_time": str(pos.get("quote_time") or ""),
    }
    plan = version.get("execution_plan") or {}
    exit_minimum_bars = max(
        1,
        min(
            500,
            int(
                ((plan.get("stage_requirements") or {}).get("exit") or {}).get(
                    "minimum_bars"
                )
                or 1
            ),
        ),
    )
    bar_status = str(
        (((plan.get("strategy") or {}).get("data_contract") or {}).get(
            "bar_status"
        ))
        or "closed"
    )
    evaluation_rows = merge_live_quote(
        rows,
        quote,
        limit=min(501, exit_minimum_bars + (1 if bar_status == "closed" else 0)),
    )
    result = evaluate_frozen_strategy_stage(
        version,
        "exit",
        evaluation_rows,
        code=code,
        name=str(pos.get("name") or ""),
        runtime_facts={
            "account.cash": _safe_float(state.get("cash"), 0.0),
            "position.quantity": position_qty(pos),
            "position.available_shares": available_to_sell(pos, dt.strftime("%Y-%m-%d")),
            "position.avg_cost": avg_cost,
            "position.pnl_pct": (
                (price / avg_cost - 1.0) * 100.0
                if price > 0 and avg_cost > 0
                else None
            ),
            "position.hold_days": holding_days(pos, dt.strftime("%Y-%m-%d")),
        },
        data_context=prompt_strategy_data_context(quote, dt),
    )
    recorded = strategy_store.record_evaluation(version_id, result["audit"])
    result["evaluation_id"] = recorded["evaluation_id"]
    return result


def validate_versioned_prompt_exit_evidence(
    code: str,
    pos: Mapping[str, Any],
    *,
    store: PromptStrategyStore | None = None,
) -> str:
    """Fail closed unless the pending exit matches its frozen, replayable audit."""
    version_id = str(pos.get("prompt_strategy_version_id") or "")
    evaluation_id = str(pos.get("prompt_strategy_exit_evaluation_id") or "")
    audit_sha256 = str(pos.get("prompt_strategy_exit_audit_sha256") or "")
    evaluation = pos.get("prompt_strategy_exit_evaluation")
    if not version_id or not evaluation_id or len(audit_sha256) != 64:
        return "文字策略退出缺少完整审计引用"
    if pos.get("prompt_strategy_exit_status") != "true" or not isinstance(
        evaluation,
        Mapping,
    ):
        return "文字策略退出审计未证明规则成立"
    strategy_store = store or PromptStrategyStore()
    try:
        version = strategy_store.get_version(version_id)
        binding = strategy_store.active_position_binding(code)
        recorded = strategy_store.get_evaluation(evaluation_id)
    except Exception as exc:
        return f"文字策略退出审计无法回放（{type(exc).__name__}）"
    if version is None or str(version.get("plan_sha256") or "") != str(
        pos.get("prompt_strategy_plan_sha256") or ""
    ):
        return "文字策略退出版本或计划指纹不一致"
    if (
        not isinstance(binding, Mapping)
        or str(binding.get("strategy_version_id") or "") != version_id
    ):
        return "文字策略持仓版本绑定缺失或不一致"
    if not isinstance(recorded, Mapping):
        return "文字策略退出审计记录不存在"
    audit = recorded.get("audit")
    if not isinstance(audit, Mapping):
        return "文字策略退出审计载荷缺失"
    if (
        str(recorded.get("strategy_version_id") or "") != version_id
        or str(audit.get("strategy_version_id") or "") != version_id
        or str(audit.get("stage") or "") != "exit"
        or normalize_code(audit.get("code") or "") != normalize_code(code)
        or str(audit.get("audit_sha256") or "") != audit_sha256
        or str(audit.get("plan_sha256") or "")
        != str(version.get("plan_sha256") or "")
        or (audit.get("evaluation") or {}) != dict(evaluation)
        or str(((audit.get("evaluation") or {}).get("status") or "")) != "true"
    ):
        return "文字策略退出审计与当前持仓证据不一致"
    return ""


def _refresh_position_bbi(
    state: dict[str, Any],
    dt: datetime | None = None,
    *,
    evaluate_prompt_exits: bool = False,
) -> None:
    """Fetch daily K-lines for open positions and cache sell-rule indicators."""
    positions = state.get("positions") or {}
    if not positions:
        return
    import statistics as _st
    prompt_store = PromptStrategyStore() if evaluate_prompt_exits else None
    for code, pos in positions.items():
        try:
            if (
                not evaluate_prompt_exits
                and position_entry_strategy(pos) == STRATEGY_SOURCE_PRESET_TEXT
                and pos.get("prompt_strategy_version_id")
            ):
                continue
            if (
                evaluate_prompt_exits
                and position_entry_strategy(pos) == STRATEGY_SOURCE_PRESET_TEXT
                and pos.get("prompt_strategy_version_id")
                and pos.get("prompt_strategy_pending_exit")
            ):
                pending_version = (
                    prompt_store.get_version(
                        str(pos.get("prompt_strategy_version_id") or "")
                    )
                    if prompt_store is not None
                    else None
                )
                pending_evaluation = pos.get("prompt_strategy_exit_evaluation")
                if (
                    pending_version is None
                    or not pos.get("prompt_strategy_exit_evaluation_id")
                    or not isinstance(pending_evaluation, Mapping)
                    or str(pending_evaluation.get("plan_sha256") or "")
                    != str(pending_version.get("plan_sha256") or "")
                ):
                    raise ValueError("待卖文字策略缺少可验证的冻结退出审计")
                pending_as_of = dt or datetime.now()
                pending_sellable = available_to_sell(
                    pos,
                    pending_as_of.strftime("%Y-%m-%d"),
                )
                pos["prompt_strategy_exit_status"] = "true"
                pos["prompt_strategy_pending_exit_ready"] = pending_sellable > 0
                pos["prompt_strategy_exit_checked_at"] = pending_as_of.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                continue
            script = STOCK_TOOLS_SCRIPT
            requested_kline_count = 130
            if (
                evaluate_prompt_exits
                and position_entry_strategy(pos) == STRATEGY_SOURCE_PRESET_TEXT
                and pos.get("prompt_strategy_version_id")
                and prompt_store is not None
            ):
                prompt_version = prompt_store.get_version(
                    str(pos.get("prompt_strategy_version_id") or "")
                )
                prompt_plan = (prompt_version or {}).get("execution_plan") or {}
                exit_minimum_bars = max(
                    1,
                    min(
                        500,
                        int(
                            ((prompt_plan.get("stage_requirements") or {}).get("exit") or {}).get(
                                "minimum_bars"
                            )
                            or 1
                        ),
                    ),
                )
                prompt_bar_status = str(
                    (((prompt_plan.get("strategy") or {}).get("data_contract") or {}).get(
                        "bar_status"
                    ))
                    or "closed"
                )
                requested_kline_count = min(
                    501,
                    exit_minimum_bars + (1 if prompt_bar_status == "closed" else 0),
                )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "kline",
                    code,
                    str(requested_kline_count),
                ],
                capture_output=True, text=True, timeout=20,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                continue
            data = json.loads(proc.stdout)
            raw_rows = [r for r in (data.get("rows") or []) if isinstance(r, dict) and r.get("close")]
            entry_strategy = position_entry_strategy(pos)
            zettaranc_position = is_zettaranc_strategy(entry_strategy)
            sector_tide_position = is_sector_tide_strategy(entry_strategy)
            niuone_position = is_niuone_strategy(entry_strategy)
            rows = raw_rows
            as_of = dt or datetime.now()
            if (
                evaluate_prompt_exits
                and entry_strategy == STRATEGY_SOURCE_PRESET_TEXT
                and pos.get("prompt_strategy_version_id")
            ):
                prompt_exit = evaluate_prompt_position_exit(
                    normalize_code(code),
                    pos,
                    raw_rows,
                    state=state,
                    dt=as_of,
                    store=prompt_store,
                )
                if prompt_exit is not None:
                    pos["prompt_strategy_exit_status"] = str(
                        (prompt_exit.get("evaluation") or {}).get("status") or "unknown"
                    )
                    pos["prompt_strategy_exit_evaluation"] = _json_safe_copy(
                        prompt_exit.get("evaluation") or {}
                    )
                    pos["prompt_strategy_exit_evaluation_id"] = str(
                        prompt_exit.get("evaluation_id") or ""
                    )
                    pos["prompt_strategy_exit_audit_sha256"] = str(
                        (prompt_exit.get("audit") or {}).get("audit_sha256") or ""
                    )
                    pos["prompt_strategy_exit_checked_at"] = as_of.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    if pos["prompt_strategy_exit_status"] == "true":
                        sellable = available_to_sell(
                            pos,
                            as_of.strftime("%Y-%m-%d"),
                        )
                        total_quantity = position_qty(pos)
                        pos["prompt_strategy_pending_exit"] = (
                            sellable < total_quantity
                        )
                        pos["prompt_strategy_pending_exit_ready"] = sellable > 0
                        pos["prompt_strategy_pending_exit_reason"] = (
                            "退出条件已成立，但全部或部分股份受T+1约束"
                            if sellable < total_quantity
                            else ""
                        )
                    else:
                        pos["prompt_strategy_pending_exit"] = False
                        pos["prompt_strategy_pending_exit_ready"] = False
                        pos["prompt_strategy_pending_exit_reason"] = ""
                # Frozen prompt positions have an independent exit rule stack.  Do
                # not compute BBI/KDJ/ATR or any other legacy exit-only indicator.
                continue
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
                        completed_volumes = [
                            _row_float(row, "volume")
                            for row in rows[-20:]
                            if _row_float(row, "volume") > 0
                        ]
                        if completed_volumes:
                            pos["median_volume_20"] = round(
                                statistics.median(completed_volumes),
                                3,
                            )
                    pos["last_kline_date"] = rows[-1].get("date") or ""
                    desired_stop_sources = {
                        "shaofu_b1": {"n_structure_low"},
                        "b2_confirm": {"b1_low"},
                        "b3_accelerate": {"b3_kline_low", "b2_midpoint"},
                        "super_b1": {"super_b1_washout_low"},
                    }
                    current_source = str(pos.get("shaofu_stop_source") or "")
                    should_refresh_z_stop = zettaranc_position and current_source not in desired_stop_sources.get(entry_strategy, set())
                    should_refresh_legacy_stop = not zettaranc_position and not sector_tide_position and not niuone_position and (
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
        except Exception as exc:
            if evaluate_prompt_exits and pos.get("prompt_strategy_version_id"):
                pos["prompt_strategy_exit_status"] = "unknown"
                pos["prompt_strategy_exit_error"] = type(exc).__name__
                pos["prompt_strategy_exit_checked_at"] = (
                    (dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
                )
            continue


def _refresh_frozen_prompt_position_exits(
    state: dict[str, Any],
    dt: datetime,
) -> None:
    positions = state.get("positions") or {}
    prompt_positions = {
        code: pos
        for code, pos in positions.items()
        if isinstance(pos, dict)
        and position_qty(pos) > 0
        and position_entry_strategy(pos) == STRATEGY_SOURCE_PRESET_TEXT
        and pos.get("prompt_strategy_version_id")
    }
    if not prompt_positions:
        return
    prompt_state = dict(state)
    prompt_state["positions"] = prompt_positions
    _refresh_position_bbi(
        prompt_state,
        dt,
        evaluate_prompt_exits=True,
    )

AUTO_EXIT_PERSISTENCE_STATUS_KEY = "_auto_exit_persistence_status"
AUTO_EXIT_ELIGIBLE_CODES_KEY = "_auto_exit_eligible_codes"
AUTO_EXIT_REFRESH_BASELINE_KEY = "_auto_exit_refresh_baseline"
_AUTO_EXIT_ACCOUNT_POSITION_FIELDS = frozenset({
    "avg_cost",
    "buy_date_lots",
    "qty",
    "shares",
})
_AUTO_EXIT_REFRESH_META_FIELDS = (
    "last_quote_refresh",
    "last_intraday_refresh",
)


def _auto_exit_refresh_baseline(state: Mapping[str, Any]) -> dict[str, Any]:
    """Capture only fields needed to distinguish refresh deltas from stale state."""
    baseline = {
        "positions": copy.deepcopy(state.get("positions") or {}),
    }
    for field in _AUTO_EXIT_REFRESH_META_FIELDS:
        if field in state:
            baseline[field] = copy.deepcopy(state[field])
    return baseline


def _default_persistence_status() -> dict[str, bool]:
    return {
        "trades_persisted": True,
        "decision_persisted": True,
        "durable_evidence_persisted": True,
    }


def _pop_auto_exit_persistence_status(
    state: dict[str, Any],
) -> dict[str, bool]:
    raw = state.pop(AUTO_EXIT_PERSISTENCE_STATUS_KEY, None)
    if not isinstance(raw, dict):
        return _default_persistence_status()
    defaults = _default_persistence_status()
    return {
        key: raw.get(key) is True
        for key in defaults
    }


def _position_account_identity(position: Mapping[str, Any]) -> tuple[Any, ...]:
    try:
        qty = int(position.get("qty") or position.get("shares") or 0)
    except (TypeError, ValueError):
        qty = 0
    raw_lots = position.get("buy_date_lots")
    lots: list[tuple[str, int]] = []
    if isinstance(raw_lots, Mapping):
        for day, raw_qty in raw_lots.items():
            try:
                lot_qty = int(raw_qty or 0)
            except (TypeError, ValueError):
                lot_qty = 0
            lots.append((str(day), lot_qty))
    return (
        qty,
        round(_safe_float(position.get("avg_cost"), 0.0), 8),
        tuple(sorted(lots)),
    )


def _merge_refreshed_auto_exit_context(
    canonical_state: dict[str, Any],
    refreshed_state: Mapping[str, Any],
    refresh_baseline: Mapping[str, Any],
) -> set[str]:
    """Carry quote/rule inputs onto unchanged canonical positions."""
    canonical_positions = {
        normalize_code(code): position
        for code, position in (canonical_state.get("positions") or {}).items()
        if isinstance(position, dict) and normalize_code(code)
    }
    refreshed_positions = {
        normalize_code(code): position
        for code, position in (refreshed_state.get("positions") or {}).items()
        if isinstance(position, Mapping) and normalize_code(code)
    }
    baseline_positions = {
        normalize_code(code): position
        for code, position in (refresh_baseline.get("positions") or {}).items()
        if isinstance(position, Mapping) and normalize_code(code)
    }
    eligible_codes: set[str] = set()
    for code, refreshed_position in refreshed_positions.items():
        canonical_position = canonical_positions.get(code)
        if canonical_position is None:
            continue
        if _position_account_identity(canonical_position) != _position_account_identity(
            refreshed_position
        ):
            continue
        baseline_position = baseline_positions.get(code)
        if baseline_position is None:
            continue
        for field in set(baseline_position) | set(refreshed_position):
            if field not in _AUTO_EXIT_ACCOUNT_POSITION_FIELDS:
                baseline_has_field = field in baseline_position
                refreshed_has_field = field in refreshed_position
                if (
                    baseline_has_field == refreshed_has_field
                    and baseline_position.get(field) == refreshed_position.get(field)
                ):
                    continue
                if refreshed_has_field:
                    canonical_position[field] = copy.deepcopy(
                        refreshed_position[field]
                    )
                else:
                    canonical_position.pop(field, None)
        eligible_codes.add(code)

    for field in _AUTO_EXIT_REFRESH_META_FIELDS:
        baseline_has_field = field in refresh_baseline
        refreshed_has_field = field in refreshed_state
        if (
            baseline_has_field == refreshed_has_field
            and refresh_baseline.get(field) == refreshed_state.get(field)
        ):
            continue
        if refreshed_has_field:
            canonical_state[field] = copy.deepcopy(refreshed_state[field])
        else:
            canonical_state.pop(field, None)
    return eligible_codes


def _commit_refreshed_auto_exits(
    refreshed_state: Mapping[str, Any],
    refresh_baseline: Mapping[str, Any],
    dt: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, bool]]:
    """Re-read, evaluate, and persist auto exits in one account transaction."""
    with state_file_write_lock():
        canonical_state = load_state()
        eligible_codes = _merge_refreshed_auto_exit_context(
            canonical_state,
            refreshed_state,
            refresh_baseline,
        )
        canonical_state.pop(AUTO_EXIT_PERSISTENCE_STATUS_KEY, None)
        canonical_state[AUTO_EXIT_ELIGIBLE_CODES_KEY] = sorted(eligible_codes)
        try:
            executed = check_auto_exits(canonical_state, dt)
        finally:
            canonical_state.pop(AUTO_EXIT_ELIGIBLE_CODES_KEY, None)
        persistence_status = _pop_auto_exit_persistence_status(canonical_state)
        record_equity(canonical_state)
        save_state(canonical_state)
    return canonical_state, executed, persistence_status


def check_auto_exits(
    state: dict[str, Any],
    dt: datetime | None = None,
) -> list[dict[str, Any]]:
    """检查所有持仓是否触发自动止盈/止损/技术退出条件。
    
    退出优先级由 evaluate_sell_signal 统一维护：
    硬止损、S1/BBI失效、10日低点、峰值回撤/ATR吊灯、
    分批止盈、目标止盈、持仓时间离场。
    """
    state[AUTO_EXIT_PERSISTENCE_STATUS_KEY] = _default_persistence_status()
    check_dt = dt or datetime.now()
    trade_allowed, _ = is_a_share_execution_time(check_dt)
    if not trade_allowed:
        return []

    positions = state.get("positions") or {}
    if not positions:
        return []
    eligible_raw = state.get(AUTO_EXIT_ELIGIBLE_CODES_KEY)
    eligible_codes = (
        {
            normalize_code(code)
            for code in eligible_raw
            if normalize_code(code)
        }
        if isinstance(eligible_raw, (list, tuple, set, frozenset))
        else None
    )
    
    today = check_dt.strftime("%Y-%m-%d")
    time_exit_allowed = is_time_exit_check_time(check_dt)
    b3_exit_allowed = is_b3_exit_check_time(check_dt)
    soft_exit_allowed = is_shaofu_soft_exit_check_time(check_dt)
    soft_exit_confirmation_key = check_dt.strftime("%Y-%m-%d %H:%M")
    executed = []
    cash = float(state.get("cash") or 0)
    
    for code in list(positions.keys()):
        if eligible_codes is not None and normalize_code(code) not in eligible_codes:
            continue
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
        
        exit_signal = evaluate_sell_signal(
            code,
            pos,
            today,
            time_exit_allowed=time_exit_allowed,
            b3_exit_allowed=b3_exit_allowed,
            soft_exit_allowed=soft_exit_allowed,
            soft_exit_confirmation_key=soft_exit_confirmation_key,
        )
        if not exit_signal:
            continue
        exit_reason = str(exit_signal.get("reason") or "")
        entry_strategy = str(
            position_entry_strategy(pos)
            or latest_buy_strategy_for_code(state, code)
            or classify_buy_strategy(str(pos.get("entry_reason") or ""))
        )
        if (
            entry_strategy == STRATEGY_SOURCE_PRESET_TEXT
            and pos.get("prompt_strategy_version_id")
        ):
            prompt_exit_error = validate_versioned_prompt_exit_evidence(code, pos)
            if prompt_exit_error:
                pos["prompt_strategy_exit_error"] = prompt_exit_error
                continue
            pos.pop("prompt_strategy_exit_error", None)
        exit_rule = classify_exit_rule(exit_reason, str(exit_signal.get("signal") or ""))
        trade_time = now_ts()
        niuone_entry_context = (
            niuone_entry_context_from_position(pos)
            if is_niuone_strategy(entry_strategy)
            else {}
        )
        niuone_lifecycle_evidence = (
            niuone_lifecycle_exit_evidence_from_position(
                pos,
                observed_at=trade_time,
            )
            if is_niuone_strategy(entry_strategy)
            else {}
        )
        sell_ratio = float(exit_signal.get("sell_ratio") or 1.0)
        
        # 执行卖出
        qty = min(sellable, position_qty(pos))
        if sell_ratio < 1.0:
            qty = max(100, int(qty * sell_ratio) // 100 * 100)
            if exit_signal.get("signal") == "sell_score_reduce":
                pos["sell_score_half_done"] = True
            if exit_signal.get("signal") == "luzhu_half":
                pos["luzhu_half_done"] = True
            if exit_signal.get("signal") == "shaofu_soft_reduce":
                pos["shaofu_soft_exit_reduced"] = True
            if exit_signal.get("signal") == "niu_lifecycle_climax_partial":
                pos["niuone_lifecycle_climax_partial_done"] = True
            if exit_signal.get("signal") in {"niu_r_partial", "niu_2r_partial"}:
                pos.update({
                    "niuone_markup_rebalance_reduced": True,
                    "niuone_markup_rebalance_cycle_peak_price": round(
                        float(price),
                        3,
                    ),
                    "niuone_markup_rebalance_stall_count": 0,
                    "niuone_markup_rebalance_observation_count": 0,
                    "niuone_markup_rebalance_last_observation": today,
                    "niuone_markup_rebalance_reduction_source": str(
                        exit_signal.get("signal") or ""
                    ),
                })
            if exit_signal.get("signal") == "niu_markup_rebalance_partial":
                rebalance_atr = _safe_float(
                    pos.get("atr20") or pos.get("entry_atr20"),
                    0.0,
                )
                pos.update({
                    "niuone_markup_rebalance_armed": True,
                    "niuone_markup_rebalance_reduced": True,
                    "niuone_markup_rebalance_armed_date": today,
                    "niuone_markup_rebalance_reentry_price": round(
                        float(price)
                        + NIUONE_MARKUP_REBALANCE_REBOUND_ATR
                        * rebalance_atr,
                        3,
                    ),
                    "niuone_markup_rebalance_trim_count": (
                        int(
                            pos.get("niuone_markup_rebalance_trim_count") or 0
                        ) + 1
                    ),
                    "niuone_markup_rebalance_last_trim_price": round(
                        float(price),
                        3,
                    ),
                    "niuone_markup_rebalance_reduction_source": (
                        "niu_markup_rebalance_partial"
                    ),
                })
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
        
        position_closed = pos["qty"] <= 0
        prompt_binding_release_error = ""
        if position_closed and pos.get("prompt_strategy_version_id"):
            try:
                PromptStrategyStore().release_position(code)
            except Exception as exc:
                prompt_binding_release_error = type(exc).__name__
        if position_closed:
            positions.pop(code, None)
        cash += net_proceeds
        
        executed_trade = {
            "time": trade_time,
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
        }
        if niuone_entry_context:
            executed_trade["niuone_entry_context"] = dict(
                niuone_entry_context
            )
        if niuone_lifecycle_evidence:
            executed_trade["niuone_lifecycle_evidence"] = dict(
                niuone_lifecycle_evidence
            )
        if entry_strategy == STRATEGY_SOURCE_PRESET_TEXT and pos.get(
            "prompt_strategy_version_id"
        ):
            executed_trade.update({
                "prompt_strategy_version_id": str(
                    pos.get("prompt_strategy_version_id") or ""
                ),
                "prompt_strategy_plan_sha256": str(
                    pos.get("prompt_strategy_plan_sha256") or ""
                ),
                "prompt_strategy_exit_evaluation_id": str(
                    pos.get("prompt_strategy_exit_evaluation_id") or ""
                ),
                "prompt_strategy_exit_audit_sha256": str(
                    pos.get("prompt_strategy_exit_audit_sha256") or ""
                ),
                "prompt_strategy_binding_released": (
                    position_closed and not prompt_binding_release_error
                ),
            })
            if prompt_binding_release_error:
                executed_trade["prompt_strategy_binding_release_error"] = (
                    prompt_binding_release_error
                )
        executed.append(executed_trade)
    
    if executed:
        state["cash"] = round(cash, 2)
        state.setdefault("trade_log", []).extend(executed)
        del state["trade_log"][:-TRADE_LOG_LIMIT]
        trades_persisted = _sync_trades_to_db(executed)
        if trades_persisted:
            _sync_positions_to_db(state)
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
        decision_persisted = _sync_decision_to_db(log_entry)
        state[AUTO_EXIT_PERSISTENCE_STATUS_KEY] = {
            "trades_persisted": trades_persisted,
            "decision_persisted": decision_persisted,
            "durable_evidence_persisted": (
                trades_persisted and decision_persisted
            ),
        }
    
    return executed


def run_auto_exits_once(dt: datetime | None = None) -> dict[str, Any]:
    """Run the side-effectful automatic exit script once for scheduled checks."""
    dt = dt or datetime.now()
    state = load_state()
    refresh_baseline = _auto_exit_refresh_baseline(state)
    strategy_payload = load_latest_sector_tide_payload()
    sync_sector_tide_position_context(state, strategy_payload)
    sync_niuone_position_context(state, strategy_payload)
    sync_zettaranc_position_context(state, strategy_payload)
    refresh_realtime_prices(state)
    refresh_position_intraday(state)
    _refresh_position_bbi(state, dt)
    _refresh_frozen_prompt_position_exits(state, dt)
    update_zettaranc_volume_context(state, dt)
    state, executed, persistence_status = _commit_refreshed_auto_exits(
        state,
        refresh_baseline,
        dt,
    )
    executed = _accounted_trade_executions(executed)
    if executed:
        _notify_trade_executions_safely(executed)
    return {
        "ok": persistence_status["durable_evidence_persisted"],
        "checked_at": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "b3_exit_time": B3_EXIT_HHMM,
        "time_exit_time": TIME_EXIT_HHMM,
        "executed": executed,
        "executed_count": len(executed),
        **persistence_status,
        "portfolio": enrich_portfolio(state),
    }


def run_position_exit_checks_before_decision(
    state: dict[str, Any],
    dt: datetime | None = None,
) -> list[dict[str, Any]]:
    """Refresh and evaluate every open position before candidate/model work."""
    positions = state.get("positions") or {}
    if not any(isinstance(pos, dict) and position_qty(pos) > 0 for pos in positions.values()):
        return []
    current = dt or datetime.now()
    baseline_value = state.pop(AUTO_EXIT_REFRESH_BASELINE_KEY, None)
    refresh_baseline = (
        baseline_value
        if isinstance(baseline_value, Mapping)
        else _auto_exit_refresh_baseline(state)
    )
    refresh_realtime_prices(state)
    refresh_position_intraday(state)
    _refresh_position_bbi(state, current)
    _refresh_frozen_prompt_position_exits(state, current)
    update_zettaranc_volume_context(state, current)
    canonical_state, executed, persistence_status = _commit_refreshed_auto_exits(
        state,
        refresh_baseline,
        current,
    )
    state.clear()
    state.update(canonical_state)
    state[AUTO_EXIT_PERSISTENCE_STATUS_KEY] = persistence_status
    return executed


def maybe_record_session_equity_heartbeat(min_interval_seconds: int = EQUITY_HEARTBEAT_MIN_SECONDS) -> bool:
    """Record session equity independently of dashboard requests."""
    now = datetime.now()
    if not is_a_share_equity_heartbeat_clock(now):
        return False
    refreshed_state = load_state()
    pruned = prune_future_intraday_equity_points(refreshed_state, now=now)
    last_dt = latest_equity_timestamp(refreshed_state)
    if last_dt and not equity_heartbeat_due(now, last_dt, min_interval_seconds):
        if pruned:
            save_state(refreshed_state)
        return False

    # Fetch quotes outside the portfolio lock so transient providers cannot
    # block trading writes. Only quote fields are carried into the transaction.
    try:
        refresh_realtime_prices(refreshed_state)
    except Exception as exc:
        refreshed_state["last_quote_refresh"] = {
            "time": now_ts(),
            "updated": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    # Re-read under the cross-process write lock after the network call. A trade
    # may have committed while quotes were loading; its same-minute point must
    # remain authoritative instead of being replaced by this older snapshot.
    with state_file_write_lock():
        state = load_state()
        commit_now = datetime.now()
        if not is_a_share_equity_heartbeat_clock(commit_now):
            return False
        pruned = prune_future_intraday_equity_points(state, now=commit_now)
        last_dt = latest_equity_timestamp(state)
        if last_dt and not equity_heartbeat_due(
            commit_now,
            last_dt,
            min_interval_seconds,
        ):
            if pruned:
                save_state(state)
            return False
        apply_realtime_price_snapshot(state, refreshed_state)
        recorded = record_equity(state)
        save_state(state)
        return recorded


def load_crossdesk_config(base_url_env: str = "", api_key_env: str = "") -> tuple[str, str]:
    shared = resolve_shared_model_config(os.environ)
    env_base_url = shared.base_url if base_url_env == "DASHBOARD_DECISION_BASE_URL" else ""
    env_api_key = shared.api_key if api_key_env == "DASHBOARD_DECISION_API_KEY" else ""
    env_base_url = env_base_url or (os.environ.get(base_url_env) if base_url_env else "")
    env_api_key = env_api_key or (os.environ.get(api_key_env) if api_key_env else "")
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
    """Backward-compatible wrapper around the shared model response parser."""
    parsed = parse_model_response(raw)
    return parsed.content, parsed.detail


def request_chat_content(
    base_url: str,
    api_key: str,
    payload: dict,
    model_name: str,
    max_retries: int = 3,
    timeout: int = 60,
    *,
    api_mode: str = "auto",
    stream_mode: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    reasoning: dict[str, Any] | None = None,
) -> str:
    """Call a compatible model endpoint and require visible assistant text."""
    import time as _time
    last_err: Exception | None = None
    request_payload = {**payload, "model": model_name}
    for attempt in range(max_retries):
        try:
            model_request = build_model_request(
                base_url,
                model_name,
                list(request_payload.get("messages") or []),
                max_tokens=int(request_payload.get("max_tokens") or 0) or None,
                api_mode=api_mode,
                tools=tools,
                reasoning=reasoning,
                reasoning_effort=str(request_payload.get("reasoning_effort") or ""),
                stream=bool(request_payload.get("stream", False)),
                extra_payload=request_payload,
            )
            parsed = request_model_complete(
                model_request,
                api_key,
                timeout=timeout,
                stream_mode=stream_mode or DECISION_STREAM_MODE,
                opener=urllib.request.urlopen,
            )
            content, detail = parsed.content, parsed.detail
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
    stream_mode: str | None = None,
) -> dict[str, Any]:
    """Request a JSON object, retrying truncated/malformed non-empty responses."""
    request_payload = dict(payload)
    last_error: Exception | None = None
    for attempt in range(max(1, max_parse_attempts)):
        request_kwargs: dict[str, Any] = {}
        if stream_mode is not None:
            request_kwargs["stream_mode"] = stream_mode
        content = request_chat_content(
            base_url,
            api_key,
            request_payload,
            model_name,
            max_retries=3,
            timeout=timeout,
            **request_kwargs,
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
            model_request = build_model_request(
                base_url,
                str(payload.get("model") or ""),
                list(payload.get("messages") or []),
                max_tokens=int(payload.get("max_tokens") or 0) or None,
                api_mode="auto",
                reasoning_effort=str(payload.get("reasoning_effort") or ""),
                stream=bool(payload.get("stream", False)),
                extra_payload=payload,
            )
            parsed = request_model(
                model_request,
                api_key,
                timeout=timeout,
                opener=urllib.request.urlopen,
            )
            if parsed.data is None:
                raise ValueError("空响应")
            return parsed.data
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                _time.sleep(2 ** attempt)  # 1s, 2s, 4s 退避
    raise last_err


def load_news_precheck_config() -> NewsPrecheckConfig | None:
    try:
        return NewsPrecheckConfig.from_mapping(os.environ)
    except ValueError as exc:
        detail = str(exc).split(":", 1)[-1]
        raise RuntimeError("消息面预检配置不完整：" + detail) from exc


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
            "entry_theme": pos.get("entry_theme") or "",
            "active_theme": pos.get("active_theme") or "",
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
                    "industry_flow_direction", "industry_flow_rank",
                    "industry_flow_net_yi", "industry_outflow_rank",
                    "industry_outflow_net_yi", "industry_flow_generated_at",
                    "market_turnover_ratio", "projected_volume_ratio_20d",
                    "shaofu_volume_price_signal", "shaofu_soft_exit_status",
                    "shaofu_soft_exit_signal", "shaofu_soft_exit_count",
                    "shaofu_soft_exit_required",
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
        "recent_trades": [
            trade
            for trade in (portfolio.get("trade_log") or [])
            if isinstance(trade, Mapping) and trade_counts_for_account(trade)
        ][:8],
        "last_b1_generated_at": portfolio.get("last_b1_generated_at"),
        "last_decision_at": portfolio.get("last_decision_at"),
        "last_quote_refresh": portfolio.get("last_quote_refresh"),
        "last_intraday_refresh": portfolio.get("last_intraday_refresh"),
        "last_error": portfolio.get("last_error"),
    }


def check_candidate_news_precheck(candidates: list[dict[str, Any]]) -> str:
    """Retrieve through iWencai and judge with the decision model for top candidates.

    Returns: 格式化的消息面文本，供决策 prompt 使用。
    """
    top_candidates = [c for c in candidates[:5] if isinstance(c, dict)]
    if not top_candidates:
        return ""
    news_config = load_news_precheck_config()
    if news_config is None:
        return ""
    cached_records = [candidate.get("news_precheck") for candidate in top_candidates]
    source_mode = "iwencai"
    cached_count = sum(
        1
        for record in cached_records
        if cached_news_record_matches_source(record, source_mode, news_config.model)
    )
    if cached_count == len(top_candidates):
        return format_cached_news_records(cached_records)

    missing_candidates = [
        top_candidates[idx]
        for idx in range(len(top_candidates))
        if not cached_news_record_matches_source(
            cached_records[idx], source_mode, news_config.model
        )
    ]
    fresh_records = fetch_candidate_news_records(
        missing_candidates,
        news_config,
        max_candidates=len(missing_candidates),
    )
    fresh_iter = iter(fresh_records)
    combined_records = [
        record
        if cached_news_record_matches_source(record, source_mode, news_config.model)
        else next(fresh_iter, {})
        for record in cached_records
    ]
    lines = [
        format_cached_news_record(record)
        for record in combined_records
        if isinstance(record, dict) and record
    ]
    return "【消息面预检（同花顺问财）】\n" + "\n".join(lines) if lines else ""


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


def active_frozen_prompt_strategy() -> dict[str, Any] | None:
    if current_strategy_suite() != STRATEGY_SOURCE_PRESET_TEXT:
        return None
    return PromptStrategyStore().active_version()


def load_prompt_strategy_rows(
    code: str,
    *,
    quote: Mapping[str, Any] | None = None,
    count: int = 120,
    timeout: int = 20,
) -> list[dict[str, Any]]:
    bounded_count = max(1, min(501, int(count or 1)))
    proc = subprocess.run(
        [
            sys.executable,
            str(STOCK_TOOLS_SCRIPT),
            "kline",
            normalize_code(code),
            str(bounded_count),
        ],
        capture_output=True,
        text=True,
        timeout=max(5, min(30, int(timeout))),
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError("无法获取文字策略所需K线")
    payload = json.loads(proc.stdout)
    rows = [
        dict(item)
        for item in (payload.get("rows") or [])
        if isinstance(item, Mapping)
    ]
    if not rows:
        raise RuntimeError("文字策略K线为空")
    quote_payload = dict(quote or {})
    return (
        merge_live_quote(rows, quote_payload, limit=bounded_count)
        if quote_payload
        else rows[-bounded_count:]
    )


def prompt_strategy_data_context(
    quote: Mapping[str, Any],
    evaluated_at: datetime,
) -> dict[str, Any]:
    evaluated_date = evaluated_at.strftime("%Y-%m-%d")
    calendar = trading_day_status(evaluated_date, allow_refresh=False)
    return {
        "expected_closed_date": str(calendar.get("previous_trading_day") or "")[:10],
        "expected_live_date": evaluated_date,
        "evaluated_at": evaluated_at.strftime("%Y-%m-%d %H:%M:%S"),
        "observed_at": str(quote.get("quote_time") or ""),
        "quote_trade_date": quote_trade_date(quote),
    }


def build_local_prompt_decision(
    candidates: list[dict[str, Any]],
    state: dict[str, Any],
    version: Mapping[str, Any],
    market_strategy_ctx: Mapping[str, Any],
) -> dict[str, Any]:
    plan = version.get("execution_plan") or {}
    strategy = plan.get("strategy") or {}
    execution_mode = str(strategy.get("execution_mode") or "recommend_only")
    if execution_mode != "simulation":
        return {
            "summary": f"冻结文字策略本轮生成{len(candidates)}个研究建议，不执行模拟交易",
            "actions": [],
            "recommendations": [
                {
                    "code": normalize_code(candidate.get("code") or ""),
                    "name": str(candidate.get("name") or ""),
                }
                for candidate in candidates
                if normalize_code(candidate.get("code") or "")
            ],
            "model": "LOCAL_PROMPT_RULE_ENGINE",
            "provider": "local_rule",
            "execution_mode": execution_mode,
            "prompt_strategy_version_id": str(version.get("version_id") or ""),
            "prompt_plan_sha256": str(version.get("plan_sha256") or ""),
        }
    max_new = min(
        int(strategy.get("max_new_buys_per_cycle") or 0),
        int(market_strategy_ctx.get("max_new_buys_per_decision") or 0),
    )
    allow_new = bool(market_strategy_ctx.get("allow_new_buys", True))
    actions: list[dict[str, Any]] = []
    positions = state.get("positions") or {}
    position_policy = strategy.get("position") or {}
    for candidate in candidates:
        if len(actions) >= max_new or not allow_new:
            break
        code = normalize_code(candidate.get("code") or "")
        if not code:
            continue
        existing_qty = position_qty(positions.get(code) or {})
        if existing_qty > 0 and not bool(position_policy.get("allow_add", False)):
            continue
        provisional_shares = (
            int(position_policy.get("value") or 0)
            if str(position_policy.get("type") or "") == "fixed_shares"
            else 100
        )
        actions.append({
            "action": "BUY",
            "code": code,
            "name": str(candidate.get("name") or ""),
            "shares": provisional_shares,
            "reason": (
                "冻结文字策略的选股条件已通过；成交前由本地引擎复核入场条件并按冻结仓位规则计算股数"
            ),
            "prompt_strategy_version_id": str(version.get("version_id") or ""),
        })
    return {
        "summary": (
            f"冻结文字策略本地决策：{len(actions)}个标的进入买前复核"
            if actions
            else "冻结文字策略本轮没有可执行的新买入"
        ),
        "actions": actions,
        "model": "LOCAL_PROMPT_RULE_ENGINE",
        "provider": "local_rule",
        "execution_mode": execution_mode,
        "prompt_strategy_version_id": str(version.get("version_id") or ""),
        "prompt_plan_sha256": str(version.get("plan_sha256") or ""),
    }


def evaluate_prompt_entry_before_buy(
    candidate: Mapping[str, Any],
    *,
    code: str,
    name: str,
    quote: Mapping[str, Any],
    position: Mapping[str, Any] | None = None,
    account_cash: float | None = None,
    evaluated_at: datetime | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    version_id = str(candidate.get("prompt_strategy_version_id") or "")
    if not version_id:
        return None, None, ""
    store = PromptStrategyStore()
    version = store.get_version(version_id)
    active = store.active_version()
    if version is None or active is None or active.get("version_id") != version_id:
        return None, None, "文字策略候选版本已失效，请等待新版本重新选股"
    selection_audit = candidate.get("prompt_rule_audit")
    if not isinstance(selection_audit, Mapping):
        return None, None, "文字策略候选缺少选股审计"
    replay = replay_rule_evaluation_audit(
        dict(selection_audit),
        plan=dict(version.get("execution_plan") or {}),
    )
    if not replay.get("ok") or str((selection_audit.get("evaluation") or {}).get("status")) != "true":
        return None, None, "文字策略候选选股审计无法回放"
    try:
        plan = version.get("execution_plan") or {}
        entry_minimum_bars = max(
            1,
            min(
                500,
                int(
                    ((plan.get("stage_requirements") or {}).get("entry") or {}).get(
                        "minimum_bars"
                    )
                    or 1
                ),
            ),
        )
        bar_status = str(
            (((plan.get("strategy") or {}).get("data_contract") or {}).get(
                "bar_status"
            ))
            or "closed"
        )
        rows = load_prompt_strategy_rows(
            code,
            quote=quote,
            count=min(
                501,
                entry_minimum_bars + (1 if bar_status == "closed" else 0),
            ),
        )
        total_qty = position_qty(dict(position or {}))
        available_qty = available_to_sell(dict(position or {})) if position else 0
        avg_cost = _safe_float((position or {}).get("avg_cost"), 0.0)
        price = _safe_float(quote.get("price"), 0.0)
        result = evaluate_frozen_strategy_stage(
            version,
            "entry",
            rows,
            code=code,
            name=name,
            runtime_facts={
                "account.cash": account_cash,
                "position.quantity": total_qty,
                "position.available_shares": available_qty,
                "position.avg_cost": avg_cost,
                "position.pnl_pct": (
                    (price / avg_cost - 1.0) * 100.0
                    if price > 0 and avg_cost > 0
                    else None
                ),
                "position.hold_days": (
                    holding_days(dict(position), today_key())
                    if position
                    else 0
                ),
            },
            data_context=prompt_strategy_data_context(
                quote,
                evaluated_at or datetime.now(),
            ),
        )
        recorded = store.record_evaluation(version_id, result["audit"])
        result["evaluation_id"] = recorded["evaluation_id"]
    except Exception as exc:
        return None, version, f"文字策略买前复核失败（{type(exc).__name__}）"
    if str(result["evaluation"].get("status") or "") != "true":
        status = str(result["evaluation"].get("status") or "unknown")
        return result, version, f"文字策略入场条件复核为{status}，本轮不买入"
    return result, version, ""


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
        if any(is_niuone_strategy(strategy_id) for strategy_id in enabled):
            custom += (
                "\n- 牛牛战法执行层动态风险预算：进攻/轮动/修复/防守的单笔权益风险分别≤1.50%/1.00%/0.60%/0.30%，"
                "策略内组合风险≤4.50%/3.00%/1.80%/0.90%，总仓≤70%/55%/35%/20%，主题风险≤3.00%/2.00%/1.20%/0.60%，主题敞口≤55%/40%/25%/12%；仅市场复合硬停止禁止新仓。"
                f"领涨/转强/启动/试仓单票30%/25%/15%/6.25%仅为绝对上限，同一主题最多2只、当日跨轮最多新开{NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY}只、同时最多持有{NIUONE_MAX_OPEN_POSITIONS}只。"
                "\n- 牛牛战法按主线酝酿→主升→高潮→分歧→退幕识别；试仓只参与酝酿候选和启动早段，主升阶段围绕启动/领涨，高潮不追普遍新仓，分歧只观察核心股调整后转强或减仓，持续回落不触发买点，退幕只退出。最近30根日K还须满足：左侧至少回落5日和8%，低点后至少修复3日和6%，收复左侧跌幅须在60%（含）至200%（不含）之间，并确认右侧持续抬高；达到200%后不再按早期试仓。"
                "试仓在进攻/轮动/修复/防守的单笔权益风险分别≤0.35%/0.30%/0.25%/0.15%，以右侧最近3根日K低点为止损；试仓/启动持仓浮盈在2%～12%、仍处主升且个股保持强势领涨时，跨日延续先向10%上限加仓，主线确认后再向20%上限加仓，每级一次，分歧/高潮/退幕不加仓。"
                "\n- 牛牛战法退出：试仓所属题材首次进入退幕即退出，3个交易日未延续右侧趋势也退出；成熟路径另按连续两个交易日跌出行业前三龙头梯队、主线连续转弱、市场硬停止叠加退幕和策略时间窗退出；高潮且不亏先减仓1/3，进攻/修复/防守试仓盘中达到0.75R先减仓50%，轮动试仓及成熟路径达到1R先减仓45%，余仓成本保护并按2ATR跟踪。"
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
        niuone_enabled=any(is_niuone_strategy(strategy_id) for strategy_id in enabled),
        prompt_strategy_enabled=STRATEGY_SOURCE_PRESET_TEXT in enabled,
    )


def active_strategy_ids_for_decision() -> set[str]:
    return enabled_strategy_ids(
        os.environ.get(PERSONA_STRATEGY_ENV),
        os.environ.get(STRATEGY_SOURCE_ENV),
        os.environ.get(ACTIVE_STRATEGY_ENV),
    )


def position_strategy_ids_for_prompt(positions: list[dict[str, Any]]) -> set[str]:
    """Collect entry strategies from live position marks without using active-suite state."""
    known = known_strategy_ids()
    strategy_ids: set[str] = set()
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        mark = pos.get("strategy_mark") if isinstance(pos.get("strategy_mark"), dict) else {}
        values = [position_entry_strategy(pos), mark.get("component_strategy_id")]
        strategy_ids.update(str(value) for value in values if str(value or "") in known)
    return strategy_ids


def preset_position_policy_context(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return private frozen policy data for prompt-driven open positions."""
    positions = state.get("positions") if isinstance(state.get("positions"), Mapping) else {}
    contexts: list[dict[str, Any]] = []
    for raw_code, raw_pos in positions.items():
        if not isinstance(raw_pos, Mapping):
            continue
        pos = dict(raw_pos)
        if position_qty(pos) <= 0 or position_entry_strategy(pos) != STRATEGY_SOURCE_PRESET_TEXT:
            continue
        code = normalize_code(str(pos.get("code") or raw_code or ""))
        if not code:
            continue
        contexts.append({
            "code": code,
            "name": str(pos.get("name") or ""),
            "snapshot": _json_safe_copy(pos.get("preset_strategy_snapshot") or {}),
            "interpretation": _json_safe_copy(pos.get("preset_strategy_interpretation") or {}),
            "interpretation_sha256": str(
                pos.get("preset_strategy_interpretation_sha256") or ""
            ),
        })
    return contexts


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
    
    strategy_suite = current_strategy_suite()
    compact_candidates = candidates[:100] if strategy_suite == STRATEGY_SOURCE_PRESET_TEXT else candidates[:8]
    # 自适应参数（市场情绪驱动）
    adaptive = get_adaptive_params()
    # 多战法上下文：统计战法分布，给每个候选标注最优战法
    preset_strategy_text = current_preset_strategy_text()
    active_strategy_ids = active_strategy_ids_for_decision()
    portfolio_positions = [p for p in (portfolio.get("positions") or []) if isinstance(p, dict)]
    position_strategy_ids = position_strategy_ids_for_prompt(portfolio_positions)
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
    builtin_position_strategy_ids = position_strategy_ids - {
        STRATEGY_SOURCE_PRESET_TEXT
    }
    builtin_position_exit_section = (
        build_position_exit_prompt_section(
            builtin_position_strategy_ids,
            b3_exit_hhmm=B3_EXIT_HHMM,
            time_exit_hhmm=TIME_EXIT_HHMM,
        )
        if builtin_position_strategy_ids
        else ""
    )
    private_preset_position_contexts = portfolio.get("_preset_position_policy_context")
    private_preset_position_contexts = (
        private_preset_position_contexts
        if isinstance(private_preset_position_contexts, list)
        else []
    )
    frozen_preset_exit_section = format_frozen_preset_exit_section(
        private_preset_position_contexts
    )
    position_exit_section = "\n\n".join(
        section
        for section in (frozen_preset_exit_section, builtin_position_exit_section)
        if section
    ) or "当前没有带有效 strategy_mark 的持仓，无需加载历史持仓退出规则。"
    position_by_code = {
        normalize_code(pos.get("code") or ""): pos
        for pos in portfolio_positions
        if normalize_code(pos.get("code") or "")
    }
    # Build compact candidate list with strategy context
    cand_lines = []
    for c in compact_candidates:
        if strategy_suite == STRATEGY_SOURCE_PRESET_TEXT:
            facts = preset_candidate_facts(c)
            cand_lines.append("  " + json.dumps(facts, ensure_ascii=False, sort_keys=True))
            continue
        strat = c.get("best_strategy", "")
        strat_label = strategy_labels.get(strat, strat)
        zettaranc_flow_detail = ""
        if is_zettaranc_strategy(strat) and c.get("industry_flow_matched"):
            zettaranc_flow_detail = (
                f"行业主力净流入排名:{c.get('industry_flow_rank','-')}/"
                f"{c.get('industry_flow_rank_total','-')} "
                f"资金加分:+{c.get('industry_flow_adjustment',0)} "
            )
        elif is_zettaranc_strategy(strat) and c.get("industry_outflow_matched"):
            zettaranc_flow_detail = (
                f"行业主力净流出排名:{c.get('industry_outflow_rank','-')}/"
                f"{c.get('industry_outflow_rank_total','-')} "
                f"净额:{c.get('industry_outflow_net_yi','-')}亿 "
            )
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
                f"隔夜美股:{c.get('overnight_us_tone_label','-')}/"
                f"{c.get('overnight_us_sector') or '无行业映射'} "
                f"消息面:{c.get('news_tone_label','未检查')} "
                f"外部确认调整:{c.get('external_context_adjustment','-')} "
            )
        elif is_niuone_strategy(strat):
            tide_detail = (
                f"市场:{c.get('market_regime','-')}/{c.get('market_score','-')} "
                f"题材:{c.get('signal_theme') or c.get('industry') or c.get('sector') or '-'} "
                f"行业:{c.get('industry') or c.get('sector') or '-'} "
                f"归因:{c.get('signal_theme_attribution_score','-')}/"
                f"{c.get('signal_theme_attribution_weight','-')} "
                f"主线:{mainline_state_label(c.get('mainline_state'))}/{c.get('mainline_score','-')} "
                f"模式:{mainline_mode_label(c.get('mainline_mode'))} 核心:{c.get('mainline_primary') or '-'}"
                f"/{c.get('mainline_secondary') or '-'} "
                f"强股:{c.get('strong_stock_count','-')} 有效强股:{c.get('effective_strong_count','-')} "
                f"龙头集中度:{c.get('leader_concentration','-')} 个股角色:{stock_role_label(c.get('stock_role'))} "
                f"个股强度:{c.get('stock_strong_score','-')} 主线排名:{c.get('stock_sector_rank','-')} "
                f"止损:{c.get('stop_price','-')}({c.get('stop_distance_pct','-')}%) "
                f"有效损失:{c.get('effective_loss_distance_pct','-')}% "
                f"单笔预算:{c.get('per_trade_risk_budget_pct','-')}% "
                f"动态仓位上限:{c.get('max_position_pct_by_risk','-')}% "
                f"消息面:{c.get('news_tone_label','未检查')} "
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
            f"{'距EMA20' if is_dynamic_risk_strategy(strat) else '距BBI'}:{c.get('distance_pct')}% "
            f"{zettaranc_flow_detail}"
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
        elif is_niuone_strategy(strat):
            tide_detail = (
                f" 题材:{c.get('signal_theme') or c.get('industry') or c.get('sector') or '-'}"
                f" 行业:{c.get('industry') or c.get('sector') or '-'}"
                f" 主线:{mainline_state_label(c.get('mainline_state'))}/{c.get('mainline_score','-')}"
            )
        held_candidate_lines.append(
            f"  {code} {c.get('name') or pos.get('name')} 当前仓位{pos.get('position_pct')}% "
            f"盈亏{pos.get('pnl_pct')}% 今日{pos.get('today_pnl_pct')}% "
            f"候选战法:{strat_label} 评分:{c.get('best_score')}/{c.get('score_total',10)} "
            f"基准:{c.get('entry_threshold','-')} "
            f"{'距EMA20' if is_dynamic_risk_strategy(strat) else '距BBI'}:{c.get('distance_pct')}%{tide_detail} "
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
    preset_output_lines: list[str] = []
    preset_interpretation_schema = ""
    if strategy_suite == STRATEGY_SOURCE_PRESET_TEXT:
        preset_output_lines.extend([
            "- 必须把当前文字原文解释为可审计的 selection_rules、entry_rules、exit_rules、position_rules、time_rules、ambiguities 六组字符串数组；未写明的卖出、仓位或时间纪律必须采用保守规则补齐，不能省略字段。",
            "- BUY只能选择上方中性行情事实池中真实存在的代码；reason必须写明命中的文字规则、关键行情事实、仓位依据和失效条件。",
            "- 对已有预设文字策略持仓加仓时，必须使用该持仓买入时冻结的完整结构化规则，并原样返回相同的六组规则；不得重新解释或混用当前其他版本。",
        ])
        preset_interpretation_schema = '''  "strategy_interpretation":{
    "selection_rules":["选股规则"],
    "entry_rules":["买入触发"],
    "exit_rules":["卖出/止损止盈"],
    "position_rules":["仓位规则"],
    "time_rules":["时间纪律"],
    "ambiguities":[]
  },
'''
    if private_preset_position_contexts:
        preset_output_lines.append(
            "- 对预设文字历史持仓SELL时，reason必须写明该持仓策略指纹及命中的买入时冻结退出规则；无法匹配冻结规则则HOLD。"
        )
    preset_output_requirements = (
        "预设文字策略额外输出要求：\n" + "\n".join(preset_output_lines)
        if preset_output_lines
        else ""
    )
    prompt = f"""你是A股模拟账户交易决策器。账户初始资金100万，只做A股模拟交易，不是真实下单。
必须遵守：
{trade_discipline_text}

当前激活策略：{strategy_source_label}
当前选股范围：{friendly_stock_universe(current_stock_universe())}

【当前新开仓策略规则（只控制BUY）】
{active_strategy_section}

【已有持仓退出规则（按strategy_mark动态装载）】
{position_exit_section}

隔离要求：本轮BUY只能依据当前新开仓策略及其候选，不得引用、混合或补充其他未启用策略。SELL必须逐只读取已有持仓的strategy_mark并执行上方对应的原策略退出纪律；不得因为当前激活策略变化而改写历史持仓归因。即使候选为空、盘面禁止开仓或日内亏损预算触发，也必须继续判断已有持仓的SELL/HOLD。

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

本次候选股（预设文字策略为中性行情事实池；内置策略标注最优战法+评分）：
{candidates_section}

当前持仓与候选池重合（加仓/减仓/继续观察的重点）：
{held_candidates_section}

加仓语义与纪律：
- 对当前账户JSON里已有持仓输出 BUY，表示加仓/补仓；shares 是本次新增股数，不是目标总股数。
- 加仓只用于顺势确认或强势回踩重新达标；亏损扩大、跌破原止损、今日新买T+1锁仓、盘面谨慎/防守时，不得为了摊低成本而加仓。
- 牛牛试仓/启动持仓首次加仓仅限浮盈{NIUONE_MARKUP_UPGRADE_MIN_PNL_PCT:g}%～{NIUONE_MARKUP_UPGRADE_MAX_PNL_PCT:g}%、生命周期主升且个股保持强势领涨：启动跨日延续先向{NIUONE_MARKUP_EARLY_UPGRADE_POSITION_CAP_PCT:g}%上限加仓，主线完全确认后向{NIUONE_MARKUP_UPGRADE_POSITION_CAP_PCT:g}%上限加仓。确认领涨仓随后可重复执行波段再平衡：有效回落或横盘先减仓1/3，只有重新转强价被收复、生命周期回到主升且个股恢复强势领涨才补回风险上限；补回后必须等待下一次独立回撤，不设终身加仓次数上限。每笔仍取风险预算和阶段/单票上限的较小值，shares 只填写当前仓位到目标仓位的差额；高潮、未转强分歧、退幕不得加仓。
- 加仓理由必须写明：原入场战法、当前盈亏/仓位、加仓后仓位占比、失效/止损条件，以及为何优于新开仓或继续HOLD。

{preset_output_requirements}

严格返回JSON，不要markdown，不要解释，格式：
{{
  "summary":"一句中文结论（含战法偏好+总体判断）",
{preset_interpretation_schema}
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
    if DECISION_REASONING_EFFORT:
        payload["reasoning_effort"] = DECISION_REASONING_EFFORT

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
    localize_decision_display_fields(result)
    audit_generated_at = now_ts()
    if strategy_suite == STRATEGY_SOURCE_PRESET_TEXT:
        interpretation = normalize_preset_strategy_interpretation(
            result.get("strategy_interpretation")
        )
        if interpretation is not None:
            result["strategy_interpretation"] = interpretation
        snapshot = build_preset_strategy_snapshot(
            preset_strategy_text,
            captured_at=audit_generated_at,
        )
        result["preset_strategy_audit"] = build_preset_decision_audit(
            snapshot=snapshot,
            candidates=compact_candidates,
            interpretation=result.get("strategy_interpretation") or {},
            prompt=prompt,
            generated_at=audit_generated_at,
        )
    if private_preset_position_contexts:
        result["preset_exit_audit"] = build_preset_exit_audit(
            private_preset_position_contexts,
            prompt=prompt,
            generated_at=audit_generated_at,
        )
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
    localize_decision_display_fields(decision)
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
        if DECISION_REASONING_EFFORT:
            payload["reasoning_effort"] = DECISION_REASONING_EFFORT
        content = request_chat_content(
            base_url,
            api_key,
            payload,
            MODEL,
            max_retries=2,
            timeout=DECISION_REQUEST_TIMEOUT,
        )
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
        localize_decision_display_fields(decision)
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
    evaluated_at: datetime | None = None,
) -> list[dict[str, Any]]:
    executed = []
    cand_by_code = {normalize_code(c.get("code", "")): c for c in candidates}
    positions = state.setdefault("positions", {})
    cash = float(state.get("cash") or 0)
    new_buys = 0
    market_strategy_ctx = market_strategy_ctx or current_market_strategy_context()
    execution_context = decision.pop("_niuone_execution_context", {})
    execution_context = (
        execution_context if isinstance(execution_context, dict) else {}
    )
    decision.setdefault("execution_blocks", [])
    effective_max_open_positions = int(market_strategy_ctx.get("max_open_positions", MAX_OPEN_POSITIONS))
    effective_max_new_buys = int(market_strategy_ctx.get("max_new_buys_per_decision", MAX_NEW_BUYS_PER_DECISION))
    niuone_opened_today = niuone_opened_position_codes_on_date(state)
    allow_market_guidance_buys = bool(market_strategy_ctx.get("allow_new_buys", True))
    daily_loss_budget_exceeded, daily_loss_budget_pnl = check_daily_loss_budget(state)
    execution_date = evaluated_at.strftime("%Y-%m-%d") if evaluated_at else today_key()
    if not trade_allowed:
        return executed
    for action in (decision.get("actions") or [])[:5]:
        current_allowed, current_reason = is_a_share_execution_time(evaluated_at)
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
            add_execution_block(
                decision,
                code,
                "模型未给出有效仓位 shares，本轮不自动补默认仓位",
                category="invalid_order_quantity",
            )
            continue
        if shares % 100 != 0:
            add_execution_block(
                decision,
                code,
                f"模型仓位{shares}股不是100股整数倍，本轮不自动取整",
                category="invalid_order_quantity",
            )
            continue
        if act == "BUY":
            prompt_entry_result: dict[str, Any] | None = None
            prompt_strategy_version: dict[str, Any] | None = None
            if daily_loss_budget_exceeded:
                add_execution_block(
                    decision,
                    code,
                    f"日内亏损预算已触发({daily_loss_budget_pnl:.1f}%)，仅暂停BUY，SELL继续执行",
                    category="daily_loss_budget",
                )
                continue
            if not candidate or not candidate_in_stock_universe(candidate):
                add_execution_block(
                    decision,
                    code,
                    "买入标的不在当前选股范围",
                    category="candidate_eligibility",
                )
                continue
            candidate_strategy_id = str(
                candidate.get("best_strategy")
                or candidate.get("buy_strategy")
                or candidate.get("strategy_id")
                or ""
            )
            preset_strategy_buy = (
                candidate_strategy_id == STRATEGY_SOURCE_PRESET_TEXT
                or isinstance(decision.get("preset_strategy_audit"), Mapping)
            )
            versioned_prompt_buy = bool(
                str(candidate.get("prompt_strategy_version_id") or "")
            )
            if preset_strategy_buy:
                if current_strategy_suite() != STRATEGY_SOURCE_PRESET_TEXT:
                    add_execution_block(
                        decision,
                        code,
                        "当前激活策略不是预设文字策略，旧文字策略BUY已失效",
                        category="strategy_policy",
                    )
                    continue
                if versioned_prompt_buy:
                    (
                        prompt_entry_result,
                        prompt_strategy_version,
                        preset_audit_error,
                    ) = evaluate_prompt_entry_before_buy(
                        candidate,
                        code=code,
                        name=str(name),
                        quote=q,
                        position=positions.get(code),
                        account_cash=cash,
                        evaluated_at=evaluated_at,
                    )
                else:
                    preset_audit_error = validate_preset_buy_audit(
                        decision.get("preset_strategy_audit"),
                        code=code,
                        candidates=candidates,
                        current_text=current_preset_strategy_text(),
                    )
                if preset_audit_error:
                    add_execution_block(
                        decision,
                        code,
                        preset_audit_error,
                        category="strategy_policy",
                    )
                    continue
            if not allow_market_guidance_buys:
                add_execution_block(
                    decision,
                    code,
                    f"盘面指引为{market_strategy_ctx.get('tone_label', '防守')}，暂停买入",
                    category="market_guidance",
                )
                continue
            buy_strategy = (
                STRATEGY_SOURCE_PRESET_TEXT
                if preset_strategy_buy
                else classify_buy_strategy(reason, candidate)
            )
            niuone_selection_context = (
                niuone_candidate_selection_context(
                    candidate,
                    candidates,
                    buy_strategy,
                )
                if is_niuone_strategy(buy_strategy)
                else {}
            )
            blockers = candidate_buy_blockers(candidate)
            if blockers:
                add_execution_block(
                    decision,
                    code,
                    "买入拦截：" + "、".join(blockers),
                    category="candidate_eligibility",
                )
                continue
            if is_niuone_strategy(buy_strategy) and quote_is_at_limit_up(code, str(name), q):
                add_execution_block(
                    decision,
                    code,
                    "牛牛战法不在涨停价模拟买入，改选行业龙头梯队后续可交易标的",
                    category="market_mechanics",
                )
                continue
            existing_pos = positions.get(code)
            old_qty = position_qty(existing_pos or {})
            existing_entry_strategy = position_entry_strategy(existing_pos or {}) if old_qty > 0 else ""
            if (
                old_qty > 0
                and existing_entry_strategy == STRATEGY_SOURCE_PRESET_TEXT
                and not preset_strategy_buy
            ):
                add_execution_block(
                    decision,
                    code,
                    "预设文字策略持仓只能按买入时冻结的同版本、同解释规则加仓",
                    category="strategy_policy",
                )
                continue
            if preset_strategy_buy and old_qty > 0:
                same_version = (
                    str((existing_pos or {}).get("prompt_strategy_version_id") or "")
                    == str(candidate.get("prompt_strategy_version_id") or "")
                    if versioned_prompt_buy
                    else str(
                        ((existing_pos or {}).get("preset_strategy_snapshot") or {}).get(
                            "text_sha256"
                        )
                        or ""
                    )
                    == str(
                        ((decision.get("preset_strategy_audit") or {}).get("snapshot") or {}).get(
                            "text_sha256"
                        )
                        or ""
                    )
                    and str(
                        (existing_pos or {}).get("preset_strategy_interpretation_sha256")
                        or ""
                    )
                    == str(
                        (decision.get("preset_strategy_audit") or {}).get(
                            "interpretation_sha256"
                        )
                        or ""
                    )
                )
                if existing_entry_strategy != STRATEGY_SOURCE_PRESET_TEXT or not same_version:
                    add_execution_block(
                        decision,
                        code,
                        "不得用不同版本或不同解释的文字策略加仓，也不得混入其他策略持仓",
                        category="strategy_policy",
                    )
                    continue
            if (
                old_qty > 0
                and str(
                    (existing_pos or {}).get("initial_buy_strategy")
                    or existing_entry_strategy
                ) == "niu_reversal_probe"
                and int(((existing_pos or {}).get("buy_date_lots") or {}).get(execution_date, 0) or 0) > 0
            ):
                add_execution_block(
                    decision,
                    code,
                    "牛牛试仓当日只建立一次轻仓，等待后续日线确认",
                    category="lifecycle_rule",
                )
                continue
            niuone_upgrade_source = str(
                (existing_pos or {}).get("initial_buy_strategy")
                or existing_entry_strategy
            )
            niuone_rebalance_reentry = bool(
                old_qty > 0
                and buy_strategy == "niu_leader"
                and (existing_pos or {}).get(
                    "niuone_markup_rebalance_armed"
                ) is True
            )
            niuone_stage_add_attempt = bool(
                old_qty > 0
                and (
                    niuone_upgrade_source in {
                        "niu_reversal_probe",
                        "niu_emerging",
                    }
                    or niuone_rebalance_reentry
                )
                and is_niuone_strategy(buy_strategy)
            )
            current_pnl_pct = (
                (float(price) / float((existing_pos or {}).get("avg_cost")) - 1.0)
                * 100.0
                if niuone_stage_add_attempt
                and _safe_float((existing_pos or {}).get("avg_cost"), 0.0) > 0
                else 0.0
            )
            niuone_upgrade_blocker = (
                niuone_markup_rebalance_reentry_blocker(
                    niuone_upgrade_source,
                    existing_pos or {},
                    candidate,
                    current_price=float(price),
                    current_pnl_pct=current_pnl_pct,
                )
                if niuone_rebalance_reentry
                else niuone_markup_upgrade_blocker(
                    niuone_upgrade_source,
                    candidate,
                    current_pnl_pct=current_pnl_pct,
                )
                if niuone_stage_add_attempt
                else None
            )
            if (
                niuone_upgrade_blocker is None
                and not niuone_rebalance_reentry
                and buy_strategy == "niu_emerging"
                and (existing_pos or {}).get(
                    "niuone_markup_early_scale_in_done"
                ) is True
            ):
                niuone_upgrade_blocker = "牛牛主升早期加仓已经执行，本阶段不重复加仓"
            elif (
                niuone_upgrade_blocker is None
                and not niuone_rebalance_reentry
                and buy_strategy == "niu_leader"
                and (existing_pos or {}).get(
                    "niuone_markup_confirmed_scale_in_done"
                ) is True
            ):
                niuone_upgrade_blocker = "牛牛确认主升加仓已经执行，本阶段不重复加仓"
            if niuone_upgrade_blocker:
                add_execution_block(
                    decision,
                    code,
                    niuone_upgrade_blocker,
                    category="lifecycle_rule",
                )
                continue
            niuone_markup_scale_add = bool(
                niuone_stage_add_attempt
                and niuone_upgrade_blocker is None
            )
            niuone_upgrade_add = bool(
                niuone_markup_scale_add
                and existing_entry_strategy != buy_strategy
            )
            if (
                old_qty > 0
                and is_dynamic_risk_strategy(buy_strategy)
                and existing_entry_strategy != buy_strategy
                and not niuone_upgrade_add
            ):
                suite_label = "牛牛战法" if is_niuone_strategy(buy_strategy) else "板块潮汐"
                add_execution_block(
                    decision,
                    code,
                    f"{suite_label}不得把{buy_strategy_label(buy_strategy)}加到原{buy_strategy_label(existing_entry_strategy)}持仓形成混合策略",
                    category="lifecycle_rule",
                )
                continue
            open_position_limit = (
                min(effective_max_open_positions, NIUONE_MAX_OPEN_POSITIONS)
                if is_niuone_strategy(buy_strategy)
                else effective_max_open_positions
            )
            if old_qty <= 0 and open_position_count(positions) >= open_position_limit:
                if is_niuone_strategy(buy_strategy) and open_position_limit == NIUONE_MAX_OPEN_POSITIONS:
                    limit_reason = f"牛牛战法最多同时持有{NIUONE_MAX_OPEN_POSITIONS}只"
                else:
                    limit_reason = (
                        f"盘面动态持仓已达{open_position_limit}只上限"
                        f"（静态{MAX_OPEN_POSITIONS}只）"
                    )
                add_execution_block(
                    decision,
                    code,
                    limit_reason,
                    category="position_capacity",
                )
                continue
            if old_qty <= 0 and is_niuone_strategy(buy_strategy):
                action["niuone_daily_new_position_count_before"] = len(
                    niuone_opened_today
                )
                action["niuone_daily_new_position_limit"] = (
                    NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY
                )
                if (
                    len(niuone_opened_today)
                    >= NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY
                ):
                    add_execution_block(
                        decision,
                        code,
                        "牛牛战法当日新开仓已达"
                        f"{NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY}只上限"
                        "（跨决策轮次累计）",
                        category="position_capacity",
                    )
                    continue
            if old_qty <= 0 and new_buys >= effective_max_new_buys:
                add_execution_block(
                    decision,
                    code,
                    f"盘面动态本轮新开仓已达{effective_max_new_buys}笔上限",
                    category="position_capacity",
                )
                continue

            total_equity = portfolio_total_equity_for_limits(cash, positions)
            current_position_value = position_market_value(existing_pos or {}, float(price))
            current_market_value = portfolio_market_value(positions)
            if existing_pos:
                current_market_value = max(0.0, current_market_value - position_market_value(existing_pos) + current_position_value)
            if versioned_prompt_buy:
                quantity_policy = (
                    (prompt_entry_result or {}).get("action_intent") or {}
                ).get("quantity_policy") or {}
                resolved_size = resolve_prompt_order_shares(
                    quantity_policy,
                    price=float(price),
                    total_equity=total_equity,
                    current_position_value=current_position_value,
                    existing_quantity=old_qty,
                )
                if resolved_size.get("error"):
                    add_execution_block(
                        decision,
                        code,
                        str(resolved_size["error"]),
                        category="strategy_policy",
                    )
                    continue
                action["requested_shares_before_prompt_policy"] = shares
                shares = int(resolved_size["shares"])
                action["shares"] = shares
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
            prompt_required_cash_pct = max(
                MIN_CASH_RESERVE_PCT,
                float(
                    market_strategy_ctx.get(
                        "min_cash_reserve_pct",
                        MIN_CASH_RESERVE_PCT,
                    )
                ),
            )
            prompt_total_limit_pct = min(
                MAX_TOTAL_POSITION_PCT,
                float(
                    market_strategy_ctx.get(
                        "max_total_position_pct",
                        MAX_TOTAL_POSITION_PCT,
                    )
                ),
                100.0 - prompt_required_cash_pct,
            )
            if versioned_prompt_buy:
                if position_after_trade_pct > MAX_SINGLE_POSITION_PCT + 1e-9:
                    add_execution_block(
                        decision,
                        code,
                        f"文字策略买入后单票仓位{position_after_trade_pct:.2f}%超过系统硬上限{MAX_SINGLE_POSITION_PCT:g}%",
                        category="risk_ceiling",
                    )
                    continue
                if total_position_after_trade_pct > prompt_total_limit_pct + 1e-9:
                    add_execution_block(
                        decision,
                        code,
                        f"文字策略买入后总仓位{total_position_after_trade_pct:.2f}%超过系统硬上限{prompt_total_limit_pct:g}%",
                        category="risk_ceiling",
                    )
                    continue
            niuone_execution_reference_price = 0.0
            niuone_execution_gap_pct: float | None = None
            niuone_entry_subroute = ""
            niuone_entry_context: dict[str, Any] = {}
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
            elif is_dynamic_risk_strategy(buy_strategy):
                niuone_buy = is_niuone_strategy(buy_strategy)
                dynamic_label = "牛牛战法" if niuone_buy else "板块潮汐"
                exposure_label = "主题" if niuone_buy else "行业"
                risk_persona = "niuone" if niuone_buy else "sector_tide"
                if niuone_buy:
                    niuone_entry_subroute = str(
                        candidate.get("niuone_entry_subroute") or ""
                    )
                    niuone_execution_reference_price = _safe_float(
                        q.get("prev_close") or candidate.get("recent_close"),
                        0.0,
                    )
                    if niuone_execution_reference_price > 0:
                        niuone_execution_gap_pct = round(
                            (
                                float(price) / niuone_execution_reference_price
                                - 1.0
                            ) * 100.0,
                            4,
                        )
                regime = str(candidate.get("market_regime") or "")
                if candidate.get("market_hard_stop") or not candidate.get("market_allows_buys", False):
                    add_execution_block(
                        decision,
                        code,
                        f"{dynamic_label}市场风控禁止新开仓",
                        category="market_guidance",
                    )
                    continue
                allowed_regimes = (
                    NIUONE_ENTRY_REGIMES
                    if niuone_buy
                    else {"offensive", "rotation", "recovery"}
                )
                if regime not in allowed_regimes:
                    add_execution_block(
                        decision,
                        code,
                        f"{dynamic_label}市场状态{regime or '缺失'}不可买入",
                        category="market_guidance",
                    )
                    continue
                if (
                    niuone_buy
                    and old_qty <= 0
                    and niuone_entry_subroute
                    == NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE
                    and (
                        niuone_execution_gap_pct is None
                        or niuone_execution_gap_pct
                        > NIUONE_MARKUP_MOMENTUM_PROBE_MAX_EXECUTION_GAP_PCT
                        + 1e-9
                    )
                ):
                    reason = (
                        "主升动量试仓缺少可核验的前收盘价"
                        if niuone_execution_gap_pct is None
                        else "主升动量试仓执行价较信号收盘高开"
                        f"{niuone_execution_gap_pct:.2f}%超过"
                        f"{NIUONE_MARKUP_MOMENTUM_PROBE_MAX_EXECUTION_GAP_PCT:g}%"
                    )
                    add_execution_block(
                        decision,
                        code,
                        reason,
                        category="market_mechanics",
                    )
                    continue
                if buy_strategy == "niu_reversal_probe" and old_qty > 0:
                    add_execution_block(
                        decision,
                        code,
                        "牛牛试仓只允许一次初始建仓，须满足后续阶段条件再加仓",
                        category="lifecycle_rule",
                    )
                    continue
                if (
                    buy_strategy == "niu_emerging"
                    and old_qty > 0
                    and not niuone_markup_scale_add
                ):
                    add_execution_block(
                        decision,
                        code,
                        "牛牛启动观察仓只在主升早期延续条件满足后加仓",
                        category="lifecycle_rule",
                    )
                    continue
                if buy_strategy == "tide_recovery" and old_qty > 0:
                    today_lots = int(((existing_pos or {}).get("buy_date_lots") or {}).get(execution_date, 0) or 0)
                    if today_lots > 0:
                        add_execution_block(decision, code, "冰点修复观察仓当日禁止加仓，须次日确认")
                        continue

                tide_risk_budget = (
                    niuone_risk_budget(regime, buy_strategy)
                    if niuone_buy
                    else sector_tide_risk_budget(regime)
                )
                single_limit_pct = (
                    float(NIUONE_ABSOLUTE_POSITION_CAP_PCT[buy_strategy])
                    if niuone_buy
                    else strategy_position_limit_pct(buy_strategy)
                )
                if (
                    niuone_buy
                    and old_qty <= 0
                    and niuone_entry_subroute
                    == NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE
                ):
                    single_limit_pct = min(
                        single_limit_pct,
                        NIUONE_MARKUP_MOMENTUM_PROBE_POSITION_CAP_PCT,
                    )
                if niuone_buy and niuone_markup_scale_add:
                    if buy_strategy == "niu_emerging":
                        markup_stage_cap_pct = (
                            NIUONE_MARKUP_EARLY_UPGRADE_POSITION_CAP_PCT
                        )
                    elif niuone_upgrade_source in {
                        "niu_reversal_probe",
                        "niu_emerging",
                    }:
                        markup_stage_cap_pct = (
                            NIUONE_MARKUP_UPGRADE_POSITION_CAP_PCT
                        )
                    else:
                        markup_stage_cap_pct = (
                            NIUONE_ABSOLUTE_POSITION_CAP_PCT["niu_leader"]
                        )
                    single_limit_pct = min(
                        single_limit_pct,
                        markup_stage_cap_pct,
                    )
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
                if (
                    not niuone_buy
                    and exact_total_after_pct > tide_total_limit_pct + 1e-9
                ):
                    add_execution_block(
                        decision,
                        code,
                        f"{dynamic_label}{regime}状态买入后总仓位{exact_total_after_pct:.2f}%超过{tide_total_limit_pct:g}%硬上限",
                        category="risk_ceiling",
                    )
                    continue

                industry = (
                    niuone_position_theme(existing_pos or {})
                    if niuone_buy and old_qty > 0
                    else dynamic_strategy_exposure_key(
                        candidate,
                        buy_strategy,
                    )
                )
                if not industry:
                    add_execution_block(
                        decision,
                        code,
                        f"{dynamic_label}候选缺少{exposure_label}归属",
                        category="risk_input",
                    )
                    continue
                same_industry_positions = [
                    pos_item
                    for pos_code, pos_item in positions.items()
                    if pos_code != code
                    and isinstance(pos_item, dict)
                    and position_qty(pos_item) > 0
                    and dynamic_strategy_exposure_key(
                        pos_item,
                        position_entry_strategy(pos_item),
                    ) == industry
                ]
                if old_qty <= 0 and len(same_industry_positions) >= 2:
                    add_execution_block(
                        decision,
                        code,
                        f"{industry}{exposure_label}已有2只持仓，达到{dynamic_label}上限",
                        category="position_capacity",
                    )
                    continue
                other_industry_value = sum(
                    position_market_value(pos_item) for pos_item in same_industry_positions
                )
                industry_value_after = (
                    position_after_trade_value + other_industry_value
                )
                industry_pct_after = industry_value_after / total_equity * 100 if total_equity > 0 else 100.0
                sector_position_limit_pct = tide_risk_budget["max_sector_position_pct"]
                if (
                    not niuone_buy
                    and industry_pct_after > sector_position_limit_pct + 1e-9
                ):
                    add_execution_block(
                        decision,
                        code,
                        f"{industry}{exposure_label}买入后敞口{industry_pct_after:.2f}%超过{regime}状态动态上限{sector_position_limit_pct:g}%",
                        category="risk_ceiling",
                    )
                    continue

                candidate_stop_price = _safe_float(candidate.get("stop_price"), 0.0)
                existing_stop_price = _safe_float((existing_pos or {}).get("entry_stop_price"), 0.0) if old_qty > 0 else 0.0
                tide_effective_stop_price = max(candidate_stop_price, existing_stop_price)
                actual_stop_distance_pct = structural_stop_distance_pct(float(price), tide_effective_stop_price)
                if niuone_buy:
                    entry_atr = _safe_float(
                        candidate.get("atr") or candidate.get("atr14") or candidate.get("atr20"),
                        0.0,
                    )
                    actual_stop_atr = (
                        (float(price) - tide_effective_stop_price) / entry_atr
                        if entry_atr > 0 and tide_effective_stop_price > 0
                        else 0.0
                    )
                    structural_limits = niuone_structural_stop_limits(
                        regime,
                        buy_strategy,
                        niuone_entry_subroute,
                    )
                    if not niuone_structure_risk_ok(
                        actual_stop_distance_pct,
                        actual_stop_atr,
                        regime,
                        buy_strategy,
                        niuone_entry_subroute,
                    ):
                        add_execution_block(
                            decision,
                            code,
                            f"牛牛战法缺少有效结构止损/ATR，或止损距离超过"
                            f"{structural_limits['max_stop_distance_pct']:g}%/"
                            f"{structural_limits['max_stop_atr']:g}ATR",
                            category="risk_input",
                        )
                        continue
                elif actual_stop_distance_pct <= 0 or actual_stop_distance_pct > 6:
                    add_execution_block(
                        decision,
                        code,
                        f"{dynamic_label}缺少有效结构止损，或止损距离超过6%",
                        category="risk_input",
                    )
                    continue
                tide_gap_buffer_pct = max(
                    _safe_float(candidate.get("gap_buffer_pct"), 0.0),
                    _safe_float((existing_pos or {}).get("gap_buffer_pct"), 0.0),
                )
                if tide_gap_buffer_pct <= 0:
                    add_execution_block(
                        decision,
                        code,
                        f"{dynamic_label}缺少历史跳空/ATR缓冲，动态风险预算无法计算",
                        category="risk_input",
                    )
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
                existing_open_risk_pct = dynamic_strategy_existing_open_risk_pct(
                    positions,
                    total_equity,
                    persona=risk_persona,
                    excluding_code=code,
                )
                existing_sector_risk_pct = (
                    dynamic_strategy_existing_open_risk_pct(
                        positions,
                        total_equity,
                        persona=risk_persona,
                        excluding_code=code,
                        industry=industry,
                    )
                )
                risk_order_ceiling = dynamic_risk_order_ceiling(
                    price=float(price),
                    total_equity=total_equity,
                    cash=cash,
                    current_position_value=current_position_value,
                    current_market_value=current_market_value,
                    other_industry_value=other_industry_value,
                    dynamic_position_cap_pct=tide_dynamic_position_cap_pct,
                    total_position_cap_pct=tide_total_limit_pct,
                    sector_position_cap_pct=sector_position_limit_pct,
                    effective_loss_distance_pct_value=(
                        tide_effective_loss_distance_pct
                    ),
                    max_open_risk_pct=tide_risk_budget["max_open_risk_pct"],
                    existing_open_risk_pct=existing_open_risk_pct,
                    max_sector_risk_pct=(
                        tide_risk_budget["max_sector_risk_pct"]
                    ),
                    existing_sector_risk_pct=existing_sector_risk_pct,
                    required_cash_pct=100.0 - tide_total_limit_pct,
                )
                maximum_permitted_shares = int(
                    risk_order_ceiling["maximum_permitted_shares"]
                )
                model_requested_shares = shares
                risk_ceiling_auto_reduced = bool(
                    niuone_buy
                    and maximum_permitted_shares > 0
                    and model_requested_shares > maximum_permitted_shares
                )
                if risk_ceiling_auto_reduced:
                    shares = maximum_permitted_shares
                    action["shares"] = shares
                action["model_requested_shares"] = model_requested_shares
                action["maximum_permitted_shares"] = (
                    maximum_permitted_shares
                )
                action["maximum_permitted_gross"] = risk_order_ceiling[
                    "maximum_permitted_gross"
                ]
                action["risk_ceiling_binding_constraints"] = list(
                    risk_order_ceiling["binding_constraints"]
                )
                action["risk_ceiling_utilization_pct"] = (
                    round(shares / maximum_permitted_shares * 100.0, 4)
                    if maximum_permitted_shares > 0 else None
                )
                action["risk_ceiling_auto_reduced"] = (
                    risk_ceiling_auto_reduced
                )
                action["position_before_qty"] = old_qty
                action["position_opened"] = old_qty <= 0

                # NiuOne is allowed to preserve an otherwise valid signal by
                # reducing only to the deterministic whole-lot risk ceiling.
                # Recompute every dependent value from the executable size so
                # the same hard limits remain fail-closed after adjustment.
                requested_gross = shares * float(price)
                order_position_pct = position_pct_of_equity(
                    requested_gross,
                    total_equity,
                )
                position_after_trade_value = (
                    current_position_value + requested_gross
                )
                position_after_trade_pct = position_pct_of_equity(
                    position_after_trade_value,
                    total_equity,
                )
                total_position_after_trade_pct = position_pct_of_equity(
                    current_market_value + requested_gross,
                    total_equity,
                )
                exact_position_after_pct = (
                    position_after_trade_value / total_equity * 100
                    if total_equity > 0 else 100.0
                )
                exact_total_after_pct = (
                    (current_market_value + requested_gross)
                    / total_equity * 100
                    if total_equity > 0 else 100.0
                )
                industry_value_after = (
                    position_after_trade_value + other_industry_value
                )
                industry_pct_after = (
                    industry_value_after / total_equity * 100
                    if total_equity > 0 else 100.0
                )
                if exact_total_after_pct > tide_total_limit_pct + 1e-9:
                    add_execution_block(
                        decision,
                        code,
                        f"{dynamic_label}{regime}状态风险裁单后总仓位"
                        f"{exact_total_after_pct:.2f}%仍超过"
                        f"{tide_total_limit_pct:g}%硬上限",
                        category="risk_ceiling",
                    )
                    continue
                if industry_pct_after > sector_position_limit_pct + 1e-9:
                    add_execution_block(
                        decision,
                        code,
                        f"{industry}{exposure_label}风险裁单后敞口"
                        f"{industry_pct_after:.2f}%仍超过{regime}状态"
                        f"动态上限{sector_position_limit_pct:g}%",
                        category="risk_ceiling",
                    )
                    continue
                if exact_position_after_pct > tide_dynamic_position_cap_pct + 1e-9:
                    add_execution_block(
                        decision,
                        code,
                        f"{dynamic_label}{buy_strategy_label(buy_strategy)}买入后仓位{exact_position_after_pct:.2f}%超过风险预算动态上限"
                        f"{tide_dynamic_position_cap_pct:.2f}%（绝对上限{single_limit_pct:g}%）",
                        category="risk_ceiling",
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
                        category="risk_ceiling",
                    )
                    continue
                open_risk_after = (
                    existing_open_risk_pct + tide_position_open_risk_pct
                )
                if open_risk_after > tide_risk_budget["max_open_risk_pct"] + 1e-9:
                    add_execution_block(
                        decision,
                        code,
                        f"{dynamic_label}买入后策略内未实现止损风险{open_risk_after:.3f}%超过{regime}状态组合预算"
                        f"{tide_risk_budget['max_open_risk_pct']:.2f}%",
                        category="risk_ceiling",
                    )
                    continue
                sector_risk_after = (
                    existing_sector_risk_pct + tide_position_open_risk_pct
                )
                if sector_risk_after > tide_risk_budget["max_sector_risk_pct"] + 1e-9:
                    add_execution_block(
                        decision,
                        code,
                        f"{industry}{exposure_label}买入后未实现止损风险{sector_risk_after:.3f}%超过{regime}状态{exposure_label}预算"
                        f"{tide_risk_budget['max_sector_risk_pct']:.2f}%",
                        category="risk_ceiling",
                    )
                    continue
            qty = shares
            gross = qty * float(price)
            fees = calc_trade_fees(gross, "BUY")
            total_cost = gross + fees["total_fee"]
            if total_cost > cash:
                add_execution_block(
                    decision,
                    code,
                    f"模型买入仓位{shares}股现金不足，本轮不自动缩小",
                    category="risk_ceiling",
                )
                continue
            if versioned_prompt_buy:
                equity_after_fees = max(0.0, total_equity - float(fees["total_fee"]))
                cash_after_trade_pct = position_pct_of_equity(
                    cash - total_cost,
                    equity_after_fees,
                )
                if float(cash_after_trade_pct or 0) + 1e-9 < prompt_required_cash_pct:
                    add_execution_block(
                        decision,
                        code,
                        f"文字策略买入后现金{float(cash_after_trade_pct or 0):.2f}%低于系统硬下限{prompt_required_cash_pct:g}%（含交易费用）",
                        category="risk_ceiling",
                    )
                    continue
            if is_zettaranc_strategy(buy_strategy) or is_dynamic_risk_strategy(buy_strategy):
                equity_after_fees = max(0.0, total_equity - float(fees["total_fee"]))
                cash_after_trade = cash - total_cost
                cash_after_trade_pct = position_pct_of_equity(cash_after_trade, equity_after_fees)
                required_cash_pct = (
                    100.0 - float(tide_total_limit_pct)
                    if is_dynamic_risk_strategy(buy_strategy) and tide_total_limit_pct is not None
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
                        category="risk_ceiling",
                    )
                    continue
            prompt_position_binding: dict[str, Any] | None = None
            if versioned_prompt_buy and old_qty <= 0:
                try:
                    prompt_position_binding = PromptStrategyStore().bind_position(
                        code=code,
                        strategy_version_id=str(
                            (prompt_strategy_version or {}).get("version_id") or ""
                        ),
                        entry_evaluation_id=str(
                            (prompt_entry_result or {}).get("evaluation_id") or ""
                        ),
                    )
                except Exception as exc:
                    add_execution_block(
                        decision,
                        code,
                        f"文字策略持仓版本绑定失败（{type(exc).__name__}）",
                        category="strategy_policy",
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
            if buy_strategy == STRATEGY_SOURCE_PRESET_TEXT:
                if versioned_prompt_buy:
                    if old_qty <= 0:
                        pos["prompt_strategy_version_id"] = str(
                            (prompt_strategy_version or {}).get("version_id") or ""
                        )
                        pos["prompt_strategy_plan_sha256"] = str(
                            (prompt_strategy_version or {}).get("plan_sha256") or ""
                        )
                        pos["prompt_strategy_entry_evaluation_id"] = str(
                            (prompt_entry_result or {}).get("evaluation_id") or ""
                        )
                        pos["prompt_strategy_entry_audit_sha256"] = str(
                            ((prompt_entry_result or {}).get("audit") or {}).get(
                                "audit_sha256"
                            )
                            or ""
                        )
                        pos["prompt_strategy_bound_at"] = now_ts()
                        pos["prompt_strategy_binding_id"] = str(
                            (prompt_position_binding or {}).get("binding_id") or ""
                        )
                    pos["prompt_strategy_last_entry_evaluation"] = _json_safe_copy(
                        (prompt_entry_result or {}).get("evaluation") or {}
                    )
                else:
                    preset_audit = decision.get("preset_strategy_audit") or {}
                    if old_qty <= 0:
                        pos["preset_strategy_snapshot"] = _json_safe_copy(
                            preset_audit.get("snapshot") or {}
                        )
                        pos["preset_strategy_interpretation"] = _json_safe_copy(
                            preset_audit.get("interpretation") or {}
                        )
                        pos["preset_strategy_prompt_protocol"] = str(
                            preset_audit.get("prompt_protocol") or ""
                        )
                        pos["preset_strategy_prompt_sha256"] = str(
                            preset_audit.get("prompt_sha256") or ""
                        )
                        pos["preset_strategy_interpretation_sha256"] = str(
                            preset_audit.get("interpretation_sha256") or ""
                        )
                        candidate_pool = preset_audit.get("candidate_pool") or {}
                        pos["preset_strategy_candidate_pool_sha256"] = str(
                            candidate_pool.get("facts_sha256") or ""
                        )
                        pos["preset_strategy_candidate_pool_count"] = int(
                            candidate_pool.get("count") or 0
                        )
                        pos["preset_strategy_entry_audited_at"] = str(
                            preset_audit.get("generated_at") or now_ts()
                        )
                industry = str(
                    candidate.get("industry") or candidate.get("sector") or ""
                ).strip()
                if industry:
                    pos["industry"] = industry
                    pos["sector"] = industry
            if is_zettaranc_strategy(buy_strategy):
                industry = str(candidate.get("industry") or candidate.get("sector") or "").strip()
                if industry:
                    pos["industry"] = industry
                    pos["sector"] = industry
                for key in (
                    "industry_flow_available",
                    "industry_flow_matched",
                    "industry_flow_direction",
                    "industry_flow_rank",
                    "industry_flow_rank_total",
                    "industry_flow_net_yi",
                    "industry_outflow_matched",
                    "industry_outflow_rank",
                    "industry_outflow_rank_total",
                    "industry_outflow_net_yi",
                    "industry_flow_source",
                    "industry_flow_generated_at",
                ):
                    if key in candidate:
                        pos[key] = candidate.get(key)
            if is_dynamic_risk_strategy(buy_strategy):
                niuone_buy = is_niuone_strategy(buy_strategy)
                pos["industry"] = str(
                    candidate.get("industry")
                    or candidate.get("sector")
                    or ""
                ).strip()
                pos["sector"] = pos["industry"]
                pos["entry_stop_price"] = round(tide_effective_stop_price, 3)
                pos["entry_stop_source"] = str(
                    candidate.get("stop_source")
                    or ("niu_structure_low" if niuone_buy else "tide_structure_low")
                )
                pos["entry_stop_distance_pct"] = round(
                    structural_stop_distance_pct(float(price), pos["entry_stop_price"]),
                    3,
                )
                entry_atr = _safe_float(
                    candidate.get("atr") or candidate.get("atr14") or candidate.get("atr20"),
                    0.0,
                )
                pos["entry_atr"] = round(entry_atr, 3)
                pos["entry_atr_period"] = int(_safe_float(candidate.get("atr_period"), 14.0))
                pos["entry_atr20"] = round(entry_atr, 3)
                pos["gap_buffer_pct"] = round(tide_gap_buffer_pct, 3)
                pos["execution_buffer_pct"] = round(tide_execution_buffer_pct, 3)
                pos["effective_loss_distance_pct"] = round(tide_effective_loss_distance_pct, 3)
                pos["position_open_risk_pct"] = round(tide_position_open_risk_pct, 4)
                pos["dynamic_position_cap_pct"] = round(tide_dynamic_position_cap_pct, 3)
                pos["absolute_position_cap_pct"] = round(single_limit_pct, 3)
                entry_market_regime = str(
                    pos.get("entry_market_regime")
                    or pos.get("risk_budget_regime")
                    or pos.get("market_regime")
                    or candidate.get("market_regime")
                    or ""
                )
                pos["risk_budget_regime"] = str(candidate.get("market_regime") or "")
                pos["per_trade_risk_budget_pct"] = tide_risk_budget.get("per_trade_risk_pct")
                pos["max_open_risk_pct"] = tide_risk_budget.get("max_open_risk_pct")
                pos["max_sector_risk_pct"] = tide_risk_budget.get("max_sector_risk_pct")
                pos["max_total_position_pct"] = tide_risk_budget.get("max_total_position_pct")
                pos["max_sector_position_pct"] = tide_risk_budget.get("max_sector_position_pct")
                pos["entry_market_regime"] = entry_market_regime
                pos["market_regime"] = str(candidate.get("market_regime") or "")
                pos["sector_score"] = candidate.get("sector_score")
                pos["sector_status"] = candidate.get("sector_status")
                pos["stock_sector_rank"] = candidate.get("stock_sector_rank")
                if niuone_buy:
                    reversal_buy = buy_strategy == "niu_reversal_probe"
                    signal_theme = niuone_candidate_theme(candidate)
                    if old_qty <= 0:
                        pos["entry_theme"] = signal_theme
                        pos["active_theme"] = signal_theme
                        pos["entry_stock_activity_score"] = candidate.get(
                            "stock_activity_score"
                        )
                        pos["entry_stock_market_amount_percentile"] = candidate.get(
                            "stock_market_amount_percentile"
                        )
                        pos["entry_stock_theme_amount_percentile"] = candidate.get(
                            "stock_theme_amount_percentile"
                        )
                        pos["entry_stock_activity_confirmed"] = bool(
                            candidate.get("stock_activity_confirmed")
                        )
                        pos["entry_theme_basis"] = str(
                            candidate.get("theme_basis") or ""
                        )
                        pos["entry_theme_attribution_score"] = candidate.get(
                            "signal_theme_attribution_score"
                        )
                        pos["entry_theme_attribution_weight"] = candidate.get(
                            "signal_theme_attribution_weight"
                        )
                        pos["entry_theme_historical_prior_score"] = (
                            candidate.get(
                                "signal_theme_historical_prior_score"
                            )
                        )
                        pos["entry_theme_cohort_alignment_score"] = candidate.get(
                            "signal_theme_cohort_alignment_score"
                        )
                        pos["entry_theme_peer_resonance_score"] = candidate.get(
                            "signal_theme_peer_resonance_score"
                        )
                        pos["entry_theme_return_correlation_score"] = candidate.get(
                            "signal_theme_return_correlation_score"
                        )
                        pos["entry_theme_return_correlation_rank_score"] = candidate.get(
                            "signal_theme_return_correlation_rank_score"
                        )
                        pos["entry_theme_return_correlation_observation_count"] = candidate.get(
                            "signal_theme_return_correlation_observation_count"
                        )
                        pos["entry_theme_return_correlation_peer_count"] = candidate.get(
                            "signal_theme_return_correlation_peer_count"
                        )
                        pos["entry_theme_specificity_score"] = candidate.get(
                            "signal_theme_specificity_score"
                        )
                        pos["entry_theme_membership_source"] = str(
                            candidate.get("signal_theme_membership_source")
                            or candidate.get("theme_basis")
                            or ""
                        )
                        pos["entry_theme_unattributed_weight"] = candidate.get(
                            "unattributed_theme_weight"
                        )
                    elif not str(pos.get("active_theme") or "").strip():
                        pos["active_theme"] = str(
                            pos.get("entry_theme") or signal_theme
                        ).strip()
                    pos["mainline_score"] = candidate.get("mainline_score", candidate.get("sector_score"))
                    pos["mainline_state"] = candidate.get("mainline_state", candidate.get("sector_status"))
                    pos["mainline_raw_state"] = candidate.get("mainline_raw_state")
                    pos["mainline_confirmation_count"] = candidate.get("mainline_confirmation_count")
                    pos["mainline_cross_day_persistent"] = bool(candidate.get("mainline_cross_day_persistent"))
                    pos["mainline_confirmed"] = bool(candidate.get("mainline_confirmed"))
                    if niuone_entry_subroute:
                        pos["niuone_entry_subroute"] = niuone_entry_subroute
                    for key in (
                        "niuone_lifecycle_stage",
                        "niuone_lifecycle_label",
                        "niuone_lifecycle_order",
                        "niuone_lifecycle_entry_policy",
                    ):
                        if key in candidate:
                            pos[key] = candidate[key]
                    if reversal_buy:
                        for key in (
                            "reversal_basis", "daily_v_reversal", "daily_v_left_peak_date",
                            "daily_v_trough_date", "daily_v_left_days", "daily_v_right_days",
                            "daily_v_decline_pct", "daily_v_rebound_pct",
                            "daily_v_recovery_ratio", "daily_v_rising_ratio",
                            "daily_v_pattern_score",
                        ):
                            pos[key] = candidate.get(key)
                    pos["today_breadth_pct"] = candidate.get("today_breadth_pct")
                    pos["effective_strong_count"] = candidate.get("effective_strong_count")
                    pos["leader_concentration"] = candidate.get("leader_concentration")
                    pos["mainline_weak_count"] = 0
                    pos["stock_role"] = candidate.get("stock_role")
                    pos["stock_leader_rank"] = candidate.get("stock_leader_rank")
                    pos["stock_leader_tier"] = bool(candidate.get("stock_leader_tier"))
                    pos["stock_strong"] = bool(candidate.get("stock_strong"))
                    pos["niu_leader_lost_count"] = 0
                    if (
                        old_qty <= 0
                        and niuone_execution_reference_price > 0
                        and niuone_execution_gap_pct is not None
                    ):
                        pos["entry_execution_reference_price"] = round(
                            niuone_execution_reference_price,
                            3,
                        )
                        pos["entry_execution_gap_pct"] = (
                            niuone_execution_gap_pct
                        )
                    if old_qty <= 0:
                        pos["entry_industry"] = pos["industry"]
                        pos["entry_model_requested_shares"] = action.get(
                            "model_requested_shares"
                        )
                        pos["entry_executed_shares"] = shares
                        pos["entry_maximum_permitted_shares"] = action.get(
                            "maximum_permitted_shares"
                        )
                        pos["entry_risk_ceiling_utilization_pct"] = (
                            action.get("risk_ceiling_utilization_pct")
                        )
                        pos["entry_risk_ceiling_binding_constraints"] = list(
                            action.get("risk_ceiling_binding_constraints")
                            or []
                        )
                        pos["entry_risk_ceiling_auto_reduced"] = bool(
                            action.get("risk_ceiling_auto_reduced")
                        )
                        for key in (
                            "entry_signal_generated_at",
                            "entry_schedule_slot",
                            "entry_schedule_run_kind",
                            "entry_schedule_triggered_at",
                            "entry_execution_mode",
                        ):
                            if execution_context.get(key):
                                pos[key] = execution_context[key]
                        entry_context_fields = (
                            (
                                "niuone_lifecycle_stage",
                                "entry_niuone_lifecycle_stage",
                            ),
                            (
                                "niuone_lifecycle_label",
                                "entry_niuone_lifecycle_label",
                            ),
                            (
                                "niuone_lifecycle_order",
                                "entry_niuone_lifecycle_order",
                            ),
                            (
                                "niuone_lifecycle_entry_policy",
                                "entry_niuone_lifecycle_entry_policy",
                            ),
                            ("mainline_state", "entry_mainline_state"),
                            ("mainline_score", "entry_mainline_score"),
                            (
                                "mainline_score_change",
                                "entry_mainline_score_change",
                            ),
                            (
                                "mainline_state_streak",
                                "entry_mainline_state_streak",
                            ),
                            (
                                "mainline_cross_day_persistent",
                                "entry_mainline_cross_day_persistent",
                            ),
                            (
                                "mainline_confirmed",
                                "entry_mainline_confirmed",
                            ),
                            (
                                "today_strength_score",
                                "entry_today_strength_score",
                            ),
                            (
                                "strong_stock_count",
                                "entry_strong_stock_count",
                            ),
                            (
                                "effective_strong_count",
                                "entry_effective_strong_count",
                            ),
                            (
                                "stock_sector_rank",
                                "entry_stock_sector_rank",
                            ),
                            ("stock_strong", "entry_stock_strong"),
                            (
                                "stock_leader_tier",
                                "entry_stock_leader_tier",
                            ),
                            (
                                "daily_v_recovery_ratio",
                                "entry_daily_v_recovery_ratio",
                            ),
                        )
                        for source_key, target_key in entry_context_fields:
                            if (
                                source_key in candidate
                                and candidate.get(source_key) is not None
                            ):
                                pos[target_key] = candidate.get(source_key)
                        pos.update(niuone_selection_context)
                        entry_mainline_score = _safe_float(
                            pos.get("entry_mainline_score"),
                            -1.0,
                        )
                        if entry_mainline_score >= 0:
                            pos["mainline_peak_score"] = round(
                                entry_mainline_score,
                                3,
                            )
                            pos["mainline_peak_drawdown_points"] = 0.0
                        record_niuone_lifecycle_observation(
                            pos,
                            observed_at=str(
                                pos.get("entry_signal_generated_at")
                                or now_ts()
                            ),
                            source="entry_signal",
                            complete_from_entry=True,
                        )
                        niuone_entry_context = (
                            niuone_entry_context_from_position(pos)
                        )
                else:
                    pos["sector_weak_count"] = 0
            if niuone_upgrade_add:
                pos.setdefault("initial_buy_strategy", existing_entry_strategy)
                pos.setdefault("initial_entry_reason", str(pos.get("entry_reason") or ""))
                pos["buy_strategy"] = buy_strategy
                pos["strategy_upgrade_from"] = existing_entry_strategy
                pos["strategy_upgraded_at"] = now_ts()
                pos["latest_add_reason"] = reason
                entry_mark_strategy = buy_strategy
                entry_mark_component = existing_entry_strategy
                entry_mark_source = "BUY_UPGRADE"
            elif old_qty <= 0 or not pos.get("buy_strategy"):
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
            if niuone_markup_scale_add:
                early_markup_scale_in = buy_strategy == "niu_emerging"
                if early_markup_scale_in:
                    markup_stage_cap_pct = (
                        NIUONE_MARKUP_EARLY_UPGRADE_POSITION_CAP_PCT
                    )
                elif niuone_upgrade_source in {
                    "niu_reversal_probe",
                    "niu_emerging",
                }:
                    markup_stage_cap_pct = (
                        NIUONE_MARKUP_UPGRADE_POSITION_CAP_PCT
                    )
                else:
                    markup_stage_cap_pct = (
                        NIUONE_ABSOLUTE_POSITION_CAP_PCT["niu_leader"]
                    )
                pos["niuone_markup_scale_in"] = True
                pos["niuone_markup_scale_in_cap_pct"] = (
                    markup_stage_cap_pct
                )
                pos["niuone_markup_scale_in_tier"] = (
                    "early" if early_markup_scale_in else "confirmed"
                )
                pos[
                    "niuone_markup_early_scale_in_done"
                    if early_markup_scale_in
                    else "niuone_markup_confirmed_scale_in_done"
                ] = True
                pos["niuone_markup_scale_in_min_pnl_pct"] = (
                    NIUONE_MARKUP_UPGRADE_MIN_PNL_PCT
                )
                pos["niuone_markup_scale_in_max_pnl_pct"] = (
                    NIUONE_MARKUP_UPGRADE_MAX_PNL_PCT
                )
                pos["niuone_markup_scale_in_count"] = (
                    int(pos.get("niuone_markup_scale_in_count") or 0) + 1
                )
                pos["niuone_markup_scale_in_last_at"] = now_ts()
                pos["niuone_markup_scale_in_signal_pnl_pct"] = round(
                    current_pnl_pct,
                    4,
                )
                if buy_strategy == "niu_leader":
                    pos.update({
                        "niuone_markup_rebalance_cycle_peak_price": round(
                            float(price),
                            3,
                        ),
                        "niuone_markup_rebalance_stall_count": 0,
                        "niuone_markup_rebalance_observation_count": 0,
                        "niuone_markup_rebalance_last_observation": (
                            execution_date
                        ),
                        "niuone_markup_rebalance_last_add_date": execution_date,
                        "niuone_markup_rebalance_armed": False,
                        "niuone_markup_rebalance_reduced": False,
                        "niuone_markup_rebalance_reentry_price": None,
                    })
                    if niuone_rebalance_reentry:
                        pos["niuone_markup_rebalance_reentry_count"] = (
                            int(
                                pos.get(
                                    "niuone_markup_rebalance_reentry_count"
                                ) or 0
                            ) + 1
                        )
            entry_mark = apply_entry_strategy_mark(
                pos,
                entry_mark_strategy,
                reason,
                source=entry_mark_source,
                component_strategy=entry_mark_component,
            )
            action["strategy_mark"] = entry_mark
            if buy_strategy == STRATEGY_SOURCE_PRESET_TEXT:
                if versioned_prompt_buy:
                    action["prompt_strategy_version_id"] = str(
                        pos.get("prompt_strategy_version_id") or ""
                    )
                    action["prompt_strategy_plan_sha256"] = str(
                        pos.get("prompt_strategy_plan_sha256") or ""
                    )
                    action["prompt_strategy_entry_evaluation_id"] = str(
                        (prompt_entry_result or {}).get("evaluation_id") or ""
                    )
                    action["prompt_strategy_entry_audit"] = _json_safe_copy(
                        (prompt_entry_result or {}).get("audit") or {}
                    )
                else:
                    action_preset_audit = (
                        decision.get("preset_strategy_audit")
                        if isinstance(decision.get("preset_strategy_audit"), Mapping)
                        else {}
                    )
                    action_candidate_pool = (
                        action_preset_audit.get("candidate_pool")
                        if isinstance(action_preset_audit.get("candidate_pool"), Mapping)
                        else {}
                    )
                    action["preset_strategy_snapshot"] = _json_safe_copy(
                        pos.get("preset_strategy_snapshot") or {}
                    )
                    action["preset_strategy_interpretation"] = _json_safe_copy(
                        pos.get("preset_strategy_interpretation") or {}
                    )
                    action["preset_strategy_prompt_protocol"] = str(
                        action_preset_audit.get("prompt_protocol") or ""
                    )
                    action["preset_strategy_prompt_sha256"] = str(
                        action_preset_audit.get("prompt_sha256") or ""
                    )
                    action["preset_strategy_interpretation_sha256"] = str(
                        pos.get("preset_strategy_interpretation_sha256") or ""
                    )
                    action["preset_strategy_candidate_pool_sha256"] = str(
                        action_candidate_pool.get("facts_sha256") or ""
                    )
                    action["preset_strategy_candidate_pool_count"] = int(
                        action_candidate_pool.get("count") or 0
                    )
            action["order_position_pct"] = order_position_pct
            action["position_after_trade_pct"] = position_after_trade_pct
            action["total_position_after_trade_pct"] = total_position_after_trade_pct
            if is_dynamic_risk_strategy(buy_strategy):
                action["effective_loss_distance_pct"] = round(tide_effective_loss_distance_pct, 3)
                action["position_open_risk_pct"] = round(tide_position_open_risk_pct, 4)
                action["dynamic_position_cap_pct"] = round(tide_dynamic_position_cap_pct, 3)
                if niuone_execution_gap_pct is not None:
                    action["execution_gap_pct"] = niuone_execution_gap_pct
                if niuone_entry_subroute:
                    action["niuone_entry_subroute"] = niuone_entry_subroute
                if niuone_entry_context:
                    action["niuone_entry_context"] = dict(
                        niuone_entry_context
                    )
            pos["highest_price"] = round(max(float(pos.get("highest_price") or price), float(price)), 3)
            current_pnl_pct = ((float(price) / float(pos["avg_cost"]) - 1) * 100) if pos.get("avg_cost") else 0.0
            prior_max_pnl = float(pos.get("max_pnl_pct") or current_pnl_pct)
            pos["max_pnl_pct"] = round(max(prior_max_pnl, current_pnl_pct), 2)
            lots = pos.setdefault("buy_date_lots", {})
            lots[execution_date] = int(lots.get(execution_date, 0)) + qty
            cash -= total_cost
            if old_qty <= 0:
                new_buys += 1
                if is_niuone_strategy(buy_strategy):
                    niuone_opened_today.add(code)
            executed_trade = {
                "time": now_ts(), "action": "BUY", "code": code, "name": name,
                "shares": qty, "price": round(price, 3), "amount": round(gross, 2),
                "commission": fees["commission"], "transfer_fee": fees["transfer_fee"],
                "stamp_duty": fees["stamp_duty"], "fee": fees["total_fee"],
                "total_cost": round(total_cost, 2), "price_source": price_source,
                "quote_time": q.get("quote_time") or now_ts(),
                "quote_source": q.get("source") or price_source,
                "position_before_qty": old_qty,
                "position_after_qty": old_qty + qty,
                "position_opened": old_qty <= 0,
                "order_position_pct": order_position_pct,
                "position_after_trade_pct": position_after_trade_pct,
                "total_position_after_trade_pct": total_position_after_trade_pct,
                "trade_reason": current_reason, "reason": reason,
                "buy_strategy": buy_strategy,
                "strategy_mark": entry_mark,
            }
            for key in (
                "model_requested_shares",
                "maximum_permitted_shares",
                "maximum_permitted_gross",
                "risk_ceiling_utilization_pct",
                "risk_ceiling_binding_constraints",
                "risk_ceiling_auto_reduced",
            ):
                if key in action:
                    executed_trade[key] = _json_safe_copy(action[key])
            if niuone_execution_gap_pct is not None:
                executed_trade["execution_gap_pct"] = niuone_execution_gap_pct
            if niuone_entry_context:
                executed_trade["niuone_entry_context"] = dict(
                    niuone_entry_context
                )
            if buy_strategy == STRATEGY_SOURCE_PRESET_TEXT:
                prompt_trade_fields = (
                    (
                        "prompt_strategy_version_id",
                        "prompt_strategy_plan_sha256",
                        "prompt_strategy_entry_evaluation_id",
                        "prompt_strategy_entry_audit",
                    )
                    if versioned_prompt_buy
                    else (
                        "preset_strategy_snapshot",
                        "preset_strategy_interpretation",
                        "preset_strategy_prompt_protocol",
                        "preset_strategy_prompt_sha256",
                        "preset_strategy_interpretation_sha256",
                        "preset_strategy_candidate_pool_sha256",
                        "preset_strategy_candidate_pool_count",
                    )
                )
                for key in prompt_trade_fields:
                    executed_trade[key] = _json_safe_copy(action.get(key))
            executed.append(executed_trade)
        elif act == "SELL":
            pos = positions.get(code)
            if not pos:
                continue
            entry_strategy = str(
                position_entry_strategy(pos)
                or latest_buy_strategy_for_code(state, code)
                or classify_buy_strategy(str(pos.get("entry_reason") or ""))
            )
            if entry_strategy == STRATEGY_SOURCE_PRESET_TEXT:
                preset_sell_error = validate_preset_sell_audit(
                    decision.get("preset_exit_audit"),
                    code=code,
                    position_snapshot=pos.get("preset_strategy_snapshot"),
                    position_interpretation=pos.get("preset_strategy_interpretation"),
                    position_interpretation_sha256=str(
                        pos.get("preset_strategy_interpretation_sha256") or ""
                    ),
                )
                if preset_sell_error:
                    add_execution_block(
                        decision,
                        code,
                        preset_sell_error,
                        category="strategy_policy",
                    )
                    continue
                action["preset_strategy_text_sha256"] = str(
                    (pos.get("preset_strategy_snapshot") or {}).get("text_sha256")
                    or ""
                )
                action["preset_strategy_exit_prompt_sha256"] = str(
                    (decision.get("preset_exit_audit") or {}).get("prompt_sha256")
                    or ""
                )
                action["preset_strategy_exit_prompt_protocol"] = str(
                    (decision.get("preset_exit_audit") or {}).get(
                        "prompt_protocol"
                    )
                    or ""
                )
            sell_niuone_entry_context = (
                niuone_entry_context_from_position(pos)
                if is_niuone_strategy(entry_strategy)
                else {}
            )
            if entry_strategy == "shaofu_b1":
                add_execution_block(
                    decision,
                    code,
                    "少妇B1卖出由本地持仓状态机执行；模型SELL仅作建议，未直接成交",
                )
                action["action"] = "HOLD"
                action["reason"] = (
                    "模型SELL已降级为HOLD：等待结构止损、白黄线死叉、白线两日破位"
                    "或经资金流/预测量能连续确认的软退出"
                )
                continue
            avg_cost = float(pos.get("avg_cost") or 0)
            available_qty = available_to_sell(pos)
            if is_niuone_strategy(entry_strategy):
                model_requested_sell_shares = shares
                sell_quantity_auto_reduced = bool(
                    available_qty > 0
                    and available_qty % 100 == 0
                    and model_requested_sell_shares > available_qty
                )
                if sell_quantity_auto_reduced:
                    shares = available_qty
                    action["shares"] = shares
                action["sell_execution_evidence_schema_version"] = (
                    FORWARD_SELL_EXECUTION_EVIDENCE_SCHEMA_VERSION
                )
                action["sell_execution_source"] = "model_action"
                action["model_requested_sell_shares"] = (
                    model_requested_sell_shares
                )
                action["available_sell_shares"] = available_qty
                action["sell_quantity_auto_reduced"] = (
                    sell_quantity_auto_reduced
                )
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
            day_reference_price = _safe_float(q.get("prev_close") or pos.get("prev_close"), 0.0)
            day_pnl = net_proceeds - qty * day_reference_price if day_reference_price > 0 else None
            day_pnl_pct = (
                day_pnl / (qty * day_reference_price) * 100
                if day_pnl is not None and qty > 0
                else None
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
            sell_trade_time = now_ts()
            sell_niuone_lifecycle_evidence = (
                niuone_lifecycle_exit_evidence_from_position(
                    pos,
                    observed_at=sell_trade_time,
                )
                if is_niuone_strategy(entry_strategy)
                else {}
            )
            pos["qty"] = position_qty(pos) - qty
            pos.pop("shares", None)
            pos["last_price"] = price
            # consume non-today lots FIFO-ish
            remaining = qty
            lots = pos.get("buy_date_lots") or {}
            for date in sorted(list(lots.keys())):
                if date == execution_date or remaining <= 0:
                    continue
                use = min(int(lots.get(date) or 0), remaining)
                lots[date] = int(lots.get(date) or 0) - use
                remaining -= use
                if lots[date] <= 0:
                    lots.pop(date, None)
            if pos["qty"] <= 0:
                positions.pop(code, None)
            cash += net_proceeds
            executed_trade = {
                "time": sell_trade_time, "action": "SELL", "code": code,
                "name": pos.get("name") or name, "shares": qty,
                "price": round(price, 3), "amount": round(gross, 2),
                "commission": fees["commission"],
                "transfer_fee": fees["transfer_fee"],
                "stamp_duty": fees["stamp_duty"], "fee": fees["total_fee"],
                "net_proceeds": round(net_proceeds, 2),
                "pnl": round(realized_pnl, 2),
                "pnl_pct": round(realized_pnl_pct, 2),
                "price_source": price_source,
                "day_pnl": round(day_pnl, 2) if day_pnl is not None else None,
                "day_pnl_pct": round(day_pnl_pct, 2)
                if day_pnl_pct is not None else None,
                "quote_time": q.get("quote_time") or now_ts(),
                "quote_source": q.get("source") or price_source,
                "order_position_pct": order_position_pct,
                "position_before_trade_pct": position_before_trade_pct,
                "position_after_trade_pct": position_after_trade_pct,
                "total_position_after_trade_pct": total_position_after_trade_pct,
                "position_before_qty": position_qty(pos) + qty,
                "position_after_qty": max(0, position_qty(pos)),
                "position_fully_closed": position_qty(pos) <= 0,
                "trade_reason": current_reason, "reason": reason,
                "buy_strategy": entry_strategy, "exit_rule": exit_rule,
                "strategy_mark": entry_mark, "exit_strategy_mark": exit_mark,
            }
            for key in (
                "sell_execution_evidence_schema_version",
                "sell_execution_source",
                "model_requested_sell_shares",
                "available_sell_shares",
                "sell_quantity_auto_reduced",
            ):
                if key in action:
                    executed_trade[key] = _json_safe_copy(action[key])
            if sell_niuone_entry_context:
                executed_trade["niuone_entry_context"] = dict(
                    sell_niuone_entry_context
                )
            if sell_niuone_lifecycle_evidence:
                executed_trade["niuone_lifecycle_evidence"] = dict(
                    sell_niuone_lifecycle_evidence
                )
            if entry_strategy == STRATEGY_SOURCE_PRESET_TEXT:
                executed_trade["preset_strategy_text_sha256"] = str(
                    action.get("preset_strategy_text_sha256") or ""
                )
                executed_trade["preset_strategy_exit_prompt_sha256"] = str(
                    action.get("preset_strategy_exit_prompt_sha256") or ""
                )
                executed_trade["preset_strategy_exit_prompt_protocol"] = str(
                    action.get("preset_strategy_exit_prompt_protocol") or ""
                )
            executed.append(executed_trade)
    state["cash"] = round(cash, 2)
    state.setdefault("trade_log", []).extend(executed)
    del state["trade_log"][:-TRADE_LOG_LIMIT]
    return executed


def _sync_decision_to_db(log_entry: dict) -> bool:
    """将决策日志同步写入 SQLite。"""
    try:
        from niuniu_db import record_decision as _rd
        return _rd(log_entry) is True
    except Exception as exc:
        print(
            "[WARN] 决策耐久证据写入失败: "
            f"{type(exc).__name__}",
            flush=True,
        )
        return False


def _sync_trades_to_db(executed: list[dict[str, Any]]) -> bool:
    """将已成交记录同步写入 SQLite。"""
    if not executed:
        return True
    try:
        from niuniu_db import record_trade as _rt
        results = [_rt(item) is True for item in executed]
        return all(results)
    except Exception as exc:
        print(
            "[WARN] 成交耐久证据写入失败: "
            f"{type(exc).__name__}",
            flush=True,
        )
        return False


def _latest_b1_decision_log(
    state: Mapping[str, Any],
    generated_at: str,
    schedule_slot: str,
) -> dict[str, Any] | None:
    for raw in reversed(state.get("decision_log") or []):
        if not isinstance(raw, dict):
            continue
        if str(raw.get("b1_generated_at") or "") != generated_at:
            continue
        if str(raw.get("schedule_slot") or "") != schedule_slot:
            continue
        return raw
    return None


def _decision_has_candidate_evidence(log_entry: Mapping[str, Any]) -> bool:
    return decision_has_durable_candidate_evidence(log_entry)


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
        return localize_strategy_text(explicit)
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
    candidate_evidence: list[dict[str, Any]],
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
        "strategy_suite": current_strategy_suite(),
        "decision": _json_safe_copy(decision),
        "candidates": _json_safe_copy(
            candidates[:100]
            if isinstance(decision.get("preset_strategy_audit"), Mapping)
            else candidates[:20]
        ),
        "candidate_evidence_schema_version": 2,
        "execution_evidence_schema_version": (
            FORWARD_EXECUTION_EVIDENCE_SCHEMA_VERSION
        ),
        "candidate_evidence": _json_safe_copy(candidate_evidence),
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
        queued_suite = str(entry.get("strategy_suite") or "").strip()
        if queued_suite and queued_suite != current_strategy_suite():
            candidates = (
                [
                    candidate
                    for candidate in candidates
                    if isinstance(candidate, dict) and candidate_matches_active_strategy(candidate)
                ]
                if isinstance(candidates, list)
                else []
            )
            decision["summary"] += "；策略已切换，旧策略买入候选已移除"
        market_strategy_ctx = select_current_market_strategy_context(state, now)
        refine_overlimit_buy_actions(
            decision,
            state,
            candidates if isinstance(candidates, list) else [],
            enrich_portfolio(state),
            market_strategy_ctx,
        )
        decision["_niuone_execution_context"] = {
            "entry_signal_generated_at": entry.get("b1_generated_at") or "",
            "entry_schedule_slot": entry.get("schedule_slot") or "",
            "entry_schedule_run_kind": entry.get("schedule_run_kind") or "",
            "entry_schedule_triggered_at": entry.get("schedule_triggered_at") or "",
            "entry_execution_mode": "deferred",
        }
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
            "candidate_evidence_schema_version": entry.get(
                "candidate_evidence_schema_version"
            ),
            "execution_evidence_schema_version": entry.get(
                "execution_evidence_schema_version"
            ),
            "candidate_evidence": _json_safe_copy(
                entry.get("candidate_evidence") or []
            ),
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
        all_executed = _accounted_trade_executions(all_executed)
        if all_executed:
            _notify_trade_executions_safely(all_executed)
    return {"executed": all_executed, "attempted": attempted}


def run_decision_after_b1(b1_payload: dict[str, Any], force: bool = False) -> dict[str, Any]:
    state = load_state()
    auto_exit_refresh_baseline = _auto_exit_refresh_baseline(state)
    generated_at = b1_payload.get("generated_at") or now_ts()
    schedule_slot = b1_payload.get("schedule_slot") or ""
    schedule_run_kind = b1_payload.get("schedule_run_kind") or ""
    schedule_triggered_at = b1_payload.get("schedule_triggered_at") or ""
    execution_context = {
        "entry_signal_generated_at": generated_at,
        "entry_schedule_slot": schedule_slot,
        "entry_schedule_run_kind": schedule_run_kind,
        "entry_schedule_triggered_at": schedule_triggered_at,
        "entry_execution_mode": "direct",
    }
    already_decided = bool(not force and state.get("last_b1_generated_at") == generated_at)
    sync_sector_tide_position_context(state, b1_payload)
    sync_niuone_position_context(state, b1_payload)
    sync_zettaranc_position_context(state, b1_payload)
    state.pop(AUTO_EXIT_PERSISTENCE_STATUS_KEY, None)
    state[AUTO_EXIT_REFRESH_BASELINE_KEY] = auto_exit_refresh_baseline
    try:
        position_exit_executed = run_position_exit_checks_before_decision(
            state,
            datetime.now(),
        )
    finally:
        state.pop(AUTO_EXIT_REFRESH_BASELINE_KEY, None)
    position_exit_executed = _accounted_trade_executions(
        position_exit_executed
    )
    position_exit_persistence = _pop_auto_exit_persistence_status(state)
    if already_decided:
        prior_decision = _latest_b1_decision_log(
            state,
            str(generated_at),
            str(schedule_slot),
        )
        decision_persisted = bool(
            prior_decision
            and _decision_has_candidate_evidence(prior_decision)
            and _sync_decision_to_db(prior_decision)
        )
        record_equity(state)
        save_state(state)
        if position_exit_executed:
            _notify_trade_executions_safely(position_exit_executed)
        return {
            "skipped": True,
            "reason": "already_decided_for_this_b1",
            "executed": position_exit_executed,
            "position_exit_executed": position_exit_executed,
            "model_executed": [],
            "decision_persisted": decision_persisted,
            "trades_persisted": position_exit_persistence[
                "trades_persisted"
            ],
            "durable_evidence_persisted": (
                decision_persisted
                and position_exit_persistence[
                    "durable_evidence_persisted"
                ]
            ),
            "state": enrich_portfolio(state),
        }
    market_strategy_ctx = market_strategy_context_for_b1(b1_payload)

    # 日内亏损预算只暂停新开仓；已有持仓的本地和模型退出继续运行。
    budget_exceeded, today_pnl = check_daily_loss_budget(state)
    buy_budget_exceeded = bool(budget_exceeded)
    if buy_budget_exceeded:
        market_strategy_ctx = {
            **market_strategy_ctx,
            "allow_new_buys": False,
            "max_new_buys_per_decision": 0,
            "daily_loss_budget_exceeded": True,
            "daily_loss_budget_pnl_pct": round(float(today_pnl), 3),
            "daily_loss_budget_limit_pct": DAILY_LOSS_BUDGET_PCT,
        }
        state["trading_paused"] = True
        state["pause_reason"] = f"日内亏损预算({today_pnl:.1f}%)"
        state["pause_since"] = now_ts()
        try:
            from self_optimizer import run_optimization
            run_optimization()
        except Exception:
            pass
    else:
        for key in ("trading_paused", "pause_reason", "pause_since"):
            state.pop(key, None)

    compact_market_ctx = compact_market_strategy_context(market_strategy_ctx)
    state["market_decision_context"] = compact_market_ctx
    frozen_prompt_version = active_frozen_prompt_strategy()

    # 自适应参数
    adaptive = get_adaptive_params()
    
    raw_candidates = decision_candidate_rows(b1_payload)
    observed_candidates = observed_candidate_rows(b1_payload)
    candidates = [
        c for c in raw_candidates
        if (
            isinstance(c, dict)
            and candidate_in_stock_universe(c)
            and candidate_matches_active_strategy(c)
            and candidate_is_buyable(c)
        )
    ]
    if frozen_prompt_version is not None:
        frozen_version_id = str(frozen_prompt_version.get("version_id") or "")
        prompt_exit_codes = {
            normalize_code(item.get("code") or "")
            for item in position_exit_executed
            if isinstance(item, Mapping)
            and str(item.get("action") or "").upper() == "SELL"
            and str(item.get("prompt_strategy_version_id") or "")
        }
        conflict_policy = str(
            ((frozen_prompt_version.get("execution_plan") or {}).get("strategy") or {}).get(
                "conflict_policy"
            )
            or "exit_first"
        )
        candidates = [
            candidate
            for candidate in candidates
            if str(candidate.get("prompt_strategy_version_id") or "")
            == frozen_version_id
            and not (
                conflict_policy == "exit_first"
                and normalize_code(candidate.get("code") or "") in prompt_exit_codes
            )
        ]
    if (
        current_strategy_suite() == STRATEGY_SOURCE_PRESET_TEXT
        and frozen_prompt_version is None
        and not current_preset_strategy_text()
    ):
        candidates = []
    candidate_evidence = build_practice_candidate_evidence(
        observed_candidates,
        candidates,
    )
    
    trade_allowed, trade_reason = is_a_share_execution_time()
    if buy_budget_exceeded:
        trade_reason = f"{trade_reason}；日内亏损预算触发，仅允许SELL/HOLD"
    deferred_due_at = "" if trade_allowed else deferred_execution_due_at(schedule_slot)
    market_env = check_market_environment()
    market_sent = check_market_sentiment()
    # 市场情绪过冷时降低仓位上限（模型自行判断，此处仅提示）
    sentiment_note = ""
    if market_sent["sentiment"] == "cold" and trade_allowed:
        sentiment_note = f"⚠️市场情绪偏冷({market_sent['detail']})，建议仓位减半或不建仓"
    portfolio = enrich_portfolio(state)
    portfolio["_preset_position_policy_context"] = preset_position_policy_context(state)
    has_open_positions = any(
        isinstance(pos, dict) and position_qty(pos) > 0
        for pos in (state.get("positions") or {}).values()
    )

    def make_decision(reason: str) -> dict[str, Any]:
        if frozen_prompt_version is not None:
            local_decision = build_local_prompt_decision(
                candidates,
                state,
                frozen_prompt_version,
                market_strategy_ctx,
            )
            local_decision["market_guidance"] = compact_market_ctx
            local_decision["decision_intelligence"] = safe_decision_intelligence_context(
                portfolio,
                candidates,
                market_strategy_ctx,
                "",
            )
            local_decision["decision_reason"] = reason
            return local_decision
        return call_model_decision(
            candidates,
            portfolio,
            True,
            reason,
            market_strategy_ctx,
        )

    try:
        if not has_open_positions and (not candidates or buy_budget_exceeded):
            summary = (
                f"日内亏损预算触发（今日累计{today_pnl:.1f}%），当前无持仓，仅暂停新开仓"
                if buy_budget_exceeded
                else "本轮无候选且无持仓，无需生成买卖动作"
            )
            decision = {
                "summary": summary,
                "actions": [],
                "model": "SYSTEM_POSITION_RISK_CHECK",
                "provider": "local_rule",
                "market_guidance": compact_market_ctx,
                "decision_intelligence": safe_decision_intelligence_context(
                    portfolio, candidates, market_strategy_ctx, ""
                ),
            }
            executed = []
        elif not trade_allowed and deferred_due_at:
            model_trade_reason = (
                f"计划{schedule_slot[-5:]}选股属于上午连续竞价时段；当前{trade_reason}。"
                f"请正常生成买卖策略，系统会在{deferred_due_at[-8:-3]}开盘后复核并成交。"
            )
            decision = make_decision(model_trade_reason)
            if frozen_prompt_version is None:
                refine_overlimit_buy_actions(decision, state, candidates, portfolio, market_strategy_ctx)
            execution_allowed, execution_reason = is_a_share_execution_time()
            if execution_allowed:
                trade_allowed = True
                trade_reason = execution_reason
                decision["_niuone_execution_context"] = execution_context
                executed = execute_actions(
                    state,
                    decision,
                    candidates,
                    execution_allowed,
                    execution_reason,
                    market_strategy_ctx,
                )
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
                        candidate_evidence=candidate_evidence,
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
            decision = make_decision(trade_reason)
            if frozen_prompt_version is None:
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
                decision["_niuone_execution_context"] = execution_context
                executed = execute_actions(
                    state,
                    decision,
                    candidates,
                    execution_allowed,
                    execution_reason,
                    market_strategy_ctx,
                )
        state["last_error"] = ""
    except Exception as exc:
        decision = {
            "summary": (
                "本地文字策略决策失败，本轮不交易"
                if frozen_prompt_version is not None
                else "模型决策失败，本轮不交易"
            ),
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
        "candidate_evidence_schema_version": 2,
        "execution_evidence_schema_version": (
            FORWARD_EXECUTION_EVIDENCE_SCHEMA_VERSION
        ),
        "candidate_evidence": candidate_evidence,
    }
    if schedule_slot:
        log_entry["schedule_slot"] = schedule_slot
        log_entry["schedule_run_kind"] = schedule_run_kind
        log_entry["schedule_triggered_at"] = schedule_triggered_at
    state.setdefault("decision_log", []).append(log_entry)
    del state["decision_log"][:-50]
    decision_persisted = _sync_decision_to_db(log_entry)
    candidate_evidence_valid = _decision_has_candidate_evidence(log_entry)
    model_trades_persisted = _sync_trades_to_db(executed)
    if executed and model_trades_persisted:
        _sync_positions_to_db(state)
    record_equity(state)
    save_state(state)
    model_executed = _accounted_trade_executions(executed)
    all_executed = [*position_exit_executed, *model_executed]
    if all_executed:
        _notify_trade_executions_safely(all_executed)
    return {
        "decision": decision,
        "executed": all_executed,
        "position_exit_executed": position_exit_executed,
        "model_executed": model_executed,
        "decision_persisted": decision_persisted,
        "candidate_evidence_valid": candidate_evidence_valid,
        "trades_persisted": (
            position_exit_persistence["trades_persisted"]
            and model_trades_persisted
        ),
        "durable_evidence_persisted": (
            candidate_evidence_valid
            and decision_persisted
            and position_exit_persistence["durable_evidence_persisted"]
            and model_trades_persisted
        ),
        "portfolio": enrich_portfolio(state),
    }


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
        f"牛牛战法按主线酝酿→主升→高潮→分歧→退幕识别，试仓只参与酝酿候选和启动早段，酝酿候选中的强势股等待启动确认；主升围绕启动/领涨，高潮不追普遍新仓，分歧只观察核心股调整后转强或减仓，持续回落不触发买点，退幕只退出。"
        f"当日跨决策轮次累计最多新开{NIUONE_MAX_NEW_POSITIONS_PER_TRADING_DAY}只、最多同时持有{NIUONE_MAX_OPEN_POSITIONS}只，并硬执行单笔/组合/主题风险预算；总仓70%/55%/35%、主题敞口55%/40%/25%，"
        f"领涨/转强/启动/试仓单票绝对上限30%/25%/15%/6.25%，试仓单笔风险仅0.35%/0.30%/0.25%。"
        f"允许无明确主线；单只股票独强不得确认主线，日线V型结构则按独立试仓路径评估。"
        f"系统底线风控：峰值回撤/ATR吊灯保护、持仓超25日退出；"
        f"Z哥卖出风控：少妇B1至少观察{SHAOFU_MIN_HOLD_TRADING_DAYS}个交易日，开盘前30分钟仅执行硬退出，普通转弱经行业资金/预测量能连续确认后先减半；"
        f"模型SELL不直接成交。另保留防卖飞5分评分、B3次日不涨离场({B3_EXIT_HHMM}开盘检查)、B2两日不延续离场、超级B1未兑现离场({TIME_EXIT_HHMM}尾盘检查)、"
        f"卤煮半仓、S1/S2/S3逃顶、出货五式、BBI/白线两日破位、白线死叉黄线。"
        f"板块潮汐按行业连续两日退潮、市场硬停止、时间窗、2R减半和2ATR跟踪退出。"
        f"牛牛试仓所属题材首次进入退幕即退出，3个交易日未延续右侧趋势也退出；成熟路径按龙头梯队、主线退幕、市场硬停止和时间窗退出；进攻/修复/防守试仓盘中达到0.75R先减仓50%，轮动试仓及成熟路径达到1R先减仓45%，余仓成本保护并按2ATR跟踪。"
        f"买入按万一免五计费。"
    )


def snapshot_closing_equity_once() -> dict[str, Any]:
    """Refresh prices and persist one post-close account mark without trading."""
    now = datetime.now()
    if not is_a_share_trading_day(now):
        return {
            "ok": True,
            "skipped": True,
            "reason": "non_trading_day",
        }
    state = load_state()
    latest_strategy_payload = load_latest_sector_tide_payload()
    sync_sector_tide_position_context(state, latest_strategy_payload)
    sync_niuone_position_context(state, latest_strategy_payload)
    sync_zettaranc_position_context(state, latest_strategy_payload)
    refresh_realtime_prices(state)
    refresh_position_intraday(state)
    _refresh_position_bbi(state)
    rebuild_intraday_equity_curve(state, now=now)
    record_equity(state)
    _sync_positions_to_db(state)
    save_state(state)
    today = now.strftime("%Y-%m-%d")
    closing_points = [
        point
        for point in state.get("daily_equity_history", [])
        if isinstance(point, dict)
        and str(point.get("time") or "").startswith(today)
        and str(point.get("time") or "")[11:16] >= "15:00"
    ]
    if not closing_points:
        return {
            "ok": False,
            "skipped": False,
            "reason": "post_close_equity_point_missing",
        }
    latest = max(closing_points, key=lambda point: str(point.get("time") or ""))
    return {
        "ok": True,
        "skipped": False,
        "time": latest.get("time"),
        "equity": latest.get("equity"),
        "cash": latest.get("cash"),
        "market_value": latest.get("market_value"),
        "position_count": sum(
            position_qty(position) > 0
            for position in (state.get("positions") or {}).values()
            if isinstance(position, dict)
        ),
    }


def get_dashboard_payload() -> dict[str, Any]:
    state = load_state()
    now = datetime.now()
    latest_strategy_payload = load_latest_sector_tide_payload()
    sync_sector_tide_position_context(state, latest_strategy_payload)
    sync_niuone_position_context(state, latest_strategy_payload)
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
    payload["equity_history"] = load_account_history(
        "equity_history",
        state.get("equity_history", []),
        limit=2000,
    )
    payload["daily_equity_history"] = load_account_history(
        "daily_equity_history",
        state.get("daily_equity_history", []),
        limit=EQUITY_HISTORY_LIMIT,
    )
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
        auto_exit_result = run_auto_exits_once()
        print(json.dumps(auto_exit_result, ensure_ascii=False, indent=2))
        if auto_exit_result.get("ok") is not True:
            raise SystemExit(1)
    elif "--snapshot-equity" in sys.argv:
        snapshot_result = snapshot_closing_equity_once()
        print(json.dumps(snapshot_result, ensure_ascii=False, indent=2))
        if snapshot_result.get("ok") is not True:
            raise SystemExit(1)
    else:
        print(json.dumps(get_dashboard_payload(), ensure_ascii=False, indent=2))
