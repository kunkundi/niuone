"""Isolate legacy AkShare calls so provider retry loops respect report deadlines."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

OPERATIONS = {
    "stock_zh_a_spot_em": ("A_SHARE_SUMMARY_SPOT_TIMEOUT", 50),
    "stock_zh_a_spot": ("A_SHARE_SUMMARY_SINA_TIMEOUT", 10),
    "stock_fund_flow_industry": ("A_SHARE_SUMMARY_FUND_TIMEOUT", 10),
    "stock_zt_pool_em": ("A_SHARE_SUMMARY_LIMIT_POOL_TIMEOUT", 8),
    "stock_zt_pool_dtgc_em": ("A_SHARE_SUMMARY_LIMIT_POOL_TIMEOUT", 8),
}
WORKER = Path(__file__).resolve().parents[2] / "entrypoints/a_share_provider_worker.py"


def fetch_frame(operation: str, *args: Any, **kwargs: Any) -> Any:
    import pandas as pd

    setting, default = OPERATIONS[operation]
    try:
        timeout = max(1, min(60, int(os.environ.get(setting, str(default)))))
    except ValueError:
        timeout = default
    try:
        result = subprocess.run(
            [sys.executable, str(WORKER), operation],
            input=json.dumps({"args": args, "kwargs": kwargs}), capture_output=True,
            text=True, encoding="utf-8", timeout=timeout, check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"{operation} exceeded {timeout}s") from exc
    if result.returncode:
        raise RuntimeError(f"{operation} worker exited ({result.returncode})")
    if len(result.stdout) > 8 * 1024 * 1024:
        raise ValueError("oversized report provider response")
    value = json.loads(result.stdout)
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("columns"), list) or not isinstance(value.get("data"), list):
        raise ValueError("invalid report provider frame")
    return pd.DataFrame(value["data"], columns=value["columns"])


def main() -> None:
    operation = sys.argv[1]
    if operation not in OPERATIONS:
        raise ValueError("unsupported report provider operation")
    request = json.load(sys.stdin)
    import akshare as ak

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        frame = getattr(ak, operation)(*request["args"], **request["kwargs"])
    def encode(value: Any) -> Any:
        if hasattr(value, 'isoformat'):
            return value.isoformat()
        if hasattr(value, 'item'):
            return value.item()
        raise TypeError(f'unsupported provider value: {type(value).__name__}')

    payload = None if frame is None else {
        'columns': list(frame.columns),
        'data': frame.astype(object).where(frame.notna(), None).values.tolist(),
    }
    json.dump(payload, sys.stdout, ensure_ascii=False, allow_nan=False, default=encode)


if __name__ == "__main__":
    main()
