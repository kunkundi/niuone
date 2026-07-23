# Deployment, Validation, and Rollback Manual

[简体中文](OPERATIONS.md) | English

This document records NiuOne's local operation, validation, deployment, log inspection, and rollback procedures. Real runtime data is stored centrally in `.local-data/`, which is not tracked by Git.

## 1. Directory Conventions

```text
/path/to/NiuOne/
├── app/                    # Local service and task source code
├── tests/                  # Unit tests
├── scripts/                # Validation, deployment, and task scripts
├── docs/                   # Documentation
├── config/                 # Runtime strategy documentation
├── .local-data/            # Real local runtime data, ignored by Git
├── run.sh                  # One-click startup for macOS/Linux
├── run.bat                 # One-click Windows BAT startup
├── run-dashboard.sh        # Web service entry point
├── run-niuone-cron-scheduler.sh
└── run-x-watchlist-daemon.sh
```

Runtime data is stored by default in:

```text
.local-data/
├── dashboard.env
├── .venv/
├── runtime/
│   ├── dashboard_users.db
│   ├── dashboard_admin_token.txt
│   ├── push_history.db
│   ├── niuniu.db
│   ├── config.yaml
│   ├── cron/state/
│   ├── cron/output/
│   └── logs/
└── backups/
```

Do not commit databases, local credentials, logs, model configuration, or archived content from `.local-data/` to Git, and do not copy them into public contexts.

## 2. Pre-Run Checks

One-click startup:

```bash
./run.sh
```

The dashboard home page and displayed data remain publicly accessible, while the settings page and administrative APIs always require administrator authentication. If `DASHBOARD_ADMIN_PASSWORD` is configured, use that password; otherwise, use the bootstrap administrator key generated automatically by the service. The local key is stored at `$DASHBOARD_HOME/dashboard_admin_token.txt` (default: `.local-data/runtime/dashboard_admin_token.txt`), and the Docker key is stored at `/data/runtime/dashboard_admin_token.txt`.

On the first startup, read the bootstrap administrator key from `$DASHBOARD_HOME/dashboard_admin_token.txt` and use it to enter the settings page, then set an administrator password under “Access Control.” The new password takes effect immediately and invalidates existing sessions. Alternatively, before startup, edit `.local-data/dashboard.env`, whose permissions are `0600`, and set `DASHBOARD_ADMIN_PASSWORD` directly. Do not pass passwords through command-line arguments, where they may be recorded in shell history or process lists.

To specify the dashboard port:

```bash
./run.sh --port 8877
```

On Windows, use `run.bat --port 8877`.

The first run creates `.local-data/.venv`, installs dependencies, generates `.local-data/dashboard.env`, and then starts:

```text
http://127.0.0.1:8787/
```

The administrator password is saved to `.local-data/dashboard.env`. Treat both the password and the bootstrap administrator key as sensitive credentials; do not commit them or copy them into public contexts.

Public deployments continue to run `./run-dashboard.sh`: FastAPI/Uvicorn serves the Vue public page, password-protected `/admin`, and every API on port `8787`, with no second production port. The server publishes content-addressed snapshots every 15 seconds; the browser checks a lightweight version pointer and fetches data only for changed sections. See [Dashboard Incremental Delivery and Deployment](DASHBOARD_V2_EN.md) for caching and reverse-proxy guidance.

## 3. Model Configuration

NiuOne requires a large language model to run the complete workflow. Grok is recommended for X watchlist monitoring and the daily U.S. institutional ratings report. Enhanced A-share market summaries can use any model compatible with `/chat/completions`. The news pre-check for A-share candidates can be configured separately with a model that supports real-time search. Trading decisions after stock selection can use a compatible model, with DeepSeek recommended.

Core configuration items:

| Scenario | Configuration items |
|---|---|
| Master switch for NiuNiu U.S. Stocks | `DASHBOARD_US_FEATURES_ENABLED` |
| Grok API | `DASHBOARD_GROK_BASE_URL`, `DASHBOARD_GROK_API_KEY`, `DASHBOARD_GROK_MODEL`, `DASHBOARD_GROK_API_MODE`, `DASHBOARD_GROK_CONTEXT_LENGTH` |
| Separate override for A-share market model summaries | `A_SHARE_MODEL_SUMMARY_BASE_URL`, `A_SHARE_MODEL_SUMMARY_API_KEY`, `A_SHARE_MODEL_SUMMARY_MODEL`, `A_SHARE_MODEL_SUMMARY_MAX_TOKENS` |
| News pre-check API | `DASHBOARD_NEWS_BASE_URL`, `DASHBOARD_NEWS_API_KEY`, `DASHBOARD_NEWS_MODEL`, `DASHBOARD_NEWS_API_MODE`, `DASHBOARD_NEWS_MAX_TOKENS`, `DASHBOARD_NEWS_CONCURRENCY` |
| Built-in iWencai data source | `IWENCAI_ENABLED`, `IWENCAI_BASE_URL`, `IWENCAI_API_KEY`, `IWENCAI_TIMEOUT_SECONDS`, `IWENCAI_MAX_RETRIES`, `IWENCAI_MAX_CONCURRENCY`, `IWENCAI_CACHE_TTL_SECONDS`, `IWENCAI_DRAGON_TIGER_CRON` |
| Trading-decision API | `DASHBOARD_DECISION_BASE_URL`, `DASHBOARD_DECISION_API_KEY`, `DASHBOARD_DECISION_MODEL` |
| Trading-decision intelligence bundle | `DASHBOARD_DECISION_INTELLIGENCE_ENABLED`, `DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS`, `DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` |
| Trading discipline for trading decisions | `DASHBOARD_TRADE_DISCIPLINE_TEXT`; when empty, the built-in default discipline is used; when populated, its content is inserted into the “Mandatory Rules” section of the model prompt |
| Simulated-account cadence and position-sizing references | `DASHBOARD_MAX_OPEN_POSITIONS`, `DASHBOARD_MAX_NEW_BUYS_PER_DECISION`, `DASHBOARD_MAX_SINGLE_POSITION_PCT`, `DASHBOARD_MAX_TOTAL_POSITION_PCT`, `DASHBOARD_MIN_CASH_RESERVE_PCT`; these are model references by default, while suites with registered hard limits, including Z-ge and Sector Tide, enforce the stricter global or suite limit in the simulation layer |
| Separate override for U.S. stock ratings | `US_RATING_BASE_URL`, `US_RATING_API_KEY`, `US_RATING_MODEL`, `US_RATING_MAX_TOKENS` |
| Separate override for the X watchlist | `X_WATCHLIST_BASE_URL`, `X_WATCHLIST_API_KEY`, `X_WATCHLIST_MODEL`, `X_WATCHLIST_MAX_TOKENS` |

After administrator authentication, preferably use the settings button on the page to open the settings page and manage these values. Every section that requires a model and API key includes a **Test Model Connection** button. The test uses the current form values without saving them; leaving the API key input empty reuses the saved secret. Tweet monitoring and U.S. ratings settings are controlled by the “Enable NiuNiu U.S. Stocks” switch. When disabled, the settings page hides these items, and the background X monitoring and U.S. ratings scheduled tasks are skipped. You can also edit `.local-data/dashboard.env` directly; after saving, restart the affected components as appropriate, or wait for the next task cycle to pick up the changes.
`DASHBOARD_GROK_API_MODE` accepts `auto`, `responses`, or `chat`. The default `auto` mode uses the Responses API with `web_search`/`x_search` tools for Grok 4.5 and keeps Chat Completions for other models; compatible gateways can force either mode. `X_WATCHLIST_REQUEST_TIMEOUT_SECONDS` controls the per-account X request timeout and defaults to `45` seconds.
`DASHBOARD_NEWS_API_MODE` also accepts `auto`, `responses`, or `chat`. The default `auto` mode uses the Responses API with the `web_search` tool for Grok 4.5 and GPT-5 search models, while other models remain on Chat Completions; either mode can be forced for a gateway.
`*_CONTEXT_LENGTH` represents only the model context window and defaults to `128000`; `*_MAX_TOKENS` is the desired maximum output length and is mapped to `max_tokens` or `max_output_tokens` for the selected API. Known GPT-5.6 gateway aliases that reject the Responses output-limit parameter omit it, and other gateways receive one guarded retry without it when they explicitly report the parameter as unsupported. Both JSON and SSE responses are accepted, including gateways that force SSE when `stream=false`.
The news pre-check examines at most five candidate stocks concurrently by default. If the upstream service returns rate limits or 403/429 responses, reduce `DASHBOARD_NEWS_CONCURRENCY` to `2` or `1`.

The iWencai data source is disabled by default. The **iWencai Data Source** settings include **Test iWencai Connection**, which sends one lightweight read-only query using the current form values without saving settings or modifying dragon-tiger snapshots. After enabling it and configuring an API key, the Dashboard exposes the purpose-built
`/api/iwencai/dragon-tiger?date=YYYY-MM-DD&page=1&limit=100` endpoint. It does not proxy arbitrary natural-language queries,
caps each page at 100 stocks, and uses the Dashboard's existing rate limits and cache. Results are deduplicated by stock code, `sector` contains the industry, and duplicate leaderboard entries remain available under `details`. `seats` retains the top-five buy/sell institution, brokerage, and explicitly tagged hot-money/quant seats, including separate `buy_rank` and `sell_rank` when one broker appears on both sides; `institution_seats` remains as a compatibility subset. A seat-detail failure does not block the main list or replace valid seat rows already archived for the same trading day. iWencai responses are research snapshots; timeouts, count mismatches, and upstream failures return explicit status without overwriting account, fill, or other real trading records.
The `/dragon-tiger` Dashboard section can switch by trading date and prefers the exact dated archive. By default, Cron refreshes
`.local-data/runtime/cron/output/iwencai_dragon_tiger_latest.json` at 18:00 China time on A-share trading days and also writes `.local-data/runtime/cron/output/iwencai_dragon_tiger/YYYY-MM-DD.json`. Empty or failed main-list responses replace neither the last valid snapshot nor dated archives.

The trading-decision intelligence bundle is enabled by default. Each model decision after a stock-selection scan on the Practice page reads market monitoring, overnight U.S. market data, index quotes, sector performance, industry fund flows, trending stocks, candidate news, and an account-position summary, then writes the compressed `decision_intelligence` into the simulated-trading decision log. If a market-data source fails, its `source_status` is retained, and the current decision continues with available information and existing risk controls.

The canonical URL for the Practice page is `/?category=practice`. The candidate query and refresh endpoints are `/api/practice_candidates` and `/api/practice_candidates/refresh`, respectively. The old `category=b1_screen` and `/api/b1_screen` paths are retained only as compatibility entry points.

### 3.1 Market Data and Fund-Flow Settings

The **Market Data and Fund-Flow Settings** page groups index refresh and industry fund-flow controls:

| Setting | Default | Allowed range | Application |
|---|---:|---:|---|
| `DASHBOARD_INDICES_TTL_SECONDS` | `60` | Greater than 0 seconds | Hot-applied |
| `DASHBOARD_INDUSTRY_FLOW_PLAYBACK_SPEED` | `0.5` | `0.5`, `0.75`, `1`, `1.5`, or `2` | Hot-applied; used on the next fund-flow page load |
| `DASHBOARD_INDUSTRY_FLOW_SIDE_LIMIT` | `10` | `1`–`10` industries per side | Hot-applied; used by the next fund-flow request |
| `DASHBOARD_INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS` | `60` | `60`–`600` seconds | Hot-applied; used by the next sampler cycle |
| `DASHBOARD_INDUSTRY_FLOW_MORNING_START` | `09:25` | China-time `HH:MM` | Hot-applied; used by the next sampler check |
| `DASHBOARD_INDUSTRY_FLOW_MORNING_END` | `11:31` | China-time `HH:MM` | Hot-applied; used by the next sampler check |
| `DASHBOARD_INDUSTRY_FLOW_AFTERNOON_START` | `13:00` | China-time `HH:MM` | Hot-applied; used by the next sampler check |
| `DASHBOARD_INDUSTRY_FLOW_AFTERNOON_END` | `15:01` | China-time `HH:MM` | Hot-applied; used by the next sampler check |

By default, industry fund flow is sampled only on A-share trading days during 09:25–11:31 and 13:00–15:01 China time. All four boundaries can be edited on the settings page and must satisfy morning start < morning end < afternoon start < afternoon end. Changing the window or interval does not delete stored real samples; points outside the active window are excluded from playback, and new samples follow the updated window and minimum spacing.

The **Main Fund Flow** ranking on the indices page and the fund-flow animation share Eastmoney's industry-board **Today Main Net Amount** metric (`f62`, converted from yuan to CNY 100 million) and the same 60-second cache. New snapshots and samples are stored in `industry_main_money_flow_cache.json` and `industry_main_flow_history.json`, respectively. Legacy files based on total inflow minus total outflow are retained but are never mixed into main-net playback.

The A-share market-sentiment chart on the indices page reads one Tencent Shanghai/Shenzhen full-market snapshot every 60 seconds. It uses the returned current, high, upper-limit, and lower-limit prices to count sealed limit-ups, sealed limit-downs, and broken limit-ups; positive and negative quote changes produce the red and green counts. Actual turnover in the lower panel primarily comes from the sum of Eastmoney one-minute turnover for the Shanghai Composite and Shenzhen Component. If that request fails or is stale, the service falls back to cumulative turnover from the Tencent full-market quote batch and exposes the selected source in the API and UI. Projected full-day turnover no longer uses elapsed-minute linear extrapolation. The service reads Eastmoney five-minute index bars, selects the latest 20 complete common Shanghai/Shenzhen trading days before today, calculates each day's cumulative share at the current trading progress, and divides current turnover by the median of those 20 shares. During the first five minutes it holds the historical first complete five-minute-bucket share to bound opening-auction effects; lunch is aligned on continuous trading progress and the close share is 100%. The profile is cached by trading day and a failed load retries after five minutes. Until 20 complete days are available, actual turnover remains visible but no linear substitute projection is generated. Increment is the signed difference between projected full-day turnover and the last complete day in that same profile. All three series use CNY 100 million. The universe includes ST stocks and excludes B shares, Beijing Stock Exchange listings, and securities without a valid current price. The background sampler runs only on A-share trading days during 09:30–11:30 and 13:00–15:00 China time and stores real observations in `market_breadth_history.json`. Legacy observations without turnover or increment remain intact and render as gaps rather than synthetic zeroes. An incomplete Tencent batch, insufficient turnover coverage, or failed request retains the previous valid history.

Industry fund-flow snapshots, fund-flow samples, and market-sentiment samples retain only the current Beijing calendar day. The Dashboard validates file dates at startup, and a resident background task atomically clears `industry_main_money_flow_cache.json`, `industry_main_flow_history.json`, and `market_breadth_history.json` every day at 00:00 Asia/Shanghai while invalidating the related in-memory API caches. If an upstream source still reports the previous day's timestamp after midnight, the server rejects that snapshot instead of displaying or persisting it; the page remains empty until the first valid current-day sample arrives.

### 3.2 Practice-Strategy Scheduling and Process Ownership

Individual practice strategies do not own separate candidate-scan timers. At every configured time, the B1 scheduler inside the Dashboard starts the shared scanner. The scanner reads `DASHBOARD_ACTIVE_STRATEGY` and runs only the scorers in that active suite. After a successful scan, the scheduled path synchronously runs the model assessment and simulated execution-layer checks.

| Setting | Default | Scope | Application |
|---|---|---|---|
| `DASHBOARD_ACTIVE_STRATEGY` | `zettaranc` | New candidates, model prompt, and entry rules | Hot-applied; used by the next scan |
| `DASHBOARD_B1_SCHEDULE_ENABLED` | `1` | Starts the Dashboard's built-in candidate scheduler | Dashboard restart required |
| `DASHBOARD_B1_SCHEDULE_TIMES` | `09:25,10:00,10:30,11:00,11:20,13:00,13:30,14:00,14:30,14:50` | Candidate-scan and trading-decision times | Hot-applied |
| `DASHBOARD_B1_SCHEDULE_CATCHUP_MINUTES` | `35` | Catch-up window after brief Dashboard downtime | Dashboard restart required |
| `DASHBOARD_B3_EXIT_TIME` | `09:37` | Opening automatic-exit check | Read by a subsequent Cron cycle |
| `DASHBOARD_TIME_EXIT_TIME` | `14:45` | End-of-day automatic exits and time-box checks | Read by a subsequent Cron cycle |

The 09:25 scan falls in the quiet period after the opening auction. The system may generate candidates and model actions, but it does not book a fill at the auction reference price. Executable actions are queued, and after 09:30 the Dashboard's deferred-decision worker rechecks the session, current price, cash, and strategy risk budgets.

Users can click **Manually trigger candidate scan and trading strategy** on the Practice page to run the complete flow. It uses the same scanner, active-strategy setting, and execution layer as the scheduled path; it is not a force-fill or risk-bypass endpoint. A normal page refresh only reads cached and account state.

Every scheduled or manual B1 decision refreshes all open positions first and evaluates each position under the original exit rules identified by its stored `strategy_mark`; the active suite controls new candidates and BUYs only. SELL/HOLD checks continue when the candidate list is empty or the daily loss budget has fired, and that budget pauses new entries only.

Local automatic exits are also invoked by the separate Cron Scheduler process at dedicated times. Structural stops, Sector Tide deterioration, strategy time boxes, 2R, and 2 ATR remain discrete checks rather than tick-by-tick monitoring. Both the Dashboard and Cron Scheduler processes must be running for the full lifecycle.

When a strategy appears not to trigger, check in this order:

1. Confirm that `DASHBOARD_ACTIVE_STRATEGY` in `.local-data/dashboard.env` names the expected suite.
2. Confirm that `DASHBOARD_B1_SCHEDULE_ENABLED` is enabled and the Dashboard process is still running.
3. Confirm that the current time is at a `DASHBOARD_B1_SCHEDULE_TIMES` slot or within the catch-up window.
4. Inspect `.local-data/runtime/cron/state/b1_schedule_state.json` for an `ok`, `error`, or `skipped` status for the slot.
5. Inspect `.local-data/runtime/cron/output/multi_strategy_latest.json` for a recent `generated_at`, the active suite's candidates, and required context fields.
6. If automatic exits did not run, inspect the Cron Scheduler process and `.local-data/runtime/logs/niuone_cron_scheduler.log`.

See the [Strategy Research Guide](strategies/README_EN.md#34-sector-tide) for Sector Tide user rules, risk budgets, and the developer data contract.

## 4. Validation Procedure

```bash
./scripts/validate.sh
```

The validation covers:

1. Python syntax checks
2. Vue/Vite production build and frontend JavaScript syntax checks
3. Syntax checks for Shell startup scripts
4. Windows BAT entry-point checks
5. Unit tests under `tests/`

Validate an isolated instance:

```bash
DASHBOARD_HOME=/tmp/niuone-smoke DASHBOARD_PORT=8878 ./scripts/run_standalone.sh
```

Health checks:

```bash
curl -s -o /dev/null -w 'HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8878/
curl -s -o /dev/null -w 'HTTP:%{http_code} TOTAL:%{time_total}\n' 'http://127.0.0.1:8878/api/messages?limit=1'
```

Both are expected to return `HTTP:200`.

## 5. Long-Term Local Operation

Register and start the long-running services for the current platform through the one-click startup entry point:

```bash
./run.sh --service
```

Windows:

```cmd
run.bat --service
```

Check status or restart on macOS / Linux:

```bash
./scripts/manage-long-running.sh status
./scripts/manage-long-running.sh restart
```

Windows PowerShell:

```powershell
powershell -File .\scripts\manage-long-running.ps1 -Action Status
powershell -File .\scripts\manage-long-running.ps1 -Action Restart
```

macOS uses LaunchAgent, Linux uses user-level systemd, and Windows uses Task Scheduler. For installation locations, unattended operation, logs, and uninstallation instructions, see the [Standalone Operation Guide](STANDALONE_EN.md).

## 6. Deployment Procedure

For Docker Hub image builds, version tags, and push procedures, see [Container Image Release Process](CONTAINER_RELEASE_EN.md).

Local deployment script:

```bash
cd /path/to/NiuOne
./scripts/deploy_to_live.sh
```

The script:

- Runs `./scripts/validate.sh` first
- Backs up the current `app/`, local environment file, and `run-dashboard.sh` to `.local-data/backups/`
- Ensures that the runtime directory exists
- Sends `HUP` to the current service process at `127.0.0.1:8787`
- Performs a smoke check by visiting `/`

If the service is managed in long-running mode, the platform service manager normally starts a new process after `HUP`. If no service manager is present, manually run `./run.sh` or the corresponding startup script again.

Post-deployment checks:

```bash
curl -s -o /dev/null -w 'HOME HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8787/
curl -s "http://127.0.0.1:8787/api/messages?limit=1" | python3 -m json.tool | head
```

The `db_path` in the `/api/messages` response should point to `.local-data/runtime/push_history.db` inside the project directory.

## 7. Log and Task Checks

Common log directory:

```text
.local-data/runtime/logs/
```

Common state and output directories:

```text
.local-data/runtime/cron/state/
.local-data/runtime/cron/output/
```

Task scripts:

```bash
./run-niuone-cron-scheduler.sh
./run-x-watchlist-daemon.sh
./scripts/run_us_rating_report.sh
```

Manage X watchlist authors under “Tweet Monitoring Authors” on the settings page. Enter handles without `@`.

## 8. Rollback

Deployment backups are stored by default in:

```text
.local-data/backups/
```

Example of manually rolling back `app/`:

```bash
cp -R .local-data/backups/<backup-name>/app/. app/
./scripts/validate.sh
launchctl kickstart -k gui/$(id -u)/ai.niuone.dashboard
```

To roll back a Git commit, prefer non-destructive commands:

```bash
git revert <commit-sha>
./scripts/validate.sh
git push origin main
```

Check after rollback:

```bash
curl -s -o /dev/null -w 'HTTP:%{http_code}\n' http://127.0.0.1:8787/
```

## 9. Frequently Asked Questions

### The Page Does Not Start

Check with:

```bash
./run.sh --no-browser
```

Confirm that Python is available, dependencies were installed successfully, and the port is not in use.

### The Page Opens but Has No Historical Messages

Check the message database:

```bash
ls -lh .local-data/runtime/push_history.db
curl -s "http://127.0.0.1:8787/api/messages?limit=5" | python3 -m json.tool | head
```

The current message stream primarily uses `push_history.db`. Corresponding messages appear on the page only after the task scripts successfully write them to this database.

New market-monitoring, X-monitoring, and U.S. institutional-ratings records are written only to this database; Markdown files are no longer generated. Existing historical `.md` files from before the upgrade are preserved unchanged, but the page does not read or automatically delete them.

### Tasks Do Not Update Automatically

Check these three areas:

```bash
launchctl print gui/$(id -u)/ai.niuone.cron-scheduler | sed -n '1,100p'
launchctl print gui/$(id -u)/ai.niuone.x-watchlist | sed -n '1,100p'
tail -n 200 .local-data/runtime/logs/*.log
```

Also confirm that model keys, task schedules, and monitored tweet authors have been configured.

### The Page Is Blank After Frontend Changes

Run:

```bash
./scripts/validate.sh
```

This builds the `web/` Vue application and checks migration `frontend/` JavaScript, `app/` Python, Shell/PowerShell entrypoints, and the complete unit-test suite.

### Do Not Commit Real Data

Check before committing:

```bash
git status --ignored --short
```

`.local-data/` should be shown as ignored and must not appear among staged files.

## 10. Maintenance Principles

1. Run `./scripts/validate.sh` after changing source code.
2. Use an independent `DASHBOARD_HOME=/tmp/...` and a port other than 8787 for temporary tests.
3. Keep the dashboard publicly accessible, while always requiring administrator authentication for the settings page and administrative APIs.
4. Keep real databases, local credentials, logs, and model configuration only in `.local-data/`.
5. New message-producing tasks should write directly to `push_history.db` instead of generating separate historical Markdown files.
