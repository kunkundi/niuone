"""Hard deadlines for legacy providers whose HTTP clients omit timeouts."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.process_lease import FileLease


TIMEOUTS = {"sector_rows": 20.0, "hot_stocks": 45.0}
WORKER = Path(__file__).resolve().parents[2] / "entrypoints" / "market_data_worker.py"


def run_bounded_market_fetch(operation: str, *, cache_path: Path) -> Any:
    """Keep provider logic intact, but kill a stalled worker and release its lease."""
    timeout = TIMEOUTS[operation]
    lease = FileLease(cache_path.with_suffix(".fetch.lock"), stale_after_seconds=timeout + 10)
    if not lease.acquire():
        raise RuntimeError(f"{operation} refresh already running")
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            result = subprocess.run(
                [sys.executable, str(WORKER), operation],
                capture_output=True, text=True, encoding="utf-8",
                timeout=timeout, check=False, env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"{operation} refresh exceeded {timeout:g}s") from exc
        if result.returncode:
            raise RuntimeError(f"{operation} worker exited ({result.returncode})")
        value = json.loads(result.stdout)
        expected = list if operation == "sector_rows" else dict
        if not isinstance(value, expected):
            raise ValueError(f"{operation} worker returned invalid payload")
        return value
    finally:
        lease.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=TIMEOUTS)
    operation = parser.parse_args().operation
    if operation == "sector_rows":
        from dashboard.apis.sectors_service import _ak_rows_in_process
        value = _ak_rows_in_process()
    else:
        from dashboard.apis.hot_stocks_service import _compute_in_process
        value = _compute_in_process()
    json.dump(value, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
