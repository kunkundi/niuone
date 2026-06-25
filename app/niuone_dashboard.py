#!/usr/bin/env python3
"""NiuOne dashboard for messages, models, and trading signals."""

from __future__ import annotations

import argparse
import hashlib
import hmac
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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from http import cookies
import urllib.request

from niuone_paths import get_dashboard_env_file, get_dashboard_home, get_local_data_dir
import push_history

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
AUTH_DB = DASHBOARD_HOME / "dashboard_users.db"
ADMIN_TOKEN_FILE = DASHBOARD_HOME / "dashboard_admin_token.txt"
AUTH_COOKIE_NAME = "dashboard_token"
ADMIN_PASSWORD_COOKIE_NAME = "dashboard_admin_session"
VISITOR_COOKIE_NAME = "niuone_visitor_id"
ACTION_HEADER_NAME = "X-NiuOne-Action"
ACTION_HEADER_VALUES = {"1", "true", "yes", "on"}
NIUONE_LAUNCHD_LABELS = (
    "ai.niuone.cron-scheduler",
    "ai.niuone.x-watchlist",
    "ai.niuone.dashboard",
)
NIUONE_RESTART_DELAY_SECONDS = float(os.environ.get("NIUONE_RESTART_DELAY_SECONDS", "1.2") or "1.2")
ADMIN_PASSWORD = os.environ.get("DASHBOARD_ADMIN_PASSWORD", "").strip()
AUTH_ENABLED = os.environ.get("DASHBOARD_AUTH_ENABLED", "1").lower() not in {"0", "false", "no"}
TRUSTED_PROXY_CIDRS = tuple(
    value.strip()
    for value in os.environ.get("DASHBOARD_TRUSTED_PROXIES", "127.0.0.1/32,::1/128").split(",")
    if value.strip()
)
AUTH_MAX_ONLINE = int(os.environ.get("DASHBOARD_MAX_ONLINE", "0") or "0")
AUTH_ONLINE_WINDOW_SECONDS = int(os.environ.get("DASHBOARD_ONLINE_WINDOW_SECONDS", "300") or "300")
AUTH_TOUCH_INTERVAL_SECONDS = int(os.environ.get("DASHBOARD_AUTH_TOUCH_INTERVAL_SECONDS", "30") or "30")
MAX_POST_BODY_BYTES = int(os.environ.get("DASHBOARD_MAX_POST_BODY_BYTES", str(256 * 1024)) or str(256 * 1024))
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
RATE_LIMIT_AUTH = int(os.environ.get("DASHBOARD_RATE_LIMIT_AUTH", "900") or "900")
RATE_LIMIT_LOGIN = int(os.environ.get("DASHBOARD_RATE_LIMIT_LOGIN", "20") or "20")
RATE_LIMIT_ADMIN = int(os.environ.get("DASHBOARD_RATE_LIMIT_ADMIN", "90") or "90")
RATE_LIMIT_BUCKETS: dict[tuple[str, str], tuple[float, int]] = {}
RATE_LIMIT_LOCK = threading.Lock()
AUTH_TOUCH_CACHE: dict[str, float] = {}
AUTH_TOUCH_LOCK = threading.Lock()
VISIT_STATS_LOCK = threading.Lock()
API_TTLS = {
    "messages": 10,
    "b1_screen": 5,
    "niuniu_practice": 5,
    "practice_benchmarks": 30,
    "indices": int(os.environ.get("DASHBOARD_INDICES_TTL_SECONDS", "15") or "15"),
    "sectors": 60,
    "hot_stocks": 60,
    "money_flow": 60,
    "market_flow": 30,
    "us_quotes": 30,
}

SECRET_PLACEHOLDER = "__KEEP_SECRET__"
SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|credential|(?:^|[_-])token(?:$|[_-]))",
    re.I,
)

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

    {"name": "DASHBOARD_AUTH_ENABLED", "label": "启用访问认证", "group": "访问控制", "kind": "bool", "default": "1", "effect": "restart"},
    {"name": "DASHBOARD_ADMIN_PASSWORD", "label": "设置页管理员密码", "group": "访问控制", "kind": "secret", "default": "", "effect": "restart"},
    {"name": "DASHBOARD_MAX_ONLINE", "label": "最大在线 viewer", "group": "访问控制", "kind": "int", "default": "0", "effect": "restart"},
    {"name": "DASHBOARD_ONLINE_WINDOW_SECONDS", "label": "在线判断窗口秒数", "group": "访问控制", "kind": "int", "default": "300", "effect": "restart"},
    {"name": "DASHBOARD_AUTH_TOUCH_INTERVAL_SECONDS", "label": "访问时间写库间隔秒数", "group": "访问控制", "kind": "int", "default": "30", "effect": "restart"},
    {"name": "DASHBOARD_EDGE_CACHE_ENABLED", "label": "允许 CDN 缓存 API", "group": "访问控制", "kind": "bool", "default": "0", "effect": "restart"},
    {"name": "DASHBOARD_MAX_POST_BODY_BYTES", "label": "POST 表单最大字节", "group": "访问控制", "kind": "int", "default": str(256 * 1024), "effect": "restart"},

    {"name": "DASHBOARD_RATE_LIMIT_ENABLED", "label": "启用限流", "group": "限流与缓存", "kind": "bool", "default": "1", "effect": "restart"},
    {"name": "DASHBOARD_RATE_LIMIT_WINDOW_SECONDS", "label": "限流窗口秒数", "group": "限流与缓存", "kind": "int", "default": "60", "effect": "restart"},
    {"name": "DASHBOARD_RATE_LIMIT_ANON", "label": "未登录请求/窗口", "group": "限流与缓存", "kind": "int", "default": "240", "effect": "restart"},
    {"name": "DASHBOARD_RATE_LIMIT_AUTH", "label": "已登录请求/窗口", "group": "限流与缓存", "kind": "int", "default": "900", "effect": "restart"},
    {"name": "DASHBOARD_RATE_LIMIT_LOGIN", "label": "登录尝试/窗口", "group": "限流与缓存", "kind": "int", "default": "20", "effect": "restart"},
    {"name": "DASHBOARD_RATE_LIMIT_ADMIN", "label": "管理操作/窗口", "group": "限流与缓存", "kind": "int", "default": "90", "effect": "restart"},
    {"name": "DASHBOARD_API_CACHE_MAX_ENTRIES", "label": "API 缓存条目上限", "group": "限流与缓存", "kind": "int", "default": "256", "effect": "restart"},
    {"name": "DASHBOARD_API_OFFSET_MAX", "label": "消息分页最大 offset", "group": "限流与缓存", "kind": "int", "default": "5000", "effect": "restart"},
    {"name": "DASHBOARD_X_MEDIA_CACHE_MAX_ENTRIES", "label": "X 图片缓存条目上限", "group": "限流与缓存", "kind": "int", "default": "96", "effect": "restart"},
    {"name": "DASHBOARD_X_MEDIA_CACHE_TTL_SECONDS", "label": "X 图片缓存 TTL 秒数", "group": "限流与缓存", "kind": "int", "default": str(7 * 24 * 3600), "effect": "restart"},
    {"name": "DASHBOARD_X_MEDIA_MAX_BYTES", "label": "X 图片代理最大字节", "group": "限流与缓存", "kind": "int", "default": str(8 * 1024 * 1024), "effect": "restart"},

    {"name": "DASHBOARD_B1_SCHEDULE_ENABLED", "label": "启用 B1 定时扫描", "group": "任务调度", "kind": "bool", "default": "1", "effect": "restart"},
    {"name": "DASHBOARD_B1_SCHEDULE_TIMES", "label": "选股及买卖决策时间点", "group": "选股及买卖决策时间点", "kind": "time_list", "default": "09:25,10:00,10:30,11:00,11:20,13:00,13:30,14:00,14:30,14:50", "effect": "runtime"},
    {"name": "DASHBOARD_B1_SCAN_TIMEOUT_SECONDS", "label": "B1 扫描超时秒数", "group": "任务调度", "kind": "int", "default": "360", "effect": "restart"},
    {"name": "DASHBOARD_B1_SCHEDULE_CATCHUP_MINUTES", "label": "B1 漏触发补跑窗口分钟", "group": "任务调度", "kind": "int", "default": "35", "effect": "restart"},
    {"name": "DASHBOARD_B1_SCHEDULE_STALE_SECONDS", "label": "B1 运行中陈旧秒数", "group": "任务调度", "kind": "int", "default": "900", "effect": "restart"},
    {"name": "DASHBOARD_CRON_MAX_ATTEMPTS", "label": "Cron 失败最大运行次数", "group": "任务调度", "kind": "int", "default": "2", "effect": "next_run"},
    {"name": "DASHBOARD_CRON_RETRY_DELAY_SECONDS", "label": "Cron 失败重试间隔秒数", "group": "任务调度", "kind": "int", "default": "300", "effect": "next_run"},
    {"name": "DASHBOARD_PENDING_DECISION_POLL_SECONDS", "label": "延迟成交检查秒数", "group": "任务调度", "kind": "int", "default": "5", "effect": "restart"},
    {"name": "DASHBOARD_DECISION_MAX_TOKENS", "label": "决策最大输出长度", "group": "买卖决策模型", "kind": "int", "default": "6000", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_TIMEOUT", "label": "决策请求超时", "group": "买卖决策模型", "kind": "int", "default": "180", "effect": "next_run"},

    {"name": "US_RATING_BASE_URL", "label": "美股评级 API Base URL", "group": "上游模型覆盖", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "US_RATING_API_KEY", "label": "美股评级 API Key", "group": "上游模型覆盖", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "CROSSDESK_BASE_URL", "label": "Crossdesk Base URL", "group": "上游模型覆盖", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "CROSSDESK_API_KEY", "label": "Crossdesk API Key", "group": "上游模型覆盖", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_GROK_MODEL", "label": "Grok 模型", "group": "推文监控/美股买入评级模型", "kind": "text", "default": "grok-4.20-multi-agent-xhigh", "effect": "next_run"},
    {"name": "DASHBOARD_GROK_BASE_URL", "label": "Grok API 地址", "group": "推文监控/美股买入评级模型", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_GROK_API_KEY", "label": "Grok API 密钥", "group": "推文监控/美股买入评级模型", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_MODEL", "label": "买卖决策模型", "group": "买卖决策模型", "kind": "text", "default": "deepseek-v4-pro", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_BASE_URL", "label": "DeepSeek API 地址", "group": "买卖决策模型", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_API_KEY", "label": "DeepSeek API 密钥", "group": "买卖决策模型", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_MARKET_AUCTION_CRON", "label": "盘前竞价监控时间", "group": "盘面监控生产时间点", "kind": "cron_time", "default": "25 9 * * 1-5", "effect": "next_run"},
    {"name": "DASHBOARD_MARKET_MIDDAY_CRON", "label": "午盘监控时间", "group": "盘面监控生产时间点", "kind": "cron_time", "default": "40 11 * * 1-5", "effect": "next_run"},
    {"name": "DASHBOARD_MARKET_CLOSE_CRON", "label": "盘后监控时间", "group": "盘面监控生产时间点", "kind": "cron_time", "default": "10 15 * * 1-5", "effect": "next_run"},
    {"name": "X_WATCHLIST_DAEMON_INTERVAL_SECONDS", "label": "推文监控间隔", "group": "推文监控周期", "kind": "int", "default": "1200", "effect": "next_run"},
    {"name": "DASHBOARD_US_RATING_CRON", "label": "美股买入评级时间", "group": "美股买入评级周期", "kind": "cron_time", "default": "0 11 * * *", "effect": "next_run"},
    {"name": "DASHBOARD_INDICES_TTL_SECONDS", "label": "指数行情更新间隔", "group": "指数行情更新周期", "kind": "int", "default": "15", "effect": "runtime"},

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
    "DASHBOARD_GROK_MODEL",
    "DASHBOARD_GROK_BASE_URL",
    "DASHBOARD_GROK_API_KEY",
    "DASHBOARD_DECISION_MODEL",
    "DASHBOARD_DECISION_BASE_URL",
    "DASHBOARD_DECISION_API_KEY",
    "DASHBOARD_DECISION_MAX_TOKENS",
    "DASHBOARD_DECISION_TIMEOUT",
    "DASHBOARD_B1_SCHEDULE_TIMES",
    "DASHBOARD_MARKET_AUCTION_CRON",
    "DASHBOARD_MARKET_MIDDAY_CRON",
    "DASHBOARD_MARKET_CLOSE_CRON",
    "X_WATCHLIST_DAEMON_INTERVAL_SECONDS",
    "DASHBOARD_US_RATING_CRON",
    "DASHBOARD_CRON_MAX_ATTEMPTS",
    "DASHBOARD_CRON_RETRY_DELAY_SECONDS",
    "DASHBOARD_INDICES_TTL_SECONDS",
]
TRADER_RUNTIME_ENV_NAMES = {
    "DASHBOARD_GROK_MODEL",
    "DASHBOARD_GROK_BASE_URL",
    "DASHBOARD_GROK_API_KEY",
    "DASHBOARD_DECISION_MODEL",
    "DASHBOARD_DECISION_BASE_URL",
    "DASHBOARD_DECISION_API_KEY",
    "DASHBOARD_DECISION_MAX_TOKENS",
    "DASHBOARD_DECISION_TIMEOUT",
}
ENV_GROUP_ORDER = [
    "推文监控/美股买入评级模型",
    "买卖决策模型",
    "选股及买卖决策时间点",
    "盘面监控生产时间点",
    "推文监控周期",
    "美股买入评级周期",
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


def should_touch_auth(token_hash: str, now: float) -> bool:
    with AUTH_TOUCH_LOCK:
        last = AUTH_TOUCH_CACHE.get(token_hash, 0.0)
        if now - last < AUTH_TOUCH_INTERVAL_SECONDS:
            return False
        AUTH_TOUCH_CACHE[token_hash] = now
        if len(AUTH_TOUCH_CACHE) > 5000:
            cutoff = now - AUTH_ONLINE_WINDOW_SECONDS * 2
            for key, ts in list(AUTH_TOUCH_CACHE.items()):
                if ts < cutoff:
                    AUTH_TOUCH_CACHE.pop(key, None)
        return True


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


def new_viewer_token() -> str:
    return 'nv_' + secrets.token_urlsafe(32)


def new_invite_code() -> str:
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return 'NN-' + ''.join(secrets.choice(alphabet) for _ in range(4)) + '-' + ''.join(secrets.choice(alphabet) for _ in range(4))


def ensure_auth_db() -> None:
    AUTH_DB.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(AUTH_DB)) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS invite_codes (
                code TEXT PRIMARY KEY,
                max_uses INTEGER NOT NULL DEFAULT 1,
                used_count INTEGER NOT NULL DEFAULT 0,
                expires_at REAL,
                note TEXT DEFAULT '',
                disabled INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS viewers (
                token_hash TEXT PRIMARY KEY,
                token_prefix TEXT NOT NULL,
                invite_code TEXT,
                nickname TEXT DEFAULT '',
                role TEXT NOT NULL DEFAULT 'viewer',
                created_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                last_ip TEXT DEFAULT '',
                user_agent TEXT DEFAULT '',
                disabled INTEGER NOT NULL DEFAULT 0
            )
        """)
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
        con.commit()


def create_invite_code(code: str | None = None, max_uses: int = 1, ttl_hours: int = 168, note: str = '') -> dict[str, Any]:
    ensure_auth_db()
    code = (code or new_invite_code()).strip().upper()
    max_uses = max(1, int(max_uses or 1))
    expires_at = _now_ts() + max(1, int(ttl_hours or 168)) * 3600 if ttl_hours else None
    with closing(sqlite3.connect(AUTH_DB)) as con:
        con.execute('INSERT INTO invite_codes(code,max_uses,used_count,expires_at,note,disabled,created_at) VALUES(?,?,?,?,?,?,?)',
                    (code, max_uses, 0, expires_at, note or '', 0, _now_ts()))
        con.commit()
    return {'code': code, 'max_uses': max_uses, 'used_count': 0, 'expires_at': expires_at, 'note': note or '', 'disabled': False}


def list_invite_codes() -> list[dict[str, Any]]:
    ensure_auth_db()
    with closing(sqlite3.connect(AUTH_DB)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute('SELECT * FROM invite_codes ORDER BY created_at DESC').fetchall()
    return [dict(r) for r in rows]


def list_viewers() -> list[dict[str, Any]]:
    ensure_auth_db()
    with closing(sqlite3.connect(AUTH_DB)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute('SELECT token_hash, token_prefix, invite_code, nickname, role, created_at, last_seen_at, last_ip, user_agent, disabled FROM viewers ORDER BY last_seen_at DESC').fetchall()
    return [dict(r) for r in rows]


def count_online_viewers() -> int:
    ensure_auth_db()
    cutoff = _now_ts() - AUTH_ONLINE_WINDOW_SECONDS
    with closing(sqlite3.connect(AUTH_DB)) as con:
        row = con.execute("SELECT COUNT(*) FROM viewers WHERE disabled=0 AND role='viewer' AND last_seen_at>=?", (cutoff,)).fetchone()
    return int(row[0] if row else 0)


def increment_visit_count(visitor_id: str) -> dict[str, int]:
    """Count page views for the main dashboard only; API polling is excluded."""
    ensure_auth_db()
    now = _now_ts()
    visitor_hash = hash_token(visitor_id)
    with VISIT_STATS_LOCK:
        with closing(sqlite3.connect(AUTH_DB)) as con:
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


def redeem_invite_code(code: str, nickname: str = '', ip: str = '', user_agent: str = '') -> dict[str, Any]:
    ensure_auth_db()
    code = (code or '').strip().upper()
    if not code:
        return {'ok': False, 'error': '请输入邀请码'}
    now = _now_ts()
    with closing(sqlite3.connect(AUTH_DB)) as con:
        con.row_factory = sqlite3.Row
        row = con.execute('SELECT * FROM invite_codes WHERE code=?', (code,)).fetchone()
        if not row:
            return {'ok': False, 'error': '邀请码不存在'}
        if int(row['disabled'] or 0):
            return {'ok': False, 'error': '邀请码已停用'}
        if row['expires_at'] and float(row['expires_at']) < now:
            return {'ok': False, 'error': '邀请码已过期'}
        if int(row['used_count'] or 0) >= int(row['max_uses'] or 1):
            return {'ok': False, 'error': '邀请码已用完'}
        if AUTH_MAX_ONLINE and count_online_viewers() >= AUTH_MAX_ONLINE:
            return {'ok': False, 'error': '当前观看人数已满，请稍后再试'}
        token = new_viewer_token()
        token_hash = hash_token(token)
        con.execute('UPDATE invite_codes SET used_count=used_count+1 WHERE code=?', (code,))
        con.execute("""INSERT INTO viewers(token_hash,token_prefix,invite_code,nickname,role,created_at,last_seen_at,last_ip,user_agent,disabled)
                       VALUES(?,?,?,?,?,?,?,?,?,0)""",
                    (token_hash, token[:10], code, (nickname or '').strip()[:80], 'viewer', now, now, ip[:80], user_agent[:300]))
        con.commit()
    return {'ok': True, 'token': token, 'role': 'viewer'}


def get_or_create_admin_token() -> str:
    ensure_auth_db()
    if ADMIN_TOKEN_FILE.exists():
        token = ADMIN_TOKEN_FILE.read_text().strip()
        if token:
            return token
    token = 'na_' + secrets.token_urlsafe(36)
    ADMIN_TOKEN_FILE.write_text(token + '\n')
    try:
        ADMIN_TOKEN_FILE.chmod(0o600)
    except OSError:
        pass
    now = _now_ts()
    with closing(sqlite3.connect(AUTH_DB)) as con:
        con.execute("""INSERT OR REPLACE INTO viewers(token_hash,token_prefix,invite_code,nickname,role,created_at,last_seen_at,last_ip,user_agent,disabled)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (hash_token(token), token[:10], 'admin-bootstrap', '牛牛大王', 'admin', now, now, '', 'bootstrap', 0))
        con.commit()
    return token


def authenticate_viewer_token(token: str | None, ip: str = '', user_agent: str = '') -> dict[str, Any] | None:
    ensure_auth_db()
    token = (token or '').strip()
    if not token:
        return None
    if ADMIN_TOKEN_FILE.exists() and secrets.compare_digest(token, ADMIN_TOKEN_FILE.read_text().strip()):
        get_or_create_admin_token()
    token_hash = hash_token(token)
    with closing(sqlite3.connect(AUTH_DB)) as con:
        con.row_factory = sqlite3.Row
        row = con.execute('SELECT * FROM viewers WHERE token_hash=?', (token_hash,)).fetchone()
        if not row or int(row['disabled'] or 0):
            return None
        now = _now_ts()
        if should_touch_auth(token_hash, now):
            con.execute('UPDATE viewers SET last_seen_at=?, last_ip=?, user_agent=? WHERE token_hash=?',
                        (now, ip[:80], user_agent[:300], row['token_hash']))
            con.commit()
        result = dict(row)
        result['last_seen_at'] = now
        return result


def set_viewer_disabled(token_or_hash: str, disabled: bool = True) -> dict[str, Any]:
    ensure_auth_db()
    key = (token_or_hash or '').strip()
    if not key:
        return {'ok': False, 'error': 'missing token'}
    token_hash = key if len(key) == 64 and re.fullmatch(r'[0-9a-f]+', key) else hash_token(key)
    with closing(sqlite3.connect(AUTH_DB)) as con:
        cur = con.execute('UPDATE viewers SET disabled=? WHERE token_hash=?', (1 if disabled else 0, token_hash))
        con.commit()
    return {'ok': cur.rowcount > 0}


def set_invite_disabled(code: str, disabled: bool = True) -> dict[str, Any]:
    ensure_auth_db()
    with closing(sqlite3.connect(AUTH_DB)) as con:
        cur = con.execute('UPDATE invite_codes SET disabled=? WHERE code=?', (1 if disabled else 0, (code or '').strip().upper()))
        con.commit()
    return {'ok': cur.rowcount > 0}


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


def get_practice_payload() -> dict[str, Any]:
    try:
        trader = get_trader_module()
        # 盘面时间内按 dashboard 刷新节奏补记账户权益点；与是否交易/是否有B1候选无关。
        if hasattr(trader, "maybe_record_session_equity_heartbeat"):
            trader.maybe_record_session_equity_heartbeat()
        payload = trader.get_dashboard_payload()
        try:
            refresh_b1_candidate_cache_from_current_pool()
        except Exception as refresh_exc:
            print(f"[WARN] B1候选池复核失败: {type(refresh_exc).__name__}: {refresh_exc}", flush=True)
        return payload
    except Exception as exc:
        print(f"[WARN] practice payload error: {type(exc).__name__}: {exc}", flush=True)
        return {"positions": [], "cash": 0, "total_equity": 0, "initial_cash": 0,
                "total_pnl": 0, "total_pnl_pct": 0, "trade_log": [], "decision_log": [],
                "equity_history": [], "last_error": str(exc), "decision_model": "", "decision_provider": ""}

def get_practice_payload_fast() -> dict[str, Any]:
    """Return a local portfolio snapshot without network quote refresh or auto trading checks."""
    try:
        trader = get_trader_module()
        state = trader.load_state()
        payload = trader.enrich_portfolio(state)
        payload["equity_history"] = state.get("equity_history", [])
        payload["daily_equity_history"] = state.get("daily_equity_history", [])
        payload["trading_paused"] = state.get("trading_paused", False)
        payload["pause_reason"] = state.get("pause_reason", "")
        payload["pause_since"] = state.get("pause_since", "")
        payload["strategy_performance"] = trader.track_strategy_performance(state) if hasattr(trader, "track_strategy_performance") else {}
        payload["trade_rule_note"] = "A股模拟：100股整数倍、T+1；模拟成交仅允许09:30-11:30、13:00-15:00，09:15-09:25只作开盘集合竞价观察/申报参考，09:25-09:30静默期不按参考价记成交。系统自动卖出：买入K线/前低止损、防卖飞5分评分、卤煮半仓、S1/S2/S3逃顶、出货五式、BBI/白线两日破位、白线死叉黄线、峰值回撤/ATR吊灯保护、持仓超25日退出。"
        payload["snapshot_mode"] = "fast"
        return payload
    except Exception as exc:
        print(f"[WARN] fast practice payload error: {type(exc).__name__}: {exc}", flush=True)
        return {"positions": [], "cash": 0, "total_equity": 0, "initial_cash": 0,
                "total_pnl": 0, "total_pnl_pct": 0, "trade_log": [], "decision_log": [],
                "equity_history": [], "last_error": str(exc), "snapshot_mode": "fast"}

def normalize_b1_payload_for_trader(b1_payload: dict[str, Any]) -> dict[str, Any]:
    items = b1_payload.get("items") or b1_payload.get("candidates") or []
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
            "strategy_distribution": dict(strat_counts),
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
            schedule_meta = {}
            if schedule_slot:
                schedule_meta = {
                    "schedule_slot": schedule_slot,
                    "schedule_run_kind": schedule_run_kind or "scheduled",
                    "schedule_triggered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            cache = {**data, "items": items, "candidates": items, "count": len(items),
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


def remove_query_param(path: str, param_name: str) -> str:
    parsed = urlparse(path)
    pairs = []
    for key, values in parse_qs(parsed.query, keep_blank_values=True).items():
        if key == param_name:
            continue
        for value in values:
            pairs.append((key, value))
    query = urlencode(pairs)
    return parsed.path + (f"?{query}" if query else "")


def clamp_limit(raw: str | None, default: int = API_DEFAULT_LIMIT) -> int:
    try:
        value = int(raw) if raw else default
    except (TypeError, ValueError):
        value = default
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


def quote_env_value(value: str) -> str:
    value = str(value or "")
    if value and re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def normalize_env_update(name: str, value: str, kind: str) -> str:
    value = str(value or "").strip()
    if kind == "bool":
        return "1" if value.lower() in {"1", "true", "yes", "on"} else "0"
    if kind == "int" and value:
        int(value)
    if kind == "time_list":
        return normalize_time_list_update(value)
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
        if value == "" and name not in existing and kind != "time_list":
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
    "DASHBOARD_MARKET_AUCTION_CRON",
    "DASHBOARD_MARKET_MIDDAY_CRON",
    "DASHBOARD_MARKET_CLOSE_CRON",
    "DASHBOARD_US_RATING_CRON",
}
CRON_TIME_CONFIGS = {
    "DASHBOARD_MARKET_AUCTION_CRON": {"day_label": "周一至周五"},
    "DASHBOARD_MARKET_MIDDAY_CRON": {"day_label": "周一至周五"},
    "DASHBOARD_MARKET_CLOSE_CRON": {"day_label": "周一至周五"},
    "DASHBOARD_US_RATING_CRON": {"day_label": "每天"},
}
ADMIN_GROUP_NOTES = {
    "推文监控/美股买入评级模型": "推荐使用 grok；推文监控和美股买入评级共用这组模型配置。",
    "买卖决策模型": "推荐使用 deepseek-v4-pro；用于选股结果后的买卖决策。",
    "选股及买卖决策时间点": "使用北京时间 HH:MM，可设置多个时间点。",
    "盘面监控生产时间点": "直接填写北京时间 HH:MM；盘面监控在 A 股交易日触发。",
    "推文监控周期": "单位为秒，保存后从下一轮监控开始使用。",
    "美股买入评级周期": "直接填写北京时间 HH:MM；默认每天触发。",
    "指数行情更新周期": "单位为秒，保存后立即用于后续行情请求。",
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
        elif name in {"X_WATCHLIST_DAEMON_INTERVAL_SECONDS", "DASHBOARD_INDICES_TTL_SECONDS"} and str(value or "").strip():
            if int(value) <= 0:
                raise ValueError(f"{name} 必须大于 0")
        elif name == "DASHBOARD_CRON_MAX_ATTEMPTS" and str(value or "").strip():
            if int(value) < 1:
                raise ValueError(f"{name} 必须大于等于 1")
        elif name == "DASHBOARD_CRON_RETRY_DELAY_SECONDS" and str(value or "").strip():
            if int(value) < 0:
                raise ValueError(f"{name} 必须大于等于 0")


def sync_business_runtime_settings(changed: dict[str, str] | list[str] | set[str] | tuple[str, ...] | None) -> dict[str, Any]:
    global B1_SCHEDULE_TIMES, TRADER_MODULE, TRADER_MODULE_MTIME
    if isinstance(changed, dict):
        changed_names = set(changed.keys())
    else:
        changed_names = set(changed or [])
    env_values = parse_env_file()
    for name in ADMIN_VISIBLE_ENV_NAMES:
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

    if changed_names & TRADER_RUNTIME_ENV_NAMES:
        TRADER_MODULE = None
        TRADER_MODULE_MTIME = 0.0
        with API_RESPONSE_LOCK:
            API_RESPONSE_CACHE.pop("niuniu_practice", None)
            API_RESPONSE_CACHE.pop("niuniu_practice_fast", None)
        applied.append("trader_runtime")

    if changed_names & set(ADMIN_VISIBLE_ENV_NAMES):
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
    return "", "default"


def build_admin_config_payload() -> dict[str, Any]:
    env_values = parse_env_file()
    names = set(ADMIN_VISIBLE_ENV_NAMES)
    items = []
    admin_order = {name: idx for idx, name in enumerate(ADMIN_VISIBLE_ENV_NAMES)}
    for name in sorted(names, key=lambda n: admin_order.get(n, 999)):
        schema = ENV_CONFIG_BY_NAME.get(name, {"name": name, "label": name, "group": "其他", "kind": "text", "default": "", "effect": "restart"})
        fallback_value, fallback_source = business_config_fallback_value(name)
        default_value = schema.get("default", "")
        effective = os.environ.get(name) or env_values.get(name) or fallback_value or default_value
        secret = schema.get("kind") == "secret" or is_secret_config_key(name)
        file_value = env_values.get(name)
        if file_value is None:
            file_value = "" if secret else default_value
        source = "dashboard.env" if name in env_values else ("process env" if os.environ.get(name) else fallback_source)
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
        items.append(item)
    return {
        "items": items,
        "secret_placeholder": SECRET_PLACEHOLDER,
    }

INDICES_HTML = None

LOGIN_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>牛牛大作手 · 观看登录</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%90%AE%3C/text%3E%3C/svg%3E">
<style>
:root{color-scheme:dark;--bg:#06070a;--panel:#10131a;--line:#252b38;--text:#f2f4f8;--muted:#99a3b3;--accent:#7c5cff;--red:#fb7185;--green:#34d399}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:radial-gradient(circle at 20% 0%,rgba(124,92,255,.30),transparent 34rem),var(--bg);color:var(--text);padding:20px}.box{width:min(440px,100%);border:1px solid var(--line);border-radius:24px;padding:28px;background:linear-gradient(135deg,rgba(16,19,26,.94),rgba(21,26,36,.88));box-shadow:0 28px 90px rgba(0,0,0,.36)}h1{margin:0 0 8px;font-size:30px;letter-spacing:-.04em}.sub{color:var(--muted);line-height:1.6;margin-bottom:22px}label{display:block;color:#cbd5e1;font-weight:800;font-size:13px;margin:14px 0 7px}input{width:100%;border:1px solid var(--line);background:#0b0e14;color:var(--text);border-radius:14px;padding:13px 14px;font:inherit;outline:none}input:focus{border-color:rgba(124,92,255,.75);box-shadow:0 0 0 4px rgba(124,92,255,.13)}button{width:100%;margin-top:18px;border:0;border-radius:14px;padding:13px 14px;font:inherit;font-weight:900;color:white;background:linear-gradient(135deg,var(--accent),#24c6dc);cursor:pointer}.error{border:1px solid rgba(251,113,133,.35);background:rgba(127,29,29,.25);color:#fecdd3;border-radius:14px;padding:10px 12px;margin-bottom:14px}.hint{font-size:12px;color:#64748b;margin-top:14px;line-height:1.5}.ok{color:var(--green)}</style>
</head><body><form class="box" method="post" action="/login">
<h1>🐮 牛牛大作手</h1><div class="sub">请输入牛牛大王发放的邀请码，激活本设备的观看权限。</div>
__ERROR__
<label>邀请码</label><input name="code" autocomplete="one-time-code" placeholder="NN-XXXX-XXXX" required autofocus>
<label>昵称（可选）</label><input name="nickname" autocomplete="nickname" placeholder="方便牛牛1号识别访问者">
<button type="submit">进入 Dashboard</button>
<div class="hint">邀请码只用于首次激活；成功后会在浏览器保存个人访问凭证。请勿转发邀请码或访问链接。</div>
</form></body></html>"""

ADMIN_PASSWORD_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>牛牛大作手 · 设置验证</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%90%AE%3C/text%3E%3C/svg%3E">
<style>
:root{color-scheme:dark;--bg:#06070a;--panel:#10131a;--line:#252b38;--text:#f2f4f8;--muted:#99a3b3;--accent:#7c5cff;--cyan:#24c6dc;--red:#fb7185}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:radial-gradient(circle at 20% 0%,rgba(124,92,255,.28),transparent 34rem),var(--bg);color:var(--text);padding:20px}.box{width:min(420px,100%);border:1px solid var(--line);border-radius:24px;padding:28px;background:linear-gradient(135deg,rgba(16,19,26,.94),rgba(21,26,36,.88));box-shadow:0 28px 90px rgba(0,0,0,.36)}h1{margin:0 0 8px;font-size:28px;letter-spacing:0}.sub{color:var(--muted);line-height:1.6;margin-bottom:22px}label{display:block;color:#cbd5e1;font-weight:800;font-size:13px;margin:14px 0 7px}input{width:100%;border:1px solid var(--line);background:#0b0e14;color:var(--text);border-radius:14px;padding:13px 14px;font:inherit;outline:none}input:focus{border-color:rgba(124,92,255,.75);box-shadow:0 0 0 4px rgba(124,92,255,.13)}button{width:100%;margin-top:18px;border:0;border-radius:14px;padding:13px 14px;font:inherit;font-weight:900;color:white;background:linear-gradient(135deg,var(--accent),var(--cyan));cursor:pointer}.error{border:1px solid rgba(251,113,133,.35);background:rgba(127,29,29,.25);color:#fecdd3;border-radius:14px;padding:10px 12px;margin-bottom:14px}.hint{font-size:12px;color:#64748b;margin-top:14px;line-height:1.5}.toplink{display:inline-block;color:#9db2ff;text-decoration:none;margin-top:14px;font-weight:800}
</style>
</head><body><form class="box" method="post" action="/admin/password">
<h1>设置页验证</h1><div class="sub">请输入管理员密码后进入业务配置。</div>
__ERROR__
<label>管理员密码</label><input name="admin_password" type="password" autocomplete="current-password" placeholder="管理员密码" required autofocus>
<button type="submit">进入设置</button>
<div class="hint">该密码来自启动配置 <code>DASHBOARD_ADMIN_PASSWORD</code>，修改后重启服务生效。</div>
<a class="toplink" href="/">返回 Dashboard</a>
</form></body></html>"""

ADMIN_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>牛牛大作手</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%90%AE%3C/text%3E%3C/svg%3E">
<style>
:root{color-scheme:dark;--bg:#07090d;--surface:#10151b;--surface2:#151b23;--line:#26313d;--line2:#334155;--text:#f3f6fb;--muted:#94a3b8;--soft:#cbd5e1;--accent:#2dd4bf;--blue:#60a5fa;--red:#fb7185;--green:#34d399;--yellow:#fbbf24}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:linear-gradient(180deg,#0b1016 0%,var(--bg) 48%,#050608 100%);color:var(--text);min-height:100vh}.admin-header{border-bottom:1px solid rgba(148,163,184,.16);background:rgba(7,9,13,.88);backdrop-filter:blur(16px);padding:22px clamp(16px,4vw,42px)}.admin-header-inner{max-width:1180px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.eyebrow{font-size:12px;font-weight:850;color:var(--accent);letter-spacing:.04em;margin-bottom:6px}h1{margin:0;font-size:30px;letter-spacing:0}h2{margin:0;font-size:18px;letter-spacing:0}p{margin:0}.muted{color:var(--muted)}.toplink{color:#dbeafe;text-decoration:none;border:1px solid rgba(148,163,184,.20);background:rgba(15,23,42,.62);border-radius:8px;padding:9px 12px;font-weight:850}.toplink:hover{border-color:rgba(96,165,250,.54);background:rgba(30,41,59,.72)}.admin-main{width:min(1180px,100%);margin:0 auto;padding:20px clamp(14px,4vw,42px) 34px;display:grid;gap:16px}.settings-form{display:grid;gap:14px}.settings-group{border:1px solid rgba(148,163,184,.16);border-radius:8px;background:rgba(16,21,27,.88);box-shadow:0 18px 56px rgba(0,0,0,.22);overflow:hidden}.settings-group-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:16px 18px;border-bottom:1px solid rgba(148,163,184,.12);background:rgba(21,27,35,.72)}.settings-group-note{color:var(--muted);font-size:13px;line-height:1.5;margin-top:5px}.settings-count{font-size:12px;color:#a7f3d0;border:1px solid rgba(45,212,191,.24);background:rgba(20,184,166,.10);border-radius:999px;padding:3px 8px;white-space:nowrap}.settings-list{display:grid}.setting-row{display:grid;grid-template-columns:minmax(170px,.72fr) minmax(250px,1fr) minmax(220px,.84fr);gap:16px;align-items:start;padding:16px 18px;border-top:1px solid rgba(148,163,184,.10)}.setting-row:first-child{border-top:0}.setting-copy{display:grid;gap:4px;min-width:0}.config-label{font-weight:850;color:#e5edf8;line-height:1.35}.setting-editor{min-width:0}.setting-editor input,.setting-editor select{width:100%;min-width:0}.setting-state{display:grid;gap:8px;min-width:0}.setting-state-item{display:grid;gap:3px}.setting-state-label{font-size:11px;color:#7b8aa0;font-weight:850}.config-meta{font-size:12px;color:#b6c2d2;max-width:100%;overflow-wrap:anywhere;line-height:1.45}.config-empty{color:#64748b}input,select,textarea,button{border:1px solid var(--line);background:#0b0f15;color:var(--text);border-radius:8px;padding:10px 12px;font:inherit;min-width:0}input:focus,select:focus,textarea:focus{outline:2px solid rgba(96,165,250,.70);outline-offset:1px;border-color:rgba(96,165,250,.62)}textarea{width:100%;min-height:460px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.45;resize:vertical}button{cursor:pointer;font-weight:850;background:linear-gradient(135deg,rgba(20,184,166,.92),rgba(96,165,250,.76));border:0;color:#061017}.save-button{min-height:42px;padding:10px 16px;justify-self:end}.settings-actions{position:sticky;bottom:14px;z-index:3;display:flex;justify-content:flex-end;padding:10px;border:1px solid rgba(148,163,184,.18);border-radius:8px;background:rgba(8,11,16,.86);backdrop-filter:blur(14px);box-shadow:0 18px 54px rgba(0,0,0,.30)}.time-list-control{display:grid;gap:8px}.time-list-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:6px}.time-list-item{display:grid;grid-template-columns:minmax(92px,1fr) 34px;gap:4px;align-items:center}.time-list-item input{min-width:0}.time-list-add,.time-list-remove{display:inline-grid;place-items:center;padding:0;border-radius:8px;border:1px solid rgba(148,163,184,.22);background:rgba(15,23,42,.78);color:#dbeafe}.time-list-add{width:38px;height:38px;justify-self:start}.time-list-remove{width:34px;height:38px;color:#fecdd3}.okmsg{border:1px solid rgba(52,211,153,.28);background:rgba(6,78,59,.20);color:#bbf7d0;border-radius:8px;padding:11px 13px}.errmsg{border:1px solid rgba(251,113,133,.34);background:rgba(127,29,29,.22);color:#fecdd3;border-radius:8px;padding:11px 13px}@media(max-width:940px){.setting-row{grid-template-columns:1fr;gap:10px}.setting-state{grid-template-columns:repeat(2,minmax(0,1fr))}.save-button{width:100%}.settings-actions{position:static}}@media(max-width:620px){.admin-header{padding:18px 14px}.admin-main{padding:16px 12px 26px}.settings-group-head,.setting-row{padding:14px}.setting-state{grid-template-columns:1fr}.time-list-grid{grid-template-columns:1fr}.toplink{width:100%;text-align:center}}</style>
</head><body><header class="admin-header"><div class="admin-header-inner"><div><div class="eyebrow">牛牛大作手</div><h1>设置</h1></div><a class="toplink" href="/">返回 Dashboard</a></div></header>
<main class="admin-main">
__NOTICE__
__ENV_CONFIG__
</main>
<script>
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
    input.type = 'time';
    input.name = fieldName;
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
    input.focus();
    return;
  }
  var removeButton = target.closest('[data-time-list-remove]');
  if (removeButton) {
    var item = removeButton.closest('.time-list-item');
    if (item) item.remove();
  }
});
</script>
</body></html>"""

INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>牛牛大作手</title>
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
    .settings-link, .refresh-pill, .visit-pill { display:inline-flex; align-items:baseline; gap:8px; flex:0 0 auto; border:1px solid rgba(148,163,184,.16); background:rgba(15,23,42,.58); border-radius:999px; padding:7px 11px; color:#cbd5e1; box-shadow:inset 0 1px 0 rgba(255,255,255,.035); }
    .settings-link { align-items:center; text-decoration:none; color:#e5edf8; font-size:13px; font-weight:850; border-color:rgba(124,92,255,.30); background:rgba(124,92,255,.14); transition:.16s ease; }
    .settings-link:hover { border-color:rgba(157,178,255,.62); background:rgba(124,92,255,.22); transform:translateY(-1px); }
    .settings-link:focus-visible { outline:2px solid rgba(157,178,255,.86); outline-offset:2px; }
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
    .sector-cloud { background:linear-gradient(180deg, rgba(15,23,42,.92), rgba(6,10,18,.96)); border:1px solid rgba(148,163,184,.14); border-radius:18px; padding:18px; box-shadow:0 18px 70px rgba(0,0,0,.24), inset 0 1px 0 rgba(255,255,255,.035); }
    .sector-cloud h3 { margin:0 0 10px; font-size:16px; color:#dbeafe; font-weight:850; letter-spacing:-.01em; }
    .sector-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(130px, 1fr)); gap:8px; }
    .sector-item { background:rgba(2,6,23,.50); border:1px solid rgba(148,163,184,.12); border-radius:13px; padding:10px; box-shadow:inset 0 1px 0 rgba(255,255,255,.025); }
    .sector-name { font-size:12px; color:#cbd5e1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-weight:700; }
    .sector-item.up, .hot-item.up { background:rgba(127,29,29,.28); border-color:rgba(248,113,113,.22); }
    .sector-item.down, .hot-item.down { background:rgba(6,78,59,.28); border-color:rgba(52,211,153,.22); }
    .sector-item.flat, .hot-item.flat { background:rgba(30,41,59,.30); border-color:rgba(148,163,184,.12); }
    .sector-item.up .sector-pct, .hot-item.up .sector-pct { color:#fb7185; text-shadow:0 0 14px rgba(248,113,113,.22); }
    .sector-item.down .sector-pct, .hot-item.down .sector-pct { color:#34d399; text-shadow:0 0 14px rgba(52,211,153,.22); }
    .sector-pct { font-size:14px; font-weight:850; margin-top:4px; font-variant-numeric:tabular-nums; }
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
    .indices-part-title { margin:0; color:#f8fafc; font-size:18px; line-height:1.2; font-weight:900; letter-spacing:0; }
    .indices-part-meta { color:#7b8aa0; font-size:12px; font-weight:750; font-variant-numeric:tabular-nums; white-space:nowrap; }
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
    .hot-item { background:rgba(2,6,23,.50); border:1px solid rgba(148,163,184,.12); border-radius:13px; padding:10px; box-shadow:inset 0 1px 0 rgba(255,255,255,.025); }
    .hot-price { font-size:14px; font-weight:850; margin-top:3px; font-variant-numeric:tabular-nums; }
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
    .market-detail-box { border-top:1px solid rgba(148,163,184,.12); padding-top:14px; display:grid; gap:10px; }
    .market-detail-line { white-space:pre-wrap; color:#e2e8f0; font-size:14px; line-height:1.65; overflow-wrap:anywhere; word-break:break-word; }
    .market-detail-heading { color:#dbeafe; font-size:13px; font-weight:850; margin-top:4px; }
    .market-detail-note { color:#94a3b8; }
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
    .practice-perf-summary { display:grid; grid-template-columns:repeat(3, minmax(150px, 210px)); gap:8px; width:max-content; max-width:100%; margin:0 0 10px; }
    .practice-perf-summary .inline-field { min-width:0; padding:7px 9px; }
    .practice-perf-summary .inline-value { font-size:13px; }
    .practice-perf-grid { display:grid; grid-template-columns:minmax(360px, 470px) minmax(280px, 330px); gap:10px; width:fit-content; max-width:100%; margin-bottom:10px; align-items:start; }
    .practice-perf-block { background:rgba(2,6,23,.42); border:1px solid rgba(148,163,184,.10); border-radius:12px; padding:10px 12px; min-width:0; }
    .practice-perf-title { color:#64748b; font-size:11px; margin-bottom:6px; }
    .exit-rule-row { width:100%; appearance:none; border:0; border-radius:8px; padding:5px 5px; background:transparent; color:inherit; cursor:pointer; display:grid; grid-template-columns:minmax(52px, 72px) auto minmax(62px, 1fr); align-items:center; gap:4px; text-align:left; font-size:11.5px; line-height:1.35; }
    .exit-rule-row:hover, .exit-rule-row.active { background:rgba(255,255,255,.06); }
    .exit-detail-card { position:fixed; z-index:40; border:1px solid rgba(148,163,184,.18); border-radius:12px; background:rgba(15,23,42,.96); backdrop-filter:blur(16px); padding:10px; display:grid; gap:8px; box-shadow:0 22px 58px rgba(0,0,0,.38), inset 0 1px 0 rgba(255,255,255,.06); }
    .exit-detail-head { display:flex; justify-content:space-between; align-items:flex-start; gap:10px; color:#e2e8f0; font-size:12px; font-weight:850; }
    .exit-detail-close { appearance:none; border:1px solid rgba(148,163,184,.18); background:rgba(2,6,23,.44); color:#94a3b8; border-radius:8px; padding:3px 7px; font-size:11px; line-height:1; }
    .exit-detail-list { display:grid; gap:7px; max-height:min(340px, calc(100vh - 180px)); overflow:auto; padding-right:2px; }
    .exit-detail-item { border:1px solid rgba(148,163,184,.10); border-radius:10px; background:rgba(2,6,23,.36); padding:8px 9px; display:grid; gap:6px; }
    .exit-detail-top { display:flex; justify-content:space-between; gap:10px; align-items:flex-start; font-size:12px; }
    .exit-detail-meta { display:flex; gap:8px; flex-wrap:wrap; color:#94a3b8; font-size:11px; line-height:1.35; }
    .exit-detail-reason { color:#94a3b8; font-size:11px; line-height:1.45; overflow-wrap:anywhere; }
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
      .settings-link, .refresh-pill, .visit-pill { padding:5px 7px; gap:5px; }
      .settings-link { font-size:12px; }
      .refresh-pill span, .visit-pill span { display:none; }
      .refresh-pill b, .visit-pill b { font-size:11px; }
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
      .market-detail-line { font-size:13.5px; line-height:1.58; }
      .market-detail-heading { font-size:12.5px; }
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
      .sector-cloud > div[style*="display:flex"] { flex-direction:column; gap:12px !important; }
      .sector-cloud div[style*="min-width:260px"], .sector-cloud div[style*="min-width:250px"] { min-width:0 !important; width:100%; }
      .sector-cloud .inline-field { padding:7px 8px; }
      .sector-cloud .inline-field .inline-value { font-size:13px; }
      .practice-stats { grid-template-columns:repeat(2,minmax(0,1fr)) !important; gap:7px !important; }
      .practice-perf-summary { grid-template-columns:repeat(3,minmax(0,1fr)); width:100%; }
      .practice-perf-grid { grid-template-columns:1fr; width:100%; }
      .practice-chart-card { padding:11px 10px 8px; border-radius:15px; }
      .practice-chart-head { flex-direction:column; gap:8px; }
      .practice-chart-title-row { width:100%; justify-content:space-between; }
      .practice-chart-kpis { justify-content:stretch; width:100%; }
      .practice-kpi { flex:1; min-width:0; text-align:left; padding:6px 7px; }
      .practice-chart-wrap { height:142px; }
      .practice-time-label { font-size:10px; bottom:2px; }
      .practice-axis-label.bot { bottom:29px; }
      .practice-zero-axis-label { font-size:9.5px; }
      .practice-current-line { bottom:34px; }
      .practice-curve { height:60px !important; }
      .position-metrics { grid-template-columns:repeat(2, minmax(0, 1fr)); gap:8px 10px; }
      .position-brief-grid { grid-template-columns:repeat(2, minmax(0, 1fr)); gap:7px; }
      .position-brief-card { padding:9px 10px; gap:7px; }
      .position-brief-name { font-size:13px; }
      .position-brief-item b { font-size:12.5px; }
      .market-strip { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:7px; margin:0 0 9px; }
      .indices-page { gap:11px; }
      .indices-switch { width:100%; }
      .indices-switch-btn { padding:8px 10px; font-size:13px; }
      .indices-part-title { font-size:16px; }
      .index-card { border-radius:13px; padding:8px 9px; min-width:0; box-shadow:none; }
      .index-name { font-size:11px; }
      .index-price { font-size:17px; }
      .index-change { font-size:11px; }
      .sparkline { height:34px; }
      .index-time { display:none; }
      .sector-grid { grid-template-columns:repeat(3, minmax(0, 1fr)); gap:6px; }
      .sector-item, .hot-item { padding:8px 7px; border-radius:12px; min-width:0; }
      .sector-name { font-size:10.5px; letter-spacing:-.01em; }
      .sector-pct { font-size:12.5px; margin-top:3px; }
      .hot-price { font-size:12.5px; margin-top:3px; }
      .flow-val { display:block; margin-left:0; margin-top:2px; font-size:10.5px; }
      .sector-pct .flow-val { display:block; }
    }
    @media (max-width: 390px) {
      .tab { font-size:12px; padding:7px 9px; }
      .content { font-size:14px; }
    }
    /* ---- rating table styles ---- */
    .rating-card { padding:0; overflow:visible; background:linear-gradient(180deg, rgba(17,24,39,.96), rgba(8,11,18,.96)); border-color:rgba(99,102,241,.22); width:fit-content; max-width:100%; }
    .rating-table-wrap { margin:0; border:1px solid rgba(148,163,184,.16); border-radius:16px; overflow:hidden; background:rgba(2,6,23,.30); width:min(100%, 920px); max-width:100%; }
    .rating-table-title { display:flex; justify-content:space-between; gap:10px; align-items:center; padding:9px 12px; color:#c7d2fe; font-weight:800; font-size:13px; border-bottom:1px solid rgba(148,163,184,.12); background:rgba(99,102,241,.10); }
    .rating-table-title small { color:#94a3b8; font-weight:500; font-size:11px; white-space:nowrap; }
    .rating-table { width:100%; table-layout:auto; border-collapse:collapse; font-size:15px; }
    .rating-table td,.rating-table th { padding:5px 9px; text-align:left; white-space:nowrap; border:none; line-height:1.2; }
    .rating-table th { color:#8da0b8; font-size:13px; letter-spacing:.03em; font-weight:750; background:rgba(15,23,42,.45); }
    .rating-table thead th:nth-child(1) { min-width:72px; }
    .rating-table thead th:nth-child(2) { min-width:92px; }
    .rating-table thead th:nth-child(3) { min-width:300px; }
    .rating-table thead th:nth-child(4) { min-width:88px; }
    .rating-action-inline { display:inline-block; color:#94a3b8; font-size:12px; line-height:1.1; font-weight:650; margin-right:6px; }
    .rating-table tr:last-child td { border-bottom:0; }
    .rating-table tbody tr.rating-data-row { cursor:pointer; transition:.14s ease; }
    .rating-table tbody tr.rating-data-row:hover { background:rgba(99,102,241,.12); }
    .rating-table tbody tr.rating-data-row.expanded { background:rgba(99,102,241,.16); }
    .rating-detail-row { display:none; }
    .rating-detail-row.open { display:table-row; }
    .rating-detail-cell { padding:0 !important; background:linear-gradient(180deg, rgba(99,102,241,.10), rgba(2,6,23,.34)); border-bottom:1px solid rgba(148,163,184,.16) !important; }
    .rating-inline-detail { padding:13px 14px 15px; display:grid; gap:10px; }
    .rating-inline-head { display:flex; justify-content:space-between; gap:10px; align-items:flex-start; color:#f8fafc; font-weight:850; }
    .rating-inline-sub { color:#94a3b8; font-size:12px; font-weight:500; margin-top:2px; }
    .rating-inline-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
    .inline-field { border:1px solid rgba(148,163,184,.12); border-radius:13px; padding:10px 11px; background:rgba(2,6,23,.36); }
    .inline-label { color:#8da0b8; font-size:11px; font-weight:800; letter-spacing:.05em; margin-bottom:5px; }
    .inline-value { color:#e5edf8; font-size:13.5px; line-height:1.7; white-space:pre-wrap; }
    .rating-table .ticker { color:#f8fafc; font-weight:850; letter-spacing:.01em; }
    .rating-table .price { color:#e0f2fe; font-weight:800; }
    .rating-table .target { color:#d1fae5; font-weight:800; }
    .rating-table .muted { color:#64748b; font-weight:500; }
    @media (max-width: 720px) {
      .rating-card { width:100%; max-width:100%; }
      .rating-table-wrap { margin:0; width:100%; background:transparent; border:0; }
      .rating-table-title { border:1px solid rgba(148,163,184,.14); border-radius:14px 14px 0 0; background:rgba(99,102,241,.12); }
      .rating-table-title small { display:block; }
      .rating-table { display:block; width:100%; }
      .rating-table thead { display:none; }
      .rating-table tbody { display:grid; width:100%; gap:7px; padding:7px 0 0; }
      .rating-table tr.rating-data-row { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:6px 9px; padding:10px 11px; border:1px solid rgba(148,163,184,.14); border-radius:14px; background:linear-gradient(135deg, rgba(15,23,42,.72), rgba(30,41,59,.42)); box-shadow:0 6px 18px rgba(0,0,0,.16); }
      .rating-table tr.rating-data-row.expanded { background:linear-gradient(135deg, rgba(99,102,241,.20), rgba(30,41,59,.48)); border-color:rgba(129,140,248,.36); }
      .rating-table th, .rating-table td { padding:0; white-space:normal; min-width:0; }
      .rating-table td { display:flex; flex-direction:column; gap:4px; align-items:flex-start; overflow-wrap:anywhere; }
      .rating-table td::before { content:attr(data-label); color:#8da0b8; font-size:11px; font-weight:800; letter-spacing:.05em; }
      .rating-table td:nth-child(1) { grid-column:1; grid-row:1; }
      .rating-table td:nth-child(2) { grid-column:2; grid-row:1; align-items:flex-end; }
      .rating-table td:nth-child(3) { grid-column:1 / -1; grid-row:2; }
      .rating-table td:nth-child(4) { grid-column:1 / -1; grid-row:3; align-items:flex-end; }
      .rating-detail-row.open { display:block; }
      .rating-detail-row.open .rating-detail-cell { display:block; width:100%; border-radius:14px; overflow:hidden; }
      .rating-inline-grid { grid-template-columns:1fr; gap:8px; }
      .rating-inline-detail { padding:11px 10px 12px; }
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
    <h1>牛牛大作手</h1>
    <div class="header-actions">
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
let usQuotesData = {items: {}, symbols: []};
let usQuotesLoadingKey = '';
let hotStocksData = {};
let moneyFlowData = {inflow: [], outflow: []};
let marketFlowData = {total_inflow_yi: null, total_outflow_yi: null, net_flow_yi: null};
let b1ScreenData = {items: [], count: 0};
let niuniuPracticeData = {positions: [], equity_history: [], trade_log: [], decision_log: [], cash: 1000000, total_equity: 1000000};
let practiceBenchmarksData = {items: []};
let benchmarkOverlay = {sh000001: true, sh000300: true, sz399006: true, sh000688: true};
const initialParams = new URLSearchParams(location.search);
let activeCategory = initialParams.get('category') || 'b1_screen';
let indicesViewMode = initialParams.get('panel') === 'market' ? 'market' : 'index';
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
let practiceExitRuleDetailKey = '';
window.practiceExitRuleDetailKey = practiceExitRuleDetailKey;
let practiceExitRulePopover = {top: 96, left: 16, width: 380, maxHeight: 420};
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
const fmtDurationSeconds = s => {
  const n = Number(s);
  if (!Number.isFinite(n)) return '--';
  return n >= 3600 ? (n/3600).toFixed(1)+'h' : n >= 60 ? (n/60).toFixed(0)+'m' : n.toFixed(0)+'s';
};
const upCls = v => v > 0 ? 'up' : v < 0 ? 'down' : 'flat';
const CATEGORY_ORDER = ['indices', 'b1_screen', 'x_monitor', 'market_monitor', 'us_ratings'];
const CATEGORY_LABELS = {all:'全部', indices:'指数行情', b1_screen:'牛牛实战', us_ratings:'美股机构买入评级', x_monitor:'推特监控', market_monitor:'盘面监控', other:'其他'};
const MESSAGE_CATEGORIES = ['x_monitor', 'market_monitor', 'us_ratings'];
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
      data, indicesData, sectorData, hotStocksData, moneyFlowData, marketFlowData,
      b1ScreenData, niuniuPracticeData, practiceBenchmarksData, usQuotesData,
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
    hotStocksData = cached.hotStocksData || hotStocksData;
    moneyFlowData = cached.moneyFlowData || moneyFlowData;
    marketFlowData = cached.marketFlowData || marketFlowData;
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
  const msgUrl = messagesUrl(isMessageCategory() ? messageOffset() : 0, isMessageCategory() ? messagePageLimit() : 1);
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
  try {
    const [idx, sec, hot, mf, mkf] = await Promise.all([
      fetch('/api/indices').then(r => r.ok ? r.json() : Promise.resolve({})),
      Promise.resolve(sectorData),
      fetch('/api/hot_stocks').then(r => r.ok ? r.json() : Promise.resolve({})),
      fetch('/api/money_flow').then(r => r.ok ? r.json() : Promise.resolve({})),
      fetch('/api/market_flow').then(r => r.ok ? r.json() : Promise.resolve({}))
    ]);
    indicesData = idx; sectorData = sec; hotStocksData = hot; moneyFlowData = mf; marketFlowData = mkf;
    if (activeCategory === 'market_monitor') render();
  } catch(e) {}
}
async function loadIndices() {
  try {
    const idxPromise = fetch('/api/indices').then(r => r.ok ? r.json() : {items: []});
    const secPromise = fetch('/api/sectors').then(r => r.ok ? r.json() : {sectors: []});
    const hotPromise = fetch('/api/hot_stocks').then(r => r.ok ? r.json() : {items: []});
    const mfPromise = fetch('/api/money_flow').then(r => r.ok ? r.json() : {inflow: [], outflow: []});
    const mkfPromise = fetch('/api/market_flow').then(r => r.ok ? r.json() : {total_inflow_yi: null});
    const idx = await idxPromise;
    indicesData = idx || {items: []};
    if (activeCategory === 'indices') render();
    saveViewState();
    const [sec, hot, mf, mkf] = await Promise.all([secPromise, hotPromise, mfPromise, mkfPromise]);
    sectorData = sec || sectorData || {sectors: []};
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
  $('categoryTabs').innerHTML = CATEGORY_ORDER.map(key => {
    const count = (key === 'indices' || key === 'b1_screen') ? '' : ` · ${data.categories?.[key]?.count || 0}`;
    return `<a class="tab ${activeCategory === key ? 'active' : ''}" data-category="${key}" href="/?category=${encodeURIComponent(key)}">${CATEGORY_LABELS[key]}${count}</a>`;
  }).join('');
  document.querySelectorAll('.tab[data-category]').forEach(tab => tab.onclick = (event) => {
    event.preventDefault();
    const nextCategory = tab.dataset.category;
    if (!nextCategory || nextCategory === activeCategory) return;
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
function placePracticeExitRulePopover(target) {
  const width = Math.min(440, Math.max(300, window.innerWidth - 24));
  const maxHeight = Math.max(220, Math.min(440, window.innerHeight - 24));
  if (!target || !target.getBoundingClientRect) {
    return {top: 72, left: Math.max(12, window.innerWidth - width - 12), width, maxHeight};
  }
  const rect = target.getBoundingClientRect();
  let left = Math.min(window.innerWidth - width - 12, Math.max(12, rect.right - width));
  let top = rect.bottom + 8;
  if (top + maxHeight > window.innerHeight - 12) {
    top = Math.max(12, rect.top - maxHeight - 8);
  }
  return {top: Math.round(top), left: Math.round(left), width: Math.round(width), maxHeight: Math.round(maxHeight)};
}
function setPracticeExitRuleDetail(ruleKey, event) {
  if (event && event.stopPropagation) event.stopPropagation();
  const normalizedKey = String(ruleKey || '');
  const nextKey = practiceExitRuleDetailKey === normalizedKey ? '' : normalizedKey;
  practiceExitRuleDetailKey = nextKey;
  if (nextKey) {
    practiceExitRulePopover = placePracticeExitRulePopover(event?.currentTarget);
  }
  window.practiceExitRuleDetailKey = practiceExitRuleDetailKey;
  if (activeCategory === 'b1_screen') render();
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
function renderPracticeCurve(history, dailyHistory, initialCash=1000000, benchmarks={items:[]}) {
  const isDailyMode = practiceCurveMode === 'daily';
  
  function normalizeEquityPoints(source) {
    return (source || [])
      .map(p => ({time: p.time || '', equity: Number(p.equity), pnlPct: Number(p.pnl_pct)}))
      .filter(p => Number.isFinite(p.equity) && p.time);
  }
  function compactDailyPoints(points) {
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
  let rawPoints = [];
  let dailyCompactedPoints = [];
  if (isDailyMode) {
    const compactedFromDaily = compactDailyPoints(normalizeEquityPoints(dailyHistory));
    const compactedFromIntraday = compactDailyPoints(normalizeEquityPoints(history));
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
    rawPoints = normalizeEquityPoints(history);
  }
  if (rawPoints.length < 2) return '<div class="empty" style="padding:18px">收益曲线等待更多净值点…</div>';
  const latestTradingClockPoint = [...rawPoints].reverse().find(p => tradingClockMinuteOfDay(p.time) != null);
  const latestDay = (latestTradingClockPoint || rawPoints[rawPoints.length - 1]).time.slice(0, 10);
  const w = 720, h = 210, left = 12, right = 58, top = 18, bottom = 24;
  const innerW = w - left - right, innerH = h - top - bottom;
  const totalSessionMinutes = 4 * 60; // 4小时 = 240分钟
  let points = [];
  let timeTicks = [];
  let xFromTime;
  let titleStr = '';
  
  if (isDailyMode) {
    points = dailyCompactedPoints;
    if (points.length < 2) return '<div class="empty" style="padding:18px">日收益等待更多交易日净值点…</div>';
    titleStr = `日收益 · 起始资金 ${fmtAmount(initialCash)}`;
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
    points = rawPoints.filter(p => p.time.slice(0, 10) === latestDay);
    if (points.length < 2) points = rawPoints.slice(-180);
    
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
    
    if (points.length > 80) {
      const step = Math.ceil(points.length / 60);
      points = points.filter((_, i, arr) => i === 0 || i === arr.length - 1 || i % step === 0);
    }
    
    titleStr = `固定盘面时间轴 09:30-15:00 · 基准线 ${fmtAmount(initialCash)}`;
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
  const chartBase = isDailyMode ? initialCash : vals[0];
  const chartPcts = vals.map((v, i) => {
    const base = isDailyMode ? (i > 0 ? vals[i - 1] : initialCash) : chartBase;
    return base ? (v / base - 1) * 100 : 0;
  });
  const chartDeltas = vals.map((v, i) => {
    const base = isDailyMode ? (i > 0 ? vals[i - 1] : initialCash) : chartBase;
    return v - (base || 0);
  });
  const start = vals[0], last = vals[vals.length - 1], prev = vals[Math.max(0, vals.length - 2)];
  // 收益曲线只展示牛牛账户本身，指数对照不再叠加，避免干扰账户收益率观察。
  const activeBenchmarks = [];
  const benchmarkSeries = activeBenchmarks.map((b, idx) => ({...b, color: b.symbol === 'sh000001' ? '#f59e0b' : b.symbol === 'sh000300' ? '#60a5fa' : b.symbol === 'sz399006' ? '#ec4899' : '#8b5cf6'}));
  
  // Y轴自适应：基于账户当前日波动区间，上下各留出约 15% 的呼吸空间
  const accountMinPct = Math.min(...chartPcts);
  const accountMaxPct = Math.max(...chartPcts);
  const accountRange = accountMaxPct - accountMinPct;
  const minVisibleRange = 1.0;  // 最小显示范围1%
  const yMidPct = (accountMaxPct + accountMinPct) / 2;
  const halfRange = Math.max(minVisibleRange / 2, accountRange * 0.6);
  
  let yMinPct = yMidPct - halfRange;
  let yMaxPct = yMidPct + halfRange;
  
  // 始终把 0% 盈亏平衡线纳入视野，避免只看到亏损/盈利区间时丢失基准。
  const zeroPaddingPct = 0.05;
  yMinPct = Math.min(yMinPct, -zeroPaddingPct);
  yMaxPct = Math.max(yMaxPct, zeroPaddingPct);
  
  const span = (yMaxPct - yMinPct) || 1;
  const y = pct => top + (yMaxPct - pct) / span * innerH;
  const clampPct = pct => Math.max(yMinPct, Math.min(yMaxPct, pct));
  const pts = points.map((p, i) => [xFromTime(p.time), y(chartPcts[i])]);
  
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
  const line = isDailyMode ? straightSvgPath(pts) : smoothSvgPath(pts);
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
  const maxDrawdown = (() => {
    let peak = vals[0], mdd = 0;
    for (const v of vals) { peak = Math.max(peak, v); mdd = Math.min(mdd, peak ? (v / peak - 1) * 100 : 0); }
    return mdd;
  })();
  const trendCls = totalPnl >= 0 ? 'up' : 'down';
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
  const chartTitle = isDailyMode ? '收益曲线 · 日收益' : '收益曲线 · 当日收益';
  const chartSub = isDailyMode
    ? `按交易日最后净值计算 · 0轴为前一交易日净值 · 最近点：${esc(lastTime)}`
    : `固定盘面时间轴 09:30-15:00 · 0轴为今日首个净值 · 最近点：${esc(lastTime)}`;
  const primaryKpiLabel = isDailyMode ? '最近日收益' : '当日收益';
  const modeButtons = `<div class="practice-mode-control" aria-label="收益曲线模式">
    <button class="practice-mode-btn ${!isDailyMode ? 'active' : ''}" type="button" onclick="setPracticeCurveMode('intraday')">当日收益</button>
    <button class="practice-mode-btn ${isDailyMode ? 'active' : ''}" type="button" onclick="setPracticeCurveMode('daily')">日收益</button>
  </div>`;
  return `<div class="practice-chart-card">
    <div class="practice-chart-head">
      <div>
        <div class="practice-chart-title-row">
          <div class="practice-chart-title">${chartTitle}</div>
          ${modeButtons}
        </div>
        <div class="practice-chart-sub">${chartSub}</div>
        <div class="benchmark-toggle-row"><button class="benchmark-toggle on" type="button" style="--dot:${markerColor}"><span class="benchmark-dot"></span>牛牛账户收益率</button></div>
      </div>
      <div class="practice-chart-kpis">
        <div class="practice-kpi"><div class="practice-kpi-label">${primaryKpiLabel}</div><div class="practice-kpi-value ${deltaCls}">${delta >= 0 ? '+' : ''}${fmtAmount(delta)} / ${deltaPct >= 0 ? '+' : ''}${fmtNumber(deltaPct)}%</div></div>
        <div class="practice-kpi"><div class="practice-kpi-label">累计收益</div><div class="practice-kpi-value ${trendCls}">${fmtAmount(totalPnl)} / ${fmtNumber(totalPct)}%</div></div>
        <div class="practice-kpi"><div class="practice-kpi-label">最大回撤</div><div class="practice-kpi-value down">${fmtNumber(maxDrawdown)}%</div></div>
      </div>
    </div>
    <div class="practice-chart-wrap">
      <span class="practice-axis-label top">${fmtNumber(yMaxPct)}%</span>
      ${showMidAxisLabel ? `<span class="practice-axis-label mid">${fmtNumber(midPct)}%</span>` : ''}
      <span class="practice-axis-label bot">${fmtNumber(yMinPct)}%</span>
      <span class="practice-zero-axis-label" style="top:${zeroAxisTopPct.toFixed(2)}%">0%</span>
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
        <line x1="${left}" x2="${w-right}" y1="${baseY.toFixed(1)}" y2="${baseY.toFixed(1)}" stroke="rgba(226,232,240,.46)" stroke-width="1.2" stroke-dasharray="7 5"/>
        <path d="${area}" fill="url(#practiceFill)"/>
        ${benchmarkPaths.map(b => `<path d="${b.d}" fill="none" stroke="${b.color}" stroke-width="1.5" opacity=".58" vector-effect="non-scaling-stroke"><title>${b.name} ${Number.isFinite(b.lastPct) ? fmtNumber(b.lastPct) + '%' : ''}</title></path>`).join('')}
        <path d="${line}" fill="none" stroke="${markerColor}" stroke-width="2.2" vector-effect="non-scaling-stroke" filter="url(#practiceGlow)"/>
      </svg>
      <span class="practice-current-line" style="left:${markerLeftPct.toFixed(2)}%"></span>
      <span class="practice-current-marker" style="left:${markerLeftPct.toFixed(2)}%;top:${markerTopPct.toFixed(2)}%;--marker-color:${markerColor};--marker-glow:${markerGlow}" title="当前 ${fmtAmount(last)}"></span>
      ${timeTickHtml}
    </div>
  </div>`;
}
function renderPracticePanel() {
  const p = niuniuPracticeData || {};
  const positions = p.positions || [];
  const soldStocks = p.today_sold_stocks || [];
  const showSoldStocks = practicePositionMode === 'sold';
  const totalEquity = Number(p.total_equity);
  const pnl = Number(p.total_pnl || 0);
  const pnlCls = pnl >= 0 ? 'up' : 'down';
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
    return `<div class="position-card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <span style="font-weight:700;font-size:16px;color:#f8fafc">${esc(x.code)} ${esc(x.name||'')}</span>
        <span style="font-size:13px;color:#94a3b8">${esc(x.qty)}股</span>
      </div>
      <div class="position-metrics">
        <div class="position-metric"><div class="position-label">成本/现价</div><div class="position-value combo">${costPriceText}</div></div>
        <div class="position-metric"><div class="position-label">盈亏</div><div class="position-value strong combo" style="color:${c}">${pnlText}</div></div>
        <div class="position-metric"><div class="position-label">实时涨幅</div><div class="position-value strong" style="color:${changeColor}">${changeText}</div></div>
        <div class="position-metric"><div class="position-label">最低涨幅</div><div class="position-value strong" style="color:${lowColor}">${lowText}</div></div>
        <div class="position-metric"><div class="position-label">最高涨幅</div><div class="position-value strong" style="color:${highColor}">${highText}</div></div>
        <div class="position-metric"><div class="position-label">今日收益</div><div class="position-value strong" style="color:${dayColor}">${todayText}</div></div>
        <div class="position-metric"><div class="position-label">市值</div><div class="position-value">${fmtAmount(x.market_value)}</div></div>
        <div class="position-metric"><div class="position-label">可卖/持有</div><div class="position-value" style="color:#94a3b8">${availableHoldText}</div></div>
      </div>
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
      ${x.reason ? `<div style="margin-top:8px;color:#94a3b8;font-size:12px;line-height:1.55">卖出理由：${esc(x.reason)}</div>` : ''}
    </div>`;
  }).join('') : '<div class="empty" style="padding:18px;font-size:13px">今日暂无卖出股票</div>';
  const stockCards = showSoldStocks ? soldCards : posCards;
  const stockCardsClass = !showSoldStocks && positions.length && practicePositionBriefMode ? 'position-brief-grid' : 'position-card-list';
  const decisions = (p.decision_log || []).slice(0,3).map(d => `<div style="font-size:12px;color:#94a3b8;margin-top:6px">${esc(d.time||'')}｜${esc(d.trade_reason||'')}｜${esc((d.decision||{}).summary||'')}</div>`).join('') || '<div class="muted" style="font-size:12px">暂无决策记录</div>';
  const quote = p.last_quote_refresh || {};
  const channels = quote.channel_counts || {};
  const channelText = quote.quote_time ? ` 腾讯${channels.tencent ?? 0}/东财${channels.eastmoney ?? 0}/Sina${channels.sina ?? 0}/单票${channels.single ?? 0}` : '';
  const quoteNote = quote.quote_time ? `｜行情：${esc(quote.quote_time)} 更新${quote.updated ?? 0}只${channelText}${quote.fallback ? `，回退${quote.fallback}只` : ''}` : '';
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
    ${renderStrategyPerformance(p.strategy_performance, p)}
    <div style="margin-top:10px;color:#94a3b8;font-size:12px">${esc(p.trade_rule_note||'A股模拟：100股整数倍、T+1；09:15-09:25只作开盘集合竞价观察，09:25-09:30不模拟成交。')}｜模型：${esc(p.decision_model || 'deepseek-v4-flash-free')}${quoteNote}</div>
    <div style="margin-top:8px">${decisions}</div>
    ${p.last_error ? `<div class="empty" style="color:#f87171;margin-top:10px">模型/交易错误：${esc(p.last_error)}</div>` : ''}
  </section>`;
}

function setIndicesViewMode(mode) {
  indicesViewMode = mode === 'market' ? 'market' : 'index';
  syncViewUrl();
  if (activeCategory === 'indices') render();
  saveViewState();
}

function renderStrategyPerformance(perf, portfolio) {
  if (!perf || !Object.keys(perf).length) return '';
  const BUY_COLORS = {
    trend_pullback: '#60a5fa',
    breakout: '#ec4899',
    shaofu_b1: '#f97316', b2_confirm: '#22c55e',
    b3_accelerate: '#a78bfa', super_b1: '#fb7185',
    balanced_momentum: '#facc15', legacy_b1: '#38bdf8',
    mixed: '#c084fc', unknown_buy: '#64748b',
    auto_exit: '#94a3b8', unknown: '#64748b'
  };
  const BUY_NAMES = {
    trend_pullback: '趋势回踩',
    breakout: '突破确认',
    shaofu_b1: '少妇B1', b2_confirm: 'B2确认',
    b3_accelerate: 'B3中继', super_b1: '超级B1',
    balanced_momentum: '中庸动量', legacy_b1: 'B1旧版',
    mixed: '混合买入', unknown_buy: '未识别买入',
    auto_exit: '系统退出', unknown: '其他'
  };
  const EXIT_COLORS = {
    stop_loss: '#fb7185', take_profit: '#f97316', profit_protection: '#facc15',
    top_escape: '#c084fc', technical_break: '#60a5fa', sell_score: '#22c55e',
    no_progress: '#94a3b8', position_adjust: '#38bdf8', model_sell: '#818cf8',
    other_exit: '#64748b'
  };
  const EXIT_NAMES = {
    stop_loss: '止损', take_profit: '主动止盈', profit_protection: '回撤保护',
    top_escape: '逃顶/出货', technical_break: '技术破位', sell_score: '卖出评分',
    no_progress: '信号未兑现', position_adjust: '仓位调整', model_sell: '模型卖出',
    other_exit: '其他卖出'
  };
  const retiredStrategies = new Set(['strict' + '_b1', 'goldi' + 'locks']);
  const renderBlock = (title, entries, names, colors, options = {}) => {
    const showOpen = !!options.showOpen;
    const activePerfEntries = Object.entries(entries || {})
      .filter(([k, v]) => !retiredStrategies.has(k) && v && typeof v === 'object')
      .filter(([, v]) => Number(v.trades || 0) || Number(v.open_trades || 0) || Number(v.wins || 0) || Number(v.losses || 0) || Number(v.flats || 0))
      .sort((a, b) => (Number(b[1].trades || 0) + Number(b[1].open_trades || 0)) - (Number(a[1].trades || 0) + Number(a[1].open_trades || 0)));
    if (!activePerfEntries.length) return '';
    const rows = activePerfEntries.map(([k,v]) => {
      const color = colors[k] || '#94a3b8';
      const name = names[k] || k;
      const winRate = Number(v.win_rate || 0);
      const winCls = winRate >= 50 ? 'up' : 'down';
      const flatText = Number(v.flats || 0) ? `/${v.flats || 0}平` : '';
      const openTrades = Number(v.open_trades || 0);
      const openPnl = Number(v.open_pnl || 0);
      const combinedPnl = Number(v.combined_pnl ?? (Number(v.total_pnl || 0) + openPnl));
      const openFlatText = Number(v.open_flats || 0) ? `/${v.open_flats || 0}平` : '';
      const openCls = openPnl >= 0 ? 'up' : 'down';
      const combinedCls = combinedPnl >= 0 ? 'up' : 'down';
      const openLine = showOpen && openTrades
        ? `<div style="margin-left:80px;color:#94a3b8;font-size:11px;line-height:1.35;display:flex;gap:8px;flex-wrap:wrap">
            <span>持仓 ${v.open_wins||0}浮盈/${v.open_losses||0}浮亏${openFlatText}</span>
            <span class="${openCls}">${openPnl >= 0 ? '+' : ''}${fmtAmount(openPnl)}</span>
            <span>合计 <span class="${combinedCls}">${combinedPnl >= 0 ? '+' : ''}${fmtAmount(combinedPnl)}</span></span>
          </div>`
        : '';
      return `<div style="font-size:12px;padding:3px 0;min-width:0">
        <div style="display:flex;align-items:center;gap:6px;min-width:0;flex-wrap:wrap">
          <span style="width:8px;height:8px;border-radius:2px;background:${color};flex-shrink:0"></span>
          <span style="color:#e2e8f0;min-width:66px;white-space:nowrap">${esc(name)}</span>
          <span style="color:#94a3b8;white-space:nowrap">已了结 ${v.wins||0}胜/${v.losses||0}负${flatText}</span>
          <span class="${winCls}" style="font-weight:600;margin-left:4px;white-space:nowrap">${fmtNumber(winRate)}%</span>
          <span style="color:#94a3b8;margin-left:4px;white-space:nowrap">均${(v.avg_pnl||0)>=0?'+':''}${fmtAmount(v.avg_pnl||0)}</span>
        </div>
        ${openLine}
      </div>`;
    }).join('');
		    return `<div class="practice-perf-block">
		      <div class="practice-perf-title">${esc(title)}</div>
		      ${rows}
		    </div>`;
	  };
  const renderExitAttributionBlock = (entries) => {
    const activeEntries = Object.entries(entries || {})
      .filter(([, v]) => v && typeof v === 'object' && Number(v.trades || v.trigger_count || 0))
      .sort((a, b) => Number(b[1].trades || b[1].trigger_count || 0) - Number(a[1].trades || a[1].trigger_count || 0));
    if (!activeEntries.length) return '';
    const activeKey = activeEntries.some(([k]) => k === practiceExitRuleDetailKey) ? practiceExitRuleDetailKey : '';
    const rows = activeEntries.map(([k, v]) => {
      const color = EXIT_COLORS[k] || '#94a3b8';
      const name = EXIT_NAMES[k] || k;
      const triggers = Number(v.trades || v.trigger_count || 0);
      const total = Number(v.total_pnl || 0);
      const avg = Number(v.avg_pnl || 0);
      const totalCls = total >= 0 ? 'up' : 'down';
      const isActive = activeKey === k;
      return `<button type="button" class="exit-rule-row ${isActive ? 'active' : ''}" onclick='setPracticeExitRuleDetail(${JSON.stringify(k)}, event)'>
        <span style="display:flex;align-items:center;gap:6px;min-width:0"><span style="width:8px;height:8px;border-radius:2px;background:${color};flex-shrink:0"></span><span style="color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(name)}</span></span>
        <span style="color:#94a3b8;white-space:nowrap">触发${triggers}次</span>
        <span style="text-align:right;white-space:nowrap"><span class="${totalCls}" style="font-weight:800">${total >= 0 ? '+' : ''}${fmtAmount(total)}</span><span style="color:#94a3b8;margin-left:5px">均${avg >= 0 ? '+' : ''}${fmtAmount(avg)}</span></span>
      </button>`;
    }).join('');
    const activeRow = activeKey ? entries[activeKey] : null;
    const activeName = activeKey ? (EXIT_NAMES[activeKey] || activeKey) : '';
    const detailItems = (activeRow?.items || []).map(item => {
      const pnl = Number(item.pnl || 0);
      const pnlPct = Number(item.pnl_pct);
      const pnlCls = pnl >= 0 ? 'up' : 'down';
      const pctText = Number.isFinite(pnlPct) ? ` / ${pnlPct >= 0 ? '+' : ''}${fmtNumber(pnlPct)}%` : '';
      const buyName = BUY_NAMES[item.buy_strategy] || item.buy_strategy || '未识别买入';
      return `<div class="exit-detail-item">
        <div class="exit-detail-top">
          <div style="min-width:0">
            <div style="color:#e2e8f0;font-weight:850;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(item.name || '')} <span style="color:#94a3b8;font-weight:700">${esc(item.code || '')}</span></div>
            <div class="exit-detail-meta">
              <span>${esc(item.time || '')}</span>
              <span>${esc(buyName)}</span>
              <span>${fmtNumber(item.shares, 0)}股 @ ${fmtNumber(item.price, 3)}</span>
            </div>
          </div>
          <div class="${pnlCls}" style="font-weight:900;white-space:nowrap">${pnl >= 0 ? '+' : ''}${fmtAmount(pnl)}${pctText}</div>
        </div>
        ${item.reason ? `<div class="exit-detail-reason">${esc(item.reason)}</div>` : ''}
      </div>`;
    }).join('');
    const pop = practiceExitRulePopover || {};
    const popTop = Number(pop.top || 72);
    const popLeft = Number(pop.left || 12);
    const popWidth = Number(pop.width || 380);
    const popMaxHeight = Number(pop.maxHeight || 420);
    const detail = activeRow ? `<div class="exit-detail-card" style="top:${popTop}px;left:${popLeft}px;width:${popWidth}px;max-height:${popMaxHeight}px">
      <div class="exit-detail-head">
        <div>${esc(activeName)} · 触发 ${Number(activeRow.trades || activeRow.trigger_count || 0)} 次</div>
        <button type="button" class="exit-detail-close" onclick="setPracticeExitRuleDetail('')">收起</button>
      </div>
      <div class="exit-detail-list">${detailItems || '<div class="empty" style="padding:14px;font-size:12px">暂无明细</div>'}</div>
    </div>` : '';
    return `<div class="practice-perf-block">
      <div class="practice-perf-title">卖出归因</div>
      ${rows}
      ${detail}
    </div>`;
  };
  const hasSplitPerf = perf.buy_strategy || perf.exit_rule;
  const buyBlock = renderBlock('买入战法绩效', hasSplitPerf ? perf.buy_strategy : perf, BUY_NAMES, BUY_COLORS, {showOpen: true});
  const exitBlock = hasSplitPerf ? renderExitAttributionBlock(perf.exit_rule) : '';
  const blocks = [buyBlock, exitBlock].filter(Boolean).join('');
  if (!blocks) return '';
  const closedPnl = Number((perf.summary || {}).total_pnl);
  const summaryOpenPnl = Number((perf.summary || {}).open_pnl);
  const fallbackOpenPnl = (portfolio?.positions || []).reduce((sum, x) => {
    const value = Number(x.pnl);
    return sum + (Number.isFinite(value) ? value : 0);
  }, 0);
  const openPnl = Number.isFinite(summaryOpenPnl) ? summaryOpenPnl : fallbackOpenPnl;
  const totalPnl = Number(portfolio?.total_pnl);
  const metric = (label, value) => {
    const cls = Number(value) >= 0 ? 'up' : 'down';
    return `<div class="inline-field"><div class="inline-label">${label}</div><div class="inline-value ${cls}">${Number(value) >= 0 ? '+' : ''}${fmtAmount(value)}</div></div>`;
  };
  const summary = (Number.isFinite(closedPnl) || Number.isFinite(openPnl) || Number.isFinite(totalPnl))
    ? `<div class="practice-perf-summary">
        ${Number.isFinite(totalPnl) ? metric('总收益', totalPnl) : ''}
        ${Number.isFinite(closedPnl) ? metric('已实现', closedPnl) : ''}
        ${Number.isFinite(openPnl) ? metric('持仓浮动', openPnl) : ''}
      </div>`
    : '';
  return `${summary}<div class="practice-perf-grid">${blocks}</div>`;
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
  function renderSessionMarketGroups() {
    const aOpen = isAShareOpenNow();
    const usOpen = isUsOpenNow();
    const aIndexItems = marketItems('a_index', 'domestic');
    const aDaySession = isAShareDaySessionNow() && aIndexItems.length;
    const showAIndexBeforeUsOpen = aIndexItems.length && !usOpen;
    const sections = (aOpen || aDaySession || showAIndexBeforeUsOpen) ? [
      ['A股指数', aIndexItems],
      ['A股期货', marketItems('a_futures')],
      ['美股期货', marketItems('us_futures')],
      ['大宗商品', marketItems('commodity', 'commodity')],
    ] : usOpen ? [
      ['美股指数', marketItems('us_index', 'global')],
      ['A股期货', marketItems('a_futures')],
      ['大宗商品', marketItems('commodity', 'commodity')],
    ] : [
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
    cloudHtml = `<div class="sector-cloud"><h3>板块涨跌幅</h3><div style="display:flex;gap:16px;flex-wrap:wrap"><div style="flex:1;min-width:260px">${renderSectorMoveBlock('涨幅前十', gainTop, true)}</div><div style="flex:1;min-width:260px">${renderSectorMoveBlock('跌幅前十', lossTop, false)}</div></div></div>`;
  }
  const indexHtml = renderSessionMarketGroups();
  const marketFlowHtml = renderMarketFlowBlock();
  const marketHtml = `${cloudHtml}${hotHtml}${marketFlowHtml}${mfHtml}`;
  const marketModuleCount = [cloudHtml, hotHtml, marketFlowHtml, mfHtml].filter(Boolean).length;
  const hasMarketPayload =
    ['gain_top', 'loss_top', 'sectors', 'items'].some(key => Array.isArray(sec[key])) ||
    ['amount_top', 'turnover_top', 'volume_top', 'items'].some(key => Array.isArray(hot[key])) ||
    ['inflow', 'outflow'].some(key => Array.isArray(mf[key]));
  const activePanel = indicesViewMode === 'market' ? 'market' : 'index';
  const activeTitle = activePanel === 'market' ? '行情' : '指数';
  const activeMeta = activePanel === 'market' ? `${marketModuleCount || 0} 组` : `${items.length} 项`;
  const activeHtml = activePanel === 'market'
    ? (marketHtml || `<div class="empty" style="padding:18px">${hasMarketPayload ? '暂无行情数据' : '行情加载中...'}</div>`)
    : (indexHtml || '<div class="empty" style="padding:18px">暂无指数数据</div>');
  return `${errorHtml}<div class="indices-page">
    <div class="indices-switch" role="group" aria-label="指数行情切换">
      <button type="button" class="indices-switch-btn ${activePanel === 'index' ? 'active' : ''}" aria-pressed="${activePanel === 'index' ? 'true' : 'false'}" onclick="setIndicesViewMode('index')">指数</button>
      <button type="button" class="indices-switch-btn ${activePanel === 'market' ? 'active' : ''}" aria-pressed="${activePanel === 'market' ? 'true' : 'false'}" onclick="setIndicesViewMode('market')">行情</button>
    </div>
    <section class="indices-part" id="${activePanel === 'market' ? 'market-overview' : 'indices-overview'}">
      <div class="indices-part-head"><h2 class="indices-part-title">${activeTitle}</h2><div class="indices-part-meta">${activeMeta}</div></div>
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
    const STRATEGY_META = {
      trend_pullback: {label:'趋势回踩',  color:'#60a5fa'},
      breakout:       {label:'突破确认',  color:'#ec4899'},
      shaofu_b1:      {label:'少妇B1',    color:'#f97316'},
      b2_confirm:     {label:'B2确认',    color:'#22c55e'},
      b3_accelerate:  {label:'B3中继',    color:'#a78bfa'},
      super_b1:       {label:'超级B1',    color:'#fb7185'},
    };
    const tierCounts = {high:0, mid:0, low:0};
    for (const item of items) {
      const s = item.best_score || item.score || 0;
      const threshold = Number(item.entry_threshold || 8);
      if (s >= threshold) tierCounts.high++;
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
      const stratName = item.best_strategy || '';
      const sm = STRATEGY_META[stratName] || {label:stratName||'综合', color:'#94a3b8'};
      let groupBadge = '';
      const finalScore = item.best_score || item.score || 0;
      const entryThreshold = Number(item.entry_threshold || 8);
      const scoreBasis = item.score_basis || '';
      const tradeDiscipline = [item.position_hint, item.time_stop].filter(Boolean).join(' · ');
      if (finalScore >= entryThreshold) groupBadge = '<span style="background:rgba(52,211,153,.15);color:#34d399;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600">达标</span>';
      else if (finalScore >= entryThreshold - 1.5) groupBadge = '<span style="background:rgba(251,191,36,.15);color:#fbbf24;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600">等确认</span>';
      else groupBadge = '<span style="background:rgba(148,163,184,.12);color:#94a3b8;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600">仅观察</span>';
      html += `<div style="background:rgba(16,19,26,.86);border:1px solid var(--line);border-radius:18px;padding:16px;box-shadow:0 10px 36px rgba(0,0,0,.18)">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:10px">
          <div><span style="font-weight:780;font-size:17px;color:#f8fafc">${esc(item.code)} ${esc(item.name)}</span>
            <span style="display:inline-block;margin-left:8px;padding:2px 8px;border-radius:999px;background:${sm.color}22;color:${sm.color};font-size:12px;border:1px solid ${sm.color}44">${esc(sm.label)}</span>
            ${item.industry ? `<span style="display:inline-block;margin-left:6px;padding:2px 8px;border-radius:999px;background:rgba(124,92,255,.15);color:#c4b5fd;font-size:12px">${esc(item.industry)}</span>` : ''}
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
function inlineField(label, value) {
  if (!value) return '';
  return `<div class="inline-field"><div class="inline-label">${esc(label)}</div><div class="inline-value">${renderMarkdown(value)}</div></div>`;
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
        <div class="rating-inline-head"><div>${esc(ticker)}${company ? ' · ' + esc(company) : ''}<div class="rating-inline-sub">${esc(item.action || '')}</div></div><span class="muted">再次点击收起</span></div>
        <div class="rating-inline-grid">
          ${inlineField('机构 / 分析师', item.analyst)}
          ${inlineField('适合关注类型', item.type)}
          ${inlineField('看多逻辑 / 催化剂', item.reason)}
          ${inlineField('风险点', item.risk)}
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
function renderMarketDetail(content) {
  const lines = String(content || '').split('\n');
  const html = lines.map(raw => {
    const line = String(raw || '').trim();
    if (!line) return '';
    const clean = cleanMarketLine(line);
    const heading = /\*\*.+\*\*/.test(line) || /^[📊🔥💰⚡📈💡⚠️🌡️📌👀ℹ️]/u.test(clean);
    if (heading) return `<div class="market-detail-heading">${esc(clean)}</div>`;
    const cls = /^[·•]/.test(clean) || /^流[入出]/.test(clean) || /^数据为/.test(clean) ? ' market-detail-note' : '';
    return `<div class="market-detail-line${cls}">${esc(clean)}</div>`;
  }).filter(Boolean).join('');
  return `<div class="market-detail-box">${html}</div>`;
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
  if (!records.length) return '<div class="empty">暂无盘面监控消息</div>';
  const groups = groupMarketRecordsByDay(records);
  const days = [...groups.keys()].sort().reverse();
  if (!days.length) return '<div class="empty">暂无盘面监控消息</div>';
  if (marketDayIndex >= days.length) marketDayIndex = 0;
  const day = days[marketDayIndex] || days[0];
  const dayRecords = groups.get(day) || [];
  return `<div class="market-monitor-grid">${dayRecords.map(r => renderMarketMonitorCard(r)).join('')}</div>${renderMarketDayPager(records, days, day, dayRecords)}`;
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
  if (practiceExitRuleDetailKey && activeCategory === 'b1_screen') {
    if (event.target.closest('.exit-detail-card') || event.target.closest('.exit-rule-row')) return;
    setPracticeExitRuleDetail('');
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
  if (event.key === 'Escape' && practiceExitRuleDetailKey) {
    event.preventDefault();
    setPracticeExitRuleDetail('');
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


def render_login_page(error: str = '') -> bytes:
    err = f'<div class="error">{html.escape(error)}</div>' if error else ''
    return LOGIN_HTML.replace('__ERROR__', err).encode('utf-8')


def render_admin_password_page(error: str = '') -> bytes:
    err = f'<div class="error">{html.escape(error)}</div>' if error else ''
    return ADMIN_PASSWORD_HTML.replace('__ERROR__', err).encode('utf-8')


def admin_password_enabled() -> bool:
    return bool(ADMIN_PASSWORD)


def admin_session_value() -> str:
    if not ADMIN_PASSWORD:
        return ""
    secret = get_or_create_admin_token()
    digest = hmac.new(secret.encode("utf-8"), ADMIN_PASSWORD.encode("utf-8"), hashlib.sha256).hexdigest()
    return "ad_" + digest


def verify_admin_password(password: str) -> bool:
    return bool(ADMIN_PASSWORD) and secrets.compare_digest(str(password or ""), ADMIN_PASSWORD)


def validate_admin_session(cookie_value: str) -> bool:
    if not admin_password_enabled():
        return True
    return bool(cookie_value) and secrets.compare_digest(cookie_value, admin_session_value())


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
        return (
            f"<select name='env__{escaped_name}'>"
            f"<option value='' {'selected' if current == '' else ''}>默认</option>"
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
            f"<div class='time-list-control' data-time-list data-field-name='{field_name}'>"
            f"<input type='hidden' name='{field_name}' value=''>"
            "<div class='time-list-grid' data-time-list-items>"
            + "".join(inputs)
            + "</div><button type='button' class='time-list-add' data-time-list-add "
            "aria-label='添加时间点' title='添加时间点'>+</button></div>"
            "<div class='config-meta'>北京时间</div>"
        )
    input_type = "number" if kind == "int" else "text"
    return f"<input type='{input_type}' name='env__{escaped_name}' value='{html.escape(value)}'>"


def render_env_config_table(payload: dict[str, Any]) -> str:
    groups: list[dict[str, Any]] = []
    current_group: dict[str, Any] | None = None
    for item in payload["items"]:
        group = str(item.get("group") or "其他")
        if current_group is None or current_group["name"] != group:
            current_group = {"name": group, "items": []}
            groups.append(current_group)
        current_group["items"].append(item)

    sections: list[str] = []
    for group in groups:
        group_name = str(group["name"])
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
            rows.append(
                "<div class='setting-row'>"
                f"<div class='setting-copy'><div class='config-label'>{html.escape(label)}</div></div>"
                f"<div class='setting-editor'>{render_env_input(item)}</div>"
                "<div class='setting-state'>"
                f"<div class='setting-state-item'><div class='setting-state-label'>当前</div><div class='config-meta'>{current_html}</div></div>"
                f"<div class='setting-state-item'><div class='setting-state-label'>默认</div><div class='config-meta'>{default_html}</div></div>"
                "</div>"
                "</div>"
            )
        sections.append(
            "<section class='settings-group'>"
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
        + "".join(sections)
        + "<div class='settings-actions'><button class='save-button' type='submit'>保存业务配置</button></div>"
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

    def query_token(self) -> str:
        parsed = urlparse(self.path)
        return parse_qs(parsed.query).get("token", [""])[0].strip()

    def request_token(self) -> str:
        token = self.query_token()
        if token:
            return token
        return parse_request_cookies(self.headers.get("Cookie")).get(AUTH_COOKIE_NAME, "")

    def request_visitor_id(self) -> tuple[str, bool]:
        visitor_id = parse_request_cookies(self.headers.get("Cookie")).get(VISITOR_COOKIE_NAME, "").strip()
        if re.fullmatch(r"nvst_[A-Za-z0-9_-]{20,80}", visitor_id or ""):
            return visitor_id, False
        return "nvst_" + secrets.token_urlsafe(24), True

    def current_user(self) -> dict[str, Any] | None:
        if not AUTH_ENABLED:
            return {"role": "admin", "nickname": "auth-disabled"}
        return authenticate_viewer_token(self.request_token(), ip=self.client_ip(), user_agent=self.headers.get("User-Agent", ""))

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
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.write_response(render_login_page("访问过于频繁，请稍后再试"))

    def enforce_rate_limit(self, scope: str, key: str, limit: int) -> bool:
        ok, retry_after = check_rate_limit(scope, key, limit)
        if not ok:
            self.send_rate_limited(retry_after)
            return False
        return True

    def cookie_flags(self) -> str:
        secure = "; Secure" if self.is_secure_request() else ""
        return f"Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax{secure}"

    def visitor_cookie_flags(self) -> str:
        secure = "; Secure" if self.is_secure_request() else ""
        return f"Path=/; Max-Age=31536000; SameSite=Lax{secure}"

    def admin_session_cookie_flags(self) -> str:
        secure = "; Secure" if self.is_secure_request() else ""
        return f"Path=/; Max-Age=86400; HttpOnly; SameSite=Lax{secure}"

    def admin_password_session_valid(self) -> bool:
        cookie_value = parse_request_cookies(self.headers.get("Cookie")).get(ADMIN_PASSWORD_COOKIE_NAME, "")
        return validate_admin_session(cookie_value)

    def send_html(self, payload: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_response(payload)

    def redirect(
        self,
        location: str,
        *,
        set_cookie: str | None = None,
        clear_cookie: bool = False,
        set_admin_cookie: str | None = None,
        clear_admin_cookie: bool = False,
    ) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", f"{AUTH_COOKIE_NAME}={set_cookie}; {self.cookie_flags()}")
        if clear_cookie:
            secure = "; Secure" if self.is_secure_request() else ""
            self.send_header("Set-Cookie", f"{AUTH_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax{secure}")
        if set_admin_cookie:
            self.send_header("Set-Cookie", f"{ADMIN_PASSWORD_COOKIE_NAME}={set_admin_cookie}; {self.admin_session_cookie_flags()}")
        if clear_admin_cookie:
            secure = "; Secure" if self.is_secure_request() else ""
            self.send_header("Set-Cookie", f"{ADMIN_PASSWORD_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax{secure}")
        self.end_headers()

    def send_auth_required(self) -> bool:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.send_response(401)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.write_response(json.dumps({"error": "auth_required"}, ensure_ascii=False).encode("utf-8"))
        else:
            self.redirect("/login")
        return False

    def send_admin_password_required(self) -> bool:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.send_response(403)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.write_response(json.dumps({"error": "admin_password_required"}, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_html(render_admin_password_page())
        return False

    def require_user(self) -> dict[str, Any] | None:
        user = self.current_user()
        if not user:
            self.send_auth_required()
            return None
        return user

    def require_admin_identity(self) -> dict[str, Any] | None:
        user = self.require_user()
        if not user:
            return None
        if user.get("role") != "admin":
            self.send_response(403)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.write_response(json.dumps({"error": "admin_required"}, ensure_ascii=False).encode("utf-8"))
            return None
        return user

    def require_admin(self) -> dict[str, Any] | None:
        user = self.require_admin_identity()
        if not user:
            return None
        if not self.admin_password_session_valid():
            self.send_admin_password_required()
            return None
        return user

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
            if schema.get("kind") == "time_list":
                result[key] = ",".join(v.strip() for v in values if v.strip())
            else:
                result[key] = values[-1] if values else ""
        return result

    def send_payload(self, payload: bytes, *, content_type: str = "application/json; charset=utf-8",
                     edge_ttl: int = 10, browser_ttl: int = 3, cache_hit: bool | None = None) -> None:
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
        self.end_headers()
        self.write_response(payload)

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
        if parsed.path == "/login":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if parsed.path == "/":
            if self.current_user():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "private, max-age=30, stale-while-revalidate=300")
                self.send_header("CDN-Cache-Control", "no-store")
                self.end_headers()
            else:
                self.redirect("/login")
            return
        if parsed.path.startswith("/api/"):
            if self.current_user():
                self.send_response(200)
            else:
                self.send_response(401)
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
        query_token = self.query_token()
        if query_token and not parsed.path.startswith("/api/"):
            user = authenticate_viewer_token(query_token, ip=self.client_ip(), user_agent=self.headers.get("User-Agent", ""))
            if user:
                self.redirect(remove_query_param(self.path, "token"), set_cookie=query_token)
                return
        if parsed.path == "/login":
            if self.current_user():
                self.redirect("/")
            else:
                self.send_html(render_login_page())
            return
        if parsed.path == "/logout":
            self.redirect("/login", clear_cookie=True, clear_admin_cookie=True)
            return
        if parsed.path == "/admin":
            if not self.require_admin():
                return
            self.send_html(render_admin_page(parse_qs(parsed.query)))
            return
        if parsed.path == "/api/auth/status":
            user = self.current_user()
            self.send_json_uncached({"authenticated": bool(user), "user": user and {"nickname": user.get("nickname"), "role": user.get("role")}, "online": count_online_viewers()})
            return
        if parsed.path == "/api/admin/invites":
            if not self.require_admin():
                return
            self.send_json_uncached({"items": list_invite_codes()})
            return
        if parsed.path == "/api/admin/viewers":
            if not self.require_admin():
                return
            self.send_json_uncached({"items": list_viewers(), "online": count_online_viewers()})
            return
        if parsed.path == "/api/admin/config":
            if not self.require_admin():
                return
            self.send_json_uncached(build_admin_config_payload())
            return
        if parsed.path == "/":
            if not self.require_user():
                return
            visitor_id, new_visitor = self.request_visitor_id()
            visit_stats = increment_visit_count(visitor_id)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("CDN-Cache-Control", "no-store")
            if new_visitor:
                self.send_header("Set-Cookie", f"{VISITOR_COOKIE_NAME}={visitor_id}; {self.visitor_cookie_flags()}")
            self.end_headers()
            page = INDEX_HTML.replace("__VISIT_COUNT__", f"{visit_stats['visits']:,}")
            page = page.replace("__UNIQUE_VISIT_COUNT__", f"{visit_stats['unique']:,}")
            self.write_response(page.encode("utf-8"))
            return
        if parsed.path.startswith("/api/"):
            user = self.require_user()
            if not user:
                return
            token_key = hash_token(self.request_token())[:16] if self.request_token() else self.client_ip()
            if not self.enforce_rate_limit("auth", token_key, RATE_LIMIT_AUTH):
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
                self.send_json_cached("b1_screen", API_TTLS["b1_screen"], load_b1_cache, edge_ttl=API_TTLS["b1_screen"], browser_ttl=3)
            return
        if parsed.path == "/api/b1_screen/trigger":
            self.send_method_not_allowed("POST")
            return
        if parsed.path == "/api/niuniu_practice":
            params = parse_qs(parsed.query)
            fast = params.get("fast", ["0"])[0].lower() in {"1", "true", "yes"}
            if fast:
                self.send_json_cached("niuniu_practice_fast", API_TTLS["niuniu_practice"], get_practice_payload_fast, edge_ttl=API_TTLS["niuniu_practice"], browser_ttl=3)
            else:
                self.send_json_cached("niuniu_practice", API_TTLS["niuniu_practice"], get_practice_payload, edge_ttl=API_TTLS["niuniu_practice"], browser_ttl=3)
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
            def produce_indices():
                try:
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("indices",
                        os.path.join(os.path.dirname(__file__), "indices_dashboard_api.py"))
                    if spec and spec.loader:
                        indices_mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(indices_mod)
                        raw_result = indices_mod.fetch_indices_data()
                        return raw_result if isinstance(raw_result, dict) else {"items": raw_result}
                    return {"items": []}
                except Exception as exc:
                    return {"items": [], "error": str(exc)}
            self.send_json_cached("indices", API_TTLS["indices"], produce_indices, edge_ttl=API_TTLS["indices"], browser_ttl=5)
            return
        if parsed.path == "/api/sectors":
            self.send_json_cached("sectors", API_TTLS["sectors"], lambda: run_dashboard_helper("sectors_dashboard_api.py", {"sectors": [], "items": [], "gain_top": [], "loss_top": []}, timeout=120), edge_ttl=API_TTLS["sectors"], browser_ttl=15)
            return
        if parsed.path == "/api/hot_stocks":
            self.send_json_cached("hot_stocks", API_TTLS["hot_stocks"], lambda: run_dashboard_helper("hot_stocks_dashboard_api.py", {"items": [], "amount_top": [], "turnover_top": [], "volume_top": []}, timeout=120), edge_ttl=API_TTLS["hot_stocks"], browser_ttl=15)
            return
        if parsed.path == "/api/us_quotes":
            params = parse_qs(parsed.query)
            symbols = sanitize_symbols(params.get("symbols", [""])[0])
            cache_key = "us_quotes:" + ",".join(symbols)
            self.send_json_cached(cache_key, API_TTLS["us_quotes"], lambda: fetch_us_quotes(symbols), edge_ttl=API_TTLS["us_quotes"], browser_ttl=10)
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
        if parsed.path == "/login":
            if not self.enforce_rate_limit("login", self.client_ip(), RATE_LIMIT_LOGIN):
                return
            try:
                form = self.read_form()
            except RequestTooLarge:
                self.send_response(413)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.write_response(render_login_page("请求过大，请重新提交"))
                return
            result = redeem_invite_code(form.get("code", ""), nickname=form.get("nickname", ""), ip=self.client_ip(), user_agent=self.headers.get("User-Agent", ""))
            if result.get("ok"):
                self.redirect("/", set_cookie=result["token"])
            else:
                self.send_html(render_login_page(result.get("error", "邀请码无效")), status=403)
            return
        if parsed.path == "/admin/password":
            if not self.enforce_rate_limit("admin", self.client_ip(), RATE_LIMIT_ADMIN):
                return
            user = self.require_admin_identity()
            if not user:
                return
            try:
                form = self.read_form()
            except RequestTooLarge:
                self.send_html(render_admin_password_page("请求过大，请重新提交"), status=413)
                return
            if verify_admin_password(form.get("admin_password", "")):
                self.redirect("/admin", set_admin_cookie=admin_session_value())
            else:
                self.send_html(render_admin_password_page("管理员密码错误"), status=403)
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
                updates = {
                    key[len("env__"):]: value
                    for key, value in form.items()
                    if key.startswith("env__") and key[len("env__"):] in ADMIN_VISIBLE_ENV_NAMES
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
        if parsed.path in {"/admin/invite/create", "/api/admin/invite/create"}:
            if not self.require_admin():
                return
            if not self.enforce_rate_limit("admin", self.client_ip(), RATE_LIMIT_ADMIN):
                return
            try:
                form = self.read_form()
            except RequestTooLarge:
                self.send_json_uncached({"ok": False, "error": "request_too_large"})
                return
            try:
                invite = create_invite_code(code=form.get("code") or None, max_uses=int(form.get("max_uses") or 1), ttl_hours=int(form.get("ttl_hours") or 168), note=form.get("note", ""))
            except Exception as exc:
                if parsed.path.startswith("/api/"):
                    self.send_json_uncached({"ok": False, "error": str(exc)})
                else:
                    self.redirect("/admin")
                return
            if parsed.path.startswith("/api/"):
                self.send_json_uncached({"ok": True, "invite": invite})
            else:
                self.redirect("/admin")
            return
        if parsed.path == "/admin/invite/toggle":
            if not self.require_admin():
                return
            if not self.enforce_rate_limit("admin", self.client_ip(), RATE_LIMIT_ADMIN):
                return
            try:
                form = self.read_form()
            except RequestTooLarge:
                self.redirect("/admin")
                return
            set_invite_disabled(form.get("code", ""), form.get("disabled", "1") in {"1", "true", "yes"})
            self.redirect("/admin")
            return
        if parsed.path == "/admin/viewer/toggle":
            if not self.require_admin():
                return
            if not self.enforce_rate_limit("admin", self.client_ip(), RATE_LIMIT_ADMIN):
                return
            try:
                form = self.read_form()
            except RequestTooLarge:
                self.redirect("/admin")
                return
            set_viewer_disabled(form.get("token_hash", ""), form.get("disabled", "1") in {"1", "true", "yes"})
            self.redirect("/admin")
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
US_QUOTE_SYMBOL_MAP: dict[str, list[str]] = {}  # populated from config or known list


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
    return result


def _safe_float(v: str) -> float | None:
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


def main() -> None:
    ensure_auth_db()
    get_or_create_admin_token()
    parser = argparse.ArgumentParser(description="NiuOne dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    server = ReusableThreadingHTTPServer((args.host, args.port), Handler)
    start_b1_scheduler()
    start_pending_decision_executor()
    print(f"牛牛大作手：http://{args.host}:{args.port}")
    print(f"用户管理：admin token saved at {ADMIN_TOKEN_FILE}; open /admin?token=<token-from-file>")
    print(f"消息历史：{push_history.DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
