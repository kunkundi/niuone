#!/usr/bin/env python3
"""Shared strategy metadata for scanner, trader, and dashboard payloads."""
from __future__ import annotations

import os
from typing import Any


PERSONA_STRATEGY_ENV = "DASHBOARD_ENABLED_PERSONA_STRATEGIES"
STRATEGY_SOURCE_ENV = "DASHBOARD_STRATEGY_SOURCE"
ACTIVE_STRATEGY_ENV = "DASHBOARD_ACTIVE_STRATEGY"
PRESET_STRATEGY_TEXT_ENV = "DASHBOARD_PRESET_STRATEGY_TEXT"
TRADE_DISCIPLINE_TEXT_ENV = "DASHBOARD_TRADE_DISCIPLINE_TEXT"
STRATEGY_SOURCE_BUILTIN = "builtin"
STRATEGY_SOURCE_PERSONA = STRATEGY_SOURCE_BUILTIN
STRATEGY_SOURCE_PRESET_TEXT = "preset_text"
STRATEGY_SUITE_PRESET_TEXT = STRATEGY_SOURCE_PRESET_TEXT
PRESET_STRATEGY_TEXT_MAX_CHARS = 8000
TRADE_DISCIPLINE_TEXT_MAX_CHARS = 12000
BASIC_STRATEGY_GROUP_ID = "base"
DEFAULT_BUILTIN_STRATEGY_GROUP_ID = "zettaranc"
DEPRECATED_STRATEGY_OPTION_IDS = {"buffett_value"}
DEPRECATED_STRATEGY_SOURCE_ALIASES = {"persona": STRATEGY_SOURCE_BUILTIN}

STRATEGY_SOURCE_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "id": STRATEGY_SOURCE_BUILTIN,
        "label": "内置策略",
        "desc": "在基础策略、Z哥、李大霄、板块潮汐中选择一个参与选股和买卖决策",
        "color": "#60a5fa",
    },
    {
        "id": STRATEGY_SOURCE_PRESET_TEXT,
        "label": "预设文字",
        "desc": "由买卖决策模型把输入文字优化为本轮选股和买卖规则",
        "color": "#2dd4bf",
    },
)

STRATEGY_DEFINITIONS: dict[str, dict[str, Any]] = {
    "b3_accelerate": {
        "label": "B3中继",
        "color": "#a78bfa",
        "desc": "B2后小阳/十字星分歧转一致",
        "family": "persona",
        "persona": "zettaranc",
        "scorer": "score_b3_accelerate",
        "display_order": 10,
        "position_limit_pct": 10.0,
        "aliases": ["B3", "B3中继", "b3_accelerate"],
        "profile": {
            "priority": 90,
            "entry_threshold": 8.5,
            "score_basis": "确定性最高/盈亏比最低",
            "position_hint": "快进快出，次日09:37不涨走",
            "time_stop": "T+1开盘09:37不涨退出",
            "certainty_rank": 1,
            "risk_reward_rank": 4,
        },
    },
    "b2_confirm": {
        "label": "B2确认",
        "color": "#22c55e",
        "desc": "B1后3日内放量中/大阳确认趋势",
        "family": "persona",
        "persona": "zettaranc",
        "scorer": "score_b2_confirm",
        "display_order": 20,
        "position_limit_pct": 10.0,
        "aliases": ["B2", "B2确认", "b2_confirm"],
        "profile": {
            "priority": 82,
            "entry_threshold": 8.0,
            "score_basis": "趋势确认/放量长阳",
            "position_hint": "确认仓，拒绝追高",
            "time_stop": "T+2尾盘14:45不延续退出",
            "certainty_rank": 2,
            "risk_reward_rank": 3,
        },
    },
    "breakout": {
        "label": "突破确认",
        "color": "#ec4899",
        "desc": "平台/前高突破后回踩站稳",
        "family": "local",
        "scorer": "score_breakout",
        "display_order": 30,
        "position_limit_pct": 10.0,
        "aliases": ["突破确认", "突破", "breakout"],
        "profile": {
            "priority": 76,
            "entry_threshold": 8.0,
            "score_basis": "突破确认",
            "position_hint": "确认仓",
            "time_stop": "跌回平台内降预期",
            "certainty_rank": 2,
            "risk_reward_rank": 3,
        },
    },
    "shaofu_b1": {
        "label": "少妇B1",
        "color": "#f97316",
        "desc": "J≤12(最好负值)+N型上移+缩量回调+牛绳约束",
        "family": "persona",
        "persona": "zettaranc",
        "scorer": "score_shaofu_b1",
        "display_order": 40,
        "position_limit_pct": 8.0,
        "aliases": ["少妇B1", "shaofu_b1"],
        "profile": {
            "priority": 72,
            "entry_threshold": 8.0,
            "score_basis": "胜率与盈亏比优先",
            "position_hint": "试错仓，止损必须近",
            "time_stop": "3天不涨走",
            "certainty_rank": 3,
            "risk_reward_rank": 1,
        },
    },
    "trend_pullback": {
        "label": "趋势回踩",
        "color": "#60a5fa",
        "desc": "趋势股回踩BBI/EMA不破",
        "family": "local",
        "scorer": "score_trend_pullback",
        "display_order": 50,
        "position_limit_pct": 8.0,
        "aliases": ["趋势回踩", "trend_pullback"],
        "profile": {
            "priority": 68,
            "entry_threshold": 8.0,
            "score_basis": "趋势回踩",
            "position_hint": "低吸仓",
            "time_stop": "跌破BBI/EMA支撑走",
            "certainty_rank": 3,
            "risk_reward_rank": 2,
        },
    },
    "tide_leader": {
        "label": "主线领航",
        "color": "#06b6d4",
        "desc": "进攻或轮动行情中强行业的领涨突破/缩量回踩",
        "family": "persona",
        "persona": "sector_tide",
        "scorer": "score_tide_leader",
        "display_order": 60,
        "position_limit_pct": 8.0,
        "aliases": ["板块潮汐", "主线领航", "tide_leader"],
        "profile": {
            "priority": 88,
            "entry_threshold": 8.0,
            "score_basis": "领先行业/板块内领涨/趋势确认",
            "position_hint": "按有效损失距离动态定仓，单票绝对上限8%",
            "time_stop": "5日未创新高或不再跑赢行业退出",
            "certainty_rank": 1,
            "risk_reward_rank": 2,
        },
    },
    "tide_rotation": {
        "label": "轮动初升",
        "color": "#14b8a6",
        "desc": "行业排名与广度快速改善时捕捉第一梯队",
        "family": "persona",
        "persona": "sector_tide",
        "scorer": "score_tide_rotation",
        "display_order": 62,
        "position_limit_pct": 6.0,
        "aliases": ["轮动初升", "tide_rotation"],
        "profile": {
            "priority": 80,
            "entry_threshold": 8.2,
            "score_basis": "行业排名改善/第一梯队/拒绝补涨追高",
            "position_hint": "按轮动风险预算动态定仓，单票绝对上限6%",
            "time_stop": "3日不延续或行业改善失败退出",
            "certainty_rank": 2,
            "risk_reward_rank": 2,
        },
    },
    "tide_recovery": {
        "label": "冰点修复",
        "color": "#22d3ee",
        "desc": "复合风险解除后参与最先修复的行业和个股",
        "family": "persona",
        "persona": "sector_tide",
        "scorer": "score_tide_recovery",
        "display_order": 64,
        "position_limit_pct": 4.0,
        "aliases": ["冰点修复", "tide_recovery"],
        "profile": {
            "priority": 72,
            "entry_threshold": 8.5,
            "score_basis": "市场修复确认/行业率先转强/小仓验证",
            "position_hint": "按修复风险预算建观察仓，单票绝对上限4%，次日确认后才允许加仓",
            "time_stop": "T+2未确认修复退出",
            "certainty_rank": 3,
            "risk_reward_rank": 1,
        },
    },
    "super_b1": {
        "label": "超级B1",
        "color": "#fb7185",
        "desc": "放量破位洗盘后缩量企稳且J值仍负",
        "family": "persona",
        "persona": "zettaranc",
        "scorer": "score_super_b1",
        "display_order": 70,
        "position_limit_pct": 6.0,
        "aliases": ["超级B1", "super_b1"],
        "profile": {
            "priority": 58,
            "entry_threshold": 8.5,
            "score_basis": "洗盘反转/只赌一次",
            "position_hint": "小仓试错，破洗盘低点走",
            "time_stop": "14:45检查未兑现则退出",
            "certainty_rank": 4,
            "risk_reward_rank": 2,
        },
    },
    "li_daxiao_bottom": {
        "label": "李大霄",
        "color": "#f59e0b",
        "desc": "低估蓝筹、底部发育、逆向情绪和去杠杆防守代理",
        "family": "persona",
        "persona": "li_daxiao",
        "scorer": "score_li_daxiao_bottom",
        "display_order": 80,
        "position_limit_pct": 5.0,
        "aliases": ["李大霄", "李大霄底部", "李大霄低位企稳", "li_daxiao", "li_daxiao_bottom"],
        "profile": {
            "priority": 56,
            "entry_threshold": 8.0,
            "score_basis": "低估蓝筹/底部发育/安全边际代理",
            "position_hint": "余钱小仓、正金字塔分批，不追高不上杠杆",
            "time_stop": "底部发育失败、放量破位或题材过热退出",
            "certainty_rank": 4,
            "risk_reward_rank": 2,
            "decision_heuristics": [
                "做好人买好股得好报：只看主板高流动性蓝筹代理，优先低估和高股息方向",
                "黑五类回避：小盘、次新、伪成长、垃圾、题材炒作一律降级观察",
                "正金字塔分批：底部区域越跌越谨慎观察，涨疯了不追",
                "杠杆毒药：高换手、融资偿还、放量破位时不买或降仓",
            ],
        },
    },
}


STRATEGY_META: dict[str, dict[str, Any]] = {
    key: {
        "label": value["label"],
        "color": value["color"],
        "desc": value["desc"],
        "family": value.get("family", ""),
        "display_order": value.get("display_order", 999),
    }
    for key, value in STRATEGY_DEFINITIONS.items()
}

STRATEGY_SCORE_PROFILES: dict[str, dict[str, Any]] = {
    key: dict(value["profile"])
    for key, value in STRATEGY_DEFINITIONS.items()
}

STRATEGY_POSITION_LIMIT_PCT: dict[str, float] = {
    key: float(value.get("position_limit_pct", 10.0))
    for key, value in STRATEGY_DEFINITIONS.items()
}

DISPLAY_STRATEGY_ORDER: tuple[str, ...] = tuple(
    key
    for key, _ in sorted(
        STRATEGY_DEFINITIONS.items(),
        key=lambda item: int(item[1].get("display_order", 999)),
    )
)

_ALIAS_TO_STRATEGY: dict[str, str] = {}
for _strategy_id, _definition in STRATEGY_DEFINITIONS.items():
    for _alias in [_strategy_id, *(_definition.get("aliases") or [])]:
        _ALIAS_TO_STRATEGY[str(_alias).lower()] = _strategy_id


def known_strategy_ids() -> set[str]:
    return set(STRATEGY_DEFINITIONS.keys())


def strategy_ids_for_family(family: str) -> tuple[str, ...]:
    return tuple(
        key
        for key in DISPLAY_STRATEGY_ORDER
        if STRATEGY_DEFINITIONS.get(key, {}).get("family") == family
    )


def strategy_ids_for_persona(persona: str) -> tuple[str, ...]:
    return tuple(
        key
        for key in DISPLAY_STRATEGY_ORDER
        if STRATEGY_DEFINITIONS.get(key, {}).get("persona") == persona
    )


STRATEGY_SUITES: dict[str, dict[str, Any]] = {
    BASIC_STRATEGY_GROUP_ID: {
        "id": BASIC_STRATEGY_GROUP_ID,
        "label": "基础策略",
        "desc": "突破确认、趋势回踩",
        "color": "#60a5fa",
        "strategy_ids": strategy_ids_for_family("local"),
    },
    "zettaranc": {
        "id": "zettaranc",
        "label": "Z哥",
        "desc": "少妇B1、B2确认、B3中继、超级B1、卖出风控",
        "color": "#f97316",
        "strategy_ids": strategy_ids_for_persona("zettaranc"),
    },
    "li_daxiao_bottom": {
        "id": "li_daxiao_bottom",
        "label": "李大霄",
        "desc": "低估蓝筹、底部发育、逆向情绪和去杠杆防守代理",
        "color": "#f59e0b",
        "strategy_ids": ("li_daxiao_bottom",),
    },
    "sector_tide": {
        "id": "sector_tide",
        "label": "板块潮汐",
        "desc": "市场状态、行业轮动、板块内领涨与冰点修复",
        "color": "#06b6d4",
        "strategy_ids": strategy_ids_for_persona("sector_tide"),
    },
}

# Backward-compatible export for integrations that still use the old name.
CONFIGURABLE_STRATEGY_GROUPS = STRATEGY_SUITES


def individual_persona_strategy_ids() -> tuple[str, ...]:
    grouped_ids = {
        str(strategy_id)
        for group in STRATEGY_SUITES.values()
        for strategy_id in (group.get("strategy_ids") or ())
    }
    return tuple(
        key
        for key in DISPLAY_STRATEGY_ORDER
        if STRATEGY_DEFINITIONS.get(key, {}).get("family") == "persona" and key not in grouped_ids
    )


def configurable_strategy_option_ids() -> tuple[str, ...]:
    return tuple(STRATEGY_SUITES.keys()) + individual_persona_strategy_ids()


def default_enabled_persona_strategies_value() -> str:
    options = configurable_strategy_option_ids()
    if DEFAULT_BUILTIN_STRATEGY_GROUP_ID in options:
        return DEFAULT_BUILTIN_STRATEGY_GROUP_ID
    return options[0] if options else ""


def strategy_suite_options() -> list[dict[str, Any]]:
    """Return mutually exclusive, independently executable strategy suites."""
    suites = [
        {
            "id": key,
            "label": value["label"],
            "desc": value["desc"],
            "color": value["color"],
        }
        for key, value in STRATEGY_SUITES.items()
    ]
    suites.append({
        "id": STRATEGY_SUITE_PRESET_TEXT,
        "label": "预设文字策略",
        "desc": "使用用户文字规则独立决定候选、买入、卖出、仓位和时间纪律",
        "color": "#2dd4bf",
    })
    return suites


def normalize_strategy_suite_update(value: str | None) -> str:
    suite = str(value or DEFAULT_BUILTIN_STRATEGY_GROUP_ID).strip()
    # Accept the former source value as a migration alias. Its concrete suite
    # is resolved from the legacy group setting by active_strategy_suite().
    if suite == STRATEGY_SOURCE_BUILTIN:
        suite = DEFAULT_BUILTIN_STRATEGY_GROUP_ID
    allowed = {str(item["id"]) for item in strategy_suite_options()}
    if suite not in allowed:
        raise ValueError(f"未知独立策略: {suite}")
    return suite


def active_strategy_suite(
    raw: str | None = None,
    legacy_source_raw: str | None = None,
    legacy_group_raw: str | None = None,
) -> str:
    """Resolve the active suite, preferring the new single setting.

    Old ``builtin + group`` installations remain valid until the user next
    saves strategy settings, at which point DASHBOARD_ACTIVE_STRATEGY is used.
    """
    if raw is None:
        raw = os.environ.get(ACTIVE_STRATEGY_ENV)
    if raw:
        return normalize_strategy_suite_update(raw)
    if legacy_source_raw is None:
        legacy_source_raw = os.environ.get(STRATEGY_SOURCE_ENV)
    if str(legacy_source_raw or STRATEGY_SOURCE_BUILTIN).strip() == STRATEGY_SOURCE_PRESET_TEXT:
        return STRATEGY_SUITE_PRESET_TEXT
    if legacy_group_raw is None:
        legacy_group_raw = os.environ.get(PERSONA_STRATEGY_ENV)
    if legacy_group_raw is None:
        legacy_group_raw = default_enabled_persona_strategies_value()
    return normalize_strategy_suite_update(
        normalize_strategy_list_update(legacy_group_raw)
    )


def normalize_strategy_source_update(value: str | None) -> str:
    source = str(value or STRATEGY_SOURCE_BUILTIN).strip()
    source = DEPRECATED_STRATEGY_SOURCE_ALIASES.get(source, source)
    allowed = {item["id"] for item in STRATEGY_SOURCE_OPTIONS}
    if source not in allowed:
        raise ValueError(f"未知策略来源: {source}")
    return source


def active_strategy_source(raw: str | None = None) -> str:
    if raw is None:
        raw = os.environ.get(STRATEGY_SOURCE_ENV)
    return normalize_strategy_source_update(raw)


def preset_text_strategy_active(raw: str | None = None) -> bool:
    return active_strategy_source(raw) == STRATEGY_SOURCE_PRESET_TEXT


def normalize_multiline_setting_text_update(value: str | None, *, max_chars: int, label: str) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > max_chars:
        raise ValueError(f"{label}最多 {max_chars} 字")
    return text.replace("\n", "\\n")


def decode_multiline_setting_text(value: str | None) -> str:
    text = str(value or "")
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def normalize_preset_strategy_text_update(value: str | None) -> str:
    return normalize_multiline_setting_text_update(
        value,
        max_chars=PRESET_STRATEGY_TEXT_MAX_CHARS,
        label="预设文字策略",
    )


def decode_preset_strategy_text(value: str | None) -> str:
    return decode_multiline_setting_text(value)


def normalize_trade_discipline_text_update(value: str | None) -> str:
    return normalize_multiline_setting_text_update(
        value,
        max_chars=TRADE_DISCIPLINE_TEXT_MAX_CHARS,
        label="交易纪律",
    )


def decode_trade_discipline_text(value: str | None) -> str:
    return decode_multiline_setting_text(value)


def default_trade_discipline_text(
    *,
    max_open_positions: int = 6,
    max_new_buys_per_decision: int = 2,
    position_limit_desc: str = "无固定百分比硬限制",
    adaptive_label: str = "中性",
    adaptive_position_mult: float = 1.0,
    zettaranc_enabled: bool = True,
    sector_tide_enabled: bool = False,
) -> str:
    position_limit_desc = str(position_limit_desc or "无固定百分比硬限制")
    position_rule = (
        "- 板块潮汐由动态风险预算决定仓位：进攻/轮动/修复的单笔风险预算分别为权益0.30%/0.20%/0.10%，策略内组合未实现止损风险≤1.50%/0.80%/0.30%，总仓≤45%/30%/15%，行业敞口≤12%/10%/6%；防守禁止新仓。主线/轮动/修复的8%/6%/4%仅是单票绝对天花板，同一行业最多2只。"
        if sector_tide_enabled
        else
        "- Z哥人格仓位必须硬执行注册战法上限（单票最高10%）、总仓位≤80%、现金≥20%，高确定性也不得突破；其他人格首次建仓、加仓、减仓比例由评分、风险标记、盘面级别和账户状态决定。"
        if zettaranc_enabled
        else "- 仓位不按固定百分比硬卡：首次建仓、加仓、减仓比例由评分、风险标记、盘面级别和账户状态决定；集中持仓必须在reason说明依据。"
    )
    execution_rule = (
        "- 每条 BUY/SELL 必须给出100股整数倍 shares；板块潮汐执行层按“结构止损距离+近60日向下跳空P95与0.5ATR孰高+0.20%费用滑点”计算有效损失距离，再复核单笔、策略组合、行业风险预算和动态仓位上限；任何一项超限都直接拦截且不自动缩量。"
        if sector_tide_enabled
        else
        "- 每条 BUY/SELL 的仓位大小由你决定：必须给出100股整数倍 shares；仓位大小一律按“参考价或成交价 × shares ÷ 当前总权益 × 100%”定义，并在 reason 里写明这个百分比依据；执行层不会替你补默认仓位或自动缩量，Z哥仓位超限、现金不足、动态盘面暂停买入或超过可卖数量都会直接拦截。"
        if zettaranc_enabled
        else "- 每条 BUY/SELL 的仓位大小由你决定：必须给出100股整数倍 shares；仓位大小一律按“参考价或成交价 × shares ÷ 当前总权益 × 100%”定义，并在 reason 里写明这个百分比依据；执行层不会替你补默认仓位或自动缩量，现金不足、动态盘面暂停买入或超过可卖数量会直接拦截。"
    )
    risk_rule = (
        "- 板块潮汐退出：结构止损；行业分数<55连续两次；复合风险硬停止且行业转弱；主线5日/轮动3日/修复T+2不延续；达到2R先减半，余仓峰值-2ATR跟踪"
        if sector_tide_enabled
        else
        "- 系统底线风控：持仓超25日退出；Z哥按入场战法使用专属结构止损，防卖飞、卤煮、S1/S2/S3、出货五式、白线/黄线等归属于下方 Z哥卖出风控"
        if zettaranc_enabled
        else "- 系统底线风控：持仓超25日退出；其他止损止盈按当前激活策略和既有持仓标记执行"
    )
    registered_position_rule = (
        "- 板块潮汐动态风险预算、总仓/行业敞口和8%/6%/4%绝对上限是执行层硬限制，不是参考值。"
        if sector_tide_enabled
        else f"- 注册策略仓位纪律只作为参考：{position_limit_desc}。"
    )
    generic_exit_rules = [] if sector_tide_enabled else [
        "- 移动止损：盈利>5%后进入回撤保护，回到成本附近自动退出",
        "- 信号恶化退出：持有>10天仍未站回BBI且盈利不足，或持有>12天仍亏>3%，自动离场",
    ]
    return "\n".join([
        "- A股模拟成交窗口：09:30-11:30、13:00-15:00；09:15-09:25只作开盘集合竞价观察/申报参考，09:25-09:30为静默期，不得直接按参考价记成交。",
        "- T+1：今日买入的股票今日不可卖；只能卖available_qty。",
        "- 买入必须100股整数倍；不能融资、不能做空、现金不能为负。",
        f"- 单次决策最多给{max_new_buys_per_decision}条新买入；当前持仓达到{max_open_positions}只时只允许卖出/持有，不能继续开新仓，避免“开超市”。",
        position_rule,
        f"- 今日盘面监控指引优先调整买入节奏；谨慎/防守盘面必须主动缩手或等待确认，不能上午把{max_open_positions}只买满。",
        execution_rule,
        registered_position_rule,
        "- 综合决策参考是每次决策的必读输入：盘面监控、隔夜美股、指数/期货、板块涨跌、行业资金、热门股、消息面预检、当前仓位和现金状态都必须影响BUY/SELL/HOLD与shares。",
        "- 当前账户JSON里的 strategy_mark/buy_strategy/entry_reason/last_exit_rule 是既有持仓的策略标记；后续加仓、减仓、清仓必须读取这些标记，按原入场策略的时间纪律和卖出规则处理，不能把 B3、B2、趋势回踩、李大霄等不同策略混同。",
        "- 对已有持仓再次输出 BUY 表示加仓/补仓，shares 是本次新增股数而不是目标总股数；只允许顺势确认加仓，不能为了摊低亏损成本而越跌越买，今日新买且T+1锁仓的票原则上不再日内加仓。",
        risk_rule,
        *generic_exit_rules,
        "- 同板块持仓不超过2只（避免集中风险）",
        "- 必须按候选自带的“基准”判断是否达标；未达各自基准只能观察，不能因为裸分接近8就买",
        "- 策略共识只能用于排序，不能突破持仓数、T+1、现金不能为负、交易窗口等执行规则。",
        f"- 当前自适应模式：{adaptive_label}（仓位系数{adaptive_position_mult:g}x，仅作为你决定 shares 的参考）",
        "- 盈亏比过滤：优先选盈亏比≥2:1的票（上涨空间/下跌空间），盈亏比<1.5自动标记风险",
        "- 波动率提示：20日波动>3.5%时应倾向缩小你输出的 shares，波动<1.5%时可酌情提高 shares",
        "- 融资+大宗信号：优先买入融资净买入+大宗溢价的票，谨慎对待融资偿还+大宗折价的票",
    ])


def normalize_strategy_list_update(value: str, *, family: str = "persona") -> str:
    allowed = set(configurable_strategy_option_ids() if family in {"persona", "builtin"} else strategy_ids_for_family(family))
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in str(value or "").replace("，", ",").split(","):
        strategy_id = raw.strip()
        if not strategy_id:
            continue
        if strategy_id in DEPRECATED_STRATEGY_OPTION_IDS:
            continue
        if strategy_id not in allowed:
            raise ValueError(f"未知策略: {strategy_id}")
        if strategy_id not in seen:
            seen.add(strategy_id)
            normalized.append(strategy_id)
        if family in {"persona", "builtin"} and normalized:
            break
    if family in {"persona", "builtin"} and not normalized:
        return BASIC_STRATEGY_GROUP_ID
    return ",".join(normalized)


def enabled_persona_strategy_ids(raw: str | None = None, strategy_source_raw: str | None = None) -> set[str]:
    if preset_text_strategy_active(strategy_source_raw):
        return set()
    if raw is None:
        raw = os.environ.get(PERSONA_STRATEGY_ENV)
    if raw is None:
        raw = default_enabled_persona_strategies_value()
    normalized = normalize_strategy_list_update(raw, family="persona")
    return set(normalized.split(",")) if normalized else set()


def enabled_strategy_ids(
    enabled_persona_raw: str | None = None,
    strategy_source_raw: str | None = None,
    strategy_suite_raw: str | None = None,
) -> set[str]:
    enabled: set[str] = set()
    suite = active_strategy_suite(strategy_suite_raw, strategy_source_raw, enabled_persona_raw)
    # Text strategies intentionally use only the neutral base scanner as their
    # raw candidate pool; the model-provided rules remain the sole decision policy.
    enabled_options = {BASIC_STRATEGY_GROUP_ID if suite == STRATEGY_SUITE_PRESET_TEXT else suite}
    for option_id in enabled_options:
        group = STRATEGY_SUITES.get(option_id)
        if group:
            enabled.update(str(strategy_id) for strategy_id in group.get("strategy_ids") or ())
        elif option_id in STRATEGY_DEFINITIONS:
            enabled.add(option_id)
    return enabled


def enabled_strategy_meta(enabled_persona_raw: str | None = None, strategy_source_raw: str | None = None, strategy_suite_raw: str | None = None) -> dict[str, dict[str, Any]]:
    enabled = enabled_strategy_ids(enabled_persona_raw, strategy_source_raw, strategy_suite_raw)
    return {key: value for key, value in STRATEGY_META.items() if key in enabled}


def enabled_strategy_score_profiles(enabled_persona_raw: str | None = None, strategy_source_raw: str | None = None, strategy_suite_raw: str | None = None) -> dict[str, dict[str, Any]]:
    enabled = enabled_strategy_ids(enabled_persona_raw, strategy_source_raw, strategy_suite_raw)
    return {key: value for key, value in STRATEGY_SCORE_PROFILES.items() if key in enabled}


def classify_strategy_text(text: str) -> str | None:
    """Return a strategy id when text contains a registered id or alias."""
    raw = str(text or "")
    if not raw:
        return None
    lowered = raw.lower()
    for alias in sorted(_ALIAS_TO_STRATEGY, key=len, reverse=True):
        if alias and alias in lowered:
            return _ALIAS_TO_STRATEGY[alias]
    for alias, strategy_id in sorted(_ALIAS_TO_STRATEGY.items(), key=lambda item: len(item[0]), reverse=True):
        if alias and alias in raw:
            return strategy_id
    return None


def strategy_prompt_labels(enabled_ids: set[str] | None = None) -> dict[str, str]:
    selected = set(STRATEGY_DEFINITIONS.keys()) if enabled_ids is None else enabled_ids
    return {
        key: f"{value['label']}（{value['desc']}）"
        for key, value in STRATEGY_DEFINITIONS.items()
        if key in selected
    }


def strategy_settings_options(*, family: str = "persona") -> list[dict[str, Any]]:
    group_options = [
        {
            "id": key,
            "label": value["label"],
            "desc": value["desc"],
            "color": value["color"],
        }
        for key, value in STRATEGY_SUITES.items()
    ] if family == "persona" else []
    strategy_ids = individual_persona_strategy_ids() if family == "persona" else strategy_ids_for_family(family)
    strategy_options = [
        {
            "id": key,
            "label": STRATEGY_DEFINITIONS[key]["label"],
            "desc": STRATEGY_DEFINITIONS[key]["desc"],
            "color": STRATEGY_DEFINITIONS[key]["color"],
        }
        for key in strategy_ids
    ]
    return group_options + strategy_options
