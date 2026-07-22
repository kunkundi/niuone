#!/usr/bin/env python3
"""行业板块今日主力净额（净流入/净流出前十）。

The Eastmoney industry-board endpoint exposes ``f62`` as today's main-fund
net amount in yuan.  Keep this shared snapshot small and explicit so the
indices page, industry-flow animation, and trading context all consume the
same metric and timestamp.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from dashboard_json_cache import read_json_cache, write_json_cache
from niuone_paths import get_dashboard_home

if __package__ == "app":
    from .dashboard.apis.cache import load_cached_payload
else:
    from dashboard.apis.cache import load_cached_payload


CACHE_BASE = get_dashboard_home(Path(__file__).resolve().parents[1]) / "cron" / "output"
# Do not mix the previous total-inflow-minus-total-outflow cache with the new
# main-fund metric.  The legacy file remains untouched for recovery/auditing.
CACHE_PATH = CACHE_BASE / "industry_main_money_flow_cache.json"
CACHE_TTL = 60

# The public delay host serves the same closing/main-net fields and is more
# reliable for server-side clients than the browser-oriented push2 hostname.
EASTMONEY_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
SOURCE_NAME = "东方财富行业板块主力净额"
SOURCE_URL = "https://data.eastmoney.com/bkzj/hy.html"
METRIC_NAME = "industry_main_net_flow"
METRIC_LABEL = "今日主力净额"
PAGE_SIZE = 100
MAX_PAGES = 5
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 8
MAX_REQUEST_ATTEMPTS = 2
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
FIELDS = (
    "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,"
    "f84,f87,f204,f205,f124"
)


def _beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def _empty_payload() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "metric": METRIC_NAME,
        "metric_label": METRIC_LABEL,
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "retention_date": _beijing_now().strftime("%Y-%m-%d"),
        "inflow": [],
        "outflow": [],
    }


def _is_current_day_payload(payload: dict[str, Any]) -> bool:
    generated_at = str(payload.get("generated_at") or "")
    if generated_at:
        return generated_at[:10] == _beijing_now().strftime("%Y-%m-%d")
    return not payload.get("inflow") and not payload.get("outflow")


def _read_current_day_cache(
    path: Path,
    ttl_seconds: int | float | None,
) -> dict[str, Any] | None:
    payload = read_json_cache(path, ttl_seconds)
    if payload is None or not _is_current_day_payload(payload):
        return None
    return payload


def _compute_current_day() -> dict[str, Any]:
    payload = _compute()
    return payload if _is_current_day_payload(payload) else _empty_payload()


def _finite_number(value: Any) -> float | None:
    try:
        text = str(value).replace(",", "").replace("%", "").strip()
        if not text or text in {"-", "--", "None", "nan"}:
            return None
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _yi(value: Any) -> float | None:
    number = _finite_number(value)
    return None if number is None else round(number / 100_000_000, 4)


def _download_json(
    url: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    curl_path: str | None = None,
) -> dict[str, Any]:
    """Download one bounded JSON response with a short retry budget.

    Eastmoney currently closes Python's default TLS connection on some hosts,
    while the system curl client remains accepted.  Prefer curl when present
    and retain urllib as a cross-platform fallback.
    """

    curl = curl_path if curl_path is not None else shutil.which("curl")
    last_error: Exception | None = None
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        try:
            if curl:
                completed = runner(
                    [
                        curl,
                        "--fail",
                        "--silent",
                        "--show-error",
                        "--location",
                        "--connect-timeout",
                        "4",
                        "--max-time",
                        str(REQUEST_TIMEOUT_SECONDS),
                        "--user-agent",
                        "Mozilla/5.0 NiuOne/1.0",
                        url,
                    ],
                    capture_output=True,
                    timeout=REQUEST_TIMEOUT_SECONDS + 2,
                    check=False,
                )
                if completed.returncode != 0:
                    detail = completed.stderr.decode("utf-8", errors="replace").strip()
                    raise RuntimeError(f"curl failed ({completed.returncode}): {detail[:160]}")
                raw = bytes(completed.stdout)
            else:
                request = Request(url, headers={"User-Agent": "Mozilla/5.0 NiuOne/1.0"})
                with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise RuntimeError("industry main-flow response is too large")
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("industry main-flow response is not an object")
            return payload
        except Exception as exc:
            last_error = exc
            if attempt + 1 < MAX_REQUEST_ATTEMPTS:
                sleep(0.25 * (attempt + 1))
    assert last_error is not None
    raise RuntimeError(
        f"industry main-flow request failed: {type(last_error).__name__}: {last_error}"
    ) from last_error


def _fetch_page(page: int) -> tuple[list[dict[str, Any]], int]:
    query = urlencode({
        "pn": page,
        "pz": PAGE_SIZE,
        "po": 1,
        "np": 1,
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "fltt": 2,
        "invt": 2,
        "fid": "f62",
        # Current Eastmoney industry-board universe.  The older t:2 filter is
        # no longer the industry list used by the official board-fund page.
        "fs": "m:90 s:4",
        "fields": FIELDS,
    })
    payload = _download_json(f"{EASTMONEY_URL}?{query}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("industry main-flow response has no data")
    diff = data.get("diff")
    if diff is None:
        diff = []
    if not isinstance(diff, list):
        raise RuntimeError("industry main-flow rows are invalid")
    rows = [row for row in diff if isinstance(row, dict)]
    total = int(_finite_number(data.get("total")) or len(rows))
    return rows, max(0, total)


def _source_time(rows: list[dict[str, Any]]) -> str:
    timestamps = [
        int(value)
        for row in rows
        if (value := _finite_number(row.get("f124"))) is not None and value > 0
    ]
    moment = (
        datetime.fromtimestamp(max(timestamps), tz=BEIJING_TZ)
        if timestamps
        else datetime.now(BEIJING_TZ)
    )
    return moment.replace(second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    name = str(raw.get("f14") or "").strip()
    main_net = _finite_number(raw.get("f62"))
    if not name or main_net is None or abs(main_net) < 1:
        return None
    main_net_yi = round(main_net / 100_000_000, 4)
    return {
        "code": str(raw.get("f12") or "").strip(),
        "name": name,
        "price": _finite_number(raw.get("f2")),
        "pct": _finite_number(raw.get("f3")),
        "net_flow": main_net,
        "net_flow_yi": main_net_yi,
        "main_net_flow_yi": main_net_yi,
        "main_net_ratio": _finite_number(raw.get("f184")),
        "super_large_net_flow_yi": _yi(raw.get("f66")),
        "super_large_net_ratio": _finite_number(raw.get("f69")),
        "large_net_flow_yi": _yi(raw.get("f72")),
        "large_net_ratio": _finite_number(raw.get("f75")),
        "middle_net_flow_yi": _yi(raw.get("f78")),
        "middle_net_ratio": _finite_number(raw.get("f81")),
        "small_net_flow_yi": _yi(raw.get("f84")),
        "small_net_ratio": _finite_number(raw.get("f87")),
        "leader": str(raw.get("f204") or "").strip(),
        "leader_code": str(raw.get("f205") or "").strip(),
    }


def _compute() -> dict[str, Any]:
    first_rows, total = _fetch_page(1)
    raw_rows = list(first_rows)
    page_count = min(MAX_PAGES, max(1, math.ceil(total / PAGE_SIZE)))
    for page in range(2, page_count + 1):
        page_rows, _ = _fetch_page(page)
        raw_rows.extend(page_rows)

    deduplicated: dict[str, dict[str, Any]] = {}
    for raw in raw_rows:
        row = _normalize_row(raw)
        if row is None:
            continue
        key = row["code"] or row["name"]
        deduplicated[key] = row
    rows = list(deduplicated.values())
    if len(rows) < 20:
        raise RuntimeError(f"industry main-flow returned only {len(rows)} usable rows")

    inflow = sorted(
        (row for row in rows if row["net_flow_yi"] > 0),
        key=lambda row: (-row["net_flow_yi"], row["name"]),
    )[:10]
    outflow = sorted(
        (row for row in rows if row["net_flow_yi"] < 0),
        key=lambda row: (row["net_flow_yi"], row["name"]),
    )[:10]
    return {
        "schema_version": 2,
        "metric": METRIC_NAME,
        "metric_label": METRIC_LABEL,
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "generated_at": _source_time(raw_rows),
        "inflow": inflow,
        "outflow": outflow,
        "count": len(rows),
    }


def fetch_money_flow(force_refresh: bool = False) -> dict[str, Any]:
    empty = _empty_payload()
    return load_cached_payload(
        CACHE_PATH,
        CACHE_TTL,
        compute=_compute_current_day,
        empty=empty,
        read_cache=_read_current_day_cache,
        write_cache=write_json_cache,
        force_refresh=force_refresh,
    )


if __name__ == "__main__":
    print(json.dumps(
        fetch_money_flow(force_refresh="--force-refresh" in sys.argv[1:]),
        ensure_ascii=False,
        indent=2,
    ))
