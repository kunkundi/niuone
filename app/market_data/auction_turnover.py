"""Opening-auction turnover factor built from local market reports."""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import sqlite3
import statistics
from pathlib import Path
from typing import Any

from app.core.paths import get_dashboard_home


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUCTION_JOB_ID = "8453b3f28cd3"
CLOSE_JOB_ID = "67ac98149ead"
PROFILE_DAYS = 20
MIN_PROFILE_DAYS = 10
AUCTION_ELASTICITY = 0.5
MODEL = "auction_shrinkage_opening_5m_intraday_v4"
MODEL_LABEL = "竞价平方根收缩开盘 + 近20日5分钟日内成交分布"
SOURCE_NAME = "本地09:25竞价与盘后成交额记录"
SOURCE_URL = "https://quote.eastmoney.com/"


def _finite_float(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _amount_yi(number: str, unit: str) -> float | None:
    value = _finite_float(number)
    if value is None or value <= 0:
        return None
    multipliers = {
        "万亿": 10_000.0,
        "亿": 1.0,
        "万": 0.0001,
        "元": 0.00000001,
    }
    multiplier = multipliers.get(str(unit))
    return value * multiplier if multiplier is not None else None


def extract_auction_turnover_yi(content: str) -> float | None:
    """Extract the complete-market 09:25 amount, rejecting degraded reports."""

    text = str(content or "")
    if any(
        marker in text
        for marker in (
            "开盘后补全",
            "补全时点成交额",
            "历史补档",
            "不可回放",
            "竞价快照部分页失败",
        )
    ):
        return None
    sample_match = re.search(r"样本\s*`?([\d,]+)`?\s*只", text)
    sample_count = _finite_float(sample_match.group(1)) if sample_match else None
    if sample_count is None or sample_count < 4_000:
        return None
    match = re.search(
        r"(?m)^强高开\s*`?[\d,]+`?[^\n]*?\|\s*"
        r"竞价额\s*`?([\d,.]+)\s*(万亿|亿|万|元)`?",
        text,
    )
    return _amount_yi(match.group(1), match.group(2)) if match else None


def extract_close_turnover_yi(content: str) -> float | None:
    """Extract the complete-market amount from the deterministic close section."""

    text = str(content or "")
    if "现货行情未取到有效数据" in text:
        return None
    match = re.search(
        r"(?m)^(?:封死)?涨停\s*`?[\d,]+`?[^\n]*?\|\s*"
        r"成交额\s*`?([\d,.]+)\s*(万亿|亿|万|元)`?",
        text,
    )
    return _amount_yi(match.group(1), match.group(2)) if match else None


def _clock(value: Any) -> dt.time | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"\d{4}-\d{2}-\d{2} (\d{2}):(\d{2})(?::\d{2})?", text)
    if not match:
        return None
    try:
        return dt.time(int(match.group(1)), int(match.group(2)))
    except ValueError:
        return None


def _default_db_path() -> Path:
    return get_dashboard_home(PROJECT_ROOT) / "push_history.db"


def _default_state_path() -> Path:
    return (
        get_dashboard_home(PROJECT_ROOT)
        / "cron"
        / "state"
        / "a_share_auction_summary.json"
    )


def _structured_auction_series(path: Path) -> dict[str, float]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    samples = payload.get("samples") if isinstance(payload, dict) else None
    result: dict[str, float] = {}
    for raw in samples or []:
        sample = raw if isinstance(raw, dict) else {}
        day = str(sample.get("date") or "")
        captured_at = str(sample.get("captured_at") or "")
        amount = _finite_float(sample.get("auction_turnover_yi"))
        quote_count = _finite_float(sample.get("quote_count"))
        captured_clock = _clock(captured_at)
        if (
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)
            and captured_at[:10] == day
            and captured_clock is not None
            and captured_clock < dt.time(9, 27)
            and amount is not None
            and amount > 0
            and quote_count is not None
            and quote_count >= 4_000
        ):
            result[day] = amount
    return result


def load_turnover_report_series(
    *,
    db_path: Path | None = None,
    state_path: Path | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Load pure auction and complete close amounts without exposing report text."""

    auction = _structured_auction_series(state_path or _default_state_path())
    close: dict[str, float] = {}
    path = db_path or _default_db_path()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    try:
        auction_rows = connection.execute(
            "SELECT time_text, content FROM dashboard_messages "
            "WHERE source_id LIKE ? ORDER BY timestamp",
            (f"cron_output_{AUCTION_JOB_ID}%",),
        )
        for time_text, content in auction_rows:
            day = str(time_text or "")[:10]
            clock = _clock(time_text)
            amount = extract_auction_turnover_yi(str(content or ""))
            if (
                re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)
                and clock is not None
                and clock < dt.time(9, 27)
                and amount is not None
            ):
                auction.setdefault(day, amount)
        close_rows = connection.execute(
            "SELECT time_text, content FROM dashboard_messages "
            "WHERE source_id LIKE ? ORDER BY timestamp",
            (f"cron_output_{CLOSE_JOB_ID}%",),
        )
        for time_text, content in close_rows:
            day = str(time_text or "")[:10]
            clock = _clock(time_text)
            amount = extract_close_turnover_yi(str(content or ""))
            if (
                re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)
                and clock is not None
                and clock >= dt.time(15, 0)
                and amount is not None
            ):
                close[day] = amount
    finally:
        connection.close()
    return auction, close


def build_auction_turnover_profile(
    before_date: dt.date,
    *,
    auction_by_date: dict[str, float],
    close_by_date: dict[str, float],
    profile_days: int = PROFILE_DAYS,
    min_profile_days: int = MIN_PROFILE_DAYS,
) -> dict[str, Any]:
    """Build a shrinkage prior from matched auction and close turnover."""

    current_day = before_date.isoformat()
    current_auction = _finite_float(auction_by_date.get(current_day))
    if current_auction is None or current_auction <= 0:
        raise ValueError(f"Opening auction turnover is unavailable for {current_day}")
    eligible = sorted(
        day
        for day in set(auction_by_date).intersection(close_by_date)
        if day < current_day
        and _finite_float(auction_by_date.get(day)) is not None
        and _finite_float(close_by_date.get(day)) is not None
        and float(auction_by_date[day]) > 0
        and float(close_by_date[day]) > 0
    )
    selected = eligible[-max(1, int(profile_days)):]
    required = max(1, min(int(profile_days), int(min_profile_days)))
    if len(selected) < required:
        raise ValueError(
            f"Opening auction turnover profile has {len(selected)} matched days; "
            f"requires {required}"
        )
    historical_auction_median = statistics.median(
        float(auction_by_date[day]) for day in selected
    )
    historical_turnover_median = statistics.median(
        float(close_by_date[day]) for day in selected
    )
    if historical_auction_median <= 0 or historical_turnover_median <= 0:
        raise ValueError("Opening auction turnover profile has no valid median")
    relative_auction = current_auction / historical_auction_median
    estimated = historical_turnover_median * math.pow(
        relative_auction,
        AUCTION_ELASTICITY,
    )
    if not math.isfinite(estimated) or estimated <= 0:
        raise ValueError("Opening auction turnover profile has no valid estimate")
    estimated = max(current_auction, estimated)
    effective_multiplier = estimated / current_auction
    return {
        "model": MODEL,
        "model_label": MODEL_LABEL,
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "profile_days": len(selected),
        "profile_start": selected[0],
        "profile_end": selected[-1],
        "auction_turnover_yi": round(current_auction, 2),
        "auction_multiplier": round(effective_multiplier, 6),
        "auction_elasticity": AUCTION_ELASTICITY,
        "historical_auction_median_yi": round(historical_auction_median, 2),
        "historical_turnover_median_yi": round(historical_turnover_median, 2),
        "opening_estimated_turnover_yi": round(estimated, 2),
        "daily_profiles": [
            {
                "date": day,
                "auction_turnover_yi": round(float(auction_by_date[day]), 2),
                "turnover_yi": round(float(close_by_date[day]), 2),
            }
            for day in selected
        ],
    }


def fetch_auction_turnover_profile(
    before_date: dt.date,
    *,
    db_path: Path | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    auction, close = load_turnover_report_series(
        db_path=db_path,
        state_path=state_path,
    )
    return build_auction_turnover_profile(
        before_date,
        auction_by_date=auction,
        close_by_date=close,
    )


__all__ = [
    "build_auction_turnover_profile",
    "extract_auction_turnover_yi",
    "extract_close_turnover_yi",
    "fetch_auction_turnover_profile",
    "load_turnover_report_series",
]
