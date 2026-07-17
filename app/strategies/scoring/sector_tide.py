"""Sector Tide (板块潮汐) market, sector, and stock-relative-strength strategy."""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from typing import Any, Mapping

from ..sector_tide_risk import (
    SECTOR_TIDE_ABSOLUTE_POSITION_CAP_PCT,
    SECTOR_TIDE_EXECUTION_BUFFER_PCT,
    downside_gap_buffer_pct,
    effective_loss_distance_pct,
    risk_sized_position_cap_pct,
    sector_tide_risk_budget,
    structural_stop_distance_pct,
)
from .common import safe_float, safe_round, with_strategy_profile


SECTOR_TIDE_STRATEGY_IDS = frozenset({"tide_leader", "tide_rotation", "tide_recovery"})
SECTOR_TIDE_MIN_MEMBERS = 3
SECTOR_TIDE_MIN_ROWS = 55
SECTOR_TIDE_DRAGON_TIGER_MAX_SECTOR_ADJUSTMENT = 2.5
SECTOR_TIDE_DRAGON_TIGER_MAX_STOCK_ADJUSTMENT = 0.35


def _mean(values: list[float], default: float = 0.0) -> float:
    return statistics.mean(values) if values else default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _clamp_signed(value: float) -> float:
    return _clamp(value, -1.0, 1.0)


def _industry_name(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).strip()
    for suffix in ("行业", "板块", "概念", "指数"):
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            text = text[: -len(suffix)]
    return text


def _stock_code(value: Any) -> str:
    matched = re.search(r"\d{6}", str(value or ""))
    return matched.group(0) if matched else ""


def _dragon_tiger_direction_strength(
    *,
    net_amount: Any,
    buy_amount: Any,
    sell_amount: Any,
    net_ratio_pct: Any = None,
) -> float | None:
    """Normalize one leaderboard balance without letting absolute size dominate."""
    net = safe_float(net_amount)
    buy = safe_float(buy_amount)
    sell = safe_float(sell_amount)
    ratio = safe_float(net_ratio_pct)
    if net is None and buy is not None and sell is not None:
        net = buy - sell
    if net is None:
        return None
    if ratio is not None:
        return _clamp_signed(ratio / 15.0)
    gross = max(0.0, buy or 0.0) + max(0.0, sell or 0.0)
    if gross > 0:
        return _clamp_signed((net / gross) / 0.40)
    return _clamp_signed(net / 50_000_000.0)


def _dragon_tiger_item_signal(
    item: Mapping[str, Any],
    *,
    seat_data_complete: bool,
) -> dict[str, Any]:
    """Build a bounded confirmation signal from the main list and top-five seats."""
    main_strength = _dragon_tiger_direction_strength(
        net_amount=item.get("net_amount_yuan"),
        buy_amount=item.get("buy_amount_yuan"),
        sell_amount=item.get("sell_amount_yuan"),
        net_ratio_pct=item.get("net_ratio_pct"),
    )
    components: list[tuple[float, float]] = []
    if main_strength is not None:
        components.append((main_strength, 0.70))

    if seat_data_complete and int(safe_float(item.get("seat_record_count")) or 0) > 0:
        seat_strength = _dragon_tiger_direction_strength(
            net_amount=item.get("seat_net_amount_yuan"),
            buy_amount=item.get("seat_buy_amount_yuan"),
            sell_amount=item.get("seat_sell_amount_yuan"),
        )
        if seat_strength is not None:
            components.append((seat_strength, 0.20))
    if seat_data_complete and int(safe_float(item.get("institution_record_count")) or 0) > 0:
        institution_strength = _dragon_tiger_direction_strength(
            net_amount=item.get("institution_net_amount_yuan"),
            buy_amount=item.get("institution_buy_amount_yuan"),
            sell_amount=item.get("institution_sell_amount_yuan"),
        )
        if institution_strength is not None:
            components.append((institution_strength, 0.10))

    total_weight = sum(weight for _value, weight in components)
    strength = (
        sum(value * weight for value, weight in components) / total_weight
        if total_weight > 0
        else 0.0
    )
    net = safe_float(item.get("net_amount_yuan"))
    buy = safe_float(item.get("buy_amount_yuan"))
    sell = safe_float(item.get("sell_amount_yuan"))
    if net is None and buy is not None and sell is not None:
        net = buy - sell
    ratio = safe_float(item.get("net_ratio_pct"))
    if ratio is None and net is not None:
        gross = max(0.0, buy or 0.0) + max(0.0, sell or 0.0)
        ratio = net / gross * 100 if gross > 0 else None
    signal = "positive" if strength >= 0.15 else ("negative" if strength <= -0.15 else "neutral")
    return {
        "listed": True,
        "score": round(50 + strength * 35, 2),
        "strength": round(strength, 4),
        "signal": signal,
        "confidence": round(total_weight, 2),
        "net_amount_yuan": net,
        "net_ratio_pct": safe_round(ratio, 3),
        "seat_net_amount_yuan": safe_float(item.get("seat_net_amount_yuan")),
        "institution_net_amount_yuan": safe_float(item.get("institution_net_amount_yuan")),
        "seat_record_count": int(safe_float(item.get("seat_record_count")) or 0),
        "institution_record_count": int(safe_float(item.get("institution_record_count")) or 0),
    }


def _dragon_tiger_context(
    snapshot: Any,
    members: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Map an exact prior-day archive onto this scan's stock and industry universe."""
    source = snapshot if isinstance(snapshot, Mapping) else {}
    items = source.get("items") if isinstance(source.get("items"), list) else []
    available = source.get("available") is True and bool(items)
    member_by_code = {
        _stock_code(member.get("code")): member
        for member in members
        if _stock_code(member.get("code"))
    }
    stock_signals: dict[str, dict[str, Any]] = {}
    sector_signals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seat_data_complete = source.get("seat_data_complete") is True
    if available:
        for raw_item in items:
            if not isinstance(raw_item, Mapping):
                continue
            code = _stock_code(raw_item.get("code"))
            member = member_by_code.get(code)
            if not code or member is None or code in stock_signals:
                continue
            signal = _dragon_tiger_item_signal(
                raw_item,
                seat_data_complete=seat_data_complete,
            )
            stock_signals[code] = signal
            industry = str(member.get("industry") or "")
            if industry:
                sector_signals[industry].append(signal)

    sectors: dict[str, dict[str, Any]] = {}
    for industry, signals in sector_signals.items():
        average_strength = _mean([float(signal["strength"]) for signal in signals])
        support = min(1.0, len(signals) / 2.0)
        strength = average_strength * support
        sectors[industry] = {
            "listed_count": len(signals),
            "positive_count": sum(1 for signal in signals if signal["signal"] == "positive"),
            "negative_count": sum(1 for signal in signals if signal["signal"] == "negative"),
            "score": round(50 + strength * 35, 2),
            "strength": round(strength, 4),
            "adjustment": round(
                strength * SECTOR_TIDE_DRAGON_TIGER_MAX_SECTOR_ADJUSTMENT,
                3,
            ),
        }

    metadata = {
        "available": available,
        "source": str(source.get("source") or "local_dragon_tiger_archive"),
        "as_of_date": str(source.get("date") or ""),
        "requested_date": str(source.get("requested_date") or source.get("date") or ""),
        "archive": source.get("archive") is True,
        "item_count": len(items),
        "matched_stock_count": len(stock_signals),
        "matched_sector_count": len(sectors),
        "seat_data_complete": seat_data_complete,
        "error": str(source.get("error") or ""),
        "usage": "previous_trading_day_confirmation",
    }
    return metadata, stock_signals, sectors


def _return_pct(rows: list[dict[str, Any]], lookback: int) -> float | None:
    if len(rows) <= lookback:
        return None
    current = safe_float(rows[-1].get("close"))
    base = safe_float(rows[-lookback - 1].get("close"))
    if current is None or base is None or base <= 0:
        return None
    return (current / base - 1) * 100


def _percentile(value: float, population: list[float]) -> float:
    clean = sorted(float(item) for item in population if item is not None)
    if not clean:
        return 50.0
    if len(clean) == 1:
        return 50.0
    below = sum(1 for item in clean if item < value)
    equal = sum(1 for item in clean if item == value)
    return _clamp((below + max(0, equal - 1) / 2) / (len(clean) - 1) * 100)


def _atr(rows: list[dict[str, Any]], lookback: int = 14) -> float | None:
    if len(rows) < 2:
        return None
    true_ranges: list[float] = []
    for idx in range(max(1, len(rows) - lookback), len(rows)):
        high = safe_float(rows[idx].get("high"))
        low = safe_float(rows[idx].get("low"))
        prev_close = safe_float(rows[idx - 1].get("close"))
        if high is None or low is None or prev_close is None:
            continue
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return _mean(true_ranges) if true_ranges else None


def _member_metrics(item: dict[str, Any]) -> dict[str, Any] | None:
    rows = item.get("rows") if isinstance(item.get("rows"), list) else []
    if len(rows) < SECTOR_TIDE_MIN_ROWS:
        return None
    latest = rows[-1]
    close = safe_float(latest.get("close"))
    ema20 = safe_float(latest.get("ema20"))
    ema50 = safe_float(latest.get("ema50"))
    ret5 = _return_pct(rows, 5)
    ret20 = _return_pct(rows, 20)
    if close is None or close <= 0 or ret5 is None or ret20 is None:
        return None
    recent_volumes = [safe_float(row.get("volume")) for row in rows[-5:]]
    prior_volumes = [safe_float(row.get("volume")) for row in rows[-25:-5]]
    recent_volumes = [value for value in recent_volumes if value is not None and value >= 0]
    prior_volumes = [value for value in prior_volumes if value is not None and value >= 0]
    volume_ratio = _mean(recent_volumes) / _mean(prior_volumes) if prior_volumes and _mean(prior_volumes) > 0 else 1.0
    prior_highs = [safe_float(row.get("high")) for row in rows[-21:-1]]
    prior_highs = [value for value in prior_highs if value is not None and value > 0]
    quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
    return {
        "code": str(item.get("code") or latest.get("symbol_code") or ""),
        "name": str(item.get("name") or latest.get("stock_name") or ""),
        "industry": _industry_name(item.get("industry") or latest.get("industry")),
        "ret5": ret5,
        "ret20": ret20,
        "above_ema20": bool(ema20 and close >= ema20),
        "trend_aligned": bool(ema20 and ema50 and close >= ema20 >= ema50),
        "new_high20": bool(prior_highs and close >= max(prior_highs)),
        "volume_ratio": volume_ratio,
        "amount": safe_float(quote.get("amount")) or safe_float(latest.get("quote_amount")) or 0.0,
        "change_pct": safe_float(quote.get("change_pct")) or safe_float(latest.get("change_pct")) or 0.0,
    }


def _flow_map(flow_rows: Any) -> dict[str, float]:
    if isinstance(flow_rows, dict):
        rows = [*(flow_rows.get("inflow") or []), *(flow_rows.get("outflow") or [])]
    else:
        rows = flow_rows if isinstance(flow_rows, list) else []
    out: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _industry_name(row.get("name") or row.get("industry") or row.get("行业"))
        value = safe_float(row.get("net_flow_yi") if row.get("net_flow_yi") is not None else row.get("net_flow"))
        if name and value is not None:
            out[name] = value
    return out


def _matched_flow(industry: str, flows: dict[str, float]) -> float | None:
    if industry in flows:
        return flows[industry]
    matches = [value for name, value in flows.items() if industry in name or name in industry]
    return _mean(matches) if matches else None


def _market_context(
    members: list[dict[str, Any]],
    market_snapshot: dict[str, Any],
    previous_market: dict[str, Any] | None,
) -> dict[str, Any]:
    up = int(safe_float(market_snapshot.get("up")) or 0)
    down = int(safe_float(market_snapshot.get("down")) or 0)
    active = up + down
    breadth_score = up / active * 100 if active else 50.0
    median_change = safe_float(market_snapshot.get("median_change_pct")) or 0.0
    median_score = _clamp(50 + median_change * 20)
    limit_up = int(safe_float(market_snapshot.get("limit_up")) or 0)
    limit_down = int(safe_float(market_snapshot.get("limit_down")) or 0)
    limit_total = limit_up + limit_down
    limit_score = 50 + (limit_up - limit_down) / limit_total * 50 if limit_total else 50.0
    core_count = int(safe_float(market_snapshot.get("core_index_count")) or 0)
    below_count = int(safe_float(market_snapshot.get("index_below_ma20_count")) or 0)
    index_score = 100 - below_count / core_count * 100 if core_count else 50.0
    market_ret20 = _mean([float(member["ret20"]) for member in members])
    trend_score = _clamp(50 + market_ret20 * 4)
    participation_score = _clamp(50 + (_mean([float(member["volume_ratio"]) for member in members], 1.0) - 1) * 50)
    score = (
        index_score * 0.25
        + breadth_score * 0.25
        + median_score * 0.15
        + limit_score * 0.15
        + trend_score * 0.10
        + participation_score * 0.10
    )
    index_break = core_count >= 3 and below_count >= 2 and (safe_float(market_snapshot.get("index_average_change_pct")) or 0) <= -0.5
    breadth_break = down >= max(100, int(up * 1.5)) and median_change <= -0.8
    limit_spread = limit_down >= max(5, limit_up)
    hard_stop = bool(index_break and breadth_break and limit_spread)
    raw_state = "defensive" if hard_stop or score < 40 else ("offensive" if score >= 65 and breadth_score >= 55 else "rotation")

    previous_market = previous_market if isinstance(previous_market, dict) else {}
    previous_state = str(previous_market.get("state") or "")
    previous_raw = str(previous_market.get("raw_state") or previous_state)
    confirmation_count = int(previous_market.get("confirmation_count") or 0) + 1 if raw_state == previous_raw else 1
    if hard_stop:
        state = "defensive"
    elif previous_state == "defensive" and raw_state != "defensive":
        state = "recovery"
    elif confirmation_count >= 2 or not previous_state:
        state = raw_state
    else:
        state = previous_state

    risk_budget = sector_tide_risk_budget(state)
    return {
        "score": round(score, 2),
        "raw_state": raw_state,
        "state": state,
        "confirmation_count": confirmation_count,
        "hard_stop": hard_stop,
        "allow_new_buys": state != "defensive" and not hard_stop,
        **risk_budget,
        "breadth_score": round(breadth_score, 2),
        "median_change_pct": round(median_change, 3),
        "limit_up": limit_up,
        "limit_down": limit_down,
        "core_index_count": core_count,
        "index_below_ma20_count": below_count,
        "market_ret20_pct": round(market_ret20, 2),
    }


def build_sector_tide_context(
    prepared_items: list[dict[str, Any]],
    *,
    market_snapshot: dict[str, Any] | None = None,
    flow_rows: Any = None,
    previous_market: dict[str, Any] | None = None,
    dragon_tiger_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one immutable-style cross-sectional context for a full scan."""
    members = [metric for item in prepared_items if (metric := _member_metrics(item)) is not None]
    market_snapshot = market_snapshot if isinstance(market_snapshot, dict) else {}
    market = _market_context(members, market_snapshot, previous_market)
    market_ret5 = _mean([float(member["ret5"]) for member in members])
    market_ret20 = _mean([float(member["ret20"]) for member in members])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for member in members:
        if member["industry"]:
            grouped[str(member["industry"])].append(member)
    dragon_tiger, dragon_tiger_stocks, dragon_tiger_sectors = _dragon_tiger_context(
        dragon_tiger_snapshot,
        members,
    )

    raw_sectors: dict[str, dict[str, Any]] = {}
    for industry, sector_members in grouped.items():
        raw_sectors[industry] = {
            "industry": industry,
            "member_count": len(sector_members),
            "ret5": _mean([float(member["ret5"]) for member in sector_members]),
            "ret20": _mean([float(member["ret20"]) for member in sector_members]),
            "breadth20": _mean([100.0 if member["above_ema20"] else 0.0 for member in sector_members]),
            "new_high20_ratio": _mean([100.0 if member["new_high20"] else 0.0 for member in sector_members]),
            "volume_ratio": _mean([float(member["volume_ratio"]) for member in sector_members], 1.0),
            "amount": sum(float(member["amount"]) for member in sector_members),
        }

    flows = _flow_map(flow_rows)
    flow_population = list(flows.values())
    ret5_population = [sector["ret5"] - market_ret5 for sector in raw_sectors.values()]
    ret20_population = [sector["ret20"] - market_ret20 for sector in raw_sectors.values()]
    volume_population = [sector["volume_ratio"] for sector in raw_sectors.values()]
    liquidity_population = [sector["amount"] for sector in raw_sectors.values()]
    for sector in raw_sectors.values():
        sector["relative_5d_pct"] = sector["ret5"] - market_ret5
        sector["relative_20d_pct"] = sector["ret20"] - market_ret20
        sector["rs5_percentile"] = _percentile(sector["relative_5d_pct"], ret5_population)
        sector["rs20_percentile"] = _percentile(sector["relative_20d_pct"], ret20_population)
        sector["rank_acceleration"] = sector["rs5_percentile"] - sector["rs20_percentile"]
    acceleration_population = [sector["rank_acceleration"] for sector in raw_sectors.values()]

    sectors: dict[str, dict[str, Any]] = {}
    for industry, sector in raw_sectors.items():
        acceleration_percentile = _percentile(sector["rank_acceleration"], acceleration_population)
        volume_percentile = _percentile(sector["volume_ratio"], volume_population)
        liquidity_percentile = _percentile(sector["amount"], liquidity_population)
        flow_value = _matched_flow(industry, flows)
        flow_score = _percentile(flow_value, flow_population) if flow_value is not None else volume_percentile
        base_score = (
            sector["rs20_percentile"] * 0.25
            + sector["rs5_percentile"] * 0.15
            + acceleration_percentile * 0.15
            + sector["breadth20"] * 0.20
            + sector["new_high20_ratio"] * 0.10
            + flow_score * 0.10
            + liquidity_percentile * 0.05
        )
        dragon_tiger_sector = dragon_tiger_sectors.get(industry) or {}
        dragon_tiger_adjustment = float(dragon_tiger_sector.get("adjustment") or 0.0)
        score = _clamp(base_score + dragon_tiger_adjustment)
        eligible_data = sector["member_count"] >= SECTOR_TIDE_MIN_MEMBERS
        if eligible_data and score >= 75 and sector["rs20_percentile"] >= 70:
            status = "leading"
        elif eligible_data and score >= 65 and sector["rank_acceleration"] >= 15 and sector["rs5_percentile"] >= 65:
            status = "improving"
        elif score < 45 or sector["rs20_percentile"] < 35:
            status = "lagging"
        else:
            status = "weakening"
        sectors[industry] = {
            **sector,
            "base_score": round(base_score, 2),
            "score": round(score, 2),
            "status": status,
            "eligible_data": eligible_data,
            "flow_net_yi": safe_round(flow_value, 2),
            "flow_source": "industry_net_flow" if flow_value is not None else "volume_participation_fallback",
            "relative_5d_pct": round(sector["relative_5d_pct"], 2),
            "relative_20d_pct": round(sector["relative_20d_pct"], 2),
            "rs5_percentile": round(sector["rs5_percentile"], 2),
            "rs20_percentile": round(sector["rs20_percentile"], 2),
            "rank_acceleration": round(sector["rank_acceleration"], 2),
            "breadth20": round(sector["breadth20"], 2),
            "new_high20_ratio": round(sector["new_high20_ratio"], 2),
            "volume_ratio": round(sector["volume_ratio"], 2),
            "dragon_tiger_score": dragon_tiger_sector.get("score", 50.0),
            "dragon_tiger_adjustment": round(dragon_tiger_adjustment, 3),
            "dragon_tiger_listed_count": int(dragon_tiger_sector.get("listed_count") or 0),
            "dragon_tiger_positive_count": int(dragon_tiger_sector.get("positive_count") or 0),
            "dragon_tiger_negative_count": int(dragon_tiger_sector.get("negative_count") or 0),
        }

    stock_context: dict[str, dict[str, Any]] = {}
    all_ret20 = [float(member["ret20"]) for member in members]
    for industry, sector_members in grouped.items():
        sector_ret5 = [float(member["ret5"]) for member in sector_members]
        sector_ret20 = [float(member["ret20"]) for member in sector_members]
        for member in sector_members:
            rs5_rank = _percentile(float(member["ret5"]), sector_ret5)
            rs20_rank = _percentile(float(member["ret20"]), sector_ret20)
            dragon_tiger_stock = dragon_tiger_stocks.get(str(member["code"])) or {}
            dragon_tiger_strength = float(dragon_tiger_stock.get("strength") or 0.0)
            stock_context[str(member["code"])] = {
                "industry": industry,
                "sector_relative_rank": round(rs20_rank * 0.6 + rs5_rank * 0.4, 2),
                "market_relative_rank": round(_percentile(float(member["ret20"]), all_ret20), 2),
                "ret5": round(float(member["ret5"]), 2),
                "ret20": round(float(member["ret20"]), 2),
                "dragon_tiger_listed": bool(dragon_tiger_stock.get("listed")),
                "dragon_tiger_score": dragon_tiger_stock.get("score", 50.0),
                "dragon_tiger_signal": dragon_tiger_stock.get("signal", "neutral"),
                "dragon_tiger_confidence": dragon_tiger_stock.get("confidence", 0.0),
                "dragon_tiger_adjustment": round(
                    dragon_tiger_strength * SECTOR_TIDE_DRAGON_TIGER_MAX_STOCK_ADJUSTMENT,
                    3,
                ),
                "dragon_tiger_net_amount_yuan": dragon_tiger_stock.get("net_amount_yuan"),
                "dragon_tiger_net_ratio_pct": dragon_tiger_stock.get("net_ratio_pct"),
                "dragon_tiger_seat_net_amount_yuan": dragon_tiger_stock.get("seat_net_amount_yuan"),
                "dragon_tiger_institution_net_amount_yuan": dragon_tiger_stock.get("institution_net_amount_yuan"),
                "dragon_tiger_seat_record_count": int(dragon_tiger_stock.get("seat_record_count") or 0),
                "dragon_tiger_institution_record_count": int(
                    dragon_tiger_stock.get("institution_record_count") or 0
                ),
            }

    return {
        "version": 3,
        "market": market,
        "market_ret5_pct": round(market_ret5, 2),
        "market_ret20_pct": round(market_ret20, 2),
        "sector_count": len(sectors),
        "mapped_stock_count": len(stock_context),
        "data_coverage": round(len(stock_context) / len(prepared_items), 4) if prepared_items else 0.0,
        "dragon_tiger": dragon_tiger,
        "sectors": sectors,
        "stocks": stock_context,
    }


def _entry_metrics(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    if len(rows) < SECTOR_TIDE_MIN_ROWS or not isinstance(context, dict):
        return None
    latest = rows[-1]
    code = str(latest.get("symbol_code") or "")
    industry = _industry_name(latest.get("industry"))
    sector = (context.get("sectors") or {}).get(industry)
    stock = (context.get("stocks") or {}).get(code)
    market = context.get("market") if isinstance(context.get("market"), dict) else {}
    dragon_tiger = context.get("dragon_tiger") if isinstance(context.get("dragon_tiger"), dict) else {}
    if not isinstance(sector, dict) or not isinstance(stock, dict):
        return None
    close = safe_float(latest.get("close"))
    ema20 = safe_float(latest.get("ema20"))
    ema50 = safe_float(latest.get("ema50"))
    prev_ema20 = safe_float(rows[-2].get("ema20"))
    atr = _atr(rows)
    if close is None or close <= 0 or ema20 is None or ema50 is None or atr is None or atr <= 0:
        return None
    distance_pct = (close / ema20 - 1) * 100
    extension_atr = (close - ema20) / atr
    prior_highs = [safe_float(row.get("high")) for row in rows[-21:-1]]
    prior_highs = [value for value in prior_highs if value is not None and value > 0]
    recent_volume = safe_float(latest.get("volume")) or 0.0
    prior_volumes = [safe_float(row.get("volume")) for row in rows[-21:-1]]
    prior_volumes = [value for value in prior_volumes if value is not None and value > 0]
    volume_ratio = recent_volume / _mean(prior_volumes) if prior_volumes else 1.0
    change_pct = safe_float(latest.get("change_pct")) or 0.0
    breakout = bool(prior_highs and close >= max(prior_highs) * 1.002 and 1.2 <= volume_ratio <= 2.5)
    pullback_lows = [safe_float(row.get("low")) for row in rows[-4:]]
    pullback_lows = [value for value in pullback_lows if value is not None and value > 0]
    pullback = bool(pullback_lows and min(pullback_lows) <= ema20 * 1.02 and close >= ema20 and volume_ratio <= 1.15 and change_pct >= -0.5)
    prev_close = safe_float(rows[-2].get("close")) or close
    reclaim = bool(prev_close <= ema20 * 1.01 and close > ema20 and change_pct > 0 and volume_ratio >= 1.0)
    trend_aligned = close >= ema20 >= ema50 and (prev_ema20 is None or ema20 >= prev_ema20)
    structure_lows = [safe_float(row.get("low")) for row in rows[-3:]]
    structure_lows = [value for value in structure_lows if value is not None and value > 0]
    structure_low = min(structure_lows) if structure_lows else close - atr * 1.5
    stop_distance_pct = structural_stop_distance_pct(close, structure_low)
    stop_atr = (close - structure_low) / atr if atr > 0 else 99.0
    risk_ok = 0 < stop_distance_pct <= 6 and stop_atr <= 1.5
    gap_buffer_pct = downside_gap_buffer_pct(rows, atr=atr, close=close)
    effective_distance_pct = effective_loss_distance_pct(
        close,
        structure_low,
        gap_buffer_pct=gap_buffer_pct,
        execution_buffer_pct=SECTOR_TIDE_EXECUTION_BUFFER_PCT,
    )

    trend_score = (100 if trend_aligned else (70 if close >= ema20 else 20))
    entry_score = 100 if breakout else (85 if pullback else (75 if reclaim else 35))
    volume_score = _clamp(50 + (volume_ratio - 1) * 50)
    liquidity_score = _clamp(50 + ((safe_float(latest.get("quote_amount")) or 8e8) / 8e8 - 1) * 25)
    risk_score = 100 if risk_ok and extension_atr <= 1.5 else (65 if risk_ok and extension_atr <= 2 else 20)
    stock_score = (
        float(stock["sector_relative_rank"]) * 0.25
        + float(stock["market_relative_rank"]) * 0.20
        + trend_score * 0.15
        + entry_score * 0.15
        + volume_score * 0.10
        + liquidity_score * 0.05
        + risk_score * 0.10
    )
    sector_score = float(sector["score"])
    base_sector_score = float(sector.get("base_score", sector_score))
    score_before_dragon_tiger = (stock_score * 0.60 + base_sector_score * 0.40) / 10
    sector_dragon_tiger_adjustment = (sector_score - base_sector_score) * 0.40 / 10
    raw_dragon_tiger_adjustment = (
        float(stock.get("dragon_tiger_adjustment") or 0.0)
        + sector_dragon_tiger_adjustment
    )
    dragon_tiger_positive_suppressed = bool(
        raw_dragon_tiger_adjustment > 0 and (change_pct > 7 or extension_atr > 1.5)
    )
    dragon_tiger_adjustment = 0.0 if dragon_tiger_positive_suppressed else raw_dragon_tiger_adjustment
    composite_score = _clamp(score_before_dragon_tiger + dragon_tiger_adjustment, 0.0, 10.0)
    return {
        "code": code,
        "industry": industry,
        "market": market,
        "sector": sector,
        "stock": stock,
        "dragon_tiger": dragon_tiger,
        "close": close,
        "ema20": ema20,
        "ema50": ema50,
        "atr20": atr,
        "distance_pct": distance_pct,
        "extension_atr": extension_atr,
        "volume_ratio": volume_ratio,
        "change_pct": change_pct,
        "breakout": breakout,
        "pullback": pullback,
        "reclaim": reclaim,
        "trend_aligned": trend_aligned,
        "stop_price": structure_low,
        "stop_distance_pct": stop_distance_pct,
        "stop_atr": stop_atr,
        "gap_buffer_pct": gap_buffer_pct,
        "execution_buffer_pct": SECTOR_TIDE_EXECUTION_BUFFER_PCT,
        "effective_loss_distance_pct": effective_distance_pct,
        "risk_ok": risk_ok,
        "stock_score": stock_score,
        "score_before_dragon_tiger": score_before_dragon_tiger,
        "dragon_tiger_adjustment": dragon_tiger_adjustment,
        "dragon_tiger_positive_suppressed": dragon_tiger_positive_suppressed,
        "composite_score": composite_score,
    }


def _payload(
    strategy_name: str,
    metrics: dict[str, Any],
    *,
    score: float,
    verdict: str,
    risk_flags: list[str],
) -> dict[str, Any]:
    market = metrics["market"]
    sector = metrics["sector"]
    stock = metrics["stock"]
    dragon_tiger = metrics["dragon_tiger"]
    budget = sector_tide_risk_budget(str(market.get("state") or ""))
    absolute_position_cap_pct = SECTOR_TIDE_ABSOLUTE_POSITION_CAP_PCT[strategy_name]
    max_position_pct_by_risk = risk_sized_position_cap_pct(
        per_trade_risk_pct=budget["per_trade_risk_pct"],
        effective_loss_distance_pct_value=metrics["effective_loss_distance_pct"],
        absolute_cap_pct=absolute_position_cap_pct,
    )
    return {
        "score": score,
        "score_total": 10,
        "verdict": verdict,
        "industry": metrics["industry"],
        "sector_status": sector.get("status"),
        "sector_score": safe_round(sector.get("score"), 2),
        "sector_member_count": sector.get("member_count"),
        "sector_data_eligible": bool(sector.get("eligible_data")),
        "sector_relative_5d_pct": sector.get("relative_5d_pct"),
        "sector_relative_20d_pct": sector.get("relative_20d_pct"),
        "sector_rank_acceleration": sector.get("rank_acceleration"),
        "sector_breadth20": sector.get("breadth20"),
        "sector_flow_net_yi": sector.get("flow_net_yi"),
        "sector_flow_source": sector.get("flow_source"),
        "market_regime": market.get("state"),
        "market_score": market.get("score"),
        "market_hard_stop": bool(market.get("hard_stop")),
        "market_allows_buys": bool(market.get("allow_new_buys")),
        "stock_sector_rank": stock.get("sector_relative_rank"),
        "stock_market_rank": stock.get("market_relative_rank"),
        "stock_score": round(metrics["stock_score"], 2),
        "score_before_dragon_tiger": safe_round(metrics["score_before_dragon_tiger"], 3),
        "dragon_tiger_available": bool(dragon_tiger.get("available")),
        "dragon_tiger_as_of_date": dragon_tiger.get("as_of_date"),
        "dragon_tiger_source": dragon_tiger.get("source"),
        "dragon_tiger_seat_data_complete": bool(dragon_tiger.get("seat_data_complete")),
        "dragon_tiger_listed": bool(stock.get("dragon_tiger_listed")),
        "dragon_tiger_score": stock.get("dragon_tiger_score", 50.0),
        "dragon_tiger_signal": stock.get("dragon_tiger_signal", "neutral"),
        "dragon_tiger_confidence": stock.get("dragon_tiger_confidence", 0.0),
        "dragon_tiger_adjustment": safe_round(metrics["dragon_tiger_adjustment"], 3),
        "dragon_tiger_positive_suppressed": metrics["dragon_tiger_positive_suppressed"],
        "dragon_tiger_net_amount_yuan": stock.get("dragon_tiger_net_amount_yuan"),
        "dragon_tiger_net_ratio_pct": stock.get("dragon_tiger_net_ratio_pct"),
        "dragon_tiger_seat_net_amount_yuan": stock.get("dragon_tiger_seat_net_amount_yuan"),
        "dragon_tiger_institution_net_amount_yuan": stock.get("dragon_tiger_institution_net_amount_yuan"),
        "dragon_tiger_seat_record_count": stock.get("dragon_tiger_seat_record_count", 0),
        "dragon_tiger_institution_record_count": stock.get(
            "dragon_tiger_institution_record_count",
            0,
        ),
        "sector_dragon_tiger_score": sector.get("dragon_tiger_score", 50.0),
        "sector_dragon_tiger_adjustment": sector.get("dragon_tiger_adjustment", 0.0),
        "sector_dragon_tiger_listed_count": sector.get("dragon_tiger_listed_count", 0),
        "ema20": safe_round(metrics["ema20"], 3),
        "ema50": safe_round(metrics["ema50"], 3),
        "atr20": safe_round(metrics["atr20"], 3),
        "distance_pct": safe_round(metrics["distance_pct"], 2),
        "extension_atr": safe_round(metrics["extension_atr"], 2),
        "volume_ratio": safe_round(metrics["volume_ratio"], 2),
        "change_pct": safe_round(metrics["change_pct"], 2),
        "trend_aligned": metrics["trend_aligned"],
        "breakout": metrics["breakout"],
        "pullback": metrics["pullback"],
        "reclaim": metrics["reclaim"],
        "stop_price": safe_round(metrics["stop_price"], 3),
        "stop_source": "tide_structure_low",
        "stop_distance_pct": safe_round(metrics["stop_distance_pct"], 2),
        "stop_atr": safe_round(metrics["stop_atr"], 2),
        "gap_buffer_pct": safe_round(metrics["gap_buffer_pct"], 3),
        "execution_buffer_pct": safe_round(metrics["execution_buffer_pct"], 3),
        "effective_loss_distance_pct": safe_round(metrics["effective_loss_distance_pct"], 3),
        "per_trade_risk_budget_pct": budget["per_trade_risk_pct"],
        "max_open_risk_pct": budget["max_open_risk_pct"],
        "max_sector_risk_pct": budget["max_sector_risk_pct"],
        "max_total_position_pct": budget["max_total_position_pct"],
        "max_sector_position_pct": budget["max_sector_position_pct"],
        "absolute_position_cap_pct": absolute_position_cap_pct,
        "max_position_pct_by_risk": max_position_pct_by_risk,
        "risk_ok": metrics["risk_ok"],
        "risk_flags": risk_flags,
        "recent_close": safe_round(metrics["close"], 3),
    }


def score_tide_leader(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    metrics = _entry_metrics(rows, context)
    if metrics is None:
        return None
    sector = metrics["sector"]
    market = metrics["market"]
    risk_flags: list[str] = []
    if market.get("state") not in {"offensive", "rotation"}:
        risk_flags.append("市场并非进攻/轮动状态")
    if sector.get("status") != "leading":
        risk_flags.append("行业未进入领先状态")
    if float(metrics["stock"].get("sector_relative_rank") or 0) < 80:
        risk_flags.append("个股并非行业前20%")
    if not (metrics["breakout"] or metrics["pullback"]):
        risk_flags.append("未形成突破或缩量回踩买点")
    if not metrics["risk_ok"]:
        risk_flags.append("结构止损超过1.5ATR或6%")
    score = metrics["composite_score"]
    verdict = "高匹配主线领航" if score >= 8 else ("观察主线领航" if score >= 6.5 else "不匹配")
    return with_strategy_profile(
        "tide_leader",
        _payload("tide_leader", metrics, score=score, verdict=verdict, risk_flags=risk_flags),
    )


def score_tide_rotation(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    metrics = _entry_metrics(rows, context)
    if metrics is None:
        return None
    sector = metrics["sector"]
    market = metrics["market"]
    risk_flags: list[str] = []
    if market.get("state") not in {"rotation", "offensive"}:
        risk_flags.append("市场并非轮动/进攻状态")
    if sector.get("status") != "improving":
        risk_flags.append("行业未进入改善状态")
    if float(sector.get("rank_acceleration") or 0) < 15:
        risk_flags.append("行业排名改善不足")
    if float(metrics["stock"].get("sector_relative_rank") or 0) < 70:
        risk_flags.append("个股并非行业第一梯队")
    if metrics["change_pct"] > 7 or metrics["extension_atr"] > 1.5:
        risk_flags.append("轮动初升拒绝追高")
    if not (metrics["breakout"] or metrics["pullback"] or metrics["reclaim"]):
        risk_flags.append("轮动买点未确认")
    if not metrics["risk_ok"]:
        risk_flags.append("结构止损超过1.5ATR或6%")
    score = metrics["composite_score"]
    verdict = "高匹配轮动初升" if score >= 8.2 else ("观察轮动初升" if score >= 6.5 else "不匹配")
    return with_strategy_profile(
        "tide_rotation",
        _payload("tide_rotation", metrics, score=score, verdict=verdict, risk_flags=risk_flags),
    )


def score_tide_recovery(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    metrics = _entry_metrics(rows, context)
    if metrics is None:
        return None
    sector = metrics["sector"]
    market = metrics["market"]
    risk_flags: list[str] = []
    if market.get("state") != "recovery" or market.get("hard_stop"):
        risk_flags.append("市场尚未完成冰点修复")
    if sector.get("status") not in {"leading", "improving"}:
        risk_flags.append("行业未率先修复")
    if float(metrics["stock"].get("sector_relative_rank") or 0) < 70:
        risk_flags.append("个股修复强度不足")
    if not (metrics["reclaim"] or metrics["breakout"]):
        risk_flags.append("未重新站回EMA20或突破修复高点")
    if metrics["extension_atr"] > 1.5:
        risk_flags.append("冰点修复拒绝追高")
    if not metrics["risk_ok"]:
        risk_flags.append("结构止损超过1.5ATR或6%")
    score = metrics["composite_score"]
    verdict = "高匹配冰点修复" if score >= 8.5 else ("观察冰点修复" if score >= 6.5 else "不匹配")
    return with_strategy_profile(
        "tide_recovery",
        _payload("tide_recovery", metrics, score=score, verdict=verdict, risk_flags=risk_flags),
    )


# The scoring engine uses this marker to preserve the legacy one-argument
# scorer API for every existing strategy.
score_tide_leader.requires_context = True  # type: ignore[attr-defined]
score_tide_rotation.requires_context = True  # type: ignore[attr-defined]
score_tide_recovery.requires_context = True  # type: ignore[attr-defined]
