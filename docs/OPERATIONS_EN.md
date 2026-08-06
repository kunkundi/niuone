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

`/healthz` reports only that the web process is alive and is suitable for container liveness. `/readyz` also checks that runtime storage is writable and that market data required by the active strategy is ready; it returns `503` during first-start initialization and `200` afterward. `/api/system/data-readiness` always returns `200` with the same structured diagnosis for UI progress, cache coverage, persistent-volume, and timezone notices. Do not use `/readyz` as a liveness probe that restarts the container during initialization.

The final **About** settings group shows the project author, GitHub repository, Apache License 2.0, current version, and newest Docker Hub release, with a **Check for updates** button that bypasses the server cache and refreshes the upstream result. **Automatically check for new versions** is enabled by default and takes effect at runtime; set `DASHBOARD_AUTO_VERSION_CHECK_ENABLED=0` in `dashboard.env` to disable it. “Do not remind me about this version” is stored only in the current browser; manually clicking the home-page version still checks again, and a later release can trigger a new reminder.

## 3. Model Configuration

NiuOne requires a large language model to run the complete workflow. X watchlist monitoring uses Grok with `x_search` support. The daily U.S. institutional ratings report can use a dedicated model with real-time web search and reuses Grok when those settings are blank. Enhanced A-share market summaries can use any model compatible with `/chat/completions`. A-share candidates plus dragon-tiger limit-up-streak or consecutive-listing stocks use the separately configured news-precheck model with real-time search support. Trading decisions after stock selection can use a compatible model, with DeepSeek recommended.

Core configuration items:

| Scenario | Configuration items |
|---|---|
| Master switch for NiuNiu U.S. Stocks | `DASHBOARD_US_FEATURES_ENABLED` |
| Independent X watchlist switch | `X_WATCHLIST_ENABLED`; disabling it does not disable the U.S. ratings report |
| Grok API | `DASHBOARD_GROK_BASE_URL`, `DASHBOARD_GROK_API_KEY`, `DASHBOARD_GROK_MODEL`, `DASHBOARD_GROK_API_MODE`, `DASHBOARD_GROK_CONTEXT_LENGTH` |
| Separate override for A-share market model summaries | `A_SHARE_MODEL_SUMMARY_BASE_URL`, `A_SHARE_MODEL_SUMMARY_API_KEY`, `A_SHARE_MODEL_SUMMARY_MODEL`, `A_SHARE_MODEL_SUMMARY_MAX_TOKENS` |
| News pre-check API | `DASHBOARD_NEWS_BASE_URL`, `DASHBOARD_NEWS_API_KEY`, `DASHBOARD_NEWS_MODEL`, `DASHBOARD_NEWS_API_MODE`, `DASHBOARD_NEWS_MAX_TOKENS`, `DASHBOARD_NEWS_CONCURRENCY` |
| Built-in iWencai data source | `IWENCAI_ENABLED`, `IWENCAI_BASE_URL`, `IWENCAI_API_KEY`, `IWENCAI_TIMEOUT_SECONDS`, `IWENCAI_MAX_RETRIES`, `IWENCAI_MAX_CONCURRENCY`, `IWENCAI_CACHE_TTL_SECONDS`, `IWENCAI_DRAGON_TIGER_CRON` |
| Trading-decision API | `DASHBOARD_DECISION_BASE_URL`, `DASHBOARD_DECISION_API_KEY`, `DASHBOARD_DECISION_MODEL` |
| Trading-decision intelligence bundle | `DASHBOARD_DECISION_INTELLIGENCE_ENABLED`, `DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS`, `DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` |
| Trading discipline for trading decisions | `DASHBOARD_TRADE_DISCIPLINE_TEXT`; when empty, the built-in default discipline is used; when populated, its content is inserted into the “Mandatory Rules” section of the model prompt |
| Simulated-account cadence and position-sizing references | `DASHBOARD_MAX_OPEN_POSITIONS`, `DASHBOARD_MAX_NEW_BUYS_PER_DECISION`, `DASHBOARD_MAX_SINGLE_POSITION_PCT`, `DASHBOARD_MAX_TOTAL_POSITION_PCT`, `DASHBOARD_MIN_CASH_RESERVE_PCT`; these are model references by default, while suites with registered hard limits, including Z-ge and Sector Tide, enforce the stricter global or suite limit in the simulation layer |
| Separate override for U.S. stock ratings | `US_RATING_MODEL`, `US_RATING_BASE_URL`, `US_RATING_API_KEY`, `US_RATING_MAX_TOKENS` |
| Separate override for the X watchlist | `X_WATCHLIST_BASE_URL`, `X_WATCHLIST_API_KEY`, `X_WATCHLIST_MODEL`, `X_WATCHLIST_MAX_TOKENS` |

After administrator authentication, preferably use the settings button on the page to open the settings page and manage these values. Every section that requires a model and API key includes a **Test Model Connection** button. The test uses the current form values without saving them; leaving the API key input empty reuses the saved secret. Tweet monitoring and U.S. ratings settings are controlled by the “Enable NiuNiu U.S. Stocks” master switch. When disabled, the settings page hides these items, and the background X monitoring and U.S. ratings scheduled tasks are skipped. To stop tweet queries while retaining U.S. ratings, disable **Enable X Watchlist Monitoring**; both the daemon and direct monitor entry point then skip X requests without affecting the ratings schedule. You can also edit `.local-data/dashboard.env` directly; after saving, restart the affected components as appropriate, or wait for the next task cycle to pick up the changes.
`DASHBOARD_GROK_API_MODE` accepts `auto`, `responses`, or `chat`. The default `auto` mode uses the Responses API with `web_search`/`x_search` tools for Grok 4.5 and keeps Chat Completions for other models; compatible gateways can force either mode. `X_WATCHLIST_REQUEST_TIMEOUT_SECONDS` controls the per-account X request timeout and defaults to `45` seconds.
`DASHBOARD_NEWS_API_MODE` also accepts `auto`, `responses`, or `chat`. The default `auto` mode uses the Responses API with `web_search` for Grok 4.5 and GPT-5 search models. A Grok Responses news model also receives `x_search`; other models use `web_search` for publicly indexed Xueqiu/X pages and never switch to `DASHBOARD_GROK_*`.
`*_CONTEXT_LENGTH` represents only the model context window and defaults to `128000`; `*_MAX_TOKENS` is the desired maximum output length and is mapped to `max_tokens` or `max_output_tokens` for the selected API. Known GPT-5.6 gateway aliases that reject the Responses output-limit parameter omit it, and other gateways receive one guarded retry without it when they explicitly report the parameter as unsupported. Both JSON and SSE responses are accepted, including gateways that force SSE when `stream=false`.
The news pre-check examines at most five candidate stocks concurrently by default. If the upstream service returns rate limits or 403/429 responses, reduce `DASHBOARD_NEWS_CONCURRENCY` to `2` or `1`. When a legacy snapshot has an `unclassified_response` whose saved summary implies one unambiguous positive, negative, or neutral conclusion, backfill repairs the label locally while retaining the original fetch time and making no model request; ambiguous records remain unclassified.

The iWencai data source is disabled by default. The **iWencai Data Source** settings include **Test iWencai Connection**, which sends one lightweight read-only query using the current form values without saving settings or modifying dragon-tiger snapshots. After enabling it and configuring an API key, the Dashboard exposes the purpose-built
`/api/iwencai/dragon-tiger?date=YYYY-MM-DD&page=1&limit=100` endpoint. It does not proxy arbitrary natural-language queries,
caps each page at 100 stocks, and uses the Dashboard's existing rate limits and cache. Results are deduplicated by stock code, `sector` contains the industry, and `limit_up_reason` plus `limit_up_reason_category` expose iWencai's summarized limit-up reason and category. Duplicate leaderboard entries remain available under `details`. Each daily snapshot is compared with the preceding A-share trading-day rolling snapshot. Repeated stocks receive `consecutive_listed`, `consecutive_list_days`, and up to ten `consecutive_list_dates`; a missing adjacent snapshot safely resets the streak instead of guessing across a gap. Each news-precheck batch uses the scheduled dragon-tiger query time configured by `IWENCAI_DRAGON_TIGER_CRON` as its start and never uses the upstream response's `generated_at`. After a successful pull, unchecked stocks use `DASHBOARD_NEWS_*` to search the latest three days when either `limit_up_streak >= 2` or both `consecutive_listed = true` and `consecutive_list_days >= 2`, with at most five concurrent requests. Company and exchange disclosures plus mainstream financial media are used for factual verification; Xueqiu and X/Twitter are presented separately as market sentiment and unverified posts must not become company facts. Each stock's `news_precheck.checked` state and the aggregate `limit_up_news_checked_codes` and `limit_up_news_pending_codes` fields are persisted; the legacy `continuous_news_*` fields remain compatibility aliases. Scheduler startup catches up the latest due trading-date snapshot, and each new pull first backfills the retained snapshot, allowing missed Friday checks to complete after a weekend restart or before the next pull. Once all required stocks are checked, later same-day pulls make no model calls. Missing or failed configuration never blocks the main snapshot and never falls back to `DASHBOARD_GROK_*`. `seats` retains the top-five buy/sell institution, brokerage, and explicitly tagged hot-money/quant seats, including separate `buy_rank` and `sell_rank` when one broker appears on both sides; `institution_seats` remains as a compatibility subset. A seat-detail failure does not block the main list; when the query date is unchanged, missing seat data does not replace valid rows in the current snapshot. iWencai responses are research snapshots; timeouts, count mismatches, and upstream failures return explicit status without overwriting account, fill, or other real trading records.
The `/dragon-tiger` Dashboard section can query a selected trading date live. Current-day data and the most recent rolling snapshot remain public until the next successful query replaces that snapshot; earlier dates require the administrator password and a valid session. When a current-day live query is empty, the endpoint continues returning the most recent successful snapshot instead of replacing the page with an empty state before the new list is published. Every non-current-date response is excluded from public and CDN caching so the replaced date becomes protected immediately. Only a request matching the latest snapshot date reuses local data before an upstream query; other dates are not persisted. By default, Cron refreshes `.local-data/runtime/cron/output/iwencai_dragon_tiger_latest.json` at 18:00 China time on A-share trading days. The file retains only the most recent non-empty successful query and is atomically replaced by the next successful query. That refresh also removes legacy `iwencai_dragon_tiger/YYYY-MM-DD.json` archives; empty or failed main-list responses preserve the previous valid snapshot.

The trading-decision intelligence bundle is enabled by default. Each model decision after a stock-selection scan on the Practice page reads market monitoring, overnight U.S. market data, index quotes, sector performance, industry fund flows, trending stocks, candidate news, and an account-position summary, then writes the compressed `decision_intelligence` into the simulated-trading decision log. If a market-data source fails, its `source_status` is retained, and the current decision continues with available information and existing risk controls.

The canonical URL for the Practice page is `/practice`. The candidate query and refresh endpoints are `/api/practice_candidates` and `/api/practice_candidates/refresh`, respectively. Legacy links based on `?category=practice` or `?category=b1_screen`, plus the `/api/b1_screen` endpoint, are retained only as compatibility entry points.

### 3.1 Market Data and Fund-Flow Settings

The **Market Data and Fund-Flow Settings** page groups index refresh and industry fund-flow controls:

| Setting | Default | Allowed range | Application |
|---|---:|---:|---|
| `DASHBOARD_INDICES_TTL_SECONDS` | `60` | Greater than 0 seconds | Hot-applied |
| `DASHBOARD_NIUONE_MAINLINE_MINUTE_REFRESH_ENABLED` | `1` | `0` or `1` | Dashboard restart required |
| `DASHBOARD_MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS` | `30` | `30`–`600` seconds | Dashboard restart required |
| `DASHBOARD_INDUSTRY_FLOW_PLAYBACK_SPEED` | `0.5` | `0.5`, `0.75`, `1`, `1.5`, `2`, `5`, or `10` | Hot-applied; used on the next fund-flow page load |
| `DASHBOARD_INDUSTRY_FLOW_SIDE_LIMIT` | `10` | `1`–`10` industries per side | Hot-applied; used by the next fund-flow request |
| `DASHBOARD_INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS` | `60` | `60`–`600` seconds | Hot-applied; used by the next sampler cycle |
| `DASHBOARD_INDUSTRY_FLOW_MORNING_START` | `09:25` | China-time `HH:MM` | Hot-applied; used by the next sampler check |
| `DASHBOARD_INDUSTRY_FLOW_MORNING_END` | `11:31` | China-time `HH:MM` | Hot-applied; used by the next sampler check |
| `DASHBOARD_INDUSTRY_FLOW_AFTERNOON_START` | `13:00` | China-time `HH:MM` | Hot-applied; used by the next sampler check |
| `DASHBOARD_INDUSTRY_FLOW_AFTERNOON_END` | `15:01` | China-time `HH:MM` | Hot-applied; used by the next sampler check |

By default, industry fund flow is sampled only on A-share trading days during 09:25–11:31 and 13:00–15:01 China time. All four boundaries can be edited on the settings page and must satisfy morning start < morning end < afternoon start < afternoon end. Changing the window or interval does not delete stored real samples; points outside the active window are excluded from playback, and new samples follow the updated window and minimum spacing.

The **Main Fund Flow** ranking on the indices page and the fund-flow animation share Eastmoney's industry-board **Today Main Net Amount** metric (`f62`, converted from yuan to CNY 100 million) and the same 60-second cache. New snapshots and samples are stored in `industry_main_money_flow_cache.json` and `industry_main_flow_history.json`, respectively. Legacy files based on total inflow minus total outflow are retained but are never mixed into main-net playback.

When `DASHBOARD_NIUONE_MAINLINE_MINUTE_REFRESH_ENABLED=1`, the same validated per-stock Tencent quote batch is handed to the Theme Strength calculator, so that page does not issue another Tencent request. The shared interval defaults to 30 seconds and is configurable through `DASHBOARD_MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS`. The fast calculator reads only private daily-K-line and industry-mapping caches, retains the Dragon-Tiger confirmation component from the newest complete research scan, and records quote time separately from calculation time. It never requests or consumes company news for theme recognition. Insufficient coverage, a stale quote timestamp, or a calculation failure retains the previous valid theme result.

The A-share market-sentiment chart on the indices page reads one Tencent Shanghai/Shenzhen full-market snapshot every 30 seconds by default. It uses the returned current, high, upper-limit, and lower-limit prices to count sealed limit-ups, sealed limit-downs, and broken limit-ups; positive and negative quote changes produce the red and green counts. Actual turnover in the lower panel primarily comes from the sum of Eastmoney one-minute turnover for the Shanghai Composite and Shenzhen Component. If that request fails or is stale, the service falls back to cumulative turnover from the Tencent full-market quote batch and exposes the selected source in the API and UI. Projected full-day turnover uses a two-stage method. From 09:30 through 09:34, today's only live input is the finalized 09:25 full-market auction amount. The estimate is `median historical full-day turnover × (today's auction amount ÷ median historical auction amount)^0.5`, using up to the latest 20 valid matched trading days. This square-root shrinkage prevents an out-of-range auction amount from propagating with one-for-one elasticity; intraday actual turnover is not an input during these five minutes, and fewer than 10 valid pairs suppresses the projection. From 09:35 onward it uses the latest 20 complete trading days of five-minute cumulative turnover distributions, dividing current cumulative turnover by the median same-time cumulative share; fewer than 20 complete days likewise suppresses the projection. The auction job stores only pre-09:27 structured samples covering at least 4,000 stocks, so post-open recovery runs cannot contaminate the factor. Projected increment is the signed difference between projected turnover and the latest complete previous trading day's full-day turnover; that comparison reference is independent of the active projection model's training samples, and every valid point on the same trading day uses the same comparison date. In addition to the complete current-day market-breadth samples, the history file retains only a compact actual cumulative-turnover curve from the latest prior trading day. Up to 600 aggregate samples are retained, enough for a complete trading day at the 30-second default. The API aligns today's and the previous day's actual turnover by trading progress and exposes their same-time difference: positive means expanding volume and negative means contracting volume. All turnover series use CNY 100 million. The API also exposes the active stage's source, sample range, sample count, and five-minute interval where applicable. The universe includes ST stocks and excludes B shares, Beijing Stock Exchange listings, and securities without a valid current price. The background sampler runs only on A-share trading days during 09:30–11:30 and 13:00–15:00 China time and stores real observations in `market_breadth_history.json`. Legacy observations without turnover or increment remain intact and render as gaps rather than synthetic zeroes. If one day contains observations from different projection models, the API hides only incompatible projected and increment fields while preserving real breadth, limit-state, and actual-turnover observations. An incomplete Tencent batch, insufficient turnover coverage, or failed request retains the previous valid history.

After the intraday 20-day turnover-distribution profile is built successfully for the first time, it is atomically saved as `cron/output/turnover_profile_cache.json` under the private runtime directory. A restarted Dashboard restores only an exact-current-trading-day cache whose model version and full structure validate, then recomputes the projection from the latest actual cumulative turnover. Cross-day, corrupt, or incomplete caches are not reused, and a failed upstream refresh never overwrites a valid saved profile.

Industry fund-flow snapshots, samples, and the market-sentiment curve use 09:00 Asia/Shanghai as the display rollover. The prior calendar day's closing data remains visible after midnight through 08:59:59; at 09:00 the current display is cleared and waits for the new day's first valid sample. Market-sentiment history also retains one compact actual-turnover curve from the latest prior trading day. The Dashboard validates file dates at startup, and a resident background task atomically clears `industry_main_money_flow_cache.json` every day at 09:00 Asia/Shanghai while rolling `industry_main_flow_history.json` by each sample timestamp. Only samples outside the current display day are removed, so stale top-level metadata or mixed-day content cannot discard valid current-day observations. Every successful industry-flow sample first updates the atomic `industry_main_flow_history.recovery.json` mirror; startup merges current-day real samples from that mirror when the primary file is missing, damaged, or unexpectedly empty. The same task rolls `market_breadth_history.json`: the prior display day's breadth and limit-state fields are discarded, and only actual cumulative turnover is archived. Related in-memory API caches are invalidated at the same time. If an upstream source still reports the previous day's timestamp after 09:00, the server rejects that snapshot instead of displaying or persisting it; the page remains empty until the first valid current-day sample arrives.

### 3.2 Practice-Strategy Scheduling and Process Ownership

Strict-forward v18 aligns NiuOne opening capacity with the portfolio backtest: at most two first openings are permitted per Beijing trading date across Practice decision cycles. The execution layer reconstructs today's opened codes from persistent fill state and de-duplicates them by code; adds and other strategy suites do not consume the NiuOne allowance, while a further symbol is rejected as `position_capacity`. Both the value and counting rule are protocol-frozen.

Individual practice strategies do not own separate candidate-scan timers. At 09:10 on every A-share trading day, the Dashboard prewarms the latest 120 Tencent qfq daily bars for every supported non-ST stock into private SQLite. A cold deployment, lost volume, or expired cache no longer waits for that time window: bounded initialization begins immediately after service startup. A same-day retry after interruption fetches only missing symbols, and a failed response never replaces a successful series. Practice scans require 90% valid-date coverage by default. Dashboard-launched scans then read only valid local history and merge bulk live quotes; they do not issue per-symbol history fallbacks on the interactive path. When coverage is insufficient, a manual task queues behind initialization and shows the stage, completed count, and failures, while a scheduled task records a data-not-ready outcome and does not enter simulated trading with incomplete data.

At every configured time, the B1 scheduler inside the Dashboard first generates one unified **Current Market Summary and Evaluation** from live indexes, industry performance, industry main-fund flow, market breadth/turnover, and existing market scans, then starts the shared scanner. The full-market Tencent quote stage has a separate 90-second default aggregate budget so one slow upstream cannot consume the entire 480-second scan budget. The scanner reads `DASHBOARD_ACTIVE_STRATEGY` and runs only the scorers in that active suite. When that scan finishes, the scheduled path both passes the same artifact into model assessment and simulated execution checks and starts a background full-market Theme Strength research scan. The latter ignores `DASHBOARD_ACTIVE_STRATEGY`, updates only the dedicated theme cache, and cannot create candidates or trades. Multiple Dashboard instances that share one runtime directory use process leases to serialize prewarm and full-scan jobs, preventing duplicate scans and trades. Manual-task terminal state is persisted atomically; a restart marks unfinished work as interrupted and never replays trading automatically.

The Practice page no longer derives a separate market-evaluation label from B1 breadth thresholds. The summary artifact's `tone` / `tone_label` is both the displayed evaluation and the trading-context risk level; when the model is unavailable, the same module's local summarizer is used. Clicking **Generate Current Market Summary and Evaluation** or **Manually run candidate scan and trading strategy** refreshes this artifact, while scheduled refreshes reuse `DASHBOARD_PRACTICE_SCHEDULE_TIMES`. A failed generation preserves the latest valid same-day artifact instead of replacing it with an incomplete snapshot.

| Setting | Default | Scope | Application |
|---|---|---|---|
| `DASHBOARD_ACTIVE_STRATEGY` | `niuone` | New candidates, model prompt, and entry rules | Hot-applied; used by the next scan |
| `DASHBOARD_B1_SCHEDULE_ENABLED` | `1` | Starts the Dashboard's built-in candidate scheduler | Dashboard restart required |
| `DASHBOARD_PRACTICE_SCHEDULE_TIMES` | `09:25,10:00,10:30,11:00,11:20,13:00,13:30,14:00,14:30,14:50` | Practice summary/evaluation, active-strategy scan, and trading-decision times | Hot-applied; legacy `DASHBOARD_B1_SCHEDULE_TIMES` is read only for compatibility |
| `DASHBOARD_B1_SCHEDULE_CATCHUP_MINUTES` | `35` | Catch-up window after brief Dashboard downtime | Dashboard restart required |
| `DASHBOARD_B1_SCAN_TIMEOUT_SECONDS` | `480` | Hard timeout for a complete scanner process; timeout results identify the active stage | Dashboard restart required |
| `DASHBOARD_TENCENT_QUOTE_STAGE_TIMEOUT_SECONDS` | `90` | Aggregate budget for Tencent full-market live quotes, from 15 through 300 seconds | Dashboard restart required |
| `DASHBOARD_KLINE_CACHE_ENABLED` | `1` | Prefer and incrementally fill the local daily-K-line SQLite cache during scans | Dashboard restart required |
| `DASHBOARD_KLINE_PREWARM_ENABLED` | `1` | Starts the pre-market full-universe daily-K-line refresh | Dashboard restart required |
| `DASHBOARD_KLINE_PREWARM_TIME` | `09:10` | Prewarm time on A-share trading days | Dashboard restart required |
| `DASHBOARD_KLINE_PREWARM_WORKERS` | `12` | Download concurrency, capped at 16 | Dashboard restart required |
| `DASHBOARD_KLINE_PREWARM_TIMEOUT_SECONDS` | `600` | Total timeout for one prewarm run | Dashboard restart required |
| `DASHBOARD_KLINE_PREWARM_CATCHUP_MINUTES` | `15` | Catch-up window after brief Dashboard downtime | Dashboard restart required |
| `DASHBOARD_KLINE_BOOTSTRAP_ENABLED` | `1` | Initialize immediately after a cold start or cache expiry, outside the pre-market window | Dashboard restart required |
| `DASHBOARD_KLINE_BOOTSTRAP_MAX_ATTEMPTS` | `3` | Maximum automatic initialization attempts per date | Dashboard restart required |
| `DASHBOARD_KLINE_READINESS_MIN_COVERAGE_PERCENT` | `90` | Valid-date daily-K-line coverage required to admit a Practice scan, from 90 through 100 | Dashboard restart required |
| `DASHBOARD_MANUAL_DATA_INITIALIZATION_TIMEOUT_SECONDS` | `660` | Total time a manual task may wait in the data-initialization queue | Dashboard restart required |
| `DASHBOARD_B3_EXIT_TIME` | `09:37` | Opening automatic-exit check | Read by a subsequent Cron cycle |
| `DASHBOARD_TIME_EXIT_TIME` | `14:45` | End-of-day automatic exits and time-box checks | Read by a subsequent Cron cycle |
| `DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON` | `5 9 * * 1-5` | Freeze or verify the strict-forward protocol before the first Practice decision | Runs immediately at Scheduler startup, then follows Cron Monday through Friday |
| `DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON` | `15 15 * * 1-5` | Refresh marks without trading and persist the post-close account equity | Read by the subsequent strict-forward evaluation |
| `DASHBOARD_NIUONE_FORWARD_CRON` | `20 15 * * 1-5` | Build the NiuOne strict-forward report from the complete simulated-fill ledger | Read by a subsequent Monday-through-Friday Cron cycle |
| `DASHBOARD_NIUONE_FORWARD_COHORT_START` | `2026-08-04` | First-entry date admitted to the strict-forward cohort | Read by a subsequent Cron cycle; changing it requires a new protocol lock |

If an existing deployment defines only `DASHBOARD_B1_SCHEDULE_TIMES`, the Dashboard continues to read that value. When both keys are present, `DASHBOARD_PRACTICE_SCHEDULE_TIMES` wins. The settings page exposes only the new key; its next save writes the new key and removes the legacy key from the local `dashboard.env` file.

The 09:25 scan falls in the quiet period after the opening auction. The system may generate candidates and model actions, but it does not book a fill at the auction reference price. Executable actions are queued, and after 09:30 the Dashboard's deferred-decision worker rechecks the session, current price, cash, and strategy risk budgets.

Users can click **Manually trigger candidate scan and trading strategy** on the Practice page to run the complete flow. It uses the same scanner, active-strategy setting, and execution layer as the scheduled path; it is not a force-fill or risk-bypass endpoint. A normal page refresh only reads cached and account state.

Every scheduled or manual B1 decision refreshes all open positions first and evaluates each position under the original exit rules identified by its stored `strategy_mark`; the active suite controls new candidates and BUYs only. SELL/HOLD checks continue when the candidate list is empty or the daily loss budget has fired, and that budget pauses new entries only.

Local automatic exits are also invoked by the separate Cron Scheduler process at dedicated times. Structural stops, Sector Tide deterioration, strategy time boxes, 2R, and 2 ATR remain discrete checks rather than tick-by-tick monitoring. Both the Dashboard and Cron Scheduler processes must be running for the full lifecycle.

NiuOne strict-forward evidence begins on `2026-08-04`. Every Cron Scheduler startup immediately runs a database-independent `--protocol-only` preflight, followed by a scheduled 09:05 check on weekdays. Both a normal startup and a late startup between 09:05 and 09:25 therefore freeze or verify `cron/state/niuone_forward_protocol.json` before the first Practice decision. Before the cohort starts, preflight also freezes a code-free zero-position account boundary; a missing, invested, or late baseline makes account return unattributable. The deterministic preflight gets one attempt, so a mismatch cannot stall unrelated scheduled jobs behind a five-minute retry. In addition to the latest 200 JSON rows retained for display, every simulated fill stores its complete payload idempotently in `niuniu.db`, so the opening BUY's theme/rank/industry/execution-gap snapshot, signal timestamp, schedule slot, scheduled/catch-up/manual origin, direct/deferred execution mode, sizing boundary, adds, and partial exits survive display-log trimming. Protocol v18 also stores each complete displayed opportunity set, canonical strategy identity, decision-pool eligibility, model-requested/maximum-permitted shares, and structured filter/rejection reasons; trading still consumes only explicit `trade_items`, so audit data cannot widen the pool. Deferred execution records inherit their original slot's opportunity set. The report deduplicates by scheduled slot and profiles observed → eligible → model BUY → executed BUY conversion, sizing utilization, and consistency by all five lifecycle stages. A complete empty set is valid; missing fields, duplicate codes/ranks, or eligibility/blocker contradictions are not. At 15:15 on each actual A-share operating day, `--snapshot-equity` refreshes marks without trading and persists post-close account equity. At 15:20 the Scheduler read-only merges durable SQLite history with the recent JSON overlay and atomically refreshes the private `cron/output/niuone_forward_evaluation.json` report. State-only JSON rows remain a recovery overlay and cannot substitute for a durable entry payload or equity point. v18 also requires every NiuOne opening BUY to initialize a holding-stage path, every subsequent mainline scan to append or extend it, and an actual SELL to freeze the exit stage; a missing operating-day observation or any entry/path/exit time or stage mismatch keeps the lifecycle `data_quality_blocked`.

The lock freezes the cohort date, gates, shadow candidates, relevant NiuOne scoring/selection/exit/execution, scheduling, and durable fill/decision-storage source files, plus non-secret runtime settings. The settings include the preflight/post-close Cron expressions and the effective durable-database, recovery-state, and two operational-audit-state paths. Values are stored only as per-field SHA-256 digests, so paths, prompt text, and model endpoints are not copied into the report. `--as-of` controls only the report cutoff; lock timestamps and pre-cohort replacement eligibility always use the actual wall-clock date, so a backdated report cannot replace the lock after the cohort begins. A later mismatch preserves the original lock, makes the preflight return a non-zero status, marks the post-close report `protocol_mismatch`, and blocks advancement even when the sample-size or elapsed-time gate is otherwise met. The Scheduler retains 400 days of terminal job results in `niuone_cron_scheduler.json`, capped at ten runs per job per day; the Dashboard retains 400 days of Practice-slot outcomes in `b1_schedule_state.json`. A Practice slot is `ok` only when screening and the trading-decision chain succeed and the complete decision evidence reaches SQLite. Model or persistence failure is `error`, while a cache hit without proof that its decision ran is `skipped`. A failed durable fill or system-decision write also fails an independent automatic-exit task.

The report becomes eligible for operations review only after 30 complete zero-to-zero lifecycles or three full calendar months under one unchanged protocol, when 100% of completed lifecycles have complete entry attribution, and when every actual A-share operating day from cohort start through report cutoff has complete evidence. The existing exchange-calendar cache controls operating dates; without a trustworthy cache the system conservatively falls back to Monday through Friday. Each day requires a successful preflight before the first decision slot, every configured Practice slot marked `ok`, a rich SQLite decision row for every slot, successful opening and closing exit checks, the 15:15 post-close equity snapshot, and the 15:20 forward evaluation. If the sample gate is met but that coverage is incomplete, status is `operations_blocked`; a missed historical opportunity or mark cannot be reconstructed by rerunning one aggregate report, so archive the invalid cohort and start a new one. Legacy or non-durable payloads and missing mainline state/industry, same-stage rank, signal/schedule timestamps, conditional schedule slot, execution mode, or sizing boundary remain visible in descriptive totals and missing-field diagnostics. When the sample gate is met but attribution is incomplete, status is `data_quality_blocked`. Three elapsed months with fewer than 30 completed lifecycles sets `review_scope=frequency_and_operations_only`. The lifecycle gate requires at least 30 fully attributed lifecycles, observed win rate at or above the frozen 59.71% historical reference, a trade-level Wilson 95% lower bound above 50%, fee-inclusive average net return and cumulative realized P&L above zero, and profit factor above 1. It also requires at least 30 unique entry-date-by-industry clusters and 30 Herfindahl effective clusters, a cluster-balanced win rate at or above 59.71%, its normal 95% lower bound above 50%, and positive cluster-balanced average net return. Trades opened in the same industry on the same date add only one unique cluster, so a concentrated mainline wave cannot masquerade as independent replication. A final high-win-rate and positive-return claim additionally requires no non-NiuOne or unknown-strategy fills, one durable post-15:00 equity point for every operating day, continuous initial-capital and accounting identities, positive portfolio return, maximum drawdown no worse than 6%, return-to-drawdown of at least 1, and complete operations and opportunity-funnel evidence. The report never promotes a rule automatically. To change production rules, a locked setting, or an invalid cohort, first stop the Dashboard and Cron Scheduler, archive the existing report and protocol lock, set `DASHBOARD_NIUONE_FORWARD_COHORT_START` to a new trading day, and run preflight before that date. Do not remove only the lock while retaining the old cohort date, because that would admit old-protocol trades into the new cohort.

Executed BUYs in the v20 funnel come from the durable fill ledger and are reconciled against execution copies in decision payloads; discrepancies remain explicit diagnostics and keep the cohort `data_quality_blocked`.

v18 applies bounded risk reduction to NiuOne BUYs: when a valid 100-share-lot model request exceeds only a positive deterministic maximum, execution uses that maximum and durably records model-requested shares, executed shares, the maximum, and the reduction flag. Candidate eligibility, daily/holding/theme capacity, structural-stop inputs, cash reserve, and every risk budget remain unchanged and fail-closed; a zero ceiling still rejects, while Sector Tide and other suites retain their existing execution behavior.

v18 also removes a T+1 execution loss for model-directed NiuOne SELLs. When a valid 100-share-lot request exceeds only the positive whole-lot quantity currently available because some shares remain locked from today's purchases, execution sells the available quantity instead of turning the entire request into no fill. The model request, availability at execution, actual fill, and reduction flag are stored durably and audited by the post-close report. If reduction is needed, zero or non-round-lot availability still rejects; local automatic exits and other suites keep their existing behavior.

v18 also froze the NiuOne Probe daily-V recovery ratio to `[0.60, 2.00)`. Scoring and the pre-fill recheck both reject a repair below 60% or one that has already reached twice the prior decline; the latter belongs in a confirmed Launch, Leading, or Resumption path rather than consuming a Probe slot. The protocol lock records both bounds explicitly, and v20 freezes the new production candidate's historical reference win rate at 59.71%.

v18 also freezes Markup quality into the protocol identity. NiuOne Leading requires both a top-20% within-mainline rank and same-day theme strength of at least 60. NiuOne Launch accepts only a cross-day-persistent `emerging` theme; a confirmed `mainline` must use Leading. Scoring and the pre-fill recheck share the same fail-closed rule.

v18 also freezes Probe continuation quality and capital-utilization boundaries. A theme must have at least six strong stocks or a Brewing-state streak of at least three trading days. Up to two qualified Probes may be retained per day and the absolute single-name cap is 6.25%, while offensive/rotation/recovery per-trade equity risk remains 0.35%/0.30%/0.25%.

v20 adds a two-tier lifecycle scale-in/scale-out rule on top of those boundaries. The 6.25% limit is only the Brewing Probe cap. A Probe- or Launch-origin position with 2%–12% unrealized profit may add once toward a 10% cap when its emerging mainline persists across sessions, remains in Markup, and the stock stays in the strong Leading tier. Once the mainline is fully confirmed it may add once more toward a 20% cap. Risk sizing, theme/portfolio risk, cash, and the stage cap still determine the smaller target. Profit above 12% is no longer chased, and Climax, Divergence, and Fade never authorize an add. The first non-losing Climax observation trims one third once without disabling the existing partial-profit, breakeven, or 2 ATR trailing rules.

v21 replaces the post-confirmation fixed add count with repeatable reduction/re-entry cycles. A confirmed Leading position releases one third after either a 1 ATR decline from the cycle's closing-price peak or three sessions without a new peak while at least 0.25 ATR below it. Released risk can be replaced only after price rises 0.5 ATR from the trim, the lifecycle returns to Markup, and strong Leading status is restored. A fill clears the armed state and starts a new cycle; another add therefore requires another independent pullback, while the lifetime add limit is frozen as `null`. Divergence may trigger a reduction, but unrecovered Divergence, Climax, and Fade cannot add. This production-rule change advances the strict-forward lock to `niuone-strict-forward-v21`; v20 and v21 fills must not be pooled into one cohort.

v22 fixes action/stage mismatches for multi-concept stocks. Each NiuOne action selects a lifecycle-compatible concept membership, confirmed branches are no longer excluded merely because they are outside the two display mainlines, and a top-20% strong core name may continue as Leading after its confirmed theme becomes `diverging`. Divergence no longer repeats the contradictory 60-point same-day broad-theme-strength gate. Daily openings, total and per-theme holdings, structural stops, and price-pattern gates remain unchanged. The strict-forward lock advances to `niuone-strict-forward-v22` with a default new cohort date of `2026-08-04`; archive the old lock and report before deployment and never pool v21 and v22 fills.

v23 adds a conditional Markup Momentum Probe. It applies only to a cross-day-persistent `emerging` theme already in Markup when the stock is the number-one industry leader, has strength of at least 90 and score of at least 8.0, and the market is not defensive. The route permits up to 3.2 ATR of price extension and an 18%/3 ATR structural stop, but rejects a next-open gap above 3% and caps the initial position at 3%; effective-loss-distance sizing may reduce it further. All ordinary Launch, Probe, and Leading gates remain unchanged. The strict-forward lock advances to `niuone-strict-forward-v23`, which must not be pooled with v22 fills.

v24 tightens the Markup Momentum Probe using a causal January–June 2026 replay. An ordinary entry requires score at least 8.1, theme score at least 70, and no more than 1 ATR of EMA20 extension. The 2.5–3.2 ATR band is reserved for an exceptional acceleration with daily gain at least 9.5% and volume ratio no greater than 1.2. The qualified initial cap rises to 4%, while effective-loss-distance, account/theme risk budgets, and the 3% next-open gap still bind first. The strict-forward lock advances to `niuone-strict-forward-v24`, which must not be pooled with v23 fills.

Administrator backtest v25 removes the selectable Balanced/Aggressive profiles and always enforces Aggressive parameters for NiuOne: 1.35x account-risk budgets, 1.15x total/theme exposure budgets, and 3/6/3 daily-new/total/theme capacity. The server ignores a stale client's submitted profile and does not restore an old Balanced result. This advances only the backtest protocol to `niuone-backtest-v25`; the production strict-forward protocol remains v24.

v25 fixes premature relative-rank liquidation of a remainder that has already been de-risked at Climax. Only while the Climax reduction is complete, the stock remains strong, the theme score is at least 55, and the theme is neither fading nor inactive, leader-rank loss requires three consecutive sessions instead of two and the trailing distance widens from 2 ATR to 3 ATR. Loss of any health condition immediately restores the original two-session/2 ATR policy; structural and break-even stops, mainline weakness, Fade, and the market hard stop still run first. The strict-forward lock advances to `niuone-strict-forward-v25`, and the administrator backtest advances to `niuone-backtest-v26`; neither may pool evidence with an older protocol.

v26 permits NiuOne entries in a defensive regime at the minimum-risk tier. Mature-path per-trade/open/theme risk limits are 0.30%/0.90%/0.60%, with 20% total exposure and 12% theme exposure; Probe tightens these to 0.15% per trade, 0.30% per theme, and 5% theme exposure, and takes 50% off at 0.75R. Lifecycle, leader, setup, structural-stop, limit-up, and portfolio-capacity gates are unchanged; the compound hard stop still blocks new entries. Strict-forward locks advance to `niuone-strict-forward-v26`, administrator backtests advance to `niuone-backtest-v27`, and older evidence must not be pooled.

v27 separates NiuOne's factual industry from its traded narrative. Eastmoney `f100` remains in `industry/sector`, while the action-selected `f103` concept is stored in `signal_theme`. A multi-concept stock derives 75% current evidence from theme strength, within-theme rank, peer co-movement, and same-day rank, plus a 25% historical prior accumulated from preceding snapshots; its concept-attribution weights sum to exactly one. The first fill freezes `entry_theme` and its evidence. `active_theme` changes only when another lifecycle-valid theme leads by at least 10 points for two consecutive trading days, so later scans cannot silently rewrite the factual industry or entry narrative. Risk capacity follows the action/active theme. Strict-forward locks advance to `niuone-strict-forward-v27`, cluster performance by entry date × entry theme, and require theme, basis, score, weight, and historical-prior evidence. Administrator backtests advance to `niuone-backtest-v28`; archive the old lock and report before deployment and never pool older fills.

v29 moves multi-concept attribution ahead of theme aggregation. Eastmoney `f103` supplies candidate labels only; current evidence no longer reads a theme total that already contains the focal stock, and instead combines leave-one-out peer resonance, cohort direction, same-day rank, and structural rank before applying the 25% causal prior. Theme recognition performs no news search, and a saved news summary cannot add a candidate, change an attribution score, or change a theme total. Independent mainline scans skip news precheck entirely; ordinary strategy scans may still use it only as a pre-entry candidate risk check. Softmax allocation retains residual unattributed mass when the candidate set is weak. Theme strong stocks, amount, breadth, intraday strength, and leaders are then recomputed with attribution weights, while intraday breadth is shrunk toward market breadth according to effective attributed sample size. A stock below 15% attribution cannot lead that theme, and the public top five collapses highly overlapping label clones. Theme context advances to schema v10 and refuses v9 cross-day confirmation. Strict-forward locks advance to `niuone-strict-forward-v29`, administrator backtests to `niuone-backtest-v30`; archive prior locks, reports, and backtests and never pool them.

v30 adds a 20-session market-neutral return-wave signal to multi-concept attribution. It correlates the stock's daily excess returns with the leave-one-out median excess return of each `f103` cohort, then shrinks the signal by its relative rank across that stock's candidates. Every NiuOne scan mode now skips news precheck and model calls; news configuration remains available only to other modules that explicitly use it. Context/cache schemas advance to v11/v9 and strict-forward/backtest protocols to `niuone-strict-forward-v30`/`niuone-backtest-v31`; older evidence must not be pooled.

v31 fixes the second dilution of multi-concept leaders. The 15% attribution-weight floor still filters ordinary weak branches, but a stock's highest-scoring theme remains leadership-eligible when its attribution score is at least 60, even if many candidate labels push that primary share below 15%. Qualified structural leaders rank by raw stock strength and qualified intraday leaders by same-day return; attribution score is only a tie-breaker. The admin backtest checks structural eligibility against the actual next-session open, while 5bp synthetic slippage affects only the fill and risk sizing. Theme breadth, amount, concentration, lifecycle, setup, stop, and portfolio-risk rules are unchanged. Context/cache schemas advance to v12/v10 and strict-forward/backtest protocols to `niuone-strict-forward-v31`/`niuone-backtest-v32`; archive old locks, reports, and backtests before deployment.

When a strategy appears not to trigger, check in this order:

1. Confirm that `DASHBOARD_ACTIVE_STRATEGY` in `.local-data/dashboard.env` names the expected suite.
2. Confirm that `DASHBOARD_B1_SCHEDULE_ENABLED` is enabled and the Dashboard process is still running.
3. Confirm that the current time is at a `DASHBOARD_PRACTICE_SCHEDULE_TIMES` slot or within the catch-up window.
4. Inspect `.local-data/runtime/cron/state/b1_schedule_state.json` for an `ok`, `error`, or `skipped` status for the slot.
5. Confirm that `.local-data/runtime/market_data/tencent_daily_klines.sqlite3` exists and today's `prewarm_runs` row is `completed`.
6. Inspect `.local-data/runtime/cron/output/multi_strategy_latest.json` for a recent `generated_at`, the active suite's candidates, and required context fields.
7. If automatic exits did not run, inspect the Cron Scheduler process and `.local-data/runtime/logs/niuone_cron_scheduler.log`.
8. If the strict-forward report is stale, first inspect the Scheduler startup log for the protocol preflight and check `DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON`, then inspect `DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON`, `DASHBOARD_NIUONE_FORWARD_CRON`, `niuniu.db`, and `.local-data/runtime/cron/output/niuone_forward_evaluation.json`. For `operations_blocked`, also inspect the missing dates/events reported from `niuone_cron_scheduler.json` and `b1_schedule_state.json`. For `portfolio_evidence_blocked`, inspect the reported account boundary, missing equity dates, and structured invalid-field counts. For `protocol_mismatch`, inspect only the `changed_fields` names and follow the new-cohort procedure. Do not overwrite the original lock or copy these private files into public diagnostics.

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

This builds the `web/` Vue application and checks `web/` JavaScript, `app/` Python, Shell and Windows BAT entrypoints, and the complete unit-test suite.

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
