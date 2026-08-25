#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER_DATA_DIR="${NIUONE_CONTAINER_DATA_DIR:-/data}"
CONTAINER_HOST="${NIUONE_CONTAINER_HOST:-0.0.0.0}"
CONTAINER_PORT="${NIUONE_CONTAINER_PORT:-8787}"
CONTAINER_TZ="${NIUONE_CONTAINER_TZ:-Asia/Shanghai}"
SOURCE_ENV_FILE="${DASHBOARD_ENV_FILE:-$CONTAINER_DATA_DIR/dashboard.env}"
_NIUONE_EXPLICIT_NEWSNOW_BASE_URL_SET=0
_NIUONE_EXPLICIT_NEWSNOW_BASE_URL=""
_NIUONE_BUNDLED_NEWSNOW_URL="${NIUONE_BUNDLED_NEWSNOW_URL:-}"
if [[ -n "${NEWSNOW_BASE_URL:-}" ]]; then
  _NIUONE_EXPLICIT_NEWSNOW_BASE_URL_SET=1
  _NIUONE_EXPLICIT_NEWSNOW_BASE_URL="$NEWSNOW_BASE_URL"
fi

_NIUONE_CONTAINER_PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -n "$_NIUONE_CONTAINER_PYTHON_BIN" ]]; then
  _NIUONE_CONTAINER_PYTHON_BIN="$(command -v "$_NIUONE_CONTAINER_PYTHON_BIN" || true)"
fi
if [[ -z "$_NIUONE_CONTAINER_PYTHON_BIN" ]]; then
  if ! _NIUONE_CONTAINER_PYTHON_BIN="$(command -v python3)"; then
    echo "python3 is required but was not found in PATH." >&2
    exit 1
  fi
fi
readonly _NIUONE_CONTAINER_PYTHON_BIN

if [[ -f "$SOURCE_ENV_FILE" ]]; then
  set -a
  source "$SOURCE_ENV_FILE"
  set +a
fi
if [[ -n "$_NIUONE_BUNDLED_NEWSNOW_URL" ]]; then
  export NIUONE_BUNDLED_NEWSNOW_URL="$_NIUONE_BUNDLED_NEWSNOW_URL"
  if [[ "$_NIUONE_EXPLICIT_NEWSNOW_BASE_URL_SET" == "1" ]]; then
    export NEWSNOW_BASE_URL="$_NIUONE_EXPLICIT_NEWSNOW_BASE_URL"
  else
    unset NEWSNOW_BASE_URL
  fi
elif [[ "$_NIUONE_EXPLICIT_NEWSNOW_BASE_URL_SET" == "1" ]]; then
  export NEWSNOW_BASE_URL="$_NIUONE_EXPLICIT_NEWSNOW_BASE_URL"
fi
unset _NIUONE_EXPLICIT_NEWSNOW_BASE_URL_SET
unset _NIUONE_EXPLICIT_NEWSNOW_BASE_URL
unset _NIUONE_BUNDLED_NEWSNOW_URL

# Runtime paths and the listening address are container invariants. Keep them
# outside dashboard.env so a host-oriented config cannot escape the data volume
# or make the service unreachable through its published port.
export HOME=/home/niuone
export TZ="$CONTAINER_TZ"
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHON_BIN="$_NIUONE_CONTAINER_PYTHON_BIN"
export NIUONE_CONTAINER_DATA_DIR="$CONTAINER_DATA_DIR"
export NIUONE_CONTAINER_HOST="$CONTAINER_HOST"
export NIUONE_CONTAINER_PORT="$CONTAINER_PORT"
export NIUONE_LOCAL_DATA_DIR="$CONTAINER_DATA_DIR"
export DASHBOARD_ENV_FILE="$CONTAINER_DATA_DIR/dashboard.env"
export DASHBOARD_HOME="$CONTAINER_DATA_DIR/runtime"
export DASHBOARD_HOST="$CONTAINER_HOST"
export DASHBOARD_PORT="$CONTAINER_PORT"
export DASHBOARD_PUBLIC_DATA_DIR="$DASHBOARD_HOME/public-data"
export DASHBOARD_PUBLIC_PROJECTION_ENABLED="${DASHBOARD_PUBLIC_PROJECTION_ENABLED:-1}"
export DASHBOARD_CONFIG="$DASHBOARD_HOME/config.yaml"
export DASHBOARD_LOG_DIR="$DASHBOARD_HOME/logs"
export DASHBOARD_PUSH_HISTORY_DB="$DASHBOARD_HOME/push_history.db"
export DASHBOARD_PORTFOLIO_STATE="$DASHBOARD_HOME/cron/output/niuniu_practice_portfolio.json"
export DASHBOARD_NIUNIU_DB="$DASHBOARD_HOME/niuniu.db"
export DASHBOARD_PROMPT_STRATEGY_DB="$DASHBOARD_HOME/prompt_strategies.db"
export DASHBOARD_TRADER_SCRIPT="$ROOT/app/entrypoints/niuniu_practice_trader.py"
export DASHBOARD_B1_SCANNER="$ROOT/app/entrypoints/multi_strategy_screen.py"
export DASHBOARD_CN_STOCK_TOOLS="$ROOT/app/entrypoints/cn_stock_tools.py"
export DASHBOARD_CRON_JOBS="$DASHBOARD_HOME/cron/jobs.json"
export NIUONE_ROOT="$ROOT"

umask 077
if ! mkdir -p \
  "$DASHBOARD_HOME/cron/state" \
  "$DASHBOARD_HOME/cron/output" \
  "$DASHBOARD_HOME/logs"; then
  echo "NiuOne runtime directories cannot be created by uid=$(id -u), gid=$(id -g): $DASHBOARD_HOME" >&2
  exit 73
fi

for runtime_dir in \
  "$DASHBOARD_HOME" \
  "$DASHBOARD_HOME/cron" \
  "$DASHBOARD_HOME/cron/state" \
  "$DASHBOARD_HOME/cron/output" \
  "$DASHBOARD_HOME/logs"; do
  if [[ ! -w "$runtime_dir" ]]; then
    echo "NiuOne runtime directory is not writable by uid=$(id -u), gid=$(id -g): $runtime_dir" >&2
    exit 73
  fi
done

for runtime_file in \
  "$DASHBOARD_PORTFOLIO_STATE" \
  "$DASHBOARD_NIUNIU_DB" \
  "$DASHBOARD_PROMPT_STRATEGY_DB"; do
  if [[ -e "$runtime_file" && ( ! -r "$runtime_file" || ! -w "$runtime_file" ) ]]; then
    echo "NiuOne runtime file is not readable and writable by uid=$(id -u), gid=$(id -g): $runtime_file" >&2
    exit 73
  fi
done

if [[ $# -eq 0 ]]; then
  set -- dashboard
fi

case "$1" in
  dashboard)
    shift
    exec "$PYTHON_BIN" "$ROOT/app/entrypoints/niuone_dashboard.py" \
      --host "$DASHBOARD_HOST" --port "$DASHBOARD_PORT" "$@"
    ;;
  scheduler)
    shift
    exec "$PYTHON_BIN" "$ROOT/app/entrypoints/niuone_cron_scheduler.py" "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
