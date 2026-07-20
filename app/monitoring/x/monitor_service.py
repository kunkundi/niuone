#!/usr/bin/env python3
import json
import os
import re
import html
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import concurrent.futures
from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.model_api import ModelResponseParseError, build_model_request, request_model
from niuone_paths import get_dashboard_env_file, get_dashboard_home

if __package__ == "app":
    from .monitoring.x.config import (
        env_token_count as _env_token_count,
        parse_watchlist_accounts as _parse_watchlist_accounts,
        watchlist_accounts_from_state as _watchlist_accounts_from_state,
    )
    from .monitoring.x.content import (
        extract_media_from_value,
        extract_x_media,
        first_meta_content,
        has_recovered_context,
        is_x_post_media_url,
        media_type_from_url,
        merge_media_items,
        needs_context_repair,
        normalize_media_url,
        parse_social_posting,
        should_hold_for_context as _should_hold_for_context,
    )
    from .monitoring.x.formatting import display_text, fmt_media_items, fmt_missing_context, fmt_post, fmt_text_box
    from .monitoring.x.state import (
        canonical_post_time,
        choose_latest_value,
        compact_sent_context_entry,
        is_newer_post,
        job_ran_after_pending,
        latest_from_items,
        looks_like_x_handle,
        merge_latest,
        merge_seen_ids,
        needs_context_hydration,
        normalize_post_time,
        parse_any_datetime,
        parse_iso,
        parse_post_time,
        pending_is_already_latest,
        post_time_is_implausible,
        sent_context_key,
        should_retry_sent_context,
    )
else:
    from monitoring.x.config import (
        env_token_count as _env_token_count,
        parse_watchlist_accounts as _parse_watchlist_accounts,
        watchlist_accounts_from_state as _watchlist_accounts_from_state,
    )
    from monitoring.x.content import (
        extract_media_from_value,
        extract_x_media,
        first_meta_content,
        has_recovered_context,
        is_x_post_media_url,
        media_type_from_url,
        merge_media_items,
        needs_context_repair,
        normalize_media_url,
        parse_social_posting,
        should_hold_for_context as _should_hold_for_context,
    )
    from monitoring.x.formatting import display_text, fmt_media_items, fmt_missing_context, fmt_post, fmt_text_box
    from monitoring.x.state import (
        canonical_post_time,
        choose_latest_value,
        compact_sent_context_entry,
        is_newer_post,
        job_ran_after_pending,
        latest_from_items,
        looks_like_x_handle,
        merge_latest,
        merge_seen_ids,
        needs_context_hydration,
        normalize_post_time,
        parse_any_datetime,
        parse_iso,
        parse_post_time,
        pending_is_already_latest,
        post_time_is_implausible,
        sent_context_key,
        should_retry_sent_context,
    )

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def load_dashboard_env() -> None:
    allowed = {
        "DASHBOARD_GROK_MODEL",
        "DASHBOARD_GROK_API_MODE",
        "DASHBOARD_GROK_CONTEXT_LENGTH",
        "DASHBOARD_GROK_BASE_URL",
        "DASHBOARD_GROK_API_KEY",
        "X_WATCHLIST_MODEL",
        "X_WATCHLIST_CONTEXT_LENGTH",
        "X_WATCHLIST_MAX_TOKENS",
        "X_WATCHLIST_BASE_URL",
        "X_WATCHLIST_API_KEY",
        "X_WATCHLIST_ACCOUNTS",
        "X_WATCHLIST_REQUEST_TIMEOUT_SECONDS",
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

try:
    import push_history
except Exception:  # The monitor keeps retry state when database storage is unavailable.
    push_history = None

try:
    import yaml
except Exception:
    yaml = None

def parse_watchlist_accounts(value: str | None) -> list[str]:
    return _parse_watchlist_accounts(value)


def env_token_count(*names: str, default: int = 0) -> int:
    return _env_token_count(*names, default=default, environ=os.environ)


def configured_max_tokens(default: int) -> int:
    return X_WATCHLIST_MAX_TOKENS or default


MODEL = os.environ.get("X_WATCHLIST_MODEL") or os.environ.get("DASHBOARD_GROK_MODEL") or "grok-4.20-multi-agent-xhigh"
GROK_API_MODE = os.environ.get("DASHBOARD_GROK_API_MODE") or "auto"
X_WATCHLIST_CONTEXT_LENGTH = env_token_count("X_WATCHLIST_CONTEXT_LENGTH", "DASHBOARD_GROK_CONTEXT_LENGTH", default=128000)
X_WATCHLIST_MAX_TOKENS = env_token_count("X_WATCHLIST_MAX_TOKENS", default=4096)
CROSSDESK_PROVIDER_NAME = "Crossdesk.ccwu.cc"
CROSSDESK_PROVIDER_NAME_LOWER = CROSSDESK_PROVIDER_NAME.lower()
TEMPORARY_HTTP_CODES = {408, 429, 500, 502, 503, 504}
# Keep this comfortably below the launchd daemon's inner timeout.
# Grok-backed X fetching can intermittently return empty/non-JSON content or run slow;
# those should be treated as transient poll misses, not user-visible job failures.
TOTAL_DEADLINE_SECONDS = 135
try:
    REQUEST_TIMEOUT_SECONDS = max(
        8,
        min(120, int(os.environ.get("X_WATCHLIST_REQUEST_TIMEOUT_SECONDS") or "45")),
    )
except (TypeError, ValueError):
    REQUEST_TIMEOUT_SECONDS = 45
DETAIL_REQUEST_TIMEOUT_SECONDS = 8
REPAIR_REQUEST_TIMEOUT_SECONDS = 10
HELD_CONTEXT_REPAIR_TIMEOUT_SECONDS = 8
MAX_CONTEXT_REPAIR_ITEMS = 4
CONTEXT_REPAIR_RETRY_ROUNDS = 2
CONTEXT_REPAIR_RETRY_SLEEP_SECONDS = 2
HTML_FETCH_ATTEMPTS = 2
MEDIA_HTML_HYDRATE_TIMEOUT_SECONDS = 5
MAX_MEDIA_HTML_HYDRATE_ITEMS = 6
MAX_SENT_CONTEXT_REPAIR_ITEMS = 2
MAX_SENT_CONTEXT_REPAIR_QUEUE = 40
MAX_SENT_CONTEXT_REPAIR_ATTEMPTS = 8
SENT_CONTEXT_REPAIR_COOLDOWN_MINUTES = 20
SENT_CONTEXT_REPAIR_LOOKBACK_HOURS = 72
MAX_ATTEMPTS_PER_BATCH = 1
MAX_FETCH_WORKERS = 5
DELIVERY_RETRY_COOLDOWN_MINUTES = 45
RECENT_POSTS_PER_ACCOUNT = 1
RECENT_MISSING_BACKFILL_HOURS = 12
# Query each account separately but concurrently. Single-account prompts are far
# more reliable with the Grok gateway, while concurrency keeps wall-clock time
# below cron's script hard timeout and preserves every-account coverage.
DEFAULT_BATCH_SIZE = 1
STATE_PATH = Path(os.environ.get("DASHBOARD_X_WATCHLIST_STATE") or str(DASHBOARD_HOME / "cron" / "state" / "x_watchlist_latest.json")).expanduser()
CONFIG_PATH = Path(os.environ.get("DASHBOARD_CONFIG") or str(DASHBOARD_HOME / "config.yaml")).expanduser()
JOBS_PATH = Path(os.environ.get("DASHBOARD_CRON_JOBS") or str(DASHBOARD_HOME / "cron" / "jobs.json")).expanduser()
JOB_ID = "a7d479f754d2"


def watchlist_accounts_from_state(path: Path = STATE_PATH) -> list[str]:
    return _watchlist_accounts_from_state(path, account_parser=parse_watchlist_accounts)


def configured_watchlist_accounts() -> list[str]:
    if "X_WATCHLIST_ACCOUNTS" in os.environ:
        return parse_watchlist_accounts(os.environ.get("X_WATCHLIST_ACCOUNTS"))
    return watchlist_accounts_from_state()


ACCOUNTS = configured_watchlist_accounts()


def load_config():
    env_base_url = os.environ.get("X_WATCHLIST_BASE_URL") or os.environ.get("DASHBOARD_GROK_BASE_URL") or os.environ.get("CROSSDESK_BASE_URL")
    env_api_key = os.environ.get("X_WATCHLIST_API_KEY") or os.environ.get("DASHBOARD_GROK_API_KEY") or os.environ.get("CROSSDESK_API_KEY")
    if env_base_url and env_api_key:
        return env_base_url.rstrip("/"), env_api_key
    if yaml is None:
        raise RuntimeError("PyYAML is required")
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    providers = cfg.get("custom_providers") or []
    crossdesk = None
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        if str(provider.get("name") or "").strip().lower() == CROSSDESK_PROVIDER_NAME_LOWER:
            crossdesk = provider
            break
    if not crossdesk:
        raise RuntimeError(f"Missing custom provider {CROSSDESK_PROVIDER_NAME} in config")
    base_url = (crossdesk.get("base_url") or "").rstrip("/")
    api_key = crossdesk.get("api_key") or ""
    if not base_url or not api_key:
        raise RuntimeError(f"Missing base_url or api_key for custom provider {CROSSDESK_PROVIDER_NAME}")
    return base_url, api_key


def extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    decoder = json.JSONDecoder()
    try:
        obj, _end = decoder.raw_decode(text)
        return obj
    except Exception:
        match = re.search(r"\{", text)
        if not match:
            raise
        obj, _end = decoder.raw_decode(text[match.start():])
        return obj


def is_temporary_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in TEMPORARY_HTTP_CODES
    if isinstance(exc, (urllib.error.URLError, TimeoutError)):
        return True
    # Grok/model-gateway sometimes returns empty text, truncated JSON, or prose
    # instead of the strictly requested JSON. For a polling monitor this is a
    # transient upstream miss; stay quiet and retry on the next cron tick.
    if isinstance(exc, (json.JSONDecodeError, ModelResponseParseError)):
        return True
    return False


def openai_chat_json(base_url, api_key, prompt, max_tokens, timeout=REQUEST_TIMEOUT_SECONDS, x_handles=None):
    handles = []
    for raw_handle in x_handles or []:
        handle = str(raw_handle or "").strip().lstrip("@").lower()
        if handle and handle not in handles:
            handles.append(handle)
    tool = {"type": "x_search"}
    if handles:
        tool["allowed_x_handles"] = handles[:20]
    model_request = build_model_request(
        base_url,
        MODEL,
        [{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        api_mode=GROK_API_MODE,
        tools=[tool],
        reasoning={"effort": "low"},
        stream=False,
    )
    parsed = request_model(
        model_request,
        api_key,
        timeout=timeout,
        opener=urllib.request.urlopen,
    )
    return extract_json(parsed.content)


def call_grok_once(base_url, api_key, account_handles, latest_by_handle, timeout=REQUEST_TIMEOUT_SECONDS):
    account_text = ", ".join("@" + account for account in account_handles)
    prompt = f"""
请使用你可用的实时 X/Twitter 能力，获取以下账号每个账号最近 {RECENT_POSTS_PER_ACCOUNT} 条公开推文的最小列表：{account_text}。

严格返回 JSON，不要 markdown，不要解释。格式：
{{"accounts":[{{"handle":"wallstreet0name","display_name":"昵称","posts":[{{"post_id":"数字ID或唯一ID","time":"YYYY-MM-DD HH:mm:ss 北京时间","chinese_text":"完整正文；外文完整翻译成中文；中文保留原文","conversation_type":"original|reply|quote|repost|unknown","media":[{{"type":"image|video|gif|unknown","url":"媒体直链或图片URL；不能获取则不要添加该媒体项","description":""}}]}}]}}]}}

要求：
- 必须返回上述每个账号，即使没有抓到也要给 posts: []。
- 每个账号最多 {RECENT_POSTS_PER_ACCOUNT} 条，优先最新推文。
- 必须包含 post_id；没有数字 ID 时用可稳定去重的唯一字符串。
- 必须包含发布时间；尽量使用北京时间。
- 不要省略推文正文。
- 如果推文包含图片/视频/GIF，尽量返回可打开的 media url；不需要识图、OCR 或图片内容描述。
- 普通纯文字推文 media 填 [] 即可。
- 尽量判断 conversation_type：回复填 reply，引用填 quote，转推填 repost，只有确定不是回复/引用/转推时才填 original；不确定填 unknown。
"""
    parsed = openai_chat_json(
        base_url,
        api_key,
        prompt,
        configured_max_tokens(min(3000, 1000 + 500 * len(account_handles))),
        timeout=timeout,
        x_handles=account_handles,
    )
    return parsed.get("accounts", [])


def hydrate_posts(base_url, api_key, new_items, timeout=DETAIL_REQUEST_TIMEOUT_SECONDS):
    if not new_items:
        return []
    post_refs = []
    for idx, (display_name, post, post_id, handle) in enumerate(new_items, 1):
        post_refs.append({
            "index": idx,
            "handle": handle,
            "display_name": display_name,
            "post_id": post_id,
            "tweet_url": f"https://x.com/{handle}/status/{post_id}" if str(post_id).isdigit() else "",
            "time": post.get("time"),
            "full_text": post.get("full_text") or "",
            "chinese_text": post.get("chinese_text") or "",
            "conversation_type": post.get("conversation_type") or "unknown",
            "media": post.get("media") or [],
            "reply_to_media": post.get("reply_to_media") or [],
            "quoted_media": post.get("quoted_media") or [],
        })
    prompt = f"""
请使用你可用的实时 X/Twitter 能力，为下面这些已经判定为新帖的推文补全完整信息。不要只看输入文本，必须逐条打开/定位 tweet_url 或用 handle+post_id 检查该推文所在会话、引用对象、被回复对象。重点：回复/引用上下文比摘要更重要；短回复没有上下文对用户完全无用。

输入推文 JSON：
{json.dumps(post_refs, ensure_ascii=False)}

严格返回 JSON，不要 markdown，不要解释。格式：
{{"posts":[{{"index":1,"handle":"账号handle","display_name":"昵称","post_id":"数字ID或唯一ID","time":"YYYY-MM-DD HH:mm:ss，尽量使用北京时间，如只能GMT请注明","full_text":"完整原文，不要摘要","chinese_text":"如果原文非中文则完整翻译成中文；如果原文中文则保留完整中文原文","conversation_type":"original|reply|quote|repost|unknown","reply_to_author":"如果本条是回复，填被回复账号昵称或handle，否则空字符串","reply_to_text":"如果本条是回复，填被回复推文完整原文，否则空字符串","reply_to_chinese_text":"被回复推文完整中文翻译/原文，否则空字符串","quoted_author":"如果有引用/转推，填被引用账号昵称或handle，否则空字符串","quoted_text":"如果有引用/转推的原文则填完整内容，否则空字符串","quoted_chinese_text":"引用内容的中文完整翻译/原文，否则空字符串","context_missing_reason":"如果判断是 reply/quote/repost 但无法取得上下文，说明原因；如果不是或已取得则空字符串","media":[{{"type":"image|video|gif|unknown","url":"媒体直链或图片URL；不能获取则不要添加该媒体项","description":""}}],"quoted_media":[{{"type":"image|video|gif|unknown","url":"被引用/转推内容的媒体直链或图片URL；不能获取则不要添加该媒体项","description":""}}],"reply_to_media":[{{"type":"image|video|gif|unknown","url":"被回复内容的媒体直链或图片URL；不能获取则不要添加该媒体项","description":""}}]}}]}}

硬性要求：
- 输出 posts 数量和 index 尽量与输入一致。
- 不要返回 X 链接字段。
- 对非中文内容，chinese_text / reply_to_chinese_text / quoted_chinese_text 只填中文翻译，不要混入英文原文。
- 对中文内容，上述中文字段只填中文原文，不要附加英文或“翻译：”。
- 不要省略推文正文。
- 必须重新判断 conversation_type；不要相信输入里的 original/unknown。
- 如果推文是回复，必须返回被回复推文的作者、完整文本、中文翻译和媒体；如果查不到，conversation_type 仍填 reply，并填写 context_missing_reason。
- 如果推文包含引用/转推，必须返回被引用内容的作者、完整文本、中文翻译和媒体；如果查不到，conversation_type 仍填 quote/repost，并填写 context_missing_reason。
- 如果无法确定是不是回复/引用，填 unknown，不要伪装成 original。
- 如果推文或引用/回复里有图片/视频/GIF，尽量返回可打开的媒体 URL；不需要识图、OCR 或图片内容描述。
"""
    try:
        # Context may belong to an unmonitored reply or quote author, so this
        # lookup must not inherit the initial account-fetch allowlist.
        parsed = openai_chat_json(
            base_url,
            api_key,
            prompt,
            configured_max_tokens(min(12000, 2200 + 1700 * len(new_items))),
            timeout=timeout,
        )
        by_index = {int(post.get("index")): post for post in parsed.get("posts", []) if isinstance(post, dict) and str(post.get("index", "")).isdigit()}
    except Exception:
        return new_items

    hydrated = []
    for idx, (display_name, post, post_id, handle) in enumerate(new_items, 1):
        rich_post = by_index.get(idx)
        if isinstance(rich_post, dict):
            merged_post = dict(post)
            for key, value in rich_post.items():
                if key in {"index", "handle", "display_name"}:
                    continue
                # Do not let a later hydration pass erase media URLs
                # that were already returned by the minimal fetch. This matters
                # when the first Grok lookup finds the image URL but the detail
                # hydration pass omits it.
                if key in {"media", "reply_to_media", "quoted_media"} and not value and merged_post.get(key):
                    continue
                if key in {"media", "reply_to_media", "quoted_media"} and isinstance(value, list):
                    merged_post[key] = merge_media_items(merged_post.get(key) or [], value)
                    continue
                merged_post[key] = value
            merged_post = normalize_post_time(merged_post, post_id)
            hydrated.append((rich_post.get("display_name") or display_name, merged_post, post_id, handle))
        else:
            hydrated.append((display_name, normalize_post_time(post, post_id), post_id, handle))
    return hydrated


def should_hold_for_context(post):
    # Prefer not missing alerts over strict context completeness. Grok sometimes
    # times out while resolving reply/quote parents; holding those items made the
    # dashboard feed appear stale. Set X_WATCHLIST_STRICT_CONTEXT_HOLD=1 to
    # restore old behavior.
    strict = os.environ.get("X_WATCHLIST_STRICT_CONTEXT_HOLD", "0").lower() in {"1", "true", "yes"}
    return _should_hold_for_context(post, strict=strict)


def fetch_url_text(url, timeout=8, attempts=HTML_FETCH_ATTEMPTS):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    last_error = None
    attempts = max(1, int(attempts or 1))
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "ignore")
        except Exception as exc:
            last_error = exc
            if not is_temporary_error(exc) or attempt >= attempts:
                break
            time.sleep(min(1.5, 0.35 * attempt))
    raise last_error


def parse_x_html_tweet(handle, post_id, timeout=8, attempts=HTML_FETCH_ATTEMPTS):
    raw = ""
    last_error = None
    for domain in ("x.com", "twitter.com"):
        try:
            raw = fetch_url_text(f"https://{domain}/{handle}/status/{post_id}", timeout=timeout, attempts=attempts)
            break
        except Exception as exc:
            last_error = exc
    if not raw:
        raise last_error
    social = parse_social_posting(raw)
    author = social.get("author") if isinstance(social.get("author"), dict) else {}
    text = str(social.get("articleBody") or first_meta_content(raw, ["og:description", "description"])).strip()
    media = extract_x_media(raw, social=social)
    display_name = str(author.get("name") or "").strip()
    screen_name = str(author.get("alternateName") or "").lstrip("@").strip() or handle
    time_text = ""
    date_published = str(social.get("datePublished") or "").strip()
    if date_published:
        try:
            dt = datetime.fromisoformat(date_published.replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=8)))
            time_text = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            time_text = date_published
    reply_to_handle = ""
    reply_match = re.search(r'Replying to\s*<a[^>]+href="https://(?:x|twitter)\.com/([^"/?#]+)"', raw, flags=re.I | re.S)
    if reply_match:
        reply_to_handle = html.unescape(reply_match.group(1)).lstrip("@").strip()
    # The logged-out X HTML contains a Flight/RSC cache with TweetResults rest_id
    # entries for the conversation. Exclude the current tweet and use the nearest
    # older tweet as the parent when the page says this is a reply.
    parent_id = ""
    ids = []
    for match in re.finditer(r'__typename:\"?TweetResults\"?.{0,220}?rest_id:\"?(\d{10,25})\"?', raw, flags=re.S):
        candidate = match.group(1)
        if candidate != str(post_id) and candidate not in ids:
            ids.append(candidate)
    if not ids:
        for candidate in re.findall(r'rest_id:\"?(\d{10,25})\"?', raw):
            if candidate != str(post_id) and candidate not in ids:
                ids.append(candidate)
    if ids and str(post_id).isdigit():
        current = int(post_id)
        older = [candidate for candidate in ids if candidate.isdigit() and int(candidate) < current]
        if older:
            parent_id = max(older, key=lambda value: int(value))
    elif ids:
        parent_id = ids[0]
    return {
        "raw": raw,
        "post_id": str(post_id),
        "handle": screen_name,
        "display_name": display_name,
        "text": text,
        "time": time_text,
        "media": media,
        "reply_to_handle": reply_to_handle,
        "parent_id": parent_id,
    }


def repair_context_from_x_html(display_name, post, post_id, handle, timeout=8):
    timeout = max(3, int(timeout or 8))
    fetch_timeout = max(3, min(8, timeout))
    fetch_attempts = HTML_FETCH_ATTEMPTS if timeout >= 7 else 1
    try:
        current = parse_x_html_tweet(handle, post_id, timeout=fetch_timeout, attempts=fetch_attempts)
    except Exception:
        return post
    merged = dict(post)
    if current.get("text"):
        merged["full_text"] = current["text"]
        merged["chinese_text"] = current["text"]
    if current.get("time"):
        merged.setdefault("time", current["time"])
    if current.get("media"):
        merged["media"] = merge_media_items(merged.get("media") or [], current.get("media") or [])
    reply_handle = current.get("reply_to_handle") or ""
    parent_id = current.get("parent_id") or ""
    if reply_handle:
        merged["conversation_type"] = "reply"
    if reply_handle and parent_id:
        try:
            parent = parse_x_html_tweet(reply_handle, parent_id, timeout=fetch_timeout, attempts=fetch_attempts)
            parent_text = parent.get("text") or ""
            if parent_text:
                parent_author = parent.get("display_name") or ("@" + reply_handle)
                merged["reply_to_author"] = parent_author
                merged["reply_to_text"] = parent_text
                merged["reply_to_chinese_text"] = parent_text
                if parent.get("media"):
                    merged["reply_to_media"] = merge_media_items(merged.get("reply_to_media") or [], parent.get("media") or [])
                merged.pop("context_missing_reason", None)
                return merged
        except Exception:
            pass
    if reply_handle:
        merged["context_missing_reason"] = f"x_html_parent_unresolved:{reply_handle}:{parent_id or 'no_parent_id'}"
    return merged


def hydrate_missing_media_from_x_html(new_items, deadline, timeout_seconds=MEDIA_HTML_HYDRATE_TIMEOUT_SECONDS, max_items=None):
    hydrated_items = list(new_items)
    if max_items is None:
        max_items = max(1, int(os.environ.get("X_WATCHLIST_MAX_MEDIA_HTML_HYDRATE_ITEMS", str(MAX_MEDIA_HTML_HYDRATE_ITEMS))))
    candidates = []
    for idx, (_display_name, post, post_id, handle) in enumerate(hydrated_items):
        if post.get("media") or not str(post_id).isdigit() or not handle:
            continue
        candidates.append(idx)
        if len(candidates) >= max_items:
            break
    if not candidates or deadline - time.monotonic() <= 8:
        return hydrated_items

    def hydrate_index(idx):
        display_name, post, post_id, handle = hydrated_items[idx]
        current = parse_x_html_tweet(handle, post_id, timeout=timeout_seconds, attempts=1)
        if not current.get("media"):
            return idx, None
        merged_post = dict(post)
        merged_post["media"] = merge_media_items(merged_post.get("media") or [], current.get("media") or [])
        return idx, (display_name, merged_post, post_id, handle)

    workers = max(1, min(len(candidates), int(os.environ.get("X_WATCHLIST_MEDIA_HTML_WORKERS", "3"))))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    try:
        future_to_idx = {executor.submit(hydrate_index, idx): idx for idx in candidates}
        done, _not_done = concurrent.futures.wait(
            future_to_idx,
            timeout=max(4, min(timeout_seconds + 2, int(deadline - time.monotonic() - 3))),
            return_when=concurrent.futures.ALL_COMPLETED,
        )
        for future in done:
            try:
                idx, item = future.result(timeout=0)
            except Exception:
                continue
            if item:
                hydrated_items[idx] = item
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return hydrated_items


def repair_one_context(base_url, api_key, display_name, post, post_id, handle, timeout=REPAIR_REQUEST_TIMEOUT_SECONDS):
    x_html_repaired = repair_context_from_x_html(display_name, post, post_id, handle, timeout=min(timeout, 8))
    if has_recovered_context(x_html_repaired):
        return x_html_repaired
    post = x_html_repaired
    tweet_url = f"https://x.com/{handle}/status/{post_id}" if str(post_id).isdigit() else ""
    prompt = f"""
请使用实时 X/Twitter 能力，单独查询下面这条推文的上下文。重点是找出它引用/回复/转推的原推，不要只复述本推文本身。

账号：@{handle}
昵称：{display_name}
post_id：{post_id}
tweet_url：{tweet_url}
时间：{post.get('time') or ''}
正文：{post.get('chinese_text') or post.get('full_text') or ''}

严格返回 JSON，不要 markdown，不要解释：
{{"found":true,"tweet_id":"","conversation_type":"original|reply|quote|repost|unknown","main_text":"完整本推文原文/中文","reply_to_author":"","reply_to_text":"被回复原推全文，否则空","reply_to_chinese_text":"被回复原推中文，否则空","reply_to_media":[{{"type":"image|video|gif|unknown","url":"被回复原推媒体直链；没有直链则不要添加该媒体项","description":""}}],"quoted_author":"","quoted_text":"被引用/转推原推全文，否则空","quoted_chinese_text":"被引用/转推原推中文，否则空","quoted_media":[{{"type":"image|video|gif|unknown","url":"被引用/转推媒体直链；没有直链则不要添加该媒体项","description":""}}],"context_missing_reason":"如果是 reply/quote/repost 但仍取不到上下文，写原因；否则空"}}

硬性要求：
- 必须检查 tweet_url 或 handle+post_id 对应的推文页面/会话。
- 如果正文像“白毛股神:xxx”“RT @xxx”“引用了xxx”的转述，优先判定是否 quote/repost，并找被引用对象。
- 能取到原推时必须返回 quoted_text/reply_to_text，不要留空。
- reply_to_chinese_text / quoted_chinese_text 只填中文：外文原帖只给中文翻译，中文原帖只给中文原文；不要中英双语，不要加“翻译：”。
- 如果原推包含图片/视频/GIF，尽量返回 reply_to_media/quoted_media 的可打开 URL；不需要识图、OCR 或图片内容描述。
"""
    # The parent or quoted post can belong to a different account. Restricting
    # X Search to the monitored handle would make that context unreachable.
    parsed = openai_chat_json(
        base_url,
        api_key,
        prompt,
        configured_max_tokens(3000),
        timeout=timeout,
    )
    if not isinstance(parsed, dict):
        return post
    merged = dict(post)
    mapping = {
        "conversation_type": "conversation_type",
        "main_text": "chinese_text",
        "reply_to_author": "reply_to_author",
        "reply_to_text": "reply_to_text",
        "reply_to_chinese_text": "reply_to_chinese_text",
        "quoted_author": "quoted_author",
        "quoted_text": "quoted_text",
        "quoted_chinese_text": "quoted_chinese_text",
        "context_missing_reason": "context_missing_reason",
    }
    for source, target in mapping.items():
        value = parsed.get(source)
        if isinstance(value, str) and value.strip():
            merged[target] = value.strip()
    for source, target in (("reply_to_media", "reply_to_media"), ("quoted_media", "quoted_media")):
        value = parsed.get(source)
        if isinstance(value, list):
            cleaned = [item for item in value if isinstance(item, dict) and str(item.get("url") or "").strip()]
            if cleaned:
                merged[target] = merge_media_items(merged.get(target) or [], cleaned)
    return merged


def repair_missing_contexts(base_url, api_key, new_items, deadline, timeout_seconds=REPAIR_REQUEST_TIMEOUT_SECONDS, max_items_per_round=None):
    repaired_items = list(new_items)
    max_rounds = max(1, int(os.environ.get("X_WATCHLIST_CONTEXT_REPAIR_RETRY_ROUNDS", str(CONTEXT_REPAIR_RETRY_ROUNDS))))
    if max_items_per_round is None:
        max_items_per_round = max(1, int(os.environ.get("X_WATCHLIST_MAX_CONTEXT_REPAIR_ITEMS", str(MAX_CONTEXT_REPAIR_ITEMS))))
    else:
        max_items_per_round = max(1, int(max_items_per_round))
    retry_sleep = max(0, float(os.environ.get("X_WATCHLIST_CONTEXT_REPAIR_RETRY_SLEEP_SECONDS", str(CONTEXT_REPAIR_RETRY_SLEEP_SECONDS))))
    total_attempts = 0
    last_round = 0
    last_unresolved = 0
    last_errors = []

    def repair_index(idx, round_index, per_request_timeout):
        display_name, post, post_id, handle = repaired_items[idx]
        try:
            repaired_post = repair_one_context(base_url, api_key, display_name, post, post_id, handle, timeout=per_request_timeout)
            if has_recovered_context(repaired_post):
                repaired_post.pop("context_missing_reason", None)
            return idx, display_name, repaired_post, post_id, handle, None
        except Exception as exc:
            repaired_post = dict(post)
            repaired_post["context_missing_reason"] = f"单条上下文查询第{round_index}轮失败：{type(exc).__name__}"
            return idx, display_name, repaired_post, post_id, handle, f"{handle}:{post_id}:{type(exc).__name__}"

    for round_index in range(1, max_rounds + 1):
        indexed_candidates = []
        for idx, (_display_name, post, _post_id, _handle) in enumerate(repaired_items):
            if needs_context_repair(post):
                indexed_candidates.append((parse_post_time(post.get("time")) or datetime.min, idx))
        candidates = [idx for _post_time, idx in sorted(indexed_candidates, key=lambda item: item[0], reverse=True)]
        last_unresolved = len(candidates)
        if not candidates:
            break
        remaining_deadline = deadline - time.monotonic()
        if remaining_deadline <= 8:
            break
        last_round = round_index
        selected = candidates[:max_items_per_round]
        selected_times = [repaired_items[idx][1].get("time") for idx in selected]
        per_request_timeout = max(6, min(timeout_seconds, int(remaining_deadline - 4)))
        total_attempts += len(selected)
        workers = max(1, min(len(selected), int(os.environ.get("X_WATCHLIST_CONTEXT_REPAIR_WORKERS", str(MAX_CONTEXT_REPAIR_ITEMS)))))
        attempted_this_round = 0
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        future_to_idx = {}
        try:
            future_to_idx = {executor.submit(repair_index, idx, round_index, per_request_timeout): idx for idx in selected}
            done, not_done = concurrent.futures.wait(
                future_to_idx,
                timeout=max(6, min(per_request_timeout + 2, int(deadline - time.monotonic() - 3))),
                return_when=concurrent.futures.ALL_COMPLETED,
            )
            attempted_this_round = len(done) + len(not_done)
            for future in done:
                idx, display_name, post, post_id, handle, err = future.result(timeout=0)
                if err:
                    last_errors.append(err)
                repaired_items[idx] = (display_name, post, post_id, handle)
            for future in not_done:
                idx = future_to_idx[future]
                future.cancel()
                display_name, post, post_id, handle = repaired_items[idx]
                repaired_post = dict(post)
                repaired_post["context_missing_reason"] = f"单条上下文查询第{round_index}轮失败：TimeoutError"
                repaired_items[idx] = (display_name, repaired_post, post_id, handle)
                last_errors.append(f"{handle}:{post_id}:TimeoutError")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        still_missing = any(needs_context_repair(post) for _display_name, post, _post_id, _handle in repaired_items)
        if not still_missing:
            break
        if attempted_this_round == 0:
            break
        if round_index < max_rounds and retry_sleep and (deadline - time.monotonic()) > (retry_sleep + 8):
            time.sleep(retry_sleep)

    repair_missing_contexts.last_stats = {
        "rounds": last_round,
        "attempts": total_attempts,
        "unresolved": sum(1 for _display_name, post, _post_id, _handle in repaired_items if needs_context_repair(post)),
        "last_unresolved": last_unresolved,
        "selected_times": selected_times[-8:] if 'selected_times' in locals() else [],
        "last_errors": last_errors[-8:],
    }
    return repaired_items


def call_grok_batch(base_url, api_key, account_handles, latest_by_handle, timeout=REQUEST_TIMEOUT_SECONDS):
    last_error = None
    max_attempts = max(1, int(os.environ.get("X_WATCHLIST_MAX_ATTEMPTS", str(MAX_ATTEMPTS_PER_BATCH))))
    for attempt in range(1, max_attempts + 1):
        try:
            return call_grok_once(base_url, api_key, account_handles, latest_by_handle, timeout=timeout)
        except Exception as exc:
            last_error = exc
            if not is_temporary_error(exc) or attempt == max_attempts:
                break
        time.sleep(2 * attempt + len(account_handles))
    raise last_error


def call_grok(base_url, api_key, latest_by_handle):
    # Single-account prompts are the most reliable with the current Grok gateway.
    # Run several in parallel so every cron tick still covers the full watchlist.
    call_grok.last_issue = ""
    if not ACCOUNTS:
        call_grok.last_issue = "watchlist_accounts_empty"
        return []
    deadline = time.monotonic() + int(os.environ.get("X_WATCHLIST_DEADLINE_SECONDS", str(TOTAL_DEADLINE_SECONDS)))
    all_accounts = []
    temporary_errors = []
    non_temporary_errors = []
    workers = max(1, min(len(ACCOUNTS), int(os.environ.get("X_WATCHLIST_MAX_WORKERS", str(MAX_FETCH_WORKERS)))))

    def fetch_one(handle):
        remaining = deadline - time.monotonic()
        if remaining <= 5:
            raise TimeoutError("deadline reached before request")
        timeout = max(8, min(REQUEST_TIMEOUT_SECONDS, int(remaining - 3)))
        return call_grok_batch(base_url, api_key, [handle], latest_by_handle, timeout=timeout)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    future_to_handle = {}
    try:
        future_to_handle = {executor.submit(fetch_one, handle): handle for handle in ACCOUNTS}
        done, not_done = concurrent.futures.wait(
            future_to_handle,
            timeout=max(8, int(deadline - time.monotonic())),
            return_when=concurrent.futures.ALL_COMPLETED,
        )
        for future in done:
            try:
                accounts = future.result(timeout=0)
                if accounts:
                    all_accounts.extend(accounts)
            except Exception as exc:
                if is_temporary_error(exc):
                    temporary_errors.append(f"{future_to_handle.get(future)}: {type(exc).__name__}: {exc}")
                else:
                    non_temporary_errors.append(exc)
        for future in not_done:
            future.cancel()
            temporary_errors.append(f"{future_to_handle.get(future)}: TimeoutError: deadline exceeded")
    finally:
        # Do not wait for stuck urllib/model-gateway worker threads here. The cron
        # runner has a hard ~120s timeout; waiting for cancelled-but-stuck futures
        # caused user-visible script timeout alerts.
        executor.shutdown(wait=False, cancel_futures=True)

    if temporary_errors and not all_accounts:
        call_grok.last_issue = "; ".join(temporary_errors[-5:])[:700]
    elif temporary_errors:
        call_grok.last_issue = "partial temporary errors: " + "; ".join(temporary_errors[-5:])[:650]
    if all_accounts:
        return all_accounts
    if non_temporary_errors:
        raise non_temporary_errors[0]
    return []


def load_state():
    if not STATE_PATH.exists():
        return {"seen_ids": {}, "latest": {}, "created_at": datetime.now(timezone.utc).isoformat()}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"seen_ids": {}, "latest": {}, "created_at": datetime.now(timezone.utc).isoformat(), "recovered_from_corrupt": True}


def normalize_item_post_times(items):
    return [
        (display_name, normalize_post_time(post, post_id), post_id, handle)
        for display_name, post, post_id, handle in items
        if not post_time_is_implausible(post.get("time"), post_id)
    ]


def normalize_monitor_state_times(state):
    """Repair persisted X state times and discard impossible future cursors."""
    changed = 0

    def normalize_latest(values):
        nonlocal changed
        if not isinstance(values, dict):
            return
        for handle, value in list(values.items()):
            if not isinstance(value, dict):
                continue
            if post_time_is_implausible(value.get("time"), value.get("post_id")):
                values.pop(handle, None)
                changed += 1
                continue
            normalized = normalize_post_time(value, value.get("post_id"))
            if normalized.get("time") != value.get("time"):
                value["time"] = normalized.get("time")
                changed += 1

    normalize_latest(state.get("latest"))
    pending = state.get("pending_delivery")
    if isinstance(pending, dict):
        normalize_latest(pending.get("latest"))

    for queue_name in ("sent_missing_context", "held_for_context"):
        queue = state.get(queue_name)
        if not isinstance(queue, list):
            continue
        valid_entries = []
        for entry in queue:
            if not isinstance(entry, dict):
                valid_entries.append(entry)
                continue
            post = entry.get("post") if isinstance(entry.get("post"), dict) else {}
            post_id = entry.get("post_id") or post.get("post_id")
            if post_time_is_implausible(post.get("time") or entry.get("time"), post_id):
                changed += 1
                continue
            normalized_post = normalize_post_time(post, post_id)
            if post and normalized_post != post:
                entry["post"] = normalized_post
                changed += 1
            post_time = canonical_post_time(
                normalized_post.get("time") or entry.get("time"),
                post_id,
            )
            if not post_time:
                valid_entries.append(entry)
                continue
            time_text = post_time.strftime("%Y-%m-%d %H:%M:%S")
            if entry.get("time") != time_text:
                entry["time"] = time_text
                changed += 1
            if entry.get("timestamp") is not None:
                timestamp = post_time.replace(tzinfo=timezone(timedelta(hours=8))).timestamp()
                if abs(float(entry.get("timestamp") or 0) - timestamp) >= 1:
                    entry["timestamp"] = timestamp
                    changed += 1
            valid_entries.append(entry)
        queue[:] = valid_entries
    return changed


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def find_job_record():
    try:
        data = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    jobs = data.get("jobs") if isinstance(data, dict) else data
    if not isinstance(jobs, list):
        return None
    for job in jobs:
        # cron/jobs.json stores the identifier as "id"; cronjob(list) exposes it as "job_id".
        # Accept both so delivery-aware retry state works across both representations.
        if job.get("id") == JOB_ID or job.get("job_id") == JOB_ID:
            return job
    return None


def previous_delivery_succeeded():
    job = find_job_record()
    if not job:
        return False
    return job.get("last_status") == "ok" and not job.get("last_delivery_error")


def previous_delivery_rate_limited():
    job = find_job_record()
    if not job:
        return False
    error = job.get("last_delivery_error") or ""
    return "rate limited" in error.lower()


def pending_in_cooldown(state):
    pending = state.get("pending_delivery") or {}
    if not pending:
        return False
    job = find_job_record()
    if not job or not job_ran_after_pending(job, pending) or not previous_delivery_rate_limited():
        return False
    last_attempt = parse_iso(pending.get("last_attempt_at") or pending.get("created_at"))
    if not last_attempt:
        return False
    return datetime.now(timezone.utc) - last_attempt < timedelta(minutes=DELIVERY_RETRY_COOLDOWN_MINUTES)


def clear_stale_pending(state):
    if not pending_is_already_latest(state):
        return False
    state.pop("pending_delivery", None)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return True


def apply_pending_if_delivered(state):
    pending = state.get("pending_delivery") or {}
    if not pending:
        return False
    job = find_job_record()
    if not job or not job_ran_after_pending(job, pending) or not previous_delivery_succeeded():
        return False
    seen = state.setdefault("seen_ids", {})
    latest = state.setdefault("latest", {})
    merge_seen_ids(seen, pending.get("seen_ids") or {})
    merge_latest(latest, pending.get("latest") or {})
    state.pop("pending_delivery", None)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return True


def print_pending_and_exit(state):
    pending = state.get("pending_delivery") or {}
    if not pending:
        return False
    # User-facing X alerts should stay in the dashboard database. Drop any legacy
    # stdout fallback and let the normal database path retry on a later poll.
    state.pop("pending_delivery", None)
    state["last_delivery_mode"] = "stdout_fallback_suppressed"
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return False


def write_direct_x_alerts_to_db(send_items):
    if push_history is None or not send_items:
        return 0
    messages = []
    now = datetime.now(timezone.utc)
    for idx, (display_name, post, post_id, handle) in enumerate(send_items, 1):
        if post_time_is_implausible(post.get("time"), post_id):
            continue
        post = normalize_post_time(post, post_id)
        post_time = canonical_post_time(post.get("time"), post_id)
        if post_time:
            timestamp = post_time.replace(tzinfo=timezone(timedelta(hours=8))).timestamp()
            time_text = post_time.strftime("%Y-%m-%d %H:%M:%S")
        else:
            timestamp = now.timestamp()
            time_text = now.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        content = fmt_post(idx, display_name, post, include_media_urls=False)
        messages.append({
            "id": push_history.stable_id("x_watchlist", handle, post_id),
            "timestamp": timestamp,
            "time_text": time_text,
            "category": "x_monitor",
            "source_type": "x_watchlist",
            "source_id": handle,
            "source_label": display_name,
            "platform": "dashboard",
            "platform_label": "Dashboard",
            "chat": "x-watchlist",
            "external_id": str(post_id or ""),
            "title": "推特监控",
            "content": content,
            "chars": len(content),
            "matched": True,
            "kind": "database_record",
            "delivery": {"mode": "dashboard_database_only", "job_id": JOB_ID},
            "metadata": {"handle": handle, "post": post},
        })
    return push_history.upsert_many(messages)


def remember_sent_missing_contexts(state, send_items):
    queue = {}
    for entry in state.get("sent_missing_context") or []:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key") or sent_context_key(entry.get("handle"), entry.get("post_id"))
        if key and ":" in key:
            entry["key"] = key
            queue[key] = entry
    now = datetime.now(timezone.utc).isoformat()
    for display_name, post, post_id, handle in send_items:
        if not isinstance(post, dict) or not should_retry_sent_context(post):
            continue
        key = sent_context_key(handle, post_id)
        if not key or key == ":":
            continue
        existing = queue.get(key) or {}
        queue[key] = {
            **existing,
            "key": key,
            "handle": str(handle or "").lstrip("@").lower(),
            "display_name": display_name,
            "post_id": str(post_id or ""),
            "time": post.get("time"),
            "post": post,
            "source_type": "x_watchlist",
            "source_id": str(handle or "").lstrip("@").lower(),
            "source_label": display_name,
            "platform": "dashboard",
            "platform_label": "Dashboard",
            "chat": "x-watchlist",
            "external_id": str(post_id or ""),
            "title": "推特监控",
            "kind": "database_record",
            "delivery": {"mode": "dashboard_database_only", "job_id": JOB_ID},
            "queued_at": existing.get("queued_at") or now,
            "updated_at": now,
        }
    if queue:
        state["sent_missing_context"] = sorted(
            (compact_sent_context_entry(entry) for entry in queue.values()),
            key=lambda entry: parse_post_time(entry.get("time")) or datetime.min,
            reverse=True,
        )[:MAX_SENT_CONTEXT_REPAIR_QUEUE]


def decode_json_field(value):
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return None


def sent_context_entry_from_db_row(row):
    item = dict(row)
    content = str(item.get("content") or "")
    if "上下文：本次未取到" not in content:
        return None
    metadata = decode_json_field(item.get("metadata_json")) or {}
    post = metadata.get("post") if isinstance(metadata.get("post"), dict) else {}
    handle = str(metadata.get("handle") or item.get("source_id") or "").lstrip("@").lower()
    post_id = str(item.get("external_id") or post.get("post_id") or "").strip()
    if not looks_like_x_handle(handle) or not post_id:
        return None
    post = dict(post)
    post.setdefault("post_id", post_id)
    post.setdefault("time", item.get("time_text") or "")
    if "回复上下文：本次未取到" in content:
        post.setdefault("conversation_type", "reply")
    elif "引用/转推上下文：本次未取到" in content:
        post.setdefault("conversation_type", "quote")
    display_name = item.get("source_label") or post.get("display_name") or handle
    delivery = decode_json_field(item.get("delivery_json")) or {"mode": "dashboard_database_only", "job_id": JOB_ID}
    key = sent_context_key(handle, post_id)
    return {
        "key": key,
        "handle": handle,
        "display_name": display_name,
        "post_id": post_id,
        "time": post.get("time") or item.get("time_text") or "",
        "post": post,
        "db_id": item.get("id") or "",
        "timestamp": item.get("timestamp"),
        "source_type": item.get("source_type") or "x_watchlist",
        "source_id": item.get("source_id") or handle,
        "source_label": item.get("source_label") or display_name,
        "platform": item.get("platform") or "dashboard",
        "platform_label": item.get("platform_label") or "Dashboard",
        "chat": item.get("chat") or "x-watchlist",
        "chat_label": item.get("chat_label") or "",
        "external_id": post_id,
        "title": item.get("title") or "推特监控",
        "kind": item.get("kind") or "database_record",
        "delivery": delivery,
        "raw_path": item.get("raw_path") or "",
        "created_at": item.get("created_at"),
    }


def load_recent_sent_missing_context_entries(limit):
    if push_history is None:
        return []
    try:
        con = push_history.connect()
    except Exception:
        return []
    try:
        lookback = time.time() - int(os.environ.get(
            "X_WATCHLIST_SENT_CONTEXT_REPAIR_LOOKBACK_HOURS",
            str(SENT_CONTEXT_REPAIR_LOOKBACK_HOURS),
        )) * 3600
        rows = con.execute(
            """
            SELECT * FROM dashboard_messages
            WHERE category = 'x_monitor'
              AND external_id IS NOT NULL
              AND external_id != ''
              AND content LIKE '%上下文：本次未取到%'
              AND timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (lookback, max(limit * 4, limit)),
        ).fetchall()
    except Exception:
        return []
    finally:
        try:
            con.close()
        except Exception:
            pass
    entries = []
    seen = set()
    for row in rows:
        entry = sent_context_entry_from_db_row(row)
        if not entry or entry["key"] in seen:
            continue
        seen.add(entry["key"])
        entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


def load_sent_context_retry_entries(state, limit):
    by_key = {}
    for entry in state.get("sent_missing_context") or []:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key") or sent_context_key(entry.get("handle"), entry.get("post_id"))
        handle = str(entry.get("handle") or "").lstrip("@").lower()
        post_id = str(entry.get("post_id") or "").strip()
        post = entry.get("post") if isinstance(entry.get("post"), dict) else None
        if key and looks_like_x_handle(handle) and post_id and post:
            entry["key"] = key
            by_key[key] = entry
    for entry in load_recent_sent_missing_context_entries(limit):
        by_key.setdefault(entry["key"], entry)

    attempts_by_key = state.setdefault("sent_context_repair_attempts", {})
    max_attempts = max(1, int(os.environ.get(
        "X_WATCHLIST_SENT_CONTEXT_REPAIR_MAX_ATTEMPTS",
        str(MAX_SENT_CONTEXT_REPAIR_ATTEMPTS),
    )))
    cooldown = timedelta(minutes=max(1, int(os.environ.get(
        "X_WATCHLIST_SENT_CONTEXT_REPAIR_COOLDOWN_MINUTES",
        str(SENT_CONTEXT_REPAIR_COOLDOWN_MINUTES),
    ))))
    now = datetime.now(timezone.utc)
    ready = []
    for entry in by_key.values():
        key = entry.get("key")
        attempt_record = attempts_by_key.get(key)
        if isinstance(attempt_record, dict):
            attempts = int(attempt_record.get("attempts") or entry.get("attempts") or 0)
            last_attempt = parse_iso(attempt_record.get("last_attempt_at") or entry.get("last_attempt_at"))
        else:
            attempts = int(attempt_record or entry.get("attempts") or 0)
            last_attempt = parse_iso(entry.get("last_attempt_at"))
        if attempts >= max_attempts:
            continue
        if last_attempt and now - last_attempt < cooldown:
            continue
        entry["attempts"] = attempts
        ready.append(entry)
    ready.sort(key=lambda entry: parse_post_time(entry.get("time")) or datetime.min, reverse=True)
    return ready[:limit]


def update_sent_context_queue_after_attempts(state, attempted_entries, repaired_keys):
    queue = {}
    for entry in state.get("sent_missing_context") or []:
        if isinstance(entry, dict):
            key = entry.get("key") or sent_context_key(entry.get("handle"), entry.get("post_id"))
            if key and key not in repaired_keys:
                entry["key"] = key
                queue[key] = entry
    for entry in attempted_entries:
        key = entry.get("key")
        if not key or key in repaired_keys:
            continue
        queue[key] = entry
    if queue:
        state["sent_missing_context"] = sorted(
            (compact_sent_context_entry(entry) for entry in queue.values()),
            key=lambda entry: parse_post_time(entry.get("time")) or datetime.min,
            reverse=True,
        )[:MAX_SENT_CONTEXT_REPAIR_QUEUE]
    else:
        state["sent_missing_context"] = []


def upsert_repaired_context_message(entry, display_name, repaired_post, post_id, handle):
    if push_history is None:
        return False
    repaired_post = normalize_post_time(repaired_post, post_id)
    post_time = canonical_post_time(repaired_post.get("time") or entry.get("time"), post_id)
    timestamp = entry.get("timestamp")
    time_text = entry.get("time") or repaired_post.get("time") or ""
    if post_time:
        timestamp = post_time.replace(tzinfo=timezone(timedelta(hours=8))).timestamp()
        time_text = post_time.strftime("%Y-%m-%d %H:%M:%S")
    elif not timestamp:
        timestamp = time.time()
    metadata = {
        "handle": handle,
        "post": repaired_post,
        "context_repaired_at": datetime.now(timezone.utc).isoformat(),
    }
    content = fmt_post(1, display_name, repaired_post, include_media_urls=False)
    message = {
        "id": entry.get("db_id") or push_history.stable_id("x_watchlist", handle, post_id),
        "timestamp": timestamp,
        "time_text": time_text,
        "category": "x_monitor",
        "source_type": entry.get("source_type") or "x_watchlist",
        "source_id": entry.get("source_id") or handle,
        "source_label": entry.get("source_label") or display_name,
        "platform": entry.get("platform") or "dashboard",
        "platform_label": entry.get("platform_label") or "Dashboard",
        "chat": entry.get("chat") or "x-watchlist",
        "chat_label": entry.get("chat_label") or "",
        "external_id": str(post_id or ""),
        "title": entry.get("title") or "推特监控",
        "content": content,
        "chars": len(content),
        "matched": True,
        "kind": entry.get("kind") or "database_record",
        "delivery": entry.get("delivery") or {"mode": "dashboard_database_only", "job_id": JOB_ID},
        "metadata": metadata,
        "raw_path": entry.get("raw_path") or "",
        "created_at": entry.get("created_at") or time.time(),
    }
    try:
        return bool(push_history.upsert_many([message]))
    except Exception:
        return False


def repair_sent_missing_contexts(base_url, api_key, state, deadline, max_items=None):
    repair_sent_missing_contexts.last_stats = {}
    if push_history is None or deadline - time.monotonic() <= 12:
        return 0
    if max_items is None:
        max_items = max(1, int(os.environ.get(
            "X_WATCHLIST_SENT_CONTEXT_REPAIR_ITEMS",
            str(MAX_SENT_CONTEXT_REPAIR_ITEMS),
        )))
    entries = load_sent_context_retry_entries(state, max_items)
    if not entries:
        return 0
    attempts_by_key = state.setdefault("sent_context_repair_attempts", {})
    attempted_entries = []
    repaired_keys = set()
    repaired_count = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for entry in entries:
        if deadline - time.monotonic() <= 10:
            break
        key = entry.get("key")
        display_name = entry.get("display_name") or entry.get("source_label") or entry.get("handle")
        post = entry.get("post") if isinstance(entry.get("post"), dict) else {}
        post_id = str(entry.get("post_id") or entry.get("external_id") or "").strip()
        handle = str(entry.get("handle") or "").lstrip("@").lower()
        if not key or not looks_like_x_handle(handle) or not post_id or not post:
            continue
        attempts = int(entry.get("attempts") or 0) + 1
        try:
            timeout = max(5, min(REPAIR_REQUEST_TIMEOUT_SECONDS, int(deadline - time.monotonic() - 4)))
            repaired_post = repair_one_context(base_url, api_key, display_name, post, post_id, handle, timeout=timeout)
            entry["post"] = repaired_post
            entry["last_attempt_at"] = now_iso
            entry["attempts"] = attempts
            if has_recovered_context(repaired_post):
                repaired_post.pop("context_missing_reason", None)
                if upsert_repaired_context_message(entry, display_name, repaired_post, post_id, handle):
                    repaired_keys.add(key)
                    attempts_by_key.pop(key, None)
                    repaired_count += 1
                    continue
            entry["last_error"] = repaired_post.get("context_missing_reason") or "context_still_missing"
        except Exception as exc:
            entry["last_error"] = f"{type(exc).__name__}: {exc}"
            entry["last_attempt_at"] = now_iso
            entry["attempts"] = attempts
        attempts_by_key[key] = {
            "attempts": attempts,
            "last_attempt_at": entry.get("last_attempt_at") or now_iso,
            "last_error": entry.get("last_error") or "",
        }
        attempted_entries.append(entry)
    update_sent_context_queue_after_attempts(state, attempted_entries, repaired_keys)
    stats = {
        "attempted": len(attempted_entries) + len(repaired_keys),
        "repaired": repaired_count,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    repair_sent_missing_contexts.last_stats = stats
    if stats["attempted"]:
        state["last_sent_context_repair"] = stats
    return repaired_count


def load_context_retry_items(state, seen):
    retry_items = []
    retry_seen_keys = set()
    for held in list(state.get("held_for_context") or []):
        if not isinstance(held, dict):
            continue
        handle = str(held.get("handle") or "").lstrip("@").lower()
        post_id = str(held.get("post_id") or "").strip()
        post = held.get("post") if isinstance(held.get("post"), dict) else None
        if not handle or not post_id or not post:
            continue
        if post_id in set(str(x) for x in seen.get(handle, [])):
            continue
        key = (handle, post_id)
        if key in retry_seen_keys:
            continue
        retry_seen_keys.add(key)
        display_name = held.get("display_name") or post.get("display_name") or handle
        retry_items.append((display_name, post, post_id, handle))
    return retry_items


def split_send_and_held(new_items):
    send_items = []
    held_for_context = []
    for display_name, post, post_id, handle in new_items:
        if should_hold_for_context(post):
            held_for_context.append({
                "handle": handle,
                "display_name": display_name,
                "post_id": post_id,
                "time": post.get("time"),
                "reason": post.get("context_missing_reason") or "missing_reply_or_quote_context",
                "post": post,
                "held_at": datetime.now(timezone.utc).isoformat(),
            })
            continue
        send_items.append((display_name, post, post_id, handle))
    return send_items, held_for_context


def send_ready_items(base_url, api_key, state, items, latest, deadline, limit=10):
    items = normalize_item_post_times(items)
    send_items, held_for_context = split_send_and_held(items)
    send_items = send_items[:limit]
    send_seen_ids = {}
    for _display_name, _post, post_id, handle in send_items:
        send_seen_ids.setdefault(handle, []).append(post_id)
    send_latest = latest_from_items(send_items, latest)

    if held_for_context:
        state["held_for_context"] = held_for_context[-20:]
        state["last_detail_issue"] = f"held_for_context:{len(held_for_context)}"
    else:
        state["held_for_context"] = []

    if not send_items:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        return False

    if deadline - time.monotonic() <= 5:
        state["last_database_error"] = "skipped_low_time"
        return False

    try:
        stored_count = write_direct_x_alerts_to_db(send_items)
    except Exception as exc:
        state["last_database_error"] = f"{type(exc).__name__}: {exc}"
        return False
    if stored_count != len(send_items):
        state["last_database_error"] = f"incomplete_write:{stored_count}/{len(send_items)}"
        return False

    remember_sent_missing_contexts(state, send_items)
    merge_seen_ids(state.setdefault("seen_ids", {}), send_seen_ids)
    merge_latest(state.setdefault("latest", {}), send_latest)
    state.pop("pending_delivery", None)
    state.pop("last_direct_delivery_error", None)
    state.pop("last_archive_error", None)
    state.pop("last_database_error", None)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["last_delivery_mode"] = "dashboard_database_only"
    return True


def main():
    started = time.monotonic()
    self_deadline = started + int(os.environ.get("X_WATCHLIST_DEADLINE_SECONDS", str(TOTAL_DEADLINE_SECONDS)))
    base_url, api_key = load_config()
    state = load_state()
    normalize_monitor_state_times(state)
    clear_stale_pending(state)
    apply_pending_if_delivered(state)
    if pending_in_cooldown(state):
        return
    if state.get("pending_delivery") and print_pending_and_exit(state):
        return
    seen = state.setdefault("seen_ids", {})
    latest = state.setdefault("latest", {})
    if (self_deadline - time.monotonic()) > 45:
        repair_sent_missing_contexts(base_url, api_key, state, self_deadline)
        if getattr(repair_sent_missing_contexts, "last_stats", {}).get("attempted"):
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            save_state(state)
    retry_items = load_context_retry_items(state, seen)
    if retry_items and (self_deadline - time.monotonic()) > 25:
        retry_items = repair_missing_contexts(
            base_url,
            api_key,
            retry_items,
            self_deadline,
            timeout_seconds=int(os.environ.get("X_WATCHLIST_HELD_CONTEXT_REPAIR_TIMEOUT_SECONDS", str(HELD_CONTEXT_REPAIR_TIMEOUT_SECONDS))),
            max_items_per_round=max(1, min(len(retry_items), int(os.environ.get("X_WATCHLIST_HELD_CONTEXT_REPAIR_ITEMS", str(MAX_CONTEXT_REPAIR_ITEMS))))),
        )
        repair_stats = getattr(repair_missing_contexts, "last_stats", {})
        if repair_stats.get("attempts"):
            state["last_context_repair"] = {
                **repair_stats,
                "phase": "held_first",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
        # If a held item is fixed, send it immediately in this same run instead of
        # waiting for the next watchlist fetch cycle. Unresolved held items remain
        # queued by send_ready_items().
        try:
            if send_ready_items(base_url, api_key, state, retry_items, latest, self_deadline):
                save_state(state)
                return
            # Persist held-first retry results immediately. A later full-watchlist
            # fetch may still hit the script alarm; don't lose the retry evidence
            # or refreshed held queue if that happens.
            save_state(state)
        except Exception as exc:
            state["last_database_error"] = f"{type(exc).__name__}: {exc}"
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            save_state(state)
            return
    for stale_key in ("last_fetch_at", "consecutive_empty_fetches", "last_fetch_issue", "last_fetch_failure_alert_at"):
        state.pop(stale_key, None)
    accounts = call_grok(base_url, api_key, latest)
    state["last_fetch_at"] = datetime.now(timezone.utc).isoformat()
    state["last_fetch_account_count"] = len(accounts)
    state["last_fetch_post_count"] = sum(len((account or {}).get("posts") or []) for account in accounts)
    state["last_fetch_issue"] = "empty_fetch" if not accounts else ""
    grok_issue = getattr(call_grok, "last_issue", "")
    if grok_issue:
        state["last_fetch_issue"] = grok_issue

    new_items = retry_items[:]
    new_item_keys = {(handle, post_id) for _display_name, _post, post_id, handle in new_items}
    pending_seen_ids = {}
    pending_latest = {}
    for account in accounts:
        handle = (account.get("handle") or "").lstrip("@").lower()
        if not handle:
            continue
        display_name = account.get("display_name") or handle
        posts = [
            normalize_post_time(post, post.get("post_id"))
            for post in (account.get("posts") or [])
            if isinstance(post, dict)
            and not post_time_is_implausible(post.get("time"), post.get("post_id"))
        ]
        account_seen = set(seen.get(handle, []))
        latest_value = latest.get(handle) or {}
        account_pending = []
        sortable_posts = []
        for post in posts:
            sortable_posts.append((canonical_post_time(post.get("time"), post.get("post_id")) or datetime.min, post))
        for _post_time, post in sorted(sortable_posts, key=lambda item: item[0]):
            post_id = str(post.get("post_id") or "").strip()
            if not post_id:
                text_key = (post.get("full_text") or post.get("chinese_text") or "")[:80]
                post_id = f"{handle}:{post.get('time','')}:{text_key}"
            post_time = canonical_post_time(post.get("time"), post_id)
            latest_time = canonical_post_time(
                (latest_value or {}).get("time"),
                (latest_value or {}).get("post_id"),
            )
            # Fetching only the latest post per account misses active accounts that
            # post multiple times in one 20-minute poll window. Now that Grok returns
            # recent 3, deliver any not-yet-seen item within a bounded lookback even
            # if it is older than the account's latest pointer.
            recent_unseen = bool(
                post_id not in account_seen
                and post_time
                and latest_time
                and post_time >= latest_time - timedelta(hours=RECENT_MISSING_BACKFILL_HOURS)
            )
            if post_id not in account_seen and (is_newer_post(post, latest_value, post_id) or recent_unseen) and (handle, post_id) not in new_item_keys:
                new_item_keys.add((handle, post_id))
                new_items.append((display_name, post, post_id, handle))
                account_pending.append(post_id)
                account_seen.add(post_id)
        if posts:
            pending_latest[handle] = choose_latest_value(latest_value, posts, display_name)
        if account_pending:
            pending_seen_ids[handle] = account_pending

    if not new_items:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        if pending_latest:
            merge_latest(state.setdefault("latest", {}), pending_latest)
        save_state(state)
        return

    remaining_for_details = self_deadline - time.monotonic()
    if remaining_for_details > 18:
        context_timeout = max(5, min(DETAIL_REQUEST_TIMEOUT_SECONDS, int(remaining_for_details - 10)))
        new_items = hydrate_posts(base_url, api_key, new_items, timeout=context_timeout)
        if (self_deadline - time.monotonic()) > 16:
            new_items = hydrate_missing_media_from_x_html(new_items, self_deadline)
        new_items = repair_missing_contexts(base_url, api_key, new_items, self_deadline)
        repair_stats = getattr(repair_missing_contexts, "last_stats", {})
        if repair_stats.get("attempts"):
            state["last_context_repair"] = repair_stats
        state["last_detail_issue"] = ""
    else:
        state["last_detail_issue"] = "skipped_low_time"

    try:
        if send_ready_items(base_url, api_key, state, new_items, latest, self_deadline):
            save_state(state)
            return
    except Exception as exc:
        state["last_database_error"] = f"{type(exc).__name__}: {exc}"

    # Do not fall back to stdout for X alerts. Leave seen/latest unmerged so the
    # next poll can retry the database write.
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["last_delivery_mode"] = "dashboard_database_failed_retry_pending"
    save_state(state)
    return


if __name__ == "__main__":
    def _deadline_alarm(_signum, _frame):
        raise TimeoutError("script self-timeout before cron hard limit")

    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _deadline_alarm)
        signal.alarm(int(os.environ.get("X_WATCHLIST_SCRIPT_ALARM_SECONDS", "90")))
    try:
        main()
    except urllib.error.HTTPError as exc:
        if exc.code in TEMPORARY_HTTP_CODES:
            # Temporary model gateway issue. Stay silent so cron does not alert the user.
            sys.exit(0)
        print(f"X 监控任务运行失败：{type(exc).__name__}: {exc}")
        sys.exit(1)
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        ModelResponseParseError,
    ) as exc:
        # Temporary network/model-output issue. Stay silent and try again next schedule.
        sys.exit(0)
    except Exception as exc:
        print(f"X 监控任务运行失败：{type(exc).__name__}: {exc}")
        sys.exit(1)
