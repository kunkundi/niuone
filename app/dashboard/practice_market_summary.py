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

from .apis.industry_flow import is_industry_flow_session_timestamp


_WRITE_LOCK = threading.Lock()
SUMMARY_SCHEMA_VERSION = 6
LIVE_SNAPSHOT_MAX_AGE_SECONDS = 300
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
        for key in (
            "key", "source", "price", "pct", "change_pct", "net_flow_yi", "leader", "leader_pct",
        ):
            value = raw.get(key)
            if key in {"key", "source", "leader"}:
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


def _sector_rows(
    payload: dict[str, Any],
    rank: str,
    *,
    source: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    prefix = "industry" if source == "行业" else "concept"
    explicit = payload.get(f"{prefix}_{rank}")
    if isinstance(explicit, list) and explicit:
        return _named_rows(explicit, limit=limit)

    raw_rows = payload.get(rank)
    if not raw_rows and rank == "gain_top":
        raw_rows = payload.get("items")
    rows = [row for row in (raw_rows or []) if isinstance(row, dict)]
    accepted_sources = {"行业", "指数"} if source == "行业" else {"概念"}
    tagged = [
        row for row in rows
        if str(row.get("source") or "").strip() in accepted_sources
    ]
    if tagged:
        rows = tagged
    elif any(str(row.get("source") or "").strip() for row in rows):
        rows = []
    elif source == "概念":
        # Older payloads did not label their universe.  Preserve those rows as
        # industries for compatibility, but never duplicate them as concepts.
        rows = []
    return _named_rows(rows, limit=limit)


def _industry_identity(name: Any) -> str:
    text = re.sub(r"\s+", "", str(name or "").strip())
    return re.sub(r"(?:Ⅱ|Ⅲ|II|III)$", "", text, flags=re.I)


def _hot_industry_rows(
    gain_top: list[dict[str, Any]],
    inflow: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Rank hot industries by cross-confirmed price strength and main inflow."""
    ranked: dict[str, dict[str, Any]] = {}
    gain_count = len(gain_top)
    flow_count = len(inflow)

    for index, row in enumerate(gain_top):
        pct = float(row.get("pct") or row.get("change_pct") or 0)
        if pct <= 0:
            continue
        identity = _industry_identity(row.get("name"))
        if not identity:
            continue
        ranked[identity] = {
            **row,
            "pct": round(pct, 2),
            "gain_rank": index + 1,
            "rank_score": (gain_count - index) * 2,
        }

    for index, row in enumerate(inflow):
        net_flow = float(row.get("net_flow_yi") or 0)
        if net_flow <= 0:
            continue
        identity = _industry_identity(row.get("name"))
        if not identity:
            continue
        existing = ranked.setdefault(identity, {"name": str(row.get("name") or "").strip(), "rank_score": 0})
        if existing.get("pct") is None and row.get("pct") is not None:
            existing["pct"] = round(float(row.get("pct") or 0), 2)
        existing["net_flow_yi"] = round(net_flow, 2)
        existing["flow_rank"] = index + 1
        existing["rank_score"] = float(existing.get("rank_score") or 0) + (flow_count - index)
        if row.get("leader") and not existing.get("leader"):
            existing["leader"] = row["leader"]

    result = list(ranked.values())
    for row in result:
        row["confirmed"] = bool(row.get("gain_rank") and row.get("flow_rank"))
    result.sort(
        key=lambda row: (
            bool(row.get("confirmed")),
            float(row.get("rank_score") or 0),
            float(row.get("pct") or 0),
            float(row.get("net_flow_yi") or 0),
        ),
        reverse=True,
    )
    for row in result:
        row.pop("rank_score", None)
    return result[:limit]


def _hot_rank_text(rows: list[dict[str, Any]]) -> str:
    items: list[str] = []
    for row in rows:
        metrics: list[str] = []
        if _number(row.get("pct")) is not None:
            metrics.append(f"{float(row['pct']):+.2f}%")
        if _number(row.get("net_flow_yi")) is not None:
            metrics.append(f"主力净流入{float(row['net_flow_yi']):+.2f}亿")
        suffix = f"（{'，'.join(metrics)}）" if metrics else ""
        items.append(f"{row['name']}{suffix}")
    return "、".join(items) or "无有效数据"


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
    gain_top = _sector_rows(
        sectors_payload or {}, "gain_top", source="行业", limit=6,
    ) if sector_fresh else []
    loss_top = _sector_rows(
        sectors_payload or {}, "loss_top", source="行业", limit=6,
    ) if sector_fresh else []
    concept_gain_top = _sector_rows(
        sectors_payload or {}, "gain_top", source="概念", limit=6,
    ) if sector_fresh else []
    concept_loss_top = _sector_rows(
        sectors_payload or {}, "loss_top", source="概念", limit=6,
    ) if sector_fresh else []
    sector_source = "板块实时排行"
    if not gain_top and inflow:
        derived = sorted([*inflow, *outflow], key=lambda row: float(row.get("pct") or 0), reverse=True)
        gain_top = derived[:6]
        loss_top = list(reversed(derived[-6:]))
        sector_source = "行业资金流行情"

    hot_sectors = _hot_industry_rows(gain_top, inflow, limit=5)

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
        f"实时热门行业综合榜：{_hot_rank_text(hot_sectors)}",
        f"行业板块涨幅前列：{_pct_rank_text(gain_top)}",
        f"行业板块跌幅前列：{_pct_rank_text(loss_top)}",
        f"热门概念涨幅前列：{_pct_rank_text(concept_gain_top)}",
        f"行业主力净流入前列：{_flow_rank_text(inflow)}",
        f"行业主力净流出前列：{_flow_rank_text(outflow)}",
    ]
    if errors:
        content_lines.append("抓取异常：" + "；".join(errors))
    summary = (
        f"本次快照核心指数为{index_text}；实时热门行业综合榜为{_hot_rank_text(hot_sectors[:3])}；"
        f"行业主力净流入集中在{_flow_rank_text(inflow[:3])}。"
    )
    complete = not missing_channels
    return {
        "source_kind": "realtime_snapshot",
        "title": "本次生成实时盘面快照",
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
            "hot_sectors": hot_sectors,
            "sectors": {
                "source": sector_source,
                "gain_top": gain_top,
                "loss_top": loss_top,
                "concept_gain_top": concept_gain_top,
                "concept_loss_top": concept_loss_top,
            },
            "industry_fund_flow": {"inflow": inflow, "outflow": outflow},
            "source_generated_at": {
                "indices": str((indices_payload or {}).get("generated_at") or ""),
                "sectors": str((sectors_payload or {}).get("generated_at") or ""),
                "money_flow": str((money_flow_payload or {}).get("generated_at") or ""),
            },
        },
    }


def _reference_flow_rows(nodes: Any, role: str, *, limit: int = 5) -> list[dict[str, Any]]:
    rows = [
        row for row in _named_rows(nodes, limit=20)
        if (
            role == "inflow"
            and float(row.get("net_flow_yi") or 0) > 0
        )
        or (
            role == "outflow"
            and float(row.get("net_flow_yi") or 0) < 0
        )
    ]
    rows.sort(
        key=lambda row: float(row.get("net_flow_yi") or 0),
        reverse=role == "inflow",
    )
    return rows[:limit]


def _compact_flow_frame(frame: dict[str, Any] | None) -> dict[str, Any]:
    source = frame if isinstance(frame, dict) else {}
    totals = source.get("totals") if isinstance(source.get("totals"), dict) else {}
    return {
        "generated_at": str(source.get("generated_at") or ""),
        "inflow": _reference_flow_rows(source.get("nodes"), "inflow", limit=3),
        "outflow": _reference_flow_rows(source.get("nodes"), "outflow", limit=3),
        "totals": {
            key: round(float(value), 2)
            for key in (
                "visible_inflow_yi",
                "visible_outflow_yi",
                "visible_balance_yi",
            )
            if (value := _number(totals.get(key))) is not None
        },
    }


def add_dashboard_market_references(
    snapshot_source: dict[str, Any],
    *,
    industry_flow_payload: dict[str, Any] | None = None,
    market_breadth_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach compact references from the fund-flow and sentiment pages."""

    result = dict(snapshot_source or {})
    snapshot = dict(result.get("snapshot") or {})
    content_lines = [line for line in str(result.get("content") or "").splitlines() if line]

    flow_source = industry_flow_payload if isinstance(industry_flow_payload, dict) else {}
    flow_nodes = flow_source.get("nodes") if isinstance(flow_source.get("nodes"), list) else []
    flow_timeline = flow_source.get("timeline") if isinstance(flow_source.get("timeline"), list) else []
    current_flow = {
        "generated_at": str(flow_source.get("generated_at") or ""),
        "inflow": _reference_flow_rows(flow_nodes, "inflow"),
        "outflow": _reference_flow_rows(flow_nodes, "outflow"),
        "totals": _compact_flow_frame({"totals": flow_source.get("totals")}).get("totals", {}),
    }
    flow_reference = {
        **current_flow,
        "metric": str(flow_source.get("metric") or ""),
        "metric_label": str(flow_source.get("metric_label") or ""),
        "sample_count": len(flow_timeline),
        "first_sample": _compact_flow_frame(flow_timeline[0] if flow_timeline else None),
        "last_sample": _compact_flow_frame(flow_timeline[-1] if flow_timeline else None),
        "available": bool(flow_source.get("available") and flow_nodes),
        "stale": bool(flow_source.get("stale_cache") or flow_source.get("error")),
    }
    if flow_reference["available"]:
        totals = flow_reference["totals"]
        flow_label = "资金流动页缓存榜" if flow_reference["stale"] else "资金流动页最新榜"
        content_lines.extend([
            f"{flow_label}：主力净流入"
            f"{_flow_rank_text(flow_reference['inflow'])}；主力净流出"
            f"{_flow_rank_text(flow_reference['outflow'])}。",
            "资金流动页可见行业合计：净流入"
            f"{float(totals.get('visible_inflow_yi') or 0):.2f}亿，净流出"
            f"{float(totals.get('visible_outflow_yi') or 0):.2f}亿，净额"
            f"{float(totals.get('visible_balance_yi') or 0):+.2f}亿；"
            f"日内采样{flow_reference['sample_count']}个。",
        ])
        first_sample = flow_reference["first_sample"]
        last_sample = flow_reference["last_sample"]
        if first_sample.get("generated_at") and last_sample.get("generated_at"):
            content_lines.append(
                "资金流动页日内对比："
                f"{first_sample['generated_at'][11:16]}净流入前列"
                f"{_flow_rank_text(first_sample['inflow'])}；"
                f"{last_sample['generated_at'][11:16]}净流入前列"
                f"{_flow_rank_text(last_sample['inflow'])}。"
            )
    else:
        content_lines.append("资金流动页参考：当前无有效行业主力净额数据。")

    breadth_source = market_breadth_payload if isinstance(market_breadth_payload, dict) else {}
    latest_breadth = breadth_source.get("latest") if isinstance(breadth_source.get("latest"), dict) else {}
    breadth_timeline = breadth_source.get("timeline") if isinstance(breadth_source.get("timeline"), list) else []
    breadth_keys = (
        "red",
        "green",
        "flat",
        "limit_up",
        "limit_down",
        "broken_limit",
        "quote_count",
        "actual_turnover_yi",
        "estimated_turnover_yi",
        "previous_turnover_yi",
        "turnover_increment_yi",
        "turnover_same_time_delta_yi",
    )
    breadth_reference: dict[str, Any] = {
        "available": bool(breadth_source.get("available") and latest_breadth),
        "stale": bool(breadth_source.get("stale_cache") or breadth_source.get("error")),
        "generated_at": str(latest_breadth.get("generated_at") or breadth_source.get("generated_at") or ""),
        "sample_count": len(breadth_timeline),
    }
    for key in breadth_keys:
        number = _number(latest_breadth.get(key))
        if number is not None:
            breadth_reference[key] = round(number, 2)
    if breadth_reference["available"]:
        breadth_label = "市场情绪页缓存值" if breadth_reference["stale"] else "市场情绪页最新值"
        content_lines.append(
            f"{breadth_label}：红盘"
            f"{int(breadth_reference.get('red') or 0)}只、绿盘"
            f"{int(breadth_reference.get('green') or 0)}只、平盘"
            f"{int(breadth_reference.get('flat') or 0)}只；涨停"
            f"{int(breadth_reference.get('limit_up') or 0)}只、跌停"
            f"{int(breadth_reference.get('limit_down') or 0)}只、炸板"
            f"{int(breadth_reference.get('broken_limit') or 0)}只；"
            f"日内采样{breadth_reference['sample_count']}个。"
        )
        turnover_parts: list[str] = []
        for key, label in (
            ("actual_turnover_yi", "实际成交"),
            ("estimated_turnover_yi", "预测全天"),
            ("previous_turnover_yi", "前日成交"),
            ("turnover_increment_yi", "预测增量"),
            ("turnover_same_time_delta_yi", "同时点差额"),
        ):
            if key in breadth_reference:
                value = float(breadth_reference[key])
                formatted = f"{value:+.2f}" if key in {
                    "turnover_increment_yi",
                    "turnover_same_time_delta_yi",
                } else f"{value:.2f}"
                turnover_parts.append(f"{label}{formatted}亿")
        if turnover_parts:
            content_lines.append("市场情绪页量能：" + "；".join(turnover_parts) + "。")
    else:
        content_lines.append("市场情绪页参考：当前无有效红绿盘及涨跌停数据。")

    snapshot["industry_flow_page"] = flow_reference
    snapshot["market_breadth_page"] = breadth_reference
    source_generated_at = dict(snapshot.get("source_generated_at") or {})
    source_generated_at.update({
        "industry_flow_page": flow_reference.get("generated_at", ""),
        "market_breadth_page": breadth_reference.get("generated_at", ""),
    })
    snapshot["source_generated_at"] = source_generated_at
    result["snapshot"] = snapshot
    result["content"] = "\n".join(content_lines)
    result["reference_pages"] = {
        "industry_flow": flow_reference["available"],
        "market_breadth": breadth_reference["available"],
    }
    return result


def _market_snapshot_without_action_guidance(content: str) -> str:
    try:
        from reports.a_share.grok import remove_original_guidance

        return remove_original_guidance(content)
    except Exception:
        return content


def _model_messages(scans: list[dict[str, Any]], day: str) -> list[dict[str, str]]:
    sections: list[str] = []
    has_reference = any(scan.get("source_kind") != "realtime_snapshot" for scan in scans)
    comparison_instruction = (
        "2到5条实时快照相对最近已有总结的对比结论，必须明确延续/强化/弱化/反转/轮动中的适用状态"
        if has_reference
        else "1到3条对本次实时快照的客观结论，明确说明当前没有更早的A股总结可比"
    )
    remaining = 60000
    for index, scan in enumerate(scans, 1):
        content = _market_snapshot_without_action_guidance(str(scan.get("content") or ""))
        excerpt = content[: min(18000, remaining)]
        remaining = max(0, remaining - len(excerpt))
        source_kind = scan.get("source_kind")
        if source_kind == "overnight_us":
            source_label = "前一美股交易日总结"
        elif source_kind == "realtime_snapshot":
            source_label = "本次生成刚抓取的实时A股盘面"
        elif source_kind == "previous_generated_summary":
            source_label = "上一版此刻盘面总结与评价"
        else:
            source_label = "当日已有A股盘面总结/扫描"
        sections.append(
            f"资料{index}｜{source_label}｜{scan.get('time')}｜{scan.get('title')}\n"
            f"已有摘要：{_summary_line(scan) or '无'}\n"
            f"扫描原文：\n{excerpt}"
        )
    system = (
        "你是牛牛1号的A股日内市场复盘助手。你会收到前一美股交易日总结、今天已有的A股盘面总结/扫描、"
        "上一版此刻盘面总结（如有），以及本次生成刚抓取的实时A股指数、行业板块涨跌和行业资金流。"
        "实时快照还包含资金流动页的行业主力净额与日内轨迹，以及市场情绪页的红绿盘、涨跌停、炸板和量能数据。"
        "先把美股总结作为A股开盘前的外部背景，再对照A股实际走势说明哪些风险偏好或板块映射得到验证、弱化或反转。"
        "判断当前热门板块时，必须以本次快照中的“实时热门行业综合榜”为准；概念榜只用于说明该行业内部的细分扩散，"
        "不得用单个概念标签替代或否定综合榜首行业。"
        "必须以本次实时快照作为最新事实，并与时间上最近的已有A股总结及上一版此刻盘面总结进行对比，"
        "明确指出判断得到延续/强化、出现弱化/反转，或板块资金发生轮动；不得只复述实时榜单。"
        "资金流动页或市场情绪页存在有效数据时，必须分别把两者纳入结论或市场结构，不得只引用指数和板块排行。"
        "不得把美股表现写成A股已经发生的事实，也不得仅凭相关性断言因果。"
        "请站在全市场视角总结指数走势、涨跌家数与涨跌停、市场情绪、成交与资金、板块轮动及日内演变。"
        "只做客观盘面复盘，不得输出开仓、买入、卖出、持仓、仓位、止损等操作指引。"
        "区分早盘判断与最新判断，不能编造输入以外的行情、政策、新闻或资金数据。"
        "必须输出严格JSON，不要Markdown、代码块或URL。"
    )
    user = f"""
日期：{day}
复盘资料总数：{len(scans)}（包含历史总结/扫描、上一版总结及本次触发实时快照）

{chr(10).join(sections)}

请输出：
{{
  "tone": "offensive|balanced|neutral|cautious|defensive",
  "tone_label": "进攻|平衡|中性|谨慎|防守",
  "summary": "2到4句中文市场总结，说明指数、情绪、资金和板块从已有总结到实时快照如何变化，不要逐条照抄",
  "comparison_lines": ["{comparison_instruction}"],
  "guidance_lines": ["2到5条纯盘面走势脉络，不得包含任何操作建议"],
  "focus_lines": ["2到5条市场结构信息，例如涨跌广度、成交资金、板块轮动或指数分化"],
  "risk_lines": ["1到4条客观风险现象，不得转化为操作建议"]
}}

再次强调：实时数据必须参与最终结论，comparison_lines 不得为空。输出是全市场走势复盘，不是交易计划；
资金流动页和市场情绪页有有效数据时，最终结果必须分别体现其资金结构与市场广度/量能信息；
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
    hot_sectors = [row for row in (snapshot.get("hot_sectors") or []) if isinstance(row, dict)][:3]
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
            f"对比{reference_label}，本次快照核心指数平均涨跌幅为{average:+.2f}%，最新指数状态{direction}。"
        )

    strong_names = list(dict.fromkeys(
        str(row.get("name") or "") for row in [*hot_sectors, *gain_top, *inflow] if str(row.get("name") or "")
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


def _local_page_reference_lines(realtime: dict[str, Any] | None) -> list[str]:
    snapshot = realtime.get("snapshot") if isinstance((realtime or {}).get("snapshot"), dict) else {}
    flow = snapshot.get("industry_flow_page") if isinstance(snapshot.get("industry_flow_page"), dict) else {}
    breadth = snapshot.get("market_breadth_page") if isinstance(snapshot.get("market_breadth_page"), dict) else {}
    lines: list[str] = []
    if flow.get("available"):
        totals = flow.get("totals") if isinstance(flow.get("totals"), dict) else {}
        lines.append(
            "资金流动页显示：可见行业净流入合计"
            f"{float(totals.get('visible_inflow_yi') or 0):.2f}亿、净流出合计"
            f"{float(totals.get('visible_outflow_yi') or 0):.2f}亿、净额"
            f"{float(totals.get('visible_balance_yi') or 0):+.2f}亿。"
        )
    if breadth.get("available"):
        lines.append(
            "市场情绪页显示：红盘"
            f"{int(breadth.get('red') or 0)}只、绿盘{int(breadth.get('green') or 0)}只，"
            f"涨停{int(breadth.get('limit_up') or 0)}只、跌停"
            f"{int(breadth.get('limit_down') or 0)}只、炸板"
            f"{int(breadth.get('broken_limit') or 0)}只。"
        )
    return lines


def _local_summary(scans: list[dict[str, Any]], day: str) -> dict[str, Any]:
    a_share_scans = [scan for scan in scans if scan.get("source_kind") == "a_share_scan"]
    overnight_us = next((scan for scan in scans if scan.get("source_kind") == "overnight_us"), None)
    previous_summary = next((scan for scan in reversed(scans) if scan.get("source_kind") == "previous_generated_summary"), None)
    realtime = next((scan for scan in reversed(scans) if scan.get("source_kind") == "realtime_snapshot"), None)
    if not a_share_scans and not realtime:
        return {
            "tone": "neutral", "tone_label": _TONE_LABELS["neutral"],
            "summary": f"{day}暂无可对比的A股盘面总结。", "comparison_lines": [],
            "trend_lines": [], "structure_lines": [], "risk_lines": [], "model_used": False,
        }
    first_tone = _tone_from_scan(a_share_scans[0]) if a_share_scans else "neutral"
    historical_tone = _tone_from_scan(a_share_scans[-1]) if a_share_scans else first_tone
    latest_tone = _realtime_tone(realtime, historical_tone)
    latest_line = _summary_line(a_share_scans[-1]) if a_share_scans else ""
    us_prefix = f"前一美股交易日整体呈{_TONE_LABELS[_tone_from_scan(overnight_us)]}基调。" if overnight_us else ""
    if not a_share_scans:
        summary = f"{day}已根据此刻实时盘面生成评价，当前风险级别为{_TONE_LABELS[latest_tone]}。"
    elif len(a_share_scans) == 1:
        summary = f"{day}已完成1次盘面扫描，当前风险级别为{_TONE_LABELS[latest_tone]}。"
    else:
        summary = (
            f"{day}已汇总{len(a_share_scans)}次A股盘面扫描，风险判断由"
            f"{_TONE_LABELS[first_tone]}演变为{_TONE_LABELS[latest_tone]}。"
        )
    if latest_line:
        summary += latest_line if summary.endswith(("。", "！", "？")) else f" {latest_line}"
    summary = us_prefix + summary
    reference = previous_summary or (a_share_scans[-1] if a_share_scans else overnight_us)
    comparison_lines = _local_comparison_lines(reference, realtime) if realtime and reference else []
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
        "structure_lines": _local_page_reference_lines(realtime),
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
    tone = str(result.get("tone") or "").strip().lower()
    if tone not in _TONE_LABELS:
        label = str(result.get("tone_label") or "").strip()
        tone = next((key for key, value in _TONE_LABELS.items() if value == label), "neutral")
    result["tone"] = tone
    result["tone_label"] = _TONE_LABELS[tone]
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


def _live_snapshot_is_stale(cached: dict[str, Any], now: datetime) -> bool:
    if now.weekday() >= 5 or not is_industry_flow_session_timestamp(now):
        return False
    try:
        captured_at = datetime.strptime(
            str(cached.get("live_snapshot_at") or ""), "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        return bool(cached.get("available"))
    age_seconds = (now - captured_at).total_seconds()
    return age_seconds < 0 or age_seconds > LIVE_SNAPSHOT_MAX_AGE_SECONDS


def summary_status(records: list[dict[str, Any]], cache_file: Path, now: datetime) -> dict[str, Any]:
    day = now.strftime("%Y-%m-%d")
    scans = collect_market_replay_sources(records, day)
    a_share_count = sum(1 for scan in scans if scan.get("source_kind") == "a_share_scan")
    us_summary_count = sum(1 for scan in scans if scan.get("source_kind") == "overnight_us")
    cached = load_cached_summary(cache_file, day)
    fingerprint = source_fingerprint(scans)
    live_snapshot_count = int(bool(cached.get("available") and cached.get("live_snapshot_at")))
    previous_summary_count = int(cached.get("previous_summary_count") or 0) if cached.get("available") else 0
    source_stale = bool(cached.get("available") and cached.get("source_fingerprint") != fingerprint)
    live_snapshot_stale = bool(cached.get("available") and _live_snapshot_is_stale(cached, now))
    return {
        **cached,
        "ok": True,
        "date": day,
        "scan_count": a_share_count,
        "us_summary_count": us_summary_count,
        "live_snapshot_count": live_snapshot_count,
        "previous_summary_count": previous_summary_count,
        "source_count": len(scans) + live_snapshot_count + previous_summary_count,
        "stale": source_stale or live_snapshot_stale,
        "stale_reasons": [
            reason for reason, active in (
                ("有新增盘面扫描", source_stale),
                ("实时快照已超过5分钟", live_snapshot_stale),
            ) if active
        ],
    }


def _previous_summary_source(cached: dict[str, Any]) -> dict[str, Any] | None:
    if not cached.get("available") or not str(cached.get("summary") or "").strip():
        return None
    content_lines = [str(cached.get("summary") or "").strip()]
    for key in ("comparison_lines", "trend_lines", "structure_lines", "risk_lines"):
        content_lines.extend(str(line).strip() for line in cached.get(key) or [] if str(line).strip())
    return {
        "source_kind": "previous_generated_summary",
        "title": "上一版此刻盘面总结与评价",
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
    trigger: str = "manual",
) -> dict[str, Any]:
    day = now.strftime("%Y-%m-%d")
    historical_sources = collect_market_replay_sources(records, day)
    a_share_count = sum(1 for scan in historical_sources if scan.get("source_kind") == "a_share_scan")
    us_summary_count = sum(1 for scan in historical_sources if scan.get("source_kind") == "overnight_us")
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
    if not a_share_count and not realtime_source:
        return {
            "ok": False,
            "available": False,
            "date": day,
            "scan_count": 0,
            "us_summary_count": us_summary_count,
            "source_count": len(historical_sources),
            "error": "暂无可用的A股盘面快照或扫描",
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
    hot_sector_lines = [
        _hot_rank_text([row])
        for row in ((((realtime_source or {}).get("snapshot") or {}).get("hot_sectors") or [])[:5])
        if isinstance(row, dict)
    ]
    payload = {
        "ok": True,
        "available": True,
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "date": day,
        "trigger": str(trigger or "manual"),
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "scan_count": a_share_count,
        "us_summary_count": us_summary_count,
        "live_snapshot_count": int(bool(realtime_source)),
        "previous_summary_count": int(bool(previous_source)),
        "source_count": len(sources),
        "source_fingerprint": source_fingerprint(historical_sources),
        "input_fingerprint": source_fingerprint(sources),
        "live_snapshot_at": live_snapshot_at,
        "hot_sector_lines": hot_sector_lines,
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
