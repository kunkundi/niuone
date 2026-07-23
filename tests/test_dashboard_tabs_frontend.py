#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_TABS_PATH = ROOT / "web" / "src" / "composables" / "useDashboardTabs.js"


class DashboardTabsFrontendTests(unittest.TestCase):
    def test_bootstrap_hydrates_lazy_panel_counts_before_panels_mount(self) -> None:
        scenario = f"""
globalThis.window = {{
  location: {{pathname: '/practice', search: ''}},
  setTimeout,
  clearTimeout,
}};
const fetchCalls = [];
globalThis.fetch = async url => {{
  fetchCalls.push(url);
  return {{
    ok: true,
    async json() {{
      return {{
        us_features_enabled: true,
        message_counts: {{
          market_monitor: 6,
          x_monitor: 108,
          us_ratings: 4,
        }},
      }};
    }},
  }};
}};
const module = await import(
  {json.dumps(DASHBOARD_TABS_PATH.as_uri())} + '?bootstrap-counts-test=1'
);
const tabs = module.useDashboardTabs();
const initialMarketCount = tabs.items.value
  .find(item => item.key === 'market_monitor')?.count;
await tabs.initializeDashboardTabs();
await tabs.initializeDashboardTabs();
const counts = Object.fromEntries(
  tabs.items.value.map(item => [item.key, item.count])
);
console.log(JSON.stringify({{fetchCalls, initialMarketCount, counts}}));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", scenario],
            cwd=ROOT / "web",
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(
            json.loads(result.stdout),
            {
                "fetchCalls": ["/api/dashboard/bootstrap"],
                "initialMarketCount": "",
                "counts": {
                    "practice": "",
                    "indices": "",
                    "market_monitor": " · 6",
                    "dragon_tiger": "",
                    "x_monitor": " · 108",
                    "us_ratings": " · 4",
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
