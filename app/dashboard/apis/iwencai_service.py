"""Normalized Dashboard services backed by the iWencai query gateway."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

if __package__ and __package__.startswith("app."):
    from ...core.json_cache import read_json_cache, write_json_cache
    from ...market_data.iwencai_client import (
        IwencaiClient,
        IwencaiConfig,
        IwencaiError,
    )
else:
    from core.json_cache import read_json_cache, write_json_cache
    from market_data.iwencai_client import IwencaiClient, IwencaiConfig, IwencaiError


CN_TZ = ZoneInfo("Asia/Shanghai")
SOURCE_NAME = "同花顺问财"
DEFAULT_LIMIT = 100
MAX_LIMIT = 100
SOURCE_PAGE_LIMIT = 100
MAX_SOURCE_PAGES = 5
MAX_SEAT_SOURCE_PAGES = 10
DETAIL_FIELDS = (
    "list_date",
    "list_type",
    "reason",
    "buy_amount_yuan",
    "sell_amount_yuan",
    "net_amount_yuan",
    "buy_ratio_pct",
    "sell_ratio_pct",
    "net_ratio_pct",
)
SEAT_FIELDS = (
    "list_date",
    "list_type",
    "reason",
    "seat_name",
    "seat_type",
    "seat_category",
    "position",
    "side",
    "rank",
    "buy_rank",
    "sell_rank",
    "buy_amount_yuan",
    "sell_amount_yuan",
    "net_amount_yuan",
    "buy_ratio_pct",
    "sell_ratio_pct",
)


def dragon_tiger_archive_path(archive_dir: Path, trade_date: str) -> Path:
    """Return the stable per-trading-day archive path."""

    return archive_dir / f"{normalize_trade_date(trade_date)}.json"


def read_dragon_tiger_snapshot(
    path: Path,
    *,
    trade_date: str | None = None,
) -> dict[str, Any] | None:
    """Read a validated durable snapshot, optionally requiring one trade date."""

    payload = read_json_cache(path)
    if not payload or payload.get("available") is not True:
        return None
    if not isinstance(payload.get("items"), list) or not payload.get("items"):
        return None
    snapshot_date = str(payload.get("date") or "")
    if trade_date is not None and snapshot_date != normalize_trade_date(trade_date):
        return None
    result = dict(payload)
    result["items"] = deduplicate_dragon_tiger_items(result["items"])
    raw_items = [item for item in result["items"] if isinstance(item, Mapping)]
    has_native_seats = any(isinstance(item.get("seats"), list) for item in raw_items)
    _attach_seats(
        result["items"],
        {
            str(item.get("code") or ""): (
                item.get("seats")
                if isinstance(item.get("seats"), list)
                else item.get("institution_seats") or []
            )
            for item in raw_items
        },
    )
    result["seat_data_complete"] = bool(
        payload.get("seat_data_complete") is True or (has_native_seats and payload.get("seat_query"))
    )
    _update_seat_payload_summary(result, payload)
    result["returned_count"] = len(result["items"])
    result["unique_count"] = max(
        len(result["items"]),
        int(result.get("unique_count") or 0),
    )
    result["snapshot"] = True
    return result


def read_dragon_tiger_archive(
    archive_dir: Path,
    *,
    trade_date: str,
) -> dict[str, Any] | None:
    """Read one exact dated archive without falling back to another day."""

    payload = read_dragon_tiger_snapshot(
        dragon_tiger_archive_path(archive_dir, trade_date),
        trade_date=trade_date,
    )
    if payload:
        payload["archive"] = True
    return payload


def _preserve_seats(
    path: Path,
    stored: dict[str, Any],
) -> dict[str, Any]:
    """Keep previously archived seat rows when a same-day refresh is partial."""

    if not (stored.get("seat_error") or stored.get("institution_error")):
        return stored
    previous = read_json_cache(path)
    if not previous or str(previous.get("date") or "") != str(stored.get("date") or ""):
        return stored
    previous_items = previous.get("items")
    if not isinstance(previous_items, list):
        return stored
    previous_by_code = {}
    previous_has_native_seats = False
    for item in previous_items:
        if not isinstance(item, Mapping):
            continue
        raw_seats = item.get("seats")
        if isinstance(raw_seats, list):
            previous_has_native_seats = True
        else:
            raw_seats = item.get("institution_seats")
        if raw_seats:
            previous_by_code[str(item.get("code") or "")] = list(raw_seats)
    if not previous_by_code:
        return stored
    for item in stored.get("items") or []:
        if not isinstance(item, dict):
            continue
        previous_seats = previous_by_code.get(str(item.get("code") or ""))
        if previous_seats:
            item["seats"] = list(previous_seats)
    _attach_seats(
        stored.get("items") or [],
        {
            str(item.get("code") or ""): item.get("seats") or []
            for item in stored.get("items") or []
            if isinstance(item, Mapping)
        },
    )
    stored["seat_preserved_from_previous"] = True
    stored["institution_preserved_from_previous"] = True
    stored["seat_data_complete"] = bool(
        previous.get("seat_data_complete") is True or (
            previous_has_native_seats and previous.get("seat_query")
        )
    )
    _update_seat_payload_summary(stored, stored)
    return stored


def _snapshot_payload(path: Path, payload: Mapping[str, Any], *, archive: bool) -> dict[str, Any] | None:
    items = payload.get("items")
    if payload.get("available") is not True or not isinstance(items, list) or not items:
        return None
    trade_date = normalize_trade_date(str(payload.get("date") or ""))
    stored = dict(payload)
    stored["items"] = [dict(item) for item in items if isinstance(item, Mapping)]
    stored["date"] = trade_date
    stored["snapshot"] = True
    stored["archive"] = archive
    stored["snapshot_saved_at"] = datetime.now(CN_TZ).isoformat(timespec="seconds")
    return _preserve_seats(path, stored)


def write_dragon_tiger_snapshot(path: Path, payload: Mapping[str, Any]) -> bool:
    """Atomically persist only a complete, non-empty successful response."""

    stored = _snapshot_payload(path, payload, archive=False)
    if stored is None:
        return False
    write_json_cache(path, stored)
    return True


def write_dragon_tiger_archive(archive_dir: Path, payload: Mapping[str, Any]) -> bool:
    """Atomically store one successful snapshot under its trading date."""

    items = payload.get("items")
    if payload.get("available") is not True or not isinstance(items, list) or not items:
        return False
    path = dragon_tiger_archive_path(archive_dir, str(payload.get("date") or ""))
    stored = _snapshot_payload(path, payload, archive=True)
    if stored is None:
        return False
    write_json_cache(path, stored)
    return True


def normalize_trade_date(value: str | None, *, now: datetime | None = None) -> str:
    raw = str(value or "").strip()
    if not raw:
        current = now or datetime.now(CN_TZ)
        return current.astimezone(CN_TZ).strftime("%Y-%m-%d")
    compact = raw.replace("-", "")
    try:
        parsed = datetime.strptime(compact, "%Y%m%d")
    except ValueError as exc:
        raise ValueError("date 必须使用 YYYY-MM-DD") from exc
    return parsed.strftime("%Y-%m-%d")


def normalize_page(value: int | str) -> int:
    try:
        page = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("page 必须是正整数") from exc
    if page < 1 or page > 100:
        raise ValueError("page 必须在 1 到 100 之间")
    return page


def normalize_limit(value: int | str) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit 必须是正整数") from exc
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit 必须在 1 到 {MAX_LIMIT} 之间")
    return limit


def _number(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _streak_count(value: Any) -> int | None:
    """Normalize iWencai streak fields such as 2, 2.0, or ``2天``."""

    if value in (None, "", "--"):
        return None
    matched = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not matched:
        return None
    return max(0, int(float(matched.group(0))))


def _integer(value: Any) -> int | None:
    if value in (None, "", "--"):
        return None
    matched = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return int(float(matched.group(0))) if matched else None


def _max_streak(*values: Any) -> int | None:
    normalized = [count for value in values if (count := _streak_count(value)) is not None]
    return max(normalized) if normalized else None


def _dynamic_value(item: Mapping[str, Any], *prefixes: str) -> Any:
    for prefix in prefixes:
        if prefix in item:
            return item[prefix]
        for key, value in item.items():
            if str(key).startswith(prefix + "["):
                return value
    return None


def _iso_list_date(value: Any, fallback: str) -> str:
    compact = str(value or "").strip().replace("-", "")
    if len(compact) == 8 and compact.isdigit():
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
    return fallback


def _sector_parts(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        candidates = value
    else:
        candidates = str(value or "").replace(">", "/").split("/")
    return [str(part).strip() for part in candidates if str(part).strip()]


def _sector_values(item: Mapping[str, Any]) -> tuple[str, str]:
    parts = _sector_parts(
        item.get("所属同花顺行业")
        or item.get("所属行业")
        or item.get("板块")
    )
    return (parts[-1] if parts else "", " / ".join(parts))


def _normalize_item(
    item: Mapping[str, Any],
    trade_date: str,
    *,
    sector: str = "",
    sector_path: str = "",
) -> dict[str, Any]:
    return {
        "code": str(item.get("股票代码") or item.get("证券代码") or ""),
        "name": str(item.get("股票简称") or item.get("证券简称") or ""),
        "sector": sector,
        "sector_path": sector_path,
        "price": _number(item.get("最新价")),
        "change_pct": _number(item.get("最新涨跌幅")),
        "limit_up_streak": _streak_count(_dynamic_value(item, "连续涨停天数")),
        "limit_down_streak": _streak_count(
            _dynamic_value(item, "连续跌停天数", "最近连续跌停天数")
        ),
        "list_date": _iso_list_date(item.get("上榜日期"), trade_date),
        "list_type": str(item.get("榜单类型") or ""),
        "reason": str(item.get("上榜原因") or ""),
        "buy_amount_yuan": _number(_dynamic_value(item, "买入额", "龙虎榜买入额")),
        "sell_amount_yuan": _number(_dynamic_value(item, "卖出额", "龙虎榜卖出额")),
        "net_amount_yuan": _number(_dynamic_value(item, "净买入额", "龙虎榜净买入额")),
        "buy_ratio_pct": _number(_dynamic_value(item, "买入额占成交额比例")),
        "sell_ratio_pct": _number(_dynamic_value(item, "卖出额占成交额比例")),
        "net_ratio_pct": _number(_dynamic_value(item, "净买入额占成交额比例")),
    }


def _seat_side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if (
        text in {"b", "buy", "买", "买入", "买方"}
        or "买入" in text
        or "买方" in text
        or re.match(r"^买(?:\d+|[一二三四五])席位", text)
    ):
        return "buy"
    if (
        text in {"s", "sell", "卖", "卖出", "卖方"}
        or "卖出" in text
        or "卖方" in text
        or re.match(r"^卖(?:\d+|[一二三四五])席位", text)
    ):
        return "sell"
    return "aggregate"


def _seat_value(item: Mapping[str, Any], *prefixes: str) -> Any:
    return _dynamic_value(item, *prefixes)


def _seat_rank(value: Any, side: str) -> int | None:
    text = str(value or "").replace("，", ",")
    matched = re.search(rf"{side}\s*(\d+|[一二三四五])\s*席位", text)
    if not matched:
        return None
    token = matched.group(1)
    if token.isdigit():
        return int(token)
    return {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}.get(token)


def _seat_category(seat_name: str, seat_type: str = "") -> str:
    text = f"{seat_name} {seat_type}"
    if "机构" in text:
        return "institution"
    if "量化" in text:
        return "quant"
    if "游资" in text:
        return "hot_money"
    return "brokerage"


def _normalize_seat(
    item: Mapping[str, Any],
    trade_date: str,
) -> dict[str, Any] | None:
    seat_name = str(
        _seat_value(
            item,
            "交易营业部名称",
            "营业部名称",
            "席位名称",
            "机构名称",
        )
        or ""
    ).strip()
    seat_type = str(
        _seat_value(item, "营业部类型", "席位类型", "机构类型") or ""
    ).strip()
    buy_amount = _number(
        _seat_value(
            item,
            "营业部买入金额",
            "机构专用席位买入金额",
            "机构买入金额",
            "机构买入总额",
            "买入金额",
            "买入额",
        )
    )
    sell_amount = _number(
        _seat_value(
            item,
            "营业部卖出金额",
            "机构专用席位卖出金额",
            "机构卖出金额",
            "机构卖出总额",
            "卖出金额",
            "卖出额",
        )
    )
    net_amount = _number(
        _seat_value(
            item,
            "营业部净额",
            "机构专用席位净额",
            "机构净买入额",
            "机构买入净额",
            "净买入额",
            "净额",
        )
    )
    buy_count = _integer(
        _seat_value(item, "买方机构专用席位数", "买方机构席位数", "买入前5机构数量")
    )
    sell_count = _integer(
        _seat_value(item, "卖方机构专用席位数", "卖方机构席位数", "卖出前5机构数量")
    )
    if not seat_name:
        return None
    if net_amount is None and buy_amount is not None and sell_amount is not None:
        net_amount = buy_amount - sell_amount
    seat_position = str(_seat_value(item, "买卖席位") or "").strip()
    explicit_side = _seat_side(
        _seat_value(item, "买入/卖出方向", "买卖方向", "交易方向", "席位方向")
    )
    explicit_rank = _integer(_seat_value(item, "买入/卖出金额排名", "席位排名", "排名"))
    buy_rank = _seat_rank(seat_position, "买")
    sell_rank = _seat_rank(seat_position, "卖")
    if buy_rank is None and explicit_side == "buy":
        buy_rank = explicit_rank
    if sell_rank is None and explicit_side == "sell":
        sell_rank = explicit_rank
    if buy_rank is not None and sell_rank is not None:
        side = "both"
    elif buy_rank is not None:
        side = "buy"
    elif sell_rank is not None:
        side = "sell"
    else:
        side = explicit_side
    rank = buy_rank if buy_rank is not None else sell_rank if sell_rank is not None else explicit_rank
    if all(value is None for value in (buy_amount, sell_amount, net_amount, buy_count, sell_count, rank)):
        return None
    category = _seat_category(seat_name, seat_type)
    default_type = {
        "institution": "机构专用",
        "quant": "量化",
        "hot_money": "游资",
        "brokerage": "营业部",
    }[category]
    return {
        "list_date": _iso_list_date(
            _seat_value(item, "上榜日期", "交易日期"),
            trade_date,
        ),
        "list_type": str(_seat_value(item, "榜单类型") or ""),
        "reason": str(_seat_value(item, "上榜原因", "异动类型名称") or ""),
        "seat_name": seat_name,
        "seat_type": seat_type or default_type,
        "seat_category": category,
        "position": seat_position,
        "side": side,
        "rank": rank,
        "buy_rank": buy_rank,
        "sell_rank": sell_rank,
        "buy_amount_yuan": buy_amount,
        "sell_amount_yuan": sell_amount,
        "net_amount_yuan": net_amount,
        "buy_ratio_pct": _number(
            _seat_value(
                item,
                "买入金额占总成交比例",
                "买入额占成交额比例",
                "机构买入占比",
            )
        ),
        "sell_ratio_pct": _number(
            _seat_value(
                item,
                "卖出金额占总成交比例",
                "卖出额占成交额比例",
                "机构卖出占比",
            )
        ),
        "buy_seat_count": buy_count,
        "sell_seat_count": sell_count,
    }


def _normalize_stored_seat(item: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(item)
    side = str(record.get("side") or "aggregate")
    rank = _integer(record.get("rank"))
    buy_rank = _integer(record.get("buy_rank"))
    sell_rank = _integer(record.get("sell_rank"))
    if buy_rank is None and side in {"buy", "both"}:
        buy_rank = rank
    if sell_rank is None and side in {"sell", "both"}:
        sell_rank = rank
    if buy_rank is not None and sell_rank is not None:
        side = "both"
    elif buy_rank is not None:
        side = "buy"
    elif sell_rank is not None:
        side = "sell"
    seat_name = str(record.get("seat_name") or "未标注营业部")
    seat_type = str(record.get("seat_type") or "")
    category = str(record.get("seat_category") or _seat_category(seat_name, seat_type))
    record.update({
        "seat_name": seat_name,
        "seat_type": seat_type or ("机构专用" if category == "institution" else "营业部"),
        "seat_category": category,
        "position": str(record.get("position") or ""),
        "side": side,
        "rank": buy_rank if buy_rank is not None else sell_rank if sell_rank is not None else rank,
        "buy_rank": buy_rank,
        "sell_rank": sell_rank,
    })
    return record


def _seat_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(item.get(field) for field in SEAT_FIELDS)


def _seats_by_code(
    rows: list[Mapping[str, Any]],
    trade_date: str,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    seen: dict[str, set[tuple[Any, ...]]] = {}
    for row in rows:
        code = _stock_code(row)
        if not code:
            continue
        record = _normalize_seat(row, trade_date)
        if record is None:
            continue
        key = _seat_key(record)
        if key in seen.setdefault(code, set()):
            continue
        seen[code].add(key)
        grouped.setdefault(code, []).append(record)
    for records in grouped.values():
        records.sort(
            key=lambda record: (
                min(
                    int(record.get("buy_rank") or 99),
                    int(record.get("sell_rank") or 99),
                ),
                record.get("buy_rank") is None,
                str(record.get("seat_name") or ""),
            )
        )
    return grouped


def _sum_present(values: list[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return sum(present) if present else None


def _seat_summary_values(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    buy_records = [
        record for record in records
        if record.get("buy_rank") is not None or record.get("side") in {"buy", "both"}
    ]
    sell_records = [
        record for record in records
        if record.get("sell_rank") is not None or record.get("side") in {"sell", "both"}
    ]
    aggregate_records = [record for record in records if record.get("side") == "aggregate"]
    buy_count_values = [
        int(record.get("buy_seat_count"))
        for record in aggregate_records
        if record.get("buy_seat_count") is not None
    ]
    sell_count_values = [
        int(record.get("sell_seat_count"))
        for record in aggregate_records
        if record.get("sell_seat_count") is not None
    ]
    buy_count = max(buy_count_values, default=len(buy_records))
    sell_count = max(sell_count_values, default=len(sell_records))
    buy_amount = _sum_present(
        [record.get("buy_amount_yuan") for record in (buy_records or aggregate_records)]
    )
    sell_amount = _sum_present(
        [record.get("sell_amount_yuan") for record in (sell_records or aggregate_records)]
    )
    net_amount = _sum_present([record.get("net_amount_yuan") for record in records])
    if net_amount is None and (buy_amount is not None or sell_amount is not None):
        net_amount = (buy_amount or 0.0) - (sell_amount or 0.0)
    return {
        "record_count": len(records),
        "buy_seat_count": buy_count,
        "sell_seat_count": sell_count,
        "buy_amount_yuan": buy_amount,
        "sell_amount_yuan": sell_amount,
        "net_amount_yuan": net_amount,
    }


def _seat_summary(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    summary = _seat_summary_values(records)
    return {f"seat_{key}": value for key, value in summary.items()}


def _institution_summary(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    summary = _seat_summary_values(records)
    return {
        f"institution_{key}": value
        for key, value in summary.items()
    }


def _attach_seats(
    items: list[dict[str, Any]],
    seats_by_code: Mapping[str, Any],
) -> None:
    for item in items:
        code = str(item.get("code") or "")
        raw_records = seats_by_code.get(code)
        records = [
            _normalize_stored_seat(record)
            for record in raw_records or []
            if isinstance(record, Mapping)
        ]
        institutions = [record for record in records if record.get("seat_category") == "institution"]
        item["seats"] = records
        item.update(_seat_summary(records))
        item["institution_seats"] = institutions
        item.update(_institution_summary(institutions))


def _update_seat_payload_summary(
    target: dict[str, Any],
    source: Mapping[str, Any],
) -> None:
    items = [item for item in target.get("items") or [] if isinstance(item, Mapping)]
    target["seat_stock_count"] = sum(1 for item in items if item.get("seat_record_count"))
    target["seat_record_count"] = sum(int(item.get("seat_record_count") or 0) for item in items)
    target["institution_stock_count"] = sum(
        1 for item in items if item.get("institution_record_count")
    )
    target["institution_record_count"] = sum(
        int(item.get("institution_record_count") or 0) for item in items
    )
    seat_error = str(source.get("seat_error") or source.get("institution_error") or "")
    if seat_error and not target.get("seat_error"):
        target["seat_error"] = seat_error
    if target["seat_record_count"]:
        target["seat_available"] = True
    elif seat_error:
        target["seat_available"] = False
    elif "seat_available" in source:
        target["seat_available"] = source.get("seat_available") is True
    elif target["institution_record_count"]:
        target["seat_available"] = True
    else:
        target["seat_available"] = None
    if target["institution_record_count"] or target.get("seat_data_complete"):
        target["institution_available"] = target.get("seat_available")
    elif source.get("institution_error"):
        target["institution_available"] = False
    elif "institution_available" in source:
        target["institution_available"] = source.get("institution_available") is True
    else:
        target["institution_available"] = None


def _detail_from_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {field: item.get(field) for field in DETAIL_FIELDS}


def _detail_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(item.get(field) for field in DETAIL_FIELDS)


def _primary_rank(item: Mapping[str, Any]) -> tuple[int, int]:
    list_type = str(item.get("list_type") or "")
    return (
        2 if "单日" in list_type else 1 if "三日" in list_type else 0,
        1 if item.get("net_amount_yuan") is not None else 0,
    )


def deduplicate_dragon_tiger_items(items: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return one row per stock while retaining distinct leaderboard details."""

    grouped: dict[str, dict[str, Any]] = {}
    detail_keys: dict[str, set[tuple[Any, ...]]] = {}
    for index, source in enumerate(items):
        item = dict(source)
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or "").strip()
        stock_key = code or (f"name:{name}" if name else f"row:{index}")
        current = grouped.get(stock_key)
        if current is None:
            current = dict(item)
            current["details"] = []
            grouped[stock_key] = current
            detail_keys[stock_key] = set()
        elif _primary_rank(item) > _primary_rank(current):
            preserved_details = current["details"]
            preserved_sector = current.get("sector")
            preserved_sector_path = current.get("sector_path")
            preserved_limit_up_streak = current.get("limit_up_streak")
            preserved_limit_down_streak = current.get("limit_down_streak")
            current.update(item)
            current["details"] = preserved_details
            if not current.get("sector"):
                current["sector"] = preserved_sector
            if not current.get("sector_path"):
                current["sector_path"] = preserved_sector_path
            current["limit_up_streak"] = _max_streak(
                current.get("limit_up_streak"),
                preserved_limit_up_streak,
            )
            current["limit_down_streak"] = _max_streak(
                current.get("limit_down_streak"),
                preserved_limit_down_streak,
            )
        else:
            if not current.get("sector") and item.get("sector"):
                current["sector"] = item.get("sector")
            if not current.get("sector_path") and item.get("sector_path"):
                current["sector_path"] = item.get("sector_path")
            current["limit_up_streak"] = _max_streak(
                current.get("limit_up_streak"),
                item.get("limit_up_streak"),
            )
            current["limit_down_streak"] = _max_streak(
                current.get("limit_down_streak"),
                item.get("limit_down_streak"),
            )

        source_details = item.get("details")
        if not isinstance(source_details, list) or not source_details:
            source_details = [_detail_from_item(item)]
        for source_detail in source_details:
            if not isinstance(source_detail, Mapping):
                continue
            detail = {field: source_detail.get(field) for field in DETAIL_FIELDS}
            if not any(value not in (None, "") for value in detail.values()):
                continue
            key = _detail_key(detail)
            if key in detail_keys[stock_key]:
                continue
            detail_keys[stock_key].add(key)
            current["details"].append(detail)

    result = list(grouped.values())
    for item in result:
        item["detail_count"] = len(item["details"])
    result.sort(
        key=lambda item: (
            item.get("net_amount_yuan") is not None,
            item.get("net_amount_yuan") or 0.0,
        ),
        reverse=True,
    )
    return result


def _reported_count(result: Mapping[str, Any]) -> int:
    try:
        return int(result.get("code_count") or 0)
    except (TypeError, ValueError):
        return 0


def _stock_code(item: Mapping[str, Any]) -> str:
    return str(item.get("股票代码") or item.get("证券代码") or "").strip()


def _query_all_stock_rows(
    client: IwencaiClient,
    query: str,
    *,
    max_pages: int = MAX_SOURCE_PAGES,
) -> tuple[list[dict[str, Any]], int, str]:
    rows: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    reported_count = 0
    trace_id = ""
    for source_page in range(1, max_pages + 1):
        result = client.query(
            query,
            page=source_page,
            limit=SOURCE_PAGE_LIMIT,
        )
        if not trace_id:
            trace_id = str(result.get("trace_id") or "")
        reported_count = max(reported_count, _reported_count(result))
        page_rows = [item for item in result.get("datas", []) if isinstance(item, dict)]
        rows.extend(page_rows)
        seen_codes.update(filter(None, (_stock_code(item) for item in page_rows)))
        if not page_rows:
            break
        if len(page_rows) < SOURCE_PAGE_LIMIT and (
            not reported_count or len(seen_codes) >= reported_count
        ):
            break
    return rows, reported_count, trace_id


def _empty_payload(
    *,
    enabled: bool,
    trade_date: str,
    page: int,
    limit: int,
    error: str,
) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "available": False,
        "source": SOURCE_NAME,
        "date": trade_date,
        "page": page,
        "limit": limit,
        "reported_count": 0,
        "returned_count": 0,
        "has_more": False,
        "count_mismatch": False,
        "seat_available": False,
        "seat_data_complete": False,
        "seat_stock_count": 0,
        "seat_record_count": 0,
        "institution_available": False,
        "institution_stock_count": 0,
        "institution_record_count": 0,
        "items": [],
        "error": error,
    }


def fetch_dragon_tiger(
    trade_date: str | None = None,
    *,
    page: int | str = 1,
    limit: int | str = DEFAULT_LIMIT,
    env: Mapping[str, str] | None = None,
    client: IwencaiClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch one normalized daily dragon-tiger list without exposing free-form queries."""

    normalized_date = normalize_trade_date(trade_date, now=now)
    normalized_page = normalize_page(page)
    normalized_limit = normalize_limit(limit)
    values = os.environ if env is None else env
    try:
        config = IwencaiConfig.from_env(values)
    except IwencaiError as exc:
        return _empty_payload(
            enabled=False,
            trade_date=normalized_date,
            page=normalized_page,
            limit=normalized_limit,
            error=exc.code,
        )
    if not config.enabled:
        return _empty_payload(
            enabled=False,
            trade_date=normalized_date,
            page=normalized_page,
            limit=normalized_limit,
            error="iwencai_disabled",
        )
    if not config.api_key:
        return _empty_payload(
            enabled=True,
            trade_date=normalized_date,
            page=normalized_page,
            limit=normalized_limit,
            error="iwencai_not_configured",
        )

    parsed_date = datetime.strptime(normalized_date, "%Y-%m-%d")
    display_date = f"{parsed_date.year}年{parsed_date.month}月{parsed_date.day}日"
    query = (
        f"{display_date}龙虎榜上榜股票、上榜原因、龙虎榜买入金额、卖出金额、净买入额、"
        "连续涨停天数、最近连续跌停天数"
    )
    sector_query = f"{display_date}龙虎榜上榜股票、所属行业"
    seat_query = f"{display_date}龙虎榜营业部"
    active_client = client or IwencaiClient(config)
    try:
        raw_items, reported_count, trace_id = _query_all_stock_rows(active_client, query)
    except IwencaiError as exc:
        payload = _empty_payload(
            enabled=True,
            trade_date=normalized_date,
            page=normalized_page,
            limit=normalized_limit,
            error=exc.code,
        )
        if exc.status_code is not None:
            payload["status_code"] = exc.status_code
        return payload

    sector_by_code: dict[str, tuple[str, str]] = {}
    sector_error = ""
    try:
        sector_rows, _sector_reported_count, _sector_trace_id = _query_all_stock_rows(
            active_client,
            sector_query,
        )
    except IwencaiError as exc:
        sector_error = exc.code
        sector_rows = []
    for raw_item in sector_rows:
        code = _stock_code(raw_item)
        if code and code not in sector_by_code:
            sector_by_code[code] = _sector_values(raw_item)

    seat_error = ""
    seat_trace_id = ""
    seat_reported_count = 0
    try:
        seat_rows, seat_reported_count, seat_trace_id = _query_all_stock_rows(
            active_client,
            seat_query,
            max_pages=MAX_SEAT_SOURCE_PAGES,
        )
    except IwencaiError as exc:
        seat_error = exc.code
        seat_rows = []
    seats_by_code = _seats_by_code(seat_rows, normalized_date)

    normalized_items: list[dict[str, Any]] = []
    for raw_item in raw_items:
        sector, sector_path = sector_by_code.get(_stock_code(raw_item), ("", ""))
        normalized_items.append(
            _normalize_item(
                raw_item,
                normalized_date,
                sector=sector,
                sector_path=sector_path,
            )
        )
    all_items = deduplicate_dragon_tiger_items(normalized_items)
    _attach_seats(all_items, seats_by_code)
    unique_count = len(all_items)
    offset = (normalized_page - 1) * normalized_limit
    items = all_items[offset : offset + normalized_limit]
    returned_count = len(items)
    has_more = offset + returned_count < unique_count
    expected_returned_count = min(
        normalized_limit,
        max(0, reported_count - offset),
    )
    payload = {
        "enabled": True,
        "available": True,
        "source": SOURCE_NAME,
        "date": normalized_date,
        "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "query": query,
        "sector_query": sector_query,
        "seat_query": seat_query,
        "seat_data_complete": not seat_error,
        # Compatibility aliases for existing API consumers.
        "institution_query": seat_query,
        "page": normalized_page,
        "limit": normalized_limit,
        "reported_count": reported_count,
        "unique_count": unique_count,
        "returned_count": returned_count,
        "raw_returned_count": len(raw_items),
        "expected_returned_count": expected_returned_count,
        "has_more": has_more,
        "count_mismatch": unique_count != reported_count,
        "trace_id": trace_id,
        "seat_available": not seat_error,
        "seat_reported_count": seat_reported_count,
        "seat_raw_returned_count": len(seat_rows),
        "seat_stock_count": sum(1 for item in all_items if item.get("seat_record_count")),
        "seat_record_count": sum(int(item.get("seat_record_count") or 0) for item in all_items),
        "seat_trace_id": seat_trace_id,
        "institution_available": not seat_error,
        "institution_reported_count": sum(
            1 for item in all_items if item.get("institution_record_count")
        ),
        "institution_raw_returned_count": sum(
            int(item.get("institution_record_count") or 0) for item in all_items
        ),
        "institution_stock_count": sum(
            1 for item in all_items if item.get("institution_record_count")
        ),
        "institution_record_count": sum(
            int(item.get("institution_record_count") or 0) for item in all_items
        ),
        "institution_trace_id": seat_trace_id,
        "items": items,
    }
    if sector_error:
        payload["sector_error"] = sector_error
    if seat_error:
        payload["seat_error"] = seat_error
        payload["institution_error"] = seat_error
    return payload
