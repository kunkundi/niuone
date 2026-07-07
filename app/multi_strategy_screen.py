#!/usr/bin/env python3
"""
牛牛1号 · 多战法扫描器 — A股主板全市场综合评分。

评估多战法（趋势/突破策略 + Z哥），每只票输出多战法分数
+ 最优战法标签，供牛牛实战模型决策时参考。

数据源（全部绕过Eastmoney代理封锁）：
  1. akshare.stock_info_a_code_name() — 代码池
  2. 腾讯 qt.gtimg.cn 批量行情 — 实时报价
  3. 腾讯 web.ifzq.gtimg.cn fqkline — 日K数据

用法：
  cd /path/to/NiuOne/app
  DASHBOARD_HOME=/path/to/NiuOne/.local-data/runtime python multi_strategy_screen.py [--json]

输出格式（JSON）：
{
  "generated_at": "2026-06-20 10:00:00",
  "candidates": [
    {
      "code": "603019", "name": "中科曙光",
      "price": 45.20, "change_pct": 2.3,
      "best_strategy": "shaofu_b1",
      "best_score": 8,
      "strategies": {
        "shaofu_b1":    {"score": 8, "verdict": "高匹配少妇B1", ...},
        "trend_pullback":{"score": 6, "verdict": "中等匹配趋势回踩", ...},
        "breakout":     {"score": 4, "verdict": "弱匹配突破", ...}
      }
    }
  ],
  "total_analyzed": 387
}
"""
import json
import os
import re
import shlex
import statistics
import sys
import time
import urllib.request
from pathlib import Path
from collections.abc import Callable
from typing import Any

from niuone_paths import get_dashboard_env_file, get_dashboard_home
from strategy_registry import (
    DISPLAY_STRATEGY_ORDER,
    PERSONA_STRATEGY_ENV,
    STRATEGY_SOURCE_ENV,
    STRATEGY_DEFINITIONS,
    STRATEGY_META,
    STRATEGY_SCORE_PROFILES,
    enabled_persona_strategy_ids,
    enabled_strategy_ids,
    enabled_strategy_meta,
    enabled_strategy_score_profiles,
)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TENCENT_QUOTE = "https://qt.gtimg.cn/q="
TENCENT_KLINE = "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
DASHBOARD_HOME = get_dashboard_home(Path(__file__).resolve().parents[1])
DASHBOARD_ENV_FILE = get_dashboard_env_file(Path(__file__).resolve().parents[1])
B1_OUTPUT_DIR = DASHBOARD_HOME / "cron" / "output"
B1_CACHE_FILE = B1_OUTPUT_DIR / "b1_screen_latest.json"
MULTI_STRATEGY_CACHE = B1_OUTPUT_DIR / "multi_strategy_latest.json"
STOCK_INDUSTRY_CACHE = B1_OUTPUT_DIR / "stock_industry_cache.json"
B1_HISTORY_DIR = B1_OUTPUT_DIR / "b1_history"
MULTI_STRATEGY_HISTORY = B1_OUTPUT_DIR / "multi_strategy_history"
DISPLAY_CANDIDATE_LIMIT = 16
DISPLAY_HEAD_LIMIT = 8
TRADE_CANDIDATE_LIMIT = 8
COMMON_MAX_BBI_DISTANCE_PCT = 6.5
B1_CORE_J_CEILING = -10.0
B1_WATCH_J_CEILING = 12.0
LI_DAXIAO_MIN_AMOUNT = 1.2e9
LI_DAXIAO_MAX_TURNOVER = 6.0
LI_DAXIAO_HOT_TURNOVER = 8.0
LI_DAXIAO_MAX_BBI_DISTANCE = 3.5
LI_DAXIAO_MAX_DAILY_CHASE_PCT = 3.5
_LOCAL_SITE_PACKAGES_READY = False
_STOCK_INDUSTRY_MEMORY_CACHE: dict[str, str] | None = None


# ========== helpers ==========

def safe_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None

def safe_round(v, n=2):
    if v is None:
        return None
    return round(v, n)


def dashboard_env_value(name: str) -> str | None:
    if name in os.environ:
        return os.environ.get(name)
    try:
        lines = DASHBOARD_ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if key.strip() != name:
            continue
        try:
            parsed = shlex.split(raw_value.strip(), posix=True)
            return parsed[0] if parsed else ""
        except ValueError:
            return raw_value.strip().strip("\"'")
    return None


def enabled_persona_strategy_setting() -> str | None:
    return dashboard_env_value(PERSONA_STRATEGY_ENV)


def strategy_source_setting() -> str | None:
    return dashboard_env_value(STRATEGY_SOURCE_ENV)


def active_strategy_scorers() -> dict[str, Callable[[list[dict[str, Any]]], dict[str, Any] | None]]:
    enabled = enabled_strategy_ids(enabled_persona_strategy_setting(), strategy_source_setting())
    return {strategy_id: scorer for strategy_id, scorer in STRATEGY_SCORERS.items() if strategy_id in enabled}


def active_strategy_meta() -> dict[str, dict[str, Any]]:
    return enabled_strategy_meta(enabled_persona_strategy_setting(), strategy_source_setting())


def active_strategy_score_profiles() -> dict[str, dict[str, Any]]:
    return enabled_strategy_score_profiles(enabled_persona_strategy_setting(), strategy_source_setting())


def with_strategy_profile(strategy_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    profile = STRATEGY_SCORE_PROFILES.get(strategy_name, {})
    score = float(payload.get("score") or 0)
    threshold = float(profile.get("entry_threshold", 8.0))
    priority = int(profile.get("priority", 50))
    payload["score"] = round(score, 1)
    payload["entry_threshold"] = threshold
    payload["strategy_priority"] = priority
    blockers = strategy_hard_blockers(strategy_name, payload)
    payload["hard_blockers"] = blockers
    payload["actionable"] = score >= threshold and not blockers
    payload["decision_score"] = round(score + priority / 100 - len(blockers) * 1.5, 2)
    for key in ("score_basis", "position_hint", "time_stop", "certainty_rank", "risk_reward_rank"):
        if key in profile:
            payload[key] = profile[key]
    return payload

def strategy_hard_blockers(strategy_name: str, payload: dict[str, Any]) -> list[str]:
    """Hard buy gates distilled from registered strategy rules; blocked names may still be watched."""
    blockers: list[str] = []
    dist = safe_float(payload.get("distance_pct"))
    if dist is not None and dist > COMMON_MAX_BBI_DISTANCE_PCT:
        blockers.append(f"距BBI>{COMMON_MAX_BBI_DISTANCE_PCT}%")

    risk_flags = set(str(x) for x in (payload.get("risk_flags") or []))
    if strategy_name == "shaofu_b1":
        j = safe_float(payload.get("current_j"))
        if j is None or j > B1_CORE_J_CEILING:
            blockers.append("B1核心J未≤-10")
        if not payload.get("vol_shrink") and not payload.get("pullback_shrink"):
            blockers.append("未缩量回调")
        if not payload.get("n_structure"):
            blockers.append("N型上移不足")
        if not payload.get("bull_rope"):
            blockers.append("牛绳/BBI支撑不足")
        stop_space = safe_float(payload.get("stop_space_pct"))
        if stop_space is not None and stop_space > 6:
            blockers.append("止损空间>6%")
        pressure_space = safe_float(payload.get("pressure_space_pct"))
        if pressure_space is not None and pressure_space < 5:
            blockers.append("上方空间不足")
    elif strategy_name == "b2_confirm":
        j = safe_float(payload.get("current_j"))
        if j is None or j >= 55:
            blockers.append("B2要求J<55")
        if not payload.get("vol_expand"):
            blockers.append("B2未放量确认")
        days = safe_float(payload.get("days_from_b1"))
        if days is None or days > 3:
            blockers.append("B2距离B1超过3日")
        if "上影偏长" in risk_flags:
            blockers.append("B2上影过长")
    elif strategy_name == "b3_accelerate":
        b2_distance = safe_float(payload.get("b2_distance"))
        if b2_distance is None or b2_distance > 2:
            blockers.append("B3距离B2过远")
        j = safe_float(payload.get("current_j"))
        if j is None or j >= 70:
            blockers.append("B3 J值过热")
        amplitude = safe_float(payload.get("amplitude_pct"))
        if amplitude is not None and amplitude >= 6:
            blockers.append("B3振幅过大")
        change = safe_float(payload.get("change_pct"))
        if change is not None and change < -0.5:
            blockers.append("B3分歧未转一致")
    elif strategy_name == "super_b1":
        stop_space = safe_float(payload.get("stop_space_pct"))
        if stop_space is not None and stop_space > 6:
            blockers.append("超级B1止损空间>6%")
        wash_days = safe_float(payload.get("wash_days_ago"))
        if wash_days is not None and wash_days > 3:
            blockers.append("洗盘信号不够新")
        if "N型结构不足" in risk_flags or "企稳K线实体偏大" in risk_flags:
            blockers.append("超级B1结构未企稳")
    elif strategy_name == "breakout":
        if not payload.get("pullback_confirmed"):
            blockers.append("突破后未回踩确认")
        if "放量过猛(疑似出货)" in risk_flags:
            blockers.append("突破放量过猛")
    elif strategy_name == "trend_pullback":
        if not payload.get("bbi_upward"):
            blockers.append("趋势未确认")
        if not payload.get("pullback_held"):
            blockers.append("回踩未守住")
    elif strategy_name == "li_daxiao_bottom":
        if not payload.get("bottom_zone"):
            blockers.append("未处低位区")
        if not payload.get("stabilizing"):
            blockers.append("底部未企稳")
        if not payload.get("bluechip_liquidity_proxy"):
            blockers.append("蓝筹流动性代理不足")
        if not payload.get("value_anchor_proxy", True):
            blockers.append("低估蓝筹代理不足")
        if not payload.get("anti_black_five_proxy", True):
            blockers.append("黑五类/题材热度代理偏高")
        if not payload.get("not_fresh_listing_proxy", True):
            blockers.append("次新代理风险")
        if not payload.get("no_chase_zone", True):
            blockers.append("李大霄不追高")
        if payload.get("speculation_heat"):
            blockers.append("换手/涨幅过热")
        if payload.get("breakdown_risk"):
            blockers.append("仍有破位风险")
        vol = safe_float(payload.get("volatility_20d_pct"))
        if vol is not None and vol > 3.8:
            blockers.append("底部波动过高")

    return blockers

def moving_avg(values, n):
    arr = list(values)
    result = []
    for i in range(len(arr)):
        if i < n - 1:
            result.append(None)
        else:
            result.append(statistics.mean(arr[i - n + 1:i + 1]))
    return result

def compute_bbi(rows):
    closes = [r["close"] for r in rows]
    ma3 = moving_avg(closes, 3)
    ma6 = moving_avg(closes, 6)
    ma12 = moving_avg(closes, 12)
    ma24 = moving_avg(closes, 24)
    bbi = []
    for i in range(len(rows)):
        if ma3[i] is None or ma6[i] is None or ma12[i] is None or ma24[i] is None:
            bbi.append(None)
        else:
            bbi.append((ma3[i] + ma6[i] + ma12[i] + ma24[i]) / 4)
    return bbi

def compute_kdj(rows):
    n = 9
    k_list, d_list, j_list = [], [], []
    for i in range(len(rows)):
        if i < n - 1:
            k_list.append(None); d_list.append(None); j_list.append(None)
            continue
        window = rows[i - n + 1:i + 1]
        llv = min(r["low"] for r in window)
        hhv = max(r["high"] for r in window)
        rsv = ((rows[i]["close"] - llv) / (hhv - llv)) * 100 if (hhv - llv) != 0 else 50
        if i == n - 1:
            k = 50; d = 50
        else:
            k = 2 / 3 * k_list[-1] + 1 / 3 * rsv
            d = 2 / 3 * d_list[-1] + 1 / 3 * k
        j = 3 * k - 2 * d
        k_list.append(k); d_list.append(d); j_list.append(j)
    return j_list

def compute_ema(values, n):
    """Exponential moving average."""
    arr = list(values)
    result = []
    k = 2 / (n + 1)
    ema = None
    for v in arr:
        if ema is None:
            ema = v
        else:
            ema = v * k + ema * (1 - k)
        result.append(ema)
    return result


def combine_z_yellow(closes):
    """Z哥黄线/大哥线：(MA14+MA28+MA57+MA114)/4."""
    ma14 = moving_avg(closes, 14)
    ma28 = moving_avg(closes, 28)
    ma57 = moving_avg(closes, 57)
    ma114 = moving_avg(closes, 114)
    out = []
    for vals in zip(ma14, ma28, ma57, ma114):
        out.append(None if any(v is None for v in vals) else sum(vals) / 4)
    return out


def pct_change(row, prev_row):
    prev = prev_row.get("close") if prev_row else None
    return ((row["close"] / prev - 1) * 100) if prev else None


def candle_body_pct(row):
    open_price = row.get("open") or 0
    return abs(row["close"] - row["open"]) / open_price * 100 if open_price else 0


def candle_amplitude_pct(row):
    prev_close = row.get("prev_close") or row.get("open") or 0
    return (row["high"] - row["low"]) / prev_close * 100 if prev_close else 0


def return_pct(current: float | None, base: float | None) -> float | None:
    if current is None or base is None or base == 0:
        return None
    return (current / base - 1) * 100


def li_daxiao_bottom_stage(drawdown_from_high: float | None, distance_from_low: float | None) -> str:
    if drawdown_from_high is None or distance_from_low is None:
        return "底部待确认"
    if drawdown_from_high <= -30 and distance_from_low <= 8:
        return "钻石底亮晶晶"
    if drawdown_from_high <= -22 and distance_from_low <= 12:
        return "婴儿底抱紧紧"
    if drawdown_from_high <= -12 and distance_from_low <= 18:
        return "儿童底在发育"
    return "底部待发育"


def pct_returns(rows, lookback=20) -> list[float]:
    window = rows[-(lookback + 1):]
    out: list[float] = []
    for idx in range(1, len(window)):
        prev_close = window[idx - 1].get("close")
        close = window[idx].get("close")
        ret = return_pct(close, prev_close)
        if ret is not None:
            out.append(ret)
    return out


def volatility_pct(rows, lookback=20) -> float | None:
    vals = pct_returns(rows, lookback)
    if len(vals) < 2:
        return None
    return statistics.stdev(vals)


def is_yang(row):
    return row["close"] >= row["open"]


def is_yin(row):
    return row["close"] < row["open"]


def n_structure_ok(rows, lookback=20):
    if len(rows) < 10:
        return True
    window = rows[-min(len(rows), lookback):]
    mid = len(window) // 2
    first = window[:mid]
    second = window[mid:]
    if not first or not second:
        return True
    return min(r["low"] for r in second) >= min(r["low"] for r in first) * 0.98


def recent_b1_indices(rows, lookback=15, end_offset=1):
    """Find recent Z哥B1 traces before the latest bar."""
    end = len(rows) - end_offset
    start = max(0, end - lookback)
    out = []
    for idx in range(start, end):
        row = rows[idx]
        j = row.get("j")
        if j is None:
            continue
        recent4 = rows[max(0, idx - 3):idx + 1]
        green_count = sum(1 for r in recent4 if is_yin(r))
        if j <= B1_CORE_J_CEILING and green_count < 4:
            out.append(idx)
    return out


# ========== Tencent data fetchers ==========

def tencent_batch_quote(codes):
    url = TENCENT_QUOTE + ",".join(codes)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("gbk", "ignore")
    results = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip().lstrip("v_")
        val = val.strip().strip('"')
        parts = val.split("~")
        if len(parts) < 38:
            continue
        price = safe_float(parts[3])
        prev_close = safe_float(parts[4])
        change_pct = ((price / prev_close - 1) * 100) if price and prev_close else None
        amount_wan = safe_float(parts[37])
        amount = amount_wan * 10000 if amount_wan else 0
        results[key] = {
            "name": parts[1],
            "price": price,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "amount": amount,
            "volume": safe_float(parts[6]),
            "high": safe_float(parts[33]),
            "low": safe_float(parts[34]),
            "turnover": safe_float(parts[38]),
        }
    return results

def tencent_klines(symbol, count=120):
    url = f"{TENCENT_KLINE}?param={symbol},day,,,{count},qfq"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return []
    try:
        kdata = (data.get("data", {}).get(symbol, {}).get("day", []) or
                 data.get("data", {}).get(symbol, {}).get("qfqday", []))
    except Exception:
        return []
    rows = []
    for item in kdata:
        if len(item) >= 6:
            rows.append({
                "date": item[0],
                "open": float(item[1]), "close": float(item[2]),
                "high": float(item[3]), "low": float(item[4]),
                "volume": float(item[5]),
            })
    return rows


# ========== Multi-Strategy Analysis ==========

def enrich_rows(rows):
    """Add BBI, J, EMA20/50, Z哥白线/黄线, change_pct to rows in-place."""
    bbi = compute_bbi(rows)
    j_vals = compute_kdj(rows)
    closes = [r["close"] for r in rows]
    ema20 = compute_ema(closes, 20)
    ema50 = compute_ema(closes, 50)
    z_white = compute_ema(compute_ema(closes, 10), 10)
    z_yellow = combine_z_yellow(closes)
    for i, (r, b, j) in enumerate(zip(rows, bbi, j_vals)):
        r["bbi"] = b
        r["j"] = j
        r["ema20"] = ema20[i]
        r["ema50"] = ema50[i]
        r["z_white"] = z_white[i] if i < len(z_white) else None
        r["z_yellow"] = z_yellow[i] if i < len(z_yellow) else None
    for i in range(1, len(rows)):
        prev_close = rows[i - 1]["close"]
        rows[i]["prev_close"] = prev_close
        rows[i]["change_pct"] = ((rows[i]["close"] / prev_close - 1) * 100) if prev_close else None


def score_trend_pullback(rows) -> dict[str, Any] | None:
    """趋势回踩战法：强趋势股回踩 BBI/EMA20 不破"""
    if len(rows) < 30:
        return None

    recent = rows[-1]; prev = rows[-2]
    close = recent["close"]
    bbi_r = recent.get("bbi"); bbi_p = prev.get("bbi")
    ema20_r = recent.get("ema20"); ema20_p = prev.get("ema20")
    ema50_r = recent.get("ema50")
    ema20_list = [r.get("ema20") for r in rows[-20:] if r.get("ema20") is not None]

    if bbi_r is None or ema20_r is None:
        return None

    dist_bbi = ((close / bbi_r - 1) * 100) if bbi_r else 99
    dist_ema20 = ((close / ema20_r - 1) * 100) if ema20_r else 99

    # BBI上升趋势
    bbi_up_3d = all(
        rows[i].get("bbi") is not None and rows[i].get("bbi") > (rows[i-1].get("bbi") or 0)
        for i in range(-3, 0)
    ) if len(rows) >= 4 else False

    # 近5日是否有回踩 (最低点接近 BBI)
    recent5_low = min(r["low"] for r in rows[-5:])
    pullback_occurred = recent5_low <= bbi_r * 1.03  # 回踩到了离BBI 3%内
    pullback_held = recent5_low >= bbi_r * 0.97     # 回踩不破BBI 3%

    # 调整期缩量(近5日均量 < 前10日均量 * 0.8)
    recent5_vol = statistics.mean(r["volume"] for r in rows[-5:])
    prior10_vol = statistics.mean(r["volume"] for r in rows[-15:-5]) if len(rows) >= 15 else recent5_vol
    vol_shrink = recent5_vol < prior10_vol * 0.85

    # 当日转强
    today_strong = recent.get("change_pct") is not None and recent.get("change_pct", -99) > -1.5

    # 趋势确认
    trend_up = bbi_up_3d and (ema20_r >= ema50_r * 0.98 if ema50_r else True)

    # 位置舒服
    position_ok = 0 <= dist_bbi <= 5 and -1 <= dist_ema20 <= 4

    # 10分制打分
    score = 0
    # 板块/趋势强度 (0-2)
    score += 2 if trend_up else (1 if bbi_up_3d else 0)
    # 回踩质量 (0-3)
    if pullback_occurred and pullback_held and vol_shrink:
        score += 3
    elif pullback_occurred and pullback_held:
        score += 2
    elif pullback_occurred:
        score += 1
    # 转强确认 (0-2)
    score += 2 if today_strong and close >= bbi_r else (1 if close >= bbi_r else 0)
    # 位置舒适度 (0-3)
    if position_ok and dist_bbi <= 3:
        score += 3
    elif position_ok:
        score += 2
    elif close >= bbi_r:
        score += 1

    # 偏离过远降级
    if dist_bbi > 6.5:
        score = max(0, score - 2)
    if dist_bbi > 10:
        score = max(0, score - 3)

    risk_flags = []
    if close < bbi_r:
        risk_flags.append("收盘在BBI下方")
    if dist_bbi > 6.5:
        risk_flags.append("距BBI偏远")
    if not trend_up:
        risk_flags.append("趋势不明确")

    verdict = ("高匹配趋势回踩" if score >= 8 else
               "中等匹配趋势回踩" if score >= 6 else
               "弱匹配趋势回踩" if score >= 4 else "不匹配")

    return with_strategy_profile("trend_pullback", {
        "score": score, "score_total": 10, "verdict": verdict,
        "bbi": safe_round(bbi_r, 2), "distance_pct": safe_round(dist_bbi, 2),
        "ema20_distance": safe_round(dist_ema20, 2),
        "bbi_upward": bbi_up_3d, "above_bbi": close >= bbi_r,
        "pullback_occurred": pullback_occurred, "pullback_held": pullback_held,
        "vol_shrink": vol_shrink, "today_strong": today_strong,
        "risk_flags": risk_flags,
        "recent_close": safe_round(close, 2),
        "change_pct": safe_round(recent.get("change_pct"), 2),
    })


def score_breakout(rows) -> dict[str, Any] | None:
    """突破确认战法：平台/前高突破后回踩不破"""
    if len(rows) < 40:
        return None

    recent = rows[-1]; close = recent["close"]
    bbi_r = recent.get("bbi"); ema20_r = recent.get("ema20")

    if bbi_r is None:
        return None

    # 找过去30日的高点平台 (看前15-30日的价格区间)
    platform_lookback = rows[-30:-5] if len(rows) >= 35 else rows[-25:-3]
    if len(platform_lookback) < 10:
        return None
    platform_high = max(r["high"] for r in platform_lookback)
    platform_low = min(r["low"] for r in platform_lookback)
    platform_range = (platform_high / platform_low - 1) * 100 if platform_low > 0 else 0

    # 平台必须有一定宽度（不是单边趋势）
    has_platform = 3 <= platform_range <= 18

    # 近5日是否突破了平台高点
    recent5_high = max(r["high"] for r in rows[-5:])
    above_platform = close > platform_high * 1.005  # 收盘站稳平台上方0.5%

    # 突破当天的量能（近3日均量 vs 平台期均量）
    recent3_vol = statistics.mean(r["volume"] for r in rows[-3:])
    platform_vol = statistics.mean(r["volume"] for r in platform_lookback)
    vol_expand = recent3_vol >= platform_vol * 1.15  # 量能放大15%以上
    vol_not_explode = recent3_vol <= platform_vol * 3.5  # 不放量过猛

    # 如果无清晰平台，尝试看前高突破 (40日新高)
    high40 = max(r["high"] for r in rows[-40:])
    is_new_high = close > high40 * 0.98 and close >= high40 * 0.99  # 接近或刷新40日高点

    # 回踩确认：突破后没有立刻跌回平台
    recent5_low = min(r["low"] for r in rows[-5:])
    pullback_confirmed = recent5_low >= platform_high * 0.97 if above_platform else True

    # BBI向上
    bbi_up = all(
        rows[i].get("bbi") is not None and (rows[i].get("bbi") or 0) > (rows[i-1].get("bbi") or 0)
        for i in range(-3, 0)
    ) if len(rows) >= 4 else False

    # 位置不能太远
    dist_bbi = ((close / bbi_r - 1) * 100) if bbi_r else 99

    # 10分制打分
    score = 0
    # 蓄势质量 (0-2)
    score += 2 if has_platform else (1 if platform_range > 0 else 0)
    # 突破有效性 (0-3)
    if above_platform and vol_expand and vol_not_explode and pullback_confirmed:
        score += 3
    elif above_platform and vol_expand:
        score += 2
    elif above_platform or (is_new_high and vol_expand):
        score += 1
    # 趋势支撑 (0-2)
    score += 2 if bbi_up and close >= bbi_r else (1 if close >= bbi_r else 0)
    # 位置/盈亏比 (0-3)
    if dist_bbi <= 4 and above_platform:
        score += 3
    elif dist_bbi <= 6:
        score += 2
    elif dist_bbi <= 8:
        score += 1

    # 距离过远降级
    if dist_bbi > 8:
        score = max(0, score - 2)
    if dist_bbi > 12:
        score = max(0, score - 3)

    risk_flags = []
    if not pullback_confirmed and above_platform:
        risk_flags.append("回踩确认不充分")
    if vol_expand and not vol_not_explode:
        risk_flags.append("放量过猛(疑似出货)")
    if close < bbi_r:
        risk_flags.append("收盘在BBI下方")
    if dist_bbi > 8:
        risk_flags.append("距BBI偏远")

    verdict = ("高匹配突破确认" if score >= 8 else
               "中等匹配突破确认" if score >= 6 else
               "弱匹配突破确认" if score >= 4 else "不匹配")

    return with_strategy_profile("breakout", {
        "score": score, "score_total": 10, "verdict": verdict,
        "bbi": safe_round(bbi_r, 2), "distance_pct": safe_round(dist_bbi, 2),
        "bbi_upward": bbi_up, "above_bbi": close >= bbi_r,
        "platform_detected": has_platform, "above_platform": above_platform,
        "vol_expand": vol_expand, "pullback_confirmed": pullback_confirmed,
        "risk_flags": risk_flags,
        "recent_close": safe_round(close, 2),
        "change_pct": safe_round(recent.get("change_pct"), 2),
    })


def score_shaofu_b1(rows) -> dict[str, Any] | None:
    """Z哥少妇B1：J≤12(最好负值) + N型上移 + 缩量回调 + 牛绳/BBI约束。"""
    if len(rows) < 30:
        return None
    recent = rows[-1]
    prev = rows[-2]
    close = recent["close"]
    bbi_r = recent.get("bbi")
    j = recent.get("j")
    if bbi_r is None or j is None or j > B1_WATCH_J_CEILING:
        return None

    recent4 = rows[-4:]
    green_count = sum(1 for r in recent4 if is_yin(r))
    if green_count >= 4:
        return None

    dist_bbi = (close / bbi_r - 1) * 100 if bbi_r else 99
    vol_shrink = recent["volume"] < prev["volume"] * 0.85 if prev.get("volume") else False
    recent5_vol = statistics.mean(r["volume"] for r in rows[-5:])
    prior10_vol = statistics.mean(r["volume"] for r in rows[-15:-5]) if len(rows) >= 15 else recent5_vol
    pullback_shrink = recent5_vol < prior10_vol * 0.9 if prior10_vol else vol_shrink
    n_ok = n_structure_ok(rows, 20)
    white = recent.get("z_white")
    yellow = recent.get("z_yellow")
    bull_rope = (white is not None and yellow is not None and white >= yellow * 0.98) or close >= bbi_r
    stop_space = max(0, (close / recent["low"] - 1) * 100) if recent.get("low") else 99
    yellow_dist = (close / yellow - 1) * 100 if yellow else dist_bbi
    high20 = max(r["high"] for r in rows[-20:])
    pressure_space = (high20 / close - 1) * 100 if close else 0

    score = 0
    if j <= -10:
        score += 2.5
    elif j <= 0:
        score += 2
    else:
        score += 1
    if vol_shrink and pullback_shrink:
        score += 2
    elif vol_shrink or pullback_shrink:
        score += 1.2
    if n_ok:
        score += 1.5
    if bull_rope:
        score += 1
    if stop_space <= 4.5:
        score += 1.5
    elif stop_space <= 6:
        score += 1
    if -3 <= dist_bbi <= 5 and abs(yellow_dist) <= 8:
        score += 1
    if pressure_space >= 5:
        score += 1
    if j > B1_CORE_J_CEILING:
        score = min(score, 7.5)
    if dist_bbi > 6.5:
        score = min(score, 7.5)
    if stop_space > 8:
        score = min(score, 6.5)
    score = min(10, score)

    risk_flags = []
    if not vol_shrink and not pullback_shrink:
        risk_flags.append("未明显缩量")
    if j > B1_CORE_J_CEILING:
        risk_flags.append("J值未到核心B1")
    if not n_ok:
        risk_flags.append("N型上移不足")
    if not bull_rope:
        risk_flags.append("白线/BBI支撑不足")
    if dist_bbi > 6.5:
        risk_flags.append("距BBI偏远")
    if stop_space > 8:
        risk_flags.append("止损空间偏大")
    if pressure_space < 5:
        risk_flags.append("上方空间不足")

    verdict = ("高匹配少妇B1" if score >= 8 else
               "中等匹配少妇B1" if score >= 6 else
               "弱匹配少妇B1" if score >= 4 else "不匹配")
    return with_strategy_profile("shaofu_b1", {
        "score": score, "score_total": 10, "verdict": verdict,
        "bbi": safe_round(bbi_r, 2), "distance_pct": safe_round(dist_bbi, 2),
        "above_bbi": close >= bbi_r, "bbi_upward": bool(len(rows) >= 2 and rows[-2].get("bbi") and bbi_r >= rows[-2]["bbi"]),
        "current_j": safe_round(j, 2), "min_j_10d": safe_round(min(r.get("j") for r in rows[-10:] if r.get("j") is not None), 2),
        "j_recovering": len(rows) >= 2 and rows[-2].get("j") is not None and j > rows[-2]["j"],
        "j_oversold": j <= B1_CORE_J_CEILING,
        "vol_shrink": vol_shrink, "pullback_shrink": pullback_shrink,
        "n_structure": n_ok, "bull_rope": bull_rope,
        "z_white": safe_round(white, 2), "z_yellow": safe_round(yellow, 2),
        "stop_space_pct": safe_round(stop_space, 2),
        "yellow_distance_pct": safe_round(yellow_dist, 2),
        "pressure_space_pct": safe_round(pressure_space, 2),
        "risk_flags": risk_flags,
        "recent_close": safe_round(close, 2),
        "change_pct": safe_round(recent.get("change_pct"), 2),
    })


def score_b2_confirm(rows) -> dict[str, Any] | None:
    """Z哥B2确认：B1后3-5日内放量中/大阳，J未过热，趋势确认。"""
    if len(rows) < 35:
        return None
    recent = rows[-1]
    prev = rows[-2]
    b1_idxs = recent_b1_indices(rows, lookback=5, end_offset=1)
    if not b1_idxs:
        return None
    days_from_b1 = len(rows) - 1 - b1_idxs[-1]
    if days_from_b1 < 1 or days_from_b1 > 5:
        return None

    change_pct = recent.get("change_pct") or pct_change(recent, prev) or 0
    j = recent.get("j") or 99
    bbi_r = recent.get("bbi")
    close = recent["close"]
    if bbi_r is None:
        return None
    dist_bbi = (close / bbi_r - 1) * 100 if bbi_r else 99

    long_yang = is_yang(recent) and change_pct >= 4
    vol_ratio = recent["volume"] / prev["volume"] if prev.get("volume") else 0
    vol_expand = vol_ratio >= 1.2
    above_bbi = close >= bbi_r
    upper_shadow = recent["high"] - max(recent["close"], recent["open"])
    body = abs(recent["close"] - recent["open"])
    upper_ok = body <= 0 or upper_shadow <= body * 1.2

    if not long_yang:
        return None

    score = 4
    if 1 <= days_from_b1 <= 3:
        score += 1.5
    elif days_from_b1 <= 5:
        score += 0.5
    if vol_expand:
        score += 1.5
        if vol_ratio <= 3:
            score += 0.5
    if j < 55:
        score += 1.5
    elif j < 70:
        score += 0.5
    if above_bbi:
        score += 1
    if upper_ok:
        score += 1
    if dist_bbi <= 6.5:
        score += 1
    if j >= 70:
        score = min(score, 7.0)
    if dist_bbi > 8:
        score = min(score, 7.5)
    if change_pct >= 9 and dist_bbi > 6.5:
        score = min(score, 7.0)
    score = min(10, score)

    risk_flags = []
    if not vol_expand:
        risk_flags.append("量能确认不足")
    if j >= 55:
        risk_flags.append("J值偏热")
    if not upper_ok:
        risk_flags.append("上影偏长")
    if not above_bbi:
        risk_flags.append("未站上BBI")
    if days_from_b1 > 3:
        risk_flags.append("B2确认偏滞后")
    if dist_bbi > 6.5:
        risk_flags.append("距BBI偏远")

    verdict = ("高匹配B2确认" if score >= 8 else
               "中等匹配B2确认" if score >= 6 else
               "弱匹配B2确认" if score >= 4 else "不匹配")
    return with_strategy_profile("b2_confirm", {
        "score": score, "score_total": 10, "verdict": verdict,
        "bbi": safe_round(bbi_r, 2), "distance_pct": safe_round(dist_bbi, 2),
        "above_bbi": above_bbi, "bbi_upward": bool(len(rows) >= 2 and rows[-2].get("bbi") and bbi_r >= rows[-2]["bbi"]),
        "current_j": safe_round(j, 2), "j_recovering": True, "j_oversold": False,
        "days_from_b1": days_from_b1, "vol_expand": vol_expand, "vol_ratio": safe_round(vol_ratio, 2),
        "risk_flags": risk_flags,
        "recent_close": safe_round(close, 2),
        "change_pct": safe_round(change_pct, 2),
    })


def score_b3_accelerate(rows) -> dict[str, Any] | None:
    """Z哥B3：B2后小阳/十字星，振幅小，分歧转一致。"""
    if len(rows) < 40:
        return None
    recent = rows[-1]
    prev = rows[-2]
    bbi_r = recent.get("bbi")
    if bbi_r is None:
        return None

    has_b2 = False
    b2_distance = None
    for offset in range(2, min(6, len(rows))):
        row = rows[-offset]
        prev_row = rows[-offset - 1] if offset + 1 <= len(rows) else None
        row_pct = row.get("change_pct") or pct_change(row, prev_row) or 0
        vol_ok = prev_row is not None and row["volume"] >= prev_row["volume"] * 1.2
        if row_pct >= 4 and is_yang(row) and vol_ok:
            has_b2 = True
            b2_distance = offset - 1
            break
    if not has_b2:
        return None
    if b2_distance is None or b2_distance > 3:
        return None

    change_pct = recent.get("change_pct") or pct_change(recent, prev) or 0
    amplitude = candle_amplitude_pct(recent)
    small_consensus = -1.5 <= change_pct < 2 and amplitude < 6 and recent["close"] >= recent["open"] * 0.985
    if not small_consensus:
        return None

    close = recent["close"]
    j = recent.get("j") or 99
    dist_bbi = (close / bbi_r - 1) * 100 if bbi_r else 99
    score = 6
    if close >= bbi_r and dist_bbi <= 5:
        score += 1
    if j < 70:
        score += 0.8
    if amplitude <= 4.5:
        score += 1
    elif amplitude < 6:
        score += 0.5
    if b2_distance <= 1:
        score += 1.2
    elif b2_distance <= 2:
        score += 1
    else:
        score += 0.4
    volume_not_explode = recent["volume"] <= prev["volume"] * 1.2 if prev.get("volume") else True
    if volume_not_explode:
        score += 1
    if -0.5 <= change_pct <= 1.5:
        score += 0.5
    if j >= 90:
        score = min(score, 8.0)
    elif j >= 70:
        score = min(score, 8.5)
    if dist_bbi > 6.5:
        score = min(score, 7.5)
    score = min(10, score)

    risk_flags = []
    if close < bbi_r:
        risk_flags.append("未站上BBI")
    if j >= 70:
        risk_flags.append("J值过热")
    if amplitude >= 6:
        risk_flags.append("振幅偏大")
    if dist_bbi > 6.5:
        risk_flags.append("距BBI偏远")

    verdict = ("高匹配B3中继" if score >= 8 else
               "中等匹配B3中继" if score >= 6 else
               "弱匹配B3中继" if score >= 4 else "不匹配")
    return with_strategy_profile("b3_accelerate", {
        "score": score, "score_total": 10, "verdict": verdict,
        "bbi": safe_round(bbi_r, 2), "distance_pct": safe_round(dist_bbi, 2),
        "above_bbi": close >= bbi_r, "bbi_upward": bool(len(rows) >= 2 and rows[-2].get("bbi") and bbi_r >= rows[-2]["bbi"]),
        "current_j": safe_round(j, 2), "j_recovering": True, "j_oversold": False,
        "b2_distance": b2_distance, "amplitude_pct": safe_round(amplitude, 2),
        "volume_not_explode": volume_not_explode,
        "risk_flags": risk_flags,
        "recent_close": safe_round(close, 2),
        "change_pct": safe_round(change_pct, 2),
    })


def score_super_b1(rows) -> dict[str, Any] | None:
    """Z哥超级B1：放量破位洗盘后缩量企稳，J值仍在负值/低位。"""
    if len(rows) < 35:
        return None
    recent = rows[-1]
    prev = rows[-2]
    bbi_r = recent.get("bbi")
    j = recent.get("j")
    if bbi_r is None or j is None or j > -5:
        return None

    wash_idx = None
    for idx in range(max(1, len(rows) - 6), len(rows) - 1):
        row = rows[idx]
        prior = rows[idx - 1]
        row_pct = row.get("change_pct") or pct_change(row, prior) or 0
        if is_yin(row) and row["volume"] >= prior["volume"] * 1.5 and row_pct <= -2:
            wash_idx = idx
            break
    if wash_idx is None:
        return None

    close = recent["close"]
    wash_low = rows[wash_idx]["low"]
    wash_days_ago = len(rows) - 1 - wash_idx
    shrink = recent["volume"] < prev["volume"] * 0.85 if prev.get("volume") else False
    stable = close >= wash_low * 0.98 and recent["low"] >= wash_low * 0.97
    small_body = candle_body_pct(recent) <= 2.5
    n_ok = n_structure_ok(rows, 20)
    dist_bbi = (close / bbi_r - 1) * 100 if bbi_r else 99
    stop_space = (close / wash_low - 1) * 100 if wash_low else 99
    if not (shrink and stable):
        return None

    score = 2.5
    if j <= -10:
        score += 2
    else:
        score += 1
    if shrink:
        score += 1
    if stable:
        score += 1
    if small_body:
        score += 1
    if close >= bbi_r * 0.97 and dist_bbi <= 5:
        score += 1
    if n_ok:
        score += 1
    if wash_days_ago <= 3:
        score += 1
    if stop_space <= 6:
        score += 1
    if close < bbi_r * 0.97:
        score = min(score, 7.0)
    if stop_space > 8:
        score = min(score, 7.0)
    if wash_days_ago > 3:
        score = min(score, 7.5)
    score = min(9, score)

    risk_flags = []
    if close < bbi_r * 0.97:
        risk_flags.append("仍低于BBI")
    if not n_ok:
        risk_flags.append("N型结构不足")
    if not small_body:
        risk_flags.append("企稳K线实体偏大")
    if stop_space > 8:
        risk_flags.append("洗盘低点止损空间偏大")
    if wash_days_ago > 3:
        risk_flags.append("洗盘信号不够新")

    verdict = ("高匹配超级B1" if score >= 8 else
               "中等匹配超级B1" if score >= 6 else
               "弱匹配超级B1" if score >= 4 else "不匹配")
    return with_strategy_profile("super_b1", {
        "score": score, "score_total": 10, "verdict": verdict,
        "bbi": safe_round(bbi_r, 2), "distance_pct": safe_round(dist_bbi, 2),
        "above_bbi": close >= bbi_r, "bbi_upward": bool(len(rows) >= 2 and rows[-2].get("bbi") and bbi_r >= rows[-2]["bbi"]),
        "current_j": safe_round(j, 2), "min_j_10d": safe_round(min(r.get("j") for r in rows[-10:] if r.get("j") is not None), 2),
        "j_recovering": len(rows) >= 2 and rows[-2].get("j") is not None and j > rows[-2]["j"],
        "j_oversold": True,
        "wash_days_ago": wash_days_ago,
        "stop_space_pct": safe_round(stop_space, 2),
        "risk_flags": risk_flags,
        "recent_close": safe_round(close, 2),
        "change_pct": safe_round(recent.get("change_pct"), 2),
    })


def score_li_daxiao_bottom(rows) -> dict[str, Any] | None:
    """李大霄风格代理：低估蓝筹、底部发育、远离黑五类和杠杆热度。"""
    if len(rows) < 80:
        return None

    recent = rows[-1]
    close = recent["close"]
    bbi_r = recent.get("bbi")
    ema20_r = recent.get("ema20")
    ema50_r = recent.get("ema50")
    if bbi_r is None or ema20_r is None or ema50_r is None:
        return None

    window120 = rows[-min(120, len(rows)):]
    high120 = max(r["high"] for r in window120)
    low120 = min(r["low"] for r in window120)
    low20 = min(r["low"] for r in rows[-20:])
    drawdown_from_high = return_pct(close, high120) or 0
    distance_from_low = return_pct(close, low120) or 0
    dist_bbi = (close / bbi_r - 1) * 100 if bbi_r else 99
    recent_change = safe_float(recent.get("change_pct"))
    amount = safe_float(recent.get("quote_amount") if "quote_amount" in recent else recent.get("amount"))
    turnover = safe_float(recent.get("quote_turnover") if "quote_turnover" in recent else recent.get("turnover"))
    symbol_code = str(recent.get("symbol_code") or recent.get("code") or "")
    stock_name = str(recent.get("stock_name") or recent.get("name") or "")
    vol20 = volatility_pct(rows, 20)
    recent5_vol = statistics.mean(r["volume"] for r in rows[-5:])
    prior20_vol = statistics.mean(r["volume"] for r in rows[-25:-5]) if len(rows) >= 25 else recent5_vol
    avg60_vol = statistics.mean(r["volume"] for r in rows[-60:])
    volume_shrink = prior20_vol > 0 and recent5_vol <= prior20_vol * 0.9
    quote_liquidity_ok = amount is None or amount >= LI_DAXIAO_MIN_AMOUNT
    volume_liquidity_ok = avg60_vol > 0 and recent5_vol >= avg60_vol * 0.35
    bluechip_liquidity_proxy = volume_liquidity_ok and quote_liquidity_ok
    turnover_calm = turnover is None or turnover <= LI_DAXIAO_MAX_TURNOVER
    turnover_hot = turnover is not None and turnover >= LI_DAXIAO_HOT_TURNOVER
    core_board_proxy = not symbol_code or symbol_code.startswith(("600", "601", "603", "605", "000", "001", "002"))
    not_fresh_listing_proxy = len(rows) >= 110 and not stock_name.startswith(("N", "C"))
    daily_chase = recent_change is not None and recent_change > LI_DAXIAO_MAX_DAILY_CHASE_PCT
    speculation_heat = (
        turnover_hot
        or (turnover is not None and turnover > LI_DAXIAO_MAX_TURNOVER and (recent_change or 0) > 2)
        or (daily_chase and dist_bbi > 2.5)
    )
    value_anchor_proxy = bluechip_liquidity_proxy and turnover_calm and core_board_proxy
    anti_black_five_proxy = (
        not_fresh_listing_proxy
        and quote_liquidity_ok
        and not speculation_heat
        and (core_board_proxy or turnover_calm)
    )
    no_chase_zone = dist_bbi <= LI_DAXIAO_MAX_BBI_DISTANCE and not daily_chase
    bbi_flattening = len(rows) >= 5 and bbi_r >= min((r.get("bbi") or bbi_r) for r in rows[-5:]) * 0.995
    stabilizing = close >= bbi_r * 0.98 and close >= ema20_r * 0.97 and recent["low"] >= low20 * 0.985
    bottom_zone = -45 <= drawdown_from_high <= -12 and distance_from_low <= 18
    breakdown_risk = close < low20 * 1.02 and (recent.get("change_pct") or 0) < -1.5
    low_volatility = vol20 is not None and vol20 <= 3.8
    bottom_stage = li_daxiao_bottom_stage(drawdown_from_high, distance_from_low)

    score = 0.0
    if bottom_zone:
        score += 2.2
    elif -55 <= drawdown_from_high <= -8 and distance_from_low <= 25:
        score += 1.3
    if stabilizing:
        score += 1.6
    elif close >= bbi_r * 0.96:
        score += 0.8
    if volume_shrink:
        score += 1.0
    if low_volatility:
        score += 1.1 if vol20 is not None and vol20 <= 2.8 else 0.7
    if bbi_flattening:
        score += 0.8
    if value_anchor_proxy:
        score += 1.4
    elif bluechip_liquidity_proxy:
        score += 0.6
    if anti_black_five_proxy:
        score += 1.0
    if no_chase_zone:
        score += 0.8
    if not breakdown_risk:
        score += 0.6
    if close >= ema50_r * 0.94:
        score += 0.5
    if distance_from_low > 25:
        score = min(score, 7.2)
    if breakdown_risk:
        score = min(score, 6.8)
    if vol20 is not None and vol20 > 4.5:
        score = min(score, 6.5)
    if not value_anchor_proxy:
        score = min(score, 7.6)
    if not anti_black_five_proxy:
        score = min(score, 7.4)
    if not no_chase_zone:
        score = min(score, 7.0)
    if speculation_heat:
        score = min(score, 6.8)
    if not not_fresh_listing_proxy:
        score = min(score, 6.8)
    score = min(10, score)

    risk_flags = []
    if not bottom_zone:
        risk_flags.append("低位区不充分")
    if not stabilizing:
        risk_flags.append("企稳不足")
    if not volume_shrink:
        risk_flags.append("未缩量")
    if breakdown_risk:
        risk_flags.append("仍贴近破位低点")
    if vol20 is not None and vol20 > 3.8:
        risk_flags.append("底部波动偏高")
    if not value_anchor_proxy:
        risk_flags.append("低估蓝筹代理不足")
    if not anti_black_five_proxy:
        risk_flags.append("黑五类/题材热度代理偏高")
    if not no_chase_zone:
        risk_flags.append("不符合正金字塔低吸")
    if turnover_hot:
        risk_flags.append("换手偏热")
    if daily_chase:
        risk_flags.append("单日涨幅偏高")
    if not not_fresh_listing_proxy:
        risk_flags.append("次新代理风险")

    verdict = ("高匹配李大霄" if score >= 8 else
               "中等匹配李大霄" if score >= 6 else
               "弱匹配李大霄" if score >= 4 else "不匹配")
    return with_strategy_profile("li_daxiao_bottom", {
        "score": score, "score_total": 10, "verdict": verdict,
        "bbi": safe_round(bbi_r, 2), "distance_pct": safe_round(dist_bbi, 2),
        "above_bbi": close >= bbi_r,
        "bbi_upward": bbi_flattening,
        "bottom_zone": bottom_zone,
        "bottom_stage": bottom_stage,
        "stabilizing": stabilizing,
        "volume_shrink": volume_shrink,
        "bluechip_liquidity_proxy": bluechip_liquidity_proxy,
        "value_anchor_proxy": value_anchor_proxy,
        "anti_black_five_proxy": anti_black_five_proxy,
        "not_fresh_listing_proxy": not_fresh_listing_proxy,
        "no_chase_zone": no_chase_zone,
        "speculation_heat": speculation_heat,
        "core_board_proxy": core_board_proxy,
        "turnover_calm": turnover_calm,
        "quote_amount_yi": safe_round(amount / 1e8, 2) if amount is not None else None,
        "quote_turnover_pct": safe_round(turnover, 2),
        "breakdown_risk": breakdown_risk,
        "drawdown_from_high_pct": safe_round(drawdown_from_high, 2),
        "distance_from_low_pct": safe_round(distance_from_low, 2),
        "volatility_20d_pct": safe_round(vol20, 2),
        "ema20": safe_round(ema20_r, 2),
        "ema50": safe_round(ema50_r, 2),
        "risk_flags": risk_flags,
        "recent_close": safe_round(close, 2),
        "change_pct": safe_round(recent.get("change_pct"), 2),
    })


STRATEGY_SCORERS: dict[str, Callable[[list[dict[str, Any]]], dict[str, Any] | None]] = {
    strategy_id: globals()[str(definition["scorer"])]
    for strategy_id, definition in STRATEGY_DEFINITIONS.items()
    if str(definition.get("scorer") or "") in globals()
}


def analyze_all_strategies(symbol, tencent_key, quote: dict[str, Any] | None = None, name: str = ""):
    """Fetch K-lines once, enrich once, run all registered strategies. Returns dict or None."""
    try:
        rows = tencent_klines(tencent_key, 120)
    except Exception:
        return None
    if len(rows) < 30:
        return None

    # Enrich once (BBI, J, EMA20, EMA50, change_pct)
    enrich_rows(rows)
    if rows:
        rows[-1]["symbol_code"] = symbol
        rows[-1]["stock_name"] = name or (quote or {}).get("name", "")
        if quote:
            rows[-1]["quote_amount"] = quote.get("amount")
            rows[-1]["quote_turnover"] = quote.get("turnover")
            rows[-1]["quote_price"] = quote.get("price")

    strategies = {}
    for strategy_id, scorer in active_strategy_scorers().items():
        # Shallow-copy rows so each scorer can add its own annotations if needed.
        scored = scorer([dict(r) for r in rows])
        if scored:
            strategies[strategy_id] = scored

    if not strategies:
        return None

    # 找出最优战法：先看是否达到各自入场基准，再看分数，最后用策略确定性/优先级打破平局。
    def best_strategy_key(name):
        item = strategies[name]
        score = float(item.get("score") or 0)
        threshold = float(item.get("entry_threshold") or 8)
        priority = int(item.get("strategy_priority") or 0)
        return (1 if score >= threshold else 0, score, priority)

    best_name = max(strategies, key=best_strategy_key)
    best_score = strategies[best_name]["score"]
    best_verdict = strategies[best_name]["verdict"]
    best_decision_score = strategies[best_name].get("decision_score", best_score)

    # 策略共识评分：多战法同时≥7分 → 提高置信度
    consensus_count = sum(1 for s in strategies.values() if s["score"] >= 7)
    consensus_boost = 1 if consensus_count >= 3 else (0.5 if consensus_count >= 2 else 0)

    return {
        "best_strategy": best_name,
        "best_score": best_score,
        "best_decision_score": best_decision_score,
        "best_verdict": best_verdict,
        "strategies": strategies,
        "consensus_count": consensus_count,
        "consensus_boost": consensus_boost,
    }


def load_main_board_code_pool():
    """Load沪深主板股票池，避开 ak.stock_info_a_code_name 的北交所依赖。"""
    import akshare as ak
    candidates = []

    def add(code, name):
        code = str(code or "").strip().split(".")[0].zfill(6)
        name = str(name or "").strip()
        if not code or not name:
            return
        if not code.startswith(("600", "601", "603", "605", "000", "001", "002")):
            return
        if name.startswith(("ST", "*ST", "退")):
            return
        candidates.append((code, name))

    errors = []
    try:
        sh = ak.stock_info_sh_name_code(symbol="主板A股")
        for _, row in sh.iterrows():
            add(row.get("证券代码"), row.get("证券简称"))
    except Exception as exc:
        errors.append(f"SH:{type(exc).__name__}")

    try:
        sz = ak.stock_info_sz_name_code(symbol="A股列表")
        for _, row in sz.iterrows():
            add(row.get("A股代码"), row.get("A股简称"))
    except Exception as exc:
        errors.append(f"SZ:{type(exc).__name__}")

    if not candidates:
        df = ak.stock_info_a_code_name()
        for _, row in df.iterrows():
            add(row.get("code"), row.get("name"))

    deduped = {}
    for code, name in candidates:
        deduped[code] = name
    if errors:
        print("  Code pool partial fallback: " + ", ".join(errors), file=sys.stderr)
    return sorted(deduped.items())


def get_margin_signal(code: str) -> dict | None:
    """获取个股融资融券信号。返回 {net_buy_ratio, signal, detail} 或 None。"""
    try:
        import akshare as ak
        from datetime import datetime as dt_mod, timedelta
        
        # 找最近一个可用交易日（融资数据非交易日为空）
        today = dt_mod.now()
        for offset in range(5):
            check_date = (today - timedelta(days=offset)).strftime("%Y%m%d")
            try:
                if code.startswith(('6','9')):
                    df = ak.stock_margin_detail_sse(date=check_date)
                elif code.startswith(('0','2','3')):
                    df = ak.stock_margin_detail_szse(date=check_date)
                else:
                    return None
                if df is not None and not df.empty:
                    break
            except Exception:
                continue
        else:
            return None
        
        # 查找该股票（沪市深市列名不同）
        if code.startswith(('6','9')):
            row = df[df['标的证券代码'].astype(str).str.zfill(6) == code]
            if row.empty: return None
            r = row.iloc[0]
            buy_amt = float(r.get('融资买入额', 0) or 0)
            repay_amt = float(r.get('融资偿还额', 0) or 0)
            balance = float(r.get('融资余额', 0) or 0)
        else:
            row = df[df['证券代码'].astype(str).str.zfill(6) == code]
            if row.empty: return None
            r = row.iloc[0]
            buy_amt = float(r.get('融资买入额', 0) or 0)
            repay_amt = 0  # 深市无此字段
            balance = float(r.get('融资余额', 0) or 0)
        
        if buy_amt + repay_amt == 0 and repay_amt == 0:
            # 深市无偿还数据，仅用融资余额判断
            if balance > 1e8:
                return {"signal": "neutral", "detail": f"融资余额{balance/1e8:.1f}亿(买入{buy_amt/1e4:.0f}万)", "net_flow_wan": round(buy_amt/1e4,1)}
            return None
        elif buy_amt + repay_amt == 0:
            return None
        
        net_flow = buy_amt - repay_amt
        ratio = net_flow / balance if balance > 0 else 0
        
        if ratio > 0.03:
            signal, detail = "bullish", f"融资净买入{net_flow/1e4:.0f}万(余额{balance/1e8:.1f}亿)"
        elif ratio > 0:
            signal, detail = "slightly_bullish", f"融资小幅净买入{net_flow/1e4:.0f}万"
        elif ratio > -0.03:
            signal, detail = "slightly_bearish", f"融资小幅净偿还{abs(net_flow)/1e4:.0f}万"
        else:
            signal, detail = "bearish", f"融资净偿还{abs(net_flow)/1e4:.0f}万(余额{balance/1e8:.1f}亿)"
        
        return {"signal": signal, "detail": detail, "net_flow_wan": round(net_flow/1e4, 1)}
    except Exception:
        return None


def get_block_trade_signal(code: str, name: str = "") -> dict | None:
    """获取个股近期大宗交易信号。溢价买入=看多，折价卖出=看空。"""
    try:
        import akshare as ak
        from datetime import datetime as dt_mod, timedelta
        end = dt_mod.now().strftime("%Y%m%d")
        start = (dt_mod.now() - timedelta(days=5)).strftime("%Y%m%d")
        
        df = ak.stock_dzjy_mrmx(symbol='A股', start_date=start, end_date=end)
        if df is None or df.empty:
            return None
        
        # 匹配该股票
        matches = df[df['证券代码'].astype(str).str.zfill(6) == code]
        if matches.empty:
            return None
        
        total_amt = matches['成交额'].sum()
        avg_premium = matches['折溢率'].mean()
        count = len(matches)
        
        if avg_premium is None or not isinstance(avg_premium, (int, float)):
            return None
        
        if avg_premium > 2:
            signal, detail = "bullish", f"大宗溢价{avg_premium:+.1f}%({count}笔{total_amt/1e4:.0f}万)"
        elif avg_premium > 0.5:
            signal, detail = "slightly_bullish", f"大宗小幅溢价{avg_premium:+.1f}%({count}笔)"
        elif avg_premium < -2:
            signal, detail = "bearish", f"大宗折价{avg_premium:+.1f}%({count}笔{total_amt/1e4:.0f}万)"
        elif avg_premium < -0.5:
            signal, detail = "slightly_bearish", f"大宗小幅折价{avg_premium:+.1f}%({count}笔)"
        else:
            signal, detail = "neutral", f"大宗平价({count}笔{total_amt/1e4:.0f}万)"
        
        return {"signal": signal, "detail": detail, "count": count, "avg_premium": round(float(avg_premium), 1)}
    except Exception:
        return None


def candidate_is_trade_ready(item: dict[str, Any]) -> bool:
    raw_score = item.get("best_score")
    if raw_score is None:
        raw_score = item.get("score")
    score = safe_float(raw_score) or 0
    threshold = safe_float(item.get("entry_threshold")) or 8
    blockers = item.get("hard_blockers") or []
    dist = safe_float(item.get("distance_pct"))
    return (
        bool(item.get("actionable", score >= threshold))
        and score >= threshold
        and not blockers
        and (dist is None or dist <= COMMON_MAX_BBI_DISTANCE_PCT)
    )


def select_trade_candidates(results: list[dict[str, Any]], limit: int = TRADE_CANDIDATE_LIMIT) -> list[dict[str, Any]]:
    """Candidates allowed to reach the trading decision model."""
    selected = []
    seen = set()
    for item in results:
        if len(selected) >= limit:
            break
        code = str(item.get("code") or "")
        if not code or code in seen or not candidate_is_trade_ready(item):
            continue
        selected.append(item)
        seen.add(code)
    return selected


def select_display_candidates(results: list[dict[str, Any]], limit: int = DISPLAY_CANDIDATE_LIMIT) -> list[dict[str, Any]]:
    """Keep top-ranked names while reserving slots for each strategy family."""
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any]) -> None:
        if len(selected) >= limit:
            return
        code = str(item.get("code") or "")
        if not code or code in seen:
            return
        selected.append(item)
        seen.add(code)

    trade_ready = [item for item in results if candidate_is_trade_ready(item)]
    for item in trade_ready[:DISPLAY_HEAD_LIMIT]:
        add(item)

    for strat in DISPLAY_STRATEGY_ORDER:
        for item in trade_ready:
            if item.get("best_strategy") == strat:
                add(item)
                break

    for item in trade_ready:
        add(item)

    for item in results:
        add(item)

    return selected


def normalize_industry_name(name: Any) -> str:
    text = str(name or "").strip()
    if not text or text.lower() in {"nan", "none", "null"} or text in {"-", "--"}:
        return ""
    text = re.sub(r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+$", "", text).strip()
    for suffix in ("行业", "板块", "概念"):
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            text = text[: -len(suffix)].strip()
    return text


def normalize_stock_code(code: Any) -> str:
    raw = str(code or "").strip()
    if not raw:
        return ""
    match = re.search(r"(\d{6})", raw)
    if match:
        return match.group(1)
    digits = re.sub(r"\D", "", raw)
    return digits.zfill(6) if digits else ""


def _record_value(row: Any, key: str) -> Any:
    if hasattr(row, "get"):
        return row.get(key)
    try:
        return row[key]
    except Exception:
        return None


def _iter_record_rows(data: Any):
    if data is None:
        return
    iterrows = getattr(data, "iterrows", None)
    if callable(iterrows):
        for _, row in iterrows():
            yield row
        return
    if isinstance(data, dict):
        yield data
        return
    try:
        for row in data:
            yield row
    except TypeError:
        return


def extract_industry_from_individual_info(info: Any) -> str:
    """Read the industry/sector name from akshare.stock_individual_info_em output."""
    direct_keys = ("行业", "所属行业", "板块", "所属板块")
    item_keys = ("item", "项目", "指标")
    value_keys = ("value", "值", "内容")

    for row in _iter_record_rows(info):
        for key in direct_keys:
            industry = normalize_industry_name(_record_value(row, key))
            if industry:
                return industry

        item_name = ""
        for key in item_keys:
            item_name = str(_record_value(row, key) or "").strip()
            if item_name:
                break
        if item_name not in direct_keys:
            continue

        for key in value_keys:
            industry = normalize_industry_name(_record_value(row, key))
            if industry:
                return industry
    return ""


def extract_industry_from_cninfo_change(info: Any) -> str:
    rows = list(_iter_record_rows(info) or [])
    standard_priority = (
        "申银万国行业分类标准",
        "中证行业分类标准",
        "巨潮行业分类标准",
        "中国上市公司协会上市公司行业分类标准",
    )
    value_keys = ("行业中类", "行业大类", "行业次类", "行业门类")

    def row_date(row: Any) -> str:
        return str(_record_value(row, "变更日期") or "")

    def row_industry(row: Any) -> str:
        for key in value_keys:
            industry = normalize_industry_name(_record_value(row, key))
            if industry:
                return industry
        return ""

    for standard in standard_priority:
        selected = [
            row for row in rows
            if standard in str(_record_value(row, "分类标准") or "")
        ]
        for row in sorted(selected, key=row_date, reverse=True):
            industry = row_industry(row)
            if industry:
                return industry

    for row in sorted(rows, key=row_date, reverse=True):
        industry = row_industry(row)
        if industry:
            return industry
    return ""


def _add_local_runtime_site_packages() -> None:
    global _LOCAL_SITE_PACKAGES_READY
    if _LOCAL_SITE_PACKAGES_READY:
        return
    _LOCAL_SITE_PACKAGES_READY = True
    version_dir = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_packages = DASHBOARD_HOME.parent / ".venv" / "lib" / version_dir / "site-packages"
    if site_packages.exists() and str(site_packages) not in sys.path:
        sys.path.insert(0, str(site_packages))


def load_stock_industry_cache() -> dict[str, str]:
    global _STOCK_INDUSTRY_MEMORY_CACHE
    if _STOCK_INDUSTRY_MEMORY_CACHE is not None:
        return dict(_STOCK_INDUSTRY_MEMORY_CACHE)
    try:
        raw = json.loads(STOCK_INDUSTRY_CACHE.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    cache = {
        normalize_stock_code(code): normalize_industry_name(industry)
        for code, industry in (raw or {}).items()
        if normalize_stock_code(code) and normalize_industry_name(industry)
    }
    _STOCK_INDUSTRY_MEMORY_CACHE = cache
    return dict(cache)


def save_stock_industry_cache(cache: dict[str, str]) -> None:
    global _STOCK_INDUSTRY_MEMORY_CACHE
    clean = {
        normalize_stock_code(code): normalize_industry_name(industry)
        for code, industry in (cache or {}).items()
        if normalize_stock_code(code) and normalize_industry_name(industry)
    }
    _STOCK_INDUSTRY_MEMORY_CACHE = clean
    try:
        STOCK_INDUSTRY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STOCK_INDUSTRY_CACHE.with_suffix(STOCK_INDUSTRY_CACHE.suffix + ".new")
        tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(STOCK_INDUSTRY_CACHE)
    except Exception as exc:
        print(f"[WARN] stock industry cache save failed: {type(exc).__name__}", file=sys.stderr)


def lookup_stock_industry(code: str, ak_module: Any | None = None) -> str:
    code = normalize_stock_code(code)
    if not code:
        return ""
    if ak_module is None:
        _add_local_runtime_site_packages()
        import akshare as ak_module

    for attempt in range(2):
        try:
            info = ak_module.stock_industry_change_cninfo(
                symbol=code,
                start_date="19900101",
                end_date=time.strftime("%Y%m%d"),
            )
            industry = extract_industry_from_cninfo_change(info)
            if industry:
                return industry
        except Exception:
            if attempt == 0:
                time.sleep(0.4)
                continue
            break

    info = ak_module.stock_individual_info_em(symbol=code)
    return extract_industry_from_individual_info(info)


def annotate_candidate_industries(
    *groups: list[dict[str, Any]],
    lookup: Callable[[str], str | None] | None = None,
) -> None:
    """Attach industry/sector labels to candidate rows without making them required."""
    missing_by_code: dict[str, list[dict[str, Any]]] = {}

    for group in groups:
        for item in group or []:
            if not isinstance(item, dict):
                continue
            industry = normalize_industry_name(
                item.get("industry") or item.get("sector") or item.get("board")
            )
            if industry:
                item["industry"] = industry
                item["sector"] = industry
                continue
            code = normalize_stock_code(item.get("code"))
            if not code:
                continue
            missing_by_code.setdefault(code, []).append(item)

    def fill_code(code: str, industry: str) -> None:
        industry = normalize_industry_name(industry)
        if not industry:
            return
        for item in missing_by_code.get(code, []):
            item["industry"] = industry
            item["sector"] = industry
        missing_by_code.pop(code, None)

    if lookup is None and missing_by_code:
        cache = load_stock_industry_cache()
        for code in list(missing_by_code):
            fill_code(code, cache.get(code, ""))

        if missing_by_code:
            cache_changed = False
            for code in list(missing_by_code):
                try:
                    industry = normalize_industry_name(lookup_stock_industry(code))
                except Exception:
                    industry = ""
                if industry:
                    cache[code] = industry
                    cache_changed = True
                    fill_code(code, industry)
                time.sleep(0.08)
            if cache_changed:
                save_stock_industry_cache(cache)
        return

    failures: list[str] = []
    for code, items in missing_by_code.items():
        try:
            industry = normalize_industry_name((lookup or lookup_stock_industry)(code))
        except Exception as exc:
            failures.append(f"{code}:{type(exc).__name__}")
            continue
        if not industry:
            continue
        for item in items:
            item["industry"] = industry
            item["sector"] = industry

    if failures:
        sample = ", ".join(failures[:5])
        more = f" (+{len(failures) - 5})" if len(failures) > 5 else ""
        print(f"[WARN] candidate industry lookup failed: {sample}{more}", file=sys.stderr)


# ========== Main ==========

def grok_industry_classify(candidates: list[dict]) -> None:
    """用 Grok 一次性查询所有候选股的行业分类。"""
    if not candidates:
        return
    try:
        import yaml
        cfg_path = Path(os.environ.get("DASHBOARD_CONFIG", DASHBOARD_HOME / "config.yaml")).expanduser()
        cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
        providers = cfg.get("custom_providers", [])
        crossdesk = next((p for p in providers if "crossdesk" in str(p.get("name","")).lower()), None)
        if not crossdesk: return
        base = crossdesk["base_url"].rstrip("/"); api_key = crossdesk["api_key"]
        stock_list = "\n".join(f"{c['code']} {c['name']}" for c in candidates)
        prompt = f"对以下A股每只给一个简短行业标签（如通信设备、半导体、汽车零部件）。只输出：代码 名称：行业\n\n{stock_list}"
        payload = {"model":"grok-4.20-multi-agent-xhigh","messages":[{"role":"user","content":prompt}],"max_tokens":200}
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "NiuOne/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8","ignore"))
        for line in data["choices"][0]["message"]["content"].strip().split("\n"):
            for c in candidates:
                if c["code"] in line and c["name"] in line:
                    parts = line.split("：",1) if "：" in line else line.split(":",1) if ":" in line else [line,""]
                    if len(parts) >= 2: c["industry"] = parts[1].strip()
                    break
    except Exception: pass


def write_outputs(json_str: str, generated_at: str) -> None:
    """Write B1 cache (backward compat), multi-strategy cache, and archives."""
    B1_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Multi-strategy cache (primary)
    tmp_ms = MULTI_STRATEGY_CACHE.with_suffix(MULTI_STRATEGY_CACHE.suffix + ".new")
    tmp_ms.write_text(json_str + "\n", encoding="utf-8")
    tmp_ms.replace(MULTI_STRATEGY_CACHE)

    # B1 cache (backward compat for dashboard/现有pipeline)
    tmp_b1 = B1_CACHE_FILE.with_suffix(B1_CACHE_FILE.suffix + ".new")
    tmp_b1.write_text(json_str + "\n", encoding="utf-8")
    tmp_b1.replace(B1_CACHE_FILE)

    # Archive
    safe_ts = str(generated_at).replace(":", "-").replace(" ", "_")
    date_part = safe_ts.split("_")[0]

    for archive_dir in [B1_HISTORY_DIR, MULTI_STRATEGY_HISTORY]:
        d = archive_dir / date_part
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{safe_ts}.json"
        ft = f.with_suffix(f.suffix + ".new")
        ft.write_text(json_str + "\n", encoding="utf-8")
        ft.replace(f)


def main():
    print("Step 1: Loading A-share code pool...", file=sys.stderr)
    candidates = load_main_board_code_pool()

    print(f"  Main board (non-ST): {len(candidates)} stocks", file=sys.stderr)

    print("Step 2: Fetching real-time batch quotes...", file=sys.stderr)
    tencent_keys = {}
    all_keys = []
    for code, name in candidates:
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        tk = prefix + code
        tencent_keys[code] = tk
        all_keys.append(tk)

    quotes = {}
    batch_size = 150
    for i in range(0, len(all_keys), batch_size):
        batch = all_keys[i:i + batch_size]
        q = tencent_batch_quote(batch)
        quotes.update(q)
        time.sleep(0.05)

    # Filter by liquidity
    liquid = []
    for code, name in candidates:
        tk = tencent_keys[code]
        q = quotes.get(tk, {})
        price = q.get("price")
        amount = q.get("amount") or 0
        if price is None or price <= 0:
            continue
        if amount < 8e8:
            continue
        liquid.append((code, name, q))

    liquid.sort(key=lambda x: x[2].get("amount", 0), reverse=True)
    top_n = min(500, len(liquid))
    to_analyze = liquid[:top_n]
    print(f"  High liquidity (成交额>8亿): {len(liquid)}, analyzing top {top_n}", file=sys.stderr)

    print("Step 3: Multi-strategy scoring (registered strategy profiles)...", file=sys.stderr)
    results = []
    for i, (code, name, q) in enumerate(to_analyze):
        tencent_key = tencent_keys[code]
        multi = analyze_all_strategies(code, tencent_key, quote=q, name=name)
        if multi is None:
            continue
        # Backward compat fields
        best = multi["strategies"].get(multi["best_strategy"], {})
        results.append({
            "code": code,
            "name": name,
            "price": q.get("price"),
            "change_pct": q.get("change_pct"),
            "amount": q.get("amount"),
            "amount_yi": round(q.get("amount", 0) / 1e8, 1) if q.get("amount") else None,
            "turnover": q.get("turnover"),
            # backward compat (dashboard B1 panel expects these)
            "score": best.get("score", 0),
            "score_total": best.get("score_total", 10),
            "verdict": best.get("verdict", ""),
            "bbi": best.get("bbi"),
            "distance_pct": best.get("distance_pct"),
            "bbi_upward": best.get("bbi_upward", False),
            "above_bbi": best.get("above_bbi", False),
            "min_j_10d": best.get("min_j_10d"),
            "current_j": best.get("current_j"),
            "j_recovering": best.get("j_recovering", False),
            "j_oversold": best.get("j_oversold", False),
            "risk_flags": best.get("risk_flags", []),
            "change_pct": q.get("change_pct"),
            # multi-strategy fields
            "best_strategy": multi["best_strategy"],
            "best_score": multi["best_score"],
            "best_decision_score": multi.get("best_decision_score", multi["best_score"]),
            "best_verdict": multi["best_verdict"],
            "entry_threshold": best.get("entry_threshold"),
            "strategy_priority": best.get("strategy_priority"),
            "score_basis": best.get("score_basis"),
            "position_hint": best.get("position_hint"),
            "time_stop": best.get("time_stop"),
            "actionable": best.get("actionable"),
            "hard_blockers": best.get("hard_blockers", []),
            "trade_ready": candidate_is_trade_ready(best),
            "strategies": multi["strategies"],
            "consensus_count": multi.get("consensus_count", 0),
            "consensus_boost": multi.get("consensus_boost", 0),
        })
        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1}/{len(to_analyze)} analyzed", file=sys.stderr)
        time.sleep(0.02)

    # Sort: best_score desc, above_bbi bonus, closer to BBI better
    def sort_key(item):
        s = item.get("best_decision_score") or item["best_score"]
        above = 1 if item.get("above_bbi") else 0
        dist = abs(item.get("distance_pct") or 99)
        return (s, above, -dist)

    results.sort(key=sort_key, reverse=True)
    display_candidates = select_display_candidates(results)
    trade_candidates = select_trade_candidates(results)
    annotate_candidate_industries(display_candidates, trade_candidates)

    print(f"  Analyzed: {len(results)} stocks", file=sys.stderr)
    print(f"  Strategy distribution:", file=sys.stderr)
    from collections import Counter
    strat_counts = Counter(r["best_strategy"] for r in results)
    for k, v in strat_counts.most_common():
        print(f"    {active_strategy_meta().get(k, {}).get('label', k)}: {v}", file=sys.stderr)

    # Output
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    
    # 融资 + 大宗交易信号（优先展示候选）
    for item in display_candidates[:10]:
        try:
            ms = get_margin_signal(item["code"])
            if ms: item["margin_signal"] = ms
        except Exception: pass
        try:
            bt = get_block_trade_signal(item["code"])
            if bt: item["block_trade_signal"] = bt
        except Exception: pass
    
    output = {
        "generated_at": generated_at,
        "items": display_candidates,
        "candidates": display_candidates,
        "count": len(display_candidates),
        "trade_items": trade_candidates,
        "trade_count": len(trade_candidates),
        "total_analyzed": len(results),
        "strategy_distribution": dict(strat_counts),
        "strategy_meta": active_strategy_meta(),
        "strategy_score_profiles": active_strategy_score_profiles(),
    }
    json_str = json.dumps(output, ensure_ascii=False, indent=2)
    print(json_str)
    write_outputs(json_str, generated_at)


if __name__ == "__main__":
    main()
