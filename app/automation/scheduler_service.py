#!/usr/bin/env python3
"""Small local scheduler for NiuOne cron-style jobs."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from niuone_paths import apply_container_runtime_overrides, get_dashboard_env_file, get_dashboard_home

if __package__ == "app":
    from .automation.cron import (
        Job,
        JobRunResult,
        cron_matches as _cron_matches,
        expand_field as _expand_field,
        job_expr_value as _job_expr_value,
        normalize_job_expr as _normalize_job_expr,
    )
else:
    from automation.cron import (
        Job,
        JobRunResult,
        cron_matches as _cron_matches,
        expand_field as _expand_field,
        job_expr_value as _job_expr_value,
        normalize_job_expr as _normalize_job_expr,
    )

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DASHBOARD_ENV_FILE = get_dashboard_env_file(PROJECT_ROOT)
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
LOG_DIR = Path(os.environ.get("DASHBOARD_LOG_DIR") or str(DASHBOARD_HOME / "logs")).expanduser()
LOG_PATH = LOG_DIR / "niuone_cron_scheduler.log"
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3
STATE_PATH = DASHBOARD_HOME / "cron" / "state" / "niuone_cron_scheduler.json"
CN_TZ = ZoneInfo("Asia/Shanghai")
STOP = False
JOB_HISTORY_RETENTION_DAYS = 400
JOB_HISTORY_RUNS_PER_JOB = 10


NIUONE_FORWARD_PROTOCOL_PREFLIGHT_JOB = Job(
    "DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON",
    "5 9 * * 1-5",
    "b3e6f88f3c62",
    "牛牛严格前向协议预检",
    ("evaluate_niuone_forward.py", "--runtime", "--protocol-only"),
    30,
)

NIUONE_EQUITY_SNAPSHOT_JOB = Job(
    "DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON",
    "15 15 * * 1-5",
    "701d0b5e4179",
    "牛牛盘后账户净值快照",
    ("niuniu_practice_trader.py", "--snapshot-equity"),
    120,
)


JOBS = (
    Job("IWENCAI_DRAGON_TIGER_CRON", "0 18 * * 1-5", "6a72470cc5e1", "交易日龙虎榜快照", ("iwencai_dragon_tiger_snapshot.py",), 120),
    Job("DASHBOARD_US_MARKET_SUMMARY_CRON", "0 8 * * 1-5", "98f0c8a12d3e", "隔夜美股盘面总结", ("us_market_summary.py", "--store"), 180),
    Job("DASHBOARD_MARKET_AUCTION_CRON", "25 9 * * 1-5", "8453b3f28cd3", "A股竞价盘前总结", ("a_share_auction_summary.py",), 180),
    Job("DASHBOARD_MARKET_MIDDAY_CRON", "40 11 * * 1-5", "192abba7eeb5", "A股午盘总结", ("a_share_midday_summary.py",), 180),
    Job("DASHBOARD_MARKET_CLOSE_CRON", "10 15 * * 1-5", "67ac98149ead", "A股盘后总结", ("a_share_close_summary.py",), 180),
    Job("DASHBOARD_MARKET_CLASSIFICATION_CRON", "40 16 * * 1-5", "f7a30c81b942", "盘后行业与概念分类刷新", ("refresh_market_classifications.py",), 210),
    NIUONE_FORWARD_PROTOCOL_PREFLIGHT_JOB,
    Job("DASHBOARD_B3_EXIT_TIME", "37 9 * * 1-5", "f4b8c0ad1a35", "牛牛B3开盘离场检查", ("niuniu_practice_trader.py", "--auto-exits"), 120),
    Job("DASHBOARD_TIME_EXIT_TIME", "45 14 * * 1-5", "fc4f23b79591", "牛牛尾盘离场检查", ("niuniu_practice_trader.py", "--auto-exits"), 120),
    NIUONE_EQUITY_SNAPSHOT_JOB,
    Job("DASHBOARD_NIUONE_FORWARD_CRON", "20 15 * * 1-5", "d419bc090808", "牛牛严格前向证据评估", ("evaluate_niuone_forward.py", "--runtime"), 30),
)
IWENCAI_STARTUP_CATCH_UP_JOB = Job(
    "IWENCAI_DRAGON_TIGER_CRON",
    "0 18 * * 1-5",
    "a7b51c9238d4",
    "龙虎榜启动追补",
    ("iwencai_dragon_tiger_snapshot.py", "--catch-up"),
    180,
)


def _rotate_log_if_needed(incoming_bytes: int) -> None:
    try:
        current_bytes = LOG_PATH.stat().st_size
    except FileNotFoundError:
        return
    except OSError:
        return
    if current_bytes + max(0, incoming_bytes) <= LOG_MAX_BYTES:
        return
    for index in range(LOG_BACKUP_COUNT, 0, -1):
        source = (
            LOG_PATH
            if index == 1
            else LOG_PATH.with_name(f"{LOG_PATH.name}.{index - 1}")
        )
        target = LOG_PATH.with_name(f"{LOG_PATH.name}.{index}")
        try:
            source.replace(target)
        except FileNotFoundError:
            continue
        except OSError:
            return


def log(message: str) -> None:
    line = f"{datetime.now(CN_TZ).isoformat()} {message}\n"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _rotate_log_if_needed(len(line.encode("utf-8")))
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()


def handle_stop(signum: int, _frame: object) -> None:
    global STOP
    STOP = True
    log(f"received signal {signum}, stopping")


def parse_env_file(path: Path = DASHBOARD_ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return apply_container_runtime_overrides(values, PROJECT_ROOT)


def read_int_setting(env_values: dict[str, str], name: str, default: int, *, min_value: int, max_value: int) -> int:
    raw = env_values.get(name) or os.environ.get(name) or str(default)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        log(f"invalid int setting name={name} value={raw!r}; using default={default}")
        value = default
    return max(min_value, min(max_value, value))


def job_enabled(job: Job, env_values: dict[str, str]) -> bool:
    if job.env_name == "IWENCAI_DRAGON_TIGER_CRON":
        # Callers pass the fully resolved dashboard.env mapping. Do not leak a
        # separately inherited process value into explicit test/runtime snapshots.
        raw = env_values.get("IWENCAI_ENABLED") or "0"
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    return True


def retry_settings(job: Job, env_values: dict[str, str] | None = None) -> tuple[int, int]:
    if job.env_name == "DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON":
        return 1, 0
    values = env_values if env_values is not None else parse_env_file()
    max_attempts = read_int_setting(values, "DASHBOARD_CRON_MAX_ATTEMPTS", 2, min_value=1, max_value=5)
    retry_delay = read_int_setting(values, "DASHBOARD_CRON_RETRY_DELAY_SECONDS", 300, min_value=0, max_value=3600)
    return max_attempts, retry_delay


def sleep_interruptibly(seconds: int) -> bool:
    deadline = time.monotonic() + max(0, int(seconds))
    while not STOP and time.monotonic() < deadline:
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    return not STOP


def expand_field(part: str, low: int, high: int, *, dow: bool = False) -> set[int]:
    return _expand_field(part, low, high, dow=dow)


def cron_matches(expr: str, now: datetime) -> bool:
    return _cron_matches(expr, now, field_expander=expand_field)


def normalize_job_expr(job: Job, expr: str) -> str:
    return _normalize_job_expr(job, expr)


def job_expr_value(job: Job, env_values: dict[str, str]) -> str:
    return _job_expr_value(job, env_values, environ=os.environ)


def load_state() -> dict[str, object]:
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"run_keys": []}
    except (OSError, TypeError, ValueError) as exc:
        log(
            "scheduler state unavailable; starting fail-closed history "
            f"error={type(exc).__name__}"
        )
        return {"run_keys": []}
    if not isinstance(payload, dict):
        log("scheduler state invalid type; starting fail-closed history")
        return {"run_keys": []}
    return payload


def save_state(state: dict[str, object]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_name(
        f".{STATE_PATH.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    )
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(STATE_PATH)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def run_job_once(job: Job, run_time: datetime, *, attempt: int = 1, max_attempts: int = 1) -> JobRunResult:
    env = os.environ.copy()
    env.update(parse_env_file())
    env.setdefault("DASHBOARD_HOME", str(DASHBOARD_HOME))
    env.setdefault("DASHBOARD_CONFIG", str(DASHBOARD_HOME / "config.yaml"))
    env.setdefault("DASHBOARD_PUSH_HISTORY_DB", str(DASHBOARD_HOME / "push_history.db"))
    env["NIUONE_CRON_RUN_KEY"] = f"{job.job_id}:{run_time.strftime('%Y%m%d%H%M')}"
    env["NIUONE_CRON_JOB_ENV"] = job.env_name
    env["NIUONE_CRON_SCHEDULED_AT"] = run_time.astimezone(CN_TZ).isoformat()
    command = [sys.executable, str(SCRIPT_DIR / "entrypoints" / job.command[0]), *job.command[1:]]
    start = time.monotonic()
    log(f"start job={job.job_id} title={job.title} attempt={attempt}/{max_attempts} command={command}")
    try:
        proc = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=job.timeout_seconds,
        )
        elapsed = time.monotonic() - start
        status = "ok" if proc.returncode == 0 else "script failed"
        success = status == "ok"
        stdout_detail = (proc.stdout or "").strip()[:500]
        stderr_detail = (proc.stderr or "").strip()[:500]
        error_detail = (stderr_detail or stdout_detail) if not success else ""
        log(
            f"finish job={job.job_id} status={status} exit={proc.returncode} "
            f"attempt={attempt}/{max_attempts} elapsed={elapsed:.1f}s error={error_detail!r}"
        )
        return JobRunResult(
            success=success,
            status=status,
            exit_code=proc.returncode,
            elapsed=elapsed,
            error=error_detail,
        )
    except subprocess.TimeoutExpired as exc:
        error = f"timeout after {job.timeout_seconds}s"
        log(f"timeout job={job.job_id} attempt={attempt}/{max_attempts} error={error}")
        return JobRunResult(False, "script failed", None, time.monotonic() - start, error)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        log(f"exception job={job.job_id} attempt={attempt}/{max_attempts} error={error}")
        return JobRunResult(False, "script failed", None, time.monotonic() - start, error)


def run_job(job: Job, run_time: datetime) -> JobRunResult:
    env_values = parse_env_file()
    max_attempts, retry_delay = retry_settings(job, env_values)
    result = run_job_once(job, run_time, attempt=1, max_attempts=max_attempts)
    attempt = 1
    while not result.success and attempt < max_attempts and not STOP:
        attempt += 1
        log(
            f"retry scheduled job={job.job_id} title={job.title} "
            f"next_attempt={attempt}/{max_attempts} delay={retry_delay}s previous_status={result.status}"
        )
        if retry_delay > 0 and not sleep_interruptibly(retry_delay):
            log(f"retry cancelled job={job.job_id} attempt={attempt}/{max_attempts} reason=stopping")
            return result
        result = run_job_once(job, run_time, attempt=attempt, max_attempts=max_attempts)
    if not result.success and max_attempts > 1:
        log(f"retry exhausted job={job.job_id} attempts={max_attempts} final_status={result.status}")
    return result


def run_startup_catch_up(now: datetime, env_values: dict[str, str]) -> JobRunResult | None:
    if not job_enabled(IWENCAI_STARTUP_CATCH_UP_JOB, env_values):
        return None
    return run_job(IWENCAI_STARTUP_CATCH_UP_JOB, now)


def run_startup_protocol_preflight(now: datetime) -> JobRunResult:
    """Freeze or verify the strict-forward protocol before any scheduled BUY."""
    return run_job(NIUONE_FORWARD_PROTOCOL_PREFLIGHT_JOB, now)


def record_job_result(
    state: dict[str, object],
    job: Job,
    scheduled_at: datetime,
    result: JobRunResult,
    *,
    completed_at: datetime | None = None,
) -> None:
    """Retain bounded per-day outcomes for strict-forward operations QA."""
    scheduled = scheduled_at.astimezone(CN_TZ)
    completed = (completed_at or datetime.now(CN_TZ)).astimezone(CN_TZ)
    raw_history = state.get("job_history")
    history = raw_history if isinstance(raw_history, dict) else {}
    day_key = scheduled.strftime("%Y-%m-%d")
    raw_day = history.get(day_key)
    day = raw_day if isinstance(raw_day, dict) else {}
    raw_runs = day.get(job.env_name)
    runs = raw_runs if isinstance(raw_runs, list) else []
    runs.append({
        "scheduled_at": scheduled.isoformat(),
        "completed_at": completed.isoformat(),
        "success": bool(result.success),
        "status": str(result.status or ""),
        "exit_code": result.exit_code,
    })
    day[job.env_name] = runs[-JOB_HISTORY_RUNS_PER_JOB:]
    history[day_key] = day
    state["job_history"] = {
        key: history[key]
        for key in sorted(history)[-JOB_HISTORY_RETENTION_DAYS:]
    }


def main() -> None:
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    log(f"scheduler started pid={os.getpid()}")
    state = load_state()
    run_keys = list(state.get("run_keys") or [])[-500:]
    startup_values = parse_env_file()
    startup_preflight_at = datetime.now(CN_TZ).replace(second=0, microsecond=0)
    startup_preflight_result = run_startup_protocol_preflight(
        startup_preflight_at
    )
    record_job_result(
        state,
        NIUONE_FORWARD_PROTOCOL_PREFLIGHT_JOB,
        startup_preflight_at,
        startup_preflight_result,
    )
    save_state(state)
    run_startup_catch_up(datetime.now(CN_TZ).replace(second=0, microsecond=0), startup_values)
    try:
        while not STOP:
            env_values = parse_env_file()
            now = datetime.now(CN_TZ).replace(second=0, microsecond=0)
            for job in JOBS:
                if not job_enabled(job, env_values):
                    continue
                expr = normalize_job_expr(job, job_expr_value(job, env_values))
                try:
                    due = cron_matches(expr, now)
                except Exception as exc:
                    log(f"invalid cron job={job.job_id} env={job.env_name} expr={expr!r} error={exc}")
                    continue
                run_key = f"{job.job_id}:{now.strftime('%Y%m%d%H%M')}"
                if due and run_key not in run_keys:
                    run_keys.append(run_key)
                    state["run_keys"] = run_keys[-500:]
                    save_state(state)
                    result = run_job(job, now)
                    record_job_result(state, job, now, result)
                    save_state(state)
            time.sleep(10)
    finally:
        state["run_keys"] = run_keys[-500:]
        save_state(state)
        log("scheduler stopped")


if __name__ == "__main__":
    main()
