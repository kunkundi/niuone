"""Runtime adapters; no model calls or account writes for operational pushes."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from app.core.paths import get_dashboard_home
from app.storage.market_reports import to_cn_datetime
from .operations import DAILY_ENABLED_ENV, enabled, notify_daily_summary, notify_operation


def _home(env: Mapping[str, str]) -> Path:
    return Path(env["DASHBOARD_HOME"]) if env.get("DASHBOARD_HOME") else get_dashboard_home(Path(__file__).resolve().parents[2])


def report_operation_safely(source: str, title: str, *, ok: bool, env: Mapping[str, str] | None = None, **kwargs: Any) -> None:
    settings = os.environ if env is None else env
    try:
        notify_operation(source, title, ok=ok, env=settings,
                         state_path=_home(settings) / "notifications" / "delivery.sqlite3", **kwargs)
    except Exception as exc:
        print(f"[WARN] 运行通知未发送: {type(exc).__name__}", flush=True)


def _daily_trades(state: Mapping[str, Any], db_path: Path, day: str) -> list[dict[str, Any]]:
    """Read the day's lossless archive only; current JSON wins over revisions."""
    rows = []
    if db_path.is_file():
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_history'").fetchone()
            if exists:
                rows = [json.loads(row[0]) for row in conn.execute(
                    "SELECT h.payload_json FROM account_history h WHERE h.history_kind='trade_log' AND h.event_time>=? AND h.event_time<? AND NOT EXISTS (SELECT 1 FROM account_history newer WHERE newer.history_kind=h.history_kind AND newer.logical_key=h.logical_key AND newer.id>h.id)",
                    (max(day, str(state.get("created_at") or "")), day + " 24:00:00"))]
    merged = {}
    for row in [*rows, *(state.get("trade_log") or [])]:
        if isinstance(row, dict) and str(row.get("time") or "")[:10] == day:
            key = tuple(str(row.get(field) or "") for field in ("time", "action", "code", "shares", "price", "reason"))
            merged[key] = row
    return sorted(merged.values(), key=lambda row: str(row.get("time") or ""))


def maybe_notify_daily_summary(env: Mapping[str, str], now: datetime) -> None:
    if not enabled(env, DAILY_ENABLED_ENV):
        return
    current = to_cn_datetime(now)
    if current.hour < 15 or current.hour >= 19:
        return
    day = current.strftime("%Y-%m-%d")
    home = _home(env)
    state_path = Path(env.get("DASHBOARD_PORTFOLIO_STATE") or home / "cron" / "output" / "niuniu_practice_portfolio.json")
    history_path = Path(env.get("DASHBOARD_PUSH_HISTORY_DB") or home / "push_history.db")
    if not state_path.is_file() or not history_path.is_file():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        with closing(sqlite3.connect(history_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
            row = conn.execute("SELECT content FROM dashboard_messages WHERE source_id=? AND time_text>=? AND time_text<? ORDER BY timestamp DESC LIMIT 1",
                               ("cron_output_67ac98149ead", day + " 15:00:00", day + " 24:00:00")).fetchone()
        if not row:
            return
        state["trade_log"] = _daily_trades(state, Path(env.get("DASHBOARD_NIUNIU_DB") or home / "niuniu.db"), day)
        notify_daily_summary(state, day, str(row[0]), env=env,
                             state_path=home / "notifications" / "delivery.sqlite3", now=current.timestamp())
    except Exception as exc:
        print(f"[WARN] 盘后通知未发送: {type(exc).__name__}", flush=True)
