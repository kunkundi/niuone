#!/usr/bin/env python3
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = ROOT / "web" / "src"
VISIBLE_POLLING_PATH = WEB_SRC / "utils" / "visiblePolling.js"
PUBLIC_PROJECTION_PATH = WEB_SRC / "composables" / "usePublicProjection.js"
INDICES_DATA_PATH = WEB_SRC / "composables" / "useIndicesData.js"


class FrontendPerformanceTests(unittest.TestCase):
    def test_dashboard_brand_logo_uses_bundled_vite_asset(self):
        index_source = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        header_source = (
            WEB_SRC / "components" / "DashboardHeader.vue"
        ).read_text(encoding="utf-8")
        logo_path = WEB_SRC / "assets" / "niuone-logo.png"

        self.assertTrue(logo_path.is_file())
        self.assertIn('href="/src/assets/niuone-logo.png"', index_source)
        self.assertIn(
            "import niuoneLogoUrl from '../assets/niuone-logo.png'",
            header_source,
        )
        self.assertIn(':src="niuoneLogoUrl"', header_source)
        self.assertNotIn('href="/favicon.png"', index_source)
        self.assertNotIn('src="/favicon.png"', header_source)

    def test_dashboard_panels_are_loaded_on_demand(self):
        source = (WEB_SRC / "components" / "DashboardPage.vue").read_text(
            encoding="utf-8"
        )
        panel_names = (
            "DragonTigerPanel",
            "IndustryFlowPanel",
            "IndicesPanel",
            "MarketMonitorPanel",
            "OverviewPanel",
            "PracticePanel",
            "UsRatingsPanel",
            "XMonitorPanel",
        )
        for panel_name in panel_names:
            self.assertIn(
                f"const {panel_name} = defineAsyncComponent(() => import('./{panel_name}.vue'))",
                source,
            )
            self.assertNotIn(f"import {panel_name} from './{panel_name}.vue'", source)

    def test_periodic_dashboard_requests_pause_while_hidden(self):
        paths = (
            WEB_SRC / "composables" / "useIndicesData.js",
            WEB_SRC / "composables" / "useIndustryFlowData.js",
            WEB_SRC / "composables" / "useMarketMonitorData.js",
            WEB_SRC / "composables" / "useUsRatingsData.js",
            WEB_SRC / "composables" / "useXMonitorData.js",
            WEB_SRC / "components" / "DragonTigerPanel.vue",
        )
        for path in paths:
            source = path.read_text(encoding="utf-8")
            self.assertIn("startVisiblePolling", source, path.name)
            self.assertNotIn("setInterval", source, path.name)

    def test_public_projection_coordinates_visible_tabs(self):
        source = (
            WEB_SRC / "composables" / "usePublicProjection.js"
        ).read_text(encoding="utf-8")
        self.assertIn("window.BroadcastChannel", source)
        self.assertIn("window.navigator?.locks", source)
        self.assertIn("visibilitychange", source)
        self.assertIn("nextRefreshDelay", source)
        self.assertNotIn("setInterval", source)

    def test_public_projection_releases_the_tab_lock_after_last_subscriber(self):
        digest = "a" * 64
        scenario = f"""
const timers = new Map();
const listeners = new Map();
const snapshots = [];
let nextTimer = 1;
let fetchCalls = 0;
let lockRequests = 0;
let channelClosed = false;
class FakeChannel {{
  addEventListener() {{}}
  removeEventListener() {{}}
  postMessage() {{}}
  close() {{ channelClosed = true; }}
}}
globalThis.window = {{
  BroadcastChannel: FakeChannel,
  navigator: {{
    locks: {{
      request(_name, callback) {{
        lockRequests += 1;
        return callback();
      }},
    }},
  }},
  setTimeout(callback, delay) {{
    const id = nextTimer++;
    timers.set(id, {{callback, delay}});
    return id;
  }},
  clearTimeout(id) {{ timers.delete(id); }},
}};
globalThis.document = {{
  visibilityState: 'visible',
  addEventListener(name, callback) {{ listeners.set(name, callback); }},
  removeEventListener(name, callback) {{
    if (listeners.get(name) === callback) listeners.delete(name);
  }},
}};
globalThis.fetch = async url => {{
  fetchCalls += 1;
  if (url === '/api/v2/public/latest') return {{
    status: 200,
    ok: true,
    headers: {{get() {{ return ''; }}}},
    async json() {{ return {{revision: 1, manifest: 'manifests/1.json'}}; }},
  }};
  return {{
    status: 200,
    ok: true,
    headers: {{get() {{ return ''; }}}},
    async json() {{ return {{sections: {{practice: {{digest: '{digest}'}}}}}}; }},
  }};
}};
const {{ subscribePublicProjection }} = await import(
  {json.dumps(PUBLIC_PROJECTION_PATH.as_uri())} + '?lock-test=1'
);
const unsubscribe = subscribePublicProjection(value => snapshots.push(value));
for (let index = 0; index < 5 && snapshots.length === 0; index += 1) {{
  await new Promise(resolve => setImmediate(resolve));
}}
unsubscribe();
await new Promise(resolve => setImmediate(resolve));
console.log(JSON.stringify({{
  fetchCalls,
  lockRequests,
  snapshots: snapshots.length,
  revision: snapshots[0]?.revision,
  channelClosed,
  timers: timers.size,
  listenerRemoved: !listeners.has('visibilitychange'),
}}));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", scenario],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            json.loads(result.stdout),
            {
                "fetchCalls": 2,
                "lockRequests": 1,
                "snapshots": 1,
                "revision": 1,
                "channelClosed": True,
                "timers": 0,
                "listenerRemoved": True,
            },
        )

    def test_visible_polling_stops_and_resumes_with_page_visibility(self):
        scenario = f"""
const timers = new Map();
const listeners = new Map();
let nextTimer = 1;
globalThis.window = {{
  setTimeout(callback, delay) {{
    const id = nextTimer++;
    timers.set(id, {{callback, delay}});
    return id;
  }},
  clearTimeout(id) {{ timers.delete(id); }},
}};
globalThis.document = {{
  visibilityState: 'hidden',
  addEventListener(name, callback) {{ listeners.set(name, callback); }},
  removeEventListener(name, callback) {{
    if (listeners.get(name) === callback) listeners.delete(name);
  }},
}};
const {{ startVisiblePolling }} = await import({json.dumps(VISIBLE_POLLING_PATH.as_uri())});
let calls = 0;
const stop = startVisiblePolling(() => {{ calls += 1; }}, 1000, {{
  runImmediately: true,
  jitterRatio: 0,
}});
const hiddenTimers = timers.size;
document.visibilityState = 'visible';
listeners.get('visibilitychange')();
const [firstId, firstTimer] = [...timers.entries()][0];
timers.delete(firstId);
firstTimer.callback();
await new Promise(resolve => setImmediate(resolve));
const nextTimerState = [...timers.values()][0];
document.visibilityState = 'hidden';
listeners.get('visibilitychange')();
const hiddenAgainTimers = timers.size;
stop();
console.log(JSON.stringify({{
  hiddenTimers,
  firstDelay: firstTimer.delay,
  calls,
  nextDelay: nextTimerState.delay,
  hiddenAgainTimers,
  listenerRemoved: !listeners.has('visibilitychange'),
}}));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", scenario],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            json.loads(result.stdout),
            {
                "hiddenTimers": 0,
                "firstDelay": 0,
                "calls": 1,
                "nextDelay": 1000,
                "hiddenAgainTimers": 0,
                "listenerRemoved": True,
            },
        )

    def test_index_polling_does_not_abort_slow_auxiliary_market_requests(self):
        scenario = f"""
const pending = new Map();
const aborted = [];
let indicesFetches = 0;
let auxiliaryFetches = 0;
globalThis.CustomEvent = class {{
  constructor(name, options) {{ this.type = name; this.detail = options?.detail; }}
}};
globalThis.window = {{ dispatchEvent() {{}} }};
globalThis.document = {{
  visibilityState: 'visible',
  createElement() {{ return {{}}; }},
}};
function response(payload) {{
  return {{ ok: true, async json() {{ return payload; }} }};
}}
globalThis.fetch = (url, options = {{}}) => {{
  if (url === '/api/indices') {{
    indicesFetches += 1;
    return Promise.resolve(response({{items: [{{name: '上证指数'}}]}}));
  }}
  auxiliaryFetches += 1;
  return new Promise((resolve, reject) => {{
    const abort = () => {{
      aborted.push(url);
      const error = new Error('aborted');
      error.name = 'AbortError';
      reject(error);
    }};
    options.signal?.addEventListener('abort', abort, {{once: true}});
    pending.set(url, payload => resolve(response(payload)));
  }});
}};
const {{ useIndicesData }} = await import(
  {json.dumps(INDICES_DATA_PATH.as_uri())} + '?auxiliary-abort-test=1'
);
const api = useIndicesData();
const first = api.refreshIndices();
for (let index = 0; index < 5 && pending.size < 6; index += 1) {{
  await new Promise(resolve => setImmediate(resolve));
}}
const second = api.refreshIndices({{background: true}});
await new Promise(resolve => setImmediate(resolve));
const payloads = {{
  '/api/market_breadth': {{latest: {{}}, timeline: [{{generated_at: 'now'}}]}},
  '/api/sectors': {{sectors: [{{name: '银行'}}]}},
  '/api/us_sectors': {{items: [{{name: '科技'}}]}},
  '/api/hot_stocks': {{items: [{{name: '样本股'}}]}},
  '/api/market_flow': {{total_inflow_yi: 1}},
  '/api/money_flow': {{inflow: [{{name: '半导体'}}], outflow: []}},
}};
for (const [url, resolve] of pending.entries()) resolve(payloads[url]);
await Promise.all([first, second]);
await new Promise(resolve => setImmediate(resolve));
console.log(JSON.stringify({{
  aborted,
  indicesFetches,
  auxiliaryFetches,
  sector: api.state.sectors.sectors?.[0]?.name,
}}));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", scenario],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
        )
        self.assertEqual(
            json.loads(result.stdout),
            {
                "aborted": [],
                "indicesFetches": 2,
                "auxiliaryFetches": 6,
                "sector": "银行",
            },
        )

    def test_slow_auxiliary_request_does_not_block_other_refresh_intervals(self):
        scenario = f"""
let now = 0;
Date.now = () => now;
const counts = new Map();
globalThis.CustomEvent = class {{
  constructor(name, options) {{ this.type = name; this.detail = options?.detail; }}
}};
globalThis.window = {{ dispatchEvent() {{}} }};
globalThis.document = {{
  visibilityState: 'visible',
  createElement() {{ return {{}}; }},
}};
function response(payload) {{
  return {{ ok: true, async json() {{ return payload; }} }};
}}
globalThis.fetch = (url, options = {{}}) => {{
  counts.set(url, (counts.get(url) || 0) + 1);
  if (url === '/api/indices') {{
    return Promise.resolve(response({{items: [{{name: '上证指数'}}]}}));
  }}
  if (url === '/api/us_sectors') {{
    return new Promise((resolve, reject) => {{
      options.signal?.addEventListener('abort', () => {{
        const error = new Error('aborted');
        error.name = 'AbortError';
        reject(error);
      }}, {{once: true}});
    }});
  }}
  const payloads = {{
    '/api/market_breadth': {{latest: {{}}, timeline: [{{generated_at: 'now'}}]}},
    '/api/sectors': {{sectors: [{{name: '银行'}}]}},
    '/api/hot_stocks': {{items: [{{name: '样本股'}}]}},
    '/api/market_flow': {{total_inflow_yi: 1}},
    '/api/money_flow': {{inflow: [{{name: '半导体'}}], outflow: []}},
  }};
  return Promise.resolve(response(payloads[url]));
}};
const {{ useIndicesData }} = await import(
  {json.dumps(INDICES_DATA_PATH.as_uri())} + '?auxiliary-independent-test=1'
);
const api = useIndicesData();
await api.refreshIndices();
await new Promise(resolve => setImmediate(resolve));
now = 31_000;
await api.refreshIndices({{background: true}});
await new Promise(resolve => setImmediate(resolve));
api.deactivateIndices();
await new Promise(resolve => setImmediate(resolve));
console.log(JSON.stringify(Object.fromEntries(counts)));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", scenario],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
        )
        self.assertEqual(
            json.loads(result.stdout),
            {
                "/api/indices": 2,
                "/api/market_breadth": 2,
                "/api/sectors": 1,
                "/api/us_sectors": 1,
                "/api/hot_stocks": 1,
                "/api/market_flow": 2,
                "/api/money_flow": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
