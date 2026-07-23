#!/usr/bin/env python3
"""Refresh the durable iWencai dragon-tiger snapshot for the Dashboard."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dashboard.apis.iwencai_service import (
    expire_dragon_tiger_archives,
    fetch_dragon_tiger,
    write_dragon_tiger_snapshot,
)
from niuone_paths import get_dashboard_home


PROJECT_ROOT = Path(os.environ.get("NIUONE_ROOT") or Path.cwd()).resolve()
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
SNAPSHOT_FILE = Path(
    os.environ.get("IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE")
    or DASHBOARD_HOME / "cron" / "output" / "iwencai_dragon_tiger_latest.json"
).expanduser()


def refresh_snapshot(path: Path = SNAPSHOT_FILE) -> tuple[dict[str, object], bool]:
    payload = fetch_dragon_tiger()
    saved = write_dragon_tiger_snapshot(path, payload)
    if saved:
        try:
            payload["expired_archive_count"] = expire_dragon_tiger_archives(
                path.parent / "iwencai_dragon_tiger"
            )
        except OSError as exc:
            payload["archive_cleanup_error"] = type(exc).__name__
    return payload, saved


def main() -> int:
    payload, saved = refresh_snapshot()
    if saved:
        print(
            f"问财龙虎榜最新快照已更新：{payload.get('date')}，"
            f"{len(payload.get('items') or [])} 条"
        )
        if payload.get("archive_cleanup_error"):
            print(
                f"[WARN] 旧龙虎榜归档清理失败：{payload['archive_cleanup_error']}",
                file=sys.stderr,
            )
        return 0
    if payload.get("error") == "iwencai_disabled":
        print("问财数据源未启用，跳过龙虎榜快照更新")
        return 0
    if payload.get("available") is True:
        print(f"问财龙虎榜当日暂无数据：{payload.get('date')}")
        return 0
    print(f"问财龙虎榜快照更新失败：{payload.get('error') or 'upstream_unavailable'}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
