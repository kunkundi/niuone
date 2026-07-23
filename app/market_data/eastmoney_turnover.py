"""Eastmoney-backed intraday turnover profiles and full-day estimates."""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


CN_TZ = dt.timezone(dt.timedelta(hours=8))
SOURCE_NAME = "东方财富沪深指数分钟线"
SOURCE_URL = "https://push2his.eastmoney.com/"
FALLBACK_SOURCE_NAME = "腾讯证券沪深A股实时行情（兜底）"
FALLBACK_SOURCE_URL = "https://gu.qq.com/"
ESTIMATE_MODEL = "eastmoney_20d_intraday_median"
ESTIMATE_MODEL_LABEL = "东方财富近20日5分钟成交分布中位数"
SECIDS = ("1.000001", "0.399001")
PROFILE_DAYS = 20
PROFILE_INTERVAL_MINUTES = 5
PROFILE_MIN_BARS = 48
PROFILE_FETCH_LIMIT = 2_000
CURRENT_FETCH_LIMIT = 300
REQUEST_ATTEMPTS = 2
PROFILE_RETRY_SECONDS = 300.0

_PROFILE_CACHE_LOCK = threading.Lock()
_PROFILE_CACHE: dict[str, dict[str, Any]] = {}


def _finite_float(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def trading_progress_minutes(value: dt.datetime | str) -> float | None:
    """Map a Beijing trading timestamp onto the 0..240 minute session axis."""

    if isinstance(value, dt.datetime):
        hour = value.hour
        minute = value.minute
        second = value.second
    else:
        match = re.search(r"(?:\d{4}-\d{2}-\d{2}\s+)?(\d{2}):(\d{2})(?::(\d{2}))?", str(value))
        if not match:
            return None
        hour, minute, second = (int(part or 0) for part in match.groups())
    clock = hour * 60 + minute + second / 60
    if 9 * 60 + 30 <= clock <= 11 * 60 + 30:
        return clock - (9 * 60 + 30)
    if 13 * 60 <= clock <= 15 * 60:
        return 120 + clock - 13 * 60
    return None


def _download_kline(secid: str, interval_minutes: int, limit: int, timeout: float) -> str:
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "klt": str(interval_minutes),
        "fqt": "1",
        "beg": "0",
        "end": "20500101",
        "lmt": str(limit),
    }
    request = Request(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode(params),
        headers={
            "User-Agent": "Mozilla/5.0 NiuOne/1.0",
            "Referer": "https://quote.eastmoney.com/",
            "Connection": "close",
        },
    )
    last_error: Exception | None = None
    for attempt in range(REQUEST_ATTEMPTS):
        try:
            with urlopen(request, timeout=max(1.0, timeout)) as response:
                return response.read().decode("utf-8", errors="ignore")
        except Exception as exc:
            last_error = exc
            if attempt + 1 < REQUEST_ATTEMPTS:
                time.sleep(0.15 * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Eastmoney turnover request did not run")


def parse_kline_amounts(body: str, secid: str) -> dict[str, dict[float, float]]:
    """Parse Eastmoney K lines into per-session-minute turnover amounts in yuan."""

    payload = json.loads(str(body or "{}"))
    data = payload.get("data") if isinstance(payload, dict) else None
    expected_code = secid.split(".", 1)[-1]
    if not isinstance(data, dict) or str(data.get("code") or "") != expected_code:
        raise ValueError(f"Eastmoney turnover response missing index {secid}")
    result: dict[str, dict[float, float]] = {}
    for raw in data.get("klines") or []:
        fields = str(raw or "").split(",")
        if len(fields) < 7 or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", fields[0]
        ):
            continue
        progress = trading_progress_minutes(fields[0])
        amount_yuan = _finite_float(fields[6])
        if progress is None or amount_yuan is None or amount_yuan < 0:
            continue
        day = fields[0][:10]
        result.setdefault(day, {})[progress] = amount_yuan
    if not result:
        raise ValueError(f"Eastmoney turnover response has no minute amounts for {secid}")
    return result


def build_turnover_profile(
    series_by_secid: dict[str, dict[str, dict[float, float]]],
    before_date: dt.date,
    *,
    profile_days: int = PROFILE_DAYS,
) -> dict[str, Any]:
    """Build a latest-N-day cumulative turnover profile from both exchanges."""

    if any(secid not in series_by_secid for secid in SECIDS):
        raise ValueError("Eastmoney turnover profile requires Shanghai and Shenzhen")
    cutoff = before_date.isoformat()
    common_dates = set.intersection(
        *(set(series_by_secid[secid]) for secid in SECIDS)
    )
    complete_days: list[tuple[str, list[tuple[float, float]], float]] = []
    for day in sorted(date for date in common_dates if date < cutoff):
        common_progress = set.intersection(
            *(set(series_by_secid[secid][day]) for secid in SECIDS)
        )
        ordered_progress = sorted(common_progress)
        if (
            len(ordered_progress) < PROFILE_MIN_BARS
            or not ordered_progress
            or ordered_progress[0] > PROFILE_INTERVAL_MINUTES
            or ordered_progress[-1] < 240
        ):
            continue
        amounts = [
            sum(series_by_secid[secid][day][progress] for secid in SECIDS)
            for progress in ordered_progress
        ]
        full_day_yuan = sum(amounts)
        if full_day_yuan <= 0:
            continue
        cumulative = 0.0
        fractions: list[tuple[float, float]] = []
        for progress, amount in zip(ordered_progress, amounts):
            cumulative += amount
            fractions.append((progress, min(1.0, cumulative / full_day_yuan)))
        complete_days.append((day, fractions, full_day_yuan))
    selected = complete_days[-max(1, int(profile_days)):]
    if len(selected) < max(1, int(profile_days)):
        raise ValueError(
            f"Eastmoney turnover profile has {len(selected)} complete days; "
            f"requires {profile_days}"
        )
    return {
        "model": ESTIMATE_MODEL,
        "model_label": ESTIMATE_MODEL_LABEL,
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "interval_minutes": PROFILE_INTERVAL_MINUTES,
        "profile_days": len(selected),
        "profile_start": selected[0][0],
        "profile_end": selected[-1][0],
        "daily_profiles": [
            {
                "date": day,
                "fractions": fractions,
                "turnover_yi": round(full_day_yuan / 100_000_000, 2),
            }
            for day, fractions, full_day_yuan in selected
        ],
    }


def _fraction_at_progress(
    fractions: list[tuple[float, float]],
    progress: float,
) -> float | None:
    if not fractions:
        return None
    if progress <= fractions[0][0]:
        return fractions[0][1]
    for index in range(1, len(fractions)):
        right_progress, right_fraction = fractions[index]
        if progress > right_progress:
            continue
        left_progress, left_fraction = fractions[index - 1]
        if right_progress <= left_progress:
            return right_fraction
        ratio = (progress - left_progress) / (right_progress - left_progress)
        return left_fraction + (right_fraction - left_fraction) * ratio
    return fractions[-1][1]


def estimate_full_day_turnover_yi(
    actual_turnover_yi: Any,
    generated_at: dt.datetime,
    profile: dict[str, Any],
) -> float | None:
    """Estimate full-day turnover using the median same-time historical share."""

    actual = _finite_float(actual_turnover_yi)
    progress = trading_progress_minutes(generated_at)
    if actual is None or actual < 0 or progress is None:
        return None
    fractions = []
    for raw in profile.get("daily_profiles") or []:
        daily = raw if isinstance(raw, dict) else {}
        points = daily.get("fractions") or []
        value = _fraction_at_progress(points, progress)
        if value is not None and 0 < value <= 1:
            fractions.append(value)
    if not fractions:
        return None
    expected_share = statistics.median(fractions)
    if expected_share <= 0:
        return None
    return round(max(actual, actual / expected_share), 2)


def _download_pair(
    interval_minutes: int,
    limit: int,
    downloader: Callable[[str, int, int, float], str],
) -> dict[str, str]:
    with ThreadPoolExecutor(max_workers=len(SECIDS)) as pool:
        futures = {
            secid: pool.submit(downloader, secid, interval_minutes, limit, 6.0)
            for secid in SECIDS
        }
        return {secid: future.result() for secid, future in futures.items()}


def fetch_turnover_profile(
    before_date: dt.date,
    *,
    downloader: Callable[[str, int, int, float], str] = _download_kline,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Fetch and cache a 20-day Eastmoney profile with bounded failure retries."""

    cache_key = before_date.isoformat()
    now = monotonic()
    with _PROFILE_CACHE_LOCK:
        cached = _PROFILE_CACHE.get(cache_key)
        if cached and cached.get("value") is not None:
            return dict(cached["value"])
        if cached and now < float(cached.get("retry_after") or 0):
            raise RuntimeError("Eastmoney turnover profile is waiting to retry")
        try:
            bodies = _download_pair(
                PROFILE_INTERVAL_MINUTES,
                PROFILE_FETCH_LIMIT,
                downloader,
            )
            parsed = {
                secid: parse_kline_amounts(body, secid)
                for secid, body in bodies.items()
            }
            profile = build_turnover_profile(parsed, before_date)
        except Exception:
            _PROFILE_CACHE[cache_key] = {
                "value": None,
                "retry_after": now + PROFILE_RETRY_SECONDS,
            }
            raise
        _PROFILE_CACHE.clear()
        _PROFILE_CACHE[cache_key] = {"value": profile, "retry_after": 0.0}
        return dict(profile)


def fetch_current_turnover_yi(
    generated_at: dt.datetime,
    *,
    downloader: Callable[[str, int, int, float], str] = _download_kline,
) -> float:
    """Fetch current Shanghai+Shenzhen turnover from Eastmoney one-minute bars."""

    bodies = _download_pair(1, CURRENT_FETCH_LIMIT, downloader)
    target_day = generated_at.date().isoformat()
    target_progress = trading_progress_minutes(generated_at)
    if target_progress is None:
        raise ValueError("Current turnover requested outside A-share trading session")
    total_yuan = 0.0
    for secid, body in bodies.items():
        by_date = parse_kline_amounts(body, secid)
        amounts = by_date.get(target_day)
        if not amounts:
            raise ValueError(f"Eastmoney current turnover missing {target_day} for {secid}")
        latest_progress = max(amounts)
        if latest_progress < max(0.0, target_progress - 5.0):
            raise ValueError(f"Eastmoney current turnover is stale for {secid}")
        total_yuan += sum(
            amount for progress, amount in amounts.items()
            if progress <= target_progress + 1.0
        )
    if total_yuan <= 0:
        raise ValueError("Eastmoney current turnover is empty")
    return round(total_yuan / 100_000_000, 2)


def fetch_market_turnover_estimate(
    generated_at: dt.datetime,
    fallback_actual_turnover_yi: Any,
    *,
    profile_fetcher: Callable[[dt.date], dict[str, Any]] = fetch_turnover_profile,
    current_fetcher: Callable[[dt.datetime], float] = fetch_current_turnover_yi,
) -> dict[str, Any]:
    """Return a 20-day estimate, using Tencent actual turnover only as fallback."""

    try:
        actual = current_fetcher(generated_at)
        actual_source = SOURCE_NAME
        actual_source_url = SOURCE_URL
    except Exception as exc:
        actual = _finite_float(fallback_actual_turnover_yi)
        if actual is None or actual < 0:
            raise RuntimeError("Eastmoney and Tencent current turnover are unavailable") from exc
        actual_source = FALLBACK_SOURCE_NAME
        actual_source_url = FALLBACK_SOURCE_URL
    result = {
        "actual_turnover_yi": round(actual, 2),
        "turnover_actual_source": actual_source,
        "turnover_actual_source_url": actual_source_url,
    }
    try:
        profile = profile_fetcher(generated_at.date())
    except Exception as exc:
        print(
            f"[WARN] Eastmoney 20-day turnover profile unavailable "
            f"error={type(exc).__name__}",
            flush=True,
        )
        result["turnover_estimate_warning"] = "近20日量能模型暂不可用"
        return result
    estimated = estimate_full_day_turnover_yi(actual, generated_at, profile)
    if estimated is None:
        result["turnover_estimate_warning"] = "当前时点暂不能估算全天量能"
        return result
    daily_profiles = profile.get("daily_profiles") or []
    previous = daily_profiles[-1] if daily_profiles else {}
    result.update({
        "estimated_turnover_yi": estimated,
        "turnover_estimate_model": str(profile.get("model") or ESTIMATE_MODEL),
        "turnover_estimate_model_label": str(
            profile.get("model_label") or ESTIMATE_MODEL_LABEL
        ),
        "turnover_estimate_source": str(profile.get("source") or SOURCE_NAME),
        "turnover_estimate_source_url": str(profile.get("source_url") or SOURCE_URL),
        "turnover_profile_days": int(profile.get("profile_days") or 0),
        "turnover_profile_start": str(profile.get("profile_start") or ""),
        "turnover_profile_end": str(profile.get("profile_end") or ""),
        "turnover_profile_interval_minutes": int(
            profile.get("interval_minutes") or PROFILE_INTERVAL_MINUTES
        ),
    })
    previous_turnover = _finite_float(previous.get("turnover_yi"))
    previous_date = str(previous.get("date") or "")
    if previous_turnover is not None and previous_turnover > 0 and previous_date:
        result.update({
            "previous_turnover_yi": round(previous_turnover, 2),
            "turnover_increment_yi": round(estimated - previous_turnover, 2),
            "turnover_comparison_date": previous_date,
            "turnover_comparison_source": SOURCE_NAME,
            "turnover_comparison_source_url": SOURCE_URL,
        })
    return result


__all__ = [
    "build_turnover_profile",
    "estimate_full_day_turnover_yi",
    "fetch_current_turnover_yi",
    "fetch_market_turnover_estimate",
    "fetch_turnover_profile",
    "parse_kline_amounts",
    "trading_progress_minutes",
]
