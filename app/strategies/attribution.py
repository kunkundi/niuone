"""Strategy attribution, labels, and position mark helpers."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .registry import STRATEGY_DEFINITIONS, classify_strategy_text, known_strategy_ids


def _now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _compact_text(value: Any, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def classify_buy_strategy(reason: str = "", candidate: dict[str, Any] | None = None) -> str:
    """Classify the entry tactic independently from the later exit rule."""
    if candidate:
        raw_strategy = str(candidate.get("buy_strategy") or candidate.get("best_strategy") or candidate.get("strategy") or "").strip()
        if raw_strategy in known_strategy_ids():
            return raw_strategy
        reason = " ".join([
            reason,
            str(candidate.get("score_basis") or ""),
            str(candidate.get("best_strategy") or ""),
            str(candidate.get("verdict") or ""),
        ])

    text = str(reason or "")
    matched = classify_strategy_text(text)
    if matched:
        return matched
    if "回踩" in text and "卖出" not in text:
        return "trend_pullback"
    return "unknown_buy"


def classify_exit_rule(reason: str = "", exit_signal: str | None = None) -> str:
    """Classify why a position was closed, separate from the entry tactic."""
    signal = str(exit_signal or "").strip()
    if signal:
        if signal in {"shaofu_entry_stop", "tide_structure_stop", "hard_stop"}:
            return "stop_loss"
        if signal in {"take_profit", "partial_take_profit", "luzhu_half", "tide_2r_partial"}:
            return "take_profit"
        if signal in {"profit_giveback", "atr_chandelier", "tide_atr_trail", "breakeven_trail", "profit_to_loss"}:
            return "profit_protection"
        if signal == "tide_sector_weak":
            return "sector_retreat"
        if signal == "tide_market_hard_stop":
            return "market_risk"
        if signal in {"s1_distribution", "s2_macd_divergence", "s3_last_escape", "chuhuo_wushi"}:
            return "top_escape"
        if signal in {
            "z_dead_cross", "z_white_break", "s1_reclaim_failed", "s1_bbi_failed",
            "bbi_breakdown", "donchian_low_break",
        }:
            return "technical_break"
        if signal in {"sell_score_exit", "sell_score_reduce"}:
            return "sell_score"
        if signal in {
            "no_progress", "max_hold_days", "stale_loser", "stale_below_bbi",
            "tide_leader_no_progress", "tide_rotation_no_follow_through", "tide_recovery_unconfirmed",
        }:
            return "no_progress"

    text = str(reason or "")
    if "止损" in text or "破入场止损" in text:
        return "stop_loss"
    if "止盈清仓" in text or "第一批止盈" in text or "卤煮止盈" in text:
        return "take_profit"
    if "峰值回撤" in text or "ATR吊灯" in text or "移动止损保本" in text or "盈转亏" in text:
        return "profit_protection"
    if "S1" in text or "S2" in text or "S3" in text or "逃顶" in text or "出货五式" in text:
        return "top_escape"
    if "卖出评分" in text or "防卖飞评分" in text:
        return "sell_score"
    if "BBI" in text or "白线" in text or "死叉" in text or "低点跌破" in text or "趋势确认失效" in text:
        return "technical_break"
    if "行业退潮" in text or "行业分数" in text:
        return "sector_retreat"
    if "市场复合风险" in text or "市场硬停止" in text:
        return "market_risk"
    if "未兑现" in text or "低效持仓" in text or "持仓到期" in text or "次日不涨" in text or "未延续" in text:
        return "no_progress"
    if "调仓" in text or "仓位" in text or "硬约束" in text:
        return "position_adjust"
    if "模型卖出" in text:
        return "model_sell"
    return "other_exit"


EXIT_RULE_LABELS: dict[str, str] = {
    "stop_loss": "止损",
    "take_profit": "止盈",
    "profit_protection": "盈利保护",
    "top_escape": "逃顶/出货",
    "technical_break": "技术破位",
    "sector_retreat": "板块退潮",
    "market_risk": "市场风险",
    "sell_score": "卖出评分",
    "no_progress": "信号未兑现",
    "position_adjust": "仓位调整",
    "model_sell": "模型卖出",
    "other_exit": "其他卖出",
}


def buy_strategy_label(strategy_id: str) -> str:
    if strategy_id == "mixed":
        return "混合买入"
    if strategy_id == "unknown_buy":
        return "未识别买入"
    return str(STRATEGY_DEFINITIONS.get(strategy_id, {}).get("label") or strategy_id or "未标记")


def build_entry_strategy_mark(
    strategy_id: str,
    reason: str = "",
    *,
    source: str = "BUY",
    component_strategy: str = "",
    marked_at: str | None = None,
) -> dict[str, Any]:
    strategy_id = str(strategy_id or "unknown_buy").strip() or "unknown_buy"
    mark = {
        "strategy_id": strategy_id,
        "label": buy_strategy_label(strategy_id),
        "source": source,
        "marked_at": marked_at or _now_ts(),
        "reason": _compact_text(reason, 220),
    }
    if component_strategy:
        mark["component_strategy_id"] = component_strategy
        mark["component_label"] = buy_strategy_label(component_strategy)
    return mark


def build_exit_strategy_mark(
    entry_strategy: str,
    exit_rule: str,
    reason: str = "",
    *,
    source: str = "SELL",
    marked_at: str | None = None,
) -> dict[str, Any]:
    exit_rule = str(exit_rule or "other_exit").strip() or "other_exit"
    entry_strategy = str(entry_strategy or "unknown_buy").strip() or "unknown_buy"
    return {
        "entry_strategy_id": entry_strategy,
        "entry_label": buy_strategy_label(entry_strategy),
        "exit_rule": exit_rule,
        "exit_label": EXIT_RULE_LABELS.get(exit_rule, exit_rule),
        "source": source,
        "marked_at": marked_at or _now_ts(),
        "reason": _compact_text(reason, 220),
    }


def _append_strategy_mark_history(pos: dict[str, Any], mark: dict[str, Any]) -> None:
    history = pos.setdefault("strategy_mark_history", [])
    if not isinstance(history, list):
        history = []
        pos["strategy_mark_history"] = history
    history.append(mark)
    del history[:-8]


def apply_entry_strategy_mark(
    pos: dict[str, Any],
    strategy_id: str,
    reason: str,
    *,
    source: str = "BUY",
    component_strategy: str = "",
) -> dict[str, Any]:
    mark = build_entry_strategy_mark(strategy_id, reason, source=source, component_strategy=component_strategy)
    pos["strategy_mark"] = mark
    pos["strategy_mark_id"] = mark["strategy_id"]
    pos["strategy_mark_label"] = mark["label"]
    pos["strategy_mark_reason"] = mark["reason"]
    pos["strategy_marked_at"] = mark["marked_at"]
    _append_strategy_mark_history(pos, {"action": "BUY", **mark})
    return mark


def apply_exit_strategy_mark(
    pos: dict[str, Any],
    entry_strategy: str,
    exit_rule: str,
    reason: str,
    *,
    source: str = "SELL",
) -> dict[str, Any]:
    mark = build_exit_strategy_mark(entry_strategy, exit_rule, reason, source=source)
    pos["last_exit_rule"] = mark["exit_rule"]
    pos["last_exit_label"] = mark["exit_label"]
    pos["last_exit_reason"] = mark["reason"]
    pos["last_exit_marked_at"] = mark["marked_at"]
    pos["last_exit_strategy_mark"] = mark
    _append_strategy_mark_history(pos, {"action": "SELL", **mark})
    return mark


def compact_position_strategy_mark(pos: dict[str, Any], fallback_strategy: str = "") -> dict[str, Any]:
    mark = pos.get("strategy_mark") if isinstance(pos.get("strategy_mark"), dict) else {}
    strategy_id = str(
        mark.get("strategy_id")
        or pos.get("strategy_mark_id")
        or pos.get("buy_strategy")
        or fallback_strategy
        or "unknown_buy"
    ).strip() or "unknown_buy"
    return {
        "strategy_id": strategy_id,
        "label": mark.get("label") or pos.get("strategy_mark_label") or buy_strategy_label(strategy_id),
        "reason": mark.get("reason") or pos.get("strategy_mark_reason") or _compact_text(pos.get("entry_reason"), 220),
        "marked_at": mark.get("marked_at") or pos.get("strategy_marked_at") or "",
        "source": mark.get("source") or "STATE",
    }
