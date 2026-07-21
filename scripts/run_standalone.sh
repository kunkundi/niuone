#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOCAL_DATA_DIR="${NIUONE_LOCAL_DATA_DIR:-$ROOT/.local-data}"
DASHBOARD_HOME="${DASHBOARD_HOME:-$LOCAL_DATA_DIR/runtime}"
HOST="${DASHBOARD_HOST:-127.0.0.1}"
PORT="${DASHBOARD_PORT:-8877}"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT/.venv/bin/python"
  elif [[ -x "$LOCAL_DATA_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$LOCAL_DATA_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

mkdir -p "$DASHBOARD_HOME/cron/output" "$DASHBOARD_HOME/logs"
export DASHBOARD_HOME
export DASHBOARD_ENV_FILE="${DASHBOARD_ENV_FILE:-$LOCAL_DATA_DIR/dashboard.env}"
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
export DASHBOARD_TRADER_SCRIPT="${DASHBOARD_TRADER_SCRIPT:-$ROOT/app/entrypoints/niuniu_practice_trader.py}"
export DASHBOARD_PORTFOLIO_STATE="${DASHBOARD_PORTFOLIO_STATE:-$DASHBOARD_HOME/cron/output/niuniu_practice_portfolio.json}"
export DASHBOARD_CONFIG="${DASHBOARD_CONFIG:-$DASHBOARD_HOME/config.yaml}"
export DASHBOARD_PUSH_HISTORY_DB="${DASHBOARD_PUSH_HISTORY_DB:-$DASHBOARD_HOME/push_history.db}"

echo "牛牛1号 standalone"
echo "  root:           $ROOT"
echo "  dashboard home: $DASHBOARD_HOME"
echo "  listen:         http://$HOST:$PORT"

"$ROOT/scripts/build-frontend.sh"
exec "$PYTHON_BIN" "$ROOT/app/entrypoints/niuone_dashboard.py" --host "$HOST" --port "$PORT"
