#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "== Python syntax checks =="
"$PYTHON_BIN" - <<'PY'
from pathlib import Path

for base in ("app", "scripts", "tests"):
    for path in sorted(Path(base).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
PY

echo "== Frontend JavaScript syntax =="
node --check web/src/main.js
node --check web/src/router.js
node --check web/src/composables/useDashboardTabs.js
node --check web/src/composables/usePublicProjection.js
node --check web/src/composables/usePracticeData.js

echo "== Vue production build =="
if ! command -v pnpm >/dev/null 2>&1; then
  echo "pnpm is required; install pnpm 11.15.1 before validation" >&2
  exit 1
fi
pnpm --dir web install --frozen-lockfile
pnpm --dir web run build

echo "== Shell syntax checks =="
for script in *.sh scripts/*.sh *.command; do
  [[ -f "$script" ]] || continue
  bash -n "$script"
done

echo "== Windows BAT launcher checks =="
"$PYTHON_BIN" - <<'PY'
from pathlib import Path

script = Path("run.bat")
if not script.exists():
    raise SystemExit("run.bat is missing")
text = script.read_text(encoding="utf-8")
for needle in ("--port", "--no-browser", "--service", "DASHBOARD_PORT", "manage-long-running.ps1"):
    if needle not in text:
        raise SystemExit(f"run.bat is missing {needle}")
for path in (Path("scripts/manage-long-running.ps1"), Path("scripts/run-windows-service.ps1")):
    if not path.exists():
        raise SystemExit(f"{path} is missing")
PY

echo "== Unit tests =="
PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -m unittest discover -s tests -p 'test_*.py'

echo "== OK =="
