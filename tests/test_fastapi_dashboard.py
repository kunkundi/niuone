#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.dashboard.fastapi_app import _legacy_module, create_app
from app.dashboard.routers.market import compact_industry_flow_payload
from app.dashboard.routers.messages import message_page_payload, messages_revision_payload

ROOT = Path(__file__).resolve().parents[1]


class FastApiDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="niuone-fastapi-")
        self.root = Path(self.temp.name)
        self.dist = self.root / "dist"
        (self.dist / "assets").mkdir(parents=True)
        (self.dist / "index.html").write_text(
            '<!doctype html><html><body><div id="app"></div>'
            '<script type="module" src="/assets/app.js"></script></body></html>',
            encoding="utf-8",
        )
        (self.dist / "assets" / "app.js").write_text(
            'document.documentElement.dataset.runtime="vue";',
            encoding="utf-8",
        )
        self.legacy = _legacy_module(None)
        self.original_public_data_dir = self.legacy.PUBLIC_DATA_DIR
        self.original_publisher = self.legacy.PUBLIC_SNAPSHOT_PUBLISHER
        self.original_stats_db = self.legacy.STATS_DB
        self.original_legacy_stats_db = self.legacy.LEGACY_STATS_DB
        self.original_stats_signature = self.legacy.VISIT_STATS_INIT_SIGNATURE
        self.original_cron_output_dir = self.legacy.CRON_OUTPUT_DIR
        self.original_market_breadth_history_file = self.legacy.MARKET_BREADTH_HISTORY_FILE
        self.original_industry_flow_history_file = self.legacy.INDUSTRY_FLOW_HISTORY_FILE
        self.original_money_flow_snapshot_file = self.legacy.MONEY_FLOW_SNAPSHOT_FILE
        self.legacy.PUBLIC_DATA_DIR = self.root / "public-data"
        self.legacy.PUBLIC_SNAPSHOT_PUBLISHER = None
        self.legacy.STATS_DB = self.root / "dashboard-stats.db"
        self.legacy.LEGACY_STATS_DB = self.root / "legacy-dashboard-stats.db"
        self.legacy.VISIT_STATS_INIT_SIGNATURE = None
        self.legacy.CRON_OUTPUT_DIR = self.root / "cron-output"
        self.legacy.CRON_OUTPUT_DIR.mkdir()
        self.legacy.MARKET_BREADTH_HISTORY_FILE = self.legacy.CRON_OUTPUT_DIR / "market_breadth_history.json"
        self.legacy.INDUSTRY_FLOW_HISTORY_FILE = self.legacy.CRON_OUTPUT_DIR / "industry_main_flow_history.json"
        self.legacy.MONEY_FLOW_SNAPSHOT_FILE = self.legacy.CRON_OUTPUT_DIR / "industry_main_money_flow_cache.json"
        self.legacy.RATE_LIMIT_BUCKETS.clear()
        self.legacy.public_snapshot_publisher().publish(
            {"account": {"cash": 100}},
            generated_at="now",
        )
        self.app = create_app(
            legacy_module=self.legacy,
            web_dist_dir=self.dist,
            enable_background_services=False,
        )
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.legacy.PUBLIC_DATA_DIR = self.original_public_data_dir
        self.legacy.PUBLIC_SNAPSHOT_PUBLISHER = self.original_publisher
        self.legacy.STATS_DB = self.original_stats_db
        self.legacy.LEGACY_STATS_DB = self.original_legacy_stats_db
        self.legacy.VISIT_STATS_INIT_SIGNATURE = self.original_stats_signature
        self.legacy.CRON_OUTPUT_DIR = self.original_cron_output_dir
        self.legacy.MARKET_BREADTH_HISTORY_FILE = self.original_market_breadth_history_file
        self.legacy.INDUSTRY_FLOW_HISTORY_FILE = self.original_industry_flow_history_file
        self.legacy.MONEY_FLOW_SNAPSHOT_FILE = self.original_money_flow_snapshot_file
        self.legacy.RATE_LIMIT_BUCKETS.clear()
        self.temp.cleanup()

    def test_vue_dashboard_and_admin_share_the_fastapi_port(self):
        for path in ("/", "/practice", "/admin", "/admin/settings/notifications"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["X-NiuOne-Frontend"], "vue3-vite")
                self.assertIn('id="app"', response.text)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(response.headers["X-Frame-Options"], "DENY")

        missing = self.client.get("/admin/settings/not-a-group")
        self.assertEqual(missing.status_code, 404)

        asset = self.client.get("/assets/app.js")
        self.assertEqual(asset.status_code, 200)
        self.assertIn("immutable", asset.headers["Cache-Control"])

    def test_native_snapshot_routes_support_etag_and_health_metadata(self):
        health = self.client.get("/healthz")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["plane"], "fastapi")
        self.assertEqual(health.json()["frontend"], "vue3-vite")

        latest = self.client.get("/api/v2/public/latest")
        self.assertEqual(latest.status_code, 200)
        self.assertTrue(latest.headers["ETag"])
        self.assertIn("s-maxage=5", latest.headers["Cache-Control"])

        unchanged = self.client.get(
            "/api/v2/public/latest",
            headers={"If-None-Match": latest.headers["ETag"]},
        )
        self.assertEqual(unchanged.status_code, 304)
        self.assertEqual(unchanged.content, b"")

    def test_version_route_is_native_and_does_not_check_upstream_for_head(self):
        payload = {
            "current_version": "1.2.3",
            "latest_version": "1.2.4",
            "update_available": True,
            "check_ok": True,
        }
        with (
            patch.object(self.legacy, "get_version_status", return_value=payload) as version,
        ):
            response = self.client.get("/api/version")
            head = self.client.head("/api/version")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        version.assert_called_once_with()

    def test_dashboard_bootstrap_is_native_and_reuses_the_visitor_cookie(self):
        message_payload = {
            "categories": {
                "market_monitor": {"label": "盘面监控", "count": 6},
                "x_monitor": {"label": "推特监控", "count": 108},
                "us_ratings": {"label": "美股机构买入评级", "count": 4},
                "other": {"label": "其他", "count": 3},
            },
        }
        with patch.object(
            self.legacy,
            "merge_records_from_db",
            return_value=message_payload,
        ) as merge_records:
            first = self.client.get("/api/dashboard/bootstrap")
            second = self.client.get("/api/dashboard/bootstrap")
            head = self.client.head("/api/dashboard/bootstrap")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["visits"], 1)
        self.assertEqual(first.json()["unique"], 1)
        self.assertEqual(
            first.json()["message_counts"],
            {"market_monitor": 6, "x_monitor": 108, "us_ratings": 4},
        )
        self.assertIs(first.json()["message_counts_available"], True)
        self.assertIn(f"{self.legacy.VISITOR_COOKIE_NAME}=nvst_", first.headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", first.headers["Set-Cookie"])
        self.assertNotIn("Set-Cookie", second.headers)
        self.assertEqual(second.json()["visits"], 2)
        self.assertEqual(second.json()["unique"], 1)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(merge_records.call_count, 2)
        merge_records.assert_called_with(limit=0)

    def test_dashboard_bootstrap_degrades_when_message_counts_are_unavailable(self):
        with (
            patch.object(
                self.legacy,
                "merge_records_from_db",
                side_effect=RuntimeError("message store unavailable"),
            ),
            patch.object(self.legacy, "us_features_enabled", return_value=True),
        ):
            response = self.client.get("/api/dashboard/bootstrap")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["visits"], 1)
        self.assertEqual(response.json()["unique"], 1)
        self.assertIs(response.json()["us_features_enabled"], True)
        self.assertEqual(response.json()["message_counts"], {})
        self.assertIs(response.json()["message_counts_available"], False)

    def test_cached_read_routes_are_native_and_keep_cache_metadata(self):
        seen_keys = []

        def cached_payload(cache_key, ttl, producer):
            del ttl, producer
            seen_keys.append(cache_key)
            return json.dumps({"route": cache_key}).encode("utf-8"), True

        with (
            patch.object(self.legacy, "cache_get_json", side_effect=cached_payload),
            patch.object(self.legacy, "seed_api_cache_from_json_file", return_value=False),
            patch.object(self.legacy, "reset_daily_market_histories", return_value=False) as reset_daily,
        ):
            for path, cache_key in (
                ("/api/messages?limit=25&offset=50&category=x_monitor", "messages:v4:x_monitor:25:50"),
                (
                    "/api/messages/revision?category=market_monitor",
                    "messages-revision:v1:market_monitor",
                ),
                (
                    "/api/messages/revision?category=x_monitor&limit=10&offset=20",
                    "messages-revision:v2:x_monitor:10:20",
                ),
                (
                    "/api/iwencai/dragon-tiger?date=2026-07-16&page=2&limit=10",
                    "iwencai_dragon_tiger:2026-07-16:2:10:0:0",
                ),
                ("/api/practice_candidates", "practice_candidates"),
                ("/api/b1_screen", "practice_candidates"),
                ("/api/niuniu_practice?fast=1", "niuniu_practice_fast:v2"),
                ("/api/niuniu_practice", "niuniu_practice"),
                ("/api/practice_benchmarks", "practice_benchmarks"),
                ("/api/indices", "indices"),
                ("/api/market_breadth", "market_breadth"),
                ("/api/sectors", "sectors"),
                ("/api/hot_stocks?sort_by=turnover", "hot_stocks:turnover"),
                ("/api/hot_stocks?sort_by=not-valid", "hot_stocks:amount"),
                ("/api/us_quotes?symbols=AAPL,msft,bad%24", "us_quotes:AAPL,MSFT"),
                ("/api/us_profiles?symbols=NVDA,amd", "us_profiles:NVDA,AMD"),
                ("/api/us_market_summary", "us_market_summary"),
                ("/api/us_sectors", "us_sectors"),
                ("/api/money_flow", "money_flow"),
                ("/api/industry-flow", "industry_flow"),
                ("/api/industry-flow?compact=1", "industry_flow:compact:v1"),
                ("/api/market_flow", "market_flow"),
            ):
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {"route": cache_key})
                    self.assertEqual(response.headers["X-Dashboard-Cache"], "HIT")
                    self.assertIn("max-age=", response.headers["Cache-Control"])
                    self.assertIn("CDN-Cache-Control", response.headers)

            head = self.client.head("/api/indices")
            invalid_dragon_tiger = self.client.get(
                "/api/iwencai/dragon-tiger?page=1&limit=101"
            )
            forced_candidates = self.client.get("/api/practice_candidates?force=1")

        self.assertEqual(reset_daily.call_count, 4)
        self.assertEqual(seen_keys, [
            "messages:v4:x_monitor:25:50",
            "messages-revision:v1:market_monitor",
            "messages-revision:v2:x_monitor:10:20",
            "iwencai_dragon_tiger:2026-07-16:2:10:0:0",
            "practice_candidates",
            "practice_candidates",
            "niuniu_practice_fast:v2",
            "niuniu_practice",
            "practice_benchmarks",
            "indices",
            "market_breadth",
            "sectors",
            "hot_stocks:turnover",
            "hot_stocks:amount",
            "us_quotes:AAPL,MSFT",
            "us_profiles:NVDA,AMD",
            "us_market_summary",
            "us_sectors",
            "money_flow",
            "industry_flow",
            "industry_flow:compact:v1",
            "market_flow",
        ])
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(invalid_dragon_tiger.status_code, 400)
        self.assertEqual(
            invalid_dragon_tiger.json()["error"],
            "invalid_iwencai_dragon_tiger_request",
        )
        self.assertEqual(forced_candidates.status_code, 405)
        self.assertEqual(forced_candidates.headers["Allow"], "POST")

    def test_message_revision_projection_omits_full_history_content(self):
        revision = messages_revision_payload(
            {
                "categories": {"market_monitor": {"count": 121}},
                "records": [{
                    "id": "latest-id",
                    "timestamp": 1784619519.4,
                    "content_hash": "latest-hash",
                    "updated_at": "2026-07-21 15:38:40",
                    "content": "large report body that must not be returned",
                    "metadata": {"ignored": True},
                }],
            },
            "market_monitor",
        )

        self.assertEqual(revision["category"], "market_monitor")
        self.assertEqual(revision["count"], 121)
        self.assertEqual(revision["latest"]["id"], "latest-id")
        self.assertEqual(revision["latest"]["content_hash"], "latest-hash")
        self.assertNotIn("content", revision["latest"])
        self.assertNotIn("records", revision)

        page_payload = {
            "categories": {"x_monitor": {"count": 21}},
            "records": [{
                "id": "page-id",
                "timestamp": 1784619500.0,
                "content_hash": "page-hash",
                "updated_at": "2026-07-21 15:30:00",
                "metadata": {"post": {"media": [{"url": "https://pbs.twimg.com/media/a.jpg"}]}},
                "content": "page body",
            }],
        }
        page_revision = messages_revision_payload(
            page_payload,
            "x_monitor",
            page_limit=10,
            page_offset=20,
        )
        changed_payload = json.loads(json.dumps(page_payload))
        changed_payload["records"][0]["metadata"]["post"]["media"][0]["url"] = (
            "https://pbs.twimg.com/media/b.jpg"
        )
        changed_revision = messages_revision_payload(
            changed_payload,
            "x_monitor",
            page_limit=10,
            page_offset=20,
        )

        self.assertEqual(page_revision["page"]["limit"], 10)
        self.assertEqual(page_revision["page"]["offset"], 20)
        self.assertEqual(page_revision["page"]["count"], 1)
        self.assertEqual(len(page_revision["page"]["fingerprint"]), 64)
        self.assertNotEqual(
            page_revision["page"]["fingerprint"],
            changed_revision["page"]["fingerprint"],
        )
        self.assertNotIn("metadata", page_revision["latest"])

        full_page = message_page_payload(
            page_payload,
            "x_monitor",
            limit=10,
            offset=20,
        )
        ordinary_page = message_page_payload(
            page_payload,
            "market_monitor",
            limit=10,
            offset=0,
        )
        self.assertEqual(full_page["revision"]["page"]["fingerprint"], page_revision["page"]["fingerprint"])
        self.assertIs(ordinary_page, page_payload)

        missing = self.client.get("/api/messages/revision")
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["error"], "message_category_required")

    def test_compact_industry_flow_projection_keeps_only_animation_fields(self):
        payload = compact_industry_flow_payload({
            "available": True,
            "generated_at": "2026-07-21 10:30:00",
            "source": "sample",
            "stale_cache": True,
            "nodes": [{
                "id": "semi",
                "name": "半导体",
                "net_flow_yi": 12.5,
                "leader": "000001",
                "volume_model": {"ignored": True},
            }],
            "timeline": [{
                "generated_at": "2026-07-21 09:30:00",
                "snapshot_id": "ignored",
                "nodes": [{
                    "id": "semi",
                    "name": "半导体",
                    "net_flow_yi": 2.5,
                    "inflow_yi": 100,
                }],
            }],
            "settings": {"side_limit": 10, "playback_speed": 0.5, "ignored": 1},
            "sampling": {
                "interval_seconds": 60,
                "windows": [{"start": "09:25", "end": "11:31"}],
                "volume_model": {"ignored": True},
            },
            "money_flow": {"inflow": [], "outflow": []},
            "inference": {"ignored": True},
        })

        self.assertEqual(
            payload["nodes"],
            [{"id": "semi", "name": "半导体", "net_flow_yi": 12.5}],
        )
        self.assertEqual(
            payload["timeline"][0]["nodes"],
            [{"id": "semi", "name": "半导体", "net_flow_yi": 2.5}],
        )
        self.assertEqual(
            payload["settings"],
            {"side_limit": 10, "playback_speed": 0.5},
        )
        self.assertNotIn("inference", payload)
        self.assertNotIn("volume_model", payload["sampling"])
        self.assertTrue(payload["stale_cache"])

    def test_native_media_and_practice_status_routes_bypass_the_adapter(self):
        (self.legacy.CRON_OUTPUT_DIR / "daily_evolution_report.json").write_text(
            json.dumps({"available": True}),
            encoding="utf-8",
        )
        with (
            patch.object(
                self.legacy,
                "fetch_x_media",
                return_value=(b"image-bytes", "image/png"),
            ) as media,
            patch.object(
                self.legacy,
                "practice_manual_cycle_status",
                return_value={"running": False},
            ) as manual_cycle,
            patch.object(
                self.legacy,
                "get_practice_market_summary_status",
                return_value={"available": True},
            ) as market_summary,
            patch.object(
                self.legacy,
                "get_self_optimize_status",
                return_value={"enabled": True},
            ) as self_optimize,
        ):
            media_response = self.client.get(
                "/api/x_media?url=https%3A%2F%2Fpbs.twimg.com%2Fmedia%2Fexample.jpg"
            )
            manual_response = self.client.get("/api/niuniu_practice/manual-cycle")
            summary_response = self.client.get("/api/niuniu_practice/market-summary")
            optimize_response = self.client.get("/api/self_optimize/status")
            evolution_response = self.client.get("/api/daily_evolution")

        self.assertEqual(media_response.status_code, 200)
        self.assertEqual(media_response.content, b"image-bytes")
        self.assertEqual(media_response.headers["Content-Type"], "image/png")
        self.assertIn("immutable", media_response.headers["Cache-Control"])
        media.assert_called_once_with("https://pbs.twimg.com/media/example.jpg")
        self.assertEqual(manual_response.json(), {"running": False})
        self.assertEqual(summary_response.json(), {"available": True})
        self.assertEqual(optimize_response.json(), {"enabled": True})
        self.assertEqual(evolution_response.json(), {"available": True})
        self.assertIn("max-age=5", evolution_response.headers["Cache-Control"])
        manual_cycle.assert_called_once_with()
        market_summary.assert_called_once_with()
        self_optimize.assert_called_once_with()

    def test_admin_config_remains_protected_and_session_method_contract_is_preserved(self):
        locked = self.client.get("/api/admin/config")
        self.assertEqual(locked.status_code, 403)
        self.assertEqual(locked.json()["error"], "admin_password_required")
        self.assertEqual(locked.headers["Cache-Control"], "no-store")

        wrong_method = self.client.head("/api/admin/session")
        self.assertEqual(wrong_method.status_code, 405)

    def test_native_admin_login_and_config_bypass_the_adapter(self):
        config_payload = {"items": [{"name": "DASHBOARD_PORT", "secret": False}]}
        with (
            patch.object(
                self.legacy,
                "verify_admin_credential",
                side_effect=[False, True],
            ) as verify,
            patch.object(self.legacy, "new_admin_session", return_value="ad_test_session"),
            patch.object(self.legacy, "validate_admin_session", return_value=True) as validate,
            patch.object(
                self.legacy,
                "build_admin_config_payload",
                return_value=config_payload,
            ) as build_config,
        ):
            rejected = self.client.post(
                "/api/admin/session",
                content="admin_password=wrong",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            accepted = self.client.post(
                "/api/admin/session",
                content="admin_password=correct",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            config = self.client.get("/api/admin/config")

        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.json()["error"], "管理员凭据错误")
        self.assertNotIn("Set-Cookie", rejected.headers)
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(accepted.json()["ok"])
        self.assertIn("dashboard_admin_session=ad_test_session", accepted.headers["Set-Cookie"])
        self.assertIn("HttpOnly", accepted.headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", accepted.headers["Set-Cookie"])
        self.assertEqual(config.status_code, 200)
        self.assertEqual(config.json(), config_payload)
        self.assertEqual(config.headers["Cache-Control"], "no-store")
        self.assertEqual([call.args[0] for call in verify.call_args_list], ["wrong", "correct"])
        validate.assert_called_once()
        build_config.assert_called_once_with()

    def test_fastapi_rejects_oversized_native_admin_login_body(self):
        response = self.client.post(
            "/api/admin/session",
            content=b"x" * (self.legacy.MAX_POST_BODY_BYTES + 1),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"], "请求过大，请重新提交")

    def test_native_admin_login_preserves_peer_rate_limit(self):
        original_limit = self.legacy.RATE_LIMIT_ADMIN_LOGIN
        self.legacy.RATE_LIMIT_ADMIN_LOGIN = 1
        self.legacy.RATE_LIMIT_BUCKETS.clear()
        try:
            with (
                patch.object(self.legacy, "verify_admin_credential", return_value=False),
            ):
                first = self.client.post(
                    "/api/admin/session",
                    content="admin_password=wrong",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                second = self.client.post(
                    "/api/admin/session",
                    content="admin_password=wrong",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        finally:
            self.legacy.RATE_LIMIT_ADMIN_LOGIN = original_limit
            self.legacy.RATE_LIMIT_BUCKETS.clear()

        self.assertEqual(first.status_code, 403)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error"], "rate_limited")
        self.assertTrue(second.headers["Retry-After"])

    def test_native_admin_config_write_filters_and_normalizes_form_fields(self):
        persisted = {
            "ok": True,
            "changed": True,
            "changed_names": ["DASHBOARD_B1_SCHEDULE_TIMES"],
            "runtime": {"ok": True},
        }
        with (
            patch.object(self.legacy, "validate_admin_session", return_value=True),
            patch.object(
                self.legacy,
                "admin_visible_env_names",
                return_value=["DASHBOARD_B1_SCHEDULE_TIMES"],
            ),
            patch.object(
                self.legacy,
                "normalize_business_updates",
                side_effect=lambda updates: updates,
            ) as normalize,
            patch.object(self.legacy, "validate_business_updates") as validate,
            patch.object(
                self.legacy,
                "removed_notification_config_names",
                return_value={"DASHBOARD_TELEGRAM_BOT_TOKEN"},
            ) as removed,
            patch.object(
                self.legacy,
                "persist_and_sync_business_updates",
                return_value=dict(persisted),
            ) as persist,
            patch.object(
                self.legacy,
                "build_admin_config_payload",
                return_value={"items": []},
            ),
        ):
            response = self.client.post(
                "/api/admin/config/env",
                content=(
                    "env__DASHBOARD_B1_SCHEDULE_TIMES=09%3A30&"
                    "env__DASHBOARD_B1_SCHEDULE_TIMES=10%3A00&"
                    "env__NOT_ALLOWED=ignored&notification_remove__telegram=1"
                ),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-NiuOne-Action": "1",
                },
            )
            unknown_group = self.client.post(
                "/api/admin/config/env/not-a-group",
                content="",
                headers={"X-NiuOne-Action": "1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["restart"]["skipped"], "hot_applied")
        self.assertEqual(response.json()["config"], {"items": []})
        expected_updates = {"DASHBOARD_B1_SCHEDULE_TIMES": "09:30,10:00"}
        normalize.assert_called_once_with(expected_updates)
        validate.assert_called_once_with(expected_updates)
        removed.assert_called_once_with({"telegram"})
        persist.assert_called_once_with(
            expected_updates,
            clear_names={"DASHBOARD_TELEGRAM_BOT_TOKEN"},
        )
        self.assertEqual(unknown_group.status_code, 404)
        self.assertEqual(unknown_group.json()["error"], "unknown_settings_group")

    def test_native_admin_connection_tests_filter_overrides(self):
        action_headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-NiuOne-Action": "true",
        }
        with (
            patch.object(self.legacy, "validate_admin_session", return_value=True),
            patch.object(
                self.legacy,
                "model_test_override_names",
                return_value={"DASHBOARD_GROK_API_KEY"},
            ),
            patch.object(
                self.legacy,
                "send_iwencai_connection_test",
                return_value={"ok": True, "message": "iwencai"},
            ) as iwencai,
            patch.object(
                self.legacy,
                "send_model_connection_test",
                return_value={"ok": True, "message": "model"},
            ) as model,
            patch.object(
                self.legacy,
                "send_notification_test",
                return_value={"ok": True, "message": "notification"},
            ) as notification,
        ):
            iwencai_response = self.client.post(
                "/api/admin/iwencai/test",
                content="env__IWENCAI_BASE_URL=https%3A%2F%2Fexample.test&env__IGNORED=secret",
                headers=action_headers,
            )
            model_response = self.client.post(
                "/api/admin/models/test",
                content="target=grok-model&env__DASHBOARD_GROK_API_KEY=key&env__IGNORED=secret",
                headers=action_headers,
            )
            notification_response = self.client.post(
                "/api/admin/notifications/test",
                content=(
                    "channel=telegram&env__DASHBOARD_TELEGRAM_BOT_TOKEN=token&"
                    "env__DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS=8&env__IGNORED=secret"
                ),
                headers=action_headers,
            )

        self.assertEqual(iwencai_response.json()["message"], "iwencai")
        self.assertEqual(model_response.json()["message"], "model")
        self.assertEqual(notification_response.json()["message"], "notification")
        iwencai.assert_called_once_with(
            {"IWENCAI_BASE_URL": "https://example.test"}
        )
        model.assert_called_once_with(
            "grok-model",
            {"DASHBOARD_GROK_API_KEY": "key"},
        )
        notification.assert_called_once_with(
            "telegram",
            {
                "DASHBOARD_TELEGRAM_BOT_TOKEN": "token",
                "DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS": "8",
            },
        )

    def test_native_admin_writes_require_action_header_and_support_yaml(self):
        with patch.object(self.legacy, "validate_admin_session", return_value=True):
            missing_action = self.client.post(
                "/api/admin/config/yaml",
                content="config_yaml=model%3A+%7B%7D",
            )
        self.assertEqual(missing_action.status_code, 403)
        self.assertEqual(missing_action.json()["error"], "action_header_required")

        with (
            patch.object(self.legacy, "validate_admin_session", return_value=True),
            patch.object(
                self.legacy,
                "write_yaml_config",
                return_value={"ok": True, "changed": True},
            ) as write_yaml,
        ):
            saved = self.client.post(
                "/api/admin/config/yaml",
                content="config_yaml=model%3A+%7B%7D",
                headers={"X-NiuOne-Action": "yes"},
            )

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), {"ok": True, "changed": True})
        write_yaml.assert_called_once_with("model: {}")

    def test_native_practice_actions_bypass_the_legacy_adapter(self):
        trader = Mock()
        trader.resume_trading.return_value = {"ok": True, "resumed": True}
        action_headers = {"X-NiuOne-Action": "1"}
        with (
            patch.object(self.legacy, "validate_admin_session", return_value=True),
            patch.object(
                self.legacy,
                "trigger_b1_scan",
                return_value={"ok": True, "candidates": []},
            ) as trigger,
            patch.object(self.legacy, "invalidate_api_cache") as invalidate,
            patch.object(
                self.legacy,
                "start_practice_manual_cycle",
                return_value={"ok": True, "started": True},
            ) as manual_cycle,
            patch.object(
                self.legacy,
                "generate_practice_market_summary",
                side_effect=[
                    {"ok": True, "generated": True},
                    {"ok": False, "error": "summary_busy"},
                ],
            ) as market_summary,
            patch.object(self.legacy, "get_trader_module", return_value=trader),
            patch.object(
                self.legacy,
                "apply_self_optimization",
                return_value={"ok": True, "applied": True},
            ) as optimize,
        ):
            refresh = self.client.post(
                "/api/practice_candidates/refresh",
                headers=action_headers,
            )
            legacy_without_force = self.client.post(
                "/api/b1_screen",
                headers=action_headers,
            )
            manual = self.client.post(
                "/api/niuniu_practice/manual-cycle",
                headers=action_headers,
            )
            summary = self.client.post(
                "/api/niuniu_practice/market-summary",
                headers=action_headers,
            )
            summary_busy = self.client.post(
                "/api/niuniu_practice/market-summary",
                headers=action_headers,
            )
            resumed = self.client.post(
                "/api/niuniu_practice/resume",
                headers=action_headers,
            )
            optimized = self.client.post(
                "/api/self_optimize/apply",
                headers=action_headers,
            )

        self.assertEqual(refresh.status_code, 200)
        self.assertEqual(refresh.json(), {"ok": True, "candidates": []})
        self.assertEqual(legacy_without_force.status_code, 404)
        self.assertEqual(manual.json(), {"ok": True, "started": True})
        self.assertEqual(summary.json(), {"ok": True, "generated": True})
        self.assertEqual(summary_busy.status_code, 409)
        self.assertEqual(summary_busy.json()["error"], "summary_busy")
        self.assertEqual(resumed.json(), {"ok": True, "resumed": True})
        self.assertEqual(optimized.json(), {"ok": True, "applied": True})
        trigger.assert_called_once_with(force=True)
        manual_cycle.assert_called_once_with()
        self.assertEqual(market_summary.call_count, 2)
        trader.resume_trading.assert_called_once_with()
        optimize.assert_called_once_with()
        invalidate.assert_any_call(self.legacy.PRACTICE_CANDIDATES_CACHE_KEY)
        invalidate.assert_any_call("niuniu_practice", self.legacy.PRACTICE_FAST_CACHE_KEY)

    def test_missing_vue_build_has_diagnostic_503(self):
        app = create_app(
            legacy_module=self.legacy,
            web_dist_dir=self.root / "missing-dist",
            enable_background_services=False,
        )
        with TestClient(app) as client:
            response = client.get("/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "vue_frontend_not_built")

    def test_vue_sources_preserve_the_existing_page_shell_contract(self):
        dashboard = (ROOT / "web" / "src" / "components" / "DashboardPage.vue").read_text(
            encoding="utf-8"
        )
        admin = (ROOT / "web" / "src" / "components" / "AdminPage.vue").read_text(
            encoding="utf-8"
        )
        for value in (
            "<ComplianceDialog />",
            "<CategoryTabs />",
            "<LastUpdated />",
            "<VersionStatus />",
            'button-id="themeToggle"',
            'href="/admin"',
        ):
            self.assertIn(value, dashboard)
        for value in (
            "<AdminLogin",
            "<AdminPageTitle />",
            "<AdminSettingsGroup",
            "<AdminSettingsIndex",
            'button-id="adminThemeToggle"',
            'id="adminApp"',
            'href="/"',
            "useAdminConfig",
        ):
            self.assertIn(value, admin)
        self.assertNotIn("callLegacy", dashboard)
        self.assertNotIn("callLegacy", admin)
        self.assertNotIn("useLegacyController", admin)
        self.assertNotIn("/static/admin.js", admin)
        self.assertNotIn("adminLegacyContent", admin)
        self.assertNotIn("niuone:admin-config", admin)
        self.assertNotIn("niuone:admin-navigate", admin)
        compliance = (
            ROOT / "web" / "src" / "components" / "ComplianceDialog.vue"
        ).read_text(encoding="utf-8")
        version = (
            ROOT / "web" / "src" / "components" / "VersionStatus.vue"
        ).read_text(encoding="utf-8")
        theme = (ROOT / "web" / "src" / "components" / "ThemeToggle.vue").read_text(
            encoding="utf-8"
        )
        tabs = (ROOT / "web" / "src" / "components" / "CategoryTabs.vue").read_text(
            encoding="utf-8"
        )
        dragon_tiger = (
            ROOT / "web" / "src" / "components" / "DragonTigerPanel.vue"
        ).read_text(encoding="utf-8")
        indices = (
            ROOT / "web" / "src" / "components" / "IndicesPanel.vue"
        ).read_text(encoding="utf-8")
        index_overview = (
            ROOT / "web" / "src" / "components" / "indices" / "IndexOverview.vue"
        ).read_text(encoding="utf-8")
        index_sparkline = (
            ROOT / "web" / "src" / "components" / "indices" / "IndexSparkline.vue"
        ).read_text(encoding="utf-8")
        market_breadth = (
            ROOT / "web" / "src" / "components" / "indices" / "MarketBreadthChart.vue"
        ).read_text(encoding="utf-8")
        market_overview = (
            ROOT / "web" / "src" / "components" / "indices" / "MarketOverview.vue"
        ).read_text(encoding="utf-8")
        indices_data = (
            ROOT / "web" / "src" / "composables" / "useIndicesData.js"
        ).read_text(encoding="utf-8")
        industry_flow = (
            ROOT / "web" / "src" / "components" / "IndustryFlowPanel.vue"
        ).read_text(encoding="utf-8")
        industry_flow_data = (
            ROOT / "web" / "src" / "composables" / "useIndustryFlowData.js"
        ).read_text(encoding="utf-8")
        industry_flow_animation = (
            ROOT / "web" / "src" / "composables" / "useIndustryFlowAnimation.js"
        ).read_text(encoding="utf-8")
        market_monitor = (
            ROOT / "web" / "src" / "components" / "MarketMonitorPanel.vue"
        ).read_text(encoding="utf-8")
        market_monitor_card = (
            ROOT / "web" / "src" / "components" / "market-monitor" / "MarketMonitorCard.vue"
        ).read_text(encoding="utf-8")
        market_monitor_detail = (
            ROOT / "web" / "src" / "components" / "market-monitor" / "MarketDetail.vue"
        ).read_text(encoding="utf-8")
        market_monitor_summary = (
            ROOT / "web" / "src" / "components" / "market-monitor" / "UsMarketSummaryCard.vue"
        ).read_text(encoding="utf-8")
        market_monitor_data = (
            ROOT / "web" / "src" / "composables" / "useMarketMonitorData.js"
        ).read_text(encoding="utf-8")
        market_monitor_display = (
            ROOT / "web" / "src" / "utils" / "marketMonitorDisplay.js"
        ).read_text(encoding="utf-8")
        us_ratings = (
            ROOT / "web" / "src" / "components" / "UsRatingsPanel.vue"
        ).read_text(encoding="utf-8")
        us_rating_card = (
            ROOT / "web" / "src" / "components" / "us-ratings" / "UsRatingCard.vue"
        ).read_text(encoding="utf-8")
        us_ratings_data = (
            ROOT / "web" / "src" / "composables" / "useUsRatingsData.js"
        ).read_text(encoding="utf-8")
        us_rating_display = (
            ROOT / "web" / "src" / "utils" / "usRatingDisplay.js"
        ).read_text(encoding="utf-8")
        x_monitor = (
            ROOT / "web" / "src" / "components" / "XMonitorPanel.vue"
        ).read_text(encoding="utf-8")
        x_monitor_row = (
            ROOT / "web" / "src" / "components" / "x-monitor" / "XMonitorRow.vue"
        ).read_text(encoding="utf-8")
        x_monitor_gallery = (
            ROOT / "web" / "src" / "components" / "x-monitor" / "XMediaGallery.vue"
        ).read_text(encoding="utf-8")
        x_monitor_viewer = (
            ROOT / "web" / "src" / "components" / "x-monitor" / "XImageViewer.vue"
        ).read_text(encoding="utf-8")
        x_monitor_data = (
            ROOT / "web" / "src" / "composables" / "useXMonitorData.js"
        ).read_text(encoding="utf-8")
        x_monitor_display = (
            ROOT / "web" / "src" / "utils" / "xMonitorDisplay.js"
        ).read_text(encoding="utf-8")
        dashboard_tabs = (
            ROOT / "web" / "src" / "composables" / "useDashboardTabs.js"
        ).read_text(encoding="utf-8")
        updated = (ROOT / "web" / "src" / "components" / "LastUpdated.vue").read_text(
            encoding="utf-8"
        )
        admin_title = (
            ROOT / "web" / "src" / "components" / "AdminPageTitle.vue"
        ).read_text(encoding="utf-8")
        admin_login = (
            ROOT / "web" / "src" / "components" / "AdminLogin.vue"
        ).read_text(encoding="utf-8")
        admin_index = (
            ROOT / "web" / "src" / "components" / "AdminSettingsIndex.vue"
        ).read_text(encoding="utf-8")
        admin_group = (
            ROOT / "web" / "src" / "components" / "AdminSettingsGroup.vue"
        ).read_text(encoding="utf-8")
        admin_input = (
            ROOT / "web" / "src" / "components" / "AdminEnvInput.vue"
        ).read_text(encoding="utf-8")
        admin_tests = (
            ROOT / "web" / "src" / "components" / "AdminConnectionTests.vue"
        ).read_text(encoding="utf-8")
        admin_notifications = (
            ROOT / "web" / "src" / "components" / "AdminNotificationSettings.vue"
        ).read_text(encoding="utf-8")
        admin_config = (
            ROOT / "web" / "src" / "composables" / "useAdminConfig.js"
        ).read_text(encoding="utf-8")
        self.assertIn('id="complianceDialog"', compliance)
        self.assertIn('id="versionStatus"', version)
        self.assertIn(':id="buttonId"', theme)
        self.assertIn('id="categoryTabs"', tabs)
        self.assertIn("router.push(dashboardCategoryPath(category))", tabs)
        self.assertIn("initializeDashboardTabs", tabs)
        self.assertIn("useDashboardTabs", tabs)
        self.assertIn("<DragonTigerPanel", dashboard)
        self.assertIn("activeCategory === 'dragon_tiger'", dashboard)
        self.assertIn("/api/iwencai/dragon-tiger", dragon_tiger)
        self.assertIn("REFRESH_INTERVAL_MS", dragon_tiger)
        self.assertIn("dragon-tiger-sort-btn", dragon_tiger)
        self.assertIn("dragon-tiger-seat-record", dragon_tiger)
        self.assertIn("setCategoryCount('dragon_tiger'", dragon_tiger)
        self.assertIn("<IndicesPanel", dashboard)
        self.assertIn("activeCategory === 'indices'", dashboard)
        self.assertIn("<IndexOverview", indices)
        self.assertIn("<MarketOverview", indices)
        self.assertIn("router.push('/industry-flow')", indices)
        self.assertIn("niuniu-dashboard-index-priority-v1", indices)
        self.assertIn("@click=\"selectPanel('market-breadth')\">市场情绪</button>", indices)
        self.assertIn("market-strip", index_overview)
        self.assertIn("<IndexSparkline", index_overview)
        self.assertNotIn("<MarketBreadthChart", index_overview)
        self.assertIn("<MarketBreadthChart", indices)
        self.assertIn("sparkline-zero", index_sparkline)
        self.assertIn("visibleSeries.value.map(series => series.label).join('、')", market_breadth)
        self.assertIn("下方量能区间（亿元）", market_breadth)
        self.assertIn('v-model="showBreadth"', market_breadth)
        self.assertIn('v-model="showLimitState"', market_breadth)
        self.assertIn('v-model="showVolume"', market_breadth)
        self.assertIn("预测 / 实际 / 增量", market_breadth)
        self.assertIn("estimated_turnover_yi", market_breadth)
        self.assertIn("actual_turnover_yi", market_breadth)
        self.assertIn("turnover_increment_yi", market_breadth)
        self.assertIn("增量基准：", market_breadth)
        self.assertIn("const turnoverSourceText = computed", market_breadth)
        self.assertIn("const turnoverEstimateText = computed", market_breadth)
        self.assertIn("量能估算：", market_breadth)
        self.assertIn("turnover_profile_days", market_breadth)
        self.assertIn("market-breadth-volume-grid", market_breadth)
        self.assertIn("market-breadth-volume-grid-zero", market_breadth)
        self.assertIn("market-breadth-line-muted", market_breadth)
        self.assertIn("market-breadth-axis-line", market_breadth)
        self.assertIn("market-breadth-endpoint", market_breadth)
        self.assertIn(":r=\"series.muted ? 1.45 : 1.9\"", market_breadth)
        self.assertIn("const drawSeries = computed", market_breadth)
        self.assertIn("const endLabels = [", market_breadth)
        self.assertIn("...spreadEndLabels(", market_breadth)
        self.assertIn("const labelRailX = Math.min", market_breadth)
        self.assertIn("latestX + 14", market_breadth)
        self.assertIn("market-breadth-end-label-connector", market_breadth)
        self.assertIn("market-breadth-end-label-bg", market_breadth)
        self.assertIn("market-breadth-end-label", market_breadth)
        self.assertIn("上午无有效采样", market_breadth)
        self.assertIn("market-breadth-missing-period", market_breadth)
        self.assertIn('@pointermove="updateHover"', market_breadth)
        self.assertIn('@pointerleave="clearHover"', market_breadth)
        self.assertIn("clearHoverOutside", market_breadth)
        self.assertIn('ref="chartElement"', market_breadth)
        self.assertIn("market-breadth-crosshair", market_breadth)
        self.assertIn("market-breadth-tooltip-panel", market_breadth)
        self.assertIn("const rows = visibleSeries.value.map", market_breadth)
        self.assertIn("42 + rows.length * 14", market_breadth)
        self.assertIn("row.displayValue", market_breadth)
        self.assertIn("color: 'var(--market-breadth-limit-up, #fb7185)'", market_breadth)
        self.assertIn("color: 'var(--market-breadth-limit-down, #4ade80)'", market_breadth)
        self.assertIn("color: 'var(--market-breadth-red, #e879f9)'", market_breadth)
        self.assertIn("color: 'var(--market-breadth-green, #38bdf8)'", market_breadth)
        self.assertNotIn("stroke-dasharray", market_breadth)
        self.assertIn("主力资金流向", market_overview)
        self.assertIn("美股板块行情暂不可用", market_overview)
        for endpoint in (
            "/api/indices",
            "/api/market_breadth",
            "/api/sectors",
            "/api/us_sectors",
            "/api/hot_stocks",
            "/api/money_flow",
            "/api/market_flow",
        ):
            self.assertIn(endpoint, indices_data)
        self.assertIn("REFRESH_INTERVAL_MS = 15 * 1000", indices_data)
        self.assertIn("MONEY_FLOW_REFRESH_INTERVAL_MS = 60 * 1000", indices_data)
        self.assertIn("MARKET_BREADTH_REFRESH_INTERVAL_MS = 60 * 1000", indices_data)
        self.assertIn("<IndustryFlowPanel", dashboard)
        self.assertIn("activeCategory === 'industry_flow'", dashboard)
        self.assertIn("/api/industry-flow?compact=1", industry_flow_data)
        self.assertIn("REFRESH_INTERVAL_MS = 60 * 1000", industry_flow_data)
        self.assertIn("REQUEST_TIMEOUT_MS = 15 * 1000", industry_flow_data)
        self.assertIn("adoptMoneyFlow", industry_flow_data)
        self.assertIn('id="industryFlowStage"', industry_flow)
        self.assertIn('id="industryFlowSeek"', industry_flow)
        self.assertIn("<TransitionGroup", industry_flow)
        self.assertIn("path: '/indices'", industry_flow)
        self.assertIn("@click=\"selectPanel('market-breadth')\">市场情绪</button>", industry_flow)
        self.assertIn("SAMPLE_PLAYBACK_MS = 460", industry_flow_animation)
        self.assertIn("function frameAt(payload, progress)", industry_flow_animation)
        self.assertIn("export function splitSortedNodes", industry_flow_animation)
        self.assertIn("speedOptions: SPEED_OPTIONS", industry_flow_animation)
        self.assertIn("<MarketMonitorPanel", dashboard)
        self.assertIn("activeCategory === 'market_monitor'", dashboard)
        self.assertIn("<MarketMonitorCard", market_monitor)
        self.assertIn("<UsMarketSummaryCard", market_monitor)
        self.assertIn("<MarketDetail", market_monitor_card)
        self.assertIn("<MarketSection", market_monitor_detail)
        self.assertIn('id="us-market-summary-body"', market_monitor_summary)
        self.assertIn("/api/messages/revision?category=${CATEGORY}", market_monitor_data)
        self.assertIn("REFRESH_INTERVAL_MS = 15 * 1000", market_monitor_data)
        self.assertIn("CACHE_KEY = 'niuniu-dashboard-market-page-v2'", market_monitor_data)
        self.assertIn("export function marketReportType", market_monitor_display)
        self.assertIn("export function groupMarketRecordsByDay", market_monitor_display)
        self.assertIn("<UsRatingsPanel", dashboard)
        self.assertIn("activeCategory === 'us_ratings'", dashboard)
        self.assertIn("<UsRatingCard", us_ratings)
        self.assertIn("loadQuotesForRecords", us_ratings)
        self.assertIn('class="rating-table"', us_rating_card)
        self.assertIn("props.loadProfile(row.ticker)", us_rating_card)
        self.assertIn("/api/messages/revision?category=${CATEGORY}", us_ratings_data)
        self.assertIn("REFRESH_INTERVAL_MS = 10 * 60 * 1000", us_ratings_data)
        self.assertIn("/api/us_quotes", us_ratings_data)
        self.assertIn("/api/us_profiles", us_ratings_data)
        self.assertIn("export function parseRatingReport", us_rating_display)
        self.assertIn("export function groupRatingRecordsByDay", us_rating_display)
        self.assertIn("<XMonitorPanel", dashboard)
        self.assertIn("activeCategory === 'x_monitor'", dashboard)
        self.assertIn("<XMonitorRow", x_monitor)
        self.assertIn("<XImageViewer", x_monitor)
        self.assertIn('class="x-row"', x_monitor_row)
        self.assertIn("<XMediaGallery", x_monitor_row)
        self.assertIn('class="x-media-tile"', x_monitor_gallery)
        self.assertIn("<Teleport to=\"body\">", x_monitor_viewer)
        self.assertIn("REFRESH_INTERVAL_MS = 15 * 1000", x_monitor_data)
        self.assertIn("CACHE_TTL_MS = 5 * 60 * 1000", x_monitor_data)
        self.assertIn("/api/messages/revision?category=${CATEGORY}&limit=", x_monitor_data)
        self.assertIn("prefetchAdjacentPages", x_monitor_data)
        self.assertIn("export function summarizeXRecord", x_monitor_display)
        self.assertIn("export function parseXThread", x_monitor_display)
        self.assertIn("export function xMediaGroups", x_monitor_display)
        self.assertIn("countOverrides", dashboard_tabs)
        self.assertIn("const activeCategory = ref", dashboard_tabs)
        self.assertIn("fetch('/api/dashboard/bootstrap'", dashboard_tabs)
        self.assertIn("const items = computed", dashboard_tabs)
        self.assertIn('id="updated"', updated)
        self.assertIn("niuone:last-updated", updated)
        self.assertIn('id="adminPageTitle"', admin_title)
        self.assertIn("niuone:admin-title", admin_title)
        self.assertIn('class="admin-login-box"', admin_login)
        self.assertIn('v-model="credential"', admin_login)
        self.assertIn("props.authenticate", admin_login)
        self.assertNotIn(
            "凭据只会提交到当前 NiuOne 服务，不会保存在浏览器页面中。",
            admin_login,
        )
        self.assertIn('class="settings-index"', admin_index)
        self.assertIn('class="settings-grid"', admin_index)
        self.assertIn("<RouterLink", admin_index)
        self.assertIn("group.item_count", admin_index)
        self.assertIn('id="env-config-form"', admin_group)
        self.assertIn(":data-save-endpoint", admin_group)
        self.assertIn("<AdminEnvInput", admin_group)
        self.assertIn("<AdminConnectionTests", admin_group)
        self.assertIn("data-feature-gated", admin_group)
        self.assertIn("data-strategy-source-gated", admin_group)
        self.assertIn("@submit.prevent.stop=\"save\"", admin_group)
        self.assertIn("onBeforeRouteLeave", admin_group)
        self.assertIn("onBeforeRouteUpdate", admin_group)
        self.assertIn("beforeunload", admin_group)
        self.assertIn("/api/admin/config/env/", admin_group)
        self.assertIn("/api/admin/models/test", admin_group)
        self.assertIn("/api/admin/iwencai/test", admin_group)
        self.assertIn("/api/admin/notifications/test", admin_group)
        self.assertIn("<AdminNotificationSettings", admin_group)
        for field_kind in (
            "time_list",
            "handle_list",
            "stock_universe",
            "strategy_source",
            "strategy_suite",
            "preset_strategy_text",
            "trade_discipline_text",
            "strategy_multi",
            "strategy_single",
            "context_length",
            "max_tokens",
        ):
            self.assertIn(field_kind, admin_input)
        self.assertIn("data-time-list-add", admin_input)
        self.assertIn("data-model-test", admin_tests)
        self.assertIn("data-iwencai-test", admin_tests)
        self.assertIn("test-model", admin_tests)
        self.assertIn("test-iwencai", admin_tests)
        self.assertIn("data-notification-channel-add", admin_notifications)
        self.assertIn("data-notification-channel-activation", admin_notifications)
        self.assertIn("data-notification-channel-remove", admin_notifications)
        self.assertIn("data-notification-channel-test", admin_notifications)
        self.assertIn("notification_remove__", admin_notifications)
        self.assertIn("defineExpose({ applySavedConfig })", admin_notifications)
        self.assertIn("fetch('/api/admin/config'", admin_config)
        self.assertIn("fetch('/api/admin/session'", admin_config)
        self.assertIn("credentials: 'same-origin'", admin_config)
        self.assertNotIn("useLegacyController", dashboard)
        self.assertNotIn("/static/dashboard.js", dashboard)
        self.assertNotIn("niuone:category-select", tabs + indices + industry_flow)
        self.assertNotIn("niuone:category-tabs", tabs + dashboard_tabs)
        self.assertIn("router.push(dashboardCategoryPath(category))", tabs)
        self.assertIn("router.push('/industry-flow')", indices)
        self.assertIn("router.push({ path: '/indices'", industry_flow)
        self.assertIn("<AdminSettingsIndex", admin)
        self.assertIn("<AdminSettingsGroup", admin)
        self.assertIn("function addChannel()", admin_notifications)
        self.assertIn("function removeChannel(channelId)", admin_notifications)
        self.assertIn("fetch('/api/admin/notifications/test'", admin_group)
        package = (ROOT / "web" / "package.json").read_text(encoding="utf-8")
        self.assertIn('"vue": "3.5.40"', package)
        self.assertIn('"vite": "7.3.6"', package)

    def test_market_breadth_chart_uses_compact_responsive_dimensions(self):
        component = (
            ROOT / "web" / "src" / "components" / "indices" / "MarketBreadthChart.vue"
        ).read_text(encoding="utf-8")
        stylesheet = (ROOT / "frontend" / "dashboard.css").read_text(encoding="utf-8")

        self.assertIn(
            "const width = chartWidth.value",
            component,
        )
        self.assertIn("const compact = width < 560", component)
        self.assertIn(
            "const height = showSentiment && showVolume.value ? (compact ? 280 : 330)",
            component,
        )
        self.assertIn(
            "const volumeTop = showSentiment ? sentimentBottom + (compact ? 20 : 24)",
            component,
        )
        self.assertIn("new ResizeObserver(syncChartWidth)", component)
        self.assertIn("watch(chartWrapElement, element =>", component)
        self.assertIn('ref="chartWrapElement"', component)
        self.assertIn("width:min(100%,720px); max-width:720px;", stylesheet)
        self.assertIn("overflow-x:hidden", stylesheet)
        self.assertIn(
            ".market-breadth-controls { display:grid; grid-template-columns:repeat(3,minmax(0,1fr));",
            stylesheet,
        )
        self.assertIn(".market-breadth-toggle small { display:none; }", stylesheet)
        self.assertIn(
            ".market-breadth-chart { width:100%; max-width:none; min-width:0; }",
            stylesheet,
        )

    def test_mobile_compliance_dialog_is_centered(self):
        stylesheet = (ROOT / "frontend" / "dashboard.css").read_text(encoding="utf-8")

        self.assertIn(
            ".compliance-dialog-backdrop { place-items:center; padding:12px; }",
            stylesheet,
        )
        self.assertNotIn(
            ".compliance-dialog-backdrop { align-items:end; padding:12px; }",
            stylesheet,
        )

    def test_http_boundaries_remain_split_into_fastapi_routers(self):
        composition = (
            ROOT / "app" / "dashboard" / "fastapi_app.py"
        ).read_text(encoding="utf-8")
        backend = (
            ROOT / "app" / "dashboard" / "server.py"
        ).read_text(encoding="utf-8")
        router_dir = ROOT / "app" / "dashboard" / "routers"
        router_sources = {
            name: (router_dir / f"{name}.py").read_text(encoding="utf-8")
            for name in ("system", "messages", "market", "practice", "admin")
        }

        self.assertNotIn('@app.api_route("/api/', composition)
        self.assertNotIn('@app.get("/api/', composition)
        self.assertNotIn('@app.post("/api/', composition)
        self.assertNotIn("BaseHTTPRequestHandler", backend)
        self.assertNotIn("ThreadingHTTPServer", backend)
        self.assertNotIn("class Handler", backend)

        for module_name in ("security", "visit_stats", "response_cache"):
            with self.subTest(service_module=module_name):
                self.assertTrue(
                    (ROOT / "app" / "dashboard" / f"{module_name}.py").is_file()
                )
                self.assertIn(
                    f"from dashboard import {module_name} as {module_name}_impl",
                    backend,
                )

        for name, route in (
            ("system", "/api/v2/public/latest"),
            ("messages", "/api/messages/revision"),
            ("market", "/api/industry-flow"),
            ("practice", "/api/niuniu_practice"),
            ("admin", "/api/admin/config"),
        ):
            with self.subTest(router=name):
                self.assertIn(route, router_sources[name])

        for removed_path in (
            ROOT / "frontend" / "index.html",
            ROOT / "frontend" / "admin.html",
            ROOT / "frontend" / "dashboard.js",
            ROOT / "frontend" / "admin.js",
        ):
            with self.subTest(removed_path=removed_path.name):
                self.assertFalse(removed_path.exists())


if __name__ == "__main__":
    unittest.main()
