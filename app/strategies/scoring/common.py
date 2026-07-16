"""Shared constants, indicators, and score-profile helpers."""
import statistics
from typing import Any

from ..registry import STRATEGY_SCORE_PROFILES


COMMON_MAX_BBI_DISTANCE_PCT = 6.5
B1_CORE_J_CEILING = -10.0
B1_WATCH_J_CEILING = 12.0
LI_DAXIAO_MIN_AMOUNT = 1.2e9
LI_DAXIAO_MAX_TURNOVER = 6.0
LI_DAXIAO_HOT_TURNOVER = 8.0
LI_DAXIAO_MAX_BBI_DISTANCE = 3.5
LI_DAXIAO_MAX_DAILY_CHASE_PCT = 3.5


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
    is_sector_tide = strategy_name in {"tide_leader", "tide_rotation", "tide_recovery"}
    if not is_sector_tide and dist is not None and dist > COMMON_MAX_BBI_DISTANCE_PCT:
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
    elif is_sector_tide:
        if not payload.get("market_allows_buys") or payload.get("market_hard_stop"):
            blockers.append("市场风控禁止新开仓")
        if not payload.get("sector_data_eligible"):
            blockers.append("行业有效样本不足")
        if not payload.get("risk_ok"):
            blockers.append("结构止损超过1.5ATR或6%")
        effective_loss = safe_float(payload.get("effective_loss_distance_pct"))
        dynamic_cap = safe_float(payload.get("max_position_pct_by_risk"))
        if effective_loss is None or effective_loss <= 0 or dynamic_cap is None or dynamic_cap <= 0:
            blockers.append("动态风险预算无法计算")

        status = str(payload.get("sector_status") or "")
        regime = str(payload.get("market_regime") or "")
        rank = safe_float(payload.get("stock_sector_rank")) or 0.0
        acceleration = safe_float(payload.get("sector_rank_acceleration")) or 0.0
        extension = safe_float(payload.get("extension_atr"))
        change = safe_float(payload.get("change_pct"))
        if strategy_name == "tide_leader":
            if regime not in {"offensive", "rotation"}:
                blockers.append("主线领航仅用于进攻/轮动行情")
            if status != "leading":
                blockers.append("行业不是领先潮位")
            if rank < 80:
                blockers.append("个股未进入行业前20%")
            if not (payload.get("breakout") or payload.get("pullback")):
                blockers.append("未形成突破/缩量回踩买点")
            if extension is not None and extension > 2:
                blockers.append("距EMA20超过2ATR")
        elif strategy_name == "tide_rotation":
            if regime not in {"offensive", "rotation"}:
                blockers.append("轮动初升仅用于进攻/轮动行情")
            if status != "improving":
                blockers.append("行业不是改善潮位")
            if acceleration < 15:
                blockers.append("行业排名加速度不足")
            if rank < 70:
                blockers.append("个股未进入行业前30%")
            if change is not None and change > 7:
                blockers.append("单日涨幅>7%拒绝追高")
            if extension is not None and extension > 1.5:
                blockers.append("距EMA20超过1.5ATR")
            if not (payload.get("breakout") or payload.get("pullback") or payload.get("reclaim")):
                blockers.append("轮动买点未确认")
        elif strategy_name == "tide_recovery":
            if regime != "recovery":
                blockers.append("市场未处冰点修复")
            if status not in {"leading", "improving"}:
                blockers.append("行业未率先修复")
            if rank < 70:
                blockers.append("个股修复强度不足")
            if not (payload.get("reclaim") or payload.get("breakout")):
                blockers.append("未站回EMA20/突破修复高点")
            if extension is not None and extension > 1.5:
                blockers.append("距EMA20超过1.5ATR")

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


def find_n_structure_prior_low(rows, end_idx=None, *, lookback=30, tolerance_pct=0.02):
    """Return the latest rising local swing low before ``end_idx``."""
    if not rows:
        return None
    end_idx = len(rows) if end_idx is None else min(len(rows), max(0, int(end_idx)))
    if end_idx < 4:
        return None
    start = max(0, end_idx - max(5, int(lookback)))
    swing_lows = []
    for idx in range(max(1, start + 1), min(end_idx, len(rows) - 1)):
        try:
            low = float(rows[idx].get("low") or 0)
            prev_low = float(rows[idx - 1].get("low") or 0)
            next_low = float(rows[idx + 1].get("low") or 0)
        except (TypeError, ValueError):
            continue
        if low > 0 and prev_low > 0 and next_low > 0 and low <= prev_low and low <= next_low:
            swing_lows.append((idx, low))
    for latest_pos in range(len(swing_lows) - 1, 0, -1):
        earlier = swing_lows[latest_pos - 1]
        latest = swing_lows[latest_pos]
        if latest[1] >= earlier[1] * (1 - float(tolerance_pct)):
            idx, price = latest
            return {
                "price": round(price, 3),
                "date": str(rows[idx].get("date") or ""),
                "previous_price": round(earlier[1], 3),
                "previous_date": str(rows[earlier[0]].get("date") or ""),
            }
    return None


def n_structure_ok(rows, lookback=20):
    return find_n_structure_prior_low(rows, lookback=lookback) is not None


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
