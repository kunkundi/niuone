#!/usr/bin/env python3
"""Deterministic overnight US market summary for A-share trading context."""
from __future__ import annotations

import math
import argparse
import json
import os
import re
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from niuone_paths import get_dashboard_env_file, get_dashboard_home

CN_TZ = ZoneInfo("Asia/Shanghai")
NY_TZ = ZoneInfo("America/New_York")
JOB_ID = "98f0c8a12d3e"
JOB_TITLE = "隔夜美股盘面总结"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
SUMMARY_CACHE_FILE = Path(
    os.environ.get("DASHBOARD_US_MARKET_SUMMARY_CACHE")
    or str(DASHBOARD_HOME / "cron" / "output" / "us_market_summary_latest.json")
).expanduser()
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE

_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_SECTOR_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
CACHE_TTL_SECONDS = 300
SECTOR_CACHE_TTL_SECONDS = 900
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
US_SECTOR_PROXY_DEFS: list[dict[str, Any]] = [
    {
        "key": "semiconductors",
        "symbol": "XSD",
        "label": "半导体",
        "kind": "industry",
        "a_share_mapping": ["半导体"],
    },
    {
        "key": "software_services",
        "symbol": "XSW",
        "label": "软件服务",
        "kind": "industry",
        "a_share_mapping": ["软件服务"],
    },
    {
        "key": "telecom",
        "symbol": "XTL",
        "label": "电信",
        "kind": "industry",
        "a_share_mapping": ["运营商"],
    },
    {
        "key": "retail",
        "symbol": "XRT",
        "label": "零售",
        "kind": "industry",
        "a_share_mapping": ["零售"],
    },
    {
        "key": "homebuilders",
        "symbol": "XHB",
        "label": "住宅建筑",
        "kind": "industry",
        "a_share_mapping": ["住宅产业链"],
    },
    {
        "key": "transportation",
        "symbol": "XTN",
        "label": "运输",
        "kind": "industry",
        "a_share_mapping": ["交通运输"],
    },
    {
        "key": "aerospace_defense",
        "symbol": "XAR",
        "label": "航空航天与国防",
        "kind": "industry",
        "a_share_mapping": ["军工"],
    },
    {
        "key": "regional_banks",
        "symbol": "KRE",
        "label": "地区银行",
        "kind": "industry",
        "a_share_mapping": ["银行"],
    },
    {
        "key": "capital_markets",
        "symbol": "KCE",
        "label": "资本市场",
        "kind": "industry",
        "a_share_mapping": ["券商"],
    },
    {
        "key": "insurance",
        "symbol": "KIE",
        "label": "保险",
        "kind": "industry",
        "a_share_mapping": ["保险"],
    },
    {
        "key": "biotechnology",
        "symbol": "XBI",
        "label": "生物科技",
        "kind": "industry",
        "a_share_mapping": ["创新药"],
    },
    {
        "key": "pharmaceuticals",
        "symbol": "XPH",
        "label": "制药",
        "kind": "industry",
        "a_share_mapping": ["制药"],
    },
    {
        "key": "healthcare_equipment",
        "symbol": "XHE",
        "label": "医疗设备",
        "kind": "industry",
        "a_share_mapping": ["医疗器械"],
    },
    {
        "key": "healthcare_services",
        "symbol": "XHS",
        "label": "医疗服务",
        "kind": "industry",
        "a_share_mapping": ["医疗服务"],
    },
    {
        "key": "oil_gas_exploration",
        "symbol": "XOP",
        "label": "油气勘探",
        "kind": "industry",
        "a_share_mapping": ["油气开采"],
    },
    {
        "key": "oil_gas_services",
        "symbol": "XES",
        "label": "油服设备",
        "kind": "industry",
        "a_share_mapping": ["油服"],
    },
    {
        "key": "metals_mining",
        "symbol": "XME",
        "label": "金属矿业",
        "kind": "industry",
        "a_share_mapping": ["金属矿业"],
    },
]


def load_dashboard_env() -> None:
    allowed = {
        "DASHBOARD_GROK_MODEL",
        "DASHBOARD_GROK_CONTEXT_LENGTH",
        "DASHBOARD_GROK_BASE_URL",
        "DASHBOARD_GROK_API_KEY",
        "US_MARKET_SUMMARY_MODEL",
        "US_MARKET_SUMMARY_CONTEXT_LENGTH",
        "US_MARKET_SUMMARY_MAX_TOKENS",
        "US_MARKET_SUMMARY_BASE_URL",
        "US_MARKET_SUMMARY_API_KEY",
        "US_MARKET_SUMMARY_DEADLINE_SECONDS",
        "US_MARKET_SUMMARY_REQUEST_TIMEOUT_SECONDS",
        "CROSSDESK_BASE_URL",
        "CROSSDESK_API_KEY",
    }
    path = get_dashboard_env_file(PROJECT_ROOT)
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in allowed or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("\"'")


load_dashboard_env()


def _int_env(name: str, default: int, *, min_value: int) -> int:
    try:
        value = int(str(os.environ.get(name) or "").strip())
    except (TypeError, ValueError):
        value = default
    return max(min_value, value)


def _token_count_env(*names: str, default: int) -> int:
    for name in names:
        raw = str(os.environ.get(name) or "").strip()
        if not raw:
            continue
        compact = raw.replace(",", "").replace("_", "").strip()
        match = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", compact)
        if not match:
            continue
        number = float(match.group(1))
        unit = match.group(2).lower()
        multiplier = 1_000_000 if unit == "m" else 1_000 if unit == "k" else 1
        value = int(number * multiplier)
        if value > 0:
            return value
    return default


US_MARKET_SUMMARY_MODEL = (
    os.environ.get("US_MARKET_SUMMARY_MODEL")
    or os.environ.get("DASHBOARD_GROK_MODEL")
    or "grok-4.20-multi-agent-xhigh"
)
US_MARKET_SUMMARY_DEADLINE_SECONDS = _int_env("US_MARKET_SUMMARY_DEADLINE_SECONDS", 150, min_value=30)
US_MARKET_SUMMARY_REQUEST_TIMEOUT_SECONDS = _int_env("US_MARKET_SUMMARY_REQUEST_TIMEOUT_SECONDS", 90, min_value=10)
US_MARKET_SUMMARY_CONTEXT_LENGTH = _token_count_env(
    "US_MARKET_SUMMARY_CONTEXT_LENGTH",
    "DASHBOARD_GROK_CONTEXT_LENGTH",
    default=128000,
)
US_MARKET_SUMMARY_MAX_TOKENS = _token_count_env(
    "US_MARKET_SUMMARY_MAX_TOKENS",
    default=4096,
)


def previous_us_session_date(cn_day: date | datetime | None = None) -> date:
    """Return the US session that should guide a China trading day.

    Tuesday-Friday use the prior calendar day. Monday, Sunday, and Saturday
    walk back to Friday because there is no completed Sunday US cash session.
    """
    if cn_day is None:
        cn_day = datetime.now(CN_TZ).date()
    if isinstance(cn_day, datetime):
        cn_day = cn_day.astimezone(CN_TZ).date() if cn_day.tzinfo else cn_day.date()
    target = cn_day - timedelta(days=1)
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    return target


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        n = float(str(value).replace("%", "").replace(",", "").strip())
        if math.isnan(n) or math.isinf(n):
            return None
        return n
    except Exception:
        return None


def _load_config() -> dict[str, Any]:
    config_path = Path(os.environ.get("DASHBOARD_CONFIG") or str(DASHBOARD_HOME / "config.yaml")).expanduser()
    try:
        import yaml  # type: ignore

        if config_path.exists():
            return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return {}


def _get_grok_credentials() -> tuple[str, str]:
    env_base_url = (
        os.environ.get("US_MARKET_SUMMARY_BASE_URL")
        or os.environ.get("DASHBOARD_GROK_BASE_URL")
        or os.environ.get("CROSSDESK_BASE_URL")
    )
    env_api_key = (
        os.environ.get("US_MARKET_SUMMARY_API_KEY")
        or os.environ.get("DASHBOARD_GROK_API_KEY")
        or os.environ.get("CROSSDESK_API_KEY")
    )
    if env_base_url and env_api_key:
        return env_base_url.rstrip("/"), env_api_key

    cfg = _load_config()
    for provider in cfg.get("custom_providers", []) or []:
        base_url = str(provider.get("base_url") or "")
        if "crossdesk.ccwu.cc" in base_url or "grok" in str(provider.get("name") or "").lower():
            return base_url.rstrip("/"), str(provider.get("api_key") or "")
    model_cfg = cfg.get("model", {}) if isinstance(cfg.get("model"), dict) else {}
    return str(model_cfg.get("base_url") or "").rstrip("/"), str(model_cfg.get("api_key") or "")


def _is_transient_error(err: Exception) -> bool:
    if isinstance(err, TimeoutError):
        return True
    if isinstance(err, HTTPError):
        return err.code in {408, 429, 500, 502, 503, 504}
    if isinstance(err, URLError):
        return True
    text = str(err).lower()
    return any(hit in text for hit in ("timed out", "timeout", "temporarily", "connection reset", "empty stream", "ssl"))


def _call_grok_api(messages: list[dict[str, str]], *, max_tokens: int = US_MARKET_SUMMARY_MAX_TOKENS) -> str:
    base_url, api_key = _get_grok_credentials()
    if not base_url or not api_key:
        raise RuntimeError("Grok credentials not found: set DASHBOARD_GROK_BASE_URL and DASHBOARD_GROK_API_KEY")
    body = json.dumps({
        "model": US_MARKET_SUMMARY_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
    }).encode("utf-8")
    req = Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "NiuOne/1.0",
        },
    )
    deadline = time.monotonic() + max(30, US_MARKET_SUMMARY_DEADLINE_SECONDS)
    last_err: Exception | None = None
    for attempt in range(1, 4):
        remaining = deadline - time.monotonic()
        if remaining <= 5:
            break
        try:
            timeout_seconds = min(max(10, US_MARKET_SUMMARY_REQUEST_TIMEOUT_SECONDS), max(10, remaining - 2))
            with urlopen(req, timeout=timeout_seconds, context=_SSL_CONTEXT) as resp:
                payload = json.loads(resp.read().decode("utf-8", "ignore"))
                content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
                if str(content or "").strip():
                    return str(content).strip()
                last_err = RuntimeError("Grok returned empty content")
        except Exception as exc:
            last_err = exc
            if attempt < 3 and _is_transient_error(exc) and (deadline - time.monotonic()) > 8:
                time.sleep(min(3 * attempt, max(0, deadline - time.monotonic() - 5)))
                continue
            break
    if last_err:
        raise RuntimeError(f"Grok call failed: {last_err}")
    raise RuntimeError("Grok call did not complete before the local deadline")


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "--"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _find_item(items: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    for item in items:
        if str(item.get("key") or "") == key:
            return item
    return None


def _metric(item: dict[str, Any] | None, fallback_label: str) -> dict[str, Any] | None:
    if not item:
        return None
    pct = _safe_float(item.get("change_pct"))
    price = _safe_float(item.get("price"))
    if pct is None and price is None:
        return None
    return {
        "label": str(item.get("name") or fallback_label),
        "value": "--" if price is None else f"{price:,.2f}",
        "change_pct": pct,
        "change_pct_text": _fmt_pct(pct),
        "tone": "up" if (pct or 0) > 0 else ("down" if (pct or 0) < 0 else "flat"),
        "time": str(item.get("time") or ""),
    }


def _last_number(values: list[Any]) -> float | None:
    for value in reversed(values or []):
        number = _safe_float(value)
        if number is not None and number > 0:
            return number
    return None


def _fetch_yahoo_daily_quote(symbol: str) -> dict[str, Any] | None:
    req = Request(
        YAHOO_CHART_URL.format(symbol=quote(symbol, safe="")),
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    timeout_seconds = _int_env("US_SECTOR_SNAPSHOT_REQUEST_TIMEOUT_SECONDS", 8, min_value=3)
    with urlopen(req, timeout=timeout_seconds, context=_SSL_CONTEXT) as resp:
        payload = json.loads(resp.read().decode("utf-8", "ignore"))
    result = (((payload.get("chart") or {}).get("result") or []) + [None])[0]
    if not isinstance(result, dict):
        return None
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    closes = (((result.get("indicators") or {}).get("quote") or [{}])[0] or {}).get("close") or []
    close_values = [number for value in closes if (number := _safe_float(value)) is not None and number > 0]
    price = _safe_float(meta.get("regularMarketPrice")) or (close_values[-1] if close_values else None)
    # chartPreviousClose is the close before the requested range, not the prior session.
    prev_close = (
        (close_values[-2] if len(close_values) >= 2 else None)
        or _safe_float(meta.get("previousClose"))
        or _safe_float(meta.get("chartPreviousClose"))
    )
    prev_close = _safe_float(prev_close)
    if price is None or prev_close is None or prev_close <= 0:
        return None
    ts = _safe_float(meta.get("regularMarketTime"))
    time_text = ""
    if ts:
        time_text = datetime.fromtimestamp(float(ts), NY_TZ).astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "symbol": symbol,
        "price": round(price, 4),
        "prev_close": round(prev_close, 4),
        "change": round(price - prev_close, 4),
        "change_pct": round((price / prev_close - 1) * 100, 4),
        "time": time_text,
    }


def fetch_us_sector_snapshot(now: datetime | None = None) -> dict[str, Any]:
    """Fetch a lightweight granular US industry ETF snapshot for A-share mapping."""
    current_ts = time.time()
    if _SECTOR_CACHE.get("data") is not None and current_ts - float(_SECTOR_CACHE.get("ts") or 0) < SECTOR_CACHE_TTL_SECONDS:
        return _SECTOR_CACHE["data"]

    def build_item(defn: dict[str, Any]) -> dict[str, Any] | None:
        try:
            quote_data = _fetch_yahoo_daily_quote(str(defn.get("symbol") or ""))
        except Exception:
            return None
        if not quote_data:
            return None
        pct = _safe_float(quote_data.get("change_pct"))
        return {
            "key": defn.get("key"),
            "symbol": defn.get("symbol"),
            "label": defn.get("label"),
            "kind": defn.get("kind") or "sector",
            "price": quote_data.get("price"),
            "prev_close": quote_data.get("prev_close"),
            "change": quote_data.get("change"),
            "change_pct": pct,
            "change_pct_text": _fmt_pct(pct),
            "time": quote_data.get("time") or "",
            "a_share_mapping": list(defn.get("a_share_mapping") or []),
        }

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        with ThreadPoolExecutor(max_workers=min(6, len(US_SECTOR_PROXY_DEFS))) as pool:
            for item in pool.map(build_item, US_SECTOR_PROXY_DEFS):
                if item:
                    items.append(item)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")

    now_cn = _now_cn(now)
    data = {
        "items": items,
        "generated_at": now_cn.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if errors:
        data["error"] = "；".join(errors[:3])
    _SECTOR_CACHE.update({"ts": current_ts, "data": data})
    return data


def _tone_from_indices(metrics: list[dict[str, Any]]) -> tuple[str, str, str]:
    pct_by_label = {str(m.get("label")): _safe_float(m.get("change_pct")) for m in metrics}
    pcts = [v for v in pct_by_label.values() if v is not None]
    if len(pcts) < 2:
        return "neutral", "中性", "美股三大指数数据不完整，按中性背景处理。"
    avg = sum(pcts) / len(pcts)
    positives = sum(1 for v in pcts if v > 0.15)
    negatives = sum(1 for v in pcts if v < -0.15)
    nas = next((v for label, v in pct_by_label.items() if "纳斯达克" in label and v is not None), None)
    spx = next((v for label, v in pct_by_label.items() if "标普" in label and v is not None), None)

    if avg <= -1.0 or (nas is not None and nas <= -1.35) or (spx is not None and spx <= -1.0):
        return "defensive", "防守", "隔夜美股明显承压，今日先把风险预算降下来。"
    if avg < -0.25 or negatives >= 2:
        return "cautious", "谨慎", "隔夜美股偏弱或分化，今日不急着追高。"
    if avg >= 0.55 and positives >= 2 and (nas is None or nas >= 0):
        return "offensive", "进攻", "隔夜美股风险偏好回暖，今日可以更积极寻找确认后的机会。"
    if avg > 0.05 and positives >= 2:
        return "balanced", "平衡", "隔夜美股整体偏暖，今日按结构性机会处理。"
    return "neutral", "中性", "隔夜美股方向不强，今日以 A 股自身竞价和资金流为准。"


def _index_sentence(index_metrics: list[dict[str, Any]]) -> str:
    parts = [f"{m['label']} {m['change_pct_text']}" for m in index_metrics]
    return "、".join(parts) if parts else "三大指数数据暂缺"


def _relative_style_line(index_metrics: list[dict[str, Any]]) -> str:
    nas = next((_safe_float(m.get("change_pct")) for m in index_metrics if "纳斯达克" in str(m.get("label"))), None)
    dow = next((_safe_float(m.get("change_pct")) for m in index_metrics if "道琼斯" in str(m.get("label"))), None)
    spx = next((_safe_float(m.get("change_pct")) for m in index_metrics if "标普" in str(m.get("label"))), None)
    if nas is not None and dow is not None and nas - dow >= 0.6:
        return "纳指相对占优，AI、半导体、算力、消费电子映射方向可提高观察优先级。"
    if dow is not None and nas is not None and dow - nas >= 0.6:
        return "道指相对占优，资金偏价值/顺周期，A股高弹性成长方向需要多等确认。"
    if spx is not None and abs(spx) <= 0.25:
        return "标普波动不大，外盘只作背景，盘中主线仍看 A 股自身强弱。"
    return "三大指数风格差异不大，按整体风险偏好执行。"


def _cross_asset_lines(metrics_by_key: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    a50 = metrics_by_key.get("a50_fut")
    if a50 and a50.get("change_pct") is not None:
        pct = float(a50["change_pct"])
        if pct >= 0.3:
            lines.append(f"A50期货 {a50['change_pct_text']}，对 A 股开盘风险偏好有正反馈。")
        elif pct <= -0.3:
            lines.append(f"A50期货 {a50['change_pct_text']}，早盘需要防低开和承接不足。")
    gold = metrics_by_key.get("xau")
    if gold and gold.get("change_pct") is not None and float(gold["change_pct"]) >= 0.8:
        lines.append(f"黄金 {gold['change_pct_text']}，避险升温时减少追高，关注防守和资源线强弱。")
    oil = metrics_by_key.get("brent")
    if oil and oil.get("change_pct") is not None and abs(float(oil["change_pct"])) >= 1.0:
        direction = "上行" if float(oil["change_pct"]) > 0 else "下行"
        lines.append(f"原油{direction} {oil['change_pct_text']}，能源、化工和通胀交易需结合 A 股量能确认。")
    return lines[:3]


def _sector_bias(pct: float | None) -> tuple[str, str]:
    if pct is None:
        return "neutral", "观察"
    if pct >= 1.0:
        return "positive", "强正映射"
    if pct >= 0.35:
        return "positive", "正映射"
    if pct <= -1.0:
        return "negative", "明显压制"
    if pct <= -0.35:
        return "negative", "负映射"
    return "neutral", "观察"


def _sector_action(label: str, pct: float | None, a_share_mapping: list[str]) -> str:
    mapping = "、".join(a_share_mapping[:4]) or "相关板块"
    direction, bias = _sector_bias(pct)
    if direction == "positive":
        return f"{bias}，A股映射看{mapping}，只在竞价强于大盘且资金流入时加分。"
    if direction == "negative":
        return f"{bias}，A股映射的{mapping}先降权，持仓若弱于板块优先控风险。"
    return f"{bias}，{label}映射到{mapping}，仅作观察不单独作为加仓理由。"


def _build_sector_mappings(sector_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    items = [item for item in ((sector_payload or {}).get("items") or []) if isinstance(item, dict)]
    normalized: list[dict[str, Any]] = []
    defs_by_symbol = {str(row.get("symbol") or ""): row for row in US_SECTOR_PROXY_DEFS}
    for raw in items:
        symbol = str(raw.get("symbol") or raw.get("proxy") or "").upper()
        defn = defs_by_symbol.get(symbol, {})
        label = str(raw.get("label") or raw.get("name") or defn.get("label") or symbol).strip()
        pct = _safe_float(raw.get("change_pct"))
        mapping = raw.get("a_share_mapping") or defn.get("a_share_mapping") or []
        if isinstance(mapping, str):
            mapping = [x.strip() for x in re.split(r"[、,，/]+", mapping) if x.strip()]
        mapping = [str(x).strip() for x in mapping if str(x).strip()]
        direction, bias = _sector_bias(pct)
        normalized.append({
            "key": raw.get("key") or defn.get("key") or symbol.lower(),
            "us_sector": label,
            "proxy": symbol,
            "kind": raw.get("kind") or defn.get("kind") or "sector",
            "change_pct": pct,
            "change_pct_text": raw.get("change_pct_text") or _fmt_pct(pct),
            "tone": direction,
            "bias": bias,
            "a_share_mapping": mapping[:5],
            "strategy": _sector_action(label, pct, mapping),
        })
    positives = [row for row in normalized if row.get("tone") == "positive"]
    negatives = [row for row in normalized if row.get("tone") == "negative"]
    neutrals = [row for row in normalized if row.get("tone") == "neutral"]
    positives.sort(key=lambda row: float(row.get("change_pct") or 0), reverse=True)
    negatives.sort(key=lambda row: float(row.get("change_pct") or 0))
    neutrals.sort(key=lambda row: abs(float(row.get("change_pct") or 0)), reverse=True)
    selected = positives[:3] + negatives[:2]
    if not selected:
        selected = neutrals[:3]
    return selected[:5]


def _sector_summary_phrase(sector_mappings: list[dict[str, Any]]) -> str:
    positives = [row for row in sector_mappings if row.get("tone") == "positive"]
    negatives = [row for row in sector_mappings if row.get("tone") == "negative"]
    parts: list[str] = []
    if positives:
        parts.append("正映射看" + "、".join(str(row.get("us_sector") or "") for row in positives[:2] if row.get("us_sector")))
    if negatives:
        parts.append("负映射避开" + "、".join(str(row.get("us_sector") or "") for row in negatives[:2] if row.get("us_sector")))
    return "板块映射：" + "；".join(parts) + "。" if parts else ""


def _sector_guidance_line(sector_mappings: list[dict[str, Any]]) -> str:
    positives = [row for row in sector_mappings if row.get("tone") == "positive"]
    negatives = [row for row in sector_mappings if row.get("tone") == "negative"]
    parts: list[str] = []
    if positives:
        row = positives[0]
        mapping = "、".join((row.get("a_share_mapping") or [])[:3])
        if mapping:
            parts.append(f"{row.get('us_sector')}正映射到{mapping}")
    if negatives:
        row = negatives[0]
        mapping = "、".join((row.get("a_share_mapping") or [])[:3])
        if mapping:
            parts.append(f"{row.get('us_sector')}负映射的{mapping}降权")
    if not parts:
        return ""
    return "板块映射：" + "；".join(parts) + "，必须等 A 股竞价、资金流和板块联动确认。"


def _strategy_lines(
    tone: str,
    tone_reason: str,
    index_metrics: list[dict[str, Any]],
    metrics_by_key: dict[str, dict[str, Any]],
    sector_mappings: list[dict[str, Any]] | None = None,
) -> list[str]:
    lines = [tone_reason, _relative_style_line(index_metrics)]
    sector_line = _sector_guidance_line(sector_mappings or [])
    if sector_line:
        lines.append(sector_line)
    if tone == "offensive":
        lines.append("买入节奏：可允许试仓，但只做竞价有溢价、开盘有承接、板块联动的候选。")
        lines.append("选股方向：优先右侧趋势、科技成长映射和放量突破，弱分支不追。")
    elif tone == "balanced":
        lines.append("买入节奏：上午先做 1 笔以内确认仓，午后再看主线是否扩散。")
        lines.append("选股方向：外盘正反馈方向可加分，但必须叠加 A 股资金流和 BBI 右侧。")
    elif tone == "cautious":
        lines.append("买入节奏：降低预算，先观察开盘 15 分钟，单轮新仓不超过 1 笔。")
        lines.append("选股方向：只保留独立强、低位放量、板块有资金承接的候选，冲高回落剔除。")
    elif tone == "defensive":
        lines.append("买入节奏：默认防守，除非 A 股竞价强修复并放量确认，否则不主动扩仓。")
        lines.append("选股方向：候选股降级观察，优先处理破位/弱于板块的持仓风险。")
    else:
        lines.append("买入节奏：按静态风控和 A 股盘中信号执行，不因外盘单独加仓。")
        lines.append("选股方向：主线不明时只跟踪高辨识度、强承接、右侧结构。")
    lines.extend(_cross_asset_lines(metrics_by_key))
    return lines[:7]


def _metrics_prompt_lines(summary: dict[str, Any]) -> str:
    lines = []
    for item in summary.get("metrics") or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- {item.get('label') or ''}: {item.get('change_pct_text') or '--'}, "
            f"点位/价格 {item.get('value') or '--'}, 时间 {item.get('time') or '--'}"
        )
    return "\n".join(lines) if lines else "- 数据暂不可用"


def _sector_prompt_lines(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    for item in summary.get("sector_mappings") or []:
        if not isinstance(item, dict):
            continue
        mapping = "、".join(str(x) for x in (item.get("a_share_mapping") or [])[:5] if str(x).strip())
        lines.append(
            f"- {item.get('us_sector') or ''}({item.get('proxy') or ''}): "
            f"{item.get('change_pct_text') or '--'}，A股映射：{mapping or '相关板块'}，"
            f"策略含义：{item.get('strategy') or item.get('bias') or '观察'}"
        )
    return "\n".join(lines) if lines else "- 板块/主题 ETF 数据暂不可用"


def build_grok_messages(base_summary: dict[str, Any]) -> list[dict[str, str]]:
    target_cn_date = base_summary.get("target_cn_date") or ""
    target_us_date = base_summary.get("target_us_date") or ""
    deterministic_hint = "\n".join(str(x) for x in base_summary.get("guidance_lines") or [])
    system = (
        "你是牛牛1号的隔夜美股盘面策略分析师。"
        "任务是在北京时间交易日早上 08:00，基于已给出的隔夜美股/期货/大宗商品快照，"
        "为 A 股当天一整天的买卖选股策略生成盘面总结。"
        "不要编造未给出的具体新闻、宏观数据或公司事件；如需提到外盘影响，只能基于输入行情。"
        "必须输出严格 JSON，不要 Markdown，不要代码块，不要 URL。"
    )
    user = f"""
今天 A 股日期：{target_cn_date}
目标美股交易日：{target_us_date}
日期规则：周一显示上周五美股盘面；其他日期显示前一美股交易日。

已采集行情：
{_metrics_prompt_lines(base_summary)}

已采集美股板块/主题 ETF 映射：
{_sector_prompt_lines(base_summary)}

本地量化初判（只作参考，可修正）：
风险级别：{base_summary.get('tone_label') or '中性'}
摘要：{base_summary.get('summary') or ''}
指引：
{deterministic_hint or '无'}

请生成 JSON，schema 必须是：
{{
  "tone": "offensive|balanced|neutral|cautious|defensive",
  "tone_label": "进攻|平衡|中性|谨慎|防守",
  "summary": "一句完整中文盘面总结，必须含目标美股交易日和三大指数方向",
  "guidance_lines": [
    "用于今日 A 股买卖/选股的一条策略指引",
    "必须包含买入节奏",
    "必须包含选股方向",
    "必须包含卖出/风控"
  ]
}}

要求：
- guidance_lines 返回 4 到 7 条，短句但要具体可执行。
- 明确指导当天一整天的买卖选股策略，不只讲早盘。
- 如果上方美股板块/主题 ETF 映射可用，至少输出一条“板块映射：...”指引，必须基于给定映射方向；若暂不可用，写明板块映射暂缺，按 A 股自身确认。
- 如果纳指显著强于道指，可提科技成长/AI/半导体/算力映射；如果道指显著强于纳指，可提价值/顺周期。
- 如果黄金明显上涨，要提示避险；如果油价大幅波动，要提示能源/化工方向只做确认。
- 不输出任何投资保证、收益承诺、URL 或来源列表。
""".strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _strip_json_fence(content: str) -> str:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    if not text.startswith("{"):
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            text = match.group(0)
    return text


def parse_grok_summary_content(content: str) -> dict[str, Any]:
    payload = json.loads(_strip_json_fence(content))
    if not isinstance(payload, dict):
        raise ValueError("Grok summary JSON must be an object")
    tone = str(payload.get("tone") or "neutral").strip()
    if tone not in {"offensive", "balanced", "neutral", "cautious", "defensive"}:
        tone = "neutral"
    tone_labels = {
        "offensive": "进攻",
        "balanced": "平衡",
        "neutral": "中性",
        "cautious": "谨慎",
        "defensive": "防守",
    }
    guidance = payload.get("guidance_lines") or []
    if not isinstance(guidance, list):
        guidance = []
    cleaned_guidance = [str(line).strip().lstrip("·- ").strip() for line in guidance if str(line).strip()]
    return {
        "tone": tone,
        "tone_label": str(payload.get("tone_label") or tone_labels[tone]).strip() or tone_labels[tone],
        "summary": str(payload.get("summary") or "").strip(),
        "guidance_lines": cleaned_guidance[:8],
    }


def apply_grok_summary(base_summary: dict[str, Any]) -> dict[str, Any]:
    content = _call_grok_api(build_grok_messages(base_summary))
    parsed = parse_grok_summary_content(content)
    if not parsed.get("summary"):
        raise ValueError("Grok summary missing summary")
    if len(parsed.get("guidance_lines") or []) < 2:
        raise ValueError("Grok summary missing actionable guidance_lines")
    return {
        **base_summary,
        **parsed,
        "model_generated": True,
        "model_provider": "grok",
        "model": US_MARKET_SUMMARY_MODEL,
    }


def build_us_market_summary_from_indices(
    indices_payload: dict[str, Any] | None,
    now: datetime | None = None,
    sector_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now_cn = now.astimezone(CN_TZ) if now and now.tzinfo else (now.replace(tzinfo=CN_TZ) if now else datetime.now(CN_TZ))
    target = previous_us_session_date(now_cn.date())
    items = [item for item in ((indices_payload or {}).get("items") or []) if isinstance(item, dict)]

    metric_keys = [
        ("dow", "道琼斯指数"),
        ("nas", "纳斯达克指数"),
        ("spx", "标普500指数"),
        ("a50_fut", "富时中国A50期货"),
        ("spx_fut", "标普500期货"),
        ("nas_fut", "纳斯达克期货"),
        ("xau", "伦敦金"),
        ("brent", "布伦特原油"),
    ]
    metrics_by_key = {
        key: metric
        for key, label in metric_keys
        for metric in [_metric(_find_item(items, key), label)]
        if metric
    }
    index_metrics = [metrics_by_key[key] for key in ("dow", "nas", "spx") if key in metrics_by_key]
    tone, tone_label, tone_reason = _tone_from_indices(index_metrics)
    available = len(index_metrics) >= 2
    index_line = _index_sentence(index_metrics)
    sector_mappings = _build_sector_mappings(sector_payload)
    sector_phrase = _sector_summary_phrase(sector_mappings)
    summary = f"{target:%Y-%m-%d} 美股收盘：{index_line}。{tone_reason}"
    if sector_phrase:
        summary = f"{summary}{sector_phrase}"
    if not available:
        summary = f"{target:%Y-%m-%d} 美股盘面数据暂不完整，今日先按中性外盘背景处理。"
    guidance_lines = _strategy_lines(tone, tone_reason, index_metrics, metrics_by_key, sector_mappings) if available else [
        "美股数据暂缺，今日不基于外盘单独提高仓位。",
        "开盘后优先看 A 股竞价强弱、资金流和板块联动。",
    ]
    return {
        "available": available,
        "target_cn_date": now_cn.strftime("%Y-%m-%d"),
        "target_us_date": target.strftime("%Y-%m-%d"),
        "date_rule": "周一显示上周五美股盘面；其他日期显示前一美股交易日。",
        "generated_at": now_cn.strftime("%Y-%m-%d %H:%M:%S"),
        "source_generated_at": (indices_payload or {}).get("generated_at") or "",
        "tone": tone,
        "tone_label": tone_label,
        "summary": summary,
        "metrics": [metrics_by_key[key] for key, _ in metric_keys if key in metrics_by_key],
        "sector_mappings": sector_mappings,
        "sector_source_generated_at": (sector_payload or {}).get("generated_at") or "",
        "guidance_lines": guidance_lines,
    }


def _now_cn(now: datetime | None = None) -> datetime:
    return now.astimezone(CN_TZ) if now and now.tzinfo else (now.replace(tzinfo=CN_TZ) if now else datetime.now(CN_TZ))


def load_cached_summary_for_today(now: datetime | None = None) -> dict[str, Any] | None:
    now_cn = _now_cn(now)
    try:
        payload = json.loads(SUMMARY_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("target_cn_date") != now_cn.strftime("%Y-%m-%d"):
        return None
    if payload.get("target_us_date") != previous_us_session_date(now_cn).strftime("%Y-%m-%d"):
        return None
    payload["cached_archive"] = True
    return payload


def save_summary_cache(summary: dict[str, Any]) -> None:
    SUMMARY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUMMARY_CACHE_FILE.with_suffix(SUMMARY_CACHE_FILE.suffix + ".new")
    tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(SUMMARY_CACHE_FILE)


def fetch_us_market_summary(
    now: datetime | None = None,
    *,
    prefer_archive: bool = True,
    use_model: bool = True,
    strict_model: bool = False,
    indices_payload: dict[str, Any] | None = None,
    sector_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if prefer_archive:
        cached = load_cached_summary_for_today(now)
        if cached:
            return cached
    source_key = str((indices_payload or {}).get("generated_at") or "") if indices_payload is not None else ""
    sector_source_key = str((sector_payload or {}).get("generated_at") or "") if sector_payload is not None else ""
    cache_key = f"{previous_us_session_date((now or datetime.now(CN_TZ))).strftime('%Y-%m-%d')}:{'model' if use_model else 'rules'}:{source_key}:{sector_source_key}"
    current_ts = time.time()
    cached = _CACHE.get("data")
    if cached and _CACHE.get("key") == cache_key and current_ts - float(_CACHE.get("ts") or 0) < CACHE_TTL_SECONDS:
        return cached
    try:
        if indices_payload is None:
            from indices_dashboard_api import fetch_indices_data

            payload = fetch_indices_data()
        else:
            payload = indices_payload
        sectors = sector_payload
        if sectors is None and indices_payload is None:
            try:
                sectors = fetch_us_sector_snapshot(now)
            except Exception:
                sectors = {"items": []}
        data = build_us_market_summary_from_indices(payload, now=now, sector_payload=sectors)
        if use_model:
            try:
                data = apply_grok_summary(data)
            except Exception as model_exc:
                if strict_model:
                    raise
                data = {
                    **data,
                    "model_generated": False,
                    "model_provider": "grok",
                    "model": US_MARKET_SUMMARY_MODEL,
                    "model_error": f"{type(model_exc).__name__}: {model_exc}",
                    "guidance_lines": [
                        "Grok 生成暂不可用，以下为本地规则兜底，今日不因外盘单独提高仓位。",
                        *(data.get("guidance_lines") or []),
                    ][:8],
                }
    except Exception as exc:
        if strict_model:
            raise
        now_cn = now.astimezone(CN_TZ) if now and now.tzinfo else datetime.now(CN_TZ)
        data = {
            "available": False,
            "target_cn_date": now_cn.strftime("%Y-%m-%d"),
            "target_us_date": previous_us_session_date(now_cn).strftime("%Y-%m-%d"),
            "date_rule": "周一显示上周五美股盘面；其他日期显示前一美股交易日。",
            "generated_at": now_cn.strftime("%Y-%m-%d %H:%M:%S"),
            "tone": "neutral",
            "tone_label": "中性",
            "summary": "隔夜美股盘面暂不可用，今日先按 A 股自身信号执行。",
            "metrics": [],
            "sector_mappings": [],
            "guidance_lines": ["美股摘要生成失败，暂不基于外盘调整仓位。"],
            "error": f"{type(exc).__name__}: {exc}",
            "model_generated": False,
            "model_provider": "grok" if use_model else "",
            "model": US_MARKET_SUMMARY_MODEL if use_model else "",
        }
    _CACHE.update({"ts": current_ts, "key": cache_key, "data": data})
    return data


def build_us_market_report_text(summary: dict[str, Any]) -> str:
    target_us_date = summary.get("target_us_date") or "--"
    generated_at = summary.get("generated_at") or ""
    tone_label = summary.get("tone_label") or "中性"
    model_label = summary.get("model") if summary.get("model_generated") else "本地规则兜底"
    metrics = [m for m in (summary.get("metrics") or []) if isinstance(m, dict)]
    sector_mappings = [m for m in (summary.get("sector_mappings") or []) if isinstance(m, dict)]
    guidance = [str(line).strip() for line in (summary.get("guidance_lines") or []) if str(line).strip()]

    lines = [
        "牛牛大王，隔夜美股盘面总结来了：",
        "",
        f"📊 **美股概况** · {generated_at}",
        f"目标美股交易日 `{target_us_date}` | 风险级别 `{tone_label}`",
        f"生成模型 `{model_label}`",
        f"💬 {summary.get('summary') or '隔夜美股盘面暂不可用，今日先按 A 股自身信号执行。'}",
        "",
        "🌎 **关键资产**",
    ]
    if metrics:
        for item in metrics[:8]:
            label = item.get("label") or ""
            value = item.get("value") or "--"
            pct = item.get("change_pct_text") or "--"
            lines.append(f"`{label}` {pct} | {value}")
    else:
        lines.append("数据暂不可用")

    lines.extend([
        "",
        "🧭 **美股板块映射**",
    ])
    if sector_mappings:
        for item in sector_mappings[:5]:
            sector = item.get("us_sector") or ""
            proxy = item.get("proxy") or ""
            pct = item.get("change_pct_text") or "--"
            mapping = "、".join(str(x) for x in (item.get("a_share_mapping") or [])[:5] if str(x).strip())
            strategy = item.get("strategy") or item.get("bias") or "仅作观察"
            lines.append(f"`{sector}({proxy})` {pct} → A股：{mapping or '相关板块'}；{strategy}")
    else:
        lines.append("板块/主题 ETF 数据暂不可用，今日先按指数和 A 股自身板块确认。")

    lines.extend([
        "",
        "🎯 **今日买卖指引**",
        f"· 风险级别：{tone_label}",
    ])
    if guidance:
        lines.extend(f"· {line}" for line in guidance[:7])
    else:
        lines.append("· 美股摘要暂缺，今日不基于外盘单独提高仓位。")

    lines.extend([
        "",
        "⚠️ **风险**",
        "· 隔夜美股只作为今日外盘背景，盘中必须以 A 股竞价、资金流和板块联动确认。",
        "· 数据为快照，以交易软件为准。",
    ])
    if summary.get("error"):
        lines.append(f"ℹ️ {summary.get('error')}")
    return "\n".join(lines).strip()


def is_a_share_trading_day_for_summary(now: datetime) -> bool:
    try:
        from a_share_calendar import is_a_share_trading_day

        return bool(is_a_share_trading_day(now))
    except Exception:
        return now.astimezone(CN_TZ).weekday() < 5


def store_us_market_summary(now: datetime | None = None) -> str | None:
    run_dt = _now_cn(now)
    if not is_a_share_trading_day_for_summary(run_dt):
        return None
    summary = fetch_us_market_summary(run_dt, prefer_archive=False, use_model=True, strict_model=True)
    save_summary_cache(summary)
    text = build_us_market_report_text(summary)
    from market_report_store import store_market_report

    store_market_report(text, job_id=JOB_ID, title=JOB_TITLE, run_dt=run_dt)
    return text


if __name__ == "__main__":
    import json

    parser = argparse.ArgumentParser(description=JOB_TITLE)
    parser.add_argument("--store", action="store_true", help="写入盘面监控消息数据库")
    parser.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    args = parser.parse_args()
    if args.store:
        try:
            report = store_us_market_summary()
        except Exception as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            sys.exit(1)
        print(report or "")
    else:
        print(json.dumps(fetch_us_market_summary(), ensure_ascii=False, indent=2))
