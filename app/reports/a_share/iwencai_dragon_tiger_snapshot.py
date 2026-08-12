#!/usr/bin/env python3
"""Refresh the durable iWencai dragon-tiger snapshot for the Dashboard."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from dashboard.apis.iwencai_service import (
    enrich_consecutive_dragon_tiger_news,
    expire_dragon_tiger_archives,
    fetch_dragon_tiger,
    mark_consecutive_dragon_tiger_items,
    read_dragon_tiger_snapshot,
    write_dragon_tiger_snapshot,
)
from market_data.news_precheck import repair_cached_news_record
from a_share_calendar import trading_day_status
from niuone_paths import get_dashboard_home


PROJECT_ROOT = Path(os.environ.get("NIUONE_ROOT") or Path.cwd()).resolve()
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
SNAPSHOT_FILE = Path(
    os.environ.get("IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE")
    or DASHBOARD_HOME / "cron" / "output" / "iwencai_dragon_tiger_latest.json"
).expanduser()
CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_DRAGON_TIGER_CRON = "0 18 * * 1-5"
SNAPSHOT_STAGE_RANK = {
    "core": 1,
    "details": 2,
    "news": 3,
}


def _snapshot_stage_rank(payload: Mapping[str, Any] | None) -> int:
    if not isinstance(payload, Mapping):
        return 0
    stage = str(payload.get("snapshot_stage") or "").strip().lower()
    if stage:
        return SNAPSHOT_STAGE_RANK.get(stage, 0)
    # Snapshots written before staged persistence were saved only after the
    # complete refresh path, so they must not be downgraded by an intermediate
    # stage during a same-day retry.
    return SNAPSHOT_STAGE_RANK["news"]


def _stage_can_replace(
    previous_snapshot: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
) -> bool:
    if not isinstance(previous_snapshot, Mapping):
        return True
    if str(previous_snapshot.get("date") or "") != str(candidate.get("date") or ""):
        return True
    return _snapshot_stage_rank(candidate) >= _snapshot_stage_rank(previous_snapshot)


def _news_precheck_items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates: list[Mapping[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        try:
            limit_up_streak = int(item.get("limit_up_streak") or 0)
        except (TypeError, ValueError):
            limit_up_streak = 0
        try:
            consecutive_days = int(item.get("consecutive_list_days") or 0)
        except (TypeError, ValueError):
            consecutive_days = 0
        is_consecutive = (
            item.get("consecutive_listed") is True and consecutive_days >= 2
        )
        if limit_up_streak < 2 and not is_consecutive:
            continue
        candidates.append(item)
    return candidates


def _pending_news_items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        item
        for item in _news_precheck_items(payload)
        if not isinstance(item.get("news_precheck"), Mapping)
        or item.get("news_precheck", {}).get("checked") is not True
    ]


def _locally_repairable_news_items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    repairable: list[Mapping[str, Any]] = []
    for item in _news_precheck_items(payload):
        record = item.get("news_precheck")
        if not isinstance(record, Mapping):
            continue
        if repair_cached_news_record(record) != dict(record):
            repairable.append(item)
    return repairable


def _news_tracking_is_current(payload: Mapping[str, Any]) -> bool:
    candidates = _news_precheck_items(payload)
    checked_codes: list[str] = []
    pending_codes: list[str] = []
    available_count = 0
    for item in candidates:
        code = str(item.get("code") or item.get("name") or "").strip()
        record = item.get("news_precheck")
        if isinstance(record, Mapping) and record.get("checked") is True:
            if code:
                checked_codes.append(code)
            if record.get("available") is True:
                available_count += 1
        elif code:
            pending_codes.append(code)

    expected = {
        "continuous_news_checked_codes": checked_codes,
        "continuous_news_pending_codes": pending_codes,
        "continuous_news_checked_count": len(checked_codes),
        "continuous_news_pending_count": len(pending_codes),
        "continuous_news_available_count": available_count,
        "continuous_news_complete": not pending_codes,
        "limit_up_news_candidate_count": len(candidates),
        "limit_up_news_checked_codes": checked_codes,
        "limit_up_news_pending_codes": pending_codes,
        "limit_up_news_checked_count": len(checked_codes),
        "limit_up_news_pending_count": len(pending_codes),
        "limit_up_news_available_count": available_count,
        "limit_up_news_complete": not pending_codes,
    }
    return all(payload.get(field) == value for field, value in expected.items())


def backfill_snapshot_news(
    path: Path = SNAPSHOT_FILE,
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    """Check pending stocks and locally repair unambiguous legacy summaries."""

    snapshot = read_dragon_tiger_snapshot(path)
    if snapshot is None or not _news_precheck_items(snapshot):
        return snapshot, False
    if (
        not _pending_news_items(snapshot)
        and not _locally_repairable_news_items(snapshot)
        and _news_tracking_is_current(snapshot)
    ):
        return snapshot, False
    updated = enrich_consecutive_dragon_tiger_news(
        snapshot,
        env=env,
        previous_snapshot=snapshot,
    )
    return updated, write_dragon_tiger_snapshot(path, updated)


def _configured_query_hhmm(values: Mapping[str, str]) -> tuple[int, int] | None:
    raw = str(values.get("IWENCAI_DRAGON_TIGER_CRON") or DEFAULT_DRAGON_TIGER_CRON).strip()
    if len(parts := raw.split()) == 5 and parts[0].isdigit() and parts[1].isdigit():
        minute, hour = int(parts[0]), int(parts[1])
    elif len(raw.split(":")) == 2 and all(part.isdigit() for part in raw.split(":")):
        hour, minute = (int(part) for part in raw.split(":"))
    else:
        return None
    return (hour, minute) if 0 <= hour <= 23 and 0 <= minute <= 59 else None


def catch_up_trade_date(
    *,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> str:
    """Resolve the latest trading date whose configured query time has passed."""

    values = os.environ if env is None else env
    current = now or datetime.now(CN_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CN_TZ)
    current = current.astimezone(CN_TZ)
    calendar = trading_day_status(current, allow_refresh=False)
    today = str(calendar.get("date") or current.strftime("%Y-%m-%d"))
    configured = _configured_query_hhmm(values)
    if calendar.get("is_trading_day") is True and configured is not None:
        if (current.hour, current.minute) >= configured:
            return today
    return str(calendar.get("previous_trading_day") or "")


def refresh_snapshot(
    path: Path = SNAPSHOT_FILE,
    *,
    env: dict[str, str] | None = None,
    trade_date: str | None = None,
) -> tuple[dict[str, object], bool]:
    previous_snapshot = read_dragon_tiger_snapshot(path)
    core_saved = False

    def persist_core_snapshot(core_payload: Mapping[str, Any]) -> None:
        nonlocal core_saved
        calendar = trading_day_status(
            str(core_payload.get("date") or ""),
            allow_refresh=False,
        )
        staged = mark_consecutive_dragon_tiger_items(
            core_payload,
            previous_snapshot,
            previous_trading_day=str(calendar.get("previous_trading_day") or ""),
        )
        staged["snapshot_stage"] = "core"
        if _stage_can_replace(previous_snapshot, staged):
            core_saved = write_dragon_tiger_snapshot(path, staged)

    fetch_kwargs = {"on_core_payload": persist_core_snapshot}
    payload = (
        fetch_dragon_tiger(trade_date, **fetch_kwargs)
        if trade_date
        else fetch_dragon_tiger(**fetch_kwargs)
    )
    detail_saved = False
    if payload.get("available") is True and payload.get("items"):
        calendar = trading_day_status(
            str(payload.get("date") or ""),
            allow_refresh=False,
        )
        payload = mark_consecutive_dragon_tiger_items(
            payload,
            previous_snapshot,
            previous_trading_day=str(calendar.get("previous_trading_day") or ""),
        )
        payload["snapshot_stage"] = "details"
        if _stage_can_replace(previous_snapshot, payload):
            detail_saved = write_dragon_tiger_snapshot(path, payload)
        payload = enrich_consecutive_dragon_tiger_news(
            payload,
            env=env,
            previous_snapshot=previous_snapshot,
        )
        payload["snapshot_stage"] = "news"
    saved = write_dragon_tiger_snapshot(path, payload) or detail_saved or core_saved
    if not saved and previous_snapshot is not None:
        # A failed or empty new-date query still leaves the previous snapshot
        # visible.  Complete its pending news after the current pull attempt so
        # that this work can never consume the budget needed to publish a new
        # core list.
        backfill_snapshot_news(path, env=env)
    if saved:
        try:
            payload["expired_archive_count"] = expire_dragon_tiger_archives(
                path.parent / "iwencai_dragon_tiger"
            )
        except OSError as exc:
            payload["archive_cleanup_error"] = type(exc).__name__
    return payload, saved


def catch_up_snapshot(
    path: Path = SNAPSHOT_FILE,
    *,
    env: dict[str, str] | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, object] | None, bool]:
    target_date = catch_up_trade_date(env=env, now=now)
    latest = read_dragon_tiger_snapshot(path)
    latest_date = str(latest.get("date") or "") if latest else ""
    if not target_date or latest_date > target_date:
        return latest, False
    if latest_date == target_date:
        if _snapshot_stage_rank(latest) < SNAPSHOT_STAGE_RANK["details"]:
            return refresh_snapshot(path, env=env, trade_date=target_date)
        return backfill_snapshot_news(path, env=env)
    return refresh_snapshot(path, env=env, trade_date=target_date)


def main() -> int:
    parser = argparse.ArgumentParser(description="刷新或补检问财龙虎榜滚动快照")
    parser.add_argument("--date", default="", help="指定需要拉取的交易日 YYYY-MM-DD")
    parser.add_argument("--catch-up", action="store_true", help="追补最近应有的交易日快照和消息面")
    parser.add_argument("--backfill-only", action="store_true", help="仅补检当前快照中的待检索股票")
    args = parser.parse_args()
    if args.backfill_only:
        payload, saved = backfill_snapshot_news()
    elif args.catch_up:
        payload, saved = catch_up_snapshot()
    else:
        payload, saved = refresh_snapshot(trade_date=args.date or None)
    if payload is None:
        print("暂无可补检的问财龙虎榜快照")
        return 0
    if args.backfill_only and not saved:
        print("当前问财龙虎榜快照无需消息面补检")
        return 0
    if args.catch_up and not saved and payload.get("snapshot") is True:
        print(f"问财龙虎榜快照无需追补：{payload.get('date')}")
        return 0
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
