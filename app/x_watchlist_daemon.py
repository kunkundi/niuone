#!/usr/bin/env python3
"""Long-running wrapper for the NiuOne X watchlist monitor."""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from niuone_paths import apply_container_runtime_overrides, get_dashboard_env_file, get_dashboard_home

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DASHBOARD_ENV_FILE = get_dashboard_env_file(PROJECT_ROOT)
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
PYTHON = Path(os.environ.get("X_WATCHLIST_PYTHON") or sys.executable).expanduser()
MONITOR = Path(os.environ.get("X_WATCHLIST_MONITOR") or str(SCRIPT_DIR / "x_watchlist_monitor.py")).expanduser()
LOG_DIR = Path(os.environ.get("DASHBOARD_LOG_DIR") or str(DASHBOARD_HOME / "logs")).expanduser()
LOG_PATH = LOG_DIR / "x_watchlist_daemon.log"
PID_PATH = DASHBOARD_HOME / "run" / "x_watchlist_daemon.pid"
STOP = False


def parse_env_file(path: Path = DASHBOARD_ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        raw_value = raw_value.strip()
        try:
            parsed = shlex.split(raw_value, posix=True)
            values[key] = parsed[0] if parsed else ""
        except ValueError:
            values[key] = raw_value.strip("\"'")
    return apply_container_runtime_overrides(values, PROJECT_ROOT)


def env_int(name: str, default: int, env: dict[str, str] | None = None) -> int:
    try:
        source = env if env is not None else os.environ
        value = source.get(name)
        return int(value) if value else default
    except (TypeError, ValueError):
        return default


DEFAULT_INTERVAL_SECONDS = env_int("X_WATCHLIST_DAEMON_INTERVAL_SECONDS", 1200)
INNER_TIMEOUT_SECONDS = env_int("X_WATCHLIST_DAEMON_INNER_TIMEOUT_SECONDS", 150)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"{utc_now()} {message}\n")
        f.flush()


def handle_stop(signum: int, _frame: object) -> None:
    global STOP
    STOP = True
    log(f"received signal {signum}, stopping after current iteration")


def runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(parse_env_file())
    return env


def us_features_enabled(env: dict[str, str] | None = None) -> bool:
    source = env if env is not None else runtime_env()
    return str(source.get("DASHBOARD_US_FEATURES_ENABLED") or "0").strip().lower() in {"1", "true", "yes", "on"}


def current_interval_seconds() -> int:
    env = runtime_env()
    return max(1, env_int("X_WATCHLIST_DAEMON_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS, env))


def run_once() -> None:
    env = runtime_env()
    if not us_features_enabled(env):
        log("skip inner: DASHBOARD_US_FEATURES_ENABLED is disabled")
        return
    env.setdefault("NIUONE_ROOT", str(PROJECT_ROOT))
    env.setdefault("DASHBOARD_HOME", str(DASHBOARD_HOME))
    env.setdefault("DASHBOARD_CONFIG", str(DASHBOARD_HOME / "config.yaml"))
    env.setdefault("DASHBOARD_PUSH_HISTORY_DB", str(DASHBOARD_HOME / "push_history.db"))
    env.setdefault("DASHBOARD_X_WATCHLIST_STATE", str(DASHBOARD_HOME / "cron" / "state" / "x_watchlist_latest.json"))
    env.setdefault("X_WATCHLIST_SCRIPT_ALARM_SECONDS", "140")
    env.setdefault("X_WATCHLIST_DEADLINE_SECONDS", "135")
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [str(PYTHON), str(MONITOR)],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=INNER_TIMEOUT_SECONDS,
        )
        elapsed = time.monotonic() - start
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if proc.returncode == 0:
            log(f"inner ok in {elapsed:.1f}s")
            if stdout:
                log(f"inner stdout: {stdout[:1000]!r}")
            if stderr:
                log(f"inner stderr: {stderr[:1000]!r}")
        else:
            log(f"inner exit={proc.returncode} in {elapsed:.1f}s stdout={stdout[:1000]!r} stderr={stderr[:1000]!r}")
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - start
        log(f"inner wrapper-timeout after {elapsed:.1f}s stdout={(exc.stdout or '')[:1000]!r} stderr={(exc.stderr or '')[:1000]!r}")
    except Exception as exc:
        log(f"inner exception: {type(exc).__name__}: {exc}")


def main() -> None:
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    log(f"daemon started pid={os.getpid()} interval={current_interval_seconds()}s monitor={MONITOR}")
    try:
        while not STOP:
            run_once()
            slept = 0
            while slept < current_interval_seconds():
                if STOP:
                    break
                time.sleep(1)
                slept += 1
    finally:
        try:
            PID_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        log("daemon stopped")


if __name__ == "__main__":
    main()
