#!/usr/bin/env python3
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
COMPAT = SRC / "compat"
ENTRYPOINTS = SRC / "entrypoints"


def load_daemon_module():
    module_name = "x_watchlist_daemon_under_test"
    sys.path.insert(0, str(SRC))
    sys.path.insert(0, str(COMPAT))
    spec = importlib.util.spec_from_file_location(module_name, COMPAT / "x_watchlist_daemon.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class XWatchlistDaemonTests(unittest.TestCase):
    def test_us_feature_gate_defaults_off(self):
        daemon = load_daemon_module()

        self.assertFalse(daemon.us_features_enabled({}))
        self.assertFalse(daemon.us_features_enabled({"DASHBOARD_US_FEATURES_ENABLED": "0"}))
        self.assertTrue(daemon.us_features_enabled({"DASHBOARD_US_FEATURES_ENABLED": "1"}))
        self.assertTrue(daemon.us_features_enabled({"DASHBOARD_US_FEATURES_ENABLED": "yes"}))

    def test_x_watchlist_gate_defaults_on(self):
        daemon = load_daemon_module()

        self.assertTrue(daemon.x_watchlist_enabled({}))
        self.assertTrue(daemon.x_watchlist_enabled({"X_WATCHLIST_ENABLED": "1"}))
        self.assertFalse(daemon.x_watchlist_enabled({"X_WATCHLIST_ENABLED": "0"}))
        self.assertFalse(daemon.x_watchlist_enabled({"X_WATCHLIST_ENABLED": "false"}))

    def test_run_once_skips_inner_monitor_when_us_features_disabled(self):
        daemon = load_daemon_module()
        old_runtime_env = daemon.runtime_env
        old_run = daemon.subprocess.run
        old_log = daemon.log
        calls = []
        logs = []
        try:
            daemon.runtime_env = lambda: {"DASHBOARD_US_FEATURES_ENABLED": "0"}
            daemon.subprocess.run = lambda *_args, **_kwargs: calls.append(True)
            daemon.log = lambda message: logs.append(message)

            daemon.run_once()
        finally:
            daemon.runtime_env = old_runtime_env
            daemon.subprocess.run = old_run
            daemon.log = old_log

        self.assertEqual(calls, [])
        self.assertTrue(any("DASHBOARD_US_FEATURES_ENABLED is disabled" in item for item in logs))

    def test_run_once_skips_inner_monitor_when_x_watchlist_disabled(self):
        daemon = load_daemon_module()
        old_runtime_env = daemon.runtime_env
        old_run = daemon.subprocess.run
        old_log = daemon.log
        calls = []
        logs = []
        try:
            daemon.runtime_env = lambda: {
                "DASHBOARD_US_FEATURES_ENABLED": "1",
                "X_WATCHLIST_ENABLED": "0",
            }
            daemon.subprocess.run = lambda *_args, **_kwargs: calls.append(True)
            daemon.log = lambda message: logs.append(message)

            daemon.run_once()
        finally:
            daemon.runtime_env = old_runtime_env
            daemon.subprocess.run = old_run
            daemon.log = old_log

        self.assertEqual(calls, [])
        self.assertTrue(any("X_WATCHLIST_ENABLED is disabled" in item for item in logs))


if __name__ == "__main__":
    unittest.main()
