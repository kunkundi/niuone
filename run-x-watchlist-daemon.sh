#!/usr/bin/env bash
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_DATA_DIR="${NIUONE_LOCAL_DATA_DIR:-$BASE/.local-data}"
if [[ -z "${DASHBOARD_ENV_FILE:-}" ]]; then
  if [[ -f "$BASE/dashboard.env" ]]; then
    DASHBOARD_ENV_FILE="$BASE/dashboard.env"
  elif [[ -f "$LOCAL_DATA_DIR/dashboard.env" ]]; then
    DASHBOARD_ENV_FILE="$LOCAL_DATA_DIR/dashboard.env"
  fi
fi
if [[ -n "${DASHBOARD_ENV_FILE:-}" && -f "$DASHBOARD_ENV_FILE" ]]; then
  set -a
  source "$DASHBOARD_ENV_FILE"
  set +a
fi

DASHBOARD_HOME="${DASHBOARD_HOME:-$LOCAL_DATA_DIR/runtime}"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x "$BASE/.venv/bin/python" ]]; then
    PYTHON_BIN="$BASE/.venv/bin/python"
  elif [[ -x "$LOCAL_DATA_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$LOCAL_DATA_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

export DASHBOARD_ENV_FILE="${DASHBOARD_ENV_FILE:-$LOCAL_DATA_DIR/dashboard.env}"
export DASHBOARD_HOME
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
export DASHBOARD_CONFIG="${DASHBOARD_CONFIG:-$DASHBOARD_HOME/config.yaml}"
export DASHBOARD_PUSH_HISTORY_DB="${DASHBOARD_PUSH_HISTORY_DB:-$DASHBOARD_HOME/push_history.db}"
export X_WATCHLIST_MONITOR="${X_WATCHLIST_MONITOR:-$BASE/app/x_watchlist_monitor.py}"
export X_WATCHLIST_PYTHON="${X_WATCHLIST_PYTHON:-$PYTHON_BIN}"
export X_WATCHLIST_SCRIPT_ALARM_SECONDS="${X_WATCHLIST_SCRIPT_ALARM_SECONDS:-140}"
export X_WATCHLIST_DAEMON_INNER_TIMEOUT_SECONDS="${X_WATCHLIST_DAEMON_INNER_TIMEOUT_SECONDS:-150}"

mkdir -p "$DASHBOARD_HOME/cron/state" "$DASHBOARD_HOME/logs"
exec "$PYTHON_BIN" "$BASE/app/x_watchlist_daemon.py"
