#!/usr/bin/env python3
"""Client helper for NiuOne paper-trading contests.

The normal local paper account remains authoritative for daily use. This module
only mirrors successful local fills to a contest server and annotates each local
trade with its official contest status.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from niuone_paths import get_dashboard_home

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
STATE_LOCK = threading.Lock()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    try:
        value = os.environ.get(name)
        return float(value) if value else default
    except (TypeError, ValueError):
        return default


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_payload(payload: Any) -> str:
    try:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        raw = str(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sign_payload(secret: str, payload: dict[str, Any]) -> str:
    return hmac.new(secret.encode("utf-8"), canonical_json(payload), hashlib.sha256).hexdigest()


def now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def state_file() -> Path:
    return Path(
        os.environ.get("DASHBOARD_CONTEST_STATE")
        or DASHBOARD_HOME / "contest_client_state.json"
    ).expanduser()


def load_state() -> dict[str, Any]:
    path = state_file()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"contests": {}}


def save_state(state: dict[str, Any]) -> None:
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def base_url(state: dict[str, Any] | None = None) -> str:
    env_value = str(os.environ.get("DASHBOARD_CONTEST_SERVER_URL") or "").strip().rstrip("/")
    if env_value:
        return env_value
    if state is None:
        state = load_state()
    return str((state or {}).get("server_url") or "").strip().rstrip("/")


def contest_id(state: dict[str, Any] | None = None) -> str:
    env_value = str(os.environ.get("DASHBOARD_CONTEST_ID") or "").strip()
    if env_value:
        return env_value
    if state is None:
        state = load_state()
    return str((state or {}).get("active_contest_id") or "").strip()


def nickname() -> str:
    return str(os.environ.get("DASHBOARD_CONTEST_NICKNAME") or "").strip() or "NiuOne participant"


def timeout_seconds() -> float:
    return max(0.5, env_float("DASHBOARD_CONTEST_TIMEOUT_SECONDS", 3.0))


def is_enabled() -> bool:
    state = load_state()
    enabled_raw = os.environ.get("DASHBOARD_CONTEST_ENABLED")
    if enabled_raw is not None and str(enabled_raw).strip() != "":
        enabled = env_bool("DASHBOARD_CONTEST_ENABLED", False)
    else:
        enabled = bool(state.get("enabled"))
    return enabled and bool(base_url(state)) and bool(contest_id(state))


def _request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    user_token: str = "",
    server_url: str = "",
) -> dict[str, Any]:
    root = (server_url or base_url()).strip().rstrip("/")
    if not root:
        return {"ok": False, "error": "missing_server_url"}
    url = root + path
    data = None
    headers = {"User-Agent": "NiuOne contest client"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if user_token:
        headers["Authorization"] = f"Bearer {user_token}"
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds()) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {"ok": False, "error": f"http_{exc.code}", "detail": body[:300]}


def get_text(path: str, *, user_token: str = "", server_url: str = "") -> str:
    root = (server_url or base_url()).strip().rstrip("/")
    if not root:
        return ""
    headers = {"User-Agent": "NiuOne contest client"}
    if user_token:
        headers["Authorization"] = f"Bearer {user_token}"
    req = urllib.request.Request(root + path, method="GET", headers=headers)
    with urllib.request.urlopen(req, timeout=max(timeout_seconds(), 35.0)) as resp:
        return resp.read().decode("utf-8", "ignore")


def post_json(
    path: str,
    payload: dict[str, Any],
    *,
    user_token: str = "",
    server_url: str = "",
) -> dict[str, Any]:
    return _request_json("POST", path, payload, user_token=user_token, server_url=server_url)


def get_json(path: str, *, user_token: str = "", server_url: str = "") -> dict[str, Any]:
    return _request_json("GET", path, None, user_token=user_token, server_url=server_url)


def parse_sse_events(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {"event": "message", "data_lines": []}
    for line in str(raw or "").splitlines():
        if line == "":
            data_text = "\n".join(current.get("data_lines") or [])
            if data_text:
                try:
                    data: Any = json.loads(data_text)
                except Exception:
                    data = data_text
                event_id = current.get("id")
                try:
                    event_id = int(event_id)
                except Exception:
                    event_id = 0
                events.append({
                    "id": event_id,
                    "event": current.get("event") or "message",
                    "data": data,
                })
            current = {"event": "message", "data_lines": []}
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "id":
            current["id"] = value
        elif field == "event":
            current["event"] = value
        elif field == "data":
            current.setdefault("data_lines", []).append(value)
    return events


def contest_state(state: dict[str, Any], cid: str) -> dict[str, Any]:
    contests = state.setdefault("contests", {})
    item = contests.setdefault(cid, {})
    if not isinstance(item, dict):
        contests[cid] = {}
        item = contests[cid]
    return item


def configured_credentials() -> tuple[str, str]:
    participant_id = str(os.environ.get("DASHBOARD_CONTEST_PARTICIPANT_ID") or "").strip()
    secret = str(os.environ.get("DASHBOARD_CONTEST_SECRET") or "").strip()
    return participant_id, secret


def _store_auth_result(state: dict[str, Any], result: dict[str, Any], server_url: str) -> None:
    state["server_url"] = server_url.strip().rstrip("/")
    if result.get("ok"):
        state["user_token"] = str(result.get("user_token") or "").strip()
        state["user"] = result.get("user") or {}
        state["logged_in_at"] = now_ts()
        state.pop("last_auth_error", None)
    else:
        state["last_auth_error"] = result.get("error") or "auth_failed"


def register_user(server_url: str, username: str, password: str, nickname_value: str = "") -> dict[str, Any]:
    normalized_server = str(server_url or "").strip().rstrip("/")
    with STATE_LOCK:
        state = load_state()
        result = post_json(
            "/api/users/register",
            {"username": username, "password": password, "nickname": nickname_value},
            server_url=normalized_server,
        )
        _store_auth_result(state, result, normalized_server)
        save_state(state)
        return result


def login_user(server_url: str, username: str, password: str) -> dict[str, Any]:
    normalized_server = str(server_url or "").strip().rstrip("/")
    with STATE_LOCK:
        state = load_state()
        result = post_json(
            "/api/users/login",
            {"username": username, "password": password},
            server_url=normalized_server,
        )
        _store_auth_result(state, result, normalized_server)
        save_state(state)
        return result


def start_linuxdo_login(server_url: str, client_callback: str) -> dict[str, Any]:
    normalized_server = str(server_url or "").strip().rstrip("/")
    with STATE_LOCK:
        state = load_state()
        result = post_json(
            "/api/auth/linuxdo/start",
            {"client_callback": str(client_callback or "").strip()},
            server_url=normalized_server,
        )
        state["server_url"] = normalized_server
        if result.get("ok"):
            state["linuxdo_login_started_at"] = now_ts()
            state["linuxdo_provider"] = "linuxdo"
            state.pop("last_auth_error", None)
        else:
            state["last_auth_error"] = result.get("error") or "linuxdo_start_failed"
        save_state(state)
        return result


def complete_linuxdo_login(server_url: str, ticket: str) -> dict[str, Any]:
    normalized_server = str(server_url or "").strip().rstrip("/")
    with STATE_LOCK:
        state = load_state()
        result = post_json(
            "/api/auth/linuxdo/ticket",
            {"ticket": str(ticket or "").strip()},
            server_url=normalized_server,
        )
        _store_auth_result(state, result, normalized_server)
        if result.get("ok"):
            state["linuxdo_logged_in_at"] = now_ts()
            state["linuxdo_provider"] = "linuxdo"
        save_state(state)
        return result


def fetch_linuxdo_status(server_url: str = "") -> dict[str, Any]:
    root = str(server_url or base_url()).strip().rstrip("/")
    if not root:
        return {"ok": False, "error": "missing_server_url", "configured": False}
    return get_json("/api/auth/linuxdo/status", server_url=root)


def logout_user() -> dict[str, Any]:
    with STATE_LOCK:
        state = load_state()
        state.pop("user_token", None)
        state.pop("user", None)
        state["logged_out_at"] = now_ts()
        save_state(state)
    return {"ok": True}


def fetch_contests(server_url: str = "") -> dict[str, Any]:
    with STATE_LOCK:
        state = load_state()
        root = str(server_url or base_url(state)).strip().rstrip("/")
        token = str(state.get("user_token") or "").strip()
        if not token:
            return {"ok": False, "error": "auth_required", "items": []}
        result = get_json("/api/contests", user_token=token, server_url=root)
        if result.get("ok"):
            state["server_url"] = root
            state["available_contests"] = result.get("items") or []
            state["contests_last_loaded_at"] = now_ts()
            state.pop("last_contests_error", None)
        else:
            state["last_contests_error"] = result.get("error") or "load_failed"
        save_state(state)
        return result


def join_contest(cid: str) -> dict[str, Any]:
    cid = str(cid or "").strip()
    if not cid:
        return {"ok": False, "error": "missing_contest_id"}
    with STATE_LOCK:
        state = load_state()
        root = base_url(state)
        token = str(state.get("user_token") or "").strip()
        if not token:
            return {"ok": False, "error": "auth_required"}
        result = post_json(
            f"/api/contests/{urllib.parse.quote(cid)}/join",
            {},
            user_token=token,
            server_url=root,
        )
        item = contest_state(state, cid)
        if result.get("ok"):
            participant_id = str(result.get("participant_id") or "").strip()
            secret = str(result.get("participant_secret") or "").strip()
            item.update({
                "participant_id": participant_id,
                "participant_secret": secret,
                "nickname": result.get("nickname") or (state.get("user") or {}).get("nickname") or nickname(),
                "joined_at": now_ts(),
            })
            state["active_contest_id"] = cid
            state["enabled"] = True
            state.pop("last_join_error", None)
        else:
            item["last_error"] = result.get("error") or "join_failed"
            state["last_join_error"] = item["last_error"]
        save_state(state)
        return result


def fetch_contest_events() -> dict[str, Any]:
    with STATE_LOCK:
        state = load_state()
        root = base_url(state)
        token = str(state.get("user_token") or "").strip()
        if not token:
            return {"ok": False, "error": "auth_required", "events": []}
        since_id = int(state.get("last_event_id") or 0)
        try:
            raw = get_text(f"/api/contests/events?since={since_id}", user_token=token, server_url=root)
            events = parse_sse_events(raw)
        except Exception as exc:
            state["last_event_error"] = f"{type(exc).__name__}: {exc}"
            save_state(state)
            return {"ok": False, "error": state["last_event_error"], "events": []}
        if events:
            state["last_event_id"] = max(int(item.get("id") or 0) for item in events)
            state.pop("last_event_error", None)
        save_state(state)
        return {"ok": True, "events": events}


def client_status(fetch_remote: bool = False) -> dict[str, Any]:
    state = load_state()
    active_id = contest_id(state)
    active = contest_state(state, active_id) if active_id else {}
    payload = {
        "ok": True,
        "enabled": is_enabled(),
        "server_url": base_url(state),
        "active_contest_id": active_id,
        "participant_id": active.get("participant_id") or "",
        "logged_in": bool(state.get("user_token")),
        "user": state.get("user") or {},
        "linuxdo": state.get("linuxdo") or {},
        "available_contests": state.get("available_contests") or [],
        "last_error": state.get("last_auth_error") or state.get("last_contests_error") or state.get("last_join_error") or "",
    }
    if fetch_remote and payload["server_url"]:
        try:
            linuxdo = fetch_linuxdo_status(payload["server_url"])
            payload["linuxdo"] = linuxdo
            state["linuxdo"] = linuxdo
            save_state(state)
        except Exception as exc:
            payload["linuxdo"] = {"ok": False, "configured": False, "error": f"{type(exc).__name__}: {exc}"}
    if fetch_remote and payload["logged_in"]:
        payload["contests"] = fetch_contests()
    return payload


def ensure_participant(state: dict[str, Any]) -> tuple[str, str] | None:
    cid = contest_id(state)
    env_participant_id, env_secret = configured_credentials()
    if env_participant_id and env_secret:
        item = contest_state(state, cid)
        item["participant_id"] = env_participant_id
        item["participant_secret"] = env_secret
        return env_participant_id, env_secret
    item = contest_state(state, cid)
    participant_id = str(item.get("participant_id") or "").strip()
    secret = str(item.get("participant_secret") or "").strip()
    if participant_id and secret:
        return participant_id, secret
    try:
        result = post_json(f"/api/contests/{urllib.parse.quote(cid)}/register", {"nickname": nickname()})
    except Exception as exc:
        item["last_error"] = f"{type(exc).__name__}: {exc}"
        return None
    if not result.get("ok"):
        item["last_error"] = result.get("error") or "register_failed"
        return None
    participant_id = str(result.get("participant_id") or "").strip()
    secret = str(result.get("participant_secret") or "").strip()
    if not participant_id or not secret:
        item["last_error"] = "register_missing_credentials"
        return None
    item.update({
        "participant_id": participant_id,
        "participant_secret": secret,
        "registered_at": now_ts(),
        "nickname": nickname(),
    })
    return participant_id, secret


def local_trade_id(trade: dict[str, Any]) -> str:
    payload = {
        "time": trade.get("time"),
        "action": trade.get("action"),
        "code": trade.get("code"),
        "shares": trade.get("shares"),
        "price": trade.get("price"),
        "amount": trade.get("amount"),
        "reason": trade.get("reason"),
    }
    return "lt_" + digest_payload(payload)[:24]


def current_strategy_hash() -> str:
    payload = {
        "strategy_source": os.environ.get("DASHBOARD_STRATEGY_SOURCE", ""),
        "persona": os.environ.get("DASHBOARD_ENABLED_PERSONA_STRATEGIES", ""),
        "preset_text": os.environ.get("DASHBOARD_PRESET_STRATEGY_TEXT", ""),
        "trade_discipline": os.environ.get("DASHBOARD_TRADE_DISCIPLINE_TEXT", ""),
    }
    return digest_payload(payload)


def build_order_payload(
    trade: dict[str, Any],
    *,
    participant_id: str,
    seq: int,
    quote_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cid = contest_id()
    quote_snapshot = quote_snapshot or {}
    trade_id = str(trade.get("local_trade_id") or local_trade_id(trade))
    trade["local_trade_id"] = trade_id
    return {
        "participant_id": participant_id,
        "client_order_id": f"{trade_id}:{cid}",
        "seq": seq,
        "side": str(trade.get("action") or "").upper(),
        "symbol": str(trade.get("code") or ""),
        "name": str(trade.get("name") or ""),
        "quantity": int(trade.get("shares") or 0),
        "client_submitted_at": now_ts(),
        "client_trade_time": str(trade.get("time") or ""),
        "client_quote_time": str(trade.get("quote_time") or quote_snapshot.get("quote_time") or trade.get("time") or ""),
        "client_fill_price": float(trade.get("price") or 0),
        "quote_source": str(trade.get("quote_source") or trade.get("price_source") or quote_snapshot.get("source") or ""),
        "raw_quote_hash": str(trade.get("raw_quote_hash") or digest_payload(quote_snapshot or {
            "price": trade.get("price"),
            "quote_time": trade.get("quote_time"),
            "quote_source": trade.get("quote_source") or trade.get("price_source"),
        })),
        "strategy_hash": current_strategy_hash(),
    }


def apply_result_to_trade(trade: dict[str, Any], result: dict[str, Any], cid: str) -> None:
    trade["contest_id"] = cid
    trade["contest_order_id"] = result.get("client_order_id") or result.get("contest_order_id") or ""
    trade["contest_status"] = result.get("status") or ("official" if result.get("ok") else "upload_failed")
    trade["official_fill_price"] = result.get("official_fill_price")
    trade["official_received_at"] = result.get("server_received_at") or ""
    trade["contest_reject_reason"] = result.get("reject_reason") or result.get("error") or ""
    if result.get("server_quote_price") is not None:
        trade["contest_server_quote_price"] = result.get("server_quote_price")
    if result.get("price_diff_pct") is not None:
        trade["contest_price_diff_pct"] = result.get("price_diff_pct")


def submit_trade(trade: dict[str, Any], quote_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    if not is_enabled():
        return {"ok": False, "status": "disabled"}
    cid = contest_id()
    with STATE_LOCK:
        state = load_state()
        item = contest_state(state, cid)
        creds = ensure_participant(state)
        if not creds:
            save_state(state)
            result = {"ok": False, "status": "register_failed", "error": item.get("last_error") or "register_failed"}
            apply_result_to_trade(trade, result, cid)
            return result
        participant_id, secret = creds
        seq = int(item.get("last_seq") or 0) + 1
        payload = build_order_payload(trade, participant_id=participant_id, seq=seq, quote_snapshot=quote_snapshot)
        payload["signature"] = sign_payload(secret, payload)
        try:
            result = post_json(f"/api/contests/{urllib.parse.quote(cid)}/orders", payload)
        except Exception as exc:
            result = {"ok": False, "status": "upload_failed", "error": f"{type(exc).__name__}: {exc}"}
        reject_reason = str(result.get("reject_reason") or "")
        if (
            result.get("server_received_at")
            and result.get("status") != "upload_failed"
            and not reject_reason.startswith("seq_mismatch")
        ):
            item["last_seq"] = max(int(item.get("last_seq") or 0), seq)
            item["last_order_at"] = now_ts()
        if result.get("error"):
            item["last_error"] = result.get("error")
        save_state(state)
    apply_result_to_trade(trade, result, cid)
    return result


def submit_trades(trades: list[dict[str, Any]]) -> None:
    if not trades or not is_enabled():
        return
    for trade in trades:
        quote_snapshot = trade.get("_contest_quote_snapshot") if isinstance(trade.get("_contest_quote_snapshot"), dict) else {}
        try:
            submit_trade(trade, quote_snapshot=quote_snapshot)
        except Exception as exc:
            apply_result_to_trade(trade, {"status": "upload_failed", "error": f"{type(exc).__name__}: {exc}"}, contest_id())
        finally:
            trade.pop("_contest_quote_snapshot", None)
