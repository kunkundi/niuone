#!/usr/bin/env python3
"""Grok-assisted A-share market monitor summaries."""
from __future__ import annotations

import json
import os
import re
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from niuone_paths import get_dashboard_env_file, get_dashboard_home


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def load_dashboard_env() -> None:
    allowed = {
        "A_SHARE_MODEL_SUMMARY_ENABLED",
        "A_SHARE_MODEL_SUMMARY_MODEL",
        "A_SHARE_MODEL_SUMMARY_CONTEXT_LENGTH",
        "A_SHARE_MODEL_SUMMARY_MAX_TOKENS",
        "A_SHARE_MODEL_SUMMARY_BASE_URL",
        "A_SHARE_MODEL_SUMMARY_API_KEY",
        "A_SHARE_MODEL_SUMMARY_DEADLINE_SECONDS",
        "A_SHARE_MODEL_SUMMARY_REQUEST_TIMEOUT_SECONDS",
        "A_SHARE_GROK_SUMMARY_ENABLED",
        "A_SHARE_GROK_SUMMARY_MODEL",
        "A_SHARE_GROK_SUMMARY_MAX_TOKENS",
        "A_SHARE_GROK_SUMMARY_BASE_URL",
        "A_SHARE_GROK_SUMMARY_API_KEY",
        "A_SHARE_GROK_SUMMARY_DEADLINE_SECONDS",
        "A_SHARE_GROK_SUMMARY_REQUEST_TIMEOUT_SECONDS",
        "DASHBOARD_GROK_MODEL",
        "DASHBOARD_GROK_CONTEXT_LENGTH",
        "DASHBOARD_GROK_BASE_URL",
        "DASHBOARD_GROK_API_KEY",
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


A_SHARE_MODEL_SUMMARY_MODEL = (
    os.environ.get("A_SHARE_MODEL_SUMMARY_MODEL")
    or os.environ.get("A_SHARE_GROK_SUMMARY_MODEL")
    or os.environ.get("DASHBOARD_GROK_MODEL")
    or "grok-4.20-multi-agent-xhigh"
)
A_SHARE_MODEL_SUMMARY_DEADLINE_SECONDS = _int_env(
    "A_SHARE_MODEL_SUMMARY_DEADLINE_SECONDS",
    _int_env("A_SHARE_GROK_SUMMARY_DEADLINE_SECONDS", 60, min_value=15),
    min_value=15,
)
A_SHARE_MODEL_SUMMARY_REQUEST_TIMEOUT_SECONDS = _int_env(
    "A_SHARE_MODEL_SUMMARY_REQUEST_TIMEOUT_SECONDS",
    _int_env("A_SHARE_GROK_SUMMARY_REQUEST_TIMEOUT_SECONDS", 45, min_value=10),
    min_value=10,
)
A_SHARE_MODEL_SUMMARY_CONTEXT_LENGTH = _token_count_env(
    "A_SHARE_MODEL_SUMMARY_CONTEXT_LENGTH",
    "DASHBOARD_GROK_CONTEXT_LENGTH",
    default=0,
)
A_SHARE_MODEL_SUMMARY_MAX_TOKENS = _token_count_env(
    "A_SHARE_MODEL_SUMMARY_MAX_TOKENS",
    "A_SHARE_GROK_SUMMARY_MAX_TOKENS",
    default=1800,
)


def a_share_grok_enabled() -> bool:
    raw = os.environ.get("A_SHARE_MODEL_SUMMARY_ENABLED")
    if raw is None:
        raw = os.environ.get("A_SHARE_GROK_SUMMARY_ENABLED", "1")
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


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
        os.environ.get("A_SHARE_MODEL_SUMMARY_BASE_URL")
        or os.environ.get("A_SHARE_GROK_SUMMARY_BASE_URL")
        or os.environ.get("DASHBOARD_GROK_BASE_URL")
        or os.environ.get("CROSSDESK_BASE_URL")
    )
    env_api_key = (
        os.environ.get("A_SHARE_MODEL_SUMMARY_API_KEY")
        or os.environ.get("A_SHARE_GROK_SUMMARY_API_KEY")
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


def call_grok_api(messages: list[dict[str, str]], *, max_tokens: int = A_SHARE_MODEL_SUMMARY_MAX_TOKENS) -> str:
    base_url, api_key = _get_grok_credentials()
    if not base_url or not api_key:
        raise RuntimeError("model summary credentials not found: set A_SHARE_MODEL_SUMMARY_BASE_URL/API_KEY or DASHBOARD_GROK_BASE_URL/API_KEY")
    body = json.dumps({
        "model": A_SHARE_MODEL_SUMMARY_MODEL,
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
    deadline = time.monotonic() + max(15, A_SHARE_MODEL_SUMMARY_DEADLINE_SECONDS)
    last_err: Exception | None = None
    for attempt in range(1, 3):
        remaining = deadline - time.monotonic()
        if remaining <= 5:
            break
        try:
            timeout_seconds = min(max(10, A_SHARE_MODEL_SUMMARY_REQUEST_TIMEOUT_SECONDS), max(10, remaining - 2))
            with urlopen(req, timeout=timeout_seconds, context=_SSL_CONTEXT) as resp:
                payload = json.loads(resp.read().decode("utf-8", "ignore"))
                content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
                if str(content or "").strip():
                    return str(content).strip()
                last_err = RuntimeError("model returned empty content")
        except Exception as exc:
            last_err = exc
            if attempt < 2 and _is_transient_error(exc) and (deadline - time.monotonic()) > 8:
                time.sleep(min(2 * attempt, max(0, deadline - time.monotonic() - 5)))
                continue
            break
    if last_err:
        raise RuntimeError(f"model summary call failed: {last_err}")
    raise RuntimeError("model summary call did not complete before the local deadline")


def build_a_share_grok_messages(local_report: str, *, title: str) -> list[dict[str, str]]:
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


def parse_a_share_grok_content(content: str) -> dict[str, Any]:
    payload = json.loads(_strip_json_fence(content))
    if not isinstance(payload, dict):
        raise ValueError("A-share Grok summary JSON must be an object")
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

    def clean_lines(value: Any, limit: int) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(line).strip().lstrip("·- ").strip() for line in value if str(line).strip()][:limit]

    return {
        "tone": tone,
        "tone_label": str(payload.get("tone_label") or tone_labels[tone]).strip() or tone_labels[tone],
        "summary": str(payload.get("summary") or "").strip(),
        "guidance_lines": clean_lines(payload.get("guidance_lines"), 8),
        "focus_lines": clean_lines(payload.get("focus_lines"), 4),
        "risk_lines": clean_lines(payload.get("risk_lines"), 4),
    }


def _remove_original_guidance(local_report: str) -> str:
    lines = str(local_report or "").splitlines()
    out: list[str] = []
    in_guidance = False
    for line in lines:
        clean = line.strip()
        if any(key in clean for key in ("今日买卖指引", "午后买卖指引", "次日买卖计划", "次日盘前指引", "盘前买卖指引")):
            in_guidance = True
            continue
        if in_guidance and clean.startswith(("📊", "🔥", "💰", "⚡", "📈", "👀", "📌", "🧭", "⚠️", "🌡️", "💡")) and "**" in clean:
            in_guidance = False
        if in_guidance:
            continue
        out.append(line)
    text = "\n".join(out).strip()
    text = re.sub(r"^牛牛大王，[^。\n]*来了：\s*", "", text).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def render_grok_a_share_report(local_report: str, parsed: dict[str, Any], *, title: str) -> str:
    summary = parsed.get("summary") or "Grok 已参与盘面复核，但未返回摘要。"
    tone_label = parsed.get("tone_label") or "中性"
    guidance = [str(x).strip().lstrip("·- ").strip() for x in parsed.get("guidance_lines") or [] if str(x).strip()]
    focus = [str(x).strip().lstrip("·- ").strip() for x in parsed.get("focus_lines") or [] if str(x).strip()]
    risks = [str(x).strip().lstrip("·- ").strip() for x in parsed.get("risk_lines") or [] if str(x).strip()]
    if not any(line.startswith("风险级别") for line in guidance):
        guidance.insert(0, f"风险级别：{tone_label}")
    title_text = str(title or "")
    guidance_title = "次日盘前指引" if "盘后" in title_text else ("午后买卖指引" if "午盘" in title_text else "今日买卖指引")

    lines = [
        f"牛牛大王，{title}来了：",
        "",
        "🤖 **模型盘面总结**",
        f"生成模型 `{A_SHARE_MODEL_SUMMARY_MODEL}`",
        f"💬 {summary}",
        "",
        f"🎯 **{guidance_title}**",
    ]
    lines.extend(f"· {line}" for line in guidance[:8])
    if focus:
        lines.extend(["", "👀 **观察重点**"])
        lines.extend(f"· {line}" for line in focus[:4])
    if risks:
        lines.extend(["", "⚠️ **模型风险提醒**"])
        lines.extend(f"· {line}" for line in risks[:4])
    snapshot = _remove_original_guidance(local_report)
    if snapshot:
        lines.extend(["", "🧾 **本地规则快照**", snapshot])
    return "\n".join(lines).strip()


def apply_grok_to_a_share_report(local_report: str, *, title: str, strict: bool = False) -> str:
    if not local_report.strip() or not a_share_grok_enabled():
        return local_report
    try:
        content = call_grok_api(build_a_share_grok_messages(local_report, title=title))
        parsed = parse_a_share_grok_content(content)
        if not parsed.get("summary"):
            raise ValueError("A-share Grok summary missing summary")
        if len(parsed.get("guidance_lines") or []) < 2:
            raise ValueError("A-share Grok summary missing actionable guidance_lines")
        return render_grok_a_share_report(local_report, parsed, title=title)
    except Exception as exc:
        if strict:
            raise
        return (
            f"{local_report.rstrip()}\n\n"
            f"ℹ️ 模型盘面总结暂不可用，已使用本地规则兜底：{type(exc).__name__}: {exc}"
        )
