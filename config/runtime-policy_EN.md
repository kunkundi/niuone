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
| `.local-data/runtime/niuniu.db` | Simulated-trading page trade and account data |
| `.local-data/runtime/config.yaml` | Model provider, model, and model-key configuration |
| `.local-data/runtime/cron/state/` | Scheduled-task, X-monitoring, and catch-up-run state |
| `.local-data/runtime/cron/output/` | Practice-trading candidate-scan cache, simulated-account state, and other non-message runtime caches |
| `.local-data/runtime/logs/` | Service and task logs |
| `.local-data/backups/` | Deployment backups, which may contain older configuration |

Do not copy any of the content above into issues, pull requests, the README, documentation examples, or chat contexts. When troubleshooting, provide only sanitized error types, timestamps, and strictly necessary fields.

## Model Keys

Recommended usage:

| Purpose | Recommended model | Settings |
|---|---|---|
| X watchlist monitoring and the daily U.S. institutional-rating report | Grok | `DASHBOARD_GROK_BASE_URL`, `DASHBOARD_GROK_API_KEY`, `DASHBOARD_GROK_MODEL`, `DASHBOARD_GROK_API_MODE` |
| Enhanced A-share market summaries | A model compatible with `/chat/completions` | `A_SHARE_MODEL_SUMMARY_BASE_URL`, `A_SHARE_MODEL_SUMMARY_API_KEY`, `A_SHARE_MODEL_SUMMARY_MODEL`; reuse `DASHBOARD_GROK_*` when left empty |
| News prechecks for A-share candidates | A model with real-time search capability | `DASHBOARD_NEWS_BASE_URL`, `DASHBOARD_NEWS_API_KEY`, `DASHBOARD_NEWS_MODEL`, `DASHBOARD_NEWS_API_MODE` |
| Buy and sell decisions after candidate screening | DeepSeek recommended; other compatible models may be used | `DASHBOARD_DECISION_BASE_URL`, `DASHBOARD_DECISION_API_KEY`, `DASHBOARD_DECISION_MODEL` |
| Comprehensive decision reference | Local aggregation; no additional model required | `DASHBOARD_DECISION_INTELLIGENCE_ENABLED`, `DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS`, `DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` |

X watchlist monitoring and the daily U.S. institutional-rating report are controlled by the `DASHBOARD_US_FEATURES_ENABLED` master switch. When it is disabled, the settings page hides the related configuration, and the background X daemon and scheduled U.S. rating task skip execution.

The comprehensive decision reference reads local market-data caches, market-message history, and simulated-account state, then writes a compressed summary to the decision log. It introduces no additional model keys, but the log may contain candidate-news summaries and must still be reviewed under this runtime-data policy before any public troubleshooting disclosure.

Model keys may be stored only in `.local-data/dashboard.env`, `.local-data/runtime/config.yaml`, or controlled system environment variables. Before committing, confirm that no new `.env`, `*.key`, `*.token`, `*.secret`, database, or backup file has been added.

The iWencai data source uses `IWENCAI_API_KEY`, which is subject to the same restriction and may only be stored in `.local-data/dashboard.env` or a controlled system environment variable.
`IWENCAI_ENABLED` is disabled by default. iWencai data is a research snapshot and supplemental market source; incomplete or cached responses must never overwrite account, fill, or real trading records.
The dragon-tiger job refreshes at 18:00 China time on A-share trading days by default. Only a non-empty successful response may atomically replace the latest snapshot and write a dated archive; failures and empty responses must preserve the last valid data. If top-five buy/sell seat details fail independently, valid institution, brokerage, and other seat rows already archived for the same trading day are preserved instead of being replaced by missing data.

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
