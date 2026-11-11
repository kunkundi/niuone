"""Shared A-share stock-universe configuration and classification helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable


STOCK_UNIVERSE_ENV = "DASHBOARD_STOCK_UNIVERSE"
DEFAULT_STOCK_UNIVERSE = "main_board"
STOCK_UNIVERSE_ORDER = ("st", "chi_next", "star_market", "main_board")
FULL_SUPPORTED_NON_ST_UNIVERSE = ("chi_next", "star_market", "main_board")
STOCK_UNIVERSE_OPTIONS = (
    {
        "id": "st",
        "label": "ST",
        "desc": "纳入主板、创业板和科创板中的 ST 股票",
        "color": "#f59e0b",
    },
    {
        "id": "chi_next",
        "label": "创业板",
        "desc": "纳入 300、301、302 开头的非 ST 股票",
        "color": "#8b5cf6",
    },
    {
        "id": "star_market",
        "label": "科创板",
        "desc": "纳入 688、689 开头的非 ST 股票",
        "color": "#06b6d4",
    },
    {
        "id": "main_board",
        "label": "主板",
        "desc": "纳入沪深主板非 ST 股票",
        "color": "#22c55e",
    },
)

_STOCK_UNIVERSE_IDS = frozenset(STOCK_UNIVERSE_ORDER)
_STOCK_UNIVERSE_LABELS = {
    str(option["id"]): str(option["label"])
    for option in STOCK_UNIVERSE_OPTIONS
}
_ST_NAME_RE = re.compile(r"^(?:S\*?ST|\*?ST)", re.IGNORECASE)


def normalize_stock_universe(value: object, *, require_selection: bool = True) -> str:
    """Return the stable comma-separated stock-universe representation."""
    if isinstance(value, str):
        raw_values: Iterable[object] = re.split(r"[,，\s]+", value)
    elif isinstance(value, Iterable):
        raw_values = value
    else:
        raw_values = ()

    selected: set[str] = set()
    for raw in raw_values:
        item = str(raw or "").strip().lower()
        if not item:
            continue
        if item not in _STOCK_UNIVERSE_IDS:
            raise ValueError(f"未知选股范围：{item}")
        selected.add(item)
    if not selected and require_selection:
        raise ValueError("选股范围至少勾选一项")
    return ",".join(item for item in STOCK_UNIVERSE_ORDER if item in selected)


def selected_stock_universe(value: object | None) -> tuple[str, ...]:
    """Parse a runtime setting, safely falling back to the legacy main-board scope."""
    raw = DEFAULT_STOCK_UNIVERSE if value is None or str(value).strip() == "" else value
    try:
        normalized = normalize_stock_universe(raw)
    except ValueError:
        normalized = DEFAULT_STOCK_UNIVERSE
    return tuple(item for item in normalized.split(",") if item)


def friendly_stock_universe(value: object | None) -> str:
    return "、".join(_STOCK_UNIVERSE_LABELS[item] for item in selected_stock_universe(value))


def stock_board(code: object) -> str:
    normalized = re.sub(r"\D", "", str(code or ""))[-6:]
    if normalized.startswith(("300", "301", "302")):
        return "chi_next"
    if normalized.startswith(("688", "689")):
        return "star_market"
    if normalized.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "main_board"
    return ""


def stock_name_is_st(name: object) -> bool:
    compact = re.sub(r"\s+", "", str(name or "")).upper()
    return bool(_ST_NAME_RE.match(compact))


def stock_in_universe(code: object, name: object, universe: object | None) -> bool:
    """Match four additive scopes: ST is its own cross-board category."""
    board = stock_board(code)
    if not board:
        return False
    selected = set(selected_stock_universe(universe))
    if stock_name_is_st(name):
        return "st" in selected
    return board in selected


def stock_universe_metadata(code: object, name: object) -> dict[str, object]:
    board = stock_board(code)
    return {
        "board": board,
        "board_label": _STOCK_UNIVERSE_LABELS.get(board, ""),
        "is_st": stock_name_is_st(name),
    }
