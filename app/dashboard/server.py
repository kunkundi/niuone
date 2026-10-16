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
import sqlite3
import time
import subprocess
import sys
import threading
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
import urllib.error
import urllib.request

from a_share_calendar import is_a_share_trading_day as calendar_is_a_share_trading_day, trading_day_status
from dashboard_json_cache import (
    read_json_cache,
    read_versioned_json_cache,
    write_json_cache,
)
from core.process_lease import FileLease
from core.model_api import (
    build_model_request,
    normalize_model_stream_mode,
    normalize_reasoning_effort,
    request_model_complete,
    stream_model_response,
)
from core.model_reasoning import (
    reasoning_effort_capability_catalog,
    resolve_model_reasoning_effort,
)
from core.shared_model_config import (
    LEGACY_SUMMARY_MODEL_ENV_NAMES,
    SHARED_MODEL_ENV_NAMES,
    SHARED_MODEL_NAMES,
    legacy_summary_migration_values,
    resolve_shared_model_config,
)
from dashboard import practice_payload as practice_payload_impl
from dashboard import practice_market_summary as practice_market_summary_impl
from dashboard.niuone_mainline import build_niuone_mainline_view
from dashboard.today_candidates import (
    TODAY_CANDIDATES_SCHEMA_VERSION,
    build_today_candidate_intraday_payload,
    build_today_candidates_payload,
)
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
    ResolvedModelTestConfig,
    model_test_metadata,
    model_test_override_names,
    model_test_setting_names,
    resolve_model_test_config,
    test_model_connection,
)
from dashboard.apis.iwencai_service import (
    DEFAULT_LIMIT as IWENCAI_DRAGON_TIGER_DEFAULT_LIMIT,
    dragon_tiger_archive_path,
    enrich_consecutive_dragon_tiger_news,
    expire_dragon_tiger_archives,
    fetch_dragon_tiger,
    mark_consecutive_dragon_tiger_items,
    normalize_limit as normalize_iwencai_limit,
    normalize_page as normalize_iwencai_page,
    normalize_trade_date as normalize_iwencai_trade_date,
    read_dragon_tiger_archive,
    read_dragon_tiger_snapshot,
    write_dragon_tiger_archive,
    write_dragon_tiger_snapshot,
)
from dashboard.apis.industry_flow import (
    DEFAULT_HISTORY_LIMIT as INDUSTRY_FLOW_HISTORY_LIMIT,
    DEFAULT_PLAYBACK_SPEED as INDUSTRY_FLOW_DEFAULT_PLAYBACK_SPEED,
    DEFAULT_SAMPLE_INTERVAL_SECONDS as INDUSTRY_FLOW_DEFAULT_SAMPLE_INTERVAL_SECONDS,
    DEFAULT_SIDE_LIMIT as INDUSTRY_FLOW_DEFAULT_SIDE_LIMIT,
    SAMPLING_WINDOWS as INDUSTRY_FLOW_DEFAULT_SAMPLING_WINDOWS,
    append_industry_flow_sample,
    build_industry_flow_payload,
    compact_industry_flow_sample,
    is_industry_flow_session_timestamp,
    normalize_industry_flow_sampling_windows,
)
from dashboard.apis.market_breadth import (
    DEFAULT_SAMPLE_INTERVAL_SECONDS as MARKET_BREADTH_DEFAULT_SAMPLE_INTERVAL_SECONDS,
    append_market_breadth_sample,
    build_market_breadth_payload,
    compact_market_breadth_sample,
    compact_previous_market_breadth_history,
    compact_previous_turnover_history,
    is_market_breadth_session_timestamp,
    roll_market_breadth_history,
)
from dashboard.apis.market_retention import (
    market_retention_date_key,
    seconds_until_next_market_retention_rollover,
)
from app.dashboard.market_breadth_recovery import plan_market_breadth_recovery
from market_data.iwencai_client import (
    DEFAULT_BASE_URL as IWENCAI_DEFAULT_BASE_URL,
    normalize_base_url as normalize_iwencai_base_url,
)
from market_data.eastmoney_turnover import (
    fetch_market_turnover_estimate,
    fetch_turnover_profile,
)
from market_data.eastmoney_concept_boards import (
    EASTMONEY_CONCEPT_BOARD_SCHEMA_VERSION,
    EASTMONEY_CONCEPT_BOARD_SOURCE,
    load_eastmoney_concept_board_signal,
)
from market_data.tencent_market_breadth import fetch_tencent_market_breadth
from market_data.tencent_kline_cache import (
    kline_cache_path,
    kline_cache_readiness,
    mark_prewarm_run_failed,
    prewarm_completed_for_date,
)
from app.monitoring.news import (
    DEFAULT_MAX_IMPORTANT_ITEMS as DEFAULT_NEWSNOW_MAX_IMPORTANT_ITEMS,
    DEFAULT_MAX_ITEMS as DEFAULT_NEWSNOW_MAX_ITEMS,
    DEFAULT_SOURCE_IDS as DEFAULT_NEWSNOW_SOURCE_IDS,
    MAX_MAX_IMPORTANT_ITEMS as NEWSNOW_MAX_IMPORTANT_ITEMS_MAX,
    MAX_MAX_ITEMS as NEWSNOW_MAX_ITEMS_MAX,
    MIN_MAX_IMPORTANT_ITEMS as NEWSNOW_MAX_IMPORTANT_ITEMS_MIN,
    MIN_MAX_ITEMS as NEWSNOW_MAX_ITEMS_MIN,
    NewsNowConfig,
    NewsNowConfigurationError,
    NewsNowService,
    SUPPORTED_SOURCES as NEWSNOW_SUPPORTED_SOURCES,
    normalize_endpoint as normalize_newsnow_endpoint,
    parse_source_ids as parse_newsnow_source_ids,
    shared_newsnow_service,
    source_options as newsnow_source_options,
)
from niuone_paths import apply_container_runtime_overrides, get_dashboard_env_file, get_dashboard_home, get_local_data_dir
import push_history
from screening.candidate_cache import (
    build_practice_candidates_cache_payload,
    write_practice_candidates_cache,
)
from screening.holding_cycle import (
    HOLDING_CYCLE_KIND,
    build_holding_cycle_payload,
    merge_niuone_holding_cycle_context,
)
from screening.niuone_mainline_cache import (
    build_niuone_mainline_summary_cache_payload,
    write_niuone_mainline_cache,
    write_niuone_mainline_summary_cache,
)
from screening.niuone_minute import NiuOneMinuteEngine
from screening.stock_universe import (
    DEFAULT_STOCK_UNIVERSE,
    STOCK_UNIVERSE_ENV,
    STOCK_UNIVERSE_OPTIONS,
    friendly_stock_universe,
    normalize_stock_universe,
    selected_stock_universe,
)
from storage.prompt_strategies import PromptStrategyStore
from strategies.prompt_refinement import (
    PromptRefinementContractError,
    PromptRefinementCoverageError,
    PromptRefinementParseError,
    build_refinement_messages,
    finalize_prompt_refinement,
    refine_prompt_once,
)
from strategies.rules import DEFAULT_FEATURE_REGISTRY, compile_strategy_spec
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
    STRATEGY_SOURCE_PRESET_TEXT,
    active_strategy_suite,
    decode_preset_strategy_text,
    decode_trade_discipline_text,
    default_trade_discipline_text,
    default_enabled_persona_strategies_value,
    enabled_strategy_ids,
    enabled_strategy_meta,
    normalize_preset_strategy_text_update,
    normalize_trade_discipline_text_update,
    normalize_strategy_source_update,
    normalize_strategy_list_update,
    normalize_strategy_suite_update,
    strategy_suite_options,
    strategy_settings_options,
)
from strategies.selection import sort_candidates_by_score
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
PROJECT_AUTHOR = "kunkundi"
PROJECT_AUTHOR_URL = "https://github.com/kunkundi"
PROJECT_REPOSITORY = "kunkundi/niuone"
PROJECT_REPOSITORY_URL = f"https://github.com/{PROJECT_REPOSITORY}"
PROJECT_LICENSE = "Apache License 2.0"
PROJECT_LICENSE_URL = f"{PROJECT_REPOSITORY_URL}/blob/main/LICENSE"
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
NEWSNOW_CACHE_FILE = DASHBOARD_HOME / "news" / "realtime_news_latest.json"
INDICES_SNAPSHOT_FILE = CRON_OUTPUT_DIR / "indices_dashboard_cache.json"
IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE = Path(
    os.environ.get("IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE")
    or CRON_OUTPUT_DIR / "iwencai_dragon_tiger_latest.json"
).expanduser()
B1_CACHE_FILE = CRON_OUTPUT_DIR / "b1_screen_latest.json"
PRACTICE_CANDIDATES_CACHE_FILE = CRON_OUTPUT_DIR / "practice_candidates_latest.json"
TODAY_CANDIDATES_CACHE_FILE = CRON_OUTPUT_DIR / "today_candidates_latest.json"
NIUONE_MAINLINE_CACHE_FILE = CRON_OUTPUT_DIR / "niuone_mainline_latest.json"
NIUONE_MAINLINE_MINUTE_CACHE_FILE = CRON_OUTPUT_DIR / "niuone_mainline_minute_latest.json"
NIUONE_MAINLINE_SUMMARY_CACHE_FILE = CRON_OUTPUT_DIR / "niuone_mainline_summary_latest.json"
STOCK_INDUSTRY_CACHE_FILE = CRON_OUTPUT_DIR / "stock_industry_cache.json"
EASTMONEY_BOARD_CACHE_FILE = CRON_OUTPUT_DIR / "eastmoney_stock_boards.json"
MONEY_FLOW_SNAPSHOT_FILE = CRON_OUTPUT_DIR / "industry_main_money_flow_cache.json"
TURNOVER_PROFILE_CACHE_FILE = CRON_OUTPUT_DIR / "turnover_profile_cache.json"
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
INDUSTRY_FLOW_PLAYBACK_SPEED_OPTIONS = (0.5, 0.75, 1.0, 1.5, 2.0, 5.0, 10.0)
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
B1_SCAN_TIMEOUT_SECONDS = int(os.environ.get("DASHBOARD_B1_SCAN_TIMEOUT_SECONDS", "480") or "480")
PRACTICE_SCHEDULE_TIMES_ENV = "DASHBOARD_PRACTICE_SCHEDULE_TIMES"
LEGACY_B1_SCHEDULE_TIMES_ENV = "DASHBOARD_B1_SCHEDULE_TIMES"
DEFAULT_PRACTICE_SCHEDULE_TIMES = "09:25,10:00,10:30,11:00,11:20,13:00,13:30,14:00,14:30,14:50"
PRACTICE_FAST_CYCLE_ENABLED_ENV = "DASHBOARD_PRACTICE_FAST_CYCLE_ENABLED"
PRACTICE_FAST_CYCLE_INTERVAL_ENV = (
    "DASHBOARD_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS"
)
DEFAULT_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS = 300
MIN_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS = 60
MAX_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS = 900
NIUONE_FORWARD_COHORT_START_ENV = "DASHBOARD_NIUONE_FORWARD_COHORT_START"
DEFAULT_NIUONE_FORWARD_COHORT_START = "2026-09-10"


def resolve_practice_schedule_times(values: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Resolve the shared Practice schedule, preferring the renamed setting."""
    source = os.environ if values is None else values
    raw = (
        source.get(PRACTICE_SCHEDULE_TIMES_ENV)
        if PRACTICE_SCHEDULE_TIMES_ENV in source
        else source.get(LEGACY_B1_SCHEDULE_TIMES_ENV, DEFAULT_PRACTICE_SCHEDULE_TIMES)
    )
    return tuple(
        value.strip()
        for value in str(raw or "").split(",")
        if value.strip()
    )


PRACTICE_SCHEDULE_TIMES = resolve_practice_schedule_times()
PRACTICE_FAST_CYCLE_ENABLED = str(
    os.environ.get(PRACTICE_FAST_CYCLE_ENABLED_ENV, "0") or "0"
).strip().lower() not in {"0", "false", "no", "off"}
PRACTICE_FAST_CYCLE_INTERVAL_SECONDS = _bounded_int_value(
    os.environ.get(PRACTICE_FAST_CYCLE_INTERVAL_ENV),
    DEFAULT_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS,
    MIN_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS,
    MAX_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS,
)
B1_SCHEDULE_ENABLED = os.environ.get("DASHBOARD_B1_SCHEDULE_ENABLED", "1").lower() not in {"0", "false", "no"}
B1_SCHEDULE_STATE_FILE = CRON_STATE_DIR / "b1_schedule_state.json"
B1_SCHEDULE_HISTORY_RETENTION_DAYS = 400
B1_SCHEDULE_CATCHUP_MINUTES = int(os.environ.get("DASHBOARD_B1_SCHEDULE_CATCHUP_MINUTES", "35") or "35")
B1_SCHEDULE_STALE_SECONDS = int(os.environ.get("DASHBOARD_B1_SCHEDULE_STALE_SECONDS", "900") or "900")
B1_SCHEDULE_RUN_KEYS: set[str] = set()
B1_SCHEDULE_LOCK = threading.RLock()
B1_SCHEDULE_THREAD: threading.Thread | None = None
PRACTICE_FAST_CYCLE_THREAD: threading.Thread | None = None
NIUONE_MAINLINE_SCAN_LOCK = threading.Lock()
NIUONE_MAINLINE_SCAN_THREAD: threading.Thread | None = None
DEFAULT_KLINE_PREWARM_TIME = "09:10"
KLINE_CACHE_ENABLED = os.environ.get("DASHBOARD_KLINE_CACHE_ENABLED", "1").lower() not in {"0", "false", "no", "off"}
KLINE_PREWARM_ENABLED = KLINE_CACHE_ENABLED and os.environ.get("DASHBOARD_KLINE_PREWARM_ENABLED", "1").lower() not in {"0", "false", "no", "off"}
KLINE_PREWARM_TIME = os.environ.get("DASHBOARD_KLINE_PREWARM_TIME", DEFAULT_KLINE_PREWARM_TIME).strip() or DEFAULT_KLINE_PREWARM_TIME
KLINE_PREWARM_CATCHUP_MINUTES = int(os.environ.get("DASHBOARD_KLINE_PREWARM_CATCHUP_MINUTES", "15") or "15")
KLINE_PREWARM_TIMEOUT_SECONDS = int(os.environ.get("DASHBOARD_KLINE_PREWARM_TIMEOUT_SECONDS", "600") or "600")
KLINE_PREWARM_RETRY_SECONDS = int(os.environ.get("DASHBOARD_KLINE_PREWARM_RETRY_SECONDS", "300") or "300")
KLINE_BOOTSTRAP_ENABLED = os.environ.get(
    "DASHBOARD_KLINE_BOOTSTRAP_ENABLED", "1"
).lower() not in {"0", "false", "no", "off"}
KLINE_BOOTSTRAP_MAX_ATTEMPTS = _bounded_int_value(
    os.environ.get("DASHBOARD_KLINE_BOOTSTRAP_MAX_ATTEMPTS", "3"), 3, 1, 12
)
KLINE_READINESS_MIN_COVERAGE_PERCENT = _bounded_int_value(
    os.environ.get("DASHBOARD_KLINE_READINESS_MIN_COVERAGE_PERCENT", "90"),
    90,
    90,
    100,
)
KLINE_READINESS_MIN_COVERAGE = KLINE_READINESS_MIN_COVERAGE_PERCENT / 100
KLINE_PREWARM_LOCK = threading.Lock()
KLINE_PREWARM_RUN_THREAD: threading.Thread | None = None
KLINE_PREWARM_SCHEDULER_THREAD: threading.Thread | None = None
KLINE_PREWARM_LAST_ATTEMPT_TS = 0.0
KLINE_PREWARM_ATTEMPTS_BY_DATE: dict[str, int] = {}
NIUONE_MAINLINE_MINUTE_REFRESH_ENABLED = str(
    os.environ.get("DASHBOARD_NIUONE_MAINLINE_MINUTE_REFRESH_ENABLED", "1") or "1"
).strip().lower() not in {"0", "false", "no", "off"}
NIUONE_MAINLINE_MINUTE_STATE_LOCK = threading.Lock()
NIUONE_MAINLINE_MINUTE_PENDING: dict[str, Any] | None = None
NIUONE_MAINLINE_MINUTE_THREAD: threading.Thread | None = None
NIUONE_MAINLINE_MINUTE_ENGINE: NiuOneMinuteEngine | None = None
NIUONE_MAINLINE_MINUTE_ENGINE_PATHS: tuple[str, str] = ("", "")
NIUONE_MAINLINE_MINUTE_NEXT_ALLOWED_MONOTONIC = 0.0
NIUONE_MAINLINE_MINUTE_MAX_CPU_SHARE = 0.25
NIUONE_MAINLINE_MINUTE_MAX_COOLDOWN_SECONDS = 300.0
NIUONE_MAINLINE_MINUTE_PROCESS_TIMEOUT_SECONDS = 180.0
NIUONE_MAINLINE_MINUTE_BUSY_RETRY_SECONDS = 15.0
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
MARKET_BREADTH_AUTO_RECOVERY_THREAD: threading.Thread | None = None
DAILY_MARKET_HISTORY_RESET_THREAD: threading.Thread | None = None
MARKET_BREADTH_AUTO_RECOVERY_DEADLINE_SECONDS = 900
MARKET_BREADTH_AUTO_RECOVERY_PROCESS_TIMEOUT_SECONDS = 960
MARKET_BREADTH_AUTO_RECOVERY_RETRY_SECONDS = 60.0
MARKET_BREADTH_AUTO_RECOVERY_MAX_ATTEMPTS = 3
MARKET_API_PREWARM_THREAD: threading.Thread | None = None
MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS = _bounded_int_value(
    os.environ.get(
        "DASHBOARD_MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS",
        str(MARKET_BREADTH_DEFAULT_SAMPLE_INTERVAL_SECONDS),
    ),
    MARKET_BREADTH_DEFAULT_SAMPLE_INTERVAL_SECONDS,
    30,
    600,
)
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
B1_FULL_SCAN_CONDITION = threading.Condition(threading.RLock())
B1_FULL_SCAN_ACTIVE: dict[str, Any] = {}
B1_FULL_SCAN_LAST_RESULT: dict[str, Any] = {}
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
PRACTICE_DECISION_LOCK = threading.Lock()
PRACTICE_DECISION_KEYS: set[str] = set()
PRACTICE_MANUAL_CYCLE_LOCK = threading.Lock()
PRACTICE_MANUAL_CYCLE_STATE_LOCK = threading.RLock()
PRACTICE_MANUAL_SCAN_REUSE_SECONDS = max(
    0,
    int(os.environ.get("DASHBOARD_MANUAL_SCAN_REUSE_SECONDS", "0") or "0"),
)
PRACTICE_MANUAL_ERROR_DISPLAY_SECONDS = 10 * 60
PRACTICE_MANUAL_CYCLE_STATE: dict[str, Any] = {
    "running": False,
    "stage": "idle",
    "started_at": "",
    "finished_at": "",
    "error": "",
}
PRACTICE_MANUAL_CYCLE_PUBLIC_FIELDS = (
    "job_id",
    "running",
    "stage",
    "stage_label",
    "completed",
    "total",
    "progress_pct",
    "cache_hits",
    "network_fallbacks",
    "worker_count",
    "source",
    "started_at",
    "updated_at",
    "finished_at",
    "generated_at",
    "candidate_count",
    "manual_scan_reused",
    "joined_existing_scan",
    "active_scan_job_id",
    "notice_code",
    "notice",
    "failure_stage",
    "error_code",
    "error",
)
PRACTICE_MARKET_SUMMARY_LOCK = threading.Lock()
PRACTICE_MARKET_SUMMARY_STATE_LOCK = threading.RLock()
PRACTICE_MARKET_SUMMARY_STATE: dict[str, Any] = {
    "running": False,
    "stage": "idle",
    "stage_label": "",
    "started_at": "",
    "finished_at": "",
    "generated_at": "",
    "error": "",
}
PRACTICE_MARKET_SUMMARY_PUBLIC_FIELDS = (
    "running",
    "stage",
    "stage_label",
    "started_at",
    "finished_at",
    "generated_at",
    "error",
)
BENCHMARK_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
BENCHMARK_TTL_SECONDS = 20
CN_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

# Public dashboard concurrency protection: cache expensive JSON payloads in-process
# so 1000 viewers do not trigger 1000 identical DB/行情/akshare computations.
API_RESPONSE_CACHE: dict[str, dict[str, Any]] = {}
API_RESPONSE_LOCK = threading.RLock()
TODAY_CANDIDATES_LOCK = threading.Lock()
API_CACHE_KEY_LOCKS: dict[str, threading.Lock] = {}
API_CACHE_KEY_GENERATIONS: dict[str, int] = {}
API_CACHE_MAX_ENTRIES = int(os.environ.get("DASHBOARD_API_CACHE_MAX_ENTRIES", "256") or "256")
API_STALE_WHILE_REFRESH_SECONDS = int(
    os.environ.get("DASHBOARD_API_STALE_WHILE_REFRESH_SECONDS", "300") or "300"
)
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
PROMPT_REFINEMENT_MAX_CONCURRENCY = max(
    1,
    min(2, int(os.environ.get("DASHBOARD_PROMPT_REFINEMENT_MAX_CONCURRENCY", "1") or "1")),
)
PROMPT_REFINEMENT_SEMAPHORE = threading.BoundedSemaphore(
    PROMPT_REFINEMENT_MAX_CONCURRENCY
)
PROMPT_REFINEMENT_MAX_ATTEMPTS = 2
IWENCAI_TEST_MAX_CONCURRENCY = 2
IWENCAI_TEST_SEMAPHORE = threading.BoundedSemaphore(IWENCAI_TEST_MAX_CONCURRENCY)
RATE_LIMIT_BUCKETS: dict[tuple[str, str], tuple[float, int]] = {}
RATE_LIMIT_LOCK = threading.Lock()
ADMIN_TOKEN_LOCK = threading.Lock()
VISIT_STATS_LOCK = threading.RLock()
VISIT_STATS_INIT_SIGNATURE: tuple[Any, ...] | None = None
ENV_FILE_WRITE_LOCK = threading.RLock()
NEWSNOW_SERVICE_LOCK = threading.Lock()
NEWSNOW_SERVICE: NewsNowService | None = None
NEWSNOW_CONFIG_NAMES = (
    "NEWSNOW_ENABLED",
    "NEWSNOW_DECISION_ENABLED",
    "NEWSNOW_OVERVIEW_IMPORTANT_ONLY",
    "NEWSNOW_BASE_URL",
    "NEWSNOW_SOURCES",
    "NEWSNOW_MAX_ITEMS",
    "NEWSNOW_MAX_IMPORTANT_ITEMS",
    "NEWSNOW_REFRESH_SECONDS",
    "NEWSNOW_TIMEOUT_SECONDS",
    "NEWSNOW_MAX_RETRIES",
    "NEWSNOW_MAX_CONCURRENCY",
)
PRACTICE_CANDIDATES_CACHE_KEY = "practice_candidates"
TODAY_CANDIDATES_CACHE_KEY = "today_candidates"
TODAY_CANDIDATE_INTRADAY_CACHE_KEY = "today_candidate_intraday"
NIUONE_MAINLINE_CACHE_KEY = "niuone_mainline"
PRACTICE_CANDIDATES_API_PATHS = frozenset({"/api/practice_candidates", "/api/b1_screen"})
PRACTICE_CANDIDATES_REFRESH_API_PATHS = frozenset({"/api/practice_candidates/refresh", "/api/b1_screen/trigger"})
PRACTICE_MANUAL_CYCLE_API_PATH = "/api/niuniu_practice/manual-cycle"
PRACTICE_MARKET_SUMMARY_API_PATH = "/api/niuniu_practice/market-summary"
PRACTICE_MARKET_SUMMARY_FILE = CRON_OUTPUT_DIR / "practice_market_summary_latest.json"
API_TTLS = {
    "messages": 10,
    "realtime_news": 15,
    "practice_candidates": int(
        os.environ.get("DASHBOARD_PRACTICE_CANDIDATES_TTL_SECONDS")
        or os.environ.get("DASHBOARD_B1_SCREEN_TTL_SECONDS")
        or "15"
    ),
    "today_candidates": int(
        os.environ.get("DASHBOARD_PRACTICE_CANDIDATES_TTL_SECONDS")
        or os.environ.get("DASHBOARD_B1_SCREEN_TTL_SECONDS")
        or "15"
    ),
    "today_candidate_intraday": 45,
    "niuone_mainline": int(os.environ.get("DASHBOARD_NIUONE_MAINLINE_TTL_SECONDS", "15") or "15"),
    "niuniu_practice": int(os.environ.get("DASHBOARD_PRACTICE_TTL_SECONDS", "15") or "15"),
    "practice_benchmarks": 30,
    "indices": int(os.environ.get("DASHBOARD_INDICES_TTL_SECONDS", "60") or "60"),
    "market_breadth": MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS,
    "sectors": 60,
    "us_sectors": int(os.environ.get("DASHBOARD_US_SECTORS_TTL_SECONDS", "300") or "300"),
    "hot_stocks": 60,
    "money_flow": 60,
    "industry_flow": 30,
    "market_flow": 30,
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

ENV_CONFIG_SCHEMA: list[dict[str, Any]] = [
    {"name": "DASHBOARD_HOME", "label": "运行数据目录", "group": "基础路径", "kind": "path", "default": str(LOCAL_DATA_DIR / "runtime"), "effect": "restart"},
    {"name": "DASHBOARD_HOST", "label": "监听地址", "group": "基础路径", "kind": "text", "default": "127.0.0.1", "effect": "restart"},
    {"name": "DASHBOARD_PORT", "label": "监听端口", "group": "基础路径", "kind": "int", "default": "8787", "effect": "restart"},
    {"name": "PYTHON_BIN", "label": "Python 可执行文件", "group": "基础路径", "kind": "path", "default": "", "effect": "restart"},
    {"name": "DASHBOARD_CONFIG", "label": "模型配置 YAML", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "config.yaml"), "effect": "restart"},
    {"name": "DASHBOARD_PUSH_HISTORY_DB", "label": "消息历史 DB", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "push_history.db"), "effect": "restart"},
    {"name": "DASHBOARD_PORTFOLIO_STATE", "label": "模拟账户状态文件", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "cron" / "output" / "niuniu_practice_portfolio.json"), "effect": "restart"},
    {"name": "DASHBOARD_NIUNIU_DB", "label": "实战页面 DB", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "niuniu.db"), "effect": "restart"},
    {"name": "DASHBOARD_PROMPT_STRATEGY_DB", "label": "文字策略版本与审计 DB", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "prompt_strategies.db"), "effect": "restart"},
    {"name": "DASHBOARD_TRADER_SCRIPT", "label": "实战页面脚本", "group": "基础路径", "kind": "path", "default": str(ENTRYPOINT_DIR / "niuniu_practice_trader.py"), "effect": "restart"},
    {"name": "DASHBOARD_B1_SCANNER", "label": "实战选股扫描脚本", "group": "基础路径", "kind": "path", "default": str(ENTRYPOINT_DIR / "multi_strategy_screen.py"), "effect": "restart"},
    {"name": "DASHBOARD_CN_STOCK_TOOLS", "label": "A股行情工具脚本", "group": "基础路径", "kind": "path", "default": str(ENTRYPOINT_DIR / "cn_stock_tools.py"), "effect": "restart"},
    {"name": "DASHBOARD_CRON_JOBS", "label": "Cron jobs JSON", "group": "基础路径", "kind": "path", "default": str(DASHBOARD_HOME / "cron" / "jobs.json"), "effect": "next_run"},
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
    {"name": "DASHBOARD_PUBLIC_REFRESH_SECONDS", "label": "公开快照刷新秒数", "group": "行情与资金流设置", "kind": "int", "default": "15", "effect": "restart"},
    {"name": "DASHBOARD_NIUONE_MAINLINE_MINUTE_REFRESH_ENABLED", "label": "题材强度跟随全市场行情更新", "group": "行情与资金流设置", "kind": "bool", "default": "1", "effect": "restart"},
    {
        "name": "DASHBOARD_MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS",
        "label": "全市场行情采样间隔（秒）",
        "group": "行情与资金流设置",
        "kind": "int",
        "default": "30",
        "effect": "restart",
        "min": "30",
        "max": "600",
        "help_title": "影响范围",
        "help_summary": "控制交易时段内共享的腾讯沪深 A 股全市场逐股行情采样频率。",
        "help_items": [
            {
                "label": "题材强度",
                "description": "决定获取最新逐股价格并重新计算题材强度的频率；复用同一批行情，不会再向腾讯重复抓取。",
            },
            {
                "label": "市场情绪",
                "description": "决定红盘、绿盘、涨跌停、炸板和腾讯兜底成交额等聚合曲线的真实采样频率。",
            },
            {
                "label": "请求负载",
                "description": "间隔越短，全市场分片请求越频繁；请求不完整、超时或计算失败时继续保留上一份有效结果。",
            },
        ],
        "help_footer": "仅在 A 股交易日 09:30–11:30、13:00–15:00 生效；允许 30–600 秒，保存后需重启 Dashboard。",
    },

    {
        "name": "NEWSNOW_ENABLED",
        "label": "启用财经快讯",
        "group": "财经快讯",
        "kind": "bool",
        "default": "1",
        "effect": "runtime",
        "bool_no_default": "1",
    },
    {
        "name": "NEWSNOW_DECISION_ENABLED",
        "label": "重要快讯辅助买卖决策",
        "group": "财经快讯",
        "kind": "bool",
        "default": "1",
        "effect": "runtime",
        "bool_no_default": "1",
        "help_title": "决策信息归属",
        "help_summary": "开启后，模型决策只读取上游标记为重要且具备可靠发布时间的财经快讯。",
        "help_footer": "交易日 15:00 前的快讯归属当日；15:00 后及休市日快讯归属下一交易日。快讯只作辅助，不会绕过候选资格、仓位与风控。",
    },
    {
        "name": "NEWSNOW_OVERVIEW_IMPORTANT_ONLY",
        "label": "在总览中仅显示重要信息",
        "group": "财经快讯",
        "kind": "bool",
        "default": "1",
        "effect": "runtime",
        "bool_no_default": "1",
    },
    {
        "name": "NEWSNOW_SOURCES",
        "label": "新闻数据源",
        "group": "财经快讯",
        "kind": "news_sources",
        "default": ",".join(DEFAULT_NEWSNOW_SOURCE_IDS),
        "effect": "runtime",
        "help_title": "NewsNow 数据源",
        "help_summary": "可搜索并多选 NewsNow 当前财经商业来源；至少选择一项。",
        "help_footer": "兼容跳转别名不会重复显示。来源越多，首次刷新耗时和上游请求量越大，建议按需选择。",
    },
    {
        "name": "NEWSNOW_MAX_ITEMS",
        "label": "快讯总保留上限",
        "group": "财经快讯",
        "kind": "int",
        "default": str(DEFAULT_NEWSNOW_MAX_ITEMS),
        "effect": "runtime",
        "min": str(NEWSNOW_MAX_ITEMS_MIN),
        "max": str(NEWSNOW_MAX_ITEMS_MAX),
        "help_title": "滚动历史容量",
        "help_summary": "控制财经快讯完整页和本地持久缓存合计保留的最大条数。",
        "help_footer": "成功刷新会合并去重后按新到旧裁剪；重要快讯在总容量内优先保留。",
    },
    {
        "name": "NEWSNOW_MAX_IMPORTANT_ITEMS",
        "label": "重要快讯保留上限",
        "group": "财经快讯",
        "kind": "int",
        "default": str(DEFAULT_NEWSNOW_MAX_IMPORTANT_ITEMS),
        "effect": "runtime",
        "min": str(NEWSNOW_MAX_IMPORTANT_ITEMS_MIN),
        "max": str(NEWSNOW_MAX_IMPORTANT_ITEMS_MAX),
        "help_title": "重要快讯容量",
        "help_summary": "控制滚动历史中最多保留多少条上游标记为重要的快讯。",
        "help_footer": "该值不能大于快讯总保留上限；达到上限后优先淘汰最旧的重要快讯。",
    },
    {"name": "NEWSNOW_REFRESH_SECONDS", "label": "本地刷新间隔（秒）", "group": "财经快讯", "kind": "int", "default": "60", "effect": "runtime", "min": "15", "max": "1800"},
    {"name": "NEWSNOW_TIMEOUT_SECONDS", "label": "单次请求超时（秒）", "group": "财经快讯", "kind": "int", "default": "10", "effect": "runtime", "min": "2", "max": "30"},
    {"name": "NEWSNOW_MAX_RETRIES", "label": "失败重试次数", "group": "财经快讯", "kind": "int", "default": "1", "effect": "runtime", "min": "0", "max": "2"},
    {"name": "NEWSNOW_MAX_CONCURRENCY", "label": "最大并发来源数", "group": "财经快讯", "kind": "int", "default": "3", "effect": "runtime", "min": "1", "max": "3"},

    {"name": "DASHBOARD_B1_SCHEDULE_ENABLED", "label": "启用实战定时运行", "group": "任务调度", "kind": "bool", "default": "1", "effect": "restart"},
    {"name": PRACTICE_SCHEDULE_TIMES_ENV, "label": "实战盘面总结、选股及交易时间点", "group": "选股与买卖设置", "kind": "time_list", "default": DEFAULT_PRACTICE_SCHEDULE_TIMES, "effect": "runtime"},
    {"name": PRACTICE_FAST_CYCLE_ENABLED_ENV, "label": "启用持仓快周期", "group": "选股与买卖设置", "kind": "bool", "default": "0", "effect": "runtime", "help_title": "同策略持仓快周期", "help_summary": "仅缩小到当前持仓并提高触发频率，评分、模型决策、退出优先和成交风控与完整选股周期共用。", "help_footer": "快周期 BUY 只表示已有持仓加仓，不发现或首次买入新股票；默认关闭。"},
    {"name": PRACTICE_FAST_CYCLE_INTERVAL_ENV, "label": "持仓快周期间隔（秒）", "group": "选股与买卖设置", "kind": "int", "default": str(DEFAULT_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS), "effect": "runtime", "min": str(MIN_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS), "max": str(MAX_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS), "help_title": "持仓重评频率", "help_summary": "在A股可成交时段按该间隔重新评判当前持仓，默认300秒。", "help_footer": "设置越短，行情与模型调用越频繁；同一时刻仍只允许一个账户决策事务。"},
    {"name": STOCK_UNIVERSE_ENV, "label": "选股范围（限制最终候选与新买入）", "group": "选股与买卖设置", "kind": "stock_universe", "default": DEFAULT_STOCK_UNIVERSE, "effect": "runtime"},
    {"name": "DASHBOARD_DISPLAY_CANDIDATE_LIMIT", "label": "候选池展示数量", "group": "选股与买卖设置", "kind": "int", "default": "10", "effect": "runtime"},
    {"name": "DASHBOARD_TRADE_CANDIDATE_LIMIT", "label": "买卖决策候选数量", "group": "选股与买卖设置", "kind": "int", "default": "10", "effect": "runtime"},
    {"name": "DASHBOARD_PRESET_STRATEGY_CANDIDATE_LIMIT", "label": "文字策略中性候选数量", "group": "选股与交易策略", "kind": "int", "default": "60", "effect": "runtime", "min": "10", "max": "100"},
    {"name": "DASHBOARD_B3_EXIT_TIME", "label": "B3开盘离场检查时间", "group": "选股与买卖设置", "kind": "time", "default": "09:37", "effect": "runtime"},
    {"name": "DASHBOARD_TIME_EXIT_TIME", "label": "尾盘离场检查时间", "group": "选股与买卖设置", "kind": "time", "default": "14:45", "effect": "runtime"},
    {"name": "DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON", "label": "牛牛严格前向开盘前协议预检", "group": "选股与买卖设置", "kind": "cron_time", "default": "5 9 * * 1-5", "effect": "next_run"},
    {"name": "DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON", "label": "牛牛严格前向盘后净值快照", "group": "选股与买卖设置", "kind": "cron_time", "default": "15 15 * * 1-5", "effect": "next_run"},
    {"name": "DASHBOARD_NIUONE_FORWARD_CRON", "label": "牛牛严格前向评估时间", "group": "选股与买卖设置", "kind": "cron_time", "default": "20 15 * * 1-5", "effect": "next_run"},
    {"name": NIUONE_FORWARD_COHORT_START_ENV, "label": "牛牛严格前向队列起始日", "group": "选股与买卖设置", "kind": "text", "default": DEFAULT_NIUONE_FORWARD_COHORT_START, "effect": "next_run"},
    {"name": ACTIVE_STRATEGY_ENV, "label": "当前独立策略", "group": "选股与交易策略", "kind": "strategy_suite", "default": default_enabled_persona_strategies_value(), "effect": "runtime"},
    {"name": PRESET_STRATEGY_TEXT_ENV, "label": "旧版预设文字（兼容）", "group": "选股与交易策略", "kind": "preset_strategy_text", "default": "", "effect": "runtime"},
    {"name": "DASHBOARD_B1_SCAN_TIMEOUT_SECONDS", "label": "实战选股扫描超时秒数", "group": "任务调度", "kind": "int", "default": "480", "effect": "restart", "min": "60", "max": "1800"},
    {"name": "DASHBOARD_B1_SCAN_WORKERS", "label": "实战选股并发数", "group": "任务调度", "kind": "int", "default": "6", "effect": "restart", "min": "1", "max": "16"},
    {"name": "DASHBOARD_TENCENT_QUOTE_STAGE_TIMEOUT_SECONDS", "label": "腾讯全市场行情阶段总超时秒数", "group": "任务调度", "kind": "int", "default": "90", "effect": "restart", "min": "15", "max": "300"},
    {"name": "DASHBOARD_KLINE_CACHE_ENABLED", "label": "启用本地日K缓存", "group": "任务调度", "kind": "bool", "default": "1", "effect": "restart"},
    {"name": "DASHBOARD_KLINE_PREWARM_ENABLED", "label": "启用盘前日K预热", "group": "任务调度", "kind": "bool", "default": "1", "effect": "restart"},
    {"name": "DASHBOARD_KLINE_PREWARM_TIME", "label": "盘前日K预热时间", "group": "任务调度", "kind": "time", "default": DEFAULT_KLINE_PREWARM_TIME, "effect": "restart"},
    {"name": "DASHBOARD_KLINE_PREWARM_WORKERS", "label": "盘前日K预热并发数", "group": "任务调度", "kind": "int", "default": "12", "effect": "restart"},
    {"name": "DASHBOARD_KLINE_PREWARM_TIMEOUT_SECONDS", "label": "盘前日K预热超时秒数", "group": "任务调度", "kind": "int", "default": "600", "effect": "restart"},
    {"name": "DASHBOARD_KLINE_PREWARM_CATCHUP_MINUTES", "label": "盘前日K预热补跑窗口分钟", "group": "任务调度", "kind": "int", "default": "15", "effect": "restart"},
    {"name": "DASHBOARD_KLINE_BOOTSTRAP_ENABLED", "label": "部署后自动初始化日K", "group": "任务调度", "kind": "bool", "default": "1", "effect": "restart"},
    {"name": "DASHBOARD_KLINE_BOOTSTRAP_MAX_ATTEMPTS", "label": "日K初始化最大尝试次数", "group": "任务调度", "kind": "int", "default": "3", "effect": "restart"},
    {"name": "DASHBOARD_KLINE_READINESS_MIN_COVERAGE_PERCENT", "label": "日K安全覆盖率百分比", "group": "任务调度", "kind": "int", "default": "90", "effect": "restart", "min": "90", "max": "100"},
    {"name": "DASHBOARD_MANUAL_DATA_INITIALIZATION_TIMEOUT_SECONDS", "label": "手动任务等待数据初始化秒数", "group": "任务调度", "kind": "int", "default": "660", "effect": "restart"},
    {"name": "DASHBOARD_MANUAL_SCAN_REUSE_SECONDS", "label": "手动选股复用候选秒数", "group": "任务调度", "kind": "int", "default": "0", "effect": "restart"},
    {"name": "DASHBOARD_B1_SCHEDULE_CATCHUP_MINUTES", "label": "实战选股漏触发补跑窗口分钟", "group": "任务调度", "kind": "int", "default": "35", "effect": "restart"},
    {"name": "DASHBOARD_B1_SCHEDULE_STALE_SECONDS", "label": "实战选股运行中陈旧秒数", "group": "任务调度", "kind": "int", "default": "900", "effect": "restart"},
    {"name": "DASHBOARD_CRON_MAX_ATTEMPTS", "label": "Cron 失败最大运行次数", "group": "任务调度", "kind": "int", "default": "2", "effect": "next_run"},
    {"name": "DASHBOARD_CRON_RETRY_DELAY_SECONDS", "label": "Cron 失败重试间隔秒数", "group": "任务调度", "kind": "int", "default": "300", "effect": "next_run"},
    {"name": "DASHBOARD_PENDING_DECISION_POLL_SECONDS", "label": "延迟成交检查秒数", "group": "任务调度", "kind": "int", "default": "5", "effect": "restart"},

    {"name": "DASHBOARD_DECISION_MAX_TOKENS", "label": "模型最大输出长度", "group": "模型配置", "kind": "max_tokens", "default": DEFAULT_MODEL_MAX_TOKENS, "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_TIMEOUT", "label": "买卖决策请求超时", "group": "任务调度", "kind": "int", "default": "180", "effect": "next_run"},
    {"name": "DASHBOARD_PROMPT_REFINEMENT_MAX_CONCURRENCY", "label": "文字策略细化并发数", "group": "任务调度", "kind": "int", "default": "1", "effect": "restart", "min": "1", "max": "2"},
    {"name": "DASHBOARD_DECISION_INTELLIGENCE_ENABLED", "label": "启用综合决策参考", "group": "综合决策参考", "kind": "bool", "default": "1", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS", "label": "决策参考缓存秒数", "group": "综合决策参考", "kind": "int", "default": "75", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS", "label": "单类参考数据上限", "group": "综合决策参考", "kind": "int", "default": "5", "effect": "next_run"},

    {"name": "IWENCAI_ENABLED", "label": "启用问财数据源", "group": "问财数据源", "kind": "bool", "default": "0", "effect": "runtime"},
    {"name": "IWENCAI_NEWS_PRECHECK_ENABLED", "label": "开启消息面预检", "group": "问财数据源", "kind": "bool", "default": "0", "effect": "next_run"},
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
    {"name": "DASHBOARD_EXIT_FEEDBACK_AUTO_TUNE_ENABLED", "label": "启用5日复盘全自动调参", "group": "交易规则与风控", "kind": "bool", "default": "1", "effect": "next_run"},
    {"name": "DASHBOARD_EXIT_FEEDBACK_MIN_SAMPLES", "label": "自动调参最少有效样本簇", "group": "交易规则与风控", "kind": "int", "default": "30", "effect": "next_run", "min": "20", "max": "500"},
    {"name": "DASHBOARD_EXIT_FEEDBACK_MIN_MONTHS", "label": "自动调参最少覆盖月份", "group": "交易规则与风控", "kind": "int", "default": "3", "effect": "next_run", "min": "2", "max": "12"},
    {"name": "DASHBOARD_EXIT_FEEDBACK_COOLDOWN_SAMPLES", "label": "每轮评估新增证据冷却", "group": "交易规则与风控", "kind": "int", "default": "10", "effect": "next_run", "min": "5", "max": "100"},

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
    {"name": "DASHBOARD_SERVERCHAN_NOTIFICATION_ENABLED", "label": "启用 Server酱通知", "group": "交易通知", "kind": "bool", "default": "0", "effect": "runtime"},
    {"name": "DASHBOARD_SERVERCHAN_SENDKEY", "label": "Server酱 SendKey", "group": "交易通知", "kind": "secret", "default": "", "effect": "runtime"},

    {"name": "CROSSDESK_BASE_URL", "label": "Crossdesk Base URL", "group": "上游模型覆盖", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "CROSSDESK_API_KEY", "label": "Crossdesk API Key", "group": "上游模型覆盖", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_MODEL", "label": "模型名称", "group": "模型配置", "kind": "text", "default": "deepseek-v4-pro", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_STREAM_MODE", "label": "流式模式", "group": "模型配置", "kind": "stream_mode", "default": "auto", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_REASONING_EFFORT", "label": "思考强度", "group": "模型配置", "kind": "reasoning_effort", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_CONTEXT_LENGTH", "label": "上下文长度", "group": "模型配置", "kind": "context_length", "default": DEFAULT_MODEL_CONTEXT_LENGTH, "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_BASE_URL", "label": "API 地址", "group": "模型配置", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_DECISION_API_KEY", "label": "API 密钥", "group": "模型配置", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "DASHBOARD_US_MARKET_SUMMARY_CRON", "label": "隔夜美股盘面总结时间", "group": "盘面监控生产时间点", "kind": "cron_time", "default": "0 8 * * 1-5", "effect": "next_run"},
    {"name": "US_MARKET_SUMMARY_MAX_TOKENS", "label": "隔夜美股总结最大输出长度", "group": "盘面监控生产时间点", "kind": "max_tokens", "default": DEFAULT_MODEL_MAX_TOKENS, "effect": "next_run"},
    {"name": "DASHBOARD_MARKET_AUCTION_CRON", "label": "盘前竞价监控时间", "group": "盘面监控生产时间点", "kind": "cron_time", "default": "25 9 * * 1-5", "effect": "next_run"},
    {"name": "DASHBOARD_MARKET_MIDDAY_CRON", "label": "午盘监控时间", "group": "盘面监控生产时间点", "kind": "cron_time", "default": "40 11 * * 1-5", "effect": "next_run"},
    {"name": "DASHBOARD_MARKET_CLOSE_CRON", "label": "盘后监控时间", "group": "盘面监控生产时间点", "kind": "cron_time", "default": "10 15 * * 1-5", "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_ENABLED", "label": "A股盘面模型总结", "group": "盘面监控生产时间点", "kind": "bool", "default": "1", "effect": "next_run", "bool_no_default": "1"},
    {"name": "A_SHARE_MODEL_SUMMARY_MODEL", "label": "盘面总结模型（A股与隔夜美股）", "group": "盘面监控生产时间点", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_STREAM_MODE", "label": "盘面总结流式模式", "group": "盘面监控生产时间点", "kind": "stream_mode", "default": "auto", "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_REASONING_EFFORT", "label": "盘面总结思考强度", "group": "盘面监控生产时间点", "kind": "reasoning_effort", "default": "", "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_CONTEXT_LENGTH", "label": "盘面总结上下文长度", "group": "盘面监控生产时间点", "kind": "context_length", "default": DEFAULT_MODEL_CONTEXT_LENGTH, "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_MAX_TOKENS", "label": "A股盘面总结最大输出长度", "group": "盘面监控生产时间点", "kind": "max_tokens", "default": DEFAULT_MODEL_MAX_TOKENS, "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_BASE_URL", "label": "盘面总结 API地址", "group": "盘面监控生产时间点", "kind": "text", "default": "", "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_API_KEY", "label": "盘面总结 API密钥", "group": "盘面监控生产时间点", "kind": "secret", "default": "", "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_DEADLINE_SECONDS", "label": "A股模型总结总超时秒数", "group": "盘面监控生产时间点", "kind": "int", "default": "60", "effect": "next_run"},
    {"name": "A_SHARE_MODEL_SUMMARY_REQUEST_TIMEOUT_SECONDS", "label": "A股模型总结单次超时秒数", "group": "盘面监控生产时间点", "kind": "int", "default": "45", "effect": "next_run"},
    {"name": "DASHBOARD_INDICES_TTL_SECONDS", "label": "指数行情更新间隔（秒）", "group": "行情与资金流设置", "kind": "int", "default": "60", "effect": "runtime", "min": "1"},
    {"name": "DASHBOARD_INDUSTRY_FLOW_PLAYBACK_SPEED", "label": "资金流默认播放速度", "group": "行情与资金流设置", "kind": "playback_speed", "default": "0.5", "effect": "runtime"},
    {"name": "DASHBOARD_INDUSTRY_FLOW_SIDE_LIMIT", "label": "资金流每侧行业数量", "group": "行情与资金流设置", "kind": "int", "default": "10", "effect": "runtime", "min": "1", "max": "10"},
    {"name": "DASHBOARD_INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS", "label": "资金流采样间隔（秒）", "group": "行情与资金流设置", "kind": "int", "default": "60", "effect": "runtime", "min": "60", "max": "600"},
    {"name": "DASHBOARD_INDUSTRY_FLOW_MORNING_START", "label": "上午采样开始时间", "group": "行情与资金流设置", "kind": "time", "default": "09:25", "effect": "runtime"},
    {"name": "DASHBOARD_INDUSTRY_FLOW_MORNING_END", "label": "上午采样结束时间", "group": "行情与资金流设置", "kind": "time", "default": "11:31", "effect": "runtime"},
    {"name": "DASHBOARD_INDUSTRY_FLOW_AFTERNOON_START", "label": "下午采样开始时间", "group": "行情与资金流设置", "kind": "time", "default": "13:00", "effect": "runtime"},
    {"name": "DASHBOARD_INDUSTRY_FLOW_AFTERNOON_END", "label": "下午采样结束时间", "group": "行情与资金流设置", "kind": "time", "default": "15:01", "effect": "runtime"},

    {"name": "DASHBOARD_AUTO_VERSION_CHECK_ENABLED", "label": "开启自动检测新版本", "group": "关于", "kind": "bool", "default": "1", "effect": "runtime"},
]
ENV_CONFIG_BY_NAME = {item["name"]: item for item in ENV_CONFIG_SCHEMA}

REASONING_EFFORT_MODEL_NAMES: dict[str, tuple[str, ...]] = {
    "DASHBOARD_DECISION_REASONING_EFFORT": ("DASHBOARD_DECISION_MODEL",),
}

ADMIN_VISIBLE_ENV_NAMES = [
    "DASHBOARD_ADMIN_PASSWORD",
    "DASHBOARD_PUBLIC_REFRESH_SECONDS",
    "DASHBOARD_B1_SCAN_TIMEOUT_SECONDS",
    "DASHBOARD_B1_SCAN_WORKERS",
    "DASHBOARD_TENCENT_QUOTE_STAGE_TIMEOUT_SECONDS",
    "DASHBOARD_KLINE_CACHE_ENABLED",
    "DASHBOARD_KLINE_PREWARM_ENABLED",
    "DASHBOARD_KLINE_PREWARM_TIME",
    "DASHBOARD_KLINE_PREWARM_WORKERS",
    "DASHBOARD_KLINE_PREWARM_TIMEOUT_SECONDS",
    "DASHBOARD_KLINE_PREWARM_CATCHUP_MINUTES",
    "DASHBOARD_KLINE_BOOTSTRAP_ENABLED",
    "DASHBOARD_KLINE_BOOTSTRAP_MAX_ATTEMPTS",
    "DASHBOARD_KLINE_READINESS_MIN_COVERAGE_PERCENT",
    "DASHBOARD_MANUAL_DATA_INITIALIZATION_TIMEOUT_SECONDS",
    "DASHBOARD_NIUONE_MAINLINE_MINUTE_REFRESH_ENABLED",
    "DASHBOARD_MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS",
    "NEWSNOW_ENABLED",
    "NEWSNOW_DECISION_ENABLED",
    "NEWSNOW_OVERVIEW_IMPORTANT_ONLY",
    "NEWSNOW_SOURCES",
    "NEWSNOW_MAX_ITEMS",
    "NEWSNOW_MAX_IMPORTANT_ITEMS",
    "NEWSNOW_REFRESH_SECONDS",
    "NEWSNOW_TIMEOUT_SECONDS",
    "NEWSNOW_MAX_RETRIES",
    "NEWSNOW_MAX_CONCURRENCY",
    "DASHBOARD_DECISION_MODEL",
    "DASHBOARD_DECISION_STREAM_MODE",
    "DASHBOARD_DECISION_REASONING_EFFORT",
    "DASHBOARD_DECISION_CONTEXT_LENGTH",
    "DASHBOARD_DECISION_BASE_URL",
    "DASHBOARD_DECISION_API_KEY",
    "DASHBOARD_DECISION_MAX_TOKENS",
    "DASHBOARD_DECISION_TIMEOUT",
    "DASHBOARD_DECISION_INTELLIGENCE_ENABLED",
    "DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS",
    "DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS",
    "IWENCAI_ENABLED",
    "IWENCAI_NEWS_PRECHECK_ENABLED",
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
    "DASHBOARD_EXIT_FEEDBACK_AUTO_TUNE_ENABLED",
    "DASHBOARD_EXIT_FEEDBACK_MIN_SAMPLES",
    "DASHBOARD_EXIT_FEEDBACK_MIN_MONTHS",
    "DASHBOARD_EXIT_FEEDBACK_COOLDOWN_SAMPLES",
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
    "DASHBOARD_SERVERCHAN_NOTIFICATION_ENABLED",
    "DASHBOARD_SERVERCHAN_SENDKEY",
    PRACTICE_SCHEDULE_TIMES_ENV,
    PRACTICE_FAST_CYCLE_ENABLED_ENV,
    PRACTICE_FAST_CYCLE_INTERVAL_ENV,
    STOCK_UNIVERSE_ENV,
    "DASHBOARD_DISPLAY_CANDIDATE_LIMIT",
    "DASHBOARD_TRADE_CANDIDATE_LIMIT",
    "DASHBOARD_PRESET_STRATEGY_CANDIDATE_LIMIT",
    "DASHBOARD_B3_EXIT_TIME",
    "DASHBOARD_TIME_EXIT_TIME",
    "DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON",
    "DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON",
    "DASHBOARD_NIUONE_FORWARD_CRON",
    NIUONE_FORWARD_COHORT_START_ENV,
    ACTIVE_STRATEGY_ENV,
    PRESET_STRATEGY_TEXT_ENV,
    "DASHBOARD_US_MARKET_SUMMARY_CRON",
    "DASHBOARD_MARKET_AUCTION_CRON",
    "DASHBOARD_MARKET_MIDDAY_CRON",
    "DASHBOARD_MARKET_CLOSE_CRON",
    "A_SHARE_MODEL_SUMMARY_ENABLED",
    "A_SHARE_MODEL_SUMMARY_DEADLINE_SECONDS",
    "A_SHARE_MODEL_SUMMARY_REQUEST_TIMEOUT_SECONDS",
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
    "DASHBOARD_AUTO_VERSION_CHECK_ENABLED",
]
TRADER_RUNTIME_ENV_NAMES = {
    STOCK_UNIVERSE_ENV,
    "IWENCAI_NEWS_PRECHECK_ENABLED",
    "DASHBOARD_DECISION_MODEL",
    "DASHBOARD_DECISION_STREAM_MODE",
    "DASHBOARD_DECISION_REASONING_EFFORT",
    "DASHBOARD_DECISION_CONTEXT_LENGTH",
    "DASHBOARD_DECISION_BASE_URL",
    "DASHBOARD_DECISION_API_KEY",
    "DASHBOARD_DECISION_MAX_TOKENS",
    "DASHBOARD_DECISION_TIMEOUT",
    "DASHBOARD_DECISION_INTELLIGENCE_ENABLED",
    "DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS",
    "DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS",
    "NEWSNOW_DECISION_ENABLED",
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
    "财经快讯",
    "模型配置",
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
    "其他",
    "关于",
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


def accepted_kline_dates_for_dashboard(now: datetime | None = None) -> set[str]:
    """Return dates whose completed history is safe for the next live scan."""
    current = now or current_cn_datetime()
    try:
        calendar = trading_day_status(current, allow_refresh=False)
    except Exception:
        calendar = {
            "date": current.strftime("%Y-%m-%d"),
            "is_trading_day": current.weekday() < 5,
            "previous_trading_day": "",
        }
    accepted = {
        str(calendar.get("previous_trading_day") or "")[:10],
    }
    if calendar.get("is_trading_day"):
        accepted.add(str(calendar.get("date") or current.strftime("%Y-%m-%d"))[:10])
    return {
        value
        for value in accepted
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
    }


def practice_scan_requires_full_kline_cache() -> bool:
    """Dashboard trading suites consume daily history only after cache readiness."""
    return True


def _runtime_storage_status() -> dict[str, Any]:
    home = Path(DASHBOARD_HOME).expanduser()
    data_dir = Path(os.environ.get("NIUONE_CONTAINER_DATA_DIR") or LOCAL_DATA_DIR).expanduser()
    writable = home.exists() and home.is_dir() and os.access(home, os.W_OK)
    containerized = bool(os.environ.get("NIUONE_CONTAINER_DATA_DIR"))
    persistent_detected = os.path.ismount(data_dir) if containerized else True
    return {
        "writable": writable,
        "containerized": containerized,
        "persistent_storage_detected": persistent_detected,
        "error_code": (
            "runtime_storage_not_writable"
            if not writable
            else "runtime_storage_not_persistent"
            if containerized and not persistent_detected
            else ""
        ),
    }


def market_data_readiness(now: datetime | None = None) -> dict[str, Any]:
    """Build the public, non-sensitive deployment and market-data readiness view."""
    current = now or current_cn_datetime()
    accepted_dates = accepted_kline_dates_for_dashboard(current)
    cache = kline_cache_readiness(
        accepted_last_dates=accepted_dates,
        path=kline_cache_path(),
        minimum_coverage=KLINE_READINESS_MIN_COVERAGE,
    )
    try:
        active_strategy = active_strategy_suite()
    except (TypeError, ValueError):
        active_strategy = "invalid"
    requires_full_cache = practice_scan_requires_full_kline_cache()
    storage = _runtime_storage_status()
    offset = datetime.now().astimezone().utcoffset()
    timezone_ok = offset == timedelta(hours=8)
    try:
        configured_workers = int(os.environ.get("DASHBOARD_B1_SCAN_WORKERS", "6") or "6")
    except (TypeError, ValueError):
        configured_workers = 6
    cpu_count = max(1, int(os.cpu_count() or 1))
    effective_workers = max(1, min(16, configured_workers, cpu_count * 2))
    data_ready = bool(cache.get("ready")) or not requires_full_cache
    blockers = []
    warnings = []
    if requires_full_cache and not cache.get("ready"):
        if not KLINE_CACHE_ENABLED:
            blockers.append("kline_cache_disabled")
        elif not KLINE_PREWARM_ENABLED:
            blockers.append("kline_prewarm_disabled")
        else:
            blockers.append(str(cache.get("error_code") or "kline_cache_incomplete"))
    if not storage["writable"]:
        blockers.append("runtime_storage_not_writable")
    elif storage["containerized"] and not storage["persistent_storage_detected"]:
        warnings.append("runtime_storage_not_persistent")
    if not timezone_ok:
        warnings.append("timezone_not_asia_shanghai")
    ready = data_ready and storage["writable"]
    if ready and warnings:
        status = "degraded"
    elif ready:
        status = "ready"
    elif KLINE_PREWARM_ENABLED and (
        cache.get("status") == "running" or KLINE_PREWARM_LOCK.locked()
    ):
        status = "initializing"
    else:
        status = "not_ready"
    requested = int(cache.get("requested_count") or 0)
    completed = int(cache.get("completed_count") or 0)
    progress_pct = round(completed / requested * 100, 1) if requested else 0.0
    return {
        "ready": ready,
        "data_ready": data_ready,
        "status": status,
        "status_label": {
            "ready": "市场数据已就绪",
            "degraded": "市场数据可用，部署环境有提醒",
            "initializing": "正在初始化市场数据",
            "not_ready": "市场数据尚未就绪",
        }[status],
        "checked_at": current.strftime("%Y-%m-%d %H:%M:%S"),
        "active_strategy": active_strategy,
        "requires_full_kline_cache": requires_full_cache,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
        "kline": {
            **cache,
            "progress_pct": progress_pct,
            "initializing": bool(
                KLINE_PREWARM_ENABLED
                and (cache.get("status") == "running" or KLINE_PREWARM_LOCK.locked())
            ),
        },
        "deployment": {
            "storage": storage,
            "timezone": {
                "ok": timezone_ok,
                "expected": "Asia/Shanghai",
                "utc_offset_seconds": int(offset.total_seconds()) if offset is not None else None,
            },
            "runtime": {
                "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "cpu_count": cpu_count,
                "configured_scan_workers": configured_workers,
                "effective_scan_workers": effective_workers,
            },
        },
    }


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


def market_indices_available(payload: dict[str, Any]) -> bool:
    return bool(payload.get("items")) and not payload.get("error")


def market_sectors_available(payload: dict[str, Any]) -> bool:
    return bool(
        payload.get("sectors")
        or payload.get("items")
        or payload.get("gain_top")
        or payload.get("loss_top")
    ) and not payload.get("error")


def market_hot_stocks_available(payload: dict[str, Any]) -> bool:
    return any(
        bool(payload.get(key))
        for key in ("items", "amount_top", "turnover_top", "volume_top", "gain_top")
    ) and not payload.get("error")


def produce_sectors_data() -> dict[str, Any]:
    return run_dashboard_helper(
        "sectors_dashboard_api.py",
        {
            "sectors": [],
            "items": [],
            "gain_top": [],
            "loss_top": [],
            "industry_gain_top": [],
            "industry_loss_top": [],
            "concept_gain_top": [],
            "concept_loss_top": [],
        },
        timeout=120,
    )


def produce_hot_stocks_data(sort_by: str = "amount") -> dict[str, Any]:
    payload = run_dashboard_helper(
        "hot_stocks_dashboard_api.py",
        {
            "items": [],
            "amount_top": [],
            "turnover_top": [],
            "volume_top": [],
            "gain_top": [],
        },
        timeout=120,
    )
    return apply_hot_stocks_sort(payload, sort_by)


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
        payload = getattr(
            trader, "enrich_portfolio_with_realized_history", trader.enrich_portfolio,
        )(state)
        equity_history = state.get("equity_history", []) or []
        daily_equity_history = state.get("daily_equity_history", []) or []
        history_loader = getattr(trader, "load_account_history", None)
        if callable(history_loader):
            equity_history = history_loader(
                "equity_history",
                equity_history,
                limit=2000,
            )
            daily_equity_history = history_loader(
                "daily_equity_history",
                daily_equity_history,
                limit=500,
            )
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
        payload["trade_markers"] = compact_trade_markers(payload.get("trade_log") or state.get("trade_log") or [])
        payload["trading_calendar"] = dashboard_trading_day_status(now)
        payload["trading_paused"] = state.get("trading_paused", False)
        payload["pause_reason"] = state.get("pause_reason", "")
        payload["pause_since"] = state.get("pause_since", "")
        strategy_performance = (
            (getattr(trader, "build_strategy_performance", trader.track_strategy_performance))(state)
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


def refresh_practice_position_marks_on_startup(trader: Any | None = None) -> bool:
    """Refresh stale quote marks once without running trading decisions."""

    if not PRACTICE_EQUITY_HEARTBEAT_LOCK.acquire(blocking=False):
        return False
    try:
        trader = trader or get_trader_module()
        refresher = getattr(trader, "refresh_position_marks_on_startup", None)
        if refresher is None:
            return False
        refreshed = bool(refresher())
        if refreshed:
            invalidate_api_cache("niuniu_practice", PRACTICE_FAST_CACHE_KEY)
        return refreshed
    except Exception as exc:
        print(f"[WARN] 模拟持仓启动刷新失败: {type(exc).__name__}: {exc}", flush=True)
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
        payload = getattr(
            trader, "enrich_portfolio_with_realized_history", trader.enrich_portfolio,
        )(state)
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
        payload["trade_markers"] = compact_trade_markers(payload.get("trade_log") or state.get("trade_log") or [])
        payload["trade_log"] = filter_today_log_entries(payload.get("trade_log") or [], now=now)
        payload["decision_log"] = filter_today_log_entries(payload.get("decision_log") or [], now=now)
        payload["trading_calendar"] = dashboard_trading_day_status(now)
        payload["trading_paused"] = state.get("trading_paused", False)
        payload["pause_reason"] = state.get("pause_reason", "")
        payload["pause_since"] = state.get("pause_since", "")
        strategy_performance = (getattr(trader, "build_strategy_performance", trader.track_strategy_performance))(state) if hasattr(trader, "track_strategy_performance") else {}
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

def _candidate_rows(payload: dict[str, Any], *keys: str) -> list[Any]:
    """Return the first explicitly supplied candidate list, preserving empties.

    Older caches may not contain ``trade_items`` and still need to fall back to
    their display candidates. A present empty list, however, means the scanner
    intentionally found no trade-ready candidates and must not be widened.
    """
    for key in keys:
        if key in payload:
            value = payload.get(key)
            return value if isinstance(value, list) else []
    return []


def normalize_b1_payload_for_trader(b1_payload: dict[str, Any]) -> dict[str, Any]:
    items = _candidate_rows(b1_payload, "trade_items", "items", "candidates")
    observed_items = _candidate_rows(
        b1_payload,
        "observed_items",
        "items",
        "candidates",
        "trade_items",
    )
    payload = {
        "items": items,
        "observed_items": observed_items,
        "generated_at": b1_payload.get("generated_at", ""),
    }
    if isinstance(b1_payload.get("market_snapshot"), dict):
        payload["market_snapshot"] = b1_payload.get("market_snapshot")
    if isinstance(b1_payload.get("sector_tide_context"), dict):
        payload["sector_tide_context"] = b1_payload.get("sector_tide_context")
    if isinstance(b1_payload.get("niuone_context"), dict):
        payload["niuone_context"] = b1_payload.get("niuone_context")
    if isinstance(b1_payload.get("market_summary"), dict):
        payload["market_summary"] = b1_payload.get("market_summary")
    if isinstance(b1_payload.get("market_decision_context"), dict):
        payload["market_decision_context"] = b1_payload.get("market_decision_context")
    if b1_payload.get("holding_cycle_only") is True:
        payload["holding_cycle_only"] = True
        payload["holding_cycle_codes"] = [
            str(code)
            for code in (b1_payload.get("holding_cycle_codes") or [])
            if str(code)
        ]
    for key in (
        "decision_cycle_kind",
        "holding_cycle_data_status",
        "holding_cycle_error",
    ):
        if b1_payload.get(key):
            payload[key] = b1_payload.get(key)
    for key in ("schedule_slot", "schedule_run_kind", "schedule_triggered_at"):
        if b1_payload.get(key):
            payload[key] = b1_payload.get(key)
    return payload

def run_practice_decision(
    b1_payload: dict[str, Any],
    *,
    blocking: bool = True,
) -> dict[str, Any]:
    # Different schedule slots may finish their scans out of order. Serialize
    # the account read/decision/execute/save transaction so a later slot cannot
    # trade against a portfolio snapshot captured before an earlier fill.
    acquired = PRACTICE_DECISION_LOCK.acquire(blocking=blocking)
    if not acquired:
        return {
            "skipped": True,
            "reason": "practice_decision_busy",
            "decision_cycle_kind": b1_payload.get("decision_cycle_kind") or "",
        }
    try:
        return get_trader_module().run_decision_after_b1(b1_payload)
    finally:
        PRACTICE_DECISION_LOCK.release()


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
            multi_cache_file = _derived_read_model_path(
                MULTI_STRATEGY_CACHE_FILE,
                B1_CACHE_FILE,
            )
            write_json_cache(B1_CACHE_FILE, parsed)
            write_json_cache(multi_cache_file, parsed)
            write_practice_candidates_cache(
                _derived_read_model_path(
                    PRACTICE_CANDIDATES_CACHE_FILE,
                    B1_CACHE_FILE,
                ),
                parsed,
                source_path=multi_cache_file,
            )
            B1_CANDIDATE_REFRESH_LAST_TS = time.time()
            return {"updated": 0, "count": 0}

        import multi_strategy_screen as scanner

        active_scorers = scanner.active_strategy_scorers()
        zettaranc_refresh = bool(
            set(active_scorers).intersection(getattr(scanner, "ZETTARANC_STRATEGY_IDS", ()))
        )
        niuone_refresh = bool(
            set(active_scorers).intersection(getattr(scanner, "NIUONE_STRATEGY_IDS", ()))
        )
        if zettaranc_refresh or niuone_refresh:
            scanner.annotate_candidate_industries(base_items, max_workers=8)
        keys_by_code = {str(item.get("code") or ""): _tencent_key_for_code(str(item.get("code") or "")) for item in base_items}
        quote_map = scanner.tencent_batch_quote(list(keys_by_code.values()))
        if niuone_refresh and isinstance(parsed.get("niuone_context"), dict):
            scoring_context = dict(parsed.get("niuone_context") or {})
        elif isinstance(parsed.get("sector_tide_context"), dict):
            scoring_context = dict(parsed.get("sector_tide_context") or {})
        else:
            scoring_context = {}
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
            best_strategy = str(multi["best_strategy"] or "")
            best = multi["strategies"].get(best_strategy, {})
            niuone_best = best_strategy in scanner.NIUONE_STRATEGY_IDS
            factual_industry = scanner.normalize_industry_name(
                best.get("classification_industry")
                or old.get("industry")
                or old.get("sector")
            )
            signal_theme = (
                scanner.normalize_industry_name(
                    best.get("signal_theme") or best.get("industry")
                )
                if niuone_best
                else ""
            )
            candidate_industry = (
                factual_industry
                if niuone_best
                else scanner.normalize_industry_name(
                    best.get("industry") or factual_industry
                )
            )
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
                "industry": candidate_industry,
                "sector": candidate_industry,
                "signal_theme": signal_theme,
                "theme_memberships": list(
                    best.get("theme_memberships") or []
                ),
                "theme_attributions": list(
                    best.get("theme_attributions") or []
                ),
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
                "theme_attribution_gap": best.get(
                    "theme_attribution_gap"
                ),
                "market_regime": best.get("market_regime"),
                "market_score": best.get("market_score"),
                "market_hard_stop": best.get("market_hard_stop"),
                "market_allows_buys": best.get("market_allows_buys"),
                "sector_status": best.get("sector_status"),
                "sector_score": best.get("sector_score"),
                "theme_basis": best.get("theme_basis"),
                "mainline_state": best.get("mainline_state"),
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
        multi_cache_file = _derived_read_model_path(
            MULTI_STRATEGY_CACHE_FILE,
            B1_CACHE_FILE,
        )
        write_json_cache(B1_CACHE_FILE, output)
        write_json_cache(multi_cache_file, output)
        write_practice_candidates_cache(
            _derived_read_model_path(
                PRACTICE_CANDIDATES_CACHE_FILE,
                B1_CACHE_FILE,
            ),
            output,
            source_path=multi_cache_file,
        )
        with API_RESPONSE_LOCK:
            API_RESPONSE_CACHE.pop(PRACTICE_CANDIDATES_CACHE_KEY, None)
            API_RESPONSE_CACHE.pop(TODAY_CANDIDATES_CACHE_KEY, None)
            API_RESPONSE_CACHE.pop(TODAY_CANDIDATE_INTRADAY_CACHE_KEY, None)
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


HOLDING_CYCLE_STATUS_LABELS = {
    "ready": "持仓数据正常",
    "partial": "部分持仓数据不可用",
    "scoring_unavailable": "持仓评分不可用",
    "scoring_error": "持仓评分发生错误",
    "scorer_config_unavailable": "策略评分配置不可用",
    "no_active_scorers": "没有启用的策略评分器",
    "quote_unavailable": "持仓行情不可用",
    "stale_or_missing_quotes": "持仓行情缺失或已过期",
    "history_unavailable": "持仓日K数据不可用",
    "empty_portfolio": "当前没有持仓",
}


def holding_cycle_start_messages(
    payload: dict[str, Any],
    *,
    observed_count: int,
    item_count: int,
) -> tuple[str, str]:
    holding_codes = {
        str(code).strip()
        for code in (payload.get("holding_cycle_codes") or [])
        if str(code).strip()
    }
    holding_count = max(len(holding_codes), observed_count, item_count)
    status = str(payload.get("holding_cycle_data_status") or "ready").strip()
    status_label = HOLDING_CYCLE_STATUS_LABELS.get(status, "持仓评分状态未知")
    if status == "ready":
        scoring_note = f"成功重评{observed_count}只"
    else:
        scoring_note = f"{status_label}（成功重评{observed_count}只）"
    candidate_note = (
        f"其中{item_count}只达到加仓候选条件"
        if item_count
        else "未生成加仓候选"
    )
    return (
        f"持仓快周期开始：当前持仓{holding_count}只，{scoring_note}，{candidate_note}；"
        "开始执行已有持仓的原策略退出规则和模型持仓复核。",
        f"持仓快周期开始：当前持仓{holding_count}只，成功重评{observed_count}只，"
        f"加仓候选{item_count}只，开始退出检查和模型持仓复核；{status_label}",
    )


def run_practice_decision_logged(
    b1_payload: dict[str, Any],
    *,
    record_start: bool = False,
    refresh_market_summary: bool = True,
    decision_blocking: bool = True,
) -> dict[str, Any]:
    payload = normalize_b1_payload_for_trader(b1_payload)
    try:
        trader = get_trader_module()
        summary = payload.get("market_summary") if isinstance(payload.get("market_summary"), dict) else {}
        if refresh_market_summary:
            summary_trigger = "scheduled" if payload.get("schedule_slot") else "manual_cycle"
            summary = refresh_practice_market_summary_for_decision(summary_trigger)
        if isinstance(summary, dict):
            payload["market_summary"] = summary
        if summary.get("available") and hasattr(trader, "market_strategy_context_from_summary"):
            refreshed_ctx = trader.market_strategy_context_from_summary(summary, current_cn_datetime())
            payload["market_decision_context"] = trader.compact_market_strategy_context(refreshed_ctx)
            with API_RESPONSE_LOCK:
                API_RESPONSE_CACHE.pop("niuniu_practice", None)
                API_RESPONSE_CACHE.pop(PRACTICE_FAST_CACHE_KEY, None)
    except Exception as exc:
        print(f"[WARN] 此刻盘面总结与评价刷新失败: {type(exc).__name__}: {exc}", flush=True)
    item_count = len(payload.get("items") or [])
    observed_count = len(payload.get("observed_items") or [])
    slot_note = ""
    if payload.get("schedule_slot"):
        kind_label = "补跑" if payload.get("schedule_run_kind") == "catchup" else "定时"
        slot_note = f"（计划{str(payload.get('schedule_slot'))[-5:]}{kind_label}）"
    holding_cycle = (
        payload.get("holding_cycle_only") is True
        or payload.get("decision_cycle_kind") == HOLDING_CYCLE_KIND
        or payload.get("schedule_run_kind") == HOLDING_CYCLE_KIND
    )
    if holding_cycle and (record_start or not item_count):
        holding_summary, holding_reason = holding_cycle_start_messages(
            payload,
            observed_count=observed_count,
            item_count=item_count,
        )
        record_practice_decision_event(
            payload,
            holding_summary,
            holding_reason,
        )
    elif not item_count:
        record_practice_decision_event(
            payload,
            f"选股完成{slot_note}：候选池{observed_count}只，其中0只进入买卖决策，"
            "继续检查已有持仓的原策略退出规则。",
            f"选股完成{slot_note}：候选池{observed_count}只，0只进入买卖决策，开始持仓退出检查",
        )
    elif record_start:
        record_practice_decision_event(
            payload,
            f"选股完成{slot_note}：候选池{observed_count}只，其中{item_count}只进入买卖决策，"
            "开始生成买卖决策。",
            f"选股后买卖决策开始{slot_note}：候选池{observed_count}只，决策池{item_count}只",
        )
    try:
        return run_practice_decision(payload, blocking=decision_blocking)
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


def _derived_read_model_path(configured: Path, reference: Path) -> Path:
    """Keep compatibility tests that relocate legacy cache globals isolated."""
    if configured.parent == CRON_OUTPUT_DIR and reference.parent != CRON_OUTPUT_DIR:
        return reference.parent / configured.name
    return configured


def _file_mtime_ns(path: Path) -> int:
    try:
        return int(path.stat().st_mtime_ns)
    except OSError:
        return 0


def _read_model_is_current(
    snapshot_path: Path,
    snapshot: Mapping[str, Any],
    source_paths: tuple[Path, ...],
) -> bool:
    snapshot_mtime = _file_mtime_ns(snapshot_path)
    if not snapshot_mtime:
        return False
    source_name = str(snapshot.get("source_cache") or "").strip()
    if source_name:
        source = next((path for path in source_paths if path.name == source_name), None)
        if source is None or not source.exists():
            return False
        version = snapshot.get("source_version")
        if isinstance(version, Mapping):
            try:
                stat = source.stat()
                current_version = {
                    "device": int(stat.st_dev),
                    "inode": int(stat.st_ino),
                    "size": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                }
            except OSError:
                return False
            if current_version != dict(version):
                return False
    return snapshot_mtime >= max((_file_mtime_ns(path) for path in source_paths), default=0)

def load_practice_candidates_cache() -> dict[str, Any]:
    strategy_suite = active_strategy_suite(
        os.environ.get(ACTIVE_STRATEGY_ENV),
        os.environ.get(STRATEGY_SOURCE_ENV),
        os.environ.get(PERSONA_STRATEGY_ENV),
    )
    active_strategy_ids = enabled_strategy_ids(
        os.environ.get(PERSONA_STRATEGY_ENV),
        os.environ.get(STRATEGY_SOURCE_ENV),
        os.environ.get(ACTIVE_STRATEGY_ENV),
    )
    strategy_meta = enabled_strategy_meta(
        os.environ.get(PERSONA_STRATEGY_ENV),
        os.environ.get(STRATEGY_SOURCE_ENV),
        os.environ.get(ACTIVE_STRATEGY_ENV),
    )
    suite_labels = {
        str(item.get("id") or ""): str(item.get("label") or item.get("id") or "")
        for item in strategy_suite_options()
    }
    errors: list[str] = []
    stale_cache_found = False
    multi_cache_file = _derived_read_model_path(
        MULTI_STRATEGY_CACHE_FILE,
        B1_CACHE_FILE,
    )
    compact_cache_file = _derived_read_model_path(
        PRACTICE_CANDIDATES_CACHE_FILE,
        multi_cache_file,
    )
    source_cache_files = (multi_cache_file, B1_CACHE_FILE)
    compact = read_versioned_json_cache(compact_cache_file)
    cache_files: list[tuple[Path, dict[str, Any] | None, bool]] = []
    if isinstance(compact, dict) and _read_model_is_current(
        compact_cache_file,
        compact,
        source_cache_files,
    ):
        cache_files.append((compact_cache_file, compact, True))
    cache_files.extend((path, None, False) for path in source_cache_files)
    for cache_file, cached_payload, is_compact in cache_files:
        try:
            if cached_payload is None and not cache_file.exists():
                continue
            parsed = cached_payload or read_json_cache(cache_file, None)
            if not isinstance(parsed, dict):
                raise ValueError(f"候选缓存格式无效：{cache_file}")
            items = parsed.get("items") or parsed.get("candidates") or []
            cached_ids: set[str] = set()
            explicit_ids = parsed.get("enabled_strategy_ids")
            if isinstance(explicit_ids, list):
                cached_ids.update(str(value) for value in explicit_ids if str(value or "").strip())
            cached_meta = parsed.get("strategy_meta")
            if not cached_ids and isinstance(cached_meta, dict):
                cached_ids.update(str(value) for value in cached_meta if str(value or "").strip())
            for field in ("items", "candidates", "trade_items"):
                rows = parsed.get(field)
                for item in rows if isinstance(rows, list) else []:
                    if not isinstance(item, dict):
                        continue
                    strategy_id = str(item.get("best_strategy") or item.get("strategy") or "").strip()
                    if strategy_id:
                        cached_ids.add(strategy_id)
            cached_suite = str(parsed.get("strategy_suite") or "").strip()
            if (
                (cached_suite and cached_suite != strategy_suite)
                or (cached_ids and not cached_ids.issubset(active_strategy_ids))
            ):
                stale_cache_found = True
                continue
            def active_rows(value: Any) -> list[dict[str, Any]]:
                if not isinstance(value, list):
                    return []
                return [
                    item
                    for item in value
                    if isinstance(item, dict)
                    and (
                        not str(item.get("best_strategy") or item.get("strategy") or "").strip()
                        or str(item.get("best_strategy") or item.get("strategy") or "").strip()
                        in active_strategy_ids
                    )
                ]

            display_items = sort_candidates_by_score(active_rows(items))
            candidates = sort_candidates_by_score(
                active_rows(_candidate_rows(parsed, "candidates", "items"))
            )
            trade_items = sort_candidates_by_score(
                active_rows(_candidate_rows(parsed, "trade_items", "items", "candidates"))
            )
            if not is_compact:
                parsed = build_practice_candidates_cache_payload(
                    parsed,
                    source_cache_name=cache_file.name,
                )
                try:
                    write_practice_candidates_cache(
                        compact_cache_file,
                        parsed,
                        source_path=cache_file,
                    )
                except OSError:
                    pass
            return {
                **parsed,
                "generated_at": parsed.get("generated_at", ""),
                "count": len(display_items),
                "items": display_items,
                "candidates": candidates,
                "trade_items": trade_items,
                "trade_count": len(trade_items),
                "strategy_suite": strategy_suite,
                "enabled_strategy_ids": sorted(active_strategy_ids),
                "strategy_meta": strategy_meta,
                "strategy_cache_stale": False,
                "refresh_required": False,
            }
        except (OSError, ValueError) as exc:
            errors.append(f"{cache_file.name}: {exc}")
    base = {
        "items": [],
        "candidates": [],
        "trade_items": [],
        "count": 0,
        "trade_count": 0,
        "generated_at": "",
        "strategy_suite": strategy_suite,
        "enabled_strategy_ids": sorted(active_strategy_ids),
        "strategy_meta": strategy_meta,
        "strategy_distribution": {},
        "strategy_cache_stale": stale_cache_found,
        "refresh_required": stale_cache_found,
    }
    if stale_cache_found:
        label = suite_labels.get(strategy_suite, strategy_suite)
        base["status_message"] = f"已切换为{label}，正在等待按当前策略重新扫描候选股"
    if errors:
        base["error"] = "; ".join(errors)
    return base


def _today_candidate_source_files(current_date: str) -> list[tuple[str, Path]]:
    history_dir = CRON_OUTPUT_DIR / "multi_strategy_history" / current_date
    history_files: list[Path] = []
    if history_dir.is_dir() and not history_dir.is_symlink():
        for path in history_dir.iterdir():
            if path.is_symlink() or not path.is_file() or path.suffix != ".json":
                continue
            try:
                timestamp = datetime.strptime(path.stem, "%Y-%m-%d_%H-%M-%S")
            except ValueError:
                continue
            if timestamp.strftime("%Y-%m-%d") == current_date:
                history_files.append(path)
    history_files.sort(key=lambda path: path.name)

    sources = [(f"history:{path.name}", path) for path in history_files[-12:]]
    compact_path = _derived_read_model_path(
        PRACTICE_CANDIDATES_CACHE_FILE,
        _derived_read_model_path(MULTI_STRATEGY_CACHE_FILE, B1_CACHE_FILE),
    )
    if compact_path.is_file() and not compact_path.is_symlink():
        sources.append(("latest", compact_path))
    return sources


def _today_candidate_source_versions(
    sources: Iterable[tuple[str, Path]],
) -> list[dict[str, Any]]:
    versions: list[dict[str, Any]] = []
    for source_id, path in sources:
        try:
            stat = path.stat()
        except OSError:
            continue
        versions.append({
            "source": source_id,
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        })
    return versions


def load_today_candidates_cache() -> dict[str, Any]:
    """Return stocks that reached the trade-ready pool in any scan today."""
    current_date = current_cn_date_key()
    summary_path = CRON_OUTPUT_DIR / TODAY_CANDIDATES_CACHE_FILE.name
    with TODAY_CANDIDATES_LOCK:
        sources = _today_candidate_source_files(current_date)
        source_versions = _today_candidate_source_versions(sources)
        cached = read_versioned_json_cache(summary_path)
        if (
            isinstance(cached, dict)
            and cached.get("schema_version") == TODAY_CANDIDATES_SCHEMA_VERSION
            and cached.get("current_date") == current_date
            and cached.get("source_versions") == source_versions
        ):
            return {
                key: value
                for key, value in cached.items()
                if key != "source_versions"
            }

        scans: list[dict[str, Any]] = []
        for _source_id, path in sources:
            parsed = read_json_cache(path, None)
            if isinstance(parsed, dict):
                scans.append(parsed)
        payload = build_today_candidates_payload(
            scans,
            current_date=current_date,
        )
        try:
            write_json_cache(
                summary_path,
                {**payload, "source_versions": source_versions},
            )
        except OSError:
            pass
        return payload


def load_today_candidate_intraday() -> dict[str, Any]:
    """Return bounded minute lines for the stocks in today's candidate read model."""
    candidates = load_today_candidates_cache().get("items")
    if not isinstance(candidates, list):
        candidates = []
    generated_at = current_cn_datetime().strftime("%Y-%m-%d %H:%M:%S")
    if not candidates:
        return build_today_candidate_intraday_payload(
            [],
            fetcher=lambda _code, _previous_close: {},
            generated_at=generated_at,
        )
    try:
        fetcher = get_trader_module().fetch_intraday_minutes
    except Exception:
        def fetcher(_code: str, _previous_close: float | None) -> dict[str, Any]:
            raise RuntimeError("intraday provider unavailable")
    return build_today_candidate_intraday_payload(
        candidates,
        fetcher=fetcher,
        generated_at=generated_at,
    )


def load_niuone_mainline_cache_payload() -> dict[str, Any]:
    """Load operational NiuOne state, using legacy full scans only as fallback."""
    payloads: list[dict[str, Any]] = []
    for cache_file in (
        NIUONE_MAINLINE_MINUTE_CACHE_FILE,
        NIUONE_MAINLINE_CACHE_FILE,
    ):
        parsed = read_json_cache(cache_file, None)
        if isinstance(parsed, dict) and isinstance(parsed.get("niuone_context"), dict):
            payloads.append(parsed)
    if payloads:
        return max(payloads, key=lambda payload: str(payload.get("generated_at") or ""))
    for cache_file in (MULTI_STRATEGY_CACHE_FILE, B1_CACHE_FILE):
        parsed = read_json_cache(cache_file, None)
        if isinstance(parsed, dict) and isinstance(parsed.get("niuone_context"), dict):
            payloads.append(parsed)
    return (
        max(payloads, key=lambda payload: str(payload.get("generated_at") or ""))
        if payloads
        else {}
    )


def load_niuone_mainline_summary_payload() -> dict[str, Any]:
    """Load the bounded Dashboard theme model and lazily migrate old caches."""
    summary_path = _derived_read_model_path(
        NIUONE_MAINLINE_SUMMARY_CACHE_FILE,
        NIUONE_MAINLINE_CACHE_FILE,
    )
    summary = read_versioned_json_cache(summary_path)
    operational_paths = (
        NIUONE_MAINLINE_MINUTE_CACHE_FILE,
        NIUONE_MAINLINE_CACHE_FILE,
    )
    if (
        isinstance(summary, dict)
        and isinstance(summary.get("niuone_context"), dict)
        and _file_mtime_ns(summary_path)
        >= max((_file_mtime_ns(path) for path in operational_paths), default=0)
    ):
        return summary
    operational = load_niuone_mainline_cache_payload()
    if not operational:
        return summary if isinstance(summary, dict) else {}
    compact = build_niuone_mainline_summary_cache_payload(operational)
    try:
        write_niuone_mainline_summary_cache(summary_path, operational)
    except OSError:
        pass
    return compact


def load_niuone_mainline_view() -> dict[str, Any]:
    return build_niuone_mainline_view(load_niuone_mainline_summary_payload())


def get_niuone_mainline_minute_engine() -> NiuOneMinuteEngine:
    """Return one in-process engine for the active private cache paths."""

    global NIUONE_MAINLINE_MINUTE_ENGINE, NIUONE_MAINLINE_MINUTE_ENGINE_PATHS
    resolved_paths = (str(kline_cache_path()), str(EASTMONEY_BOARD_CACHE_FILE))
    with NIUONE_MAINLINE_MINUTE_STATE_LOCK:
        if (
            NIUONE_MAINLINE_MINUTE_ENGINE is None
            or NIUONE_MAINLINE_MINUTE_ENGINE_PATHS != resolved_paths
        ):
            NIUONE_MAINLINE_MINUTE_ENGINE = NiuOneMinuteEngine(
                kline_cache_path=Path(resolved_paths[0]),
                industry_cache_path=Path(resolved_paths[1]),
            )
            NIUONE_MAINLINE_MINUTE_ENGINE_PATHS = resolved_paths
        return NIUONE_MAINLINE_MINUTE_ENGINE


def run_niuone_mainline_minute_refresh(
    quote_snapshot: Mapping[str, Any],
    *,
    engine: NiuOneMinuteEngine | None = None,
) -> dict[str, Any]:
    """Recalculate the theme cache from fresh quotes and local slow inputs."""

    quote_generated_at = str(quote_snapshot.get("generated_at") or "")[:19]
    existing = read_json_cache(NIUONE_MAINLINE_MINUTE_CACHE_FILE, None) or {}
    if (
        quote_generated_at
        and str(existing.get("quote_generated_at") or "")[:19] >= quote_generated_at
    ):
        return {"skipped": True, "reason": "quote_already_processed"}
    previous_payload = load_niuone_mainline_cache_payload()
    flow_rows = read_json_cache(MONEY_FLOW_SNAPSHOT_FILE, None) or {}
    if str(flow_rows.get("generated_at") or "")[:10] != quote_generated_at[:10]:
        flow_rows = {}
    resolved_now = current_cn_datetime()
    scan = (engine or get_niuone_mainline_minute_engine()).build_scan(
        quote_snapshot,
        previous_payload=previous_payload,
        flow_rows=flow_rows,
        now=resolved_now,
    )
    try:
        scan["eastmoney_concept_signal"] = (
            load_eastmoney_concept_board_signal().to_dict()
        )
    except Exception:
        scan["eastmoney_concept_signal"] = {
            "schema_version": EASTMONEY_CONCEPT_BOARD_SCHEMA_VERSION,
            "source": EASTMONEY_CONCEPT_BOARD_SOURCE,
            "captured_at": resolved_now.strftime("%Y-%m-%d %H:%M:%S"),
            "available": False,
            "status": "upstream_unavailable",
            "boards": [],
        }
    payload = write_niuone_mainline_cache(NIUONE_MAINLINE_MINUTE_CACHE_FILE, scan)
    write_niuone_mainline_summary_cache(
        _derived_read_model_path(
            NIUONE_MAINLINE_SUMMARY_CACHE_FILE,
            NIUONE_MAINLINE_CACHE_FILE,
        ),
        scan,
    )
    invalidate_api_cache(NIUONE_MAINLINE_CACHE_KEY)
    print(
        "[Theme strength] minute quotes updated "
        f"quote={payload.get('quote_generated_at') or 'unknown'} "
        f"duration_ms={payload.get('calculation_duration_ms') or 0}",
        flush=True,
    )
    return {
        "updated": True,
        "quote_generated_at": payload.get("quote_generated_at") or "",
        "generated_at": payload.get("generated_at") or "",
        "calculation_duration_ms": max(
            0, int(payload.get("calculation_duration_ms") or 0)
        ),
    }


def niuone_mainline_minute_cooldown_seconds(
    calculation_duration_ms: object,
    *,
    sample_interval_seconds: float | None = None,
) -> float:
    """Keep expensive theme refreshes within a bounded single-core duty cycle."""

    try:
        duration_seconds = max(0.0, float(calculation_duration_ms) / 1000.0)
    except (TypeError, ValueError):
        duration_seconds = 0.0
    interval_seconds = max(
        1.0,
        float(
            MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS
            if sample_interval_seconds is None
            else sample_interval_seconds
        ),
    )
    cadence_wait = max(0.0, interval_seconds - duration_seconds)
    guarded_wait = duration_seconds * (
        (1.0 / NIUONE_MAINLINE_MINUTE_MAX_CPU_SHARE) - 1.0
    )
    return min(
        NIUONE_MAINLINE_MINUTE_MAX_COOLDOWN_SECONDS,
        max(cadence_wait, guarded_wait),
    )


def run_niuone_mainline_minute_refresh_isolated(
    quote_snapshot: Mapping[str, Any],
    *,
    timeout_seconds: float = NIUONE_MAINLINE_MINUTE_PROCESS_TIMEOUT_SECONDS,
) -> bool:
    """Run the CPU-heavy refresh in a bounded interpreter process."""

    request_body = json.dumps(
        dict(quote_snapshot),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    process = subprocess.Popen(
        [sys.executable, "-B", str(ENTRYPOINT_DIR / "niuone_minute_refresh.py")],
        stdin=subprocess.PIPE,
    )
    try:
        process.communicate(
            input=request_body,
            timeout=max(1.0, float(timeout_seconds)),
        )
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        print(
            "[WARN] Minute theme-strength refresh retained previous cache: "
            "isolated compute timed out",
            file=sys.stderr,
            flush=True,
        )
        return False
    return process.returncode == 0


def niuone_mainline_heavy_scan_in_progress() -> bool:
    """Prefer complete research and trading scans over the derived minute view."""

    return (
        B1_FULL_SCAN_LOCK.locked()
        or NIUONE_MAINLINE_SCAN_LOCK.locked()
        or KLINE_PREWARM_LOCK.locked()
    )


def _niuone_mainline_minute_worker() -> None:
    global NIUONE_MAINLINE_MINUTE_NEXT_ALLOWED_MONOTONIC
    global NIUONE_MAINLINE_MINUTE_PENDING, NIUONE_MAINLINE_MINUTE_THREAD
    while True:
        with NIUONE_MAINLINE_MINUTE_STATE_LOCK:
            snapshot = NIUONE_MAINLINE_MINUTE_PENDING
            if snapshot is None:
                NIUONE_MAINLINE_MINUTE_THREAD = None
                return
            wait_seconds = max(
                0.0,
                NIUONE_MAINLINE_MINUTE_NEXT_ALLOWED_MONOTONIC - time.monotonic(),
            )
            if wait_seconds <= 0 and niuone_mainline_heavy_scan_in_progress():
                wait_seconds = NIUONE_MAINLINE_MINUTE_BUSY_RETRY_SECONDS
            if wait_seconds <= 0:
                NIUONE_MAINLINE_MINUTE_PENDING = None
        if wait_seconds > 0:
            time.sleep(wait_seconds)
            continue
        started = time.monotonic()
        try:
            updated = run_niuone_mainline_minute_refresh_isolated(snapshot)
        except Exception as exc:
            updated = False
            print(
                f"[WARN] Minute theme-strength refresh retained previous cache: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        cooldown_seconds = niuone_mainline_minute_cooldown_seconds(
            elapsed_ms
        )
        if updated:
            invalidate_api_cache(NIUONE_MAINLINE_CACHE_KEY)
        with NIUONE_MAINLINE_MINUTE_STATE_LOCK:
            NIUONE_MAINLINE_MINUTE_NEXT_ALLOWED_MONOTONIC = (
                time.monotonic() + cooldown_seconds
            )


def accept_niuone_mainline_quote_snapshot(quote_snapshot: dict[str, Any]) -> bool:
    """Coalesce fresh quote snapshots into one non-overlapping minute worker."""

    global NIUONE_MAINLINE_MINUTE_PENDING, NIUONE_MAINLINE_MINUTE_THREAD
    if not NIUONE_MAINLINE_MINUTE_REFRESH_ENABLED:
        return False
    with NIUONE_MAINLINE_MINUTE_STATE_LOCK:
        NIUONE_MAINLINE_MINUTE_PENDING = dict(quote_snapshot)
        if NIUONE_MAINLINE_MINUTE_THREAD and NIUONE_MAINLINE_MINUTE_THREAD.is_alive():
            return False
        thread = threading.Thread(
            target=_niuone_mainline_minute_worker,
            name="niuone-mainline-minute",
            daemon=True,
        )
        NIUONE_MAINLINE_MINUTE_THREAD = thread
        thread.start()
        return True


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


def b1_scan_stage_error_code(stage: str, *, timed_out: bool = False) -> str:
    """Map scanner progress stages to stable, actionable public error codes."""
    suffix = "timeout" if timed_out else "failed"
    prefix = {
        "code_pool": "code_pool",
        "quotes": "quote_source",
        "cache_check": "kline_cache",
        "industry_context": "industry_context",
        "kline_prepare": "kline_prepare",
        "scoring": "strategy_scoring",
        "news_precheck": "news_precheck",
        "persisting": "candidate_persist",
    }.get(str(stage or ""), "scan_aggregate" if timed_out else "candidate_scan")
    return f"{prefix}_{suffix}"


def niuone_mainline_cache_generated_for_slot(slot_key: str) -> bool:
    """Return whether the independent cache already covers a schedule slot."""
    if not slot_key:
        return False
    try:
        payload = json.loads(NIUONE_MAINLINE_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    generated_at = str(payload.get("generated_at") or "")[:16]
    return generated_at[:10] == slot_key[:10] and generated_at >= slot_key[:16]


def run_independent_niuone_mainline_scan(
    schedule_slot: str = "",
    *,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Refresh only the full-market theme cache without touching trade caches."""
    if schedule_slot and niuone_mainline_cache_generated_for_slot(schedule_slot):
        return {"skipped": True, "reason": "slot_already_generated"}
    if not NIUONE_MAINLINE_SCAN_LOCK.acquire(blocking=False):
        return {"skipped": True, "reason": "scan_in_progress"}
    process_lease = FileLease(
        CRON_STATE_DIR / "niuone_mainline_scan.lock",
        stale_after_seconds=B1_SCAN_TIMEOUT_SECONDS + 120,
    )
    if not process_lease.acquire():
        NIUONE_MAINLINE_SCAN_LOCK.release()
        return {"skipped": True, "reason": "scan_in_progress_other_process"}
    try:
        script = Path(
            os.environ.get("DASHBOARD_B1_SCANNER", ENTRYPOINT_DIR / "multi_strategy_screen.py")
        ).expanduser()
        if not script.exists():
            return {"error": f"扫描脚本不存在：{script}"}
        active_runner = runner or subprocess.run
        result = active_runner(
            [sys.executable, str(script), "--json", "--niuone-mainline-only"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=B1_SCAN_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            error = summarize_b1_scan_failure(str(result.stderr or ""), "")
            print(f"[WARN] Independent theme-strength scan failed: {error}", file=sys.stderr, flush=True)
            return {"error": error}
        if runner is None and not NIUONE_MAINLINE_CACHE_FILE.exists():
            return {"error": "独立题材扫描完成但未生成缓存"}
        invalidate_api_cache(NIUONE_MAINLINE_CACHE_KEY)
        print(
            f"[Theme strength] independent scan updated for {schedule_slot or 'manual'}",
            flush=True,
        )
        return {"updated": True, "schedule_slot": schedule_slot}
    except subprocess.TimeoutExpired:
        error = f"独立题材扫描超时（{B1_SCAN_TIMEOUT_SECONDS}s）"
        print(f"[WARN] {error}", file=sys.stderr, flush=True)
        return {"error": error}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        print(f"[WARN] Independent theme-strength scan error: {error}", file=sys.stderr, flush=True)
        return {"error": error}
    finally:
        process_lease.release()
        NIUONE_MAINLINE_SCAN_LOCK.release()


def start_independent_niuone_mainline_scan(schedule_slot: str = "") -> bool:
    """Start the research scan in the background when no equivalent run exists."""
    global NIUONE_MAINLINE_SCAN_THREAD
    if schedule_slot and niuone_mainline_cache_generated_for_slot(schedule_slot):
        return False
    if NIUONE_MAINLINE_SCAN_LOCK.locked():
        return False
    thread = threading.Thread(
        target=run_independent_niuone_mainline_scan,
        args=(schedule_slot,),
        name="niuone-mainline-scan",
        daemon=True,
    )
    NIUONE_MAINLINE_SCAN_THREAD = thread
    thread.start()
    return True


def kline_prewarm_due(now: datetime | None = None) -> bool:
    """Return whether today's bounded pre-market cache refresh should start."""
    if not KLINE_PREWARM_ENABLED:
        return False
    current = now or datetime.now()
    if not is_a_share_trading_day_for_dashboard(current):
        return False
    scheduled = _b1_schedule_slot_datetime(current, KLINE_PREWARM_TIME)
    if scheduled is None:
        return False
    age_seconds = (current - scheduled).total_seconds()
    if age_seconds < 0 or age_seconds > max(0, KLINE_PREWARM_CATCHUP_MINUTES) * 60:
        return False
    if prewarm_completed_for_date(current.strftime("%Y-%m-%d"), path=kline_cache_path()):
        return False
    if KLINE_PREWARM_LOCK.locked():
        return False
    return time.time() - KLINE_PREWARM_LAST_ATTEMPT_TS >= max(30, KLINE_PREWARM_RETRY_SECONDS)


def kline_bootstrap_due(now: datetime | None = None) -> bool:
    """Return whether a cold or incomplete deployment should prewarm immediately."""
    if not KLINE_PREWARM_ENABLED or not KLINE_BOOTSTRAP_ENABLED:
        return False
    current = now or current_cn_datetime()
    run_date = current.strftime("%Y-%m-%d")
    if KLINE_PREWARM_LOCK.locked():
        return False
    if KLINE_PREWARM_ATTEMPTS_BY_DATE.get(run_date, 0) >= KLINE_BOOTSTRAP_MAX_ATTEMPTS:
        return False
    readiness = market_data_readiness(current)
    if "runtime_storage_not_writable" in (readiness.get("blockers") or []):
        return False
    if readiness.get("data_ready"):
        return False
    return time.time() - KLINE_PREWARM_LAST_ATTEMPT_TS >= max(30, KLINE_PREWARM_RETRY_SECONDS)


def record_kline_prewarm_failure(target_date: str, error_code: str) -> None:
    """Best-effort terminal diagnostics must not replace the original failure."""
    try:
        mark_prewarm_run_failed(
            str(target_date)[:10],
            error_code,
            path=kline_cache_path(),
        )
    except (OSError, sqlite3.Error, ValueError):
        print(
            "[WARN] Unable to persist K-line prewarm failure status",
            file=sys.stderr,
            flush=True,
        )


def run_kline_prewarm(
    target_date: str = "",
    *,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Run the full-market prewarm subprocess without touching trading caches."""
    global KLINE_PREWARM_LAST_ATTEMPT_TS
    if not KLINE_PREWARM_LOCK.acquire(blocking=False):
        return {"skipped": True, "reason": "prewarm_in_progress"}
    process_lease = FileLease(
        CRON_STATE_DIR / "kline_prewarm.lock",
        stale_after_seconds=KLINE_PREWARM_TIMEOUT_SECONDS + 120,
    )
    if not process_lease.acquire():
        KLINE_PREWARM_LOCK.release()
        return {"skipped": True, "reason": "prewarm_in_progress_other_process"}
    KLINE_PREWARM_LAST_ATTEMPT_TS = time.time()
    try:
        run_date = str(target_date or datetime.now().strftime("%Y-%m-%d"))[:10]
        if prewarm_completed_for_date(run_date, path=kline_cache_path()):
            return {"skipped": True, "reason": "already_completed", "target_date": run_date}
        script = Path(
            os.environ.get("DASHBOARD_B1_SCANNER", ENTRYPOINT_DIR / "multi_strategy_screen.py")
        ).expanduser()
        if not script.exists():
            return {"error": f"扫描脚本不存在：{script}"}
        active_runner = runner or subprocess.run
        child_env = os.environ.copy()
        child_env["DASHBOARD_KLINE_PREWARM_TARGET_DATE"] = run_date
        previous = kline_cache_readiness(
            accepted_last_dates=accepted_kline_dates_for_dashboard(),
            path=kline_cache_path(),
            minimum_coverage=KLINE_READINESS_MIN_COVERAGE,
        )
        if (
            str(previous.get("target_date") or "") == run_date
            and str(previous.get("status") or "") in {"running", "error"}
        ):
            child_env["DASHBOARD_KLINE_PREWARM_RESUME"] = "1"
        result = active_runner(
            [sys.executable, str(script), "--json", "--prewarm-kline-cache"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=KLINE_PREWARM_TIMEOUT_SECONDS,
            env=child_env,
        )
        if result.returncode != 0:
            error = summarize_b1_scan_failure(str(result.stderr or ""), "")
            record_kline_prewarm_failure(run_date, "prewarm_process_failed")
            print(f"[WARN] Pre-market K-line prewarm failed: {error}", file=sys.stderr, flush=True)
            return {"error": error, "target_date": run_date}
        if runner is None and not prewarm_completed_for_date(run_date, path=kline_cache_path()):
            return {"error": "盘前日K预热完成但有效覆盖率不足", "target_date": run_date}
        print(f"[K-line cache] pre-market refresh completed for {run_date}", flush=True)
        return {"updated": True, "target_date": run_date}
    except subprocess.TimeoutExpired:
        error = f"盘前日K预热超时（{KLINE_PREWARM_TIMEOUT_SECONDS}s）"
        record_kline_prewarm_failure(
            str(target_date or datetime.now().strftime("%Y-%m-%d"))[:10],
            "aggregate_timeout",
        )
        print(f"[WARN] {error}", file=sys.stderr, flush=True)
        return {"error": error, "target_date": str(target_date or "")[:10]}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        record_kline_prewarm_failure(
            str(target_date or datetime.now().strftime("%Y-%m-%d"))[:10],
            "prewarm_internal_error",
        )
        print(f"[WARN] Pre-market K-line prewarm error: {error}", file=sys.stderr, flush=True)
        return {"error": error, "target_date": str(target_date or "")[:10]}
    finally:
        process_lease.release()
        KLINE_PREWARM_LOCK.release()


def start_kline_prewarm(
    target_date: str = "",
    *,
    reason: str = "scheduled",
    force: bool = False,
) -> bool:
    """Start one pre-market cache refresh in the background."""
    global KLINE_PREWARM_RUN_THREAD
    if KLINE_PREWARM_LOCK.locked():
        return False
    run_date = str(target_date or current_cn_date_key())[:10]
    if (
        not force
        and reason == "bootstrap"
        and KLINE_PREWARM_ATTEMPTS_BY_DATE.get(run_date, 0) >= KLINE_BOOTSTRAP_MAX_ATTEMPTS
    ):
        return False
    KLINE_PREWARM_ATTEMPTS_BY_DATE[run_date] = (
        KLINE_PREWARM_ATTEMPTS_BY_DATE.get(run_date, 0) + 1
    )
    thread = threading.Thread(
        target=run_kline_prewarm,
        args=(run_date,),
        name=f"kline-cache-prewarm-{reason}",
        daemon=True,
    )
    KLINE_PREWARM_RUN_THREAD = thread
    thread.start()
    return True


def kline_prewarm_schedule_loop() -> None:
    while True:
        current = current_cn_datetime()
        if kline_bootstrap_due(current):
            start_kline_prewarm(
                current.strftime("%Y-%m-%d"),
                reason="bootstrap",
            )
        elif kline_prewarm_due(current):
            start_kline_prewarm(
                current.strftime("%Y-%m-%d"),
                reason="scheduled",
            )
        time.sleep(15)


def _trigger_b1_scan_unlocked(
    force: bool = False,
    decision_mode: str = "async",
    *,
    schedule_slot: str = "",
    schedule_run_kind: str = "",
    job_id: str = "",
    require_ready_cache: bool = True,
) -> dict[str, Any]:
    import subprocess, sys
    script = Path(os.environ.get("DASHBOARD_B1_SCANNER", ENTRYPOINT_DIR / "multi_strategy_screen.py")).expanduser()
    if not script.exists():
        return {"error": f"扫描脚本不存在：{script}", "items": [], "count": 0, "generated_at": "", "running": False}
    try:
        args = [sys.executable, str(script), "--json"] + (["--force"] if force else [])
        resolved_job_id = str(job_id or f"scan-{int(time.time())}-{os.getpid()}")[:120]
        progress_file = Path(
            os.environ.get("DASHBOARD_B1_PROGRESS_FILE")
            or CRON_STATE_DIR / "b1_scan_progress.json"
        ).expanduser()
        write_json_cache(progress_file, {
            "job_id": resolved_job_id,
            "stage": "starting",
            "stage_label": "正在启动选股扫描",
            "completed": 0,
            "total": 0,
            "updated_at": _b1_schedule_now_text(),
        })
        child_env = os.environ.copy()
        child_env["DASHBOARD_B1_PROGRESS_FILE"] = str(progress_file)
        child_env["DASHBOARD_B1_JOB_ID"] = resolved_job_id
        child_env["DASHBOARD_B1_REQUIRE_READY_CACHE"] = (
            "1" if require_ready_cache else "0"
        )
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=B1_SCAN_TIMEOUT_SECONDS,
            env=child_env,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            items = _candidate_rows(data, "items", "candidates")
            candidates = _candidate_rows(data, "candidates", "items")
            trade_items = _candidate_rows(data, "trade_items", "items", "candidates")
            schedule_meta = {
                "schedule_run_kind": schedule_run_kind or "manual",
                "schedule_triggered_at": datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
            if schedule_slot:
                schedule_meta.update({
                    "schedule_slot": schedule_slot,
                    "schedule_run_kind": schedule_run_kind or "scheduled",
                })
            cache = {**data, "items": items, "candidates": candidates, "count": len(items),
                     "trade_items": trade_items, "trade_count": len(trade_items),
                     "total_analyzed": data.get("total_analyzed", 0),
                     "job_id": resolved_job_id,
                     "generated_at": data.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "running": False, "error": "", "cooldown_remaining_seconds": 0,
                     **schedule_meta}
            with B1_CANDIDATE_REFRESH_LOCK:
                write_json_cache(B1_CACHE_FILE, cache)
                write_practice_candidates_cache(
                    _derived_read_model_path(
                        PRACTICE_CANDIDATES_CACHE_FILE,
                        B1_CACHE_FILE,
                    ),
                    cache,
                    source_path=B1_CACHE_FILE,
                )
            if decision_mode == "sync":
                cache["decision_result"] = run_practice_decision_logged(cache, record_start=True)
            elif decision_mode == "async":
                maybe_run_practice_decision_async(cache)
            return cache
        error_detail = summarize_b1_scan_failure(result.stderr, result.stdout)
        progress = read_json_cache(progress_file, None) or {}
        stage = str(progress.get("stage") or "scan")
        print(f"[WARN] B1 scan failed: {error_detail}", file=sys.stderr, flush=True)
        return {
            "error": error_detail,
            "error_code": b1_scan_stage_error_code(stage),
            "stage": stage,
            "items": [],
            "count": 0,
            "generated_at": "",
            "running": False,
        }
    except subprocess.TimeoutExpired as exc:
        raw_stderr = exc.stderr or ""
        if isinstance(raw_stderr, bytes):
            raw_stderr = raw_stderr.decode("utf-8", "replace")
        progress = read_json_cache(
            Path(
                os.environ.get("DASHBOARD_B1_PROGRESS_FILE")
                or CRON_STATE_DIR / "b1_scan_progress.json"
            ).expanduser(),
            None,
        ) or {}
        stage = str(progress.get("stage") or "scan")
        error_code = b1_scan_stage_error_code(stage, timed_out=True)
        detail = summarize_b1_scan_failure(str(raw_stderr), "") if raw_stderr else ""
        stage_label = str(progress.get("stage_label") or "选股扫描")
        error = f"{stage_label}超时（{B1_SCAN_TIMEOUT_SECONDS}s）"
        if detail and detail != "扫描进程未返回错误详情":
            error += f"；{detail}"
        return {
            "error": error[:900],
            "error_code": error_code,
            "stage": stage,
            "items": [],
            "count": 0,
            "generated_at": "",
            "running": False,
        }
    except Exception as exc:
        progress = read_json_cache(
            Path(
                os.environ.get("DASHBOARD_B1_PROGRESS_FILE")
                or CRON_STATE_DIR / "b1_scan_progress.json"
            ).expanduser(),
            None,
        ) or {}
        stage = str(progress.get("stage") or "scan")
        return {
            "error": f"{type(exc).__name__}: {exc}"[:900],
            "error_code": b1_scan_stage_error_code(stage),
            "stage": stage,
            "items": [],
            "count": 0,
            "generated_at": "",
            "running": False,
        }


def trigger_b1_scan(
    force: bool = False,
    decision_mode: str = "async",
    *,
    schedule_slot: str = "",
    schedule_run_kind: str = "",
    job_id: str = "",
    require_ready: bool = True,
) -> dict[str, Any]:
    require_ready_cache = False
    if require_ready:
        readiness = market_data_readiness()
        require_ready_cache = bool(readiness.get("requires_full_kline_cache"))
        if not readiness.get("ready"):
            blockers = [str(item) for item in readiness.get("blockers") or []]
            storage_blocked = "runtime_storage_not_writable" in blockers
            initialization_started = False
            if (
                not storage_blocked
                and KLINE_PREWARM_ENABLED
                and readiness.get("requires_full_kline_cache")
            ):
                initialization_started = start_kline_prewarm(
                    current_cn_date_key(),
                    reason="scan-gate",
                    force=True,
                )
            error_code = (
                "runtime_storage_not_writable"
                if storage_blocked
                else blockers[0]
                if blockers
                else "market_data_not_ready"
            )
            return {
                "error": (
                    "运行数据目录不可写，请检查目录权限后重启服务"
                    if storage_blocked
                    else "日K缓存或初始化已禁用，请在设置页启用后重启服务"
                    if error_code in {"kline_cache_disabled", "kline_prewarm_disabled"}
                    else "市场数据尚未达到安全覆盖率，已在后台初始化日K缓存"
                ),
                "error_code": error_code,
                "stage": "deployment_check" if storage_blocked else "data_initializing",
                "items": [],
                "count": 0,
                "generated_at": "",
                "running": False,
                "initializing": bool(initialization_started),
                "readiness": readiness,
            }
    resolved_job_id = str(job_id or f"scan-{secrets.token_urlsafe(12)}")[:120]
    if not B1_FULL_SCAN_LOCK.acquire(blocking=False):
        return _b1_scan_busy_payload(other_process=False)
    process_lease = FileLease(
        CRON_STATE_DIR / "b1_full_scan.lock",
        stale_after_seconds=B1_SCAN_TIMEOUT_SECONDS + 120,
    )
    if not process_lease.acquire():
        B1_FULL_SCAN_LOCK.release()
        return _b1_scan_busy_payload(other_process=True)
    _begin_b1_full_scan(resolved_job_id)
    result: dict[str, Any] | None = None
    try:
        result = _trigger_b1_scan_unlocked(
            force,
            decision_mode,
            schedule_slot=schedule_slot,
            schedule_run_kind=schedule_run_kind,
            job_id=resolved_job_id,
            require_ready_cache=require_ready_cache,
        )
        result.setdefault("job_id", resolved_job_id)
        return result
    finally:
        _finish_b1_full_scan(resolved_job_id, result)
        process_lease.release()
        B1_FULL_SCAN_LOCK.release()


def _b1_active_scan_snapshot(*, include_progress: bool) -> dict[str, Any]:
    with B1_FULL_SCAN_CONDITION:
        active = dict(B1_FULL_SCAN_ACTIVE)
    if active.get("job_id") or not include_progress:
        return active
    progress = read_json_cache(
        Path(
            os.environ.get("DASHBOARD_B1_PROGRESS_FILE")
            or CRON_STATE_DIR / "b1_scan_progress.json"
        ).expanduser(),
        None,
    ) or {}
    return {
        key: progress.get(key)
        for key in ("job_id", "stage", "stage_label", "updated_at")
        if progress.get(key) not in {None, ""}
    }


def _b1_scan_busy_payload(*, other_process: bool) -> dict[str, Any]:
    active = _b1_active_scan_snapshot(include_progress=other_process)
    return {
        "status": "busy",
        "error": "",
        "error_code": "",
        "stage": "waiting_for_scan",
        "stage_label": "已有选股扫描正在运行，已加入当前任务",
        "items": [],
        "count": 0,
        "generated_at": "",
        "running": True,
        "busy": True,
        "joined": True,
        "other_process": other_process,
        "active_job_id": str(active.get("job_id") or ""),
        "active_stage": str(active.get("stage") or ""),
        "active_stage_label": str(active.get("stage_label") or ""),
    }


def _begin_b1_full_scan(job_id: str) -> None:
    with B1_FULL_SCAN_CONDITION:
        B1_FULL_SCAN_ACTIVE.clear()
        B1_FULL_SCAN_ACTIVE.update({
            "job_id": job_id,
            "running": True,
            "started_at": _b1_schedule_now_text(),
        })
        B1_FULL_SCAN_CONDITION.notify_all()


def _finish_b1_full_scan(
    job_id: str,
    result: Mapping[str, Any] | None,
) -> None:
    terminal = dict(result) if isinstance(result, Mapping) else {
        "error": "选股扫描未返回结果",
        "error_code": "candidate_scan_result_missing",
        "stage": "screening",
        "items": [],
        "count": 0,
        "generated_at": "",
        "running": False,
    }
    terminal.setdefault("job_id", job_id)
    terminal["running"] = False
    with B1_FULL_SCAN_CONDITION:
        B1_FULL_SCAN_LAST_RESULT.clear()
        B1_FULL_SCAN_LAST_RESULT.update({
            "job_id": job_id,
            "result": terminal,
            "finished_at": _b1_schedule_now_text(),
        })
        if str(B1_FULL_SCAN_ACTIVE.get("job_id") or "") == job_id:
            B1_FULL_SCAN_ACTIVE.clear()
        B1_FULL_SCAN_CONDITION.notify_all()


class PracticeCycleError(RuntimeError):
    def __init__(self, message: str, *, code: str, stage: str = "error") -> None:
        super().__init__(message)
        self.code = str(code or "practice_cycle_failed")[:120]
        self.stage = str(stage or "error")[:80]


def wait_for_b1_scan_result(
    busy_state: Mapping[str, Any],
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Join one bounded scan without starting a duplicate scan or decision."""

    target_job_id = str(busy_state.get("active_job_id") or "")
    timeout = max(
        1.0,
        float(timeout_seconds or (B1_SCAN_TIMEOUT_SECONDS + 120)),
    )
    deadline = time.monotonic() + timeout
    while True:
        with B1_FULL_SCAN_CONDITION:
            last_job_id = str(B1_FULL_SCAN_LAST_RESULT.get("job_id") or "")
            last_result = B1_FULL_SCAN_LAST_RESULT.get("result")
            if (
                target_job_id
                and last_job_id == target_job_id
                and isinstance(last_result, Mapping)
            ):
                return dict(last_result)
            active_job_id = str(B1_FULL_SCAN_ACTIVE.get("job_id") or "")

        if not target_job_id:
            target_job_id = active_job_id
            if not target_job_id and busy_state.get("other_process"):
                progress = read_json_cache(b1_scan_progress_file(), None) or {}
                target_job_id = str(progress.get("job_id") or "")

        if target_job_id and not active_job_id:
            cached = read_json_cache(B1_CACHE_FILE, None) or {}
            if str(cached.get("job_id") or "") == target_job_id:
                return cached
            if not (CRON_STATE_DIR / "b1_full_scan.lock").exists():
                raise PracticeCycleError(
                    "已加入的选股扫描结束，但没有产生可复用结果",
                    code="joined_scan_result_unavailable",
                    stage="waiting_for_scan",
                )
        elif (
            not target_job_id
            and not B1_FULL_SCAN_LOCK.locked()
            and not (CRON_STATE_DIR / "b1_full_scan.lock").exists()
        ):
            raise PracticeCycleError(
                "当前选股扫描已经结束，但无法确认可复用任务",
                code="joined_scan_identity_unavailable",
                stage="waiting_for_scan",
            )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PracticeCycleError(
                f"等待当前选股扫描超过{int(timeout)}秒",
                code="joined_scan_timeout",
                stage="waiting_for_scan",
            )
        with B1_FULL_SCAN_CONDITION:
            B1_FULL_SCAN_CONDITION.wait(timeout=min(1.0, remaining))


def practice_manual_cycle_state_file() -> Path:
    return Path(
        os.environ.get("DASHBOARD_PRACTICE_MANUAL_CYCLE_STATE_FILE")
        or CRON_STATE_DIR / "practice_manual_cycle.json"
    ).expanduser()


def b1_scan_progress_file() -> Path:
    return Path(
        os.environ.get("DASHBOARD_B1_PROGRESS_FILE")
        or CRON_STATE_DIR / "b1_scan_progress.json"
    ).expanduser()


def _public_practice_manual_cycle_state(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field: source[field]
        for field in PRACTICE_MANUAL_CYCLE_PUBLIC_FIELDS
        if field in source
    }


def restore_practice_manual_cycle_state() -> dict[str, Any]:
    """Restore terminal task metadata and safely close interrupted trading work."""
    stored = read_json_cache(practice_manual_cycle_state_file(), None)
    if not isinstance(stored, dict):
        return practice_manual_cycle_status()
    restored = _public_practice_manual_cycle_state(stored)
    if restored.get("running"):
        restored.update({
            "running": False,
            "stage": "interrupted",
            "stage_label": "上一次任务因服务重启而中断",
            "finished_at": _b1_schedule_now_text(),
            "error_code": "service_restarted",
            "error": "服务重启中断了未完成任务；为避免重复交易，本轮不会自动重放",
        })
    with PRACTICE_MANUAL_CYCLE_STATE_LOCK:
        PRACTICE_MANUAL_CYCLE_STATE.clear()
        PRACTICE_MANUAL_CYCLE_STATE.update(restored)
    return _set_practice_manual_cycle_state()


def practice_manual_cycle_status() -> dict[str, Any]:
    stored = read_json_cache(practice_manual_cycle_state_file(), None)
    if isinstance(stored, dict):
        stored_public = _public_practice_manual_cycle_state(stored)
        with PRACTICE_MANUAL_CYCLE_STATE_LOCK:
            current_updated_at = str(
                PRACTICE_MANUAL_CYCLE_STATE.get("updated_at") or ""
            )
            stored_updated_at = str(stored_public.get("updated_at") or "")
            if stored_updated_at >= current_updated_at:
                PRACTICE_MANUAL_CYCLE_STATE.clear()
                PRACTICE_MANUAL_CYCLE_STATE.update(stored_public)
    with PRACTICE_MANUAL_CYCLE_STATE_LOCK:
        status = _public_practice_manual_cycle_state(PRACTICE_MANUAL_CYCLE_STATE)
    if status.get("running") and status.get("stage") in {
        "waiting_for_scan",
        "screening",
        "code_pool",
        "quotes",
        "cache_check",
        "industry_context",
        "kline_prepare",
        "scoring",
        "news_precheck",
        "persisting",
    }:
        progress = read_json_cache(b1_scan_progress_file(), None) or {}
        progress_job_id = str(progress.get("job_id") or "")
        expected_job_id = str(
            status.get("active_scan_job_id") or status.get("job_id") or ""
        )
        if progress_job_id == expected_job_id:
            for name in (
                "stage",
                "stage_label",
                "completed",
                "total",
                "cache_hits",
                "network_fallbacks",
                "worker_count",
                "source",
                "updated_at",
            ):
                if name in progress:
                    if name == "stage" and status.get("stage") == "waiting_for_scan":
                        status["active_scan_stage"] = progress[name]
                    else:
                        status[name] = progress[name]
    completed = int(status.get("completed") or 0)
    total = int(status.get("total") or 0)
    status["progress_pct"] = round(completed / total * 100, 1) if total else 0.0
    status["error_visible"] = _practice_manual_error_is_visible(status)
    return status


def _practice_manual_error_is_visible(status: Mapping[str, Any]) -> bool:
    if status.get("running") or not status.get("error"):
        return False
    if str(status.get("stage") or "") not in {"error", "interrupted"}:
        return False
    timestamp = str(status.get("finished_at") or status.get("started_at") or "")[:19]
    if not timestamp:
        return True
    try:
        finished = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    age_seconds = (datetime.now() - finished).total_seconds()
    return -60 <= age_seconds <= PRACTICE_MANUAL_ERROR_DISPLAY_SECONDS


def _set_practice_manual_cycle_state(**updates: Any) -> dict[str, Any]:
    with PRACTICE_MANUAL_CYCLE_STATE_LOCK:
        updates.setdefault("updated_at", _b1_schedule_now_text())
        PRACTICE_MANUAL_CYCLE_STATE.update(updates)
        snapshot = dict(PRACTICE_MANUAL_CYCLE_STATE)
        public = _public_practice_manual_cycle_state(snapshot)
        try:
            write_json_cache(practice_manual_cycle_state_file(), public)
        except OSError as exc:
            print(
                f"[WARN] Manual practice task state persistence failed: {type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )
        return snapshot


def _wait_for_manual_cycle_market_data() -> None:
    readiness = market_data_readiness()
    if readiness.get("ready"):
        return
    blockers = [str(item) for item in readiness.get("blockers") or []]
    if "runtime_storage_not_writable" in blockers:
        raise PracticeCycleError(
            "运行数据目录不可写，请检查目录权限后重启服务",
            code="runtime_storage_not_writable",
            stage="deployment_check",
        )
    if "kline_cache_disabled" in blockers or "kline_prewarm_disabled" in blockers:
        raise PracticeCycleError(
            "日K缓存或初始化已禁用，请在设置页启用后重启服务",
            code=(
                "kline_cache_disabled"
                if "kline_cache_disabled" in blockers
                else "kline_prewarm_disabled"
            ),
            stage="deployment_check",
        )
    if not readiness.get("requires_full_kline_cache"):
        raise PracticeCycleError(
            "部署环境尚未达到运行条件",
            code=str(blockers[0] if blockers else "deployment_not_ready"),
            stage="deployment_check",
        )
    _set_practice_manual_cycle_state(
        stage="data_initializing",
        stage_label="正在初始化全市场日K数据",
        completed=int((readiness.get("kline") or {}).get("completed_count") or 0),
        total=int((readiness.get("kline") or {}).get("requested_count") or 0),
        error_code="",
        error="",
    )
    start_kline_prewarm(
        current_cn_date_key(),
        reason="manual",
        force=True,
    )
    wait_seconds = _bounded_int_value(
        os.environ.get(
            "DASHBOARD_MANUAL_DATA_INITIALIZATION_TIMEOUT_SECONDS",
            str(KLINE_PREWARM_TIMEOUT_SECONDS + 60),
        ),
        KLINE_PREWARM_TIMEOUT_SECONDS + 60,
        60,
        3600,
    )
    deadline = time.monotonic() + wait_seconds
    terminal_check_after = time.monotonic() + 5
    while time.monotonic() < deadline:
        readiness = market_data_readiness()
        kline = readiness.get("kline") if isinstance(readiness.get("kline"), dict) else {}
        _set_practice_manual_cycle_state(
            stage="data_initializing",
            stage_label="正在初始化全市场日K数据",
            completed=int(kline.get("completed_count") or 0),
            total=int(kline.get("requested_count") or 0),
            cache_hits=int(kline.get("fresh_count") or 0),
            network_fallbacks=int(kline.get("failure_count") or 0),
            source="tencent_kline",
        )
        if readiness.get("ready"):
            return
        blockers = [str(item) for item in readiness.get("blockers") or []]
        if "runtime_storage_not_writable" in blockers:
            raise PracticeCycleError(
                "运行数据目录不可写，请检查目录权限后重启服务",
                code="runtime_storage_not_writable",
                stage="deployment_check",
            )
        kline_status = str(kline.get("status") or "")
        if (
            kline_status in {"completed", "error"}
            and not KLINE_PREWARM_LOCK.locked()
            and time.monotonic() >= terminal_check_after
        ):
            raise PracticeCycleError(
                (
                    "全市场日K初始化完成，但有效覆盖率仍低于安全阈值"
                    if kline_status == "completed"
                    else "全市场日K初始化失败，请检查腾讯行情连通性和持久化目录"
                ),
                code=(
                    "kline_coverage_insufficient"
                    if kline_status == "completed"
                    else str(kline.get("error_code") or "kline_prewarm_failed")
                ),
                stage="data_initializing",
            )
        time.sleep(2)
    raise PracticeCycleError(
        f"市场数据初始化未在{wait_seconds}秒内达到安全覆盖率",
        code="kline_initialization_timeout",
        stage="data_initializing",
    )


def _load_practice_candidate_decision_context(
    generated_at: str,
) -> dict[str, Any]:
    """Read full scan context only for an explicitly reused trading cycle."""
    for path in (B1_CACHE_FILE, MULTI_STRATEGY_CACHE_FILE):
        payload = read_json_cache(path, None)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("generated_at") or "")[:19] != generated_at[:19]:
            continue
        return {
            key: payload.get(key)
            for key in (
                "market_snapshot",
                "sector_tide_context",
                "niuone_context",
                "zettaranc_context",
                "market_summary",
                "market_decision_context",
                "schedule_slot",
                "schedule_run_kind",
                "schedule_triggered_at",
            )
            if key in payload
        }
    return {}


def recent_practice_candidates_for_manual_cycle() -> dict[str, Any] | None:
    if PRACTICE_MANUAL_SCAN_REUSE_SECONDS <= 0:
        return None
    cache = load_practice_candidates_cache()
    if cache.get("error") or cache.get("refresh_required") or cache.get("strategy_cache_stale"):
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
        **_load_practice_candidate_decision_context(generated_at),
        "manual_scan_reused": True,
        "manual_scan_age_seconds": round(max(0.0, age_seconds), 1),
    }


def _run_practice_manual_cycle(process_lease: FileLease | None = None) -> None:
    try:
        _wait_for_manual_cycle_market_data()
        _set_practice_manual_cycle_state(stage="screening", stage_label="正在检查候选")
        cache = recent_practice_candidates_for_manual_cycle()
        if cache is None:
            cache = trigger_b1_scan(
                force=True,
                decision_mode="none",
                job_id=str(PRACTICE_MANUAL_CYCLE_STATE.get("job_id") or ""),
            )
        if cache.get("busy"):
            active_scan_job_id = str(cache.get("active_job_id") or "")
            _set_practice_manual_cycle_state(
                stage="waiting_for_scan",
                stage_label="已有选股扫描正在运行，正在加入当前任务",
                joined_existing_scan=True,
                active_scan_job_id=active_scan_job_id,
                notice_code="joined_existing_scan",
                notice="已加入当前选股扫描，完成后将复用同一份候选结果",
                error_code="",
                error="",
            )
            cache = wait_for_b1_scan_result(cache)
        if cache.get("error"):
            raise PracticeCycleError(
                str(cache.get("error")),
                code=str(cache.get("error_code") or "candidate_scan_failed"),
                stage=str(cache.get("stage") or "screening"),
            )
        if not isinstance(cache.get("niuone_context"), dict):
            start_independent_niuone_mainline_scan()
        cache = {
            **cache,
            "schedule_run_kind": "manual",
            "schedule_triggered_at": datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }

        _set_practice_manual_cycle_state(
            stage="trading",
            stage_label="正在生成盘面总结与评价并执行买卖策略",
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
            active_scan_job_id="",
            notice_code=(
                "joined_existing_scan"
                if PRACTICE_MANUAL_CYCLE_STATE.get("joined_existing_scan")
                else ""
            ),
            notice=(
                "已复用当前选股扫描，并完成本轮买卖策略"
                if PRACTICE_MANUAL_CYCLE_STATE.get("joined_existing_scan")
                else ""
            ),
            error_code="",
            error="",
        )
    except Exception as exc:
        error_code = (
            exc.code if isinstance(exc, PracticeCycleError) else type(exc).__name__
        )
        failure_stage = exc.stage if isinstance(exc, PracticeCycleError) else "error"
        _set_practice_manual_cycle_state(
            running=False,
            stage="error",
            stage_label="本轮执行失败",
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            failure_stage=failure_stage,
            active_scan_job_id="",
            error_code=error_code,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        invalidate_api_cache(
            PRACTICE_CANDIDATES_CACHE_KEY,
            TODAY_CANDIDATES_CACHE_KEY,
            TODAY_CANDIDATE_INTRADAY_CACHE_KEY,
            "niuniu_practice",
            PRACTICE_FAST_CACHE_KEY,
        )
        if process_lease is not None:
            process_lease.release()
        PRACTICE_MANUAL_CYCLE_LOCK.release()


def start_practice_manual_cycle() -> dict[str, Any]:
    if not PRACTICE_MANUAL_CYCLE_LOCK.acquire(blocking=False):
        return {
            **practice_manual_cycle_status(),
            "accepted": False,
            "busy": True,
            "notice_code": "joined_existing_manual_cycle",
            "notice": "已有手动任务正在运行，已加入当前任务",
        }
    initialization_timeout = _bounded_int_value(
        os.environ.get(
            "DASHBOARD_MANUAL_DATA_INITIALIZATION_TIMEOUT_SECONDS",
            str(KLINE_PREWARM_TIMEOUT_SECONDS + 60),
        ),
        KLINE_PREWARM_TIMEOUT_SECONDS + 60,
        60,
        3600,
    )
    decision_timeout = _bounded_int_value(
        os.environ.get("DASHBOARD_DECISION_TIMEOUT", "180"),
        180,
        10,
        1800,
    )
    process_lease = FileLease(
        CRON_STATE_DIR / "practice_manual_cycle.lock",
        stale_after_seconds=(
            initialization_timeout
            + B1_SCAN_TIMEOUT_SECONDS
            + decision_timeout
            + 300
        ),
    )
    if not process_lease.acquire():
        PRACTICE_MANUAL_CYCLE_LOCK.release()
        return {
            **practice_manual_cycle_status(),
            "accepted": False,
            "running": True,
            "busy": True,
            "error_code": "",
            "error": "",
            "notice_code": "joined_existing_manual_cycle",
            "notice": "其他服务实例正在执行本轮任务，已加入当前任务",
            "stage_label": "其他服务实例正在执行选股及买卖策略",
        }
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    job_id = "manual-" + secrets.token_urlsafe(12)
    status = _set_practice_manual_cycle_state(
        job_id=job_id,
        running=True,
        stage="starting",
        stage_label="正在启动",
        started_at=started_at,
        finished_at="",
        generated_at="",
        candidate_count=0,
        completed=0,
        total=0,
        progress_pct=0.0,
        cache_hits=0,
        network_fallbacks=0,
        manual_scan_reused=False,
        joined_existing_scan=False,
        active_scan_job_id="",
        decision_result=None,
        notice_code="",
        notice="",
        failure_stage="",
        error_code="",
        error="",
    )
    threading.Thread(
        target=_run_practice_manual_cycle,
        args=(process_lease,),
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


def _remember_b1_schedule_terminal(
    state: dict[str, Any],
    slot_key: str,
    slot: dict[str, Any],
) -> None:
    """Retain bounded terminal slot outcomes for strict-forward coverage."""
    try:
        scheduled = datetime.strptime(slot_key, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return
    status = str(slot.get("status") or "")
    if status not in {"ok", "error", "skipped"}:
        return
    raw_history = state.get("day_history")
    history = raw_history if isinstance(raw_history, dict) else {}
    day_key = scheduled.strftime("%Y-%m-%d")
    raw_day = history.get(day_key)
    day = raw_day if isinstance(raw_day, dict) else {}
    raw_slots = day.get("slots")
    slots = raw_slots if isinstance(raw_slots, dict) else {}
    slots[scheduled.strftime("%H:%M")] = {
        key: slot.get(key)
        for key in (
            "scheduled_at",
            "status",
            "started_at",
            "finished_at",
            "run_kind",
            "reason",
            "error",
            "error_code",
            "failure_stage",
        )
        if slot.get(key) not in {None, ""}
    }
    day["slots"] = slots
    day["updated_at"] = str(slot.get("updated_at") or "")
    history[day_key] = day
    state["day_history"] = {
        key: history[key]
        for key in sorted(history)[-B1_SCHEDULE_HISTORY_RETENTION_DAYS:]
    }


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
            _remember_b1_schedule_terminal(state, slot_key, slot)
        state["slots"] = slots
        _save_b1_schedule_state_unlocked(state)


def claim_due_b1_schedule_slot(now: datetime | None = None) -> str | None:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return None
    catchup_seconds = max(0, B1_SCHEDULE_CATCHUP_MINUTES) * 60
    stale_seconds = max(60, B1_SCHEDULE_STALE_SECONDS)
    due_slots: list[tuple[datetime, str]] = []
    for hhmm in PRACTICE_SCHEDULE_TIMES:
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
            _remember_b1_schedule_terminal(state, skipped_key, skipped)
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
        lag_seconds = _b1_schedule_slot_lag_seconds(slot_key)
        run_kind = "catchup" if lag_seconds >= 60 else "scheduled"
        _mark_b1_schedule_slot(slot_key, "running", lag_seconds=round(lag_seconds, 1), run_kind=run_kind)
        readiness = market_data_readiness()
        if not readiness.get("ready"):
            if (
                readiness.get("requires_full_kline_cache")
                and "runtime_storage_not_writable" not in (readiness.get("blockers") or [])
            ):
                start_kline_prewarm(
                    current_cn_date_key(),
                    reason="scheduled-gate",
                    force=True,
                )
            blockers = ",".join(str(item) for item in readiness.get("blockers") or [])
            error_code = str(
                (readiness.get("blockers") or ["market_data_not_ready"])[0]
            )
            _mark_b1_schedule_slot(
                slot_key,
                "error",
                error=error_code,
                error_code=error_code,
                readiness_blockers=blockers[:300],
            )
            print(
                f"[Practice schedule] {slot_key} blocked: market data not ready ({blockers})",
                flush=True,
            )
            return
        summary = refresh_practice_market_summary_for_decision("scheduled")
        if b1_cache_generated_for_slot(slot_key):
            start_independent_niuone_mainline_scan(slot_key)
            _mark_b1_schedule_slot(
                slot_key,
                "skipped",
                reason="cache_already_generated_for_slot",
                market_summary_generated_at=str(summary.get("generated_at") or ""),
            )
            return
        print(f"[Practice schedule] trigger {slot_key} kind={run_kind} lag={lag_seconds:.0f}s", flush=True)
        cache = trigger_b1_scan(
            force=True,
            decision_mode="none",
            schedule_slot=slot_key,
            schedule_run_kind=run_kind,
        )
        if cache.get("busy"):
            try:
                cache = wait_for_b1_scan_result(cache)
                cache = {
                    **cache,
                    "schedule_slot": slot_key,
                    "schedule_run_kind": run_kind,
                    "schedule_triggered_at": datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
            except PracticeCycleError as exc:
                cache = {
                    "error": str(exc),
                    "error_code": exc.code,
                    "stage": exc.stage,
                    "items": [],
                    "count": 0,
                    "generated_at": "",
                    "running": False,
                }
        with API_RESPONSE_LOCK:
            API_RESPONSE_CACHE.pop(PRACTICE_CANDIDATES_CACHE_KEY, None)
            API_RESPONSE_CACHE.pop(TODAY_CANDIDATES_CACHE_KEY, None)
            API_RESPONSE_CACHE.pop(TODAY_CANDIDATE_INTRADAY_CACHE_KEY, None)
        start_independent_niuone_mainline_scan(slot_key)
        if cache.get("error"):
            _mark_b1_schedule_slot(
                slot_key,
                "error",
                error=str(cache.get("error") or "")[:500],
                error_code=str(cache.get("error_code") or "candidate_scan_failed")[:120],
                failure_stage=str(cache.get("stage") or "screening")[:80],
            )
            print(f"[Practice schedule] {slot_key} failed: {cache.get('error')}", flush=True)
        else:
            if isinstance(summary, dict):
                cache["market_summary"] = summary
            decision_result = run_practice_decision_logged(
                cache,
                record_start=True,
                refresh_market_summary=False,
            )
            cache["decision_result"] = decision_result
            decision = (
                decision_result.get("decision")
                if isinstance(decision_result, dict)
                else None
            )
            if not isinstance(decision_result, dict):
                decision_error = "invalid_practice_decision_result"
            elif decision_result.get("error"):
                decision_error = str(decision_result.get("error") or "")[:500]
            elif isinstance(decision, dict) and decision.get("error"):
                decision_error = str(decision.get("error") or "")[:500]
            elif decision_result.get("durable_evidence_persisted") is not True:
                decision_error = "practice_decision_evidence_not_persisted"
            elif isinstance(decision, dict):
                decision_error = ""
            elif (
                decision_result.get("skipped") is True
                and decision_result.get("reason") == "already_decided_for_this_b1"
            ):
                decision_error = ""
            else:
                decision_error = "missing_practice_decision_payload"
            if decision_error:
                _mark_b1_schedule_slot(
                    slot_key,
                    "error",
                    error=decision_error,
                    count=int(cache.get("count") or 0),
                    generated_at=cache.get("generated_at") or "",
                    run_kind=run_kind,
                    reason="practice_decision_failed",
                )
                print(
                    f"[Practice schedule] {slot_key} decision failed: "
                    f"{decision_error}",
                    flush=True,
                )
                return
            _mark_b1_schedule_slot(
                slot_key,
                "ok",
                count=int(cache.get("count") or 0),
                generated_at=cache.get("generated_at") or "",
                run_kind=run_kind,
            )
            print(f"[Practice schedule] {slot_key} done: {cache.get('count', 0)} candidates", flush=True)
    except Exception as exc:
        _mark_b1_schedule_slot(slot_key, "error", error=f"{type(exc).__name__}: {exc}")
        print(f"[Practice schedule] {slot_key} error: {type(exc).__name__}: {exc}", flush=True)


def b1_schedule_loop() -> None:
    while True:
        slot_key = claim_due_b1_schedule_slot()
        if slot_key:
            threading.Thread(target=run_scheduled_b1_scan, args=(slot_key,), name="b1-scheduled-scan", daemon=True).start()
        time.sleep(15)


def practice_fast_cycle_context_payload(
    now: datetime | None = None,
) -> dict[str, Any]:
    """Combine the latest complete scan with the freshest NiuOne context."""

    payload: dict[str, Any] = {}
    for path in (MULTI_STRATEGY_CACHE_FILE, B1_CACHE_FILE):
        candidate = read_json_cache(path, None)
        if not isinstance(candidate, dict):
            continue
        if not payload or str(candidate.get("generated_at") or "") > str(
            payload.get("generated_at") or ""
        ):
            payload = dict(candidate)
    complete_context = payload.get("niuone_context")
    niuone_payload = load_niuone_mainline_cache_payload()
    niuone_context = niuone_payload.get("niuone_context")
    if isinstance(niuone_context, dict):
        complete_generated_at = str(payload.get("generated_at") or "")[:19]
        operational_generated_at = str(
            niuone_payload.get("generated_at") or ""
        )[:19]
        complete_date = complete_generated_at[:10]
        operational_date = operational_generated_at[:10]
        current_date = (now or current_cn_datetime()).strftime("%Y-%m-%d")
        complete_is_current = bool(
            isinstance(complete_context, Mapping)
            and complete_date == operational_date == current_date
        )
        payload["niuone_context"] = merge_niuone_holding_cycle_context(
            complete_context if complete_is_current else {},
            niuone_context,
        )
        payload["niuone_context_generated_at"] = str(
            niuone_payload.get("generated_at") or ""
        )
        payload["niuone_stock_profiles_generated_at"] = (
            complete_generated_at if complete_is_current else ""
        )
    return payload


def run_practice_fast_cycle_once(
    now: datetime | None = None,
    *,
    trader: Any | None = None,
    payload_builder: Callable[..., dict[str, Any]] = build_holding_cycle_payload,
) -> dict[str, Any]:
    """Run one holdings-only decision cycle without widening the symbol set."""

    current = now or current_cn_datetime()
    if not PRACTICE_FAST_CYCLE_ENABLED:
        return {"skipped": True, "reason": "practice_fast_cycle_disabled"}
    trader = trader or get_trader_module()
    trade_allowed, trade_reason = trader.is_a_share_execution_time(current)
    if not trade_allowed:
        return {
            "skipped": True,
            "reason": "outside_a_share_execution_time",
            "trade_reason": trade_reason,
        }
    state = trader.load_state()
    holdings: list[dict[str, Any]] = []
    for code, position in (state.get("positions") or {}).items():
        if not isinstance(position, dict):
            continue
        quantity = (
            trader.position_qty(position)
            if hasattr(trader, "position_qty")
            else int(position.get("qty") or position.get("quantity") or 0)
        )
        if quantity <= 0:
            continue
        holdings.append({
            **position,
            "code": str(position.get("code") or code),
            "qty": quantity,
        })
    if not holdings:
        return {"skipped": True, "reason": "no_open_positions"}

    payload = payload_builder(
        holdings,
        practice_fast_cycle_context_payload(),
        now=current,
    )
    if not isinstance(payload, dict):
        raise TypeError("持仓快周期评分结果必须为字典")
    payload["holding_cycle_only"] = True
    payload["holding_cycle_codes"] = sorted(
        str(holding.get("code") or "")
        for holding in holdings
        if str(holding.get("code") or "")
    )
    payload["decision_cycle_kind"] = HOLDING_CYCLE_KIND
    payload["schedule_run_kind"] = HOLDING_CYCLE_KIND
    payload["schedule_triggered_at"] = current.strftime("%Y-%m-%d %H:%M:%S")
    result = run_practice_decision_logged(
        payload,
        record_start=True,
        refresh_market_summary=True,
        decision_blocking=False,
    )
    if result.get("reason") != "practice_decision_busy":
        invalidate_api_cache(
            "niuniu_practice",
            PRACTICE_FAST_CACHE_KEY,
            "practice_benchmarks",
        )
    return {
        **result,
        "decision_cycle_kind": HOLDING_CYCLE_KIND,
        "holding_cycle_data_status": payload.get(
            "holding_cycle_data_status",
            "",
        ),
    }


def practice_fast_cycle_loop(
    *,
    stop_event: threading.Event | None = None,
    poll_seconds: float = 5.0,
) -> None:
    """Poll the hot-applied switch and run at the configured bounded cadence."""

    stop_event = stop_event or threading.Event()
    last_attempt = 0.0
    while not stop_event.is_set():
        now_monotonic = time.monotonic()
        interval = float(PRACTICE_FAST_CYCLE_INTERVAL_SECONDS)
        if (
            PRACTICE_FAST_CYCLE_ENABLED
            and now_monotonic - last_attempt >= interval
        ):
            last_attempt = now_monotonic
            try:
                result = run_practice_fast_cycle_once()
                if not result.get("skipped"):
                    print(
                        "[practice fast cycle] "
                        f"status={result.get('holding_cycle_data_status') or 'ready'} "
                        f"executed={len(result.get('executed') or [])}",
                        flush=True,
                    )
            except Exception as exc:
                print(
                    "[WARN] 持仓快周期失败: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
            finally:
                # Keep the configured interval between completed attempts so
                # a slow upstream/model call cannot create an immediate retry
                # loop after it returns.
                last_attempt = time.monotonic()
        if stop_event.wait(max(1.0, min(float(poll_seconds), interval))):
            return


def pending_decision_loop() -> None:
    while True:
        try:
            trader = get_trader_module()
            if hasattr(trader, "execute_due_pending_decisions"):
                with PRACTICE_DECISION_LOCK:
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
    refresh_practice_position_marks_on_startup()
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


def _market_breadth_history_recovery_file() -> Path:
    suffix = MARKET_BREADTH_HISTORY_FILE.suffix or ".json"
    return MARKET_BREADTH_HISTORY_FILE.with_name(
        f"{MARKET_BREADTH_HISTORY_FILE.stem}.recovery{suffix}"
    )


def _market_breadth_history_day(history: dict[str, Any] | None) -> str:
    """Resolve the newest real sample day before trusting file metadata."""

    source = history if isinstance(history, dict) else {}
    sample_days = sorted({
        compact["generated_at"][:10]
        for raw in source.get("samples") or []
        if (
            compact := compact_market_breadth_sample(
                raw if isinstance(raw, dict) else None
            )
        ) is not None
    })
    if sample_days:
        return sample_days[-1]
    day = str(source.get("date") or "")[:10]
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return ""
    return day


def _market_breadth_history_for_day(
    day: str,
    *histories: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge durable samples without letting a shorter file erase real points."""

    samples: list[Any] = []
    for history in histories:
        if not isinstance(history, dict):
            continue
        samples.extend(history.get("samples") or [])
        for archive_key in ("previous_day", "previous_turnover"):
            archive = history.get(archive_key)
            if isinstance(archive, dict):
                samples.extend(archive.get("samples") or [])
    return roll_market_breadth_history(
        {"samples": samples},
        day,
        interval_seconds=MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS,
    )


def _persist_market_breadth_history(history: dict[str, Any]) -> bool:
    """Atomically preserve a non-empty curve before updating the active file."""

    day = _market_breadth_history_day(history)
    if not day:
        return False
    recovery_file = _market_breadth_history_recovery_file()
    recovery = read_json_cache(recovery_file, None)
    current = read_json_cache(MARKET_BREADTH_HISTORY_FILE, None)
    merged = _market_breadth_history_for_day(day, recovery, current, history)
    changed = False
    if merged.get("samples") and merged != recovery:
        write_json_cache(recovery_file, merged)
        changed = True
    if merged != current:
        write_json_cache(MARKET_BREADTH_HISTORY_FILE, merged)
        changed = True
    return changed


def _empty_industry_flow_history(day: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "date": day,
        "interval_seconds": INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS,
        "samples": [],
    }


def _industry_flow_history_recovery_file() -> Path:
    suffix = INDUSTRY_FLOW_HISTORY_FILE.suffix or ".json"
    return INDUSTRY_FLOW_HISTORY_FILE.with_name(
        f"{INDUSTRY_FLOW_HISTORY_FILE.stem}.recovery{suffix}"
    )


def _industry_flow_history_for_day(
    day: str,
    *histories: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge durable real samples for one display day without trusting metadata."""

    by_time: dict[str, dict[str, Any]] = {}
    for history in histories:
        if not isinstance(history, dict):
            continue
        for raw in history.get("samples") or []:
            compact = compact_industry_flow_sample(
                raw if isinstance(raw, dict) else None
            )
            if compact is None or compact["generated_at"][:10] != day:
                continue
            by_time[compact["generated_at"]] = compact
    samples = [by_time[key] for key in sorted(by_time)][-INDUSTRY_FLOW_HISTORY_LIMIT:]
    return {
        "schema_version": 1,
        "date": day,
        "interval_seconds": INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS,
        "samples": samples,
    }


def _back_up_industry_flow_history(history: dict[str, Any] | None) -> bool:
    """Preserve the newest real sample day before a rollover removes it."""

    if not isinstance(history, dict):
        return False
    sample_days = sorted({
        compact["generated_at"][:10]
        for raw in history.get("samples") or []
        if (
            compact := compact_industry_flow_sample(
                raw if isinstance(raw, dict) else None
            )
        ) is not None
    })
    if not sample_days:
        return False
    recovery_file = _industry_flow_history_recovery_file()
    recovery = read_json_cache(recovery_file, None)
    backed_up = _industry_flow_history_for_day(
        sample_days[-1],
        recovery,
        history,
    )
    if backed_up == recovery:
        return False
    write_json_cache(recovery_file, backed_up)
    return True


def _persist_industry_flow_history(history: dict[str, Any]) -> bool:
    """Atomically mirror non-empty history before replacing the primary file."""

    recovery_file = _industry_flow_history_recovery_file()
    recovery = read_json_cache(recovery_file, None)
    changed = False
    if history.get("samples") and history != recovery:
        write_json_cache(recovery_file, history)
        changed = True
    current = read_json_cache(INDUSTRY_FLOW_HISTORY_FILE, None)
    if history != current:
        write_json_cache(INDUSTRY_FLOW_HISTORY_FILE, history)
        changed = True
    return changed


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
    """Roll daily market display data at the Beijing-time 09:00 boundary."""

    current = now or current_cn_datetime()
    day = market_retention_date_key(current)
    changed = False
    with MARKET_BREADTH_HISTORY_LOCK:
        history = read_json_cache(MARKET_BREADTH_HISTORY_FILE, None)
        recovery = read_json_cache(_market_breadth_history_recovery_file(), None)
        rolled = _market_breadth_history_for_day(
            day,
            recovery,
            history,
        )
        if (history is not None or recovery is not None) and rolled != history:
            changed = _persist_market_breadth_history(rolled) or changed
    with INDUSTRY_FLOW_HISTORY_LOCK:
        history = read_json_cache(INDUSTRY_FLOW_HISTORY_FILE, None)
        recovery = read_json_cache(_industry_flow_history_recovery_file(), None)
        rolled = _industry_flow_history_for_day(day, recovery, history)
        if rolled.get("samples"):
            changed = _persist_industry_flow_history(rolled) or changed
        elif history is not None:
            empty = _empty_industry_flow_history(day)
            if history != empty:
                _back_up_industry_flow_history(history)
                write_json_cache(INDUSTRY_FLOW_HISTORY_FILE, empty)
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
    """Return seconds until midnight; retained for compatibility callers."""

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
    """Roll or clear daily chart histories at 09:00 Beijing time."""

    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        if stop_event.wait(
            seconds_until_next_market_retention_rollover(current_cn_datetime())
        ):
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
    print("Daily market history reset enabled: 09:00 Asia/Shanghai", flush=True)


def load_market_breadth_samples(
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    resolved_now = now or current_cn_datetime()
    current_day = market_retention_date_key(resolved_now)
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
    current_day = market_retention_date_key(resolved_now)
    reset_daily_market_histories(resolved_now)
    with MARKET_BREADTH_HISTORY_LOCK:
        history = read_json_cache(MARKET_BREADTH_HISTORY_FILE, None) or {}
        return compact_previous_turnover_history(
            history.get("previous_turnover")
            if isinstance(history.get("previous_turnover"), dict)
            else None,
            before_date=current_day,
        )


def load_previous_market_breadth_samples(
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Load the newest complete breadth curve before the active display day."""

    resolved_now = now or current_cn_datetime()
    current_day = market_retention_date_key(resolved_now)
    reset_daily_market_histories(resolved_now)
    with MARKET_BREADTH_HISTORY_LOCK:
        history = read_json_cache(MARKET_BREADTH_HISTORY_FILE, None) or {}
        recovery = read_json_cache(_market_breadth_history_recovery_file(), None)
        candidates: list[dict[str, Any]] = []
        for raw in (
            history.get("previous_day"),
            recovery,
            recovery.get("previous_day") if isinstance(recovery, dict) else None,
        ):
            previous = compact_previous_market_breadth_history(
                raw if isinstance(raw, dict) else None,
                before_date=current_day,
            )
            if previous is not None:
                candidates.append(previous)
        previous = (
            max(candidates, key=lambda item: str(item.get("date") or ""))
            if candidates
            else None
        )
        return list((previous or {}).get("samples") or [])


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
        or compact["generated_at"][:10] != market_retention_date_key(resolved_now)
        or not is_market_breadth_session_timestamp(compact["generated_at"])
    ):
        return load_market_breadth_samples(now=resolved_now)
    with MARKET_BREADTH_HISTORY_LOCK:
        history = read_json_cache(MARKET_BREADTH_HISTORY_FILE, None)
        recovery = read_json_cache(_market_breadth_history_recovery_file(), None)
        history = _market_breadth_history_for_day(
            market_retention_date_key(resolved_now),
            recovery,
            history,
        )
        updated = append_market_breadth_sample(
            history,
            snapshot,
            interval_seconds=MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS,
        )
        if updated != history and updated.get("samples"):
            _persist_market_breadth_history(updated)
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
        previous_samples = load_previous_market_breadth_samples(now=now)
        if previous_samples:
            fallback = dict(previous_samples[-1])
            fallback.update({
                "stale_cache": True,
                "error": f"{type(error).__name__}: {error}",
            })
            payload = build_market_breadth_payload(
                fallback,
                history_samples=previous_samples,
                interval_seconds=MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS,
            )
            payload.update({
                "displaying_previous_trading_day": True,
                "display_date": fallback["generated_at"][:10],
            })
            return payload
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
        if is_market_breadth_sampling_window(now):
            return None
        previous_samples = load_previous_market_breadth_samples(now=now)
        if not previous_samples:
            return None
        latest = previous_samples[-1]
        payload = build_market_breadth_payload(
            latest,
            history_samples=previous_samples,
            interval_seconds=MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS,
        )
        payload.update({
            "displaying_previous_trading_day": True,
            "display_date": latest["generated_at"][:10],
        })
        return payload
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


def _fetch_market_turnover_estimate_with_persistent_profile(
    generated_at: datetime,
    fallback_actual_turnover_yi: Any,
) -> dict[str, Any]:
    return fetch_market_turnover_estimate(
        generated_at,
        fallback_actual_turnover_yi,
        profile_fetcher=lambda before_date: fetch_turnover_profile(
            before_date,
            persistent_cache_path=TURNOVER_PROFILE_CACHE_FILE,
        ),
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
            snapshot = fetch_tencent_market_breadth(
                turnover_estimate_fetcher=(
                    _fetch_market_turnover_estimate_with_persistent_profile
                ),
                quote_snapshot_consumer=(
                    accept_niuone_mainline_quote_snapshot
                    if NIUONE_MAINLINE_MINUTE_REFRESH_ENABLED
                    else None
                )
            )
            samples = record_market_breadth_sample(snapshot, now=current)
            compact = compact_market_breadth_sample(snapshot)
            if (
                compact is None
                or compact["generated_at"][:10] != market_retention_date_key(current)
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
            30.0,
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


def market_breadth_auto_recovery_state(
    now: datetime | None = None,
    *,
    started_at: datetime | None = None,
) -> dict[str, Any]:
    """Return whether startup recovery can run without weakening validation."""

    current = now or current_cn_datetime()
    if not is_a_share_trading_day_for_dashboard(current):
        return {"status": "not_trading_day"}
    if current.hour < 9:
        return {"status": "waiting_open"}
    samples = load_market_breadth_samples(now=current)
    close_boundary = current.replace(hour=15, minute=1, second=0, microsecond=0)
    if started_at is not None and current < close_boundary:
        latest_time = max(
            (
                datetime.strptime(sample["generated_at"], "%Y-%m-%d %H:%M:%S")
                for sample in samples
                if str(sample.get("generated_at") or "")[:10]
                == current_cn_date_key(current)
            ),
            default=None,
        )
        if latest_time is None or latest_time < started_at:
            return {"status": "waiting_startup_sample"}
    after_close = current >= close_boundary
    plan = plan_market_breadth_recovery(
        current_cn_date_key(current),
        samples,
        expected_through=(
            current.replace(hour=15, minute=0, second=0, microsecond=0)
            if after_close
            else None
        ),
        allow_pre_gap_validation=after_close,
    )
    if (
        plan["status"] in {"waiting_boundary", "waiting_validation"}
        and after_close
    ):
        return {**plan, "status": "insufficient_validation"}
    return plan


def run_market_breadth_auto_recovery_process(
    *,
    deadline_seconds: int = MARKET_BREADTH_AUTO_RECOVERY_DEADLINE_SECONDS,
    process_timeout_seconds: int = (
        MARKET_BREADTH_AUTO_RECOVERY_PROCESS_TIMEOUT_SECONDS
    ),
) -> str:
    """Run the validated writer in one bounded, cross-process leased child."""

    lease = FileLease(
        CRON_STATE_DIR / "market_breadth_auto_recovery.lock",
        stale_after_seconds=process_timeout_seconds + 120,
    )
    if not lease.acquire():
        return "busy"
    try:
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ENTRYPOINT_DIR / "recover_market_breadth_history.py"),
                    "--write",
                    "--deadline-seconds",
                    str(max(30, int(deadline_seconds))),
                ],
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=max(60, int(process_timeout_seconds)),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "failed"
        return "succeeded" if completed.returncode == 0 else "failed"
    finally:
        lease.release()


def market_breadth_auto_recovery_loop(
    *,
    stop_event: threading.Event | None = None,
    poll_seconds: float = 30.0,
    runner: Callable[[], str] | None = None,
) -> None:
    """Wait for real cross-check points, then backfill today's startup gap."""

    stop_event = stop_event or threading.Event()
    run_recovery = runner or run_market_breadth_auto_recovery_process
    started_at = current_cn_datetime()
    attempts = 0
    while not stop_event.is_set():
        state = market_breadth_auto_recovery_state(started_at=started_at)
        status = str(state.get("status") or "")
        if status in {"complete", "not_trading_day", "insufficient_validation"}:
            return
        if status == "ready":
            outcome = str(run_recovery() or "failed")
            if outcome == "succeeded":
                invalidate_api_cache("market_breadth")
                print("Market breadth startup recovery completed", flush=True)
                return
            if outcome == "failed":
                attempts += 1
                if attempts >= MARKET_BREADTH_AUTO_RECOVERY_MAX_ATTEMPTS:
                    print(
                        "[WARN] 市场宽度启动补齐失败: bounded retries exhausted",
                        flush=True,
                    )
                    return
                wait_seconds = MARKET_BREADTH_AUTO_RECOVERY_RETRY_SECONDS
            else:
                wait_seconds = poll_seconds
        else:
            wait_seconds = poll_seconds
        if stop_event.wait(max(0.1, float(wait_seconds))):
            return


def start_market_breadth_auto_recovery() -> None:
    global MARKET_BREADTH_AUTO_RECOVERY_THREAD
    if (
        MARKET_BREADTH_AUTO_RECOVERY_THREAD
        and MARKET_BREADTH_AUTO_RECOVERY_THREAD.is_alive()
    ):
        return
    MARKET_BREADTH_AUTO_RECOVERY_THREAD = threading.Thread(
        target=market_breadth_auto_recovery_loop,
        name="market-breadth-auto-recovery",
        daemon=True,
    )
    MARKET_BREADTH_AUTO_RECOVERY_THREAD.start()
    print("Market breadth startup recovery enabled", flush=True)


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
    current_day = market_retention_date_key(resolved_now)
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
    current_day = market_retention_date_key(resolved_now)
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
            _persist_industry_flow_history(updated)
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


def _money_flow_with_display_period(
    payload: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    generated_date = str(payload.get("generated_at") or "")[:10]
    current_date = now.strftime("%Y-%m-%d")
    if not generated_date or generated_date >= current_date:
        return payload
    try:
        calendar = dashboard_trading_day_status(now)
    except Exception:
        calendar = {}
    previous_date = str(calendar.get("previous_trading_day") or "")[:10]
    return {
        **payload,
        "display_date": generated_date,
        "displaying_historical_data": True,
        "displaying_previous_trading_day": generated_date == previous_date,
    }


def load_previous_money_flow_snapshot(
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Rebuild the latest real fund-flow ranking from durable samples."""

    resolved_now = now or current_cn_datetime()
    reset_daily_market_histories(resolved_now)
    with INDUSTRY_FLOW_HISTORY_LOCK:
        primary = read_json_cache(INDUSTRY_FLOW_HISTORY_FILE, None)
        recovery = read_json_cache(_industry_flow_history_recovery_file(), None)
    candidates: list[dict[str, Any]] = []
    for history in (primary, recovery):
        if not isinstance(history, dict):
            continue
        for raw in history.get("samples") or []:
            compact = compact_industry_flow_sample(
                raw if isinstance(raw, dict) else None
            )
            if compact is not None:
                candidates.append(compact)
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: str(item.get("generated_at") or ""))
    rows = [row for row in latest.get("items") or [] if isinstance(row, dict)]
    inflow = sorted(
        (row for row in rows if float(row.get("net_flow_yi") or 0) > 0),
        key=lambda row: (-float(row.get("net_flow_yi") or 0), str(row.get("name") or "")),
    )
    outflow = sorted(
        (row for row in rows if float(row.get("net_flow_yi") or 0) < 0),
        key=lambda row: (float(row.get("net_flow_yi") or 0), str(row.get("name") or "")),
    )
    if not inflow and not outflow:
        return None
    payload = {
        "schema_version": 2,
        "metric": "industry_main_net_flow",
        "metric_label": "最近交易日主力净额",
        "source": "本地最近交易日资金采样",
        "generated_at": latest["generated_at"],
        "inflow": [dict(row) for row in inflow],
        "outflow": [dict(row) for row in outflow],
        "count": len(rows),
        "stale_cache": True,
    }
    return _money_flow_with_display_period(payload, now=resolved_now)


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
        invalidate_api_cache("money_flow")
        invalidate_api_cache_prefix("industry_flow")
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
    if not B1_SCHEDULE_ENABLED or not PRACTICE_SCHEDULE_TIMES:
        return
    if B1_SCHEDULE_THREAD and B1_SCHEDULE_THREAD.is_alive():
        return
    B1_SCHEDULE_THREAD = threading.Thread(target=b1_schedule_loop, name="b1-scheduler", daemon=True)
    B1_SCHEDULE_THREAD.start()
    print(f"Practice schedule enabled: {', '.join(PRACTICE_SCHEDULE_TIMES)}", flush=True)


def start_practice_fast_cycle() -> None:
    """Keep one dormant-capable worker so the setting can hot-apply."""

    global PRACTICE_FAST_CYCLE_THREAD
    if (
        PRACTICE_FAST_CYCLE_THREAD
        and PRACTICE_FAST_CYCLE_THREAD.is_alive()
    ):
        return
    PRACTICE_FAST_CYCLE_THREAD = threading.Thread(
        target=practice_fast_cycle_loop,
        name="practice-fast-cycle",
        daemon=True,
    )
    PRACTICE_FAST_CYCLE_THREAD.start()
    print(
        "Practice holding fast cycle "
        f"{'enabled' if PRACTICE_FAST_CYCLE_ENABLED else 'available'}: "
        f"{PRACTICE_FAST_CYCLE_INTERVAL_SECONDS}s",
        flush=True,
    )


def start_kline_prewarm_scheduler() -> None:
    global KLINE_PREWARM_SCHEDULER_THREAD
    if not KLINE_PREWARM_ENABLED:
        return
    if KLINE_PREWARM_SCHEDULER_THREAD and KLINE_PREWARM_SCHEDULER_THREAD.is_alive():
        return
    KLINE_PREWARM_SCHEDULER_THREAD = threading.Thread(
        target=kline_prewarm_schedule_loop,
        name="kline-prewarm-scheduler",
        daemon=True,
    )
    KLINE_PREWARM_SCHEDULER_THREAD.start()
    print(f"K-line prewarm schedule enabled: {KLINE_PREWARM_TIME}", flush=True)


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

CATEGORIES = {
    "market_monitor": "盘面监控",
    "other": "其他",
}

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


def practice_market_summary_generation_status() -> dict[str, Any]:
    with PRACTICE_MARKET_SUMMARY_STATE_LOCK:
        return {
            field: PRACTICE_MARKET_SUMMARY_STATE[field]
            for field in PRACTICE_MARKET_SUMMARY_PUBLIC_FIELDS
            if field in PRACTICE_MARKET_SUMMARY_STATE
        }


def _set_practice_market_summary_state(**updates: Any) -> dict[str, Any]:
    with PRACTICE_MARKET_SUMMARY_STATE_LOCK:
        PRACTICE_MARKET_SUMMARY_STATE.update(updates)
        return dict(PRACTICE_MARKET_SUMMARY_STATE)


def get_practice_market_summary_status() -> dict[str, Any]:
    payload = practice_market_summary_impl.summary_status(
        _practice_market_summary_records(),
        PRACTICE_MARKET_SUMMARY_FILE,
        current_cn_datetime(),
    )
    generation = practice_market_summary_generation_status()
    generation_error = str(generation.get("error") or "")
    payload.update({
        "running": bool(generation.get("running")),
        "stage": str(generation.get("stage") or "idle"),
        "stage_label": str(generation.get("stage_label") or ""),
        "started_at": str(generation.get("started_at") or ""),
        "finished_at": str(generation.get("finished_at") or ""),
        "generation_error": generation_error,
    })
    if generation_error:
        payload["error"] = generation_error
    return payload


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
    industry_flow_payload: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(jobs) + 1) as pool:
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
        futures["market_breadth"] = pool.submit(produce_market_breadth_data)
        for key, future in futures.items():
            try:
                result = future.result()
                if key == "money_flow":
                    money_flow, history_samples = result
                    payloads[key] = money_flow
                    industry_flow_payload = build_industry_flow_payload(
                        money_flow,
                        side_limit=INDUSTRY_FLOW_SIDE_LIMIT,
                        history_samples=history_samples,
                        sample_interval_seconds=INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS,
                        playback_speed=INDUSTRY_FLOW_PLAYBACK_SPEED,
                        sampling_windows=INDUSTRY_FLOW_SAMPLING_WINDOWS,
                    )
                    if money_flow.get("inflow") or money_flow.get("outflow"):
                        invalidate_api_cache("money_flow")
                        invalidate_api_cache_prefix("industry_flow")
                else:
                    payloads[key] = result
            except Exception as exc:
                fallback = jobs[key][1] if key in jobs else {"latest": {}, "timeline": []}
                payloads[key] = {**fallback, "error": f"{type(exc).__name__}: {exc}"}
    snapshot = practice_market_summary_impl.build_realtime_market_snapshot(
        payloads.get("indices") or {},
        payloads.get("sectors") or {},
        payloads.get("money_flow") or {},
        now,
    )
    return practice_market_summary_impl.add_dashboard_market_references(
        snapshot,
        industry_flow_payload=industry_flow_payload,
        market_breadth_payload=payloads.get("market_breadth") or {},
    )


def generate_practice_market_summary(trigger: str = "manual") -> dict[str, Any]:
    return practice_market_summary_impl.generate_and_store_summary(
        _practice_market_summary_records(),
        PRACTICE_MARKET_SUMMARY_FILE,
        current_cn_datetime(),
        realtime_snapshot_provider=fetch_practice_realtime_market_snapshot,
        require_realtime=True,
        trigger=trigger,
    )


def _cached_practice_market_summary() -> dict[str, Any]:
    return practice_market_summary_impl.load_cached_summary(
        PRACTICE_MARKET_SUMMARY_FILE,
        current_cn_datetime().strftime("%Y-%m-%d"),
    )


def _publish_practice_market_summary_context(payload: dict[str, Any]) -> dict[str, Any]:
    trader = get_trader_module()
    if not hasattr(trader, "persist_market_summary_context"):
        raise RuntimeError("交易模块不支持统一盘面评价")
    ctx = trader.persist_market_summary_context(payload, current_cn_datetime())
    invalidate_api_cache("niuniu_practice", PRACTICE_FAST_CACHE_KEY)
    return trader.compact_market_strategy_context(ctx)


def _generate_and_publish_practice_market_summary(trigger: str) -> dict[str, Any]:
    _set_practice_market_summary_state(
        running=True,
        stage="generating",
        stage_label="正在抓取实时盘面、资金流动和市场情绪并生成总结与评价",
        error="",
    )
    payload = generate_practice_market_summary(trigger)
    if not payload.get("ok"):
        _set_practice_market_summary_state(
            running=False,
            stage="error",
            stage_label="盘面总结与评价生成失败",
            finished_at=current_cn_datetime().strftime("%Y-%m-%d %H:%M:%S"),
            error=str(payload.get("error") or "盘面总结与评价生成失败"),
        )
        return payload
    _publish_practice_market_summary_context(payload)
    _set_practice_market_summary_state(
        running=False,
        stage="completed",
        stage_label="此刻盘面总结与评价已更新",
        finished_at=current_cn_datetime().strftime("%Y-%m-%d %H:%M:%S"),
        generated_at=str(payload.get("generated_at") or ""),
        error="",
    )
    return payload


def refresh_practice_market_summary_for_decision(trigger: str) -> dict[str, Any]:
    """Synchronously refresh the unified artifact, preserving the last valid one on failure."""
    if not PRACTICE_MARKET_SUMMARY_LOCK.acquire(blocking=False):
        return _cached_practice_market_summary()
    try:
        started_at = current_cn_datetime().strftime("%Y-%m-%d %H:%M:%S")
        _set_practice_market_summary_state(
            running=True,
            stage="starting",
            stage_label="正在启动盘面总结与评价",
            started_at=started_at,
            finished_at="",
            generated_at="",
            error="",
        )
        try:
            payload = _generate_and_publish_practice_market_summary(trigger)
        except Exception as exc:
            payload = {
                "ok": False,
                "available": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            _set_practice_market_summary_state(
                running=False,
                stage="error",
                stage_label="盘面总结与评价生成失败",
                finished_at=current_cn_datetime().strftime("%Y-%m-%d %H:%M:%S"),
                error=payload["error"],
            )
        if payload.get("ok"):
            return payload
        cached = _cached_practice_market_summary()
        return cached if cached.get("available") else payload
    finally:
        PRACTICE_MARKET_SUMMARY_LOCK.release()


def _run_practice_market_summary() -> None:
    try:
        _generate_and_publish_practice_market_summary("manual")
    except Exception as exc:
        _set_practice_market_summary_state(
            running=False,
            stage="error",
            stage_label="盘面总结与评价生成失败",
            finished_at=current_cn_datetime().strftime("%Y-%m-%d %H:%M:%S"),
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        PRACTICE_MARKET_SUMMARY_LOCK.release()


def start_practice_market_summary() -> dict[str, Any]:
    if not PRACTICE_MARKET_SUMMARY_LOCK.acquire(blocking=False):
        return {
            "ok": True,
            **practice_market_summary_generation_status(),
            "accepted": False,
        }
    started_at = current_cn_datetime().strftime("%Y-%m-%d %H:%M:%S")
    _set_practice_market_summary_state(
        running=True,
        stage="starting",
        stage_label="正在启动盘面总结与评价",
        started_at=started_at,
        finished_at="",
        generated_at="",
        error="",
    )
    worker = threading.Thread(
        target=_run_practice_market_summary,
        name="niuone-practice-market-summary",
        daemon=True,
    )
    try:
        worker.start()
    except Exception as exc:
        _set_practice_market_summary_state(
            running=False,
            stage="error",
            stage_label="盘面总结与评价启动失败",
            finished_at=current_cn_datetime().strftime("%Y-%m-%d %H:%M:%S"),
            error=f"{type(exc).__name__}: {exc}",
        )
        PRACTICE_MARKET_SUMMARY_LOCK.release()
        raise
    return {
        "ok": True,
        **practice_market_summary_generation_status(),
        "accepted": True,
    }


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


def _refresh_api_cache(
    cache_key: str,
    producer,
    generation: int,
    key_lock: threading.Lock,
    cacheable=None,
) -> None:
    response_cache_impl.refresh_payload(
        cache_key,
        producer,
        generation,
        key_lock,
        store=_store_api_cache_payload,
        warn=lambda message: print(message, file=sys.stderr),
        cacheable=cacheable,
    )


def cache_get_json(cache_key: str, ttl: int, producer, *, cacheable=None) -> tuple[bytes, bool]:
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
        cacheable=cacheable,
    )


def seed_api_cache_from_json_file(
    cache_key: str,
    path: Path,
    ttl: int,
    transform=None,
    *,
    cacheable=None,
) -> bool:
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
        cacheable=cacheable,
        stale_while_refresh_seconds=API_STALE_WHILE_REFRESH_SECONDS,
    )


def _api_cache_entry_is_fresh(cache_key: str, ttl: int) -> bool:
    current_time = time.time()
    with API_RESPONSE_LOCK:
        cached = API_RESPONSE_CACHE.get(cache_key)
        return bool(
            cached
            and current_time - float(cached.get("ts") or 0) < max(0, ttl)
        )


def is_global_market_prewarm_window(now: datetime | None = None) -> bool:
    """Cover the Beijing-time global trading week without weekend polling."""

    current = now or current_cn_datetime()
    weekday = current.weekday()
    if weekday == 0:
        return current.hour >= 6
    if 1 <= weekday <= 4:
        return True
    if weekday == 5:
        return current.hour < 6
    return False


def prewarm_market_api_cache(*, now: datetime | None = None) -> bool:
    """Keep relevant market caches warm without polling closed markets."""

    current = now or current_cn_datetime()
    indices_ttl = API_TTLS["indices"]
    sectors_ttl = API_TTLS["sectors"]
    hot_ttl = API_TTLS["hot_stocks"]
    sectors_snapshot = CRON_OUTPUT_DIR / "sectors_dashboard_cache.json"
    hot_snapshot = CRON_OUTPUT_DIR / "hot_stocks_dashboard_cache.json"

    seed_api_cache_from_json_file(
        "indices",
        INDICES_SNAPSHOT_FILE,
        indices_ttl,
        cacheable=market_indices_available,
    )
    seed_api_cache_from_json_file(
        "sectors",
        sectors_snapshot,
        sectors_ttl,
        cacheable=market_sectors_available,
    )
    seed_api_cache_from_json_file(
        "hot_stocks:amount",
        hot_snapshot,
        hot_ttl,
        lambda payload: apply_hot_stocks_sort(payload, "amount"),
        cacheable=market_hot_stocks_available,
    )

    refresh_results = []
    if is_global_market_prewarm_window(current):
        indices = cached_json_data(
            "indices",
            indices_ttl,
            produce_indices_data,
            {"items": []},
            cacheable=market_indices_available,
        )
        refresh_results.append(
            market_indices_available(indices)
            and _api_cache_entry_is_fresh("indices", indices_ttl)
        )

    if is_market_breadth_sampling_window(current):
        sectors = cached_json_data(
            "sectors",
            sectors_ttl,
            produce_sectors_data,
            {"sectors": [], "items": []},
            cacheable=market_sectors_available,
        )
        refresh_results.append(
            market_sectors_available(sectors)
            and _api_cache_entry_is_fresh("sectors", sectors_ttl)
        )
        hot_stocks = cached_json_data(
            "hot_stocks:amount",
            hot_ttl,
            produce_hot_stocks_data,
            {"items": []},
            cacheable=market_hot_stocks_available,
        )
        refresh_results.append(
            market_hot_stocks_available(hot_stocks)
            and _api_cache_entry_is_fresh("hot_stocks:amount", hot_ttl)
        )

    return all(refresh_results)


def market_api_prewarm_loop(
    *,
    stop_event: threading.Event | None = None,
    poll_seconds: float = 30.0,
    max_backoff_seconds: float = 300.0,
    run_once=None,
) -> None:
    """Refresh shared market caches periodically, independent of active pages."""

    stop_event = stop_event or threading.Event()
    active_run_once = run_once or prewarm_market_api_cache
    base_poll_seconds = max(5.0, float(poll_seconds))
    maximum_backoff = max(base_poll_seconds, float(max_backoff_seconds))
    retry_seconds = base_poll_seconds
    while not stop_event.is_set():
        succeeded = True
        try:
            succeeded = active_run_once() is not False
        except Exception as exc:
            succeeded = False
            print(
                f"[WARN] 行情缓存预热失败: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
        if succeeded:
            retry_seconds = base_poll_seconds
        else:
            retry_seconds = min(
                maximum_backoff,
                max(base_poll_seconds * 2, retry_seconds * 2),
            )
        if stop_event.wait(retry_seconds):
            return


def start_market_api_prewarm() -> None:
    global MARKET_API_PREWARM_THREAD
    if MARKET_API_PREWARM_THREAD and MARKET_API_PREWARM_THREAD.is_alive():
        return
    MARKET_API_PREWARM_THREAD = threading.Thread(
        target=market_api_prewarm_loop,
        name="market-api-prewarm",
        daemon=True,
    )
    MARKET_API_PREWARM_THREAD.start()
    print("Market API cache prewarm enabled: 30s", flush=True)


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


def cached_json_data(
    cache_key: str,
    ttl: int,
    producer,
    fallback: dict[str, Any],
    *,
    cacheable=None,
) -> dict[str, Any]:
    payload, _ = cache_get_json(
        cache_key,
        ttl,
        producer,
        cacheable=cacheable,
    )
    return response_cache_impl.decode_json_data(payload, fallback)


def iwencai_dragon_tiger_archive_dir() -> Path:
    return IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE.parent / "iwencai_dragon_tiger"


def iwencai_dragon_tiger_snapshot_version(
    trade_date: str,
    *,
    include_latest: bool,
) -> int:
    del trade_date  # Kept in the signature for compatibility with existing callers.
    if not include_latest:
        return 0
    try:
        return IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE.stat().st_mtime_ns
    except OSError:
        return 0


def iwencai_dragon_tiger_retained_date() -> str:
    """Return the date of the rolling snapshot that remains publicly visible."""

    snapshot = read_dragon_tiger_snapshot(IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE)
    return str(snapshot.get("date") or "") if snapshot else ""


def _iwencai_dragon_tiger_latest_snapshot(
    trade_date: str,
) -> dict[str, Any] | None:
    latest = read_dragon_tiger_snapshot(IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE)
    if not latest:
        return None
    latest["stale"] = str(latest.get("date") or "") != trade_date
    latest["requested_date"] = trade_date
    latest["scheduled_refresh_time"] = "18:00"
    return latest


def produce_iwencai_dragon_tiger_data(
    trade_date: str,
    *,
    page: int,
    limit: int,
    allow_latest_snapshot: bool,
    fallback_to_latest_on_empty: bool = False,
) -> dict[str, Any]:
    use_snapshot = page == 1 and limit == IWENCAI_DRAGON_TIGER_DEFAULT_LIMIT
    previous_snapshot = None
    if use_snapshot:
        previous_snapshot = read_dragon_tiger_snapshot(
            IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE,
        )
        exact_latest = read_dragon_tiger_snapshot(
            IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE,
            trade_date=trade_date,
        )
        if exact_latest:
            exact_latest["stale"] = False
            exact_latest["scheduled_refresh_time"] = "18:00"
            return exact_latest
        if allow_latest_snapshot:
            latest = _iwencai_dragon_tiger_latest_snapshot(trade_date)
            if latest:
                return latest

    payload = fetch_dragon_tiger(trade_date, page=page, limit=limit)
    payload["scheduled_refresh_time"] = "18:00"
    if use_snapshot and fallback_to_latest_on_empty and not payload.get("items"):
        latest = _iwencai_dragon_tiger_latest_snapshot(trade_date)
        if latest:
            return latest
    if use_snapshot and allow_latest_snapshot:
        if payload.get("available") is True and payload.get("items"):
            calendar = trading_day_status(trade_date, allow_refresh=False)
            payload = mark_consecutive_dragon_tiger_items(
                payload,
                previous_snapshot,
                previous_trading_day=str(calendar.get("previous_trading_day") or ""),
            )
            payload = enrich_consecutive_dragon_tiger_news(
                payload,
                previous_snapshot=previous_snapshot,
            )
        if write_dragon_tiger_snapshot(
            IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE,
            payload,
        ):
            payload["snapshot_saved"] = True
            try:
                payload["expired_archive_count"] = expire_dragon_tiger_archives(
                    iwencai_dragon_tiger_archive_dir()
                )
            except OSError as exc:
                payload["archive_cleanup_error"] = type(exc).__name__
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
    current = current_cn_datetime()
    money_flow, _samples = fetch_and_record_money_flow(timeout=120, now=current)
    if money_flow.get("inflow") or money_flow.get("outflow"):
        return _money_flow_with_display_period(money_flow, now=current)
    previous = load_previous_money_flow_snapshot(now=current)
    if previous is not None and money_flow.get("error"):
        previous["error"] = str(money_flow["error"])
    return previous or money_flow


def produce_industry_flow_data() -> dict[str, Any]:
    current = current_cn_datetime()
    current_day = market_retention_date_key(current)
    reset_daily_market_histories(current)
    money_flow = cached_json_data(
        "money_flow",
        API_TTLS["money_flow"],
        produce_money_flow_data,
        {"inflow": [], "outflow": []},
        cacheable=lambda payload: bool(
            payload.get("inflow") or payload.get("outflow")
        ),
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


def auto_version_check_enabled(env_values: dict[str, str] | None = None) -> bool:
    values = env_values if env_values is not None else parse_env_file()
    raw = (
        os.environ.get("DASHBOARD_AUTO_VERSION_CHECK_ENABLED")
        if "DASHBOARD_AUTO_VERSION_CHECK_ENABLED" in os.environ
        else values.get("DASHBOARD_AUTO_VERSION_CHECK_ENABLED", "1")
    )
    return str(raw).strip().lower() in TRUTHY_VALUES


def newsnow_config(env_values: dict[str, str] | None = None) -> NewsNowConfig:
    """Resolve the deployment-managed endpoint before explicit process overrides."""

    values = dict(env_values if env_values is not None else parse_env_file())
    bundled_endpoint = str(os.environ.get("NIUONE_BUNDLED_NEWSNOW_URL") or "").strip()
    if bundled_endpoint:
        values["NEWSNOW_BASE_URL"] = bundled_endpoint
    for name in NEWSNOW_CONFIG_NAMES:
        if name in os.environ:
            if name == "NEWSNOW_BASE_URL" and bundled_endpoint and not str(os.environ[name]).strip():
                continue
            values[name] = os.environ[name]
    return NewsNowConfig.from_env(values)


def newsnow_overview_important_only(env_values: dict[str, str] | None = None) -> bool:
    """Return whether the compact overview feed should exclude ordinary items."""

    values = env_values if env_values is not None else parse_env_file()
    raw = (
        os.environ.get("NEWSNOW_OVERVIEW_IMPORTANT_ONLY")
        if "NEWSNOW_OVERVIEW_IMPORTANT_ONLY" in os.environ
        else values.get("NEWSNOW_OVERVIEW_IMPORTANT_ONLY", "1")
    )
    return str(raw).strip().lower() in TRUTHY_VALUES


def realtime_news_service() -> NewsNowService:
    """Return the process-local service guarding the persistent news cache."""

    global NEWSNOW_SERVICE
    if NEWSNOW_SERVICE is not None:
        return NEWSNOW_SERVICE
    with NEWSNOW_SERVICE_LOCK:
        if NEWSNOW_SERVICE is None:
            NEWSNOW_SERVICE = shared_newsnow_service(NEWSNOW_CACHE_FILE)
        return NEWSNOW_SERVICE


def produce_realtime_news_data() -> dict[str, Any]:
    """Build the public realtime-news read model without exposing its endpoint."""

    overview_important_only = newsnow_overview_important_only()
    try:
        config = newsnow_config()
    except NewsNowConfigurationError as exc:
        now = datetime.now(CN_TZ)
        return {
            "schema_version": 1,
            "enabled": True,
            "available": False,
            "status": "invalid_configuration",
            "stale": False,
            "source": "NewsNow",
            "generated_at": now.isoformat(timespec="seconds"),
            "attempted_at_ms": int(now.timestamp() * 1000),
            "successful_source_count": 0,
            "source_ids": [],
            "sources": [],
            "items": [],
            "overview_important_only": overview_important_only,
            "error": exc.code,
        }
    payload = realtime_news_service().get_news(config)
    public_payload = dict(payload)
    public_payload.pop("config_fingerprint", None)
    public_payload["overview_important_only"] = overview_important_only
    return public_payload


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
    if kind == "reasoning_effort":
        return normalize_reasoning_effort(value)
    if kind == "stream_mode":
        return normalize_model_stream_mode(value)
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
    if kind == "news_sources":
        return ",".join(parse_newsnow_source_ids(value))
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
        if value == "" and name not in existing and kind not in {
            "time_list",
            "news_sources",
            "stock_universe",
            "strategy_multi",
            "strategy_single",
        }:
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
    "DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON",
    "DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON",
    "DASHBOARD_NIUONE_FORWARD_CRON",
}
CRON_TIME_CONFIGS = {
    "IWENCAI_DRAGON_TIGER_CRON": {"day_label": "A股交易日"},
    "DASHBOARD_US_MARKET_SUMMARY_CRON": {"day_label": "A股交易日"},
    "DASHBOARD_MARKET_AUCTION_CRON": {"day_label": "周一至周五"},
    "DASHBOARD_MARKET_MIDDAY_CRON": {"day_label": "周一至周五"},
    "DASHBOARD_MARKET_CLOSE_CRON": {"day_label": "周一至周五"},
    "DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON": {"day_label": "A股交易日"},
    "DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON": {"day_label": "A股交易日"},
    "DASHBOARD_NIUONE_FORWARD_CRON": {"day_label": "A股交易日"},
}
ADMIN_GROUP_NOTES = {
    "财经快讯": "通过 NewsNow 聚合财联社电报、金十数据和华尔街见闻快讯。可选择是否将重要快讯写入买卖决策证据；交易日 15:00 后及休市日信息归入下一交易日。无需 API Key 或服务地址配置；Compose 部署会随牛牛1号自动启动内置实例，来源抓取失败时继续展示最近一次成功缓存并标记陈旧。",
    "问财数据源": "统一管理龙虎榜与可选消息面预检。问财官方公告、新闻和事件技能负责检索；最近 3 天证据经身份校验和去重后，由“买卖决策模型”判断利好、利空或中性。无有效证据直接记为中性；模型失败时标记判断不可用，不回退关键词规则。",
    "模型配置": "买卖决策、文字策略 AI 细化、问财消息判断、A 股盘面总结和隔夜美股总结共用这一套 OpenAI 兼容模型。推荐使用 deepseek-v4-pro；已知 Qwen Responses 型号会在 auto 逻辑下自动选择 Responses API。长度默认：上下文 128000 tokens，最大输出 4096 tokens。",
    "交易规则与风控": "约束买卖决策必须遵守的交易纪律、持仓数量、仓位比例、现金缓冲与盘面控仓规则。交易纪律 Prompt 会直接写入决策模型的必须遵守段。",
    "交易通知": "模拟买入或卖出成交落盘后推送。从下拉框按需添加渠道并分块配置；每个渠道可独立启用或关闭，关闭会保留配置，移除并保存后才会清除配置。Webhook、Bot Token 和签名密钥只保存、不回显。",
    "选股与买卖设置": "配置选股范围、候选数量和北京时间交易时点；板块分类固定使用东方财富概念与行业。",
    "综合决策参考": "为买卖决策汇总指数、板块、资金流向、热门股票等参考数据。缓存秒数控制数据复用周期，单类参考数据上限可设置为 1～8。",
    "选股与交易策略": "选择一套独立策略；基础策略、Z哥、李大霄、板块潮汐、牛牛战法和预设文字策略的候选、买入、卖出、仓位与 Prompt 规则互不混用。",
    "盘面监控生产时间点": "直接填写北京时间 HH:MM；隔夜美股总结默认交易日 08:00 生成，并与 A 股竞价、午盘、盘后总结共用“模型配置”栏目中的模型、地址和密钥。",
    "行情与资金流设置": "统一管理公开快照、指数刷新和行业资金流动画。播放速度、每侧行业数量、采样间隔及上午/下午采样窗口均支持运行时保存后生效；时间使用北京时间 HH:MM，默认 09:25～11:31、13:00～15:01。",
    "关于": "查看项目作者、源代码仓库、开源许可和版本信息，并控制首页是否在打开或重新加载时自动检测新版本。",
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
        "slug": "realtime-news",
        "name": "财经快讯",
        "summary": "配置财联社/金十来源、超时、重试与刷新频率；新闻服务自动管理。",
        "icon": "新闻",
    },
    {
        "slug": "model-config",
        "name": "模型配置",
        "summary": "配置买卖决策与盘面总结共用的模型和 API。",
        "icon": "模型",
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
    {
        "slug": "about",
        "name": "关于",
        "summary": "查看作者、代码仓库、开源许可和版本信息。",
        "icon": "关于",
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
    {
        "id": "serverchan",
        "label": "Server酱",
        "description": "Server酱 SendKey，推送消息到微信。",
        "enabled_name": "DASHBOARD_SERVERCHAN_NOTIFICATION_ENABLED",
        "field_names": ("DASHBOARD_SERVERCHAN_SENDKEY",),
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


def friendly_newsnow_sources_text(value: str) -> str:
    try:
        source_ids = parse_newsnow_source_ids(value)
    except ValueError:
        return str(value or "")
    return "、".join(
        str(NEWSNOW_SUPPORTED_SOURCES[source_id]["label"])
        for source_id in source_ids
    )


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
        elif name == "NEWSNOW_BASE_URL":
            value = str(normalized[name] or "").strip()
            normalized[name] = normalize_newsnow_endpoint(value) if value else ""
        elif name == "NEWSNOW_SOURCES":
            normalized[name] = ",".join(parse_newsnow_source_ids(normalized[name]))
        elif name == "IWENCAI_BASE_URL":
            normalized[name] = normalize_iwencai_base_url(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "time_list":
            normalized[name] = normalize_time_list_update(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "time":
            normalized[name] = normalize_env_update(name, normalized[name], "time")
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
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "stream_mode":
            normalized[name] = normalize_model_stream_mode(normalized[name])
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "reasoning_effort":
            normalized[name] = normalize_reasoning_effort(normalized[name])
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
        elif name == "NEWSNOW_BASE_URL":
            if str(value or "").strip():
                normalize_newsnow_endpoint(value)
        elif name == "NEWSNOW_SOURCES":
            parse_newsnow_source_ids(value)
        elif name in {
            "NEWSNOW_MAX_ITEMS",
            "NEWSNOW_MAX_IMPORTANT_ITEMS",
            "NEWSNOW_REFRESH_SECONDS",
            "NEWSNOW_TIMEOUT_SECONDS",
            "NEWSNOW_MAX_RETRIES",
            "NEWSNOW_MAX_CONCURRENCY",
        } and str(value or "").strip():
            number = int(value)
            minimum, maximum = {
                "NEWSNOW_MAX_ITEMS": (NEWSNOW_MAX_ITEMS_MIN, NEWSNOW_MAX_ITEMS_MAX),
                "NEWSNOW_MAX_IMPORTANT_ITEMS": (
                    NEWSNOW_MAX_IMPORTANT_ITEMS_MIN,
                    NEWSNOW_MAX_IMPORTANT_ITEMS_MAX,
                ),
                "NEWSNOW_REFRESH_SECONDS": (15, 1800),
                "NEWSNOW_TIMEOUT_SECONDS": (2, 30),
                "NEWSNOW_MAX_RETRIES": (0, 2),
                "NEWSNOW_MAX_CONCURRENCY": (1, 3),
            }[name]
            if number < minimum or number > maximum:
                raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
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
        elif name == PRACTICE_SCHEDULE_TIMES_ENV:
            normalize_time_list_update(value)
        elif name == NIUONE_FORWARD_COHORT_START_ENV:
            raw_date = str(value or "").strip()
            try:
                parsed_date = date.fromisoformat(raw_date)
            except ValueError:
                raise ValueError(
                    f"{name} 必须使用 YYYY-MM-DD，例如 2026-08-03"
                ) from None
            if parsed_date.isoformat() != raw_date:
                raise ValueError(
                    f"{name} 必须使用 YYYY-MM-DD，例如 2026-08-03"
                )
        elif name in {
            "DASHBOARD_B3_EXIT_TIME",
            "DASHBOARD_TIME_EXIT_TIME",
            "DASHBOARD_TIME_STOP_EXIT_TIME",
            "DASHBOARD_KLINE_PREWARM_TIME",
            *INDUSTRY_FLOW_WINDOW_CONFIG_NAMES,
        }:
            normalize_env_update(name, value, "time")
        elif name in {
            "DASHBOARD_B1_SCAN_TIMEOUT_SECONDS",
            "DASHBOARD_B1_SCAN_WORKERS",
            "DASHBOARD_TENCENT_QUOTE_STAGE_TIMEOUT_SECONDS",
            "DASHBOARD_KLINE_PREWARM_WORKERS",
            "DASHBOARD_KLINE_PREWARM_TIMEOUT_SECONDS",
            "DASHBOARD_KLINE_PREWARM_CATCHUP_MINUTES",
            "DASHBOARD_KLINE_BOOTSTRAP_MAX_ATTEMPTS",
            "DASHBOARD_KLINE_READINESS_MIN_COVERAGE_PERCENT",
            "DASHBOARD_MANUAL_DATA_INITIALIZATION_TIMEOUT_SECONDS",
        } and str(value or "").strip():
            number = int(value)
            minimum, maximum = {
                "DASHBOARD_B1_SCAN_TIMEOUT_SECONDS": (60, 1800),
                "DASHBOARD_B1_SCAN_WORKERS": (1, 16),
                "DASHBOARD_TENCENT_QUOTE_STAGE_TIMEOUT_SECONDS": (15, 300),
                "DASHBOARD_KLINE_PREWARM_WORKERS": (1, 16),
                "DASHBOARD_KLINE_PREWARM_TIMEOUT_SECONDS": (60, 1800),
                "DASHBOARD_KLINE_PREWARM_CATCHUP_MINUTES": (0, 120),
                "DASHBOARD_KLINE_BOOTSTRAP_MAX_ATTEMPTS": (1, 12),
                "DASHBOARD_KLINE_READINESS_MIN_COVERAGE_PERCENT": (90, 100),
                "DASHBOARD_MANUAL_DATA_INITIALIZATION_TIMEOUT_SECONDS": (60, 3600),
            }[name]
            if number < minimum or number > maximum:
                raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
        elif name == PRACTICE_FAST_CYCLE_INTERVAL_ENV and str(value or "").strip():
            number = int(value)
            if (
                number < MIN_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS
                or number > MAX_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS
            ):
                raise ValueError(
                    f"{name} 必须在 {MIN_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS} 到 "
                    f"{MAX_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS} 之间"
                )
        elif (
            name == "DASHBOARD_MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS"
            and str(value or "").strip()
        ):
            number = int(value)
            if number < 30 or number > 600:
                raise ValueError(f"{name} 必须在 30 到 600 之间")
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
            "DASHBOARD_INDICES_TTL_SECONDS",
            "DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS",
            "DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS",
            "DASHBOARD_MAX_OPEN_POSITIONS",
            "DASHBOARD_MORNING_MAX_OPEN_POSITIONS",
            "DASHBOARD_DISPLAY_CANDIDATE_LIMIT",
            "DASHBOARD_TRADE_CANDIDATE_LIMIT",
            "DASHBOARD_PRESET_STRATEGY_CANDIDATE_LIMIT",
        } and str(value or "").strip():
            number = int(value)
            if number <= 0:
                raise ValueError(f"{name} 必须大于 0")
            if name == "DASHBOARD_PRESET_STRATEGY_CANDIDATE_LIMIT" and not 10 <= number <= 100:
                raise ValueError("文字策略中性候选数量必须在 10 到 100 之间")
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
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") in {"max_tokens", "context_length"}:
            normalize_context_length_update(value)
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "reasoning_effort":
            normalize_reasoning_effort(value)
        elif ENV_CONFIG_BY_NAME.get(name, {}).get("kind") == "stream_mode":
            normalize_model_stream_mode(value)
    _validate_reasoning_effort_updates(updates)
    if set(updates) & set(INDUSTRY_FLOW_WINDOW_CONFIG_NAMES):
        _industry_flow_sampling_windows_value(
            updates,
            fallback=INDUSTRY_FLOW_SAMPLING_WINDOWS,
            strict=True,
        )
    newsnow_limit_names = {"NEWSNOW_MAX_ITEMS", "NEWSNOW_MAX_IMPORTANT_ITEMS"}
    if set(updates) & newsnow_limit_names:
        current = parse_env_file()
        max_items = int(
            updates.get("NEWSNOW_MAX_ITEMS")
            or current.get("NEWSNOW_MAX_ITEMS")
            or DEFAULT_NEWSNOW_MAX_ITEMS
        )
        max_important_items = int(
            updates.get("NEWSNOW_MAX_IMPORTANT_ITEMS")
            or current.get("NEWSNOW_MAX_IMPORTANT_ITEMS")
            or DEFAULT_NEWSNOW_MAX_IMPORTANT_ITEMS
        )
        if max_important_items > max_items:
            raise ValueError("NEWSNOW_MAX_IMPORTANT_ITEMS 不能大于 NEWSNOW_MAX_ITEMS")


def _validate_reasoning_effort_updates(updates: dict[str, str]) -> None:
    """Validate known model/effort combinations without restricting aliases."""

    touched_names = set(updates)
    relevant = [
        (effort_name, model_names)
        for effort_name, model_names in REASONING_EFFORT_MODEL_NAMES.items()
        if touched_names & {effort_name, *model_names}
    ]
    if not relevant:
        return

    saved = parse_env_file()

    def configured_value(name: str) -> str:
        if name in updates:
            return str(updates[name] or "").strip()
        if name in os.environ:
            return str(os.environ.get(name) or "").strip()
        if name in saved:
            return str(saved.get(name) or "").strip()
        return str(ENV_CONFIG_BY_NAME.get(name, {}).get("default") or "").strip()

    for effort_name, model_names in relevant:
        effort = configured_value(effort_name)
        model = next(
            (value for name in model_names if (value := configured_value(name))),
            "",
        )
        resolve_model_reasoning_effort(model, effort)


def sync_business_runtime_settings(
    changed: dict[str, str] | list[str] | set[str] | tuple[str, ...] | None,
    *,
    sync_names: list[str] | set[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    global ADMIN_PASSWORD, B1_CANDIDATE_REFRESH_LAST_TS, PRACTICE_SCHEDULE_TIMES
    global PRACTICE_FAST_CYCLE_ENABLED, PRACTICE_FAST_CYCLE_INTERVAL_SECONDS
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
    syncable_names = set(visible_names) | set(LEGACY_SUMMARY_MODEL_ENV_NAMES)
    for name in syncable_names:
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
    if PRACTICE_SCHEDULE_TIMES_ENV in changed_names:
        PRACTICE_SCHEDULE_TIMES = tuple(
            split_hhmm_values(env_values.get(PRACTICE_SCHEDULE_TIMES_ENV, ""))
        )
        applied.append("practice_schedule_times")
        start_b1_scheduler()
    fast_cycle_names = {
        PRACTICE_FAST_CYCLE_ENABLED_ENV,
        PRACTICE_FAST_CYCLE_INTERVAL_ENV,
    }
    if changed_names & fast_cycle_names:
        PRACTICE_FAST_CYCLE_ENABLED = str(
            env_values.get(PRACTICE_FAST_CYCLE_ENABLED_ENV, "0") or "0"
        ).strip().lower() not in {"0", "false", "no", "off"}
        PRACTICE_FAST_CYCLE_INTERVAL_SECONDS = _bounded_int_value(
            env_values.get(PRACTICE_FAST_CYCLE_INTERVAL_ENV),
            DEFAULT_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS,
            MIN_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS,
            MAX_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS,
        )
        applied.append("practice_fast_cycle")
        start_practice_fast_cycle()

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

    newsnow_names = {
        "NEWSNOW_ENABLED",
        "NEWSNOW_DECISION_ENABLED",
        "NEWSNOW_OVERVIEW_IMPORTANT_ONLY",
        "NEWSNOW_BASE_URL",
        "NEWSNOW_SOURCES",
        "NEWSNOW_MAX_ITEMS",
        "NEWSNOW_MAX_IMPORTANT_ITEMS",
        "NEWSNOW_REFRESH_SECONDS",
        "NEWSNOW_TIMEOUT_SECONDS",
        "NEWSNOW_MAX_RETRIES",
        "NEWSNOW_MAX_CONCURRENCY",
    }
    if changed_names & newsnow_names:
        invalidate_api_cache("realtime_news:v1")
        applied.append("realtime_news")

    iwencai_names = {
        "IWENCAI_ENABLED",
        "IWENCAI_NEWS_PRECHECK_ENABLED",
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
        "DASHBOARD_PRESET_STRATEGY_CANDIDATE_LIMIT",
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

    migrated_updates = dict(updates)
    migrated_clear_names = set(clear_names or set())
    if PRACTICE_SCHEDULE_TIMES_ENV in updates:
        migrated_clear_names.add(LEGACY_B1_SCHEDULE_TIMES_ENV)
    with ENV_FILE_WRITE_LOCK:
        existing = parse_env_file(include_container_overrides=False)
        if set(migrated_updates) & set(SHARED_MODEL_ENV_NAMES):
            for name, value in legacy_summary_migration_values(existing).items():
                if not str(migrated_updates.get(name) or existing.get(name) or "").strip():
                    migrated_updates[name] = value
            prospective = dict(existing)
            prospective.update(
                (name, value)
                for name, value in migrated_updates.items()
                if str(value or "").strip()
            )
            if (
                str(prospective.get(SHARED_MODEL_NAMES["base_url"]) or "").strip()
                and str(prospective.get(SHARED_MODEL_NAMES["api_key"]) or "").strip()
            ):
                migrated_clear_names.update(LEGACY_SUMMARY_MODEL_ENV_NAMES)
        result = _write_env_file_values_unlocked(
            migrated_updates,
            clear_names=migrated_clear_names,
        )
        sync_names = set(migrated_updates) | migrated_clear_names
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

    return {
        "shared-model": crossdesk,
        "decision-model": crossdesk,
        "a-share-summary-model": crossdesk,
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


def prompt_strategy_store() -> PromptStrategyStore:
    return PromptStrategyStore()


def build_prompt_strategy_admin_payload() -> dict[str, Any]:
    store = prompt_strategy_store()
    return {
        "active_version": store.active_version(),
        "runtime_enabled": active_strategy_suite() == STRATEGY_SOURCE_PRESET_TEXT,
        "versions": store.list_versions(limit=50),
        "drafts": store.list_drafts(limit=50),
        "capabilities": DEFAULT_FEATURE_REGISTRY.capability_catalog(),
    }


def create_prompt_strategy_draft(raw_prompt: str) -> dict[str, Any]:
    return prompt_strategy_store().create_draft(raw_prompt)


def _prompt_refinement_config() -> ResolvedModelTestConfig:
    settings, fallback = model_test_settings_snapshot("shared-model")
    config = resolve_model_test_config(
        "shared-model",
        settings,
        provider_fallback=fallback,
    )
    missing = []
    if not config.model:
        missing.append("模型")
    if not config.base_url:
        missing.append("API 地址")
    if not config.api_key:
        missing.append("API Key")
    if missing:
        raise ValueError("请先配置共享模型的" + "、".join(missing))
    return config


def _prompt_refinement_timeout_seconds() -> int:
    """Use the decision model's existing timeout instead of a parallel setting."""

    return _bounded_int_value(
        os.environ.get("DASHBOARD_DECISION_TIMEOUT", "180"),
        180,
        10,
        1800,
    )


class PromptRefinementStreamError(RuntimeError):
    """Safe, classified upstream failure for one refinement stream attempt."""

    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = str(code or "stream_failed")
        self.retryable = bool(retryable)


def _classify_prompt_refinement_stream_error(
    exc: Exception,
) -> PromptRefinementStreamError:
    if isinstance(exc, urllib.error.HTTPError):
        status = int(exc.code)
        retryable = status in {408, 409, 425, 429} or 500 <= status <= 599
        message = (
            f"文字策略模型服务暂时不可用（HTTP {status}）"
            if retryable
            else f"文字策略模型请求被拒绝（HTTP {status}）"
        )
        return PromptRefinementStreamError(
            message,
            code=f"http_{status}",
            retryable=retryable,
        )
    if isinstance(exc, TimeoutError):
        return PromptRefinementStreamError(
            "文字策略模型响应超时",
            code="timeout",
            retryable=True,
        )
    if isinstance(exc, ValueError):
        if str(exc) == "模型未返回可用文字策略":
            return PromptRefinementStreamError(
                "文字策略模型没有返回可用文本",
                code="empty_response",
                retryable=True,
            )
        return PromptRefinementStreamError(
            "文字策略模型流式连接在输出完成前中断",
            code="stream_interrupted",
            retryable=True,
        )
    if isinstance(exc, (OSError, urllib.error.URLError)):
        return PromptRefinementStreamError(
            "文字策略模型连接中断",
            code="connection_interrupted",
            retryable=True,
        )
    return PromptRefinementStreamError(
        f"文字策略模型细化失败（{type(exc).__name__}）",
        code="unexpected_stream_error",
        retryable=False,
    )


def _stream_prompt_refinement(messages: list[dict[str, str]]) -> Iterator[str]:
    if not PROMPT_REFINEMENT_SEMAPHORE.acquire(blocking=False):
        raise RuntimeError("当前有文字策略正在细化，请稍后重试")
    try:
        config = _prompt_refinement_config()
        try:
            yielded = False
            # Prompt refinement is an interactive browser flow, so ``auto``
            # keeps the historical incremental output.  Users can still force
            # a complete response when their gateway does not support SSE.
            if config.stream_mode != "non_stream":
                request = build_model_request(
                    config.base_url,
                    config.model,
                    messages,
                    max_tokens=7000,
                    api_mode=config.api_mode,
                    reasoning_effort=config.reasoning_effort,
                    stream=True,
                    extra_payload={"stream": True},
                )
                contents: Iterable[str] = stream_model_response(
                    request,
                    config.api_key,
                    timeout=_prompt_refinement_timeout_seconds(),
                )
            else:
                request = build_model_request(
                    config.base_url,
                    config.model,
                    messages,
                    max_tokens=7000,
                    api_mode=config.api_mode,
                    reasoning_effort=config.reasoning_effort,
                    stream=False,
                    extra_payload={"stream": False},
                )
                parsed = request_model_complete(
                    request,
                    config.api_key,
                    timeout=_prompt_refinement_timeout_seconds(),
                    stream_mode=config.stream_mode,
                )
                contents = (parsed.content,)
            for content in contents:
                text = str(content or "")
                if not text:
                    continue
                yielded = True
                yield text
            if not yielded:
                raise ValueError("模型未返回可用文字策略")
        except Exception as exc:
            raise _classify_prompt_refinement_stream_error(exc) from exc
    finally:
        PROMPT_REFINEMENT_SEMAPHORE.release()


def _complete_prompt_refinement(messages: list[dict[str, str]]) -> str:
    """Collect one complete answer as a fallback for a broken browser stream."""

    if not PROMPT_REFINEMENT_SEMAPHORE.acquire(blocking=False):
        raise RuntimeError("当前有文字策略正在细化，请稍后重试")
    try:
        config = _prompt_refinement_config()
        try:
            request = build_model_request(
                config.base_url,
                config.model,
                messages,
                max_tokens=7000,
                api_mode=config.api_mode,
                reasoning_effort=config.reasoning_effort,
                stream=False,
                extra_payload={"stream": False},
            )
            parsed = request_model_complete(
                request,
                config.api_key,
                timeout=_prompt_refinement_timeout_seconds(),
                stream_mode=config.stream_mode,
            )
            content = str(parsed.content or "").strip()
            if not content:
                raise ValueError("模型未返回可用文字策略")
            return content
        except Exception as exc:
            raise _classify_prompt_refinement_stream_error(exc) from exc
    finally:
        PROMPT_REFINEMENT_SEMAPHORE.release()


def _request_prompt_refinement(messages: list[dict[str, str]]) -> str:
    last_error: Exception | None = None
    for attempt in range(PROMPT_REFINEMENT_MAX_ATTEMPTS):
        try:
            response = (
                "".join(_stream_prompt_refinement(messages)).strip()
                if attempt == 0
                else _complete_prompt_refinement(messages)
            )
            if response:
                return response
        except PromptRefinementStreamError as exc:
            last_error = exc
            if not exc.retryable or attempt + 1 >= PROMPT_REFINEMENT_MAX_ATTEMPTS:
                break
    if last_error is not None:
        raise RuntimeError(f"{last_error}；已自动重试一次") from last_error
    raise RuntimeError("文字策略模型没有返回可用文本；已自动重试一次")


def _prompt_refinement_identity(*, injected: bool) -> tuple[str, str]:
    if injected:
        return "injected-requester", "test"
    return _prompt_refinement_config().model, "shared-model"


def _prompt_refinement_stream_event(event: str, payload: Mapping[str, Any]) -> str:
    data = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


def _prompt_refinement_public_error(exc: Exception) -> str:
    if isinstance(exc, PromptRefinementParseError):
        return str(exc).strip()
    if isinstance(exc, TimeoutError):
        return "文字策略模型响应超时，请重试"
    if isinstance(exc, (ValueError, RuntimeError)) and str(exc).strip():
        return str(exc).strip()
    return f"文字策略细化失败（{type(exc).__name__}）"


def stream_refine_prompt_strategy_draft(
    draft_id: str,
    *,
    requester=None,
) -> Iterator[str]:
    """Stream one model refinement, then compile and persist the complete output."""

    store = prompt_strategy_store()
    claimed = False
    try:
        draft = store.claim_refinement(draft_id)
        claimed = True
        messages = build_refinement_messages(str(draft.get("raw_prompt") or ""))
        yield _prompt_refinement_stream_event(
            "started",
            {"draft_id": str(draft.get("draft_id") or draft_id)},
        )
        stream_request = requester or _stream_prompt_refinement
        max_attempts = PROMPT_REFINEMENT_MAX_ATTEMPTS if requester is None else 1
        result = None
        last_error: Exception | None = None
        attempt_messages = messages
        for attempt in range(max_attempts):
            if attempt:
                retry_message = (
                    "模型上一次遗漏了可执行条件，正在要求模型完整重写一次…"
                    if isinstance(last_error, PromptRefinementCoverageError)
                    else "模型上一次输出的结构不符合规则，正在要求模型修正一次…"
                    if isinstance(last_error, PromptRefinementContractError)
                    else "模型流式输出未完整结束，正在自动重试一次…"
                )
                yield _prompt_refinement_stream_event(
                    "reset",
                    {
                        "attempt": attempt + 1,
                        "message": retry_message,
                    },
                )
            parts: list[str] = []
            try:
                use_complete_fallback = (
                    requester is None
                    and attempt > 0
                    and not isinstance(
                        last_error,
                        (
                            PromptRefinementCoverageError,
                            PromptRefinementContractError,
                        ),
                    )
                )
                contents = (
                    (_complete_prompt_refinement(attempt_messages),)
                    if use_complete_fallback
                    else stream_request(attempt_messages)
                )
                for content in contents:
                    text = str(content or "")
                    if not text:
                        continue
                    parts.append(text)
                    yield _prompt_refinement_stream_event("delta", {"text": text})
                complete_response = "".join(parts).strip()
                if not complete_response:
                    raise PromptRefinementStreamError(
                        "文字策略模型没有返回可用文本",
                        code="empty_response",
                        retryable=True,
                    )
                candidate_result = finalize_prompt_refinement(
                    attempt_messages,
                    complete_response,
                )
                try:
                    compile_strategy_spec(candidate_result.refined_spec)
                except ValueError as exc:
                    if attempt + 1 < max_attempts:
                        validation_errors = list(
                            getattr(exc, "errors", ()) or (str(exc),)
                        )
                        raise PromptRefinementContractError(
                            "模型结构化规则未通过本地编译："
                            + "；".join(validation_errors)[:1200]
                        ) from exc
                result = candidate_result
                break
            except Exception as exc:
                last_error = exc
                retryable = (
                    isinstance(
                        exc,
                        (
                            PromptRefinementParseError,
                            PromptRefinementCoverageError,
                            PromptRefinementContractError,
                        ),
                    )
                    or (
                        isinstance(exc, PromptRefinementStreamError)
                        and exc.retryable
                    )
                )
                if (
                    isinstance(
                        exc,
                        (
                            PromptRefinementCoverageError,
                            PromptRefinementContractError,
                        ),
                    )
                    and attempt + 1 < max_attempts
                ):
                    correction = (
                        "上一次结果遗漏了 capability_catalog 已支持的明确条件。"
                        if isinstance(exc, PromptRefinementCoverageError)
                        else "上一次结果未通过本地结构校验。"
                    )
                    attempt_messages = [
                        *messages,
                        {
                            "role": "user",
                            "content": (
                                correction
                                + "请重新输出一个完整 JSON；selection 与 entry 必须保留"
                                "用户要求的全部今日/昨日 OHLCV 比较，使用 market.value "
                                "和 offset_bars=0/1，且 position 必须使用 type/value/allow_add 格式。"
                                "上一次本地校验结果："
                                + str(exc)[:1200]
                            ),
                        },
                    ]
                if retryable and attempt + 1 >= max_attempts and max_attempts > 1:
                    raise RuntimeError(
                        f"{_prompt_refinement_public_error(exc)}；自动重试后仍失败"
                    ) from exc
                if not retryable or attempt + 1 >= max_attempts:
                    raise
        if result is None:
            raise last_error or RuntimeError("文字策略模型细化未完成")
        model, provider = _prompt_refinement_identity(injected=requester is not None)
        saved = store.save_refinement(
            draft_id,
            result.refined_spec,
            model=model,
            provider=provider,
            refinement_prompt_sha256=result.refinement_prompt_sha256,
        )
        claimed = False
        yield _prompt_refinement_stream_event("complete", {"draft": saved})
    except Exception as exc:
        if claimed:
            store.release_refinement_claim(draft_id)
            claimed = False
        yield _prompt_refinement_stream_event(
            "error",
            {"error": _prompt_refinement_public_error(exc)},
        )
    finally:
        if claimed:
            store.release_refinement_claim(draft_id)


def refine_prompt_strategy_draft(
    draft_id: str,
    *,
    requester=None,
) -> dict[str, Any]:
    store = prompt_strategy_store()
    draft = store.claim_refinement(draft_id)
    request_func = requester or _request_prompt_refinement
    try:
        result = refine_prompt_once(
            str(draft.get("raw_prompt") or ""),
            request_func,
        )
        model, provider = _prompt_refinement_identity(injected=requester is not None)
        return store.save_refinement(
            draft_id,
            result.refined_spec,
            model=model,
            provider=provider,
            refinement_prompt_sha256=result.refinement_prompt_sha256,
        )
    except Exception:
        store.release_refinement_claim(draft_id)
        raise


def activate_prompt_strategy_draft(
    draft_id: str,
    *,
    confirmed_plan_sha256: str,
) -> dict[str, Any]:
    store = prompt_strategy_store()
    draft = store.get_draft(draft_id)
    if draft is None:
        raise ValueError("文字策略草案不存在")
    expected = str(draft.get("plan_sha256") or "")
    if not expected or str(confirmed_plan_sha256 or "") != expected:
        raise ValueError("确认的文字策略计划指纹与待激活版本不一致")
    if str(draft.get("status") or "") == "activated":
        existing = store.get_version(str(draft.get("activated_version_id") or ""))
        if existing is not None and str(existing.get("status") or "") == "active":
            return {
                **existing,
                "runtime_activation": {
                    "ok": True,
                    "changed": False,
                    "idempotent": True,
                },
            }
        raise ValueError("该文字策略草案已激活且对应版本已退休")
    previous_suite = active_strategy_suite()
    prepared = store.prepare_activation(draft_id)
    version_id = str(prepared.get("version_id") or "")
    try:
        runtime = persist_and_sync_business_updates({
            ACTIVE_STRATEGY_ENV: STRATEGY_SOURCE_PRESET_TEXT,
        })
    except Exception as exc:
        store.fail_activation(version_id)
        raise RuntimeError(
            f"文字策略运行配置写入失败：{type(exc).__name__}"
        ) from exc
    try:
        version = store.commit_activation(version_id)
    except Exception as activation_exc:
        store.fail_activation(version_id)
        try:
            persist_and_sync_business_updates({ACTIVE_STRATEGY_ENV: previous_suite})
        except Exception as rollback_exc:
            raise RuntimeError(
                "文字策略版本提交失败，且运行策略配置回滚失败："
                f"{type(rollback_exc).__name__}"
            ) from activation_exc
        raise RuntimeError(
            f"文字策略版本提交失败：{type(activation_exc).__name__}"
        ) from activation_exc
    return {**version, "runtime_activation": runtime}


def prompt_strategy_evaluations(
    version_id: str,
    *,
    code: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    version = prompt_strategy_store().get_version(version_id)
    if version is None:
        raise ValueError("文字策略版本不存在")
    return prompt_strategy_store().list_evaluations(
        version_id,
        code=code,
        limit=limit,
    )


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
    shared_model: Any | None = None,
) -> tuple[str, str]:
    if name in SHARED_MODEL_ENV_NAMES and shared_model is not None:
        field = next(
            (key for key, configured_name in SHARED_MODEL_NAMES.items() if configured_name == name),
            "",
        )
        value = str(getattr(shared_model, field, "") or "")
        if getattr(shared_model, "source", "") == "legacy_summary":
            return value, "legacy summary settings"
        if getattr(shared_model, "source", "") == "provider" and field in {"base_url", "api_key"}:
            return value, "config.yaml"
    if name == "DASHBOARD_DECISION_BASE_URL":
        provider = crossdesk_provider if crossdesk_provider is not None else crossdesk_provider_values()
        return provider.get("base_url", ""), "config.yaml" if provider.get("base_url") else "default"
    if name == "DASHBOARD_DECISION_API_KEY":
        provider = crossdesk_provider if crossdesk_provider is not None else crossdesk_provider_values()
        return provider.get("api_key", ""), "config.yaml" if provider.get("api_key") else "default"
    return "", "default"


def build_admin_config_payload() -> dict[str, Any]:
    env_values = parse_env_file()
    crossdesk_provider = crossdesk_provider_values()
    shared_values = dict(env_values)
    for name in set(SHARED_MODEL_ENV_NAMES) | set(LEGACY_SUMMARY_MODEL_ENV_NAMES):
        if name in os.environ:
            shared_values[name] = str(os.environ[name])
    shared_model = resolve_shared_model_config(
        shared_values,
        provider_fallback=crossdesk_provider,
    )
    visible_names = admin_visible_env_names(env_values)
    names = set(visible_names)
    items = []
    admin_order = {name: idx for idx, name in enumerate(visible_names)}
    for name in sorted(names, key=lambda n: admin_order.get(n, 999)):
        schema = ENV_CONFIG_BY_NAME.get(name, {"name": name, "label": name, "group": "其他", "kind": "text", "default": "", "effect": "restart"})
        fallback_value, fallback_source = business_config_fallback_value(
            name,
            crossdesk_provider=crossdesk_provider,
            shared_model=shared_model,
        )
        if name == ACTIVE_STRATEGY_ENV and name not in env_values and name not in os.environ:
            fallback_value = active_strategy_suite(
                None,
                env_values.get(STRATEGY_SOURCE_ENV),
                env_values.get(PERSONA_STRATEGY_ENV),
            )
            fallback_source = "legacy strategy settings"
        default_value = schema.get("default", "")
        if name == PRACTICE_SCHEDULE_TIMES_ENV:
            if name in os.environ:
                effective = os.environ.get(name, "")
                source = "process env"
            elif LEGACY_B1_SCHEDULE_TIMES_ENV in os.environ:
                effective = os.environ.get(LEGACY_B1_SCHEDULE_TIMES_ENV, "")
                source = "legacy process env"
            elif name in env_values:
                effective = env_values.get(name, "")
                source = "dashboard.env"
            elif LEGACY_B1_SCHEDULE_TIMES_ENV in env_values:
                effective = env_values.get(LEGACY_B1_SCHEDULE_TIMES_ENV, "")
                source = "legacy dashboard.env"
            else:
                effective = fallback_value or default_value
                source = fallback_source
            file_value = env_values.get(name)
            if file_value is None:
                file_value = env_values.get(LEGACY_B1_SCHEDULE_TIMES_ENV)
            if file_value is None:
                file_value = default_value
        else:
            if name in os.environ:
                effective = os.environ.get(name, "")
            elif name in env_values:
                effective = env_values.get(name, "")
            else:
                effective = fallback_value or default_value
            file_value = env_values.get(name)
            if file_value is None:
                file_value = (
                    ""
                    if schema.get("kind") == "secret"
                    else fallback_value
                    if fallback_source == "legacy summary settings"
                    else default_value
                )
            source = "process env" if name in os.environ else ("dashboard.env" if name in env_values else fallback_source)
        secret = schema.get("kind") == "secret" or is_secret_config_key(name)
        item = {
            **schema,
            "secret": secret,
            "effective": display_secret(effective) if secret else effective,
            "file_value": "" if secret else file_value,
            "file_state": display_secret(env_values.get(name) or fallback_value or default_value) if secret else file_value,
            "source": source,
        }
        if schema.get("kind") == "reasoning_effort":
            item["reasoning_model_names"] = list(
                REASONING_EFFORT_MODEL_NAMES.get(name, ())
            )
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
            state_value = (
                file_value
                if name == PRACTICE_SCHEDULE_TIMES_ENV
                else env_values.get(name) or fallback_value or default_value
            )
            item.update({
                "effective": friendly_time_list_text(effective),
                "file_value": normalize_time_list_update(str(file_value or "")),
                "file_state": friendly_time_list_text(str(state_value or "")),
                "default": friendly_time_list_text(default_value),
                "time_values": split_hhmm_values(str(file_value or "")),
            })
        if schema.get("kind") == "news_sources" and not secret:
            edit_source = str(file_value or default_value)
            edit_value = ",".join(parse_newsnow_source_ids(edit_source))
            state_value = env_values.get(name) if name in env_values else (fallback_value or default_value)
            item.update({
                "effective": friendly_newsnow_sources_text(str(effective)),
                "file_value": edit_value,
                "file_state": friendly_newsnow_sources_text(str(state_value)),
                "default": friendly_newsnow_sources_text(default_value),
                "news_source_values": list(parse_newsnow_source_ids(edit_value)),
                "news_source_default_values": list(DEFAULT_NEWSNOW_SOURCE_IDS),
                "news_source_options": newsnow_source_options(),
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
        "reasoning_effort_capabilities": reasoning_effort_capability_catalog(),
        "iwencai_test": iwencai_test_metadata(),
        "ui": {
            "strategy_suite_name": ACTIVE_STRATEGY_ENV,
            "strategy_preset_name": PRESET_STRATEGY_TEXT_ENV,
            "strategy_preset_value": "preset_text",
        },
        "about": {
            "author": PROJECT_AUTHOR,
            "author_url": PROJECT_AUTHOR_URL,
            "repository": PROJECT_REPOSITORY,
            "repository_url": PROJECT_REPOSITORY_URL,
            "license": PROJECT_LICENSE,
            "license_url": PROJECT_LICENSE_URL,
            "current_version": CURRENT_VERSION,
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
            title="通知渠道测试",
            text=(
                f"{label} 渠道配置验证消息。\n"
                f"发送时间：{datetime.now(CN_TZ).strftime('%Y-%m-%d %H:%M:%S')}（北京时间）"
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


def get_version_status(force_refresh: bool = False) -> dict[str, Any]:
    now = time.time()
    with VERSION_CHECK_LOCK:
        cached = VERSION_CHECK_CACHE.get("payload")
        cached_at = float(VERSION_CHECK_CACHE.get("ts") or 0)
        cached_ttl = int(VERSION_CHECK_CACHE.get("ttl") or 0)
        if not force_refresh and isinstance(cached, dict) and now - cached_at < cached_ttl:
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
