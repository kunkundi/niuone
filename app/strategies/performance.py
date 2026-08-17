"""Pure strategy-attribution performance summaries for portfolio state."""
from __future__ import annotations

import re
from typing import Any

from .attribution import classify_buy_strategy, classify_exit_rule
try:
    from trading.accounting import trade_counts_for_account
except ModuleNotFoundError:  # package import when only the repository root is on sys.path
    from app.trading.accounting import trade_counts_for_account


def _normalize_code(code: str) -> str:
    return re.sub(r"\D", "", str(code or ""))[-6:]


def _position_qty(pos: dict[str, Any]) -> int:
    return int(pos.get("qty") or pos.get("shares") or 0)


def _empty_perf_bucket() -> dict[str, Any]:
    return {
        "wins": 0, "losses": 0, "flats": 0, "total_pnl": 0.0, "trades": 0,
        "open_wins": 0, "open_losses": 0, "open_flats": 0, "open_pnl": 0.0, "open_trades": 0,
        "items": [],
    }


def _add_perf_trade(perf: dict[str, dict[str, Any]], key: str, pnl: float, item: dict[str, Any] | None = None) -> None:
    bucket = perf.setdefault(key or "unknown", _empty_perf_bucket())
    bucket["trades"] += 1
    if pnl > 0:
        bucket["wins"] += 1
    elif pnl < 0:
        bucket["losses"] += 1
    else:
        bucket["flats"] += 1
    bucket["total_pnl"] += pnl
    if item:
        bucket.setdefault("items", []).append(item)


def _add_perf_open_position(perf: dict[str, dict[str, Any]], key: str, pnl: float) -> None:
    bucket = perf.setdefault(key or "unknown", _empty_perf_bucket())
    bucket["open_trades"] += 1
    if pnl > 0:
        bucket["open_wins"] += 1
    elif pnl < 0:
        bucket["open_losses"] += 1
    else:
        bucket["open_flats"] += 1
    bucket["open_pnl"] += pnl


def _finalize_perf(perf: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    finalized: dict[str, dict[str, Any]] = {}
    for key, bucket in perf.items():
        total = int(bucket.get("trades") or 0)
        wins = int(bucket.get("wins") or 0)
        open_total = int(bucket.get("open_trades") or 0)
        open_wins = int(bucket.get("open_wins") or 0)
        row = dict(bucket)
        row["total_pnl"] = round(float(row.get("total_pnl") or 0), 2)
        row["open_pnl"] = round(float(row.get("open_pnl") or 0), 2)
        row["combined_pnl"] = round(float(row.get("total_pnl") or 0) + float(row.get("open_pnl") or 0), 2)
        row["trigger_count"] = total
        row["win_rate"] = round(wins / total * 100, 1) if total > 0 else 0
        row["open_win_rate"] = round(open_wins / open_total * 100, 1) if open_total > 0 else 0
        row["avg_pnl"] = round(float(row["total_pnl"]) / total, 2) if total > 0 else 0
        row["items"] = sorted(row.get("items") or [], key=lambda item: str(item.get("time") or ""), reverse=True)
        finalized[key] = row
    return finalized


def latest_buy_strategy_for_code(state: dict[str, Any], code: str) -> str:
    code = _normalize_code(code)
    for trade in reversed(state.get("trade_log", []) or []):
        if not isinstance(trade, dict) or not trade_counts_for_account(trade):
            continue
        if str(trade.get("action") or "").upper() != "BUY":
            continue
        if _normalize_code(trade.get("code") or "") != code:
            continue
        return str(trade.get("buy_strategy") or classify_buy_strategy(str(trade.get("reason") or "")))
    return ""


def track_strategy_performance(state: dict[str, Any]) -> dict[str, Any]:
    """Track entry tactics with open P/L, and closed exits by rule."""
    trade_log = sorted(
        [
            t
            for t in (state.get("trade_log", []) or [])
            if isinstance(t, dict) and trade_counts_for_account(t)
        ],
        key=lambda t: str(t.get("time") or ""),
    )

    entry_perf: dict[str, dict[str, Any]] = {}
    exit_perf: dict[str, dict[str, Any]] = {}
    latest_entry_by_code: dict[str, str] = {}
    total_closed = 0
    total_pnl = 0.0
    total_open_positions = 0
    total_open_pnl = 0.0

    for trade in trade_log:
        action = str(trade.get("action") or "").upper()
        code = _normalize_code(trade.get("code") or "")
        reason = str(trade.get("reason") or "")
        if action == "BUY":
            latest_entry_by_code[code] = str(trade.get("buy_strategy") or classify_buy_strategy(reason))
            continue
        if action != "SELL":
            continue

        pnl = float(trade.get("pnl") or 0)
        entry_strategy = str(trade.get("buy_strategy") or trade.get("entry_strategy") or latest_entry_by_code.get(code) or "")
        if not entry_strategy:
            entry_strategy = classify_buy_strategy(reason)
        exit_rule = str(trade.get("exit_rule") or classify_exit_rule(reason, trade.get("exit_signal")))
        exit_item = {
            "time": trade.get("time") or "",
            "code": code,
            "name": trade.get("name") or "",
            "shares": int(trade.get("shares") or 0),
            "price": round(float(trade.get("price") or 0), 3),
            "pnl": round(pnl, 2),
            "pnl_pct": trade.get("pnl_pct"),
            "reason": reason,
            "buy_strategy": entry_strategy,
        }

        _add_perf_trade(entry_perf, entry_strategy, pnl)
        _add_perf_trade(exit_perf, exit_rule, pnl, exit_item)
        total_closed += 1
        total_pnl += pnl

    for code, pos in (state.get("positions") or {}).items():
        if not isinstance(pos, dict):
            continue
        qty = _position_qty(pos)
        if qty <= 0:
            continue
        try:
            avg_cost = float(pos.get("avg_cost") or 0)
            price = float(pos.get("last_price") or pos.get("close") or avg_cost or 0)
        except Exception:
            continue
        if avg_cost <= 0 or price <= 0:
            continue
        norm_code = _normalize_code(code or pos.get("code") or "")
        entry_strategy = str(
            pos.get("buy_strategy")
            or latest_entry_by_code.get(norm_code)
            or classify_buy_strategy(str(pos.get("entry_reason") or ""))
        )
        open_pnl = (price - avg_cost) * qty
        _add_perf_open_position(entry_perf, entry_strategy, open_pnl)
        total_open_positions += 1
        total_open_pnl += open_pnl

    return {
        "buy_strategy": _finalize_perf(entry_perf),
        "exit_rule": _finalize_perf(exit_perf),
        "summary": {
            "closed_trades": total_closed,
            "total_pnl": round(total_pnl, 2),
            "open_positions": total_open_positions,
            "open_pnl": round(total_open_pnl, 2),
            "combined_pnl": round(total_pnl + total_open_pnl, 2),
        },
    }
