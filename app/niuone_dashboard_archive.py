#!/usr/bin/env python3
"""Archive dashboard cron reports and mirror them into push history."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from niuone_paths import get_dashboard_home

try:
    import push_history
except Exception:  # pragma: no cover - import failure is surfaced by callers.
    push_history = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
CN_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def _to_cn_datetime(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(CN_TZ)
    if value.tzinfo is None:
        return value.replace(tzinfo=CN_TZ)
    return value.astimezone(CN_TZ)


def archive_market_report(
    content: str,
    *,
    job_id: str,
    title: str,
    run_dt: datetime | None = None,
    output_dir: Path | str | None = None,
) -> Path | None:
    """Write an A-share cron report to disk and to the dashboard DB."""
    body = (content or "").strip()
    if not body:
        return None

    local_dt = _to_cn_datetime(run_dt)
    out_dir = Path(output_dir).expanduser() if output_dir else DASHBOARD_HOME / "cron" / "output" / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{local_dt.strftime('%Y-%m-%d_%H-%M-%S')}.md"
    run_time = local_dt.strftime("%Y-%m-%d %H:%M:%S")
    payload = (
        f"# Cron Job: {title}\n\n"
        f"**Job ID:** {job_id}\n"
        f"**Run Time:** {run_time}\n"
        "**Mode:** standalone dashboard script\n"
        "**Status:** ok\n\n"
        "---\n\n"
        f"{body}\n"
    )
    path.write_text(payload, encoding="utf-8")
    write_market_report_to_db(body, path, job_id=job_id, title=title, run_dt=local_dt)
    return path


def write_market_report_to_db(
    content: str,
    archive_path: Path | None,
    *,
    job_id: str,
    title: str,
    run_dt: datetime | None = None,
) -> int:
    if push_history is None:
        return 0

    local_dt = _to_cn_datetime(run_dt)
    archive_stem = archive_path.stem if archive_path else local_dt.strftime("%Y-%m-%d_%H-%M-%S")
    source_id = f"cron_output_{job_id}_{archive_stem}"
    message = {
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
        "external_id": source_id,
        "title": title,
        "content": content,
        "chars": len(content),
        "matched": True,
        "kind": "cron_output",
        "delivery": {"mode": "dashboard_archive_only", "job_id": job_id},
        "metadata": {"job_name": title},
        "raw_path": str(archive_path or ""),
    }
    return push_history.upsert_many([message])
