"""Pure prompt, parsing, and rendering logic for Grok-assisted A-share reports."""
from __future__ import annotations

import json
import re
from typing import Any


def build_messages(local_report: str, *, title: str) -> list[dict[str, str]]:
    title_text = str(title or "")
    if "盘后" in title_text:
        target_guidance = "次日盘前指引"
        timing_requirement = "这是一份盘后报告，guidance_lines 必须写成次日盘前可执行计划，覆盖竞价确认、开盘15分钟承接、仓位节奏、卖出风控；不要写今天剩余交易时段。"
    elif "午盘" in title_text:
        target_guidance = "午后买卖指引"
        timing_requirement = "这是一份午盘报告，guidance_lines 必须写成午后交易计划，覆盖主线延续、13:00后承接、仓位节奏和卖出风控。"
    elif "竞价" in title_text or "盘前" in title_text:
        target_guidance = "盘前买卖指引"
        timing_requirement = "这是一份盘前/竞价报告，guidance_lines 必须写成今日开盘后的执行计划，覆盖开盘确认、上午节奏和卖出风控。"
    else:
        target_guidance = "买卖指引"
        timing_requirement = "guidance_lines 必须结合报告标题判断是盘中、盘后还是盘前，并写成对应交易时段的执行计划。"
    system = (
        "你是牛牛1号的A股盘面监控策略分析师。"
        "你会收到一份由本地规则生成的A股盘面快照，可能包含涨跌家数、涨跌停、成交额、竞价成交额、开盘强弱、封单、资金流、热门板块和强势个股。"
        f"你的任务是基于这些已给数据，补强盘面总结和{target_guidance}。"
        "不要编造未给出的新闻、政策、公司事件、资金数据或实时行情；如果数据不足，必须明确保守处理。"
        "必须输出严格JSON，不要Markdown，不要代码块，不要URL。"
    )
    user = f"""
报告标题：{title}

本地规则盘面快照：
{local_report}

请生成 JSON，schema 必须是：
{{
  "tone": "offensive|balanced|neutral|cautious|defensive",
  "tone_label": "进攻|平衡|中性|谨慎|防守",
  "summary": "一句完整中文盘面总结，必须基于输入快照",
  "guidance_lines": [
    "风险级别：进攻/平衡/中性/谨慎/防守",
    "开仓节奏：具体说明今天剩余交易时段或次日的仓位节奏",
    "买入指引：具体说明只看哪些板块/形态/确认条件",
    "卖出/风控：具体说明弱仓、冲高回落、破位等处理方式"
  ],
  "focus_lines": ["1到4条观察重点，可为空"],
  "risk_lines": ["1到4条风险提醒，可为空"]
}}

要求：
- guidance_lines 返回 4 到 7 条，短句但可执行。
- {timing_requirement}
- 不要输出收益承诺，不要建议满仓，不要说“必涨/确定”。
- 如果涨少跌多、跌停不弱、竞价低开较多、竞价成交额断档或资金流分散，买入节奏必须收紧。
- 如果市场明显强，仍要强调只做板块联动、回封、回踩不破或右侧确认。
""".strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def strip_json_fence(content: str) -> str:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    if not text.startswith("{"):
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            text = match.group(0)
    return text


def parse_content(content: str) -> dict[str, Any]:
    payload = json.loads(strip_json_fence(content))
    if not isinstance(payload, dict):
        raise ValueError("A-share Grok summary JSON must be an object")
    tone = str(payload.get("tone") or "neutral").strip()
    if tone not in {"offensive", "balanced", "neutral", "cautious", "defensive"}:
        tone = "neutral"
    tone_labels = {
        "offensive": "进攻", "balanced": "平衡", "neutral": "中性",
        "cautious": "谨慎", "defensive": "防守",
    }

    def clean_lines(value: Any, limit: int) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(line).strip().lstrip("·- ").strip() for line in value if str(line).strip()][:limit]

    return {
        "tone": tone,
        "tone_label": str(payload.get("tone_label") or tone_labels[tone]).strip() or tone_labels[tone],
        "summary": str(payload.get("summary") or "").strip(),
        "comparison_lines": clean_lines(payload.get("comparison_lines"), 5),
        "guidance_lines": clean_lines(payload.get("guidance_lines"), 8),
        "focus_lines": clean_lines(payload.get("focus_lines"), 4),
        "risk_lines": clean_lines(payload.get("risk_lines"), 4),
    }


def remove_original_guidance(local_report: str) -> str:
    out: list[str] = []
    in_guidance = False
    for line in str(local_report or "").splitlines():
        clean = line.strip()
        if any(key in clean for key in ("今日买卖指引", "午后买卖指引", "次日买卖计划", "次日盘前指引", "盘前买卖指引")):
            in_guidance = True
            continue
        if in_guidance and clean.startswith(("📊", "🔥", "💰", "⚡", "📈", "👀", "📌", "🧭", "⚠️", "🌡️", "💡")) and "**" in clean:
            in_guidance = False
        if not in_guidance:
            out.append(line)
    text = "\n".join(out).strip()
    text = re.sub(r"^牛牛大王，[^。\n]*来了：\s*", "", text).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def render_report(local_report: str, parsed: dict[str, Any], *, title: str, model: str) -> str:
    summary = parsed.get("summary") or "Grok 已参与盘面复核，但未返回摘要。"
    tone_label = parsed.get("tone_label") or "中性"
    guidance = [str(item).strip().lstrip("·- ").strip() for item in parsed.get("guidance_lines") or [] if str(item).strip()]
    focus = [str(item).strip().lstrip("·- ").strip() for item in parsed.get("focus_lines") or [] if str(item).strip()]
    risks = [str(item).strip().lstrip("·- ").strip() for item in parsed.get("risk_lines") or [] if str(item).strip()]
    if not any(line.startswith("风险级别") for line in guidance):
        guidance.insert(0, f"风险级别：{tone_label}")
    title_text = str(title or "")
    guidance_title = "次日盘前指引" if "盘后" in title_text else ("午后买卖指引" if "午盘" in title_text else "今日买卖指引")
    lines = [
        f"牛牛大王，{title}来了：", "", "🤖 **模型盘面总结**", f"生成模型 `{model}`",
        f"💬 {summary}", "", f"🎯 **{guidance_title}**",
    ]
    lines.extend(f"· {line}" for line in guidance[:8])
    if focus:
        lines.extend(["", "👀 **观察重点**"])
        lines.extend(f"· {line}" for line in focus[:4])
    if risks:
        lines.extend(["", "⚠️ **模型风险提醒**"])
        lines.extend(f"· {line}" for line in risks[:4])
    snapshot = remove_original_guidance(local_report)
    if snapshot:
        lines.extend(["", "🧾 **本地规则快照**", snapshot])
    return "\n".join(lines).strip()
