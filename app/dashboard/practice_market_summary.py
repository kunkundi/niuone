"""Generate a durable market recap from prior-US and current A-share scans."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


_WRITE_LOCK = threading.Lock()
SUMMARY_SCHEMA_VERSION = 4
_TONE_LABELS = {
    "offensive": "进攻",
    "balanced": "平衡",
    "neutral": "中性",
    "cautious": "谨慎",
    "defensive": "防守",
}
_ACTION_WORDS = re.compile(r"开仓|买入|卖出|持仓|仓位|止损|止盈|追高|低吸|减仓|清仓|试仓|调仓|加仓|应以|应当|建议|不宜|切忌")


def _record_time(record: dict[str, Any]) -> str:
    return str(record.get("time_text") or record.get("time") or "").strip()


def _record_metadata(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _is_overnight_us_record(record: dict[str, Any]) -> bool:
    identity = f"{record.get('title') or ''}\n{record.get('content') or ''}"
    return "隔夜美股盘面总结" in identity or ("美股概况" in identity and "关键资产" in identity)


def collect_daily_a_share_scans(records: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    """Return every A-share market scan for ``day`` in chronological order."""
    scans: list[dict[str, Any]] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        time_text = _record_time(record)
        content = str(record.get("content") or "").strip()
        if not time_text.startswith(day) or not content or _is_overnight_us_record(record):
            continue
        metadata = _record_metadata(record)
        guidance = metadata.get("decision_guidance")
        if not isinstance(guidance, list):
            guidance = []
        scans.append({
            "source_kind": "a_share_scan",
            "title": str(record.get("title") or record.get("chat_label") or "A股盘面扫描").strip(),
            "time": time_text,
            "content": content,
            "summary": str(metadata.get("summary") or "").strip(),
            "guidance_lines": [str(line).strip() for line in guidance if str(line).strip()][:8],
        })
    scans.sort(key=lambda item: item["time"])
    return scans


def collect_market_replay_sources(records: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    """Combine today's A-share scans with today's prior-US-session summary."""
    sources = collect_daily_a_share_scans(records, day)
    overnight_us: list[dict[str, Any]] = []
    for record in records or []:
        if (
            not isinstance(record, dict)
            or not _record_time(record).startswith(day)
            or not _is_overnight_us_record(record)
        ):
            continue
        metadata = _record_metadata(record)
        guidance = metadata.get("decision_guidance")
        overnight_us.append({
            "source_kind": "overnight_us",
            "title": str(record.get("title") or "隔夜美股盘面总结").strip(),
            "time": _record_time(record),
            "content": str(record.get("content") or "").strip(),
            "summary": str(metadata.get("summary") or "").strip(),
            "guidance_lines": [str(line).strip() for line in guidance if str(line).strip()][:8]
            if isinstance(guidance, list) else [],
        })
    if overnight_us:
        sources.append(max(overnight_us, key=lambda item: item["time"]))
    sources.sort(key=lambda item: item["time"])
    return sources


def _market_only_text(value: Any) -> str:
    clauses = [part.strip() for part in re.split(r"[，；。！？\n]+", str(value or "")) if part.strip()]
    kept = [clause for clause in clauses if not _ACTION_WORDS.search(clause)]
    return "，".join(kept).strip("，") + ("。" if kept else "")


def _summary_line(scan: dict[str, Any]) -> str:
    if scan.get("summary"):
        return _market_only_text(scan["summary"])
    for raw_line in str(scan.get("content") or "").splitlines():
        line = raw_line.strip()
        if line.startswith("💬"):
            return _market_only_text(line.lstrip("💬").strip())
    guidance = scan.get("guidance_lines") or []
    return _market_only_text(guidance[0]) if guidance else ""


def _tone_from_scan(scan: dict[str, Any]) -> str:
    text = "\n".join(scan.get("guidance_lines") or []) or str(scan.get("content") or "")
    level_match = re.search(r"风险级别[：:]\s*([^\n。；;，,]+)", text)
    level = level_match.group(1) if level_match else text
    if any(word in level for word in ("防守", "极弱", "只卖", "暂停")):
        return "defensive"
    if any(word in level for word in ("谨慎", "偏弱", "控仓")):
        return "cautious"
    if any(word in level for word in ("进攻", "积极", "偏强")):
        return "offensive"
    if any(word in level for word in ("平衡", "中性")):
        return "balanced"
    return "neutral"


def source_fingerprint(scans: list[dict[str, Any]]) -> str:
    source = [
        {
            "title": scan.get("title"),
            "time": scan.get("time"),
            "summary": _summary_line(scan),
            "guidance_lines": scan.get("guidance_lines") or [],
        }
        for scan in scans
    ]
    raw = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
        return number if number == number else None
    except (TypeError, ValueError):
        return None


def _named_rows(rows: Any, *, limit: int = 5) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("industry") or "").strip()
        if not name or name.lower() == "nan":
            continue
        row: dict[str, Any] = {"name": name}
        for key in ("key", "price", "pct", "change_pct", "net_flow_yi", "leader", "leader_pct"):
            value = raw.get(key)
            if key in {"key", "leader"}:
                if str(value or "").strip():
                    row[key] = str(value).strip()
                continue
            number = _number(value)
            if number is not None:
                row[key] = round(number, 3 if key == "price" else 2)
        compact.append(row)
        if len(compact) >= limit:
            break
    return compact


def _payload_is_fresh(payload: dict[str, Any]) -> bool:
    return bool(payload) and not payload.get("stale_cache") and not payload.get("error")


def _pct_rank_text(rows: list[dict[str, Any]]) -> str:
    return "、".join(
        f"{row['name']} {float(row.get('pct') or row.get('change_pct') or 0):+.2f}%"
        for row in rows
    ) or "无有效数据"


def _flow_rank_text(rows: list[dict[str, Any]]) -> str:
    return "、".join(
        f"{row['name']} {float(row.get('net_flow_yi') or 0):+.2f}亿"
        for row in rows
    ) or "无有效数据"


def build_realtime_market_snapshot(
    indices_payload: dict[str, Any],
    sectors_payload: dict[str, Any],
    money_flow_payload: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Normalize forced-refresh dashboard channels into one model-ready snapshot."""
    index_keys = {"sh", "sz", "cyb", "kc50"}
    indices = []
    for raw in (indices_payload or {}).get("items") or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("market_type") != "a_index" and str(raw.get("key") or "") not in index_keys:
            continue
        price = _number(raw.get("price"))
        change_pct = _number(raw.get("change_pct"))
        if not price or change_pct is None:
            continue
        indices.append({
            "key": str(raw.get("key") or ""),
            "name": str(raw.get("name") or raw.get("key") or "A股指数"),
            "price": round(price, 3),
            "change_pct": round(change_pct, 2),
            "time": str(raw.get("time") or indices_payload.get("generated_at") or ""),
        })

    flow_fresh = _payload_is_fresh(money_flow_payload or {})
    inflow = _named_rows((money_flow_payload or {}).get("inflow"), limit=6) if flow_fresh else []
    outflow = _named_rows((money_flow_payload or {}).get("outflow"), limit=6) if flow_fresh else []
    inflow = [row for row in inflow if float(row.get("net_flow_yi") or 0) > 0]
    outflow = [row for row in outflow if float(row.get("net_flow_yi") or 0) < 0]
    has_nonzero_flow = any(
        abs(float(row.get("net_flow_yi") or 0)) > 1e-9 for row in [*inflow, *outflow]
    )
    if not has_nonzero_flow:
        inflow, outflow = [], []

    sector_fresh = _payload_is_fresh(sectors_payload or {})
    gain_top = _named_rows(
        (sectors_payload or {}).get("gain_top") or (sectors_payload or {}).get("items"),
        limit=6,
    ) if sector_fresh else []
    loss_top = _named_rows((sectors_payload or {}).get("loss_top"), limit=6) if sector_fresh else []
    sector_source = "板块实时排行"
    if not gain_top and inflow:
        derived = sorted([*inflow, *outflow], key=lambda row: float(row.get("pct") or 0), reverse=True)
        gain_top = derived[:6]
        loss_top = list(reversed(derived[-6:]))
        sector_source = "行业资金流行情"

    missing_channels: list[str] = []
    if not indices or not _payload_is_fresh(indices_payload or {}):
        missing_channels.append("A股实时指数")
    if not gain_top and not loss_top:
        missing_channels.append("行业板块涨跌")
    if not inflow and not outflow:
        missing_channels.append("行业板块资金流")

    errors: list[str] = []
    for label, payload in (
        ("指数", indices_payload or {}),
        ("板块", sectors_payload or {}),
        ("行业资金流", money_flow_payload or {}),
    ):
        if payload.get("stale_cache"):
            errors.append(f"{label}强制刷新失败，仅返回旧缓存")
        elif payload.get("error"):
            errors.append(f"{label}：{payload.get('error')}")

    index_text = "、".join(
        f"{row['name']} {row['price']:.3f}（{row['change_pct']:+.2f}%）" for row in indices
    ) or "无有效数据"
    content_lines = [
        f"抓取时间：{now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"实时核心指数：{index_text}",
        f"行业板块涨幅前列：{_pct_rank_text(gain_top)}",
        f"行业板块跌幅前列：{_pct_rank_text(loss_top)}",
        f"行业主力净流入前列：{_flow_rank_text(inflow)}",
        f"行业主力净流出前列：{_flow_rank_text(outflow)}",
    ]
    if errors:
        content_lines.append("抓取异常：" + "；".join(errors))
    summary = (
        f"点击时核心指数为{index_text}；行业涨幅前列为{_pct_rank_text(gain_top[:3])}；"
        f"行业主力净流入集中在{_flow_rank_text(inflow[:3])}。"
    )
    complete = not missing_channels
    return {
        "source_kind": "realtime_snapshot",
        "title": "手动触发实时盘面快照",
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "content": "\n".join(content_lines),
        "summary": summary,
        "guidance_lines": [],
        "complete": complete,
        "missing_channels": missing_channels,
        "errors": errors,
        "snapshot": {
            "captured_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "indices": indices,
            "sectors": {"source": sector_source, "gain_top": gain_top, "loss_top": loss_top},
            "industry_fund_flow": {"inflow": inflow, "outflow": outflow},
            "source_generated_at": {
                "indices": str((indices_payload or {}).get("generated_at") or ""),
                "sectors": str((sectors_payload or {}).get("generated_at") or ""),
                "money_flow": str((money_flow_payload or {}).get("generated_at") or ""),
            },
        },
    }


def _market_snapshot_without_action_guidance(content: str) -> str:
    try:
        from reports.a_share.grok import remove_original_guidance

        return remove_original_guidance(content)
    except Exception:
        return content


def _model_messages(scans: list[dict[str, Any]], day: str) -> list[dict[str, str]]:
    sections: list[str] = []
    remaining = 60000
    for index, scan in enumerate(scans, 1):
        content = _market_snapshot_without_action_guidance(str(scan.get("content") or ""))
        excerpt = content[: min(18000, remaining)]
        remaining = max(0, remaining - len(excerpt))
        source_kind = scan.get("source_kind")
        if source_kind == "overnight_us":
            source_label = "前一美股交易日总结"
        elif source_kind == "realtime_snapshot":
            source_label = "手动按钮刚抓取的实时A股盘面"
        elif source_kind == "previous_generated_summary":
            source_label = "本按钮上一版盘面总结"
        else:
            source_label = "当日已有A股盘面总结/扫描"
        sections.append(
            f"资料{index}｜{source_label}｜{scan.get('time')}｜{scan.get('title')}\n"
            f"已有摘要：{_summary_line(scan) or '无'}\n"
            f"扫描原文：\n{excerpt}"
        )
    system = (
        "你是牛牛1号的A股日内市场复盘助手。你会收到前一美股交易日总结、今天已有的A股盘面总结/扫描、"
        "本按钮上一版总结（如有），以及点击按钮时刚抓取的实时A股指数、行业板块涨跌和行业资金流。"
        "先把美股总结作为A股开盘前的外部背景，再对照A股实际走势说明哪些风险偏好或板块映射得到验证、弱化或反转。"
        "必须以手动触发实时快照作为最新事实，并与时间上最近的已有A股总结及上一版按钮总结进行对比，"
        "明确指出判断得到延续/强化、出现弱化/反转，或板块资金发生轮动；不得只复述实时榜单。"
        "不得把美股表现写成A股已经发生的事实，也不得仅凭相关性断言因果。"
        "请站在全市场视角总结指数走势、涨跌家数与涨跌停、市场情绪、成交与资金、板块轮动及日内演变。"
        "只做客观盘面复盘，不得输出开仓、买入、卖出、持仓、仓位、止损等操作指引。"
        "区分早盘判断与最新判断，不能编造输入以外的行情、政策、新闻或资金数据。"
        "必须输出严格JSON，不要Markdown、代码块或URL。"
    )
    user = f"""
日期：{day}
复盘资料总数：{len(scans)}（包含历史总结/扫描、上一版总结及手动触发实时快照）

{chr(10).join(sections)}

请输出：
{{
  "tone": "offensive|balanced|neutral|cautious|defensive",
  "tone_label": "进攻|平衡|中性|谨慎|防守",
  "summary": "2到4句中文市场总结，说明指数、情绪、资金和板块从已有总结到实时快照如何变化，不要逐条照抄",
  "comparison_lines": ["2到5条实时快照相对最近已有总结的对比结论，必须明确延续/强化/弱化/反转/轮动中的适用状态"],
  "guidance_lines": ["2到5条纯盘面走势脉络，不得包含任何操作建议"],
  "focus_lines": ["2到5条市场结构信息，例如涨跌广度、成交资金、板块轮动或指数分化"],
  "risk_lines": ["1到4条客观风险现象，不得转化为操作建议"]
}}

再次强调：实时数据必须参与最终结论，comparison_lines 不得为空。输出是全市场走势复盘，不是交易计划；
禁止出现“开仓、买入、卖出、仓位、止损、持仓处理”等指引。
""".strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _realtime_tone(snapshot_source: dict[str, Any] | None, fallback: str) -> str:
    indices = (((snapshot_source or {}).get("snapshot") or {}).get("indices") or [])
    changes = [float(row.get("change_pct")) for row in indices if isinstance(row, dict) and _number(row.get("change_pct")) is not None]
    if not changes:
        return fallback
    average = sum(changes) / len(changes)
    if average >= 0.8:
        return "offensive"
    if average >= 0.2:
        return "balanced"
    if average <= -0.8:
        return "defensive"
    if average <= -0.2:
        return "cautious"
    return "neutral"


def _local_comparison_lines(
    reference: dict[str, Any],
    realtime: dict[str, Any],
) -> list[str]:
    snapshot = realtime.get("snapshot") if isinstance(realtime.get("snapshot"), dict) else {}
    indices = snapshot.get("indices") or []
    sectors = snapshot.get("sectors") if isinstance(snapshot.get("sectors"), dict) else {}
    funds = snapshot.get("industry_fund_flow") if isinstance(snapshot.get("industry_fund_flow"), dict) else {}
    gain_top = [row for row in (sectors.get("gain_top") or []) if isinstance(row, dict)][:3]
    inflow = [row for row in (funds.get("inflow") or []) if isinstance(row, dict)][:3]
    outflow = [row for row in (funds.get("outflow") or []) if isinstance(row, dict)][:3]
    reference_text = "\n".join((
        str(reference.get("summary") or ""),
        str(reference.get("content") or ""),
        "\n".join(reference.get("guidance_lines") or []),
    ))
    reference_label = f"{str(reference.get('time') or '')[11:16]} {reference.get('title') or '最近已有总结'}".strip()
    lines: list[str] = []

    if indices:
        average = sum(float(row.get("change_pct") or 0) for row in indices) / len(indices)
        direction = "整体偏强" if average >= 0.2 else ("整体偏弱" if average <= -0.2 else "整体震荡")
        lines.append(
            f"对比{reference_label}，点击时核心指数平均涨跌幅为{average:+.2f}%，最新指数状态{direction}。"
        )

    strong_names = list(dict.fromkeys(
        str(row.get("name") or "") for row in [*gain_top, *inflow] if str(row.get("name") or "")
    ))
    continued = [name for name in strong_names if name in reference_text]
    if continued:
        lines.append(f"此前总结提及的{'、'.join(continued[:3])}仍在实时领涨或资金净流入前列，相关方向得到延续验证。")
    elif strong_names:
        lines.append(
            f"实时强势与净流入重心转向{'、'.join(strong_names[:4])}，与{reference_label}的突出方向重合有限，板块资金呈现轮动。"
        )

    weakened = [str(row.get("name") or "") for row in outflow if str(row.get("name") or "") in reference_text]
    if weakened:
        lines.append(f"此前总结提及的{'、'.join(weakened[:3])}已进入实时资金净流出前列，原有强度出现弱化。")

    if inflow and outflow:
        inflow_total = sum(max(0.0, float(row.get("net_flow_yi") or 0)) for row in inflow)
        outflow_total = abs(sum(min(0.0, float(row.get("net_flow_yi") or 0)) for row in outflow))
        if inflow_total > outflow_total * 1.15:
            balance = "头部行业净流入强于净流出，资金集中度相对增强"
        elif outflow_total > inflow_total * 1.15:
            balance = "头部行业净流出强于净流入，资金结构相对走弱"
        else:
            balance = "头部行业流入与流出规模接近，资金仍以分化为主"
        lines.append(f"实时行业资金对比显示：{balance}。")
    return lines[:5]


def _local_summary(scans: list[dict[str, Any]], day: str) -> dict[str, Any]:
    a_share_scans = [scan for scan in scans if scan.get("source_kind") == "a_share_scan"]
    overnight_us = next((scan for scan in scans if scan.get("source_kind") == "overnight_us"), None)
    previous_summary = next((scan for scan in reversed(scans) if scan.get("source_kind") == "previous_generated_summary"), None)
    realtime = next((scan for scan in reversed(scans) if scan.get("source_kind") == "realtime_snapshot"), None)
    if not a_share_scans:
        return {
            "tone": "neutral", "tone_label": _TONE_LABELS["neutral"],
            "summary": f"{day}暂无可对比的A股盘面总结。", "comparison_lines": [],
            "trend_lines": [], "structure_lines": [], "risk_lines": [], "model_used": False,
        }
    first_tone = _tone_from_scan(a_share_scans[0])
    historical_tone = _tone_from_scan(a_share_scans[-1])
    latest_tone = _realtime_tone(realtime, historical_tone)
    latest_line = _summary_line(a_share_scans[-1])
    us_prefix = f"前一美股交易日整体呈{_TONE_LABELS[_tone_from_scan(overnight_us)]}基调。" if overnight_us else ""
    if len(a_share_scans) == 1:
        summary = f"{day}已完成1次盘面扫描，当前风险级别为{_TONE_LABELS[latest_tone]}。"
    else:
        summary = (
            f"{day}已汇总{len(a_share_scans)}次A股盘面扫描，风险判断由"
            f"{_TONE_LABELS[first_tone]}演变为{_TONE_LABELS[latest_tone]}。"
        )
    if latest_line:
        summary += latest_line if summary.endswith(("。", "！", "？")) else f" {latest_line}"
    summary = us_prefix + summary
    reference = previous_summary or a_share_scans[-1]
    comparison_lines = _local_comparison_lines(reference, realtime) if realtime else []
    if realtime:
        summary += f"实时快照显示：{_summary_line(realtime)}"
    if comparison_lines:
        summary += comparison_lines[0]
    key_points = []
    for scan in scans:
        clock = str(scan.get("time") or "")[11:16]
        line = _summary_line(scan)
        if line:
            prefix = "前日美股" if scan.get("source_kind") == "overnight_us" else clock
            key_points.append(f"{prefix} {scan.get('title')}：{line}")
    return {
        "tone": latest_tone,
        "tone_label": _TONE_LABELS[latest_tone],
        "summary": summary.strip(),
        "comparison_lines": comparison_lines,
        "trend_lines": key_points[:6],
        "structure_lines": [],
        "risk_lines": [],
        "model_used": False,
    }


def build_daily_market_summary(scans: list[dict[str, Any]], day: str) -> dict[str, Any]:
    result = _local_summary(scans, day)
    model_error = ""
    try:
        from reports.a_share.grok_service import (
            a_share_grok_enabled,
            call_grok_api,
            parse_a_share_grok_content,
        )

        if a_share_grok_enabled():
            parsed = parse_a_share_grok_content(call_grok_api(_model_messages(scans, day)))
            parsed_summary = _market_only_text(parsed.get("summary"))
            if not parsed_summary:
                raise ValueError("盘面汇总模型未返回摘要")
            result = {
                "tone": parsed.get("tone") or result["tone"],
                "tone_label": parsed.get("tone_label") or result["tone_label"],
                "summary": parsed_summary,
                "comparison_lines": [
                    line for line in (_market_only_text(item) for item in parsed.get("comparison_lines") or []) if line
                ][:5] or result.get("comparison_lines", []),
                "trend_lines": [line for line in (_market_only_text(item) for item in parsed.get("guidance_lines") or []) if line][:5],
                "structure_lines": [line for line in (_market_only_text(item) for item in parsed.get("focus_lines") or []) if line][:5],
                "risk_lines": [line for line in (_market_only_text(item) for item in parsed.get("risk_lines") or []) if line][:4],
                "model_used": True,
            }
    except Exception as exc:
        model_error = f"{type(exc).__name__}: {exc}"
    result["model_error"] = model_error
    return result


def load_cached_summary(cache_file: Path, day: str) -> dict[str, Any]:
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        if (
            isinstance(payload, dict)
            and payload.get("date") == day
            and payload.get("schema_version") == SUMMARY_SCHEMA_VERSION
        ):
            return payload
    except (OSError, ValueError):
        pass
    return {"ok": True, "available": False, "date": day}


def summary_status(records: list[dict[str, Any]], cache_file: Path, now: datetime) -> dict[str, Any]:
    day = now.strftime("%Y-%m-%d")
    scans = collect_market_replay_sources(records, day)
    a_share_count = sum(1 for scan in scans if scan.get("source_kind") == "a_share_scan")
    us_summary_count = sum(1 for scan in scans if scan.get("source_kind") == "overnight_us")
    cached = load_cached_summary(cache_file, day)
    fingerprint = source_fingerprint(scans)
    live_snapshot_count = int(bool(cached.get("available") and cached.get("live_snapshot_at")))
    previous_summary_count = int(cached.get("previous_summary_count") or 0) if cached.get("available") else 0
    return {
        **cached,
        "ok": True,
        "date": day,
        "scan_count": a_share_count,
        "us_summary_count": us_summary_count,
        "live_snapshot_count": live_snapshot_count,
        "previous_summary_count": previous_summary_count,
        "source_count": len(scans) + live_snapshot_count + previous_summary_count,
        "stale": bool(cached.get("available") and cached.get("source_fingerprint") != fingerprint),
    }


def _previous_summary_source(cached: dict[str, Any]) -> dict[str, Any] | None:
    if not cached.get("available") or not str(cached.get("summary") or "").strip():
        return None
    content_lines = [str(cached.get("summary") or "").strip()]
    for key in ("comparison_lines", "trend_lines", "structure_lines", "risk_lines"):
        content_lines.extend(str(line).strip() for line in cached.get(key) or [] if str(line).strip())
    return {
        "source_kind": "previous_generated_summary",
        "title": "手动生成的上一版今日盘面总结",
        "time": str(cached.get("generated_at") or ""),
        "content": "\n".join(content_lines),
        "summary": str(cached.get("summary") or "").strip(),
        "guidance_lines": [],
    }


def generate_and_store_summary(
    records: list[dict[str, Any]],
    cache_file: Path,
    now: datetime,
    *,
    realtime_snapshot_provider: Callable[[datetime], dict[str, Any]] | None = None,
    require_realtime: bool = False,
) -> dict[str, Any]:
    day = now.strftime("%Y-%m-%d")
    historical_sources = collect_market_replay_sources(records, day)
    a_share_count = sum(1 for scan in historical_sources if scan.get("source_kind") == "a_share_scan")
    us_summary_count = sum(1 for scan in historical_sources if scan.get("source_kind") == "overnight_us")
    if not a_share_count:
        return {
            "ok": False,
            "available": False,
            "date": day,
            "scan_count": 0,
            "us_summary_count": us_summary_count,
            "source_count": len(historical_sources),
            "error": "今日暂无可汇总的A股盘面扫描",
        }

    realtime_source: dict[str, Any] | None = None
    if realtime_snapshot_provider is not None:
        try:
            candidate = realtime_snapshot_provider(now)
            if isinstance(candidate, dict):
                realtime_source = candidate
            else:
                raise TypeError(f"实时盘面抓取返回 {type(candidate).__name__}")
        except Exception as exc:
            if require_realtime:
                return {
                    "ok": False,
                    "available": False,
                    "date": day,
                    "scan_count": a_share_count,
                    "us_summary_count": us_summary_count,
                    "source_count": len(historical_sources),
                    "error": f"实时盘面抓取失败：{type(exc).__name__}: {exc}",
                }
    if require_realtime and (not realtime_source or not realtime_source.get("complete")):
        missing = "、".join((realtime_source or {}).get("missing_channels") or []) or "实时行情"
        detail = "；".join((realtime_source or {}).get("errors") or [])
        return {
            "ok": False,
            "available": False,
            "date": day,
            "scan_count": a_share_count,
            "us_summary_count": us_summary_count,
            "source_count": len(historical_sources),
            "error": f"实时盘面抓取不完整：缺少{missing}" + (f"（{detail}）" if detail else ""),
        }

    cached_before = load_cached_summary(cache_file, day)
    previous_source = _previous_summary_source(cached_before)
    sources = list(historical_sources)
    if previous_source:
        sources.append(previous_source)
    if realtime_source:
        sources.append(realtime_source)
    result = build_daily_market_summary(sources, day)
    live_snapshot_at = str((realtime_source or {}).get("time") or "")
    payload = {
        "ok": True,
        "available": True,
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "date": day,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "scan_count": a_share_count,
        "us_summary_count": us_summary_count,
        "live_snapshot_count": int(bool(realtime_source)),
        "previous_summary_count": int(bool(previous_source)),
        "source_count": len(sources),
        "source_fingerprint": source_fingerprint(historical_sources),
        "input_fingerprint": source_fingerprint(sources),
        "live_snapshot_at": live_snapshot_at,
        "realtime_snapshot": (realtime_source or {}).get("snapshot") or {},
        "sources": [
            {"title": scan.get("title"), "time": scan.get("time"), "source_kind": scan.get("source_kind")}
            for scan in sources
        ],
        **result,
    }
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = cache_file.with_name(f".{cache_file.name}.{os.getpid()}.tmp")
    with _WRITE_LOCK:
        tmp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_file.replace(cache_file)
    return payload
