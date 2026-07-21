#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT/web"
DIST_DIR="$WEB_DIR/dist"
STAMP="$DIST_DIR/.niuone-build"
PNPM_VERSION="11.15.1"
INSTALLED_LOCK="$WEB_DIR/node_modules/.pnpm/lock.yaml"
LOCAL_VITE="$WEB_DIR/node_modules/.bin/vite"

# launchd starts background services with a minimal PATH. Include the standard
# Homebrew prefixes so a source update can rebuild Vue before the service starts.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

needs_build=0
if [[ ! -f "$DIST_DIR/index.html" || ! -f "$STAMP" ]]; then
  needs_build=1
elif find \
  "$WEB_DIR/src" \
  "$WEB_DIR/index.html" \
  "$WEB_DIR/package.json" \
  "$WEB_DIR/pnpm-lock.yaml" \
  "$WEB_DIR/pnpm-workspace.yaml" \
  "$WEB_DIR/vite.config.js" \
  "$ROOT/frontend/dashboard.css" \
  "$ROOT/frontend/admin.css" \
  -newer "$STAMP" -print -quit | grep -q .; then
  needs_build=1
fi

if [[ "$needs_build" != "1" ]]; then
  exit 0
fi

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js 22.12+ is required to build the Vue frontend." >&2
  exit 1
fi

if command -v pnpm >/dev/null 2>&1; then
  PNPM=(pnpm)
  "${PNPM[@]}" --dir "$WEB_DIR" install --frozen-lockfile
  "${PNPM[@]}" --dir "$WEB_DIR" run build
elif [[ -x "$LOCAL_VITE" && -f "$INSTALLED_LOCK" ]] && cmp -s "$WEB_DIR/pnpm-lock.yaml" "$INSTALLED_LOCK"; then
  # Long-running service managers often have a minimal PATH. If pnpm already
  # installed the exact locked dependency graph, rebuild without a network hop.
  echo "Using the existing locked frontend dependencies."
  (cd "$WEB_DIR" && "$LOCAL_VITE" build)
elif command -v npx >/dev/null 2>&1; then
  PNPM=(npx --yes "pnpm@$PNPM_VERSION")
  "${PNPM[@]}" --dir "$WEB_DIR" install --frozen-lockfile
  "${PNPM[@]}" --dir "$WEB_DIR" run build
else
  echo "pnpm or npx is required to build the Vue frontend." >&2
  exit 1
fi
touch "$STAMP"
