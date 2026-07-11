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

echo "== Embedded dashboard JavaScript syntax =="
TMP_JS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/niuone-dashboard-js.XXXXXX")"
TMP_JS="$TMP_JS_DIR/dashboard.js"
TMP_ADMIN_JS="$TMP_JS_DIR/admin.js"
trap 'rm -rf "$TMP_JS_DIR"' EXIT
"$PYTHON_BIN" - "$TMP_JS" "$TMP_ADMIN_JS" <<'PY'
from pathlib import Path
import sys

s = Path('app/niuone_dashboard.py').read_text()
for marker, output in (
    ('INDEX_HTML = r"""', sys.argv[1]),
    ('ADMIN_HTML = r"""', sys.argv[2]),
):
    html = s.split(marker, 1)[1].split('"""', 1)[0]
    js = html.split('<script>', 1)[1].split('</script>', 1)[0]
    Path(output).write_text(js)
    print(output)
PY
node --check "$TMP_JS"
node --check "$TMP_ADMIN_JS"

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
