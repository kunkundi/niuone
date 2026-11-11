# Deployment, Validation, and Rollback Manual

[简体中文](OPERATIONS.md) | English

This manual is for maintainers and developers and covers NiuOne operation, validation, deployment, log inspection, and rollback. Native deployments store real runtime data in `.local-data/`, while Docker Compose deployments use the named volume `niuone-data`. Neither is tracked by Git, and they do not synchronize automatically.

Paper-account sold cards, fill logs, chart markers, and trade notifications show cumulative realized P&L for the holding cycle and its realized return: the sum of net fill P&L through that sale divided by all buy costs in the cycle up to that time, including buy fees. Investing 10,000 and realizing 500 by selling half displays +500 / +5%. Adds increase the denominator; reopening after a full exit starts a new cycle. Today’s sold cards include earlier realized P&L from cycles sold today, taking the last cumulative value once per cycle. Unverifiable opening or quantity history displays unavailable. Original fill P&L, account cash, strategy performance, and execution rules retain their existing definitions.

Sell notification headings emphasize the fraction of the stock's entire pre-sale holding sold in that fill. Full exits are explicitly labeled “清仓 · 卖出100%持仓”. Notifications also highlight the sale amount as a percentage of account equity and show shares remaining. Full-exit status uses filled shares, before/after quantities, and explicit execution flags, never rounded account weights. Missing or contradictory quantity evidence displays the reduction percentage as unavailable.

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
└── run-niuone-cron-scheduler.sh
```

Native runtime data is stored by default in:

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

Readiness also requires the complete current-day Shanghai/Shenzhen catalog. The `listing` field reports its date, row count, and `exchange_listing_unavailable` diagnosis. Verified exchange segments are retained for the day. If the Shenzhen workbook fails, the reader checks every page of the same exchange's JSON report against its date, totals, and unique codes. Missing catalogs remain eligible for bounded bootstrap retries even after daily bars have finished warming.

Midday and closing reports try bounded direct quotes first. The Tencent fallback covers Shanghai, Shenzhen, and Beijing stocks, including ST and new listings, and rejects snapshots with failed batches. Legacy AkShare calls run in child processes with parent-enforced deadlines before source failover.

Reports and scans share a lightweight listing worker and the same complete daily catalog. The catalog retains currently listed delisting-period securities; trading consumers apply their existing exclusions. Report quotes for Shanghai and Shenzhen are checked against that catalog so obsolete delisted quote aliases cannot inflate advance, decline, or flat counts.

The final **About** settings group shows the project author, GitHub repository, Apache License 2.0, current version, and newest Docker Hub release, with a **Check for updates** button that bypasses the server cache and refreshes the upstream result. **Automatically check for new versions** is enabled by default and takes effect at runtime; set `DASHBOARD_AUTO_VERSION_CHECK_ENABLED=0` in `dashboard.env` to disable it. “Do not remind me about this version” is stored only in the current browser; manually clicking the home-page version still checks again, and a later release can trigger a new reminder.

## 3. Model and Data-Source Configuration

NiuOne uses one shared model configured in the dedicated **Model Configuration** settings section. Trading decisions, AI prompt refinement, iWencai news judgment, A-share auction/midday/close summaries, and the overnight U.S. summary all use this model.

During upgrades, legacy `A_SHARE_MODEL_SUMMARY_*` model fields are used only when the shared configuration is incomplete. The next save in **Model Configuration** safely migrates usable legacy values into `DASHBOARD_DECISION_*` and removes the duplicate fields.

Core configuration items:

| Scenario | Configuration items |
|---|---|
| Shared model for trading decisions and market summaries | `DASHBOARD_DECISION_BASE_URL`, `DASHBOARD_DECISION_API_KEY`, `DASHBOARD_DECISION_MODEL`, `DASHBOARD_DECISION_STREAM_MODE`, `DASHBOARD_DECISION_REASONING_EFFORT`, `DASHBOARD_DECISION_CONTEXT_LENGTH`, `DASHBOARD_DECISION_MAX_TOKENS` |
| Built-in iWencai data source and news precheck | `IWENCAI_ENABLED`, `IWENCAI_NEWS_PRECHECK_ENABLED`, `IWENCAI_BASE_URL`, `IWENCAI_API_KEY`, `IWENCAI_TIMEOUT_SECONDS`, `IWENCAI_MAX_RETRIES`, `IWENCAI_MAX_CONCURRENCY`, `IWENCAI_CACHE_TTL_SECONDS`, `IWENCAI_DRAGON_TIGER_CRON` |
| Trading-decision intelligence bundle | `DASHBOARD_DECISION_INTELLIGENCE_ENABLED`, `DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS`, `DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` |
| Trading discipline for trading decisions | `DASHBOARD_TRADE_DISCIPLINE_TEXT`; when empty, the built-in default discipline is used; when populated, its content is inserted into the “Mandatory Rules” section of the model prompt |
| Simulated-account cadence and position-sizing references | `DASHBOARD_MAX_OPEN_POSITIONS`, `DASHBOARD_MAX_NEW_BUYS_PER_DECISION`, `DASHBOARD_MAX_SINGLE_POSITION_PCT`, `DASHBOARD_MAX_TOTAL_POSITION_PCT`, `DASHBOARD_MIN_CASH_RESERVE_PCT`; these are model references by default, while suites with registered hard limits, including Z-ge and Sector Tide, enforce the stricter global or suite limit in the simulation layer |

After administrator authentication, preferably use the dedicated **Model Configuration** section. It includes **Test Model Connection**. Tests use current form values without saving them; leaving the API key input empty reuses the saved secret. You can also edit `.local-data/dashboard.env` directly and wait for the next task cycle to load the values.
The shared model's `DASHBOARD_DECISION_REASONING_EFFORT` accepts a model- or gateway-defined token; leaving it empty omits the parameter. Known official models in the table below are checked locally before saving, connection tests, and runtime requests. Custom models and gateway aliases outside the table remain free-form. Connection tests use the current unsaved value. Success only confirms that the gateway accepted the request, not that the upstream enforced the effort. Unsupported parameters and invalid values receive targeted diagnostics, and runtime requests never silently remove the configured effort and retry.

The shared model's `DASHBOARD_DECISION_STREAM_MODE` accepts `auto`, `stream`, or `non_stream`. The default `auto` keeps the historical non-streaming request and retries with `stream=true` only when the gateway explicitly requires streaming. `stream` forces streaming and `non_stream` forces a complete response. Background jobs always assemble the complete streamed content before JSON validation, persistence, or trading decisions.
AI prompt refinement needs to show model output live in the browser, so `auto` preserves its existing streamed display when it reuses the shared model. Select `non_stream` to return that refinement as one complete response.

### Common model reasoning-effort table

These capabilities were verified on **2026-08-13**. “Accepted input” means values accepted by the official API; mappings show where a compatibility value does not take effect literally.

| Model | Accepted input | Effective levels / compatibility mappings | Default |
|---|---|---|---|
| Qwen `qwen3.8-max` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` | Responses has seven native levels; Chat natively has `low/medium/xhigh` and maps `minimal → low`, `high/max → xhigh`, and `none → off` | `xhigh` |
| Common Qwen 3.5–3.7, Qwen3 Max, and Qwen Plus/Flash/Coder Responses models | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` | `auto` preserves all seven levels through Responses; forced Chat maps `none` to off and all other values to on; `xhigh/max` are limited to Beijing and Singapore | `xhigh` |
| Other hybrid-thinking Qwen Chat models | `disabled`, `enabled` | Converted to the top-level `enable_thinking` switch; no multi-level effort | Off or on, depending on model |
| Qwen3.7 Max Preview, Qwen3 Thinking, and QwQ Plus | Leave empty only | Always-thinking models; reasoning cannot be disabled or tuned | Always on |
| MiniMax `MiniMax-M3` | `none`, `minimal`, `low`, `medium`, `high` | `none` disables thinking; every other value enables `adaptive` and does not tune reasoning depth | Chat: `adaptive`; Responses: `none` |
| MiniMax `MiniMax-M2` / M2.1 / M2.5 / M2.7, including highspeed | `none`, `minimal`, `low`, `medium`, `high` | Always reasons; Responses accepts compatibility values but cannot disable reasoning, and Chat sends no control field | Always on |
| DeepSeek `deepseek-v4-pro` | `low`, `medium`, `high`, `xhigh`, `max` | Effective: `high`, `max`; `low → high`, `medium → high`, `xhigh → max` | `high` |
| DeepSeek `deepseek-v4-flash` | `low`, `medium`, `high`, `xhigh`, `max` | Effective: `low`, `high`, `max`; `medium → high`, `xhigh → high` | `high` |
| Zhipu `glm-5.2` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` | Effective: off, `high`, `max`; `minimal → none`, `low/medium → high`, `xhigh → max` | `max` |
| Common Zhipu GLM 4.5–5.1 text/vision models | `disabled`, `enabled` | Native `thinking.type` switch; no multi-level effort | `enabled` |
| Xiaomi `mimo-v2.5` / `mimo-v2.5-pro` | `none`, `low`, `medium`, `high` | `none` disables thinking; `low/medium/high` currently have the same enabled behavior | Thinking enabled |
| xAI `grok-4.3` / `grok-4.3-latest` / `grok-latest` | `none`, `low`, `medium`, `high` | Same as input; `none` disables reasoning | Not stated on the official model page |
| xAI `grok-4.5` | `low`, `medium`, `high` | Same as input; reasoning cannot be disabled with this parameter | `high` |
| OpenAI `gpt-5.6` / `sol` / `terra` / `luna` | `none`, `low`, `medium`, `high`, `xhigh`, `max` | Same as input | `medium` |
| OpenAI `gpt-5.4-pro` | `medium`, `high`, `xhigh` | Same as input | `medium` |
| OpenAI `gpt-5.4` / `mini` / `nano` | `none`, `low`, `medium`, `high`, `xhigh` | Same as input | `none` |
| OpenAI `gpt-5.2-pro` | `medium`, `high`, `xhigh` | Same as input | `medium` |
| OpenAI `gpt-5.2` | `none`, `low`, `medium`, `high`, `xhigh` | Same as input | `none` |
| OpenAI `gpt-5.1` | `none`, `low`, `medium`, `high` | Same as input | `none` |
| OpenAI `gpt-5-pro` | `high` | Same as input | `high` |
| OpenAI `gpt-5` | `minimal`, `low`, `medium`, `high` | Same as input | Not stated on the official model page |

Sources: [Qwen Responses](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses), [Qwen Chat](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions), [Qwen deep thinking](https://help.aliyun.com/zh/model-studio/deep-thinking), [MiniMax Responses](https://platform.minimax.io/docs/api-reference/responses-create), [MiniMax Chat](https://platform.minimax.io/docs/api-reference/text-chat-openai), [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/), [Zhipu Thinking](https://docs.bigmodel.cn/cn/guide/capabilities/thinking), [Xiaomi MiMo Responses](https://mimo.mi.com/docs/en-US/api/chat/responses), [xAI Grok 4.3](https://docs.x.ai/developers/models/grok-4.3), [xAI Reasoning](https://docs.x.ai/developers/model-capabilities/text/reasoning), [OpenAI GPT-5.6](https://developers.openai.com/api/docs/models/gpt-5.6-sol), and the corresponding official model pages. Native Claude and Gemini APIs use different thinking-control fields and are therefore outside this compatibility table; compatible gateway values for those models remain custom inputs.
`*_CONTEXT_LENGTH` represents only the model context window and defaults to `128000`; `*_MAX_TOKENS` is the desired maximum output length and is mapped to `max_tokens` or `max_output_tokens` for the selected API. Known GPT-5.6 gateway aliases that reject the Responses output-limit parameter omit it, and other gateways receive one guarded retry without it when they explicitly report the parameter as unsupported. Both JSON and SSE responses are accepted, including gateways that force SSE when `stream=false`.
When the trading decision model exhausts its output budget and returns empty visible content or truncated JSON, NiuOne retries with a progressively doubled output limit up to an automatic ceiling of `65536`. An explicitly configured value above that ceiling is never reduced during retry. Once the ceiling is reached, the budget is no longer increased and an unparseable JSON response receives only one same-budget recovery attempt. DeepSeek V4 decision models known to support native JSON output also receive `response_format=json_object`; custom gateways are not forced to accept that parameter.
`IWENCAI_NEWS_PRECHECK_ENABLED` is disabled by default and is configured in **iWencai Data Source**. When enabled, it reuses `IWENCAI_*` for retrieval and `DASHBOARD_DECISION_*` for judgment. The official `announcement-search`, `news-search`, and `hithink-event-query` Skills retrieve evidence; announcement, news, and dated event fields are restricted to the same three-day window, identity-checked, and deduplicated. When evidence exists, the trading-decision model returns a structured positive, negative, or neutral judgment. No evidence is neutral without a model call. Missing model configuration, timeout, or invalid output makes the judgment unavailable and never falls back to keyword matching. Each Skill keeps its own status and non-sensitive error code; quotes and fund flows never count as news. Legacy `DASHBOARD_NEWS_*` settings are no longer read.

The iWencai data source is disabled by default. The **iWencai Data Source** settings include **Test iWencai Connection**, which uses current form values without saving settings or modifying snapshots. It validates the market-data Skill and, when news precheck is enabled in the form, also validates all three message Skills. After enabling the source and configuring an API key, the Dashboard exposes the purpose-built
`/api/iwencai/dragon-tiger?date=YYYY-MM-DD&page=1&limit=100` endpoint. It does not proxy arbitrary natural-language queries.
It caps each page at 100 stocks and uses the Dashboard's existing rate limits and cache. When news precheck is enabled, qualifying limit-up-streak or consecutive-listing stocks query the latest three days through all three message Skills. Checked state and the Skill-set version are persisted, so a completed same-day batch is not repeated and an upgraded Skill set invalidates older cached prechecks. Disabling the switch, missing configuration, or source failures never block the main snapshot and never fall back to a model.
The `/dragon-tiger` Dashboard section can query a selected trading date live. Current-day data and the most recent rolling snapshot remain public until the next successful query replaces that snapshot; earlier dates require the administrator password and a valid session. When a current-day live query is empty, the endpoint continues returning the most recent successful snapshot instead of replacing the page with an empty state before the new list is published. Every non-current-date response is excluded from public and CDN caching so the replaced date becomes protected immediately. Only a request matching the latest snapshot date reuses local data before an upstream query; other dates are not persisted. By default, Cron refreshes `.local-data/runtime/cron/output/iwencai_dragon_tiger_latest.json` at 18:00 China time on A-share trading days. The file retains only the most recent non-empty successful query and is atomically replaced by the next successful query. That refresh also removes legacy `iwencai_dragon_tiger/YYYY-MM-DD.json` archives; empty or failed main-list responses preserve the previous valid snapshot.

Administrator strategy backtests still prefer a complete Eastmoney industry/concept snapshot and reuse a validated stale snapshot when refresh fails. Only a cold start with no Eastmoney snapshot may use an enabled and keyed iWencai source to page through current A-share Tonghuashun industries and concepts. The fallback must pass upstream-count, paging, and unique-code completeness checks before it is written to the separate private `iwencai_stock_boards.json` cache. Results identify the actual classification source; if both sources fail, the backtest fails explicitly instead of continuing with empty classifications.

The trading-decision intelligence bundle is enabled by default. Each model decision after a stock-selection scan on the Practice page reads market monitoring, overnight U.S. market data, index quotes, sector performance, industry fund flows, trending stocks, candidate news, and an account-position summary. Users can also enable important Market Flash items in the Market Flash settings. The compressed `decision_intelligence` is written into the simulated-trading decision log. If a market-data source fails, its `source_status` is retained, and the current decision continues with available information and existing risk controls.

The canonical URL for the Practice page is `/practice`. The candidate query and refresh endpoints are `/api/practice_candidates` and `/api/practice_candidates/refresh`, respectively. Legacy links based on `?category=practice` or `?category=b1_screen`, plus the `/api/b1_screen` endpoint, are retained only as compatibility entry points.

### 3.1 Market Flash

`/realtime-news` is fetched server-side through NewsNow and requires no API key. Compose starts `ghcr.io/ourongxing/newsnow:latest` by default and uses the private `http://newsnow:4444/api/s` endpoint. The container publishes no host port and is brought up automatically even when only the Dashboard service is requested. Dashboard waits only for the NewsNow container to enter the started state, not for its health check, so later upstream failures do not take down the main service. The defaults are CLS Telegraph (`cls-telegraph`), Jin10 (`jin10`), and WallstreetCN Quick (`wallstreetcn-quick`). The Market Flash admin page provides search and multi-select for the 12 current sources in NewsNow's finance and business category. The Overview page reuses the same feed as a compact five-item vertical list in its lower-right area. By default, the list keeps only items marked important by the upstream source or local rules; the setting can disable that filter without changing the full Market Flash page. Browser reads and trading decisions share one process-local refresher; the server coalesces requests according to `NEWSNOW_REFRESH_SECONDS`, while NewsNow continues to enforce each source's registered upstream interval. Successful responses are merged into a local rolling history by item ID; the defaults retain up to 300 items, reserving priority capacity for up to 50 important items.

| Setting | Default | Allowed range | Application |
|---|---:|---:|---|
| `NEWSNOW_ENABLED` | `1` | `0` or `1` | Hot-applied |
| `NEWSNOW_DECISION_ENABLED` | `1` | `0` or `1`; use important items as trading-decision evidence | Hot-applied |
| `NEWSNOW_OVERVIEW_IMPORTANT_ONLY` | `1` | `0` or `1`; affects only the Overview strip | Hot-applied |
| `NEWSNOW_SOURCES` | `cls-telegraph,jin10,wallstreetcn-quick` | Any canonical source listed by the admin page; at least one | Hot-applied |
| `NEWSNOW_MAX_ITEMS` | `300` | `1`–`3000` items; total rolling-history limit | Hot-applied |
| `NEWSNOW_MAX_IMPORTANT_ITEMS` | `50` | `1`–`1000` items and no greater than the total limit | Hot-applied |
| `NEWSNOW_REFRESH_SECONDS` | `60` | `15`–`1800` seconds | Hot-applied |
| `NEWSNOW_TIMEOUT_SECONDS` | `10` | `2`–`30` seconds | Hot-applied |
| `NEWSNOW_MAX_RETRIES` | `1` | `0`–`2` | Hot-applied |
| `NEWSNOW_MAX_CONCURRENCY` | `3` | `1`–`3` | Hot-applied |

`NEWSNOW_DECISION_ENABLED` defaults on. The decision engine uses only items explicitly marked important upstream and carrying a reliable publication time. Items published before 15:00 on an A-share trading day belong to that day's intraday decisions; items published at or after 15:00 or on a non-trading day belong to the next trading day. Future-dated, ordinary, and unassignable items are excluded. The evidence retains source, publication time, target trading day, same-day/next-day role, and stale status. Market Flash may only assist BUY/SELL/HOLD judgments for existing candidates; it cannot create candidates, relax eligibility, or bypass position and risk controls. Deployments that explicitly saved the switch as off remain off.

Sources are fetched independently under bounded concurrency and timeouts. Successful results are merged with saved records by ID, preferring the new copy, then trimmed by time to `NEWSNOW_MAX_ITEMS`; up to `NEWSNOW_MAX_IMPORTANT_ITEMS` important items receive priority within that total capacity. Results are atomically stored in `.local-data/runtime/news/realtime_news_latest.json`. A failed source reuses only that source's saved history and is marked `stale/cache`; a full failure never overwrites the cache with an empty result. Bundled NewsNow state lives in the `newsnow-data` volume and follows `docker compose up/down` automatically; users configure no service URL. Selecting more sources increases first-refresh latency and upstream request volume, so deployments should enable only what they need. Operators can use `docker compose ps newsnow` and `docker compose logs newsnow` for diagnostics, and may optionally set `NEWSNOW_IMAGE` to pin the upstream version. The service needs outbound access to every selected provider, and deployments must follow each content provider's terms for display, storage, and redistribution.

### 3.2 Market Data and Fund-Flow Settings

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

The **Main Fund Flow** ranking on the indices page and the fund-flow animation share Eastmoney's industry-board **Today Main Net Amount** metric (`f62`, converted from yuan to CNY 100 million) and the same 60-second cache. New snapshots and samples are stored in `industry_main_money_flow_cache.json` and `industry_main_flow_history.json`, respectively. Legacy files based on total inflow minus total outflow are retained but are never mixed into main-net playback. Forced refreshes use three bounded requests with increasing backoff. If all attempts fail, the previous cache remains available, while the market-summary status also exposes a compact underlying reason so operators can distinguish timeouts, HTTP/TLS failures, and incomplete responses.

When `DASHBOARD_NIUONE_MAINLINE_MINUTE_REFRESH_ENABLED=1`, the same validated per-stock Tencent quote batch is handed to the Theme Strength calculator, so that page does not issue another Tencent request. The shared interval defaults to 30 seconds and is configurable through `DASHBOARD_MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS`. The fast calculator reads only private daily-K-line and industry-mapping caches, retains the Dragon-Tiger confirmation component from the newest complete research scan, and records quote time separately from calculation time. It never requests or consumes company news for theme recognition. Insufficient coverage, a stale quote timestamp, or a calculation failure retains the previous valid theme result.

The A-share market-sentiment chart on the indices page reads one Tencent Shanghai/Shenzhen full-market snapshot every 30 seconds by default. It uses the returned current, high, upper-limit, and lower-limit prices to count sealed limit-ups, sealed limit-downs, and broken limit-ups; positive and negative quote changes produce the red and green counts. Actual turnover in the lower panel primarily comes from the sum of Eastmoney one-minute turnover for the Shanghai Composite and Shenzhen Component. If that request fails or is stale, the service falls back to cumulative turnover from the Tencent full-market quote batch and exposes the selected source in the API and UI. Projected full-day turnover uses a two-stage method. From 09:30 through 09:34, today's only live input is the finalized 09:25 full-market auction amount. The estimate is `median historical full-day turnover × (today's auction amount ÷ median historical auction amount)^0.5`, using up to the latest 20 valid matched trading days. This square-root shrinkage prevents an out-of-range auction amount from propagating with one-for-one elasticity; intraday actual turnover is not an input during these five minutes, and fewer than 10 valid pairs suppresses the projection. From 09:35 onward it uses the latest 20 complete trading days of five-minute cumulative turnover distributions, dividing current cumulative turnover by the median same-time cumulative share; fewer than 20 complete days likewise suppresses the projection. The auction job stores only pre-09:27 structured samples covering at least 4,000 stocks, so post-open recovery runs cannot contaminate the factor. Projected increment is the signed difference between projected turnover and the latest complete previous trading day's full-day turnover; that comparison reference is independent of the active projection model's training samples, and every valid point on the same trading day uses the same comparison date. In addition to the complete current-day market-breadth samples, the history file retains only a compact actual cumulative-turnover curve from the latest prior trading day. Up to 600 aggregate samples are retained, enough for a complete trading day at the 30-second default. The API aligns today's and the previous day's actual turnover by trading progress and exposes their same-time difference: positive means expanding volume and negative means contracting volume. All turnover series use CNY 100 million. The API also exposes the active stage's source, sample range, sample count, and five-minute interval where applicable. The universe includes ST stocks and excludes B shares, Beijing Stock Exchange listings, and securities without a valid current price. The background sampler runs only on A-share trading days during 09:30–11:30 and 13:00–15:00 China time and stores real observations in `market_breadth_history.json`. Legacy observations without turnover or increment remain intact and render as gaps rather than synthetic zeroes. If one day contains observations from different projection models, the API hides only incompatible projected and increment fields while preserving real breadth, limit-state, and actual-turnover observations. An incomplete Tencent batch, insufficient turnover coverage, or failed request retains the previous valid history.

After the intraday 20-day turnover-distribution profile is built successfully for the first time, it is atomically saved as `cron/output/turnover_profile_cache.json` under the private runtime directory. A restarted Dashboard restores only an exact-current-trading-day cache whose model version and full structure validate, then recomputes the projection from the latest actual cumulative turnover. Cross-day, corrupt, or incomplete caches are not reused, and a failed upstream refresh never overwrites a valid saved profile.

After the Dashboard starts on an A-share trading day, it automatically checks today's market-sentiment curve. During the session, a background worker first waits for a new post-start observation as its recovery boundary, then finds every missing minute from 09:31 through that boundary, including both a missing prefix and an internal downtime gap. It starts the isolated recovery task under a cross-process lease only after at least three real observations later than the newest gap are available for cross-checking. A startup after the close checks through 15:00 and may validate a terminal gap against at least three nearest surviving real minutes. Recovery does not block service startup, an already complete curve is an idempotent no-op, and upstream failures receive at most three bounded retries. If fewer than three validation minutes exist, the service preserves the real records and does not weaken validation. Operators can also run `python3 app/entrypoints/recover_market_breadth_history.py` for a read-only recovery rehearsal. The command operates only on the current Beijing date. It obtains the same valid Tencent universe and daily price limits as the live sampler, reconstructs minute-boundary red, green, limit-up, limit-down, and cumulative-high broken-limit states from Tencent one-minute OHLC bars, and restores actual turnover from cumulative Shanghai and Shenzhen index minute amounts. Every valid security must produce a verified result, and at least three surviving same-minute live observations must cross-check the universe, all five sentiment series, and turnover within safe bounds; any coverage gap or excessive difference aborts the run. Per-security results are aggregated only in memory. A private checkpoint stores aggregate values and verified codes so an interrupted upstream run can resume. After the rehearsal passes, an explicit `--write` first backs up both the primary and recovery files under private `backups/`, then atomically merges recovered points while giving original observations precedence at identical timestamps. The automatic path invokes that same safe `--write` entrypoint. The command never interpolates, never fills gaps from the smaller B1 universe, and never backfills prior trading dates.

Industry fund-flow snapshots, samples, and the market-sentiment curve use 09:00 Asia/Shanghai as the display rollover. The prior calendar day's closing data remains visible after midnight through 08:59:59; at 09:00 the current display is cleared and waits for the new day's first valid sample. Market-sentiment history also retains one compact actual-turnover curve from the latest prior trading day. The Dashboard validates file dates at startup, and a resident background task atomically clears `industry_main_money_flow_cache.json` every day at 09:00 Asia/Shanghai while rolling `industry_main_flow_history.json` by each sample timestamp. Only samples outside the current display day are removed, so stale top-level metadata or mixed-day content cannot discard valid current-day observations. Every successful industry-flow sample first updates the atomic `industry_main_flow_history.recovery.json` mirror; startup merges current-day real samples from that mirror when the primary file is missing, damaged, or unexpectedly empty. Market breadth likewise maintains an atomic `market_breadth_history.recovery.json` mirror. Before startup rollover, daily rollover, or a new append, the service merges the primary and recovery files by sample timestamp, so a shorter same-day curve cannot replace a more complete retained real curve. After rollover, `market_breadth_history.json` discards the prior display day's breadth and limit-state fields and archives only its actual cumulative turnover. Related in-memory API caches are invalidated at the same time. If an upstream source still reports the previous day's timestamp after 09:00, the server rejects that snapshot instead of displaying or persisting it; the page remains empty until the first valid current-day sample arrives.

Legacy providers without HTTP timeout parameters run in isolated workers: sector rows have a 20-second hard deadline and the full hot-stock fetch has 45 seconds. Cross-process leases prevent duplicate fetches for the same cache. Timeouts terminate workers and release leases instead of leaving background threads. Sector data retains its existing Tencent industry-index fallback with a source warning. If every provider is unusable, retain explicitly stale snapshots instead of overwriting valid data with empty rankings.

Industry flows first use Eastmoney's official board-chart snapshot (`dataapi/bkzj/getbkzj`), checking board identities, total coverage, main net amounts (`f62`) and source times (`f124`). The paginated quote routes provide fallback within the same 30-second budget. Lunch-hour freshness is measured against the morning close; failed refreshes retain the last valid ranking with its source status. If prior-close daily index data is unavailable, the turnover comparison can use the actual sum of complete five-minute bars for both indexes on the exact previous exchange session.

### 3.3 Practice-Strategy Scheduling and Process Ownership

A Tencent forward-adjusted request can return only `day`. For this case, the warmer checks Sina's public forward-factor table for the same security: its count, baseline and date coverage must be valid, and every factor must be exactly one. Only then does it retain the original OHLCV. Otherwise it continues to explicitly adjusted feeds. Existing history-length and freshness checks still apply.

Prewarming measures valid daily bars against the exact Shanghai and Shenzhen target list. An intraday bar from the previous session needs a fresh fetch. The Shanghai main and STAR board listings and the Shenzhen A-share listing are fetched concurrently, checked against the exchange row counts, and shared for the day. Each scheduled scan keeps its own slot and signal timestamp. Managed scans publish their caches from one verified archive. Minute theme refreshes read historical bars in batches and rank all eligible securities together.

Scheduler polling reuses generation timestamps by file version. Joinable results live in `cron/output/b1_scan_join_result.json`. For strict-forward evaluation, archive the previous protocol and establish a new cohort boundary when switching to these changes; retain the original account and fill history.

If the primary daily-bar series is too short or out of date, prewarming checks the backup source using the same adjustment basis. When neither series qualifies, the existing history is retained and the symbol counts as a coverage gap. A scan with an explicit owner belongs to its original slot: an earlier or manual scan finishing in a new slot's minute leaves that slot eligible.

Strict-forward v18 aligns NiuOne opening capacity with the portfolio backtest: at most two first openings are permitted per Beijing trading date across Practice decision cycles. The execution layer reconstructs today's opened codes from persistent fill state and de-duplicates them by code; adds and other strategy suites do not consume the NiuOne allowance, while a further symbol is rejected as `position_capacity`. Both the value and counting rule are protocol-frozen.

Individual practice strategies do not own separate candidate-scan timers. At 09:10 on every A-share trading day, the Dashboard prewarms the latest 120 Tencent qfq daily bars for every supported non-ST stock into private SQLite. A cold deployment, lost volume, or expired cache no longer waits for that time window: bounded initialization begins immediately after service startup. A same-day retry after interruption fetches only missing symbols, and a failed response never replaces a successful series. Practice scans require 90% valid-date coverage by default. Dashboard-launched scans then read only valid local history and merge bulk live quotes; they do not issue per-symbol history fallbacks on the interactive path. When coverage is insufficient, a manual task queues behind initialization and shows the stage, completed count, and failures, while a scheduled task records a data-not-ready outcome and does not enter simulated trading with incomplete data.

At every configured time, the B1 scheduler inside the Dashboard first generates one unified **Current Market Summary and Evaluation** from live indexes, industry performance, industry main-fund flow, market breadth/turnover, and existing market scans, then starts the shared scanner. The full-market Tencent quote stage has a separate 90-second default aggregate budget so one slow upstream cannot consume the entire 480-second scan budget. The scanner reads `DASHBOARD_ACTIVE_STRATEGY` and runs only the scorers in that active suite. When that scan finishes, the scheduled path both passes the same artifact into model assessment and simulated execution checks and starts a background full-market Theme Strength research scan. The latter ignores `DASHBOARD_ACTIVE_STRATEGY`, updates only the dedicated theme cache, and cannot create candidates or trades. Multiple Dashboard instances that share one runtime directory use process leases to serialize prewarm and full-scan jobs, preventing duplicate scans and trades. Manual-task terminal state is persisted atomically; a restart marks unfinished work as interrupted and never replays trading automatically.

A complete scan continues to atomically replace `multi_strategy_latest.json` and the compatibility `b1_screen_latest.json`, but only `multi_strategy_history/` receives historical snapshots. After each successful scan, cleanup retires the duplicate `b1_history/` archive and bounds the primary archive to the latest archive date with at most 12 runs for that date. Cleanup recognizes only standard date directories and timestamped JSON files; unknown files, nested directories, and symbolic links remain untouched. It never removes latest caches, simulated-account state, durable SQLite trade/decision evidence, strict-forward reports, or scheduler state.

The Practice page no longer derives a separate market-evaluation label from B1 breadth thresholds. The summary artifact's `tone` / `tone_label` is both the displayed evaluation and the trading-context risk level; when the model is unavailable, the same module's local summarizer is used. Clicking **Generate Current Market Summary and Evaluation** or **Manually run candidate scan and trading strategy** refreshes this artifact, while scheduled refreshes reuse `DASHBOARD_PRACTICE_SCHEDULE_TIMES`. A failed generation preserves the latest valid same-day artifact instead of replacing it with an incomplete snapshot.

| Setting | Default | Scope | Application |
|---|---|---|---|
| `DASHBOARD_ACTIVE_STRATEGY` | `niuone` | New candidates, model prompt, and entry rules | Hot-applied; used by the next scan |
| `DASHBOARD_B1_SCHEDULE_ENABLED` | `1` | Starts the Dashboard's built-in candidate scheduler | Dashboard restart required |
| `DASHBOARD_PRACTICE_SCHEDULE_TIMES` | `09:25,10:00,10:30,11:00,11:20,13:00,13:30,14:00,14:30,14:50` | Practice summary/evaluation, active-strategy scan, and trading-decision times | Hot-applied; legacy `DASHBOARD_B1_SCHEDULE_TIMES` is read only for compatibility |
| `DASHBOARD_PRACTICE_FAST_CYCLE_ENABLED` | `0` | Enables the holdings-only fast cycle with the same strategy policy | Hot-applied; disabled by default |
| `DASHBOARD_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS` | `300` | Fast holding-cycle interval, from 60 through 900 seconds | Hot-applied |
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
| `DASHBOARD_NIUONE_FORWARD_COHORT_START` | `2026-09-08` | First-entry date admitted to the strict-forward cohort | Read by a subsequent Cron cycle; changing it requires a new protocol lock |
| `DASHBOARD_EXIT_FEEDBACK_AUTO_TUNE_ENABLED` | `1` | Enable bounded five-session post-exit tuning; set `0` to disable it | Read by the next post-close review |
| `DASHBOARD_EXIT_FEEDBACK_MIN_SAMPLES` | `30` | Minimum independent valid observation clusters, from 20 through 500 | Read by the next post-close review |
| `DASHBOARD_EXIT_FEEDBACK_MIN_MONTHS` | `3` | Minimum observation span in months, from 2 through 12 | Read by the next post-close review |
| `DASHBOARD_EXIT_FEEDBACK_COOLDOWN_SAMPLES` | `10` | New exit/re-entry outcomes required between evaluation checkpoints, from 5 through 100 | Read by the next post-close review |

If an existing deployment defines only `DASHBOARD_B1_SCHEDULE_TIMES`, the Dashboard continues to read that value. When both keys are present, `DASHBOARD_PRACTICE_SCHEDULE_TIMES` wins. The settings page exposes only the new key; its next save writes the new key and removes the legacy key from the local `dashboard.env` file.

The 09:25 scan falls in the quiet period after the opening auction. The system may generate candidates and model actions, but it does not book a fill at the auction reference price. Executable actions are queued, and after 09:30 the Dashboard's deferred-decision worker rechecks the session, current price, cash, and strategy risk budgets.

Users can click **Manually trigger candidate scan and trading strategy** on the Practice page to run the complete flow. It uses the same scanner, active-strategy setting, and execution layer as the scheduled path; it is not a force-fill or risk-bypass endpoint. A normal page refresh only reads cached and account state.

Every scheduled or manual B1 decision refreshes all open positions first and evaluates each position under the original exit rules identified by its stored `strategy_mark`; the active suite controls new candidates and BUYs only. SELL/HOLD checks continue when the candidate list is empty or the daily loss budget has fired, and that budget pauses new entries only.

When the holding fast cycle is enabled, the Dashboard loads only current positions at the configured interval during A-share execution sessions, rescoring them with the same active scorer set and latest valid theme/market context used by the complete scan. It then enters the same unified market summary, model-decision, exit-first, and execution-risk flow. The fast cycle never discovers a new code: BUY means add to a position that still exists both when the decision starts and when it executes, while first entries and same-cycle sell-then-rebuy attempts fail closed. A holding with missing live quotes or daily bars still receives the normal SELL/HOLD checks but cannot become a fast-cycle BUY candidate. Complete and fast cycles share the account-decision lock, so an overlap skips the fast cycle instead of waiting or writing the account concurrently.

Local automatic exits are also invoked by the separate Cron Scheduler process at dedicated times. Structural stops, Sector Tide deterioration, strategy time boxes, 2R, and 2 ATR remain discrete checks rather than tick-by-tick monitoring. Both the Dashboard and Cron Scheduler processes must be running for the full lifecycle.

The current NiuOne strict-forward cohort begins on `2026-09-08`. Every Cron Scheduler startup immediately runs a database-independent `--protocol-only` preflight, followed by a scheduled 09:05 check on weekdays. Both a normal startup and a late startup between 09:05 and 09:25 therefore freeze or verify `cron/state/niuone_forward_protocol.json` before the first Practice decision. Before the cohort starts, preflight also freezes a code-free zero-position account boundary; a missing, invested, or late baseline makes account return unattributable. The deterministic preflight gets one attempt, so a mismatch cannot stall unrelated scheduled jobs behind a five-minute retry. In addition to the latest 200 JSON rows retained for display, every simulated fill stores its complete payload idempotently in `niuniu.db`, so the opening BUY's theme/rank/industry/execution-gap snapshot, signal timestamp, schedule slot, scheduled/catch-up/manual/holding-fast origin, direct/deferred execution mode, sizing boundary, adds, and partial exits survive display-log trimming. Protocol v18 also stores each complete displayed opportunity set, canonical strategy identity, decision-pool eligibility, model-requested/maximum-permitted shares, and structured filter/rejection reasons; trading still consumes only explicit `trade_items`, so audit data cannot widen the pool. Deferred execution records inherit their original slot's opportunity set. The report deduplicates by scheduled slot or by non-scheduled origin and signal time, then profiles observed → eligible → model BUY → executed BUY conversion, sizing utilization, and consistency by all five lifecycle stages. A complete empty set is valid; missing fields, duplicate codes/ranks, or eligibility/blocker contradictions are not. At 15:15 on each actual A-share operating day, `--snapshot-equity` refreshes marks without trading and persists post-close account equity. At 15:20 the Scheduler read-only merges durable SQLite history with the recent JSON overlay and atomically refreshes the private `cron/output/niuone_forward_evaluation.json` report. State-only JSON rows remain a recovery overlay and cannot substitute for a durable entry payload or equity point. v18 also requires every NiuOne opening BUY to initialize a holding-stage path, every subsequent mainline scan to append or extend it, and an actual SELL to freeze the exit stage; a missing operating-day observation or any entry/path/exit time or stage mismatch keeps the lifecycle `data_quality_blocked`.

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

v32 requires NiuOne Leading, Resumption, and Launch to rank at or above the 60th percentile by turnover amount across the market and the 50th percentile inside the action-selected theme; missing amount evidence fails closed. Probe remains exempt but retains an activity warning. Amount weight in stock strength rises to 15% while 5-day relative-strength weight falls to 20%; neither market capitalization nor turnover rate is rewarded directly. Candidate cards expose the activity score and both amount percentiles, and opening fills plus opportunity sets persist the same evidence. Theme-context/dedicated-cache schemas are v13/v11, candidate-evidence schema is v2, and strict-forward/admin-backtest protocols are `niuone-strict-forward-v32`/`niuone-backtest-v33`; archive old locks, reports, and backtests before deployment.

v33 localizes internal enums only in user-facing strategy prose. Prompts now use Chinese lifecycle, role, and mainline-mode labels; persistence converts only standalone lowercase enums with explicit Chinese strategy context, including nested dropped-buy reasons. Capitalized proper names, English technical prose, errors, acronyms, and identifiers remain unchanged, while scoring, eligibility, sizing, and risk controls are identical. The display mapping joins the frozen source identity, the strict-forward protocol advances to `niuone-strict-forward-v33`, and the default new cohort begins on `2026-08-13`; archive the v32 lock and report before deployment and never pool the two evidence sets.

Administrator backtest v34 includes the terminal liquidation session after the signal window in the equity curve and risk metrics, and improves current-session timing plus ETA during long replays. NiuOne advances to `niuone-backtest-v34` and frozen prompt strategies advance to `prompt-backtest-v2`; older results become stale and must be rerun after upgrade. Strategy rules, fill precision, and capital calculations are unchanged.

v34 removes NiuOne morning/afternoon, per-decision, and per-day opening-count limits while fixing the book at five holdings. Full-book priority combines registered strategy certainty, current signal score, theme lifecycle/score, and strong-leader identity. The executor emits a full SELL before the BUY only when the candidate is strictly higher and the lowest-priority NiuOne holding is fully T+1 sellable. Strict-forward/admin-backtest advance to `niuone-strict-forward-v34`/`niuone-backtest-v35`, with a new default cohort on `2026-08-19`; archive old locks, reports, and backtests before deployment.

v35 adds score-ladder scaling for repeated BUY signals on the same name under the same strategy. The execution layer compares the new score with the highest score of every filled BUY in the current holding period and permits an add only on a strict new high; positions and durable fills retain the before/after score, high-water mark, and BUY count. Probe still cannot add on its entry day or average down, while mature paths retain the Markup, strong-leader, and 2%–12% profit-window gates. Stage upgrades, post-trim wave re-entry, and all risk budgets remain unchanged. Strict-forward/admin-backtest advance to `niuone-strict-forward-v35`/`niuone-backtest-v36`; the not-yet-started default cohort remains `2026-08-19`.

v36 fully decouples the current-market summary/evaluation from NiuOne opening counts. Dynamic holding counts, per-decision BUY counts, and pause fields in that shared context continue to govern non-NiuOne strategies only; NiuOne prompts, over-limit refinement, and execution use only the hard five-name book plus strict full-book replacement priority. Market context may still tighten per-trade, portfolio, and theme risk budgets, total exposure, and cash, while a candidate's own confirmed compound market hard stop and the independent daily-loss budget still block entries. Strict-forward advances to `niuone-strict-forward-v36`; admin backtest already uses the same capacity semantics and remains `niuone-backtest-v36`, with the default cohort still dated `2026-08-19`.

v37 gives failed news prechecks zero trading-decision weight. Failed, timed-out, unchecked, pending, or unavailable records remain visible for diagnostics but are omitted from decision news evidence and represented as neutral in candidate summaries. They cannot lower a score, priority, or size, or serve as a no-entry, HOLD, or SELL reason; completed positive, negative, and neutral judgments still participate normally. Because the model prompt is part of the frozen evidence chain, strict-forward advances to `niuone-strict-forward-v37`; admin backtest remains `niuone-backtest-v36`, and the not-yet-started default cohort remains `2026-08-19`.

v38 removes NiuOne's fixed two-Probe daily candidate cap and fixed two-position same-sector/same-theme cap. Screening, model prompts, execution rechecks, and administrator backtests may admit more qualified names, while the five-position ceiling, single-name, theme-risk, theme-exposure, portfolio-risk, total-exposure, cash, limit-up, and T+1 controls remain binding; Sector Tide's two-position industry rule is unchanged. Strict-forward/admin-backtest advance to `niuone-strict-forward-v38`/`niuone-backtest-v37`, with a new default cohort on `2026-08-21`; archive old locks, reports, and backtests before deployment.

v39 fixes the remaining pre-lunch count ambiguity in the shared market prompt: `max_open_positions`, the morning position count, and reserved afternoon slots apply only to non-NiuOne strategies. The model must not use them to call a NiuOne book full or HOLD before the five-position ceiling is reached. Strict-forward advances to `niuone-strict-forward-v39`, the administrator backtest remains `niuone-backtest-v37`, and the new default cohort starts on `2026-08-24`; archive the v38 lock and report before deployment and do not pool pre-fix and post-fix decision evidence.

v40 raises the NiuOne Probe absolute cap to 10% and raises both its rotation per-trade NAV-risk and theme-risk ceiling to 1%, preventing the previous 0.60% theme ceiling from silently clipping the requested budget. Holdings with cross-session Markup persistence, strong leadership, and 2%–12% unrealized profit receive deterministic local 10%/20% staged scale-in actions rather than depending on the model to emit ADD. An explicit model SELL remains exit-first, while the executor rechecks all single-name, theme, portfolio, total-exposure, cash, and T+1 boundaries. Strict-forward/admin-backtest advance to `niuone-strict-forward-v40`/`niuone-backtest-v38`; the default cohort remains `2026-08-24`. Archive the v39 lock, report, and older backtests before deployment.

v41 adds a holding fast cycle that is disabled by default. When enabled, it narrows only the incremental scoring universe to current positions while retaining the complete cycle's active scorers, unified market summary, model policy, exit-first ordering, and execution controls. A fast-cycle BUY is add-only for a position that still exists at execution; it cannot discover, open, or reopen a symbol. The durable run kind is `holding_fast`, and the switch plus the bounded 60–900-second interval enter the protocol fingerprint. Strict-forward advances to `niuone-strict-forward-v41`; the administrator daily-bar backtest remains `niuone-backtest-v38` because it does not replay minute trigger times. The new default cohort starts on `2026-08-27`; archive the v40 lock and report before deployment.

v42 aligns NiuOne's concurrent-holding ceiling with the configured `DASHBOARD_MAX_OPEN_POSITIONS`. Practice prompts, free-slot calculation, full-book priority replacement, and execution rechecks use the same startup value; administrator backtests snapshot it at submission, while strict-forward records the number and protects it through the runtime-settings fingerprint. `DASHBOARD_MORNING_MAX_OPEN_POSITIONS` and market-summary dynamic counts remain non-NiuOne-only; all other risk, exposure, cash, and T+1 controls are unchanged. Strict-forward/admin-backtest advance to `niuone-strict-forward-v42`/`niuone-backtest-v39`, and the default cohort remains `2026-08-27`; archive the v41 lock, report, and older backtests before deployment.

v43 unifies non-structural exits into a staged state machine: trim 50% first and require a later-session confirmation before closing the runner, while a 4–5 sell-fly score vetoes the first session. Structural stops, inactive themes, and market hard stops are not delayed. Full-book replacement requires a three-point priority advantage. The post-close mark refreshes SQLite 1/3/5/10-session exit observations and replacement-relative returns from local daily bars, retaining a diagnosable pending state when the sell-date baseline bar is missing. A five-session shadow watch permits reopening only from a full scan after price, volume, and the original theme thesis have recovered. Strict-forward/admin-backtest advance to `niuone-strict-forward-v43`/`niuone-backtest-v40`, with a new cohort on `2026-08-28`; archive v42 locks, reports, and older backtests before deployment.

v44 connects the five-session review to bounded tuning that is disabled by default. Once the configured sample-count and month-span gates pass, each new-observation batch may move at most one declared grid step. Only soft exits, replacement advantage, and post-exit re-entry confirmation can change; hard stops and all portfolio-risk limits remain frozen. Every fill binds a version, SQLite activates parameters atomically, and materially worse version-tagged outcomes roll back automatically. Strict-forward advances to `niuone-strict-forward-v44`, admin backtest remains fixed-default `niuone-backtest-v40`, and the new cohort starts on `2026-08-31`.
v45 upgrades feedback to v2. Mature observations cannot be downgraded by a short cache window or transient source failure; exit returns use the actual fill, replacement evidence requires an actual BUY, and both allowed and blocked re-entry decisions create direct five-session shadow outcomes. Tuning uses the latest 120 valid observations, clusters correlated same-security/same-day fills, weights by executed capital, and requires a 90% confidence interval to clear a minimum economic effect. Hold evaluations advance a separate cooldown checkpoint without creating parameter versions, and account loads reconcile against the active SQLite version. Structural stops and every portfolio-risk boundary remain frozen. Strict-forward advances to `niuone-strict-forward-v45`; the cohort still starts on `2026-08-31`.
v46 enables bounded five-session post-exit tuning by default. Deployments without an explicit setting enter learning or evaluation on the next post-close review, while operators can still set `DASHBOARD_EXIT_FEEDBACK_AUTO_TUNE_ENABLED=0` to opt out. Sample gates, bounded grids, automatic rollback, and every frozen risk boundary remain unchanged. Strict-forward advances to `niuone-strict-forward-v46`; the cohort still starts on `2026-08-31`.
v47 fixes feedback visibility before the first post-close review. The Practice page always renders the five-session review strip; a default-enabled deployment without a checkpoint shows learning and waiting-for-first-review status, while an explicit opt-out shows disabled. Stale account JSON can no longer override the current runtime switch. Strict-forward advances to `niuone-strict-forward-v47`.
v48 fixes the holding fast cycle's use of compact mainline-cache rows as complete scoring context. It combines fresh minute-level theme/market state with same-day full-scan stock profiles; cross-day, missing, or failed profiles remain fail-closed, and only scorer error types and counts are retained. Add and portfolio-risk gates are unchanged. Strict-forward advances to `niuone-strict-forward-v48`, with a new cohort beginning on `2026-09-03`.

v49 adds an execution-price gate to first or reopened Probe entries: the gain from a finite positive previous close must be strictly below 3%; invalid quotes also defer entry. Scan prices or percentages cannot substitute, and a later pullback may qualify. Existing-position adds, other entry routes, and exit/risk rules are unchanged. Production execution and the default admin backtest share a pure helper; daily replay uses the signal-day close and next-session execution price including slippage. Explicit research gap parameters keep their separately identified research semantics. Strict-forward/admin-backtest advance to `niuone-strict-forward-v49`/`niuone-backtest-v41`, with a new cohort on `2026-09-08`. Archive old locks, reports, and backtest results before deployment; preserve actual holdings and fills, and do not attribute account returns to the new version from an invested baseline. This gate is motivated by a recent losing segment and still requires future evidence; filtering the same sample does not prove improved win rate.

v50 unifies stock-activity eligibility for Probe, Launch, Leading, Resumption, and every add: actual cumulative same-day turnover must be at least 3%, market amount percentile at least 60, and amount percentile within the action-selected theme at least 50. Invalid required evidence defers BUY; Probe exemptions and optional legacy bypasses are removed. Execution rechecks quoted turnover, and holding fast cycles combine fresh turnover with the latest complete-scan amount ranks. Early sessions wait until 3% is reached; projected full-day turnover cannot substitute, and high turnover alone is not an entry signal. Opening-fill evidence adds `entry_turnover_pct`. The v49 first-Probe-entry price gain below 3% remains a separate rule. Daily replay screens on observed signal-day turnover, never next-session totals; next-open fills therefore approximate the intraday rule, and missing historical turnover rejects eligibility. Strict-forward/admin-backtest advance to `niuone-strict-forward-v50`/`niuone-backtest-v42`; the unstarted cohort remains `2026-09-08`. Archive prior locks, reports, and backtests before deployment while preserving account, holdings, and fill history. The 3% threshold implements the requested activity preference; future-cohort returns, drawdown, and opportunity counts must validate its effect rather than imply a guaranteed win-rate improvement.

v51 shares complete-trade accounting between Dashboard strategy performance and strict-forward evaluation. `trading/lifecycles.py` combines adds and partial exits into verified zero-to-zero lifecycles, attributes the first entry, and deducts every buy/sell fee. Open, orphan, and inconsistent cycles are excluded; breakeven belongs in the denominator but is not a win. Dashboard reads the full durable SQLite ledger plus recent state; unreadable history makes win rates unavailable. Profitable SELL fills are a separately named trigger metric. Decision-slot counts are scoped to the cohort, with failed-model, missing-payload, invalid-candidate, and missing-durable-record diagnostics; failed or intermediate exit records cannot stand in for completed decisions. Strict-forward advances to `niuone-strict-forward-v51`; admin backtest remains `niuone-backtest-v42`. The unstarted cohort remains `2026-09-08`; archive the v50 lock/report and preserve account history. See the preregistered [Probe chase forward comparison](strategies/PROBE_CHASE_FORWARD_EN.md).

After the NiuOne book reaches that ceiling, every newly selected buyable candidate is written to the Practice operation log and labeled as candidate-only, not bought, or replacement planned. This is decision-audit evidence rather than a fill or failed order; an actual replacement must still pass priority, T+1, and paired-fill preflight controls.

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

## 5. Long-Term Operation

### 5.1 Docker Deployment

Run the commands below from the directory used for the initial deployment. If production data lives in `niuone-data`, continue to use the same Compose project for every startup, restart, and upgrade. Do not start a native instance on the same port with `./run.sh` or `run.bat`; native `.local-data/` and the Docker volume are not synchronized automatically.

```bash
# After restarting the computer or container engine; also applies Compose,
# environment-variable, and port changes
docker compose up -d

# Restart current containers only; does not apply configuration or image changes
docker compose restart

# After source changes
./scripts/docker-build.sh
docker compose up -d --no-build

# After a Docker Hub image update
docker compose pull
docker compose up -d --no-build

# Status and health checks
docker compose ls
docker compose ps
docker compose port dashboard 8787
curl -s -o /dev/null -w 'healthz=%{http_code}\n' http://127.0.0.1:8787/healthz
curl -s -o /dev/null -w 'readyz=%{http_code}\n' http://127.0.0.1:8787/readyz
```

The `dashboard` service should be `healthy`, and `healthz` should return `200`. During first-time initialization, `readyz` may temporarily return `503` and should change to `200` once required data is ready.

The Compose project name may come from the working-directory name, `-p`, or `COMPOSE_PROJECT_NAME`, and it contributes to the physical volume name. Keep that project name stable after deployment; changing it or starting from a different checkout may create a new empty volume. Development and smoke tests must use a separate project name, port, and data volume rather than the production Compose project.

`docker compose down` preserves named volumes. `docker compose down -v` and `docker volume rm` delete real runtime data and must not be used for routine restarts, upgrades, or troubleshooting. See the [Standalone Operation Guide](STANDALONE_EN.md#docker-startup-restart-and-data-volumes) for the complete lifecycle, cross-platform port checks, and volume diagnostics.

### 5.2 Native Deployment

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
```

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

New market-monitoring records are written only to this database; Markdown files are no longer generated. Existing historical `.md` files from before the upgrade are preserved unchanged, but the page does not read or automatically delete them.

### The Page Shows an Older Simulated Account or Trading Calendar

First identify the browser's target port, the Compose project, and the authoritative data source. Docker uses the logical volume `niuone-data`, while native entrypoints use `.local-data/` in the checkout; these stores are not synchronized automatically.

Run on every platform:

```bash
docker compose ls
docker compose ps
docker compose port dashboard 8787
docker volume ls --filter label=com.docker.compose.volume=niuone-data
```

Check the port owner on macOS / Linux:

```bash
lsof -nP -iTCP:8787 -sTCP:LISTEN
```

Check the port owner in Windows PowerShell:

```powershell
Get-NetTCPConnection -LocalPort 8787 -State Listen |
    Select-Object LocalAddress, LocalPort, OwningProcess
Get-Process -Id (Get-NetTCPConnection -LocalPort 8787 -State Listen |
    Select-Object -First 1 -ExpandProperty OwningProcess)
```

For Docker, the `dashboard` service should be `healthy`, and Docker should publish the target port. If a Python process from the checkout owns it, stop that native instance and run `docker compose up -d` from the original deployment directory with the original Compose project name. If `docker compose ls` or the volume list shows multiple similar projects, identify which one contains the authoritative history before taking further action. Never replace a volume with an incomplete copy, and never use `docker compose down -v` for troubleshooting.

### Tasks Do Not Update Automatically

Check these three areas:

```bash
launchctl print gui/$(id -u)/ai.niuone.cron-scheduler | sed -n '1,100p'
tail -n 200 .local-data/runtime/logs/*.log
```

Also confirm that model keys and task schedules have been configured.

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


v52 deployment: after validation, advance to `niuone-strict-forward-v52`. Before the first 2026-09-08 cohort sample, archive the v51 lock/report privately and freeze v52. Preserve the account, ledger and probe comparison outcomes. If sampling has started, create a new cohort instead of overwriting its lock under the same start date. Compare protected cash, position quantities and retained-trade fingerprints; rebuild dashboard/scheduler and verify healthz, readyz and protocol integrity. Admin daily backtests remain v42. See [model exit evidence](strategies/README_EN.md#v52-require-observed-evidence-for-model-hard-exits).

v53 deployment: after validation, privately archive prior locks, reports and NiuOne replay results; set the new cohort to 2026-09-09, rebuild dashboard/scheduler, and verify healthz, readyz and the new protocol lock. Do not overwrite an already sampled cohort under its old date. Preserve the account, durable ledger and chase-shadow outcomes through archival, configuration and restart. An invested baseline still blocks new-version portfolio attribution. The backtest client/server use v43 and strict-forward uses v53. See [Probe theme attribution](strategies/README_EN.md#v53-require-valid-theme-attribution-for-probes).

v54 deployment: shared model admission, minute-scale 429 backoff and 180-second decision expiry join the protocol fingerprint. Hard exits remain independent. Original structure and cost protection are recorded separately; cost confirmation and the two entry variants remain offline research. After validation, privately archive v53 lock/report evidence and begin a new cohort on 2026-09-10, preserving account and trade records. Rebuild dashboard/scheduler and verify healthz, readyz and v54 integrity. Admin daily backtests remain v43. See [model reliability and risk comparisons](strategies/model-reliability-risk-study_EN.md).
