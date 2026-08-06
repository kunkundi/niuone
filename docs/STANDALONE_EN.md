# Standalone Operation Guide

[简体中文](STANDALONE.md) | English

This document explains how to run NiuOne locally as a standalone application. By default, runtime data is stored in `.local-data/` inside the project directory, keeping source code separate from real data.

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

## Large Language Model Configuration

NiuOne requires access to a large language model to run the complete workflow. Without model configuration, the local pages and some static views are available, but event collection, information retrieval, X watchlist monitoring, the daily U.S. institutional ratings report, and trading decisions cannot operate fully.

Recommended configuration:

| Scenario | Recommended model | Main configuration items |
|---|---|---|
| X watchlist monitoring | Grok | `X_WATCHLIST_ENABLED`, `DASHBOARD_GROK_BASE_URL`, `DASHBOARD_GROK_API_KEY`, `DASHBOARD_GROK_MODEL`, `DASHBOARD_GROK_API_MODE`, `X_WATCHLIST_MAX_TOKENS` |
| Daily U.S. institutional ratings report | A model with real-time search; reuses Grok when left empty | `US_RATING_MODEL`, `US_RATING_BASE_URL`, `US_RATING_API_KEY`, `US_RATING_MAX_TOKENS` |
| Enhanced A-share market summary | A model compatible with `/chat/completions` | `A_SHARE_MODEL_SUMMARY_BASE_URL`, `A_SHARE_MODEL_SUMMARY_API_KEY`, `A_SHARE_MODEL_SUMMARY_MODEL`, `A_SHARE_MODEL_SUMMARY_MAX_TOKENS`; reuses `DASHBOARD_GROK_*` when left empty |
| News precheck for A-share candidates and Dragon-Tiger limit-up/consecutive-list signals | A model with real-time search capabilities | `DASHBOARD_NEWS_BASE_URL`, `DASHBOARD_NEWS_API_KEY`, `DASHBOARD_NEWS_MODEL`, `DASHBOARD_NEWS_API_MODE`, `DASHBOARD_NEWS_MAX_TOKENS`, `DASHBOARD_NEWS_CONCURRENCY` |
| iWencai dragon-tiger research data | Tonghuashun iWencai OpenAPI | `IWENCAI_ENABLED`, `IWENCAI_BASE_URL`, `IWENCAI_API_KEY`, `IWENCAI_TIMEOUT_SECONDS`, `IWENCAI_MAX_RETRIES`, `IWENCAI_MAX_CONCURRENCY`, `IWENCAI_CACHE_TTL_SECONDS`, `IWENCAI_DRAGON_TIGER_CRON` |
| Trading decisions after stock selection | DeepSeek recommended; other compatible models may be used | `DASHBOARD_DECISION_BASE_URL`, `DASHBOARD_DECISION_API_KEY`, `DASHBOARD_DECISION_MODEL` |
| Trading-decision intelligence bundle | Aggregated locally; no additional model required | `DASHBOARD_DECISION_INTELLIGENCE_ENABLED`, `DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS`, `DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` |

After startup, click the settings button on the page to manage models, task schedules, and monitored X/Twitter authors. Every section that requires a model and API key includes **Test Model Connection**; it tests the current form values without saving them and reuses the saved secret when the API key input is empty. Enter X/Twitter handles without `@`.
Tweet monitoring and U.S. ratings settings are controlled by the “Enable NiuNiu U.S. Stocks” master switch. When disabled, those settings are collapsed and hidden, and the background X monitoring and U.S. ratings scheduled tasks are skipped. Disabling only **Enable X Watchlist Monitoring** makes both the daemon and direct entry point skip X queries while the U.S. ratings report continues on schedule.
`DASHBOARD_GROK_API_MODE` defaults to `auto`: Grok 4.5 uses the Responses API with search tools, while other models use Chat Completions; set `responses` or `chat` to force a mode. `X_WATCHLIST_REQUEST_TIMEOUT_SECONDS` defaults to `45` seconds.
`DASHBOARD_NEWS_API_MODE` defaults to `auto`: Grok 4.5 and GPT-5 search models use the Responses API with `web_search`, and a Grok Responses news model also receives `x_search`. Other models use `web_search` for publicly indexed Xueqiu/X pages and never fall back to `DASHBOARD_GROK_*`.
`*_CONTEXT_LENGTH` represents only the model context window and defaults to `128000`; `*_MAX_TOKENS` is the desired maximum output length and is mapped to a compatible Chat or Responses parameter. Both JSON and SSE responses are supported.
The news pre-check examines at most five candidate stocks concurrently by default. If the upstream service imposes rate limits, reduce `DASHBOARD_NEWS_CONCURRENCY` to `2` or `1`.
The iWencai source is disabled by default. **iWencai Data Source** includes **Test iWencai Connection**, which sends one lightweight read-only query using the current address and key without saving settings or modifying dragon-tiger snapshots. Enable it and save the API key, then open `/dragon-tiger` to query dated top-five buy/sell institution, brokerage, and explicitly tagged hot-money/quant seats and amounts live, or query a selected date through `/api/iwencai/dragon-tiger`. When iWencai returns limit-up reasons, the detail card presents the reason and category separately from the leaderboard reason. Current-day data and the most recent retained snapshot require no password until the next successful query; earlier dates require the administrator password. An empty current-day live query continues to display the most recent successful snapshot. Cron refreshes the latest snapshot at 18:00 China time on A-share trading days by default and can be changed with `IWENCAI_DRAGON_TIGER_CRON`. News prechecks use the corresponding scheduled query time as their start rather than the upstream response's `generated_at`, and persist checked and pending stocks matching either a limit-up streak (`limit_up_streak >= 2`) or consecutive listing (`consecutive_listed = true` and `consecutive_list_days >= 2`) for that trading date. Scheduler startup catches up the latest due trading-date snapshot, and each new pull first backfills the retained snapshot, allowing Friday misses to complete after a weekend restart or before the next trading day. After all are checked, no same-day model calls occur. The most recent non-empty successful query is retained until and atomically replaced by the next successful query; empty or failed responses preserve the previous valid data. The next successful refresh also removes dated archives created by earlier versions, and a same-day seat-detail failure does not overwrite valid rows in the current snapshot. The key remains only in the private local `dashboard.env` and is never echoed by the page.

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
| `DASHBOARD_ADMIN_PASSWORD` | Empty | Administrator password for the settings page; when empty, the bootstrap administrator key in `$DASHBOARD_HOME/dashboard_admin_token.txt` is used |
| `PYTHON_BIN` | `.local-data/.venv/bin/python` or the Windows venv Python | Python executable |
| `DASHBOARD_CONFIG` | `$DASHBOARD_HOME/config.yaml` | YAML configuration for model providers and models |
| `DASHBOARD_PUSH_HISTORY_DB` | `$DASHBOARD_HOME/push_history.db` | Message history database |
| `DASHBOARD_PORTFOLIO_STATE` | `$DASHBOARD_HOME/cron/output/niuniu_practice_portfolio.json` | Simulated-account state |
| `DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON` | `5 9 * * 1-5` | Verify the strict-forward protocol immediately at Scheduler startup and again at 09:05 Monday through Friday |
| `DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON` | `15 15 * * 1-5` | Refresh marks without trading and persist account equity after each actual A-share session |
| `DASHBOARD_NIUONE_FORWARD_CRON` | `20 15 * * 1-5` | Recompute the NiuOne strict-forward report from the durable fill ledger after each Monday-through-Friday session; applies on the next Cron cycle |
| `DASHBOARD_NIUONE_FORWARD_COHORT_START` | `2026-08-04` | Strict-forward cohort start; archive the old protocol lock and restart from a new trading day after a rule change |
| `DASHBOARD_ACTIVE_STRATEGY` | `niuone` | Active independent strategy; changes apply to the next scan without a restart |
| `DASHBOARD_PRACTICE_SCHEDULE_TIMES` | `09:25,10:00,10:30,11:00,11:20,13:00,13:30,14:00,14:30,14:50` | Shared schedule for market summaries, screening, and simulated decisions |
| `DASHBOARD_KLINE_BOOTSTRAP_ENABLED` | `1` | Prepare full-market daily K lines immediately after a first deployment or cache expiry; requires a restart |
| `DASHBOARD_KLINE_READINESS_MIN_COVERAGE_PERCENT` | `90` | Valid-date daily-K-line coverage required to admit a Practice scan, from 90 through 100; requires a restart |
| `DASHBOARD_TENCENT_QUOTE_STAGE_TIMEOUT_SECONDS` | `90` | Aggregate budget for the full-market live-quote stage, from 15 through 300 seconds; requires a restart |
| `DASHBOARD_MANUAL_DATA_INITIALIZATION_TIMEOUT_SECONDS` | `660` | Maximum seconds a manual task waits for daily-K-line initialization; requires a restart |
| `X_WATCHLIST_ACCOUNTS` | Empty | Comma-separated list of monitored tweet authors |
| `DASHBOARD_DECISION_INTELLIGENCE_ENABLED` | `1` | Whether to enable the global intelligence bundle for trading decisions |
| `DASHBOARD_TRADE_DISCIPLINE_TEXT` | Empty | Trading-discipline text for the trading-decision prompt; the built-in default discipline is used when empty |
| `DASHBOARD_MAX_TOTAL_POSITION_PCT` | `80` | Global total-exposure cap; `zettaranc` and `sector_tide` enforce the stricter of the global limit and the strategy-suite hard cap, while other suites mainly use it as model guidance |
| `DASHBOARD_MIN_CASH_RESERVE_PCT` | `20` | Global cash buffer; `zettaranc` and `sector_tide` also enforce it at execution time, while other suites mainly use it as model guidance |
| `DASHBOARD_NIUONE_MAINLINE_MINUTE_REFRESH_ENABLED` | `1` | Reuse the full-market quote sample to refresh Theme Strength; requires a restart |
| `DASHBOARD_MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS` | `30` | Shared full-market interval for Theme Strength and Market Sentiment, from `30` through `600` seconds; requires a restart |
| `DASHBOARD_AUTO_VERSION_CHECK_ENABLED` | `1` | Check Docker Hub for a newer release on page load; applies at runtime and never installs an update automatically |

After settings are saved, configurations that support hot application are used immediately for subsequent requests. Restart the local service for configurations that require a restart.

## Independent Processes and Long-Term Operation

A complete background deployment generally consists of three independent processes:

| Process | macOS / Linux entry point | Windows entry point | Required? |
|---|---|---|---|
| Dashboard | `run-dashboard.sh` | `run.bat --no-browser --skip-install` | Yes |
| Scheduled-task scheduler | `run-niuone-cron-scheduler.sh` | `.local-data\.venv\Scripts\python.exe app\entrypoints\niuone_cron_scheduler.py` | Required for automatic summaries, database writes, or simulated-position automatic-exit checks |
| Watch-source daemon | `run-x-watchlist-daemon.sh` | `.local-data\.venv\Scripts\python.exe app\entrypoints\x_watchlist_daemon.py` | Required when the X watchlist is enabled |

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

All three processes are registered. After the “NiuNiu U.S. Stocks” feature is disabled, the X watch-source daemon skips collection and remains in a low-frequency sleep state, so it does not need to be uninstalled separately.

### Updating a Source Deployment

The version check on the settings page and home page only reports whether Docker Hub has a higher strict SemVer release. It never pulls source, replaces an image, or restarts a service. Back up `.local-data/` before upgrading. If the checkout has no uncommitted conflicts that need to be preserved or resolved, run:

```bash
git pull --ff-only
./run.sh --service --no-browser
```

Running `--service` again updates and restarts all three native services while preserving configuration, databases, and logs under `.local-data/`. When long-running services are already installed, a regular `./run.sh` (or `run.bat` on Windows) invocation also restarts the managed processes so a new frontend cannot be served by an old backend. For a foreground installation without long-running services, run:

```bash
git pull --ff-only
./run.sh --no-browser
```

The launcher installs Python dependencies when it creates the virtual environment or when the `requirements.txt` hash changes, and rebuilds Vue when frontend source, styles, or lock files change. `--skip-install` skips only the Python dependency installation check; it does not skip a missing or stale frontend build. For a container upgrade, pin a new `NIUONE_IMAGE` version tag before running `docker compose pull` and `docker compose up -d --no-build`. See the [Deployment, Validation, and Rollback Manual](OPERATIONS_EN.md) for the full backup, validation, and rollback procedure.

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
