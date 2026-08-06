"""Bounded Eastmoney industry and concept classification snapshots."""
from __future__ import annotations

import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    from app.core.json_cache import read_json_cache, write_json_cache
except ImportError:  # pragma: no cover - standalone entrypoints add app/ to sys.path
    from core.json_cache import read_json_cache, write_json_cache


EASTMONEY_BOARD_URLS = (
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
)
EASTMONEY_A_SHARE_FILTER = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
EASTMONEY_BOARD_FIELDS = "f12,f13,f14,f100,f102,f103"
EASTMONEY_BOARD_SCHEMA_VERSION = 1
EASTMONEY_BOARD_CACHE_TTL_SECONDS = 6 * 60 * 60
EASTMONEY_BOARD_TIMEOUT_SECONDS = 10.0
EASTMONEY_BOARD_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
EASTMONEY_BOARD_PAGE_SIZE = 100
EASTMONEY_BOARD_MAX_PAGES = 80
EASTMONEY_BOARD_MAX_WORKERS = 4
EASTMONEY_BOARD_MAX_ATTEMPTS_PER_PAGE = 2
EASTMONEY_BOARD_SOURCE = "eastmoney_current_industry_concept"
_CACHE_LOCK = threading.Lock()


class EastmoneyBoardError(RuntimeError):
    """Raised when an Eastmoney board snapshot is unavailable or malformed."""


def _stock_code(value: Any) -> str:
    matched = re.search(r"\d{6}", str(value or ""))
    return matched.group(0) if matched else ""


def _label(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).strip()
    return "" if text.lower() in {"", "-", "--", "nan", "none", "null"} else text


def _concepts(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        values: Iterable[Any] = value.split(",")
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, Mapping)):
        values = value
    else:
        values = ()
    return tuple(dict.fromkeys(label for item in values if (label := _label(item))))


@dataclass(frozen=True)
class EastmoneyStockBoard:
    code: str
    name: str = ""
    industry: str = ""
    region: str = ""
    concepts: tuple[str, ...] = ()

    @property
    def themes(self) -> tuple[str, ...]:
        """Prefer Eastmoney concepts and use its industry only when concepts are empty."""
        return self.concepts or ((self.industry,) if self.industry else ())


@dataclass(frozen=True)
class EastmoneyBoardSnapshot:
    captured_at: str
    as_of_date: str
    stocks: Mapping[str, EastmoneyStockBoard]
    source: str = EASTMONEY_BOARD_SOURCE
    stale: bool = False

    def subset(self, codes: Iterable[str]) -> dict[str, EastmoneyStockBoard]:
        targets = {_stock_code(code) for code in codes}
        targets.discard("")
        return {code: self.stocks[code] for code in targets if code in self.stocks}

    def industry_map(self, codes: Iterable[str]) -> dict[str, str]:
        return {
            code: stock.industry
            for code, stock in self.subset(codes).items()
            if stock.industry
        }

    def theme_map(self, codes: Iterable[str]) -> dict[str, tuple[str, ...]]:
        return {
            code: stock.themes
            for code, stock in self.subset(codes).items()
            if stock.themes
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EASTMONEY_BOARD_SCHEMA_VERSION,
            "source": self.source,
            "captured_at": self.captured_at,
            "as_of_date": self.as_of_date,
            "stocks": {
                code: {
                    "name": stock.name,
                    "industry": stock.industry,
                    "region": stock.region,
                    "concepts": list(stock.concepts),
                }
                for code, stock in self.stocks.items()
            },
        }


def parse_eastmoney_board_payload(
    payload: Mapping[str, Any],
    *,
    captured_at: str,
) -> EastmoneyBoardSnapshot:
    """Parse one complete Eastmoney A-share classification response."""
    data = payload.get("data") if isinstance(payload, Mapping) else None
    rows = data.get("diff") if isinstance(data, Mapping) else None
    total = data.get("total") if isinstance(data, Mapping) else None
    if not isinstance(rows, list) or not rows:
        raise EastmoneyBoardError("Eastmoney returned no board classifications")
    try:
        expected_total = int(total)
    except (TypeError, ValueError):
        expected_total = len(rows)
    if expected_total > len(rows):
        raise EastmoneyBoardError(
            f"Eastmoney board snapshot is incomplete ({len(rows)}/{expected_total})"
        )
    stocks: dict[str, EastmoneyStockBoard] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        code = _stock_code(row.get("f12"))
        if not code:
            continue
        stocks[code] = EastmoneyStockBoard(
            code=code,
            name=_label(row.get("f14")),
            industry=_label(row.get("f100")),
            region=_label(row.get("f102")),
            concepts=_concepts(row.get("f103")),
        )
    if not stocks:
        raise EastmoneyBoardError("Eastmoney board snapshot contained no valid stocks")
    captured = str(captured_at or "")[:19]
    return EastmoneyBoardSnapshot(
        captured_at=captured,
        as_of_date=captured[:10],
        stocks=stocks,
    )


def _snapshot_from_cache(payload: Mapping[str, Any]) -> EastmoneyBoardSnapshot:
    if (
        payload.get("schema_version") != EASTMONEY_BOARD_SCHEMA_VERSION
        or payload.get("source") != EASTMONEY_BOARD_SOURCE
        or not isinstance(payload.get("stocks"), Mapping)
    ):
        raise EastmoneyBoardError("Eastmoney board cache schema is invalid")
    stocks: dict[str, EastmoneyStockBoard] = {}
    for raw_code, raw in payload["stocks"].items():
        code = _stock_code(raw_code)
        if not code or not isinstance(raw, Mapping):
            continue
        stocks[code] = EastmoneyStockBoard(
            code=code,
            name=_label(raw.get("name")),
            industry=_label(raw.get("industry")),
            region=_label(raw.get("region")),
            concepts=_concepts(raw.get("concepts")),
        )
    if not stocks:
        raise EastmoneyBoardError("Eastmoney board cache contained no valid stocks")
    return EastmoneyBoardSnapshot(
        captured_at=str(payload.get("captured_at") or "")[:19],
        as_of_date=str(payload.get("as_of_date") or "")[:10],
        stocks=stocks,
        source=EASTMONEY_BOARD_SOURCE,
    )


def read_eastmoney_board_snapshot(path: Path) -> EastmoneyBoardSnapshot | None:
    payload = read_json_cache(Path(path), None)
    if payload is None:
        return None
    try:
        return _snapshot_from_cache(payload)
    except EastmoneyBoardError:
        return None


def _download_payload(
    url: str,
    *,
    page: int,
    timeout_seconds: float,
    opener: Callable[..., Any],
) -> Mapping[str, Any]:
    params = {
        "pn": str(max(1, int(page))),
        "pz": str(EASTMONEY_BOARD_PAGE_SIZE),
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": EASTMONEY_A_SHARE_FILTER,
        "fields": EASTMONEY_BOARD_FIELDS,
    }
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={
            "User-Agent": "Mozilla/5.0 NiuOne/1.0",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://quote.eastmoney.com/center/gridlist.html",
            "Connection": "close",
        },
    )
    with opener(request, timeout=max(1.0, float(timeout_seconds))) as response:
        body = response.read(EASTMONEY_BOARD_MAX_RESPONSE_BYTES + 1)
    if len(body) > EASTMONEY_BOARD_MAX_RESPONSE_BYTES:
        raise EastmoneyBoardError("Eastmoney board response exceeded 16 MiB")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EastmoneyBoardError("Eastmoney returned invalid board JSON") from exc
    if not isinstance(parsed, Mapping):
        raise EastmoneyBoardError("Eastmoney returned an invalid board payload")
    return parsed


def fetch_eastmoney_board_snapshot(
    *,
    timeout_seconds: float = EASTMONEY_BOARD_TIMEOUT_SECONDS,
    opener: Callable[..., Any] = urllib.request.urlopen,
    now: datetime | None = None,
) -> EastmoneyBoardSnapshot:
    """Fetch one complete current snapshot with bounded paging and host fallback."""
    def fetch_page(page: int) -> Mapping[str, Any]:
        last_error: Exception | None = None
        for attempt in range(EASTMONEY_BOARD_MAX_ATTEMPTS_PER_PAGE):
            for index, url in enumerate(EASTMONEY_BOARD_URLS):
                try:
                    return _download_payload(
                        url,
                        page=page,
                        timeout_seconds=timeout_seconds,
                        opener=opener,
                    )
                except (
                    EastmoneyBoardError,
                    urllib.error.HTTPError,
                    urllib.error.URLError,
                    TimeoutError,
                    OSError,
                ) as exc:
                    last_error = exc
                    if index + 1 < len(EASTMONEY_BOARD_URLS):
                        time.sleep(0.1)
            if attempt + 1 < EASTMONEY_BOARD_MAX_ATTEMPTS_PER_PAGE:
                time.sleep(min(0.25 * (2**attempt), 1.0))
        raise EastmoneyBoardError(
            f"Eastmoney board page {page} is unavailable "
            f"({type(last_error).__name__})"
        ) from last_error

    first = fetch_page(1)
    first_data = first.get("data") if isinstance(first, Mapping) else None
    try:
        total = int(first_data.get("total")) if isinstance(first_data, Mapping) else 0
    except (TypeError, ValueError):
        total = 0
    page_count = math.ceil(total / EASTMONEY_BOARD_PAGE_SIZE) if total > 0 else 0
    if not 1 <= page_count <= EASTMONEY_BOARD_MAX_PAGES:
        raise EastmoneyBoardError("Eastmoney board page count is outside the safety limit")
    payloads: list[Mapping[str, Any]] = [first]
    if page_count > 1:
        with ThreadPoolExecutor(max_workers=EASTMONEY_BOARD_MAX_WORKERS) as pool:
            payloads.extend(pool.map(fetch_page, range(2, page_count + 1)))
    rows_by_code: dict[str, Mapping[str, Any]] = {}
    for payload in payloads:
        data = payload.get("data") if isinstance(payload, Mapping) else None
        rows = data.get("diff") if isinstance(data, Mapping) else None
        if not isinstance(rows, list):
            raise EastmoneyBoardError("Eastmoney returned an invalid board page")
        for row in rows:
            code = _stock_code(row.get("f12")) if isinstance(row, Mapping) else ""
            if code:
                rows_by_code[code] = row
    captured_at = (now or datetime.now()).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return parse_eastmoney_board_payload(
        {"data": {"total": total, "diff": list(rows_by_code.values())}},
        captured_at=captured_at,
    )


def load_eastmoney_board_snapshot(
    *,
    cache_path: Path,
    ttl_seconds: int | float = EASTMONEY_BOARD_CACHE_TTL_SECONDS,
    allow_stale: bool = True,
    fetcher: Callable[[], EastmoneyBoardSnapshot] = fetch_eastmoney_board_snapshot,
) -> EastmoneyBoardSnapshot:
    """Use a fresh private cache, refresh atomically, then optionally use stale Eastmoney data."""
    path = Path(cache_path)
    with _CACHE_LOCK:
        fresh_payload = read_json_cache(path, ttl_seconds)
        if fresh_payload is not None:
            try:
                return _snapshot_from_cache(fresh_payload)
            except EastmoneyBoardError:
                pass
        stale = read_eastmoney_board_snapshot(path) if allow_stale else None
        try:
            snapshot = fetcher()
            write_json_cache(path, snapshot.to_dict())
            archive_path = path.parent / "eastmoney_board_snapshots" / f"{snapshot.as_of_date}.json"
            write_json_cache(archive_path, snapshot.to_dict())
            return snapshot
        except (
            EastmoneyBoardError,
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
        ):
            if stale is not None:
                return EastmoneyBoardSnapshot(
                    captured_at=stale.captured_at,
                    as_of_date=stale.as_of_date,
                    stocks=stale.stocks,
                    source=stale.source,
                    stale=True,
                )
            raise


__all__ = [
    "EASTMONEY_BOARD_CACHE_TTL_SECONDS",
    "EASTMONEY_BOARD_MAX_ATTEMPTS_PER_PAGE",
    "EASTMONEY_BOARD_SOURCE",
    "EastmoneyBoardError",
    "EastmoneyBoardSnapshot",
    "EastmoneyStockBoard",
    "fetch_eastmoney_board_snapshot",
    "load_eastmoney_board_snapshot",
    "parse_eastmoney_board_payload",
    "read_eastmoney_board_snapshot",
]
