#!/usr/bin/env python3
"""NiuOne dashboard for messages, models, and trading signals."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import ipaddress
import json
import os
import re
import secrets
import shlex
import sqlite3
import time
import subprocess
import sys
import threading
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from http import cookies
import urllib.request

from a_share_calendar import is_a_share_trading_day as calendar_is_a_share_trading_day, trading_day_status
from niuone_paths import get_dashboard_env_file, get_dashboard_home, get_local_data_dir
import push_history
from strategy_registry import (
    PERSONA_STRATEGY_ENV,
    PRESET_STRATEGY_TEXT_ENV,
    PRESET_STRATEGY_TEXT_MAX_CHARS,
    TRADE_DISCIPLINE_TEXT_ENV,
    TRADE_DISCIPLINE_TEXT_MAX_CHARS,
    STRATEGY_SOURCE_BUILTIN,
    STRATEGY_SOURCE_ENV,
    STRATEGY_SOURCE_OPTIONS,
    decode_preset_strategy_text,
    decode_trade_discipline_text,
    default_trade_discipline_text,
    default_enabled_persona_strategies_value,
    normalize_preset_strategy_text_update,
    normalize_trade_discipline_text_update,
    normalize_strategy_source_update,
    normalize_strategy_list_update,
    strategy_settings_options,
)
from us_market_summary import fetch_us_market_summary, fetch_us_sector_snapshot, load_cached_summary_for_today

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
LOCAL_DATA_DIR = get_local_data_dir(PROJECT_ROOT)
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
CONFIG_PATH = Path(os.environ.get("DASHBOARD_CONFIG") or str(DASHBOARD_HOME / "config.yaml")).expanduser()
DASHBOARD_ENV_FILE = get_dashboard_env_file(PROJECT_ROOT)
CRON_OUTPUT_DIR = DASHBOARD_HOME / "cron" / "output"
CRON_STATE_DIR = DASHBOARD_HOME / "cron" / "state"
B1_CACHE_FILE = CRON_OUTPUT_DIR / "b1_screen_latest.json"
STATS_DB = DASHBOARD_HOME / "dashboard_stats.db"
LEGACY_STATS_DB = DASHBOARD_HOME / "dashboard_users.db"
LEGACY_STATS_MIGRATION_KEY = "dashboard_users_visit_stats_v1"
VISITOR_COOKIE_NAME = "niuone_visitor_id"
ACTION_HEADER_NAME = "X-NiuOne-Action"
ACTION_HEADER_VALUES = {"1", "true", "yes", "on"}
TRUTHY_VALUES = {"1", "true", "yes", "on"}
US_FEATURE_CATEGORIES = {"x_monitor", "us_ratings"}
NIUONE_LAUNCHD_LABELS = (
    "ai.niuone.cron-scheduler",
    "ai.niuone.x-watchlist",
    "ai.niuone.dashboard",
)
NIUONE_RESTART_DELAY_SECONDS = float(os.environ.get("NIUONE_RESTART_DELAY_SECONDS", "1.2") or "1.2")
TRUSTED_PROXY_CIDRS = tuple(
    value.strip()
    for value in os.environ.get("DASHBOARD_TRUSTED_PROXIES", "127.0.0.1/32,::1/128").split(",")
    if value.strip()
)
MAX_POST_BODY_BYTES = int(os.environ.get("DASHBOARD_MAX_POST_BODY_BYTES", str(256 * 1024)) or str(256 * 1024))
GZIP_MIN_BYTES = int(os.environ.get("DASHBOARD_GZIP_MIN_BYTES", "1024") or "1024")
GZIP_CONTENT_TYPE_PREFIXES = ("application/json", "text/html", "text/plain")
B1_CACHE_MAX_AGE = 720
B1_SCAN_TIMEOUT_SECONDS = int(os.environ.get("DASHBOARD_B1_SCAN_TIMEOUT_SECONDS", "360") or "360")
B1_SCHEDULE_TIMES = tuple(
    value.strip()
    for value in os.environ.get(
        "DASHBOARD_B1_SCHEDULE_TIMES",
        "09:25,10:00,10:30,11:00,11:20,13:00,13:30,14:00,14:30,14:50",
    ).split(",")
    if value.strip()
)
B1_SCHEDULE_ENABLED = os.environ.get("DASHBOARD_B1_SCHEDULE_ENABLED", "1").lower() not in {"0", "false", "no"}
B1_SCHEDULE_STATE_FILE = CRON_STATE_DIR / "b1_schedule_state.json"
B1_SCHEDULE_CATCHUP_MINUTES = int(os.environ.get("DASHBOARD_B1_SCHEDULE_CATCHUP_MINUTES", "35") or "35")
B1_SCHEDULE_STALE_SECONDS = int(os.environ.get("DASHBOARD_B1_SCHEDULE_STALE_SECONDS", "900") or "900")
B1_SCHEDULE_RUN_KEYS: set[str] = set()
B1_SCHEDULE_LOCK = threading.RLock()
B1_SCHEDULE_THREAD: threading.Thread | None = None
PENDING_DECISION_THREAD: threading.Thread | None = None
PENDING_DECISION_POLL_SECONDS = float(os.environ.get("DASHBOARD_PENDING_DECISION_POLL_SECONDS", "5") or "5")
B1_CANDIDATE_REFRESH_LOCK = threading.Lock()
B1_CANDIDATE_REFRESH_MIN_SECONDS = float(os.environ.get("DASHBOARD_B1_CANDIDATE_REFRESH_MIN_SECONDS", "0") or "0")
B1_CANDIDATE_REFRESH_LAST_TS = 0.0
MULTI_STRATEGY_CACHE_FILE = CRON_OUTPUT_DIR / "multi_strategy_latest.json"
TRADER_SCRIPT = Path(os.environ.get("DASHBOARD_TRADER_SCRIPT", SCRIPT_DIR / "niuniu_practice_trader.py")).expanduser()
TRADER_MODULE = None
TRADER_MODULE_MTIME = 0.0
PRACTICE_DECISION_KEYS: set[str] = set()
BENCHMARK_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
BENCHMARK_TTL_SECONDS = 20
CN_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

# Public dashboard concurrency protection: cache expensive JSON payloads in-process
# so 1000 viewers do not trigger 1000 identical DB/行情/akshare computations.
API_RESPONSE_CACHE: dict[str, dict[str, Any]] = {}
API_RESPONSE_LOCK = threading.RLock()
API_CACHE_KEY_LOCKS: dict[str, threading.Lock] = {}
API_CACHE_MAX_ENTRIES = int(os.environ.get("DASHBOARD_API_CACHE_MAX_ENTRIES", "256") or "256")
X_MEDIA_CACHE: dict[str, dict[str, Any]] = {}
X_MEDIA_CACHE_LOCK = threading.RLock()
X_MEDIA_CACHE_MAX_ENTRIES = int(os.environ.get("DASHBOARD_X_MEDIA_CACHE_MAX_ENTRIES", "96") or "96")
X_MEDIA_CACHE_TTL_SECONDS = int(os.environ.get("DASHBOARD_X_MEDIA_CACHE_TTL_SECONDS", str(7 * 24 * 3600)) or str(7 * 24 * 3600))
X_MEDIA_MAX_BYTES = int(os.environ.get("DASHBOARD_X_MEDIA_MAX_BYTES", str(8 * 1024 * 1024)) or str(8 * 1024 * 1024))
X_MEDIA_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}
EDGE_CACHE_ENABLED = os.environ.get("DASHBOARD_EDGE_CACHE_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
API_DEFAULT_LIMIT = 80
API_LIMIT_MAX = 200
API_OFFSET_MAX = int(os.environ.get("DASHBOARD_API_OFFSET_MAX", "5000") or "5000")
RATE_LIMIT_ENABLED = os.environ.get("DASHBOARD_RATE_LIMIT_ENABLED", "1").lower() not in {"0", "false", "no"}
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("DASHBOARD_RATE_LIMIT_WINDOW_SECONDS", "60") or "60")
RATE_LIMIT_ANON = int(os.environ.get("DASHBOARD_RATE_LIMIT_ANON", "240") or "240")
RATE_LIMIT_API = int(os.environ.get("DASHBOARD_RATE_LIMIT_API", "900") or "900")
RATE_LIMIT_ADMIN = int(os.environ.get("DASHBOARD_RATE_LIMIT_ADMIN", "90") or "90")
RATE_LIMIT_BUCKETS: dict[tuple[str, str], tuple[float, int]] = {}
RATE_LIMIT_LOCK = threading.Lock()
VISIT_STATS_LOCK = threading.RLock()
API_TTLS = {
    "messages": 10,
    "b1_screen": int(os.environ.get("DASHBOARD_B1_SCREEN_TTL_SECONDS", "15") or "15"),
    "niuniu_practice": int(os.environ.get("DASHBOARD_PRACTICE_TTL_SECONDS", "15") or "15"),
    "practice_benchmarks": 30,
    "indices": int(os.environ.get("DASHBOARD_INDICES_TTL_SECONDS", "60") or "60"),
    "sectors": 60,
    "us_sectors": int(os.environ.get("DASHBOARD_US_SECTORS_TTL_SECONDS", "300") or "300"),
    "hot_stocks": 60,
    "money_flow": 60,
    "market_flow": 30,
    "us_quotes": 30,
    "us_market_summary": int(os.environ.get("DASHBOARD_US_MARKET_SUMMARY_TTL_SECONDS", "300") or "300"),
}

SECRET_PLACEHOLDER = "__KEEP_SECRET__"
SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|credential|(?:^|[_-])token(?:$|[_-]))",
    re.I,
)
DEFAULT_MODEL_CONTEXT_LENGTH = "128000"
DEFAULT_MODEL_MAX_TOKENS = "4096"

ENV_CONFIG_SCHEMA: list[dict[str, str]] = [
    {"name": "DASHBOARD_HOME", "label": "运行数据目录", "group": "基础路径", "kind": "path", "default": str(LOCAL_DATA_DIR / "runtime"), "effect": "restart"},
    {"name": "DASHBOARD_HOST", "label": "监听地址", "group": "基础路径", "kind": "text", "default": "127.0.0.1", "effect": "restart"},
    {"name": "DASHBOARD_PORT", "label": "监听端口", "group": "基础路径", "kind": "int", "default": "8787", "effect": "restart"},
    {"name": "PYTHON_BIN", "label": "Python 可执行文件", "group": "基础路径", "kind": "path", "default": "", "effect": "restart"},
    {"name": "DASHBOARD_CONFIG", "label": "模型配置 YAML", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "config.yaml"), "effect": "restart"},
    {"name": "DASHBOARD_PUSH_HISTORY_DB", "label": "消息历史 DB", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "push_history.db"), "effect": "restart"},
    {"name": "DASHBOARD_PORTFOLIO_STATE", "label": "模拟账户状态文件", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "cron" / "output" / "niuniu_practice_portfolio.json"), "effect": "restart"},
    {"name": "DASHBOARD_NIUNIU_DB", "label": "牛牛实战 DB", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "niuniu.db"), "effect": "restart"},
    {"name": "DASHBOARD_TRADER_SCRIPT", "label": "牛牛实战脚本", "group": "基础路径", "kind": "path", "default": str(SCRIPT_DIR / "niuniu_practice_trader.py"), "effect": "restart"},
    {"name": "DASHBOARD_B1_SCANNER", "label": "B1 扫描脚本", "group": "基础路径", "kind": "path", "default": str(SCRIPT_DIR / "multi_strategy_screen.py"), "effect": "restart"},
    {"name": "DASHBOARD_CN_STOCK_TOOLS", "label": "A股行情工具脚本", "group": "基础路径", "kind": "path", "default": str(SCRIPT_DIR / "cn_stock_tools.py"), "effect": "restart"},
    {"name": "DASHBOARD_US_RATING_OUTPUT_DIR", "label": "美股评级归档目录", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "cron" / "output" / "fd0b807138f4"), "effect": "next_run"},
    {"name": "DASHBOARD_CRON_JOBS", "label": "Cron jobs JSON", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "cron" / "jobs.json"), "effect": "next_run"},
    {"name": "DASHBOARD_X_WATCHLIST_STATE", "label": "X 监控状态文件", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "cron" / "state" / "x_watchlist_latest.json"), "effect": "next_run"},
    {"name": "DASHBOARD_X_WATCHLIST_ARCHIVE_DIR", "label": "X 监控归档目录", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "cron" / "output" / "x_watchlist_direct"), "effect": "next_run"},

    {"name": "DASHBOARD_EDGE_CACHE_ENABLED", "label": "允许 CDN 缓存 API", "group": "访问控制", "kind": "bool", "default": "0", "effect": "restart"},
    {"name": "DASHBOARD_MAX_POST_BODY_BYTES", "label": "POST 表单最大字节", "group": "访问控制", "kind": "int", "default": str(256 * 1024), "effect": "restart"},

    {"name": "DASHBOARD_RATE_LIMIT_ENABLED", "label": "启用限流", "group": "限流与缓存", "kind": "bool", "default": "1", "effect": "restart"},
    {"name": "DASHBOARD_RATE_LIMIT_WINDOW_SECONDS", "label": "限流窗口秒数", "group": "限流与缓存", "kind": "int", "default": "60", "effect": "restart"},
    {"name": "DASHBOARD_RATE_LIMIT_ANON", "label": "公开请求/窗口", "group": "限流与缓存", "kind": "int", "default": "240", "effect": "restart"},
    {"name": "DASHBOARD_RATE_LIMIT_API", "label": "API 请求/窗口", "group": "限流与缓存", "kind": "int", "default": "900", "effect": "restart"},
    {"name": "DASHBOARD_RATE_LIMIT_ADMIN", "label": "管理操作/窗口", "group": "限流与缓存", "kind": "int", "default": "90", "effect": "restart"},
    {"name": "DASHBOARD_API_CACHE_MAX_ENTRIES", "label": "API 缓存条目上限", "group": "限流与缓存", "kind": "int", "default": "256", "effect": "restart"},
    {"name": "DASHBOARD_API_OFFSET_MAX", "label": "消息分页最大 offset", "group": "限流与缓存", "kind": "int", "default": "5000", "effect": "restart"},
    {"name": "DASHBOARD_X_MEDIA_CACHE_MAX_ENTRIES", "label": "X 图片缓存条目上限", "group": "限流与缓存", "kind": "int", "default": "96", "effect": "restart"},
    {"name": "DASHBOARD_X_MEDIA_CACHE_TTL_SECONDS", "label": "X 图片缓存 TTL 秒数", "group": "限流与缓存", "kind": "int", "default": str(7 * 24 * 3600), "effect": "restart"},
    {"name": "DASHBOARD_X_MEDIA_MAX_BYTES", "label": "X 图片代理最大字节", "group": "限流与缓存", "kind": "int", "default": str(8 * 1024 * 1024), "effect": "restart"},

    {"name": "DASHBOARD_B1_SCHEDULE_ENABLED", "label": "启用 B1 定时扫描", "group": "任务调度", "kind": "bool", "default": "1", "effect": "restart"},
    {"name": "DASHBOARD_B1_SCHEDULE_TIMES", "label": "选股及买卖决策时间点", "group": "选股及买卖决策时间点", "kind": "time_list", "default": "09:25,10:00,10:30,11:00,11:20,13:00,13:30,14:00,14:30,14:50", "effect": "runtime"},
    {"name": "DASHBOARD_B3_EXIT_TIME", "label": "B3开盘离场检查时间", "group": "选股及买卖决策时间点", "kind": "time", "default": "09:30", "effect": "runtime"},
    {"name": "DASHBOARD_TIME_EXIT_TIME", "label": "尾盘离场检查时间", "group": "选股及买卖决策时间点", "kind": "time", "default": "14:45", "effect": "runtime"},
    {"name": STRATEGY_SOURCE_ENV, "label": "当前策略来源", "group": "选股策略", "kind": "strategy_source", "default": "builtin", "effect": "runtime"},
    {"name": PERSONA_STRATEGY_ENV, "label": "内置策略", "group": "选股策略", "kind": "strategy_single", "default": default_enabled_persona_strategies_value(), "effect": "runtime"},
    {"name": PRESET_STRATEGY_TEXT_ENV, "label": "预设文字策略", "group": "选股策略", "kind": "preset_strategy_text", "default": "", "effect": "runtime"},
    {"name": "DASHBOARD_B1_SCAN_TIMEOUT_SECONDS", "label": "B1 扫描超时秒数", "group": "任务调度", "kind": "int", "default": "360", "effect": "restart"},
    {"name": "DASHBOARD_B1_SCHEDULE_CATCHUP_MINUTES", "label": "B1 漏触发补跑窗口分钟", "group": "任务调度", "kind": "int", "default": "35", "effect": "restart"},
    {"name": "DASHBOARD_B1_SCHEDULE_STALE_SECONDS", "label": "B1 运行中陈旧秒数", "group": "任务调度", "kind": "int", "default": "900", "effect": "restart"},
    {"name": "DASHBOARD_CRON_MAX_ATTEMPTS", "label": "Cron 失败最大运行次数", "group": "任务调度", "kind": "int", "default": "2", "effect": "next_run"},
    {"name": "DASHBOARD_CRON_RETRY_DELAY_SECONDS", "label": "Cron 失败重试间隔秒数", "group": "任务调度", "kind": "int", "default": "300", "effect": "next_run"},
    {"name": "DASHBOARD_PENDING_DECISION_POLL_SECONDS", "label": "延迟成交检查秒数", "group": "任务调度", "kind": "int", "default": "5", "effect": "restart"},

    {"name": "DASHBOARD_DECISION_MAX_TOKENS", "label": "决策最大输出长度", "group": "买卖决策模型", "kind": "max_tokens", "default": DEFAULT_MODEL_MAX_TOKENS, "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_TIMEOUT", "label": "决策请求超时", "group": "买卖决策模型", "kind": "int", "default": "180", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_INTELLIGENCE_ENABLED", "label": "启用全局情报包", "group": "买卖决策模型", "kind": "bool", "default": "1", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS", "label": "情报包缓存秒数", "group": "买卖决策模型", "kind": "int", "default": "75", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS", "label": "情报榜单条数", "group": "买卖决策模型", "kind": "int", "default": "5", "effect": "next_run"},
    {"name": "DASHBOARD_MARKET_GUIDANCE_ENABLED", "label": "启用盘面指引控仓", "group": "买卖决策模型", "kind": "bool", "default": "1", "effect": "next_run"},
    {"name": TRADE_DISCIPLINE_TEXT_ENV, "label": "交易纪律 Prompt", "group": "买卖决策模型", "kind": "trade_discipline_text", "default": default_trade_discipline_text(), "effect": "runtime"},
    {"name": "DASHBOARD_MAX_OPEN_POSITIONS", "label": "最大持仓只数", "group": "买卖决策模型", "kind": "int", "default": "6", "effect": "next_run"},
    {"name": "DASHBOARD_MAX_NEW_BUYS_PER_DECISION", "label": "单轮最大新买入", "group": "买卖决策模型", "kind": "int", "default": "2", "effect": "next_run"},
    {"name": "DASHBOARD_MAX_SINGLE_POSITION_PCT", "label": "单票仓位参考%", "group": "买卖决策模型", "kind": "text", "default": "10", "effect": "next_run"},
    {"name": "DASHBOARD_MAX_TOTAL_POSITION_PCT", "label": "总仓位参考%", "group": "买卖决策模型", "kind": "text", "default": "80", "effect": "next_run"},
    {"name": "DASHBOARD_MIN_CASH_RESERVE_PCT", "label": "现金缓冲参考%", "group": "买卖决策模型", "kind": "text", "default": "20", "effect": "next_run"},
    {"name": "DASHBOARD_MORNING_MAX_OPEN_POSITIONS", "label": "午盘前持仓上限", "group": "买卖决策模型", "kind": "int", "default": "3", "effect": "next_run"},

    {"name": "DASHBOARD_US_FEATURES_ENABLED", "label": "开启牛牛美股", "group": "牛牛美股", "kind": "bool", "default": "0", "effect": "next_run"},
    {"name": "US_RATING_BASE_URL", "label": "美股评级 API Base URL", "group": "牛牛美股", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "US_RATING_API_KEY", "label": "美股评级 API Key", "group": "牛牛美股", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "US_RATING_CONTEXT_LENGTH", "label": "美股评级上下文长度", "group": "牛牛美股", "kind": "context_length", "default": DEFAULT_MODEL_CONTEXT_LENGTH, "effect": "next_run"},
    {"name": "US_RATING_MAX_TOKENS", "label": "美股评级最大输出长度", "group": "牛牛美股", "kind": "max_tokens", "default": DEFAULT_MODEL_MAX_TOKENS, "effect": "next_run"},
    {"name": "CROSSDESK_BASE_URL", "label": "Crossdesk Base URL", "group": "上游模型覆盖", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "CROSSDESK_API_KEY", "label": "Crossdesk API Key", "group": "上游模型覆盖", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_GROK_MODEL", "label": "Grok 模型", "group": "牛牛美股", "kind": "text", "default": "grok-4.20-multi-agent-xhigh", "effect": "next_run"},
    {"name": "DASHBOARD_GROK_CONTEXT_LENGTH", "label": "Grok 模型上下文长度", "group": "牛牛美股", "kind": "context_length", "default": DEFAULT_MODEL_CONTEXT_LENGTH, "effect": "next_run"},
    {"name": "DASHBOARD_GROK_MAX_TOKENS", "label": "Grok 最大输出长度", "group": "牛牛美股", "kind": "max_tokens", "default": DEFAULT_MODEL_MAX_TOKENS, "effect": "next_run"},
    {"name": "DASHBOARD_GROK_BASE_URL", "label": "Grok API 地址", "group": "牛牛美股", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_GROK_API_KEY", "label": "Grok API 密钥", "group": "牛牛美股", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_NEWS_MODEL", "label": "消息面预检模型", "group": "消息面预检模型", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_NEWS_CONTEXT_LENGTH", "label": "消息面预检上下文长度", "group": "消息面预检模型", "kind": "context_length", "default": DEFAULT_MODEL_CONTEXT_LENGTH, "effect": "next_run"},
    {"name": "DASHBOARD_NEWS_MAX_TOKENS", "label": "消息面预检最大输出长度", "group": "消息面预检模型", "kind": "max_tokens", "default": DEFAULT_MODEL_MAX_TOKENS, "effect": "next_run"},
    {"name": "DASHBOARD_NEWS_BASE_URL", "label": "消息面预检 API 地址", "group": "消息面预检模型", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_NEWS_API_KEY", "label": "消息面预检 API 密钥", "group": "消息面预检模型", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_NEWS_TIMEOUT", "label": "消息面预检请求超时", "group": "消息面预检模型", "kind": "int", "default": "45", "effect": "next_run"},
    {"name": "DASHBOARD_NEWS_MAX_RETRIES", "label": "消息面预检最大请求次数", "group": "消息面预检模型", "kind": "int", "default": "1", "effect": "next_run"},
    {"name": "DASHBOARD_NEWS_CONCURRENCY", "label": "消息面预检并发数", "group": "消息面预检模型", "kind": "int", "default": "5", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_MODEL", "label": "买卖决策模型", "group": "买卖决策模型", "kind": "text", "default": "deepseek-v4-pro", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_CONTEXT_LENGTH", "label": "买卖决策上下文长度", "group": "买卖决策模型", "kind": "context_length", "default": DEFAULT_MODEL_CONTEXT_LENGTH, "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_BASE_URL", "label": "买卖决策 API 地址", "group": "买卖决策模型", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_API_KEY", "label": "买卖决策 API 密钥", "group": "买卖决策模型", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_US_MARKET_SUMMARY_CRON", "label": "隔夜美股盘面总结时间", "group": "盘面监控生产时间点", "kind": "cron_time", "default": "0 8 * * 1-5", "effect": "next_run"},
    {"name": "US_MARKET_SUMMARY_MAX_TOKENS", "label": "隔夜美股总结最大输出长度", "group": "盘面监控生产时间点", "kind": "max_tokens", "default": DEFAULT_MODEL_MAX_TOKENS, "effect": "next_run"},
    {"name": "DASHBOARD_MARKET_AUCTION_CRON", "label": "盘前竞价监控时间", "group": "盘面监控生产时间点", "kind": "cron_time", "default": "25 9 * * 1-5", "effect": "next_run"},
    {"name": "DASHBOARD_MARKET_MIDDAY_CRON", "label": "午盘监控时间", "group": "盘面监控生产时间点", "kind": "cron_time", "default": "40 11 * * 1-5", "effect": "next_run"},
    {"name": "DASHBOARD_MARKET_CLOSE_CRON", "label": "盘后监控时间", "group": "盘面监控生产时间点", "kind": "cron_time", "default": "10 15 * * 1-5", "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_ENABLED", "label": "A股盘面模型总结", "group": "盘面监控生产时间点", "kind": "bool", "default": "1", "effect": "next_run", "bool_no_default": "1"},
    {"name": "A_SHARE_MODEL_SUMMARY_MODEL", "label": "A股盘面总结模型", "group": "盘面监控生产时间点", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_CONTEXT_LENGTH", "label": "A股盘面总结上下文长度", "group": "盘面监控生产时间点", "kind": "context_length", "default": DEFAULT_MODEL_CONTEXT_LENGTH, "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_MAX_TOKENS", "label": "A股盘面总结最大输出长度", "group": "盘面监控生产时间点", "kind": "max_tokens", "default": DEFAULT_MODEL_MAX_TOKENS, "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_BASE_URL", "label": "A股盘面总结 API地址", "group": "盘面监控生产时间点", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_API_KEY", "label": "A股盘面总结 API密钥", "group": "盘面监控生产时间点", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_DEADLINE_SECONDS", "label": "A股模型总结总超时秒数", "group": "盘面监控生产时间点", "kind": "int", "default": "60", "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_REQUEST_TIMEOUT_SECONDS", "label": "A股模型总结单次超时秒数", "group": "盘面监控生产时间点", "kind": "int", "default": "45", "effect": "next_run"},
    {"name": "X_WATCHLIST_ACCOUNTS", "label": "推文监控作者", "group": "牛牛美股", "kind": "handle_list", "default": "", "effect": "next_run"},
    {"name": "X_WATCHLIST_MAX_TOKENS", "label": "X 监控最大输出长度", "group": "牛牛美股", "kind": "max_tokens", "default": DEFAULT_MODEL_MAX_TOKENS, "effect": "next_run"},
    {"name": "X_WATCHLIST_DAEMON_INTERVAL_SECONDS", "label": "推文监控间隔", "group": "牛牛美股", "kind": "int", "default": "1200", "effect": "next_run"},
    {"name": "DASHBOARD_US_RATING_CRON", "label": "美股买入评级时间", "group": "牛牛美股", "kind": "cron_time", "default": "0 11 * * *", "effect": "next_run"},
    {"name": "US_RATING_DEADLINE_SECONDS", "label": "美股评级总超时秒数", "group": "牛牛美股", "kind": "int", "default": "240", "effect": "next_run"},
    {"name": "US_RATING_REQUEST_TIMEOUT_SECONDS", "label": "美股评级单次请求超时秒数", "group": "牛牛美股", "kind": "int", "default": "120", "effect": "next_run"},
    {"name": "DASHBOARD_INDICES_TTL_SECONDS", "label": "指数行情更新间隔", "group": "指数行情更新周期", "kind": "int", "default": "60", "effect": "runtime"},

    {"name": "X_WATCHLIST_STRICT_CONTEXT_HOLD", "label": "X 上下文缺失时暂缓发送", "group": "X 监控", "kind": "bool", "default": "0", "effect": "next_run"},
    {"name": "X_WATCHLIST_DEADLINE_SECONDS", "label": "X 总截止秒数", "group": "X 监控", "kind": "int", "default": "135", "effect": "next_run"},
    {"name": "X_WATCHLIST_SCRIPT_ALARM_SECONDS", "label": "X 脚本 alarm 秒数", "group": "X 监控", "kind": "int", "default": "90", "effect": "next_run"},
    {"name": "X_WATCHLIST_MAX_WORKERS", "label": "X 抓取并发", "group": "X 监控", "kind": "int", "default": "5", "effect": "next_run"},
    {"name": "X_WATCHLIST_MAX_ATTEMPTS", "label": "X 抓取重试次数", "group": "X 监控", "kind": "int", "default": "1", "effect": "next_run"},
    {"name": "X_WATCHLIST_MAX_MEDIA_HTML_HYDRATE_ITEMS", "label": "X HTML 补图条数", "group": "X 监控", "kind": "int", "default": "6", "effect": "next_run"},
    {"name": "X_WATCHLIST_MEDIA_HTML_WORKERS", "label": "X HTML 补图并发", "group": "X 监控", "kind": "int", "default": "3", "effect": "next_run"},
    {"name": "X_WATCHLIST_CONTEXT_REPAIR_RETRY_ROUNDS", "label": "X 上下文修复轮数", "group": "X 监控", "kind": "int", "default": "2", "effect": "next_run"},
    {"name": "X_WATCHLIST_MAX_CONTEXT_REPAIR_ITEMS", "label": "X 每轮修复条数", "group": "X 监控", "kind": "int", "default": "4", "effect": "next_run"},
    {"name": "X_WATCHLIST_CONTEXT_REPAIR_WORKERS", "label": "X 上下文修复并发", "group": "X 监控", "kind": "int", "default": "4", "effect": "next_run"},
    {"name": "X_WATCHLIST_CONTEXT_REPAIR_RETRY_SLEEP_SECONDS", "label": "X 修复轮间隔秒数", "group": "X 监控", "kind": "text", "default": "2", "effect": "next_run"},
    {"name": "X_WATCHLIST_HELD_CONTEXT_REPAIR_TIMEOUT_SECONDS", "label": "X held 修复超时秒数", "group": "X 监控", "kind": "int", "default": "8", "effect": "next_run"},
    {"name": "X_WATCHLIST_HELD_CONTEXT_REPAIR_ITEMS", "label": "X held 修复条数", "group": "X 监控", "kind": "int", "default": "4", "effect": "next_run"},
    {"name": "X_WATCHLIST_SENT_CONTEXT_REPAIR_LOOKBACK_HOURS", "label": "X 已发修复回看小时", "group": "X 监控", "kind": "int", "default": "72", "effect": "next_run"},
    {"name": "X_WATCHLIST_SENT_CONTEXT_REPAIR_MAX_ATTEMPTS", "label": "X 已发修复最大尝试", "group": "X 监控", "kind": "int", "default": "8", "effect": "next_run"},
    {"name": "X_WATCHLIST_SENT_CONTEXT_REPAIR_COOLDOWN_MINUTES", "label": "X 已发修复冷却分钟", "group": "X 监控", "kind": "int", "default": "20", "effect": "next_run"},
    {"name": "X_WATCHLIST_SENT_CONTEXT_REPAIR_ITEMS", "label": "X 已发修复条数", "group": "X 监控", "kind": "int", "default": "2", "effect": "next_run"},
]
ENV_CONFIG_BY_NAME = {item["name"]: item for item in ENV_CONFIG_SCHEMA}
ADMIN_VISIBLE_ENV_NAMES = [
    "DASHBOARD_US_FEATURES_ENABLED",
    "DASHBOARD_GROK_MODEL",
    "DASHBOARD_GROK_CONTEXT_LENGTH",
    "DASHBOARD_GROK_MAX_TOKENS",
    "DASHBOARD_GROK_BASE_URL",
    "DASHBOARD_GROK_API_KEY",
    "X_WATCHLIST_ACCOUNTS",
    "X_WATCHLIST_DAEMON_INTERVAL_SECONDS",
    "DASHBOARD_US_RATING_CRON",
    "US_RATING_CONTEXT_LENGTH",
    "US_RATING_MAX_TOKENS",
    "US_RATING_DEADLINE_SECONDS",
    "US_RATING_REQUEST_TIMEOUT_SECONDS",
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
    "DASHBOARD_MARKET_GUIDANCE_ENABLED",
    TRADE_DISCIPLINE_TEXT_ENV,
    "DASHBOARD_MAX_OPEN_POSITIONS",
    "DASHBOARD_MAX_NEW_BUYS_PER_DECISION",
    "DASHBOARD_MAX_SINGLE_POSITION_PCT",
    "DASHBOARD_MAX_TOTAL_POSITION_PCT",
    "DASHBOARD_MIN_CASH_RESERVE_PCT",
    "DASHBOARD_MORNING_MAX_OPEN_POSITIONS",
    "DASHBOARD_B1_SCHEDULE_TIMES",
    "DASHBOARD_B3_EXIT_TIME",
    "DASHBOARD_TIME_EXIT_TIME",
    STRATEGY_SOURCE_ENV,
    PERSONA_STRATEGY_ENV,
    PRESET_STRATEGY_TEXT_ENV,
    "DASHBOARD_US_MARKET_SUMMARY_CRON",
    "US_MARKET_SUMMARY_MAX_TOKENS",
    "DASHBOARD_MARKET_AUCTION_CRON",
    "DASHBOARD_MARKET_MIDDAY_CRON",
    "DASHBOARD_MARKET_CLOSE_CRON",
    "A_SHARE_MODEL_SUMMARY_ENABLED",
    "A_SHARE_MODEL_SUMMARY_MODEL",
    "A_SHARE_MODEL_SUMMARY_CONTEXT_LENGTH",
    "A_SHARE_MODEL_SUMMARY_MAX_TOKENS",
    "A_SHARE_MODEL_SUMMARY_BASE_URL",
    "A_SHARE_MODEL_SUMMARY_API_KEY",
    "A_SHARE_MODEL_SUMMARY_DEADLINE_SECONDS",
    "A_SHARE_MODEL_SUMMARY_REQUEST_TIMEOUT_SECONDS",
    "X_WATCHLIST_MAX_TOKENS",
    "DASHBOARD_CRON_MAX_ATTEMPTS",
    "DASHBOARD_CRON_RETRY_DELAY_SECONDS",
    "DASHBOARD_INDICES_TTL_SECONDS",
]
TRADER_RUNTIME_ENV_NAMES = {
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
    "DASHBOARD_MARKET_GUIDANCE_ENABLED",
    TRADE_DISCIPLINE_TEXT_ENV,
    "DASHBOARD_MAX_OPEN_POSITIONS",
    "DASHBOARD_MAX_NEW_BUYS_PER_DECISION",
    "DASHBOARD_MAX_SINGLE_POSITION_PCT",
    "DASHBOARD_MAX_TOTAL_POSITION_PCT",
    "DASHBOARD_MIN_CASH_RESERVE_PCT",
    "DASHBOARD_MORNING_MAX_OPEN_POSITIONS",
    "DASHBOARD_B3_EXIT_TIME",
    "DASHBOARD_TIME_EXIT_TIME",
    "DASHBOARD_TIME_STOP_EXIT_TIME",
    STRATEGY_SOURCE_ENV,
    PERSONA_STRATEGY_ENV,
    PRESET_STRATEGY_TEXT_ENV,
}
ENV_GROUP_ORDER = [
    "牛牛美股",
    "消息面预检模型",
    "买卖决策模型",
    "选股及买卖决策时间点",
    "选股策略",
    "盘面监控生产时间点",
    "指数行情更新周期",
    "基础路径",
    "访问控制",
    "限流与缓存",
    "任务调度",
    "上游模型覆盖",
    "X 监控",
    "其他",
]


def _now_ts() -> float:
    return time.time()


def hash_token(token: str) -> str:
    return hashlib.sha256(str(token).encode('utf-8')).hexdigest()


def check_rate_limit(scope: str, key: str, limit: int, window: int | None = None) -> tuple[bool, int]:
    if not RATE_LIMIT_ENABLED or limit <= 0:
        return True, 0
    window = window or RATE_LIMIT_WINDOW_SECONDS
    now = time.time()
    bucket_key = (scope, key or "unknown")
    with RATE_LIMIT_LOCK:
        started_at, count = RATE_LIMIT_BUCKETS.get(bucket_key, (now, 0))
        if now - started_at >= window:
            started_at, count = now, 0
        if count >= limit:
            return False, max(1, int(window - (now - started_at)))
        RATE_LIMIT_BUCKETS[bucket_key] = (started_at, count + 1)
        if len(RATE_LIMIT_BUCKETS) > 10000:
            cutoff = now - window * 3
            for old_key, (old_started, _) in list(RATE_LIMIT_BUCKETS.items()):
                if old_started < cutoff:
                    RATE_LIMIT_BUCKETS.pop(old_key, None)
    return True, 0


def ensure_stats_db() -> None:
    STATS_DB.parent.mkdir(parents=True, exist_ok=True)
    with VISIT_STATS_LOCK:
        with closing(sqlite3.connect(STATS_DB)) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS visit_stats (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS unique_visitors (
                    visitor_hash TEXT PRIMARY KEY,
                    first_seen_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS stats_migrations (
                    key TEXT PRIMARY KEY,
                    completed_at REAL NOT NULL
                )
            """)
            migrate_legacy_visit_stats(con)
            con.commit()


def sqlite_table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def migrate_legacy_visit_stats(con: sqlite3.Connection) -> None:
    """Move visit counters out of the retired dashboard user database once."""
    if LEGACY_STATS_DB == STATS_DB or not LEGACY_STATS_DB.exists():
        return
    if con.execute(
        "SELECT 1 FROM stats_migrations WHERE key=?",
        (LEGACY_STATS_MIGRATION_KEY,),
    ).fetchone():
        return

    try:
        with closing(sqlite3.connect(LEGACY_STATS_DB)) as legacy:
            has_visit_stats = sqlite_table_exists(legacy, "visit_stats")
            has_unique_visitors = sqlite_table_exists(legacy, "unique_visitors")
            if not has_visit_stats and not has_unique_visitors:
                return

            legacy_views = 0
            legacy_updated_at = 0.0
            if has_visit_stats:
                visit_row = legacy.execute(
                    "SELECT value, updated_at FROM visit_stats WHERE key='home_views'"
                ).fetchone()
                if visit_row:
                    legacy_views = int(visit_row[0] or 0)
                    legacy_updated_at = float(visit_row[1] or 0.0)

            legacy_visitors = []
            if has_unique_visitors:
                legacy_visitors = legacy.execute(
                    "SELECT visitor_hash, first_seen_at, last_seen_at FROM unique_visitors"
                ).fetchall()
    except sqlite3.Error as exc:
        print(f"访问统计迁移跳过：无法读取旧统计库 {LEGACY_STATS_DB}: {exc}", file=sys.stderr)
        return

    current_row = con.execute(
        "SELECT value, updated_at FROM visit_stats WHERE key='home_views'"
    ).fetchone()
    current_views = int(current_row[0] or 0) if current_row else 0
    current_updated_at = float(current_row[1] or 0.0) if current_row else 0.0
    if legacy_views > current_views:
        migrated_views = legacy_views + current_views
    else:
        migrated_views = current_views
    if migrated_views or current_row:
        con.execute(
            "INSERT INTO visit_stats(key,value,updated_at) VALUES('home_views',?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (migrated_views, max(legacy_updated_at, current_updated_at, _now_ts())),
        )

    con.executemany(
        "INSERT INTO unique_visitors(visitor_hash,first_seen_at,last_seen_at) VALUES(?,?,?) "
        "ON CONFLICT(visitor_hash) DO UPDATE SET "
        "first_seen_at=MIN(unique_visitors.first_seen_at,excluded.first_seen_at), "
        "last_seen_at=MAX(unique_visitors.last_seen_at,excluded.last_seen_at)",
        legacy_visitors,
    )
    con.execute(
        "INSERT OR REPLACE INTO stats_migrations(key,completed_at) VALUES(?,?)",
        (LEGACY_STATS_MIGRATION_KEY, _now_ts()),
    )


def increment_visit_count(visitor_id: str) -> dict[str, int]:
    """Count page views for the main dashboard only; API polling is excluded."""
    ensure_stats_db()
    now = _now_ts()
    visitor_hash = hash_token(visitor_id)
    with VISIT_STATS_LOCK:
        with closing(sqlite3.connect(STATS_DB)) as con:
            con.execute("INSERT OR IGNORE INTO visit_stats(key,value,updated_at) VALUES('home_views',0,?)", (now,))
            con.execute("UPDATE visit_stats SET value=value+1, updated_at=? WHERE key='home_views'", (now,))
            con.execute(
                "INSERT INTO unique_visitors(visitor_hash,first_seen_at,last_seen_at) VALUES(?,?,?) "
                "ON CONFLICT(visitor_hash) DO UPDATE SET last_seen_at=excluded.last_seen_at",
                (visitor_hash, now, now),
            )
            visit_row = con.execute("SELECT value FROM visit_stats WHERE key='home_views'").fetchone()
            unique_row = con.execute("SELECT COUNT(*) FROM unique_visitors").fetchone()
            con.commit()
    return {"visits": int(visit_row[0] if visit_row else 0), "unique": int(unique_row[0] if unique_row else 0)}


def parse_request_cookies(header: str | None) -> dict[str, str]:
    jar = cookies.SimpleCookie()
    if header:
        try:
            jar.load(header)
        except cookies.CookieError:
            return {}
    return {k: v.value for k, v in jar.items()}

def get_trader_module():
    global TRADER_MODULE, TRADER_MODULE_MTIME
    current_mtime = TRADER_SCRIPT.stat().st_mtime if TRADER_SCRIPT.exists() else 0.0
    if TRADER_MODULE is None or current_mtime != TRADER_MODULE_MTIME:
        import importlib.util
        spec = importlib.util.spec_from_file_location("niuniu_practice_trader", TRADER_SCRIPT)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            TRADER_MODULE = module
            TRADER_MODULE_MTIME = current_mtime
    return TRADER_MODULE

def run_dashboard_helper(script_name: str, fallback: dict[str, Any], timeout: int = 90) -> dict[str, Any]:
    """Run dashboard helper API scripts out-of-process.

    Some akshare paths load native JavaScript runtimes that can abort the whole
    Python process when imported inside the threaded HTTP server. Running helpers
    in a child process isolates those native crashes from the dashboard service.
    """
    script = Path(__file__).with_name(script_name)
    try:
        raw = subprocess.check_output([sys.executable, str(script)], text=True, timeout=timeout, stderr=subprocess.DEVNULL)
        return json.loads(raw)
    except Exception as exc:
        return {**fallback, "error": str(exc)}


def current_cn_datetime() -> datetime:
    return datetime.now(CN_TZ).replace(tzinfo=None)


def current_cn_date_key(now: datetime | None = None) -> str:
    return (now or current_cn_datetime()).strftime("%Y-%m-%d")


def dashboard_trading_day_status(now: datetime | None = None) -> dict[str, Any]:
    current = now or current_cn_datetime()
    return trading_day_status(current)


def annotate_practice_payload_clock(payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    current = now or current_cn_datetime()
    current_date = current_cn_date_key(current)
    payload["current_date"] = current_date
    payload["current_time"] = current.strftime("%Y-%m-%d %H:%M:%S")
    calendar = payload.get("trading_calendar")
    if not isinstance(calendar, dict) or str(calendar.get("date") or "") != current_date:
        calendar = dashboard_trading_day_status(current)
    payload["trading_calendar"] = calendar
    return payload


def produce_indices_data() -> dict[str, Any]:
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "indices",
            os.path.join(os.path.dirname(__file__), "indices_dashboard_api.py"),
        )
        if spec and spec.loader:
            indices_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(indices_mod)
            raw_result = indices_mod.fetch_indices_data()
            return raw_result if isinstance(raw_result, dict) else {"items": raw_result}
        return {"items": []}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


def apply_hot_stocks_sort(data: dict[str, Any], sort_by: str) -> dict[str, Any]:
    payload = dict(data or {})
    sort_key = (sort_by or "amount").strip().lower()
    if sort_key in ("turnover", "turnover_top"):
        payload["items"] = payload.get("turnover_top", [])
    elif sort_key in ("volume", "volume_top"):
        payload["items"] = payload.get("volume_top", [])
    elif sort_key in ("gain", "hot"):
        payload["items"] = payload.get("gain_top", [])
    else:
        payload["items"] = payload.get("amount_top", payload.get("items", []))
    return payload


def get_practice_payload() -> dict[str, Any]:
    try:
        trader = get_trader_module()
        # 盘面时间内按 dashboard 刷新节奏补记账户权益点；与是否交易/是否有B1候选无关。
        if hasattr(trader, "maybe_record_session_equity_heartbeat"):
            trader.maybe_record_session_equity_heartbeat()
        payload = trader.get_dashboard_payload()
        payload["trade_markers"] = compact_trade_markers(payload.get("trade_log") or [])
        annotate_practice_payload_clock(payload)
        try:
            refresh_b1_candidate_cache_from_current_pool()
        except Exception as refresh_exc:
            print(f"[WARN] B1候选池复核失败: {type(refresh_exc).__name__}: {refresh_exc}", flush=True)
        return payload
    except Exception as exc:
        print(f"[WARN] practice payload error: {type(exc).__name__}: {exc}", flush=True)
        payload = {"positions": [], "cash": 0, "total_equity": 0, "initial_cash": 0,
                   "total_pnl": 0, "total_pnl_pct": 0, "trade_log": [], "decision_log": [],
                   "equity_history": [], "trade_markers": [], "last_error": str(exc), "decision_model": "", "decision_provider": ""}
        return annotate_practice_payload_clock(payload)

def downsample_sequence(items: list[Any], max_points: int) -> list[Any]:
    items = list(items or [])
    if max_points <= 0 or len(items) <= max_points:
        return items
    last_idx = len(items) - 1
    selected: list[Any] = []
    seen: set[int] = set()
    for i in range(max_points):
        idx = int(i * last_idx / max(1, max_points - 1))
        if idx in seen:
            continue
        seen.add(idx)
        selected.append(items[idx])
    if selected and selected[-1] is not items[-1]:
        selected[-1] = items[-1]
    return selected


def parse_dashboard_ts(value: str) -> datetime | None:
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def is_a_share_trading_day_for_dashboard(dt: datetime) -> bool:
    return calendar_is_a_share_trading_day(dt)


def filter_future_equity_points(
    history: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    grace_seconds: int = 120,
) -> list[dict[str, Any]]:
    now = now or current_cn_datetime()
    cutoff = now + timedelta(seconds=max(0, int(grace_seconds or 0)))
    filtered: list[dict[str, Any]] = []
    for point in history or []:
        if not isinstance(point, dict):
            continue
        dt = parse_dashboard_ts(str(point.get("time") or ""))
        if dt is not None and (dt.date() > now.date() or (dt.date() == now.date() and dt > cutoff)):
            continue
        if dt is not None and not is_a_share_trading_day_for_dashboard(dt):
            continue
        filtered.append(point)
    return filtered


def compact_intraday_equity_history(
    history: list[dict[str, Any]],
    *,
    max_points: int = 120,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    points = sorted(
        filter_future_equity_points(history or [], now=now),
        key=lambda point: str(point.get("time") or "") if isinstance(point, dict) else "",
    )
    if not points:
        return []
    latest_day = max(
        (str(point.get("time") or "")[:10] for point in points if len(str(point.get("time") or "")) >= 10),
        default="",
    )
    day_points = [p for p in points if str(p.get("time") or "").startswith(latest_day)] if latest_day else points
    return downsample_sequence(day_points, max_points)


def compact_daily_equity_history(
    history: list[dict[str, Any]],
    *,
    max_days: int = 260,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    points = sorted(
        filter_future_equity_points(history or [], now=now),
        key=lambda point: str(point.get("time") or "") if isinstance(point, dict) else "",
    )
    for point in points:
        if not isinstance(point, dict):
            continue
        date = str(point.get("time") or "")[:10]
        if date:
            by_date[date] = point
    return [by_date[date] for date in sorted(by_date.keys())][-max_days:]


def compact_strategy_performance(perf: dict[str, Any], *, max_exit_items: int = 12) -> dict[str, Any]:
    if not isinstance(perf, dict):
        return {}
    result = dict(perf)
    exit_rules = perf.get("exit_rule")
    if isinstance(exit_rules, dict):
        compact_rules: dict[str, Any] = {}
        for key, value in exit_rules.items():
            if not isinstance(value, dict):
                compact_rules[key] = value
                continue
            next_value = dict(value)
            items = next_value.get("items")
            if isinstance(items, list) and len(items) > max_exit_items:
                next_value["items"] = items[-max_exit_items:]
                next_value["items_truncated"] = len(items) - max_exit_items
            compact_rules[key] = next_value
        result["exit_rule"] = compact_rules
    return result


def filter_today_log_entries(
    entries: list[Any],
    *,
    max_items: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    today = current_cn_date_key(now)
    rows = [
        item for item in (entries or [])
        if isinstance(item, dict) and str(item.get("time") or "").startswith(today)
    ]
    return rows[:max_items] if max_items is not None else rows


def compact_trade_markers(
    entries: list[Any],
    *,
    max_items: int = 200,
) -> list[dict[str, Any]]:
    """Keep the compact fill fields needed to annotate equity charts."""
    fields = (
        "time", "action", "code", "name", "shares", "price", "pnl", "pnl_pct",
        "position_after_trade_pct", "position_after_trade_qty",
    )
    source_rows: list[dict[str, Any]] = []
    for item in entries or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").upper()
        time_text = str(item.get("time") or "")
        if action not in {"BUY", "SELL"} or not time_text:
            continue
        row = {key: item.get(key) for key in fields if item.get(key) is not None}
        row["action"] = action
        row["time"] = time_text
        source_rows.append(row)

    source_rows.sort(key=lambda item: str(item.get("time") or ""))
    inferred_positions: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for row in source_rows:
        action = str(row.get("action") or "")
        code = str(row.get("code") or "")
        try:
            shares = max(0, int(float(row.get("shares") or 0)))
        except (TypeError, ValueError):
            shares = 0
        before_qty = inferred_positions.get(code, 0)
        if action == "BUY":
            inferred_positions[code] = before_qty + shares
        else:
            inferred_after_qty = max(0, before_qty - shares)
            explicit_after_qty = row.get("position_after_trade_qty")
            try:
                after_qty = max(0, int(float(explicit_after_qty))) if explicit_after_qty is not None else inferred_after_qty
            except (TypeError, ValueError):
                after_qty = inferred_after_qty
            inferred_positions[code] = after_qty

            explicit_after_pct = row.get("position_after_trade_pct")
            try:
                is_full_exit = float(explicit_after_pct) <= 0 if explicit_after_pct is not None else before_qty > 0 and after_qty <= 0
            except (TypeError, ValueError):
                is_full_exit = before_qty > 0 and after_qty <= 0
            row["is_full_exit"] = bool(is_full_exit)
        rows.append(row)
    return rows[-max(0, int(max_items or 0)):] if max_items else rows


def get_practice_payload_fast() -> dict[str, Any]:
    """Return a local portfolio snapshot without network quote refresh or auto trading checks."""
    try:
        now = current_cn_datetime()
        trader = get_trader_module()
        state = trader.load_state()
        payload = trader.enrich_portfolio(state)
        equity_history = state.get("equity_history", []) or []
        daily_equity_history = state.get("daily_equity_history", []) or []
        # Keep the same intraday point density as the full payload. Otherwise the
        # chart first renders a downsampled fast response, then visibly jumps when
        # the full response arrives a few seconds later.
        payload["equity_history"] = compact_intraday_equity_history(equity_history, max_points=0, now=now)
        payload["daily_equity_history"] = compact_daily_equity_history([*equity_history, *daily_equity_history], now=now)
        payload["trade_markers"] = compact_trade_markers(state.get("trade_log") or [])
        payload["trade_log"] = filter_today_log_entries(payload.get("trade_log") or [], now=now)
        payload["decision_log"] = filter_today_log_entries(payload.get("decision_log") or [], now=now)
        payload["trading_calendar"] = dashboard_trading_day_status(now)
        payload["trading_paused"] = state.get("trading_paused", False)
        payload["pause_reason"] = state.get("pause_reason", "")
        payload["pause_since"] = state.get("pause_since", "")
        strategy_performance = trader.track_strategy_performance(state) if hasattr(trader, "track_strategy_performance") else {}
        payload["strategy_performance"] = compact_strategy_performance(strategy_performance)
        if hasattr(trader, "build_trade_rule_note"):
            payload["trade_rule_note"] = trader.build_trade_rule_note()
        payload["snapshot_mode"] = "fast"
        annotate_practice_payload_clock(payload, now=now)
        return payload
    except Exception as exc:
        print(f"[WARN] fast practice payload error: {type(exc).__name__}: {exc}", flush=True)
        payload = {"positions": [], "cash": 0, "total_equity": 0, "initial_cash": 0,
                   "total_pnl": 0, "total_pnl_pct": 0, "trade_log": [], "decision_log": [],
                   "equity_history": [], "trade_markers": [], "last_error": str(exc), "snapshot_mode": "fast"}
        return annotate_practice_payload_clock(payload)

def normalize_b1_payload_for_trader(b1_payload: dict[str, Any]) -> dict[str, Any]:
    items = b1_payload.get("trade_items") or b1_payload.get("items") or b1_payload.get("candidates") or []
    payload = {"items": items, "generated_at": b1_payload.get("generated_at", "")}
    for key in ("schedule_slot", "schedule_run_kind", "schedule_triggered_at"):
        if b1_payload.get(key):
            payload[key] = b1_payload.get(key)
    return payload

def run_practice_decision(b1_payload: dict[str, Any]) -> dict[str, Any]:
    return get_trader_module().run_decision_after_b1(b1_payload)


def _tencent_key_for_code(code: str) -> str:
    code = str(code or "").strip()
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def b1_cache_has_newer_generation(base_payload: dict[str, Any]) -> bool:
    try:
        if not B1_CACHE_FILE.exists():
            return False
        latest = json.loads(B1_CACHE_FILE.read_text())
    except Exception:
        return False
    latest_generated = str(latest.get("generated_at") or "")[:19]
    base_generated = str(base_payload.get("generated_at") or "")[:19]
    return bool(latest_generated and (not base_generated or latest_generated > base_generated))


def refresh_b1_candidate_cache_from_current_pool() -> dict[str, Any]:
    """Refresh quotes and strategy scores for the current B1 candidate cache.

    This intentionally does not run a full-market scan. It keeps the existing
    candidate universe, revalidates those names with fresh quotes/K-lines, then
    rewrites the candidate cache for the dashboard.
    """
    global B1_CANDIDATE_REFRESH_LAST_TS
    now_ts_float = time.time()
    if B1_CANDIDATE_REFRESH_MIN_SECONDS > 0 and now_ts_float - B1_CANDIDATE_REFRESH_LAST_TS < B1_CANDIDATE_REFRESH_MIN_SECONDS:
        return {"skipped": True, "reason": "cooldown"}
    if not B1_CANDIDATE_REFRESH_LOCK.acquire(blocking=False):
        return {"skipped": True, "reason": "refresh_in_progress"}
    try:
        if not B1_CACHE_FILE.exists():
            return {"skipped": True, "reason": "missing_cache"}
        try:
            parsed = json.loads(B1_CACHE_FILE.read_text())
        except Exception as exc:
            return {"skipped": True, "reason": f"bad_cache:{type(exc).__name__}"}
        items = parsed.get("items") or parsed.get("candidates") or []
        base_items = [item for item in items if isinstance(item, dict) and str(item.get("code") or "").strip()]
        if not base_items:
            if b1_cache_has_newer_generation(parsed):
                B1_CANDIDATE_REFRESH_LAST_TS = time.time()
                return {"skipped": True, "reason": "newer_full_scan_available"}
            parsed["items"] = []
            parsed["candidates"] = []
            parsed["count"] = 0
            parsed["refreshed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            B1_CACHE_FILE.write_text(json.dumps(parsed, ensure_ascii=False))
            MULTI_STRATEGY_CACHE_FILE.write_text(json.dumps(parsed, ensure_ascii=False, indent=2))
            B1_CANDIDATE_REFRESH_LAST_TS = time.time()
            return {"updated": 0, "count": 0}

        import multi_strategy_screen as scanner

        keys_by_code = {str(item.get("code") or ""): _tencent_key_for_code(str(item.get("code") or "")) for item in base_items}
        quote_map = scanner.tencent_batch_quote(list(keys_by_code.values()))
        refreshed: list[dict[str, Any]] = []
        previous_by_code = {str(item.get("code") or ""): item for item in base_items}
        for code, tencent_key in keys_by_code.items():
            old = previous_by_code.get(code) or {}
            name = old.get("name") or ""
            quote = quote_map.get(tencent_key) or {}
            price = quote.get("price")
            amount = quote.get("amount") or 0
            if price is None or float(price or 0) <= 0:
                continue
            if float(amount or 0) < 8e8:
                continue
            multi = scanner.analyze_all_strategies(code, tencent_key)
            if not multi:
                continue
            best = multi["strategies"].get(multi["best_strategy"], {})
            item = {
                **old,
                "code": code,
                "name": name,
                "price": quote.get("price"),
                "change_pct": quote.get("change_pct"),
                "amount": quote.get("amount"),
                "amount_yi": round(float(quote.get("amount") or 0) / 1e8, 1) if quote.get("amount") else None,
                "turnover": quote.get("turnover"),
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
                "trade_ready": scanner.candidate_is_trade_ready(best),
                "strategies": multi["strategies"],
                "consensus_count": multi.get("consensus_count", 0),
                "consensus_boost": multi.get("consensus_boost", 0),
            }
            refreshed.append(item)

        def sort_key(item: dict[str, Any]):
            score = item.get("best_decision_score") or item.get("best_score") or 0
            above = 1 if item.get("above_bbi") else 0
            dist = abs(item.get("distance_pct") or 99)
            return (score, above, -dist)

        refreshed.sort(key=sort_key, reverse=True)
        selected = scanner.select_display_candidates(refreshed)
        trade_items = scanner.select_trade_candidates(refreshed)
        scanner.annotate_candidate_industries(selected, trade_items)
        from collections import Counter
        strat_counts = Counter(str(item.get("best_strategy") or "unknown") for item in selected)
        refreshed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if b1_cache_has_newer_generation(parsed):
            B1_CANDIDATE_REFRESH_LAST_TS = time.time()
            return {"skipped": True, "reason": "newer_full_scan_available"}
        output = {
            **parsed,
            "items": selected,
            "candidates": selected,
            "count": len(selected),
            "trade_items": trade_items,
            "trade_count": len(trade_items),
            "strategy_distribution": dict(strat_counts),
            "strategy_meta": scanner.active_strategy_meta() if hasattr(scanner, "active_strategy_meta") else scanner.STRATEGY_META,
            "strategy_score_profiles": scanner.active_strategy_score_profiles() if hasattr(scanner, "active_strategy_score_profiles") else scanner.STRATEGY_SCORE_PROFILES,
            "candidate_refresh": {
                "refreshed_at": refreshed_at,
                "source": "current_candidate_pool",
                "input_count": len(base_items),
                "updated": len(refreshed),
                "filtered_out": max(0, len(base_items) - len(refreshed)),
            },
            "refreshed_at": refreshed_at,
        }
        json_text = json.dumps(output, ensure_ascii=False, indent=2)
        B1_CACHE_FILE.write_text(json_text + "\n")
        MULTI_STRATEGY_CACHE_FILE.write_text(json_text + "\n")
        with API_RESPONSE_LOCK:
            API_RESPONSE_CACHE.pop("b1_screen", None)
        B1_CANDIDATE_REFRESH_LAST_TS = time.time()
        return output["candidate_refresh"]
    finally:
        B1_CANDIDATE_REFRESH_LOCK.release()


def record_practice_decision_event(
    b1_payload: dict[str, Any],
    summary: str,
    trade_reason: str,
    *,
    trade_allowed: bool = False,
    error: str = "",
    mark_b1_done: bool = False,
) -> None:
    try:
        trader = get_trader_module()
        generated_at = b1_payload.get("generated_at", "")
        log_entry = {
            "time": trader.now_ts() if hasattr(trader, "now_ts") else datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "b1_generated_at": generated_at,
            "trade_allowed": trade_allowed,
            "trade_reason": trade_reason,
            "decision": {
                "summary": summary,
                "actions": [],
                "model": "SYSTEM_SCHEDULE",
                "provider": "dashboard",
                "error": error,
            },
            "executed": [],
        }
        for key in ("schedule_slot", "schedule_run_kind", "schedule_triggered_at"):
            if b1_payload.get(key):
                log_entry[key] = b1_payload.get(key)
        if hasattr(trader, "record_decision_log_entry"):
            trader.record_decision_log_entry(log_entry, mark_b1_done=mark_b1_done)
    except Exception as exc:
        print(f"[WARN] 写入牛牛实战决策日志失败: {type(exc).__name__}: {exc}", flush=True)


def run_practice_decision_logged(b1_payload: dict[str, Any], *, record_start: bool = False) -> dict[str, Any]:
    payload = normalize_b1_payload_for_trader(b1_payload)
    item_count = len(payload.get("items") or [])
    slot_note = ""
    if payload.get("schedule_slot"):
        kind_label = "补跑" if payload.get("schedule_run_kind") == "catchup" else "定时"
        slot_note = f"（计划{str(payload.get('schedule_slot'))[-5:]}{kind_label}）"
    if not item_count:
        record_practice_decision_event(
            payload,
            f"选股完成{slot_note}但没有候选股，本轮不执行买卖。",
            f"选股完成{slot_note}：0只候选",
            mark_b1_done=True,
        )
        return {"skipped": True, "reason": "no_candidates"}
    if record_start:
        record_practice_decision_event(
            payload,
            f"选股完成{slot_note}：{item_count}只候选，开始生成买卖决策。",
            f"选股后买卖决策开始{slot_note}",
        )
    try:
        return run_practice_decision(payload)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        record_practice_decision_event(
            payload,
            f"选股完成但买卖决策失败：{err}",
            "选股后买卖决策失败",
            error=err,
        )
        raise


def maybe_run_practice_decision_async(b1_payload: dict[str, Any]) -> None:
    payload = normalize_b1_payload_for_trader(b1_payload)
    if not payload.get("items"):
        run_practice_decision_logged(payload)
        return
    dedup_key = f"{payload['generated_at']}_{len(payload['items'])}"
    if dedup_key in PRACTICE_DECISION_KEYS:
        return
    PRACTICE_DECISION_KEYS.add(dedup_key)
    def _worker() -> None:
        try:
            run_practice_decision_logged(payload)
        except Exception as exc:
            print(f"[WARN] 牛牛实战决策失败: {type(exc).__name__}: {exc}", flush=True)
    if len(PRACTICE_DECISION_KEYS) > 20:
        PRACTICE_DECISION_KEYS.clear()
    threading.Thread(target=_worker, name="niuniu-practice-decision", daemon=True).start()

def load_b1_cache() -> dict[str, Any]:
    try:
        if B1_CACHE_FILE.exists():
            raw = B1_CACHE_FILE.read_text()
            parsed = json.loads(raw)
            return {**parsed, "generated_at": parsed.get("generated_at", ""), 
                    "count": parsed.get("count", len(parsed.get("items", []) or parsed.get("candidates", []))),
                    "items": parsed.get("items") or parsed.get("candidates", [])}
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": str(exc), "items": [], "count": 0, "generated_at": ""}
    return {"items": [], "count": 0, "generated_at": ""}

def trigger_b1_scan(
    force: bool = False,
    decision_mode: str = "async",
    *,
    schedule_slot: str = "",
    schedule_run_kind: str = "",
) -> dict[str, Any]:
    import subprocess, sys
    script = Path(os.environ.get("DASHBOARD_B1_SCANNER", SCRIPT_DIR / "multi_strategy_screen.py")).expanduser()
    if not script.exists():
        return {"error": f"扫描脚本不存在：{script}", "items": [], "count": 0, "generated_at": "", "running": False}
    try:
        args = [sys.executable, str(script), "--json"] + (["--force"] if force else [])
        result = subprocess.run(args, capture_output=True, text=True, timeout=B1_SCAN_TIMEOUT_SECONDS)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            items = data.get("items") or data.get("candidates") or []
            candidates = data.get("candidates") or items
            trade_items = data.get("trade_items") or items
            schedule_meta = {}
            if schedule_slot:
                schedule_meta = {
                    "schedule_slot": schedule_slot,
                    "schedule_run_kind": schedule_run_kind or "scheduled",
                    "schedule_triggered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            cache = {**data, "items": items, "candidates": candidates, "count": len(items),
                     "trade_items": trade_items, "trade_count": len(trade_items),
                     "total_analyzed": data.get("total_analyzed", 0),
                     "generated_at": data.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "running": False, "error": "", "cooldown_remaining_seconds": 0,
                     **schedule_meta}
            with B1_CANDIDATE_REFRESH_LOCK:
                B1_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))
            if decision_mode == "sync":
                cache["decision_result"] = run_practice_decision_logged(cache, record_start=True)
            elif decision_mode == "async":
                maybe_run_practice_decision_async(cache)
            return cache
        return {"error": (result.stderr or result.stdout)[-500:], "items": [], "count": 0, "generated_at": "", "running": False}
    except subprocess.TimeoutExpired:
        return {"error": f"扫描超时（{B1_SCAN_TIMEOUT_SECONDS}s）", "items": [], "count": 0, "generated_at": "", "running": False}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "items": [], "count": 0, "generated_at": "", "running": False}


def b1_cache_generated_for_slot(slot_key: str) -> bool:
    try:
        if not B1_CACHE_FILE.exists():
            return False
        generated_at = (json.loads(B1_CACHE_FILE.read_text()).get("generated_at") or "")[:16]
        return generated_at == slot_key
    except Exception:
        return False


def _b1_schedule_now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_b1_schedule_state_unlocked() -> dict[str, Any]:
    try:
        state = json.loads(B1_SCHEDULE_STATE_FILE.read_text())
        if not isinstance(state, dict):
            state = {}
    except Exception:
        state = {}
    slots = state.get("slots")
    if not isinstance(slots, dict):
        state["slots"] = {}
    return state


def _save_b1_schedule_state_unlocked(state: dict[str, Any]) -> None:
    B1_SCHEDULE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = B1_SCHEDULE_STATE_FILE.with_suffix(B1_SCHEDULE_STATE_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(B1_SCHEDULE_STATE_FILE)


def _b1_schedule_slot_datetime(now: datetime, hhmm: str) -> datetime | None:
    try:
        hour_text, minute_text = str(hhmm).strip().split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except Exception:
        return None


def _b1_schedule_slot_lag_seconds(slot_key: str) -> float:
    try:
        slot_dt = datetime.strptime(slot_key, "%Y-%m-%d %H:%M")
        return max(0.0, (datetime.now() - slot_dt).total_seconds())
    except Exception:
        return 0.0


def _mark_b1_schedule_slot(slot_key: str, status: str, **fields: Any) -> None:
    with B1_SCHEDULE_LOCK:
        state = _load_b1_schedule_state_unlocked()
        slots = state.setdefault("slots", {})
        slot = slots.setdefault(slot_key, {"scheduled_at": slot_key})
        now_text = _b1_schedule_now_text()
        slot.update({"status": status, "updated_at": now_text, **fields})
        if status == "running":
            slot.pop("error", None)
            slot["started_at"] = now_text
            slot["started_ts"] = time.time()
            slot["pid"] = os.getpid()
        if status in {"ok", "error", "skipped"}:
            if status == "ok":
                slot.pop("error", None)
            slot["finished_at"] = now_text
            slot["finished_ts"] = time.time()
            B1_SCHEDULE_RUN_KEYS.discard(slot_key)
        state["slots"] = slots
        _save_b1_schedule_state_unlocked(state)


def claim_due_b1_schedule_slot(now: datetime | None = None) -> str | None:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return None
    catchup_seconds = max(0, B1_SCHEDULE_CATCHUP_MINUTES) * 60
    stale_seconds = max(60, B1_SCHEDULE_STALE_SECONDS)
    due_slots: list[tuple[datetime, str]] = []
    for hhmm in B1_SCHEDULE_TIMES:
        slot_dt = _b1_schedule_slot_datetime(now, hhmm)
        if not slot_dt:
            continue
        age_seconds = (now - slot_dt).total_seconds()
        if 0 <= age_seconds <= catchup_seconds:
            due_slots.append((slot_dt, slot_dt.strftime("%Y-%m-%d %H:%M")))
    if not due_slots:
        return None

    now_float = time.time()
    today_prefix = now.strftime("%Y-%m-%d ")
    with B1_SCHEDULE_LOCK:
        state = _load_b1_schedule_state_unlocked()
        slots = state.setdefault("slots", {})
        for key in list(slots.keys()):
            if not key.startswith(today_prefix):
                slots.pop(key, None)
        B1_SCHEDULE_RUN_KEYS.intersection_update(key for key in B1_SCHEDULE_RUN_KEYS if key.startswith(today_prefix))

        eligible: list[tuple[datetime, str]] = []
        for slot_dt, slot_key in sorted(due_slots):
            slot = slots.get(slot_key) or {}
            status = str(slot.get("status") or "")
            if status in {"ok", "skipped"}:
                continue
            started_ts = float(slot.get("started_ts") or 0)
            finished_ts = float(slot.get("finished_ts") or 0)
            if status == "running" and now_float - started_ts < stale_seconds:
                continue
            if status == "error" and now_float - finished_ts < stale_seconds:
                continue
            if status == "running" and now_float - started_ts >= stale_seconds:
                B1_SCHEDULE_RUN_KEYS.discard(slot_key)
            if slot_key in B1_SCHEDULE_RUN_KEYS:
                continue
            eligible.append((slot_dt, slot_key))

        if not eligible:
            state["slots"] = slots
            _save_b1_schedule_state_unlocked(state)
            return None

        selected_dt, selected_key = eligible[-1]
        now_text = _b1_schedule_now_text()
        for _slot_dt, skipped_key in eligible[:-1]:
            skipped = slots.setdefault(skipped_key, {"scheduled_at": skipped_key})
            skipped.update({
                "status": "skipped",
                "reason": f"later_schedule_slot_claimed:{selected_key}",
                "updated_at": now_text,
                "finished_at": now_text,
                "finished_ts": now_float,
            })
        selected_slot = {**(slots.get(selected_key) or {})}
        selected_slot.pop("error", None)
        slots[selected_key] = {
            **selected_slot,
            "scheduled_at": selected_key,
            "status": "running",
            "started_at": now_text,
            "started_ts": now_float,
            "updated_at": now_text,
            "pid": os.getpid(),
            "lag_seconds": round((now - selected_dt).total_seconds(), 1),
        }
        B1_SCHEDULE_RUN_KEYS.add(selected_key)
        state["slots"] = slots
        _save_b1_schedule_state_unlocked(state)
        return selected_key


def run_scheduled_b1_scan(slot_key: str) -> None:
    try:
        if b1_cache_generated_for_slot(slot_key):
            _mark_b1_schedule_slot(slot_key, "ok", reason="cache_already_generated_for_slot")
            return
        lag_seconds = _b1_schedule_slot_lag_seconds(slot_key)
        run_kind = "catchup" if lag_seconds >= 60 else "scheduled"
        _mark_b1_schedule_slot(slot_key, "running", lag_seconds=round(lag_seconds, 1), run_kind=run_kind)
        print(f"[B1 schedule] trigger {slot_key} kind={run_kind} lag={lag_seconds:.0f}s", flush=True)
        cache = trigger_b1_scan(
            force=True,
            decision_mode="sync",
            schedule_slot=slot_key,
            schedule_run_kind=run_kind,
        )
        with API_RESPONSE_LOCK:
            API_RESPONSE_CACHE.pop("b1_screen", None)
        if cache.get("error"):
            _mark_b1_schedule_slot(slot_key, "error", error=str(cache.get("error") or "")[:500])
            print(f"[B1 schedule] {slot_key} failed: {cache.get('error')}", flush=True)
        else:
            _mark_b1_schedule_slot(
                slot_key,
                "ok",
                count=int(cache.get("count") or 0),
                generated_at=cache.get("generated_at") or "",
                run_kind=run_kind,
            )
            print(f"[B1 schedule] {slot_key} done: {cache.get('count', 0)} candidates", flush=True)
    except Exception as exc:
        _mark_b1_schedule_slot(slot_key, "error", error=f"{type(exc).__name__}: {exc}")
        print(f"[B1 schedule] {slot_key} error: {type(exc).__name__}: {exc}", flush=True)


def b1_schedule_loop() -> None:
    while True:
        slot_key = claim_due_b1_schedule_slot()
        if slot_key:
            threading.Thread(target=run_scheduled_b1_scan, args=(slot_key,), name="b1-scheduled-scan", daemon=True).start()
        time.sleep(15)


def pending_decision_loop() -> None:
    while True:
        try:
            trader = get_trader_module()
            if hasattr(trader, "execute_due_pending_decisions"):
                result = trader.execute_due_pending_decisions()
                if result.get("attempted"):
                    print(
                        f"[practice pending] attempted={result.get('attempted')} "
                        f"executed={len(result.get('executed') or [])}",
                        flush=True,
                    )
                    with API_RESPONSE_LOCK:
                        API_RESPONSE_CACHE.pop("niuniu_practice", None)
                        API_RESPONSE_CACHE.pop("practice_benchmarks", None)
        except Exception as exc:
            print(f"[WARN] 延迟成交检查失败: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(max(1.0, PENDING_DECISION_POLL_SECONDS))


def start_pending_decision_executor() -> None:
    global PENDING_DECISION_THREAD
    if PENDING_DECISION_THREAD and PENDING_DECISION_THREAD.is_alive():
        return
    PENDING_DECISION_THREAD = threading.Thread(target=pending_decision_loop, name="practice-pending-decision", daemon=True)
    PENDING_DECISION_THREAD.start()
    print(f"Practice pending decision executor enabled: {PENDING_DECISION_POLL_SECONDS:g}s", flush=True)


def start_b1_scheduler() -> None:
    global B1_SCHEDULE_THREAD
    if not B1_SCHEDULE_ENABLED or not B1_SCHEDULE_TIMES:
        return
    if B1_SCHEDULE_THREAD and B1_SCHEDULE_THREAD.is_alive():
        return
    B1_SCHEDULE_THREAD = threading.Thread(target=b1_schedule_loop, name="b1-scheduler", daemon=True)
    B1_SCHEDULE_THREAD.start()
    print(f"B1 schedule enabled: {', '.join(B1_SCHEDULE_TIMES)}", flush=True)


def trade_minute_from_hhmm(hhmm: str) -> int | None:
    try:
        hour = int(hhmm[:2]); minute = int(hhmm[2:4])
    except Exception:
        return None
    minutes = hour * 60 + minute
    am_start, am_end, pm_start, pm_end = 9 * 60 + 30, 11 * 60 + 30, 13 * 60, 15 * 60
    if minutes < am_start or minutes > pm_end or (am_end < minutes < pm_start):
        return None
    if minutes <= am_end:
        return minutes - am_start
    return 120 + (minutes - pm_start)


def fetch_benchmark_one(symbol: str, name: str) -> dict[str, Any]:
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={symbol}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    rows = (((data.get("data") or {}).get(symbol) or {}).get("data") or {}).get("data") or []
    points = []
    base = None
    for row in rows:
        parts = str(row).split()
        if len(parts) < 2:
            continue
        minute = trade_minute_from_hhmm(parts[0])
        if minute is None:
            continue
        try:
            price = float(parts[1])
        except ValueError:
            continue
        if base is None and price > 0:
            base = price
        if base:
            points.append({"time": parts[0], "minute": minute, "price": price, "pct": round((price / base - 1) * 100, 4)})
    return {"symbol": symbol, "name": name, "base": base, "points": points, "count": len(points)}


def get_practice_benchmarks() -> dict[str, Any]:
    now = time.time()
    if BENCHMARK_CACHE.get("data") and now - float(BENCHMARK_CACHE.get("ts") or 0) < BENCHMARK_TTL_SECONDS:
        return BENCHMARK_CACHE["data"]
    try:
        defs = [("sh000001", "上证指数"), ("sh000300", "沪深300"), ("sz399006", "创业板指"), ("sh000688", "科创50")]
        with ThreadPoolExecutor(max_workers=4) as pool:
            items = list(pool.map(lambda item: fetch_benchmark_one(item[0], item[1]), defs))
        data = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "items": items, "error": ""}
    except Exception as exc:
        data = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "items": [], "error": f"{type(exc).__name__}: {exc}"}
    BENCHMARK_CACHE["ts"] = now
    BENCHMARK_CACHE["data"] = data
    return data


def fmt_ts(ts: float | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

CATEGORIES = {"us_ratings": "美股机构买入评级", "x_monitor": "推特监控",
              "market_monitor": "盘面监控", "other": "其他"}

def merge_records_from_db(limit: int | None = None, category: str | None = None, offset: int = 0) -> dict[str, Any]:
    data = push_history.query_messages(limit=limit, category=category, offset=offset)
    records = data["records"]
    label_map = CATEGORIES
    categories = {key: {"label": label, "count": int(data["categories"].get(key, 0))}
                  for key, label in label_map.items()}
    return {"generated_at": fmt_ts(time.time()), "since": None, "dashboard_home": str(DASHBOARD_HOME),
            "storage": "sqlite", "db_path": str(push_history.DB_PATH),
            "count": len(records), "total": data["total"], "platforms": data["platforms"],
            "chats": data["chats"], "categories": categories, "records": records}


def cache_get_json(cache_key: str, ttl: int, producer) -> tuple[bytes, bool]:
    now = time.time()
    with API_RESPONSE_LOCK:
        cached = API_RESPONSE_CACHE.get(cache_key)
        if cached and now - float(cached.get("ts") or 0) < ttl:
            return cached["payload"], True
        key_lock = API_CACHE_KEY_LOCKS.setdefault(cache_key, threading.Lock())
    with key_lock:
        now = time.time()
        with API_RESPONSE_LOCK:
            cached = API_RESPONSE_CACHE.get(cache_key)
            if cached and now - float(cached.get("ts") or 0) < ttl:
                return cached["payload"], True
        result = producer()
        payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
        with API_RESPONSE_LOCK:
            API_RESPONSE_CACHE[cache_key] = {"ts": time.time(), "payload": payload}
            if len(API_RESPONSE_CACHE) > API_CACHE_MAX_ENTRIES:
                oldest = sorted(API_RESPONSE_CACHE.items(), key=lambda item: float(item[1].get("ts") or 0))
                for old_key, _ in oldest[:max(1, len(API_RESPONSE_CACHE) - API_CACHE_MAX_ENTRIES)]:
                    API_RESPONSE_CACHE.pop(old_key, None)
                    API_CACHE_KEY_LOCKS.pop(old_key, None)
        return payload, False


def cached_json_data(cache_key: str, ttl: int, producer, fallback: dict[str, Any]) -> dict[str, Any]:
    payload, _ = cache_get_json(cache_key, ttl, producer)
    try:
        data = json.loads(payload.decode("utf-8", "ignore"))
        return data if isinstance(data, dict) else dict(fallback)
    except Exception as exc:
        return {**fallback, "error": str(exc)}


def produce_us_market_summary_data() -> dict[str, Any]:
    archived = load_cached_summary_for_today()
    if archived:
        return archived
    indices_payload = cached_json_data("indices", API_TTLS["indices"], produce_indices_data, {"items": []})
    try:
        sector_payload = fetch_us_sector_snapshot()
    except Exception as exc:
        sector_payload = {"items": [], "error": f"{type(exc).__name__}: {exc}"}
    return fetch_us_market_summary(
        prefer_archive=False,
        use_model=False,
        indices_payload=indices_payload,
        sector_payload=sector_payload,
    )


def produce_us_sector_data() -> dict[str, Any]:
    try:
        return fetch_us_sector_snapshot()
    except Exception as exc:
        return {"items": [], "error": f"{type(exc).__name__}: {exc}"}


def is_allowed_x_media_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme != "https" or parsed.netloc.lower() != "pbs.twimg.com":
        return False
    return bool(re.match(r"^/(?:media|ext_tw_video_thumb|tweet_video_thumb)/", parsed.path))


def fetch_x_media(url: str) -> tuple[bytes, str]:
    if not is_allowed_x_media_url(url):
        raise ValueError("unsupported_media_url")
    now = time.time()
    with X_MEDIA_CACHE_LOCK:
        cached = X_MEDIA_CACHE.get(url)
        if cached and now - float(cached.get("ts") or 0) < X_MEDIA_CACHE_TTL_SECONDS:
            return cached["body"], cached["content_type"]
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": "https://x.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        content_type = (resp.headers.get("Content-Type") or "application/octet-stream").split(";", 1)[0].strip().lower()
        if content_type not in X_MEDIA_ALLOWED_CONTENT_TYPES:
            raise ValueError("upstream_not_image")
        body = resp.read(X_MEDIA_MAX_BYTES + 1)
    if len(body) > X_MEDIA_MAX_BYTES:
        raise ValueError("media_too_large")
    with X_MEDIA_CACHE_LOCK:
        X_MEDIA_CACHE[url] = {"ts": time.time(), "body": body, "content_type": content_type}
        if len(X_MEDIA_CACHE) > X_MEDIA_CACHE_MAX_ENTRIES:
            oldest = sorted(X_MEDIA_CACHE.items(), key=lambda item: float(item[1].get("ts") or 0))
            for old_key, _ in oldest[:max(1, len(X_MEDIA_CACHE) - X_MEDIA_CACHE_MAX_ENTRIES)]:
                X_MEDIA_CACHE.pop(old_key, None)
    return body, content_type


def sanitize_symbols(raw_symbols: str) -> list[str]:
    raw_symbols = (raw_symbols or "")[:800]
    symbols = []
    for item in raw_symbols.split(","):
        symbol = item.strip().upper()
        if symbol and re.fullmatch(r"[A-Z0-9.-]{1,12}", symbol):
            symbols.append(symbol)
        if len(symbols) >= 80:
            break
    return symbols


class RequestTooLarge(ValueError):
    pass


def is_truthy_header(value: str | None) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on", "https"})


def _parse_ip_network(value: str) -> ipaddress._BaseNetwork | None:
    try:
        return ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        return None


def is_trusted_proxy_ip(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(ip_text or "").strip())
    except ValueError:
        return False
    networks = [_parse_ip_network(item) for item in TRUSTED_PROXY_CIDRS]
    return any(network is not None and ip in network for network in networks)


def first_forwarded_ip(*headers: str | None) -> str:
    for header in headers:
        for part in str(header or "").split(","):
            candidate = part.strip()
            if not candidate:
                continue
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                continue
            return candidate
    return ""


def clamp_limit(raw: str | None, default: int = API_DEFAULT_LIMIT) -> int:
    try:
        value = int(raw) if raw else default
    except (TypeError, ValueError):
        value = default
    if value == 0:
        return 0
    return max(1, min(API_LIMIT_MAX, value))

def clamp_offset(raw: str | None) -> int:
    try:
        value = int(raw) if raw else 0
    except (TypeError, ValueError):
        value = 0
    return max(0, min(API_OFFSET_MAX, value))


def is_secret_config_key(key: str) -> bool:
    return bool(SECRET_KEY_RE.search(str(key or "")))


def display_secret(value: Any) -> str:
    return "已设置，留空保持不变" if str(value or "") else "未设置"


def parse_env_file(path: Path | None = None) -> dict[str, str]:
    path = path or DASHBOARD_ENV_FILE
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        raw_value = raw_value.strip()
        try:
            parsed = shlex.split(raw_value, posix=True)
            values[key] = parsed[0] if parsed else ""
        except ValueError:
            values[key] = raw_value.strip("\"'")
    return values


def us_features_enabled(env_values: dict[str, str] | None = None) -> bool:
    values = env_values if env_values is not None else parse_env_file()
    raw = values.get("DASHBOARD_US_FEATURES_ENABLED") or os.environ.get("DASHBOARD_US_FEATURES_ENABLED") or "0"
    return str(raw).strip().lower() in TRUTHY_VALUES


def admin_visible_env_names(env_values: dict[str, str] | None = None) -> list[str]:
    return list(ADMIN_VISIBLE_ENV_NAMES)


def quote_env_value(value: str) -> str:
    value = str(value or "")
    if value and re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def normalize_context_length_update(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    compact = raw.replace(",", "").replace("_", "").strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", compact)
    if not match:
        raise ValueError("上下文长度请填写 token 数，例如 128K、1M 或 1000000")
    number = float(match.group(1))
    unit = match.group(2).lower()
    multiplier = 1_000_000 if unit == "m" else 1_000 if unit == "k" else 1
    normalized = int(number * multiplier)
    if normalized <= 0:
        raise ValueError("上下文长度必须大于 0")
    return str(normalized)


def normalize_env_update(name: str, value: str, kind: str) -> str:
    value = str(value or "").strip()
    if kind == "bool":
        return "1" if value.lower() in {"1", "true", "yes", "on"} else "0"
    if kind == "int" and value:
        int(value)
    if kind in {"max_tokens", "context_length"}:
        return normalize_context_length_update(value)
    if kind == "time":
        normalized = normalize_hhmm(value)
        if value and not normalized:
            raise ValueError(f"{ENV_CONFIG_BY_NAME.get(name, {}).get('label', name)} 请使用北京时间 HH:MM，例如 14:45")
        return normalized
    if kind == "time_list":
        return normalize_time_list_update(value)
    if kind == "handle_list":
        return normalize_handle_list_update(value)
    if kind in {"strategy_multi", "strategy_single"}:
        return normalize_strategy_list_update(value)
    if kind == "strategy_source":
        return normalize_strategy_source_update(value)
    if kind == "preset_strategy_text":
        return normalize_preset_strategy_text_update(value)
    if kind == "trade_discipline_text":
        return normalize_trade_discipline_text_update(value)
    return value


def write_env_file_values(updates: dict[str, str], path: Path | None = None) -> dict[str, Any]:
    path = path or DASHBOARD_ENV_FILE
    existing = parse_env_file(path)
    next_values = dict(existing)
    changed_names: list[str] = []
    for name, value in updates.items():
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
            raise ValueError(f"invalid env name: {name}")
        schema = ENV_CONFIG_BY_NAME.get(name, {"kind": "text"})
        kind = "secret" if schema.get("kind") == "secret" or is_secret_config_key(name) else schema.get("kind", "text")
        if kind == "secret" and value == "":
            continue
        if value == "" and name not in existing and kind not in {"time_list", "strategy_multi", "strategy_single"}:
            continue
        next_value = normalize_env_update(name, value, kind)
        if existing.get(name) != next_value:
            changed_names.append(name)
        next_values[name] = next_value
    if not changed_names:
        return {
            "ok": True,
            "path": str(path),
            "count": len(updates),
            "changed": False,
            "changed_count": 0,
            "changed_names": [],
        }
    schema_names = [item["name"] for item in ENV_CONFIG_SCHEMA]
    ordered_names = [name for name in schema_names if name in next_values]
    ordered_names.extend(sorted(name for name in next_values if name not in set(ordered_names)))
    lines = [
        "# Managed by NiuOne dashboard admin.",
        "# Business settings are reloaded by NiuOne at runtime when possible.",
    ]
    for name in ordered_names:
        lines.append(f"{name}={quote_env_value(next_values.get(name, ''))}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return {
        "ok": True,
        "path": str(path),
        "count": len(updates),
        "changed": True,
        "changed_count": len(changed_names),
        "changed_names": changed_names,
    }


def schedule_niuone_services_restart() -> dict[str, Any]:
    if os.environ.get("NIUONE_DISABLE_AUTO_RESTART", "").lower() in {"1", "true", "yes", "on"}:
        return {"ok": False, "disabled": True}
    domain = f"gui/{os.getuid()}"
    targets = [f"{domain}/{label}" for label in NIUONE_LAUNCHD_LABELS]
    delay = max(0.2, NIUONE_RESTART_DELAY_SECONDS)
    quoted_targets = " ".join(shlex.quote(target) for target in targets)
    command = (
        f"sleep {delay}; "
        f"for target in {quoted_targets}; do "
        "/bin/launchctl kickstart -k \"$target\" >/dev/null 2>&1 || true; "
        "done"
    )
    try:
        subprocess.Popen(
            ["/bin/sh", "-c", command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "labels": list(NIUONE_LAUNCHD_LABELS)}
    return {"ok": True, "labels": list(NIUONE_LAUNCHD_LABELS), "delay_seconds": delay}


def load_yaml_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    if yaml is None:
        raise RuntimeError("PyYAML is required to edit config.yaml")
    loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {"value": loaded}


def redact_yaml_secrets(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: redact_yaml_secrets(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_yaml_secrets(item, key) for item in value]
    if is_secret_config_key(key) and str(value or ""):
        return SECRET_PLACEHOLDER
    return value


def restore_yaml_secret_placeholders(new_value: Any, old_value: Any, key: str = "") -> Any:
    if is_secret_config_key(key) and new_value == SECRET_PLACEHOLDER:
        return old_value
    if isinstance(new_value, dict):
        old_dict = old_value if isinstance(old_value, dict) else {}
        return {k: restore_yaml_secret_placeholders(v, old_dict.get(k), str(k)) for k, v in new_value.items()}
    if isinstance(new_value, list):
        old_list = old_value if isinstance(old_value, list) else []
        return [
            restore_yaml_secret_placeholders(item, old_list[idx] if idx < len(old_list) else None, key)
            for idx, item in enumerate(new_value)
        ]
    return new_value


def redacted_yaml_text() -> str:
    if yaml is None:
        return "# PyYAML unavailable\n"
    cfg = load_yaml_config()
    redacted = redact_yaml_secrets(cfg)
    return yaml.safe_dump(redacted, allow_unicode=True, sort_keys=False)


def write_yaml_config(raw_text: str) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required to edit config.yaml")
    old_cfg = load_yaml_config()
    new_cfg = yaml.safe_load(raw_text or "{}")
    if new_cfg is None:
        new_cfg = {}
    if not isinstance(new_cfg, (dict, list)):
        raise ValueError("config.yaml must contain a mapping or list")
    restored = restore_yaml_secret_placeholders(new_cfg, old_cfg)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        backup = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + f".bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        backup.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    CONFIG_PATH.write_text(yaml.safe_dump(restored, allow_unicode=True, sort_keys=False), encoding="utf-8")
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass
    return {"ok": True, "path": str(CONFIG_PATH)}


CRON_CONFIG_NAMES = {
    "DASHBOARD_US_MARKET_SUMMARY_CRON",
    "DASHBOARD_MARKET_AUCTION_CRON",
    "DASHBOARD_MARKET_MIDDAY_CRON",
    "DASHBOARD_MARKET_CLOSE_CRON",
    "DASHBOARD_US_RATING_CRON",
}
CRON_TIME_CONFIGS = {
    "DASHBOARD_US_MARKET_SUMMARY_CRON": {"day_label": "A股交易日"},
    "DASHBOARD_MARKET_AUCTION_CRON": {"day_label": "周一至周五"},
    "DASHBOARD_MARKET_MIDDAY_CRON": {"day_label": "周一至周五"},
    "DASHBOARD_MARKET_CLOSE_CRON": {"day_label": "周一至周五"},
    "DASHBOARD_US_RATING_CRON": {"day_label": "每天"},
}
ADMIN_GROUP_NOTES = {
    "牛牛美股": "集中管理 X/推文监控、美股买入评级和隔夜美股盘面总结使用的 Grok 配置。长度默认：上下文 128000 tokens，最大输出 4096 tokens；关闭时隐藏 X/评级相关设置，隔夜美股总结仍会读取已配置的 Grok 参数。",
    "消息面预检模型": "用于 A 股候选股最近 3 天消息面预检；需兼容 /chat/completions，且模型或网关应具备实时搜索能力。长度默认：上下文 128000 tokens，最大输出 4096 tokens。模型和密钥留空则跳过。",
    "买卖决策模型": "推荐使用 deepseek-v4-pro；也可填写其他兼容 /chat/completions 的模型服务。长度默认：上下文 128000 tokens，最大输出 4096 tokens。",
    "选股及买卖决策时间点": "使用北京时间 HH:MM，可设置多个时间点。",
    "选股策略": "在内置策略和预设文字策略中选择一个激活；内置策略可选基础策略、Z哥或李大霄。",
    "盘面监控生产时间点": "直接填写北京时间 HH:MM；隔夜美股总结默认交易日 08:00 生成，A 股盘面监控在交易时段触发；长度默认：上下文 128000 tokens，最大输出 4096 tokens。",
    "指数行情更新周期": "单位为秒，保存后立即用于后续行情请求。",
}
US_FEATURE_GATED_GROUPS = {
    "X 监控",
}
US_FEATURE_GATED_NAMES = {
    "US_RATING_BASE_URL",
    "US_RATING_API_KEY",
    "US_RATING_CONTEXT_LENGTH",
    "US_RATING_MAX_TOKENS",
    "DASHBOARD_GROK_MODEL",
    "DASHBOARD_GROK_CONTEXT_LENGTH",
    "DASHBOARD_GROK_MAX_TOKENS",
    "DASHBOARD_GROK_BASE_URL",
    "DASHBOARD_GROK_API_KEY",
    "X_WATCHLIST_ACCOUNTS",
    "X_WATCHLIST_MAX_TOKENS",
    "X_WATCHLIST_DAEMON_INTERVAL_SECONDS",
    "DASHBOARD_US_RATING_CRON",
    "US_RATING_DEADLINE_SECONDS",
    "US_RATING_REQUEST_TIMEOUT_SECONDS",
}


def validate_cron_expr(expr: str) -> None:
    expr = str(expr or "").strip()
    if not expr:
        return
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron 表达式需要 5 段: {expr}")
    allowed = re.compile(r"^[0-9*/,\-]+$")
    for part in parts:
        if not allowed.fullmatch(part):
            raise ValueError(f"cron 表达式包含不支持的字符: {expr}")


def cron_expr_to_hhmm(expr: str) -> str:
    parts = str(expr or "").strip().split()
    if len(parts) != 5:
        return normalize_hhmm(parts[0]) if len(parts) == 1 else ""
    minute, hour = parts[0], parts[1]
    if not (minute.isdigit() and hour.isdigit()):
        return ""
    return f"{int(hour):02d}:{int(minute):02d}"


def normalize_hhmm(value: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"\d{1,2}:\d{2}", value):
        return ""
    hour, minute = [int(x) for x in value.split(":", 1)]
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"


def split_hhmm_values(value: str) -> list[str]:
    values: list[str] = []
    for raw in re.split(r"[,，\s]+", str(value or "")):
        raw = raw.strip()
        if not raw:
            continue
        values.append(normalize_hhmm(raw) or raw)
    return values


def normalize_x_handle(value: str) -> str:
    handle = str(value or "").strip().lstrip("@").lower()
    if not handle:
        return ""
    if not re.fullmatch(r"[a-z0-9_]{1,15}", handle):
        return ""
    return handle


def split_handle_values(value: str) -> list[str]:
    handles: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,，;\s]+", str(value or "")):
        handle = normalize_x_handle(raw)
        if not handle or handle in seen:
            continue
        seen.add(handle)
        handles.append(handle)
    return handles


def normalize_handle_list_update(value: str) -> str:
    handles = split_handle_values(value)
    if not handles and str(value or "").strip():
        raise ValueError("推文监控作者请使用 X handle，例如 wallstreet0name")
    return ",".join(handles)


def friendly_handle_list_text(value: str) -> str:
    return "、".join(split_handle_values(value))


def split_strategy_values(value: str) -> list[str]:
    normalized = normalize_strategy_list_update(value)
    return [item for item in normalized.split(",") if item]


def friendly_strategy_list_text(value: str) -> str:
    labels = {str(item["id"]): str(item["label"]) for item in strategy_settings_options(family="persona")}
    return "、".join(labels.get(strategy_id, strategy_id) for strategy_id in split_strategy_values(value))


def friendly_strategy_source_text(value: str) -> str:
    normalized = normalize_strategy_source_update(value)
    labels = {str(item["id"]): str(item["label"]) for item in STRATEGY_SOURCE_OPTIONS}
    return labels.get(normalized, normalized)


def x_watchlist_state_accounts(path: Path | None = None) -> list[str]:
    if path is None:
        path = Path(os.environ.get("DASHBOARD_X_WATCHLIST_STATE") or str(CRON_STATE_DIR / "x_watchlist_latest.json")).expanduser()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(state, dict):
        return []
    handles: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        handle = normalize_x_handle(str(value or ""))
        if handle and handle not in seen:
            seen.add(handle)
            handles.append(handle)

    for key in ("latest", "seen_ids"):
        section = state.get(key)
        if isinstance(section, dict):
            for handle in section:
                add(handle)
    sent_missing = state.get("sent_missing_context")
    if isinstance(sent_missing, list):
        for item in sent_missing:
            if isinstance(item, dict):
                add(item.get("handle"))
    return handles


def normalize_time_list_update(value: str) -> str:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,，\s]+", str(value or "")):
        raw = raw.strip()
        if not raw:
            continue
        item = normalize_hhmm(raw)
        if not item:
            raise ValueError(f"时间点请使用北京时间 HH:MM，例如 09:25")
        if item not in seen:
            seen.add(item)
            normalized.append(item)
    return ",".join(normalized)


def friendly_time_list_text(value: str) -> str:
    return "、".join(split_hhmm_values(value))


def normalize_cron_update(name: str, value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw.split()) == 5:
        validate_cron_expr(raw)
        return raw
    hhmm = normalize_hhmm(raw)
    if not hhmm:
        raise ValueError(f"{ENV_CONFIG_BY_NAME.get(name, {}).get('label', name)} 请使用北京时间 HH:MM，例如 09:25")
    hour, minute = [int(x) for x in hhmm.split(":", 1)]
    default_expr = str(ENV_CONFIG_BY_NAME.get(name, {}).get("default") or "* * * * *")
    default_parts = default_expr.split()
    day, month, dow = default_parts[2:5] if len(default_parts) == 5 else ("*", "*", "*")
    return f"{minute} {hour} {day} {month} {dow}"


def normalize_business_updates(updates: dict[str, str]) -> dict[str, str]:
    normalized = dict(updates)
    for name in list(normalized):
        if name in CRON_CONFIG_NAMES:
            normalized[name] = normalize_cron_update(name, normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "time_list":
            normalized[name] = normalize_time_list_update(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "time":
            normalized[name] = normalize_env_update(name, normalized[name], "time")
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "handle_list":
            normalized[name] = normalize_handle_list_update(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") in {"strategy_multi", "strategy_single"}:
            normalized[name] = normalize_strategy_list_update(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "strategy_source":
            normalized[name] = normalize_strategy_source_update(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "preset_strategy_text":
            normalized[name] = normalize_preset_strategy_text_update(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "trade_discipline_text":
            normalized[name] = normalize_trade_discipline_text_update(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") in {"max_tokens", "context_length"}:
            normalized[name] = normalize_context_length_update(normalized[name])
    return normalized


def friendly_cron_text(name: str, expr: str) -> str:
    hhmm = cron_expr_to_hhmm(expr)
    if not hhmm:
        return str(expr or "")
    day_label = CRON_TIME_CONFIGS.get(name, {}).get("day_label", "")
    return f"北京时间 {hhmm}" + (f" · {day_label}" if day_label else "")


def validate_hhmm_list(value: str) -> None:
    value = str(value or "").strip()
    if not value:
        return
    for item in [x.strip() for x in value.split(",") if x.strip()]:
        if not re.fullmatch(r"\d{2}:\d{2}", item):
            raise ValueError(f"时间点需使用 HH:MM，并用英文逗号分隔: {item}")
        hour, minute = [int(x) for x in item.split(":", 1)]
        if hour > 23 or minute > 59:
            raise ValueError(f"时间点超出范围: {item}")


def validate_business_updates(updates: dict[str, str]) -> None:
    for name, value in updates.items():
        if name in CRON_CONFIG_NAMES:
            validate_cron_expr(normalize_cron_update(name, value))
        elif name == "DASHBOARD_B1_SCHEDULE_TIMES":
            normalize_time_list_update(value)
        elif name in {"DASHBOARD_B3_EXIT_TIME", "DASHBOARD_TIME_EXIT_TIME", "DASHBOARD_TIME_STOP_EXIT_TIME"}:
            normalize_env_update(name, value, "time")
        elif name == "X_WATCHLIST_ACCOUNTS":
            normalize_handle_list_update(value)
        elif name == STRATEGY_SOURCE_ENV:
            normalize_strategy_source_update(value)
        elif name == PERSONA_STRATEGY_ENV:
            normalize_strategy_list_update(value)
        elif name == PRESET_STRATEGY_TEXT_ENV:
            normalize_preset_strategy_text_update(value)
        elif name == TRADE_DISCIPLINE_TEXT_ENV:
            normalize_trade_discipline_text_update(value)
        elif name in {
            "X_WATCHLIST_DAEMON_INTERVAL_SECONDS",
            "DASHBOARD_INDICES_TTL_SECONDS",
            "DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS",
            "DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS",
            "DASHBOARD_MAX_OPEN_POSITIONS",
            "DASHBOARD_MORNING_MAX_OPEN_POSITIONS",
        } and str(value or "").strip():
            if int(value) <= 0:
                raise ValueError(f"{name} 必须大于 0")
        elif name == "DASHBOARD_MAX_NEW_BUYS_PER_DECISION" and str(value or "").strip():
            if int(value) < 0:
                raise ValueError(f"{name} 必须大于等于 0")
        elif name in {
            "DASHBOARD_MAX_SINGLE_POSITION_PCT",
            "DASHBOARD_MAX_TOTAL_POSITION_PCT",
            "DASHBOARD_MIN_CASH_RESERVE_PCT",
        } and str(value or "").strip():
            if float(value) < 0:
                raise ValueError(f"{name} 必须大于等于 0")
        elif name == "DASHBOARD_CRON_MAX_ATTEMPTS" and str(value or "").strip():
            if int(value) < 1:
                raise ValueError(f"{name} 必须大于等于 1")
        elif name == "DASHBOARD_CRON_RETRY_DELAY_SECONDS" and str(value or "").strip():
            if int(value) < 0:
                raise ValueError(f"{name} 必须大于等于 0")
        elif name in {"US_RATING_DEADLINE_SECONDS", "US_RATING_REQUEST_TIMEOUT_SECONDS"} and str(value or "").strip():
            if int(value) <= 0:
                raise ValueError(f"{name} 必须大于 0")
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") in {"max_tokens", "context_length"}:
            normalize_context_length_update(value)


def sync_business_runtime_settings(changed: dict[str, str] | list[str] | set[str] | tuple[str, ...] | None) -> dict[str, Any]:
    global B1_CANDIDATE_REFRESH_LAST_TS, B1_SCHEDULE_TIMES, TRADER_MODULE, TRADER_MODULE_MTIME
    if isinstance(changed, dict):
        changed_names = set(changed.keys())
    else:
        changed_names = set(changed or [])
    env_values = parse_env_file()
    visible_names = admin_visible_env_names(env_values)
    for name in visible_names:
        if name in env_values:
            os.environ[name] = env_values[name]

    applied: list[str] = []
    if "DASHBOARD_B1_SCHEDULE_TIMES" in changed_names:
        B1_SCHEDULE_TIMES = tuple(split_hhmm_values(env_values.get("DASHBOARD_B1_SCHEDULE_TIMES", "")))
        applied.append("b1_schedule_times")
        start_b1_scheduler()

    if "DASHBOARD_INDICES_TTL_SECONDS" in changed_names:
        try:
            API_TTLS["indices"] = int(env_values.get("DASHBOARD_INDICES_TTL_SECONDS") or ENV_CONFIG_BY_NAME["DASHBOARD_INDICES_TTL_SECONDS"]["default"])
            with API_RESPONSE_LOCK:
                API_RESPONSE_CACHE.pop("indices", None)
            applied.append("indices_ttl")
        except (TypeError, ValueError):
            pass

    if changed_names & {STRATEGY_SOURCE_ENV, PERSONA_STRATEGY_ENV, PRESET_STRATEGY_TEXT_ENV}:
        B1_CANDIDATE_REFRESH_LAST_TS = 0.0
        with API_RESPONSE_LOCK:
            API_RESPONSE_CACHE.pop("b1_screen", None)
        applied.append("strategy_settings")
        if PERSONA_STRATEGY_ENV in changed_names:
            applied.append("persona_strategies")

    if changed_names & TRADER_RUNTIME_ENV_NAMES:
        TRADER_MODULE = None
        TRADER_MODULE_MTIME = 0.0
        with API_RESPONSE_LOCK:
            API_RESPONSE_CACHE.pop("niuniu_practice", None)
            API_RESPONSE_CACHE.pop("niuniu_practice_fast", None)
        applied.append("trader_runtime")

    if changed_names & set(visible_names):
        applied.append("env")

    return {"ok": True, "applied": sorted(set(applied)), "changed_names": sorted(changed_names)}


def crossdesk_provider_values() -> dict[str, str]:
    try:
        cfg = load_yaml_config()
    except Exception:
        return {}
    for provider in cfg.get("custom_providers", []) if isinstance(cfg.get("custom_providers"), list) else []:
        if not isinstance(provider, dict):
            continue
        if "crossdesk" in str(provider.get("name") or provider.get("base_url") or "").lower():
            return {
                "base_url": str(provider.get("base_url") or ""),
                "api_key": str(provider.get("api_key") or ""),
                "model": str(provider.get("model") or ""),
            }
    return {}


def business_config_fallback_value(name: str) -> tuple[str, str]:
    provider = crossdesk_provider_values()
    if name in {"DASHBOARD_GROK_BASE_URL", "DASHBOARD_DECISION_BASE_URL"}:
        return provider.get("base_url", ""), "config.yaml" if provider.get("base_url") else "default"
    if name in {"DASHBOARD_GROK_API_KEY", "DASHBOARD_DECISION_API_KEY"}:
        return provider.get("api_key", ""), "config.yaml" if provider.get("api_key") else "default"
    if name == "X_WATCHLIST_ACCOUNTS":
        handles = x_watchlist_state_accounts()
        return ",".join(handles), "x_watchlist_state" if handles else "default"
    return "", "default"


def build_admin_config_payload() -> dict[str, Any]:
    env_values = parse_env_file()
    visible_names = admin_visible_env_names(env_values)
    names = set(visible_names)
    items = []
    admin_order = {name: idx for idx, name in enumerate(visible_names)}
    for name in sorted(names, key=lambda n: admin_order.get(n, 999)):
        schema = ENV_CONFIG_BY_NAME.get(name, {"name": name, "label": name, "group": "其他", "kind": "text", "default": "", "effect": "restart"})
        fallback_value, fallback_source = business_config_fallback_value(name)
        default_value = schema.get("default", "")
        if name in os.environ:
            effective = os.environ.get(name, "")
        elif name in env_values:
            effective = env_values.get(name, "")
        else:
            effective = fallback_value or default_value
        secret = schema.get("kind") == "secret" or is_secret_config_key(name)
        file_value = env_values.get(name)
        if file_value is None:
            file_value = "" if secret else default_value
        source = "process env" if name in os.environ else ("dashboard.env" if name in env_values else fallback_source)
        item = {
            **schema,
            "secret": secret,
            "effective": display_secret(effective) if secret else effective,
            "file_value": "" if secret else file_value,
            "file_state": display_secret(env_values.get(name) or fallback_value or default_value) if secret else file_value,
            "source": source,
        }
        if name in CRON_TIME_CONFIGS and not secret:
            stored_file_value = str(file_value or "")
            item.update({
                "effective": friendly_cron_text(name, effective),
                "file_value": cron_expr_to_hhmm(stored_file_value) or normalize_hhmm(stored_file_value),
                "file_state": friendly_cron_text(name, env_values.get(name) or fallback_value or default_value),
                "default": friendly_cron_text(name, default_value),
                "day_label": CRON_TIME_CONFIGS[name]["day_label"],
            })
        if schema.get("kind") == "time_list" and not secret:
            item.update({
                "effective": friendly_time_list_text(effective),
                "file_value": normalize_time_list_update(str(file_value or "")),
                "file_state": friendly_time_list_text(env_values.get(name) or fallback_value or default_value),
                "default": friendly_time_list_text(default_value),
                "time_values": split_hhmm_values(str(file_value or "")),
            })
        if schema.get("kind") == "handle_list" and not secret:
            edit_value = str(file_value or "")
            if name not in env_values and name not in os.environ and fallback_value:
                edit_value = fallback_value
            state_value = env_values.get(name) if name in env_values else (fallback_value or default_value)
            item.update({
                "effective": friendly_handle_list_text(effective),
                "file_value": normalize_handle_list_update(edit_value),
                "file_state": friendly_handle_list_text(state_value),
                "default": friendly_handle_list_text(default_value),
                "handle_values": split_handle_values(edit_value),
            })
        if schema.get("kind") == "strategy_source" and not secret:
            edit_value = normalize_strategy_source_update(str(file_value or default_value))
            state_value = env_values.get(name) if name in env_values else (fallback_value or default_value)
            item.update({
                "effective": friendly_strategy_source_text(effective),
                "file_value": edit_value,
                "file_state": friendly_strategy_source_text(state_value),
                "default": friendly_strategy_source_text(default_value),
                "strategy_source_options": list(STRATEGY_SOURCE_OPTIONS),
            })
        if schema.get("kind") == "preset_strategy_text" and not secret:
            state_value = env_values.get(name) if name in env_values else (fallback_value or default_value)
            item.update({
                "effective": decode_preset_strategy_text(effective),
                "file_value": decode_preset_strategy_text(str(file_value or "")),
                "file_state": decode_preset_strategy_text(state_value),
                "default": decode_preset_strategy_text(default_value),
                "preset_strategy_max_chars": PRESET_STRATEGY_TEXT_MAX_CHARS,
            })
        if schema.get("kind") == "trade_discipline_text" and not secret:
            state_value = env_values.get(name) if name in env_values else (fallback_value or default_value)
            item.update({
                "effective": decode_trade_discipline_text(effective),
                "file_value": decode_trade_discipline_text(str(file_value or "")),
                "file_state": decode_trade_discipline_text(state_value),
                "default": decode_trade_discipline_text(default_value),
                "trade_discipline_max_chars": TRADE_DISCIPLINE_TEXT_MAX_CHARS,
            })
        if schema.get("kind") in {"strategy_multi", "strategy_single"} and not secret:
            edit_source = str(file_value or "")
            if name not in env_values and name not in os.environ and fallback_value:
                edit_source = fallback_value
            edit_value = normalize_strategy_list_update(edit_source)
            state_value = env_values.get(name) if name in env_values else (fallback_value or default_value)
            item.update({
                "effective": friendly_strategy_list_text(effective),
                "file_value": edit_value,
                "file_state": friendly_strategy_list_text(state_value),
                "default": friendly_strategy_list_text(default_value),
                "strategy_values": split_strategy_values(edit_value),
                "strategy_options": strategy_settings_options(family="persona"),
            })
        items.append(item)
    return {
        "items": items,
        "secret_placeholder": SECRET_PLACEHOLDER,
    }

INDICES_HTML = None

ADMIN_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>牛牛1号</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%90%AE%3C/text%3E%3C/svg%3E">
<style>
:root{color-scheme:dark;--bg:#07090d;--surface:#10151b;--surface2:#151b23;--line:#26313d;--line2:#334155;--text:#f3f6fb;--muted:#94a3b8;--soft:#cbd5e1;--accent:#2dd4bf;--blue:#60a5fa;--red:#fb7185;--green:#34d399;--yellow:#fbbf24}*{box-sizing:border-box}[hidden]{display:none!important}html{scroll-behavior:smooth}body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:linear-gradient(180deg,#0b1016 0%,var(--bg) 48%,#050608 100%);color:var(--text);min-height:100vh}.admin-header{border-bottom:1px solid rgba(148,163,184,.16);background:rgba(7,9,13,.88);backdrop-filter:blur(16px);padding:22px clamp(16px,4vw,42px)}.admin-header-inner{max-width:1180px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.eyebrow{font-size:12px;font-weight:850;color:var(--accent);letter-spacing:.04em;margin-bottom:6px}h1{margin:0;font-size:30px;letter-spacing:0}h2{margin:0;font-size:18px;letter-spacing:0}p{margin:0}.muted{color:var(--muted)}.toplink{color:#dbeafe;text-decoration:none;border:1px solid rgba(148,163,184,.20);background:rgba(15,23,42,.62);border-radius:8px;padding:9px 12px;font-weight:850}.toplink:hover{border-color:rgba(96,165,250,.54);background:rgba(30,41,59,.72)}.admin-main{width:min(1180px,100%);margin:0 auto;padding:20px clamp(14px,4vw,42px) 34px;display:grid;gap:16px}.settings-form{display:grid;gap:14px}.settings-group{border:1px solid rgba(148,163,184,.16);border-radius:8px;background:rgba(16,21,27,.88);box-shadow:0 18px 56px rgba(0,0,0,.22);overflow:hidden}.settings-group-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:16px 18px;border-bottom:1px solid rgba(148,163,184,.12);background:rgba(21,27,35,.72)}.settings-group-note{color:var(--muted);font-size:13px;line-height:1.5;margin-top:5px}.settings-count{font-size:12px;color:#a7f3d0;border:1px solid rgba(45,212,191,.24);background:rgba(20,184,166,.10);border-radius:999px;padding:3px 8px;white-space:nowrap}.settings-list{display:grid}.setting-row{display:grid;grid-template-columns:minmax(170px,.72fr) minmax(250px,1fr) minmax(220px,.84fr);gap:16px;align-items:start;padding:16px 18px;border-top:1px solid rgba(148,163,184,.10)}.setting-row:first-child{border-top:0}.setting-copy{display:grid;gap:4px;min-width:0}.config-label{font-weight:850;color:#e5edf8;line-height:1.35}.setting-editor{min-width:0}.setting-editor input,.setting-editor select{width:100%;min-width:0}.setting-state{display:grid;gap:8px;min-width:0}.setting-state-item{display:grid;gap:3px}.setting-state-label{font-size:11px;color:#7b8aa0;font-weight:850}.config-meta{font-size:12px;color:#b6c2d2;max-width:100%;overflow-wrap:anywhere;line-height:1.45}.config-empty{color:#64748b}input,select,textarea,button{border:1px solid var(--line);background:#0b0f15;color:var(--text);border-radius:8px;padding:10px 12px;font:inherit;min-width:0}input:focus,select:focus,textarea:focus{outline:2px solid rgba(96,165,250,.70);outline-offset:1px;border-color:rgba(96,165,250,.62)}textarea{width:100%;min-height:460px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.45;resize:vertical}button{cursor:pointer;font-weight:850;background:linear-gradient(135deg,rgba(20,184,166,.92),rgba(96,165,250,.76));border:0;color:#061017}.save-button{min-height:42px;padding:10px 16px;justify-self:end;transition:transform .12s ease,filter .12s ease,background .12s ease}.save-button:disabled{cursor:wait;filter:saturate(.65);opacity:.82}.save-button.saved{background:linear-gradient(135deg,rgba(52,211,153,.95),rgba(45,212,191,.78))}.save-button.error{background:linear-gradient(135deg,rgba(251,113,133,.95),rgba(248,113,113,.76));color:#fff}.settings-actions{position:sticky;bottom:14px;z-index:3;display:flex;justify-content:flex-end;align-items:center;gap:10px;padding:10px;border:1px solid rgba(148,163,184,.18);border-radius:8px;background:rgba(8,11,16,.86);backdrop-filter:blur(14px);box-shadow:0 18px 54px rgba(0,0,0,.30)}.settings-save-status{min-height:20px;font-size:13px;line-height:1.4;color:var(--muted);text-align:right;overflow-wrap:anywhere}.settings-save-status.ok{color:#86efac}.settings-save-status.error{color:#fecdd3}.settings-save-status.busy{color:#bfdbfe}.time-list-control{display:grid;gap:8px}.time-list-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:6px}.time-list-item{display:grid;grid-template-columns:minmax(92px,1fr) 34px;gap:4px;align-items:center}.time-list-item input{min-width:0}.time-list-add,.time-list-remove{display:inline-grid;place-items:center;padding:0;border-radius:8px;border:1px solid rgba(148,163,184,.22);background:rgba(15,23,42,.78);color:#dbeafe}.time-list-add{width:38px;height:38px;justify-self:start}.time-list-remove{width:34px;height:38px;color:#fecdd3}.okmsg{border:1px solid rgba(52,211,153,.28);background:rgba(6,78,59,.20);color:#bbf7d0;border-radius:8px;padding:11px 13px}.errmsg{border:1px solid rgba(251,113,133,.34);background:rgba(127,29,29,.22);color:#fecdd3;border-radius:8px;padding:11px 13px}@media(max-width:940px){.setting-row{grid-template-columns:1fr;gap:10px}.setting-state{grid-template-columns:repeat(2,minmax(0,1fr))}.save-button{width:100%}.settings-actions{position:static;align-items:stretch;flex-direction:column}.settings-save-status{text-align:left}}@media(max-width:620px){.admin-header{padding:18px 14px}.admin-main{padding:16px 12px 26px}.settings-group-head,.setting-row{padding:14px}.setting-state{grid-template-columns:1fr}.time-list-grid{grid-template-columns:1fr}.toplink{width:100%;text-align:center}}</style>
<style>
.admin-header{position:sticky;top:0;z-index:8;padding:16px clamp(16px,4vw,42px);background:rgba(7,10,14,.92);box-shadow:0 12px 34px rgba(0,0,0,.22)}
.admin-header-inner{max-width:1320px}
.eyebrow{color:#7dd3fc}
h1{font-size:26px}
.admin-main{width:min(1320px,100%);gap:18px}
.settings-form{gap:18px}
.settings-overview{display:flex;justify-content:space-between;align-items:flex-end;gap:18px;padding:18px 20px;border:1px solid rgba(148,163,184,.18);border-radius:8px;background:linear-gradient(135deg,rgba(14,19,27,.94),rgba(13,25,29,.86));box-shadow:0 18px 58px rgba(0,0,0,.24)}
.settings-overview-copy{display:grid;gap:6px;min-width:0}
.settings-overview-copy h2{font-size:20px}
.settings-overview-copy .muted{font-size:13px;line-height:1.55;max-width:760px;overflow-wrap:anywhere}
.settings-overview-stats{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}
.settings-stat{display:grid;gap:2px;min-width:86px;padding:9px 11px;border:1px solid rgba(148,163,184,.14);border-radius:8px;background:rgba(2,6,12,.34)}
.settings-stat-value{font-size:18px;font-weight:900;color:#ecfeff;line-height:1}
.settings-stat-label{font-size:11px;font-weight:850;color:#8ea4bb}
.settings-shell{display:grid;grid-template-columns:210px minmax(0,1fr);gap:16px;align-items:start}
.settings-sidebar{position:sticky;top:88px;display:grid;gap:10px;min-width:0;padding:12px;border:1px solid rgba(148,163,184,.16);border-radius:8px;background:rgba(10,14,20,.78);backdrop-filter:blur(14px);box-shadow:0 14px 42px rgba(0,0,0,.18)}
.settings-nav-title{font-size:12px;font-weight:900;color:#e2e8f0;padding:0 4px 2px}
.settings-nav{display:grid;gap:5px;min-width:0;max-width:100%}
.settings-nav-link{display:grid;grid-template-columns:30px minmax(0,1fr) auto;align-items:center;gap:8px;min-height:36px;padding:7px 8px;border:1px solid transparent;border-radius:8px;color:#b9c6d7;text-decoration:none;font-size:13px}
.settings-nav-link:hover,.settings-nav-link:focus-visible{color:#f8fafc;border-color:rgba(125,211,252,.28);background:rgba(14,165,233,.10);outline:0}
.settings-nav-index{font-size:11px;font-weight:900;color:#67e8f9}
.settings-nav-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.settings-nav-count{font-size:11px;font-weight:850;color:#93a4b8}
.settings-content{display:grid;gap:14px;min-width:0}
.settings-group{scroll-margin-top:92px;background:rgba(12,17,24,.88);border-color:rgba(148,163,184,.18);box-shadow:0 16px 46px rgba(0,0,0,.20)}
.settings-group-head{padding:15px 18px;background:linear-gradient(180deg,rgba(23,31,42,.88),rgba(16,23,32,.78))}
.settings-count{border-radius:8px;color:#bae6fd;border-color:rgba(125,211,252,.22);background:rgba(14,165,233,.10)}
.setting-row{grid-template-columns:minmax(150px,.62fr) minmax(280px,1fr) minmax(190px,.66fr);gap:18px;padding:14px 18px;transition:background .12s ease,border-color .12s ease}
.setting-row:hover{background:rgba(148,163,184,.045)}
.config-label{font-size:14px}
.setting-editor{display:grid;gap:7px}
.setting-editor input,.setting-editor select,.setting-editor textarea{width:100%}
.setting-state{grid-template-columns:1fr;gap:7px}
.setting-state-item{padding:8px 10px;border:1px solid rgba(148,163,184,.10);border-radius:8px;background:rgba(2,6,12,.22)}
input,select,textarea{background:rgba(5,10,16,.96);border-color:rgba(100,116,139,.70)}
input:hover,select:hover,textarea:hover{border-color:rgba(125,211,252,.42)}
select{appearance:none;background-image:linear-gradient(45deg,transparent 50%,#93c5fd 50%),linear-gradient(135deg,#93c5fd 50%,transparent 50%);background-position:calc(100% - 17px) 50%,calc(100% - 12px) 50%;background-size:5px 5px,5px 5px;background-repeat:no-repeat;padding-right:34px}
.settings-actions{right:clamp(14px,4vw,42px);left:auto;margin-left:auto;max-width:540px;border-color:rgba(125,211,252,.22);background:rgba(7,11,17,.90)}
.settings-save-status{flex:1}
.save-button{box-shadow:0 10px 20px rgba(0,0,0,.22),0 1px 0 rgba(255,255,255,.20) inset;transition:transform .08s ease,filter .08s ease,background .12s ease,box-shadow .08s ease}
.save-button:hover:not(:disabled){filter:brightness(1.05);transform:translateY(-1px)}
.save-button:active,.save-button.pressed{transform:translateY(2px) scale(.985);filter:brightness(.88);box-shadow:0 3px 8px rgba(0,0,0,.28),0 2px 8px rgba(0,0,0,.30) inset}
.strategy-multi-control{display:grid;gap:8px}
.strategy-option{display:grid;grid-template-columns:18px minmax(0,1fr);gap:9px;align-items:start;border:1px solid rgba(148,163,184,.16);border-radius:8px;background:rgba(15,23,42,.54);padding:10px 11px;cursor:pointer;transition:border-color .12s ease,background .12s ease,transform .12s ease}
.strategy-option:hover{border-color:rgba(125,211,252,.32);background:rgba(30,41,59,.62)}
.strategy-option input{width:16px;min-width:16px;height:16px;margin:2px 0 0;padding:0;accent-color:var(--accent)}
.strategy-option-main{display:grid;gap:3px;min-width:0}
.strategy-option-title{display:flex;align-items:center;gap:7px;color:#e5edf8;font-weight:850;line-height:1.25}
.strategy-option-dot{width:8px;height:8px;border-radius:3px;background:var(--strategy-color,#94a3b8);box-shadow:0 0 12px var(--strategy-color,#94a3b8);flex:0 0 auto}
.strategy-option-desc{color:#94a3b8;font-size:12px;line-height:1.45}
.preset-strategy-textarea{min-height:168px;font-family:inherit;font-size:13px;line-height:1.55}
.time-list-grid{grid-template-columns:repeat(auto-fill,minmax(126px,1fr))}
.time-list-add,.time-list-remove{transition:border-color .12s ease,background .12s ease,color .12s ease}
.time-list-add:hover,.time-list-remove:hover{border-color:rgba(125,211,252,.38);background:rgba(30,41,59,.72)}
.okmsg,.errmsg{box-shadow:0 12px 34px rgba(0,0,0,.18)}
@media(max-width:1120px){.settings-shell{grid-template-columns:1fr}.settings-sidebar{position:static;top:auto}.settings-nav{display:flex;gap:6px;overflow-x:auto;padding-bottom:2px}.settings-nav-link{grid-template-columns:auto minmax(max-content,1fr) auto;flex:0 0 auto}.settings-actions{max-width:none}}
@media(max-width:940px){.settings-overview{align-items:stretch;flex-direction:column}.settings-overview-stats{justify-content:flex-start}.setting-row{grid-template-columns:1fr}.setting-state{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:620px){h1{font-size:24px}.settings-overview{padding:15px}.settings-overview-stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}.settings-stat{min-width:0}.settings-nav-link{min-height:34px}.settings-group-head{gap:10px}.settings-count{align-self:flex-start}.setting-state{grid-template-columns:1fr}.settings-actions{right:auto}}
</style>
</head><body><header class="admin-header"><div class="admin-header-inner"><div><div class="eyebrow">牛牛1号</div><h1>设置</h1></div><a class="toplink" href="/">返回首页</a></div></header>
<main class="admin-main">
__NOTICE__
__ENV_CONFIG__
</main>
<script>
function syncUsFeatureSettings() {
  var toggle = document.querySelector('[data-feature-toggle="us"]');
  var enabled = toggle && toggle.value === '1';
  document.querySelectorAll('[data-feature-gated="us"]').forEach(function(section) {
    section.hidden = !enabled;
    section.setAttribute('aria-hidden', enabled ? 'false' : 'true');
  });
}
function currentStrategySource() {
  var checked = document.querySelector('[data-strategy-source-toggle]:checked');
  return checked ? checked.value : 'builtin';
}
function syncStrategySourceSettings() {
  var source = currentStrategySource();
  document.querySelectorAll('[data-strategy-source-gated]').forEach(function(section) {
    var enabled = section.getAttribute('data-strategy-source-gated') === source;
    section.hidden = !enabled;
    section.setAttribute('aria-hidden', enabled ? 'false' : 'true');
  });
}
document.addEventListener('DOMContentLoaded', syncUsFeatureSettings);
document.addEventListener('DOMContentLoaded', syncStrategySourceSettings);
syncUsFeatureSettings();
syncStrategySourceSettings();
function handleUsFeatureToggle(event) {
  var target = event.target;
  if (target && target.matches && target.matches('[data-feature-toggle="us"]')) {
    syncUsFeatureSettings();
  }
}
function handleStrategySourceToggle(event) {
  var target = event.target;
  if (target && target.matches && target.matches('[data-strategy-source-toggle]')) {
    syncStrategySourceSettings();
  }
}
document.addEventListener('input', handleUsFeatureToggle);
document.addEventListener('change', handleUsFeatureToggle);
document.addEventListener('input', handleStrategySourceToggle);
document.addEventListener('change', handleStrategySourceToggle);
function pulseSaveButton(button) {
  if (!button) return;
  button.classList.add('pressed');
  window.setTimeout(function() { button.classList.remove('pressed'); }, 180);
}
document.addEventListener('pointerdown', function(event) {
  var target = event.target;
  if (!target || !target.closest) return;
  var button = target.closest('[data-env-save-button]');
  if (button && !button.disabled) pulseSaveButton(button);
});
function envFormSnapshot(form) {
  if (!form || !window.FormData || !window.URLSearchParams) return '';
  return new URLSearchParams(new FormData(form)).toString();
}
function markEnvFormSaved(form) {
  if (!form) return;
  form.dataset.savedSnapshot = envFormSnapshot(form);
  form.dataset.savedState = '1';
}
function resetEnvSaveIfDirty(form) {
  if (!form || form.id !== 'env-config-form' || form.dataset.savedState !== '1') return;
  var currentSnapshot = envFormSnapshot(form);
  if (!currentSnapshot || currentSnapshot === form.dataset.savedSnapshot) return;
  form.dataset.savedState = '0';
  setEnvSaveFeedback(form, '', '有未保存修改');
}
function setEnvSaveFeedback(form, state, message) {
  var button = form ? form.querySelector('[data-env-save-button]') : null;
  var status = form ? form.querySelector('[data-env-save-status]') : null;
  if (status) {
    status.textContent = message || '';
    status.className = 'settings-save-status' + (state ? ' ' + state : '');
  }
  if (!button) return;
  if (!button.dataset.defaultText) button.dataset.defaultText = button.textContent || '保存业务配置';
  button.classList.remove('saved', 'error');
  if (state === 'busy') {
    button.disabled = true;
    button.textContent = '保存中...';
  } else if (state === 'ok') {
    button.disabled = false;
    button.classList.add('saved');
    button.textContent = '已保存';
    markEnvFormSaved(form);
  } else if (state === 'error') {
    button.disabled = false;
    button.classList.add('error');
    button.textContent = '保存失败';
  } else {
    button.disabled = false;
    button.textContent = button.dataset.defaultText || '保存业务配置';
  }
}
document.addEventListener('input', function(event) {
  var target = event.target;
  var form = target && target.closest ? target.closest('#env-config-form') : null;
  resetEnvSaveIfDirty(form);
});
document.addEventListener('change', function(event) {
  var target = event.target;
  var form = target && target.closest ? target.closest('#env-config-form') : null;
  resetEnvSaveIfDirty(form);
});
function businessSaveMessage(payload) {
  if (!payload || payload.ok === false) return '保存失败';
  if (!payload.changed) return '配置未变化，无需重新应用';
  var count = Number(payload.changed_count || 0);
  var applied = ((payload.runtime && payload.runtime.applied) || []).filter(function(item) { return item !== 'env'; });
  var message = '已保存 ' + count + ' 项';
  if (applied.length) message += '，已热应用：' + applied.join('、');
  return message;
}
document.addEventListener('submit', function(event) {
  var form = event.target;
  if (!form || form.id !== 'env-config-form') return;
  if (!window.fetch || !window.FormData || !window.URLSearchParams) return;
  event.preventDefault();
  setEnvSaveFeedback(form, 'busy', '正在保存业务配置...');
  fetch('/api/admin/config/env', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8', 'Accept': 'application/json'},
    body: new URLSearchParams(new FormData(form))
  }).then(function(response) {
    return response.json().catch(function() { return null; }).then(function(payload) {
      if (!response.ok || !payload || payload.ok === false) {
        throw new Error((payload && payload.error) || '保存失败，请确认登录状态后重试');
      }
      return payload;
    });
  }).then(function(payload) {
    syncUsFeatureSettings();
    syncStrategySourceSettings();
    setEnvSaveFeedback(form, 'ok', businessSaveMessage(payload));
  }).catch(function(error) {
    setEnvSaveFeedback(form, 'error', error && error.message ? error.message : '保存失败，请稍后重试');
  });
});
document.addEventListener('click', function(event) {
  var target = event.target;
  if (!target || !target.closest) return;
  var addButton = target.closest('[data-time-list-add]');
  if (addButton) {
    var control = addButton.closest('[data-time-list]');
    var items = control ? control.querySelector('[data-time-list-items]') : null;
    var fieldName = control ? control.getAttribute('data-field-name') : '';
    if (!items || !fieldName) return;
    var item = document.createElement('div');
    item.className = 'time-list-item';
    var input = document.createElement('input');
    input.type = control.getAttribute('data-input-type') || 'time';
    input.name = fieldName;
    input.placeholder = control.getAttribute('data-placeholder') || '';
    if (input.type === 'text') {
      input.autocapitalize = 'off';
      input.spellcheck = false;
    }
    var removeButton = document.createElement('button');
    removeButton.type = 'button';
    removeButton.className = 'time-list-remove';
    removeButton.setAttribute('data-time-list-remove', '');
    removeButton.setAttribute('aria-label', '删除时间点');
    removeButton.title = '删除时间点';
    removeButton.textContent = 'x';
    item.appendChild(input);
    item.appendChild(removeButton);
    items.appendChild(item);
    resetEnvSaveIfDirty(control.closest('form'));
    input.focus();
    return;
  }
  var removeButton = target.closest('[data-time-list-remove]');
  if (removeButton) {
    var item = removeButton.closest('.time-list-item');
    var form = removeButton.closest('form');
    if (item) item.remove();
    resetEnvSaveIfDirty(form);
  }
});
</script>
</body></html>"""

INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>牛牛1号</title>
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%90%AE%3C/text%3E%3C/svg%3E">
  <style>
    :root { color-scheme: dark; --bg:#06070a; --panel:#10131a; --panel2:#151a24; --text:#f2f4f8; --muted:#99a3b3; --line:#252b38; --accent:#7c5cff; --green:#39d98a; --yellow:#ffd166; }
    * { box-sizing: border-box; }
    html { -webkit-text-size-adjust: 100%; }
    body { margin:0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: radial-gradient(circle at top left, rgba(124,92,255,.25), transparent 34rem), var(--bg); color:var(--text); overflow-x:hidden; }
    body.x-image-viewer-open { overflow:hidden; }
    .compliance-notice { display:grid; gap:6px; padding:10px clamp(16px, 4vw, 42px); border-bottom:1px solid rgba(148,163,184,.16); background:linear-gradient(180deg, rgba(15,23,42,.96), rgba(8,11,18,.94)); color:#cbd5e1; font-size:12px; line-height:1.55; box-shadow:inset 0 -1px 0 rgba(255,255,255,.025); }
    .compliance-row { display:flex; gap:8px; align-items:flex-start; flex-wrap:wrap; }
    .compliance-badge { flex:0 0 auto; border:1px solid rgba(251,191,36,.26); background:rgba(251,191,36,.10); color:#fde68a; border-radius:999px; padding:2px 8px; font-weight:850; line-height:1.45; white-space:nowrap; }
    .compliance-badge.risk { border-color:rgba(248,113,113,.28); background:rgba(127,29,29,.16); color:#fecaca; }
    .compliance-text { min-width:0; flex:1 1 300px; overflow-wrap:anywhere; }
    header { position: sticky; top:0; z-index:2; backdrop-filter: blur(16px); background: rgba(6,7,10,.78); border-bottom:1px solid var(--line); padding:20px clamp(16px, 4vw, 42px); }
    .header-row { display:flex; align-items:center; justify-content:space-between; gap:14px; }
    .header-actions { display:flex; align-items:center; justify-content:flex-end; gap:8px; flex-wrap:wrap; }
    h1 { margin:0; font-size: clamp(26px, 4vw, 42px); letter-spacing:-.04em; }
    .settings-link, .header-link, .refresh-pill, .visit-pill { display:inline-flex; align-items:baseline; gap:8px; flex:0 0 auto; border:1px solid rgba(148,163,184,.16); background:rgba(15,23,42,.58); border-radius:999px; padding:7px 11px; color:#cbd5e1; box-shadow:inset 0 1px 0 rgba(255,255,255,.035); }
    .settings-link, .header-link { align-items:center; text-decoration:none; color:#e5edf8; font-size:13px; font-weight:850; border-color:rgba(124,92,255,.30); background:rgba(124,92,255,.14); transition:.16s ease; }
    .settings-link:hover, .header-link:hover { border-color:rgba(157,178,255,.62); background:rgba(124,92,255,.22); transform:translateY(-1px); }
    .settings-link:focus-visible, .header-link:focus-visible { outline:2px solid rgba(157,178,255,.86); outline-offset:2px; }
    .header-link svg { width:15px; height:15px; flex:0 0 auto; fill:currentColor; }
    .refresh-pill span, .visit-pill span { color:#7b8aa0; font-size:12px; font-weight:750; }
    .refresh-pill b, .visit-pill b { font-size:13px; font-variant-numeric:tabular-nums; letter-spacing:0; }
    .visit-pill { border-color:rgba(124,92,255,.28); background:rgba(124,92,255,.10); }
    .subtitle { margin-top:8px; color:var(--muted); }
    .category-tabs { display:flex; gap:10px; flex-wrap:wrap; margin-top:18px; }
    .tab { border:1px solid var(--line); background:rgba(21,26,36,.86); color:var(--muted); border-radius:999px; padding:10px 14px; cursor:pointer; transition:.18s ease; white-space:nowrap; text-decoration:none; display:inline-flex; align-items:center; }
    .tab:visited, .tab:hover, .tab:active, .tab:focus { text-decoration:none; }
    .tab.active { color:white; border-color:rgba(124,92,255,.7); background:linear-gradient(135deg, rgba(124,92,255,.95), rgba(36,198,220,.72)); box-shadow:0 12px 36px rgba(124,92,255,.22); }
    input, select, button { background:var(--panel2); border:1px solid var(--line); color:var(--text); border-radius:12px; padding:10px 12px; font:inherit; }
    button { cursor:pointer; }
    main { padding:22px clamp(16px, 4vw, 42px) 48px; }
    .card { background:rgba(16,19,26,.86); border:1px solid var(--line); border-radius:18px; box-shadow: 0 18px 70px rgba(0,0,0,.22); }
    .feed { display:grid; gap:14px; }
    .card { padding:18px; overflow:hidden; background:linear-gradient(135deg, rgba(16,19,26,.92) 0%, rgba(21,26,36,.88) 100%); }
    .sector-cloud { min-width:0; overflow:hidden; background:linear-gradient(180deg, rgba(15,23,42,.92), rgba(6,10,18,.96)); border:1px solid rgba(148,163,184,.14); border-radius:18px; padding:18px; box-shadow:0 18px 70px rgba(0,0,0,.24), inset 0 1px 0 rgba(255,255,255,.035); }
    .sector-cloud h3 { margin:0 0 10px; font-size:16px; color:#dbeafe; font-weight:850; letter-spacing:-.01em; }
    .sector-columns { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:16px; align-items:start; }
    .sector-column { min-width:0; }
    .sector-grid { min-width:0; display:grid; grid-template-columns:repeat(auto-fill, minmax(130px, 1fr)); gap:8px; }
    .sector-item { min-width:0; overflow:hidden; background:rgba(2,6,23,.50); border:1px solid rgba(148,163,184,.12); border-radius:13px; padding:10px; box-shadow:inset 0 1px 0 rgba(255,255,255,.025); }
    .sector-name { font-size:12px; color:#cbd5e1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-weight:700; }
    .sector-item.up, .hot-item.up { background:rgba(127,29,29,.28); border-color:rgba(248,113,113,.22); }
    .sector-item.down, .hot-item.down { background:rgba(6,78,59,.28); border-color:rgba(52,211,153,.22); }
    .sector-item.flat, .hot-item.flat { background:rgba(30,41,59,.30); border-color:rgba(148,163,184,.12); }
    .sector-item.up .sector-pct, .hot-item.up .sector-pct { color:#fb7185; text-shadow:0 0 14px rgba(248,113,113,.22); }
    .sector-item.down .sector-pct, .hot-item.down .sector-pct { color:#34d399; text-shadow:0 0 14px rgba(52,211,153,.22); }
    .sector-pct { min-width:0; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:14px; font-weight:850; margin-top:4px; font-variant-numeric:tabular-nums; }
    .flow-val { font-size:11px; margin-left:4px; font-weight:800; white-space:nowrap; }
    .flow-in { color:#fb7185; text-shadow:0 0 14px rgba(248,113,113,.25); }
    .flow-out { color:#34d399; text-shadow:0 0 14px rgba(52,211,153,.25); }
    .market-strip { display:flex; gap:12px; overflow-x:auto; margin:0 0 16px; padding-bottom:4px; scrollbar-width:none; }
    .market-strip::-webkit-scrollbar { display:none; }
    .indices-page { display:grid; gap:14px; }
    .indices-switch { width:min(320px,100%); display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:3px; padding:3px; border:1px solid rgba(148,163,184,.16); border-radius:14px; background:rgba(2,6,23,.46); box-shadow:inset 0 1px 0 rgba(255,255,255,.035); }
    .indices-switch-btn { appearance:none; border:0; border-radius:10px; margin:0; padding:9px 12px; min-width:0; color:#94a3b8; background:transparent; font-size:14px; line-height:1; font-weight:850; }
    .indices-switch-btn.active { color:#f8fafc; background:linear-gradient(135deg, rgba(124,92,255,.86), rgba(36,198,220,.58)); box-shadow:0 8px 22px rgba(124,92,255,.18), inset 0 1px 0 rgba(255,255,255,.10); }
    .indices-switch-btn:focus-visible { outline:2px solid rgba(157,178,255,.86); outline-offset:2px; }
    .indices-part { display:grid; gap:12px; min-width:0; }
    .indices-part-head { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:0 2px; }
    .indices-part-title-row { min-width:0; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
    .indices-part-title { margin:0; color:#f8fafc; font-size:18px; line-height:1.2; font-weight:900; letter-spacing:0; }
    .indices-part-meta { color:#7b8aa0; font-size:12px; font-weight:750; font-variant-numeric:tabular-nums; white-space:nowrap; }
    .market-region-switch { width:126px; display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:2px; padding:2px; border:1px solid rgba(148,163,184,.15); border-radius:10px; background:rgba(2,6,23,.52); box-shadow:inset 0 1px 0 rgba(255,255,255,.03); }
    .market-region-btn { appearance:none; min-width:0; margin:0; padding:6px 9px; border:0; border-radius:7px; color:#8290a5; background:transparent; font-size:12px; line-height:1; font-weight:850; white-space:nowrap; }
    .market-region-btn.active { color:#eff6ff; background:rgba(96,165,250,.22); box-shadow:inset 0 0 0 1px rgba(125,211,252,.24); }
    .market-region-btn:focus-visible { outline:2px solid rgba(125,211,252,.82); outline-offset:1px; }
    .indices-index-stack { min-width:0; }
    .indices-index-stack > div:last-child { margin-bottom:0 !important; }
    .indices-market-stack { display:grid; gap:18px; min-width:0; }
    .index-card { background:linear-gradient(180deg, rgba(15,23,42,.92), rgba(2,6,23,.84)); border:1px solid rgba(148,163,184,.14); border-radius:16px; padding:12px 14px; min-width:140px; flex:0 0 auto; box-shadow:inset 0 1px 0 rgba(255,255,255,.035), 0 10px 28px rgba(0,0,0,.18); }
    .index-card.index-up { background:linear-gradient(180deg, rgba(127,29,29,.20), rgba(15,23,42,.88)); border-color:rgba(248,113,113,.22); }
    .index-card.index-down { background:linear-gradient(180deg, rgba(6,78,59,.20), rgba(15,23,42,.88)); border-color:rgba(52,211,153,.22); }
    .index-name { font-size:12px; color:#a7b3c5; font-weight:750; }
    .index-price { font-size:22px; font-weight:850; margin-top:4px; font-variant-numeric:tabular-nums; }
    .index-change { font-size:13px; margin-top:2px; font-weight:850; font-variant-numeric:tabular-nums; }
    .index-up { color:#ff6b6b; }
    .index-down { color:#34d399; }
    .index-flat { color:#94a3b8; }
    .index-time { font-size:10px; color:#64748b; margin-top:4px; }
    .sparkline { height:40px; width:100%; margin-top:6px; stroke:currentColor; fill:none; stroke-width:1.5; opacity:.95; }
    .sparkline-area { fill:currentColor; opacity:.12; }
    .sparkline-line { stroke:currentColor; stroke-width:2; fill:none; vector-effect:non-scaling-stroke; }
    .sparkline-zero { stroke:rgba(226,232,240,.46); stroke-width:1; stroke-dasharray:4 4; vector-effect:non-scaling-stroke; }
    .index-head { font-size:12px; color:#94a3b8; margin-bottom:2px; font-weight:600; }
    .hot-item { min-width:0; overflow:hidden; background:rgba(2,6,23,.50); border:1px solid rgba(148,163,184,.12); border-radius:13px; padding:10px; box-shadow:inset 0 1px 0 rgba(255,255,255,.025); }
    .hot-price { min-width:0; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:14px; font-weight:850; margin-top:3px; font-variant-numeric:tabular-nums; }
    .us-sector-cloud .sector-grid { grid-template-columns:repeat(auto-fit, minmax(165px, 1fr)); }
    .us-sector-card { display:grid; align-content:start; gap:2px; min-height:92px; }
    .us-sector-card .sector-pct { width:100%; }
    .us-sector-map { min-width:0; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; margin-top:1px; font-size:11px; line-height:1.3; font-weight:800; }
    .us-sector-card.up .us-sector-map { color:#fda4af; }
    .us-sector-card.down .us-sector-map { color:#5eead4; }
    .us-sector-card.flat .us-sector-map { color:#94a3b8; }
    .mobile-head { display:flex; justify-content:flex-end; gap:8px; align-items:flex-start; color:rgba(148,163,184,0.7); font-size:12.5px; margin-bottom:14px; }
    .mobile-head span { text-align:right; flex:0 0 auto; }
    .meta { display:flex; gap:8px; flex-wrap:wrap; align-items:center; color:var(--muted); font-size:13px; margin-bottom:12px; }
    .pill { border:1px solid var(--line); background:#0b0e14; color:#cdd5e2; padding:5px 9px; border-radius:999px; max-width:100%; overflow-wrap:anywhere; }
    .platform { color:white; background:linear-gradient(135deg, var(--accent), #24c6dc); border:0; }
    .matched { color:#09140e; background:var(--green); border:0; }
    .sessiononly { color:#211800; background:var(--yellow); border:0; }
    .content { white-space:pre-wrap; line-height:1.7; color:#f1f5f9; max-height:260px; overflow:auto; border-top:1px solid var(--line); padding-top:14px; overflow-wrap:anywhere; word-break:break-word; font-size:15px; }
    .post-header { font-size:11px; font-weight:600; letter-spacing:0.02em; color:rgba(139,92,246,0.9); margin-bottom:10px; padding-bottom:10px; border-bottom:1px solid rgba(100,116,139,0.15); }
    .has-header .content { border-top:none; padding-top:0; }
    .thread-card { display:flex; flex-direction:column; gap:16px; }
    .thread-original { background:linear-gradient(135deg, rgba(124,92,255,0.09) 0%, rgba(100,116,139,0.06) 100%); border-left:3px solid rgba(139,92,246,0.6); padding:14px 16px; border-radius:12px; margin-bottom:2px; box-shadow: inset 0 1px 3px rgba(0,0,0,0.12); }
    .thread-original-content { font-size:14.5px; line-height:1.75; color:rgba(241,245,249,0.9); white-space:pre-wrap; word-break:break-word; }
    .thread-original-content::first-line { font-size:11px; font-weight:600; color:rgba(124,92,255,0.9); letter-spacing:0.03em; }
    .thread-reply { padding:0; }
    .thread-reply-content { font-size:15px; line-height:1.68; color:#f1f5f9; white-space:pre-wrap; word-break:break-word; }
    .market-monitor-grid { display:grid; gap:12px; }
    .market-monitor-card { border:1px solid rgba(148,163,184,.15); border-radius:16px; overflow:hidden; background:linear-gradient(135deg, rgba(16,19,26,.92), rgba(10,15,24,.96)); box-shadow:0 14px 42px rgba(0,0,0,.20), inset 0 1px 0 rgba(255,255,255,.035); cursor:pointer; transition:background .14s ease, border-color .14s ease, transform .14s ease; }
    .market-monitor-card:hover { border-color:rgba(124,92,255,.32); background:linear-gradient(135deg, rgba(18,24,36,.96), rgba(10,15,24,.96)); }
    .market-monitor-card.open { border-color:rgba(124,92,255,.42); background:linear-gradient(135deg, rgba(22,28,42,.98), rgba(9,13,22,.98)); }
    .market-card-head { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:12px; padding:15px 16px; }
    .market-card-title-row { display:flex; align-items:center; gap:8px; min-width:0; flex-wrap:wrap; }
    .market-card-title { color:#f8fafc; font-size:15px; font-weight:850; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .market-card-time { color:#64748b; font-size:12px; white-space:nowrap; font-variant-numeric:tabular-nums; }
    .market-card-preview { margin-top:7px; color:#cbd5e1; font-size:14px; line-height:1.5; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; overflow-wrap:anywhere; word-break:break-word; }
    .market-chip-row { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
    .market-chip { border:1px solid rgba(148,163,184,.13); border-radius:999px; padding:4px 8px; color:#a7b3c5; font-size:11px; background:rgba(2,6,23,.34); white-space:nowrap; max-width:100%; overflow:hidden; text-overflow:ellipsis; }
    .market-chip.up { color:#fecaca; border-color:rgba(248,113,113,.24); background:rgba(127,29,29,.18); }
    .market-chip.down { color:#bbf7d0; border-color:rgba(52,211,153,.24); background:rgba(6,78,59,.18); }
    .market-card-side { display:flex; align-items:flex-start; gap:8px; justify-content:flex-end; }
    .market-type { border-radius:999px; padding:5px 9px; color:#bfdbfe; font-size:12px; font-weight:800; background:rgba(37,99,235,.13); border:1px solid rgba(96,165,250,.24); white-space:nowrap; }
    .market-chevron { color:#64748b; font-size:18px; width:18px; text-align:center; line-height:1.35; transition:transform .14s ease, color .14s ease; }
    .market-monitor-card.open .market-chevron { color:#c4b5fd; transform:rotate(90deg); }
    .market-card-detail { padding:0 16px 16px; cursor:auto; }
    .market-detail-box { border-top:1px solid rgba(148,163,184,.12); padding-top:14px; display:grid; gap:14px; }
    .market-detail-overview { display:grid; grid-template-columns:minmax(300px,.78fr) minmax(420px,1.22fr); gap:12px; align-items:start; border:1px solid rgba(96,165,250,.16); border-radius:12px; padding:12px; background:linear-gradient(135deg, rgba(15,23,42,.82), rgba(2,6,23,.36)); box-shadow:inset 0 1px 0 rgba(255,255,255,.035); }
    .market-mood-panel { min-width:0; border-left:3px solid rgba(96,165,250,.82); border-radius:8px; padding:10px 12px; background:linear-gradient(135deg, rgba(37,99,235,.18), rgba(15,23,42,.42)); }
    .market-mood-label { color:#93c5fd; font-size:11px; font-weight:850; letter-spacing:.04em; margin-bottom:5px; }
    .market-mood-text { color:#f8fafc; font-size:15px; line-height:1.55; font-weight:750; overflow-wrap:anywhere; word-break:break-word; }
    .market-metric-grid { display:grid; grid-template-columns:repeat(4,minmax(92px,1fr)); gap:8px; }
    .market-metric-item { min-width:0; border:1px solid rgba(148,163,184,.13); border-radius:8px; padding:8px 9px; background:rgba(2,6,23,.46); box-shadow:inset 0 1px 0 rgba(255,255,255,.025); }
    .market-metric-label { color:#7b8aa0; font-size:11px; line-height:1.2; white-space:nowrap; }
    .market-metric-value { margin-top:3px; color:#e5edf8; font-size:14px; line-height:1.25; font-weight:850; font-variant-numeric:tabular-nums; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .market-metric-value.up { color:#d75442; }
    .market-metric-value.down { color:#59b881; }
    .market-section-list { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; align-items:start; }
    .market-section { position:relative; min-width:0; overflow:hidden; border:1px solid rgba(148,163,184,.14); border-radius:10px; background:linear-gradient(180deg, rgba(15,23,42,.76), rgba(2,6,23,.34)); box-shadow:inset 0 1px 0 rgba(255,255,255,.03), 0 10px 28px rgba(0,0,0,.12); }
    .market-section::before { content:""; position:absolute; left:0; top:0; bottom:0; width:3px; background:rgba(96,165,250,.68); }
    .market-section.wide { grid-column:1 / -1; }
    .market-section.hot::before { background:rgba(248,113,113,.72); }
    .market-section.flow::before { background:rgba(52,211,153,.72); }
    .market-section.risk::before { background:rgba(248,113,113,.84); }
    .market-section.tip::before { background:rgba(96,165,250,.76); }
    .market-section.overview::before { background:rgba(167,139,250,.72); }
    .market-section-head { display:flex; align-items:center; justify-content:space-between; gap:8px; padding:10px 12px 9px 13px; margin:0; border-bottom:1px solid rgba(148,163,184,.10); background:rgba(15,23,42,.56); }
    .market-section-title-wrap { display:flex; align-items:center; gap:7px; min-width:0; }
    .market-section-icon { width:24px; height:24px; border-radius:8px; display:grid; place-items:center; flex:0 0 auto; background:rgba(96,165,250,.12); color:#bfdbfe; font-size:14px; }
    .market-section.hot .market-section-icon { background:rgba(248,113,113,.12); color:#fecaca; }
    .market-section.flow .market-section-icon { background:rgba(52,211,153,.12); color:#bbf7d0; }
    .market-section.risk .market-section-icon { background:rgba(248,113,113,.14); color:#fecaca; }
    .market-section.tip .market-section-icon { background:rgba(96,165,250,.12); color:#bfdbfe; }
    .market-section.overview .market-section-icon { background:rgba(167,139,250,.12); color:#ddd6fe; }
    .market-section-title { color:#dbeafe; font-size:13px; font-weight:850; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .market-section-count { color:#8da0b8; font-size:11px; white-space:nowrap; border:1px solid rgba(148,163,184,.12); border-radius:999px; padding:2px 7px; background:rgba(2,6,23,.34); }
    .market-section-body { display:grid; gap:0; padding:8px 12px 10px 13px; }
    .market-detail-line { white-space:pre-wrap; color:#e2e8f0; font-size:13.5px; line-height:1.56; overflow-wrap:anywhere; word-break:break-word; }
    .market-detail-line.item { display:grid; grid-template-columns:9px minmax(0,1fr); gap:7px; align-items:start; padding:5px 0; border-bottom:1px solid rgba(148,163,184,.075); }
    .market-detail-line.item:last-child { border-bottom:0; }
    .market-detail-line.item::before { content:""; width:5px; height:5px; border-radius:999px; margin-top:.62em; background:rgba(148,163,184,.62); }
    .market-detail-line.note { color:#94a3b8; }
    .market-detail-line.note::before { background:rgba(148,163,184,.38); }
    .market-detail-line.flow { display:grid; grid-template-columns:46px minmax(0,1fr); gap:9px; align-items:baseline; padding:7px 0; border-bottom:1px solid rgba(148,163,184,.075); }
    .market-detail-line.flow:last-child { border-bottom:0; }
    .market-flow-label { color:#8da0b8; font-size:12px; font-weight:850; white-space:nowrap; }
    .market-flow-value { color:#e2e8f0; min-width:0; overflow-wrap:anywhere; word-break:break-word; }
    .market-num { display:inline-block; font-weight:900; font-variant-numeric:tabular-nums; border-radius:4px; padding:0 3px; line-height:1.22; }
    .market-num.up { color:#d75442; background:rgba(215,84,66,.10); text-shadow:none; }
    .market-num.down { color:#59b881; background:rgba(89,184,129,.10); text-shadow:none; }
    .market-symbol { display:inline-block; color:#e5edf8; font-weight:850; font-variant-numeric:tabular-nums; border:1px solid rgba(96,165,250,.18); border-radius:5px; padding:0 5px; line-height:1.28; background:rgba(96,165,250,.10); }
    .market-detail-line.risk { color:#fecaca; }
    .market-detail-line.risk::before { background:rgba(248,113,113,.76); }
    .market-detail-line.tip::before { background:rgba(96,165,250,.72); }
    .market-detail-heading { color:#dbeafe; font-size:13px; font-weight:850; margin-top:4px; }
    .market-detail-note { color:#94a3b8; }
    .us-market-summary-card { position:relative; overflow:hidden; border:1px solid rgba(148,163,184,.16); border-radius:16px; padding:15px 16px; background:linear-gradient(135deg, rgba(15,23,42,.94), rgba(2,6,23,.86)); box-shadow:0 14px 42px rgba(0,0,0,.20), inset 0 1px 0 rgba(255,255,255,.035); }
    .us-market-summary-card::before { content:""; position:absolute; left:0; top:0; bottom:0; width:4px; background:rgba(96,165,250,.74); }
    .us-market-summary-card.offensive::before { background:rgba(248,113,113,.78); }
    .us-market-summary-card.balanced::before { background:rgba(167,139,250,.74); }
    .us-market-summary-card.cautious::before { background:rgba(251,191,36,.78); }
    .us-market-summary-card.defensive::before { background:rgba(52,211,153,.78); }
    .us-market-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:12px; }
    .us-market-title { color:#f8fafc; font-size:16px; line-height:1.25; font-weight:900; }
    .us-market-sub { margin-top:4px; color:#7b8aa0; font-size:12px; line-height:1.35; }
    .us-market-tone { flex:0 0 auto; border:1px solid rgba(96,165,250,.24); border-radius:999px; padding:5px 9px; color:#bfdbfe; background:rgba(37,99,235,.13); font-size:12px; line-height:1; font-weight:850; white-space:nowrap; }
    .us-market-summary-card.offensive .us-market-tone { color:#fecaca; border-color:rgba(248,113,113,.28); background:rgba(127,29,29,.20); }
    .us-market-summary-card.balanced .us-market-tone { color:#ddd6fe; border-color:rgba(167,139,250,.28); background:rgba(88,28,135,.18); }
    .us-market-summary-card.cautious .us-market-tone { color:#fde68a; border-color:rgba(251,191,36,.30); background:rgba(113,63,18,.18); }
    .us-market-summary-card.defensive .us-market-tone { color:#bbf7d0; border-color:rgba(52,211,153,.28); background:rgba(6,78,59,.20); }
    .us-market-brief { color:#e2e8f0; font-size:14px; line-height:1.58; margin-bottom:12px; overflow-wrap:anywhere; word-break:break-word; }
    .us-market-metrics { display:grid; grid-template-columns:repeat(auto-fit,minmax(128px,1fr)); gap:8px; margin-bottom:12px; }
    .us-market-metric { min-width:0; border:1px solid rgba(148,163,184,.13); border-radius:9px; padding:8px 9px; background:rgba(2,6,23,.42); }
    .us-market-metric-label { color:#8da0b8; font-size:11px; line-height:1.2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .us-market-metric-value { margin-top:3px; display:flex; align-items:baseline; justify-content:space-between; gap:7px; color:#e5edf8; font-size:13px; line-height:1.25; font-weight:850; font-variant-numeric:tabular-nums; white-space:nowrap; }
    .us-market-pct.up { color:#d75442; }
    .us-market-pct.down { color:#59b881; }
    .us-market-pct.flat { color:#94a3b8; }
    .us-market-map { display:grid; gap:7px; margin:0 0 12px; }
    .us-market-map-line { min-width:0; border:1px solid rgba(148,163,184,.12); border-radius:9px; padding:8px 9px; background:rgba(15,23,42,.42); color:#cbd5e1; font-size:12.5px; line-height:1.45; overflow-wrap:anywhere; word-break:break-word; }
    .us-market-map-line strong { color:#eef2ff; font-weight:850; }
    .us-market-map-line .map-pct.up { color:#d75442; font-weight:850; }
    .us-market-map-line .map-pct.down { color:#59b881; font-weight:850; }
    .us-market-map-line .map-pct.flat { color:#94a3b8; font-weight:850; }
    .us-market-guidance { display:grid; gap:7px; }
    .us-market-guidance-line { display:grid; grid-template-columns:8px minmax(0,1fr); gap:8px; color:#cbd5e1; font-size:13.5px; line-height:1.5; }
    .us-market-guidance-line::before { content:""; width:5px; height:5px; border-radius:999px; margin-top:.62em; background:rgba(148,163,184,.62); }
    .market-day-pager { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-top:12px; padding:13px 15px; }
    .market-day-title { color:#dbeafe; font-size:14px; font-weight:850; }
    .market-day-sub { color:#7b8aa0; font-size:12px; margin-top:3px; }
    .market-day-actions { display:flex; gap:8px; flex-wrap:wrap; }
    .market-day-btn { padding:7px 11px; font-size:13px; border-radius:10px; }
    .market-day-btn:disabled { opacity:.45; cursor:not-allowed; }
    .x-monitor-panel { padding:0; overflow:hidden; }
    .x-monitor-head { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:12px; align-items:center; padding:14px 16px; border-bottom:1px solid rgba(148,163,184,.13); background:linear-gradient(180deg, rgba(15,23,42,.84), rgba(2,6,23,.28)); }
    .x-monitor-title { color:#e5edf8; font-size:15px; font-weight:850; letter-spacing:.01em; }
    .x-monitor-sub { color:#7b8aa0; font-size:12px; margin-top:3px; }
    .x-monitor-metrics { display:flex; flex-wrap:wrap; gap:8px; justify-content:flex-end; }
    .x-metric { border:1px solid rgba(148,163,184,.13); border-radius:999px; padding:5px 9px; color:#a7b3c5; font-size:12px; background:rgba(2,6,23,.32); font-variant-numeric:tabular-nums; }
    .x-list { display:grid; }
    .x-row { display:grid; grid-template-columns:42px minmax(0,1fr) auto; gap:11px; align-items:start; padding:13px 16px; border-bottom:1px solid rgba(148,163,184,.10); cursor:pointer; transition:background .14s ease, border-color .14s ease; }
    .x-row:last-child { border-bottom:0; }
    .x-row:hover { background:rgba(124,92,255,.08); }
    .x-row.open { background:rgba(15,23,42,.52); border-color:rgba(124,92,255,.24); }
    .x-avatar { width:38px; height:38px; border-radius:13px; display:grid; place-items:center; color:#f8fafc; font-size:14px; font-weight:850; background:linear-gradient(135deg, rgba(124,92,255,.95), rgba(36,198,220,.64)); box-shadow:inset 0 1px 0 rgba(255,255,255,.18); overflow:hidden; }
    .x-copy { min-width:0; }
    .x-line { display:flex; align-items:center; gap:7px; min-width:0; flex-wrap:wrap; }
    .x-author { color:#f1f5f9; font-size:14px; font-weight:850; max-width:min(42vw, 420px); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .x-handle, .x-time { color:#64748b; font-size:12px; white-space:nowrap; }
    .x-preview { margin-top:5px; color:#cbd5e1; font-size:14px; line-height:1.48; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; overflow-wrap:anywhere; word-break:break-word; }
    .x-row.open .x-preview { -webkit-line-clamp:3; color:#e2e8f0; }
    .x-media-strip { display:flex; gap:7px; margin-top:9px; align-items:center; min-height:44px; }
    .x-media-thumb { position:relative; width:64px; height:44px; border-radius:8px; overflow:hidden; border:1px solid rgba(148,163,184,.16); background:rgba(15,23,42,.74); flex:0 0 auto; }
    .x-media-thumb img { width:100%; height:100%; object-fit:cover; display:block; }
    .x-media-more { color:#8da0b8; font-size:11px; font-weight:800; border:1px solid rgba(148,163,184,.14); border-radius:999px; padding:4px 7px; background:rgba(2,6,23,.38); }
    .x-media-gallery { margin-top:12px; display:grid; gap:10px; }
    .x-media-group { display:grid; gap:8px; }
    .x-media-label { color:#8da0b8; font-size:11px; font-weight:850; letter-spacing:.05em; }
    .x-media-grid { display:grid; grid-template-columns:1fr; gap:10px; }
    .x-media-tile { appearance:none; display:block; width:100%; min-width:0; padding:0; overflow:hidden; border-radius:10px; border:1px solid rgba(148,163,184,.16); background:rgba(15,23,42,.64); text-decoration:none; cursor:zoom-in; text-align:initial; }
    .x-media-frame { width:100%; height:clamp(240px, 56vh, 640px); display:flex; align-items:center; justify-content:center; padding:8px; background:#020617; }
    .x-media-frame img { width:auto; height:auto; max-width:100%; max-height:100%; object-fit:contain; display:block; border-radius:6px; }
    .x-image-viewer-backdrop { position:fixed; inset:0; z-index:60; display:flex; align-items:center; justify-content:center; padding:18px; background:rgba(2,6,23,.78); backdrop-filter:blur(10px); }
    .x-image-viewer-card { width:min(1120px, 100%); height:min(82vh, 820px); display:grid; grid-template-rows:auto minmax(0,1fr); overflow:hidden; border:1px solid rgba(148,163,184,.20); border-radius:16px; background:rgba(8,12,22,.96); box-shadow:0 24px 90px rgba(0,0,0,.48); }
    .x-image-viewer-head { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:10px 12px; border-bottom:1px solid rgba(148,163,184,.14); }
    .x-image-viewer-title { min-width:0; color:#cbd5e1; font-size:13px; font-weight:850; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .x-image-viewer-actions { display:flex; gap:7px; align-items:center; flex:0 0 auto; }
    .x-image-viewer-btn { width:34px; height:34px; min-width:34px; display:grid; place-items:center; padding:0; border-radius:10px; border:1px solid rgba(148,163,184,.18); background:rgba(15,23,42,.88); color:#e5edf8; font-size:18px; line-height:1; font-weight:850; }
    .x-image-viewer-btn:disabled { opacity:.42; cursor:not-allowed; }
    .x-image-viewer-stage { min-height:0; overflow:auto; display:flex; align-items:center; justify-content:center; padding:14px; background:#020617; }
    .x-image-viewer-img { max-width:100%; max-height:100%; display:block; border-radius:8px; cursor:zoom-out; transform:scale(var(--x-image-zoom, 1)); transform-origin:center center; transition:transform .12s ease; }
    .x-badges { display:flex; gap:6px; align-items:center; justify-content:flex-end; flex-wrap:wrap; }
    .x-badge { border:1px solid rgba(148,163,184,.13); border-radius:999px; padding:4px 7px; color:#8da0b8; font-size:11px; background:rgba(2,6,23,.34); white-space:nowrap; }
    .x-badge.hot { color:#bfdbfe; border-color:rgba(96,165,250,.25); background:rgba(37,99,235,.12); }
    .x-chevron { color:#64748b; font-size:16px; line-height:1; width:16px; text-align:center; }
    .x-row.open .x-chevron { color:#c4b5fd; transform:rotate(90deg); }
    .x-detail { grid-column:2 / -1; margin-top:9px; padding:12px 13px; border:1px solid rgba(148,163,184,.13); border-radius:14px; background:rgba(2,6,23,.42); cursor:auto; }
    .x-detail .content { max-height:none; overflow:visible; border-top:0; padding-top:0; }
    .x-detail .thread-card { gap:12px; }
    .x-detail .thread-original { margin-bottom:0; }
    .x-pager { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-top:2px; padding:12px 14px; }
    .x-pager-status { color:#94a3b8; font-size:13px; font-variant-numeric:tabular-nums; }
    .x-pager-actions { display:flex; gap:8px; flex-wrap:wrap; }
    .x-page-btn { padding:7px 11px; font-size:13px; border-radius:10px; }
    .x-page-btn:disabled { opacity:.45; cursor:not-allowed; }
    .inline-field { border:1px solid rgba(148,163,184,.12); border-radius:13px; padding:10px 11px; background:rgba(2,6,23,.36); }
    .inline-label { color:#8da0b8; font-size:11px; font-weight:800; letter-spacing:.05em; margin-bottom:3px; }
    .inline-value { color:#e5edf8; font-size:14px; line-height:1.5; font-weight:600; }
    .practice-calendar-open-btn { display:inline-flex; align-items:center; justify-content:center; min-width:0; padding:5px 9px; border-radius:8px; border:1px solid rgba(124,92,255,.30); background:rgba(124,92,255,.14); color:#dbeafe; font-size:11px; line-height:1; font-weight:850; white-space:nowrap; box-shadow:inset 0 1px 0 rgba(255,255,255,.045); }
    .practice-calendar-open-btn:hover { border-color:rgba(157,178,255,.56); background:rgba(124,92,255,.22); }
    .practice-chart-card { position:relative; overflow:hidden; margin:12px 0; padding:14px 14px 10px; border-radius:18px; border:1px solid rgba(255,255,255,.08); background:radial-gradient(circle at 15% 0%, rgba(113,112,255,.16), transparent 34%), linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,.018)); box-shadow:inset 0 1px 0 rgba(255,255,255,.06), 0 18px 42px rgba(0,0,0,.22); }
    .practice-chart-head { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; margin-bottom:8px; }
    .practice-chart-title-row { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
    .practice-chart-title { color:#f7f8f8; font-size:13px; font-weight:800; letter-spacing:-.01em; }
    .practice-chart-sub { margin-top:3px; color:#8a8f98; font-size:11px; }
    .practice-mode-control { display:inline-flex; align-items:center; gap:2px; padding:2px; border-radius:9px; border:1px solid rgba(255,255,255,.08); background:rgba(0,0,0,.20); }
    .practice-mode-btn { appearance:none; border:0; border-radius:7px; padding:4px 8px; cursor:pointer; background:transparent; color:#8a8f98; font-size:11px; line-height:1; font-weight:800; letter-spacing:0; white-space:nowrap; }
    .practice-mode-btn.active { color:#f7f8f8; background:rgba(255,255,255,.10); box-shadow:inset 0 1px 0 rgba(255,255,255,.06); }
    .practice-chart-kpis { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
    .practice-kpi { min-width:74px; padding:6px 8px; border-radius:12px; border:1px solid rgba(255,255,255,.07); background:rgba(0,0,0,.20); text-align:right; }
    .practice-kpi-label { font-size:10px; color:#62666d; font-weight:700; letter-spacing:.04em; }
    .practice-kpi-value { margin-top:2px; font-size:13px; color:#f7f8f8; font-weight:800; font-variant-numeric:tabular-nums; }
    .practice-kpi-value.up, .practice-chart-value.up { color:#ff4d4f; }
    .practice-kpi-value.down, .practice-chart-value.down { color:#39d98a; }
    .practice-chart-wrap { position:relative; height:182px; }
    .practice-chart-svg { width:100%; height:100%; display:block; overflow:hidden; }
    .practice-axis-label { position:absolute; right:2px; font-size:10px; color:#62666d; transform:translateY(-50%); font-variant-numeric:tabular-nums; }
    .practice-axis-label.top { top:10px; }
    .practice-axis-label.mid { top:50%; }
    .practice-axis-label.bot { bottom:32px; transform:none; }
    .practice-zero-axis-label { position:absolute; right:2px; transform:translateY(-50%); color:#aeb6c6; font-size:10px; line-height:1; font-weight:800; font-variant-numeric:tabular-nums; letter-spacing:0; pointer-events:none; text-shadow:0 1px 2px rgba(0,0,0,.35); }
    .practice-time-label { position:absolute; bottom:3px; transform:translateX(-50%); color:#8a8f98; font-size:11px; line-height:1; font-weight:700; font-variant-numeric:tabular-nums; letter-spacing:0; white-space:nowrap; pointer-events:none; text-shadow:0 1px 2px rgba(0,0,0,.35); }
    .practice-time-label.start { transform:none; }
    .practice-time-label.end { transform:translateX(-100%); }
    .practice-current-marker { position:absolute; width:10px; height:10px; border-radius:999px; background:#f7f8f8; border:2px solid var(--marker-color, #39d98a); box-shadow:0 0 0 4px rgba(255,255,255,.06), 0 0 18px var(--marker-glow, rgba(57,217,138,.55)); transform:translate(-50%,-50%); pointer-events:none; }
    .practice-current-line { position:absolute; top:16px; bottom:38px; width:1px; background:linear-gradient(to bottom, transparent, rgba(255,255,255,.28), transparent); transform:translateX(-50%); pointer-events:none; }
    .practice-chart-hover-layer { position:absolute; inset:0; z-index:5; cursor:crosshair; touch-action:none; user-select:none; -webkit-user-select:none; }
    .practice-hover-line { position:absolute; top:16px; bottom:38px; left:var(--hover-x-pct, 50%); width:1px; background:linear-gradient(to bottom, transparent, rgba(226,232,240,.56), transparent); transform:translateX(-50%); opacity:0; transition:opacity .10s ease; pointer-events:none; }
    .practice-hover-marker { position:absolute; left:var(--hover-x-pct, 50%); top:var(--hover-y-pct, 50%); width:9px; height:9px; border-radius:999px; background:#f8fafc; border:2px solid var(--marker-color, #39d98a); box-shadow:0 0 0 4px rgba(255,255,255,.07), 0 0 18px var(--marker-glow, rgba(57,217,138,.45)); transform:translate(-50%,-50%); opacity:0; transition:opacity .10s ease; pointer-events:none; }
    .practice-hover-tooltip { position:absolute; left:var(--hover-x-pct, 50%); top:var(--hover-y-pct, 50%); z-index:2; min-width:168px; max-width:min(230px, calc(100% - 20px)); display:grid; gap:5px; padding:8px 9px; border:1px solid rgba(203,213,225,.20); border-radius:10px; background:rgba(10,15,24,.95); box-shadow:0 16px 42px rgba(0,0,0,.40), inset 0 1px 0 rgba(255,255,255,.07); color:#dbeafe; font-size:11px; line-height:1.25; font-variant-numeric:tabular-nums; transform:translate(12px, calc(-100% - 12px)); opacity:0; transition:opacity .10s ease, transform .10s ease; pointer-events:none; backdrop-filter:blur(10px); }
    .practice-chart-hover-layer.place-left .practice-hover-tooltip { transform:translate(calc(-100% - 12px), calc(-100% - 12px)); }
    .practice-chart-hover-layer.place-bottom .practice-hover-tooltip { transform:translate(12px, 12px); }
    .practice-chart-hover-layer.place-left.place-bottom .practice-hover-tooltip { transform:translate(calc(-100% - 12px), 12px); }
    .practice-hover-tooltip-time { color:#f8fafc; font-weight:900; white-space:nowrap; }
    .practice-hover-tooltip-row { display:flex; align-items:baseline; justify-content:space-between; gap:12px; min-width:0; color:#8da0b8; white-space:nowrap; }
    .practice-hover-tooltip-row strong { color:#e5edf8; font-weight:900; }
    .practice-hover-tooltip-row strong.up { color:#ff4d4f; }
    .practice-hover-tooltip-row strong.down { color:#39d98a; }
    .practice-chart-hover-layer.active .practice-hover-line, .practice-chart-hover-layer.active .practice-hover-marker, .practice-chart-hover-layer.active .practice-hover-tooltip { opacity:1; }
    .practice-trade-marker { appearance:none; position:absolute; z-index:8; width:18px; height:18px; display:grid; place-items:center; padding:0; border-radius:999px; border:2px solid rgba(248,250,252,.92); color:#fff; font-size:9px; line-height:1; font-weight:950; font-family:inherit; cursor:help; transform:translate(-50%,-50%); box-shadow:0 4px 13px rgba(0,0,0,.40), 0 0 0 2px rgba(15,23,42,.62); }
    .practice-trade-marker.buy { background:#2563eb; }
    .practice-trade-marker.sell-partial { background:#f59e0b; color:#241500; }
    .practice-trade-marker.sell-full { background:#ef4444; }
    .practice-trade-marker.sell-mixed { background:#db2777; }
    .practice-trade-marker.mixed { background:#7c3aed; }
    .practice-trade-marker:focus-visible { outline:2px solid #f8fafc; outline-offset:2px; }
    .practice-trade-marker-tooltip { position:absolute; left:50%; bottom:calc(100% + 8px); z-index:3; width:max-content; min-width:190px; max-width:min(310px, calc(100vw - 44px)); display:grid; gap:4px; padding:8px 9px; border:1px solid rgba(203,213,225,.24); border-radius:10px; background:rgba(10,15,24,.97); box-shadow:0 16px 42px rgba(0,0,0,.46), inset 0 1px 0 rgba(255,255,255,.07); color:#e5edf8; font-size:11px; line-height:1.35; font-weight:750; font-variant-numeric:tabular-nums; text-align:left; white-space:nowrap; transform:translateX(-50%); opacity:0; visibility:hidden; pointer-events:none; backdrop-filter:blur(10px); }
    .practice-trade-marker.place-left .practice-trade-marker-tooltip { left:auto; right:-4px; transform:none; }
    .practice-trade-marker.place-right .practice-trade-marker-tooltip { left:-4px; transform:none; }
    .practice-trade-marker.place-bottom .practice-trade-marker-tooltip { top:calc(100% + 8px); bottom:auto; }
    .practice-trade-marker:hover .practice-trade-marker-tooltip, .practice-trade-marker:focus-visible .practice-trade-marker-tooltip { opacity:1; visibility:visible; }
    .practice-trade-marker-time { color:#94a3b8; font-size:10px; font-weight:850; }
    .practice-trade-marker-line { display:flex; align-items:baseline; gap:6px; color:#f8fafc; font-weight:800; }
    .practice-trade-marker-line + .practice-trade-marker-line { padding-top:3px; border-top:1px solid rgba(148,163,184,.14); }
    .practice-trade-marker-side { min-width:18px; display:inline-grid; place-items:center; padding:1px 4px; border-radius:5px; font-size:10px; line-height:1.35; font-weight:950; }
    .practice-trade-marker-line.buy .practice-trade-marker-side { color:#dbeafe; background:rgba(37,99,235,.34); box-shadow:inset 0 0 0 1px rgba(96,165,250,.32); }
    .practice-trade-marker-line.sell .practice-trade-marker-side { color:#fef3c7; background:rgba(217,119,6,.30); box-shadow:inset 0 0 0 1px rgba(251,191,36,.30); }
    .practice-trade-marker-stock { color:#f8fafc; font-weight:900; }
    .practice-trade-marker-fill { color:#bfdbfe; font-weight:800; }
    .practice-trade-marker-pnl { margin-left:auto; font-weight:900; }
    .practice-trade-marker-pnl.up { color:#ff6b6d; }
    .practice-trade-marker-pnl.down { color:#39d98a; }
    .benchmark-toggle-row { display:flex; gap:7px; flex-wrap:wrap; margin-top:8px; }
    .benchmark-toggle { cursor:pointer; user-select:none; border:1px solid rgba(255,255,255,.08); background:rgba(255,255,255,.035); color:#8a8f98; border-radius:999px; padding:4px 8px; font-size:11px; font-weight:800; display:inline-flex; align-items:center; gap:5px; }
    .benchmark-toggle.on { color:#f7f8f8; background:rgba(255,255,255,.07); }
    .benchmark-dot { width:7px; height:7px; border-radius:999px; background:var(--dot); box-shadow:0 0 10px var(--dot); }
    .position-card-list { display:grid; gap:8px; margin:0 0 12px; }
    .position-card { background:rgba(2,6,23,.42); border:1px solid rgba(148,163,184,.10); border-radius:14px; padding:12px 13px; }
    .position-metrics { min-width:0; display:grid; grid-template-columns:repeat(auto-fit, minmax(108px, 1fr)); gap:9px 12px; align-items:start; }
    .position-metric { min-width:0; }
    .position-label { font-size:11px; color:#64748b; line-height:1.2; white-space:nowrap; }
    .position-value { margin-top:3px; font-size:13px; line-height:1.28; color:#e2e8f0; font-variant-numeric:tabular-nums; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .position-value.combo { font-size:12.5px; }
    .position-value.strong { font-weight:800; }
    .position-brief-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(138px, 1fr)); gap:8px; margin:0 0 12px; }
    .position-brief-card { min-width:0; border:1px solid rgba(148,163,184,.11); border-radius:12px; background:rgba(2,6,23,.42); padding:10px 11px; display:grid; gap:8px; }
    .position-brief-name { min-width:0; color:#f8fafc; font-size:14px; line-height:1.25; font-weight:850; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .position-brief-stats { display:grid; gap:5px; }
    .position-brief-item { min-width:0; display:flex; align-items:baseline; justify-content:space-between; gap:8px; color:#64748b; font-size:11px; line-height:1.2; }
    .position-brief-item b { color:#e2e8f0; font-size:13px; font-weight:850; font-variant-numeric:tabular-nums; white-space:nowrap; }
    .position-reason-block { margin-top:9px; display:grid; gap:6px; color:#94a3b8; font-size:12px; line-height:1.55; }
    .position-reason-row { display:grid; grid-template-columns:64px minmax(0,1fr); gap:8px; align-items:start; min-width:0; }
    .position-reason-label { color:#64748b; font-weight:850; white-space:nowrap; }
    .position-reason-text { min-width:0; overflow-wrap:anywhere; color:#aebbd0; }
    .position-reason-badges { display:flex; gap:5px; flex-wrap:wrap; min-width:0; }
    .position-reason-badge { display:inline-flex; align-items:center; max-width:100%; border:1px solid rgba(148,163,184,.16); border-radius:7px; background:rgba(15,23,42,.54); color:#dbeafe; padding:1px 6px; font-weight:850; font-size:11px; line-height:1.5; }
    .practice-log-panel { margin-top:12px; border:1px solid rgba(148,163,184,.13); border-radius:14px; background:rgba(2,6,23,.36); overflow:hidden; box-shadow:inset 0 1px 0 rgba(255,255,255,.025); }
    .practice-log-head { display:flex; align-items:baseline; justify-content:space-between; gap:10px; padding:10px 12px; border-bottom:1px solid rgba(148,163,184,.10); background:rgba(15,23,42,.46); }
    .practice-log-title { color:#e2e8f0; font-size:13px; font-weight:850; }
    .practice-log-count { color:#7b8aa0; font-size:11px; font-weight:800; white-space:nowrap; }
    .practice-log-scroll { max-height:230px; overflow-y:auto; overscroll-behavior:contain; scrollbar-gutter:stable; padding:7px; display:grid; gap:6px; scrollbar-color:rgba(148,163,184,.36) rgba(15,23,42,.36); }
    .practice-log-scroll::-webkit-scrollbar { width:9px; }
    .practice-log-scroll::-webkit-scrollbar-track { background:rgba(15,23,42,.36); border-radius:999px; }
    .practice-log-scroll::-webkit-scrollbar-thumb { background:rgba(148,163,184,.36); border-radius:999px; border:2px solid rgba(15,23,42,.36); }
    .practice-log-row { width:100%; min-width:0; display:grid; grid-template-columns:62px 48px minmax(0,1fr); gap:8px; align-items:start; padding:8px 9px; border:1px solid rgba(148,163,184,.10); border-radius:10px; background:rgba(15,23,42,.42); color:inherit; text-align:left; cursor:pointer; }
    .practice-log-row:hover { border-color:rgba(191,219,254,.28); background:rgba(30,41,59,.52); }
    .practice-log-row:focus-visible { outline:2px solid rgba(157,178,255,.82); outline-offset:2px; }
    .practice-log-time { color:#94a3b8; font-size:12px; line-height:1.45; font-weight:850; font-variant-numeric:tabular-nums; white-space:nowrap; }
    .practice-log-badge { justify-self:start; border:1px solid rgba(148,163,184,.16); border-radius:7px; padding:1px 6px; color:#cbd5e1; background:rgba(30,41,59,.62); font-size:11px; line-height:1.55; font-weight:850; white-space:nowrap; }
    .practice-log-badge.buy { border-color:rgba(248,113,113,.24); color:#fecaca; background:rgba(127,29,29,.18); }
    .practice-log-badge.sell { border-color:rgba(52,211,153,.24); color:#bbf7d0; background:rgba(6,78,59,.18); }
    .practice-log-badge.decision { border-color:rgba(124,92,255,.28); color:#ddd6fe; background:rgba(76,29,149,.16); }
    .practice-log-main { min-width:0; display:grid; gap:3px; }
    .practice-log-summary { min-width:0; color:#dbeafe; font-size:12.5px; line-height:1.45; font-weight:800; overflow-wrap:anywhere; }
    .practice-log-detail { min-width:0; color:#8da0b8; font-size:11.5px; line-height:1.45; overflow-wrap:anywhere; }
    .practice-log-detail-backdrop { position:fixed; inset:0; z-index:86; display:grid; place-items:center; padding:18px; background:rgba(2,6,23,.68); backdrop-filter:blur(10px); }
    .practice-log-detail-card { width:min(640px, calc(100vw - 32px)); max-height:min(72vh, 640px); display:grid; grid-template-rows:auto minmax(0,1fr); overflow:hidden; border:1px solid rgba(148,163,184,.18); border-radius:16px; background:linear-gradient(180deg, rgba(15,23,42,.98), rgba(8,13,24,.98)); box-shadow:0 28px 90px rgba(0,0,0,.50), inset 0 1px 0 rgba(255,255,255,.06); }
    .practice-log-detail-head { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 14px; border-bottom:1px solid rgba(148,163,184,.12); background:rgba(30,41,59,.46); }
    .practice-log-detail-title { min-width:0; color:#e5edf8; font-size:14px; line-height:1.35; font-weight:900; overflow-wrap:anywhere; }
    .practice-log-detail-close { width:30px; height:30px; min-width:30px; display:grid; place-items:center; border-radius:9px; border:1px solid rgba(148,163,184,.18); background:rgba(15,23,42,.72); color:#cbd5e1; padding:0; line-height:1; font-size:16px; font-weight:850; cursor:pointer; }
    .practice-log-detail-close:hover { border-color:rgba(203,213,225,.42); background:rgba(30,41,59,.84); }
    .practice-log-detail-body { min-height:0; overflow-y:auto; padding:15px 16px 17px; scrollbar-color:rgba(148,163,184,.36) rgba(15,23,42,.36); }
    .practice-log-detail-body::-webkit-scrollbar { width:9px; }
    .practice-log-detail-body::-webkit-scrollbar-track { background:rgba(15,23,42,.36); border-radius:999px; }
    .practice-log-detail-body::-webkit-scrollbar-thumb { background:rgba(148,163,184,.36); border-radius:999px; border:2px solid rgba(15,23,42,.36); }
    .practice-log-detail-text { color:#dbeafe; font-size:13px; line-height:1.75; white-space:pre-wrap; overflow-wrap:anywhere; }
    .practice-rule-row { margin-top:10px; display:flex; align-items:center; gap:9px; flex-wrap:wrap; color:#94a3b8; font-size:12px; line-height:1.5; }
    .practice-rule-btn { flex:0 0 auto; display:inline-flex; align-items:center; justify-content:center; min-width:0; border:1px solid rgba(124,92,255,.30); border-radius:999px; background:rgba(124,92,255,.14); color:#e5edf8; padding:5px 10px; font-size:12px; line-height:1.3; font-weight:850; cursor:pointer; transition:.16s ease; }
    .practice-rule-btn:hover { border-color:rgba(157,178,255,.62); background:rgba(124,92,255,.22); transform:translateY(-1px); }
    .practice-rule-btn:focus-visible { outline:2px solid rgba(157,178,255,.86); outline-offset:2px; }
    .practice-rule-meta { min-width:0; color:#94a3b8; overflow-wrap:anywhere; }
    .practice-rule-backdrop { position:fixed; inset:0; z-index:85; display:grid; place-items:center; padding:18px; background:rgba(2,6,23,.66); backdrop-filter:blur(10px); }
    .practice-rule-card { width:min(640px, calc(100vw - 32px)); max-height:min(72vh, 620px); overflow:hidden; border:1px solid rgba(148,163,184,.18); border-radius:16px; background:linear-gradient(180deg, rgba(15,23,42,.98), rgba(8,13,24,.98)); box-shadow:0 28px 90px rgba(0,0,0,.50), inset 0 1px 0 rgba(255,255,255,.06); }
    .practice-rule-head { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 14px; border-bottom:1px solid rgba(148,163,184,.12); background:rgba(30,41,59,.46); }
    .practice-rule-title { color:#e5edf8; font-size:14px; font-weight:900; }
    .practice-rule-close { width:30px; height:30px; min-width:30px; display:grid; place-items:center; border-radius:9px; border:1px solid rgba(148,163,184,.18); background:rgba(15,23,42,.72); color:#cbd5e1; padding:0; line-height:1; font-size:16px; font-weight:850; cursor:pointer; }
    .practice-rule-close:hover { border-color:rgba(203,213,225,.42); background:rgba(30,41,59,.84); }
    .practice-rule-body { max-height:calc(min(72vh, 620px) - 55px); overflow-y:auto; padding:15px 16px 17px; color:#cbd5e1; font-size:13px; line-height:1.75; overflow-wrap:anywhere; scrollbar-color:rgba(148,163,184,.36) rgba(15,23,42,.36); }
    .practice-rule-body::-webkit-scrollbar { width:9px; }
    .practice-rule-body::-webkit-scrollbar-track { background:rgba(15,23,42,.36); border-radius:999px; }
    .practice-rule-body::-webkit-scrollbar-thumb { background:rgba(148,163,184,.36); border-radius:999px; border:2px solid rgba(15,23,42,.36); }
    .practice-calendar-popover { position:fixed; top:50%; left:50%; z-index:70; width:min(390px, calc(100vw - 36px)); max-height:min(62vh, 500px); overflow:visible; transform:translate(-50%,-50%); }
    .practice-calendar-day-curve { position:absolute; left:0; right:0; bottom:calc(100% + 8px); z-index:1; min-width:0; overflow:visible; border:1px solid transparent; border-radius:12px; padding:8px 9px 7px; background:linear-gradient(180deg, rgba(23,32,51,.98), rgba(15,23,42,.98)) padding-box, linear-gradient(135deg, rgba(96,165,250,.60), rgba(124,92,255,.46) 48%, rgba(52,211,153,.28)) border-box; box-shadow:0 18px 58px rgba(0,0,0,.48), inset 0 1px 0 rgba(255,255,255,.07); }
    .practice-calendar-day-curve-head { display:grid; grid-template-columns:minmax(0,1fr) auto auto; align-items:start; gap:8px; margin-bottom:4px; }
    .practice-calendar-day-curve-title { min-width:0; color:#e5edf8; font-size:12px; line-height:1.2; font-weight:850; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .practice-calendar-day-curve-sub { margin-top:2px; color:#7b8aa0; font-size:9px; line-height:1.2; font-variant-numeric:tabular-nums; }
    .practice-calendar-day-curve-value { color:#e2e8f0; font-size:11px; line-height:1.2; font-weight:850; font-variant-numeric:tabular-nums; white-space:nowrap; }
    .practice-calendar-day-curve-value.up { color:#ff4d4f; }
    .practice-calendar-day-curve-value.down { color:#39d98a; }
    .practice-calendar-day-curve-close { width:22px; height:22px; min-width:22px; display:grid; place-items:center; padding:0; border-radius:7px; border:1px solid rgba(191,219,254,.18); background:rgba(30,41,59,.76); color:#cbd5e1; font-size:13px; line-height:1; font-weight:850; }
    .practice-calendar-day-curve-chart { position:relative; width:100%; height:78px; }
    .practice-calendar-day-curve-svg { width:100%; height:100%; display:block; }
    .practice-calendar-day-curve-chart .practice-trade-marker { width:15px; height:15px; border-width:1.5px; font-size:7.5px; }
    .practice-calendar-day-curve-chart .practice-trade-marker-tooltip { min-width:176px; max-width:min(290px, calc(100vw - 52px)); padding:7px 8px; font-size:10px; }
    .practice-calendar-day-curve-empty { min-height:56px; display:grid; place-items:center; border:1px dashed rgba(148,163,184,.20); border-radius:8px; color:#7b8aa0; font-size:11px; }
    .practice-calendar-card { width:100%; max-height:inherit; min-height:0; display:grid; grid-template-rows:auto auto minmax(0,1fr); overflow:hidden; border:1px solid transparent; border-radius:12px; background:linear-gradient(180deg, #172033, #101827) padding-box, linear-gradient(135deg, rgba(96,165,250,.68), rgba(124,92,255,.56) 48%, rgba(52,211,153,.32)) border-box; box-shadow:0 24px 90px rgba(0,0,0,.58), 0 0 0 1px rgba(15,23,42,.72), inset 0 1px 0 rgba(255,255,255,.075); }
    .practice-calendar-head { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:9px 10px; border-bottom:1px solid rgba(191,219,254,.18); background:rgba(30,41,59,.48); }
    .practice-calendar-title { min-width:0; color:#e5edf8; font-size:13.5px; line-height:1.2; font-weight:850; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .practice-calendar-sub { margin-top:2px; color:#7b8aa0; font-size:10px; line-height:1.25; font-variant-numeric:tabular-nums; }
    .practice-calendar-actions { display:flex; align-items:center; gap:7px; flex:0 0 auto; }
    .practice-calendar-icon-btn { width:30px; height:30px; min-width:30px; display:grid; place-items:center; padding:0; border-radius:8px; border:1px solid rgba(191,219,254,.22); background:rgba(30,41,59,.82); color:#f8fafc; font-size:16px; line-height:1; font-weight:850; }
    .practice-calendar-icon-btn:hover { border-color:rgba(199,210,254,.56); background:rgba(51,65,85,.92); }
    .practice-calendar-summary { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:5px; padding:6px 8px; border-bottom:1px solid rgba(191,219,254,.16); background:rgba(15,23,42,.18); }
    .practice-calendar-stat { min-width:0; border:1px solid rgba(191,219,254,.16); border-radius:7px; padding:5px 6px; background:rgba(30,41,59,.58); }
    .practice-calendar-stat-label { color:#93a4bb; font-size:9px; line-height:1.15; font-weight:850; }
    .practice-calendar-stat-value { margin-top:2px; color:#e2e8f0; font-size:11px; line-height:1.2; font-weight:850; font-variant-numeric:tabular-nums; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .practice-calendar-grid-wrap { min-height:0; overflow:auto; padding:7px 8px 9px; }
    .practice-calendar-weekdays, .practice-calendar-grid { display:grid; grid-template-columns:repeat(5, minmax(0, 1.14fr)) repeat(2, minmax(30px, .72fr)); gap:3px; }
    .practice-calendar-weekdays { margin-bottom:4px; }
    .practice-calendar-weekday { color:#93a4bb; font-size:10px; line-height:1.1; font-weight:850; text-align:center; }
    .practice-calendar-weekday.weekend { color:#5f6f86; font-size:9px; }
    .practice-calendar-day { min-width:0; min-height:30px; display:grid; grid-template-rows:auto minmax(0,1fr); gap:2px; border:1px solid rgba(191,219,254,.13); border-radius:6px; padding:4px; background:rgba(31,42,62,.72); }
    .practice-calendar-day.has-result { min-height:52px; cursor:pointer; }
    .practice-calendar-day.has-result:hover { border-color:rgba(191,219,254,.36); box-shadow:inset 0 0 0 1px rgba(191,219,254,.12); }
    .practice-calendar-day.selected { border-color:rgba(191,219,254,.70) !important; box-shadow:inset 0 0 0 1px rgba(191,219,254,.28), 0 0 0 1px rgba(124,92,255,.20); }
    .practice-calendar-day.blank { visibility:hidden; }
    .practice-calendar-day.weekend { border-color:rgba(100,116,139,.15); background:rgba(15,23,42,.34); }
    .practice-calendar-day.has-result.up { border-color:rgba(248,113,113,.42); background:linear-gradient(180deg, rgba(127,29,29,.38), rgba(64,26,35,.72)); }
    .practice-calendar-day.has-result.down { border-color:rgba(52,211,153,.38); background:linear-gradient(180deg, rgba(6,78,59,.40), rgba(18,55,50,.72)); }
    .practice-calendar-day.has-result.flat { border-color:rgba(191,219,254,.22); background:rgba(51,65,85,.70); }
    .practice-calendar-date { display:flex; align-items:center; justify-content:space-between; gap:3px; color:#cbd5e1; font-size:10.5px; line-height:1; font-weight:850; font-variant-numeric:tabular-nums; }
    .practice-calendar-day.weekend .practice-calendar-date { justify-content:flex-start; color:#64748b; font-size:9px; font-weight:800; }
    .practice-calendar-today { border:1px solid rgba(96,165,250,.26); color:#bfdbfe; border-radius:999px; padding:1px 5px; font-size:9.5px; line-height:1.25; }
    .practice-calendar-day.weekend .practice-calendar-today.weekend-today { grid-row:2; align-self:end; justify-self:start; padding:0 3px; border-color:rgba(148,163,184,.22); color:#94a3b8; font-size:7px; line-height:1.2; }
    .practice-calendar-values { min-width:0; display:grid; align-content:end; gap:2px; font-variant-numeric:tabular-nums; }
    .practice-calendar-rate { font-size:9.5px; line-height:1.15; font-weight:850; white-space:nowrap; overflow:hidden; text-overflow:clip; }
    .practice-calendar-amount { color:#aebbd0; font-size:8.5px; line-height:1.18; white-space:nowrap; overflow:hidden; text-overflow:clip; }
    .practice-calendar-rate.up, .practice-calendar-amount.up, .practice-calendar-stat-value.up { color:#ff4d4f; }
    .practice-calendar-rate.down, .practice-calendar-amount.down, .practice-calendar-stat-value.down { color:#39d98a; }
    .practice-calendar-no-data { align-self:end; color:#708099; font-size:10px; line-height:1; }
    .practice-calendar-day.weekend .practice-calendar-no-data { display:none; }
    .mobile-only { display:none; }
    .empty { color:var(--muted); text-align:center; padding:42px; border:1px dashed var(--line); border-radius:18px; }
    .right { margin-left:auto; }
    a { color:#9db2ff; }
    .loading { text-align:center; padding:60px 20px; color:#64748b; font-size:16px; }
    .practice-curve { height:120px; width:100%; margin-top:8px; }
    @media (max-width: 720px) {
      body { background:var(--bg); }
      .compliance-notice { padding:9px 12px; font-size:11px; gap:5px; }
      .compliance-row { gap:6px; }
      .compliance-badge { padding:1px 7px; }
      header { position:static; padding:8px 9px 7px; backdrop-filter:none; background:rgba(6,7,10,.98); }
      .header-row { gap:8px; }
      .header-actions { gap:5px; }
      h1 { font-size:18px; line-height:1.06; letter-spacing:-.02em; }
      .settings-link, .header-link, .refresh-pill, .visit-pill { padding:5px 7px; gap:5px; }
      .settings-link, .header-link { font-size:12px; }
      .refresh-pill span, .visit-pill span { display:none; }
      .refresh-pill b, .visit-pill b { font-size:11px; }
      .practice-log-head { padding:8px 9px; }
      .practice-log-scroll { max-height:190px; padding:6px; }
      .practice-log-row { grid-template-columns:50px 42px minmax(0,1fr); gap:6px; padding:7px 8px; }
      .practice-log-time { font-size:11px; }
      .practice-log-badge { font-size:10.5px; padding:1px 5px; }
      .practice-log-summary { font-size:12px; }
      .practice-log-detail { font-size:11px; }
      .practice-rule-row { gap:6px; align-items:flex-start; }
      .practice-rule-btn { padding:4px 8px; font-size:11.5px; }
      .practice-rule-meta { font-size:11px; line-height:1.45; }
      .practice-rule-card { width:calc(100vw - 24px); max-height:76vh; border-radius:14px; }
      .practice-rule-head { padding:10px 11px; }
      .practice-rule-body { max-height:calc(76vh - 51px); padding:12px 12px 14px; font-size:12.5px; line-height:1.7; }
      .subtitle { display:none; }
      .category-tabs { margin:8px -9px 0; padding:0 9px 4px; overflow-x:auto; flex-wrap:nowrap; scrollbar-width:none; -webkit-overflow-scrolling:touch; scroll-snap-type:x proximity; }
      .category-tabs::-webkit-scrollbar { display:none; }
      .tab { flex:0 0 auto; padding:7px 10px; font-size:12px; scroll-snap-align:start; }
      .toolbar { display:grid; grid-template-columns:1fr; gap:7px; margin-top:7px; }
      input { grid-column:1 / -1; min-width:0; width:100%; }
      select { min-width:0; width:100%; padding:8px; font-size:13px; }
      #chat { display:none; }
      button { width:auto; min-width:78px; padding:8px 9px; font-size:13px; }
      main { padding:8px 8px 28px; }
      .feed { gap:11px; }
      .card { padding:13px 12px; border-radius:14px; box-shadow:0 4px 12px rgba(0,0,0,0.2); }
      .mobile-head { display:flex; justify-content:flex-end; gap:8px; align-items:flex-start; color:rgba(148,163,184,0.65); font-size:12px; margin-bottom:10px; }
      .mobile-head b { color:var(--text); font-size:14px; font-weight:700; }
      .mobile-head span { text-align:right; flex:0 0 auto; }
      .meta { display:none; }
      .pill { padding:4px 7px; border-radius:9px; min-width:0; }
      .platform { border-radius:999px; }
      .right { display:none; }
      .meta .pill:nth-child(2), .meta .pill:nth-child(3), .meta .pill:nth-child(4), .meta .pill:nth-child(5), .meta .pill:nth-child(6), .meta .pill:nth-child(7), .meta .pill:nth-child(8) { display:none; }
      .mobile-only { display:inline; }
      .content { font-size:15px; line-height:1.68; max-height:none; overflow:visible; padding-top:10px; }
      .post-header { font-size:10.5px; margin-bottom:9px; padding-bottom:8px; }
      .thread-card { gap:13px; }
      .thread-original { padding:12px 13px; border-radius:10px; margin-bottom:0; }
      .thread-original-content { font-size:14px; line-height:1.7; }
      .thread-original-content::first-line { font-size:10.5px; }
      .thread-reply-content { font-size:14.5px; line-height:1.65; }
      .market-monitor-grid { gap:9px; }
      .market-monitor-card { border-radius:14px; }
      .market-card-head { grid-template-columns:1fr; padding:12px 11px; gap:9px; }
      .market-card-title { font-size:14px; max-width:100%; white-space:normal; }
      .market-card-preview { font-size:13.5px; line-height:1.46; margin-top:6px; }
      .market-card-side { justify-content:space-between; align-items:center; }
      .market-type { font-size:11px; padding:4px 8px; }
      .market-card-detail { padding:0 11px 12px; }
      .market-detail-box { gap:11px; padding-top:11px; }
      .market-detail-overview { grid-template-columns:1fr; gap:9px; padding:9px; border-radius:10px; }
      .market-mood-panel { padding:9px 10px; border-radius:8px; }
      .market-mood-text { font-size:13.5px; line-height:1.5; }
      .market-metric-grid { grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px; }
      .market-metric-item { padding:7px 8px; border-radius:8px; }
      .market-metric-value { font-size:13px; }
      .market-section-list { grid-template-columns:1fr; gap:10px; }
      .market-section.wide { grid-column:auto; }
      .market-section { border-radius:9px; }
      .market-section-head { padding:9px 10px 8px 11px; }
      .market-section-body { padding:7px 10px 9px 11px; }
      .market-section-title { font-size:12.5px; }
      .market-section-icon { width:22px; height:22px; border-radius:7px; font-size:13px; }
      .market-detail-line { font-size:13.5px; line-height:1.58; }
      .market-detail-line.flow { grid-template-columns:38px minmax(0,1fr); gap:7px; padding:6px 0; }
      .market-detail-heading { font-size:12.5px; }
      .us-market-summary-card { border-radius:14px; padding:12px 11px; }
      .us-market-head { display:grid; grid-template-columns:1fr; gap:8px; margin-bottom:10px; }
      .us-market-title { font-size:14.5px; }
      .us-market-tone { justify-self:start; }
      .us-market-brief { font-size:13.5px; line-height:1.5; }
      .us-market-metrics { grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px; }
      .us-market-metric { padding:7px 8px; }
      .us-market-map-line { font-size:12px; line-height:1.42; padding:7px 8px; }
      .us-market-guidance-line { font-size:13px; line-height:1.48; }
      .market-day-pager { align-items:stretch; padding:10px 11px; gap:8px; }
      .market-day-title { font-size:13.5px; }
      .market-day-actions { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); width:100%; gap:7px; }
      .market-day-btn { width:100%; min-width:0; padding:8px 9px; }
      .market-day-btn:nth-child(1) { order:3; }
      .market-day-btn:nth-child(2) { order:1; }
      .market-day-btn:nth-child(3) { order:2; }
      .market-day-btn:nth-child(4) { order:4; }
      .x-monitor-panel { border-radius:15px; }
      .x-monitor-head { grid-template-columns:1fr; gap:8px; padding:10px 11px; }
      .x-monitor-title { font-size:13.5px; }
      .x-monitor-metrics { justify-content:flex-start; gap:6px; }
      .x-metric { font-size:11px; padding:4px 7px; }
      .x-row { grid-template-columns:36px minmax(0,1fr); gap:9px; padding:11px 10px; }
      .x-avatar { width:34px; height:34px; border-radius:11px; font-size:13px; }
      .x-author { max-width:100%; font-size:13.5px; }
      .x-preview { font-size:13.5px; line-height:1.44; }
      .x-media-strip { min-height:40px; gap:6px; }
      .x-media-thumb { width:58px; height:40px; border-radius:7px; }
      .x-media-grid { gap:8px; }
      .x-media-frame { height:clamp(220px, 68vh, 520px); padding:6px; }
      .x-image-viewer-backdrop { padding:8px; }
      .x-image-viewer-card { height:86vh; border-radius:13px; }
      .x-image-viewer-head { padding:8px; }
      .x-image-viewer-title { font-size:12px; }
      .x-image-viewer-btn { width:32px; height:32px; min-width:32px; }
      .x-badges { grid-column:2; justify-content:flex-start; }
      .x-chevron { display:none; }
      .x-detail { grid-column:1 / -1; margin-top:8px; padding:10px; border-radius:12px; }
      .x-pager { align-items:stretch; gap:8px; padding:10px 11px; }
      .x-pager-status { width:100%; font-size:12px; }
      .x-pager-actions { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); width:100%; gap:7px; }
      .x-page-btn { width:100%; min-width:0; padding:8px 9px; }
      .x-page-btn:nth-child(1) { order:3; }
      .x-page-btn:nth-child(2) { order:1; }
      .x-page-btn:nth-child(3) { order:2; }
      .x-page-btn:nth-child(4) { order:4; }
      .empty { padding:28px 12px; }
      .sector-cloud { padding:11px; border-radius:15px; margin-left:0; margin-right:0; }
      .sector-cloud h3 { font-size:13.5px; margin-bottom:7px; }
      .sector-columns { grid-template-columns:1fr; gap:12px; }
      .sector-cloud > div[style*="display:flex"] { flex-direction:column; gap:12px !important; }
      .sector-cloud div[style*="min-width:260px"], .sector-cloud div[style*="min-width:250px"] { min-width:0 !important; width:100%; }
      .sector-cloud .inline-field { padding:7px 8px; }
      .sector-cloud .inline-field .inline-value { font-size:13px; }
      .practice-stats { grid-template-columns:repeat(2,minmax(0,1fr)) !important; gap:7px !important; }
      .practice-calendar-open-btn { padding:5px 8px; font-size:11px; }
      .practice-chart-card { padding:11px 10px 8px; border-radius:15px; }
      .practice-chart-head { flex-direction:column; gap:8px; }
      .practice-chart-title-row { width:100%; justify-content:space-between; }
      .practice-chart-kpis { justify-content:stretch; width:100%; }
      .practice-kpi { flex:1; min-width:0; text-align:left; padding:6px 7px; }
      .practice-hover-tooltip { min-width:156px; max-width:min(220px, calc(100% - 16px)); padding:7px 8px; font-size:10.5px; }
      .practice-hover-tooltip-row { gap:9px; }
      .practice-chart-wrap { height:142px; }
      .practice-time-label { font-size:10px; bottom:2px; }
      .practice-axis-label.bot { bottom:29px; }
      .practice-zero-axis-label { font-size:9.5px; }
      .practice-current-line, .practice-hover-line { bottom:34px; }
      .practice-curve { height:60px !important; }
      .position-metrics { grid-template-columns:repeat(2, minmax(0, 1fr)); gap:8px 10px; }
      .position-brief-grid { grid-template-columns:repeat(2, minmax(0, 1fr)); gap:7px; }
      .position-brief-card { padding:9px 10px; gap:7px; }
      .position-brief-name { font-size:13px; }
      .position-brief-item b { font-size:12.5px; }
      .practice-calendar-popover { top:50% !important; left:50%; right:auto !important; bottom:auto; width:min(340px, calc(100vw - 42px)); max-height:64vh; transform:translate(-50%,-50%); }
      .practice-calendar-day-curve { bottom:calc(100% + 6px); padding:7px 8px 6px; border-radius:11px; }
      .practice-calendar-day-curve-head { gap:6px; margin-bottom:3px; }
      .practice-calendar-day-curve-title { font-size:11.5px; }
      .practice-calendar-day-curve-sub { font-size:8.5px; }
      .practice-calendar-day-curve-value { font-size:10px; }
      .practice-calendar-day-curve-chart { height:66px; }
      .practice-calendar-day-curve-empty { min-height:46px; font-size:10.5px; }
      .practice-calendar-card { border-radius:12px; }
      .practice-calendar-head { padding:8px 9px; gap:7px; }
      .practice-calendar-title { font-size:12.5px; }
      .practice-calendar-actions { gap:5px; }
      .practice-calendar-icon-btn { width:28px; height:28px; min-width:28px; }
      .practice-calendar-summary { grid-template-columns:repeat(3,minmax(0,1fr)); gap:3px; padding:6px 7px; }
      .practice-calendar-stat { padding:4px 5px; }
      .practice-calendar-stat-label { font-size:8.5px; }
      .practice-calendar-stat-value { font-size:10px; }
      .practice-calendar-grid-wrap { padding:6px 7px 8px; }
      .practice-calendar-weekdays, .practice-calendar-grid { grid-template-columns:repeat(5, minmax(0, 1.16fr)) repeat(2, minmax(26px, .62fr)); gap:3px; }
      .practice-calendar-day { min-height:28px; padding:3px; gap:2px; }
      .practice-calendar-day.has-result { min-height:48px; }
      .practice-calendar-date { font-size:10px; }
      .practice-calendar-day.weekend .practice-calendar-date { font-size:8.5px; }
      .practice-calendar-rate { font-size:9px; }
      .practice-calendar-amount { font-size:8px; }
      .market-strip { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:7px; margin:0 0 9px; }
      .indices-page { gap:11px; }
      .indices-switch { width:100%; }
      .indices-switch-btn { padding:8px 10px; font-size:13px; }
      .indices-part-title-row { gap:8px; }
      .indices-part-title { font-size:16px; }
      .market-region-switch { width:116px; }
      .market-region-btn { min-width:0; padding:6px 7px; font-size:11.5px; }
      .index-card { border-radius:13px; padding:8px 9px; min-width:0; box-shadow:none; }
      .index-name { font-size:11px; }
      .index-price { font-size:17px; }
      .index-change { font-size:11px; }
      .sparkline { height:34px; }
      .index-time { display:none; }
      .sector-grid { grid-template-columns:repeat(3, minmax(0, 1fr)); gap:6px; }
      .us-sector-cloud .sector-grid { grid-template-columns:repeat(2, minmax(0, 1fr)); }
      .us-sector-card { min-height:84px; }
      .sector-item, .hot-item { padding:8px 7px; border-radius:12px; min-width:0; }
      .sector-name { font-size:10.5px; letter-spacing:-.01em; }
      .sector-pct { font-size:12.5px; margin-top:3px; }
      .hot-price { font-size:12.5px; margin-top:3px; }
      .us-sector-map { font-size:10.5px; }
      .flow-val { display:block; margin-left:0; margin-top:2px; font-size:10.5px; }
      .sector-pct .flow-val { display:block; }
    }
    @media (max-width: 390px) {
      .tab { font-size:12px; padding:7px 9px; }
      .content { font-size:14px; }
    }
    /* ---- rating table styles ---- */
    .rating-card { padding:0; overflow:visible; background:linear-gradient(180deg, rgba(17,24,39,.96), rgba(8,11,18,.96)); border-color:rgba(99,102,241,.22); width:100%; max-width:100%; }
    .rating-table-wrap { margin:0; border:1px solid rgba(148,163,184,.16); border-radius:18px; overflow:hidden; background:rgba(2,6,23,.30); width:100%; max-width:100%; }
    .rating-table-title { display:flex; justify-content:space-between; gap:14px; align-items:center; padding:14px 16px; color:#c7d2fe; font-weight:850; font-size:15px; border-bottom:1px solid rgba(148,163,184,.12); background:rgba(99,102,241,.10); }
    .rating-table-title small { color:#94a3b8; font-weight:500; font-size:12px; white-space:nowrap; }
    .rating-table { width:100%; table-layout:fixed; border-collapse:collapse; font-size:16px; }
    .rating-table td,.rating-table th { padding:12px 16px; text-align:left; white-space:nowrap; border:none; border-bottom:1px solid rgba(148,163,184,.10); line-height:1.35; }
    .rating-table th { color:#8da0b8; font-size:13.5px; letter-spacing:.03em; font-weight:800; background:rgba(15,23,42,.45); }
    .rating-table thead th:nth-child(1) { width:16%; min-width:108px; }
    .rating-table thead th:nth-child(2) { width:18%; min-width:126px; }
    .rating-table thead th:nth-child(3) { width:42%; min-width:300px; }
    .rating-table thead th:nth-child(4) { width:24%; min-width:126px; }
    .rating-table td:nth-child(3) { white-space:normal; }
    .rating-action-inline { display:inline-block; color:#94a3b8; font-size:12.5px; line-height:1.35; font-weight:650; margin-left:8px; vertical-align:baseline; }
    .rating-table tr:last-child td { border-bottom:0; }
    .rating-table tbody tr.rating-data-row { cursor:pointer; transition:.14s ease; }
    .rating-table tbody tr.rating-data-row:hover { background:rgba(99,102,241,.12); }
    .rating-table tbody tr.rating-data-row.expanded { background:linear-gradient(90deg, rgba(67,56,202,.34), rgba(15,23,42,.68)); box-shadow:inset 4px 0 0 rgba(125,211,252,.92); }
    .rating-detail-row { display:none; }
    .rating-detail-row.open { display:table-row; }
    .rating-detail-cell { padding:0 !important; background:linear-gradient(180deg, rgba(8,13,34,.98), rgba(3,7,18,.98)); border-top:1px solid rgba(125,211,252,.24) !important; border-bottom:1px solid rgba(148,163,184,.18) !important; box-shadow:inset 0 1px 0 rgba(255,255,255,.035); }
    .rating-inline-detail { padding:18px 18px 20px; display:grid; gap:14px; }
    .rating-inline-grid { display:grid; grid-template-columns:repeat(12, minmax(0, 1fr)); gap:12px; align-items:stretch; }
    .rating-inline-grid .inline-field { border:1px solid rgba(148,163,184,.12); border-radius:13px; padding:12px 13px; background:rgba(2,6,23,.36); }
    .rating-inline-grid .inline-label { color:#8da0b8; font-size:11px; font-weight:800; letter-spacing:.05em; margin-bottom:5px; }
    .rating-inline-grid .inline-value { color:#e5edf8; font-size:14px; line-height:1.7; white-space:pre-wrap; }
    .rating-detail-company, .rating-detail-meta { grid-column:span 6; }
    .rating-detail-reason, .rating-detail-risk { grid-column:span 6; }
    .rating-table .ticker { color:#f8fafc; font-weight:900; font-size:17px; letter-spacing:.01em; }
    .rating-table .price { color:#e0f2fe; font-weight:850; }
    .rating-table .target { color:#d1fae5; font-weight:850; }
    .rating-table .upside { font-weight:850; }
    .rating-table .upside.pos { color:#fb7185; }
    .rating-table .upside.neg { color:#34d399; }
    .rating-table .muted { color:#64748b; font-weight:500; }
    @media (max-width: 720px) {
      .rating-card { width:100%; max-width:100%; background:transparent; border:0; box-shadow:none; }
      .rating-table-wrap { margin:0; width:100%; background:transparent; border:0; }
      .rating-table-title { padding:7px 9px; gap:8px; font-size:12px; border:1px solid rgba(148,163,184,.14); border-radius:12px 12px 0 0; background:rgba(99,102,241,.12); }
      .rating-table-title small { display:block; font-size:10px; }
      .rating-table { display:block; width:100%; font-size:13px; }
      .rating-table thead { display:none; }
      .rating-table tbody { display:grid; width:100%; gap:6px; padding:6px 0 0; }
      .rating-table tr.rating-data-row { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:5px 8px; padding:8px 9px; border:1px solid rgba(148,163,184,.14); border-radius:12px; background:linear-gradient(135deg, rgba(15,23,42,.72), rgba(30,41,59,.42)); box-shadow:0 4px 12px rgba(0,0,0,.14); }
      .rating-table tr.rating-data-row.expanded { background:linear-gradient(135deg, rgba(67,56,202,.30), rgba(15,23,42,.70)); border-color:rgba(125,211,252,.36); box-shadow:inset 3px 0 0 rgba(125,211,252,.88), 0 4px 12px rgba(0,0,0,.14); }
      .rating-table th, .rating-table td { padding:0; white-space:normal; min-width:0; }
      .rating-table td { display:flex; flex-direction:column; gap:2px; align-items:flex-start; overflow-wrap:anywhere; border-bottom:0; line-height:1.25; }
      .rating-table td::before { content:attr(data-label); color:#8da0b8; font-size:10px; font-weight:800; letter-spacing:.05em; }
      .rating-table td:nth-child(1) { grid-column:1; grid-row:1; }
      .rating-table td:nth-child(2) { grid-column:2; grid-row:1; align-items:flex-end; }
      .rating-table td:nth-child(3) { grid-column:1 / -1; grid-row:2; }
      .rating-table td:nth-child(4) { grid-column:1 / -1; grid-row:3; align-items:flex-end; }
      .rating-table .ticker { font-size:14px; }
      .rating-table .price, .rating-table .target, .rating-table .upside { font-size:13px; }
      .rating-action-inline { display:block; margin:2px 0 0; font-size:10.5px; line-height:1.25; }
      .rating-detail-row.open { display:block; }
      .rating-detail-row.open .rating-detail-cell { display:block; width:100%; border-radius:12px; overflow:hidden; }
      .rating-inline-grid { grid-template-columns:1fr; gap:6px; }
      .rating-detail-company, .rating-detail-meta, .rating-detail-reason, .rating-detail-risk { grid-column:auto; }
      .rating-inline-detail { padding:9px 9px 10px; gap:7px; }
      .rating-inline-grid .inline-field { padding:7px 8px; border-radius:10px; }
      .rating-inline-grid .inline-label { font-size:10px; margin-bottom:3px; }
      .rating-inline-grid .inline-value { font-size:12.5px; line-height:1.55; }
    }
  </style>
</head>
<body>
<section class="compliance-notice" aria-label="非荐股和入市风险提示">
  <div class="compliance-row"><span class="compliance-badge">非荐股提示</span><span class="compliance-text">本页面仅用于个人研究、模拟交易和信息展示，不构成证券、期货投资咨询、投资建议、荐股服务或任何买卖依据；不承诺收益，不代客理财，不收取荐股费用。</span></div>
  <div class="compliance-row"><span class="compliance-badge risk">入市风险提示</span><span class="compliance-text">证券、期货等投资存在本金损失风险，市场有涨有跌；请通过正规持牌机构独立判断、自主决策、风险自担。投资有风险，入市需谨慎。</span></div>
</section>
<header>
  <div class="header-row">
    <h1>牛牛1号</h1>
    <div class="header-actions">
      <a class="header-link" href="https://github.com/kunkundi/niuone" target="_blank" rel="noopener noreferrer" title="开源仓库" aria-label="打开 GitHub 开源仓库">
        <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <path d="M8 0C3.58 0 0 3.67 0 8.2c0 3.62 2.29 6.69 5.47 7.78.4.08.55-.18.55-.4 0-.2-.01-.85-.01-1.55-2.01.38-2.53-.5-2.69-.96-.09-.24-.48-.96-.82-1.15-.28-.15-.68-.52-.01-.53.63-.01 1.08.59 1.23.84.72 1.24 1.87.89 2.33.68.07-.53.28-.89.51-1.1-1.78-.21-3.64-.91-3.64-4.03 0-.89.31-1.62.82-2.19-.08-.21-.36-1.04.08-2.16 0 0 .67-.22 2.2.84A7.42 7.42 0 0 1 8 3.99c.68 0 1.36.09 2 .28 1.53-1.06 2.2-.84 2.2-.84.44 1.12.16 1.95.08 2.16.51.57.82 1.29.82 2.19 0 3.13-1.87 3.82-3.65 4.03.29.26.54.75.54 1.51 0 1.1-.01 1.98-.01 2.25 0 .22.15.48.55.4A8.12 8.12 0 0 0 16 8.2C16 3.67 12.42 0 8 0Z"/>
        </svg>
        <span>GitHub</span>
      </a>
      <a class="settings-link" href="/admin" title="进入设置页" aria-label="进入设置页">设置</a>
      <div class="visit-pill" title="累计首页访问人次"><span>访问人次</span><b id="visitCount">__VISIT_COUNT__</b></div>
      <div class="visit-pill" title="按浏览器匿名 Cookie 统计的唯一访客数"><span>访客数</span><b id="uniqueVisitCount">__UNIQUE_VISIT_COUNT__</b></div>
      <div class="refresh-pill" title="最后刷新"><span>最后刷新</span><b id="updated">--</b></div>
    </div>
  </div>
  <div id="categoryTabs" class="category-tabs"></div>
</header>
<main>
  <section id="feed" class="feed"><div class="loading">加载中…</div></section>
</main>
<script>
let data = {records: [], platforms: [], chats: [], categories: {}};
let indicesData = {};
let sectorData = {};
let usSectorData = {items: []};
let usQuotesData = {items: {}, symbols: []};
let usQuotesLoadingKey = '';
let hotStocksData = {};
let moneyFlowData = {inflow: [], outflow: []};
let marketFlowData = {total_inflow_yi: null, total_outflow_yi: null, net_flow_yi: null};
let usMarketSummaryData = {loading: true};
let b1ScreenData = {items: [], count: 0};
let niuniuPracticeData = {positions: [], equity_history: [], trade_log: [], decision_log: [], cash: 1000000, total_equity: 1000000};
let practiceBenchmarksData = {items: []};
let benchmarkOverlay = {sh000001: true, sh000300: true, sz399006: true, sh000688: true};
const initialParams = new URLSearchParams(location.search);
let activeCategory = initialParams.get('category') || 'b1_screen';
const US_FEATURES_ENABLED = __US_FEATURES_ENABLED__;
let indicesViewMode = initialParams.get('panel') === 'market' ? 'market' : 'index';
let indicesMarketRegionOverride = '';
const X_MONITOR_PAGE_SIZE = 10;
let usRatingDayIndex = 0;
let ratingExpandedRowId = '';
let xExpandedRecordKey = '';
let xImageViewer = {url: '', label: '', zoom: 1};
let marketExpandedRecordKey = '';
let marketDayIndex = Math.max(0, Number(initialParams.get('day') || 1) - 1);
let xPageOffset = Math.max(0, (Number(initialParams.get('page') || 1) - 1) * X_MONITOR_PAGE_SIZE);
let xLoadedOffset = -1;
let practiceCurveMode = initialParams.get('curve') === 'daily' ? 'daily' : 'intraday';
window.practiceCurveMode = practiceCurveMode;
let practicePositionMode = initialParams.get('holdings') === 'sold' ? 'sold' : 'open';
window.practicePositionMode = practicePositionMode;
let practicePositionBriefMode = initialParams.get('brief') === '1';
window.practicePositionBriefMode = practicePositionBriefMode;
let practiceLogDetailKey = '';
let practiceRuleNoteOpen = false;
let practiceCalendarOpen = false;
let practiceCalendarMonth = '';
let practiceCalendarSelectedDate = '';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const actionFetch = (url, options = {}) => fetch(url, {
  ...options,
  method: options.method || 'POST',
  headers: {'X-NiuOne-Action': '1', ...(options.headers || {})}
});
const fmtNumber = (v, d=2) => {
  const n = Number(v);
  return Number.isFinite(n) ? Number(n.toFixed(d)).toLocaleString('en') : '--';
};
const fmtAmount = v => {
  const n = Number(v);
  if (!Number.isFinite(n)) return '--';
  return Math.abs(n) >= 10000 ? (n/10000).toFixed(2) + '万' : n.toFixed(2);
};
const fmtSignedAmount = v => {
  const n = Number(v);
  return Number.isFinite(n) ? `${n >= 0 ? '+' : ''}${fmtAmount(n)}` : '--';
};
const fmtSignedPct = (v, d=2) => {
  const n = Number(v);
  return Number.isFinite(n) ? `${n >= 0 ? '+' : ''}${fmtNumber(n, d)}%` : '--';
};
const fmtDurationSeconds = s => {
  const n = Number(s);
  if (!Number.isFinite(n)) return '--';
  return n >= 3600 ? (n/3600).toFixed(1)+'h' : n >= 60 ? (n/60).toFixed(0)+'m' : n.toFixed(0)+'s';
};
function compactText(value, limit=120) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  return text.length > limit ? `${text.slice(0, Math.max(0, limit - 1)).trimEnd()}…` : text;
}
function practiceOperationLogDate(payload) {
  const generatedDate = String((payload || {}).generated_at || '').slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(generatedDate) ? generatedDate : localDateKey();
}
function practiceTradeLogEntry(trade, idx) {
  const action = String(trade.action || '').toUpperCase();
  const isBuy = action === 'BUY';
  const isSell = action === 'SELL';
  const actionLabel = isBuy ? '买入' : (isSell ? '卖出' : '成交');
  const codeName = [trade.code, trade.name].map(x => String(x || '').trim()).filter(Boolean).join(' ');
  const shares = trade.shares == null ? '' : `${trade.shares}股`;
  const price = Number(trade.price);
  const amount = Number(trade.amount);
  const pnl = Number(trade.pnl);
  const details = [
    Number.isFinite(price) ? `价 ${fmtNumber(price, 3)}` : '',
    shares,
    Number.isFinite(amount) ? `额 ${fmtAmount(amount)}` : '',
    isSell && Number.isFinite(pnl) ? `盈亏 ${pnl >= 0 ? '+' : ''}${fmtAmount(pnl)}` : '',
    compactText(trade.reason || trade.trade_reason || '', 100),
  ].filter(Boolean);
  return {
    key: `trade-${idx}`,
    time: String(trade.time || ''),
    kind: 'trade',
    raw: trade,
    badgeClass: isBuy ? 'buy' : (isSell ? 'sell' : 'trade'),
    badge: actionLabel,
    summary: `${actionLabel} ${codeName || '--'}${shares ? ` · ${shares}` : ''}`,
    detail: details.join('｜'),
    order: idx,
  };
}
function practiceExecutableActionCount(actions) {
  return actions.filter(action => {
    const act = String((action || {}).action || (action || {}).type || '').toUpperCase();
    return act === 'BUY' || act === 'SELL';
  }).length;
}
function practiceDecisionActionByCode(actions) {
  const byCode = new Map();
  actions.forEach(action => {
    const code = String((action || {}).code || '').trim();
    if (code) byCode.set(code, action || {});
  });
  return byCode;
}
function practiceBlockedReasons(decision, actions) {
  const raw = Array.isArray(decision.execution_blocked_reasons)
    ? decision.execution_blocked_reasons
    : (decision.execution_blocked_reason ? [decision.execution_blocked_reason] : []);
  const byCode = practiceDecisionActionByCode(actions);
  const seen = new Set();
  return raw.map(item => {
    const text = String(item || '').trim();
    if (!text || seen.has(text)) return '';
    seen.add(text);
    const match = text.match(/^(\d{6})[:：]\s*(.*)$/);
    if (!match) return text;
    const action = byCode.get(match[1]) || {};
    const name = String(action.name || '').trim();
    const subject = [match[1], name].filter(Boolean).join(' ');
    return `${subject}：${match[2] || '执行拦截'}`;
  }).filter(Boolean);
}
function practiceDecisionExecutionNote(entry, decisionTime) {
  const executed = Array.isArray(entry.executed) ? entry.executed : [];
  const times = executed.map(item => String((item || {}).time || '').slice(11, 19)).filter(Boolean);
  const uniqueTimes = [...new Set(times)];
  if (!uniqueTimes.length) return '';
  const first = uniqueTimes[0];
  const last = uniqueTimes[uniqueTimes.length - 1];
  const range = first === last ? first : `${first}-${last}`;
  return range && range !== String(decisionTime || '').slice(11, 19) ? `成交时间${range}` : '';
}
function practiceBuyRefinementText(decision) {
  const refinement = decision.buy_refinement || {};
  const dropped = Array.isArray(refinement.dropped) ? refinement.dropped : [];
  const kept = Array.isArray(refinement.kept_codes) ? refinement.kept_codes : [];
  if (!dropped.length && !kept.length) return '';
  const keptText = kept.length ? `保留${kept.join('、')}` : '未保留新仓';
  const droppedText = dropped.length
    ? `放弃${dropped.map(item => [item.code, item.name].filter(Boolean).join(' ')).filter(Boolean).join('、')}`
    : '';
  const summary = compactText(refinement.summary || refinement.reason || '', 90);
  return ['二次取舍', keptText, droppedText, summary].filter(Boolean).join('：');
}
function practiceDecisionLogEntry(entry, idx) {
  const decision = entry.decision || {};
  const actions = Array.isArray(decision.actions) ? decision.actions : [];
  const executed = Array.isArray(entry.executed) ? entry.executed : [];
  const suggestedCount = practiceExecutableActionCount(actions);
  const blockedReasons = practiceBlockedReasons(decision, actions);
  const statusParts = [
    suggestedCount ? `建议${suggestedCount}笔` : '',
    executed.length ? `执行${executed.length}笔` : '',
    blockedReasons.length ? `拦截${blockedReasons.length}笔` : '',
  ].filter(Boolean);
  const actionText = statusParts.length ? statusParts.join(' / ') : '无成交';
  const blockedText = blockedReasons.length ? `拦截：${compactText(blockedReasons.join('；'), 140)}` : '';
  const summary = compactText(decision.summary || entry.trade_reason || '模型决策', 120);
  const executionNote = practiceDecisionExecutionNote(entry, entry.time);
  const refinementText = practiceBuyRefinementText(decision);
  const details = [
    compactText(entry.trade_reason || '', 90),
    actionText,
    refinementText,
    blockedText,
    executionNote,
    decision.error ? compactText(decision.error, 90) : '',
  ].filter(Boolean);
  return {
    key: `decision-${idx}`,
    time: String(entry.time || ''),
    kind: 'decision',
    raw: entry,
    badgeClass: 'decision',
    badge: '决策',
    summary,
    detail: details.join('｜'),
    order: idx,
  };
}
function normalizePracticeOperationLogs(payload) {
  const p = payload || {};
  const date = practiceOperationLogDate(p);
  const entries = [];
  (p.trade_log || []).forEach((trade, idx) => {
    if (trade && String(trade.time || '').slice(0, 10) === date) {
      entries.push(practiceTradeLogEntry(trade, idx));
    }
  });
  (p.decision_log || []).forEach((entry, idx) => {
    if (entry && String(entry.time || '').slice(0, 10) === date) {
      entries.push(practiceDecisionLogEntry(entry, idx + 10000));
    }
  });
  return entries.sort((a, b) => String(b.time || '').localeCompare(String(a.time || '')) || a.order - b.order);
}
function renderPracticeOperationLog(payload) {
  const date = practiceOperationLogDate(payload);
  const entries = normalizePracticeOperationLogs(payload);
  const rows = entries.length ? entries.map(item => `
    <button type="button" class="practice-log-row" data-practice-log-key="${esc(item.key)}" title="查看完整日志" aria-label="查看完整日志：${esc(item.summary)}">
      <div class="practice-log-time">${esc(String(item.time || '').slice(11, 19) || '--')}</div>
      <div class="practice-log-badge ${esc(item.badgeClass)}">${esc(item.badge)}</div>
      <div class="practice-log-main">
        <div class="practice-log-summary">${esc(item.summary)}</div>
        ${item.detail ? `<div class="practice-log-detail">${esc(item.detail)}</div>` : ''}
      </div>
    </button>`).join('') : '<div class="empty" style="padding:18px;font-size:13px">当日暂无操作日志</div>';
  return `<div class="practice-log-panel">
    <div class="practice-log-head">
      <div class="practice-log-title">操作日志</div>
      <div class="practice-log-count">${esc(date)} · ${entries.length}条</div>
    </div>
    <div class="practice-log-scroll" tabindex="0" role="region" aria-label="当日所有操作日志">${rows}</div>
  </div>`;
}
function practiceLogTextValue(value) {
  if (value === null || value === undefined) return '';
  if (Array.isArray(value)) return value.map(practiceLogTextValue).filter(Boolean).join('；');
  if (typeof value === 'object') return practiceLogTextValue(value.summary || value.reason || value.detail || '');
  return String(value || '').trim();
}
function practiceLogRawText(item) {
  const raw = item && item.raw && typeof item.raw === 'object' ? item.raw : {};
  if (item.kind === 'trade') {
    return practiceLogTextValue(raw.reason || raw.trade_reason || item.detail || item.summary);
  }
  const decision = raw.decision && typeof raw.decision === 'object' ? raw.decision : {};
  const textParts = [
    practiceLogTextValue(decision.summary),
    practiceLogTextValue(raw.trade_reason),
    practiceLogTextValue(decision.execution_blocked_reasons || decision.execution_blocked_reason),
    practiceLogTextValue(decision.buy_refinement),
    practiceLogTextValue(decision.error),
  ].filter(Boolean);
  return [...new Set(textParts)].join('\n\n') || item.detail || item.summary || '无原文';
}
function renderPracticeLogDetailModal(payload) {
  if (!practiceLogDetailKey) return '';
  const item = normalizePracticeOperationLogs(payload).find(entry => entry.key === practiceLogDetailKey);
  if (!item) return '';
  const text = practiceLogRawText(item);
  return `<div class="practice-log-detail-backdrop" role="presentation">
    <div class="practice-log-detail-card" role="dialog" aria-modal="true" aria-label="完整操作日志">
      <div class="practice-log-detail-head">
        <div class="practice-log-detail-title">${esc(item.summary || '完整操作日志')}</div>
        <button type="button" class="practice-log-detail-close" data-practice-log-action="close" title="关闭" aria-label="关闭">x</button>
      </div>
      <div class="practice-log-detail-body">
        <div class="practice-log-detail-text">${esc(text)}</div>
      </div>
    </div>
  </div>`;
}
function practiceRuleFallbackNote() {
  return '100股整数倍、T+1；09:15-09:25只作开盘集合竞价观察，09:25-09:30不模拟成交。';
}
function renderPracticeRuleNoteModal(note) {
  if (!practiceRuleNoteOpen) return '';
  const text = String(note || practiceRuleFallbackNote()).trim();
  return `<div class="practice-rule-backdrop" role="presentation">
    <div class="practice-rule-card" role="dialog" aria-modal="true" aria-label="交易规则">
      <div class="practice-rule-head">
        <div class="practice-rule-title">交易规则</div>
        <button type="button" class="practice-rule-close" data-practice-rule-action="close" title="关闭" aria-label="关闭">x</button>
      </div>
      <div class="practice-rule-body">${esc(text)}</div>
    </div>
  </div>`;
}
const upCls = v => v > 0 ? 'up' : v < 0 ? 'down' : 'flat';
const CATEGORY_ORDER = ['b1_screen', 'indices', 'market_monitor', 'x_monitor', 'us_ratings'];
const CATEGORY_LABELS = {all:'全部', indices:'指数行情', b1_screen:'牛牛实战', us_ratings:'美股机构买入评级', x_monitor:'推特监控', market_monitor:'盘面监控', other:'其他'};
const US_FEATURE_CATEGORIES = new Set(['x_monitor', 'us_ratings']);
const MESSAGE_CATEGORIES = ['x_monitor', 'market_monitor', 'us_ratings'];
function categoryAvailable(category) {
  return !US_FEATURE_CATEGORIES.has(category) || US_FEATURES_ENABLED;
}
function visibleCategoryOrder() {
  return CATEGORY_ORDER.filter(categoryAvailable);
}
function normalizeActiveCategory(category) {
  return visibleCategoryOrder().includes(category) ? category : 'b1_screen';
}
activeCategory = normalizeActiveCategory(activeCategory);
const VIEW_STATE_KEY = 'niuniu-dashboard-view-state-v3';
const DATA_CACHE_TTL_MS = 30000;
const AUTO_REFRESH_TICK_MS = 15000;
const US_RATINGS_AUTO_REFRESH_MS = 10 * 60 * 1000;
let loadSeq = 0;
let pendingLoadController = null;
let loadingMoreHistory = false;
let lastAutoRefreshAt = 0;
function saveViewState() {
  try {
    sessionStorage.setItem(VIEW_STATE_KEY, JSON.stringify({
      data, indicesData, sectorData, usSectorData, hotStocksData, moneyFlowData, marketFlowData,
      usMarketSummaryData, b1ScreenData, niuniuPracticeData, practiceBenchmarksData, usQuotesData,
      xPageOffset, xLoadedOffset, practiceCurveMode, practicePositionMode, practicePositionBriefMode, indicesViewMode,
      savedAt: Date.now()
    }));
  } catch (e) {}
}
function restoreViewState() {
  try {
    const cached = JSON.parse(sessionStorage.getItem(VIEW_STATE_KEY) || '{}');
    if (!cached.savedAt || Date.now() - cached.savedAt > DATA_CACHE_TTL_MS) return;
    data = cached.data || data;
    indicesData = cached.indicesData || indicesData;
    sectorData = cached.sectorData || sectorData;
    usSectorData = cached.usSectorData || usSectorData;
    hotStocksData = cached.hotStocksData || hotStocksData;
    moneyFlowData = cached.moneyFlowData || moneyFlowData;
    marketFlowData = cached.marketFlowData || marketFlowData;
    usMarketSummaryData = cached.usMarketSummaryData || usMarketSummaryData;
    b1ScreenData = cached.b1ScreenData || b1ScreenData;
    niuniuPracticeData = cached.niuniuPracticeData || niuniuPracticeData;
    practiceBenchmarksData = cached.practiceBenchmarksData || practiceBenchmarksData;
    usQuotesData = cached.usQuotesData || usQuotesData;
    if (!initialParams.has('curve') && ['intraday', 'daily'].includes(cached.practiceCurveMode)) {
      practiceCurveMode = cached.practiceCurveMode;
      window.practiceCurveMode = practiceCurveMode;
    }
    if (!initialParams.has('holdings') && ['open', 'sold'].includes(cached.practicePositionMode)) {
      practicePositionMode = cached.practicePositionMode;
      window.practicePositionMode = practicePositionMode;
    }
    if (!initialParams.has('brief') && typeof cached.practicePositionBriefMode === 'boolean') {
      practicePositionBriefMode = cached.practicePositionBriefMode;
      window.practicePositionBriefMode = practicePositionBriefMode;
    }
    if (!initialParams.has('panel') && ['index', 'market'].includes(cached.indicesViewMode)) {
      indicesViewMode = cached.indicesViewMode;
    }
    if (!initialParams.has('page')) xPageOffset = Math.max(0, Number(cached.xPageOffset || 0));
    xLoadedOffset = Number.isFinite(Number(cached.xLoadedOffset)) ? Number(cached.xLoadedOffset) : -1;
  } catch (e) {}
}
function hasWarmData(category) {
  if (category === 'indices') return Array.isArray(indicesData.items) && indicesData.items.length;
  if (category === 'b1_screen') return (Array.isArray(b1ScreenData.items) && b1ScreenData.items.length) || Array.isArray(niuniuPracticeData.equity_history);
  if (category === 'x_monitor') return xLoadedOffset === xPageOffset && (data.records || []).some(r => r.category === category);
  if (isMessageCategory(category)) return (data.records || []).some(r => r.category === category);
  return false;
}
function optionize(select, values, label) {
  const current = select.value;
  select.innerHTML = `<option value="">${label}</option>` + values.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
  select.value = values.includes(current) ? current : '';
}
function isMessageCategory(category = activeCategory) {
  return MESSAGE_CATEGORIES.includes(category);
}
function messagePageLimit(category = activeCategory) {
  if (category === 'us_ratings') return 120;
  if (category === 'x_monitor') return X_MONITOR_PAGE_SIZE;
  if (category === 'market_monitor') return 200;
  return 80;
}
function currentViewUrl() {
  const params = new URLSearchParams();
  params.set('category', activeCategory);
  if (activeCategory === 'x_monitor' && xPageOffset > 0) {
    params.set('page', String(Math.floor(xPageOffset / messagePageLimit('x_monitor')) + 1));
  }
  if (activeCategory === 'market_monitor' && marketDayIndex > 0) {
    params.set('day', String(marketDayIndex + 1));
  }
  if (activeCategory === 'indices' && indicesViewMode === 'market') {
    params.set('panel', 'market');
  }
  if (activeCategory === 'b1_screen' && practicePositionMode === 'sold') {
    params.set('holdings', 'sold');
  }
  if (activeCategory === 'b1_screen' && practicePositionBriefMode) {
    params.set('brief', '1');
  }
  return '/?' + params.toString();
}
function syncViewUrl() {
  history.replaceState(null, '', currentViewUrl());
}
function messageOffset(category = activeCategory) {
  return category === 'x_monitor' ? xPageOffset : 0;
}
function recordKey(r) {
  return String(r.id || r.raw_path || r.external_id || `${r.category || ''}:${r.session_id || ''}:${r.timestamp || ''}:${(r.content || '').slice(0, 80)}`);
}
function mergeRecordLists(primary, secondary) {
  const seen = new Set();
  const merged = [];
  for (const r of [...(primary || []), ...(secondary || [])]) {
    const key = recordKey(r);
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(r);
  }
  return merged.sort((a, b) => {
    const at = Number(a.timestamp || Date.parse(a.time || 0) / 1000 || 0);
    const bt = Number(b.timestamp || Date.parse(b.time || 0) / 1000 || 0);
    return bt - at;
  });
}
function activeCategoryTotal() {
  if (isMessageCategory()) return Number(data.categories?.[activeCategory]?.count || 0);
  return Number(data.total || 0);
}
function messagesUrl(offset = messageOffset(), limit = messagePageLimit()) {
  const msgCategory = isMessageCategory() ? activeCategory : '';
  return `/api/messages?limit=${limit}&offset=${offset}${msgCategory ? '&category=' + encodeURIComponent(msgCategory) : ''}`;
}
function autoRefreshIntervalMs(category = activeCategory) {
  return category === 'us_ratings' ? US_RATINGS_AUTO_REFRESH_MS : AUTO_REFRESH_TICK_MS;
}
async function autoRefresh() {
  if (Date.now() - lastAutoRefreshAt < autoRefreshIntervalMs()) return;
  await load({background:true});
}
async function load({background=false} = {}) {
  const seq = ++loadSeq;
  if (pendingLoadController) pendingLoadController.abort();
  const controller = new AbortController();
  pendingLoadController = controller;
  const msgUrl = messagesUrl(isMessageCategory() ? messageOffset() : 0, isMessageCategory() ? messagePageLimit() : 0);
  if (!background && !hasWarmData(activeCategory)) {
    $('feed').innerHTML = '<div class="loading">加载中…</div>';
  }
  const res = await fetch(msgUrl, {signal: controller.signal});
  const nextData = await res.json();
  if (seq !== loadSeq) return;
  data = background && isMessageCategory() && activeCategory !== 'x_monitor'
    ? {...nextData, records: mergeRecordLists(nextData.records || [], data.records || [])}
    : nextData;
  if (activeCategory === 'x_monitor') xLoadedOffset = xPageOffset;
  $('updated').textContent = data.generated_at?.slice(11) || '--';
  renderTabs();
  if (activeCategory === 'indices') { loadIndices(); }
  if (activeCategory === 'b1_screen') { loadB1Screen(); }
  if (activeCategory === 'market_monitor') { loadIndicesDataInBg(); }
  if (seq !== loadSeq) return;
  render();
  if (activeCategory === 'us_ratings') refreshVisibleUsQuotes();
  lastAutoRefreshAt = Date.now();
  saveViewState();
}
async function loadMoreMessages() {
  if (!isMessageCategory() || activeCategory === 'us_ratings' || activeCategory === 'x_monitor' || loadingMoreHistory) return;
  loadingMoreHistory = true;
  render();
  try {
    const offset = (data.records || []).length;
    const res = await fetch(messagesUrl(offset, messagePageLimit()));
    const nextData = await res.json();
    data = {
      ...nextData,
      records: mergeRecordLists(data.records || [], nextData.records || []),
      categories: nextData.categories || data.categories || {},
      platforms: nextData.platforms || data.platforms || [],
      chats: nextData.chats || data.chats || [],
      total: nextData.total || data.total,
      generated_at: nextData.generated_at || data.generated_at,
    };
    saveViewState();
  } finally {
    loadingMoreHistory = false;
    render();
  }
}
async function loadXPage(nextOffset) {
  if (activeCategory !== 'x_monitor' || loadingMoreHistory) return;
  const limit = messagePageLimit('x_monitor');
  const total = activeCategoryTotal();
  const maxOffset = total ? Math.max(0, (Math.ceil(total / limit) - 1) * limit) : Math.max(0, Number(nextOffset || 0));
  const targetOffset = Math.max(0, Math.min(Number(nextOffset || 0), maxOffset));
  if (targetOffset === xPageOffset && xLoadedOffset === xPageOffset) return;
  const previousOffset = xPageOffset;
  xPageOffset = targetOffset;
  xExpandedRecordKey = '';
  syncViewUrl();
  loadingMoreHistory = true;
  render();
  try {
    await load({background:true});
  } catch (err) {
    xPageOffset = previousOffset;
    syncViewUrl();
    throw err;
  } finally {
    loadingMoreHistory = false;
    render();
  }
}
function ratingSymbolsFromRecords(records) {
  const symbols = new Set();
  for (const r of records || []) {
    const parsed = parseRatingReport(r.content || '');
    if (!parsed) continue;
    for (const item of parsed.items) {
      const ticker = String((item.name || '').split('/')[0] || '').trim().toUpperCase();
      if (/^[A-Z][A-Z0-9.]{1,8}$/.test(ticker)) symbols.add(ticker);
    }
  }
  return [...symbols];
}
async function loadUsQuotes(records = currentUsRatingRecords()) {
  const symbols = ratingSymbolsFromRecords(records);
  const cachedItems = usQuotesData.items || {};
  const missing = symbols.filter(symbol => !cachedItems[symbol]);
  if (!missing.length) return false;
  const symList = missing.join(',');
  if (usQuotesLoadingKey === symList) return false;
  usQuotesLoadingKey = symList;
  try {
    const res = await fetch(`/api/us_quotes?symbols=${encodeURIComponent(symList)}`);
    const nextQuotes = await res.json();
    usQuotesData = {
      ...nextQuotes,
      items: {...(usQuotesData.items || {}), ...((nextQuotes && nextQuotes.items) || {})},
      symbols: [...new Set([...(usQuotesData.symbols || []), ...((nextQuotes && nextQuotes.symbols) || missing)])],
    };
    saveViewState();
    return true;
  } catch (e) {
    console.error('us quotes load error', e);
    return false;
  } finally {
    if (usQuotesLoadingKey === symList) usQuotesLoadingKey = '';
  }
}
function refreshVisibleUsQuotes() {
  if (activeCategory !== 'us_ratings') return;
  const records = currentUsRatingRecords();
  loadUsQuotes(records).then(changed => {
    if (!changed || activeCategory !== 'us_ratings') return;
    render();
    restoreRatingDetail();
    saveViewState();
  }).catch(e => console.error('us quotes refresh error', e));
}
async function loadIndicesDataInBg() {
  const fetchJson = (url, fallback) => fetch(url).then(r => r.ok ? r.json() : fallback);
  const refreshMarket = () => {
    if (activeCategory === 'market_monitor') render();
    saveViewState();
  };
  const applyResult = (label, promise, onData, onError) => promise.then(data => {
    onData(data);
    refreshMarket();
    return data;
  }).catch(e => {
    console.error(label + ' load error', e);
    if (onError) onError(e);
    refreshMarket();
    return null;
  });
  if (!usMarketSummaryData.generated_at && !usMarketSummaryData.summary) {
    usMarketSummaryData = {...usMarketSummaryData, loading: true};
  }
  const tasks = [
    applyResult('indices', fetchJson('/api/indices', {}), data => { indicesData = data || {}; }),
    applyResult('hot stocks', fetchJson('/api/hot_stocks', {}), data => { hotStocksData = data || {}; }),
    applyResult('us sectors', fetchJson('/api/us_sectors', {items: []}), data => { usSectorData = data || {items: []}; }),
    applyResult('money flow', fetchJson('/api/money_flow', {inflow: [], outflow: []}), data => { moneyFlowData = data || {inflow: [], outflow: []}; }),
    applyResult('market flow', fetchJson('/api/market_flow', {total_inflow_yi: null}), data => { marketFlowData = data || {total_inflow_yi: null}; }),
    applyResult(
      'us market summary',
      fetchJson('/api/us_market_summary', {available:false, error:'load_failed'}),
      data => { usMarketSummaryData = data || {available:false}; },
      e => { usMarketSummaryData = {available:false, error:String(e), loading:false}; }
    ),
  ];
  await Promise.allSettled(tasks);
}
async function loadIndices() {
  try {
    const idxPromise = fetch('/api/indices').then(r => r.ok ? r.json() : {items: []});
    const secPromise = fetch('/api/sectors').then(r => r.ok ? r.json() : {sectors: []});
    const usSecPromise = fetch('/api/us_sectors').then(r => r.ok ? r.json() : {items: []});
    const hotPromise = fetch('/api/hot_stocks').then(r => r.ok ? r.json() : {items: []});
    const mfPromise = fetch('/api/money_flow').then(r => r.ok ? r.json() : {inflow: [], outflow: []});
    const mkfPromise = fetch('/api/market_flow').then(r => r.ok ? r.json() : {total_inflow_yi: null});
    const idx = await idxPromise;
    indicesData = idx || {items: []};
    if (activeCategory === 'indices') render();
    saveViewState();
    const [sec, usSec, hot, mf, mkf] = await Promise.all([secPromise, usSecPromise, hotPromise, mfPromise, mkfPromise]);
    sectorData = sec || sectorData || {sectors: []};
    usSectorData = usSec || usSectorData || {items: []};
    hotStocksData = hot || hotStocksData || {items: []};
    moneyFlowData = mf || moneyFlowData || {inflow: [], outflow: []};
    marketFlowData = mkf || marketFlowData || {total_inflow_yi: null};
    if (activeCategory === 'indices') render();
    saveViewState();
  } catch(e) {
    console.error('indices load error', e);
    indicesData = {items: [], error: String(e)};
    if (activeCategory === 'indices') render();
  }
}
async function loadB1Screen() {
  try {
    const b1Promise = fetch('/api/b1_screen').then(r => r.ok ? r.json() : {items:[],count:0});
    const fastPracticePromise = fetch('/api/niuniu_practice?fast=1').then(r => r.ok ? r.json() : {positions:[],cash:1000000,total_equity:1000000});
    const benchmarksPromise = fetch('/api/practice_benchmarks').then(r => r.ok ? r.json() : {items:[]});
    const [b1Raw, p] = await Promise.all([b1Promise, fastPracticePromise]);
    const b1Items = b1Raw.items || b1Raw.candidates || [];
    const b1 = {...b1Raw, items:b1Items, count:b1Raw.count || b1Items.length};
    niuniuPracticeData = p;
    b1ScreenData = b1;
    if (activeCategory === 'b1_screen') render();
    saveViewState();
    benchmarksPromise.then(bm => {
      practiceBenchmarksData = bm || {items: []};
      if (activeCategory === 'b1_screen') render();
      saveViewState();
    }).catch(e => console.error('practice benchmarks load error', e));
    fetch('/api/niuniu_practice').then(r => r.ok ? r.json() : null).then(full => {
      if (!full) return;
      niuniuPracticeData = full;
      if (activeCategory === 'b1_screen') render();
      saveViewState();
    }).catch(e => console.error('practice full load error', e));
  } catch(e) { console.error('b1 screen load error', e); }
}
function renderTabs() {
  $('categoryTabs').innerHTML = visibleCategoryOrder().map(key => {
    const count = (key === 'indices' || key === 'b1_screen') ? '' : ` · ${data.categories?.[key]?.count || 0}`;
    return `<a class="tab ${activeCategory === key ? 'active' : ''}" data-category="${key}" href="/?category=${encodeURIComponent(key)}">${CATEGORY_LABELS[key]}${count}</a>`;
  }).join('');
  document.querySelectorAll('.tab[data-category]').forEach(tab => tab.onclick = (event) => {
    event.preventDefault();
    const nextCategory = tab.dataset.category;
    if (!nextCategory || !categoryAvailable(nextCategory) || nextCategory === activeCategory) return;
    activeCategory = nextCategory;
    usRatingDayIndex = 0;
    ratingExpandedRowId = '';
    xExpandedRecordKey = '';
    marketExpandedRecordKey = '';
    if (activeCategory === 'market_monitor') marketDayIndex = 0;
    if (activeCategory === 'x_monitor') {
      xPageOffset = 0;
      xLoadedOffset = -1;
    }
    syncViewUrl();
    renderTabs();
    // Immediate optimistic switch: show cached/placeholder page in the same click frame,
    // then hydrate with fresh API data. This removes the perceived "button pressed but
    // nothing happens" delay when a heavy endpoint is cold.
    if (hasWarmData(activeCategory)) render();
    else $('feed').innerHTML = '<div class="loading">加载中…</div>';
    load({background:true}).catch(err => {
      if (err && err.name === 'AbortError') return;
      console.error(err);
    });
  });
}
function filtered() {
  return (data.records || []).filter(r => {
    if (activeCategory !== 'all' && r.category !== activeCategory) return false;
    return true;
  }).sort((a, b) => {
    const at = Number(a.timestamp || Date.parse(a.time || 0) / 1000 || 0);
    const bt = Number(b.timestamp || Date.parse(b.time || 0) / 1000 || 0);
    return bt - at;
  });
}
function clamp01(v) {
  return Math.max(0, Math.min(1, v));
}
function clockMinuteOfDay(timeText) {
  const m = String(timeText || '').match(/(\d{1,2}):(\d{2})/);
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}
function globalSessionElapsedMinute(clockMinute, sessionStartMinute) {
  if (clockMinute == null || sessionStartMinute == null) return null;
  let elapsed = clockMinute - sessionStartMinute;
  if (elapsed < 0) elapsed += 24 * 60;
  return elapsed;
}
function marketClockParts(timeZone) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone, hour12:false, weekday:'short', hour:'2-digit', minute:'2-digit'
  }).formatToParts(new Date());
  const pick = type => parts.find(p => p.type === type)?.value || '';
  let hour = Number(pick('hour'));
  const minute = Number(pick('minute'));
  if (hour === 24) hour = 0;
  return {weekday: pick('weekday'), minuteOfDay: hour * 60 + minute};
}
function isWeekdayClock(clock) {
  return ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'].includes(clock.weekday);
}
function isAShareOpenNow() {
  const c = marketClockParts('Asia/Shanghai');
  const m = c.minuteOfDay;
  return isWeekdayClock(c) && ((m >= 9 * 60 + 15 && m <= 11 * 60 + 30) || (m >= 13 * 60 && m <= 15 * 60));
}
function isAShareDaySessionNow() {
  const c = marketClockParts('Asia/Shanghai');
  const m = c.minuteOfDay;
  return isWeekdayClock(c) && m >= 9 * 60 + 15 && m <= 15 * 60;
}
function isUsOpenNow() {
  const c = marketClockParts('America/New_York');
  const m = c.minuteOfDay;
  return isWeekdayClock(c) && m >= 9 * 60 + 30 && m <= 16 * 60;
}
function indicesSwitchSession(aIndexItems = []) {
  const hasAIndexItems = !Array.isArray(aIndexItems) || aIndexItems.length > 0;
  if (isAShareOpenNow() || (isAShareDaySessionNow() && hasAIndexItems)) return 'a_share';
  if (isUsOpenNow()) return 'us_open';
  return 'global';
}
function compressedGlobalSessionProgresses(minuteLine, sessionStartMinute) {
  const rows = [];
  (minuteLine || []).forEach((p, idx) => {
    const clockMinute = clockMinuteOfDay(p.time);
    const elapsed = globalSessionElapsedMinute(clockMinute, sessionStartMinute);
    if (elapsed != null) rows.push({idx, elapsed});
  });
  if (rows.length < 2) return new Map();
  const gapThresholdMinutes = 30;
  const keptGapMinutes = 1;
  let removed = 0;
  let prevElapsed = rows[0].elapsed;
  const compressed = rows.map((row, i) => {
    if (i > 0) {
      const gap = row.elapsed - prevElapsed;
      if (gap > gapThresholdMinutes) {
        removed += Math.max(0, gap - keptGapMinutes);
      }
      prevElapsed = row.elapsed;
    }
    return {idx: row.idx, elapsed: row.elapsed - removed};
  });
  const denominator = Math.max(1, 24 * 60 - removed);
  return new Map(compressed.map(row => [row.idx, clamp01(row.elapsed / denominator)]));
}
function indexSparklineProgress(point, item, fallbackProgress, sessionStartMinute=null) {
  const marketType = String(item.market_type || '');
  if (marketType === 'a_index') {
    const minute = Number(point.minute);
    const tradeMinute = Number.isFinite(minute) ? minute : tradeMinuteOfDay(point.time);
    if (tradeMinute != null) return clamp01(tradeMinute / 240);
  }
  const clockMinute = clockMinuteOfDay(point.time);
  if (clockMinute != null) {
    if (marketType === 'us_index') return clamp01((clockMinute - (9 * 60 + 30)) / 390);
    const elapsed = globalSessionElapsedMinute(clockMinute, sessionStartMinute);
    if (elapsed != null) return clamp01(elapsed / (24 * 60));
  }
  return fallbackProgress;
}
function renderSparkline(vals, item={}) {
  const w=120, h=34, pad=4;
  const minuteLine = Array.isArray(item.minute_line) ? item.minute_line : [];
  let points = [];
  if (minuteLine.length >= 2) {
    const sessionStartMinute = (() => {
      for (const p of minuteLine) {
        const minute = clockMinuteOfDay(p.time);
        if (minute != null) return minute;
      }
      return null;
    })();
    const marketType = String(item.market_type || '');
    const compressedProgresses = marketType && marketType !== 'a_index' && marketType !== 'us_index'
      ? compressedGlobalSessionProgresses(minuteLine, sessionStartMinute)
      : new Map();
    points = minuteLine.map((p, i) => {
      const price = Number(p.price);
      if (!Number.isFinite(price) || price <= 0) return null;
      const fallbackProgress = i / Math.max(1, minuteLine.length - 1);
      const progress = compressedProgresses.has(i)
        ? compressedProgresses.get(i)
        : indexSparklineProgress(p, item, fallbackProgress, sessionStartMinute);
      return {price, x: clamp01(progress) * w};
    }).filter(Boolean);
  } else {
    const prices = (vals || []).map(v => Number(v)).filter(v => Number.isFinite(v) && v > 0);
    points = prices.map((price, i) => ({price, x: (i / Math.max(1, prices.length - 1)) * w}));
  }
  if (points.length < 2) return '';
  const currentPrice = Number(item.price);
  const currentChange = Number(item.change);
  const currentPct = Number(item.change_pct);
  let base = Number(item.prev_close ?? item.prevClose);
  if (!Number.isFinite(base) || base <= 0) {
    if (Number.isFinite(currentPrice) && Number.isFinite(currentChange) && Math.abs(currentPrice - currentChange) > 0) {
      base = currentPrice - currentChange;
    } else if (Number.isFinite(currentPrice) && Number.isFinite(currentPct) && currentPct > -99.9) {
      base = currentPrice / (1 + currentPct / 100);
    }
  }
  if (!Number.isFinite(base) || base <= 0) base = points[0].price;
  const pctVals = points.map(p => (p.price / base - 1) * 100);
  const minPct = Math.min(0, ...pctVals);
  const maxPct = Math.max(0, ...pctVals);
  const padPct = Math.max((maxPct - minPct) * 0.16, 0.05);
  const yMin = minPct - padPct;
  const yMax = maxPct + padPct;
  const span=(yMax-yMin)||1;
  const y = pct => h-pad-((pct-yMin)/span)*(h-pad*2);
  const pts=pctVals.map((v,i)=>[points[i].x,y(v)]);
  const line = pts.map((p,i)=>`${i?'L':'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  const zeroY = y(0);
  const firstX = pts[0][0];
  const lastX = pts[pts.length - 1][0];
  return `<svg class="sparkline" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <line class="sparkline-zero" x1="0" x2="${w}" y1="${zeroY.toFixed(1)}" y2="${zeroY.toFixed(1)}"><title>0% 基准线</title></line>
    <path class="sparkline-area" d="${line} L${lastX.toFixed(1)} ${zeroY.toFixed(1)} L${firstX.toFixed(1)} ${zeroY.toFixed(1)} Z"></path>
    <path class="sparkline-line" d="${line}"></path>
  </svg>`;
}
function tradeMinuteOfDay(timeText) {
  const m = String(timeText || '').match(/(\d{2}):(\d{2})/);
  if (!m) return null;
  const minutes = Number(m[1]) * 60 + Number(m[2]);
  const amStart = 9 * 60 + 30, amEnd = 11 * 60 + 30, pmStart = 13 * 60, pmEnd = 15 * 60;
  if (minutes < amStart || minutes > pmEnd || (minutes > amEnd && minutes < pmStart)) return null;
  if (minutes <= amEnd) return minutes - amStart;
  return 120 + (minutes - pmStart);
}
function toggleBenchmark(symbol) {
  benchmarkOverlay[symbol] = !benchmarkOverlay[symbol];
  render();
}
function setPracticeCurveMode(mode) {
  practiceCurveMode = mode === 'daily' ? 'daily' : 'intraday';
  window.practiceCurveMode = practiceCurveMode;
  if (activeCategory === 'b1_screen') render();
  saveViewState();
}
function setPracticePositionMode(mode) {
  practicePositionMode = mode === 'sold' ? 'sold' : 'open';
  window.practicePositionMode = practicePositionMode;
  syncViewUrl();
  if (activeCategory === 'b1_screen') render();
  saveViewState();
}
function setPracticePositionBriefMode(enabled) {
  practicePositionBriefMode = !!enabled;
  window.practicePositionBriefMode = practicePositionBriefMode;
  syncViewUrl();
  if (activeCategory === 'b1_screen') render();
  saveViewState();
}
function renderPracticeHoverTooltip(item) {
  if (!item) return '';
  const rows = (item.rows || []).map(row => {
    const cls = row.cls ? ` class="${esc(row.cls)}"` : '';
    return `<span class="practice-hover-tooltip-row"><span>${esc(row.label)}</span><strong${cls}>${esc(row.value)}</strong></span>`;
  }).join('');
  return `<span class="practice-hover-tooltip-time">${esc(item.timeText || '--')}</span>${rows}`;
}
function practiceHoverLayerPoints(layer) {
  if (!layer) return [];
  if (Array.isArray(layer._practiceHoverPoints)) return layer._practiceHoverPoints;
  try {
    layer._practiceHoverPoints = JSON.parse(layer.dataset.practiceHoverPoints || '[]');
  } catch (err) {
    layer._practiceHoverPoints = [];
  }
  return layer._practiceHoverPoints;
}
function setPracticeHoverPoint(layer, point) {
  if (!layer || !point) return;
  layer.classList.add('active');
  layer.classList.toggle('place-left', Number(point.xPct || 0) > 66);
  layer.classList.toggle('place-bottom', Number(point.yPct || 0) < 34);
  layer.style.setProperty('--hover-x-pct', `${Number(point.xPct || 0).toFixed(2)}%`);
  layer.style.setProperty('--hover-y-pct', `${Number(point.yPct || 0).toFixed(2)}%`);
  if (point.ariaLabel) layer.setAttribute('aria-label', point.ariaLabel);
  const tooltip = layer.querySelector('.practice-hover-tooltip');
  if (tooltip) tooltip.innerHTML = renderPracticeHoverTooltip(point);
}
function practiceHoverNearestPoint(layer, clientX) {
  const points = practiceHoverLayerPoints(layer);
  if (!points.length) return null;
  const rect = layer.getBoundingClientRect();
  const xPct = rect.width > 0 ? clamp01((clientX - rect.left) / rect.width) * 100 : 0;
  let nearest = points[0];
  let bestDistance = Math.abs(Number(nearest.xPct || 0) - xPct);
  for (const point of points.slice(1)) {
    const distance = Math.abs(Number(point.xPct || 0) - xPct);
    if (distance < bestDistance) {
      nearest = point;
      bestDistance = distance;
    }
  }
  return nearest;
}
function practiceHoverMove(event, layer) {
  if (!layer) return;
  layer.dataset.practicePointerType = event.pointerType || 'mouse';
  if (event.type === 'pointerdown') {
    layer.dataset.practicePointerDown = '1';
    if (layer.setPointerCapture && event.pointerId != null) {
      try { layer.setPointerCapture(event.pointerId); } catch (err) {}
    }
  }
  const point = practiceHoverNearestPoint(layer, event.clientX);
  setPracticeHoverPoint(layer, point);
  if (event.cancelable && (event.pointerType === 'touch' || layer.dataset.practicePointerDown === '1')) {
    event.preventDefault();
  }
}
function practiceHoverRelease(event, layer) {
  if (!layer) return;
  layer.dataset.practicePointerDown = '0';
  if (layer.releasePointerCapture && event.pointerId != null) {
    try { layer.releasePointerCapture(event.pointerId); } catch (err) {}
  }
}
function practiceHoverLeave(layer) {
  if (!layer) return;
  if (layer.dataset.practicePointerDown === '1' || layer.dataset.practicePointerType === 'touch') return;
  layer.classList.remove('active');
}
function straightSvgPath(points) {
  if (!Array.isArray(points) || points.length === 0) return '';
  return points.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
}
function smoothSvgPath(points) {
  if (!Array.isArray(points) || points.length === 0) return '';
  if (points.length === 1) return `M${points[0][0].toFixed(1)} ${points[0][1].toFixed(1)}`;
  if (points.length === 2) return points.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  const d = [`M${points[0][0].toFixed(1)} ${points[0][1].toFixed(1)}`];
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[Math.max(0, i - 1)];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[Math.min(points.length - 1, i + 2)];
    const cp1x = Math.min(p2[0], Math.max(p1[0], p1[0] + (p2[0] - p0[0]) / 6));
    const cp1y = p1[1] + (p2[1] - p0[1]) / 6;
    const cp2x = Math.min(p2[0], Math.max(p1[0], p2[0] - (p3[0] - p1[0]) / 6));
    const cp2y = p2[1] - (p3[1] - p1[1]) / 6;
    d.push(`C${cp1x.toFixed(1)} ${cp1y.toFixed(1)} ${cp2x.toFixed(1)} ${cp2y.toFixed(1)} ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`);
  }
  return d.join(' ');
}
function dayMinuteOfDay(timeText) {
  const m = String(timeText || '').match(/(\d{2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return null;
  const hh = Number(m[1]), mm = Number(m[2]), ss = Number(m[3] || 0);
  if (!Number.isFinite(hh) || !Number.isFinite(mm) || !Number.isFinite(ss)) return null;
  return Math.max(0, Math.min(1439.999, hh * 60 + mm + ss / 60));
}
function tradingClockMinuteOfDay(timeText) {
  const minute = dayMinuteOfDay(timeText);
  if (minute == null) return null;
  const start = 9 * 60 + 30;
  const amEnd = 11 * 60 + 30;
  const pmStart = 13 * 60;
  const end = 15 * 60;
  
  if (minute < start || minute > end || (minute > amEnd && minute < pmStart)) return null;
  
  // 上午时间段
  if (minute <= amEnd) {
    return minute - start;
  }
  // 下午时间段：需要扣除中间休市的 90 分钟 (13:00 - 11:30)
  return (minute - start) - 90;
}
function clampedTradingClockMinuteOfDay(timeText) {
  const minute = dayMinuteOfDay(timeText);
  if (minute == null) return 0;
  const start = 9 * 60 + 30;
  const amEnd = 11 * 60 + 30;
  const pmStart = 13 * 60;
  const end = 15 * 60;
  if (minute <= start) return 0;
  if (minute <= amEnd) return minute - start;
  // 午间休市的净值心跳点应固定在上午收盘位置，而不是回到 09:30。
  if (minute < pmStart) return 120;
  if (minute <= end) return (minute - start) - 90;
  return 240;
}
function normalizePracticeEquityPoints(source) {
  return (source || [])
    .map(p => ({time: p.time || '', equity: Number(p.equity), pnlPct: Number(p.pnl_pct)}))
    .filter(p => Number.isFinite(p.equity) && p.time);
}
function normalizePracticeTradeMarkers(source) {
  return (source || [])
    .map(trade => {
      const action = String(trade?.action || '').toUpperCase();
      const afterPct = trade?.position_after_trade_pct;
      return {
        time: String(trade?.time || ''),
        action,
        code: String(trade?.code || ''),
        name: String(trade?.name || ''),
        shares: Number(trade?.shares),
        price: Number(trade?.price),
        pnl: Number(trade?.pnl),
        pnlPct: Number(trade?.pnl_pct),
        isFullExit: trade?.is_full_exit === true || (action === 'SELL' && afterPct !== null && afterPct !== undefined && Number(afterPct) <= 0),
      };
    })
    .filter(trade => trade.time && (trade.action === 'BUY' || trade.action === 'SELL'))
    .sort((a, b) => a.time.localeCompare(b.time));
}
function practiceTradeMarkersForDate(date) {
  const payload = niuniuPracticeData || {};
  const source = Array.isArray(payload.trade_markers) && payload.trade_markers.length
    ? payload.trade_markers
    : (payload.trade_log || []);
  return normalizePracticeTradeMarkers(source).filter(trade => trade.time.slice(0, 10) === date);
}
function practiceTradeShareText(shares) {
  const value = Number(shares);
  if (!Number.isFinite(value)) return '--';
  return Number.isInteger(value) ? String(value) : fmtNumber(value, 2);
}
function practiceTradePriceText(price) {
  const value = Number(price);
  if (!Number.isFinite(value)) return '--';
  const cents = Math.round(value * 100) / 100;
  return value === cents ? value.toFixed(2) : value.toFixed(3);
}
function practiceTradeMarkerLine(trade) {
  const side = trade.action === 'BUY' ? '买' : '卖';
  const stockName = trade.name || trade.code || '--';
  let text = `${side} ${stockName} ${practiceTradeShareText(trade.shares)}股×${practiceTradePriceText(trade.price)}`;
  if (trade.action === 'SELL' && trade.isFullExit && Number.isFinite(trade.pnl)) {
    text += ` 盈亏${fmtSignedAmount(trade.pnl)}`;
    if (Number.isFinite(trade.pnlPct)) text += ` (${fmtSignedPct(trade.pnlPct)})`;
  }
  return text;
}
function renderPracticeTradeMarkerLine(trade) {
  const isBuy = trade.action === 'BUY';
  const side = isBuy ? '买' : '卖';
  const sideClass = isBuy ? 'buy' : 'sell';
  const stockName = trade.name || trade.code || '--';
  const fillText = `${practiceTradeShareText(trade.shares)}股×${practiceTradePriceText(trade.price)}`;
  const hasPnl = !isBuy && trade.isFullExit && Number.isFinite(trade.pnl);
  const pnlText = hasPnl
    ? `盈亏${fmtSignedAmount(trade.pnl)}${Number.isFinite(trade.pnlPct) ? ` (${fmtSignedPct(trade.pnlPct)})` : ''}`
    : '';
  const pnlClass = Number(trade.pnl) >= 0 ? 'up' : 'down';
  return `<span class="practice-trade-marker-line ${sideClass}">
    <span class="practice-trade-marker-side">${side}</span>
    <span class="practice-trade-marker-stock">${esc(stockName)}</span>
    <span class="practice-trade-marker-fill">${esc(fillText)}</span>
    ${pnlText ? `<span class="practice-trade-marker-pnl ${pnlClass}">${esc(pnlText)}</span>` : ''}
  </span>`;
}
function practiceInterpolatedYAtX(series, targetX) {
  const points = (series || [])
    .map(point => Array.isArray(point) ? {x:Number(point[0]), y:Number(point[1])} : {x:Number(point.x), y:Number(point.y)})
    .filter(point => Number.isFinite(point.x) && Number.isFinite(point.y))
    .sort((a, b) => a.x - b.x);
  if (!points.length || !Number.isFinite(Number(targetX))) return null;
  const x = Number(targetX);
  if (x <= points[0].x) return points[0].y;
  if (x >= points.at(-1).x) return points.at(-1).y;
  for (let idx = 1; idx < points.length; idx += 1) {
    const right = points[idx];
    if (right.x < x) continue;
    const left = points[idx - 1];
    const span = right.x - left.x;
    if (span <= 0) return right.y;
    return left.y + (right.y - left.y) * ((x - left.x) / span);
  }
  return points.at(-1).y;
}
function renderPracticeTradeMarkers(date, xFromTime, series, viewportWidth, viewportHeight) {
  const trades = practiceTradeMarkersForDate(date)
    .filter(trade => tradingClockMinuteOfDay(trade.time) != null && Number.isFinite(trade.shares) && trade.shares > 0);
  if (!trades.length) return '';
  const groups = new Map();
  for (const trade of trades) {
    const minuteKey = trade.time.slice(0, 16);
    if (!groups.has(minuteKey)) groups.set(minuteKey, []);
    groups.get(minuteKey).push(trade);
  }
  return [...groups.entries()].map(([minuteKey, groupTrades]) => {
    const xValues = groupTrades.map(trade => Number(xFromTime(trade.time))).filter(Number.isFinite);
    if (!xValues.length) return '';
    const x = xValues.reduce((sum, value) => sum + value, 0) / xValues.length;
    const y = practiceInterpolatedYAtX(series, x);
    if (!Number.isFinite(y)) return '';
    const xPct = Math.max(0, Math.min(100, x / viewportWidth * 100));
    const yPct = Math.max(0, Math.min(100, y / viewportHeight * 100));
    const actions = new Set(groupTrades.map(trade => trade.action));
    let sideClass = 'mixed';
    if (actions.size === 1 && actions.has('BUY')) {
      sideClass = 'buy';
    } else if (actions.size === 1 && actions.has('SELL')) {
      const fullExitCount = groupTrades.filter(trade => trade.isFullExit).length;
      sideClass = fullExitCount === groupTrades.length
        ? 'sell-full'
        : fullExitCount === 0 ? 'sell-partial' : 'sell-mixed';
    }
    const markerText = groupTrades.length > 1 ? String(groupTrades.length) : (actions.has('BUY') ? 'B' : 'S');
    const placement = [xPct > 72 ? 'place-left' : xPct < 28 ? 'place-right' : '', yPct < 34 ? 'place-bottom' : ''].filter(Boolean).join(' ');
    const lines = groupTrades.map(practiceTradeMarkerLine);
    const timeText = minuteKey.slice(11);
    const ariaLabel = `${timeText} ${lines.join('；')}`;
    return `<button type="button" class="practice-trade-marker ${sideClass} ${placement}" style="left:${xPct.toFixed(2)}%;top:${yPct.toFixed(2)}%" aria-label="${esc(ariaLabel)}">
      ${esc(markerText)}
      <span class="practice-trade-marker-tooltip" aria-hidden="true">
        <span class="practice-trade-marker-time">${esc(timeText)}</span>
        ${groupTrades.map(renderPracticeTradeMarkerLine).join('')}
      </span>
    </button>`;
  }).join('');
}
function practicePctAxisBounds(values) {
  const finite = (values || []).map(Number).filter(Number.isFinite);
  if (!finite.length) return {min: -0.01, max: 0.01, digits: 3};
  const dataMin = Math.min(...finite);
  const dataMax = Math.max(...finite);
  const dataRange = Math.max(0, dataMax - dataMin);
  const minSpan = Math.min(0.2, Math.max(0.02, dataRange * 1.4));
  const pad = Math.max(dataRange * 0.18, minSpan * 0.10);
  let min = dataMin - pad;
  let max = dataMax + pad;
  let span = max - min;
  if (span < minSpan) {
    const expand = (minSpan - span) / 2;
    min -= expand;
    max += expand;
    span = max - min;
  }
  const zeroNear = dataMin <= 0 && dataMax >= 0
    || Math.min(Math.abs(dataMin), Math.abs(dataMax)) <= Math.max(dataRange * 2, 0.04);
  if (zeroNear) {
    min = Math.min(min, -0.01);
    max = Math.max(max, 0.01);
    span = max - min;
  }
  return {min, max, digits: span < 0.05 ? 3 : 2};
}
function compactPracticeDailyPoints(points) {
  const byDate = new Map();
  for (const p of points || []) {
    const date = String(p.time || '').slice(0, 10);
    if (!date) continue;
    const prev = byDate.get(date);
    if (!prev || (new Date(p.time).getTime() || 0) >= (new Date(prev.time).getTime() || 0)) {
      byDate.set(date, p);
    }
  }
  return [...byDate.values()].sort((a, b) => (new Date(a.time).getTime() || 0) - (new Date(b.time).getTime() || 0));
}
function currentDateKey(date=new Date()) {
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(date);
    const get = type => (parts.find(part => part.type === type) || {}).value || '';
    const year = get('year'), month = get('month'), day = get('day');
    if (year && month && day) return `${year}-${month}-${day}`;
  } catch (err) {}
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}
function practicePayloadDateKey() {
  const p = niuniuPracticeData || {};
  const tradingCalendar = p.trading_calendar || {};
  return String(p.current_date || tradingCalendar.date || currentDateKey()).slice(0, 10);
}
function buildPracticeCalendarRows(history, dailyHistory, initialCash=1000000) {
  const normalizedHistory = normalizePracticeEquityPoints(history);
  const normalizedDailyHistory = normalizePracticeEquityPoints(dailyHistory);
  const byDate = new Map();
  for (const p of [...compactPracticeDailyPoints(normalizedHistory), ...compactPracticeDailyPoints(normalizedDailyHistory)]) {
    const date = String(p.time || '').slice(0, 10);
    if (!date) continue;
    const prev = byDate.get(date);
    if (!prev || (new Date(p.time).getTime() || 0) >= (new Date(prev.time).getTime() || 0)) {
      byDate.set(date, p);
    }
  }
  const points = [...byDate.values()]
    .sort((a, b) => (new Date(a.time).getTime() || 0) - (new Date(b.time).getTime() || 0));
  let previousEquity = Number(initialCash);
  return points.map(p => {
    const date = String(p.time || '').slice(0, 10);
    const equity = Number(p.equity);
    const base = Number.isFinite(previousEquity) && previousEquity > 0 ? previousEquity : equity;
    const pnl = Number.isFinite(equity) && Number.isFinite(base) ? equity - base : 0;
    const pnlPct = base ? pnl / base * 100 : 0;
    previousEquity = equity;
    return {date, time:p.time, equity, pnl, pnlPct};
  }).filter(row => row.date);
}
function renderPracticeCurve(history, dailyHistory, initialCash=1000000, benchmarks={items:[]}) {
  const isDailyMode = practiceCurveMode === 'daily';
  const normalizedHistory = normalizePracticeEquityPoints(history);
  const normalizedDailyHistory = normalizePracticeEquityPoints(dailyHistory);
  const tradingCalendar = (niuniuPracticeData && niuniuPracticeData.trading_calendar) || {};
  const isNonTradingCalendarDay = tradingCalendar.is_trading_day === false;
  const targetDate = practicePayloadDateKey();
  let rawPoints = [];
  let dailyCompactedPoints = [];
  let intradayBasePoint = null;
  if (isDailyMode) {
    const compactedFromDaily = compactPracticeDailyPoints(normalizedDailyHistory);
    const compactedFromIntraday = compactPracticeDailyPoints(normalizedHistory);
    const byDate = new Map();
    for (const p of [...compactedFromIntraday, ...compactedFromDaily]) {
      const date = String(p.time || '').slice(0, 10);
      if (!date) continue;
      const prev = byDate.get(date);
      if (!prev || (new Date(p.time).getTime() || 0) >= (new Date(prev.time).getTime() || 0)) {
        byDate.set(date, p);
      }
    }
    dailyCompactedPoints = [...byDate.values()]
      .sort((a, b) => (new Date(a.time).getTime() || 0) - (new Date(b.time).getTime() || 0));
    rawPoints = dailyCompactedPoints;
  } else {
    rawPoints = normalizedHistory;
  }
  rawPoints = [...rawPoints].sort((a, b) => (new Date(a.time).getTime() || 0) - (new Date(b.time).getTime() || 0));
  if (rawPoints.length < 2) return '<div class="empty" style="padding:18px">收益曲线等待更多净值点…</div>';
  const latestTradingClockPoint = rawPoints.filter(p => tradingClockMinuteOfDay(p.time) != null).at(-1);
  const latestDataDay = (latestTradingClockPoint || rawPoints[rawPoints.length - 1]).time.slice(0, 10);
  const latestDay = !isDailyMode && !isNonTradingCalendarDay && targetDate ? targetDate : latestDataDay;
  if (!isDailyMode) {
    const priorByDate = new Map();
    for (const p of [...compactPracticeDailyPoints(normalizedHistory), ...compactPracticeDailyPoints(normalizedDailyHistory)]) {
      const date = String(p.time || '').slice(0, 10);
      if (!date || date >= latestDay) continue;
      const prev = priorByDate.get(date);
      if (!prev || (new Date(p.time).getTime() || 0) >= (new Date(prev.time).getTime() || 0)) {
        priorByDate.set(date, p);
      }
    }
    intradayBasePoint = [...priorByDate.values()]
      .sort((a, b) => (new Date(a.time).getTime() || 0) - (new Date(b.time).getTime() || 0))
      .at(-1) || null;
  }
  const w = 720, h = 210, left = 12, right = 58, top = 18, bottom = 24;
  const innerW = w - left - right, innerH = h - top - bottom;
  const totalSessionMinutes = 4 * 60; // 4小时 = 240分钟
  let points = [];
  let timeTicks = [];
  let xFromTime;
  
  if (isDailyMode) {
    points = dailyCompactedPoints;
    if (points.length < 2) return '<div class="empty" style="padding:18px">累计收益等待更多交易日净值点…</div>';
    // 横轴按日期
    const totalDays = points.length;
    xFromTime = time => {
      const idx = points.findIndex(p => p.time === time);
      if (idx < 0) return left;
      return left + (idx / Math.max(1, totalDays - 1)) * innerW;
    };
    if (totalDays > 1) {
      if (totalDays <= 5) {
        timeTicks = points.map((p, idx) => ({
          label: p.time.slice(5, 10),
          x: left + (idx / Math.max(1, totalDays - 1)) * innerW,
        }));
      } else {
        timeTicks = [
          {label: points[0].time.slice(5,10), x: left},
          {label: points[totalDays-1].time.slice(5,10), x: left + innerW},
        ];
        timeTicks.splice(1, 0, {
          label: points[Math.floor((totalDays-1)/2)].time.slice(5,10),
          x: left + innerW * 0.5,
        });
      }
    }
  } else {
    const dayPoints = rawPoints.filter(p => p.time.slice(0, 10) === latestDay);
    const sessionPoints = dayPoints.filter(p => tradingClockMinuteOfDay(p.time) != null);
    if (sessionPoints.length >= 2) {
      points = sessionPoints;
    } else if (isNonTradingCalendarDay && dayPoints.length >= 2) {
      points = dayPoints;
    } else {
      points = [];
    }
    if (points.length < 2) {
      const modeButtons = `<div class="practice-mode-control" aria-label="收益曲线模式">
        <button class="practice-mode-btn active" type="button" onclick="setPracticeCurveMode('intraday')">当日收益</button>
        <button class="practice-mode-btn" type="button" onclick="setPracticeCurveMode('daily')">累计收益</button>
      </div>`;
      const calendarButton = `<button class="practice-calendar-open-btn" type="button" onclick="openPracticeCalendar(event)">交易日历</button>`;
      const latestHint = latestDataDay && latestDataDay !== latestDay ? ` · 最近已有分时点 ${esc(latestDataDay)}` : '';
      const emptyTitle = isNonTradingCalendarDay && latestDay ? `今日收益曲线（${esc(latestDay)}）` : '今日收益曲线';
      const emptySub = isNonTradingCalendarDay
        ? `非交易日展示最近交易日 · ${latestHint.replace(/^ · /, '') || '等待交易日'}`
        : `北京时间 ${esc(latestDay || targetDate || '--')} · 等待今日盘中净值点${latestHint}`;
      return `<div class="practice-chart-card">
        <div class="practice-chart-head">
          <div>
            <div class="practice-chart-title-row">
              <div class="practice-chart-title">${emptyTitle}</div>
              ${modeButtons}
              ${calendarButton}
            </div>
            <div class="practice-chart-sub">${emptySub}</div>
          </div>
        </div>
        <div class="empty" style="padding:18px">今日收益曲线等待北京时间 ${esc(latestDay || targetDate || '--')} 的盘中净值点…</div>
      </div>`;
    }
    
    // 按时间排序并去重
    points = [...points].sort((a,b) => (new Date(a.time).getTime() || 0) - (new Date(b.time).getTime() || 0));
    const seenTimes = new Set();
    points = points.filter(p => {
      const key = String(p.time || '');
      if (seenTimes.has(key)) return false;
      seenTimes.add(key);
      return true;
    });
    
    // 降采样：只保留盘中发生的点（避免盘后大量相同x坐标的点堆积导致贝塞尔曲线错乱）
    points = points.filter((p, i, arr) => {
      if (i === 0 || i === arr.length - 1) return true; // 保留首尾
      const m = dayMinuteOfDay(p.time);
      // 过滤掉盘前和盘后的密集点，只留一个
      if (m != null && (m < 9 * 60 + 30 || m > 15 * 60)) {
        const prev = dayMinuteOfDay(arr[i-1].time);
        if (prev != null && (prev < 9 * 60 + 30 || prev > 15 * 60)) return false;
      }
      return true;
    });
    
    xFromTime = time => {
      const clampedMinute = Math.max(0, Math.min(totalSessionMinutes, clampedTradingClockMinuteOfDay(time)));
      return left + (clampedMinute / totalSessionMinutes) * innerW;
    };
    timeTicks = [
      {label:'09:30', x:left},
      {label:'11:30', x:left + innerW * 0.5},
      {label:'15:00', x:left + innerW},
    ];
  }
  const vals = points.map(p => p.equity);
  const chartBase = isDailyMode ? initialCash : (Number.isFinite(Number(intradayBasePoint?.equity)) ? Number(intradayBasePoint.equity) : vals[0]);
  const chartPcts = vals.map(v => chartBase ? (v / chartBase - 1) * 100 : 0);
  const chartDeltas = vals.map(v => v - (chartBase || 0));
  const last = vals[vals.length - 1], prev = vals[Math.max(0, vals.length - 2)];
  // 收益曲线只展示牛牛账户本身，指数对照不再叠加，避免干扰账户收益率观察。
  const activeBenchmarks = [];
  const benchmarkSeries = activeBenchmarks.map((b, idx) => ({...b, color: b.symbol === 'sh000001' ? '#f59e0b' : b.symbol === 'sh000300' ? '#60a5fa' : b.symbol === 'sz399006' ? '#ec4899' : '#8b5cf6'}));
  
  const yAxis = practicePctAxisBounds(chartPcts);
  const yMinPct = yAxis.min;
  const yMaxPct = yAxis.max;
  
  const span = (yMaxPct - yMinPct) || 1;
  const y = pct => top + (yMaxPct - pct) / span * innerH;
  const clampPct = pct => Math.max(yMinPct, Math.min(yMaxPct, pct));
  const plottedPts = points.map((p, i) => [xFromTime(p.time), y(chartPcts[i])]);
  const pts = plottedPts.slice();
  
  // 增加一条：如果是从盘中途才开始有数据的（比如从14:30开始），为了不让前面全是空的，
  // 我们往最左边（09:30，x=left）补充一个与第一点 Y 值一样的起始点，把曲线拉平过去。
  if (!isDailyMode && pts.length > 0 && pts[0][0] > left + 1) {
     pts.unshift([left, pts[0][1]]);
  }
  
  const benchmarkPaths = benchmarkSeries.map(b => {
    const bpts = b.points
      .filter(pt => Number.isFinite(Number(pt.pct)) && Number.isFinite(Number(pt.minute)))
      .map(pt => [left + (Number(pt.minute) / totalSessionMinutes) * innerW, y(clampPct(Number(pt.pct))) ]);
    const d = smoothSvgPath(bpts);
    const lastPct = b.points.length ? Number(b.points[b.points.length - 1].pct) : null;
    return {...b, d, lastPct};
  }).filter(b => b.d);
  const line = straightSvgPath(pts);
  const zeroAxisInView = yMinPct <= 0 && yMaxPct >= 0;
  const areaBaseY = y(clampPct(0));
  const area = `${line} L${pts[pts.length-1][0].toFixed(1)} ${areaBaseY.toFixed(1)} L${pts[0][0].toFixed(1)} ${areaBaseY.toFixed(1)} Z`;
  const baseY = areaBaseY;
  const lastPt = pts[pts.length - 1];
  const totalPnl = last - initialCash;
  const totalPct = initialCash ? totalPnl / initialCash * 100 : 0;
  const latestDelta = chartDeltas[chartDeltas.length - 1] || 0;
  const latestDeltaPct = chartPcts[chartPcts.length - 1] || 0;
  const delta = latestDelta;
  const deltaPct = latestDeltaPct;
  const dayDelta = last - prev;
  const dayDeltaPct = prev ? (last / prev - 1) * 100 : 0;
  const maxDrawdown = (() => {
    let peak = vals[0], mdd = 0;
    for (const v of vals) { peak = Math.max(peak, v); mdd = Math.min(mdd, peak ? (v / peak - 1) * 100 : 0); }
    return mdd;
  })();
  const deltaCls = delta >= 0 ? 'up' : 'down';
  const midPct = (yMaxPct + yMinPct) / 2;
  const showMidAxisLabel = Math.abs(midPct) >= 0.08;
  const gridYs = [yMaxPct, midPct, yMinPct].map(y);
  const lastTime = points[points.length - 1].time ? (isDailyMode ? points[points.length - 1].time.slice(0, 10) : points[points.length - 1].time.slice(5,16)) : '';
  const markerLeftPct = (lastPt[0] / w) * 100;
  const markerTopPct = (lastPt[1] / h) * 100;
  const zeroAxisTopPct = (baseY / h) * 100;
  const isUp = latestDelta >= 0;
  const markerColor = isUp ? '#ff4d4f' : '#39d98a';
  const markerGlow = isUp ? 'rgba(255,77,79,.55)' : 'rgba(57,217,138,.55)';
  const timeTickHtml = timeTicks.map((t, idx) => {
    const cls = idx === 0 ? 'start' : (idx === timeTicks.length - 1 ? 'end' : 'mid');
    return `<span class="practice-time-label ${cls}" style="left:${((t.x / w) * 100).toFixed(2)}%">${esc(t.label)}</span>`;
  }).join('');
  const hoverSourcePoints = points.map((p, i) => {
    const equity = Number(p.equity);
    const previousEquity = i > 0 ? Number(points[i - 1].equity) : Number(initialCash);
    const dayDeltaForPoint = Number.isFinite(equity) && Number.isFinite(previousEquity) ? equity - previousEquity : 0;
    const dayPctForPoint = previousEquity ? (equity / previousEquity - 1) * 100 : 0;
    return {
      time: String(p.time || ''),
      equity,
      x: plottedPts[i]?.[0] ?? xFromTime(p.time),
      y: plottedPts[i]?.[1] ?? y(chartPcts[i] || 0),
      delta: Number(chartDeltas[i] || 0),
      pct: Number(chartPcts[i] || 0),
      dayDelta: dayDeltaForPoint,
      dayPct: dayPctForPoint,
    };
  }).filter(point => point.time && Number.isFinite(point.equity) && Number.isFinite(point.x) && Number.isFinite(point.y));
  const hoverPoints = [];
  for (const point of hoverSourcePoints) {
    const lastHoverPoint = hoverPoints[hoverPoints.length - 1];
    if (lastHoverPoint && Math.abs(lastHoverPoint.x - point.x) < 0.5) {
      hoverPoints[hoverPoints.length - 1] = point;
    } else {
      hoverPoints.push(point);
    }
  }
  const hoverValueCls = value => Number(value) >= 0 ? 'up' : 'down';
  const hoverItems = hoverPoints.map(point => {
    const xPct = Math.max(0, Math.min(100, point.x / w * 100));
    const yPct = Math.max(0, Math.min(100, point.y / h * 100));
    const timeText = isDailyMode ? point.time.slice(0, 10) : point.time.slice(5, 16);
    const amountText = fmtSignedAmount(point.delta);
    const pctText = fmtSignedPct(point.pct);
    const dayAmountText = fmtSignedAmount(point.dayDelta);
    const dayPctText = fmtSignedPct(point.dayPct);
    const titleText = isDailyMode
      ? `${timeText} 累计金额 ${amountText}，累计收益率 ${pctText}，当日金额 ${dayAmountText}，当日收益率 ${dayPctText}`
      : `${timeText} 收益金额 ${amountText}，收益率 ${pctText}，账户净值 ${fmtAmount(point.equity)}`;
    const rows = isDailyMode
      ? [
        {label:'累计金额', value:amountText, cls:hoverValueCls(point.delta)},
        {label:'累计收益率', value:pctText, cls:hoverValueCls(point.delta)},
        {label:'当日金额', value:dayAmountText, cls:hoverValueCls(point.dayDelta)},
        {label:'当日收益率', value:dayPctText, cls:hoverValueCls(point.dayDelta)},
      ]
      : [
        {label:'收益金额', value:amountText, cls:hoverValueCls(point.delta)},
        {label:'收益率', value:pctText, cls:hoverValueCls(point.delta)},
        {label:'账户净值', value:fmtAmount(point.equity), cls:''},
      ];
    return {xPct, yPct, timeText, ariaLabel:titleText, rows};
  });
  const defaultHoverItem = hoverItems[hoverItems.length - 1] || null;
  const hoverLayerHtml = defaultHoverItem
    ? `<span class="practice-chart-hover-layer" data-practice-hover-points="${esc(JSON.stringify(hoverItems))}" style="--hover-x-pct:${defaultHoverItem.xPct.toFixed(2)}%;--hover-y-pct:${defaultHoverItem.yPct.toFixed(2)}%;--marker-color:${markerColor};--marker-glow:${markerGlow}" aria-label="${esc(defaultHoverItem.ariaLabel)}" onpointerenter="practiceHoverMove(event, this)" onpointermove="practiceHoverMove(event, this)" onpointerdown="practiceHoverMove(event, this)" onpointerup="practiceHoverRelease(event, this)" onpointercancel="practiceHoverRelease(event, this)" onpointerleave="practiceHoverLeave(this)">
      <span class="practice-hover-line"></span>
      <span class="practice-hover-marker"></span>
      <span class="practice-hover-tooltip">${renderPracticeHoverTooltip(defaultHoverItem)}</span>
    </span>`
    : '';
  const tradeMarkerHtml = isDailyMode ? '' : renderPracticeTradeMarkers(latestDay, xFromTime, plottedPts, w, h);
  const chartTitle = isDailyMode ? '收益曲线 · 累计收益' : `今日收益曲线${isNonTradingCalendarDay && latestDay ? `（${esc(latestDay)}）` : ''}`;
  const intradayBaseLabel = intradayBasePoint
    ? `0轴为上一交易日净值(${esc(String(intradayBasePoint.time || '').slice(5, 16))})`
    : '0轴为今日首个净值';
  const chartSub = isDailyMode
    ? `按交易日最后净值计算 · 0轴为起始资金 · 最近点：${esc(lastTime)}`
    : `固定盘面时间轴 09:30-15:00 · ${intradayBaseLabel} · 最近点：${esc(lastTime)}`;
  const primaryKpiLabel = isDailyMode ? '最新总收益' : '当日收益';
  const secondaryKpiLabel = isDailyMode ? '较前日变化' : '累计收益';
  const secondaryKpiPnl = isDailyMode ? dayDelta : totalPnl;
  const secondaryKpiPct = isDailyMode ? dayDeltaPct : totalPct;
  const secondaryKpiCls = secondaryKpiPnl >= 0 ? 'up' : 'down';
  const modeButtons = `<div class="practice-mode-control" aria-label="收益曲线模式">
    <button class="practice-mode-btn ${!isDailyMode ? 'active' : ''}" type="button" onclick="setPracticeCurveMode('intraday')">当日收益</button>
    <button class="practice-mode-btn ${isDailyMode ? 'active' : ''}" type="button" onclick="setPracticeCurveMode('daily')">累计收益</button>
  </div>`;
  const calendarButton = `<button class="practice-calendar-open-btn" type="button" onclick="openPracticeCalendar(event)">交易日历</button>`;
  return `<div class="practice-chart-card">
    <div class="practice-chart-head">
      <div>
        <div class="practice-chart-title-row">
          <div class="practice-chart-title">${chartTitle}</div>
          ${modeButtons}
          ${calendarButton}
        </div>
        <div class="practice-chart-sub">${chartSub}</div>
        <div class="benchmark-toggle-row"><button class="benchmark-toggle on" type="button" style="--dot:${markerColor}"><span class="benchmark-dot"></span>牛牛账户收益率</button></div>
      </div>
      <div class="practice-chart-kpis">
        <div class="practice-kpi"><div class="practice-kpi-label">${primaryKpiLabel}</div><div class="practice-kpi-value ${deltaCls}">${delta >= 0 ? '+' : ''}${fmtAmount(delta)} / ${deltaPct >= 0 ? '+' : ''}${fmtNumber(deltaPct)}%</div></div>
        <div class="practice-kpi"><div class="practice-kpi-label">${secondaryKpiLabel}</div><div class="practice-kpi-value ${secondaryKpiCls}">${secondaryKpiPnl >= 0 ? '+' : ''}${fmtAmount(secondaryKpiPnl)} / ${secondaryKpiPct >= 0 ? '+' : ''}${fmtNumber(secondaryKpiPct)}%</div></div>
        <div class="practice-kpi"><div class="practice-kpi-label">最大回撤</div><div class="practice-kpi-value down">${fmtNumber(maxDrawdown)}%</div></div>
      </div>
    </div>
    <div class="practice-chart-wrap">
      <span class="practice-axis-label top">${fmtNumber(yMaxPct, yAxis.digits)}%</span>
      ${showMidAxisLabel ? `<span class="practice-axis-label mid">${fmtNumber(midPct, yAxis.digits)}%</span>` : ''}
      <span class="practice-axis-label bot">${fmtNumber(yMinPct, yAxis.digits)}%</span>
      ${zeroAxisInView ? `<span class="practice-zero-axis-label" style="top:${zeroAxisTopPct.toFixed(2)}%">0%</span>` : ''}
      <svg class="practice-chart-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <defs>
          <linearGradient id="practiceFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="${markerColor}" stop-opacity="0.30"/>
            <stop offset="100%" stop-color="${markerColor}" stop-opacity="0.02"/>
          </linearGradient>
          <filter id="practiceGlow" x="-20%" y="-60%" width="140%" height="220%"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
        </defs>
        ${gridYs.map(gy => `<line x1="${left}" x2="${w-right}" y1="${gy.toFixed(1)}" y2="${gy.toFixed(1)}" stroke="rgba(255,255,255,.07)" stroke-dasharray="4 6"/>`).join('')}
        ${timeTicks.map(t => `<line x1="${t.x.toFixed(1)}" x2="${t.x.toFixed(1)}" y1="${top}" y2="${h-bottom}" stroke="rgba(255,255,255,.045)"/>`).join('')}
        ${zeroAxisInView ? `<line x1="${left}" x2="${w-right}" y1="${baseY.toFixed(1)}" y2="${baseY.toFixed(1)}" stroke="rgba(226,232,240,.46)" stroke-width="1.2" stroke-dasharray="7 5"/>` : ''}
        <path d="${area}" fill="url(#practiceFill)"/>
        ${benchmarkPaths.map(b => `<path d="${b.d}" fill="none" stroke="${b.color}" stroke-width="1.5" opacity=".58" vector-effect="non-scaling-stroke"><title>${b.name} ${Number.isFinite(b.lastPct) ? fmtNumber(b.lastPct) + '%' : ''}</title></path>`).join('')}
        <path d="${line}" fill="none" stroke="${markerColor}" stroke-width="2.2" vector-effect="non-scaling-stroke" filter="url(#practiceGlow)"/>
      </svg>
      <span class="practice-current-line" style="left:${markerLeftPct.toFixed(2)}%"></span>
      <span class="practice-current-marker" style="left:${markerLeftPct.toFixed(2)}%;top:${markerTopPct.toFixed(2)}%;--marker-color:${markerColor};--marker-glow:${markerGlow}" title="当前 ${fmtAmount(last)}"></span>
      ${hoverLayerHtml}
      ${tradeMarkerHtml}
      ${timeTickHtml}
    </div>
  </div>`;
}
function practiceCalendarRoot() {
  let root = document.getElementById('practiceCalendarRoot');
  if (!root) {
    root = document.createElement('div');
    root.id = 'practiceCalendarRoot';
    document.body.appendChild(root);
  }
  return root;
}
function monthKeyFromDate(value) {
  const text = String(value || '').slice(0, 10);
  return text.length >= 7 ? text.slice(0, 7) : '';
}
function localDateKey(date=new Date()) {
  return currentDateKey(date);
}
function shiftMonthKey(monthKey, delta) {
  const m = String(monthKey || '').match(/^(\d{4})-(\d{2})$/);
  const base = m ? new Date(Number(m[1]), Number(m[2]) - 1 + delta, 1) : new Date();
  return `${base.getFullYear()}-${String(base.getMonth() + 1).padStart(2, '0')}`;
}
function renderPracticeCalendarDayCurve(date) {
  if (!date) return '';
  const p = niuniuPracticeData || {};
  const initialCash = Number(p.initial_cash || 1000000);
  const history = normalizePracticeEquityPoints(p.equity_history || [])
    .sort((a, b) => (new Date(a.time).getTime() || 0) - (new Date(b.time).getTime() || 0));
  const dailyHistory = normalizePracticeEquityPoints(p.daily_equity_history || [])
    .sort((a, b) => (new Date(a.time).getTime() || 0) - (new Date(b.time).getTime() || 0));
  const allDayHistoryPoints = history
    .filter(point => String(point.time || '').slice(0, 10) === date)
    .filter((point, idx, arr) => idx === 0 || String(point.time || '') !== String(arr[idx - 1].time || ''));
  const dailyDayPoints = dailyHistory.filter(point => String(point.time || '').slice(0, 10) === date);
  const sessionDayPoints = allDayHistoryPoints.filter(point => tradingClockMinuteOfDay(point.time) != null);
  const dailyPoints = compactPracticeDailyPoints([...history, ...dailyHistory]);
  const prevPoint = dailyPoints.filter(point => String(point.time || '').slice(0, 10) < date).at(-1);
  const baseEquity = Number(prevPoint?.equity || initialCash);
  const row = buildPracticeCalendarRows(history, dailyHistory, initialCash).find(item => item.date === date);
  const latestEquity = Number(sessionDayPoints.at(-1)?.equity ?? allDayHistoryPoints.at(-1)?.equity ?? dailyDayPoints.at(-1)?.equity ?? row?.equity);
  const finalPnl = Number.isFinite(latestEquity) && Number.isFinite(baseEquity) ? latestEquity - baseEquity : Number(row?.pnl || 0);
  const finalPct = baseEquity ? finalPnl / baseEquity * 100 : Number(row?.pnlPct || 0);
  const valueCls = finalPnl > 0 ? 'up' : finalPnl < 0 ? 'down' : 'flat';
  const signedAmount = `${finalPnl >= 0 ? '+' : ''}${fmtAmount(finalPnl)}`;
  const signedPct = `${finalPct >= 0 ? '+' : ''}${fmtNumber(finalPct)}%`;
  let curveSourcePoints = sessionDayPoints;
  const hasSessionCurve = sessionDayPoints.length >= 2;
  if (!hasSessionCurve && Number.isFinite(baseEquity) && baseEquity > 0 && Number.isFinite(latestEquity)) {
    curveSourcePoints = [
      {time: `${date} 09:30:00`, equity: baseEquity, pnlPct: 0},
      {time: `${date} 15:00:00`, equity: latestEquity, pnlPct: finalPct},
    ];
  }
  const curveSubPrefix = hasSessionCurve ? '' : '仅有收盘点 · ';
  const closeBtn = '<button type="button" class="practice-calendar-day-curve-close" data-practice-calendar-action="clear-day" title="关闭曲线" aria-label="关闭曲线">x</button>';
  const head = `<div class="practice-calendar-day-curve-head">
    <div>
      <div class="practice-calendar-day-curve-title">${esc(date.slice(5))} 当日收益曲线</div>
      <div class="practice-calendar-day-curve-sub">${curveSubPrefix}0轴 ${prevPoint ? esc(String(prevPoint.time || '').slice(5, 16)) : '初始资金'}</div>
    </div>
    <div class="practice-calendar-day-curve-value ${valueCls}">${signedAmount} / ${signedPct}</div>
    ${closeBtn}
  </div>`;
  if (curveSourcePoints.length < 2 || !Number.isFinite(baseEquity) || baseEquity <= 0) {
    return `<div class="practice-calendar-day-curve" data-practice-calendar-curve>${head}<div class="practice-calendar-day-curve-empty">等待当日分时点</div></div>`;
  }
  // Match the wide, fixed-height SVG viewport so preserveAspectRatio does not
  // letterbox the intraday x-axis with large empty gutters on both sides.
  const w = 464, h = 96, left = 8, right = 12, top = 8, bottom = 14;
  const innerW = w - left - right;
  const innerH = h - top - bottom;
  const curvePoints = curveSourcePoints.map(point => {
    const minute = clampedTradingClockMinuteOfDay(point.time);
    const pct = (Number(point.equity) - baseEquity) / baseEquity * 100;
    return {minute, pct};
  }).filter(point => Number.isFinite(point.minute) && Number.isFinite(point.pct));
  if (curvePoints.length < 2) {
    return `<div class="practice-calendar-day-curve" data-practice-calendar-curve>${head}<div class="practice-calendar-day-curve-empty">等待当日分时点</div></div>`;
  }
  const values = curvePoints.map(point => point.pct);
  let minV = Math.min(0, ...values);
  let maxV = Math.max(0, ...values);
  const pad = Math.max((maxV - minV) * 0.12, 0.08);
  minV -= pad;
  maxV += pad;
  const yFor = value => top + (maxV - value) / Math.max(0.0001, maxV - minV) * innerH;
  const xFor = minute => left + Math.max(0, Math.min(240, minute)) / 240 * innerW;
  const path = curvePoints.map((point, idx) => `${idx ? 'L' : 'M'}${xFor(point.minute).toFixed(1)},${yFor(point.pct).toFixed(1)}`).join(' ');
  const lastPoint = curvePoints.at(-1);
  const markerX = xFor(lastPoint.minute).toFixed(1);
  const markerY = yFor(lastPoint.pct).toFixed(1);
  const zeroY = yFor(0).toFixed(1);
  const stroke = finalPnl >= 0 ? '#ff4d4f' : '#39d98a';
  const fill = finalPnl >= 0 ? 'rgba(255,77,79,.13)' : 'rgba(57,217,138,.13)';
  const areaPath = `${path} L${markerX},${h - bottom} L${xFor(curvePoints[0].minute).toFixed(1)},${h - bottom} Z`;
  const plottedCurvePoints = curvePoints.map(point => [xFor(point.minute), yFor(point.pct)]);
  const tradeMarkerHtml = renderPracticeTradeMarkers(
    date,
    time => xFor(clampedTradingClockMinuteOfDay(time)),
    plottedCurvePoints,
    w,
    h,
  );
  return `<div class="practice-calendar-day-curve" data-practice-calendar-curve>${head}
    <div class="practice-calendar-day-curve-chart">
      <svg class="practice-calendar-day-curve-svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(date)} 当日收益曲线">
        <line x1="${left}" y1="${zeroY}" x2="${w - right}" y2="${zeroY}" stroke="rgba(203,213,225,.32)" stroke-width="1" stroke-dasharray="4 5"></line>
        <path d="${areaPath}" fill="${fill}"></path>
        <path d="${path}" fill="none" stroke="${stroke}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></path>
        <circle cx="${markerX}" cy="${markerY}" r="4" fill="#f8fafc" stroke="${stroke}" stroke-width="2"></circle>
        <text x="${left}" y="${h - 2}" fill="#7b8aa0" font-size="9">09:30</text>
        <text x="${left + innerW / 2}" y="${h - 2}" fill="#7b8aa0" font-size="9" text-anchor="middle">11:30</text>
        <text x="${w - right}" y="${h - 2}" fill="#7b8aa0" font-size="9" text-anchor="end">15:00</text>
      </svg>
      ${tradeMarkerHtml}
    </div>
  </div>`;
}
function renderPracticeCalendarModal() {
  const root = practiceCalendarRoot();
  if (!practiceCalendarOpen) {
    root.innerHTML = '';
    return;
  }
  const p = niuniuPracticeData || {};
  const rows = buildPracticeCalendarRows(p.equity_history || [], p.daily_equity_history || [], Number(p.initial_cash || 1000000));
  const latestMonth = monthKeyFromDate(rows.at(-1)?.date) || monthKeyFromDate(localDateKey());
  if (!practiceCalendarMonth) practiceCalendarMonth = latestMonth;
  const monthMatch = String(practiceCalendarMonth || latestMonth).match(/^(\d{4})-(\d{2})$/);
  const year = monthMatch ? Number(monthMatch[1]) : new Date().getFullYear();
  const month = monthMatch ? Number(monthMatch[2]) : new Date().getMonth() + 1;
  practiceCalendarMonth = `${year}-${String(month).padStart(2, '0')}`;
  const monthStart = new Date(year, month - 1, 1);
  const daysInMonth = new Date(year, month, 0).getDate();
  const firstWeekday = (monthStart.getDay() + 6) % 7;
  const rowByDate = new Map(rows.map(row => [row.date, row]));
  const monthRows = rows.filter(row => row.date.startsWith(practiceCalendarMonth));
  const monthPnl = monthRows.reduce((sum, row) => sum + (Number(row.pnl) || 0), 0);
  const monthBase = monthRows.length ? monthRows[0].equity - monthRows[0].pnl : Number(p.initial_cash || 0);
  const monthPct = monthBase ? monthPnl / monthBase * 100 : 0;
  const winDays = monthRows.filter(row => Number(row.pnl) > 0).length;
  const lossDays = monthRows.filter(row => Number(row.pnl) < 0).length;
  const flatDays = Math.max(0, monthRows.length - winDays - lossDays);
  const clsFor = value => Number(value) > 0 ? 'up' : Number(value) < 0 ? 'down' : 'flat';
  const signedPct = value => `${Number(value) >= 0 ? '+' : ''}${fmtNumber(value)}%`;
  const signedAmount = value => `${Number(value) >= 0 ? '+' : ''}${fmtAmount(value)}`;
  const signedCellPct = value => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '--';
    const digits = Math.abs(n) >= 1 ? 1 : 2;
    return `${n >= 0 ? '+' : ''}${fmtNumber(n, digits)}%`;
  };
  const signedCellAmount = value => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '--';
    const sign = n >= 0 ? '+' : '';
    const abs = Math.abs(n);
    if (abs >= 10000) return `${sign}${(n / 10000).toFixed(abs >= 100000 ? 1 : 2)}万`;
    if (abs >= 100) return `${sign}${Math.round(n)}`;
    return `${sign}${n.toFixed(1)}`;
  };
  const todayText = localDateKey();
  const cells = [];
  for (let i = 0; i < firstWeekday; i++) cells.push('<div class="practice-calendar-day blank" aria-hidden="true"></div>');
  for (let day = 1; day <= daysInMonth; day++) {
    const date = `${practiceCalendarMonth}-${String(day).padStart(2, '0')}`;
    const row = rowByDate.get(date);
    const valueCls = row ? clsFor(row.pnl) : '';
    const selectedCls = date === practiceCalendarSelectedDate ? 'selected' : '';
    const dateAttr = row ? `data-practice-calendar-date="${esc(date)}"` : '';
    const dayOfWeek = new Date(year, month - 1, day).getDay();
    const isWeekend = dayOfWeek === 0 || dayOfWeek === 6;
    const weekendCls = isWeekend && !row ? 'weekend' : '';
    const isToday = date === todayText;
    const weekendTodayMarker = isToday && isWeekend && !row ? '<span class="practice-calendar-today weekend-today">今</span>' : '';
    const inlineTodayMarker = isToday && !weekendTodayMarker ? '<span class="practice-calendar-today">今</span>' : '';
    const fullText = row ? `${date} ${signedPct(row.pnlPct)} / ${signedAmount(row.pnl)}` : `${date}${isWeekend ? ' 周末' : ''}`;
    cells.push(`<div class="practice-calendar-day ${weekendCls} ${selectedCls} ${row ? `has-result ${valueCls}` : ''}" ${dateAttr} title="${esc(fullText)}" aria-label="${esc(fullText)}">
      <div class="practice-calendar-date"><span>${day}</span>${inlineTodayMarker}</div>
      ${row ? `<div class="practice-calendar-values">
        <div class="practice-calendar-rate ${valueCls}">${signedCellPct(row.pnlPct)}</div>
        <div class="practice-calendar-amount ${valueCls}">${signedCellAmount(row.pnl)}</div>
      </div>` : '<div class="practice-calendar-no-data">--</div>'}
      ${weekendTodayMarker}
    </div>`);
  }
  const selectedCurve = practiceCalendarSelectedDate && practiceCalendarSelectedDate.startsWith(practiceCalendarMonth) && rowByDate.has(practiceCalendarSelectedDate)
    ? renderPracticeCalendarDayCurve(practiceCalendarSelectedDate)
    : '';
  root.innerHTML = `<div class="practice-calendar-popover">
    ${selectedCurve}
    <div class="practice-calendar-card" role="dialog" aria-label="交易日历">
      <div class="practice-calendar-head">
        <div>
          <div class="practice-calendar-title">交易日历 · ${year}年${String(month).padStart(2, '0')}月</div>
          <div class="practice-calendar-sub">${monthRows.length ? `有记录 ${monthRows.length} 天 · 最近 ${esc(monthRows.at(-1).date)}` : '本月暂无收益记录'}</div>
        </div>
        <div class="practice-calendar-actions">
          <button type="button" class="practice-calendar-icon-btn" data-practice-calendar-action="prev" title="上个月" aria-label="上个月">‹</button>
          <button type="button" class="practice-calendar-icon-btn" data-practice-calendar-action="next" title="下个月" aria-label="下个月">›</button>
          <button type="button" class="practice-calendar-icon-btn" data-practice-calendar-action="close" title="关闭" aria-label="关闭">x</button>
        </div>
      </div>
      <div class="practice-calendar-summary">
        <div class="practice-calendar-stat"><div class="practice-calendar-stat-label">本月收益</div><div class="practice-calendar-stat-value ${clsFor(monthPnl)}">${signedAmount(monthPnl)} / ${signedPct(monthPct)}</div></div>
        <div class="practice-calendar-stat"><div class="practice-calendar-stat-label">盈利天数</div><div class="practice-calendar-stat-value up">${winDays}</div></div>
        <div class="practice-calendar-stat"><div class="practice-calendar-stat-label">亏损/持平</div><div class="practice-calendar-stat-value">${lossDays} / ${flatDays}</div></div>
      </div>
      <div class="practice-calendar-grid-wrap">
        <div class="practice-calendar-weekdays">${['一','二','三','四','五','六','日'].map((day, idx) => `<div class="practice-calendar-weekday ${idx >= 5 ? 'weekend' : ''}">${day}</div>`).join('')}</div>
        <div class="practice-calendar-grid">${cells.join('')}</div>
      </div>
    </div>
  </div>`;
}
function openPracticeCalendar(event) {
  if (event && event.stopPropagation) event.stopPropagation();
  const p = niuniuPracticeData || {};
  const rows = buildPracticeCalendarRows(p.equity_history || [], p.daily_equity_history || [], Number(p.initial_cash || 1000000));
  practiceCalendarMonth = monthKeyFromDate(rows.at(-1)?.date) || monthKeyFromDate(localDateKey());
  practiceCalendarSelectedDate = '';
  practiceCalendarOpen = true;
  renderPracticeCalendarModal();
}
function closePracticeCalendar() {
  practiceCalendarOpen = false;
  practiceCalendarSelectedDate = '';
  renderPracticeCalendarModal();
}
function shiftPracticeCalendarMonth(delta) {
  practiceCalendarMonth = shiftMonthKey(practiceCalendarMonth, delta);
  practiceCalendarSelectedDate = '';
  renderPracticeCalendarModal();
}
function renderPracticePanel() {
  const p = niuniuPracticeData || {};
  const positions = p.positions || [];
  const soldStocks = p.today_sold_stocks || [];
  const showSoldStocks = practicePositionMode === 'sold';
  const totalEquity = Number(p.total_equity);
  const pnl = Number(p.total_pnl || 0);
  const pnlCls = pnl >= 0 ? 'up' : 'down';
  const BUY_NAMES = {
    trend_pullback: '趋势回踩',
    breakout: '突破确认',
    shaofu_b1: '少妇B1', b2_confirm: 'B2确认',
    b3_accelerate: 'B3中继', super_b1: '超级B1',
    li_daxiao_bottom: '李大霄',
    mixed: '混合买入', unknown_buy: '未识别买入',
    auto_exit: '系统退出', unknown: '其他'
  };
  const EXIT_NAMES = {
    stop_loss: '止损', take_profit: '主动止盈', profit_protection: '回撤保护',
    top_escape: '逃顶/出货', technical_break: '技术破位', sell_score: '卖出评分',
    no_progress: '信号未兑现', position_adjust: '仓位调整', model_sell: '模型卖出',
    other_exit: '其他卖出'
  };
  const dynamicStrategyMeta = (b1ScreenData && b1ScreenData.strategy_meta) || {};
  for (const [key, meta] of Object.entries(dynamicStrategyMeta)) {
    BUY_NAMES[key] = meta.label || BUY_NAMES[key] || key;
  }
  const splitTags = value => {
    if (Array.isArray(value)) return value.map(x => String(x || '').trim()).filter(Boolean);
    return String(value || '').split(/[，,]/).map(x => x.trim()).filter(Boolean);
  };
  const uniq = values => Array.from(new Set((values || []).filter(Boolean)));
  const inferExitRulesFromReason = reason => {
    const text = String(reason || '');
    const rules = [];
    const add = rule => { if (rule && !rules.includes(rule)) rules.push(rule); };
    if (/止损|破入场止损/.test(text)) add('stop_loss');
    if (/止盈清仓|第一批止盈|卤煮止盈|止盈/.test(text)) add('take_profit');
    if (/峰值回撤|ATR吊灯|移动止损保本|盈转亏/.test(text)) add('profit_protection');
    if (/S1|S2|S3|逃顶|出货五式/.test(text)) add('top_escape');
    if (/卖出评分|防卖飞评分/.test(text)) add('sell_score');
    if (/BBI|白线|死叉|低点跌破|趋势确认失效/.test(text)) add('technical_break');
    if (/未兑现|低效持仓|持仓到期|次日不涨|未延续/.test(text)) add('no_progress');
    return rules;
  };
  const badgeList = labels => labels.length
    ? `<span class="position-reason-badges">${labels.map(label => `<span class="position-reason-badge">${esc(label)}</span>`).join('')}</span>`
    : '';
  const reasonRow = (label, content) => content
    ? `<div class="position-reason-row"><span class="position-reason-label">${esc(label)}</span><span class="position-reason-text">${content}</span></div>`
    : '';
  const positionModeButtons = `<div class="practice-mode-control" aria-label="持仓视图">
    <button class="practice-mode-btn ${!showSoldStocks ? 'active' : ''}" type="button" onclick="setPracticePositionMode('open')">当前持仓${positions.length ? ` ${positions.length}` : ''}</button>
    <button class="practice-mode-btn ${showSoldStocks ? 'active' : ''}" type="button" onclick="setPracticePositionMode('sold')">今日卖出${soldStocks.length ? ` ${soldStocks.length}` : ''}</button>
  </div>`;
  const positionDisplayButtons = `<div class="practice-mode-control" aria-label="持仓显示模式">
    <button class="practice-mode-btn ${!practicePositionBriefMode ? 'active' : ''}" type="button" onclick="setPracticePositionBriefMode(false)">完整</button>
    <button class="practice-mode-btn ${practicePositionBriefMode ? 'active' : ''}" type="button" onclick="setPracticePositionBriefMode(true)">简要</button>
  </div>`;
  const posCards = positions.length ? positions.map(x => {
    const pnlValue = Number(x.pnl);
    const pnlPct = Number(x.pnl_pct);
    const c = !Number.isFinite(pnlValue) ? '#94a3b8' : (pnlValue >= 0 ? '#ff4d4f' : '#39d98a');
    const marketValue = Number(x.market_value);
    const positionPct = Number.isFinite(totalEquity) && totalEquity > 0 && Number.isFinite(marketValue) ? marketValue / totalEquity * 100 : null;
    const positionText = Number.isFinite(positionPct) ? `${fmtNumber(positionPct)}%` : '--';
    const pnlPctText = Number.isFinite(pnlPct) ? `${pnlPct >= 0 ? '+' : ''}${fmtNumber(pnlPct)}%` : '--';
    if (practicePositionBriefMode) {
      return `<div class="position-brief-card">
        <div class="position-brief-name">${esc(x.name || x.code || '--')}</div>
        <div class="position-brief-stats">
          <div class="position-brief-item"><span>仓位</span><b>${positionText}</b></div>
          <div class="position-brief-item"><span>盈亏</span><b style="color:${c}">${pnlPctText}</b></div>
        </div>
      </div>`;
    }
    const changePct = Number(x.change_pct);
    const todayPnl = Number(x.today_pnl);
    const todayPct = Number(x.today_pnl_pct ?? x.change_pct);
    const dayLowPct = Number(x.day_low_pct);
    const dayHighPct = Number(x.day_high_pct);
    const dayColor = Number.isFinite(todayPnl) ? (todayPnl >= 0 ? '#ff4d4f' : '#39d98a') : '#94a3b8';
    const changeColor = Number.isFinite(changePct) ? (changePct >= 0 ? '#ff4d4f' : '#39d98a') : '#94a3b8';
    const changeText = Number.isFinite(changePct) ? `${changePct >= 0 ? '+' : ''}${fmtNumber(changePct)}%` : '--';
    const lowColor = Number.isFinite(dayLowPct) ? (dayLowPct >= 0 ? '#ff4d4f' : '#39d98a') : '#94a3b8';
    const highColor = Number.isFinite(dayHighPct) ? (dayHighPct >= 0 ? '#ff4d4f' : '#39d98a') : '#94a3b8';
    const lowText = Number.isFinite(dayLowPct) ? `${dayLowPct >= 0 ? '+' : ''}${fmtNumber(dayLowPct)}%` : '--';
    const highText = Number.isFinite(dayHighPct) ? `${dayHighPct >= 0 ? '+' : ''}${fmtNumber(dayHighPct)}%` : '--';
    const todayText = Number.isFinite(todayPnl)
      ? `${todayPnl >= 0 ? '+' : ''}${fmtAmount(todayPnl)}${Number.isFinite(todayPct) ? ` / ${todayPct >= 0 ? '+' : ''}${fmtNumber(todayPct)}%` : ''}`
      : '--';
    const costPriceText = `${fmtNumber(x.avg_cost)} / ${fmtNumber(x.last_price)}`;
    const pnlText = Number.isFinite(pnlValue)
      ? `${pnlValue >= 0 ? '+' : ''}${fmtAmount(pnlValue)}${Number.isFinite(pnlPct) ? ` / ${pnlPct >= 0 ? '+' : ''}${fmtNumber(pnlPct)}%` : ''}`
      : '--';
    const availableHoldText = `${x.available_qty ?? 0} / ${x.qty ?? 0}`;
    const buyStrategyLabels = uniq(splitTags(x.buy_strategy).map(key => BUY_NAMES[key] || key));
    const buyReasonText = String(x.entry_reason || x.buy_reason || '').trim();
    const buyReasonBlock = x.bought_today && (buyStrategyLabels.length || buyReasonText)
      ? `<div class="position-reason-block">
          ${reasonRow('买入策略', badgeList(buyStrategyLabels))}
          ${reasonRow('买入理由', esc(buyReasonText))}
        </div>`
      : '';
    return `<div class="position-card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <span style="font-weight:700;font-size:16px;color:#f8fafc">${esc(x.code)} ${esc(x.name||'')}</span>
      </div>
      <div class="position-metrics">
        <div class="position-metric"><div class="position-label">成本/现价</div><div class="position-value combo">${costPriceText}</div></div>
        <div class="position-metric"><div class="position-label">盈亏</div><div class="position-value strong combo" style="color:${c}">${pnlText}</div></div>
        <div class="position-metric"><div class="position-label">实时涨幅</div><div class="position-value strong" style="color:${changeColor}">${changeText}</div></div>
        <div class="position-metric"><div class="position-label">最低/最高</div><div class="position-value strong combo"><span style="color:${lowColor}">${lowText}</span><span style="color:#64748b">/</span><span style="color:${highColor}">${highText}</span></div></div>
        <div class="position-metric"><div class="position-label">今日收益</div><div class="position-value strong" style="color:${dayColor}">${todayText}</div></div>
        <div class="position-metric"><div class="position-label">市值</div><div class="position-value">${fmtAmount(x.market_value)}</div></div>
        <div class="position-metric"><div class="position-label">仓位占比</div><div class="position-value">${positionText}</div></div>
        <div class="position-metric"><div class="position-label">可卖/持有</div><div class="position-value" style="color:#94a3b8">${availableHoldText}</div></div>
      </div>
      ${buyReasonBlock}
    </div>`;
  }).join('') : '<div class="empty" style="padding:18px;font-size:13px">暂无持仓，等待模型决策建仓</div>';
  const soldCards = soldStocks.length ? soldStocks.map(x => {
    const realized = Number(x.realized_pnl);
    const realizedPct = Number(x.realized_pnl_pct);
    const afterSellPnl = Number(x.after_sell_pnl);
    const afterSellPct = Number(x.change_after_sell_pct);
    const currentChangePct = Number(x.current_change_pct);
    const realizedColor = Number.isFinite(realized) ? (realized >= 0 ? '#ff4d4f' : '#39d98a') : '#94a3b8';
    const afterColor = Number.isFinite(afterSellPnl) ? (afterSellPnl > 0 ? '#f59e0b' : (afterSellPnl < 0 ? '#34d399' : '#94a3b8')) : '#94a3b8';
    const currentColor = Number.isFinite(currentChangePct) ? (currentChangePct >= 0 ? '#ff4d4f' : '#39d98a') : '#94a3b8';
    const realizedText = Number.isFinite(realized)
      ? `${realized >= 0 ? '+' : ''}${fmtAmount(realized)}${Number.isFinite(realizedPct) ? ` / ${realizedPct >= 0 ? '+' : ''}${fmtNumber(realizedPct)}%` : ''}`
      : '--';
    const afterText = Number.isFinite(afterSellPnl)
      ? `${afterSellPnl >= 0 ? '+' : ''}${fmtAmount(afterSellPnl)}${Number.isFinite(afterSellPct) ? ` / ${afterSellPct >= 0 ? '+' : ''}${fmtNumber(afterSellPct)}%` : ''}`
      : '--';
    const currentChangeText = Number.isFinite(currentChangePct) ? `${currentChangePct >= 0 ? '+' : ''}${fmtNumber(currentChangePct)}%` : '--';
    const observation = Number.isFinite(afterSellPnl)
      ? (afterSellPnl > 0 ? '卖出后上涨' : (afterSellPnl < 0 ? '卖出后回落' : '卖出后持平'))
      : '等待行情';
    const priceText = `${fmtNumber(x.avg_sell_price)} / ${x.current_price == null ? '--' : fmtNumber(x.current_price)}`;
    const sellReasonText = String(x.reason || '').trim();
    const rawExitRules = Array.isArray(x.exit_rules) && x.exit_rules.length ? x.exit_rules : x.exit_rule;
    const exitRuleKeys = splitTags(rawExitRules);
    const exitRuleLabels = uniq((exitRuleKeys.length ? exitRuleKeys : inferExitRulesFromReason(sellReasonText)).map(key => EXIT_NAMES[key] || key));
    const sellReasonBlock = (exitRuleLabels.length || sellReasonText)
      ? `<div class="position-reason-block">
          ${reasonRow('卖出归因', badgeList(exitRuleLabels))}
          ${reasonRow('卖出理由', esc(sellReasonText))}
        </div>`
      : '';
    return `<div class="position-card">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:8px">
        <span style="font-weight:700;font-size:16px;color:#f8fafc">${esc(x.code)} ${esc(x.name||'')}</span>
        <span style="font-size:13px;color:#94a3b8">${esc(x.shares)}股 · ${esc((x.last_sell_time||'').slice(11,16))}</span>
      </div>
      <div class="position-metrics">
        <div class="position-metric"><div class="position-label">卖出/现价</div><div class="position-value combo">${priceText}</div></div>
        <div class="position-metric"><div class="position-label">已实现盈亏</div><div class="position-value strong combo" style="color:${realizedColor}">${realizedText}</div></div>
        <div class="position-metric"><div class="position-label">卖后变化</div><div class="position-value strong combo" style="color:${afterColor}">${afterText}</div></div>
        <div class="position-metric"><div class="position-label">观察</div><div class="position-value strong" style="color:${afterColor}">${observation}</div></div>
        <div class="position-metric"><div class="position-label">实时涨幅</div><div class="position-value strong" style="color:${currentColor}">${currentChangeText}</div></div>
        <div class="position-metric"><div class="position-label">卖出金额</div><div class="position-value">${fmtAmount(x.sell_amount)}</div></div>
        <div class="position-metric"><div class="position-label">到账金额</div><div class="position-value">${fmtAmount(x.net_proceeds)}</div></div>
        <div class="position-metric"><div class="position-label">费用</div><div class="position-value" style="color:#94a3b8">${fmtAmount(x.fee)}</div></div>
      </div>
      ${sellReasonBlock}
    </div>`;
  }).join('') : '<div class="empty" style="padding:18px;font-size:13px">今日暂无卖出股票</div>';
  const stockCards = showSoldStocks ? soldCards : posCards;
  const stockCardsClass = !showSoldStocks && positions.length && practicePositionBriefMode ? 'position-brief-grid' : 'position-card-list';
  const operationLog = renderPracticeOperationLog(p);
  const logDetailModal = renderPracticeLogDetailModal(p);
  const quote = p.last_quote_refresh || {};
  const channels = quote.channel_counts || {};
  const ruleNote = p.trade_rule_note || practiceRuleFallbackNote();
  const ruleModal = renderPracticeRuleNoteModal(ruleNote);
  const channelText = quote.quote_time ? `腾讯${channels.tencent ?? 0}/东财${channels.eastmoney ?? 0}/Sina${channels.sina ?? 0}/单票${channels.single ?? 0}` : '';
  const quoteNote = quote.quote_time ? `行情：${esc(quote.quote_time)} 更新${quote.updated ?? 0}只 ${channelText}${quote.fallback ? `，回退${quote.fallback}只` : ''}` : '';
  const ruleMeta = [`模型：${esc(p.decision_model || 'deepseek-v4-pro')}`, quoteNote].filter(Boolean).join('｜');
  return `<section class="sector-cloud" style="margin-bottom:18px">
    <h3>牛牛实战 · 模拟账户</h3>
    ${p.trading_paused ? `<div style=\"background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.35);border-radius:12px;padding:10px 14px;margin:10px 0;display:flex;justify-content:space-between;align-items:center\">
      <span style=\"color:#fbbf24;font-size:13px\">⏸️ 交易已暂停：${esc(p.pause_reason||'风控触发')}（${esc((p.pause_since||'').slice(11,16))}起）</span>
      <button onclick=\"actionFetch('/api/niuniu_practice/resume').then(r=>r.json()).then(d=>{if(d.resumed)location.reload()})\" style=\"background:rgba(52,211,153,.18);color:#34d399;border:1px solid rgba(52,211,153,.35);border-radius:8px;padding:6px 12px;cursor:pointer;font-size:12px;font-weight:600\">🔄 强制恢复交易</button>
    </div>` : ''}
    <div class="practice-stats" style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:12px 0">
      <div class="inline-field"><div class="inline-label">初始资金</div><div class="inline-value">${fmtAmount(p.initial_cash)}</div></div>
      <div class="inline-field"><div class="inline-label">总权益</div><div class="inline-value">${fmtAmount(p.total_equity)}</div></div>
      <div class="inline-field"><div class="inline-label">现金</div><div class="inline-value">${fmtAmount(p.cash)}</div></div>
      <div class="inline-field"><div class="inline-label">累计收益</div><div class="inline-value ${pnlCls}">${fmtAmount(p.total_pnl)} / ${fmtNumber(p.total_pnl_pct)}%</div></div>
    </div>
    <div>${renderPracticeCurve(p.equity_history || [], p.daily_equity_history || [], Number(p.initial_cash || 1000000), practiceBenchmarksData || {items:[]})}</div>
    <div style="display:flex;align-items:center;justify-content:flex-start;gap:12px;flex-wrap:wrap;margin:12px 0 8px">
      ${positionModeButtons}
      ${!showSoldStocks ? positionDisplayButtons : ''}
    </div>
    <div class="${stockCardsClass}">${stockCards}</div>
    ${operationLog}
    ${logDetailModal}
    <div class="practice-rule-row">
      <button type="button" class="practice-rule-btn" data-practice-rule-action="open">交易规则</button>
      <span class="practice-rule-meta">${ruleMeta}</span>
    </div>
    ${ruleModal}
    ${p.last_error ? `<div class="empty" style="color:#f87171;margin-top:10px">模型/交易错误：${esc(p.last_error)}</div>` : ''}
  </section>`;
}

function setIndicesViewMode(mode) {
  indicesViewMode = mode === 'market' ? 'market' : 'index';
  syncViewUrl();
  if (activeCategory === 'indices') render();
  saveViewState();
}

function setIndicesMarketRegion(mode) {
  if (!['a_share', 'us'].includes(mode)) return;
  indicesMarketRegionOverride = mode;
  if (activeCategory === 'indices' && indicesViewMode === 'market') render();
}

function resolvedIndicesMarketRegion(aIndexItems = []) {
  if (indicesMarketRegionOverride) return indicesMarketRegionOverride;
  return indicesSwitchSession(aIndexItems) === 'a_share' ? 'a_share' : 'us';
}

function renderIndicesPanel() {
  const idx = indicesData;
  const items = idx.items || [];
  const hot = hotStocksData;
  const sec = sectorData;
  const sectors = sec.sectors || sec.items || [];
  const mf = moneyFlowData;
  const errorHtml = idx.error ? `<div class="empty" style="color:#f87171;margin-bottom:12px">指数接口错误：${esc(idx.error)}</div>` : '';
  if (!items.length && !idx.error) {
    return '<div class="loading">行情加载中...</div>';
  }
  function trendClass(item) {
    const c = Number(item.change_pct);
    return c > 0 ? 'index-up' : c < 0 ? 'index-down' : 'index-flat';
  }
  function fmtChange(item) {
    if (item.change_pct == null) return '';
    const c = Number(item.change_pct);
    const sign = c > 0 ? '+' : '';
    return `<div class="index-change ${trendClass(item)}">${sign}${fmtNumber(item.change_pct,2)}%</div>`;
  }
  function renderFlowBlock(title, list, isInflow) {
    return `<h3>${title}</h3><div class="sector-grid">${list.map(s => {
      const cls = isInflow ? 'up' : 'down';
      const sign = Number(s.pct) > 0 ? '+' : '';
      const flowSign = Number(s.net_flow_yi ?? 0) > 0 ? '+' : '';
      const flowCls = isInflow ? 'flow-in' : 'flow-out';
      const bg = isInflow ? 'rgba(127,29,29,.28)' : 'rgba(6,78,59,.28)';
      const border = isInflow ? 'rgba(248,113,113,.22)' : 'rgba(52,211,153,.22)';
      return `<div class="hot-item ${cls}" style="position:relative;background:${bg};border-color:${border}"><div class="sector-name">${esc(s.name)}</div><div class="hot-price">${fmtNumber(s.price)}</div><div class="sector-pct">${sign}${fmtNumber(s.pct)}% <span class="flow-val ${flowCls}">${flowSign}${fmtNumber(s.net_flow_yi,2)}亿</span></div></div>`;
    }).join('')}</div>`;
  }
  let mfHtml = '';
  if (mf.inflow && mf.inflow.length && mf.outflow && mf.outflow.length) {
    mfHtml = `<div class="sector-cloud"><h3 style="display:flex;align-items:center;gap:12px;flex-wrap:wrap"><span>主力资金流向</span></h3><div style="display:flex;gap:16px;flex-wrap:wrap"><div style="flex:1;min-width:260px">${renderFlowBlock('主力净流入前十', mf.inflow, true)}</div><div style="flex:1;min-width:260px">${renderFlowBlock('主力净流出前十', mf.outflow, false)}</div></div></div>`;
  }
  function renderMarketFlowBlock() {
    const mf = marketFlowData;
    if (mf.total_inflow_yi == null) return '';
    if (!Number(mf.total_inflow_yi) && !Number(mf.total_outflow_yi) && !Number(mf.net_flow_yi)) return '';
    const netCls = Number(mf.net_flow_yi) > 0 ? 'up' : Number(mf.net_flow_yi) < 0 ? 'down' : 'flat';
    const sign = Number(mf.net_flow_yi) > 0 ? '+' : '';
    return `<div class="sector-cloud"><h3>大盘资金流向</h3><div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:10px">
      <div style="flex:1;min-width:120px;text-align:center;padding:8px 12px;background:#111;border-radius:8px"><div style="font-size:12px;color:#999">总流入</div><div style="font-size:18px;color:#e74c3c;font-weight:bold">${fmtAmount(mf.total_inflow)}</div><div style="font-size:11px;color:#666">${fmtNumber(mf.total_inflow_yi, 0)}亿</div></div>
      <div style="flex:1;min-width:120px;text-align:center;padding:8px 12px;background:#111;border-radius:8px"><div style="font-size:12px;color:#999">总流出</div><div style="font-size:18px;color:#2ecc71;font-weight:bold">${fmtAmount(mf.total_outflow)}</div><div style="font-size:11px;color:#666">${fmtNumber(mf.total_outflow_yi, 0)}亿</div></div>
      <div style="flex:1;min-width:120px;text-align:center;padding:8px 12px;background:#111;border-radius:8px"><div style="font-size:12px;color:#999">净流入</div><div style="font-size:18px;color:${netCls === 'up' ? '#e74c3c' : '#2ecc71'};font-weight:bold">${sign}${fmtAmount(mf.net_flow)}</div><div style="font-size:11px;color:#666">${sign}${fmtNumber(mf.net_flow_yi, 0)}亿</div></div>
    </div></div>`;
  }
  function renderIndexGroup(title, list) {
    if (!list || !list.length) return '';
    return `<div style="margin-bottom:18px"><h3 style="margin:0 0 10px;color:#c7d2fe;font-size:15px">${title}</h3><section class="market-strip">${list.map(item => `
      <article class="index-card ${trendClass(item)}">
        <div class="index-name">${esc(item.name)}</div>
        <div class="index-price">${fmtNumber(item.price)}</div>
        ${fmtChange(item)}
        ${renderSparkline(item.sparkline, item)}
        <div class="index-time">${esc(item.time || '')}</div>
      </article>
    `).join('')}</section></div>`;
  }
  function legacyMarketType(item) {
    const key = String(item.key || '');
    const code = String(item.code || '');
    const name = String(item.name || '');
    if (item.market_type) return item.market_type;
    if (key === 'a50_fut' || code === 'hf_CHA50CFD' || /A50|富时中国/.test(name)) return 'a_futures';
    if (/_fut$/.test(key) || /期货/.test(name)) return 'us_futures';
    if (['dow', 'nas', 'spx'].includes(key) || /^us/.test(code)) return 'us_index';
    if (key === 'xau' || key === 'brent' || /黄金|伦敦金|原油/.test(name)) return 'commodity';
    if (item.group === 'domestic' || /^s[hz]/.test(code)) return 'a_index';
    return item.group || '';
  }
  function marketItems(type, fallbackGroup = '') {
    const grouped = idx.market_groups?.[type];
    if (Array.isArray(grouped) && grouped.length) return grouped;
    return items.filter(x => legacyMarketType(x) === type || (fallbackGroup && x.group === fallbackGroup && legacyMarketType(x) === type));
  }
  const aIndexItems = marketItems('a_index', 'domestic');
  const usIndexItems = marketItems('us_index', 'global');
  function renderSessionMarketGroups() {
    const session = indicesSwitchSession(aIndexItems);
    const sections = session === 'a_share' ? [
      ['A股指数', aIndexItems],
      ['A股期货', marketItems('a_futures')],
      ['美股期货', marketItems('us_futures')],
      ['大宗商品', marketItems('commodity', 'commodity')],
    ] : session === 'us_open' ? [
      ['美股指数', usIndexItems],
      ['A股期货', marketItems('a_futures')],
      ['大宗商品', marketItems('commodity', 'commodity')],
    ] : [
      ['美股指数', usIndexItems],
      ['A股期货', marketItems('a_futures')],
      ['美股期货', marketItems('us_futures')],
      ['大宗商品', marketItems('commodity', 'commodity')],
    ];
    return sections.map(([title, list]) => renderIndexGroup(title, list)).join('');
  }
  function renderRankBlock(title, list, mode) {
    if (!list || !list.length) return '';
    return `<div style="flex:1;min-width:250px"><h3>${title}</h3><div class="sector-grid">${list.slice(0,10).map(s => {
      const cls = Number(s.pct) > 0 ? 'up' : Number(s.pct) < 0 ? 'down' : 'flat';
      const sign = Number(s.pct) > 0 ? '+' : '';
      const sub = mode === 'turnover' ? `换手 ${fmtNumber(s.turnover,2)}%` : mode === 'volume' ? `量 ${fmtNumber((s.volume_lot||0)/10000,1)}万手` : `额 ${fmtNumber(s.amount_yi,2)}亿`;
      return `<div class="hot-item ${cls}"><div class="sector-name">${esc(s.code)} ${esc(s.name||'')}</div><div class="hot-price">${fmtNumber(s.price)}</div><div class="sector-pct">${sign}${fmtNumber(s.pct)}% <span class="flow-val">${sub}</span></div></div>`;
    }).join('')}</div></div>`;
  }
  let hotHtml = '';
  if ((hot.amount_top && hot.amount_top.length) || (hot.turnover_top && hot.turnover_top.length) || (hot.volume_top && hot.volume_top.length)) {
    hotHtml = `<div class="sector-cloud"><h3>活跃股票榜</h3><div style="display:flex;gap:16px;flex-wrap:wrap">${renderRankBlock('成交额前十', hot.amount_top || hot.items || [], 'amount')}${renderRankBlock('换手率前十', hot.turnover_top || [], 'turnover')}${renderRankBlock('成交量前十', hot.volume_top || [], 'volume')}</div></div>`;
  } else if (hot.items && hot.items.length) {
    const items = hot.items.slice(0, 12);
    hotHtml = `<div class="sector-cloud"><h3>热搜股票</h3><div class="sector-grid">${items.map(s => {
      const cls = Number(s.pct) > 0 ? 'up' : Number(s.pct) < 0 ? 'down' : 'flat';
      const sign = Number(s.pct) > 0 ? '+' : '';
      return `<div class="hot-item ${cls}"><div class="sector-name">${esc(s.code)} ${esc(s.name||'')}</div><div class="hot-price">${fmtNumber(s.price)}</div><div class="sector-pct">${sign}${fmtNumber(s.pct)}%</div></div>`;
    }).join('')}</div></div>`;
  }
  let cloudHtml = '';
  const gainTop = sec.gain_top || sectors.slice(0, 10);
  const lossTop = sec.loss_top || [];
  function renderSectorCloudHeading(source) {
    const sourceMeta = source && source.generated_at ? `<span class="flow-val">更新 ${esc(source.generated_at)}</span>` : '';
    return `<h3>板块涨跌幅 ${sourceMeta}</h3>`;
  }
  function renderSectorMoveBlock(title, list, isGain) {
    if (!list || !list.length) return '';
    return `<h3>${title}</h3><div class="sector-grid">${list.slice(0,10).map(s => {
      const pct = Number(s.pct || 0);
      const sign = pct > 0 ? '+' : '';
      const cls = isGain ? 'up' : 'down';
      return `<div class="sector-item ${cls}"><div class="sector-name">${esc(s.name)}</div><div class="sector-pct">${sign}${fmtNumber(s.pct)}%</div></div>`;
    }).join('')}</div>`;
  }
  if (gainTop.length || lossTop.length) {
    cloudHtml = `<div class="sector-cloud">${renderSectorCloudHeading(sec)}<div style="display:flex;gap:16px;flex-wrap:wrap"><div style="flex:1;min-width:260px">${renderSectorMoveBlock('涨幅前十', gainTop, true)}</div><div style="flex:1;min-width:260px">${renderSectorMoveBlock('跌幅前十', lossTop, false)}</div></div></div>`;
  }
  function normalizedUsSectorRows() {
    return ((usSectorData && usSectorData.items) || []).map(row => {
      const pct = Number(row.change_pct);
      return {
        ...row,
        name: row.label || row.name || row.symbol || '',
        pct: Number.isFinite(pct) ? pct : null,
      };
    }).filter(row => row.name);
  }
  function renderUsSectorMoveBlock(title, list, fallbackTone) {
    if (!list || !list.length) {
      const emptyText = fallbackTone === 'up' ? '暂无上涨板块' : '暂无下跌板块';
      return `<h3>${title}</h3><div class="empty" style="padding:18px">${emptyText}</div>`;
    }
    return `<h3>${title}</h3><div class="sector-grid">${list.slice(0,10).map(s => {
      const pct = Number(s.pct);
      const cls = Number.isFinite(pct) ? (pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat') : fallbackTone;
      const sign = Number.isFinite(pct) && pct > 0 ? '+' : '';
      const mapping = Array.isArray(s.a_share_mapping) && s.a_share_mapping.length ? s.a_share_mapping.slice(0, 3).join('、') : (s.kind === 'theme' ? '主题ETF' : '行业ETF');
      const symbol = s.symbol ? `${s.symbol} · ` : '';
      const priceText = `${symbol}${Number.isFinite(Number(s.price)) ? fmtNumber(s.price) : '--'}`;
      const pctText = Number.isFinite(pct) ? `${sign}${fmtNumber(pct)}%` : '--';
      const titleText = `${s.name || ''} ${priceText} ${pctText} ${mapping}`.trim();
      return `<div class="hot-item us-sector-card ${cls}" title="${esc(titleText)}"><div class="sector-name">${esc(s.name)}</div><div class="hot-price">${esc(priceText)}</div><div class="sector-pct">${esc(pctText)}</div><div class="us-sector-map">${esc(mapping)}</div></div>`;
    }).join('')}</div>`;
  }
  function renderUsSectorMarketBlock() {
    const rows = normalizedUsSectorRows();
    if (!rows.length) {
      const text = usSectorData && usSectorData.error ? `美股板块行情暂不可用：${esc(usSectorData.error)}` : '美股板块行情加载中...';
      return `<div class="sector-cloud">${renderSectorCloudHeading(usSectorData)}<div class="empty" style="padding:18px">${text}</div></div>`;
    }
    const gainRows = rows.filter(row => Number.isFinite(row.pct) && row.pct > 0).sort((a, b) => b.pct - a.pct);
    const lossRows = rows.filter(row => Number.isFinite(row.pct) && row.pct < 0).sort((a, b) => a.pct - b.pct);
    return `<div class="sector-cloud us-sector-cloud">${renderSectorCloudHeading(usSectorData)}<div class="sector-columns"><div class="sector-column">${renderUsSectorMoveBlock('涨幅前十', gainRows, 'up')}</div><div class="sector-column">${renderUsSectorMoveBlock('跌幅前十', lossRows, 'down')}</div></div></div>`;
  }
  const indexHtml = renderSessionMarketGroups();
  const marketFlowHtml = renderMarketFlowBlock();
  const aShareMarketHtml = `${cloudHtml}${hotHtml}${marketFlowHtml}${mfHtml}`;
  const marketRegion = resolvedIndicesMarketRegion(aIndexItems);
  const marketUsesUsSectors = marketRegion === 'us';
  const marketHtml = marketUsesUsSectors ? renderUsSectorMarketBlock() : aShareMarketHtml;
  const usSectorCount = normalizedUsSectorRows().length;
  const marketModuleCount = marketUsesUsSectors ? usSectorCount : [cloudHtml, hotHtml, marketFlowHtml, mfHtml].filter(Boolean).length;
  const hasMarketPayload =
    marketUsesUsSectors ? usSectorCount > 0 : (
      ['gain_top', 'loss_top', 'sectors', 'items'].some(key => Array.isArray(sec[key])) ||
      ['amount_top', 'turnover_top', 'volume_top', 'items'].some(key => Array.isArray(hot[key])) ||
      ['inflow', 'outflow'].some(key => Array.isArray(mf[key]))
  );
  const activePanel = indicesViewMode === 'market' ? 'market' : 'index';
  const activeTitleHtml = activePanel === 'index' ? '<h2 class="indices-part-title">指数</h2>' : '';
  const activeMeta = activePanel === 'market' ? `${marketModuleCount || 0} ${marketUsesUsSectors ? '项' : '组'}` : `${items.length} 项`;
  const marketRegionSwitchHtml = activePanel === 'market' ? `
    <div class="market-region-switch" role="group" aria-label="行情市场切换" title="${indicesMarketRegionOverride ? '当前为手动选择' : '当前按交易时段自动选择'}">
      <button type="button" class="market-region-btn ${marketRegion === 'a_share' ? 'active' : ''}" data-market-region="a_share" aria-pressed="${marketRegion === 'a_share' ? 'true' : 'false'}" onclick="setIndicesMarketRegion('a_share')">A股</button>
      <button type="button" class="market-region-btn ${marketRegion === 'us' ? 'active' : ''}" data-market-region="us" aria-pressed="${marketRegion === 'us' ? 'true' : 'false'}" onclick="setIndicesMarketRegion('us')">美股</button>
    </div>` : '';
  const activeHtml = activePanel === 'market'
    ? (marketHtml || `<div class="empty" style="padding:18px">${hasMarketPayload ? '暂无行情数据' : '行情加载中...'}</div>`)
    : (indexHtml || '<div class="empty" style="padding:18px">暂无指数数据</div>');
  return `${errorHtml}<div class="indices-page">
    <div class="indices-switch" role="group" aria-label="指数行情切换">
      <button type="button" class="indices-switch-btn ${activePanel === 'index' ? 'active' : ''}" aria-pressed="${activePanel === 'index' ? 'true' : 'false'}" onclick="setIndicesViewMode('index')">指数</button>
      <button type="button" class="indices-switch-btn ${activePanel === 'market' ? 'active' : ''}" aria-pressed="${activePanel === 'market' ? 'true' : 'false'}" onclick="setIndicesViewMode('market')">行情</button>
    </div>
    <section class="indices-part" id="${activePanel === 'market' ? 'market-overview' : 'indices-overview'}">
      <div class="indices-part-head"><div class="indices-part-title-row">${activeTitleHtml}${marketRegionSwitchHtml}</div><div class="indices-part-meta">${activeMeta}</div></div>
      <div class="${activePanel === 'market' ? 'indices-market-stack' : 'indices-index-stack'}">${activeHtml}</div>
    </section>
  </div>`;
}
function toggleHotStockSort(sort) {
  hotStockSortBy = sort;
  fetch('/api/hot_stocks?sort_by=' + sort)
    .then(r => r.ok ? r.json() : null)
    .then(d => { if (d) hotStocksData = d; })
    .then(() => render())
    .catch(() => {});
}
async function triggerB1Scan() {
  const remaining = Number(b1ScreenData.cooldown_remaining_seconds || 0);
  if (b1ScreenData.running || remaining > 0) return;
  b1ScreenData = {...b1ScreenData, running:true, error:''};
  renderB1Screen();
  try {
    const res = await actionFetch('/api/b1_screen/trigger');
    const d = await res.json();
    b1ScreenData = {...b1ScreenData, ...d};
    renderB1Screen();
    setTimeout(() => load().catch(console.error), 1200);
  } catch (err) {
    b1ScreenData = {...b1ScreenData, running:false, error:String(err)};
    renderB1Screen();
  }
}
function renderB1Screen() {
  const d = b1ScreenData;
  const items = d.items || [];
  const err = d.error || '';
  const running = !!d.running;
  const cooldownRemaining = Number(d.cooldown_remaining_seconds || 0);
  const cooling = !running && cooldownRemaining > 0;
  const statusText = running ? `⏳ 计算中${d.started_at ? ' · 开始 ' + esc(d.started_at.slice(11)) : ''}` : `🕐 扫描时间: ${esc(d.generated_at || '--')} · 高流动性主板扫描 ${esc(d.count || items.length)} 只入选`;
  const header = `<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px;color:var(--muted);font-size:13px;flex-wrap:wrap">
    <span>${statusText}</span>
  </div>`;
  let html = renderPracticePanel() + header;
  if (running) {
    html += `<div class="empty" style="border-color:rgba(124,92,255,.35);color:#c4b5fd;background:rgba(124,92,255,.08)">⏳ 多战法正在计算中，完成后页面会自动刷新；当前下方仍显示上一版缓存结果。</div>`;
  }
  if (err) {
    html += `<div class="empty" style="color:#f87171">⚠️ ${esc(err)}</div>`;
  } else if (!items.length) {
    html += '<div class="empty">暂无多战法结果，请等待扫描完成…</div>';
  } else {
    const fallbackStrategyMeta = {
      trend_pullback: {label:'趋势回踩',  color:'#60a5fa'},
      breakout:       {label:'突破确认',  color:'#ec4899'},
      shaofu_b1:      {label:'少妇B1',    color:'#f97316'},
      b2_confirm:     {label:'B2确认',    color:'#22c55e'},
      b3_accelerate:  {label:'B3中继',    color:'#a78bfa'},
      super_b1:       {label:'超级B1',    color:'#fb7185'},
    };
    const STRATEGY_META = {...fallbackStrategyMeta, ...(d.strategy_meta || {})};
    const tierCounts = {high:0, mid:0, low:0};
    for (const item of items) {
      const s = item.best_score || item.score || 0;
      const threshold = Number(item.entry_threshold || 8);
      const hardBlockers = item.hard_blockers || [];
      const tradeReady = !!item.actionable && !hardBlockers.length && s >= threshold;
      if (tradeReady) tierCounts.high++;
      else if (s >= threshold - 1.5) tierCounts.mid++;
      else tierCounts.low++;
    }
    html += `<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px">
      <span style="padding:4px 10px;border-radius:999px;background:rgba(52,211,153,.15);color:#34d399;border:1px solid rgba(52,211,153,.3);font-size:12px">🥇 试仓 ${tierCounts.high}只</span>
      <span style="padding:4px 10px;border-radius:999px;background:rgba(251,191,36,.15);color:#fbbf24;border:1px solid rgba(251,191,36,.3);font-size:12px">🥈 等确认 ${tierCounts.mid}只</span>
      <span style="padding:4px 10px;border-radius:999px;background:rgba(148,163,184,.12);color:#94a3b8;border:1px solid rgba(148,163,184,.2);font-size:12px">👀 仅观察 ${tierCounts.low}只</span>
    </div>`;
    const distribution = d.strategy_distribution || {};
    const distHtml = Object.entries(distribution).filter(([_, count]) => Number(count) > 0).map(([name, count]) => {
      const sm = STRATEGY_META[name] || {label:name, color:'#94a3b8'};
      return `<span style="padding:4px 10px;border-radius:999px;background:${sm.color}18;color:${sm.color};border:1px solid ${sm.color}38;font-size:12px">${esc(sm.label)} ${Number(count)||0}</span>`;
    }).join('');
    if (distHtml) {
      html += `<div style="display:flex;flex-wrap:wrap;gap:8px;margin:-8px 0 18px">${distHtml}</div>`;
    }
    html += '<div style="display:grid;gap:12px">';
    for (const item of items) {
      const chg = item.change_pct != null ? (item.change_pct > 0 ? '+' : '') + item.change_pct.toFixed(2) + '%' : '--';
      const chgCls = item.change_pct > 0 ? 'up' : item.change_pct < 0 ? 'down' : 'flat';
      const distStr = item.distance_pct != null ? (item.distance_pct > 0 ? '+' : '') + item.distance_pct.toFixed(2) + '%' : '--';
      const bbiUp = item.bbi_upward ? '✅' : '❌';
      const aboveBbi = item.above_bbi ? '✅' : '❌';
      const jRec = item.j_recovering ? '📈回升' : item.j_oversold ? '📉续降' : '--';
      const jInfo = item.min_j_10d != null ? `J最低 ${item.min_j_10d.toFixed(1)} ${jRec}` : '';
      const riskFlags = (item.risk_flags || []).map(f => `<span style="color:#f87171;font-size:11px;margin-left:6px">⚠️${esc(f)}</span>`).join('');
      const hardBlockers = item.hard_blockers || [];
      const hardBlockerFlags = hardBlockers.map(f => `<span style="color:#fbbf24;font-size:11px;margin-left:6px">硬过滤:${esc(f)}</span>`).join('');
      const stratName = item.best_strategy || '';
      const sm = STRATEGY_META[stratName] || {label:stratName||'综合', color:'#94a3b8'};
      let groupBadge = '';
      const finalScore = item.best_score || item.score || 0;
      const entryThreshold = Number(item.entry_threshold || 8);
      const scoreBasis = item.score_basis || '';
      const tradeDiscipline = [item.position_hint, item.time_stop].filter(Boolean).join(' · ');
      const tradeReady = !!item.actionable && !hardBlockers.length && finalScore >= entryThreshold;
      const industryLabel = item.industry || item.sector || item.board || '';
      const groupBadgeBase = 'display:inline-flex;align-items:center;flex:0 0 auto;white-space:nowrap;line-height:1;background:rgba(52,211,153,.15);color:#34d399;padding:6px 10px;border-radius:999px;font-size:11px;font-weight:600';
      if (tradeReady) groupBadge = `<span style="${groupBadgeBase}">交易达标</span>`;
      else if (hardBlockers.length) groupBadge = `<span style="${groupBadgeBase};background:rgba(251,191,36,.15);color:#fbbf24">硬过滤</span>`;
      else if (finalScore >= entryThreshold - 1.5) groupBadge = `<span style="${groupBadgeBase};background:rgba(251,191,36,.15);color:#fbbf24">等确认</span>`;
      else groupBadge = `<span style="${groupBadgeBase};background:rgba(148,163,184,.12);color:#94a3b8">仅观察</span>`;
      html += `<div style="background:rgba(16,19,26,.86);border:1px solid var(--line);border-radius:18px;padding:16px;box-shadow:0 10px 36px rgba(0,0,0,.18)">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:10px">
          <div style="min-width:0;flex:1 1 auto">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0">
              <span style="font-weight:780;font-size:17px;color:#f8fafc">${esc(item.code)} ${esc(item.name)}</span>
              <span style="display:inline-flex;align-items:center;white-space:nowrap;padding:2px 8px;border-radius:999px;background:${sm.color}22;color:${sm.color};font-size:12px;border:1px solid ${sm.color}44">${esc(sm.label)}</span>
            </div>
            ${industryLabel ? `<div style="margin-top:8px"><span style="display:inline-flex;align-items:center;max-width:100%;white-space:nowrap;padding:2px 8px;border-radius:999px;background:rgba(124,92,255,.15);color:#c4b5fd;font-size:12px">${esc(industryLabel)}</span></div>` : ''}
          </div>
          ${groupBadge}
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px">
          <div style="background:rgba(2,6,23,.42);border:1px solid rgba(148,163,184,.10);border-radius:12px;padding:8px 10px;flex:1;min-width:100px">
            <div style="color:#8da0b8;font-size:11px">价格 / 涨跌</div>
            <div style="color:#eef2ff;font-size:14px;font-weight:600">${fmtNumber(item.price)} <span class="index-change ${chgCls}" style="font-size:13px">${esc(chg)}</span></div>
          </div>
          <div style="background:rgba(2,6,23,.42);border:1px solid rgba(148,163,184,.10);border-radius:12px;padding:8px 10px;flex:1;min-width:100px">
            <div style="color:#8da0b8;font-size:11px">${esc(sm.label)}评分</div>
            <div style="color:#eef2ff;font-size:14px;font-weight:600">${item.best_score||item.score}/${item.score_total||10} · 基准≥${entryThreshold}</div>
          </div>
          <div style="background:rgba(2,6,23,.42);border:1px solid rgba(148,163,184,.10);border-radius:12px;padding:8px 10px;flex:1;min-width:100px">
            <div style="color:#8da0b8;font-size:11px">BBI / 距BBI</div>
            <div style="color:#eef2ff;font-size:14px;font-weight:600">${fmtNumber(item.bbi)} / ${esc(distStr)}</div>
          </div>
          <div style="background:rgba(2,6,23,.42);border:1px solid rgba(148,163,184,.10);border-radius:12px;padding:8px 10px;flex:1;min-width:100px">
            <div style="color:#8da0b8;font-size:11px">成交额</div>
            <div style="color:#eef2ff;font-size:14px;font-weight:600">${item.amount_yi != null ? item.amount_yi + '亿' : '--'}</div>
          </div>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:6px;color:#94a3b8;font-size:12px">
          <span>BBI上行 ${bbiUp}</span>
          <span>站上BBI ${aboveBbi}</span>
          <span>${esc(jInfo)}</span>
          ${scoreBasis ? `<span>${esc(scoreBasis)}</span>` : ''}
          ${tradeDiscipline ? `<span>${esc(tradeDiscipline)}</span>` : ''}
          ${hardBlockerFlags}
          ${riskFlags}
        </div>
      </div>`;
    }
    html += '</div>';
  }
  $('feed').innerHTML = html;
}
function ratingDateKey(r) {
  const t = String(r.time || '').trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(t)) return t.slice(0, 10);
  const ts = Number(r.timestamp || 0);
  if (Number.isFinite(ts) && ts > 0) {
    const d = new Date(ts * 1000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
  return '未知日期';
}
function groupRatingRecordsByDay(records) {
  const groups = new Map();
  for (const r of records) {
    const key = ratingDateKey(r);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  return groups;
}
function currentUsRatingRecords(records = filtered()) {
  const groups = groupRatingRecordsByDay(records);
  const days = [...groups.keys()].sort().reverse();
  if (!days.length) return [];
  const day = days[usRatingDayIndex] || days[0];
  return groups.get(day) || [];
}
function shortRatingDate(day) {
  const s = String(day || '');
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
    return `${Number(s.slice(5, 7))}/${Number(s.slice(8, 10))}`;
  }
  return s || '--';
}
function ratingDayButtons(days, restoreDetail = false) {
  const olderDay = days[usRatingDayIndex + 1] || '';
  const newerDay = days[usRatingDayIndex - 1] || '';
  const restoreCall = restoreDetail ? 'restoreRatingDetail();' : '';
  return `
        <button title="查看更早的评级日报" onclick="usRatingDayIndex=Math.min(usRatingDayIndex+1,${days.length-1});render();${restoreCall}refreshVisibleUsQuotes()" ${olderDay ? '' : 'disabled'} style="padding:5px 10px;font-size:12px">‹ ${olderDay ? '更早 ' + esc(shortRatingDate(olderDay)) : '已是最早'}</button>
        <button title="回到更新的评级日报" onclick="usRatingDayIndex=Math.max(usRatingDayIndex-1,0);render();${restoreCall}refreshVisibleUsQuotes()" ${newerDay ? '' : 'disabled'} style="padding:5px 10px;font-size:12px">${newerDay ? '更新 ' + esc(shortRatingDate(newerDay)) : '已是最新'} ›</button>`;
}
function renderUsRatingDay(records) {
  const groups = groupRatingRecordsByDay(records);
  const days = [...groups.keys()].sort().reverse();
  if (!days.length) return '<div class="empty">暂无美股机构买入评级消息</div>';
  const day = days[usRatingDayIndex] || days[0];
  const dayRecords = groups.get(day) || [];
  return `<div class="sector-cloud" style="margin-bottom:14px">
    <div class="rating-day-pager" style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
      <span style="font-weight:700;color:#c7d2fe">${esc(day)}</span>
      <div class="rating-day-actions" style="display:flex;gap:8px">
${ratingDayButtons(days)}
      </div>
    </div>
  </div>
  <div style="display:grid;gap:14px">
    ${dayRecords.map(r => renderRatingCard(r)).join('')}
  </div>`;
}
function fmtUsd(v) { return '$' + (Number(v) || 0).toFixed(2); }
function pctClass(v) { const n = Number(v); return Number.isFinite(n) ? (n >= 0 ? 'pos' : 'neg') : ''; }
function renderMarkdown(s) { let html = esc(s); html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>'); html = html.replace(/`([^`]+)`/g, '<code>$1</code>'); return html; }
function cleanRatingValue(v) { return String(v || '').replace(/^[-–—]\s*/, '').replace(/\*\*/g, '').replace(/\s+/g, ' ').trim(); }
function extractTargetPrice(text) {
  const s = String(text || '').replace(/,/g, '').split(/此前|原为|previously|from\s+\$?/i)[0];
  const arrowMatches = Array.from(s.matchAll(/(?:→|->|至|到|上调至|提高至)\s*\$?\s*([0-9]+(?:\.[0-9]+)?)/g));
  if (arrowMatches.length) { const n = Number(arrowMatches[arrowMatches.length - 1][1]); if (Number.isFinite(n)) return n; }
  const patterns = [/\$\s*([0-9]+(?:\.[0-9]+)?)/g, /([0-9]+(?:\.[0-9]+)?)\s*(?:美元|美金|usd)/gi, /(?:目标价)\s*([0-9]+(?:\.[0-9]+)?)/g];
  let best = null;
  for (const re of patterns) { for (const m of s.matchAll(re)) { const n = Number(m[1]); if (Number.isFinite(n)) best = n; } }
  return best;
}
function parseRatingReport(content) {
  const rawLines = String(content || '').split('\n');
  const lines = rawLines.map(line => line.replace(/\s+$/g, ''));
  const stockHeaderRe = /^(?:[-*]\s+|#{2,4}\s*\d+[）.)]?\s*|\d+[）.)]\s*)\*{0,2}[A-Z][A-Z0-9.]{0,8}\s*(?:\/|（|\()\s*[A-Z]/;
  const boldStockRe = /^\*{1,2}\s*(?:\d+[）.)]\s*)?[A-Z][A-Z0-9.]{2,8}\s*(?:[/(（]|\/\s*[A-Z])/;
  const firstStockIdx = lines.findIndex(line => stockHeaderRe.test(line.trim()) || boldStockRe.test(line.trim()));
  if (firstStockIdx < 0) return null;
  const intro = lines.slice(0, firstStockIdx).join('\n').replace(/^-{3,}\s*$/gm, '').trim();
  const title = (intro.split('\n').map(x => x.trim()).filter(Boolean)[0] || '机构买入评级').replace(/^标题[:：]\s*/, '');
  const summary = intro.split('\n').map(x => x.trim()).filter(Boolean).slice(1).join('\n\n');
  const items = []; let current = null;
  const fieldMap = [
    ['analyst', /^[-–—\s*]*(?:\*\*)?机构\/分析师(?:\*\*)?[:：](.*)$/],
    ['action', /^[-–—\s*]*(?:\*\*)?评级动作(?:\*\*)?[:：](.*)$/],
    ['target', /^[-–—\s*]*(?:\*\*)?目标价(?:\*\*)?[:：](.*)$/],
    ['reason', /^[-–—\s*]*(?:\*\*)?核心理由\/催化剂(?:\*\*)?[:：](.*)$/],
    ['risk', /^[-–—\s*]*(?:\*\*)?风险点(?:\*\*)?[:：](.*)$/],
    ['type', /^[-–—\s*]*(?:\*\*)?适合关注类型(?:\*\*)?[:：](.*)$/]
  ];
  let activeKey = '';
  function parseStockHeader(line) {
    let numberedBoldMatch = line.match(/^(?:[-*]\s+|#{2,4}\s*\d+[）.)]?\s*|\d+[）.)]\s*)\*{1,2}\s*([A-Z][A-Z0-9.]{1,8})\s*[（(]\s*([^)）]+?)\s*[)）]\s*\*{0,2}\s*(?:[—–-]\s*(.*))?$/);
    if (!numberedBoldMatch) { numberedBoldMatch = line.match(/^(?:[-*]\s+|#{2,4}\s*\d+[）.)]?\s*|\d+[）.)]\s*)\*{1,2}\s*([A-Z][A-Z0-9.]{1,8})\s*\/\s*([^*：:]+?)\s*\*{0,2}\s*(?:[—–-]\s*(.*))?$/); }
    if (numberedBoldMatch) { return {name: numberedBoldMatch[1].toUpperCase() + ' / ' + cleanRatingValue(numberedBoldMatch[2] || ''), inline: cleanRatingValue(numberedBoldMatch[3] || '')}; }
    const oldMatch = line.match(/^(?:[-*]\s+|#{2,4}\s*\d+[）.)]?\s*|\d+[）.)]\s*)\*{0,2}([A-Z][A-Z0-9.]{0,8}\s*\/\s*[A-Z][^：:]+?)(?:\*{0,2})\s*(?:[:：](.*))?$/);
    if (oldMatch) return {name: cleanRatingValue(oldMatch[1]), inline: cleanRatingValue(oldMatch[2] || '')};
    let boldMatch = line.match(/^\*{1,2}\s*(?:\d+[）.)]\s*)?([A-Z][A-Z0-9.]{2,8})\s*\/\s*([^：:]+?)(?:\s*\*{1,2}|\s*[:：]|$)/);
    if (!boldMatch) { boldMatch = line.match(/^\*{1,2}\s*(?:\d+[）.)]\s*)?([A-Z][A-Z0-9.]{2,8})\s*[（(]\s*([^)）]+?)\s*[)）]/); }
    if (boldMatch) {
      const ticker = boldMatch[1].toUpperCase(), company = cleanRatingValue(boldMatch[2] || '');
      const rest = line.slice(boldMatch[0].length).trim(), inlineMatch = rest.match(/^[\s\S]*?[:：]\s*(.*)/);
      return {name: ticker + (company ? ' / ' + company : ''), inline: cleanRatingValue(inlineMatch ? inlineMatch[1] : '')};
    }
    return null;
  }
  for (const raw of lines.slice(firstStockIdx)) {
    const line = raw.trim();
    if (!line || /^-{3,}$/.test(line)) continue;
    const parsed = parseStockHeader(line);
    if (parsed) {
      const candidateName = parsed.name;
      if (/报道|来源|链接|检索|摘要/.test(candidateName)) continue;
      if (current) items.push(current);
      current = {name: candidateName}; activeKey = '';
      const inline = parsed.inline;
      if (inline) {
        const sentences = inline.split(/[；;。]/).map(x => cleanRatingValue(x)).filter(Boolean);
        for (const sentence of sentences) {
          if (/目标价|\$\s*\d|\d+(?:\.\d+)?\s*(?:美元|美金)/i.test(sentence) && !current.target) current.target = sentence;
          else if (/机构|分析师|\/\s*[A-Z][A-Za-z .]+/.test(sentence) && !current.analyst) current.analyst = sentence;
          else if (/评级|上调|维持|新覆盖|Buy|Overweight|Outperform|Neutral|Underperform/i.test(sentence) && !current.action) current.action = sentence;
          else if (/风险/.test(sentence) && !current.risk) current.risk = sentence.replace(/^风险是?/, '');
          else if (/适合关注类型/.test(sentence) && !current.type) current.type = sentence.replace(/^适合关注类型[:：]?/, '');
          else if (!current.reason) current.reason = sentence;
          else current.reason = cleanRatingValue(current.reason + '；' + sentence);
        }
      }
      continue;
    }
    if (!current) continue;
    let matched = false;
    for (const [key, re] of fieldMap) { const m = line.match(re); if (m) { current[key] = cleanRatingValue(m[1]); activeKey = key; matched = true; break; } }
    if (!matched && activeKey) current[activeKey] = cleanRatingValue((current[activeKey] || '') + ' ' + line);
  }
  if (current) items.push(current);
  const validItems = items.filter(item => /^[A-Z][A-Z0-9.]{1,8}\s*\/?\s*/.test(item.name));
  if (!validItems.length) return null;
  return {title, summary, items: validItems};
}
function inlineField(label, value, className = '') {
  if (!value) return '';
  return `<div class="inline-field ${esc(className)}"><div class="inline-label">${esc(label)}</div><div class="inline-value">${renderMarkdown(value)}</div></div>`;
}
function ratingCompanyDetail(ticker, company, quote) {
  const lines = [`股票代码：${ticker}`];
  const companyName = cleanRatingValue(company);
  const sector = cleanRatingValue(quote && quote.sector);
  const industry = cleanRatingValue(quote && quote.industry);
  if (companyName) lines.push(`公司：${companyName}`);
  if (sector || industry) lines.push(`分类：${[sector, industry].filter(Boolean).join(' / ')}`);
  return lines.join('\n');
}
function ratingMetaDetail(item) {
  const lines = [];
  const analyst = cleanRatingValue(item && item.analyst);
  const type = cleanRatingValue(item && item.type);
  if (analyst) lines.push(`机构 / 分析师：${analyst}`);
  if (type) lines.push(`关注类型：${type}`);
  return lines.join('\n');
}
function safeDomIdPart(value) {
  return String(value || '').replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'row';
}
function ratingStableRowId(reportKey, ticker, idx) {
  return `rating-${safeDomIdPart(reportKey)}-${safeDomIdPart(ticker)}-${idx}`;
}
function renderRatingPriceTable(report, reportTime, reportKey) {
  const seen = new Set();
  const ratingItems = report.items.filter(item => {
    const ticker = String((item.name || '').split('/')[0] || '').trim().toUpperCase();
    if (!ticker || seen.has(ticker)) return false;
    seen.add(ticker); return true;
  });
  const rows = ratingItems.map((item, idx) => {
    const [tickerRaw, ...companyParts] = item.name.split('/').map(x => x.trim());
    const ticker = (tickerRaw || item.name || '').toUpperCase();
    const company = companyParts.join(' / ');
    const target = extractTargetPrice(item.target || item.action || '');
    const quote = (usQuotesData.items || {})[ticker] || {};
    const price = Number(quote.price);
    const upside = Number.isFinite(price) && price > 0 && Number.isFinite(target) ? ((target / price - 1) * 100) : null;
    const rowId = ratingStableRowId(reportKey || reportTime, ticker, idx);
    return `<tr id="rating-row-${rowId}" class="rating-data-row" onclick="toggleRatingDetail('${rowId}')" title="点击向下展开看多逻辑、机构/分析师和风险点">
      <td data-label="股票"><span class="ticker">${esc(ticker)}</span></td>
      <td data-label="当前股价"><span class="price">${Number.isFinite(price) ? fmtUsd(price) : '--'}</span></td>
      <td data-label="目标股价">${Number.isFinite(target) ? '<span class="target">' + fmtUsd(target) + '</span>' : (item.target ? renderMarkdown(item.target) : '--')}${item.action ? '<span class="rating-action-inline">' + renderMarkdown(item.action.replace(/，.*$/, '')) + '</span>' : ''}</td>
      <td data-label="目标空间">${Number.isFinite(upside) ? '<span class="upside ' + pctClass(upside) + '">' + (upside >= 0 ? '+' : '') + upside.toFixed(1) + '%</span>' : '<span class="muted">--</span>'}</td>
    </tr>
    <tr id="rating-detail-${rowId}" class="rating-detail-row"><td class="rating-detail-cell" colspan="4">
      <div class="rating-inline-detail">
        <div class="rating-inline-grid">
          ${inlineField('公司详情', ratingCompanyDetail(ticker, company, quote), 'rating-detail-company')}
          ${inlineField('评级信息', ratingMetaDetail(item), 'rating-detail-meta')}
          ${inlineField('看多逻辑 / 催化剂', item.reason, 'rating-detail-reason')}
          ${inlineField('风险点', item.risk, 'rating-detail-risk')}
        </div>
      </div>
    </td></tr>`;
  }).join('');
  return `<div class="rating-table-wrap">
    <div class="rating-table-title"><span>股票价格对照表</span><small>${reportTime ? esc(reportTime) : ''}</small></div>
    <table class="rating-table">
      <thead><tr><th>股票</th><th>当前股价</th><th>目标股价</th><th>目标空间</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>`;
}
function renderRatingCard(r) {
  const report = parseRatingReport(r.content);
  if (!report) {
    const lines = r.content.split('\n');
    return `<div class="card">${lines.slice(0, 30).map(l => esc(l)).join('<br>')}${lines.length > 30 ? '<br>...' : ''}</div>`;
  }
  const tableHtml = renderRatingPriceTable(report, r.time, recordKey(r));
  return `<article class="card rating-card">${tableHtml}</article>`;
}
function toggleRatingDetail(rowId) {
  const detailRow = document.getElementById('rating-detail-' + rowId);
  if (!detailRow) return;
  const dataRow = document.getElementById('rating-row-' + rowId);
  const wasOpen = detailRow.classList.contains('open');
  // Close all other open detail rows in the same table
  const table = dataRow ? dataRow.closest('table') : null;
  if (table) table.querySelectorAll('.rating-detail-row.open').forEach(el => el.classList.remove('open'));
  if (table) table.querySelectorAll('.rating-data-row.expanded').forEach(el => el.classList.remove('expanded'));
  if (!wasOpen) {
    detailRow.classList.add('open');
    if (dataRow) dataRow.classList.add('expanded');
    ratingExpandedRowId = rowId;
  } else {
    ratingExpandedRowId = '';
  }
}
function restoreRatingDetail() {
  if (!ratingExpandedRowId) return;
  const detailRow = document.getElementById('rating-detail-' + ratingExpandedRowId);
  if (!detailRow) { ratingExpandedRowId = ''; return; }
  const dataRow = document.getElementById('rating-row-' + ratingExpandedRowId);
  detailRow.classList.add('open');
  if (dataRow) dataRow.classList.add('expanded');
}
function renderUsRatingDay(records) {
  const groups = groupRatingRecordsByDay(records);
  const days = [...groups.keys()].sort().reverse();
  if (!days.length) return '<div class="empty">暂无美股机构买入评级消息</div>';
  const day = days[usRatingDayIndex] || days[0];
  const dayRecords = groups.get(day) || [];
  return `<div class="sector-cloud" style="margin-bottom:14px">
    <div class="rating-day-pager" style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
      <span style="font-weight:700;color:#c7d2fe">${esc(day)}</span>
      <div class="rating-day-actions" style="display:flex;gap:8px">
${ratingDayButtons(days, true)}
      </div>
    </div>
  </div>
  <div style="display:grid;gap:14px">
    ${dayRecords.map(r => renderRatingCard(r)).join('')}
  </div>`;
}
function renderHistoryControls(records) {
  if (!isMessageCategory()) return '';
  if (activeCategory === 'x_monitor') {
    return renderXPager(records);
  }
  if (activeCategory === 'us_ratings') {
    return '';
  }
  const shown = records.length;
  const total = activeCategoryTotal();
  if (!total || shown >= total) {
    return `<div class="sector-cloud" style="margin-top:2px;padding:12px 14px;color:#94a3b8;font-size:13px">已显示全部历史：${shown} / ${total || shown}</div>`;
  }
  return `<div class="sector-cloud" style="margin-top:2px;padding:12px 14px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
    <span style="color:#94a3b8;font-size:13px">已显示 ${shown} / ${total} 条历史</span>
    <button onclick="loadMoreMessages()" ${loadingMoreHistory ? 'disabled' : ''} style="padding:8px 12px;font-size:13px">${loadingMoreHistory ? '加载中...' : '加载更多历史'}</button>
  </div>`;
}
function renderXPager(records) {
  const limit = messagePageLimit('x_monitor');
  const total = activeCategoryTotal();
  const totalPages = Math.max(1, Math.ceil((total || records.length || 1) / limit));
  const page = Math.min(totalPages, Math.floor(xPageOffset / limit) + 1);
  const first = total && records.length ? xPageOffset + 1 : (records.length ? 1 : 0);
  const last = total && records.length ? Math.min(xPageOffset + records.length, total) : records.length;
  const prevOffset = Math.max(0, xPageOffset - limit);
  const nextOffset = xPageOffset + limit;
  const lastOffset = Math.max(0, (totalPages - 1) * limit);
  const atFirst = xPageOffset <= 0;
  const atLast = total ? nextOffset >= total : records.length < limit;
  const disabled = loadingMoreHistory ? 'disabled' : '';
  return `<div class="sector-cloud x-pager">
    <div class="x-pager-status">第 ${page} / ${totalPages} 页 · ${first}-${last} / ${total || last} 条${loadingMoreHistory ? ' · 加载中...' : ''}</div>
    <div class="x-pager-actions">
      <button class="x-page-btn" onclick="loadXPage(0)" ${disabled || atFirst ? 'disabled' : ''}>首页</button>
      <button class="x-page-btn" onclick="loadXPage(${prevOffset})" ${disabled || atFirst ? 'disabled' : ''}>上一页</button>
      <button class="x-page-btn" onclick="loadXPage(${nextOffset})" ${disabled || atLast ? 'disabled' : ''}>下一页</button>
      <button class="x-page-btn" onclick="loadXPage(${lastOffset})" ${disabled || atLast ? 'disabled' : ''}>末页</button>
    </div>
  </div>`;
}
function shortHash(text) {
  let h = 2166136261;
  const s = String(text || '');
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(36);
}
function marketRecordKey(r) {
  return 'market-' + shortHash(recordKey(r));
}
function cleanMarketLine(line) {
  return String(line || '').replace(/\*\*/g, '').replace(/`/g, '').replace(/\s+/g, ' ').trim();
}
function marketReportType(content) {
  const s = String(content || '');
  if (/竞价/.test(s)) return '竞价';
  if (/午盘/.test(s)) return '午盘';
  if (/盘后/.test(s)) return '盘后';
  return '盘面';
}
function marketSectionLines(lines, headingText, limit = 3) {
  const start = lines.findIndex(line => cleanMarketLine(line).includes(headingText));
  if (start < 0) return [];
  const result = [];
  for (const raw of lines.slice(start + 1)) {
    const line = cleanMarketLine(raw);
    if (!line) continue;
    if (/\*\*.+\*\*/.test(raw) || /^[📊🔥💰⚡📈💡⚠️🌡️📌👀ℹ️]/u.test(line)) break;
    result.push(line);
    if (result.length >= limit) break;
  }
  return result;
}
function summarizeMarketRecord(r) {
  const raw = String(r.content || '');
  const lines = raw.split('\n').map(x => x.trim()).filter(Boolean);
  const cleanLines = lines.map(cleanMarketLine).filter(Boolean);
  const titleLine = cleanLines[0] || '盘面监控';
  const title = titleLine.replace(/^牛牛大王[，,]\s*/, '').replace(/来了[:：]?$/, '').trim() || '盘面监控';
  const timeLine = cleanLines.find(line => /\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}/.test(line)) || '';
  const timeMatch = timeLine.match(/\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}/);
  const mood = cleanLines.find(line => line.startsWith('💬')) || '';
  const overview = cleanLines.find(line => /^样本\s/.test(line) || /^涨停池\s/.test(line)) || '';
  const volume = cleanLines.find(line => /成交额\s/.test(line)) || '';
  const hotLines = marketSectionLines(lines, '热门板块', 3);
  const chips = [];
  for (const line of [overview, volume, ...hotLines]) {
    const text = truncateText(line.replace(/^💬\s*/, ''), 34);
    if (text) chips.push(text);
  }
  return {
    title,
    type: marketReportType(raw),
    time: timeMatch ? timeMatch[0] : (r.time || ''),
    preview: truncateText((mood || overview || titleLine).replace(/^💬\s*/, ''), 150),
    chips: chips.slice(0, 5)
  };
}
function marketLeadingIcon(text) {
  const m = String(text || '').match(/^(📊|🔥|💰|⚡|📈|💡|⚠️|⚠|🌡️|🌡|📌|👀|ℹ️|ℹ)\s*/u);
  return m ? {icon: m[1], rest: String(text || '').slice(m[0].length).trim()} : {icon: '', rest: String(text || '').trim()};
}
function marketSectionTone(title, icon) {
  const s = `${title || ''} ${icon || ''}`;
  if (/风险|⚠/.test(s)) return 'risk';
  if (/资金/.test(s)) return 'flow';
  if (/热门|强势|封单|热度|🔥|⚡|🌡|📌/.test(s)) return 'hot';
  if (/操作|提示|观察|💡|👀/.test(s)) return 'tip';
  if (/概况|情绪|📊/.test(s)) return 'overview';
  return '';
}
function marketHeadingInfo(raw) {
  const clean = cleanMarketLine(raw);
  if (!clean) return null;
  const leading = marketLeadingIcon(clean);
  const titleSource = leading.rest || clean;
  const titleParts = titleSource.split(/[·|]/).map(x => x.trim()).filter(Boolean);
  const title = (titleParts[0] || titleSource).replace(/[:：]$/, '').trim();
  const hasMarkdownHeading = /\*\*.+\*\*/.test(String(raw || ''));
  const knownHeading = /^(市场概况|竞价情绪|开盘价强弱|热门板块|竞价强势板块|资金流向|强势个股|成交活跃|竞价成交活跃|操作提示|风险|复合热度|涨停封单|封单|跌停风险|重点观察)/.test(title);
  if (!hasMarkdownHeading && !knownHeading) return null;
  return {
    title: title || '盘面小节',
    meta: titleParts.slice(1).join(' · '),
    icon: leading.icon || '•',
    tone: marketSectionTone(title, leading.icon)
  };
}
function parseMarketDetail(content) {
  const sections = [];
  const intro = [];
  let current = null;
  const pushCurrent = () => {
    if (current && (current.items.length || current.meta)) sections.push(current);
    current = null;
  };
  for (const raw of String(content || '').split('\n')) {
    if (!String(raw || '').trim()) continue;
    const heading = marketHeadingInfo(raw);
    if (heading) {
      pushCurrent();
      current = {...heading, items: []};
      continue;
    }
    const clean = cleanMarketLine(raw);
    if (!clean) continue;
    if (current) current.items.push(clean);
    else intro.push(clean);
  }
  pushCurrent();
  return {intro, sections};
}
function marketMoodLine(sections) {
  for (const section of sections) {
    for (const line of section.items || []) {
      const clean = cleanMarketLine(line);
      if (/^💬/.test(clean)) return clean.replace(/^💬\s*/, '').trim();
    }
  }
  return '';
}
function marketMetricTone(label, value) {
  const n = Number(String(value || '').replace(/[^\d.-]/g, ''));
  if (/上涨|涨停/.test(label)) return 'up';
  if (/下跌|跌停/.test(label)) return 'down';
  if (Number.isFinite(n) && n > 0 && /^\+/.test(String(value || '').trim())) return 'up';
  if (Number.isFinite(n) && n < 0) return 'down';
  return '';
}
function marketSummaryMetrics(sections) {
  const overview = sections.find(section => /市场概况|竞价情绪/.test(section.title)) || sections[0];
  if (!overview) return [];
  const metrics = [];
  const seen = new Set();
  for (const line of overview.items || []) {
    const clean = cleanMarketLine(line).replace(/^💬\s*/, '').trim();
    for (const part of clean.split(/[|·]/).map(x => x.trim()).filter(Boolean)) {
      const m = part.match(/^(涨停池|跌停池|竞价额|竞价量|成交额|样本|高开|平开|低开|强高开|深低开|上涨|下跌|平盘|涨停|跌停)\s*([+\-]?\d[\d,.]*(?:\.\d+)?\s*(?:只|亿手|万手|手|亿|万亿|万|%)?)/);
      if (!m || seen.has(m[1])) continue;
      seen.add(m[1]);
      metrics.push({label: m[1], value: m[2].replace(/\s+/g, ''), tone: marketMetricTone(m[1], m[2])});
      if (metrics.length >= 8) return metrics;
    }
  }
  return metrics;
}
function isMarketMetricLine(line) {
  const clean = cleanMarketLine(line).replace(/^💬\s*/, '').trim();
  return /(?:^|[|·]\s*)(涨停池|跌停池|竞价额|竞价量|成交额|样本|高开|平开|低开|强高开|深低开|上涨|下跌|平盘|涨停|跌停)\s*[+\-]?\d/.test(clean);
}
function renderMarketOverview(parsed) {
  const mood = marketMoodLine(parsed.sections);
  const metrics = marketSummaryMetrics(parsed.sections);
  if (!mood && !metrics.length) return '';
  const moodHtml = mood ? `<div class="market-mood-panel"><div class="market-mood-label">核心判断</div><div class="market-mood-text">${esc(mood)}</div></div>` : '';
  const metricHtml = metrics.length ? `<div class="market-metric-grid">${metrics.map(item => `
    <div class="market-metric-item">
      <div class="market-metric-label">${esc(item.label)}</div>
      <div class="market-metric-value ${esc(item.tone)}">${esc(item.value)}</div>
    </div>`).join('')}</div>` : '';
  return `<div class="market-detail-overview">${moodHtml}${metricHtml}</div>`;
}
function marketSectionDisplayItems(section) {
  const isOverview = /市场概况|竞价情绪/.test(section.title || '');
  return (section.items || []).filter(line => {
    const clean = cleanMarketLine(line);
    if (/^💬/.test(clean)) return false;
    if (isOverview && isMarketMetricLine(clean)) return false;
    return true;
  });
}
function renderMarketSignedText(text, options = {}) {
  const source = String(text || '');
  const colorUnsignedMoney = !!options.colorUnsignedMoney;
  const pattern = /((?:sh|sz|bj)?\d{6}\s+[*A-Za-z\u4e00-\u9fa5][*A-Za-z0-9\u4e00-\u9fa5·]{1,12})|([+\-]\d[\d,.]*(?:\.\d+)?\s*(?:%|万亿|亿手|万手|手|亿|万|元)?|\d[\d,.]*(?:\.\d+)?\s*(?:万亿|亿手|万手|手|亿))/gi;
  let html = '';
  let last = 0;
  for (const match of source.matchAll(pattern)) {
    const token = match[0];
    const start = match.index || 0;
    html += esc(source.slice(last, start));
    if (match[1]) {
      html += `<span class="market-symbol">${esc(token)}</span>`;
      last = start + token.length;
      continue;
    }
    const compact = token.replace(/\s+/g, '');
    const unsignedMoney = !/^[+\-]/.test(compact) && /(?:万亿|亿)$/.test(compact);
    const cls = compact.startsWith('-')
      ? 'down'
      : (compact.startsWith('+') || (colorUnsignedMoney && unsignedMoney) ? 'up' : '');
    html += cls ? `<span class="market-num ${cls}">${esc(token)}</span>` : esc(token);
    last = start + token.length;
  }
  html += esc(source.slice(last));
  return html;
}
function renderMarketDetailLine(text, sectionTone = '') {
  const clean = cleanMarketLine(text).replace(/^·\s*/, '').trim();
  if (!clean) return '';
  const flow = clean.match(/^(流入|流出)[:：]\s*(.+)$/);
  if (flow) {
    return `<div class="market-detail-line flow"><span class="market-flow-label">${esc(flow[1])}</span><span class="market-flow-value">${renderMarketSignedText(flow[2], {colorUnsignedMoney: true})}</span></div>`;
  }
  const cls = ['market-detail-line', 'item'];
  if (/^数据暂不可用|^数据为|^ℹ️|^ℹ/.test(clean)) cls.push('note');
  if (sectionTone === 'risk') cls.push('risk');
  if (sectionTone === 'tip') cls.push('tip');
  const colorUnsignedMoney = sectionTone === 'flow' || /净额/.test(clean);
  return `<div class="${cls.join(' ')}"><span>${renderMarketSignedText(clean, {colorUnsignedMoney})}</span></div>`;
}
function renderMarketSection(section) {
  const items = marketSectionDisplayItems(section);
  if (!items.length && /市场概况|竞价情绪/.test(section.title || '')) return '';
  if (!items.length && !section.meta) return '';
  const tone = section.tone || '';
  const wide = /热门板块|竞价强势板块|资金流向|竞价成交活跃/.test(section.title || '') ? ' wide' : '';
  const count = items.length ? `<span class="market-section-count">${items.length} 条</span>` : '';
  const meta = section.meta ? `<span class="market-section-count">${esc(section.meta)}</span>` : count;
  const body = items.map(line => renderMarketDetailLine(line, tone)).filter(Boolean).join('');
  return `<section class="market-section ${esc(tone)}${wide}">
    <div class="market-section-head">
      <div class="market-section-title-wrap"><span class="market-section-icon">${esc(section.icon || '•')}</span><span class="market-section-title">${esc(section.title || '盘面小节')}</span></div>
      ${meta}
    </div>
    ${body ? `<div class="market-section-body">${body}</div>` : ''}
  </section>`;
}
function renderMarketDetail(content) {
  const parsed = parseMarketDetail(content);
  const overview = renderMarketOverview(parsed);
  const intro = parsed.intro.filter(line => !/^牛牛大王[，,]/.test(line)).map(line => renderMarketDetailLine(line)).filter(Boolean).join('');
  const sections = parsed.sections.map(renderMarketSection).filter(Boolean).join('');
  if (!overview && !intro && !sections) {
    const fallback = String(content || '').split('\n').map(line => renderMarketDetailLine(line)).filter(Boolean).join('');
    return `<div class="market-detail-box">${fallback}</div>`;
  }
  return `<div class="market-detail-box">${overview}${intro ? `<div class="market-section-list"><section class="market-section wide"><div class="market-section-head"><div class="market-section-title-wrap"><span class="market-section-icon">•</span><span class="market-section-title">摘要</span></div></div><div class="market-section-body">${intro}</div></section></div>` : ''}${sections ? `<div class="market-section-list">${sections}</div>` : ''}</div>`;
}
function renderMarketMonitorCard(r) {
  const key = marketRecordKey(r);
  const summary = summarizeMarketRecord(r);
  const open = marketExpandedRecordKey === key;
  const chips = summary.chips.map(text => {
    const cls = /\s-\d/.test(text) ? ' down' : /\s\+\d/.test(text) ? ' up' : '';
    return `<span class="market-chip${cls}">${esc(text)}</span>`;
  }).join('');
  return `<article class="market-monitor-card ${open ? 'open' : ''}" data-market-key="${esc(key)}" aria-expanded="${open ? 'true' : 'false'}">
    <div class="market-card-head">
      <div>
        <div class="market-card-title-row"><span class="market-card-title">${esc(summary.title)}</span>${summary.time ? `<span class="market-card-time">${esc(summary.time)}</span>` : ''}</div>
        <div class="market-card-preview">${esc(summary.preview || '等待盘面摘要')}</div>
        ${chips ? `<div class="market-chip-row">${chips}</div>` : ''}
      </div>
      <div class="market-card-side"><span class="market-type">${esc(summary.type)}</span><span class="market-chevron">›</span></div>
    </div>
    ${open ? `<div class="market-card-detail">${renderMarketDetail(r.content || '')}</div>` : ''}
  </article>`;
}
function usMarketToneClass(tone) {
  return ['offensive', 'balanced', 'cautious', 'defensive'].includes(tone) ? tone : 'neutral';
}
function renderUsMarketSummaryCard() {
  const d = usMarketSummaryData || {};
  if (d.loading && !d.generated_at) {
    return `<section class="us-market-summary-card neutral">
      <div class="us-market-head">
        <div><div class="us-market-title">隔夜美股盘面总结</div><div class="us-market-sub">正在加载昨晚美股盘面...</div></div>
        <div class="us-market-tone">加载中</div>
      </div>
      <div class="us-market-brief">这条摘要会作为今日买卖选股的外盘背景，盘中仍以 A 股竞价、资金流和板块联动确认。</div>
    </section>`;
  }
  const tone = usMarketToneClass(String(d.tone || 'neutral'));
  const toneLabel = d.tone_label || '中性';
  const target = d.target_us_date || '--';
  const dateRule = d.date_rule || '周一显示上周五美股盘面；其他日期显示前一美股交易日。';
  const summary = d.summary || (d.error ? '隔夜美股盘面暂不可用，今日先按 A 股自身信号执行。' : '等待隔夜美股盘面总结。');
  const metrics = (d.metrics || []).slice(0, 8);
  const metricHtml = metrics.length ? `<div class="us-market-metrics">${metrics.map(m => {
    const pct = Number(m.change_pct);
    const pctCls = Number.isFinite(pct) ? upCls(pct) : 'flat';
    return `<div class="us-market-metric">
      <div class="us-market-metric-label">${esc(m.label || '')}</div>
      <div class="us-market-metric-value"><span>${esc(m.value || '--')}</span><span class="us-market-pct ${pctCls}">${esc(m.change_pct_text || '--')}</span></div>
    </div>`;
  }).join('')}</div>` : '';
  const mappings = (d.sector_mappings || []).slice(0, 5);
  const mappingHtml = mappings.length ? `<div class="us-market-map">${mappings.map(m => {
    const pct = Number(m.change_pct);
    const pctCls = Number.isFinite(pct) ? upCls(pct) : 'flat';
    const mapText = Array.isArray(m.a_share_mapping) ? m.a_share_mapping.slice(0, 4).join(' / ') : (m.a_share_mapping || '');
    const sector = m.proxy ? `${m.us_sector || ''}(${m.proxy})` : (m.us_sector || '');
    return `<div class="us-market-map-line"><strong>${esc(sector)}</strong> <span class="map-pct ${pctCls}">${esc(m.change_pct_text || '--')}</span> · ${esc(mapText || '相关板块')} · ${esc(m.strategy || m.bias || '观察')}</div>`;
  }).join('')}</div>` : '';
  const guidance = (d.guidance_lines || []).slice(0, 7);
  const guidanceHtml = guidance.length ? `<div class="us-market-guidance">${guidance.map(line => `<div class="us-market-guidance-line"><span>${esc(line)}</span></div>`).join('')}</div>` : '';
  return `<section class="us-market-summary-card ${tone}">
    <div class="us-market-head">
      <div>
        <div class="us-market-title">隔夜美股盘面总结</div>
        <div class="us-market-sub">目标美股交易日 ${esc(target)} · ${esc(dateRule)}</div>
      </div>
      <div class="us-market-tone">${esc(toneLabel)}</div>
    </div>
    <div class="us-market-brief">${esc(summary)}</div>
    ${metricHtml}
    ${mappingHtml}
    ${guidanceHtml}
  </section>`;
}
function marketDateKey(r) {
  const t = String(r.time || '').trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(t)) return t.slice(0, 10);
  const contentDate = String(r.content || '').match(/\d{4}-\d{2}-\d{2}/);
  if (contentDate) return contentDate[0];
  const ts = Number(r.timestamp || 0);
  if (Number.isFinite(ts) && ts > 0) {
    const d = new Date(ts * 1000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
  return '未知日期';
}
function groupMarketRecordsByDay(records) {
  const groups = new Map();
  for (const r of records) {
    const key = marketDateKey(r);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  return groups;
}
function setMarketDay(index) {
  const records = filtered();
  const days = [...groupMarketRecordsByDay(records).keys()].sort().reverse();
  if (!days.length) return;
  marketDayIndex = Math.max(0, Math.min(Number(index || 0), days.length - 1));
  marketExpandedRecordKey = '';
  syncViewUrl();
  render();
  saveViewState();
}
function renderMarketDayPager(allRecords, days, day, dayRecords) {
  const total = activeCategoryTotal();
  const atLatest = marketDayIndex <= 0;
  const atEarliest = marketDayIndex >= days.length - 1;
  const loadedText = total && allRecords.length < total ? `已载入最近 ${allRecords.length} / ${total} 条` : `共 ${days.length} 个日期`;
  return `<div class="sector-cloud market-day-pager">
    <div>
      <div class="market-day-title">${esc(day)} · ${dayRecords.length} 条盘面监控</div>
      <div class="market-day-sub">${loadedText}</div>
    </div>
    <div class="market-day-actions">
      <button class="market-day-btn" onclick="setMarketDay(0)" ${atLatest ? 'disabled' : ''}>最新</button>
      <button class="market-day-btn" onclick="setMarketDay(${marketDayIndex - 1})" ${atLatest ? 'disabled' : ''}>后一天</button>
      <button class="market-day-btn" onclick="setMarketDay(${marketDayIndex + 1})" ${atEarliest ? 'disabled' : ''}>前一天</button>
      <button class="market-day-btn" onclick="setMarketDay(${days.length - 1})" ${atEarliest ? 'disabled' : ''}>最早</button>
    </div>
  </div>`;
}
function renderMarketMonitor(records) {
  const usSummaryHtml = renderUsMarketSummaryCard();
  if (!records.length) return `${usSummaryHtml}<div class="empty">暂无盘面监控消息</div>`;
  const groups = groupMarketRecordsByDay(records);
  const days = [...groups.keys()].sort().reverse();
  if (!days.length) return `${usSummaryHtml}<div class="empty">暂无盘面监控消息</div>`;
  if (marketDayIndex >= days.length) marketDayIndex = 0;
  const day = days[marketDayIndex] || days[0];
  const dayRecords = groups.get(day) || [];
  return `${usSummaryHtml}<div class="market-monitor-grid">${dayRecords.map(r => renderMarketMonitorCard(r)).join('')}</div>${renderMarketDayPager(records, days, day, dayRecords)}`;
}
function xRecordKey(r) {
  return 'x-' + shortHash(recordKey(r));
}
function cleanXLine(line) {
  return String(line || '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/^#{1,6}\s*/, '')
    .replace(/^[│┃┌└↳\-–—━\s]+/u, '')
    .replace(/\*\*/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}
function normalizeXMarker(line) {
  return cleanXLine(line).replace(/^【([^】]+)】/, '$1｜').trim();
}
function xLineRole(line) {
  const s = normalizeXMarker(line);
  if (/^(?:引用)?原[贴帖](?:\s*[|｜:：]|$)/.test(s)) return 'original';
  if (/^回复(?:\s*[|｜:：]|$)/.test(s)) return 'reply';
  return '';
}
function isXNoiseLine(line) {
  const s = cleanXLine(line);
  return !s || /^X Watchlist Dashboard Archive$/i.test(s) || /^Cron Job:/i.test(s) ||
    /^Job ID:/i.test(s) || /^Run Time:/i.test(s) || /^Mode:/i.test(s) ||
    /^Status:/i.test(s) || /^发现 X 账号新推文/.test(s) || /^X 新推文 \d+/.test(s);
}
function xParts(line) {
  return normalizeXMarker(line).split(/[｜|]/).map(x => x.trim()).filter(Boolean);
}
function xIsTimePart(part) {
  const s = String(part || '').trim();
  return /\d{4}-\d{2}-\d{2}/.test(s) || /^时间未知$/.test(s);
}
function xCleanAuthorPart(part) {
  return String(part || '')
    .replace(/^(?:引用)?原[贴帖]\s*[:：]?/, '')
    .replace(/^回复\s*[:：]?/, '')
    .replace(/^评论\/转述\s*[:：]?/, '')
    .trim();
}
function xLooksLikeRolePart(part) {
  const s = xCleanAuthorPart(part);
  return /^(?:回复|评论\/转述|转述|评论|引用|原[贴帖]|引用原[贴帖])$/.test(s) || !s;
}
function xHeaderAuthor(parts, role) {
  if (!parts.length) return '';
  if (parts.length >= 3 || role === 'reply' || role === 'original' || xLooksLikeRolePart(parts[0])) {
    const found = parts.find((p, i) => i > 0 && !xIsTimePart(p));
    return xCleanAuthorPart(found || parts[1] || parts[0]);
  }
  if (parts.length === 2 && xIsTimePart(parts[1])) {
    return xCleanAuthorPart(parts[0]);
  }
  return xCleanAuthorPart(parts.find(p => /^@/.test(p)) || parts[0]);
}
function xMetadataAuthor(r) {
  const post = xPostMeta(r);
  const direct = String(post.display_name || '').trim();
  if (direct) return direct;
  const sourceLabel = String((r && r.source_label) || '').trim();
  if (sourceLabel && sourceLabel !== '推特监控' && sourceLabel !== 'X 监控') return sourceLabel;
  const handle = String((r && r.metadata && r.metadata.handle) || post.handle || (r && r.source_id) || '').trim();
  return handle && !/^cron_/i.test(handle) ? '@' + handle.replace(/^@/, '') : '';
}
function truncateText(text, maxLen = 180) {
  const s = String(text || '').replace(/\s+/g, ' ').trim();
  return s.length > maxLen ? s.slice(0, maxLen - 1) + '…' : s;
}
function summarizeXRecord(r) {
  const raw = String(r.content || '');
  const lines = raw.split('\n').map(cleanXLine).filter(line => line && !isXNoiseLine(line));
  const replyIdx = lines.findIndex(line => xLineRole(line) === 'reply');
  const originalIdx = lines.findIndex(line => xLineRole(line) === 'original');
  const headerIdx = replyIdx >= 0 ? replyIdx : (originalIdx >= 0 ? originalIdx : lines.findIndex(line => line.includes('｜') || line.includes('|')));
  const headerLine = headerIdx >= 0 ? lines[headerIdx] : (lines[0] || '');
  const parts = xParts(headerLine);
  const role = xLineRole(headerLine);
  let author = xHeaderAuthor(parts, role);
  if (!author || xIsTimePart(author)) author = xMetadataAuthor(r);
  author = author || 'X';
  const timeFromHeader = parts.find(p => /\d{4}-\d{2}-\d{2}/.test(p));
  const bodyStart = headerIdx >= 0 ? headerIdx + 1 : 0;
  let bodyLines = lines.slice(bodyStart).filter(line => !xLineRole(line) && !isXNoiseLine(line) && !/^[-━└]+$/.test(line));
  if (!bodyLines.length) bodyLines = lines.filter(line => !xLineRole(line) && !isXNoiseLine(line));
  const preview = truncateText(bodyLines.join(' '), 190) || '暂无正文';
  const source = String(r.platform || r.chat_title || r.chat_name || r.session_id || '').trim();
  const label = role === 'reply' ? '回复' : (role === 'original' && headerLine.includes('引用') ? '引用' : '推文');
  const initialSource = author.replace(/^@/, '').trim();
  return {
    author,
    time: timeFromHeader || r.time || '',
    preview,
    source,
    label,
    threaded: originalIdx >= 0 && replyIdx >= 0,
    initial: (initialSource[0] || 'X').toUpperCase()
  };
}
function xPostMeta(r) {
  const meta = r && typeof r.metadata === 'object' && r.metadata ? r.metadata : {};
  return meta && typeof meta.post === 'object' && meta.post ? meta.post : {};
}
function cleanXMediaUrl(url) {
  let s = String(url || '').trim().replace(/\\\//g, '/');
  if (!/^https?:\/\//i.test(s)) return '';
  if (s.includes('pbs.twimg.com/media/') && !s.includes('?') && !/:(?:large|small|medium|orig)$/i.test(s) && /\.(?:jpg|jpeg|png|webp)$/i.test(s)) {
    s += ':large';
  }
  return s;
}
function isXPostMediaUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' && parsed.hostname === 'pbs.twimg.com' && /^\/(?:media|ext_tw_video_thumb|tweet_video_thumb)\//.test(parsed.pathname);
  } catch (_err) {
    return false;
  }
}
function xMediaItems(items) {
  if (!Array.isArray(items)) return [];
  const seen = new Set();
  const out = [];
  for (const item of items) {
    if (!item || typeof item !== 'object') continue;
    const url = cleanXMediaUrl(item.url || '');
    const type = String(item.type || '').trim() || 'image';
    if (!isXPostMediaUrl(url)) continue;
    const key = url;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({url, type});
  }
  return out.slice(0, 8);
}
function xMediaDisplayUrl(url) {
  return `/api/x_media?url=${encodeURIComponent(url)}`;
}
function clampXImageZoom(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 1;
  return Math.max(0.5, Math.min(3, Math.round(n * 100) / 100));
}
function xImageViewerRoot() {
  let root = document.getElementById('xImageViewerRoot');
  if (!root) {
    root = document.createElement('div');
    root.id = 'xImageViewerRoot';
    document.body.appendChild(root);
  }
  return root;
}
function renderXImageViewer() {
  const root = xImageViewerRoot();
  document.body.classList.toggle('x-image-viewer-open', !!xImageViewer.url);
  if (!xImageViewer.url) {
    root.innerHTML = '';
    return;
  }
  const zoom = clampXImageZoom(xImageViewer.zoom);
  xImageViewer.zoom = zoom;
  const src = xMediaDisplayUrl(xImageViewer.url);
  const label = xImageViewer.label || '推文图片';
  root.innerHTML = `<div class="x-image-viewer-backdrop">
    <div class="x-image-viewer-card" role="dialog" aria-modal="true" aria-label="${esc(label)}">
      <div class="x-image-viewer-head">
        <div class="x-image-viewer-title">${esc(label)} · ${Math.round(zoom * 100)}%</div>
        <div class="x-image-viewer-actions">
          <button type="button" class="x-image-viewer-btn" data-x-viewer-action="zoom-out" title="缩小" aria-label="缩小" ${zoom <= 0.5 ? 'disabled' : ''}>-</button>
          <button type="button" class="x-image-viewer-btn" data-x-viewer-action="zoom-in" title="放大" aria-label="放大" ${zoom >= 3 ? 'disabled' : ''}>+</button>
          <button type="button" class="x-image-viewer-btn" data-x-viewer-action="close" title="关闭" aria-label="关闭">x</button>
        </div>
      </div>
      <div class="x-image-viewer-stage">
        <img class="x-image-viewer-img" data-x-viewer-action="close" src="${esc(src)}" alt="${esc(label)}" style="--x-image-zoom:${zoom}" draggable="false">
      </div>
    </div>
  </div>`;
}
function openXImageViewer(url, label) {
  if (!isXPostMediaUrl(url)) return;
  xImageViewer = {url, label: label || '推文图片', zoom: 1};
  renderXImageViewer();
}
function closeXImageViewer() {
  xImageViewer = {url: '', label: '', zoom: 1};
  renderXImageViewer();
}
function zoomXImageViewer(delta) {
  if (!xImageViewer.url) return;
  xImageViewer.zoom = clampXImageZoom((xImageViewer.zoom || 1) + delta);
  renderXImageViewer();
}
function xMediaGroups(r) {
  const post = xPostMeta(r);
  return [
    {key:'reply_to_media', label:'原帖图片', items:xMediaItems(post.reply_to_media)},
    {key:'quoted_media', label:'引用图片', items:xMediaItems(post.quoted_media)},
    {key:'media', label:'推文图片', items:xMediaItems(post.media)}
  ].filter(group => group.items.length);
}
function xAllMediaItems(r) {
  return xMediaGroups(r).flatMap(group => group.items);
}
function renderXMediaStrip(r) {
  const media = xAllMediaItems(r).filter(item => item.url);
  if (!media.length) return '';
  const thumbs = media.slice(0, 3).map(item => `<span class="x-media-thumb"><img src="${esc(xMediaDisplayUrl(item.url))}" alt="推文图片" loading="lazy"></span>`).join('');
  const more = media.length > 3 ? `<span class="x-media-more">+${media.length - 3}</span>` : '';
  return `<div class="x-media-strip">${thumbs}${more}</div>`;
}
function renderXMediaGallery(groups) {
  groups = (groups || []).filter(group => group.items && group.items.length);
  if (!groups.length) return '';
  return `<div class="x-media-gallery">${groups.map(group => {
    const tiles = group.items.map(item => {
      return `<button type="button" class="x-media-tile" data-x-image-url="${esc(item.url)}" data-x-image-label="${esc(group.label)}" title="查看图片">
        <span class="x-media-frame"><img src="${esc(xMediaDisplayUrl(item.url))}" alt="${esc(group.label)}" loading="lazy"></span>
      </button>`;
    }).join('');
    return `<div class="x-media-group"><div class="x-media-label">${esc(group.label)}</div><div class="x-media-grid">${tiles}</div></div>`;
  }).join('')}</div>`;
}
function stripXCurrentPostHeader(text) {
  const lines = String(text || '').split('\n');
  if (!lines.length) return '';
  const firstLine = lines[0] || '';
  const isEmojiHeader = /^[\p{Emoji}\uFE0F\u200D]+\s*\*\*.+?\*\*/u.test(firstLine);
  if (xLineRole(firstLine) === 'reply' || firstLine.includes('｜') || firstLine.includes('|') || isEmojiHeader) {
    return lines.slice(1).join('\n').trim();
  }
  return String(text || '').trim();
}
function renderXDetail(r) {
  const thread = parseThread(r.content || '');
  const groups = xMediaGroups(r);
  const originalGroups = groups.filter(group => group.key === 'reply_to_media' || group.key === 'quoted_media');
  const mainGroups = groups.filter(group => group.key === 'media');
  if (thread.originalPost && thread.reply) {
    const replyBody = stripXCurrentPostHeader(thread.reply) || thread.reply;
    return `<div class="thread-card">
      <div class="thread-reply"><div class="thread-reply-content">${esc(replyBody)}</div>${renderXMediaGallery(mainGroups)}</div>
      <div class="thread-original"><div class="thread-original-content">${esc(thread.originalPost)}</div>${renderXMediaGallery(originalGroups)}</div>
    </div>`;
  }
  const lines = String(r.content || '').split('\n');
  const body = stripXCurrentPostHeader(lines.join('\n')) || '（无正文）';
  return `<div class="content">${esc(body)}</div>${renderXMediaGallery(groups)}`;
}
function renderXRow(r) {
  const key = xRecordKey(r);
  const s = summarizeXRecord(r);
  const open = xExpandedRecordKey === key;
  return `<article class="x-row ${open ? 'open' : ''}" data-x-key="${esc(key)}" aria-expanded="${open ? 'true' : 'false'}">
    <div class="x-avatar">${esc(s.initial)}</div>
    <div class="x-copy">
      <div class="x-line"><span class="x-author">${esc(s.author)}</span><span class="x-handle">${esc(s.label)}</span>${s.time ? `<span class="x-time">${esc(s.time)}</span>` : ''}</div>
      ${open ? '' : `<div class="x-preview">${esc(s.preview)}</div>${renderXMediaStrip(r)}`}
    </div>
    <div class="x-badges"><span class="x-chevron">›</span></div>
    ${open ? `<div class="x-detail">${renderXDetail(r)}</div>` : ''}
  </article>`;
}
function renderXMonitor(records) {
  if (!records.length) return '<div class="empty">暂无推特监控消息</div>';
  const total = activeCategoryTotal();
  const latest = records[0]?.time || '';
  const oldest = records[records.length - 1]?.time || '';
  const limit = messagePageLimit('x_monitor');
  const totalPages = Math.max(1, Math.ceil((total || records.length || 1) / limit));
  const page = Math.min(totalPages, Math.floor(xPageOffset / limit) + 1);
  return `<section class="sector-cloud x-monitor-panel">
    <div class="x-monitor-head">
      <div><div class="x-monitor-title">推特监控流</div><div class="x-monitor-sub">${latest ? '最新 ' + esc(latest) : '等待监控数据'}${oldest ? ' · 最早 ' + esc(oldest) : ''}</div></div>
      <div class="x-monitor-metrics"><span class="x-metric">第 ${page} / ${totalPages} 页</span><span class="x-metric">本页 ${records.length}</span></div>
    </div>
    <div class="x-list">${records.map(r => renderXRow(r)).join('')}</div>
  </section>${renderHistoryControls(records)}`;
}
function render() {
  if (activeCategory === 'indices') {
    $('feed').innerHTML = renderIndicesPanel();
    return;
  }
  if (activeCategory === 'b1_screen') {
    renderB1Screen();
    renderPracticeCalendarModal();
    return;
  }
  const records = filtered();
  if (activeCategory === 'us_ratings') {
    $('feed').innerHTML = renderUsRatingDay(records) + renderHistoryControls(records);
    restoreRatingDetail();
    return;
  }
  if (activeCategory === 'x_monitor') {
    $('feed').innerHTML = renderXMonitor(records);
    return;
  }
  if (activeCategory === 'market_monitor') {
    $('feed').innerHTML = renderMarketMonitor(records);
    return;
  }
  $('feed').innerHTML = records.length
    ? records.map(r => renderCard(r)).join('') + renderHistoryControls(records)
    : '<div class="empty">暂无匹配消息</div>';
}
function parseThread(content) {
  const lines = content.split('\n');
  let originalPost = null, reply = null, inOriginal = false, inReply = false;
  const originalLines = [], replyLines = [];
  for (const line of lines) {
    const trimmed = line.trim();
    const marker = normalizeXMarker(trimmed);
    if (!marker || /^[-━└]+$/.test(marker)) continue;
    if (/^(?:引用)?原[贴帖](?:\s*[|｜:：]|$)/.test(marker)) {
      inOriginal = true; inReply = false;
      if (marker.includes('|') || marker.includes('｜') || marker.includes('：') || marker.includes(':')) originalLines.push(marker);
      continue;
    }
    if (/^回复(?:\s*[|｜:：]|$)/.test(marker)) {
      inOriginal = false; inReply = true;
      if (marker.includes('|') || marker.includes('｜') || marker.includes('：') || marker.includes(':')) replyLines.push(marker);
      continue;
    }
    const bodyLine = trimmed.replace(/^[│┃]\s?/u, '').trim();
    if (inOriginal && bodyLine) originalLines.push(bodyLine);
    else if (inReply && bodyLine) replyLines.push(bodyLine);
  }
  if (originalLines.length > 0 && replyLines.length > 0) {
    originalPost = originalLines.join('\n').trim();
    reply = replyLines.join('\n').trim();
  }
  return { originalPost, reply };
}
function renderCard(r) {
  const thread = parseThread(r.content);
  if (thread.originalPost && thread.reply) {
    return `<article class="card thread-card">
        <div class="mobile-head"><span>${esc(r.time)}</span></div>
        <div class="thread-original"><div class="thread-original-content">${esc(thread.originalPost)}</div></div>
        <div class="thread-reply"><div class="thread-reply-content">${esc(thread.reply)}</div></div>
      </article>`;
  }
  const lines = r.content.split('\n');
  let header = '', body = '';
  const firstLine = lines[0] || '';
  const isEmojiHeader = /^[\p{Emoji}\uFE0F\u200D]+\s*\*\*.+?\*\*/u.test(firstLine);
  if (lines.length > 0 && (firstLine.includes('｜') || firstLine.includes('|') || isEmojiHeader)) {
    header = firstLine; body = lines.slice(1).join('\n').trim();
  } else { body = r.content; }
  return `<article class="card${header ? ' has-header' : ''}">
      <div class="mobile-head"><span>${esc(r.time)}</span></div>
      ${header ? `<div class="post-header">${esc(header)}</div>` : ''}
      <div class="content">${esc(body)}</div>
    </article>`;
}
document.addEventListener('click', event => {
  const logAction = event.target.closest('[data-practice-log-action]');
  if (logAction) {
    event.preventDefault();
    event.stopPropagation();
    if (logAction.dataset.practiceLogAction === 'close') {
      practiceLogDetailKey = '';
      if (activeCategory === 'b1_screen') render();
    }
    return;
  }
  if (practiceLogDetailKey && event.target.classList && event.target.classList.contains('practice-log-detail-backdrop')) {
    practiceLogDetailKey = '';
    if (activeCategory === 'b1_screen') render();
    return;
  }
  const logTrigger = event.target.closest('[data-practice-log-key]');
  if (logTrigger) {
    event.preventDefault();
    event.stopPropagation();
    practiceLogDetailKey = logTrigger.dataset.practiceLogKey || '';
    if (activeCategory === 'b1_screen') render();
    return;
  }
  const ruleAction = event.target.closest('[data-practice-rule-action]');
  if (ruleAction) {
    event.preventDefault();
    event.stopPropagation();
    practiceRuleNoteOpen = ruleAction.dataset.practiceRuleAction === 'open';
    if (activeCategory === 'b1_screen') render();
    return;
  }
  if (practiceRuleNoteOpen && event.target.classList && event.target.classList.contains('practice-rule-backdrop')) {
    practiceRuleNoteOpen = false;
    if (activeCategory === 'b1_screen') render();
    return;
  }
  const calendarAction = event.target.closest('[data-practice-calendar-action]');
  if (calendarAction) {
    event.preventDefault();
    event.stopPropagation();
    const action = calendarAction.dataset.practiceCalendarAction;
    if (action === 'close') closePracticeCalendar();
    else if (action === 'prev') shiftPracticeCalendarMonth(-1);
    else if (action === 'next') shiftPracticeCalendarMonth(1);
    else if (action === 'clear-day') {
      practiceCalendarSelectedDate = '';
      renderPracticeCalendarModal();
    }
    return;
  }
  const calendarDate = event.target.closest('[data-practice-calendar-date]');
  if (calendarDate) {
    event.preventDefault();
    event.stopPropagation();
    const nextDate = calendarDate.dataset.practiceCalendarDate || '';
    practiceCalendarSelectedDate = practiceCalendarSelectedDate === nextDate ? '' : nextDate;
    renderPracticeCalendarModal();
    return;
  }
  if (practiceCalendarOpen && !event.target.closest('.practice-calendar-card') && !event.target.closest('[data-practice-calendar-curve]') && !event.target.closest('.practice-calendar-open-btn')) {
    closePracticeCalendar();
    return;
  }
  const viewerAction = event.target.closest('[data-x-viewer-action]');
  if (viewerAction) {
    event.preventDefault();
    event.stopPropagation();
    const action = viewerAction.dataset.xViewerAction;
    if (action === 'close') closeXImageViewer();
    else if (action === 'zoom-in') zoomXImageViewer(0.25);
    else if (action === 'zoom-out') zoomXImageViewer(-0.25);
    return;
  }
  if (event.target.classList && event.target.classList.contains('x-image-viewer-backdrop')) {
    closeXImageViewer();
    return;
  }
  const imageTrigger = event.target.closest('[data-x-image-url]');
  if (imageTrigger) {
    event.preventDefault();
    event.stopPropagation();
    openXImageViewer(imageTrigger.dataset.xImageUrl || '', imageTrigger.dataset.xImageLabel || '推文图片');
    return;
  }
  if (activeCategory === 'x_monitor') {
    if (event.target.closest('.x-detail')) return;
    const row = event.target.closest('.x-row[data-x-key]');
    if (!row) return;
    xExpandedRecordKey = xExpandedRecordKey === row.dataset.xKey ? '' : row.dataset.xKey;
    render();
    return;
  }
  if (activeCategory === 'market_monitor') {
    if (event.target.closest('.market-card-detail')) return;
    const card = event.target.closest('.market-monitor-card[data-market-key]');
    if (!card) return;
    marketExpandedRecordKey = marketExpandedRecordKey === card.dataset.marketKey ? '' : card.dataset.marketKey;
    render();
  }
});
document.addEventListener('keydown', event => {
  if (practiceLogDetailKey && event.key === 'Escape') {
    event.preventDefault();
    practiceLogDetailKey = '';
    if (activeCategory === 'b1_screen') render();
    return;
  }
  if (practiceRuleNoteOpen && event.key === 'Escape') {
    event.preventDefault();
    practiceRuleNoteOpen = false;
    if (activeCategory === 'b1_screen') render();
    return;
  }
  if (practiceCalendarOpen && event.key === 'Escape') {
    event.preventDefault();
    closePracticeCalendar();
    return;
  }
  if (!xImageViewer.url) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    closeXImageViewer();
  } else if (event.key === '+' || event.key === '=') {
    event.preventDefault();
    zoomXImageViewer(0.25);
  } else if (event.key === '-') {
    event.preventDefault();
    zoomXImageViewer(-0.25);
  }
});
restoreViewState();
renderTabs();
if (hasWarmData(activeCategory)) render();
load().catch(err => { if (!err || err.name !== 'AbortError') console.error(err); });
setInterval(() => autoRefresh().catch(err => { if (!err || err.name !== 'AbortError') console.error(err); }), AUTO_REFRESH_TICK_MS);
</script>
</body>
</html>"""



def fmt_admin_time(ts: float | None) -> str:
    if not ts:
        return '--'
    return datetime.fromtimestamp(float(ts)).strftime('%m-%d %H:%M')


def render_admin_notice(params: dict[str, list[str]]) -> str:
    if params.get("config_saved", [""])[0] in {"env", "env_restart"}:
        return "<div class='okmsg'>业务配置已保存；设置页配置会在后续任务或请求中直接使用。</div>"
    if params.get("config_saved", [""])[0] == "env_hot":
        return "<div class='okmsg'>业务配置已保存；设置页配置会在后续任务或请求中直接使用。</div>"
    if params.get("config_saved", [""])[0] == "env_noop":
        return "<div class='okmsg'>业务配置未发生变化，无需重新应用。</div>"
    if params.get("config_saved", [""])[0] == "yaml":
        return "<div class='okmsg'>模型配置已保存；依赖 config.yaml 的任务会在下次读取时使用新配置。</div>"
    error = params.get("config_error", [""])[0]
    if error:
        return f"<div class='errmsg'>{html.escape(error)}</div>"
    return ""


def render_env_input(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "")
    kind = str(item.get("kind") or "text")
    value = str(item.get("file_value") or "")
    escaped_name = html.escape(name)
    if item.get("secret"):
        placeholder = html.escape(str(item.get("file_state") or display_secret(None)))
        return (
            f"<input type='password' name='env__{escaped_name}' "
            f"placeholder='{placeholder}' autocomplete='new-password'>"
        )
    if kind == "bool":
        current = normalize_env_update(name, value, "bool") if value != "" else ""
        toggle_attr = ""
        default_option = f"<option value='' {'selected' if current == '' else ''}>默认</option>"
        if name == "DASHBOARD_US_FEATURES_ENABLED" or item.get("bool_no_default"):
            toggle_attr = " data-feature-toggle='us'"
            if name != "DASHBOARD_US_FEATURES_ENABLED":
                toggle_attr = ""
            default_option = ""
        return (
            f"<select name='env__{escaped_name}'{toggle_attr}>"
            f"{default_option}"
            f"<option value='1' {'selected' if current == '1' else ''}>启用</option>"
            f"<option value='0' {'selected' if current == '0' else ''}>停用</option>"
            f"</select>"
        )
    if kind == "cron_time":
        day_label = html.escape(str(item.get("day_label") or ""))
        return (
            f"<input type='time' name='env__{escaped_name}' value='{html.escape(value)}'>"
            f"<div class='config-meta'>北京时间{(' · ' + day_label) if day_label else ''}</div>"
        )
    if kind == "time":
        return (
            f"<input type='time' name='env__{escaped_name}' value='{html.escape(value)}'>"
            "<div class='config-meta'>北京时间</div>"
        )
    if kind == "time_list":
        values = list(item.get("time_values") or split_hhmm_values(value))
        field_name = f"env__{escaped_name}"
        inputs = []
        for slot_value in values:
            inputs.append(
                "<div class='time-list-item'>"
                f"<input type='time' name='{field_name}' value='{html.escape(slot_value)}'>"
                "<button type='button' class='time-list-remove' data-time-list-remove "
                "aria-label='删除时间点' title='删除时间点'>x</button>"
                "</div>"
            )
        return (
            f"<div class='time-list-control' data-time-list data-field-name='{field_name}' data-input-type='time'>"
            f"<input type='hidden' name='{field_name}' value=''>"
            "<div class='time-list-grid' data-time-list-items>"
            + "".join(inputs)
            + "</div><button type='button' class='time-list-add' data-time-list-add "
            "aria-label='添加时间点' title='添加时间点'>+</button></div>"
            "<div class='config-meta'>北京时间</div>"
        )
    if kind == "handle_list":
        values = list(item.get("handle_values") or split_handle_values(value))
        field_name = f"env__{escaped_name}"
        inputs = []
        for handle in values:
            inputs.append(
                "<div class='time-list-item'>"
                f"<input type='text' name='{field_name}' value='{html.escape(handle)}' placeholder='handle' autocapitalize='off' spellcheck='false'>"
                "<button type='button' class='time-list-remove' data-time-list-remove "
                "aria-label='删除作者' title='删除作者'>x</button>"
                "</div>"
            )
        return (
            f"<div class='time-list-control' data-time-list data-field-name='{field_name}' data-input-type='text' data-placeholder='handle'>"
            f"<input type='hidden' name='{field_name}' value=''>"
            "<div class='time-list-grid' data-time-list-items>"
            + "".join(inputs)
            + "</div><button type='button' class='time-list-add' data-time-list-add "
            "aria-label='添加作者' title='添加作者'>+</button></div>"
            "<div class='config-meta'>X/Twitter handle</div>"
        )
    if kind == "strategy_source":
        current = normalize_strategy_source_update(value)
        field_name = f"env__{escaped_name}"
        option_html = []
        for option in item.get("strategy_source_options") or STRATEGY_SOURCE_OPTIONS:
            source_id = str(option.get("id") or "")
            if not source_id:
                continue
            checked = " checked" if source_id == current else ""
            label = html.escape(str(option.get("label") or source_id))
            desc = html.escape(str(option.get("desc") or ""))
            color = html.escape(str(option.get("color") or "#94a3b8"))
            option_html.append(
                f"<label class='strategy-option' style='--strategy-color:{color}'>"
                f"<input type='radio' name='{field_name}' value='{html.escape(source_id)}'{checked} data-strategy-source-toggle>"
                "<span class='strategy-option-main'>"
                f"<span class='strategy-option-title'><span class='strategy-option-dot'></span>{label}</span>"
                f"<span class='strategy-option-desc'>{desc}</span>"
                "</span>"
                "</label>"
            )
        return (
            "<div class='strategy-multi-control'>"
            + "".join(option_html)
            + "</div><div class='config-meta'>内置策略和预设文字二选一激活</div>"
        )
    if kind == "preset_strategy_text":
        max_chars = int(item.get("preset_strategy_max_chars") or PRESET_STRATEGY_TEXT_MAX_CHARS)
        return (
            f"<textarea class='preset-strategy-textarea' name='env__{escaped_name}' "
            f"maxlength='{max_chars}' spellcheck='false' placeholder='例如：只做主线强趋势回踩，买入后跌破5日线离场；盈利超过8%后分批止盈。'>"
            f"{html.escape(value)}</textarea>"
            "<div class='config-meta'>激活后由买卖决策模型优化为选股、买入、卖出和仓位规则</div>"
        )
    if kind == "trade_discipline_text":
        max_chars = int(item.get("trade_discipline_max_chars") or TRADE_DISCIPLINE_TEXT_MAX_CHARS)
        return (
            f"<textarea class='trade-discipline-textarea' name='env__{escaped_name}' "
            f"maxlength='{max_chars}' spellcheck='false' placeholder='留空时使用内置交易纪律'>"
            f"{html.escape(value)}</textarea>"
            "<div class='config-meta'>直接写入买卖决策模型 prompt 的“必须遵守”段；留空时使用内置默认纪律</div>"
        )
    if kind in {"strategy_multi", "strategy_single"}:
        selected = set(item.get("strategy_values") or split_strategy_values(value))
        field_name = f"env__{escaped_name}"
        options = item.get("strategy_options") or strategy_settings_options(family="persona")
        option_html = []
        for option in options:
            strategy_id = str(option.get("id") or "")
            if not strategy_id:
                continue
            checked = " checked" if strategy_id in selected else ""
            input_type = "radio" if kind == "strategy_single" else "checkbox"
            label = html.escape(str(option.get("label") or strategy_id))
            desc = html.escape(str(option.get("desc") or ""))
            color = html.escape(str(option.get("color") or "#94a3b8"))
            option_html.append(
                f"<label class='strategy-option' style='--strategy-color:{color}'>"
                f"<input type='{input_type}' name='{field_name}' value='{html.escape(strategy_id)}'{checked}>"
                "<span class='strategy-option-main'>"
                f"<span class='strategy-option-title'><span class='strategy-option-dot'></span>{label}</span>"
                f"<span class='strategy-option-desc'>{desc}</span>"
                "</span>"
                "</label>"
            )
        return (
            f"<div class='strategy-multi-control'>"
            f"<input type='hidden' name='{field_name}' value=''>"
            + "".join(option_html)
            + "</div><div class='config-meta'>每次只启用一个内置策略</div>"
        )
    if kind == "context_length":
        return (
            f"<input type='text' name='env__{escaped_name}' value='{html.escape(value)}' "
            "placeholder='默认 128000；例如 128K、1M 或 1000000' inputmode='numeric'>"
            "<div class='config-meta'>默认 128000 tokens；填写后保存为数字 tokens</div>"
        )
    if kind == "max_tokens":
        return (
            f"<input type='text' name='env__{escaped_name}' value='{html.escape(value)}' "
            "placeholder='默认 4096；例如 2048 或 8192' inputmode='numeric'>"
            "<div class='config-meta'>默认 4096 tokens；填写后覆盖请求 max_tokens</div>"
        )
    input_type = "number" if kind == "int" else "text"
    return f"<input type='{input_type}' name='env__{escaped_name}' value='{html.escape(value)}'>"


def admin_group_anchor(group_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", group_name.lower()).strip("-")
    if slug:
        return f"settings-{slug}"
    digest = hashlib.sha1(group_name.encode("utf-8")).hexdigest()[:8]
    return f"settings-{digest}"


def render_nav_label(label: str) -> str:
    if len(label) < 2:
        return html.escape(label)
    return html.escape(label[0]) + f"<span>{html.escape(label[1:])}</span>"


def render_env_config_table(payload: dict[str, Any]) -> str:
    us_feature_enabled = False
    groups: list[dict[str, Any]] = []
    current_group: dict[str, Any] | None = None
    for item in payload["items"]:
        if item.get("name") == "DASHBOARD_US_FEATURES_ENABLED":
            us_feature_enabled = str(item.get("effective") or item.get("file_value") or "").strip().lower() in {"1", "true", "yes", "on"}
        group = str(item.get("group") or "其他")
        if current_group is None or current_group["name"] != group:
            current_group = {"name": group, "items": [], "anchor": admin_group_anchor(group)}
            groups.append(current_group)
        current_group["items"].append(item)

    sections: list[str] = []
    nav_items: list[str] = []
    for group in groups:
        group_name = str(group["name"])
        group_anchor = str(group["anchor"])
        note = ADMIN_GROUP_NOTES.get(group_name, "")
        note_html = f"<p class='settings-group-note'>{html.escape(note)}</p>" if note else ""
        rows: list[str] = []
        for item in group["items"]:
            name = str(item.get("name") or "")
            label = str(item.get("label") or name)
            current = str(item.get("effective") or "")
            default = str(item.get("default") or "")
            current_html = html.escape(current) if current else "<span class='config-empty'>未设置</span>"
            default_html = html.escape(default) if default else "<span class='config-empty'>未设置</span>"
            row_attrs = "class='setting-row'"
            if name in US_FEATURE_GATED_NAMES:
                row_attrs += " data-feature-gated='us'"
                if not us_feature_enabled:
                    row_attrs += " hidden aria-hidden='true'"
                else:
                    row_attrs += " aria-hidden='false'"
            if name == PERSONA_STRATEGY_ENV:
                row_attrs += f" data-strategy-source-gated='{STRATEGY_SOURCE_BUILTIN}'"
            elif name == PRESET_STRATEGY_TEXT_ENV:
                row_attrs += " data-strategy-source-gated='preset_text'"
            rows.append(
                f"<div {row_attrs}>"
                f"<div class='setting-copy'><div class='config-label'>{html.escape(label)}</div></div>"
                f"<div class='setting-editor'>{render_env_input(item)}</div>"
                "<div class='setting-state'>"
                f"<div class='setting-state-item'><div class='setting-state-label'>当前</div><div class='config-meta'>{current_html}</div></div>"
                f"<div class='setting-state-item'><div class='setting-state-label'>默认</div><div class='config-meta'>{default_html}</div></div>"
                "</div>"
                "</div>"
            )
        gated_attrs = ""
        if group_name in US_FEATURE_GATED_GROUPS:
            gated_attrs = " data-feature-gated='us'"
            if not us_feature_enabled:
                gated_attrs += " hidden aria-hidden='true'"
            else:
                gated_attrs += " aria-hidden='false'"
        nav_items.append(
            f"<a class='settings-nav-link' href='#{group_anchor}'{gated_attrs}>"
            f"<span class='settings-nav-index'>{len(nav_items) + 1:02d}</span>"
            f"<span class='settings-nav-label'>{render_nav_label(group_name)}</span>"
            f"<span class='settings-nav-count'>{len(group['items'])}</span>"
            "</a>"
        )
        sections.append(
            "<section class='settings-group'"
            f" id='{group_anchor}'"
            + gated_attrs
            + ">"
            "<div class='settings-group-head'>"
            f"<div><h2>{html.escape(group_name)}</h2>{note_html}</div>"
            f"<span class='settings-count'>{len(group['items'])} 项</span>"
            "</div>"
            "<div class='settings-list'>"
            + "".join(rows)
            + "</div>"
            "</section>"
        )

    return (
        "<form id='env-config-form' class='settings-form' method='post' action='/admin/config/env'>"
        "<div id='config' class='settings-overview'>"
        "<div class='settings-overview-copy'>"
        "<h2>业务配置</h2>"
        "<p class='muted'>按分组维护运行参数、模型接入、任务时间和策略开关；保存后会写入本地配置并同步可热应用项。</p>"
        "</div>"
        "<div class='settings-overview-stats'>"
        f"<div class='settings-stat'><span class='settings-stat-value'>{len(groups)}</span><span class='settings-stat-label'>分组</span></div>"
        f"<div class='settings-stat'><span class='settings-stat-value'>{len(payload['items'])}</span><span class='settings-stat-label'>配置项</span></div>"
        "</div>"
        "</div>"
        "<div class='settings-shell'>"
        "<aside class='settings-sidebar' aria-label='设置分组'>"
        "<div class='settings-nav-title'>分组</div>"
        "<nav class='settings-nav'>"
        + "".join(nav_items)
        + "</nav>"
        "</aside>"
        "<div class='settings-content'>"
        + "".join(sections)
        + "</div>"
        "</div>"
        + "<div class='settings-actions'>"
        "<div class='settings-save-status' data-env-save-status role='status' aria-live='polite'></div>"
        "<button class='save-button' data-env-save-button type='submit'>保存业务配置</button>"
        "</div>"
        "</form>"
    )


def render_yaml_config(payload: dict[str, Any]) -> str:
    error = payload.get("yaml_error")
    if error:
        return f"<div class='errmsg'>{html.escape(str(error))}</div>"
    return (
        "<form method='post' action='/admin/config/yaml'>"
        f"<p class='muted'>文件：<code>{html.escape(payload['config_path'])}</code>；密钥以 <code>{SECRET_PLACEHOLDER}</code> 占位，保存时会保留原值。</p>"
        f"<textarea name='config_yaml' spellcheck='false'>{html.escape(str(payload.get('yaml') or ''))}</textarea>"
        "<button style='margin-top:12px'>保存模型配置</button>"
        "</form>"
    )


def render_admin_page(params: dict[str, list[str]] | None = None) -> bytes:
    params = params or {}
    config_payload = build_admin_config_payload()
    page = ADMIN_HTML
    page = page.replace('__NOTICE__', render_admin_notice(params))
    page = page.replace('__ENV_CONFIG__', render_env_config_table(config_payload))
    return page.encode('utf-8')

class Handler(BaseHTTPRequestHandler):
    server_version = "NiuOneDashboard"
    sys_version = ""

    def remote_ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    def request_from_trusted_proxy(self) -> bool:
        return is_trusted_proxy_ip(self.remote_ip())

    def client_ip(self) -> str:
        if self.request_from_trusted_proxy():
            forwarded = first_forwarded_ip(self.headers.get("CF-Connecting-IP"), self.headers.get("X-Forwarded-For"))
            if forwarded:
                return forwarded
        return self.remote_ip()

    def is_secure_request(self) -> bool:
        if not self.request_from_trusted_proxy():
            return False
        if is_truthy_header(self.headers.get("X-Forwarded-Proto")):
            return True
        cf_visitor = self.headers.get("CF-Visitor") or ""
        return '"scheme":"https"' in cf_visitor.replace(" ", "").lower()

    def request_visitor_id(self) -> tuple[str, bool]:
        visitor_id = parse_request_cookies(self.headers.get("Cookie")).get(VISITOR_COOKIE_NAME, "").strip()
        if re.fullmatch(r"nvst_[A-Za-z0-9_-]{20,80}", visitor_id or ""):
            return visitor_id, False
        return "nvst_" + secrets.token_urlsafe(24), True

    def current_user(self) -> dict[str, Any]:
        return {"role": "admin", "nickname": "local"}

    def send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; object-src 'none'")
        if self.is_secure_request():
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    def end_headers(self) -> None:
        self.send_security_headers()
        try:
            super().end_headers()
        except (BrokenPipeError, ConnectionResetError):
            return

    def write_response(self, payload: bytes) -> None:
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            return

    def accepts_gzip(self) -> bool:
        accepted = str(self.headers.get("Accept-Encoding") or "")
        return any(part.strip().split(";", 1)[0].lower() == "gzip" for part in accepted.split(","))

    def maybe_gzip_payload(self, payload: bytes, content_type: str) -> tuple[bytes, bool]:
        if len(payload) < GZIP_MIN_BYTES or not self.accepts_gzip():
            return payload, False
        normalized_type = content_type.split(";", 1)[0].strip().lower()
        if normalized_type not in GZIP_CONTENT_TYPE_PREFIXES:
            return payload, False
        compressed = gzip.compress(payload, compresslevel=5)
        if len(compressed) >= len(payload):
            return payload, False
        return compressed, True

    def send_compression_headers(self, gzipped: bool, payload_len: int) -> None:
        if gzipped:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(payload_len))

    def send_json_error(self, status: int, error: str, *, allow: str | None = None) -> None:
        self.send_response(status)
        if allow:
            self.send_header("Allow", allow)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_response(json.dumps({"error": error}, ensure_ascii=False).encode("utf-8"))

    def send_method_not_allowed(self, allow: str = "POST") -> None:
        self.send_json_error(405, "method_not_allowed", allow=allow)

    def require_api_action_request(self) -> bool:
        header_value = str(self.headers.get(ACTION_HEADER_NAME) or "").strip().lower()
        if header_value not in ACTION_HEADER_VALUES:
            self.send_json_error(403, "action_header_required")
            return False
        return True

    def send_rate_limited(self, retry_after: int) -> None:
        parsed = urlparse(self.path)
        self.send_response(429)
        self.send_header("Retry-After", str(retry_after))
        self.send_header("Cache-Control", "no-store")
        if parsed.path.startswith("/api/"):
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.write_response(json.dumps({"error": "rate_limited", "retry_after": retry_after}, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.write_response(b"rate limited")

    def enforce_rate_limit(self, scope: str, key: str, limit: int) -> bool:
        ok, retry_after = check_rate_limit(scope, key, limit)
        if not ok:
            self.send_rate_limited(retry_after)
            return False
        return True

    def visitor_cookie_flags(self) -> str:
        secure = "; Secure" if self.is_secure_request() else ""
        return f"Path=/; Max-Age=31536000; SameSite=Lax{secure}"

    def send_html(self, payload: bytes, status: int = 200) -> None:
        content_type = "text/html; charset=utf-8"
        body, gzipped = self.maybe_gzip_payload(payload, content_type)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_compression_headers(gzipped, len(body))
        self.end_headers()
        self.write_response(body)

    def redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def require_admin(self) -> dict[str, Any]:
        return self.current_user()

    def read_form(self) -> dict[str, str]:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        if length > MAX_POST_BODY_BYTES:
            raise RequestTooLarge(f"request body too large: {length}")
        raw = self.rfile.read(length).decode("utf-8", "ignore")
        parsed = parse_qs(raw, keep_blank_values=True)
        result: dict[str, str] = {}
        for key, values in parsed.items():
            env_name = key[len("env__"):] if key.startswith("env__") else ""
            schema = ENV_CONFIG_BY_NAME.get(env_name, {})
            if schema.get("kind") in {"time_list", "handle_list", "strategy_multi", "strategy_single"}:
                result[key] = ",".join(v.strip() for v in values if v.strip())
            else:
                result[key] = values[-1] if values else ""
        return result

    def send_payload(self, payload: bytes, *, content_type: str = "application/json; charset=utf-8",
                     edge_ttl: int = 10, browser_ttl: int = 3, cache_hit: bool | None = None) -> None:
        body, gzipped = self.maybe_gzip_payload(payload, content_type)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if edge_ttl > 0:
            if EDGE_CACHE_ENABLED:
                self.send_header("Cache-Control", f"public, max-age={browser_ttl}, s-maxage={edge_ttl}, stale-while-revalidate={edge_ttl * 2}")
                self.send_header("CDN-Cache-Control", f"public, max-age={edge_ttl}, stale-while-revalidate={edge_ttl * 2}")
            else:
                self.send_header("Cache-Control", f"private, max-age={browser_ttl}, stale-while-revalidate={max(browser_ttl, edge_ttl)}")
                self.send_header("CDN-Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "no-store")
        if cache_hit is not None:
            self.send_header("X-Dashboard-Cache", "HIT" if cache_hit else "MISS")
        self.send_compression_headers(gzipped, len(body))
        self.end_headers()
        self.write_response(body)

    def send_json_cached(self, key: str, ttl: int, producer, *, edge_ttl: int | None = None, browser_ttl: int = 3) -> None:
        payload, hit = cache_get_json(key, ttl, producer)
        self.send_payload(payload, edge_ttl=edge_ttl if edge_ttl is not None else ttl, browser_ttl=min(browser_ttl, ttl), cache_hit=hit)

    def send_json_uncached(self, result: dict[str, Any], *, no_store: bool = True) -> None:
        payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_payload(payload, edge_ttl=0 if no_store else 1)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if not self.enforce_rate_limit("ip", self.client_ip(), RATE_LIMIT_ANON):
            return
        if parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "private, max-age=30, stale-while-revalidate=300")
            self.send_header("CDN-Cache-Control", "no-store")
            self.end_headers()
            return
        if parsed.path.startswith("/api/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if not self.enforce_rate_limit("ip", self.client_ip(), RATE_LIMIT_ANON):
            return
        if parsed.path == "/admin":
            self.send_html(render_admin_page(parse_qs(parsed.query)))
            return
        if parsed.path == "/api/admin/config":
            self.send_json_uncached(build_admin_config_payload())
            return
        if parsed.path == "/":
            visitor_id, new_visitor = self.request_visitor_id()
            visit_stats = increment_visit_count(visitor_id)
            page = INDEX_HTML.replace("__VISIT_COUNT__", f"{visit_stats['visits']:,}")
            page = page.replace("__UNIQUE_VISIT_COUNT__", f"{visit_stats['unique']:,}")
            page = page.replace("__US_FEATURES_ENABLED__", "true" if us_features_enabled() else "false")
            content_type = "text/html; charset=utf-8"
            body, gzipped = self.maybe_gzip_payload(page.encode("utf-8"), content_type)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("CDN-Cache-Control", "no-store")
            if new_visitor:
                self.send_header("Set-Cookie", f"{VISITOR_COOKIE_NAME}={visitor_id}; {self.visitor_cookie_flags()}")
            self.send_compression_headers(gzipped, len(body))
            self.end_headers()
            self.write_response(body)
            return
        if parsed.path.startswith("/api/"):
            if not self.enforce_rate_limit("api", self.client_ip(), RATE_LIMIT_API):
                return
        if parsed.path == "/api/x_media":
            params = parse_qs(parsed.query)
            media_url = params.get("url", [""])[0].strip()
            try:
                body, content_type = fetch_x_media(media_url)
            except Exception:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.write_response(b"media unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=604800, immutable")
            self.end_headers()
            self.write_response(body)
            return
        if parsed.path == "/api/messages":
            params = parse_qs(parsed.query)
            limit = clamp_limit(params.get("limit", [""])[0])
            offset = clamp_offset(params.get("offset", [""])[0])
            category = params.get("category", [""])[0].strip() or None
            cache_key = f"messages:v4:{category or 'all'}:{limit}:{offset}"
            def produce_messages():
                return merge_records_from_db(limit=limit, category=category, offset=offset)
            self.send_json_cached(cache_key, API_TTLS["messages"], produce_messages, edge_ttl=API_TTLS["messages"], browser_ttl=5)
            return
        if parsed.path == "/api/b1_screen":
            params = parse_qs(parsed.query)
            if params.get("force", ["0"])[0].lower() in {"1", "true", "yes"}:
                self.send_method_not_allowed("POST")
            else:
                self.send_json_cached("b1_screen", API_TTLS["b1_screen"], load_b1_cache, edge_ttl=API_TTLS["b1_screen"], browser_ttl=10)
            return
        if parsed.path == "/api/b1_screen/trigger":
            self.send_method_not_allowed("POST")
            return
        if parsed.path == "/api/niuniu_practice":
            params = parse_qs(parsed.query)
            fast = params.get("fast", ["0"])[0].lower() in {"1", "true", "yes"}
            if fast:
                self.send_json_cached("niuniu_practice_fast", API_TTLS["niuniu_practice"], get_practice_payload_fast, edge_ttl=API_TTLS["niuniu_practice"], browser_ttl=10)
            else:
                self.send_json_cached("niuniu_practice", API_TTLS["niuniu_practice"], get_practice_payload, edge_ttl=API_TTLS["niuniu_practice"], browser_ttl=10)
            return
        if parsed.path == "/api/niuniu_practice/resume":
            self.send_method_not_allowed("POST")
            return
        if parsed.path == "/api/self_optimize/status":
            from self_optimizer import get_status
            payload = json.dumps(get_status(), ensure_ascii=False).encode("utf-8")
            self.send_payload(payload, edge_ttl=0)
            return
        if parsed.path == "/api/self_optimize/apply":
            self.send_method_not_allowed("POST")
            return
        if parsed.path == "/api/daily_evolution":
            report_file = CRON_OUTPUT_DIR / "daily_evolution_report.json"
            if report_file.exists():
                payload = report_file.read_bytes()
            else:
                payload = json.dumps({"error":"尚无进化报告，等待首次盘后运行"}, ensure_ascii=False).encode("utf-8")
            self.send_payload(payload, edge_ttl=10, browser_ttl=5)
            return
        if parsed.path == "/api/practice_benchmarks":
            self.send_json_cached("practice_benchmarks", API_TTLS["practice_benchmarks"], get_practice_benchmarks, edge_ttl=API_TTLS["practice_benchmarks"], browser_ttl=10)
            return
        if parsed.path == "/api/indices":
            self.send_json_cached("indices", API_TTLS["indices"], produce_indices_data, edge_ttl=API_TTLS["indices"], browser_ttl=15)
            return
        if parsed.path == "/api/sectors":
            self.send_json_cached("sectors", API_TTLS["sectors"], lambda: run_dashboard_helper("sectors_dashboard_api.py", {"sectors": [], "items": [], "gain_top": [], "loss_top": []}, timeout=120), edge_ttl=API_TTLS["sectors"], browser_ttl=15)
            return
        if parsed.path == "/api/hot_stocks":
            params = parse_qs(parsed.query)
            sort_by = (params.get("sort_by", ["amount"])[0] or "amount").strip().lower()
            if sort_by not in {"amount", "amount_top", "turnover", "turnover_top", "volume", "volume_top", "gain", "hot"}:
                sort_by = "amount"

            def produce_hot_stocks():
                data = run_dashboard_helper(
                    "hot_stocks_dashboard_api.py",
                    {"items": [], "amount_top": [], "turnover_top": [], "volume_top": [], "gain_top": []},
                    timeout=120,
                )
                return apply_hot_stocks_sort(data, sort_by)

            self.send_json_cached(f"hot_stocks:{sort_by}", API_TTLS["hot_stocks"], produce_hot_stocks, edge_ttl=API_TTLS["hot_stocks"], browser_ttl=15)
            return
        if parsed.path == "/api/us_quotes":
            params = parse_qs(parsed.query)
            symbols = sanitize_symbols(params.get("symbols", [""])[0])
            cache_key = "us_quotes:" + ",".join(symbols)
            self.send_json_cached(cache_key, API_TTLS["us_quotes"], lambda: fetch_us_quotes(symbols), edge_ttl=API_TTLS["us_quotes"], browser_ttl=10)
            return
        if parsed.path == "/api/us_market_summary":
            self.send_json_cached("us_market_summary", API_TTLS["us_market_summary"], produce_us_market_summary_data, edge_ttl=API_TTLS["us_market_summary"], browser_ttl=30)
            return
        if parsed.path == "/api/us_sectors":
            self.send_json_cached("us_sectors", API_TTLS["us_sectors"], produce_us_sector_data, edge_ttl=API_TTLS["us_sectors"], browser_ttl=30)
            return
        if parsed.path == "/api/money_flow":
            self.send_json_cached("money_flow", API_TTLS["money_flow"], lambda: run_dashboard_helper("money_flow_dashboard_api.py", {"inflow": [], "outflow": []}, timeout=120), edge_ttl=API_TTLS["money_flow"], browser_ttl=15)
            return
        if parsed.path == "/api/market_flow":
            self.send_json_cached("market_flow", API_TTLS["market_flow"], lambda: run_dashboard_helper("market_flow_dashboard_api.py", {"total_inflow_yi": None}, timeout=30), edge_ttl=API_TTLS["market_flow"], browser_ttl=10)
            return
        self.send_response(404)
        self.end_headers()
        self.write_response(b"not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self.enforce_rate_limit("ip", self.client_ip(), RATE_LIMIT_ANON):
            return
        if parsed.path in {"/api/b1_screen", "/api/b1_screen/trigger"}:
            params = parse_qs(parsed.query)
            if parsed.path == "/api/b1_screen" and params.get("force", ["0"])[0].lower() not in {"1", "true", "yes"}:
                self.send_response(404)
                self.end_headers()
                self.write_response(b"not found")
                return
            if not self.require_admin():
                return
            if not self.require_api_action_request():
                return
            if not self.enforce_rate_limit("admin", self.client_ip(), RATE_LIMIT_ADMIN):
                return
            cache_data = trigger_b1_scan(force=True)
            API_RESPONSE_CACHE.pop("b1_screen", None)
            self.send_json_uncached(cache_data)
            return
        if parsed.path == "/api/niuniu_practice/resume":
            if not self.require_admin():
                return
            if not self.require_api_action_request():
                return
            if not self.enforce_rate_limit("admin", self.client_ip(), RATE_LIMIT_ADMIN):
                return
            result = get_trader_module().resume_trading()
            API_RESPONSE_CACHE.pop("niuniu_practice", None)
            self.send_json_uncached(result)
            return
        if parsed.path == "/api/self_optimize/apply":
            if not self.require_admin():
                return
            if not self.require_api_action_request():
                return
            if not self.enforce_rate_limit("admin", self.client_ip(), RATE_LIMIT_ADMIN):
                return
            from self_optimizer import apply_optimization
            payload = json.dumps(apply_optimization(), ensure_ascii=False).encode("utf-8")
            self.send_payload(payload, edge_ttl=0)
            return
        if parsed.path in {"/admin/config/env", "/api/admin/config/env"}:
            if not self.require_admin():
                return
            if not self.enforce_rate_limit("admin", self.client_ip(), RATE_LIMIT_ADMIN):
                return
            try:
                form = self.read_form()
                visible_names = set(admin_visible_env_names())
                updates = {
                    key[len("env__"):]: value
                    for key, value in form.items()
                    if key.startswith("env__") and key[len("env__"):] in visible_names
                }
                updates = normalize_business_updates(updates)
                validate_business_updates(updates)
                result = write_env_file_values(updates)
                result["runtime"] = sync_business_runtime_settings(result.get("changed_names") or [])
                if result.get("changed"):
                    result["restart"] = {"ok": False, "skipped": "hot_applied"}
                else:
                    result["restart"] = {"ok": False, "skipped": "unchanged"}
            except Exception as exc:
                if parsed.path.startswith("/api/"):
                    self.send_json_uncached({"ok": False, "error": str(exc)})
                else:
                    self.redirect("/admin?" + urlencode({"config_error": str(exc)[:220]}) + "#config")
                return
            if parsed.path.startswith("/api/"):
                self.send_json_uncached(result)
            else:
                saved_state = "env_hot" if result.get("changed") else "env_noop"
                self.redirect(f"/admin?config_saved={saved_state}#config")
            return
        if parsed.path in {"/admin/config/yaml", "/api/admin/config/yaml"}:
            if not self.require_admin():
                return
            if not self.enforce_rate_limit("admin", self.client_ip(), RATE_LIMIT_ADMIN):
                return
            try:
                form = self.read_form()
                result = write_yaml_config(form.get("config_yaml", ""))
            except Exception as exc:
                if parsed.path.startswith("/api/"):
                    self.send_json_uncached({"ok": False, "error": str(exc)})
                else:
                    self.redirect("/admin?" + urlencode({"config_error": str(exc)[:220]}) + "#yaml")
                return
            if parsed.path.startswith("/api/"):
                self.send_json_uncached(result)
            else:
                self.redirect("/admin?config_saved=yaml#yaml")
            return
        self.send_response(404)
        self.end_headers()
        self.write_response(b"not found")

    def log_message(self, fmt: str, *args: Any) -> None:
        sanitized_args = list(args)
        if sanitized_args and isinstance(sanitized_args[0], str):
            sanitized_args[0] = re.sub(r"([?&]token=)[^&\s]+", r"\1[redacted]", sanitized_args[0])
        print(f"{self.address_string()} - {fmt % tuple(sanitized_args)}")


SINA_US_QUOTE_URL = "https://hq.sinajs.cn/list="
NASDAQ_COMPANY_PROFILE_URL = "https://api.nasdaq.com/api/company/{symbol}/company-profile"
US_QUOTE_SYMBOL_MAP: dict[str, list[str]] = {}  # populated from config or known list
US_SECTOR_LABELS = {
    "Basic Materials": "基础材料",
    "Communication Services": "通信服务",
    "Communications": "通信服务",
    "Consumer Cyclical": "可选消费",
    "Consumer Defensive": "必需消费",
    "Consumer Discretionary": "可选消费",
    "Consumer Staples": "必需消费",
    "Energy": "能源",
    "Financial Services": "金融服务",
    "Financials": "金融",
    "Healthcare": "医疗保健",
    "Health Care": "医疗保健",
    "Industrials": "工业",
    "Real Estate": "房地产",
    "Technology": "科技",
    "Utilities": "公用事业",
}


def localized_us_sector(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    label = US_SECTOR_LABELS.get(raw)
    return f"{label}（{raw}）" if label else raw


def fetch_us_company_profile(symbol: str) -> dict[str, str]:
    safe_symbol = re.sub(r"[^A-Za-z0-9.\-]", "", str(symbol or "").upper())
    if not safe_symbol:
        return {}
    url = NASDAQ_COMPANY_PROFILE_URL.format(symbol=safe_symbol)
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
                "Origin": "https://www.nasdaq.com",
                "Referer": f"https://www.nasdaq.com/market-activity/stocks/{safe_symbol.lower()}",
            },
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            payload = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception:
        return {}
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return {}

    def profile_value(key: str) -> str:
        item = data.get(key)
        if isinstance(item, dict):
            return str(item.get("value") or "").strip()
        return str(item or "").strip()

    sector = localized_us_sector(profile_value("Sector"))
    industry = profile_value("Industry")
    profile: dict[str, str] = {}
    if sector:
        profile["sector"] = sector
    if industry:
        profile["industry"] = industry
    return profile


def fetch_us_company_profiles(symbols: list[str]) -> dict[str, dict[str, str]]:
    unique_symbols = list(dict.fromkeys(s for s in symbols if s))
    if not unique_symbols:
        return {}
    max_workers = min(6, len(unique_symbols))
    profiles: dict[str, dict[str, str]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for symbol, profile in zip(unique_symbols, executor.map(fetch_us_company_profile, unique_symbols)):
            if profile:
                profiles[symbol] = profile
    return profiles


def fetch_us_quotes(symbols: list[str]) -> dict[str, Any]:
    """Fetch US stock quotes from Sina. Returns {items: {TICKER: {price, name, change, pct}}}."""
    result: dict[str, Any] = {"items": {}, "symbols": symbols, "error": None}
    if not symbols:
        return result
    # Map tickers to Sina codes: gb_<ticker.lower()>
    codes = [f"gb_{s.lower()}" for s in symbols]
    url = SINA_US_QUOTE_URL + ",".join(codes)
    try:
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("gbk", "ignore")
    except Exception as e:
        result["error"] = f"quote fetch error: {e}"
        return result
    # Parse: var hq_str_gb_ticker="name,price,pct,..."  per line
    for line in raw.split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        try:
            var_part, val_part = line.split("=", 1)
            val = val_part.strip().strip('"')
            code = var_part.replace("var hq_str_", "").strip()
            ticker = code.replace("gb_", "").upper()
            parts = val.split(",")
            if len(parts) >= 4:
                name = parts[0]
                price = _safe_float(parts[1])
                pct = _safe_float(parts[2])
                change = _safe_float(parts[4]) if len(parts) > 4 else None
                result["items"][ticker] = {
                    "name": name, "price": price, "pct": pct, "change": change,
                }
        except (ValueError, IndexError):
            continue
    profiles = fetch_us_company_profiles(symbols)
    for ticker, profile in profiles.items():
        if ticker in result["items"]:
            result["items"][ticker].update(profile)
        else:
            result["items"][ticker] = profile
    return result


def _safe_float(v: str) -> float | None:
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


def main() -> None:
    ensure_stats_db()
    parser = argparse.ArgumentParser(description="NiuOne dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    server = ReusableThreadingHTTPServer((args.host, args.port), Handler)
    start_b1_scheduler()
    start_pending_decision_executor()
    print(f"牛牛1号：http://{args.host}:{args.port}")
    print("设置页：/admin")
    print(f"访问统计：{STATS_DB}")
    print(f"消息历史：{push_history.DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
