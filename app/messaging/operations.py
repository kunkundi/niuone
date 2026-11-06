"""Opt-in operational notifications with bounded cross-process delivery leases."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Mapping
import uuid

from .dispatcher import dispatch
from .models import DeliveryResult, Notification, TRUTHY_VALUES
from app.trading.accounting import trade_counts_for_account

FAULT_ENABLED_ENV = "DASHBOARD_OPERATIONAL_NOTIFICATION_ENABLED"
DAILY_ENABLED_ENV = "DASHBOARD_DAILY_SUMMARY_NOTIFICATION_ENABLED"
FAULT_DEDUPE_SECONDS = 1800
RETRY_SECONDS = 60
LEASE_SECONDS = 120


def enabled(env: Mapping[str, Any], name: str) -> bool:
    return str(env.get(name) or "").lower() in TRUTHY_VALUES


def error_category(error: Any) -> tuple[str, bool]:
    """Only emit fixed categories, never raw exceptions, URLs, or API bodies."""
    text = str(error).lower()
    if any(word in text for word in ("permissionerror", "permission denied", "errno 13")):
        return "文件权限异常", True
    if any(word in text for word in ("http 401", "http 403", "http error 401", "http error 403", "subscription_not_found", "invalid_api_key")):
        return "接口授权异常", True
    if any(word in text for word in ("not_persisted", "accounting", "frozen account fee")):
        return "账户记录或配置异常", True
    if any(word in text for word in ("timeout", "expired", "deadline")):
        return "请求超时", False
    return "任务运行异常", False


def _deliver_guarded(
    path: Path, key: str, event: str, notification: Notification,
    env: Mapping[str, Any], *, now: float, repeat_after: float,
    deliver: Callable[..., list[DeliveryResult]], recovery: bool = False,
) -> list[DeliveryResult]:
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    with closing(sqlite3.connect(path, timeout=2)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS deliveries (key TEXT PRIMARY KEY, event TEXT NOT NULL, delivered REAL NOT NULL DEFAULT 0, attempted REAL NOT NULL DEFAULT 0, lease REAL NOT NULL DEFAULT 0, token TEXT NOT NULL DEFAULT '')")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT event, delivered, attempted, lease FROM deliveries WHERE key=?", (key,)).fetchone()
        if row and row[3] > now:
            conn.rollback()
            return []
        if recovery and (not row or (row[0] == "recovered" and row[1] > 0) or (row[0].startswith("failed") and row[1] <= 0)):
            # Do not announce recovery for an incident the user never received.
            conn.execute("INSERT OR REPLACE INTO deliveries(key,event,delivered) VALUES (?, 'recovered', ?)", (key, now))
            conn.commit()
            return []
        if row and row[0] == event:
            if (row[1] > 0 and now - row[1] < repeat_after) or (row[2] > 0 and now - row[2] < RETRY_SECONDS):
                conn.rollback()
                return []
        prior_delivery = row[1] if row and row[0] == event else 0
        conn.execute("INSERT OR REPLACE INTO deliveries VALUES (?, ?, ?, ?, ?, ?)",
                     (key, event, prior_delivery, now, now + LEASE_SECONDS, token))
        conn.commit()
    try:
        results = deliver(notification, env=env)
    except Exception:
        results = [DeliveryResult("notification", False, "dispatch failed")]
    delivered = now if results and all(result.ok for result in results) else prior_delivery
    with closing(sqlite3.connect(path, timeout=2)) as conn:
        conn.execute("UPDATE deliveries SET delivered=?, lease=0, token='' WHERE key=? AND token=?",
                     (delivered, key, token))
        conn.commit()
    return results


def notify_operation(
    source: str, title: str, *, ok: bool, env: Mapping[str, Any], state_path: Path,
    error: Any = "", final_attempt: bool = True, critical: bool = False,
    now: float | None = None, deliver: Callable[..., list[DeliveryResult]] = dispatch,
) -> list[DeliveryResult]:
    if not enabled(env, FAULT_ENABLED_ENV):
        return []
    category, inferred_critical = error_category(error)
    if not ok and not final_attempt and not (critical or inferred_critical):
        return []
    event = "recovered" if ok else f"failed:{category}:{bool(critical or inferred_critical)}"
    severity = "关键故障" if critical or inferred_critical else "本轮运行故障"
    notification = Notification(
        event_type=f"runtime.{'recovered' if ok else 'failed'}", title=f"NiuOne {'运行恢复' if ok else severity}：{title}",
        text="该任务已恢复成功。" if ok else f"{category}；本轮未成功，请查看管理页或服务器日志。",
        metadata={"source": source},
    )
    return _deliver_guarded(state_path, f"incident:{source}", event, notification, env,
                            now=time.time() if now is None else now,
                            repeat_after=FAULT_DEDUPE_SECONDS, deliver=deliver, recovery=ok)


def build_daily_summary(state: Mapping[str, Any], day: str, report: str) -> Notification | None:
    points = [point for point in state.get("daily_equity_history", [])
              if isinstance(point, Mapping) and str(point.get("time") or "")[:10] == day
              and str(point.get("time") or "")[11:16] >= "15:00"]
    if not points or not report.strip():
        return None
    point = max(points, key=lambda item: str(item.get("time") or ""))
    equity = float(point["equity"])
    initial = float(state["initial_cash"])
    previous = [item for item in state.get("daily_equity_history", [])
                if isinstance(item, Mapping) and str(item.get("time") or "")[:10] < day
                and float(item.get("equity") or 0) > 0]
    if previous:
        opening = float(max(previous, key=lambda item: str(item.get("time") or ""))["equity"])
    elif str(state.get("performance_start_date") or state.get("created_at") or "")[:10] == day:
        opening = initial
    else:
        opening = None
    trades = [trade for trade in state.get("trade_log", []) if isinstance(trade, Mapping)
              and str(trade.get("time") or "")[:10] == day and trade_counts_for_account(trade)]
    fees = sum(float(trade.get("fee") or 0) for trade in trades)
    daily_pnl = f"{equity - opening:+.2f}元" if opening is not None else "基准缺失，暂不计算"
    lines = [f"{day}（北京时间）模拟账户", f"总权益 {equity:.2f}元；当日盈亏 {daily_pnl}",
             f"累计盈亏 {equity - initial:+.2f}元；当日成交 {len(trades)}笔，费用 {fees:.2f}元", "持仓："]
    held = [(code, pos) for code, pos in (state.get("positions") or {}).items()
            if isinstance(pos, Mapping) and int(pos.get("qty") or pos.get("shares") or 0) > 0]
    lines.extend(f"{code} {pos.get('name') or ''} {int(pos.get('qty') or pos.get('shares') or 0)}股" for code, pos in held)
    if not held:
        lines.append("空仓")
    lines.extend(["A股盘后报告摘要（原文节选）：", report.strip()[:220], "当日成交（最多展示10笔，费用汇总包含全部）："])
    lines.extend(f"{trade.get('action')} {trade.get('code')} {trade.get('shares')}股 × {trade.get('price')}；费用{float(trade.get('fee') or 0):.2f}元" for trade in trades[:10])
    if not trades:
        lines.append("无成交")
    lines.append("完整成交及报告请查看Dashboard。")
    return Notification("account.daily_summary", f"NiuOne 盘后总结 {day}", "\n".join(lines), {"day": day})


def notify_daily_summary(
    state: Mapping[str, Any], day: str, report: str, *, env: Mapping[str, Any], state_path: Path,
    now: float | None = None, deliver: Callable[..., list[DeliveryResult]] = dispatch,
) -> list[DeliveryResult]:
    if not enabled(env, DAILY_ENABLED_ENV):
        return []
    notification = build_daily_summary(state, day, report)
    if notification is None:
        return []
    account = str(state.get("created_at") or "")
    return _deliver_guarded(state_path, f"daily:{account}:{day}", "daily", notification, env,
                            now=time.time() if now is None else now, repeat_after=float("inf"), deliver=deliver)
