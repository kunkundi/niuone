#!/usr/bin/env python3
"""Architecture contracts for the split application domains."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
COMPAT = APP / "compat"
ENTRYPOINTS = APP / "entrypoints"


class AppModuleBoundaryTests(unittest.TestCase):
    def run_python(self, source: str) -> dict[str, object]:
        output = subprocess.check_output(
            [sys.executable, "-c", source],
            cwd=ROOT,
            text=True,
        )
        return json.loads(output)

    def test_app_root_contains_no_loose_python_modules(self):
        self.assertEqual([path.name for path in APP.glob("*.py")], ["__init__.py"])

    def test_simulated_trading_does_not_load_backtesting_package(self):
        result = self.run_python(
            f"""
import json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(APP)!r}]
import niuniu_practice_trader
print(json.dumps({{
    "loaded": any(
        name == "backtesting" or name.startswith("backtesting.")
        or name == "app.backtesting" or name.startswith("app.backtesting.")
        for name in sys.modules
    )
}}))
"""
        )
        self.assertEqual(result, {"loaded": False})

    def test_relocated_entrypoints_publish_their_implementation_path(self):
        result = self.run_python(
            f"""
import importlib.util, json
paths = {{}}
for name in ('cn_stock_tools', 'niuone_cron_scheduler'):
    spec = importlib.util.spec_from_file_location(name + '_relocation_test', {str(COMPAT)!r} + '/' + name + '.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths[name] = str(module.__implementation_file__)
print(json.dumps(paths))
"""
        )
        self.assertTrue(result["cn_stock_tools"].endswith("app/market_data/cn_stock_tools.py"))
        self.assertTrue(result["niuone_cron_scheduler"].endswith("app/automation/scheduler_service.py"))

    def test_messaging_registry_is_shared_when_package_is_imported_first(self):
        result = self.run_python(
            f"""
import json, sys
import app.messaging as package_first
import importlib
sys.path[:0] = [{str(COMPAT)!r}, {str(APP)!r}]
import messaging
import notifications
import app.compat.notifications as package_facade
submodules_same = all(
    importlib.import_module(f"app.messaging.{{name}}")
    is importlib.import_module(f"messaging.{{name}}")
    for name in ("models", "channels", "transport", "dispatcher", "trades")
)
print(json.dumps({{
    "same_package": package_first is messaging,
    "same_registry": notifications._CHANNEL_REGISTRY is package_first._CHANNEL_REGISTRY,
    "same_package_registry": package_facade._CHANNEL_REGISTRY is package_first._CHANNEL_REGISTRY,
    "same_submodules": submodules_same,
    "channels": list(notifications.registered_channels()),
}}))
"""
        )
        self.assertTrue(result["same_package"])
        self.assertTrue(result["same_registry"])
        self.assertTrue(result["same_package_registry"])
        self.assertTrue(result["same_submodules"])
        self.assertEqual(
            result["channels"],
            ["feishu", "dingtalk", "wecom", "telegram", "serverchan"],
        )

    def test_messaging_registry_is_shared_when_legacy_facade_is_imported_first(self):
        result = self.run_python(
            f"""
import importlib, json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(APP)!r}]
import notifications
import messaging.dispatcher
import app.messaging
import app.compat.notifications
submodules_same = all(
    importlib.import_module(f"app.messaging.{{name}}")
    is importlib.import_module(f"messaging.{{name}}")
    for name in ("models", "channels", "transport", "dispatcher", "trades")
)
print(json.dumps({{
    "same_registry": notifications._CHANNEL_REGISTRY is app.messaging._CHANNEL_REGISTRY,
    "same_facade_registry": app.compat.notifications._CHANNEL_REGISTRY is app.messaging._CHANNEL_REGISTRY,
    "same_submodules": submodules_same,
    "channel_count": len(notifications.registered_channels()),
}}))
"""
        )
        self.assertTrue(result["same_registry"])
        self.assertTrue(result["same_facade_registry"])
        self.assertTrue(result["same_submodules"])
        self.assertEqual(result["channel_count"], 5)

    def test_pure_domain_packages_support_both_import_styles(self):
        result = self.run_python(
            f"""
import json, sys
from datetime import datetime
from pathlib import Path
from app.automation.cron import cron_matches as app_cron_matches
from app.core.paths import get_dashboard_home as app_dashboard_home
from app.dashboard.practice_payload import downsample_sequence as app_downsample
from app.dashboard.apis.hot_stocks import select_hot_stock_ranking as app_select_ranking
from app.reports.a_share.calendar import cache_has_year as app_cache_has_year
from app.reports.a_share.common import normalize_code as app_normalize
from app.storage.history_records import stable_id as app_stable_id
from app.storage.market_reports import extract_decision_guidance as app_guidance
from app.trading.sell_signals import _sell_signal as app_sell_signal
sys.path[:0] = [{str(COMPAT)!r}, {str(APP)!r}]
from automation.cron import cron_matches
from core.paths import get_dashboard_home
from dashboard.practice_payload import downsample_sequence
from dashboard.apis.hot_stocks import select_hot_stock_ranking
from reports.a_share.calendar import cache_has_year
from reports.a_share.common import normalize_code
from storage.history_records import stable_id
from storage.market_reports import extract_decision_guidance
from trading.sell_signals import _sell_signal
root = Path('/tmp/niuone-module-test')
report = '**盘前指引**\\n· 风险级别：谨慎'
ranking = {{'amount_top': [{{'name': '成交额'}}], 'turnover_top': [{{'name': '换手率'}}]}}
print(json.dumps({{
    "app": [
        str(app_dashboard_home(root)), app_cron_matches('0 8 * * 1-5', datetime(2026, 7, 10, 8)),
        app_downsample(list(range(10)), 3), app_select_ranking(dict(ranking), 'turnover')['items'],
        app_normalize("sh600000"),
        app_cache_has_year({{'2026-07-10'}}, 2026), app_stable_id('x', 1),
        app_guidance(report), app_sell_signal("r", "s"),
    ],
    "legacy": [
        str(get_dashboard_home(root)), cron_matches('0 8 * * 1-5', datetime(2026, 7, 10, 8)),
        downsample_sequence(list(range(10)), 3), select_hot_stock_ranking(dict(ranking), 'turnover')['items'],
        normalize_code("sh600000"),
        cache_has_year({{'2026-07-10'}}, 2026), stable_id('x', 1),
        extract_decision_guidance(report), _sell_signal("r", "s"),
    ],
}}))
"""
        )
        self.assertEqual(result["app"], result["legacy"])

    def test_notifications_facade_loads_by_file_without_path_bootstrap(self):
        with tempfile.TemporaryDirectory() as cwd:
            source = f"""
import importlib.util, json
spec = importlib.util.spec_from_file_location("isolated_notifications", {str(COMPAT / 'notifications.py')!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(json.dumps({{"channels": list(module.registered_channels())}}))
"""
            output = subprocess.check_output(
                [sys.executable, "-c", source],
                cwd=cwd,
                env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
                text=True,
            )
        self.assertEqual(
            json.loads(output)["channels"],
            ["feishu", "dingtalk", "wecom", "telegram", "serverchan"],
        )

    def test_package_trade_notification_uses_runtime_dispatch_hook(self):
        result = self.run_python(
            """
import json
import app.messaging as messaging
calls = []
messaging.dispatch = lambda notification, env=None, **kwargs: calls.append(notification.event_type) or [
    messaging.DeliveryResult("hook", True, "")
]
result = messaging.notify_trade_executions([{"action": "BUY", "code": "600000"}], {})
print(json.dumps({"channels": [item.channel for item in result], "calls": calls}))
"""
        )
        self.assertEqual(result, {"channels": ["hook"], "calls": ["trade.executed"]})

    def test_concurrent_messaging_submodule_imports_share_registry(self):
        result = self.run_python(
            f"""
import importlib, json, sys, threading
sys.path[:0] = [{str(COMPAT)!r}, {str(APP)!r}]
barrier = threading.Barrier(2)
modules = {{}}
errors = []
def load(key, name):
    try:
        barrier.wait(timeout=3)
        modules[key] = importlib.import_module(name)
    except Exception as exc:
        errors.append(type(exc).__name__ + ":" + str(exc))
threads = [
    threading.Thread(target=load, args=("legacy", "messaging.dispatcher")),
    threading.Thread(target=load, args=("package", "app.messaging.dispatcher")),
]
for thread in threads: thread.start()
for thread in threads: thread.join()
print(json.dumps({{
    "errors": errors,
    "same_module": modules.get("legacy") is modules.get("package"),
    "same_registry": getattr(modules.get("legacy"), "_CHANNEL_REGISTRY", None)
        is getattr(modules.get("package"), "_CHANNEL_REGISTRY", None),
}}))
"""
        )
        self.assertEqual(result, {"errors": [], "same_module": True, "same_registry": True})

    def test_dashboard_reloads_sell_support_without_mutating_old_trader(self):
        result = self.run_python(
            f"""
import json, sys
sys.path[:0] = [{str(COMPAT)!r}, {str(APP)!r}]
import niuone_dashboard as dashboard
old_trader = dashboard.get_trader_module()
old_support = old_trader._sell_signals
dashboard.TRADER_SELL_SIGNALS_MTIME = -1
new_trader = dashboard.get_trader_module()
print(json.dumps({{
    "new_trader": new_trader is not old_trader,
    "new_support": new_trader._sell_signals is not old_support,
    "old_unchanged": old_trader._sell_signals is old_support,
    "published": sys.modules.get("trading.sell_signals") is new_trader._sell_signals,
}}))
"""
        )
        self.assertEqual(
            result,
            {
                "new_trader": True,
                "new_support": True,
                "old_unchanged": True,
                "published": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
