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
trap 'rm -rf "$TMP_JS_DIR"' EXIT
"$PYTHON_BIN" - "$TMP_JS" <<'PY'
from pathlib import Path
import sys

s = Path('app/niuone_dashboard.py').read_text()
html = s.split('INDEX_HTML = r"""', 1)[1].split('"""', 1)[0]
js = html.split('<script>', 1)[1].split('</script>', 1)[0]
Path(sys.argv[1]).write_text(js)
print(sys.argv[1])
PY
node --check "$TMP_JS"

echo "== Shell syntax checks =="
for script in *.sh scripts/*.sh *.command; do
  [[ -f "$script" ]] || continue
  bash -n "$script"
done

echo "== PowerShell syntax checks =="
if command -v pwsh >/dev/null 2>&1; then
  pwsh -NoLogo -NoProfile -Command "\$errors = \$null; [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw run.ps1), [ref]\$errors) > \$null; if (\$errors) { \$errors | Format-List; exit 1 }"
elif command -v powershell >/dev/null 2>&1; then
  powershell -NoLogo -NoProfile -Command "\$errors = \$null; [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw run.ps1), [ref]\$errors) > \$null; if (\$errors) { \$errors | Format-List; exit 1 }"
else
  echo "PowerShell not found; skipping run.ps1 syntax check"
fi

echo "== Unit tests =="
PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -m unittest discover -s tests -p 'test_*.py'

echo "== OK =="
