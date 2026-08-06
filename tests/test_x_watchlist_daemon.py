#!/usr/bin/env python3
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
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

    def test_runtime_env_preserves_explicit_x_watchlist_disable(self):
        daemon = load_daemon_module()
        old_parse_env_file = daemon.parse_env_file
        old_value = os.environ.get("X_WATCHLIST_ENABLED")
        try:
            os.environ["X_WATCHLIST_ENABLED"] = "0"
            daemon.parse_env_file = lambda: {"X_WATCHLIST_ENABLED": "1"}

            self.assertEqual(daemon.runtime_env()["X_WATCHLIST_ENABLED"], "0")
            self.assertFalse(daemon.x_watchlist_enabled())
        finally:
            daemon.parse_env_file = old_parse_env_file
            if old_value is None:
                os.environ.pop("X_WATCHLIST_ENABLED", None)
            else:
                os.environ["X_WATCHLIST_ENABLED"] = old_value

    def test_standalone_launcher_preserves_explicit_x_watchlist_disable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / "dashboard.env"
            env_file.write_text("X_WATCHLIST_ENABLED=1\n", encoding="utf-8")
            probe = root / "probe.py"
            probe.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os\n"
                "print(json.dumps({'enabled': os.environ.get('X_WATCHLIST_ENABLED')}))\n",
                encoding="utf-8",
            )
            probe.chmod(0o700)
            env = os.environ.copy()
            env.update(
                {
                    "DASHBOARD_ENV_FILE": str(env_file),
                    "DASHBOARD_HOME": str(root / "runtime"),
                    "PYTHON_BIN": str(probe),
                    "X_WATCHLIST_ENABLED": "0",
                }
            )

            output = subprocess.check_output(
                ["bash", str(ROOT / "run-x-watchlist-daemon.sh")],
                cwd=ROOT,
                env=env,
                text=True,
            )

            self.assertEqual(json.loads(output)["enabled"], "0")

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
