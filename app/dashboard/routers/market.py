"""Market-data FastAPI routes used by the Vue dashboard."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool


CachedResponder = Callable[..., Awaitable[Response]]
LimitEnforcer = Callable[[Request], Awaitable[Response | None]]


def compact_industry_flow_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Project the animation response to fields consumed by the Vue client."""

    def compact_node(node: Any) -> dict[str, Any]:
        source = node if isinstance(node, dict) else {}
        return {
            "id": source.get("id"),
            "name": source.get("name"),
            "net_flow_yi": source.get("net_flow_yi"),
        }

    sampling = payload.get("sampling") if isinstance(payload.get("sampling"), dict) else {}
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    result: dict[str, Any] = {
        "available": payload.get("available", False),
        "generated_at": payload.get("generated_at"),
        "source": payload.get("source"),
        "nodes": [compact_node(node) for node in payload.get("nodes") or []],
        "timeline": [
            {
                "generated_at": frame.get("generated_at"),
                "nodes": [compact_node(node) for node in frame.get("nodes") or []],
            }
            for frame in payload.get("timeline") or []
            if isinstance(frame, dict)
        ],
        "settings": {
            "side_limit": settings.get("side_limit"),
            "playback_speed": settings.get("playback_speed"),
        },
        "sampling": {
            "interval_seconds": sampling.get("interval_seconds"),
            "windows": sampling.get("windows") or [],
        },
        "money_flow": payload.get("money_flow") or {},
    }
    for key in ("stale_cache", "error"):
        if key in payload:
            result[key] = payload[key]
    return result


def create_market_router(
    *,
    services: Any,
    cached_response: CachedResponder,
    enforce_api_limits: LimitEnforcer,
) -> APIRouter:
    """Create native read routes for Chinese and US market data."""

    router = APIRouter(include_in_schema=False)

    def prepare_daily_money_flow_cache(ttl: int) -> None:
        services.reset_daily_market_histories()
        services.seed_api_cache_from_json_file(
            "money_flow",
            services.MONEY_FLOW_SNAPSHOT_FILE,
            ttl,
        )

    @router.api_route("/api/iwencai/dragon-tiger", methods=["GET", "HEAD"])
    async def iwencai_dragon_tiger(request: Request) -> Response:
        limited = await enforce_api_limits(request)
        if limited is not None:
            return limited
        if request.method == "HEAD":
            return Response(
                status_code=200,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        raw_trade_date = str(request.query_params.get("date") or "").strip()
        try:
            trade_date = services.normalize_iwencai_trade_date(raw_trade_date)
            page = services.normalize_iwencai_page(request.query_params.get("page") or "1")
            limit = services.normalize_iwencai_limit(
                request.query_params.get("limit")
                or str(services.IWENCAI_DRAGON_TIGER_DEFAULT_LIMIT)
            )
        except ValueError:
            return JSONResponse(
                {"error": "invalid_iwencai_dragon_tiger_request"},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        allow_latest_snapshot = not raw_trade_date
        snapshot_version = (
            services.iwencai_dragon_tiger_snapshot_version(
                trade_date,
                include_latest=allow_latest_snapshot,
            )
            if page == 1 and limit == services.IWENCAI_DRAGON_TIGER_DEFAULT_LIMIT
            else 0
        )
        ttl = services.API_TTLS["iwencai_dragon_tiger"]
        return await cached_response(
            request,
            cache_key=(
                f"iwencai_dragon_tiger:{trade_date}:{page}:{limit}:"
                f"{int(allow_latest_snapshot)}:{snapshot_version}"
            ),
            ttl=ttl,
            producer=lambda: services.produce_iwencai_dragon_tiger_data(
                trade_date,
                page=page,
                limit=limit,
                allow_latest_snapshot=allow_latest_snapshot,
            ),
            edge_ttl=ttl,
            browser_ttl=min(30, ttl),
            enforce_limits=False,
        )

    @router.api_route("/api/x_media", methods=["GET", "HEAD"])
    async def x_media(request: Request) -> Response:
        limited = await enforce_api_limits(request)
        if limited is not None:
            return limited
        if request.method == "HEAD":
            return Response(status_code=200, headers={"Cache-Control": "no-store"})
        media_url = str(request.query_params.get("url") or "").strip()
        try:
            body, content_type = await run_in_threadpool(services.fetch_x_media, media_url)
        except Exception:
            return Response(
                content=b"media unavailable",
                status_code=404,
                media_type="text/plain",
                headers={"Cache-Control": "no-store"},
            )
        return Response(
            content=body,
            status_code=200,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=604800, immutable"},
        )

    @router.api_route("/api/indices", methods=["GET", "HEAD"])
    async def indices(request: Request) -> Response:
        ttl = services.API_TTLS["indices"]
        return await cached_response(
            request,
            cache_key="indices",
            ttl=ttl,
            producer=services.produce_indices_data,
            edge_ttl=ttl,
            browser_ttl=15,
            before_cache=lambda: services.seed_api_cache_from_json_file(
                "indices",
                services.INDICES_SNAPSHOT_FILE,
                ttl,
            ),
        )

    @router.api_route("/api/market_breadth", methods=["GET", "HEAD"])
    async def market_breadth(request: Request) -> Response:
        ttl = services.API_TTLS["market_breadth"]
        return await cached_response(
            request,
            cache_key="market_breadth",
            ttl=ttl,
            producer=services.produce_market_breadth_data,
            edge_ttl=ttl,
            browser_ttl=15,
            before_cache=services.reset_daily_market_histories,
        )

    @router.api_route("/api/sectors", methods=["GET", "HEAD"])
    async def sectors(request: Request) -> Response:
        ttl = services.API_TTLS["sectors"]
        fallback = {
            "sectors": [],
            "items": [],
            "gain_top": [],
            "loss_top": [],
            "industry_gain_top": [],
            "industry_loss_top": [],
            "concept_gain_top": [],
            "concept_loss_top": [],
        }
        return await cached_response(
            request,
            cache_key="sectors",
            ttl=ttl,
            producer=lambda: services.run_dashboard_helper(
                "sectors_dashboard_api.py",
                fallback,
                timeout=120,
            ),
            edge_ttl=ttl,
            browser_ttl=15,
            before_cache=lambda: services.seed_api_cache_from_json_file(
                "sectors",
                services.CRON_OUTPUT_DIR / "sectors_dashboard_cache.json",
                ttl,
            ),
        )

    @router.api_route("/api/hot_stocks", methods=["GET", "HEAD"])
    async def hot_stocks(request: Request) -> Response:
        sort_by = str(request.query_params.get("sort_by") or "amount").strip().lower()
        if sort_by not in {
            "amount",
            "amount_top",
            "turnover",
            "turnover_top",
            "volume",
            "volume_top",
            "gain",
            "hot",
        }:
            sort_by = "amount"
        ttl = services.API_TTLS["hot_stocks"]
        cache_key = f"hot_stocks:{sort_by}"

        def transform(payload: dict[str, Any]) -> dict[str, Any]:
            return services.apply_hot_stocks_sort(payload, sort_by)

        def produce() -> dict[str, Any]:
            payload = services.run_dashboard_helper(
                "hot_stocks_dashboard_api.py",
                {
                    "items": [],
                    "amount_top": [],
                    "turnover_top": [],
                    "volume_top": [],
                    "gain_top": [],
                },
                timeout=120,
            )
            return transform(payload)

        return await cached_response(
            request,
            cache_key=cache_key,
            ttl=ttl,
            producer=produce,
            edge_ttl=ttl,
            browser_ttl=15,
            before_cache=lambda: services.seed_api_cache_from_json_file(
                cache_key,
                services.CRON_OUTPUT_DIR / "hot_stocks_dashboard_cache.json",
                ttl,
                transform,
            ),
        )

    @router.api_route("/api/us_quotes", methods=["GET", "HEAD"])
    async def us_quotes(request: Request) -> Response:
        symbols = services.sanitize_symbols(str(request.query_params.get("symbols") or ""))
        ttl = services.API_TTLS["us_quotes"]
        return await cached_response(
            request,
            cache_key="us_quotes:" + ",".join(symbols),
            ttl=ttl,
            producer=lambda: services.fetch_us_quotes(symbols),
            edge_ttl=ttl,
            browser_ttl=10,
        )

    @router.api_route("/api/us_profiles", methods=["GET", "HEAD"])
    async def us_profiles(request: Request) -> Response:
        symbols = services.sanitize_symbols(str(request.query_params.get("symbols") or ""))
        ttl = services.API_TTLS["us_profiles"]
        return await cached_response(
            request,
            cache_key="us_profiles:" + ",".join(symbols),
            ttl=ttl,
            producer=lambda: services.fetch_us_profiles(symbols),
            edge_ttl=ttl,
            browser_ttl=3600,
        )

    @router.api_route("/api/us_market_summary", methods=["GET", "HEAD"])
    async def us_market_summary(request: Request) -> Response:
        ttl = services.API_TTLS["us_market_summary"]
        return await cached_response(
            request,
            cache_key="us_market_summary",
            ttl=ttl,
            producer=services.produce_us_market_summary_data,
            edge_ttl=ttl,
            browser_ttl=30,
        )

    @router.api_route("/api/us_sectors", methods=["GET", "HEAD"])
    async def us_sectors(request: Request) -> Response:
        ttl = services.API_TTLS["us_sectors"]
        return await cached_response(
            request,
            cache_key="us_sectors",
            ttl=ttl,
            producer=services.produce_us_sector_data,
            edge_ttl=ttl,
            browser_ttl=30,
        )

    @router.api_route("/api/money_flow", methods=["GET", "HEAD"])
    async def money_flow(request: Request) -> Response:
        ttl = services.API_TTLS["money_flow"]
        return await cached_response(
            request,
            cache_key="money_flow",
            ttl=ttl,
            producer=services.produce_money_flow_data,
            edge_ttl=ttl,
            browser_ttl=15,
            before_cache=lambda: prepare_daily_money_flow_cache(ttl),
        )

    @router.api_route("/api/industry-flow", methods=["GET", "HEAD"])
    async def industry_flow(request: Request) -> Response:
        money_flow_ttl = services.API_TTLS["money_flow"]
        ttl = services.API_TTLS["industry_flow"]
        compact = str(request.query_params.get("compact") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        return await cached_response(
            request,
            cache_key="industry_flow:compact:v1" if compact else "industry_flow",
            ttl=ttl,
            producer=(
                lambda: compact_industry_flow_payload(services.produce_industry_flow_data())
            ) if compact else services.produce_industry_flow_data,
            edge_ttl=ttl,
            browser_ttl=10,
            before_cache=lambda: prepare_daily_money_flow_cache(money_flow_ttl),
        )

    @router.api_route("/api/market_flow", methods=["GET", "HEAD"])
    async def market_flow(request: Request) -> Response:
        ttl = services.API_TTLS["market_flow"]
        return await cached_response(
            request,
            cache_key="market_flow",
            ttl=ttl,
            producer=lambda: services.run_dashboard_helper(
                "market_flow_dashboard_api.py",
                {"total_inflow_yi": None},
                timeout=30,
            ),
            edge_ttl=ttl,
            browser_ttl=10,
        )

    return router
