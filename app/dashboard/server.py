#!/usr/bin/env python3
"""NiuOne dashboard for messages, models, and trading signals."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import shlex
import time
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import urllib.request

from a_share_calendar import is_a_share_trading_day as calendar_is_a_share_trading_day, trading_day_status
from dashboard_json_cache import read_json_cache, write_json_cache
from dashboard import practice_payload as practice_payload_impl
from dashboard import practice_market_summary as practice_market_summary_impl
from dashboard import response_cache as response_cache_impl
from dashboard import security as security_impl
from dashboard import visit_stats as visit_stats_impl
from dashboard.iwencai_connectivity import (
    IWENCAI_TEST_FIELD_NAMES,
    iwencai_test_metadata,
    test_iwencai_connection,
)
from dashboard.model_connectivity import (
    MODEL_TEST_TARGET_BY_ID,
    model_test_metadata,
    model_test_override_names,
    model_test_setting_names,
    test_model_connection,
)
from dashboard.apis.iwencai_service import (
    DEFAULT_LIMIT as IWENCAI_DRAGON_TIGER_DEFAULT_LIMIT,
    dragon_tiger_archive_path,
    fetch_dragon_tiger,
    normalize_limit as normalize_iwencai_limit,
    normalize_page as normalize_iwencai_page,
    normalize_trade_date as normalize_iwencai_trade_date,
    read_dragon_tiger_archive,
    read_dragon_tiger_snapshot,
    write_dragon_tiger_archive,
    write_dragon_tiger_snapshot,
)
from dashboard.apis.industry_flow import (
    DEFAULT_PLAYBACK_SPEED as INDUSTRY_FLOW_DEFAULT_PLAYBACK_SPEED,
    DEFAULT_SAMPLE_INTERVAL_SECONDS as INDUSTRY_FLOW_DEFAULT_SAMPLE_INTERVAL_SECONDS,
    DEFAULT_SIDE_LIMIT as INDUSTRY_FLOW_DEFAULT_SIDE_LIMIT,
    SAMPLING_WINDOWS as INDUSTRY_FLOW_DEFAULT_SAMPLING_WINDOWS,
    append_industry_flow_sample,
    build_industry_flow_payload,
    is_industry_flow_session_timestamp,
    normalize_industry_flow_sampling_windows,
)
from dashboard.apis.market_breadth import (
    DEFAULT_SAMPLE_INTERVAL_SECONDS as MARKET_BREADTH_DEFAULT_SAMPLE_INTERVAL_SECONDS,
    append_market_breadth_sample,
    build_market_breadth_payload,
    compact_market_breadth_sample,
    compact_previous_turnover_history,
    is_market_breadth_session_timestamp,
    roll_market_breadth_history,
)
from market_data.iwencai_client import (
    DEFAULT_BASE_URL as IWENCAI_DEFAULT_BASE_URL,
    normalize_base_url as normalize_iwencai_base_url,
)
from market_data.tencent_market_breadth import fetch_tencent_market_breadth
from niuone_paths import apply_container_runtime_overrides, get_dashboard_env_file, get_dashboard_home, get_local_data_dir
import push_history
from screening.stock_universe import (
    DEFAULT_STOCK_UNIVERSE,
    STOCK_UNIVERSE_ENV,
    STOCK_UNIVERSE_OPTIONS,
    friendly_stock_universe,
    normalize_stock_universe,
    selected_stock_universe,
)
from strategies.registry import (
    ACTIVE_STRATEGY_ENV,
    PERSONA_STRATEGY_ENV,
    PRESET_STRATEGY_TEXT_ENV,
    PRESET_STRATEGY_TEXT_MAX_CHARS,
    TRADE_DISCIPLINE_TEXT_ENV,
    TRADE_DISCIPLINE_TEXT_MAX_CHARS,
    STRATEGY_SOURCE_BUILTIN,
    STRATEGY_SOURCE_ENV,
    STRATEGY_SOURCE_OPTIONS,
    active_strategy_suite,
    decode_preset_strategy_text,
    decode_trade_discipline_text,
    default_trade_discipline_text,
    default_enabled_persona_strategies_value,
    normalize_preset_strategy_text_update,
    normalize_trade_discipline_text_update,
    normalize_strategy_source_update,
    normalize_strategy_list_update,
    normalize_strategy_suite_update,
    strategy_suite_options,
    strategy_settings_options,
)
from us_market_summary import fetch_us_market_summary, fetch_us_sector_snapshot, load_cached_summary_for_today

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
ENTRYPOINT_DIR = SCRIPT_DIR / "entrypoints"
COMPAT_DIR = SCRIPT_DIR / "compat"
VERSION_PATTERN = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
CURRENT_VERSION = str(os.environ.get("NIUONE_VERSION") or "dev").strip() or "dev"
DOCKER_HUB_REPOSITORY = "kunkundi/niuone"
DOCKER_HUB_REPOSITORY_URL = f"https://hub.docker.com/r/{DOCKER_HUB_REPOSITORY}"
DOCKER_HUB_TAGS_API = (
    "https://hub.docker.com/v2/namespaces/kunkundi/repositories/niuone/tags"
)
VERSION_CHECK_TTL_SECONDS = 15 * 60
VERSION_CHECK_FAILURE_TTL_SECONDS = 5 * 60
VERSION_CHECK_MAX_PAGES = 20
VERSION_CHECK_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
VERSION_CHECK_CACHE: dict[str, Any] = {"ts": 0.0, "ttl": 0, "payload": None}
VERSION_CHECK_LOCK = threading.Lock()
LOCAL_DATA_DIR = get_local_data_dir(PROJECT_ROOT)
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
PUBLIC_DATA_DIR = Path(
    os.environ.get("DASHBOARD_PUBLIC_DATA_DIR") or DASHBOARD_HOME / "public-data"
).expanduser()
PUBLIC_SNAPSHOT_PUBLISHER: Any = None
CONFIG_PATH = Path(os.environ.get("DASHBOARD_CONFIG") or str(DASHBOARD_HOME / "config.yaml")).expanduser()
DASHBOARD_ENV_FILE = get_dashboard_env_file(PROJECT_ROOT)
CRON_OUTPUT_DIR = DASHBOARD_HOME / "cron" / "output"
CRON_STATE_DIR = DASHBOARD_HOME / "cron" / "state"
INDICES_SNAPSHOT_FILE = CRON_OUTPUT_DIR / "indices_dashboard_cache.json"
IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE = Path(
    os.environ.get("IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE")
    or CRON_OUTPUT_DIR / "iwencai_dragon_tiger_latest.json"
).expanduser()
B1_CACHE_FILE = CRON_OUTPUT_DIR / "b1_screen_latest.json"
MONEY_FLOW_SNAPSHOT_FILE = CRON_OUTPUT_DIR / "industry_main_money_flow_cache.json"
# Main-net samples use a new history file so legacy total-flow observations
# remain recoverable but can never be replayed under the new metric label.
INDUSTRY_FLOW_HISTORY_FILE = CRON_OUTPUT_DIR / "industry_main_flow_history.json"
MARKET_BREADTH_HISTORY_FILE = CRON_OUTPUT_DIR / "market_breadth_history.json"
STATS_DB = DASHBOARD_HOME / "dashboard_stats.db"
LEGACY_STATS_DB = DASHBOARD_HOME / "dashboard_users.db"
LEGACY_STATS_MIGRATION_KEY = "dashboard_users_visit_stats_v1"
ADMIN_TOKEN_FILE = DASHBOARD_HOME / "dashboard_admin_token.txt"
ADMIN_SESSION_COOKIE_NAME = "dashboard_admin_session"
VISITOR_COOKIE_NAME = "niuone_visitor_id"
ACTION_HEADER_NAME = "X-NiuOne-Action"
ACTION_HEADER_VALUES = {"1", "true", "yes", "on"}
TRUTHY_VALUES = {"1", "true", "yes", "on"}
US_FEATURE_CATEGORIES = {"x_monitor", "us_ratings"}
INDUSTRY_FLOW_PLAYBACK_SPEED_OPTIONS = (0.5, 0.75, 1.0, 1.5, 2.0)
INDUSTRY_FLOW_WINDOW_CONFIG_NAMES = (
    "DASHBOARD_INDUSTRY_FLOW_MORNING_START",
    "DASHBOARD_INDUSTRY_FLOW_MORNING_END",
    "DASHBOARD_INDUSTRY_FLOW_AFTERNOON_START",
    "DASHBOARD_INDUSTRY_FLOW_AFTERNOON_END",
)


def _bounded_int_value(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _industry_flow_playback_speed_value(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return INDUSTRY_FLOW_DEFAULT_PLAYBACK_SPEED
    return parsed if parsed in INDUSTRY_FLOW_PLAYBACK_SPEED_OPTIONS else INDUSTRY_FLOW_DEFAULT_PLAYBACK_SPEED


def _industry_flow_sampling_windows_value(
    values: dict[str, Any],
    *,
    fallback: tuple[tuple[str, str], tuple[str, str]] = INDUSTRY_FLOW_DEFAULT_SAMPLING_WINDOWS,
    strict: bool = False,
) -> tuple[tuple[str, str], tuple[str, str]]:
    defaults = {
        INDUSTRY_FLOW_WINDOW_CONFIG_NAMES[0]: fallback[0][0],
        INDUSTRY_FLOW_WINDOW_CONFIG_NAMES[1]: fallback[0][1],
        INDUSTRY_FLOW_WINDOW_CONFIG_NAMES[2]: fallback[1][0],
        INDUSTRY_FLOW_WINDOW_CONFIG_NAMES[3]: fallback[1][1],
    }
    resolved = {
        name: str(values.get(name) or default).strip()
        for name, default in defaults.items()
    }
    windows = (
        (resolved[INDUSTRY_FLOW_WINDOW_CONFIG_NAMES[0]], resolved[INDUSTRY_FLOW_WINDOW_CONFIG_NAMES[1]]),
        (resolved[INDUSTRY_FLOW_WINDOW_CONFIG_NAMES[2]], resolved[INDUSTRY_FLOW_WINDOW_CONFIG_NAMES[3]]),
    )
    try:
        return normalize_industry_flow_sampling_windows(windows)
    except ValueError:
        if strict:
            raise
        return fallback


NIUONE_LAUNCHD_LABELS = (
    "ai.niuone.cron-scheduler",
    "ai.niuone.x-watchlist",
    "ai.niuone.dashboard",
)
NIUONE_RESTART_DELAY_SECONDS = float(os.environ.get("NIUONE_RESTART_DELAY_SECONDS", "1.2") or "1.2")
ADMIN_PASSWORD = os.environ.get("DASHBOARD_ADMIN_PASSWORD", "").strip()
ADMIN_SESSION_TTL_SECONDS = int(os.environ.get("DASHBOARD_ADMIN_SESSION_TTL_SECONDS", "86400") or "86400")
TRUSTED_PROXY_CIDRS = tuple(
    value.strip()
    for value in os.environ.get("DASHBOARD_TRUSTED_PROXIES", "127.0.0.1/32,::1/128").split(",")
    if value.strip()
)
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
PRACTICE_EQUITY_HEARTBEAT_LOCK = threading.Lock()
PRACTICE_EQUITY_HEARTBEAT_THREAD: threading.Thread | None = None
PRACTICE_EQUITY_HEARTBEAT_POLL_SECONDS = 5.0
INDUSTRY_FLOW_HISTORY_LOCK = threading.RLock()
INDUSTRY_FLOW_SAMPLER_THREAD: threading.Thread | None = None
MARKET_BREADTH_HISTORY_LOCK = threading.RLock()
MARKET_BREADTH_REFRESH_LOCK = threading.Lock()
MARKET_BREADTH_SAMPLER_THREAD: threading.Thread | None = None
DAILY_MARKET_HISTORY_RESET_THREAD: threading.Thread | None = None
MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS = MARKET_BREADTH_DEFAULT_SAMPLE_INTERVAL_SECONDS
INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS = _bounded_int_value(
    os.environ.get("DASHBOARD_INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS"),
    INDUSTRY_FLOW_DEFAULT_SAMPLE_INTERVAL_SECONDS,
    60,
    600,
)
INDUSTRY_FLOW_SIDE_LIMIT = _bounded_int_value(
    os.environ.get("DASHBOARD_INDUSTRY_FLOW_SIDE_LIMIT"),
    INDUSTRY_FLOW_DEFAULT_SIDE_LIMIT,
    1,
    10,
)
INDUSTRY_FLOW_PLAYBACK_SPEED = _industry_flow_playback_speed_value(
    os.environ.get("DASHBOARD_INDUSTRY_FLOW_PLAYBACK_SPEED")
)
INDUSTRY_FLOW_SAMPLING_WINDOWS = _industry_flow_sampling_windows_value(os.environ)
B1_CANDIDATE_REFRESH_LOCK = threading.Lock()
B1_FULL_SCAN_LOCK = threading.Lock()
B1_CANDIDATE_REFRESH_MIN_SECONDS = float(os.environ.get("DASHBOARD_B1_CANDIDATE_REFRESH_MIN_SECONDS", "0") or "0")
B1_CANDIDATE_REFRESH_LAST_TS = 0.0
MULTI_STRATEGY_CACHE_FILE = CRON_OUTPUT_DIR / "multi_strategy_latest.json"
TRADER_SCRIPT = Path(
    os.environ.get("DASHBOARD_TRADER_SCRIPT", ENTRYPOINT_DIR / "niuniu_practice_trader.py")
).expanduser()
TRADER_MODULE = None
TRADER_MODULE_MTIME = 0.0
TRADER_SELL_SIGNALS_FILE = SCRIPT_DIR / "trading" / "sell_signals.py"
TRADER_SELL_SIGNALS_MTIME = 0.0
TRADER_MODULE_LOCK = threading.Lock()
PRACTICE_DECISION_KEYS: set[str] = set()
PRACTICE_MANUAL_CYCLE_LOCK = threading.Lock()
PRACTICE_MANUAL_CYCLE_STATE_LOCK = threading.RLock()
PRACTICE_MANUAL_SCAN_REUSE_SECONDS = max(
    0,
    int(os.environ.get("DASHBOARD_MANUAL_SCAN_REUSE_SECONDS", "0") or "0"),
)
PRACTICE_MANUAL_CYCLE_STATE: dict[str, Any] = {
    "running": False,
    "stage": "idle",
    "started_at": "",
    "finished_at": "",
    "error": "",
}
PRACTICE_MANUAL_CYCLE_PUBLIC_FIELDS = (
    "running",
    "stage",
    "stage_label",
    "started_at",
    "finished_at",
    "generated_at",
    "candidate_count",
    "manual_scan_reused",
    "error",
)
BENCHMARK_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
BENCHMARK_TTL_SECONDS = 20
CN_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

# Public dashboard concurrency protection: cache expensive JSON payloads in-process
# so 1000 viewers do not trigger 1000 identical DB/行情/akshare computations.
API_RESPONSE_CACHE: dict[str, dict[str, Any]] = {}
API_RESPONSE_LOCK = threading.RLock()
API_CACHE_KEY_LOCKS: dict[str, threading.Lock] = {}
API_CACHE_KEY_GENERATIONS: dict[str, int] = {}
API_CACHE_MAX_ENTRIES = int(os.environ.get("DASHBOARD_API_CACHE_MAX_ENTRIES", "256") or "256")
API_STALE_WHILE_REFRESH_SECONDS = int(
    os.environ.get("DASHBOARD_API_STALE_WHILE_REFRESH_SECONDS", "300") or "300"
)
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
RATE_LIMIT_ADMIN_LOGIN = int(os.environ.get("DASHBOARD_RATE_LIMIT_ADMIN_LOGIN", "10") or "10")
RATE_LIMIT_NOTIFICATION_TEST = int(os.environ.get("DASHBOARD_NOTIFICATION_TEST_RATE_LIMIT", "10") or "10")
RATE_LIMIT_MODEL_TEST = int(os.environ.get("DASHBOARD_MODEL_TEST_RATE_LIMIT", "10") or "10")
RATE_LIMIT_IWENCAI_TEST = int(os.environ.get("DASHBOARD_IWENCAI_TEST_RATE_LIMIT", "10") or "10")
MODEL_TEST_TIMEOUT_SECONDS = max(
    5,
    min(30, int(os.environ.get("DASHBOARD_MODEL_TEST_TIMEOUT_SECONDS", "20") or "20")),
)
MODEL_TEST_MAX_CONCURRENCY = 2
MODEL_TEST_SEMAPHORE = threading.BoundedSemaphore(MODEL_TEST_MAX_CONCURRENCY)
IWENCAI_TEST_MAX_CONCURRENCY = 2
IWENCAI_TEST_SEMAPHORE = threading.BoundedSemaphore(IWENCAI_TEST_MAX_CONCURRENCY)
RATE_LIMIT_BUCKETS: dict[tuple[str, str], tuple[float, int]] = {}
RATE_LIMIT_LOCK = threading.Lock()
ADMIN_TOKEN_LOCK = threading.Lock()
VISIT_STATS_LOCK = threading.RLock()
VISIT_STATS_INIT_SIGNATURE: tuple[Any, ...] | None = None
ENV_FILE_WRITE_LOCK = threading.RLock()
PRACTICE_CANDIDATES_CACHE_KEY = "practice_candidates"
PRACTICE_CANDIDATES_API_PATHS = frozenset({"/api/practice_candidates", "/api/b1_screen"})
PRACTICE_CANDIDATES_REFRESH_API_PATHS = frozenset({"/api/practice_candidates/refresh", "/api/b1_screen/trigger"})
PRACTICE_MANUAL_CYCLE_API_PATH = "/api/niuniu_practice/manual-cycle"
PRACTICE_MARKET_SUMMARY_API_PATH = "/api/niuniu_practice/market-summary"
PRACTICE_MARKET_SUMMARY_FILE = CRON_OUTPUT_DIR / "practice_market_summary_latest.json"
API_TTLS = {
    "messages": 10,
    "practice_candidates": int(
        os.environ.get("DASHBOARD_PRACTICE_CANDIDATES_TTL_SECONDS")
        or os.environ.get("DASHBOARD_B1_SCREEN_TTL_SECONDS")
        or "15"
    ),
    "niuniu_practice": int(os.environ.get("DASHBOARD_PRACTICE_TTL_SECONDS", "15") or "15"),
    "practice_benchmarks": 30,
    "indices": int(os.environ.get("DASHBOARD_INDICES_TTL_SECONDS", "60") or "60"),
    "market_breadth": MARKET_BREADTH_DEFAULT_SAMPLE_INTERVAL_SECONDS,
    "sectors": 60,
    "us_sectors": int(os.environ.get("DASHBOARD_US_SECTORS_TTL_SECONDS", "300") or "300"),
    "hot_stocks": 60,
    "money_flow": 60,
    "industry_flow": 30,
    "market_flow": 30,
    "us_quotes": 30,
    "us_profiles": int(os.environ.get("DASHBOARD_US_PROFILES_TTL_SECONDS", "86400") or "86400"),
    "us_market_summary": int(os.environ.get("DASHBOARD_US_MARKET_SUMMARY_TTL_SECONDS", "300") or "300"),
    "iwencai_dragon_tiger": int(os.environ.get("IWENCAI_CACHE_TTL_SECONDS", "300") or "300"),
}
PRACTICE_FAST_CACHE_KEY = "niuniu_practice_fast:v2"
CALENDAR_HISTORY_SCHEMA_VERSION = 1
CALENDAR_HISTORY_MAX_DAYS = 20
CALENDAR_HISTORY_BUCKET_MINUTES = 10

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
    {"name": "DASHBOARD_NIUNIU_DB", "label": "实战页面 DB", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "niuniu.db"), "effect": "restart"},
    {"name": "DASHBOARD_TRADER_SCRIPT", "label": "实战页面脚本", "group": "基础路径", "kind": "path", "default": str(ENTRYPOINT_DIR / "niuniu_practice_trader.py"), "effect": "restart"},
    {"name": "DASHBOARD_B1_SCANNER", "label": "实战选股扫描脚本", "group": "基础路径", "kind": "path", "default": str(ENTRYPOINT_DIR / "multi_strategy_screen.py"), "effect": "restart"},
    {"name": "DASHBOARD_CN_STOCK_TOOLS", "label": "A股行情工具脚本", "group": "基础路径", "kind": "path", "default": str(ENTRYPOINT_DIR / "cn_stock_tools.py"), "effect": "restart"},
    {"name": "DASHBOARD_CRON_JOBS", "label": "Cron jobs JSON", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "cron" / "jobs.json"), "effect": "next_run"},
    {"name": "DASHBOARD_X_WATCHLIST_STATE", "label": "X 监控状态文件", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "cron" / "state" / "x_watchlist_latest.json"), "effect": "next_run"},
    {"name": "DASHBOARD_PUBLIC_DATA_DIR", "label": "公开快照目录", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "public-data"), "effect": "restart"},
    {"name": "DASHBOARD_PUBLIC_PROJECTION_ENABLED", "label": "公开增量快照", "group": "基础路径", "kind": "bool", "default": "1", "effect": "restart"},

    {"name": "DASHBOARD_ADMIN_PASSWORD", "label": "设置页管理员密码", "group": "访问控制", "kind": "secret", "default": "", "effect": "runtime"},
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
    {"name": "DASHBOARD_PUBLIC_REFRESH_SECONDS", "label": "公开快照刷新秒数", "group": "行情与资金流设置", "kind": "int", "default": "15", "effect": "restart"},

    {"name": "DASHBOARD_B1_SCHEDULE_ENABLED", "label": "启用实战定时选股", "group": "任务调度", "kind": "bool", "default": "1", "effect": "restart"},
    {"name": "DASHBOARD_B1_SCHEDULE_TIMES", "label": "选股及买卖决策时间点", "group": "选股与买卖设置", "kind": "time_list", "default": "09:25,10:00,10:30,11:00,11:20,13:00,13:30,14:00,14:30,14:50", "effect": "runtime"},
    {"name": STOCK_UNIVERSE_ENV, "label": "选股范围", "group": "选股与买卖设置", "kind": "stock_universe", "default": DEFAULT_STOCK_UNIVERSE, "effect": "runtime"},
    {"name": "DASHBOARD_DISPLAY_CANDIDATE_LIMIT", "label": "候选池展示数量", "group": "选股与买卖设置", "kind": "int", "default": "10", "effect": "runtime"},
    {"name": "DASHBOARD_TRADE_CANDIDATE_LIMIT", "label": "买卖决策候选数量", "group": "选股与买卖设置", "kind": "int", "default": "10", "effect": "runtime"},
    {"name": "DASHBOARD_B3_EXIT_TIME", "label": "B3开盘离场检查时间", "group": "选股与买卖设置", "kind": "time", "default": "09:37", "effect": "runtime"},
    {"name": "DASHBOARD_TIME_EXIT_TIME", "label": "尾盘离场检查时间", "group": "选股与买卖设置", "kind": "time", "default": "14:45", "effect": "runtime"},
    {"name": ACTIVE_STRATEGY_ENV, "label": "当前独立策略", "group": "选股与交易策略", "kind": "strategy_suite", "default": default_enabled_persona_strategies_value(), "effect": "runtime"},
    {"name": PRESET_STRATEGY_TEXT_ENV, "label": "预设文字策略", "group": "选股与交易策略", "kind": "preset_strategy_text", "default": "", "effect": "runtime"},
    {"name": "DASHBOARD_B1_SCAN_TIMEOUT_SECONDS", "label": "实战选股扫描超时秒数", "group": "任务调度", "kind": "int", "default": "360", "effect": "restart"},
    {"name": "DASHBOARD_B1_SCAN_WORKERS", "label": "实战选股并发数", "group": "任务调度", "kind": "int", "default": "6", "effect": "restart"},
    {"name": "DASHBOARD_MANUAL_SCAN_REUSE_SECONDS", "label": "手动选股复用候选秒数", "group": "任务调度", "kind": "int", "default": "0", "effect": "restart"},
    {"name": "DASHBOARD_B1_SCHEDULE_CATCHUP_MINUTES", "label": "实战选股漏触发补跑窗口分钟", "group": "任务调度", "kind": "int", "default": "35", "effect": "restart"},
    {"name": "DASHBOARD_B1_SCHEDULE_STALE_SECONDS", "label": "实战选股运行中陈旧秒数", "group": "任务调度", "kind": "int", "default": "900", "effect": "restart"},
    {"name": "DASHBOARD_CRON_MAX_ATTEMPTS", "label": "Cron 失败最大运行次数", "group": "任务调度", "kind": "int", "default": "2", "effect": "next_run"},
    {"name": "DASHBOARD_CRON_RETRY_DELAY_SECONDS", "label": "Cron 失败重试间隔秒数", "group": "任务调度", "kind": "int", "default": "300", "effect": "next_run"},
    {"name": "DASHBOARD_PENDING_DECISION_POLL_SECONDS", "label": "延迟成交检查秒数", "group": "任务调度", "kind": "int", "default": "5", "effect": "restart"},

    {"name": "DASHBOARD_DECISION_MAX_TOKENS", "label": "决策最大输出长度", "group": "买卖决策模型", "kind": "max_tokens", "default": DEFAULT_MODEL_MAX_TOKENS, "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_TIMEOUT", "label": "决策请求超时", "group": "买卖决策模型", "kind": "int", "default": "180", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_INTELLIGENCE_ENABLED", "label": "启用综合决策参考", "group": "综合决策参考", "kind": "bool", "default": "1", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS", "label": "决策参考缓存秒数", "group": "综合决策参考", "kind": "int", "default": "75", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS", "label": "单类参考数据上限", "group": "综合决策参考", "kind": "int", "default": "5", "effect": "next_run"},

    {"name": "IWENCAI_ENABLED", "label": "启用问财数据源", "group": "问财数据源", "kind": "bool", "default": "0", "effect": "runtime"},
    {"name": "IWENCAI_BASE_URL", "label": "问财 API 地址", "group": "问财数据源", "kind": "text", "default": IWENCAI_DEFAULT_BASE_URL, "effect": "runtime"},
    {"name": "IWENCAI_API_KEY", "label": "问财 API Key", "group": "问财数据源", "kind": "secret", "default": "", "effect": "runtime"},
    {"name": "IWENCAI_TIMEOUT_SECONDS", "label": "问财请求超时秒数", "group": "问财数据源", "kind": "int", "default": "20", "effect": "runtime"},
    {"name": "IWENCAI_MAX_RETRIES", "label": "问财失败重试次数", "group": "问财数据源", "kind": "int", "default": "1", "effect": "runtime"},
    {"name": "IWENCAI_MAX_CONCURRENCY", "label": "问财最大并发数", "group": "问财数据源", "kind": "int", "default": "2", "effect": "runtime"},
    {"name": "IWENCAI_CACHE_TTL_SECONDS", "label": "问财龙虎榜缓存秒数", "group": "问财数据源", "kind": "int", "default": "300", "effect": "runtime"},
    {"name": "IWENCAI_DRAGON_TIGER_CRON", "label": "龙虎榜交易日更新时间", "group": "问财数据源", "kind": "cron_time", "default": "0 18 * * 1-5", "effect": "next_run"},

    {"name": "DASHBOARD_MARKET_GUIDANCE_ENABLED", "label": "启用盘面指引控仓", "group": "交易规则与风控", "kind": "bool", "default": "1", "effect": "next_run"},
    {"name": TRADE_DISCIPLINE_TEXT_ENV, "label": "交易纪律 Prompt", "group": "交易规则与风控", "kind": "trade_discipline_text", "default": default_trade_discipline_text(), "effect": "runtime"},
    {"name": "DASHBOARD_MAX_OPEN_POSITIONS", "label": "最大持仓只数", "group": "交易规则与风控", "kind": "int", "default": "6", "effect": "next_run"},
    {"name": "DASHBOARD_MAX_NEW_BUYS_PER_DECISION", "label": "单轮最大新买入", "group": "交易规则与风控", "kind": "int", "default": "2", "effect": "next_run"},
    {"name": "DASHBOARD_MAX_SINGLE_POSITION_PCT", "label": "单票仓位参考%", "group": "交易规则与风控", "kind": "text", "default": "10", "effect": "next_run"},
    {"name": "DASHBOARD_MAX_TOTAL_POSITION_PCT", "label": "总仓位参考%", "group": "交易规则与风控", "kind": "text", "default": "80", "effect": "next_run"},
    {"name": "DASHBOARD_MIN_CASH_RESERVE_PCT", "label": "现金缓冲参考%", "group": "交易规则与风控", "kind": "text", "default": "20", "effect": "next_run"},
    {"name": "DASHBOARD_MORNING_MAX_OPEN_POSITIONS", "label": "午盘前持仓上限", "group": "交易规则与风控", "kind": "int", "default": "3", "effect": "next_run"},

    {"name": "DASHBOARD_NOTIFICATION_ENABLED", "label": "启用模拟成交通知", "group": "交易通知", "kind": "bool", "default": "0", "effect": "runtime"},
    {"name": "DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS", "label": "单次推送超时秒数", "group": "交易通知", "kind": "int", "default": "5", "effect": "runtime"},
    {"name": "DASHBOARD_FEISHU_NOTIFICATION_ENABLED", "label": "启用飞书通知", "group": "交易通知", "kind": "bool", "default": "0", "effect": "runtime"},
    {"name": "DASHBOARD_FEISHU_WEBHOOK_URL", "label": "飞书机器人 Webhook", "group": "交易通知", "kind": "secret", "default": "", "effect": "runtime"},
    {"name": "DASHBOARD_FEISHU_SIGNING_SECRET", "label": "飞书签名密钥（可选）", "group": "交易通知", "kind": "secret", "default": "", "effect": "runtime"},
    {"name": "DASHBOARD_DINGTALK_NOTIFICATION_ENABLED", "label": "启用钉钉通知", "group": "交易通知", "kind": "bool", "default": "0", "effect": "runtime"},
    {"name": "DASHBOARD_DINGTALK_WEBHOOK_URL", "label": "钉钉机器人 Webhook", "group": "交易通知", "kind": "secret", "default": "", "effect": "runtime"},
    {"name": "DASHBOARD_DINGTALK_SIGNING_SECRET", "label": "钉钉签名密钥（可选）", "group": "交易通知", "kind": "secret", "default": "", "effect": "runtime"},
    {"name": "DASHBOARD_WECOM_NOTIFICATION_ENABLED", "label": "启用企业微信通知", "group": "交易通知", "kind": "bool", "default": "0", "effect": "runtime"},
    {"name": "DASHBOARD_WECOM_WEBHOOK_URL", "label": "企业微信机器人 Webhook", "group": "交易通知", "kind": "secret", "default": "", "effect": "runtime"},
    {"name": "DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED", "label": "启用 Telegram 通知", "group": "交易通知", "kind": "bool", "default": "0", "effect": "runtime"},
    {"name": "DASHBOARD_TELEGRAM_BOT_TOKEN", "label": "Telegram Bot Token", "group": "交易通知", "kind": "secret", "default": "", "effect": "runtime"},
    {"name": "DASHBOARD_TELEGRAM_CHAT_ID", "label": "Telegram Chat ID", "group": "交易通知", "kind": "text", "default": "", "effect": "runtime"},

    {"name": "DASHBOARD_US_FEATURES_ENABLED", "label": "开启牛牛美股", "group": "牛牛美股", "kind": "bool", "default": "0", "effect": "next_run"},
    {"name": "US_RATING_BASE_URL", "label": "美股评级 API Base URL", "group": "牛牛美股", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "US_RATING_API_KEY", "label": "美股评级 API Key", "group": "牛牛美股", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "US_RATING_CONTEXT_LENGTH", "label": "美股评级上下文长度", "group": "牛牛美股", "kind": "context_length", "default": DEFAULT_MODEL_CONTEXT_LENGTH, "effect": "next_run"},
    {"name": "US_RATING_MAX_TOKENS", "label": "美股评级最大输出长度", "group": "牛牛美股", "kind": "max_tokens", "default": DEFAULT_MODEL_MAX_TOKENS, "effect": "next_run"},
    {"name": "CROSSDESK_BASE_URL", "label": "Crossdesk Base URL", "group": "上游模型覆盖", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "CROSSDESK_API_KEY", "label": "Crossdesk API Key", "group": "上游模型覆盖", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_GROK_MODEL", "label": "Grok 模型", "group": "牛牛美股", "kind": "text", "default": "grok-4.20-multi-agent-xhigh", "effect": "next_run"},
    {"name": "DASHBOARD_GROK_API_MODE", "label": "Grok 搜索工具接口模式", "group": "牛牛美股", "kind": "api_mode", "default": "auto", "effect": "next_run"},
    {"name": "DASHBOARD_GROK_CONTEXT_LENGTH", "label": "Grok 模型上下文长度", "group": "牛牛美股", "kind": "context_length", "default": DEFAULT_MODEL_CONTEXT_LENGTH, "effect": "next_run"},
    {"name": "DASHBOARD_GROK_MAX_TOKENS", "label": "Grok 最大输出长度", "group": "牛牛美股", "kind": "max_tokens", "default": DEFAULT_MODEL_MAX_TOKENS, "effect": "next_run"},
    {"name": "DASHBOARD_GROK_BASE_URL", "label": "Grok API 地址", "group": "牛牛美股", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_GROK_API_KEY", "label": "Grok API 密钥", "group": "牛牛美股", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_NEWS_MODEL", "label": "消息面预检模型", "group": "消息面预检模型", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_NEWS_API_MODE", "label": "消息面搜索工具接口模式", "group": "消息面预检模型", "kind": "api_mode", "default": "auto", "effect": "next_run"},
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
    {"name": "DASHBOARD_INDICES_TTL_SECONDS", "label": "指数行情更新间隔（秒）", "group": "行情与资金流设置", "kind": "int", "default": "60", "effect": "runtime", "min": "1"},
    {"name": "DASHBOARD_INDUSTRY_FLOW_PLAYBACK_SPEED", "label": "资金流默认播放速度", "group": "行情与资金流设置", "kind": "playback_speed", "default": "0.5", "effect": "runtime"},
    {"name": "DASHBOARD_INDUSTRY_FLOW_SIDE_LIMIT", "label": "资金流每侧行业数量", "group": "行情与资金流设置", "kind": "int", "default": "10", "effect": "runtime", "min": "1", "max": "10"},
    {"name": "DASHBOARD_INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS", "label": "资金流采样间隔（秒）", "group": "行情与资金流设置", "kind": "int", "default": "60", "effect": "runtime", "min": "60", "max": "600"},
    {"name": "DASHBOARD_INDUSTRY_FLOW_MORNING_START", "label": "上午采样开始时间", "group": "行情与资金流设置", "kind": "time", "default": "09:25", "effect": "runtime"},
    {"name": "DASHBOARD_INDUSTRY_FLOW_MORNING_END", "label": "上午采样结束时间", "group": "行情与资金流设置", "kind": "time", "default": "11:31", "effect": "runtime"},
    {"name": "DASHBOARD_INDUSTRY_FLOW_AFTERNOON_START", "label": "下午采样开始时间", "group": "行情与资金流设置", "kind": "time", "default": "13:00", "effect": "runtime"},
    {"name": "DASHBOARD_INDUSTRY_FLOW_AFTERNOON_END", "label": "下午采样结束时间", "group": "行情与资金流设置", "kind": "time", "default": "15:01", "effect": "runtime"},

    {"name": "X_WATCHLIST_STRICT_CONTEXT_HOLD", "label": "X 上下文缺失时暂缓发送", "group": "X 监控", "kind": "bool", "default": "0", "effect": "next_run"},
    {"name": "X_WATCHLIST_DEADLINE_SECONDS", "label": "X 总截止秒数", "group": "X 监控", "kind": "int", "default": "135", "effect": "next_run"},
    {"name": "X_WATCHLIST_REQUEST_TIMEOUT_SECONDS", "label": "X 单账号请求超时秒数", "group": "牛牛美股", "kind": "int", "default": "45", "effect": "next_run"},
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
    "DASHBOARD_ADMIN_PASSWORD",
    "DASHBOARD_PUBLIC_REFRESH_SECONDS",
    "DASHBOARD_US_FEATURES_ENABLED",
    "DASHBOARD_GROK_MODEL",
    "DASHBOARD_GROK_API_MODE",
    "DASHBOARD_GROK_CONTEXT_LENGTH",
    "DASHBOARD_GROK_MAX_TOKENS",
    "DASHBOARD_GROK_BASE_URL",
    "DASHBOARD_GROK_API_KEY",
    "X_WATCHLIST_ACCOUNTS",
    "X_WATCHLIST_DAEMON_INTERVAL_SECONDS",
    "X_WATCHLIST_REQUEST_TIMEOUT_SECONDS",
    "DASHBOARD_US_RATING_CRON",
    "US_RATING_CONTEXT_LENGTH",
    "US_RATING_MAX_TOKENS",
    "US_RATING_DEADLINE_SECONDS",
    "US_RATING_REQUEST_TIMEOUT_SECONDS",
    "DASHBOARD_NEWS_MODEL",
    "DASHBOARD_NEWS_API_MODE",
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
    "IWENCAI_ENABLED",
    "IWENCAI_BASE_URL",
    "IWENCAI_API_KEY",
    "IWENCAI_TIMEOUT_SECONDS",
    "IWENCAI_MAX_RETRIES",
    "IWENCAI_MAX_CONCURRENCY",
    "IWENCAI_CACHE_TTL_SECONDS",
    "IWENCAI_DRAGON_TIGER_CRON",
    "DASHBOARD_MARKET_GUIDANCE_ENABLED",
    TRADE_DISCIPLINE_TEXT_ENV,
    "DASHBOARD_MAX_OPEN_POSITIONS",
    "DASHBOARD_MAX_NEW_BUYS_PER_DECISION",
    "DASHBOARD_MAX_SINGLE_POSITION_PCT",
    "DASHBOARD_MAX_TOTAL_POSITION_PCT",
    "DASHBOARD_MIN_CASH_RESERVE_PCT",
    "DASHBOARD_MORNING_MAX_OPEN_POSITIONS",
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
    "DASHBOARD_B1_SCHEDULE_TIMES",
    STOCK_UNIVERSE_ENV,
    "DASHBOARD_DISPLAY_CANDIDATE_LIMIT",
    "DASHBOARD_TRADE_CANDIDATE_LIMIT",
    "DASHBOARD_B3_EXIT_TIME",
    "DASHBOARD_TIME_EXIT_TIME",
    ACTIVE_STRATEGY_ENV,
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
    "DASHBOARD_INDUSTRY_FLOW_PLAYBACK_SPEED",
    "DASHBOARD_INDUSTRY_FLOW_SIDE_LIMIT",
    "DASHBOARD_INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS",
    "DASHBOARD_INDUSTRY_FLOW_MORNING_START",
    "DASHBOARD_INDUSTRY_FLOW_MORNING_END",
    "DASHBOARD_INDUSTRY_FLOW_AFTERNOON_START",
    "DASHBOARD_INDUSTRY_FLOW_AFTERNOON_END",
]
TRADER_RUNTIME_ENV_NAMES = {
    STOCK_UNIVERSE_ENV,
    "DASHBOARD_NEWS_MODEL",
    "DASHBOARD_NEWS_API_MODE",
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
    ACTIVE_STRATEGY_ENV,
    PRESET_STRATEGY_TEXT_ENV,
}
ENV_GROUP_ORDER = [
    "牛牛美股",
    "消息面预检模型",
    "买卖决策模型",
    "交易规则与风控",
    "交易通知",
    "选股与买卖设置",
    "综合决策参考",
    "选股与交易策略",
    "盘面监控生产时间点",
    "行情与资金流设置",
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
    return security_impl.hash_token(token)


def get_or_create_admin_token() -> str:
    """Return the local bootstrap credential used to protect admin sessions."""
    return security_impl.load_or_create_admin_token(ADMIN_TOKEN_FILE, ADMIN_TOKEN_LOCK)


def admin_session_signing_key() -> bytes:
    return security_impl.derive_admin_session_signing_key(
        get_or_create_admin_token(),
        ADMIN_PASSWORD,
    )


def new_admin_session(now: float | None = None) -> str:
    return security_impl.create_admin_session(admin_session_signing_key(), now)


def validate_admin_session(cookie_value: str, now: float | None = None) -> bool:
    return security_impl.validate_admin_session(
        cookie_value,
        admin_session_signing_key(),
        ttl_seconds=ADMIN_SESSION_TTL_SECONDS,
        now=now,
    )


def verify_admin_credential(value: str) -> bool:
    return security_impl.verify_admin_credential(
        value,
        ADMIN_PASSWORD or get_or_create_admin_token(),
    )


def check_rate_limit(scope: str, key: str, limit: int, window: int | None = None) -> tuple[bool, int]:
    return security_impl.consume_rate_limit(
        scope,
        key,
        limit,
        enabled=RATE_LIMIT_ENABLED,
        default_window=RATE_LIMIT_WINDOW_SECONDS,
        buckets=RATE_LIMIT_BUCKETS,
        lock=RATE_LIMIT_LOCK,
        window=window,
    )


def visit_stats_init_signature() -> tuple[Any, ...]:
    return visit_stats_impl.database_signature(STATS_DB, LEGACY_STATS_DB)


def ensure_stats_db() -> None:
    global VISIT_STATS_INIT_SIGNATURE
    VISIT_STATS_INIT_SIGNATURE = visit_stats_impl.ensure_database(
        stats_db=STATS_DB,
        legacy_stats_db=LEGACY_STATS_DB,
        initialized_signature=VISIT_STATS_INIT_SIGNATURE,
        lock=VISIT_STATS_LOCK,
        migrate_legacy=migrate_legacy_visit_stats,
        now=_now_ts,
    )


def sqlite_table_exists(con: Any, table: str) -> bool:
    return visit_stats_impl.sqlite_table_exists(con, table)


def migrate_legacy_visit_stats(con: Any) -> bool:
    """Move visit counters out of the retired dashboard user database once."""
    return visit_stats_impl.migrate_legacy_database(
        con,
        stats_db=STATS_DB,
        legacy_stats_db=LEGACY_STATS_DB,
        migration_key=LEGACY_STATS_MIGRATION_KEY,
        now=_now_ts,
        warn=lambda message: print(message, file=sys.stderr),
    )


def increment_visit_count(visitor_id: str) -> dict[str, int]:
    """Count page views for the main dashboard only; API polling is excluded."""
    return visit_stats_impl.increment_visit_count(
        visitor_id,
        stats_db=STATS_DB,
        lock=VISIT_STATS_LOCK,
        ensure_initialized=ensure_stats_db,
        hash_visitor=hash_token,
        now=_now_ts,
    )


def parse_request_cookies(header: str | None) -> dict[str, str]:
    return security_impl.parse_request_cookies(header)

def get_trader_module():
    global TRADER_MODULE, TRADER_MODULE_MTIME, TRADER_SELL_SIGNALS_MTIME
    current_mtime = TRADER_SCRIPT.stat().st_mtime if TRADER_SCRIPT.exists() else 0.0
    support_mtime = TRADER_SELL_SIGNALS_FILE.stat().st_mtime if TRADER_SELL_SIGNALS_FILE.exists() else 0.0
    if (
        TRADER_MODULE is None
        or current_mtime != TRADER_MODULE_MTIME
        or support_mtime != TRADER_SELL_SIGNALS_MTIME
    ):
        with TRADER_MODULE_LOCK:
            current_mtime = TRADER_SCRIPT.stat().st_mtime if TRADER_SCRIPT.exists() else 0.0
            support_mtime = TRADER_SELL_SIGNALS_FILE.stat().st_mtime if TRADER_SELL_SIGNALS_FILE.exists() else 0.0
            if (
                TRADER_MODULE is None
                or current_mtime != TRADER_MODULE_MTIME
                or support_mtime != TRADER_SELL_SIGNALS_MTIME
            ):
                import importlib.util
                support_module = None
                support_package = None
                old_support_module = sys.modules.get("trading.sell_signals")
                if support_mtime != TRADER_SELL_SIGNALS_MTIME:
                    import trading as support_package

                    candidate_name = f"_niuone_sell_signals_{time.time_ns()}"
                    support_spec = importlib.util.spec_from_file_location(
                        candidate_name,
                        TRADER_SELL_SIGNALS_FILE,
                    )
                    if support_spec is None or support_spec.loader is None:
                        raise RuntimeError(f"cannot load trader support module: {TRADER_SELL_SIGNALS_FILE}")
                    support_module = importlib.util.module_from_spec(support_spec)
                    support_module.__package__ = "trading"
                    sys.modules[candidate_name] = support_module
                    try:
                        support_spec.loader.exec_module(support_module)
                    finally:
                        sys.modules.pop(candidate_name, None)
                    canonical_support_name = "trading.sell_signals"
                    for value in vars(support_module).values():
                        if getattr(value, "__module__", None) == candidate_name:
                            try:
                                value.__module__ = canonical_support_name
                            except (AttributeError, TypeError):
                                pass
                    canonical_support_spec = importlib.util.spec_from_file_location(
                        canonical_support_name,
                        TRADER_SELL_SIGNALS_FILE,
                    )
                    support_module.__name__ = canonical_support_name
                    support_module.__package__ = "trading"
                    support_module.__spec__ = canonical_support_spec
                    support_module.__loader__ = canonical_support_spec.loader if canonical_support_spec else None
                spec = importlib.util.spec_from_file_location("niuniu_practice_trader", TRADER_SCRIPT)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    if support_module is not None:
                        module._sell_signals = support_module
                    spec.loader.exec_module(module)
                    if support_module is not None:
                        sys.modules["trading.sell_signals"] = support_module
                        setattr(support_package, "sell_signals", support_module)
                        if sys.modules.get("app.trading.sell_signals") is old_support_module:
                            sys.modules["app.trading.sell_signals"] = support_module
                            app_trading = sys.modules.get("app.trading")
                            if app_trading is not None:
                                setattr(app_trading, "sell_signals", support_module)
                    TRADER_MODULE = module
                    TRADER_MODULE_MTIME = current_mtime
                    TRADER_SELL_SIGNALS_MTIME = support_mtime
    return TRADER_MODULE

def run_dashboard_helper(
    script_name: str,
    fallback: dict[str, Any],
    timeout: int = 90,
    args: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Run dashboard helper API scripts out-of-process.

    Some akshare paths load native JavaScript runtimes that can abort the whole
    Python process when imported inside the threaded HTTP server. Running helpers
    in a child process isolates those native crashes from the dashboard service.
    """
    script = COMPAT_DIR / script_name
    try:
        raw = subprocess.check_output(
            [sys.executable, str(script), *args],
            text=True,
            timeout=timeout,
            stderr=subprocess.DEVNULL,
        )
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


def latest_valid_equity_time(history: list[dict[str, Any]]) -> str:
    return practice_payload_impl.latest_valid_equity_time(history)


def annotate_practice_snapshot(payload: dict[str, Any], *, mode: str, history_scope: str) -> dict[str, Any]:
    last_equity_time = latest_valid_equity_time(payload.get("equity_history") or [])
    source_updated_at = str(payload.get("source_updated_at") or "")
    source_last_equity_time = last_equity_time
    payload["snapshot_mode"] = mode
    payload["equity_history_scope"] = history_scope
    payload["source_updated_at"] = source_updated_at
    payload["source_last_equity_time"] = source_last_equity_time
    payload["snapshot_meta"] = {
        "schema_version": 2,
        "mode": mode,
        "source_updated_at": source_updated_at,
        "source_last_equity_time": source_last_equity_time,
    }
    return payload


def persist_indices_snapshot(payload: dict[str, Any]) -> bool:
    """Keep the last complete index response for fast startup fallback."""
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items or payload.get("error"):
        return False
    snapshot = dict(payload)
    snapshot.pop("stale_cache", None)
    try:
        write_json_cache(INDICES_SNAPSHOT_FILE, snapshot)
    except (OSError, TypeError, ValueError) as exc:
        print(
            f"dashboard indices snapshot write failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return False
    return True


def produce_indices_data() -> dict[str, Any]:
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "indices",
            str(COMPAT_DIR / "indices_dashboard_api.py"),
        )
        if spec and spec.loader:
            indices_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(indices_mod)
            raw_result = indices_mod.fetch_indices_data()
            result = raw_result if isinstance(raw_result, dict) else {"items": raw_result}
            persist_indices_snapshot(result)
            return result
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
    """Return the retained local portfolio history without request-side I/O.

    Quote refresh, equity heartbeats, trading checks, and state persistence are
    owned by the dedicated background workers.  Keeping this read path local is
    important because a stale-cache refresh runs in the Dashboard process and a
    network-backed payload rebuild can otherwise starve every HTTP request.
    """
    try:
        now = current_cn_datetime()
        trader = get_trader_module()
        state = trader.load_state()
        payload = trader.enrich_portfolio(state)
        equity_history = state.get("equity_history", []) or []
        daily_equity_history = state.get("daily_equity_history", []) or []
        payload["equity_history"] = filter_future_equity_points(
            equity_history,
            now=now,
        )
        payload["daily_equity_history"] = compact_daily_equity_history(
            [*equity_history, *daily_equity_history],
            now=now,
        )
        payload["source_updated_at"] = str(
            state.get("updated_at") or payload.get("source_updated_at") or ""
        )
        payload["source_last_equity_time"] = latest_valid_equity_time(
            payload["equity_history"]
        )
        payload["calendar_history"] = build_compact_calendar_history(
            equity_history,
            source_updated_at=payload["source_updated_at"],
            now=now,
        )
        payload["trade_markers"] = compact_trade_markers(state.get("trade_log") or [])
        payload["trading_calendar"] = dashboard_trading_day_status(now)
        payload["trading_paused"] = state.get("trading_paused", False)
        payload["pause_reason"] = state.get("pause_reason", "")
        payload["pause_since"] = state.get("pause_since", "")
        strategy_performance = (
            trader.track_strategy_performance(state)
            if hasattr(trader, "track_strategy_performance")
            else {}
        )
        payload["strategy_performance"] = compact_strategy_performance(
            strategy_performance
        )
        if hasattr(trader, "build_trade_rule_note"):
            payload["trade_rule_note"] = trader.build_trade_rule_note()
        payload["decision_model"] = str(getattr(trader, "MODEL", "") or "")
        payload["decision_provider"] = str(
            getattr(trader, "PROVIDER_DISPLAY_NAME", "") or ""
        )
        annotate_practice_snapshot(payload, mode="full", history_scope="retained_history")
        return annotate_practice_payload_clock(payload, now=now)
    except Exception as exc:
        print(f"[WARN] practice payload error: {type(exc).__name__}: {exc}", flush=True)
        payload = {"positions": [], "cash": 0, "total_equity": 0, "initial_cash": 0,
                   "total_pnl": 0, "total_pnl_pct": 0, "trade_log": [], "decision_log": [],
                   "equity_history": [], "trade_markers": [], "last_error": str(exc), "decision_model": "", "decision_provider": ""}
        annotate_practice_snapshot(payload, mode="full", history_scope="unavailable")
        return annotate_practice_payload_clock(payload)


def record_practice_equity_heartbeat(trader: Any | None = None) -> bool:
    """Record one due equity heartbeat without overlapping another producer."""

    if not PRACTICE_EQUITY_HEARTBEAT_LOCK.acquire(blocking=False):
        return False
    try:
        trader = trader or get_trader_module()
        recorder = getattr(trader, "maybe_record_session_equity_heartbeat", None)
        if recorder is None:
            return False
        recorded = bool(recorder())
        if recorded:
            invalidate_api_cache("niuniu_practice", PRACTICE_FAST_CACHE_KEY)
        return recorded
    except Exception as exc:
        print(f"[WARN] 模拟账户权益心跳失败: {type(exc).__name__}: {exc}", flush=True)
        return False
    finally:
        PRACTICE_EQUITY_HEARTBEAT_LOCK.release()

def downsample_sequence(items: list[Any], max_points: int) -> list[Any]:
    return practice_payload_impl.downsample_sequence(items, max_points)


def parse_dashboard_ts(value: str) -> datetime | None:
    return practice_payload_impl.parse_dashboard_ts(value)


def is_a_share_trading_day_for_dashboard(dt: datetime) -> bool:
    return calendar_is_a_share_trading_day(dt)


def filter_future_equity_points(
    history: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    grace_seconds: int = 120,
) -> list[dict[str, Any]]:
    return practice_payload_impl.filter_future_equity_points(
        history,
        now=now or current_cn_datetime(),
        is_trading_day=is_a_share_trading_day_for_dashboard,
        grace_seconds=grace_seconds,
        parse_timestamp=parse_dashboard_ts,
    )


def compact_intraday_equity_history(
    history: list[dict[str, Any]],
    *,
    max_points: int = 120,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    resolved_now = now or current_cn_datetime()
    return practice_payload_impl.compact_intraday_equity_history(
        history,
        max_points=max_points,
        now=resolved_now,
        is_trading_day=is_a_share_trading_day_for_dashboard,
        filter_points=lambda points, **_kwargs: filter_future_equity_points(
            points,
            now=now,
        ),
        downsample=downsample_sequence,
    )


def dashboard_session_elapsed_minute(value: str) -> float | None:
    return practice_payload_impl.dashboard_session_elapsed_minute(
        value,
        parse_timestamp=parse_dashboard_ts,
    )


def build_compact_calendar_history(
    history: list[dict[str, Any]],
    *,
    source_updated_at: str = "",
    max_days: int = CALENDAR_HISTORY_MAX_DAYS,
    bucket_minutes: int = CALENDAR_HISTORY_BUCKET_MINUTES,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved_now = now or current_cn_datetime()
    return practice_payload_impl.build_compact_calendar_history(
        history,
        source_updated_at=source_updated_at,
        max_days=max_days,
        bucket_minutes=bucket_minutes,
        default_bucket_minutes=CALENDAR_HISTORY_BUCKET_MINUTES,
        schema_version=CALENDAR_HISTORY_SCHEMA_VERSION,
        now=resolved_now,
        is_trading_day=is_a_share_trading_day_for_dashboard,
        filter_points=lambda points, **_kwargs: filter_future_equity_points(
            points,
            now=now,
        ),
        elapsed_minute=dashboard_session_elapsed_minute,
    )


def compact_daily_equity_history(
    history: list[dict[str, Any]],
    *,
    max_days: int = 260,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    resolved_now = now or current_cn_datetime()
    return practice_payload_impl.compact_daily_equity_history(
        history,
        max_days=max_days,
        now=resolved_now,
        is_trading_day=is_a_share_trading_day_for_dashboard,
        filter_points=lambda points, **_kwargs: filter_future_equity_points(
            points,
            now=now,
        ),
    )


def compact_strategy_performance(perf: dict[str, Any], *, max_exit_items: int = 12) -> dict[str, Any]:
    return practice_payload_impl.compact_strategy_performance(
        perf,
        max_exit_items=max_exit_items,
    )


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
    return practice_payload_impl.compact_trade_markers(
        entries,
        max_items=max_items,
    )


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
        payload["source_updated_at"] = str(state.get("updated_at") or payload.get("source_updated_at") or "")
        payload["source_last_equity_time"] = latest_valid_equity_time(equity_history)
        payload["calendar_history"] = build_compact_calendar_history(
            equity_history,
            source_updated_at=payload["source_updated_at"],
            now=now,
        )
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
        # The fast snapshot is rendered before the full snapshot on most page
        # loads, so it must carry the same model identity as the full payload.
        # Otherwise the browser has no authoritative value during hydration.
        payload["decision_model"] = str(getattr(trader, "MODEL", "") or "")
        payload["decision_provider"] = str(getattr(trader, "PROVIDER_DISPLAY_NAME", "") or "")
        annotate_practice_snapshot(payload, mode="fast", history_scope="latest_day")
        annotate_practice_payload_clock(payload, now=now)
        return payload
    except Exception as exc:
        print(f"[WARN] fast practice payload error: {type(exc).__name__}: {exc}", flush=True)
        payload = {"positions": [], "cash": 0, "total_equity": 0, "initial_cash": 0,
                   "total_pnl": 0, "total_pnl_pct": 0, "trade_log": [], "decision_log": [],
                   "equity_history": [], "trade_markers": [], "last_error": str(exc),
                   "decision_model": "", "decision_provider": "",
                   "calendar_history": {"schema_version": CALENDAR_HISTORY_SCHEMA_VERSION, "complete": False, "days": {}}}
        annotate_practice_snapshot(payload, mode="fast", history_scope="unavailable")
        return annotate_practice_payload_clock(payload)

def normalize_b1_payload_for_trader(b1_payload: dict[str, Any]) -> dict[str, Any]:
    items = b1_payload.get("trade_items") or b1_payload.get("items") or b1_payload.get("candidates") or []
    payload = {"items": items, "generated_at": b1_payload.get("generated_at", "")}
    if isinstance(b1_payload.get("market_snapshot"), dict):
        payload["market_snapshot"] = b1_payload.get("market_snapshot")
    if isinstance(b1_payload.get("sector_tide_context"), dict):
        payload["sector_tide_context"] = b1_payload.get("sector_tide_context")
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
        latest = json.loads(B1_CACHE_FILE.read_text(encoding="utf-8"))
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
            parsed = json.loads(B1_CACHE_FILE.read_text(encoding="utf-8"))
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
            B1_CACHE_FILE.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
            MULTI_STRATEGY_CACHE_FILE.write_text(
                json.dumps(parsed, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            B1_CANDIDATE_REFRESH_LAST_TS = time.time()
            return {"updated": 0, "count": 0}

        import multi_strategy_screen as scanner

        active_scorers = scanner.active_strategy_scorers()
        zettaranc_refresh = bool(
            set(active_scorers).intersection(getattr(scanner, "ZETTARANC_STRATEGY_IDS", ()))
        )
        if zettaranc_refresh:
            scanner.annotate_candidate_industries(base_items, max_workers=8)
        keys_by_code = {str(item.get("code") or ""): _tencent_key_for_code(str(item.get("code") or "")) for item in base_items}
        quote_map = scanner.tencent_batch_quote(list(keys_by_code.values()))
        scoring_context = (
            dict(parsed.get("sector_tide_context") or {})
            if isinstance(parsed.get("sector_tide_context"), dict)
            else {}
        )
        if zettaranc_refresh:
            scoring_context["industry_money_flow"] = scanner.fetch_sector_tide_money_flow()
        refreshed: list[dict[str, Any]] = []
        previous_by_code = {str(item.get("code") or ""): item for item in base_items}
        for code, tencent_key in keys_by_code.items():
            old = previous_by_code.get(code) or {}
            name = old.get("name") or ""
            if hasattr(scanner, "candidate_in_configured_stock_universe") and not scanner.candidate_in_configured_stock_universe(old):
                continue
            quote = quote_map.get(tencent_key) or {}
            price = quote.get("price")
            amount = quote.get("amount") or 0
            if price is None or float(price or 0) <= 0:
                continue
            if float(amount or 0) < 8e8:
                continue
            multi = scanner.analyze_all_strategies(
                code,
                tencent_key,
                quote=quote,
                name=name,
                industry=str(old.get("industry") or old.get("sector") or ""),
                context=scoring_context or None,
                scorers=active_scorers,
            )
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
                "industry": best.get("industry") or old.get("industry") or old.get("sector") or "",
                "sector": best.get("industry") or old.get("sector") or old.get("industry") or "",
                "market_regime": best.get("market_regime"),
                "market_score": best.get("market_score"),
                "market_hard_stop": best.get("market_hard_stop"),
                "market_allows_buys": best.get("market_allows_buys"),
                "sector_status": best.get("sector_status"),
                "sector_score": best.get("sector_score"),
                "stock_sector_rank": best.get("stock_sector_rank"),
                "stock_market_rank": best.get("stock_market_rank"),
                "score_before_industry_flow": best.get("score_before_industry_flow"),
                "industry_flow_available": best.get("industry_flow_available"),
                "industry_flow_matched": best.get("industry_flow_matched"),
                "industry_flow_rank": best.get("industry_flow_rank"),
                "industry_flow_rank_total": best.get("industry_flow_rank_total"),
                "industry_flow_net_yi": best.get("industry_flow_net_yi"),
                "industry_flow_adjustment": best.get("industry_flow_adjustment"),
                "industry_flow_source": best.get("industry_flow_source"),
                "industry_flow_generated_at": best.get("industry_flow_generated_at"),
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
            "stock_universe": list(scanner.configured_stock_universe()) if hasattr(scanner, "configured_stock_universe") else parsed.get("stock_universe", []),
            "stock_universe_label": scanner.friendly_stock_universe(scanner.configured_stock_universe()) if hasattr(scanner, "configured_stock_universe") else parsed.get("stock_universe_label", ""),
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
        B1_CACHE_FILE.write_text(json_text + "\n", encoding="utf-8")
        MULTI_STRATEGY_CACHE_FILE.write_text(json_text + "\n", encoding="utf-8")
        with API_RESPONSE_LOCK:
            API_RESPONSE_CACHE.pop(PRACTICE_CANDIDATES_CACHE_KEY, None)
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
        market_ctx = b1_payload.get("market_decision_context")
        market_ctx = dict(market_ctx) if isinstance(market_ctx, dict) else {}
        decision_payload = {
            "summary": summary,
            "actions": [],
            "model": "SYSTEM_SCHEDULE",
            "provider": "dashboard",
            "error": error,
        }
        if market_ctx:
            decision_payload["market_guidance"] = market_ctx
        log_entry = {
            "time": trader.now_ts() if hasattr(trader, "now_ts") else datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "b1_generated_at": generated_at,
            "trade_allowed": trade_allowed,
            "trade_reason": trade_reason,
            "decision": decision_payload,
            "executed": [],
        }
        if market_ctx:
            log_entry["market_decision_context"] = market_ctx
        for key in ("schedule_slot", "schedule_run_kind", "schedule_triggered_at"):
            if b1_payload.get(key):
                log_entry[key] = b1_payload.get(key)
        if hasattr(trader, "record_decision_log_entry"):
            trader.record_decision_log_entry(log_entry, mark_b1_done=mark_b1_done)
    except Exception as exc:
        print(f"[WARN] 写入实战页面决策日志失败: {type(exc).__name__}: {exc}", flush=True)


def run_practice_decision_logged(b1_payload: dict[str, Any], *, record_start: bool = False) -> dict[str, Any]:
    payload = normalize_b1_payload_for_trader(b1_payload)
    try:
        trader = get_trader_module()
        if hasattr(trader, "refresh_market_strategy_context_for_b1"):
            refreshed_ctx = trader.refresh_market_strategy_context_for_b1(payload)
            payload["market_decision_context"] = trader.compact_market_strategy_context(refreshed_ctx)
            with API_RESPONSE_LOCK:
                API_RESPONSE_CACHE.pop("niuniu_practice", None)
                API_RESPONSE_CACHE.pop(PRACTICE_FAST_CACHE_KEY, None)
    except Exception as exc:
        print(f"[WARN] 定时选股盘面标签刷新失败: {type(exc).__name__}: {exc}", flush=True)
    item_count = len(payload.get("items") or [])
    slot_note = ""
    if payload.get("schedule_slot"):
        kind_label = "补跑" if payload.get("schedule_run_kind") == "catchup" else "定时"
        slot_note = f"（计划{str(payload.get('schedule_slot'))[-5:]}{kind_label}）"
    if not item_count:
        record_practice_decision_event(
            payload,
            f"选股完成{slot_note}但没有候选股，继续检查已有持仓的原策略退出规则。",
            f"选股完成{slot_note}：0只候选，开始持仓退出检查",
        )
    elif record_start:
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
            print(f"[WARN] 实战页面决策失败: {type(exc).__name__}: {exc}", flush=True)
    if len(PRACTICE_DECISION_KEYS) > 20:
        PRACTICE_DECISION_KEYS.clear()
    threading.Thread(target=_worker, name="niuniu-practice-decision", daemon=True).start()

def load_practice_candidates_cache() -> dict[str, Any]:
    errors: list[str] = []
    for cache_file in (MULTI_STRATEGY_CACHE_FILE, B1_CACHE_FILE):
        try:
            if not cache_file.exists():
                continue
            parsed = json.loads(cache_file.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError(f"候选缓存格式无效：{cache_file}")
            items = parsed.get("items") or parsed.get("candidates") or []
            return {
                **parsed,
                "generated_at": parsed.get("generated_at", ""),
                "count": parsed.get("count", len(items)),
                "items": items,
            }
        except (OSError, ValueError) as exc:
            errors.append(f"{cache_file.name}: {exc}")
    if errors:
        return {"error": "; ".join(errors), "items": [], "count": 0, "generated_at": ""}
    return {"items": [], "count": 0, "generated_at": ""}


def summarize_b1_scan_failure(stderr: str, stdout: str, limit: int = 900) -> str:
    """Keep the scanner stage and final exception without leaking a full traceback."""
    raw = (stderr or stdout or "").strip()
    if not raw:
        return "扫描进程未返回错误详情"
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    stage = next((line for line in reversed(lines) if line.startswith("Step ")), "")
    final_line = lines[-1]
    detail = f"{stage}；{final_line}" if stage and stage != final_line else final_line
    if len(detail) > limit:
        detail = detail[: max(0, limit - 3)] + "..."
    return detail


def _trigger_b1_scan_unlocked(
    force: bool = False,
    decision_mode: str = "async",
    *,
    schedule_slot: str = "",
    schedule_run_kind: str = "",
) -> dict[str, Any]:
    import subprocess, sys
    script = Path(os.environ.get("DASHBOARD_B1_SCANNER", ENTRYPOINT_DIR / "multi_strategy_screen.py")).expanduser()
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
                B1_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            if decision_mode == "sync":
                cache["decision_result"] = run_practice_decision_logged(cache, record_start=True)
            elif decision_mode == "async":
                maybe_run_practice_decision_async(cache)
            return cache
        error_detail = summarize_b1_scan_failure(result.stderr, result.stdout)
        print(f"[WARN] B1 scan failed: {error_detail}", file=sys.stderr, flush=True)
        return {"error": error_detail, "items": [], "count": 0, "generated_at": "", "running": False}
    except subprocess.TimeoutExpired:
        return {"error": f"扫描超时（{B1_SCAN_TIMEOUT_SECONDS}s）", "items": [], "count": 0, "generated_at": "", "running": False}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "items": [], "count": 0, "generated_at": "", "running": False}


def trigger_b1_scan(
    force: bool = False,
    decision_mode: str = "async",
    *,
    schedule_slot: str = "",
    schedule_run_kind: str = "",
) -> dict[str, Any]:
    if not B1_FULL_SCAN_LOCK.acquire(blocking=False):
        return {
            "error": "已有选股扫描正在运行，请等待当前扫描完成",
            "items": [],
            "count": 0,
            "generated_at": "",
            "running": True,
            "busy": True,
        }
    try:
        return _trigger_b1_scan_unlocked(
            force,
            decision_mode,
            schedule_slot=schedule_slot,
            schedule_run_kind=schedule_run_kind,
        )
    finally:
        B1_FULL_SCAN_LOCK.release()


def practice_manual_cycle_status() -> dict[str, Any]:
    with PRACTICE_MANUAL_CYCLE_STATE_LOCK:
        return {
            field: PRACTICE_MANUAL_CYCLE_STATE[field]
            for field in PRACTICE_MANUAL_CYCLE_PUBLIC_FIELDS
            if field in PRACTICE_MANUAL_CYCLE_STATE
        }


def _set_practice_manual_cycle_state(**updates: Any) -> dict[str, Any]:
    with PRACTICE_MANUAL_CYCLE_STATE_LOCK:
        PRACTICE_MANUAL_CYCLE_STATE.update(updates)
        return dict(PRACTICE_MANUAL_CYCLE_STATE)


def recent_practice_candidates_for_manual_cycle() -> dict[str, Any] | None:
    if PRACTICE_MANUAL_SCAN_REUSE_SECONDS <= 0:
        return None
    cache = load_practice_candidates_cache()
    if cache.get("error"):
        return None
    generated_at = str(cache.get("generated_at") or "")[:19]
    try:
        generated_dt = datetime.strptime(generated_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    age_seconds = (datetime.now() - generated_dt).total_seconds()
    if age_seconds < -60 or age_seconds > PRACTICE_MANUAL_SCAN_REUSE_SECONDS:
        return None
    return {
        **cache,
        "manual_scan_reused": True,
        "manual_scan_age_seconds": round(max(0.0, age_seconds), 1),
    }


def _run_practice_manual_cycle() -> None:
    try:
        _set_practice_manual_cycle_state(stage="screening", stage_label="正在检查候选并生成盘面评价")
        cache = recent_practice_candidates_for_manual_cycle()
        if cache is None:
            cache = trigger_b1_scan(force=True, decision_mode="none")
        if cache.get("error"):
            raise RuntimeError(str(cache.get("error")))

        _set_practice_manual_cycle_state(
            stage="trading",
            stage_label="正在执行买卖策略",
            candidate_count=int(cache.get("count") or 0),
            generated_at=str(cache.get("generated_at") or ""),
            manual_scan_reused=bool(cache.get("manual_scan_reused")),
        )
        decision_result = run_practice_decision_logged(cache, record_start=True)
        _set_practice_manual_cycle_state(
            running=False,
            stage="completed",
            stage_label="本轮选股及买卖已完成",
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            decision_result=decision_result,
            error="",
        )
    except Exception as exc:
        _set_practice_manual_cycle_state(
            running=False,
            stage="error",
            stage_label="本轮执行失败",
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        invalidate_api_cache(PRACTICE_CANDIDATES_CACHE_KEY, "niuniu_practice", PRACTICE_FAST_CACHE_KEY)
        PRACTICE_MANUAL_CYCLE_LOCK.release()


def start_practice_manual_cycle() -> dict[str, Any]:
    if not PRACTICE_MANUAL_CYCLE_LOCK.acquire(blocking=False):
        return {**practice_manual_cycle_status(), "accepted": False}
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = _set_practice_manual_cycle_state(
        running=True,
        stage="starting",
        stage_label="正在启动",
        started_at=started_at,
        finished_at="",
        generated_at="",
        candidate_count=0,
        manual_scan_reused=False,
        decision_result=None,
        error="",
    )
    threading.Thread(
        target=_run_practice_manual_cycle,
        name="niuniu-practice-manual-cycle",
        daemon=True,
    ).start()
    return {**status, "accepted": True}


def b1_cache_generated_for_slot(slot_key: str) -> bool:
    try:
        if not B1_CACHE_FILE.exists():
            return False
        generated_at = (
            json.loads(B1_CACHE_FILE.read_text(encoding="utf-8")).get("generated_at") or ""
        )[:16]
        return generated_at == slot_key
    except Exception:
        return False


def _b1_schedule_now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_b1_schedule_state_unlocked() -> dict[str, Any]:
    try:
        state = json.loads(B1_SCHEDULE_STATE_FILE.read_text(encoding="utf-8"))
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
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
            API_RESPONSE_CACHE.pop(PRACTICE_CANDIDATES_CACHE_KEY, None)
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
                        API_RESPONSE_CACHE.pop(PRACTICE_FAST_CACHE_KEY, None)
                        API_RESPONSE_CACHE.pop("practice_benchmarks", None)
        except Exception as exc:
            print(f"[WARN] 延迟成交检查失败: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(max(1.0, PENDING_DECISION_POLL_SECONDS))


def practice_equity_heartbeat_loop(
    *,
    stop_event: threading.Event | None = None,
    poll_seconds: float = PRACTICE_EQUITY_HEARTBEAT_POLL_SECONDS,
) -> None:
    """Keep minute equity snapshots flowing even when no dashboard is open."""

    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        record_practice_equity_heartbeat()
        if stop_event.wait(max(1.0, float(poll_seconds))):
            return


def start_practice_equity_heartbeat() -> None:
    global PRACTICE_EQUITY_HEARTBEAT_THREAD
    if PRACTICE_EQUITY_HEARTBEAT_THREAD and PRACTICE_EQUITY_HEARTBEAT_THREAD.is_alive():
        return
    PRACTICE_EQUITY_HEARTBEAT_THREAD = threading.Thread(
        target=practice_equity_heartbeat_loop,
        name="practice-equity-heartbeat",
        daemon=True,
    )
    PRACTICE_EQUITY_HEARTBEAT_THREAD.start()
    print("Practice equity heartbeat enabled: 60s", flush=True)


def is_market_breadth_sampling_window(now: datetime | None = None) -> bool:
    """Return whether Beijing time is inside an A-share quote session."""

    current = now or current_cn_datetime()
    return (
        is_a_share_trading_day_for_dashboard(current)
        and is_market_breadth_session_timestamp(current)
    )


def _daily_payload_date_keys(payload: dict[str, Any]) -> set[str]:
    keys = {
        str(payload.get(field) or "")[:10]
        for field in ("date", "retention_date", "generated_at")
        if str(payload.get(field) or "")[:10]
    }
    for raw in payload.get("samples") or []:
        if not isinstance(raw, dict):
            continue
        value = str(raw.get("generated_at") or "")[:10]
        if value:
            keys.add(value)
    return keys


def _empty_market_breadth_history(day: str) -> dict[str, Any]:
    return roll_market_breadth_history(
        None,
        day,
        interval_seconds=MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS,
    )


def _empty_industry_flow_history(day: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "date": day,
        "interval_seconds": INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS,
        "samples": [],
    }


def _empty_money_flow_snapshot(day: str) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "metric": "industry_main_net_flow",
        "metric_label": "今日主力净额",
        "retention_date": day,
        "inflow": [],
        "outflow": [],
    }


def reset_daily_market_histories(now: datetime | None = None) -> bool:
    """Roll breadth history and clear current-day-only market-flow data."""

    day = current_cn_date_key(now)
    changed = False
    with MARKET_BREADTH_HISTORY_LOCK:
        history = read_json_cache(MARKET_BREADTH_HISTORY_FILE, None)
        rolled = roll_market_breadth_history(
            history,
            day,
            interval_seconds=MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS,
        )
        if history is not None and rolled != history:
            write_json_cache(MARKET_BREADTH_HISTORY_FILE, rolled)
            changed = True
    with INDUSTRY_FLOW_HISTORY_LOCK:
        history = read_json_cache(INDUSTRY_FLOW_HISTORY_FILE, None)
        if history is not None and _daily_payload_date_keys(history) != {day}:
            write_json_cache(INDUSTRY_FLOW_HISTORY_FILE, _empty_industry_flow_history(day))
            changed = True
        snapshot = read_json_cache(MONEY_FLOW_SNAPSHOT_FILE, None)
        if snapshot is not None and _daily_payload_date_keys(snapshot) != {day}:
            write_json_cache(MONEY_FLOW_SNAPSHOT_FILE, _empty_money_flow_snapshot(day))
            changed = True
    if changed:
        invalidate_api_cache("market_breadth", "money_flow")
        invalidate_api_cache_prefix("industry_flow")
    return changed


def seconds_until_next_cn_midnight(now: datetime | None = None) -> float:
    current = now or current_cn_datetime()
    next_midnight = datetime.combine(
        current.date() + timedelta(days=1),
        datetime.min.time(),
        tzinfo=current.tzinfo,
    )
    return max(0.1, (next_midnight - current).total_seconds())


def daily_market_history_reset_loop(
    *,
    stop_event: threading.Event | None = None,
) -> None:
    """Roll or clear daily chart histories at each Beijing midnight."""

    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        if stop_event.wait(seconds_until_next_cn_midnight()):
            return
        reset_daily_market_histories(current_cn_datetime())


def start_daily_market_history_reset() -> None:
    global DAILY_MARKET_HISTORY_RESET_THREAD
    reset_daily_market_histories(current_cn_datetime())
    if (
        DAILY_MARKET_HISTORY_RESET_THREAD
        and DAILY_MARKET_HISTORY_RESET_THREAD.is_alive()
    ):
        return
    DAILY_MARKET_HISTORY_RESET_THREAD = threading.Thread(
        target=daily_market_history_reset_loop,
        name="daily-market-history-reset",
        daemon=True,
    )
    DAILY_MARKET_HISTORY_RESET_THREAD.start()
    print("Daily market history reset enabled: 00:00 Asia/Shanghai", flush=True)


def load_market_breadth_samples(
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    resolved_now = now or current_cn_datetime()
    current_day = current_cn_date_key(resolved_now)
    reset_daily_market_histories(resolved_now)
    with MARKET_BREADTH_HISTORY_LOCK:
        history = read_json_cache(MARKET_BREADTH_HISTORY_FILE, None) or {}
        samples: list[dict[str, Any]] = []
        for raw in history.get("samples") or []:
            compact = compact_market_breadth_sample(raw if isinstance(raw, dict) else None)
            if (
                compact is not None
                and compact["generated_at"][:10] == current_day
                and is_market_breadth_session_timestamp(compact["generated_at"])
            ):
                samples.append(compact)
        return samples


def load_previous_market_turnover_history(
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    resolved_now = now or current_cn_datetime()
    current_day = current_cn_date_key(resolved_now)
    reset_daily_market_histories(resolved_now)
    with MARKET_BREADTH_HISTORY_LOCK:
        history = read_json_cache(MARKET_BREADTH_HISTORY_FILE, None) or {}
        return compact_previous_turnover_history(
            history.get("previous_turnover")
            if isinstance(history.get("previous_turnover"), dict)
            else None,
            before_date=current_day,
        )


def record_market_breadth_sample(
    snapshot: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Persist one complete market snapshot without inventing missing values."""

    resolved_now = now or current_cn_datetime()
    reset_daily_market_histories(resolved_now)
    compact = compact_market_breadth_sample(snapshot)
    if (
        compact is None
        or compact["generated_at"][:10] != current_cn_date_key(resolved_now)
        or not is_market_breadth_session_timestamp(compact["generated_at"])
    ):
        return load_market_breadth_samples(now=resolved_now)
    with MARKET_BREADTH_HISTORY_LOCK:
        history = read_json_cache(MARKET_BREADTH_HISTORY_FILE, None) or {}
        updated = append_market_breadth_sample(
            history,
            snapshot,
            interval_seconds=MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS,
        )
        if updated != history and updated.get("samples"):
            write_json_cache(MARKET_BREADTH_HISTORY_FILE, updated)
        return [
            sample
            for sample in (updated.get("samples") or [])
            if isinstance(sample, dict)
        ]


def _market_breadth_failure_payload(
    error: Exception,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    samples = load_market_breadth_samples(now=now)
    if not samples:
        return build_market_breadth_payload({
            "error": f"{type(error).__name__}: {error}",
        })
    fallback = dict(samples[-1])
    fallback.update({
        "stale_cache": True,
        "error": f"{type(error).__name__}: {error}",
    })
    return build_market_breadth_payload(
        fallback,
        history_samples=samples,
        previous_turnover=load_previous_market_turnover_history(now=now),
        interval_seconds=MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS,
    )


def _cached_market_breadth_payload(now: datetime) -> dict[str, Any] | None:
    """Reuse a fresh sample, or the last close while the market is not sampling."""

    samples = load_market_breadth_samples(now=now)
    if not samples:
        return None
    latest = samples[-1]
    try:
        latest_time = datetime.strptime(
            str(latest.get("generated_at") or ""),
            "%Y-%m-%d %H:%M:%S",
        )
    except ValueError:
        return None
    same_day = latest_time.date() == now.date()
    age_seconds = (now - latest_time).total_seconds()
    fresh = (
        same_day
        and -120 <= age_seconds < max(5, MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS - 5)
    )
    if is_market_breadth_sampling_window(now) and not fresh:
        return None
    return build_market_breadth_payload(
        latest,
        history_samples=samples,
        previous_turnover=load_previous_market_turnover_history(now=now),
        interval_seconds=MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS,
    )


def produce_market_breadth_data() -> dict[str, Any]:
    """Fetch, validate, persist, and project one market-breadth observation."""

    with MARKET_BREADTH_REFRESH_LOCK:
        current = current_cn_datetime()
        reset_daily_market_histories(current)
        cached = _cached_market_breadth_payload(current)
        if cached is not None:
            return cached
        if not is_market_breadth_sampling_window(current):
            return build_market_breadth_payload(
                {},
                history_samples=[],
                previous_turnover=load_previous_market_turnover_history(now=current),
                interval_seconds=MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS,
            )
        try:
            snapshot = fetch_tencent_market_breadth()
            samples = record_market_breadth_sample(snapshot, now=current)
            compact = compact_market_breadth_sample(snapshot)
            if (
                compact is None
                or compact["generated_at"][:10] != current_cn_date_key(current)
            ):
                return build_market_breadth_payload(
                    {},
                    history_samples=samples,
                    previous_turnover=load_previous_market_turnover_history(
                        now=current
                    ),
                    interval_seconds=MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS,
                )
            return build_market_breadth_payload(
                snapshot,
                history_samples=samples,
                previous_turnover=load_previous_market_turnover_history(now=current),
                interval_seconds=MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS,
            )
        except Exception as exc:
            return _market_breadth_failure_payload(exc, now=current)


def refresh_market_breadth_sample() -> bool:
    payload = produce_market_breadth_data()
    latest = payload.get("latest") if isinstance(payload.get("latest"), dict) else {}
    recorded = bool(latest and not payload.get("error"))
    if recorded:
        invalidate_api_cache("market_breadth")
    return recorded


def market_breadth_sampling_loop(
    *,
    stop_event: threading.Event | None = None,
    poll_seconds: float | None = None,
) -> None:
    """Collect current-day breadth curves even when the index page is closed."""

    stop_event = stop_event or threading.Event()
    next_due = time.monotonic()
    while not stop_event.is_set():
        interval = max(
            60.0,
            float(
                MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS
                if poll_seconds is None
                else poll_seconds
            ),
        )
        if is_market_breadth_sampling_window():
            try:
                refresh_market_breadth_sample()
            except Exception as exc:
                print(f"[WARN] 市场宽度采样失败: {type(exc).__name__}: {exc}", flush=True)
        next_due += interval
        now = time.monotonic()
        if next_due <= now:
            skipped_intervals = int((now - next_due) // interval) + 1
            next_due += skipped_intervals * interval
        if stop_event.wait(max(0.1, next_due - now)):
            return


def start_market_breadth_sampler() -> None:
    global MARKET_BREADTH_SAMPLER_THREAD
    if MARKET_BREADTH_SAMPLER_THREAD and MARKET_BREADTH_SAMPLER_THREAD.is_alive():
        return
    MARKET_BREADTH_SAMPLER_THREAD = threading.Thread(
        target=market_breadth_sampling_loop,
        name="market-breadth-sampler",
        daemon=True,
    )
    MARKET_BREADTH_SAMPLER_THREAD.start()
    print(
        f"Market breadth sampler enabled: {MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS}s",
        flush=True,
    )


def is_industry_flow_sampling_window(now: datetime | None = None) -> bool:
    """Return whether Beijing time is inside either fixed sampling session."""

    current = now or current_cn_datetime()
    if not is_a_share_trading_day_for_dashboard(current):
        return False
    return is_industry_flow_session_timestamp(
        current,
        sampling_windows=INDUSTRY_FLOW_SAMPLING_WINDOWS,
    )


def _filter_industry_flow_session_samples(
    samples: list[Any],
    *,
    retention_day: str = "",
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for item in samples:
        if not isinstance(item, dict):
            continue
        generated_at = str(item.get("generated_at") or "")
        try:
            sample_time = datetime.strptime(generated_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if retention_day and generated_at[:10] != retention_day:
            continue
        if is_industry_flow_sampling_window(sample_time):
            filtered.append(item)
    return filtered


def load_industry_flow_samples(
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    resolved_now = now or current_cn_datetime()
    current_day = current_cn_date_key(resolved_now)
    reset_daily_market_histories(resolved_now)
    with INDUSTRY_FLOW_HISTORY_LOCK:
        history = read_json_cache(INDUSTRY_FLOW_HISTORY_FILE, None) or {}
        return _filter_industry_flow_session_samples(
            history.get("samples") or [],
            retention_day=current_day,
        )


def record_industry_flow_sample(
    money_flow: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Persist one valid sample without replacing earlier same-day observations."""

    resolved_now = now or current_cn_datetime()
    current_day = current_cn_date_key(resolved_now)
    reset_daily_market_histories(resolved_now)
    generated_at = str(money_flow.get("generated_at") or "")
    try:
        sample_time = datetime.strptime(generated_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return load_industry_flow_samples(now=resolved_now)
    if generated_at[:10] != current_day or not is_industry_flow_sampling_window(sample_time):
        return load_industry_flow_samples(now=resolved_now)

    with INDUSTRY_FLOW_HISTORY_LOCK:
        history = read_json_cache(INDUSTRY_FLOW_HISTORY_FILE, None) or {}
        existing_samples = _filter_industry_flow_session_samples(
            history.get("samples") or [],
            retention_day=current_day,
        )
        same_day_samples = [
            item for item in existing_samples
            if str(item.get("generated_at") or "")[:10] == generated_at[:10]
        ]
        if same_day_samples:
            latest_time = datetime.strptime(
                str(same_day_samples[-1].get("generated_at") or ""),
                "%Y-%m-%d %H:%M:%S",
            )
            elapsed_seconds = (sample_time - latest_time).total_seconds()
            if 0 < elapsed_seconds < INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS:
                return existing_samples
        updated = append_industry_flow_sample(
            history,
            money_flow,
            interval_seconds=INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS,
        )
        if updated != history and updated.get("samples"):
            write_json_cache(INDUSTRY_FLOW_HISTORY_FILE, updated)
        return _filter_industry_flow_session_samples(
            updated.get("samples") or [],
            retention_day=current_day,
        )


def fetch_and_record_money_flow(
    *,
    force_refresh: bool = False,
    timeout: int = 120,
    now: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch one money-flow snapshot and immediately preserve valid history."""

    money_flow = run_dashboard_helper(
        "money_flow_dashboard_api.py",
        {"inflow": [], "outflow": []},
        timeout=timeout,
        args=("--force-refresh",) if force_refresh else (),
    )
    return money_flow, record_industry_flow_sample(money_flow, now=now)


def refresh_industry_flow_sample() -> bool:
    money_flow, samples = fetch_and_record_money_flow(
        force_refresh=True,
        # Leave enough time in the minute for the next wall-clock sample even
        # when the upstream request stalls.
        timeout=50,
    )
    generated_at = str(money_flow.get("generated_at") or "")
    recorded = bool(samples and str(samples[-1].get("generated_at") or "") == generated_at)
    if recorded:
        invalidate_api_cache("money_flow", "industry_flow")
    return recorded


def industry_flow_sampling_loop(
    *,
    stop_event: threading.Event | None = None,
    poll_seconds: float | None = None,
) -> None:
    """Collect periodic industry-flow snapshots even when the page is closed."""

    stop_event = stop_event or threading.Event()
    next_due = time.monotonic()
    while not stop_event.is_set():
        interval = max(
            60.0,
            float(INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS if poll_seconds is None else poll_seconds),
        )
        if is_industry_flow_sampling_window():
            try:
                refresh_industry_flow_sample()
            except Exception as exc:
                print(f"[WARN] 行业资金流采样失败: {type(exc).__name__}: {exc}", flush=True)
        next_due += interval
        now = time.monotonic()
        if next_due <= now:
            skipped_intervals = int((now - next_due) // interval) + 1
            next_due += skipped_intervals * interval
        if stop_event.wait(max(0.1, next_due - now)):
            return


def start_industry_flow_sampler() -> None:
    global INDUSTRY_FLOW_SAMPLER_THREAD
    if INDUSTRY_FLOW_SAMPLER_THREAD and INDUSTRY_FLOW_SAMPLER_THREAD.is_alive():
        return
    INDUSTRY_FLOW_SAMPLER_THREAD = threading.Thread(
        target=industry_flow_sampling_loop,
        name="industry-flow-sampler",
        daemon=True,
    )
    INDUSTRY_FLOW_SAMPLER_THREAD.start()
    print(f"Industry flow sampler enabled: {INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS}s", flush=True)


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


def _practice_market_summary_records() -> list[dict[str, Any]]:
    data = push_history.query_messages(category="market_monitor", limit=100)
    return [record for record in (data.get("records") or []) if isinstance(record, dict)]


def get_practice_market_summary_status() -> dict[str, Any]:
    return practice_market_summary_impl.summary_status(
        _practice_market_summary_records(),
        PRACTICE_MARKET_SUMMARY_FILE,
        current_cn_datetime(),
    )


def fetch_practice_realtime_market_snapshot(now: datetime) -> dict[str, Any]:
    """Force-refresh current A-share channels in isolated helper processes."""
    jobs = {
        "indices": ("indices_dashboard_api.py", {"items": []}),
        "sectors": (
            "sectors_dashboard_api.py",
            {
                "gain_top": [],
                "loss_top": [],
                "industry_gain_top": [],
                "industry_loss_top": [],
                "concept_gain_top": [],
                "concept_loss_top": [],
                "items": [],
            },
        ),
        "money_flow": ("money_flow_dashboard_api.py", {"inflow": [], "outflow": []}),
    }
    payloads: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {}
        for key, (script_name, fallback) in jobs.items():
            if key == "money_flow":
                futures[key] = pool.submit(
                    fetch_and_record_money_flow,
                    force_refresh=True,
                    timeout=120,
                    now=now,
                )
                continue
            futures[key] = pool.submit(
                run_dashboard_helper,
                script_name,
                fallback,
                120,
                ("--force-refresh",),
            )
        for key, future in futures.items():
            try:
                result = future.result()
                payloads[key] = result[0] if key == "money_flow" else result
            except Exception as exc:
                payloads[key] = {**jobs[key][1], "error": f"{type(exc).__name__}: {exc}"}
    return practice_market_summary_impl.build_realtime_market_snapshot(
        payloads.get("indices") or {},
        payloads.get("sectors") or {},
        payloads.get("money_flow") or {},
        now,
    )


def generate_practice_market_summary() -> dict[str, Any]:
    return practice_market_summary_impl.generate_and_store_summary(
        _practice_market_summary_records(),
        PRACTICE_MARKET_SUMMARY_FILE,
        current_cn_datetime(),
        realtime_snapshot_provider=fetch_practice_realtime_market_snapshot,
        require_realtime=True,
    )


def _store_api_cache_payload(cache_key: str, payload: bytes, generation: int) -> bool:
    return response_cache_impl.store_payload(
        cache_key,
        payload,
        generation,
        entries=API_RESPONSE_CACHE,
        entries_lock=API_RESPONSE_LOCK,
        key_locks=API_CACHE_KEY_LOCKS,
        generations=API_CACHE_KEY_GENERATIONS,
        max_entries=API_CACHE_MAX_ENTRIES,
    )


def _refresh_api_cache(cache_key: str, producer, generation: int, key_lock: threading.Lock) -> None:
    response_cache_impl.refresh_payload(
        cache_key,
        producer,
        generation,
        key_lock,
        store=_store_api_cache_payload,
        warn=lambda message: print(message, file=sys.stderr),
    )


def cache_get_json(cache_key: str, ttl: int, producer) -> tuple[bytes, bool]:
    return response_cache_impl.get_json(
        cache_key,
        ttl,
        producer,
        entries=API_RESPONSE_CACHE,
        entries_lock=API_RESPONSE_LOCK,
        key_locks=API_CACHE_KEY_LOCKS,
        generations=API_CACHE_KEY_GENERATIONS,
        stale_while_refresh_seconds=API_STALE_WHILE_REFRESH_SECONDS,
        store=_store_api_cache_payload,
        refresh=_refresh_api_cache,
    )


def seed_api_cache_from_json_file(cache_key: str, path: Path, ttl: int, transform=None) -> bool:
    """Seed a cold in-memory cache from the latest durable dashboard snapshot.

    The entry is deliberately marked just past its TTL: the first request gets
    useful data immediately while ``cache_get_json`` refreshes it in the
    background through the normal producer.
    """
    return response_cache_impl.seed_from_json_file(
        cache_key,
        path,
        ttl,
        entries=API_RESPONSE_CACHE,
        entries_lock=API_RESPONSE_LOCK,
        transform=transform,
    )


def invalidate_api_cache(*cache_keys: str) -> None:
    response_cache_impl.invalidate(
        cache_keys,
        entries=API_RESPONSE_CACHE,
        entries_lock=API_RESPONSE_LOCK,
        generations=API_CACHE_KEY_GENERATIONS,
    )


def invalidate_api_cache_prefix(prefix: str) -> None:
    """Invalidate every in-process cache entry under one bounded API family."""
    response_cache_impl.invalidate_prefix(
        prefix,
        entries=API_RESPONSE_CACHE,
        entries_lock=API_RESPONSE_LOCK,
        generations=API_CACHE_KEY_GENERATIONS,
    )


def cached_json_data(cache_key: str, ttl: int, producer, fallback: dict[str, Any]) -> dict[str, Any]:
    payload, _ = cache_get_json(cache_key, ttl, producer)
    return response_cache_impl.decode_json_data(payload, fallback)


def iwencai_dragon_tiger_archive_dir() -> Path:
    return IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE.parent / "iwencai_dragon_tiger"


def iwencai_dragon_tiger_snapshot_version(
    trade_date: str,
    *,
    include_latest: bool,
) -> int:
    paths = [dragon_tiger_archive_path(iwencai_dragon_tiger_archive_dir(), trade_date)]
    if include_latest:
        paths.append(IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE)
    versions = []
    for path in paths:
        try:
            versions.append(path.stat().st_mtime_ns)
        except OSError:
            continue
    return max(versions, default=0)


def produce_iwencai_dragon_tiger_data(
    trade_date: str,
    *,
    page: int,
    limit: int,
    allow_latest_snapshot: bool,
) -> dict[str, Any]:
    use_snapshot = page == 1 and limit == IWENCAI_DRAGON_TIGER_DEFAULT_LIMIT
    if use_snapshot:
        exact_latest = read_dragon_tiger_snapshot(
            IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE,
            trade_date=trade_date,
        )
        if allow_latest_snapshot and exact_latest:
            exact_latest["stale"] = False
            exact_latest["scheduled_refresh_time"] = "18:00"
            return exact_latest
        archived = read_dragon_tiger_archive(
            iwencai_dragon_tiger_archive_dir(),
            trade_date=trade_date,
        )
        if archived:
            archived["stale"] = False
            archived["scheduled_refresh_time"] = "18:00"
            return archived
        if exact_latest:
            exact_latest["stale"] = False
            exact_latest["scheduled_refresh_time"] = "18:00"
            return exact_latest
        if allow_latest_snapshot:
            latest = read_dragon_tiger_snapshot(IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE)
            if latest:
                latest["stale"] = str(latest.get("date") or "") != trade_date
                latest["requested_date"] = trade_date
                latest["scheduled_refresh_time"] = "18:00"
                return latest

    payload = fetch_dragon_tiger(trade_date, page=page, limit=limit)
    payload["scheduled_refresh_time"] = "18:00"
    if use_snapshot:
        if write_dragon_tiger_archive(iwencai_dragon_tiger_archive_dir(), payload):
            payload["archive_saved"] = True
        if allow_latest_snapshot and write_dragon_tiger_snapshot(
            IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE,
            payload,
        ):
            payload["snapshot_saved"] = True
    return payload


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


def produce_money_flow_data() -> dict[str, Any]:
    money_flow, _samples = fetch_and_record_money_flow(timeout=120)
    return money_flow


def produce_industry_flow_data() -> dict[str, Any]:
    current = current_cn_datetime()
    current_day = current_cn_date_key(current)
    reset_daily_market_histories(current)
    money_flow = cached_json_data(
        "money_flow",
        API_TTLS["money_flow"],
        produce_money_flow_data,
        {"inflow": [], "outflow": []},
    )
    history_samples = record_industry_flow_sample(money_flow, now=current)
    if str(money_flow.get("generated_at") or "")[:10] != current_day:
        money_flow = {
            "schema_version": 2,
            "metric": "industry_main_net_flow",
            "metric_label": "今日主力净额",
            "inflow": [],
            "outflow": [],
        }
    return build_industry_flow_payload(
        money_flow,
        side_limit=INDUSTRY_FLOW_SIDE_LIMIT,
        history_samples=history_samples,
        sample_interval_seconds=INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS,
        playback_speed=INDUSTRY_FLOW_PLAYBACK_SPEED,
        sampling_windows=INDUSTRY_FLOW_SAMPLING_WINDOWS,
    )


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


def is_truthy_header(value: str | None) -> bool:
    return security_impl.is_truthy_header(value)


def _parse_ip_network(value: str) -> ipaddress._BaseNetwork | None:
    return security_impl.parse_ip_network(value)


def is_trusted_proxy_ip(ip_text: str) -> bool:
    return security_impl.is_trusted_proxy_ip(
        ip_text,
        TRUSTED_PROXY_CIDRS,
        parse_network=_parse_ip_network,
    )


def first_forwarded_ip(*headers: str | None) -> str:
    return security_impl.first_forwarded_ip(*headers)


def clamp_limit(raw: str | None, default: int = API_DEFAULT_LIMIT) -> int:
    return security_impl.clamp_limit(
        raw,
        default=default,
        maximum=API_LIMIT_MAX,
    )

def clamp_offset(raw: str | None) -> int:
    return security_impl.clamp_offset(raw, maximum=API_OFFSET_MAX)


def is_secret_config_key(key: str) -> bool:
    return bool(SECRET_KEY_RE.search(str(key or "")))


def display_secret(value: Any) -> str:
    return "已设置，留空保持不变" if str(value or "") else "未设置"


def display_secret_state(value: Any) -> str:
    return "已设置" if str(value or "") else "未设置"


def parse_env_file(path: Path | None = None, *, include_container_overrides: bool = True) -> dict[str, str]:
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
    if include_container_overrides:
        return apply_container_runtime_overrides(values, PROJECT_ROOT)
    return values


# Some legacy service definitions invoke this module directly instead of using
# run-dashboard.sh. Preserve explicit process overrides, otherwise load the
# admin credential from the private dashboard.env file here as well.
if "DASHBOARD_ADMIN_PASSWORD" not in os.environ:
    ADMIN_PASSWORD = str(
        parse_env_file(include_container_overrides=False).get("DASHBOARD_ADMIN_PASSWORD") or ""
    ).strip()


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
    if kind == "playback_speed":
        speed = _industry_flow_playback_speed_value(value)
        try:
            requested = float(value)
        except (TypeError, ValueError):
            requested = -1
        if requested not in INDUSTRY_FLOW_PLAYBACK_SPEED_OPTIONS:
            allowed = "、".join(f"{item:g}x" for item in INDUSTRY_FLOW_PLAYBACK_SPEED_OPTIONS)
            raise ValueError(f"资金流播放速度必须是 {allowed} 之一")
        return f"{speed:g}"
    if kind in {"max_tokens", "context_length"}:
        return normalize_context_length_update(value)
    if kind == "api_mode":
        normalized = value.lower().replace("-", "_") or "auto"
        aliases = {
            "auto": "auto",
            "responses": "responses",
            "response": "responses",
            "chat": "chat",
            "chat_completions": "chat",
            "chat_completion": "chat",
        }
        if normalized not in aliases:
            raise ValueError("API 接口模式必须是 auto、responses 或 chat")
        return aliases[normalized]
    if kind == "time":
        normalized = normalize_hhmm(value)
        if value and not normalized:
            raise ValueError(f"{ENV_CONFIG_BY_NAME.get(name, {}).get('label', name)} 请使用北京时间 HH:MM，例如 14:45")
        return normalized
    if kind == "time_list":
        return normalize_time_list_update(value)
    if kind == "handle_list":
        return normalize_handle_list_update(value)
    if kind == "stock_universe":
        return normalize_stock_universe(value)
    if kind in {"strategy_multi", "strategy_single"}:
        return normalize_strategy_list_update(value)
    if kind == "strategy_source":
        return normalize_strategy_source_update(value)
    if kind == "strategy_suite":
        return normalize_strategy_suite_update(value)
    if kind == "preset_strategy_text":
        return normalize_preset_strategy_text_update(value)
    if kind == "trade_discipline_text":
        return normalize_trade_discipline_text_update(value)
    return value


def write_env_file_values(
    updates: dict[str, str],
    path: Path | None = None,
    *,
    clear_names: set[str] | None = None,
) -> dict[str, Any]:
    with ENV_FILE_WRITE_LOCK:
        return _write_env_file_values_unlocked(
            updates,
            path,
            clear_names=clear_names,
        )


def _write_env_file_values_unlocked(
    updates: dict[str, str],
    path: Path | None = None,
    *,
    clear_names: set[str] | None = None,
) -> dict[str, Any]:
    path = path or DASHBOARD_ENV_FILE
    existing = parse_env_file(path, include_container_overrides=False)
    next_values = dict(existing)
    changed_names: list[str] = []
    requested_clear_names = set(clear_names or set())
    for name in requested_clear_names:
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
            raise ValueError(f"invalid env name: {name}")
    for name, value in updates.items():
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
            raise ValueError(f"invalid env name: {name}")
        if name in requested_clear_names:
            continue
        schema = ENV_CONFIG_BY_NAME.get(name, {"kind": "text"})
        kind = "secret" if schema.get("kind") == "secret" or is_secret_config_key(name) else schema.get("kind", "text")
        if kind == "secret" and not str(value or "").strip():
            continue
        if value == "" and name not in existing and kind not in {"time_list", "stock_universe", "strategy_multi", "strategy_single"}:
            continue
        next_value = normalize_env_update(name, value, kind)
        if existing.get(name) != next_value:
            changed_names.append(name)
        next_values[name] = next_value
    for name in sorted(requested_clear_names):
        if name in next_values or name in os.environ:
            if name not in changed_names:
                changed_names.append(name)
        next_values.pop(name, None)
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
    content = "\n".join(lines).rstrip() + "\n"
    temporary_path = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}.tmp"
    )
    temporary_fd: int | None = None
    try:
        temporary_fd = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(temporary_fd, "w", encoding="utf-8") as stream:
            temporary_fd = None
            stream.write(content)
        temporary_path.replace(path)
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        temporary_path.unlink(missing_ok=True)
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
    "IWENCAI_DRAGON_TIGER_CRON",
    "DASHBOARD_US_MARKET_SUMMARY_CRON",
    "DASHBOARD_MARKET_AUCTION_CRON",
    "DASHBOARD_MARKET_MIDDAY_CRON",
    "DASHBOARD_MARKET_CLOSE_CRON",
    "DASHBOARD_US_RATING_CRON",
}
CRON_TIME_CONFIGS = {
    "IWENCAI_DRAGON_TIGER_CRON": {"day_label": "A股交易日"},
    "DASHBOARD_US_MARKET_SUMMARY_CRON": {"day_label": "A股交易日"},
    "DASHBOARD_MARKET_AUCTION_CRON": {"day_label": "周一至周五"},
    "DASHBOARD_MARKET_MIDDAY_CRON": {"day_label": "周一至周五"},
    "DASHBOARD_MARKET_CLOSE_CRON": {"day_label": "周一至周五"},
    "DASHBOARD_US_RATING_CRON": {"day_label": "每天"},
}
ADMIN_GROUP_NOTES = {
    "牛牛美股": "集中管理 X/推文监控、美股买入评级和隔夜美股盘面总结使用的 Grok 配置。长度默认：上下文 128000 tokens，最大输出 4096 tokens；关闭时隐藏 X/评级相关设置，隔夜美股总结仍会读取已配置的 Grok 参数。",
    "消息面预检模型": "用于 A 股候选股最近 3 天消息面预检；auto 会为 Grok 4.5 和 GPT-5 系列搜索模型选择 Responses API，也可显式选择 responses 或 chat。长度默认：上下文 128000 tokens，最大输出 4096 tokens。模型和密钥留空则跳过。",
    "买卖决策模型": "推荐使用 deepseek-v4-pro；也可填写其他兼容 /chat/completions 的模型服务。长度默认：上下文 128000 tokens，最大输出 4096 tokens。",
    "交易规则与风控": "约束买卖决策必须遵守的交易纪律、持仓数量、仓位比例、现金缓冲与盘面控仓规则。交易纪律 Prompt 会直接写入决策模型的必须遵守段。",
    "交易通知": "模拟买入或卖出成交落盘后推送。从下拉框按需添加渠道并分块配置；每个渠道可独立启用或关闭，关闭会保留配置，移除并保存后才会清除配置。Webhook、Bot Token 和签名密钥只保存、不回显。",
    "选股与买卖设置": "配置主板、创业板、科创板和 ST 选股范围、候选数量，并维护北京时间 HH:MM 的选股、决策及离场时间。",
    "综合决策参考": "为买卖决策汇总指数、板块、资金流向、热门股票等参考数据。缓存秒数控制数据复用周期，单类参考数据上限可设置为 1～8。",
    "选股与交易策略": "选择一套独立策略；基础策略、Z哥、李大霄、板块潮汐和预设文字策略的候选、买入、卖出、仓位与 Prompt 规则互不混用。",
    "盘面监控生产时间点": "直接填写北京时间 HH:MM；隔夜美股总结默认交易日 08:00 生成，A 股盘面监控在交易时段触发；长度默认：上下文 128000 tokens，最大输出 4096 tokens。",
    "行情与资金流设置": "统一管理公开快照、指数刷新和行业资金流动画。播放速度、每侧行业数量、采样间隔及上午/下午采样窗口均支持运行时保存后生效；时间使用北京时间 HH:MM，默认 09:25～11:31、13:00～15:01。",
}
ADMIN_SETTING_GROUPS: tuple[dict[str, str], ...] = (
    {
        "slug": "access-control",
        "name": "访问控制",
        "summary": "管理设置页管理员密码与访问凭据。",
        "icon": "安全",
    },
    {
        "slug": "notifications",
        "name": "交易通知",
        "summary": "管理成交通知总开关，以及飞书、钉钉等推送渠道。",
        "icon": "通知",
    },
    {
        "slug": "news-precheck",
        "name": "消息面预检模型",
        "summary": "配置候选股消息面预检使用的模型、网关与并发参数。",
        "icon": "预检",
    },
    {
        "slug": "decision-model",
        "name": "买卖决策模型",
        "summary": "配置交易决策模型、API 接入与输出限制。",
        "icon": "决策",
    },
    {
        "slug": "trading-risk",
        "name": "交易规则与风控",
        "summary": "维护交易纪律、持仓数量、仓位比例与现金缓冲规则。",
        "icon": "风控",
    },
    {
        "slug": "decision-times",
        "name": "选股与买卖设置",
        "summary": "配置股票范围、候选数量，以及选股、买卖决策和离场时间。",
        "icon": "交易",
    },
    {
        "slug": "decision-reference",
        "name": "综合决策参考",
        "summary": "汇总指数、板块、资金流和热门股票，辅助买卖决策。",
        "icon": "参考",
    },
    {
        "slug": "iwencai",
        "name": "问财数据源",
        "summary": "配置问财网关、密钥、超时、重试、并发与缓存。",
        "icon": "问财",
    },
    {
        "slug": "stock-strategy",
        "name": "选股与交易策略",
        "summary": "选择内置策略或维护自定义预设文字策略。",
        "icon": "策略",
    },
    {
        "slug": "us-market",
        "name": "牛牛美股",
        "summary": "配置美股功能、Grok 接入、推文监控与评级任务。",
        "icon": "美股",
    },
    {
        "slug": "market-monitoring",
        "name": "盘面监控生产时间点",
        "summary": "配置隔夜美股与 A 股盘前、午盘、盘后的监控任务。",
        "icon": "盘面",
    },
    {
        "slug": "task-scheduling",
        "name": "任务调度",
        "summary": "设置后台任务失败后的重试次数与间隔。",
        "icon": "调度",
    },
    {
        "slug": "indices-refresh",
        "name": "行情与资金流设置",
        "summary": "调整指数刷新、资金流展示数量、播放速度、采样频率和时间窗口。",
        "icon": "行情",
    },
)
ADMIN_SETTING_GROUP_BY_SLUG = {
    str(group["slug"]): group for group in ADMIN_SETTING_GROUPS
}
ADMIN_SETTING_GROUP_BY_NAME = {
    str(group["name"]): group for group in ADMIN_SETTING_GROUPS
}
NOTIFICATION_GENERAL_CONFIG_NAMES = (
    "DASHBOARD_NOTIFICATION_ENABLED",
    "DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS",
)
NOTIFICATION_CHANNEL_SETTINGS: tuple[dict[str, Any], ...] = (
    {
        "id": "feishu",
        "label": "飞书",
        "description": "群机器人 Webhook，可选安全签名。",
        "enabled_name": "DASHBOARD_FEISHU_NOTIFICATION_ENABLED",
        "field_names": ("DASHBOARD_FEISHU_WEBHOOK_URL", "DASHBOARD_FEISHU_SIGNING_SECRET"),
    },
    {
        "id": "dingtalk",
        "label": "钉钉",
        "description": "群自定义机器人 Webhook，可选加签密钥。",
        "enabled_name": "DASHBOARD_DINGTALK_NOTIFICATION_ENABLED",
        "field_names": ("DASHBOARD_DINGTALK_WEBHOOK_URL", "DASHBOARD_DINGTALK_SIGNING_SECRET"),
    },
    {
        "id": "wecom",
        "label": "企业微信",
        "description": "群机器人 Webhook。",
        "enabled_name": "DASHBOARD_WECOM_NOTIFICATION_ENABLED",
        "field_names": ("DASHBOARD_WECOM_WEBHOOK_URL",),
    },
    {
        "id": "telegram",
        "label": "Telegram",
        "description": "Bot Token 与接收消息的 Chat ID。",
        "enabled_name": "DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED",
        "field_names": ("DASHBOARD_TELEGRAM_BOT_TOKEN", "DASHBOARD_TELEGRAM_CHAT_ID"),
    },
)
NOTIFICATION_CHANNEL_BY_ID = {
    str(channel["id"]): channel for channel in NOTIFICATION_CHANNEL_SETTINGS
}
NOTIFICATION_PRESENCE_STATE_NAMES = frozenset(
    str(name)
    for channel in NOTIFICATION_CHANNEL_SETTINGS
    for name in channel.get("field_names", ())
)


def removed_notification_config_names(channel_ids: set[str] | list[str] | tuple[str, ...]) -> set[str]:
    """Return channel fields that must be deleted when a channel is removed."""

    clear_names: set[str] = set()
    for channel_id in channel_ids:
        channel = NOTIFICATION_CHANNEL_BY_ID.get(str(channel_id or "").strip().lower())
        if channel is None:
            continue
        clear_names.add(str(channel["enabled_name"]))
        clear_names.update(str(name) for name in channel.get("field_names", ()))
    return clear_names


US_FEATURE_GATED_GROUPS = {
    "X 监控",
}
US_FEATURE_GATED_NAMES = {
    "US_RATING_BASE_URL",
    "US_RATING_API_KEY",
    "US_RATING_CONTEXT_LENGTH",
    "US_RATING_MAX_TOKENS",
    "DASHBOARD_GROK_MODEL",
    "DASHBOARD_GROK_API_MODE",
    "DASHBOARD_GROK_CONTEXT_LENGTH",
    "DASHBOARD_GROK_MAX_TOKENS",
    "DASHBOARD_GROK_BASE_URL",
    "DASHBOARD_GROK_API_KEY",
    "X_WATCHLIST_ACCOUNTS",
    "X_WATCHLIST_MAX_TOKENS",
    "X_WATCHLIST_DAEMON_INTERVAL_SECONDS",
    "X_WATCHLIST_REQUEST_TIMEOUT_SECONDS",
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


def friendly_strategy_suite_text(value: str) -> str:
    normalized = normalize_strategy_suite_update(value)
    labels = {str(item["id"]): str(item["label"]) for item in strategy_suite_options()}
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
        elif name == "IWENCAI_BASE_URL":
            normalized[name] = normalize_iwencai_base_url(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "time_list":
            normalized[name] = normalize_time_list_update(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "time":
            normalized[name] = normalize_env_update(name, normalized[name], "time")
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "handle_list":
            normalized[name] = normalize_handle_list_update(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "stock_universe":
            normalized[name] = normalize_stock_universe(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") in {"strategy_multi", "strategy_single"}:
            normalized[name] = normalize_strategy_list_update(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "strategy_source":
            normalized[name] = normalize_strategy_source_update(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "strategy_suite":
            normalized[name] = normalize_strategy_suite_update(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "preset_strategy_text":
            normalized[name] = normalize_preset_strategy_text_update(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "trade_discipline_text":
            normalized[name] = normalize_trade_discipline_text_update(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "api_mode":
            normalized[name] = normalize_env_update(name, normalized[name], "api_mode")
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "playback_speed":
            normalized[name] = normalize_env_update(name, normalized[name], "playback_speed")
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
        elif name == "IWENCAI_BASE_URL":
            normalize_iwencai_base_url(value)
        elif name in {
            "IWENCAI_TIMEOUT_SECONDS",
            "IWENCAI_MAX_RETRIES",
            "IWENCAI_MAX_CONCURRENCY",
            "IWENCAI_CACHE_TTL_SECONDS",
        } and str(value or "").strip():
            number = int(value)
            minimum, maximum = {
                "IWENCAI_TIMEOUT_SECONDS": (2, 60),
                "IWENCAI_MAX_RETRIES": (0, 2),
                "IWENCAI_MAX_CONCURRENCY": (1, 4),
                "IWENCAI_CACHE_TTL_SECONDS": (15, 3600),
            }[name]
            if number < minimum or number > maximum:
                raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
        elif name == "DASHBOARD_B1_SCHEDULE_TIMES":
            normalize_time_list_update(value)
        elif name in {
            "DASHBOARD_B3_EXIT_TIME",
            "DASHBOARD_TIME_EXIT_TIME",
            "DASHBOARD_TIME_STOP_EXIT_TIME",
            *INDUSTRY_FLOW_WINDOW_CONFIG_NAMES,
        }:
            normalize_env_update(name, value, "time")
        elif name == "X_WATCHLIST_ACCOUNTS":
            normalize_handle_list_update(value)
        elif name == STOCK_UNIVERSE_ENV:
            normalize_stock_universe(value)
        elif name == STRATEGY_SOURCE_ENV:
            normalize_strategy_source_update(value)
        elif name == PERSONA_STRATEGY_ENV:
            normalize_strategy_list_update(value)
        elif name == ACTIVE_STRATEGY_ENV:
            normalize_strategy_suite_update(value)
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
            "DASHBOARD_DISPLAY_CANDIDATE_LIMIT",
            "DASHBOARD_TRADE_CANDIDATE_LIMIT",
        } and str(value or "").strip():
            if int(value) <= 0:
                raise ValueError(f"{name} 必须大于 0")
        elif name == "DASHBOARD_INDUSTRY_FLOW_PLAYBACK_SPEED":
            normalize_env_update(name, value, "playback_speed")
        elif name == "DASHBOARD_INDUSTRY_FLOW_SIDE_LIMIT" and str(value or "").strip():
            number = int(value)
            if number < 1 or number > 10:
                raise ValueError("资金流每侧行业数量必须在 1 到 10 之间")
        elif name == "DASHBOARD_INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS" and str(value or "").strip():
            number = int(value)
            if number < 60 or number > 600:
                raise ValueError("资金流采样间隔必须在 60 到 600 秒之间")
        elif name == "DASHBOARD_MAX_NEW_BUYS_PER_DECISION" and str(value or "").strip():
            if int(value) < 0:
                raise ValueError(f"{name} 必须大于等于 0")
        elif name == "DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS" and str(value or "").strip():
            timeout = int(value)
            if timeout < 1 or timeout > 30:
                raise ValueError(f"{name} 必须在 1 到 30 之间")
        elif name == "X_WATCHLIST_REQUEST_TIMEOUT_SECONDS" and str(value or "").strip():
            timeout = int(value)
            if timeout < 8 or timeout > 120:
                raise ValueError(f"{name} 必须在 8 到 120 之间")
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
    if set(updates) & set(INDUSTRY_FLOW_WINDOW_CONFIG_NAMES):
        _industry_flow_sampling_windows_value(
            updates,
            fallback=INDUSTRY_FLOW_SAMPLING_WINDOWS,
            strict=True,
        )


def sync_business_runtime_settings(
    changed: dict[str, str] | list[str] | set[str] | tuple[str, ...] | None,
    *,
    sync_names: list[str] | set[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    global ADMIN_PASSWORD, B1_CANDIDATE_REFRESH_LAST_TS, B1_SCHEDULE_TIMES
    global INDUSTRY_FLOW_PLAYBACK_SPEED, INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS, INDUSTRY_FLOW_SIDE_LIMIT
    global INDUSTRY_FLOW_SAMPLING_WINDOWS
    global TRADER_MODULE, TRADER_MODULE_MTIME, TRADER_SELL_SIGNALS_MTIME
    if isinstance(changed, dict):
        changed_names = set(changed.keys())
    else:
        changed_names = set(changed or [])
    runtime_names = set(sync_names) if sync_names is not None else set(changed_names)
    env_values = parse_env_file()
    visible_names = admin_visible_env_names(env_values)
    for name in visible_names:
        if name not in runtime_names:
            continue
        if name in env_values:
            os.environ[name] = env_values[name]
        elif name in changed_names:
            os.environ.pop(name, None)

    applied: list[str] = []
    if "DASHBOARD_ADMIN_PASSWORD" in changed_names:
        ADMIN_PASSWORD = str(env_values.get("DASHBOARD_ADMIN_PASSWORD") or "").strip()
        applied.append("admin_password")
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

    industry_flow_names = {
        "DASHBOARD_INDUSTRY_FLOW_PLAYBACK_SPEED",
        "DASHBOARD_INDUSTRY_FLOW_SIDE_LIMIT",
        "DASHBOARD_INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS",
        *INDUSTRY_FLOW_WINDOW_CONFIG_NAMES,
    }
    if changed_names & industry_flow_names:
        INDUSTRY_FLOW_PLAYBACK_SPEED = _industry_flow_playback_speed_value(
            env_values.get("DASHBOARD_INDUSTRY_FLOW_PLAYBACK_SPEED")
            or ENV_CONFIG_BY_NAME["DASHBOARD_INDUSTRY_FLOW_PLAYBACK_SPEED"]["default"]
        )
        INDUSTRY_FLOW_SIDE_LIMIT = _bounded_int_value(
            env_values.get("DASHBOARD_INDUSTRY_FLOW_SIDE_LIMIT"),
            INDUSTRY_FLOW_DEFAULT_SIDE_LIMIT,
            1,
            10,
        )
        INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS = _bounded_int_value(
            env_values.get("DASHBOARD_INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS"),
            INDUSTRY_FLOW_DEFAULT_SAMPLE_INTERVAL_SECONDS,
            60,
            600,
        )
        INDUSTRY_FLOW_SAMPLING_WINDOWS = _industry_flow_sampling_windows_value(env_values)
        invalidate_api_cache("industry_flow")
        applied.append("industry_flow")

    iwencai_names = {
        "IWENCAI_ENABLED",
        "IWENCAI_BASE_URL",
        "IWENCAI_API_KEY",
        "IWENCAI_TIMEOUT_SECONDS",
        "IWENCAI_MAX_RETRIES",
        "IWENCAI_MAX_CONCURRENCY",
        "IWENCAI_CACHE_TTL_SECONDS",
    }
    if changed_names & iwencai_names:
        try:
            API_TTLS["iwencai_dragon_tiger"] = int(
                env_values.get("IWENCAI_CACHE_TTL_SECONDS")
                or ENV_CONFIG_BY_NAME["IWENCAI_CACHE_TTL_SECONDS"]["default"]
            )
        except (TypeError, ValueError):
            pass
        invalidate_api_cache_prefix("iwencai_dragon_tiger:")
        applied.append("iwencai")

    if changed_names & {
        STRATEGY_SOURCE_ENV,
        PERSONA_STRATEGY_ENV,
        ACTIVE_STRATEGY_ENV,
        PRESET_STRATEGY_TEXT_ENV,
        "DASHBOARD_DISPLAY_CANDIDATE_LIMIT",
        "DASHBOARD_TRADE_CANDIDATE_LIMIT",
        STOCK_UNIVERSE_ENV,
    }:
        B1_CANDIDATE_REFRESH_LAST_TS = 0.0
        with API_RESPONSE_LOCK:
            API_RESPONSE_CACHE.pop(PRACTICE_CANDIDATES_CACHE_KEY, None)
        applied.append("strategy_settings")
        if changed_names & {PERSONA_STRATEGY_ENV, ACTIVE_STRATEGY_ENV}:
            applied.append("active_strategy")

    if changed_names & TRADER_RUNTIME_ENV_NAMES:
        with TRADER_MODULE_LOCK:
            TRADER_MODULE = None
            TRADER_MODULE_MTIME = 0.0
            TRADER_SELL_SIGNALS_MTIME = 0.0
        invalidate_api_cache("niuniu_practice", PRACTICE_FAST_CACHE_KEY)
        applied.append("trader_runtime")

    if changed_names & set(visible_names):
        applied.append("env")

    return {"ok": True, "applied": sorted(set(applied)), "changed_names": sorted(changed_names)}


def persist_and_sync_business_updates(
    updates: dict[str, str],
    *,
    clear_names: set[str] | None = None,
) -> dict[str, Any]:
    """Persist and hot-apply one validated update set as a single operation."""

    with ENV_FILE_WRITE_LOCK:
        result = _write_env_file_values_unlocked(
            updates,
            clear_names=clear_names,
        )
        sync_names = set(updates) | set(clear_names or set())
        result["runtime"] = sync_business_runtime_settings(
            result.get("changed_names") or [],
            sync_names=sync_names,
        )
        return result


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


def model_test_provider_fallbacks() -> dict[str, dict[str, str]]:
    """Load complete YAML provider fallbacks without exposing their secrets."""

    try:
        cfg = load_yaml_config()
    except Exception:
        cfg = {}

    providers = cfg.get("custom_providers", []) if isinstance(cfg, dict) else []
    providers = providers if isinstance(providers, list) else []
    crossdesk: dict[str, str] = {}
    grok: dict[str, str] = {}
    for raw_provider in providers:
        if not isinstance(raw_provider, dict):
            continue
        provider = {
            "base_url": str(raw_provider.get("base_url") or "").strip(),
            "api_key": str(raw_provider.get("api_key") or "").strip(),
        }
        if not all(provider.values()):
            continue
        identity = " ".join(
            (
                str(raw_provider.get("name") or ""),
                provider["base_url"],
            )
        ).lower()
        if not crossdesk and "crossdesk" in identity:
            crossdesk = provider
        if not grok and (
            "grok" in str(raw_provider.get("name") or "").lower()
            or "crossdesk.ccwu.cc" in provider["base_url"].lower()
        ):
            grok = provider

    raw_model = cfg.get("model", {}) if isinstance(cfg, dict) else {}
    model_provider = {
        "base_url": str(raw_model.get("base_url") or "").strip(),
        "api_key": str(raw_model.get("api_key") or "").strip(),
    } if isinstance(raw_model, dict) else {}
    if not all(model_provider.values()):
        model_provider = {}

    return {
        "decision-model": crossdesk,
        "grok-model": crossdesk,
        "us-rating-model": crossdesk or model_provider,
        "a-share-summary-model": grok or crossdesk or model_provider,
    }


def model_test_settings_snapshot(
    target_id: str,
    overrides: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve saved settings plus unsaved form values for one model test."""

    allowed_names = model_test_override_names(target_id)
    settings: dict[str, str] = {}
    for name in model_test_setting_names():
        default = str(ENV_CONFIG_BY_NAME.get(name, {}).get("default") or "").strip()
        if default:
            settings[name] = default

    file_values = parse_env_file()
    for name in model_test_setting_names():
        if name in file_values:
            settings[name] = str(file_values[name])
        if name in os.environ:
            settings[name] = str(os.environ[name])

    for name, raw_value in (overrides or {}).items():
        if name not in allowed_names:
            continue
        value = str(raw_value or "").strip()
        if is_secret_config_key(name) and not value:
            continue
        settings[name] = value

    fallback = model_test_provider_fallbacks().get(target_id, {})
    return settings, fallback


def send_model_connection_test(
    target_id: str,
    overrides: dict[str, str] | None = None,
    *,
    opener=None,
) -> dict[str, Any]:
    """Run one rate-limited caller's test under a small process-wide cap."""

    if target_id not in MODEL_TEST_TARGET_BY_ID:
        return {"ok": False, "target": "", "error": "不支持的模型测试目标"}
    if not MODEL_TEST_SEMAPHORE.acquire(blocking=False):
        return {
            "ok": False,
            "target": target_id,
            "error": "当前模型测试较多，请稍后重试",
            "error_code": "busy",
        }
    try:
        settings, fallback = model_test_settings_snapshot(target_id, overrides)
        kwargs: dict[str, Any] = {
            "provider_fallback": fallback,
            "timeout": MODEL_TEST_TIMEOUT_SECONDS,
        }
        if opener is not None:
            kwargs["opener"] = opener
        return test_model_connection(target_id, settings, **kwargs)
    finally:
        MODEL_TEST_SEMAPHORE.release()


def iwencai_test_settings_snapshot(
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Resolve saved iWencai settings plus unsaved form values."""

    settings = {
        name: str(ENV_CONFIG_BY_NAME.get(name, {}).get("default") or "")
        for name in IWENCAI_TEST_FIELD_NAMES
    }
    file_values = parse_env_file()
    for name in IWENCAI_TEST_FIELD_NAMES:
        if name in file_values:
            settings[name] = str(file_values[name])
        if name in os.environ:
            settings[name] = str(os.environ[name])
    for name, raw_value in (overrides or {}).items():
        if name not in IWENCAI_TEST_FIELD_NAMES:
            continue
        value = str(raw_value or "").strip()
        if is_secret_config_key(name) and not value:
            continue
        settings[name] = value
    return settings


def send_iwencai_connection_test(
    overrides: dict[str, str] | None = None,
    *,
    opener=None,
) -> dict[str, Any]:
    settings = iwencai_test_settings_snapshot(overrides)
    kwargs: dict[str, Any] = {"semaphore": IWENCAI_TEST_SEMAPHORE}
    if opener is not None:
        kwargs["opener"] = opener
    return test_iwencai_connection(settings, **kwargs)


def business_config_fallback_value(
    name: str,
    *,
    crossdesk_provider: dict[str, str] | None = None,
) -> tuple[str, str]:
    if name in {"DASHBOARD_GROK_BASE_URL", "DASHBOARD_DECISION_BASE_URL"}:
        provider = crossdesk_provider if crossdesk_provider is not None else crossdesk_provider_values()
        return provider.get("base_url", ""), "config.yaml" if provider.get("base_url") else "default"
    if name in {"DASHBOARD_GROK_API_KEY", "DASHBOARD_DECISION_API_KEY"}:
        provider = crossdesk_provider if crossdesk_provider is not None else crossdesk_provider_values()
        return provider.get("api_key", ""), "config.yaml" if provider.get("api_key") else "default"
    if name == "X_WATCHLIST_ACCOUNTS":
        handles = x_watchlist_state_accounts()
        return ",".join(handles), "x_watchlist_state" if handles else "default"
    return "", "default"


def build_admin_config_payload() -> dict[str, Any]:
    env_values = parse_env_file()
    crossdesk_provider = crossdesk_provider_values()
    visible_names = admin_visible_env_names(env_values)
    names = set(visible_names)
    items = []
    admin_order = {name: idx for idx, name in enumerate(visible_names)}
    for name in sorted(names, key=lambda n: admin_order.get(n, 999)):
        schema = ENV_CONFIG_BY_NAME.get(name, {"name": name, "label": name, "group": "其他", "kind": "text", "default": "", "effect": "restart"})
        fallback_value, fallback_source = business_config_fallback_value(
            name,
            crossdesk_provider=crossdesk_provider,
        )
        if name == ACTIVE_STRATEGY_ENV and name not in env_values and name not in os.environ:
            fallback_value = active_strategy_suite(
                None,
                env_values.get(STRATEGY_SOURCE_ENV),
                env_values.get(PERSONA_STRATEGY_ENV),
            )
            fallback_source = "legacy strategy settings"
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
        if schema.get("kind") == "stock_universe" and not secret:
            edit_source = env_values.get(name) if name in env_values else (fallback_value or default_value)
            edit_value = normalize_stock_universe(edit_source)
            state_value = env_values.get(name) if name in env_values else (fallback_value or default_value)
            item.update({
                "effective": friendly_stock_universe(effective),
                "file_value": edit_value,
                "file_state": friendly_stock_universe(state_value),
                "default": friendly_stock_universe(default_value),
                "stock_universe_values": list(selected_stock_universe(edit_value)),
                "stock_universe_options": list(STOCK_UNIVERSE_OPTIONS),
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
        if schema.get("kind") == "strategy_suite" and not secret:
            edit_source = str(env_values.get(name) or fallback_value or default_value)
            edit_value = normalize_strategy_suite_update(edit_source)
            state_value = env_values.get(name) if name in env_values else (fallback_value or default_value)
            item.update({
                "effective": friendly_strategy_suite_text(str(effective)),
                "file_value": edit_value,
                "file_state": friendly_strategy_suite_text(str(state_value)),
                "default": friendly_strategy_suite_text(str(default_value)),
                "strategy_suite_options": strategy_suite_options(),
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
        item["current_state"] = (
            display_secret_state(effective)
            if secret or name in NOTIFICATION_PRESENCE_STATE_NAMES
            else str(item.get("effective") or "")
        )
        items.append(item)
    item_counts: dict[str, int] = {}
    for item in items:
        group_name = str(item.get("group") or "其他")
        item_counts[group_name] = item_counts.get(group_name, 0) + 1
    return {
        "items": items,
        "groups": [
            {
                **group,
                "note": ADMIN_GROUP_NOTES.get(str(group["name"]), ""),
                "item_count": item_counts.get(str(group["name"]), 0),
            }
            for group in ADMIN_SETTING_GROUPS
            if item_counts.get(str(group["name"]), 0)
        ],
        "notification_channels": [
            {
                **channel,
                "field_names": list(channel.get("field_names", ())),
            }
            for channel in NOTIFICATION_CHANNEL_SETTINGS
        ],
        "notification_general_names": list(NOTIFICATION_GENERAL_CONFIG_NAMES),
        "model_tests": model_test_metadata(),
        "iwencai_test": iwencai_test_metadata(),
        "ui": {
            "us_feature_toggle_name": "DASHBOARD_US_FEATURES_ENABLED",
            "us_feature_gated_names": sorted(US_FEATURE_GATED_NAMES),
            "strategy_suite_name": ACTIVE_STRATEGY_ENV,
            "strategy_preset_name": PRESET_STRATEGY_TEXT_ENV,
            "strategy_preset_value": "preset_text",
        },
        "secret_placeholder": SECRET_PLACEHOLDER,
    }


def notification_settings_snapshot(names: tuple[str, ...] | set[str]) -> dict[str, str]:
    """Build the effective notification config without exposing it to callers."""

    settings = {
        name: str(ENV_CONFIG_BY_NAME.get(name, {}).get("default") or "")
        for name in names
    }
    file_values = parse_env_file()
    for name in names:
        if name in file_values:
            settings[name] = str(file_values[name])
        if name in os.environ:
            settings[name] = str(os.environ[name])
    return settings


def send_notification_test(
    channel_id: str,
    overrides: dict[str, str] | None = None,
    *,
    transport=None,
    clock=None,
) -> dict[str, Any]:
    """Send one explicit test message using unsaved values with saved fallbacks."""

    normalized_id = str(channel_id or "").strip().lower()
    channel = NOTIFICATION_CHANNEL_BY_ID.get(normalized_id)
    if channel is None:
        return {"ok": False, "channel": "", "error": "不支持的通知渠道"}

    timeout_name = "DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS"
    allowed_names = {timeout_name, *(str(name) for name in channel.get("field_names", ()))}
    settings = notification_settings_snapshot(allowed_names)
    for name, raw_value in (overrides or {}).items():
        if name not in allowed_names:
            continue
        value = str(raw_value or "").strip()
        secret = ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "secret" or is_secret_config_key(name)
        if secret and not value:
            continue
        if name == timeout_name and not value:
            return {"ok": False, "channel": normalized_id, "error": "单次推送超时秒数不能为空"}
        settings[name] = value

    label = str(channel["label"])
    try:
        from notifications import Notification, dispatch_to_channel

        notification = Notification(
            event_type="notification.test",
            title="牛牛1号通知测试",
            text=(
                f"{label} 渠道配置验证消息。\n模拟成交，非实盘。\n"
                f"发送时间：{datetime.now(CN_TZ).strftime('%Y-%m-%d %H:%M:%S')}（北京时间）\n"
                "这是一条测试通知，不代表真实买卖或成交。"
            ),
            metadata={"channel": normalized_id, "test": True},
        )
        result = dispatch_to_channel(
            notification,
            normalized_id,
            settings,
            transport=transport,
            clock=clock,
        )
    except Exception as exc:
        print(f"通知测试异常：{type(exc).__name__}", file=sys.stderr)
        return {
            "ok": False,
            "channel": normalized_id,
            "error": "通知测试失败",
        }

    if result.ok:
        return {
            "ok": True,
            "channel": normalized_id,
            "message": f"{label} 测试通知已发送",
        }
    return {
        "ok": False,
        "channel": normalized_id,
        "error": result.error or "通知发送失败",
    }

# Frontend documents and UI behavior live in frontend/.

def release_version_tuple(value: str) -> tuple[int, int, int] | None:
    match = VERSION_PATTERN.fullmatch(str(value or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def fetch_latest_docker_version() -> str:
    versions: list[tuple[tuple[int, int, int], str]] = []
    page_count = 1
    for page in range(1, VERSION_CHECK_MAX_PAGES + 1):
        url = f"{DOCKER_HUB_TAGS_API}?page={page}&page_size=100"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": f"NiuOne/{CURRENT_VERSION}",
            },
        )
        with urllib.request.urlopen(request, timeout=6) as response:
            body = response.read(VERSION_CHECK_MAX_RESPONSE_BYTES + 1)
        if len(body) > VERSION_CHECK_MAX_RESPONSE_BYTES:
            raise ValueError("Docker Hub response is too large")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("Docker Hub returned an invalid tag list")
        for item in payload["results"]:
            name = str(item.get("name") or "") if isinstance(item, dict) else ""
            parsed = release_version_tuple(name)
            if parsed is not None:
                versions.append((parsed, name))
        if page == 1:
            try:
                total = max(0, int(payload.get("count") or 0))
            except (TypeError, ValueError):
                total = len(payload["results"])
            page_count = max(1, min(VERSION_CHECK_MAX_PAGES, (total + 99) // 100))
        if page >= page_count:
            break
    if not versions:
        raise ValueError("Docker Hub has no strict release tags")
    return max(versions, key=lambda item: item[0])[1]


def build_version_status() -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result: dict[str, Any] = {
        "current_version": CURRENT_VERSION,
        "latest_version": None,
        "update_available": None,
        "check_ok": False,
        "checked_at": checked_at,
        "repository": DOCKER_HUB_REPOSITORY,
        "repository_url": DOCKER_HUB_REPOSITORY_URL,
    }
    try:
        latest_version = fetch_latest_docker_version()
        current = release_version_tuple(CURRENT_VERSION)
        latest = release_version_tuple(latest_version)
        result["latest_version"] = latest_version
        result["update_available"] = current < latest if current is not None and latest is not None else None
        result["check_ok"] = True
    except Exception as exc:
        print(f"Docker Hub 版本检查失败：{type(exc).__name__}", file=sys.stderr)
    return result


def get_version_status() -> dict[str, Any]:
    now = time.time()
    with VERSION_CHECK_LOCK:
        cached = VERSION_CHECK_CACHE.get("payload")
        cached_at = float(VERSION_CHECK_CACHE.get("ts") or 0)
        cached_ttl = int(VERSION_CHECK_CACHE.get("ttl") or 0)
        if isinstance(cached, dict) and now - cached_at < cached_ttl:
            return dict(cached)
        payload = build_version_status()
        ttl = VERSION_CHECK_TTL_SECONDS if payload["check_ok"] else VERSION_CHECK_FAILURE_TTL_SECONDS
        VERSION_CHECK_CACHE.update({"ts": now, "ttl": ttl, "payload": payload})
        return dict(payload)


def get_self_optimize_status() -> dict[str, Any]:
    from self_optimizer import get_status

    return get_status()

def apply_self_optimization() -> dict[str, Any]:
    from self_optimizer import apply_optimization

    return apply_optimization()


def admin_setting_group_env_names(group_slug: str) -> set[str]:
    group = ADMIN_SETTING_GROUP_BY_SLUG.get(str(group_slug or ""))
    if not group:
        return set()
    group_name = str(group["name"])
    return {
        name
        for name in admin_visible_env_names()
        if str(ENV_CONFIG_BY_NAME.get(name, {}).get("group") or "其他") == group_name
    }


def public_snapshot_publisher() -> Any:
    global PUBLIC_SNAPSHOT_PUBLISHER
    if PUBLIC_SNAPSHOT_PUBLISHER is None:
        from app.dashboard.public_snapshots import SnapshotPublisher

        PUBLIC_SNAPSHOT_PUBLISHER = SnapshotPublisher(PUBLIC_DATA_DIR)
    return PUBLIC_SNAPSHOT_PUBLISHER


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


def fetch_us_profiles(symbols: list[str]) -> dict[str, Any]:
    """Fetch optional company classification independently from live quotes."""
    return {
        "items": fetch_us_company_profiles(symbols),
        "symbols": symbols,
        "error": None,
    }


def fetch_us_quotes(symbols: list[str]) -> dict[str, Any]:
    """Fetch live US prices without waiting for optional company profiles."""
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
    parser = argparse.ArgumentParser(description="NiuOne dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    from app.dashboard.fastapi_app import run

    print(f"牛牛1号：http://{args.host}:{args.port}")
    if ADMIN_PASSWORD:
        print("设置页：/admin（管理员密码保护已启用）")
    else:
        print(f"设置页：/admin（管理员密钥：{ADMIN_TOKEN_FILE}）")
    print(f"访问统计：{STATS_DB}")
    print(f"消息历史：{push_history.DB_PATH}")
    run(host=args.host, port=args.port, legacy_module=sys.modules[__name__])


if __name__ == "__main__":
    main()
