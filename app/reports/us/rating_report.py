#!/usr/bin/env python3
"""Generate and store a US institutional buy-rating daily report.

Usage:
    us_rating_report.py              # generates, stores, and prints report
    us_rating_report.py --store-only
    us_rating_report.py --test       # quick smoke test
"""

from __future__ import annotations

import os
import re
import ssl
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.request import urlopen

from core.model_api import build_model_request, request_model
from niuone_paths import get_dashboard_env_file, get_dashboard_home

# Crossdesk has intermittent SSL record-layer failures with Python's
# default SSL context.  Disable certificate verification for this
# internal-only tool.  The API key still authenticates the request.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def load_dashboard_env() -> None:
    allowed = {
        "DASHBOARD_GROK_MODEL",
        "DASHBOARD_GROK_API_MODE",
        "DASHBOARD_GROK_CONTEXT_LENGTH",
        "DASHBOARD_GROK_BASE_URL",
        "DASHBOARD_GROK_API_KEY",
        "US_RATING_MODEL",
        "US_RATING_CONTEXT_LENGTH",
        "US_RATING_MAX_TOKENS",
        "US_RATING_BASE_URL",
        "US_RATING_API_KEY",
        "US_RATING_DEADLINE_SECONDS",
        "US_RATING_REQUEST_TIMEOUT_SECONDS",
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
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
os.environ.setdefault("DASHBOARD_HOME", str(DASHBOARD_HOME))
CN_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

try:
    import push_history
except Exception:
    push_history = None

_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE

JOB_ID = "fd0b807138f4"
JOB_NAME = "每日美股机构买入评级汇报"
CONFIG_PATH = Path(os.environ.get("DASHBOARD_CONFIG") or str(DASHBOARD_HOME / "config.yaml")).expanduser()
US_RATING_MODEL = os.environ.get("US_RATING_MODEL") or os.environ.get("DASHBOARD_GROK_MODEL") or "grok-4.20-multi-agent-xhigh"
GROK_API_MODE = os.environ.get("DASHBOARD_GROK_API_MODE") or "auto"


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


US_RATING_DEADLINE_SECONDS = _int_env("US_RATING_DEADLINE_SECONDS", 240, min_value=30)
US_RATING_REQUEST_TIMEOUT_SECONDS = _int_env("US_RATING_REQUEST_TIMEOUT_SECONDS", 120, min_value=10)
US_RATING_CONTEXT_LENGTH = _token_count_env("US_RATING_CONTEXT_LENGTH", "DASHBOARD_GROK_CONTEXT_LENGTH", default=128000)
US_RATING_MAX_TOKENS = _token_count_env("US_RATING_MAX_TOKENS", default=4096)


def _load_config():
    import yaml
    if not CONFIG_PATH.exists():
        return {}
    return yaml.safe_load(CONFIG_PATH.read_text()) or {}


def _get_crossdesk_credentials():
    env_base_url = os.environ.get("US_RATING_BASE_URL") or os.environ.get("DASHBOARD_GROK_BASE_URL") or os.environ.get("CROSSDESK_BASE_URL")
    env_api_key = os.environ.get("US_RATING_API_KEY") or os.environ.get("DASHBOARD_GROK_API_KEY") or os.environ.get("CROSSDESK_API_KEY")
    if env_base_url and env_api_key:
        return env_base_url.rstrip("/"), env_api_key

    cfg = _load_config()
    for p in cfg.get("custom_providers", []):
        if "crossdesk.ccwu.cc" in p.get("base_url", ""):
            return p["base_url"].rstrip("/"), p["api_key"]
    m = cfg.get("model", {})
    return m.get("base_url", "").rstrip("/"), m.get("api_key", "")


def _is_transient_error(err):
    if isinstance(err, TimeoutError):
        return True
    if isinstance(err, HTTPError):
        return err.code in {408, 429, 500, 502, 503, 504}
    if isinstance(err, URLError):
        return True
    text = str(err).lower()
    return any(s in text for s in ("timed out", "timeout", "temporarily", "connection reset", "empty stream", "ssl"))


def _call_api(base_url, api_key, messages, max_tokens=US_RATING_MAX_TOKENS):
    model_request = build_model_request(
        base_url,
        US_RATING_MODEL,
        messages,
        max_tokens=max_tokens,
        api_mode=GROK_API_MODE,
        tools=[{"type": "web_search"}],
        reasoning={"effort": "low"},
        stream=False,
    )
    last_err = None
    # Keep our own wall clock bounded so slow upstream model/web-search runs end
    # as a clear cron failure instead of a stale dashboard.
    deadline = time.monotonic() + max(30, US_RATING_DEADLINE_SECONDS)
    for attempt in range(1, 4):
        remaining = deadline - time.monotonic()
        if remaining <= 5:
            break
        try:
            timeout_seconds = min(max(10, US_RATING_REQUEST_TIMEOUT_SECONDS), max(10, remaining - 2))
            parsed = request_model(
                model_request,
                api_key,
                timeout=timeout_seconds,
                opener=urlopen,
                ssl_context=_SSL_CONTEXT,
            )
            if str(parsed.content or "").strip():
                return parsed.content
            last_err = RuntimeError("API returned empty content")
        except Exception as e:
            last_err = e
            if attempt < 3 and (deadline - time.monotonic()) > 8:
                time.sleep(min(3 * attempt, max(0, deadline - time.monotonic() - 5)))
    if last_err:
        raise RuntimeError(f"API call failed after 3 attempts: {last_err}")
    raise RuntimeError("API call did not complete before the local deadline")


def build_system_prompt():
    return (
        "[IMPORTANT: You are running as a scheduled cron job. "
        "DELIVERY: Your final response will be stored in the local dashboard database. "
        "Just produce the report body; the script handles database storage. "
        'SILENT: If there is genuinely nothing new to report, respond with exactly "[SILENT]". '
        "Never combine [SILENT] with content.]"
    )


def build_user_prompt():
    beijing_now = datetime.now(timezone.utc).astimezone(CN_TZ)
    date_str = beijing_now.strftime("%Y\u5e74%m\u6708%d\u65e5")  # 年月日

    LQ = "\u201c"  # "
    RQ = "\u201d"  # "

    return (
        f"\u4f60\u662f\u725b\u725b1\u53f7\u3002\u6bcf\u5929\u5317\u4eac\u65f6\u95f4 11:00 "
        f"\u4e3a\u7528\u6237\uff08\u79f0\u547c{LQ}\u725b\u725b\u5927\u738b{RQ}\uff09"
        f"\u6c47\u62a5\u8fc7\u53bb\u7ea624\u5c0f\u65f6/\u6700\u8fd1\u4e00\u4e2a\u7f8e\u80a1\u4ea4\u6613\u65e5\u4e2d\uff0c"
        f"\u534e\u5c14\u8857\u673a\u6784/\u5206\u6790\u5e08\u5bf9\u7f8e\u80a1\u7ed9\u51fa\u4e70\u5165\u503e\u5411\u8bc4\u7ea7\u7684\u91cd\u70b9\u80a1\u7968\u3002\n\n"

        f"\u76ee\u6807\uff1a\u7b5b\u9009{LQ}\u673a\u6784\u8bc4\u7ea7\u4e70\u5165\u7684\u7f8e\u80a1{RQ}\uff0c"
        f"\u91cd\u70b9\u5173\u6ce8\uff1a\u65b0\u8986\u76d6 Buy/Overweight/Outperform\u3001"
        f"\u4e0a\u8c03\u81f3\u4e70\u5165\u503e\u5411\u3001\u7ef4\u6301\u4e70\u5165\u4e14\u663e\u8457\u4e0a\u8c03\u76ee\u6807\u4ef7\u3001"
        f"\u591a\u4e2a\u673a\u6784\u96c6\u4e2d\u770b\u591a\u3001\u6216\u5927\u884c/\u77e5\u540d\u5206\u6790\u5e08"
        f"\u7ed9\u51fa\u9ad8\u7f6e\u4fe1\u5ea6\u4e70\u5165\u89c2\u70b9\u3002\n\n"

        f"\u8bf7\u4f7f\u7528\u5b9e\u65f6\u7f51\u9875\u68c0\u7d22\uff0c\u4f18\u5148\u6765\u6e90\u5305\u62ec "
        f"MarketBeat analyst ratings\u3001TipRanks\u3001TheFly\u3001"
        f"Benzinga Analyst Ratings\u3001Investing.com analyst ratings\u3001"
        f"CNBC/Reuters/MarketWatch/Yahoo Finance/Seeking Alpha "
        f"\u7b49\u516c\u5f00\u4fe1\u606f\u3002\u4e0d\u8981\u7f16\u9020\u6570\u636e\uff1b\u5982\u679c\u6765\u6e90\u4e0d\u8db3\uff0c"
        f"\u8bf7\u660e\u786e\u5199{LQ}\u672a\u68c0\u7d22\u5230\u8db3\u591f\u53ef\u9760\u6765\u6e90{RQ}\u3002\n\n"

        f"\u8f93\u51fa\u4e2d\u6587\uff0c\u5199\u5165{LQ}\u4e70\u5165\u8bc4\u7ea7{RQ}dashboard \u5f52\u6863\u3002\n\n"

        f"\u91cd\u8981\u8981\u6c42\uff1a\u5fc5\u987b\u662f\u5b8c\u6574\u65e5\u62a5\uff0c"
        f"\u4e0d\u8981\u4e3a\u4e86\u7f29\u77ed\u800c\u7701\u7565\u5173\u952e\u5185\u5bb9\uff1b"
        f"\u4f46\u4e0d\u8981\u8f93\u51fa\u4efb\u4f55 URL\u3001\u7f51\u9875\u94fe\u63a5\u3001"
        f"Markdown \u94fe\u63a5\u6216\u88f8\u94fe\u63a5\u3002"
        f"dashboard \u4e2d\u9700\u8981\u4fdd\u7559\u5b8c\u6574\u65e5\u62a5\uff0c\u4e0d\u8981\u727a\u7272\u5173\u952e\u5185\u5bb9\u3002\n\n"

        f"\u4e25\u683c\u683c\u5f0f\u8981\u6c42\uff1a\u4e3a\u4e86\u8ba9 dashboard \u81ea\u52a8\u89e3\u6790\u4e3a\u8868\u683c\uff0c"
        f"\u6bcf\u53ea\u80a1\u7968\u5fc5\u987b\u4f7f\u7528\u4e0b\u9762\u7684\u591a\u884c\u5b57\u6bb5\u5757\uff0c"
        f"\u4e0d\u8981\u5199\u6210\u4e00\u884c\u957f\u53e5\uff0c"
        f"\u4e0d\u8981\u7528{LQ}1. **DDOG\uff08Datadog\uff09** \u2014 ...{RQ}\u8fd9\u79cd\u683c\u5f0f\u3002\n\n"

        f"\u6807\u9898\uff1a\u725b\u725b\u5927\u738b\uff0c\u7f8e\u80a1\u673a\u6784\u4e70\u5165\u8bc4\u7ea7\u65e5\u62a5\uff08{date_str}\uff09\n\n"

        f"- TICKER / Company Name\n"
        f"  \u673a\u6784/\u5206\u6790\u5e08\uff1a\u673a\u6784\u540d\u79f0 / \u5206\u6790\u5e08\u59d3\u540d\uff08\u5982\u6709\uff09\n"
        f"  \u8bc4\u7ea7\u52a8\u4f5c\uff1a\u4f8b\u5982 \u4ece Hold \u4e0a\u8c03\u81f3 Buy / \u65b0\u8986\u76d6 Overweight / \u7ef4\u6301 Buy \u5e76\u4e0a\u8c03\u76ee\u6807\u4ef7\n"
        f"  \u76ee\u6807\u4ef7\uff1a\u4f8b\u5982 300\u7f8e\u5143\uff1b\u5982\u65e0\u53ef\u9760\u76ee\u6807\u4ef7\u5199{LQ}\u672a\u62ab\u9732{RQ}\n"
        f"  \u6838\u5fc3\u7406\u7531/\u50ac\u5316\u5242\uff1a\u5b8c\u6574\u8bf4\u660e\u770b\u591a\u903b\u8f91\n"
        f"  \u98ce\u9669\u70b9\uff1a\u5b8c\u6574\u8bf4\u660e\u4e3b\u8981\u98ce\u9669\n"
        f"  \u9002\u5408\u5173\u6ce8\u7c7b\u578b\uff1a\u77ed\u7ebf\u50ac\u5316 / \u4e2d\u7ebf\u8d8b\u52bf / \u957f\u671f\u914d\u7f6e\n\n"

        f"\u6570\u91cf\uff1a\u5148\u7ed9 5-10 \u6761\u6700\u503c\u5f97\u5173\u6ce8\u7684\u80a1\u7968\uff0c"
        f"\u6bcf\u6761\u90fd\u5fc5\u987b\u5305\u542b\u4ee5\u4e0a 6 \u4e2a\u5b57\u6bb5\u3002\n\n"

        f"\u6700\u540e\u7ed9{LQ}\u725b\u725b1\u53f7\u7ed3\u8bba{RQ}\uff1a\u6309\u770b\u597d\u7a0b\u5ea6\u5206\u4e3a"
        f"\u3010\u4f18\u5148\u8ddf\u8e2a\u3011\u3010\u89c2\u5bdf\u3011\u3010\u8c28\u614e\u3011\uff0c"
        f"\u6bcf\u6863\u8bf4\u660e\u7406\u7531\u3002\n"

        f"\u53c2\u8003\u6765\u6e90\u53ea\u5217\u6765\u6e90\u540d\u79f0\uff0c\u4f8b\u5982 MarketBeat\u3001TipRanks\u3001"
        f"Benzinga\u3001TheFly\u3001Reuters \u7b49\uff1b"
        f"\u4e0d\u8981\u8f93\u51fa\u4efb\u4f55 URL\u3001\u7f51\u9875\u94fe\u63a5\u3001Markdown \u94fe\u63a5\u6216\u88f8\u94fe\u63a5\u3002"
    )


def clean_report_content(content: str) -> str:
    content = (content or "").strip()
    if content.startswith("```"):
        lines = content.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    if content.startswith("[SILENT]") and len(content) > 20:
        content = content[len("[SILENT]"):].strip()
    return content


def generate_report(test_mode: bool = False) -> str:
    base_url, api_key = _get_crossdesk_credentials()
    if not base_url or not api_key:
        print(f"ERROR: crossdesk credentials not found in {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)

    if test_mode:
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Reply concisely."},
            {"role": "user", "content": "List 2 recent US stock analyst upgrades, brief: - TICKER: action by firm"},
        ]
        max_tokens = 500
    else:
        messages = [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": build_user_prompt()},
        ]
        max_tokens = US_RATING_MAX_TOKENS

    return clean_report_content(_call_api(base_url, api_key, messages, max_tokens))


def write_report_to_db(content: str, now: datetime | None = None) -> int:
    if push_history is None:
        raise RuntimeError("push history database module is unavailable")
    if now is None:
        now = datetime.now(timezone.utc)
    local_dt = now.astimezone(CN_TZ)
    run_key = os.environ.get("NIUONE_CRON_RUN_KEY") or f"{JOB_ID}:{local_dt.strftime('%Y-%m-%d_%H-%M-%S')}"
    source_id = f"cron_output_{JOB_ID}"
    message = {
        "id": push_history.stable_id("us_ratings", JOB_ID, run_key),
        "timestamp": now.timestamp(),
        "time_text": local_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "category": "us_ratings",
        "source_type": "us_ratings",
        "source_id": source_id,
        "source_label": "美股机构买入评级",
        "platform": "dashboard",
        "platform_label": "Dashboard",
        "chat": "us-ratings",
        "external_id": run_key,
        "title": "美股机构买入评级",
        "content": content,
        "chars": len(content),
        "matched": True,
        "kind": "cron_output",
        "delivery": {"mode": "dashboard_database_only", "job_id": JOB_ID},
        "metadata": {"job_name": JOB_NAME, "run_key": run_key},
    }
    count = push_history.upsert_many([message])
    if count != 1:
        raise RuntimeError(f"US rating database write returned {count}")
    return count


def main():
    test_mode = "--test" in sys.argv
    store_only = "--store-only" in sys.argv

    try:
        content = generate_report(test_mode=test_mode)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        print(f"ERROR: {reason}", file=sys.stderr)
        sys.exit(1)

    if not content:
        reason = "API returned empty content"
        print(f"ERROR: {reason}", file=sys.stderr)
        sys.exit(1)

    if content.strip() == "[SILENT]":
        print("ERROR: API returned [SILENT] instead of a report", file=sys.stderr)
        sys.exit(1)

    if not test_mode:
        now = datetime.now(timezone.utc)
        try:
            write_report_to_db(content, now=now)
        except Exception as exc:
            print(f"ERROR: database write failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            sys.exit(1)

    if store_only:
        return
    print(content)


if __name__ == "__main__":
    main()
