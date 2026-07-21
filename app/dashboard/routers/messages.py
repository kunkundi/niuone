"""Message history and lightweight revision FastAPI routes."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response


CachedResponder = Callable[..., Awaitable[Response]]


def messages_revision_payload(
    payload: dict[str, Any],
    category: str,
    *,
    page_limit: int | None = None,
    page_offset: int = 0,
) -> dict[str, Any]:
    """Project a message page to the fields needed for change detection."""

    records = payload.get("records")
    normalized_records = records if isinstance(records, list) else []
    latest = normalized_records[0] if normalized_records else {}
    if not isinstance(latest, dict):
        latest = {}
    category_data = (payload.get("categories") or {}).get(category) or {}
    if not isinstance(category_data, dict):
        category_data = {}
    result: dict[str, Any] = {
        "category": category,
        "count": int(category_data.get("count") or 0),
        "latest": {
            "id": str(latest.get("id") or ""),
            "timestamp": latest.get("timestamp"),
            "content_hash": str(latest.get("content_hash") or ""),
            "updated_at": str(latest.get("updated_at") or ""),
        },
    }
    if page_limit is not None:
        signature = [
            [
                str(record.get("id") or record.get("raw_path") or record.get("external_id") or ""),
                record.get("timestamp"),
                str(record.get("content_hash") or ""),
                str(record.get("updated_at") or ""),
                record.get("metadata") if isinstance(record.get("metadata"), dict) else {},
            ]
            for record in normalized_records
            if isinstance(record, dict)
        ]
        encoded = json.dumps(
            signature,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        result["page"] = {
            "limit": int(page_limit),
            "offset": int(page_offset),
            "count": len(normalized_records),
            "fingerprint": hashlib.sha256(encoded).hexdigest(),
        }
    return result


def message_page_payload(
    payload: dict[str, Any],
    category: str | None,
    *,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Attach the X page fingerprint used by its lightweight refresh loop."""

    if category != "x_monitor":
        return payload
    return {
        **payload,
        "revision": messages_revision_payload(
            payload,
            category,
            page_limit=limit,
            page_offset=offset,
        ),
    }


def create_messages_router(*, services: Any, cached_response: CachedResponder) -> APIRouter:
    """Create native read routes for message pages and their revisions."""

    router = APIRouter(include_in_schema=False)

    @router.api_route("/api/messages", methods=["GET", "HEAD"])
    async def messages(request: Request) -> Response:
        limit = services.clamp_limit(request.query_params.get("limit"))
        offset = services.clamp_offset(request.query_params.get("offset"))
        category = str(request.query_params.get("category") or "").strip() or None
        ttl = services.API_TTLS["messages"]
        return await cached_response(
            request,
            cache_key=f"messages:v4:{category or 'all'}:{limit}:{offset}",
            ttl=ttl,
            producer=lambda: message_page_payload(
                services.merge_records_from_db(
                    limit=limit,
                    category=category,
                    offset=offset,
                ),
                category,
                limit=limit,
                offset=offset,
            ),
            edge_ttl=ttl,
            browser_ttl=5,
        )

    @router.api_route("/api/messages/revision", methods=["GET", "HEAD"])
    async def messages_revision(request: Request) -> Response:
        category = str(request.query_params.get("category") or "").strip()
        if re.fullmatch(r"[a-z0-9_]{1,64}", category) is None:
            return JSONResponse(
                {"error": "message_category_required"},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        page_requested = "limit" in request.query_params or "offset" in request.query_params
        limit = services.clamp_limit(request.query_params.get("limit"), default=10)
        offset = services.clamp_offset(request.query_params.get("offset"))
        ttl = services.API_TTLS["messages"]
        cache_key = (
            f"messages-revision:v2:{category}:{limit}:{offset}"
            if page_requested
            else f"messages-revision:v1:{category}"
        )
        return await cached_response(
            request,
            cache_key=cache_key,
            ttl=ttl,
            producer=lambda: messages_revision_payload(
                services.merge_records_from_db(
                    limit=limit if page_requested else 1,
                    category=category,
                    offset=offset if page_requested else 0,
                ),
                category,
                page_limit=limit if page_requested else None,
                page_offset=offset if page_requested else 0,
            ),
            edge_ttl=ttl,
            browser_ttl=5,
        )

    return router
