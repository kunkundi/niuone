"""Practice-account read models and protected action FastAPI routes."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool


CachedResponder = Callable[..., Awaitable[Response]]
LimitEnforcer = Callable[[Request], Awaitable[Response | None]]
JsonResponder = Callable[..., Response]
AdminActionGuard = Callable[..., Awaitable[Response | None]]
RateLimiter = Callable[..., Response | None]


def create_practice_router(
    *,
    services: Any,
    cached_response: CachedResponder,
    enforce_api_limits: LimitEnforcer,
    require_admin_action: AdminActionGuard,
    rate_limit: RateLimiter,
    json_response: JsonResponder,
) -> APIRouter:
    """Create practice data, status, report, and action routes."""

    router = APIRouter(include_in_schema=False)

    async def practice_candidates_response(request: Request) -> Response:
        if str(request.query_params.get("force") or "0").lower() in {
            "1",
            "true",
            "yes",
        }:
            limited = await enforce_api_limits(request)
            if limited is not None:
                return limited
            return JSONResponse(
                {"error": "method_not_allowed"},
                status_code=405,
                headers={"Allow": "POST", "Cache-Control": "no-store"},
            )
        ttl = services.API_TTLS["practice_candidates"]
        return await cached_response(
            request,
            cache_key=services.PRACTICE_CANDIDATES_CACHE_KEY,
            ttl=ttl,
            producer=services.load_practice_candidates_cache,
            edge_ttl=ttl,
            browser_ttl=10,
        )

    for path in services.PRACTICE_CANDIDATES_API_PATHS:
        router.add_api_route(path, practice_candidates_response, methods=["GET", "HEAD"])

    @router.api_route("/api/niuniu_practice", methods=["GET", "HEAD"])
    async def niuniu_practice(request: Request) -> Response:
        fast = str(request.query_params.get("fast") or "0").lower() in {
            "1",
            "true",
            "yes",
        }
        ttl = services.API_TTLS["niuniu_practice"]
        return await cached_response(
            request,
            cache_key=services.PRACTICE_FAST_CACHE_KEY if fast else "niuniu_practice",
            ttl=ttl,
            producer=(
                services.get_practice_payload_fast
                if fast
                else services.get_practice_payload
            ),
            edge_ttl=ttl,
            browser_ttl=10,
        )

    async def uncached_status(
        request: Request,
        producer: Callable[[], dict[str, Any]],
    ) -> Response:
        limited = await enforce_api_limits(request)
        if limited is not None:
            return limited
        if request.method == "HEAD":
            return Response(
                status_code=200,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        payload = await run_in_threadpool(producer)
        return json_response(request, payload, cache_control="no-store")

    @router.api_route("/api/niuniu_practice/manual-cycle", methods=["GET", "HEAD"])
    async def practice_manual_cycle(request: Request) -> Response:
        return await uncached_status(request, services.practice_manual_cycle_status)

    @router.api_route("/api/niuniu_practice/market-summary", methods=["GET", "HEAD"])
    async def practice_market_summary(request: Request) -> Response:
        return await uncached_status(
            request,
            services.get_practice_market_summary_status,
        )

    @router.api_route("/api/self_optimize/status", methods=["GET", "HEAD"])
    async def self_optimize_status(request: Request) -> Response:
        return await uncached_status(request, services.get_self_optimize_status)

    @router.api_route("/api/daily_evolution", methods=["GET", "HEAD"])
    async def daily_evolution(request: Request) -> Response:
        limited = await enforce_api_limits(request)
        if limited is not None:
            return limited
        if request.method == "HEAD":
            return Response(
                status_code=200,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )

        def read_report() -> bytes:
            report_file = services.CRON_OUTPUT_DIR / "daily_evolution_report.json"
            if report_file.exists():
                return report_file.read_bytes()
            return json.dumps(
                {"error": "尚无进化报告，等待首次盘后运行"},
                ensure_ascii=False,
            ).encode("utf-8")

        payload = await run_in_threadpool(read_report)
        if services.EDGE_CACHE_ENABLED:
            cache_control = "public, max-age=5, s-maxage=10, stale-while-revalidate=20"
            cdn_cache_control = "public, max-age=10, stale-while-revalidate=20"
        else:
            cache_control = "private, max-age=5, stale-while-revalidate=10"
            cdn_cache_control = "no-store"
        return Response(
            content=payload,
            status_code=200,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Cache-Control": cache_control,
                "CDN-Cache-Control": cdn_cache_control,
            },
        )

    async def refresh_practice_candidates(request: Request) -> Response:
        anonymous_checked = False
        if request.url.path == "/api/b1_screen":
            limited = rate_limit(request)
            if limited is not None:
                return limited
            anonymous_checked = True
            force = str(request.query_params.get("force") or "0").strip().lower()
            if force not in {"1", "true", "yes"}:
                return Response(status_code=404, headers={"Cache-Control": "no-store"})
        rejected = await require_admin_action(
            request,
            anonymous_limit_checked=anonymous_checked,
        )
        if rejected is not None:
            return rejected

        def refresh() -> dict[str, Any]:
            payload = services.trigger_b1_scan(force=True)
            services.invalidate_api_cache(services.PRACTICE_CANDIDATES_CACHE_KEY)
            return payload

        payload = await run_in_threadpool(refresh)
        return json_response(request, payload, cache_control="no-store")

    for refresh_path in sorted(services.PRACTICE_CANDIDATES_REFRESH_API_PATHS):
        router.add_api_route(refresh_path, refresh_practice_candidates, methods=["POST"])
    router.add_api_route("/api/b1_screen", refresh_practice_candidates, methods=["POST"])

    @router.post("/api/niuniu_practice/manual-cycle")
    async def start_practice_manual_cycle(request: Request) -> Response:
        rejected = await require_admin_action(request)
        if rejected is not None:
            return rejected
        payload = await run_in_threadpool(services.start_practice_manual_cycle)
        return json_response(request, payload, cache_control="no-store")

    @router.post("/api/niuniu_practice/market-summary")
    async def generate_practice_market_summary(request: Request) -> Response:
        rejected = await require_admin_action(request)
        if rejected is not None:
            return rejected
        payload = await run_in_threadpool(services.generate_practice_market_summary)
        if not payload.get("ok"):
            return JSONResponse(
                {"error": str(payload.get("error") or "盘面总结生成失败")},
                status_code=409,
                headers={"Cache-Control": "no-store"},
            )
        return json_response(request, payload, cache_control="no-store")

    @router.post("/api/niuniu_practice/resume")
    async def resume_practice_trading(request: Request) -> Response:
        rejected = await require_admin_action(request)
        if rejected is not None:
            return rejected

        def resume() -> dict[str, Any]:
            payload = services.get_trader_module().resume_trading()
            services.invalidate_api_cache(
                "niuniu_practice",
                services.PRACTICE_FAST_CACHE_KEY,
            )
            return payload

        payload = await run_in_threadpool(resume)
        return json_response(request, payload, cache_control="no-store")

    @router.post("/api/self_optimize/apply")
    async def apply_self_optimization(request: Request) -> Response:
        rejected = await require_admin_action(request)
        if rejected is not None:
            return rejected
        payload = await run_in_threadpool(services.apply_self_optimization)
        return json_response(request, payload, cache_control="no-store")

    @router.api_route("/api/practice_benchmarks", methods=["GET", "HEAD"])
    async def practice_benchmarks(request: Request) -> Response:
        ttl = services.API_TTLS["practice_benchmarks"]
        return await cached_response(
            request,
            cache_key="practice_benchmarks",
            ttl=ttl,
            producer=services.get_practice_benchmarks,
            edge_ttl=ttl,
            browser_ttl=10,
        )

    return router
