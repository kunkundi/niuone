#!/usr/bin/env python3
"""Refresh the durable iWencai dragon-tiger snapshot for the Dashboard."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dashboard.apis.iwencai_service import (
    fetch_dragon_tiger,
    write_dragon_tiger_archive,
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
    archived = write_dragon_tiger_archive(path.parent / "iwencai_dragon_tiger", payload)
    saved = write_dragon_tiger_snapshot(path, payload) and archived
    return payload, saved


def main() -> int:
    payload, saved = refresh_snapshot()
    if saved:
        print(
            f"问财龙虎榜快照及交易日归档已更新：{payload.get('date')}，"
            f"{len(payload.get('items') or [])} 条"
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
