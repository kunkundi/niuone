"""Generate a durable market recap from prior-US and current A-share scans."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


_WRITE_LOCK = threading.Lock()
SUMMARY_SCHEMA_VERSION = 3
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
        source_label = "前一美股交易日总结" if scan.get("source_kind") == "overnight_us" else "当日A股扫描"
        sections.append(
            f"资料{index}｜{source_label}｜{scan.get('time')}｜{scan.get('title')}\n"
            f"已有摘要：{_summary_line(scan) or '无'}\n"
            f"扫描原文：\n{excerpt}"
        )
    system = (
        "你是牛牛1号的A股日内市场复盘助手。你会收到前一美股交易日总结，以及今天按时间排列的全部A股盘面扫描结果。"
        "先把美股总结作为A股开盘前的外部背景，再对照A股实际走势说明哪些风险偏好或板块映射得到验证、弱化或反转。"
        "不得把美股表现写成A股已经发生的事实，也不得仅凭相关性断言因果。"
        "请站在全市场视角总结指数走势、涨跌家数与涨跌停、市场情绪、成交与资金、板块轮动及日内演变。"
        "只做客观盘面复盘，不得输出开仓、买入、卖出、持仓、仓位、止损等操作指引。"
        "区分早盘判断与最新判断，不能编造输入以外的行情、政策、新闻或资金数据。"
        "必须输出严格JSON，不要Markdown、代码块或URL。"
    )
    user = f"""
日期：{day}
复盘资料总数：{len(scans)}（包含前一美股交易日总结和当日A股扫描）

{chr(10).join(sections)}

请输出：
{{
  "tone": "offensive|balanced|neutral|cautious|defensive",
  "tone_label": "进攻|平衡|中性|谨慎|防守",
  "summary": "2到4句中文市场总结，说明指数、情绪、资金和板块从早到晚如何变化，不要逐条照抄",
  "guidance_lines": ["2到5条纯盘面走势脉络，不得包含任何操作建议"],
  "focus_lines": ["2到5条市场结构信息，例如涨跌广度、成交资金、板块轮动或指数分化"],
  "risk_lines": ["1到4条客观风险现象，不得转化为操作建议"]
}}

再次强调：输出是全市场走势复盘，不是交易计划；禁止出现“开仓、买入、卖出、仓位、止损、持仓处理”等指引。
""".strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _local_summary(scans: list[dict[str, Any]], day: str) -> dict[str, Any]:
    a_share_scans = [scan for scan in scans if scan.get("source_kind") != "overnight_us"]
    overnight_us = next((scan for scan in scans if scan.get("source_kind") == "overnight_us"), None)
    first_tone = _tone_from_scan(a_share_scans[0])
    latest_tone = _tone_from_scan(a_share_scans[-1])
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
    return {
        **cached,
        "ok": True,
        "date": day,
        "scan_count": a_share_count,
        "us_summary_count": us_summary_count,
        "source_count": len(scans),
        "stale": bool(cached.get("available") and cached.get("source_fingerprint") != fingerprint),
    }


def generate_and_store_summary(records: list[dict[str, Any]], cache_file: Path, now: datetime) -> dict[str, Any]:
    day = now.strftime("%Y-%m-%d")
    scans = collect_market_replay_sources(records, day)
    a_share_count = sum(1 for scan in scans if scan.get("source_kind") == "a_share_scan")
    us_summary_count = sum(1 for scan in scans if scan.get("source_kind") == "overnight_us")
    if not a_share_count:
        return {
            "ok": False,
            "available": False,
            "date": day,
            "scan_count": 0,
            "us_summary_count": us_summary_count,
            "source_count": len(scans),
            "error": "今日暂无可汇总的A股盘面扫描",
        }
    result = build_daily_market_summary(scans, day)
    payload = {
        "ok": True,
        "available": True,
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "date": day,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "scan_count": a_share_count,
        "us_summary_count": us_summary_count,
        "source_count": len(scans),
        "source_fingerprint": source_fingerprint(scans),
        "sources": [
            {"title": scan.get("title"), "time": scan.get("time"), "source_kind": scan.get("source_kind")}
            for scan in scans
        ],
        **result,
    }
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = cache_file.with_name(f".{cache_file.name}.{os.getpid()}.tmp")
    with _WRITE_LOCK:
        tmp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_file.replace(cache_file)
    return payload
