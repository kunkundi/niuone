"""Rebuild an existing Eastmoney classification snapshot outside the live scan."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zipfile import BadZipFile
from zoneinfo import ZoneInfo

from app.core.paths import get_dashboard_home
from app.core.process_lease import FileLease
from app.market_data.eastmoney_boards import (
    EASTMONEY_A_SHARE_FILTER, EastmoneyBoardError, EastmoneyBoardSnapshot,
    parse_eastmoney_board_payload, publish_board_snapshot, read_eastmoney_board_snapshot,
)
from app.market_data.exchange_listing import fetch_exchange_listing
from app.reports.a_share.calendar import trading_day_status

BEIJING = ZoneInfo("Asia/Shanghai")
CHART_URL = "https://data.eastmoney.com/dataapi/bkzj/getbkzj"
CHART_FIELDS = "f62,f13,f100,f102,f103,f124"
SEGMENTS = tuple(EASTMONEY_A_SHARE_FILTER.split(","))
BATCH_SIZE = 80
MAX_CODES = 10_000
MIN_LISTED = 5_000
MAX_BYTES = 4 * 1024 * 1024
JOB_BUDGET_SECONDS = 150
Chart = Callable[[str, float], tuple[int, list[Mapping[str, Any]]]]


def _segment(code: str) -> str:
    if not re.fullmatch(r"\d{6}", code) or code[:2] not in {"00", "30", "60", "68"}:
        raise EastmoneyBoardError("unknown security in the A-share classification universe")
    return {"00": SEGMENTS[0], "30": SEGMENTS[1], "60": SEGMENTS[2], "68": SEGMENTS[3]}[code[:2]]


def _verify_row(row: Mapping[str, Any]) -> str:
    code = row.get("f12")
    if not isinstance(code, str):
        raise EastmoneyBoardError("invalid classification security code")
    _segment(code)
    if str(row.get("f13")) != ("1" if code.startswith("6") else "0"):
        raise EastmoneyBoardError("classification exchange mismatch")
    if not all(isinstance(row.get(key), str) for key in ("f14", "f100", "f102", "f103")):
        raise EastmoneyBoardError("missing classification fields")
    if "f124" not in row:
        raise EastmoneyBoardError("missing source observation time")
    return code


def get_chart(query: str, deadline: float) -> tuple[int, list[Mapping[str, Any]]]:
    req = Request(CHART_URL + "?" + urlencode({"code": query, "key": CHART_FIELDS}), headers={
        "User-Agent": "Mozilla/5.0 NiuOne/1.0", "Referer": "https://data.eastmoney.com/bkzj/hy.html",
    })
    cause = None
    for _ in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            with urlopen(req, timeout=min(8, remaining)) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise EastmoneyBoardError("oversized classification response")
            payload = json.loads(raw)
            data = payload.get("data") if isinstance(payload, dict) and payload.get("rc", 0) == 0 else None
            rows = data.get("diff") if isinstance(data, dict) else None
            total = data.get("total") if isinstance(data, dict) else None
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise EastmoneyBoardError("invalid classification response")
            if type(total) is not int or not 0 < len(rows) <= total <= MAX_CODES:
                raise EastmoneyBoardError("invalid classification total")
            return total, rows
        except (OSError, ValueError, EastmoneyBoardError) as exc:
            cause = exc
    raise EastmoneyBoardError("classification request failed or exceeded its budget") from cause


def verified_snapshot(previous: EastmoneyBoardSnapshot, listed: set[str], session: str, *,
                      now: datetime, deadline: float, chart: Chart = get_chart) -> EastmoneyBoardSnapshot:
    """Old codes identify requests; all labels and observation times come from new responses."""
    if not MIN_LISTED <= len(listed) <= MAX_CODES:
        raise EastmoneyBoardError("incomplete exchange stock list")
    candidates = set(previous.stocks) | listed

    def fetch(query: str):
        if time.monotonic() >= deadline:
            raise EastmoneyBoardError("classification recovery deadline exceeded")
        return chart(query, deadline)

    groups = (EASTMONEY_A_SHARE_FILTER, *SEGMENTS)
    totals: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for group, (total, sample) in zip(groups, pool.map(fetch, groups)):
            codes = [_verify_row(row) for row in sample]
            if not 0 < len(codes) <= total <= MAX_CODES or len(set(codes)) != len(codes):
                raise EastmoneyBoardError("invalid classification market sample")
            if group in SEGMENTS and any(_segment(code) != group for code in codes):
                raise EastmoneyBoardError("classification segment mismatch")
            candidates.update(codes)
            totals[group] = total

    if (len(candidates) != totals[EASTMONEY_A_SHARE_FILTER] or
            any(sum(_segment(code) == group for code in candidates) != totals[group] for group in SEGMENTS)):
        raise EastmoneyBoardError("classification universe does not match the published market totals")

    def batch(codes: list[str]) -> list[Mapping[str, Any]]:
        query = ",".join(f"i:{int(code.startswith('6'))}.{code}" for code in codes)
        total, rows = fetch(query)
        if total != len(rows) or total != len(codes) or {_verify_row(row) for row in rows} != set(codes):
            raise EastmoneyBoardError("incomplete or mismatched classification batch")
        return rows

    ordered = sorted(candidates)
    by_code: dict[str, Mapping[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for rows in pool.map(batch, (ordered[i:i + BATCH_SIZE] for i in range(0, len(ordered), BATCH_SIZE))):
            by_code.update((row["f12"], row) for row in rows)
    stamps = []
    for code in listed:
        value = by_code[code]["f124"]
        if type(value) in (int, float) and math.isfinite(value) and value > 0:
            stamps.append(value)
    if len(stamps) < 0.98 * len(listed) or max(stamps) > now.timestamp() + 60:
        raise EastmoneyBoardError("missing or future classification source times")
    stamps.sort()
    # Require the typical listed observation as well as the newest one to belong to
    # this session. Suspended stocks may legitimately have an older last quote.
    observed = datetime.fromtimestamp(stamps[-1], BEIJING)
    median = datetime.fromtimestamp(stamps[len(stamps) // 2], BEIJING)
    close = datetime.fromisoformat(session + "T15:00:00").replace(tzinfo=BEIJING)
    if (observed.date().isoformat() != session or median.date().isoformat() != session
            or median < close - timedelta(minutes=5)):
        raise EastmoneyBoardError("classification source observations are stale")
    snapshot = parse_eastmoney_board_payload({"data": {"total": len(candidates), "diff": list(by_code.values())}},
                                             captured_at=observed.strftime("%Y-%m-%d %H:%M:%S"))
    industry = sum(bool(snapshot.stocks[code].industry) for code in listed)
    concepts = sum(bool(snapshot.stocks[code].concepts) for code in listed)
    if industry < 0.98 * len(listed) or concepts < 0.90 * len(listed):
        raise EastmoneyBoardError("insufficient listed-stock classification coverage")
    return snapshot


def recover_if_due(path: Path, now: datetime) -> str:
    current = now.astimezone(BEIJING)
    if current.hour < 15:
        return "等待收盘"
    status = trading_day_status(current, allow_refresh=False)
    if not status.get("calendar_cached") or not status["is_trading_day"]:
        return "无已确认的当日交易时段"
    session = str(status["date"])
    previous = read_eastmoney_board_snapshot(path)
    if previous is None:
        return "等待首次完整分类快照"
    if previous.captured_at >= session + " 15:00:00":
        return "当日收盘分类已就绪"
    lease = FileLease(path.with_name(path.name + ".recover.lock"), stale_after_seconds=300)
    if not lease.acquire():
        return "分类刷新进行中"
    try:
        previous = read_eastmoney_board_snapshot(path)
        if previous is None or previous.captured_at >= session + " 15:00:00":
            return "无需刷新"
        deadline = time.monotonic() + JOB_BUDGET_SECONDS
        listed = {code for code, _ in fetch_exchange_listing({session})}
        snapshot = verified_snapshot(previous, listed, session, now=current, deadline=deadline)
        if time.monotonic() >= deadline:
            raise EastmoneyBoardError("classification recovery deadline exceeded")
        published = publish_board_snapshot(path, snapshot)
        return f"{published.captured_at}，完整分类 {len(published.stocks)} 条"
    finally:
        lease.release()


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    path = get_dashboard_home(root) / "cron" / "output" / "eastmoney_stock_boards.json"
    try:
        result = recover_if_due(path, datetime.now(BEIJING))
    except EastmoneyBoardError as exc:
        print(f"行业与概念分类刷新失败：{exc}")
        return 1
    except (OSError, ValueError, RuntimeError, KeyError, IndexError, StopIteration, BadZipFile) as exc:
        print(f"行业与概念分类刷新失败：{type(exc).__name__}")
        return 1
    print("行业与概念分类：" + result)
    return 0
