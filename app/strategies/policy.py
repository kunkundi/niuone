"""Pure strategy-level position and candidate eligibility policies."""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from .lifecycle import (
    NIUONE_LIFECYCLE_ACTION_LABELS,
    niuone_lifecycle_entry_blocker,
)
from .niuone_risk import (
    NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE,
    NIUONE_MARKUP_REBALANCE_MIN_SESSIONS_AFTER_ADD,
    NIUONE_MARKUP_REBALANCE_PULLBACK_ATR,
    NIUONE_MARKUP_REBALANCE_STALL_MIN_ATR,
    NIUONE_MARKUP_REBALANCE_STALL_SESSIONS,
    NIUONE_MARKUP_UPGRADE_MAX_PNL_PCT,
    NIUONE_MARKUP_UPGRADE_MIN_PNL_PCT,
    complete_niuone_entry_fields,
    niuone_markup_momentum_probe_eligible,
)
from .registry import STRATEGY_DEFINITIONS, STRATEGY_POSITION_LIMIT_PCT


NIUONE_TODAY_OBSERVATION_THRESHOLD = 60.0
NIUONE_LEADER_MIN_SECTOR_RANK = 80.0
NIUONE_MATURE_MIN_MARKET_AMOUNT_PERCENTILE = 60.0
NIUONE_MATURE_MIN_THEME_AMOUNT_PERCENTILE = 50.0
NIUONE_MIN_ENTRY_TURNOVER_PCT = 3.0
NIUONE_DAILY_V_MIN_RECOVERY_RATIO = 0.60
NIUONE_DAILY_V_MAX_RECOVERY_RATIO = 2.0
NIUONE_REVERSAL_CONTINUATION_MIN_STRONG_COUNT = 6
NIUONE_REVERSAL_CONTINUATION_MIN_STATE_STREAK = 3
NIUONE_REVERSAL_ENTRY_MAX_CHANGE_PCT_EXCLUSIVE = 3.0
NIUONE_THEME_ATTRIBUTION_CONFIDENCE_SCORE = 60.0
NIUONE_THEME_LEADER_MIN_ATTRIBUTION_WEIGHT = 0.15


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def niu_leader_entry_breadth_blocker(
    values: Mapping[str, Any],
) -> str | None:
    """Require relative leadership plus stage-appropriate theme resonance."""
    sector_rank = _safe_float(values.get("stock_sector_rank"), -1.0)
    if sector_rank + 1e-9 < NIUONE_LEADER_MIN_SECTOR_RANK:
        return f"牛牛领涨个股需处于主线前{100 - NIUONE_LEADER_MIN_SECTOR_RANK:g}%"
    # A confirmed theme may enter divergence while one of its core leaders
    # remains strong.  The five-stage contract explicitly permits that leader
    # route; requiring broad same-day theme strength again would make the route
    # unreachable by construction.  Relative leadership and the existing
    # strong/leader-tier checks remain mandatory.
    if (
        str(values.get("niuone_lifecycle_stage") or "") == "divergence"
        or str(values.get("mainline_state") or values.get("sector_status") or "")
        == "diverging"
    ):
        return None
    today_strength = _safe_float(values.get("today_strength_score"), -1.0)
    if today_strength + 1e-9 < NIUONE_TODAY_OBSERVATION_THRESHOLD:
        return (
            "牛牛领涨需题材当日强度"
            f"≥{NIUONE_TODAY_OBSERVATION_THRESHOLD:g}"
        )
    return None


def niu_startup_theme_blocker(values: Mapping[str, Any]) -> str | None:
    """Keep the startup identity on persistent emerging themes only."""
    status = str(
        values.get("sector_status")
        or values.get("mainline_state")
        or values.get("state")
        or ""
    )
    if status != "emerging":
        return "主题不处于跨日延续的待确认启动阶段"
    if values.get("mainline_cross_day_persistent") is not True:
        return "启动主题尚未跨交易日延续"
    return None


def niuone_stock_activity_blocker(
    strategy_id: str,
    values: Mapping[str, Any],
) -> str | None:
    """Require observed capital participation for every NiuOne BUY route.

    The legacy opt-in flag remains readable metadata, never an exemption.
    Intraday turnover is actual cumulative turnover, not a full-day estimate.
    """
    if strategy_id not in NIUONE_LIFECYCLE_ACTION_LABELS:
        return None
    if values.get("stock_activity_data_available") is not True:
        return "个股成交活跃度数据不可用，牛牛各阶段暂停入选"
    market_percentile = _safe_float(
        values.get("stock_market_amount_percentile"),
        -1.0,
    )
    theme_percentile = _safe_float(
        values.get("stock_theme_amount_percentile"),
        -1.0,
    )
    if (
        not math.isfinite(market_percentile)
        or not math.isfinite(theme_percentile)
        or not 0 <= market_percentile <= 100
        or not 0 <= theme_percentile <= 100
        or market_percentile + 1e-9
        < NIUONE_MATURE_MIN_MARKET_AMOUNT_PERCENTILE
        or theme_percentile + 1e-9
        < NIUONE_MATURE_MIN_THEME_AMOUNT_PERCENTILE
    ):
        return (
            "个股成交活跃度不足（全市场成交额分位需≥"
            f"{NIUONE_MATURE_MIN_MARKET_AMOUNT_PERCENTILE:g}，题材内需≥"
            f"{NIUONE_MATURE_MIN_THEME_AMOUNT_PERCENTILE:g}）"
        )
    return niuone_turnover_blocker(values.get("turnover"))


def niuone_turnover_blocker(turnover: Any) -> str | None:
    """Check cumulative turnover in percentage points, including exactly 3%."""
    value = _safe_float(turnover, math.nan)
    if isinstance(turnover, bool) or not math.isfinite(value) or value < 0:
        return "个股换手率数据不可用，牛牛各阶段暂停买入"
    if value + 1e-9 < NIUONE_MIN_ENTRY_TURNOVER_PCT:
        return f"个股当日累计换手率需≥{NIUONE_MIN_ENTRY_TURNOVER_PCT:g}%，当前{value:.2f}%"
    return None


def niu_reversal_entry_price_blocker(
    *,
    price: Any,
    previous_close: Any,
) -> str | None:
    """Reject chased first probes using the execution price, never a scan price.

    Composition layers call this only for a zero-position Probe BUY. Existing
    holdings and mature entry routes keep their own scale-in/entry policies.
    """
    current = _safe_float(price, math.nan)
    reference = _safe_float(previous_close, math.nan)
    if (
        isinstance(price, bool)
        or isinstance(previous_close, bool)
        or not math.isfinite(current)
        or not math.isfinite(reference)
        or current <= 0
        or reference <= 0
    ):
        return "牛牛试仓新开仓缺少有效现价或前收盘价，等待有效报价"
    change_pct = (current / reference - 1.0) * 100.0
    if change_pct >= NIUONE_REVERSAL_ENTRY_MAX_CHANGE_PCT_EXCLUSIVE - 1e-9:
        return (
            f"牛牛试仓新开仓涨幅{change_pct:.2f}%达到"
            f"{NIUONE_REVERSAL_ENTRY_MAX_CHANGE_PCT_EXCLUSIVE:g}%，等待回落后重新评估"
        )
    return None


def niu_reversal_recovery_blocker(
    values: Mapping[str, Any],
) -> str | None:
    """Keep daily-V probes inside the confirmed but still-early recovery band."""
    recovery_ratio = _safe_float(
        values.get("daily_v_recovery_ratio"),
        -1.0,
    )
    if recovery_ratio + 1e-9 < NIUONE_DAILY_V_MIN_RECOVERY_RATIO:
        recovery_pct = NIUONE_DAILY_V_MIN_RECOVERY_RATIO * 100
        return f"V型右侧尚未收复左侧跌幅的{recovery_pct:g}%"
    if recovery_ratio >= NIUONE_DAILY_V_MAX_RECOVERY_RATIO - 1e-9:
        recovery_pct = NIUONE_DAILY_V_MAX_RECOVERY_RATIO * 100
        return f"V型右侧修复已达到左侧跌幅的{recovery_pct:g}%，不再按早期试仓"
    return None


def niu_reversal_theme_attribution_blocker(
    values: Mapping[str, Any],
) -> str | None:
    """Require participation in the selected theme, even for non-leader probes.

    Action routing retains a weak fallback for diagnostics. It is not entry
    evidence. Reuse the established theme membership boundary and preserve
    the high-evidence primary-theme exception for multi-concept stocks.
    """
    raw_weight = values.get("signal_theme_attribution_weight")
    raw_score = values.get("signal_theme_attribution_score")
    weight = _safe_float(raw_weight, math.nan)
    score = _safe_float(raw_score, math.nan)
    if (
        isinstance(raw_weight, bool) or isinstance(raw_score, bool)
        or not math.isfinite(weight) or not 0 <= weight <= 1
        or not math.isfinite(score) or not 0 <= score <= 100
    ):
        return "牛牛试仓题材归因数据不可用，等待有效归因证据"
    if weight >= NIUONE_THEME_LEADER_MIN_ATTRIBUTION_WEIGHT:
        return None

    # Recompute primary identity from the complete attribution list; a model
    # flag or a high score on a secondary theme cannot grant this exception.
    attributions = values.get("theme_attributions")
    if isinstance(attributions, list) and attributions:
        valid: list[Mapping[str, Any]] = []
        for item in attributions:
            if not isinstance(item, Mapping):
                break
            item_score = _safe_float(item.get("attribution_score"), math.nan)
            if (
                not str(item.get("theme") or "").strip()
                or isinstance(item.get("attribution_score"), bool)
                or not math.isfinite(item_score) or not 0 <= item_score <= 100
            ):
                break
            valid.append(item)
        if len(valid) == len(attributions):
            # The scorer already orders score ties by stock rank and name;
            # max preserves that order without requiring omitted rank fields.
            primary = max(valid, key=lambda item: float(item["attribution_score"]))
            if (
                str(primary["theme"]).strip()
                == str(values.get("signal_theme") or values.get("industry") or "").strip()
                and score >= NIUONE_THEME_ATTRIBUTION_CONFIDENCE_SCORE
                and math.isclose(score, float(primary["attribution_score"]), abs_tol=1e-9)
                and math.isclose(
                    weight, _safe_float(primary.get("attribution_weight"), math.nan),
                    abs_tol=1e-9,
                )
            ):
                return None
    return (
        "牛牛试仓题材归因不足（权重需≥15%，"
        "或为归因分≥60的首要题材）"
    )


def niu_reversal_continuation_blocker(
    values: Mapping[str, Any],
) -> str | None:
    """Require either broad participation or a sustained brewing state."""
    strong_count = _safe_float(values.get("strong_stock_count"), -1.0)
    state_streak = _safe_float(values.get("mainline_state_streak"), -1.0)
    if (
        strong_count + 1e-9
        >= NIUONE_REVERSAL_CONTINUATION_MIN_STRONG_COUNT
        or state_streak + 1e-9
        >= NIUONE_REVERSAL_CONTINUATION_MIN_STATE_STREAK
    ):
        return None
    return (
        "牛牛试仓需题材至少"
        f"{NIUONE_REVERSAL_CONTINUATION_MIN_STRONG_COUNT}只强势股，"
        "或酝酿状态连续至少"
        f"{NIUONE_REVERSAL_CONTINUATION_MIN_STATE_STREAK}个交易日"
    )


def niuone_markup_upgrade_blocker(
    source_strategy_id: str,
    candidate: Mapping[str, Any],
    *,
    current_pnl_pct: float,
    rebalance_reentry: bool = False,
) -> str | None:
    """Require profitable markup leadership before a staged scale-in."""
    source = str(source_strategy_id or "")
    incoming = str(
        candidate.get("best_strategy")
        or candidate.get("strategy_id")
        or ""
    )
    allowed_sources = {"niu_reversal_probe", "niu_emerging"}
    if rebalance_reentry:
        allowed_sources.add("niu_leader")
    if source not in allowed_sources:
        return (
            "牛牛波段补仓只适用于已确认领涨仓"
            if rebalance_reentry
            else "牛牛阶段加仓只适用于试仓或启动观察仓"
        )
    if str(candidate.get("niuone_lifecycle_stage") or "") != "markup":
        return "牛牛只在主升阶段加仓，高潮/分歧/退幕不加仓"
    if (
        candidate.get("stock_leader_tier") is not True
        or candidate.get("stock_strong") is not True
    ):
        return "牛牛主升加仓要求个股仍是强势领涨梯队"
    if current_pnl_pct + 1e-9 < NIUONE_MARKUP_UPGRADE_MIN_PNL_PCT:
        return (
            "牛牛主升加仓要求原持仓浮盈至少"
            f"{NIUONE_MARKUP_UPGRADE_MIN_PNL_PCT:g}%"
        )
    if (
        not rebalance_reentry
        and current_pnl_pct > NIUONE_MARKUP_UPGRADE_MAX_PNL_PCT + 1e-9
    ):
        return (
            "牛牛主升加仓仅限浮盈"
            f"≤{NIUONE_MARKUP_UPGRADE_MAX_PNL_PCT:g}%的延续窗口，"
            "避免高位追仓"
        )
    state = str(candidate.get("mainline_state") or "")
    if rebalance_reentry and incoming != "niu_leader":
        return "牛牛波段补仓只接受重新转强后的确认领涨信号"
    if incoming == "niu_emerging":
        if (
            state != "emerging"
            or candidate.get("mainline_cross_day_persistent") is not True
        ):
            return "牛牛主升早期加仓要求启动主线跨日延续"
        return None
    if incoming != "niu_leader":
        return "牛牛主升加仓只接受启动领涨或确认领涨信号"
    if candidate.get("mainline_confirmed") is not True:
        return "牛牛确认主升加仓要求主线完成跨日确认"
    if state != "mainline":
        return "牛牛确认主升加仓只允许确认主线状态"
    return None


def niuone_markup_rebalance_observation(
    position: Mapping[str, Any],
    *,
    current_price: float,
    atr: float,
    session_key: str,
    lifecycle_stage: str,
    current_pnl_pct: float,
    strong_leader: bool,
    pullback_atr: float = NIUONE_MARKUP_REBALANCE_PULLBACK_ATR,
    stall_sessions: int = NIUONE_MARKUP_REBALANCE_STALL_SESSIONS,
    stall_min_atr: float = NIUONE_MARKUP_REBALANCE_STALL_MIN_ATR,
    minimum_sessions_after_add: int = (
        NIUONE_MARKUP_REBALANCE_MIN_SESSIONS_AFTER_ADD
    ),
) -> dict[str, Any]:
    """Advance one causal markup wave and report whether it should trim.

    The returned ``state`` is applied by the caller.  A session is counted at
    most once, so repeated intraday evaluations cannot manufacture a sideways
    streak.  The function never arms a re-entry itself; that happens only
    after the partial sell is actually filled.
    """
    price = _safe_float(current_price, 0.0)
    resolved_atr = _safe_float(atr, 0.0)
    key = str(session_key or "")
    if price <= 0 or resolved_atr <= 0 or not key:
        return {"state": {}, "trim": False, "trigger": "unavailable"}
    if str(position.get("niuone_markup_rebalance_last_observation") or "") == key:
        return {"state": {}, "trim": False, "trigger": "duplicate_session"}

    raw_peak = _safe_float(
        position.get("niuone_markup_rebalance_cycle_peak_price"),
        0.0,
    )
    if raw_peak <= 0:
        return {
            "state": {
                "niuone_markup_rebalance_cycle_peak_price": round(price, 4),
                "niuone_markup_rebalance_stall_count": 0,
                "niuone_markup_rebalance_observation_count": 0,
                "niuone_markup_rebalance_last_observation": key,
            },
            "trim": False,
            "trigger": "cycle_initialized",
        }

    previous_stall = int(
        _safe_float(position.get("niuone_markup_rebalance_stall_count"), 0.0)
    )
    observations = int(
        _safe_float(
            position.get("niuone_markup_rebalance_observation_count"),
            0.0,
        )
    ) + 1
    new_peak = price > raw_peak + 1e-9
    peak = max(raw_peak, price)
    drawdown_atr = max(0.0, (peak - price) / resolved_atr)
    if new_peak or drawdown_atr + 1e-9 < float(stall_min_atr):
        stall_count = 0
    else:
        stall_count = previous_stall + 1
    state = {
        "niuone_markup_rebalance_cycle_peak_price": round(peak, 4),
        "niuone_markup_rebalance_drawdown_atr": round(drawdown_atr, 4),
        "niuone_markup_rebalance_stall_count": stall_count,
        "niuone_markup_rebalance_observation_count": observations,
        "niuone_markup_rebalance_last_observation": key,
    }
    pullback = drawdown_atr + 1e-9 >= float(pullback_atr)
    sideways = (
        stall_count >= int(stall_sessions)
        and drawdown_atr + 1e-9 >= float(stall_min_atr)
    )
    eligible = bool(
        lifecycle_stage in {"markup", "divergence"}
        and current_pnl_pct + 1e-9 >= NIUONE_MARKUP_UPGRADE_MIN_PNL_PCT
        and observations >= int(minimum_sessions_after_add)
        and position.get("niuone_markup_rebalance_armed") is not True
    )
    rebalance_trigger = bool(eligible and (pullback or sideways))
    return {
        "state": state,
        "trim": bool(
            rebalance_trigger
            and position.get("niuone_markup_rebalance_reduced") is not True
        ),
        "arm_existing_reduction": bool(
            rebalance_trigger
            and position.get("niuone_markup_rebalance_reduced") is True
        ),
        "trigger": "pullback" if pullback else "sideways" if sideways else "none",
        "drawdown_atr": round(drawdown_atr, 4),
        "stall_count": stall_count,
    }


def niuone_markup_rebalance_reentry_blocker(
    source_strategy_id: str,
    position: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    current_price: float,
    current_pnl_pct: float,
) -> str | None:
    """Require a filled trim and a fresh rebound before replacing risk."""
    if position.get("niuone_markup_rebalance_armed") is not True:
        return "牛牛波段补仓需先完成一次有效回落或横盘减仓"
    trigger_price = _safe_float(
        position.get("niuone_markup_rebalance_reentry_price"),
        0.0,
    )
    if trigger_price <= 0:
        return "牛牛波段补仓缺少重新转强价格"
    if current_price + 1e-9 < trigger_price:
        return (
            "牛牛波段补仓等待重新转强：现价"
            f"{current_price:.2f} < 触发价{trigger_price:.2f}"
        )
    return niuone_markup_upgrade_blocker(
        source_strategy_id,
        candidate,
        current_pnl_pct=current_pnl_pct,
        rebalance_reentry=True,
    )


def strategy_position_limit_pct(strategy: str, max_single_position_pct: float) -> float:
    return min(
        max_single_position_pct,
        float(STRATEGY_POSITION_LIMIT_PCT.get(strategy or "", max_single_position_pct)),
    )


def candidate_buy_blockers(
    candidate: dict[str, Any] | None,
    *,
    max_bbi_distance_pct: float = 6.5,
) -> list[str]:
    if not candidate:
        return ["买入标的不在本轮交易候选池"]
    candidate = complete_niuone_entry_fields(candidate)
    strategy_id = str(
        candidate.get("best_strategy")
        or candidate.get("buy_strategy")
        or candidate.get("strategy_id")
        or ""
    )
    blockers = [str(item) for item in (candidate.get("hard_blockers") or []) if str(item).strip()]
    if strategy_id in NIUONE_LIFECYCLE_ACTION_LABELS:
        lifecycle_blocker = niuone_lifecycle_entry_blocker(
            strategy_id,
            candidate,
        )
        if lifecycle_blocker and lifecycle_blocker not in blockers:
            blockers.append(lifecycle_blocker)
        activity_blocker = niuone_stock_activity_blocker(
            strategy_id,
            candidate,
        )
        if activity_blocker and activity_blocker not in blockers:
            blockers.append(activity_blocker)
    raw_score = candidate.get("best_score")
    if raw_score is None:
        raw_score = candidate.get("score")
    score = _safe_float(raw_score, 0.0)
    threshold = _safe_float(candidate.get("entry_threshold"), 8.0)
    if score < threshold:
        blockers.append(f"评分{score:g}<基准{threshold:g}")
    if candidate.get("actionable") is False:
        blockers.append("候选未通过战法硬过滤")
    if strategy_id == "niu_leader":
        leader_breadth_blocker = niu_leader_entry_breadth_blocker(candidate)
        if leader_breadth_blocker and leader_breadth_blocker not in blockers:
            blockers.append(leader_breadth_blocker)
    if strategy_id == "niu_emerging":
        startup_blocker = niu_startup_theme_blocker(candidate)
        if startup_blocker and startup_blocker not in blockers:
            blockers.append(startup_blocker)
        if (
            str(candidate.get("niuone_entry_subroute") or "")
            == NIUONE_MARKUP_MOMENTUM_PROBE_SUBROUTE
            and not niuone_markup_momentum_probe_eligible(candidate)
        ):
            blockers.append("主升动量试仓身份条件不完整")
    if strategy_id == "niu_reversal_probe":
        attribution_blocker = niu_reversal_theme_attribution_blocker(candidate)
        if attribution_blocker and attribution_blocker not in blockers:
            blockers.append(attribution_blocker)
        recovery_blocker = niu_reversal_recovery_blocker(candidate)
        if recovery_blocker and recovery_blocker not in blockers:
            blockers.append(recovery_blocker)
        continuation_blocker = niu_reversal_continuation_blocker(candidate)
        if continuation_blocker and continuation_blocker not in blockers:
            blockers.append(continuation_blocker)
    persona = STRATEGY_DEFINITIONS.get(strategy_id, {}).get("persona")
    reversal_probe = strategy_id == "niu_reversal_probe"
    if persona == "niuone" and (
        candidate.get("daily_v_reversal") is not True
        if reversal_probe
        else (
            candidate.get("stock_leader_tier") is not True
            or candidate.get("stock_strong") is not True
        )
    ):
        leader_blocker = (
            "未形成日线区间V型反转"
            if reversal_probe
            else "个股未进入强势行业龙头梯队"
        )
        if leader_blocker not in blockers:
            blockers.append(leader_blocker)
    is_ema_strategy = persona in {"sector_tide", "niuone"}
    distance = candidate.get("distance_pct")
    if not is_ema_strategy and distance is not None and _safe_float(distance, 99.0) > max_bbi_distance_pct:
        blockers.append(f"距BBI>{max_bbi_distance_pct}%")
    return blockers


def candidate_is_buyable(
    candidate: dict[str, Any] | None,
    *,
    max_bbi_distance_pct: float = 6.5,
) -> bool:
    return not candidate_buy_blockers(candidate, max_bbi_distance_pct=max_bbi_distance_pct)
