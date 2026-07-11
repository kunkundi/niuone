#!/usr/bin/env python3
"""Persist dashboard market reports directly in SQLite."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import push_history


CN_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def _to_cn_datetime(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(CN_TZ)
    if value.tzinfo is None:
        return value.replace(tzinfo=CN_TZ)
    return value.astimezone(CN_TZ)


def extract_decision_guidance(content: str) -> list[str]:
    """Extract the compact buy/sell guidance block from a market report."""
    lines = [line.strip() for line in str(content or "").splitlines()]
    guidance: list[str] = []
    in_section = False
    for line in lines:
        clean = line.strip()
        if not clean:
            if in_section and guidance:
                break
            continue
        if any(key in clean for key in ("买卖指引", "买卖计划", "盘前指引")):
            in_section = True
            continue
        if in_section and clean.startswith(("📊", "🔥", "💰", "⚡", "📈", "👀", "📌", "🧭", "⚠️", "🌡️", "💡")) and "**" in clean:
            break
        if in_section:
            guidance.append(clean.lstrip("·- ").strip())
    return guidance[:8]


def store_market_report(
    content: str,
    *,
    job_id: str,
    title: str,
    run_dt: datetime | None = None,
) -> int:
    """Store one market report in the dashboard database without file output."""
    body = (content or "").strip()
    if not body:
        return 0

    local_dt = _to_cn_datetime(run_dt)
    run_key = os.environ.get("NIUONE_CRON_RUN_KEY") or f"{job_id}:{local_dt.strftime('%Y-%m-%d_%H-%M-%S')}"
    source_id = f"cron_output_{job_id}"
    message = {
        "id": push_history.stable_id("market_monitor", job_id, run_key),
        "timestamp": local_dt.timestamp(),
        "time_text": local_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "category": "market_monitor",
        "source_type": "market_monitor",
        "source_id": source_id,
        "source_label": "盘面监控",
        "platform": "dashboard",
        "platform_label": "Dashboard",
        "chat": "market-monitor",
        "chat_label": title,
        "external_id": run_key,
        "title": title,
        "content": body,
        "chars": len(body),
        "matched": True,
        "kind": "cron_output",
        "delivery": {"mode": "dashboard_database_only", "job_id": job_id},
        "metadata": {
            "job_name": title,
            "decision_guidance": extract_decision_guidance(body),
            "run_key": run_key,
        },
    }
    count = push_history.upsert_many([message])
    if count != 1:
        raise RuntimeError(f"market report database write returned {count}")
    return count
