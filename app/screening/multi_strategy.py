#!/usr/bin/env python3
"""
牛牛1号 · 多战法扫描器 — A股主板全市场综合评分。

评估多战法（趋势/突破策略 + Z哥），每只票输出多战法分数
+ 最优战法标签，供实战页面模型决策时参考。

数据源（主链路优先绕过 Eastmoney 代理封锁）：
  1. akshare.stock_info_a_code_name() — 代码池
  2. 腾讯 qt.gtimg.cn 批量行情 — 实时报价
  3. 腾讯双入口 fqkline，东方财富备用 — 前复权日K数据

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
import http.client
import json
import os
import re
import shlex
import shutil
import statistics
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from collections.abc import Callable, Mapping
from typing import Any

from core.json_cache import write_json_cache
from niuone_paths import get_dashboard_env_file, get_dashboard_home
from market_data.news_precheck import (
    NewsPrecheckConfig,
    fetch_candidate_news_records,
)
from market_data.eastmoney_boards import (
    EastmoneyStockBoard,
    load_eastmoney_board_snapshot,
)
from market_data.tencent_kline_cache import (
    DEFAULT_KLINE_COUNT,
    DEFAULT_PREWARM_WORKERS,
    fetch_a_share_daily_klines,
    kline_cache_path,
    load_kline_series_map,
    merge_live_quote,
    prewarm_kline_cache,
    store_kline_series,
)
from screening.candidate_cache import write_practice_candidates_cache
from screening.stock_universe import (
    DEFAULT_STOCK_UNIVERSE,
    FULL_SUPPORTED_NON_ST_UNIVERSE,
    STOCK_UNIVERSE_ENV,
    friendly_stock_universe,
    normalize_stock_universe,
    selected_stock_universe,
    stock_board,
    stock_in_universe,
    stock_name_is_st,
    stock_universe_metadata,
)
from screening.niuone_mainline_cache import (
    load_cached_niuone_context,
    write_niuone_mainline_cache,
    write_niuone_mainline_summary_cache,
)
from storage.prompt_strategies import PromptStrategyStore
from strategies.prompt_runtime import score_prompt_selection
from strategies.registry import (
    ACTIVE_STRATEGY_ENV,
    DISPLAY_STRATEGY_ORDER,
    PERSONA_STRATEGY_ENV,
    STRATEGY_SOURCE_ENV,
    STRATEGY_DEFINITIONS,
    STRATEGY_META,
    STRATEGY_SCORE_PROFILES,
    STRATEGY_SUITE_PRESET_TEXT,
    active_strategy_suite,
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
    NIUONE_STRATEGY_IDS,
    SECTOR_TIDE_STRATEGY_IDS,
    STRATEGY_SCORERS,
    ZETTARANC_STRATEGY_IDS,
    analyze_enriched_rows,
    build_niuone_context,
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
    zettaranc_industry_flow_signal,
)
from strategies.selection import (
    candidate_is_trade_ready,
    select_display_candidates,
    select_trade_candidates,
)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TENCENT_QUOTE = "https://qt.gtimg.cn/q="
TENCENT_QUOTE_TIMEOUT_SECONDS = 10
TENCENT_QUOTE_MAX_ATTEMPTS = 3
TENCENT_QUOTE_BACKOFF_SECONDS = 0.5
DEFAULT_TENCENT_QUOTE_STAGE_TIMEOUT_SECONDS = 90
DASHBOARD_HOME = get_dashboard_home(Path(__file__).resolve().parents[1])
DASHBOARD_ENV_FILE = get_dashboard_env_file(Path(__file__).resolve().parents[1])
B1_OUTPUT_DIR = DASHBOARD_HOME / "cron" / "output"
B1_CACHE_FILE = B1_OUTPUT_DIR / "b1_screen_latest.json"
MULTI_STRATEGY_CACHE = B1_OUTPUT_DIR / "multi_strategy_latest.json"
PRACTICE_CANDIDATES_CACHE = B1_OUTPUT_DIR / "practice_candidates_latest.json"
NIUONE_MAINLINE_CACHE = B1_OUTPUT_DIR / "niuone_mainline_latest.json"
NIUONE_MAINLINE_MINUTE_CACHE = B1_OUTPUT_DIR / "niuone_mainline_minute_latest.json"
NIUONE_MAINLINE_SUMMARY_CACHE = B1_OUTPUT_DIR / "niuone_mainline_summary_latest.json"
STOCK_INDUSTRY_CACHE = B1_OUTPUT_DIR / "stock_industry_cache.json"
EASTMONEY_BOARD_CACHE = B1_OUTPUT_DIR / "eastmoney_stock_boards.json"
B1_HISTORY_DIR = B1_OUTPUT_DIR / "b1_history"
MULTI_STRATEGY_HISTORY = B1_OUTPUT_DIR / "multi_strategy_history"
SCAN_HISTORY_RETENTION_DATES = 1
SCAN_HISTORY_MAX_FILES_PER_DATE = 12
DISPLAY_CANDIDATE_LIMIT = 16
DISPLAY_HEAD_LIMIT = 8
TRADE_CANDIDATE_LIMIT = 8
SECTOR_TIDE_NEWS_PRECHECK_LIMIT = 5
NIUONE_MAINLINE_ONLY_FLAG = "--niuone-mainline-only"
KLINE_PREWARM_ONLY_FLAG = "--prewarm-kline-cache"
HIGH_LIQUIDITY_MIN_AMOUNT = 8e8
MAX_TRADE_ANALYSIS_COUNT = 500
PRESET_TEXT_CANDIDATE_LIMIT_ENV = "DASHBOARD_PRESET_STRATEGY_CANDIDATE_LIMIT"
DEFAULT_PRESET_TEXT_CANDIDATE_LIMIT = 60
STOCK_INDUSTRY_BULK_CACHE_MIN_COVERAGE = 0.85
_STOCK_INDUSTRY_MEMORY_CACHE: dict[str, str] | None = None
_MARGIN_DETAIL_CACHE: dict[tuple[str, str], Any] = {}
_MARGIN_DETAIL_CACHE_LOCK = threading.Lock()
_BLOCK_TRADE_CACHE: dict[tuple[str, str], Any] = {}
_BLOCK_TRADE_CACHE_LOCK = threading.Lock()
_NATIVE_JAVASCRIPT_CONTEXT: Any | None = None
_NATIVE_JAVASCRIPT_CONTEXT_LOCK = threading.Lock()


def report_scan_progress(
    stage: str,
    *,
    stage_label: str,
    completed: int = 0,
    total: int = 0,
    **fields: Any,
) -> None:
    """Publish bounded scanner progress when launched by the Dashboard."""
    raw_path = str(os.environ.get("DASHBOARD_B1_PROGRESS_FILE") or "").strip()
    if not raw_path:
        return
    payload = {
        "job_id": str(os.environ.get("DASHBOARD_B1_JOB_ID") or "").strip(),
        "stage": str(stage or "running")[:80],
        "stage_label": str(stage_label or "正在运行选股扫描")[:160],
        "completed": max(0, int(completed or 0)),
        "total": max(0, int(total or 0)),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    for name in (
        "cache_hits",
        "network_fallbacks",
        "worker_count",
        "source",
        "error_code",
    ):
        if name in fields:
            payload[name] = fields[name]
    try:
        write_json_cache(Path(raw_path).expanduser(), payload)
    except OSError:
        pass


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


def prepare_threaded_native_javascript_runtime() -> bool:
    """Initialize MiniRacer once before akshare work enters thread pools.

    V8's process-wide partition allocator can abort the interpreter when several
    MiniRacer contexts race through their first initialization. Keeping one
    warmed context alive makes later per-request contexts safe to create from
    worker threads. If initialization is unavailable, callers must fall back to
    serial execution rather than risk terminating the scanner process.
    """
    global _NATIVE_JAVASCRIPT_CONTEXT
    if _NATIVE_JAVASCRIPT_CONTEXT is not None:
        return True
    with _NATIVE_JAVASCRIPT_CONTEXT_LOCK:
        if _NATIVE_JAVASCRIPT_CONTEXT is not None:
            return True
        try:
            from py_mini_racer import MiniRacer

            context = MiniRacer()
            context.eval("1")
        except Exception as exc:
            print(
                f"[WARN] native JavaScript runtime prewarm failed: {type(exc).__name__}",
                file=sys.stderr,
            )
            return False
        _NATIVE_JAVASCRIPT_CONTEXT = context
        return True

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


def dashboard_env_enabled(name: str, default: bool = True) -> bool:
    raw = dashboard_env_value(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def active_strategy_scorers() -> dict[str, Callable[[list[dict[str, Any]]], dict[str, Any] | None]]:
    enabled = enabled_strategy_ids(enabled_persona_strategy_setting(), strategy_source_setting(), active_strategy_setting())
    return {strategy_id: scorer for strategy_id, scorer in STRATEGY_SCORERS.items() if strategy_id in enabled}


def niuone_mainline_only_mode(argv: list[str] | None = None) -> bool:
    """Return whether this process only refreshes the independent theme view."""
    return NIUONE_MAINLINE_ONLY_FLAG in (sys.argv[1:] if argv is None else argv)


def kline_prewarm_only_mode(argv: list[str] | None = None) -> bool:
    """Return whether this process only refreshes the local daily-K-line cache."""
    return KLINE_PREWARM_ONLY_FLAG in (sys.argv[1:] if argv is None else argv)


def strategy_scorers_for_run(*, niuone_mainline_only: bool = False) -> dict[str, Callable[..., Any]]:
    """Keep research-only scans independent from the configured trading suite."""
    if niuone_mainline_only:
        return {
            strategy_id: scorer
            for strategy_id, scorer in STRATEGY_SCORERS.items()
            if strategy_id in NIUONE_STRATEGY_IDS
        }
    return active_strategy_scorers()


def active_strategy_meta() -> dict[str, dict[str, Any]]:
    return enabled_strategy_meta(enabled_persona_strategy_setting(), strategy_source_setting(), active_strategy_setting())


def active_strategy_score_profiles() -> dict[str, dict[str, Any]]:
    return enabled_strategy_score_profiles(enabled_persona_strategy_setting(), strategy_source_setting(), active_strategy_setting())


def preset_text_candidate_limit() -> int:
    try:
        value = int(
            dashboard_env_value(PRESET_TEXT_CANDIDATE_LIMIT_ENV)
            or DEFAULT_PRESET_TEXT_CANDIDATE_LIMIT
        )
    except (TypeError, ValueError):
        value = DEFAULT_PRESET_TEXT_CANDIDATE_LIMIT
    return max(10, min(100, value))


def configured_stock_universe() -> tuple[str, ...]:
    return selected_stock_universe(dashboard_env_value(STOCK_UNIVERSE_ENV))


def scan_stock_universes(
    scorers: Mapping[str, Any],
    configured_universe: object | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return independent trade and market-reference universes for one scan."""
    trade_universe = selected_stock_universe(
        configured_stock_universe() if configured_universe is None else configured_universe
    )
    niuone_enabled = bool(NIUONE_STRATEGY_IDS.intersection(scorers))
    reference_universe = FULL_SUPPORTED_NON_ST_UNIVERSE if niuone_enabled else trade_universe
    return trade_universe, reference_universe


def merge_stock_code_pools(*pools: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Merge stock pools by code while retaining deterministic ordering."""
    merged: dict[str, str] = {}
    for pool in pools:
        for code, name in pool:
            normalized_code = normalize_stock_code(code)
            if normalized_code:
                merged[normalized_code] = str(name or "").strip()
    return sorted(merged.items())


def candidate_in_configured_stock_universe(candidate: dict[str, Any]) -> bool:
    return stock_in_universe(
        candidate.get("code"),
        candidate.get("name"),
        configured_stock_universe(),
    )


def niuone_lifecycle_candidate_metadata(
    scored: Mapping[str, Any],
) -> dict[str, Any]:
    """Project the complete five-stage contract into candidate telemetry."""
    return {
        key: scored.get(key)
        for key in (
            "niuone_lifecycle_stage",
            "niuone_lifecycle_label",
            "niuone_lifecycle_order",
            "niuone_lifecycle_entry_policy",
        )
    }


# ========== Tencent data fetchers ==========

class TencentQuoteBatchError(RuntimeError):
    """A bounded Tencent quote request exhausted its safe retry budget."""


class _EmptyTencentQuoteResponse(ValueError):
    pass


def _tencent_quote_error_label(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
        return "timeout"
    if isinstance(exc, _EmptyTencentQuoteResponse):
        return "empty_or_unusable_response"
    return type(exc).__name__


def _parse_tencent_batch_quote(text: str) -> dict[str, dict[str, Any]]:
    results = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip().lstrip("v_")
        val = val.strip().strip('"')
        parts = val.split("~")
        if len(parts) < 39:
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
            "open": safe_float(parts[5]),
            "change_pct": change_pct,
            "amount": amount,
            "volume": safe_float(parts[6]),
            "high": safe_float(parts[33]),
            "low": safe_float(parts[34]),
            "turnover": safe_float(parts[38]),
            "quote_time": parts[30] if len(parts) > 30 else "",
        }
    return results


def tencent_batch_quote(
    codes: list[str] | tuple[str, ...],
    *,
    timeout_seconds: float = TENCENT_QUOTE_TIMEOUT_SECONDS,
    max_attempts: int = TENCENT_QUOTE_MAX_ATTEMPTS,
    backoff_seconds: float = TENCENT_QUOTE_BACKOFF_SECONDS,
    batch_label: str = "",
    sleep_fn: Callable[[float], None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch one quote batch with bounded retries and sanitized diagnostics."""
    code_list = [str(code).strip() for code in codes if str(code).strip()]
    if not code_list:
        return {}
    attempts = max(1, int(max_attempts))
    timeout = max(0.1, float(timeout_seconds))
    base_backoff = max(0.0, float(backoff_seconds))
    active_sleep = sleep_fn or time.sleep
    scope = f" batch={batch_label}" if batch_label else ""
    last_error: BaseException | None = None
    attempts_used = 0

    for attempt in range(1, attempts + 1):
        attempts_used = attempt
        try:
            url = TENCENT_QUOTE + ",".join(code_list)
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                text = response.read().decode("gbk", "ignore")
            results = _parse_tencent_batch_quote(text)
            if not results:
                raise _EmptyTencentQuoteResponse()
            return results
        except urllib.error.HTTPError as exc:
            try:
                exc.close()
            except OSError:
                pass
            last_error = exc
            retryable = exc.code in {408, 429} or exc.code >= 500
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.HTTPException,
            _EmptyTencentQuoteResponse,
        ) as exc:
            last_error = exc
            retryable = True

        if attempt >= attempts or not retryable:
            break
        delay = base_backoff * (2 ** (attempt - 1))
        print(
            f"[WARN] Tencent quote{scope} attempt={attempt}/{attempts} failed "
            f"error={_tencent_quote_error_label(last_error)} retry_in={delay:.1f}s",
            file=sys.stderr,
        )
        if delay > 0:
            active_sleep(delay)

    raise TencentQuoteBatchError(
        f"Tencent quote{scope} failed after {attempts_used}/{attempts} attempts: "
        f"{_tencent_quote_error_label(last_error or RuntimeError())}"
    ) from last_error


def bounded_quote_request_timeout(
    remaining_seconds: float,
    *,
    max_attempts: int = TENCENT_QUOTE_MAX_ATTEMPTS,
    backoff_seconds: float = TENCENT_QUOTE_BACKOFF_SECONDS,
) -> float:
    """Keep one retrying quote batch inside the remaining stage budget."""
    attempts = max(1, int(max_attempts))
    backoff_budget = max(0.0, float(backoff_seconds)) * sum(
        2 ** index for index in range(max(0, attempts - 1))
    )
    request_budget = max(0.1, float(remaining_seconds) - backoff_budget)
    return max(0.1, min(TENCENT_QUOTE_TIMEOUT_SECONDS, request_budget / attempts))


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
    for symbol, quote in (quotes or {}).items():
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
            "limit_pct": (
                4.8
                if stock_name_is_st(quote.get("name"))
                else 19.8
                if stock_board(symbol) in {"chi_next", "star_market"}
                else 9.8
            ),
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
        "limit_up": sum(1 for row in rows if row["change_pct"] >= row["limit_pct"]),
        "limit_down": sum(1 for row in rows if row["change_pct"] <= -row["limit_pct"]),
        "average_change_pct": round(statistics.mean(changes), 3) if changes else None,
        "median_change_pct": round(statistics.median(changes), 3) if changes else None,
        "total_amount": round(sum(row["amount"] for row in rows), 2),
    }


def filter_high_liquidity_candidates(
    candidates: list[tuple[str, str]],
    tencent_keys: Mapping[str, str],
    quotes: Mapping[str, dict[str, Any]],
    *,
    limit: int | None = None,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Return valid high-liquidity stocks, optionally capped after amount ranking."""
    selected: list[tuple[str, str, dict[str, Any]]] = []
    for code, name in candidates:
        quote = quotes.get(tencent_keys.get(code, ""), {})
        price = safe_float(quote.get("price"))
        amount = safe_float(quote.get("amount")) or 0.0
        if price is None or price <= 0 or amount < HIGH_LIQUIDITY_MIN_AMOUNT:
            continue
        selected.append((code, name, quote))
    selected.sort(key=lambda item: safe_float(item[2].get("amount")) or 0.0, reverse=True)
    return selected if limit is None else selected[:max(0, int(limit))]


def filter_niuone_reference_candidates(
    candidates: list[tuple[str, str]],
    tencent_keys: Mapping[str, str],
    quotes: Mapping[str, dict[str, Any]],
) -> list[tuple[str, str, dict[str, Any]]]:
    """Return every stock with a usable quote, without liquidity/move gates."""
    selected: list[tuple[str, str, dict[str, Any]]] = []
    for code, name in candidates:
        quote = quotes.get(tencent_keys.get(code, ""), {})
        price = safe_float(quote.get("price"))
        if price is None or price <= 0:
            continue
        selected.append((code, name, quote))
    return selected


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
    """Backward-compatible loader backed by the shared multi-source client."""
    return fetch_a_share_daily_klines(symbol, count)


# ========== Multi-Strategy Analysis ==========

def prepare_strategy_rows(
    symbol: str,
    tencent_key: str,
    *,
    quote: dict[str, Any] | None = None,
    name: str = "",
    industry: str = "",
    historical_rows: list[dict[str, Any]] | None = None,
    kline_loader: Callable[[str, int], list[dict[str, Any]]] | None = None,
    fetched_callback: Callable[[str, list[dict[str, Any]]], None] | None = None,
    kline_count: int = DEFAULT_KLINE_COUNT,
    enrich_legacy_indicators: bool = True,
    minimum_rows: int = 30,
) -> list[dict[str, Any]] | None:
    """Fetch a stock once and optionally prepare legacy-suite indicators."""
    rows = [dict(row) for row in historical_rows] if historical_rows else []
    if not rows:
        try:
            rows = (kline_loader or tencent_klines)(
                tencent_key,
                max(1, min(501, int(kline_count or DEFAULT_KLINE_COUNT))),
            )
        except Exception:
            return None
        if rows and fetched_callback is not None:
            fetched_callback(tencent_key, rows)
    rows = merge_live_quote(rows, quote, limit=kline_count)
    if len(rows) < max(1, int(minimum_rows or 1)):
        return None

    # Frozen prompt strategies materialize only their compiled dependencies later.
    # Other suites still share the established legacy enrichment pass.
    if enrich_legacy_indicators:
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
    historical_rows: list[dict[str, Any]] | None = None,
    fetched_callback: Callable[[str, list[dict[str, Any]]], None] | None = None,
    context: dict[str, Any] | None = None,
    scorers: dict[str, Callable[..., dict[str, Any] | None]] | None = None,
    kline_count: int = DEFAULT_KLINE_COUNT,
    enrich_legacy_indicators: bool = True,
    minimum_rows: int = 30,
):
    """Run all active strategies, optionally in one shared cross-sectional context."""
    prepared = rows or prepare_strategy_rows(
        symbol,
        tencent_key,
        quote=quote,
        name=name,
        industry=industry,
        historical_rows=historical_rows,
        fetched_callback=fetched_callback,
        kline_count=kline_count,
        enrich_legacy_indicators=enrich_legacy_indicators,
        minimum_rows=minimum_rows,
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


def load_previous_niuone_context() -> dict[str, Any] | None:
    """Load the prior 牛牛战法 state and retain its persisted market date."""
    candidates: list[tuple[str, int, dict[str, Any]]] = []
    for path in (
        NIUONE_MAINLINE_MINUTE_CACHE,
        NIUONE_MAINLINE_CACHE,
        MULTI_STRATEGY_CACHE,
    ):
        context = load_cached_niuone_context(path)
        if context is None:
            continue
        try:
            modified_ns = int(path.stat().st_mtime_ns)
        except OSError:
            modified_ns = 0
        context_time = re.sub(
            r"\D",
            "",
            str(context.get("sample_at") or context.get("as_of_date") or ""),
        )[:14].ljust(14, "0")
        candidates.append((context_time, modified_ns, context))
    return max(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else None


def resolve_niuone_trading_dates(
    prepared_items: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    status_loader: Callable[..., dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Resolve the quote/K-line market date and its exact prior trading day."""
    date_counts: dict[str, int] = {}
    for item in prepared_items:
        quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
        quote_date = re.search(r"(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})", str(quote.get("quote_time") or ""))
        if quote_date:
            value = f"{quote_date.group('year')}-{quote_date.group('month')}-{quote_date.group('day')}"
            date_counts[value] = date_counts.get(value, 0) + 1
            continue
        rows = item.get("rows") if isinstance(item.get("rows"), list) else []
        latest = rows[-1] if rows and isinstance(rows[-1], dict) else {}
        matched = re.search(r"\d{4}-\d{2}-\d{2}", str(latest.get("date") or ""))
        if matched:
            value = matched.group(0)
            date_counts[value] = date_counts.get(value, 0) + 1
    if date_counts:
        as_of_date = max(date_counts, key=lambda value: (date_counts[value], value))
    else:
        as_of_date = (now or datetime.now()).strftime("%Y-%m-%d")
    if status_loader is None:
        from a_share_calendar import trading_day_status

        status_loader = trading_day_status
    try:
        status = status_loader(as_of_date, allow_refresh=False)
        previous_trading_day = str(status.get("previous_trading_day") or "")[:10]
    except Exception:
        previous_trading_day = ""
    return as_of_date, previous_trading_day


def resolve_quote_trading_dates(
    quotes: Mapping[str, Mapping[str, Any]],
    *,
    now: datetime | None = None,
    status_loader: Callable[..., dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Resolve the current and previous market dates before K-line preparation."""
    return resolve_niuone_trading_dates(
        [{"quote": quote} for quote in (quotes or {}).values() if isinstance(quote, Mapping)],
        now=now,
        status_loader=status_loader,
    )


def fetch_industry_money_flow() -> dict[str, Any]:
    """Return the same cached industry main-flow snapshot used by Dashboard."""
    try:
        from dashboard.apis.money_flow_service import fetch_money_flow

        payload = fetch_money_flow()
        return payload if isinstance(payload, dict) else {"inflow": [], "outflow": []}
    except Exception as exc:
        print(f"[WARN] industry money flow unavailable: {type(exc).__name__}; using neutral fallback", file=sys.stderr)
        return {"inflow": [], "outflow": []}


def fetch_sector_tide_money_flow() -> dict[str, Any]:
    """Backward-compatible alias for integrations patching the old helper."""
    return fetch_industry_money_flow()


def sector_tide_dragon_tiger_snapshot_file() -> Path:
    """Resolve the rolling latest snapshot used as prior-day confirmation."""

    return Path(
        os.environ.get("IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE")
        or B1_OUTPUT_DIR / "iwencai_dragon_tiger_latest.json"
    ).expanduser()


def sector_tide_dragon_tiger_archive_dir() -> Path:
    """Compatibility path for integrations still injecting an archive reader."""

    return sector_tide_dragon_tiger_snapshot_file().parent / "iwencai_dragon_tiger"


def load_previous_sector_tide_dragon_tiger(
    now: datetime | None = None,
    *,
    snapshot_path: Path | None = None,
    snapshot_reader: Callable[..., dict[str, Any] | None] | None = None,
    archive_dir: Path | None = None,
    status_loader: Callable[..., dict[str, Any]] | None = None,
    archive_reader: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Load the rolling snapshot only when it is the exact prior trading day."""
    if status_loader is None:
        from a_share_calendar import trading_day_status

        status_loader = trading_day_status
    if snapshot_reader is None and archive_reader is None:
        if archive_dir is not None:
            from dashboard.apis.iwencai_service import read_dragon_tiger_archive

            archive_reader = read_dragon_tiger_archive
        else:
            from dashboard.apis.iwencai_service import read_dragon_tiger_snapshot

            snapshot_reader = read_dragon_tiger_snapshot

    current = now or datetime.now()
    try:
        calendar = status_loader(current, allow_refresh=False)
    except Exception as exc:
        return {
            "available": False,
            "source": "local_dragon_tiger_snapshot",
            "date": "",
            "requested_date": "",
            "items": [],
            "error": f"calendar_{type(exc).__name__}",
        }
    previous_date = str(calendar.get("previous_trading_day") or "")
    unavailable = {
        "available": False,
        "source": "local_dragon_tiger_snapshot",
        "date": previous_date,
        "requested_date": previous_date,
        "items": [],
        "calendar_source": str(calendar.get("source") or ""),
    }
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", previous_date):
        unavailable["error"] = "previous_trading_day_unavailable"
        return unavailable
    try:
        if archive_reader is not None:
            snapshot = archive_reader(
                archive_dir or sector_tide_dragon_tiger_archive_dir(),
                trade_date=previous_date,
            )
        else:
            snapshot = snapshot_reader(
                snapshot_path or sector_tide_dragon_tiger_snapshot_file(),
                trade_date=previous_date,
            )
    except Exception as exc:
        unavailable["error"] = f"snapshot_read_{type(exc).__name__}"
        return unavailable
    if not isinstance(snapshot, dict):
        unavailable["error"] = "snapshot_missing"
        return unavailable
    payload = dict(snapshot)
    payload["requested_date"] = previous_date
    payload["calendar_source"] = str(calendar.get("source") or "")
    return payload


def load_sector_tide_overnight_us(
    now: datetime | None = None,
    *,
    summary_loader: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Load only the cache validated for today's completed US session."""
    if summary_loader is None:
        import us_market_summary

        summary_loader = us_market_summary.load_cached_summary_for_today
    try:
        summary = summary_loader(now)
    except Exception as exc:
        return {
            "available": False,
            "source": "overnight_us_market_summary",
            "error": f"cache_read_{type(exc).__name__}",
        }
    if not isinstance(summary, dict):
        return {
            "available": False,
            "source": "overnight_us_market_summary",
            "error": "cache_missing_or_stale",
        }
    return dict(summary)


def fetch_sector_tide_news_precheck(
    candidates: list[dict[str, Any]],
    now: datetime | None = None,
    *,
    config: NewsPrecheckConfig | None = None,
    fetcher: Callable[..., list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Fetch bounded structured news for a first-pass dynamic-strategy shortlist."""
    selected = [item for item in candidates[:SECTOR_TIDE_NEWS_PRECHECK_LIMIT] if isinstance(item, dict)]
    if not selected:
        return {"configured": False, "available": False, "records": [], "error": "empty_shortlist"}
    try:
        active_config = config or NewsPrecheckConfig.from_mapping({
            name: dashboard_env_value(name) or ""
            for name in (
                "IWENCAI_NEWS_PRECHECK_ENABLED",
                "IWENCAI_ENABLED",
                "IWENCAI_BASE_URL",
                "IWENCAI_API_KEY",
                "IWENCAI_TIMEOUT_SECONDS",
                "IWENCAI_MAX_RETRIES",
                "IWENCAI_MAX_CONCURRENCY",
                "DASHBOARD_DECISION_MODEL",
                "DASHBOARD_DECISION_BASE_URL",
                "DASHBOARD_DECISION_API_KEY",
                "DASHBOARD_DECISION_STREAM_MODE",
                "DASHBOARD_DECISION_REASONING_EFFORT",
                "DASHBOARD_DECISION_TIMEOUT",
                "DASHBOARD_DECISION_MAX_TOKENS",
                "A_SHARE_MODEL_SUMMARY_MODEL",
                "A_SHARE_MODEL_SUMMARY_BASE_URL",
                "A_SHARE_MODEL_SUMMARY_API_KEY",
                "A_SHARE_MODEL_SUMMARY_STREAM_MODE",
                "A_SHARE_MODEL_SUMMARY_REASONING_EFFORT",
                "A_SHARE_MODEL_SUMMARY_MAX_TOKENS",
                "CROSSDESK_BASE_URL",
                "CROSSDESK_API_KEY",
                "DASHBOARD_CONFIG",
            )
        })
    except ValueError as exc:
        return {
            "configured": True,
            "available": False,
            "records": [],
            "error": str(exc).split(":", 1)[0],
        }
    if active_config is None:
        return {"configured": False, "available": False, "records": [], "error": "not_configured"}
    active_fetcher = fetcher or fetch_candidate_news_records
    try:
        records = active_fetcher(
            selected,
            active_config,
            max_candidates=SECTOR_TIDE_NEWS_PRECHECK_LIMIT,
            now=now,
        )
    except Exception as exc:
        return {
            "configured": True,
            "available": False,
            "records": [],
            "error": f"fetch_{type(exc).__name__}",
        }
    return {
        "configured": True,
        "available": any(record.get("available") for record in records),
        "fetched_at": next(
            (str(record.get("fetched_at") or "") for record in records if record.get("fetched_at")),
            "",
        ),
        "records": records,
        "error": "" if any(record.get("available") for record in records) else "all_records_unavailable",
    }


def niuone_news_shortlist(
    context: Mapping[str, Any] | None,
    limit: int = SECTOR_TIDE_NEWS_PRECHECK_LIMIT,
) -> list[dict[str, Any]]:
    """Select the strongest NiuOne names without using the active trade suite."""
    themes = context.get("themes") if isinstance(context, Mapping) else {}
    if not isinstance(themes, Mapping):
        return []
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for theme in themes.values():
        if not isinstance(theme, Mapping):
            continue
        industry = str(theme.get("industry") or "").strip()
        for stock in theme.get("strong_stocks") or []:
            if not isinstance(stock, Mapping):
                continue
            code = normalize_stock_code(stock.get("code"))
            if not code or code in seen:
                continue
            seen.add(code)
            candidates.append({
                "code": code,
                "name": str(stock.get("name") or "").strip(),
                "industry": industry,
                "strong_score": float(stock.get("strong_score") or 0.0),
            })
    candidates.sort(
        key=lambda item: (
            float(item.get("strong_score") or 0.0),
            str(item.get("code") or ""),
        ),
        reverse=True,
    )
    return candidates[:max(0, int(limit))]


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


def load_bulk_stock_board_map(codes: set[str]) -> dict[str, EastmoneyStockBoard]:
    """Load one batch of current Eastmoney industries and concepts."""
    targets = {normalize_stock_code(code) for code in codes}
    targets.discard("")
    if not targets:
        return {}
    snapshot = load_eastmoney_board_snapshot(cache_path=EASTMONEY_BOARD_CACHE)
    return snapshot.subset(targets)


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


def load_bulk_stock_industry_map(codes: set[str]) -> dict[str, str]:
    """Return only Eastmoney ``f100`` industries for compatibility callers."""
    return {
        code: stock.industry
        for code, stock in load_bulk_stock_board_map(codes).items()
        if stock.industry
    }


def lookup_stock_industry(code: str, ak_module: Any | None = None) -> str:
    """Resolve one industry from the same Eastmoney batch source."""
    code = normalize_stock_code(code)
    if not code:
        return ""
    stock = load_bulk_stock_board_map({code}).get(code)
    return stock.industry if stock is not None else ""


def annotate_candidate_industries(
    *groups: list[dict[str, Any]],
    lookup: Callable[[str], str | None] | None = None,
    bulk_lookup: Callable[[set[str]], Mapping[str, str]] | None = None,
    max_fallback_lookups: int | None = None,
    max_workers: int = 1,
) -> None:
    """Attach Eastmoney industry plus multi-label concepts to candidate rows."""
    if lookup is None and bulk_lookup is None:
        items = [
            item
            for group in groups
            for item in (group or [])
            if isinstance(item, dict)
        ]
        codes = {
            code for item in items if (code := normalize_stock_code(item.get("code")))
        }
        boards = load_bulk_stock_board_map(codes)
        industry_cache: dict[str, str] = {}
        for item in items:
            code = normalize_stock_code(item.get("code"))
            stock = boards.get(code)
            if stock is None:
                item.pop("industry", None)
                item.pop("sector", None)
                item.pop("themes", None)
                continue
            industry = normalize_industry_name(stock.industry)
            themes = list(dict.fromkeys(
                label
                for raw in stock.themes
                if (label := normalize_industry_name(raw))
            ))
            if industry:
                item["industry"] = industry
                item["sector"] = industry
                industry_cache[code] = industry
            else:
                item.pop("industry", None)
                item.pop("sector", None)
            if themes:
                item["themes"] = themes
            else:
                item.pop("themes", None)
        save_stock_industry_cache(industry_cache)
        return

    # Explicit test/compatibility hooks retain the former single-label contract,
    # but production does not call them and never reads a non-Eastmoney cache.
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
        cache_changed = False
        cached_count = sum(1 for code in missing_by_code if cache.get(code))
        cache_coverage = cached_count / len(missing_by_code) if missing_by_code else 1.0

        if (
            bulk_lookup is not None
            and cache_coverage < STOCK_INDUSTRY_BULK_CACHE_MIN_COVERAGE
        ):
            try:
                bulk = bulk_lookup(set(missing_by_code))
            except Exception as exc:
                print(
                    f"[WARN] bulk stock industry lookup failed: {type(exc).__name__}",
                    file=sys.stderr,
                )
                bulk = {}
            for code, industry in (bulk or {}).items():
                normalized_code = normalize_stock_code(code)
                normalized_industry = normalize_industry_name(industry)
                if normalized_code in missing_by_code and normalized_industry:
                    cache[normalized_code] = normalized_industry
                    cache_changed = True
                    fill_code(normalized_code, normalized_industry)

        for code in list(missing_by_code):
            fill_code(code, cache.get(code, ""))

        if missing_by_code:
            missing_codes = list(missing_by_code)
            skipped_count = 0
            if max_fallback_lookups is not None:
                lookup_limit = max(0, int(max_fallback_lookups))
                skipped_count = max(0, len(missing_codes) - lookup_limit)
                missing_codes = missing_codes[:lookup_limit]
            resolved: dict[str, str] = {}
            workers = max(1, min(int(max_workers or 1), 12, len(missing_codes) or 1))
            if workers > 1 and not prepare_threaded_native_javascript_runtime():
                workers = 1
            if workers > 1 and missing_codes:
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                    future_by_code = {pool.submit(lookup_stock_industry, code): code for code in missing_codes}
                    for future in concurrent.futures.as_completed(future_by_code):
                        code = future_by_code[future]
                        try:
                            resolved[code] = normalize_industry_name(future.result())
                        except Exception:
                            resolved[code] = ""
            elif missing_codes:
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
            if skipped_count:
                print(
                    f"[WARN] skipped {skipped_count} per-stock industry fallbacks after bulk lookup",
                    file=sys.stderr,
                )
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


def _valid_archive_date(value: str) -> bool:
    try:
        parsed = datetime.strptime(str(value), "%Y-%m-%d")
    except ValueError:
        return False
    return parsed.strftime("%Y-%m-%d") == value


def _archive_date_directories(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        return []
    return sorted(
        path
        for path in root.iterdir()
        if not path.is_symlink()
        and path.is_dir()
        and _valid_archive_date(path.name)
    )


def _archive_json_files(date_dir: Path) -> list[Path]:
    archives: list[Path] = []
    for path in date_dir.iterdir():
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            continue
        try:
            timestamp = datetime.strptime(path.stem, "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            continue
        if timestamp.strftime("%Y-%m-%d") != date_dir.name:
            continue
        archives.append(path)
    return sorted(archives, key=lambda path: path.name)


def cleanup_scan_history(
    active_date: str,
    *,
    legacy_history_dir: Path | None = None,
    primary_history_dir: Path | None = None,
    retention_dates: int = SCAN_HISTORY_RETENTION_DATES,
    max_files_per_date: int = SCAN_HISTORY_MAX_FILES_PER_DATE,
) -> dict[str, int]:
    """Retire legacy B1 archives and bound the primary scan history.

    Only timestamped JSON files inside validated date directories are eligible
    for deletion. Unknown files, nested directories, and symbolic links are
    deliberately preserved.
    """
    if not _valid_archive_date(active_date):
        raise ValueError("active scan archive date must use YYYY-MM-DD")
    if retention_dates < 1:
        raise ValueError("scan history must retain at least one archive date")
    if max_files_per_date < 1:
        raise ValueError("scan history must retain at least one file per date")

    legacy_root = Path(legacy_history_dir or B1_HISTORY_DIR)
    primary_root = Path(primary_history_dir or MULTI_STRATEGY_HISTORY)
    removed_legacy = 0
    removed_primary = 0
    failures: list[str] = []

    def remove_archives(paths: list[Path], *, legacy: bool) -> None:
        nonlocal removed_legacy, removed_primary
        for path in paths:
            try:
                path.unlink()
            except OSError as exc:
                failures.append(type(exc).__name__)
            else:
                if legacy:
                    removed_legacy += 1
                else:
                    removed_primary += 1

    for date_dir in _archive_date_directories(legacy_root):
        remove_archives(_archive_json_files(date_dir), legacy=True)
        try:
            date_dir.rmdir()
        except OSError:
            pass
    try:
        legacy_root.rmdir()
    except OSError:
        pass

    primary_dates = _archive_date_directories(primary_root)
    retained_names = {
        path.name for path in primary_dates[-retention_dates:]
    }
    retained_names.add(active_date)
    for date_dir in primary_dates:
        archives = _archive_json_files(date_dir)
        if date_dir.name not in retained_names:
            remove_archives(archives, legacy=False)
            try:
                date_dir.rmdir()
            except OSError:
                pass
            continue
        remove_archives(
            archives[:-max_files_per_date],
            legacy=False,
        )

    if failures:
        error_types = ",".join(sorted(set(failures)))
        print(
            "[WARN] scan history cleanup incomplete: "
            f"failures={len(failures)} error_types={error_types}",
            file=sys.stderr,
        )
    return {
        "legacy_removed": removed_legacy,
        "primary_removed": removed_primary,
    }


def write_outputs(
    payload: Mapping[str, Any],
    generated_at: str,
    *,
    json_str: str | None = None,
) -> None:
    """Write latest caches and one bounded multi-strategy history."""
    B1_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Primary archive. The former B1 history was an identical compatibility
    # copy and is removed by the bounded cleanup below.
    safe_ts = str(generated_at).replace(":", "-").replace(" ", "_")
    date_part = safe_ts.split("_")[0]
    d = MULTI_STRATEGY_HISTORY / date_part
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{safe_ts}.json"
    # Serialize once to disk, then copy in bounded chunks. Keep all three
    # snapshots identical even if another producer replaces a latest cache.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as serialized:
        if json_str is not None:
            serialized.write(json_str)
        else:
            json.dump(payload, serialized, ensure_ascii=False, indent=2)
        serialized.write("\n")
        for target in (MULTI_STRATEGY_CACHE, B1_CACHE_FILE, f):
            temporary = target.with_name(
                f".{target.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
            )
            try:
                serialized.seek(0)
                with temporary.open("w", encoding="utf-8") as stream:
                    shutil.copyfileobj(serialized, stream, length=1024 * 1024)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)

    # The Dashboard polls this bounded read model instead of the full scan.
    write_practice_candidates_cache(
        PRACTICE_CANDIDATES_CACHE,
        payload,
        source_path=MULTI_STRATEGY_CACHE,
    )
    try:
        cleanup_scan_history(date_part)
    except (OSError, ValueError) as exc:
        print(
            "[WARN] scan history cleanup failed: "
            f"error_type={type(exc).__name__}",
            file=sys.stderr,
        )


def prewarm_full_market_klines(
    *,
    workers: int | None = None,
    target_date: str = "",
    fetcher: Callable[[str, int], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Populate the private SQLite cache for every supported non-ST A share."""
    resolved_target_date = str(
        target_date
        or os.environ.get("DASHBOARD_KLINE_PREWARM_TARGET_DATE")
        or datetime.now().strftime("%Y-%m-%d")
    )[:10]
    candidates = load_a_share_code_pool(FULL_SUPPORTED_NON_ST_UNIVERSE)
    symbols = [
        ("sh" if code.startswith(("6", "9")) else "sz") + code
        for code, _name in candidates
    ]
    if workers is None:
        try:
            workers = int(
                dashboard_env_value("DASHBOARD_KLINE_PREWARM_WORKERS")
                or DEFAULT_PREWARM_WORKERS
            )
        except (TypeError, ValueError):
            workers = DEFAULT_PREWARM_WORKERS

    def progress(completed: int, total: int, failures: int) -> None:
        report_scan_progress(
            "kline_prewarm",
            stage_label="正在初始化全市场日K数据",
            completed=completed,
            total=total,
            network_fallbacks=failures,
            source="tencent_kline",
        )
        print(
            f"  ... {completed}/{total} daily K-line series prepared; failures={failures}",
            file=sys.stderr,
        )

    accepted_last_dates: set[str] = set()
    if dashboard_env_enabled("DASHBOARD_KLINE_PREWARM_RESUME", False):
        try:
            from a_share_calendar import trading_day_status

            calendar = trading_day_status(resolved_target_date, allow_refresh=False)
            accepted_last_dates = {
                str(calendar.get("date") or "")[:10],
                str(calendar.get("previous_trading_day") or "")[:10],
            }
            accepted_last_dates = {
                value
                for value in accepted_last_dates
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
            }
        except Exception:
            accepted_last_dates = set()

    return prewarm_kline_cache(
        symbols,
        path=kline_cache_path(),
        target_date=resolved_target_date,
        workers=workers,
        accepted_last_dates=accepted_last_dates,
        fetcher=fetcher,
        progress=progress,
    )


def main():
    if kline_prewarm_only_mode():
        report_scan_progress(
            "kline_prewarm",
            stage_label="正在初始化全市场日K数据",
        )
        print("Pre-market task: warming full-market daily K-line SQLite cache...", file=sys.stderr)
        result = prewarm_full_market_klines()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(
            "  K-line cache prewarm completed: "
            f"success={result.get('success_count', 0)}/"
            f"{result.get('requested_count', 0)} "
            f"failures={result.get('failure_count', 0)} "
            f"duration={result.get('duration_seconds', 0)}s",
            file=sys.stderr,
        )
        return

    report_scan_progress("code_pool", stage_label="正在加载A股代码池")
    print("Step 1: Loading A-share code pool...", file=sys.stderr)
    niuone_mainline_only = niuone_mainline_only_mode()
    scorers = strategy_scorers_for_run(niuone_mainline_only=niuone_mainline_only)
    sector_tide_enabled = bool(SECTOR_TIDE_STRATEGY_IDS.intersection(scorers))
    niuone_enabled = bool(NIUONE_STRATEGY_IDS.intersection(scorers))
    zettaranc_enabled = bool(ZETTARANC_STRATEGY_IDS.intersection(scorers))
    preset_text_enabled = STRATEGY_SUITE_PRESET_TEXT in scorers
    prompt_strategy_version: dict[str, Any] | None = None
    prompt_strategy_store: PromptStrategyStore | None = None
    prompt_runtime_data_context: dict[str, Any] = {}
    prompt_selection_minimum_bars = DEFAULT_KLINE_COUNT
    if preset_text_enabled:
        prompt_strategy_store = PromptStrategyStore()
        prompt_strategy_version = prompt_strategy_store.active_version()
        if prompt_strategy_version is not None:
            scorers = dict(scorers)
            scorers[STRATEGY_SUITE_PRESET_TEXT] = (
                lambda rows, version=prompt_strategy_version: score_prompt_selection(
                    rows,
                    version,
                    data_context=prompt_runtime_data_context,
                )
            )
            prompt_selection_minimum_bars = max(
                1,
                min(
                    500,
                    int(
                        ((prompt_strategy_version.get("execution_plan") or {}).get(
                            "stage_requirements"
                        ) or {}).get("selection", {}).get(
                            "minimum_bars",
                            DEFAULT_KLINE_COUNT,
                        )
                    ),
                ),
            )
            print(
                "  Frozen prompt strategy: "
                f"version={prompt_strategy_version.get('version_id')} "
                f"plan={str(prompt_strategy_version.get('plan_sha256') or '')[:12]}",
                file=sys.stderr,
            )
        else:
            print(
                "  No activated prompt-strategy version; using legacy neutral candidate mode",
                file=sys.stderr,
            )
    prompt_only_runtime = (
        prompt_strategy_version is not None
        and set(scorers) == {STRATEGY_SUITE_PRESET_TEXT}
    )
    prompt_selection_kline_count = prompt_selection_minimum_bars
    if prompt_strategy_version is not None and str(
        (((prompt_strategy_version.get("execution_plan") or {}).get("strategy") or {}).get(
            "data_contract"
        ) or {}).get("bar_status") or "closed"
    ) == "closed":
        prompt_selection_kline_count = min(
            501,
            prompt_selection_minimum_bars + 1,
        )
    if niuone_mainline_only:
        print("  Independent theme-strength research mode; trading suite is ignored", file=sys.stderr)
    configured_universe = configured_stock_universe()
    stock_universe, reference_stock_universe = scan_stock_universes(scorers, configured_universe)
    candidates = load_a_share_code_pool(stock_universe)
    if reference_stock_universe == stock_universe:
        reference_candidates = candidates
    else:
        reference_candidates = load_a_share_code_pool(reference_stock_universe)
    scan_candidates = merge_stock_code_pools(candidates, reference_candidates)

    print(
        f"  Configured trade universe ({friendly_stock_universe(stock_universe)}): "
        f"{len(candidates)} stocks",
        file=sys.stderr,
    )
    if niuone_enabled:
        print(
            f"  牛牛 reference universe ({friendly_stock_universe(reference_stock_universe)}、非ST): "
            f"{len(reference_candidates)} stocks; final trades remain limited to configured universe",
            file=sys.stderr,
        )

    report_scan_progress("quotes", stage_label="正在获取全市场实时行情")
    print("Step 2: Fetching real-time batch quotes...", file=sys.stderr)
    tencent_keys = {}
    all_keys = []
    for code, name in scan_candidates:
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        tk = prefix + code
        tencent_keys[code] = tk
        all_keys.append(tk)

    quotes = {}
    batch_size = 150
    batch_total = max(1, (len(all_keys) + batch_size - 1) // batch_size)
    try:
        quote_stage_timeout = float(
            dashboard_env_value("DASHBOARD_TENCENT_QUOTE_STAGE_TIMEOUT_SECONDS")
            or DEFAULT_TENCENT_QUOTE_STAGE_TIMEOUT_SECONDS
        )
    except (TypeError, ValueError):
        quote_stage_timeout = DEFAULT_TENCENT_QUOTE_STAGE_TIMEOUT_SECONDS
    quote_stage_timeout = max(15.0, min(300.0, quote_stage_timeout))
    quote_stage_deadline = time.monotonic() + quote_stage_timeout
    for i in range(0, len(all_keys), batch_size):
        batch = all_keys[i:i + batch_size]
        batch_number = i // batch_size + 1
        remaining_seconds = quote_stage_deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise TencentQuoteBatchError(
                "Tencent quote aggregate deadline exceeded after "
                f"{batch_number - 1}/{batch_total} batches"
            )
        q = tencent_batch_quote(
            batch,
            batch_label=f"{batch_number}/{batch_total}",
            timeout_seconds=bounded_quote_request_timeout(remaining_seconds),
        )
        quotes.update(q)
        report_scan_progress(
            "quotes",
            stage_label="正在获取全市场实时行情",
            completed=batch_number,
            total=batch_total,
            source="tencent_quote",
        )
        time.sleep(0.05)
    reference_keys = {tencent_keys[code] for code, _name in reference_candidates}
    reference_quotes = {key: quote for key, quote in quotes.items() if key in reference_keys}
    market_snapshot = build_market_snapshot(
        reference_quotes,
        pool_count=len(reference_candidates),
        stock_universe=reference_stock_universe,
    )
    try:
        index_quotes = tencent_batch_quote(list(CORE_INDEX_SYMBOLS.values()))
        market_snapshot.update(build_index_risk_snapshot(index_quotes))
    except Exception:
        pass

    if niuone_enabled or preset_text_enabled:
        to_analyze = filter_niuone_reference_candidates(
            candidates,
            tencent_keys,
            quotes,
        )
        context_candidates = (
            [
                (code, name, quotes.get(tencent_keys.get(code, ""), {}))
                for code, name in reference_candidates
            ]
            if niuone_enabled
            else to_analyze
        )
    else:
        liquid = filter_high_liquidity_candidates(candidates, tencent_keys, quotes)
        to_analyze = liquid[:MAX_TRADE_ANALYSIS_COUNT]
        context_candidates = to_analyze
    if niuone_enabled:
        print(
            f"  牛牛 full-market deep analysis: {len(context_candidates)} stocks "
            f"(no turnover/change filter); configured trade pool has "
            f"{len(to_analyze)} stocks with usable quotes",
            file=sys.stderr,
        )
    elif preset_text_enabled:
        print(
            f"  Preset-text neutral deep analysis: {len(to_analyze)} stocks "
            f"with usable quotes; no built-in strategy entry threshold is applied",
            file=sys.stderr,
        )
    else:
        print(
            f"  Trade-pool high liquidity (成交额>8亿): {len(liquid)}, "
            f"analyzing top {len(to_analyze)}",
            file=sys.stderr,
        )

    try:
        scan_workers = int(dashboard_env_value("DASHBOARD_B1_SCAN_WORKERS") or "6")
    except (TypeError, ValueError):
        scan_workers = 6
    worker_target_count = max(
        len(to_analyze),
        len(context_candidates) if niuone_enabled else 0,
        1,
    )
    scan_workers = max(1, min(16, scan_workers, worker_target_count))
    if scan_workers > 1 and not prepare_threaded_native_javascript_runtime():
        print("  Native JavaScript runtime unavailable; falling back to 1 worker", file=sys.stderr)
        scan_workers = 1
    print(
        f"Step 3: Multi-strategy scoring (registered strategy profiles, {scan_workers} workers)...",
        file=sys.stderr,
    )
    kline_cache_enabled = dashboard_env_enabled("DASHBOARD_KLINE_CACHE_ENABLED", True)
    strict_kline_cache = dashboard_env_enabled(
        "DASHBOARD_B1_REQUIRE_READY_CACHE",
        False,
    )
    cached_klines_by_symbol: dict[str, list[dict[str, Any]]] = {}
    pending_kline_cache: dict[str, list[dict[str, Any]]] = {}
    pending_kline_cache_lock = threading.Lock()
    needed_kline_symbols = list(dict.fromkeys(
        tencent_keys[code]
        for code, _name, _quote in [*context_candidates, *to_analyze]
        if code in tencent_keys
    ))
    report_scan_progress(
        "cache_check",
        stage_label="正在检查日K缓存",
        total=len(needed_kline_symbols),
        worker_count=scan_workers,
    )
    scan_as_of_date, scan_previous_trading_day = resolve_quote_trading_dates(
        reference_quotes if niuone_enabled else quotes
    )
    prompt_runtime_data_context.update({
        "expected_closed_date": scan_previous_trading_day,
        "expected_live_date": scan_as_of_date,
        "evaluated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    if kline_cache_enabled:
        accepted_cache_dates = {
            value
            for value in (scan_as_of_date, scan_previous_trading_day)
            if value
        }
        try:
            cached_klines_by_symbol = load_kline_series_map(
                needed_kline_symbols,
                path=kline_cache_path(),
                accepted_last_dates=accepted_cache_dates,
                min_rows=(
                    prompt_selection_minimum_bars
                    if prompt_strategy_version is not None
                    else 30
                ),
                count=(
                    prompt_selection_kline_count
                    if prompt_strategy_version is not None
                    else DEFAULT_KLINE_COUNT
                ),
            )
        except Exception as exc:
            print(
                f"[WARN] local K-line cache unavailable: {type(exc).__name__}; using network fallback",
                file=sys.stderr,
            )
        print(
            "  Daily K-line SQLite cache: "
            f"hits={len(cached_klines_by_symbol)}/{len(needed_kline_symbols)} "
            f"as_of={scan_as_of_date or 'unknown'} "
            f"previous={scan_previous_trading_day or 'unknown'}",
            file=sys.stderr,
        )
        report_scan_progress(
            "cache_check",
            stage_label="正在检查日K缓存",
            completed=len(cached_klines_by_symbol),
            total=len(needed_kline_symbols),
            cache_hits=len(cached_klines_by_symbol),
            network_fallbacks=(
                0
                if strict_kline_cache
                else max(0, len(needed_kline_symbols) - len(cached_klines_by_symbol))
            ),
            worker_count=scan_workers,
        )

    if strict_kline_cache:
        if not kline_cache_enabled:
            raise RuntimeError("ready K-line cache is required but local cache is disabled")
        try:
            minimum_coverage = float(
                dashboard_env_value("DASHBOARD_KLINE_READINESS_MIN_COVERAGE_PERCENT")
                or "90"
            ) / 100
        except (TypeError, ValueError):
            minimum_coverage = 0.9
        minimum_coverage = max(0.9, min(1.0, minimum_coverage))
        cache_coverage = (
            len(cached_klines_by_symbol) / len(needed_kline_symbols)
            if needed_kline_symbols
            else 1.0
        )
        if cache_coverage < minimum_coverage:
            raise RuntimeError(
                "ready K-line cache coverage was lost before scanning: "
                f"{cache_coverage:.1%} < {minimum_coverage:.1%}"
            )

    def remember_fetched_klines(symbol: str, rows: list[dict[str, Any]]) -> None:
        if not kline_cache_enabled or not rows:
            return
        with pending_kline_cache_lock:
            pending_kline_cache[symbol] = rows

    def flush_fetched_klines() -> int:
        if not kline_cache_enabled:
            return 0
        with pending_kline_cache_lock:
            pending = dict(pending_kline_cache)
            pending_kline_cache.clear()
        if not pending:
            return 0
        try:
            stored = store_kline_series(pending, path=kline_cache_path())
            print(f"  Daily K-line SQLite cache filled from fallback: {stored}", file=sys.stderr)
            return stored
        except Exception as exc:
            print(f"[WARN] local K-line cache write failed: {type(exc).__name__}", file=sys.stderr)
            return 0

    sector_tide_context: dict[str, Any] | None = None
    niuone_context: dict[str, Any] | None = None
    strategy_context: dict[str, Any] | None = None
    prepared_by_code: dict[str, list[dict[str, Any]]] = {}
    industry_by_code: dict[str, str] = {}
    prepared_items: list[dict[str, Any]] = []
    sector_tide_flow_rows: dict[str, Any] = {"inflow": [], "outflow": []}
    previous_sector_tide_market: dict[str, Any] | None = None
    previous_niuone_context: dict[str, Any] | None = None
    niuone_as_of_date = ""
    niuone_previous_trading_day = ""
    dragon_tiger_snapshot: dict[str, Any] | None = None
    overnight_us_snapshot: dict[str, Any] | None = None

    industry_members: list[dict[str, Any]] = []
    if sector_tide_enabled or niuone_enabled or zettaranc_enabled:
        report_scan_progress(
            "industry_context",
            stage_label="正在准备行业与题材分类",
            total=len(context_candidates),
        )
        print("  Resolving candidate industries for strategy scoring...", file=sys.stderr)
        industry_members = [
            {"code": code, "name": name, "quote": q}
            for code, name, q in context_candidates
        ]
        context_codes = {str(item["code"]) for item in industry_members}
        trade_only_industry_members = [
            {"code": code, "name": name, "quote": q}
            for code, name, q in to_analyze
            if str(code) not in context_codes
        ]
        annotate_candidate_industries(
            industry_members,
            trade_only_industry_members,
        )
        industry_by_code = {
            str(item["code"]): normalize_industry_name(item.get("industry"))
            for item in [*industry_members, *trade_only_industry_members]
        }
        sector_tide_flow_rows = fetch_sector_tide_money_flow()
        if zettaranc_enabled:
            strategy_context = {"industry_money_flow": sector_tide_flow_rows}

    if sector_tide_enabled or niuone_enabled:
        label = "market/sector tide" if sector_tide_enabled else "strong-stock mainline"
        print(f"  Building shared {label} context...", file=sys.stderr)
        report_scan_progress(
            "kline_prepare",
            stage_label="正在准备全市场日K与题材上下文",
            completed=0,
            total=len(industry_members),
            cache_hits=len(cached_klines_by_symbol),
            network_fallbacks=(
                0
                if strict_kline_cache
                else max(0, len(needed_kline_symbols) - len(cached_klines_by_symbol))
            ),
            worker_count=scan_workers,
        )

        def prepare_context_member(item: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
            code = str(item["code"])
            name = str(item["name"])
            industry = normalize_industry_name(item.get("industry"))
            quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
            historical_rows = cached_klines_by_symbol.get(tencent_keys[code])
            if strict_kline_cache and not historical_rows:
                return item, None
            rows = prepare_strategy_rows(
                code,
                tencent_keys[code],
                quote=quote,
                name=name,
                industry=industry,
                historical_rows=historical_rows,
                fetched_callback=remember_fetched_klines,
                kline_count=(
                    prompt_selection_kline_count
                    if prompt_strategy_version is not None
                    else DEFAULT_KLINE_COUNT
                ),
                enrich_legacy_indicators=not prompt_only_runtime,
                minimum_rows=(
                    prompt_selection_minimum_bars
                    if prompt_strategy_version is not None
                    else 30
                ),
            )
            return item, rows

        if scan_workers > 1:
            context_pool: Any = concurrent.futures.ThreadPoolExecutor(max_workers=scan_workers)
            prepared_context = context_pool.map(prepare_context_member, industry_members)
        else:
            context_pool = None
            prepared_context = map(prepare_context_member, industry_members)
        try:
            for index, (item, rows) in enumerate(prepared_context):
                code = str(item["code"])
                name = str(item["name"])
                industry = normalize_industry_name(item.get("industry"))
                themes = list(item.get("themes") or ())
                quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
                if rows:
                    prepared_by_code[code] = rows
                    industry_by_code[code] = industry
                    prepared_items.append({
                        "code": code,
                        "name": name,
                        "industry": industry,
                        "themes": themes,
                        "quote": quote,
                        "rows": rows,
                    })
                if (index + 1) % 100 == 0:
                    report_scan_progress(
                        "kline_prepare",
                        stage_label="正在准备全市场日K与题材上下文",
                        completed=index + 1,
                        total=len(industry_members),
                        cache_hits=len(cached_klines_by_symbol),
                        network_fallbacks=(
                            0
                            if strict_kline_cache
                            else max(0, len(needed_kline_symbols) - len(cached_klines_by_symbol))
                        ),
                        worker_count=scan_workers,
                    )
                    print(
                        f"  ... {index + 1}/{len(industry_members)} cross-sectional members prepared",
                        file=sys.stderr,
                    )
        finally:
            if context_pool is not None:
                context_pool.shutdown(wait=True)
        flush_fetched_klines()
        if niuone_enabled:
            niuone_as_of_date, niuone_previous_trading_day = resolve_niuone_trading_dates(prepared_items)
        dragon_tiger_snapshot = load_previous_sector_tide_dragon_tiger()
        if sector_tide_enabled:
            previous_sector_tide_market = load_previous_sector_tide_market()
            overnight_us_snapshot = load_sector_tide_overnight_us()
            sector_tide_context = build_sector_tide_context(
                prepared_items,
                market_snapshot=market_snapshot,
                flow_rows=sector_tide_flow_rows,
                previous_market=previous_sector_tide_market,
                dragon_tiger_snapshot=dragon_tiger_snapshot,
                overnight_us_snapshot=overnight_us_snapshot,
            )
            sector_tide_context["industry_money_flow"] = sector_tide_flow_rows
            strategy_context = sector_tide_context
            market = sector_tide_context.get("market") or {}
            dragon_tiger = sector_tide_context.get("dragon_tiger") or {}
            overnight_us = sector_tide_context.get("overnight_us") or {}
            print(
                "  Tide context: "
                f"market={market.get('state')} score={market.get('score')} "
                f"sectors={sector_tide_context.get('sector_count')} "
                f"coverage={sector_tide_context.get('data_coverage')} "
                f"dragon_tiger={dragon_tiger.get('as_of_date') or 'unavailable'} "
                f"matched={dragon_tiger.get('matched_stock_count', 0)} "
                f"overnight_us={overnight_us.get('target_us_date') or 'unavailable'} "
                f"tone={overnight_us.get('tone', 'neutral')}",
                file=sys.stderr,
            )
        else:
            previous_niuone_context = load_previous_niuone_context()
            niuone_context = build_niuone_context(
                prepared_items,
                reference_pool_count=len(reference_candidates),
                market_snapshot=market_snapshot,
                flow_rows=sector_tide_flow_rows,
                previous_context=previous_niuone_context,
                dragon_tiger_snapshot=dragon_tiger_snapshot,
                as_of_date=niuone_as_of_date,
                previous_trading_day=niuone_previous_trading_day,
                sample_at=str(market_snapshot.get("captured_at") or ""),
                theme_basis="eastmoney_concept",
            )
            niuone_context["industry_money_flow"] = sector_tide_flow_rows
            niuone_context["reference_stock_universe"] = list(reference_stock_universe)
            niuone_context["reference_stock_universe_label"] = friendly_stock_universe(reference_stock_universe)
            niuone_context["reference_pool_count"] = len(reference_candidates)
            niuone_context["reference_prefilter_count"] = len(context_candidates)
            niuone_context["reference_analysis_count"] = len(context_candidates)
            strategy_context = niuone_context
            market = niuone_context.get("market") or {}
            mainline = niuone_context.get("mainline") or {}
            print(
                "  牛牛主线 context: "
                f"market={market.get('state')} score={market.get('score')} "
                f"mode={mainline.get('mode')} primary={mainline.get('primary') or 'none'} "
                f"intraday={mainline.get('intraday_primary') or 'none'} "
                f"as_of={niuone_as_of_date or 'unknown'} "
                f"themes={niuone_context.get('theme_count')} "
                f"strong_stocks={niuone_context.get('strong_stock_count')} "
                f"coverage={niuone_context.get('data_coverage')}",
                file=sys.stderr,
            )

    if niuone_mainline_only:
        if niuone_context is None:
            raise RuntimeError("independent NiuOne mainline context was not generated")
        generated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        output = {
            "generated_at": generated_at,
            "reference_stock_universe": list(reference_stock_universe),
            "reference_stock_universe_label": friendly_stock_universe(reference_stock_universe),
            "reference_pool_count": len(reference_candidates),
            "reference_prefilter_count": len(context_candidates),
            "reference_analysis_count": len(context_candidates),
            "niuone_context": niuone_context,
        }
        write_niuone_mainline_cache(NIUONE_MAINLINE_CACHE, output)
        write_niuone_mainline_summary_cache(
            NIUONE_MAINLINE_SUMMARY_CACHE,
            output,
        )
        json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        print(
            "  Independent theme-strength cache updated: "
            f"generated_at={generated_at} themes={niuone_context.get('theme_count')} "
            "theme_source=eastmoney_concept_and_market_resonance",
            file=sys.stderr,
        )
        return

    def analyze_candidate(candidate):
        code, name, q = candidate
        tencent_key = tencent_keys[code]
        if (
            strict_kline_cache
            and code not in prepared_by_code
            and tencent_key not in cached_klines_by_symbol
        ):
            return None
        try:
            multi = analyze_all_strategies(
                code,
                tencent_key,
                quote=q,
                name=name,
                industry=industry_by_code.get(code, ""),
                rows=prepared_by_code.get(code),
                historical_rows=cached_klines_by_symbol.get(tencent_key),
                fetched_callback=remember_fetched_klines,
                context=strategy_context,
                scorers=scorers,
                kline_count=(
                    prompt_selection_kline_count
                    if prompt_strategy_version is not None
                    else DEFAULT_KLINE_COUNT
                ),
                enrich_legacy_indicators=not prompt_only_runtime,
                minimum_rows=(
                    prompt_selection_minimum_bars
                    if prompt_strategy_version is not None
                    else 30
                ),
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
        best_strategy = str(multi["best_strategy"] or "")
        best = multi["strategies"].get(best_strategy, {})
        niuone_best = best_strategy in NIUONE_STRATEGY_IDS
        factual_industry = normalize_industry_name(
            best.get("classification_industry")
            or industry_by_code.get(code, "")
        )
        signal_theme = (
            normalize_industry_name(
                best.get("signal_theme") or best.get("industry")
            )
            if niuone_best
            else ""
        )
        candidate_industry = (
            factual_industry
            if niuone_best
            else normalize_industry_name(
                best.get("industry") or factual_industry
            )
        )
        return {
            "code": code,
            "name": name,
            **stock_universe_metadata(code, name),
            "price": q.get("price"),
            "change_pct": q.get("change_pct"),
            "amount": q.get("amount"),
            "amount_yi": round(q.get("amount", 0) / 1e8, 1) if q.get("amount") else None,
            "turnover": q.get("turnover"),
            "industry": candidate_industry,
            "sector": candidate_industry,
            "signal_theme": signal_theme,
            "theme_memberships": list(best.get("theme_memberships") or []),
            "theme_attributions": list(best.get("theme_attributions") or []),
            "signal_theme_attribution_score": best.get(
                "signal_theme_attribution_score"
            ),
            "signal_theme_attribution_weight": best.get(
                "signal_theme_attribution_weight"
            ),
            "signal_theme_historical_prior_score": best.get(
                "signal_theme_historical_prior_score"
            ),
            "signal_theme_cohort_alignment_score": best.get(
                "signal_theme_cohort_alignment_score"
            ),
            "signal_theme_peer_resonance_score": best.get(
                "signal_theme_peer_resonance_score"
            ),
            "signal_theme_return_correlation_score": best.get(
                "signal_theme_return_correlation_score"
            ),
            "signal_theme_return_correlation_rank_score": best.get(
                "signal_theme_return_correlation_rank_score"
            ),
            "signal_theme_return_correlation_observation_count": best.get(
                "signal_theme_return_correlation_observation_count"
            ),
            "signal_theme_return_correlation_peer_count": best.get(
                "signal_theme_return_correlation_peer_count"
            ),
            "signal_theme_specificity_score": best.get(
                "signal_theme_specificity_score"
            ),
            "signal_theme_membership_source": best.get(
                "signal_theme_membership_source"
            ),
            "unattributed_theme_weight": best.get(
                "unattributed_theme_weight"
            ),
            "theme_attribution_confident": best.get(
                "theme_attribution_confident"
            ),
            "theme_attribution_gap": best.get("theme_attribution_gap"),
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
            "return_5d_pct": best.get("return_5d_pct"),
            "return_20d_pct": best.get("return_20d_pct"),
            "distance_ema20_pct": best.get("distance_ema20_pct"),
            "distance_bbi_pct": best.get("distance_bbi_pct"),
            "distance_high_20d_pct": best.get("distance_high_20d_pct"),
            "volume_ratio_5d": best.get("volume_ratio_5d"),
            "volatility_20d_pct": best.get("volatility_20d_pct"),
            "above_ema20": best.get("above_ema20"),
            "change_pct": q.get("change_pct"),
            # multi-strategy fields
            "best_strategy": best_strategy,
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
            "prompt_strategy_version_id": best.get("prompt_strategy_version_id"),
            "prompt_plan_sha256": best.get("prompt_plan_sha256"),
            "prompt_rule_status": best.get("prompt_rule_status"),
            "prompt_rule_evaluation": best.get("prompt_rule_evaluation"),
            "prompt_rule_audit": best.get("prompt_rule_audit"),
            "prompt_feature_metadata": best.get("prompt_feature_metadata"),
            "prompt_feature_errors": best.get("prompt_feature_errors"),
            "prompt_facts": best.get("prompt_facts"),
            "market_regime": best.get("market_regime"),
            "market_score": best.get("market_score"),
            "market_hard_stop": best.get("market_hard_stop"),
            "market_allows_buys": best.get("market_allows_buys"),
            "sector_status": best.get("sector_status"),
            "sector_score": best.get("sector_score"),
            "theme_basis": best.get("theme_basis"),
            "mainline_state": best.get("mainline_state"),
            **niuone_lifecycle_candidate_metadata(best),
            "mainline_raw_state": best.get("mainline_raw_state"),
            "mainline_intraday_state": best.get("mainline_intraday_state"),
            "mainline_score": best.get("mainline_score"),
            "mainline_mode": best.get("mainline_mode"),
            "mainline_primary": best.get("mainline_primary"),
            "mainline_secondary": best.get("mainline_secondary"),
            "mainline_selected": best.get("mainline_selected"),
            "mainline_confirmation_count": best.get("mainline_confirmation_count"),
            "mainline_intraday_confirmation_count": best.get("mainline_intraday_confirmation_count"),
            "mainline_cross_day_persistent": best.get("mainline_cross_day_persistent"),
            "mainline_cross_day_confirmed": best.get("mainline_cross_day_confirmed"),
            "mainline_confirmed": best.get("mainline_confirmed"),
            "mainline_core_overlap_count": best.get("mainline_core_overlap_count"),
            "mainline_core_overlap_ratio": best.get("mainline_core_overlap_ratio"),
            "mainline_continued_core_codes": best.get("mainline_continued_core_codes"),
            "mainline_as_of_date": best.get("mainline_as_of_date"),
            "mainline_previous_as_of_date": best.get("mainline_previous_as_of_date"),
            "mainline_state_streak": best.get("mainline_state_streak"),
            "mainline_score_change": best.get("mainline_score_change"),
            "today_eligible_data": best.get("today_eligible_data"),
            "today_up_count": best.get("today_up_count"),
            "today_1_5pct_count": best.get("today_1_5pct_count"),
            "today_breadth_pct": best.get("today_breadth_pct"),
            "today_median_change_pct": best.get("today_median_change_pct"),
            "today_strength_score": best.get("today_strength_score"),
            "today_leadership_score": best.get("today_leadership_score"),
            "reversal_basis": best.get("reversal_basis"),
            "daily_v_reversal": best.get("daily_v_reversal"),
            "daily_v_left_peak_date": best.get("daily_v_left_peak_date"),
            "daily_v_trough_date": best.get("daily_v_trough_date"),
            "daily_v_left_days": best.get("daily_v_left_days"),
            "daily_v_right_days": best.get("daily_v_right_days"),
            "daily_v_decline_pct": best.get("daily_v_decline_pct"),
            "daily_v_rebound_pct": best.get("daily_v_rebound_pct"),
            "daily_v_recovery_ratio": best.get("daily_v_recovery_ratio"),
            "daily_v_rising_ratio": best.get("daily_v_rising_ratio"),
            "daily_v_right_trend_confirmed": best.get("daily_v_right_trend_confirmed"),
            "daily_v_pattern_score": best.get("daily_v_pattern_score"),
            "strong_stock_count": best.get("strong_stock_count"),
            "effective_strong_count": best.get("effective_strong_count"),
            "leader_concentration": best.get("leader_concentration"),
            "single_stock_dominated": best.get("single_stock_dominated"),
            "stock_role": best.get("stock_role"),
            "stock_leader_rank": best.get("stock_leader_rank"),
            "stock_leader_tier": best.get("stock_leader_tier"),
            "stock_strong": best.get("stock_strong"),
            "stock_strong_score": best.get("stock_strong_score"),
            "stock_activity_gate_required": best.get(
                "stock_activity_gate_required"
            ),
            "stock_activity_data_available": best.get(
                "stock_activity_data_available"
            ),
            "stock_market_amount_percentile": best.get(
                "stock_market_amount_percentile"
            ),
            "stock_theme_amount_percentile": best.get(
                "stock_theme_amount_percentile"
            ),
            "stock_volume_participation_percentile": best.get(
                "stock_volume_participation_percentile"
            ),
            "stock_activity_score": best.get("stock_activity_score"),
            "stock_activity_confirmed": best.get(
                "stock_activity_confirmed"
            ),
            "stock_reversal_leader_rank": best.get("stock_reversal_leader_rank"),
            "stock_reversal_leader_tier": best.get("stock_reversal_leader_tier"),
            "stock_reversal_strong": best.get("stock_reversal_strong"),
            "stock_today_rank_score": best.get("stock_today_rank_score"),
            "sector_rank_acceleration": best.get("sector_rank_acceleration"),
            "sector_breadth20": best.get("sector_breadth20"),
            "stock_sector_rank": best.get("stock_sector_rank"),
            "stock_market_rank": best.get("stock_market_rank"),
            "score_before_industry_flow": best.get("score_before_industry_flow"),
            "industry_flow_available": best.get("industry_flow_available"),
            "industry_flow_matched": best.get("industry_flow_matched"),
            "industry_flow_direction": best.get("industry_flow_direction"),
            "industry_flow_rank": best.get("industry_flow_rank"),
            "industry_flow_rank_total": best.get("industry_flow_rank_total"),
            "industry_flow_net_yi": best.get("industry_flow_net_yi"),
            "industry_outflow_matched": best.get("industry_outflow_matched"),
            "industry_outflow_rank": best.get("industry_outflow_rank"),
            "industry_outflow_rank_total": best.get("industry_outflow_rank_total"),
            "industry_outflow_net_yi": best.get("industry_outflow_net_yi"),
            "industry_flow_adjustment": best.get("industry_flow_adjustment"),
            "industry_flow_source": best.get("industry_flow_source"),
            "industry_flow_generated_at": best.get("industry_flow_generated_at"),
            "score_before_external_context": best.get("score_before_external_context"),
            "raw_external_context_adjustment": best.get("raw_external_context_adjustment"),
            "external_context_adjustment": best.get("external_context_adjustment"),
            "external_context_capped": best.get("external_context_capped"),
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
            "overnight_us_available": best.get("overnight_us_available"),
            "overnight_us_target_date": best.get("overnight_us_target_date"),
            "overnight_us_tone": best.get("overnight_us_tone"),
            "overnight_us_tone_label": best.get("overnight_us_tone_label"),
            "overnight_us_summary": best.get("overnight_us_summary"),
            "overnight_us_sector_matched": best.get("overnight_us_sector_matched"),
            "overnight_us_sector": best.get("overnight_us_sector"),
            "overnight_us_proxy": best.get("overnight_us_proxy"),
            "overnight_us_change_pct": best.get("overnight_us_change_pct"),
            "overnight_us_signal": best.get("overnight_us_signal"),
            "overnight_us_adjustment": best.get("overnight_us_adjustment"),
            "overnight_us_positive_suppressed": best.get("overnight_us_positive_suppressed"),
            "news_precheck_configured": best.get("news_precheck_configured"),
            "news_precheck": best.get("news_precheck"),
            "news_checked": best.get("news_checked"),
            "news_available": best.get("news_available"),
            "news_tone": best.get("news_tone"),
            "news_tone_label": best.get("news_tone_label"),
            "news_summary": best.get("news_summary"),
            "news_fetched_at": best.get("news_fetched_at"),
            "news_adjustment": best.get("news_adjustment"),
            "news_positive_suppressed": best.get("news_positive_suppressed"),
            "ema20": best.get("ema20"),
            "ema50": best.get("ema50"),
            "atr": best.get("atr"),
            "atr_period": best.get("atr_period"),
            "atr20": best.get("atr20"),
            "stop_price": best.get("stop_price"),
            "stop_source": best.get("stop_source"),
            "stop_distance_pct": best.get("stop_distance_pct"),
            "stop_atr": best.get("stop_atr"),
            "max_stop_distance_pct": best.get("max_stop_distance_pct"),
            "max_stop_atr": best.get("max_stop_atr"),
            "max_entry_change_pct": best.get("max_entry_change_pct"),
            "max_entry_extension_atr": best.get("max_entry_extension_atr"),
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
    report_scan_progress(
        "scoring",
        stage_label="正在执行本地策略评分",
        completed=0,
        total=len(to_analyze),
        worker_count=scan_workers,
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=scan_workers) as pool:
        for completed, item in enumerate(pool.map(analyze_candidate, to_analyze), 1):
            if item is not None:
                results.append(item)
            if completed % 50 == 0:
                report_scan_progress(
                    "scoring",
                    stage_label="正在执行本地策略评分",
                    completed=completed,
                    total=len(to_analyze),
                    worker_count=scan_workers,
                )
                print(f"  ... {completed}/{len(to_analyze)} analyzed", file=sys.stderr)
    flush_fetched_klines()

    # Sort: best_score desc, above_bbi bonus, closer to BBI better
    def sort_key(item):
        s = item.get("best_decision_score") or item["best_score"]
        above = 1 if item.get("above_bbi") else 0
        dist = abs(item.get("distance_pct") or 99)
        return (s, above, -dist)

    results.sort(key=sort_key, reverse=True)
    if prompt_strategy_version is not None and prompt_strategy_store is not None:
        prompt_audits = [
            item["prompt_rule_audit"]
            for item in results
            if isinstance(item.get("prompt_rule_audit"), dict)
        ]
        if prompt_audits:
            prompt_strategy_store.record_evaluations_batch(
                str(prompt_strategy_version.get("version_id") or ""),
                prompt_audits,
            )
    if sector_tide_enabled and sector_tide_context is not None:
        report_scan_progress("news_precheck", stage_label="正在检查候选股消息面", total=SECTOR_TIDE_NEWS_PRECHECK_LIMIT)
        news_shortlist = [
            item
            for item in results
            if str(item.get("best_strategy") or "") in SECTOR_TIDE_STRATEGY_IDS
        ][:SECTOR_TIDE_NEWS_PRECHECK_LIMIT]
        news_snapshot = fetch_sector_tide_news_precheck(news_shortlist)
        sector_tide_context = build_sector_tide_context(
            prepared_items,
            market_snapshot=market_snapshot,
            flow_rows=sector_tide_flow_rows,
            previous_market=previous_sector_tide_market,
            dragon_tiger_snapshot=dragon_tiger_snapshot,
            overnight_us_snapshot=overnight_us_snapshot,
            news_snapshot=news_snapshot,
        )
        sector_tide_context["industry_money_flow"] = sector_tide_flow_rows
        strategy_context = sector_tide_context
        record_codes = {
            normalize_stock_code(record.get("code"))
            for record in news_snapshot.get("records") or []
            if isinstance(record, dict) and normalize_stock_code(record.get("code"))
        }
        source_by_code = {str(candidate[0]): candidate for candidate in to_analyze}
        refreshed_by_code: dict[str, dict[str, Any]] = {}
        for code in record_codes:
            source = source_by_code.get(code)
            refreshed = analyze_candidate(source) if source else None
            if refreshed is not None:
                refreshed_by_code[code] = refreshed
        if refreshed_by_code:
            results = [
                refreshed_by_code.get(str(item.get("code") or ""), item)
                for item in results
            ]
            results.sort(key=sort_key, reverse=True)
        news_meta = sector_tide_context.get("news") or {}
        print(
            "  Tide news precheck: "
            f"configured={news_meta.get('configured')} "
            f"checked={news_meta.get('matched_stock_count', 0)} "
            f"available={news_meta.get('available_stock_count', 0)}",
            file=sys.stderr,
        )
    display_candidates = select_display_candidates(results)
    trade_candidates = select_trade_candidates(
        results,
        limit=(
            int(
                ((prompt_strategy_version.get("execution_plan") or {}).get("strategy") or {}).get(
                    "candidate_limit",
                    preset_text_candidate_limit(),
                )
            )
            if prompt_strategy_version is not None
            else preset_text_candidate_limit() if preset_text_enabled else None
        ),
    )
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
        "strategy_suite": active_strategy_suite(
            active_strategy_setting(),
            strategy_source_setting(),
            enabled_persona_strategy_setting(),
        ),
        "enabled_strategy_ids": sorted(scorers),
        "configured_stock_universe": list(configured_universe),
        "configured_stock_universe_label": friendly_stock_universe(configured_universe),
        "stock_universe": list(stock_universe),
        "stock_universe_label": friendly_stock_universe(stock_universe),
        "reference_stock_universe": list(reference_stock_universe),
        "reference_stock_universe_label": friendly_stock_universe(reference_stock_universe),
        "reference_pool_count": len(reference_candidates),
        "reference_prefilter_count": len(context_candidates),
        "reference_analysis_count": len(context_candidates),
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
    if prompt_strategy_version is not None:
        output["prompt_strategy"] = {
            "version_id": prompt_strategy_version.get("version_id"),
            "revision": prompt_strategy_version.get("revision"),
            "plan_sha256": prompt_strategy_version.get("plan_sha256"),
            "engine_version": prompt_strategy_version.get("engine_version"),
        }
    if sector_tide_context is not None:
        output["sector_tide_context"] = sector_tide_context
    if niuone_context is not None:
        output["niuone_context"] = niuone_context
    if zettaranc_enabled:
        output["zettaranc_context"] = {
            "industry_money_flow": sector_tide_flow_rows,
        }
    report_scan_progress("persisting", stage_label="正在保存本轮候选结果")
    if niuone_context is not None:
        write_niuone_mainline_cache(NIUONE_MAINLINE_CACHE, output)
        write_niuone_mainline_summary_cache(
            NIUONE_MAINLINE_SUMMARY_CACHE,
            output,
        )
    write_outputs(output, generated_at)
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    report_scan_progress(
        "completed",
        stage_label="选股扫描已完成",
        completed=len(results),
        total=len(to_analyze),
        worker_count=scan_workers,
    )


if __name__ == "__main__":
    main()
