"""System, bootstrap, and immutable public-snapshot FastAPI routes."""

from __future__ import annotations

import re
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool


LimitCheck = Callable[[Request], Awaitable[Response | None]]
PublicLimitCheck = Callable[[Request], Response | None]
JsonResponder = Callable[..., Response]
SecureRequestCheck = Callable[[Request], bool]
MESSAGE_COUNT_CATEGORIES = ("market_monitor", "x_monitor", "us_ratings")


def dashboard_message_counts(payload: dict[str, Any]) -> dict[str, int]:
    """Return the lightweight message counts shown in Dashboard navigation."""

    categories = payload.get("categories")
    if not isinstance(categories, dict):
        categories = {}
    result: dict[str, int] = {}
    for category in MESSAGE_COUNT_CATEGORIES:
        value = categories.get(category)
        raw_count = value.get("count") if isinstance(value, dict) else value
        try:
            count = int(raw_count or 0)
        except (TypeError, ValueError, OverflowError):
            count = 0
        result[category] = max(0, count)
    return result


def create_system_router(
    *,
    services: Any,
    enforce_api_limits: LimitCheck,
    enforce_public_limit: PublicLimitCheck,
    json_response: JsonResponder,
    request_is_secure: SecureRequestCheck,
) -> APIRouter:
    """Create routes that expose process health and public read-model metadata."""

    router = APIRouter(include_in_schema=False)

    @router.api_route("/healthz", methods=["GET", "HEAD"])
    async def health(request: Request) -> Response:
        limited = enforce_public_limit(request)
        if limited is not None:
            return limited
        latest = services.public_snapshot_publisher().read_latest()
        return json_response(
            request,
            {
                "ok": True,
                "plane": "fastapi",
                "frontend": "vue3-vite",
                "snapshot_ready": latest is not None,
                "revision": int((latest or {}).get("revision") or 0),
            },
            cache_control="no-store",
        )

    @router.api_route("/api/version", methods=["GET", "HEAD"])
    async def version_status(request: Request) -> Response:
        limited = await enforce_api_limits(request)
        if limited is not None:
            return limited
        if request.method == "HEAD":
            return Response(
                status_code=200,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        payload = await run_in_threadpool(services.get_version_status)
        return json_response(request, payload, cache_control="no-store")

    @router.api_route("/api/dashboard/bootstrap", methods=["GET", "HEAD"])
    async def dashboard_bootstrap(request: Request) -> Response:
        limited = await enforce_api_limits(request)
        if limited is not None:
            return limited
        if request.method == "HEAD":
            return Response(
                status_code=200,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )

        cookies = services.parse_request_cookies(request.headers.get("Cookie"))
        visitor_id = str(cookies.get(services.VISITOR_COOKIE_NAME, "") or "").strip()
        new_visitor = re.fullmatch(r"nvst_[A-Za-z0-9_-]{20,80}", visitor_id) is None
        if new_visitor:
            visitor_id = "nvst_" + secrets.token_urlsafe(24)
        visit_stats = await run_in_threadpool(services.increment_visit_count, visitor_id)
        try:
            message_payload = await run_in_threadpool(
                services.merge_records_from_db,
                limit=0,
            )
            message_counts = dashboard_message_counts(message_payload)
            message_counts_available = True
        except Exception:
            # Navigation counts are optional. Keep feature flags and visitor
            # bootstrap available when the independent message store is down.
            message_counts = {}
            message_counts_available = False
        payload = {
            "visits": visit_stats["visits"],
            "unique": visit_stats["unique"],
            "us_features_enabled": services.us_features_enabled(),
            "message_counts": message_counts,
            "message_counts_available": message_counts_available,
        }
        response = json_response(request, payload, cache_control="no-store")
        if new_visitor:
            secure = "; Secure" if request_is_secure(request) else ""
            response.headers["Set-Cookie"] = (
                f"{services.VISITOR_COOKIE_NAME}={visitor_id}; "
                f"Path=/; Max-Age=31536000; SameSite=Lax{secure}"
            )
        return response

    @router.api_route("/api/v2/public/latest", methods=["GET", "HEAD"])
    async def public_latest(request: Request) -> Response:
        limited = enforce_public_limit(request)
        if limited is not None:
            return limited
        latest = services.public_snapshot_publisher().read_latest()
        if latest is None:
            return json_response(
                request,
                {"error": "public_snapshot_not_ready"},
                cache_control="no-store",
                status_code=503,
            )
        return json_response(
            request,
            latest,
            cache_control="public, max-age=5, s-maxage=5, stale-while-revalidate=30",
        )

    @router.api_route("/api/v2/public/manifests/{revision}.json", methods=["GET", "HEAD"])
    async def public_manifest(request: Request, revision: int) -> Response:
        limited = enforce_public_limit(request)
        if limited is not None:
            return limited
        manifest = services.public_snapshot_publisher().read_manifest(revision)
        if manifest is None:
            return JSONResponse(
                {"error": "manifest_not_found"},
                status_code=404,
                headers={"Cache-Control": "no-store"},
            )
        return json_response(
            request,
            manifest,
            cache_control="public, max-age=31536000, immutable",
        )

    @router.api_route("/api/v2/public/objects/{digest}.json", methods=["GET", "HEAD"])
    async def public_object(request: Request, digest: str) -> Response:
        limited = enforce_public_limit(request)
        if limited is not None:
            return limited
        value = services.public_snapshot_publisher().read_object(digest)
        if value is None:
            return JSONResponse(
                {"error": "object_not_found"},
                status_code=404,
                headers={"Cache-Control": "no-store"},
            )
        return json_response(
            request,
            value,
            cache_control="public, max-age=31536000, immutable",
            etag=digest,
        )

    @router.api_route("/api/v2/public/{remainder:path}", methods=["GET", "HEAD", "POST"])
    async def missing_public_api(remainder: str) -> Response:
        del remainder
        return JSONResponse(
            {"error": "not_found"},
            status_code=404,
            headers={"Cache-Control": "no-store"},
        )

    return router
