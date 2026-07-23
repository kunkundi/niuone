# Dashboard Incremental Delivery and Deployment

[简体中文](DASHBOARD_V2.md) | English

The Dashboard now runs on Vue 3 + Vite and FastAPI/Uvicorn while preserving the existing layout, sections, and interactions. The public page and password-protected `/admin` share one process and port. Performance work remains centered on server-side snapshots, conditional requests, lazy loading, and CDN caching; trading and market-data computation never move into the browser.

## Architecture

```text
Market data / models / schedules / simulated trading
                         │
                         ▼
      Single FastAPI/Uvicorn process (0.0.0.0:8787 by default)
        ├── Vue 3 public page and /admin
        ├── native incremental snapshot APIs
        ├── in-process legacy API adapter (no second port)
        └── background presentation projection (every 15s)
                         │
                         ▼
           $DASHBOARD_HOME/public-data/
             ├── latest.json
             ├── manifests/<revision>.json
             └── objects/<sha256>.json
```

Every 15 seconds the browser first checks `/api/v2/public/latest`. This pointer is normally smaller than 300 bytes and supports `ETag` and `304 Not Modified`. When the revision changes, the browser reads the manifest and calls the relevant compatibility API only for sections whose digest changed. The simulated-account first paint uses only the fast snapshot. Full history is requested only when the user opens a calendar date whose intraday data is missing; after success it is not downloaded again in that page session, and failures retry no more than once every five minutes.

`app/dashboard/public_projection.py` builds the allow-listed projection, and `app/dashboard/public_snapshots.py` publishes it atomically. Browser traffic does not execute trades, call models, or rebuild complete history.

Vue source lives under `web/`, and Vite writes the ignored production build to `web/dist/`. The macOS, Linux, and Windows launchers rebuild it with the locked pnpm dependencies when sources change or output is missing. Docker builds it in a separate Node stage, so Node is not included in the runtime image. Vue components now own theme switching, the compliance dialog, version status, category bootstrap and Vue Router, the last-refresh indicator, every public route, the complete simulated account, and the complete administrator page. The dragon-tiger component owns date-specific requests, the 60-second latest-data refresh, sorting, detail expansion, and category counts. Indices are split into a data composable, sparkline, grouped-index, and A-share/US-market components; the primary data refreshes every 15 seconds, money flow every 60 seconds, and the first index paint precedes parallel auxiliary ranking requests. Industry flow is also split into a 60-second data layer and an animation layer. It preserves real-sample interpolation, left/right inflow and outflow ranking, play, pause, replay, seeking, and speed controls while cancelling requests and animation frames when the component is left. Market monitoring is split into data-cache, trading-day pager, report-card, structured-detail, and overnight-US-summary components. It loads up to 200 history records once, then polls only `/api/messages/revision` for the count and latest-record fingerprint every 15 seconds; full history is fetched again only when that fingerprint changes, while the overnight summary refreshes independently every five minutes. US institutional ratings are split into a data cache, date pager, rating table, and expandable-detail component. They load up to 120 history records once, then check only the lightweight revision summary every ten minutes and reload history only after a revision change. Live quotes load for the selected date, while company sector and industry profiles load only when a stock row is expanded. X monitoring is split into a paged data cache, current-page fingerprint checks, adjacent-page prefetch, post rows, thread details, media galleries, and an image viewer. It retains ten records per page and a five-minute session cache, polls only the lightweight page hash every 15 seconds, and reloads the page only when a record, repaired context, or media metadata changes. Pending images are cancelled before navigation or component teardown. Simulated trading is split into an account-data composable, account overview, open/sold position cards, return chart, trading calendar, operation log, rule, market summary, and candidate components. Account and candidate data share one public-projection subscription and reload only after the relevant section digest changes. Production pages no longer load the old Dashboard controller; category bootstrap, deep links, and navigation are owned by Vue Router. Administrator coverage includes login, the settings index, client-side routing, every settings group, save and dirty-state behavior, leave confirmation, notification-channel add/remove and activation, and iWencai/model/notification connection tests. FastAPI directly handles version/bootstrap, messages and their lightweight revision projection, dragon-tiger data, the X media proxy, simulated-trading read models and protected refresh/resume/market-summary actions, status reports, self-optimization apply, indices, sectors, hot stocks, industry flow, US-market auxiliary data, and benchmarks. It also owns administrator login, configuration reads and writes, and the iWencai, model, and notification connection tests. All browser APIs are explicitly declared by FastAPI. Unknown paths return 404 instead of entering the old `BaseHTTPRequestHandler`, and the Vue administrator page does not load `frontend/admin.js`.

The indices page adds a dedicated **Market Sentiment** switch immediately after **Fund Flow**. This view presents 60-second real A-share samples: the upper dual-axis panel displays limit-ups, limit-downs, broken limit-ups, red stocks, and green stocks, while a lower shared-time-axis turnover panel compares projected full-day turnover, actual cumulative turnover, and the signed increment of the projection versus the previous trading day's full-day turnover, in CNY 100 million. Actual turnover primarily uses Eastmoney one-minute Shanghai/Shenzhen index turnover with Tencent full-market turnover as fallback. The projection divides current turnover by the median same-time cumulative share across the latest 20 complete Eastmoney five-minute trading-day profiles. The API exposes the selected actual source, model, sample range, and sample count, and the turnover panel displays that methodology. The FastAPI-native endpoint is `/api/market_breadth`, and the deep link is `/indices?panel=market-breadth`.

Vue industry-flow requests use the `compact=1` field projection so only animation-required sample nodes cross the network.

## Administrator Security

`/admin` may share the public domain. The page itself can open. FastAPI-native login, configuration reads and writes, and iWencai/model/notification connection tests require the appropriate administrator credential or cookie. Mutating and test requests also require `X-NiuOne-Action`; they remain protected by body-size and administrator rate limits, plus dedicated limits for each connection-test class. Use a strong administrator password and HTTPS from Cloudflare or the reverse proxy through to the origin.

## Run

```bash
./run.sh
```

For development, Vite hot reload and FastAPI may run separately. Port `5173` is development-only; production still exposes only `8787`:

```bash
pnpm --dir web install --frozen-lockfile
pnpm --dir web dev
./run-dashboard.sh
```

Open:

- Public page: <http://127.0.0.1:8787/>
- Administrator page: <http://127.0.0.1:8787/admin>

| Setting | Default | Effective |
|---|---:|---|
| `DASHBOARD_PUBLIC_PROJECTION_ENABLED` | `1` | restart |
| `DASHBOARD_PUBLIC_REFRESH_SECONDS` | `15` | restart |
| `DASHBOARD_PUBLIC_DATA_DIR` | `$DASHBOARD_HOME/public-data` | restart |

## Public Caching

Cloudflare Tunnel, Nginx, or Caddy needs to proxy only port `8787`. Recommended policies:

- `/assets/*`: cache content-hashed Vue/Vite assets for one year;
- `/api/v2/public/objects/*` and `/api/v2/public/manifests/*`: one year, immutable;
- `/api/v2/public/latest`: five-second edge caching with 30-second stale-while-revalidate;
- `/admin*`, `/api/admin/*`, and mutating requests: never cache;
- other APIs: honor the server's `Cache-Control`; do not force one public cache rule across the site.

Production HTTP endpoints are split into system, messages, market, practice, and admin routers under `dashboard/routers/`. The old `BaseHTTPRequestHandler`, `ThreadingHTTPServer`, native HTML files, and Dashboard/Admin JavaScript controllers have been deleted.

If the home uplink or Tunnel path remains the bottleneck, run the same single-port service on a cloud host near users and let the CDN cache static assets and incremental snapshots. Migration and rollback change only the runtime location and domain origin; account, trade, and message history are not rebuilt.
