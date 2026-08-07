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
2. 将优化后的规则作为本轮唯一的新开仓策略，用它筛选中性行情事实候选并决定买卖；其他策略不得影响本轮新增仓判断，中性候选排序不构成买点或其他战法意见。
3. 若用户规则含糊、互相冲突或突破A股交易/账户风控硬约束，按更保守的解释执行；无法确认则HOLD。
4. 返回JSON的summary和reason里简短体现预设文字策略的核心规则。"""


def build_position_exit_prompt_section(
    position_strategy_ids: set[str],
    *,
    b3_exit_hhmm: str,
    time_exit_hhmm: str,
) -> str:
    """Build exit-only rules for the strategies actually held in the portfolio."""
    known_ids = {
        strategy_id
        for strategy_id in position_strategy_ids
        if strategy_id in STRATEGY_DEFINITIONS
        and strategy_id != STRATEGY_SOURCE_PRESET_TEXT
    }
    if not known_ids:
        return "当前没有带有效 strategy_mark 的持仓，无需加载历史持仓退出规则。"

    sections: list[str] = []
    zettaranc_ids = {
        strategy_id
        for strategy_id in known_ids
        if STRATEGY_DEFINITIONS.get(strategy_id, {}).get("persona") == "zettaranc"
    }
    if zettaranc_ids:
        labels = "、".join(
            str(STRATEGY_DEFINITIONS[strategy_id].get("label") or strategy_id)
            for strategy_id in sorted(
                zettaranc_ids,
                key=lambda item: int(STRATEGY_DEFINITIONS[item].get("display_order", 999)),
            )
        )
        sections.append(f"""Z哥历史持仓退出纪律（仅适用于 strategy_mark={labels}）：
- 少妇B1使用N型上移结构最近前低；B2使用前置B1低点；B3使用B3当日低点（缺失时用B2大阳线中位）；超级B1使用放量洗盘阴线低点。
- 少妇B1买入后至少观察3个交易日；结构止损、白线死叉黄线、白线连续两日破位和高置信度出货属于硬退出。其余盈转亏、BBI、评分、回撤和时间效率信号属于软退出，开盘前30分钟不执行，需连续确认；行业净流入或缩量回调支持继续HOLD，行业净流出叠加放量下跌才加速减半。
- 少妇B1的模型SELL仅作建议，不直接成交；实际卖出由本地持仓状态机执行。缺失、失败或过期的行业资金与预测量能保持中性，不得作为卖出理由。
- 保留防卖飞5分评分、S1/S2/S3逃顶、出货五式、BBI/白线两日破位、白线死叉黄线、峰值回撤与ATR吊灯保护。
- B3仅在{b3_exit_hhmm}执行次日开盘不涨离场；B2和超级B1仅在{time_exit_hhmm}执行相应时间窗退出；必须服从T+1可卖数量。""")

    sector_tide_ids = {
        strategy_id
        for strategy_id in known_ids
        if STRATEGY_DEFINITIONS.get(strategy_id, {}).get("persona") == "sector_tide"
    }
    if sector_tide_ids:
        labels = "、".join(
            str(STRATEGY_DEFINITIONS[strategy_id].get("label") or strategy_id)
            for strategy_id in sorted(
                sector_tide_ids,
                key=lambda item: int(STRATEGY_DEFINITIONS[item].get("display_order", 999)),
            )
        )
        sections.append(f"""板块潮汐历史持仓退出纪律（仅适用于 strategy_mark={labels}）：
- 跌破入场结构止损退出；行业分数低于55连续两次退出；市场复合风险硬停止且行业转弱时退出。
- 主线5日、轮动3日、修复T+2未延续时退出；达到2R先减半，余仓按峰值减2ATR跟踪。
- 当前激活策略发生变化不得改写这些持仓的入场策略、止损锚点或退出时间窗。""")

    niuone_ids = {
        strategy_id
        for strategy_id in known_ids
        if STRATEGY_DEFINITIONS.get(strategy_id, {}).get("persona") == "niuone"
    }
    if niuone_ids:
        labels = "、".join(
            str(STRATEGY_DEFINITIONS[strategy_id].get("label") or strategy_id)
            for strategy_id in sorted(
                niuone_ids,
                key=lambda item: int(STRATEGY_DEFINITIONS[item].get("display_order", 999)),
            )
        )
        sections.append(f"""牛牛战法历史持仓退出纪律（仅适用于 strategy_mark={labels}）：
- 跌破入场结构止损退出；试仓所属题材首次确认fading/inactive或分数低于55即退出；成熟路径连续两个交易日跌出强势行业前三龙头梯队时换出，主题连续两次转弱时退出；市场硬停止且主线转弱时退出。
- 领涨5日、转强3日、启动T+2或试仓3日未延续时退出；试仓若首次建仓处于进攻/修复/防守行情，盘中达到0.75R先减仓50%，轮动入场试仓和成熟路径仍在1R减仓45%；余仓止损抬到成本线并按峰值减2ATR跟踪。
- 主线识别允许结果为“无主线”；当前激活策略发生变化不得改写这些持仓的止损、主题状态或退出时间窗。""")

    covered = zettaranc_ids | sector_tide_ids | niuone_ids
    other_ids = known_ids - covered
    if other_ids:
        labels = "、".join(
            str(STRATEGY_DEFINITIONS[strategy_id].get("label") or strategy_id)
            for strategy_id in sorted(
                other_ids,
                key=lambda item: int(STRATEGY_DEFINITIONS[item].get("display_order", 999)),
            )
        )
        sections.append(
            f"其他历史持仓退出纪律（strategy_mark={labels}）："
            "按持仓保存的时间纪律、结构止损、BBI/趋势失效、峰值回撤和ATR保护执行；"
            "不得套用当前新开仓策略重写原入场逻辑。"
        )
    return "\n\n".join(sections)


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
        and strategy_id != STRATEGY_SOURCE_PRESET_TEXT
    )
    persona_strategy_lines = []
    for strategy_id, definition in sorted(
        STRATEGY_DEFINITIONS.items(),
        key=lambda item: int(item[1].get("display_order", 999)),
    ):
        if (
            definition.get("family") != "persona"
            or definition.get("persona") in {"zettaranc", "sector_tide", "niuone"}
            or strategy_id not in active_strategy_ids
        ):
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
3. 少妇B1：交易级B1按J≤-10执行；J≤12但未到负值只观察。必须缩量、N型上移、黄线/BBI附近、上方压力不重；买入后至少观察3个交易日，结构未坏优先拿住，普通转弱只形成连续确认的软退出
4. 超级B1：洗盘反转小仓，只赌一次；放量破位后缩量企稳、J仍负、止损空间可控才考虑，未兑现到窗口日{time_exit_hhmm}尾盘走
5. 行业资金优先：复用资金流动页“今日主力净额”榜单；所属行业进入净流入前十时，第1名加1.50分、之后每名递减0.15分、第10名加0.15分；榜单失败、过期、行业缺失或无法唯一匹配时不加不减

Z哥卖出风控（属于Z哥体系）：
- 仓位硬纪律：Z哥单票不得超过对应战法上限（最高10%），账户总仓位不得超过80%，至少保留20%现金；不得以高确定性为由突破
- 少妇B1用N型上移结构最近前低；B2用前置B1低点；B3用B3当天低点（缺失时用B2大阳线中位）；超级B1用放量洗盘阴线低点
- 止盈按卤煮形态执行，不使用固定8%减半或12%清仓；同时保留防卖飞5分评分、S1/S2/S3逃顶、出货五式、BBI/白线两日破位、白线死叉黄线、峰值回撤/ATR吊灯保护
- 少妇B1软退出在开盘前30分钟不执行；行业主力净流入或缩量回调否决普通软卖出，行业净流出与预测放量下跌共振时可提前减半；行情数据缺失或过期保持中性
- 少妇B1的模型SELL不得直接成交，必须交由本地持仓状态机复核和执行
- B3仅在{b3_exit_hhmm}做开盘离场检查，B2/超级B1仅在{time_exit_hhmm}做尾盘离场检查""" if zettaranc_enabled else ""
    base_strategy_enabled = any(
        STRATEGY_DEFINITIONS.get(strategy_id, {}).get("family") == "local"
        for strategy_id in active_strategy_ids
    )
    base_strategy_section = """基础策略：
1. 突破确认：优先看有效突破和回踩不破，再作为确认仓处理
2. 趋势回踩：强趋势股回踩BBI/EMA不破，按低吸仓处理""" if base_strategy_enabled else ""
    sector_tide_enabled = any(
        STRATEGY_DEFINITIONS.get(strategy_id, {}).get("persona") == "sector_tide"
        for strategy_id in active_strategy_ids
    )
    sector_tide_strategy_section = """板块潮汐（市场→行业→个股，三层硬门控）：
1. 先服从市场状态：进攻/轮动/冰点修复总仓动态上限为45%/30%/15%；防守状态或复合风险硬停止时禁止新开仓，不能用个股高分抵消。
2. 主线领航：仅做进攻/轮动行情中的领先行业，个股必须处行业前20%，只买放量突破或EMA20附近缩量回踩；8%仅为单票绝对上限，实际仓位由风险预算计算。
3. 轮动初升：仅做排名加速度≥15且进入改善潮位的行业，个股必须处行业前30%，单日涨幅>7%或距EMA20>1.5ATR不追；6%仅为单票绝对上限。
4. 冰点修复：仅在防守解除后的修复状态做率先转强行业，重新站回EMA20或突破修复高点才买；4%仅为单票绝对上限，当日只建观察仓，次日确认后才可加仓。
5. 动态风险预算：进攻/轮动/修复的单笔权益风险≤0.30%/0.20%/0.10%，策略内组合未实现止损风险≤1.50%/0.80%/0.30%，单行业风险≤0.60%/0.40%/0.20%，行业敞口≤12%/10%/6%，同一行业最多2只。
6. 有效损失距离=结构止损距离+max(近60日向下跳空P95, 0.5ATR占比)+0.20%费用滑点；动态单票上限=min(注册绝对上限, 单笔风险预算÷有效损失距离)。
7. 退出服从潮退：行业分数<55连续两次退出；市场复合风险硬停止且行业转弱时减仓/退出。主线5日、轮动3日、修复T+2未延续退出。盈利达到2R先减半，余仓按峰值-2ATR跟踪，不使用固定8%/12%止盈。
8. 行业资金流缺失时只允许使用量能参与度替代，并明确标记数据源；不得把缺失资金流当成净流入。
9. 外部确认只做限幅覆盖：读取已完成的隔夜美股盘面及明确A股行业映射，并对首轮前5候选读取近3日个股消息面。正向外盘/消息不能把落后行业、追高或硬过滤候选变成买点；隔夜防守、负行业映射和明确利空必须降权并写入风险。""" if sector_tide_enabled else ""
    niuone_enabled = any(
        STRATEGY_DEFINITIONS.get(strategy_id, {}).get("persona") == "niuone"
        for strategy_id in active_strategy_ids
    )
    niuone_strategy_section = """牛牛战法（主线酝酿→主线主升→主线高潮→主线分歧→主线退幕）：
1. 主线不是涨幅榜第一名：必须由多只强势股在20/5日相对强度、成交参与、趋势和新高上共同确认；单只股票独强会触发集中度惩罚，不能确认主线。
2. 阶段与动作分离：酝酿候选阶段及未跨日延续的启动阶段归入酝酿，跨日延续的启动阶段及普通强度主线阶段归入主升，确认主线且题材分数达到78为高潮，分歧阶段归入分歧，退潮阶段归入退幕；高潮仍允许满足领涨或企稳转强条件的核心股开仓，分歧只观察核心股调整后转强或减仓，持续回落不触发买点，退幕只退出。日内强势不直接升级主线。
3. 牛牛领涨：在未触发复合风险硬停止的进攻/轮动/修复/防守行情中，只做跨交易日已确认主线，主升、高潮和有效分歧阶段均可参与；个股必须处于该行业strong_score前三且仍为强势股，第一名优先，第一名涨停或无有效买点时可顺延，只买有效突破或首次EMA20缩量回踩。突破路径按收盘价超过前20日高点的ATR距离复核并以突破位下方0.5ATR为止损，回踩路径仍按距EMA20复核；不设固定单日涨幅上限，实际执行价达到涨停才拒绝，进攻/轮动/修复或防守的对应锚点距离上限为1.5/1.25/1ATR，30%是单票绝对上限。
4. 牛牛转强（兼容旧称承接/回踩）：主题仍处于主线阶段或分歧阶段、尚未退幕且分数≥70时，高潮和分歧均可观察行业前三核心股在EMA20附近企稳后重新收复；持续回落不触发买点。不设固定单日涨幅上限，实际执行价达到涨停才拒绝；进攻行情距EMA20上限为1.25ATR，轮动/修复为1ATR，25%是单票绝对上限。
5. 牛牛试仓（基于日线V型反转，兼容旧称反转）：在未触发复合风险硬停止的酝酿候选或启动阶段题材中识别最近30根日K内的V型趋势；防守行情仍可按最低风险档开仓。酝酿候选题材里已成为强势股的名称必须等待牛牛启动确认，主线或分歧成熟题材不得继续以试仓身份开仓。左侧回落至少5个交易日且跌幅≥8%，阶段低点距今3～15个交易日，右侧反弹≥6%、收复左侧跌幅须在60%（含）至200%（不含）之间，上涨交易日占比至少2/3并确认右侧继续抬高；达到200%后不再按早期试仓。题材还须至少有6只强势股，或酝酿状态已连续3个交易日。以右侧最近3根日K低点为止损且止损距离不得超过6%/2ATR，距EMA20不得超过1.5ATR；不设固定单日涨幅上限，实际执行价达到涨停才拒绝。每天最多保留评分最高的2只，按早期试仓风险预算参与，单票绝对上限6.25%。
6. 牛牛启动：主题必须处于跨交易日延续、但尚未正式确认为主线的启动阶段；已确认主线阶段必须改走牛牛领涨，不得再用低门槛启动身份参与。至少两只强势股保持共振，只允许行业前三龙头梯队突破/收复时建立观察仓。突破路径按超过前20日高点的ATR距离复核并以突破位下方0.5ATR为止损，收复路径仍按距EMA20复核；不设固定单日涨幅上限，实际执行价达到涨停才拒绝，对应锚点距离仍不得超过1.5ATR，15%是单票绝对上限。启动仍是观察仓。试仓或启动持仓只有浮盈在2%～12%、生命周期仍为主升且个股保持强势领涨梯队时才分级加仓：启动主线跨日延续先向10%上限加一次，主线完全确认后再向20%上限加一次；实际仓位仍取风险预算和阶段上限的较小值，分歧/高潮/退幕不加仓。
7. 进攻/轮动/修复/防守的确认路径单笔权益风险≤1.50%/1.00%/0.60%/0.30%、主题风险≤3.00%/2.00%/1.20%/0.60%、主题敞口≤55%/40%/25%/12%；牛牛试仓仅≤0.35%/0.30%/0.25%/0.15%、主题风险≤0.70%/0.60%/0.50%/0.30%、主题敞口≤12%/10%/8%/5%。策略内组合未实现止损风险≤4.50%/3.00%/1.80%/0.90%，总仓≤70%/55%/35%/20%，同一主题最多2只、策略同时最多持有5只；防守允许开仓，复合风险硬停止才禁止新仓。
8. 结构止损硬上限随行情状态为进攻10%/2.5ATR、轮动8%/2ATR、修复或防守6%/1.5ATR；牛牛试仓按右侧确认低点复核且上限为6%/2ATR。有效损失距离还须加入跳空与费用滑点，执行层按风险预算反推仓位，不能自动缩量绕过超限shares。
9. 龙虎榜和近3日消息只作限幅确认：正面消息不能制造主线或把追高变成买点，明确利空必须降权；行业暂作为可审计的主题代理，不能把名称相近当作确定概念归因。
10. 退出服从生命周期：试仓所属题材首次进入退幕即退出，3个交易日未延续右侧趋势也退出；其他路径在连续两个交易日跌出行业前三龙头梯队时换出。所有路径继续执行主线转弱和结构止损；主线进入高潮且持仓不亏时先减仓1/3，试仓若首次建仓处于进攻/修复/防守行情，盘中达到0.75R先减仓50%，轮动试仓和成熟路径仍在1R减仓45%；余仓止损抬到成本线并按峰值减2ATR跟踪。""" if niuone_enabled else ""
    if strategy_suite == STRATEGY_SOURCE_PRESET_TEXT:
        persona_strategy_section = ""
    else:
        persona_strategy_section = "\n".join(persona_strategy_lines)
    active_strategy_section = next(
        (
            section
            for section in (
                preset_strategy_section,
                niuone_strategy_section,
                sector_tide_strategy_section,
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
        "sector_tide_strategy_section": sector_tide_strategy_section,
        "niuone_strategy_section": niuone_strategy_section,
        "persona_strategy_section": persona_strategy_section,
        "preset_strategy_section": preset_strategy_section,
    }
