"""Administrator authentication and configuration FastAPI routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool


JsonResponder = Callable[..., Response]
RateLimiter = Callable[..., Response | None]
ClientResolver = Callable[[Request], str]
SecureResolver = Callable[[Request], bool]


async def read_request_body(request: Request, limit: int) -> bytes | None:
    """Read a request body without allowing it to exceed the configured limit."""

    try:
        declared_size = int(request.headers.get("Content-Length", "0") or "0")
    except ValueError:
        declared_size = 0
    if declared_size > limit:
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def parse_admin_form(body: bytes, services: Any) -> dict[str, str]:
    """Parse an admin form with the existing multi-value normalization rules."""

    parsed = parse_qs(body.decode("utf-8", "ignore"), keep_blank_values=True)
    result: dict[str, str] = {}
    multi_value_kinds = {
        "time_list",
        "handle_list",
        "stock_universe",
        "strategy_multi",
        "strategy_single",
    }
    for key, values in parsed.items():
        env_name = key[len("env__") :] if key.startswith("env__") else ""
        schema = services.ENV_CONFIG_BY_NAME.get(env_name, {})
        if schema.get("kind") in multi_value_kinds:
            result[key] = ",".join(value.strip() for value in values if value.strip())
        else:
            result[key] = values[-1] if values else ""
    return result


class AdminAccess:
    """Shared authentication policy for admin and protected practice actions."""

    def __init__(
        self,
        *,
        services: Any,
        rate_limit: RateLimiter,
    ) -> None:
        self.services = services
        self.rate_limit = rate_limit

    async def session_valid(self, request: Request) -> bool:
        cookies = self.services.parse_request_cookies(request.headers.get("Cookie"))
        cookie_value = str(
            cookies.get(self.services.ADMIN_SESSION_COOKIE_NAME, "") or ""
        )
        return await run_in_threadpool(
            self.services.validate_admin_session,
            cookie_value,
        )

    async def require_action(
        self,
        request: Request,
        *,
        anonymous_limit_checked: bool = False,
    ) -> Response | None:
        if not anonymous_limit_checked:
            limited = self.rate_limit(request)
            if limited is not None:
                return limited
        if not await self.session_valid(request):
            return JSONResponse(
                {"error": "admin_password_required"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        action_value = str(
            request.headers.get(self.services.ACTION_HEADER_NAME) or ""
        ).strip().lower()
        if action_value not in self.services.ACTION_HEADER_VALUES:
            return JSONResponse(
                {"error": "action_header_required"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        return self.rate_limit(
            request,
            scope="admin",
            limit=self.services.RATE_LIMIT_ADMIN,
        )

    async def read_form(
        self,
        request: Request,
    ) -> tuple[dict[str, str] | None, Response | None]:
        body = await read_request_body(request, self.services.MAX_POST_BODY_BYTES)
        if body is None:
            return None, JSONResponse(
                {"error": "request_too_large"},
                status_code=413,
                headers={"Cache-Control": "no-store"},
            )
        return parse_admin_form(body, self.services), None


def create_admin_router(
    *,
    services: Any,
    access: AdminAccess,
    rate_limit: RateLimiter,
    client_ip: ClientResolver,
    request_is_secure: SecureResolver,
    json_response: JsonResponder,
) -> APIRouter:
    """Create admin session, connection-test, and configuration routes."""

    router = APIRouter(include_in_schema=False)

    @router.api_route("/api/admin/config", methods=["GET", "HEAD"])
    async def admin_config(request: Request) -> Response:
        limited = rate_limit(request)
        if limited is not None:
            return limited
        if not await access.session_valid(request):
            return JSONResponse(
                {"error": "admin_password_required"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        if request.method == "HEAD":
            return Response(
                status_code=200,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        payload = await run_in_threadpool(services.build_admin_config_payload)
        return json_response(request, payload, cache_control="no-store")

    @router.post("/api/admin/session")
    async def create_admin_session(request: Request) -> Response:
        limited = rate_limit(request)
        if limited is not None:
            return limited
        peer_ip = request.client.host if request.client else ""
        limited = rate_limit(
            request,
            scope="admin-login-peer",
            limit=services.RATE_LIMIT_ADMIN_LOGIN,
            key=peer_ip,
        )
        if limited is not None:
            return limited
        resolved_client_ip = client_ip(request)
        if resolved_client_ip != peer_ip:
            limited = rate_limit(
                request,
                scope="admin-login-client",
                limit=services.RATE_LIMIT_ADMIN_LOGIN,
                key=resolved_client_ip,
            )
            if limited is not None:
                return limited

        body = await read_request_body(request, services.MAX_POST_BODY_BYTES)
        if body is None:
            return JSONResponse(
                {"error": "请求过大，请重新提交"},
                status_code=413,
                headers={"Cache-Control": "no-store"},
            )
        form = parse_qs(body.decode("utf-8", "ignore"), keep_blank_values=True)
        password_values = form.get("admin_password") or [""]
        supplied_password = str(password_values[-1] or "")
        authenticated = await run_in_threadpool(
            services.verify_admin_credential,
            supplied_password,
        )
        payload: dict[str, Any] = {"ok": authenticated}
        if not authenticated:
            payload["error"] = "管理员凭据错误"
        response = json_response(
            request,
            payload,
            cache_control="no-store",
            status_code=200 if authenticated else 403,
        )
        if authenticated:
            session_value = await run_in_threadpool(services.new_admin_session)
            max_age = max(60, services.ADMIN_SESSION_TTL_SECONDS)
            secure = "; Secure" if request_is_secure(request) else ""
            response.headers["Set-Cookie"] = (
                f"{services.ADMIN_SESSION_COOKIE_NAME}={session_value}; "
                f"Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax{secure}"
            )
        return response

    @router.post("/api/admin/iwencai/test")
    async def admin_iwencai_test(request: Request) -> Response:
        rejected = await access.require_action(request)
        if rejected is not None:
            return rejected
        limited = rate_limit(
            request,
            scope="iwencai-test",
            limit=services.RATE_LIMIT_IWENCAI_TEST,
        )
        if limited is not None:
            return limited
        form, invalid = await access.read_form(request)
        if invalid is not None:
            return invalid
        overrides = {
            key[len("env__") :]: value
            for key, value in (form or {}).items()
            if key.startswith("env__")
            and key[len("env__") :] in services.IWENCAI_TEST_FIELD_NAMES
        }
        payload = await run_in_threadpool(services.send_iwencai_connection_test, overrides)
        return json_response(request, payload, cache_control="no-store")

    @router.post("/api/admin/models/test")
    async def admin_model_test(request: Request) -> Response:
        rejected = await access.require_action(request)
        if rejected is not None:
            return rejected
        limited = rate_limit(
            request,
            scope="model-test",
            limit=services.RATE_LIMIT_MODEL_TEST,
        )
        if limited is not None:
            return limited
        form, invalid = await access.read_form(request)
        if invalid is not None:
            return invalid
        target_id = str((form or {}).get("target") or "").strip()
        allowed_names = services.model_test_override_names(target_id)
        overrides = {
            key[len("env__") :]: value
            for key, value in (form or {}).items()
            if key.startswith("env__") and key[len("env__") :] in allowed_names
        }
        payload = await run_in_threadpool(
            services.send_model_connection_test,
            target_id,
            overrides,
        )
        return json_response(request, payload, cache_control="no-store")

    @router.post("/api/admin/notifications/test")
    async def admin_notification_test(request: Request) -> Response:
        rejected = await access.require_action(request)
        if rejected is not None:
            return rejected
        limited = rate_limit(
            request,
            scope="notification-test",
            limit=services.RATE_LIMIT_NOTIFICATION_TEST,
        )
        if limited is not None:
            return limited
        form, invalid = await access.read_form(request)
        if invalid is not None:
            return invalid
        channel_id = str((form or {}).get("channel") or "").strip().lower()
        channel = services.NOTIFICATION_CHANNEL_BY_ID.get(channel_id)
        allowed_names = {"DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS"}
        if channel is not None:
            allowed_names.update(str(name) for name in channel.get("field_names", ()))
        overrides = {
            key[len("env__") :]: value
            for key, value in (form or {}).items()
            if key.startswith("env__") and key[len("env__") :] in allowed_names
        }
        payload = await run_in_threadpool(
            services.send_notification_test,
            channel_id,
            overrides,
        )
        return json_response(request, payload, cache_control="no-store")

    @router.post("/api/admin/config/env")
    @router.post("/api/admin/config/env/{group_slug}")
    async def save_admin_config(request: Request, group_slug: str = "") -> Response:
        rejected = await access.require_action(request)
        if rejected is not None:
            return rejected
        group = services.ADMIN_SETTING_GROUP_BY_SLUG.get(group_slug) if group_slug else None
        if group_slug and group is None:
            return JSONResponse(
                {"error": "unknown_settings_group"},
                status_code=404,
                headers={"Cache-Control": "no-store"},
            )
        form, invalid = await access.read_form(request)
        if invalid is not None:
            return invalid

        def persist() -> dict[str, Any]:
            visible_names = (
                services.admin_setting_group_env_names(group_slug)
                if group_slug
                else set(services.admin_visible_env_names())
            )
            updates = {
                key[len("env__") :]: value
                for key, value in (form or {}).items()
                if key.startswith("env__") and key[len("env__") :] in visible_names
            }
            removed_channels = (
                {
                    key[len("notification_remove__") :]
                    for key, value in (form or {}).items()
                    if key.startswith("notification_remove__")
                    and str(value or "").strip().lower() in services.TRUTHY_VALUES
                }
                if not group_slug or group_slug == "notifications"
                else set()
            )
            normalized = services.normalize_business_updates(updates)
            services.validate_business_updates(normalized)
            result = services.persist_and_sync_business_updates(
                normalized,
                clear_names=services.removed_notification_config_names(removed_channels),
            )
            result["reauth_required"] = "DASHBOARD_ADMIN_PASSWORD" in set(
                result.get("changed_names") or []
            )
            result["restart"] = {
                "ok": False,
                "skipped": "hot_applied" if result.get("changed") else "unchanged",
            }
            if group is not None:
                result["group"] = {"slug": group_slug, "name": str(group["name"])}
            result["config"] = services.build_admin_config_payload()
            return result

        try:
            payload = await run_in_threadpool(persist)
        except Exception as exc:
            payload = {"ok": False, "error": str(exc)}
        return json_response(request, payload, cache_control="no-store")

    @router.post("/api/admin/config/yaml")
    async def save_admin_yaml(request: Request) -> Response:
        rejected = await access.require_action(request)
        if rejected is not None:
            return rejected
        form, invalid = await access.read_form(request)
        if invalid is not None:
            return invalid
        try:
            payload = await run_in_threadpool(
                services.write_yaml_config,
                str((form or {}).get("config_yaml") or ""),
            )
        except Exception as exc:
            payload = {"ok": False, "error": str(exc)}
        return json_response(request, payload, cache_control="no-store")

    return router
