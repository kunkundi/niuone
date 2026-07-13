"""Pure prompt fragments derived from the active strategy configuration."""
from __future__ import annotations

from typing import Any

from .registry import (
    STRATEGY_SUITES,
    STRATEGY_DEFINITIONS,
    STRATEGY_POSITION_LIMIT_PCT,
    STRATEGY_SOURCE_PRESET_TEXT,
    strategy_prompt_labels,
)


def format_preset_strategy_section(source: str, preset_text: str) -> str:
    if source != STRATEGY_SOURCE_PRESET_TEXT:
        return ""
    if not preset_text:
        return (
            "预设文字策略（当前激活）：未填写预设文字。"
            "本轮不得新开仓，只能按既有持仓风控卖出或HOLD。"
        )
    return f"""预设文字策略（当前激活）：
用户原文：
{preset_text}

执行方式：
1. 先将用户原文分析并优化成清晰的选股条件、买入触发、卖出/止损止盈、仓位和时间纪律。
2. 将优化后的规则作为本轮唯一的新开仓策略，用它筛选候选股并决定买卖；其他策略不得影响本轮新增仓判断，基础扫描结果只作为原始候选池。
3. 若用户规则含糊、互相冲突或突破A股交易/账户风控硬约束，按更保守的解释执行；无法确认则HOLD。
4. 返回JSON的summary和reason里简短体现预设文字策略的核心规则。"""


def build_strategy_prompt_sections(
    strategy_suite: str,
    preset_strategy_text: str,
    active_strategy_ids: set[str],
    *,
    b3_exit_hhmm: str,
    time_exit_hhmm: str,
) -> dict[str, Any]:
    """Build strategy-only decision prompt sections without reading runtime state."""
    preset_strategy_section = format_preset_strategy_section(strategy_suite, preset_strategy_text)
    suite = STRATEGY_SUITES.get(strategy_suite) or {}
    strategy_source_label = (
        "预设文字策略"
        if strategy_suite == STRATEGY_SOURCE_PRESET_TEXT
        else f"{suite.get('label') or strategy_suite}（独立策略）"
    )
    strategy_labels = strategy_prompt_labels(active_strategy_ids)
    position_limit_desc = "、".join(
        f"{strategy_labels.get(strategy_id, strategy_id).split('（', 1)[0]}≤{limit:g}%"
        for strategy_id, limit in sorted(
            STRATEGY_POSITION_LIMIT_PCT.items(),
            key=lambda item: int(STRATEGY_DEFINITIONS.get(item[0], {}).get("display_order", 999)),
        )
        if strategy_id in active_strategy_ids
    )
    persona_strategy_lines = []
    for strategy_id, definition in sorted(
        STRATEGY_DEFINITIONS.items(),
        key=lambda item: int(item[1].get("display_order", 999)),
    ):
        if definition.get("family") != "persona" or definition.get("persona") == "zettaranc" or strategy_id not in active_strategy_ids:
            continue
        profile = definition.get("profile") or {}
        heuristics = profile.get("decision_heuristics") or []
        heuristic_text = "；纪律：" + "；".join(str(item) for item in heuristics) if heuristics else ""
        persona_strategy_lines.append(
            f"- {definition.get('label')} — {definition.get('desc')}；定位：{profile.get('score_basis', '-')}{heuristic_text}"
        )
    zettaranc_enabled = any(
        STRATEGY_DEFINITIONS.get(strategy_id, {}).get("persona") == "zettaranc"
        for strategy_id in active_strategy_ids
    )
    zettaranc_strategy_section = f"""Z哥评分基准（永不套牢优先）：
1. B3中继：确定性最高但盈亏比最低，只做贴近B2、振幅小、J不过热的箭在弦上，T+1 {b3_exit_hhmm}开盘不涨走
2. B2确认：必须放量长阳、一阳穿多线、J<55、B1后3日内；偏滞后或离BBI远就是追高，不买；T+2 {time_exit_hhmm}尾盘不延续走
3. 少妇B1：交易级B1按J≤-10执行；J≤12但未到负值只观察。必须缩量、N型上移、黄线/BBI附近、上方压力不重；3天不涨走
4. 超级B1：洗盘反转小仓，只赌一次；放量破位后缩量企稳、J仍负、止损空间可控才考虑，未兑现到窗口日{time_exit_hhmm}尾盘走

Z哥卖出风控（属于Z哥体系）：
- 仓位硬纪律：Z哥单票不得超过对应战法上限（最高10%），账户总仓位不得超过80%，至少保留20%现金；不得以高确定性为由突破
- 少妇B1用N型上移结构最近前低；B2用前置B1低点；B3用B3当天低点（缺失时用B2大阳线中位）；超级B1用放量洗盘阴线低点
- 止盈按卤煮形态执行，不使用固定8%减半或12%清仓；同时保留防卖飞5分评分、S1/S2/S3逃顶、出货五式、BBI/白线两日破位、白线死叉黄线、峰值回撤/ATR吊灯保护
- B3仅在{b3_exit_hhmm}做开盘离场检查，B2/超级B1仅在{time_exit_hhmm}做尾盘离场检查""" if zettaranc_enabled else ""
    base_strategy_enabled = any(
        STRATEGY_DEFINITIONS.get(strategy_id, {}).get("family") == "local"
        for strategy_id in active_strategy_ids
    )
    base_strategy_section = """基础策略：
1. 突破确认：优先看有效突破和回踩不破，再作为确认仓处理
2. 趋势回踩：强趋势股回踩BBI/EMA不破，按低吸仓处理""" if base_strategy_enabled else ""
    if strategy_suite == STRATEGY_SOURCE_PRESET_TEXT:
        persona_strategy_section = ""
    else:
        persona_strategy_section = "\n".join(persona_strategy_lines)
    active_strategy_section = next(
        (
            section
            for section in (
                preset_strategy_section,
                zettaranc_strategy_section,
                base_strategy_section,
                persona_strategy_section,
            )
            if section
        ),
        "当前策略没有可用规则，本轮不得新开仓。",
    )

    return {
        "strategy_source_label": strategy_source_label,
        "active_strategy_section": active_strategy_section,
        "strategy_labels": strategy_labels,
        "position_limit_desc": position_limit_desc,
        "zettaranc_strategy_section": zettaranc_strategy_section,
        "base_strategy_section": base_strategy_section,
        "persona_strategy_section": persona_strategy_section,
        "preset_strategy_section": preset_strategy_section,
    }
