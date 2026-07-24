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
        self.original_iwencai_snapshot_file = self.legacy.IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE
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
        self.legacy.IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE = (
            self.legacy.CRON_OUTPUT_DIR / "iwencai_dragon_tiger_latest.json"
        )
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
        self.legacy.IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE = self.original_iwencai_snapshot_file
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
        non_cacheable_keys = []

        def cached_payload(cache_key, ttl, producer, *, cacheable=None):
            del ttl, producer
            seen_keys.append(cache_key)
            if cacheable is not None:
                non_cacheable_keys.append(cache_key)
            return json.dumps({"route": cache_key}).encode("utf-8"), True

        with (
            patch.object(self.legacy, "cache_get_json", side_effect=cached_payload),
            patch.object(self.legacy, "seed_api_cache_from_json_file", return_value=False),
            patch.object(self.legacy, "reset_daily_market_histories", return_value=False) as reset_daily,
        ):
            admin_cookie_header = {
                "Cookie": (
                    f"{self.legacy.ADMIN_SESSION_COOKIE_NAME}="
                    f"{self.legacy.new_admin_session()}"
                ),
            }
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
                    "iwencai_dragon_tiger:2026-07-16:2:10:0:0:0",
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
                    response = self.client.get(
                        path,
                        headers=(
                            admin_cookie_header
                            if "/dragon-tiger?date=" in path
                            else None
                        ),
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {"route": cache_key})
                    self.assertEqual(response.headers["X-Dashboard-Cache"], "HIT")
                    if "/dragon-tiger?date=" in path:
                        self.assertEqual(response.headers["Cache-Control"], "no-store")
                    else:
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
            "iwencai_dragon_tiger:2026-07-16:2:10:0:0:0",
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
        self.assertEqual(
            non_cacheable_keys,
            ["us_sectors", "money_flow", "industry_flow", "industry_flow:compact:v1"],
        )
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(invalid_dragon_tiger.status_code, 400)
        self.assertEqual(
            invalid_dragon_tiger.json()["error"],
            "invalid_iwencai_dragon_tiger_request",
        )
        self.assertEqual(forced_candidates.status_code, 405)
        self.assertEqual(forced_candidates.headers["Allow"], "POST")

    def test_dragon_tiger_retained_date_stays_public_until_next_snapshot(self):
        current_date = self.legacy.normalize_iwencai_trade_date("")
        historical_date = "2000-01-04"
        retained_date = "2000-01-05"
        self.assertTrue(
            self.legacy.write_dragon_tiger_snapshot(
                self.legacy.IWENCAI_DRAGON_TIGER_SNAPSHOT_FILE,
                {
                    "enabled": True,
                    "available": True,
                    "source": "同花顺问财",
                    "date": retained_date,
                    "items": [{"code": "000002.SZ"}],
                },
            )
        )
        payload = {
            "enabled": True,
            "available": True,
            "source": "同花顺问财",
            "date": historical_date,
            "items": [{"code": "000001.SZ"}],
        }
        with patch.object(
            self.legacy,
            "produce_iwencai_dragon_tiger_data",
            return_value=payload,
        ) as produce:
            locked = self.client.get(
                f"/api/iwencai/dragon-tiger?date={historical_date}"
            )
            locked_head = self.client.head(
                f"/api/iwencai/dragon-tiger?date={historical_date}"
            )
            retained = self.client.get(
                f"/api/iwencai/dragon-tiger?date={retained_date}"
            )
            retained_head = self.client.head(
                f"/api/iwencai/dragon-tiger?date={retained_date}"
            )
            current = self.client.get(
                f"/api/iwencai/dragon-tiger?date={current_date}"
            )
            unlocked = self.client.get(
                f"/api/iwencai/dragon-tiger?date={historical_date}",
                headers={
                    "Cookie": (
                        f"{self.legacy.ADMIN_SESSION_COOKIE_NAME}="
                        f"{self.legacy.new_admin_session()}"
                    ),
                },
            )

        self.assertEqual(locked.status_code, 403)
        self.assertEqual(locked.json(), {"error": "admin_password_required"})
        self.assertEqual(locked.headers["Cache-Control"], "no-store")
        self.assertEqual(locked_head.status_code, 403)
        self.assertEqual(retained.status_code, 200)
        self.assertEqual(retained.headers["Cache-Control"], "no-store")
        self.assertEqual(retained_head.status_code, 200)
        self.assertEqual(current.status_code, 200)
        self.assertEqual(unlocked.status_code, 200)
        self.assertEqual(unlocked.headers["Cache-Control"], "no-store")
        self.assertEqual(produce.call_count, 3)
        self.assertFalse(
            produce.call_args_list[0].kwargs["fallback_to_latest_on_empty"]
        )
        self.assertTrue(
            produce.call_args_list[1].kwargs["fallback_to_latest_on_empty"]
        )
        self.assertFalse(
            produce.call_args_list[2].kwargs["fallback_to_latest_on_empty"]
        )

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
            "changed_names": ["DASHBOARD_PRACTICE_SCHEDULE_TIMES"],
            "runtime": {"ok": True},
        }
        with (
            patch.object(self.legacy, "validate_admin_session", return_value=True),
            patch.object(
                self.legacy,
                "admin_visible_env_names",
                return_value=["DASHBOARD_PRACTICE_SCHEDULE_TIMES"],
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
                    "env__DASHBOARD_PRACTICE_SCHEDULE_TIMES=09%3A30&"
                    "env__DASHBOARD_PRACTICE_SCHEDULE_TIMES=10%3A00&"
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
        expected_updates = {"DASHBOARD_PRACTICE_SCHEDULE_TIMES": "09:30,10:00"}
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
                "start_practice_market_summary",
                side_effect=[
                    {"ok": True, "accepted": True, "running": True},
                    {"ok": True, "accepted": False, "running": True},
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
        self.assertEqual(summary.status_code, 202)
        self.assertEqual(summary.json(), {"ok": True, "accepted": True, "running": True})
        self.assertEqual(summary_busy.status_code, 202)
        self.assertEqual(summary_busy.json(), {"ok": True, "accepted": False, "running": True})
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
            "const baseHeight = showSentiment && showVolume.value ? (compact ? 280 : 330)",
            component,
        )
        self.assertIn(
            "Math.max(compactMinHeight, Math.min(baseHeight, chartAvailableHeight.value))",
            component,
        )
        self.assertIn("const visualViewport = window.visualViewport", component)
        self.assertIn("window.visualViewport?.addEventListener('resize', syncChartSize", component)
        self.assertIn("visualViewport.height + visualViewport.offsetTop", component)
        self.assertIn("const bottomReserve = availableWidth < 560 ? 56 : 40", component)
        self.assertIn("const availableHeight = Math.floor", component)
        self.assertIn(
            "const volumeTop = showSentiment ? sentimentBottom + sectionGap : margin.top",
            component,
        )
        self.assertIn("const drawableHeight = height - margin.top - margin.bottom - sectionGap", component)
        self.assertIn("new ResizeObserver(syncChartSize)", component)
        self.assertIn("window.addEventListener('resize', syncChartSize", component)
        self.assertIn("watch(chartWrapElement, element =>", component)
        self.assertIn('ref="chartWrapElement"', component)
        self.assertIn("chartWidth.value = Math.max(300, availableWidth)", component)
        self.assertNotIn("Math.min(720", component)
        self.assertIn(
            ".market-breadth-chart { display:block; width:100%; max-width:none; min-width:0;",
            stylesheet,
        )
        self.assertNotIn("width:min(100%,720px); max-width:720px;", stylesheet)
        self.assertIn("overflow-x:hidden", stylesheet)
        self.assertIn(
            '.market-breadth-head { display:grid; grid-template-columns:minmax(210px,1fr) auto auto; grid-template-areas:"heading controls meta";',
            stylesheet,
        )
        self.assertIn(
            ".market-breadth-controls { grid-area:controls; display:grid; grid-template-columns:repeat(3,max-content);",
            stylesheet,
        )
        self.assertIn(".market-breadth-info-popover { position:absolute;", stylesheet)
        self.assertNotIn("cursor:help", stylesheet)
        self.assertNotIn("outline:2px solid rgba(129,140,248,.42)", stylesheet)
        self.assertIn(
            ".market-breadth-info:hover .market-breadth-info-popover, .market-breadth-info:focus-within .market-breadth-info-popover",
            stylesheet,
        )
        self.assertIn("header { position:sticky; top:0; z-index:20;", stylesheet)
        self.assertIn(
            "box-shadow:inset 0 1px 0 rgba(255,255,255,.035), 0 10px 28px rgba(0,0,0,.14); -webkit-user-select:none; user-select:none;",
            stylesheet,
        )
        self.assertIn(
            ".market-breadth-controls { width:100%; grid-template-columns:repeat(3,minmax(0,1fr));",
            stylesheet,
        )
        self.assertIn('.market-breadth-toggle.active::before { content:"✓";', stylesheet)
        self.assertIn(
            ".market-breadth-toggle input { position:absolute; width:1px; height:1px;",
            stylesheet,
        )
        self.assertIn(".market-breadth-toggle small { display:none; }", stylesheet)

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

    def test_mobile_dragon_tiger_names_are_not_truncated_by_seat_counts(self):
        component = (
            ROOT / "web" / "src" / "components" / "DragonTigerPanel.vue"
        ).read_text(encoding="utf-8")
        stylesheet = (ROOT / "frontend" / "dashboard.css").read_text(encoding="utf-8")

        self.assertNotIn("seatBadge", component)
        self.assertNotIn("dragon-tiger-seat-badge", component + stylesheet)
        self.assertIn(
            ".dragon-tiger-list-name { align-items:flex-start; gap:3px; "
            "overflow:visible; white-space:normal; }",
            stylesheet,
        )
        self.assertIn(
            ".dragon-tiger-list-name > span { overflow:visible; text-overflow:clip; "
            "white-space:normal; overflow-wrap:anywhere; }",
            stylesheet,
        )
        self.assertIn("最近一次成功查询会保留至下次成功更新", component)
        self.assertNotIn("每日成功数据按交易日归档", component)
        self.assertIn("当日及当前保留的最近数据无需密码", component)
        self.assertIn("await authenticateAdmin(adminAuth.credential)", component)

    def test_dragon_tiger_foreground_query_exposes_loading_state(self):
        component = (
            ROOT / "web" / "src" / "components" / "DragonTigerPanel.vue"
        ).read_text(encoding="utf-8")
        stylesheet = (ROOT / "frontend" / "dashboard.css").read_text(encoding="utf-8")

        self.assertIn("if (payload.value.loading) return '实时回源查询中…'", component)
        self.assertIn("{{ payload.loading ? '查询中…' : '查看' }}", component)
        self.assertIn('v-if="payload.available || payload.loading"', component)
        self.assertIn('querying: payload.loading', component)
        self.assertIn('role="status"', component)
        self.assertIn('aria-live="polite"', component)
        self.assertGreaterEqual(component.count(':disabled="payload.loading"'), 3)
        self.assertIn(".dragon-tiger-status.querying", stylesheet)

    def test_dragon_tiger_details_distinguish_limit_up_and_listing_reasons(self):
        component = (
            ROOT / "web" / "src" / "components" / "DragonTigerPanel.vue"
        ).read_text(encoding="utf-8")

        self.assertIn('aria-label="涨停原因"', component)
        self.assertIn("item.limit_up_reason || item.limit_up_reason_category", component)
        self.assertIn("同花顺问财归纳，仅供研究参考", component)
        self.assertIn('aria-label="上榜理由"', component)

    def test_dragon_tiger_collapsed_rows_color_limit_up_reason_names(self):
        component = (
            ROOT / "web" / "src" / "components" / "DragonTigerPanel.vue"
        ).read_text(encoding="utf-8")
        stylesheet = (ROOT / "frontend" / "dashboard.css").read_text(
            encoding="utf-8"
        )

        self.assertIn("dragon-tiger-name-has-limit-up-reason", component)
        self.assertIn("，涨停原因：${limitUpReason(item)}` : undefined", component)
        self.assertNotIn("dragon-tiger-limit-up-marker", component + stylesheet)
        self.assertNotIn(">涨因</small>", component)
        self.assertIn(
            ".dragon-tiger-name-has-limit-up-reason { color:#fb7185; "
            "text-decoration-line:underline; text-decoration-style:dotted;",
            stylesheet,
        )
        self.assertIn(
            'html:not([data-theme="dark"]) '
            ".dragon-tiger-name-has-limit-up-reason { color:#a8173a;",
            stylesheet,
        )
        self.assertIn(
            'html[data-theme="dark"] '
            ".dragon-tiger-name-has-limit-up-reason { color:#fda4af;",
            stylesheet,
        )

    def test_dragon_tiger_limit_up_reason_tooltip_is_immediate_and_specific(self):
        component = (
            ROOT / "web" / "src" / "components" / "DragonTigerPanel.vue"
        ).read_text(encoding="utf-8")
        stylesheet = (ROOT / "frontend" / "dashboard.css").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("有涨停原因，展开查看", component)
        self.assertIn('@pointerenter="showLimitUpReasonTooltip($event, item)"', component)
        self.assertIn('@pointerleave="hideLimitUpReasonTooltip"', component)
        self.assertIn('v-if="limitUpTooltip.visible"', component)
        self.assertIn("<span>{{ limitUpTooltip.text }}</span>", component)
        self.assertIn('role="tooltip"', component)
        self.assertIn(".dragon-tiger-limit-up-tooltip { position:fixed;", stylesheet)
        self.assertIn("background:var(--panel); color:var(--text);", stylesheet)
        self.assertIn("nameCell.classList.contains('dragon-tiger-list-name')", component)
        self.assertIn("const rect = target.getBoundingClientRect()", component)
        self.assertIn("Array.from(nameCell.children).reduce(", component)
        self.assertIn("window.innerWidth - anchorRight - tooltipGap", component)
        self.assertIn("limitUpTooltip.x = anchorRight + tooltipGap", component)
        self.assertIn("limitUpTooltip.width = tooltipWidth", component)
        self.assertIn("rect.top + rect.height / 2 - estimatedHeight / 2", component)
        self.assertNotIn("event?.clientX", component)
        self.assertNotIn("event?.clientY", component)
        self.assertNotIn("limitUpTooltip.left", component)
        self.assertIn(
            ':style="{left: `${limitUpTooltip.x}px`, top: `${limitUpTooltip.y}px`, '
            'width: `${limitUpTooltip.width}px`}"',
            component,
        )
        self.assertNotIn("limitUpTooltip.above", component)
        self.assertNotIn(".dragon-tiger-limit-up-tooltip.left", stylesheet)
        self.assertNotIn("transition-delay", stylesheet)

    def test_dragon_tiger_page_blocks_selection_and_clipboard_copy(self):
        component = (
            ROOT / "web" / "src" / "components" / "DragonTigerPanel.vue"
        ).read_text(encoding="utf-8")
        stylesheet = (ROOT / "frontend" / "dashboard.css").read_text(
            encoding="utf-8"
        )

        self.assertIn("function preventDragonTigerClipboard(event)", component)
        self.assertIn("event.preventDefault()", component)
        self.assertIn(
            "document.addEventListener('copy', preventDragonTigerClipboard, true)",
            component,
        )
        self.assertIn(
            "document.addEventListener('cut', preventDragonTigerClipboard, true)",
            component,
        )
        self.assertIn(
            "document.removeEventListener('copy', preventDragonTigerClipboard, true)",
            component,
        )
        self.assertIn(
            "document.removeEventListener('cut', preventDragonTigerClipboard, true)",
            component,
        )
        self.assertIn(
            ".dragon-tiger-panel { width:100%; max-width:900px; margin-inline:auto; "
            "padding:0; -webkit-user-select:none; user-select:none; }",
            stylesheet,
        )
        self.assertIn("pointer-events:none; -webkit-user-select:none; user-select:none;", stylesheet)

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
