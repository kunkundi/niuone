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
| X watchlist monitoring and daily U.S. institutional ratings report | Grok | `DASHBOARD_GROK_BASE_URL`, `DASHBOARD_GROK_API_KEY`, `DASHBOARD_GROK_MODEL`, `DASHBOARD_GROK_API_MODE`, `X_WATCHLIST_MAX_TOKENS`, `US_RATING_MAX_TOKENS` |
| Enhanced A-share market summary | A model compatible with `/chat/completions` | `A_SHARE_MODEL_SUMMARY_BASE_URL`, `A_SHARE_MODEL_SUMMARY_API_KEY`, `A_SHARE_MODEL_SUMMARY_MODEL`, `A_SHARE_MODEL_SUMMARY_MAX_TOKENS`; reuses `DASHBOARD_GROK_*` when left empty |
| News pre-check for A-share candidates | A model with real-time search capabilities | `DASHBOARD_NEWS_BASE_URL`, `DASHBOARD_NEWS_API_KEY`, `DASHBOARD_NEWS_MODEL`, `DASHBOARD_NEWS_API_MODE`, `DASHBOARD_NEWS_MAX_TOKENS`, `DASHBOARD_NEWS_CONCURRENCY` |
| iWencai dragon-tiger research data | Tonghuashun iWencai OpenAPI | `IWENCAI_ENABLED`, `IWENCAI_BASE_URL`, `IWENCAI_API_KEY`, `IWENCAI_TIMEOUT_SECONDS`, `IWENCAI_MAX_RETRIES`, `IWENCAI_MAX_CONCURRENCY`, `IWENCAI_CACHE_TTL_SECONDS`, `IWENCAI_DRAGON_TIGER_CRON` |
| Trading decisions after stock selection | DeepSeek recommended; other compatible models may be used | `DASHBOARD_DECISION_BASE_URL`, `DASHBOARD_DECISION_API_KEY`, `DASHBOARD_DECISION_MODEL` |
| Trading-decision intelligence bundle | Aggregated locally; no additional model required | `DASHBOARD_DECISION_INTELLIGENCE_ENABLED`, `DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS`, `DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` |

After startup, click the settings button on the page to manage models, task schedules, and monitored X/Twitter authors. Every section that requires a model and API key includes **Test Model Connection**; it tests the current form values without saving them and reuses the saved secret when the API key input is empty. Enter X/Twitter handles without `@`.
Tweet monitoring and U.S. ratings settings are controlled by the “Enable NiuNiu U.S. Stocks” switch. When disabled, those settings are collapsed and hidden, and the background X monitoring and U.S. ratings scheduled tasks are skipped.
`DASHBOARD_GROK_API_MODE` defaults to `auto`: Grok 4.5 uses the Responses API with search tools, while other models use Chat Completions; set `responses` or `chat` to force a mode. `X_WATCHLIST_REQUEST_TIMEOUT_SECONDS` defaults to `45` seconds.
`DASHBOARD_NEWS_API_MODE` defaults to `auto`: Grok 4.5 and GPT-5 search models use the Responses API with the `web_search` tool; set `responses` or `chat` to force a mode.
`*_CONTEXT_LENGTH` represents only the model context window and defaults to `128000`; `*_MAX_TOKENS` is the desired maximum output length and is mapped to a compatible Chat or Responses parameter. Both JSON and SSE responses are supported.
The news pre-check examines at most five candidate stocks concurrently by default. If the upstream service imposes rate limits, reduce `DASHBOARD_NEWS_CONCURRENCY` to `2` or `1`.
The iWencai source is disabled by default. **iWencai Data Source** includes **Test iWencai Connection**, which sends one lightweight read-only query using the current address and key without saving settings or modifying dragon-tiger snapshots. Enable it and save the API key, then open `/dragon-tiger` to browse dated top-five buy/sell institution, brokerage, and explicitly tagged hot-money/quant seats and amounts, or query dated research snapshots through `/api/iwencai/dragon-tiger`. Cron refreshes the latest snapshot at 18:00 China time on A-share trading days and archives it under `iwencai_dragon_tiger/YYYY-MM-DD.json`; empty or failed responses preserve the last valid data, and a same-day seat-detail failure does not overwrite archived seat rows. The key remains only in the private local `dashboard.env` and is never echoed by the page.

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
| `X_WATCHLIST_ACCOUNTS` | Empty | Comma-separated list of monitored tweet authors |
| `DASHBOARD_DECISION_INTELLIGENCE_ENABLED` | `1` | Whether to enable the global intelligence bundle for trading decisions |
| `DASHBOARD_TRADE_DISCIPLINE_TEXT` | Empty | Trading-discipline text for the trading-decision prompt; the built-in default discipline is used when empty |
| `DASHBOARD_MAX_TOTAL_POSITION_PCT` | `80` | Global total-exposure cap; `zettaranc` and `sector_tide` enforce the stricter of the global limit and the strategy-suite hard cap, while other suites mainly use it as model guidance |
| `DASHBOARD_MIN_CASH_RESERVE_PCT` | `20` | Global cash buffer; `zettaranc` and `sector_tide` also enforce it at execution time, while other suites mainly use it as model guidance |

After settings are saved, configurations that support hot application are used immediately for subsequent requests. Restart the local service for configurations that require a restart.

## Independent Processes and Long-Term Operation

A complete background deployment generally consists of three independent processes:

| Process | macOS / Linux entry point | Windows entry point | Required? |
|---|---|---|---|
| Dashboard | `run-dashboard.sh` | `run.bat --no-browser --skip-install` | Yes |
| Scheduled-task scheduler | `run-niuone-cron-scheduler.sh` | `.local-data\.venv\Scripts\python.exe app\niuone_cron_scheduler.py` | Required for automatic summaries, database writes, or simulated-position automatic-exit checks |
| Watch-source daemon | `run-x-watchlist-daemon.sh` | `.local-data\.venv\Scripts\python.exe app\x_watchlist_daemon.py` | Required when the X watchlist is enabled |

The live B1 stock-selection schedule runs inside the Dashboard process. The scheduled-task scheduler does not select stocks, but it does run the independent automatic-exit checks for simulated positions. Both processes must stay running for the full scheduled selection-decision-exit lifecycle.

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
| macOS | `~/Library/LaunchAgents/ai.niuone.*.plist` | Starts after the current user signs in and restarts automatically after an unexpected exit | `.local-data/runtime/logs/ai.niuone.*.log` |
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
