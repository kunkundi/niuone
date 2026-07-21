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
DASHBOARD_HOST="${DASHBOARD_HOST:-127.0.0.1}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8787}"
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
export PYTHON_BIN
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
export DASHBOARD_CONFIG="${DASHBOARD_CONFIG:-$DASHBOARD_HOME/config.yaml}"
export DASHBOARD_PUSH_HISTORY_DB="${DASHBOARD_PUSH_HISTORY_DB:-$DASHBOARD_HOME/push_history.db}"
export DASHBOARD_PORTFOLIO_STATE="${DASHBOARD_PORTFOLIO_STATE:-$DASHBOARD_HOME/cron/output/niuniu_practice_portfolio.json}"
export DASHBOARD_TRADER_SCRIPT="${DASHBOARD_TRADER_SCRIPT:-$BASE/app/entrypoints/niuniu_practice_trader.py}"
export DASHBOARD_PUBLIC_PROJECTION_ENABLED="${DASHBOARD_PUBLIC_PROJECTION_ENABLED:-1}"
mkdir -p "$DASHBOARD_HOME/cron/output" "$DASHBOARD_HOME/logs"
"$BASE/scripts/build-frontend.sh"
exec "$PYTHON_BIN" "$BASE/app/entrypoints/niuone_dashboard.py" --host "$DASHBOARD_HOST" --port "$DASHBOARD_PORT"
