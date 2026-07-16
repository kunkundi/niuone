"""Strategy-specific exit rules without market-data or execution side effects."""
from __future__ import annotations

from typing import Any


def _sell_signal(reason: str, signal: str, sell_ratio: float = 1.0) -> dict[str, Any]:
    return {"reason": reason, "signal": signal, "sell_ratio": sell_ratio}


def evaluate_strategy_time_exit(
    *,
    entry_strategy: str,
    hold_days: int,
    max_pnl_pct: float,
    pnl_pct: float,
    time_exit_allowed: bool,
    b3_exit_allowed: bool,
    b3_exit_hhmm: str,
    time_exit_hhmm: str,
    no_progress_hold_days: int,
    no_progress_max_pnl_pct: float,
) -> dict[str, Any] | None:
    """Evaluate strategy-specific time-boxed exits."""
    if b3_exit_allowed and entry_strategy == "b3_accelerate" and hold_days >= 1 and max_pnl_pct < 1.0 and pnl_pct <= 0:
        return _sell_signal(
            f"B3次日不涨离场 ({hold_days}d {b3_exit_hhmm}开盘检查，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
            "b3_next_day_no_progress",
        )
    if time_exit_allowed:
        if entry_strategy == "tide_leader" and hold_days >= 5 and max_pnl_pct < 3.0:
            return _sell_signal(
                f"主线领航5日未创新高 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "tide_leader_no_progress",
            )
        if entry_strategy == "tide_rotation" and hold_days >= 3 and max_pnl_pct < 2.0:
            return _sell_signal(
                f"轮动初升3日未延续 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "tide_rotation_no_follow_through",
            )
        if entry_strategy == "tide_recovery" and hold_days >= 2 and max_pnl_pct < 1.5 and pnl_pct <= 0.5:
            return _sell_signal(
                f"冰点修复T+2未确认 ({hold_days}d，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "tide_recovery_unconfirmed",
            )
        if entry_strategy == "b2_confirm" and hold_days >= 2 and max_pnl_pct < 2.0 and pnl_pct <= 0.5:
            return _sell_signal(
                f"B2确认未延续离场 ({hold_days}d {time_exit_hhmm}尾盘检查，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "b2_no_follow_through",
            )
        if entry_strategy == "super_b1" and hold_days >= no_progress_hold_days and max_pnl_pct < no_progress_max_pnl_pct:
            return _sell_signal(
                f"超级B1只赌一次未兑现离场 ({hold_days}d {time_exit_hhmm}尾盘检查，最高盈利{max_pnl_pct:.1f}%，现盈亏{pnl_pct:.1f}%)",
                "super_b1_no_progress",
            )
    return None
