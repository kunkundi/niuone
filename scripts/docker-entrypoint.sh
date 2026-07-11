#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER_DATA_DIR="${NIUONE_CONTAINER_DATA_DIR:-/data}"
CONTAINER_HOST="${NIUONE_CONTAINER_HOST:-0.0.0.0}"
CONTAINER_PORT="${NIUONE_CONTAINER_PORT:-8787}"
CONTAINER_TZ="${NIUONE_CONTAINER_TZ:-Asia/Shanghai}"
SOURCE_ENV_FILE="${DASHBOARD_ENV_FILE:-$CONTAINER_DATA_DIR/dashboard.env}"

if [[ -f "$SOURCE_ENV_FILE" ]]; then
  set -a
  source "$SOURCE_ENV_FILE"
  set +a
fi

# Runtime paths and the listening address are container invariants. Keep them
# outside dashboard.env so a host-oriented config cannot escape the data volume
# or make the service unreachable through its published port.
export HOME=/home/niuone
export TZ="$CONTAINER_TZ"
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHON_BIN="$(command -v python)"
export NIUONE_CONTAINER_DATA_DIR="$CONTAINER_DATA_DIR"
export NIUONE_CONTAINER_HOST="$CONTAINER_HOST"
export NIUONE_CONTAINER_PORT="$CONTAINER_PORT"
export NIUONE_LOCAL_DATA_DIR="$CONTAINER_DATA_DIR"
export DASHBOARD_ENV_FILE="$CONTAINER_DATA_DIR/dashboard.env"
export DASHBOARD_HOME="$CONTAINER_DATA_DIR/runtime"
export DASHBOARD_HOST="$CONTAINER_HOST"
export DASHBOARD_PORT="$CONTAINER_PORT"
export DASHBOARD_CONFIG="$DASHBOARD_HOME/config.yaml"
export DASHBOARD_LOG_DIR="$DASHBOARD_HOME/logs"
export DASHBOARD_PUSH_HISTORY_DB="$DASHBOARD_HOME/push_history.db"
export DASHBOARD_PORTFOLIO_STATE="$DASHBOARD_HOME/cron/output/niuniu_practice_portfolio.json"
export DASHBOARD_NIUNIU_DB="$DASHBOARD_HOME/niuniu.db"
export DASHBOARD_TRADER_SCRIPT="$ROOT/app/niuniu_practice_trader.py"
export DASHBOARD_B1_SCANNER="$ROOT/app/multi_strategy_screen.py"
export DASHBOARD_CN_STOCK_TOOLS="$ROOT/app/cn_stock_tools.py"
export DASHBOARD_CRON_JOBS="$DASHBOARD_HOME/cron/jobs.json"
export DASHBOARD_X_WATCHLIST_STATE="$DASHBOARD_HOME/cron/state/x_watchlist_latest.json"
export NIUONE_ROOT="$ROOT"
export X_WATCHLIST_MONITOR="$ROOT/app/x_watchlist_monitor.py"
export X_WATCHLIST_PYTHON="$PYTHON_BIN"
export X_WATCHLIST_SCRIPT_ALARM_SECONDS="${X_WATCHLIST_SCRIPT_ALARM_SECONDS:-140}"
export X_WATCHLIST_DAEMON_INNER_TIMEOUT_SECONDS="${X_WATCHLIST_DAEMON_INNER_TIMEOUT_SECONDS:-150}"

umask 077
mkdir -p \
  "$DASHBOARD_HOME/cron/state" \
  "$DASHBOARD_HOME/logs"

if [[ $# -eq 0 ]]; then
  set -- dashboard
fi

case "$1" in
  dashboard)
    shift
    exec "$PYTHON_BIN" "$ROOT/app/niuone_dashboard.py" \
      --host "$DASHBOARD_HOST" --port "$DASHBOARD_PORT" "$@"
    ;;
  scheduler)
    shift
    exec "$PYTHON_BIN" "$ROOT/app/niuone_cron_scheduler.py" "$@"
    ;;
  x-watchlist)
    shift
    exec "$PYTHON_BIN" "$ROOT/app/x_watchlist_daemon.py" "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
