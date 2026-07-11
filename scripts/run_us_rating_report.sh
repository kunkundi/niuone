#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOCAL_DATA_DIR="${NIUONE_LOCAL_DATA_DIR:-$ROOT/.local-data}"
DASHBOARD_HOME="${DASHBOARD_HOME:-$LOCAL_DATA_DIR/runtime}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

export DASHBOARD_HOME
export DASHBOARD_ENV_FILE="${DASHBOARD_ENV_FILE:-$LOCAL_DATA_DIR/dashboard.env}"
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
export DASHBOARD_CONFIG="${DASHBOARD_CONFIG:-$DASHBOARD_HOME/config.yaml}"
export DASHBOARD_PUSH_HISTORY_DB="${DASHBOARD_PUSH_HISTORY_DB:-$DASHBOARD_HOME/push_history.db}"

mkdir -p "$DASHBOARD_HOME/logs"

exec "$PYTHON_BIN" "$ROOT/app/us_rating_report.py" --store-only "$@"
