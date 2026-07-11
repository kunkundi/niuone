"""Shared local path defaults for NiuOne.

Runtime data lives in an ignored .local-data directory by default so a repository upload
does not accidentally commit databases, tokens, logs, or generated reports.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def get_local_data_dir(root: Path) -> Path:
    return Path(os.environ.get("NIUONE_LOCAL_DATA_DIR") or root / ".local-data").expanduser()


def get_dashboard_home(root: Path) -> Path:
    return Path(os.environ.get("DASHBOARD_HOME") or get_local_data_dir(root) / "runtime").expanduser()


def get_dashboard_env_file(root: Path) -> Path:
    if os.environ.get("DASHBOARD_ENV_FILE"):
        return Path(os.environ["DASHBOARD_ENV_FILE"]).expanduser()
    project_env = root / "dashboard.env"
    if project_env.exists():
        return project_env
    return get_local_data_dir(root) / "dashboard.env"


def container_runtime_overrides(root: Path) -> dict[str, str]:
    """Return path and listener values that must stay inside a container."""
    raw_data_dir = str(os.environ.get("NIUONE_CONTAINER_DATA_DIR") or "").strip()
    if not raw_data_dir:
        return {}

    data_dir = Path(raw_data_dir).expanduser()
    dashboard_home = data_dir / "runtime"
    app_dir = root / "app"
    python_bin = str(os.environ.get("PYTHON_BIN") or sys.executable)
    return {
        "NIUONE_LOCAL_DATA_DIR": str(data_dir),
        "NIUONE_ROOT": str(root),
        "DASHBOARD_ENV_FILE": str(data_dir / "dashboard.env"),
        "DASHBOARD_HOME": str(dashboard_home),
        "DASHBOARD_HOST": str(os.environ.get("NIUONE_CONTAINER_HOST") or "0.0.0.0"),
        "DASHBOARD_PORT": str(os.environ.get("NIUONE_CONTAINER_PORT") or "8787"),
        "PYTHON_BIN": python_bin,
        "DASHBOARD_CONFIG": str(dashboard_home / "config.yaml"),
        "DASHBOARD_LOG_DIR": str(dashboard_home / "logs"),
        "DASHBOARD_PUSH_HISTORY_DB": str(dashboard_home / "push_history.db"),
        "DASHBOARD_PORTFOLIO_STATE": str(dashboard_home / "cron" / "output" / "niuniu_practice_portfolio.json"),
        "DASHBOARD_NIUNIU_DB": str(dashboard_home / "niuniu.db"),
        "DASHBOARD_TRADER_SCRIPT": str(app_dir / "niuniu_practice_trader.py"),
        "DASHBOARD_B1_SCANNER": str(app_dir / "multi_strategy_screen.py"),
        "DASHBOARD_CN_STOCK_TOOLS": str(app_dir / "cn_stock_tools.py"),
        "DASHBOARD_CRON_JOBS": str(dashboard_home / "cron" / "jobs.json"),
        "DASHBOARD_X_WATCHLIST_STATE": str(dashboard_home / "cron" / "state" / "x_watchlist_latest.json"),
        "X_WATCHLIST_MONITOR": str(app_dir / "x_watchlist_monitor.py"),
        "X_WATCHLIST_PYTHON": python_bin,
    }


def apply_container_runtime_overrides(values: dict[str, str], root: Path) -> dict[str, str]:
    """Keep host-oriented dashboard.env paths from overriding container paths."""
    overrides = container_runtime_overrides(root)
    if not overrides:
        return values
    merged = dict(values)
    merged.update(overrides)
    return merged
