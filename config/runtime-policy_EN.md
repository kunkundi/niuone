# Runtime Data and Sensitive Information Handling Policy

[简体中文](runtime-policy.md) | English

This document defines how NiuOne handles runtime data, model keys, and private local files. Its purpose is to allow real data to remain inside the project directory while ensuring that content uploaded to a public repository contains no user data or sensitive information.

## Directory Boundaries

Source directory:

```text
/path/to/NiuOne
```

Private runtime directory:

```text
.local-data/
├── dashboard.env
├── .venv/
├── runtime/
└── backups/
```

`.local-data/`, `dashboard.env`, databases, local credentials, logs, and backup files are all ignored by `.gitignore`.

## Content That Must Not Be Committed or Shared Externally

| Path | Description |
|---|---|
| `.local-data/dashboard.env` | Local environment variables, paths, and any model keys or administrator passwords |
| `.local-data/.venv/` | Local Python virtual environment |
| `.local-data/runtime/dashboard_admin_token.txt` | Bootstrap administrator key used when `DASHBOARD_ADMIN_PASSWORD` is not configured |
| `.local-data/runtime/dashboard_users.db` | Local users and authentication data |
| `.local-data/runtime/push_history.db` | Message history |
| `.local-data/runtime/niuniu.db` | Practice trades, account data, complete observed opportunity sets, five-stage holding paths/exit stages, and durable decision evidence |
| `.local-data/runtime/cron/output/niuone_forward_evaluation.json` | NiuOne strict-forward aggregates, five-stage opportunity/sizing funnel, holding paths/stage transitions/exit stages, rejection categories, trade-level and entry-date-by-industry cluster-robust win-rate intervals, daily portfolio return/drawdown, performance gate, coverage diagnostics, and shadow groups |
| `.local-data/runtime/cron/state/niuone_forward_protocol.json` | Frozen code/non-secret runtime-configuration fingerprint and code-free starting-account boundary for the NiuOne strict-forward cohort |
| `.local-data/runtime/cron/state/niuone_cron_scheduler.json` | Bounded Cron run keys and daily task outcomes used by strict-forward evaluation |
| `.local-data/runtime/cron/state/b1_schedule_state.json` | Bounded terminal scan/decision outcomes for configured Practice slots |
| `.local-data/runtime/market_data/tencent_daily_klines.sqlite3` | Full-market daily-K-line cache populated before the open and incrementally filled by intraday scans |
| `.local-data/runtime/backtesting/` | Server-side progress/results for each strategy's current backtest, short-lived subprocess exchange files, and compressed selection replay tapes addressed by protocol/data/classification content; this is not a general historical daily-K cache for other modules |
| `.local-data/runtime/config.yaml` | Model provider, model, and model-key configuration |
| `.local-data/runtime/cron/state/` | Scheduled-task, X-monitoring, and catch-up-run state |
| `.local-data/runtime/cron/output/` | Practice-trading candidate-scan cache, simulated-account state, and other non-message runtime caches |
| `.local-data/runtime/logs/` | Service and task logs |
| `.local-data/backups/` | Deployment backups, which may contain older configuration |

The Dashboard incremental API may return only content inside `.local-data/runtime/public-data/` that was generated through the field allow-lists in `public_projection.py`. Never configure its parent directory, databases, or `cron/output/` as a static-site root. CDN synchronisation must be limited precisely to `objects/`, `manifests/`, and `latest.json`, and sanitisation tests must be reviewed after every schema change.

Do not copy any of the content above into issues, pull requests, the README, documentation examples, or chat contexts. When troubleshooting, provide only sanitized error types, timestamps, and strictly necessary fields.

## Model Keys

Recommended usage:

| Purpose | Recommended model | Settings |
|---|---|---|
| X watchlist monitoring | Grok with `x_search` support | `X_WATCHLIST_ENABLED`, `DASHBOARD_GROK_BASE_URL`, `DASHBOARD_GROK_API_KEY`, `DASHBOARD_GROK_MODEL`, `DASHBOARD_GROK_API_MODE` |
| Daily U.S. institutional-rating report | A model with real-time web search; reuses Grok when left blank | `US_RATING_MODEL`, `US_RATING_BASE_URL`, `US_RATING_API_KEY`, `US_RATING_MAX_TOKENS` |
| Enhanced A-share market summaries | A model compatible with `/chat/completions` | `A_SHARE_MODEL_SUMMARY_BASE_URL`, `A_SHARE_MODEL_SUMMARY_API_KEY`, `A_SHARE_MODEL_SUMMARY_MODEL`; reuse `DASHBOARD_GROK_*` when left empty |
| News prechecks for A-share candidates and dragon-tiger limit-up-streak/consecutive-listing stocks | A model with real-time search capability | `DASHBOARD_NEWS_BASE_URL`, `DASHBOARD_NEWS_API_KEY`, `DASHBOARD_NEWS_MODEL`, `DASHBOARD_NEWS_API_MODE` |
| Buy and sell decisions after candidate screening | DeepSeek recommended; other compatible models may be used | `DASHBOARD_DECISION_BASE_URL`, `DASHBOARD_DECISION_API_KEY`, `DASHBOARD_DECISION_MODEL` |
| Comprehensive decision reference | Local aggregation; no additional model required | `DASHBOARD_DECISION_INTELLIGENCE_ENABLED`, `DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS`, `DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` |

X watchlist monitoring and the daily U.S. institutional-rating report are controlled by the `DASHBOARD_US_FEATURES_ENABLED` master switch. When it is disabled, the settings page hides the related configuration, and the background X daemon and scheduled U.S. rating task skip execution. `X_WATCHLIST_ENABLED` is an independent X-monitoring switch that defaults to enabled. Setting it explicitly to `0` makes both the X daemon and direct entry point return before any model request without affecting the U.S. rating task. When the switch is set explicitly in the process or container environment, it takes precedence over the same value in `dashboard.env`.

The comprehensive decision reference reads local market-data caches, market-message history, and simulated-account state, then writes a compressed summary to the decision log. It introduces no additional model keys, but the log may contain candidate-news summaries and must still be reviewed under this runtime-data policy before any public troubleshooting disclosure.

Model keys may be stored only in `.local-data/dashboard.env`, `.local-data/runtime/config.yaml`, or controlled system environment variables. Before committing, confirm that no new `.env`, `*.key`, `*.token`, `*.secret`, database, or backup file has been added.

The iWencai data source uses `IWENCAI_API_KEY`, which is subject to the same restriction and may only be stored in `.local-data/dashboard.env` or a controlled system environment variable.
`IWENCAI_ENABLED` is disabled by default. iWencai data is a research snapshot and supplemental market source; incomplete or cached responses must never overwrite account, fill, or real trading records.
The dragon-tiger job refreshes at 18:00 China time on A-share trading days by default. Only the most recent non-empty successful response is retained and atomically replaced by the next successful query; failures and empty responses preserve the last valid data. Dated archives created by earlier versions are removed after the next successful refresh. If top-five buy/sell seat details fail independently, valid institution, brokerage, and other seat rows in the current snapshot are preserved only when the query date is unchanged.
Consecutive listing may be confirmed only from successful rolling snapshots on adjacent A-share trading days; a missing intermediate snapshot resets the streak rather than guessing across a data gap. Each news-precheck batch uses the scheduled dragon-tiger query time configured by `IWENCAI_DRAGON_TIGER_CRON` as its start, never the upstream response's `generated_at`. After the dragon-tiger pull succeeds, unchecked stocks are queried when either `limit_up_streak >= 2` or both `consecutive_listed = true` and `consecutive_list_days >= 2`, with at most five concurrent requests. The precheck cross-checks company and exchange disclosures plus mainstream financial media, while public Xueqiu and X/Twitter content is classified separately as market sentiment and must never turn an unverified post into a company fact. When the configured news model supports Grok Responses it may use `x_search` directly; other models use `web_search` for publicly indexed Xueqiu/X pages and still never read or fall back to `DASHBOARD_GROK_*`. Scheduler startup catches up the latest due trading-date snapshot, and each new pull first backfills pending checks in the retained snapshot, allowing Friday data to be completed after a weekend restart or before the next trading-day pull. The snapshot records checked and pending stocks; after all are checked, later same-day pulls must not call the model again. A stored `unclassified_response` may be relabeled locally during backfill only when its saved summary has one unambiguous conclusion; the original fetch time is retained, no model call is made, and ambiguous records remain unchanged. Missing configuration, rate limits, timeouts, or parse failures must never block or clear the main dragon-tiger data. Snapshots and public APIs retain only the structured summary, tone, fetch time, check state, and non-sensitive error code—not model credentials, full post text, or complete upstream responses.
Current-day dragon-tiger data remains public, as does the most recent rolling snapshot until the next successful query replaces it. Earlier dates require a valid administrator session. An empty current-day live query must fall back to the most recent successful snapshot instead of replacing the page with an empty state before new data is published. No non-current-date response may use public or CDN caching, so the replaced date becomes protected immediately after a refresh.

## Local Copies and Testing

Do not experiment directly against the real `.local-data/runtime/` directory. Use a temporary runtime directory for testing:

```bash
DASHBOARD_HOME=/tmp/niuone-smoke DASHBOARD_PORT=8877 ./scripts/run_standalone.sh
```

Before committing, run:

```bash
./scripts/validate.sh
git status --ignored --short
```

`.local-data/` should appear as ignored and must not appear in staged files.

## Releases and Backups

The local deployment script backs up the current `app/`, environment file, and startup scripts to:

```text
.local-data/backups/
```

The backup directory is also private data and must not be committed or shared externally. For rollback, prefer restoring `app/` from a backup or use `git revert` for a non-destructive commit rollback.

## Responding to Suspected Exposure

If a model key, local credential, or database is accidentally published:

1. Immediately revoke or rotate the affected key or credential.
2. Remove the exposed content from code and documentation.
3. Review `git status --ignored --short` and recent commits.
4. If no administrator password is configured, rebuild `.local-data/runtime/dashboard_admin_token.txt` when necessary; rebuild related databases as needed.
5. For sensitive content already pushed to a remote service, follow that service's incident-response process to remove it from history.
