"""Command service for durable NiuOne strict-forward evidence reports."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shlex
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

from app.core.paths import get_dashboard_env_file, get_dashboard_home
from app.reports.a_share.calendar import trading_day_status
from app.screening.stock_universe import selected_stock_universe
from app.strategies.registry import active_strategy_suite
from app.strategies.scoring.niuone import NIUONE_STRATEGY_IDS
from app.trading.niuone_forward import (
    DEFAULT_COHORT_START,
    evaluate_niuone_forward,
    decision_has_durable_candidate_evidence,
    load_niuone_forward_daily_equity_from_db,
    load_niuone_forward_decisions_from_db,
    load_niuone_forward_trades_from_db,
    merge_forward_trade_rows,
)


def _project_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "app" / "core" / "paths.py").is_file():
            return candidate
    raise RuntimeError("cannot locate NiuOne project root")


PROJECT_ROOT = _project_root()
CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_OUTPUT = Path("/tmp/niuone-forward-evaluation.json")
FORWARD_COHORT_START_ENV = "DASHBOARD_NIUONE_FORWARD_COHORT_START"
FORWARD_PREFLIGHT_CRON_ENV = "DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON"
FORWARD_EQUITY_SNAPSHOT_CRON_ENV = (
    "DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON"
)
FORWARD_EVALUATION_CRON_ENV = "DASHBOARD_NIUONE_FORWARD_CRON"
FORWARD_OPENING_EXIT_CRON_ENV = "DASHBOARD_B3_EXIT_TIME"
FORWARD_CLOSING_EXIT_CRON_ENV = "DASHBOARD_TIME_EXIT_TIME"
PROTOCOL_LOCK_SCHEMA_VERSION = 1
PROTOCOL_SOURCE_PATHS = (
    "app/automation/cron.py",
    "app/automation/scheduler_service.py",
    "app/dashboard/server.py",
    "app/entrypoints/evaluate_niuone_forward.py",
    "app/storage/practice_db.py",
    "app/strategies/attribution.py",
    "app/strategies/display.py",
    "app/strategies/exits.py",
    "app/strategies/lifecycle.py",
    "app/strategies/niuone_risk.py",
    "app/strategies/policy.py",
    "app/strategies/prompts.py",
    "app/strategies/registry.py",
    "app/strategies/scoring/common.py",
    "app/strategies/scoring/engine.py",
    "app/strategies/scoring/niuone.py",
    "app/strategies/selection.py",
    "app/screening/multi_strategy.py",
    "app/trading/fees.py",
    "app/trading/niuone_forward.py",
    "app/trading/niuone_forward_service.py",
    "app/trading/practice_trader.py",
)
PROTOCOL_RUNTIME_SETTING_DEFAULTS = {
    FORWARD_COHORT_START_ENV: DEFAULT_COHORT_START,
    "DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON": "5 9 * * 1-5",
    "DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON": "15 15 * * 1-5",
    "DASHBOARD_NIUONE_FORWARD_CRON": "20 15 * * 1-5",
    "DASHBOARD_NIUNIU_DB": "",
    "DASHBOARD_PORTFOLIO_STATE": "",
    "DASHBOARD_ACTIVE_STRATEGY": "niuone",
    "DASHBOARD_STOCK_UNIVERSE": "main_board",
    "DASHBOARD_PRACTICE_SCHEDULE_TIMES": (
        "09:25,10:00,10:30,11:00,11:20,13:00,13:30,14:00,14:30,14:50"
    ),
    "DASHBOARD_B1_SCHEDULE_ENABLED": "1",
    "DASHBOARD_B1_SCHEDULE_CATCHUP_MINUTES": "35",
    "DASHBOARD_B1_SCAN_TIMEOUT_SECONDS": "480",
    "DASHBOARD_B1_SCAN_WORKERS": "6",
    "DASHBOARD_KLINE_CACHE_ENABLED": "1",
    "DASHBOARD_KLINE_PREWARM_ENABLED": "1",
    "DASHBOARD_KLINE_PREWARM_TIME": "09:10",
    "DASHBOARD_KLINE_PREWARM_WORKERS": "12",
    "DASHBOARD_KLINE_PREWARM_TIMEOUT_SECONDS": "600",
    "DASHBOARD_KLINE_PREWARM_CATCHUP_MINUTES": "15",
    "DASHBOARD_DISPLAY_CANDIDATE_LIMIT": "10",
    "DASHBOARD_TRADE_CANDIDATE_LIMIT": "10",
    "DASHBOARD_B3_EXIT_TIME": "09:37",
    "DASHBOARD_TIME_EXIT_TIME": "14:45",
    "DASHBOARD_MAX_OPEN_POSITIONS": "6",
    "DASHBOARD_MAX_NEW_BUYS_PER_DECISION": "2",
    "DASHBOARD_MAX_SINGLE_POSITION_PCT": "10",
    "DASHBOARD_MAX_TOTAL_POSITION_PCT": "80",
    "DASHBOARD_MIN_CASH_RESERVE_PCT": "20",
    "DASHBOARD_MARKET_GUIDANCE_ENABLED": "1",
    "DASHBOARD_MORNING_MAX_OPEN_POSITIONS": "3",
    "DASHBOARD_PENDING_DECISION_POLL_SECONDS": "5",
    "DASHBOARD_DECISION_MODEL": "deepseek-v4-pro",
    "DASHBOARD_DECISION_BASE_URL": "",
    "DASHBOARD_DECISION_CONTEXT_LENGTH": "128000",
    "DASHBOARD_DECISION_MAX_TOKENS": "4096",
    "DASHBOARD_DECISION_TIMEOUT": "180",
    "DASHBOARD_DECISION_INTELLIGENCE_ENABLED": "1",
    "DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS": "75",
    "DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS": "5",
    "DASHBOARD_TRADE_DISCIPLINE_TEXT": "",
    "DASHBOARD_NEWS_MODEL": "",
    "DASHBOARD_NEWS_API_MODE": "auto",
    "DASHBOARD_NEWS_BASE_URL": "",
    "DASHBOARD_NEWS_CONTEXT_LENGTH": "128000",
    "DASHBOARD_NEWS_MAX_TOKENS": "4096",
    "DASHBOARD_NEWS_TIMEOUT": "45",
    "DASHBOARD_NEWS_MAX_RETRIES": "1",
    "DASHBOARD_NEWS_CONCURRENCY": "5",
    "IWENCAI_ENABLED": "0",
    "IWENCAI_BASE_URL": "",
    "IWENCAI_TIMEOUT_SECONDS": "20",
    "IWENCAI_MAX_RETRIES": "1",
    "IWENCAI_MAX_CONCURRENCY": "2",
}
PROTOCOL_DERIVED_RUNTIME_SETTING_NAMES = (
    "NIUONE_CRON_SCHEDULER_STATE_PATH",
    "NIUONE_B1_SCHEDULE_STATE_PATH",
    "NIUONE_A_SHARE_CALENDAR_CACHE_PATH",
)
_BOOLEAN_PROTOCOL_SETTINGS = {
    "DASHBOARD_B1_SCHEDULE_ENABLED",
    "DASHBOARD_KLINE_CACHE_ENABLED",
    "DASHBOARD_KLINE_PREWARM_ENABLED",
    "DASHBOARD_MARKET_GUIDANCE_ENABLED",
    "DASHBOARD_DECISION_INTELLIGENCE_ENABLED",
    "IWENCAI_ENABLED",
}
_INTEGER_PROTOCOL_SETTINGS = {
    name
    for name in PROTOCOL_RUNTIME_SETTING_DEFAULTS
    if name.endswith(("_SECONDS", "_MINUTES", "_WORKERS", "_MAX_ITEMS"))
} | {
    "DASHBOARD_DISPLAY_CANDIDATE_LIMIT",
    "DASHBOARD_TRADE_CANDIDATE_LIMIT",
    "DASHBOARD_MAX_OPEN_POSITIONS",
    "DASHBOARD_MAX_NEW_BUYS_PER_DECISION",
    "DASHBOARD_MORNING_MAX_OPEN_POSITIONS",
    "DASHBOARD_DECISION_CONTEXT_LENGTH",
    "DASHBOARD_DECISION_MAX_TOKENS",
    "DASHBOARD_DECISION_TIMEOUT",
    "DASHBOARD_NEWS_CONTEXT_LENGTH",
    "DASHBOARD_NEWS_MAX_TOKENS",
    "DASHBOARD_NEWS_TIMEOUT",
    "DASHBOARD_NEWS_MAX_RETRIES",
    "DASHBOARD_NEWS_CONCURRENCY",
    "IWENCAI_TIMEOUT_SECONDS",
    "IWENCAI_MAX_RETRIES",
    "IWENCAI_MAX_CONCURRENCY",
}
_FLOAT_PROTOCOL_SETTINGS = {
    "DASHBOARD_MAX_SINGLE_POSITION_PCT",
    "DASHBOARD_MAX_TOTAL_POSITION_PCT",
    "DASHBOARD_MIN_CASH_RESERVE_PCT",
}
_PROTOCOL_FILE_THREAD_LOCK = threading.RLock()


def _load_trades(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    trades = payload.get("trade_log") if isinstance(payload, dict) else payload
    if not isinstance(trades, list):
        raise ValueError("input must be a trade list or an account object with trade_log")
    return [row for row in trades if isinstance(row, dict)]


def _mark_non_durable_overlay(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prevent state-only rows from satisfying durable-payload attribution."""
    return [
        {**row, "_forward_payload_available": False}
        for row in rows
    ]


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _practice_database_has_evidence(path: Path) -> bool | None:
    """Return whether an existing DB contains account evidence, or None on error."""
    if not path.is_file():
        return False
    try:
        uri = f"{path.expanduser().resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            for table in ("trades", "daily_equity", "position_snapshots"):
                if table in tables and connection.execute(
                    f"SELECT 1 FROM {table} LIMIT 1"
                ).fetchone() is not None:
                    return True
            return False
    except (OSError, sqlite3.Error):
        return None


def _capture_account_baseline(
    state_path: Path,
    db_path: Path,
    *,
    captured_at: datetime,
) -> dict[str, Any]:
    """Capture a code-free pre-cohort account boundary for the protocol lock."""
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        database_has_evidence = _practice_database_has_evidence(db_path)
        if database_has_evidence is False:
            return {
                "status": "new_account_state_not_initialized",
                "source": "absent_state_and_empty_database",
                "captured_at": captured_at.isoformat(timespec="seconds"),
                "clean_zero_position_boundary": False,
            }
        return {
            "status": "missing_state_with_existing_or_unreadable_database",
            "source": "state_unavailable",
            "captured_at": captured_at.isoformat(timespec="seconds"),
            "clean_zero_position_boundary": False,
        }
    except (OSError, TypeError, ValueError):
        return {
            "status": "invalid_account_state",
            "source": "state_parse_failed",
            "captured_at": captured_at.isoformat(timespec="seconds"),
            "clean_zero_position_boundary": False,
        }
    if not isinstance(payload, Mapping):
        return {
            "status": "invalid_account_state",
            "source": "state_not_mapping",
            "captured_at": captured_at.isoformat(timespec="seconds"),
            "clean_zero_position_boundary": False,
        }
    initial_cash = _finite_number(payload.get("initial_cash"))
    cash = _finite_number(payload.get("cash"))
    account_created_at = str(payload.get("created_at") or "").strip()
    positions = payload.get("positions")
    if (
        initial_cash is None
        or initial_cash <= 0
        or cash is None
        or cash < 0
        or not isinstance(positions, Mapping)
        or _beijing_timestamp(account_created_at) is None
    ):
        return {
            "status": "invalid_account_state",
            "source": "state_account_fields_invalid",
            "captured_at": captured_at.isoformat(timespec="seconds"),
            "clean_zero_position_boundary": False,
        }
    market_value = 0.0
    open_count = 0
    niuone_count = 0
    non_niuone_count = 0
    unknown_count = 0
    for raw_position in positions.values():
        if not isinstance(raw_position, Mapping):
            continue
        quantity_value = _finite_number(
            raw_position.get("qty") or raw_position.get("shares") or 0
        )
        if (
            quantity_value is None
            or quantity_value < 0
            or not quantity_value.is_integer()
        ):
            return {
                "status": "invalid_account_state",
                "source": "state_position_quantity_invalid",
                "captured_at": captured_at.isoformat(timespec="seconds"),
                "clean_zero_position_boundary": False,
            }
        quantity = int(quantity_value)
        if quantity <= 0:
            continue
        price = _finite_number(
            raw_position.get("last_price")
            or raw_position.get("avg_cost")
        )
        if price is None or price <= 0:
            return {
                "status": "invalid_account_state",
                "source": "state_position_price_invalid",
                "captured_at": captured_at.isoformat(timespec="seconds"),
                "clean_zero_position_boundary": False,
            }
        open_count += 1
        market_value += price * quantity
        mark = raw_position.get("strategy_mark")
        mark = mark if isinstance(mark, Mapping) else {}
        strategy_id = str(
            raw_position.get("buy_strategy")
            or raw_position.get("initial_buy_strategy")
            or mark.get("strategy_id")
            or ""
        ).strip()
        if strategy_id in NIUONE_STRATEGY_IDS:
            niuone_count += 1
        elif strategy_id:
            non_niuone_count += 1
        else:
            unknown_count += 1
    total_equity = cash + market_value
    clean_boundary = open_count == 0
    return {
        "status": "captured",
        "source": "practice_state",
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "account_created_at": account_created_at,
        "initial_cash": round(initial_cash, 2),
        "cash": round(cash, 2),
        "total_equity": round(total_equity, 2),
        "open_position_count": open_count,
        "niuone_position_count": niuone_count,
        "non_niuone_position_count": non_niuone_count,
        "unknown_position_strategy_count": unknown_count,
        "clean_zero_position_boundary": clean_boundary,
    }


def _load_locked_account_baseline(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    baseline = payload.get("account_baseline") if isinstance(
        payload, Mapping
    ) else None
    return dict(baseline) if isinstance(baseline, Mapping) else None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    )
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_dashboard_env_values(path: Path | None = None) -> dict[str, str]:
    values: dict[str, str] = {}
    source = path or get_dashboard_env_file(PROJECT_ROOT)
    try:
        lines = source.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return values
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        name = key.strip()
        if name not in PROTOCOL_RUNTIME_SETTING_DEFAULTS and name not in {
            "DASHBOARD_HOME",
            "DASHBOARD_B1_SCHEDULE_TIMES",
            "DASHBOARD_TIME_STOP_EXIT_TIME",
            "DASHBOARD_STRATEGY_SOURCE",
            "DASHBOARD_ENABLED_PERSONA_STRATEGIES",
        }:
            continue
        try:
            parsed = shlex.split(raw_value.strip(), posix=True)
            values[name] = parsed[0] if parsed else ""
        except ValueError:
            values[name] = raw_value.strip().strip("\"'")
    return values


def _configured_value(
    name: str,
    env_values: Mapping[str, str],
    default: str = "",
) -> str:
    if name in os.environ:
        return str(os.environ.get(name) or "")
    if name in env_values:
        return str(env_values.get(name) or "")
    return default


def _canonical_setting(name: str, value: str) -> str:
    raw = str(value or "").strip()
    if name in _BOOLEAN_PROTOCOL_SETTINGS:
        return "false" if raw.lower() in {"0", "false", "no", "off"} else "true"
    if name in _INTEGER_PROTOCOL_SETTINGS:
        try:
            return str(int(raw))
        except ValueError:
            return f"invalid:{raw}"
    if name in _FLOAT_PROTOCOL_SETTINGS:
        try:
            return format(float(raw), ".12g")
        except ValueError:
            return f"invalid:{raw}"
    if name == "DASHBOARD_PRACTICE_SCHEDULE_TIMES":
        return ",".join(
            item.strip()
            for item in raw.replace("，", ",").split(",")
            if item.strip()
        )
    return raw


def _resolved_protocol_settings(
    env_values: Mapping[str, str] | None = None,
) -> dict[str, str]:
    configured = dict(
        _load_dashboard_env_values() if env_values is None else env_values
    )
    resolved = {
        name: _canonical_setting(
            name,
            _configured_value(name, configured, default),
        )
        for name, default in PROTOCOL_RUNTIME_SETTING_DEFAULTS.items()
    }
    dashboard_home = Path(
        _configured_value(
            "DASHBOARD_HOME",
            configured,
            str(get_dashboard_home(PROJECT_ROOT)),
        )
    ).expanduser()
    path_defaults = {
        "DASHBOARD_NIUNIU_DB": dashboard_home / "niuniu.db",
        "DASHBOARD_PORTFOLIO_STATE": (
            dashboard_home
            / "cron"
            / "output"
            / "niuniu_practice_portfolio.json"
        ),
    }
    for name, default_path in path_defaults.items():
        resolved[name] = str(
            Path(
                _configured_value(name, configured, str(default_path))
            ).expanduser().resolve()
        )
    derived_state_root = dashboard_home / "cron" / "state"
    resolved["NIUONE_CRON_SCHEDULER_STATE_PATH"] = str(
        (derived_state_root / "niuone_cron_scheduler.json").resolve()
    )
    resolved["NIUONE_B1_SCHEDULE_STATE_PATH"] = str(
        (derived_state_root / "b1_schedule_state.json").resolve()
    )
    resolved["NIUONE_A_SHARE_CALENDAR_CACHE_PATH"] = str(
        (derived_state_root / "a_share_trading_calendar.json").resolve()
    )
    if (
        "DASHBOARD_PRACTICE_SCHEDULE_TIMES" not in os.environ
        and "DASHBOARD_PRACTICE_SCHEDULE_TIMES" not in configured
    ):
        legacy_schedule = _configured_value(
            "DASHBOARD_B1_SCHEDULE_TIMES",
            configured,
            PROTOCOL_RUNTIME_SETTING_DEFAULTS["DASHBOARD_PRACTICE_SCHEDULE_TIMES"],
        )
        resolved["DASHBOARD_PRACTICE_SCHEDULE_TIMES"] = _canonical_setting(
            "DASHBOARD_PRACTICE_SCHEDULE_TIMES",
            legacy_schedule,
        )
    if (
        "DASHBOARD_TIME_EXIT_TIME" not in os.environ
        and "DASHBOARD_TIME_EXIT_TIME" not in configured
    ):
        resolved["DASHBOARD_TIME_EXIT_TIME"] = _configured_value(
            "DASHBOARD_TIME_STOP_EXIT_TIME",
            configured,
            "14:45",
        ).strip()
    raw_active = _configured_value("DASHBOARD_ACTIVE_STRATEGY", configured)
    resolved["DASHBOARD_ACTIVE_STRATEGY"] = active_strategy_suite(
        raw_active or None,
        _configured_value("DASHBOARD_STRATEGY_SOURCE", configured) or None,
        _configured_value("DASHBOARD_ENABLED_PERSONA_STRATEGIES", configured) or None,
    )
    resolved["DASHBOARD_STOCK_UNIVERSE"] = ",".join(
        selected_stock_universe(
            _configured_value("DASHBOARD_STOCK_UNIVERSE", configured, "main_board")
        )
    )
    return resolved


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _protocol_fingerprint(identity: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(serialized)


def _build_protocol_identity(
    protocol: Mapping[str, Any],
    *,
    runtime_settings: Mapping[str, str],
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    stable_protocol = {
        key: protocol.get(key)
        for key in (
            "version",
            "cohort_start",
            "minimum_completed_trades",
            "minimum_calendar_months",
            "historical_reference_win_rate_pct",
            "win_rate_confidence_level",
            "evidence_gate_rule",
            "performance_assessment_rule",
            "unit_of_analysis",
            "opportunity_unit_of_analysis",
            "portfolio_unit_of_analysis",
            "account_boundary_rule",
            "portfolio_daily_coverage_rule",
            "holding_lifecycle_daily_coverage_rule",
            "maximum_new_niuone_positions_per_trading_day",
            "daily_new_position_limit_rule",
            "niuone_reversal_minimum_recovery_ratio_inclusive",
            "niuone_reversal_maximum_recovery_ratio_exclusive",
            "niuone_reversal_recovery_rule",
            "niuone_reversal_minimum_strong_stock_count",
            "niuone_reversal_minimum_state_streak",
            "niuone_reversal_continuation_rule",
            "niuone_reversal_daily_candidate_limit",
            "niuone_reversal_absolute_position_cap_pct",
            "niuone_leader_minimum_sector_rank_inclusive",
            "niuone_leader_minimum_today_strength_inclusive",
            "niuone_leader_quality_rule",
            "niuone_startup_allowed_mainline_states",
            "niuone_startup_state_rule",
            "lifecycle_entry_strategy_routes",
            "oversized_niuone_buy_rule",
            "oversized_niuone_sell_rule",
            "performance_cluster_unit",
            "minimum_unique_performance_clusters",
            "minimum_effective_performance_clusters",
            "performance_cluster_confidence_rule",
            "maximum_portfolio_drawdown_pct",
            "minimum_return_to_drawdown_ratio",
            "candidate_evidence_schema_version",
            "execution_evidence_schema_version",
            "sell_execution_evidence_schema_version",
            "holding_lifecycle_evidence_schema_version",
            "required_candidate_evidence_fields",
            "required_entry_context_fields",
            "required_exit_context_fields",
            "required_executed_buy_sizing_fields",
            "required_executed_sell_sizing_fields",
            "conditional_entry_context_rules",
            "allowed_schedule_run_kinds",
            "allowed_execution_modes",
            "shadow_execution_gap_pct",
            "shadow_recovery_ratio_cap",
            "shadow_candidates",
            "required_operating_day_events",
            "operating_day_coverage_rule",
        )
    }
    source_files = {
        relative: _sha256_bytes((project_root / relative).read_bytes())
        for relative in PROTOCOL_SOURCE_PATHS
    }
    setting_hashes = {
        name: _sha256_bytes(f"{name}\0{value}".encode("utf-8"))
        for name, value in sorted(runtime_settings.items())
    }
    return {
        "schema_version": PROTOCOL_LOCK_SCHEMA_VERSION,
        "protocol": stable_protocol,
        "source_files": source_files,
        "runtime_settings": setting_hashes,
    }


def _changed_identity_fields(
    locked: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[str]:
    changed: list[str] = []
    for section in ("protocol", "source_files", "runtime_settings"):
        locked_values = locked.get(section)
        current_values = current.get(section)
        locked_map = locked_values if isinstance(locked_values, Mapping) else {}
        current_map = current_values if isinstance(current_values, Mapping) else {}
        for key in sorted(set(locked_map) | set(current_map)):
            if locked_map.get(key) != current_map.get(key):
                changed.append(f"{section}.{key}")
    return changed


def _pre_cohort_refresh_allowed(
    locked: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    refresh_date: date,
) -> bool:
    """Allow replacing a valid lock only before its cohort can contain data."""
    locked_protocol = locked.get("protocol")
    current_protocol = current.get("protocol")
    if not isinstance(locked_protocol, Mapping) or not isinstance(
        current_protocol,
        Mapping,
    ):
        return False
    locked_start = str(locked_protocol.get("cohort_start") or "")[:10]
    current_start = str(current_protocol.get("cohort_start") or "")[:10]
    if not locked_start or locked_start != current_start:
        return False
    try:
        cohort_start = date.fromisoformat(current_start)
    except ValueError:
        return False
    return refresh_date < cohort_start


@contextmanager
def _protocol_file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _PROTOCOL_FILE_THREAD_LOCK:
        with lock_path.open("a+b") as handle:
            if os.name == "nt":  # pragma: no cover - Windows deployment path
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


def _freeze_protocol_lock(
    path: Path,
    identity: Mapping[str, Any],
    *,
    frozen_at: str,
    refresh_date: date,
    account_baseline: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    target = path.expanduser().resolve()
    current_identity = dict(identity)
    current_fingerprint = _protocol_fingerprint(current_identity)
    with _protocol_file_lock(target):
        if not target.exists():
            payload = {
                "schema_version": PROTOCOL_LOCK_SCHEMA_VERSION,
                "frozen_at": frozen_at,
                "fingerprint": current_fingerprint,
                "identity": current_identity,
                "account_baseline": dict(account_baseline or {}),
            }
            _atomic_write_json(target, payload)
            return {
                "status": "frozen",
                "cohort_valid": True,
                "locked_fingerprint": current_fingerprint,
                "current_fingerprint": current_fingerprint,
                "changed_fields": [],
                "account_baseline": dict(account_baseline or {}),
            }
        try:
            locked_payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            locked_payload = None
        if (
            not isinstance(locked_payload, Mapping)
            or locked_payload.get("schema_version") != PROTOCOL_LOCK_SCHEMA_VERSION
            or not isinstance(locked_payload.get("identity"), Mapping)
            or len(str(locked_payload.get("fingerprint") or "")) != 64
        ):
            return {
                "status": "invalid_lock",
                "cohort_valid": False,
                "locked_fingerprint": None,
                "current_fingerprint": current_fingerprint,
                "changed_fields": ["protocol_lock_document"],
                "account_baseline": None,
            }
        locked_identity = locked_payload.get("identity")
        locked_identity = (
            locked_identity if isinstance(locked_identity, Mapping) else {}
        )
        locked_fingerprint = str(locked_payload.get("fingerprint") or "")
        locked_baseline = locked_payload.get("account_baseline")
        locked_baseline = (
            dict(locked_baseline)
            if isinstance(locked_baseline, Mapping) else None
        )
        if (
            locked_fingerprint == current_fingerprint
            and locked_identity == current_identity
        ):
            return {
                "status": "matched",
                "cohort_valid": True,
                "locked_fingerprint": locked_fingerprint,
                "current_fingerprint": current_fingerprint,
                "changed_fields": [],
                "account_baseline": locked_baseline,
            }
        changed_fields = _changed_identity_fields(
            locked_identity,
            current_identity,
        )
        if not changed_fields:
            changed_fields = ["protocol_lock_fingerprint"]
        if _pre_cohort_refresh_allowed(
            locked_identity,
            current_identity,
            refresh_date=refresh_date,
        ):
            payload = {
                "schema_version": PROTOCOL_LOCK_SCHEMA_VERSION,
                "frozen_at": frozen_at,
                "fingerprint": current_fingerprint,
                "identity": current_identity,
                "account_baseline": dict(account_baseline or {}),
                "pre_cohort_replacement": {
                    "replaced_fingerprint": locked_fingerprint,
                    "changed_fields": changed_fields,
                },
            }
            _atomic_write_json(target, payload)
            return {
                "status": "refrozen_pre_cohort",
                "cohort_valid": True,
                "locked_fingerprint": current_fingerprint,
                "current_fingerprint": current_fingerprint,
                "changed_fields": changed_fields,
                "account_baseline": dict(account_baseline or {}),
            }
        return {
            "status": "mismatch",
            "cohort_valid": False,
            "locked_fingerprint": locked_fingerprint or None,
            "current_fingerprint": current_fingerprint,
            "changed_fields": changed_fields,
            "account_baseline": locked_baseline,
        }


def _apply_protocol_integrity(
    report: dict[str, Any],
    integrity: Mapping[str, Any],
) -> None:
    status = str(integrity.get("status") or "")
    report["protocol_integrity"] = {
        **dict(integrity),
        "automatic_promotion_allowed": False,
        "source_file_count": len(PROTOCOL_SOURCE_PATHS),
        "runtime_setting_count": (
            len(PROTOCOL_RUNTIME_SETTING_DEFAULTS)
            + len(PROTOCOL_DERIVED_RUNTIME_SETTING_NAMES)
        ),
    }
    if status not in {"mismatch", "invalid_lock"}:
        return
    gate = report["evidence_gate"]
    gate["sample_status_before_protocol_check"] = gate["status"]
    gate["sample_evidence_gate_met"] = gate.get(
        "sample_evidence_gate_met_before_operations",
        gate["evidence_gate_met"],
    )
    gate["evidence_gate_met_before_protocol_check"] = gate[
        "evidence_gate_met"
    ]
    gate["status"] = "protocol_mismatch"
    gate["evidence_gate_met"] = False
    gate["decision"] = (
        "protocol_lock_invalid_requires_operator_review"
        if status == "invalid_lock"
        else "protocol_changed_requires_new_cohort"
    )
    performance = report.get("performance_assessment")
    if isinstance(performance, dict):
        performance["status_before_protocol_check"] = performance.get(
            "status"
        )
        performance["status"] = "protocol_mismatch"
        performance[
            "high_win_rate_and_positive_return_claim_supported"
        ] = False
        performance[
            "positive_risk_adjusted_portfolio_return_supported"
        ] = False
        performance["high_portfolio_return_claim_supported"] = False
    report["warnings"].append(
        "The frozen strict-forward protocol does not match the current code or "
        "runtime configuration. The original lock was preserved and this "
        "cohort cannot advance until an operator starts a new cohort."
    )


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _beijing_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            return None
    return (
        parsed.replace(tzinfo=CN_TZ)
        if parsed.tzinfo is None
        else parsed.astimezone(CN_TZ)
    )


def _configured_schedule_times(value: Any) -> tuple[str, ...]:
    times: list[str] = []
    for item in str(value or "").replace("，", ",").split(","):
        text = item.strip()
        try:
            parsed = datetime.strptime(text, "%H:%M")
        except ValueError:
            continue
        normalized = parsed.strftime("%H:%M")
        if normalized not in times:
            times.append(normalized)
    return tuple(sorted(times))


def _expected_operating_days(
    start: date,
    cutoff: date,
    *,
    calendar_cache_file: Path | None = None,
) -> list[date]:
    """Use the cached exchange calendar, with the existing weekday fallback."""
    expected: list[date] = []
    current = start
    while current <= cutoff:
        if current.weekday() < 5:
            status = trading_day_status(
                current,
                cache_file=(
                    calendar_cache_file
                    or Path("/tmp/niuone-forward-no-calendar-cache.json")
                ),
                allow_refresh=False,
            )
            if (
                status.get("calendar_cached") is not True
                or status.get("is_trading_day") is True
            ):
                expected.append(current)
        current += timedelta(days=1)
    return expected


def _successful_job_runs(
    scheduler_state: Mapping[str, Any],
    day_key: str,
    job_env: str,
) -> list[Mapping[str, Any]]:
    history = scheduler_state.get("job_history")
    history = history if isinstance(history, Mapping) else {}
    day = history.get(day_key)
    day = day if isinstance(day, Mapping) else {}
    runs = day.get(job_env)
    return [
        run
        for run in (runs if isinstance(runs, list) else [])
        if isinstance(run, Mapping) and run.get("success") is True
    ]


def _current_forward_cron_run(
    *,
    runtime_now: datetime,
    integrity: Mapping[str, Any],
) -> dict[str, Any] | None:
    if os.environ.get("NIUONE_CRON_JOB_ENV") != FORWARD_EVALUATION_CRON_ENV:
        return None
    scheduled_at = _beijing_timestamp(
        os.environ.get("NIUONE_CRON_SCHEDULED_AT")
    )
    if scheduled_at is None:
        return None
    return {
        "scheduled_at": scheduled_at.isoformat(),
        "completed_at": runtime_now.astimezone(CN_TZ).isoformat(),
        "success": integrity.get("status") in {
            "frozen",
            "matched",
            "refrozen_pre_cohort",
        },
        "status": str(integrity.get("status") or ""),
    }


def _apply_operational_coverage(
    report: dict[str, Any],
    *,
    scheduler_state: Mapping[str, Any],
    b1_state: Mapping[str, Any],
    runtime_settings: Mapping[str, str],
    decision_rows: Sequence[Mapping[str, Any]] = (),
    current_forward_run: Mapping[str, Any] | None = None,
) -> None:
    """Require complete daily simulator operations before manual review."""
    protocol = report.get("protocol")
    protocol = protocol if isinstance(protocol, Mapping) else {}
    start = date.fromisoformat(str(protocol.get("cohort_start") or "")[:10])
    cutoff = date.fromisoformat(str(protocol.get("as_of") or "")[:10])
    calendar_cache_value = runtime_settings.get(
        "NIUONE_A_SHARE_CALENDAR_CACHE_PATH"
    )
    expected_days = _expected_operating_days(
        start,
        cutoff,
        calendar_cache_file=(
            Path(calendar_cache_value)
            if calendar_cache_value else None
        ),
    )

    schedule_enabled = (
        runtime_settings.get("DASHBOARD_B1_SCHEDULE_ENABLED") == "true"
    )
    schedule_times = _configured_schedule_times(
        runtime_settings.get("DASHBOARD_PRACTICE_SCHEDULE_TIMES")
    )
    first_schedule_time = schedule_times[0] if schedule_times else ""
    b1_history = b1_state.get("day_history")
    b1_history = b1_history if isinstance(b1_history, Mapping) else {}
    durable_decision_slots: set[str] = set()
    for row in decision_rows:
        if (
            not isinstance(row, Mapping)
            or row.get("_forward_payload_available") is not True
            or not decision_has_durable_candidate_evidence(row)
            or str(row.get("schedule_run_kind") or "")
            not in {"scheduled", "catchup"}
        ):
            continue
        decision = row.get("decision")
        if (
            not isinstance(decision, Mapping)
            or decision.get("error")
            or not isinstance(decision.get("actions"), list)
            or not isinstance(row.get("executed"), list)
        ):
            continue
        slot = str(row.get("schedule_slot") or "")[:16]
        if _beijing_timestamp(slot) is not None:
            durable_decision_slots.add(slot)
    missing_days: list[dict[str, Any]] = []
    missing_counts: dict[str, int] = {}
    complete_count = 0

    def add_missing(items: list[str], requirement: str) -> None:
        if requirement in items:
            return
        items.append(requirement)
        missing_counts[requirement] = missing_counts.get(requirement, 0) + 1

    for day in expected_days:
        day_key = day.isoformat()
        missing: list[str] = []
        if not schedule_enabled:
            add_missing(missing, "practice_schedule_disabled")
        if not schedule_times:
            add_missing(missing, "practice_schedule_invalid_or_empty")

        first_slot = (
            _beijing_timestamp(f"{day_key} {first_schedule_time}")
            if first_schedule_time else None
        )
        preflight_runs = _successful_job_runs(
            scheduler_state,
            day_key,
            FORWARD_PREFLIGHT_CRON_ENV,
        )
        preflight_before_first = any(
            completed is not None
            and first_slot is not None
            and completed < first_slot
            for completed in (
                _beijing_timestamp(run.get("completed_at"))
                for run in preflight_runs
            )
        )
        if not preflight_before_first:
            add_missing(missing, "protocol_preflight_before_first_decision")

        raw_b1_day = b1_history.get(day_key)
        b1_day = raw_b1_day if isinstance(raw_b1_day, Mapping) else {}
        raw_slots = b1_day.get("slots")
        slots = raw_slots if isinstance(raw_slots, Mapping) else {}
        for slot_time in schedule_times:
            raw_slot = slots.get(slot_time)
            slot = raw_slot if isinstance(raw_slot, Mapping) else {}
            if str(slot.get("status") or "") != "ok":
                add_missing(missing, f"practice_slot:{slot_time}")
            decision_slot = f"{day_key} {slot_time}"
            if decision_slot not in durable_decision_slots:
                add_missing(
                    missing,
                    f"practice_decision_ledger:{slot_time}",
                )

        for job_env, requirement in (
            (FORWARD_OPENING_EXIT_CRON_ENV, "opening_exit_check_ok"),
            (FORWARD_CLOSING_EXIT_CRON_ENV, "closing_exit_check_ok"),
            (
                FORWARD_EQUITY_SNAPSHOT_CRON_ENV,
                "closing_equity_snapshot_ok",
            ),
        ):
            if not _successful_job_runs(scheduler_state, day_key, job_env):
                add_missing(missing, requirement)

        forward_runs = _successful_job_runs(
            scheduler_state,
            day_key,
            FORWARD_EVALUATION_CRON_ENV,
        )
        if current_forward_run is not None:
            scheduled_at = _beijing_timestamp(
                current_forward_run.get("scheduled_at")
            )
            if (
                current_forward_run.get("success") is True
                and scheduled_at is not None
                and scheduled_at.date() == day
            ):
                forward_runs.append(current_forward_run)
        if not forward_runs:
            add_missing(missing, "post_close_forward_evaluation_ok")

        if missing:
            missing_days.append({"date": day_key, "missing": missing})
        else:
            complete_count += 1

    expected_count = len(expected_days)
    operational_gate_met = bool(expected_count) and not missing_days
    opportunities = report.get("opportunities")
    opportunities = opportunities if isinstance(opportunities, Mapping) else {}
    sell_execution = opportunities.get("sell_execution")
    sell_execution = (
        sell_execution if isinstance(sell_execution, Mapping) else {}
    )
    funnel_quality_applicable = bool(
        opportunities.get("retained_decision_cycle_count")
        or opportunities.get("invalid_decision_timestamp_count")
        or sell_execution.get("model_sell_fill_count")
        or sell_execution.get("invalid_sell_execution_fill_count")
    )
    opportunity_funnel_quality_met = bool(
        not funnel_quality_applicable
        or opportunities.get("funnel_data_quality_gate_met") is True
    )
    report["operations"] = {
        "definition": "configured_weekday_simulator_operating_day",
        "configured_schedule_enabled": schedule_enabled,
        "configured_schedule_slots": list(schedule_times),
        "durable_practice_decision_slot_count": len(
            durable_decision_slots
        ),
        "expected_operating_day_count": expected_count,
        "complete_operating_day_count": complete_count,
        "operating_day_coverage_pct": round(
            complete_count / expected_count * 100.0,
            4,
        ) if expected_count else None,
        "operational_coverage_gate_met": operational_gate_met,
        "opportunity_funnel_quality_applicable": (
            funnel_quality_applicable
        ),
        "opportunity_funnel_data_quality_gate_met": (
            opportunity_funnel_quality_met
        ),
        "missing_requirement_counts": dict(sorted(missing_counts.items())),
        "incomplete_operating_days": missing_days,
    }
    gate = report["evidence_gate"]
    before_operations = bool(gate.get("evidence_gate_met"))
    gate["sample_evidence_gate_met_before_operations"] = before_operations
    gate["operational_coverage_gate_met"] = operational_gate_met
    gate["opportunity_funnel_data_quality_gate_met"] = (
        opportunity_funnel_quality_met
    )
    gate["evidence_gate_met"] = (
        before_operations
        and operational_gate_met
        and opportunity_funnel_quality_met
    )
    if before_operations and not operational_gate_met:
        gate["status"] = "operations_blocked"
        gate["decision"] = "incomplete_operating_day_coverage"
    elif before_operations and not opportunity_funnel_quality_met:
        gate["status"] = "data_quality_blocked"
        gate["decision"] = "inconsistent_forward_opportunity_evidence"
    if expected_count and not operational_gate_met:
        report["warnings"].append(
            "One or more configured weekday simulator operating days lack a "
            "successful preflight, Practice slot, exit check, or post-close "
            "evaluation. The cohort cannot advance until coverage is complete."
        )
    if funnel_quality_applicable and not opportunity_funnel_quality_met:
        report["warnings"].append(
            "Opportunity-set, model-action, BUY decision-execution, or model-"
            "directed SELL durable-fill evidence is incomplete or inconsistent. "
            "The cohort cannot advance until a complete new-protocol sample is "
            "collected."
        )
    performance = report.get("performance_assessment")
    performance = performance if isinstance(performance, dict) else {}
    performance_before_operations = bool(
        performance.get("performance_criteria_met_before_operations")
    )
    performance["operational_coverage_gate_met"] = operational_gate_met
    performance["opportunity_funnel_data_quality_gate_met"] = (
        opportunity_funnel_quality_met
    )
    final_performance_claim = all((
        performance_before_operations,
        operational_gate_met,
        opportunity_funnel_quality_met,
    ))
    performance[
        "high_win_rate_and_positive_return_claim_supported"
    ] = final_performance_claim
    performance[
        "positive_risk_adjusted_portfolio_return_supported"
    ] = final_performance_claim
    performance["high_portfolio_return_claim_supported"] = False
    if performance_before_operations:
        if not operational_gate_met:
            performance["status"] = "operations_blocked"
            performance["decision"] = (
                "repair_operating_day_coverage_before_performance_review"
            )
        elif not opportunity_funnel_quality_met:
            performance["status"] = "data_quality_blocked"
            performance["decision"] = (
                "repair_opportunity_funnel_before_performance_review"
            )
        else:
            performance["status"] = "claim_supported_for_manual_review"
            performance["decision"] = (
                "manual_review_only_no_automatic_promotion"
            )


def _runtime_paths() -> tuple[Path, Path, Path, Path, Path, Path]:
    dashboard_home = get_dashboard_home(PROJECT_ROOT)
    db_path = Path(
        os.environ.get("DASHBOARD_NIUNIU_DB") or dashboard_home / "niuniu.db"
    ).expanduser()
    state_path = Path(
        os.environ.get("DASHBOARD_PORTFOLIO_STATE")
        or dashboard_home / "cron" / "output" / "niuniu_practice_portfolio.json"
    ).expanduser()
    output_path = dashboard_home / "cron" / "output" / "niuone_forward_evaluation.json"
    lock_path = dashboard_home / "cron" / "state" / "niuone_forward_protocol.json"
    scheduler_state_path = (
        dashboard_home / "cron" / "state" / "niuone_cron_scheduler.json"
    )
    b1_schedule_state_path = (
        dashboard_home / "cron" / "state" / "b1_schedule_state.json"
    )
    return (
        db_path,
        state_path,
        output_path,
        lock_path,
        scheduler_state_path,
        b1_schedule_state_path,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate strict-forward NiuOne paper-trading evidence.",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--state",
        type=Path,
        help="Explicit JSON state or exported trade-log path.",
    )
    source.add_argument(
        "--db",
        type=Path,
        help="Explicit practice SQLite path; opened read-only.",
    )
    parser.add_argument(
        "--state-overlay",
        type=Path,
        help="Optional recent JSON state merged over --db by stable fill identity.",
    )
    parser.add_argument(
        "--runtime",
        action="store_true",
        help="Use private configured DB/state paths and the runtime report path.",
    )
    parser.add_argument(
        "--protocol-only",
        action="store_true",
        help="Freeze or verify the runtime protocol without opening trade evidence.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cohort-start")
    parser.add_argument("--as-of", help="Deterministic YYYY-MM-DD cutoff; defaults to today.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.runtime and (args.state or args.db or args.state_overlay):
        parser.error("--runtime cannot be combined with explicit evidence paths")
    if args.protocol_only and not args.runtime:
        parser.error("--protocol-only requires --runtime")
    if not args.runtime and not (args.state or args.db):
        parser.error("one of --state, --db, or --runtime is required")
    if args.state_overlay and not args.db:
        parser.error("--state-overlay requires --db")

    env_values = _load_dashboard_env_values()
    runtime_settings = _resolved_protocol_settings(env_values)
    cohort_start = args.cohort_start or (
        runtime_settings[FORWARD_COHORT_START_ENV]
        if args.runtime else DEFAULT_COHORT_START
    )
    runtime_settings[FORWARD_COHORT_START_ENV] = cohort_start
    runtime_now = datetime.now(CN_TZ)

    if args.protocol_only:
        (
            db_path,
            state_path,
            _runtime_output,
            protocol_lock_path,
            _scheduler_state_path,
            _b1_schedule_state_path,
        ) = _runtime_paths()
        protocol_report = evaluate_niuone_forward(
            [],
            cohort_start=cohort_start,
            as_of=args.as_of,
        )
        identity = _build_protocol_identity(
            protocol_report["protocol"],
            runtime_settings=runtime_settings,
        )
        account_baseline = _capture_account_baseline(
            state_path,
            db_path,
            captured_at=runtime_now,
        )
        integrity = _freeze_protocol_lock(
            protocol_lock_path,
            identity,
            frozen_at=runtime_now.isoformat(timespec="seconds"),
            refresh_date=runtime_now.date(),
            account_baseline=account_baseline,
        )
        changed_fields = ",".join(integrity["changed_fields"]) or "none"
        print(
            "NiuOne protocol preflight: "
            f"status={integrity['status']} changed_fields={changed_fields}"
        )
        return 2 if integrity["status"] in {"mismatch", "invalid_lock"} else 0

    source: dict[str, Any]
    decision_rows: list[dict[str, Any]] = []
    daily_equity_rows: list[dict[str, Any]] = []
    account_baseline: dict[str, Any] | None = None
    protocol_lock_path: Path | None = None
    scheduler_state: dict[str, Any] = {}
    b1_schedule_state: dict[str, Any] = {}
    if args.runtime:
        (
            db_path,
            state_path,
            runtime_output,
            protocol_lock_path,
            scheduler_state_path,
            b1_schedule_state_path,
        ) = _runtime_paths()
        scheduler_state = _load_mapping(scheduler_state_path)
        b1_schedule_state = _load_mapping(b1_schedule_state_path)
        db_rows, db_diagnostics = load_niuone_forward_trades_from_db(db_path)
        decision_rows, decision_diagnostics = (
            load_niuone_forward_decisions_from_db(db_path)
        )
        daily_equity_rows, daily_equity_diagnostics = (
            load_niuone_forward_daily_equity_from_db(db_path)
        )
        account_baseline = _load_locked_account_baseline(
            protocol_lock_path
        )
        if (
            account_baseline is None
            and runtime_now.date()
            < date.fromisoformat(str(cohort_start)[:10])
        ):
            account_baseline = _capture_account_baseline(
                state_path,
                db_path,
                captured_at=runtime_now,
            )
        state_rows = (
            _mark_non_durable_overlay(_load_trades(state_path))
            if state_path.is_file()
            else []
        )
        trades, source_duplicate_count = merge_forward_trade_rows(
            db_rows,
            state_rows,
        )
        output = args.output or runtime_output
        source = {
            "kind": "runtime_database_with_recent_state_overlay",
            **db_diagnostics,
            **decision_diagnostics,
            **daily_equity_diagnostics,
            "state_overlay_trade_row_count": len(state_rows),
            "source_duplicate_trade_count": source_duplicate_count,
        }
    elif args.db:
        db_rows, db_diagnostics = load_niuone_forward_trades_from_db(args.db)
        decision_rows, decision_diagnostics = (
            load_niuone_forward_decisions_from_db(args.db)
        )
        daily_equity_rows, daily_equity_diagnostics = (
            load_niuone_forward_daily_equity_from_db(args.db)
        )
        overlay_rows = (
            _mark_non_durable_overlay(_load_trades(args.state_overlay))
            if args.state_overlay
            else []
        )
        trades, source_duplicate_count = merge_forward_trade_rows(
            db_rows,
            overlay_rows,
        )
        output = args.output or DEFAULT_OUTPUT
        source = {
            "kind": "explicit_database_with_state_overlay"
            if args.state_overlay else "explicit_database",
            **db_diagnostics,
            **decision_diagnostics,
            **daily_equity_diagnostics,
            "state_overlay_trade_row_count": len(overlay_rows),
            "source_duplicate_trade_count": source_duplicate_count,
        }
    else:
        trades = _load_trades(args.state)
        output = args.output or DEFAULT_OUTPUT
        source = {
            "kind": "explicit_json_state",
            "state_trade_row_count": len(trades),
            "source_duplicate_trade_count": 0,
        }

    report_cutoff = date.fromisoformat(
        str(args.as_of or runtime_now.date().isoformat())[:10]
    )
    expected_operating_dates = [
        value.isoformat()
        for value in _expected_operating_days(
            date.fromisoformat(str(cohort_start)[:10]),
            report_cutoff,
            calendar_cache_file=Path(
                runtime_settings[
                    "NIUONE_A_SHARE_CALENDAR_CACHE_PATH"
                ]
            ),
        )
    ]
    report = evaluate_niuone_forward(
        trades,
        decision_rows=decision_rows,
        daily_equity_rows=daily_equity_rows,
        account_baseline=account_baseline,
        expected_operating_dates=expected_operating_dates,
        cohort_start=cohort_start,
        as_of=args.as_of,
    )
    report["source"] = source
    report["generated_on"] = (
        args.as_of or datetime.now(CN_TZ).date().isoformat()
    )
    identity = _build_protocol_identity(
        report["protocol"],
        runtime_settings=runtime_settings,
    )
    if protocol_lock_path is not None:
        integrity = _freeze_protocol_lock(
            protocol_lock_path,
            identity,
            frozen_at=runtime_now.isoformat(timespec="seconds"),
            refresh_date=runtime_now.date(),
            account_baseline=account_baseline,
        )
    else:
        current_fingerprint = _protocol_fingerprint(identity)
        integrity = {
            "status": "unlocked_explicit_evaluation",
            "cohort_valid": None,
            "locked_fingerprint": None,
            "current_fingerprint": current_fingerprint,
            "changed_fields": [],
        }
    _apply_operational_coverage(
        report,
        scheduler_state=scheduler_state,
        b1_state=b1_schedule_state,
        runtime_settings=runtime_settings,
        decision_rows=decision_rows,
        current_forward_run=(
            _current_forward_cron_run(
                runtime_now=runtime_now,
                integrity=integrity,
            )
            if args.runtime else None
        ),
    )
    _apply_protocol_integrity(report, integrity)
    _atomic_write_json(output, report)
    gate = report["evidence_gate"]
    overall = report["overall"]
    print(
        "NiuOne forward evaluation: "
        f"status={gate['status']} completed={overall['completed_trade_count']} "
        f"win_rate={overall['win_rate_pct']} output={output.expanduser().resolve()}"
    )
    return 2 if integrity["status"] in {"mismatch", "invalid_lock"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
