#!/usr/bin/env python3
import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
COMPAT = SRC / "compat"
ENTRYPOINTS = SRC / "entrypoints"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(COMPAT))

from dashboard.apis.cache import load_cached_payload


def load_module(name):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", COMPAT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DashboardJsonCacheTests(unittest.TestCase):
    def test_force_refresh_bypasses_fresh_cache(self):
        calls = []

        result = load_cached_payload(
            Path("unused.json"),
            75,
            compute=lambda: calls.append(True) or {"value": "live"},
            empty={},
            read_cache=lambda _path, ttl: {"value": "cached"} if ttl is not None else None,
            write_cache=lambda _path, _data: None,
            force_refresh=True,
        )

        self.assertEqual(result, {"value": "live"})
        self.assertEqual(calls, [True])

    def test_read_json_cache_ignores_bad_json_and_non_object_values(self):
        cache = load_module("dashboard_json_cache")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cache.json"

            path.write_text("{bad", encoding="utf-8")
            self.assertIsNone(cache.read_json_cache(path))

            path.write_text("[1, 2, 3]", encoding="utf-8")
            self.assertIsNone(cache.read_json_cache(path))

    def test_write_json_cache_replaces_existing_file_atomically(self):
        cache = load_module("dashboard_json_cache")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cache.json"
            path.write_text('{"old": true}\n', encoding="utf-8")

            cache.write_json_cache(path, {"new": "牛"})

            self.assertEqual(cache.read_json_cache(path), {"new": "牛"})
            self.assertEqual(list(Path(td).glob(".*.tmp")), [])

    def test_dashboard_api_falls_back_to_stale_cache_on_compute_failure(self):
        stale_payload = {
            "generated_at": "2026-07-02 09:30:00",
            "items": [{"name": "旧缓存"}],
            "sectors": [{"name": "旧缓存"}],
            "gain_top": [{"name": "旧缓存"}],
            "loss_top": [],
            "inflow": [{"name": "旧缓存"}],
            "outflow": [],
            "amount_top": [{"name": "成交额"}],
            "turnover_top": [{"name": "换手率"}],
            "volume_top": [{"name": "成交量"}],
        }
        modules = [
            ("sectors_dashboard_api", "fetch_sector_data", None, lambda data: data["items"][0]["name"]),
            ("money_flow_dashboard_api", "fetch_money_flow", None, lambda data: data["inflow"][0]["name"]),
            ("hot_stocks_dashboard_api", "fetch_hot_stocks", "turnover", lambda data: data["items"][0]["name"]),
        ]

        for module_name, fetch_name, arg, pick in modules:
            with self.subTest(module=module_name), tempfile.TemporaryDirectory() as td:
                mod = load_module(module_name)
                mod.CACHE_PATH = Path(td) / "cache.json"
                mod.CACHE_PATH.write_text(
                    mod.json.dumps(stale_payload, ensure_ascii=False),
                    encoding="utf-8",
                )
                old_mtime = time.time() - mod.CACHE_TTL - 10
                os.utime(mod.CACHE_PATH, (old_mtime, old_mtime))
                mod._compute = lambda: (_ for _ in ()).throw(RuntimeError("upstream down"))

                if arg is None:
                    data = getattr(mod, fetch_name)()
                else:
                    data = getattr(mod, fetch_name)(arg)

                self.assertTrue(data["stale_cache"])
                self.assertEqual(data["error"], "upstream down")
                self.assertEqual(pick(data), "换手率" if arg == "turnover" else "旧缓存")

    def test_dashboard_hot_stocks_sort_selects_requested_rank(self):
        dashboard = load_module("niuone_dashboard")
        data = {
            "items": [{"name": "默认"}],
            "amount_top": [{"name": "成交额"}],
            "turnover_top": [{"name": "换手率"}],
            "volume_top": [{"name": "成交量"}],
            "gain_top": [{"name": "涨幅"}],
        }

        self.assertEqual(dashboard.apply_hot_stocks_sort(data, "turnover")["items"][0]["name"], "换手率")
        self.assertEqual(dashboard.apply_hot_stocks_sort(data, "volume_top")["items"][0]["name"], "成交量")
        self.assertEqual(dashboard.apply_hot_stocks_sort(data, "hot")["items"][0]["name"], "涨幅")
        self.assertEqual(dashboard.apply_hot_stocks_sort(data, "unknown")["items"][0]["name"], "成交额")


if __name__ == "__main__":
    unittest.main()
