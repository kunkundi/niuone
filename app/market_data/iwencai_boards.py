"""Validated iWencai fallback snapshots for A-share classifications."""
from __future__ import annotations

import math
import re
import threading
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from app.core.json_cache import read_json_cache, write_json_cache
    from app.market_data.iwencai_client import (
        IwencaiClient,
        IwencaiConfig,
        IwencaiError,
    )
except ImportError:  # pragma: no cover - standalone entrypoints add app/ to sys.path
    from core.json_cache import read_json_cache, write_json_cache
    from market_data.iwencai_client import IwencaiClient, IwencaiConfig, IwencaiError


IWENCAI_BOARD_QUERY = "全部A股，股票代码，股票简称，所属同花顺行业，所属概念"
IWENCAI_BOARD_SCHEMA_VERSION = 1
IWENCAI_BOARD_CACHE_TTL_SECONDS = 24 * 60 * 60
IWENCAI_BOARD_PAGE_SIZE = 100
IWENCAI_BOARD_MAX_PAGES = 80
IWENCAI_BOARD_MAX_WORKERS = 2
IWENCAI_BOARD_SOURCE = "iwencai_current_industry_concept"
CN_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
_CACHE_LOCK = threading.Lock()


class IwencaiBoardError(RuntimeError):
    """Raised when a complete iWencai classification snapshot is unavailable."""


def _stock_code(value: Any) -> str:
    matched = re.search(r"\d{6}", str(value or ""))
    return matched.group(0) if matched else ""


def _label(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).strip()
    return "" if text.lower() in {"", "-", "--", "nan", "none", "null"} else text


def _labels(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates: Iterable[Any] = re.split(r"[,，;；]", value)
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, Mapping)):
        candidates = value
    else:
        candidates = ()
    return tuple(dict.fromkeys(label for item in candidates if (label := _label(item))))


@dataclass(frozen=True)
class IwencaiStockBoard:
    code: str
    name: str = ""
    industry: str = ""
    concepts: tuple[str, ...] = ()

    @property
    def themes(self) -> tuple[str, ...]:
        return self.concepts or ((self.industry,) if self.industry else ())


@dataclass(frozen=True)
class IwencaiBoardSnapshot:
    captured_at: str
    as_of_date: str
    stocks: Mapping[str, IwencaiStockBoard]
    source: str = IWENCAI_BOARD_SOURCE
    stale: bool = False

    def subset(self, codes: Iterable[str]) -> dict[str, IwencaiStockBoard]:
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
            "schema_version": IWENCAI_BOARD_SCHEMA_VERSION,
            "source": self.source,
            "captured_at": self.captured_at,
            "as_of_date": self.as_of_date,
            "stocks": {
                code: {
                    "name": stock.name,
                    "industry": stock.industry,
                    "concepts": list(stock.concepts),
                }
                for code, stock in self.stocks.items()
            },
        }


def parse_iwencai_board_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    expected_total: int,
    captured_at: str,
) -> IwencaiBoardSnapshot:
    """Parse and require one complete iWencai A-share classification result."""

    stocks: dict[str, IwencaiStockBoard] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        code = _stock_code(row.get("股票代码") or row.get("证券代码"))
        if not code:
            continue
        industry_labels = _labels(row.get("所属同花顺行业") or row.get("所属行业"))
        industry_set = set(industry_labels)
        concepts = tuple(
            label
            for label in _labels(row.get("所属概念") or row.get("概念"))
            if label not in industry_set
        )
        stocks[code] = IwencaiStockBoard(
            code=code,
            name=_label(row.get("股票简称") or row.get("证券简称")),
            industry=industry_labels[-1] if industry_labels else "",
            concepts=concepts,
        )
    if expected_total <= 0:
        raise IwencaiBoardError("iWencai did not report an A-share classification count")
    if len(stocks) < expected_total:
        raise IwencaiBoardError(
            f"iWencai board snapshot is incomplete ({len(stocks)}/{expected_total})"
        )
    captured = str(captured_at or "")[:19]
    return IwencaiBoardSnapshot(
        captured_at=captured,
        as_of_date=captured[:10],
        stocks=stocks,
    )


def _snapshot_from_cache(payload: Mapping[str, Any]) -> IwencaiBoardSnapshot:
    if (
        payload.get("schema_version") != IWENCAI_BOARD_SCHEMA_VERSION
        or payload.get("source") != IWENCAI_BOARD_SOURCE
        or not isinstance(payload.get("stocks"), Mapping)
    ):
        raise IwencaiBoardError("iWencai board cache schema is invalid")
    stocks: dict[str, IwencaiStockBoard] = {}
    for raw_code, raw in payload["stocks"].items():
        code = _stock_code(raw_code)
        if not code or not isinstance(raw, Mapping):
            continue
        stocks[code] = IwencaiStockBoard(
            code=code,
            name=_label(raw.get("name")),
            industry=_label(raw.get("industry")),
            concepts=_labels(raw.get("concepts")),
        )
    if not stocks:
        raise IwencaiBoardError("iWencai board cache contained no valid stocks")
    return IwencaiBoardSnapshot(
        captured_at=str(payload.get("captured_at") or "")[:19],
        as_of_date=str(payload.get("as_of_date") or "")[:10],
        stocks=stocks,
    )


def read_iwencai_board_snapshot(path: Path) -> IwencaiBoardSnapshot | None:
    payload = read_json_cache(Path(path), None)
    if payload is None:
        return None
    try:
        return _snapshot_from_cache(payload)
    except IwencaiBoardError:
        return None


def _reported_count(payload: Mapping[str, Any]) -> int:
    try:
        return int(payload.get("code_count") or 0)
    except (TypeError, ValueError):
        return 0


def fetch_iwencai_board_snapshot(
    *,
    config: IwencaiConfig | None = None,
    client: IwencaiClient | None = None,
    now: datetime | None = None,
) -> IwencaiBoardSnapshot:
    """Fetch all current A-share classifications through the official gateway."""

    resolved_config = config or IwencaiConfig.from_env()
    if not resolved_config.enabled:
        raise IwencaiBoardError("iWencai board fallback is disabled")
    if not resolved_config.api_key:
        raise IwencaiBoardError("iWencai board fallback API key is missing")
    active_client = client or IwencaiClient(resolved_config)

    def fetch_page(page: int) -> Mapping[str, Any]:
        try:
            return active_client.query(
                IWENCAI_BOARD_QUERY,
                page=page,
                limit=IWENCAI_BOARD_PAGE_SIZE,
                is_cache=True,
                expand_index=False,
            )
        except IwencaiError as exc:
            raise IwencaiBoardError(
                f"iWencai board page {page} is unavailable ({exc.code})"
            ) from exc

    first = fetch_page(1)
    total = _reported_count(first)
    page_count = math.ceil(total / IWENCAI_BOARD_PAGE_SIZE) if total > 0 else 0
    if not 1 <= page_count <= IWENCAI_BOARD_MAX_PAGES:
        raise IwencaiBoardError("iWencai board page count is outside the safety limit")
    payloads: list[Mapping[str, Any]] = [first]
    if page_count > 1:
        workers = min(
            IWENCAI_BOARD_MAX_WORKERS,
            max(1, int(resolved_config.max_concurrency)),
        )
        with ThreadPoolExecutor(max_workers=workers) as pool:
            payloads.extend(pool.map(fetch_page, range(2, page_count + 1)))

    rows: list[Mapping[str, Any]] = []
    for page, payload in enumerate(payloads, start=1):
        page_total = _reported_count(payload)
        if page_total and page_total != total:
            raise IwencaiBoardError("iWencai board result count changed during paging")
        page_rows = payload.get("datas") if isinstance(payload, Mapping) else None
        if not isinstance(page_rows, list):
            raise IwencaiBoardError(f"iWencai returned an invalid board page {page}")
        rows.extend(row for row in page_rows if isinstance(row, Mapping))
    current = now or datetime.now(CN_TZ)
    if current.tzinfo is not None:
        current = current.astimezone(CN_TZ)
    captured_at = current.strftime("%Y-%m-%d %H:%M:%S")
    return parse_iwencai_board_rows(
        rows,
        expected_total=total,
        captured_at=captured_at,
    )


def load_iwencai_board_snapshot(
    *,
    cache_path: Path,
    ttl_seconds: int | float = IWENCAI_BOARD_CACHE_TTL_SECONDS,
    allow_stale: bool = True,
    fetcher: Callable[[], IwencaiBoardSnapshot] = fetch_iwencai_board_snapshot,
) -> IwencaiBoardSnapshot:
    """Use a fresh cache, refresh atomically, then optionally use stale data."""

    path = Path(cache_path)
    with _CACHE_LOCK:
        fresh_payload = read_json_cache(path, ttl_seconds)
        if fresh_payload is not None:
            try:
                return _snapshot_from_cache(fresh_payload)
            except IwencaiBoardError:
                pass
        stale = read_iwencai_board_snapshot(path) if allow_stale else None
        try:
            snapshot = fetcher()
            write_json_cache(path, snapshot.to_dict())
            archive_path = path.parent / "iwencai_board_snapshots" / f"{snapshot.as_of_date}.json"
            write_json_cache(archive_path, snapshot.to_dict())
            return snapshot
        except (IwencaiBoardError, IwencaiError, OSError):
            if stale is not None:
                return IwencaiBoardSnapshot(
                    captured_at=stale.captured_at,
                    as_of_date=stale.as_of_date,
                    stocks=stale.stocks,
                    source=stale.source,
                    stale=True,
                )
            raise


__all__ = [
    "IWENCAI_BOARD_CACHE_TTL_SECONDS",
    "IWENCAI_BOARD_QUERY",
    "IWENCAI_BOARD_SOURCE",
    "IwencaiBoardError",
    "IwencaiBoardSnapshot",
    "IwencaiStockBoard",
    "fetch_iwencai_board_snapshot",
    "load_iwencai_board_snapshot",
    "parse_iwencai_board_rows",
    "read_iwencai_board_snapshot",
]
