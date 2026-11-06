# Standalone Operation Guide

[简体中文](STANDALONE.md) | English

This guide is for users and developers running NiuOne on a personal computer or server. Native deployments store runtime data in `.local-data/` inside the project directory by default, while Docker Compose uses the named volume `niuone-data`. Both methods keep source code separate from real data, but they do not synchronize with each other automatically.

## One-Click Startup

```bash
cd /path/to/NiuOne
./run.sh
```

| System | Startup method |
|---|---|
| macOS | Run `./run.sh` in Terminal |
| Windows | Double-click `run.bat` or run it from CMD |
| Linux | Run `./run.sh` in a terminal |

On the first run, the script automatically:

- Creates `.local-data/`
- Creates `.local-data/.venv`
- Installs `requirements.txt`
- Builds the Vue 3/Vite frontend under `web/` from locked dependencies
- Generates `.local-data/dashboard.env`
- Initializes the log, database, and task output directories under `.local-data/runtime/`

After startup, visit:

```text
http://127.0.0.1:8787/
```

The dashboard home page and displayed data remain publicly accessible, while the settings page and administrative APIs always require administrator authentication. On the first startup, use the bootstrap administrator key generated automatically by the service to enter the settings page. Its path is `$DASHBOARD_HOME/dashboard_admin_token.txt`, which defaults to `.local-data/runtime/dashboard_admin_token.txt`. After signing in, you can set an administrator password under “Access Control.” The new password takes effect immediately and invalidates existing sessions. Alternatively, before startup, edit `.local-data/dashboard.env`, whose permissions are `0600`, and set `DASHBOARD_ADMIN_PASSWORD` directly. Do not pass passwords through command-line arguments.

You can also specify the dashboard port during one-click startup. The script saves it to `.local-data/dashboard.env`:

```bash
./run.sh --port 8877
```

Windows:

```cmd
run.bat --port 8877
```

### First-Install Timeouts in Mainland China

If the first run reports a connection or read timeout during `pip install`, the current network may have unreliable access to PyPI; this does not indicate a missing project dependency. Before running `run.bat`, configure a user-level pip mirror and bounded request timeout and retry values. The following example uses the [Tsinghua Open Source Mirror](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/):

```cmd
python -m pip config --user set global.index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
python -m pip config --user set global.timeout 60
python -m pip config --user set global.retries 10
python -m pip config debug
```

If only the Python Launcher is available, replace `python` with `py -3`. These commands write the following equivalent configuration to the user-level `%APPDATA%\pip\pip.ini`:

```ini
[global]
index-url = https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
timeout = 60
retries = 10
```

After `pip config debug` shows the expected settings, run `run.bat` again. You may use another trusted HTTPS mirror that is reachable from your network; do not bypass certificate verification with `trusted-host` or HTTP. See the [official pip configuration documentation](https://pip.pypa.io/en/stable/topics/configuration/) for configuration file locations and precedence.

The public page and complete settings UI use one FastAPI/Uvicorn process and port, at `8787/` and `8787/admin` by default. Vite's port `5173` is only for local hot reload and is not part of production deployment. The settings page may be accessed through the domain, while configuration and action APIs still require an administrator session. See [Dashboard Incremental Delivery and Deployment](DASHBOARD_V2_EN.md) for snapshot and CDN guidance.

## Isolated Startup

For debugging or acceptance testing, use a separate port and a temporary runtime directory to avoid affecting real data:

```bash
cd /path/to/NiuOne
DASHBOARD_HOME=/tmp/niuone-smoke DASHBOARD_PORT=8877 ./scripts/run_standalone.sh
```

Visit:

```text
http://127.0.0.1:8877/
```

`scripts/run_standalone.sh` does not create a Python virtual environment, but it builds the Vue frontend when needed. It is intended for development or validation environments where Python, Node.js, and dependencies are already available.

On Windows, PowerShell can run an isolated instance using a temporary data directory:

```powershell
cd C:\path\to\NiuOne
$env:NIUONE_LOCAL_DATA_DIR = Join-Path $env:TEMP "niuone-smoke"
.\run.bat --port 8877 --no-browser
```

After testing, stop the process and delete `$env:TEMP\niuone-smoke` if needed.

Developers may also use a separate Compose project for integration or smoke testing. First confirm that the example port `8877` is free, then run:

macOS / Linux:

```bash
NIUONE_PORT=8877 docker compose -p niuone-smoke up -d --build
docker compose -p niuone-smoke ps
```

Windows PowerShell:

```powershell
$env:NIUONE_PORT = "8877"
docker compose -p niuone-smoke up -d --build
docker compose -p niuone-smoke ps
Remove-Item Env:NIUONE_PORT
```

After testing, remove only this explicitly named temporary project:

```bash
docker compose -p niuone-smoke down -v
```

`-p niuone-smoke` isolates containers, networks, and data volumes. Use a unique project name and free port for parallel tests. `down -v` is appropriate here only for a temporary project that is known to contain no production data; never run it against a production project.

## Docker Startup, Restart, and Data Volumes

The commands below apply to the Compose configuration included with the project and should be run from the directory used for the initial deployment. Keep each production instance on either Docker or native entrypoints. Once Docker contains the authoritative history, manage that instance only through Compose. Developers may run isolated instances in parallel, but every instance needs a distinct port, Compose project name, and data volume.

| Scenario | Command | Preserves `niuone-data` |
|---|---|---|
| Restore after a computer or container-engine restart | `docker compose up -d` | Yes |
| Restart every container | `docker compose restart` | Yes |
| Restart only the Dashboard and scheduler | `docker compose restart dashboard scheduler` | Yes |
| Apply Compose, environment-variable, or port changes | `docker compose up -d` | Yes |
| Update after source changes | Run `./scripts/docker-build.sh`, then `docker compose up -d --no-build` | Yes |
| Update a Docker Hub image | Run `docker compose pull`, then `docker compose up -d --no-build` | Yes |
| Stop and remove containers | `docker compose down` | Yes |
| Follow service logs | `docker compose logs -f` | Yes |

`docker compose restart` only restarts the current containers; it does not read updated Compose configuration, environment variables, ports, or images. Use the corresponding `up -d` command when any of those values changes. Compose services use `restart: unless-stopped`, but the container engine must be running first. Docker Desktop users may enable launch at sign-in, while Linux administrators should ensure that the Docker service starts at boot.

Verify every restore or upgrade with:

```bash
docker compose ls
docker compose ps
docker compose port dashboard 8787
curl -s -o /dev/null -w 'healthz=%{http_code}\n' http://127.0.0.1:8787/healthz
curl -s -o /dev/null -w 'readyz=%{http_code}\n' http://127.0.0.1:8787/readyz
```

The `dashboard` service should be `healthy`, and `healthz` should return `200`. During first-time initialization, `readyz` may temporarily return `503` and should change to `200` when required data is ready. Check which process owns the default port:

macOS / Linux:

```bash
lsof -nP -iTCP:8787 -sTCP:LISTEN
```

Windows PowerShell:

```powershell
Get-NetTCPConnection -LocalPort 8787 -State Listen |
    Select-Object LocalAddress, LocalPort, OwningProcess
Get-Process -Id (Get-NetTCPConnection -LocalPort 8787 -State Listen |
    Select-Object -First 1 -ExpandProperty OwningProcess)
```

A Docker deployment should show that Docker publishes port `8787`. If a Python process from the checkout owns it, stop that native instance before running `docker compose up -d`.

The Compose project name may come from the working-directory name, `-p`, or `COMPOSE_PROJECT_NAME`, and it contributes to the physical volume name. Do not casually change the project name or start the deployment from a different checkout after the first deployment, because Compose may create a new empty volume. If history suddenly appears empty, identify the project and logical volume before copying or rebuilding any data:

```bash
docker compose ls
docker volume ls --filter label=com.docker.compose.volume=niuone-data
```

> Do not use `docker compose down -v` or `docker volume rm` for routine restarts, upgrades, or troubleshooting. `niuone-data` is a logical volume name; the physical volume name usually has a Compose project prefix. Deleting it removes configuration, databases, the simulated account, and history. Ordinary `docker compose down`, `restart`, and `up -d` operations preserve named volumes.

## Model and Data-Source Configuration

Cron Scheduler refreshes existing industry and concept classifications at 16:40 Beijing time on trading days. Configure another after-close time with `DASHBOARD_MARKET_CLASSIFICATION_CRON` in Settings; it applies to the next run. See the [operations manual](OPERATIONS_EN.md#32-market-data-and-fund-flow-settings).

NiuOne uses large language models for market summaries and trading decisions.

Recommended configuration:

| Scenario | Recommended model or data source | Main configuration items |
|---|---|---|
| Trading decisions, prompt refinement, news judgment, and A-share/overnight U.S. summaries | One shared OpenAI-compatible model | `DASHBOARD_DECISION_BASE_URL`, `DASHBOARD_DECISION_API_KEY`, `DASHBOARD_DECISION_MODEL`, `DASHBOARD_DECISION_STREAM_MODE`, `DASHBOARD_DECISION_REASONING_EFFORT`, `DASHBOARD_DECISION_CONTEXT_LENGTH`, `DASHBOARD_DECISION_MAX_TOKENS` |
| iWencai dragon-tiger research data and news precheck | Tonghuashun iWencai OpenAPI | `IWENCAI_ENABLED`, `IWENCAI_NEWS_PRECHECK_ENABLED`, `IWENCAI_BASE_URL`, `IWENCAI_API_KEY`, `IWENCAI_TIMEOUT_SECONDS`, `IWENCAI_MAX_RETRIES`, `IWENCAI_MAX_CONCURRENCY`, `IWENCAI_CACHE_TTL_SECONDS`, `IWENCAI_DRAGON_TIGER_CRON` |
| Trading-decision intelligence bundle | Aggregated locally; no additional model required | `DASHBOARD_DECISION_INTELLIGENCE_ENABLED`, `DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS`, `DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` |

Reasoning effort remains manually editable and is omitted when left empty. Known common models are checked against a local capability table before saving, manual tests, and runtime requests; custom models outside the table remain free-form. The request layer converts fields for the documented Qwen, MiniMax, GLM, and MiMo protocols and shows mappings where a compatibility value is not a real effort level. The settings-page **Common model reasoning-effort table** and the [operations manual](OPERATIONS_EN.md#common-model-reasoning-effort-table) list current values and compatibility mappings.

**Market Flash** requires no model, API key, or service-URL configuration. Compose starts, stops, and restores the official NewsNow container together with NiuOne, while the Dashboard reads it over the private container network; users manage no separate port or process. NewsNow persists its state in the separate `newsnow-data` volume. The admin page provides search and multi-select access to the 12 current sources in the finance and business category, with CLS Telegraph, Jin10, and WallstreetCN Quick enabled by default. The Overview page shows the latest five items as a vertical list in its lower-right area and defaults to important items only; disabling **Show only important information in Overview** includes ordinary items without changing the full Market Flash page. Native `run.sh` / `run.bat` deployments also require no configuration and automatically use the public-service fallback when no container sidecar is running. The browser receives only the normalized same-origin `/api/realtime-news` response. Successful refreshes merge by ID into a bounded rolling history of 300 items by default, with priority retention for up to 50 important items; on upstream failure, the Dashboard reuses that saved history from `.local-data/runtime/news/realtime_news_latest.json` and marks it as cached.

After startup, use the dedicated **Model Configuration** section for the one shared model; trading decisions and market monitoring no longer configure models separately. This section includes **Test Model Connection**. Tests use current form values without saving them and reuse the saved secret when the API-key input is empty.
The shared model's `DASHBOARD_DECISION_STREAM_MODE` defaults to `auto`: it normally uses non-streaming and switches only when the gateway explicitly requires `stream=true`; set `stream` or `non_stream` to force either transport. Streamed content is assembled completely before validation and use.
AI prompt refinement reuses the shared model. Because that interactive flow displays output live in the browser, it remains streamed in `auto`; select `non_stream` to return one complete response instead.
`DASHBOARD_DECISION_CONTEXT_LENGTH` represents only the model context window and defaults to `128000`; `DASHBOARD_DECISION_MAX_TOKENS` is the desired maximum output length and is mapped to a compatible Chat or Responses parameter. Both JSON and SSE responses are supported.
`IWENCAI_NEWS_PRECHECK_ENABLED` is disabled by default and can be enabled in **iWencai Data Source**. The official `announcement-search`, `news-search`, and `hithink-event-query` Skills retrieve evidence, while `DASHBOARD_DECISION_*` classifies filtered, identity-checked, deduplicated evidence as positive, negative, or neutral. No evidence is neutral without a model call. Model failure makes judgment unavailable and never falls back to keyword matching. Quotes and flows never count as news. Legacy `DASHBOARD_NEWS_*` settings are no longer read.
The iWencai source is disabled by default. **iWencai Data Source** includes **Test iWencai Connection** and the optional news-precheck switch. The button validates the market-data Skill and, when the switch is enabled in the form, all three message Skills. Qualifying limit-up-streak or consecutive-listing stocks are checked through iWencai only; disabling the switch skips prechecks completely. Source failures do not block or overwrite the main dragon-tiger snapshot. The key remains only in the private local `dashboard.env` and is never echoed by the page.

Administrator strategy backtests prefer a complete Eastmoney industry/concept snapshot and may reuse a validated stale snapshot. On a cold start with no Eastmoney snapshot, an enabled and keyed iWencai source can page through current A-share Tonghuashun industries and concepts as a fallback. Only a result that passes upstream-count and unique-code completeness checks is stored in the separate private cache and used by a backtest. The result identifies its actual source, and both sources failing never produces a fabricated empty-classification result.

The trading-decision intelligence bundle is enabled by default. It adds market monitoring, overnight U.S. market data, indexes/futures, sector performance, industry fund flows, trending stocks, candidate news, and an account-position summary to every simulated-trading decision prompt and log. If an individual market-data source fails, only its status is recorded; the failure does not block the current decision cycle.

## Runtime Files

By default, runtime data is stored in:

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

`.local-data/` is ignored by `.gitignore`. Do not commit its databases, local credentials, logs, model configuration, or task output to Git.

## Key Configuration Items

| Configuration item | Default | Description |
|---|---|---|
| `DASHBOARD_HOME` | `.local-data/runtime` | Root directory for runtime data |
| `DASHBOARD_HOST` | `127.0.0.1` | Listening address |
| `DASHBOARD_PORT` | `8787` | Listening port |
| `NEWSNOW_DECISION_ENABLED` | `1` | Use important Market Flash items as decision evidence; after-close and non-trading-day items belong to the next session; hot-applied |
| `NEWSNOW_OVERVIEW_IMPORTANT_ONLY` | `1` | Keep only important items in the Overview strip; hot-applied |
| `NEWSNOW_SOURCES` | `cls-telegraph,jin10,wallstreetcn-quick` | Market Flash sources, separated by commas |
| `NEWSNOW_MAX_ITEMS` | `300` | Total rolling-history limit from 1 through 3000; hot-applied |
| `NEWSNOW_MAX_IMPORTANT_ITEMS` | `50` | Important-item limit from 1 through 1000 and no greater than the total; hot-applied |
| `NEWSNOW_REFRESH_SECONDS` | `60` | NiuOne local refresh interval, from 15 through 1800 seconds; hot-applied |
| `DASHBOARD_ADMIN_PASSWORD` | Empty | Administrator password for the settings page; when empty, the bootstrap administrator key in `$DASHBOARD_HOME/dashboard_admin_token.txt` is used |
| `PYTHON_BIN` | `.local-data/.venv/bin/python` or the Windows venv Python | Python executable |
| `DASHBOARD_CONFIG` | `$DASHBOARD_HOME/config.yaml` | YAML configuration for model providers and models |
| `DASHBOARD_PUSH_HISTORY_DB` | `$DASHBOARD_HOME/push_history.db` | Message history database |
| `DASHBOARD_PORTFOLIO_STATE` | `$DASHBOARD_HOME/cron/output/niuniu_practice_portfolio.json` | Simulated-account state |
| `DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON` | `5 9 * * 1-5` | Verify the strict-forward protocol immediately at Scheduler startup and again at 09:05 Monday through Friday |
| `DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON` | `15 15 * * 1-5` | Refresh marks without trading and persist account equity after each actual A-share session |
| `DASHBOARD_NIUONE_FORWARD_CRON` | `20 15 * * 1-5` | Recompute the NiuOne strict-forward report from the durable fill ledger after each Monday-through-Friday session; applies on the next Cron cycle |
| `DASHBOARD_NIUONE_FORWARD_COHORT_START` | `2026-09-03` | Strict-forward cohort start; archive the old protocol lock and restart from a new trading day after a rule change |
| `DASHBOARD_EXIT_FEEDBACK_AUTO_TUNE_ENABLED` | `1` | Enable bounded five-session post-exit tuning by default; set `0` to disable it on the next post-close review |
| `DASHBOARD_EXIT_FEEDBACK_MIN_SAMPLES` | `30` | Required independent valid five-session clusters, from 20 through 500 |
| `DASHBOARD_EXIT_FEEDBACK_MIN_MONTHS` | `3` | Required observation span in months, from 2 through 12 |
| `DASHBOARD_EXIT_FEEDBACK_COOLDOWN_SAMPLES` | `10` | New exit/re-entry outcomes required between evaluation checkpoints, from 5 through 100 |
| `DASHBOARD_ACTIVE_STRATEGY` | `niuone` | Active independent strategy; changes apply to the next scan without a restart |
| `DASHBOARD_PRACTICE_SCHEDULE_TIMES` | `09:25,10:00,10:30,11:00,11:20,13:00,13:30,14:00,14:30,14:50` | Shared schedule for market summaries, screening, and simulated decisions |
| `DASHBOARD_PRACTICE_FAST_CYCLE_ENABLED` | `0` | Enable same-policy rescoring of current holdings only; hot-applied and disabled by default |
| `DASHBOARD_PRACTICE_FAST_CYCLE_INTERVAL_SECONDS` | `300` | Holding fast-cycle interval, from 60 through 900 seconds; hot-applied |
| `DASHBOARD_KLINE_BOOTSTRAP_ENABLED` | `1` | Prepare full-market daily K lines immediately after a first deployment or cache expiry; requires a restart |
| `DASHBOARD_KLINE_READINESS_MIN_COVERAGE_PERCENT` | `90` | Valid-date daily-K-line coverage required to admit a Practice scan, from 90 through 100; requires a restart |
| `DASHBOARD_TENCENT_QUOTE_STAGE_TIMEOUT_SECONDS` | `90` | Aggregate budget for the full-market live-quote stage, from 15 through 300 seconds; requires a restart |
| `DASHBOARD_MANUAL_DATA_INITIALIZATION_TIMEOUT_SECONDS` | `660` | Maximum seconds a manual task waits for daily-K-line initialization; requires a restart |
| `DASHBOARD_DECISION_INTELLIGENCE_ENABLED` | `1` | Whether to enable the global intelligence bundle for trading decisions |
| `DASHBOARD_TRADE_DISCIPLINE_TEXT` | Empty | Trading-discipline text for the trading-decision prompt; the built-in default discipline is used when empty |
| `DASHBOARD_MAX_TOTAL_POSITION_PCT` | `80` | Global total-exposure cap; `zettaranc` and `sector_tide` enforce the stricter of the global limit and the strategy-suite hard cap, while other suites mainly use it as model guidance |
| `DASHBOARD_MIN_CASH_RESERVE_PCT` | `20` | Global cash buffer; `zettaranc` and `sector_tide` also enforce it at execution time, while other suites mainly use it as model guidance |
| `DASHBOARD_NIUONE_MAINLINE_MINUTE_REFRESH_ENABLED` | `1` | Reuse the full-market quote sample to refresh Theme Strength; requires a restart |
| `DASHBOARD_MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS` | `30` | Shared full-market interval for Theme Strength and Market Sentiment, from `30` through `600` seconds; requires a restart |
| `DASHBOARD_AUTO_VERSION_CHECK_ENABLED` | `1` | Check Docker Hub for a newer release on page load; applies at runtime and never installs an update automatically |

After settings are saved, configurations that support hot application are used immediately for subsequent requests. Restart the local service for configurations that require a restart.

When the holding fast cycle is enabled, the Dashboard narrows only the incremental scoring universe to current positions while retaining the complete cycle's active scorers, unified market summary, model decision, exit-first ordering, and execution controls. It never discovers a new symbol: BUY is add-only for a position that still exists at execution, while first entries and same-cycle sell-then-rebuy attempts fail closed. Missing live quotes or daily bars remove fast-cycle BUY eligibility without disabling normal SELL/HOLD checks. Fast and complete cycles share the account-decision lock, so an overlap skips the fast cycle.

## Independent Processes and Long-Term Operation

A complete background deployment generally consists of two independent processes:

| Process | macOS / Linux entry point | Windows entry point | Required? |
|---|---|---|---|
| Dashboard | `run-dashboard.sh` | `run.bat --no-browser --skip-install` | Yes |
| Scheduled-task scheduler | `run-niuone-cron-scheduler.sh` | `.local-data\.venv\Scripts\python.exe app\entrypoints\niuone_cron_scheduler.py` | Required for automatic summaries, database writes, or simulated-position automatic-exit checks |

The live B1 stock-selection schedule runs inside the Dashboard process. Before each scheduled trading decision, it synchronously generates the unified **Current Market Summary and Evaluation**, whose risk label becomes the Practice trading context. The page button and the manual candidate-scan/trading flow use the same generator. The scheduled-task scheduler does not select stocks, but at startup and again at 09:05 on weekdays it freezes or verifies the strict-forward protocol and the pre-cohort zero-position account boundary before the first 09:25 decision. It then runs independent automatic-exit checks, takes a no-trade post-close equity snapshot at 15:15, and derives the private NiuOne strict-forward report at 15:20 from complete `niuniu.db` fills, observed opportunity sets, daily equity, and decision payloads plus the recent JSON log. Protocol v18 requires both an `ok` terminal state and structurally complete SQLite decision evidence for every Practice slot. Deferred execution retains the original slot's candidate denominator, and the report presents observed, eligible, model-BUY, executed-BUY, sizing-utilization, and rejection-category evidence by all five stages. Persistence or schema-validation failure fails that slot or automatic-exit task. The frozen fingerprint covers all three forward Cron expressions, effective durable-database/recovery-state/operational-audit/exchange-calendar paths, and the scheduling/storage/evaluation source chain; path values are stored only as digests, and `--as-of` cannot alter the actual lock date. The 30-trade or three-full-month sample gate becomes reviewable only when every completed lifecycle has complete entry attribution and every actual A-share operating day has a pre-first-slot preflight, all Practice slots and durable decision rows, both exit checks, the post-close equity snapshot, and forward evaluation recorded as successful; without a trustworthy exchange calendar the system conservatively falls back to weekdays. Three elapsed months with fewer than 30 completed lifecycles permit only a frequency/operations review. A final high-win-rate and positive-return claim additionally requires at least 30 trades and must pass the frozen historical-reference, trade-level Wilson 95% lower bound, minimum unique and Herfindahl-effective entry-date-by-industry cluster counts, cluster-balanced win rate and its 95% lower bound, fee-inclusive lifecycle-return, NiuOne-only account attribution, positive portfolio return, maximum-drawdown-at-most-6%, return-to-drawdown-at-least-1, operations, and opportunity-funnel gates. Same-date, same-industry fills add only one unique cluster. Missing attribution yields `data_quality_blocked`; missing operations yields `operations_blocked`. A code or locked-setting change also blocks cohort advancement until the old report/lock is archived and a new `DASHBOARD_NIUONE_FORWARD_COHORT_START` begins. Both processes must stay running for the full protocol-preflight-selection-summary/evaluation-decision-exit-equity-snapshot-forward-attribution lifecycle. v18 also records the holding-stage path from the first BUY through each mainline scan and freezes the exit stage only on an actual SELL; a missing operating-day observation or a path that does not align with entry and exit prevents manual-review eligibility.

Executed BUYs in the v20 funnel come from the durable fill ledger and are reconciled against execution copies in decision payloads; inconsistencies block manual-review eligibility. NiuOne first openings are accumulated across Practice decision cycles by Beijing trading date and capped at two per day; adds and other strategy suites are excluded, and the historical portfolio backtest uses the same shared rule.

When a NiuOne BUY has passed every other hard check and only its model quantity exceeds a positive whole-lot risk ceiling, v18 reduces the executed quantity to that ceiling and records the model request, actual fill, ceiling, and reduction flag. A zero ceiling or any eligibility, capacity, or input failure still rejects the order. This recovers otherwise-safe orders from small sizing errors without increasing any position or risk limit.

When a model-directed NiuOne SELL exceeds a positive whole-lot T+1 available quantity, v18 executes the available quantity and records the model request, availability at execution, actual fill, and reduction flag for post-close validation. If reduction is needed, zero or non-round-lot availability still rejects; local automatic exits and other strategy suites are unchanged.

v18 fixed the NiuOne Probe daily-V recovery ratio at `[0.60, 2.00)` and applies the same boundary during scoring and the pre-fill recheck. The protocol lock records both bounds; v20 freezes the strict-forward historical reference win rate from the new production candidate at 59.71%.

v18 also freezes Markup quality: NiuOne Leading must be both top-20% within its mainline and backed by same-day theme strength of at least 60. NiuOne Launch accepts only a cross-day-persistent `emerging` theme; a confirmed `mainline` must use Leading. Scoring and the pre-fill recheck share these fail-closed rules.

v18 also freezes Probe continuation quality: a theme must have at least six strong stocks or a Brewing-state streak of at least three trading days. Up to two qualified Probes may be retained per day and the absolute single-name cap is 6.25%, while per-trade equity risk remains 0.35%/0.30%/0.25%.

v20 defines 6.25% as the Brewing Probe cap. A Probe- or Launch-origin position with 2%–12% unrealized profit may add once toward a 10% cap when its emerging mainline persists across sessions, remains in Markup, and the stock stays in the strong Leading tier. Once the mainline is fully confirmed it may add once more toward a 20% cap. Risk sizing, theme/portfolio capacity, cash, and the stage cap may bind earlier. Profit above 12% is not chased; Climax, Divergence, and Fade never authorize an add. The first non-losing Climax observation trims one third once, while the existing partial-profit, breakeven, and 2 ATR trailing rules remain active.

v21 enables repeatable wave rebalancing after Leading confirmation instead of imposing a lifetime add count. The position releases one third after either a 1 ATR decline from the cycle's closing-price peak or three sessions without a new peak while at least 0.25 ATR below it. Released risk is replaced only after price rises 0.5 ATR from the trim, the lifecycle returns to Markup, and strong Leading status is restored. Every re-entry resets the cycle, so another add requires another independent pullback. Divergence may reduce risk but cannot replace it before recovery; Climax and Fade also cannot add. Standalone strict-forward locks advance to `niuone-strict-forward-v21` and must not reuse an earlier protocol cohort.

v22 fixes action/stage mismatches for multi-concept stocks. Each NiuOne action selects a lifecycle-compatible concept membership, confirmed branches are no longer excluded merely because they fall outside the two display mainlines, and a top-20% strong core name may continue as Leading after its confirmed theme becomes `diverging`. Divergence no longer repeats the contradictory 60-point same-day theme-strength gate. Portfolio capacity, price patterns, and structural risk controls remain unchanged. The strict-forward lock advances to `niuone-strict-forward-v22` with a new default cohort on `2026-08-04`; archive the old lock and report before deployment and do not pool v21 and v22 fills.

v23 adds a conditional Markup Momentum Probe for the number-one leader of a cross-day-persistent `emerging` theme already in Markup. It requires stock strength of at least 90, a score of at least 8.0, a non-defensive market, and a next-open gap no greater than 3%. The route permits 3.2 ATR of price extension and an 18%/3 ATR structural stop, but fixes the initial absolute position cap at 3% and lets effective-loss-distance sizing reduce it further. Ordinary Launch, Probe, and Leading rules are unchanged. Standalone strict-forward locks advance to `niuone-strict-forward-v23` and must not pool v22 and v23 fills.

v24 splits the Markup Momentum Probe into two geometries. An ordinary entry requires score at least 8.1, theme score at least 70, and no more than 1 ATR of EMA20 extension. An exceptional acceleration may use 2.5–3.2 ATR only when daily gain is at least 9.5% and volume ratio is no greater than 1.2. The qualified initial cap is 4%, still reduced by effective-loss-distance and portfolio risk budgets. Standalone strict-forward locks advance to `niuone-strict-forward-v24` and must not pool v23 and v24 fills.

Administrator backtest v25 fixes NiuOne to Aggressive parameters and removes the Balanced/Aggressive selector. The server normalizes any profile submitted by a stale client to `aggressive` and ignores persisted Balanced results. This changes only the backtest protocol to `niuone-backtest-v25`; the production strict-forward protocol remains v24.

v25 conditionally follows the remainder after a completed Climax reduction while the stock is still strong, the theme score is at least 55, and the theme is neither fading nor inactive. Relative leader-rank loss then requires three consecutive sessions instead of two, and the trail widens from 2 ATR to 3 ATR. Any failed health condition restores the original two-session/2 ATR behavior; structural and break-even stops, mainline weakness, Fade, and the market hard stop remain unchanged. Standalone strict-forward locks advance to `niuone-strict-forward-v25`, while administrator backtests advance to `niuone-backtest-v26`; older evidence must not be reused.

v26 permits NiuOne entries in a defensive regime at the minimum-risk tier. Mature-path per-trade/open/theme risk limits are 0.30%/0.90%/0.60%, with 20% total exposure and 12% theme exposure; Probe tightens these to 0.15% per trade, 0.30% per theme, and 5% theme exposure, and takes 50% off at 0.75R. Other eligibility and execution gates are unchanged, while the compound hard stop still blocks new entries. Standalone strict-forward locks advance to `niuone-strict-forward-v26`, administrator backtests advance to `niuone-backtest-v27`, and older evidence must not be reused.

v27 stores Eastmoney's factual `f100` industry separately from the action-selected `f103` NiuOne theme. Multi-concept attribution combines 75% current co-movement evidence with a 25% prior accumulated from preceding snapshots, and each stock's concept weights sum to one. The first fill freezes the entry theme; the active theme changes only after another lifecycle-valid theme leads by at least 10 points for two consecutive trading days. Theme risk capacity follows the action/active theme, and Dashboard displays theme and industry separately. Standalone strict-forward locks advance to `niuone-strict-forward-v27`, cluster by entry date × entry theme, and require complete theme-attribution evidence; administrator backtests advance to `niuone-backtest-v28`. Archive the old lock and report before deployment and do not reuse old results.

v29 treats Eastmoney `f103` as candidate labels rather than the final traded narrative. It attributes each stock only from leave-one-out peer resonance, cohort direction, and ranks; theme recognition performs no news search, and saved news cannot alter candidates, attribution, or theme totals. Independent mainline scans skip news precheck entirely, while ordinary strategy scans may still use it only as a pre-entry candidate risk check. The model preserves residual unattributed mass and recomputes theme strong stocks, breadth, amount, and leaders with those weights. Intraday breadth is shrunk toward market breadth by effective sample size, and Dashboard collapses label clones driven by the same core cohort. Theme context advances to schema v10, so older snapshots cannot provide cross-day confirmation. Standalone strict-forward locks advance to `niuone-strict-forward-v29` and administrator backtests to `niuone-backtest-v30`; archive prior locks, reports, and backtest results before deployment.

v30 adds 20-session market-neutral return-wave attribution. It compares the stock with the leave-one-out median excess-return path of each `f103` cohort and shrinks the result by relative candidate rank. No NiuOne scan mode performs news precheck or a model call. Context/cache schemas are v11/v9 and standalone strict-forward/backtest protocols are `niuone-strict-forward-v30`/`niuone-backtest-v31`.

v31 fixes repeated dilution in multi-concept leadership. The 15% weight floor remains for ordinary weak branches, while the stock's highest-scoring theme gets one low-share exception when its attribution score is at least 60. Qualified structural and intraday leaders then rank by raw strength and same-day return respectively, with attribution score used only as a tie-breaker. Weighted breadth, amount, concentration, and every trading-risk gate remain unchanged. Context/cache schemas are v12/v10 and standalone strict-forward/backtest protocols are `niuone-strict-forward-v31`/`niuone-backtest-v32`; archive old protocol locks, reports, and backtests before deployment.

v32 adds a stock capital-activity gate to mature mainline entries: Leading, Resumption, and Launch require at least the 60th market-wide amount percentile and the 50th percentile inside the action-selected theme, while missing amount evidence fails closed. Probe remains available for early discovery with an explicit activity warning. Amount weight in stock strength rises to 15% and 5-day strength falls to 20%; size and turnover rate are not direct rewards. Context/dedicated-cache schemas are v13/v11, candidate-evidence schema is v2, and standalone strict-forward/backtest protocols are `niuone-strict-forward-v32`/`niuone-backtest-v33`; archive old locks, reports, and backtests before deployment.

v33 localizes internal enums only in user-facing strategy prose. Prompts use Chinese lifecycle, role, and mainline-mode labels; persistence and historical rendering convert standalone lowercase enums only in Chinese strategy context, including nested dropped-buy reasons. Proper names, English technical prose, errors, acronyms, and identifiers remain unchanged, and all strategy gates and risk controls are identical. The display mapping joins the protocol fingerprint, standalone strict-forward advances to `niuone-strict-forward-v33`, and the default new cohort begins on `2026-08-13`; archive the v32 lock and report before deployment.

Administrator backtest v34 includes the terminal liquidation session after the signal window in the equity curve and risk metrics, and improves current-session timing plus ETA during long replays. NiuOne advances to `niuone-backtest-v34` and frozen prompt strategies advance to `prompt-backtest-v2`; older results become stale and must be rerun after a standalone upgrade. Strategy rules, fill precision, and capital calculations are unchanged.

v34 removes NiuOne morning/afternoon, per-decision, and per-day opening-count limits while fixing the book at five holdings. At full capacity, the system compares an auditable candidate priority with the lowest-priority NiuOne holding and executes SELL-before-BUY only when the candidate is strictly higher and every old lot is T+1 sellable; risk and theme budgets are unchanged. Strict-forward/admin-backtest advance to `niuone-strict-forward-v34`/`niuone-backtest-v35`, with a new default cohort on `2026-08-19`; archive old locks, reports, and backtests before deployment.

v35 adds same-name, same-strategy score-ladder scaling. Every filled BUY advances a holding-period score high-water mark; another signal may add only when its score sets a strict new high, while ties, declines, and missing scores fail closed. Probe cannot add on its entry day or average down, and mature paths retain the Markup, strong-leader, and 2%–12% profit-window gates. Stage upgrades, post-trim wave re-entry, and all portfolio controls remain independent. Strict-forward/admin-backtest advance to `niuone-strict-forward-v35`/`niuone-backtest-v36`; the not-yet-started default cohort remains `2026-08-19`.

v36 decouples the current-market summary/evaluation from NiuOne opening counts. Its dynamic holding count, per-decision BUY count, and pause fields no longer limit NiuOne; model prompts, over-limit refinement, and execution enforce only the five-name ceiling and full-book replacement priority. Per-trade, portfolio, and theme risk budgets, total exposure, cash, the candidate's own compound market hard stop, and the daily-loss budget remain effective. Standalone strict-forward advances to `niuone-strict-forward-v36`; admin backtest remains `niuone-backtest-v36` because it already uses the same capacity semantics, and the default cohort remains `2026-08-19`.

v37 assigns zero decision weight to failed news prechecks. Failed, timed-out, unchecked, pending, or unavailable records stay available for diagnostics but are omitted from model news evidence and mapped to neutral in candidate summaries. They cannot reduce score, priority, or sizing, or justify no entry, HOLD, or SELL; completed positive, negative, and neutral results continue to participate. Standalone strict-forward advances to `niuone-strict-forward-v37`; admin backtest remains `niuone-backtest-v36`, and the default cohort remains `2026-08-19`.

v38 removes NiuOne's fixed two-Probe daily candidate cap and fixed two-position same-sector/same-theme cap. The five-position ceiling plus single-name, theme-risk, theme-exposure, portfolio-risk, total-exposure, cash, limit-up, and T+1 controls remain; Sector Tide is unchanged. Standalone strict-forward/admin-backtest advance to `niuone-strict-forward-v38`/`niuone-backtest-v37`, with a new default cohort on `2026-08-21`; archive old locks, reports, and backtests before deployment.

v39 scopes the shared market prompt's pre-lunch position count and reserved afternoon slots explicitly to non-NiuOne strategies. NiuOne BUY/HOLD decisions compare the book only with the five-position ceiling. Standalone strict-forward advances to `niuone-strict-forward-v39`, the administrator backtest remains `niuone-backtest-v37`, and the new default cohort starts on `2026-08-24`; archive the v38 lock and report before deployment.

v40 raises the Probe absolute cap to 10% and raises both rotation Probe per-trade NAV risk and theme risk to 1%. The local lifecycle rule deterministically emits 10%/20% staged scale-ins for holdings with cross-session Markup persistence, strong leadership, and 2%–12% unrealized profit. Explicit SELL remains exit-first and all existing risk, exposure, cash, and T+1 boundaries remain active. Standalone strict-forward/admin-backtest advance to `niuone-strict-forward-v40`/`niuone-backtest-v38`; the default cohort remains `2026-08-24`. Archive the v39 lock, report, and older backtests before deployment.

v41 adds a holding fast cycle that is disabled by default. It narrows only the incremental scoring universe and keeps the complete cycle's scoring, unified market summary, model policy, exit-first ordering, and execution controls. A fast-cycle BUY is add-only for a position that still exists at execution and cannot discover, open, or reopen a symbol. The durable origin is `holding_fast`, and the switch plus 60–900-second interval enter the protocol fingerprint. Standalone strict-forward advances to `niuone-strict-forward-v41`, the administrator daily-bar backtest remains `niuone-backtest-v38`, and the new default cohort starts on `2026-08-27`; archive the v40 lock and report before deployment.

v42 aligns NiuOne's concurrent-holding ceiling with `DASHBOARD_MAX_OPEN_POSITIONS`. Model prompts, free-slot calculation, full-book replacement, final execution checks, administrator backtests, and strict-forward share the configured value. Pre-lunch and market-summary dynamic counts still do not limit NiuOne, and all other risk budgets remain unchanged. Standalone strict-forward/admin-backtest advance to `niuone-strict-forward-v42`/`niuone-backtest-v39`, while the default cohort remains `2026-08-27`; archive the v41 lock, report, and older backtests before deployment.

v43 routes non-structural no-progress, sell-score, theme/sector weakening, and ordinary profit-giveback exits through one staged policy: reduce 50% first, wait for a distinct-session confirmation before closing the runner, and let a 4–5 sell-fly score veto the first session. Structural stops, inactive themes, and market hard stops remain immediate. Full-book replacement now requires a three-point priority margin. SQLite durably refreshes each SELL at 1/3/5/10 sessions with MFE, MAE, close/benchmark excess, and replacement-relative returns; sell-fly and avoided-loss labels wait for a complete five-session window. A fully closed staged exit enters a five-session shadow watch and can reopen only from a full scan after reclaiming its exit high/BBI with volume and the original thesis intact. Standalone strict-forward/admin-backtest advance to `niuone-strict-forward-v43`/`niuone-backtest-v40`, with a new cohort on `2026-08-28`; archive v42 locks, reports, and older backtests before deployment.

v44 adds opt-in bounded post-exit feedback. Once the configured complete-sample and cross-month gates are met, each new-sample batch can move at most one audited grid step. Only soft exits, replacement advantage, and post-exit re-entry confirmation are tunable; structural stops, T+1, account/portfolio/theme risk, and position caps remain frozen. Fills retain the version and parameters, SQLite activates versions atomically, and materially worse version-tagged outcomes roll back automatically. Standalone strict-forward advances to `niuone-strict-forward-v44`; backtest remains fixed-default `niuone-backtest-v40`, and the new cohort starts on `2026-08-31`.
v45 upgrades feedback to v2. Completed observations mature only forward, returns use actual fills, replacement evidence requires an executed BUY, and allowed or blocked re-entry decisions produce direct five-session shadow outcomes. The latest 120 valid observations are clustered by security and day, capital-weighted, and evaluated with 90% confidence intervals and minimum economic effects. Hold evaluations no longer create policy versions, while account loads reconcile the active SQLite version after interrupted cross-file commits. Structural stops and all portfolio-risk limits remain frozen. Strict-forward advances to `niuone-strict-forward-v45`; the cohort still begins on `2026-08-31`.
v46 enables bounded five-session post-exit tuning by default. Standalone deployments without an explicit setting enter learning or evaluation on the next post-close review, while `DASHBOARD_EXIT_FEEDBACK_AUTO_TUNE_ENABLED=0` still opts out. Sample gates, bounded grids, automatic rollback, and frozen risk boundaries are unchanged. Strict-forward advances to `niuone-strict-forward-v46`; the cohort still begins on `2026-08-31`.
v47 makes feedback observable before the first post-close review. The Practice page always keeps the five-session review strip visible and derives enabled state from current runtime configuration instead of stale account JSON. Default-enabled deployments show that they are waiting for the first review; explicit opt-outs show disabled. Strict-forward advances to `niuone-strict-forward-v47`.
v48 fixes the holding fast cycle's use of compact mainline-cache stock rows. The cycle now combines the freshest minute-level theme/market state with scorer-complete stock profiles from a same-day full scan; cross-day, missing, or invalid profiles still fail closed, and scorer failures retain only non-sensitive type counts. Add eligibility, exit priority, and all portfolio controls are unchanged. Strict-forward advances to `niuone-strict-forward-v48`, with a new cohort starting on `2026-09-03`.

### One-Click Enablement

`--service` first performs the same directory initialization, virtual-environment creation, and dependency installation as a normal startup, then registers and immediately starts the native services for the current platform. Running it again updates the existing registrations, which is useful after code or configuration changes.

macOS / Linux:

```bash
./run.sh --service
```

Windows:

```cmd
run.bat --service
```

It can be combined with other arguments:

```bash
./run.sh --service --port 8877 --no-browser
```

```cmd
run.bat --service --port 8877 --no-browser
```

Both processes are registered.

### Updating a Source Deployment

The version check on the settings page and home page only reports whether Docker Hub has a higher strict SemVer release. It never pulls source, replaces an image, or restarts a service. Back up `.local-data/` before upgrading. If the checkout has no uncommitted conflicts that need to be preserved or resolved, run:

```bash
git pull --ff-only
./run.sh --service --no-browser
```

Running `--service` again updates and restarts both native services while preserving configuration, databases, and logs under `.local-data/`. When long-running services are already installed, a regular `./run.sh` (or `run.bat` on Windows) invocation also restarts the managed processes so a new frontend cannot be served by an old backend. For a foreground installation without long-running services, run:

```bash
git pull --ff-only
./run.sh --no-browser
```

The launcher installs Python dependencies when it creates the virtual environment or when the `requirements.txt` hash changes, and rebuilds Vue when frontend source, styles, or lock files change. `--skip-install` skips only the Python dependency installation check; it does not skip a missing or stale frontend build. For a container upgrade, pin a new `NIUONE_IMAGE` version tag and optionally set `NEWSNOW_IMAGE` to pin NewsNow before running `docker compose pull` and `docker compose up -d --no-build`; both persistent volumes are retained. See the [Deployment, Validation, and Rollback Manual](OPERATIONS_EN.md) for the full backup, validation, and rollback procedure.

### Status, Restart, and Uninstallation

macOS / Linux:

```bash
./scripts/manage-long-running.sh status
./scripts/manage-long-running.sh restart
./scripts/manage-long-running.sh uninstall
```

Windows PowerShell:

```powershell
powershell -File .\scripts\manage-long-running.ps1 -Action Status
powershell -File .\scripts\manage-long-running.ps1 -Action Restart
powershell -File .\scripts\manage-long-running.ps1 -Action Uninstall
```

Uninstallation removes only the services or scheduled tasks. It does not delete the configuration, databases, or logs in `.local-data/`.

### Platform Behavior

| Platform | Implementation | Automatic startup behavior | Service logs |
|---|---|---|---|
| macOS | `~/Library/LaunchAgents/ai.niuone.*.plist` | Starts after the current user signs in and restarts automatically after an unexpected exit | `.local-data/runtime/logs/ai.niuone.*.stdout.log` and `*.stderr.log` |
| Linux | `~/.config/systemd/user/niuone-*.service` | Starts through user-level systemd; the script attempts to enable linger | `journalctl --user -u niuone-dashboard.service` |
| Windows | `NiuOne *` scheduled tasks | Starts after the current user signs in and automatically retries after an unexpected exit | `.local-data\runtime\logs\windows-service-*.log` |

If Linux reports that linger cannot be enabled, run the following after obtaining the necessary authorization:

```bash
loginctl enable-linger "$USER"
```

Windows uses “At log on” startup by default to avoid placing the Windows login password in a command. For unattended hosts that must run after boot before anyone signs in, change the trigger to “At startup” in Task Scheduler, select “Run whether user is logged on or not,” and let Windows securely store the credentials for the account that runs the task. Use a dedicated standard user account; do not change it to `SYSTEM`.

## Troubleshooting

On macOS / Linux, check whether the page is accessible:

```bash
curl -s -o /dev/null -w 'HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8787/
```

Check the logs:

```bash
ls -lh .local-data/runtime/logs/
tail -n 100 .local-data/runtime/logs/*.log
```

Confirm that real data is still ignored:

```bash
git status --ignored --short
```

On Windows PowerShell, check the page and scheduled tasks:

```powershell
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8787/).StatusCode
Get-ScheduledTask -TaskName "NiuOne*" | Get-ScheduledTaskInfo
```

Check the latest logs:

```powershell
Get-ChildItem .\.local-data\runtime\logs\*.log |
  ForEach-Object {
    "=== $($_.Name) ==="
    Get-Content $_.FullName -Tail 100
  }
```

If a scheduled task shows `Ready` but the page is inaccessible, first run `.\run.bat --no-browser --skip-install` manually to inspect console errors, then check port usage, the Python virtual environment, and `.local-data\dashboard.env`.
