"""FastAPI composition layer for the single-port NiuOne Dashboard.

FastAPI owns the listening socket, native v2 read-model routes, and the Vue
application. All browser-facing routes are declared by FastAPI; no second
HTTP server, internal proxy port, or ``BaseHTTPRequestHandler`` fallback is
started.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from contextlib import asynccontextmanager, closing
from pathlib import Path
from types import ModuleType
from typing import Any, AsyncIterator, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.staticfiles import StaticFiles

from app.dashboard.routers import (
    AdminAccess,
    create_admin_router,
    create_market_router,
    create_messages_router,
    create_practice_router,
    create_system_router,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WEB_DIST_DIR = PROJECT_ROOT / "web" / "dist"
GZIP_MIN_BYTES = int(os.environ.get("DASHBOARD_GZIP_MIN_BYTES", "1024") or "1024")
SPA_DASHBOARD_PATHS = (
    "/",
    "/practice",
    "/indices",
    "/industry-flow",
    "/dragon-tiger",
    "/market-monitor",
    "/x-monitor",
    "/us-ratings",
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; object-src 'none'"
    ),
}


def _legacy_module(module: ModuleType | None) -> ModuleType:
    if module is not None:
        return module
    from app.compat import niuone_dashboard as server

    return server


def _request_is_secure(request: Request, legacy: ModuleType) -> bool:
    client = request.client
    peer_ip = client.host if client else ""
    if not legacy.is_trusted_proxy_ip(peer_ip):
        return request.url.scheme == "https"
    if legacy.is_truthy_header(request.headers.get("X-Forwarded-Proto")):
        return True
    visitor = request.headers.get("CF-Visitor") or ""
    return '"scheme":"https"' in visitor.replace(" ", "").lower()


def _client_ip(request: Request, legacy: ModuleType) -> str:
    client = request.client
    peer_ip = client.host if client else ""
    if not legacy.is_trusted_proxy_ip(peer_ip):
        return peer_ip
    forwarded = legacy.first_forwarded_ip(
        request.headers.get("CF-Connecting-IP"),
        request.headers.get("X-Forwarded-For"),
    )
    return forwarded or peer_ip


def _rate_limit_response(
    request: Request,
    legacy: ModuleType,
    *,
    scope: str = "ip",
    limit: int | None = None,
    key: str | None = None,
) -> Response | None:
    ok, retry_after = legacy.check_rate_limit(
        scope,
        _client_ip(request, legacy) if key is None else key,
        legacy.RATE_LIMIT_ANON if limit is None else limit,
    )
    if ok:
        return None
    return JSONResponse(
        {"error": "rate_limited", "retry_after": retry_after},
        status_code=429,
        headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
    )


def _canonical_json_response(
    request: Request,
    value: dict[str, Any],
    *,
    cache_control: str,
    status_code: int = 200,
    etag: str = "",
) -> Response:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    resolved_etag = f'"{etag or hashlib.sha256(payload).hexdigest()}"'
    headers = {"Cache-Control": cache_control, "ETag": resolved_etag}
    if status_code == 200 and request.headers.get("If-None-Match", "").strip() == resolved_etag:
        return Response(status_code=304, headers=headers)
    content = b"" if request.method == "HEAD" else payload
    return Response(
        content=content,
        status_code=status_code,
        media_type="application/json",
        headers=headers,
    )


def create_app(
    *,
    legacy_module: ModuleType | None = None,
    web_dist_dir: Path | None = None,
    enable_background_services: bool = True,
) -> FastAPI:
    """Create the production single-port ASGI application."""

    legacy = _legacy_module(legacy_module)
    dist_dir = Path(web_dist_dir or DEFAULT_WEB_DIST_DIR).expanduser()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        projection_service = None
        if enable_background_services:
            legacy.ensure_stats_db()
            with closing(legacy.push_history.connect()):
                pass
            legacy.get_or_create_admin_token()
            legacy.start_b1_scheduler()
            legacy.start_pending_decision_executor()
            legacy.start_practice_equity_heartbeat()
            legacy.start_daily_market_history_reset()
            legacy.start_market_breadth_sampler()
            legacy.start_industry_flow_sampler()
            projection_enabled = str(
                os.environ.get("DASHBOARD_PUBLIC_PROJECTION_ENABLED", "1") or "1"
            ).strip().lower() not in {"0", "false", "no", "off"}
            if projection_enabled:
                from app.dashboard.projection_service import LegacyDashboardSources, ProjectionService

                projection_service = ProjectionService(
                    LegacyDashboardSources(legacy),
                    legacy.public_snapshot_publisher(),
                    interval_seconds=float(
                        os.environ.get("DASHBOARD_PUBLIC_REFRESH_SECONDS") or 15
                    ),
                )
                projection_service.start()
        app.state.projection_service = projection_service
        try:
            yield
        finally:
            if projection_service is not None:
                projection_service.stop()

    app = FastAPI(
        title="NiuOne Dashboard API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=GZIP_MIN_BYTES, compresslevel=5)

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            if name not in response.headers:
                response.headers[name] = value
        if _request_is_secure(request, legacy):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        if request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    app.mount(
        "/assets",
        StaticFiles(directory=str(dist_dir / "assets"), check_dir=False),
        name="vue-assets",
    )

    async def serve_vue(request: Request) -> Response:
        limited = _rate_limit_response(request, legacy)
        if limited is not None:
            return limited
        index_path = dist_dir / "index.html"
        if not index_path.is_file():
            return JSONResponse(
                {
                    "error": "vue_frontend_not_built",
                    "hint": "run pnpm --dir web install && pnpm --dir web build",
                },
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        if request.method == "HEAD":
            return Response(
                status_code=200,
                media_type="text/html",
                headers={
                    "Cache-Control": "no-store",
                    "X-NiuOne-Frontend": "vue3-vite",
                },
            )
        return FileResponse(
            index_path,
            media_type="text/html",
            headers={
                "Cache-Control": "no-store",
                "X-NiuOne-Frontend": "vue3-vite",
            },
        )

    for path in SPA_DASHBOARD_PATHS:
        app.add_api_route(path, serve_vue, methods=["GET", "HEAD"], include_in_schema=False)
    app.add_api_route("/admin", serve_vue, methods=["GET", "HEAD"], include_in_schema=False)

    @app.api_route("/admin/settings/{group_slug}", methods=["GET", "HEAD"], include_in_schema=False)
    async def admin_group(request: Request, group_slug: str) -> Response:
        if group_slug not in legacy.ADMIN_SETTING_GROUP_BY_SLUG:
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        return await serve_vue(request)

    async def enforce_native_api_limits(request: Request) -> Response | None:
        limited = _rate_limit_response(request, legacy)
        if limited is not None:
            return limited
        if request.method == "HEAD":
            return None
        return _rate_limit_response(
            request,
            legacy,
            scope="api",
            limit=legacy.RATE_LIMIT_API,
        )

    async def cached_native_api_response(
        request: Request,
        *,
        cache_key: str,
        ttl: int,
        producer: Callable[[], dict[str, Any]],
        edge_ttl: int,
        browser_ttl: int,
        before_cache: Callable[[], Any] | None = None,
        enforce_limits: bool = True,
    ) -> Response:
        if enforce_limits:
            limited = await enforce_native_api_limits(request)
            if limited is not None:
                return limited
        if request.method == "HEAD":
            return Response(
                status_code=200,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )

        def load_payload() -> tuple[bytes, bool]:
            if before_cache is not None:
                before_cache()
            return legacy.cache_get_json(cache_key, ttl, producer)

        payload, cache_hit = await run_in_threadpool(load_payload)
        resolved_browser_ttl = min(browser_ttl, ttl)
        if edge_ttl > 0 and legacy.EDGE_CACHE_ENABLED:
            cache_control = (
                f"public, max-age={resolved_browser_ttl}, s-maxage={edge_ttl}, "
                f"stale-while-revalidate={edge_ttl * 2}"
            )
            cdn_cache_control = (
                f"public, max-age={edge_ttl}, stale-while-revalidate={edge_ttl * 2}"
            )
        elif edge_ttl > 0:
            cache_control = (
                f"private, max-age={resolved_browser_ttl}, "
                f"stale-while-revalidate={max(resolved_browser_ttl, edge_ttl)}"
            )
            cdn_cache_control = "no-store"
        else:
            cache_control = "no-store"
            cdn_cache_control = "no-store"
        return Response(
            content=payload,
            status_code=200,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Cache-Control": cache_control,
                "CDN-Cache-Control": cdn_cache_control,
                "X-Dashboard-Cache": "HIT" if cache_hit else "MISS",
            },
        )

    app.include_router(
        create_system_router(
            services=legacy,
            enforce_api_limits=enforce_native_api_limits,
            enforce_public_limit=lambda request: _rate_limit_response(request, legacy),
            json_response=_canonical_json_response,
            request_is_secure=lambda request: _request_is_secure(request, legacy),
        )
    )
    app.include_router(
        create_messages_router(
            services=legacy,
            cached_response=cached_native_api_response,
        )
    )
    app.include_router(
        create_market_router(
            services=legacy,
            cached_response=cached_native_api_response,
            enforce_api_limits=enforce_native_api_limits,
        )
    )

    admin_access = AdminAccess(
        services=legacy,
        rate_limit=lambda request, **kwargs: _rate_limit_response(
            request,
            legacy,
            **kwargs,
        ),
    )
    app.include_router(
        create_admin_router(
            services=legacy,
            access=admin_access,
            rate_limit=lambda request, **kwargs: _rate_limit_response(
                request,
                legacy,
                **kwargs,
            ),
            client_ip=lambda request: _client_ip(request, legacy),
            request_is_secure=lambda request: _request_is_secure(request, legacy),
            json_response=_canonical_json_response,
        )
    )
    app.include_router(
        create_practice_router(
            services=legacy,
            cached_response=cached_native_api_response,
            enforce_api_limits=enforce_native_api_limits,
            require_admin_action=admin_access.require_action,
            rate_limit=lambda request, **kwargs: _rate_limit_response(
                request,
                legacy,
                **kwargs,
            ),
            json_response=_canonical_json_response,
        )
    )

    return app


def run(*, host: str, port: int, legacy_module: ModuleType | None = None) -> None:
    """Run the ASGI application on the only Dashboard listener."""

    import uvicorn

    app = create_app(legacy_module=legacy_module)
    uvicorn.run(
        app,
        host=host,
        port=port,
        proxy_headers=False,
        server_header=False,
        access_log=True,
    )


if __name__ == "__main__":  # pragma: no cover
    run(
        host=os.environ.get("DASHBOARD_HOST", "127.0.0.1"),
        port=int(os.environ.get("DASHBOARD_PORT", "8787")),
        legacy_module=sys.modules.get("app.dashboard.server"),
    )
