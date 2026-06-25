#!/usr/bin/env python3
"""Small local scheduler for NiuOne cron-style jobs."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from niuone_paths import get_dashboard_env_file, get_dashboard_home

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DASHBOARD_ENV_FILE = get_dashboard_env_file(PROJECT_ROOT)
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
LOG_DIR = Path(os.environ.get("DASHBOARD_LOG_DIR") or str(DASHBOARD_HOME / "logs")).expanduser()
LOG_PATH = LOG_DIR / "niuone_cron_scheduler.log"
STATE_PATH = DASHBOARD_HOME / "cron" / "state" / "niuone_cron_scheduler.json"
OUTPUT_DIR = DASHBOARD_HOME / "cron" / "output"
CN_TZ = ZoneInfo("Asia/Shanghai")
STOP = False


@dataclass(frozen=True)
class Job:
    env_name: str
    default_expr: str
    job_id: str
    title: str
    command: tuple[str, ...]
    timeout_seconds: int = 180
    archive_stdout: bool = True


@dataclass(frozen=True)
class JobRunResult:
    success: bool
    status: str
    exit_code: int | None = None
    elapsed: float = 0.0
    archive_path: str = ""
    error: str = ""


JOBS = (
    Job("DASHBOARD_MARKET_AUCTION_CRON", "25 9 * * 1-5", "8453b3f28cd3", "A股竞价盘前总结", ("a_share_auction_summary.py",), 180, True),
    Job("DASHBOARD_MARKET_MIDDAY_CRON", "40 11 * * 1-5", "192abba7eeb5", "A股午盘总结", ("a_share_midday_summary.py",), 180, True),
    Job("DASHBOARD_MARKET_CLOSE_CRON", "10 15 * * 1-5", "67ac98149ead", "A股盘后总结", ("a_share_close_summary.py",), 180, True),
    Job("DASHBOARD_US_RATING_CRON", "0 11 * * *", "fd0b807138f4", "每日美股机构买入评级汇报", ("us_rating_report.py", "--archive-only"), 150, False),
)


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(CN_TZ).isoformat()} {message}\n")
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
    return values


def read_int_setting(env_values: dict[str, str], name: str, default: int, *, min_value: int, max_value: int) -> int:
    raw = env_values.get(name) or os.environ.get(name) or str(default)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        log(f"invalid int setting name={name} value={raw!r}; using default={default}")
        value = default
    return max(min_value, min(max_value, value))


def retry_settings(job: Job, env_values: dict[str, str] | None = None) -> tuple[int, int]:
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
    values: set[int] = set()
    for token in str(part or "").split(","):
        token = token.strip()
        if not token:
            continue
        base, _, step_text = token.partition("/")
        step = int(step_text) if step_text else 1
        if step <= 0:
            raise ValueError(f"invalid cron step: {part}")
        if base == "*":
            start, end = low, high
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(base)
        for value in range(start, end + 1, step):
            normalized = 0 if dow and value == 7 else value
            if low <= normalized <= high:
                values.add(normalized)
    return values


def cron_matches(expr: str, now: datetime) -> bool:
    minute, hour, day, month, dow = str(expr or "").split()
    cron_dow = 0 if now.isoweekday() == 7 else now.isoweekday()
    return (
        now.minute in expand_field(minute, 0, 59)
        and now.hour in expand_field(hour, 0, 23)
        and now.day in expand_field(day, 1, 31)
        and now.month in expand_field(month, 1, 12)
        and cron_dow in expand_field(dow, 0, 7, dow=True)
    )


def normalize_job_expr(job: Job, expr: str) -> str:
    raw = str(expr or "").strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", raw):
        hour_text, minute_text = raw.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"invalid time: {raw}")
        default_parts = job.default_expr.split()
        day, month, dow = default_parts[2:5] if len(default_parts) == 5 else ("*", "*", "*")
        return f"{minute} {hour} {day} {month} {dow}"
    return raw


def load_state() -> dict[str, object]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"run_keys": []}


def save_state(state: dict[str, object]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def archive_job_output(job: Job, run_time: datetime, status: str, stdout: str, stderr: str, *, attempt: int = 1) -> Path:
    out_dir = OUTPUT_DIR / job.job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if attempt <= 1 else f"_retry{attempt - 1}"
    path = out_dir / f"{run_time.strftime('%Y-%m-%d_%H-%M-%S')}{suffix}.md"
    body = (stdout or "").strip()
    if stderr.strip():
        body = f"{body}\n\n```stderr\n{stderr.strip()}\n```".strip()
    payload = (
        f"# Cron Job: {job.title}\n\n"
        f"**Job ID:** {job.job_id}\n"
        f"**Run Time:** {run_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"**Mode:** niuone scheduler\n"
        f"**Attempt:** {attempt}\n"
        f"**Status:** {status}\n\n"
        "---\n\n"
        f"{body}\n"
    )
    path.write_text(payload, encoding="utf-8")
    return path


def run_job_once(job: Job, run_time: datetime, *, attempt: int = 1, max_attempts: int = 1) -> JobRunResult:
    env = os.environ.copy()
    env.update(parse_env_file())
    env.setdefault("DASHBOARD_HOME", str(DASHBOARD_HOME))
    env.setdefault("DASHBOARD_CONFIG", str(DASHBOARD_HOME / "config.yaml"))
    env.setdefault("DASHBOARD_PUSH_HISTORY_DB", str(DASHBOARD_HOME / "push_history.db"))
    command = [sys.executable, str(SCRIPT_DIR / job.command[0]), *job.command[1:]]
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
        archive_marker = re.search(r"archived:\s*(.+)", proc.stderr or "")
        if not job.archive_stdout and proc.returncode == 0 and not archive_marker:
            status = "script failed"
        success = status == "ok"
        if job.archive_stdout:
            archive_path = archive_job_output(job, run_time, status, proc.stdout or "", proc.stderr or "", attempt=attempt)
            log(f"finish job={job.job_id} status={status} exit={proc.returncode} attempt={attempt}/{max_attempts} elapsed={elapsed:.1f}s archive={archive_path}")
            return JobRunResult(success, status, proc.returncode, elapsed, str(archive_path))
        else:
            detail = f" archive={archive_marker.group(1).strip()}" if archive_marker else " missing_archive_marker=true"
            log(f"finish job={job.job_id} status={status} exit={proc.returncode} attempt={attempt}/{max_attempts} elapsed={elapsed:.1f}s{detail} stderr={(proc.stderr or '').strip()[:500]!r}")
            archive_path = archive_marker.group(1).strip() if archive_marker else ""
            return JobRunResult(success, status, proc.returncode, elapsed, archive_path, (proc.stderr or "").strip()[:500])
    except subprocess.TimeoutExpired as exc:
        if job.archive_stdout:
            archive_path = archive_job_output(job, run_time, "script failed", exc.stdout or "", f"timeout after {job.timeout_seconds}s\n{exc.stderr or ''}", attempt=attempt)
            log(f"timeout job={job.job_id} attempt={attempt}/{max_attempts} archive={archive_path}")
            return JobRunResult(False, "script failed", None, time.monotonic() - start, str(archive_path), f"timeout after {job.timeout_seconds}s")
        else:
            log(f"timeout job={job.job_id} attempt={attempt}/{max_attempts} after {job.timeout_seconds}s")
            return JobRunResult(False, "script failed", None, time.monotonic() - start, "", f"timeout after {job.timeout_seconds}s")
    except Exception as exc:
        if job.archive_stdout:
            archive_path = archive_job_output(job, run_time, "script failed", "", f"{type(exc).__name__}: {exc}", attempt=attempt)
            log(f"exception job={job.job_id} attempt={attempt}/{max_attempts} archive={archive_path} error={type(exc).__name__}: {exc}")
            return JobRunResult(False, "script failed", None, time.monotonic() - start, str(archive_path), f"{type(exc).__name__}: {exc}")
        else:
            log(f"exception job={job.job_id} attempt={attempt}/{max_attempts} error={type(exc).__name__}: {exc}")
            return JobRunResult(False, "script failed", None, time.monotonic() - start, "", f"{type(exc).__name__}: {exc}")


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


def main() -> None:
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    log(f"scheduler started pid={os.getpid()}")
    state = load_state()
    run_keys = list(state.get("run_keys") or [])[-500:]
    try:
        while not STOP:
            env_values = parse_env_file()
            now = datetime.now(CN_TZ).replace(second=0, microsecond=0)
            for job in JOBS:
                expr = normalize_job_expr(job, env_values.get(job.env_name) or os.environ.get(job.env_name) or job.default_expr)
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
                    run_job(job, now)
            time.sleep(10)
    finally:
        state["run_keys"] = run_keys[-500:]
        save_state(state)
        log("scheduler stopped")


if __name__ == "__main__":
    main()
