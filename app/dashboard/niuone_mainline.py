"""Public read model for the dedicated NiuOne mainline page."""
from __future__ import annotations

import math
from typing import Any, Mapping

try:
    from app.market_data.eastmoney_concept_boards import (
        normalize_eastmoney_concept_name,
    )
except ImportError:  # pragma: no cover - standalone entrypoints add app/ to sys.path
    from market_data.eastmoney_concept_boards import (
        normalize_eastmoney_concept_name,
    )


NIUONE_MAINLINE_VIEW_SCHEMA_VERSION = 14
NIUONE_MAINLINE_THEME_LIMIT = 5


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result):
        return None
    return int(result) if result.is_integer() else round(result, 4)


def _integer(value: Any) -> int:
    number = _number(value)
    return max(0, int(number or 0))


def _observed_count(value: Any) -> int | None:
    number = _number(value)
    return number if isinstance(number, int) and number >= 0 else None


def _text(value: Any, limit: int = 160) -> str:
    return str(value or "").strip()[:limit]


def _eastmoney_board_view(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    name = _text(value.get("name"), 80)
    rank = _integer(value.get("rank"))
    if not name or rank <= 0:
        return None
    up_count = _integer(value.get("up_count"))
    down_count = _integer(value.get("down_count"))
    flat_count = _integer(value.get("flat_count"))
    quote_count = up_count + down_count + flat_count
    leader_code = _text(value.get("leader_code"), 12)
    leader_name = _text(value.get("leader_name"), 40)
    return {
        "board_code": _text(value.get("code"), 16),
        "board_name": name,
        "rank": rank,
        "change_pct": _number(value.get("change_pct")),
        "main_net_yi": _number(value.get("main_net_yi")),
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "breadth_pct": (
            round(up_count / quote_count * 100, 2) if quote_count else None
        ),
        "leader": (
            {
                "code": leader_code,
                "name": leader_name,
                "change_pct": _number(value.get("leader_change_pct")),
            }
            if leader_code or leader_name
            else None
        ),
    }


def _eastmoney_signal_view(
    value: Any,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    default = {
        "available": False,
        "status": "not_collected",
        "source": "eastmoney_concept_board_rank",
        "source_url": "",
        "captured_at": "",
        "quote_generated_at": "",
        "sort": "change_pct_desc",
        "total_count": 0,
        "covered_count": 0,
        "stale": False,
        "matched_theme_count": 0,
    }
    if not isinstance(value, Mapping):
        return default, {}
    boards = [
        board
        for raw in list(value.get("boards") or [])[:100]
        if (board := _eastmoney_board_view(raw)) is not None
    ]
    available = value.get("available") is not False and bool(boards)
    signal = {
        "available": available,
        "status": (
            "available"
            if available
            else _text(value.get("status"), 40) or "upstream_unavailable"
        ),
        "source": _text(value.get("source"), 60)
        or "eastmoney_concept_board_rank",
        "source_url": _text(value.get("source_url"), 240),
        "captured_at": _text(value.get("captured_at"), 19),
        "quote_generated_at": _text(value.get("quote_generated_at"), 19),
        "sort": _text(value.get("sort"), 32) or "change_pct_desc",
        "total_count": _integer(value.get("total_count")),
        "covered_count": _integer(value.get("covered_count")) or len(boards),
        "stale": value.get("stale") is True,
        "matched_theme_count": 0,
    }
    lookup: dict[str, dict[str, Any]] = {}
    if available:
        for board in boards:
            normalized = normalize_eastmoney_concept_name(
                board.get("board_name")
            )
            if normalized and normalized not in lookup:
                lookup[normalized] = board
    return signal, lookup


def _strong_stock_view(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    code = _text(value.get("code"), 12)
    name = _text(value.get("name"), 40)
    if not code and not name:
        return None
    return {
        "code": code,
        "name": name,
        "strong_score": _number(value.get("strong_score")),
        "change_pct": _number(value.get("change_pct")),
        "attribution_score": _number(value.get("attribution_score")),
        "attribution_weight": _number(value.get("attribution_weight")),
        "role": _text(value.get("role"), 16),
    }


def _theme_view(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    industry = _text(value.get("industry"), 80)
    if not industry:
        return None
    strong_stocks = [
        stock
        for raw in list(value.get("strong_stocks") or [])[:5]
        if (stock := _strong_stock_view(raw)) is not None
    ]
    leader_stock = next(
        (dict(stock) for stock in strong_stocks if stock.get("role") == "leader"),
        dict(strong_stocks[0]) if strong_stocks else None,
    )
    today_leaders = [
        stock
        for raw in list(value.get("today_leaders") or [])[:5]
        if (stock := _strong_stock_view(raw)) is not None
    ]
    today_leader_stock = dict(today_leaders[0]) if today_leaders else None
    continued_codes = [
        _text(code, 12)
        for code in list(value.get("continued_core_codes") or [])[:5]
        if _text(code, 12)
    ]
    member_count = _integer(value.get("member_count"))
    effective_strong_count = _number(value.get("effective_strong_count"))
    effective_breadth_pct = _number(value.get("effective_breadth_pct"))
    if effective_breadth_pct is None and effective_strong_count is not None and member_count > 0:
        effective_breadth_pct = round(float(effective_strong_count) / member_count * 100, 2)
    if effective_breadth_pct is not None:
        effective_breadth_pct = round(min(100.0, max(0.0, float(effective_breadth_pct))), 2)
    today_breadth_pct = _number(value.get("today_breadth_pct"))
    if today_breadth_pct is not None:
        today_breadth_pct = round(min(100.0, max(0.0, float(today_breadth_pct))), 2)
    today_data_coverage = _number(value.get("today_data_coverage"))
    if today_data_coverage is not None:
        today_data_coverage = round(min(1.0, max(0.0, float(today_data_coverage))), 4)
    return {
        "industry": industry,
        "score": _number(value.get("score")),
        "state": _text(value.get("state"), 32),
        "raw_state": _text(value.get("raw_state"), 32),
        "intraday_state": _text(value.get("intraday_state"), 32),
        "niuone_lifecycle_stage": _text(
            value.get("niuone_lifecycle_stage"), 24
        ),
        "niuone_lifecycle_label": _text(
            value.get("niuone_lifecycle_label"), 24
        ),
        "niuone_lifecycle_order": _integer(
            value.get("niuone_lifecycle_order")
        ),
        "niuone_lifecycle_entry_policy": _text(
            value.get("niuone_lifecycle_entry_policy"), 32
        ),
        "member_count": member_count,
        "attributed_member_count": _number(value.get("attributed_member_count")),
        "eligible_data": value.get("eligible_data") is True,
        "today_eligible_data": value.get("today_eligible_data") is True,
        "today_quote_count": _integer(value.get("today_quote_count")),
        "today_data_coverage": today_data_coverage,
        "today_attributed_data_coverage": _number(
            value.get("today_attributed_data_coverage")
        ),
        "today_up_count": _integer(value.get("today_up_count")),
        "today_1_5pct_count": _integer(value.get("today_1_5pct_count")),
        "today_3pct_count": _integer(value.get("today_3pct_count")),
        "today_5pct_count": _integer(value.get("today_5pct_count")),
        "today_breadth_pct": today_breadth_pct,
        "today_attributed_quote_count": _number(
            value.get("today_attributed_quote_count")
        ),
        "today_attributed_up_count": _number(
            value.get("today_attributed_up_count")
        ),
        "today_attributed_breadth_pct": _number(
            value.get("today_attributed_breadth_pct")
        ),
        "today_adjusted_breadth_pct": _number(
            value.get("today_adjusted_breadth_pct")
        ),
        "today_median_change_pct": _number(value.get("today_median_change_pct")),
        "today_strength_score": _number(value.get("today_strength_score")),
        "today_leadership_score": _number(value.get("today_leadership_score")),
        "strong_stock_count": _integer(value.get("strong_stock_count")),
        "raw_strong_stock_count": _integer(value.get("raw_strong_stock_count")),
        "attributed_strong_stock_count": _number(
            value.get("attributed_strong_stock_count")
        ),
        "effective_strong_count": effective_strong_count,
        "effective_breadth_pct": effective_breadth_pct,
        "leader_concentration": _number(value.get("leader_concentration")),
        "single_stock_dominated": value.get("single_stock_dominated") is True,
        "confirmation_count": _integer(value.get("confirmation_count")),
        "intraday_confirmation_count": _integer(value.get("intraday_confirmation_count")),
        "cross_day_persistent": value.get("cross_day_persistent") is True,
        "cross_day_confirmed": value.get("cross_day_confirmed") is True,
        "mainline_confirmed": value.get("mainline_confirmed") is True,
        "core_overlap_count": _integer(value.get("core_overlap_count")),
        "core_overlap_ratio": _number(value.get("core_overlap_ratio")),
        "continued_core_codes": continued_codes,
        "as_of_date": _text(value.get("as_of_date"), 10),
        "previous_as_of_date": _text(value.get("previous_as_of_date"), 10),
        "score_change": _number(value.get("score_change")),
        "flow_net_yi": _number(value.get("flow_net_yi")),
        "leader_stock": leader_stock,
        "strong_stocks": strong_stocks,
        "today_leader_stock": today_leader_stock,
        "today_leaders": today_leaders,
        "related_themes": [
            _text(label, 80)
            for label in list(value.get("related_themes") or [])[:5]
            if _text(label, 80)
        ],
    }


def _theme_driver_codes(theme: Mapping[str, Any], *, today: bool) -> list[str]:
    key = "today_leaders" if today else "strong_stocks"
    return [
        _text(stock.get("code"), 12)
        for stock in list(theme.get(key) or [])[:3]
        if isinstance(stock, Mapping) and _text(stock.get("code"), 12)
    ]


def _diverse_themes(
    ordered: list[dict[str, Any]],
    *,
    today: bool,
) -> list[dict[str, Any]]:
    """Collapse label clones driven by the same attributed stock cohort."""
    selected: list[dict[str, Any]] = []
    for raw_theme in ordered:
        theme = dict(raw_theme)
        drivers = _theme_driver_codes(theme, today=today)
        duplicate: dict[str, Any] | None = None
        for existing in selected:
            existing_drivers = _theme_driver_codes(existing, today=today)
            if not drivers or not existing_drivers:
                continue
            shared = set(drivers).intersection(existing_drivers)
            overlap_base = min(len(drivers), len(existing_drivers))
            high_overlap = bool(
                overlap_base and len(shared) / overlap_base >= 0.6
            )
            same_leader = drivers[0] == existing_drivers[0]
            leader_key = "today_leader_stock" if today else "leader_stock"
            leader = theme.get(leader_key) if isinstance(theme.get(leader_key), Mapping) else {}
            existing_leader = (
                existing.get(leader_key)
                if isinstance(existing.get(leader_key), Mapping)
                else {}
            )
            weak_duplicate_leader = bool(
                same_leader
                and min(
                    float(leader.get("attribution_weight") or 0.0),
                    float(existing_leader.get("attribution_weight") or 0.0),
                ) < 0.35
            )
            if high_overlap or weak_duplicate_leader:
                duplicate = existing
                break
        if duplicate is None:
            selected.append(theme)
            continue
        related = list(duplicate.get("related_themes") or [])
        label = _text(theme.get("industry"), 80)
        if label and label not in related:
            related.append(label)
        duplicate["related_themes"] = related[:5]
    return selected


def _coverage_reason_view(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    count = _integer(value.get("count"))
    if count <= 0:
        return None
    label = _text(value.get("label"), 60)
    if not label:
        return None
    return {
        "key": _text(value.get("key"), 40),
        "label": label,
        "count": count,
        "description": _text(value.get("description"), 160),
    }


def build_niuone_mainline_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the explicit public fields used by the NiuOne mainline page."""
    payload = payload if isinstance(payload, Mapping) else {}
    context = payload.get("niuone_context") if isinstance(payload.get("niuone_context"), Mapping) else {}
    market = context.get("market") if isinstance(context.get("market"), Mapping) else {}
    mainline = context.get("mainline") if isinstance(context.get("mainline"), Mapping) else {}
    today_primary = _text(mainline.get("today_primary"), 80)
    raw_themes = context.get("themes") if isinstance(context.get("themes"), Mapping) else {}
    theme_views = [theme for value in raw_themes.values() if (theme := _theme_view(value)) is not None]
    eastmoney_signal, eastmoney_lookup = _eastmoney_signal_view(
        payload.get("eastmoney_concept_signal")
    )
    matched_theme_count = 0
    for theme in theme_views:
        normalized = normalize_eastmoney_concept_name(theme.get("industry"))
        board = eastmoney_lookup.get(normalized)
        if board is not None:
            theme["eastmoney"] = dict(board)
            matched_theme_count += 1
    eastmoney_signal["matched_theme_count"] = matched_theme_count
    themes = _diverse_themes(
        sorted(
            theme_views,
            key=lambda theme: float(theme.get("score") or 0),
            reverse=True,
        ),
        today=False,
    )
    today_themes = _diverse_themes(sorted(
        (
            theme
            for theme in theme_views
            if theme.get("today_eligible_data") and theme.get("today_strength_score") is not None
        ),
        key=lambda theme: (
            float(theme.get("today_strength_score") or 0),
            float(theme.get("today_median_change_pct") or 0),
        ),
        reverse=True,
    ), today=True)
    reference_pool_count = _integer(payload.get("reference_pool_count"))
    mapped_stock_count = _integer(context.get("mapped_stock_count"))
    diagnostics = (
        context.get("coverage_diagnostics")
        if isinstance(context.get("coverage_diagnostics"), Mapping)
        else {}
    )
    uncovered_stock_count = max(0, reference_pool_count - mapped_stock_count)
    uncovered_reasons = [
        reason
        for value in list(diagnostics.get("reasons") or [])[:8]
        if (reason := _coverage_reason_view(value)) is not None
    ]
    if uncovered_stock_count and not uncovered_reasons:
        uncovered_reasons = [{
            "key": "legacy_unclassified",
            "label": "历史快照未记录明细",
            "count": uncovered_stock_count,
            "description": "下一次题材强度扫描将按数据处理阶段补齐原因",
        }]
    coverage = (
        round(mapped_stock_count / reference_pool_count, 4)
        if reference_pool_count
        else None
    )
    return {
        "schema_version": NIUONE_MAINLINE_VIEW_SCHEMA_VERSION,
        "available": bool(context),
        "generated_at": _text(payload.get("generated_at"), 19),
        "quote_generated_at": _text(payload.get("quote_generated_at"), 19),
        "refresh_mode": _text(payload.get("refresh_mode"), 32),
        "calculation_duration_ms": _integer(payload.get("calculation_duration_ms")),
        "as_of_date": _text(context.get("as_of_date"), 10),
        "reference_stock_universe_label": _text(payload.get("reference_stock_universe_label"), 120),
        "market": {
            "score": _number(market.get("score")),
            "state": _text(market.get("state"), 32),
            "raw_state": _text(market.get("raw_state"), 32),
            "hard_stop": market.get("hard_stop") is True,
            "breadth_score": _number(market.get("breadth_score")),
            "median_change_pct": _number(market.get("median_change_pct")),
            "limit_up": _observed_count(market.get("limit_up")),
            "limit_down": _observed_count(market.get("limit_down")),
        },
        "mainline": {
            "mode": _text(mainline.get("mode"), 20) or "none",
            "primary": _text(mainline.get("primary"), 80),
            "primary_score": _number(mainline.get("primary_score")),
            "secondary": _text(mainline.get("secondary"), 80),
            "secondary_score": _number(mainline.get("secondary_score")),
            "score_gap": _number(mainline.get("score_gap")),
            "reason": _text(mainline.get("reason"), 200),
            "intraday_primary": _text(mainline.get("intraday_primary"), 80),
            "intraday_primary_score": _number(mainline.get("intraday_primary_score")),
            "today_primary": today_primary,
            "today_primary_score": _number(mainline.get("today_primary_score")),
            "today_primary_breadth_pct": _number(mainline.get("today_primary_breadth_pct")),
            "today_observation_reason": (
                "今日强度仅作观察，不改变原有跨日主线确认门槛"
                if today_primary
                else ""
            ),
        },
        "theme_count": _integer(context.get("theme_count")),
        "strong_stock_count": _integer(context.get("strong_stock_count")),
        "eastmoney_concept_signal": eastmoney_signal,
        "themes": themes[:NIUONE_MAINLINE_THEME_LIMIT],
        "today_themes": today_themes[:NIUONE_MAINLINE_THEME_LIMIT],
        "data_quality": {
            "reference_pool_count": reference_pool_count,
            "reference_analysis_count": _integer(payload.get("reference_analysis_count")),
            "prepared_stock_count": _integer(diagnostics.get("prepared_stock_count")),
            "mapped_stock_count": mapped_stock_count,
            "unmapped_stock_count": uncovered_stock_count,
            "uncovered_reasons": uncovered_reasons,
            "coverage": coverage,
        },
    }
